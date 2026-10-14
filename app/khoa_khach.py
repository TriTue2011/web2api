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
import re
import secrets
import threading
from contextlib import contextmanager
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


def lam_moi_cache() -> None:
    """Xoá cache trong bộ nhớ để lượt đọc tiếp theo nạp lại tệp khoa_khach.json từ đĩa."""
    global _KHOA_KHACH_CACHE, _CACHE_FILE_STAT, _CO_THAY_DOI
    with _KHOA_LUONG:
        _KHOA_KHACH_CACHE = []
        _CACHE_FILE_STAT = None
        _CO_THAY_DOI = False


@contextmanager
def thay_tep_tu_ngoai():
    """Ghi đè khoa_khach.json từ NGOÀI module (khôi phục) trong khối này: giữ khoá luồng suốt lượt ghi để nhịp
    `dong_bo_dia` không ghi cache CŨ đè lên tệp mới, rồi bỏ cache để lượt tra sau đọc lại từ đĩa."""
    with _KHOA_LUONG:
        try:
            yield
        finally:
            lam_moi_cache()


_ID_KHOA = re.compile(r"[A-Za-z0-9_-]{1,64}")
_BAM_HOP_LE = re.compile(r"[0-9a-f]{64}")


def kiem_tep(du_lieu: Any) -> list[dict[str, Any]]:
    """Kiểm THUẦN (không ghi) nội dung khoa_khach.json — dùng cho khôi phục, cùng hình dạng `tao_khoa` ghi ra.

    Mọi trường quyết định quyền đều BẮT BUỘC và đúng kiểu: thiếu `pham_vi`/`bat` không được rơi về mặc định «*»/bật
    của đường xác thực. Trả danh sách bản ghi chuẩn hoá; sai → ValueError nêu vị trí và quy tắc, không chép giá trị.
    """
    ds = du_lieu.get("khoa") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list):
        raise ValueError('cần dạng {"khoa": [...]}')
    ra, id_da_thay, bam_da_thay = [], set(), set()
    for i, k in enumerate(ds):
        if not isinstance(k, dict):
            raise ValueError(f"khoa[{i}]: phải là đối tượng")
        if not isinstance(k.get("id"), str) or not _ID_KHOA.fullmatch(k["id"]):
            raise ValueError(f"khoa[{i}]: «id» phải gồm chữ, số, «_», «-» (1–64 ký tự)")
        if not isinstance(k.get("bam"), str) or not _BAM_HOP_LE.fullmatch(k["bam"]):
            raise ValueError(f"khoa[{i}]: «bam» phải là SHA-256 dạng 64 ký tự hex thường")
        if k["id"] in id_da_thay or k["bam"] in bam_da_thay:
            raise ValueError(f"khoa[{i}]: «id» hoặc «bam» trùng mục trước")
        if not isinstance(k.get("bat"), bool):
            raise ValueError(f"khoa[{i}]: «bat» phải là true/false")
        pv = k.get("pham_vi")
        if (not isinstance(pv, list) or not pv
                or not all(isinstance(x, str) and (x == "*" or MA_KHA_NANG.match(x)) for x in pv)):
            raise ValueError(f"khoa[{i}]: «pham_vi» phải là danh sách khác rỗng gồm «*» hoặc mã khả năng")
        nhan = k.get("nhan", "Khoá khách")
        so_luot = k.get("so_luot", 0)
        if not isinstance(nhan, str) or len(nhan) > 200:
            raise ValueError(f"khoa[{i}]: «nhan» phải là chuỗi ≤ 200 ký tự")
        if not isinstance(so_luot, int) or isinstance(so_luot, bool) or so_luot < 0:
            raise ValueError(f"khoa[{i}]: «so_luot» phải là số nguyên ≥ 0")
        for t in ("tao_luc", "dung_cuoi"):
            if k.get(t) is not None and not isinstance(k[t], str):
                raise ValueError(f"khoa[{i}]: «{t}» phải là chuỗi hoặc null")
        id_da_thay.add(k["id"])
        bam_da_thay.add(k["bam"])
        ra.append({"id": k["id"], "nhan": nhan, "bat": k["bat"], "pham_vi": list(dict.fromkeys(pv)),
                   "tao_luc": k.get("tao_luc"), "dung_cuoi": k.get("dung_cuoi"), "so_luot": so_luot,
                   "bam": k["bam"]})
    return ra


def khac_biet(moi: list[dict[str, Any]]) -> dict[str, Any]:
    """So danh sách khoá (đã qua `kiem_tep`) với khoá đang có — cho người duyệt khôi phục thấy mình chấp nhận gì:
    khoá MỚI (có thể là băm do người khác chọn = cửa vào /v1), khoá cùng id mà ĐỔI BĂM, đổi phạm vi/bật/nhãn, và
    khoá đang có sẽ MẤT. Không trả băm."""
    with _KHOA_LUONG:
        cu = {k.get("id"): dict(k) for k in _doc_tu_dia_neu_can()}
    ds = []
    for k in moi:
        c = cu.get(k["id"])
        if c is None:
            tt = "moi"
        elif c.get("bam") != k["bam"]:
            tt = "doi_bam"
        elif (c.get("bat", True), list(c.get("pham_vi", ["*"])), c.get("nhan")) != (k["bat"], k["pham_vi"], k["nhan"]):
            tt = "doi"
        else:
            tt = "giu_nguyen"
        ds.append({"id": k["id"], "nhan": k["nhan"], "bat": k["bat"], "pham_vi": list(k["pham_vi"]), "trang_thai": tt})
    con = {k["id"] for k in moi}
    return {"so_khoa": len(moi), "khoa": ds, "se_mat": sorted(str(i) for i in cu if i not in con)}


def tim_theo_bam(bam_tim: str) -> dict[str, Any] | None:
    """Tra khoá khách theo băm SHA-256 (đường tra thật của xác thực, sau bước băm). None nếu không có."""
    if not isinstance(bam_tim, str):
        return None
    with _KHOA_LUONG:
        ds = _doc_tu_dia_neu_can()
        for k in ds:
            if hmac.compare_digest(bam_tim.encode("ascii"), str(k.get("bam", "")).encode("ascii")):
                return k
    return None


def tim_theo_khoa_tho(khoa_tho: str) -> dict[str, Any] | None:
    """Đường nóng: tìm khoá khách theo chuỗi khoá thô (tính sha256 rồi tra bằng `tim_theo_bam`).

    Không ghi đĩa. Trả về dict khoá (bản sao hoặc tham chiếu an toàn) hoặc None.
    """
    if not khoa_tho or not isinstance(khoa_tho, str):
        return None
    return tim_theo_bam(hashlib.sha256(khoa_tho.encode("utf-8")).hexdigest())


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
