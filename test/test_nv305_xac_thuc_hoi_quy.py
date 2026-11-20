"""Hồi quy ngân sách và trạng thái 2FA NV305 trên mã xác thực thật."""

from copy import deepcopy
from collections import OrderedDict
from unittest.mock import AsyncMock, Mock

import pytest

from app import gioi_han, nguoi_dung, xac_thuc_2fa
from test_nv305_bao_mat import MK_ADMIN, MK_MOI, _dang_nhap, _qt, _tao_admin, dong_ho, moi_truong
from test_nv305_bao_mat_gioi_han import CAC_DUONG_2FA, NGUONG, phien_2fa


CAC_DUONG_NHAY_CAM = (*CAC_DUONG_2FA, "/api/toi/2fa/tat", "/api/toi/2fa/tao-lai-ma-khoi-phuc")
MA_SAI = "ma-gia-khong-hop-le"


@pytest.fixture(autouse=True)
def cach_ly_khoa_ten(monkeypatch):
    monkeypatch.setattr(nguoi_dung, "_THEO_DOI_SAI", OrderedDict())


def _than(ma=MA_SAI, duong=""):
    than = {"mat_khau_moi": MK_MOI, "ma": ma, "ma_2fa": ma}
    if duong in ("/api/toi/2fa/tat", "/api/toi/2fa/tao-lai-ma-khoi-phuc"):
        than["mat_khau"] = MK_ADMIN
    return than


@pytest.mark.parametrize("nguon", CAC_DUONG_NHAY_CAM)
def test_moi_duong_dung_chung_ngan_sach_2fa(phien_2fa, monkeypatch, nguon):
    client, _, _, cookies, csrf = phien_2fa
    verifier = Mock(wraps=xac_thuc_2fa.xac_thuc_ma)
    monkeypatch.setattr(xac_thuc_2fa, "xac_thuc_ma", verifier)
    for _ in range(NGUONG + 1):
        r = client.post(nguon, json=_than(duong=nguon), cookies=cookies, headers=csrf)
        assert r.status_code == 401, r.text
    for dich in CAC_DUONG_NHAY_CAM:
        r = client.post(dich, json=_than(duong=dich), cookies=cookies,
                        headers={**csrf, "X-Forwarded-For": "203.0.113.20"})
        assert r.status_code == 429, r.text
        assert r.json()["error"]["code"] == "rate_limit_exceeded"
        assert int(r.headers["Retry-After"]) > 0
    assert verifier.call_count == NGUONG + 1


@pytest.mark.parametrize("loai", ["tat_ca", "tai_khoan", "ip", "pham_vi_2fa"])
@pytest.mark.parametrize("da_khoa", [False, True], ids=["bo-dem", "dang-khoa"])
def test_bo_chan_thong_thuong_khong_xoa_ngan_sach_2fa(phien_2fa, monkeypatch, loai, da_khoa):
    client, admin, _, cookies, csrf = phien_2fa
    duong = "/api/toi/xac-nhan-lai"
    verifier = Mock(wraps=xac_thuc_2fa.xac_thuc_ma)
    monkeypatch.setattr(xac_thuc_2fa, "xac_thuc_ma", verifier)
    so_lan = NGUONG + 1 if da_khoa else NGUONG
    for _ in range(so_lan):
        assert client.post(duong, json=_than(), cookies=cookies, headers=csrf).status_code == 401
    # API thật: kể cả chuỗi phạm vi tài khoản gửi vào trường IP cũng không xoá được ngân sách.
    body = {"loai": "ip" if loai == "pham_vi_2fa" else loai,
            "ten": admin["ten_dang_nhap"],
            "ip": f"2fa:{admin['id']}" if loai == "pham_vi_2fa" else "192.0.2.1"}
    r = client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json=body, headers=_qt())
    assert r.status_code == 200, r.text
    if not da_khoa:
        assert client.post(duong, json=_than(), cookies=cookies, headers=csrf).status_code == 401
    r = client.post(duong, json=_than(), cookies=cookies,
                    headers={**csrf, "X-Forwarded-For": "203.0.113.21"})
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) > 0
    assert verifier.call_count == NGUONG + 1


@pytest.mark.parametrize("nguon_dang_nhap", [False, True])
def test_dang_nhap_chia_se_ngan_sach_voi_phien_web(phien_2fa, monkeypatch, nguon_dang_nhap):
    client, admin, _, cookies, csrf = phien_2fa
    verifier = Mock(wraps=xac_thuc_2fa.xac_thuc_ma)
    monkeypatch.setattr(xac_thuc_2fa, "xac_thuc_ma", verifier)

    def thu_dang_nhap():
        r = client.post("/api/dang-nhap", json={"ten_dang_nhap": admin["ten_dang_nhap"], "mat_khau": MK_ADMIN})
        assert r.status_code == 401, r.text
        return client.post("/api/dang-nhap/2fa", json={"ve": r.json()["error"]["ve"], "ma": MA_SAI})

    def thu_phien():
        return client.post("/api/toi/xac-nhan-lai", json=_than(), cookies=cookies, headers=csrf)

    nguon, dich = (thu_dang_nhap, thu_phien) if nguon_dang_nhap else (thu_phien, thu_dang_nhap)
    for _ in range(NGUONG + 1):
        assert nguon().status_code == 401
    # Bỏ chặn lớp đăng nhập thường để chỉ kiểm ngân sách 2FA còn nguyên.
    assert client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tat_ca"}, headers=_qt()).status_code == 200
    r = dich()
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) > 0
    assert verifier.call_count == NGUONG + 1


@pytest.mark.parametrize("duong", ["bat-dau", "tat", "tao-lai-ma-khoi-phuc"])
def test_khoa_ten_chan_truoc_verifier_mat_khau(phien_2fa, monkeypatch, duong):
    client, admin, _, cookies, csrf = phien_2fa
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "2")
    for _ in range(2):
        nguoi_dung.ghi_nhan_dang_nhap_sai(admin["ten_dang_nhap"], ip="192.0.2.1")
    verifier = AsyncMock(wraps=nguoi_dung.kiem_tra_mat_khau)
    monkeypatch.setattr(nguoi_dung, "kiem_tra_mat_khau", verifier)
    r = client.post(f"/api/toi/2fa/{duong}", json={**_than(), "mat_khau": MK_ADMIN}, cookies=cookies, headers=csrf)
    assert r.status_code == 401, r.text
    verifier.assert_not_awaited()


@pytest.mark.parametrize("duong", ["bat-dau", "xac-nhan"])
def test_dang_ky_khong_ghi_de_active_ke_ca_co_seed_cho(phien_2fa, dong_ho, monkeypatch, duong):
    client, admin, _, cookies, csrf = phien_2fa
    dl = xac_thuc_2fa._doc_kho_user(admin["id"])
    seed_cho = xac_thuc_2fa.tao_bi_mat_totp()
    dl["cho_xac_nhan"] = {"bi_mat": seed_cho, "tao_luc": 1700000000.0}
    xac_thuc_2fa._ghi_kho_user(admin["id"], dl)
    truoc = deepcopy(dl)
    verifier = Mock(wraps=xac_thuc_2fa.kiem_tra_totp)
    monkeypatch.setattr(xac_thuc_2fa, "kiem_tra_totp", verifier)
    r = client.post(f"/api/toi/2fa/{duong}", json={"mat_khau": MK_ADMIN, "ma": dong_ho.ma_moi(seed_cho)},
                    cookies=cookies, headers=csrf)
    assert r.status_code == (409 if duong == "bat-dau" else 401), r.text
    assert r.json()["error"]["code"] == ("totp_already_active" if duong == "bat-dau" else "invalid_totp")
    verifier.assert_not_called()
    assert xac_thuc_2fa._doc_kho_user(admin["id"]) == truoc


@pytest.mark.parametrize("sua", [
    {"bi_mat": ""}, {"bi_mat": None}, {"bi_mat": 123}, {"bi_mat": "!seed-gia!"},
    {"bi_mat": "   "}, {"moc_buoc_cuoi": "sai"}, {"ma_khoi_phuc_bam": [123]},
], ids=["rong", "null", "sai-kieu", "base32-hong", "khoang-trang", "moc-hong", "khoi-phuc-hong"])
def test_kho_active_sai_cau_truc_503_khong_lui_password(phien_2fa, monkeypatch, sua):
    client, admin, _, cookies, csrf = phien_2fa
    dl = xac_thuc_2fa._doc_kho_user(admin["id"])
    dl.update(sua)
    xac_thuc_2fa._ghi_kho_user(admin["id"], dl)
    verifier = AsyncMock(wraps=nguoi_dung.kiem_tra_mat_khau)
    monkeypatch.setattr(nguoi_dung, "kiem_tra_mat_khau", verifier)
    for duong in (*CAC_DUONG_2FA, "/api/toi/2fa"):
        if duong == "/api/toi/2fa":
            r = client.get(duong, cookies=cookies, headers=csrf)
        else:
            r = client.post(duong, json={"mat_khau": MK_ADMIN, "mat_khau_cu": MK_ADMIN,
                                        "mat_khau_moi": MK_MOI}, cookies=cookies, headers=csrf)
        assert r.status_code == 503, r.text
        assert r.json()["error"]["code"] == "totp_store_unreadable"
    verifier.assert_not_awaited()
    client.cookies.clear()
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": admin["ten_dang_nhap"], "mat_khau": MK_ADMIN})
    assert r.status_code == 503, r.text
    assert "w2a_phien" not in r.cookies


def test_dang_ky_cho_cung_chiu_ngan_sach_2fa(moi_truong, dong_ho, monkeypatch):
    client, _ = moi_truong
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", str(NGUONG))
    gioi_han.khoi_dong()
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    bi_mat, _ = xac_thuc_2fa.bat_dau_dang_ky(admin["id"], admin["ten_dang_nhap"])
    verifier = Mock(wraps=xac_thuc_2fa.kiem_tra_totp)
    monkeypatch.setattr(xac_thuc_2fa, "kiem_tra_totp", verifier)
    for _ in range(NGUONG + 1):
        r = client.post("/api/toi/2fa/xac-nhan", json={"ma": MA_SAI}, cookies=cookies, headers=csrf)
        assert r.status_code == 401, r.text
    r = client.post("/api/toi/2fa/xac-nhan", json={"ma": dong_ho.ma(bi_mat)}, cookies=cookies, headers=csrf)
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) > 0
    assert verifier.call_count == NGUONG + 1
    assert not xac_thuc_2fa.da_bat_2fa(admin["id"])


def test_khoi_dong_reset_ngan_sach_2fa_va_giu_cau_hinh(phien_2fa):
    client, _, _, cookies, csrf = phien_2fa
    for _ in range(NGUONG + 1):
        assert client.post("/api/toi/xac-nhan-lai", json=_than(), cookies=cookies, headers=csrf).status_code == 401
    assert client.post("/api/toi/xac-nhan-lai", json=_than(), cookies=cookies, headers=csrf).status_code == 429
    gioi_han.khoi_dong()
    assert client.post("/api/toi/xac-nhan-lai", json=_than(), cookies=cookies, headers=csrf).status_code == 401
