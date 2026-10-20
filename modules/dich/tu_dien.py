"""Tra từ điển tại chỗ (không gọi mạng) — cho ô tra cứu bên cạnh bản dịch.

NGUỒN: ``_dang_goc()`` (quy tắc hình thái tối thiểu tiếng Anh), ``TU_LOAI``,
logic ``tra()`` và ``dong_tra_cho_chat()`` port từ ``services/tu_dien.py`` tại
commit d879b03 của kho c2a (bản sao chỉ-đọc ``~/workspace/c2a-nv146/``).
KHÔNG sửa c2a.

KHÁC c2a: kho SQLite ``<data>/tudien/en-vi.db`` (không ship cùng, cài bằng
script tải riêng) KHÔNG port — module này dùng mục từ do người dùng NHẬP qua
hành động ``tu_dien_nhap`` (JSONL, có trần), lưu tệp JSON 0600 trong dữ liệu
module. Mỗi mục: ``{"tu", "vi", "tu_loai", "vi_du", "ipa"}``. Lý do: không kéo
kho SQLite hàng chục MB vào patch, không phụ thuộc script tải ngoài.

Vì sao vẫn cần từ điển khi đã có model dịch: model phải CHỌN một nghĩa, còn
người tra một từ thường cần THẤY CẢ CÁC NGHĨA rồi tự chọn.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

log = logging.getLogger("web2api.dich.tu_dien")

#: Trần số nghĩa trả về. (port từ c2a)
TOI_DA_NGHIA = 15

#: Trần ký tự cho MỘT câu ví dụ trong khối tra cho chat. (port từ c2a)
VI_DU_TOI_DA = 120

#: Trần nhập từ điển: 20.000 mục / 2 MB nội dung.
TRAN_MUC_NHAP = 20_000
TRAN_KY_TU_NHAP = 2_000_000

#: Trần xuất từ điển: cùng 20.000 mục.
TRAN_MUC_XUAT = 20_000

#: Mã từ loại → nhãn tiếng Việt. Mã lạ giữ nguyên. (port từ c2a)
TU_LOAI: dict[str, str] = {
    "N": "danh từ", "noun": "danh từ", "n": "danh từ",
    "V": "động từ", "verb": "động từ",
    "A": "tính từ", "adj": "tính từ",
    "D": "trạng từ", "adv": "trạng từ",
    "idiom": "thành ngữ",
    "P": "giới từ", "C": "liên từ", "pron": "đại từ",
}

#: Mục từ hợp lệ cần có các khoá chuỗi này (tu, vi bắt buộc).
_KHOA_MUC = ("tu", "vi", "tu_loai", "vi_du", "ipa")


def _chuan(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or "")).strip().lower()


def _dang_goc(tu: str) -> list[str]:
    """Các dạng gốc có thể của một từ đã chia, theo thứ tự nên thử.

    Kho tra theo TỪ GỐC nên "strokes" tra thẳng là trượt. Bộ quy tắc hình thái
    tối thiểu cho tiếng Anh, cố ý không dùng thư viện lemmatize. (port từ c2a)
    """
    ra: list[str] = []

    def them(x: str) -> None:
        if x and len(x) > 1 and x not in ra:
            ra.append(x)

    if tu.endswith("ies") and len(tu) > 4:
        them(tu[:-3] + "y")
    if tu.endswith("es") and len(tu) > 3:
        them(tu[:-2])
    if tu.endswith("s") and not tu.endswith("ss"):
        them(tu[:-1])
    if tu.endswith("ing"):
        them(tu[:-3])
        them(tu[:-3] + "e")
    if tu.endswith("ed"):
        them(tu[:-2])
        them(tu[:-1])
    if tu.endswith("er") or tu.endswith("est"):
        them(tu[:-2] if tu.endswith("er") else tu[:-3])
    # Phụ âm gấp đôi trước đuôi: "stopped" → "stop", "running" → "run".
    m = re.match(r"^(.*?)([bcdfglmnprst])\2(?:ed|ing)$", tu)
    if m:
        them(m.group(1) + m.group(2))
    return ra


def _nghia_cua(muc: list[dict], dung: str) -> list[dict]:
    """Mọi nghĩa của ĐÚNG dạng chữ ``dung`` (đã chuẩn hoá)."""
    ra = []
    for m in muc:
        if _chuan(m.get("tu", "")) == dung:
            vi = str(m.get("vi", "")).strip()
            if not vi:
                continue
            ra.append({
                "vi": vi,
                "tu_loai": TU_LOAI.get(str(m.get("tu_loai", "")).strip(),
                                       str(m.get("tu_loai", "")).strip()),
                "vi_du": str(m.get("vi_du", "")).strip(),
            })
        if len(ra) >= TOI_DA_NGHIA:
            break
    return ra


def tra(muc: list[dict], tu: str) -> dict:
    """Tra một từ/cụm → nghĩa tiếng Việt, từ loại, câu ví dụ, IPA.

    Trả ``{"tu", "goc" (chỉ khi khác), "ipa", "nghia": [...]}``.
    Không tìm thấy → ``nghia`` rỗng. Không ném lỗi: tính năng phụ.
    (port logic từ ``tra()`` của c2a, nguồn dữ liệu đổi sang danh sách mục)
    """
    goc = _chuan(tu)
    ra: dict = {"tu": goc, "ipa": "", "nghia": []}
    if not goc or not muc:
        return ra
    try:
        nghia = _nghia_cua(muc, goc)
        dung = goc
        if not nghia:
            for ung_vien in _dang_goc(goc):
                nghia = _nghia_cua(muc, ung_vien)
                if nghia:
                    dung, ra["goc"] = ung_vien, goc
                    break
        if not nghia:
            return ra
        ipa = ""
        for m in muc:
            if _chuan(m.get("tu", "")) == dung and str(m.get("ipa", "")).strip():
                ipa = str(m["ipa"]).strip()
                break
        ra["tu"], ra["nghia"] = dung, nghia
        ra["ipa"] = ipa
    except Exception as exc:  # mục bẩn: ô tra cứu trống, không đứt lượt
        log.warning("tra từ điển '%s' lỗi: %s", goc, str(exc)[:200])
    return ra


def dong_tra_cho_chat(muc: list[dict], tu: str, *, toi_da: int = TOI_DA_NGHIA,
                      kem_vi_du: bool = True) -> str:
    """Khối chữ tra từ điển GỌN — rỗng nếu không tra được gì. (port từ c2a)

    Hiện ĐỦ số nghĩa tra được (kèm ví dụ cắt 120 ký tự): ví dụ mới là thứ phân
    biệt các nghĩa với nhau.
    """
    r = tra(muc, tu)
    if not r["nghia"]:
        return ""
    dau = r["tu"] + (f" {r['ipa']}" if r["ipa"] else "")
    if r.get("goc"):
        dau += f" (dạng gốc của “{r['goc']}”)"
    dong = [f"📖 {dau}"]
    for i, n in enumerate(r["nghia"][:toi_da], 1):
        tl = f"({n['tu_loai']}) " if n["tu_loai"] else ""
        dong.append(f"{i}. {tl}{n['vi']}")
        vd = (n.get("vi_du") or "").strip() if kem_vi_du else ""
        if vd:
            if len(vd) > VI_DU_TOI_DA:
                vd = vd[:VI_DU_TOI_DA - 1].rstrip() + "…"
            dong.append(f"   · {vd}")
    con = len(r["nghia"]) - toi_da
    if con > 0:
        dong.append(f"…và {con} nghĩa nữa")
    return "\n".join(dong)


def _chuan_muc(d: dict) -> dict | None:
    """Một dòng JSONL → mục từ chuẩn, hoặc None nếu dòng hỏng."""
    if not isinstance(d, dict):
        return None
    tu = str(d.get("tu", "")).strip()
    vi = str(d.get("vi", "")).strip()
    if not tu or not vi or len(tu) > 200 or len(vi) > 2000:
        return None
    return {
        "tu": tu,
        "vi": vi,
        "tu_loai": str(d.get("tu_loai", "")).strip()[:40],
        "vi_du": str(d.get("vi_du", "")).strip()[:500],
        "ipa": str(d.get("ipa", "")).strip()[:80],
    }


def nhap_jsonl(noi_dung: str) -> tuple[list[dict], int]:
    """Nội dung JSONL (mỗi dòng một mục) → (danh sách mục, số dòng bỏ qua).

    Có trần 20.000 mục / 2 MB. Trùng từ (theo dạng chuẩn hoá): giữ mục đầu.
    Vượt trần → ValueError.
    """
    noi_dung = str(noi_dung or "")
    if len(noi_dung) > TRAN_KY_TU_NHAP:
        raise ValueError(f"nội dung từ điển vượt trần {TRAN_KY_TU_NHAP} ký tự")
    muc: list[dict] = []
    thay: set[str] = set()
    bo_qua = 0
    for dong in noi_dung.splitlines():
        dong = dong.strip()
        if not dong:
            continue
        try:
            d = json.loads(dong)
        except Exception:
            bo_qua += 1
            continue
        m = _chuan_muc(d)
        if m is None:
            bo_qua += 1
            continue
        if len(muc) >= TRAN_MUC_NHAP:
            raise ValueError(f"từ điển vượt trần {TRAN_MUC_NHAP} mục")
        chuan = _chuan(m["tu"])
        if chuan in thay:
            continue  # trùng từ: giữ mục đầu, không tính là bỏ qua
        thay.add(chuan)
        muc.append(m)
    return muc, bo_qua


def xuat_jsonl(muc: list[dict]) -> str:
    """Danh sách mục → JSONL (tối đa 20.000 mục)."""
    dong = [json.dumps(m, ensure_ascii=False) for m in (muc or [])[:TRAN_MUC_XUAT]]
    return "\n".join(dong)
