"""Nhật ký chẩn đoán đăng nhập (đề nghị xin quyền 04/11/2026, Q-D).

Vòng đệm nhỏ TRONG BỘ NHỚ ghi các bước dịch vụ đã đi: điều hướng (tên miền + đường dẫn), bấm/điền kèm selector đã thử và kết quả.
Chủ máy / khách MCP đọc để biết đăng nhập hỏng ở bước nào thay vì đoán.

KHÔNG ghi: giá trị ô nhập, query và fragment của URL (OAuth mang login_hint/state/code), cookie, email (che trong selector và đường dẫn),
tiêu đề trang, tên hồ sơ. Chỉ nhận trường trong danh sách trắng ``TRUONG``; loại bước ngoài ``LOAI`` bị bỏ. Không ghi ra đĩa.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

TRAN_VONG = 300
TRAN_SELECTOR = 120
TRAN_URL = 160
LOAI = frozenset({"dieu_huong", "bam", "dien"})
KET_QUA = frozenset({"ok", "bam_duoc", "khong_thay", "loi"})
TRUONG = ("luc", "loai", "selector", "url", "ket_qua", "ma_loi")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+(?:@|%40)[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_DOAN_AN_TOAN = re.compile(r"[A-Za-z0-9_\-.]{0,19}")
_SCHEME_NOI_DUNG = frozenset({"data", "blob", "javascript", "vbscript"})
_DIEU_KHIEN = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_MA_LOI = re.compile(r"^[a-z0-9_]{1,64}$")

_vong: deque[dict[str, Any]] = deque(maxlen=TRAN_VONG)
_khoa = threading.Lock()


def lam_sach_url(url: Any) -> str:
    """Chỉ ``tên-miền/đường-dẫn``; đoạn đường dẫn dài ≥ 20 ký tự (id, mã) thay bằng «dai»; email bị che."""
    if not isinstance(url, str):
        return ""
    try:
        goc = urlsplit(url)
        if goc.scheme in _SCHEME_NOI_DUNG:
            return goc.scheme + ":«noi-dung»"  # data:/blob:/javascript: mang nội dung, không phải địa chỉ
        p = goc if "//" in url else urlsplit("//" + url)
    except ValueError:
        return ""
    host = p.hostname or ""
    # Giữ đoạn đường dẫn ngắn và chỉ chữ/số/_-. ; đoạn dài hoặc có ký tự lạ (=, %, ~, ;, JWT có dấu chấm dài) → «dai».
    doan = [d if _DOAN_AN_TOAN.fullmatch(d) else "«dai»" for d in (p.path or "").split("/")]
    r = _EMAIL.sub("«email»", host + "/".join(doan))
    return _DIEU_KHIEN.sub("", r)[:TRAN_URL]


def che_selector(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    return _EMAIL.sub("«email»", _DIEU_KHIEN.sub(" ", s))[:TRAN_SELECTOR]


def ghi(loai: str, **truong: Any) -> None:
    """Thêm một bước. Không bao giờ ném lỗi (nhật ký chẩn đoán không được làm hỏng đăng nhập)."""
    try:
        if loai not in LOAI:
            return
        muc: dict[str, Any] = {"luc": int(time.time()), "loai": loai}
        if "selector" in truong:
            muc["selector"] = che_selector(truong["selector"])
        if "url" in truong:
            muc["url"] = lam_sach_url(truong["url"])
        if truong.get("ket_qua") in KET_QUA:
            muc["ket_qua"] = truong["ket_qua"]
        ma = truong.get("ma_loi")
        if isinstance(ma, str) and _MA_LOI.fullmatch(ma):
            muc["ma_loi"] = ma
        with _khoa:
            _vong.append(muc)
    except Exception:
        pass


def doc(so_buoc: int = 50) -> list[dict[str, Any]]:
    """``so_buoc`` bước gần nhất, cũ trước mới sau."""
    n = max(1, min(int(so_buoc), TRAN_VONG))
    with _khoa:
        return [dict(m) for m in list(_vong)[-n:]]


def xoa() -> None:
    with _khoa:
        _vong.clear()


def gan_trang(page: Any) -> None:
    """Ghi mỗi lần KHUNG CHÍNH của trang điều hướng (khung con/quảng cáo bỏ)."""
    def _khi_dieu_huong(khung: Any) -> None:
        if getattr(khung, "parent_frame", None) is None:
            ghi("dieu_huong", url=getattr(khung, "url", ""), ket_qua="ok")
    try:
        page.on("framenavigated", _khi_dieu_huong)
    except Exception:
        pass


def gan_ngu_canh(ctx: Any) -> None:
    """Gắn bộ ghi vào MỌI trang của một context (trang có sẵn và trang mở sau) — một chỗ cho cả bốn nhà cung cấp."""
    try:
        for p in list(ctx.pages):
            gan_trang(p)
        ctx.on("page", gan_trang)
    except Exception:
        pass
