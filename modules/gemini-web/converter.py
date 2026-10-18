"""Chuyển đổi định dạng OpenAI ↔ giao diện web Gemini.

- Vào: thân /v1/chat/completions (messages) → một prompt văn bản duy nhất
  (web Gemini bản này chỉ nhận prompt chữ; ảnh/video CHƯA làm).
- Ra: văn bản Gemini → JSON / SSE chuẩn OpenAI.
"""

from __future__ import annotations

import json
import time
import uuid

from .client import giai_quyet_model, MO_HINH_HO_TRO

TIEN_TO = "gemini-web"

_NHAN_VAI = {
    "system": "System",
    "user": "User",
    "assistant": "Model",
    "tool": "Tool",
}


def tach_tien_to(model: str | None) -> str:
    """'gemini-web/3.5-flash' → '3.5-flash' (bỏ tiền tố module nếu có)."""
    m = str(model or "").strip()
    if m.startswith(TIEN_TO + "/"):
        m = m[len(TIEN_TO) + 1:]
    return m


def giai_quyet_model_day_du(
    model: str | None,
    biet_them: tuple[str, ...] | list[str] | set[str] = (),
) -> str:
    """Tên model đầy đủ 'gemini-web/x' → tên thân thiện đã kiểm ('3.5-flash'…).

    Ném ModelKhongCo khi tên lạ được chỉ đích danh (→ 400 ở module).
    """
    return giai_quyet_model(tach_tien_to(model), biet_them=biet_them)


def id_day_du(ten_than_thien: str) -> str:
    """Tên thân thiện → id đầy đủ 'gemini-web/<tên>'."""
    return f"{TIEN_TO}/{ten_than_thien}"


def _van_ban_cua_noi_dung(noi_dung) -> str:
    if isinstance(noi_dung, str):
        return noi_dung
    if isinstance(noi_dung, list):
        phan = []
        for p in noi_dung:
            if isinstance(p, dict) and p.get("type") == "text":
                phan.append(str(p.get("text") or ""))
            # Phần ảnh/tệp: giai đoạn này chưa làm — bỏ qua, không vỡ.
        return "".join(phan)
    return ""


def chuyen_thanh_prompt(body: dict) -> str:
    """messages OpenAI → prompt văn bản cho web Gemini, giữ vai trò và thứ tự."""
    body = body or {}
    thong_diep = body.get("messages") or []
    dong = []
    for m in thong_diep:
        if not isinstance(m, dict):
            continue
        vai = _NHAN_VAI.get(str(m.get("role") or "user"), "User")
        van_ban = _van_ban_cua_noi_dung(m.get("content"))
        if van_ban.strip():
            dong.append(f"{vai}: {van_ban}")
    prompt = "\n".join(dong).strip()
    return prompt or ""


# ---------------------------------------------------------------------------
# Đáp án OpenAI.
# ---------------------------------------------------------------------------

def _hoan_thanh_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def phan_hoi_json(model_id: str, van_ban: str) -> bytes:
    than = {
        "id": _hoan_thanh_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": van_ban},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return json.dumps(than, ensure_ascii=False).encode("utf-8")


def _doi_tuong_chunk(model_id: str, hoan_thanh_id: str, tao_luc: int,
                     delta: dict, ket_thuc: str | None = None) -> dict:
    return {
        "id": hoan_thanh_id,
        "object": "chat.completion.chunk",
        "created": tao_luc,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": ket_thuc}],
    }


def dong_sse(du_lieu: str) -> bytes:
    return f"data: {du_lieu}\n\n".encode("utf-8")


def chunk_vai_tro(model_id: str, hoan_thanh_id: str, tao_luc: int) -> bytes:
    return dong_sse(json.dumps(
        _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {"role": "assistant"}),
        ensure_ascii=False))


def chunk_delta(model_id: str, hoan_thanh_id: str, tao_luc: int, text: str) -> bytes:
    return dong_sse(json.dumps(
        _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {"content": text}),
        ensure_ascii=False))


def chunk_ket_thuc(model_id: str, hoan_thanh_id: str, tao_luc: int) -> bytes:
    return dong_sse(json.dumps(
        _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {}, "stop"),
        ensure_ascii=False))


def chunk_loi(thong_diep: str) -> bytes:
    return dong_sse(json.dumps({"error": {"message": thong_diep}}, ensure_ascii=False))


def dong_done() -> bytes:
    return b"data: [DONE]\n\n"


def tao_hoan_thanh_id() -> str:
    return _hoan_thanh_id()


__all__ = [
    "TIEN_TO", "MO_HINH_HO_TRO",
    "tach_tien_to", "giai_quyet_model_day_du", "id_day_du",
    "chuyen_thanh_prompt", "phan_hoi_json",
    "dong_sse", "chunk_vai_tro", "chunk_delta", "chunk_ket_thuc",
    "chunk_loi", "dong_done", "tao_hoan_thanh_id",
]
