"""Bộ chuyển đổi định dạng OpenAI sang Grok web và ngược lại.

- Yêu cầu chat OpenAI → chế độ Grok web + chuỗi lời nhắn văn bản.
- Luồng mảnh văn bản Grok web → các khúc SSE chuẩn OpenAI
  (role, delta content, finish_reason, [DONE]).
- Gom luồng → JSON chat.completion chuẩn OpenAI (không stream).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

#: Các chế độ của web app Grok, cũng là tên model sau tiền tố ``grok/``.
CAC_CHE_DO = ("fast", "auto", "expert", "heavy")
CHE_DO_MAC_DINH = "fast"


class LoiGiuaLuu(Exception):
    """Đánh dấu luồng văn bản kết thúc bất thường giữa chừng.

    ``luong_sse_openai`` bắt ngoại lệ này và kết thúc stream NGAY — không gửi
    khúc ``finish_reason: stop`` cũng không gửi ``[DONE]``, để phía khách biết
    đây không phải là kết thúc bình thường.
    """


class CheDoKhongCo(ValueError):
    """Chế độ người dùng yêu cầu nhưng không có → 400 (không fallback âm thầm)."""


def tach_che_do(
    model: str,
    biet_them: tuple[str, ...] | list[str] | set[str] = (),
) -> str:
    """Bỏ tiền tố ``grok/``; thuộc CAC_CHE_DO hoặc biet_them -> trả nguyên;
    rỗng hoặc auto -> fast; còn lại -> ném CheDoKhongCo(ValueError).
    """
    che_do = model.split("/", 1)[1] if "/" in model else model
    che_do = che_do.strip().lower()
    if not che_do or che_do == "auto":
        return CHE_DO_MAC_DINH
    if che_do in CAC_CHE_DO:
        return che_do
    if che_do in biet_them:
        return che_do
    raise CheDoKhongCo(f"chế độ {che_do!r} không được hỗ trợ")


def hop_van_ban(messages: list[dict]) -> str:
    """Gộp danh sách tin nhắn OpenAI thành một chuỗi văn bản cho Grok web.

    Chỉ lấy phần text (bỏ qua ảnh/audio — giai đoạn này chưa hỗ trợ media).
    """
    cac_dong = []
    for msg in messages:
        vai = msg.get("role", "user")
        nd = msg.get("content", "")
        if isinstance(nd, list):
            doan = [
                item.get("text", "")
                for item in nd
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            nd = " ".join(doan)
        nd = str(nd or "")
        if vai == "system":
            cac_dong.append(f"[Hướng dẫn hệ thống]: {nd}")
        elif vai == "assistant":
            cac_dong.append(f"[Trợ lý]: {nd}")
        else:
            cac_dong.append(nd)
    return "\n\n".join(cac_dong)


def _khuc_sse(doi_tuong: dict) -> bytes:
    return f"data: {json.dumps(doi_tuong, ensure_ascii=False)}\n\n".encode("utf-8")


def _khuc_dau(model_name: str, chat_id: str, created_ts: int) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    }


def _khuc_ket_thuc(model_name: str, chat_id: str, created_ts: int) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
    }


async def luong_sse_openai(
    luong_chu: AsyncIterator[str],
    model_name: str,
) -> AsyncIterator[bytes]:
    """Mảnh văn bản Grok web → các khúc SSE chuẩn OpenAI, kết thúc bằng [DONE]."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    yield _khuc_sse(_khuc_dau(model_name, chat_id, created_ts))
    try:
        async for manh in luong_chu:
            if not manh:
                continue
            yield _khuc_sse(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": manh},
                            "finish_reason": None,
                        }
                    ],
                }
            )
    except LoiGiuaLuu:
        return  # lỗi giữa stream: kết thúc bất thường, không stop/[DONE]
    yield _khuc_sse(_khuc_ket_thuc(model_name, chat_id, created_ts))
    yield b"data: [DONE]\n\n"


async def gom_json_openai(
    luong_chu: AsyncIterator[str],
    model_name: str,
) -> dict:
    """Gom luồng mảnh văn bản thành JSON chat.completion chuẩn OpenAI."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    cac_manh = []
    async for manh in luong_chu:
        if manh:
            cac_manh.append(manh)
    van_ban = "".join(cac_manh)
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": van_ban},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
