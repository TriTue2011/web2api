"""Cắt văn bản thành các đoạn dịch-được và đoạn được-bảo-vệ.

NGUỒN: chép mã chạy thật từ ``services/translate_service.py``
(``_KHONG_DICH`` + ``_tach_doan``) tại commit d879b03 của kho c2a
(đọc từ bản sao chỉ-đọc ``~/workspace/c2a-nv146/``), chỉ đổi tên hàm cho hợp
không gian tên module. KHÔNG sửa c2a.

Ý tưởng giữ nguyên: không "dặn model đừng dịch" code/URL/markup — CẮT các
đoạn này ra khỏi phần gửi đi, dịch phần còn lại, rồi ghép lại đúng thứ tự.
Thứ tự các nhánh regex CÓ Ý NGHĨA: khối mã phải đứng trước mọi thứ khác vì
bên trong nó có cả URL, dấu ``|`` và markup đầu dòng.
"""

from __future__ import annotations

import re

#: Regex các đoạn KHÔNG được dịch. (port nguyên từ c2a, xem docstring module)
_KHONG_DICH = re.compile(
    r"```.*?```"                        # khối mã ba dấu huyền
    r"|~~~.*?~~~"                       # khối mã ba dấu ngã
    r"|`[^`\n]+`"                       # mã trong dòng
    r"|image://\S+"                     # marker ảnh nội bộ của dự án
    r"|https?://\S+"                    # URL
    r"|[\w.+-]+@[\w-]+\.[\w.]+"         # email
    r"|\{\{[^{}]{0,80}\}\}"             # {{bien}} của template
    r"|<[A-Za-z/!][^>\n]{0,200}>"       # thẻ HTML/XML
    # ── Bộ xương Markdown ───────────────────────────────────────────────────
    # Không chừa những thứ này ra thì model ăn luôn cả cấu trúc: "## Tiêu đề"
    # mất dấu thăng, "- mục" mất gạch đầu dòng, bảng mất cột.
    r"|^[ \t]*(?:>[ \t]*)*(?:#{1,6}|[-*+]|\d{1,3}[.)])[ \t]*"  # markup đầu dòng
    r"|^[ \t]*[-*_=]{3,}[ \t]*$"        # đường kẻ ngang / dòng kẻ bảng
    r"|^[ \t]*\|?[-: \t|]+\|?[ \t]*$"   # dòng phân cách của bảng |---|:--:|
    r"|\|"                              # vách ô bảng — mỗi ô thành một đoạn riêng
    r"|^[ \t]*>[ \t]*",                 # trích dẫn không kèm markup nào khác
    re.DOTALL | re.MULTILINE,
)


def tach_doan(text: str) -> list[tuple[bool, str]]:
    """Cắt văn bản thành ``[(dịch_được, đoạn), ...]`` theo ``_KHONG_DICH``.

    (port nguyên từ ``_tach_doan`` của c2a; chỉ đổi tên.)
    """
    doan: list[tuple[bool, str]] = []
    vi_tri = 0
    for m in _KHONG_DICH.finditer(text):
        if m.start() > vi_tri:
            doan.append((True, text[vi_tri:m.start()]))
        doan.append((False, m.group(0)))
        vi_tri = m.end()
    if vi_tri < len(text):
        doan.append((True, text[vi_tri:]))
    return doan
