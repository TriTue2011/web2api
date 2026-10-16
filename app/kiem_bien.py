"""Bộ kiểm biên dùng chung cho mọi giá trị nhận từ yêu cầu (thân JSON, query, path, header).

Nguyên tắc:
- Sửa LỚP LỖI, không vá triệu chứng.
- Kiểm tra kiểu dữ liệu TRƯỚC khi xử lý; từ chối kiểu lạ, unhashable, bool lọt vào số, float NaN/Infinity.
- Kiểm tra chuỗi: encode được UTF-8 (từ chối lone surrogate), giới hạn độ dài, loại ký tự điều khiển/NUL.
- Kiểm tra thành viên: kiểm kiểu băm được trước khi toán tử `in` kiểm tra tập hợp, tránh 500 TypeError.
- Mọi lỗi không đạt chuẩn đều trả về LoiOpenAI với mã 400 chuẩn OpenAI.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

from app.loi_openai import LoiOpenAI


def kiem_chuoi(
    gia_tri: Any,
    *,
    toi_da: int | None = None,
    toi_thieu: int = 0,
    cho_phep_none: bool = False,
    mac_dinh: str | None = None,
    ten_truong: str = "chuỗi",
    cam_dieu_khien: bool = False,
    cam_nul: bool = False,
) -> str | None:
    """Kiểm tra chuỗi: đúng kiểu str (không bool), encode UTF-8 hợp lệ, độ dài trong trần sàn."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    if isinstance(gia_tri, bool) or not isinstance(gia_tri, str):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là chuỗi.", code="invalid_value")

    try:
        gia_tri.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError):
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» chứa ký tự Unicode không hợp lệ (surrogate không được phép).",
            code="invalid_value",
        ) from None

    if cam_nul and "\x00" in gia_tri:
        raise LoiOpenAI(400, f"Trường «{ten_truong}» không được chứa ký tự byte 0 (NUL).", code="invalid_value")

    if cam_dieu_khien:
        for c in gia_tri:
            if ord(c) < 32 and c not in ("\t", "\n", "\r"):
                raise LoiOpenAI(
                    400,
                    f"Trường «{ten_truong}» không được chứa ký tự điều khiển.",
                    code="invalid_value",
                )

    do_dai = len(gia_tri)
    if do_dai < toi_thieu:
        raise LoiOpenAI(
            400,
            f"Độ dài trường «{ten_truong}» tối thiểu là {toi_thieu} ký tự.",
            code="invalid_value",
        )
    if toi_da is not None and do_dai > toi_da:
        raise LoiOpenAI(
            400,
            f"Độ dài trường «{ten_truong}» vượt quá mức tối đa {toi_da} ký tự.",
            code="invalid_value",
        )

    return gia_tri


def kiem_thanh_vien(
    gia_tri: Any,
    tap_hop: Iterable[Any],
    *,
    ten_truong: str = "giá trị",
    cho_phep_none: bool = False,
    mac_dinh: Any = None,
) -> Any:
    """Kiểm tra giá trị thuộc tập hợp cho phép. Kiểm tra băm được TRƯỚC KHI `in` để tránh 500 TypeError."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    try:
        hash(gia_tri)
    except (TypeError, RecursionError):
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» có kiểu dữ liệu không hợp lệ (không thể băm).",
            code="invalid_value",
        ) from None

    try:
        hop_le = gia_tri in tap_hop
    except (TypeError, RecursionError):
        hop_le = False

    if not hop_le:
        try:
            ds = sorted(str(x) for x in tap_hop)
        except Exception:
            ds = list(tap_hop)
        raise LoiOpenAI(
            400,
            f"Giá trị trường «{ten_truong}» không hợp lệ (chỉ nhận: {ds}).",
            code="invalid_value",
        )

    return gia_tri


def kiem_so_nguyen(
    gia_tri: Any,
    *,
    toi_thieu: int | None = None,
    toi_da: int | None = None,
    cho_phep_none: bool = False,
    mac_dinh: int | None = None,
    ten_truong: str = "số nguyên",
) -> int | None:
    """Kiểm tra số nguyên: không bool, hữu hạn, trần/sàn chống tràn."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    if isinstance(gia_tri, bool) or not isinstance(gia_tri, int):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là số nguyên.", code="invalid_value")

    if toi_thieu is not None and gia_tri < toi_thieu:
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» ({gia_tri}) nhỏ hơn mức tối thiểu ({toi_thieu}).",
            code="invalid_value",
        )
    if toi_da is not None and gia_tri > toi_da:
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» ({gia_tri}) vượt quá mức tối đa ({toi_da}).",
            code="invalid_value",
        )

    return gia_tri


def kiem_so_thuc(
    gia_tri: Any,
    *,
    toi_thieu: float | None = None,
    toi_da: float | None = None,
    cho_phep_none: bool = False,
    mac_dinh: float | None = None,
    ten_truong: str = "số thực",
) -> float | None:
    """Kiểm tra số thực: không bool, phải hữu hạn (từ chối NaN/Infinity), trần/sàn."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là số thực.", code="invalid_value")

    if not math.isfinite(gia_tri):
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» phải là số hữu hạn (không chấp nhận NaN hoặc vô cực).",
            code="invalid_value",
        )

    val = float(gia_tri)
    if toi_thieu is not None and val < toi_thieu:
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» ({val}) nhỏ hơn mức tối thiểu ({toi_thieu}).",
            code="invalid_value",
        )
    if toi_da is not None and val > toi_da:
        raise LoiOpenAI(
            400,
            f"Trường «{ten_truong}» ({val}) vượt quá mức tối đa ({toi_da}).",
            code="invalid_value",
        )

    return val


def kiem_boolean(
    gia_tri: Any,
    *,
    cho_phep_none: bool = False,
    mac_dinh: bool | None = None,
    ten_truong: str = "giá trị logic",
    code: str = "invalid_request",
) -> bool | None:
    """Kiểm tra giá trị boolean thuần tuý (chỉ True/False)."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code=code)

    if not isinstance(gia_tri, bool):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là true hoặc false.", code=code)

    return gia_tri


def kiem_danh_sach(
    gia_tri: Any,
    *,
    kieu_phan_tu: Callable[[Any], Any] | None = None,
    toi_thieu: int = 0,
    toi_da: int | None = None,
    cho_phep_none: bool = False,
    mac_dinh: list | None = None,
    ten_truong: str = "danh sách",
) -> list | None:
    """Kiểm tra danh sách (list): độ dài và kiểu từng phần tử nếu có."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    if not isinstance(gia_tri, list):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là danh sách.", code="invalid_value")

    n = len(gia_tri)
    if n < toi_thieu:
        raise LoiOpenAI(
            400,
            f"Danh sách «{ten_truong}» có ít nhất {toi_thieu} phần tử.",
            code="invalid_value",
        )
    if toi_da is not None and n > toi_da:
        raise LoiOpenAI(
            400,
            f"Danh sách «{ten_truong}» vượt quá số lượng tối đa ({toi_da}).",
            code="invalid_value",
        )

    if kieu_phan_tu is not None:
        return [kieu_phan_tu(x) for x in gia_tri]

    return gia_tri


def kiem_tu_dien(
    gia_tri: Any,
    *,
    cho_phep_none: bool = False,
    mac_dinh: dict | None = None,
    ten_truong: str = "đối tượng",
) -> dict | None:
    """Kiểm tra đối tượng JSON (dict)."""
    if gia_tri is None:
        if cho_phep_none:
            return mac_dinh
        raise LoiOpenAI(400, f"Trường «{ten_truong}» là bắt buộc.", code="invalid_value")

    if not isinstance(gia_tri, dict):
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là đối tượng JSON.", code="invalid_value")

    return gia_tri
