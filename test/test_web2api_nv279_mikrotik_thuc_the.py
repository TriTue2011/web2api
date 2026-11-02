"""Test toàn diện M3, M4, M5 cho NV279: Thực thể, nhịp, cô lập lỗi và dọn máy vắng."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import pytest

from app.kham_pha import doc_manifest
from test_mikrotik_routeros import (
    _nap,
    chung_chi_tls,
    _cho_phep_loopback,
    NGUOI_DUNG_GIA,
    MAT_KHAU_GIA,
    THU_MUC_MODULE,
)
from test_web2api_nv279_mikrotik_may_chu_gia import (
    tao_may_chu_nv279,
    INTERFACES_GIA,
    RESOURCE_GIA,
    WIREGUARD_PEERS_GIA,
    KID_CONTROL_GIA,
)


def _tao_module(tmp_path=None, dong_ho=None):
    _, lop = _nap()
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None
    return lop(manifest, _thu_muc_du_lieu=tmp_path, _dong_ho=dong_ho)


def _cau_hinh_chuan(port, van_tay, **kwargs):
    cfg = {
        "dia_chi": f"127.0.0.1:{port}",
        "tai_khoan": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA,
        "kiem_chung_chi_tls": "true",
        "van_tay_tls": van_tay,
        "cho_phep_khong_ma_hoa": "false",
        "chu_ky_nhanh_giay": "15",
        "chu_ky_cham_giay": "120",
        "ngay_giu_may_vang": "14",
        "bat_kid_control": "true",
    }
    cfg.update(kwargs)
    return cfg


def test_thuc_the_day_du_va_tinh_toc_do_cong(chung_chi_tls, tmp_path):
    srv, cfg = tao_may_chu_nv279(chung_chi_tls)
    port = srv.server_address[1]

    moc_thoi_gian = [1000.0]

    def dong_ho_gia():
        return moc_thoi_gian[0]

    mod = _tao_module(tmp_path=tmp_path, dong_ho=dong_ho_gia)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))

    try:
        # Lượt 1: khởi tạo
        tt1 = asyncio.run(mod.thuc_the())
        ids1 = {t["id"] for t in tt1}

        # 1. Kiểm tra có đủ các thực thể cổng mạng
        assert f"{mod.tien_to}.cong_ether1_trang_thai" in ids1
        assert f"{mod.tien_to}.cong_ether1_nhan_mbit" in ids1
        assert f"{mod.tien_to}.cong_ether1_gui_mbit" in ids1
        assert f"{mod.tien_to}.cong_wlan1_trang_thai" in ids1

        # 2. Kiểm tra tài nguyên
        assert f"{mod.tien_to}.router_cpu_phan_tram" in ids1
        assert f"{mod.tien_to}.router_ram_phan_tram" in ids1
        assert f"{mod.tien_to}.router_thoi_gian_chay_giay" in ids1
        assert f"{mod.tien_to}.router_nhiet_do_c" in ids1

        # 3. Kiểm tra WireGuard
        wg_peers = [t for t in tt1 if "wg_" in t["id"]]
        assert len(wg_peers) == 4  # 2 peers * 2 thực thể
        for p in wg_peers:
            # Tuyệt đối không chứa public key
            assert "public-key" not in str(p)

        # 4. Kiểm tra Kid Control
        kc_devs = [t for t in tt1 if "kc_" in t["id"]]
        assert len(kc_devs) == 2  # 1 dev * 2 thực thể
        for k in kc_devs:
            assert "mac_may" in k["thuoc_tinh"]
            assert "mac" not in k["thuoc_tinh"]  # Không nhầm với sổ máy mạng

        # 5. Kiểm tra sổ thiết bị lõi không nhận các thực thể mới
        cac_thuc_the_co_mac = [t for t in tt1 if t.get("thuoc_tinh", {}).get("mac")]
        for t in cac_thuc_the_co_mac:
            assert t["loai"] == "may_mang"

        # Kiểm tra trạng thái lượt 1: mẫu đầu của cổng có hop_le=False, gia_tri=None
        st1 = asyncio.run(mod.trang_thai([
            f"{mod.tien_to}.cong_ether1_nhan_mbit",
            f"{mod.tien_to}.cong_ether1_trang_thai",
            f"{mod.tien_to}.router_cpu_phan_tram",
            f"{mod.tien_to}.router_ram_phan_tram",
        ]))
        assert st1[f"{mod.tien_to}.cong_ether1_nhan_mbit"]["hop_le"] is False
        assert st1[f"{mod.tien_to}.cong_ether1_nhan_mbit"]["gia_tri"] is None
        assert st1[f"{mod.tien_to}.cong_ether1_trang_thai"]["gia_tri"] == "len"
        assert st1[f"{mod.tien_to}.router_cpu_phan_tram"]["gia_tri"] == 15
        assert st1[f"{mod.tien_to}.router_ram_phan_tram"]["gia_tri"] == 50.0

        # Lượt 2: Sau 8 giây, byte tăng 1_000_000 byte -> tốc độ 1.0 Mbit/s
        moc_thoi_gian[0] += 8.0
        # Buộc làm mới
        mod._lan_doc_nhanh = 0.0
        cfg["interfaces"][0]["rx-byte"] = "2000000"  # Tăng 1 MB trong 8s
        cfg["interfaces"][0]["tx-byte"] = "2000000"  # Không tăng -> 0 Mbit/s

        tt2 = asyncio.run(mod.thuc_the())
        st2 = asyncio.run(mod.trang_thai([
            f"{mod.tien_to}.cong_ether1_nhan_mbit",
            f"{mod.tien_to}.cong_ether1_gui_mbit",
        ]))
        assert st2[f"{mod.tien_to}.cong_ether1_nhan_mbit"]["hop_le"] is True
        assert st2[f"{mod.tien_to}.cong_ether1_nhan_mbit"]["gia_tri"] == 1.0
        assert st2[f"{mod.tien_to}.cong_ether1_gui_mbit"]["gia_tri"] == 0.0

    finally:
        srv.shutdown()
        srv.server_close()


def test_co_lap_loi_tung_nguon(chung_chi_tls, tmp_path):
    # Resource và WireGuard bị trap, interface và lease/host bình thường
    srv, cfg = tao_may_chu_nv279(
        chung_chi_tls,
        traps={"/system/resource/print", "/interface/wireguard/peers/print"}
    )
    port = srv.server_address[1]

    mod = _tao_module(tmp_path=tmp_path)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))

    try:
        tt = asyncio.run(mod.thuc_the())
        ids = {t["id"] for t in tt}

        # Interface và Lease/Host vẫn hoạt động bình thường!
        assert f"{mod.tien_to}.cong_ether1_trang_thai" in ids
        may_mang = [t for t in tt if t["loai"] == "may_mang"]
        assert len(may_mang) == 3

        # WireGuard bị trap -> nguồn là 'khong_co', không sinh thực thể
        assert not any("wg_" in i for i in ids)

        # Resource bị trap -> nguồn bị lỗi router_tu_choi
        sk = asyncio.run(mod.suc_khoe())
        assert sk["ok"] is True
        assert sk["nguon"]["wireguard"]["trang_thai"] == "khong_co"
        assert sk["nguon"]["resource"]["trang_thai"] == "loi"
        assert sk["nguon"]["resource"]["loi"] == "router_tu_choi"
        assert sk["nguon"]["interface"]["trang_thai"] == "ok"
    finally:
        srv.shutdown()
        srv.server_close()


def test_thuc_the_khong_chan_qua_3_giay_khi_server_cham(chung_chi_tls, tmp_path):
    # Server trả chậm 4 giây cho /interface/print
    srv, cfg = tao_may_chu_nv279(
        chung_chi_tls,
        do_tre_theo_lenh={"/interface/print": 4.0}
    )
    port = srv.server_address[1]

    mod = _tao_module(tmp_path=tmp_path)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))

    t0 = time.monotonic()
    try:
        # thuc_the phải trở về trước 3.5 giây với dữ liệu đệm
        tt = asyncio.run(mod.thuc_the())
        dt = time.monotonic() - t0
        assert dt < 3.5, f"thuc_the chặn quá lâu: {dt:.2f}s"
    finally:
        srv.shutdown()
        srv.server_close()


def test_don_may_vang_mat_va_so_ben(chung_chi_tls, tmp_path):
    moc_thoi_gian = [1000000.0]

    def dong_ho_gia():
        return moc_thoi_gian[0]

    # Cấu hình máy chủ với:
    # Máy 1: AA:BB:CC:00:00:01 (đang thấy ở cả lease và host)
    # Máy 2: AA:BB:CC:00:00:02 (chỉ có lease, status="waiting", last-seen không có)
    # Máy 3: AA:BB:CC:00:00:03 (chỉ có trong host)
    # Máy 4: AA:BB:CC:00:00:04 (lease tĩnh có last-seen="3w" = 21 ngày)
    leases = [
        {"mac-address": "AA:BB:CC:00:00:01", "address": "192.0.2.11", "status": "bound"},
        {"mac-address": "AA:BB:CC:00:00:02", "address": "192.0.2.12", "status": "waiting"},
        {"mac-address": "AA:BB:CC:00:00:04", "address": "192.0.2.14", "status": "waiting", "last-seen": "3w"},
    ]
    hosts = [
        {"mac-address": "AA:BB:CC:00:00:01", "on-interface": "bridge-lan"},
        {"mac-address": "AA:BB:CC:00:00:03", "on-interface": "ether2"},
    ]

    srv, cfg = tao_may_chu_nv279(chung_chi_tls, leases=leases, hosts=hosts)
    port = srv.server_address[1]

    mod = _tao_module(tmp_path=tmp_path, dong_ho=dong_ho_gia)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"], ngay_giu_may_vang="14"))

    try:
        # Lượt 1:
        # Máy 1: đang thấy -> có
        # Máy 2: không đang thấy, lần đầu quan sát -> ghi mốc vắng -> có
        # Máy 3: có trong host -> đang thấy -> có
        # Máy 4: last-seen="3w" (21 ngày > 14 ngày) -> KHÔNG báo ngay từ lượt đầu!
        tt1 = asyncio.run(mod.thuc_the())
        macs1 = {t["thuoc_tinh"]["mac"] for t in tt1 if t["loai"] == "may_mang"}
        assert "aa:bb:cc:00:00:01" in macs1
        assert "aa:bb:cc:00:00:02" in macs1
        assert "aa:bb:cc:00:00:03" in macs1
        assert "aa:bb:cc:00:00:04" not in macs1

        # Kiểm tra tệp sổ bền đã được ghi và có quyền 0600
        tep_so = tmp_path / "thay_lan_cuoi.json"
        assert tep_so.is_file()
        quyen = oct(os.stat(tep_so).st_mode & 0o777)
        assert quyen == "0o600"

        # Lượt 2: Sau 13 ngày 23 giờ (chưa quá 14 ngày)
        moc_thoi_gian[0] += (14 * 86400 - 3600)  # 13 ngày 23 giờ
        mod._lan_doc_cham = 0.0
        tt2 = asyncio.run(mod.thuc_the())
        macs2 = {t["thuoc_tinh"]["mac"] for t in tt2 if t["loai"] == "may_mang"}
        assert "aa:bb:cc:00:00:02" in macs2  # Vẫn báo vì chưa quá 14 ngày

        # Lượt 3: Sau thêm 2 giờ (tổng cộng 14 ngày 1 giờ > 14 ngày)
        moc_thoi_gian[0] += 7200
        mod._lan_doc_cham = 0.0
        tt3 = asyncio.run(mod.thuc_the())
        macs3 = {t["thuoc_tinh"]["mac"] for t in tt3 if t["loai"] == "may_mang"}
        assert "aa:bb:cc:00:00:02" not in macs3  # Đã bị dọn khỏi sổ!

        # Lượt 4: Khởi động lại module (instance mới) cùng thư mục dữ liệu
        mod_moi = _tao_module(tmp_path=tmp_path, dong_ho=dong_ho_gia)
        mod_moi.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"], ngay_giu_may_vang="14"))
        tt4 = asyncio.run(mod_moi.thuc_the())
        macs4 = {t["thuoc_tinh"]["mac"] for t in tt4 if t["loai"] == "may_mang"}
        assert "aa:bb:cc:00:00:02" not in macs4  # Vẫn giữ mốc và không báo

        # Lượt 5: Máy 2 quay trở lại mạng (status="bound")
        cfg["leases"][1]["status"] = "bound"
        mod_moi._lan_doc_cham = 0.0
        tt5 = asyncio.run(mod_moi.thuc_the())
        macs5 = {t["thuoc_tinh"]["mac"] for t in tt5 if t["loai"] == "may_mang"}
        assert "aa:bb:cc:00:00:02" in macs5  # Xuất hiện trở lại!

    finally:
        srv.shutdown()
        srv.server_close()


def test_cau_hinh_cai_va_nap(chung_chi_tls, tmp_path):
    srv, cfg = tao_may_chu_nv279(chung_chi_tls)
    port = srv.server_address[1]

    mod = _tao_module(tmp_path=tmp_path)

    # 1. Test cai() với chữ rác ở trường số -> ok: False
    kq_rac = asyncio.run(mod.cai(_cau_hinh_chuan(port, chung_chi_tls["van_tay"], chu_ky_nhanh_giay="abc")))
    assert kq_rac["ok"] is False
    assert "chu_ky_nhanh_giay" in str(kq_rac["loi"]) or "nhanh" in str(kq_rac["loi"])

    # 2. Test cai() hợp lệ: kẹp khoảng và đủ 10 trường trong cau_hinh_luu
    kq_hop_le = asyncio.run(mod.cai(_cau_hinh_chuan(
        port, chung_chi_tls["van_tay"],
        chu_ky_nhanh_giay="5",    # ngoài khoảng 10..3600 -> kẹp thành 10
        chu_ky_cham_giay="5000", # ngoài khoảng 30..3600 -> kẹp thành 3600
        ngay_giu_may_vang="0",   # ngoài khoảng 1..365 -> kẹp thành 1
    )))
    assert kq_hop_le["ok"] is True
    luu = kq_hop_le["cau_hinh_luu"]
    assert luu["chu_ky_nhanh_giay"] == "10"
    assert luu["chu_ky_cham_giay"] == "3600"
    assert luu["ngay_giu_may_vang"] == "1"
    assert luu["bat_kid_control"] == "true"
    assert "dia_chi" in luu
    assert "tai_khoan" in luu
    assert "mat_khau" in luu

    # 3. Test nap() với cấu hình cũ 0.1.0 (thiếu 4 trường mới)
    mod.nap({
        "dia_chi": f"127.0.0.1:{port}",
        "tai_khoan": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA,
    })
    assert mod._chu_ky_nhanh_giay == 15
    assert mod._chu_ky_cham_giay == 120
    assert mod._ngay_giu_may_vang == 14
    assert mod._bat_kid_control is True

    # 4. thuc_hien luôn từ chối
    kq_th = asyncio.run(mod.thuc_hien("any_id", "bat", {}))
    assert kq_th["ok"] is False

    srv.shutdown()
    srv.server_close()


def test_khong_mo_luong_doc_thu_hai_khi_luong_dau_con_chay(chung_chi_tls, tmp_path):
    # Server trả chậm 4 s: thuc_the bỏ chờ sau ~2,5 s; lần gọi ngay sau đó KHÔNG được mở luồng đọc thứ hai
    # (nếu mở thì router bị dồn lệnh và mỗi luồng giữ một phiên đăng nhập).
    srv, cfg = tao_may_chu_nv279(chung_chi_tls, do_tre_theo_lenh={"/interface/print": 4.0})
    port = srv.server_address[1]
    mod = _tao_module(tmp_path=tmp_path)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))
    try:
        async def hai_lan():
            await mod.thuc_the()
            await mod.thuc_the()
        asyncio.run(hai_lan())
        so_login = sum(1 for c in cfg["da_nhan"] if c and c[0] == "/login")
        assert so_login == 1, f"mở {so_login} phiên đăng nhập chồng nhau"
    finally:
        srv.shutdown()
        srv.server_close()


def test_loi_giao_thuc_giua_chung_thi_bo_phien_khong_doc_tiep_tren_phien_lech_nhip(chung_chi_tls, tmp_path):
    """Lệnh `lease` hỏng giữa chừng (ở đây: router ngắt kết nối sau câu đầu): phản hồi dở khiến phiên lệch nhịp, đọc tiếp lệnh `host` trên cùng
    phiên có thể nhận nhầm phần còn sót làm dữ liệu bảng host. Phiên phải bị bỏ ngay (lượt sau mở phiên mới)."""
    srv, cfg = tao_may_chu_nv279(chung_chi_tls, che_do_theo_lenh={"/ip/dhcp-server/lease/print": "cat_giua_chung"})
    port = srv.server_address[1]
    mod = _tao_module(tmp_path=tmp_path)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))
    try:
        asyncio.run(mod.thuc_the())
        lenh = [c[0] for c in cfg["da_nhan"] if c]
        assert "/ip/dhcp-server/lease/print" in lenh
        assert "/interface/bridge/host/print" not in lenh, "đã đọc tiếp host trên phiên lệch nhịp"
        sk = asyncio.run(mod.suc_khoe())
        assert sk["nguon"]["lease"]["trang_thai"] == "loi" and sk["nguon"]["lease"]["loi"] == "doc"
        assert sk["nguon"]["interface"]["trang_thai"] == "ok"          # các nguồn đã đọc xong trước đó không bị ảnh hưởng
    finally:
        srv.shutdown()
        srv.server_close()


def test_mang_hon_50_may_van_doc_du_bang_lease_va_host(chung_chi_tls, tmp_path):
    """Trần 50 câu cũ làm nguồn lease/host LỖI vĩnh viễn trên mạng có hơn 50 máy (nhà thật có hàng chục thiết bị)."""
    lease = [{"mac-address": f"AA:BB:CC:00:{i // 256:02X}:{i % 256:02X}", "address": f"192.0.2.{i % 250 + 1}", "host-name": f"may{i}",
              "status": "bound", "last-seen": "1m"} for i in range(1, 181)]
    host = [{"mac-address": l["mac-address"], "on-interface": "ether2"} for l in lease]
    srv, cfg = tao_may_chu_nv279(chung_chi_tls, leases=lease, hosts=host)
    port = srv.server_address[1]
    mod = _tao_module(tmp_path=tmp_path)
    mod.nap(_cau_hinh_chuan(port, chung_chi_tls["van_tay"]))
    try:
        tt = asyncio.run(mod.thuc_the())
        sk = asyncio.run(mod.suc_khoe())
        assert sk["nguon"]["lease"]["trang_thai"] == "ok" and sk["nguon"]["host"]["trang_thai"] == "ok"
        assert len([t for t in tt if t["loai"] == "may_mang"]) == 180
    finally:
        srv.shutdown()
        srv.server_close()
