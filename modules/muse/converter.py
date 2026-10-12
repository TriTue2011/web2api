"""Bộ chuyển đổi định dạng OpenAI sang Muse và ngược lại.

Xử lý:
- Yêu cầu chat OpenAI -> Yêu cầu Muse /chat/stream
- Phản hồi stream Muse -> Luồng sự kiện SSE OpenAI (role, delta content, finish_reason, [DONE])
- Phản hồi không stream -> JSON chat.completion chuẩn OpenAI.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator


def tach_model_muse(model: str) -> str:
    """Bỏ tiền tố 'muse/' nếu có."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def trich_xuat_noi_dung_chat(messages: list[dict]) -> str:
    """Trích xuất chuỗi nội dung văn bản từ danh sách tin nhắn."""
    cac_dong = []
    for msg in messages:
        vai = msg.get("role", "user")
        nd = msg.get("content", "")
        if isinstance(nd, list):
            # Nội dung đa phương thức
            doan = []
            for item in nd:
                if isinstance(item, dict) and item.get("type") == "text":
                    doan.append(item.get("text", ""))
            nd = " ".join(doan)
        if vai == "system":
            cac_dong.append(f"[Hướng dẫn hệ thống]: {nd}")
        elif vai == "user":
            cac_dong.append(f"{nd}")
        elif vai == "assistant":
            cac_dong.append(f"[Trợ lý]: {nd}")
    return "\n\n".join(cac_dong)


def chuyen_yeu_cau_chat_openai(body: dict) -> dict:
    """Chuyển đổi thân JSON OpenAI sang thân yêu cầu Muse /chat/stream."""
    messages = body.get("messages", [])
    van_ban = trich_xuat_noi_dung_chat(messages)
    return {
        "items": [
            {"type": "text", "text": van_ban}
        ],
        "capabilities": ["chat", "streaming"],
    }


async def chuyen_dong_muse_sang_sse_openai(
    luong_chunks: AsyncIterator[bytes],
    model_name: str,
) -> AsyncIterator[bytes]:
    """Chuyển đổi luồng nhị phân từ BodyChunk thành các khúc SSE chuẩn OpenAI."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())

    # Khúc đầu tiên thông báo vai trò assistant
    role_chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(role_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    buffer_chuoi = ""
    try:
        async for raw_bytes in luong_chunks:
            buffer_chuoi += raw_bytes.decode("utf-8", errors="replace")
            # Mỗi dòng có thể là một JSON hoặc SSE event
            while "\n" in buffer_chuoi:
                line, buffer_chuoi = buffer_chuoi.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break

                try:
                    ev = json.loads(line)
                except Exception:
                    # Nếu là văn bản thuần không phải JSON
                    ev = {"eventName": "delta.text_append", "text": line}

                ev_type = ev.get("eventName") or ev.get("type") or ""
                text_delta = ""
                if ev_type == "delta.text_append":
                    text_delta = ev.get("text", "")
                elif ev_type == "delta.message_start":
                    text_delta = ""
                elif "text" in ev:
                    text_delta = ev.get("text", "")

                if text_delta:
                    content_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": text_delta},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n".encode("utf-8")

        # Khúc kết thúc
        finish_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(finish_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    except Exception as e:
        err_chunk = {
            "error": {
                "message": str(e),
                "type": "upstream_error",
                "code": "muse_stream_error",
            }
        }
        yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n".encode("utf-8")


async def gom_dong_muse_thanh_json_openai(
    luong_chunks: AsyncIterator[bytes],
    model_name: str,
) -> dict:
    """Gom toàn bộ dữ liệu từ luồng stream Muse thành phản hồi JSON không stream."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())
    full_content = []

    buffer_chuoi = ""
    async for raw_bytes in luong_chunks:
        buffer_chuoi += raw_bytes.decode("utf-8", errors="replace")
        while "\n" in buffer_chuoi:
            line, buffer_chuoi = buffer_chuoi.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                ev = json.loads(line)
            except Exception:
                ev = {"eventName": "delta.text_append", "text": line}

            ev_type = ev.get("eventName") or ev.get("type") or ""
            if ev_type == "delta.text_append":
                full_content.append(ev.get("text", ""))
            elif "text" in ev:
                full_content.append(ev.get("text", ""))

    text = "".join(full_content)
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created_ts,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ],
    }
