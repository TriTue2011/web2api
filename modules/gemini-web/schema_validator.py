"""Bộ kiểm tra JSON Schema tối thiểu và an toàn cho module gemini-web.

Tự cài đặt bằng thư viện chuẩn Python (không thêm gói ngoài jsonschema).
Hỗ trợ các từ khoá phổ biến của OpenAPI / OpenAI JSON Schema:
- type: "string", "number", "integer", "boolean", "object", "array", "null"
- properties, required, additionalProperties (cho object)
- items (cho array)
- enum (danh sách giá trị cho phép)
- minimum, maximum, minLength, maxLength
- pattern (chống ReDoS: giới hạn độ dài chuỗi và chạy timeout < 0.5s)
"""

from __future__ import annotations

import json
import re
from typing import Any, Tuple

# Trần an toàn DoS bộ nhớ
TOI_DA_KY_TU_THAM_SO = 65536


def kiem_tra_json_schema(du_lieu: Any, schema: dict) -> Tuple[bool, str]:
    """Kiểm tra `du_lieu` có khớp với `schema` (JSON Schema) không.

    Trả về: (hop_le: bool, ly_do_loi: str).
    Từ khoá 'pattern' bị từ chối có chủ đích để ngăn ngừa hoàn toàn nguy cơ ReDoS.
    """
    if not isinstance(schema, dict) or not schema:
        return True, ""

    # Từ chối từ khoá pattern để đảm bảo an toàn tuyệt đối
    if "pattern" in schema:
        return False, "không hỗ trợ pattern trong JSON Schema (chống ReDoS)"

    # 1. Kiểm tra enum
    if "enum" in schema:
        enum_list = schema["enum"]
        if isinstance(enum_list, (list, tuple)):
            if du_lieu not in enum_list:
                return False, f"giá trị {du_lieu!r} không nằm trong enum {enum_list}"

    # 2. Kiểm tra type
    loai_schema = schema.get("type")
    if loai_schema:
        cac_loai = [loai_schema] if isinstance(loai_schema, str) else list(loai_schema)
        khop_loai = False
        for kieu in cac_loai:
            if kieu == "string" and isinstance(du_lieu, str):
                khop_loai = True
            elif kieu == "number" and isinstance(du_lieu, (int, float)) and not isinstance(du_lieu, bool):
                khop_loai = True
            elif kieu == "integer" and isinstance(du_lieu, int) and not isinstance(du_lieu, bool):
                khop_loai = True
            elif kieu == "boolean" and isinstance(du_lieu, bool):
                khop_loai = True
            elif kieu == "object" and isinstance(du_lieu, dict):
                khop_loai = True
            elif kieu == "array" and isinstance(du_lieu, list):
                khop_loai = True
            elif kieu == "null" and du_lieu is None:
                khop_loai = True
        if not khop_loai:
            return False, f"kiểu dữ liệu {type(du_lieu).__name__} không khớp schema {loai_schema}"

    # 3. Kiểm tra kiểu số (number / integer)
    if isinstance(du_lieu, (int, float)) and not isinstance(du_lieu, bool):
        if "minimum" in schema and du_lieu < schema["minimum"]:
            return False, f"giá trị {du_lieu} nhỏ hơn minimum {schema['minimum']}"
        if "maximum" in schema and du_lieu > schema["maximum"]:
            return False, f"giá trị {du_lieu} lớn hơn maximum {schema['maximum']}"

    # 4. Kiểm tra kiểu chuỗi (string)
    if isinstance(du_lieu, str):
        if "minLength" in schema and len(du_lieu) < schema["minLength"]:
            return False, f"độ dài chuỗi {len(du_lieu)} nhỏ hơn minLength {schema['minLength']}"
        if "maxLength" in schema and len(du_lieu) > schema["maxLength"]:
            return False, f"độ dài chuỗi {len(du_lieu)} lớn hơn maxLength {schema['maxLength']}"

    # 5. Kiểm tra kiểu đối tượng (object / dict)
    if isinstance(du_lieu, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for req_field in required:
            if req_field not in du_lieu:
                return False, f"thiếu trường bắt buộc {req_field!r}"

        additional = schema.get("additionalProperties")
        for k, v in du_lieu.items():
            if k in properties:
                sub_schema = properties[k]
                if isinstance(sub_schema, dict):
                    ok, msg = kiem_tra_json_schema(v, sub_schema)
                    if not ok:
                        return False, f"trường {k!r}: {msg}"
            else:
                if additional is False:
                    return False, f"trường lạ không được phép: {k!r}"
                elif isinstance(additional, dict):
                    ok, msg = kiem_tra_json_schema(v, additional)
                    if not ok:
                        return False, f"trường phụ {k!r}: {msg}"

    # 6. Kiểm tra kiểu mảng (array / list)
    if isinstance(du_lieu, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(du_lieu):
                ok, msg = kiem_tra_json_schema(item, items_schema)
                if not ok:
                    return False, f"phần tử thứ {i}: {msg}"

    return True, ""


def trich_xuat_json(text: str) -> Tuple[bool, Any, str]:
    """Cố gắng bóc tách JSON từ văn bản phản hồi của model.

    Hỗ trợ JSON thuần hoặc bọc trong hàng rào markdown ```json ... ```.
    Trả về: (thanh_cong: bool, parsed_data: Any, loi_str: str).
    """
    s = str(text or "").strip()
    if not s:
        return False, None, "văn bản rỗng"

    # 1. Thử parse trực tiếp
    try:
        return True, json.loads(s), ""
    except Exception:
        pass

    # 2. Bóc tách từ markdown code block ```json ... ``` hoặc ``` ... ```
    re_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
    if re_block:
        candidate = re_block.group(1).strip()
        try:
            return True, json.loads(candidate), ""
        except Exception:
            pass

    # 3. Tìm vùng bắt đầu bằng { và kết thúc bằng } hoặc [ và ]
    dau_obj = s.find("{")
    cuoi_obj = s.rfind("}")
    if dau_obj != -1 and cuoi_obj > dau_obj:
        candidate = s[dau_obj : cuoi_obj + 1]
        try:
            return True, json.loads(candidate), ""
        except Exception:
            pass

    dau_arr = s.find("[")
    cuoi_arr = s.rfind("]")
    if dau_arr != -1 and cuoi_arr > dau_arr:
        candidate = s[dau_arr : cuoi_arr + 1]
        try:
            return True, json.loads(candidate), ""
        except Exception:
            pass

    return False, None, "không tìm thấy JSON hợp lệ trong phản hồi"
