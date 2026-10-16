"""Kiểm kiểu chặt cho trường từ thân JSON của khách — MỘT hàm dùng chung.

Bẫy đã biết (NV21): bool là int trong Python nên ``isinstance(True, int)`` đúng; ``int("5")``
nhận chuỗi số; ``int(1.5)`` lặng lẽ cắt phần thập phân; ``int(float("inf"))`` ném
``OverflowError`` — KHÔNG phải ``ValueError`` — nên ``except (ValueError, TypeError)`` để lọt
thành 500. Hàm dưới loại tất cả ngay từ đầu: chỉ nhận đúng ``int`` (không bool), kèm trần
dưới/trên tùy chọn. Sai → ``LoiOpenAI`` 400 (mã ``invalid_request``), không bao giờ 500.

Dùng ở mọi nơi cần số nguyên từ thân yêu cầu (tri_thuc, quy_trinh, ...); đừng viết bộ kiểm
thứ hai.
"""

from __future__ import annotations

from app.loi_openai import LoiOpenAI


def so_nguyen_chat(
    gia_tri,
    ten: str,
    *,
    toi_thieu: int | None = None,
    toi_da: int | None = None,
) -> int:
    """Số nguyên chặt từ thân yêu cầu của khách.

    Chỉ nhận đúng ``int`` — loại ``bool`` (``True`` là ``int`` trong Python), ``float``
    (kể cả ``inf``/``nan`` — không còn ``OverflowError`` lọt thành 500), ``str`` và ``None``.
    Kèm trần dưới/trên tùy chọn. Sai → ``LoiOpenAI`` 400.
    """
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, int):
        raise LoiOpenAI(400, f"«{ten}» phải là số nguyên", code="invalid_request")
    if toi_thieu is not None and gia_tri < toi_thieu:
        raise LoiOpenAI(400, f"«{ten}» phải ≥ {toi_thieu}", code="invalid_request")
    if toi_da is not None and gia_tri > toi_da:
        raise LoiOpenAI(400, f"«{ten}» phải ≤ {toi_da}", code="invalid_request")
    return gia_tri
