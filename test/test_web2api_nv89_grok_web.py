"""Test NV89 cho module grok-web: đăng nhập tự động + khôi phục phiên.

Hợp đồng sao y mẫu NV88 (chatgpt-web) nhưng dữ liệu theo grok-web:

- `dang_nhap(DongDaTach)`: cookie → kiểm qua `lay_uid_tu_phien` (bắt buộc có
  cookie «sso»); mat_khau → trình duyệt mở https://x.com/login (SSO qua X),
  đọc cookie grok.com; dang_nhap_lai → mật khẩu đã lưu.
- `lam_moi_phien` (tầng a), `dang_nhap_tu_dong` (tầng b), `tai_khoan_ke`/
  `danh_dau_hong` (tầng c), `mat_khau_da_luu`, `co_tai_khoan`,
  `ho_so_trinh_duyet`, `_khoa_tu_jar`/`_tim_jar`.
- Trình duyệt là GIẢ (TrinhDuyetGia của app.khoi_phuc_phien). Máy chủ HTTP là
  GIẢ (http.server trên 127.0.0.1 + WEB2API_TEST_CHO_PHEP_LOOPBACK=1) cho
  các trường hợp không có cookie «x-userid». Không Internet, không Chrome.

Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_nv89_mod_test"

SSO_GIA_OK = "sso-gia-nv89-ok"
SSO_GIA_HET_HAN = "sso-gia-nv89-het-han"
UID_GIA = "uid-gia-nv89"
MAT_KHAU_GIA = "mat-khau-gia-nv89-001"
EMAIL_GIA = "ban-gia-nv89@example.com"


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


# ───────────── máy chủ HTTP giả cho /api/auth/session ─────────────

def _lay_sso(headers) -> str:
    for muc in (headers.get("Cookie") or "").split(";"):
        k, _, v = muc.partition("=")
        if k.strip() == "sso":
            return v.strip()
    return ""


class _XuLyPhien(BaseHTTPRequestHandler):
    def _tra(self, ma: int, body: bytes, kieu: str = "text/plain"):
        self.send_response(ma)
        self.send_header("Content-Type", kieu)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.path.rstrip("/").endswith("/api/auth/session"):
            self._tra(404, b"khong co")
            return
        sso = _lay_sso(self.headers)
        if sso == SSO_GIA_HET_HAN:
            self._tra(401, b"het han")
            return
        if not sso:
            self._tra(401, b"thieu sso")
            return
        body = json.dumps({"session": {"userId": "uid-gia-nv89-http"}}).encode("utf-8")
        self._tra(200, body, "application/json")

    def log_message(self, *args):
        pass


@pytest.fixture()
def may_chu_phien(monkeypatch):
    """URL gốc HTTP giả cho lay_uid_tu_phien (cho phép loopback như test gốc)."""
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


def _trinh_duyet_thanh_cong():
    return TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GIA_OK, "x-userid": UID_GIA},
        }})


def _jar_gia(sso: str = SSO_GIA_OK, uid: str = UID_GIA) -> dict:
    return {"sso": sso, "x-userid": uid}


# ───────────── khoá tài khoản mờ ─────────────

def test_khoa_tu_jar_dung_khuon_va_on_dinh_thu_tu(mod):
    khoa1 = mod._khoa_tu_jar({"b": "2-gia", "a": "1-gia"})
    khoa2 = mod._khoa_tu_jar({"a": "1-gia", "b": "2-gia"})
    assert khoa1 == khoa2
    assert re.fullmatch(r"gr-[0-9a-f]{12}", khoa1)
    assert mod._khoa_tu_jar({"sso": SSO_GIA_OK}) != khoa1
    assert mod._tim_jar(khoa1) is None


# ───────────── dang_nhap: dán cookie ─────────────

def test_dang_nhap_cookie_hop_le(mod):
    chuoi = f"sso={SSO_GIA_OK}; x-userid={UID_GIA}"
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(chuoi))
    kq = chay(mod.dang_nhap(tk))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == chuoi
    assert tk.cookie.da_xoa()
    # jar được giữ trong danh sách, khoá mờ song song với _dt_id
    khoa = mod._khoa_tu_jar(_jar_gia())
    tim = mod._tim_jar(khoa)
    assert tim is not None and tim[1]["sso"] == SSO_GIA_OK
    assert len(mod._tai_khoan) == len(mod._dt_id) == 1
    assert mod._dt_id[0] is None


def test_dang_nhap_cookie_thieu_sso(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(f"x-userid={UID_GIA}"))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "thieu_cookie"
    assert tk.cookie.da_xoa()
    assert mod._tai_khoan == []


def test_dang_nhap_cookie_bi_tu_choi_401(mod, may_chu_phien):
    mod._goc_https = may_chu_phien  # jar không có x-userid → đi qua HTTP giả
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(f"sso={SSO_GIA_HET_HAN}"))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_trang_thai == 401
    assert tk.cookie.da_xoa()
    assert mod._tai_khoan == []


def test_dang_nhap_cookie_loi_may_chu(mod, monkeypatch):
    """LoiGrokWeb không thuộc nhóm phiên → ma_loi loi_may_chu (không dò chữ)."""
    def nem(*a, **k):
        raise mod_client.LoiGrokWeb("loi-gia-nv89")
    monkeypatch.setattr(mod_module, "lay_uid_tu_phien", nem)
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(f"sso={SSO_GIA_OK}; x-userid={UID_GIA}"))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "loi_may_chu"


def test_dang_nhap_loai_khong_ho_tro(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="totp")
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "loai_khong_ho_tro"


# ───────────── dang_nhap: mật khẩu qua trình duyệt giả ─────────────

def test_dang_nhap_mat_khau_thanh_cong_va_luu_mat_khau(mod):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert kq.cookie == f"sso={SSO_GIA_OK}; x-userid={UID_GIA}"
    assert tk.mat_khau.da_xoa()
    # mật khẩu đã lưu NHƯ BÍ MẬT để tầng (b) dùng lại sau
    khoa = mod._khoa_tu_jar(_jar_gia())
    bm = mod.mat_khau_da_luu(khoa)
    assert bm is not None and bm.lay() == MAT_KHAU_GIA
    bm.xoa()
    # thứ tự bước đăng nhập trên trình duyệt giả
    ten_buoc = [h[0] for h in mod._trinh_duyet_da_dung.hanh_dong]
    assert ten_buoc[0] == "mo_trang"
    assert "dien" in ten_buoc and "bam" in ten_buoc
    url_mo = mod._trinh_duyet_da_dung.hanh_dong[0][1]
    assert url_mo == "https://x.com/login"


def test_dang_nhap_khong_doc_duoc_sso_thi_that_bai(mod):
    """Đăng nhập xong nhưng cookie thiếu «sso» → không được phép thành công."""
    def td_thieu_sso():
        return TrinhDuyetGia(trang={
            "https://x.com/login": {
                "tin_hieu": ["thanh_cong"],
                "cookie": {"x-userid": UID_GIA},
            }})
    mod._tao_trinh_duyet = td_thieu_sso
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "khong_lay_duoc_phien"
    assert tk.mat_khau.da_xoa()
    assert mod._tai_khoan == []


def test_dang_nhap_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": ["captcha"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()
    assert mod._tai_khoan == []


def test_dang_nhap_khong_co_trinh_duyet_thi_can_nguoi(mod):
    """Không có bộ điều khiển trình duyệt → không đoán mò, báo cần người."""
    mod._tao_trinh_duyet = lambda: None
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()


def test_dang_nhap_lai_dung_mat_khau_da_luu(mod):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq1 = chay(mod.dang_nhap(tk))
    assert kq1.thanh_cong
    # dòng chỉ có email → đăng nhập lại bằng mật khẩu đã lưu, không cần gõ lại
    tk2 = DongDaTach(dong=2, email=EMAIL_GIA, loai="dang_nhap_lai")
    kq2 = chay(mod.dang_nhap(tk2))
    assert kq2.thanh_cong, kq2.ma_loi


def test_dang_nhap_lai_khong_co_mat_khau_luu(mod):
    tk = DongDaTach(dong=1, email="chua-luu@example.com", loai="dang_nhap_lai")
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "khong_co_mat_khau_luu"


# ───────────── tầng (a): làm mới phiên qua HTTP giả ─────────────

def test_lam_moi_phien_thanh_cong_qua_http(mod, may_chu_phien):
    mod._goc_https = may_chu_phien
    mod._tai_khoan = [{"sso": SSO_GIA_OK}]  # không x-userid → đi qua HTTP giả
    mod._dt_id = [None]
    khoa = mod._khoa_tu_jar({"sso": SSO_GIA_OK})
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a"


def test_lam_moi_phien_het_han(mod, may_chu_phien):
    mod._goc_https = may_chu_phien
    mod._tai_khoan = [{"sso": SSO_GIA_HET_HAN}]
    mod._dt_id = [None]
    khoa = mod._khoa_tu_jar({"sso": SSO_GIA_HET_HAN})
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong
    assert kq.tang == "a" and kq.ma_loi == "het_han"
    assert kq.ma_trang_thai == 401


def test_lam_moi_phien_khoa_la(mod):
    kq = chay(mod.lam_moi_phien("gr-khong-co"))
    assert not kq.thanh_cong and kq.ma_loi == "khoa_khong_co"


def test_lam_moi_phien_loi_la_dong_cua(mod, monkeypatch):
    """Lỗi lạ khi kiểm uid → kết quả THẤT BẠI, không ném ra ngoài."""
    def nem(*a, **k):
        raise RuntimeError("loi-gia-nv89")
    mod._tai_khoan = [_jar_gia()]
    mod._dt_id = [None]
    monkeypatch.setattr(mod_module, "lay_uid_tu_phien", nem)
    khoa = mod._khoa_tu_jar(_jar_gia())
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_loi == "loi_mang"


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_dang_nhap_tu_dong(mod):
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    tk = TaiKhoanCanKhoiPhuc(khoa="khoa-moi", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, _trinh_duyet_thanh_cong()))
    assert kq.thanh_cong and kq.tang == "b"
    assert tk.mat_khau.da_xoa()  # module xoá sau khi đọc xong


def test_dang_nhap_tu_dong_thieu_mat_khau(mod):
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    tk = TaiKhoanCanKhoiPhuc(khoa="khoa-moi", email=EMAIL_GIA, mat_khau=None)
    kq = chay(mod.dang_nhap_tu_dong(tk, _trinh_duyet_thanh_cong()))
    assert not kq.thanh_cong and kq.ma_loi == "thieu_mat_khau"


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tai_khoan_ke_va_danh_dau_hong(mod):
    jar_a = {"sso": "sso-gia-nv89-a", "x-userid": "u-a"}
    jar_b = {"sso": "sso-gia-nv89-b", "x-userid": "u-b"}
    mod._tai_khoan = [jar_a, jar_b]
    mod._dt_id = [None, None]
    khoa_a = mod._khoa_tu_jar(jar_a)
    khoa_b = mod._khoa_tu_jar(jar_b)
    mod.danh_dau_hong(khoa_b, "sai_chung_thuc")
    # tài khoản hỏng bị bỏ qua → kế của chính nó là tài khoản khoẻ
    assert mod.tai_khoan_ke(khoa_b) == khoa_a
    # kế của tài khoản khoẻ không phải chính nó và không phải tài khoản hỏng
    assert mod.tai_khoan_ke(khoa_a) is None


def test_chi_mot_tai_khoan_thi_khong_co_ke(mod):
    jar = _jar_gia()
    mod._tai_khoan = [jar]
    mod._dt_id = [None]
    assert mod.tai_khoan_ke(mod._khoa_tu_jar(jar)) is None


def test_co_tai_khoan(mod):
    jar = _jar_gia()
    mod._tai_khoan = [jar]
    mod._dt_id = [None]
    assert mod.co_tai_khoan(mod._khoa_tu_jar(jar))
    assert not mod.co_tai_khoan("gr-khong-co")


def test_ho_so_trinh_duyet_hop_khuon(mod):
    hs = mod.ho_so_trinh_duyet(mod._khoa_tu_jar(_jar_gia()))
    assert hs.startswith("grok-web-")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", hs)


# ───────────── tái dùng bộ chạy lần lượt ─────────────

def test_luot_chay_voi_dang_nhap_cua_module(mod):
    """LuotChay (NV67) gọi thẳng mod.dang_nhap — lõi tái dùng, không viết lại."""
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    ds = [
        DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                   cookie=BiMatTam(f"sso={SSO_GIA_OK}; x-userid={UID_GIA}")),
        DongDaTach(dong=2, email="hai@example.com", loai="mat_khau",
                   mat_khau=BiMatTam(MAT_KHAU_GIA)),
    ]
    luot = LuotChay(ds, mod.dang_nhap, ngu=lambda s: asyncio.sleep(0))
    tom_tat = chay(luot.chay())
    assert (tom_tat.xong, tom_tat.loi) == (2, 0)
    for tk in ds:
        tk.xoa_bi_mat()


# ───────────── bí mật không vào log ─────────────

def test_mat_khau_khong_vao_log(mod, caplog):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    with caplog.at_level(logging.INFO, logger="web2api.module.grok_web"):
        chay(mod.dang_nhap(tk))
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log


# ───────────── URL qua app/tu_xa.py (chống SSRF) ─────────────

def test_cai_ws_url_loopback_bi_chan_khi_chua_cho_phep(mod, monkeypatch):
    """ws_url trỏ loopback mà test không cho phép → cai() từ chối ngay ở cổng
    kiểm URL (app/tu_xa.py), không mở WebSocket."""
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    chuoi = f"sso={SSO_GIA_OK}; x-userid={UID_GIA}"
    kq = chay(mod.cai({"tai_khoan": [chuoi], "ws_url": "ws://127.0.0.1:9/duong-gia"}))
    assert kq["ok"] is False
    assert "không hợp lệ" in kq["loi"]


def test_cai_ws_url_vuot_kiem_tu_xa_khi_cho_phep_loopback(mod, may_chu_phien):
    """Cho phép loopback → qua được cổng kiểm URL; lỗi (nếu có) nằm ở bước bắt
    tay WebSocket, không phải ở URL."""
    chuoi = f"sso={SSO_GIA_OK}; x-userid={UID_GIA}"
    ws_url = may_chu_phien.replace("http://", "ws://") + "/duong-gia"
    kq = chay(mod.cai({"tai_khoan": [chuoi], "ws_url": ws_url}))
    assert "không hợp lệ" not in kq.get("loi", "")
