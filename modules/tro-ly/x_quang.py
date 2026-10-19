"""NV136-F — X-quang prompt: nhật ký chỉ-thêm mỗi lượt chạy (module tro-ly).

Mỗi lượt ghi: danh sách mảnh vào (id, nguon, token ước tính, lý do vào),
mảnh bị loại (lý do: tran/trung/dieu_kien), tổng token theo tầng, công cụ có
schema, công cụ chỉ ở danh mục.

- Chỉ-thêm: file JSONL, mỗi dòng một bản ghi, không sửa/xoá dòng cũ.
- LƯỢC ĐỒ ĐÓNG: chỉ ghi các trường số/đếm/định danh cố định được khai tường minh;
  không ghi giá trị tự do; trường lạ bị LOẠI BỎ hoàn toàn (không lọc danh sách từ khoá).
  Số liệu token (tổng, theo tầng) luôn nguyên vẹn; khoá/cookie/mật khẩu không thể lọt vào.
- API quản trị đọc có phân quyền theo hồ sơ (IDOR): chỉ đọc được hồ sơ của mình.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

TANG_HOP_LE = ("on_dinh", "ngu_canh", "hay_doi")


def _chuan_hoa_manh_vao(m: Any) -> dict | None:
    if not isinstance(m, dict):
        return None
    try:
        tok = int(m.get("token", 0))
    except (ValueError, TypeError):
        tok = 0
    return {
        "id": str(m.get("id", "")),
        "nguon": str(m.get("nguon", "")),
        "token": tok,
        "tang": str(m.get("tang", "")),
        "ly_do": str(m.get("ly_do", "")),
    }


def _chuan_hoa_manh_loai(m: Any) -> dict | None:
    if not isinstance(m, dict):
        return None
    try:
        tok = int(m.get("token", 0))
    except (ValueError, TypeError):
        tok = 0
    return {
        "id": str(m.get("id", "")),
        "nguon": str(m.get("nguon", "")),
        "token": tok,
        "ly_do": str(m.get("ly_do", "")),
    }


def _chuan_hoa_token_theo_tang(ttt: Any) -> dict[str, int]:
    res = {t: 0 for t in TANG_HOP_LE}
    if isinstance(ttt, dict):
        for t in TANG_HOP_LE:
            if t in ttt:
                try:
                    res[t] = int(ttt[t])
                except (ValueError, TypeError):
                    pass
    return res


def _chuan_hoa_danh_sach_chuoi(ds: Any) -> list[str]:
    if not isinstance(ds, (list, tuple)):
        return []
    return [str(x) for x in ds if isinstance(x, (str, int, float))]


class NhatKyXQuang:
    """Nhật ký X-quang chỉ-thêm theo lược đồ đóng, lưu JSONL bền có khoá luồng."""

    def __init__(self, duong_dan: str | Path) -> None:
        self.duong_dan = Path(duong_dan)
        self._khoa = threading.RLock()
        self.duong_dan.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.duong_dan.parent, 0o700)
        except OSError:
            pass

    def ghi(self, *, luot_id: str, ho_so_id: str, x_quang: dict,
            cong_cu_schema: list[str], cong_cu_danh_muc: list[str],
            **them: Any) -> None:
        x_quang = x_quang or {}
        manh_vao = [_chuan_hoa_manh_vao(m) for m in x_quang.get("manh_vao", [])]
        manh_vao = [m for m in manh_vao if m is not None]

        manh_loai = [_chuan_hoa_manh_loai(m) for m in x_quang.get("manh_loai", [])]
        manh_loai = [m for m in manh_loai if m is not None]

        try:
            tong_token = int(x_quang.get("tong_token", 0))
        except (ValueError, TypeError):
            tong_token = 0

        # Lược đồ đóng cố định: trường lạ bị loại bỏ hoàn toàn
        ban_ghi = {
            "luot_id": str(luot_id),
            "ho_so_id": str(ho_so_id),
            "manh_vao": manh_vao,
            "manh_loai": manh_loai,
            "token_theo_tang": _chuan_hoa_token_theo_tang(x_quang.get("token_theo_tang")),
            "tong_token": tong_token,
            "cong_cu_schema": _chuan_hoa_danh_sach_chuoi(cong_cu_schema),
            "cong_cu_danh_muc": _chuan_hoa_danh_sach_chuoi(cong_cu_danh_muc),
        }

        with self._khoa:
            p = self.duong_dan
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(p.parent, 0o700)
            except OSError:
                pass
            dong = json.dumps(ban_ghi, ensure_ascii=False) + "\n"
            tep_moi = not p.exists()
            flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
            fd = os.open(str(p), flags, 0o600)
            try:
                if tep_moi:
                    try:
                        os.fchmod(fd, 0o600)
                    except OSError:
                        pass
                os.write(fd, dong.encode("utf-8"))
            finally:
                os.close(fd)

    def doc(self, *, ho_so_id: str, nguoi_hoi_ho_so_id: str,
            luot_id: str | None = None) -> list[dict]:
        """Đọc các dòng của một hồ sơ. IDOR: người hỏi khác hồ sơ → từ chối."""
        if nguoi_hoi_ho_so_id != ho_so_id:
            raise PermissionError("khong_duoc_doc_ho_so_khac")
        with self._khoa:
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
