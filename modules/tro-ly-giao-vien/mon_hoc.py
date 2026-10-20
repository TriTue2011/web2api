"""Danh mục môn theo từng lớp (phần thuần dữ liệu, không I/O).

NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher_workspace.py), port theo LUẬT PORT.
CHỈNH: đặt tên công khai tiếng Việt `chuan_mon` thay `_normalize_subject` (cùng hành vi).
"""


from __future__ import annotations


SUBJECTS = (
    "toan",   # Toán — cả 12 lớp
    "tviet",  # Tiếng Việt — lớp 1–5
    "van",    # Ngữ văn — lớp 6–12
    "anh",    # Tiếng Anh — cả 12 lớp
    "sudia",  # Lịch sử và Địa lí — lớp 4–9 (MỘT quyển)
    "su",     # Lịch sử — lớp 10–12 (quyển riêng)
    "dia",    # Địa lí — lớp 10–12 (quyển riêng)
    "ly",     # Vật lí — lớp 10–12
    "hoa",    # Hoá học — lớp 10–12
    "sinh",   # Sinh học — lớp 10–12
)
SUBJECT_LABEL = {
    "toan": "Toán",
    "tviet": "Tiếng Việt",
    "van": "Ngữ văn",
    "anh": "Tiếng Anh",
    "sudia": "Lịch sử và Địa lí",
    "su": "Lịch sử",
    "dia": "Địa lí",
    "ly": "Vật lí",
    "hoa": "Hoá học",
    "sinh": "Sinh học",
}

# Môn của TỪNG LỚP — khai rõ từng lớp một, KHÔNG gộp dải, để sửa lớp nào chỉ
# đụng đúng dòng lớp đó.
#
# Đo thật 2026-07-28 trên kho chính thức taphuan.nxbgd.vn, hỏi RIÊNG TỪNG mã môn
# (`?grade=N&subjects=<1 mã>`) rồi hợp lại. Mã môn của kho:
#   1=Tiếng Việt  2=Tiếng Anh  3=Toán  5=Vật lí  6=Hoá học  7=Sinh học
#   8=Lịch sử  9=Địa lí  21=Ngữ văn  22=Lịch sử và Địa lí
#
# PHẢI hỏi từng mã: trang danh mục CẮT BỚT kết quả khi lọc nhiều mã một lượt.
# Lớp 10 hỏi gộp `subjects=21,3,8,9,5,6,7,2` chỉ trả 12 quyển, hỏi riêng từng mã
# ra 17 quyển — thiếu hẳn Tiếng Anh, Hoá học, Sinh học. Ai đọc số từ truy vấn
# gộp sẽ kết luận sai là kho "không có" những môn đó.
#
# Số quyển đo được: lớp 1–2: 5 · lớp 3: 8 · lớp 4–6: 7 · lớp 7–9: 6 ·
# lớp 10–12: 17 (gồm cả các quyển "chuyên đề học tập").
#
# Lớp 3 còn 2 quyển Tiếng Hàn — chưa đưa vào vì hệ thống chưa có mã môn cho
# ngoại ngữ thứ hai. Lớp 6–9 không có Hoá/Sinh riêng (chương trình gộp vào Khoa
# học tự nhiên, mà kho không liệt kê quyển nào cho môn đó).
GRADE_SUBJECTS: dict[int, tuple[str, ...]] = {
    1:  ("toan", "tviet", "anh"),
    2:  ("toan", "tviet", "anh"),
    3:  ("toan", "tviet", "anh"),
    4:  ("toan", "tviet", "anh", "sudia"),
    5:  ("toan", "tviet", "anh", "sudia"),
    6:  ("toan", "van", "anh", "sudia"),
    7:  ("toan", "van", "anh", "sudia"),
    8:  ("toan", "van", "anh", "sudia"),
    9:  ("toan", "van", "anh", "sudia"),
    10: ("toan", "van", "anh", "su", "dia", "ly", "hoa", "sinh"),
    11: ("toan", "van", "anh", "su", "dia", "ly", "hoa", "sinh"),
    12: ("toan", "van", "anh", "su", "dia", "ly", "hoa", "sinh"),
}

def subjects_for(grade: int) -> tuple[str, ...]:
    """Môn của MỘT lớp. Lớp lạ → toàn bộ SUBJECTS (thà rộng hơn là chặn oan)."""
    try:
        g = int(grade)
    except (TypeError, ValueError):
        return SUBJECTS
    return GRADE_SUBJECTS.get(g, SUBJECTS)


# Tiểu học 1–5 · THCS 6–9 · THPT 10–12
GRADES = tuple(range(1, 13))


GRADES = tuple(range(1, 13))

def level_of(grade: int) -> str:
    if grade <= 5:
        return "tieu_hoc"
    if grade <= 9:
        return "thcs"
    return "thpt"


def level_label(grade: int) -> str:
    return {"tieu_hoc": "Tiểu học", "thcs": "THCS", "thpt": "THPT"}.get(
        level_of(grade), "")

SUBJECT_ALIASES: dict[str, str] = {
    "toan": "toan", "toán": "toan", "math": "toan",
    # Tiếng Việt (1–5) và Ngữ văn (6–12) là HAI mã khác nhau. "van" trơ trọi →
    # Ngữ văn; muốn Tiếng Việt phải nói rõ. "tv" trước trỏ về `van`, giờ trỏ
    # `tviet` vì đó mới là thứ người ta viết tắt.
    "van": "van", "văn": "van", "ngu_van": "van", "ngữ văn": "van",
    "ngu van": "van",
    "tviet": "tviet", "tieng_viet": "tviet", "tiếng việt": "tviet",
    "tieng viet": "tviet", "tv": "tviet",
    "anh": "anh", "english": "anh", "tieng_anh": "anh", "tiếng anh": "anh",
    "en": "anh", "tieng anh": "anh",
    # Sách gộp lớp 4–9 có mã riêng; đừng để nó rơi về `su` hay `dia`.
    "sudia": "sudia", "su_dia": "sudia",
    "lich_su_va_dia_li": "sudia", "lich su va dia li": "sudia",
    "lịch sử và địa lí": "sudia", "lịch sử và địa lý": "sudia",
    "su": "su", "sử": "su", "lich_su": "su", "lich su": "su", "lịch sử": "su",
    "history": "su",
    "dia": "dia", "địa": "dia", "dia_ly": "dia", "dia ly": "dia",
    "địa lý": "dia", "dia li": "dia", "địa lí": "dia", "geography": "dia",
    "ly": "ly", "lý": "ly", "lí": "ly", "vat_ly": "ly", "vat ly": "ly",
    "vật lý": "ly", "vat li": "ly", "vật lí": "ly", "physics": "ly",
    "hoa": "hoa", "hóa": "hoa", "hoá": "hoa", "hoa_hoc": "hoa",
    "hoa hoc": "hoa", "hóa học": "hoa", "hoá học": "hoa", "chemistry": "hoa",
    "sinh": "sinh", "sinh_hoc": "sinh", "sinh hoc": "sinh",
    "sinh học": "sinh", "biology": "sinh",
}


def chuan_mon(subject: str) -> str | None:
    s = (subject or "").strip().lower()
    return SUBJECT_ALIASES.get(s) or (s if s in SUBJECTS else None)


_normalize_subject = chuan_mon

def nhan(mon: str) -> str | None:
    """Nhãn hiển thị của một môn (hoặc None nếu không biết)."""
    return SUBJECT_LABEL.get(mon)
