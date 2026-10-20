"""Thời gian theo múi giờ nhà (Asia/Ho_Chi_Minh) — kỳ lương và mốc ghi đều theo giờ nhà.

Không có trong c2a (bản gốc dùng giờ cục bộ của tiến trình); ở đây neo rõ vào giờ Việt Nam để
kỳ lương không lệch khi container chạy UTC. Chỉ dùng thư viện chuẩn (zoneinfo; dự phòng
UTC+7 cố định — Việt Nam không có giờ mùa).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    MUI_GIO_NHA = ZoneInfo("Asia/Ho_Chi_Minh")  # type: ignore[assignment]
except Exception:  # noqa: BLE001 — môi trường thiếu tzdata
    MUI_GIO_NHA = timezone(timedelta(hours=7))  # type: ignore[assignment]


def bay_gio_dt() -> datetime:
    return datetime.now(MUI_GIO_NHA)


def bay_gio() -> str:
    return bay_gio_dt().isoformat(timespec="seconds")
