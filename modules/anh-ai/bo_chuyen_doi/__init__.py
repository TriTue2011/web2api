# Sổ đăng ký adapter ảnh — port từ
# c2a-nv146/services/image_providers/__init__.py (commit d879b03).
# Đã chỉnh khi port sang web2api:
# - custom_openai_image không cần cp_id từ tiền tố "custom:<id>": module.py đã
#   tách nhà cung cấp bằng đường model anh_ai/custom:<id>/<model> và truyền
#   base_url qua credentials; adapter custom chỉ dùng base_url + api_keys.
# - get_image_adapter(nha) giữ ngữ nghĩa như c2a (sdwebui/flow luôn trả thể hiện
#   MỚI vì giữ trạng thái tạm theo request; các adapter còn lại là singleton).

"""Sổ đăng ký bộ chuyển đổi tạo/sửa ảnh AI theo nhà cung cấp."""

from __future__ import annotations

from typing import Any

from ._base import BaseImageAdapter
from .bfl import BFLAdapter
from .cloudflare import CloudflareAIAdapter
from .custom_openai_image import CustomOpenAIImageAdapter
from .fal_ai import FalAIAdapter
from .flow_google import FlowImageAdapter
from .gemini_image import GeminiImageAdapter
from .huggingface import HuggingFaceAdapter
from .nvidia_nim_image import NvidiaNimImageAdapter
from .sdwebui import SDWebUIAdapter
from .stability import StabilityAIAdapter

#: Ánh xạ khoá nhà cung cấp → thể hiện adapter (đủ 10 nhà theo manifest).
IMAGE_ADAPTERS: dict[str, BaseImageAdapter] = {
    "sdwebui": SDWebUIAdapter(),
    "huggingface": HuggingFaceAdapter(),
    "cloudflare_ai": CloudflareAIAdapter(),
    "fal_ai": FalAIAdapter(),
    "stability_ai": StabilityAIAdapter(),
    "black_forest_labs": BFLAdapter(),
    "gemini": GeminiImageAdapter(),
    "nvidia_nim_image": NvidiaNimImageAdapter(),
    "flow": FlowImageAdapter(),
    "custom": CustomOpenAIImageAdapter("custom"),
}

#: Adapter nào KHÔNG được dùng lại giữa các request (giữ trạng thái tạm).
_ADAPTER_MOT_LAN = ("sdwebui", "flow")


def get_image_adapter(nha: str) -> BaseImageAdapter | None:
    """Tìm adapter theo khoá nhà cung cấp.

    «custom:<id-may-chu>» → adapter OpenAI-compatible chung (module truyền
    base_url + khoá qua credentials). Khoá lạ → None.
    """
    if not nha or not isinstance(nha, str):
        return None
    if nha in _ADAPTER_MOT_LAN:
        # sdwebui/flow giữ tập tài khoản đã thử trong một request — không được
        # là singleton: hai lượt đồng thời có thể ghi đè trạng thái tạm của
        # nhau và retry nhầm endpoint/tài khoản (lỗi ghi trong c2a).
        return IMAGE_ADAPTERS.get(nha)
    if nha.startswith("custom:"):
        return IMAGE_ADAPTERS.get("custom")
    return IMAGE_ADAPTERS.get(nha)


def is_image_provider(nha: str) -> bool:
    """True nếu khoá này là nhà cung cấp ảnh hợp lệ."""
    if not isinstance(nha, str):
        return False
    if nha.startswith("custom:"):
        return len(nha) > len("custom:")
    return nha in IMAGE_ADAPTERS and nha != "custom"


def danh_sach_nha_cung_cap() -> list[str]:
    """Mọi khoá nhà cung cấp (không tính pseudo-key «custom»)."""
    return sorted(k for k in IMAGE_ADAPTERS if k != "custom")


def _thi_tan_adapter_moi(mau: Any) -> Any:
    """Thể hiện mới cho mỗi lượt gọi (sdwebui/flow/custom giữ trạng thái tạm
    theo request nên không được là singleton)."""
    pid = getattr(mau, "provider_id", None)
    try:
        return type(mau)(pid) if pid is not None else type(mau)()
    except Exception:
        try:
            return type(mau)()
        except Exception:
            return mau


def lay_thi_tan_moi(nha: str) -> BaseImageAdapter | None:
    """Trả thể hiện MỚI cho mỗi lượt gọi (c2a: sdwebui/flow giữ trạng thái tạm
    theo request nên không được là singleton — tạo mới hết cho đồng nhất)."""
    adapter = get_image_adapter(nha)
    if adapter is None:
        return None
    return _thi_tan_adapter_moi(adapter)
