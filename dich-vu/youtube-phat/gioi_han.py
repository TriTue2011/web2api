"""Trần tài nguyên của add-on máy chủ (CPU/RAM).

Máy chủ chạy một luồng cho mỗi kết nối (ThreadingHTTPServer); mỗi lần trích xuất bằng yt-dlp hay tìm kiếm tốn vài chục tới
cả trăm MB và một lõi CPU. Nhiều loa cùng bấm «bài kế», tải trước bài sau và tìm kiếm chồng lên nhau thì chồng chất. Hai trần:

* ``chiem_cho``: số lần trích xuất/tìm kiếm chạy CÙNG LÚC (mặc định 2, biến môi trường ``YT_TRICH_XUAT_DONG_THOI``); lần
  thừa chờ tối đa ``timeout`` giây rồi báo bận, không treo luồng vô hạn.
* ``cat_bo_nho_dem``: bộ nhớ đệm giữ theo hạn dùng còn có trần SỐ MỤC; thừa thì bỏ mục sắp hết hạn trước."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager


def _so_nguyen(ten: str, mac_dinh: int, thap: int, cao: int) -> int:
    try:
        v = int(os.environ.get(ten, mac_dinh))
    except (TypeError, ValueError):
        v = mac_dinh
    return max(thap, min(cao, v))


TRICH_XUAT_DONG_THOI = _so_nguyen("YT_TRICH_XUAT_DONG_THOI", 2, 1, 8)
_CHO = threading.BoundedSemaphore(TRICH_XUAT_DONG_THOI)
#: Số mục tối đa của mỗi bộ nhớ đệm (luồng đã giải, kết quả Zing).
BO_NHO_DEM_TOI_DA = 256


@contextmanager
def chiem_cho(timeout: float, loi: Exception):
    """Chiếm một chỗ trích xuất/tìm kiếm; hết chỗ quá ``timeout`` giây thì ném ``loi`` (không treo)."""
    if not _CHO.acquire(timeout=max(0.5, float(timeout))):
        raise loi
    try:
        yield
    finally:
        _CHO.release()


def cat_bo_nho_dem(dem: dict, toi_da: int = BO_NHO_DEM_TOI_DA, het_han=lambda gia_tri: gia_tri) -> dict:
    """Giữ tối đa ``toi_da`` mục: bỏ mục có hạn dùng sớm nhất trước. ``het_han(giá trị)`` trả mốc hết hạn của mục."""
    if len(dem) <= toi_da:
        return dem
    giu = sorted(dem.items(), key=lambda kv: het_han(kv[1]), reverse=True)[:toi_da]
    return dict(giu)
