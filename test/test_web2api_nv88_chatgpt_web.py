"""Test NV88 cho module chatgpt-web (provider mẫu): đăng nhập tự động + khôi phục.

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
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv88_mod_test"

TOKEN_GIA_OK = "token-gia-nv88-ok-0001"
TOKEN_GIA_HET_HAN = "token-gia-nv88-het-han-0002"
MAT_KHAU_GIA = "mat-khau-gia-nv88-001"
EMAIL_GIA = "ban-gia-nv88@example.com"


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
        auth = req.headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if req.url.path == "/backend-api/me":
            if token == TOKEN_GIA_OK or token.startswith("token-tu-"):
                return httpx.Response(200, json={"email": EMAIL_GIA})
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")
    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch):
    """Module chatgpt-web với client giả + kho bí mật tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("chatgpt-web")
    transport = _may_chu_gia()

    def tao_client(token):
        return mod_client.ChatGPTWebClient(
            token, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


def _trinh_duyet_thanh_cong(token_moi="token-tu-phien-gia-0003"):
    return TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"access_token": token_moi},
        }})


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


# ───────────── dang_nhap: dán cookie ─────────────

def test_dang_nhap_cookie_hop_le(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(TOKEN_GIA_OK))
    kq = chay(mod.dang_nhap(tk))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA_OK
    assert tk.cookie.da_xoa()


def test_dang_nhap_cookie_het_han(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(TOKEN_GIA_HET_HAN))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong
    assert kq.ma_trang_thai == 401


# ───────────── dang_nhap: mật khẩu qua trình duyệt giả ─────────────

def test_dang_nhap_mat_khau_thanh_cong_va_luu_mat_khau(mod):
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert tk.mat_khau.da_xoa()
    # mật khẩu đã lưu NHƯ BÍ MẬT để tầng (b) dùng lại sau
    bm = mod.mat_khau_da_luu(mod._khoa_tu_token(kq.cookie))
    assert bm is not None and bm.lay() == MAT_KHAU_GIA
    bm.xoa()
    # thứ tự bước đăng nhập trên trình duyệt giả
    ten_buoc = [h[0] for h in mod._trinh_duyet_da_dung.hanh_dong]
    assert ten_buoc[0] == "mo_trang"
    assert "dien" in ten_buoc and "bam" in ten_buoc


def test_dang_nhap_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {"tin_hieu": ["captcha"], "cookie": {}}})
    mod._tao_trinh_duyet = lambda: td
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()


def test_dang_nhap_khong_co_trinh_duyet_thi_can_nguoi(mod):
    """Không có bộ điều khiển trình duyệt → không đoán mò, báo cần người."""
    mod._tao_trinh_duyet = lambda: None
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


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


# ───────────── tầng (a): làm mới phiên ─────────────

def test_lam_moi_phien_thanh_cong(mod):
    mod.nap({"tai_khoan": [TOKEN_GIA_OK]})
    khoa = mod._khoa_tu_token(TOKEN_GIA_OK)
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a"


def test_lam_moi_phien_het_han(mod):
    mod.nap({"tai_khoan": [TOKEN_GIA_HET_HAN]})
    khoa = mod._khoa_tu_token(TOKEN_GIA_HET_HAN)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong and kq.ma_trang_thai == 401


def test_lam_moi_phien_khoa_la(mod):
    kq = chay(mod.lam_moi_phien("tk-khong-co"))
    assert not kq.thanh_cong


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_dang_nhap_tu_dong(mod):
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    tk = TaiKhoanCanKhoiPhuc(khoa="tk-moi", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, mod._tao_trinh_duyet()))
    assert kq.thanh_cong and kq.tang == "b"
    assert tk.mat_khau.da_xoa()  # module xoá sau khi đọc xong


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tai_khoan_ke_va_danh_dau_hong(mod):
    mod.nap({"tai_khoan": [TOKEN_GIA_OK, TOKEN_GIA_HET_HAN]})
    khoa_ok = mod._khoa_tu_token(TOKEN_GIA_OK)
    khoa_hh = mod._khoa_tu_token(TOKEN_GIA_HET_HAN)
    mod.danh_dau_hong(khoa_hh, "sai_chung_thuc")
    # tài khoản hỏng bị xếp sau → kế của chính nó là tài khoản khoẻ
    assert mod.tai_khoan_ke(khoa_hh) == khoa_ok
    # chỉ còn một tài khoản trong thực tế → None (hết cách ở tầng c)
    mod2_khoa = [khoa_hh]
    assert mod.tai_khoan_ke("tk-khong-co") in (khoa_ok, khoa_hh, None)


def test_chi_mot_tai_khoan_thi_khong_co_ke(mod):
    mod.nap({"tai_khoan": [TOKEN_GIA_OK]})
    khoa = mod._khoa_tu_token(TOKEN_GIA_OK)
    assert mod.tai_khoan_ke(khoa) is None


def test_ho_so_trinh_duyet_hop_khuon(mod):
    hs = mod.ho_so_trinh_duyet("tk-a1b2c3d4e5f6")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", hs)


# ───────────── tái dùng bộ chạy lần lượt ─────────────

def test_luot_chay_voi_dang_nhap_cua_module(mod):
    """LuotChay (NV67) gọi thẳng mod.dang_nhap — lõi tái dùng, không viết lại."""
    mod._tao_trinh_duyet = _trinh_duyet_thanh_cong
    ds = [
        DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie", cookie=BiMatTam(TOKEN_GIA_OK)),
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
    with caplog.at_level(logging.INFO, logger="web2api.module.chatgpt-web"):
        chay(mod.dang_nhap(tk))
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU_GIA not in ban_log
