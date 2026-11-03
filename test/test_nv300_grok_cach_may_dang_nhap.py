"""NV300 — LỖI THẬT chủ máy báo: «thêm Grok bằng EMAIL (có mật khẩu) nhưng nó đăng nhập qua GOOGLE».

Nguyên nhân (đo trên mã, hai tầng):
  1. Bên đăng nhập thực thi trong web (`may-dang-nhap`) KHÔNG đọc `cach_dang_nhap`
     của tài khoản; tài khoản chưa khai «đăng nhập bằng» thì đi đường Google chung
     (`/v1/session/auto-login-saved`), coi email tài khoản là email Google.
  2. Dịch vụ `dich-vu/may-dang-nhap` (grok_web_login) hễ thấy nút Google trên
     màn đăng nhập là bấm Google ("Đường 1") và coi như xong; đường email chỉ chạy
     khi KHÔNG có nút Google — mà grok.com luôn hiện cả bốn nút.
Hồi quy: cách email KHÔNG được chạm Google; cách google đi đường Google/hồ sơ.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC / "test"))

from test_web2api_nv223_dang_nhap_qua_google import (  # noqa: E402,F401
    EMAIL_GOOGLE, EMAIL_GROK, MK_GOOGLE, MK_GPT, _chay, _dong,
    _don_trang_thai_dang_nhap_google, _onboard_cua, dich_vu_gia, mod)


def _dong_grok(cach, **kw):
    d = _dong(email=EMAIL_GROK, mk=MK_GPT, totp=None, module_cua="grok-web", **kw)
    d.cach_dang_nhap = cach
    return d


def test_grok_cach_email_di_duong_truc_tiep_khong_cham_google(mod, dich_vu_gia):
    d = _dong_grok("email")                      # KHÔNG khai dang_nhap_bang (NV223)
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert dich_vu_gia.google_bat_dau == []       # không đi /v1/session/auto-login-saved
    ob = _onboard_cua(dich_vu_gia, "grok-web")
    assert len(ob) == 1 and ob[0]["method"] == "email"
    assert ob[0]["email"] == EMAIL_GROK
    (luu,) = dich_vu_gia.da_luu                   # bí mật của CHÍNH tài khoản
    assert luu["email"] == EMAIL_GROK and luu["password"] == MK_GPT
    assert luu["loai"] == "openai"


def test_grok_cach_google_di_duong_google_voi_bi_mat_tai_khoan_google(mod, dich_vu_gia):
    d = _dong_grok("google", dang_nhap_bang="google", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    ob = _onboard_cua(dich_vu_gia, "grok-web")
    assert len(ob) == 1 and ob[0]["method"] == "google"
    assert ob[0]["email"] == EMAIL_GOOGLE
    assert dich_vu_gia.google_bat_dau == []
    (luu,) = dich_vu_gia.da_luu
    assert luu["loai"] == "google" and luu["password"] == MK_GOOGLE


def test_grok_cach_google_khong_co_mat_khau_rieng_van_dang_nhap_duoc(mod, dich_vu_gia):
    """Cách google chọn hồ sơ đã lưu: tài khoản đích không có mật khẩu riêng."""
    d = _dong_grok("google", dang_nhap_bang="google", google=True)
    d.mat_khau = None
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert _onboard_cua(dich_vu_gia, "grok-web")[0]["method"] == "google"


def test_lop_tuong_tu_claude_cach_email_khong_roi_ve_google(mod, dich_vu_gia):
    """Cùng lớp lỗi ở provider khác: cách email tường minh KHÔNG được đi Google."""
    d = _dong(email="chu.claude@example.com", mk=MK_GPT, totp=None, module_cua="claude-web")
    d.cach_dang_nhap = "email"
    _chay(mod.dang_nhap(d))
    assert dich_vu_gia.google_bat_dau == []
    assert _onboard_cua(dich_vu_gia, "claude-web") == []   # đường mã email, không phải onboard Google


def test_lop_tuong_tu_chatgpt_giu_truc_tiep(mod, dich_vu_gia):
    dich_vu_gia.refresh_jwt_ok = False
    d = _dong()
    d.cach_dang_nhap = "email"
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert dich_vu_gia.google_bat_dau == [] and _onboard_cua(dich_vu_gia, "chatgpt") == []
    assert len(_onboard_cua(dich_vu_gia, "openai")) == 1


def test_loi_gan_bi_mat_cach_ho_so_thanh_dang_nhap_ho(tmp_path, monkeypatch):
    """Lõi: tài khoản cách google (hồ sơ = tài khoản Google đã lưu) → dong mang
    bí mật của tài khoản Google đó để bên thực thi đăng nhập hộ."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    from app import kho_tai_khoan as ktk
    from app import tuyen
    from app.tai_khoan_hang_loat import DongDaTach
    gg = ktk.luu_tai_khoan(EMAIL_GOOGLE, MK_GOOGLE, None, "", module_id="gemini-web", nhom="ai")
    grok = ktk.luu_tai_khoan(EMAIL_GOOGLE, None, None, "", module_id="grok-web", nhom="ai",
                             cach_dang_nhap="google", ho_so=gg["id"])
    d = DongDaTach(dong=0, email=EMAIL_GOOGLE, loai="google")
    bm = tuyen._gan_bi_mat_dang_nhap_ho(d, ktk.tim_theo_id(grok["id"]))
    assert d.dang_nhap_bang == ktk.DANG_NHAP_BANG_QUA_TAI_KHOAN_LUU
    assert d.dnh_email == EMAIL_GOOGLE and d.dnh_mat_khau is not None
    for b in bm:
        if b is not None:
            b.xoa()
