"""Điều phối đọc «mã xác minh qua email» khi đăng nhập (NV122).

Lõi KHÔNG tự đọc hộp thư: nó chỉ tìm module ĐANG CHẠY khai khả năng
`ma_xac_minh`, hỏi hộp thư của module đó, rồi đưa mã THẲNG cho module đăng
nhập trong cùng tiến trình. Mã không bao giờ đi qua HTTP, log, nhật ký,
trạng thái lượt hay thông báo lỗi (test NV122 quét cả bốn).

Hợp đồng module đọc (do module triển khai, NV121):
- manifest khai `cung_cap: ["ma_xac_minh"]`;
- `hop_thu_ra() -> [{"id", "email_che"}]` (đồng bộ; `id` nên là địa chỉ email
  để lõi khớp — lõi so email đã chuẩn hoá với `id`/`email` nếu có);
- `async lay_ma(yeu_cau) -> {"ok": True, "ma": ...} | {"ok": False, "ma_loi": ...}`
  với `yeu_cau = {"hop_thu", "tu_mien", "sau_luc", "do_dai", "cho_giay"}`.

Bảo mật:
- `tu_mien`/`do_dai` lấy từ MANIFEST của module đăng nhập (lõi truyền vào),
  KHÔNG bao giờ từ yêu cầu HTTP — nếu không đường này thành công cụ đọc
  thư tuỳ ý.
- Hạn dòng toàn cục: tối đa 6 lượt gọi `lay_ma` mỗi 60 giây.
- Mỗi tài khoản chỉ dùng được hộp thư trùng email của nó HOẶC hộp thư quản
  trị chỉ định rõ (do tầng gọi quyết `hop_thu`, lõi chỉ khớp đúng).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

from app import nhat_ky

log = logging.getLogger("web2api.ma_xac_minh")

#: Mã khả năng mà module đọc hộp thư khai trong manifest.
KHA_NANG = "ma_xac_minh"
KENH_NHAT_KY = "ma_xac_minh"

#: Hết giờ mặc định cho một lần đọc mã (giây). Test monkeypatch hằng này.
CHO_GIAY_MAC_DINH = 120
#: Hạn dòng toàn cục: tối đa lượt gọi lay_ma trong cửa sổ trượt 60 giây.
GIOI_HAN_LUOT_PHUT = 6
CUA_SO_GIAY = 60.0

_MAU_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_MAU_MA_LOI = re.compile(r"[a-z0-9_]{1,64}")

_khoa_han_dong = threading.Lock()
_cac_moc_goi: deque[float] = deque()


def chuan_hoa_hop_thu(hop_thu: object) -> str | None:
    """Chuẩn hoá địa chỉ hộp thư (bỏ khoảng trắng + casefold); sai khuôn → None."""
    if not isinstance(hop_thu, str):
        return None
    e = hop_thu.strip().casefold()
    return e if _MAU_EMAIL.fullmatch(e) else None


def _bam_rut_gon(email_chuan: str) -> str:
    """Băm rút gọn (16 ký tự hex) để ghi nhật ký mà không lộ địa chỉ."""
    return hashlib.sha256(email_chuan.encode("utf-8")).hexdigest()[:16]


def dat_lai_han_dong() -> None:
    """Xoá bộ đếm hạn dòng (dùng cho test)."""
    with _khoa_han_dong:
        _cac_moc_goi.clear()


def _het_han_dong() -> bool:
    """True nếu đã vượt 6 lượt/phút; còn trong hạn thì ghi nhận lượt này."""
    now = time.monotonic()
    with _khoa_han_dong:
        while _cac_moc_goi and now - _cac_moc_goi[0] > CUA_SO_GIAY:
            _cac_moc_goi.popleft()
        if len(_cac_moc_goi) >= GIOI_HAN_LUOT_PHUT:
            return True
        _cac_moc_goi.append(now)
        return False


def _module_doc_ma(so) -> list[tuple[str, object]]:
    """Các (id, thể hiện) ĐANG CHẠY có khả năng `ma_xac_minh` và đủ hợp đồng."""
    ket: list[tuple[str, object]] = []
    dang_chay = getattr(so, "dang_chay", None) or {}
    catalog = getattr(so, "catalog", None) or {}
    for id_, the_hien in dang_chay.items():
        km = catalog.get(id_)
        manifest = getattr(km, "manifest", None) or {}
        if KHA_NANG not in (manifest.get("cung_cap") or []):
            continue
        if not callable(getattr(the_hien, "hop_thu_ra", None)):
            continue
        if not callable(getattr(the_hien, "lay_ma", None)):
            continue
        ket.append((id_, the_hien))
    return ket


def _hop_thu_cua_module(the_hien) -> list[str]:
    """Email đã chuẩn hoá của các hộp thư module công bố; module hỏng → []."""
    try:
        ds = the_hien.hop_thu_ra()
    except Exception as e:
        log.warning("ma-xac-minh: hop_thu_ra() lỗi: %s", type(e).__name__)
        return []
    if not isinstance(ds, list):
        return []
    ket: list[str] = []
    for muc in ds:
        if not isinstance(muc, dict):
            continue
        for khoa in ("id", "email"):
            e = chuan_hoa_hop_thu(muc.get(khoa))
            if e and e not in ket:
                ket.append(e)
    return ket


def co_hop_thu(so, hop_thu: str) -> bool:
    """Đồng bộ: có hộp thư đang đọc được nào khớp `hop_thu` không.

    Dùng cho kiểm 400 TRƯỚC khi chạy lượt (không bắt người dùng chờ vô ích).
    Module hỏng lúc liệt kê thì bị bỏ qua, không làm hỏng phép kiểm.
    """
    muc_tieu = chuan_hoa_hop_thu(hop_thu)
    if not muc_tieu:
        return False
    for _id, the_hien in _module_doc_ma(so):
        if muc_tieu in _hop_thu_cua_module(the_hien):
            return True
    return False


def danh_sach_hop_thu(so) -> list[dict]:
    """[{id, email_che, module}] cho GET /api/ma-xac-minh/hop-thu (quản trị)."""
    ket: list[dict] = []
    for id_, the_hien in _module_doc_ma(so):
        try:
            ds = the_hien.hop_thu_ra()
        except Exception:
            continue
        if not isinstance(ds, list):
            continue
        for muc in ds:
            if not isinstance(muc, dict):
                continue
            muc_id, che = muc.get("id"), muc.get("email_che")
            if isinstance(muc_id, str) and muc_id and isinstance(che, str) and che:
                ket.append({"id": muc_id, "email_che": che, "module": id_})
    return ket


def _ghi_nhat_ky(module_id: str | None, hop_thu_chuan: str, ket_qua: str,
                 cho_giay: object) -> None:
    """Nhật ký kênh `ma_xac_minh`: chỉ module, hộp thư BĂM rút gọn, kết quả,
    độ dài chờ — KHÔNG mã, KHÔNG tiêu đề thư."""
    try:
        nhat_ky.ghi(
            KENH_NHAT_KY,
            "thong_tin" if ket_qua == "ok" else "canh_bao",
            f"Đọc mã xác minh: {ket_qua}",
            module=module_id or "-",
            hop_thu_bam=_bam_rut_gon(hop_thu_chuan),
            ket_qua=ket_qua,
            cho_giay=cho_giay,
        )
    except Exception as e:
        log.warning("ma-xac-minh: ghi nhật ký lỗi: %s", type(e).__name__)


async def lay_ma_xac_minh(app, hop_thu: str, tu_mien: list,
                         do_dai: tuple, sau_luc: str,
                         cho_giay: int) -> tuple[str | None, str]:
    """Đọc mã xác minh từ hộp thư qua module đang chạy.

    Trả `(ma, ma_loi)`: thành công → `(ma, "")`; thất bại → `(None, ma_loi)`
    với `ma_loi` thuộc {khong_co_hop_thu, het_gio, gioi_han, loi_module, …}.
    KHÔNG BAO GIỜ ném lỗi ra ngoài — module hỏng/treo chỉ cho mã lỗi gọn.

    `tu_mien`/`do_dai` do TẦNG GỌI lấy từ manifest module đăng nhập; lõi
    không đọc chúng từ bất kỳ yêu cầu HTTP nào.
    """
    muc_tieu = chuan_hoa_hop_thu(hop_thu)
    if not muc_tieu:
        return None, "khong_co_hop_thu"
    so = getattr(getattr(app, "state", None), "so", None)
    if so is None:
        return None, "khong_co_hop_thu"
    muc = None
    for id_, the_hien in _module_doc_ma(so):
        if muc_tieu in _hop_thu_cua_module(the_hien):
            muc = (id_, the_hien)
            break
    if muc is None:
        _ghi_nhat_ky(None, muc_tieu, "khong_co_hop_thu", cho_giay)
        return None, "khong_co_hop_thu"
    if _het_han_dong():
        _ghi_nhat_ky(None, muc_tieu, "gioi_han", cho_giay)
        return None, "gioi_han"
    id_, the_hien = muc
    if not (isinstance(do_dai, (list, tuple)) and len(do_dai) == 2
            and all(type(x) is int for x in do_dai)):
        _ghi_nhat_ky(id_, muc_tieu, "loi_module", cho_giay)
        return None, "loi_module"
    try:
        cho = float(cho_giay)
    except (TypeError, ValueError):
        cho = float(CHO_GIAY_MAC_DINH)
    cho = min(max(cho, 1.0), 600.0)
    yeu_cau = {
        "hop_thu": muc_tieu,
        "tu_mien": [str(x) for x in (tu_mien or [])],
        "sau_luc": str(sau_luc or ""),
        "do_dai": [do_dai[0], do_dai[1]],
        "cho_giay": cho,
    }
    try:
        ket_qua = await asyncio.wait_for(the_hien.lay_ma(yeu_cau), timeout=cho)
    except asyncio.TimeoutError:
        _ghi_nhat_ky(id_, muc_tieu, "het_gio", cho_giay)
        return None, "het_gio"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("ma-xac-minh: lay_ma của %s lỗi: %s", id_, type(e).__name__)
        _ghi_nhat_ky(id_, muc_tieu, "loi_module", cho_giay)
        return None, "loi_module"
    if not isinstance(ket_qua, dict) or ket_qua.get("ok") is not True:
        ma_loi = ket_qua.get("ma_loi") if isinstance(ket_qua, dict) else None
        if not (isinstance(ma_loi, str) and _MAU_MA_LOI.fullmatch(ma_loi)):
            ma_loi = "loi_module"
        _ghi_nhat_ky(id_, muc_tieu, ma_loi, cho_giay)
        return None, ma_loi
    ma = ket_qua.get("ma")
    if not isinstance(ma, str) or not ma:
        _ghi_nhat_ky(id_, muc_tieu, "loi_module", cho_giay)
        return None, "loi_module"
    _ghi_nhat_ky(id_, muc_tieu, "ok", cho_giay)
    return ma, ""


def luc_hien_tai_iso() -> str:
    """Mốc `sau_luc`: lúc bắt đầu bước đăng nhập (chỉ đọc thư tới sau mốc này)."""
    return datetime.now(timezone.utc).isoformat()
