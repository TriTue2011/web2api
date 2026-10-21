# test_nv209_xac_minh_danh_tinh.py — NV209: dịch vụ may-dang-nhap nhận ra các
# trang XÁC MINH DANH TÍNH của Google là «cần người» và DỪNG, không để phiên
# chạy mãi.
#
# BẰNG CHỨNG ĐO THẬT (21/10/2026 09:52): sau khi TOTP được chấp nhận, Google
# chuyển sang /verification/selfie/precollection; nhật ký dịch vụ chỉ còn
# «vòng 2FA — trang=/verification/selfie/precollection, 0 dòng chọn,
# khung reCAPTCHA=False», phiên đứng ở state=running,
# message="Đã gửi mã, đang xác minh..." mãi.
#
# BẢNG ĐO BƯỚC 1 — loại trang → dịch vụ HIỆN làm gì → MONG MUỐN:
#  /verification/selfie/*        "working"→hết 240s   need_human ngay (xac_minh_selfie), đóng trình duyệt
#  /signin/v2/challenge/ipp     "working"→hết 240s   need_human ngay (xac_minh_dien_thoai)
#  /signin/v2/challenge/dp      need_tap nếu body khớp chữ, không thì "working"→hết trần
#                                                   need_tap (URL là tín hiệu cấu trúc)
#  /signin/rejected              failed chung / "working"  need_human (dang_nhap_bi_tu_choi) + vĩnh viễn
#  /signin/v2/disabled           "working"→hết 240s   need_human (tai_khoan_bi_khoa) + vĩnh viễn
#  /verification/* khác          "working"→hết 240s   need_human (xac_minh_khac) sau vài vòng ngắn
#  /signin/v2/challenge/az (TOTP) need_code ✓         GIỮ NGUYÊN (không phân loại nhầm)
#  trang consent / app           success ✓            GIỮ NGUYÊN
#
# Phân loại theo NGUYÊN TẮC: tiền tố đường dẫn URL + tín hiệu cấu trúc DOM
# (ô tải ảnh/camera); CẤM danh sách từ khoá/chuỗi lỗi theo nhà cung cấp.
from __future__ import annotations

import asyncio
import time

import pytest

import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)
from src import auto_login
from src.auto_login import LoginSession


def _chay(coro):
    return asyncio.run(coro)


# ── Trang / ctx giả: chỉ đủ cho _detect_state + _phan_loai_thu_thach + _vong_2fa ──

class _DinhViGia:
    def __init__(self, trang, chon):
        self._trang = trang
        self._chon = chon

    @property
    def first(self):
        return self

    async def count(self):
        return self._trang._dem(self._chon)

    async def is_visible(self, timeout=None):
        return self._trang._dem(self._chon) > 0

    async def inner_text(self, timeout=None):
        return self._trang._chu(self._chon)

    async def click(self, timeout=None):
        raise RuntimeError("khong co phan tu de bam (gia)")


class _TrangGia:
    """Trang Playwright giả. Không dùng từ khoá dò chữ của Google thật."""

    def __init__(self, url, *, o_chup_anh=False, chu_body="", o_ma=False):
        self._url = url
        self._o_chup_anh = o_chup_anh
        self._chu_body = chu_body
        self._o_ma = o_ma  # ô nhập mã 2FA có hiện không

    @property
    def url(self):
        return self._url

    @url.setter
    def url(self, v):
        self._url = v

    def locator(self, chon):
        return _DinhViGia(self, chon)

    def _dem(self, chon):
        if 'input[type="file"]' in chon:
            return 1 if self._o_chup_anh else 0
        if chon == 'input[type="tel"][autocomplete="one-time-code"]':
            return 1 if self._o_ma else 0
        return 0

    def _chu(self, chon):
        if chon == "body":
            return self._chu_body
        return ""

    async def evaluate(self, js, *args):
        return False


class _CtxGia:
    def __init__(self, trang, *, cookie=()):
        self._trang = trang
        self._cookie = list(cookie)

    @property
    def pages(self):
        return [self._trang]

    async def cookies(self):
        return [{"name": n, "value": "gia"} for n in self._cookie]


COOKIE_DANG_NHAP = ("__Secure-1PSID", "SID")


def _phien():
    return LoginSession(profile="nv209-gia", email="nguoi.dung@example.com")


# ── BƯỚC 1 (đỏ trên mã cũ): bảng phân loại theo đường dẫn ─────────────────────

@pytest.mark.parametrize("duong_dan, ma_mong, vv_mong", [
    ("/verification/selfie/precollection", "xac_minh_selfie", False),
    ("/verification/selfie/collect", "xac_minh_selfie", False),
    ("/signin/v2/challenge/ipp", "xac_minh_dien_thoai", False),
    ("/signin/v2/challenge/dp", "xac_minh_dien_thoai_tap", False),
    ("/signin/rejected", "dang_nhap_bi_tu_choi", True),
    ("/signin/v2/disabled", "tai_khoan_bi_khoa", True),
    ("/verification/xac-minh-bo-sung", "xac_minh_khac", False),
])
def test_bang_phan_loai_duong_dan(duong_dan, ma_mong, vv_mong):
    assert auto_login._ma_loi_thu_thach_theo_duong_dan(duong_dan) == (ma_mong, vv_mong)


@pytest.mark.parametrize("duong_dan", [
    "/signin/v2/challenge/az",   # TOTP: dịch vụ đã biết xử lý (need_code)
    "/signin/v2/identifier",
    "/signin/challenge/pwd",
    "/",
    "",
])
def test_duong_dan_khong_phai_thu_thach(duong_dan):
    assert auto_login._ma_loi_thu_thach_theo_duong_dan(duong_dan) is None


# ── Phân loại bất đồng bộ: tín hiệu cấu trúc DOM ──────────────────────────────

def test_o_chup_anh_la_tin_hieu_selfie_du_url_la():
    """Ô tải ảnh/camera (tín hiệu cấu trúc) → selfie ngay cả khi URL lạ."""
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/xyz",
                      o_chup_anh=True)
    kq = _chay(auto_login._phan_loai_thu_thach(
        trang, "/signin/v2/challenge/xyz",
        so_dong_chon=0, co_khung_captcha=False))
    assert kq is not None
    ma_loi, vv, dung_ngay = kq
    assert ma_loi == "xac_minh_selfie"
    assert vv is False
    assert dung_ngay is True


def test_trang_thuong_khong_bi_phan_loai():
    trang = _TrangGia("https://accounts.google.com/signin/v2/identifier")
    assert _chay(auto_login._phan_loai_thu_thach(
        trang, "/signin/v2/identifier", so_dong_chon=0,
        co_khung_captcha=False)) is None


def test_khung_captcha_duoc_uu_tien_khong_phan_loai_chong():
    """Trang vừa có khung captcha vừa ở đường dẫn thử thách: luồng captcha giữ
    quyền xử lý, không bị need_human ghi đè."""
    trang = _TrangGia("https://accounts.google.com/verification/selfie/precollection")
    assert _chay(auto_login._phan_loai_thu_thach(
        trang, "/verification/selfie/precollection", so_dong_chon=0,
        co_khung_captcha=True)) is None


# ── Vòng 2FA tích hợp với trang giả ───────────────────────────────────────────

def test_vong_2fa_selfie_dung_ngay_khong_cho_het_tran():
    """Tái hiện đúng bằng chứng đo thật: /verification/selfie/precollection,
    0 dòng chọn, không captcha → need_human NGAY (không quay 240s)."""
    trang = _TrangGia(
        "https://accounts.google.com/verification/selfie/precollection?TL=x",
        chu_body="xac minh danh tinh cua ban")
    ctx = _CtxGia(trang)
    phien = _phien()
    bat_dau = time.time()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=240))
    het = time.time() - bat_dau
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_selfie"
    assert phien.completed_at is not None
    assert het < 60, f"phải dừng ngay, không chờ hết trần (mất {het:.1f}s)"
    assert "selfie" in phien.message.lower()


def test_vong_2fa_tai_khoan_bi_khoa():
    trang = _TrangGia("https://accounts.google.com/signin/v2/disabled?x=1")
    ctx = _CtxGia(trang)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "tai_khoan_bi_khoa"


def test_vong_2fa_bi_tu_choi():
    trang = _TrangGia("https://accounts.google.com/signin/rejected")
    ctx = _CtxGia(trang)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "dang_nhap_bi_tu_choi"


def test_vong_2fa_goi_dien_thoai():
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/ipp?d=1")
    ctx = _CtxGia(trang)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_dien_thoai"


def test_vong_2fa_thu_thach_la_can_vai_vong_ngan():
    """Trang /verification/* lạ: cho vài vòng ngắn rồi mới need_human
    (xac_minh_khac) — không treo tới hết trần."""
    trang = _TrangGia("https://accounts.google.com/verification/xac-minh-bo-sung")
    ctx = _CtxGia(trang)
    phien = _phien()
    bat_dau = time.time()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=120))
    het = time.time() - bat_dau
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_khac"
    assert het < 60, f"chỉ được vài vòng ngắn (mất {het:.1f}s)"


def test_vong_2fa_cham_dien_thoai_dp_thanh_need_tap():
    """dp (device prompt): URL là tín hiệu cấu trúc của «chạm điện thoại»
    → need_tap; người dùng chạm xong thì vòng 2FA thành công."""
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/dp?x=1",
                      chu_body="xac minh")
    ctx = _CtxGia(trang)
    phien = _phien()

    async def kich_ban():
        tac_vu = asyncio.create_task(auto_login._vong_2fa(phien, trang, ctx,
                                                          han_giay=60))
        await asyncio.sleep(4.5)  # xong vòng 1: đã phân loại dp
        assert phien.state == "need_tap", phien.state
        # Người dùng chạm «Có» trên điện thoại: Google chuyển trang + có cookie
        trang.url = "https://myaccount.google.com/"
        ctx._cookie = list(COOKIE_DANG_NHAP)
        return await tac_vu

    assert _chay(kich_ban()) is True


def test_vong_2fa_totp_khong_bi_nham_la_thu_thach():
    """HỒI QUY: /signin/v2/challenge/az (TOTP) chưa kịp hiện ô mã thì KHÔNG
    được phân loại nhầm thành thử thách — giữ hành vi cũ (chờ hết trần)."""
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/az")
    ctx = _CtxGia(trang)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=9))
    assert ok is False
    assert phien.state == "failed"  # hết trần như cũ, không phải need_human
    assert phien.error != "xac_minh_khac"


def test_vong_2fa_trang_app_khong_bi_phan_loai():
    """HỒI QUY: đã vào app (cookie đủ) thì success, không dính need_human."""
    trang = _TrangGia("https://gemini.google.com/app")
    ctx = _CtxGia(trang, cookie=COOKIE_DANG_NHAP)
    phien = _phien()
    assert _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=30)) is True


# ── Đóng tài nguyên trình duyệt khi need_human ────────────────────────────────

def test_run_dong_trinh_duyet_khi_need_human(monkeypatch):
    goi = []

    class PoolGia:
        def dau_dang_nhap(self, p):
            goi.append("dau")

        def xong_dang_nhap(self, p):
            goi.append("xong")

        async def close_profile(self, p, **kw):
            goi.append("close")

    async def run_inner_gia(phien, mat_khau):
        phien.state = "need_human"
        phien.error = "xac_minh_selfie"

    monkeypatch.setattr(auto_login, "pool", PoolGia())
    monkeypatch.setattr(auto_login, "_run_inner", run_inner_gia)
    phien = _phien()
    _chay(auto_login._run(phien, "mat-khau-gia"))
    assert "close" in goi, "need_human phải đóng trình duyệt như failed/success"
