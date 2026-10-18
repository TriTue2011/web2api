"""Thuật toán khớp thiết bị nhanh và cổng an toàn cho Home Assistant (NV131).

Tách độc lập logic khớp chuỗi đa tín hiệu, cổng căn từ, biên mơ hồ,
kiểm tra khả thi theo cấu trúc và công cụ chẩn đoán giải thích khớp.
Không cần lượt gọi LLM đối với các lệnh điều khiển thiết bị rõ ràng.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from functools import lru_cache
from typing import Any, Callable, Sequence

# ── Trọng số kết hợp đa tín hiệu ──────────────────────────────
W_WORD = 0.33
W_CHAR = 0.32
W_BM25 = 0.20
W_INTENT = 0.15

DEFAULT_MIN_CONFIDENCE = 0.48
DEFAULT_MIN_MARGIN = 0.08
PREFILTER_TOP = 64

_BM25_K1 = 1.5
_BM25_B = 0.75

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

_FILLER = frozenset({
    "cho", "toi", "minh", "anh", "em", "giup", "gium", "dum",
    "di", "nhe", "luon", "the", "voi", "day", "nho", "ho", "cai",
    "bac", "chu", "co", "nay", "kia", "do", "duoc",
    "please", "can", "you", "me",
})

# Từ phủ định xét TRƯỚC khi bỏ dấu («đừng» ≠ «dừng»)
_NEGATION_WORDS_RAW = frozenset({
    "đừng", "không", "chớ", "chẳng", "chưa", "k", "ko", "khong", "chang", "chua",
    "not", "dont", "don't", "never",
})

_ACTION_VERBS_RAW = frozenset({
    "bật", "tắt", "mở", "đóng", "dừng", "ngừng", "khoá", "khép", "hạ",
    "phát", "chạy", "chỉnh", "tăng", "giảm", "đặt", "kích hoạt",
    "bat", "tat", "mo", "dong", "dung", "ngung", "khoa", "khep", "ha",
    "phat", "chay", "chinh", "tang", "giam", "dat", "kich_hoat",
    "turn", "switch", "enable", "disable", "start", "stop", "open", "close",
    "lock", "unlock", "play", "pause", "set", "adjust", "on", "off",
})

_NEGATION_MODIFIERS_RAW = frozenset({
    "được", "có", "nên", "vội", "cần", "phải", "thể", "hề", "nữa", "lại",
    "thèm", "muốn", "bao_giờ", "duoc", "co", "nen", "voi", "can", "phai",
    "the", "he", "nua", "lai", "them", "muon", "to", "be",
})


def _co_phu_dinh_hanh_dong(cau: str) -> bool:
    """Nhận diện cấu trúc phủ định đứng ngay trước (hoặc kèm từ đệm) động từ hành động.

    Xét trên chuỗi CÒN DẤU trước khi bỏ dấu/bỏ từ đệm («đừng» ≠ «dừng»).
    """
    if not cau:
        return False
    s = unicodedata.normalize("NFKC", str(cau)).casefold()
    tokens = re.findall(r"\w+|[^\w\s]", s)
    n = len(tokens)
    for i in range(n):
        tok = tokens[i]
        if tok in _NEGATION_WORDS_RAW:
            for j in range(i + 1, min(i + 4, n)):
                target = tokens[j]
                if target in _ACTION_VERBS_RAW:
                    giua = tokens[i + 1:j]
                    if all(t in _NEGATION_MODIFIERS_RAW or t in _NEGATION_WORDS_RAW for t in giua):
                        return True
    return False

_ON_WORDS = frozenset({"bat", "mo", "on", "enable", "chay", "start", "kich_hoat"})
_OFF_WORDS = frozenset({"tat", "dong", "off", "disable", "stop", "ngung", "dung"})
_OPEN_WORDS = frozenset({"mo", "open"})
_CLOSE_WORDS = frozenset({"dong", "close", "khep"})
_LOCK_WORDS = frozenset({"khoa", "lock"})
_UNLOCK_WORDS = frozenset({"mo_khoa", "unlock"})
_BRIGHT_WORDS = frozenset({"sang", "do_sang", "chinh_sang", "tang_sang", "giam_sang", "brightness"})
_TEMP_WORDS = frozenset({"nhiet_do", "chinh_nhiet", "dat_nhiet_do", "temperature", "nhiet"})
_VOLUME_WORDS = frozenset({"am_luong", "volume", "to", "nho"})
_MEDIA_PLAY_WORDS = frozenset({"phat", "play", "tiep_tuc"})
_MEDIA_PAUSE_WORDS = frozenset({"tam_dung", "pause"})

_ALL_ACTION_WORDS = (
    _ON_WORDS | _OFF_WORDS | _OPEN_WORDS | _CLOSE_WORDS |
    _LOCK_WORDS | _UNLOCK_WORDS | _BRIGHT_WORDS | _TEMP_WORDS |
    _VOLUME_WORDS | _MEDIA_PLAY_WORDS | _MEDIA_PAUSE_WORDS |
    {"turn", "on", "off"}
)

_OPPOSING_ACTIONS = {
    frozenset({"bat", "tat"}),
    frozenset({"mo", "dong"}),
    frozenset({"khoa", "mo_khoa"}),
    frozenset({"phat", "tam_dung"}),
    frozenset({"phat", "dung"}),
}

_ALIGN_THRESHOLDS = ((2, 0.75), (3, 0.66), (5, 0.60))
_ALIGN_BASE = 0.50


@lru_cache(maxsize=8192)
def normalize(text: str | None) -> str:
    """NFKC + thường hoá + bỏ dấu Latin (kể cả đ→d) + bỏ dấu câu + gộp khoảng trắng."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).casefold().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return _WS_RE.sub(" ", _NON_WORD_RE.sub(" ", s)).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in s.split() if t]


def _char_ngrams(s: str, n: int = 3) -> set[str]:
    s = s.replace(" ", "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i: i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _seq_ratio(a: str, b: str) -> float:
    """Tỉ lệ LCS: 2·LCS / (|a| + |b|)."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    dp = [0] * (lb + 1)
    for i in range(1, la + 1):
        prev = 0
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cur = dp[j]
            if ai == b[j - 1]:
                dp[j] = prev + 1
            elif dp[j - 1] > dp[j]:
                dp[j] = dp[j - 1]
            prev = cur
        # dọn dẹp hàng
    return (2.0 * dp[lb]) / (la + lb)


def _word_sim(q: str, c: str, qt: list[str], ct: list[str]) -> float:
    """LCS chuỗi + sort-tokens LCS + token-set overlap có phạt độ dài."""
    if not q or not c:
        return 0.0
    char_lcs = _seq_ratio(q, c)
    sort_lcs = _seq_ratio(" ".join(sorted(qt)), " ".join(sorted(ct)))
    qs, cs = set(qt), set(ct)
    overlap = len(qs & cs) / min(len(qs), len(cs)) if qs and cs else 0.0
    len_ratio = min(len(qt), len(ct)) / max(len(qt), len(ct)) if qt and ct else 0.0
    base_sim = (char_lcs + sort_lcs + overlap * len_ratio) / 3.0

    # Người dùng nói tắt: toàn bộ từ nội dung trong câu đều nằm trong nhãn ứng viên
    if qs and cs and qs.issubset(cs):
        subset_ratio = len(qs) / len(cs)
        bonus_score = 0.60 + 0.35 * subset_ratio
        return max(base_sim, bonus_score)

    return base_sim


def _bm25_scores(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """BM25 tính trên chính tập ứng viên, chuẩn hoá về [0, 1]."""
    n = len(docs)
    if not n or not query_tokens:
        return [0.0] * n
    lengths = [len(d) for d in docs]
    total = sum(lengths)
    if not total:
        return [0.0] * n
    avg = total / n
    df: dict[str, int] = {}
    for d in docs:
        for tok in set(d):
            df[tok] = df.get(tok, 0) + 1
    raw = [0.0] * n
    for tok in set(query_tokens):
        freq_docs = df.get(tok)
        if not freq_docs:
            continue
        idf = math.log(1 + (n - freq_docs + 0.5) / (freq_docs + 0.5))
        for i, d in enumerate(docs):
            tf = d.count(tok)
            if not tf:
                continue
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * lengths[i] / avg)
            raw[i] += idf * (_BM25_K1 + 1) * tf / denom
    top = max(raw)
    return [r / top for r in raw] if top > 0 else raw


def _intent_affinity(cau: str, hanh_dong: str, bo_qua_phu_dinh: bool = False) -> tuple[float, bool]:
    """Tính độ tương hợp hành động và phát hiện hành động đối nghịch.

    Trả về: (diem_tuong_hop, co_doi_nghich)
    """
    cau_xu_ly = cau
    if not bo_qua_phu_dinh and cau:
        # Gắn nhãn tách «đừng» (phủ định) khỏi «dừng» (động từ tắt/dừng) trước khi bỏ dấu
        cau_xu_ly = re.sub(r"\bđừng\b", "phu_dinh_dung", cau, flags=re.IGNORECASE)

    q_norm = normalize(cau_xu_ly)
    toks = set(q_norm.split())

    has_turn_on = "turn on" in q_norm
    has_turn_off = "turn off" in q_norm
    has_mo_khoa = "mo khoa" in q_norm or "unlock" in q_norm

    user_tat = (bool(toks & _OFF_WORDS) or has_turn_off) and not has_turn_on
    user_bat = (bool(toks & _ON_WORDS) or has_turn_on) and not user_tat
    user_mo = (bool(toks & _OPEN_WORDS) or "open" in toks) and not has_mo_khoa
    user_dong = (bool(toks & _CLOSE_WORDS) or "close" in toks)
    user_khoa = ("khoa" in toks or "lock" in toks) and not has_mo_khoa
    user_mo_khoa = has_mo_khoa

    user_chinh_sang = bool(toks & _BRIGHT_WORDS) or "sang" in toks
    user_nhiet_do = bool(toks & _TEMP_WORDS) or "nhiet" in toks or "lanh" in toks or "am" in toks
    user_am_luong = bool(toks & _VOLUME_WORDS) or "tieng" in toks

    if user_tat and hanh_dong == "bat":
        return 0.0, True
    if user_bat and hanh_dong == "tat":
        return 0.0, True
    if user_dong and hanh_dong == "mo":
        return 0.0, True
    if user_mo and hanh_dong == "dong":
        return 0.0, True
    if user_khoa and hanh_dong == "mo_khoa":
        return 0.0, True
    if user_mo_khoa and hanh_dong == "khoa":
        return 0.0, True

    if user_bat and hanh_dong in ("bat", "kich_hoat", "phat"):
        return 1.0, False
    if user_tat and hanh_dong in ("tat", "tam_dung", "dung", "tat_che_do"):
        return 1.0, False
    if user_mo and hanh_dong == "mo":
        return 1.0, False
    if user_dong and hanh_dong == "dong":
        return 1.0, False
    if user_khoa and hanh_dong == "khoa":
        return 1.0, False
    if user_mo_khoa and hanh_dong == "mo_khoa":
        return 1.0, False
    if user_chinh_sang and hanh_dong == "chinh_sang":
        return 1.0, False
    if user_nhiet_do and hanh_dong == "dat_nhiet_do":
        return 1.0, False
    if user_am_luong and hanh_dong == "chinh_am_luong":
        return 1.0, False

    return 0.6, False


def _tokens_aligned(query_tokens: Sequence[str], cand_tokens: Sequence[str]) -> bool:
    """Mỗi từ nội dung trong câu phải ghép 1-1 với một từ tương ứng của ứng viên."""
    if not query_tokens:
        return True
    if not cand_tokens:
        return False

    pairs = sorted(
        (_seq_ratio(q, c), qi, ci)
        for qi, q in enumerate(query_tokens)
        for ci, c in enumerate(cand_tokens)
    )
    matched: dict[int, float] = {}
    used_c: set[int] = set()
    for score, qi, ci in reversed(pairs):
        if qi in matched or ci in used_c:
            continue
        matched[qi] = score
        used_c.add(ci)

    for qi, qt in enumerate(query_tokens):
        limit = _ALIGN_BASE
        for max_len, threshold in _ALIGN_THRESHOLDS:
            if len(qt) <= max_len:
                limit = threshold
                break
        if matched.get(qi, 0.0) < limit:
            return False
    return True


class ChiMucUngVien:
    """Chỉ mục ứng viên lưu bền theo hash danh sách thực thể.

    Tuyệt đối không lưu câu nói của người dùng.
    """

    def __init__(self, ngon_ngu: str = "vi"):
        self.ngon_ngu = ngon_ngu
        self.hash_thuc_the: str = ""
        self.so_lan_dung_lai: int = 0
        self.danh_sach: list[dict] = []
        self.danh_sach_norm: list[tuple[str, list[str], set[str], dict]] = []

    def cap_nhat(self, danh_sach_thuc_the: list[dict]) -> str:
        danh_sach = danh_sach_thuc_the or []
        khoa_hash = []
        for e in danh_sach:
            eid = str(e.get("id") or "")
            ten = str(e.get("ten") or "")
            khu = str(e.get("khu") or "")
            khoa_hash.append(f"{eid}:{ten}:{khu}")
        khoa_hash.sort()
        moi_hash = hashlib.sha256(";".join(khoa_hash).encode("utf-8")).hexdigest()

        if moi_hash == self.hash_thuc_the and self.danh_sach_norm:
            self.so_lan_dung_lai += 1
            return self.hash_thuc_the

        self.hash_thuc_the = moi_hash
        self.danh_sach = danh_sach
        norm_list = []
        for e in danh_sach:
            ten = normalize(e.get("ten") or "")
            khu = normalize(e.get("khu") or "")
            eid = str(e.get("id") or "")
            eid_part = normalize(eid.split(".", 1)[-1].replace(".", " ").replace("_", " "))
            label = f"{ten} {khu} {eid_part}".strip()
            toks = _tokens(label)
            grams = _char_ngrams(label)
            norm_list.append((label, toks, grams, e))
        self.danh_sach_norm = norm_list
        return self.hash_thuc_the


class KetQuaKhop(list):
    """Danh sách kết quả khớp kèm các thuộc tính chẩn đoán và điều khiển."""

    def __init__(
        self,
        items: Sequence[dict] | None = None,
        *,
        chuyen_llm: bool = False,
        cau_cat: str = "",
        mo_ho: bool = False,
        ly_do: str = "",
    ):
        super().__init__(items or [])
        self.chuyen_llm = chuyen_llm
        self.cau_cat = cau_cat
        self.cau_da_cat = cau_cat
        self.mo_ho = mo_ho
        self.ly_do = ly_do

    def get(self, key: str, default: Any = None) -> Any:
        if key == "chuyen_llm":
            return self.chuyen_llm
        if key in ("cau_cat", "cau_da_cat", "cau"):
            return self.cau_cat
        if key == "mo_ho":
            return self.mo_ho
        if key == "ly_do":
            return self.ly_do
        if key == "danh_sach":
            return list(self)
        return default

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            return self.get(item)
        return super().__getitem__(item)



def kiem_kha_thi_ung_vien(
    entity: dict,
    hanh_dong: str,
    *,
    mien_cam: frozenset[str] | set[str],
    kha_nang_mien: dict[str, list[dict]],
    trang_thai_hong: frozenset[str] | set[str],
    nhom_duoc_dieu_khien: Sequence[str] | None = None,
) -> tuple[bool, str | None]:
    """Kiểm khả thi trước khi chạy theo NGUYÊN TẮC cấu trúc của module:

    Các tham số mien_cam, kha_nang_mien, trang_thai_hong là BẮT BUỘC (không mặc định mở).
    """
    loai = entity.get("loai") or ""
    if not loai:
        eid = str(entity.get("id") or "")
        if "." in eid:
            parts = eid.split(".")
            loai = parts[1] if len(parts) > 2 and parts[0] == "home_assistant" else parts[0]
            if loai not in kha_nang_mien and len(parts) > 1:
                loai = parts[0]

    if loai in mien_cam:
        return False, f"miền «{loai}» không bao giờ được điều khiển qua module này"

    if nhom_duoc_dieu_khien is not None and loai not in nhom_duoc_dieu_khien:
        return False, f"miền «{loai}» nằm ngoài nhóm được điều khiển"

    st = entity.get("trang_thai") or entity.get("state")
    if st is not None and str(st).strip().lower() in trang_thai_hong:
        return False, f"thiết bị đang ở trạng thái không khả dụng ({st})"

    if loai in kha_nang_mien:
        cac_hd_mien = {m["hanh_dong"] for m in kha_nang_mien.get(loai, [])}
        if hanh_dong not in cac_hd_mien:
            return False, f"hành động «{hanh_dong}» không được hỗ trợ cho miền «{loai}»"

    tinh_nang = entity.get("tinh_nang") or []
    ds_hd = [tn.get("hanh_dong") for tn in tinh_nang if isinstance(tn, dict)]
    if ds_hd and hanh_dong not in ds_hd:
        return False, f"hành động «{hanh_dong}» không được hỗ trợ trên thiết bị"

    return True, None


def _khop_hot_word_dau_cau(cau_raw: str, tu_khoa_goi: list[str]) -> tuple[bool, str, str]:
    """Khớp hot word ở ĐẦU câu theo ranh giới từ, cắt đúng vị trí trên chuỗi gốc.

    Trả về: (co_khop, tu_khoa_khop, cau_cat)
    """
    if not cau_raw or not tu_khoa_goi:
        return False, "", ""

    cau_trang = cau_raw.strip()
    matches = list(re.finditer(r"\w+", cau_trang))
    if not matches:
        return False, "", ""

    for kw in tu_khoa_goi:
        kw_str = str(kw).strip()
        if not kw_str:
            continue
        kw_tokens = normalize(kw_str).split()
        if not kw_tokens:
            continue
        k = len(kw_tokens)
        if len(matches) < k:
            continue

        dau_tokens = [normalize(matches[i].group(0)) for i in range(k)]
        if dau_tokens == kw_tokens:
            end_pos = matches[k - 1].end()
            cau_cat = cau_trang[end_pos:].strip().lstrip(",;:?!- ")
            return True, kw_str, cau_cat

    return False, "", ""


def khop_thiet_bi(
    cau: str,
    ung_vien: list[dict] | None = None,
    *,
    mien_cam: frozenset[str] | set[str],
    kha_nang_mien: dict[str, list[dict]],
    trang_thai_hong: frozenset[str] | set[str],
    tinh_rui_ro: Callable[[str, str | None], str],
    tu_khoa_goi: list[str] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    ngon_ngu: str = "vi",
    chi_muc: ChiMucUngVien | None = None,
    bo_qua_phu_dinh: bool = False,
    nhom_duoc_dieu_khien: Sequence[str] | None = None,
    module: Any = None,
) -> KetQuaKhop:
    """Khớp câu nói với thiết bị Home Assistant mà không cần lượt LLM."""
    if not cau or not cau.strip():
        return KetQuaKhop([], ly_do="Câu nói rỗng")

    # (a) Kiểm tra từ khoá gọi (hot word) ở ĐẦU câu theo ranh giới từ
    if tu_khoa_goi:
        co_hw, kw_khop, cau_cat = _khop_hot_word_dau_cau(cau, tu_khoa_goi)
        if co_hw:
            return KetQuaKhop(
                [],
                chuyen_llm=True,
                cau_cat=cau_cat,
                ly_do=f"Bắt đầu bằng từ khoá gọi «{kw_khop}»",
            )

    # (a2) Nhận diện phủ định bằng CẤU TRÚC (xét TRƯỚC khi bỏ dấu/bỏ từ đệm)
    if not bo_qua_phu_dinh and _co_phu_dinh_hanh_dong(cau):
        return KetQuaKhop(
            [],
            chuyen_llm=True,
            mo_ho=True,
            ly_do="Phát hiện phủ định trước động từ hành động, chuyển LLM xử lý",
        )

    q_full = normalize(cau)
    if not q_full:
        return KetQuaKhop([], ly_do="Câu không chứa ký tự hợp lệ")

    q_tokens = _tokens(q_full)

    content_tokens = [
        t for t in q_tokens
        if t not in _FILLER and t not in _ALL_ACTION_WORDS and len(t) > 1
    ]
    if not content_tokens:
        content_tokens = [t for t in q_tokens if t not in _FILLER and len(t) > 1]
    if not content_tokens:
        content_tokens = list(q_tokens)

    q_content = " ".join(content_tokens) if content_tokens else q_full

    danh_sach = ung_vien
    if danh_sach is None:
        if module is not None and hasattr(module, "danh_sach_thuc_the_hien_tai"):
            danh_sach = module.danh_sach_thuc_the_hien_tai()
        else:
            danh_sach = []

    if not danh_sach:
        return KetQuaKhop([], ly_do="Không có ứng viên nào")

    if chi_muc is None:
        chi_muc = ChiMucUngVien(ngon_ngu=ngon_ngu)
    chi_muc.cap_nhat(danh_sach)
    norm_list = chi_muc.danh_sach_norm

    q_grams = _char_ngrams(q_content)
    pool_indices = list(range(len(norm_list)))
    char_scores = [_jaccard(q_grams, norm_list[i][2]) for i in pool_indices]
    q_toks_set = set(content_tokens)
    prefilter_scores = [
        char_scores[i] + (len(q_toks_set & set(norm_list[i][1])) / len(q_toks_set) if q_toks_set else 0.0)
        for i in pool_indices
    ]
    if len(pool_indices) > PREFILTER_TOP:
        pool_indices.sort(key=lambda i: prefilter_scores[i], reverse=True)
        pool_indices = pool_indices[:PREFILTER_TOP]

    pool_docs = [norm_list[i][1] for i in pool_indices]
    bm25 = _bm25_scores(content_tokens, pool_docs)

    ung_vien_diem: list[dict] = []
    for slot, idx in enumerate(pool_indices):
        c_label, c_toks, c_grams, ent = norm_list[idx]
        eid = ent.get("id") or ""
        ten = ent.get("ten") or eid
        tinh_nang = ent.get("tinh_nang") or [{"hanh_dong": "bat"}]

        w_score = _word_sim(q_content, c_label, content_tokens, c_toks)
        ch_score = char_scores[idx]
        bm25_score = bm25[slot]
        can_tu_ok = _tokens_aligned(content_tokens, c_toks)

        for tn in tinh_nang:
            hd = tn.get("hanh_dong") if isinstance(tn, dict) else str(tn)
            intent_score, opposing = _intent_affinity(cau, hd, bo_qua_phu_dinh=bo_qua_phu_dinh)

            if opposing:
                continue

            # Điểm tương hợp hành động trung tính (ví dụ 0,6) KHÔNG đủ để vượt ngưỡng điều khiển
            if intent_score <= 0.6:
                continue

            diem_tong = (
                W_WORD * w_score +
                W_CHAR * ch_score +
                W_BM25 * bm25_score +
                W_INTENT * intent_score
            )

            ten_toks = _tokens(normalize(ten))
            khop_chinh_xac = (
                w_score >= 0.95 or
                (bool(ten_toks) and all(t in q_tokens for t in ten_toks))
            )

            loai_ent = ent.get("loai") or ""
            if not loai_ent:
                parts = eid.split(".")
                loai_ent = parts[1] if len(parts) > 2 and parts[0] == "home_assistant" else parts[0]
            dc = ent.get("device_class") or (ent.get("thuoc_tinh") or {}).get("device_class")
            rui_ro_ent = tinh_rui_ro(loai_ent, dc)

            ung_vien_diem.append({
                "entity_id": eid,
                "ten": ten,
                "hanh_dong": hd,
                "diem": round(diem_tong, 4),
                "diem_chuoi": round(w_score, 4),
                "diem_ngram": round(ch_score, 4),
                "diem_bm25": round(bm25_score, 4),
                "diem_hanh_dong": round(intent_score, 4),
                "qua_cong_can_tu": can_tu_ok,
                "khop_chinh_xac": khop_chinh_xac,
                "ent": ent,
                "rui_ro": rui_ro_ent,
                "ly_do": "Khớp tốt",
            })

    if not ung_vien_diem:
        return KetQuaKhop([], mo_ho=True, ly_do="Không có ứng viên nào phù hợp hành động")

    ung_vien_diem.sort(key=lambda x: x["diem"], reverse=True)

    hop_le = [uv for uv in ung_vien_diem if uv["qua_cong_can_tu"] and uv["diem"] >= min_confidence]
    if not hop_le:
        return KetQuaKhop([], ly_do="Không ứng viên nào vượt qua cổng căn từ và ngưỡng tin cậy")

    ds_phan_biet: list[dict] = []
    da_thay_eid: set[str] = set()
    for uv in hop_le:
        eid = uv["entity_id"]
        if eid not in da_thay_eid:
            da_thay_eid.add(eid)
            ds_phan_biet.append(uv)

    # (b) Kiểm khả thi trước khi chạy: thử tối đa 3 ứng viên, cho phép đúng 1 lần phục hồi
    top_3 = ds_phan_biet[:3]
    ung_vien_kha_thi: list[dict] = []
    so_lan_phuc_hoi = 0

    for idx_uv, uv in enumerate(top_3):
        ent = uv["ent"]
        hd = uv["hanh_dong"]
        if module is not None and hasattr(module, "kiem_kha_thi"):
            kha_thi, loi = module.kiem_kha_thi(ent.get("id") or "", hd)
        else:
            kha_thi, loi = kiem_kha_thi_ung_vien(
                ent,
                hd,
                mien_cam=mien_cam,
                kha_nang_mien=kha_nang_mien,
                trang_thai_hong=trang_thai_hong,
                nhom_duoc_dieu_khien=nhom_duoc_dieu_khien,
            )
        if kha_thi:
            uv_ban_sao = dict(uv)
            if so_lan_phuc_hoi > 0:
                uv_ban_sao["ly_do"] = "Đã phục hồi từ ứng viên trước (bị từ chối)"
            ung_vien_kha_thi.append(uv_ban_sao)
            if so_lan_phuc_hoi > 0:
                break
        else:
            if so_lan_phuc_hoi == 0:
                so_lan_phuc_hoi += 1
                continue
            else:
                break

    if not ung_vien_kha_thi:
        return KetQuaKhop([], ly_do="Các ứng viên hàng đầu đều không khả thi")

    # (c) Kiểm tra biên mơ hồ (margin) giữa các ứng viên khả thi
    is_mo_ho = False
    ly_do_mo_ho = ""
    if len(ung_vien_kha_thi) >= 2 and so_lan_phuc_hoi == 0:
        top1 = ung_vien_kha_thi[0]
        top2 = ung_vien_kha_thi[1]
        chenh_lech = top1["diem"] - top2["diem"]
        margin_ap_dung = min_margin * 0.5 if top1.get("khop_chinh_xac") else min_margin
        if chenh_lech < margin_ap_dung:
            is_mo_ho = True
            ly_do_mo_ho = (
                f"Mơ hồ: hai ứng viên điểm sát nhau (chênh {chenh_lech:.3f} < {margin_ap_dung:.3f})"
            )
            for uv in ung_vien_kha_thi:
                uv["mo_ho"] = True
                uv["ly_do"] = ly_do_mo_ho

    if is_mo_ho:
        ds_tra_ve = [
            {
                "entity_id": uv["entity_id"],
                "ten": uv["ten"],
                "hanh_dong": uv["hanh_dong"],
                "diem": uv["diem"],
                "rui_ro": uv["rui_ro"],
                "ly_do": uv["ly_do"],
                "mo_ho": True,
            }
            for uv in ung_vien_kha_thi[:3]
        ]
        return KetQuaKhop(
            ds_tra_ve,
            mo_ho=True,
            ly_do=ly_do_mo_ho,
        )

    chon = ung_vien_kha_thi[0]
    ds_tra_ve = [
        {
            "entity_id": chon["entity_id"],
            "ten": chon["ten"],
            "hanh_dong": chon["hanh_dong"],
            "diem": chon["diem"],
            "rui_ro": chon["rui_ro"],
            "ly_do": chon["ly_do"],
        }
    ]
    return KetQuaKhop(ds_tra_ve, ly_do=chon.get("ly_do") or "Khớp thành công")


def giai_thich_khop(
    cau: str,
    ung_vien: list[dict] | None = None,
    *,
    mien_cam: frozenset[str] | set[str],
    kha_nang_mien: dict[str, list[dict]],
    trang_thai_hong: frozenset[str] | set[str],
    tinh_rui_ro: Callable[[str, str | None], str],
    module: Any = None,
    ngon_ngu: str = "vi",
    chi_muc: ChiMucUngVien | None = None,
    nhom_duoc_dieu_khien: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Công cụ chẩn đoán: trả chi tiết điểm từng tín hiệu để tinh chỉnh."""
    q_norm = normalize(cau)
    q_tokens = _tokens(q_norm)
    content_tokens = [
        t for t in q_tokens
        if t not in _FILLER and t not in _ALL_ACTION_WORDS and len(t) > 1
    ]
    if not content_tokens:
        content_tokens = [t for t in q_tokens if t not in _FILLER and len(t) > 1]
    if not content_tokens:
        content_tokens = list(q_tokens)
    q_content = " ".join(content_tokens) if content_tokens else q_norm

    danh_sach = ung_vien or []
    if chi_muc is None:
        chi_muc = ChiMucUngVien(ngon_ngu=ngon_ngu)
    chi_muc.cap_nhat(danh_sach)
    norm_list = chi_muc.danh_sach_norm

    q_grams = _char_ngrams(q_content)
    all_docs = [item[1] for item in norm_list]
    bm25 = _bm25_scores(content_tokens, all_docs)

    ds_chi_tiet = []
    for slot, (c_label, c_toks, c_grams, ent) in enumerate(norm_list):
        eid = ent.get("id") or ""
        ten = ent.get("ten") or eid
        tinh_nang = ent.get("tinh_nang") or [{"hanh_dong": "bat"}]

        w_score = _word_sim(q_content, c_label, content_tokens, c_toks)
        ch_score = _jaccard(q_grams, c_grams)
        bm25_score = bm25[slot] if slot < len(bm25) else 0.0
        can_tu_ok = _tokens_aligned(content_tokens, c_toks)

        for tn in tinh_nang:
            hd = tn.get("hanh_dong") if isinstance(tn, dict) else str(tn)
            intent_score, opposing = _intent_affinity(cau, hd)
            diem_tong = (
                W_WORD * w_score +
                W_CHAR * ch_score +
                W_BM25 * bm25_score +
                W_INTENT * intent_score
            )
            if module is not None and hasattr(module, "kiem_kha_thi"):
                kha_thi, loi_kt = module.kiem_kha_thi(eid, hd)
            else:
                kha_thi, loi_kt = kiem_kha_thi_ung_vien(
                    ent,
                    hd,
                    mien_cam=mien_cam,
                    kha_nang_mien=kha_nang_mien,
                    trang_thai_hong=trang_thai_hong,
                    nhom_duoc_dieu_khien=nhom_duoc_dieu_khien,
                )
            ly_do_list = []
            if opposing:
                ly_do_list.append("Hành động đối nghịch")
            if not can_tu_ok:
                ly_do_list.append("Không qua cổng căn từ")
            if not kha_thi:
                ly_do_list.append(f"Không khả thi: {loi_kt}")
            if not ly_do_list:
                ly_do_list.append("Hợp lệ")

            loai_ent = ent.get("loai") or ""
            if not loai_ent:
                parts = eid.split(".")
                loai_ent = parts[1] if len(parts) > 2 and parts[0] == "home_assistant" else parts[0]
            dc = ent.get("device_class") or (ent.get("thuoc_tinh") or {}).get("device_class")
            rui_ro = tinh_rui_ro(loai_ent, dc)

            ds_chi_tiet.append({
                "entity_id": eid,
                "ten": ten,
                "hanh_dong": hd,
                "diem_tong": round(diem_tong, 4),
                "diem_chuoi": round(w_score, 4),
                "diem_ngram": round(ch_score, 4),
                "diem_bm25": round(bm25_score, 4),
                "diem_hanh_dong": round(intent_score, 4),
                "qua_cong_can_tu": can_tu_ok,
                "kha_thi": kha_thi,
                "rui_ro": rui_ro,
                "ly_do": "; ".join(ly_do_list),
            })

    ds_chi_tiet.sort(key=lambda x: x["diem_tong"], reverse=True)
    return {
        "cau_goc": cau,
        "cau_chuan_hoa": q_norm,
        "ung_vien": ds_chi_tiet,
    }


__all__ = [
    "normalize",
    "_tokens",
    "_char_ngrams",
    "_jaccard",
    "_seq_ratio",
    "_word_sim",
    "_bm25_scores",
    "_intent_affinity",
    "_tokens_aligned",
    "ChiMucUngVien",
    "KetQuaKhop",
    "kiem_kha_thi_ung_vien",
    "khop_thiet_bi",
    "giai_thich_khop",
    "W_WORD",
    "W_CHAR",
    "W_BM25",
    "W_INTENT",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_MARGIN",
    "PREFILTER_TOP",
]
