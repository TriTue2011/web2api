# Nguồn: c2a-nv146/services/image_providers/nvidia_nim_image.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - `from services.image_providers._base import ...` → `from ._base import ...`
# - Bỏ `from services.config import config`: API key NVIDIA nay lấy HOÀN TOÀN
#   từ credentials do module.py truyền (apiKeys/api_keys/apiKey/api_key).
#   `_get_keys()` đổi thành `_get_keys(credentials)` — không còn nhánh đọc
#   config.data["providers"]["nvidia_nim"].
# - Bỏ `from utils.log import logger` → `logging.getLogger(__name__)` của stdlib.
# - `url_to_base64(image_b64)` → `url_to_base64(image_b64, tai_tu_url=self.tai_tu_url)`.
# - Giữ nguyên bảng kích thước NVIDIA đã đo thật, trần steps theo model, nhãn
#   tiếng Việt NVIDIA_SIZE_LABELS và logic image-to-image.

"""Bộ chuyển đổi tạo ảnh NVIDIA NIM.

Endpoint: https://ai.api.nvidia.com/v1/genai/{model}
Auth: Bearer token từ build.nvidia.com
Định dạng request/response riêng — cần chuyển đổi sang/từ định dạng OpenAI.

Tỉ lệ khung hình hỗ trợ (theo tài liệu NVIDIA):
  1:1, 16:9, 9:16, 5:4, 4:5, 3:2, 2:3

Model ví dụ: black-forest-labs/flux.2-klein-4b
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


# Kích thước NVIDIA CHO PHÉP, theo tài liệu chính thức của flux.2-klein-4b (chủ
# máy đưa 31/07/2026). Bảng cũ có 4 cặp KHÔNG có trong tài liệu — 1152x768,
# 768x1152, 1280x1024, 1024x1280 — tức người dùng chọn "3:2" hay "5:4" thì gửi đi
# một cặp số model không công bố hỗ trợ.
_NVIDIA_SIZE_MAP: dict[str, tuple[int, int]] = {
    "1024x1024": (1024, 1024),    # 1:1
    "1216x832":  (1216, 832),     # ~3:2 ngang
    "832x1216":  (832, 1216),     # ~2:3 dọc
    "1152x896":  (1152, 896),     # ~4:3 ngang
    "896x1152":  (896, 1152),     # ~3:4 dọc
    "1344x768":  (1344, 768),     # 16:9 ngang
    "768x1344":  (768, 1344),     # 9:16 dọc
}
_NVIDIA_DEFAULT_SIZE = "1344x768"  # 16:9
# Nhãn tiếng Việt cho menu chọn thông số (bot hỏi lại qua Tele/Zalo, và tab Ảnh).
NVIDIA_SIZE_LABELS: dict[str, str] = {
    "1024x1024": "1:1 vuông (1024×1024)",
    "1344x768":  "16:9 ngang (1344×768)",
    "768x1344":  "9:16 dọc (768×1344)",
    "1216x832":  "3:2 ngang (1216×832)",
    "832x1216":  "2:3 dọc (832×1216)",
    "1152x896":  "4:3 ngang (1152×896)",
    "896x1152":  "3:4 dọc (896×1152)",
}

# Trần `steps` theo TỪNG model. Dòng flux nhanh (schnell/klein) chưng cất còn vài
# bước nên NVIDIA CHẶN CỨNG ở 4 — gửi nhiều hơn là 422, không phải ảnh xấu:
#   {"type":"less_than_equal","loc":["body","steps"],"msg":"Input should be less
#    than or equal to 4","input":8,"ctx":{"le":4}}
# (dò thật bằng key của máy chủ ngày 30/07). Mặc định cũ 28 bước làm mọi lời gọi
# tới hai model này hỏng ngay, kể cả khi người dùng không đụng gì tới `steps`.
_TRAN_STEPS: dict[str, int] = {
    "black-forest-labs/flux.2-klein-4b": 4,
    "black-forest-labs/flux.1-schnell": 4,
}
_STEPS_MAC_DINH = 28


def _so_buoc(model: str, yeu_cau: Any) -> int:
    """Số bước hợp lệ cho model này (kẹp vào trần nếu model có trần)."""
    tran = _TRAN_STEPS.get(str(model or "").strip().lower())
    try:
        buoc = int(yeu_cau) if yeu_cau is not None else (tran or _STEPS_MAC_DINH)
    except (TypeError, ValueError):
        buoc = tran or _STEPS_MAC_DINH
    if tran:
        return max(1, min(buoc, tran))
    return max(5, buoc)


def _nvidia_size(size: str | None) -> tuple[int, int]:
    """Map OpenAI size → NVIDIA-compatible resolution."""
    if not size:
        return _NVIDIA_SIZE_MAP[_NVIDIA_DEFAULT_SIZE]
    if size in _NVIDIA_SIZE_MAP:
        return _NVIDIA_SIZE_MAP[size]
    # Try parsing WxH from existing SIZE_MAP, then map to closest nvidia size
    w, h = size_to_width_height(size)
    # Find best match by aspect ratio
    target_ratio = w / h if h else 1.0
    best = _NVIDIA_SIZE_MAP[_NVIDIA_DEFAULT_SIZE]
    best_diff = float("inf")
    for nw, nh in _NVIDIA_SIZE_MAP.values():
        n_ratio = nw / nh if nh else 1.0
        diff = abs(target_ratio - n_ratio)
        if diff < best_diff:
            best_diff = diff
            best = (nw, nh)
    return best


class NvidiaNimImageAdapter(BaseImageAdapter):
    """NVIDIA NIM Image Generation adapter.

    Uses NVIDIA's image generation endpoint (different from chat endpoint).
    API key do module.py truyền trong credentials (xoay key theo _key_index).
    """

    BASE_URL = "https://ai.api.nvidia.com/v1/genai"
    supports_image_edit = True

    @staticmethod
    def _get_keys(credentials: dict[str, Any] | None) -> list[str]:
        """Đọc key từ credentials do module.py truyền (thay cho config.data)."""
        if not isinstance(credentials, dict):
            return []
        many = credentials.get("apiKeys") or credentials.get("api_keys") or []
        if isinstance(many, list):
            keys = [str(key).strip() for key in many if str(key).strip()]
            if keys:
                return keys
        one = str(credentials.get("apiKey") or credentials.get("api_key") or "").strip()
        return [one] if one else []

    def build_url(self, model: str, credentials: dict[str, Any] | None) -> str:
        return f"{self.BASE_URL}/{model}"

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        from ._base import first_image_bytes_mime

        prompt = str(body.get("prompt") or "")
        size = str(body.get("size") or "")
        w, h = _nvidia_size(size if size else None)

        ra: dict[str, Any] = {
            "prompt": prompt,
            "width": w,
            "height": h,
            "seed": int(body.get("seed", 0)),
            "steps": _so_buoc(model, body.get("steps")),
        }
        # SỬA ẢNH THEO ẢNH GỐC (image-to-image). NVIDIA nhận khoá `image` là một
        # DANH SÁCH data URL (tài liệu chính thức: "image": ["data:image/png;base64,…"]).
        anh = body.get("image")
        if anh:
            ds = anh if isinstance(anh, (list, tuple)) else [anh]
            hop_le = [str(x) for x in ds if str(x or "").startswith("data:image/")]
            if hop_le:
                ra["image"] = hop_le
        else:
            # Protocol image-edits chuyển file upload thành `images` (tuple
            # bytes, filename, mime), không phải khoá `image`.
            raw, mime = first_image_bytes_mime(body.get("images") or [])
            if raw:
                data_url = base64.b64encode(raw).decode("ascii")
                ra["image"] = [f"data:{mime or 'image/png'};base64,{data_url}"]
        return ra

    def build_headers(
        self,
        credentials: dict[str, Any] | None,
        request_body: dict[str, Any],
        model: str,
        body: dict[str, Any],
    ) -> dict[str, str]:
        keys = self._get_keys(credentials)
        api_key = keys[0] if keys else ""
        if credentials and isinstance(credentials, dict):
            try:
                key_index = int(credentials.get("_key_index") or 0)
            except (TypeError, ValueError):
                key_index = 0
            # Dispatcher đặt `_key_index` theo từng retry để xoay key.
            if keys:
                api_key = keys[key_index % len(keys)]
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def parse_response(self, response: Any) -> dict[str, Any] | None:
        """Parse NVIDIA image gen response → OpenAI image format."""
        if not hasattr(response, "json"):
            return None

        try:
            data = response.json()
        except Exception as exc:
            logger.error({"event": "nvidia_image_parse_error", "error": str(exc)})
            return None

        # Response format: {"artifacts":[{"base64":"..."}]} or {"image":"..."}
        image_b64 = ""

        # NVIDIA returns artifacts array with base64
        artifacts = data.get("artifacts") or []
        if isinstance(artifacts, list) and artifacts:
            first = artifacts[0]
            if isinstance(first, dict):
                image_b64 = first.get("base64") or first.get("image") or ""
            elif isinstance(first, str):
                image_b64 = first

        if not image_b64:
            # Try direct image field
            image_b64 = data.get("image") or ""

        if not image_b64:
            images = data.get("images") or data.get("data") or []
            if isinstance(images, list) and images:
                first = images[0]
                if isinstance(first, dict):
                    image_b64 = first.get("image") or first.get("b64_json") or first.get("url") or first.get("base64") or ""
                elif isinstance(first, str):
                    image_b64 = first

        if not image_b64:
            logger.error({"event": "nvidia_image_no_data", "keys": list(data.keys())[:5]})
            return None

        # If it's a URL, convert to base64 (qua hàm đã kiểm SSRF của module.py)
        if image_b64.startswith("http"):
            image_b64 = url_to_base64(image_b64, tai_tu_url=self.tai_tu_url)

        return {"data": [{"b64_json": image_b64}]}

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Convert parsed result → OpenAI image response format."""
        data = parsed.get("data") or []
        normalized_data = []
        for item in data:
            b64 = item.get("b64_json") or ""
            if b64:
                if not b64.startswith("data:"):
                    b64 = f"data:image/png;base64,{b64}"
                normalized_data.append({"b64_json": b64, "revised_prompt": str(body.get("prompt") or "")})

        if not normalized_data:
            return {"created": now_sec(), "data": []}

        return {"created": now_sec(), "data": normalized_data}

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        """Test connectivity to NVIDIA image gen endpoint."""
        try:
            keys = self._get_keys(credentials)
            api_key = keys[0] if keys else ""
            resp = requests.get(
                "https://integrate.api.nvidia.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            try:
                return resp.status_code == 200
            finally:
                resp.close()
        except Exception:
            return False


nvidia_nim_image_adapter = NvidiaNimImageAdapter()
