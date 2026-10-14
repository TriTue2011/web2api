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
from app.kham_pha import MA_KHA_NANG
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.khoa_khach")

_KHOA_LUONG = threading.RLock()
_KHOA_KHACH_CACHE: list[dict[str, Any]] = []
_CACHE_FILE_STAT: tuple[Any, ...] | None = None
_CO_THAY_DOI = False
_DONG_THOI_DANG_CHAY: dict[str, int] = {}


def _p_khoa_khach() -> Path:
    return kho.thu_muc_du_lieu() / "khoa_khach.json"


#: Trần số phần tử một danh sách phạm vi (chặn thân yêu cầu nhồi danh sách khổng lồ vào tệp).
TOI_DA_PHAN_TU_PHAM_VI = 64


def la_ma_pham_vi(x: Any) -> bool:
    """Một phần tử phạm vi hợp lệ: `*` hoặc mã khả năng mẫu MA_KHA_NANG, có thể kèm hậu tố `:<mã>`
    (vd `thiet_bi:ghi` — đúng dạng các đường /api/thiet-bi kiểm). Mã MỞ: kiểm MẪU, không giữ danh sách mã."""
    if not isinstance(x, str):
        return False
    if x == "*":
        return True
    phan = x.split(":")
    return len(phan) <= 2 and all(MA_KHA_NANG.match(m) for m in phan)


def chuan_hoa_pham_vi(gia_tri: Any, *, cho_phep_rong: bool, ten_truong: str = "pham_vi") -> list[str]:
    """Bộ kiểm DUY NHẤT cho danh sách phạm vi do khách gửi (khoá khách và trần của người dùng).

    Phải là list các chuỗi hợp lệ (`la_ma_pham_vi`), không phần tử rỗng/khoảng trắng/sai kiểu, bỏ trùng giữ thứ tự.
    KHÔNG BAO GIỜ đổi danh sách rỗng thành «*»: rỗng nghĩa là KHÔNG có quyền nào — `cho_phep_rong=False`
    (phạm vi một khoá) thì rỗng → 400; `cho_phep_rong=True` (trần của người dùng) thì rỗng hợp lệ.
    """
    if not isinstance(gia_tri, list) or len(gia_tri) > TOI_DA_PHAN_TU_PHAM_VI:
        raise LoiOpenAI(400, f"Trường «{ten_truong}» phải là danh sách mã phạm vi (tối đa {TOI_DA_PHAN_TU_PHAM_VI} phần tử).",
                        code="invalid_value")
    ket_qua: list[str] = []
    for x in gia_tri:
        if not la_ma_pham_vi(x):
            raise LoiOpenAI(400, f"Trường «{ten_truong}» chỉ nhận «*» hoặc mã khả năng [a-z][a-z0-9_] (có thể kèm «:<mã>»).",
                            code="invalid_value")
        if x not in ket_qua:
            ket_qua.append(x)
    if not ket_qua and not cho_phep_rong:
        raise LoiOpenAI(400, f"Trường «{ten_truong}» không được rỗng — rỗng là không có quyền nào, không phải «tất cả».",
                        code="invalid_value")
    return ket_qua


def giao_pham_vi(pham_vi: list[str], tran: list[str]) -> list[str]:
    """Phạm vi hiệu lực = GIAO của phạm vi khoá với trần. «*» ở một bên nghĩa là bên kia quyết."""
    if "*" in tran:
        return list(pham_vi)
    if "*" in pham_vi:
        return list(tran)
    return [x for x in pham_vi if x in tran]


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

    Khoá có chủ (`chu_id`): chủ bị tắt/xoá hoặc kho người dùng đọc lỗi → bản sao `bat=False` (401, đóng khi lỗi);
    còn lại → bản sao có `pham_vi` = GIAO(phạm vi khoá, trần HIỆN TẠI của chủ) — hạ trần/hạ vai thu hẹp khoá đã
    cấp ngay ở lượt sau, không cần sửa khoá. Thứ tự khoá: khoá khách rồi mới tới khoá người dùng; module
    nguoi_dung không bao giờ gọi vào đây khi đang giữ khoá của nó.
    """
    if not khoa_tho or not isinstance(khoa_tho, str):
        return None
    bam_tim = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        for k in ds:
            if hmac.compare_digest(bam_tim.encode("ascii"), str(k.get("bam", "")).encode("ascii")):
                chu_id = k.get("chu_id")
                if chu_id:
                    from app import nguoi_dung
                    try:
                        nd = nguoi_dung.tim_theo_id(chu_id)
                    except nguoi_dung.LoiKhoNguoiDung:
                        nd = None
                    k_copy = dict(k)
                    if nd is None or nd.get("bat", True) is not True:
                        k_copy["bat"] = False
                        return k_copy
                    k_copy["pham_vi"] = giao_pham_vi(list(k.get("pham_vi", ["*"])), nguoi_dung.tran_pham_vi(nd))
                    return k_copy
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
        "chu_id": k.get("chu_id"),
        "tao_luc": k.get("tao_luc"),
        "dung_cuoi": k.get("dung_cuoi"),
        "so_luot": int(k.get("so_luot", 0)),
    }


def danh_sach_khoa() -> list[dict[str, Any]]:
    """Liệt kê mọi khoá khách (không băm, không khoá thô)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        return [_sach_khoa(k) for k in ds]


def danh_sach_khoa_theo_chu(chu_id: str) -> list[dict[str, Any]]:
    """Liệt kê khoá khách thuộc về một người dùng cụ thể."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        return [_sach_khoa(k) for k in ds if k.get("chu_id") == chu_id]


def xoa_khoa_theo_chu(chu_id: str) -> int:
    """Xoá toàn bộ khoá khách thuộc về một người dùng (khi xoá người dùng)."""
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        ban_dau = len(ds)
        _KHOA_KHACH_CACHE[:] = [k for k in ds if k.get("chu_id") != chu_id]
        da_xoa = ban_dau - len(_KHOA_KHACH_CACHE)
        if da_xoa > 0:
            _ghi_ra_dia_khoi_tao()
    return da_xoa


def tao_khoa(
    nhan: str = "",
    pham_vi: list[str] | None = None,
    chu_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Tạo khoá khách mới: sinh khoá ngẫu nhiên hiện MỘT LẦN, ghi nguyên tử 0600 và ghi sổ quan_tri.

    `pham_vi=None` (không nêu) = «*» — hợp đồng cũ của đường quản trị. Đã nêu thì phải qua `chuan_hoa_pham_vi`
    (danh sách rỗng → 400, KHÔNG thành «*»). Đường người dùng luôn truyền danh sách tường minh đã kiểm trần.
    """
    nhan_chuan = str(nhan or "").strip() or "Khoá khách"
    pv = ["*"] if pham_vi is None else chuan_hoa_pham_vi(pham_vi, cho_phep_rong=False)
    khoa_tho = f"w2a_{secrets.token_urlsafe(32)}"
    bam = hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest()
    id_khoa = f"kk_{secrets.token_hex(6)}"
    tao_luc = kho.bay_gio_iso()

    muc = {
        "id": id_khoa,
        "nhan": nhan_chuan,
        "bat": True,
        "pham_vi": pv,
        "chu_id": chu_id,
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
        chu_id=chu_id,
    )
    ban_ghi = _sach_khoa(muc)
    return ban_ghi, khoa_tho


def sua_khoa(
    id_khoa: str,
    bat: bool | None = None,
    nhan: str | None = None,
    pham_vi: list[str] | None = None,
) -> dict[str, Any]:
    """Cập nhật thông tin khoá khách (bật/tắt, nhãn, phạm vi). None = giữ nguyên; `bat` phải là bool thật."""
    if bat is not None and not isinstance(bat, bool):
        raise LoiOpenAI(400, "Trường «bat» phải là true hoặc false.", code="invalid_value")
    pv_moi = None if pham_vi is None else chuan_hoa_pham_vi(pham_vi, cho_phep_rong=False)
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
            tim_thay["bat"] = bat
        if nhan is not None:
            tim_thay["nhan"] = str(nhan).strip() or tim_thay.get("nhan", "Khoá khách")
        if pv_moi is not None:
            tim_thay["pham_vi"] = pv_moi

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
