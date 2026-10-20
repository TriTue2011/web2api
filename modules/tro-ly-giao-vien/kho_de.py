"""Kho đề kiểm tra của từng danh tính giáo viên (0600, ghi nguyên tử, trần số lượng).

An toàn:
- Đề nằm dưới <WEB2API_DATA>/du_lieu/tro-ly-giao-vien/<khoa>/de/<quiz_id>.json,
  trong đó <khoa> = SHA-256(danh_tinh_id)[:16] — danh tính A không bao giờ thấy đề của B.
- Tệp 0600, thư mục danh tính 0700; ghi nguyên tử (temp + os.replace) để tiến
  trình bị cắt giữa chừng không để lại tệp hỏng.
- quiz_id chỉ nhận [A-Za-z0-9_-]{1,64}; khoá danh tính chỉ nhận hex — chống
  thoát đường dẫn.
- Trần 100 đề/danh tính; tệp hỏng JSON: báo lỗi, KHÔNG ghi đè, đề xuất xoa_de.
- Không log nội dung đề/bài làm trong hàm này.

Hành động GHI (luu_de, xoa_de) do tầng module quyết định có bật không (mặc định TẮT
theo quy ước chung); bản thân kho_de không tự bật gì.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

TRAN_DE_DANH_TINH = 100

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KHOA_OK = re.compile(r"^[0-9a-f]{8,64}$")


def _thu_muc_danh_tinh(goc: Path, khoa: str) -> Path:
    if not _KHOA_OK.fullmatch(khoa or ""):
        raise ValueError("khoá danh tính không hợp lệ")
    p = Path(goc) / "tro-ly-giao-vien" / khoa / "de"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p.parent, 0o700)
    os.chmod(p, 0o700)
    return p


def _ten_tep(quiz_id: str) -> str:
    if not _ID_OK.fullmatch(quiz_id or ""):
        raise ValueError("quiz_id không hợp lệ")
    return quiz_id + ".json"


def luu_de(goc: Path | str, khoa: str, quiz_id: str, de: dict[str, Any]) -> dict[str, Any]:
    """Lưu một đề (ghi đè cùng quiz_id của cùng danh tính)."""
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        ten = _ten_tep(quiz_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    if len(list(thu_muc.glob("*.json"))) >= TRAN_DE_DANH_TINH and not (thu_muc / ten).exists():
        return {"ok": False, "loi": f"vượt trần {TRAN_DE_DANH_TINH} đề/danh tính"}
    du_lieu = json.dumps(de, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        fd, tam = tempfile.mkstemp(dir=thu_muc, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "wb") as f:
            f.write(du_lieu)
            f.flush()
            os.fsync(fd)
        os.chmod(tam, 0o600)
        os.replace(tam, thu_muc / ten)
        os.chmod(thu_muc / ten, 0o600)
    except OSError as e:
        return {"ok": False, "loi": f"không ghi được đề: {e}"}
    return {"ok": True, "quiz_id": quiz_id, "duong_dan": thu_muc / ten}


def doc_de(goc: Path | str, khoa: str, quiz_id: str) -> dict[str, Any]:
    """Đọc một đề. Tệp hỏng → báo lỗi, không xoá, không ghi đè."""
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        tep = thu_muc / _ten_tep(quiz_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    if not tep.is_file():
        return {"ok": False, "loi": "không thấy đề"}
    try:
        de = json.loads(tep.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "loi": f"tệp đề hỏng, dùng xoa_de rồi lưu lại: {e}"}
    return {"ok": True, "quiz_id": quiz_id, "de": de}


def liet_ke_de(goc: Path | str, khoa: str) -> dict[str, Any]:
    """Liệt kê quiz_id của đúng một danh tính (không lộ nội dung)."""
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    return {"ok": True, "danh_sach": sorted(p.stem for p in thu_muc.glob("*.json"))}


def xoa_de(goc: Path | str, khoa: str, quiz_id: str) -> dict[str, Any]:
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        tep = thu_muc / _ten_tep(quiz_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    try:
        tep.unlink(missing_ok=True)
    except OSError as e:
        return {"ok": False, "loi": f"không xoá được: {e}"}
    return {"ok": True, "quiz_id": quiz_id}
