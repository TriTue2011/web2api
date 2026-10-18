"""NV136-F — X-quang prompt: nhật ký chỉ-thêm mỗi lượt chạy (module tro-ly).

Mỗi lượt ghi: danh sách mảnh vào (id, nguon, token ước tính, lý do vào),
mảnh bị loại (lý do: tran/trung/dieu_kien), tổng token theo tầng, công cụ có
schema, công cụ chỉ ở danh mục.

- Chỉ-thêm: file JSONL, mỗi dòng một bản ghi, không sửa/xoá dòng cũ.
- Không chứa bí mật: lọc mọi giá trị của trường có tên chứa mat_khau, token,
  bi_mat, khoa (theo TÊN TRƯỜNG cấu trúc, không theo nội dung).
- API quản trị đọc có phân quyền theo hồ sơ (IDOR): chỉ đọc được hồ sơ của mình.

Ghi chú: NV135 đang làm nhật ký lượt chạy SQLite + API HTTP; khi NV135 gộp,
hàm ghi/đọc ở đây được nối vào nhật ký đó (ghi trong CẦN QUYẾT của báo cáo).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TRUONG_NHAY_CAM = re.compile(r"mat_khau|token|bi_mat|khoa", re.IGNORECASE)
# Mẫu key=value nhúng trong chuỗi (vd "mat_khau_noi_bo=abc123") → che giá trị.
# Theo cấu trúc tên khoá, không theo nội dung.
_GIA_TRI_NHAY_CAM_RE = re.compile(
    r"((?:mat_khau|token|bi_mat|khoa)[_a-z0-9]*)\s*=\s*([^\s,}]+)",
    re.IGNORECASE)


def _loc_bi_mat(obj: Any) -> Any:
    """Đệ quy thay giá trị của trường nhạy cảm bằng '…che'.

    - Trường có tên nhạy cảm → che toàn bộ giá trị.
    - Chuỗi chứa mẫu <tên_nhạy_cảm>=<giá_trị> → che phần giá trị.
    """
    if isinstance(obj, dict):
        return {k: ("…che" if _TRUONG_NHAY_CAM.search(str(k)) else _loc_bi_mat(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_loc_bi_mat(v) for v in obj]
    if isinstance(obj, str):
        obj = _GIA_TRI_NHAY_CAM_RE.sub(r"\1=…che", obj)
        if len(obj) > 2000:
            return obj[:2000] + "…(cắt)"
    return obj


class NhatKyXQuang:
    """Nhật ký X-quang chỉ-thêm, lưu theo file JSONL."""

    def __init__(self, duong_dan: str | Path) -> None:
        self.duong_dan = Path(duong_dan)
        self.duong_dan.parent.mkdir(parents=True, exist_ok=True)

    def ghi(self, *, luot_id: str, ho_so_id: str, x_quang: dict,
            cong_cu_schema: list[str], cong_cu_danh_muc: list[str],
            **them: Any) -> None:
        ban_ghi = {
            "luot_id": luot_id,
            "ho_so_id": ho_so_id,
            "manh_vao": x_quang.get("manh_vao", []),
            "manh_loai": x_quang.get("manh_loai", []),
            "token_theo_tang": x_quang.get("token_theo_tang", {}),
            "tong_token": x_quang.get("tong_token", 0),
            "cong_cu_schema": list(cong_cu_schema),
            "cong_cu_danh_muc": list(cong_cu_danh_muc),
        }
        ban_ghi.update(them)
        ban_ghi = _loc_bi_mat(ban_ghi)
        with open(self.duong_dan, "a", encoding="utf-8") as f:
            f.write(json.dumps(ban_ghi, ensure_ascii=False) + "\n")

    def doc(self, *, ho_so_id: str, nguoi_hoi_ho_so_id: str,
            luot_id: str | None = None) -> list[dict]:
        """Đọc các dòng của một hồ sơ. IDOR: người hỏi khác hồ sơ → từ chối."""
        if nguoi_hoi_ho_so_id != ho_so_id:
            raise PermissionError("khong_duoc_doc_ho_so_khac")
        if not self.duong_dan.exists():
            return []
        ket_qua = []
        with open(self.duong_dan, encoding="utf-8") as f:
            for dong in f:
                dong = dong.strip()
                if not dong:
                    continue
                try:
                    d = json.loads(dong)
                except json.JSONDecodeError:
                    continue
                if d.get("ho_so_id") != ho_so_id:
                    continue
                if luot_id is not None and d.get("luot_id") != luot_id:
                    continue
                ket_qua.append(d)
        return ket_qua
