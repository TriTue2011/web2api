"""NV309-c — khóa serial toàn cục cho đường Google + giữ captcha toàn cục.

SAI S3: w2a chỉ có khóa riêng từng module + hoãn captcha riêng từng module
1800 giây → hai module có thể đăng nhập Google song song, dồn RAM và mời
captcha chùm (c2a: `_glogin_serial` 1 phiên tại một thời điểm, giãn cách
25 giây + giữ CAPTCHA toàn cục 6 giờ).

Mảnh này: MỘT chỗ duy nhất trong lõi (`app/khoa_dang_nhap_serial.py`) —
serial + gap + hold — áp tại điểm chặn `tai_dung_ho_so` của 4 module.
KHÔNG đổi ngữ nghĩa challenge → can_nguoi (Q4): gặp thử thách vẫn trả
can_nguoi như cũ, chỉ thêm tác dụng phụ đặt hold cho các lượt sau.
Hoãn riêng từng module 1800 giây giữ nguyên.

Đỏ-trước-xanh: trước mảnh này, module lõi chưa tồn tại (ImportError) và
`tai_dung_ho_so` không nhận `chu_giu` (TypeError).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import pytest

from app import khoa_dang_nhap_serial as kg
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import DongDaTach

THU_MUC_MODULE = Path("modules/grok-web")
HO_SO = "abcdef0123456789"
EMAIL_A = "a-gia@example.com"
EMAIL_B = "b-gia@example.com"


def chay(coro):
    return asyncio.run(coro)


def tai_grok_web():
    ten = "web2api_module_grok_web_nv309c"
    sys.modules.pop(ten, None)
    tep = THU_MUC_MODULE / "module.py"
    manifest = json.loads((THU_MUC_MODULE / "manifest.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        ten, tep, submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod, manifest


def mod_moi(monkeypatch):
    mod, manifest = tai_grok_web()
    m = mod.Module(manifest)

    async def khong_co_thu_thach(*a):
        return False

    monkeypatch.setattr(m, "_co_thu_thach", khong_co_thu_thach)
    return m


@pytest.fixture(autouse=True)
def dat_lai_khoa(monkeypatch):
    """Mỗi test chạy trên khóa sạch, gap/hold nhỏ để test nhanh."""
    kg.dat_lai()
    # NV309-c: gap đọc từ env lúc gọi nên fixture đặt env, không đặt hằng.
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(kg, "GIU_CAPTCHA_GIAY", 3600.0)
    yield
    kg.dat_lai()


# ── Khóa lõi: serial + gap ────────────────────────────────────────────────

def test_hai_luot_google_khong_chay_chong():
    """Lượt 2 chỉ vào sau khi lượt 1 ra (không song song)."""
    dang_chay = 0
    dong_thoi_toi_da = 0

    async def kich_ban():
        async def luot(ten, giu):
            nonlocal dang_chay, dong_thoi_toi_da
            async with kg.phien_dang_nhap(ten):
                dang_chay += 1
                dong_thoi_toi_da = max(dong_thoi_toi_da, dang_chay)
                await asyncio.sleep(giu)
                dang_chay -= 1

        await asyncio.gather(luot("a", 0.05), luot("b", 0.0))

    chay(kich_ban())
    assert dang_chay == 0
    assert dong_thoi_toi_da == 1


def test_gap_giua_hai_luot(monkeypatch):
    """Lượt 2 bắt đầu sau lượt 1 kết thúc ít nhất bằng gap (monkeypatch env)."""
    kg.dat_lai()
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0.2")
    moc = []

    async def kich_ban():
        async with kg.phien_dang_nhap("a"):
            pass
        moc.append(time.monotonic())
        async with kg.phien_dang_nhap("b"):
            moc.append(time.monotonic())

    chay(kich_ban())
    assert moc[1] - moc[0] >= 0.19  # gap 0.2 trừ sai số đo nhỏ


@pytest.mark.parametrize("gia_tri", ["inf", "nan", "-1"])
def test_gap_khong_huu_han_hoac_am_bi_tu_choi(monkeypatch, gia_tri):
    """Cấu hình gap không thể làm khóa chờ vô hạn hoặc nhận giá trị âm."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", gia_tri)
    assert math.isfinite(kg._gap_hieu_luc())
    assert kg._gap_hieu_luc() >= 0


def test_luot_google_co_deadline_va_nha_khoa_sau_khi_het_han(monkeypatch):
    """Lượt treo hết deadline phải nhả serial để lượt sau tiếp tục."""
    monkeypatch.setenv("WEB2API_DANG_NHAP_TOI_DA_GIAY", "0.05")

    async def kich_ban():
        with pytest.raises(asyncio.TimeoutError):
            async with kg.phien_dang_nhap("a"):
                await asyncio.sleep(0.2)
        async with kg.phien_dang_nhap("b"):
            return True

    assert chay(kich_ban()) is True


# ── Hold captcha toàn cục qua module Grok ──────────────────────────────────

def test_captcha_o_module_a_chan_module_b(monkeypatch):
    """Grok gặp captcha (tín hiệu captcha) → đặt hold; lượt Google khác
    (email khác) bị chặn mà KHÔNG lái trình duyệt."""
    m = mod_moi(monkeypatch)
    td_cap = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["captcha"], "cookie": {}}})
    kq = chay(m.tai_dung_ho_so(HO_SO, td_cap, chu_giu=EMAIL_A))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert kg.dang_giu()

    td_b = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["da_dang_nhap"],
                                 "cookie": {"sso": "sso-gia"}}})
    kq2 = chay(m.tai_dung_ho_so(HO_SO, td_b, chu_giu=EMAIL_B))
    assert not kq2.thanh_cong and kq2.ma_loi == kg.MA_LOI_GIU
    assert ("mo_trang", m._URL_TAI_DUNG) not in td_b.hanh_dong


def test_chu_hold_chay_lai_thanh_cong_thi_go_hold(monkeypatch):
    """Chủ hold (người đã tích tay xong) chạy lại → thành công + gỡ hold →
    lượt khác chạy lại được."""
    m = mod_moi(monkeypatch)
    kg.dat_giu_captcha(EMAIL_A)
    assert kg.dang_giu()
    td = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["da_dang_nhap"],
                                 "cookie": {"sso": "sso-gia"}}})
    kq = chay(m.tai_dung_ho_so(HO_SO, td, chu_giu=EMAIL_A))
    assert kq.thanh_cong, kq.ma_loi
    assert not kg.dang_giu()


def test_2fa_thuan_khong_dat_hold(monkeypatch):
    """2FA thuần (không captcha) → can_nguoi nhưng KHÔNG đặt hold toàn cục
    (2FA là theo tài khoản, không phải thử thách theo IP)."""
    m = mod_moi(monkeypatch)
    td = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["2fa"], "cookie": {}}})
    kq = chay(m.tai_dung_ho_so(HO_SO, td, chu_giu=EMAIL_A))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert not kg.dang_giu()


def test_luot_khong_danh_tinh_chi_serial_khong_dat_hold(monkeypatch):
    """Lượt không danh tính (không chu_giu, không id hồ sơ) gặp captcha →
    vẫn can_nguoi nhưng KHÔNG đặt hold toàn cục (không có chủ để gỡ)."""
    m = mod_moi(monkeypatch)
    td = TrinhDuyetGia(
        trang={m._URL_TAI_DUNG: {"tin_hieu": ["captcha"], "cookie": {}}})
    kq = chay(m.tai_dung_ho_so("", td))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert not kg.dang_giu()
