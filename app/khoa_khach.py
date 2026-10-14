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


class LoiKhoKhoaKhach(LoiOpenAI):
    """khoa_khach.json tồn tại nhưng không đọc/phân tích được. 503, không lộ chi tiết tệp ra phản hồi.

    ĐÓNG KHI LỖI (cùng nguyên tắc với `nguoi_dung.LoiKhoNguoiDung`): không bao giờ coi tệp hỏng là «chưa có khoá» —
    lần ghi kế tiếp sẽ GHI ĐÈ mất mọi khoá cũ. Mọi đường ghi đọc qua `_doc_tu_dia_neu_can` nên dừng ở đây; đường xác
    thực (`tim_theo_bam`) bắt lỗi này và trả None (không cấp quyền, không 500)."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "Kho khoá khách không đọc được. Khôi phục tệp khoa_khach.json từ bản sao lưu (Hệ thống → Sao lưu) rồi thử "
            "lại; các thao tác ghi khoá bị khoá để không ghi đè khoá hiện có.",
            loai="server_error",
            code="guest_key_store_unreadable",
        )


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

    Luôn gọi khi đang giữ _KHOA_LUONG. Tệp KHÔNG tồn tại → kho rỗng. Tệp tồn tại mà đọc lỗi, JSON hỏng hoặc sai cấu
    trúc (gốc không phải {"khoa": [...]}, phần tử không phải object có `id` và `bam`) → LoiKhoKhoaKhach, cache giữ
    nguyên. Không lọc lặng lẽ phần tử hỏng: lọc rồi lần ghi sau sẽ xoá mất chúng.
    """
    global _KHOA_KHACH_CACHE, _CACHE_FILE_STAT, _CO_THAY_DOI
    p = _p_khoa_khach()
    try:
        st = os.stat(p)
    except FileNotFoundError:
        if not _CO_THAY_DOI:
            _KHOA_KHACH_CACHE = []
            _CACHE_FILE_STAT = None
        return _KHOA_KHACH_CACHE
    except OSError as e:
        log.warning("Không đọc được %s: %s", p, type(e).__name__)
        raise LoiKhoKhoaKhach() from None
    danh_tinh = (str(p), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if _CACHE_FILE_STAT == danh_tinh:
        return _KHOA_KHACH_CACHE
    try:
        du_lieu = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("Kho khoá khách hỏng: %s", e)
        raise LoiKhoKhoaKhach() from None
    ds = du_lieu.get("khoa") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list) or not all(isinstance(k, dict) and "bam" in k and "id" in k for k in ds):
        log.warning("Kho khoá khách sai cấu trúc: %s", p)
        raise LoiKhoKhoaKhach()
    _KHOA_KHACH_CACHE = [dict(k) for k in ds]
    _CACHE_FILE_STAT = danh_tinh
    _CO_THAY_DOI = False
    return _KHOA_KHACH_CACHE


def kiem_tra_kho() -> None:
    """Ném LoiKhoKhoaKhach nếu kho tồn tại mà không đọc được — cho đường cần kiểm TRƯỚC khi đổi trạng thái khác."""
    with _KHOA_LUONG:
        _doc_tu_dia_neu_can()


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
        if not _CO_THAY_DOI:
            return
        try:
            _doc_tu_dia_neu_can()  # tệp đổi từ ngoài → nạp lại (bỏ đếm lượt); hỏng → KHÔNG ghi đè
        except LoiKhoKhoaKhach:
            return  # không ném: vòng nền và lúc tắt tiến trình gọi hàm này
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
        try:  # CÙNG bộ kiểm với đường tạo/sửa khoá — tệp khôi phục không được hẹp hơn hay rộng hơn thứ đã ghi ra
            pv = chuan_hoa_pham_vi(k.get("pham_vi"), cho_phep_rong=False)
        except LoiOpenAI:
            raise ValueError(f"khoa[{i}]: «pham_vi» phải là danh sách khác rỗng (tối đa {TOI_DA_PHAN_TU_PHAM_VI} phần "
                             "tử) gồm «*» hoặc mã khả năng (có thể kèm «:<mã>»)") from None
        nhan = k.get("nhan", "Khoá khách")
        so_luot = k.get("so_luot", 0)
        if not isinstance(nhan, str) or len(nhan) > 200:
            raise ValueError(f"khoa[{i}]: «nhan» phải là chuỗi ≤ 200 ký tự")
        if not isinstance(so_luot, int) or isinstance(so_luot, bool) or so_luot < 0:
            raise ValueError(f"khoa[{i}]: «so_luot» phải là số nguyên ≥ 0")
        for t in ("tao_luc", "dung_cuoi"):
            if k.get(t) is not None and not isinstance(k[t], str):
                raise ValueError(f"khoa[{i}]: «{t}» phải là chuỗi hoặc null")
        chu_id = k.get("chu_id")
        if chu_id is not None and (not isinstance(chu_id, str) or not _ID_KHOA.fullmatch(chu_id)):
            raise ValueError(f"khoa[{i}]: «chu_id» phải là null hoặc mã người dùng hợp lệ")
        id_da_thay.add(k["id"])
        bam_da_thay.add(k["bam"])
        ra.append({"id": k["id"], "nhan": nhan, "bat": k["bat"], "pham_vi": pv,
                   "tao_luc": k.get("tao_luc"), "dung_cuoi": k.get("dung_cuoi"), "so_luot": so_luot,
                   "bam": k["bam"], **({"chu_id": chu_id} if chu_id else {})})
    return ra


def _rong_hon(moi: list[str], cu: list[str] | None) -> bool:
    """Phạm vi `moi` cho quyền mà `cu` không cho (khoá mới: `cu=None`, chỉ «*» mới là cờ đỏ)."""
    if cu is None:
        return "*" in moi
    return "*" not in cu and ("*" in moi or any(x not in cu for x in moi))


def _chu_cua(chu_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """(bản ghi chủ, None) hoặc (None, lý do chủ không dùng được). Gọi NGOÀI _KHOA_LUONG."""
    from app import nguoi_dung
    try:
        nd = nguoi_dung.tim_theo_id(chu_id)
    except nguoi_dung.LoiKhoNguoiDung:
        return None, "kho người dùng không đọc được"
    if nd is None:
        return None, "chủ không tồn tại trên máy này"
    if nd.get("bat", True) is not True:
        return None, "chủ đang bị tắt"
    return nd, None


def khac_biet(moi: list[dict[str, Any]]) -> dict[str, Any]:
    """So danh sách khoá (đã qua `kiem_tep`) với khoá đang có — cho người duyệt khôi phục thấy mình chấp nhận gì:
    khoá MỚI (có thể là băm do người khác chọn = cửa vào /v1), khoá cùng id mà ĐỔI BĂM, ĐỔI CHỦ (`doi_chu`), đổi phạm
    vi/bật/nhãn, và khoá đang có sẽ MẤT. Mỗi mục nêu `chu_id` (và `chu_id_hien_tai` nếu khoá đang có), cờ
    `mo_rong_pham_vi` (khoá mới «*», hoặc phạm vi rộng hơn bản đang có) và `canh_bao` khi chủ không dùng được (khoá sẽ
    bị đóng sau khôi phục). `bo_chu=True` khi khoá thô ĐANG có chủ (khớp theo id HOẶC theo băm — băm mới là thứ xác
    thực, id chỉ là nhãn) mà bản sao lưu bỏ/đổi chủ — sao_luu chặn mục đó. Kho hiện tại hỏng → mọi khoá là «moi», `kho_hien_tai_hong=True` (khôi phục là đường cứu,
    không được chặn nó). Không trả băm."""
    hong = False
    with _KHOA_LUONG:
        try:
            cu = {k.get("id"): dict(k) for k in _doc_tu_dia_neu_can()}
        except LoiKhoKhoaKhach:
            cu, hong = {}, True
    cu_theo_bam = {c.get("bam"): c for c in cu.values()}
    ds = []
    for k in moi:
        c = cu.get(k["id"])
        chu_dang_co = {x.get("chu_id") for x in (c, cu_theo_bam.get(k["bam"])) if x is not None and x.get("chu_id")}
        if c is None:
            tt = "moi"
        elif c.get("bam") != k["bam"]:
            tt = "doi_bam"
        elif c.get("chu_id") != k.get("chu_id"):
            tt = "doi_chu"
        elif (c.get("bat", True), list(c.get("pham_vi", ["*"])), c.get("nhan")) != (k["bat"], k["pham_vi"], k["nhan"]):
            tt = "doi"
        else:
            tt = "giu_nguyen"
        muc = {"id": k["id"], "nhan": k["nhan"], "bat": k["bat"], "pham_vi": list(k["pham_vi"]),
               "chu_id": k.get("chu_id"), "trang_thai": tt,
               "mo_rong_pham_vi": _rong_hon(k["pham_vi"], None if c is None else list(c.get("pham_vi", ["*"]))),
               "canh_bao": None, "bo_chu": any(ch != k.get("chu_id") for ch in chu_dang_co)}
        if c is not None:
            muc["chu_id_hien_tai"] = c.get("chu_id")
        if k.get("chu_id"):
            _, ly_do = _chu_cua(k["chu_id"])
            if ly_do:
                muc["canh_bao"] = f"{ly_do} — khoá sẽ bị đóng sau khôi phục (401) cho tới khi chủ có mặt và được bật"
        ds.append(muc)
    con = {k["id"] for k in moi}
    return {"so_khoa": len(moi), "khoa": ds, "se_mat": sorted(str(i) for i in cu if i not in con),
            "kho_hien_tai_hong": hong}


def ap_chinh_sach_chu(k: dict[str, Any]) -> dict[str, Any]:
    """Chính sách DUY NHẤT từ bản ghi thô ra khoá hiệu lực (bản sao). Khoá không chủ → nguyên. Khoá có chủ: chủ bị
    tắt/xoá hoặc kho người dùng đọc lỗi → `bat=False` (đóng khi lỗi); còn lại `pham_vi` = GIAO(phạm vi khoá, trần
    HIỆN TẠI của chủ) — hạ trần/hạ vai thu hẹp khoá đã cấp ngay ở lượt sau. Gọi NGOÀI _KHOA_LUONG."""
    k = dict(k)
    chu_id = k.get("chu_id")
    if not chu_id:
        return k
    from app import nguoi_dung
    nd, _ = _chu_cua(chu_id)
    if nd is None:
        k["bat"] = False
    else:
        k["pham_vi"] = giao_pham_vi(list(k.get("pham_vi", ["*"])), nguoi_dung.tran_pham_vi(nd))
    return k


def ban_ghi_theo_bam(bam_tim: str) -> dict[str, Any] | None:
    """Bản ghi THÔ (bản sao, chưa áp chính sách chủ) theo băm — thứ đã ghi trên đĩa. Kho hỏng → LoiKhoKhoaKhach."""
    if not isinstance(bam_tim, str):
        return None
    with _KHOA_LUONG:
        for k in _doc_tu_dia_neu_can():
            if hmac.compare_digest(bam_tim.encode("ascii"), str(k.get("bam", "")).encode("ascii")):
                return dict(k)
    return None


def tim_theo_bam(bam_tim: str) -> dict[str, Any] | None:
    """Tra khoá khách theo băm SHA-256 (đường tra thật của xác thực, sau bước băm): bản ghi thô qua
    `ap_chinh_sach_chu`. None nếu không có — và cả khi kho khoá khách hỏng (ĐÓNG: không cấp quyền, không 500; khoá
    quản trị vẫn dùng được để khôi phục). Tra chủ chạy sau khi nhả khoá khoá khách."""
    try:
        k = ban_ghi_theo_bam(bam_tim)
    except LoiKhoKhoaKhach:
        return None
    return None if k is None else ap_chinh_sach_chu(k)


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
        "chu_id": k.get("chu_id"),
        "tao_luc": k.get("tao_luc"),
        "dung_cuoi": k.get("dung_cuoi"),
        "so_luot": int(k.get("so_luot", 0)),
    }


def _kem_hieu_luc(ds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bản ghi an toàn kèm `pham_vi_hieu_luc` — đúng thứ xác thực sẽ cấp (cùng `ap_chinh_sach_chu`); khoá tắt hoặc chủ
    không dùng được → []. `pham_vi` giữ nguyên là phạm vi gốc đã lưu."""
    ra = []
    for k in ds:
        hl = ap_chinh_sach_chu(k)
        ra.append({**_sach_khoa(k), "pham_vi_hieu_luc": list(hl.get("pham_vi", ["*"])) if hl.get("bat", True) else []})
    return ra


def danh_sach_khoa() -> list[dict[str, Any]]:
    """Liệt kê mọi khoá khách (không băm, không khoá thô)."""
    with _KHOA_LUONG:
        ds = [dict(k) for k in _doc_tu_dia_neu_can()]
    return _kem_hieu_luc(ds)


def danh_sach_khoa_theo_chu(chu_id: str) -> list[dict[str, Any]]:
    """Liệt kê khoá khách thuộc về một người dùng cụ thể."""
    with _KHOA_LUONG:
        ds = [dict(k) for k in _doc_tu_dia_neu_can() if k.get("chu_id") == chu_id]
    return _kem_hieu_luc(ds)


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
