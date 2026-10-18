"""NV136-G — Sổ lỗi công cụ (bộ nhớ thất bại): lưu bền theo hồ sơ (module tro-ly).

Lượt công cụ bị từ chối/sai tham số/kết quả lỗi ghi
(cong_cu, loai_loi, tom_tat ngắn). Khi công cụ đó được nạp lại, tối đa 3 dòng
lỗi gần nhất LIÊN QUAN công cụ ấy được đưa vào mảnh hay_doi — không dán cả sổ.

Học ý c2a `services/agent/bai_hoc.py`: nhắc đúng chỗ liên quan, không dán cả sổ.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SO_DONG_TOI_DA = 3
DO_DAI_TOM_TAT_TOI_DA = 200


class SoLoi:
    """Sổ lỗi theo hồ sơ, lưu JSON bền."""

    def __init__(self, duong_dan: str | Path) -> None:
        self.duong_dan = Path(duong_dan)
        self.duong_dan.parent.mkdir(parents=True, exist_ok=True)

    def _doc_tat(self) -> dict:
        if not self.duong_dan.exists():
            return {}
        try:
            return json.loads(self.duong_dan.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _ghi_tat(self, du_lieu: dict) -> None:
        tmp = self.duong_dan.with_suffix(".tmp")
        tmp.write_text(json.dumps(du_lieu, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.duong_dan)

    def ghi(self, ho_so_id: str, cong_cu: str, loai_loi: str, tom_tat: str) -> None:
        du_lieu = self._doc_tat()
        muc = {"cong_cu": cong_cu, "loai_loi": loai_loi,
               "tom_tat": tom_tat[:DO_DAI_TOM_TAT_TOI_DA],
               "luc": int(time.time())}
        du_lieu.setdefault(ho_so_id, []).append(muc)
        # Giữ sổ gọn: tối đa 200 mục mỗi hồ sơ (cũ nhất bị loại).
        du_lieu[ho_so_id] = du_lieu[ho_so_id][-200:]
        self._ghi_tat(du_lieu)

    def lay_lien_quan(self, ho_so_id: str, cong_cu: str,
                      toi_da: int = SO_DONG_TOI_DA) -> list[dict]:
        """Tối đa `toi_da` dòng gần nhất của ĐÚNG công cụ này (IDOR theo hồ sơ)."""
        muc = [m for m in self._doc_tat().get(ho_so_id, [])
               if m.get("cong_cu") == cong_cu]
        return muc[-toi_da:][::-1] if toi_da > 0 else []

    def manh_hay_doi(self, ho_so_id: str, cong_cu: str) -> str:
        """Dựng nội dung mảnh hay_doi từ sổ lỗi (đưa vào prompt)."""
        muc = self.lay_lien_quan(ho_so_id, cong_cu)
        if not muc:
            return ""
        dong = [f"- {m['loai_loi']}: {m['tom_tat']}" for m in muc]
        return ("Rút kinh nghiệm khi dùng công cụ %s:\n%s"
                % (cong_cu, "\n".join(dong)))
