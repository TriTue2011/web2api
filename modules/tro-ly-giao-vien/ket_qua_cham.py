"""Lưu kết quả chấm tự luận bằng model theo danh tính giáo viên (0600, nguyên tử).

An toàn (cùng khuôn kho_de.py):
- Kết quả nằm dưới <WEB2API_DATA>/du_lieu/tro-ly-giao-vien/<khoa>/ket_qua/
  <ket_qua_id>.json; <khoa> = SHA-256(danh_tinh_id)[:16] — GV A không bao giờ
  thấy kết quả của GV B (IDOR bị chặn ở tầng đường dẫn).
- Tệp 0600, thư mục 0700; ghi nguyên tử (temp + os.replace).
- Bản ghi CHỈ chứa: đề (đã cắt), barem, điểm/nhận xét theo tiêu chí, điểm đề
  xuất/chính thức, trạng thái, thời điểm, model đã dùng. KHÔNG chứa bài làm
  học sinh và KHÔNG chứa tên học sinh — bài làm chỉ sống trong lượt chấm.
- Trần kết quả mỗi danh tính để đĩa không phình vô hạn.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

TRAN_KET_QUA_DANH_TINH = 200

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KHOA_OK = re.compile(r"^[0-9a-f]{8,64}$")


def _thu_muc_danh_tinh(goc: Path, khoa: str) -> Path:
    if not _KHOA_OK.fullmatch(khoa or ""):
        raise ValueError("khoá danh tính không hợp lệ")
    p = Path(goc) / "tro-ly-giao-vien" / khoa / "ket_qua"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p.parent, 0o700)
    os.chmod(p, 0o700)
    return p


def _ten_tep(ket_qua_id: str) -> str:
    if not _ID_OK.fullmatch(ket_qua_id or ""):
        raise ValueError("ket_qua_id không hợp lệ")
    return ket_qua_id + ".json"


def luu_ket_qua(goc: Path | str, khoa: str, ket_qua_id: str,
                ban_ghi: dict[str, Any]) -> dict[str, Any]:
    """Lưu/ghi đè một bản ghi kết quả (ghi đè cùng id của cùng danh tính)."""
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        ten = _ten_tep(ket_qua_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    if (len(list(thu_muc.glob("*.json"))) >= TRAN_KET_QUA_DANH_TINH
            and not (thu_muc / ten).exists()):
        return {"ok": False,
                "loi": f"vượt trần {TRAN_KET_QUA_DANH_TINH} kết quả/danh tính"}
    du_lieu = json.dumps(ban_ghi, ensure_ascii=False, indent=2).encode("utf-8")
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
        return {"ok": False, "loi": f"không ghi được kết quả: {e}"}
    return {"ok": True, "ket_qua_id": ket_qua_id}


def doc_ket_qua(goc: Path | str, khoa: str, ket_qua_id: str) -> dict[str, Any]:
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        tep = thu_muc / _ten_tep(ket_qua_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    if not tep.is_file():
        return {"ok": False, "loi": "không thấy kết quả chấm"}
    try:
        ban_ghi = json.loads(tep.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "loi": f"tệp kết quả hỏng, dùng xoa_ket_qua: {e}"}
    return {"ok": True, "ket_qua_id": ket_qua_id, "ket_qua": ban_ghi}


def liet_ke_ket_qua(goc: Path | str, khoa: str) -> dict[str, Any]:
    """Tóm tắt kết quả của đúng một danh tính (không lộ nội dung chi tiết)."""
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    ra: list[dict[str, Any]] = []
    for tep in sorted(thu_muc.glob("*.json"), key=lambda p: p.name):
        try:
            bg = json.loads(tep.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # tệp hỏng: bỏ qua ở danh sách, doc_ket_qua sẽ báo rõ
        ra.append({
            "ket_qua_id": tep.stem,
            "luc": bg.get("luc"),
            "mon": bg.get("mon"),
            "diem_de_xuat": bg.get("diem_de_xuat"),
            "diem_chinh_thuc": bg.get("diem_chinh_thuc"),
            "trang_thai": bg.get("trang_thai"),
        })
    return {"ok": True, "danh_sach": ra}


def xoa_ket_qua(goc: Path | str, khoa: str, ket_qua_id: str) -> dict[str, Any]:
    try:
        thu_muc = _thu_muc_danh_tinh(Path(goc), khoa)
        tep = thu_muc / _ten_tep(ket_qua_id)
    except ValueError as e:
        return {"ok": False, "loi": str(e)}
    try:
        tep.unlink(missing_ok=True)
    except OSError as e:
        return {"ok": False, "loi": f"không xoá được: {e}"}
    return {"ok": True, "ket_qua_id": ket_qua_id}
