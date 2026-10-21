# Nguồn: c2a-nv146/services/image_providers/sdwebui.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - `from services.image_providers._base import ...` → `from ._base import ...`
# - Bỏ `from utils.log import logger` → `logging.getLogger(__name__)` của stdlib.
# - base_url: giữ constructor `SDWebUIAdapter(base_url=...)` (mặc định localhost:7860);
#   build_url nay ƯU TIÊN credentials["base_url"] do module.py truyền nếu có.

"""Bộ chuyển đổi SD WebUI (AUTOMATIC1111) — chạy local, không cần API key."""

from __future__ import annotations

import base64
import logging
from typing import Any

from curl_cffi import requests

from ._base import BaseImageAdapter, now_sec, size_to_width_height

logger = logging.getLogger(__name__)


class SDWebUIAdapter(BaseImageAdapter):
    """Stable Diffusion Web UI (AUTOMATIC1111) adapter.

    NoAuth — runs locally at http://localhost:7860.
    Supports txt2img and img2img when body.images / reference is present.
    """

    no_auth = True
    supports_image_edit = True

    def __init__(self, base_url: str = "http://localhost:7860"):
        self.base_url = base_url.rstrip("/")
        self._use_img2img = False

    def _co_so(self, credentials: dict[str, Any] | None) -> str:
        """base_url do module.py truyền trong credentials được ưu tiên."""
        if credentials and isinstance(credentials, dict):
            base = str(credentials.get("base_url") or "").rstrip("/")
            if base:
                return base
        return self.base_url

    def build_url(self, model: str, credentials: dict[str, Any] | None) -> str:
        base = self._co_so(credentials)
        if self._use_img2img:
            return f"{base}/sdapi/v1/img2img"
        return f"{base}/sdapi/v1/txt2img"

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        from ._base import first_image_bytes_mime

        prompt = str(body.get("prompt") or "")
        n = max(1, min(4, int(body.get("n") or 1)))
        size = str(body.get("size") or "1792x1024")
        w, h = size_to_width_height(size)

        payload: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": "",
            "width": w,
            "height": h,
            "steps": 20,
            "batch_size": n,
            "cfg_scale": 7,
            "sampler_name": "Euler a",
        }
        # Img2img when chat/edit attached a reference image
        raw, _mime = first_image_bytes_mime(body.get("images") or [])
        if raw:
            self._use_img2img = True
            payload["init_images"] = [base64.b64encode(raw).decode("ascii")]
            payload["denoising_strength"] = 0.55
        else:
            self._use_img2img = False
        return payload

    def build_headers(
        self,
        credentials: dict[str, Any] | None,
        request_body: dict[str, Any],
        model: str,
        body: dict[str, Any],
    ) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        # SD WebUI returns {"images": ["base64...", ...]}
        images = parsed.get("images") or []
        data = [
            {"b64_json": img}
            for img in images
            if isinstance(img, str)
        ]
        return {"created": now_sec(), "data": data}

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        try:
            resp = requests.get(f"{self._co_so(credentials)}/sdapi/v1/sd-models", timeout=5)
            try:
                return resp.status_code == 200
            finally:
                resp.close()
        except Exception:
            return False


sdwebui_adapter = SDWebUIAdapter()
