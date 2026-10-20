"""Mục lục tự động từ markdown SGK + đoán tập sách (phần thuần, không I/O).

NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher_lecture.py: toc_tu_markdown,
~/workspace/c2a-nv146/services/agent/teacher_workspace.py: detect_volume), port theo LUẬT PORT.
Markdown đưa vào là DỮ LIỆU KHÔNG TIN: chỉ phân tích bằng regex, không thực thi, không ghi.
Lưu mục lục (save_toc của c2a, ghi đĩa) KHÔNG port đợt này.
"""


from __future__ import annotations

import re
from typing import Any


_TOC_LINE_RE = re.compile(
    r"^[\s·•\-–]*"
    r"(?:\*\*)?(Bài|Unit|Chủ đề|Chương|Tuần)\s*(\d{1,3})(?:\*\*)?"
    r"[.．:：]?\s*"
    r"(?:[-–—]\s*)?"
    r"(?P<ten>[^\n]{2,200}?)"
    r"\s*(?:[-–—.…]{1,}|\s)\s*(?P<trang>\d{1,3})\s*$",
    re.M | re.IGNORECASE,
)

def toc_tu_markdown(md: str, *, tap: str = "") -> list[dict[str, Any]]:
    """Rút mục lục từ markdown một quyển sách (đường nạp file/URL gọi).

    Vì sao cần: dropdown "chọn bài" của tab Bài giảng đọc file mục lục có cấu
    trúc. Trước đây chỉ có đường gieo tay, nên sách nạp qua giao diện thì tra
    cứu được mà KHÔNG chọn được bài — người dùng thấy sách "đã nạp" nhưng ô chọn
    bài trống, không hiểu vì sao.

    Khuôn nhận dạng lấy từ 82 quyển đã đọc: dòng mục lục của bộ Kết nối luôn là
    "<Bài|Unit|Chủ đề|Chương|Tuần> <số>. <tên> — <trang>", phần nối giữa tên và
    trang có thể là gạch dài, dấu chấm rải, hay chỉ khoảng trắng.

    Lọc thêm hai thứ để không nhặt rác:
      · trang phải tăng dần theo thứ tự bài — mục lục thật luôn vậy; dòng trong
        thân sách ("xem Bài 12 trang 3") thì không;
      · mỗi số bài giữ bản ĐẦU TIÊN, vì mục lục nằm ở đầu quyển còn các lần
        nhắc lại về sau là trích dẫn.
    """
    ra: list[dict[str, Any]] = []
    da_co: set[str] = set()
    trang_cuoi = 0
    for m in _TOC_LINE_RE.finditer(md or ""):
        loai, so = m.group(1), m.group(2)
        ten = re.sub(r"\s+", " ", m.group("ten")).strip(" .·•*-–—")
        try:
            trang = int(m.group("trang"))
        except (TypeError, ValueError):
            continue
        if not ten or len(ten) < 2 or trang <= 0 or trang > 999:
            continue
        bai = so if loai.lower() in ("bài", "chủ đề", "chương", "tuần") else f"Unit {so}"
        if bai in da_co:
            continue
        if trang < trang_cuoi:
            continue          # số trang thụt lùi ⇒ không còn ở vùng mục lục
        da_co.add(bai)
        trang_cuoi = trang
        ra.append({"bai": bai, "ten": ten, "trang": trang, "tap": tap})
    return ra


_VOL_RE = re.compile(
    r"(?:t[aậ]p|volume|vol)[\s._-]*(m[oộ]t|hai|1|2|i{1,2})(?![\w])",
    re.IGNORECASE)

def detect_volume(*nguon: str) -> str:
    """Đoán TẬP của quyển sách từ tiêu đề / tên tệp: "tập một" | "tập hai" | "".

    Vì sao cần: kho gộp cả hai tập của một môn vào cùng lớp–môn, nên "ghi đè"
    không có tập là xoá luôn tập kia — nạp tập hai làm mất tập một vừa nạp hôm
    trước, im lặng, không gì báo. Giao diện cũng chỉ hiện "lớp 4 · toan" nên
    người dùng không biết trên máy đang có tập nào.

    Nhận cả "tập 1", "tập một", "tap-1", "SGK_Toán_4_KNTT_tập_1.pdf" — đúng các
    khuôn tên tệp có thật trên máy chủ.
    """
    for s in nguon:
        m = _VOL_RE.search(str(s or ""))
        if not m:
            continue
        raw = (m.group(1) or m.group(2) or "").lower()
        if raw in ("mot", "một", "1", "i"):
            return "tập một"
        if raw in ("hai", "2", "ii"):
            return "tập hai"
    return ""


muc_luc_tu_markdown = toc_tu_markdown
doan_tap = detect_volume
