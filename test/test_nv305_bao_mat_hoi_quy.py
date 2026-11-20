"""Hồi quy bảo mật lõi NV305; dùng môi trường và bằng chứng giả của bộ test gốc."""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app import nguoi_dung, xac_thuc_2fa
from test_nv305_bao_mat import (
    MK_ADMIN,
    MK_VNC_TEST,
    MODULE,
    ModBaoMatGia,
    _bat_2fa_admin,
    _dang_nhap,
    _dang_nhap_2fa,
    _hien,
    _qt,
    _stepup,
    _tao_admin,
    dong_ho,
    moi_truong,
)


MODULE_KHAC = "module-khac-nv305"
CAC_ROUTE = [
    pytest.param("PUT", "/mat-khau-vnc", {"mat_khau": MK_VNC_TEST},
                 "dat_mat_khau_vnc", id="set"),
    pytest.param("GET", "/mat-khau-vnc/trang-thai", {},
                 "trang_thai_mat_khau_vnc", id="password-status"),
    pytest.param("POST", "/mat-khau-vnc/hien", {"mat_khau": MK_ADMIN},
                 "hien_mat_khau_vnc", id="reveal"),
    pytest.param("POST", "/man-hinh/bat-tat", {"bat": True},
                 "bat_tat_novnc", id="toggle"),
    pytest.param("GET", "/man-hinh/trang-thai", {},
                 "trang_thai_novnc", id="screen-status"),
    pytest.param("POST", "/{module}/xem", {},
                 "trinh_duyet_xem_mo", id="view"),
]
CAC_ROUTE_THAY_DOI = [r for r in CAC_ROUTE if r.values[0] != "GET"]
CAC_ROUTE_TRA_LOI_MODULE = [r for r in CAC_ROUTE if r.id != "view"]


@pytest.fixture
def goi_module(moi_truong, monkeypatch):
    """Theo dõi biên module thật/đích tráo; không thay cơ chế xác thực của lõi."""
    client, mod = moi_truong
    mod_khac = ModBaoMatGia()
    client.app.state.so.dang_chay[MODULE_KHAC] = mod_khac
    cac_goi = {}
    for id_module, doi_tuong in ((MODULE, mod), (MODULE_KHAC, mod_khac)):
        for route in CAC_ROUTE:
            ten = route.values[3]
            if ten == "trinh_duyet_xem_mo":
                goi = AsyncMock(side_effect=AssertionError("Không được mở dịch vụ xem"))
            else:
                goi = AsyncMock(wraps=getattr(doi_tuong, ten))
            monkeypatch.setattr(doi_tuong, ten, goi, raising=False)
            cac_goi[id_module, ten] = goi
    return cac_goi


@pytest.mark.parametrize("method,path,body,ten", CAC_ROUTE)
def test_sau_route_khong_xac_thuc_tra_401(moi_truong, goi_module, method, path, body, ten):
    client, _ = moi_truong
    r = client.request(method, "/api/trinh-duyet" + path.format(module=MODULE), json=body)
    assert r.status_code == 401, r.text
    for goi in goi_module.values():
        goi.assert_not_called()


@pytest.mark.parametrize("danh_tinh", ["guest", "user"])
@pytest.mark.parametrize("target", [MODULE, MODULE_KHAC])
@pytest.mark.parametrize("method,path,body,ten", CAC_ROUTE)
def test_quyen_thap_khong_the_trao_module_hay_id(
    moi_truong, goi_module, danh_tinh, target, method, path, body, ten,
):
    client, _ = moi_truong
    admin = _tao_admin(client)
    if danh_tinh == "guest":
        tao = client.post("/api/khoa-khach", headers=_qt(),
                          json={"nhan": "khach-gia-nv305", "pham_vi": ["chat"]})
        assert tao.status_code == 200, tao.text
        cookies = {}
        headers = {"Authorization": "Bearer " + tao.json()["khoa"]["khoa"]}
    else:
        user, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="user-hoi-quy-nv305", mat_khau=MK_ADMIN,
        ))
        assert user["vai"] == "user"
        cookies, headers = _dang_nhap(client, user["ten_dang_nhap"], MK_ADMIN)

    # Bằng chứng đúng của admin và ID có thật vẫn không nâng quyền người gọi.
    than = {**body, "module": target, "module_dich": target,
            "id": admin["id"], "nguoi_dung_id": admin["id"],
            "ho_so": "0123456789abcdef", "vai": "admin"}
    r = client.request(method, "/api/trinh-duyet" + path.format(module=target),
                       json=than, params={"module": target, "id": admin["id"]},
                       cookies=cookies, headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "forbidden"
    for goi in goi_module.values():
        goi.assert_not_called()


@pytest.mark.parametrize("csrf_sai", [None, "x" * 43], ids=["missing", "wrong"])
@pytest.mark.parametrize("method,path,body,ten", CAC_ROUTE_THAY_DOI)
def test_session_thieu_hay_sai_csrf_khong_goi_module(
    moi_truong, goi_module, csrf_sai, method, path, body, ten,
):
    client, _ = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r_stepup = _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r_stepup.status_code == 200, r_stepup.text
    headers = {} if csrf_sai is None else {"X-CSRF-Token": csrf_sai}
    r = client.request(method, "/api/trinh-duyet" + path.format(module=MODULE),
                       json=body, cookies=cookies, headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "csrf_token_invalid"
    for goi in goi_module.values():
        goi.assert_not_called()


@pytest.mark.parametrize("bat_2fa", [False, True], ids=["password", "totp"])
def test_reveal_khong_tan_dung_cua_so_stepup(moi_truong, dong_ho, bat_2fa):
    client, mod = moi_truong
    admin = _tao_admin(client)
    if bat_2fa:
        bi_mat = _bat_2fa_admin(admin, dong_ho)
        cookies, csrf = _dang_nhap_2fa(
            client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat,
        )
        bang_chung = {"ma_2fa": dong_ho.ma_moi(bi_mat)}
    else:
        cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
        bang_chung = {"mat_khau": MK_ADMIN}
    r_stepup = _stepup(client, cookies, csrf, bang_chung)
    assert r_stepup.status_code == 200, r_stepup.text
    # Chứng minh cửa sổ đang có hiệu lực bằng một thay đổi cần step-up.
    r_toggle = client.post("/api/trinh-duyet/man-hinh/bat-tat", json={"bat": True},
                           cookies=cookies, headers=csrf)
    assert r_toggle.status_code == 200, r_toggle.text
    assert mod.bat_tat_goi == [True]
    r = _hien(client, cookies, csrf, {})
    assert r.status_code == 401, r.text
    assert mod.hien_goi == 0
    assert MK_VNC_TEST not in r.text


@pytest.mark.parametrize("bat_2fa", [False, True], ids=["password", "totp"])
def test_reveal_thanh_cong_khong_duoc_luu_cache(moi_truong, dong_ho, bat_2fa):
    client, mod = moi_truong
    admin = _tao_admin(client)
    if bat_2fa:
        bi_mat = _bat_2fa_admin(admin, dong_ho)
        cookies, csrf = _dang_nhap_2fa(
            client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat,
        )
        bang_chung = {"ma_2fa": dong_ho.ma_moi(bi_mat)}
    else:
        cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
        bang_chung = {"mat_khau": MK_ADMIN}
    r = _hien(client, cookies, csrf, bang_chung)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "mat_khau": MK_VNC_TEST}
    assert r.headers.get("Cache-Control") == "no-store"
    assert mod.hien_goi == 1


@pytest.mark.parametrize("kem_cookie", [False, True])
def test_reveal_bearer_kem_mat_khau_dung_van_bi_tu_choi(moi_truong, kem_cookie):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    if not kem_cookie:
        client.cookies.clear()
    r = client.post("/api/trinh-duyet/mat-khau-vnc/hien",
                    json={"mat_khau": MK_ADMIN, "id": admin["id"]},
                    headers={**csrf, **_qt()})
    assert r.status_code == 401, r.text
    assert MK_VNC_TEST not in r.text
    assert mod.hien_goi == 0


def test_reveal_phien_da_thu_hoi_khong_duoc_dung_mat_khau_dung(moi_truong):
    from app import phien_web

    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    phien_web.thu_hoi_phien_nguoi_dung(admin["id"])
    r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 401, r.text
    assert mod.hien_goi == 0


def test_reveal_kho_2fa_hong_khong_lui_sang_mat_khau(moi_truong, monkeypatch):
    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)

    def kho_hong(_):
        raise xac_thuc_2fa.LoiKho2FA()

    monkeypatch.setattr(xac_thuc_2fa, "da_bat_2fa", kho_hong)
    r = _hien(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "totp_store_unreadable"
    assert MK_ADMIN not in r.text and MK_VNC_TEST not in r.text
    assert mod.hien_goi == 0


@pytest.mark.parametrize("method,path,body,ten", CAC_ROUTE_TRA_LOI_MODULE)
def test_ngoai_le_module_khong_lo_bi_mat_trong_loi_log_hay_nhat_ky(
    moi_truong, monkeypatch, caplog, method, path, body, ten,
):
    from app import nhat_ky

    client, mod = moi_truong
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200
    goi = AsyncMock(side_effect=RuntimeError(f"mat_khau={MK_VNC_TEST}"))
    monkeypatch.setattr(mod, ten, goi)
    with caplog.at_level(logging.DEBUG):
        r = client.request(method, "/api/trinh-duyet" + path,
                           json=body, cookies=cookies, headers=csrf)
    assert r.status_code == 502, r.text
    goi.assert_awaited_once()
    assert MK_VNC_TEST not in r.text
    assert MK_VNC_TEST not in caplog.text
    assert MK_VNC_TEST not in str(nhat_ky.doc(gioi_han=500))


@pytest.mark.parametrize("ma_loi", [None, "sai_khoa_dich_vu", "tinh_nang_tat"])
@pytest.mark.parametrize("method,path,body,ten", CAC_ROUTE_TRA_LOI_MODULE)
def test_loi_tu_do_cua_module_khong_phan_chieu_bi_mat(
    moi_truong, goi_module, ma_loi, method, path, body, ten,
):
    client, _ = moi_truong
    # Chỉ một controller để đi tới nhánh lỗi upstream; nhiều controller bị chặn 409.
    client.app.state.so.dang_chay.pop(MODULE_KHAC)
    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    r_stepup = _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN})
    assert r_stepup.status_code == 200, r_stepup.text
    goi = goi_module[MODULE, ten]
    loi_gia = f"chi-tiet-module-gia: mat_khau={MK_VNC_TEST}"
    goi.side_effect = [
        {"ok": False, "loi": loi_gia, "ma_loi": ma_loi},
        {"ok": False, "loi": "loi-tu-do-khac", "ma_loi": ma_loi},
    ]
    cac_phan_hoi = []
    for _ in range(2):
        r = client.request(method, "/api/trinh-duyet" + path,
                           json=body, cookies=cookies, headers=csrf)
        assert r.status_code == 502, r.text
        assert MK_VNC_TEST not in r.text
        assert "chi-tiet-module-gia" not in r.text
        assert "loi-tu-do-khac" not in r.text
        assert r.json()["error"]["message"]
        cac_phan_hoi.append(r.json())
    assert cac_phan_hoi[0] == cac_phan_hoi[1], "Thông điệp phải độc lập với lỗi tự do"
    assert goi.await_count == 2
    for (module_id, _), goi_khac in goi_module.items():
        if module_id == MODULE_KHAC:
            goi_khac.assert_not_called()


@pytest.mark.parametrize("loai_ma", ["totp", "khoi_phuc"])
@pytest.mark.parametrize("dung_truoc_o", ["stepup", "reveal"])
def test_ma_da_dung_khong_replay_de_reveal(moi_truong, dong_ho, loai_ma, dung_truoc_o):
    client, mod = moi_truong
    admin = _tao_admin(client)
    bi_mat = _bat_2fa_admin(admin, dong_ho)
    cookies, csrf = _dang_nhap_2fa(
        client, admin["ten_dang_nhap"], MK_ADMIN, dong_ho, bi_mat,
    )
    if loai_ma == "totp":
        ma = dong_ho.ma_moi(bi_mat)
    else:
        ma = xac_thuc_2fa.tao_lai_ma_khoi_phuc(admin["id"])[0]
    bang_chung = {"ma_2fa": ma}
    goi_dau = _stepup if dung_truoc_o == "stepup" else _hien
    r_dau = goi_dau(client, cookies, csrf, bang_chung)
    assert r_dau.status_code == 200, r_dau.text
    so_lan_hien = 0 if dung_truoc_o == "stepup" else 1
    assert mod.hien_goi == so_lan_hien
    r = _hien(client, cookies, csrf, bang_chung)
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "invalid_totp"
    assert MK_VNC_TEST not in r.text
    assert mod.hien_goi == so_lan_hien
    # Mã tươi vẫn dùng được: lần từ chối trên là chống replay, không phải mất phiên.
    r_moi = _hien(client, cookies, csrf, {"ma_2fa": dong_ho.ma_moi(bi_mat)})
    assert r_moi.status_code == 200, r_moi.text
    assert r_moi.json()["mat_khau"] == MK_VNC_TEST
    assert mod.hien_goi == so_lan_hien + 1
