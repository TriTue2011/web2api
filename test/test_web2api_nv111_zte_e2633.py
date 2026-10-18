"""Test NV111 — module `zte-e2633`: nguồn máy mạng (CHỈ ĐỌC) từ bộ phát WiFi ZTE E2633.

Test-first: chạy đỏ trước khi có module. Mọi máy chủ đều GIẢ cục bộ (127.0.0.1,
TLS tự ký do test tự tạo); không có mạng thật, không có dữ liệu thật của nhà chủ kho
(MAC/IP/tên máy đều GIẢ rõ ràng: aa:bb:cc:00:00:0x, 192.0.2.x, 198.51.100.x, «may-gia-…»).

Máy chủ giả mô phỏng ĐÚNG giao thức đo từ trang đăng nhập của thiết bị:
  GET / → HTML có addParameter("_sessionTOKEN", "<số>")
  GET /function_module/login_module/login_page/logintoken_lua.lua → token
  POST / đúng thứ tự trường (Username, Password, action, Frm_Logintoken, captchaCode,
      _sessionTOKEN) + header `check` = SHA-256 của đúng chuỗi thân → cookie phiên;
      sai thứ tự/sai check → trang «页面过期»
  GET /getpage.lua?pid=1005&nextpage=topo_lua.lua&Action=GetALLClients → JSON máy WiFi
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import logging
import socket
import ssl
import sys
import threading
import types
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "zte-e2633"
TEN_GOI = "zte_e2633_nv111_mod_test"

# ── Dữ liệu GIẢ rõ ràng (không phải của nhà ai) ──────────────────────────────
TAI_KHOAN_GIA = "admin"
MAT_KHAU_GIA = "MatKhauGia-ZTE-0001-khong-that"
SESSION_TOKEN_GIA = "48291"
LOGIN_TOKEN_GIA = "tokengia123"

MAY_GIA_1 = {"MACAddress": "AA:BB:CC:00:00:01", "IPAddress": "192.0.2.11",
             "HostName": "may-gia-01", "AccessType": "1", "RSSI": "-57", "ConnectTime": "3720"}
MAY_GIA_2 = {"MACAddress": "aa-bb-cc-00-00-02", "IPAddress": "198.51.100.22",
             "HostName": "may-gia-02", "AccessType": "2", "RSSI": "-63", "ConnectTime": "120"}
MAY_GIA_3 = {"MACAddress": "aabb.cc00.0003", "IPAddress": "192.0.2.13",
             "HostName": "may-gia-03", "AccessType": "9", "RSSI": "khong-so", "ConnectTime": "5"}


def chay(coro):
    return asyncio.run(coro)


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    spec = importlib.util.spec_from_file_location(f"{TEN_GOI}.module", THU_MUC / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{TEN_GOI}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Máy chủ ZTE E2633 GIẢ (HTTPS tự ký) ──────────────────────────────────────

class _TrangThaiGia:
    """Trạng thái của máy chủ giả; test đổi các cờ để dựng từng ca."""

    def __init__(self):
        self.may = [dict(MAY_GIA_1), dict(MAY_GIA_2), dict(MAY_GIA_3)]
        self.che_do_json = "thuong"  # thuong | rong | la
        self.phien = set()
        self.lan_dang_nhap = 0
        self.lan_dang_nhap_loi = 0
        self.khoa_sau_loi = 2  # None = không bao giờ khoá
        self.het_phien_truoc_khi_doc = False
        self.bam_dung = hashlib.sha256((MAT_KHAU_GIA + LOGIN_TOKEN_GIA).encode()).hexdigest()


def _chung_chi_tu_ky(tmp):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ten = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "zte-gia")])
    bay_gio = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(ten).issuer_name(ten)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(bay_gio - timedelta(minutes=5))
            .not_valid_after(bay_gio + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost"),
                 x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    p_cert = tmp / "gia.crt"
    p_key = tmp / "gia.key"
    p_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    p_key.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    return p_cert, p_key, cert.fingerprint(hashes.SHA256()).hex()


class _HandlerGia(BaseHTTPRequestHandler):
    tt: _TrangThaiGia = None  # gán khi dựng máy chủ
    server_version = "ZteGia/1.0"

    def log_message(self, *a):  # im lặng
        pass

    def _gui(self, ma, than: bytes, kieu="text/html; charset=utf-8", cookie=None):
        self.send_response(ma)
        self.send_header("Content-Type", kieu)
        self.send_header("Content-Length", str(len(than)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(than)

    def do_GET(self):
        tt = self.tt
        phan = urllib.parse.urlsplit(self.path)
        if phan.path == "/":
            html = (f'<html><body><form><script>addParameter("_sessionTOKEN", '
                    f'"{SESSION_TOKEN_GIA}")</script></form></body></html>')
            self._gui(200, html.encode())
        elif phan.path == "/function_module/login_module/login_page/logintoken_lua.lua":
            self._gui(200, LOGIN_TOKEN_GIA.encode(), "text/plain")
        elif phan.path == "/getpage.lua":
            qs = urllib.parse.parse_qs(phan.query)
            if qs.get("Action") != ["GetALLClients"]:
                self._gui(404, b"khong co")
                return
            cookie = self.headers.get("Cookie", "")
            phien_hop_le = any(p in cookie for p in tt.phien)
            if tt.het_phien_truoc_khi_doc:
                tt.phien.clear()
                tt.het_phien_truoc_khi_doc = False
                phien_hop_le = False
            if not phien_hop_le:
                # Phiên hết/không có: trả lại trang đăng nhập (không có clientsData)
                self._gui(200, b"<html>login</html>")
                return
            if tt.che_do_json == "rong":
                self._gui(200, json.dumps({"clientsData": []}).encode(), "application/json")
            elif tt.che_do_json == "la":
                du = {"clientsData": [
                    {"MACAddress": "khong-phai-mac", "IPAddress": "192.0.2.99"},
                    "chuoi-chu-khong-phai-doi-tuong",
                    dict(MAY_GIA_1),
                ]}
                self._gui(200, json.dumps(du).encode(), "application/json")
            else:
                self._gui(200, json.dumps({"clientsData": tt.may}).encode(), "application/json")
        else:
            self._gui(404, b"khong co")

    def do_POST(self):
        tt = self.tt
        if self.path != "/":
            self._gui(404, b"khong co")
            return
        dai = int(self.headers.get("Content-Length", 0) or 0)
        than = self.rfile.read(dai)
        if tt.khoa_sau_loi is not None and tt.lan_dang_nhap_loi >= tt.khoa_sau_loi:
            self._gui(200, json.dumps({"bi_khoa": True}).encode(), "application/json")
            return
        truong = urllib.parse.parse_qsl(than.decode("latin1"), keep_blank_values=True)
        ten_truong = [k for k, _ in truong]
        check_gui = self.headers.get("check", "")
        check_dung = hashlib.sha256(than).hexdigest()
        thu_tu_dung = ten_truong == ["Username", "Password", "action", "Frm_Logintoken",
                                     "captchaCode", "_sessionTOKEN"]
        anh_xa = dict(truong)
        hop_le = (thu_tu_dung and check_gui == check_dung
                  and anh_xa.get("Username") == TAI_KHOAN_GIA
                  and anh_xa.get("Password") == tt.bam_dung
                  and anh_xa.get("action") == "login"
                  and anh_xa.get("_sessionTOKEN") == SESSION_TOKEN_GIA)
        if not hop_le:
            tt.lan_dang_nhap_loi += 1
            self._gui(200, "页面过期".encode("utf-8"))
            return
        tt.lan_dang_nhap += 1
        tt.lan_dang_nhap_loi = 0
        ma_phien = "phien-gia-%d" % tt.lan_dang_nhap
        tt.phien.add(ma_phien)
        self._gui(200, b"<html>ok</html>", cookie=f"zte_sess={ma_phien}; Path=/")


@pytest.fixture()
def may_chu_gia(tmp_path):
    tt = _TrangThaiGia()
    p_cert, p_key, van_tay = _chung_chi_tu_ky(tmp_path)
    _HandlerGia.tt = tt
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HandlerGia)
    ngu = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ngu.load_cert_chain(str(p_cert), str(p_key))
    srv.socket = ngu.wrap_socket(srv.socket, server_side=True)
    cong = srv.server_address[1]
    luong = threading.Thread(target=srv.serve_forever, daemon=True)
    luong.start()
    yield {"tt": tt, "dia_chi": f"https://127.0.0.1:{cong}", "van_tay": van_tay}
    srv.shutdown()


@pytest.fixture()
def cho_phep_loopback(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


def _cau_hinh(dia_chi, kiem_chung_chi="false", van_tay=""):
    return {"dia_chi": dia_chi, "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA,
            "kiem_chung_chi_tls": kiem_chung_chi, "van_tay_tls": van_tay}


@pytest.fixture()
def mod():
    return _tai_goi()


@pytest.fixture()
def manifest():
    return json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


# ── Manifest hợp lệ ──────────────────────────────────────────────────────────

def test_manifest_hop_le_va_khai_thiet_bi(manifest):
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "zte-e2633"
    assert manifest["tien_to"] == "zte-e2633"
    assert "thiet_bi" in manifest["cung_cap"]
    assert manifest["tai_nguyen"]["mang_ra"], "phải khai mạng ra"


# ── Luồng đăng nhập đúng: thứ tự trường + header check ───────────────────────

def test_dang_nhap_dung_tra_ve_thuc_the_c1(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert len(ds) == 3
    e1 = ds[0]
    assert e1["id"] == "zte-e2633.aabbcc000001"
    assert e1["ten"] == "may-gia-01"
    assert e1["loai"] == "may_mang"
    assert e1["thuoc_tinh"] == {"mac": "aa:bb:cc:00:00:01", "ip": "192.0.2.11",
                                "ten_may_tu_khai": "may-gia-01", "bang_tan": "2.4G",
                                "rssi": -57, "nguon": "wifi"}
    assert e1["rui_ro_mac_dinh"] == "doc"
    assert e1["tinh_nang"] == []
    e2 = ds[1]
    assert e2["id"] == "zte-e2633.aabbcc000002"  # MAC dạng aa-bb-cc… vẫn chuẩn hoá
    assert e2["thuoc_tinh"]["bang_tan"] == "5G"
    e3 = ds[2]
    assert e3["thuoc_tinh"]["bang_tan"] is None  # AccessType lạ → null, không sập
    assert e3["thuoc_tinh"]["rssi"] is None  # RSSI không số → null
    # id duy nhất
    assert len({e["id"] for e in ds}) == 3


def test_chi_dang_nhap_mot_lan_moi_chu_ky(mod, manifest, may_chu_gia, cho_phep_loopback):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        chay(m.thuc_the(None))
        chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert tt.lan_dang_nhap == 1, "phiên đã cache thì không đăng nhập lại"


def test_phien_het_han_dang_nhap_lai_dung_mot_lan(mod, manifest, may_chu_gia, cho_phep_loopback):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert len(chay(m.thuc_the(None))) == 3
        tt.het_phien_truoc_khi_doc = True  # máy chủ giả huỷ phiên
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert len(ds) == 3
    assert tt.lan_dang_nhap == 2, "hết phiên thì đăng nhập lại đúng một lần"


# ── Sai thứ tự / sai check / sai mật khẩu ────────────────────────────────────

def test_sai_mat_khau_dang_nhap_that_bai_khong_sap(mod, manifest, may_chu_gia,
                                                  cho_phep_loopback, caplog):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"])
    ch["mat_khau"] = "SaiMatKhau-Gia-0002"
    m.nap(ch)
    try:
        with caplog.at_level(logging.DEBUG, logger="web2api.zte_e2633"):
            ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert ds == []
    assert "SaiMatKhau-Gia-0002" not in caplog.text, "mật khẩu rò vào log"
    assert MAT_KHAU_GIA not in caplog.text


def test_loi_lap_lien_tuc_thi_dung_khong_thu_lai(mod, manifest, may_chu_gia, cho_phep_loopback):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"])
    ch["mat_khau"] = "SaiMatKhau-Gia-0003"
    m.nap(ch)
    try:
        for _ in range(6):
            chay(m.thuc_the(None))
        lan_loi = tt.lan_dang_nhap_loi
        chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    # Sau ngưỡng lỗi lặp, module DỪNG hẳn (không gửi thêm lần đăng nhập nào tới thiết bị)
    assert tt.lan_dang_nhap_loi == lan_loi


def test_tin_hieu_khoa_cua_thiet_bi_thi_dung(mod, manifest, may_chu_gia, cho_phep_loopback):
    tt = may_chu_gia["tt"]
    tt.khoa_sau_loi = 1  # máy chủ giả khoá sau 1 lần sai
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"])
    ch["mat_khau"] = "SaiMatKhau-Gia-0004"
    m.nap(ch)
    try:
        chay(m.thuc_the(None))  # lần 1: sai
        truoc = tt.lan_dang_nhap_loi
        chay(m.thuc_the(None))  # lần 2: thiết bị báo khoá → module dừng
        chay(m.thuc_the(None))  # lần 3: không được gửi thêm
    finally:
        chay(m.dung())
    assert tt.lan_dang_nhap_loi == truoc, "đã khoá thì không thử đăng nhập nữa"


# ── Dữ liệu lạ: rỗng, sai khuôn, tên có ký tự điều khiển ─────────────────────

def test_danh_sach_rong_tra_ve_rong(mod, manifest, may_chu_gia, cho_phep_loopback):
    may_chu_gia["tt"].che_do_json = "rong"
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert chay(m.thuc_the(None)) == []
    finally:
        chay(m.dung())


def test_json_la_bo_qua_muc_sai_giu_muc_dung(mod, manifest, may_chu_gia, cho_phep_loopback):
    may_chu_gia["tt"].che_do_json = "la"
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert [e["id"] for e in ds] == ["zte-e2633.aabbcc000001"]


def test_ten_chua_ky_tu_dieu_khien_duoc_lam_sach(mod, manifest, may_chu_gia, cho_phep_loopback):
    may_chu_gia["tt"].may = [dict(MAY_GIA_1, HostName="may\x00gia\x1f-01\n")]
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert ds[0]["ten"] == "maygia-01"
    assert ds[0]["thuoc_tinh"]["ten_may_tu_khai"] == "maygia-01"


# ── thuc_hien luôn từ chối; trang_thai có mặt/không ──────────────────────────

def test_thuc_hien_luon_tu_choi(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        kq = chay(m.thuc_hien("zte-e2633.aabbcc000001", "bat", {}))
    finally:
        chay(m.dung())
    assert kq["ok"] is False
    assert kq.get("loi")


def test_trang_thai_co_mat_va_khong(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        tt = chay(m.trang_thai(["zte-e2633.aabbcc000001", "zte-e2633.ffffffffffff"]))
    finally:
        chay(m.dung())
    assert tt["zte-e2633.aabbcc000001"]["gia_tri"] == "co"
    assert tt["zte-e2633.aabbcc000001"]["hop_le"] is True
    assert tt["zte-e2633.ffffffffffff"]["gia_tri"] == "khong"


# ── Chưa cấu hình: lõi vẫn chạy ──────────────────────────────────────────────

def test_chua_cau_hinh_loi_chay_binh_thuong(mod, manifest):
    m = mod.Module(manifest)
    m.nap({})
    assert chay(m.thuc_the(None)) == []
    tt = chay(m.trang_thai(["zte-e2633.aabbcc000001"]))
    assert tt["zte-e2633.aabbcc000001"]["gia_tri"] == "khong"
    assert chay(m.thuc_hien("zte-e2633.x", "bat", {}))["ok"] is False
    sk = chay(m.suc_khoe())
    assert sk["ok"] is False


# ── cai: kiểm SSRF, trường bắt buộc, thử đăng nhập thật ─────────────────────

def test_cai_thieu_dia_chi_bao_loi(mod, manifest):
    m = mod.Module(manifest)
    kq = chay(m.cai({"tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA}))
    assert kq["ok"] is False and kq["loi"]


def test_cai_chan_ssrf_metadata_dam_may(mod, manifest):
    m = mod.Module(manifest)
    kq = chay(m.cai({"dia_chi": "http://169.254.169.254/", "tai_khoan": TAI_KHOAN_GIA,
                     "mat_khau": MAT_KHAU_GIA}))
    assert kq["ok"] is False
    assert "chặn" in kq["loi"]


def test_cai_thanh_cong_voi_may_chu_gia(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    kq = chay(m.cai(_cau_hinh(may_chu_gia["dia_chi"])))
    assert kq["ok"] is True, kq.get("loi")
    assert kq["loi"] is None
    assert kq["cau_hinh_luu"]["dia_chi"] == may_chu_gia["dia_chi"]
    assert MAT_KHAU_GIA not in json.dumps(kq["ket_qua_tung_khoa"])


def test_cai_sai_mat_khau_bao_loi_khong_ro_mat_khau(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"])
    ch["mat_khau"] = "SaiMatKhau-Gia-0005"
    kq = chay(m.cai(ch))
    assert kq["ok"] is False
    assert "SaiMatKhau-Gia-0005" not in (kq["loi"] or "")


def test_cai_van_tay_sai_dinh_dang_bao_loi(mod, manifest):
    m = mod.Module(manifest)
    ch = {"dia_chi": "https://192.0.2.1/", "tai_khoan": TAI_KHOAN_GIA,
          "mat_khau": MAT_KHAU_GIA, "van_tay_tls": "khong-phai-hex"}
    kq = chay(m.cai(ch))
    assert kq["ok"] is False and "van_tay_tls" in (kq["loi"] or "")


# ── TLS: mặc định an toàn, tự ký phải tắt kiểm hoặc ghim vân tay ─────────────

def test_mac_dinh_kiem_chung_chi_tu_ky_that_bai_an_toan(mod, manifest, may_chu_gia,
                                                       cho_phep_loopback):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"], kiem_chung_chi="true")
    m.nap(ch)
    try:
        assert chay(m.thuc_the(None)) == []  # lỗi TLS → rỗng, không sập
    finally:
        chay(m.dung())


def test_ghim_van_tay_dung_thi_thanh_cong(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"], kiem_chung_chi="true",
                    van_tay=may_chu_gia["van_tay"]))
    try:
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert len(ds) == 3


def test_ghim_van_tay_sai_thi_that_bai(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"], kiem_chung_chi="true",
                    van_tay="ab" * 32))
    try:
        assert chay(m.thuc_the(None)) == []
    finally:
        chay(m.dung())


def test_khong_dung_proxy_moi_truong(mod, manifest, may_chu_gia, cho_phep_loopback, monkeypatch):
    # Dù môi trường đặt proxy chết, module vẫn nối thẳng tới thiết bị trong LAN.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    m = mod.Module(manifest)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        ds = chay(m.thuc_the(None))
    finally:
        chay(m.dung())
    assert len(ds) == 3
