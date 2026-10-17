"""Test NV90 cho module grok-web: nhận ra thử thách Cloudflare → «cần người»,
và patch bổ sung (lệnh trực tiếp của chủ máy): cố vượt thử thách tối đa 3 lượt
trước khi bó tay.

Theo spec NV90 gốc (file nv90-ke-hoach.txt THẮNG mọi bản trước): grok-web NHẬN RA
thử thách bằng CẤU TRÚC (HTTP 403/503 kèm tiêu đề `cf-mitigated: challenge`,
hoặc tín hiệu trình duyệt `thu_thach_truy_cap`) rồi báo `can_nguoi`.

Patch bổ sung (lệnh trực tiếp của chủ máy, 17/10): bước đăng nhập bằng trình
duyệt CỐ GẮNG VƯỢT ô Cloudflare Turnstile tối đa 3 lượt
(`trinh_duyet.vuot_thu_thach()` trong hợp đồng `app/khoi_phuc_phien.py` — mã mở,
lõi không biết tên nhà cung cấp); sau mỗi lượt đều đọc lại tín hiệu để chắc,
không bao giờ coi việc gọi hàm là đã vượt. Vượt không được sau 3 lượt →
`can_nguoi` như NV90 (người tích ô tay qua noVNC, rồi xoa-can-nguoi để chạy
tiếp). Hai chế độ đăng nhập (cấu hình `che_do_dang_nhap`, mặc định
`truc_tiep`): `truc_tiep` — email + mật khẩu trực tiếp trên x.com (như OpenAI);
`google` — SSO Google, phiên Google được lưu để lần sau chỉ bấm «tiếp tục với
Google». TRUNG THỰC: c2a đo 3 cách click đều KHÔNG ăn trên grok.com thật →
bước vượt này chỉ kiểm bằng trình duyệt GIẢ; CHƯA KIỂM trên grok.com thật.

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


def test_trinh_duyet_gap_tin_hieu_thu_thach_thi_thu_vuot_roi_can_nguoi(mod):
    """Trình duyệt báo tín hiệu thu_thach_truy_cap → cố vượt tối đa 3 lượt;
    không qua được → can_nguoi (giữ luồng NV90), không điền form.

    (Patch bổ sung theo lệnh chủ máy: thử vượt trước khi bó tay.)"""
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["thu_thach_truy_cap"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()
    assert mod._tai_khoan == []
    # Đã cố vượt đúng 3 lượt, không hề điền form hay bấm đăng nhập.
    assert sum(1 for hd in td.hanh_dong if hd[0] == "vuot_thu_thach") == 3
    assert not any(hd[0] == "dien" for hd in td.hanh_dong)


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


# ───────────── patch bổ sung: cố vượt thử thách (tối đa 3 lượt) ─────────────
# Lệnh trực tiếp của chủ máy: bước đăng nhập bằng trình duyệt CỐ GẮNG VƯỢT ô
# Cloudflare Turnstile trước khi bó tay. Vượt không được sau 3 lượt → can_nguoi
# như NV90. c2a đã đo 3 cách click đều KHÔNG ăn trên grok.com thật → bước vượt
# này chỉ kiểm bằng trình duyệt GIẢ; CHƯA KIỂM trên grok.com thật.

def test_vuot_thu_thach_thanh_cong_thi_dang_nhap_tiep(mod):
    """Tín hiệu thử thách nhưng vượt thành công (giả lập) → đăng nhập tiếp được."""
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thu_thach_truy_cap"],
            "vuot_thu_thach_thanh_cong": True,
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert ("vuot_thu_thach",) in td.hanh_dong
    # Sau khi vượt thì điền form như thường.
    assert ("dien", "email") in td.hanh_dong
    assert ("dien", "mat_khau") in td.hanh_dong
    assert tk.mat_khau.da_xoa()


def test_vuot_thu_thach_that_bai_3_luot_thi_can_nguoi(mod):
    """Vượt không được sau đúng 3 lượt → can_nguoi (giữ luồng NV90)."""
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thu_thach_truy_cap"],
            "vuot_thu_thach_thanh_cong": False,
            "cookie": {},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert sum(1 for hd in td.hanh_dong if hd[0] == "vuot_thu_thach") == 3
    assert not any(hd[0] == "dien" for hd in td.hanh_dong)
    assert tk.mat_khau.da_xoa()


# ───────────── hai chế độ đăng nhập ─────────────

def test_che_do_truc_tiep_dung_email_mat_khau_tren_x(mod):
    mod._nap_tu({"tai_khoan": ["sso=abc"], "che_do_dang_nhap": "truc_tiep"})
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert ("dien", "email") in td.hanh_dong
    assert ("dien", "mat_khau") in td.hanh_dong
    assert ("bam", "dang_nhap") in td.hanh_dong
    assert tk.mat_khau.da_xoa()


def test_che_do_dang_nhap_sai_thi_bao_loi(mod):
    loi = mod._nap_tu({"tai_khoan": ["sso=abc"], "che_do_dang_nhap": "sai"})
    assert loi is not None and "che_do_dang_nhap" in loi


def test_che_do_google_lan_dau_nhap_va_luu_phien(mod):
    """Chế độ google: lần đầu nhập qua Google rồi LƯU phiên Google vào kho riêng."""
    mod._nap_tu({"tai_khoan": ["sso=abc"], "che_do_dang_nhap": "google"})
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["thanh_cong"], "cookie": {}},
        "https://accounts.google.com/": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert ("bam", "dang_nhap_google") in td.hanh_dong
    assert mod._doc_phien_google().get("sso") == SSO_GIA_OK
    assert tk.mat_khau.da_xoa()


def test_che_do_google_lan_2_chi_bam_tiep_tuc_khong_nhap_lai(mod):
    """Đã có phiên Google lưu → chỉ bấm «tiếp tục với Google», không nhập lại."""
    mod._nap_tu({"tai_khoan": ["sso=abc"], "che_do_dang_nhap": "google"})
    mod._luu_phien_google({"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["thanh_cong"], "cookie": {}},
        "https://accounts.google.com/": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert ("bam", "tiep_tuc_google") in td.hanh_dong
    assert not any(hd[0] == "dien" for hd in td.hanh_dong)
    assert tk.mat_khau.da_xoa()


def test_mat_khau_khong_vao_log_khi_vuot_va_google(mod, caplog):
    """Mật khẩu không lọt vào log ở cả bước vượt thử thách và chế độ google."""
    mod._nap_tu({"tai_khoan": ["sso=abc"], "che_do_dang_nhap": "google"})
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thu_thach_truy_cap"],
            "vuot_thu_thach_thanh_cong": True,
            "cookie": {},
        },
        "https://accounts.google.com/": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    with caplog.at_level(logging.INFO, logger="web2api.module.grok_web"):
        kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log
    assert SSO_GIA_OK not in ban_log
