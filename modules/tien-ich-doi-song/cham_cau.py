"""Phục hồi DẤU CÂU cho lời thoại chép máy không có dấu.

Port từ kho CỦA CHÍNH CHỦ MÁY (LUẬT PORT 19/10, chỉ chép không viết lại):
  nguồn: /opt/claude-c2a/chatgpt2api/services/cham_cau.py
  commit: d879b03 (cây CHỈ ĐỌC, không sửa c2a)

Vì sao cần (giữ nguyên lời c2a): phụ đề TỰ SINH của YouTube là chữ trần, không
một dấu chấm nào — đo thật 29/08/2026 trên một video 9 phút: 4253 ký tự,
**0** dấu ``. ! ? …``. Chấm câu lại ở ĐẦU đường ống thì các bước gộp câu làm
đúng việc của chúng.

Chỉnh so với c2a: KHÔNG có (chép nguyên 100% — hàm `goi_model(model, messages)`
đã được c2a thiết kế để caller TIÊM vào, tách vậy để test không chạm mạng/GPU;
ở web2api caller là tác nhân/tro-ly với model của lõi).

An toàn (giữ nguyên): model CHỈ được thêm dấu. Bản trả về bị soi lại từng chữ —
lệch một chữ là bỏ cả lô đó và giữ nguyên bản gốc, vì chữ sai làm hỏng phụ đề
còn nặng hơn thiếu dấu câu.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

log = logging.getLogger("web2api.tien_ich_doi_song.cham_cau")

GoiModel = Callable[[str, list[dict]], str]

#: Ký tự KẾT một câu — KHÔNG gồm dấu phẩy.
_KET_CAU = ".!?…。！？؟।"
#: Bản chữ có ít hơn MỘT dấu kết câu trên ngần này ký tự thì coi như chép máy
#: không dấu. Phụ đề người làm dày hơn nhiều (đo trên phụ đề thật: ~70 ký tự
#: một dấu), nên biên này rộng và không bắt nhầm.
KY_TU_MOI_DAU_CAU = 400
#: Dưới ngần này ký tự thì không xét: một clip vài câu có thể không có dấu nào
#: mà vẫn chẳng có gì để sửa.
KY_TU_TOI_THIEU = 200
#: Ký tự mỗi lượt gọi model. Cắt tại ranh giới MẢNH phụ đề, không cắt giữa
#: mảnh. Lô lớn hơn thì model hay bỏ sót chữ, mà sót chữ là mất cả lô.
KY_TU_MOI_LO = 1800
#: Số chữ ở HAI MÉP lô mà model được phép bỏ. Lô cắt theo số ký tự nên hay mở
#: hoặc đóng bằng một chữ cụt; model được dặn viết cho ra câu nên nó gạt chữ
#: cụt đó đi. Chữ bị bỏ được chép lại NGUYÊN VĂN (không có dấu câu), nên nới
#: chỗ này không làm mất chữ nào — chỉ hai mép là chưa chấm.
CHU_MEP_TOI_DA = 2

_MA_KHOI = re.compile(r"^```[a-zA-Z]*|```$", re.MULTILINE)


class LoiChamCau(Exception):
    """Model trả lỗi / không gọi được — caller bọc ``call_model`` raise cái này."""


def thieu_dau_cau(cac_manh: list[str]) -> bool:
    """Bản chép này có phải chữ trần không dấu câu không?"""
    chu = " ".join(str(x or "").strip() for x in cac_manh).strip()
    if len(chu) < KY_TU_TOI_THIEU:
        return False
    so_dau = sum(chu.count(k) for k in _KET_CAU)
    return so_dau == 0 or len(chu) / so_dau > KY_TU_MOI_DAU_CAU


def _khoa(tu: str) -> str:
    """Chữ rút về dạng SO SÁNH ĐƯỢC: bỏ mọi dấu câu, hạ chữ thường.

    Giữ nguyên dấu tiếng Việt (``\\w`` theo unicode) — chỉ gạt đi thứ mà model
    được phép thêm.
    """
    return re.sub(r"[^\w]", "", tu, flags=re.UNICODE).lower()


def _tim_khop(ra: list[str], goc: list[str]) -> int:
    """Vị trí mà dãy chữ ``goc`` bắt đầu trong dãy chữ ``ra``; -1 nếu không có.

    Có bước dò này để một câu mở đầu thừa của model ("Đây là bản đã chấm câu:")
    không làm hỏng cả lô — phần thân vẫn dùng được.
    """
    if not goc or len(ra) < len(goc):
        return -1
    dau = goc[0]
    for i in range(len(ra) - len(goc) + 1):
        if ra[i] == dau and ra[i:i + len(goc)] == goc:
            return i
    return -1


def _theo_manh(goc: list[str], tu: list[str]) -> list[str]:
    """Dãy chữ phẳng → rải lại về đúng số mảnh như ``goc``."""
    ra: list[str] = []
    vi_tri = 0
    for m in goc:
        n = len(str(m or "").split())
        ra.append(" ".join(tu[vi_tri:vi_tri + n]))
        vi_tri += n
    return ra


def _rai_lai(goc: list[str], raw: str) -> list[str] | None:
    """Bản đã chấm câu → trả về ĐÚNG số mảnh như ``goc``, giữ nguyên chữ.

    ``None`` nghĩa là model đã đổi chữ ở GIỮA (thêm/bớt/đổi/đảo) — caller giữ
    bản gốc. Bỏ tối đa ``CHU_MEP_TOI_DA`` chữ ở hai mép thì vẫn nhận, và chữ bị
    bỏ được chép lại nguyên văn.
    """
    tu_goc = [t for m in goc for t in str(m or "").split()]
    if not tu_goc:
        return None
    tu_ra = _MA_KHOI.sub("", raw or "").split()
    khoa_goc = [_khoa(t) for t in tu_goc]
    khoa_ra = [_khoa(t) for t in tu_ra]
    # Thử khớp TRỌN trước; không được mới lần lượt tha hai mép.
    for bo_dau in range(CHU_MEP_TOI_DA + 1):
        for bo_cuoi in range(CHU_MEP_TOI_DA - bo_dau + 1):
            n = len(khoa_goc) - bo_dau - bo_cuoi
            if n < 1:
                continue
            i = _tim_khop(khoa_ra, khoa_goc[bo_dau:bo_dau + n])
            if i >= 0:
                return _theo_manh(goc, tu_goc[:bo_dau] + tu_ra[i:i + n]
                                  + tu_goc[bo_dau + n:])
    return None


def _chia_lo(cac_manh: list[str]) -> list[list[int]]:
    """Chia chỉ số mảnh thành các lô ≤ ``KY_TU_MOI_LO`` ký tự."""
    lo: list[list[int]] = []
    hien: list[int] = []
    dai = 0
    for i, m in enumerate(cac_manh):
        n = len(str(m or ""))
        if hien and dai + n > KY_TU_MOI_LO:
            lo.append(hien)
            hien, dai = [], 0
        hien.append(i)
        dai += n + 1
    if hien:
        lo.append(hien)
    return lo


def phuc_hoi(cac_manh: list[str], model: str, goi_model: GoiModel,
             moi_lo: Callable[[int, int], None] | None = None) -> list[str]:
    """Các mảnh lời thoại không dấu → chính các mảnh đó, đã chấm câu.

    Trả về danh sách CÙNG ĐỘ DÀI, cùng thứ tự, cùng chữ — chỉ khác dấu câu và
    chữ hoa đầu câu. Lô nào model làm hỏng thì lô đó giữ nguyên bản gốc; không
    bao giờ tệ hơn đầu vào.

    ``moi_lo(so_lo, tong_lo)`` được gọi trước mỗi lượt để caller báo tiến độ:
    phim dài có hàng chục lô, im lặng cả phút thì người dùng tưởng máy treo.
    """
    if not cac_manh or not model:
        return list(cac_manh)
    system = (
        "Bạn là biên tập viên phụ đề. Văn bản dưới đây là lời thoại chép máy, "
        "KHÔNG có dấu câu. Việc của bạn là chèn dấu câu (chấm, phẩy, hỏi, "
        "than, ba chấm) và viết hoa đầu câu.\n"
        "TUYỆT ĐỐI KHÔNG được thêm chữ, bớt chữ, đổi chữ, đảo thứ tự chữ, "
        "dịch, tóm tắt hay giải thích. Số lượng và thứ tự các từ phải giữ "
        "nguyên y hệt bản gốc.\n"
        "Chỉ trả về đúng đoạn văn bản đã chấm câu, không kèm gì khác."
    )
    ra = list(cac_manh)
    cac_lo = _chia_lo(cac_manh)
    for so_lo, lo in enumerate(cac_lo, 1):
        goc = [str(cac_manh[i] or "") for i in lo]
        chu = " ".join(x.strip() for x in goc if x.strip())
        if not chu:
            continue
        if moi_lo:
            try:
                moi_lo(so_lo, len(cac_lo))
            except Exception:
                pass
        try:
            raw = goi_model(model, [{"role": "system", "content": system},
                                    {"role": "user", "content": chu}])
        except Exception as exc:
            log.warning("chấm câu: model lỗi (%s) — giữ nguyên lô %d…%d",
                        str(exc)[:160], lo[0], lo[-1])
            continue
        moi = _rai_lai(goc, raw)
        if moi is None:
            log.warning("chấm câu: model đổi chữ — giữ nguyên lô %d…%d",
                        lo[0], lo[-1])
            continue
        for i, t in zip(lo, moi):
            ra[i] = t
    return ra
