# Nguồn: c2a-nv146/services/image_providers/_base.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - Import `from services.image_providers._base import ...` ở các adapter → `from ._base import ...`
# - Bỏ `services.net_guard`: adapter KHÔNG tự tải URL. `url_to_base64` nay nhận
#   tham số `tai_tu_url(url, timeout)` do module.py truyền vào (đã kiểm SSRF
#   trước khi gọi adapter). Nếu chưa được gán, ném RuntimeError rõ ràng thay vì
#   tải trực tiếp.
# - BaseImageAdapter có thêm thuộc tính lớp `tai_tu_url` (mặc định None) để
#   module.py gán sau khi tạo adapter.

"""Lớp nền và hàm phụ trợ dùng chung cho các bộ chuyển đổi ảnh AI."""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

from curl_cffi import requests

# Cấu hình polling cho adapter bất đồng bộ
POLL_INTERVAL_S = 1.5
POLL_TIMEOUT_S = 120

# OpenAI size → width x height (16:9 mặc định)
SIZE_MAP: dict[str, tuple[int, int]] = {
    "1024x1024": (1024, 1024),    # 1:1
    "1792x1024": (1792, 1024),    # 16:9 ← DEFAULT
    "1024x1792": (1024, 1792),    # 9:16
    "1280x896":  (1280, 896),     # ~4:3 landscape
    "896x1280":  (896, 1280),     # ~4:3 portrait
}

DEFAULT_SIZE = "1792x1024"


def size_to_width_height(size: str | None) -> tuple[int, int]:
    """Convert OpenAI size string → (width, height). Default 16:9."""
    if not size:
        return SIZE_MAP[DEFAULT_SIZE]
    if size in SIZE_MAP:
        return SIZE_MAP[size]
    # Try parsing "WxH" format
    try:
        parts = size.split("x")
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
    except (ValueError, TypeError):
        pass
    return SIZE_MAP[DEFAULT_SIZE]


def first_image_bytes_mime(
    images: Any,
) -> tuple[bytes | None, str]:
    """Extract first reference image from body.images variants.

    Accepts:
      - (bytes, filename, mime) / [bytes, filename, mime]  ← chat/edit path
      - bare bytes
      - data:image/...;base64,... string
    Returns (raw_bytes_or_None, mime).
    """
    if not images:
        return None, "image/png"
    first = images[0] if isinstance(images, (list, tuple)) else images
    mime = "image/png"
    raw: bytes | None = None
    if isinstance(first, (tuple, list)) and first:
        cand = first[0]
        if isinstance(cand, (bytes, bytearray)):
            raw = bytes(cand)
        if len(first) >= 3 and first[2]:
            mime = str(first[2])
        elif len(first) >= 2 and isinstance(first[1], str) and "/" in str(first[1]):
            # sometimes (bytes, mime) without filename
            if not str(first[1]).endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                mime = str(first[1])
    elif isinstance(first, (bytes, bytearray)):
        raw = bytes(first)
    elif isinstance(first, str) and first.startswith("data:"):
        try:
            header, data = first.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or mime
            raw = base64.b64decode(data)
        except Exception:
            raw = None
    elif isinstance(first, dict):
        b64 = first.get("b64_json") or first.get("data") or ""
        mime = str(first.get("mime") or first.get("mime_type") or mime)
        if b64:
            try:
                raw = base64.b64decode(str(b64).split(",")[-1])
            except Exception:
                raw = None
    return raw, mime


def size_to_aspect_ratio(size: str | None) -> str:
    """Convert OpenAI size → aspect ratio string (e.g. '16:9')."""
    w, h = size_to_width_height(size)
    if w == h:
        return "1:1"
    if w > h:
        if w / h > 1.6:
            return "16:9"
        return "4:3"
    else:
        if h / w > 1.6:
            return "9:16"
        return "3:4"


def url_to_base64(
    url: str,
    timeout: int = 30,
    tai_tu_url: Callable[[str, int], bytes] | None = None,
) -> str:
    """Tải ảnh từ URL rồi trả về chuỗi base64.

    `tai_tu_url`: hàm do module.py truyền vào, ký hiệu `tai_tu_url(url, timeout)`
    trả về bytes thô. module.py chịu trách nhiệm kiểm tra SSRF TRƯỚC khi gọi
    adapter — adapter không bao giờ tự tải URL lạ. Chuỗi data: URL được tách
    trực tiếp, không cần tải.
    """
    raw = str(url or "").strip()
    if raw.startswith("data:"):
        # data:image/...;base64,xxx
        try:
            return raw.split(",", 1)[1]
        except Exception:
            return ""
    if tai_tu_url is None:
        raise RuntimeError(
            "url_to_base64 cần module.py gán adapter.tai_tu_url(url, timeout) — "
            "adapter không tự tải URL chưa qua kiểm tra"
        )
    data = tai_tu_url(raw, timeout)
    return base64.b64encode(bytes(data)).decode("ascii")


def now_sec() -> int:
    """Current time in seconds (Unix timestamp)."""
    return int(time.time())


def sleep_s(seconds: float) -> None:
    """Sleep for seconds."""
    time.sleep(seconds)


class BaseImageAdapter:
    """Lớp nền cho các bộ chuyển đổi tạo ảnh.

    Mỗi adapter triển khai 6 phương thức lõi (giữ nguyên tên vì module.py dùng):
    - build_url(model, credentials) -> str
    - build_body(model, body) -> dict
    - build_headers(credentials, request_body, model, body) -> dict
    - parse_response(response) -> dict | None  (tuỳ chọn, cho polling bất đồng bộ)
    - normalize(parsed, body) -> dict  (định dạng tương thích OpenAI)
    - test_connection(credentials) -> bool

    Thuộc tính `tai_tu_url`: module.py GÁN sau khi tạo adapter. Là hàm đã qua
    kiểm SSRF, ký hiệu `tai_tu_url(url, timeout) -> bytes`, dùng khi adapter
    cần tải ảnh từ URL do nhà cung cấp trả về (kết quả polling).
    """

    no_auth: bool = False
    # Image edits must fail clearly unless an adapter actually forwards the
    # reference image. Returning a brand-new text-to-image result is worse
    # than an error because callers believe their source image was edited.
    supports_image_edit: bool = False

    # Hàm tải URL do module.py truyền vào (đã kiểm SSRF). None = chưa gán.
    tai_tu_url: Callable[[str, int], bytes] | None = None

    def build_url(self, model: str, credentials: dict[str, Any] | None) -> str:
        raise NotImplementedError

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def build_headers(
        self,
        credentials: dict[str, Any] | None,
        request_body: dict[str, Any],
        model: str,
        body: dict[str, Any],
    ) -> dict[str, str]:
        raise NotImplementedError

    def parse_response(self, response: Any) -> dict[str, Any] | None:
        """Optional: custom response parsing (async polling, SSE, etc.)."""
        return None

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Convert provider response to OpenAI format: {created, data: [{b64_json}]}."""
        raise NotImplementedError

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        """Quick connection test."""
        return True
