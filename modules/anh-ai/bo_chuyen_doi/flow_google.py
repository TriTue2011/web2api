# Nguồn: c2a-nv146/services/image_providers/flow_google.py (commit d879b03)
# Đã chỉnh khi port sang web2api:
# - `from services.image_providers._base import ...` → `from ._base import ...`
# - Bỏ `from services.config import config`: cấu hình Flow (solver_url, accounts)
#   nay lấy từ credentials["_cau_hinh"] do module.py truyền vào (đọc lười: mỗi
#   lần truy cập đọc từ dict credentials của request hiện tại), không đọc config
#   toàn cục của c2a nữa.
# - Bỏ `from services.captcha import captcha_base`: chuẩn hoá solver_url bằng hàm
#   nội bộ _chuan_hoa_solver_url (solver_url bắt buộc phải khai trong cấu hình
#   module thì build_url mới chạy; thiếu → RuntimeError rõ ràng).
# - Bỏ `from utils.log import logger` → logging stdlib; bỏ note_provider_account
#   (c2a request_context) — web2api không có; ghi nhãn tài khoản vào log.
# - Bỏ `from utils.helper import VIDEO_GEN_MODELS` → tập VIDEO_GEN_MODELS nội bộ
#   (tên model video Flow đã biết; tên lạ khác vẫn đi qua vì không dám chắc).
# - Bỏ `services.account_recovery.flow_recover_and_notify` (c2a): giữ log + demote.
# - Bỏ `services.gemini_watermark.strip_watermark_b64_items` trong normalize():
#   theo spec NV192, gỡ dấu nước của nhà cung cấp KHÔNG port (vùng điều khoản
#   dịch vụ) — giữ nguyên ảnh trả về.
# - `_reorder_flow_account` (đang lưu cấu hình c2a): web2api chỉ reorder
#   TRONG TIẾN TRÌNH (danh sách ưu tiên _POOL_THU_TU), không ghi cấu hình.
# - parse_response: url_to_base64 dùng self.tai_tu_url do module.py GÁN (đã kiểm
#   SSRF), không tải trực tiếp nữa.

"""Bộ chuyển đổi tạo ảnh Google Labs Flow qua đường REST của captcha-solver.

Đoạn đo thẳng ngày 09–10/08/2026 được giữ nguyên vì là số liệu định tính cách
gọi: gọi thẳng `aisandbox-pa.googleapis.com` chạy tốt, chỉ cần bearer token
từ phiên đã đăng nhập cộng token reCAPTCHA Enterprise (đường REST của
captcha-solver) — lượt TẠO ẢNH đi bằng HTTP thuần, 30–33s thay vì 73–94s qua
giao diện; link ảnh là link gốc đã ký trên CDN.

Cấu hình module anh-ai cho nhà cung cấp này:
    solver_url   — địa chỉ REST của captcha-solver (VD http://127.0.0.1:8010);
                   bắt buộc, đi qua kiểm SSRF app.tu_xa lúc cài cấu hình.
    flow_accounts — JSON: [{"profile": "...", "project_id": "..."}] (mỗi tài
                   khoản là một browser profile + project Flow); xoay nghiêm
                   ngặt theo thứ tự ưu tiên, tài khoản hết ngạch nghỉ cooldown.
    api_key      — bearer của solver (captcha_solver_api_key trong c2a).
"""

from __future__ import annotations

import base64 as _b64
import logging
import re
import threading
import time
from typing import Any

from curl_cffi import requests

from ._base import BaseImageAdapter, first_image_bytes_mime, now_sec, url_to_base64

logger = logging.getLogger(__name__)

# ĐO THẲNG VÀO API 09/08/2026 (tài khoản google-benbap115). Flow kiểm
# `imageModelName` TRƯỚC cửa reCAPTCHA nên đọc được kết quả từng tên mà không
# tốn tín dụng nào: GEM_PIX_2 / NARWHAL / HARBOR_SEAL hợp lệ; GEM_PIX và
# IMAGEN_3_5 có thật nhưng tài khoản không có (404); NANO_BANANA_PRO 400
# INVALID_ARGUMENT — KHÔNG phải hằng số có thật (tên này sống sót trên đường
# DOM vì chỉ dùng làm khoá tra NHÃN dropdown, không bao giờ gửi xuống API).
_MODEL_ALIASES = {
    "banana": "NARWHAL",
    "banana-2": "NARWHAL",
    "narwhal": "NARWHAL",
    "banana-2-lite": "HARBOR_SEAL",
    "harbor-seal": "HARBOR_SEAL",
    "auto": "GEM_PIX_2",
    "": "GEM_PIX_2",
    "best": "GEM_PIX_2",
    "banana-pro": "GEM_PIX_2",
    "nano-banana-pro": "GEM_PIX_2",
    "gem-pix-2": "GEM_PIX_2",
}

# Model ảnh Flow đã bỏ. Giữ để _resolve_model nói thẳng "model đã bị bỏ" thay
# vì để tên rơi xuống nhánh "tên lạ" rồi gửi một chuỗi vô nghĩa xuống API.
_MODEL_ANH_DA_NGHI = {
    "imagen-4": "IMAGEN_3_5 trả 404 khi đo 09/08/2026",
    "imagen4": "IMAGEN_3_5 trả 404 khi đo 09/08/2026",
    "imagen": "IMAGEN_3_5 trả 404 khi đo 09/08/2026",
    "imagen-3-5": "IMAGEN_3_5 trả 404 khi đo 09/08/2026",
    "imagen3_5": "IMAGEN_3_5 trả 404 khi đo 09/08/2026",
}

# Model video Flow đã biết (đo đường DOM 08/08/2026): chặn ở cổng ảnh vì Flow
# đặt cả hai loại chung một không gian tên `flow/`, và gửi imageModelName là
# tên video sẽ hỏng mà không nói vì sao.
_VIDEO_GEN_MODELS = frozenset({
    "flow/veo-2", "flow/veo-3", "flow/veo-3-fast", "flow/veo-3.1",
    "flow/veo-3.1-fast", "flow/veo-3.1-fast@1x",
})

FLOW_MODELS = [
    {"id": "flow/banana-pro",    "label": "Nano Banana Pro",    "internal": "GEM_PIX_2"},
    {"id": "flow/banana-2",      "label": "Nano Banana 2",      "internal": "NARWHAL"},
    {"id": "flow/banana-2-lite", "label": "Nano Banana 2 Lite", "internal": "HARBOR_SEAL"},
    {"id": "flow/auto",          "label": "Auto (xoay model)",  "internal": "GEM_PIX_2"},
]

_ASPECT_FROM_SIZE: dict[tuple[int, int], str] = {
    (1024, 1024): "IMAGE_ASPECT_RATIO_SQUARE",
    (1792, 1024): "IMAGE_ASPECT_RATIO_LANDSCAPE",
    (1024, 1792): "IMAGE_ASPECT_RATIO_PORTRAIT",
    (1280, 896):  "IMAGE_ASPECT_RATIO_LANDSCAPE",
    (896, 1280):  "IMAGE_ASPECT_RATIO_PORTRAIT",
}

# ĐO THẲNG VÀO API 10/08/2026 (Google báo lỗi kèm TÊN TRƯỜNG nên quét miễn phí):
# SQUARE / LANDSCAPE / PORTRAIT / LANDSCAPE_FOUR_THREE / PORTRAIT_THREE_FOUR
# hợp lệ; LANDSCAPE_4_3 / PORTRAIT_3_4 KHÔNG tồn tại (lỗi đường DOM che mất vì
# chỉ dùng chuỗi làm khoá tra NHÃN, không bao giờ gửi xuống API).
_ASPECT_FROM_LABEL: dict[str, str] = {
    "16:9":      "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "4:3":       "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    "1:1":       "IMAGE_ASPECT_RATIO_SQUARE",
    "square":    "IMAGE_ASPECT_RATIO_SQUARE",
    "3:4":       "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR",
    "9:16":      "IMAGE_ASPECT_RATIO_PORTRAIT",
    "portrait":  "IMAGE_ASPECT_RATIO_PORTRAIT",
    "landscape": "IMAGE_ASPECT_RATIO_LANDSCAPE",
}

_ASPECT_LABEL_FROM_CONST: dict[str, str] = {
    "IMAGE_ASPECT_RATIO_LANDSCAPE": "16:9",
    "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": "4:3",
    "IMAGE_ASPECT_RATIO_SQUARE": "1:1",
    "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR": "3:4",
    "IMAGE_ASPECT_RATIO_PORTRAIT": "9:16",
}


def _resolve_model(model: str) -> str:
    """Ánh xạ 'flow/<bí danh>' → imageModelName thật của Flow."""
    raw = (model or "").strip().lower()
    if raw.startswith("flow/"):
        raw = raw[len("flow/"):]
    if raw in _MODEL_ALIASES:
        return _MODEL_ALIASES[raw]
    if raw in _MODEL_ANH_DA_NGHI:
        raise ValueError(
            f"'flow/{raw}' là model ảnh Flow ĐÃ BỎ ({_MODEL_ANH_DA_NGHI[raw]}). "
            f"Model ảnh còn dùng được: "
            f"{', '.join(sorted(m['id'] for m in FLOW_MODELS))}."
        )
    if f"flow/{raw}" in _VIDEO_GEN_MODELS:
        raise ValueError(
            f"'flow/{raw}' là model TẠO VIDEO, không tạo được ảnh. Model ảnh của "
            f"Flow: {', '.join(sorted(m['id'] for m in FLOW_MODELS))}."
        )
    # Tên lạ vẫn cho đi qua (viết hoa nguyên văn) để model ảnh mới của Flow
    # dùng được ngay mà không cần sửa mã.
    return raw.upper() if raw else "GEM_PIX_2"


def _resolve_aspect(size: str | None) -> str:
    """Nhãn tỉ lệ/«WxH» → hằng số IMAGE_ASPECT_RATIO_* của Flow."""
    if not size:
        return "IMAGE_ASPECT_RATIO_LANDSCAPE"
    s = str(size).strip().lower()
    if s in _ASPECT_FROM_LABEL:
        return _ASPECT_FROM_LABEL[s]
    if "x" in s:
        try:
            w, h = (int(x) for x in s.split("x"))
            mapped = _ASPECT_FROM_SIZE.get((w, h))
            if mapped:
                return mapped
            if w == h:
                return "IMAGE_ASPECT_RATIO_SQUARE"
            return "IMAGE_ASPECT_RATIO_LANDSCAPE" if w > h else "IMAGE_ASPECT_RATIO_PORTRAIT"
        except (TypeError, ValueError):
            pass
    return "IMAGE_ASPECT_RATIO_LANDSCAPE"


def _chuan_hoa_solver_url(url: str) -> str:
    """Chuẩn hoá địa chỉ REST của captcha-solver (bỏ hậu tố thừa)."""
    u = (url or "").strip().rstrip("/")
    for duoi in ("/api/captcha", "/captcha"):
        if u.endswith(duoi):
            u = u[: -len(duoi)]
    return u


# ── Account pool (trạng thái trong tiến trình) ─────────────────────────────

_pool_lock = threading.Lock()
_account_state: dict[str, dict[str, float | str]] = {}
# Thứ tự ưu tiên ưu ái (khác thứ tự khai trong cấu hình sau khi promote/demote).
_POOL_THU_TU: list[str] = []

_DEFAULT_COOLDOWN_S = 3600.0  # hạn mức Flow Pro thường reset mỗi giờ


def _cau_hinh_flow(credentials: dict[str, Any] | None) -> dict[str, Any]:
    if not credentials or not isinstance(credentials, dict):
        return {}
    ch = credentials.get("_cau_hinh") or {}
    return ch if isinstance(ch, dict) else {}


def _cooldown_seconds(credentials: dict[str, Any] | None) -> float:
    raw = _cau_hinh_flow(credentials).get("flow_cooldown_giay")
    try:
        val = float(raw)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return _DEFAULT_COOLDOWN_S


def _accounts(credentials: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = _cau_hinh_flow(credentials).get("flow_accounts") or []
    if isinstance(raw, str):
        import json as _json
        try:
            raw = _json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for a in raw:
        if isinstance(a, dict) and a.get("project_id"):
            out.append({
                "profile": str(a.get("profile") or "google-fx"),
                "project_id": str(a["project_id"]),
                "label": str(a.get("label") or a.get("name") or a.get("profile") or "google-fx"),
            })
    return out


def _account_key(account: dict[str, Any]) -> str:
    return f"{account.get('profile', '')}::{account.get('project_id', '')}"


def _thu_tu_hien_tai(credentials: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Danh sách tài khoản theo thứ tự ưu tiên hiện tại (đã promote/demote)."""
    ds = _accounts(credentials)
    if not ds:
        return []
    with _pool_lock:
        bang = {_account_key(a): a for a in ds}
        theo_uu_tien = [bang[k] for k in _POOL_THU_TU if k in bang]
        con_lai = [a for a in ds if _account_key(a) not in set(_POOL_THU_TU)]
        _POOL_THU_TU[:] = [_account_key(a) for a in theo_uu_tien + con_lai]
        return theo_uu_tien + con_lai


def _next_account(credentials: dict[str, Any] | None,
                  exclude: set[str] | None = None) -> dict[str, Any] | None:
    """Ưu tiên nghiêm ngặt: tài khoản #0 luôn được thử trước; chỉ sang #1 khi
    #0 đang cooldown hoặc đã bị loại trừ trong lượt này."""
    ds = _thu_tu_hien_tai(credentials)
    if not ds:
        return None
    exclude = exclude or set()
    now = time.time()
    with _pool_lock:
        for acc in ds:
            key = _account_key(acc)
            if key in exclude:
                continue
            state = _account_state.get(key, {})
            den = state.get("cooldown_until", 0)
            if den and now < float(den):
                continue
            return acc
        return None


#: Lỗi HẾT NGẠCH / bị giới hạn tốc độ — khớp theo TỪ. Từng khớp chuỗi con
#: "rate" in text, mà «rate» nằm sẵn trong «gene*rate*»: một lỗi kiểu "failed to
#: generate image" là đủ cho tài khoản nghỉ 1 giờ (sai số đo c2a).
_HET_NGACH_RE = re.compile(
    r"\b(quota|rate[ _-]?limit(?:ed)?|resource[ _]exhausted|usage[ _]limit|too many requests)\b",
    re.IGNORECASE)


def _la_het_ngach(status: int, text: str) -> bool:
    return status == 429 or bool(_HET_NGACH_RE.search(str(text or "")))


def _mark_quota_exhausted(account: dict[str, Any],
                          credentials: dict[str, Any] | None) -> None:
    cooldown_s = _cooldown_seconds(credentials)
    with _pool_lock:
        st = _account_state.setdefault(_account_key(account), {})
        st["cooldown_until"] = time.time() + cooldown_s
        st["ly_do"] = "hết ngạch / bị giới hạn tốc độ"
    logger.warning({"event": "flow_account_cooldown", "account": account.get("label"),
                    "cooldown_s": cooldown_s})


_NGHI_KHI_BI_GAN_CO_S = 6 * 3600


def _tam_nghi_ca_nhom(credentials: dict[str, Any] | None, ly_do: str) -> None:
    """Cho MỌI tài khoản Flow nghỉ — lỗi thuộc cả nhóm (IP / cờ Google)."""
    try:
        giay = float(_cau_hinh_flow(credentials).get("flow_pause_giay") or _NGHI_KHI_BI_GAN_CO_S)
    except (TypeError, ValueError):
        giay = _NGHI_KHI_BI_GAN_CO_S
    den = time.time() + giay
    with _pool_lock:
        for acc in _accounts(credentials):
            st = _account_state.setdefault(_account_key(acc), {})
            if den > float(st.get("cooldown_until") or 0):
                st["cooldown_until"] = den
                st["ly_do"] = "Google gắn cờ hoạt động bất thường cho cả nhóm"
    logger.warning({"event": "flow_ca_nhom_tam_nghi", "ly_do": ly_do, "giay": giay})


def _mo_ta_dang_nghi(credentials: dict[str, Any] | None) -> str:
    now = time.time()
    with _pool_lock:
        st = [_account_state.get(_account_key(a)) or {} for a in _accounts(credentials)]
    con = [(float(x.get("cooldown_until") or 0) - now, str(x.get("ly_do") or "lượt thất bại trước"))
           for x in st]
    con = [c for c in con if c[0] > 0]
    if not con:
        return ""
    giay, _ = min(con)
    ly_do = "; ".join(sorted({c[1] for c in con}))
    return f"còn ~{max(1, round(giay / 60))} phút nữa tài khoản đầu tiên mới được dùng lại ({ly_do})"


def _reorder_flow_account(account: dict[str, Any], to_front: bool) -> None:
    """Move tài khoản lên ĐẦU (khoẻ) / xuống CUỐI (chết) trong thứ tự ưu tiên
    TRONG TIẾN TRÌNH (không ghi cấu hình — khác c2a, web2api không cho module
    tự sửa cấu hình người dùng)."""
    key = _account_key(account)
    with _pool_lock:
        if key in _POOL_THU_TU:
            _POOL_THU_TU.remove(key)
        _POOL_THU_TU.insert(0, key) if to_front else _POOL_THU_TU.append(key)
    logger.info({"event": "flow_account_reorder", "account": account.get("label"),
                 "to": "front" if to_front else "back"})


# ── Adapter ──────────────────────────────────────────────────────────────

class FlowImageAdapter(BaseImageAdapter):
    """Adapter ảnh Google Labs Flow qua đường REST của captcha-solver."""

    no_auth = False
    supports_image_edit = True

    # Tài khoản đã thử trong từng request — keyed bằng id của credentials dict
    # (một dict mỗi request), để retry bỏ qua tài khoản chết thay vì thử lại nó.
    _tried_by_req: dict[int, set[str]] = {}

    def get_key_count(self, credentials: dict[str, Any] | None) -> int:
        """Số tài khoản để vòng thử xoay qua."""
        return max(1, len(_accounts(credentials)))

    def _current_account(self, key_try: int,
                         credentials: dict[str, Any] | None = None) -> dict[str, Any] | None:
        req_key = id(credentials) if credentials is not None else 0
        if key_try == 0:
            self._tried_by_req[req_key] = set()  # request mới: xoá loại trừ cũ
        excluded = self._tried_by_req.setdefault(req_key, set())
        acc = _next_account(credentials, exclude=excluded)
        if acc:
            excluded.add(_account_key(acc))
        if len(excluded) > 16:  # phòng rò: không bao giờ nhận key_try=0
            self._tried_by_req.pop(req_key, None)
        return acc

    def _solver_base(self, credentials: dict[str, Any] | None) -> str:
        base = _chuan_hoa_solver_url(str(_cau_hinh_flow(credentials).get("solver_url") or ""))
        if not base:
            raise RuntimeError(
                "flow provider missing solver_url: khai «solver_url» trong cấu hình "
                "module anh-ai (địa chỉ REST của captcha-solver).")
        return base

    def build_url(self, model: str, credentials: dict[str, Any] | None,
                  key_index: int = 0) -> str:
        base = self._solver_base(credentials)
        # Gài tài khoản đã chọn lên credentials để build_body khỏi xoay lại.
        # Xoá tài khoản của lần thử trước trước: khi mọi tài khoản còn lại vừa
        # vào cooldown, giữ lại giá trị cũ sẽ làm retry gửi lại ĐÚNG profile
        # vừa báo quota.
        if credentials is not None:
            credentials.pop("_flow_account", None)
        account = self._current_account(key_index, credentials=credentials)
        if credentials is not None and account is not None:
            credentials["_flow_account"] = account
        return f"{base}/v1/google/flow/rest/generate-image"

    def build_body(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        """Build request cho captcha-solver Flow.

        Nhận tham số OpenAI chuẩn (size, n, model) + ghi đè Flow qua extra_body:
        {"aspect_ratio": "16:9", "flow_model": "banana-pro", "count": 1}.
        Mặc định: 16:9 landscape, Nano Banana Pro, 1 ảnh.
        """
        prompt = str(body.get("prompt") or "")
        extra = body.get("extra_body") or {}
        if not isinstance(extra, dict):
            extra = {}

        aspect_in = extra.get("aspect_ratio") or extra.get("aspect") or body.get("size")
        aspect = _resolve_aspect(str(aspect_in) if aspect_in else None)

        model_in = extra.get("flow_model") or extra.get("model") or model
        flow_model = _resolve_model(str(model_in))

        count_in = extra.get("count") or extra.get("n") or body.get("n") or 1
        try:
            count = max(1, min(4, int(count_in)))
        except (TypeError, ValueError):
            count = 1

        out: dict[str, Any] = {
            "prompt": prompt,
            # REST nhận NHÃN ("16:9"), không nhận hằng số IMAGE_ASPECT_RATIO_*.
            # Gửi sai dạng là Google trả 400 kèm tên trường, không âm thầm
            # ra sai khung.
            "aspect_ratio": _ASPECT_LABEL_FROM_CONST.get(aspect, "16:9"),
            "model": flow_model,
            "count": count,
            "timeout": 280,      # Nano Banana Pro có lúc chậm
            "headless": False,   # đo 01/10/2026: headless bị Google trả
                                 # PUBLIC_ERROR_UNUSUAL_ACTIVITY
        }
        # Img2img: REST nhận MẢNG base64 (`images_b64`).
        raw, _ = first_image_bytes_mime(body.get("images") or [])
        if raw:
            out["images_b64"] = [_b64.b64encode(bytes(raw)).decode("ascii")]
        return out

    def build_headers(self, credentials: dict[str, Any] | None,
                      request_body: dict[str, Any], model: str,
                      body: dict[str, Any]) -> dict[str, str]:
        account = (credentials or {}).get("_flow_account")
        if account is None and credentials is None:
            account = _next_account(credentials)
        elif account is None and _next_account(credentials) is not None:
            da_thu = len(self._tried_by_req.get(id(credentials), ()))
            raise RuntimeError(
                f"đã thử hết {da_thu} tài khoản Google Flow còn dùng được trong lượt này "
                "mà đều lỗi — xem lỗi của từng lần thử ở trên.")
        if account:
            request_body["project_id"] = account["project_id"]
            request_body["profile"] = account["profile"]
            logger.info({"event": "flow_account_chosen",
                         "label": account.get("label"),
                         "profile": account["profile"]})
        else:
            so_tk = len(_accounts(credentials))
            if so_tk:
                raise RuntimeError(
                    f"cả {so_tk} tài khoản Google Flow đang nghỉ — "
                    f"{_mo_ta_dang_nghi(credentials) or 'vừa hết giờ nghỉ, thử lại'}. "
                    "Chờ hết giờ nghỉ hoặc thêm tài khoản dự phòng.")
            raise RuntimeError(
                "no Google Flow accounts configured. "
                "Khai «flow_accounts» trong cấu hình module anh-ai.")
        headers = {"Content-Type": "application/json"}
        api_key = str((credentials or {}).get("apiKey") or (credentials or {}).get("api_key") or "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def parse_response(self, response: Any) -> dict[str, Any] | None:
        """Bắt quota 502 trước khi vòng chung coi nó là lỗi chết (ở đây 502 hết
        ngạch chỉ có nghĩa là xoay sang tài khoản kế)."""
        if not hasattr(response, "status_code"):
            return None
        if response.status_code >= 400:
            text = ""
            try:
                text = response.text[:600]
            except Exception:
                text = ""
            if _la_het_ngach(response.status_code, text.lower()):
                req = getattr(response, "request", None)
                account = getattr(req, "_flow_account", None) if req is not None else None
                if account and not isinstance(account, dict):
                    account = None
                raise RuntimeError(f"flow quota/rate: HTTP {response.status_code}: {text[:200]}")
            return None
        # Đường REST luôn trả JSON: {"media_ids": [...], "urls": [...], ...}.
        try:
            payload = response.json()
        except Exception:
            return None
        urls = [str(u) for u in (payload.get("urls") or []) if u]
        if not urls:
            return {"data": []}
        meta_chung = {
            "model": payload.get("model"),
            "media_ids": payload.get("media_ids"),
            "elapsed_ms": payload.get("elapsed_ms"),
        }
        data: list[dict[str, Any]] = []
        for url in urls:
            try:
                # Link CDN đã ký sẵn (Expires + Signature), tải được bằng HTTP
                # thường. Vẫn qua tai_tu_url (module gán, đã kiểm SSRF): URL là
                # dữ liệu từ captcha-solver/upstream, không được biến thành
                # fetch loopback/metadata service nếu upstream lỗi/bị giả.
                tai = self.tai_tu_url
                if tai is None:
                    raise RuntimeError("adapter flow chưa được module gán tai_tu_url")
                image_b64 = url_to_base64(url, timeout=60, tai_tu_url=tai)
                if not image_b64:
                    raise RuntimeError("Flow CDN trả dữ liệu ảnh rỗng")
                data.append({"b64_json": image_b64, "_flow_meta": meta_chung})
            except Exception as exc:
                logger.warning({"event": "flow_download_failed", "url": url[:120],
                                "error": str(exc)})
        return {"data": data}

    def normalize(self, parsed: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        data = parsed.get("data") or []
        # KHÔNG gỡ watermark (vùng điều khoản dịch vụ — xem hoi-nv192-gemini-watermark.md).
        return {"created": now_sec(), "data": data}

    def on_key_success(self, credentials: dict[str, Any] | None) -> None:
        account = (credentials or {}).get("_flow_account")
        if account:
            _reorder_flow_account(account, to_front=True)

    def on_key_failed(self, credentials: dict[str, Any] | None, status: int, text: str) -> None:
        account = (credentials or {}).get("_flow_account")
        if not account:
            return
        low = str(text or "").lower()
        # Solver trả 429 khi profile đang đăng nhập/tạo ảnh ở lượt khác — khoá
        # tạm của browser, không phải hết ngạch Google: lượt sau vẫn dùng được
        # tài khoản ngay khi browser rảnh.
        if status == 429 and "account busy" in low:
            return
        # Dispatcher chặn resp.status_code>=400 TRƯỚC khi gọi parse_response,
        # nên cooldown hết ngạch đặt ở đây thay vì parse_response.
        if _la_het_ngach(status, low):
            _mark_quota_exhausted(account, credentials)
        # Khoẻ lên đầu / chết xuống cuối — bỏ qua 400 (lỗi request, không phải
        # lỗi tài khoản) để một cuộc gọi sai không xáo trộn pool.
        if status == 400:
            return
        if "unusual_activity" in low:
            # Đo 23–28/09/2026: cờ "hoạt động bất thường" của Google chặn CẢ
            # NHÓM theo IP, mọi đường gọi. Nghỉ cả nhóm một quãng thay vì gõ
            # cửa mỗi giờ — gõ tiếp dễ giữ cờ lâu hơn.
            _tam_nghi_ca_nhom(credentials, "unusual_activity")
            return
        _reorder_flow_account(account, to_front=False)
        # c2a ở đây còn tự khôi phục phiên + báo Telegram (account_recovery) —
        # web2api KHÔNG port: ghi log, người vận hành xử lý thủ công.
        looks_logged_out = any(k in low for k in (
            "hydrat", "logged out", "logout", "manual-login", "session",
            "sign in", "signin", "đăng nhập", "401", "403"))
        is_quota = any(k in low for k in ("quota", "credit", "limit", "insufficient"))
        if looks_logged_out and not is_quota:
            logger.warning({"event": "flow_account_co_the_mat_phien",
                            "account": account.get("label"),
                            "go_y": "đăng nhập lại profile Google thủ công"})

    def test_connection(self, credentials: dict[str, Any] | None = None) -> bool:
        try:
            base = self._solver_base(credentials)
        except RuntimeError:
            return False
        try:
            r = requests.get(f"{base}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
