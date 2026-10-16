"""Ngữ cảnh gọi nội bộ và API (ngu_canh): giữ danh tính, nguồn gọi và phạm vi hiệu lực.

Giải quyết SEC-002: Mọi lời gọi (HTTP, nội bộ giữa các module, tác vụ nền) đều có ngữ cảnh.
Nguyên tắc ĐÓNG KHI LỖI: Thiếu ngữ cảnh hợp lệ -> từ chối (403 missing_context).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Literal

from app.loi_openai import LoiOpenAI

NguonGoi = Literal["nguoi", "ai", "tu_dong", "kenh"]
NGUON_HOP_LE = {"nguoi", "ai", "tu_dong", "kenh"}


@dataclass(frozen=True)
class NguCanh:
    danh_tinh: dict[str, Any]
    nguon: NguonGoi
    pham_vi_hieu_luc: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.danh_tinh, dict):
            raise ValueError("danh_tinh phải là đối tượng từ điển")
        if self.nguon not in NGUON_HOP_LE:
            raise ValueError(f"nguon không hợp lệ: {self.nguon!r} (chỉ nhận: {NGUON_HOP_LE})")
        if not isinstance(self.pham_vi_hieu_luc, tuple):
            object.__setattr__(self, "pham_vi_hieu_luc", tuple(self.pham_vi_hieu_luc or ()))


NGU_CANH: contextvars.ContextVar[NguCanh | None] = contextvars.ContextVar("NGU_CANH", default=None)


def dat(
    danh_tinh: dict[str, Any],
    nguon: NguonGoi = "nguoi",
    pham_vi_hieu_luc: tuple[str, ...] | list[str] | None = None,
) -> contextvars.Token[NguCanh | None]:
    """Đặt ngữ cảnh gọi hiện tại vào ContextVar. Trả về Token để khôi phục nếu cần."""
    if pham_vi_hieu_luc is None:
        pv_raw = danh_tinh.get("pham_vi", ())
        pham_vi_tuple = tuple(pv_raw) if isinstance(pv_raw, (list, tuple)) else ()
    else:
        pham_vi_tuple = tuple(pham_vi_hieu_luc)

    nc = NguCanh(
        danh_tinh=dict(danh_tinh),
        nguon=nguon,
        pham_vi_hieu_luc=pham_vi_tuple,
    )
    return NGU_CANH.set(nc)


def khoi_phuc(token: contextvars.Token[NguCanh | None]) -> None:
    """Khôi phục ngữ cảnh về trạng thái trước đó."""
    NGU_CANH.reset(token)


def lay() -> NguCanh | None:
    """Lấy ngữ cảnh gọi hiện tại, trả về None nếu chưa được đặt."""
    return NGU_CANH.get()


def bat_buoc() -> NguCanh:
    """Bắt buộc phải có ngữ cảnh; thiếu thì ném LoiOpenAI(403, code="missing_context")."""
    nc = NGU_CANH.get()
    if nc is None:
        raise LoiOpenAI(
            403,
            "Thiếu ngữ cảnh gọi hợp lệ (missing context).",
            loai="permission_error",
            code="missing_context",
        )
    return nc

