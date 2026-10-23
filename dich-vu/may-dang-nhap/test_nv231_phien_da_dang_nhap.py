# test_nv231_phien_da_dang_nhap.py — NV231: Google hiện trang XÁC MINH DANH
# TÍNH nhưng PHIÊN ĐÃ ĐĂNG NHẬP → tính là THÀNH CÔNG (không cần người).
#
# Chủ máy 21/10: «Phần xác minh khuôn mặt không cần, như thế là đăng nhập
# được rồi». Nguyên tắc: KIỂM KẾT QUẢ THẬT (cookie phiên Google), không đoán
# theo URL. Không vượt/bỏ qua thử thách, không tự điền selfie/điện thoại,
# không giải CAPTCHA — chỉ nhận ra rằng phiên ĐÃ có.
#
# BẢNG ĐO:
#  (1) /verification/selfie/* + cookie phiên hợp lệ  → success (bỏ qua trang
#      xác minh), KHÔNG bấm/điền gì
#  (2) cùng trang + KHÔNG cookie phiên               → need_human (như NV209)
#  (3) /signin/rejected, /signin/v2/disabled + cookie CŨ → vẫn lỗi VĨNH VIỄN
#      (không biến thành success)
#  (4) /verification/* lạ + có phiên                 → success
#      /verification/* lạ + không phiên               → vài vòng ngắn rồi
#      need_human (như NV209)
#  (5) cookie đăng nhập có nhưng HẾT HẠN / rỗng / sai miền → KHÔNG success
#  (6) không log cookie/URL có TL= (mã phiên)
from __future__ import annotations

import asyncio
import logging
import time

import pytest

import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)
from src import auto_login
from src.auto_login import LoginSession


def _chay(coro):
    return asyncio.run(coro)


# ── Trang / ctx giả ───────────────────────────────────────────────────────────

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
    def __init__(self, url, *, o_chup_anh=False, chu_body="", o_ma=False):
        self._url = url
        self._o_chup_anh = o_chup_anh
        self._chu_body = chu_body
        self._o_ma = o_ma

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
    """Context giả: cookie có đủ trường như Playwright thật
    (name/value/domain/expires)."""

    def __init__(self, trang, *, cookie=()):
        self._trang = trang
        self._cookie = list(cookie)

    @property
    def pages(self):
        return [self._trang]

    async def cookies(self):
        return list(self._cookie)


def _cookie_phien(ten="__Secure-1PSID", gia_tri="gia-tri-bi-mat",
                  mien=".google.com", het_han=None):
    ck = {"name": ten, "value": gia_tri, "domain": mien, "path": "/",
          "expires": -1, "httpOnly": True, "secure": True, "sameSite": "None"}
    if het_han is not None:
        ck["expires"] = het_han
    return ck


COOKIE_PHIEU_HOP_LE = (
    _cookie_phien("__Secure-1PSID", het_han=time.time() + 3600 * 24 * 30),
    _cookie_phien("SID", het_han=time.time() + 3600 * 24 * 30),
)
COOKIE_PHIEU_HET_HAN = (
    _cookie_phien("__Secure-1PSID", het_han=time.time() - 3600),
)
COOKIE_PHIEU_RONG = (
    _cookie_phien("__Secure-1PSID", gia_tri="", het_han=time.time() + 3600),
)
COOKIE_PHIEU_SAI_MIEN = (
    _cookie_phien("SID", mien=".example.com", het_han=time.time() + 3600),
)


def _phien():
    return LoginSession(profile="nv231-gia", email="nguoi.dung@example.com")


# ── (1) trang selfie + phiên hợp lệ → success ─────────────────────────────────

def test_selfie_co_phien_thanh_success_khong_can_nguoi():
    trang = _TrangGia(
        "https://accounts.google.com/verification/selfie/precollection?TL=x",
        chu_body="xac minh danh tinh cua ban")
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is True
    assert phien.state == "success"
    assert "bỏ qua trang xác minh" in phien.message


def test_xac_minh_dien_thoai_ipp_co_phien_thanh_success():
    """Mọi mục «xac_minh_*» đều được kiểm phiên trước khi kết luận need_human."""
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/ipp?d=1")
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is True
    assert phien.state == "success"


# ── (2) cùng trang, không phiên → need_human như NV209 ────────────────────────

def test_selfie_khong_phien_van_need_human():
    trang = _TrangGia(
        "https://accounts.google.com/verification/selfie/precollection?TL=x")
    ctx = _CtxGia(trang)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_selfie"
    assert "bỏ qua" not in phien.message


# ── (3) lỗi vĩnh viễn không biến thành success dù có cookie cũ ───────────────

@pytest.mark.parametrize("url, ma", [
    ("https://accounts.google.com/signin/rejected", "dang_nhap_bi_tu_choi"),
    ("https://accounts.google.com/signin/v2/disabled?x=1", "tai_khoan_bi_khoa"),
])
def test_loi_vinh_vien_khong_thanh_success_du_co_cookie(url, ma):
    trang = _TrangGia(url)
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == ma


# ── (4) /verification/ lạ ────────────────────────────────────────────────────

def test_thu_thach_la_co_phien_thanh_success():
    trang = _TrangGia("https://accounts.google.com/verification/xac-minh-bo-sung")
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is True
    assert phien.state == "success"


def test_thu_thach_la_khong_phien_vai_vong_roi_need_human():
    """Không phiên: giữ hành vi NV209 — vài vòng ngắn rồi need_human."""
    trang = _TrangGia("https://accounts.google.com/verification/xac-minh-bo-sung")
    ctx = _CtxGia(trang)
    phien = _phien()
    bat_dau = time.time()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=120))
    het = time.time() - bat_dau
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_khac"
    assert het < 60


# ── (5) cookie hết hạn / rỗng / sai miền → KHÔNG success ─────────────────────

@pytest.mark.parametrize("cookie", [
    COOKIE_PHIEU_HET_HAN,
    COOKIE_PHIEU_RONG,
    COOKIE_PHIEU_SAI_MIEN,
])
def test_cookie_khong_dung_duoc_khong_thanh_success(cookie):
    trang = _TrangGia(
        "https://accounts.google.com/verification/selfie/precollection?TL=x")
    ctx = _CtxGia(trang, cookie=cookie)
    phien = _phien()
    ok = _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    assert ok is False
    assert phien.state == "need_human"
    assert phien.error == "xac_minh_selfie"


# ── _co_phien_google: kiểm đơn vị ────────────────────────────────────────────

def test_co_phien_google_cookie_phien_dung():
    trang = _TrangGia("https://www.google.com/")
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    assert _chay(auto_login._co_phien_google(ctx)) is True


@pytest.mark.parametrize("ten", ["__Secure-1PSID", "__Secure-3PSID", "SID"])
def test_co_phien_google_tung_ten_trong_bang(ten):
    """Từng tên trong bảng _GOOGLE_LOGIN_COOKIES đứng MỘT MÌNH vẫn là phiên.

    Bổ sung sau đột biến M5 (rút bảng chỉ còn "SID" mà không test nào đỏ):
    thiếu một tên trong bảng = phiên thật chỉ có cookie đó bị coi là chưa
    đăng nhập → need_human oan.
    """
    trang = _TrangGia("https://www.google.com/")
    ctx = _CtxGia(trang, cookie=(
        _cookie_phien(ten, het_han=time.time() + 3600),))
    assert _chay(auto_login._co_phien_google(ctx)) is True


def test_co_phien_google_session_cookie_khong_expires():
    """Cookie phiên (expires=-1 như Playwright thật) vẫn tính là hợp lệ."""
    trang = _TrangGia("https://www.google.com/")
    ctx = _CtxGia(trang, cookie=(_cookie_phien("SID"),))
    assert _chay(auto_login._co_phien_google(ctx)) is True


@pytest.mark.parametrize("cookie", [
    (),
    COOKIE_PHIEU_HET_HAN,
    COOKIE_PHIEU_RONG,
    COOKIE_PHIEU_SAI_MIEN,
    (_cookie_phien("ten-la"),),
])
def test_co_phien_google_sai_thi_false(cookie):
    trang = _TrangGia("https://www.google.com/")
    ctx = _CtxGia(trang, cookie=cookie)
    assert _chay(auto_login._co_phien_google(ctx)) is False


def test_co_phien_google_ctx_loi_thi_false():
    class CtxLoi:
        async def cookies(self):
            raise RuntimeError("mang hong")

    assert _chay(auto_login._co_phien_google(CtxLoi())) is False


# ── (6) không lộ cookie / URL có TL= vào log ─────────────────────────────────

def test_khong_log_cookie_va_url_co_tl(caplog):
    url = ("https://accounts.google.com/verification/selfie/precollection"
           "?TL=ma-phien-bi-mat-xyz")
    trang = _TrangGia(url, chu_body="xac minh")
    ctx = _CtxGia(trang, cookie=COOKIE_PHIEU_HOP_LE)
    phien = _phien()
    with caplog.at_level(logging.INFO, logger="src.auto_login"):
        _chay(auto_login._vong_2fa(phien, trang, ctx, han_giay=60))
    ban_ghi = "\n".join(r.getMessage() for r in caplog.records)
    assert "TL=" not in ban_ghi
    assert "ma-phien-bi-mat-xyz" not in ban_ghi
    assert "gia-tri-bi-mat" not in ban_ghi
    assert "gia-tri-bi-mat" not in phien.message


# ── Hồi quy: chạm điện thoại vẫn need_tap khi chưa có phiên ──────────────────

def test_dp_chua_co_phien_van_need_tap():
    """dp: chưa có phiên → giữ need_tap (không bị NV231 biến thành success)."""
    trang = _TrangGia("https://accounts.google.com/signin/v2/challenge/dp?x=1",
                      chu_body="xac minh")
    ctx = _CtxGia(trang)
    phien = _phien()

    async def kich_ban():
        tac_vu = asyncio.create_task(
            auto_login._vong_2fa(phien, trang, ctx, han_giay=30))
        await asyncio.sleep(4.5)
        assert phien.state == "need_tap", phien.state
        tac_vu.cancel()
        try:
            await tac_vu
        except asyncio.CancelledError:
            pass

    _chay(kich_ban())
