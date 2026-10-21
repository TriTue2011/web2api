# Nguồn: c2a-nv146/services/image_guard.py (commit d879b03)
# Port sang module web2api `anh-ai`: chép nguyên logic, chỉ đổi import
# (services.image_utils → .tien_ich_anh, import tương đối trong gói).
"""Hàng rào chung cho MỌI ảnh đi vào hệ thống: định dạng, số lượng, dung lượng,
và **số điểm ảnh sau khi giải nén**.

Vì sao cần một chỗ dùng chung: trần dung lượng byte KHÔNG chặn được ảnh bom nén.
Một file PNG 40 KB có thể khai báo 50.000 × 50.000 điểm ảnh; Pillow giải nén ra
là ~10 GB RAM và tiến trình chết trước khi kịp làm gì. Trần byte hoàn toàn vô
can, vì file thật sự nhỏ.

Trước module này, mỗi đường vào tự đặt luật riêng và lệch nhau:
- `/v1/images/edits` có trần 20MB/ảnh, 8 ảnh, tổng 48MB — nhưng không kiểm điểm ảnh.
- `/api/image-tasks/edits` chỉ có trần 50MB mỗi file, KHÔNG giới hạn số ảnh và
  KHÔNG giới hạn tổng — cùng một người dùng đi cửa này là lách sạch cửa kia.
- `image_utils.normalize`, ảnh Zalo và thumbnail mở thẳng bằng Pillow.

Từ đây mọi đường vào gọi cùng một hàm.

Kiểm theo thứ tự rẻ-trước-đắt-sau, dừng ở lỗi đầu tiên:
1. rỗng / quá trần byte;
2. magic bytes — phải thật sự là ảnh (chặn HTML, JSON, PDF lọt vào chỗ ảnh);
3. đọc HEADER bằng Pillow để lấy kích thước — bước này KHÔNG giải nén điểm ảnh;
4. trần cạnh, trần tổng điểm ảnh, trần số khung hình (GIF/APNG nhiều khung cũng
   là một kiểu bom).
"""
from __future__ import annotations

import io
import logging
import warnings
from typing import Any, Iterable

from .tien_ich_anh import UnsupportedImage, describe, sniff_format

logger = logging.getLogger(__name__)

# ── Trần dùng chung cho mọi đường vào ────────────────────────────────────────
MAX_IMAGE_BYTES = 20 * 1024 * 1024        # mỗi ảnh
MAX_IMAGES_PER_REQUEST = 8                # số ảnh mỗi request
MAX_TOTAL_IMAGE_BYTES = 48 * 1024 * 1024  # tổng của cả request
# 50 megapixel: rộng hơn mọi máy ảnh dân dụng, nhưng chỉ bằng ~1/200 mức mà một
# file bom nén vài chục KB khai báo được.
MAX_PIXELS = 50_000_000
MAX_DIMENSION = 20_000                    # cạnh dài nhất
MAX_FRAMES = 512                          # GIF/APNG

# Định dạng ảnh thật sự chấp nhận ở biên. Định dạng lạ mà Pillow đọc được vẫn
# qua bước header bên dưới, nhưng magic bytes rác thì chặn ngay.
_KHONG_PHAI_ANH = {"pdf", "html", "json", "svg", ""}


class ImageRejected(ValueError):
    """Ảnh bị từ chối ở biên. `.ly_do` là câu tiếng Việt trả thẳng cho người dùng."""

    def __init__(self, ly_do: str) -> None:
        self.ly_do = ly_do
        super().__init__(ly_do)


def _mo_header(raw: bytes):
    """Mở ảnh và ĐỌC HEADER thôi (không `load()`, nên không giải nén điểm ảnh).

    Cảnh báo DecompressionBombWarning của Pillow bị nâng thành LỖI trong phạm vi
    hàm này — mặc định nó chỉ warn rồi vẫn cho đi tiếp, tức là không chặn gì.
    Dùng catch_warnings để không đụng vào cấu hình warning toàn tiến trình.
    """
    from PIL import Image

    from .tien_ich_anh import _register_plugins, _FRIENDLY

    _register_plugins()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        return Image.open(io.BytesIO(raw))


def kiem_anh(data: bytes | None, *, ten: str = "", max_bytes: int = MAX_IMAGE_BYTES) -> str:
    """Kiểm một ảnh. Trả MIME đoán được. Raise :class:`ImageRejected` nếu hỏng.

    Không giải nén ảnh, nên gọi được ở biên mà không tốn RAM.
    """
    nhan = f" ({ten})" if ten else ""
    raw = data or b""
    if not raw:
        raise ImageRejected(f"Tệp ảnh rỗng{nhan}.")
    if len(raw) > max_bytes:
        raise ImageRejected(
            f"Ảnh quá lớn{nhan}: {len(raw) // (1024 * 1024)}MB, trần {max_bytes // (1024 * 1024)}MB."
        )

    fmt = sniff_format(raw)
    if fmt in _KHONG_PHAI_ANH:
        from .tien_ich_anh import _FRIENDLY
        raise ImageRejected(
            f"Tệp này không phải ảnh{nhan}: {_FRIENDLY.get(fmt, fmt or 'không nhận dạng được')}."
        )

    try:
        img = _mo_header(raw)
        rong, cao = img.size
    except UnsupportedImage:
        raise
    except Exception as exc:
        # Pillow có trần điểm ảnh riêng (~89 triệu) và tự ném DecompressionBomb*
        # trước khi tới các phép kiểm bên dưới. Nói bằng cùng một câu để người
        # dùng — và test — không phải đoán xem trần nào bắt được.
        if "DecompressionBomb" in type(exc).__name__ or "decompression bomb" in str(exc).lower():
            raise ImageRejected(
                f"Ảnh quá nhiều điểm ảnh{nhan}, trần {MAX_PIXELS // 1_000_000} triệu. "
                "File nhỏ mà kích thước lớn thế này thường là ảnh bom nén."
            ) from exc
        logger.warning({"event": "image_guard_unreadable", "error": str(exc)[:120], **describe(raw)})
        raise ImageRejected(f"Không đọc được ảnh{nhan}: {str(exc)[:120]}") from exc

    if rong <= 0 or cao <= 0:
        raise ImageRejected(f"Ảnh có kích thước không hợp lệ{nhan}: {rong}×{cao}.")
    if max(rong, cao) > MAX_DIMENSION:
        raise ImageRejected(
            f"Ảnh có cạnh quá dài{nhan}: {rong}×{cao}, trần {MAX_DIMENSION} điểm ảnh mỗi cạnh."
        )
    if rong * cao > MAX_PIXELS:
        raise ImageRejected(
            f"Ảnh quá nhiều điểm ảnh{nhan}: {rong}×{cao} = {rong * cao // 1_000_000} triệu, "
            f"trần {MAX_PIXELS // 1_000_000} triệu. "
            "File nhỏ mà kích thước lớn thế này thường là ảnh bom nén."
        )
    khung = int(getattr(img, "n_frames", 1) or 1)
    if khung > MAX_FRAMES:
        raise ImageRejected(f"Ảnh động quá nhiều khung hình{nhan}: {khung}, trần {MAX_FRAMES}.")

    return f"image/{(img.format or fmt or 'jpeg').lower()}"


def kiem_bo_anh(
    items: Iterable[tuple[bytes, str, str]],
    *,
    max_count: int = MAX_IMAGES_PER_REQUEST,
    max_each: int = MAX_IMAGE_BYTES,
    max_total: int = MAX_TOTAL_IMAGE_BYTES,
) -> None:
    """Kiểm cả lô ảnh của một request: số lượng, tổng byte, và từng ảnh một.

    `items` là list `(bytes, tên_tệp, mime)` — đúng dạng mà /v1/images/edits và
    /api/image-tasks/edits đang dựng.
    """
    ds = list(items)
    if len(ds) > max_count:
        raise ImageRejected(f"Quá nhiều ảnh: {len(ds)}, trần {max_count} ảnh mỗi lượt.")
    tong = 0
    for raw, ten, _mime in ds:
        kiem_anh(raw, ten=ten, max_bytes=max_each)
        tong += len(raw or b"")
        if tong > max_total:
            raise ImageRejected(
                f"Tổng dung lượng ảnh vượt trần: {tong // (1024 * 1024)}MB, "
                f"trần {max_total // (1024 * 1024)}MB."
            )


def giai_ma_data_url(url: str, *, max_bytes: int = MAX_IMAGE_BYTES,
                     ten: str = "") -> tuple[bytes, str]:
    """`data:<mime>;base64,<...>` → (bytes, mime), có trần và kiểm nội dung.

    Dùng cho MỌI đường vision chat (Gemini Web, Claude, ChatGPT backend). Trước
    đây mỗi nơi tự `base64.b64decode(...)` không trần, trong khi nhánh URL http
    ngay cạnh đã có `max_bytes` — nên client chỉ cần đổi từ link sang data-URL
    là đi vòng qua hết mọi giới hạn.

    Đo độ dài chuỗi base64 TRƯỚC khi giải mã: `b64decode` cấp phát bản giải mã
    rồi mới trả về, nên đo sau là RAM đã mất.
    """
    nhan = f" ({ten})" if ten else ""
    s = str(url or "")
    if "," not in s:
        raise ImageRejected(f"data-URL không hợp lệ{nhan}: thiếu dấu phẩy ngăn phần dữ liệu.")
    head, b64 = s.split(",", 1)
    mime = (head[5:].split(";")[0] or "image/png").lower() if head.startswith("data:") else "image/png"

    uoc_luong = len(b64) * 3 // 4
    if uoc_luong > max_bytes:
        raise ImageRejected(
            f"Ảnh quá lớn{nhan}: ~{uoc_luong // (1024 * 1024)}MB, "
            f"trần {max_bytes // (1024 * 1024)}MB."
        )
    import base64 as _b64
    try:
        data = _b64.b64decode(b64)
    except Exception as exc:
        raise ImageRejected(f"Không giải mã được ảnh base64{nhan}: {str(exc)[:80]}") from exc

    # magic bytes + trần điểm ảnh/khung hình — chặn cả bom nén lẫn tệp không phải ảnh.
    kiem_anh(data, ten=ten, max_bytes=max_bytes)
    return data, mime


def kiem_anh_hoac_none(data: bytes | None, **kw: Any) -> str | None:
    """Như :func:`kiem_anh` nhưng trả None thay vì raise — cho chỗ best-effort."""
    try:
        return kiem_anh(data, **kw)
    except Exception:
        return None
