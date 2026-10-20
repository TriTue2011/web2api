"""Xác định lớp của học sinh + mạch kiến thức (phần thuần, không I/O, không lưu hồ sơ).

NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher_path.py), port theo LUẬT PORT.
CHỈNH: đặt tên công khai tiếng Việt: xac_dinh_lop(resolve_grade), mach_kien_thuc(strands_for),
school_year_start/grade_from_birth_year giữ nguyên tên (hàm nền được nhiều chỗ dùng).
Hồ sơ học sinh bền vững (get_or_create_profile, placement...) KHÔNG port đợt này (xem kiểm kê).
"""


from __future__ import annotations

from typing import Any


SCHOOL_YEAR_CUTOFF_MONTH = 8


def school_year_start(today: Any = None) -> int:
    """Năm bắt đầu của năm học đang xét (2026 = năm học 2026–2027)."""
    import datetime
    d = today or datetime.date.today()
    return d.year if d.month >= SCHOOL_YEAR_CUTOFF_MONTH else d.year - 1


def grade_from_birth_year(birth_year: Any, today: Any = None) -> int:
    """Lớp suy từ năm sinh. 0 = không suy được / ngoài phạm vi lớp 1–12.

    Trả 0 chứ KHÔNG kẹp về 1 hay 12: trẻ 4 tuổi chưa vào lớp 1 và người 30 tuổi
    không học lớp 12 — kẹp lại là bịa ra một lớp rồi mọi thứ phía sau (SGK, đề,
    lộ trình) đều sai mà không ai biết.
    """
    try:
        y = int(birth_year)
    except (TypeError, ValueError):
        return 0
    if not (1900 < y < 2200):
        return 0
    g = school_year_start(today) - y - 5
    return g if 1 <= g <= 12 else 0


def resolve_grade(profile: dict[str, Any], today: Any = None) -> dict[str, Any]:
    """Lớp CUỐI CÙNG của một hồ sơ + nói rõ nó đến từ đâu.

    Thứ tự: `grade` khai tay (ghi đè) > suy từ `birth_year` > 0 (chưa biết).
    Phải có đường ghi đè: học lại một năm, học vượt, vào lớp 1 muộn, hay học hệ
    khác đều làm công thức năm sinh sai — mà lớp sai thì sai luôn SGK và đề.
    """
    sy = school_year_start(today)
    by = profile.get("birth_year")
    computed = grade_from_birth_year(by, today)
    override = profile.get("grade")
    try:
        override = int(override or 0)
    except (TypeError, ValueError):
        override = 0
    if not (1 <= override <= 12):
        override = 0
    return {
        "grade": override or computed,
        "grade_computed": computed,
        "grade_override": override or None,
        "grade_source": "khai tay" if override else ("năm sinh" if computed else "chưa biết"),
        "birth_year": int(by) if str(by or "").strip().lstrip("-").isdigit() else None,
        "school_year": f"{sy}–{sy + 1}",
        "school_year_start": sy,
        "age": (sy - int(by)) if str(by or "").strip().isdigit() else None,
    }


# ── Student profile (độc lập) ───────────────────────────────────────────────


MATH_STRANDS: dict[str, list[dict[str, Any]]] = {
    "1-2": [
        {"id": "cong", "label": "Cộng trong phạm vi 100", "topics": ["cộng", "cộng có nhớ"]},
        {"id": "tru", "label": "Trừ / trừ có mượn", "topics": ["trừ", "trừ có mượn"]},
        {"id": "so_hoc", "label": "Số và so sánh", "topics": ["số", "so sánh"]},
    ],
    "3-5": [
        {"id": "nhan", "label": "Nhân / bảng cửu chương", "topics": ["nhân", "cửu chương"]},
        {"id": "chia", "label": "Chia", "topics": ["chia"]},
        {"id": "cong_tru", "label": "Cộng trừ nâng cao", "topics": ["cộng", "trừ"]},
        {"id": "do_luong", "label": "Đo lường / đồng hồ", "topics": ["độ dài", "đồng hồ"]},
    ],
    "6-9": [
        {"id": "phan_so", "label": "Phân số / số thập phân", "topics": ["phân số", "thập phân"]},
        {"id": "pt", "label": "Phương trình cơ bản", "topics": ["phương trình"]},
        {"id": "hinh", "label": "Hình học cơ bản", "topics": ["hình", "chu vi", "diện tích"]},
        {"id": "ty_le", "label": "Tỷ lệ / phần trăm", "topics": ["phần trăm", "tỷ lệ"]},
    ],
    "10-12": [
        {"id": "ham_so", "label": "Hàm số / đồ thị", "topics": ["hàm số"]},
        {"id": "luong_giac", "label": "Lượng giác cơ bản", "topics": ["lượng giác"]},
        {"id": "tich_phan", "label": "Tích phân / đạo hàm (khung)", "topics": ["đạo hàm", "tích phân"]},
        {"id": "hinh_khong_gian", "label": "Hình không gian", "topics": ["thể tích", "diện tích"]},
    ],
}

VAN_STRANDS = [
    {"id": "chinhta", "label": "Chính tả (hỏi/ngã, ch/tr)", "topics": ["chính tả", "hỏi", "ngã"]},
    {"id": "daucau", "label": "Dấu câu", "topics": ["dấu câu"]},
    {"id": "doc", "label": "Đọc hiểu", "topics": ["đọc hiểu"]},
    {"id": "tuloai", "label": "Từ loại", "topics": ["từ loại"]},
    {"id": "viet", "label": "Viết câu / đoạn", "topics": ["viết", "đoạn văn"]},
]

# Anh: 5 kỹ năng CEFR-aligned
EN_STRANDS = [
    {"id": "grammar", "label": "Grammar (ngữ pháp)", "topics": ["grammar", "present", "past"]},
    {"id": "listening", "label": "Listening (nghe)", "topics": ["listening"]},
    {"id": "speaking", "label": "Speaking (nói)", "topics": ["speaking", "greet"]},
    {"id": "reading", "label": "Reading (đọc)", "topics": ["reading"]},
    {"id": "writing", "label": "Writing (viết)", "topics": ["writing"]},
]

def _math_band(grade: int) -> str:
    g = int(grade or 5)
    if g <= 2:
        return "1-2"
    if g <= 5:
        return "3-5"
    if g <= 9:
        return "6-9"
    return "10-12"


def strands_for(subject: str, grade: int) -> list[dict[str, Any]]:
    sub = (subject or "toan").lower()
    if sub == "van":
        return list(VAN_STRANDS)
    if sub == "anh":
        return list(EN_STRANDS)
    return list(MATH_STRANDS.get(_math_band(grade), MATH_STRANDS["3-5"]))


# ── Placement test ──────────────────────────────────────────────────────────


def xac_dinh_lop(ho_so: dict[str, Any] | None, hom_nay_gia: Any = None) -> dict[str, Any]:
    """Xác định lớp của một hồ sơ: trả {"lop": 0..12, "nguon": ...}.

    hom_nay_gia: "YYYY-MM-DD" hoặc datetime.date (chỉ phần test/cấu hình mới
    truyền; mặc định hôm nay). Không ghi hồ sơ, không lưu năm sinh ra ngoài.
    """
    from datetime import date as _date

    hom_nay = None
    if hom_nay_gia:
        if isinstance(hom_nay_gia, str):
            nam, thang, ngay = (int(x) for x in hom_nay_gia.split("-"))
            hom_nay = _date(nam, thang, ngay)
        else:
            hom_nay = hom_nay_gia
    ho_so = ho_so or {}
    profile = {}
    if ho_so.get("nam_sinh") is not None:
        profile["birth_year"] = ho_so["nam_sinh"]
    if ho_so.get("lop") is not None:
        profile["grade"] = ho_so["lop"]
    kq = resolve_grade(profile, today=hom_nay)
    nguon = {"override": "khai tay", "birth_year": "năm sinh"}.get(
        kq.get("grade_source", ""), str(kq.get("grade_source", "")))
    return {"lop": int(kq.get("grade") or 0), "nguon": nguon}


mach_kien_thuc = strands_for
