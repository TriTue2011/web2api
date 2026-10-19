"""Hộp thư hỏi/đáp một đường của module tu-van-hanh.

Port từ `services/hoi_giam_sat.py` của c2a (kho của chính chủ máy, đang chạy):
- Hỏi chủ máy bằng tin đánh số, trả lời bằng `cl <số>` (rỗng = chưa trả lời,
  KHÔNG tự suy diễn).
- Hỏi quá hạn thì bỏ — không hành động theo bối cảnh cũ.
- Đổi lớp kết nối: c2a ghi tệp JSON; ở đây giữ trong bộ nhớ theo vòng đời module
  (câu hỏi vốn có hạn dùng ngắn, mất khi khởi động lại là an toàn).
- Cải thiện: hàng đợi có trần (c2a chỉ một câu hỏi tại một thời điểm).

Nguyên tắc, không danh sách: chỉ chuỗi bắt đầu bằng tiền tố `cl ` (không phân
biệt hoa/thường) mới là trả lời; còn lại trả None để tin đi tiếp như thường.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

#: Tiền tố trả lời — giữ đúng c2a để chủ máy quen tay.
_TIEN_TO = ("cl ", "claude ")


class HopThu:
    """Hàng đợi câu hỏi có trần và hạn dùng."""

    def __init__(self, han_gio: float = 6.0, tran_hang_doi: int = 5,
                 lay_gio: Callable[[], float] | None = None):
        self._han = max(60.0, float(han_gio) * 3600.0)
        self._tran = max(1, int(tran_hang_doi))
        self._lay_gio = lay_gio or time.time
        self._hang: dict[str, dict] = {}

    # ── đặt câu hỏi ──

    def dat_cau_hoi(self, cau_hoi: str, lua_chon: list[str]) -> tuple[str, str]:
        """Đặt câu hỏi, trả (mã, text đã đánh số để gửi qua kênh).

        Vượt trần hàng đợi thì bỏ câu cũ nhất chưa trả lời.
        """
        ch = (cau_hoi or "").strip()
        ds = [str(x).strip() for x in (lua_chon or []) if str(x).strip()]
        if not ch or not ds:
            return "", ""
        self._don_het_han()
        while len(self._hang) >= self._tran:
            cu_nhat = min(self._hang, key=lambda k: self._hang[k]["ts"])
            del self._hang[cu_nhat]
        ma = uuid.uuid4().hex[:12]
        self._hang[ma] = {
            "cau_hoi": ch,
            "lua_chon": ds,
            "ts": self._lay_gio(),
            "tra_loi": "",
            "tra_loi_ts": 0.0,
        }
        dong = [ch, ""]
        dong += [f"{i}. {x}" for i, x in enumerate(ds, 1)]
        dong += ["", 'Trả lời: nhắn "cl 1" (hoặc số tương ứng).']
        return ma, "\n".join(dong)

    # ── trả lời ──

    def tra_loi(self, text: str) -> tuple[str | None, str]:
        """Chủ máy trả lời. Trả (mã_câu_hỏi, thông_báo) hoặc (None, thông_báo).

        (None, "") = không phải trả lời — tin đi tiếp như thường.
        """
        t = (text or "").strip()
        low = t.lower()
        if not any(low.startswith(p) for p in _TIEN_TO):
            return None, ""
        for p in _TIEN_TO:
            if low.startswith(p):
                t = t[len(p):].strip()
                break
        # Ghi nhận có câu hỏi vừa hết hạn TRƯỚC khi dọn, để báo đúng lý do.
        bay_gio = self._lay_gio()
        co_het_han = any(bay_gio - float(d.get("ts") or 0) > self._han
                         for d in self._hang.values())
        self._don_het_han()
        if not self._hang:
            if co_het_han:
                return None, "Câu hỏi đã quá hạn nên em bỏ rồi."
            return None, ""
        # Trả lời cho câu hỏi cũ nhất đang chờ (ngữ nghĩa c2a).
        ma = min(self._hang, key=lambda k: self._hang[k]["ts"])
        d = self._hang[ma]
        ds = d["lua_chon"]
        chon = ""
        if t.isdigit():
            i = int(t)
            if 1 <= i <= len(ds):
                chon = ds[i - 1]
        if not chon:
            for x in ds:
                if x.lower() == t.lower():
                    chon = x
                    break
        if not chon:
            return None, ("Chưa hiểu. Câu hỏi đang chờ:\n"
                          + "\n".join(f"{i}. {x}" for i, x in enumerate(ds, 1)))
        d["tra_loi"] = chon
        d["tra_loi_ts"] = self._lay_gio()
        return ma, f"Đã ghi nhận: {chon}."

    # ── đọc / dọn ──

    def doc_tra_loi(self, ma: str) -> dict:
        """Đọc câu trả lời. Rỗng = chưa trả lời hoặc đã hết hạn = KHÔNG làm gì."""
        d = self._hang.get(ma)
        if not d or not d.get("tra_loi"):
            return {}
        if self._lay_gio() - float(d.get("ts") or 0) > self._han:
            return {}
        return {
            "cau_hoi": d.get("cau_hoi") or "",
            "tra_loi": d.get("tra_loi") or "",
            "tra_loi_ts": float(d.get("tra_loi_ts") or 0),
        }

    def xoa(self, ma: str) -> None:
        self._hang.pop(ma, None)

    def so_cau_hoi_cho(self) -> int:
        self._don_het_han()
        return len(self._hang)

    def _don_het_han(self) -> None:
        bay_gio = self._lay_gio()
        het = [k for k, d in self._hang.items()
               if bay_gio - float(d.get("ts") or 0) > self._han]
        for k in het:
            del self._hang[k]
