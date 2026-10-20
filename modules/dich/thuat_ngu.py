"""Thuật ngữ người dùng tự thêm (bảng sửa tay) — tôn trọng ở mọi lượt dịch chữ.

NGUỒN: ``thuong_hoa()``, ``co_trong()``, ``tach_thuat_ngu()``, ``ghi_sua()``,
``xoa_sua()``, ``cap_nguoi_dung()`` (bảng "sửa tay" ``<src>.sua.json``) port từ
``services/thuat_ngu.py`` tại commit d879b03 của kho c2a (bản sao chỉ-đọc
``~/workspace/c2a-nv146/``). KHÔNG sửa c2a.

KHÔNG port từ c2a: bản CURATED ``<src>.json`` + bản TỰ HỌC ``<src>.hoc.json`` +
``doan_linh_vuc()`` + ``hau_ky_thuat_ngu()`` — chúng thuộc pipeline dịch phụ đề
(NLLB dịch thô → hậu kỳ thay thuật ngữ theo lĩnh vực đoán được), không thuộc
đường dịch chữ qua model của lõi. Ở đây chỉ cần đúng tầng "sửa tay": người dùng
gõ tay ở hành động ``thuat_ngu_them``, áp thẳng không qua đoán lĩnh vực.

Khác c2a: hàm đọc/ghi nhận ``thu_muc`` (thư mục dữ liệu do module truyền vào)
thay vì đọc ``services.config.DATA_DIR``; tên tệp ``thuat_ngu-<src>.sua.json``
(thêm tiền tố ``thuat_ngu-`` để không lẫn với kho khác trong cùng thư mục).
``src`` do người dùng truyền nên bị siết bằng regex (chống đi ngược thư mục).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

log = logging.getLogger("web2api.dich.thuat_ngu")

#: src hợp lệ: mã tiếng viết thường. Chặn "../" và ký tự lạ.
_SRC_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")

#: Trần độ dài để một term không phình tệp 0600.
_TRAN_TERM = 200
_TRAN_VI = 500
_TRAN_LINH_VUC = 50


def thuong_hoa(s: str) -> str:
    """Thường hoá một cụm để so khớp: bỏ dấu câu rìa, gộp khoảng trắng, hạ chữ.

    KHÔNG bỏ dấu tiếng Việt; chỉ chuẩn hoá Unicode về NFC. (port từ c2a)
    """
    s = unicodedata.normalize("NFC", str(s or ""))
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _tep(thu_muc: Path, src: str) -> Path:
    src = str(src or "").lower().strip()
    if not _SRC_HOP_LE.match(src):
        raise ValueError(f"mã tiếng nguồn không hợp lệ: {src!r}")
    return Path(thu_muc) / f"thuat_ngu-{src}.sua.json"


def doc(thu_muc: Path, src: str) -> dict[str, dict[str, str]]:
    """Đọc bảng sửa tay → {lĩnh vực: {term đã thường hoá: thuật ngữ VI}}.

    Thiếu tệp trả rỗng; tệp hỏng trả rỗng (ghi cảnh báo), không ném ra ngoài.
    """
    tep = _tep(thu_muc, src)
    try:
        raw = json.loads(tep.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.warning("thuật ngữ %s hỏng, bỏ qua: %s", tep.name, str(exc)[:200])
        return {}
    goi: dict[str, dict[str, str]] = {}
    if isinstance(raw, dict):
        for linh_vuc, cap in raw.items():
            if not isinstance(cap, dict):
                continue
            goi[str(linh_vuc)] = {
                thuong_hoa(k): str(v)
                for k, v in cap.items()
                if str(k).strip() and str(v).strip()
            }
    return goi


def _ghi_tep(thu_muc: Path, src: str, bang: dict[str, dict[str, str]]) -> None:
    """Ghi nguyên tử, quyền 0600, thư mục cha 0700."""
    tep = _tep(thu_muc, src)
    tep.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tam = tep.with_suffix(".tmp")
    tam.write_text(
        json.dumps({lv: cap for lv, cap in bang.items() if cap},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tam.chmod(0o600)
    tam.replace(tep)


def ghi_sua(thu_muc: Path, src: str, linh_vuc: str, tu_goc: str, tu_viet: str) -> None:
    """Thêm/ghi đè một sửa tay. (port từ c2a, thêm trần độ dài)"""
    lv = str(linh_vuc or "").strip()
    term_n = thuong_hoa(tu_goc)
    vi_s = str(tu_viet or "").strip()
    if not (lv and term_n and vi_s):
        raise ValueError("thiếu lĩnh vực / từ gốc / từ Việt")
    if len(lv) > _TRAN_LINH_VUC or len(term_n) > _TRAN_TERM or len(vi_s) > _TRAN_VI:
        raise ValueError("lĩnh vực / từ gốc / từ Việt quá dài")
    bang = doc(thu_muc, src)
    bang.setdefault(lv, {})[term_n] = vi_s
    _ghi_tep(thu_muc, src, bang)


def xoa_sua(thu_muc: Path, src: str, linh_vuc: str, tu_goc: str) -> bool:
    """Bỏ một sửa tay. Trả True nếu có xoá. (port từ c2a)"""
    lv = str(linh_vuc or "").strip()
    term_n = thuong_hoa(tu_goc)
    bang = doc(thu_muc, src)
    if term_n not in bang.get(lv, {}):
        return False
    del bang[lv][term_n]
    _ghi_tep(thu_muc, src, bang)
    return True


def liet_ke(thu_muc: Path, src: str, linh_vuc: str) -> list[dict[str, str]]:
    """[{tu_goc, tu_viet, linh_vuc}] của MỘT lĩnh vực (chỉ tầng sửa tay)."""
    lv = str(linh_vuc or "").strip()
    cap = doc(thu_muc, src).get(lv, {})
    return [{"tu_goc": t, "tu_viet": v, "linh_vuc": lv}
            for t, v in sorted(cap.items())]


def cap_nguoi_dung(thu_muc: Path, src: str) -> list[tuple[str, str]]:
    """[(term nguồn, thuật ngữ VI)] người dùng TỰ THÊM cho tiếng ``src``.

    Gộp mọi lĩnh vực (lúc áp không hỏi lĩnh vực nữa); trùng term giữa hai lĩnh
    vực thì bản đọc sau thắng. (port từ c2a ``cap_nguoi_dung``)
    """
    gop: dict[str, str] = {}
    for lv in sorted(doc(thu_muc, src)):
        for term, vi in doc(thu_muc, src)[lv].items():
            if term and vi:
                gop[term] = vi
    return list(gop.items())


#: Mẫu khớp-nguyên-từ đã biên dịch, theo term. (port từ c2a)
_MAU_CA_TU: dict[str, re.Pattern[str] | None] = {}
_LA_LATIN = re.compile(r"[a-z0-9][a-z0-9\s\-'.&/]*", re.ASCII)


def co_trong(term: str, hay: str) -> bool:
    """``term`` (đã thường hoá) có xuất hiện trong ``hay`` (đã thường hoá) không.

    Term Latin khớp NGUYÊN TỪ (tránh "la" nằm trong "player"); term
    Nhật/Trung (viết không cách từ) khớp chuỗi con. (port từ c2a)
    """
    if term not in _MAU_CA_TU:
        _MAU_CA_TU[term] = (
            re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")
            if _LA_LATIN.fullmatch(term) else None)
    mau = _MAU_CA_TU[term]
    return bool(mau.search(hay)) if mau is not None else term in hay


def tach_thuat_ngu(text: str, cap: list[tuple[str, str]]) -> list[tuple[bool, str]]:
    """Cắt đoạn thành [(gửi_model, chữ)] — thuật ngữ thành mảnh KHOÁ đã mang sẵn
    bản dịch bắt buộc, phần còn lại để model lo. (port nguyên từ c2a)

    Khớp không phân biệt hoa thường, ưu tiên cụm DÀI trước; chữ hoa đầu câu của
    bản gốc được giữ sang bản dịch bắt buộc.
    """
    if not cap or not text:
        return [(True, text)] if text else []
    thu_tu = sorted(cap, key=lambda c: len(c[0]), reverse=True)
    mau = "|".join(re.escape(goc) for goc, _ in thu_tu)
    rx = re.compile(rf"(?<!\w)({mau})(?!\w)", re.IGNORECASE)
    tra = {thuong_hoa(goc): dich for goc, dich in thu_tu}
    ra: list[tuple[bool, str]] = []
    vt = 0
    text = unicodedata.normalize("NFC", text)
    for m in rx.finditer(text):
        if m.start() > vt:
            ra.append((True, text[vt:m.start()]))
        thay = tra[thuong_hoa(m.group(1))]
        if m.group(1)[:1].isupper() and thay[:1].islower():
            thay = thay[0].upper() + thay[1:]
        ra.append((False, thay))
        vt = m.end()
    if vt < len(text):
        ra.append((True, text[vt:]))
    return ra
