"""Test NV295 (hướng 19:30) — cổng IP LAN + tự ghim chứng chỉ lần đầu + cách ly.

- Cổng IP LAN (app/tu_xa.kiem_ip_lan): chỉ RFC 1918 + ULA fc00::/7 được
  nối; tên miền bị từ chối NGAY mà không phân giải DNS; IP WAN, CGNAT,
  loopback, link-local, dải đặc biệt đều bị từ chối.
- Tự ghim lần đầu: nối lần đầu tới IP LAN gặp chứng chỉ không kiểm được
  thì hệ thống TỰ đo + ghim vân tay rồi đăng nhập lại bằng đường ghim
  (kiểm trước khi gửi mật khẩu) — không hỏi người dùng, không có nút ở
  lần đầu. Nút «Tin chứng chỉ này» chỉ còn cho trường hợp chứng chỉ đổi.
- Cách ly (19:19): vé tin gắn module + đích; kho tin không chung; vân tay
  đầy đủ không bao giờ ra giao diện (kieu bi_mat, GET chỉ trả dạng che).

Mọi chứng chỉ/mật khẩu/địa chỉ trong file này đều GIẢ, tạo lúc chạy test
(openssl tự ký, loopback); không có vân tay thật của thiết bị nào.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC))

from app.kham_pha import doc_manifest, nap_ma  # noqa: E402
from app.tu_xa import kiem_ip_lan  # noqa: E402

THU_MUC_MIKROTIK = GOC / "modules" / "mikrotik-routeros"
THU_MUC_ZTE = GOC / "modules" / "zte-e2633"

MAT_KHAU_GIA = "mat-khau-gia-nv295b"
NGUOI_DUNG_GIA = "nguoi-doc-gia"
VAN_TAY_GIA = "cd" * 32  # 64 ký tự hex giả


@pytest.fixture(autouse=True)
def _cho_phep_loopback(monkeypatch):
    # Máy chủ giả chạy trên loopback — chỉ test mới được phép.
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


# ───────── Cổng IP LAN ─────────

@pytest.mark.parametrize("ip", [
    "10.0.0.1", "10.255.255.255", "172.16.0.1", "172.31.255.255",
    "192.168.0.1", "192.168.255.255", "fd00::1", "fd12:3456::1",
    "::ffff:192.168.1.1",  # IPv4-mapped → bóc ra 192.168.1.1
])
def test_ip_lan_cho_phep(ip):
    assert str(kiem_ip_lan(ip)) in (ip, "192.168.1.1")


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "1.1.1.1", "100.64.0.1", "100.127.255.255",  # WAN + CGNAT
    "127.0.0.1", "::1", "169.254.1.1", "169.254.169.254", "fe80::1",
    "0.0.0.0", "::", "224.0.0.1", "ff02::1",
    "192.0.2.1", "198.51.100.1", "203.0.113.1", "2001:db8::1", "240.0.0.1",
    "2002:7f00:1::",  # 6to4 nhúng 127.0.0.1
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
])
def test_ip_lan_tu_choi(ip, monkeypatch):
    # Loopback trong danh sách này bị từ chối vì test này KHÔNG bật cờ test.
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    with pytest.raises(ValueError):
        kiem_ip_lan(ip)


def test_ip_lan_tu_choi_ten_mien_khong_phan_giai(monkeypatch):
    # Tên miền bị từ chối NGAY — getaddrinfo mà được gọi là test hỏng.
    def _cam(*a, **k):
        raise AssertionError("kiem_ip_lan không được phân giải DNS")
    monkeypatch.setattr(socket, "getaddrinfo", _cam)
    with pytest.raises(ValueError, match="tên miền"):
        kiem_ip_lan("router.lan")
    with pytest.raises(ValueError, match="tên miền"):
        kiem_ip_lan("example.com")


def test_ip_lan_loopback_chi_duoc_phep_khi_co_co_test():
    assert str(kiem_ip_lan("127.0.0.1")) == "127.0.0.1"
    assert str(kiem_ip_lan("::1")) == "::1"


# ───────── Nạp module mikrotik ─────────

_lop_mikrotik_cls = None
_ten_goi_mikrotik = None


def _lop_mikrotik():
    global _lop_mikrotik_cls, _ten_goi_mikrotik
    if _lop_mikrotik_cls is None:
        manifest, loi = doc_manifest(THU_MUC_MIKROTIK)
        assert loi is None, loi
        lop, _ten_goi_mikrotik = nap_ma(THU_MUC_MIKROTIK, manifest, "test")
        _lop_mikrotik_cls = lop
    return _lop_mikrotik_cls


def _module_mikrotik():
    manifest, loi = doc_manifest(THU_MUC_MIKROTIK)
    assert loi is None, loi
    return _lop_mikrotik()(manifest)


def test_mikrotik_kiem_dia_chi_tu_choi_ten_mien_khong_phan_giai(monkeypatch):
    def _cam(*a, **k):
        raise AssertionError("kiem_dia_chi không được phân giải DNS")
    monkeypatch.setattr(socket, "getaddrinfo", _cam)
    mod = _module_mikrotik()
    with pytest.raises(ValueError, match="tên miền"):
        mod.kiem_dia_chi("router.lan:8729")
    assert mod.kiem_dia_chi("192.168.1.1") == ("192.168.1.1", 8729)


def test_mikrotik_cai_tu_choi_wan_truoc_khi_noi(monkeypatch):
    # WAN bị từ chối ở bước kiểm địa chỉ — create_connection mà được gọi
    # là test hỏng (chưa hề nối ra ngoài).
    def _cam(*a, **k):
        raise AssertionError("địa chỉ WAN không được nối")
    monkeypatch.setattr(socket, "create_connection", _cam)
    kq = asyncio.run(_module_mikrotik().cai(
        {"dia_chi": "8.8.8.8:8729", "tai_khoan": NGUOI_DUNG_GIA,
         "mat_khau": MAT_KHAU_GIA, "kiem_chung_chi_tls": "true",
         "van_tay_tls": "", "cho_phep_khong_ma_hoa": "false"}))
    assert kq["ok"] is False
    assert "mạng nhà" in (kq["loi"] or "")


# ───────── Máy chủ RouterOS giả (TLS + /login) ─────────

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


@pytest.fixture(scope="session")
def chung_chi_ros(tmp_path_factory):
    d = tmp_path_factory.mktemp("nv295-ros")
    key, crt = d / "key.pem", d / "crt.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(crt), "-days", "1", "-nodes", "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True, timeout=60)
    der = ssl.PEM_cert_to_DER_cert(crt.read_text())
    return {"crt": crt, "key": key, "van_tay": hashlib.sha256(der).hexdigest()}


class _XuLyRos(socketserver.BaseRequestHandler):
    def handle(self):
        cfg = self.server.cfg
        s = self.request
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cfg["crt"]), str(cfg["key"]))
        try:
            s = ctx.wrap_socket(s, server_side=True)
        except (OSError, ssl.SSLError):
            return
        try:
            while True:
                cau = _doc_cau(s)
                if cau is None:
                    return
                if not cau:
                    continue
                if cau[0] == "/login":
                    ten, mk = _lay_tu(cau, "name"), _lay_tu(cau, "password")
                    cfg["da_thu_dang_nhap"].append((ten, mk))
                    if ten == cfg["nguoi_dung"] and mk == cfg["mat_khau"]:
                        s.sendall(_cau("!done"))
                    else:
                        s.sendall(_cau("!trap", "=message=cannot log in") + _cau("!done"))
                else:
                    s.sendall(_cau("!done"))
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                s.close()
            except OSError:
                pass


class MayChuRos(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def may_chu_ros(chung_chi_ros):
    created = []

    def _tao():
        cfg = {"crt": chung_chi_ros["crt"], "key": chung_chi_ros["key"],
               "nguoi_dung": NGUOI_DUNG_GIA, "mat_khau": MAT_KHAU_GIA,
               "da_thu_dang_nhap": []}
        srv = MayChuRos(("127.0.0.1", 0), _XuLyRos)
        srv.cfg = cfg
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True)
        t.start()
        created.append(srv)
        return srv, cfg

    yield _tao
    for srv in created:
        srv.shutdown()
        srv.server_close()


def test_mikrotik_tu_ghim_lan_dau_khong_hoi_nguoi_dung(may_chu_ros, chung_chi_ros):
    srv, cfg = may_chu_ros()
    kq = asyncio.run(_module_mikrotik().cai(
        {"dia_chi": f"127.0.0.1:{srv.server_address[1]}",
         "tai_khoan": NGUOI_DUNG_GIA, "mat_khau": MAT_KHAU_GIA,
         "kiem_chung_chi_tls": "true", "van_tay_tls": "",
         "cho_phep_khong_ma_hoa": "false"}))
    assert kq["ok"] is True
    assert "tin_chung_chi" not in kq  # lần đầu: tự ghim, không nút, không hỏi
    assert kq["cau_hinh_luu"]["van_tay_tls"] == chung_chi_ros["van_tay"]
    # Mật khẩu chỉ gửi ĐÚNG MỘT lần, ở lượt nối lại sau khi đã ghim
    # (lượt đầu chết ở bắt tay kiểm chứng chỉ, chưa gửi gì).
    assert cfg["da_thu_dang_nhap"] == [(NGUOI_DUNG_GIA, MAT_KHAU_GIA)]


def test_la_loi_xac_minh_chung_chi_chi_nhan_dung_loai_loi():
    _lop_mikrotik()
    goi = sys.modules[_ten_goi_mikrotik]
    fn = goi._la_loi_xac_minh_chung_chi
    assert fn(RuntimeError("x")) is False

    try:
        raise ssl.SSLCertVerificationError(1, "self-signed")
    except ssl.SSLCertVerificationError as e:
        assert fn(e) is True
    # Lỗi mạng trong bắt tay (không phải lỗi chứng chỉ) → không tự ghim.
    try:
        raise ConnectionResetError("reset")
    except OSError as e:
        assert fn(e) is False


# ───────── ZTE: tự ghim (mock khách) + từ chối tên miền/WAN ─────────

def _tai_zte():
    ten_goi = "nv295b_zte_test"
    for ten in [t for t in list(sys.modules) if t == ten_goi or t.startswith(ten_goi + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(ten_goi)
    goi.__path__ = [str(THU_MUC_ZTE)]
    sys.modules[ten_goi] = goi
    spec = importlib.util.spec_from_file_location(f"{ten_goi}.module",
                                                  THU_MUC_ZTE / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{ten_goi}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_zte_tu_ghim_lan_dau_luu_van_tay_do_duoc(monkeypatch):
    from app import chung_chi_tls
    mod = _tai_zte()

    class _KhachGia:
        def __init__(self, dia_chi, tai_khoan, mat_khau, *, kiem_chung_chi_tls=True,
                     van_tay_tls=""):
            self._van_tay = van_tay_tls
            self._client = None

        @property
        def khoa_phien(self):
            return ("127.0.0.1", NGUOI_DUNG_GIA)

        async def doc_may(self):
            if not self._van_tay:
                raise mod.ZteLoiKetNoi("khong toi duoc", tls_tu_ky=True)
            assert self._van_tay == VAN_TAY_GIA  # lượt thử lại phải mang vân tay vừa đo
            return []

        async def dong(self):
            pass

    monkeypatch.setattr(mod, "MayKhachZte", _KhachGia)
    monkeypatch.setattr(chung_chi_tls, "do_van_tay",
                        lambda h, p, het_gio=8.0: VAN_TAY_GIA)
    cai_mod = mod.Module(json.loads((THU_MUC_ZTE / "manifest.json").read_text()))
    kq = asyncio.run(cai_mod.cai({
        "dia_chi": "https://127.0.0.1:8443", "tai_khoan": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA, "kiem_chung_chi_tls": "true", "van_tay_tls": ""}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["van_tay_tls"] == VAN_TAY_GIA
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] is None and muc["canh_bao"] is None  # thử lại thành công: hết cảnh báo
    assert "tin_chung_chi" not in kq  # lần đầu: tự ghim, không nút


def test_zte_cai_tu_choi_ten_mien_khong_phan_giai(monkeypatch):
    def _cam(*a, **k):
        raise AssertionError("ZTE không được phân giải DNS cho tên miền")
    monkeypatch.setattr(socket, "getaddrinfo", _cam)
    monkeypatch.setattr(socket, "create_connection", _cam)
    mod = _tai_zte()
    cai_mod = mod.Module(json.loads((THU_MUC_ZTE / "manifest.json").read_text()))
    kq = asyncio.run(cai_mod.cai({
        "dia_chi": "https://router.lan:8443", "tai_khoan": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA}))
    assert kq["ok"] is False
    assert "tên miền" in (kq["loi"] or "")


def test_zte_cai_tu_choi_wan(monkeypatch):
    def _cam(*a, **k):
        raise AssertionError("ZTE không được nối ra WAN")
    monkeypatch.setattr(socket, "create_connection", _cam)
    mod = _tai_zte()
    cai_mod = mod.Module(json.loads((THU_MUC_ZTE / "manifest.json").read_text()))
    kq = asyncio.run(cai_mod.cai({
        "dia_chi": "https://8.8.8.8:8443", "tai_khoan": NGUOI_DUNG_GIA,
        "mat_khau": MAT_KHAU_GIA}))
    assert kq["ok"] is False
    assert "mạng nhà" in (kq["loi"] or "")


# ───────── Cách ly (19:19) + hiển thị (19:26) ─────────

def test_ve_tin_sai_module_bi_tu_choi():
    from app import chung_chi_tls

    class _SoGia:
        def __init__(self):
            self.da_goi = []

        async def cai(self, module_id, cau_hinh):
            self.da_goi.append((module_id, dict(cau_hinh)))
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}

    so = _SoGia()
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "192.0.2.1", 8729)
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, "zte-e2633", {}, ve, "abcd1234…ef567890"))
    assert kq["ok"] is False
    assert so.da_goi == []  # vé module A không bao giờ ghi vào module B


def test_tin_chung_chi_chi_ghim_dung_dich_trong_ve(may_chu_ros, chung_chi_ros):
    # Vé mang đích A: hành động tin đo đúng A và chỉ lưu vân tay của A —
    # dù cau_hinh form có trỏ sang nơi khác, vân tay lưu vẫn là của A
    # (module nối sang nơi khác sau đó sẽ hỏng kiểm ghim, không được tin ké).
    from app import chung_chi_tls

    class _SoGia:
        def __init__(self):
            self.da_goi = []

        async def cai(self, module_id, cau_hinh):
            self.da_goi.append((module_id, dict(cau_hinh)))
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}

    srv, _ = may_chu_ros()
    cong = srv.server_address[1]
    ve = chung_chi_tls.tao_ve("mikrotik-routeros", "127.0.0.1", cong)
    so = _SoGia()
    kq = asyncio.run(chung_chi_tls.tin_chung_chi(
        so, "mikrotik-routeros",
        {"dia_chi": "192.0.2.99:8729"},  # form trỏ nơi khác — vé vẫn thắng
        ve, chung_chi_tls.ma_hien_thi(chung_chi_ros["van_tay"])))
    assert kq["ok"] is True
    assert so.da_goi[0][1]["van_tay_tls"] == chung_chi_ros["van_tay"]


def test_de_nghi_doi_khong_chua_chuoi_thiet_bi_cung_cap(monkeypatch):
    # Khối tin chỉ có câu cố định + mặt nạ hex — không chuỗi nào do thiết bị
    # cung cấp (tên máy, subject chứng chỉ, chuỗi lỗi) lọt vào giao diện tin.
    from app import chung_chi_tls
    mod = _module_mikrotik()
    monkeypatch.setattr(chung_chi_tls, "do_van_tay",
                        lambda h, p, het_gio=8.0: VAN_TAY_GIA)
    de_nghi = asyncio.run(mod._de_nghi_tin("192.0.2.1", 8729))
    assert de_nghi is not None and de_nghi["ly_do"] == "doi"
    assert de_nghi["ma_hien_thi"] == chung_chi_tls.ma_hien_thi(VAN_TAY_GIA)
    for ch in "<>&":
        assert ch not in de_nghi["thong_bao"]


def test_van_tay_tls_la_bi_mat_khong_tra_day_du():
    # 19:26: vân tay đầy đủ không hiện — manifest đánh bi_mat nên kho chỉ
    # trả dạng che, giá trị đầy đủ nằm kho bí mật.
    from app import kho
    for module_id in ("mikrotik-routeros", "zte-e2633"):
        m = json.loads((GOC / "modules" / module_id / "manifest.json").read_text())
        truong = next(t for t in m["truong_cau_hinh"] if t["khoa"] == "van_tay_tls")
        assert truong["kieu"] == "bi_mat"
    cong_khai, bi_mat = kho.tach_cau_hinh(
        [{"khoa": "van_tay_tls", "kieu": "bi_mat"}], {"van_tay_tls": VAN_TAY_GIA})
    assert cong_khai == {"van_tay_tls": "…" + VAN_TAY_GIA[-4:]}
    assert bi_mat == {"van_tay_tls": VAN_TAY_GIA}
