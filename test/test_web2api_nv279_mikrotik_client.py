"""Test M1 cho NV279: Kiểm tra máy khách chỉ đọc routeros.py.

Chứng minh máy khách từ chối mọi lệnh ghi / ngoài danh sách TRƯỚC khi gửi byte nào,
chặn trường nhạy cảm trong proplist, tự điền proplist an toàn khi rỗng,
và chỉ cho phép 7 đường quy định.
"""
from __future__ import annotations

import pytest
from test_mikrotik_routeros import (
    _nap,
    _ket_noi_test,
    may_chu,
    chung_chi_tls,
    _cho_phep_loopback,
    NGUOI_DUNG_GIA,
    MAT_KHAU_GIA,
)

LENH_CAM_NV279 = [
    "/ip/address/add",
    "/interface/set",
    "/interface/disable",
    "/interface/enable",
    "/interface/wireguard/peers/remove",
    "/interface/wireguard/peers/set",
    "/system/reboot",
    "/system/script/run",
    "/system/resource/set",
    "/export",
    "/tool/fetch",
    "/user/print",  # ngoài danh sách
    "/interface/wireguard/print",  # có khoá riêng, ngoài danh sách
    "/ip/dhcp-server/lease/print;/system/reboot",
    "/interface/print =disabled=yes",
    "/interface/monitor-traffic",  # cấm qua goi_doc
]


@pytest.mark.parametrize("lenh", LENH_CAM_NV279)
def test_goi_doc_tu_choi_lenh_cam_truoc_khi_gui(may_chu, chung_chi_tls, lenh):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        so_luong_da_nhan = len(cfg["da_nhan"])
        with pytest.raises(ros.RouterOSError):
            p.goi_doc(lenh)
        assert len(cfg["da_nhan"]) == so_luong_da_nhan  # không gửi byte nào
    finally:
        p.dong()


@pytest.mark.parametrize("proplist", [
    "private-key",
    "preshared-key",
    "password",
    "mac-address,private-key",
    "mac-address;reboot",
    "mac-address=yes",
    "truong_la_khong_co_trong_tap_cho_phep",
])
def test_goi_doc_tu_choi_proplist_cam_hoac_la(may_chu, chung_chi_tls, proplist):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        so_luong_da_nhan = len(cfg["da_nhan"])
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print", proplist)
        assert len(cfg["da_nhan"]) == so_luong_da_nhan
    finally:
        p.dong()


def test_goi_doc_proplist_rong_tu_dien_tap_day_du_va_an_toan(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        p.goi_doc("/ip/dhcp-server/lease/print")
        # Câu gửi đến server phải có proplist đầy đủ
        cau_gui = cfg["da_nhan"][-1]
        assert cau_gui[0] == "/ip/dhcp-server/lease/print"
        tu_prop = [t for t in cau_gui if t.startswith("=.proplist=")][0]
        cac_truong = set(tu_prop.split("=")[2].split(","))
        assert cac_truong == ros.LENH_DOC_CHO_PHEP["/ip/dhcp-server/lease/print"]
        # Không chứa khoá riêng / mật khẩu
        assert "private-key" not in cac_truong
        assert "password" not in cac_truong
    finally:
        p.dong()


def test_goi_doc_loc_bo_truong_la_khi_router_tra_ve(may_chu, chung_chi_tls):
    ros, _ = _nap()
    # Server trả về trường .id và khoá lạ
    leases_co_id = [
        {
            ".id": "*1",
            "mac-address": "AA:BB:CC:00:00:01",
            "address": "192.0.2.11",
            "khoa_la": "gia_tri_la",
        }
    ]
    srv, cfg = may_chu(leases=leases_co_id)
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        kq = p.goi_doc("/ip/dhcp-server/lease/print")
        assert len(kq) == 1
        assert ".id" not in kq[0]
        assert "khoa_la" not in kq[0]
        assert kq[0]["mac-address"] == "AA:BB:CC:00:00:01"
        assert kq[0]["address"] == "192.0.2.11"
    finally:
        p.dong()


@pytest.mark.parametrize("ten_cong_sai", [
    "ether 1",
    "ether1=yes",
    "ether1;reboot",
    "ether1\n",
    "ether1\x00",
    "",
    "a" * 33,  # vượt quá 32 ký tự
])
def test_do_luu_luong_mot_lan_tu_choi_ten_cong_sai(may_chu, chung_chi_tls, ten_cong_sai):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        so_luong_da_nhan = len(cfg["da_nhan"])
        with pytest.raises(ros.RouterOSError):
            p.do_luu_luong_mot_lan(ten_cong_sai)
        assert len(cfg["da_nhan"]) == so_luong_da_nhan
    finally:
        p.dong()


def test_loi_router_tu_choi_nem_dung_lop_ngoai_le(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        # Máy chủ cũ trả !trap cho các lệnh ngoài lease/host
        with pytest.raises(ros.LoiRouterTuChoi) as exc_info:
            p.goi_doc("/interface/print")
        assert isinstance(exc_info.value, ros.RouterOSError)
    finally:
        p.dong()
