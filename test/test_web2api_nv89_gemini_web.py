"""Test NV89 cho module gemini-web: đăng nhập tự động + khôi phục (theo mẫu NV88).

- `dang_nhap(DongDaTach)` theo hợp đồng NV67 (bộ chạy lần lượt tái dùng).
- `lam_moi_phien` (tầng a), `dang_nhap_tu_dong` (tầng b), `tai_khoan_ke`/
  `danh_dau_hong` (tầng c), `mat_khau_da_luu`, `co_tai_khoan`, `ho_so_trinh_duyet`.
- Trình duyệt là GIẢ (TrinhDuyetGia của app.khoi_phuc_phien), máy chủ HTTP là
  giả (httpx.MockTransport). Không Internet, không Chrome.

Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import httpx
import pytest
import re

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap, LuotChay

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv89_mod_test"

PSID_GIA_OK = "psid-gia-nv89-ok-0001"
PSIDTS_GIA_OK = "psidts-gia-nv89-0001"
PSID_GIA_HET_HAN = "psid-gia-nv89-het-han-0002"

COOKIE_GIA_OK = f"__Secure-1PSID={PSID_GIA_OK}; __Secure-1PSIDTS={PSIDTS_GIA_OK}"
COOKIE_GIA_HET_HAN = f"__Secure-1PSID={PSID_GIA_HET_HAN}"


def _khoa(cookie):
    """Khoá NV93 «tk-<băm>» của một chuỗi cookie giả."""
    psid, _ = mod_module._tach_psid(cookie)
    return Module._khoa_tu_token(psid)
MAT_KHAU_GIA = "mat-khau-gia-nv89-001"
EMAIL_GIA = "ban-gia-nv89@example.com"

URL_DANG_NHAP_GOOGLE = (
    "https://accounts.google.com/signin/v2/identifier"
    "?flowName=GlifWebSignIn&flowEntry=ServiceLogin"
)


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


def _may_chu_gia():
    def xu_ly(req):
        if req.url.path == "/":
            cookie = req.headers.get("cookie", "")
            psid = None
            for phan in cookie.split(";"):
                ten, _, gia_tri = phan.strip().partition("=")
                if ten == "__Secure-1PSID":
                    psid = gia_tri.strip()
            if psid == PSID_GIA_OK:
                return httpx.Response(
                    200, text='<html>"SNlM0e":"SNlM0e-gia-nv89"</html>')
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")
    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch):
    """Module gemini-web với client giả + kho bí mật tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("gemini-web")
    transport = _may_chu_gia()

    def tao_client(psid, psidts):
        return mod_client.GeminiWebClient(
            psid, psidts, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


def _trinh_duyet_thanh_cong(psid=PSID_GIA_OK, psidts=PSIDTS_GIA_OK):
    return TrinhDuyetGia(trang={
        URL_DANG_NHAP_GOOGLE: {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"__Secure-1PSID": psid,
                       "__Secure-1PSIDTS": psidts},
        }})


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


# ───────────── dang_nhap: dán cookie ─────────────

def test_dang_nhap_cookie_hop_le(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_OK))
    kq = chay(mod.dang_nhap(tk))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == COOKIE_GIA_OK
    assert tk.cookie.da_xoa()


def test_dang_nhap_cookie_het_han(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_HET_HAN))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_trang_thai == 401
    assert kq.ma_loi == "phien_khong_dung_duoc"


# ───────────── dang_nhap: mật khẩu qua trình duyệt giả ─────────────

def test_dang_nhap_mat_khau_thanh_cong_va_luu_mat_khau(mod):
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong()
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert tk.mat_khau.da_xoa()
    # mật khẩu đã lưu NHƯ BÍ MẬT để tầng (b) dùng lại sau
    bm = mod.mat_khau_da_luu(_khoa(kq.cookie))
    assert bm is not None and bm.lay() == MAT_KHAU_GIA
    bm.xoa()
    # thứ tự bước đăng nhập trên trình duyệt giả
    ten_buoc = [h[0] for h in mod._trinh_duyet_da_dung.hanh_dong]
    assert ten_buoc[0] == "mo_trang"
    assert "dien" in ten_buoc and "bam" in ten_buoc


def test_dang_nhap_gap_captcha_thi_can_nguoi(mod):
    # Bản module 2026-10-27: CAPTCHA trước khi điền trả mã RIÊNG
    # thu_thach_truoc_khi_dien (không chìm thành can_nguoi), vẫn thất bại,
    # vẫn không điền, vẫn xoá mật khẩu — hành vi cũ, chỉ mã rõ hơn.
    td = TrinhDuyetGia(trang={
        URL_DANG_NHAP_GOOGLE: {"tin_hieu": ["captcha"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert tk.mat_khau.da_xoa()


def test_dang_nhap_khong_co_trinh_duyet_thi_can_nguoi(mod):
    """Không có bộ điều khiển trình duyệt → không đoán mò, báo cần người."""
    # Bản module 2026-10-27: không có controller trả mã RIÊNG
    # trinh_duyet_none (không chìm thành can_nguoi), vẫn thất bại.
    mod._tao_trinh_duyet = lambda: None
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "trinh_duyet_none"


def test_dang_nhap_lai_dung_mat_khau_da_luu(mod):
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong()
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


# ───────────── tầng (a): kiểm phiên ─────────────

def test_lam_moi_phien_thanh_cong(mod):
    mod.nap({"cookies": [COOKIE_GIA_OK]})
    khoa = _khoa(COOKIE_GIA_OK)
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a"


def test_lam_moi_phien_het_han(mod):
    mod.nap({"cookies": [COOKIE_GIA_HET_HAN]})
    khoa = _khoa(COOKIE_GIA_HET_HAN)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_trang_thai == 401
    assert kq.tang == "a" and kq.ma_loi == "het_han"


def test_lam_moi_phien_khoa_la(mod):
    kq = chay(mod.lam_moi_phien("tk-khong-co-gia"))
    assert not kq.thanh_cong and kq.ma_loi == "khoa_khong_co"


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_dang_nhap_tu_dong(mod):
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong()
    tk = TaiKhoanCanKhoiPhuc(khoa="tk-moi-gia", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, mod._tao_trinh_duyet()))
    assert kq.thanh_cong and kq.tang == "b"
    assert tk.mat_khau.da_xoa()  # module xoá sau khi đọc xong


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tai_khoan_ke_va_danh_dau_hong(mod):
    mod.nap({"cookies": [COOKIE_GIA_OK, COOKIE_GIA_HET_HAN]})
    khoa_ok = _khoa(COOKIE_GIA_OK)
    khoa_hh = _khoa(COOKIE_GIA_HET_HAN)
    mod.danh_dau_hong(khoa_hh, "sai_chung_thuc")
    # tài khoản hỏng bị bỏ qua → kế của nó là tài khoản khoẻ
    assert mod.tai_khoan_ke(khoa_hh) == khoa_ok


def test_chi_mot_tai_khoan_thi_khong_co_ke(mod):
    mod.nap({"cookies": [COOKIE_GIA_OK]})
    khoa = _khoa(COOKIE_GIA_OK)
    assert mod.tai_khoan_ke(khoa) is None


def test_ho_so_trinh_duyet_hop_khuon(mod):
    hs = mod.ho_so_trinh_duyet("tk-a1b2c3d4e5f6")
    assert hs.startswith("gemini-web-")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", hs)


# ───────────── tái dùng bộ chạy lần lượt ─────────────

def test_luot_chay_voi_dang_nhap_cua_module(mod):
    """LuotChay (NV67) gọi thẳng mod.dang_nhap — lõi tái dùng, không viết lại."""
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong()
    ds = [
        DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie", cookie=BiMatTam(COOKIE_GIA_OK)),
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
    mod._tao_trinh_duyet = lambda: _trinh_duyet_thanh_cong()
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    with caplog.at_level(logging.INFO, logger="web2api.module.gemini-web"):
        chay(mod.dang_nhap(tk))
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log
    assert PSID_GIA_OK not in ban_log


# ───────────── lỗi lạ đóng cửa (fail-closed) ─────────────

def test_loi_la_o_tang_a_dong_cua(mod):
    """Transport ném RuntimeError ở tầng (a) → kết quả False, không bao giờ True."""
    def xu_ly(req):
        raise RuntimeError("loi-gia-bat-ngo")
    transport = httpx.MockTransport(xu_ly)
    mod.nap({"cookies": [COOKIE_GIA_OK]})
    khoa = _khoa(COOKIE_GIA_OK)

    def tao_client_loi(psid, psidts):
        return mod_client.GeminiWebClient(
            psid, psidts, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client_loi
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong is False
    assert kq.tang == "a" and kq.ma_loi == "loi_mang"
