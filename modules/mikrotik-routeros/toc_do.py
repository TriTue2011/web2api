"""Tính tốc độ từ bộ đếm byte và xử lý thời lượng RouterOS (hàm thuần).

Quy tắc vật lý:
- Tốc độ Mbit/s tính từ hiệu bộ đếm byte giữa hai lần đo: delta * 8 / dt / 1e6.
- Xử lý tràn bộ đếm (64-bit hoặc 32-bit): delta = (2**bit - truoc) + nay.
- Giới hạn trần vật lý TRAN_VAT_LY = 400_000 Mbit/s (400 Gbit/s). Vượt trần → coi là reset bộ đếm.
- Nhận biết router khởi động lại khi thời gian chạy (uptime) giảm → trả None để đặt lại mẫu gốc.
"""
from __future__ import annotations

import re
from typing import Any

# Trần vật lý cổng mạng RouterOS: 400 Gbit/s (400_000 Mbit/s).
# Nếu tốc độ suy ra vượt trần này (ví dụ do bộ đếm giảm mà không phải tràn hợp lệ),
# coi là đặt lại bộ đếm hoặc không hợp lý và bỏ mẫu.
TRAN_VAT_LY = 400_000.0

_KHUON_DANG_CHU = re.compile(
    r"^(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?(?:(\d+)ms)?$"
)
_KHUON_DANG_DONG_HO = re.compile(
    r"^(?:(?:(\d+)d\s*)?(\d{1,2}):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$"
)


def _trich_mau(m: Any) -> tuple[int | None, float | None, int | None]:
    """Trích (gia_tri_byte, giay, uptime) từ tuple hoặc object."""
    if m is None:
        return None, None, None
    if hasattr(m, "gia_tri") and hasattr(m, "giay"):
        v = getattr(m, "gia_tri")
        t = getattr(m, "giay")
        u = getattr(m, "thoi_gian_chay_router_giay", None)
    elif isinstance(m, (tuple, list)) and len(m) >= 2:
        v = m[0]
        t = m[1]
        u = m[2] if len(m) >= 3 else None
    else:
        return None, None, None

    # Kiểm tra kiểu
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return None, None, None
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0:
        return None, None, None
    if u is not None:
        if isinstance(u, bool) or not isinstance(u, (int, float)) or u < 0:
            return None, None, None
        u = int(u)
    return v, float(t), u


def tinh_toc_do_mbit(
    truoc: Any,
    nay: Any,
    *,
    bit: int = 64,
) -> float | None:
    """Tính tốc độ truyền tải Mbit/s từ bộ đếm byte.

    truoc, nay: bộ (gia_tri_byte: int, giay: float, thoi_gian_chay_router_giay: int | None).
    """
    nay_v, nay_t, nay_u = _trich_mau(nay)
    if nay_v is None or nay_t is None:
        return None

    if truoc is None:
        return None

    truoc_v, truoc_t, truoc_u = _trich_mau(truoc)
    if truoc_v is None or truoc_t is None:
        return None

    dt = nay_t - truoc_t
    if dt < 1.0:
        # Khoảng cách < 1 giây: chưa đủ độ tin cậy để tính tốc độ
        return None

    # Kiểm tra router khởi động lại: uptime giảm
    if truoc_u is not None and nay_u is not None and nay_u < truoc_u:
        return None

    if nay_v >= truoc_v:
        delta = nay_v - truoc_v
    else:
        # Bộ đếm giảm: có thể tràn bộ đếm (bit=64 hoặc bit=32)
        delta = (2**bit - truoc_v) + nay_v

    if delta < 0:
        return None

    mbit = (delta * 8.0) / (dt * 1_000_000.0)
    if mbit > TRAN_VAT_LY:
        # Vượt trần vật lý: tràn không hợp lý hoặc đặt lại bộ đếm
        return None

    return round(mbit, 3)


def doc_thoi_luong_giay(xau: Any) -> int | None:
    """Chuyển chuỗi thời lượng RouterOS thành số giây nguyên.

    Hỗ trợ cả dạng «1w2d3h4m5s» (w, d, h, m, s, ms) và dạng «[Nd ]hh:mm:ss».
    Trả về None nếu rỗng, «never», «none» hoặc sai khuôn.
    """
    if not isinstance(xau, str):
        return None
    s = xau.strip().lower()
    if not s or s in ("never", "none", "unknown"):
        return None

    # 1. Thử khuôn dạng chữ RouterOS (1w2d3h4m5s)
    m = _KHUON_DANG_CHU.match(s)
    if m and any(m.groups()):
        w, d, h, phut, giay, ms = m.groups()
        tong = (
            (int(w) if w else 0) * 7 * 86400
            + (int(d) if d else 0) * 86400
            + (int(h) if h else 0) * 3600
            + (int(phut) if phut else 0) * 60
            + (int(giay) if giay else 0)
        )
        if ms:
            tong += int((int(ms) + 500) // 1000)
        return tong

    # 2. Thử khuôn dạng đồng hồ (2d03:14:25 hoặc 03:14:25 hoặc 14:25)
    m = _KHUON_DANG_DONG_HO.match(s)
    if m:
        d, h, phut, giay, ms = m.groups()
        tong = (
            (int(d) if d else 0) * 86400
            + (int(h) if h else 0) * 3600
            + (int(phut) if phut else 0) * 60
            + (int(giay) if giay else 0)
        )
        return tong

    return None
