"""NV136-D — Bộ chọn công cụ theo tầng + cổng biên + phát hiện chồng chéo (module tro-ly).

Nguyên tắc:
- Xếp hạng nhiều tín hiệu thuần Python, KHÔNG danh sách từ khoá ánh xạ ý định→công cụ:
  tương đồng chuỗi (LCS), Jaccard 3-gram ký tự, BM25 trên CHÍNH tập ứng viên,
  khớp miền/loại thực thể C1 (do module khai, khớp chuỗi cấu trúc), tỉ lệ thành
  công đo từ nhật ký lượt chạy.
- CỔNG BIÊN: điểm hạng 1 < ngưỡng tối thiểu, hoặc chênh hạng 1–2 < ngưỡng,
  hoặc cặp đối nghịch không vượt biên đầy đủ, hoặc hai ứng viên thuộc cặp
  chồng chéo → mo_ho=true, agent phải hỏi lại thay vì đoán.
- Thiếu tham số bắt buộc (theo mẫu khai báo của công cụ) → hỏi lại.
- DANH MỤC: tên + một dòng (mo_ta_ngan ≤ 150 ký tự) CHỈ qua tim_cong_cu(y_dinh);
  schema đầy đủ chỉ nạp cho công cụ được chọn, tối đa K (mặc định 8, trần 16).

Học ý (không sao chép):
- c2a `services/ha_intent_rank.py`: ensemble W_WORD 0.33 / W_CHAR 0.32 /
  W_BM25 0.20 / W_INTENT 0.15; cổng căn từ; cổng biên min_confidence + min_margin;
  prefilter n-gram trước LCS đắt.
- c2a `services/agent/skills.py`: mô tả skill ≤ 150 ký tự trong bộ định tuyến.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

# ── Công cụ nền: ≤ 8, schema luôn nạp (C1) ────────────────────────────────
CONG_CU_NEN = [
    {"ten": "tim_cong_cu",
     "mo_ta": "Tìm công cụ phù hợp với ý định; trả schema đầy đủ của công cụ được chọn",
     "schema": {"type": "object",
                "properties": {"y_dinh": {"type": "string"}},
                "required": ["y_dinh"]}},
    {"ten": "tim_tri_thuc",
     "mo_ta": "Tìm tri thức liên quan; trả tham chiếu, không đổ toàn văn",
     "schema": {"type": "object",
                "properties": {"truy_van": {"type": "string"}},
                "required": ["truy_van"]}},
    {"ten": "doc_tri_thuc",
     "mo_ta": "Đọc đoạn tri thức đầy đủ theo id tham chiếu",
     "schema": {"type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"]}},
    {"ten": "hoi_lai",
     "mo_ta": "Hỏi lại người dùng khi mơ hồ hoặc thiếu tham số",
     "schema": {"type": "object",
                "properties": {"cau_hoi": {"type": "string"},
                               "lua_chon": {"type": "array", "items": {"type": "string"}}},
                "required": ["cau_hoi"]}},
    {"ten": "ket_thuc",
     "mo_ta": "Kết thúc lượt với câu trả lời cuối cùng",
     "schema": {"type": "object",
                "properties": {"tra_loi": {"type": "string"}},
                "required": ["tra_loi"]}},
    {"ten": "ghi_nho_tam",
     "mo_ta": "Ghi nhớ tạm trong phạm vi lượt chạy hiện tại",
     "schema": {"type": "object",
                "properties": {"khoa": {"type": "string"},
                               "gia_tri": {"type": "string"}},
                "required": ["khoa", "gia_tri"]}},
]
assert len(CONG_CU_NEN) <= 8

# ── Ngưỡng cổng biên (có thể chỉnh; test đột biến tắt cổng) ───────────────
NGUONG_TOI_THIEU = 0.30
NGUONG_BIEN = 0.08
NGUONG_BIEN_DOI_NGHICH = 0.15  # cặp đối nghịch phải vượt biên đầy đủ
NGUONG_CHONG_CHEO = 0.50       # max(Jaccard 3-gram, Jaccard token) của mô tả
K_MAC_DINH = 8
K_TRAN = 16
PREFILTER_TOP = 24

W_CHUOI = 0.20
W_TOKEN = 0.15   # max LCS từng token query với token ứng viên (biến thể mail/email)
W_3GRAM = 0.20
W_BM25 = 0.25
W_MIEN = 0.10
W_THANH_CONG = 0.10

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

from functools import lru_cache


@lru_cache(maxsize=8192)
def chuan_hoa(text: str) -> str:
    """Bỏ dấu, thường hoá, bỏ dấu câu, gộp khoảng trắng (học c2a normalize)."""
    s = unicodedata.normalize("NFKC", text or "").casefold().replace("đ", "d")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    return _WS_RE.sub(" ", _NON_WORD_RE.sub(" ", s)).strip()


def _tokens(s: str) -> list[str]:
    return [t for t in s.split() if t]


def _3grams(s: str) -> set[str]:
    s = s.replace(" ", "")
    if len(s) < 3:
        return {s} if s else set()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _lcs_ratio(a: str, b: str) -> float:
    """2*LCS/(len a + len b), DP một hàng (học c2a _seq_ratio)."""
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
    return (2.0 * dp[lb]) / (la + lb)


def _tuong_dong_chuoi(q: str, ten: str, mo_ta: str) -> float:
    """LCS trên chuỗi thô + LCS trên token đã sắp xếp + phủ token."""
    c = f"{ten} {mo_ta}"
    qt, ct = _tokens(q), _tokens(c)
    if not q or not c:
        return 0.0
    char_lcs = _lcs_ratio(q, c)
    sort_lcs = _lcs_ratio(" ".join(sorted(qt)), " ".join(sorted(ct)))
    qs, cs = set(qt), set(ct)
    overlap = len(qs & cs) / min(len(qs), len(cs)) if qs and cs else 0.0
    len_ratio = min(len(qt), len(ct)) / max(len(qt), len(ct)) if qt and ct else 0.0
    return (char_lcs + sort_lcs + overlap * len_ratio) / 3.0


def _token_max_sim(qt: list[str], ct: list[str]) -> float:
    """Trung bình max LCS từng token query với token ứng viên.

    Bắt biến thể viết tắt/gần nghĩa ở cấp token (vd "mail" ~ "email")
    mà không cần danh sách từ khoá.
    """
    if not qt or not ct:
        return 0.0
    return sum(max(_lcs_ratio(t, c) for c in ct) for t in qt) / len(qt)


def _bm25(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """BM25 trên chính tập ứng viên, chuẩn hoá về [0,1] (học c2a)."""
    n = len(docs)
    if not n or not query_tokens:
        return [0.0] * n
    avg = sum(len(d) for d in docs) / n
    if not avg:
        return [0.0] * n
    df: dict[str, int] = {}
    for d in docs:
        for tok in set(d):
            df[tok] = df.get(tok, 0) + 1
    k1, b = 1.5, 0.75
    raw = [0.0] * n
    for tok in set(query_tokens):
        f = df.get(tok)
        if not f:
            continue
        idf = math.log(1 + (n - f + 0.5) / (f + 0.5))
        for i, d in enumerate(docs):
            tf = d.count(tok)
            if not tf:
                continue
            denom = tf + k1 * (1 - b + b * len(d) / avg)
            raw[i] += idf * (k1 + 1) * tf / denom
    top = max(raw)
    return [r / top for r in raw] if top > 0 else raw


def mo_ta_ngan(mo_ta: str, gioi_han: int = 150) -> str:
    """Một dòng mô tả ≤ 150 ký tự: câu đầu của mô tả, cắt gọn (học c2a SKILL_DESC_MAX)."""
    cau_dau = re.split(r"[.!?\n]", mo_ta or "", maxsplit=1)[0].strip()
    if len(cau_dau) <= gioi_han:
        return cau_dau
    return cau_dau[:gioi_han - 1] + "…"


def _diem_mien(y_dinh_chuan: str, mien: str) -> float:
    """Khớp miền C1 do module khai bằng khớp chuỗi cấu trúc (không danh sách từ khoá)."""
    if not mien:
        return 0.5
    m = chuan_hoa(mien).replace("_", " ")
    return 1.0 if m and m in y_dinh_chuan else 0.3


def do_chong_cheo(ds_cong_cu: list[dict]) -> list[tuple[str, str, float]]:
    """Trả về các cặp (ten_a, ten_b, diem) có mô tả giống nhau quá ngưỡng.

    Điểm = max(Jaccard 3-gram ký tự, Jaccard tập token) — bắt cả cặp chỉ khác
    vài từ ("lưu trữ" ~ "dữ liệu").
    """
    chuan = [(c["ten"], chuan_hoa(c.get("mo_ta", ""))) for c in ds_cong_cu]
    grams = {t: _3grams(s) for t, s in chuan}
    toks = {t: set(_tokens(s)) for t, s in chuan}
    cap = []
    for i in range(len(chuan)):
        for j in range(i + 1, len(chuan)):
            ti, tj = chuan[i][0], chuan[j][0]
            d = max(_jaccard(grams[ti], grams[tj]), _jaccard(toks[ti], toks[tj]))
            if d >= NGUONG_CHONG_CHEO:
                cap.append((ti, tj, round(d, 3)))
    return cap


def _la_doi_nghich(a: dict, b: dict) -> bool:
    """Cặp đối nghịch do module khai báo cấu trúc (doi_nghich_voi), không đoán bằng từ."""
    return (a.get("doi_nghich_voi") == b.get("ten")
            or b.get("doi_nghich_voi") == a.get("ten"))


def _thuoc_cap_chong_cheo(a: str, b: str, cap: list[tuple[str, str, float]]) -> bool:
    return any((x == a and y == b) or (x == b and y == a) for x, y, _ in cap)


def chon_cong_cu(y_dinh: str, ung_vien: list[dict], *, k: int = K_MAC_DINH,
                 cong_cu_giu: list[str] | None = None,
                 ti_le_thanh_cong: dict[str, float] | None = None,
                 nguong_toi_thieu: float = NGUONG_TOI_THIEU,
                 nguong_bien: float = NGUONG_BIEN,
                 nguong_bien_doi_nghich: float = NGUONG_BIEN_DOI_NGHICH,
                 nguong_chong_cheo: float = NGUONG_CHONG_CHEO) -> dict:
    """Xếp hạng ứng viên theo y_dinh, qua cổng biên.

    Trả về {cong_cu, diem, mo_ho, ly_do, cau_hoi, lua_chon, thieu_tham_so, xep_hang}.
    """
    if not ung_vien:
        return {"cong_cu": None, "diem": 0.0, "mo_ho": True,
                "ly_do": "khong_co_ung_vien", "cau_hoi": None,
                "lua_chon": [], "thieu_tham_so": [], "xep_hang": []}
    q = chuan_hoa(y_dinh)
    qt = _tokens(q)
    ti_le = ti_le_thanh_cong or {}

    nhan = [(c["ten"], f"{c['ten']} {chuan_hoa(c.get('mo_ta', ''))}") for c in ung_vien]
    # Prefilter rẻ bằng 3-gram trước khi chạy LCS đắt (học c2a).
    q_grams = _3grams(q)
    diem_3g = [_jaccard(q_grams, _3grams(nh)) for _, nh in nhan]
    pool = list(range(len(ung_vien)))
    if len(pool) > PREFILTER_TOP:
        pool = sorted(pool, key=lambda i: diem_3g[i], reverse=True)[:PREFILTER_TOP]

    docs = [_tokens(nhan[i][1]) for i in pool]
    bm = _bm25(qt, docs)
    giu = set(cong_cu_giu or [])

    xep = []
    for slot, i in enumerate(pool):
        c = ung_vien[i]
        _, nh = nhan[i]
        ct = _tokens(nh)
        s_chuoi = _tuong_dong_chuoi(q, chuan_hoa(c["ten"]), chuan_hoa(c.get("mo_ta", "")))
        s_token = _token_max_sim(qt, ct)
        s_3g = diem_3g[i]
        s_bm = bm[slot]
        s_mien = _diem_mien(q, c.get("mien", ""))
        s_tc = ti_le.get(c["ten"], 0.5)
        diem = (W_CHUOI * s_chuoi + W_TOKEN * s_token + W_3GRAM * s_3g
                + W_BM25 * s_bm + W_MIEN * s_mien + W_THANH_CONG * s_tc)
        # Công cụ vừa dùng thành công ở lượt trước được giữ lại (C3).
        if c["ten"] in giu:
            diem = min(1.0, diem + 0.05)
        xep.append({"ten": c["ten"], "diem": round(diem, 4),
                    "thanh_phan": {"chuoi": round(s_chuoi, 3),
                                   "token": round(s_token, 3),
                                   "3gram": round(s_3g, 3),
                                   "bm25": round(s_bm, 3), "mien": s_mien,
                                   "thanh_cong": s_tc}})
    xep.sort(key=lambda x: x["diem"], reverse=True)
    best = xep[0]
    second = xep[1] if len(xep) > 1 else None

    # CỔNG BIÊN
    mo_ho = False
    ly_do = "du_diem"
    if best["diem"] < nguong_toi_thieu:
        mo_ho, ly_do = True, "diem_thap"
    elif second and (best["diem"] - second["diem"]) < nguong_bien:
        mo_ho, ly_do = True, "bien_hep"
    if not mo_ho and second:
        b0 = next(c for c in ung_vien if c["ten"] == best["ten"])
        b1 = next(c for c in ung_vien if c["ten"] == second["ten"])
        if _la_doi_nghich(b0, b1) and (best["diem"] - second["diem"]) < nguong_bien_doi_nghich:
            mo_ho, ly_do = True, "doi_nghich"
        elif 0 < nguong_chong_cheo < 1.0 and _thuoc_cap_chong_cheo(
                best["ten"], second["ten"],
                do_chong_cheo([c for c in ung_vien if c["ten"] in (best["ten"], second["ten"])])):
            # Chỉ kiểm chồng chéo trên cặp top-2 (rẻ); ngưỡng toàn cục dùng ở suc_khoe.
            mo_ho, ly_do = True, "chong_cheo"

    # Thiếu tham số bắt buộc: theo mẫu khai báo của công cụ (cấu trúc, không đoán).
    thieu: list[str] = []
    if not mo_ho:
        b0 = next(c for c in ung_vien if c["ten"] == best["ten"])
        for ten_ts, mau in (b0.get("tham_so_bat_buoc") or {}).items():
            try:
                if not re.search(mau, y_dinh):
                    thieu.append(ten_ts)
            except re.error:
                thieu.append(ten_ts)

    cau_hoi = None
    lua_chon: list[str] = []
    if mo_ho or thieu:
        lua_chon = [x["ten"] for x in xep[:4]]
        if mo_ho:
            cau_hoi = f"Ý bạn là công cụ nào? {'; '.join(f'{i+1}. {t}' for i, t in enumerate(lua_chon))}"
        else:
            cau_hoi = (f"Công cụ {best['ten']} cần thêm tham số: "
                       f"{', '.join(thieu)}. Bạn cho biết giá trị?")

    # top-K có schema đầy đủ (K mặc định 8, trần 16).
    k = max(1, min(k, K_TRAN))
    topk = []
    bang = {c["ten"]: c for c in ung_vien}
    for x in xep[:k]:
        c = bang[x["ten"]]
        topk.append({"ten": x["ten"], "mo_ta": mo_ta_ngan(c.get("mo_ta", "")),
                     "diem": x["diem"]})

    return {"cong_cu": best["ten"], "diem": best["diem"], "mo_ho": mo_ho,
            "ly_do": ly_do, "cau_hoi": cau_hoi, "lua_chon": lua_chon,
            "thieu_tham_so": thieu, "xep_hang": topk}


def tim_cong_cu(y_dinh: str, danh_muc: list[dict], *, k: int = K_MAC_DINH,
                **kw) -> dict:
    """C2: DANH MỤC (tên + một dòng) CHỈ qua hàm này; trả schema top-K.

    Schema đầy đủ ở đây là mô phỏng: mỗi mục top-K kèm mo_ta_ngan; schema thật
    do module chủ quản cung cấp khi lượt chạy gọi (NV135 nối).
    """
    kq = chon_cong_cu(y_dinh, danh_muc, k=k, **kw)
    kq["danh_muc"] = [{"ten": c["ten"], "mo_ta": mo_ta_ngan(c.get("mo_ta", ""))}
                      for c in danh_muc]
    kq["cong_cu_nen"] = [c["ten"] for c in CONG_CU_NEN]
    return kq
