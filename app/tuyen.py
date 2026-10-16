"""Các tuyến API của lõi: /v1 (chuẩn OpenAI), /api/modules, /api/tinh-nang, /api/nguon-git, /m/<id>/.

Quyền: MỞ cho GET/POST /api/thiet-lap, POST /api/dang-nhap;
mọi tuyến API khác (/api/modules, /api/tinh-nang...) đòi khoá/phiên quản trị (QT) hoặc phiên web (PW).
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import math
import os
import re
import secrets
import time
from http import HTTPMethod

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.convertors import PathConvertor
from starlette.routing import Match
from starlette.types import Receive, Scope, Send
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from app import (
    combo as combo_mod,
    dia_chi as dia_chi_mod,
    gioi_han as gioi_han_mod,
    ip_cho_phep as ip_cho_phep_mod,
    kho,
    khoa_khach as khoa_khach_mod,
    nguoi_dung as nguoi_dung_mod,
    nhat_ky as nhat_ky_mod,
    phien as phien_mod,
    phien_web as phien_web_mod,
    sao_luu as sao_luu_mod,
    thiet_bi as thiet_bi_mod,
    tri_thuc as tri_thuc_mod,
    novnc as novnc_mod,
    xac_thuc,
    cong_cu as cong_cu_mod,
)
from app.kham_pha import ID_HOP_LE
from app.loi_openai import LoiOpenAI, lam_sach_thong_diep, nhac_lai
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiCanDuyetQuyen, LoiSo, SoModule
from app.than_co_tran import THAN_JSON, THAN_MULTIPART, doc_het
from app.xac_thuc import can_phien_web, can_quan_tri, kiem_tra_pham_vi, xac_thuc_v1
from app.yeu_cau_da_phan import TepTai, YeuCauDaPhan, doc_multipart, la_multipart

log = logging.getLogger("web2api.tuyen")
QT = [Depends(can_quan_tri)]
V1_AUTH = [Depends(xac_thuc_v1)]
PW = [Depends(can_phien_web)]


def _khong_luu_dem(response: Response) -> None:
    """Phản hồi mang bí mật MỘT LẦN (mật khẩu sinh ra, khoá thô, csrf): không cho trình duyệt/proxy lưu đệm —
    cùng cách `POST /api/sao-luu` đã làm."""
    response.headers["Cache-Control"] = "no-store"


KHONG_LUU = [Depends(_khong_luu_dem)]

v1 = APIRouter(prefix="/v1", dependencies=V1_AUTH)
#: Bắt mọi đường /v1 mà router `v1` không có — main.py include NGAY SAU `v1` (route đăng ký sau vào `v1` vẫn đứng
#: trước nó). Thiếu nó thì mount StaticFiles ở gốc nuốt mất và trả 405 {"detail"} cho mọi POST lạ.
v1_khong_co = APIRouter(prefix="/v1", dependencies=V1_AUTH)
modules = APIRouter(prefix="/api/modules", dependencies=QT)
tinh_nang = APIRouter(dependencies=QT)
nguon_git = APIRouter(prefix="/api/nguon-git", dependencies=QT)
dia_chi = APIRouter(prefix="/api", dependencies=QT)
nhat_ky = APIRouter(prefix="/api/nhat-ky", dependencies=QT)
khoa_khach = APIRouter(prefix="/api/khoa-khach", dependencies=QT)
phien = APIRouter(prefix="/api/phien", dependencies=QT)
combo = APIRouter(prefix="/api/combo", dependencies=QT)
sao_luu = APIRouter(prefix="/api", dependencies=QT)

thiet_bi = APIRouter(prefix="/api/thiet-bi", dependencies=V1_AUTH)
phe_duyet = APIRouter(prefix="/api/phe-duyet", dependencies=QT)
tim_kiem = APIRouter(prefix="/api/tim-kiem")
cong_cu = APIRouter(prefix="/api/cong-cu", dependencies=V1_AUTH)
tep_module = APIRouter()
trinh_duyet = novnc_mod.api_router
novnc = novnc_mod.novnc_router

thiet_lap = APIRouter(prefix="/api/thiet-lap")
dang_nhap = APIRouter(prefix="/api/dang-nhap")
dang_xuat = APIRouter(prefix="/api/dang-xuat", dependencies=PW)
toi = APIRouter(prefix="/api/toi", dependencies=PW)
nguoi_dung = APIRouter(prefix="/api/nguoi-dung", dependencies=QT)


def _nhac_lai(chuoi: str) -> str:
    """Chuỗi khách điều khiển (đường dẫn, tên model) nêu lại trong thông báo lỗi: dùng nhac_lai của loi_openai."""
    return nhac_lai(chuoi)


def _so(request: Request) -> SoModule:
    return request.app.state.so


def _ng(request: Request) -> NguonGit:
    return request.app.state.nguon_git


def _kiem_content_type_json(request: Request) -> None:
    """Chống Login CSRF: chỉ nhận Content-Type: application/json (so phần trước dấu ;, không phân biệt hoa thường)."""
    ct = request.headers.get("content-type", "")
    kieu = ct.split(";", 1)[0].strip().lower()
    if kieu != "application/json":
        raise LoiOpenAI(
            415,
            "Chỉ chấp nhận Content-Type: application/json",
            code="unsupported_media_type",
        )



async def _than_json(request: Request) -> dict:
    # Không dùng request.json(): nó nạp trọn thân không giới hạn (và giữ thêm một bản trong request suốt lượt gọi).
    # Thân đọc ngay trong lời gọi json.loads, KHÔNG gán biến: json.loads giải mã bytearray thành str rồi bỏ nó, bộ nhớ
    # đỉnh 2× thân thay vì 3× (test_bo_nho_dinh_than_json_vua_tran). Bước đọc chỉ ném LoiOpenAI (413/400 có mã riêng),
    # nên except dưới đây chỉ bắt lỗi giải mã: ValueError (cả UnicodeDecodeError) và RecursionError (mảng/đối tượng
    # lồng quá sâu — vẫn là thân hỏng của khách, không phải lỗi nội bộ).
    try:
        than = json.loads(await doc_het(request, THAN_JSON))
    except (ValueError, RecursionError):
        raise LoiOpenAI(400, "Thân yêu cầu không phải JSON hợp lệ") from None
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")
    return than


def _lay_bool(than: dict, ten: str, mac_dinh: bool | None = False) -> bool | None:
    """Lấy trường logic từ thân JSON của khách/quản trị: chỉ nhận đúng bool, vắng mặt hoặc null.

    Ép kiểu lỏng (`bool("false")` → True) từng khiến chuỗi "false" bị coi là đồng ý/xác nhận.
    Vắng mặt hoặc null → `mac_dinh` (mặc định False). Kiểu khác → 400 (theo mẫu kiểm trường
    trong tệp này).
    """
    if ten not in than or than[ten] is None:
        return mac_dinh
    gia_tri = than[ten]
    if not isinstance(gia_tri, bool):
        raise LoiOpenAI(400, f"«{ten}» phải là true/false.", code="invalid_request")
    return gia_tri


def _loi_so(e: LoiSo | LoiGit) -> LoiOpenAI:
    return LoiOpenAI(e.ma, lam_sach_thong_diep(str(e)))


# ───────────── /v1 ─────────────

def _nhom_cua_model(item: dict) -> str:
    kn = item.get("kha_nang")
    if isinstance(kn, list) and kn:
        dau = kn[0]
        return str(dau).strip() if dau else "chat"
    if isinstance(kn, str) and kn.strip():
        return kn.strip()
    return "chat"


async def _danh_sach_models(request: Request) -> list[dict]:
    so = _so(request)
    cac = [(id_, m) for id_, m in so.dang_chay.items() if callable(getattr(m, "models", None))]
    ket_qua = await asyncio.gather(*(m.models() for _, m in cac), return_exceptions=True)
    try:
        cai_dat_tat_ca = kho.doc_cai_dat_model()
    except kho.KhoHong:
        cai_dat_tat_ca = {}
    data = []
    for (id_, _), kq in zip(cac, ket_qua):
        if isinstance(kq, Exception):
            log.warning("Module %s lỗi khi liệt kê model, bỏ qua: %s: %s", id_, type(kq).__name__, kq)
            continue
        if isinstance(kq, BaseException):
            raise kq
        cai_dat = cai_dat_tat_ca.get(id_, {})
        tat_set = set(cai_dat.get("tat", []))
        mac_dinh_ids = set(cai_dat.get("mac_dinh", {}).values())
        for item in kq:
            m_id = item.get("id")
            if not isinstance(m_id, str) or m_id in tat_set:
                continue
            muc = dict(item)
            if m_id in mac_dinh_ids:
                muc["mac_dinh"] = True
            data.append(muc)

    data += combo_mod.muc_model(data)  # combo hỏng trong combo.json bị bỏ, không làm sập danh sách

    danh_tinh = getattr(request.state, "danh_tinh", None)
    if isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "khach":
        pham_vi = danh_tinh.get("pham_vi", [])
        if "*" not in pham_vi:
            data = [
                m for m in data
                if any(kn in pham_vi for kn in (m.get("kha_nang") or ["chat"]))
            ]
    return data


@v1.get("/models")
async def v1_models(request: Request):
    data = await _danh_sach_models(request)
    return {"object": "list", "data": data}


@v1.get("/models/{model:path}")
async def v1_model_retrieve(request: Request, model: str):
    raw_path = request.scope.get("raw_path", b"")
    if not model or not model.strip("/"):
        if raw_path.endswith(b"/"):
            tho = raw_path.rstrip(b"/")
            dich = tho + (b"?" + request.scope["query_string"] if request.scope.get("query_string") else b"")
            return RedirectResponse(dich.decode("latin-1"), status_code=307)
        raise LoiOpenAI(404, f"web2api không có đường {request.method} {_nhac_lai(request.url.path)} (chưa hỗ trợ). "
                             "Các đường /v1 đang có: xem README mục 8; danh sách model: GET /v1/models.",
                        loai="invalid_request_error", code="unknown_endpoint")
    request.state.model_khach = model
    data = await _danh_sach_models(request)
    for muc in data:
        if muc.get("id") == model:
            tien_to, co_gach, _ = model.partition("/")
            so = _so(request)
            id_ = so.tim_theo_tien_to(tien_to) if co_gach else None
            if id_ and id_ in so.dang_chay:
                _ghi_nhan_module(request, so, id_)
            return muc
    raise LoiOpenAI(404, f"Model {_nhac_lai(model)!r} không tồn tại hoặc đã tắt.", code="model_not_found")


def _module_dang_chay(so: SoModule, id_: str, tien_to: str):
    m = so.dang_chay.get(id_)
    if m is None:  # tìm thấy tiền tố mà không chạy → mã đã cài (catalog), chưa liên kết; module từ xa luôn chạy
        raise LoiOpenAI(404, f"Module {id_!r} (tiền tố {tien_to}/) đã cài nhưng chưa có khoá/cấu hình — mở "
                             f"Hệ thống → tab Module → ô «{so.catalog[id_].manifest['ten']}» trên trang web2api để "
                             f"thêm khoá (hoặc POST /api/modules/{id_}/cai)", code="module_not_configured")
    return m


def _ghi_nhan_module(request: Request, so: SoModule, id_: str) -> None:
    """Module đã nhận phục vụ lượt này: ghi vào request.state cho nhật ký `cuoc_goi` và tiêu đề X-Web2api-Module
    (app/nhat_ky.py) — lõi biết chắc module nào, không suy từ chuỗi khách gửi."""
    request.state.module = id_
    request.state.tien_to_module = so.tien_to_cua(id_)


def _module_cho_model(so: SoModule, model: str):
    """(id, thể hiện) của module phục vụ `model` dạng <tiền tố>/<tên> — MỘT cơ chế cho /v1 thường VÀ cho từng bước
    combo (app/combo.py nhận hàm này qua `phan_giai`). Không thuộc ai / chưa liên kết / model đã tắt → LoiOpenAI 404
    có mã riêng (model_not_found, module_not_configured). Không ghi gì vào request."""
    tien_to, co_gach, _ = model.partition("/")
    id_ = so.tim_theo_tien_to(tien_to) if co_gach else None
    if id_ is None:
        cai = ", ".join(so.tien_to_dang_chay()) or "(chưa có module nào đã liên kết)"
        raise LoiOpenAI(404, f"Model {model!r} không thuộc module nào. Model phải có dạng <tiền tố>/<tên>; "
                             f"tiền tố đang dùng được: {cai}. Xem GET /v1/models.", code="model_not_found")
    m = _module_dang_chay(so, id_, tien_to)
    try:
        tat_set = set(kho.doc_cai_dat_model_module(id_).get("tat", []))
    except kho.KhoHong:
        tat_set = set()
    if model in tat_set:
        raise LoiOpenAI(404, f"Model {model!r} đã tắt trong Hệ thống → AI (xem GET /v1/models).",
                        code="model_not_found")
    return id_, m


def _combo_theo_model(request: Request, so: SoModule, model: str):
    """Thể hiện combo nếu `model` gọi combo, None nếu không. Thứ tự phân giải: tiền tố của một module (kể cả module
    mang tiền tố «combo») → `combo/<tên>` → tên trần không «/» trùng tên combo. Chỉ đọc combo.json khi model có
    dạng gọi combo — lượt gọi module thường không chạm đĩa."""
    tien_to, co_gach, ten = model.partition("/")
    if co_gach and (tien_to != "combo" or so.tim_theo_tien_to(tien_to) is not None):
        return None
    if not co_gach:
        ten = model
    if not ID_HOP_LE.fullmatch(ten):
        return None
    cb = combo_mod.lay(ten)
    if cb is None:
        if co_gach:
            raise LoiOpenAI(404, f"Không có combo {ten!r} (xem GET /v1/models).", code="model_not_found")
        return None
    request.state.module = None
    request.state.tien_to_module = None
    return combo_mod.BoDieuPhoiCombo(ten, cb, so, request, phan_giai=lambda mdl: _module_cho_model(so, mdl))


def _module_theo_model(request: Request, than: dict):
    """(id, thể hiện) của module có tiền tố ghi trong `model` — 404 rõ ràng nếu không thuộc ai/chưa liên kết.
    `model` gọi combo → ("combo/<tên>", thể hiện combo) có `chat`/`anh` như module thật."""
    so = _so(request)
    model = than.get("model")
    if isinstance(model, str):
        request.state.model_khach = model  # ghi TRƯỚC mọi lỗi: lượt 404 model_not_found vẫn có model trong nhật ký
    if not isinstance(model, str) or not model:
        raise LoiOpenAI(400, "Thiếu trường «model» (dạng <tiền tố>/<tên>; danh sách ở GET /v1/models)",
                        code="missing_model")
    cb = _combo_theo_model(request, so, model)
    if cb is not None:
        return f"combo/{cb.ten}", cb
    id_, m = _module_cho_model(so, model)
    _ghi_nhan_module(request, so, id_)
    return id_, m


def _module_theo_ma_video(request: Request, ma: str):
    """Mã video ra ngoài là "<tien_to>:<mã của module>"; tiền tố không chứa «:» (TIEN_TO_HOP_LE) nên tách ở
    dấu «:» đầu tiên là đủ — không cần bảng mã → module."""
    so = _so(request)
    tien_to, co_cham, trong = ma.partition(":")
    id_ = so.tim_theo_tien_to(tien_to) if co_cham and trong else None
    if id_ is None:
        raise LoiOpenAI(404, f"Không có video {ma!r} (mã dạng <tiền tố>:<mã>, lấy từ POST /v1/videos)",
                        code="video_not_found")
    m = _module_dang_chay(so, id_, tien_to)
    _ghi_nhan_module(request, so, id_)
    return id_, m, trong


def _can_phuong_thuc(m, id_: str, viec: str, *ten: str) -> None:
    """Khả năng tuỳ chọn: xét theo phương thức CÓ THẬT trên thể hiện (module từ xa, module cũ không có → 501)."""
    if not all(callable(getattr(m, t, None)) for t in ten):
        raise LoiOpenAI(501, f"Module {id_!r} không hỗ trợ {viec} (không có phương thức {', '.join(ten)}).",
                        code="capability_not_supported")


def _can_tep(yc: YeuCauDaPhan, ten: str, toi_thieu: int = 1, toi_da: int | None = None) -> None:
    """Phần tệp bắt buộc theo CHUẨN OpenAI của đường (không theo nhà cung cấp nào) — lõi dựng YeuCauDaPhan nên
    lõi bảo đảm hình của nó; module nhận được là có đủ tệp."""
    so = len(yc.tep_ten(ten))
    if so < toi_thieu:
        raise LoiOpenAI(400, f"Thiếu tệp «{ten}» (phần multipart có filename, không phải trường chữ)",
                        code="missing_file")
    if toi_da is not None and so > toi_da:
        raise LoiOpenAI(400, f"Chỉ nhận tối đa {toi_da} tệp «{ten}», nhận {so}", code="too_many_files")


def sse_giu_giay() -> float:
    """Số giây im lặng tối đa của stream trước khi lõi chen `: keep-alive` (WEB2API_SSE_GIU_GIAY, mặc định 15).
    Giá trị không phải số dương hữu hạn (0, âm, chữ, nan, inf) → mặc định: đặt sai không được tắt keep-alive."""
    try:
        val = float(os.environ.get("WEB2API_SSE_GIU_GIAY", "15"))
    except ValueError:
        return 15.0
    return val if val > 0 and math.isfinite(val) else 15.0


def sse_dem_toi_da_bytes() -> int:
    """Trần bộ đệm (byte) của luồng SSE chat trước khi phát nguyên văn (WEB2API_SSE_DEM_TOI_DA_KB, mặc định 1024 KiB).
    Giá trị không phải số dương hữu hạn (0, âm, chữ, nan, inf) → mặc định 1024 KiB: đặt sai không được tắt trần."""
    try:
        val = float(os.environ.get("WEB2API_SSE_DEM_TOI_DA_KB", "1024"))
    except (ValueError, TypeError):
        return 1024 * 1024
    if not (val > 0 and math.isfinite(val)):
        return 1024 * 1024
    so_byte = int(val * 1024)
    return so_byte if so_byte > 0 else 1024 * 1024


def _index_hop_le(choice: dict) -> int | None:
    """`index` của một choice: thiếu → 0 (một choice); không phải số nguyên thật (list, dict, bool, chuỗi, số thực)
    → None: choice đó chuyển nguyên văn, không ném (giá trị lạ có thể không băm được)."""
    idx = choice.get("index", 0)
    return idx if isinstance(idx, int) and not isinstance(idx, bool) else None


def _co_tool_calls(choice: dict) -> bool:
    msg = choice.get("message") or choice.get("delta")
    return isinstance(msg, dict) and isinstance(msg.get("tool_calls"), list) and len(msg["tool_calls"]) > 0


def _chuan_hoa_chat_thuong(noi_dung: bytes) -> bytes:
    """Phản hồi chat KHÔNG stream có tool_calls mà finish_reason là "stop" hoặc thiếu/null → "tool_calls" (SDK
    OpenAI/LangChain/n8n bỏ qua lời gọi hàm khi thấy "stop"). Thân không stream là trọn một lượt nên «thiếu» cũng là
    đã kết thúc. "length"/"content_filter" GIỮ NGUYÊN: lời gọi hàm bị cắt có tham số JSON đứt, gắn "tool_calls" là
    báo sai rằng nó hoàn chỉnh. Quyết bằng cấu trúc JSON; thân không phải JSON đúng dạng → nguyên văn."""
    try:
        j = json.loads(noi_dung)
    except (ValueError, RecursionError):
        return noi_dung
    choices = j.get("choices") if isinstance(j, dict) else None
    if not isinstance(choices, list):
        return noi_dung
    da_sua = False
    for choice in choices:
        if isinstance(choice, dict) and _co_tool_calls(choice) and choice.get("finish_reason") in ("stop", None):
            choice["finish_reason"] = "tool_calls"
            da_sua = True
    return json.dumps(j, ensure_ascii=False).encode("utf-8") if da_sua else noi_dung


def _chuan_hoa_khuc_stream(su_kien: bytes, choices_co_tool: set[int]) -> bytes:
    """Chuẩn hoá MỘT sự kiện SSE trọn vẹn: choice đã có tool_calls (ở sự kiện này hoặc trước đó, nhớ theo `index`
    trong `choices_co_tool`) mà kết thúc bằng finish_reason "stop" → "tool_calls". Trong stream, null nghĩa là CHƯA
    xong nên không đụng; "length"/"content_filter" giữ nguyên (xem _chuan_hoa_chat_thuong). Dòng nào không phải
    `data:` JSON đúng dạng → nguyên văn; dòng được ghi lại giữ nguyên ký tự cuối dòng (\r)."""
    if b"data:" not in su_kien:
        return su_kien
    da_sua_khuc = False
    dong_moi = []
    for dong in su_kien.split(b"\n"):
        than = dong.strip()
        j = None
        if than.startswith(b"data:") and than[5:].strip() != b"[DONE]":
            try:
                j = json.loads(than[5:])
            except (ValueError, RecursionError):
                j = None
        choices = j.get("choices") if isinstance(j, dict) else None
        da_sua_dong = False
        for choice in choices if isinstance(choices, list) else ():
            if not isinstance(choice, dict):
                continue
            idx = _index_hop_le(choice)
            if idx is None:
                continue
            if _co_tool_calls(choice):
                choices_co_tool.add(idx)
            if choice.get("finish_reason") == "stop" and idx in choices_co_tool:
                choice["finish_reason"] = "tool_calls"
                da_sua_dong = True
        if da_sua_dong:
            da_sua_khuc = True
            dong = b"data: " + json.dumps(j, ensure_ascii=False).encode("utf-8") + dong[len(dong.rstrip()):]
        dong_moi.append(dong)
    return b"\n".join(dong_moi) if da_sua_khuc else su_kien


#: Kết thúc một sự kiện SSE = một dòng trống (dòng kết bằng LF hoặc CRLF).
_HET_SU_KIEN = re.compile(rb"\r?\n\r?\n")
_KEEP_ALIVE = b": keep-alive\n\n"


async def _khuc_ke(it):
    return await anext(it)


async def _dong_luong_module(cho: asyncio.Task | None, it) -> None:
    """Huỷ tác vụ đang chờ khúc kế (nếu còn), ĐỢI nó dừng hẳn, rồi mới aclose() luồng module — aclose khi luồng còn
    chạy trong tác vụ kia ném «already running» và không đóng được gì."""
    if cho is not None:
        cho.cancel()
        await asyncio.wait({cho})
        if not cho.cancelled():
            cho.exception()  # đã lấy: không cảnh báo «exception was never retrieved»
    dong = getattr(it, "aclose", None)
    if callable(dong):
        try:
            await dong()
        except Exception as e:  # luồng module hỏng lúc đóng: không đổi kết cục của lượt gọi
            log.warning("Đóng luồng stream của module lỗi: %s", type(e).__name__)


async def _luong_sse_chuan_hoa(iterator, giu_giay: float, dem_toi_da: int | None = None):
    """Bọc stream chat của module: chuẩn hoá finish_reason và chen `: keep-alive` khi im lặng quá `giu_giay`.

    Lõi TỰ đệm theo ranh giới sự kiện SSE (dòng trống), không dựa cách module chia khúc (từng dòng, từng mẩu byte hay
    nhiều sự kiện một khúc): chỉ chuẩn hoá và phát SỰ KIỆN TRỌN, mỗi sự kiện phát ngay khi đủ ranh giới; keep-alive chỉ
    chen khi bộ đệm rỗng (không đang dở một sự kiện). Bộ đệm có trần `dem_toi_da` (mặc định đọc từ
    WEB2API_SSE_DEM_TOI_DA_KB qua sse_dem_toi_da_bytes()): nếu vượt trần mà chưa gặp ranh giới sự kiện, phát nguyên văn
    phần đã đệm để tránh cạn bộ nhớ và ghi đúng một cảnh báo cho mỗi lượt gọi. Quét ranh giới bắt đầu từ
    max(0, độ dài bộ đệm cũ − 3) để tránh chi phí bậc hai O(N^2). Phần dở dang lúc hết luồng phát nguyên văn.

    Mọi đường thoát — đọc hết, module lỗi, khách ngắt lúc chờ (CancelledError), generator bị đóng khi đang dừng ở
    `yield` (GeneratorExit) — đều đi qua `finally`: huỷ tác vụ chờ và aclose() luồng module, để luồng ngược dòng không
    chạy tiếp giữ kết nối và token. Việc dọn được che chắn (shield) vì cancel scope của anyio (Starlette) huỷ lặp lại
    ở mọi await còn trong scope."""
    if dem_toi_da is None:
        dem_toi_da = sse_dem_toi_da_bytes()
    co_tool: set[int] = set()
    it = iterator.__aiter__()
    cho: asyncio.Task | None = None
    dem = b""
    canh_bao_tran = False
    try:
        while True:
            if cho is None:
                cho = asyncio.create_task(_khuc_ke(it))
            xong, _ = await asyncio.wait({cho}, timeout=giu_giay)
            if not xong:
                if not dem:
                    yield _KEEP_ALIVE
                continue
            tac_vu, cho = cho, None
            try:
                khuc = tac_vu.result()
            except StopAsyncIteration:
                break
            len_cu = len(dem)
            dem += khuc.encode("utf-8") if isinstance(khuc, str) else khuc
            pos = max(0, len_cu - 3)
            while (het := _HET_SU_KIEN.search(dem, pos)) is not None:
                su_kien, dem = dem[:het.end()], dem[het.end():]
                yield _chuan_hoa_khuc_stream(su_kien, co_tool)
                pos = 0
            if dem and not dem.strip(b"\r\n"):  # chỉ còn dòng trống lẻ giữa hai sự kiện: không dở gì, phát luôn
                yield dem
                dem = b""
            elif len(dem) > dem_toi_da:
                if not canh_bao_tran:
                    log.warning("Bộ đệm SSE vượt trần %d byte, phát nguyên văn để tránh cạn bộ nhớ", dem_toi_da)
                    canh_bao_tran = True
                yield dem
                dem = b""
        if dem:
            yield dem
    finally:
        await asyncio.shield(_dong_luong_module(cho, it))


class PhanHoiDong(StreamingResponse):
    """StreamingResponse bảo đảm gọi aclose() trên iterator của module đúng một lần khi hoàn thành hoặc ngắt."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        da_dong = False

        async def _dong():
            nonlocal da_dong
            if not da_dong:
                da_dong = True
                dong = getattr(self.body_iterator, "aclose", None)
                if callable(dong):
                    try:
                        await dong()
                    except Exception as e:  # như _dong_luong_module: không đổi kết cục lượt gọi, nhưng không im lặng
                        log.warning("Đóng luồng nhị phân của module lỗi: %s", type(e).__name__)

        try:
            await super().__call__(scope, receive, send)
        finally:
            await _dong()


def _tra_ve(ma: int, headers: dict, noi_dung) -> Response:
    headers = dict(headers)
    kieu = headers.pop("content-type", None)
    if isinstance(noi_dung, (bytes, str)):
        # Thân đã có trọn: Starlette tự tính content-length đúng; số của module (có thể lệch) bị bỏ.
        headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
        return Response(noi_dung, status_code=ma, headers=headers, media_type=kieu)
    bo_stream = {"content-length", "content-encoding", "transfer-encoding"}
    headers = {k: v for k, v in headers.items() if k.lower() not in bo_stream}
    return PhanHoiDong(noi_dung, status_code=ma, headers=headers, media_type=kieu)


def _gan_tien_to_ma_video(tien_to: str, ma: int, headers: dict, noi_dung) -> Response:
    """Đối tượng video thành công: "id" của module → "<tien_to>:<id>" (khách gửi lại đúng mã này)."""
    if ma == 200 and isinstance(noi_dung, bytes):
        j = json.loads(noi_dung)
        if isinstance(j, dict) and isinstance(j.get("id"), str):
            j["id"] = f"{tien_to}:{j['id']}"
            noi_dung = json.dumps(j, ensure_ascii=False).encode()
    return _tra_ve(ma, headers, noi_dung)


@v1.post("/chat/completions")
async def v1_chat(request: Request):
    kiem_tra_pham_vi(request, "chat")
    # Đọc thân như dict THÔ (không qua mô hình Pydantic): mô hình sẽ lặng lẽ bỏ trường lạ
    # như chữ ký suy nghĩ hay trường riêng của nhà cung cấp.
    than = await _than_json(request)
    stream = _lay_bool(than, "stream", False)
    id_, m = _module_theo_model(request, than)
    _can_phuong_thuc(m, id_, "trò chuyện", "chat")
    ma, headers, noi_dung = await m.chat(than, stream)
    if ma == 200:
        if stream and hasattr(noi_dung, "__aiter__"):
            noi_dung = _luong_sse_chuan_hoa(noi_dung, sse_giu_giay(), sse_dem_toi_da_bytes())
        elif isinstance(noi_dung, bytes):
            noi_dung = _chuan_hoa_chat_thuong(noi_dung)
    return _tra_ve(ma, headers, noi_dung)


@v1.post("/images/generations")
async def v1_anh(request: Request):
    kiem_tra_pham_vi(request, "anh")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    _can_phuong_thuc(m, id_, "tạo ảnh", "anh")
    _kiem_n_anh(than.get("n"))
    return _tra_ve(*await m.anh(than))


@v1.post("/images/edits")
async def v1_anh_sua(request: Request):
    kiem_tra_pham_vi(request, "anh_sua")
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(request, yc.truong)
    _can_phuong_thuc(m, id_, "sửa ảnh", "anh_sua")
    _can_tep(yc, "image")
    _can_tep(yc, "mask", toi_thieu=0, toi_da=1)
    prompt = yc.truong.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise LoiOpenAI(400, "Thiếu trường «prompt» (mô tả cần sửa gì)", code="missing_prompt")
    _kiem_n_anh(yc.truong.get("n"), tu_form=True)
    return _tra_ve(*await m.anh_sua(yc))


def _kiem_tra_va_tach_tool_image_generation(than: dict) -> dict:
    tools = than.get("tools")
    if not isinstance(tools, list) or len(tools) != 1:
        raise LoiOpenAI(400, "Chỉ hỗ trợ công cụ image_generation", code="unsupported_tool")
    tool = tools[0]
    if not isinstance(tool, dict) or tool.get("type") != "image_generation":
        raise LoiOpenAI(400, "Chỉ hỗ trợ công cụ image_generation", code="unsupported_tool")
    return tool


#: Vai của chuẩn Responses → phần của prompt Images một lượt. developer/system là CHỈ DẪN (cùng nghĩa với trường
#: `instructions`); user là lời yêu cầu; assistant là lượt trả lời cũ — Images không có hội thoại, ghép vào là bịa thêm
#: yêu cầu nên bỏ. Đây là bảng ÁNH XẠ giữa hai hợp đồng (đủ bốn vai của đặc tả), không phải danh sách dò chữ: vai
#: ngoài bảng → 400, không đoán nghĩa.
_PHAN_THEO_VAI = {"developer": "chi_dan", "system": "chi_dan", "user": "yeu_cau", "assistant": None}


def _trich_xuat_responses_prompt_va_anh(than: dict) -> tuple[str, list[str]]:
    """(prompt, các URL ảnh đầu vào) từ thân Responses. Prompt = phần chỉ dẫn (`instructions`, rồi tin developer/system
    theo thứ tự gặp) + phần yêu cầu (tin user và mục không có vai), nối bằng dòng trống. Ảnh lấy ở mọi tin không bị
    bỏ."""
    raw_input = than.get("input")
    instructions = than.get("instructions")
    chi_dan: list[str] = []
    yeu_cau: list[str] = []
    input_images: list[str] = []
    if isinstance(instructions, str) and instructions.strip():
        chi_dan.append(instructions.strip())

    def _xu_ly_phan(phan, dich: list[str]):
        if not isinstance(phan, dict):
            if isinstance(phan, str) and phan.strip():
                dich.append(phan.strip())
            return
        ptype = phan.get("type")
        if ptype == "input_text":
            txt = phan.get("text")
            if isinstance(txt, str) and txt.strip():
                dich.append(txt.strip())
        elif ptype == "input_image":
            img = phan.get("image_url")
            if isinstance(img, str) and img.strip():
                input_images.append(img.strip())
            elif isinstance(img, dict) and isinstance(img.get("url"), str) and img["url"].strip():
                input_images.append(img["url"].strip())

    if isinstance(raw_input, str):
        if raw_input.strip():
            yeu_cau.append(raw_input.strip())
    elif isinstance(raw_input, list):
        for muc in raw_input:
            if isinstance(muc, str):
                if muc.strip():
                    yeu_cau.append(muc.strip())
            elif isinstance(muc, dict):
                role = muc.get("role")
                if role is None:
                    phan = "yeu_cau"
                elif isinstance(role, str) and role in _PHAN_THEO_VAI:
                    phan = _PHAN_THEO_VAI[role]
                else:
                    raise LoiOpenAI(400, "«role» của mục trong «input» phải là user, developer, system hoặc assistant",
                                    code="invalid_value")
                if phan is None:
                    continue
                dich = chi_dan if phan == "chi_dan" else yeu_cau
                content = muc.get("content")
                if content is None:
                    _xu_ly_phan(muc, dich)
                elif isinstance(content, str):
                    if content.strip():
                        dich.append(content.strip())
                elif isinstance(content, list):
                    for p in content:
                        _xu_ly_phan(p, dich)

    return "\n\n".join(chi_dan + yeu_cau), input_images


#: Trần `n` (số ảnh một lượt) của MỌI đường ảnh — đúng trần trong đặc tả OpenAI Images API («Must be between 1 and
#: 10»). Lõi chặn ở biên để một lượt gọi không nhân chi phí/hạn mức của nhà cung cấp lên tuỳ ý; module vẫn có thể
#: chặn chặt hơn theo model.
N_ANH_TOI_DA = 10
_SO_NGUYEN_ASCII = re.compile(r"[0-9]{1,6}")


def _kiem_n_anh(n, *, tu_form: bool = False) -> None:
    """Kiểm `n` cho /v1/images/generations, /v1/images/edits và /v1/responses — một hàm, một luật. None (thiếu hoặc
    null) = module dùng mặc định. Thân JSON: số nguyên thật (bool không phải số). Trường form multipart luôn là chuỗi
    (`tu_form`): nhận chuỗi chữ số ASCII. Sai kiểu hoặc ngoài [1, N_ANH_TOI_DA] → 400."""
    if n is None:
        return
    if tu_form and isinstance(n, str) and _SO_NGUYEN_ASCII.fullmatch(n.strip()):
        n = int(n)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= N_ANH_TOI_DA:
        raise LoiOpenAI(400, f"«n» phải là số nguyên từ 1 đến {N_ANH_TOI_DA}", code="invalid_value")


#: Tham số của công cụ image_generation lõi chuyển sang module — cùng tên trường của /v1/images/generations và
#: /v1/images/edits (đặc tả OpenAI), nên module nhận y như từ đường chuẩn; cũng là các thuộc tính mục output của
#: Responses nêu lại từ KẾT QUẢ. Bảng ÁNH XẠ giữa hai hợp đồng, không phải danh sách dò chữ.
_THAM_SO_CHUOI_ANH = ("size", "quality", "background", "output_format")


def _giai_ma_input_images(input_images: list[str]) -> list[TepTai]:
    tep_list: list[TepTai] = []
    mb_tran = THAN_MULTIPART.so_mb()
    byte_tran = int(mb_tran * 1024 * 1024)
    tong_byte = 0

    for idx, url in enumerate(input_images):
        url = url.strip()
        if url.startswith(("http://", "https://")):
            raise LoiOpenAI(400, "Không hỗ trợ tải ảnh từ URL từ xa qua đường này (chống SSRF)",
                            code="unsupported_image_url")
        if not url.startswith("data:"):
            raise LoiOpenAI(400, "Ảnh đầu vào phải là data URL base64", code="unsupported_image_url")

        header, sep, b64_str = url.partition(",")
        if not sep or not header.startswith("data:") or ";base64" not in header:
            raise LoiOpenAI(400, "Data URL ảnh không hợp lệ (cần data:image/...;base64,...)",
                            code="unsupported_image_url")

        phan_dau = header[len("data:"):].strip()
        mime = phan_dau.split(";")[0].strip().lower()
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise LoiOpenAI(400, f"Định dạng ảnh không được hỗ trợ: {_nhac_lai(mime)} (chỉ nhận image/png, image/jpeg, "
                                 "image/webp)", code="unsupported_image_url")

        try:
            noi_dung = base64.b64decode(b64_str, validate=True)
        except Exception:
            raise LoiOpenAI(400, "Dữ liệu base64 của ảnh không hợp lệ", code="unsupported_image_url")

        tong_byte += len(noi_dung)
        if len(noi_dung) > byte_tran or tong_byte > byte_tran:
            raise THAN_MULTIPART.loi_vuot(mb_tran)

        duoi = "png" if mime == "image/png" else ("jpg" if mime == "image/jpeg" else "webp")
        tep_list.append(TepTai(
            ten_truong="image",
            ten_tep=f"input_image_{idx}.{duoi}",
            content_type=mime,
            noi_dung=noi_dung,
        ))
    return tep_list


def _dinh_dang_anh(b64: str) -> str | None:
    """Định dạng thật của ảnh theo chữ ký đầu tệp (chuẩn của chính PNG/JPEG/WebP, không theo nhà cung cấp) — để mục
    output nêu `output_format` của KẾT QUẢ khi module không nêu, thay vì lặp lại định dạng khách xin."""
    try:
        dau = base64.b64decode(b64[:24], validate=True)  # 24 ký tự base64 = 18 byte đầu, đủ cho cả ba chữ ký
    except ValueError:
        return None
    if dau.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if dau.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if dau[:4] == b"RIFF" and dau[8:12] == b"WEBP":
        return "webp"
    return None


def _xu_ly_ket_qua_module_anh(noi_dung, model_khach: str) -> dict:
    """Phản hồi THÀNH CÔNG của module (thân /v1/images/*) → đối tượng `response`. Thuộc tính ảnh (size, quality,
    background, output_format) chỉ nêu khi biết từ kết quả: trường cấp đầu của phản hồi module, hoặc — riêng
    output_format — chữ ký byte ảnh. Không lặp tham số khách như thể là kết quả."""
    if not isinstance(noi_dung, (bytes, bytearray, str)):
        raise LoiOpenAI(502, "Phản hồi từ module không hợp lệ", code="upstream_error")

    try:
        data_json = json.loads(noi_dung)
    except Exception:
        raise LoiOpenAI(502, "Phản hồi từ module không phải JSON hợp lệ", code="upstream_error")

    if not isinstance(data_json, dict) or not isinstance(data_json.get("data"), list):
        raise LoiOpenAI(502, "Phản hồi từ module thiếu danh sách dữ liệu ảnh «data»", code="upstream_error")

    cac_anh = data_json["data"]
    if not cac_anh:
        raise LoiOpenAI(502, "Phản hồi từ module không có ảnh nào trong «data»", code="upstream_error")

    thuoc_tinh = {k: data_json[k] for k in _THAM_SO_CHUOI_ANH if isinstance(data_json.get(k), str)}
    output_items = []
    for phan_tu in cac_anh:
        if not isinstance(phan_tu, dict):
            continue
        if "url" in phan_tu and "b64_json" not in phan_tu:
            raise LoiOpenAI(502, "Module trả về URL thay vì b64_json; Responses API cần dữ liệu ảnh",
                            code="upstream_error")
        b64 = phan_tu.get("b64_json")
        if not isinstance(b64, str) or not b64:
            raise LoiOpenAI(502, "Phản hồi từ module không chứa b64_json", code="upstream_error")

        if b64.startswith("data:"):
            _, sep, phan_thuan = b64.partition(",")
            if sep:
                b64 = phan_thuan

        item = {
            "id": f"ig_{secrets.token_hex(12)}",
            "type": "image_generation_call",
            "status": "completed",
            "result": b64,
        }
        if phan_tu.get("revised_prompt"):
            item["revised_prompt"] = phan_tu["revised_prompt"]
        item.update(thuoc_tinh)
        if "output_format" not in item and (dinh_dang := _dinh_dang_anh(b64)):
            item["output_format"] = dinh_dang
        output_items.append(item)

    if not output_items:
        raise LoiOpenAI(502, "Không có ảnh hợp lệ nào trong phản hồi của module", code="upstream_error")

    resp_id = f"resp_{secrets.token_hex(12)}"
    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model_khach,
        "output": output_items,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


async def _sse_responses_anh(resp_obj: dict):
    """Chuỗi sự kiện Responses cho kết quả ảnh đã có trọn. Lỗi giữa chừng (mã 200 đã gửi) → MỘT sự kiện `error` đúng
    khuôn Responses (type, sequence_number, code, message, param) rồi dừng; chữ của ngoại lệ chỉ vào log (tên lớp)."""
    seq = 0

    def _su_kien(ten: str, du_lieu: dict) -> str:
        return f"event: {ten}\ndata: {json.dumps({'type': ten, 'sequence_number': seq, **du_lieu}, ensure_ascii=False)}\n\n"

    try:
        resp_khoi_tao = {**resp_obj, "status": "in_progress", "output": [], "usage": None}
        yield _su_kien("response.created", {"response": resp_khoi_tao})
        seq += 1
        yield _su_kien("response.in_progress", {"response": resp_khoi_tao})
        seq += 1
        for out_idx, it in enumerate(resp_obj["output"]):
            it_in_progress = {"id": it["id"], "type": "image_generation_call", "status": "in_progress",
                              **{k: it[k] for k in _THAM_SO_CHUOI_ANH if k in it}}
            yield _su_kien("response.output_item.added", {"output_index": out_idx, "item": it_in_progress})
            seq += 1
            yield _su_kien("response.image_generation_call.in_progress", {"output_index": out_idx, "item_id": it["id"]})
            seq += 1
            yield _su_kien("response.image_generation_call.generating", {"output_index": out_idx, "item_id": it["id"]})
            seq += 1
            yield _su_kien("response.output_item.done", {"output_index": out_idx, "item": it})
            seq += 1
        yield _su_kien("response.completed", {"response": resp_obj})
    except Exception as e:
        log.warning("Stream /v1/responses lỗi giữa chừng: %s", type(e).__name__)
        yield _su_kien("error", {"code": "stream_error", "message": "Lỗi khi phát sự kiện stream của web2api",
                                 "param": None})


@v1.post("/responses")
async def v1_responses(request: Request):
    # Phạm vi theo CHỨC NĂNG THẬT, cùng luật với đường chuẩn: có ảnh đầu vào là sửa ảnh → «anh_sua» như
    # /v1/images/edits; không có là tạo ảnh → «anh» như /v1/images/generations. Chưa đọc thân thì chưa biết chức
    # năng, nên cửa đầu chỉ cho khoá có MỘT trong hai (khoá không quyền ảnh nào → 403 trước khi đọc thân); đòi đúng
    # phạm vi ngay sau khi biết chức năng, TRƯỚC khi tra model (khoá hẹp không dò được model nào tồn tại).
    kiem_tra_pham_vi(request, "anh", "anh_sua")
    than = await _than_json(request)
    # Kiểm kiểu NGAY khi đọc thân, trước mọi việc tốn tài nguyên: sai kiểu mà để sau lệnh gọi module thì ảnh đã
    # sinh (đã tốn hạn mức nhà cung cấp) rồi khách mới nhận 400.
    stream = _lay_bool(than, "stream", False)
    tool = _kiem_tra_va_tach_tool_image_generation(than)
    prompt, input_images = _trich_xuat_responses_prompt_va_anh(than)
    sua_anh = bool(input_images)
    kiem_tra_pham_vi(request, "anh_sua" if sua_anh else "anh")
    if not prompt:
        raise LoiOpenAI(400, "Thiếu mô tả yêu cầu trong «input» hoặc «instructions»", code="missing_prompt")
    _kiem_n_anh(tool.get("n"))
    tham_so = {k: tool[k] for k in _THAM_SO_CHUOI_ANH if tool.get(k) is not None}
    if not all(isinstance(v, str) for v in tham_so.values()):
        raise LoiOpenAI(400, f"Tham số {', '.join(_THAM_SO_CHUOI_ANH)} của công cụ image_generation phải là chuỗi",
                        code="invalid_value")

    id_, m = _module_theo_model(request, than)
    model_khach = request.state.model_khach
    # Thân/yêu cầu cho module dựng y như đường chuẩn tương ứng: model ĐẦY ĐỦ (module tự bỏ tiền tố như với
    # /v1/images/*; combo tự thay model của từng bước), cùng bộ tham số.
    if sua_anh:
        _can_phuong_thuc(m, id_, "sửa ảnh", "anh_sua")
        truong: dict[str, str | list[str]] = {"model": model_khach, "prompt": prompt, **tham_so}
        if tool.get("n") is not None:
            truong["n"] = str(tool["n"])
        yc = YeuCauDaPhan(truong=truong, tep=_giai_ma_input_images(input_images))
    else:
        _can_phuong_thuc(m, id_, "tạo ảnh", "anh")
        body_images = {"model": model_khach, "prompt": prompt, "response_format": "b64_json", **tham_so}
        if tool.get("n") is not None:
            body_images["n"] = tool["n"]
    try:
        ma, headers, noi_dung = await (m.anh_sua(yc) if sua_anh else m.anh(body_images))
    except LoiOpenAI:
        raise
    except Exception as e:
        log.warning("Module %s lỗi khi xử lý ảnh trong responses: %s", id_, type(e).__name__)
        raise LoiOpenAI(502, f"Module gặp sự cố khi xử lý ảnh ({type(e).__name__})", code="upstream_error") from None

    if not 200 <= ma < 300:
        return _tra_ve(ma, headers, noi_dung)  # lỗi của module chuyển NGUYÊN như /v1/images/* (cả Retry-After)

    resp_obj = _xu_ly_ket_qua_module_anh(noi_dung, model_khach)
    # Thân do LÕI dựng lại nên chỉ giữ tiêu đề thuộc không gian tên của chính web2api (vd x-web2api-route của combo);
    # mọi tiêu đề khác của module (set-cookie, server, tiêu đề tả thân cũ…) mặc định bị bỏ.
    tieu_de = {k: v for k, v in dict(headers).items() if k.lower().startswith("x-web2api-")}
    if not stream:
        return JSONResponse(resp_obj, status_code=200, headers=tieu_de)
    return StreamingResponse(
        _sse_responses_anh(resp_obj),
        status_code=200,
        media_type="text/event-stream",
        headers={**tieu_de, "cache-control": "no-cache", "connection": "keep-alive", "x-accel-buffering": "no"},
    )


@v1.post("/audio/speech")
async def v1_noi(request: Request):
    kiem_tra_pham_vi(request, "noi")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    _can_phuong_thuc(m, id_, "đọc văn bản thành giọng nói", "noi")
    return _tra_ve(*await m.noi(than))


@v1.post("/audio/transcriptions")
async def v1_nghe(request: Request):
    kiem_tra_pham_vi(request, "nghe")
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(request, yc.truong)
    _can_phuong_thuc(m, id_, "nhận dạng giọng nói", "nghe")
    _can_tep(yc, "file", toi_da=1)
    return _tra_ve(*await m.nghe(yc))


_VIDEO = ("video_tao", "video_trang_thai", "video_noi_dung")


@v1.post("/videos")
async def v1_video_tao(request: Request):
    kiem_tra_pham_vi(request, "video", "video_tao")
    # SDK openai gửi videos.create bằng multipart/form-data: trường chữ thành thân JSON (giá trị vẫn là chuỗi —
    # module tự ép kiểu, như với thân JSON do khách gõ).
    yc = await doc_multipart(request) if la_multipart(request) else None
    than = dict(yc.truong) if yc else await _than_json(request)
    so = _so(request)
    id_, m = _module_theo_model(request, than)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    if yc and yc.tep:
        # video_tao nhận thân JSON; chưa có hợp đồng đưa tệp cho nó — từ chối rõ ràng thay vì lặng lẽ bỏ tệp.
        raise LoiOpenAI(415, f"POST /v1/videos chưa nhận phần tệp (trường: "
                             f"{', '.join(sorted({t.ten_truong for t in yc.tep}))}) — tệp tham chiếu chưa hỗ trợ. "
                             "Gửi multipart chỉ có trường chữ, hoặc thân JSON với ảnh đầu vào dạng base64/data URL "
                             "theo tài liệu của module.", code="unsupported_media_type")
    return _gan_tien_to_ma_video(so.tien_to_cua(id_), *await m.video_tao(than))


@v1.get("/videos/{ma}")
async def v1_video_trang_thai(ma: str, request: Request):
    kiem_tra_pham_vi(request, "video", "video_trang_thai")
    so = _so(request)
    id_, m, trong = _module_theo_ma_video(request, ma)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _gan_tien_to_ma_video(so.tien_to_cua(id_), *await m.video_trang_thai(trong))


@v1.get("/videos/{ma}/content")
async def v1_video_noi_dung(ma: str, request: Request):
    kiem_tra_pham_vi(request, "video", "video_noi_dung")
    id_, m, trong = _module_theo_ma_video(request, ma)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _tra_ve(*await m.video_noi_dung(trong))


@v1.post("/embeddings")
async def v1_embeddings(request: Request):
    kiem_tra_pham_vi(request, "nhung")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    so = _so(request)
    km = so.catalog.get(id_)
    if km and "nhung" in getattr(km, "chuan_loi", {}):
        raise LoiOpenAI(501, f"Khả năng «nhung» của module {id_!r} không đúng chuẩn", code="capability_not_supported")
    _can_phuong_thuc(m, id_, "nhúng vector", "nhung")
    inp = than.get("input")
    if inp is None or not isinstance(inp, (str, list)):
        raise LoiOpenAI(400, "Thiếu hoặc sai trường «input» (chuỗi hoặc danh sách)", code="invalid_input")
    return _tra_ve(*await m.nhung(than))


@v1.post("/rerank")
async def v1_rerank(request: Request):
    kiem_tra_pham_vi(request, "xep_hang")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    so = _so(request)
    km = so.catalog.get(id_)
    if km and "xep_hang" in getattr(km, "chuan_loi", {}):
        raise LoiOpenAI(501, f"Khả năng «xep_hang» của module {id_!r} không đúng chuẩn", code="capability_not_supported")
    _can_phuong_thuc(m, id_, "xếp hạng", "xep_hang")

    query = than.get("query")
    if not isinstance(query, str):
        raise LoiOpenAI(400, "Trường «query» phải là chuỗi", code="invalid_query")

    docs = than.get("documents")
    if not isinstance(docs, list):
        raise LoiOpenAI(400, "Trường «documents» phải là danh sách", code="invalid_documents")
    for doc in docs:
        if not (isinstance(doc, str) or (isinstance(doc, dict) and "text" in doc)):
            raise LoiOpenAI(400, "Mỗi phần tử trong «documents» phải là chuỗi hoặc đối tượng có trường «text»", code="invalid_documents")

    top_n = than.get("top_n")
    if top_n is not None:
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0 or top_n > len(docs):
            raise LoiOpenAI(400, f"Trường «top_n» phải là số nguyên dương ≤ số tài liệu ({len(docs)})", code="invalid_top_n")

    return _tra_ve(*await m.xep_hang(than))



def _nuot_moi_hau_to(route) -> bool:
    """Route có tham số kiểu `path` (vd `/models/{model:path}`) khớp MỌI hậu tố: nó khớp-một-phần một đường không chứng
    minh đường đó có thật (chỉ biết khi tra dữ liệu), nên không dùng để trả 405 — giữ 404 `unknown_endpoint` như đường
    lạ. Quyết từ kiểu bộ chuyển tham số của route, không từ bảng đường gõ tay."""
    return any(isinstance(c, PathConvertor) for c in getattr(route, "param_convertors", {}).values())


@v1_khong_co.api_route("", methods=list(HTTPMethod), include_in_schema=False)
@v1_khong_co.api_route("/{duong:path}", methods=list(HTTPMethod), include_in_schema=False)
async def duong_v1_khong_co(request: Request):
    """Đường có trong router `v1` nhưng sai phương thức → 405 + Allow; không có hẳn → 404 `unknown_endpoint`.

    Suy từ CHÍNH router `v1` (route nào khớp đường mà không khớp phương thức — Match.PARTIAL của Starlette), không từ
    bảng tay: thêm đường mới vào `v1` là tự đúng. Ngoại lệ suy từ cấu trúc: route có tham số `path` khớp mọi hậu tố
    (`_nuot_moi_hau_to`) nên không làm căn cứ cho 405 — vd `POST /v1/models/x` vẫn 404 như trước khi có
    `GET /v1/models/{model}`. Nêu lại đường khách gọi nhưng chỉ phần path (query có thể mang
    khoá, vd ?key=…), đã cắt ngắn và bỏ ký tự điều khiển — không phản chiếu chuỗi dài/độc.

    Đường có «/» cuối mà bỏ «/» thì KHỚP TRỌN một route của `v1` (cả phương thức) → 307 sang đường không «/» cuối,
    giữ query, không đổi phương thức — như redirect_slashes của Starlette (cơ chế đó không bao giờ chạy ở đây vì mount
    StaticFiles ở gốc khớp mọi đường). Chỉ bỏ «/» có thật trong đường THÔ khách gửi (raw_path, còn nguyên %xx) và
    dựng Location từ đó: «%2F» cuối không phải dấu «/» của đường nên không bị bỏ — không thể chuyển hướng vòng."""
    scope = dict(request.scope)
    tho = scope["raw_path"].rstrip(b"/")
    so_gach = len(scope["raw_path"]) - len(tho)
    if so_gach and tho:
        bo_gach = {**scope, "path": scope["path"][:-so_gach]}
        if any(r.matches(bo_gach)[0] is Match.FULL for r in v1.routes):
            dich = tho + (b"?" + scope["query_string"] if scope["query_string"] else b"")
            return RedirectResponse(dich.decode("latin-1"), status_code=307)
    cho_phep = sorted({pt for r in v1.routes if not _nuot_moi_hau_to(r) and r.matches(scope)[0] is Match.PARTIAL
                       for pt in r.methods})
    goi = f"{request.method} {_nhac_lai(request.url.path)}"
    if cho_phep:
        raise LoiOpenAI(405, f"{goi}: đường này chỉ nhận {', '.join(cho_phep)}", code="method_not_allowed",
                        headers={"Allow": ", ".join(cho_phep)})
    raise LoiOpenAI(404, f"web2api không có đường {goi} (chưa hỗ trợ). Các đường /v1 đang có: xem README mục 8; "
                         "danh sách model: GET /v1/models.", loai="invalid_request_error", code="unknown_endpoint")


# ───────────── /api/modules ─────────────

@modules.get("")
async def ds_modules(request: Request):
    """Module ĐÃ CÀI = có mã trên đĩa (catalog), kèm cấu hình công khai đã lưu (bí mật đã che) nếu có;
    rồi mục modules.json không còn mã/hỏng; rồi module từ xa. Không bí mật, không tên repo.

    `da_cau_hinh`: đã liên kết (đang phục vụ /v1). `can_cau_hinh`: manifest có trường bắt buộc.
    Mọi module đã cài đều có `tab_web` — tab riêng của module hoặc tab chung dựng từ truong_cau_hinh."""
    so = _so(request)
    ds, canh_bao = kho.doc_modules()
    if canh_bao:
        log.warning("%s", canh_bao)
    da_luu = {m["id"]: m for m in ds if isinstance(m, dict) and isinstance(m.get("id"), str)}
    ra = []
    for id_, km in so.catalog.items():
        muc = dict(da_luu.get(id_) or {"id": id_})
        muc.setdefault("ten", km.manifest["ten"])
        muc.update(loai=km.manifest["loai"], tien_to=km.tien_to, phien_ban=km.manifest.get("phien_ban"),
                   nguon=km.nguon, can_cau_hinh=km.can_cau_hinh, da_cau_hinh=id_ in so.dang_chay,
                   tab_web=km.duong_tab(),
                   cho_duyet_quyen=kho.doc_cho_duyet_quyen(id_) is not None)
        ra.append(muc)
    # Mục cấu hình mà mã đã mất (xoá tay) hoặc hỏng: vẫn hiện, không giấu — để quản trị thấy.
    ra += [dict(m) if isinstance(m, dict) else {"id": None, "hong": repr(m)} for m in ds
           if not (isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"] in so.catalog)]
    ra += [so.cong_khai_tu_xa(i) for i in so.tu_xa]
    return {"modules": ra, "canh_bao": canh_bao, "thong_bao_so": _ng(request).so_thong_bao()}


@modules.get("/catalog")
async def catalog(request: Request):
    so = _so(request)
    ds = []
    for km in so.catalog.values():
        m = km.manifest
        ds.append({k: m.get(k) for k in ("id", "ten", "mo_ta", "phien_ban", "loai", "giao_thuc", "tien_to",
                                         "truong_cau_hinh", "tai_nguyen", "yeu_cau_goi", "tab_web", "cung_cap",
                                         "loai_ten", "loai_thu_tu", "muc")}
                  | {"nguon": km.nguon, "da_cau_hinh": km.id in so.dang_chay})
    return {"modules": ds, "loi": so.loi}


@modules.get("/thong-bao", dependencies=QT)
async def thong_bao(request: Request):
    return _ng(request).thong_bao()


@modules.post("/nap-lai", dependencies=QT)
async def nap_lai(request: Request):
    return await _so(request).nap_lai()


@modules.post("/tu-xa", dependencies=QT)
async def them_tu_xa(request: Request):
    try:
        return await _so(request).them_tu_xa(await _than_json(request))
    except LoiSo as e:
        raise _loi_so(e) from None


@modules.post("/{id_}/cai", dependencies=QT)
async def cai(id_: str, request: Request):
    than = await _than_json(request)
    cau_hinh = than.get("cau_hinh")
    if not isinstance(cau_hinh, dict):
        raise LoiOpenAI(400, 'Thân cần dạng {"cau_hinh": {...}}')
    try:
        kq = await _so(request).cai(id_, cau_hinh)
    except LoiSo as e:
        raise _loi_so(e) from None
    return JSONResponse(kq, status_code=200 if kq["ok"] else 400)


@modules.delete("/{id_}", dependencies=QT)
async def go(id_: str, request: Request):
    try:
        kq = await _so(request).go(id_)
    except LoiSo as e:
        raise _loi_so(e) from None
    _ng(request).da_go(id_)  # đang bật tự cài thì không được cài lại ngay bản vừa gỡ
    return kq


@modules.get("/{id_}/suc-khoe", dependencies=QT)
async def suc_khoe(id_: str, request: Request):
    so = _so(request)
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} đã cài nhưng chưa có khoá/cấu hình — chưa có gì để kiểm"
                        if id_ in so.catalog else f"Không có module {id_!r}")
    return await m.suc_khoe()


@modules.get("/{id_}/cau-hinh", dependencies=QT)
async def cau_hinh(id_: str, request: Request):
    """Hợp đồng của form cấu hình trên web: trường của manifest + giá trị ĐÃ LƯU, bí mật chỉ ở dạng che
    (đọc phần công khai của modules.json — không mở tệp bí mật). Thứ tự dạng che của `danh_sach_bi_mat`
    chính là chỉ số dùng trong dạng sửa {"giu", "them"} của POST …/cai."""
    so = _so(request)
    km = so.catalog.get(id_)
    if km is None:
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    muc = so.muc_da_luu(id_) or {}
    return {"id": id_, "ten": km.manifest["ten"], "truong_cau_hinh": km.truong_cau_hinh,
            "gia_tri": muc.get("cau_hinh_cong_khai") or {}, "da_cau_hinh": id_ in so.dang_chay}


async def _model_cua_module(id_: str, request: Request) -> list[dict]:
    """Danh sách model gốc của module đang chạy (404 nếu không có / chưa liên kết, 502 nếu module lỗi)."""
    so = _so(request)
    if id_ not in so.catalog and id_ not in so.tu_xa:
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} chưa liên kết", code="module_not_configured")
    if not callable(getattr(m, "models", None)):  # không khai khả năng theo model: không có model nào (như /v1/models)
        return []
    try:
        return await m.models()
    except Exception as e:
        log.warning("Module %s lỗi khi lấy danh sách model: %s", id_, e)
        raise LoiOpenAI(502, f"Không đọc được danh sách model từ module: {e}", code="upstream_error") from None


def _tom_tat_model(raw_models: list[dict], tat: set[str], mac_dinh: dict[str, str]) -> dict:
    """Danh mục model kèm bật/tắt/mặc định theo cài đặt đã cho; dùng chung cho GET và PUT."""
    models = []
    for item in raw_models:
        mid = item.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        nhom = _nhom_cua_model(item)
        bat = mid not in tat
        muc = {"id": mid, "nhom": nhom, "bat": bat, "mac_dinh": bool(bat and mac_dinh.get(nhom) == mid)}
        if "kha_nang" in item:
            muc["kha_nang"] = item["kha_nang"]
        models.append(muc)
    return {"models": models, "tat": [x["id"] for x in models if not x["bat"]],
            "mac_dinh": {nh: mid for nh, mid in mac_dinh.items() if any(x["id"] == mid and x["bat"] for x in models)},
            "tong_so": len(models), "dang_bat": sum(1 for x in models if x["bat"])}


def _doc_cai_dat_model(id_: str) -> dict:
    try:
        return kho.doc_cai_dat_model_module(id_)
    except kho.KhoHong:
        return {"tat": [], "mac_dinh": {}}


@modules.get("/{id_}/model", dependencies=QT)
async def lay_model_module(id_: str, request: Request):
    """Danh mục model của một module (id, nhóm, bật/tắt, mặc định, số đếm)."""
    raw = await _model_cua_module(id_, request)
    cai_dat = _doc_cai_dat_model(id_)
    return {"id": id_, **_tom_tat_model(raw, set(cai_dat["tat"]), cai_dat["mac_dinh"])}


@modules.put("/{id_}/model", dependencies=QT)
async def cap_nhat_model_module(id_: str, request: Request):
    """Cập nhật bật/tắt và mặc định model của một module.
    Thân: {"tat": [id…], "mac_dinh": {<nhóm>: id}}; trường vắng = giữ nguyên. Id lạ bỏ qua và nêu trong `bo_qua`.
    Mặc định phải là model đang bật: tắt model mặc định → bỏ mặc định; đặt mặc định cho model tắt → 400.
    """
    than = await _than_json(request)
    tat_in, mac_dinh_in = than.get("tat"), than.get("mac_dinh")
    if tat_in is not None and not isinstance(tat_in, list):
        raise LoiOpenAI(400, "«tat» phải là danh sách id model", code="invalid_request")
    if mac_dinh_in is not None and not isinstance(mac_dinh_in, dict):
        raise LoiOpenAI(400, "«mac_dinh» phải là đối tượng {nhóm: id}", code="invalid_request")
    raw = await _model_cua_module(id_, request)
    hop_le = {item["id"] for item in raw if isinstance(item.get("id"), str)}
    cu = _doc_cai_dat_model(id_)
    bo_qua: set[str] = set()

    if tat_in is None:
        tat_moi = set(cu["tat"])
    else:
        ds = [x for x in tat_in if isinstance(x, str)]
        bo_qua.update(x for x in ds if x not in hop_le)
        tat_moi = {x for x in ds if x in hop_le}

    if mac_dinh_in is None:
        mac_dinh_moi = dict(cu["mac_dinh"])
    else:
        mac_dinh_moi = {}
        for nhom, mid in mac_dinh_in.items():
            if not isinstance(nhom, str) or not isinstance(mid, str):
                continue
            if mid not in hop_le:
                bo_qua.add(mid)
            elif mid in tat_moi:
                raise LoiOpenAI(400, f"Không thể đặt model đã tắt {mid!r} làm mặc định cho nhóm {nhom!r}",
                                code="model_disabled")
            else:
                mac_dinh_moi[nhom] = mid
    mac_dinh_moi = {nh: mid for nh, mid in mac_dinh_moi.items() if mid in hop_le and mid not in tat_moi}

    try:
        kho.luu_cai_dat_model_module(id_, sorted(tat_moi & hop_le), mac_dinh_moi)
    except kho.KhoHong as e:
        raise LoiOpenAI(500, f"Không lưu được cài đặt model: {e}") from e
    return {"ok": True, "bo_qua": sorted(bo_qua), **_tom_tat_model(raw, tat_moi, mac_dinh_moi)}


# ───────────── /api/tinh-nang ─────────────

@tinh_nang.get("/api/tinh-nang")
async def xem_tinh_nang(request: Request):
    """MỞ (như GET /api/modules): khung giao diện cần nó TRƯỚC khi người dùng dán khoá quản trị — trang đầu
    là trang Module khi chưa cài gì. Không bí mật, không cấu hình, không tên repo. `co_combo`: đã có ít nhất một
    combo hợp lệ."""
    return {**_so(request).tinh_nang(), "co_combo": combo_mod.co_combo()}


# ───────────── /api/nguon-git ─────────────

@nguon_git.get("")
async def xem_nguon_git(request: Request):
    return _ng(request).cong_khai()


@nguon_git.put("")
async def dat_nguon_git(request: Request):
    try:
        return _ng(request).luu_cau_hinh(await _than_json(request))
    except ValueError as e:
        raise LoiOpenAI(400, str(e)) from None


@nguon_git.post("/cai/{id_}")
async def cai_tu_git(id_: str, request: Request):
    """Cài module từ git. Thân JSON tuỳ chọn {"chap_nhan_quyen_them": [...], "sha_commit": "..."}
    để DUYỆT bản đang chờ (phải khớp chính xác diff đã xem); không có thân → cài thường."""
    than = {}
    if (request.headers.get("content-type") or "").split(";")[0].strip().lower() == "application/json":
        try:
            than = await request.json()
        except ValueError:
            than = {}
    if not isinstance(than, dict):
        than = {}
    try:
        if than.get("chap_nhan_quyen_them") is not None or than.get("sha_commit") is not None:
            return await _ng(request).cai(id_, chap_nhan_quyen_them=than.get("chap_nhan_quyen_them"),
                                         sha_commit=than.get("sha_commit"))
        return await _ng(request).cai(id_)
    except LoiCanDuyetQuyen as e:
        return JSONResponse({"ok": False, "ma": "can_duyet_quyen", "id": e.id_,
                             "sha_commit": e.sha_commit, "tree_sha": e.tree_sha, "diff": e.diff},
                            status_code=409)
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


# ───────────── /m/<id>/ — giao diện đi theo module ─────────────

#: Trang module chạy CÙNG origin với giao diện quản trị (khoá quản trị nằm ở localStorage): CSP chặt — chỉ script/kiểu
#: từ chính máy chủ (không inline, không eval), không nhúng khung, không plugin — và header bảo vệ, kể cả khi 404.
HEADER_TRANG_MODULE = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@tep_module.get("/m/{id_}/{duong:path}")
async def tep_cua_module(id_: str, duong: str, request: Request):
    so = _so(request)
    km = so.catalog.get(id_)
    # Mở khi module ĐÃ CÀI (mã trên đĩa) và khai tab riêng — kể cả lúc chưa liên kết: tệp tĩnh của module,
    # không chứa bí mật, và có khi chính là nơi người dùng thêm khoá.
    if km is None or not km.manifest.get("tab_web"):
        raise HTTPException(404, headers=HEADER_TRANG_MODULE)
    goc = (km.thu_muc / "web").resolve()
    p = (goc / duong).resolve()
    # resolve() rồi so tổ tiên: chặn «..» lẫn symlink trỏ ra ngoài thư mục web/ của module.
    if not p.is_relative_to(goc) or not p.is_file():
        raise HTTPException(404, headers=HEADER_TRANG_MODULE)
    return FileResponse(p, headers=HEADER_TRANG_MODULE)


# ───────────── /api/dia-chi, /api/ket-noi ─────────────

@dia_chi.get("/dia-chi")
async def xem_dia_chi(request: Request):
    return dia_chi_mod.lay_thong_tin_dia_chi(request)


@dia_chi.put("/dia-chi")
async def dat_dia_chi(request: Request):
    than = await _than_json(request)
    if "url" not in than:
        raise LoiOpenAI(400, "Thân yêu cầu phải có trường «url» (chuỗi hoặc null)", loai="invalid_request_error")
    url = than["url"]
    if url is None:
        kho.xoa_dia_chi_da_dat()
    elif isinstance(url, str):
        url_chuan = dia_chi_mod.kiem_tra_va_chuan_hoa_url(url)
        kho.luu_dia_chi_da_dat(url_chuan)
    else:
        raise LoiOpenAI(400, "Trường «url» phải là chuỗi hoặc null", loai="invalid_request_error")
    return dia_chi_mod.lay_thong_tin_dia_chi(request)


@dia_chi.get("/ket-noi")
async def huong_dan_ket_noi(request: Request):
    return dia_chi_mod.soan_huong_dan_ket_noi(request)

# ───────────── /api/nhat-ky — nhật ký hệ thống theo kênh ─────────────

@nhat_ky.get("")
def xem_nhat_ky(kenh: str | None = None, muc: str | None = None, q: str | None = None, truoc: str | None = None,
                gioi_han: int = 50):
    # `def` (không async): FastAPI chạy trong luồng phụ — quét tới 20 MB không chặn vòng lặp sự kiện.
    from app import nhat_ky as kho_nhat_ky
    try:
        return kho_nhat_ky.doc(kenh=kenh, muc=muc, q=q, truoc=truoc, gioi_han=gioi_han)
    except ValueError:
        raise LoiOpenAI(400, "Tham số «truoc» phải là thời điểm ISO 8601 — lấy nguyên `luc` của dòng cuối trang "
                             "trước") from None


@nhat_ky.get("/kenh")
def cac_kenh_nhat_ky():
    from app import nhat_ky as kho_nhat_ky
    return kho_nhat_ky.danh_sach_kenh()


@nhat_ky.get("/chuoi")
async def kiem_tra_chuoi_nhat_ky():
    # async + to_thread: quét tới 100 MB không chặn vòng lặp sự kiện. Không trả nội dung bản ghi.
    from app import nhat_ky as kho_nhat_ky
    return await asyncio.to_thread(kho_nhat_ky.kiem_tra_chuoi)


#: Trần của `truoc_ngay` (100 năm): số lớn hơn không mang nghĩa gì và làm phép trừ ngày tràn.
TRAN_TRUOC_NGAY = 36500


@nhat_ky.delete("")
def xoa_nhat_ky(request: Request, truoc_ngay: float | None = None):
    if truoc_ngay is None:
        raise LoiOpenAI(400, "Thiếu tham số «truoc_ngay» (số ngày)")
    from app import nhat_ky as kho_nhat_ky
    nguong_min = kho_nhat_ky.nguong_xoa_toi_thieu_ngay()
    if not (math.isfinite(truoc_ngay) and nguong_min <= truoc_ngay <= TRAN_TRUOC_NGAY):
        raise LoiOpenAI(
            400,
            f"Tham số «truoc_ngay» phải trong khoảng {nguong_min}…{TRAN_TRUOC_NGAY}"
            + (f" (ngưỡng tối thiểu {nguong_min} ngày để chống xoá lén nhật ký)" if truoc_ngay < nguong_min else ""),
        )
    da_xoa = kho_nhat_ky.xoa_cu(truoc_ngay)
    # Ghi nhận ngay sau khi xoá để dòng này còn lại trong nhật ký
    from app.gioi_han import lay_ip_an_toan
    ip_khach = lay_ip_an_toan(request)
    danh_tinh = getattr(request.state, "danh_tinh", None)
    nguoi_goi = (
        danh_tinh.get("id", "quan_tri")
        if isinstance(danh_tinh, dict)
        else getattr(request.state, "nguoi_goi", "quan_tri")
    )
    kho_nhat_ky.ghi(
        "quan_tri",
        "canh_bao",
        f"Xoá nhật ký kiểm toán cũ hơn {truoc_ngay} ngày: đã xoá {da_xoa} dòng",
        da_xoa=da_xoa,
        nguoi_goi=nguoi_goi,
        ip=ip_khach,
        truoc_ngay=truoc_ngay,
    )
    return {"ok": True, "da_xoa": da_xoa}


# ───────────── /api/khoa-khach — quản lý khoá khách (chỉ quản trị) ─────────────

@khoa_khach.get("")
def ds_khoa_khach():
    return {"ok": True, "khoa": khoa_khach_mod.danh_sach_khoa()}


@khoa_khach.post("", dependencies=KHONG_LUU)
async def tao_khoa_khach(request: Request):
    than = await _than_json(request)
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(
        nhan=than.get("nhan", ""),
        pham_vi=than.get("pham_vi"),
        het_han=than.get("het_han"),
    )
    return {"ok": True, "khoa": {**ban_ghi, "khoa": khoa_tho}}


@khoa_khach.post("/{id_}/xoay", dependencies=KHONG_LUU)
async def xoay_khoa_khach(id_: str, request: Request):
    raw = await doc_het(request, THAN_JSON)
    if raw.strip():
        try:
            than = json.loads(raw)
        except (ValueError, RecursionError):
            raise LoiOpenAI(400, "Thân yêu cầu không phải JSON hợp lệ") from None
        if not isinstance(than, dict):
            raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")
    else:
        than = {}
    cu_giay = than.get("cu_giay")
    ban_ghi, khoa_tho = khoa_khach_mod.xoay_khoa(
        id_khoa=id_,
        cu_giay=cu_giay,
    )
    return {"ok": True, "khoa": {**ban_ghi, "khoa": khoa_tho}}


@khoa_khach.patch("/{id_}")
async def sua_khoa_khach(id_: str, request: Request):
    than = await _than_json(request)
    kwargs: dict[str, Any] = {
        "id_khoa": id_,
        "bat": _lay_bool(than, "bat", None),
        "nhan": than.get("nhan"),
        "pham_vi": than.get("pham_vi"),
    }
    if "het_han" in than:
        kwargs["het_han"] = than.get("het_han")
    kq = khoa_khach_mod.sua_khoa(**kwargs)
    return {"ok": True, "khoa": kq}


@khoa_khach.patch("")
async def sua_khoa_khach_khong_path(request: Request):
    than = await _than_json(request)
    id_ = than.get("id")
    if not id_:
        raise LoiOpenAI(400, "Thiếu trường «id» trong thân yêu cầu.")
    kwargs: dict[str, Any] = {
        "id_khoa": id_,
        "bat": _lay_bool(than, "bat", None),
        "nhan": than.get("nhan"),
        "pham_vi": than.get("pham_vi"),
    }
    if "het_han" in than:
        kwargs["het_han"] = than.get("het_han")
    kq = khoa_khach_mod.sua_khoa(**kwargs)
    return {"ok": True, "khoa": kq}


@khoa_khach.delete("/{id_}")
def xoa_khoa_khach(id_: str):
    da_xoa = khoa_khach_mod.xoa_khoa(id_khoa=id_)
    return {"ok": True, "da_xoa": da_xoa}


# ───────────── /api/phien — quản lý phiên tài khoản (chỉ quản trị) ─────────────

@phien.get("")
def xem_phien():
    ds = phien_mod.liet_ke()
    can_dang_nhap = sum(1 for x in ds if x.get("trang_thai") == "can_dang_nhap")
    return {"phien": ds, "tong": len(ds), "can_dang_nhap_tong": can_dang_nhap}


@phien.delete("/{module}/{tai_khoan}")
def xoa_phien(module: str, tai_khoan: str):
    if not phien_mod.ID_HOP_LE.match(module) or not phien_mod.ID_HOP_LE.match(tai_khoan):
        raise LoiOpenAI(400, "id module hoặc tài khoản không hợp lệ")
    da_xoa = phien_mod.xoa(module, tai_khoan)
    if not da_xoa:
        raise LoiOpenAI(404, "Không tìm thấy phiên")
    return {"ok": True, "module": module, "tai_khoan": tai_khoan}


@phien.post("/{module}/{tai_khoan}/kiem")
async def kiem_phien(module: str, tai_khoan: str, request: Request):
    if not phien_mod.ID_HOP_LE.match(module) or not phien_mod.ID_HOP_LE.match(tai_khoan):
        raise LoiOpenAI(400, "id module hoặc tài khoản không hợp lệ")
    try:
        kq = await phien_mod.kiem_mot_phien(module, tai_khoan, so=_so(request))
    except KeyError:
        raise LoiOpenAI(404, "Không tìm thấy phiên") from None
    return {"ok": True, **kq}


# ───────────── Tuyến THIẾT BỊ / ỨNG DỤNG (/api/thiet-bi) ─────────────

@thiet_bi.get("")
async def lay_thiet_bi(
    request: Request,
    q: str | None = None,
    loai: str | None = None,
    khu: str | None = None,
    module: str | None = None,
    rui_ro: str | None = None,
):
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi")
    so = _so(request)
    ds, loi = await thiet_bi_mod.lay_danh_sach_thuc_the(
        so, loc_q=q, loc_loai=loai, loc_khu=khu, loc_module=module, loc_rui_ro=rui_ro
    )
    loai_set = sorted(set(e.get("loai") for e in ds if e.get("loai")))
    khu_set = sorted(set(e.get("khu") for e in ds if e.get("khu")))
    module_set = sorted(set(e.get("module") for e in ds if e.get("module")))
    return {
        "thuc_the": ds,
        "loi_module": loi,
        "loai": loai_set,
        "khu": khu_set,
        "module": module_set,
        "rui_ro": sorted(thiet_bi_mod.RUI_RO_HOP_LE),
    }


@thiet_bi.get("/su-kien")
async def sse_su_kien_thiet_bi(request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:su_kien")
    q = await thiet_bi_mod.BO_PHAT.dang_ky()

    async def phat_dong():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    su_kien = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(su_kien, ensure_ascii=False)}\n\n".encode("utf-8")
                except asyncio.TimeoutError:
                    yield b": giu_ket_noi\n\n"
        finally:
            await thiet_bi_mod.BO_PHAT.huy(q)

    return StreamingResponse(
        phat_dong(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@thiet_bi.post("/su-kien")
async def bao_su_kien_thiet_bi(request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:su_kien")
    than = await _than_json(request)
    e_id = than.get("id") or than.get("thuc_the")
    if not isinstance(e_id, str) or not e_id.strip():
        raise LoiOpenAI(400, "Thiếu hoặc sai trường «id»")
    sk = {
        "id": e_id.strip(),
        "thuc_the": e_id.strip(),
        "loai": str(than.get("loai") or "trang_thai"),
        "gia_tri": than.get("gia_tri"),
        "luc": kho.bay_gio_iso(),
    }
    await thiet_bi_mod.BO_PHAT.phat(sk)
    return {"ok": True}


@thiet_bi.get("/{id_thuc_the}")
async def lay_chi_tiet_thiet_bi(id_thuc_the: str, request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi")
    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")
    return {"thuc_the": item, "trang_thai": item.get("trang_thai")}


@thiet_bi.put("/{id_thuc_the}/rui-ro", dependencies=QT)
async def dat_rui_ro_thiet_bi(id_thuc_the: str, request: Request):
    than = await _than_json(request)
    rui_ro = than.get("rui_ro")
    if rui_ro not in thiet_bi_mod.RUI_RO_HOP_LE:
        raise LoiOpenAI(400, f"Mức rủi ro không hợp lệ (chỉ nhận: {sorted(thiet_bi_mod.RUI_RO_HOP_LE)})", code="invalid_request")
    thiet_bi_mod.luu_ghi_de_rui_ro(id_thuc_the, rui_ro)
    nhat_ky_mod.ghi(
        "thiet_bi",
        "thong_tin",
        f"Đặt rủi ro «{rui_ro}» cho thực thể «{id_thuc_the}»",
        thuc_the=id_thuc_the,
        rui_ro=rui_ro,
        nguoi_goi=getattr(request.state, "nguoi_goi", "quan_tri"),
    )
    return {"ok": True, "id": id_thuc_the, "rui_ro": rui_ro}


@thiet_bi.post("/{id_thuc_the}/hanh-dong")
async def thuc_hien_hanh_dong_thiet_bi(id_thuc_the: str, request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:ghi")
    than = await _than_json(request)
    hanh_dong = than.get("hanh_dong")
    if not isinstance(hanh_dong, str) or not hanh_dong.strip():
        raise LoiOpenAI(400, "Thiếu trường «hanh_dong»")
    hanh_dong = hanh_dong.strip()
    tham_so = than.get("tham_so") or {}
    if not isinstance(tham_so, dict):
        raise LoiOpenAI(400, "«tham_so» phải là đối tượng")
    xac_nhan = _lay_bool(than, "xac_nhan", False)
    ly_do = str(than.get("ly_do") or "")

    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")

    if hanh_dong not in item.get("tinh_nang", []):
        raise LoiOpenAI(
            400,
            f"Hành động «{hanh_dong}» không nằm trong tính năng hỗ trợ của thực thể {id_thuc_the!r} (hỗ trợ: {item.get('tinh_nang', [])})",
            code="invalid_action",
        )

    nguoi_goi = getattr(request.state, "nguoi_goi", "khach")
    danh_tinh = getattr(request.state, "danh_tinh", {})
    la_quan_tri = isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "quan_tri"

    # Xử lý mức rủi ro nguy_hiem
    if item.get("rui_ro") == "nguy_hiem":
        if la_quan_tri:
            if not xac_nhan:
                nhat_ky_mod.ghi(
                    "thiet_bi",
                    "canh_bao",
                    f"Từ chối 409: hành động nguy hiểm «{hanh_dong}» trên «{id_thuc_the}» cần quản trị xác nhận",
                    thuc_the=id_thuc_the,
                    hanh_dong=hanh_dong,
                    nguoi_goi=nguoi_goi,
                )
                raise LoiOpenAI(
                    409,
                    f"Hành động «{hanh_dong}» trên thực thể «{id_thuc_the}» có mức rủi ro nguy hiểm, cần gửi kèm «xac_nhan: true».",
                    code="can_xac_nhan",
                )
        else:
            # Khoá khách: KHÔNG thực hiện, tạo mục HÀNG CHỜ PHÊ DUYỆT và trả 202
            pd = thiet_bi_mod.them_hang_cho_phe_duyet(
                ai_de_xuat=nguoi_goi,
                thuc_the=id_thuc_the,
                hanh_dong=hanh_dong,
                tham_so=tham_so,
                ly_do=ly_do,
            )
            nhat_ky_mod.ghi(
                "thiet_bi",
                "thong_tin",
                f"Tạo yêu cầu phê duyệt {pd['id']}: «{nguoi_goi}» yêu cầu «{hanh_dong}» trên «{id_thuc_the}» (nguy hiểm)",
                phe_duyet_id=pd["id"],
                thuc_the=id_thuc_the,
                hanh_dong=hanh_dong,
                nguoi_goi=nguoi_goi,
                ly_do=ly_do,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "phe_duyet_id": pd["id"],
                    "trang_thai": "cho_phe_duyet",
                    "cho_phe_duyet": True,
                },
                status_code=202,
            )

    # Thực hiện hành động
    tien_to, _, _ = id_thuc_the.partition(".")
    mod_id = so.tim_theo_tien_to(tien_to)
    m = so.dang_chay.get(mod_id)
    if not m or not callable(getattr(m, "thuc_hien", None)):
        raise LoiOpenAI(502, f"Module cho thực thể {id_thuc_the!r} không sẵn sàng hoặc không hỗ trợ thuc_hien.", code="upstream_error")

    try:
        kq = await m.thuc_hien(id_thuc_the, hanh_dong, tham_so)
    except Exception as e:
        log.exception("Module %s lỗi khi thực hiện hành động %s trên %s", mod_id, hanh_dong, id_thuc_the)
        nhat_ky_mod.ghi(
            "thiet_bi",
            "loi",
            f"Module lỗi khi thực hiện «{hanh_dong}» trên «{id_thuc_the}»: {e}",
            thuc_the=id_thuc_the,
            hanh_dong=hanh_dong,
            nguoi_goi=nguoi_goi,
            loi=str(e),
        )
        raise LoiOpenAI(502, f"Module lỗi khi thực hiện hành động: {type(e).__name__}", code="upstream_error") from e

    nhat_ky_mod.ghi(
        "thiet_bi",
        "thong_tin",
        f"Thực hiện thành công hành động «{hanh_dong}» trên «{id_thuc_the}»",
        thuc_the=id_thuc_the,
        hanh_dong=hanh_dong,
        nguoi_goi=nguoi_goi,
        tham_so=thiet_bi_mod.lam_sach_tham_so(tham_so),
    )
    return kq


# ───────────── Hàng chờ phê duyệt (/api/phe-duyet) ─────────────

@phe_duyet.get("")
def danh_sach_phe_duyet():
    return {"hang_cho": thiet_bi_mod.doc_hang_cho_phe_duyet()}


@phe_duyet.post("/{pd_id}/duyet")
async def xu_ly_duyet(pd_id: str, request: Request):
    so = _so(request)
    nguoi_duyet = getattr(request.state, "nguoi_goi", "quan_tri")
    return await thiet_bi_mod.duyet_phe_duyet(pd_id, so, nguoi_duyet=nguoi_duyet)


@phe_duyet.post("/{pd_id}/tu-choi")
def xu_ly_tu_choi(pd_id: str, request: Request):
    nguoi_tu_choi = getattr(request.state, "nguoi_goi", "quan_tri")
    return thiet_bi_mod.tu_choi_phe_duyet(pd_id, nguoi_tu_choi=nguoi_tu_choi)


# ───────────── Công cụ cho AI (/api/cong-cu) ─────────────

@cong_cu.get("")
async def lay_danh_sach_cong_cu(
    request: Request,
    loai: str | None = None,
    q: str | None = None,
):
    """GET /api/cong-cu: sinh danh sách công cụ chuẩn OpenAI function-calling từ thực thể thiết bị.

    - Không khoá -> 401 (qua V1_AUTH).
    - Khoá khách thiếu phạm vi thiet_bi:doc hoặc thiet_bi:ghi -> 403 (insufficient_scope).
    - Khoá khách có thiet_bi:doc -> chỉ thấy tools có rủi ro "doc".
    - Khoá khách có thiet_bi:ghi hoặc quản trị -> thấy đầy đủ tools.
    """
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi")
    so = _so(request)

    danh_tinh = getattr(request.state, "danh_tinh", None)
    duoc_ghi = False
    if isinstance(danh_tinh, dict):
        if danh_tinh.get("loai") == "quan_tri":
            duoc_ghi = True
        else:
            pv = danh_tinh.get("pham_vi", [])
            if "*" in pv or "thiet_bi:ghi" in pv:
                duoc_ghi = True
    elif getattr(request.state, "nguoi_goi", "") == "quan_tri":
        duoc_ghi = True

    ds, _ = await thiet_bi_mod.lay_danh_sach_thuc_the(so, loc_q=q, loc_loai=loai)
    tools = cong_cu_mod.sinh_cong_cu(ds, duoc_ghi=duoc_ghi)
    return {
        "object": "list",
        "data": list(tools),
        "tong": tools.tong,
        "bi_cat": tools.bi_cat,
        "nguon_ngoai": True,
    }


# ───────────── Tìm kiếm và nguồn tri thức (/api/tim-kiem) ─────────────

@tim_kiem.get("/nguon", dependencies=QT)
async def lay_nguon_tim_kiem(request: Request):
    so = _so(request)
    return {"nguon": await tri_thuc_mod.lay_danh_sach_nguon(so)}


@tim_kiem.post("", dependencies=V1_AUTH)
async def goi_tim_kiem(request: Request):
    kiem_tra_pham_vi(request, "tim_kiem")
    than = await _than_json(request)
    so = _so(request)
    danh_tinh = getattr(request.state, "danh_tinh", None)
    return await tri_thuc_mod.thuc_hien_tim_kiem(than, so, danh_tinh=danh_tinh)


@tim_kiem.post("/muc-luc", dependencies=V1_AUTH)
async def lay_muc_luc(request: Request):
    kiem_tra_pham_vi(request, "tim_kiem")
    than = await _than_json(request)
    so = _so(request)
    return await tri_thuc_mod.lay_muc_luc(than, so)

# ───────────── Quản trị Combo (/api/combo) — chỉ khoá quản trị (router `combo`, dependencies=QT) ─────────────

def _phan_giai(request: Request):
    so = _so(request)
    return lambda model: _module_cho_model(so, model)


@combo.get("")
async def danh_sach_combo():
    """Mọi combo hợp lệ đã lưu; `so_muc_hong` đếm mục hỏng trong combo.json bị bỏ qua."""
    tat_ca, hong = combo_mod.doc_tat_ca()
    return {"combos": [tat_ca[t] for t in sorted(tat_ca)], "so_muc_hong": hong}


@combo.get("/trang-thai")
async def xem_trang_thai_combo(request: Request):
    """Trạng thái từng bước: phân giải được không, cầu chì theo nhóm việc, hạ bậc, số phiên dính."""
    return {"trang_thai": combo_mod.trang_thai_tat_ca(_so(request), _phan_giai(request))}


@combo.get("/{ten:path}")
async def lay_mot_combo(ten: str):
    muc = combo_mod.lay(combo_mod.kiem_ten(ten))
    if muc is None:
        raise LoiOpenAI(404, "Không tìm thấy combo", code="not_found")
    return muc


@combo.put("/{ten:path}")
async def cap_nhat_combo(ten: str, request: Request):
    """Tạo/ghi đè một combo. Sai kiểu → 400, không lưu; bước chưa dùng được vẫn lưu và nêu trong `bo_qua`."""
    than = await _than_json(request)
    cb, bo_qua = await combo_mod.kiem_tra_combo(ten, than, _so(request), _phan_giai(request))
    try:
        luu = kho.luu_mot_combo(cb["ten"], cb)
    except OSError as e:
        raise LoiOpenAI(500, f"Không lưu được combo: {type(e).__name__}") from e
    nhat_ky_mod.ghi("combo", "thong_tin", f"Lưu combo «{cb['ten']}» với {len(cb['buoc'])} bước",
                    ten=cb["ten"], so_buoc=len(cb["buoc"]), so_bo_qua=len(bo_qua))
    return {"ok": True, "combo": luu, "bo_qua": bo_qua}


@combo.delete("/{ten:path}")
async def xoa_mot_combo(ten: str):
    ten = combo_mod.kiem_ten(ten)
    if not kho.xoa_mot_combo(ten):
        raise LoiOpenAI(404, "Không tìm thấy combo", code="not_found")
    nhat_ky_mod.ghi("combo", "thong_tin", f"Xoá combo «{ten}»", ten=ten)
    return {"ok": True}


# ───────────── THIẾT LẬP LẦN ĐẦU ─────────────

_KHOA_THIET_LAP = asyncio.Lock()


def _chuoi_tuy_chon(than: dict, ten: str) -> str | None:
    """Kiểm KIỂU ở biên: trường vắng/null → None; có thì phải là chuỗi, sai → 400 invalid_value (không str() ép kiểu)."""
    gia_tri = than.get(ten)
    if gia_tri is not None and not isinstance(gia_tri, str):
        raise LoiOpenAI(400, f"Trường «{ten}» phải là chuỗi.", code="invalid_value")
    return gia_tri


def _chuoi_bat_buoc(than: dict, ten: str) -> str:
    """Trường bắt buộc: phải là chuỗi không rỗng (sau khi bỏ khoảng trắng hai đầu), sai/thiếu → 400 invalid_value."""
    gia_tri = _chuoi_tuy_chon(than, ten)
    if gia_tri is None or not gia_tri.strip():
        raise LoiOpenAI(400, f"Thiếu trường «{ten}».", code="invalid_value")
    return gia_tri


@thiet_lap.get("")
def kiem_tra_thiet_lap():
    # Ngoại lệ mở có chủ đích: Kiểm tra hệ thống có cần thiết lập tài khoản admin lần đầu hay không
    # (chỉ trả cờ boolean can_thiet_lap, không chứa thông tin nhạy cảm hay bí mật).
    return {"can_thiet_lap": not nguoi_dung_mod.co_admin()}


@thiet_lap.post("", dependencies=KHONG_LUU)
async def thuc_hien_thiet_lap(request: Request, response: Response):
    # Ngoại lệ mở có chủ đích: Thiết lập tài khoản admin lần đầu (chỉ chạy khi chưa có admin;
    # đã có admin trả 409 ngay và không xử lý thân; bảo vệ bằng mã thiết lập/khoá quản trị và giới hạn tần suất).
    # Đường tạo admin: cùng cổng IP với đường quản trị (WEB2API_QT_IP_CHO_PHEP; loopback trực tiếp vẫn được).
    ip_cho_phep_mod.kiem_tra_ip_quan_tri(request)
    gioi_han_mod.kiem_tra_truoc_xac_thuc(request)

    # Kho người dùng hỏng -> 503 kèm hướng dẫn khôi phục (đóng khi lỗi: KHÔNG mở lại thiết lập, không ghi đè)
    nguoi_dung_mod.kiem_tra_kho()
    phien_web_mod.kiem_tra_kho()  # kho phiên hỏng → 503 TRƯỚC khi tạo admin và xoá mã thiết lập
    # Đã có admin -> 409 ngay, không xử lý thân
    if nguoi_dung_mod.co_admin():
        raise LoiOpenAI(
            409,
            "Hệ thống đã có tài khoản quản trị viên. Không thể thiết lập lại.",
            loai="invalid_request_error",
            code="already_setup",
        )

    _kiem_content_type_json(request)
    than = await _than_json(request)
    ma_thiet_lap = than.get("ma_thiet_lap")
    ma_thiet_lap = ma_thiet_lap.strip() if isinstance(ma_thiet_lap, str) else ""
    # Thiết lập bắt buộc cả tên và mật khẩu: admin sinh ngẫu nhiên ở đây sẽ mất mật khẩu (không hiện ra) → khoá ngoài.
    ten_dang_nhap = _chuoi_bat_buoc(than, "ten_dang_nhap").strip()
    mat_khau = _chuoi_bat_buoc(than, "mat_khau")

    ma_luu = nguoi_dung_mod.doc_ma_thiet_lap() or ""
    khoa_qt = xac_thuc.khoa_quan_tri()

    hop_le = False
    if ma_thiet_lap:
        if ma_luu and hmac.compare_digest(ma_thiet_lap.encode("utf-8"), ma_luu.encode("utf-8")):
            hop_le = True
        elif hmac.compare_digest(ma_thiet_lap.encode("utf-8"), khoa_qt.encode("utf-8")):
            hop_le = True

    if not hop_le:
        gioi_han_mod.ghi_loi_xac_thuc(request)
        raise LoiOpenAI(
            401,
            "Mã thiết lập không chính xác.",
            loai="invalid_request_error",
            code="invalid_setup_code",
        )

    async with _KHOA_THIET_LAP:
        # «Chỉ khi chưa có admin» kiểm lại TRONG lần khoá ghi của kho (kiểm-rồi-ghi cùng vùng găng)
        ban_ghi, ten_chuan, _ = await nguoi_dung_mod.tao_nguoi_dung(
            vai="admin",
            ten_dang_nhap=ten_dang_nhap,
            mat_khau=mat_khau,
            pham_vi_toi_da=["*"],
            chi_khi_chua_co_admin=True,
        )
        nguoi_dung_mod.xoa_ma_thiet_lap()
        phien, token_tho = phien_web_mod.tao_phien(ban_ghi["id"])
        phien_web_mod.dat_cookie_phien(response, request, token_tho)
        nhat_ky_mod.ghi(
            "quan_tri",
            "thong_tin",
            f"Thiết lập thành công tài khoản quản trị «{_nhac_lai(ten_chuan)}»",
            ten_dang_nhap=_nhac_lai(ten_chuan),
        )
        return {"ok": True, "csrf": phien["csrf"], "nguoi_dung": ban_ghi}


# ───────────── ĐĂNG NHẬP / ĐĂNG XUẤT ─────────────

_LAN_CANH_BAO_KHO_HONG = [0.0]


def _canh_bao_kho_hong_khi_dang_nhap() -> None:
    """Một dòng cảnh báo kênh he_thong khi đăng nhập gặp kho người dùng hỏng — tối đa một dòng mỗi 60 giây."""
    bay_gio = time.monotonic()
    if bay_gio - _LAN_CANH_BAO_KHO_HONG[0] < 60 and _LAN_CANH_BAO_KHO_HONG[0]:
        return
    _LAN_CANH_BAO_KHO_HONG[0] = bay_gio
    nhat_ky_mod.ghi(
        "he_thong",
        "canh_bao",
        "Đăng nhập web bị từ chối vì kho tài khoản nguoi_dung.json không đọc được — khôi phục từ bản sao lưu.",
    )


#: Nhật ký «tên đăng nhập sai mẫu»: tối đa một dòng mỗi IP mỗi 60 giây, nhớ tối đa 1024 IP (bỏ cũ nhất) — kẻ gửi
#: dồn dập không đẩy trôi được dòng nhật ký khác, và RAM có trần.
_TEN_SAI_LAN_GHI: dict[str, float] = {}


def _ghi_ten_sai_mau(request: Request, ten: Any) -> None:
    ip, _ = gioi_han_mod.ip_hieu_luc_phan_loai(request.client.host if request.client else "",
                                               request.headers.get("x-forwarded-for"))
    ip = ip or "khong-xac-dinh"
    bay_gio = time.monotonic()
    if bay_gio - _TEN_SAI_LAN_GHI.get(ip, -1e9) < 60:
        return
    if len(_TEN_SAI_LAN_GHI) >= 1024:
        _TEN_SAI_LAN_GHI.pop(next(iter(_TEN_SAI_LAN_GHI)))
    _TEN_SAI_LAN_GHI.pop(ip, None)
    _TEN_SAI_LAN_GHI[ip] = bay_gio
    mo_ta = _nhac_lai(ten[:200]) if isinstance(ten, str) else f"<{type(ten).__name__}>"
    nhat_ky_mod.ghi("quan_tri", "canh_bao",
                    f"Đăng nhập thất bại: tên đăng nhập không hợp lệ «{mo_ta}» từ IP {ip} (tối đa một dòng/phút/IP)",
                    ip=ip)


@dang_nhap.post("", dependencies=KHONG_LUU)
async def dang_nhap_web(request: Request, response: Response):
    # Ngoại lệ mở có chủ đích: Đăng nhập giao diện web bằng mật khẩu (giới hạn tần suất IP,
    # khóa tên tài khoản sau 5 lần sai liên tiếp trong 15 phút, băm giả chống timing attack, lỗi 401 đồng nhất).
    # KHÔNG chịu cổng IP quản trị TRƯỚC khi kiểm mật khẩu, có chủ đích: người dùng thường đăng nhập từ ngoài cổng.
    # Tài khoản vai admin: mật khẩu đúng mà IP ngoài cổng → 403 ip_not_allowed, KHÔNG tạo phiên (phiên admin ngoài
    # cổng chẳng dùng được gì — mọi chỗ dùng phiên admin đều chịu cổng: xac_thuc.can_phien_web, nguoi_goi). Kiểm SAU
    # mật khẩu để sai mật khẩu vẫn là 401 đồng nhất (không lộ vai); 403 không lộ thêm gì so với 200 trước đây.
    gioi_han_mod.kiem_tra_truoc_xac_thuc(request)
    phien_web_mod.kiem_tra_kho()  # kho phiên hỏng → 503 trước khi kiểm mật khẩu (không lộ gì về tài khoản)
    ip_khach = gioi_han_mod.lay_ip_an_toan(request)

    _kiem_content_type_json(request)
    than = await _than_json(request)
    # Tên không thể là tài khoản (sai mẫu, quá dài, sai kiểu) = cùng 401 đồng nhất + băm giả, và KHÔNG đi tiếp xuống
    # bộ theo dõi sai/nhật ký: mọi cấu trúc trong RAM chỉ nhận khoá đã qua mẫu tên (≤ 32 ký tự).
    ten_dang_nhap = nguoi_dung_mod.ten_co_the_ton_tai(than.get("ten_dang_nhap")) or ""
    mat_khau = than.get("mat_khau")
    mat_khau = mat_khau if isinstance(mat_khau, str) else ""  # sai kiểu = thiếu → cùng 401 đồng nhất

    try:
        if not ten_dang_nhap or not mat_khau:
            if not ten_dang_nhap and than.get("ten_dang_nhap") not in (None, ""):
                _ghi_ten_sai_mau(request, than.get("ten_dang_nhap"))
            await nguoi_dung_mod.chay_bam_gia()
            gioi_han_mod.ghi_loi_xac_thuc(request)
            raise LoiOpenAI(
                401,
                "Sai tên đăng nhập hoặc mật khẩu.",
                loai="invalid_request_error",
                code="invalid_credentials",
            )

        # 1. Kiểm tra khóa hai tầng: theo (IP, tên) và theo tên toàn cục
        try:
            nguoi_dung_mod.kiem_tra_khoa_ten(ten_dang_nhap, ip=ip_khach)
        except LoiOpenAI:
            await nguoi_dung_mod.chay_bam_gia()
            gioi_han_mod.ghi_loi_xac_thuc(request)
            nhat_ky_mod.ghi(
                "quan_tri",
                "canh_bao",
                f"Từ chối đăng nhập: tài khoản «{_nhac_lai(ten_dang_nhap)}» đang bị khoá tạm thời",
                ten_dang_nhap=_nhac_lai(ten_dang_nhap),
            )
            raise

        # 2. Tìm người dùng — kho hỏng: cùng 401 đồng nhất như sai thông tin (không lộ), một cảnh báo he_thong
        try:
            nd = nguoi_dung_mod.tim_theo_ten(ten_dang_nhap)
        except nguoi_dung_mod.LoiKhoNguoiDung:
            await nguoi_dung_mod.chay_bam_gia()
            _canh_bao_kho_hong_khi_dang_nhap()
            raise LoiOpenAI(
                401,
                "Sai tên đăng nhập hoặc mật khẩu.",
                loai="invalid_request_error",
                code="invalid_credentials",
            ) from None
        if nd is None or not nd.get("bat", True):
            nguoi_dung_mod.ghi_nhan_dang_nhap_sai(ten_dang_nhap, ip=ip_khach)
            await nguoi_dung_mod.chay_bam_gia()
            gioi_han_mod.ghi_loi_xac_thuc(request)
            nhat_ky_mod.ghi(
                "quan_tri",
                "canh_bao",
                f"Đăng nhập thất bại: tên «{_nhac_lai(ten_dang_nhap)}» không tồn tại hoặc bị tắt",
                ten_dang_nhap=_nhac_lai(ten_dang_nhap),
            )
            raise LoiOpenAI(
                401,
                "Sai tên đăng nhập hoặc mật khẩu.",
                loai="invalid_request_error",
                code="invalid_credentials",
            )

        # 3. Kiểm tra mật khẩu
        dung = await nguoi_dung_mod.kiem_tra_mat_khau(mat_khau, nd.get("bam_mat_khau", ""))
    except nguoi_dung_mod.LoiHangChoQuaTai:
        gioi_han_mod.ghi_loi_xac_thuc(request)
        raise
    if not dung:
        nguoi_dung_mod.ghi_nhan_dang_nhap_sai(ten_dang_nhap, ip=ip_khach)
        gioi_han_mod.ghi_loi_xac_thuc(request)
        nhat_ky_mod.ghi(
            "quan_tri",
            "canh_bao",
            f"Đăng nhập thất bại: sai mật khẩu cho «{_nhac_lai(ten_dang_nhap)}»",
            ten_dang_nhap=_nhac_lai(ten_dang_nhap),
        )
        raise LoiOpenAI(
            401,
            "Sai tên đăng nhập hoặc mật khẩu.",
            loai="invalid_request_error",
            code="invalid_credentials",
        )

    # 4. Mật khẩu đúng: tài khoản admin chịu cổng IP quản trị trước khi có phiên (xem lý do ở đầu hàm)
    if nd.get("vai") == "admin":
        ip_cho_phep_mod.kiem_tra_ip_quan_tri(request)

    # 5. Thành công: reset bộ đếm sai theo cặp (IP, tên) và trừ tầng 2, tạo phiên mới
    nguoi_dung_mod.ghi_nhan_dang_nhap_dung(ten_dang_nhap, ip=ip_khach)
    phien, token_tho = phien_web_mod.tao_phien(nd["id"])
    phien_web_mod.dat_cookie_phien(response, request, token_tho)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Đăng nhập web thành công: «{_nhac_lai(ten_dang_nhap)}»",
        ten_dang_nhap=_nhac_lai(ten_dang_nhap),
    )
    return {"ok": True, "csrf": phien["csrf"], "nguoi_dung": nguoi_dung_mod.ban_ghi_sach(nd)}


@dang_xuat.post("")
def dang_xuat_web(request: Request, response: Response):
    phien = getattr(request.state, "phien_web", None)
    if phien and "bam" in phien:
        phien_web_mod.thu_hoi_phien(phien["bam"])
    phien_web_mod.xoa_cookie_phien(response)
    return {"ok": True}


# ───────────── TỰ PHỤC VỤ (can_phien_web) ─────────────

@toi.get("", dependencies=KHONG_LUU)
def lay_thong_tin_toi(request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    phien = getattr(request.state, "phien_web", {})
    return {
        "id": nd.get("id"),
        "ten_dang_nhap": nd.get("ten_dang_nhap"),
        "vai": nd.get("vai"),
        "csrf": phien.get("csrf"),
        "pham_vi_toi_da": list(nd.get("pham_vi_toi_da", ["*"] if nd.get("vai") == "admin" else [])),
    }


@toi.post("/mat-khau")
async def doi_mat_khau_toi(request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    phien = getattr(request.state, "phien_web", {})
    than = await _than_json(request)
    mat_khau_cu = _chuoi_tuy_chon(than, "mat_khau_cu")
    mat_khau_moi = _chuoi_tuy_chon(than, "mat_khau_moi")
    if not mat_khau_cu or not mat_khau_moi:
        raise LoiOpenAI(400, "Cần cung cấp mật khẩu cũ và mật khẩu mới.", code="invalid_input")
    phien_web_mod.kiem_tra_kho()  # bước thu hồi phiên sau đó phải chạy được, kiểm trước khi đổi
    ip_khach = gioi_han_mod.lay_ip_an_toan(request)

    await nguoi_dung_mod.doi_mat_khau_chinh_minh(nd["id"], mat_khau_cu, mat_khau_moi, ip=ip_khach)
    phien_web_mod.thu_hoi_phien_nguoi_dung(nd["id"], tru_bam=phien.get("bam"))
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Người dùng «{_nhac_lai(nd.get('ten_dang_nhap', ''))}» đổi mật khẩu thành công",
        id_nguoi_dung=nd["id"],
    )
    return {"ok": True}


@toi.get("/khoa")
def lay_khoa_toi(request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    return {"khoa": khoa_khach_mod.danh_sach_khoa_theo_chu(nd["id"])}


@toi.post("/khoa", dependencies=KHONG_LUU)
async def tao_khoa_toi(request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    than = await _than_json(request)
    nhan = (_chuoi_tuy_chon(than, "nhan") or "").strip()

    # Trần tối đa 10 khoá
    ds_khoa = khoa_khach_mod.danh_sach_khoa_theo_chu(nd["id"])
    if len(ds_khoa) >= 10:
        raise LoiOpenAI(400, "Mỗi người dùng chỉ được tạo tối đa 10 khoá API.", code="max_keys_reached")

    tran = nguoi_dung_mod.tran_pham_vi(nd)  # admin → «*», user → pham_vi_toi_da
    # Không nêu trường → bằng đúng trần. Đã nêu (kể cả null) → danh sách KHÔNG rỗng gồm mã hợp lệ; rỗng KHÔNG là «tất cả».
    pv_yc = khoa_khach_mod.chuan_hoa_pham_vi(than["pham_vi"] if "pham_vi" in than else tran, cho_phep_rong=False)
    if "*" not in tran:
        for s in pv_yc:
            if s not in tran:
                raise LoiOpenAI(403, f"Không thể yêu cầu phạm vi vượt quá giới hạn (thiếu «{_nhac_lai(s)}»).",
                                loai="insufficient_scope", code="insufficient_scope")

    het_han = than.get("het_han")
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(nhan=nhan, pham_vi=pv_yc, chu_id=nd["id"], het_han=het_han)
    return {"khoa": ban_ghi, "khoa_tho": khoa_tho}


@toi.delete("/khoa/{id}")
def xoa_khoa_toi(id: str, request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    ds_khoa = khoa_khach_mod.danh_sach_khoa_theo_chu(nd["id"])
    tim = next((k for k in ds_khoa if k.get("id") == id), None)
    if tim is None:
        # Khoá của người khác hoặc không tồn tại đều trả 404 giống hệt (không lộ tồn tại)
        raise LoiOpenAI(404, "Không tìm thấy khoá khách.", code="key_not_found")
    khoa_khach_mod.xoa_khoa(id)
    return {"ok": True}


# ───────────── QUẢN TRỊ NGƯỜI DÙNG (QT) ─────────────

@nguoi_dung.get("")
def lay_danh_sach_nguoi_dung():
    return {"nguoi_dung": nguoi_dung_mod.danh_sach_nguoi_dung()}


@nguoi_dung.post("", dependencies=KHONG_LUU)
async def tao_nguoi_dung_moi(request: Request):
    than = await _than_json(request)
    vai = than.get("vai", "user")  # kiểu kiểm trong tao_nguoi_dung (kiem_vai), trần trong chuan_hoa_pham_vi
    ten_dang_nhap = _chuoi_tuy_chon(than, "ten_dang_nhap")
    mat_khau = _chuoi_tuy_chon(than, "mat_khau")
    pham_vi_toi_da = than.get("pham_vi_toi_da")

    ban_ghi, ten_chuan, mat_khau_tho = await nguoi_dung_mod.tao_nguoi_dung(
        vai=vai,
        ten_dang_nhap=ten_dang_nhap,
        mat_khau=mat_khau,
        pham_vi_toi_da=pham_vi_toi_da,
    )
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Quản trị viên tạo người dùng «{_nhac_lai(ten_chuan)}» (vai: {ban_ghi['vai']})",
        id_nguoi_dung=ban_ghi["id"],
        vai=ban_ghi["vai"],
    )
    return {"nguoi_dung": ban_ghi, "ten_dang_nhap": ten_chuan, "mat_khau": mat_khau_tho}


@nguoi_dung.patch("/{id}")
async def cap_nhat_nguoi_dung(id: str, request: Request):
    than = await _than_json(request)
    phien_web_mod.kiem_tra_kho()  # thu hồi phiên khi tắt phải chạy được — kiểm TRƯỚC khi sửa bản ghi
    vai = than.get("vai")
    bat = than.get("bat")
    pham_vi_toi_da = than.get("pham_vi_toi_da")

    ban_ghi = nguoi_dung_mod.sua_nguoi_dung(
        id_=id,
        vai=vai,
        bat=bat,
        pham_vi_toi_da=pham_vi_toi_da,
    )
    if not ban_ghi["bat"]:  # trạng thái sau thao tác là TẮT → thu hồi mọi phiên (bật lại không sống lại phiên cũ)
        phien_web_mod.thu_hoi_phien_nguoi_dung(id)
        novnc_mod.thu_hoi_phien_theo_nguoi_dung(id)

    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Cập nhật tài khoản người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
        bat=ban_ghi["bat"],
        vai=ban_ghi["vai"],
    )
    return {"nguoi_dung": ban_ghi}


@nguoi_dung.post("/{id}/dat-lai-mat-khau", dependencies=KHONG_LUU)
async def dat_lai_mat_khau(id: str):
    phien_web_mod.kiem_tra_kho()  # không đặt mật khẩu mới rồi mới 503 (mật khẩu mất, không ai biết)
    ban_ghi, mat_khau_moi = await nguoi_dung_mod.dat_lai_mat_khau_nguoi_dung(id)
    phien_web_mod.thu_hoi_phien_nguoi_dung(id)
    novnc_mod.thu_hoi_phien_theo_nguoi_dung(id)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Đặt lại mật khẩu cho người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
    )
    return {"nguoi_dung": ban_ghi, "mat_khau_moi": mat_khau_moi}


@nguoi_dung.delete("/{id}")
def xoa_tai_khoan(id: str):
    phien_web_mod.kiem_tra_kho()  # kiểm trước khi xoá: không để xoá xong người dùng rồi 503 bỏ dở khoá/phiên
    khoa_khach_mod.kiem_tra_kho()  # cùng lý do: kho khoá hỏng → 503 TRƯỚC khi xoá người dùng
    nguoi_dung_mod.xoa_nguoi_dung(id)
    khoa_khach_mod.xoa_khoa_theo_chu(id)
    phien_web_mod.thu_hoi_phien_nguoi_dung(id)
    novnc_mod.thu_hoi_phien_theo_nguoi_dung(id)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Xoá tài khoản người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
    )
    return {"ok": True}


# ───────────── Sao lưu và khôi phục (/api/sao-luu, /api/khoi-phuc) ─────────────

@sao_luu.post("/sao-luu")
async def tao_sao_luu_route(request: Request):
    than = await _than_json(request)
    kem_phien = than.get("kem_phien", False)
    if not isinstance(kem_phien, bool):
        raise LoiOpenAI(400, "«kem_phien» phải là true/false.", code="invalid_request")
    # scrypt + đọc tệp: luồng phụ, không chặn vòng sự kiện.
    du_lieu_bytes, ten_tep = await asyncio.to_thread(sao_luu_mod.tao_ban_sao_luu, than.get("mat_khau"), kem_phien)
    return Response(
        content=du_lieu_bytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{ten_tep}"',
            "Cache-Control": "no-store",
        },
    )


def _truong_mot(yc, ten: str) -> str | None:
    v = yc.truong.get(ten)
    if isinstance(v, list):
        v = v[0] if v else None
    return None if v is None else str(v).strip()


@sao_luu.post("/khoi-phuc")
async def khoi_phuc_route(request: Request):
    """Hai chế độ cùng MỘT kế hoạch (app/sao_luu.py): `kiem` (mặc định) trả kế hoạch + khác biệt nhạy cảm + sha256 của
    tệp, không ghi; `ap_dung` đòi `sha256` đó rồi mới ghi."""
    yc = await doc_multipart(request)
    ds_tep = yc.tep_ten("tep")
    if len(ds_tep) != 1 or not ds_tep[0].noi_dung:
        nhat_ky_mod.ghi("quan_tri", "canh_bao", "Khôi phục thất bại: thiếu hoặc thừa tệp trong trường «tep»")
        raise LoiOpenAI(400, "Cần đúng một tệp sao lưu trong trường «tep».", code="sao_luu_khong_hop_le")
    tep = ds_tep[0]
    mat_khau = _truong_mot(yc, "mat_khau") or None
    che_do = (_truong_mot(yc, "che_do") or "kiem").lower()
    if che_do not in ("kiem", "ap_dung"):
        raise LoiOpenAI(400, "«che_do» chỉ nhận «kiem» hoặc «ap_dung».", code="invalid_request")

    try:
        if che_do == "kiem":
            return await sao_luu_mod.kiem_khoi_phuc(tep.noi_dung, mat_khau, so=_so(request), ng=_ng(request))
        return await sao_luu_mod.ap_dung_khoi_phuc(tep.noi_dung, mat_khau, so=_so(request), ng=_ng(request),
                                                   sha256_da_kiem=_truong_mot(yc, "sha256"))
    except LoiOpenAI as e:
        nhat_ky_mod.ghi("quan_tri", "canh_bao", f"Khôi phục thất bại ({che_do}): {e}", che_do=che_do)
        raise
    except Exception as e:
        nhat_ky_mod.ghi("quan_tri", "loi", f"Khôi phục lỗi không xác định ({che_do}): {type(e).__name__}",
                        che_do=che_do)
        raise LoiOpenAI(400, "Bản sao lưu không hợp lệ.", code="sao_luu_khong_hop_le") from e
