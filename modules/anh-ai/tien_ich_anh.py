# Nguồn: c2a-nv146/services/image_utils.py (commit d879b03)
# Port sang module web2api `anh-ai`: chép nguyên logic.
# Đổi duy nhất: trong normalize(), import `from services.image_guard import ...`
# → `from .kiem_anh import ...` (import tương đối trong gói).
"""Nhận dạng + chuẩn hoá ảnh TRƯỚC khi đẩy vào pipeline vision.

Vì sao cần: Pillow chỉ mở được JPEG/PNG/GIF/WEBP/BMP/TIFF. Ảnh iPhone
(HEIC/HEIF), JPEG-XL (một số CDN Zalo trả `.jxl`), AVIF, hoặc file tải hỏng
(HTML/JSON lọt vào chỗ ảnh) đều rơi vào ``UnidentifiedImageError``:

    cannot identify image file <_io.BytesIO object at 0x...>

Trước đây lỗi đó chỉ nổ ở TẦNG CUỐI (upload ảnh lên ChatGPT web), nên combo
vision thử lần lượt hết provider — provider nào cũng chết vì cùng một tấm ảnh
hỏng — rồi báo "cạn provider" kèm thông báo khó hiểu. Module này chặn ngay đầu
vào: đọc được thì chuẩn hoá về JPEG/PNG, không đọc được thì báo lỗi có tên định
dạng để nói thẳng với người dùng.
"""
from __future__ import annotations

import io
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_plugin_lock = threading.Lock()
_plugins_ready = False

# Định dạng gửi thẳng cho provider (mọi backend vision đều nhận).
_PASSTHROUGH = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

# Tên thân thiện cho thông báo tiếng Việt.
_FRIENDLY = {
    "heic": "HEIC/HEIF (ảnh iPhone)",
    "avif": "AVIF",
    "jxl": "JPEG XL",
    "pdf": "file PDF chứ không phải ảnh",
    "html": "trang HTML — link tải ảnh trả về trang web chứ không phải ảnh",
    "json": "dữ liệu JSON — link tải ảnh trả về lỗi chứ không phải ảnh",
    "svg": "SVG (ảnh vector)",
    "": "không nhận dạng được định dạng",
}

_HEIF_BRANDS = {
    b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
    b"mif1", b"msf1",
}
_AVIF_BRANDS = {b"avif", b"avis"}


class UnsupportedImage(ValueError):
    """Bytes không phải ảnh Pillow đọc được (kèm nhãn định dạng để báo user)."""

    def __init__(self, fmt: str, size: int = 0) -> None:
        self.fmt = fmt or ""
        self.size = int(size or 0)
        self.label = _FRIENDLY.get(self.fmt, self.fmt or _FRIENDLY[""])
        super().__init__(f"ảnh không đọc được: {self.label} ({self.size} byte)")


def sniff_format(data: bytes | None) -> str:
    """Đoán định dạng từ magic bytes. '' nếu không nhận ra."""
    if not data or len(data) < 4:
        return ""
    head = bytes(data[:32])
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in _AVIF_BRANDS:
            return "avif"
        if brand in _HEIF_BRANDS:
            return "heic"
    if head.startswith(b"\xff\x0a") or head.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n"):
        return "jxl"
    if head.startswith(b"%PDF"):
        return "pdf"
    stripped = head.lstrip()[:20].lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<head")):
        return "html"
    if stripped.startswith((b"<svg", b"<?xml")):
        return "svg"
    if stripped[:1] in (b"{", b"["):
        return "json"
    return ""


def describe(data: bytes | None) -> dict[str, Any]:
    """Mô tả ngắn cho log: kích thước + magic bytes + định dạng đoán được."""
    raw = data or b""
    return {
        "bytes": len(raw),
        "sniff": sniff_format(raw) or "unknown",
        "magic": raw[:12].hex(),
    }


def _register_plugins() -> None:
    """Nạp plugin HEIF/AVIF/JXL nếu môi trường có — không có thì bỏ qua."""
    global _plugins_ready
    if _plugins_ready:
        return
    with _plugin_lock:
        if _plugins_ready:
            return
        for mod, fn in (("pillow_heif", "register_heif_opener"),
                        ("pillow_avif", ""),
                        ("pillow_jxl", "")):
            try:
                m = __import__(mod)
                if fn:
                    getattr(m, fn)()
            except Exception:
                continue
        _plugins_ready = True


def normalize(data: bytes | None, *, max_dim: int = 0,
              jpeg_quality: int = 90) -> tuple[bytes, str]:
    """Bytes ảnh bất kỳ → (bytes, mime) mà mọi provider vision đều nhận.

    - JPEG/PNG/GIF/WEBP: giữ nguyên bytes (không re-encode, không mất chất).
    - Định dạng khác Pillow đọc được (BMP/TIFF/HEIC khi có plugin…): chuyển JPEG.
    - ``max_dim`` > 0: ép cạnh dài về đúng mức đó (0 = giữ nguyên).
    - Không đọc được → raise :class:`UnsupportedImage` (có ``.label`` tiếng Việt).
    """
    raw = data or b""
    if not raw:
        raise UnsupportedImage("", 0)

    from PIL import Image

    _register_plugins()
    # Chặn bom nén TRƯỚC `img.load()`. `Image.open` chỉ đọc header nên rẻ; còn
    # `load()` mới thật sự giải nén — một file 40KB khai báo 50.000×50.000 sẽ
    # ngốn ~10GB ở đúng dòng đó. Import trong hàm để tránh vòng import
    # (kiem_anh dùng lại sniff_format/describe của chính module này).
    from .kiem_anh import ImageRejected, kiem_anh
    try:
        kiem_anh(raw, max_bytes=1 << 62)   # trần byte do nơi gọi tự lo
    except ImageRejected as exc:
        logger.warning({"event": "image_bomb_blocked", "ly_do": str(exc)[:160], **describe(raw)})
        raise UnsupportedImage(sniff_format(raw), len(raw)) from exc

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        fmt = sniff_format(raw)
        logger.warning({"event": "image_unreadable", "error": str(exc)[:120], **describe(raw)})
        raise UnsupportedImage(fmt, len(raw)) from exc

    fmt = str(img.format or "").upper()
    resize_to: tuple[int, int] | None = None
    if max_dim > 0:
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            resize_to = (max(1, round(w * scale)), max(1, round(h * scale)))

    if fmt in _PASSTHROUGH and resize_to is None:
        return raw, _PASSTHROUGH[fmt]

    out = img
    if resize_to is not None:
        out = out.resize(resize_to, Image.LANCZOS)
    if out.mode not in ("RGB", "L"):
        out = out.convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=jpeg_quality)
    converted = buf.getvalue()
    logger.info({"event": "image_normalized", "from": fmt or "unknown",
                 "size": list(img.size), "to_size": list(out.size),
                 "bytes": [len(raw), len(converted)]})
    return converted, "image/jpeg"


def normalize_or_none(data: bytes | None, **kw: Any) -> tuple[bytes, str] | None:
    """Như :func:`normalize` nhưng trả None thay vì raise (cho chỗ best-effort)."""
    try:
        return normalize(data, **kw)
    except Exception:
        return None


def is_supported(data: bytes | None) -> bool:
    """True nếu Pillow (kèm plugin) mở được bytes này."""
    return normalize_or_none(data) is not None


# Chuỗi lỗi Pillow/module này để tầng combo nhận ra "đổi provider cũng vô ích".
_IMAGE_ERROR_MARKERS = (
    "cannot identify image file",
    "ảnh không đọc được",
    "unsupportedimage",
)


def is_unsupported_image_error(text: str | None) -> bool:
    """True nếu lỗi là 'ảnh hỏng' — thử provider khác cũng chết y hệt."""
    low = str(text or "").lower()
    return any(mark in low for mark in _IMAGE_ERROR_MARKERS)
