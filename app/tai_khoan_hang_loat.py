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


def so_hong_lien_tiep_dung() -> int:
    """Dừng cả hàng khi hỏng liên tiếp từng này tài khoản (NV210: cấu hình được
    qua ``WEB2API_DANG_NHAP_HONG_LIEN_TIEP_DUNG``, mặc định 2)."""
    try:
        v = int(str(os.environ.get("WEB2API_DANG_NHAP_HONG_LIEN_TIEP_DUNG", "")
                    or "").strip())
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else SO_HONG_LIEN_TIEP_DUNG


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


class BiMatTotp(BiMatTam):
    """Bí mật tạm chứa khoá TOTP Base32 (RFC 6238) trong bộ nhớ; xoá sau khi dùng.

    Mỗi lần `lay()` sẽ tính mã 6 số THỜI ĐIỂM ĐÓ bằng `sinh_totp` từ `app.xac_thuc_2fa`
    (mã luôn mới, không bị hết hạn vì thời gian chờ đợi giữa các bước).

    Khoá TOTP KHÔNG được lưu ra đĩa ở việc này (chỉ tồn tại trong bộ nhớ lượt
    đăng nhập, xoá sau khi dùng). Việc lưu khoá TOTP ra đĩa để tự động đăng nhập
    lại là một quyết định kiến trúc và chính sách bảo mật riêng của kho, chưa được
    phép thực hiện tại việc này nhằm bảo vệ tuyệt đối bí mật của người dùng.
    """

    __slots__ = ()

    def lay(self, moc_thoi_gian: int | float | None = None) -> str:
        """Tính và trả mã TOTP 6 số tại thời điểm gọi (hoặc moc_thoi_gian giả định)."""
        if self.da_xoa():
            return ""
        from app.xac_thuc_2fa import sinh_totp
        raw = bytes(self._buf).decode("ascii")
        return sinh_totp(raw, moc_thoi_gian=moc_thoi_gian)

    def lay_va_xoa(self, moc_thoi_gian: int | float | None = None) -> str:
        """Tính mã TOTP thời điểm này rồi ghi đè bộ đệm bằng 0 ngay lập tức."""
        try:
            return self.lay(moc_thoi_gian=moc_thoi_gian)
        finally:
            self.xoa()

    def __repr__(self) -> str:  # pragma: no cover - hiển thị
        return "<BiMatTotp>"

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
    #: NV122 — cờ «lấy mã từ hộp thư» (cột thứ tư tuỳ chọn của văn bản dán):
    #: None = không tích; True = dùng hộp thư trùng email tài khoản;
    #: chuỗi email = dùng hộp thư đó (quản trị chỉ định rõ).
    ma_tu_hop_thu: bool | str | None = None

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


#: Sentinel cho cột hộp thư sai khuôn (phân biệt với None = không tích).
_HOP_THU_SAI: object = object()


def _tach_cot_hop_thu(gia_tri: str) -> bool | str | None | object:
    """Tách cột hộp thư thứ tư (NV122): "" → None (không tích); "true" → True
    (dùng hộp thư trùng email tài khoản); "false" → None; email hợp lệ → chuỗi
    email đó (hộp thư quản trị chỉ định rõ); còn lại → _HOP_THU_SAI."""
    g = (gia_tri or "").strip()
    if not g:
        return None
    thap = g.casefold()
    if thap == "true":
        return True
    if thap == "false":
        return None
    return g if _email_hop_le(g) else _HOP_THU_SAI


def kiem_tra_va_tao_otp(otp_tho: Any) -> BiMatTam | BiMatTotp | None:
    """Kiểm tra và tạo đối tượng bí mật cho trường OTP / cột thứ 3.

    Quy tắc nhận:
      (1) KHOÁ TOTP base32 (A–Z, 2–7, bỏ dấu cách/gạch ngang, chữ hoa/thường đều được,
          độ dài 16–128) -> BiMatTotp (lớp con của BiMatTam: mỗi lần .lay() tính mã 6 số thời điểm đó).
      (2) Mã 6–8 chữ số đang có -> BiMatTam như cũ.
      (3) Khác -> ném ValueError("otp_khong_hop_le"), không đoán.
    """
    if otp_tho is None:
        return None
    if isinstance(otp_tho, bool) or not isinstance(otp_tho, str):
        raise ValueError("otp_khong_hop_le")
    try:
        otp_tho.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError):
        raise ValueError("otp_khong_hop_le") from None
    s = otp_tho.strip()
    if not s:
        return None
    # (2) Mã 6–8 chữ số đang có
    if re.fullmatch(r"[0-9]{6,8}", s):
        return BiMatTam(s)
    # (1) KHOÁ TOTP base32: bỏ dấu cách và gạch ngang, độ dài 16-128
    s_clean = re.sub(r"[\s-]", "", s).upper()
    if 16 <= len(s_clean) <= 128 and re.fullmatch(r"[A-Z2-7]+", s_clean):
        try:
            from app.xac_thuc_2fa import giai_ma_base32
            giai_ma_base32(s_clean)
            return BiMatTotp(s_clean)
        except Exception as e:
            raise ValueError("otp_khong_hop_le") from e
    # (3) Khác -> từ chối khuôn
    raise ValueError("otp_khong_hop_le")


def tach_dong(van_ban: str, nang_cap_totp: bool = False) -> tuple[list[DongDaTach], list[LoiDong], list[DongBiTrung]]:
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
            totp_val: BiMatTam | BiMatTotp | None = None
            if phan[2]:
                if nang_cap_totp:
                    try:
                        totp_val = kiem_tra_va_tao_otp(phan[2])
                    except ValueError:
                        loi.append(LoiDong(so_dong, "mã OTP hoặc khoá 2FA không hợp lệ"))
                        continue
                else:
                    totp_val = BiMatTam(phan[2])
            ban_ghi = DongDaTach(so_dong, email, _LOAI_MAT_KHAU,
                                 mat_khau=BiMatTam(phan[1]),
                                 totp=totp_val)
        elif len(phan) == 4:
            # NV122: cột thứ tư tuỳ chọn «hop_thu» — cờ lấy mã xác minh từ hộp thư
            # (ghi chú cho NV120 gộp với mảng tai_khoan có cấu trúc: trường
            # tương ứng là tai_khoan[i].ma_tu_hop_thu: bool | email).
            if phan[1].lower().startswith(_TIEN_TO_COOKIE):
                loi.append(LoiDong(so_dong, "sai khuôn: 'cookie=' chỉ đi với email"))
                continue
            if not phan[1]:
                loi.append(LoiDong(so_dong, "thiếu mật khẩu"))
                continue
            totp_val_4: BiMatTam | BiMatTotp | None = None
            if phan[2]:
                if nang_cap_totp:
                    try:
                        totp_val_4 = kiem_tra_va_tao_otp(phan[2])
                    except ValueError:
                        loi.append(LoiDong(so_dong, "mã OTP hoặc khoá 2FA không hợp lệ"))
                        continue
                else:
                    totp_val_4 = BiMatTam(phan[2])
            hop_thu = _tach_cot_hop_thu(phan[3])
            if hop_thu is _HOP_THU_SAI:
                loi.append(LoiDong(so_dong, "hop_thu sai khuôn"))
                if totp_val_4 is not None:
                    totp_val_4.xoa()
                continue
            ban_ghi = DongDaTach(so_dong, email, _LOAI_MAT_KHAU,
                                 mat_khau=BiMatTam(phan[1]),
                                 totp=totp_val_4,
                                 ma_tu_hop_thu=hop_thu)
        else:
            loi.append(LoiDong(so_dong, "sai khuôn: quá nhiều dấu '|'"))
            continue

        da_thay[khoa_trung] = so_dong
        danh_sach.append(ban_ghi)

    return danh_sach, loi, bo_trung


def tach_danh_sach_tai_khoan(
    danh_sach_tho: list,
) -> tuple[list[DongDaTach], list[DongBiTrung]]:
    """Tách danh sách tài khoản dạng cấu trúc: [{email, mat_khau, otp?}, ...] (tối đa 20).

    Kiểm tra chặt chẽ:
      - Tối đa 20 phần tử.
      - Mỗi phần tử là đối tượng JSON:
          + email: chuẩn RFC / mẫu hẹp, không chứa ký tự HTML / điều khiển / surrogate.
          + mat_khau: chuỗi 1..256 ký tự, không chứa ký tự điều khiển (ord < 32 hoặc ord == 127), không chứa surrogate.
          + otp: tuỳ chọn, chuỗi <= 128 ký tự, đúng khuôn (khoá Base32 hoặc mã 6-8 số).
      - Lỗi sai khuôn ném ValueError kèm số thứ tự phần tử (KHÔNG in giá trị, KHÔNG lộ câu codec).
      - Email trùng nhau giữ lần đầu, các lần sau coi như bị trùng (xoá bí mật).
      - Mật khẩu/OTP chỉ lưu trong BiMatTam / BiMatTotp, xoá sau khi dùng.
    """
    if not isinstance(danh_sach_tho, list) or isinstance(danh_sach_tho, (bool, str, bytes)):
        raise ValueError("Trường «tai_khoan» phải là danh sách.")
    if len(danh_sach_tho) > 20:
        raise ValueError("Danh sách «tai_khoan» vượt quá tối đa 20 phần tử.")
    if not danh_sach_tho:
        return [], []

    ket_qua: list[DongDaTach] = []
    bo_trung: list[DongBiTrung] = []
    da_thay: dict[str, int] = {}

    for idx, item in enumerate(danh_sach_tho, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: phải là đối tượng JSON.")

        email_raw = item.get("email")
        if isinstance(email_raw, bool) or not isinstance(email_raw, str):
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: email không hợp lệ.")
        try:
            email_raw.encode("utf-8")
        except (UnicodeEncodeError, UnicodeError):
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: email không hợp lệ.") from None
        email = email_raw.strip()
        if not _email_hop_le(email) or len(email) > 254:
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: email không hợp lệ.")

        mat_khau_raw = item.get("mat_khau")
        if isinstance(mat_khau_raw, bool) or not isinstance(mat_khau_raw, str):
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: mật khẩu không hợp lệ (chuỗi 1..256 ký tự, không chứa ký tự điều khiển).")
        try:
            mat_khau_raw.encode("utf-8")
        except (UnicodeEncodeError, UnicodeError):
            raise ValueError(f"Phần tử thứ {idx} sai khuôn: mật khẩu không hợp lệ (chuỗi 1..256 ký tự, không chứa ký tự điều khiển).") from None
        if (
            not (1 <= len(mat_khau_raw) <= 256)
            or any(ord(c) < 32 or ord(c) == 127 for c in mat_khau_raw)
        ):
            raise ValueError(
                f"Phần tử thứ {idx} sai khuôn: mật khẩu không hợp lệ (chuỗi 1..256 ký tự, không chứa ký tự điều khiển)."
            )

        otp_raw = item.get("otp")
        otp_obj: BiMatTam | BiMatTotp | None = None
        if otp_raw is not None and not (isinstance(otp_raw, str) and not otp_raw.strip()):
            if isinstance(otp_raw, bool) or not isinstance(otp_raw, str):
                raise ValueError(
                    f"Phần tử thứ {idx} sai khuôn: trường «otp» không hợp lệ."
                )
            try:
                otp_raw.encode("utf-8")
            except (UnicodeEncodeError, UnicodeError):
                raise ValueError(
                    f"Phần tử thứ {idx} sai khuôn: trường «otp» không hợp lệ."
                ) from None
            if len(otp_raw) > 128:
                raise ValueError(
                    f"Phần tử thứ {idx} sai khuôn: trường «otp» không hợp lệ (chuỗi tối đa 128 ký tự)."
                )
            try:
                otp_obj = kiem_tra_va_tao_otp(otp_raw)
            except ValueError:
                raise ValueError(
                    f"Phần tử thứ {idx} sai khuôn: trường «otp» không hợp lệ (chỉ nhận mã 6-8 số hoặc khoá Base32)."
                ) from None

        ma_hop_thu_val = None
        if "ma_tu_hop_thu" in item and item["ma_tu_hop_thu"] is not None:
            mth = item["ma_tu_hop_thu"]
            if isinstance(mth, bool):
                ma_hop_thu_val = mth
            elif isinstance(mth, str) and _email_hop_le(mth.strip()):
                ma_hop_thu_val = mth.strip()
            else:
                raise ValueError(f"Phần tử thứ {idx} sai khuôn: ma_tu_hop_thu không hợp lệ.")

        khoa_trung = email.lower()
        if khoa_trung in da_thay:
            bm_mk = BiMatTam(mat_khau_raw)
            bm_mk.xoa()
            if otp_obj is not None:
                otp_obj.xoa()
            bo_trung.append(DongBiTrung(idx, email))
            continue

        da_thay[khoa_trung] = idx
        dong_obj = DongDaTach(
            idx,
            email,
            _LOAI_MAT_KHAU,
            mat_khau=BiMatTam(mat_khau_raw),
            totp=otp_obj,
            ma_tu_hop_thu=ma_hop_thu_val,
        )
        ket_qua.append(dong_obj)

    return ket_qua, bo_trung


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
                if hong_lien_tiep >= so_hong_lien_tiep_dung():
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


# ── Quản lý lượt chạy cho API (NV67 nhóm 3, việc 7) ────────────────────────────

#: Mã «dòng» khi module chưa triển khai hợp đồng `dang_nhap` — KHÔNG phải lỗi:
#: không tính vào hỏng liên tiếp, người dùng vẫn dán cookie hoặc đăng nhập tay
#: qua trình duyệt từ xa dùng chung cho dòng đó.
MA_CHUA_HO_TRO_TU_DONG = "module_chua_ho_tro_dang_nhap_tu_dong"

_TRANG_THAI_LUOT_DANG_CHAY = "dang_chay"
_TRANG_THAI_LUOT_HOAN_THANH = "hoan_thanh"


def _ban_ghi_thanh_dict(ban_ghi: BanGhiKetQua) -> dict:
    """BanGhiKetQua → dict JSON được (không bao giờ chứa bí mật)."""
    return {
        "dong": ban_ghi.dong,
        "email": ban_ghi.email,
        "trang_thai": ban_ghi.trang_thai,
        "ma_loi": ban_ghi.ma_loi,
        "ma_trang_thai": ban_ghi.ma_trang_thai,
    }


def _tom_tat_tu_ket_qua(ket_qua: list[BanGhiKetQua]) -> dict:
    return {
        "tong": len(ket_qua),
        "xong": sum(1 for b in ket_qua if b.trang_thai == _TRANG_THAI_XONG),
        "loi": sum(1 for b in ket_qua if b.trang_thai == _TRANG_THAI_LOI),
        "bo_qua": sum(1 for b in ket_qua if b.trang_thai == _TRANG_THAI_BO_QUA),
    }


class QuanLyLuotChay:
    """Giữ lượt đăng nhập hàng loạt đang chạy / vừa xong cho tầng API.

    Chỉ MỘT lượt chạy nền một lúc (quyết định của chủ kho NV67): `bat_dau` trong
    lúc lượt cũ còn chạy → `DaCoLuotChay`. «Tiếp tục sau lỗi/dừng» không cần giữ
    bí mật giữa các lượt: client gọi `bat_dau` lại kèm `bo_qua_email` (email đã
    xong, lấy từ `trang_thai`) cùng văn bản dán gốc.

    `tao_luot`: factory `(tai_khoan, ham_dang_nhap) -> LuotChay` — mặc định là
    `LuotChay`; test truyền factory với đồng hồ giả để đo giãn cách mà không
    chờ thật.
    """

    def __init__(self, tao_luot=None):
        self._khoa = threading.Lock()
        self._tao_luot = tao_luot or LuotChay
        self._hien_tai: dict | None = None
        self._dem = 0

    async def _chay_nen(self, luot: LuotChay, ket_thuc: list) -> None:
        """Chạy lượt trong task nền; ghi mốc kết thúc để `trang_thai` biết đã xong."""
        try:
            await luot.chay()
        except DaCoLuotChay:
            # Không thể xảy ra qua bat_dau (đã kiểm task.done), nhưng không để
            # task nền nổ tung nếu có race.
            pass
        finally:
            ket_thuc.append(time.monotonic())

    def bat_dau(self, tai_khoan: list[DongDaTach], ham_dang_nhap,
                module_id: str) -> dict:
        """Khởi động lượt chạy nền. Phải gọi trong vòng sự kiện đang chạy.

        `ham_dang_nhap=None` (module chưa triển khai hợp đồng) → mọi dòng
        `bo_qua` ngay với `MA_CHUA_HO_TRO_TU_DONG`, không tạo task nền.
        Bí mật của các dòng không được chạy bị xoá ngay tại đây.
        """
        with self._khoa:
            if self._hien_tai is not None:
                task = self._hien_tai["task"]
                if task is not None and not task.done():
                    # Đừng giữ bí mật của lượt mới khi từ chối.
                    for tk in tai_khoan:
                        tk.xoa_bi_mat()
                    raise DaCoLuotChay("đã có một lượt đăng nhập hàng loạt đang chạy")
            self._dem += 1
            luot_id = f"luot-{self._dem}"
            bat_dau_luc = time.monotonic()

            if ham_dang_nhap is None or not tai_khoan:
                ket_qua_rieng = [
                    BanGhiKetQua(
                        tk.dong, tk.email, _TRANG_THAI_BO_QUA,
                        ma_loi=MA_CHUA_HO_TRO_TU_DONG if ham_dang_nhap is None else "",
                    )
                    for tk in tai_khoan
                ]
                for tk in tai_khoan:
                    tk.xoa_bi_mat()
                self._hien_tai = {
                    "luot_id": luot_id,
                    "module_id": module_id,
                    "luot": None,
                    "task": None,
                    "ket_qua_rieng": ket_qua_rieng,
                    "bat_dau_luc": bat_dau_luc,
                    "ket_thuc_luc": [bat_dau_luc],
                }
            else:
                luot = self._tao_luot(list(tai_khoan), ham_dang_nhap)
                ket_thuc: list = []
                task = asyncio.create_task(self._chay_nen(luot, ket_thuc))
                self._hien_tai = {
                    "luot_id": luot_id,
                    "module_id": module_id,
                    "luot": luot,
                    "task": task,
                    "ket_qua_rieng": None,
                    "bat_dau_luc": bat_dau_luc,
                    "ket_thuc_luc": ket_thuc,
                }
            return {"luot_id": luot_id, "module_id": module_id, "tong": len(tai_khoan)}

    def trang_thai(self) -> dict | None:
        """Tiến độ lượt hiện tại; None nếu chưa từng có lượt nào."""
        with self._khoa:
            ht = self._hien_tai
            if ht is None:
                return None
            if ht["luot"] is not None:
                ket_qua = ht["luot"].ket_qua
                xong_chua = ht["task"].done()
            else:
                ket_qua = list(ht["ket_qua_rieng"])
                xong_chua = True
            ket_thuc = ht["ket_thuc_luc"][0] if ht["ket_thuc_luc"] else None
            return {
                "luot_id": ht["luot_id"],
                "module_id": ht["module_id"],
                "trang_thai_luot": _TRANG_THAI_LUOT_HOAN_THANH if xong_chua else _TRANG_THAI_LUOT_DANG_CHAY,
                "bat_dau_luc": ht["bat_dau_luc"],
                "ket_thuc_luc": ket_thuc,
                "ket_qua": [_ban_ghi_thanh_dict(b) for b in ket_qua],
                "tom_tat": _tom_tat_tu_ket_qua(ket_qua),
            }

    def dung(self) -> bool:
        """Yêu cầu dừng lượt đang chạy. Trả False nếu không có lượt nào đang chạy."""
        with self._khoa:
            ht = self._hien_tai
            if ht is None or ht["luot"] is None:
                return False
            if ht["task"].done():
                return False
            ht["luot"].dung()
            return True
