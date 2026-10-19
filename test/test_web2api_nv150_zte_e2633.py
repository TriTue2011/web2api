"""Test NV150 — module `zte-e2633`: tách «lưu cấu hình» khỏi «kiểm tra kết nối»,
phân loại lỗi, một phiên.

Test-first: chạy ĐỎ trước khi sửa module. Mọi máy chủ đều GIẢ cục bộ (127.0.0.1,
TLS tự ký do test tự tạo); không có mạng thật, không có dữ liệu thật của nhà chủ kho
(MAC/IP/tên máy/mật khẩu đều GIẢ rõ ràng).

Hành vi mới (hợp đồng NV150):
1. `cai()`: kiểm biên (SSRF/trường) rồi LƯU cấu hình NGAY, không phụ thuộc đăng nhập
   thử. Đăng nhập thử chỉ sinh CẢNH BÁO có cấu trúc (`ok: True` + `canh_bao`/`ma_loi`
   trong từng mục `ket_qua_tung_khoa`).
2. `suc_khoe()`: trả «chưa kết nối được: <lý do>» thay vì lỗi; không đăng nhập lại
   mỗi lần gọi (tối đa 1 lần mỗi N phút, mặc định 10, cấu hình được).
3. Một phiên: hết phiên đăng nhập lại đúng 1 lần; `dung()` quên phiên phía client;
   không gửi yêu cầu đăng xuất. Đọc hỏng = LỖI, giữ dữ liệu cũ, không trả «0 máy».
   Trang lạ/token rỗng KHÔNG tạm ngừng dài; bị từ chối mới tạm ngừng dài (30 phút)
   rồi hồi phục (đồng hồ giả trong test).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import logging
import shutil
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
TEN_GOI = "zte_e2633_nv150_mod_test"

# ── Dữ liệu GIẢ rõ ràng ──────────────────────────────────────────────────────
TAI_KHOAN_GIA = "admin"
MAT_KHAU_GIA = "MatKhauGia-ZTE-150-khong-that"
SESSION_TOKEN_GIA = "48291"
LOGIN_TOKEN_GIA = "tokengia150"

MAY_GIA_1 = {"MACAddress": "AA:BB:CC:00:00:01", "IPAddress": "192.0.2.11",
             "HostName": "may-gia-01", "AccessType": "1", "RSSI": "-57"}
MAY_GIA_2 = {"MACAddress": "aa-bb-cc-00-00-02", "IPAddress": "198.51.100.22",
             "HostName": "may-gia-02", "AccessType": "2", "RSSI": "-63"}


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


class DongHoGia:
    """Đồng hồ giả cho test tạm ngừng/hồi phục."""

    def __init__(self):
        self.bay_gio = datetime.now(timezone.utc)

    def __call__(self):
        return self.bay_gio

    def nhich(self, phut):
        self.bay_gio += timedelta(minutes=phut)


# ── Máy chủ ZTE E2633 GIẢ ────────────────────────────────────────────────────

class _TrangThaiGia:
    def __init__(self):
        self.may = [dict(MAY_GIA_1), dict(MAY_GIA_2)]
        self.che_do_json = "thuong"  # thuong | rong | hong
        self.phien = set()
        self.lan_dang_nhap = 0
        self.lan_dang_nhap_loi = 0
        self.lan_get_du_lieu = 0
        self.het_phien_truoc_khi_doc = False
        self.trang_dang_nhap_la = False  # GET / trả trang lạ (không có _sessionTOKEN)
        self.token_rong = False          # token đăng nhập rỗng
        self.tu_choi_dang_nhap = False   # POST luôn trả «đã quá hạn» dù đúng mật khẩu
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
    tt: _TrangThaiGia = None
    server_version = "ZteGia/1.0"

    def log_message(self, *a):
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
            if tt.trang_dang_nhap_la:
                self._gui(200, b"<html><body>trang la khong co token</body></html>")
            else:
                html = (f'<html><body><form><script>addParameter("_sessionTOKEN", '
                        f'"{SESSION_TOKEN_GIA}")</script></form></body></html>')
                self._gui(200, html.encode())
        elif phan.path == "/function_module/login_module/login_page/logintoken_lua.lua":
            self._gui(200, b"" if tt.token_rong else LOGIN_TOKEN_GIA.encode(), "text/plain")
        elif phan.path == "/getpage.lua":
            tt.lan_get_du_lieu += 1
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
                self._gui(200, b"<html>login</html>")
                return
            if tt.che_do_json == "rong":
                self._gui(200, json.dumps({"clientsData": []}).encode(), "application/json")
            elif tt.che_do_json == "hong":
                # JSON parse được nhưng SAI KHUÔN: thiếu khoá clientsData
                self._gui(200, json.dumps({"danh_sach": tt.may}).encode(),
                          "application/json")
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
        truong = urllib.parse.parse_qsl(than.decode("latin1"), keep_blank_values=True)
        ten_truong = [k for k, _ in truong]
        check_gui = self.headers.get("check", "")
        check_dung = hashlib.sha256(than).hexdigest()
        thu_tu_dung = ten_truong == ["Username", "Password", "action", "Frm_Logintoken",
                                     "captchaCode", "_sessionTOKEN"]
        anh_xa = dict(truong)
        hop_le = (not tt.tu_choi_dang_nhap and thu_tu_dung and check_gui == check_dung
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


@pytest.fixture()
def dong_ho():
    return DongHoGia()


def _cau_hinh(dia_chi, kiem_chung_chi="false", van_tay="", mat_khau=MAT_KHAU_GIA):
    return {"dia_chi": dia_chi, "tai_khoan": TAI_KHOAN_GIA, "mat_khau": mat_khau,
            "kiem_chung_chi_tls": kiem_chung_chi, "van_tay_tls": van_tay}


@pytest.fixture()
def mod():
    return _tai_goi()


@pytest.fixture()
def manifest():
    return json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def _module(mod, manifest, dong_ho=None):
    return mod.Module(manifest, dong_ho=dong_ho) if dong_ho else mod.Module(manifest)


# ── HỢP ĐỒNG 1: cai() lưu cấu hình ngay, đăng nhập thử chỉ là cảnh báo ────────

def test_cai_tu_choi_van_luu_va_canh_bao_co_ma_loi(mod, manifest, may_chu_gia,
                                                  cho_phep_loopback, caplog):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"], mat_khau="SaiMatKhau-Gia-150")
    with caplog.at_level(logging.DEBUG, logger="web2api.zte_e2633"):
        kq = chay(m.cai(ch))
    assert kq["ok"] is True, "đăng nhập thử lỗi vẫn phải lưu cấu hình"
    assert kq["loi"] is None
    luu = kq["cau_hinh_luu"]
    assert luu["dia_chi"] == may_chu_gia["dia_chi"]
    assert luu["tai_khoan"] == TAI_KHOAN_GIA
    assert luu["mat_khau"] == "SaiMatKhau-Gia-150"
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] == "MAT_KHAU_BI_TU_CHOI"
    assert muc["canh_bao"] and "một phiên" in muc["canh_bao"]
    assert "SaiMatKhau-Gia-150" not in json.dumps(kq["ket_qua_tung_khoa"])
    assert "SaiMatKhau-Gia-150" not in caplog.text


def test_cai_tls_tu_ky_van_luu_va_huong_dan_ngay_trong_thong_bao(
        mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"], kiem_chung_chi="true")  # chứng chỉ tự ký
    kq = chay(m.cai(ch))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["dia_chi"] == may_chu_gia["dia_chi"]
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] == "TLS_TU_KY"
    assert "kiem_chung_chi_tls=false" in muc["canh_bao"]


def test_cai_mat_mang_van_luu_va_ma_loi_ket_noi(mod, manifest, cho_phep_loopback):
    m = mod.Module(manifest)
    ch = _cau_hinh("https://127.0.0.1:9")  # cổng discard: không tới được
    kq = chay(m.cai(ch))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["dia_chi"] == "https://127.0.0.1:9"
    assert kq["ket_qua_tung_khoa"][0]["ma_loi"] == "KHONG_TOI_DUOC"


def test_cai_ssrf_khong_luu(mod, manifest):
    m = mod.Module(manifest)
    kq = chay(m.cai({"dia_chi": "http://169.254.169.254/", "tai_khoan": TAI_KHOAN_GIA,
                     "mat_khau": MAT_KHAU_GIA}))
    assert kq["ok"] is False
    assert "cau_hinh_luu" not in kq


def test_cai_thieu_truong_khong_luu(mod, manifest):
    m = mod.Module(manifest)
    kq = chay(m.cai({"dia_chi": "https://192.0.2.1"}))
    assert kq["ok"] is False
    assert "cau_hinh_luu" not in kq


def test_cai_thanh_cong_khong_canh_bao(mod, manifest, may_chu_gia, cho_phep_loopback):
    m = mod.Module(manifest)
    kq = chay(m.cai(_cau_hinh(may_chu_gia["dia_chi"])))
    assert kq["ok"] is True
    muc = kq["ket_qua_tung_khoa"][0]
    assert muc["ma_loi"] is None and muc["canh_bao"] is None
    assert muc["so_may"] == 2


# ── HỢP ĐỒNG 2: suc_khoe không đăng nhập lại mỗi lần ──────────────────────────

def test_suc_khoe_khong_dang_nhap_lai_moi_lan(mod, manifest, may_chu_gia,
                                             cho_phep_loopback, dong_ho):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        for _ in range(3):
            sk = chay(m.suc_khoe())
            assert sk["ok"] is True and sk["so_may"] == 2
    finally:
        chay(m.dung())
    assert tt.lan_dang_nhap == 1, "3 lần suc_khoe chỉ được đăng nhập 1 lần"


def test_suc_khoe_het_phien_dang_nhap_lai_dung_mot_lan(mod, manifest, may_chu_gia,
                                                      cho_phep_loopback, dong_ho):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert chay(m.suc_khoe())["ok"] is True
        tt.het_phien_truoc_khi_doc = True
        dong_ho.nhich(11)  # qua ngưỡng đăng nhập lại
        sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert sk["ok"] is True and sk["so_may"] == 2
    assert tt.lan_dang_nhap == 2, "hết phiên thì đăng nhập lại đúng một lần"


def test_suc_khoe_chua_cau_hinh_bao_chua_ket_noi_duoc(mod, manifest):
    m = mod.Module(manifest)
    m.nap({})
    sk = chay(m.suc_khoe())
    assert sk["ok"] is False
    assert "chưa kết nối được" in sk["loi"]


# ── HỢP ĐỒNG 3: đọc hỏng là lỗi, giữ dữ liệu cũ ───────────────────────────────

def test_doc_hong_giu_du_lieu_cu_khong_tra_0_may(mod, manifest, may_chu_gia,
                                                cho_phep_loopback, dong_ho):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert len(chay(m.thuc_the(None))) == 2
        tt.che_do_json = "hong"  # JSON thiếu clientsData: sai khuôn
        ds = chay(m.thuc_the(None))
        assert [e["id"] for e in ds] == ["zte-e2633.aabbcc000001",
                                         "zte-e2633.aabbcc000002"], \
            "đọc hỏng phải giữ dữ liệu cũ, không trả rỗng"
        dong_ho.nhich(11)  # qua cả hạn đọc cũ (5') lẫn ngưỡng đăng nhập lại (10')
        sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert sk["ok"] is False, "đọc hỏng là lỗi, không phải «không có máy»"
    assert sk["so_may"] == 2, "giữ số máy lần đọc trước, không trả 0"
    assert "sai khuôn" in sk["loi"] or "đọc hỏng" in sk["loi"]


def test_trang_la_token_rong_khong_tam_ngung_dai(mod, manifest, may_chu_gia,
                                                cho_phep_loopback, dong_ho):
    tt = may_chu_gia["tt"]
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        for co in ("trang_dang_nhap_la", "token_rong"):
            chay(m.dung())  # quên phiên → lần đọc tới bắt buộc đăng nhập lại
            setattr(tt, co, True)
            assert chay(m.thuc_the(None)) == []
            setattr(tt, co, False)
            dong_ho.nhich(1)  # chỉ 1 phút sau: nếu bị tạm ngừng dài thì vẫn chặn
            assert len(chay(m.thuc_the(None))) == 2, \
                f"{co}: trang lạ/token rỗng không được tạm ngừng dài"
    finally:
        chay(m.dung())


def test_bi_tu_choi_tam_ngung_roi_hoi_phuc(mod, manifest, may_chu_gia,
                                           cho_phep_loopback, dong_ho):
    tt = may_chu_gia["tt"]
    tt.tu_choi_dang_nhap = True  # bộ phát từ chối mọi lần đăng nhập
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert chay(m.thuc_the(None)) == []
        loi_truoc = tt.lan_dang_nhap_loi
        dong_ho.nhich(5)
        assert chay(m.thuc_the(None)) == []
        assert tt.lan_dang_nhap_loi == loi_truoc, "đang tạm ngừng: không gửi thêm"
        sk = chay(m.suc_khoe())
        assert sk["ok"] is False and "chưa kết nối được" in sk["loi"]
        # Hồi phục: hết 30 phút tạm ngừng, bộ phát chấp nhận lại
        tt.tu_choi_dang_nhap = False
        dong_ho.nhich(31)
        assert len(chay(m.thuc_the(None))) == 2
    finally:
        chay(m.dung())


def test_mat_khau_khong_vao_log_phan_hoi_suc_khoe(mod, manifest, may_chu_gia,
                                                 cho_phep_loopback, caplog):
    m = mod.Module(manifest)
    ch = _cau_hinh(may_chu_gia["dia_chi"], mat_khau="SaiMatKhau-Gia-151")
    m.nap(ch)
    try:
        with caplog.at_level(logging.DEBUG, logger="web2api.zte_e2633"):
            kq = chay(m.cai(ch))
            sk = chay(m.suc_khoe())
    finally:
        chay(m.dung())
    assert "SaiMatKhau-Gia-151" not in caplog.text
    assert "SaiMatKhau-Gia-151" not in json.dumps(kq["ket_qua_tung_khoa"])
    assert "SaiMatKhau-Gia-151" not in json.dumps(sk)


# ── Tích hợp qua SoModule: cài → lưu → nạp lại → suc-khoe ────────────────────

def test_cai_qua_so_module_luu_nap_lai_suc_khoe(tmp_path, monkeypatch, may_chu_gia):
    """(a) cai() qua SoModule với bộ phát giả từ chối mật khẩu vẫn ok + lưu cấu hình;
    (b) mật khẩu vào bi_mat/ (0600), không vào modules.json;
    (c) khởi động lại SoModule: cấu hình nạp lại, suc_khoe hoạt động với bộ phát giả."""
    import asyncio
    from app import kho, so_module
    from app.so_module import SoModule

    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    mod_dir = tmp_path / "modules" / "zte-e2633"
    shutil.copytree(THU_MUC, mod_dir, ignore=shutil.ignore_patterns("__pycache__"))

    so = SoModule()
    asyncio.run(so.khoi_dong())
    assert "zte-e2633" in so.catalog

    # SSRF vẫn bị chặn
    kq_ssrf = asyncio.run(so.cai("zte-e2633", {
        "dia_chi": "http://169.254.169.254/", "tai_khoan": TAI_KHOAN_GIA,
        "mat_khau": MAT_KHAU_GIA}))
    assert kq_ssrf["ok"] is False

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    # (a) mật khẩu SAI nhưng vẫn lưu (cảnh báo có cấu trúc)
    kq = asyncio.run(so.cai("zte-e2633", {
        "dia_chi": may_chu_gia["dia_chi"], "tai_khoan": TAI_KHOAN_GIA,
        "mat_khau": "SaiMatKhau-Gia-152", "kiem_chung_chi_tls": "false"}))
    assert kq["ok"] is True, kq
    assert kq["ket_qua_tung_khoa"][0]["ma_loi"] == "MAT_KHAU_BI_TU_CHOI"
    assert "SaiMatKhau-Gia-152" not in json.dumps(kq)

    # (b) đĩa: modules.json không có mật khẩu; bi_mat/ có, quyền 0600
    tep_modules = tmp_path / "modules.json"
    dl = json.loads(tep_modules.read_text(encoding="utf-8"))
    muc = next(m for m in dl["modules"] if m["id"] == "zte-e2633")
    assert "SaiMatKhau-Gia-152" not in json.dumps(muc["cau_hinh_cong_khai"])
    tep_bm = tmp_path / "bi_mat" / "zte-e2633.json"
    assert tep_bm.exists()
    assert oct(tep_bm.stat().st_mode & 0o777) == "0o600"
    assert kho.doc_bi_mat("zte-e2633").get("mat_khau") == "SaiMatKhau-Gia-152"

    # Cài lại với mật khẩu ĐÚNG → hết cảnh báo, cấu hình trên đĩa được cập nhật
    kq2 = asyncio.run(so.cai("zte-e2633", {
        "dia_chi": may_chu_gia["dia_chi"], "tai_khoan": TAI_KHOAN_GIA,
        "mat_khau": MAT_KHAU_GIA, "kiem_chung_chi_tls": "false"}))
    assert kq2["ok"] is True
    assert kq2["ket_qua_tung_khoa"][0]["ma_loi"] is None
    assert kho.doc_bi_mat("zte-e2633").get("mat_khau") == MAT_KHAU_GIA

    # (c) khởi động lại: nạp lại cấu hình, suc_khoe chạy với bộ phát giả
    so_moi = SoModule()
    asyncio.run(so_moi.khoi_dong())
    the = so_moi.dang_chay["zte-e2633"]
    sk = asyncio.run(the.suc_khoe())
    assert sk["ok"] is True and sk["so_may"] == 2, sk


def test_phien_ban_02_va_mo_ta_du_phong(manifest):
    assert manifest["phien_ban"] == "0.2.0"
    mo_ta = manifest["mo_ta"]
    assert "DỰ PHÒNG" in mo_ta or "dự phòng" in mo_ta
    assert "một phiên" in mo_ta
    khoa = {t["khoa"] for t in manifest["truong_cau_hinh"]}
    assert "khoang_cach_dang_nhap_phut" in khoa
