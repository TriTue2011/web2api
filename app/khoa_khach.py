"""Quản lý khoá khách có phạm vi và kiểm soát đồng thời (Việc E).

Thiết kế:
- Tệp `<DATA>/khoa_khach.json` (KHÔNG chứa khoá):
    {
      "khoa": [
        {
          "id": "kk_...",
          "nhan": "...",
          "bat": true,
          "pham_vi": ["*"] | [mã khả năng],
          "tao_luc": "ISO",
          "dung_cuoi": "ISO" | null,
          "so_luot": 0,
          "bam": SHA-256 hex
        }
      ]
    }
- Khoá sinh dạng secrets.token_urlsafe(32) tiền tố "w2a_", hiện ĐÚNG MỘT LẦN khi tạo.
- Băm SHA-256, so bằng hmac.compare_digest; ghi nguyên tử quyền 0600.
- Đường nóng (/v1/*): đếm lượt và dung_cuoi ghi vào bộ nhớ, đồng bộ ra đĩa theo nhịp
  hoặc khi tắt tiến trình — không ghi đĩa mỗi lượt.
- Trần đồng thời: WEB2API_KHACH_DONG_THOI → 429 + Retry-After (không tính là lỗi provider).
- Sổ thao tác: ghi kênh `quan_tri` cho tạo/sửa/thu hồi và từ chối 401/403, KHÔNG ghi khoá.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from app import kho, nhat_ky
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.khoa_khach")

_KHOA_LUONG = threading.RLock()
_KHOA_KHACH_CACHE: list[dict[str, Any]] = []
_CACHE_FILE_STAT: tuple[Any, ...] | None = None
_CO_THAY_DOI = False
_DONG_THOI_DANG_CHAY: dict[str, int] = {}


def _p_khoa_khach() -> Path:
    return kho.thu_muc_du_lieu() / "khoa_khach.json"


def tran_dong_thoi() -> int | None:
    """Trần yêu cầu đồng thời cho mỗi khoá khách từ biến WEB2API_KHACH_DONG_THOI. None nếu không đặt."""
    val = os.environ.get("WEB2API_KHACH_DONG_THOI", "").strip()
    if not val:
        return None
    try:
        n = int(val)
        return n if n > 0 else None
    except ValueError:
        return None


def _doc_tu_dia_neu_can() -> list[dict[str, Any]]:
    """Đọc tệp khoa_khach.json nếu cache chưa có hoặc tệp bị đổi bên ngoài (so stat).

    Luôn gọi khi đang giữ _KHOA_LUONG.
    """
    global _KHOA_KHACH_CACHE, _CACHE_FILE_STAT, _CO_THAY_DOI
    p = _p_khoa_khach()
    try:
        st = os.stat(p)
        danh_tinh = (str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if _CACHE_FILE_STAT == danh_tinh and not _CO_THAY_DOI:
            return _KHOA_KHACH_CACHE
        if not _CO_THAY_DOI or _CACHE_FILE_STAT != danh_tinh:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("khoa"), list):
                _KHOA_KHACH_CACHE = [
                    dict(k) for k in du_lieu["khoa"] if isinstance(k, dict) and "bam" in k and "id" in k
                ]
            else:
                _KHOA_KHACH_CACHE = []
            _CACHE_FILE_STAT = danh_tinh
            _CO_THAY_DOI = False
    except FileNotFoundError:
        if not _CO_THAY_DOI:
            _KHOA_KHACH_CACHE = []
            _CACHE_FILE_STAT = None
    except Exception as e:
        log.warning("Lỗi khi đọc %s: %s", p, e)
        if not _CO_THAY_DOI:
            _KHOA_KHACH_CACHE = []
            _CACHE_FILE_STAT = None
    return _KHOA_KHACH_CACHE


def _ghi_ra_dia_khoi_tao() -> None:
    """Ghi cache hiện tại ra đĩa với quyền 0600 (nguyên tử). Luôn gọi khi đang giữ _KHOA_LUONG."""
    global _CACHE_FILE_STAT, _CO_THAY_DOI
    p = _p_khoa_khach()
    du_lieu = {"khoa": _KHOA_KHACH_CACHE}
    kho.ghi_nguyen_tu(p, kho.json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=0o600)
    try:
        st = os.stat(p)
        _CACHE_FILE_STAT = (str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    except Exception:
        _CACHE_FILE_STAT = None
    _CO_THAY_DOI = False


def dong_bo_dia() -> None:
    """Đồng bộ dữ liệu thay đổi từ bộ nhớ ra đĩa nếu có cờ _CO_THAY_DOI (chạy theo nhịp / shutdown)."""
    with _KHOA_LUONG:
        if _CO_THAY_DOI:
            _ghi_ra_dia_khoi_tao()


def tim_theo_khoa_tho(khoa_tho: str) -> dict[str, Any] | None:
    """Đường nóng: tìm khoá khách theo chuỗi khoá thô (tính sha256 và so digest trong bộ nhớ).

    Không ghi đĩa. Trả về dict khoá (bản sao hoặc tham chiếu an toàn) hoặc None.
    """
    if not khoa_tho or not isinstance(khoa_tho, str):
        return None
    bam_tim = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        for k in ds:
            if hmac.compare_digest(bam_tim.encode("ascii"), str(k.get("bam", "")).encode("ascii")):
                return k
    return None


def ghi_nhan_su_dung(id_khoa: str) -> None:
    """Đường nóng: tăng số lượt và cập nhật lần dùng cuối trong bộ nhớ; đánh dấu cờ thay đổi."""
    global _CO_THAY_DOI
    bay_gio = kho.bay_gio_iso()
    with _KHOA_LUONG:
        for k in _KHOA_KHACH_CACHE:
            if k.get("id") == id_khoa:
                k["so_luot"] = int(k.get("so_luot", 0)) + 1
                k["dung_cuoi"] = bay_gio
                _CO_THAY_DOI = True
                break


def vao_dong_thoi(id_khoa: str) -> None:
    """Kiểm tra và chiếm một vị trí xử lý đồng thời cho khoá khách. Vượt trần → 429 kèm Retry-After."""
    tran = tran_dong_thoi()
    if tran is None:
        return
    with _KHOA_LUONG:
        hien_tai = _DONG_THOI_DANG_CHAY.get(id_khoa, 0)
        if hien_tai >= tran:
            raise LoiOpenAI(
                429,
                f"Quá giới hạn yêu cầu đồng thời cho khoá khách (tối đa {tran} yêu cầu cùng lúc).",
                loai="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": "1"},
            )
        _DONG_THOI_DANG_CHAY[id_khoa] = hien_tai + 1


def ra_dong_thoi(id_khoa: str | None) -> None:
    """Giải phóng một vị trí xử lý đồng thời khi kết thúc lượt gọi."""
    if not id_khoa:
        return
    with _KHOA_LUONG:
        if id_khoa in _DONG_THOI_DANG_CHAY:
            con = _DONG_THOI_DANG_CHAY[id_khoa] - 1
            if con <= 0:
                _DONG_THOI_DANG_CHAY.pop(id_khoa, None)
            else:
                _DONG_THOI_DANG_CHAY[id_khoa] = con


# ───────────── Quản trị qua API ─────────────

def _sach_khoa(k: dict[str, Any]) -> dict[str, Any]:
    """Trả về bản ghi an toàn: bỏ trường `bam` và mọi thông tin bí mật."""
    return {
        "id": k.get("id"),
        "nhan": k.get("nhan"),
        "bat": bool(k.get("bat", True)),
        "pham_vi": list(k.get("pham_vi", ["*"])),
        "tao_luc": k.get("tao_luc"),
        "dung_cuoi": k.get("dung_cuoi"),
        "so_luot": int(k.get("so_luot", 0)),
    }


def danh_sach_khoa() -> list[dict[str, Any]]:
    """Liệt kê mọi khoá khách (không băm, không khoá thô)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        return [_sach_khoa(k) for k in ds]


def tao_khoa(nhan: str = "", pham_vi: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """Tạo khoá khách mới: sinh khoá ngẫu nhiên hiện MỘT LẦN, ghi nguyên tử 0600 và ghi sổ quan_tri."""
    nhan_chuan = str(nhan or "").strip() or "Khoá khách"
    pv = list(pham_vi) if isinstance(pham_vi, list) and pham_vi else ["*"]
    khoa_tho = f"w2a_{secrets.token_urlsafe(32)}"
    bam = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    id_khoa = f"kk_{secrets.token_hex(6)}"
    tao_luc = kho.bay_gio_iso()

    muc = {
        "id": id_khoa,
        "nhan": nhan_chuan,
        "bat": True,
        "pham_vi": pv,
        "tao_luc": tao_luc,
        "dung_cuoi": None,
        "so_luot": 0,
        "bam": bam,
    }

    with _KHOA_LUONG:
        _doc_tu_dia_neu_can()
        _KHOA_KHACH_CACHE.append(muc)
        _ghi_ra_dia_khoi_tao()

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        f"Tạo khoá khách «{nhan_chuan}» (id: {id_khoa})",
        id_khoa=id_khoa,
        nhan=nhan_chuan,
        pham_vi=pv,
    )
    ban_ghi = _sach_khoa(muc)
    return ban_ghi, khoa_tho


def sua_khoa(
    id_khoa: str,
    bat: bool | None = None,
    nhan: str | None = None,
    pham_vi: list[str] | None = None,
) -> dict[str, Any]:
    """Cập nhật thông tin khoá khách (bật/tắt, nhãn, phạm vi)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        tim_thay = None
        for k in ds:
            if k.get("id") == id_khoa:
                tim_thay = k
                break
        if not tim_thay:
            raise LoiOpenAI(404, f"Không tìm thấy khoá khách có id {id_khoa!r}", code="key_not_found")

        if bat is not None:
            tim_thay["bat"] = bool(bat)
        if nhan is not None:
            tim_thay["nhan"] = str(nhan).strip() or tim_thay.get("nhan", "Khoá khách")
        if pham_vi is not None:
            tim_thay["pham_vi"] = list(pham_vi) if isinstance(pham_vi, list) and pham_vi else ["*"]

        _ghi_ra_dia_khoi_tao()
        kq = _sach_khoa(tim_thay)

    nhat_ky.ghi(
        "quan_tri",
        "thong_tin",
        f"Cập nhật khoá khách (id: {id_khoa})",
        id_khoa=id_khoa,
        bat=kq["bat"],
        nhan=kq["nhan"],
        pham_vi=kq["pham_vi"],
    )
    return kq


def xoa_khoa(id_khoa: str) -> bool:
    """Thu hồi khoá khách (xoá khỏi danh sách, ghi đĩa ngay, các lượt sau lập tức 401)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        ban_dau = len(ds)
        _KHOA_KHACH_CACHE[:] = [k for k in ds if k.get("id") != id_khoa]
        da_xoa = len(_KHOA_KHACH_CACHE) < ban_dau
        if da_xoa:
            _ghi_ra_dia_khoi_tao()

    if da_xoa:
        nhat_ky.ghi(
            "quan_tri",
            "thong_tin",
            f"Thu hồi khoá khách (id: {id_khoa})",
            id_khoa=id_khoa,
        )
    return da_xoa
