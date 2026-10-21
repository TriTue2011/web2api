"""Máy khách cho ChatGPT bản web (chatgpt.com, giao thức backend-api nội bộ).

Luồng giao tiếp HỌC từ kho tham khảo ``chatgpt2api`` (chỉ đọc, không chép mã —
gói đó dùng ``curl_cffi`` và các token chống bot phức tạp; ở đây tự cài đặt
tối thiểu bằng ``httpx`` theo ĐÚNG luồng đã học được):

1. Xác thực bằng Bearer access token do NGƯỜI DÙNG dán (trường ``tai_khoan``) —
   không tự đăng nhập, không tự động hoá trình duyệt. Giai đoạn này cookie/phiên
   do người dùng dán hoặc lấy qua noVNC (module ``vnc-xa`` qua ``can_module``).
2. ``init()``: GET ``/backend-api/me`` kèm ``Authorization: Bearer <token>``;
   token hỏng → 401/403.
3. Trò chuyện: POST ``/backend-api/conversation`` với JSON ``{"action": "next",
   "messages": [...], "model": <slug>, "parent_message_id": <uuid>, ...}`` và
   tiêu đề ``Accept: text/event-stream``. Đáp án là SSE: các dòng
   ``data: {...}`` mang ``message.content.parts`` (văn bản của assistant, chỉ
   lấy khi ``author.role == "assistant"``); dòng ``data: [DONE]`` kết thúc
   luồng; sự kiện có ``"error"`` là lỗi. Mỗi sự kiện mới mang toàn bộ văn bản
   tới lúc đó nên client tự tính ``delta`` theo tiền tố.
   Web thật có lớp chống bot (sentinel); module KHÔNG vượt lớp đó — 403 ở bước
   chat được báo rõ là ``LoiChanBot``.
4. Stream: cùng endpoint; mỗi sự kiện mới mang toàn bộ văn bản tới lúc đó nên
   client tự tính ``delta`` so với sự kiện trước.

Giao thức web của OpenAI KHÔNG chính thức và có thể đổi bất cứ lúc nào —
module này CHƯA KIỂM với dịch vụ thật, chỉ kiểm với máy chủ giả trong test/.
Mọi lỗi đều được bọc thành ngoại lệ có cấu trúc (mã HTTP + thông điệp chung),
KHÔNG bao giờ mang nội dung đáp án thô hay token ra ngoài.

LÀM MỚI TOKEN: kho tham khảo có cơ chế làm mới token OAuth (refresh_token,
khoá theo từng refresh_token để hai luồng không đua nhau, cache kết quả 60s).
Module này CHƯA làm: token do người dùng dán tay; hết hạn thì dán token mới.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

import httpx

from app.lam_moi_model import TOI_DA_MODEL, chuan_hoa_ma_model, chuan_hoa_nhan

log = logging.getLogger("web2api.module.chatgpt-web")

#: Máy chủ web ChatGPT (hằng số — không phải do người dùng nhập).
MAY_CHU_MAC_DINH = "https://chatgpt.com"

_DUONG_ME = "/backend-api/me"
_DUONG_GOI = "/backend-api/accounts/check"
_DUONG_HOI_THOAI = "/backend-api/f/conversation"
_DUONG_MODELS = "/backend-api/models"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# Ngoại lệ có cấu trúc: chỉ mang mã + thông điệp chung, không mang token
# hay nội dung đáp án thô của nhà cung cấp.
# ---------------------------------------------------------------------------

class LoiChatGPTWeb(Exception):
    """Lỗi chung của máy khách; ``ma`` là HTTP status tương ứng để trả khách."""

    def __init__(self, ma: int, thong_diep: str):
        super().__init__(thong_diep)
        self.ma = ma


class LoiXacThuc(LoiChatGPTWeb):
    """Token bị từ chối (401/403) — tài khoản này hỏng, nên chuyển tài khoản."""

    def __init__(self, thong_diep: str = "token không được chatgpt.com chấp nhận"):
        super().__init__(401, thong_diep)


class LoiHanMuc(LoiChatGPTWeb):
    """Bị giới hạn lượt (429)."""

    def __init__(self, thong_diep: str = "chatgpt.com giới hạn lượt gọi (429)"):
        super().__init__(429, thong_diep)


class LoiMayChu(LoiChatGPTWeb):
    """Lỗi phía OpenAI (5xx) hoặc đáp án sai cấu trúc — không phải lỗi tài khoản."""

    def __init__(self, thong_diep: str = "chatgpt.com lỗi phía máy chủ"):
        super().__init__(502, thong_diep)


class LoiHetGio(LoiChatGPTWeb):
    """Hết giờ chờ nối/đọc."""

    def __init__(self, thong_diep: str = "chatgpt.com không phản hồi kịp"):
        super().__init__(504, thong_diep)


class LoiChanBot(LoiChatGPTWeb):
    """403 ở bước chat — token vừa qua /me nên KHÔNG phải lỗi token: chatgpt.com
    chặn lượt tự động. Web2api không vượt cơ chế này (chat ChatGPT: dùng module
    openaicodex hoặc khoá API). Phân loại theo mã HTTP + bước gọi."""

    def __init__(self, thong_diep: str = "chatgpt.com chặn lượt chat tự động (403) — "
               "không phải lỗi token; dùng module openaicodex hoặc khoá API để chat"):
        super().__init__(403, thong_diep)


class ModelKhongCo(ValueError):
    """Tên model người dùng chỉ đích danh nhưng module không biết → 400."""


# ---------------------------------------------------------------------------
# Registry model: tên thân thiện → slug mà backend-api hiểu.
# ---------------------------------------------------------------------------

BI_DANH_MODEL = {
    "4o": "gpt-4o",
    "4o-mini": "gpt-4o-mini",
    "4.1": "gpt-4.1",
    "4.1-mini": "gpt-4.1-mini",
    "o3": "o3",
    "o4-mini": "o4-mini",
    "auto": "gpt-4o",
}

MO_HINH_HO_TRO = ("4o", "4o-mini", "4.1", "4.1-mini", "o3", "o4-mini")

MODEL_MAC_DINH = "4o"

_TIEN_TO_STRIP = ("chatgpt-web/", "chatgpt/", "cgw/")


def ten_tu_slug(slug: str) -> str:
    """Slug từ backend-api → tên thân thiện; slug lạ dùng nguyên slug."""
    s = str(slug or "").strip()
    for than_thien in MO_HINH_HO_TRO:
        if BI_DANH_MODEL.get(than_thien) == s:
            return than_thien
    for k, v in BI_DANH_MODEL.items():
        if k != "auto" and v == s:
            return k
    return s


def giai_quyet_model(ten: str | None, biet_them: tuple[str, ...] | list[str] | set[str] = ()) -> str:
    """Tên model (đã/không kèm tiền tố) → tên thân thiện; lạ → ModelKhongCo.

    Chỉ ``auto`` (hoặc rỗng) được rơi về mặc định; người dùng CHỈ ĐÍCH DANH
    tên lạ phải nhận 400, không âm thầm chạy model khác.
    """
    raw = str(ten or "").strip()
    m = raw
    for pfx in _TIEN_TO_STRIP:
        if m.lower().startswith(pfx):
            m = m[len(pfx):]
            break
    if not m or m.lower() == "auto":
        return MODEL_MAC_DINH
    if m in biet_them:
        return m
    m_lower = m.lower()
    if m_lower in biet_them:
        return m_lower
    if m_lower in BI_DANH_MODEL:
        chuan = BI_DANH_MODEL[m_lower]
        for than_thien, ten_chuan in BI_DANH_MODEL.items():
            if ten_chuan == chuan and than_thien in MO_HINH_HO_TRO:
                return than_thien
        return chuan
    for than_thien in MO_HINH_HO_TRO:
        if BI_DANH_MODEL[than_thien] == m_lower:
            return than_thien
    raise ModelKhongCo(f"model {ten!r} không có trong danh sách hỗ trợ")


def slug_cua_model(ten_than_thien: str) -> str:
    """Tên thân thiện → slug gửi trong payload hội thoại."""
    return BI_DANH_MODEL.get(ten_than_thien, ten_than_thien)


# ---------------------------------------------------------------------------
# Dựng payload hội thoại + bóc SSE.
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def dung_tin_nhan_hoi_thoai(role: str, van_ban: str) -> dict:
    """Một tin nhắn OpenAI → tin nhắn dạng conversation của backend-api."""
    return {
        "id": _uuid(),
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [van_ban]},
        "metadata": {},
    }


def _van_ban_cua_noi_dung(noi_dung) -> str:
    if isinstance(noi_dung, str):
        return noi_dung
    if isinstance(noi_dung, list):
        phan = []
        for p in noi_dung:
            if isinstance(p, dict) and p.get("type") == "text":
                phan.append(str(p.get("text") or ""))
        return "".join(phan)
    return ""


def chuyen_thanh_tin_nhan(body: dict) -> list[dict]:
    """messages OpenAI → danh sách tin nhắn conversation (system gộp đầu)."""
    thong_diep = (body or {}).get("messages") or []
    he_thong = []
    thuong = []
    for m in thong_diep:
        if not isinstance(m, dict):
            continue
        vai = str(m.get("role") or "user")
        van_ban = _van_ban_cua_noi_dung(m.get("content"))
        if not van_ban.strip():
            continue
        if vai == "system":
            he_thong.append(van_ban)
        elif vai == "tool":
            thuong.append(("user", f"[Kết quả công cụ {m.get('name') or '?'}]:\n{van_ban}"))
        elif vai == "assistant":
            thuong.append(("assistant", van_ban))
        else:
            thuong.append(("user", van_ban))
    ra = []
    if he_thong:
        ra.append(dung_tin_nhan_hoi_thoai("system", "\n\n---\n\n".join(he_thong)))
    for vai, van_ban in thuong:
        ra.append(dung_tin_nhan_hoi_thoai(vai, van_ban))
    return ra


def dung_payload(tin_nhan: list[dict], slug: str) -> dict:
    """Payload POST /backend-api/f/conversation (bản rút gọn theo web app)."""
    return {
        "action": "next",
        "messages": tin_nhan,
        "model": slug,
        "parent_message_id": _uuid(),
        "conversation_mode": {"kind": "primary_assistant"},
        "force_paragen": False,
        "force_rate_limit": False,
        "force_use_sse": True,
        "history_and_training_disabled": True,
        "reset_rate_limits": False,
        "suggestions": [],
        "supported_encodings": ["v1"],
        "system_hints": [],
        "timezone": "Asia/Saigon",
        "timezone_offset_min": -420,
        "variant_purpose": "comparison_implicit",
        "websocket_request_id": _uuid(),
        "client_contextual_info": {
            "is_dark_mode": False,
            "time_since_loaded": 120,
            "page_height": 900,
            "page_width": 1400,
            "pixel_ratio": 2,
            "screen_height": 1440,
            "screen_width": 2560,
        },
    }


def trich_van_ban_su_kien(su_kien: dict) -> tuple[str, bool]:
    """Một sự kiện SSE đã phân tích → (văn bản assistant tới lúc này, đã_xong).

    Chỉ lấy ``parts`` của tin nhắn do assistant viết; bỏ qua echo của user.
    Không đúng cấu trúc → ("", False), không vỡ.
    """
    try:
        if not isinstance(su_kien, dict):
            return "", False
        tin = su_kien.get("message")
        if not isinstance(tin, dict):
            return "", False
        tac_gia = tin.get("author") or {}
        if tac_gia.get("role") != "assistant":
            return "", False
        noi_dung = tin.get("content") or {}
        if noi_dung.get("content_type") not in ("text", "multimodal_text"):
            return "", False
        parts = noi_dung.get("parts") or []
        van_ban = "".join(str(p) for p in parts if isinstance(p, str))
        xong = tin.get("status") == "finished_successfully" or bool(tin.get("end_turn"))
        return van_ban, xong
    except (TypeError, AttributeError):
        return "", False


# ---------------------------------------------------------------------------
# Máy khách.
# ---------------------------------------------------------------------------

class ChatGPTWebClient:
    """Phiên HTTP đã xác thực bằng Bearer token cho một tài khoản."""

    def __init__(self, token: str,
                 base_url: str = MAY_CHU_MAC_DINH,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 120.0):
        self._token = token
        self._da_init = False
        self._dong = False
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=httpx.Timeout(connect=15.0, read=timeout, write=15.0, pool=15.0),
            headers={
                "User-Agent": _UA,
                "Accept": "*/*",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                "Authorization": f"Bearer {token}",
                "Origin": MAY_CHU_MAC_DINH,
                "Referer": MAY_CHU_MAC_DINH + "/",
            },
        )

    def __repr__(self):  # không bao giờ in token ra
        return f"ChatGPTWebClient(da_init={self._da_init})"

    def _kiem_ma(self, ma: int, buoc: str) -> None:
        if ma == 403 and buoc == "chat":
            raise LoiChanBot()
        if ma in (401, 403):
            raise LoiXacThuc(f"token không được chấp nhận ở bước {buoc} (HTTP {ma})")
        if ma == 429:
            raise LoiHanMuc(f"giới hạn lượt gọi ở bước {buoc}")
        if ma >= 500:
            raise LoiMayChu(f"máy chủ lỗi ở bước {buoc} (HTTP {ma})")
        if ma >= 400:
            raise LoiMayChu(f"đáp án lạ ở bước {buoc} (HTTP {ma})")

    async def init(self) -> dict:
        """Kiểm token còn sống qua /backend-api/me; hỏng → LoiXacThuc."""
        if self._dong:
            raise LoiMayChu("client đã đóng")
        try:
            r = await self._client.get(_DUONG_ME)
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ kiểm token: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"không nối được chatgpt.com: {type(e).__name__}") from None
        self._kiem_ma(r.status_code, "init")
        self._da_init = True
        try:
            return r.json() if isinstance(r.json(), dict) else {}
        except ValueError:
            return {}

    async def thong_tin_goi(self) -> str | None:
        """Gói tài khoản qua /backend-api/accounts/check.

        Trả ``accounts.default.plan_type`` (free/plus/pro…) suy từ CẤU TRÚC
        JSON — không dò chữ. Không đọc được / mã lạ → None (module coi như
        "khong_ro", tài khoản vẫn dùng được). CHƯA KIỂM trên dịch vụ thật.
        """
        try:
            r = await self._client.get(_DUONG_GOI)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            du_lieu = r.json()
        except ValueError:
            return None
        if not isinstance(du_lieu, dict):
            return None
        tai_khoan = du_lieu.get("accounts")
        if not isinstance(tai_khoan, dict):
            return None
        mac_dinh = tai_khoan.get("default")
        if not isinstance(mac_dinh, dict):
            return None
        goi = mac_dinh.get("plan_type")
        goi = str(goi).strip().lower() if goi else ""
        return goi or None

    async def lay_model(self) -> list[dict]:
        """Lấy danh sách model khả dụng từ GET /backend-api/models.

        Trả [{"slug": str, "nhan": str | None}]; slug qua chuan_hoa_ma_model,
        nhãn qua chuan_hoa_nhan, bỏ trùng, cắt ở TOI_DA_MODEL.
        """
        if self._dong:
            raise LoiMayChu("client đã đóng")
        try:
            r = await self._client.get(_DUONG_MODELS, params={"history_and_training_disabled": "false"})
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ lấy danh sách model: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"không nối được chatgpt.com: {type(e).__name__}") from None
        self._kiem_ma(r.status_code, "lay_model")
        try:
            du_lieu = r.json()
        except (ValueError, TypeError):
            raise LoiMayChu("đáp án /backend-api/models không đúng cấu trúc") from None
        if not isinstance(du_lieu, dict) or not isinstance(du_lieu.get("models"), list):
            raise LoiMayChu("đáp án /backend-api/models không đúng cấu trúc")

        ket_qua: list[dict] = []
        da_thay: set[str] = set()
        for m in du_lieu["models"]:
            if not isinstance(m, dict):
                continue
            slug_raw = m.get("slug")
            slug = chuan_hoa_ma_model(slug_raw)
            if slug is None or slug in da_thay:
                continue
            da_thay.add(slug)
            nhan = chuan_hoa_nhan(m.get("title"))
            ket_qua.append({"slug": slug, "nhan": nhan})
            if len(ket_qua) >= TOI_DA_MODEL:
                break
        return ket_qua

    def _tieu_de_hoi_thoai(self) -> dict:
        return {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "X-OpenAI-Target-Path": _DUONG_HOI_THOAI,
            "X-OpenAI-Target-Route": _DUONG_HOI_THOAI,
        }

    async def generate_content_stream(self, tin_nhan: list[dict], model: str | None = None, slug_da_giai: str | None = None):
        """Async generator ra từng delta văn bản của assistant."""
        slug = slug_da_giai if slug_da_giai is not None else slug_cua_model(giai_quyet_model(model))
        payload = dung_payload(tin_nhan, slug)
        try:
            async with self._client.stream(
                "POST", _DUONG_HOI_THOAI,
                headers=self._tieu_de_hoi_thoai(), json=payload,
            ) as r:
                self._kiem_ma(r.status_code, "chat")
                truoc = ""
                da_thay_su_kien = False
                async for dong in r.aiter_lines():
                    dong = dong.strip()
                    if not dong.startswith("data:"):
                        continue
                    du_lieu = dong[len("data:"):].strip()
                    if du_lieu == "[DONE]":
                        break
                    try:
                        su_kien = json.loads(du_lieu)
                    except ValueError:
                        continue
                    van_ban, xong = trich_van_ban_su_kien(su_kien)
                    if not isinstance(su_kien, dict) or "message" not in su_kien:
                        continue
                    da_thay_su_kien = True
                    # Sự kiện mới thường mang toàn bộ văn bản tới lúc đó → chỉ
                    # nhả phần chênh (delta); sự kiện lặp lại y hệt thì bỏ qua.
                    if van_ban.startswith(truoc):
                        delta = van_ban[len(truoc):]
                    else:
                        delta = van_ban
                    truoc = van_ban
                    if delta:
                        yield delta
                    if xong:
                        break
                if not da_thay_su_kien:
                    # 200 nhưng không có sự kiện message nào: web đổi định dạng —
                    # báo 502 có cấu trúc thay vì trả văn bản rỗng gây hiểu lầm.
                    raise LoiMayChu("đáp án không đúng cấu trúc SSE hội thoại")
        except LoiChatGPTWeb:
            raise
        except httpx.TimeoutException as e:
            raise LoiHetGio(f"hết giờ chờ đáp án: {type(e).__name__}") from None
        except httpx.HTTPError as e:
            raise LoiMayChu(f"mất kết nối khi đang chat: {type(e).__name__}") from None

    async def generate_content(self, tin_nhan: list[dict], model: str | None = None, slug_da_giai: str | None = None) -> str:
        """Bản không stream: gom các delta thành văn bản đầy đủ."""
        phan = [d async for d in self.generate_content_stream(tin_nhan, model, slug_da_giai=slug_da_giai)]
        return "".join(phan)

    async def close(self) -> None:
        self._dong = True
        await self._client.aclose()
