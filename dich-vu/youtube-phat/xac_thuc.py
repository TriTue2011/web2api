"""Xác thực Bearer cho API của dịch vụ youtube-phat.

Viết mới cho dịch vụ độc lập (đợt A NV153). Mọi đường API cần khoá đều dùng
`kiem_bearer`; sai/thiếu token → `LoiXacThuc("invalid_auth")` để server.py dịch
thành HTTP 401.
"""

from __future__ import annotations

import hmac


class LoiXacThuc(Exception):
    """Xác thực thất bại (thiếu hoặc sai Bearer token)."""


def kiem_bearer(authorization: str | None, token: str) -> None:
    """Đúng Bearer token thì im lặng; thiếu/sai → raise LoiXacThuc("invalid_auth").

    So bằng `hmac.compare_digest` để không rò thời gian so khớp.
    """
    if not hmac.compare_digest(str(authorization or ""), f"Bearer {token}"):
        raise LoiXacThuc("invalid_auth")


def trich_bearer(authorization: str | None) -> str | None:
    """Trả token thô sau tiền tố "Bearer " (không phân biệt hoa/thường ở tiền tố),
    hoặc None nếu header vắng/không đúng dạng."""
    text = str(authorization or "").strip()
    if text[:7].lower() == "bearer " and len(text) > 7:
        return text[7:]
    return None
