"""NV106 — test TĨNH cho khối @media (pointer:coarse) trong web/khung.css.

Không cần Chrome: phân tích văn bản CSS, khẳng định khối cảm ứng NV106 đặt
  - min-height:44px + min-width:44px cho bộ chọn cấu trúc
    (button, a[href], [role=button], [role=tab], summary, select) — adapt mục 1;
  - font-size:16px cho ô nhập (thắng style nội tuyến nên có !important) — adapt mục 2;
  - chữ phụ ≥ 12px cho nhãn thanh dưới điện thoại và nhãn ngôn ngữ khối mã.

ĐỘT BIẾN: xoá khối @media (pointer:coarse) mới của NV106 → test này phải ĐỎ.
(Các khối cảm ứng cũ ở dòng 63/184 không đặt min-height:44px cho cả bộ chọn
nên không làm test xanh nhầm.)
"""

from __future__ import annotations

import re
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
KHUNG_CSS = GOC / "web" / "khung.css"

BO_CHON_BAM = ["button", "a[href]", "[role=button]", "[role=tab]", "summary", "select"]


def _khoi_coarse(css: str) -> list[str]:
    """Mọi nội dung trong @media (pointer:coarse){ ... } (xử lý ngoặc lồng nhau)."""
    khoi = []
    for m in re.finditer(r"@media\s*\(\s*pointer\s*:\s*coarse\s*\)\s*\{", css):
        i, dem = m.end(), 1
        while dem and i < len(css):
            dem += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        khoi.append(css[m.end():i - 1])
    return khoi


def _quy_tac(than_khoi: str):
    """Danh sách (chuỗi bộ chọn, thân khai báo) trong một thân @media."""
    return [(m.group(1), m.group(2))
            for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", than_khoi)]


def test_coarse_dat_min_44px_cho_bo_chon_bam_duoc():
    css = KHUNG_CSS.read_text(encoding="utf-8")
    dat = False
    for khoi in _khoi_coarse(css):
        for bo_chon, than in _quy_tac(khoi):
            if "min-height" in than and "44px" in than and "min-width" in than:
                if all(bc in bo_chon for bc in BO_CHON_BAM):
                    dat = True
    assert dat, ("khung.css thiếu quy tắc @media (pointer:coarse) đặt "
                "min-height/min-width:44px cho button, a[href], [role=button], "
                "[role=tab], summary, select")


def test_coarse_o_nhap_16px_thang_style_noi_tuyen():
    css = KHUNG_CSS.read_text(encoding="utf-8")
    dat = False
    for khoi in _khoi_coarse(css):
        for bo_chon, than in _quy_tac(khoi):
            if ("font-size" in than and "16px" in than and "!important" in than
                    and "input" in bo_chon and "select" in bo_chon and "textarea" in bo_chon):
                dat = True
    assert dat, ("khung.css thiếu quy tắc @media (pointer:coarse) đặt font-size:16px "
                "!important cho input/select/textarea (thắng style nội tuyến 13/14px "
                "trong các .dc.html)")


def test_coarse_chu_phu_khong_duoi_12px():
    css = KHUNG_CSS.read_text(encoding="utf-8")
    dat_bn = dat_md = False
    for khoi in _khoi_coarse(css):
        for bo_chon, than in _quy_tac(khoi):
            m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", than)
            if not m or float(m.group(1)) < 12:
                continue
            if ".bn" in bo_chon and ("a" in bo_chon or "button" in bo_chon):
                dat_bn = True
            if "pre" in bo_chon:
                dat_md = True
    assert dat_bn, "khung.css thiếu quy tắc nâng nhãn thanh dưới điện thoại (.bn) lên ≥ 12px"
    assert dat_md, "khung.css thiếu quy tắc nâng nhãn ngôn ngữ khối mã (.md pre::before) lên ≥ 12px"
