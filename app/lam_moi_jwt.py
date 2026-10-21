"""Vòng nền làm mới JWT cho các module AI (NV207).

Kiến trúc theo `app/tu_lam_moi.py`: mỗi module đang chạy có một
`VongLamMoiJWT`; `QuanLyLamMoiJWT` đồng bộ với sổ module mỗi 60 giây
(thêm vòng cho module mới có khả năng, dọn vòng của module đã đi).

Module tham gia bằng phương thức tuỳ chọn (hợp đồng xem docs/CHUAN-MODULE.md):

  * `lam_moi_jwt(khoa) -> dict` — một lượt làm mới cho một khoá; không ném,
    trả dict có `ma` nêu kết quả. Vòng gọi TUẦN TỰ từng khoá (giãn cách 5s),
    không gọi mạng khi chưa tới ngưỡng (module tự quyết bằng claim exp).

Lõi KHÔNG chứa tên nhà cung cấp nào.

Biến môi trường (đọc mỗi lần gọi):

  * `WEB2API_JWT_LAM_MOI_BAT` — "1"/"0", mặc định "1".
  * `WEB2API_JWT_LAM_MOI_KHOANG_GIAY` — chu kỳ vòng, mặc định 21600 (6 giờ);
    tối thiểu 300.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable

log = logging.getLogger("web2api.lam_moi_jwt")


def _lay_bool_env(ten: str, mac_dinh: bool) -> bool:
    val = os.environ.get(ten, "").strip().lower()
    if not val:
        return mac_dinh
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return mac_dinh


def _lay_so_env(ten: str, mac_dinh: float, toi_thieu: float) -> float:
    val = os.environ.get(ten, "").strip()
    if not val:
        return mac_dinh
    try:
        so = float(val)
    except ValueError:
        return mac_dinh
    if so != so:  # NaN
        return mac_dinh
    return max(toi_thieu, so)


def lam_moi_bat() -> bool:
    """Có bật vòng làm mới JWT không (mặc định bật)."""
    return _lay_bool_env("WEB2API_JWT_LAM_MOI_BAT", True)


def khoang_giay() -> float:
    """Chu kỳ vòng (giây); tối thiểu 300 để không thành vòng lặp bận."""
    return _lay_so_env("WEB2API_JWT_LAM_MOI_KHOANG_GIAY", 21600.0, 300.0)


# ── Single-flight: một lượt làm mới cho mỗi (module, khoá) ──────────────────

_khoa_luong = threading.Lock()
_dang_chay: dict[tuple[str, str], asyncio.Task] = {}


def _lay_dang_chay() -> dict[tuple[str, str], asyncio.Task]:
    with _khoa_luong:
        return {k: t for k, t in _dang_chay.items() if not t.done()}


class VongLamMoiJWT:
    """Một vòng làm mới JWT định kỳ cho một module.

    `ngu`: hàm ngủ chích được cho test (mặc định `asyncio.sleep`).
    Mỗi vòng: liệt kê `module.khoa_tai_khoan()` (thiếu → bỏ qua module), gọi
    `module.lam_moi_jwt(khoa)` tuần tự cho từng khoá chưa có lượt chạy
    (single-flight theo cặp (module_id, khoa)), giãn cách 5 giây giữa các khoá.
    """

    GIAN_CACH_KHOA_GIAY = 5.0

    def __init__(self, module: Any, module_id: str, *,
                 ngu: Callable[[float], Any] | None = None):
        self._module = module
        self._module_id = module_id
        self._ngu = ngu or asyncio.sleep
        self._dung_co = False
        self._task: asyncio.Task | None = None
        self.da_dung = False  # test quan sát

    def bat_dau(self) -> None:
        """Tạo tác vụ vòng lặp trong vòng lặp sự kiện đang chạy."""
        if self._task is not None and not self._task.done():
            return
        self._dung_co = False
        self.da_dung = False
        self._task = asyncio.get_running_loop().create_task(self._vong_lap())

    def dung(self) -> None:
        """Dừng gọn: đặt cờ + huỷ tác vụ (không để mồ côi)."""
        self._dung_co = True
        self.da_dung = True
        if self._task is not None:
            self._task.cancel()

    async def _vong_lap(self) -> None:
        try:
            while not self._dung_co:
                await self.chay_mot_vong()
                await self._ngu(khoang_giay())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("lam-moi-jwt: vòng %s lỗi: %s", self._module_id,
                        type(e).__name__)
        finally:
            self.da_dung = True

    async def chay_mot_vong(self) -> dict:
        """Chạy một lượt làm mới qua các khoá. Trả thống kê {ma: dem}."""
        thong_ke: dict[str, int] = {}
        if not lam_moi_bat():
            return thong_ke
        lam_moi = getattr(self._module, "lam_moi_jwt", None)
        if not callable(lam_moi):
            return thong_ke
        lay_khoa = getattr(self._module, "khoa_tai_khoan", None)
        if not callable(lay_khoa):
            return thong_ke
        try:
            cac_khoa = lay_khoa()
        except Exception as e:
            log.warning("lam-moi-jwt: khoa_tai_khoan của %s lỗi: %s",
                        self._module_id, type(e).__name__)
            return thong_ke
        if not isinstance(cac_khoa, (list, tuple)):
            return thong_ke
        for khoa in cac_khoa:
            if self._dung_co:
                break
            khoa = str(khoa)
            key = (self._module_id, khoa)
            with _khoa_luong:
                task_cu = _dang_chay.get(key)
                dang = task_cu is not None and not task_cu.done()
            if dang:
                thong_ke["dang_chay"] = thong_ke.get("dang_chay", 0) + 1
                continue
            try:
                task = asyncio.get_running_loop().create_task(
                    self._goi_an_toan(lam_moi, khoa))
            except RuntimeError:
                log.warning("lam-moi-jwt: không có vòng lặp sự kiện đang chạy")
                break
            with _khoa_luong:
                _dang_chay[key] = task
            try:
                kq = await asyncio.shield(task)
            finally:
                with _khoa_luong:
                    _dang_chay.pop(key, None)
            ma = (kq or {}).get("ma") or "khong_ro"
            thong_ke[ma] = thong_ke.get(ma, 0) + 1
            await self._ngu(self.GIAN_CACH_KHOA_GIAY)
        return thong_ke

    async def _goi_an_toan(self, lam_moi: Callable, khoa: str) -> dict:
        try:
            kq = await lam_moi(khoa)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("lam-moi-jwt: %s/%s lỗi không ngờ: %s",
                        self._module_id, khoa, type(e).__name__)
            return {"ok": False, "ma": "loi_noi_bo"}
        return kq if isinstance(kq, dict) else {"ok": False, "ma": "tra_ve_la"}


class QuanLyLamMoiJWT:
    """Giữ một VongLamMoiJWT cho mỗi module đang chạy có `lam_moi_jwt`.

    Đồng bộ định kỳ với sổ module (mỗi 60s): module mới/gỡ thì thêm/bớt vòng —
    không để tác vụ nền mồ côi khi gỡ module giữa chừng.
    """

    CHU_KY_DONG_BO = 60.0

    def __init__(self):
        self._vong: dict[str, VongLamMoiJWT] = {}
        self._task: asyncio.Task | None = None
        self._dung_co = False
        self._lay_dang_chay: Callable[[], dict] | None = None

    def bat_dau(self, lay_dang_chay: Callable[[], dict]) -> None:
        """`lay_dang_chay`: () -> {module_id: module}. Tạo tác vụ đồng bộ."""
        if self._task is not None and not self._task.done():
            return
        self._lay_dang_chay = lay_dang_chay
        self._dung_co = False
        self._task = asyncio.get_running_loop().create_task(
            self._vong_dong_bo())

    async def dung(self) -> None:
        """Dừng gọn: dừng mọi vòng rồi huỷ tác vụ đồng bộ."""
        self._dung_co = True
        for vong in list(self._vong.values()):
            vong.dung()
        self._vong.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _vong_dong_bo(self) -> None:
        try:
            while not self._dung_co:
                try:
                    lay = self._lay_dang_chay or (lambda: {})
                    self.dong_hoa(lay())
                except Exception as e:
                    log.warning("lam-moi-jwt: đồng bộ vòng lỗi: %s",
                                type(e).__name__)
                await asyncio.sleep(self.CHU_KY_DONG_BO)
        except asyncio.CancelledError:
            pass

    def dong_hoa(self, dang_chay: dict) -> None:
        """Thêm vòng cho module mới có lam_moi_jwt; dừng vòng của module đã đi
        (gỡ/dừng) hoặc không còn khả năng làm mới JWT."""
        dang_chay = dang_chay or {}
        for module_id in list(self._vong):
            module = dang_chay.get(module_id)
            if module is None or not callable(getattr(module, "lam_moi_jwt", None)) \
                    or self._vong[module_id]._module is not module:
                self.go_vong(module_id)
        for module_id, module in dang_chay.items():
            if module_id not in self._vong and \
                    callable(getattr(module, "lam_moi_jwt", None)):
                vong = VongLamMoiJWT(module, module_id)
                self._vong[module_id] = vong
                vong.bat_dau()

    def go_vong(self, module_id: str) -> bool:
        """Dừng và bỏ vòng của một module. Trả True nếu đã có vòng."""
        vong = self._vong.pop(module_id, None)
        if vong is None:
            return False
        vong.dung()
        return True

    def cac_vong(self) -> dict[str, VongLamMoiJWT]:
        return dict(self._vong)


_quan_ly: QuanLyLamMoiJWT | None = None
_khoa_quan_ly = threading.Lock()


def quan_ly() -> QuanLyLamMoiJWT:
    """Quản lý vòng làm mới JWT dùng chung (singleton theo tiến trình)."""
    global _quan_ly
    with _khoa_quan_ly:
        if _quan_ly is None:
            _quan_ly = QuanLyLamMoiJWT()
        return _quan_ly


async def huy_tat_ca() -> int:
    """Huỷ mọi lượt làm mới đang chạy (dọn khi tắt máy / cuối test)."""
    with _khoa_luong:
        muc = list(_dang_chay.items())
    for _, t in muc:
        t.cancel()
    for _, t in muc:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    return len(muc)
