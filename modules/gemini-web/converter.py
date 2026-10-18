"""Chuyển đổi định dạng OpenAI ↔ giao diện web Gemini.

- Vào: thân /v1/chat/completions (messages) → một prompt văn bản duy nhất
  (hỗ trợ tools, tool_choice, response_format JSON schema, multi-turn tool calls).
- Ra: văn bản Gemini → JSON / SSE chuẩn OpenAI (có bóc tách tool_calls, structured output).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
import uuid
from typing import Any, Tuple

from .client import MO_HINH_HO_TRO, giai_quyet_model
from .schema_validator import kiem_tra_json_schema, trich_xuat_json

log = logging.getLogger("web2api.module.gemini-web")

TIEN_TO = "gemini-web"

_NHAN_VAI = {
    "system": "System",
    "user": "User",
    "assistant": "Model",
    "tool": "Tool",
}

TOOL_WRAP_HINT = (
    "\n\n### SYSTEM: TOOL CALLING PROTOCOL (MANDATORY) ###\n"
    "If tool execution is required, you MUST adhere to this EXACT protocol. No exceptions.\n\n"
    "1. OUTPUT RESTRICTION: Your response MUST contain ONLY the [ToolCalls] block. Conversational filler, preambles, or concluding remarks are STRICTLY PROHIBITED.\n"
    "2. WRAPPING LOGIC: Every parameter value MUST be enclosed in a markdown code block using 3 backticks (```).\n"
    "3. TAG SYMMETRY: All tags MUST be balanced and closed in the exact reverse order of opening. Incomplete or unclosed blocks are strictly prohibited.\n\n"
    "REQUIRED SYNTAX:\n"
    "[ToolCalls]\n"
    "[Call:tool_name]\n"
    "[CallParameter:parameter_name]\n"
    "```\n"
    "value\n"
    "```\n"
    "[/CallParameter]\n"
    "[/Call]\n"
    "[/ToolCalls]\n\n"
    "CRITICAL: Do NOT mix natural language with protocol tags. Either respond naturally OR provide the protocol block alone."
)


def tach_tien_to(model: str | None) -> str:
    """'gemini-web/3.5-flash' → '3.5-flash' (bỏ tiền tố module nếu có)."""
    m = str(model or "").strip()
    if m.startswith(TIEN_TO + "/"):
        m = m[len(TIEN_TO) + 1 :]
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


def _van_ban_cua_noi_dung(noi_dung: Any) -> str:
    if isinstance(noi_dung, str):
        return noi_dung
    if isinstance(noi_dung, list):
        phan = []
        for p in noi_dung:
            if isinstance(p, dict) and p.get("type") == "text":
                phan.append(str(p.get("text") or ""))
        return "".join(phan)
    return ""


_THE_GIAO_THUC_RE = re.compile(
    r"\[(/?(?:ToolCalls|Call|CallParameter|ToolResults|Result|ToolResult)(?::[^\r\n\]]*)?)\]",
    re.IGNORECASE,
)
_THE_MO_DO_DANG_RE = re.compile(
    r"\[(?=/?(?:ToolCalls|Call|CallParameter|ToolResults|Result|ToolResult)(?:[:\]]))",
    re.IGNORECASE,
)


def vo_hieu_hoa_the_giao_thuc(text: str) -> str:
    """Vô hiệu hoá các thẻ giao thức trong nội dung không tin cậy để chống tiêm gián tiếp."""
    if not text or not isinstance(text, str):
        return ""
    res = _THE_GIAO_THUC_RE.sub(r"［\1］", text)
    res = _THE_MO_DO_DANG_RE.sub("［", res)
    return res



def _tao_tool_prompt(tools: list[dict], tool_choice: Any = None) -> str:
    if not tools:
        return ""
    lines = [
        "SYSTEM INTERFACE: You have access to the following technical tools. You MUST invoke them when necessary to fulfill the request, strictly adhering to the provided JSON schemas."
    ]
    for t in tools:
        func = t.get("function") or {}
        tname = func.get("name")
        tdesc = func.get("description", "No description provided.")
        params = func.get("parameters") or {}
        lines.append(f"Tool `{tname}`: {tdesc}")
        lines.append(f"Arguments JSON schema: {json.dumps(params, ensure_ascii=False)}")

    lines.append(TOOL_WRAP_HINT)
    if tool_choice == "required":
        lines.append("CRITICAL REQUIREMENT: You MUST invoke at least one tool to answer this request.")
    elif isinstance(tool_choice, dict):
        fn_req = (tool_choice.get("function") or {}).get("name")
        if fn_req:
            lines.append(f"CRITICAL REQUIREMENT: You MUST invoke the specific tool `{fn_req}`.")
    return "\n".join(lines)


def chuyen_thanh_prompt(body: dict) -> str:
    """messages OpenAI → prompt văn bản cho web Gemini, giữ vai trò và thứ tự."""
    body = body or {}
    thong_diep = body.get("messages") or []
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    response_format = body.get("response_format")

    dong = []

    # 1. Chèn chỉ dẫn công cụ (Tool protocol) nếu có
    if tools and tool_choice != "none":
        tool_sys = _tao_tool_prompt(tools, tool_choice)
        if tool_sys:
            dong.append(f"System: {tool_sys}")

    # 2. Chèn chỉ dẫn định dạng có cấu trúc (Structured Outputs) nếu có
    if isinstance(response_format, dict):
        rf_type = response_format.get("type")
        if rf_type == "json_object":
            dong.append(
                "System: CRITICAL: You MUST return ONLY a valid JSON object. "
                "Do not include any explanations, greetings, or text outside the JSON."
            )
        elif rf_type == "json_schema":
            schema_info = response_format.get("json_schema") or {}
            schema_body = schema_info.get("schema") or {}
            dong.append(
                "System: CRITICAL: You MUST return ONLY a valid JSON object that strictly conforms "
                f"to the following JSON schema:\n{json.dumps(schema_body, ensure_ascii=False)}\n"
                "Do not include any markdown fences, explanations, greetings, or text outside the JSON."
            )

    # 3. Chuyển đổi từng tin nhắn
    for m in thong_diep:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")

        # Xử lý vai trò tool (kết quả thực thi công cụ từ client)
        if role == "tool":
            tname = vo_hieu_hoa_the_giao_thuc(str(m.get("name") or "tool"))
            val = vo_hieu_hoa_the_giao_thuc(_van_ban_cua_noi_dung(m.get("content")))
            dong.append(
                f"[ToolResults]\n[Result:{tname}]\n[ToolResult]\n{val}\n[/ToolResult]\n[/Result]\n[/ToolResults]"
            )
            continue

        # Xử lý vai trò assistant có tool_calls trước đó
        tool_calls = m.get("tool_calls")
        if role == "assistant" and tool_calls and isinstance(tool_calls, list):
            calls_text = []
            for tc in tool_calls:
                func = tc.get("function") or {}
                fn_name = func.get("name") or ""
                args = func.get("arguments") or "{}"
                params_text = ""
                try:
                    args_obj = json.loads(args) if isinstance(args, str) else args
                    if isinstance(args_obj, dict):
                        for pk, pv in args_obj.items():
                            pv_str = json.dumps(pv, ensure_ascii=False) if not isinstance(pv, str) else str(pv)
                            params_text += f"[CallParameter:{pk}]\n```\n{pv_str}\n```\n[/CallParameter]\n"
                    else:
                        params_text = f"```\n{args}\n```\n"
                except Exception:
                    params_text = f"```\n{args}\n```\n"
                calls_text.append(f"[Call:{fn_name}]\n{params_text}[/Call]")

            tc_block = "[ToolCalls]\n" + "\n".join(calls_text) + "\n[/ToolCalls]"
            val = vo_hieu_hoa_the_giao_thuc(_van_ban_cua_noi_dung(m.get("content")))
            if val.strip():
                dong.append(f"Model: {val}\n{tc_block}")
            else:
                dong.append(f"Model: {tc_block}")
            continue

        # Tin nhắn thông thường
        vai = _NHAN_VAI.get(role, "User")
        van_ban = vo_hieu_hoa_the_giao_thuc(_van_ban_cua_noi_dung(m.get("content")))
        if van_ban.strip():
            dong.append(f"{vai}: {van_ban}")

    prompt = "\n".join(dong).strip()
    return prompt or ""


def trich_xuat_tool_calls(
    text: str, tools: list[dict] | None
) -> Tuple[str, list[dict[str, Any]]]:
    """Phân tích đầu ra của model để trích xuất tool_calls an toàn.

    ĐẦU RA LÀ DỮ LIỆU KHÔNG TIN CẬY:
    - (a) Chỉ chấp nhận tên công cụ NẰM TRONG danh sách tools khai báo (tên lạ → bỏ).
    - (b) Tham số kiểm theo JSON Schema của công cụ; sai → bỏ lời gọi đó.
    - (c) Chịu lỗi định dạng hợp lý (thiếu thẻ đóng → bỏ lời gọi chứ không đoán).
    - (d) Giới hạn số lời gọi ≤ 16 và độ dài tham số ≤ 65536 ký tự.
    - (e) id = chuỗi ngẫu nhiên mới mỗi lời gọi (`call_` + 24 ký tự hex).
    """
    if not tools or not text:
        return text, []

    # Danh mục công cụ hợp lệ
    ten_hop_le = {}
    for t in tools:
        if isinstance(t, dict):
            f = t.get("function")
            if isinstance(f, dict) and f.get("name"):
                ten_hop_le[str(f["name"])] = f.get("parameters") or {}

    # Kiểm tra khối [ToolCalls]...[/ToolCalls]
    # Phải có đầy đủ cả thẻ mở và đóng; thiếu thẻ đóng → bỏ
    m_block = re.search(r"\[ToolCalls\]([\s\S]*?)\[/ToolCalls\]", text, re.IGNORECASE)
    if not m_block:
        return text, []

    block_content = m_block.group(1)
    cleaned_text = re.sub(
        r"\[ToolCalls\][\s\S]*?\[/ToolCalls\]", "", text, flags=re.IGNORECASE
    ).strip()

    valid_calls = []

    # Bóc từng [Call:name]body[/Call]
    # Nếu trong block có [Call: mà không có [/Call] tương ứng → định dạng hỏng
    call_matches = list(re.finditer(r"\[Call:([^\]]+)\]([\s\S]*?)\[/Call\]", block_content, re.IGNORECASE))
    so_mo_call = len(re.findall(r"\[Call:", block_content, re.IGNORECASE))
    so_dong_call = len(re.findall(r"\[/Call\]", block_content, re.IGNORECASE))
    if so_mo_call != so_dong_call:
        log.warning("gemini-web: phát hiện thẻ [Call:] không cân xứng (%d != %d), bỏ lời gọi dở dang",
                    so_mo_call, so_dong_call)

    for m in call_matches:
        if len(valid_calls) >= 16:
            break  # Giới hạn tối đa 16 lời gọi mỗi lượt

        name = m.group(1).strip()
        body = m.group(2)

        # (a) Tên công cụ bắt buộc phải nằm trong tools khai báo
        if name not in ten_hop_le:
            log.warning("gemini-web: bỏ qua lời gọi công cụ lạ không khai báo: %r", name)
            continue

        # Kiểm tra tính cân xứng của CallParameter trong body
        so_mo_param = len(re.findall(r"\[CallParameter:", body, re.IGNORECASE))
        so_dong_param = len(re.findall(r"\[/CallParameter\]", body, re.IGNORECASE))
        if so_mo_param != so_dong_param:
            log.warning("gemini-web: bỏ qua lời gọi %s do thẻ parameter không đóng đầy đủ", name)
            continue

        args_dict = {}
        loi_param = False
        param_matches = list(re.finditer(r"\[CallParameter:([^\]]+)\]([\s\S]*?)\[/CallParameter\]", body, re.IGNORECASE))

        if param_matches:
            for pm in param_matches:
                pname = pm.group(1).strip()
                pval = pm.group(2).strip()
                # Cắt bỏ hàng rào markdown code block
                pval = re.sub(r"^`{3,}.*?\n?", "", pval)
                pval = re.sub(r"\n?`{3,}$", "", pval).strip()

                if len(pval) > 65536:
                    loi_param = True
                    break

                try:
                    args_dict[pname] = json.loads(pval)
                except Exception:
                    args_dict[pname] = pval
        else:
            # Fallback nếu model sinh trực tiếp JSON trong body
            clean_body = body.strip()
            clean_body = re.sub(r"^`{3,}.*?\n?", "", clean_body)
            clean_body = re.sub(r"\n?`{3,}$", "", clean_body).strip()
            if clean_body.startswith("{") and clean_body.endswith("}"):
                if len(clean_body) > 65536:
                    loi_param = True
                else:
                    try:
                        parsed = json.loads(clean_body)
                        if isinstance(parsed, dict):
                            args_dict = parsed
                    except Exception:
                        loi_param = True

        if loi_param:
            continue

        # (b) Kiểm tra tham số theo JSON Schema của công cụ
        schema = ten_hop_le[name]
        hop_le, ly_do = kiem_tra_json_schema(args_dict, schema)
        if not hop_le:
            log.warning("gemini-web: bỏ qua lời gọi %s do không khớp schema: %s", name, ly_do)
            continue

        # (e) Sinh id ngẫu nhiên dạng call_ + 24 ký tự hex
        call_id = f"call_{secrets.token_hex(12)}"
        valid_calls.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args_dict, ensure_ascii=False),
            },
        })

    return cleaned_text, valid_calls


class LocToolCallStream:
    """Bộ lọc chặn lộ protocol [ToolCalls] ra stream SSE khi request có tools.

    Gom các chunk; từ marker [ToolCalls] trở đi thì nuốt hết để caller trích xuất
    tool_calls đầy đủ khi kết thúc luồng. Không phát nửa lời gọi!
    """

    _MARKER = re.compile(r"\[(?:ToolCalls\]|Call:)", re.IGNORECASE)
    _DUOI_DO_DANG = re.compile(r"\[[A-Za-z]{0,9}$")

    def __init__(self) -> None:
        self._giu = ""
        self._chan = False
        self._tat_ca = []
        self._da_phat = []

    def feed(self, chunk: str) -> str:
        self._tat_ca.append(chunk)
        if self._chan:
            return ""
        buf = self._giu + chunk
        m = self._MARKER.search(buf)
        if m:
            self._chan = True
            self._giu = ""
            out = buf[: m.start()]
            if out:
                self._da_phat.append(out)
            return out
        t = self._DUOI_DO_DANG.search(buf)
        if t:
            self._giu = buf[t.start() :]
            out = buf[: t.start()]
            if out:
                self._da_phat.append(out)
            return out
        self._giu = ""
        if buf:
            self._da_phat.append(buf)
        return buf

    def flush(self) -> str:
        """Trả về đuôi đang giữ nếu không phải marker."""
        if self._chan:
            return ""
        out = self._giu
        self._giu = ""
        if out:
            self._da_phat.append(out)
        return out

    def van_ban_day_du(self) -> str:
        return "".join(self._tat_ca)

    def da_phat(self) -> str:
        return "".join(self._da_phat)


# ---------------------------------------------------------------------------
# Đáp án OpenAI.
# ---------------------------------------------------------------------------

def _hoan_thanh_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def phan_hoi_json(
    model_id: str, van_ban: str, tool_calls: list[dict] | None = None
) -> bytes:
    msg: dict[str, Any] = {"role": "assistant"}
    if tool_calls:
        msg["content"] = van_ban or None
        msg["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    else:
        msg["content"] = van_ban
        finish_reason = "stop"

    than = {
        "id": _hoan_thanh_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    return json.dumps(than, ensure_ascii=False).encode("utf-8")


def _doi_tuong_chunk(
    model_id: str,
    hoan_thanh_id: str,
    tao_luc: int,
    delta: dict,
    ket_thuc: str | None = None,
) -> dict:
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
    return dong_sse(
        json.dumps(
            _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {"role": "assistant"}),
            ensure_ascii=False,
        )
    )


def chunk_delta(model_id: str, hoan_thanh_id: str, tao_luc: int, text: str) -> bytes:
    return dong_sse(
        json.dumps(
            _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {"content": text}),
            ensure_ascii=False,
        )
    )


def chunk_delta_tool_calls(
    model_id: str, hoan_thanh_id: str, tao_luc: int, tool_calls: list[dict]
) -> bytes:
    delta_calls = []
    for idx, tc in enumerate(tool_calls):
        delta_calls.append({
            "index": idx,
            "id": tc.get("id"),
            "type": "function",
            "function": tc.get("function") or {},
        })
    return dong_sse(
        json.dumps(
            _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {"tool_calls": delta_calls}),
            ensure_ascii=False,
        )
    )


def chunk_ket_thuc(
    model_id: str, hoan_thanh_id: str, tao_luc: int, finish_reason: str = "stop"
) -> bytes:
    return dong_sse(
        json.dumps(
            _doi_tuong_chunk(model_id, hoan_thanh_id, tao_luc, {}, finish_reason),
            ensure_ascii=False,
        )
    )


def chunk_loi(thong_diep: str) -> bytes:
    return dong_sse(json.dumps({"error": {"message": thong_diep}}, ensure_ascii=False))


def dong_done() -> bytes:
    return b"data: [DONE]\n\n"


def tao_hoan_thanh_id() -> str:
    return _hoan_thanh_id()


__all__ = [
    "TIEN_TO",
    "MO_HINH_HO_TRO",
    "tach_tien_to",
    "giai_quyet_model_day_du",
    "id_day_du",
    "chuyen_thanh_prompt",
    "trich_xuat_tool_calls",
    "LocToolCallStream",
    "kiem_tra_json_schema",
    "trich_xuat_json",
    "phan_hoi_json",
    "dong_sse",
    "chunk_vai_tro",
    "chunk_delta",
    "chunk_delta_tool_calls",
    "chunk_ket_thuc",
    "chunk_loi",
    "dong_done",
    "tao_hoan_thanh_id",
]
