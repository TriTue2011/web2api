# Nguồn: c2a-nv146/services/image_providers/custom_openai_image.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - `from services.image_providers._base import ...` → `from ._base import ...`
# - Bỏ `from services.config import config`: cấu hình provider (base_url,
#   api_key/api_keys) nay LẤY TRỰC TIẾP từ dict `credentials` do module.py
#   truyền vào mỗi request — không còn đọc config.data["custom_providers"].
#   base_url được lưu tạm trên instance trong build_url để parse_response nối
#   đường dẫn tương đối (parse_response không nhận credentials).
# - Bỏ `from utils.log import logger` → `logging.getLogger(__name__)` của stdlib.
# - `url_to_base64(img_url)` → `url_to_base64(img_url, tai_tu_url=self.tai_tu_url)`.

"""Bộ chuyển đổi ảnh cho provider tuỳ biến tương thích OpenAI — dùng endpoint chat.

Dành cho provider tuỳ biến hỗ trợ tạo ảnh qua API chat (vd máy chủ API Gemini
với /v1/responses hoặc công cụ tạo ảnh dựng sẵn).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from curl_cffi import requests

from ._base import (
    BaseImageAdapter,
    now_sec,
    size_to_width_height,
    url_to_base64,
)

logger = logging.getLogger(__name__)


class CustomOpenAIImageAdapter(BaseImageAdapter):
    """Generic image adapter for custom providers — uses chat endpoint."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        # base_url do module.py truyền trong credentials; lưu tạm tại build_url
        # để parse_response/test_connection dùng (chúng không nhận credentials
        # đầy đủ trong mọi đường gọi).
        self._base_url = ""

    supports_image_edit = True

    def _get_api_keys(self, credentials: dict[str, Any] | None) -> list[str]:
        cfg = credentials if isinstance(credentials, dict) else {}
        raw = cfg.get("api_keys") or cfg.get("apiKeys") or []
        if not isinstance(raw, list):
            raw = []
        keys = [str(key).strip() for key in raw if str(key).strip()]
        single = str(cfg.get("api_key") or cfg.get("apiKey") or "").strip()
        if single and single not in keys:
            keys.insert(0, single)
        return keys

    def get_key_count(self, credentials: dict[str, Any] | None) -> int:
        """Expose all configured keys to the shared image retry loop."""
        return max(1, len(self._get_api_keys(credentials)))

    def build_url(self, model: str, credentials: dict[str, Any] | None) -> str:
        cfg = credentials if isinstance(credentials, dict) else {}
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        self._base_url = base_url
        base_has_v1 = base_url.endswith("/v1")
        # Detect API style
        if base_has_v1 or "deepseek.com" in base_url:
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        prompt = str(body.get("prompt") or "")
        size = str(body.get("size") or "1792x1024")
        w, h = size_to_width_height(size)
        image_data = body.get("image") or body.get("image_data")  # from edit endpoint (base64)
        # Also handle images from _handle_adapter_edit (list of (bytes, filename, mime) tuples)
        images_raw = body.get("images") or []

        content: list[dict[str, Any]] = []
        if image_data:
            # Image edit mode: include original image + editing instruction
            content.append({
                "type": "text",
                "text": f"Edit this image based on the following instruction: {prompt}\nSize: {w}x{h}\nReturn the edited image as a base64 data URL."
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_data}"}
            })
        elif images_raw and len(images_raw) > 0:
            # Image edit with raw upload bytes
            import base64 as _b64
            content.append({
                "type": "text",
                "text": f"Edit this image based on the following instruction: {prompt}\nSize: {w}x{h}\nReturn the edited image as a base64 data URL."
            })
            for img_tuple in images_raw[:1]:  # Take first image
                if isinstance(img_tuple, (list, tuple)) and len(img_tuple) > 0:
                    img_bytes = img_tuple[0] if isinstance(img_tuple[0], bytes) else img_tuple
                else:
                    img_bytes = img_tuple if isinstance(img_tuple, bytes) else str(img_tuple).encode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{_b64.b64encode(img_bytes).decode()}"}
                })
        else:
            # Image generation mode
            content.append({
                "type": "text",
                "text": f"Generate an image based on this description: {prompt}\nSize: {w}x{h}\nReturn the image as a base64 data URL."
            })

        return {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
            "temperature": 0.9,
        }

    def build_headers(
        self,
        credentials: dict[str, Any] | None,
        request_body: dict[str, Any],
        model: str,
        body: dict[str, Any],
    ) -> dict[str, str]:
        keys = self._get_api_keys(credentials)
        try:
            key_index = int((credentials or {}).get("_key_index") or 0)
        except (TypeError, ValueError):
            key_index = 0
        api_key = keys[key_index % len(keys)] if keys else ""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _noi_base_url(self, img_url: str) -> str:
        """Nối đường dẫn tương đối (/media/...) với base_url của provider."""
        if img_url.startswith("/") and self._base_url:
            return self._base_url + img_url
        return img_url

    def parse_response(self, response: Any) -> dict[str, Any] | None:
        """Parse chat response to extract generated image (base64)."""
        if not hasattr(response, "json"):
            return None

        try:
            data = response.json()
        except Exception as exc:
            logger.error({"event": "custom_image_parse_error", "error": str(exc)})
            return None

        choices = data.get("choices") or []
        for choice in choices:
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            if not content:
                continue

            # Handle array content (multi-part)
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            parts.append(str(part.get("text") or ""))
                        elif part.get("type") == "image_url":
                            url = str(part.get("image_url", {}).get("url") or "")
                            if url:
                                parts.append(f"![]({url})")
                content = "\n".join(parts)

            if not content:
                continue

            # Try to extract base64 image from response
            import re
            # Match data:image/...;base64,...
            match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
            if match:
                return {"data": [{"b64_json": match.group(1)}]}

            # Maybe the entire response is a base64 image
            if content.startswith('/9j/') or content.startswith('iVBOR'):
                return {"data": [{"b64_json": content}]}

            # Try markdown image syntax: ![alt](url)
            md_match = re.search(r'!\[[^\]]*\]\(([^)]+)\)', content)
            if md_match:
                try:
                    img_url = self._noi_base_url(md_match.group(1))
                    b64 = url_to_base64(img_url, tai_tu_url=self.tai_tu_url)
                    return {"data": [{"b64_json": b64}]}
                except Exception:
                    pass

            # If the response contains a URL to an image (absolute or relative)
            url_match = re.search(r'https?://[^\s"\')\]]+\.(?:png|jpg|jpeg|webp)[^\s"\')\]]*', content)
            if not url_match:
                # Also try relative paths like /media/img_xxx.png?token=yyy
                url_match = re.search(r'(/media/[^\s"\')\]]+\.(?:png|jpg|jpeg|webp)[^\s"\')\]]*)', content)
            if url_match:
                try:
                    img_url = self._noi_base_url(url_match.group(0))
                    b64 = url_to_base64(img_url, tai_tu_url=self.tai_tu_url)
                    return {"data": [{"b64_json": b64}]}
                except Exception:
                    pass

        logger.warning({"event": "custom_image_no_data", "provider": self.provider_id})
        return None

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        data = parsed.get("data") or []
        normalized_data = []
        for item in data:
            b64 = item.get("b64_json") or ""
            if b64 and not b64.startswith("data:"):
                b64 = f"data:image/png;base64,{b64}"
            if b64:
                normalized_data.append({"b64_json": b64, "revised_prompt": str(body.get("prompt") or "")})
        return {"created": now_sec(), "data": normalized_data} if normalized_data else {"created": now_sec(), "data": []}

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        base_url = self._base_url
        if not base_url and isinstance(credentials, dict):
            base_url = str(credentials.get("base_url") or "").rstrip("/")
        if not base_url:
            return False
        keys = self._get_api_keys(credentials)
        api_key = keys[0] if keys else ""
        try:
            # Detect API style for models endpoint
            models_path = "/models" if "deepseek.com" in base_url or base_url.endswith("/v1") else "/v1/models"
            resp = requests.get(
                f"{base_url}{models_path}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            try:
                return resp.status_code == 200
            finally:
                resp.close()
        except Exception:
            return False
