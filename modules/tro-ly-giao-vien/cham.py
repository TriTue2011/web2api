# Kiểm tra · chấm bài · sửa lỗi — giáo viên đa cấp (đợt 1: chỉ phần thuần, KHÔNG gọi mô hình).
#
# NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher_assess.py), port theo LUẬT PORT.
# CHỈNH so với c2a (đợt này không gọi mô hình, không đọc đĩa):
# - `_english_item`/`format_rubric_help`: import teacher_english -> `from . import tieng_anh`.
# - `make_quiz`: import teacher_workspace -> `from . import mon_hoc`; BỎ ghi đĩa (lưu đề qua
#   `kho_de` của module: 0600, nguyên tử, cách ly theo danh tính).
# - `grade_answer`: BỎ tham số `use_llm` và bước `_llm_grade` (chấm tự luận bằng model tách đợt
#   riêng — ghi trong kiểm kê); chấm heuristic số/gợi ý/từ khoá giữ nguyên.
# - `_llm_grade`: KHÔNG port (gọi services.agent.runtime.call_model).
# - `grade_quiz`: nhận `quiz` (dict) thay vì `quiz_id` — tầng module đọc đề từ `kho_de`.
# - `load_quiz` (_ROOT đĩa cũ): KHÔNG port (thay bằng `kho_de`).
# - `progressive_hints`: BỎ nhánh tra KB SGK (cần RAG c2a); gợi ý bậc thang thuần giữ nguyên.
# - `_enrich_grade`: praise_for lấy từ `.giao_an` (port từ teacher.py).


"""Kiểm tra · chấm bài · sửa lỗi — giáo viên đa cấp.

- make_quiz: lấy gợi ý từ KB SGK + sinh đề theo mẫu (không cần LLM)
- grade_answer: chấm khách quan (số/lựa chọn) + chấm tự luận qua model (nếu có)
- explain_fix: gợi ý sửa lỗi dựa KB + rubric
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _extract_numbers(s: str) -> list[float]:
    out: list[float] = []
    for m in re.finditer(r"-?\d+(?:[.,]\d+)?", s or ""):
        try:
            out.append(float(m.group(0).replace(",", ".")))
        except ValueError:
            pass
    return out


def make_quiz(
    *,
    grade: int,
    subject: str,
    topic: str = "",
    n: int = 5,
    workspace_id: str = "",
    difficulty: str = "medium",
) -> dict[str, Any]:
    """Sinh đề làm được thật (phép tính / câu hỏi cụ thể), không dán đoạn SGK.

    Trả {quiz_id, grade, subject, questions:[{id, prompt, answer_hint, type}]}.
    """
    from . import mon_hoc as mon

    n = max(1, min(int(n or 5), 10))
    sub = mon.chuan_mon(subject) or "toan"
    g = int(grade) if int(grade) in mon.GRADES else 2
    q = (topic or "").strip() or SUBJECT_HINT.get(sub, "ôn tập")
    diff = (difficulty or "medium").strip().lower()
    if diff not in {"easy", "medium", "hard"}:
        diff = "medium"

    # Sinh bài tập cụ thể (ưu tiên) — KB chỉ dùng gợi chủ đề
    questions = _generate_practice_items(
        grade=g, subject=sub, topic=q, n=n, difficulty=diff,
    )

    quiz_id = uuid.uuid4().hex[:12]
    quiz = {
        "quiz_id": quiz_id,
        "grade": g,
        "subject": sub,
        "topic": q,
        "difficulty": diff,
        "workspace_id": workspace_id or f"lop{g}-{sub}",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "questions": questions,
    }
    # Đợt 1: không ghi đĩa ở đây — lưu đề qua kho_de của module (0600, theo danh tính).
    return quiz


def make_quiz_adaptive(
    *,
    grade: int,
    subject: str,
    topic: str = "",
    n: int = 5,
    workspace_id: str = "",
    difficulty: str = "medium",
) -> dict[str, Any]:
    """Alias rõ nghĩa cho lớp học adaptive."""
    return make_quiz(
        grade=grade, subject=subject, topic=topic, n=n,
        workspace_id=workspace_id, difficulty=difficulty,
    )


SUBJECT_HINT = {
    "toan": "cộng trừ nhân chia",
    "van": "đọc hiểu viết đoạn",
    "anh": "vocabulary sentences",
}


def _rng_seed(*parts: Any) -> int:
    s = "|".join(str(p) for p in parts) + f"|{time.time_ns()}"
    return abs(hash(s)) % (2**31)


def _generate_practice_items(
    *,
    grade: int,
    subject: str,
    topic: str,
    n: int,
    difficulty: str,
) -> list[dict[str, Any]]:
    """Sinh n câu làm được (có đáp án gợi ý khi chấm)."""
    topic_l = (topic or "").lower()
    out: list[dict[str, Any]] = []
    if subject == "toan":
        for i in range(n):
            item = _math_item(grade, topic_l, difficulty, i)
            item["id"] = f"q{i + 1}"
            item["difficulty"] = difficulty
            out.append(item)
        return out
    if subject == "anh":
        for i in range(n):
            item = _english_item(grade, topic_l, difficulty, i)
            item["id"] = f"q{i + 1}"
            item["difficulty"] = difficulty
            out.append(item)
        return out
    for i in range(n):
        item = _van_item(grade, topic_l, difficulty, i)
        item["id"] = f"q{i + 1}"
        item["difficulty"] = difficulty
        out.append(item)
    return out


def _math_item(grade: int, topic: str, difficulty: str, i: int) -> dict[str, Any]:
    """Bài toán cụ thể: Tính … / Điền số … — không dán lý thuyết SGK."""
    import random
    rnd = random.Random(_rng_seed(grade, topic, difficulty, i))

    # Phân loại chủ đề
    t = topic
    want_sub = any(k in t for k in ("trừ", "tru", "mượn", "muon", "subtract"))
    want_add = any(k in t for k in ("cộng", "cong", "add", "tổng"))
    want_mul = any(k in t for k in ("nhân", "nhan", "cửu chương", "cuu chuong", "mul", "×", "x "))
    want_div = any(k in t for k in ("chia", "div", "÷"))
    want_clock = any(k in t for k in ("đồng hồ", "dong ho", "giờ", "gio", "phút"))
    want_len = any(k in t for k in ("độ dài", "do dai", "mét", "met", "cm", "xăng"))

    if not any([want_sub, want_add, want_mul, want_div, want_clock, want_len]):
        # auto theo lớp
        if grade <= 2:
            want_sub = i % 2 == 0
            want_add = not want_sub
        elif grade <= 5:
            want_mul = i % 3 == 0
            want_div = i % 3 == 1
            want_add = i % 3 == 2
        else:
            want_mul = True

    def _range_for_diff(base_hi: int) -> tuple[int, int]:
        if difficulty == "easy":
            return 1, max(5, base_hi // 2)
        if difficulty == "hard":
            return max(5, base_hi // 3), base_hi
        return 2, base_hi

    # Trừ có mượn / trừ
    if want_sub:
        if grade <= 1:
            lo, hi = _range_for_diff(10)
            a = rnd.randint(lo + 1, hi)
            b = rnd.randint(1, a)
        elif grade <= 3 or "mượn" in t or "muon" in t:
            # buộc mượn hàng đơn vị khi medium/hard
            lo, hi = _range_for_diff(99 if grade <= 3 else 999)
            tens = rnd.randint(max(2, lo // 10), max(2, hi // 10))
            u_a = rnd.randint(0, 8)
            u_b = rnd.randint(u_a + 1, 9)  # mượn
            a = tens * 10 + u_a
            b = rnd.randint(1, tens - 1) * 10 + u_b
            if b >= a:
                b = (tens - 1) * 10 + u_b if tens > 1 else u_b
                if b >= a:
                    a, b = max(a, b + 1), min(a, b)
        else:
            lo, hi = _range_for_diff(100 if grade <= 5 else 1000)
            a = rnd.randint(lo + 1, hi)
            b = rnd.randint(1, a - 1)
        ans = a - b
        steps = "Viết phép tính thẳng cột nếu cần; nhớ mượn khi hàng đơn vị không đủ."
        if difficulty == "hard":
            prompt = f"Tính {a} − {b}. Viết đủ các bước (mượn nếu có) và kết quả."
        else:
            prompt = f"Tính: {a} − {b} = ?"
        return {
            "type": "calc",
            "prompt": prompt,
            "answer_hint": str(ans),
            "source": "math:subtract",
            "meta": {"a": a, "b": b, "op": "-", "steps": steps},
        }

    if want_add:
        if grade <= 1:
            lo, hi = _range_for_diff(10)
        elif grade <= 3:
            lo, hi = _range_for_diff(50 if difficulty == "easy" else 100)
        else:
            lo, hi = _range_for_diff(500 if difficulty != "hard" else 2000)
        a = rnd.randint(lo, hi)
        b = rnd.randint(lo, hi)
        if difficulty != "easy" and grade >= 2 and (a % 10) + (b % 10) < 10:
            # tạo có nhớ
            a = (a // 10) * 10 + rnd.randint(5, 9)
            b = (b // 10) * 10 + rnd.randint(5, 9)
        ans = a + b
        prompt = (
            f"Tính {a} + {b}. Nêu có nhớ hay không và kết quả."
            if difficulty == "hard" else
            f"Tính: {a} + {b} = ?"
        )
        return {
            "type": "calc",
            "prompt": prompt,
            "answer_hint": str(ans),
            "source": "math:add",
            "meta": {"a": a, "b": b, "op": "+"},
        }

    if want_mul:
        if grade <= 3 or "cửu" in t or "chuong" in t:
            tables = [2, 5, 10] if grade <= 2 else list(range(2, 10))
            if difficulty == "easy":
                tables = [2, 5]
            if difficulty == "hard" and grade >= 3:
                tables = list(range(2, 10))
            a = rnd.choice(tables)
            b = rnd.randint(1, 10)
        else:
            a = rnd.randint(2, 12 if difficulty != "hard" else 20)
            b = rnd.randint(2, 12 if difficulty != "hard" else 20)
        ans = a * b
        return {
            "type": "calc",
            "prompt": f"Tính: {a} × {b} = ?",
            "answer_hint": str(ans),
            "source": "math:mul",
            "meta": {"a": a, "b": b, "op": "*"},
        }

    if want_div:
        b = rnd.randint(2, 9)
        qv = rnd.randint(2, 10 if difficulty != "hard" else 15)
        a = b * qv
        return {
            "type": "calc",
            "prompt": f"Tính: {a} ÷ {b} = ?",
            "answer_hint": str(qv),
            "source": "math:div",
            "meta": {"a": a, "b": b, "op": "/"},
        }

    if want_clock:
        h = rnd.randint(1, 12)
        m = 0 if difficulty == "easy" else rnd.choice([0, 15, 30, 45])
        prompt = (
            f"Đồng hồ chỉ {h} giờ {m:02d} phút. Kim ngắn ở số nào? "
            f"(Trả lời số trên mặt đồng hồ, vd: {h})"
        )
        return {
            "type": "short",
            "prompt": prompt,
            "answer_hint": str(h),
            "source": "math:clock",
        }

    if want_len:
        cm = rnd.choice([50, 100, 150, 200])
        m = cm / 100
        if i % 2 == 0:
            return {
                "type": "calc",
                "prompt": f"{cm} cm = ? mét",
                "answer_hint": str(int(m) if m == int(m) else m),
                "source": "math:length",
            }
        return {
            "type": "calc",
            "prompt": f"{int(m) if m == int(m) else m} mét = ? cm",
            "answer_hint": str(cm),
            "source": "math:length",
        }

    # fallback cộng
    a, b = rnd.randint(2, 20), rnd.randint(2, 20)
    return {
        "type": "calc",
        "prompt": f"Tính: {a} + {b} = ?",
        "answer_hint": str(a + b),
        "source": "math:fallback",
    }


def _english_item(grade: int, topic: str, difficulty: str, i: int) -> dict[str, Any]:
    """Bài Anh toàn diện (4 kỹ năng + grammar theo cấp) — module teacher_english."""
    from . import tieng_anh as te
    return te.make_english_item(
        grade=grade, topic=topic, difficulty=difficulty, i=i,
    )


def _van_item(grade: int, topic: str, difficulty: str, i: int) -> dict[str, Any]:
    """Bài Văn/TV cụ thể: chính tả, dấu câu, sắp xếp, đọc hiểu, từ loại, viết."""
    import random
    rnd = random.Random(_rng_seed("van", grade, topic, difficulty, i))
    t = (topic or "").lower()

    want_chinh_ta = any(k in t for k in (
        "chính tả", "chinh ta", "hỏi", "ngã", "hoi", "nga", "ch/tr", "s/x",
    ))
    want_dau = any(k in t for k in (
        "dấu", "dau cau", "chấm", "phẩy", "phay", "cham hoi",
    ))
    want_doc = any(k in t for k in (
        "đọc", "doc hieu", "ý chính", "y chinh", "đoạn", "doan van",
    ))
    want_tu = any(k in t for k in (
        "từ loại", "tu loai", "danh từ", "động từ", "tính từ",
        "danh tu", "dong tu", "tinh tu",
    ))
    want_viet = any(k in t for k in (
        "viết", "viet", "kể", "ke ", "tả", "ta ", "đoạn văn",
    ))

    kind = i % 5
    if want_chinh_ta:
        kind = 0
    elif want_dau:
        kind = 1
    elif want_doc:
        kind = 2
    elif want_tu:
        kind = 3
    elif want_viet:
        kind = 4

    if kind == 0:
        pairs = [
            ("hỏi", "Em muốn hỏi cô một câu."),
            ("ngã", "Lá vàng ngã xuống sân."),
            ("ch", "Chị cho em mượn sách."),
            ("tr", "Trời nắng đẹp quá."),
        ]
        key, sample = pairs[i % len(pairs)]
        if key in ("hỏi", "ngã"):
            return {
                "type": "short",
                "prompt": (
                    f"Tiếng Việt · Chính tả (hỏi / ngã)\n"
                    f"Chọn câu đúng và **viết lại cả câu**:\n"
                    f"A) Em muốn hỏi cô một câu.\n"
                    f"B) Lá vàng ngã xuống sân.\n"
                    f"→ Câu dùng đúng chữ «{key}»."
                ),
                "answer_hint": key,
                "source": "van:chinhta",
            }
        return {
            "type": "short",
            "prompt": (
                f"Tiếng Việt · Chính tả (ch / tr)\n"
                f"Điền **ch** hoặc **tr**, viết lại cả câu:\n"
                f"…ị cho em mượn sách.\n"
                f"Mẫu đúng: «{sample}»"
            ),
            "answer_hint": "chị" if key == "ch" else "trời",
            "source": "van:chinhta-chtr",
        }

    if kind == 1:
        items = [
            ("Hôm nay trời đẹp", ".", "Hôm nay trời đẹp."),
            ("Em tên là gì", "?", "Em tên là gì?"),
            ("Bạn ơi lại đây", "!", "Bạn ơi lại đây!"),
        ]
        stem, mark, full = items[i % len(items)]
        return {
            "type": "short",
            "prompt": (
                f"Tiếng Việt · Dấu câu\n"
                f"Thêm dấu ( .  ?  ! ) vào cuối và viết lại **cả câu**:\n"
                f"«{stem}»"
            ),
            "answer_hint": mark,
            "source": "van:daucau",
            "meta": {"full": full},
        }

    if kind == 2:
        passages = [
            (
                "Lan có một chú mèo trắng. Mỗi sáng mèo nằm sưởi nắng trước cửa. "
                "Lan cho mèo ăn cá và vuốt ve nó.",
                "mèo",
                "Chú mèo của Lan",
            ),
            (
                "Sáng nay mưa to. Minh mang áo mưa và đi bộ đến trường. "
                "Trên đường, Minh giúp một bạn nhỏ nhặt hộp bút rơi.",
                "giúp bạn",
                "Minh giúp bạn trên đường mưa",
            ),
            (
                "Vườn nhà bà có cây ổi và cây xoài. Hè đến, quả chín vàng. "
                "Các cháu hái quả và rửa sạch trước khi ăn.",
                "hái quả",
                "Vườn quả nhà bà",
            ),
        ]
        text, key, title = passages[i % len(passages)]
        if difficulty == "easy" or grade <= 2:
            return {
                "type": "short",
                "prompt": (
                    f"Tiếng Việt · Đọc hiểu\n"
                    f"Đoạn văn:\n«{text}»\n\n"
                    f"Câu hỏi: Đoạn văn nói về điều gì? (viết 1 câu ngắn)"
                ),
                "answer_hint": key,
                "source": "van:doc-easy",
            }
        return {
            "type": "short",
            "prompt": (
                f"Tiếng Việt · Đọc hiểu\n"
                f"Đoạn văn:\n«{text}»\n\n"
                f"1) Đặt **tiêu đề** ngắn.\n"
                f"2) Viết **ý chính** 1–2 câu.\n"
                f"(Gợi ý tiêu đề gần: «{title}»)"
            ),
            "answer_hint": title,
            "source": "van:doc",
        }

    if kind == 3:
        items = [
            ("Bạn học chăm.", "học", "động từ"),
            ("Con mèo trắng nằm im.", "mèo", "danh từ"),
            ("Hoa hồng rất đẹp.", "đẹp", "tính từ"),
            ("Em viết bài cẩn thận.", "cẩn thận", "tính từ"),
        ]
        sent, word, loai = items[i % len(items)]
        return {
            "type": "short",
            "prompt": (
                f"Tiếng Việt · Từ loại\n"
                f"Trong câu: «{sent}»\n"
                f"Từ «{word}» thuộc loại từ nào?\n"
                f"(Trả lời đúng một: danh từ / động từ / tính từ)"
            ),
            "answer_hint": loai,
            "source": "van:tuloai",
        }

    # kind 4 — sắp xếp / viết đoạn
    if difficulty == "easy" or grade <= 2:
        scramble_sets = [
            (["em", "đi", "học", "sáng"], "Em đi học sáng."),
            (["mẹ", "nấu", "cơm", "ngon"], "Mẹ nấu cơm ngon."),
            (["bạn", "chơi", "nhảy", "dây"], "Bạn chơi nhảy dây."),
        ]
        words, full = scramble_sets[i % len(scramble_sets)]
        w = list(words)
        rnd.shuffle(w)
        return {
            "type": "short",
            "prompt": (
                f"Tiếng Việt · Đặt câu\n"
                f"Sắp xếp thành câu đúng (chữ hoa + dấu chấm):\n"
                f"{' / '.join(w)}\n"
                f"→ Viết câu hoàn chỉnh."
            ),
            "answer_hint": full.lower().rstrip("."),
            "source": "van:sapxep",
            "meta": {"full": full},
        }
    topics_write = [
        ("một bạn cùng lớp", 4 if difficulty != "hard" else 7),
        ("việc nhà em thường làm", 5 if difficulty != "hard" else 8),
        ("buổi sáng đến trường", 5 if difficulty != "hard" else 8),
        ("yêu thương ông bà", 6 if difficulty != "hard" else 8),
    ]
    chu_de, n_cau = topics_write[i % len(topics_write)]
    if grade >= 6 or difficulty == "hard":
        return {
            "type": "short",
            "prompt": (
                f"Ngữ văn · Viết đoạn\n"
                f"Viết đoạn {n_cau}–{n_cau + 2} câu về «{chu_de}».\n"
                f"Có: câu mở · 2–3 ý thân · câu kết. Dấu chấm đầy đủ."
            ),
            "answer_hint": chu_de,
            "source": "van:doan",
        }
    return {
        "type": "short",
        "prompt": (
            f"Tiếng Việt · Viết\n"
            f"Viết {n_cau} câu về «{chu_de}».\n"
            f"Mỗi câu có chữ hoa đầu câu và dấu chấm cuối câu."
        ),
        "answer_hint": chu_de,
        "source": "van:viet",
    }


def _hint_from_body(body: str) -> str:
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    return " ".join(lines[:3])


def _fallback_prompt(
    subject: str, grade: int, i: int, *, difficulty: str = "medium",
) -> str:
    """Legacy — make_quiz không còn dùng; giữ cho test cũ nếu import."""
    item = _generate_practice_items(
        grade=grade, subject=subject, topic="", n=i + 1, difficulty=difficulty,
    )[-1]
    return str(item.get("prompt") or "")


# Rubric band Văn / Anh (và Toán gọn)
_RUBRIC_VAN = [
    (9, "Xuất sắc", "Đủ ý, mạch lạc, từ ngữ phù hợp; mở–thân–kết rõ."),
    (7, "Khá", "Có ý chính, còn thiếu chi tiết hoặc liên kết câu."),
    (5, "Trung bình", "Đúng hướng nhưng ý mỏng / lặp / lệch đề một phần."),
    (3, "Yếu", "Thiếu ý chính hoặc diễn đạt khó hiểu."),
    (0, "Chưa đạt", "Không bám đề hoặc gần như không có nội dung."),
]
_RUBRIC_ANH = [
    (9, "Excellent", "Task complete; accurate grammar; good range; coherent (≈B1+)."),
    (7, "Good", "Task mostly complete; minor slips; clear enough (≈A2–B1)."),
    (5, "Fair", "Understandable; limited range; several errors (≈A2)."),
    (3, "Weak", "Hard to follow; incomplete; many errors (≈A1–A2)."),
    (0, "Not yet", "Little relevant English / off-task."),
]
_RUBRIC_TOAN = [
    (9, "Xuất sắc", "Kết quả đúng, trình bày bước rõ."),
    (7, "Khá", "Hướng đúng; có thể thiếu bước hoặc sai sót nhỏ."),
    (5, "Trung bình", "Có phần đúng nhưng lệch kết quả / thiếu bước."),
    (3, "Yếu", "Lệch hướng giải hoặc sai kiến thức nền."),
    (0, "Chưa đạt", "Không có lời giải hợp lệ."),
]


def rubric_band(score_0_10: int, *, subject: str = "toan") -> dict[str, Any]:
    """Band điểm chi tiết cho PH/GV (Văn/Anh ưu tiên)."""
    s = max(0, min(10, int(score_0_10)))
    table = _RUBRIC_TOAN
    if subject == "van":
        table = _RUBRIC_VAN
    elif subject == "anh":
        table = _RUBRIC_ANH
    for thr, label, desc in table:
        if s >= thr:
            return {
                "score_0_10": s,
                "band": label,
                "description": desc,
                "subject": subject,
            }
    return {"score_0_10": s, "band": "Chưa đạt", "description": "", "subject": subject}


def format_rubric_help(subject: str = "van") -> str:
    if subject == "anh":
        try:
            from . import tieng_anh as te
            return te.english_rubric_detailed()
        except Exception:
            pass
    table = _RUBRIC_VAN if subject == "van" else (
        _RUBRIC_ANH if subject == "anh" else _RUBRIC_TOAN
    )
    lines = [f"**Rubric chấm {subject}** (thang 0–10):", ""]
    for thr, label, desc in table:
        lines.append(f"- **{thr}+ · {label}:** {desc}")
    return "\n".join(lines)



def _enrich_grade(
    result: dict[str, Any],
    *,
    grade: int,
    subject: str,
    question: str,
    answer_hint: str,
) -> dict[str, Any]:
    """Bổ sung praise / next_step / misconception — phong cách GV trên lớp."""
    from . import giao_an as ga

    score = int(result.get("score_0_10") or 0)
    result["praise"] = ga.praise_for(score, grade=grade)
    result["band"] = rubric_band(score, subject=subject)
    if result.get("correct") or score >= 7:
        result.setdefault("next_step", "Thử 1 bài tương tự khó hơn một chút, hoặc tóm tắt lại cách làm.")
        result.setdefault("misconception", "")
    else:
        result.setdefault(
            "next_step",
            "Dùng teacher_hint level=1 (rồi 2–3 nếu cần), làm lại từng bước.",
        )
        if not result.get("misconception"):
            # Heuristic nhẹ
            if subject == "toan" and _extract_numbers(answer_hint) and _extract_numbers(
                str(result.get("student_answer") or "")
            ):
                result["misconception"] = "Có thể lệch kết quả số / quên bước trung gian."
            else:
                result["misconception"] = "Chưa khớp yêu cầu đề hoặc thiếu ý chính."
    result.setdefault("subject", subject)
    result.setdefault("grade", grade)
    result.setdefault("question_preview", (question or "")[:120])
    return result


def grade_answer(
    *,
    question: str,
    student_answer: str,
    answer_hint: str = "",
    subject: str = "toan",
    grade: int = 5,
) -> dict[str, Any]:
    """Chấm 1 câu. Trả score, feedback, fixes, praise, next_step, misconception."""
    q = (question or "").strip()
    ans = (student_answer or "").strip()
    hint = (answer_hint or "").strip()
    if not ans:
        return _enrich_grade(
            {
                "score_0_10": 0,
                "correct": False,
                "feedback": "Chưa có bài làm.",
                "fixes": ["Hãy viết lại câu trả lời / phép tính."],
                "student_answer": ans,
            },
            grade=grade, subject=subject, question=q, answer_hint=hint,
        )

    # 1) Chấm số (toán): nếu gợi ý và bài có số khớp
    if subject == "toan" or _extract_numbers(hint):
        hn = _extract_numbers(hint)
        an = _extract_numbers(ans)
        if hn and an and abs(hn[-1] - an[-1]) < 1e-6:
            return _enrich_grade(
                {
                    "score_0_10": 10,
                    "correct": True,
                    "feedback": "Kết quả đúng.",
                    "fixes": [],
                    "student_answer": ans,
                },
                grade=grade, subject=subject, question=q, answer_hint=hint,
            )
        if hn and an and abs(hn[-1] - an[-1]) < 1e-3 * max(1, abs(hn[-1])):
            return _enrich_grade(
                {
                    "score_0_10": 9,
                    "correct": True,
                    "feedback": "Gần đúng / làm tròn chấp nhận được.",
                    "fixes": [],
                    "student_answer": ans,
                },
                grade=grade, subject=subject, question=q, answer_hint=hint,
            )

    # 2) Khớp chuỗi gợi ý (ngắn)
    if hint and _norm(hint) in _norm(ans):
        return _enrich_grade(
            {
                "score_0_10": 8,
                "correct": True,
                "feedback": "Bài có đủ ý chính theo gợi ý.",
                "fixes": [],
                "student_answer": ans,
            },
            grade=grade, subject=subject, question=q, answer_hint=hint,
        )

    # 3) KHÔNG gọi mô hình ở đợt 1 (tách đợt «chấm tự luận bằng model» — ghi trong kiểm kê).
    # 4) Heuristic độ dài + từ khóa
    keys = set(_norm(hint).split()) | set(_norm(q).split())
    keys = {k for k in keys if len(k) > 2}
    hit = sum(1 for k in keys if k in _norm(ans))
    ratio = hit / max(1, min(8, len(keys)))
    score = int(round(min(7, ratio * 10)))
    return _enrich_grade(
        {
            "score_0_10": score,
            "correct": score >= 7,
            "feedback": (
                "Bài đã có một phần ý; cần bổ sung rõ ràng hơn."
                if score >= 4 else
                "Bài còn thiếu ý hoặc lệch yêu cầu."
            ),
            "fixes": [
                "Đọc lại đề, gạch chân yêu cầu.",
                "Viết đủ bước / ý (mở – thân – kết nếu là văn).",
                f"Tham khảo gợi ý: {hint[:200]}" if hint else "Ôn lại bài trong SGK (search_sgk).",
            ],
            "student_answer": ans,
        },
        grade=grade, subject=subject, question=q, answer_hint=hint,
    )



def grade_quiz(
    quiz: dict[str, Any],
    answers: dict[str, str],
) -> dict[str, Any]:
    """Chấm cả đề. quiz là dict từ make_quiz (đợt 1: không đọc đĩa).
    answers: {q1: "...", q2: "..."}."""
    if not quiz:
        return {"ok": False, "error": "Không thấy đề để chấm"}
    quiz_id = str(quiz.get("quiz_id") or "")
    details = []
    total = 0
    for q in quiz.get("questions") or []:
        qid = str(q.get("id"))
        r = grade_answer(
            question=str(q.get("prompt") or ""),
            student_answer=str(answers.get(qid) or answers.get(qid.upper()) or ""),
            answer_hint=str(q.get("answer_hint") or ""),
            subject=str(quiz.get("subject") or "toan"),
            grade=int(quiz.get("grade") or 5),
        )
        r["id"] = qid
        details.append(r)
        total += int(r.get("score_0_10") or 0)
    n = max(1, len(details))
    avg = round(total / n, 1)
    return {
        "ok": True,
        "quiz_id": quiz_id,
        "average_0_10": avg,
        "percent": int(round(avg * 10)),
        "details": details,
        "summary": _summary_vi(avg, details),
    }


def _summary_vi(avg: float, details: list[dict]) -> str:
    weak = [d["id"] for d in details if int(d.get("score_0_10") or 0) < 5]
    lines = [
        f"Điểm trung bình: {avg}/10 ({int(round(avg * 10))}%).",
    ]
    if weak:
        lines.append("Cần ôn lại: " + ", ".join(weak) + ".")
    else:
        lines.append("Làm khá đều các câu.")
    lines.append("Xem feedback từng câu để sửa lỗi cụ thể.")
    return " ".join(lines)


def format_quiz_for_student(quiz: dict[str, Any]) -> str:
    lines = [
        f"**Đề kiểm tra** `{quiz.get('quiz_id')}` · "
        f"Lớp {quiz.get('grade')} · {quiz.get('subject')} · chủ đề: {quiz.get('topic')}",
        "",
    ]
    for q in quiz.get("questions") or []:
        lines.append(f"**{q.get('id')}.** {q.get('prompt')}")
        lines.append("")
    lines.append(
        "_Nộp bài: gửi teacher_grade với quiz_id và answers "
        '(vd: {"q1":"...","q2":"..."}) hoặc chấm từng câu._'
    )
    return "\n".join(lines)


def progressive_hints(
    *,
    question: str,
    student_attempt: str = "",
    answer_hint: str = "",
    level: int = 1,
    subject: str = "toan",
    grade: int = 5,
) -> dict[str, Any]:
    """Gợi ý bậc thang 1→2→3 (Socratic scaffold) — không đập đáp án ở level 1–2.

    level 1: định hướng / hỏi lại yêu cầu
    level 2: gợi ý bước / công thức / ý chính (che bớt đáp án)
    level 3: gần đáp án + vẫn yêu cầu HS tự kết
    """
    lv = max(1, min(3, int(level or 1)))
    q = (question or "").strip()
    attempt = (student_attempt or "").strip()
    hint = (answer_hint or "").strip()
    # Đợt 1: không tra KB SGK (cần RAG c2a — tách đợt riêng theo kiểm kê).
    body_kb = ""
    sub = subject
    g = grade

    hints: list[str] = []
    if lv >= 1:
        hints.append(
            "Đọc lại đề: gạch chân **yêu cầu** (tính gì / giải thích gì / viết gì). "
            "Con/em đang cần tìm gì trước?"
        )
        if attempt:
            hints.append(
                f"Bài làm hiện tại có hướng: «{attempt[:120]}…» — "
                "hãy nói rõ bước đầu tiên con/em đã làm."
                if len(attempt) > 120 else
                f"Bài làm hiện tại: «{attempt}». Bước đầu tiên là gì?"
            )
        else:
            hints.append("Chưa có bài làm — thử viết 1 bước đầu (không cần full đáp án).")
    if lv >= 2:
        if subject == "toan" or sub == "toan":
            hints.append(
                "Gợi ý bước: xác định dữ kiện → chọn phép/công thức → tính từng phần → đối chiếu đơn vị."
            )
        elif subject == "anh" or sub == "anh":
            hints.append(
                "Hint: identify tense/keywords in the prompt; write a simple sentence first."
            )
        else:
            hints.append(
                "Gợi ý ý: nêu luận điểm 1 câu → 1–2 dẫn chứng / chi tiết → câu kết."
            )
        if body_kb:
            # Che số cuối trong gợi ý KB nếu có
            soft = re.sub(r"\b\d+(?:[.,]\d+)?\b", "…", body_kb[:280])
            hints.append(f"Gợi ý từ bài (đã che bớt số): {soft}")
        elif hint:
            soft = re.sub(r"\b\d+(?:[.,]\d+)?\b", "…", hint[:200])
            hints.append(f"Manh mối (che số): {soft}")
    if lv >= 3:
        if hint:
            hints.append(
                f"Gần đáp án — đối chiếu với: {hint[:240]}. "
                "Hãy viết lại lời giải của mình (không chép nguyên)."
            )
        else:
            hints.append(
                "Bước cuối: hoàn thiện phép tính/đoạn văn; đọc to 1 lần để tự kiểm."
            )
        hints.append(
            "Sau khi làm xong, nộp để **teacher_grade** chấm + nhận sửa lỗi cụ thể."
        )

    return {
        "level": lv,
        "max_level": 3,
        "question": q[:400],
        "hints": hints,
        "spoiler": lv >= 3,
        "text": _format_hints(lv, hints),
    }


def _format_hints(level: int, hints: list[str]) -> str:
    title = {
        1: "Gợi ý nhẹ (level 1/3) — tự suy nghĩ trước",
        2: "Gợi ý bước (level 2/3) — vẫn chưa phải đáp án",
        3: "Gợi ý mạnh (level 3/3) — gần đáp án, hãy tự viết lại",
    }.get(level, f"Gợi ý level {level}")
    lines = [f"**{title}**", ""]
    for i, h in enumerate(hints, 1):
        lines.append(f"{i}. {h}")
    if level < 3:
        lines.append("")
        lines.append(f"_Vẫn kẹt? Gọi teacher_hint level={level + 1}._")
    return "\n".join(lines)


def make_check(
    *,
    grade: int,
    subject: str,
    topic: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """1 câu kiểm tra hiểu (exit ticket / CFU) — không full quiz."""
    quiz = make_quiz(
        grade=grade, subject=subject, topic=topic or "kiểm tra hiểu",
        n=1, workspace_id=workspace_id,
    )
    q = (quiz.get("questions") or [{}])[0]
    return {
        "check_id": quiz.get("quiz_id"),
        "grade": quiz.get("grade"),
        "subject": quiz.get("subject"),
        "topic": quiz.get("topic"),
        "question": q.get("prompt") or "",
        "answer_hint": q.get("answer_hint") or "",
        "text": (
            f"**Kiểm tra hiểu (1 câu)** · lớp {quiz.get('grade')} · "
            f"{quiz.get('subject')}\n\n"
            f"{q.get('prompt')}\n\n"
            f"_Trả lời xong → teacher_grade question=… answer=… "
            f"(hoặc quiz_id=`{quiz.get('quiz_id')}` answers={{\\\"q1\\\":\\\"…\\\"}})._"
        ),
    }


def format_grade_for_student(r: dict[str, Any]) -> str:
    """In phản hồi chấm kiểu giáo viên trên lớp."""
    lines = [
        f"**Chấm:** {r.get('score_0_10')}/10 "
        f"({'đạt' if r.get('correct') else 'cần sửa'})",
    ]
    if r.get("praise"):
        lines.append(str(r["praise"]))
    if r.get("feedback"):
        lines.append(str(r["feedback"]))
    if r.get("misconception"):
        lines.append(f"Có thể lệch ở: {r['misconception']}")
    for f in r.get("fixes") or []:
        lines.append(f"- Sửa: {f}")
    if r.get("next_step"):
        lines.append(f"**Bước tiếp:** {r['next_step']}")
    return "\n".join(lines)

# ── Bí danh tiếng Việt cho tầng hành động (tham số đặt lại tên, hành vi từ c2a) ──

def ra_de(*, lop: int = 5, mon: str = "toan", chu_de: str = "",
          so_cau: int = 5, do_kho: str = "medium") -> dict[str, Any]:
    """Ra một đề tập luyện (không lưu — lưu qua kho_de khi người dùng yêu cầu).

    Trả dạng c2a (`questions`) + dạng tiếng Việt (`cau_hoi` với `de_bai`,
    `goi_y_dap_an`) — cùng một danh sách, một nguồn sự thật.
    """
    quiz = make_quiz(grade=lop, subject=mon, topic=chu_de, n=so_cau, difficulty=do_kho)
    for q in quiz.get("questions") or []:
        q.setdefault("de_bai", q.get("prompt", ""))
        q.setdefault("goi_y_dap_an", q.get("answer_hint", ""))
    quiz["cau_hoi"] = quiz.get("questions") or []
    quiz["lop"] = quiz["grade"]
    quiz["mon"] = quiz["subject"]
    return quiz


def cham_mot_cau(cau_hoi: str, tra_loi_hs: str, goi_y_dap_an: str = "",
                 mon: str = "toan", lop: int = 5) -> dict[str, Any]:
    """Chấm một câu (thuần heuristic, không gọi mô hình).

    Bài làm của học sinh là dữ liệu không tin: chỉ dùng để đối chiếu đáp án,
    không log, không đưa vào prompt mô hình (đợt này không có bước mô hình).
    """
    kq = grade_answer(question=cau_hoi, student_answer=tra_loi_hs,
                      answer_hint=goi_y_dap_an, subject=mon, grade=lop)
    return {
        "ok": kq.get("ok", True),
        "diem": int(kq.get("score_0_10") or 0),
        "dung": bool(kq.get("correct")),
        "phan_hoi": kq.get("feedback") or "",
        "khen": kq.get("praise") or "",
        "loi_sai_thuong_gap": kq.get("misconception") or "",
        "buoc_sua": kq.get("fixes") or [],
        "buoc_tiep": kq.get("next_step") or "",
    }


def band_diem(diem_0_10: int, mon: str = "toan") -> dict[str, Any]:
    return rubric_band(diem_0_10, subject=mon)


def bang_rubric(mon: str = "toan") -> str:
    return format_rubric_help(mon)


def goi_y_bac_thang(cau_hoi: str, goi_y_dap_an: str = "", mon: str = "toan",
                    lop: int = 5, cap: int = 1) -> dict[str, Any]:
    """Gợi ý theo bậc (cấp càng cao càng lộ — gọi khi học sinh bí)."""
    h = progressive_hints(question=cau_hoi, answer_hint=goi_y_dap_an,
                          level=cap, subject=mon, grade=lop)
    return {"cap": h["level"], "goi_y": h["hints"], "text_goi_y": h["text"]}
