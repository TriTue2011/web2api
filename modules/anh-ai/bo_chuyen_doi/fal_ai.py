# Nguồn: c2a-nv146/services/image_providers/fal_ai.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - `from services.image_providers._base import ...` → `from ._base import ...`
# - Bỏ `from utils.log import logger` → `logging.getLogger(__name__)` của stdlib.
# - Bỏ `from services.net_guard import check_url`: status_url từ response vẫn
#   được giới hạn host bằng kiểm tra nội bộ (chỉ chấp nhận queue.fal.run) để
#   response giả mạo không biến worker thành SSRF trong lúc polling.
# - `url_to_base64(url)` → `url_to_base64(url, tai_tu_url=self.tai_tu_url)`:
#   adapter không tự tải URL; module.py gán `adapter.tai_tu_url` (hàm đã kiểm
#   SSRF) sau khi tạo adapter.

"""Bộ chuyển đổi Fal.ai — adapter polling bất đồng bộ qua queue API.

Model format: fal_ai/fal-ai/flux/schnell
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from curl_cffi import requests

from ._base import (
    BaseImageAdapter,
    POLL_INTERVAL_S,
    POLL_TIMEOUT_S,
    now_sec,
    sleep_s,
    url_to_base64,
)

logger = logging.getLogger(__name__)

# Host duy nhất được phép khi polling status_url (chống SSRF từ response giả).
_FAL_POLL_HOSTS = {"queue.fal.run"}


def _kiem_status_url(status_url: str) -> str:
    """Chỉ chấp nhận status_url thuộc host của Fal.ai."""
    host = (urlparse(str(status_url)).hostname or "").lower()
    if host not in _FAL_POLL_HOSTS:
        raise RuntimeError(f"Fal.ai trả status_url ngoài host cho phép: {host or status_url[:60]}")
    return str(status_url)


class FalAIAdapter(BaseImageAdapter):
    """Fal.ai async queue adapter.

    Submits to queue API, polls status_url until completion.
    """

    BASE_URL = "https://queue.fal.run"
    supports_image_edit = True

    def build_url(self, model: str, credentials: dict[str, Any] | None) -> str:
        return f"{self.BASE_URL}/{model}"

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        import base64 as _b64

        from ._base import first_image_bytes_mime

        prompt = str(body.get("prompt") or "")
        size = str(body.get("size") or "1792x1024")

        # Map OpenAI size to fal.ai image_size
        size_map = {
            "1024x1024": "square_hd",
            "1792x1024": "landscape_16_9",
            "1024x1792": "portrait_16_9",
            "1280x896": "landscape_4_3",
            "896x1280": "portrait_4_3",
        }

        out: dict[str, Any] = {
            "prompt": prompt,
            "image_size": size_map.get(size, "landscape_16_9"),
            "num_images": max(1, min(4, int(body.get("n") or 1))),
        }
        # Best-effort img2img: many fal endpoints accept image_url / image_url list.
        # Send data-URL so models like flux/dev/image-to-image can use it; plain
        # text→image models ignore unknown fields.
        raw, mime = first_image_bytes_mime(body.get("images") or [])
        if raw:
            data_url = f"data:{mime or 'image/png'};base64,{_b64.b64encode(raw).decode()}"
            out["image_url"] = data_url
            out["image_urls"] = [data_url]
            out["strength"] = 0.55
        return out

    def build_headers(
        self,
        credentials: dict[str, Any] | None,
        request_body: dict[str, Any],
        model: str,
        body: dict[str, Any],
    ) -> dict[str, str]:
        api_key = ""
        if credentials and isinstance(credentials, dict):
            api_key = str(credentials.get("apiKey") or credentials.get("accessToken") or "")
        return {
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json",
        }

    def parse_response(self, response: Any) -> dict[str, Any] | None:
        """Submit to queue, poll for result."""
        if not hasattr(response, "json"):
            return None

        data = response.json()
        status_url = data.get("status_url")
        if not status_url:
            raise RuntimeError("Fal.ai không trả về status_url cho tác vụ đã gửi")

        # status_url là dữ liệu từ response bên ngoài, không phải URL cấu hình
        # do admin tin cậy — giới hạn host trước khi polling.
        status_url = _kiem_status_url(str(status_url))

        # Poll for completion
        elapsed = 0.0
        while elapsed < POLL_TIMEOUT_S:
            sleep_s(POLL_INTERVAL_S)
            elapsed += POLL_INTERVAL_S

            try:
                poll_resp = requests.get(status_url, timeout=30)
                try:
                    poll_data = poll_resp.json()
                finally:
                    try:
                        poll_resp.close()
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning({"event": "fal_ai_poll_error", "error": str(exc)})
                continue

            status = poll_data.get("status", "")
            if status == "COMPLETED":
                result = poll_data.get("result") or poll_data
                images = (
                    result.get("images") or
                    [result.get("image")] if result.get("image") else
                    []
                )
                if images:
                    b64_list = []
                    for img in images:
                        if isinstance(img, dict) and img.get("url"):
                            b64_list.append({
                                "b64_json": url_to_base64(img["url"], tai_tu_url=self.tai_tu_url),
                            })
                    return {"data": b64_list}

                logger.error({"event": "fal_ai_no_images", "result": str(result)[:200]})
                raise RuntimeError("Fal.ai hoàn tất nhưng không trả ảnh")

            elif status in ("FAILED", "CANCELLED"):
                error_msg = str(poll_data.get("error") or "unknown")
                logger.error({"event": "fal_ai_failed", "status": status, "error": error_msg})
                raise RuntimeError(f"Fal.ai {status.lower()}: {error_msg[:300]}")

        logger.error({"event": "fal_ai_timeout", "elapsed": elapsed})
        raise RuntimeError(f"Fal.ai quá thời gian chờ ({int(POLL_TIMEOUT_S)} giây)")

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        data = parsed.get("data") or []
        return {"created": now_sec(), "data": data}

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        try:
            resp = requests.get("https://queue.fal.run", timeout=10)
            try:
                return resp.status_code < 500
            finally:
                resp.close()
        except Exception:
            return False


fal_ai_adapter = FalAIAdapter()
