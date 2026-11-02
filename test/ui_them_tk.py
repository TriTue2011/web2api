"""Hàm dùng chung cho test giao diện «＋ Thêm tài khoản» của Hệ thống → AI → Tài khoản (NV282: MỘT form, hiện dần).

Form: bước 1 chọn nhà (`[data-nhom-them]`, bỏ khi chỉ có một nhà); bước 2 chọn cách thêm (`[data-cach-them="<module>|dn"` hoặc
`"<module>|api"]`, bỏ khi nhà chỉ có một cách); bước 3 chỉ các ô cần nhập. Mọi ô phụ nằm trong «Tuỳ chọn thêm» (`#tuy-chon-them`).
"""
from __future__ import annotations

import time


def mo_form(trang, han: float = 15):
    trang.cho("document.getElementById('nut-them')", han=han, mo_ta="nút ＋ Thêm tài khoản")
    if not trang.js("!!document.querySelector('#form-them')"):
        trang.bam("document.getElementById('nut-them')")
    trang.cho("!!document.querySelector('#form-them')", han=5, mo_ta="form thêm tài khoản")


def chon_nha(trang, nhom: str):
    """Bấm chip nhà nếu có (nhà duy nhất thì đã tự chọn, không có chip)."""
    sel = f"document.querySelector('[data-nhom-them=\"{nhom}\"]')"
    if trang.js(f"!!{sel}"):
        trang.bam(sel, f"chip nhà {nhom}")
        time.sleep(0.2)


def chon_cach(trang, kieu: str, module: str = ""):
    """kieu = «dn» (đăng nhập) hoặc «api» (khoá API). Nhà chỉ có một cách thì không có chip, không cần bấm."""
    mau = f'[data-cach-them="{module}|{kieu}"]' if module else f'[data-cach-them$="|{kieu}"]'
    sel = f"document.querySelector('{mau}')"
    if trang.js(f"!!{sel}"):
        trang.bam(sel, f"cách thêm {kieu}")
    time.sleep(0.2)
    if kieu == "dn":
        trang.cho("!!document.getElementById('khung-tu-dong')", han=5, mo_ta="khung đăng nhập (#khung-tu-dong)")


def mo_dan_nhieu_dong(trang):
    """Mở «Tuỳ chọn thêm» rồi «Dán nhiều dòng» (hai mục thu gọn lồng nhau, đóng sẵn)."""
    trang.js("(() => { const t = document.querySelector('#tuy-chon-them'); if (t && !t.open) t.querySelector('summary').click(); return !!t; })()")
    trang.cho("document.querySelector('#tuy-chon-them') && document.querySelector('#tuy-chon-them').open", han=5, mo_ta="Tuỳ chọn thêm mở")
    trang.js("(() => { const d = document.querySelector('#o-tu-dong').closest('details'); if (d && !d.open) d.querySelector('summary').click(); return true; })()")
    trang.cho("document.querySelector('#o-tu-dong').closest('details').open", han=5, mo_ta="Dán nhiều dòng mở")
