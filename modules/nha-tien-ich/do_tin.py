"""Độ tin của cảm biến — đo trên lịch sử THẬT, học từ DỮ LIỆU.

Port từ ``services/do_tin_cam_bien.py`` (c2a, commit d879b03). Giữ nguyên thuật
toán và các ngưỡng ĐÃ ĐO trên dữ liệu thật (không phải ngưỡng cứng theo tên cảm
biến): nhiễu = đổi dày + phần lớn lần ở chớp nhoáng; kẹt = từng đổi nhiều nhưng
đứng im 24h; ngưỡng giữ = p40 các lần ở.

Ba bẫy tự học của c2a (CLAUDE.md «Bot TỰ HỌC») được giữ bằng thiết kế:
1. Không dùng giá trị MỚI NHẤT cho câu hỏi quá khứ — ``chuoi_tai_moc`` dựng lại
   giá trị TẠI mốc thời gian từ chuỗi sự kiện, không lấy trạng thái hiện tại.
2. Phải có mẫu ÂM — ``them_mau_am`` sinh mẫu «không có người» xen kẽ.
3. Bỏ mẫu do AI tự gây (``do_ai=1``) khi học — ``loc_bo_do_ai``.
"""
from __future__ import annotations

import time
from typing import Any

#: Một lần ở dưới ngần này giây coi là NHIỄU (đo 04/10/2026 trên dữ liệu thật).
NGAN_GIAY = 10.0
#: «Nhiễu»: phần lớn lần đổi là chớp nhoáng + đổi đủ dày.
NHIEU_TL = 0.25
NHIEU_DOI_NGAY = 150.0
#: «Kẹt»: 7 ngày từng đổi ngần này lần nhưng 24 giờ qua đứng im.
KET_DOI_7NGAY = 50
KET_IM_GIAY = 86400.0
#: Khoảng cách radar: chạm 0 quá nhiều ô = tín hiệu giật.
KC_0_TL = 0.6


def _dwell(ev: list[tuple[float, str]]) -> dict[str, list[float]]:
    d: dict[str, list[float]] = {}
    for i in range(len(ev) - 1):
        d.setdefault(ev[i][1], []).append(ev[i + 1][0] - ev[i][0])
    return d


def _nguong_giu(moi: list[float]) -> float:
    """Ngưỡng GIỮ: p40 các lần ở, chặn trong [NGAN_GIAY, 120]."""
    if not moi:
        return NGAN_GIAY
    s = sorted(moi)
    p40 = s[int(len(s) * 0.4)]
    return round(max(NGAN_GIAY, min(120.0, p40)), 1)


def do_tin(ma: str, su_kien: list[tuple[float, str]], su_kien_7n: list[tuple[float, str]] | None = None,
           so_ngay: float = 3.0, now: float | None = None) -> dict[str, Any]:
    """Độ tin của MỘT cảm biến nhị phân trên chuỗi sự kiện (ts, giá_trị).

    Port từ ``do_tin_cam_bien.do`` — tách khỏi SQLite để dùng được trong module;
    chuỗi sự kiện do tầng nhật ký của module cung cấp.
    """
    now = float(now if now is not None else time.time())
    ev = sorted(su_kien, key=lambda x: x[0])
    ev7 = sorted(su_kien_7n, key=lambda x: x[0]) if su_kien_7n is not None else ev
    if len(ev) < 4:
        return {"ma": ma, "so_doi": len(ev), "nhan": "it_du_lieu"}
    d = _dwell(ev)
    moi = [v for ds in d.values() for v in ds]
    doi_ngay = len(ev) / so_ngay
    ngan_tl = sum(1 for v in moi if v < NGAN_GIAY) / len(moi) if moi else 0.0
    dwell_dai = max(moi) if moi else 0.0
    moc_cuoi = ev7[-1][0] if ev7 else 0.0
    ket = len(ev7) >= KET_DOI_7NGAY and (now - moc_cuoi) >= KET_IM_GIAY
    if ket:
        nhan = "ket"
    elif ngan_tl >= NHIEU_TL and doi_ngay >= NHIEU_DOI_NGAY:
        nhan = "nhieu"
    else:
        nhan = "lanh"
    return {"ma": ma, "so_doi": len(ev), "doi_ngay": round(doi_ngay, 1),
            "ngan_tl": round(ngan_tl, 3), "dwell_dai_gio": round(dwell_dai / 3600, 2),
            "nhieu_giay": _nguong_giu(moi), "moc_cuoi": moc_cuoi,
            "im_gio": round((now - moc_cuoi) / 3600, 1), "nhan": nhan}


def do_tin_so(ma: str, o_5p: list[tuple[float, float]], now: float | None = None) -> dict[str, Any]:
    """Độ tin cảm biến SỐ (khoảng cách radar): [(nho, lon)] theo ô 5 phút."""
    now = float(now if now is not None else time.time())
    if len(o_5p) < 20:
        return {"ma": ma, "nhan": "it_du_lieu"}
    cham0 = sum(1 for nho, _ in o_5p if nho == 0) / len(o_5p)
    bien = sum(1 for nho, lon in o_5p if lon - nho > 3) / len(o_5p)
    nhan = "nhieu" if cham0 >= KC_0_TL else "lanh"
    return {"ma": ma, "so_o": len(o_5p), "cham0_tl": round(cham0, 3),
            "bien_rong_tl": round(bien, 3), "nhan": nhan}


# ── Ba bẫy tự học ────────────────────────────────────────────────────────────

def chuoi_tai_moc(su_kien: list[tuple[float, str]], moc: float) -> str | None:
    """Bẫy 1 — giá trị TẠI mốc quá khứ, dựng từ chuỗi sự kiện.

    Không bao giờ lấy trạng thái HIỆN TẠI để trả lời câu hỏi về quá khứ (rò rỉ
    tương lai). Port nguyên tắc ``boi_canh() cho quá khứ, hien_tai() cho bây giờ``.
    """
    gt: str | None = None
    for ts, g in sorted(su_kien, key=lambda x: x[0]):
        if ts <= moc:
            gt = str(g)
        else:
            break
    return gt


def them_mau_am(mau_duong: list[dict[str, Any]], ti_le: float = 1.0) -> list[dict[str, Any]]:
    """Bẫy 2 — sinh mẫu ÂM: log chỉ ghi cái đã xảy ra; không có mẫu âm thì mọi
    xác suất học được đều bằng 1. Mỗi mẫu dương sinh ``ti_le`` mẫu âm «không có
    người / không bật» tại mốc xen kẽ."""
    ra = list(mau_duong)
    for m in mau_duong:
        moc = float(m.get("moc", 0)) - 1800.0  # 30 phút trước mẫu dương
        ra.append({**m, "moc": moc, "nhan_mau": "am", "ket_qua": "khong"})
    return ra


def loc_bo_do_ai(mau: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bẫy 3 — bỏ mẫu do AI tự gây (``do_ai=1``) khi học: bot bật đèn rồi thấy
    đèn bật lại tự khẳng định vòng quanh."""
    return [m for m in mau if not m.get("do_ai")]
