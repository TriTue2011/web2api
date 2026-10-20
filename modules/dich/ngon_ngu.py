"""Mã ngôn ngữ + tên → mã + nhận diện ngôn ngữ bằng heuristic.

NGUỒN: ``detect()`` (phần heuristic ``_LATIN_NGAN``), ``chon_dich_sang()`` và
``giai_ma_target()`` port từ ``services/translate_service.py`` tại commit
d879b03 của kho c2a (bản sao chỉ-đọc ``~/workspace/c2a-nv146/``), đổi tên cho
hợp không gian tên module. KHÔNG sửa c2a.

KHÔNG port phần gọi máy chủ của c2a (``/detect``, ``/languages`` của
LibreTranslate) vì module này dịch qua model của lõi web2api — model tự xử lý
ngôn ngữ, không có tập ngôn ngữ cố định để tra. Heuristic chỉ để:
- gán nhãn ``ngon_ngu_nguon`` khi người dùng không chỉ định,
- chọn ngôn ngữ đích mặc định (Việt ↔ Anh).
"""

from __future__ import annotations

import re

VI = "vi"
EN = "en"

#: Tên tiếng → mã ISO. Người dùng có thể gõ "tiếng trung", "English", ...
_TEN_TO_MA: dict[str, str] = {
    "tiếng việt": "vi", "vietnamese": "vi",
    "tiếng anh": "en", "english": "en",
    "tiếng trung": "zh", "chinese": "zh", "tiếng hoa": "zh",
    "tiếng nhật": "ja", "japanese": "ja",
    "tiếng hàn": "ko", "korean": "ko",
    "tiếng pháp": "fr", "french": "fr",
    "tiếng đức": "de", "german": "de",
    "tiếng tây ban nha": "es", "spanish": "es",
    "tiếng nga": "ru", "russian": "ru",
    "tiếng thái": "th", "thai": "th",
    "tiếng lào": "lo", "lao": "lo",
    "tiếng khmer": "km", "khmer": "km",
    "tiếng indonesia": "id", "indonesian": "id",
    "tiếng malaysia": "ms", "malay": "ms",
}

#: Bí danh mã → mã chuẩn dùng cho model.
_BI_DANH_MA: dict[str, str] = {
    "zh-hans": "zh", "zh-cn": "zh", "zh-sg": "zh",
    "zh-hant": "zh-hant", "zh-tw": "zh-hant", "zh-hk": "zh-hant",
    "pt-br": "pt", "en-us": "en", "en-gb": "en",
}

#: Đoạn Latin ngắn (một-hai từ) — port từ ``_LATIN_NGAN`` của c2a. Bộ dò thống
#: kê đoán BẬY ở độ dài này ("stroke"→sk, "hello"→fi), nên nhận thẳng "en".
_LATIN_NGAN = re.compile(r"^[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*)?$")

#: Dấu tiếng Việt — phát hiện không cần model.
_VIET = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡ"
    r"ùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE)

_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def chuan_ma(ma: str) -> str:
    """Tên/mã người dùng gõ → mã ISO cho model. Mã lạ đi thẳng cho model tự lo."""
    s = str(ma or "").strip().lower()
    if not s:
        return ""
    if s in _TEN_TO_MA:
        return _TEN_TO_MA[s]
    if s in _BI_DANH_MA:
        return _BI_DANH_MA[s]
    return s


def detect(text: str) -> tuple[str, float]:
    """(mã ngôn ngữ, độ tự tin 0..100) bằng heuristic thuần Python, không mạng.

    Port phần heuristic của ``detect()`` c2a; phần gọi ``/detect`` của máy chủ
    LibreTranslate KHÔNG port vì module này không có máy chủ dịch riêng.
    """
    t = (text or "").strip()
    if not t:
        return "", 0.0
    if _LATIN_NGAN.match(t):
        return EN, 100.0
    if _VIET.search(t):
        return VI, 95.0
    if _KANA.search(t):
        return "ja", 80.0
    if _HANGUL.search(t):
        return "ko", 80.0
    if _CJK.search(t):
        return "zh", 80.0
    if _CYRILLIC.search(t):
        return "ru", 80.0
    return "", 0.0


def chon_dich_sang(nguon: str) -> str:
    """Ngôn ngữ đích khi người dùng KHÔNG chỉ định. (port từ c2a)

    Tiếng Việt → tiếng Anh; mọi thứ khác → tiếng Việt. Quy tắc DUY NHẤT.
    """
    return EN if str(nguon or "").lower() == VI else VI


def giai_ma_target(nguon: str, target: str = "") -> str:
    """Mã đích từ lựa chọn người dùng + ngôn ngữ nguồn ĐÃ nhận diện. (port từ c2a)

    - ``""``       → cặp Việt ↔ Anh mặc định.
    - ``"cap:zh"`` → nguồn tiếng Việt thì sang ``zh``, còn lại về ``vi``.
    - mã trơ (``"fr"``) → giữ nguyên.
    """
    t = str(target or "").strip().lower()
    if not t:
        return chon_dich_sang(nguon)
    if t.startswith("cap:"):
        kia = t[4:] or EN
        return kia if str(nguon or "").lower() == VI else VI
    return chuan_ma(t) or t
