"""Tự động khôi phục / làm mới phiên tài khoản provider nhiều tầng (NV91-P2).

Nối vào đường chạy THẬT: khi một lời gọi chat của module gặp LỖI PHIÊN nhận ra
bằng CẤU TRÚC (mã 401/403 theo nhóm phiên mà module đã khai — module tự phát
hiện, không dò chữ), module gọi `bao_loi_phien(module, khoa)`. Lõi khi đó:

  * chạy `app.khoi_phuc_phien.khoi_phuc` cho ĐÚNG tài khoản đó MỘT lần
    (single-flight: nhiều yêu cầu cùng lúc chỉ kích hoạt một lượt; có thời gian
    nghỉ `WEB2API_TU_LAM_MOI_NGHI_GIAY` giữa các lượt), rồi module tự chuyển
    tài khoản/khoá kế theo quy tắc của nó (chỉ đổi TRƯỚC byte đầu stream; không
    chặn lựa chọn cuối; không bịa hạn mức);
  * vòng làm mới ĐỊNH KỲ nền (`VongTuLamMoi`, một vòng cho mỗi module): gọi
    `lam_moi_phien` cho tài khoản lâu chưa kiểm; tôn trọng chốt dừng (2 hỏng
    liên tiếp tầng b → ngừng tự đăng nhập tài khoản đó); giãn cách giữa các
    tài khoản; không vòng lặp bận; tắt gọn khi module dừng/gỡ;
  * hỏng tới tầng (d) `can_nguoi` → ghi nhật ký quản trị + trạng thái «cần
    người»; KHÔNG tự thử lại mãi.

Lõi KHÔNG chứa tên nhà cung cấp nào. Module tham gia bằng cách triển khai các
phương thức tuỳ chọn của hợp đồng NV88 (`lam_moi_phien`, `dang_nhap_tu_dong`,
…) và gọi `bao_loi_phien` ở nhánh lỗi phiên của đường chat; thêm hai phương
thức tuỳ chọn mới của NV91 (xem `docs/CHUAN-MODULE.md`):

  * `khoa_tai_khoan() -> list[str]` — khoá mờ ổn định của các tài khoản (để vòng
    định kỳ liệt kê; thiếu thì vòng bỏ qua module đó);
  * `danh_dau_khoe(khoa)` — đánh dấu tài khoản khoẻ lại sau khi tự khôi phục
    thành công (thiếu thì lõi bỏ qua, module tự lo).

Biến môi trường (đọc mỗi lần gọi để test/vận hành đổi được lúc chạy; xem
`docs/THAM-CHIEU.md` mục 8):

  * `WEB2API_TU_LAM_MOI_BAT` — "1"/"0", mặc định "1". Tắt thì không tự kích
    hoạt gì cả (đường API thủ công `/api/tai-khoan/khoi-phuc` vẫn chạy).
  * `WEB2API_TU_LAM_MOI_KHOANG_GIAY` — chu kỳ vòng định kỳ, mặc định 1800
    (30 phút); tối thiểu 60.
  * `WEB2API_TU_LAM_MOI_NGHI_GIAY` — nghỉ giữa hai lượt tự khôi phục của cùng
    một tài khoản, mặc định 300 (5 phút).
  * `WEB2API_TU_LAM_MOI_GIAN_CACH_TAI_KHOAN_GIAY` — giãn cách giữa các tài
    khoản trong một vòng định kỳ, mặc định 30.

An toàn bí mật: khoá tài khoản là chuỗi mờ; mật khẩu chỉ tồn tại trong
`BiMatTam` và bị `khoi_phuc` xoá sau dùng; không log/không ghi nhật ký bí mật.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable

from app import khoi_phuc_phien as kp
from app import kho_tai_khoan
from app import nhat_ky

log = logging.getLogger("web2api.tu_lam_moi")

#: Đồng hồ chích được cho test (giây, đơn điệu).
_dong_ho = time.monotonic


def _bay_gio() -> float:
    return _dong_ho()


# ── Cấu hình ─────────────────────────────────────────────────────────────────

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


def tu_dong_bat() -> bool:
    """Có bật tự khôi phục/làm mới không (mặc định bật)."""
    return _lay_bool_env("WEB2API_TU_LAM_MOI_BAT", True)


def khoang_giay() -> float:
    """Chu kỳ vòng định kỳ (giây); tối thiểu 60 để không thành vòng lặp bận."""
    return _lay_so_env("WEB2API_TU_LAM_MOI_KHOANG_GIAY", 1800.0, 60.0)


def nghi_giay() -> float:
    """Nghỉ giữa hai lượt tự khôi phục của cùng một tài khoản (giây)."""
    return _lay_so_env("WEB2API_TU_LAM_MOI_NGHI_GIAY", 300.0, 0.0)


def gian_cach_tai_khoan_giay() -> float:
    """Giãn cách giữa các tài khoản trong một vòng định kỳ (giây)."""
    return _lay_so_env("WEB2API_TU_LAM_MOI_GIAN_CACH_TAI_KHOAN_GIAY", 30.0, 0.0)


# ── Bộ điều khiển trình duyệt cho tầng (b) ────────────────────────────────────
#
# NV92 sẽ điền bộ điều khiển THẬT qua `dang_ky_trinh_duyet`. Chưa có thì tầng
# (b) nhận trinh_duyet=None và module tự quyết (thường → can_nguoi).

_ham_tao_trinh_duyet: dict[str, Callable[[], Any]] = {}


def dang_ky_trinh_duyet(module_id: str, ham_tao: Callable[[], Any]) -> None:
    """Đăng ký hàm tạo bộ điều khiển trình duyệt cho một module (NV92 điền)."""
    if not isinstance(module_id, str) or not module_id:
        raise ValueError("module_id phải là chuỗi khác rỗng")
    if not callable(ham_tao):
        raise ValueError("ham_tao phải gọi được")
    _ham_tao_trinh_duyet[module_id] = ham_tao


async def _lay_trinh_duyet(module_id: str):
    ham_tao = _ham_tao_trinh_duyet.get(module_id)
    if ham_tao is None:
        return None
    try:
        ket_qua = ham_tao()
        if asyncio.iscoroutine(ket_qua):
            ket_qua = await ket_qua
        return ket_qua
    except Exception as e:
        # Không lấy được trình duyệt thì coi như không có (fail-closed):
        # module sẽ trả can_nguoi thay vì vỡ.
        log.warning("tu-lam-moi: không tạo được trình duyệt cho %s: %s",
                    module_id, type(e).__name__)
        return None


# ── Single-flight: một lượt khôi phục cho mỗi (module, tài khoản) ─────────────

_khoa_luong = threading.Lock()
_dang_chay: dict[tuple[str, str], asyncio.Task] = {}
_lan_kich_cuoi: dict[tuple[str, str], float] = {}
#: Lần kiểm định kỳ gần nhất của từng (module, tài khoản) — sổ RIÊNG của lõi
#: tự làm mới (không chạm app/khoi_phuc_phien.py để tránh xung đột gộp patch).
_lan_kiem: dict[tuple[str, str], float] = {}


def _lay_lan_kiem(module_id: str, khoa: str) -> float:
    with _khoa_luong:
        return _lan_kiem.get((module_id, khoa), 0.0)


def _dat_lan_kiem(module_id: str, khoa: str, luc: float) -> None:
    with _khoa_luong:
        _lan_kiem[(module_id, khoa)] = luc


def _co_kha_nang_khoi_phuc(module: Any) -> bool:
    """Module phải triển khai ít nhất một tầng (a)/(b) thì lõi mới tự kích
    hoạt; không thì gọi là vô nghĩa (sẽ rơi ngay xuống can_nguoi)."""
    return callable(getattr(module, "lam_moi_phien", None)) or \
        callable(getattr(module, "dang_nhap_tu_dong", None))


def bao_loi_phien(module: Any, khoa: Any, ma_loi: str = "") -> "asyncio.Task | None":
    """Module gọi khi đường chat gặp lỗi phiên (nhận ra bằng cấu trúc).

    Fire-and-forget: kích hoạt MỘT lượt khôi phục nền cho đúng tài khoản đó
    (single-flight theo cặp (module_id, khoa); nghỉ `nghi_giay()` giữa các
    lượt). Trả tác vụ nền để test chờ, hoặc None khi: tắt cấu hình, module
    không có khả năng khôi phục, đang có lượt chạy, đang trong thời gian nghỉ,
    khoá sai khuôn, hoặc không có vòng lặp đang chạy (gọi từ thread lạ).

    Module KHÔNG chờ hàm này: sau khi gọi thì chuyển tài khoản/khoá kế ngay
    theo quy tắc chuyển tài khoản của nó.
    """
    if not tu_dong_bat():
        return None
    try:
        module_id = kp._id_cua_module(module)
        khoa = kp.kiem_khoa(khoa)
    except ValueError:
        log.warning("tu-lam-moi: khoá tài khoản sai khuôn — bỏ qua kích hoạt")
        return None
    if not _co_kha_nang_khoi_phuc(module):
        return None
    key = (module_id, khoa)
    with _khoa_luong:
        task = _dang_chay.get(key)
        if task is not None and not task.done():
            return task  # đang chạy: trả tác vụ hiện có, không kích hoạt thêm
        bay_gio = _bay_gio()
        if bay_gio - _lan_kich_cuoi.get(key, 0.0) < nghi_giay():
            return None  # đang nghỉ giữa các lượt
        _lan_kich_cuoi[key] = bay_gio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("tu-lam-moi: gọi ngoài vòng lặp sự kiện — bỏ qua")
            return None
        task = loop.create_task(_chay_khoi_phuc(module, module_id, khoa, ma_loi))
        _dang_chay[key] = task
        log.info("tu-lam-moi: kích hoạt tự khôi phục %s/%s", module_id, khoa)
        return task


async def _chay_khoi_phuc(module: Any, module_id: str, khoa: str, ma_loi: str,
                        dang_chay: dict | None = None) -> None:
    """Lượt khôi phục nền: chạy các tầng (a)→(d), cập nhật trạng thái.

    `dang_chay` được truyền tiếp cho `khoi_phuc` để tầng (b) tự lấy bộ điều
    khiển trình duyệt thật qua cầu nối lõi (NV94-e). None → tự lấy từ registry.
    """
    if dang_chay is None:
        dang_chay = _dang_chay_hien_tai()
    try:
        trinh_duyet = await _lay_trinh_duyet(module_id)
        tai_khoan = kp.TaiKhoanCanKhoiPhuc(khoa=khoa)
        kq = await kp.khoi_phuc(module, tai_khoan, trinh_duyet=trinh_duyet,
                                dang_chay=dang_chay)
        if kq.thanh_cong and kq.khoa_moi:
            # Tầng (c): tài khoản cũ vừa bị đánh dấu hỏng và đã chuyển sang khoá
            # kế — KHÔNG bỏ dấu hỏng, kẻo lần chọn sau lại quay về khoá hỏng.
            log.info("tu-lam-moi: %s/%s chuyển sang khoá kế ở tầng %s",
                     module_id, khoa, kq.tang)
        elif kq.thanh_cong:
            _danh_dau_khoe(module, khoa)
            log.info("tu-lam-moi: %s/%s tự khôi phục xong ở tầng %s",
                     module_id, khoa, kq.tang)
        elif kq.can_nguoi:
            # NV144: trước khi báo «cần người», thử tự đăng nhập lại bằng tài
            # khoản đã lưu (công tắc mặc định BẬT, giãn cách theo
            # WEB2API_DANG_NHAP_GIAN_CACH_GIAY, dừng khi hỏng 2 liên tiếp).
            # Chỉ GỌI hợp đồng dang_nhap của module, không sửa module.
            try:
                email_phien = kho_tai_khoan.email_cua_phien(module, khoa)
                if email_phien:
                    kq_dl = await kho_tai_khoan.thu_dang_nhap_lai(
                        module=module, module_id=module_id, dinh_danh=email_phien)
                    if kq_dl.get("thu") and kq_dl.get("thanh_cong"):
                        _danh_dau_khoe(module, khoa)
                        log.info("tu-lam-moi: %s/%s tự đăng nhập lại bằng tài "
                                 "khoản đã lưu xong", module_id, khoa)
                        return
            except Exception as e:
                log.warning("tu-lam-moi: tự đăng nhập lại lỗi: %s",
                            type(e).__name__)
            # 2.3: hỏng tới tầng (d) → nhật ký quản trị + trạng thái «cần
            # người» (trạng thái do khoi_phuc ghi rồi); KHÔNG tự thử lại mãi
            # (lượt sau bị chặn bởi chốt dừng / thời gian nghỉ).
            nhat_ky.ghi(
                "quan_tri", "canh_bao",
                f"Tài khoản {khoa} ({module_id}) tự khôi phục không được, "
                f"cần người xử lý (tầng {kq.tang}, mã {kq.ma_loi}).",
                module=module_id,
            )
            log.warning("tu-lam-moi: %s/%s can_nguoi sau tự khôi phục", module_id, khoa)
        else:
            log.info("tu-lam-moi: %s/%s tự khôi phục chưa được (tầng %s, mã %s)",
                     module_id, khoa, kq.tang, kq.ma_loi)
    except Exception as e:
        # Không để tác vụ nền chết im lặng, cũng không ném ra ngoài.
        log.warning("tu-lam-moi: lỗi khi tự khôi phục %s/%s: %s",
                    module_id, khoa, type(e).__name__)
    finally:
        with _khoa_luong:
            _dang_chay.pop((module_id, khoa), None)


def _danh_dau_khoe(module: Any, khoa: str) -> None:
    """Báo module đánh dấu tài khoản khoẻ lại (phương thức tuỳ chọn NV91)."""
    danh_dau = getattr(module, "danh_dau_khoe", None)
    if not callable(danh_dau):
        return
    try:
        danh_dau(khoa)
    except Exception as e:
        log.warning("tu-lam-moi: danh_dau_khoe lỗi: %s", type(e).__name__)


async def huy_cua_module(module_id: str) -> int:
    """Huỷ mọi tác vụ tự khôi phục đang chạy của một module (khi gỡ/dừng).
    Trả số tác vụ đã huỷ."""
    with _khoa_luong:
        muc = [(k, t) for k, t in _dang_chay.items() if k[0] == module_id]
    for _, t in muc:
        t.cancel()
    for _, t in muc:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    return len(muc)


async def huy_tat_ca() -> int:
    """Huỷ mọi tác vụ (dọn khi tắt máy / cuối test)."""
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


def so_tac_vu_dang_chay(module_id: str | None = None) -> int:
    """Số tác vụ tự khôi phục chưa xong (lọc theo module nếu cho)."""
    with _khoa_luong:
        return sum(1 for (mid, _), t in _dang_chay.items()
                   if not t.done() and (module_id is None or mid == module_id))


# ── Vòng làm mới định kỳ ─────────────────────────────────────────────────────

class VongTuLamMoi:
    """Một vòng làm mới định kỳ cho một module.

    `ngu`: hàm ngủ chích được cho test (mặc định `asyncio.sleep`).
    Mỗi vòng: liệt kê `module.khoa_tai_khoan()` (thiếu → bỏ qua module), gọi
    `lam_moi_phien` cho tài khoản «lâu chưa kiểm» (chưa kiểm từ đầu hoặc quá
    `khoang_giay()`), bỏ qua tài khoản đang `can_nguoi` hoặc đã
    `ngung_tu_dang_nhap` (tôn trọng chốt dừng), giãn cách
    `gian_cach_tai_khoan_giay()` giữa các tài khoản.
    """

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
            log.warning("tu-lam-moi: vòng %s lỗi: %s", self._module_id, type(e).__name__)
        finally:
            self.da_dung = True

    async def chay_mot_vong(self) -> int:
        """Chạy một lượt kiểm qua các tài khoản. Trả số tài khoản đã kiểm."""
        if not tu_dong_bat():
            return 0
        lay_khoa = getattr(self._module, "khoa_tai_khoan", None)
        if not callable(lay_khoa):
            return 0
        try:
            cac_khoa = lay_khoa()
        except Exception as e:
            log.warning("tu-lam-moi: khoa_tai_khoan của %s lỗi: %s",
                        self._module_id, type(e).__name__)
            return 0
        if not isinstance(cac_khoa, (list, tuple)):
            log.warning("tu-lam-moi: khoa_tai_khoan của %s trả kiểu lạ", self._module_id)
            return 0
        da_kiem = 0
        cach = gian_cach_tai_khoan_giay()
        for khoa in cac_khoa:
            if self._dung_co:
                break
            if da_kiem:
                await self._ngu(cach)  # giãn cách giữa các tài khoản
            if await self._kiem_mot_tai_khoan(khoa):
                da_kiem += 1
        return da_kiem

    async def _kiem_mot_tai_khoan(self, khoa: Any) -> bool:
        """Kiểm một tài khoản nếu «lâu chưa kiểm». Trả True nếu đã kiểm."""
        try:
            khoa = kp.kiem_khoa(khoa)
        except ValueError:
            return False
        tt = kp.lay_trang_thai(self._module_id, khoa)
        if tt.get("can_nguoi") or tt.get("ngung_tu_dang_nhap"):
            return False  # tôn trọng chốt dừng / trạng thái cần người
        bay_gio = _bay_gio()
        if _lay_lan_kiem(self._module_id, khoa) and \
                bay_gio - _lay_lan_kiem(self._module_id, khoa) < khoang_giay():
            return False  # vừa kiểm trong vòng này
        lam_moi = getattr(self._module, "lam_moi_phien", None)
        if not callable(lam_moi):
            return False
        kq = await kp._goi_an_toan(lam_moi, "a", khoa)
        _dat_lan_kiem(self._module_id, khoa, bay_gio)
        if kq.thanh_cong:
            _danh_dau_khoe(self._module, khoa)
        elif kq.can_nguoi:
            kp.dat_can_nguoi(self._module_id, khoa, "lam_moi_dinh_ky",
                             "Vòng làm mới định kỳ gặp màn hình cần người xử lý.")
            nhat_ky.ghi(
                "quan_tri", "canh_bao",
                f"Tài khoản {khoa} ({self._module_id}) cần người xử lý "
                f"(vòng làm mới định kỳ, mã {kq.ma_loi}).",
                module=self._module_id,
            )
        return True


class QuanLyTuLamMoi:
    """Giữ một VongTuLamMoi cho mỗi module đang chạy có `lam_moi_phien`.

    Đồng bộ định kỳ với sổ module (mỗi 60s): module mới/gỡ thì thêm/bớt vòng —
    không để tác vụ nền mồ côi khi gỡ module giữa chừng.
    """

    CHU_KY_DONG_BO = 60.0

    def __init__(self):
        self._vong: dict[str, VongTuLamMoi] = {}
        self._task: asyncio.Task | None = None
        self._dung_co = False
        self._lay_dang_chay: Callable[[], dict] | None = None

    def bat_dau(self, lay_dang_chay: Callable[[], dict]) -> None:
        """`lay_dang_chay`: () -> {module_id: module}. Tạo tác vụ đồng bộ.

        NV94-e: NHỚ hàm này (và đăng ký toàn cục) để `bao_loi_phien` truyền
        `dang_chay` cho `khoi_phuc` — nối vòng tự làm mới với cầu nối trình
        duyệt thật.
        """
        if self._task is not None and not self._task.done():
            return
        self._lay_dang_chay = lay_dang_chay
        dang_ky_dang_chay(lay_dang_chay)
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
                    log.warning("tu-lam-moi: đồng bộ vòng lỗi: %s", type(e).__name__)
                await asyncio.sleep(self.CHU_KY_DONG_BO)
        except asyncio.CancelledError:
            pass

    def dong_hoa(self, dang_chay: dict) -> None:
        """Thêm vòng cho module mới có lam_moi_phien; dừng vòng của module đã
        đi (gỡ/dừng) hoặc không còn khả năng làm mới."""
        dang_chay = dang_chay or {}
        for module_id in list(self._vong):
            module = dang_chay.get(module_id)
            # Thể hiện module bị thay (lưu cấu hình/cập nhật → thể hiện cũ đã dừng):
            # bỏ vòng cũ để dựng lại trên thể hiện mới, không bám vào cái đã dừng.
            if module is None or not callable(getattr(module, "lam_moi_phien", None)) \
                    or self._vong[module_id]._module is not module:
                self.go_vong(module_id)
        for module_id, module in dang_chay.items():
            if module_id not in self._vong and \
                    callable(getattr(module, "lam_moi_phien", None)):
                vong = VongTuLamMoi(module, module_id)
                self._vong[module_id] = vong
                vong.bat_dau()

    def go_vong(self, module_id: str) -> bool:
        """Dừng và bỏ vòng của một module. Trả True nếu đã có vòng."""
        vong = self._vong.pop(module_id, None)
        if vong is None:
            return False
        vong.dung()
        return True

    def cac_vong(self) -> dict[str, VongTuLamMoi]:
        return dict(self._vong)


#: Hàm () -> {module_id: module} do lifespan đăng ký qua bat_dau/dang_ky_dang_chay.
#: `bao_loi_phien` dùng để truyền `dang_chay` cho `khoi_phuc` (NV94-e: nối
#: NV91 với cầu nối trình duyệt thật NV92).
_lay_dang_chay: Callable[[], dict] | None = None


def dang_ky_dang_chay(ham: Callable[[], dict]) -> None:
    """Đăng ký hàm lấy các module đang chạy (lifespan gọi một lần)."""
    global _lay_dang_chay
    if not callable(ham):
        raise ValueError("ham phải gọi được")
    _lay_dang_chay = ham


def _dang_chay_hien_tai() -> dict | None:
    if _lay_dang_chay is None:
        return None
    try:
        dc = _lay_dang_chay()
    except Exception:
        return None
    return dc if isinstance(dc, dict) else None


_quan_ly: QuanLyTuLamMoi | None = None
_khoa_quan_ly = threading.Lock()


def quan_ly() -> QuanLyTuLamMoi:
    """Quản lý vòng định kỳ dùng chung (singleton theo tiến trình)."""
    global _quan_ly
    with _khoa_quan_ly:
        if _quan_ly is None:
            _quan_ly = QuanLyTuLamMoi()
        return _quan_ly
