"""Chuẩn THIẾT BỊ / ỨNG DỤNG của lõi (thiet_bi): thực thể, hành động, sự kiện, hàng chờ phê duyệt.

Nguyên tắc:
- Lõi KHÔNG chứa tên dịch vụ hay thiết bị cụ thể nào; mọi thứ là mã mở.
- Định tuyến theo tiền tố: id thực thể bắt đầu bằng "<tien_to>." → module có tiền tố đó.
- Ghi đè mức độ rủi ro lưu tại <DATA>/thiet_bi.json (0600).
- Hàng chờ phê duyệt cho hành động nguy hiểm lưu tại <DATA>/phe_duyet.json (0600), tự huỷ khi quá hạn.
- Sự kiện hỗ trợ SSE một chiều cho quản trị và khoá khách có phạm vi thiet_bi:su_kien.
- Sổ nhật ký kênh `thiet_bi`: ghi nhận hành động, phê duyệt, từ chối; KHÔNG ghi khoá hay tham số bí mật.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import kho, nhat_ky
from app.loi_openai import LoiOpenAI
from app.so_module import SoModule

log = logging.getLogger("web2api.thiet_bi")

_KHOA_LUONG = threading.RLock()
RUI_RO_HOP_LE = {"doc", "ghi", "nguy_hiem"}
TU_KHOA_NHAY_CAM = {"mat_khau", "password", "pass", "token", "secret", "key", "api_key", "khoa"}


def _luu_json(p: Path, du_lieu: Any, quyen: int = 0o600) -> None:
    kho.ghi_nguyen_tu(p, json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=quyen)



def bo_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt và chuyển chữ thường để tìm kiếm linh hoạt."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFD", s)
    khong_dau = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return khong_dau.replace("đ", "d").replace("Đ", "d").lower()


def lam_sach_tham_so(ts: dict) -> dict:
    """Che các giá trị có khả năng là bí mật trong tham số trước khi ghi nhật ký."""
    if not isinstance(ts, dict):
        return {}
    sach = {}
    for k, v in ts.items():
        ten_k = str(k).lower()
        if any(tu in ten_k for tu in TU_KHOA_NHAY_CAM):
            sach[k] = "…che"
        elif isinstance(v, dict):
            sach[k] = lam_sach_tham_so(v)
        else:
            sach[k] = v
    return sach


def _p_thiet_bi() -> Path:
    return kho.thu_muc_du_lieu() / "thiet_bi.json"


def _p_phe_duyet() -> Path:
    return kho.thu_muc_du_lieu() / "phe_duyet.json"


# ───────────── Ghi đè rủi ro ─────────────

def doc_ghi_de_rui_ro() -> dict[str, str]:
    """Đọc từ điển ghi đè rủi ro {id_thuc_the: "doc" | "ghi" | "nguy_hiem"}.

    Tệp hỏng → 503 (không trả rỗng): trả rỗng rồi ghi lại sẽ XOÁ toàn bộ ghi đè cũ.
    """
    with _KHOA_LUONG:
        p = _p_thiet_bi()
        try:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("ghi_de_rui_ro"), dict):
                return {str(k): str(v) for k, v in du_lieu["ghi_de_rui_ro"].items() if v in RUI_RO_HOP_LE}
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
            raise LoiOpenAI(503, f"Không đọc được kho thiết bị ({p.name}), không ghi đè.",
                            code="store_unreadable") from None
        return {}


def luu_ghi_de_rui_ro(id_thuc_the: str, rui_ro: str) -> None:
    """Lưu ghi đè rủi ro cho một thực thể (0600, nguyên tử)."""
    if rui_ro not in RUI_RO_HOP_LE:
        raise ValueError(f"Mức rủi ro không hợp lệ: {rui_ro!r} (chỉ nhận: {sorted(RUI_RO_HOP_LE)})")
    with _KHOA_LUONG:
        hien_tai = doc_ghi_de_rui_ro()
        hien_tai[id_thuc_the] = rui_ro
        _luu_json(_p_thiet_bi(), {"ghi_de_rui_ro": hien_tai})


# ───────────── Hàng chờ phê duyệt ─────────────

def thoi_han_phe_duyet_phut() -> int:
    """Thời hạn mục phê duyệt (phút), mặc định 10 phút từ biến WEB2API_PHE_DUYET_PHUT."""
    val = os.environ.get("WEB2API_PHE_DUYET_PHUT", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 10


def doc_hang_cho_phe_duyet() -> list[dict[str, Any]]:
    """Đọc danh sách mục đang chờ phê duyệt. Mục quá hạn tự động bị loại bỏ.

    Tệp hỏng → 503 (không trả rỗng): trả rỗng rồi ghi lại sẽ XOÁ toàn bộ hàng chờ.
    """
    with _KHOA_LUONG:
        p = _p_phe_duyet()
        hang_cho: list[dict[str, Any]] = []
        try:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("hang_cho"), list):
                hang_cho = [dict(m) for m in du_lieu["hang_cho"] if isinstance(m, dict)]
        except FileNotFoundError:
            return []
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
            raise LoiOpenAI(503, f"Không đọc được hàng chờ phê duyệt ({p.name}), không ghi đè.",
                            code="store_unreadable") from None

        bay_gio = datetime.now(timezone.utc)
        con_han: list[dict[str, Any]] = []
        co_thay_doi = False

        for item in hang_cho:
            het_han_str = item.get("het_han")
            if het_han_str:
                try:
                    hh = datetime.fromisoformat(het_han_str)
                    if hh.tzinfo is None:
                        hh = hh.replace(tzinfo=timezone.utc)
                    if hh <= bay_gio:
                        co_thay_doi = True
                        continue
                except Exception:
                    co_thay_doi = True
                    continue
            con_han.append(item)

        if co_thay_doi:
            try:
                _luu_json(p, {"hang_cho": con_han})
            except Exception as e:
                log.warning("Lỗi khi dọn mục phê duyệt quá hạn: %s", e)

        return con_han


def them_hang_cho_phe_duyet(
    ai_de_xuat: str,
    thuc_the: str,
    hanh_dong: str,
    tham_so: dict,
    ly_do: str = "",
) -> dict[str, Any]:
    """Thêm một yêu cầu vào hàng chờ phê duyệt, lưu nguyên tử và phát sự kiện SSE."""
    phut = thoi_han_phe_duyet_phut()
    bay_gio = datetime.now(timezone.utc)
    het_han = bay_gio.timestamp() + phut * 60
    het_han_iso = datetime.fromtimestamp(het_han, timezone.utc).isoformat()
    pd_id = f"pd_{secrets.token_hex(6)}"

    muc = {
        "id": pd_id,
        "ai_de_xuat": ai_de_xuat,
        "thuc_the": thuc_the,
        "hanh_dong": hanh_dong,
        "tham_so": dict(tham_so or {}),
        "ly_do": str(ly_do or ""),
        "luc": kho.bay_gio_iso(),
        "het_han": het_han_iso,
    }

    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        ds.append(muc)
        _luu_json(_p_phe_duyet(), {"hang_cho": ds})

    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_moi", "muc": muc})
    return muc


async def duyet_phe_duyet(pd_id: str, so: SoModule, nguoi_duyet: str = "quan_tri") -> dict:
    """Quản trị duyệt mục phê duyệt: thực hiện hành động trên module rồi xoá mục khỏi hàng chờ."""
    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        tim_thay = next((m for m in ds if m.get("id") == pd_id), None)
        if tim_thay is None:
            raise LoiOpenAI(404, f"Không tìm thấy mục phê duyệt {pd_id!r} hoặc mục đã quá hạn.", code="not_found")
        con_lai = [m for m in ds if m.get("id") != pd_id]
        _luu_json(_p_phe_duyet(), {"hang_cho": con_lai})

    thuc_the_id = tim_thay["thuc_the"]
    hanh_dong = tim_thay["hanh_dong"]
    tham_so = tim_thay.get("tham_so", {})
    ai_de_xuat = tim_thay.get("ai_de_xuat", "khach")

    # Thực hiện hành động
    tien_to, _, _ = thuc_the_id.partition(".")
    mod_id = so.tim_theo_tien_to(tien_to)
    if not mod_id or mod_id not in so.dang_chay:
        raise LoiOpenAI(502, f"Module cho tiền tố {tien_to!r} hiện không sẵn sàng.", code="upstream_error")

    km = so.catalog.get(mod_id)
    if not km or not callable(getattr(so.dang_chay[mod_id], "thuc_hien", None)):
        raise LoiOpenAI(502, f"Module {mod_id!r} không hỗ trợ thực hiện hành động.", code="upstream_error")

    m = so.dang_chay[mod_id]
    try:
        kq = await m.thuc_hien(thuc_the_id, hanh_dong, tham_so)
    except Exception as e:
        log.exception("Lỗi khi thực hiện hành động đã duyệt trên module %s", mod_id)
        nhat_ky.ghi(
            "thiet_bi",
            "loi",
            f"Duyệt nhưng lỗi: «{nguoi_duyet}» duyệt «{hanh_dong}» trên «{thuc_the_id}» nhưng module lỗi: {e}",
            nguoi_duyet=nguoi_duyet,
            ai_de_xuat=ai_de_xuat,
            thuc_the=thuc_the_id,
            hanh_dong=hanh_dong,
            loi=str(e),
        )
        raise LoiOpenAI(502, f"Module lỗi khi thực hiện: {type(e).__name__}", code="upstream_error") from e

    nhat_ky.ghi(
        "thiet_bi",
        "thong_tin",
        f"Đã duyệt: «{nguoi_duyet}» duyệt hành động «{hanh_dong}» cho thực thể «{thuc_the_id}» (đề xuất bởi {ai_de_xuat})",
        nguoi_duyet=nguoi_duyet,
        ai_de_xuat=ai_de_xuat,
        thuc_the=thuc_the_id,
        hanh_dong=hanh_dong,
        tham_so=lam_sach_tham_so(tham_so),
    )
    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_xong", "id": pd_id, "thuc_the": thuc_the_id, "hanh_dong": hanh_dong})
    return {"ok": True, "id": pd_id, "ket_qua": kq}


def tu_choi_phe_duyet(pd_id: str, nguoi_tu_choi: str = "quan_tri") -> dict:
    """Quản trị từ chối mục phê duyệt: xoá khỏi hàng chờ và ghi sổ kênh thiet_bi."""
    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        tim_thay = next((m for m in ds if m.get("id") == pd_id), None)
        if tim_thay is None:
            raise LoiOpenAI(404, f"Không tìm thấy mục phê duyệt {pd_id!r} hoặc mục đã quá hạn.", code="not_found")
        con_lai = [m for m in ds if m.get("id") != pd_id]
        _luu_json(_p_phe_duyet(), {"hang_cho": con_lai})

    thuc_the_id = tim_thay["thuc_the"]
    hanh_dong = tim_thay["hanh_dong"]
    ai_de_xuat = tim_thay.get("ai_de_xuat", "khach")

    nhat_ky.ghi(
        "thiet_bi",
        "thong_tin",
        f"Từ chối: «{nguoi_tu_choi}» từ chối hành động «{hanh_dong}» cho thực thể «{thuc_the_id}» (đề xuất bởi {ai_de_xuat})",
        nguoi_tu_choi=nguoi_tu_choi,
        ai_de_xuat=ai_de_xuat,
        thuc_the=thuc_the_id,
        hanh_dong=hanh_dong,
    )
    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_tu_choi", "id": pd_id, "thuc_the": thuc_the_id, "hanh_dong": hanh_dong})
    return {"ok": True, "id": pd_id, "da_tu_choi": True}


# ───────────── Tra cứu thực thể ─────────────

def _module_thiet_bi_hop_le(so: SoModule) -> list[tuple[str, Any, Any]]:
    """Liệt kê các module đang chạy có hỗ trợ chuẩn thiet_bi: (id, km, thể_hiện)."""
    kq = []
    for id_, m in list(so.dang_chay.items()):
        km = so.catalog.get(id_)
        if not km:
            continue
        cung_cap = km.manifest.get("cung_cap", [])
        if "thiet_bi" not in cung_cap:
            continue
        if "thiet_bi" in getattr(km, "chuan_loi", {}):
            continue
        if not (callable(getattr(m, "thuc_the", None)) and
                callable(getattr(m, "trang_thai", None)) and
                callable(getattr(m, "thuc_hien", None))):
            continue
        kq.append((id_, km, m))
    return kq


async def lay_danh_sach_thuc_the(
    so: SoModule,
    loc_q: str | None = None,
    loc_loai: str | None = None,
    loc_khu: str | None = None,
    loc_module: str | None = None,
    loc_rui_ro: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Gộp thực thể mọi module đang chạy có chuẩn thiet_bi, kèm rủi ro hiệu lực và trạng thái."""
    modules = _module_thiet_bi_hop_le(so)
    ghi_de = doc_ghi_de_rui_ro()
    danh_sach_thuc_the: list[dict[str, Any]] = []
    loi_module: list[dict[str, str]] = []
    id_da_co: set[str] = set()

    for mod_id, km, m in modules:
        tien_to = km.tien_to
        try:
            raw_entities = await asyncio.wait_for(m.thuc_the(None), timeout=5.0)
        except Exception as e:
            log.warning("Module %s lỗi/quá hạn khi gọi thuc_the: %s", mod_id, e)
            loi_module.append({"module": mod_id, "loi": str(e)})
            continue

        if not isinstance(raw_entities, list):
            loi_module.append({"module": mod_id, "loi": "thuc_the không trả về danh sách"})
            continue

        thuc_the_hop_le = []
        ids_can_trang_thai = []

        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            e_id = item.get("id")
            if not isinstance(e_id, str):
                continue
            # Kiểm hình dạng: id phải bắt đầu bằng f"{tien_to}."
            if not e_id.startswith(f"{tien_to}."):
                log.warning("Module %s trả thực thể có id %r sai tiền tố (cần bắt đầu bằng %s.), bỏ qua",
                            mod_id, e_id, f"{tien_to}.")
                continue
            # Kiểm trùng lặp
            if e_id in id_da_co:
                log.warning("Module %s trả thực thể trùng id %r, bỏ qua", mod_id, e_id)
                continue
            id_da_co.add(e_id)

            rr_mac_dinh = item.get("rui_ro_mac_dinh")
            if rr_mac_dinh not in RUI_RO_HOP_LE:
                rr_mac_dinh = "nguy_hiem"

            # Rủi ro hiệu lực: người dùng ghi đè > mặc định > "nguy_hiem"
            rr_hieu_luc = ghi_de.get(e_id, rr_mac_dinh)

            muc = {
                "id": e_id,
                "ten": str(item.get("ten") or e_id),
                "loai": str(item.get("loai") or "thiet_bi"),
                "khu": item.get("khu"),
                "thuoc_tinh": dict(item.get("thuoc_tinh") or {}),
                "tinh_nang": list(item.get("tinh_nang") or []),
                "rui_ro_mac_dinh": rr_mac_dinh,
                "rui_ro": rr_hieu_luc,
                "module": mod_id,
                "trang_thai": None,
            }
            thuc_the_hop_le.append(muc)
            ids_can_trang_thai.append(e_id)

        # Lấy trạng thái cho các thực thể hợp lệ
        if ids_can_trang_thai:
            try:
                tt_dict = await asyncio.wait_for(m.trang_thai(ids_can_trang_thai), timeout=5.0)
                if isinstance(tt_dict, dict):
                    for muc in thuc_the_hop_le:
                        muc["trang_thai"] = tt_dict.get(muc["id"])
            except Exception as e:
                log.warning("Module %s lỗi/quá hạn khi lấy trang_thai: %s", mod_id, e)

        danh_sach_thuc_the.extend(thuc_the_hop_le)

    # Áp dụng bộ lọc
    kq = []
    q_chuan = bo_dau(loc_q.strip()) if loc_q and loc_q.strip() else None

    for item in danh_sach_thuc_the:
        if q_chuan:
            ten_chuan = bo_dau(item["ten"])
            id_chuan = bo_dau(item["id"])
            if q_chuan not in ten_chuan and q_chuan not in id_chuan:
                continue
        if loc_loai and item["loai"] != loc_loai:
            continue
        if loc_khu and item["khu"] != loc_khu:
            continue
        if loc_module and item["module"] != loc_module:
            continue
        if loc_rui_ro and item["rui_ro"] != loc_rui_ro:
            continue
        kq.append(item)

    return kq, loi_module


async def lay_mot_thuc_the(id_thuc_the: str, so: SoModule) -> dict[str, Any] | None:
    """Lấy thông tin và trạng thái của một thực thể cụ thể theo id (404 nếu không có)."""
    tien_to, co_cham, _ = id_thuc_the.partition(".")
    if not co_cham:
        return None

    mod_id = so.tim_theo_tien_to(tien_to)
    if not mod_id or mod_id not in so.dang_chay:
        return None

    km = so.catalog.get(mod_id)
    if not km or "thiet_bi" in getattr(km, "chuan_loi", {}):
        return None

    m = so.dang_chay[mod_id]
    if not (callable(getattr(m, "thuc_the", None)) and callable(getattr(m, "trang_thai", None))):
        return None

    try:
        raw_entities = await asyncio.wait_for(m.thuc_the(None), timeout=5.0)
    except Exception:
        return None

    if not isinstance(raw_entities, list):
        return None

    tim_thay = next((item for item in raw_entities if isinstance(item, dict) and item.get("id") == id_thuc_the), None)
    if not tim_thay:
        return None

    ghi_de = doc_ghi_de_rui_ro()
    rr_mac_dinh = tim_thay.get("rui_ro_mac_dinh")
    if rr_mac_dinh not in RUI_RO_HOP_LE:
        rr_mac_dinh = "nguy_hiem"
    rr_hieu_luc = ghi_de.get(id_thuc_the, rr_mac_dinh)

    trang_thai = None
    try:
        tt_dict = await asyncio.wait_for(m.trang_thai([id_thuc_the]), timeout=5.0)
        if isinstance(tt_dict, dict):
            trang_thai = tt_dict.get(id_thuc_the)
    except Exception:
        pass

    return {
        "id": id_thuc_the,
        "ten": str(tim_thay.get("ten") or id_thuc_the),
        "loai": str(tim_thay.get("loai") or "thiet_bi"),
        "khu": tim_thay.get("khu"),
        "thuoc_tinh": dict(tim_thay.get("thuoc_tinh") or {}),
        "tinh_nang": list(tim_thay.get("tinh_nang") or []),
        "rui_ro_mac_dinh": rr_mac_dinh,
        "rui_ro": rr_hieu_luc,
        "module": mod_id,
        "trang_thai": trang_thai,
    }


# ───────────── Phát sự kiện SSE ─────────────

class BoPhatSuKienThietBi:
    """Bộ phân phối sự kiện SSE cho các client đang kết nối."""

    def __init__(self):
        self._nghe: set[asyncio.Queue] = set()
        self._khoa = asyncio.Lock()

    async def dang_ky(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._khoa:
            self._nghe.add(q)
        return q

    async def huy(self, q: asyncio.Queue) -> None:
        async with self._khoa:
            self._nghe.discard(q)

    async def phat(self, su_kien: dict) -> None:
        async with self._khoa:
            cac_q = list(self._nghe)
        for q in cac_q:
            try:
                q.put_nowait(su_kien)
            except asyncio.QueueFull:
                pass

    def phat_dong_bo(self, su_kien: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.phat(su_kien))
        except RuntimeError:
            pass


BO_PHAT = BoPhatSuKienThietBi()
