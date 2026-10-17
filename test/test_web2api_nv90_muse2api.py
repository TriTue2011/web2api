"""Test NV90 cho module muse2api: hợp đồng tự đăng nhập 8 hàm (mẫu NV88).

muse.ai đăng nhập qua SSO của Meta, KHÔNG có form mật khẩu để tự điền —
`dang_nhap` chế độ mật khẩu / `dang_nhap_lai` và tầng (b) `dang_nhap_tu_dong`
trả `can_nguoi` kèm hướng dẫn mở noVNC (trung thực, không đoán luồng không có).
Đường làm mới/đăng nhập lại khả thi duy nhất là kiểm lại cookie (tầng a).

Cookie phiên giữ nguyên cơ chế hiện có của muse2api (chuỗi cookie trong
_TaiKhoan, kiểm bằng bootstrap). Không Internet, không trình duyệt thật —
`_kiem_cookie` được chích giả.

Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
import types
from pathlib import Path

import pytest

from app import kho
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "muse2api"
TEN_GOI = "muse2api_nv90_mod_test"

COOKIE_GIA_OK = "hatch_sess=ok-gia-nv90; hatch_gw=gw-gia-nv90"
COOKIE_GIA_HET_HAN = "hatch_sess=het-han-gia-nv90"
MAT_KHAU_GIA = "mat-khau-gia-nv90-001"
EMAIL_GIA = "ban-gia-nv90@example.com"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("chat", "protocol", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


def _kiem_cookie_gia(cookies: str):
    if "het-han-gia" in cookies:
        return False, "cookie het han (gia)", True
    if "loi-mang-gia" in cookies:
        return False, "timeout (gia)", False
    return True, None, False


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(Module, "_kiem_cookie", staticmethod(_kiem_cookie_gia))
    md = Module(dict(MANIFEST))
    md.kho = kho.kho_rieng("muse2api")
    return md


# ───────────── hợp đồng 8 hàm ─────────────

def test_du_8_ham_hop_dong(mod):
    for ten in ("dang_nhap", "lam_moi_phien", "dang_nhap_tu_dong",
                "tai_khoan_ke", "danh_dau_hong", "mat_khau_da_luu",
                "co_tai_khoan", "ho_so_trinh_duyet"):
        assert callable(getattr(mod, ten, None)), f"thiếu {ten}"


def test_dang_nhap_cookie_ok(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_OK))
    kq = chay(mod.dang_nhap(tk))
    assert kq.thanh_cong, kq.ma_loi
    assert len(mod._accs) == 1
    assert mod.co_tai_khoan(mod._khoa_tu_cookies(COOKIE_GIA_OK))
    assert tk.cookie.da_xoa()


def test_dang_nhap_cookie_het_han(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_HET_HAN))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_trang_thai == 401
    assert mod._accs == []
    assert tk.cookie.da_xoa()


def test_dang_nhap_mat_khau_va_dang_nhap_lai_thi_can_nguoi(mod):
    """muse.ai không có form mật khẩu (SSO Meta) → cần người đăng nhập tay."""
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                    mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap(tk))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert tk.mat_khau.da_xoa()
    tk2 = DongDaTach(dong=2, email=EMAIL_GIA, loai="dang_nhap_lai")
    kq2 = chay(mod.dang_nhap(tk2))
    assert not kq2.thanh_cong and kq2.ma_loi == "can_nguoi"


# ───────────── tầng (a): làm mới phiên ─────────────

def test_lam_moi_phien_thanh_cong(mod):
    chay(mod.dang_nhap(DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                                  cookie=BiMatTam(COOKIE_GIA_OK))))
    khoa = mod._khoa_tu_cookies(COOKIE_GIA_OK)
    kq = chay(mod.lam_moi_phien(khoa))
    assert kq.thanh_cong and kq.tang == "a"


def test_lam_moi_phien_het_han(mod):
    mod._accs = [mod_module._TaiKhoan("acc1", COOKIE_GIA_HET_HAN)]
    khoa = mod._khoa_tu_cookies(COOKIE_GIA_HET_HAN)
    kq = chay(mod.lam_moi_phien(khoa))
    assert not kq.thanh_cong
    assert kq.tang == "a" and kq.ma_loi == "het_han" and kq.ma_trang_thai == 401


def test_lam_moi_phien_khoa_la(mod):
    kq = chay(mod.lam_moi_phien("ms-khong-co"))
    assert not kq.thanh_cong and kq.ma_loi == "khoa_khong_co"


# ───────────── tầng (b): đăng nhập lại tự động ─────────────

def test_dang_nhap_tu_dong_thi_can_nguoi(mod):
    """Không có form mật khẩu để tự điền → can_nguoi kèm hướng dẫn noVNC."""
    from app.khoi_phuc_phien import TaiKhoanCanKhoiPhuc
    tk = TaiKhoanCanKhoiPhuc(khoa="khoa-moi", email=EMAIL_GIA,
                             mat_khau=BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.dang_nhap_tu_dong(tk, None))
    assert not kq.thanh_cong
    assert kq.tang == "b" and kq.ma_loi == "can_nguoi" and kq.can_nguoi
    assert kq.huong_dan  # có hướng dẫn cho người
    assert tk.mat_khau.da_xoa()


# ───────────── tầng (c): chuyển tài khoản kế ─────────────

def test_tai_khoan_ke_va_danh_dau_hong(mod):
    chay(mod.dang_nhap(DongDaTach(dong=1, email="a@example.com", loai="cookie",
                                  cookie=BiMatTam("hatch_sess=a-gia-nv90"))))
    chay(mod.dang_nhap(DongDaTach(dong=2, email="b@example.com", loai="cookie",
                                  cookie=BiMatTam("hatch_sess=b-gia-nv90"))))
    khoa_a = mod._khoa_tu_cookies("hatch_sess=a-gia-nv90")
    khoa_b = mod._khoa_tu_cookies("hatch_sess=b-gia-nv90")
    mod.danh_dau_hong(khoa_b, "sai_chung_thuc")
    assert mod.tai_khoan_ke(khoa_b) == khoa_a
    assert mod.tai_khoan_ke(khoa_a) is None


def test_co_tai_khoan_va_ho_so_trinh_duyet(mod):
    assert not mod.co_tai_khoan("ms-khong-co")
    chay(mod.dang_nhap(DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                                  cookie=BiMatTam(COOKIE_GIA_OK))))
    khoa = mod._khoa_tu_cookies(COOKIE_GIA_OK)
    assert mod.co_tai_khoan(khoa)
    assert mod.mat_khau_da_luu(khoa) is None  # muse.ai không lưu mật khẩu
    hs = mod.ho_so_trinh_duyet(khoa)
    assert hs.startswith("muse2api-")
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", hs)


# ───────────── bí mật không lộ ─────────────

def test_bi_mat_khong_vao_log(mod, caplog):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie",
                    cookie=BiMatTam(COOKIE_GIA_OK))
    with caplog.at_level(logging.INFO, logger="web2api.module.muse2api"):
        chay(mod.dang_nhap(tk))
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert "ok-gia-nv90" not in ban_log
