"""Hồi quy giới hạn xác minh 2FA trong phiên web NV305."""

from unittest.mock import Mock

import pytest

from app import gioi_han, xac_thuc_2fa
from test_nv305_bao_mat import (
    MK_ADMIN,
    MK_MOI,
    _bat_2fa_admin,
    _dang_nhap,
    _dang_nhap_2fa,
    _tao_admin,
    dong_ho,
    moi_truong,
)


CAC_DUONG_2FA = ("/api/toi/mat-khau", "/api/toi/xac-nhan-lai",
                 "/api/trinh-duyet/mat-khau-vnc/hien")
NGUONG = 2


@pytest.fixture
def phien_2fa(moi_truong, dong_ho, monkeypatch):
    client, _ = moi_truong
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", str(NGUONG))
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "192.0.2.0/24")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    gioi_han.khoi_dong()
    client._transport.client = ("192.0.2.1", 1234)
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat)
    return client, admin, bi_mat, cookies, csrf


@pytest.mark.parametrize("duong", CAC_DUONG_2FA)
def test_vuot_nguong_2fa_chan_truoc_xac_minh(phien_2fa, dong_ho, duong, monkeypatch):
    client, _, _, cookies, csrf = phien_2fa
    xac_minh = Mock(wraps=xac_thuc_2fa.xac_thuc_ma)
    monkeypatch.setattr(xac_thuc_2fa, "xac_thuc_ma", xac_minh)
    than = {"mat_khau_moi": MK_MOI, "ma_2fa": "ma-gia-khong-hop-le"}
    # Bộ đếm thật cho phép lần vượt ngưỡng, rồi chặn từ yêu cầu kế tiếp.
    for lan in range(NGUONG + 1):
        headers = {**csrf, "X-Forwarded-For": f"198.51.100.{lan + 1}, 203.0.113.10"}
        r = client.post(duong, json=than, cookies=cookies, headers=headers)
        assert r.status_code == 401, r.text
    assert xac_minh.call_count == NGUONG + 1

    # Cùng ngân sách cho cả ba thao tác, kể cả khi IP hiệu lực thay đổi.
    cac_dich = [(duong, "203.0.113.10"), (duong, "203.0.113.11")]
    cac_dich.extend((d, "203.0.113.11") for d in CAC_DUONG_2FA if d != duong)
    for dich, ip in cac_dich:
        r = client.post(dich, json=than, cookies=cookies,
                        headers={**csrf, "X-Forwarded-For": ip})
        assert r.status_code == 429, r.text
        assert r.json()["error"]["code"] == "rate_limit_exceeded"
        assert int(r.headers["Retry-After"]) > 0
    assert xac_minh.call_count == NGUONG + 1
    assert any(muc["ip"] == "203.0.113.10" for muc in gioi_han._bo_2fa().liet_ke_khoa())

    # Tài khoản khác cùng IP vẫn có thể xác minh mã hợp lệ.
    admin_khac = _tao_admin(client, ten="admin-bm-khac")
    bi_mat_khac = _bat_2fa_admin(admin_khac, dong_ho)
    cookies_khac, csrf_khac = _dang_nhap_2fa(
        client, admin_khac["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat_khac,
    )
    r = client.post(duong, json={**than, "ma_2fa": dong_ho.ma_moi(bi_mat_khac)},
                    cookies=cookies_khac, headers={**csrf_khac, "X-Forwarded-For": "203.0.113.10"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("duong", CAC_DUONG_2FA)
@pytest.mark.parametrize("loai_ma", ["totp", "khoi_phuc"])
def test_2fa_van_nhan_ma_hop_le(phien_2fa, dong_ho, duong, loai_ma):
    client, admin, bi_mat, cookies, csrf = phien_2fa
    so_ma_khoi_phuc = xac_thuc_2fa.so_ma_khoi_phuc_con_lai(admin["id"])
    ma = (dong_ho.ma_moi(bi_mat) if loai_ma == "totp"
          else xac_thuc_2fa.tao_lai_ma_khoi_phuc(admin["id"])[0])
    r = client.post(duong, json={"mat_khau_moi": MK_MOI, "ma_2fa": ma},
                    cookies=cookies, headers=csrf)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    if loai_ma == "khoi_phuc":
        assert xac_thuc_2fa.so_ma_khoi_phuc_con_lai(admin["id"]) == so_ma_khoi_phuc - 1


def test_2fa_tat_doi_mat_khau_van_xac_minh_mat_khau_cu(moi_truong):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    than = {"mat_khau_moi": MK_MOI}
    duong = "/api/toi/mat-khau"
    assert client.post(duong, json=than, cookies=cookies, headers=csrf).status_code == 400
    r = client.post(duong, json={**than, "mat_khau_cu": MK_MOI}, cookies=cookies, headers=csrf)
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "incorrect_password"
    r = client.post(duong, json={**than, "mat_khau_cu": MK_ADMIN}, cookies=cookies, headers=csrf)
    assert r.status_code == 200, r.text
    _dang_nhap(client, admin["ten_dang_nhap"], MK_MOI)
