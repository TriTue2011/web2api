"""Thuật toán chuẩn hoá văn bản tiếng Việt để đọc (TTS).

Tuân thủ nghiêm ngặt các nguyên tắc:
- Thuần Python, không I/O, không gọi mạng.
- Xác định (cùng đầu vào -> cùng đầu ra).
- Giới hạn 20.000 ký tự/lần (vượt quá -> ValueError).
- Chống ReDoS: regex an toàn, không lồng lượng từ vô hạn, xử lý chuỗi 20.000 ký tự < 1 giây.
- Xử lý đủ 8 lớp lỗi theo thứ tự ưu tiên cấu trúc.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .du_lieu import (
    DON_VI,
    NGUYEN_TO,
    PHAN_UNG,
    TEN_CHU,
    TEN_NGUYEN_TO,
    TIEN_TE,
    VIET_TAT_MO_HO,
    VIET_TAT_MOT_NGHIA,
    XUNG_HO,
    chuyen_so_la_ma,
)

TRAN_KY_TU = 20_000

# Các từ số tiếng Việt
_SO_CHU = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def doc_so_nguyen(n: int) -> str:
    """Đọc số nguyên không âm (0 đến hàng tỷ) chuẩn tiếng Việt."""
    if n < 10:
        return _SO_CHU[n]
    if n < 100:
        chuc, dv = divmod(n, 10)
        dau = "mười" if chuc == 1 else f"{_SO_CHU[chuc]} mươi"
        if dv == 0:
            return dau
        if dv == 1:
            duoi = "mốt" if chuc > 1 else "một"
        elif dv == 4:
            duoi = "tư" if chuc > 1 else "bốn"
        elif dv == 5:
            duoi = "lăm"
        else:
            duoi = _SO_CHU[dv]
        return f"{dau} {duoi}"
    if n < 1000:
        tram, du = divmod(n, 100)
        dau = f"{_SO_CHU[tram]} trăm"
        if du == 0:
            return dau
        if du < 10:
            return f"{dau} linh {_SO_CHU[du]}"
        return f"{dau} {doc_so_nguyen(du)}"
    if n < 1_000_000:
        nghin, du = divmod(n, 1000)
        dau = f"{doc_so_nguyen(nghin)} nghìn"
        if du == 0:
            return dau
        if du < 100:
            return f"{dau} không trăm {'linh ' + _SO_CHU[du] if du < 10 else doc_so_nguyen(du)}"
        return f"{dau} {doc_so_nguyen(du)}"
    if n < 1_000_000_000:
        trieu, du = divmod(n, 1_000_000)
        dau = f"{doc_so_nguyen(trieu)} triệu"
        if du == 0:
            return dau
        if du < 1000:
            return f"{dau} không trăm nghìn {doc_so_nguyen(du)}"
        return f"{dau} {doc_so_nguyen(du)}"
    ty, du = divmod(n, 1_000_000_000)
    dau = f"{doc_so_nguyen(ty)} tỷ"
    if du == 0:
        return dau
    return f"{dau} {doc_so_nguyen(du)}"


def doc_so_thap_phan(phan_nguyen: str, phan_thap_phan: str) -> str:
    """Đọc số thập phân: nguyên + 'phẩy' + thập phân."""
    try:
        nguyen_str = doc_so_nguyen(int(phan_nguyen))
    except ValueError:
        nguyen_str = phan_nguyen

    tp_str = phan_thap_phan.strip()
    if tp_str == "5":
        tp_doc = "năm"
    elif tp_str == "25":
        tp_doc = "hai lăm"
    elif tp_str.startswith("0") or len(tp_str) > 2:
        tp_doc = " ".join(_SO_CHU[int(c)] for c in tp_str if c.isdigit())
    elif len(tp_str) <= 2:
        try:
            n_tp = int(tp_str)
            tp_doc = doc_so_nguyen(n_tp)
        except ValueError:
            tp_doc = " ".join(_SO_CHU[int(c)] for c in tp_str if c.isdigit())
    else:
        tp_doc = " ".join(_SO_CHU[int(c)] for c in tp_str if c.isdigit())

    return f"{nguyen_str} phẩy {tp_doc}"


def danh_van_chu_cai(s: str) -> str:
    """Đánh vần từng chữ cái tiếng Việt."""
    phan = []
    for c in s:
        u = c.upper()
        if u in TEN_CHU:
            phan.append(TEN_CHU[u])
        elif c.isalnum():
            phan.append(c)
    return " ".join(phan)


# Regex các mẫu thường dùng
_RE_EMOJI = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\ufe00-\ufe0f]",
    flags=re.UNICODE,
)
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'()]{1,2000}[^\s<>\"'().,;:!?]", re.IGNORECASE)
_RE_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,10})\b")
_RE_SO_DT = re.compile(r"(?<![\w.,])(0\d{9,10})(?![\w.,])")


def _xu_ly_hau_ky_tts(t: str) -> tuple[str, int]:
    """Bỏ emoji, Markdown, LaTeX, giữ nguyên nội dung."""
    da_doi = 0

    # 1. Bỏ Emoji và Variation Selectors
    t_moi, n = _RE_EMOJI.subn("", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # 2. LaTeX: $$...$$ hoặc $...$
    def _thay_latex(m: re.Match) -> str:
        nd = m.group(1).strip()
        nd = nd.replace("\\sin", "sin").replace("\\cos", "cos").replace("\\tan", "tan")
        nd = nd.replace("\\times", " nhân ").replace("\\div", " chia ")
        return nd

    t_moi, n = re.subn(r"\$\$(.+?)\$\$", _thay_latex, t, flags=re.DOTALL)
    if n > 0:
        da_doi += n
        t = t_moi

    t_moi, n = re.subn(r"\$([^\$\n]+?)\$", _thay_latex, t)
    if n > 0:
        da_doi += n
        t = t_moi

    # 3. Markdown
    # Bảng Markdown: | Cột 1 | Cột 2 |
    def _thay_bang_markdown(m: re.Match) -> str:
        nonlocal da_doi
        bang_str = m.group(0)
        lines = bang_str.strip().splitlines()
        o_cac_dong = []
        for line in lines:
            stripped = line.strip()
            if re.fullmatch(r"\|(?:\s*[-:]+[-| :]*)\|", stripped):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            for c in cells:
                if c:
                    o_cac_dong.append(c)
        da_doi += len(lines)
        return " " + " ".join(o_cac_dong)

    t = re.sub(r"(?:\n|^)[ \t]*\|[^\n]+\|[ \t]*(?:\n[ \t]*\|[^\n]+\|[ \t]*)*", _thay_bang_markdown, t)

    # Link: [Nội dung](url) -> Nội dung
    t_moi, n = re.subn(r"\[([^\]]+)\]\([^\)]+\)", r"\1", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # In đậm/nghiêng: **chữ** hoặc *chữ*
    t_moi, n = re.subn(r"\*\*([^*]+)\*\*", r"\1", t)
    if n > 0:
        da_doi += n
        t = t_moi

    t_moi, n = re.subn(r"\*([^*]+)\*", r"\1", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # Code inline: `code`
    t_moi, n = re.subn(r"`([^`]+)`", r"\1", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # Tiêu đề: # Tiêu đề, ## Tiêu đề
    t_moi, n = re.subn(r"(?m)^#{1,6}\s+", "", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # Blockquote: > Trích dẫn
    t_moi, n = re.subn(r"(?m)^\s*>\s*", "", t)
    if n > 0:
        da_doi += n
        t = t_moi

    # Dòng kẻ ngang: --- hoặc ***
    t_moi, n = re.subn(r"(?m)^\s*[-*_]{3,}\s*$", "", t)
    if n > 0:
        da_doi += n
        t = t_moi

    return t, da_doi


def _xu_ly_gach_dau_dong(t: str) -> tuple[str, int]:
    """Xử lý gạch đầu dòng: - 60% không phải số âm."""
    da_doi = 0
    t_moi, n = re.subn(r"(?m)^(\s*)[-–—•*]\s+", r"\1", t)
    if n > 0:
        da_doi += n
        t = t_moi
    return t, da_doi


def _xu_ly_url_va_email(t: str) -> tuple[str, int]:
    """Đọc URL và Email sang tiếng Việt chuẩn."""
    da_doi = 0

    def _thay_email(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        user = m.group(1)
        domain = m.group(2)
        d_parts = domain.split(".")
        return f"{user} a còng {' chấm '.join(d_parts)}"

    t = _RE_EMAIL.sub(_thay_email, t)

    def _thay_url(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        raw = m.group(0)
        u = re.sub(r"^(?:https?://)?(?:www\.)?", "", raw, flags=re.IGNORECASE)
        u = re.split(r"[?#]", u, maxsplit=1)[0].rstrip("/")
        may, _, duong = u.partition("/")
        host_parts = [p for p in may.split(".") if p]
        host_str = " chấm ".join(host_parts)
        if duong:
            path_parts = [re.sub(r"[-_.]+", " ", p) for p in duong.split("/") if p]
            return f"{host_str} gạch chéo {' gạch chéo '.join(path_parts)}"
        return host_str

    t = _RE_URL.sub(_thay_url, t)
    return t, da_doi


def _xu_ly_tieu_de_in_hoa(t: str) -> tuple[str, int]:
    """Hạ chữ in hoa tiêu đề có dấu tiếng Việt, giữ nguyên viết tắt đã biết."""
    da_doi = 0
    lines = t.splitlines()
    lines_moi = []
    for line in lines:
        words = line.split()
        if len(words) >= 2:
            # Kiểm tra xem dòng có phải là tiêu đề in hoa không
            count_upper = sum(1 for w in words if w.isupper() and len(w) >= 2)
            has_accent = any(c in "ÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ" for c in line)
            if count_upper >= 2 and (has_accent or count_upper >= len(words) - 1):
                words_moi = []
                for w in words:
                    # Giữ nguyên nếu là từ viết tắt đã biết trong từ điển
                    if w in VIET_TAT_MOT_NGHIA or w in VIET_TAT_MO_HO:
                        words_moi.append(w)
                    elif w.isupper():
                        words_moi.append(w.lower())
                        da_doi += 1
                    else:
                        words_moi.append(w)
                line = " ".join(words_moi)
        lines_moi.append(line)
    return "\n".join(lines_moi), da_doi


def _xu_ly_ten_viet_tat_cham(t: str) -> tuple[str, int]:
    """Tên viết tắt có chấm L.T.M.P -> đọc theo chữ cái."""
    da_doi = 0

    def _thay_ten_cham(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        raw = m.group(0)
        letters = [c for c in raw if c.isalpha()]
        return " ".join(TEN_CHU.get(c.upper(), c) for c in letters)

    t = re.sub(r"(?<![\w.])(?:[A-ZĐ]\.){1,}[A-ZĐ](?![\w])\.?", _thay_ten_cham, t)
    return t, da_doi


def _xu_ly_so_dien_thoai(t: str) -> tuple[str, int]:
    """Số điện thoại đọc từng chữ số."""
    da_doi = 0

    def _thay_sdt(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        so = m.group(1)
        return " ".join(_SO_CHU[int(c)] for c in so)

    t = _RE_SO_DT.sub(_thay_sdt, t)
    return t, da_doi


def _xu_ly_khoang_va_ti_so(t: str) -> tuple[str, int]:
    """Khoảng số, ngày tháng, tỉ số."""
    da_doi = 0

    # 1. Số hiệu văn bản: 100/2019/NĐ-CP -> 100 năm 2019 nghị định Chính phủ
    def _thay_vb(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        so = m.group(1)
        nam = m.group(2)
        ky_hieu = m.group(3)
        kh_doc = VIET_TAT_MOT_NGHIA.get(ky_hieu, ky_hieu)
        return f"{so} năm {nam} {kh_doc}"

    t = re.sub(r"(?<![\w/])(\d{1,5})/((?:19|20)\d{2})/([A-ZĐ][A-ZĐ0-9]*(?:-[A-ZĐ][A-ZĐ0-9]*)*)(?![\w/])", _thay_vb, t)

    # 2. Khoảng ngày: 10-21/11/2025 -> 10 đến ngày 21 tháng 11 năm 2025
    def _thay_khoang_ngay(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        tu = m.group(1) or ""
        n1 = m.group(2)
        n2 = m.group(3)
        thang = m.group(4)
        nam = m.group(5) if m.group(5) else ""
        nam_str = f" năm {nam[1:]}" if nam else ""
        return f"{tu}{n1} đến ngày {n2} tháng {thang}{nam_str}"

    t = re.sub(r"\b(từ\s+)?(?:ngày\s+)?(\d{1,2})-(\d{1,2})/(\d{1,2})((?:/(?:19|20)\d{2})?)(?![\w/])", _thay_khoang_ngay, t)

    # Ngày tháng: ngày 25-9 -> ngày 25 tháng 9
    def _thay_ngay_thang(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        return f"{m.group(1)}{m.group(2)} tháng {m.group(3)}"

    t = re.sub(r"(\bngày\s+)(\d{1,2})-(\d{1,2})\b", _thay_ngay_thang, t)

    # 3. Khoảng số tăng dần trước đơn vị thời gian / đơn vị đo: 2-3 ngày, 8-10 giờ, 0-100 km/h
    don_vi_khoang = (
        r"giây|phút|giờ|tiếng|ngày|tuần|tháng|năm|tuổi|lần|người|học sinh|doanh nghiệp"
        r"|km/h|m/s|km|m|cm|mm|kg|g|mg|l|lít|ml|kWh|kcal|Ah|V|W|Hz|°C|%|triệu|tỷ|nghìn"
        r"|ha|GB|MB|TB|KB"
    )

    def _thay_khoang(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        a, b = int(m.group(1)), int(m.group(2))
        dv = m.group(3)
        if a >= b:
            return m.group(0)
        # Quét lùi bằng chỉ số, không cắt chuỗi tiền tố
        idx = m.start() - 1
        s = m.string
        while idx >= 0 and s[idx].isspace():
            idx -= 1
        co_tu = (
            idx >= 1
            and s[idx - 1:idx + 1].lower() == "từ"
            and (idx - 2 < 0 or not s[idx - 2].isalnum())
        )
        tu = "từ " if a == 0 and not co_tu else ""
        return f"{tu}{a} đến {b} {dv}"

    t = re.sub(rf"(?<![\w/.,:-])(\d{{1,4}})-(\d{{1,4}})\s+({don_vi_khoang})(?![\wÀ-ỹ])", _thay_khoang, t)

    # 4. Tỉ số trận đấu: 3-1, 2-2, 2-0
    def _thay_ti_so(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        tien_to_tran = m.group(1)
        return f"{tien_to_tran} {m.group(2)} {m.group(3)}"

    t = re.sub(
        r"\b(trận\s+đấu|kết\s+quả\s+trận\s+đấu|kết\s+quả|tỉ\s+số|tỷ\s+số|hòa|hoà|thắng|thua)\s+(\d{1,2})-(\d{1,2})\b",
        _thay_ti_so,
        t,
        flags=re.IGNORECASE,
    )

    return t, da_doi


def _xu_ly_so_la_ma(t: str) -> tuple[str, int]:
    """Số La Mã: quý IV, thế kỷ XXI, 95-III, II/III."""
    da_doi = 0

    # Phân số La Mã: II/III -> hai phần ba
    def _thay_ps_la_ma(m: re.Match) -> str:
        nonlocal da_doi
        n1 = chuyen_so_la_ma(m.group(1))
        n2 = chuyen_so_la_ma(m.group(2))
        if n1 and n2:
            da_doi += 1
            return f"{doc_so_nguyen(n1)} phần {doc_so_nguyen(n2)}"
        return m.group(0)

    t = re.sub(r"\b([IVX]{1,6})/([IVX]{1,6})\b", _thay_ps_la_ma, t)

    # Quý La Mã: quý IV -> quý bốn
    def _thay_quy(m: re.Match) -> str:
        nonlocal da_doi
        n = chuyen_so_la_ma(m.group(1))
        if n:
            da_doi += 1
            return f"quý {doc_so_nguyen(n)}"
        return m.group(0)

    t = re.sub(r"\b(?:quý|Quý)\s+([IVX]{1,4})\b", _thay_quy, t)

    # Thế kỷ La Mã: thế kỷ XXI -> thế kỷ hai mươi mốt
    def _thay_the_ky(m: re.Match) -> str:
        nonlocal da_doi
        n = chuyen_so_la_ma(m.group(1))
        if n:
            da_doi += 1
            return f"thế kỷ {doc_so_nguyen(n)}"
        return m.group(0)

    t = re.sub(r"\b(?:thế\s+kỷ|Thế\s+kỷ)\s+([IVX]{1,6})\b", _thay_the_ky, t)

    # Hậu tố mã: 95-III -> 95 3
    def _thay_hau_to(m: re.Match) -> str:
        nonlocal da_doi
        n = chuyen_so_la_ma(m.group(1))
        if n:
            da_doi += 1
            return f" {n}"
        return m.group(0)

    t = re.sub(r"(?<=[\dA-Za-z])-([IVX]+)(?![\w])", _thay_hau_to, t)

    return t, da_doi


def _xu_ly_phan_tach_hang_nghin(t: str) -> tuple[str, int]:
    """Phân tách hàng nghìn 1.234.567 -> một triệu hai trăm ba mươi tư nghìn năm trăm sáu mươi bảy."""
    da_doi = 0

    def _thay_hang_nghin(m: re.Match) -> str:
        nonlocal da_doi
        raw = m.group(0)
        so_thuan = raw.replace(".", "")
        try:
            n = int(so_thuan)
            da_doi += 1
            return doc_so_nguyen(n)
        except ValueError:
            return raw

    t = re.sub(r"(?<![\w.,])\d{1,3}(?:\.\d{3}){1,}(?![\d.,])", _thay_hang_nghin, t)
    return t, da_doi


def _xu_ly_tien_te(t: str) -> tuple[str, int]:
    """Tiền tệ: 50 USD, 10 BTC, 100.000 đ, một triệu đ."""
    da_doi = 0
    tien_pattern = "|".join(re.escape(k) for k in sorted(TIEN_TE.keys(), key=len, reverse=True))

    def _thay_tien(m: re.Match) -> str:
        nonlocal da_doi
        truoc = m.group(1)
        ma_tien = m.group(2)
        ten_tien = TIEN_TE.get(ma_tien, ma_tien)
        da_doi += 1
        return f"{truoc} {ten_tien}"

    t = re.sub(rf"(?<![\w.,])(\d+(?:[.,]\d+)*)\s*({tien_pattern})(?![\w/-])", _thay_tien, t)
    tu_so = r"\b(?:không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|mươi|trăm|nghìn|triệu|tỷ|lăm|mốt|tư|linh)"
    t = re.sub(rf"({tu_so})\s+({tien_pattern})(?![\w/-])", _thay_tien, t)
    return t, da_doi


def _xu_ly_don_vi_va_thap_phan(t: str) -> tuple[str, int]:
    """Xử lý đơn vị đo tập đóng và số thập phân đi kèm."""
    da_doi = 0
    dv_keys = sorted(DON_VI.keys(), key=len, reverse=True)
    dv_pattern = "|".join(re.escape(k) for k in dv_keys)

    def _thay_dv(m: re.Match) -> str:
        nonlocal da_doi
        so_str = m.group(1)
        dv = m.group(2)

        # Giờ 'h': chỉ là giờ khi <= 24
        if dv == "h":
            try:
                if float(so_str.replace(",", ".")) > 24:
                    return m.group(0)
            except ValueError:
                pass
        # Độ K: phải có khoảng trắng
        if dv == "K" and " " not in m.group(0):
            return m.group(0)

        ten_dv = DON_VI.get(dv, dv)
        da_doi += 1

        # Nếu là số thập phân: đổi số sang chữ (7.3 -> bảy phẩy ba)
        m_tp = re.fullmatch(r"(\d+)[.,](\d+)", so_str)
        if m_tp:
            nguyen = m_tp.group(1)
            thap_phan = m_tp.group(2)
            so_doc = doc_so_thap_phan(nguyen, thap_phan)
            return f"{so_doc} {ten_dv}"

        return f"{so_str} {ten_dv}"

    t = re.sub(rf"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*({dv_pattern})(?![\w/-])", _thay_dv, t)
    return t, da_doi


def _xu_ly_so_am(t: str) -> tuple[str, int]:
    """Số âm: -5 độ C, -10°C, -2 độ C."""
    da_doi = 0

    def _thay_am(m: re.Match) -> str:
        nonlocal da_doi
        da_doi += 1
        return f"âm {m.group(1)}"

    t = re.sub(r"(?<![\w.,])-(\d+(?:[.,]\d+)?)(?=\s*(?:độ|°C|ken vin|vôn|mét|oát|%))", _thay_am, t)
    t = re.sub(r"(?m)^-(\d+(?:[.,]\d+)?)\s+", lambda m: f"âm {m.group(1)} ", t)
    return t, da_doi


def _la_ten_nguoi_hoac_tu_thuong(cau: str, start: int, token: str) -> bool:
    """Kiểm tra ký hiệu có phải tên người hoặc từ ngữ thông thường không.
    Dùng chỉ số lùi/tiến trong tập đóng XUNG_HO, không cắt tiền tố chuỗi.
    """
    # 1. Tìm từ đứng ngay trước start
    idx = start - 1
    while idx >= 0 and cau[idx].isspace():
        idx -= 1
    if idx >= 0:
        end_w = idx + 1
        while idx >= 0 and not cau[idx].isspace():
            idx -= 1
        tu_truoc = cau[idx + 1:end_w].lower().strip(".,;:!?\"'()")
        if tu_truoc in XUNG_HO:
            return True

    # 2. Tìm từ đứng ngay sau token (ví dụ: "Ba mẹ", "Ba má")
    sau_start = start + len(token)
    idx_sau = sau_start
    while idx_sau < len(cau) and cau[idx_sau].isspace():
        idx_sau += 1
    if idx_sau < len(cau):
        end_sau = idx_sau
        while end_sau < len(cau) and not cau[end_sau].isspace():
            end_sau += 1
        tu_sau = cau[idx_sau:end_sau].lower().strip(".,;:!?\"'()")
        if tu_sau in XUNG_HO:
            return True

    return False


def _phan_tich_cong_thuc(raw: str) -> tuple[bool, str, str]:
    """Phân tích cấu trúc công thức hoá học theo IUPAC và bảng 118 nguyên tố.
    Trả về (la_cong_thuc, clean_formula, dien_tich_str).
    """
    if raw.endswith("°C") or "°C" in raw or raw.startswith("0x"):
        return False, "", ""

    dien_tich = ""
    dt_m = re.search(r"(\^?\d*[+-])$", raw)
    clean = raw
    if dt_m:
        dt_raw = dt_m.group(1).lstrip("^")
        clean = raw[:dt_m.start()]
        dau = "cộng" if dt_raw.endswith("+") else "trừ"
        so_dt = dt_raw[:-1]
        dien_tich = f"{doc_so_nguyen(int(so_dt)) if so_dt else ''} {dau}".strip()

    # Không nhầm lẫn với từ viết tắt đã biết trong từ điển
    if clean in VIET_TAT_MOT_NGHIA or clean in VIET_TAT_MO_HO:
        return False, "", ""

    parts = re.findall(r"([A-Z][a-z]?|\(|\)|\d+)", clean)
    if "".join(parts) != clean:
        return False, "", ""

    elems = [p for p in parts if p in NGUYEN_TO]
    if not elems:
        return False, "", ""

    valid_elems = all(p in NGUYEN_TO or p.isdigit() or p in "()" for p in parts)
    if not valid_elems:
        return False, "", ""

    # Đặc trưng cấu trúc hoá học IUPAC:
    # 1. Có chỉ số số học (H2SO4, CaCO3, H2O, CO2...)
    has_digits = any(p.isdigit() for p in parts)
    # 2. Có dấu ngoặc (Al(OH)3, Ca(OH)2...)
    has_parens = "(" in clean and ")" in clean
    # 3. Có điện tích (Fe3+, SO4^2-...)
    has_charge = bool(dien_tich)
    # 4. Hợp chất IUPAC gồm ≥ 2 nguyên tố, trong đó có nguyên tố 2 chữ cái (NaCl, NaOH, HCl, BaO, CaO...)
    has_iupac_compound = len(elems) >= 2 and any(len(e) == 2 for e in elems)

    if has_digits or has_parens or has_charge or has_iupac_compound:
        return True, clean, dien_tich
    return False, "", ""


def _tach_thanh_cau(t: str) -> list[str]:
    """Tách chuỗi thành các phân đoạn câu giữ nguyên định dạng ký tự."""
    tokens = re.split(r"([!?;\n]+|(?<!\d)\.(?!\d)\s+)", t)
    cau_list = []
    tam = ""
    for tok in tokens:
        if not tok:
            continue
        tam += tok
        if re.search(r"[!?;\n]$|(?<!\d)\.(?!\d)\s+$", tok):
            cau_list.append(tam)
            tam = ""
    if tam:
        cau_list.append(tam)
    return cau_list


def _xu_ly_hoa_hoc(t: str) -> tuple[str, int]:
    """Xử lý công thức hoá học và phản ứng theo nguyên tắc cấu trúc."""
    da_doi = 0
    _UNG_VIEN_RE = re.compile(r"(?<![\w°])\d{0,4}[A-Z(][A-Za-z0-9()^+\-]{0,40}[A-Za-z0-9()+–-](?!\.[A-Za-z])(?!\w)")

    def _doc_ct(token: str) -> str:
        m = re.findall(r"([A-Z][a-z]?|\(|\)|\d+)", token)
        res = []
        i = 0
        while i < len(m):
            item = m[i]
            if item == "(":
                j = i + 1
                while j < len(m) and m[j] != ")":
                    j += 1
                if j < len(m):
                    trong = [_doc_ct_don(x) for x in m[i + 1:j]]
                    chi_so = ""
                    if j + 1 < len(m) and m[j + 1].isdigit():
                        chi_so = f" {doc_so_nguyen(int(m[j + 1]))} lần"
                        i = j + 1
                    else:
                        i = j
                    res.append(" ".join(trong) + chi_so)
                else:
                    res.append(item)
            else:
                res.append(_doc_ct_don(item))
            i += 1
        return " ".join(res)

    def _doc_ct_don(item: str) -> str:
        if item.isdigit():
            return doc_so_nguyen(int(item))
        if item == "Ba":
            return "ba ri"
        if item in NGUYEN_TO:
            return danh_van_chu_cai(item)
        return item

    def _thay_ct(m: re.Match) -> str:
        nonlocal da_doi
        raw = m.group(0)
        la_ct, clean, dien_tich = _phan_tich_cong_thuc(raw)
        if la_ct:
            doc = _doc_ct(clean)
            da_doi += 1
            return f"{doc} {dien_tich}".strip() if dien_tich else doc
        return raw

    # Xử lý theo từng câu theo nguyên tắc cấu trúc
    cac_cau = _tach_thanh_cau(t)
    cau_moi: list[str] = []

    for cau in cac_cau:
        # Chuyển đổi điện tích số mũ
        cau = cau.replace("³⁺", "3+").replace("²⁻", "2-").replace("⁺", "+").replace("⁻", "-")

        # Kiểm tra phương trình phản ứng
        co_phan_ung = False
        for ky_hieu, nghia in PHAN_UNG.items():
            if ky_hieu in cau:
                cau = cau.replace(ky_hieu, f" {nghia} ")
                da_doi += 1
                co_phan_ung = True

        if "phản ứng" in cau.lower():
            co_phan_ung = True

        if co_phan_ung:
            cau_thay, n_cong = re.subn(r"([A-Za-z0-9\)])\s+\+\s+([A-Za-z0-9\(])", r"\1 cộng \2", cau)
            if n_cong > 0:
                da_doi += n_cong
                cau = cau_thay

        # Kiểm tra công thức nhận được bằng cấu trúc
        co_cong_thuc = False
        for m in _UNG_VIEN_RE.finditer(cau):
            if _phan_tich_cong_thuc(m.group(0))[0]:
                co_cong_thuc = True
                break

        # «ngữ cảnh hoá học» = trong cùng câu có CÔNG THỨC nhận được bằng cấu trúc hoặc có phản ứng
        ngu_canh_hoa_hoc = co_cong_thuc or co_phan_ung

        if ngu_canh_hoa_hoc:
            # Thay thế các công thức cấu trúc
            cau = _UNG_VIEN_RE.sub(_thay_ct, cau)

            # Ký hiệu nguyên tố đứng một mình chỉ đọc theo hoá học khi cùng câu có công thức
            if co_phan_ung:
                def _thay_pt(m: re.Match) -> str:
                    nonlocal da_doi
                    sym = m.group(0)
                    if sym in NGUYEN_TO and not _la_ten_nguoi_hoac_tu_thuong(cau, m.start(), sym):
                        da_doi += 1
                        return danh_van_chu_cai(sym)
                    return sym

                cau = re.sub(r"\b[A-Z][a-z]?\b", _thay_pt, cau)
            else:
                def _thay_ba(m: re.Match) -> str:
                    nonlocal da_doi
                    if not _la_ten_nguoi_hoac_tu_thuong(cau, m.start(), "Ba"):
                        da_doi += 1
                        return "ba ri"
                    return m.group(0)

                cau = re.sub(r"\bBa\b", _thay_ba, cau)

        cau_moi.append(cau)

    return "".join(cau_moi), da_doi


def _xu_ly_viet_tat(t: str) -> tuple[str, list[dict[str, Any]], int]:
    """Xử lý từ viết tắt: 1 nghĩa, nhiều nghĩa (mo_ho), và viết tắt lạ đánh vần."""
    da_doi = 0
    mo_ho: list[dict[str, Any]] = []

    # 1. Viết tắt nhiều nghĩa: thu thập mo_ho và mặc định đánh vần (dùng re.sub một lượt, vị trí tính theo chuỗi gốc của bước)
    mo_ho_keys = sorted(VIET_TAT_MO_HO.keys(), key=len, reverse=True)
    pattern_mo_ho = rf"\b({'|'.join(re.escape(k) for k in mo_ho_keys)})\b"

    def _thay_mo_ho(m: re.Match) -> str:
        nonlocal da_doi
        vt = m.group(1)
        truoc = m.string[max(0, m.start() - 5):m.start()]
        if vt == "BTC" and re.search(r"\d\s*$", truoc):
            return m.group(0)
        mo_ho.append({
            "vi_tri": [m.start(), m.end()],
            "chuoi": vt,
            "cac_nghia": list(VIET_TAT_MO_HO[vt]),
        })
        da_doi += 1
        return danh_van_chu_cai(vt)

    t = re.sub(pattern_mo_ho, _thay_mo_ho, t)

    # 2. Viết tắt 1 nghĩa đã biết (sắp xếp theo độ dài giảm dần, quét một lượt bằng regex)
    sorted_vt = sorted(VIET_TAT_MOT_NGHIA.keys(), key=len, reverse=True)
    pattern_mot_nghia = rf"(?<![\w])({'|'.join(re.escape(k) for k in sorted_vt)})(?![\w])"

    def _thay_mot_nghia(m: re.Match) -> str:
        nonlocal da_doi
        w = m.group(1)
        da_doi += 1
        return VIET_TAT_MOT_NGHIA[w]

    t = re.sub(pattern_mot_nghia, _thay_mot_nghia, t)

    # 3. Viết tắt lạ đánh vần: các từ IN HOA 2-5 ký tự chưa biết (API, SDK, CPU...)
    def _thay_viet_tat_la(m: re.Match) -> str:
        nonlocal da_doi
        w = m.group(0)
        if chuyen_so_la_ma(w) is not None:
            return w
        da_doi += 1
        return danh_van_chu_cai(w)

    t = re.sub(r"\b[A-ZĐ]{2,5}\b", _thay_viet_tat_la, t)

    return t, mo_ho, da_doi


def chuan_hoa_doc(van_ban: str, tuy_chon: dict[str, Any] | None = None) -> dict[str, Any]:
    """HỢP ĐỒNG: công cụ chuan_hoa_doc(van_ban, tuy_chon).

    Trả về:
        {
            "van_ban_doc": str,
            "mo_ho": list[{"vi_tri": [int, int], "chuoi": str, "cac_nghia": list[str]}],
            "da_doi": int,
        }
    """
    if not isinstance(van_ban, str):
        raise TypeError("van_ban phải là chuỗi ký tự (str)")

    if len(van_ban) > TRAN_KY_TU:
        raise ValueError(f"Văn bản vượt quá giới hạn 20.000 ký tự (nhận {len(van_ban)})")

    tuy_chon = tuy_chon or {}
    t = unicodedata.normalize("NFC", van_ban)
    tong_da_doi = 0

    # Lớp 8: Hậu xử lý TTS (emoji, Markdown, LaTeX)
    t, n8 = _xu_ly_hau_ky_tts(t)
    tong_da_doi += n8

    # Lớp 1: Gạch đầu dòng
    t, n1 = _xu_ly_gach_dau_dong(t)
    tong_da_doi += n1

    # Lớp 7: Tiêu đề in hoa, URL, Email, tên viết tắt có chấm
    t, n7_u = _xu_ly_url_va_email(t)
    t, n7_t = _xu_ly_tieu_de_in_hoa(t)
    t, n7_c = _xu_ly_ten_viet_tat_cham(t)
    tong_da_doi += n7_u + n7_t + n7_c

    # Lớp 4: Số điện thoại, ngày tháng, khoảng số, tỉ số, số La Mã
    t, n4_dt = _xu_ly_so_dien_thoai(t)
    t, n4_k = _xu_ly_khoang_va_ti_so(t)
    t, n4_lm = _xu_ly_so_la_ma(t)
    tong_da_doi += n4_dt + n4_k + n4_lm

    # Tiền tệ (chuyển ký hiệu tiền trước khi phân tách hàng nghìn số)
    t, n_tien = _xu_ly_tien_te(t)
    tong_da_doi += n_tien

    # Lớp 2: Phân tách hàng nghìn
    t, n2_hn = _xu_ly_phan_tach_hang_nghin(t)
    tong_da_doi += n2_hn

    # Lớp 3 & Lớp 2: Đơn vị đo tập đóng & số thập phân đi kèm đơn vị & số âm
    t, n3 = _xu_ly_don_vi_va_thap_phan(t)
    t, n_am = _xu_ly_so_am(t)
    tong_da_doi += n3 + n_am

    # Lớp 6: Công thức hoá học
    t, n6 = _xu_ly_hoa_hoc(t)
    tong_da_doi += n6

    # Lớp 5: Viết tắt (1 nghĩa, nhiều nghĩa, đánh vần lạ)
    t, mo_ho, n5 = _xu_ly_viet_tat(t)
    tong_da_doi += n5

    # Làm sạch khoảng trắng dư thừa
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s+", "\n", t)
    t = t.strip()

    return {
        "van_ban_doc": t,
        "mo_ho": mo_ho,
        "da_doi": tong_da_doi,
    }
