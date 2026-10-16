"""Tách dòng tài khoản hàng loạt + bộ chạy đăng nhập lần lượt (NV67 nhóm 3, việc 5–6).

Lõi KHÔNG chứa tên nhà cung cấp. Việc ĐĂNG NHẬP THẬT vào dịch vụ nào là do từng
module thực hiện qua «Hợp đồng đăng nhập chung» (`KetQuaDangNhap` + hàm đăng nhập
bất đồng bộ); lõi chỉ điều phối: tách dòng, giãn cách, dừng sớm, xoá bí mật.

Hai khuôn dòng được chấp nhận (dấu `|` vì mật khẩu hay chứa dấu `:`):
  1. ``email|mật khẩu|hạt-giống-TOTP`` — đăng nhập tự động (TOTP có thể để trống)
  2. ``email|cookie=<chuỗi cookie>``      — dán thẳng cookie đã đăng nhập sẵn
  3. ``email``                            — đăng nhập lại tài khoản đã lưu trước đây

Luật tách dòng:
  - Dòng trống hoặc bắt đầu bằng ``#`` → bỏ qua (dòng ghi chú).
  - Dòng sai khuôn (thiếu ``@``, mật khẩu trống, cookie trống, thừa phần…)
    → báo đúng số dòng, các dòng đúng vẫn được nhận.
  - Email trùng nhau trong cùng lần tách → chỉ giữ lần đầu, báo đã bỏ trùng.
  - Email kiểm bằng biểu thức chính quy hẹp (chữ/số và ``._%+-``): vừa chặn sai
    khuôn vừa chặn ký tự HTML (``<>&"'``) ngay từ đầu vào → chống XSS khi hiển thị.

An toàn bí mật:
  - Mật khẩu/TOTP/cookie thô nằm trong `BiMatTam` (bộ đệm bytearray); bộ chạy ghi
    đè bộ đệm bằng 0 ngay sau khi tài khoản đó đăng nhập xong (kể cả khi lỗi).
    Lưu ý trung thực: chuỗi giải mã thoáng qua vẫn tồn tại trong bộ nhớ Python
    cho tới khi GC thu hồi — CPython không cho xoá triệt để kiểu `str`.
  - Không log, không trả bí mật trong kết quả: bản ghi kết quả chỉ giữ
    ``ma_loi`` (mã ngắn) và ``ma_trang_thai``; cookie trả về thuộc về module lưu
    vào kho mã hoá của nó, lõi không giữ.
  - ``ma_loi`` phải là mã ngắn ``[a-z0-9_]{1,64}``; module đưa mã lạ/câu dài →
    lõi thay bằng `phan_loai_loi(ma_trang_thai)` — phân loại bằng mã trạng thái,
    không dò chữ.

Bộ chạy lần lượt (`LuotChay`):
  - Một luồng, giãn cách `WEB2API_DANG_NHAP_GIAN_CACH_GIAY` giây (mặc định 30,
    đọc mỗi lượt chạy) giữa hai tài khoản — đo bằng đồng hồ mock được.
  - Dừng cả hàng khi hỏng 2 tài khoản LIÊN TIẾP; người dùng gọi `dung()` thì dừng
    sau tài khoản đang chạy; kết quả đã xong được giữ nguyên.
  - Chỉ một lượt chạy một lúc trong toàn tiến trình (khoá không chặn).

API/bảng tiến độ là việc 7, không nằm ở đây.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass, field

# ── Hằng số ──────────────────────────────────────────────────────────────────

#: Giãn cách mặc định giữa hai lần đăng nhập (giây) — quyết định của chủ kho NV67.
GIAN_CACH_MAC_DINH_GIAY = 30.0
#: Trần giãn cách để tránh cấu hình nhầm thành hàng giờ.
GIAN_CACH_TOI_DA_GIAY = 3600.0
#: Dừng cả hàng khi hỏng liên tiếp từng này tài khoản.
SO_HONG_LIEN_TIEP_DUNG = 2

#: Tên biến môi trường chỉnh giãn cách (cài đặt nâng cao).
BIEN_GIAN_CACH = "WEB2API_DANG_NHAP_GIAN_CACH_GIAY"

#: Email hẹp: vừa kiểm khuôn vừa chặn ký tự HTML ngay từ đầu vào.
_MAU_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: Mã lỗi hợp lệ: ngắn, chỉ chữ thường/số/gạch dưới.
_MAU_MA_LOI = re.compile(r"[a-z0-9_]{1,64}")

_LOAI_MAT_KHAU = "mat_khau"
_LOAI_COOKIE = "cookie"
_LOAI_DANG_NHAP_LAI = "dang_nhap_lai"

_TIEN_TO_COOKIE = "cookie="


def gian_cach_giay() -> float:
    """Giãn cách (giây) giữa hai lần đăng nhập; đọc biến môi trường MỖI LẦN GỌI."""
    try:
        gia_tri = float(os.environ.get(BIEN_GIAN_CACH, "") or GIAN_CACH_MAC_DINH_GIAY)
    except (TypeError, ValueError):
        gia_tri = GIAN_CACH_MAC_DINH_GIAY
    return min(max(gia_tri, 0.0), GIAN_CACH_TOI_DA_GIAY)


# ── Bí mật tạm trong bộ nhớ ──────────────────────────────────────────────────

class BiMatTam:
    """Chứa một bí mật thô (mật khẩu/TOTP/cookie) trong bộ nhớ; xoá sau khi dùng.

    Dùng `bytearray` để có thể ghi đè bằng 0. `lay()` trả bản sao `str` rồi để
    người gọi tự quyết thời điểm `xoa()`; `lay_va_xoa()` làm cả hai trong một
    bước. `repr()`/`str()` không bao giờ lộ giá trị.
    """

    __slots__ = ("_buf",)

    def __init__(self, gia_tri: str):
        self._buf = bytearray((gia_tri or "").encode("utf-8"))

    def lay(self) -> str:
        """Trả giá trị hiện tại (bản sao); KHÔNG xoá — người gọi phải `xoa()` sau."""
        return bytes(self._buf).decode("utf-8")

    def lay_va_xoa(self) -> str:
        """Trả giá trị rồi ghi đè bộ đệm bằng 0 ngay lập tức."""
        try:
            return self.lay()
        finally:
            self.xoa()

    def xoa(self) -> None:
        """Ghi đè toàn bộ bộ đệm bằng 0. Gọi lại nhiều lần vẫn an toàn."""
        for i in range(len(self._buf)):
            self._buf[i] = 0

    def da_xoa(self) -> bool:
        """Kiểm tra bộ đệm đã toàn 0 chưa (dùng cho test, không dùng để ra quyết định)."""
        return all(b == 0 for b in self._buf)

    def __repr__(self) -> str:  # pragma: no cover - hiển thị
        return "<BiMatTam>"

    __str__ = __repr__


# ── Tách dòng ────────────────────────────────────────────────────────────────

@dataclass
class DongDaTach:
    """Một dòng đã tách xong. Bí mật nằm trong `BiMatTam`, không ở dạng `str` trần."""

    dong: int
    email: str
    loai: str  # "mat_khau" | "cookie" | "dang_nhap_lai"
    mat_khau: BiMatTam | None = None
    totp: BiMatTam | None = None
    cookie: BiMatTam | None = None

    def xoa_bi_mat(self) -> None:
        """Xoá mọi bí mật thô của dòng này khỏi bộ nhớ."""
        for bi_mat in (self.mat_khau, self.totp, self.cookie):
            if bi_mat is not None:
                bi_mat.xoa()

    def __repr__(self) -> str:  # pragma: no cover - hiển thị
        return f"DongDaTach(dong={self.dong}, email={self.email!r}, loai={self.loai!r})"


@dataclass
class LoiDong:
    """Một dòng sai khuôn: báo ĐÚNG SỐ DÒNG, không chặn các dòng đúng."""

    dong: int
    loi: str


@dataclass
class DongBiTrung:
    """Dòng bị bỏ vì email đã xuất hiện trước đó (giữ lần đầu)."""

    dong: int
    email: str


def _email_hop_le(email: str) -> bool:
    return bool(_MAU_EMAIL.fullmatch(email))


def tach_dong(van_ban: str) -> tuple[list[DongDaTach], list[LoiDong], list[DongBiTrung]]:
    """Tách văn bản dán hàng loạt thành danh sách tài khoản.

    Trả về `(danh_sach, loi, bo_trung)`:
      - `danh_sach`: các `DongDaTach` hợp lệ theo thứ tự xuất hiện.
      - `loi`: các `LoiDong` (số dòng tính từ 1, kể cả dòng trống/dòng `#`).
      - `bo_trung`: các dòng bị bỏ vì email trùng (so không phân biệt hoa/thường).
    """
    danh_sach: list[DongDaTach] = []
    loi: list[LoiDong] = []
    bo_trung: list[DongBiTrung] = []
    da_thay: dict[str, int] = {}

    for so_dong, dong_tho in enumerate((van_ban or "").splitlines(), start=1):
        dong = dong_tho.strip()
        if not dong or dong.startswith("#"):
            continue
        phan = [p.strip() for p in dong.split("|")]

        email = phan[0]
        if not _email_hop_le(email):
            loi.append(LoiDong(so_dong, "thiếu email hợp lệ"))
            continue

        khoa_trung = email.lower()
        if khoa_trung in da_thay:
            bo_trung.append(DongBiTrung(so_dong, email))
            continue

        ban_ghi: DongDaTach | None = None
        if len(phan) == 1:
            ban_ghi = DongDaTach(so_dong, email, _LOAI_DANG_NHAP_LAI)
        elif len(phan) == 2:
            if phan[1].lower().startswith(_TIEN_TO_COOKIE):
                gia_tri = phan[1][len(_TIEN_TO_COOKIE):].strip()
                if not gia_tri:
                    loi.append(LoiDong(so_dong, "cookie trống sau 'cookie='"))
                    continue
                ban_ghi = DongDaTach(so_dong, email, _LOAI_COOKIE,
                                     cookie=BiMatTam(gia_tri))
            elif phan[1]:
                ban_ghi = DongDaTach(so_dong, email, _LOAI_MAT_KHAU,
                                     mat_khau=BiMatTam(phan[1]))
            else:
                loi.append(LoiDong(so_dong, "thiếu mật khẩu"))
                continue
        elif len(phan) == 3:
            if phan[1].lower().startswith(_TIEN_TO_COOKIE):
                loi.append(LoiDong(so_dong, "sai khuôn: 'cookie=' chỉ đi với email"))
                continue
            if not phan[1]:
                loi.append(LoiDong(so_dong, "thiếu mật khẩu"))
                continue
            ban_ghi = DongDaTach(so_dong, email, _LOAI_MAT_KHAU,
                                 mat_khau=BiMatTam(phan[1]),
                                 totp=BiMatTam(phan[2]) if phan[2] else None)
        else:
            loi.append(LoiDong(so_dong, "sai khuôn: quá nhiều dấu '|'"))
            continue

        da_thay[khoa_trung] = so_dong
        danh_sach.append(ban_ghi)

    return danh_sach, loi, bo_trung


# ── Hợp đồng đăng nhập chung ─────────────────────────────────────────────────

@dataclass(frozen=True)
class KetQuaDangNhap:
    """Hợp đồng đăng nhập chung giữa lõi và module (lõi không biết tên provider).

    Module triển khai hàm bất đồng bộ::

        async def dang_nhap(tai_khoan: DongDaTach) -> KetQuaDangNhap: ...

    nhận `DongDaTach` (đọc bí mật qua `BiMatTam.lay()`), trả về:
      - `thanh_cong=True` + `cookie` (cookie/phiên mới; module tự lưu vào kho
        mã hoá của nó — lõi không giữ cookie trong kết quả);
      - `thanh_cong=False` + `ma_loi` (mã ngắn, không chứa câu lỗi thô/bí mật)
        và/hoặc `ma_trang_thai` (mã HTTP) để lõi phân loại.
    """

    thanh_cong: bool
    cookie: str | None = None
    ma_loi: str = ""
    ma_trang_thai: int | None = None


def phan_loai_loi(ma_trang_thai: int | None) -> str:
    """Ánh xạ mã trạng thái HTTP → mã lỗi ngắn. Không dò chữ trong câu lỗi."""
    if ma_trang_thai is None:
        return "loi_mang"
    if ma_trang_thai == 401:
        return "sai_chung_thuc"
    if ma_trang_thai == 403:
        return "bi_cam"
    if ma_trang_thai == 404:
        return "khong_tim_thay"
    if ma_trang_thai == 429:
        return "han_muc"
    if 400 <= ma_trang_thai < 500:
        return "yeu_cau_sai"
    if 500 <= ma_trang_thai < 600:
        return "loi_may_chu"
    return "loi_khong_ro"


def _ma_loi_an_toan(ket_qua: KetQuaDangNhap) -> str:
    """Lấy mã lỗi an toàn: mã của module phải đúng hình, không thì suy từ trạng thái."""
    if ket_qua.ma_loi and _MAU_MA_LOI.fullmatch(ket_qua.ma_loi):
        return ket_qua.ma_loi
    return phan_loai_loi(ket_qua.ma_trang_thai)


# ── Bộ chạy lần lượt ─────────────────────────────────────────────────────────

_TRANG_THAI_CHO = "cho"
_TRANG_THAI_DANG_CHAY = "dang_chay"
_TRANG_THAI_XONG = "xong"
_TRANG_THAI_LOI = "loi"
_TRANG_THAI_BO_QUA = "bo_qua"

_MA_DUNG_TAY = "nguoi_dung_dung"
_MA_HONG_LIEN_TIEP = "hong_2_lien_tiep"

#: Chỉ một lượt chạy một lúc trong toàn tiến trình (quyết định của chủ kho NV67).
_khoa_luot_chay = threading.Lock()


class DaCoLuotChay(Exception):
    """Đã có một lượt đăng nhập hàng loạt đang chạy — không chạy chồng."""


@dataclass
class BanGhiKetQua:
    """Kết quả của một tài khoản trong lượt chạy. KHÔNG chứa bí mật."""

    dong: int
    email: str
    trang_thai: str  # "cho" | "dang_chay" | "xong" | "loi" | "bo_qua"
    ma_loi: str = ""
    ma_trang_thai: int | None = None


@dataclass
class TomTatLuotChay:
    """Tóm tắt khi lượt chạy kết thúc (xong hết, dừng tay, hay dừng vì hỏng)."""

    tong: int
    xong: int
    loi: int
    bo_qua: int
    ket_qua: list[BanGhiKetQua] = field(default_factory=list)


class LuotChay:
    """Bộ chạy đăng nhập lần lượt: một luồng, giãn cách, dừng sớm, xoá bí mật.

    `ham_dang_nhap`: hàm bất đồng bộ của module theo hợp đồng `KetQuaDangNhap`.
    `dong_ho`: hàm trả thời gian đơn điệu (giây) — truyền đồng hồ giả để test.
    `ngu`: hàm bất đồng bộ chờ N giây — truyền hàm giả để test đo giãn cách.
    """

    def __init__(
        self,
        tai_khoan: list[DongDaTach],
        ham_dang_nhap,
        *,
        dong_ho=None,
        ngu=None,
    ):
        self._tai_khoan = list(tai_khoan)
        self._ham_dang_nhap = ham_dang_nhap
        self._dong_ho = dong_ho or time.monotonic
        self._ngu = ngu or asyncio.sleep
        self._ket_qua = [
            BanGhiKetQua(tk.dong, tk.email, _TRANG_THAI_CHO) for tk in self._tai_khoan
        ]
        self._yeu_cau_dung = False

    def dung(self) -> None:
        """Yêu cầu dừng: dừng sau tài khoản đang chạy, giữ kết quả đã xong."""
        self._yeu_cau_dung = True

    @property
    def ket_qua(self) -> list[BanGhiKetQua]:
        return list(self._ket_qua)

    async def chay(self) -> TomTatLuotChay:
        """Chạy toàn bộ hàng. Ném `DaCoLuotChay` nếu đã có lượt khác đang chạy."""
        if not _khoa_luot_chay.acquire(blocking=False):
            raise DaCoLuotChay("đã có một lượt đăng nhập hàng loạt đang chạy")
        try:
            return await self._chay_noi_bo()
        finally:
            _khoa_luot_chay.release()

    async def _chay_noi_bo(self) -> TomTatLuotChay:
        gian_cach = gian_cach_giay()
        moc_truoc: float | None = None
        hong_lien_tiep = 0

        for i, tk in enumerate(self._tai_khoan):
            ban_ghi = self._ket_qua[i]
            if self._yeu_cau_dung:
                self._danh_dau_con_lai(i, _MA_DUNG_TAY)
                break

            if moc_truoc is not None and gian_cach > 0:
                cho = gian_cach - (self._dong_ho() - moc_truoc)
                if cho > 0:
                    await self._ngu(cho)

            ban_ghi.trang_thai = _TRANG_THAI_DANG_CHAY
            try:
                ket_qua = await self._ham_dang_nhap(tk)
            except Exception:
                # Module ném lỗi thô: không để câu lỗi lạ lọt vào kết quả.
                ket_qua = KetQuaDangNhap(False, ma_trang_thai=None)
            finally:
                # Xoá bí mật thô khỏi bộ nhớ NGAY sau khi dùng, kể cả khi lỗi.
                tk.xoa_bi_mat()
            moc_truoc = self._dong_ho()

            if ket_qua.thanh_cong:
                ban_ghi.trang_thai = _TRANG_THAI_XONG
                hong_lien_tiep = 0
            else:
                ban_ghi.trang_thai = _TRANG_THAI_LOI
                ban_ghi.ma_loi = _ma_loi_an_toan(ket_qua)
                ban_ghi.ma_trang_thai = ket_qua.ma_trang_thai
                hong_lien_tiep += 1
                if hong_lien_tiep >= SO_HONG_LIEN_TIEP_DUNG:
                    # Hỏng liên tiếp: dừng cả hàng để khỏi «đẻ thêm thử thách».
                    self._danh_dau_con_lai(i + 1, _MA_HONG_LIEN_TIEP)
                    break

        return self._tom_tat()

    def _danh_dau_con_lai(self, tu: int, ma_loi: str) -> None:
        for ban_ghi in self._ket_qua[tu:]:
            if ban_ghi.trang_thai == _TRANG_THAI_CHO:
                ban_ghi.trang_thai = _TRANG_THAI_BO_QUA
                ban_ghi.ma_loi = ma_loi

    def _tom_tat(self) -> TomTatLuotChay:
        xong = sum(1 for b in self._ket_qua if b.trang_thai == _TRANG_THAI_XONG)
        loi = sum(1 for b in self._ket_qua if b.trang_thai == _TRANG_THAI_LOI)
        bo_qua = sum(1 for b in self._ket_qua if b.trang_thai == _TRANG_THAI_BO_QUA)
        return TomTatLuotChay(
            tong=len(self._ket_qua),
            xong=xong,
            loi=loi,
            bo_qua=bo_qua,
            ket_qua=list(self._ket_qua),
        )
