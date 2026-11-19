"""Điều phối tuần tự cho các lượt đăng nhập dùng chung (NV309-c)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
import os
import time

#: Khoảng nghỉ tối thiểu giữa các lượt.
GIAN_CACH_GIAY = 25.0
#: Thời lượng tối đa một lượt giữ khóa.
TOI_DA_GIAY = 30 * 60.0
#: Thời lượng giữ trạng thái chờ người.
GIU_CAPTCHA_GIAY = 6 * 3600.0
MA_LOI_GIU = "cho_captcha_toan_cuc"
# Tín hiệu chung của thử thách theo IP; xác minh theo tài khoản không giữ lượt khác.
TIN_HIEU_CAPTCHA = {"captcha", "thu_thach_truy_cap"}

_khoa_serial: asyncio.Lock | None = None
_lan_xong_cuoi: float = 0.0
_giu_den: float = 0.0
_giu_chu: str = ""


def _lay_khoa() -> asyncio.Lock:
    global _khoa_serial
    if _khoa_serial is None:
        _khoa_serial = asyncio.Lock()
    return _khoa_serial


def dat_lai() -> None:
    """Reset trạng thái singleton (cho test; production không gọi)."""
    global _lan_xong_cuoi, _giu_den, _giu_chu
    _lan_xong_cuoi = 0.0
    _giu_den = 0.0
    _giu_chu = ""


def _so_huu_han(gia_tri, mac_dinh: float) -> float:
    try:
        gia_tri = float(gia_tri or mac_dinh)
    except (TypeError, ValueError):
        return mac_dinh
    if not math.isfinite(gia_tri) or gia_tri < 0:
        return mac_dinh
    return gia_tri


def _gap_hieu_luc() -> float:
    return _so_huu_han(
        os.environ.get("WEB2API_KHOA_DANG_NHAP_GIAN_CACH_GIAY"), GIAN_CACH_GIAY)


def _thoi_gian_toi_da() -> float:
    return _so_huu_han(
        os.environ.get("WEB2API_KHOA_DANG_NHAP_TOI_DA_GIAY"), TOI_DA_GIAY)


def dinh_danh_chu(email: str = "", ho_so: str = "") -> str:
    """Định danh ổn định của một lượt: email + hồ sơ, không dùng lẫn ID.

    Một email có thể có nhiều hồ sơ trình duyệt; ghép cả hai ngăn hồ sơ này
    gỡ hold của hồ sơ kia trong khi vẫn dùng cùng một quy tắc ở mọi đường gọi.
    """
    if isinstance(ho_so, dict):
        ho_so = ho_so.get("id") or ""
    phan = f"{(email or '').strip().casefold()}\x00{str(ho_so or '').strip()}"
    if not phan.strip("\x00"):
        return ""
    return hashlib.sha256(phan.encode("utf-8")).hexdigest()


def dang_giu() -> bool:
    """Đang trong thời gian giữ captcha toàn cục?"""
    return time.time() < _giu_den


def thong_tin_giu() -> tuple[bool, str, int]:
    """(đang_giữ, chủ_hold, số_phút_còn_lại) — chỉ dùng nội bộ để so khớp và
    log kỹ thuật; KHÔNG đưa email chủ hold vào thông điệp gửi người dùng
    (tránh lộ email tài khoản khác)."""
    if not dang_giu():
        return False, "", 0
    con = max(0, int((_giu_den - time.time()) / 60))
    return True, _giu_chu, con


def dat_giu_captcha(chu: str = "") -> None:
    """Đặt giữ captcha toàn cục 6 giờ cho `chu` (chủ hold).

    Chỉ khi `chu` khác rỗng: lượt không danh tính gặp captcha vẫn trả
    can_nguoi như cũ nhưng không đặt hold toàn cục.
    """
    global _giu_den, _giu_chu
    chu = (chu or "").strip()
    if not chu:
        return
    _giu_den = time.time() + GIU_CAPTCHA_GIAY
    _giu_chu = chu


def bo_giu_neu_chu(chu: str = "") -> None:
    """Gỡ hold nếu `chu` đúng là chủ hold (chủ đã đăng nhập xong)."""
    global _giu_den, _giu_chu
    chu = (chu or "").strip()
    if chu and chu == _giu_chu:
        _giu_den = 0.0
        _giu_chu = ""


class BiGiuTrangThai(Exception):
    """Lượt bị chặn vì trạng thái giữ đang thuộc về một chủ khác."""


@contextlib.asynccontextmanager
async def phien_dang_nhap(chu: str = "", *, gap_giay=None,
                           toi_da_giay=None):
    """Một lượt đăng nhập: serial chung + giãn cách + kiểm trạng thái giữ.

    - Giữ serial suốt lượt (kể cả vòng chờ tín hiệu) — lượt khác xếp hàng.
    - Trong lock: nếu đang giữ và `chu` không phải chủ → raise
      `BiGiuTrangThai` (module dịch thành `MA_LOI_GIU`, không lái trình
      duyệt). Chủ hold được chạy lại (đường hồi phục: người đã tích tay xong
      → lượt này thấy da_dang_nhap → thành công → gỡ hold).
    - Nếu được chạy: ngủ nốt gap từ mốc kết thúc lượt trước rồi yield.
    - Finally: chỉ đóng mốc khi lượt thực sự chạy (lượt bị chặn không đẩy
      mốc); luôn nhả lock.
    """
    global _lan_xong_cuoi
    toi_da = (_so_huu_han(toi_da_giay, TOI_DA_GIAY)
              if toi_da_giay is not None else _thoi_gian_toi_da())
    gap = (_so_huu_han(gap_giay, GIAN_CACH_GIAY)
           if gap_giay is not None else _gap_hieu_luc())
    khoa = _lay_khoa()
    da_gianh = False
    da_chay = False
    try:
        async with asyncio.timeout(toi_da):
            await khoa.acquire()
            da_gianh = True
            if dang_giu() and (chu or "").strip() != _giu_chu:
                raise BiGiuTrangThai
            cho = gap - (time.monotonic() - _lan_xong_cuoi) \
                if _lan_xong_cuoi else 0.0
            if cho > 0:
                await asyncio.sleep(cho)
            da_chay = True
            yield
    finally:
        if da_chay:
            _lan_xong_cuoi = time.monotonic()
        if da_gianh:
            khoa.release()
