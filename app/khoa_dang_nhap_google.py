"""Khóa serial toàn cục cho đường Google + giữ captcha toàn cục (NV309-c).

SAI S3: trước đây mỗi module *-web chỉ có khóa riêng + hoãn captcha riêng
1800 giây → hai module có thể đăng nhập Google song song, dồn RAM và mời
captcha chùm. c2a tránh điều này bằng `_glogin_serial` (1 phiên tại một thời
điểm, giãn cách 25 giây) + giữ CAPTCHA toàn cục 6 giờ — đọc lấy hiểu từ
`inbox/tai-lieu/c2a-dang-nhap/services/account_recovery.py`, tự viết lại cho
kiến trúc w2a (asyncio, không threading).

MỘT chỗ duy nhất: mọi lượt Google của module (điểm chặn `tai_dung_ho_so`)
đi qua `phien_dang_nhap_google`. Per-module 1800 giây giữ nguyên (lớp dưới).

Phạm vi: khóa này sống trong TIẾN TRÌNH LÕI → serialize các lượt Google của
module. Tiến trình DỊCH VỤ (do_google_login_steps) là tiến trình khác, không
bị khóa này serialize (vẫn bị trần 2 ngữ cảnh của pool) — serialize xuyên
tiến trình cần cơ chế khóa chia sẻ, là follow-up, không tự đoán ở đây.

Chủ hold (`chu`) là EMAIL của tài khoản — khớp cả đường đã lưu lẫn T2 cùng
tài khoản. Lượt không danh tính (chu rỗng) chỉ serial, không đặt hold (không
có chủ để gỡ — tránh hold kẹt 6 giờ không ai gỡ được).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
import os
import time

#: Giãn cách tối thiểu giữa 2 lượt Google (như bấm tay lần lượt — c2a 25s).
GIAN_CACH_GIAY = 25.0
#: Một lượt Google không được giữ serial vô hạn (có thể ghi đè bằng env).
TOI_DA_GIAY = 30 * 60.0
#: Giữ captcha toàn cục (c2a 6 giờ: thử thách theo IP, không phải theo tài khoản).
GIU_CAPTCHA_GIAY = 6 * 3600.0
#: Mã lỗi khi lượt Google bị chặn vì đang giữ captcha (mã ngắn, khớp khuôn
#: chung [a-z0-9_]{1,64}; KHÔNG phải can_nguoi — người đã xử lý captcha đầu
#: rồi, các lượt khác chỉ chờ, không cần người bấm thêm).
MA_LOI_GIU = "cho_captcha_toan_cuc"

#: Tín hiệu captcha/thử thách theo IP (khác 2FA/xác minh theo tài khoản).
TIN_HIEU_CAPTCHA = {"captcha", "cloudflare", "thu_thach_truy_cap"}

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


def _gap_hieu_luc() -> float:
    """Giãn cách hiệu lực: env `WEB2API_GOOGLE_GIAN_CACH_GIAY` (nếu đặt) thắng
    hằng mặc định — test đặt 0 để khỏi ngủ 25 giây mỗi lượt; env sai → mặc định."""
    return _so_huu_han("WEB2API_GOOGLE_GIAN_CACH_GIAY", GIAN_CACH_GIAY)


def _so_huu_han(ten_env: str, mac_dinh: float) -> float:
    try:
        gia_tri = float(os.environ.get(ten_env, "") or mac_dinh)
    except (TypeError, ValueError):
        return mac_dinh
    if not math.isfinite(gia_tri) or gia_tri < 0:
        return mac_dinh
    return gia_tri


def _thoi_gian_toi_da() -> float:
    return _so_huu_han("WEB2API_GOOGLE_TOI_DA_GIAY", TOI_DA_GIAY)


def dinh_danh_chu_hold(email: str = "", ho_so: str = "") -> str:
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


class BiGiuCaptchaToanCuc(Exception):
    """Lượt Google bị chặn vì đang giữ captcha toàn cục (chủ khác)."""


@contextlib.asynccontextmanager
async def phien_dang_nhap_google(chu: str = ""):
    """Một lượt đăng nhập Google: serial toàn cục + giãn cách + kiểm hold.

    - Giữ serial suốt lượt (kể cả vòng chờ tín hiệu) — lượt khác xếp hàng.
    - Trong lock: nếu đang giữ và `chu` không phải chủ → raise
      `BiGiuCaptchaToanCuc` (module dịch thành `MA_LOI_GIU`, không lái trình
      duyệt). Chủ hold được chạy lại (đường hồi phục: người đã tích tay xong
      → lượt này thấy da_dang_nhap → thành công → gỡ hold).
    - Nếu được chạy: ngủ nốt gap từ mốc kết thúc lượt trước rồi yield.
    - Finally: chỉ đóng mốc khi lượt thực sự chạy (lượt bị chặn không đẩy
      mốc); luôn nhả lock.
    """
    global _lan_xong_cuoi
    khoa = _lay_khoa()
    da_gianh = False
    da_chay = False
    try:
        async with asyncio.timeout(_thoi_gian_toi_da()):
            await khoa.acquire()
            da_gianh = True
            if dang_giu() and (chu or "").strip() != _giu_chu:
                raise BiGiuCaptchaToanCuc
            cho = _gap_hieu_luc() - (time.monotonic() - _lan_xong_cuoi) \
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
