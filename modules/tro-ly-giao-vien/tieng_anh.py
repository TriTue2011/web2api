# Bài tập tiếng Anh theo cấp (VN phổ thông) — sinh câu từ mẫu lập trình sẵn, KHÔNG gọi mô hình.
# NGUỒN: c2a (~/workspace/c2a-nv146/services/agent/teacher_english.py), sao chép nguyên văn theo LUẬT PORT
# (commit d879b03). Không đổi gì. Tệp là thư viện chuẩn, không gọi mạng, không I/O.

"""Tiếng Anh toàn diện: curriculum theo cấp + sinh bài tập 4 kỹ năng.

Khung gọn (VN phổ thông):
  1–2  Primary starter: greetings, numbers, colors, animals, classroom
  3–5  Primary: present simple, family, daily routines, past intro
  6–9  THCS: tenses, conditionals intro, passive, reading, writing
  10–12 THPT: advanced tenses, exam skills, opinion writing
"""
from __future__ import annotations

import random
import time
from typing import Any


def _seed(*parts: Any) -> int:
    s = "|".join(str(p) for p in parts) + f"|{time.time_ns()}"
    return abs(hash(s)) % (2**31)


def band_of_grade(grade: int) -> str:
    g = int(grade or 1)
    if g <= 2:
        return "p12"
    if g <= 5:
        return "p35"
    if g <= 9:
        return "thcs"
    return "thpt"


# ── Curriculum map (topic keywords → skill focus) ───────────────────────────

TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "greet": ("chào", "chao", "hello", "hi ", "name", "how are", "greet", "hỏi tên", "nice to meet"),
    "number": ("số", "number", "count", "đếm", "dem", "eleven", "twenty", "one to"),
    "animal": ("động vật", "dong vat", "animal", "cat", "dog", "bird", "fish", "pet"),
    "color": ("màu", "mau", "color", "colour", "red", "blue", "yellow"),
    "classroom": ("lớp", "classroom", "stand", "sit", "open", "mệnh lệnh", "command", "book"),
    "family": ("gia đình", "family", "mother", "father", "brother", "sister"),
    "food": ("đồ ăn", "food", "eat", "fruit", "apple", "rice", "hungry"),
    "daily": ("thói quen", "daily", "routine", "every day", "get up", "school day"),
    "present": ("hiện tại", "present simple", "present", "he she", "does", "simple present"),
    "past": ("quá khứ", "past simple", "past", "yesterday", "last", "went", "did", "simple past"),
    "future": ("tương lai", "future", "will", "going to", "tomorrow"),
    "continuous": ("tiếp diễn", "continuous", "progressive", "ing", "now", "at the moment"),
    "perfect": ("hiện tại hoàn thành", "present perfect", "perfect", "have ever", "since", "for "),
    "compare": ("so sánh", "comparative", "superlative", "than", "the most", "compare"),
    "modal": ("modal", "can", "must", "should", "may", "might", "could"),
    "conditional": ("điều kiện", "conditional", "condition", "if ", "type 1", "type 2"),
    "passive": ("bị động", "passive", "is made", "was built"),
    "reported": ("tường thuật", "reported", "said that", "tell", "indirect"),
    "preposition": ("giới từ", "preposition", "in on at", "under"),
    "vocab": ("từ vựng", "vocab", "vocabulary", "word", "words"),
    "reading": ("đọc", "reading", "passage", "comprehension"),
    "writing": ("viết", "writing", "paragraph", "essay", "opinion"),
    "listening": ("nghe", "listening", "listen"),
    "speaking": ("nói", "speaking", "dialogue", "role play", "conversation"),
    "grammar": ("ngữ pháp", "grammar", "tense", "cấu trúc"),
    "exam": ("thi", "exam", "test", "đgnl", "toeic", "ielts"),
    "animals": ("animals",),  # alias id from UI chips
}


def detect_focus(topic: str, grade: int, i: int) -> str:
    t = (topic or "").lower().strip()
    # exact skill id from UI chips
    if t in _BUILDERS_KEYS():
        return t
    # alias map (animals → animal)
    alias = {"animals": "animal", "colors": "color", "numbers": "number",
             "greetings": "greet", "tenses": "grammar"}
    if t in alias:
        return alias[t]
    for focus, keys in TOPIC_ALIASES.items():
        if focus == t or any(k in t for k in keys):
            return focus if focus != "animals" else "animal"
    # default rotation by grade band — diverse skills across questions
    band = band_of_grade(grade)
    pools = {
        "p12": ("greet", "number", "animal", "color", "classroom", "vocab"),
        "p35": ("family", "daily", "present", "past", "food", "reading", "writing"),
        "thcs": ("present", "past", "future", "continuous", "compare", "modal",
                 "reading", "writing", "vocab", "grammar"),
        "thpt": ("perfect", "conditional", "passive", "reported", "reading",
                 "writing", "exam", "grammar"),
    }
    pool = pools.get(band, pools["p35"])
    return pool[i % len(pool)]


def _BUILDERS_KEYS() -> set[str]:
    return {
        "greet", "number", "animal", "color", "classroom", "family", "food",
        "daily", "present", "past", "future", "continuous", "perfect",
        "compare", "modal", "conditional", "passive", "reported", "preposition",
        "vocab", "reading", "writing", "listening", "speaking", "grammar", "exam",
    }


# ── Item builders ───────────────────────────────────────────────────────────

def make_english_item(
    *,
    grade: int,
    topic: str,
    difficulty: str,
    i: int,
) -> dict[str, Any]:
    """Một câu bài tập Anh cụ thể + answer_hint."""
    rnd = random.Random(_seed("en", grade, topic, difficulty, i))
    diff = (difficulty or "medium").strip().lower()
    if diff not in {"easy", "medium", "hard"}:
        diff = "medium"
    g = max(1, min(12, int(grade or 5)))
    focus = detect_focus(topic, g, i)
    builder = _BUILDERS.get(focus) or _item_vocab
    item = builder(rnd, g, diff, i, topic)
    item.setdefault("type", "short")
    item.setdefault("source", f"en:{focus}")
    item["skill"] = focus
    item["cefr_hint"] = _cefr_for_grade(g)
    return item


def _cefr_for_grade(grade: int) -> str:
    if grade <= 2:
        return "Pre-A1"
    if grade <= 5:
        return "A1"
    if grade <= 7:
        return "A2"
    if grade <= 9:
        return "A2–B1"
    if grade <= 11:
        return "B1"
    return "B1–B2"


def english_skill_map(grade: int = 5) -> dict[str, Any]:
    """Public: danh sách skill/topic gợi ý UI."""
    g = int(grade or 5)
    band = band_of_grade(g)
    catalog = {
        "p12": [
            ("greet", "Chào hỏi / Asking name"),
            ("number", "Numbers 1–20"),
            ("animal", "Animals"),
            ("color", "Colors & objects"),
            ("classroom", "Classroom English"),
            ("vocab", "Picture vocabulary"),
        ],
        "p35": [
            ("family", "Family members"),
            ("daily", "Daily routines"),
            ("present", "Present simple"),
            ("past", "Past simple (intro)"),
            ("food", "Food & likes"),
            ("reading", "Short reading"),
            ("writing", "Write about yourself"),
            ("speaking", "Mini dialogue"),
        ],
        "thcs": [
            ("present", "Present simple / continuous"),
            ("past", "Past simple"),
            ("future", "Will / going to"),
            ("compare", "Comparatives"),
            ("modal", "Can / must / should"),
            ("conditional", "Conditionals 0–1"),
            ("passive", "Passive (basic)"),
            ("reading", "Reading comprehension"),
            ("writing", "Opinion paragraph"),
            ("grammar", "Error correction"),
            ("vocab", "Word formation / collocation"),
        ],
        "thpt": [
            ("perfect", "Present perfect"),
            ("conditional", "Conditionals 1–2"),
            ("passive", "Passive voice"),
            ("reported", "Reported speech"),
            ("reading", "Exam reading"),
            ("writing", "Essay / email"),
            ("exam", "Exam skills"),
            ("grammar", "Advanced grammar"),
            ("vocab", "Academic vocabulary"),
        ],
    }
    return {
        "grade": g,
        "band": band,
        "cefr": _cefr_for_grade(g),
        "skills": [{"id": a, "label": b} for a, b in catalog.get(band, catalog["p35"])],
        "all_foci": sorted(TOPIC_ALIASES.keys()),
    }


def english_rubric_detailed() -> str:
    return (
        "**English rubric (0–10)**\n\n"
        "- **9–10 Excellent (B1+):** clear ideas, accurate grammar, good range, coherent.\n"
        "- **7–8 Good (A2–B1):** mostly clear; minor errors; task complete.\n"
        "- **5–6 Fair (A2):** understandable; limited range; several grammar slips.\n"
        "- **3–4 Weak (A1–A2):** hard to follow; incomplete task; many errors.\n"
        "- **0–2 Not yet:** little relevant English / off-task.\n\n"
        "Chấm riêng: **Accuracy** (ngữ pháp/chính tả) · **Range** (từ vựng) · "
        "**Task** (đúng yêu cầu) · **Coherence** (liên kết)."
    )


# ── Concrete items ──────────────────────────────────────────────────────────

def _mc(opts: list[str], correct: str, rnd: random.Random) -> list[str]:
    pool = list(dict.fromkeys(opts + [correct]))
    while len(pool) < 3:
        pool.append(correct + "x")
    chosen = rnd.sample(pool, k=min(3, len(pool)))
    if correct not in chosen:
        chosen[0] = correct
    rnd.shuffle(chosen)
    return chosen


def _item_greet(rnd, g, diff, i, topic) -> dict[str, Any]:
    pairs = [
        ("What is your name?", "My name is"),
        ("How are you?", "I am fine"),
        ("Nice to meet you.", "Nice to meet you"),
        ("Where are you from?", "I am from"),
        ("How old are you?", "I am"),
    ]
    q, hint = pairs[i % len(pairs)]
    if g <= 2 or diff == "easy":
        return {
            "prompt": (
                f"English · Speaking/Writing · Greetings ({_cefr_for_grade(g)})\n"
                f"A: {q}\n"
                f"B: … Write ONE full answer (start like «{hint}…»)."
            ),
            "answer_hint": hint,
            "type": "short",
        }
    return {
        "prompt": (
            f"English · Dialogue\n"
            f"Write a 3-line dialogue including: «{q}»\n"
            f"Use capital letters and full stops."
        ),
        "answer_hint": hint,
        "type": "short",
    }


def _item_number(rnd, g, diff, i, topic) -> dict[str, Any]:
    table = [
        (1, "one"), (2, "two"), (3, "three"), (4, "four"), (5, "five"),
        (6, "six"), (7, "seven"), (8, "eight"), (9, "nine"), (10, "ten"),
        (11, "eleven"), (12, "twelve"), (13, "thirteen"), (15, "fifteen"),
        (20, "twenty"), (21, "twenty-one"), (30, "thirty"), (100, "one hundred"),
    ]
    if g <= 2:
        table = table[:12]
    elif g <= 5:
        table = table[:16]
    n, word = table[i % len(table)]
    if i % 2 == 0:
        return {
            "prompt": f"English · Numbers\nWrite **{n}** in English words:",
            "answer_hint": word,
            "type": "short",
        }
    return {
        "prompt": f"English · Numbers\nFill: I have _____ books. ({n} → English word)",
        "answer_hint": word,
        "type": "short",
    }


def _item_animal(rnd, g, diff, i, topic) -> dict[str, Any]:
    animals = [
        ("cat", "meow", "It is a cat."),
        ("dog", "bark", "It is a dog."),
        ("bird", "fly", "It is a bird."),
        ("fish", "swim", "It is a fish."),
        ("chicken", "egg", "It is a chicken."),
        ("elephant", "trunk", "It is an elephant."),
        ("tiger", "stripe", "It is a tiger."),
    ]
    if g <= 3:
        animals = animals[:5]
    a, feat, sent = animals[i % len(animals)]
    opts = _mc([x[0] for x in animals], a, rnd)
    if diff == "hard" and g >= 4:
        return {
            "prompt": (
                f"English · Animals\n"
                f"Write 2 sentences about a **{a}**:\n"
                f"(1) what it is  (2) what it can do / one fact."
            ),
            "answer_hint": a,
            "type": "short",
        }
    return {
        "prompt": (
            f"English · Animals · Vocabulary\n"
            f"Choose: It is a/an _____.\n"
            f"A) {opts[0]}  B) {opts[1]}  C) {opts[2]}\n"
            f"→ Write the correct word only."
        ),
        "answer_hint": a,
        "type": "short",
    }


def _item_color(rnd, g, diff, i, topic) -> dict[str, Any]:
    colors = [
        ("red", "bag"), ("blue", "pen"), ("green", "book"),
        ("yellow", "bus"), ("black", "cat"), ("white", "board"),
        ("orange", "ball"), ("pink", "flower"),
    ]
    c, obj = colors[i % len(colors)]
    return {
        "prompt": (
            f"English · Colors\n"
            f"Complete and rewrite the full sentence:\n"
            f"I have a _____ {obj}.  (color = {c})"
        ),
        "answer_hint": c,
        "type": "short",
    }


def _item_classroom(rnd, g, diff, i, topic) -> dict[str, Any]:
    cmds = [
        ("Stand up.", "Stand up"),
        ("Sit down.", "Sit down"),
        ("Open your book.", "Open your book"),
        ("Close the door.", "Close the door"),
        ("Listen carefully.", "Listen carefully"),
        ("Raise your hand.", "Raise your hand"),
    ]
    cmd, hint = cmds[i % len(cmds)]
    if g >= 4 and diff != "easy":
        return {
            "prompt": (
                f"English · Classroom English\n"
                f"Translate into English (polite classroom): «{ _vi_for_cmd(cmd) }»\n"
                f"Write the English command."
            ),
            "answer_hint": hint,
            "type": "short",
        }
    return {
        "prompt": f"English · Classroom\nCopy correctly:\n«{cmd}»",
        "answer_hint": hint,
        "type": "short",
    }


def _vi_for_cmd(cmd: str) -> str:
    m = {
        "Stand up.": "Đứng dậy.",
        "Sit down.": "Ngồi xuống.",
        "Open your book.": "Mở sách ra.",
        "Close the door.": "Đóng cửa lại.",
        "Listen carefully.": "Hãy lắng nghe.",
        "Raise your hand.": "Giơ tay lên.",
    }
    return m.get(cmd, cmd)


def _item_family(rnd, g, diff, i, topic) -> dict[str, Any]:
    rows = [
        ("mother", "She is my mother."),
        ("father", "He is my father."),
        ("brother", "He is my brother."),
        ("sister", "She is my sister."),
        ("grandmother", "She is my grandmother."),
    ]
    w, sent = rows[i % len(rows)]
    return {
        "prompt": (
            f"English · Family\n"
            f"Fill: This is my _____.  (word: {w})\n"
            f"Then write one more sentence about him/her."
        ),
        "answer_hint": w,
        "type": "short",
    }


def _item_food(rnd, g, diff, i, topic) -> dict[str, Any]:
    foods = ["rice", "noodles", "apple", "banana", "chicken", "milk", "bread", "egg"]
    f = foods[i % len(foods)]
    return {
        "prompt": (
            f"English · Food\n"
            f"Write: I like _____. / I don't like _____.  (use: {f})\n"
            f"Write TWO full sentences."
        ),
        "answer_hint": f,
        "type": "short",
    }


def _item_daily(rnd, g, diff, i, topic) -> dict[str, Any]:
    verbs = [
        ("get up", "at 6 o'clock"),
        ("have breakfast", "at 6:30"),
        ("go to school", "at 7 o'clock"),
        ("do homework", "in the evening"),
        ("go to bed", "at 9 o'clock"),
    ]
    v, t = verbs[i % len(verbs)]
    return {
        "prompt": (
            f"English · Daily routines · Present simple\n"
            f"Make a sentence with «{v}» and «{t}».\n"
            f"Example pattern: I {v} {t}."
        ),
        "answer_hint": v,
        "type": "short",
    }


def _item_present(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("She _____ to school every day. (go)", "goes"),
        ("They _____ football on Sundays. (play)", "play"),
        ("He _____ not like spinach. (do)", "does"),
        ("_____ you live in Ha Noi? (Do)", "Do"),
        ("The sun _____ in the east. (rise)", "rises"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": (
            f"English · Grammar · Present simple\n"
            f"Fill the blank with the correct form:\n{stem}"
        ),
        "answer_hint": ans,
        "type": "short",
    }


def _item_past(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("go → yesterday I _____ to the park.", "went"),
        ("see → She _____ a movie last night.", "saw"),
        ("have → We _____ dinner at 7 p.m.", "had"),
        ("do → What _____ you do yesterday?", "did"),
        ("is → He _____ happy yesterday.", "was"),
        ("play → They _____ football last Sunday.", "played"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Grammar · Past simple\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_future(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("I _____ call you tomorrow. (will)", "will"),
        ("She is _____ visit her grandma. (going to)", "going to"),
        ("They _____ arrive at 8. (will)", "will"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Grammar · Future\nFill: {stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_continuous(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("She _____ (read) a book now.", "is reading"),
        ("They _____ (play) football at the moment.", "are playing"),
        ("I _____ (not/sleep) now.", "am not sleeping"),
        ("_____ you _____ (watch) TV now? (be/verb)", "Are watching"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Present continuous\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_perfect(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("I have _____ finished my homework. (just)", "just"),
        ("She has lived here _____ 2019. (since/for)", "since"),
        ("They have been friends _____ ten years. (since/for)", "for"),
        ("_____ you ever _____ to Da Nang? (Have / be)", "Have been"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Present perfect\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_compare(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("This book is _____ than that one. (interesting)", "more interesting"),
        ("He is the _____ student in class. (tall)", "tallest"),
        ("My house is _____ than yours. (big)", "bigger"),
        ("English is _____ as Math. (not / difficult) → not as … as", "not as difficult"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Comparatives / Superlatives\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_modal(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("You _____ wear a helmet. (must / can)", "must"),
        ("_____ I open the window? (May / Must)", "May"),
        ("You _____ eat more vegetables. (should)", "should"),
        ("She _____ swim very well. (can)", "can"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Modals\nChoose/fill: {stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_conditional(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("If it rains, we _____ at home. (stay)", "will stay"),
        ("If I _____ rich, I would travel. (be)", "were"),
        ("If you heat ice, it _____. (melt)", "melts"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Conditionals\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_passive(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("They build houses. → Houses _____ by them. (are built)", "are built"),
        ("Someone stole my bike. → My bike _____. (was stolen)", "was stolen"),
        ("People speak English here. → English _____ here. (is spoken)", "is spoken"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Passive voice\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_reported(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ('Tom said, "I am tired." → Tom said that he _____ tired. (was)', "was"),
        ('She said, "I like tea." → She said that she _____ tea. (liked)', "liked"),
        ('He said, "I will go." → He said he _____ go. (would)', "would"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Reported speech\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_preposition(rnd, g, diff, i, topic) -> dict[str, Any]:
    items = [
        ("The book is _____ the table. (on/in/at)", "on"),
        ("I get up _____ 6 a.m. (on/in/at)", "at"),
        ("Her birthday is _____ May. (on/in/at)", "in"),
        ("He lives _____ Ha Noi. (on/in/at)", "in"),
    ]
    stem, ans = items[i % len(items)]
    return {
        "prompt": f"English · Prepositions\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


def _item_vocab(rnd, g, diff, i, topic) -> dict[str, Any]:
    packs = {
        "p12": [("apple", "fruit"), ("school", "place"), ("happy", "feeling")],
        "p35": [("homework", "school work"), ("weather", "sun/rain"), ("hobby", "free time")],
        "thcs": [("environment", "nature around us"), ("opportunity", "a chance"),
                 ("improve", "make better")],
        "thpt": [("significant", "important"), ("analyze", "examine in detail"),
                 ("consequence", "result")],
    }
    band = band_of_grade(g)
    pairs = packs.get(band, packs["p35"])
    word, meaning = pairs[i % len(pairs)]
    opts = _mc([p[0] for p in pairs] + ["table", "window", "music"], word, rnd)
    return {
        "prompt": (
            f"English · Vocabulary ({_cefr_for_grade(g)})\n"
            f"Meaning: «{meaning}»\n"
            f"Choose the best word:\n"
            f"A) {opts[0]}  B) {opts[1]}  C) {opts[2]}\n"
            f"→ Write the word."
        ),
        "answer_hint": word,
        "type": "short",
    }


def _item_reading(rnd, g, diff, i, topic) -> dict[str, Any]:
    passages = [
        (
            "Lan has a small dog. Every morning she walks the dog in the park. "
            "The dog likes to run and play with a ball.",
            "dog",
            "What does the dog like to do?",
            "run",
        ),
        (
            "Minh goes to school by bus. Classes start at 7:00. "
            "He likes English and Math. After school he plays football.",
            "bus",
            "How does Minh go to school?",
            "bus",
        ),
        (
            "Plastic pollution is a big problem. Many animals eat plastic by mistake. "
            "We should reuse bags and bottles to protect the ocean.",
            "plastic",
            "What should we reuse?",
            "bags",
        ),
        (
            "Online learning helps students review lessons at home. "
            "However, too much screen time can hurt their eyes. "
            "A good plan is to take breaks every 30 minutes.",
            "screen",
            "How often should students take breaks?",
            "30",
        ),
    ]
    if g <= 3:
        passages = passages[:2]
    elif g <= 7:
        passages = passages[:3]
    text, key, q, ans = passages[i % len(passages)]
    return {
        "prompt": (
            f"English · Reading ({_cefr_for_grade(g)})\n"
            f"Read:\n«{text}»\n\n"
            f"Question: {q}\n"
            f"Answer in English (short)."
        ),
        "answer_hint": ans,
        "type": "short",
        "meta": {"key": key},
    }


def _item_writing(rnd, g, diff, i, topic) -> dict[str, Any]:
    if g <= 3:
        prompts = [
            ("Write 3 sentences about your favorite animal.", "animal"),
            ("Write 3 sentences about your school.", "school"),
            ("Write about your best friend (3 sentences).", "friend"),
        ]
    elif g <= 7:
        prompts = [
            ("Write a paragraph (5–6 sentences) about your daily routine.", "routine"),
            ("Write about a trip you remember (5 sentences). Use past tense.", "trip"),
            ("Write: advantages of reading books (5 sentences).", "reading"),
        ]
    else:
        prompts = [
            (
                "Write an opinion paragraph (8–10 sentences): "
                "«Students should / should not use phones in class.» "
                "Give 2 reasons + example.",
                "phones",
            ),
            (
                "Write an email (80–100 words) to a friend about your last holiday.",
                "holiday",
            ),
            (
                "Write a short essay: How can teenagers protect the environment?",
                "environment",
            ),
        ]
    prompt, hint = prompts[i % len(prompts)]
    return {
        "prompt": f"English · Writing ({_cefr_for_grade(g)})\n{prompt}",
        "answer_hint": hint,
        "type": "short",
    }


def _item_listening(rnd, g, diff, i, topic) -> dict[str, Any]:
    """Proxy listening: HS nghe TTS (nếu có) + trả lời — script ngắn."""
    scripts = [
        ("I have a red bag and a blue pen.", "red"),
        ("Tom gets up at six and goes to school at seven.", "six"),
        ("There are three cats under the table.", "three"),
        ("She will visit her grandmother tomorrow morning.", "grandmother"),
    ]
    script, ans = scripts[i % len(scripts)]
    return {
        "prompt": (
            f"English · Listening (read / play TTS)\n"
            f"Listen or read carefully:\n«{script}»\n\n"
            f"Write ONE key detail you heard (word/number from the sentence)."
        ),
        "answer_hint": ans,
        "type": "short",
        "tts_script": script,
        "source": "en:listening",
    }


def _item_speaking(rnd, g, diff, i, topic) -> dict[str, Any]:
    cues = [
        "Introduce yourself (name, age, school, hobby) in 4 sentences.",
        "Describe your bedroom in 4 sentences.",
        "Talk about your favorite subject and why (4 sentences).",
        "Role-play: order food at a restaurant (4–6 lines dialogue).",
    ]
    if g >= 8:
        cues.append("Give a 1-minute opinion: Is homework useful? (write what you would say).")
    cue = cues[i % len(cues)]
    return {
        "prompt": (
            f"English · Speaking → write what you would SAY\n"
            f"{cue}\n"
            f"(Full sentences, capitals, full stops.)"
        ),
        "answer_hint": "I",
        "type": "short",
        "source": "en:speaking",
    }


def _item_grammar(rnd, g, diff, i, topic) -> dict[str, Any]:
    errors = [
        ("She go to school every day.", "goes", "She goes to school every day."),
        ("I am play football now.", "playing", "I am playing football now."),
        ("He didn't went home.", "go", "He didn't go home."),
        ("There is many books on the table.", "are", "There are many books on the table."),
        ("I have saw that movie.", "seen", "I have seen that movie."),
    ]
    bad, hint, good = errors[i % len(errors)]
    return {
        "prompt": (
            f"English · Error correction\n"
            f"Find the mistake and rewrite the correct sentence:\n"
            f"«{bad}»"
        ),
        "answer_hint": hint,
        "type": "short",
        "meta": {"full": good},
    }


def _item_exam(rnd, g, diff, i, topic) -> dict[str, Any]:
    tasks = [
        (
            "Cloze: People should _____ more trees to reduce pollution. "
            "(plant / planting / planted)",
            "plant",
        ),
        (
            "Rewrite: «They built this bridge in 2010.» → Passiveive starts with «This bridge…»",
            "was built",
        ),
        (
            "Choose closest meaning: «I can manage.» ≈ I can _____ . (handle it / ignore it)",
            "handle",
        ),
    ]
    stem, ans = tasks[i % len(tasks)]
    return {
        "prompt": f"English · Exam skill\n{stem}",
        "answer_hint": ans,
        "type": "short",
    }


_BUILDERS = {
    "greet": _item_greet,
    "number": _item_number,
    "animal": _item_animal,
    "color": _item_color,
    "classroom": _item_classroom,
    "family": _item_family,
    "food": _item_food,
    "daily": _item_daily,
    "present": _item_present,
    "past": _item_past,
    "future": _item_future,
    "continuous": _item_continuous,
    "perfect": _item_perfect,
    "compare": _item_compare,
    "modal": _item_modal,
    "conditional": _item_conditional,
    "passive": _item_passive,
    "reported": _item_reported,
    "preposition": _item_preposition,
    "vocab": _item_vocab,
    "reading": _item_reading,
    "writing": _item_writing,
    "listening": _item_listening,
    "speaking": _item_speaking,
    "grammar": _item_grammar,
    "exam": _item_exam,
}
