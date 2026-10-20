"""Giáo án 6 pha + lời khen + verbalize cho TTS (phần thuần, KHÔNG gọi mô hình).

NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher.py), port theo LUẬT PORT.
CHỈNH: `build_lesson_plan` dùng `from services.agent import teacher_workspace as tw` ->
`from . import mon_hoc as mon` (GRADES/SUBJECT_LABEL/chuan_mon/level_label).
Văn bản giáo án còn nhắc các hành động của đợt sau (search_sgk, teacher_hint...) — giữ
nguyên như c2a đã chạy; các đợt sau cài tiếp các hành động đó (xem kiểm kê).
"""


from __future__ import annotations

import re
from typing import Any

from . import mon_hoc as mon


def skill_for_grade(grade: int) -> str:
    g = int(grade or 0)
    if g <= 5:
        return "giao-vien-tieu-hoc"
    if g <= 9:
        return "giao-vien-thcs"
    return "giao-vien-thpt"


_MATH_VERBAL = (
    (re.compile(r"\s*×\s*|\s*x\s+(?=\d)", re.I), " nhân "),
    (re.compile(r"\s*÷\s*"), " chia "),
    (re.compile(r"\s*=\s*"), " bằng "),
    (re.compile(r"\s*\+\s*"), " cộng "),
    (re.compile(r"\s*−\s*|\s*–\s*|\s*—\s*"), " trừ "),
    (re.compile(r"\s*%\s*"), " phần trăm "),
    (re.compile(r"\s*≠\s*"), " khác "),
    (re.compile(r"\s*≤\s*"), " nhỏ hơn hoặc bằng "),
    (re.compile(r"\s*≥\s*"), " lớn hơn hoặc bằng "),
    (re.compile(r"\s*<\s*"), " nhỏ hơn "),
    (re.compile(r"\s*>\s*"), " lớn hơn "),
    (re.compile(r"\s*²"), " bình phương"),
    (re.compile(r"\s*³"), " lập phương"),
    (re.compile(r"[#*_`]+"), " "),
)


def verbalize_for_tts(text: str) -> str:
    """Chuyển ký hiệu toán/markdown thành lời — sẵn sàng đọc loa."""
    t = (text or "").strip()
    if not t:
        return ""
    for pat, rep in _MATH_VERBAL:
        t = pat.sub(rep, t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def praise_for(score_0_10: int, *, grade: int = 5) -> str:
    """Lời khen ngắn theo điểm — growth mindset, không mắng."""
    s = max(0, min(10, int(score_0_10)))
    if grade <= 5:
        if s >= 9:
            return "Giỏi lắm! Con làm chắc tay rồi."
        if s >= 7:
            return "Tốt lắm! Gần đạt điểm tuyệt đối rồi."
        if s >= 5:
            return "Cố gắng tốt! Cô/thầy cùng con chỉnh chỗ còn lệch nhé."
        return "Không sao, lỗi là bạn của người học. Mình thử lại từng bước nhé."
    if s >= 9:
        return "Rất tốt — nắm vững dạng này."
    if s >= 7:
        return "Khá ổn; còn 1–2 chỗ tinh chỉnh là xuất sắc."
    if s >= 5:
        return "Đã có hướng đúng; cần bổ sung bước / ý cho đủ."
    return "Chưa đạt lần này — mình khoanh lỗi và luyện lại 1 dạng tương tự."


def build_lesson_plan(
    *,
    grade: int,
    subject: str,
    topic: str,
    objective: str = "",
    student_weak: str = "",
    level_note: str = "",
) -> str:
    """Giáo án 6 pha kiểu lớp học (mục tiêu → kết), markdown ngắn cho agent."""
    g = int(grade) if int(grade) in mon.GRADES else 5
    sub = mon.chuan_mon(subject) or "toan"
    topic = (topic or "ôn tập").strip()
    obj = (objective or "").strip() or f"Nắm được ý chính: {topic}"
    skill = skill_for_grade(g)
    level = mon.level_label(g)
    lines = [
        f"**Giáo án lớp học** · Lớp {g} ({level}) · {mon.SUBJECT_LABEL.get(sub, sub)}",
        f"Skill gợi ý: `{skill}`",
        f"**Mục tiêu (I can…):** {obj}",
        f"**Chủ đề:** {topic}",
    ]
    if student_weak:
        lines.append(f"**Lưu ý từ memory HS:** còn yếu — {student_weak}")
    if level_note:
        lines.append(f"**Ghi chú cấp học:** {level_note}")
    lines.extend([
        "",
        "### 1) Mục tiêu (30s)",
        f"- Nói rõ 1 câu: hôm nay em/con sẽ… «{obj}».",
        "- Không nhồi nhiều ý.",
        "",
        "### 2) Khởi động (1–2 phút)",
        "- 1 câu nối kiến thức cũ / ví dụ đời sống gần gũi.",
        "- Hỏi HS nhớ gì liên quan (Socratic, chưa giảng).",
        "",
        "### 3) Giảng — I do → We do",
        "- **I do:** 1 ví dụ mẫu, nêu từng bước (không nhảy bước).",
        "- **We do:** 1 bài cùng làm; hỏi «bước tiếp theo là gì?» trước khi chốt.",
        "- Dùng **search_sgk** lấy khung kiến thức; không bịa trang SGK.",
        "",
        "### 4) Luyện — You do + gợi ý bậc thang",
        "- HS tự làm 1 bài tương tự.",
        "- Kẹt: **teacher_hint** level=1 rồi 2 rồi 3 (không đập đáp án ngay).",
        "",
        "### 5) Kiểm tra hiểu (CFU)",
        "- **teacher_check** 1 câu exit ticket HOẶC 1 câu miệng «nhắc lại giúp…».",
        "- Đúng → khen cụ thể; sai → chỉ lỗi + 1 bài siêu ngắn.",
        "",
        "### 6) Kết · về nhà · memory",
        "- 2–4 câu tóm tắt (TTS-friendly, **verbalize_for_tts**).",
        "- **teacher_memory** add: weak/strong + note buổi học.",
        "- (Tuỳ chọn) schedule nhắc ôn nếu PH nhờ.",
        "",
        "_Nguyên tắc: productive struggle — HS phải nghĩ; AI/coach hỏi trước khi cho đáp án._",
    ])
    return "\n".join(lines)


def format_classroom_reply(
    *,
    phase: str,
    body: str,
    check_question: str = "",
    tts_summary: str = "",
) -> str:
    """Bọc phản hồi 1 lượt dạy: pha + nội dung + CFU + TTS gợi ý."""
    phase = (phase or "").strip() or "giang"
    parts = [f"[{phase}]"]
    if body.strip():
        parts.append(body.strip())
    if check_question.strip():
        parts.append("")
        parts.append(f"**Kiểm tra hiểu:** {check_question.strip()}")
    if tts_summary.strip():
        parts.append("")
        parts.append(f"**Đọc loa (TTS):** {verbalize_for_tts(tts_summary)}")
    return "\n".join(parts)

# ── Bí danh tiếng Việt cho tầng hành động ──

def loi_khen(diem_0_10: int, lop: int = 5) -> str:
    return praise_for(diem_0_10, grade=lop)


def mau_giao_an(lop: int = 5, mon: str = "toan", chu_de: str = "",
                muc_tieu: str = "", thoi_luong: str = "40 phút") -> str:
    return build_lesson_plan(grade=lop, subject=mon, topic=chu_de,
                             objective=muc_tieu)


def verbalize_cho_tts(van_ban: str) -> str:
    return verbalize_for_tts(van_ban)
