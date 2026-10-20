"""Bộ lập lịch chạy nền của module tu-van-hanh.

Port ý tưởng từ `services/web_session_scheduler.py` của c2a (kho của chính chủ máy):
- Mỗi nhiệm vụ có chu kỳ riêng, lùi dần khi lỗi, reset khi thành công.
- Khởi động nhiều lần cũng chỉ chạy một (single-flight).
- Dừng sạch, không luồng/tác vụ mồ côi.

Đổi lớp kết nối: c2a dùng `threading.Thread` + `time.sleep`; ở đây dùng
`asyncio.Task` cho hợp với vòng sự kiện của web2api (tránh luồng mồ côi khi
dừng tiến trình). Đồng hồ và hàm ngủ tiêm được để test không ngủ thật
(theo mẫu `VongHoiVong` của lõi ở app/kenh_vao.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

#: Lùi tối đa khi nhiệm vụ lỗi liên tiếp (giống VongHoiVong của lõi).
LUI_TOI_DA = 300.0


class LichTrinh:
    def __init__(self, lay_gio: Callable[[], float] | None = None,
                 ngu: Callable[[float], Awaitable[None]] | None = None):
        self._lay_gio = lay_gio or time.time
        self._ngu = ngu or asyncio.sleep
        self._nhiem_vu: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._dung_co = False

    def them(self, ten: str, chu_ky_giay: float, ham: Callable[[], Awaitable[Any]]) -> None:
        """Đăng ký nhiệm vụ. Gọi lại cùng tên thì ghi đè (không trùng)."""
        self._nhiem_vu[ten] = {
            "chu_ky": max(1.0, float(chu_ky_giay)),
            "ham": ham,
            "lan_cuoi": 0.0,
            "lui": 0.0,
        }

    def bat_dau(self) -> None:
        """Khởi động vòng lặp nền. Gọi nhiều lần cũng chỉ chạy một."""
        if self._task is not None and not self._task.done():
            return
        self._dung_co = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("tu-van-hanh: không có vòng sự kiện đang chạy — lịch chưa khởi động")
            return
        self._task = loop.create_task(self._vong())

    async def dung(self) -> None:
        """Dừng vòng lặp nền (idempotent) — không để tác vụ mồ côi."""
        self._dung_co = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def so_tac_vu_chay(self) -> int:
        return 1 if (self._task is not None and not self._task.done()) else 0

    async def chay_mot_vong(self) -> dict[str, str]:
        """Chạy các nhiệm vụ đã tới hạn (dùng cho test với đồng hồ giả).

        Trả {tên: "ok" | "loi"}.
        """
        ket_qua: dict[str, str] = {}
        bay_gio = self._lay_gio()
        for ten, nv in self._nhiem_vu.items():
            chu_ky = nv["lui"] or nv["chu_ky"]
            if bay_gio - nv["lan_cuoi"] < chu_ky:
                continue
            nv["lan_cuoi"] = bay_gio
            try:
                await nv["ham"]()
                nv["lui"] = 0.0
                ket_qua[ten] = "ok"
            except Exception as e:
                # Nguyên tắc (lỗi 3, soát độc lập): lùi dần không bao giờ được
                # NHANH hơn chu kỳ gốc — trần lùi là max(LUI_TOI_DA, chu_ky).
                # Trước đây trần cứng 300s ép nhiệm vụ chu kỳ 1800/3600s chạy
                # dày hơn 6–12 lần khi lỗi (tự gây quá tải).
                tran = max(LUI_TOI_DA, nv["chu_ky"])
                nv["lui"] = min(max((nv["lui"] or nv["chu_ky"]) * 2, nv["chu_ky"]), tran)
                log.warning("tu-van-hanh: nhiệm vụ %s lỗi (%s), lùi %ss",
                            ten, type(e).__name__, nv["lui"])
                ket_qua[ten] = "loi"
        return ket_qua

    async def _vong(self) -> None:
        try:
            while not self._dung_co:
                await self.chay_mot_vong()
                await self._ngu(1.0)
        except asyncio.CancelledError:
            pass
