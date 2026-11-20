"""Các đường quản lý màn hình NV305 dùng chung một module theo hợp đồng."""

from unittest.mock import Mock, call

import pytest

from test_nv305_bao_mat import (
    MK_ADMIN,
    MK_VNC_TEST,
    ModBaoMatGia,
    _dang_nhap,
    _stepup,
    _tao_admin,
    moi_truong,  # noqa: F401 — fixture
)


@pytest.mark.parametrize("cac_kha_nang, cac_ma", [
    pytest.param([], [404] * 5, id="so-rong"),
    pytest.param([()], [404] * 5, id="khong-callable"),
    pytest.param([(0, 1, 2, 3, 4)], [200] * 5, id="du-nam"),
    pytest.param([(0, 1)], [200, 200, 501, 501, 501], id="module-cu"),
    pytest.param([(0,)], [200, 501, 501, 501, 501], id="chi-dat"),
    pytest.param([(1,)], [501, 200, 501, 501, 501], id="chi-trang-thai-mat-khau"),
    pytest.param([(2,)], [501, 501, 200, 501, 501], id="chi-hien"),
    pytest.param([(3,)], [501, 501, 501, 200, 501], id="chi-bat-tat"),
    pytest.param([(4,)], [501, 501, 501, 501, 200], id="chi-trang-thai-man-hinh"),
    pytest.param([(0, 1, 2, 3)], [200, 200, 200, 200, 501], id="thieu-mot"),
    pytest.param([(0, 1, 2, 3, 4), (0,)], [409] * 5, id="du-nam-va-mot-phan"),
    pytest.param([(0,), (1,)], [409] * 5, id="tach-doc-ghi"),
    pytest.param([(0, 1), (2, 3, 4)], [409] * 5, id="tach-cu-moi"),
    pytest.param([(0, 1, 2, 3, 4)] * 2, [409] * 5, id="hai-controller-day-du"),
])
def test_nam_duong_quan_ly_man_hinh_chon_module_theo_hop_dong(
    moi_truong, cac_kha_nang, cac_ma,
):
    client, mod = moi_truong
    dang_chay = client.app.state.so.dang_chay
    dang_chay.clear()
    thuong = Mock(spec=["models"])
    id_gui_len = "module-thuong-gia"
    dang_chay[id_gui_len] = thuong
    ten_phuong_thuc = (
        "dat_mat_khau_vnc", "trang_thai_mat_khau_vnc", "hien_mat_khau_vnc",
        "bat_tat_novnc", "trang_thai_novnc",
    )
    cac_module = []
    for i, kha_nang in enumerate(cac_kha_nang):
        dieu_khien = Mock(spec=ModBaoMatGia, wraps=mod if i == 0 else ModBaoMatGia())
        for j, ten in enumerate(ten_phuong_thuc):
            if j not in kha_nang:
                setattr(dieu_khien, ten, None)
        dang_chay[f"controller-gia-{i}"] = dieu_khien
        cac_module.append(dieu_khien)
    if len(cac_module) > 1:
        id_gui_len = "controller-gia-1"

    admin = _tao_admin(client)
    cookies, csrf = _dang_nhap(client, admin["ten_dang_nhap"], MK_ADMIN)
    assert _stepup(client, cookies, csrf, {"mat_khau": MK_ADMIN}).status_code == 200

    cac_duong = (
        ("PUT", "mat-khau-vnc", {"mat_khau": MK_VNC_TEST}),
        ("GET", "mat-khau-vnc/trang-thai", None),
        ("POST", "mat-khau-vnc/hien", {"mat_khau": MK_ADMIN}),
        ("POST", "man-hinh/bat-tat", {"bat": True}),
        ("GET", "man-hinh/trang-thai", None),
    )
    for (method, duong, than), ma in zip(cac_duong, cac_ma, strict=True):
        r = client.request(
            method, f"/api/trinh-duyet/{duong}", headers=csrf,
            params={"module": id_gui_len},
            json={**than, "module": id_gui_len} if than is not None else None,
        )
        assert r.status_code == ma, f"{method} {duong}: {r.text}"

    assert thuong.mock_calls == []
    cac_goi = [
        call.dat_mat_khau_vnc(MK_VNC_TEST),
        call.trang_thai_mat_khau_vnc(),
        call.hien_mat_khau_vnc(),
        call.bat_tat_novnc(True),
        call.trang_thai_novnc(),
    ]
    for i, dieu_khien in enumerate(cac_module):
        assert dieu_khien.mock_calls == ([
            goi for goi, ma in zip(cac_goi, cac_ma, strict=True) if ma == 200
        ] if i == 0 else [])
