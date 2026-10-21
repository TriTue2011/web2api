"""Cầu nối lõi cho module nha-tien-ich.

Module KHÔNG import trực tiếp module ``home-assistant`` hay ``hoc-nha``. Mọi
trao đổi với chúng đi qua một trong hai đường:

1. Lõi thật: đọc/gọi thực thể của module khác qua cổng ``app.thiet_bi``
   (``lay_danh_sach_thuc_the`` / ``thuc_hien_hanh_dong_loi``) với ngữ cảnh gọi
   hiện tại — đúng chuẩn C1, lõi đóng dấu nguồn và kiểm quyền.
2. Tiêm hàm (``dat_doc_trang_thai`` / ``dat_goi_hanh_dong``): test và môi
   trường chưa có lõi dùng hàm giả; mặc định chưa tiêm = «chưa cấu hình».

Nguồn: ý tưởng tiêm từ ``modules/hoc-nha/ha_client.py`` (NV146).
"""
from __future__ import annotations

from typing import Any, Callable

_doc_trang_thai: Callable[[], list[dict[str, Any]]] | None = None
_goi_hanh_dong: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None
_gui_hoc_nha: Callable[[dict[str, Any]], None] | None = None


def dat_doc_trang_thai(fn: Callable[[], list[dict[str, Any]]] | None) -> None:
    """Tiêm hàm đọc trạng thái HA: () -> [{entity_id, state, attributes{...}}]."""
    global _doc_trang_thai
    _doc_trang_thai = fn


def dat_goi_hanh_dong(fn: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None) -> None:
    """Tiêm hàm gọi hành động: (entity_id, hanh_dong, tham_so) -> dict."""
    global _goi_hanh_dong
    _goi_hanh_dong = fn


def dat_gui_hoc_nha(fn: Callable[[dict[str, Any]], None] | None) -> None:
    """Tiêm hàm báo sự kiện về hoc-nha (qua lõi khi chạy thật)."""
    global _gui_hoc_nha
    _gui_hoc_nha = fn


def dat_lai() -> None:
    global _doc_trang_thai, _goi_hanh_dong, _gui_hoc_nha
    _doc_trang_thai = _goi_hanh_dong = _gui_hoc_nha = None


def _ngu_canh_hien_tai() -> Any | None:
    try:
        from app import ngu_canh as _nc
        return _nc.lay()
    except Exception:
        return None


def la_quan_tri() -> bool:
    """Người gọi hiện tại có vai quản trị (hoặc nội bộ) không?"""
    nc = _ngu_canh_hien_tai()
    if nc is None:
        return False
    try:
        return nc.danh_tinh.get("loai") in ("quan_tri", "noi_bo")
    except Exception:
        return False


def doc_trang_thai() -> list[dict[str, Any]] | None:
    """Trạng thái các thực thể HA; None = chưa cấu hình nguồn đọc."""
    if _doc_trang_thai is not None:
        try:
            return list(_doc_trang_thai() or [])
        except Exception:
            return None
    # Đường lõi thật: liệt kê thực thể home-assistant qua catalog của lõi.
    try:
        from app import thiet_bi as _tb
        import asyncio
        nc = _ngu_canh_hien_tai()
        ds = asyncio.get_event_loop().run_until_complete(
            _tb.lay_danh_sach_thuc_the(ngu_canh=nc))
        return None  # catalog lõi chưa đủ trạng thái — cần home-assistant.trang_thai
    except Exception:
        return None


def goi_hanh_dong(entity_id: str, hanh_dong: str, tham_so: dict[str, Any]) -> dict[str, Any]:
    """Gọi hành động lên một thực thể HA qua lõi (hoặc hàm tiêm trong test)."""
    if _goi_hanh_dong is not None:
        return _goi_hanh_dong(entity_id, hanh_dong, tham_so or {})
    try:
        from app import thiet_bi as _tb
        import asyncio
        nc = _ngu_canh_hien_tai()
        ma, kq = asyncio.get_event_loop().run_until_complete(
            _tb.thuc_hien_hanh_dong_loi(entity_id, hanh_dong, tham_so or {}, ngu_canh=nc))
        return {"ok": ma < 400, "ma": ma, **(kq or {})}
    except Exception as exc:
        return {"ok": False, "loi": f"khong_goi_duoc: {type(exc).__name__}"}


def bao_hoc_nha(su_kien: dict[str, Any]) -> None:
    """Báo một sự kiện nhà về hoc-nha (nếu đã nối; im lặng khi chưa nối)."""
    if _gui_hoc_nha is None:
        return
    try:
        _gui_hoc_nha(su_kien)
    except Exception:
        pass
