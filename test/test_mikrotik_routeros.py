"""Test module mikrotik-routeros (modules/mikrotik-routeros/): nguồn máy mạng CHỈ ĐỌC
từ router MikroTik qua RouterOS API (giao thức câu-từ nhị phân, TLS cổng 8729).

Máy chủ RouterOS GIẢ dựng bằng thư viện chuẩn (socket + ssl + openssl tạo chứng chỉ
tự ký lúc chạy test); mọi tương tác mạng đều loopback, không mạng thật.
Mật khẩu trong test là chuỗi GIẢ rõ ràng; địa chỉ ví dụ 192.0.2.x / MAC aa:bb:cc:…
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.kham_pha import doc_manifest, kiem_manifest, nap_ma

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "mikrotik-routeros"
MAT_KHAU_GIA = "mat-khau-gia-nv112"
NGUOI_DUNG_GIA = "nguoi-doc-gia"

_routeros = None   # module routeros.py, nạp lười sau khi có mã
_lop_module = None


def _nap():
    global _routeros, _lop_module
    if _lop_module is None:
        manifest, loi = doc_manifest(THU_MUC_MODULE)
        assert loi is None, loi
        lop, ten_goi = nap_ma(THU_MUC_MODULE, manifest, "test")
        import sys
        goi = sys.modules[ten_goi]
        _lop_module = lop
        import importlib
        _routeros = importlib.import_module(f"{ten_goi}.routeros")
    return _routeros, _lop_module


def _module_moi():
    _nap()
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    return _lop_module(manifest)


# ───────── Mã hoá câu-từ RouterOS (phía máy chủ giả) ─────────

def _ma_do_dai(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + n.to_bytes(4, "big")


def _cau(*tu: str) -> bytes:
    ra = b""
    for t in tu:
        b = t.encode("utf-8")
        ra += _ma_do_dai(len(b)) + b
    return ra + b"\x00"


def _doc_chinh_xac(s, n: int) -> bytes | None:
    b = b""
    while len(b) < n:
        try:
            c = s.recv(n - len(b))
        except (OSError, ssl.SSLError):
            return None
        if not c:
            return None
        b += c
    return b


def _doc_do_dai(s) -> int | None:
    b = _doc_chinh_xac(s, 1)
    if b is None:
        return None
    c = b[0]
    if c < 0x80:
        return c
    if c < 0xC0:
        d = _doc_chinh_xac(s, 1)
        return None if d is None else ((c & 0x3F) << 8) | d[0]
    if c < 0xE0:
        d = _doc_chinh_xac(s, 2)
        return None if d is None else ((c & 0x1F) << 16) | int.from_bytes(d, "big")
    if c < 0xF0:
        d = _doc_chinh_xac(s, 3)
        return None if d is None else ((c & 0x0F) << 24) | int.from_bytes(d, "big")
    d = _doc_chinh_xac(s, 4)
    return None if d is None else int.from_bytes(d, "big")


def _doc_cau(s) -> list[str] | None:
    tu = []
    while True:
        n = _doc_do_dai(s)
        if n is None:
            return None
        if n == 0:
            return tu
        b = _doc_chinh_xac(s, n)
        if b is None:
            return None
        tu.append(b.decode("utf-8", errors="replace"))


def _lay_tu(cau, khoa):
    for t in cau:
        if t == f"={khoa}" or t.startswith(f"={khoa}="):
            _, _, v = t[1:].partition("=")
            return v
    return ""


# ───────── Máy chủ RouterOS giả ─────────

LEASES_GIA = [
    {"mac-address": "AA:BB:CC:00:00:01", "address": "192.0.2.11",
     "host-name": "may-tinh-van-phong", "status": "bound"},
    {"mac-address": "aa:bb:cc:00:00:02", "address": "192.0.2.12",
     "host-name": "dien-thoai\nXAU\x00\x01rat-dai-" + "x" * 60, "status": "bound"},
]
HOSTS_GIA = [
    {"mac-address": "AA:BB:CC:00:00:01", "on-interface": "bridge-lan"},
    {"mac-address": "AA:BB:CC:00:00:03", "on-interface": "ether2"},
]


@pytest.fixture(scope="session")
def chung_chi_tls(tmp_path_factory):
    """Chứng chỉ tự ký cho máy chủ giả (openssl, tạo một lần mỗi phiên test)."""
    d = tmp_path_factory.mktemp("ros-tls")
    key, crt = d / "key.pem", d / "crt.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(crt), "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True, timeout=60)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    return {"crt": crt, "key": key, "van_tay": hashlib.sha256(der).hexdigest()}


class _XuLy(socketserver.BaseRequestHandler):
    def handle(self):
        cfg = self.server.cfg
        s = self.request
        if cfg["tls"]:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cfg["crt"]), str(cfg["key"]))
            try:
                s = ctx.wrap_socket(s, server_side=True)
            except (OSError, ssl.SSLError):
                return
        try:
            self._phuc_vu(s, cfg)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass

    def _phuc_vu(self, s, cfg):
        while True:
            cau = _doc_cau(s)
            if cau is None:
                return
            cfg["da_nhan"].append(cau)
            if not cau:
                continue
            lenh = cau[0]
            if lenh == "/login":
                ten, mk = _lay_tu(cau, "name"), _lay_tu(cau, "password")
                cfg["da_thu_dang_nhap"].append((ten, mk))
                if ten == cfg["nguoi_dung"] and mk == cfg["mat_khau"]:
                    s.sendall(_cau("!done"))
                else:
                    s.sendall(_cau("!trap", "=message=cannot log in") + _cau("!done"))
            elif lenh == "/ip/dhcp-server/lease/print":
                self._tra_bang(s, cfg, cfg["leases"])
            elif lenh == "/interface/bridge/host/print":
                self._tra_bang(s, cfg, cfg["hosts"])
            else:
                s.sendall(_cau("!trap", "=message=unknown command") + _cau("!done"))

    def _tra_bang(self, s, cfg, bang):
        che_do = cfg["che_do"]
        if che_do == "im_lang":
            time.sleep(30)  # client phải hết giờ trước
            return
        if che_do == "rac":
            s.sendall(b"\xff\xff\xff\xff\xff")  # độ dài rác
            return
        if che_do == "cat_giua_chung":
            if bang:
                row = bang[0]
                s.sendall(_cau("!re", *[f"={k}={v}" for k, v in row.items()]))
            s.close()  # ngắt giữa chừng, không !done
            return
        if che_do == "nhieu_cau":
            for i in range(70):
                s.sendall(_cau("!re", "=mac-address=AA:BB:CC:00:00:01",
                               f"=address=192.0.2.{100 + i}"))
            s.sendall(_cau("!done"))
            return
        for row in bang:
            s.sendall(_cau("!re", *[f"={k}={v}" for k, v in row.items()]))
        s.sendall(_cau("!done"))


class MayChuGia(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def may_chu(chung_chi_tls, tmp_path):
    """Máy chủ giả chạy nền; trả (may_chu, cfg). cfg['che_do'] đổi được giữa các test."""
    created = []

    def _tao(*, che_do="thuong", leases=None, hosts=None):
        cfg = {
            "tls": True, "crt": chung_chi_tls["crt"], "key": chung_chi_tls["key"],
            "nguoi_dung": NGUOI_DUNG_GIA, "mat_khau": MAT_KHAU_GIA,
            "leases": LEASES_GIA if leases is None else leases,
            "hosts": HOSTS_GIA if hosts is None else hosts,
            "che_do": che_do, "da_nhan": [], "da_thu_dang_nhap": [],
        }
        srv = MayChuGia(("127.0.0.1", 0), _XuLy)
        srv.cfg = cfg
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        t.start()
        created.append(srv)
        return srv, cfg

    yield _tao
    for srv in created:
        srv.shutdown()
        srv.server_close()


def _ket_noi_test(ros, host, port, *, van_tay, het_gio=5.0):
    return ros.ket_noi(host, port, NGUOI_DUNG_GIA, MAT_KHAU_GIA,
                       tls=True, kiem_chung_chi=False, van_tay=van_tay, het_gio=het_gio)


def _cau_hinh_test(port):
    return {"dia_chi": f"127.0.0.1:{port}", "tai_khoan": NGUOI_DUNG_GIA,
            "mat_khau": MAT_KHAU_GIA, "kiem_chung_chi_tls": "false",
            "van_tay_tls": "", "cho_phep_khong_ma_hoa": "false"}


@pytest.fixture(autouse=True)
def _cho_phep_loopback(monkeypatch):
    # Máy chủ giả chạy trên loopback — chỉ test mới được phép.
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

# ───────── Client: đăng nhập / đọc ─────────

def test_dang_nhap_va_doc_thanh_cong(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        leases = p.goi_doc("/ip/dhcp-server/lease/print", "mac-address,address")
        assert len(leases) == 2
        assert leases[0]["mac-address"] == "AA:BB:CC:00:00:01"
        hosts = p.goi_doc("/interface/bridge/host/print")
        assert any(h.get("on-interface") == "bridge-lan" for h in hosts)
        assert cfg["da_thu_dang_nhap"] == [(NGUOI_DUNG_GIA, MAT_KHAU_GIA)]
    finally:
        p.dong()


def test_dang_nhap_sai_mat_khau(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        with pytest.raises(ros.LoiDangNhap) as ei:
            p.dang_nhap(NGUOI_DUNG_GIA, "sai-hoan-toan")
        assert "sai-hoan-toan" not in str(ei.value)  # mật khẩu không vào ngoại lệ
    finally:
        p.dong()


def test_van_tay_sai_khong_gui_mat_khau(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    with pytest.raises(ros.LoiChungChi) as ei:
        _ket_noi_test(ros, "127.0.0.1", port, van_tay="0" * 64)
    assert cfg["da_thu_dang_nhap"] == []  # chưa hề gửi mật khẩu
    assert MAT_KHAU_GIA not in str(ei.value)


def test_van_tay_dung_dang_chu_thuong_va_khoang_trang(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu()
    port = srv.server_address[1]
    vt = " " + chung_chi_tls["van_tay"].upper() + "\n"
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=vt)
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
    finally:
        p.dong()


# ───────── Client: CHỈ lệnh đọc (lọc theo cấu trúc, trước khi gửi) ─────────

LENH_CAM = [
    "/ip/dhcp-server/lease/add",
    "/ip/dhcp-server/lease/set",
    "/ip/dhcp-server/lease/remove",
    "/system/reboot",
    "/ip/dhcp-server/lease/print ",          # dấu cách cuối
    " /ip/dhcp-server/lease/print",          # dấu cách đầu
    "/ip/dhcp-server/lease/print\n",
    "/ip/dhcp-server/lease/print;reboot",
    "/ip/dhcp-server/lease/print?name=x",    # truy vấn giả dạng
    "/ip/dhcp-server/lease/print=x",
    "print",
    "",
    "/",
    "/printX",
    "X/ip/dhcp-server/lease/print",
]


@pytest.mark.parametrize("lenh", LENH_CAM)
def test_lenh_ghi_va_gia_dang_bi_tu_choi_truoc_khi_gui(may_chu, chung_chi_tls, lenh):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        so_luong_da_nhan = len(cfg["da_nhan"])
        with pytest.raises(ros.RouterOSError):
            p.goi_doc(lenh)
        assert len(cfg["da_nhan"]) == so_luong_da_nhan  # không byte lệnh nào tới server
    finally:
        p.dong()


def test_proplist_la_bi_tu_choi(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, cfg = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print", "mac-address;reboot")
    finally:
        p.dong()


# ───────── Client: câu trả lời lạ / ngắt / hết giờ ─────────

def test_cau_sai_khuon_khong_treo(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu(che_do="rac")
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"], het_gio=5.0)
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print")
    finally:
        p.dong()


def test_ngat_ket_noi_giua_chung(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu(che_do="cat_giua_chung")
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"], het_gio=5.0)
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print")
    finally:
        p.dong()


def test_het_gio_doc(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu(che_do="im_lang")
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"], het_gio=1.0)
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        bat_dau = time.monotonic()
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print")
        assert time.monotonic() - bat_dau < 10.0  # hết giờ thật, không treo
    finally:
        p.dong()


def test_ket_noi_that_bai_nhanh(may_chu):
    ros, _ = _nap()
    with pytest.raises(ros.LoiKetNoi):
        ros.ket_noi("127.0.0.1", 9, NGUOI_DUNG_GIA, MAT_KHAU_GIA,
                    tls=False, het_gio=2.0)  # cổng 9 không ai nghe


def test_tran_so_cau_chong_phinh_bo_nho(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu(che_do="nhieu_cau")
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"], het_gio=5.0)
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        with pytest.raises(ros.RouterOSError):
            p.goi_doc("/ip/dhcp-server/lease/print")
    finally:
        p.dong()


def test_trap_cua_router_bi_cat_ngan(may_chu, chung_chi_tls):
    ros, _ = _nap()
    srv, _ = may_chu()
    port = srv.server_address[1]
    p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"])
    try:
        p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
        with pytest.raises(ros.RouterOSError) as ei:
            p.goi_doc("/lenh/khong/co/print")
        assert len(str(ei.value)) <= 120  # lời router bị cắt, không phình
    finally:
        p.dong()


def test_mat_khau_khong_ro_ri_qua_ngoai_le_va_log(may_chu, chung_chi_tls, caplog):
    ros, _ = _nap()
    srv, _ = may_chu(che_do="rac")
    port = srv.server_address[1]
    with caplog.at_level(logging.WARNING, logger="web2api.mikrotik_routeros"):
        p = _ket_noi_test(ros, "127.0.0.1", port, van_tay=chung_chi_tls["van_tay"], het_gio=3.0)
        try:
            try:
                p.dang_nhap(NGUOI_DUNG_GIA, MAT_KHAU_GIA)
                p.goi_doc("/ip/dhcp-server/lease/print")
            except ros.RouterOSError as e:
                assert MAT_KHAU_GIA not in str(e)
                logging.getLogger("web2api.mikrotik_routeros").warning(
                    "lỗi đọc router: %s", type(e).__name__)
        finally:
            p.dong()
    for rec in caplog.records:
        assert MAT_KHAU_GIA not in rec.getMessage()

# ───────── Module: manifest ─────────

def test_manifest_hop_le_va_du_truong():
    manifest, loi = doc_manifest(THU_MUC_MODULE)
    assert loi is None, loi
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "mikrotik-routeros"
    assert manifest["tien_to"] == "mikrotikrouteros"
    assert "thiet_bi" in manifest["cung_cap"]
    assert manifest["yeu_cau_goi"] == []  # chỉ thư viện chuẩn
    khoa = {t["khoa"]: t for t in manifest["truong_cau_hinh"]}
    assert khoa["mat_khau"]["kieu"] == "bi_mat"
    assert khoa["dia_chi"]["bat_buoc"] and khoa["tai_khoan"]["bat_buoc"]
    for truong in ("mo_ta", "tien_to", "phien_ban", "hang", "giao_thuc"):
        assert manifest[truong]
    assert isinstance(manifest["tai_nguyen"]["mang_ra"], list) and manifest["tai_nguyen"]["mang_ra"]


# ───────── Module: kiểm địa chỉ (đúng luật app/tu_xa.py) ─────────

def test_kiem_dia_chi_lan_hop_le():
    mod = _module_moi()
    assert mod.kiem_dia_chi("192.168.1.1") == ("192.168.1.1", 8729)
    assert mod.kiem_dia_chi("10.0.0.1:8729") == ("10.0.0.1", 8729)
    assert mod.kiem_dia_chi(" 172.16.5.5:18729 ") == ("172.16.5.5", 18729)


def test_kiem_dia_chi_chan_loopback_mac_dinh():
    mod = _module_moi()
    import os
    os.environ.pop("WEB2API_TEST_CHO_PHEP_LOOPBACK", None)
    try:
        with pytest.raises(ValueError):
            mod.kiem_dia_chi("127.0.0.1")
    finally:
        os.environ["WEB2API_TEST_CHO_PHEP_LOOPBACK"] = "1"


def test_kiem_dia_chi_chan_link_local_va_sai_khuon():
    mod = _module_moi()
    with pytest.raises(ValueError):
        mod.kiem_dia_chi("169.254.169.254")
    for xau in ("", "192.168.1.1:99999", "192.168.1.1:0", "khong-phai-dia-chi..", "192.168.1.1:abc"):
        with pytest.raises(ValueError):
            mod.kiem_dia_chi(xau)


# ───────── Module: cai() ─────────

def test_cai_thanh_cong(may_chu, chung_chi_tls):
    mod = _module_moi()
    srv, _ = may_chu()
    port = srv.server_address[1]
    cfg = _cau_hinh_test(port)
    cfg["van_tay_tls"] = chung_chi_tls["van_tay"]
    kq = asyncio.run(mod.cai(cfg))
    assert kq["ok"] is True
    assert kq["loi"] is None
    assert kq["ket_qua_tung_khoa"] == []
    luu = kq["cau_hinh_luu"]
    assert luu["tai_khoan"] == NGUOI_DUNG_GIA
    assert luu["mat_khau"] == MAT_KHAU_GIA  # lõi lưu vào kho bí mật, không trả ra API


def test_cai_thieu_truong_bat_buoc():
    mod = _module_moi()
    kq = asyncio.run(mod.cai({"dia_chi": "192.168.1.1", "tai_khoan": "doc"}))
    assert kq["ok"] is False and "mat_khau" in kq["loi"]
    kq2 = asyncio.run(mod.cai({"tai_khoan": "doc", "mat_khau": "x"}))
    assert kq2["ok"] is False and "dia_chi" in kq2["loi"]


def test_cai_dang_nhap_sai_khong_lo_mat_khau(may_chu, chung_chi_tls):
    mod = _module_moi()
    srv, _ = may_chu()
    port = srv.server_address[1]
    cfg = _cau_hinh_test(port)
    cfg["mat_khau"] = "sai-mat-khau-nv112"
    cfg["van_tay_tls"] = chung_chi_tls["van_tay"]
    kq = asyncio.run(mod.cai(cfg))
    assert kq["ok"] is False
    assert "sai-mat-khau-nv112" not in str(kq["loi"])


def test_cai_dia_chi_bi_chan(may_chu):
    mod = _module_moi()
    import os
    os.environ.pop("WEB2API_TEST_CHO_PHEP_LOOPBACK", None)
    try:
        kq = asyncio.run(mod.cai({"dia_chi": "127.0.0.1", "tai_khoan": "a", "mat_khau": "b"}))
        assert kq["ok"] is False
    finally:
        os.environ["WEB2API_TEST_CHO_PHEP_LOOPBACK"] = "1"


# ───────── Module: thuc_the() gộp MAC ─────────

def _nap_va_chay_thuc_the(port, van_tay):
    mod = _module_moi()
    cfg = _cau_hinh_test(port)
    cfg["van_tay_tls"] = van_tay
    mod.nap(cfg)
    return asyncio.run(mod.thuc_the(None))


def test_thuc_the_gop_lease_va_host_theo_mac(may_chu, chung_chi_tls):
    srv, _ = may_chu()
    port = srv.server_address[1]
    ds = _nap_va_chay_thuc_the(port, chung_chi_tls["van_tay"])
    theo_id = {e["id"]: e for e in ds}
    assert len(ds) == 3  # 2 lease + 1 host-only

    e1 = theo_id["mikrotikrouteros.aabbcc000001"]
    assert e1["ten"] == "may-tinh-van-phong"
    assert e1["loai"] == "may_mang"
    assert e1["thuoc_tinh"]["mac"] == "aa:bb:cc:00:00:01"
    assert e1["thuoc_tinh"]["ip"] == "192.0.2.11"
    assert e1["thuoc_tinh"]["ten_may_tu_khai"] == "may-tinh-van-phong"
    assert e1["thuoc_tinh"]["cong"] == "bridge-lan"
    assert e1["thuoc_tinh"]["nguon"] == "dhcp"
    assert e1["rui_ro_mac_dinh"] == "doc"
    assert e1["tinh_nang"] == []

    e3 = theo_id["mikrotikrouteros.aabbcc000003"]  # chỉ có trong bảng host
    assert e3["thuoc_tinh"]["nguon"] == "host"
    assert e3["thuoc_tinh"]["ip"] is None
    assert e3["thuoc_tinh"]["cong"] == "ether2"


def test_thuc_the_lam_sach_ten_khong_tin(may_chu, chung_chi_tls):
    srv, _ = may_chu()
    port = srv.server_address[1]
    ds = _nap_va_chay_thuc_the(port, chung_chi_tls["van_tay"])
    e2 = {e["id"]: e for e in ds}["mikrotikrouteros.aabbcc000002"]
    ten = e2["ten"]
    assert "\n" not in ten and "\x00" not in ten
    assert len(ten) <= 40
    assert ten == e2["thuoc_tinh"]["ten_may_tu_khai"]


def test_thuc_the_bo_mac_sai_khuon(may_chu, chung_chi_tls):
    leases_xau = [
        {"mac-address": "khong-phai-mac", "address": "192.0.2.21", "host-name": "xau1"},
        {"mac-address": "FF:FF:FF:FF:FF:FF", "address": "192.0.2.22", "host-name": "xau2"},
        {"mac-address": "00:00:00:00:00:00", "address": "192.0.2.23", "host-name": "xau3"},
    ]
    srv, _ = may_chu(leases=leases_xau, hosts=[])
    port = srv.server_address[1]
    ds = _nap_va_chay_thuc_the(port, chung_chi_tls["van_tay"])
    assert ds == []


def test_thuc_the_chua_cau_hinh_tra_rong():
    mod = _module_moi()
    assert asyncio.run(mod.thuc_the(None)) == []
    sk = asyncio.run(mod.suc_khoe())
    assert sk["ok"] is False
    assert sk["khoa"] == []


def test_thuc_the_loi_server_khong_sap(may_chu, chung_chi_tls):
    srv, _ = may_chu(che_do="cat_giua_chung")
    port = srv.server_address[1]
    ds = _nap_va_chay_thuc_the(port, chung_chi_tls["van_tay"])
    assert ds == []


def test_thuc_hien_luon_tu_choi():
    mod = _module_moi()
    kq = asyncio.run(mod.thuc_hien("mikrotikrouteros.aabbcc000001", "bat", {}))
    assert kq["ok"] is False
    assert kq["loi"]


def test_trang_thai():
    mod = _module_moi()
    kq = asyncio.run(mod.trang_thai(["mikrotikrouteros.aabbcc000001"]))
    assert kq["mikrotikrouteros.aabbcc000001"]["hop_le"] is False


def test_suc_khoe_thanh_cong(may_chu, chung_chi_tls):
    mod = _module_moi()
    srv, _ = may_chu()
    port = srv.server_address[1]
    cfg = _cau_hinh_test(port)
    cfg["van_tay_tls"] = chung_chi_tls["van_tay"]
    mod.nap(cfg)
    sk = asyncio.run(mod.suc_khoe())
    assert sk["ok"] is True
    assert MAT_KHAU_GIA not in json.dumps(sk, ensure_ascii=False)


def test_suc_khoe_sai_van_tay(may_chu, chung_chi_tls):
    mod = _module_moi()
    srv, _ = may_chu()
    port = srv.server_address[1]
    cfg = _cau_hinh_test(port)
    cfg["van_tay_tls"] = "f" * 64
    mod.nap(cfg)
    sk = asyncio.run(mod.suc_khoe())
    assert sk["ok"] is False
    assert MAT_KHAU_GIA not in json.dumps(sk, ensure_ascii=False)
