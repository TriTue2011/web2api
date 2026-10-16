"""Cổng gửi ra đích danh của lõi: `POST /api/gui/{module}`.

Mọi tin gửi ra ngoài (kênh nhắn tin, email…) đi qua MỘT cửa duy nhất của lõi thay vì
mỗi module tự gọi nhà cung cấp. Ba lỗi c2a từng gặp thật được chặn ở tầng lõi:

1. **Gửi nhầm đích mà vẫn `ok: true`**: c2a không nêu `bot_id` thì tin ra bot ĐẦU
   danh sách. Ở đây `kenh_id` là BẮT BUỘC khi module có hơn một đích (400
   `kenh_required`, không đoán, không «đích đầu danh sách»); đích không có trong
   `kenh_ra()` của module → 404. Phản hồi luôn nêu `kenh` thực sự đã gửi để người
   gọi tự xác minh.
2. **Module báo láo kết quả**: lõi KHÔNG tin `ok` của module — kết quả phải là dict
   có `ok` là bool THẬT; `ok` thiếu hoặc không phải bool → coi là thất bại (502).
   Lỗi thô của nhà cung cấp không bao giờ lộ ra phản hồi (chỉ mã + câu chung; chi
   tiết vào log hệ thống).
3. **Gửi không trần**: hạn dòng theo (khoá, module) — mặc định 20 tin/phút — và
   trần đồng thời toàn cục — mặc định 8; vượt → 429 kèm `Retry-After`.

Xác thực: `V1_AUTH` (khoá quản trị hoặc khoá khách) VÀ phạm vi riêng `gui:<module>`
(`kiem_tra_pham_vi`) — không dùng chung với phạm vi chat/đọc; khoá `*` có mọi
phạm vi. Nội dung tin KHÔNG BAO GIỜ vào nhật ký hay phản hồi: sổ kênh `gui_ra` chỉ
ghi module, `kenh_id` đã băm, mã kết quả và độ dài.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import nhat_ky
from app.loi_openai import LoiOpenAI
from app.than_co_tran import Tran
from app.xac_thuc import xac_thuc_v1
from app.yeu_cau_da_phan import doc_multipart, la_multipart

log = logging.getLogger("web2api.gui_ra")

router = APIRouter(prefix="/api/gui", dependencies=[Depends(xac_thuc_v1)])

# ───────────── cấu hình (đọc mỗi lượt, đổi không cần khởi động lại) ─────────────

#: Trần tổng byte tệp đính kèm — theo cơ chế app/than_co_tran.py (Tran + loi_vuot).
TRAN_TEP = Tran("WEB2API_GUI_TEP_TOI_DA_MB", 25.0, "tệp đính kèm gửi ra")


def _so_nguyen(ten: str, mac_dinh: int, toi_thieu: int = 0) -> int:
    gia_tri = os.environ.get(ten, "").strip()
    if not gia_tri:
        return mac_dinh
    try:
        so = int(gia_tri)
        if so < toi_thieu:
            raise ValueError
        return so
    except ValueError:
        log.warning("%s=%r không hợp lệ, dùng mặc định %s", ten, gia_tri[:40], mac_dinh)
        return mac_dinh


def _tin_phut() -> int:
    return _so_nguyen("WEB2API_GUI_TIN_PHUT", 20)


def _dong_thoi_toi_da() -> int:
    return _so_nguyen("WEB2API_GUI_DONG_THOI", 8)


def _noi_dung_toi_da_byte() -> int:
    return _so_nguyen("WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE", 16 * 1024, toi_thieu=1)


def _thoi_gian_giay() -> float:
    gia_tri = os.environ.get("WEB2API_GUI_THOI_GIAN_GIAY", "").strip()
    if not gia_tri:
        return 60.0
    try:
        so = float(gia_tri)
        if not (so > 0) or so != so:
            raise ValueError
        return so
    except ValueError:
        log.warning("WEB2API_GUI_THOI_GIAN_GIAY=%r không hợp lệ, dùng mặc định 60", gia_tri[:40])
        return 60.0


# ───────────── trạng thái trong bộ nhớ (mất khi khởi động lại — đúng thiết kế) ─────────────

_KHOA = threading.Lock()
_HAN_DONG: dict[tuple[str, str], deque] = {}  # (id_khoa, module) -> mốc các tin trong 60s
_DANG_CHAY = 0  # số lượt gửi đang xử lý (toàn cục)


def _dat_lai_trang_thai() -> None:
    """Xoá trạng thái — chỉ dùng cho test."""
    global _DANG_CHAY
    with _KHOA:
        _HAN_DONG.clear()
        _DANG_CHAY = 0


# ───────────── tra cứu module ─────────────

def _lay_module(so, module_id: str):
    """(đối tượng module đang chạy, manifest) hoặc None nếu module không chạy/không khai kenh_ra."""
    try:
        m = so.dang_chay.get(module_id)
    except Exception:
        return None
    if m is None:
        return None
    km = so.catalog.get(module_id)
    man = getattr(km, "manifest", None)
    if not isinstance(man, dict):
        return None
    cung_cap = man.get("cung_cap", [])
    if "kenh_ra" not in cung_cap:
        return None
    if not callable(getattr(m, "gui_tin", None)) or not callable(getattr(m, "kenh_ra", None)):
        return None
    return m, man


def _ds_kenh_hop_le(ds) -> list[dict] | None:
    """Chuẩn hoá danh sách đích của module; None nếu hình dạng sai."""
    if not isinstance(ds, list):
        return None
    ra = []
    for muc in ds:
        if not isinstance(muc, dict):
            return None
        kid = muc.get("id")
        if not isinstance(kid, str) or not kid.strip():
            return None
        ra.append({"kenh_id": kid.strip(), "ten": muc.get("ten") if isinstance(muc.get("ten"), str) else None})
    return ra


def _ghi_nhat_ky(module: str, kenh_id: str | None, ket_qua: str, do_dai: int) -> None:
    muc = "thong_tin" if ket_qua == "ok" else "canh_bao"
    kenh_bam = hashlib.sha256(f"{module}:{kenh_id}".encode()).hexdigest()[:16] if kenh_id else None
    # KHÔNG ghi nội dung tin, KHÔNG ghi token — chỉ module, kenh đã băm, mã kết quả, độ dài.
    nhat_ky.ghi("gui_ra", muc, f"gui_ra {module} -> {ket_qua}", module=module,
                kenh_bam=kenh_bam, ket_qua=ket_qua, do_dai=do_dai)


def _loi_han_dong(retry_after: int) -> LoiOpenAI:
    return LoiOpenAI(
        429, "Vượt trần gửi ra — thử lại sau.", loai="rate_limit_error",
        code="rate_limit_exceeded", headers={"Retry-After": str(retry_after)},
    )


def _kiem_han_dong(id_khoa: str, module: str) -> None:
    """Trần tin/phút theo (khoá, module); vượt → 429 + Retry-After."""
    gioi_han = _tin_phut()
    bay_gio = time.monotonic()
    with _KHOA:
        moc = _HAN_DONG.setdefault((id_khoa, module), deque())
        han = bay_gio - 60.0
        while moc and moc[0] <= han:
            moc.popleft()
        if len(moc) >= gioi_han:
            con_lai = max(1, math.ceil(moc[0] + 60.0 - bay_gio))
            raise _loi_han_dong(con_lai)
        moc.append(bay_gio)


def _vao_dong_thoi() -> None:
    """Trần đồng thời toàn cục; vượt → 429 + Retry-After. Gọi _ra_dong_thoi() trong finally."""
    global _DANG_CHAY
    with _KHOA:
        if _DANG_CHAY >= _dong_thoi_toi_da():
            raise _loi_han_dong(1)
        _DANG_CHAY += 1


def _ra_dong_thoi() -> None:
    global _DANG_CHAY
    with _KHOA:
        _DANG_CHAY = max(0, _DANG_CHAY - 1)


def _co_pham_vi_gui(danh_tinh: dict, module: str) -> bool:
    """Khoá có phạm vi gửi ra cho module không?

    Phạm vi riêng ``gui:<module>`` (khoá ``*`` có mọi phạm vi; quản trị luôn có).
    Mã phạm vi (MA_KHA_NANG) không cho dấu gạch ngang trong khi id module cho
    phép, nên so cả dạng chuẩn hoá ``-`` → ``_`` (vd module ``mod-a`` dùng phạm vi
    ``gui:mod_a``) — ghi rõ trong tài liệu.
    """
    if danh_tinh.get("loai") == "quan_tri":
        return True
    pham_vi = danh_tinh.get("pham_vi", [])
    if "*" in pham_vi:
        return True
    ma = module.replace("-", "_")
    return f"gui:{module}" in pham_vi or f"gui:{ma}" in pham_vi


@router.post("/{module}")
async def gui_ra(request: Request, module: str):
    so = request.app.state.so
    danh_tinh = getattr(request.state, "danh_tinh", None) or {}
    # Phạm vi RIÊNG cho gửi ra — không dùng chung với phạm vi chat/đọc.
    if not _co_pham_vi_gui(danh_tinh, module):
        raise LoiOpenAI(
            403,
            f"Khoá không có quyền gửi ra qua module này (cần phạm vi «gui:{module}»).",
            loai="insufficient_scope",
            code="insufficient_scope",
        )
    id_khoa = str(danh_tinh.get("id", "khong_ro"))

    tim = _lay_module(so, module)
    if tim is None:
        raise LoiOpenAI(404, "Không có module gửi ra này.", code="module_khong_co")
    mod, _man = tim

    try:
        ds_kenh = _ds_kenh_hop_le(mod.kenh_ra())
    except Exception as e:
        log.warning("module %s.kenh_ra() ném %s", module, type(e).__name__)
        raise LoiOpenAI(502, "Module gửi ra lỗi.", code="module_loi") from e
    if ds_kenh is None:
        log.warning("module %s.kenh_ra() trả hình dạng sai", module)
        raise LoiOpenAI(502, "Module gửi ra lỗi.", code="module_loi")
    if not ds_kenh:
        raise LoiOpenAI(503, "Module chưa có đích gửi nào.", code="chua_co_kenh")

    # Đích danh: nhiều đích mà không nêu kenh_id → 400, KHÔNG đoán «đích đầu danh sách».
    than_so = await _doc_than_mong(request)
    kenh_id = than_so.get("kenh_id")
    if kenh_id is not None and not isinstance(kenh_id, str):
        raise LoiOpenAI(400, "Trường «kenh_id» phải là chuỗi.", code="invalid_request")
    kenh_id = kenh_id.strip() if isinstance(kenh_id, str) else None
    if not kenh_id:
        if len(ds_kenh) > 1:
            raise LoiOpenAI(400, "Phải nêu rõ «kenh_id» khi module có nhiều đích gửi.",
                            code="kenh_required")
        kenh_id = ds_kenh[0]["kenh_id"]
    dich = next((d for d in ds_kenh if d["kenh_id"] == kenh_id), None)
    if dich is None:
        raise LoiOpenAI(404, "Không có đích gửi này.", code="kenh_khong_co")

    _kiem_han_dong(id_khoa, module)
    _vao_dong_thoi()
    try:
        noi_dung, do_dai = than_so["noi_dung"], than_so["do_dai"]
        try:
            ket_qua = await asyncio.wait_for(mod.gui_tin(dich, noi_dung), timeout=_thoi_gian_giay())
        except asyncio.TimeoutError as e:
            _ghi_nhat_ky(module, kenh_id, "timeout", do_dai)
            raise LoiOpenAI(504, "Module gửi ra quá thời gian.", code="gui_timeout") from e
        except LoiOpenAI:
            raise
        except Exception as e:
            # Lỗi thô của nhà cung cấp KHÔNG lộ ra phản hồi — chỉ mã + câu chung.
            _ghi_nhat_ky(module, kenh_id, "loi_module", do_dai)
            log.exception("gui_tin của module %s ném %s", module, type(e).__name__)
            raise LoiOpenAI(502, "Gửi thất bại.", code="gui_that_bai") from e
    finally:
        _ra_dong_thoi()

    # Lõi KHÔNG tin `ok` của module: phải là dict có `ok` là bool THẬT.
    if not isinstance(ket_qua, dict) or not isinstance(ket_qua.get("ok"), bool):
        _ghi_nhat_ky(module, kenh_id, "ok_sai", do_dai)
        log.warning("module %s.gui_tin() trả kết quả không chuẩn (ok không phải bool)", module)
        raise LoiOpenAI(502, "Gửi thất bại.", code="gui_that_bai")
    if not ket_qua["ok"]:
        _ghi_nhat_ky(module, kenh_id, "that_bai", do_dai)
        raise LoiOpenAI(502, "Gửi thất bại.", code="gui_that_bai")

    _ghi_nhat_ky(module, kenh_id, "ok", do_dai)
    return JSONResponse({"ok": True, "kenh": kenh_id})


async def _doc_than_mong(request: Request) -> dict:
    """Đọc thân: {"kenh_id": str|None, "noi_dung": dict, "do_dai": int}."""
    if la_multipart(request):
        yc = await doc_multipart(request)
        kenh_id = yc.truong.get("kenh_id")
        if isinstance(kenh_id, list):
            kenh_id = kenh_id[0] if kenh_id else None
        noi_dung, do_dai = await _noi_dung_tu_multipart(yc)
        return {"kenh_id": kenh_id, "noi_dung": noi_dung, "do_dai": do_dai}
    tran = _noi_dung_toi_da_byte()
    dai = request.headers.get("content-length")
    if dai is not None:
        dai = dai.strip()
        if not (dai.isascii() and dai.isdigit()):
            raise LoiOpenAI(400, "Header Content-Length không hợp lệ", code="invalid_content_length")
        so = dai.lstrip("0") or "0"
        if len(so) > len(str(tran)) or int(so) > tran:
            raise LoiOpenAI(413, f"Nội dung gửi ra vượt trần {tran} byte "
                                 f"(đổi bằng WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE)",
                            code="request_too_large")
    tho = bytearray()
    async for khuc in request.stream():
        tho += khuc
        if len(tho) > tran:
            raise LoiOpenAI(413, f"Nội dung gửi ra vượt trần {tran} byte "
                                 f"(đổi bằng WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE)",
                            code="request_too_large")
    try:
        than = json.loads(tho)
    except (json.JSONDecodeError, ValueError):
        raise LoiOpenAI(400, "Thân phải là JSON object.", code="invalid_request")
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân phải là JSON object.", code="invalid_request")
    noi_dung = than.get("noi_dung", {})
    if not isinstance(noi_dung, dict):
        raise LoiOpenAI(400, "Trường «noi_dung» phải là object.", code="invalid_request")
    return {"kenh_id": than.get("kenh_id"), "noi_dung": noi_dung, "do_dai": len(tho)}


async def _noi_dung_tu_multipart(yc) -> tuple[dict, int]:
    """Trích noi_dung + tệp từ YeuCauDaPhan; kiểm trần tệp theo app/than_co_tran.py."""
    tong_tep = sum(len(t.noi_dung) for t in yc.tep_ten("tep"))
    if tong_tep > int(TRAN_TEP.so_mb() * 1024 * 1024):
        raise TRAN_TEP.loi_vuot(TRAN_TEP.so_mb())
    tho = yc.truong.get("noi_dung", "{}")
    if isinstance(tho, list):
        tho = tho[0] if tho else "{}"
    try:
        noi_dung = json.loads(tho)
    except (json.JSONDecodeError, TypeError):
        raise LoiOpenAI(400, "Trường «noi_dung» phải là JSON object.", code="invalid_request")
    if not isinstance(noi_dung, dict):
        raise LoiOpenAI(400, "Trường «noi_dung» phải là JSON object.", code="invalid_request")
    if yc.tep_ten("tep"):
        noi_dung["_tep"] = [
            {"ten": t.ten_tep, "kieu": t.content_type, "du_lieu": t.noi_dung}
            for t in yc.tep_ten("tep")
        ]
    return noi_dung, len(tho.encode("utf-8")) + tong_tep
