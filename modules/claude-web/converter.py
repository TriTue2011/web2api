"""Chuyển đổi định dạng OpenAI ↔ API web claude.ai.

Mọi đặc thù của claude.ai (tên model nội bộ, gom messages thành một prompt, đọc
sự kiện SSE) nằm ở đây; module.py chỉ lo tài khoản và phân loại lỗi.
"""

from __future__ import annotations

import json
import time
import uuid

#: Bí danh thân thiện → id model nội bộ của claude.ai (học từ web app; lạ thì cho
#: qua nguyên văn; "auto"/rỗng → bỏ trường model để claude.ai tự chọn).
MO_HINH_BI_DANH = {
    "sonnet": "claude-sonnet-5",
    "sonnet-5": "claude-sonnet-5",
    "sonnet-4.6": "claude-sonnet-4-6",
    "sonnet-4.5": "claude-sonnet-4-5",
    "opus": "claude-opus-4-8",
    "opus-4.8": "claude-opus-4-8",
    "opus-4.7": "claude-opus-4-7",
    "opus-4.6": "claude-opus-4-6",
    "opus-3": "claude-3-opus-20240229",
    "opus-4.1": "claude-opus-4-1",
    "haiku": "claude-haiku-4-5",
    "haiku-4.5": "claude-haiku-4-5",
    "fable-5": "claude-fable-5",
}

#: UUID cha gốc của claude.ai cho lượt đầu của hội thoại mới.
UUID_CHA_GOC = "00000000-0000-4000-8000-000000000000"


class LoiYeuCau(Exception):
    """Yêu cầu OpenAI sai hình dạng (→ 400), hoặc có ảnh (→ chưa hỗ trợ ở giai đoạn này)."""

    def __init__(self, thong_bao: str, ma: int = 400):
        super().__init__(thong_bao)
        self.thong_bao = thong_bao
        self.ma = ma


def giai_model(model: str, tien_to: str = "clw") -> str:
    """«clw/sonnet» → «claude-sonnet-5»; bí danh lạ → nguyên văn; «auto»/rỗng → «»
    (nghĩa là bỏ trường model trong thân gửi claude.ai)."""
    m = str(model or "").strip()
    if m.startswith(tien_to + "/"):
        m = m[len(tien_to) + 1:].strip()
    m = m.lower()
    if not m or m == "auto":
        return ""
    return MO_HINH_BI_DANH.get(m, m)


def danh_sach_model(tien_to: str = "clw") -> list[dict]:
    """Danh sách model cho GET /v1/models: id dạng «<tien_to>/<tên>»."""
    ten = sorted(set(MO_HINH_BI_DANH) | {"auto"})
    return [{"id": f"{tien_to}/{t}", "object": "model", "owned_by": "claude-web"}
            for t in ten]


def _van_ban_cua(phan) -> str:
    if isinstance(phan, dict) and phan.get("type") == "text":
        t = phan.get("text")
        return t if isinstance(t, str) else ""
    return ""


def xay_dung_prompt(messages) -> str:
    """Mảng messages OpenAI → một chuỗi prompt cho claude.ai (server giữ lịch sử
    theo hội thoại, nên mỗi lượt gửi gộp toàn bộ cuộc trò chuyện)."""
    if not isinstance(messages, list) or not messages:
        raise LoiYeuCau("Trường «messages» phải là mảng khác rỗng.")
    phan: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            raise LoiYeuCau("Mỗi phần tử của «messages» phải là đối tượng.")
        role = str(msg.get("role") or "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    raise LoiYeuCau("claude-web giai đoạn này chưa hỗ trợ ảnh.", ma=400)
            van_ban = " ".join(_van_ban_cua(p) for p in content).strip()
        elif isinstance(content, str):
            van_ban = content
        elif content is None:
            van_ban = ""
        else:
            van_ban = str(content)
        if not van_ban.strip():
            continue
        nhan = {"system": "System", "assistant": "Assistant", "user": "User"}.get(
            role, role.capitalize() or "User")
        phan.append(f"{nhan}: {van_ban}")
    if not phan:
        raise LoiYeuCau("«messages» không có nội dung chữ nào.")
    phan.append("Assistant:")
    return "\n\n".join(phan)


def than_hoan_thanh(prompt: str, model_noi_bo: str, mui_gio: str = "Asia/Ho_Chi_Minh") -> dict:
    """Thân POST /…/chat_conversations/{id}/completion của claude.ai."""
    than = {
        "prompt": prompt,
        "parent_message_uuid": UUID_CHA_GOC,
        "timezone": mui_gio,
        "attachments": [],
        "files": [],
        "sync_sources": [],
        "rendering_mode": "messages",
    }
    if model_noi_bo:
        than["model"] = model_noi_bo
    return than


def trich_van_ban_sse(su_kien: dict) -> str:
    """Trích đoạn văn bản tăng dần từ một sự kiện SSE của claude.ai."""
    if not isinstance(su_kien, dict):
        return ""
    if isinstance(su_kien.get("completion"), str):
        return su_kien["completion"]
    delta = su_kien.get("delta")
    if isinstance(delta, dict):
        t = delta.get("text")
        return t if isinstance(t, str) else ""
    return ""


def la_su_kien_loi(su_kien: dict) -> bool:
    return (isinstance(su_kien, dict)
            and (str(su_kien.get("type") or "") == "error" or bool(su_kien.get("error"))))


def mo_hinh_that(su_kien: dict) -> str | None:
    """message_start mang tên model thật mà claude.ai đã dùng."""
    if isinstance(su_kien, dict) and su_kien.get("type") == "message_start":
        tin = su_kien.get("message")
        if isinstance(tin, dict) and tin.get("model"):
            return str(tin["model"])
    return None


def la_su_kien_dung(su_kien: dict) -> bool:
    return (isinstance(su_kien, dict)
            and (su_kien.get("type") in ("message_stop", "completion_stop")
                 or bool(su_kien.get("stop_reason"))))


def goi_loi(thong_bao: str, loai: str = "loi", ma_http: int | None = None) -> bytes:
    """Thân lỗi chuẩn OpenAI (bytes JSON). Không bao giờ chứa cookie/khoá."""
    than = {"error": {"message": thong_bao, "type": loai}}
    if ma_http is not None:
        than["error"]["code"] = ma_http
    return json.dumps(than, ensure_ascii=False).encode("utf-8")


def phan_hoi_khong_stream(model: str, van_ban: str, mo_hinh_that: str | None = None) -> bytes:
    return json.dumps({
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": mo_hinh_that or model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": van_ban},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }, ensure_ascii=False).encode("utf-8")


def chunk_sse(model: str, ma_hoi: str, luc: int, delta: dict,
              ket_thuc: str | None = None) -> bytes:
    """Một chunk SSE chuẩn OpenAI (bytes, đã có «data: » và \\n\\n)."""
    return ("data: " + json.dumps({
        "id": ma_hoi, "object": "chat.completion.chunk", "created": luc, "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": ket_thuc}],
    }, ensure_ascii=False) + "\n\n").encode("utf-8")
