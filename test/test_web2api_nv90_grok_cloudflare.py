"""Test NV90 cho module grok-web: nhận ra thử thách Cloudflare → «cần người».

Theo đúng spec NV90 gốc (file nv90-ke-hoach.txt của chủ máy THẮNG mọi bản
trước): grok-web NHẬN RA thử thách bằng CẤU TRÚC (HTTP 403/503 kèm tiêu đề
`cf-mitigated: challenge`, hoặc tín hiệu trình duyệt `thu_thach_truy_cap`) rồi
báo `can_nguoi` NGAY — TUYỆT ĐỐI KHÔNG viết code tự tích ô / tự giải / né
Cloudflare dưới mọi hình thức (không click tọa độ, không dịch vụ giải hộ,
không che dấu vết). Người tích ô xong bấm «Đã xử lý»
(`POST /api/tai-khoan/khoi-phuc/xoa-can-nguoi`) thì lượt kế chạy tiếp nửa tự
làm được.

- Trình duyệt là GIẢ (TrinhDuyetGia của app.khoi_phuc_phien). Máy chủ HTTP là
  GIẢ (http.server trên 127.0.0.1 + WEB2API_TEST_CHO_PHEP_LOOPBACK=1).
  Không Internet, không Chrome.
- Thử thách KHÔNG làm tăng bộ đếm «hỏng liên tiếp» (người xử lý được thì không
  phải lỗi tài khoản).
- Chế độ dán cookie cho phép dán cả cookie `cf_*` (cf_clearance); 403 thử thách
  hiện thành «cần người», không thành «cookie sai».

Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_nv90_mod_test"

SSO_GIA_OK = "sso-gia-nv90-ok"
SSO_GIA_CHALLENGE = "sso-gia-nv90-challenge"
UID_GIA = "uid-gia-nv90"
MAT_KHAU_GIA = "mat-khau-gia-nv90-001"
EMAIL_GIA = "ban-gia-nv90@example.com"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


# ───────────── máy chủ HTTP giả ─────────────

def _lay_sso(headers) -> str:
    for muc in (headers.get("Cookie") or "").split(";"):
        k, _, v = muc.partition("=")
        if k.strip() == "sso":
            return v.strip()
    return ""


class _XuLyPhien(BaseHTTPRequestHandler):
    def _tra(self, ma: int, body: bytes, tieu_de: dict | None = None,
             kieu: str = "text/plain"):
        self.send_response(ma)
        self.send_header("Content-Type", kieu)
        for k, v in (tieu_de or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.path.rstrip("/").endswith("/api/auth/session"):
            self._tra(404, b"khong co")
            return
        sso = _lay_sso(self.headers)
        if sso == SSO_GIA_CHALLENGE:
            # Thử thách Cloudflare: 403 + cf-mitigated: challenge (cấu trúc).
            self._tra(403, b"challenge", {"cf-mitigated": "challenge"})
            return
        if sso == SSO_GIA_OK:
            body = json.dumps({"session": {"userId": UID_GIA}}).encode("utf-8")
            self._tra(200, body, kieu="application/json")
            return
        self._tra(401, b"het han")

    def log_message(self, *args):
        pass


@pytest.fixture()
def may_chu_phien(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    may_chu = ThreadingHTTPServer(("127.0.0.1", 0), _XuLyPhien)
    luong = threading.Thread(target=may_chu.serve_forever, daemon=True)
    luong.start()
    yield f"http://127.0.0.1:{may_chu.server_address[1]}"
    may_chu.shutdown()
    luong.join(10)


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    md = Module(dict(MANIFEST))
    md.kho = kho.kho_rieng("grok-web")
    return md


# ───────────── nhận ra thử thách bằng cấu trúc ─────────────

def test_http_403_kem_cf_mitigated_challenge_thi_can_nguoi(mod, may_chu_phien):
    """Chế độ dán cookie: 403 thử thách → «cần người», KHÔNG phải «cookie sai»."""
    mod._goc_https = may_chu_phien
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(f"sso={SSO_GIA_CHALLENGE}; cf_clearance=gia-001"))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.cookie.da_xoa()


def test_403_khong_tieu_de_thu_thach_thi_khong_phai_can_nguoi(mod, may_chu_phien):
    """403 thường (cookie hết hạn, không có cf-mitigated) → lỗi phiên, không
    phải can_nguoi — phân biệt bằng cấu trúc, không đoán."""
    mod._goc_https = may_chu_phien
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam("sso=het-han-khong-co"))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi != "can_nguoi"


def test_lam_moi_phien_gap_thu_thach_thi_can_nguoi(mod, may_chu_phien):
    mod._goc_https = may_chu_phien
    mod._tai_khoan = [{"sso": SSO_GIA_CHALLENGE}]
    mod._dt_id = [None]
    khoa = mod._khoa_tu_jar({"sso": SSO_GIA_CHALLENGE})
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong
    assert kq.tang == "a" and kq.ma_loi == "can_nguoi" and kq.can_nguoi


def test_trinh_duyet_gap_tin_hieu_thu_thach_thi_can_nguoi_ngay(mod):
    """Trình duyệt báo tín hiệu thu_thach_truy_cap → dừng NGAY, không cố vượt."""
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["thu_thach_truy_cap"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()
    assert mod._tai_khoan == []
    # Không hề thử điền form hay bấm gì sau khi thấy thử thách.
    assert all(hd[0] == "mo_trang" for hd in td.hanh_dong)


def test_cho_phep_dan_cookie_cf(mod):
    """Chế độ dán cookie cho phép dán cả cookie cf_* (cf_clearance)."""
    loi = mod._nap_tu({"tai_khoan": ["sso=abc; cf_clearance=xyz"]})
    assert loi is None
    assert mod._tai_khoan[0]["cf_clearance"] == "xyz"


# ───────────── sau xoa-can-nguoi chạy tiếp ─────────────

def test_sau_xoa_can_nguoi_thi_chay_tiep_duoc(mod):
    """Người tích ô xong và bấm «Đã xử lý» → lượt kế chạy nửa tự làm được
    (điền email/mật khẩu, đọc cookie phiên, kiểm phiên bằng cấu trúc)."""
    from app import khoi_phuc_phien as kpp
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    kpp.dat_can_nguoi("grok-web", "khoa-gia", "thu_thach", "huong dan")
    assert kpp.lay_trang_thai("grok-web", "khoa-gia")["can_nguoi"] is True
    kpp.xoa_can_nguoi("grok-web", "khoa-gia")
    tt = kpp.lay_trang_thai("grok-web", "khoa-gia")
    assert tt["can_nguoi"] is False and tt["hong_b_lien_tiep"] == 0

    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = TaiKhoanCanKhoiPhuc(khoa="khoa-gia", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, td))
    assert kq.thanh_cong and kq.tang == "b", kq.ma_loi
    assert tk.mat_khau.da_xoa()


def test_thu_thach_khong_tang_bo_dem_hong_lien_tiep(tmp_path, monkeypatch):
    """Thử thách → can_nguoi ở tầng (b) KHÔNG làm tăng bộ đếm «hỏng liên tiếp»
    (người xử lý được thì không phải lỗi tài khoản) → không bị ngừng tự đăng
    nhập oan."""
    from app import khoi_phuc_phien as kpp
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data2"))

    class ModGia:
        manifest = {"id": "grok-web"}

        async def lam_moi_phien(self, khoa):
            return kpp.KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han")

        async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
            return kpp.KetQuaKhoiPhuc(False, tang="b", ma_loi="can_nguoi",
                                      can_nguoi=True)

        def tai_khoan_ke(self, khoa):
            return None

        def danh_dau_hong(self, khoa, ma_loi):
            pass

        def mat_khau_da_luu(self, khoa):
            return None

        def ho_so_trinh_duyet(self, khoa):
            return "grok-web-gia"

    tk = kpp.TaiKhoanCanKhoiPhuc(khoa="khoa-gia", email=EMAIL_GIA,
                                 mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(kpp.khoi_phuc(ModGia(), tk, trinh_duyet=None,
                            mo_novnc=lambda *a: None))
    assert kq.can_nguoi and kq.ma_loi == "can_nguoi"
    tt = kpp.lay_trang_thai("grok-web", "khoa-gia")
    assert tt["hong_b_lien_tiep"] == 0
    assert tt["ngung_tu_dang_nhap"] is False


# ───────────── bí mật không lộ ─────────────

def test_mat_khau_va_cookie_khong_vao_log(mod, caplog):
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    with caplog.at_level(logging.INFO, logger="web2api.module.grok_web"):
        kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log
    assert SSO_GIA_OK not in ban_log
