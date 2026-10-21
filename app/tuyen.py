"""Các tuyến API của lõi: /v1 (chuẩn OpenAI), /api/modules, /api/tinh-nang, /api/nguon-git, /m/<id>/.

Quyền: MỞ cho GET/POST /api/thiet-lap, POST /api/dang-nhap;
mọi tuyến API khác (/api/modules, /api/tinh-nang...) đòi khoá/phiên quản trị (QT) hoặc phiên web (PW).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
    danh_tinh as danh_tinh_mod,
    dia_chi as dia_chi_mod,
    gioi_han as gioi_han_mod,
    ip_cho_phep as ip_cho_phep_mod,
    kho,
    khoa_khach as khoa_khach_mod,
    kho_tai_khoan as kho_tai_khoan_mod,
    ma_xac_minh as ma_xac_minh_mod,
    nguoi_dung as nguoi_dung_mod,
    nhat_ky as nhat_ky_mod,
    phien as phien_mod,
    phien_web as phien_web_mod,
    sao_luu as sao_luu_mod,
    tai_khoan_hang_loat as tai_khoan_hang_loat_mod,
    lam_moi_model as lam_moi_model_mod,
    khoi_phuc_phien as khoi_phuc_phien_mod,
    gan_ket_qua_dang_nhap as gan_ket_qua_dang_nhap_mod,
    thiet_bi as thiet_bi_mod,
    tri_thuc as tri_thuc_mod,
    novnc as novnc_mod,
    webhook as webhook_mod,
    gui_ra as gui_ra_mod,
    xac_thuc,
    cong_cu as cong_cu_mod,
    kiem_bien,
    xac_thuc_2fa as xac_thuc_2fa_mod,
)
from app.goi_kha_nang import goi
from app.kham_pha import ID_HOP_LE, dung_luong_tren_dia
from app.loi_openai import LoiOpenAI, lam_sach_thong_diep, nhac_lai
from app.nguon_git import LoiCanXacNhanCaiMoi, LoiGit, NguonGit
from app.so_module import LoiCanDuyetQuyen, LoiModuleDangDuocCan, LoiSo, SoModule
from app.than_co_tran import THAN_JSON, THAN_MULTIPART, doc_het
from app.xac_thuc import can_phien_web, can_quan_tri, dinh_danh_nguoi_thuc_hien, khop_pham_vi, kiem_tra_pham_vi, kiem_tra_step_up, xac_thuc_v1
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
danh_tinh = APIRouter(prefix="/api/danh-tinh", dependencies=QT)
bao_mat = APIRouter(prefix="/api/bao-mat", dependencies=QT)
tai_khoan = APIRouter(prefix="/api/tai-khoan", dependencies=QT)
ma_xac_minh = APIRouter(prefix="/api/ma-xac-minh", dependencies=QT)


@ma_xac_minh.get("/hop-thu")
def liet_ke_hop_thu_doc_ma(request: Request):
    """Hộp thư đọc được từ các module đang chạy khai khả năng «ma_xac_minh»
    (NV122): [{id, email_che, module}] — giao diện dùng để hiện/ẩn nút tích
    «Lấy mã từ hộp thư». Không có hộp thư nào → danh sách rỗng."""
    return {"hop_thu": ma_xac_minh_mod.danh_sach_hop_thu(_so(request))}


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



def _tu_choi_constant(c: str) -> None:
    raise ValueError(f"Giá trị số không hợp lệ: {c}")


async def _than_json(request: Request) -> dict:
    try:
        than = json.loads(await doc_het(request, THAN_JSON), parse_constant=_tu_choi_constant)
    except (ValueError, RecursionError):
        raise LoiOpenAI(400, "Thân yêu cầu không phải JSON hợp lệ", code="invalid_request") from None
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON", code="invalid_request")
    return than


def _lay_bool(than: dict, ten: str, mac_dinh: bool | None = False) -> bool | None:
    """Lấy trường logic từ thân JSON của khách/quản trị: chỉ nhận đúng bool, vắng mặt hoặc null."""
    return kiem_bien.kiem_boolean(than.get(ten), cho_phep_none=True, mac_dinh=mac_dinh, ten_truong=ten)


def _loi_so(e: LoiSo | LoiGit) -> LoiOpenAI:
    return LoiOpenAI(e.ma, lam_sach_thong_diep(str(e)), code=getattr(e, "code", None))


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
        data_loc = []
        for m in data:
            m_id = m.get("id", "")
            tien_to, _, _ = m_id.partition("/")
            mod_id = so.tim_theo_tien_to(tien_to)
            m_kns = m.get("kha_nang") or ["chat"]
            if any(any(khop_pham_vi(pv, kn, module=mod_id) for pv in pham_vi) for kn in m_kns):
                data_loc.append(m)
        data = data_loc
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
    kiem_tra_pham_vi(request, "chat", module=id_)
    _can_phuong_thuc(m, id_, "trò chuyện", "chat")
    ma, headers, noi_dung = await goi(m, "chat", than, stream=stream)
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
    kiem_tra_pham_vi(request, "anh", module=id_)
    _can_phuong_thuc(m, id_, "tạo ảnh", "anh")
    _kiem_n_anh(than.get("n"))
    return _tra_ve(*await goi(m, "anh", than))


@v1.post("/images/edits")
async def v1_anh_sua(request: Request):
    kiem_tra_pham_vi(request, "anh_sua")
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(request, yc.truong)
    kiem_tra_pham_vi(request, "anh_sua", module=id_)
    _can_phuong_thuc(m, id_, "sửa ảnh", "anh_sua")
    _can_tep(yc, "image")
    _can_tep(yc, "mask", toi_thieu=0, toi_da=1)
    prompt = yc.truong.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise LoiOpenAI(400, "Thiếu trường «prompt» (mô tả cần sửa gì)", code="missing_prompt")
    _kiem_n_anh(yc.truong.get("n"), tu_form=True)
    return _tra_ve(*await goi(m, "anh_sua", yc))


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
    kiem_tra_pham_vi(request, "anh_sua" if sua_anh else "anh", module=id_)
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
    kiem_tra_pham_vi(request, "noi", module=id_)
    _can_phuong_thuc(m, id_, "đọc văn bản thành giọng nói", "noi")
    return _tra_ve(*await goi(m, "noi", than))


@v1.post("/audio/transcriptions")
async def v1_nghe(request: Request):
    kiem_tra_pham_vi(request, "nghe")
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(request, yc.truong)
    kiem_tra_pham_vi(request, "nghe", module=id_)
    _can_phuong_thuc(m, id_, "nhận dạng giọng nói", "nghe")
    _can_tep(yc, "file", toi_da=1)
    return _tra_ve(*await goi(m, "nghe", yc))


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
    kiem_tra_pham_vi(request, "video", "video_tao", module=id_)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    if yc and yc.tep:
        # video_tao nhận thân JSON; chưa có hợp đồng đưa tệp cho nó — từ chối rõ ràng thay vì lặng lẽ bỏ tệp.
        raise LoiOpenAI(415, f"POST /v1/videos chưa nhận phần tệp (trường: "
                             f"{', '.join(sorted({t.ten_truong for t in yc.tep}))}) — tệp tham chiếu chưa hỗ trợ. "
                             "Gửi multipart chỉ có trường chữ, hoặc thân JSON với ảnh đầu vào dạng base64/data URL "
                             "theo tài liệu của module.", code="unsupported_media_type")
    return _gan_tien_to_ma_video(so.tien_to_cua(id_), *await goi(m, "video_tao", than))


@v1.get("/videos/{ma}")
async def v1_video_trang_thai(ma: str, request: Request):
    kiem_tra_pham_vi(request, "video", "video_trang_thai")
    so = _so(request)
    id_, m, trong = _module_theo_ma_video(request, ma)
    kiem_tra_pham_vi(request, "video", "video_trang_thai", module=id_)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _gan_tien_to_ma_video(so.tien_to_cua(id_), *await goi(m, "video_trang_thai", trong))


@v1.get("/videos/{ma}/content")
async def v1_video_noi_dung(ma: str, request: Request):
    kiem_tra_pham_vi(request, "video", "video_noi_dung")
    id_, m, trong = _module_theo_ma_video(request, ma)
    kiem_tra_pham_vi(request, "video", "video_noi_dung", module=id_)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _tra_ve(*await goi(m, "video_noi_dung", trong))


@v1.post("/embeddings")
async def v1_embeddings(request: Request):
    kiem_tra_pham_vi(request, "nhung")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    kiem_tra_pham_vi(request, "nhung", module=id_)
    so = _so(request)
    km = so.catalog.get(id_)
    if km and "nhung" in getattr(km, "chuan_loi", {}):
        raise LoiOpenAI(501, f"Khả năng «nhung» của module {id_!r} không đúng chuẩn", code="capability_not_supported")
    _can_phuong_thuc(m, id_, "nhúng vector", "nhung")
    inp = than.get("input")
    if inp is None or not isinstance(inp, (str, list)):
        raise LoiOpenAI(400, "Thiếu hoặc sai trường «input» (chuỗi hoặc danh sách)", code="invalid_input")
    return _tra_ve(*await goi(m, "nhung", than))


@v1.post("/rerank")
async def v1_rerank(request: Request):
    kiem_tra_pham_vi(request, "xep_hang")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    kiem_tra_pham_vi(request, "xep_hang", module=id_)
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

    return _tra_ve(*await goi(m, "xep_hang", than))



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
                   can_module=km.manifest.get("can_module") or [],
                   nhom=km.manifest.get("nhom"),
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
                                         "loai_ten", "loai_thu_tu", "nhom", "muc", "can_module")}
                  | {"nguon": km.nguon, "da_cau_hinh": km.id in so.dang_chay,
                     "dung_luong_dia": dung_luong_tren_dia(km.thu_muc) if getattr(km, "thu_muc", None) else None})
    return {"modules": ds, "loi": so.loi}


@modules.get("/thong-bao", dependencies=QT)
async def thong_bao(request: Request):
    return _ng(request).thong_bao()


def _ban_do_ngoai_tu_nguon_git(request: Request) -> dict:
    """can_module của các module nguồn git BIẾT mà máy chưa cài (NV222):
    trích từ `quyen` (can_module là một quyền — app/quyen_module.py)."""
    ra: dict[str, list[str]] = {}
    for m in (_ng(request).trang_thai.get("modules") or []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        deps = sorted({q.get("ten") for q in (m.get("quyen") or [])
                       if isinstance(q, dict) and q.get("loai") == "can_module" and q.get("ten")})
        if deps:
            ra[m["id"]] = deps
    return ra


@modules.get("/do-thi-phu-thuoc", dependencies=QT)
async def do_thi_phu_thuoc(request: Request):
    """Đồ thị phụ thuộc toàn cục cho giao diện vẽ sơ đồ (NV222):
    {"nut": [{id, ten, da_cai, loai}], "canh": [{tu, den}], "thu_tu_goi_y": [...]}."""
    try:
        return _so(request).do_thi_phu_thuoc(_ban_do_ngoai_tu_nguon_git(request))
    except LoiSo as e:
        raise _loi_so(e) from None


@modules.get("/{id_}/ke-hoach-cai", dependencies=QT)
async def ke_hoach_cai(id_: str, request: Request):
    """Kế hoạch cài các phụ thuộc còn thiếu của module (NV222) — chỉ tính toán,
    không cài gì. Giao diện hiện kế hoạch này rồi mới cho 1 bấm xác nhận."""
    try:
        return _so(request).ke_hoach_cai(id_, _ban_do_ngoai_tu_nguon_git(request))
    except LoiSo as e:
        raise _loi_so(e) from None


@modules.post("/cai-theo-ke-hoach", dependencies=QT)
async def cai_theo_ke_hoach(request: Request):
    """Cài các phụ thuộc còn thiếu theo đúng kế hoạch rồi cài chính module (NV222).
    Thân: {"id_": "<id>", "ke_hoach": [...], "xac_nhan"?: bool, "ma_xac_nhan"?: str}.
    Kế hoạch phải khớp đúng với tính lại lúc chạy (chống thân giả) → 409
    `ke_hoach_cu`. Cài hỏng ở đâu DỪNG ở đó — không cài lén tiếp, trả tiến độ."""
    than = await _than_json(request)
    id_ = than.get("id_")
    if not isinstance(id_, str) or not id_:
        raise LoiOpenAI(400, 'Thân cần {"id_": "<id module>", "ke_hoach": [...]}')
    ke_hoach = than.get("ke_hoach")
    if not isinstance(ke_hoach, list):
        raise LoiOpenAI(400, 'Thân cần {"id_": "<id module>", "ke_hoach": [...]}')
    xac_nhan = bool(than.get("xac_nhan"))
    ma_xac_nhan = than.get("ma_xac_nhan")
    ng = _ng(request)

    async def ham_cai(dep_id: str):
        return await ng.cai(dep_id, xac_nhan=xac_nhan, ma_xac_nhan=ma_xac_nhan)

    try:
        kq = await _so(request).cai_theo_ke_hoach(id_, ke_hoach, ham_cai)
    except LoiSo as e:
        raise _loi_so(e) from None
    if not kq["ok"]:
        return JSONResponse(kq, status_code=kq.get("ma_http") or 400)
    return kq


@modules.post("/nap-lai", dependencies=QT)
async def nap_lai(request: Request):
    return await _so(request).nap_lai()


@modules.post("/tu-xa", dependencies=QT)
async def them_tu_xa(request: Request):
    try:
        return await _so(request).them_tu_xa(await _than_json(request))
    except LoiSo as e:
        raise _loi_so(e) from None


@modules.get("/tu-xa/preset")
async def preset_tu_xa(request: Request):
    """Danh sách preset nhà cung cấp từ xa (NV199b) — DỮ LIỆU, không bí mật.

    Giao diện «Thêm nhà cung cấp» dùng để điền sẵn id/tên/tiền tố/địa chỉ gốc
    và hướng dẫn lấy khoá. Không có khoá quản trị vẫn gọi được như GET /api/modules;
    thiếu tệp preset thì trả rỗng (giao diện ẩn ô chọn preset).
    """
    from app.so_module import doc_preset_tu_xa

    return {"preset": doc_preset_tu_xa()}


@modules.post("/tu-xa/kiem-tra", dependencies=QT)
async def kiem_tra_tu_xa_endpoint(request: Request):
    """Kiểm THUẦN (không ghi) nhà cung cấp từ xa trước khi đăng ký (NV199b).

    Thân: {base_url, api_key?, model?}. Chỉ gọi GET /models rất ngắn, phân loại
    lỗi thành khoa/han_muc/dia_chi/mo_hinh; không bao giờ lộ api_key.
    """
    from app.so_module import kiem_tra_tu_xa

    try:
        return await kiem_tra_tu_xa(await _than_json(request))
    except LoiSo as e:
        raise _loi_so(e) from None


@modules.post("/do-nut-gpu", dependencies=QT)
async def do_nut_gpu_endpoint(request: Request):
    """Dò nút GPU trước khi đăng ký (NV140): đọc health và w2a-node, kiểm SSRF."""
    from app.nut_gpu import do_nut_gpu

    than = await _than_json(request)
    base_url = than.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise LoiOpenAI(400, "Thiếu trường «base_url»")
    api_key = than.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise LoiOpenAI(400, "api_key phải là chuỗi")
    try:
        return await do_nut_gpu(base_url, api_key)
    except ValueError as e:
        raise LoiOpenAI(400, str(e)) from None
    except Exception as e:
        raise LoiOpenAI(502, f"Không kết nối được tới nút GPU: {e}") from None


@modules.post("/{id_}/bat-tat", dependencies=QT)
async def bat_tat_module(id_: str, request: Request):
    """Bật hoặc tắt module từ xa / nút GPU (NV140)."""
    than = await _than_json(request)
    if "bat" not in than:
        raise LoiOpenAI(400, "Thiếu trường «bat» (true hoặc false)")
    bat = bool(than.get("bat"))
    try:
        return await _so(request).bat_tat_tu_xa(id_, bat)
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
    if kq.get("ok"):
        kq = lam_moi_model_mod.sau_khi_cai(request.app, id_, kq)
    return JSONResponse(kq, status_code=200 if kq["ok"] else 400)


@modules.delete("/{id_}", dependencies=QT)
async def go(id_: str, request: Request):
    """Gỡ module. NV73: module đang được module khác cần (`can_module`) → 409 `module_dang_duoc_can`
    kèm danh sách; gỡ module cuối cùng còn cần một module chung → phản hồi có `hoi_giu_phu_thuoc`
    để giao diện hỏi quản trị giữ hay gỡ theo (lõi không tự quyết)."""
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    try:
        kq = await _so(request).go(id_, nguoi_thuc_hien=nguoi_thuc_hien)
    except LoiModuleDangDuocCan as e:
        return JSONResponse({"ok": False, "ma": "module_dang_duoc_can", "id": e.id_,
                             "dang_duoc_can_boi": e.dung}, status_code=409)
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


@modules.get("/{id_}/jwt-trang-thai", dependencies=QT)
async def jwt_trang_thai(id_: str, request: Request):
    """NV207: trạng thái JWT từng khoá của một module (ngày hết hạn từ claim
    exp, trạng thái làm mới gần nhất, cờ cảnh báo). Module không khai
    `jwt_trang_thai()` → 404. Không bí mật (khoá đã mờ)."""
    so = _so(request)
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} đã cài nhưng chưa có khoá/cấu hình — chưa có gì để kiểm"
                        if id_ in so.catalog else f"Không có module {id_!r}")
    fn = getattr(m, "jwt_trang_thai", None)
    if not callable(fn):
        raise LoiOpenAI(404, f"Module {id_!r} không hỗ trợ trạng thái JWT")
    return fn()


#: NV212: nút «Thử chat» thật cho từng nhà cung cấp AI (tách khỏi «Kiểm tra» sức khoẻ).
#: Mỗi lần thử tốn lượt của tài khoản thật nên chỉ chạy khi quản trị bấm tay — không tự gọi định kỳ.
_THU_CHAT_TRA_LOI_TOI_DA = 80


def _thu_chat_tran_giay() -> float:
    """Trần thời gian một lượt thử chat (WEB2API_THU_CHAT_TRAN_GIAY, mặc định 60 giây).
    Giá trị không phải số dương hữu hạn → mặc định: đặt sai không được tắt trần."""
    try:
        val = float(os.environ.get("WEB2API_THU_CHAT_TRAN_GIAY", "60"))
    except ValueError:
        return 60.0
    return val if val > 0 and math.isfinite(val) else 60.0


def _thu_chat_gian_cach_giay() -> float:
    """Giãn tối thiểu giữa hai lần thử của cùng một module, chống bấm dồn
    (WEB2API_THU_CHAT_GIAN_CACH_GIAY, mặc định 5 giây)."""
    try:
        val = float(os.environ.get("WEB2API_THU_CHAT_GIAN_CACH_GIAY", "5"))
    except ValueError:
        return 5.0
    return val if val >= 0 and math.isfinite(val) else 5.0


_THU_CHAT_KHOA: dict[str, asyncio.Lock] = {}
_THU_CHAT_LAN_CUOI: dict[str, float] = {}


def _phan_loai_loi_thu_chat(ma: int | None) -> tuple[str, str]:
    """(ma_loi, thong_diep_ngan) theo MÃ HTTP / mã chuẩn của lõi — không dò chuỗi, không tên nhà cung cấp."""
    if ma == 401:
        return "token_bi_tu_choi", "Token bị từ chối (401)"
    if ma == 403:
        return "bi_chan_chong_bot", "Bị chặn chống bot (403)"
    if ma == 404:
        return "khong_tim_thay", "Không tìm thấy (404)"
    if ma == 429:
        return "het_han_muc", "Hết hạn mức (429)"
    if isinstance(ma, int) and 500 <= ma <= 599:
        return "dich_vu_loi", f"Dịch vụ lỗi ({ma})"
    if isinstance(ma, int) and 400 <= ma <= 499:
        return "loi_yeu_cau", f"Yêu cầu không hợp lệ ({ma})"
    return "loi_khac", "Lỗi không xác định"


def _lam_sach_tra_loi(s: str) -> str:
    sach = "".join(c for c in s.strip() if c.isprintable() or c in "\n\t")
    return " ".join(sach.split())[:_THU_CHAT_TRA_LOI_TOI_DA]


def _trich_tra_loi_ngan(noi_dung: bytes) -> str | None:
    try:
        j = json.loads(noi_dung)
    except (ValueError, RecursionError):
        return None
    choices = j.get("choices") if isinstance(j, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message")
    nd = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(nd, str) or not nd.strip():
        return None
    return _lam_sach_tra_loi(nd) or None


async def _model_thu_chat(so: SoModule, id_: str, m, model_goi) -> str:
    """Model dùng để thử chat: người gọi chỉ định (phải thuộc module) hoặc model đầu tiên
    khả dụng của module (ưu tiên model có khả năng chat)."""
    if isinstance(model_goi, str) and model_goi.strip():
        model_goi = model_goi.strip()
        tien_to, gach, _ = model_goi.partition("/")
        if not gach or so.tim_theo_tien_to(tien_to) != id_:
            raise LoiOpenAI(400, f"Model {model_goi!r} không thuộc module {id_!r} "
                                 f"(xem GET /api/modules/{id_}/model)", code="model_khong_thuoc_module")
        return model_goi
    fn = getattr(m, "models", None)
    if not callable(fn):
        raise LoiOpenAI(502, f"Module {id_!r} không liệt kê được model", code="khong_co_model")
    try:
        tat = set(kho.doc_cai_dat_model_module(id_).get("tat", []))
    except kho.KhoHong:
        tat = set()
    try:
        ds = await fn()
    except Exception as e:
        raise LoiOpenAI(502, f"Module {id_!r} lỗi khi liệt kê model: {type(e).__name__}",
                        code="khong_co_model") from None
    ung_vien = [muc["id"] for muc in (ds or [])
                if isinstance(muc, dict) and isinstance(muc.get("id"), str)
                and muc["id"] and muc["id"] not in tat]
    for muc in (ds or []):
        if isinstance(muc, dict) and muc.get("id") in ung_vien:
            kn = muc.get("kha_nang")
            if not isinstance(kn, list) or "chat" in kn:
                return muc["id"]
    if ung_vien:
        return ung_vien[0]
    raise LoiOpenAI(502, f"Module {id_!r} không có model nào khả dụng", code="khong_co_model")


@modules.post("/{id_}/thu-chat", dependencies=QT)
async def thu_chat(id_: str, request: Request):
    """NV212: thử chat THẬT một model của module — gửi một câu hỏi ngắn qua ĐÚNG đường
    /v1/chat/completions nội bộ (cùng `goi(m, "chat", ...)` và cùng chuẩn hoá như người dùng
    thật, không đường tắt). Trả {ok, model, do_tre_ms, tra_loi_ngan, ma_loi, thong_diep_ngan,
    lan_thu}: không bao giờ trả token/khoá/thân lỗi thô của nhà cung cấp. Chỉ quản trị.
    Mỗi module một lượt đồng thời (khoá) + giãn tối thiểu giữa hai lần (chống bấm dồn);
    mỗi lần thử tốn lượt của tài khoản thật nên không tự gọi định kỳ."""
    so = _so(request)
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} đã cài nhưng chưa có khoá/cấu hình — chưa có gì để thử"
                        if id_ in so.catalog else f"Không có module {id_!r}")
    _can_phuong_thuc(m, id_, "trò chuyện", "chat")
    than_qt = await _than_json(request)
    model = await _model_thu_chat(so, id_, m, than_qt.get("model"))

    bay_gio = time.monotonic()
    gian_cach = _thu_chat_gian_cach_giay()
    lan_cuoi = _THU_CHAT_LAN_CUOI.get(id_, 0.0)
    if bay_gio - lan_cuoi < gian_cach:
        raise LoiOpenAI(429, f"Đang thử dồn — thử lại sau {gian_cach - (bay_gio - lan_cuoi):.0f} giây",
                        code="thu_chat_qua_nhanh")
    khoa = _THU_CHAT_KHOA.setdefault(id_, asyncio.Lock())
    if khoa.locked():
        raise LoiOpenAI(429, "Đang có một lượt thử chat cho module này — chờ xong rồi thử lại",
                        code="thu_chat_dang_chay")
    _THU_CHAT_LAN_CUOI[id_] = bay_gio

    than = {"model": model,
            "messages": [{"role": "user", "content": "Trả lời đúng một từ: OK"}],
            "max_tokens": 16, "stream": False}
    bat_dau = time.monotonic()
    async with khoa:
        try:
            async with asyncio.timeout(_thu_chat_tran_giay()):
                ma, _headers, noi_dung = await goi(m, "chat", than, stream=False)
        except TimeoutError:
            do_tre = int((time.monotonic() - bat_dau) * 1000)
            return {"ok": False, "model": model, "do_tre_ms": do_tre, "tra_loi_ngan": None,
                    "ma_loi": "het_gio", "thong_diep_ngan": "Hết thời gian chờ",
                    "lan_thu": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except LoiOpenAI as e:
            ma_loi, thong_diep = _phan_loai_loi_thu_chat(e.ma)
            do_tre = int((time.monotonic() - bat_dau) * 1000)
            return {"ok": False, "model": model, "do_tre_ms": do_tre, "tra_loi_ngan": None,
                    "ma_loi": ma_loi, "thong_diep_ngan": thong_diep,
                    "lan_thu": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except Exception:
            do_tre = int((time.monotonic() - bat_dau) * 1000)
            return {"ok": False, "model": model, "do_tre_ms": do_tre, "tra_loi_ngan": None,
                    "ma_loi": "loi_ket_noi", "thong_diep_ngan": "Lỗi kết nối",
                    "lan_thu": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    do_tre = int((time.monotonic() - bat_dau) * 1000)
    lan_thu = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if ma == 200 and isinstance(noi_dung, bytes):
        noi_dung = _chuan_hoa_chat_thuong(noi_dung)
        return {"ok": True, "model": model, "do_tre_ms": do_tre,
                "tra_loi_ngan": _trich_tra_loi_ngan(noi_dung), "ma_loi": None,
                "thong_diep_ngan": None, "lan_thu": lan_thu}
    ma_loi, thong_diep = _phan_loai_loi_thu_chat(ma if isinstance(ma, int) else None)
    return {"ok": False, "model": model, "do_tre_ms": do_tre, "tra_loi_ngan": None,
            "ma_loi": ma_loi, "thong_diep_ngan": thong_diep, "lan_thu": lan_thu}


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


# ───────────── NV110: trần số mục mac_dinh của PUT /api/modules/{id}/model ────
# Giá trị tự chọn theo spec (spec không nêu số, yêu cầu «biên nhỏ cố định, có test biên»).
TRAN_THEM_MAC_DINH = 5


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
    NV110: `mac_dinh` bị trần số mục (số nhóm thật của module + TRAN_THEM_MAC_DINH) — vượt → 400 `invalid_value`
    (chống phình model.json bằng hàng nghìn nhóm lạ).
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

    if mac_dinh_in is not None:
        nhom_that = {_nhom_cua_model(item) for item in raw if isinstance(item, dict)}
        tran = len(nhom_that) + TRAN_THEM_MAC_DINH
        if len(mac_dinh_in) > tran:
            raise LoiOpenAI(400, f"«mac_dinh» vượt trần số mục cho phép ({tran})", code="invalid_value")

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
            elif nhom not in nhom_that:
                # NV110: nhóm lạ (không thuộc tập nhóm thật của module) bị lọc
                # như tat đang làm — không lưu vào sổ, nêu trong bo_qua.
                bo_qua.add(nhom)
            else:
                mac_dinh_moi[nhom] = mid
    mac_dinh_moi = {nh: mid for nh, mid in mac_dinh_moi.items() if mid in hop_le and mid not in tat_moi}
    tat_luu = (tat_moi & hop_le) | {x for x in cu["tat"] if x not in hop_le}

    try:
        kho.luu_cai_dat_model_module(id_, sorted(tat_luu), mac_dinh_moi)
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
    để DUYỆT bản đang chờ (phải khớp chính xác diff đã xem); không có thân → cài thường.

    NV50: duyệt hạng B cần thêm "xac_nhan_lan_2": true (xác nhận lại lần 2) — thiếu → 409.
    """
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
            # NV50 CG1: duyệt hạng B là hành động nguy hiểm — thiếu xác nhận lần 2
            # (xac_nhan_lan_2) → 409, chưa kích hoạt (chống bấm nhầm). Kiểm ở đây
            # (nơi người bấm), không đặt trong lõi NguonGit.
            if not than.get("xac_nhan_lan_2"):
                raise LoiGit("duyệt hạng B cần xác nhận lần 2: gửi lại với "
                             "{\"xac_nhan_lan_2\": true} cùng chap_nhan_quyen_them và sha_commit",
                             409)
            danh_tinh = getattr(request.state, "danh_tinh", None) or {}
            return await _ng(request).cai(id_, chap_nhan_quyen_them=than.get("chap_nhan_quyen_them"),
                                         sha_commit=than.get("sha_commit"),
                                         nguoi_thuc_hien=danh_tinh.get("id"))
        xac_nhan = bool(than.get("xac_nhan"))
        ma_xac_nhan = than.get("ma_xac_nhan")
        return await _ng(request).cai(id_, xac_nhan=xac_nhan, ma_xac_nhan=ma_xac_nhan)
    except LoiCanDuyetQuyen as e:
        return JSONResponse({"ok": False, "ma": "can_duyet_quyen", "id": e.id_,
                             "sha_commit": e.sha_commit, "tree_sha": e.tree_sha, "diff": e.diff},
                            status_code=409)
    except LoiCanXacNhanCaiMoi as e:
        return JSONResponse({
            "ok": False,
            "ma": "can_xac_nhan_cai_moi",
            "id": e.id_,
            "repo": e.repo,
            "sha_commit": e.sha_commit,
            "phien_ban": e.phien_ban,
            "quyen": e.quyen,
            "ma_xac_nhan": e.ma_xac_nhan,
        }, status_code=409)
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


# ───────────── /api/nguon-git/nguon — NV59: quản lý nhiều nguồn git ─────────────

@nguon_git.get("/nguon")
async def ds_nguon_git(request: Request):
    return {"nguon": _ng(request).danh_sach_nguon()}


@nguon_git.post("/nguon")
async def them_nguon_git(request: Request):
    try:
        return await _ng(request).them_nguon(await _than_json(request))
    except ValueError as e:
        raise LoiOpenAI(400, str(e)) from None
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


@nguon_git.put("/nguon/{id_}")
async def sua_nguon_git(id_: str, request: Request):
    try:
        return await _ng(request).sua_nguon(id_, await _than_json(request))
    except ValueError as e:
        raise LoiOpenAI(400, str(e)) from None
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


@nguon_git.delete("/nguon/{id_}")
async def xoa_nguon_git(id_: str, request: Request):
    try:
        await _ng(request).xoa_nguon(id_)
        return {"ok": True, "id": id_}
    except ValueError as e:
        raise LoiOpenAI(400, str(e)) from None
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


# ───────────── /m/<id>/ — giao diện đi theo module ─────────────

#: Trang module chạy CÙNG origin với giao diện quản trị (khoá quản trị nằm ở localStorage): CSP chặt — chỉ script/kiểu
#: từ chính máy chủ (không inline, không eval), không nhúng khung, không plugin — và header bảo vệ, kể cả khi 404.
HEADER_TRANG_MODULE = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
                                "frame-ancestors 'self'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
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
        nhat_ky_mod.ghi("quan_tri", "thong_tin", "Xoá địa chỉ truy cập đã đặt (đặt lại về tự động)")
    elif isinstance(url, str):
        url_chuan = dia_chi_mod.kiem_tra_va_chuan_hoa_url(url)
        kho.luu_dia_chi_da_dat(url_chuan)
        nhat_ky_mod.ghi("quan_tri", "thong_tin", f"Đổi địa chỉ truy cập thành công: {url_chuan}", url=url_chuan)
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
        nguoi_thuc_hien=nguoi_goi,
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
    so = _so(request)
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(
        nhan=than.get("nhan", ""),
        pham_vi=than.get("pham_vi"),
        het_han=than.get("het_han"),
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request),
        so=so,
    )
    return {"ok": True, "khoa": {**ban_ghi, "khoa": khoa_tho}}


@khoa_khach.post("/{id_}/xoay", dependencies=KHONG_LUU)
async def xoay_khoa_khach(id_: str, request: Request):
    kiem_tra_step_up(request)
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
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request),
    )
    return {"ok": True, "khoa": {**ban_ghi, "khoa": khoa_tho}}


@khoa_khach.patch("/{id_}")
async def sua_khoa_khach(id_: str, request: Request):
    than = await _than_json(request)
    so = _so(request)
    kwargs: dict[str, Any] = {
        "id_khoa": id_,
        "bat": _lay_bool(than, "bat", None),
        "nhan": than.get("nhan"),
        "pham_vi": than.get("pham_vi"),
        "nguoi_thuc_hien": dinh_danh_nguoi_thuc_hien(request),
        "so": so,
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
    so = _so(request)
    kwargs: dict[str, Any] = {
        "id_khoa": id_,
        "bat": _lay_bool(than, "bat", None),
        "nhan": than.get("nhan"),
        "pham_vi": than.get("pham_vi"),
        "nguoi_thuc_hien": dinh_danh_nguoi_thuc_hien(request),
        "so": so,
    }
    if "het_han" in than:
        kwargs["het_han"] = than.get("het_han")
    kq = khoa_khach_mod.sua_khoa(**kwargs)
    return {"ok": True, "khoa": kq}


@khoa_khach.delete("/{id_}")
def xoa_khoa_khach(id_: str, request: Request):
    da_xoa = khoa_khach_mod.xoa_khoa(id_khoa=id_, nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request))
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
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi", module=module)
    so = _so(request)
    ds, loi = await thiet_bi_mod.lay_danh_sach_thuc_the(
        so, loc_q=q, loc_loai=loai, loc_khu=khu, loc_module=module, loc_rui_ro=rui_ro
    )
    danh_tinh = getattr(request.state, "danh_tinh", None)
    if isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "khach":
        pham_vi = danh_tinh.get("pham_vi", [])
        ds = [
            e for e in ds
            if any(
                khop_pham_vi(pv, "thiet_bi:doc", module=e.get("module")) or
                khop_pham_vi(pv, "thiet_bi:ghi", module=e.get("module"))
                for pv in pham_vi
            )
        ]
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
    # NGHE sự kiện cần phạm vi thiet_bi:su_kien_doc; tương thích: khoá có "*" hoặc
    # thiet_bi:su_kien (cũ, trước NV29) vẫn nghe được — ghi trong THAM-CHIEU.
    kiem_tra_pham_vi(request, "thiet_bi:su_kien_doc", "thiet_bi:su_kien")
    danh_tinh = getattr(request.state, "danh_tinh", None) or {}
    q = await thiet_bi_mod.BO_PHAT.dang_ky(
        loai=str(danh_tinh.get("loai", "khach")),
        id_=str(danh_tinh.get("id", "khong_ro")),
        pham_vi=danh_tinh.get("pham_vi", ()),
    )

    async def phat_dong():
        chu_ky = thiet_bi_mod.chu_ky_kiem_lai_khoa_giay()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    su_kien = await asyncio.wait_for(q.get(), timeout=chu_ky)
                    yield f"data: {json.dumps(su_kien, ensure_ascii=False)}\n\n".encode("utf-8")
                except asyncio.TimeoutError:
                    # SEC-003: kiểm lại khoá mỗi chu kỳ bằng cùng hàm xác thực.
                    # Khoá bị tắt/thu hồi/hết hạn (hoặc IP bị khoá) → đóng luồng, không gửi thêm.
                    try:
                        try:
                            del request.state.danh_tinh  # bỏ danh tính đã lưu để xác thực lại từ đầu
                        except (AttributeError, KeyError):
                            # starlette ≥1.7: State.__delattr__ ném KeyError khi thiếu
                            pass
                        await xac_thuc.nguoi_goi(request)
                    except LoiOpenAI:
                        break
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
    # GỬI sự kiện giữ phạm vi thiet_bi:su_kien (quyền NGHE đã tách sang thiet_bi:su_kien_doc).
    kiem_tra_pham_vi(request, "thiet_bi:su_kien")
    # V1_AUTH đã xác thực: nguoi_goi trả danh tính đã lưu, không tính lại.
    danh_tinh = await xac_thuc.nguoi_goi(request)
    nguon = thiet_bi_mod.nguon_cua_danh_tinh(danh_tinh)
    than = await _than_json(request)
    # SEC-003: lõi kiểm tra + đóng dấu. Quy tắc tối thiểu: sự kiện phải nhắm thực thể
    # đang tồn tại trong danh mục lõi (không có quan hệ sở hữu khoá↔thực thể trong lõi).
    sk = thiet_bi_mod.chuan_hoa_su_kien_thiet_bi(than, nguon=nguon)
    if await thiet_bi_mod.lay_mot_thuc_the(sk["thuc_the"], _so(request)) is None:
        raise LoiOpenAI(
            404,
            f"Thực thể «{sk['thuc_the']}» không tồn tại trong danh mục lõi",
            code="not_found",
        )
    sk["luc"] = kho.bay_gio_iso()
    await thiet_bi_mod.BO_PHAT.phat(sk)
    return {"ok": True}


@thiet_bi.get("/{id_thuc_the}")
async def lay_chi_tiet_thiet_bi(id_thuc_the: str, request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi")
    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")
    mod_id = item.get("module")
    kiem_tra_pham_vi(request, "thiet_bi:doc", "thiet_bi:ghi", module=mod_id)
    return {"thuc_the": item, "trang_thai": item.get("trang_thai")}


@thiet_bi.put("/{id_thuc_the}/rui-ro", dependencies=QT)
async def dat_rui_ro_thiet_bi(id_thuc_the: str, request: Request):
    than = await _than_json(request)
    rui_ro = kiem_bien.kiem_thanh_vien(than.get("rui_ro"), thiet_bi_mod.RUI_RO_HOP_LE, ten_truong="rui_ro")
    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    tn = item.get("tinh_nang") if item else None
    thiet_bi_mod.luu_ghi_de_rui_ro(
        id_thuc_the,
        rui_ro,
        tinh_nang=tn,
        rui_ro_mac_dinh=item.get("rui_ro_mac_dinh") if item else None,
    )
    nhat_ky_mod.ghi(
        "thiet_bi",
        "thong_tin",
        f"Đặt rủi ro «{rui_ro}» cho thực thể «{id_thuc_the}»",
        thuc_the=id_thuc_the,
        rui_ro=rui_ro,
        nguoi_goi=getattr(request.state, "nguoi_goi", "quan_tri"),
    )
    return {"ok": True, "id": id_thuc_the, "rui_ro": rui_ro}


@thiet_bi.post("/{id_thuc_the}/xac-nhan", dependencies=QT)
async def xac_nhan_thuc_the_thiet_bi(id_thuc_the: str, request: Request):
    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")
    than = {}
    if int(request.headers.get("content-length", "0") or "0") > 0:
        than = await _than_json(request)
    rui_ro = than.get("rui_ro")
    if rui_ro is not None and rui_ro not in thiet_bi_mod.RUI_RO_HOP_LE:
        raise LoiOpenAI(400, f"Mức rủi ro không hợp lệ: {rui_ro!r} (hợp lệ: {sorted(thiet_bi_mod.RUI_RO_HOP_LE)})", code="invalid_request")
    thiet_bi_mod.xac_nhan_thuc_the(
        id_thuc_the,
        item.get("tinh_nang", []),
        rui_ro=rui_ro,
        rui_ro_mac_dinh=item.get("rui_ro_mac_dinh"),
    )
    nhat_ky_mod.ghi(
        "thiet_bi",
        "thong_tin",
        f"Quản trị xác nhận thực thể «{id_thuc_the}»",
        thuc_the=id_thuc_the,
        nguoi_goi=getattr(request.state, "nguoi_goi", "quan_tri"),
    )
    return {"ok": True, "id": id_thuc_the}


@thiet_bi.post("/{id_thuc_the}/hanh-dong")
async def thuc_hien_hanh_dong_thiet_bi(id_thuc_the: str, request: Request):
    kiem_tra_pham_vi(request, "thiet_bi:ghi")
    than = await _than_json(request)
    hanh_dong = than.get("hanh_dong") or ""
    tham_so = than.get("tham_so") or {}
    xac_nhan = _lay_bool(than, "xac_nhan", False)
    ly_do = str(than.get("ly_do") or "")
    if len(ly_do) > 500:
        raise LoiOpenAI(400, "Độ dài «ly_do» vượt quá giới hạn cho phép (tối đa 500 ký tự)", code="invalid_request")
    so = _so(request)
    item = await thiet_bi_mod.lay_mot_thuc_the(id_thuc_the, so)
    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")
    mod_id = item.get("module")
    kiem_tra_pham_vi(request, "thiet_bi:ghi", module=mod_id)
    # Thực thể nguy hiểm do quản trị tự chạy (không qua hàng chờ) cũng đòi xác nhận lại như mọi thao tác nguy hiểm khác;
    # khách vẫn đi đường hàng chờ (202), nên không bị chặn ở đây.
    dt = getattr(request.state, "danh_tinh", None)
    if item.get("rui_ro") == "nguy_hiem" and isinstance(dt, dict) and dt.get("loai") == "quan_tri":
        kiem_tra_step_up(request)

    ma, kq = await thiet_bi_mod.thuc_hien_hanh_dong_loi(
        id_thuc_the=id_thuc_the,
        hanh_dong=hanh_dong,
        tham_so=tham_so,
        xac_nhan=xac_nhan,
        ly_do=ly_do,
        ngu_canh=None,
        so=so,
    )
    if ma == 202:
        return JSONResponse(kq, status_code=202)
    return kq


# ───────────── Hàng chờ phê duyệt (/api/phe-duyet) ─────────────

@phe_duyet.get("")
async def danh_sach_phe_duyet(request: Request):
    return {"hang_cho": await thiet_bi_mod.lay_hang_cho_hien_thi(_so(request))}


@phe_duyet.post("/{pd_id}/duyet")
async def xu_ly_duyet(pd_id: str, request: Request):
    kiem_tra_step_up(request)
    so = _so(request)
    nguoi_duyet = getattr(request.state, "nguoi_goi", "quan_tri")
    bam_mong_doi = None
    cl = 0
    try:
        cl = int(request.headers.get("content-length", "0") or "0")
    except (ValueError, TypeError):
        cl = 0
    if cl > 0:
        than = await _than_json(request)
        bam_mong_doi = kiem_bien.kiem_chuoi(than.get("bam"), cho_phep_none=True, ten_truong="bam")
    return await thiet_bi_mod.duyet_phe_duyet(pd_id, so, nguoi_duyet=nguoi_duyet, bam_mong_doi=bam_mong_doi)


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
            if any(khop_pham_vi(p, "thiet_bi:ghi") for p in pv):
                duoc_ghi = True
    elif getattr(request.state, "nguoi_goi", "") == "quan_tri":
        duoc_ghi = True

    ds, _ = await thiet_bi_mod.lay_danh_sach_thuc_the(so, loc_q=q, loc_loai=loai)
    if isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "khach":
        pv = danh_tinh.get("pham_vi", [])
        ds = [
            e for e in ds
            if any(
                khop_pham_vi(p, "thiet_bi:doc", module=e.get("module")) or
                khop_pham_vi(p, "thiet_bi:ghi", module=e.get("module"))
                for p in pv
            )
        ]
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
    than = await _than_json(request)
    mod_id = str(than.get("nguon") or "").strip() or None
    kiem_tra_pham_vi(request, "tim_kiem", module=mod_id)
    so = _so(request)
    danh_tinh = getattr(request.state, "danh_tinh", None)
    return await tri_thuc_mod.thuc_hien_tim_kiem(than, so, danh_tinh=danh_tinh)


@tim_kiem.post("/muc-luc", dependencies=V1_AUTH)
async def lay_muc_luc(request: Request):
    than = await _than_json(request)
    mod_id = str(than.get("nguon") or "").strip() or None
    kiem_tra_pham_vi(request, "tim_kiem", module=mod_id)
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


@combo.get("/goi-y")
async def goi_y_combo(request: Request):
    """Gợi ý bước combo mặc định theo thứ tự chủ máy (21/10/2026): Codex trước → web sau (cùng nhóm).

    Dựa trên DỮ LIỆU THẬT: module AI đã liên kết có khả năng chat + model đầu tiên
    của mỗi module trong GET /v1/models. «Codex» đọc từ định danh do CHÍNH module
    khai (id chứa «codex») — không phải bảng tên nhà cung cấp; khi kho tài khoản
    có trường loại tài khoản tường minh thì chuyển sang dùng trường đó.
    """
    so = _so(request)
    ds = await _danh_sach_models(request)
    model_dau: dict[str, str] = {}
    for muc in ds:
        mid = muc.get("id", "")
        tt, g, _ = mid.partition("/")
        if g and tt not in model_dau:
            model_dau[tt] = mid
    ung_vien: list[dict] = []
    bo_qua: list[dict] = []
    for id_, mod in so.dang_chay.items():
        mf: dict = {}
        try:
            km = so.catalog.get(id_)
            if km is not None and getattr(km, "manifest", None):
                mf = dict(km.manifest)
        except Exception:
            mf = {}
        if not mf:
            mf = getattr(mod, "manifest", None) or {}
        if mf.get("loai") != "ai":
            continue
        if "chat" not in (mf.get("cung_cap") or []):
            bo_qua.append({"module_id": id_, "ly_do": "không có khả năng chat"})
            continue
        tien_to = mf.get("tien_to") or ""
        model = model_dau.get(tien_to)
        if not model:
            bo_qua.append({"module_id": id_, "ly_do": "không có model nào trong /v1/models"})
            continue
        nhom = mf.get("nhom") or {}
        ung_vien.append({
            "model": model,
            "module_id": id_,
            "ten_module": mf.get("ten") or id_,
            "nhom": nhom.get("id") or "",
            "ten_nhom": nhom.get("ten") or "",
            "la_codex": "codex" in id_.lower(),
        })
    # Cùng nhóm: Codex trước → web sau; các nhóm sắp theo id nhóm.
    ung_vien.sort(key=lambda u: (u["nhom"], 0 if u["la_codex"] else 1, u["ten_module"]))
    return {
        "goi_y": ung_vien,
        "bo_qua": bo_qua,
        "nguyen_tac": "Codex trước → web sau (cùng nhóm); nhóm theo nhom.id trong manifest; "
                      "«codex» đọc từ id do chính module khai",
    }


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
    except kho.KhoHong as e:
        raise LoiOpenAI(500, f"Không lưu được combo: {e} — khôi phục hoặc xoá tệp combo.json hỏng",
                        code="store_unreadable") from e
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
    """Kiểm KIỂU ở biên: trường vắng/null → None; có thì phải là chuỗi hợp lệ (không surrogate), sai → 400."""
    return kiem_bien.kiem_chuoi(than.get(ten), cho_phep_none=True, ten_truong=ten)


def _chuoi_bat_buoc(than: dict, ten: str) -> str:
    """Trường bắt buộc: phải là chuỗi không rỗng (sau khi bỏ khoảng trắng hai đầu), sai/thiếu → 400."""
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

    # 4b. Xác thực hai bước (tuỳ chọn, mặc định TẮT; người dùng tự bật ở Bảo mật). Mật khẩu đúng mà bật 2FA thì CHƯA cấp phiên
    # và CHƯA xoá bộ đếm sai: kẻ biết mật khẩu không được dùng bước này để xoá dấu vết dò. Cấp vé tạm 5 phút, dùng một lần.
    if xac_thuc_2fa_mod.da_bat_2fa(nd["id"]):
        ve = xac_thuc_2fa_mod.tao_ve_tam(nd["id"], request, muc_dich="dang_nhap_2fa")
        err = LoiOpenAI(
            401,
            "Cần mã xác thực hai bước (2FA).",
            loai="authentication_error",
            code="can_ma_2fa",
            headers={"Cache-Control": "no-store"},
        )
        err.than["error"]["ve"] = ve
        err.than["ve"] = ve
        raise err

    # 5. Thành công: reset bộ đếm sai theo cặp (IP, tên) và trừ tầng 2, tạo phiên mới
    so_that_bai = nguoi_dung_mod.ghi_nhan_dang_nhap_dung(ten_dang_nhap, ip=ip_khach)
    thiet_bi_moi = nguoi_dung_mod.kiem_va_ghi_nhan_thiet_bi(
        ten_dang_nhap, ip=ip_khach, ua=request.headers.get("user-agent", "")
    )
    if thiet_bi_moi:
        nhat_ky_mod.ghi(
            "quan_tri",
            "canh_bao",
            f"Đăng nhập từ thiết bị/IP chưa từng thấy: «{_nhac_lai(ten_dang_nhap)}»",
            ten_dang_nhap=_nhac_lai(ten_dang_nhap),
            ip=ip_khach,
        )
    if so_that_bai > 0:
        nhat_ky_mod.ghi(
            "quan_tri",
            "canh_bao",
            f"Đăng nhập thành công sau {so_that_bai} lần đăng nhập thất bại: «{_nhac_lai(ten_dang_nhap)}»",
            ten_dang_nhap=_nhac_lai(ten_dang_nhap),
            ip=ip_khach,
        )
    phien, token_tho = phien_web_mod.tao_phien(nd["id"])
    phien_web_mod.dat_cookie_phien(response, request, token_tho)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Đăng nhập web thành công: «{_nhac_lai(ten_dang_nhap)}»",
        ten_dang_nhap=_nhac_lai(ten_dang_nhap),
    )
    return {"ok": True, "csrf": phien["csrf"], "nguoi_dung": nguoi_dung_mod.ban_ghi_sach(nd)}


@dang_nhap.post("/2fa", dependencies=KHONG_LUU)
async def dang_nhap_xac_thuc_2fa(request: Request, response: Response):
    """Bước hai của đăng nhập web (đường mở CÓ VÉ: vé tạm sống 5 phút, dùng một lần, gắn IP; không lộ lý do sai)."""
    ip_khach = gioi_han_mod.lay_ip_an_toan(request)
    gioi_han_mod.kiem_tra_truoc_xac_thuc(request)

    than = await _than_json(request)
    ve = _chuoi_tuy_chon(than, "ve")
    ma = _chuoi_tuy_chon(than, "ma")
    if not ve or not ma:
        raise LoiOpenAI(401, "Vé xác thực không hợp lệ hoặc đã hết hạn.", code="ve_khong_hop_le")

    thong_tin_ve = xac_thuc_2fa_mod.xac_thuc_va_huy_ve_tam(ve, request, muc_dich="dang_nhap_2fa")
    if not thong_tin_ve:
        gioi_han_mod.ghi_loi_xac_thuc(request)
        raise LoiOpenAI(401, "Vé xác thực không hợp lệ hoặc đã hết hạn.", code="ve_khong_hop_le")

    nd = nguoi_dung_mod.tim_theo_id(thong_tin_ve["nguoi_dung_id"])
    if not nd or not nd.get("bat", True):
        raise LoiOpenAI(401, "Vé xác thực không hợp lệ hoặc đã hết hạn.", code="ve_khong_hop_le")
    if nd.get("vai") == "admin":
        ip_cho_phep_mod.kiem_tra_ip_quan_tri(request)  # cổng IP quản trị áp cả ở bước hai

    if not xac_thuc_2fa_mod.xac_thuc_ma(nd["id"], ma):
        nguoi_dung_mod.ghi_nhan_dang_nhap_sai(nd["ten_dang_nhap"], ip=ip_khach)
        gioi_han_mod.ghi_loi_xac_thuc(request)
        nhat_ky_mod.ghi(
            "quan_tri",
            "canh_bao",
            f"Đăng nhập thất bại: sai mã 2FA cho «{_nhac_lai(nd['ten_dang_nhap'])}»",
            ten_dang_nhap=_nhac_lai(nd["ten_dang_nhap"]),
        )
        raise LoiOpenAI(401, "Mã xác thực hai bước không chính xác.", code="invalid_totp")

    nguoi_dung_mod.ghi_nhan_dang_nhap_dung(nd["ten_dang_nhap"], ip=ip_khach)
    phien, token_tho = phien_web_mod.tao_phien(nd["id"])
    phien_web_mod.dat_cookie_phien(response, request, token_tho)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Đăng nhập 2FA thành công cho «{_nhac_lai(nd['ten_dang_nhap'])}»",
        ten_dang_nhap=_nhac_lai(nd["ten_dang_nhap"]),
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

    so = _so(request)
    tran = nguoi_dung_mod.tran_pham_vi(nd)  # admin → «*», user → pham_vi_toi_da
    # Không nêu trường → bằng đúng trần. Đã nêu (kể cả null) → danh sách KHÔNG rỗng gồm mã hợp lệ; rỗng KHÔNG là «tất cả».
    pv_yc = khoa_khach_mod.chuan_hoa_pham_vi(
        than["pham_vi"] if "pham_vi" in than else tran,
        cho_phep_rong=False,
        kiem_tra_module=True,
        so=so,
    )
    for s in pv_yc:
        if not khoa_khach_mod.pham_vi_nam_trong_tran(s, tran):
            raise LoiOpenAI(403, f"Không thể yêu cầu phạm vi vượt quá giới hạn (thiếu «{_nhac_lai(s)}»).",
                            loai="insufficient_scope", code="insufficient_scope")

    het_han = than.get("het_han")
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(nhan=nhan, pham_vi=pv_yc, chu_id=nd["id"], het_han=het_han, so=so)
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


@toi.post("/xac-nhan-lai", dependencies=KHONG_LUU)
async def xac_nhan_lai_toi(request: Request):
    """Xác nhận lại (step-up) cho phiên web quản trị để thực hiện thao tác nguy hiểm."""
    nd = getattr(request.state, "nguoi_dung", {})
    vai = nd.get("vai", "user")
    if vai != "admin":
        raise LoiOpenAI(
            403,
            "Chỉ quản trị viên mới có thể xác nhận lại quyền thao tác nguy hiểm.",
            loai="permission_error",
            code="forbidden",
        )
    phien = getattr(request.state, "phien_web", {})
    bam = phien.get("bam")
    if not bam:
        raise LoiOpenAI(401, "Cần phiên đăng nhập hợp lệ.", loai="invalid_request_error", code="unauthorized")

    than = await _than_json(request)
    mat_khau = _chuoi_tuy_chon(than, "mat_khau")
    khoa_qt = _chuoi_tuy_chon(than, "khoa_quan_tri")
    if not mat_khau and not khoa_qt:
        raise LoiOpenAI(400, "Cần cung cấp «mat_khau» hoặc «khoa_quan_tri» để xác nhận lại.", loai="invalid_request_error")

    ip_khach = gioi_han_mod.lay_ip_an_toan(request)
    hop_le = False
    if mat_khau:
        ten = str(nd.get("ten_dang_nhap", ""))
        try:
            nguoi_dung_mod.kiem_tra_khoa_ten(ten, ip=ip_khach)
        except LoiOpenAI:
            await nguoi_dung_mod.chay_bam_gia()
            raise LoiOpenAI(401, "Xác nhận lại thất bại: mật khẩu không chính xác.", loai="invalid_request_error", code="invalid_credentials") from None
        u = nguoi_dung_mod.tim_theo_id(nd["id"])
        if u:
            bam_cu = str(u.get("bam_mat_khau", ""))
            hop_le = await nguoi_dung_mod.kiem_tra_mat_khau(mat_khau, bam_cu)
        if not hop_le:
            nguoi_dung_mod.ghi_nhan_dang_nhap_sai(ten, ip=ip_khach)
        else:
            nguoi_dung_mod.ghi_nhan_dang_nhap_dung(ten, ip=ip_khach)
    elif khoa_qt:
        from app import xac_thuc
        hop_le = hmac.compare_digest(khoa_qt.encode("utf-8"), xac_thuc.khoa_quan_tri().encode("utf-8"))

    if not hop_le:
        gioi_han_mod.ghi_loi_xac_thuc(request)
        nhat_ky_mod.ghi(
            "quan_tri",
            "canh_bao",
            f"Xác nhận lại (step-up) thất bại cho quản trị viên «{nd.get('ten_dang_nhap', '')}»",
            id_nguoi_dung=nd.get("id"),
        )
        raise LoiOpenAI(401, "Thông tin xác nhận lại không chính xác.", loai="invalid_request_error", code="invalid_credentials")

    phien_web_mod.xac_nhan_lai_phien(bam)
    cua_so = phien_web_mod.stepup_cua_so_giay()
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Quản trị viên «{nd.get('ten_dang_nhap', '')}» xác nhận lại (step-up) thành công",
        id_nguoi_dung=nd.get("id"),
        cua_so_giay=cua_so,
    )
    return {"ok": True, "cua_so_giay": cua_so}


# ───────────── XÁC THỰC HAI BƯỚC (2FA) CỦA CHÍNH TÔI — tuỳ chọn, mặc định TẮT ─────────────

async def _nd_va_mat_khau_dung(request: Request, than: dict) -> dict:
    """Người dùng hiện tại đã nhập ĐÚNG mật khẩu (đòi lại mật khẩu cho thao tác nhạy cảm). Sai → đếm vào khoá đăng nhập."""
    nd = getattr(request.state, "nguoi_dung", {})
    mat_khau = _chuoi_tuy_chon(than, "mat_khau")
    if not mat_khau:
        raise LoiOpenAI(401, "Cần nhập mật khẩu để thực hiện thao tác 2FA.", code="invalid_credentials")
    u = nguoi_dung_mod.tim_theo_id(nd.get("id", "")) or {}
    if not await nguoi_dung_mod.kiem_tra_mat_khau(mat_khau, u.get("bam_mat_khau", "")):
        nguoi_dung_mod.ghi_nhan_dang_nhap_sai(nd.get("ten_dang_nhap", ""), ip=gioi_han_mod.lay_ip_an_toan(request))
        raise LoiOpenAI(401, "Mật khẩu không chính xác.", code="invalid_credentials")
    return nd


@toi.get("/2fa", dependencies=KHONG_LUU)
def lay_trang_thai_2fa_toi(request: Request):
    nd = getattr(request.state, "nguoi_dung", {})
    return JSONResponse(
        {"ok": True, "bat": xac_thuc_2fa_mod.da_bat_2fa(nd["id"]),
         "so_ma_khoi_phuc_con_lai": xac_thuc_2fa_mod.so_ma_khoi_phuc_con_lai(nd["id"])},
        headers={"Cache-Control": "no-store"},
    )


@toi.post("/2fa/bat-dau", dependencies=KHONG_LUU)
async def bat_dau_2fa_toi(request: Request):
    """Sinh bí mật TOTP chờ xác nhận (chưa bật). Cần mật khẩu. Bí mật chỉ hiện ở phản hồi này."""
    than = await _than_json(request)
    nd = await _nd_va_mat_khau_dung(request, than)
    bi_mat, uri = xac_thuc_2fa_mod.bat_dau_dang_ky(nd["id"], nd.get("ten_dang_nhap", ""))
    return JSONResponse({"ok": True, "bi_mat": bi_mat, "uri": uri}, headers={"Cache-Control": "no-store"})


@toi.post("/2fa/xac-nhan", dependencies=KHONG_LUU)
async def xac_nhan_2fa_toi(request: Request):
    """Nhập mã đầu tiên từ ứng dụng để BẬT 2FA; trả 10 mã khôi phục MỘT LẦN (chỉ lưu bản băm)."""
    nd = getattr(request.state, "nguoi_dung", {})
    than = await _than_json(request)
    ma = _chuoi_tuy_chon(than, "ma")
    if not ma:
        raise LoiOpenAI(400, "Cần nhập mã xác thực từ ứng dụng.", code="invalid_input")
    hop_le, ma_khoi_phuc = xac_thuc_2fa_mod.xac_nhan_dang_ky(nd["id"], ma)
    if not hop_le:
        raise LoiOpenAI(401, "Mã xác thực không chính xác.", code="invalid_totp")
    nhat_ky_mod.ghi("quan_tri", "thong_tin",
                    f"Người dùng «{_nhac_lai(nd.get('ten_dang_nhap', ''))}» đã bật 2FA", id_nguoi_dung=nd["id"])
    return JSONResponse({"ok": True, "ma_khoi_phuc": ma_khoi_phuc}, headers={"Cache-Control": "no-store"})


@toi.post("/2fa/tat", dependencies=KHONG_LUU)
async def tat_2fa_toi(request: Request):
    """Tắt 2FA của chính mình: cần mật khẩu VÀ một mã 2FA (hoặc mã khôi phục)."""
    than = await _than_json(request)
    nd = await _nd_va_mat_khau_dung(request, than)
    ma = _chuoi_tuy_chon(than, "ma")
    if not ma:
        raise LoiOpenAI(400, "Cần nhập mã 2FA (hoặc mã khôi phục) để tắt 2FA.", code="invalid_input")
    if not xac_thuc_2fa_mod.xac_thuc_ma(nd["id"], ma):
        raise LoiOpenAI(401, "Mã xác thực không chính xác.", code="invalid_totp")
    xac_thuc_2fa_mod.tat_2fa(nd["id"])
    nhat_ky_mod.ghi("quan_tri", "thong_tin",
                    f"Người dùng «{_nhac_lai(nd.get('ten_dang_nhap', ''))}» đã tắt 2FA", id_nguoi_dung=nd["id"])
    return {"ok": True}


@toi.post("/2fa/tao-lai-ma-khoi-phuc", dependencies=KHONG_LUU)
async def tao_lai_ma_khoi_phuc_toi(request: Request):
    than = await _than_json(request)
    nd = await _nd_va_mat_khau_dung(request, than)
    ma = _chuoi_tuy_chon(than, "ma")
    if not ma:
        raise LoiOpenAI(400, "Cần nhập mã 2FA để tạo lại mã khôi phục.", code="invalid_input")
    if not xac_thuc_2fa_mod.xac_thuc_ma(nd["id"], ma):
        raise LoiOpenAI(401, "Mã xác thực không chính xác.", code="invalid_totp")
    ma_khoi_phuc = xac_thuc_2fa_mod.tao_lai_ma_khoi_phuc(nd["id"])
    nhat_ky_mod.ghi("quan_tri", "thong_tin",
                    f"Người dùng «{_nhac_lai(nd.get('ten_dang_nhap', ''))}» đã tạo lại mã khôi phục 2FA",
                    id_nguoi_dung=nd["id"])
    return JSONResponse({"ok": True, "ma_khoi_phuc": ma_khoi_phuc}, headers={"Cache-Control": "no-store"})


# ───────────── QUẢN TRỊ NGƯỜI DÙNG (QT) ─────────────

@nguoi_dung.get("")
def lay_danh_sach_nguoi_dung():
    # `bat_2fa`: để quản trị biết ai đang dùng 2FA (chỉ cờ, không lộ bí mật hay mã khôi phục).
    ra = []
    for u in nguoi_dung_mod.danh_sach_nguoi_dung():
        try:
            bat_2fa = xac_thuc_2fa_mod.da_bat_2fa(str(u.get("id", "")))
        except xac_thuc_2fa_mod.LoiKho2FA:
            bat_2fa = True  # tệp 2FA hỏng: coi là ĐANG bật để quản trị còn thấy nút «Tắt 2FA» đặt lại
        ra.append({**u, "bat_2fa": bat_2fa})
    return {"nguoi_dung": ra}


@nguoi_dung.post("/{id}/tat-2fa", dependencies=KHONG_LUU)
def quan_tri_tat_2fa_nguoi_dung(id: str):
    """Quản trị tắt 2FA hộ người dùng mất điện thoại (kèm thu hồi mọi phiên của họ). Chỉ admin (router QT)."""
    nd = nguoi_dung_mod.tim_theo_id(id)
    if not nd:
        raise LoiOpenAI(404, "Không tìm thấy người dùng.", code="not_found")
    da_tat = xac_thuc_2fa_mod.dat_lai_2fa_cho_nguoi_dung(nd["ten_dang_nhap"])
    phien_web_mod.thu_hoi_phien_nguoi_dung(id)
    nhat_ky_mod.ghi("quan_tri", "canh_bao",
                    f"Quản trị tắt 2FA của «{_nhac_lai(nd['ten_dang_nhap'])}» (thu hồi mọi phiên)", id_nguoi_dung=id)
    return {"ok": True, "da_tat": bool(da_tat)}


@nguoi_dung.post("", dependencies=KHONG_LUU)
async def tao_nguoi_dung_moi(request: Request):
    than = await _than_json(request)
    vai = than.get("vai", "user")  # kiểu kiểm trong tao_nguoi_dung (kiem_vai), trần trong chuan_hoa_pham_vi
    if vai == "admin":
        kiem_tra_step_up(request)
    ten_dang_nhap = _chuoi_tuy_chon(than, "ten_dang_nhap")
    mat_khau = _chuoi_tuy_chon(than, "mat_khau")
    pham_vi_toi_da = than.get("pham_vi_toi_da")
    so = _so(request)

    ban_ghi, ten_chuan, mat_khau_tho = await nguoi_dung_mod.tao_nguoi_dung(
        vai=vai,
        ten_dang_nhap=ten_dang_nhap,
        mat_khau=mat_khau,
        pham_vi_toi_da=pham_vi_toi_da,
        so=so,
    )
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Quản trị viên tạo người dùng «{_nhac_lai(ten_chuan)}» (vai: {ban_ghi['vai']})",
        id_nguoi_dung=ban_ghi["id"],
        vai=ban_ghi["vai"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"nguoi_dung": ban_ghi, "ten_dang_nhap": ten_chuan, "mat_khau": mat_khau_tho}


@nguoi_dung.patch("/{id}")
async def cap_nhat_nguoi_dung(id: str, request: Request):
    than = await _than_json(request)
    vai = than.get("vai")
    bat = than.get("bat")
    if vai is not None or bat is not None:
        kiem_tra_step_up(request)
    phien_web_mod.kiem_tra_kho()  # thu hồi phiên khi tắt phải chạy được — kiểm TRƯỚC khi sửa bản ghi
    pham_vi_toi_da = than.get("pham_vi_toi_da")
    so = _so(request)

    ban_ghi = nguoi_dung_mod.sua_nguoi_dung(
        id_=id,
        vai=vai,
        bat=bat,
        pham_vi_toi_da=pham_vi_toi_da,
        so=so,
    )
    if not ban_ghi["bat"] or vai is not None:  # trạng thái sau thao tác là TẮT hoặc ĐỔI VAI → thu hồi mọi phiên
        phien_web_mod.thu_hoi_phien_nguoi_dung(id)

    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Cập nhật tài khoản người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
        bat=ban_ghi["bat"],
        vai=ban_ghi["vai"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"nguoi_dung": ban_ghi}


@nguoi_dung.post("/{id}/dat-lai-mat-khau", dependencies=KHONG_LUU)
async def dat_lai_mat_khau(id: str, request: Request):
    kiem_tra_step_up(request)
    phien_web_mod.kiem_tra_kho()  # không đặt mật khẩu mới rồi mới 503 (mật khẩu mất, không ai biết)
    ban_ghi, mat_khau_moi = await nguoi_dung_mod.dat_lai_mat_khau_nguoi_dung(id)
    phien_web_mod.thu_hoi_phien_nguoi_dung(id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Đặt lại mật khẩu cho người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"nguoi_dung": ban_ghi, "mat_khau_moi": mat_khau_moi}


@nguoi_dung.delete("/{id}")
def xoa_tai_khoan(id: str, request: Request):
    phien_web_mod.kiem_tra_kho()  # kiểm trước khi xoá: không để xoá xong người dùng rồi 503 bỏ dở khoá/phiên
    khoa_khach_mod.kiem_tra_kho()  # cùng lý do: kho khoá hỏng → 503 TRƯỚC khi xoá người dùng
    nguoi_dung_mod.xoa_nguoi_dung(id)
    khoa_khach_mod.xoa_khoa_theo_chu(id)
    phien_web_mod.thu_hoi_phien_nguoi_dung(id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Xoá tài khoản người dùng (id: {_nhac_lai(id)})",
        id_nguoi_dung=id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"ok": True}


# ───────────── DANH TÍNH (QT) ─────────────

@danh_tinh.get("")
def lay_danh_sach_danh_tinh():
    return {"danh_tinh": danh_tinh_mod.danh_sach()}


@danh_tinh.post("", status_code=201)
async def tao_danh_tinh_moi(request: Request):
    than = await _than_json(request)
    email = _chuoi_tuy_chon(than, "email")
    nhan = _chuoi_tuy_chon(than, "nhan")
    if email is None or not email.strip():
        raise LoiOpenAI(400, "Thiếu trường «email».", code="invalid_value")
    ban_ghi = danh_tinh_mod.tao(email, nhan)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Tạo danh tính «{_nhac_lai(ban_ghi['email'])}»",
        id_danh_tinh=ban_ghi["id"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"danh_tinh": ban_ghi}


@danh_tinh.get("/chua-gan")
def lay_danh_tinh_chua_gan(module_id: str, request: Request):
    """Danh tính chưa gắn vào module này — giao diện hiện «email có sẵn» để
    tái dùng thay vì nhập lại email. (NV91-P1)"""
    module_id = (module_id or "").strip()
    danh_tinh_mod.kiem_module_id(module_id)
    if request.app.state.so.tien_to_cua(module_id) is None:
        raise LoiOpenAI(404, f"Module «{_nhac_lai(module_id)}» chưa được cài.", code="module_chua_cai")
    return {"module_id": module_id, "danh_tinh": danh_tinh_mod.chua_gan(module_id)}


@danh_tinh.post("/tao-va-gan")
async def tao_va_gan_danh_tinh(request: Request):
    """Tạo-nếu-chưa-có + gắn vào module (idempotent: email đã có thì chỉ gắn,
    không trùng). 201 khi tạo mới, 200 khi gắn vào danh tính đã có. (NV91-P1)"""
    than = await _than_json(request)
    email = _chuoi_bat_buoc(than, "email")
    nhan = _chuoi_tuy_chon(than, "nhan")
    module_id = _chuoi_bat_buoc(than, "module_id")
    danh_tinh_mod.kiem_module_id(module_id)
    if request.app.state.so.tien_to_cua(module_id) is None:
        raise LoiOpenAI(404, f"Module «{_nhac_lai(module_id)}» chưa được cài.", code="module_chua_cai")
    ban_ghi, da_co_san = danh_tinh_mod.tao_va_gan(email, nhan, module_id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"{'Gắn' if da_co_san else 'Tạo và gắn'} danh tính «{_nhac_lai(ban_ghi['email'])}» "
        f"vào module «{_nhac_lai(module_id)}»",
        id_danh_tinh=ban_ghi["id"],
        module=module_id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return JSONResponse({"danh_tinh": ban_ghi, "da_co_san": da_co_san},
                        status_code=200 if da_co_san else 201)


@danh_tinh.post("/go-theo-module")
async def go_danh_tinh_theo_module(request: Request):
    """Gỡ module khỏi mọi danh tính (dọn «gắn» mồ côi khi gỡ module/tài khoản). (NV91-P1)"""
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    dem = danh_tinh_mod.go_theo_module(module_id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Gỡ module «{_nhac_lai(module_id)}» khỏi {dem} danh tính",
        module=module_id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"ok": True, "da_go": dem}


async def _kiem_tin_hieu_dang_nhap(request_hoac_app: Any, ho_so: str
                                   ) -> tuple[bool, str]:
    """Kiểm TÍN HIỆU phiên của một hồ sơ trình duyệt bằng QUY_TAC_TIN_HIEU của
    module có khả năng «dang_nhap_danh_tinh» (NV155c).

    NV155c-SUA (A1): đường cũ gọi `tin_hieu()` thẳng trên bộ điều khiển — với
    dịch vụ trình duyệt THẬT, hồ sơ ĐÃ ĐÓNG trả 404 và hồ sơ mở chưa giành trả
    409 nên «dang_nhap» không bao giờ đạt được. Nay dùng bộ kiểm chung của lõi
    `khoi_phuc_phien.kiem_da_dang_nhap` (ưu tiên đọc kho cookie của hồ sơ đã
    đóng qua API hồ sơ của dịch vụ; dự phòng mở tạm có kiểm soát) — NV179 dùng
    lại helper này. Trả (ket_qua, nguon).

    Trả (False, …) khi không kiểm được (thiếu module/bộ điều khiển, dịch vụ
    lỗi) — đóng cửa: trạng thái mặc định luôn là chua_dang_nhap.
    """
    mod_dn = _module_dang_nhap_danh_tinh(request_hoac_app, bat_buoc=False)
    if mod_dn is None:
        return False, "khong_kiem_duoc"
    lay_quy_tac = getattr(mod_dn, "quy_tac_tin_hieu", None)
    if not callable(lay_quy_tac):
        return False, "khong_kiem_duoc"
    try:
        quy_tac = lay_quy_tac() or []
    except Exception as e:
        log.warning("nhan-ho-so: quy_tac_tin_hieu lỗi: %s", type(e).__name__)
        return False, "khong_kiem_duoc"
    try:
        td = _dieu_khien_that(request_hoac_app, ho_so)
    except LoiOpenAI:
        return False, "khong_kiem_duoc"
    try:
        return await khoi_phuc_phien_mod.kiem_da_dang_nhap(td, list(quy_tac))
    finally:
        await _dong_trinh_duyet_that(td)


@danh_tinh.post("/nhan-ho-so")
async def nhan_workspace_lam_tai_khoan(request: Request):
    """«Nhận workspace làm tài khoản» (NV155c): gắn hồ sơ trình duyệt ĐÃ ĐÓNG
    (workspace) vào danh tính của email, để tài khoản đã đăng nhập sẵn hiện ở
    trang AI («Thêm tài khoản → Qua danh tính»).

    Chỉ quản trị (phụ thuộc QT của router: không khoá → 401, khoá khách/vai
    thấp → 403). Hồ sơ phải có thật ở dịch vụ trình duyệt và ĐÃ ĐÓNG (đang mở
    → 409, giữ cơ chế «Đóng · giữ phiên»); đã gắn danh tính khác → 409 (chống
    cướp hồ sơ/IDOR). Trạng thái «dang_nhap» CHỈ khi kiểm được tín hiệu phiên
    qua quy tắc của module đăng nhập danh tính; không kiểm được →
    «chua_dang_nhap». Không bí mật vào phản hồi/nhật ký; lỗi không nêu email.
    """
    than = await _than_json(request)
    ho_so = _chuoi_bat_buoc(than, "ho_so")
    email = _chuoi_bat_buoc(than, "email")
    nhan = _chuoi_tuy_chon(than, "nhan")
    if not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
        raise LoiOpenAI(400, "«ho_so» không hợp lệ.", code="invalid_value")
    email_chuan = danh_tinh_mod.chuan_hoa_email(email)
    nhan_chuan = danh_tinh_mod.kiem_nhan(nhan)
    mod_td = _doi_module_trinh_duyet(request)
    try:
        ds_ws = await mod_td.trinh_duyet_ds()
    except Exception as e:
        log.warning("nhan-ho-so: trinh_duyet_ds lỗi: %s", type(e).__name__)
        raise LoiOpenAI(502, "Không đọc được danh sách hồ sơ ở dịch vụ trình duyệt.",
                        code="loi_dich_vu_trinh_duyet") from None
    ws = next((w for w in (ds_ws or [])
               if isinstance(w, dict) and w.get("ho_so") == ho_so), None)
    if ws is None:
        raise LoiOpenAI(404, "Hồ sơ trình duyệt này không có ở dịch vụ trình duyệt.",
                        code="ho_so_khong_co")
    if ws.get("dang_mo"):
        raise LoiOpenAI(409, "Hồ sơ trình duyệt đang mở. Đóng trình duyệt "
                             "(giữ phiên) rồi nhận lại.", code="ho_so_dang_mo")
    da_dang_nhap, nguon_kiem = await _kiem_tin_hieu_dang_nhap(request, ho_so)
    trang_thai_kiem = "dang_nhap" if da_dang_nhap else "chua_dang_nhap"
    ban_ghi, da_co_san = danh_tinh_mod.nhan_ho_so(
        ho_so, email_chuan, nhan_chuan, trang_thai_kiem)
    # NV155c-SUA (A2): phản hồi/nhật ký dùng trạng thái ĐÃ LƯU của bản ghi
    # (nhan_ho_so chỉ nâng, không hạ) — không dùng trị vừa tính lần này.
    trang_thai = ban_ghi["trang_thai_dang_nhap"]
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Nhận workspace làm tài khoản (nhan-ho-so): danh tính "
        f"«{_nhac_lai(ban_ghi['email'])}», trạng thái {trang_thai}",
        id_danh_tinh=ban_ghi["id"], ho_so=ho_so,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return JSONResponse({
        "danh_tinh": ban_ghi,
        "ho_so": ho_so,
        "trang_thai_dang_nhap": trang_thai,
        # NV155c-SUA (A1): nói rõ ĐÃ kiểm thế nào — "cookie_ho_so" (kho cookie
        # của hồ sơ đã đóng), "phien_truc_tiep" (phiên sống), "khong_kiem_duoc".
        "kiem_tin_hieu": nguon_kiem,
        "da_co_san": da_co_san,
        "buoc_tiep_theo": "Vào Hệ thống → AI → Thêm tài khoản → Qua danh tính "
                          "để dùng tài khoản này.",
    }, status_code=200 if da_co_san else 201)


@danh_tinh.get("/{id}")
def lay_mot_danh_tinh(id: str):
    ban_ghi = danh_tinh_mod.lay(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy danh tính.", code="danh_tinh_khong_co")
    return {"danh_tinh": ban_ghi}


@danh_tinh.delete("/{id}")
async def xoa_danh_tinh(id: str, request: Request):
    ban_ghi = danh_tinh_mod.lay(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy danh tính.", code="danh_tinh_khong_co")
    ho_so = ban_ghi["ho_so_trinh_duyet"]
    # NV155c-SUA (A3): hồ sơ gắn qua đường «nhận» (ho_so_do_nhan) là workspace
    # CÓ SẴN của người dùng (có thể chứa nhiều phiên) — xoá danh tính chỉ GỠ
    # GẮN: đóng trình duyệt nhưng GIỮ thư mục + phiên (giu_phien=True), không
    # xoá hẳn như hồ sơ do luồng /dang-nhap tự tạo.
    giu_phien = bool(ban_ghi.get("ho_so_do_nhan"))
    # Xoá bản ghi TRƯỚC: còn tài khoản provider trỏ tới thì xoa() ném 409 và
    # hồ sơ trình duyệt (phiên đăng nhập) còn nguyên; đóng hồ sơ sau khi xoá thành công.
    danh_tinh_mod.xoa(id)
    if ho_so:
        # Lỗi đóng hồ sơ chỉ ghi log, không làm hỏng việc xoá đã xong.
        mod_td = _tim_module_trinh_duyet(request)
        if mod_td is not None:
            try:
                await mod_td.trinh_duyet_dong(ho_so, giu_phien=giu_phien)
            except Exception as e:
                log.warning("danh-tinh: đóng hồ sơ trình duyệt %s lỗi: %s",
                            ho_so, type(e).__name__)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Xoá danh tính (id: {_nhac_lai(id)})",
        id_danh_tinh=id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    ket_qua = {"ok": True}
    if ho_so and giu_phien:
        ket_qua["canh_bao"] = (
            "Đã xoá danh tính (chỉ gỡ gắn). Hồ sơ trình duyệt được GIỮ NGUYÊN "
            "cả thư mục lẫn phiên đăng nhập vì workspace này có sẵn từ trước "
            "khi «nhận» — kiểm tra trang Workspace nếu muốn tự xoá.")
    elif ho_so:
        # Cảnh báo: mọi tài khoản dùng chung hồ sơ trình duyệt này coi như
        # đã đăng xuất — kiểm tra lại từng tài khoản.
        ket_qua["canh_bao"] = (
            "Đã xoá danh tính và đóng hồ sơ trình duyệt. Mọi tài khoản dùng chung "
            "hồ sơ này coi như đã đăng xuất — kiểm tra lại từng tài khoản.")
    return ket_qua


@danh_tinh.post("/{id}/gan")
async def gan_danh_tinh_vao_provider(id: str, request: Request):
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    danh_tinh_mod.kiem_module_id(module_id)
    if request.app.state.so.tien_to_cua(module_id) is None:
        raise LoiOpenAI(404, f"Module «{_nhac_lai(module_id)}» chưa được cài.", code="module_chua_cai")
    ban_ghi = danh_tinh_mod.gan(id, module_id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Gắn danh tính (id: {_nhac_lai(id)}) vào module «{_nhac_lai(module_id)}»",
        id_danh_tinh=id,
        module=module_id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"danh_tinh": ban_ghi}


@danh_tinh.post("/{id}/go")
async def go_danh_tinh_khoi_provider(id: str, request: Request):
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    ban_ghi = danh_tinh_mod.go(id, module_id)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Gỡ danh tính (id: {_nhac_lai(id)}) khỏi module «{_nhac_lai(module_id)}»",
        id_danh_tinh=id,
        module=module_id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"danh_tinh": ban_ghi}


# ───────────── ĐĂNG NHẬP DANH TÍNH QUA TRÌNH DUYỆT (QT) — NV92 P2 ─────────────
#
# Một «danh tính» có thể gắn một hồ sơ trình duyệt bền: đăng nhập một lần ở đây,
# các module provider tái dùng hồ sơ đó (POST /api/tai-khoan/tai-dung) mà không
# nhập lại bí mật. Thủ tục đăng nhập nằm trong module có khả năng
# «dang_nhap_danh_tinh» — lõi KHÔNG gọi tên module nào. Gặp CAPTCHA/xác minh
# thiết bị → «cần người» + mở trình duyệt, KHÔNG tự giải. Mật khẩu/mã 2FA không
# bao giờ vào phản hồi/log/nhật ký.
#
# LUẬT KIẾN TRÚC (bắt buộc, có test bảo vệ): lõi không được nhắc tên nhà cung
# cấp hay module cụ thể nào — kể cả trong chú thích. Mọi đặc thù nhà cung cấp
# nằm trong module.

#: Khả năng của module đảm nhận thủ tục đăng nhập danh tính qua trình duyệt.
KHA_NANG_DANG_NHAP_DANH_TINH = "dang_nhap_danh_tinh"

#: Khuôn mã hồ sơ trình duyệt do dịch vụ trình duyệt cấp.
#: Khuôn mã hồ sơ trình duyệt: dùng chung app/danh_tinh.MAU_HO_SO_TRINH_DUYET
#: (NV94-d: từng định nghĩa riêng, lệch với danh_tinh).
MAU_HO_SO_TRINH_DUYET = danh_tinh_mod.MAU_HO_SO_TRINH_DUYET


def _lay_state_app(request_hoac_app: Any):
    if hasattr(request_hoac_app, "app"):
        return getattr(request_hoac_app.app, "state", None)
    return getattr(request_hoac_app, "state", None)


def _module_dang_nhap_danh_tinh(request_hoac_app: Any, bat_buoc: bool = True):
    """Thể hiện module có khả năng «dang_nhap_danh_tinh» đang chạy.

    Tìm theo KHẢ NĂNG, không theo tên module. `bat_buoc=False` → trả None thay
    vì 501 (dùng khi tiến độ chỉ là thông tin bổ sung).
    """
    state = _lay_state_app(request_hoac_app)
    so = getattr(state, "so", None)
    for mod in ((getattr(so, "dang_chay", None) or {})).values():
        cung_cap = (getattr(mod, "manifest", None) or {}).get("cung_cap") or []
        if KHA_NANG_DANG_NHAP_DANH_TINH in cung_cap:
            return mod
    if bat_buoc:
        raise LoiOpenAI(501, "Chưa có module đăng nhập danh tính qua trình duyệt.",
                        code="module_chua_cai")
    return None


def _tim_module_trinh_duyet(request_hoac_app: Any):
    """Module đã cài có khả năng «trinh_duyet» — KHÔNG gọi tên module nào.

    Tên phương thức phải khớp hợp đồng lõi dùng
    (app/khoi_phuc_phien.lay_trinh_duyet_that gọi `trinh_duyet_that(ho_so)`)."""
    state = _lay_state_app(request_hoac_app)
    so = getattr(state, "so", None)
    for mod in ((getattr(so, "dang_chay", None) or {})).values():
        cung_cap = (getattr(mod, "manifest", None) or {}).get("cung_cap") or []
        if ("trinh_duyet" in cung_cap
                and callable(getattr(mod, "trinh_duyet_that", None))):
            return mod
    return None


def _doi_module_trinh_duyet(request_hoac_app: Any):
    mod = _tim_module_trinh_duyet(request_hoac_app)
    if mod is None:
        raise LoiOpenAI(503, "Chưa có dịch vụ trình duyệt "
                             "(cần module có khả năng «trinh_duyet»).",
                        code="chua_co_trinh_duyet")
    return mod


async def _dong_trinh_duyet_that(td: Any) -> None:
    """Đóng bộ điều khiển trình duyệt thật (NV94-P3): nhả quyền (luỹ đẳng)
    + đóng httpx.Client. Không ném — đường gọi đã có xử lý lỗi riêng."""
    dong = getattr(td, "dong", None)
    if callable(dong):
        try:
            await dong()
        except Exception as e:
            log.warning("trinh-duyet-that: dong() lỗi: %s", type(e).__name__)


def _dieu_khien_that(request_hoac_app: Any, ho_so: str):
    """Bộ điều khiển trình duyệt thật qua CẦU NỐI LÕI (NV92 P1):
    lõi tìm module có khả năng «trinh_duyet» rồi gọi `trinh_duyet_that(ho_so)`
    của nó — KHÔNG gọi tên module nào. Không lấy được → 503 (đóng cửa)."""
    state = _lay_state_app(request_hoac_app)
    so = getattr(state, "so", None)
    dang_chay = (so is not None and getattr(so, "dang_chay", None)) or {}
    td = khoi_phuc_phien_mod.lay_trinh_duyet_that(ho_so, dang_chay)
    if td is None:
        raise LoiOpenAI(503, "Không lấy được bộ điều khiển trình duyệt thật.",
                        code="chua_co_trinh_duyet_that")
    return td


def _danh_tinh_theo_ho_so(ho_so: str) -> dict:
    ban_ghi = danh_tinh_mod.tim_theo_ho_so(ho_so)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không có danh tính nào gắn với hồ sơ trình duyệt này.",
                        code="ho_so_khong_co")
    return ban_ghi


async def _don_danh_tinh_do_dang(request_hoac_app: Any, mod_td, dt_id: str,
                                 ho_so: str | None) -> None:
    """Dọn danh tính (và hồ sơ trình duyệt nếu đã tạo) khi /dang-nhap hỏng trước
    lúc lượt chạy bắt đầu — thử lại cùng email không bị 400 «email_ton_tai»."""
    try:
        danh_tinh_mod.xoa(dt_id)
    except Exception as e:
        log.warning("danh-tinh: dọn bản ghi dở %s lỗi: %s", dt_id, type(e).__name__)
    if ho_so:
        try:
            await mod_td.trinh_duyet_dong(ho_so, giu_phien=False)
        except Exception as e:
            log.warning("danh-tinh: đóng hồ sơ dở %s lỗi: %s", ho_so, type(e).__name__)


@danh_tinh.post("/dang-nhap", status_code=201)
async def dang_nhap_danh_tinh_qua_trinh_duyet(request: Request):
    """Tạo danh tính + tự đăng nhập trong hồ sơ trình duyệt (chạy nền).

    Thân: {"email" (bắt buộc), "mat_khau"? (không có → chỉ kiểm «đi tắt», chưa
    đăng nhập thì «cần người»), "ma_2fa"? (mã 2FA có sẵn, tuỳ chọn), "nhan"?}.
    Chạy lần lượt qua LuotChay (giãn cách, dừng được); đang có lượt chạy → 409.
    """
    than = await _than_json(request)
    email = _chuoi_bat_buoc(than, "email")
    mat_khau = _chuoi_tuy_chon(than, "mat_khau")
    ma_2fa = _chuoi_tuy_chon(than, "ma_2fa")
    nhan = _chuoi_tuy_chon(than, "nhan")
    email_chuan = danh_tinh_mod.chuan_hoa_email(email)
    mod_dn = _module_dang_nhap_danh_tinh(request)
    mod_td = _doi_module_trinh_duyet(request)
    # Tạo danh tính trước để kiểm trùng email; rồi mới tạo hồ sơ trình duyệt.
    ban_ghi = danh_tinh_mod.tao(email_chuan, nhan)
    dt_id = ban_ghi["id"]
    try:
        ws = await mod_td.trinh_duyet_tao(ten=email_chuan)
    except Exception as e:
        log.warning("danh-tinh: tạo hồ sơ trình duyệt cho %s lỗi: %s",
                    dt_id, type(e).__name__)
        await _don_danh_tinh_do_dang(request, mod_td, dt_id, None)
        raise LoiOpenAI(502, f"Không tạo được hồ sơ trình duyệt ({type(e).__name__}).",
                        code="loi_dich_vu_trinh_duyet") from None
    ho_so = ws.get("ho_so") if isinstance(ws, dict) else None
    if not isinstance(ho_so, str) or not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
        await _don_danh_tinh_do_dang(request, mod_td, dt_id, None)
        raise LoiOpenAI(502, "Dịch vụ trình duyệt trả mã hồ sơ không hợp lệ.",
                        code="loi_dich_vu_trinh_duyet")
    try:
        danh_tinh_mod.cap_nhat_trang_thai(dt_id, ho_so=ho_so)
        td = _dieu_khien_that(request, ho_so)
    except Exception:
        await _don_danh_tinh_do_dang(request, mod_td, dt_id, ho_so)
        raise

    tk = tai_khoan_hang_loat_mod.DongDaTach(
        dong=1, email=email_chuan, loai="mat_khau",
        mat_khau=tai_khoan_hang_loat_mod.BiMatTam(mat_khau) if mat_khau else None,
        totp=tai_khoan_hang_loat_mod.BiMatTam(ma_2fa) if ma_2fa else None)

    async def ham_dang_nhap(dong):
        # Giành quyền điều khiển máy cho đúng lượt này rồi NHẢ LUÔN
        # (kể cả khi lỗi) — hồ sơ không kẹt «đang bận».
        # NV94-P3: xong thì DONG (đóng httpx.Client của TrinhDuyetThat).
        try:
            async with khoi_phuc_phien_mod.phien_dieu_khien_that(td):
                kq = await mod_dn.dang_nhap(
                    ho_so, dong.email, dong.mat_khau, td, ma_2fa=dong.totp)
        finally:
            await _dong_trinh_duyet_that(td)
        try:
            if kq.thanh_cong:
                danh_tinh_mod.cap_nhat_trang_thai(dt_id, trang_thai="dang_nhap")
            elif kq.can_nguoi:
                danh_tinh_mod.cap_nhat_trang_thai(dt_id, trang_thai="can_nguoi")
        except Exception as e:
            log.warning("danh-tinh: cập nhật trạng thái %s lỗi: %s",
                        dt_id, type(e).__name__)
        return tai_khoan_hang_loat_mod.KetQuaDangNhap(
            kq.thanh_cong, ma_loi=kq.ma_loi, ma_trang_thai=kq.ma_trang_thai)

    quan_ly = request.app.state.quan_ly_luot_chay
    try:
        thong_tin = quan_ly.bat_dau([tk], ham_dang_nhap, "dang-nhap-danh-tinh")
    except tai_khoan_hang_loat_mod.DaCoLuotChay:
        tk.xoa_bi_mat()
        await _don_danh_tinh_do_dang(request, mod_td, dt_id, ho_so)
        raise LoiOpenAI(409, "Đã có một lượt đăng nhập đang chạy.",
                        code="da_co_luot_chay") from None
    mat_khau = None
    ma_2fa = None  # noqa: F841 — không giữ chuỗi thô
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Tạo danh tính «{_nhac_lai(email_chuan)}» + tự đăng nhập trong hồ sơ trình duyệt",
        id_danh_tinh=dt_id, nguoi_thuc_hien=nguoi_thuc_hien)
    return {"luot": thong_tin, "danh_tinh": danh_tinh_mod.lay(dt_id),
            "ho_so": ho_so}


@danh_tinh.get("/{id}/trang-thai")
def trang_thai_dang_nhap_danh_tinh(id: str, request: Request):
    """Trạng thái danh tính: bản ghi (trạng thái đăng nhập, hồ sơ trình duyệt,
    module đã gắn) + tiến độ đăng nhập (không bí mật)."""
    ban_ghi = danh_tinh_mod.lay(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy danh tính.",
                        code="danh_tinh_khong_co")
    tien_do: dict = {}
    mod = _module_dang_nhap_danh_tinh(request, bat_buoc=False)
    if mod is not None and callable(getattr(mod, "tien_do", None)):
        try:
            tien_do = mod.tien_do(ban_ghi["ho_so_trinh_duyet"]) or {}
        except Exception as e:
            log.warning("danh-tinh: đọc tiến độ %s lỗi: %s",
                        id, type(e).__name__)
    return {"danh_tinh": ban_ghi, "tien_do": tien_do}


@danh_tinh.post("/{id}/kiem-lai")
async def kiem_lai_dang_nhap_danh_tinh(id: str, request: Request):
    """Kiểm lại tín hiệu đăng nhập của hồ sơ đã gắn (NV179): dùng QUY TẮC TÍN HIỆU
    của module «dang_nhap_danh_tinh» (cùng cơ chế NV155c `nhan-ho-so`) — không tự
    dò. Chỉ NÂNG trạng thái lên «dang_nhap» khi kiểm được tín hiệu; kiểm không
    được (hết hạn, workspace hỏng, dịch vụ chết) thì GIỮ NGUYÊN trạng thái cũ,
    không đoán. Chưa gắn hồ sơ → 400 `chua_co_ho_so`; không có danh tính → 404."""
    ban_ghi = danh_tinh_mod.lay(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy danh tính.",
                        code="danh_tinh_khong_co")
    ho_so = ban_ghi["ho_so_trinh_duyet"]
    if not ho_so:
        raise LoiOpenAI(400, "Danh tính chưa gắn hồ sơ trình duyệt.",
                        code="chua_co_ho_so")
    try:
        da, _nguon = await _kiem_tin_hieu_dang_nhap(request, ho_so)  # (kết quả, nguồn) — NV155c-SUA
    except Exception as e:
        log.warning("danh-tinh: kiểm lại đăng nhập %s lỗi: %s",
                    id, type(e).__name__)
        da = False
    if da:
        try:
            ban_ghi = danh_tinh_mod.cap_nhat_trang_thai(id, trang_thai="dang_nhap")
        except Exception as e:
            log.warning("danh-tinh: cập nhật trạng thái %s lỗi: %s",
                        id, type(e).__name__)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Kiểm lại đăng nhập danh tính (id: {_nhac_lai(id)}): "
        f"{'đã đăng nhập' if da else 'không kiểm được tín hiệu — giữ nguyên trạng thái'}",
        id_danh_tinh=id, nguoi_thuc_hien=nguoi_thuc_hien)
    return {"danh_tinh": ban_ghi,
            "da_dang_nhap": bool(da),
            "trang_thai_dang_nhap": ban_ghi["trang_thai_dang_nhap"]}


@danh_tinh.post("/{id}/ma-2fa")
async def gui_ma_2fa_danh_tinh(id: str, request: Request):
    """Gửi mã 2FA khi danh tính đang ở trạng thái chờ («cho_2fa»)."""
    than = await _than_json(request)
    ma = _chuoi_bat_buoc(than, "ma")
    ban_ghi = danh_tinh_mod.lay(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy danh tính.",
                        code="danh_tinh_khong_co")
    ho_so = ban_ghi["ho_so_trinh_duyet"]
    if not ho_so:
        raise LoiOpenAI(400, "Danh tính chưa gắn hồ sơ trình duyệt.",
                        code="chua_co_ho_so")
    mod_dn = _module_dang_nhap_danh_tinh(request)
    td = _dieu_khien_that(request, ho_so)
    bm = tai_khoan_hang_loat_mod.BiMatTam(ma)
    ma = None  # noqa: F841 — không giữ chuỗi thô
    try:
        # NV94-P3: xong thì DONG (đóng httpx.Client của TrinhDuyetThat).
        try:
            async with khoi_phuc_phien_mod.phien_dieu_khien_that(td):
                kq = await mod_dn.nhap_ma_2fa(ho_so, bm, td)
        finally:
            await _dong_trinh_duyet_that(td)
    except Exception as e:
        # Giành quyền lỗi → nhap_ma_2fa chưa chạy → tự xoá mã ở đây
        # (xoa() luỹ đẳng; các nhánh trong nhap_ma_2fa đã tự xoá).
        bm.xoa()
        if getattr(e, "ma", None) == 409:
            raise LoiOpenAI(409, "Hồ sơ trình duyệt đang bận (có người hoặc "
                                 "máy khác đang điều khiển). Thử lại sau.",
                            code="ho_so_dang_ban") from None
        log.warning("danh-tinh: gửi mã 2FA %s lỗi: %s",
                    id, type(e).__name__)
        raise LoiOpenAI(502, f"Lỗi khi gửi mã 2FA ({type(e).__name__}).",
                        code="loi_module") from None
    try:
        if kq.thanh_cong:
            danh_tinh_mod.cap_nhat_trang_thai(id, trang_thai="dang_nhap")
        elif kq.can_nguoi:
            danh_tinh_mod.cap_nhat_trang_thai(id, trang_thai="can_nguoi")
    except Exception as e:
        log.warning("danh-tinh: cập nhật trạng thái %s lỗi: %s",
                    id, type(e).__name__)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Gửi mã 2FA cho danh tính (id: {_nhac_lai(id)}): "
        f"{'xong' if kq.thanh_cong else kq.ma_loi}",
        id_danh_tinh=id, nguoi_thuc_hien=nguoi_thuc_hien)
    return kq.sang_dict()



# ───────────── TÀI KHOẢN HÀNG LOẠT (QT) — NV67 nhóm 3, việc 7 ─────────────
#
# Dán hàng loạt + đăng nhập lần lượt (chỉ phần máy chủ, chưa có giao diện web).
# Bí mật thô (mật khẩu/TOTP/cookie) KHÔNG BAO GIỜ xuất hiện trong phản hồi, log
# hay nhật ký: `dan-hang-loat` chỉ trả dong/email/loai; lượt chạy xoá bí mật
# ngay sau khi từng tài khoản đăng nhập xong (xem app/tai_khoan_hang_loat.py).

def _kiem_module_da_cai_tai_khoan(request: Request, module_id: str) -> None:
    """module_id phải là module đã cài; chưa cài → 404 module_chua_cai."""
    if request.app.state.so.tien_to_cua(module_id) is None:
        raise LoiOpenAI(404, f"Module «{_nhac_lai(module_id)}» chưa được cài.", code="module_chua_cai")


def _ham_dang_nhap_cua_module(request: Request, module_id: str):
    """Hàm `dang_nhap` theo hợp đồng KetQuaDangNhap của module; None nếu module
    chưa triển khai (dòng đó sẽ bo_qua, không phải lỗi)."""
    mod = request.app.state.so.dang_chay.get(module_id)
    ham = getattr(mod, "dang_nhap", None) if mod is not None else None
    return ham if callable(ham) else None


# ───────────── TÀI KHOẢN ĐÃ LƯU (NV144, QT) ─────────────
# Kho tài khoản đã lưu: email + nhãn + mật khẩu + TOTP secret (tuỳ chọn).


def _nhom_cua_module(request: Request, module_id: str) -> str:
    """Nhóm của module theo manifest (NV197c): ``manifest.nhom.id``.

    Thiếu → chính ``module_id`` (vẫn suy từ module, không đoán từ email/token).
    """
    try:
        km = request.app.state.so.catalog.get(module_id)
        man = km.manifest if km is not None else {}
        nhom = str((man.get("nhom") or {}).get("id") or "").strip()
    except Exception:
        nhom = ""
    return nhom or module_id
# Mọi đường đều cần khoá quản trị (QT). Bí mật KHÔNG BAO GIỜ rời server qua
# các đường này — giao diện/trình duyệt không nhận lại mật khẩu.

def _loi_kho_tai_khoan(e: kho_tai_khoan_mod.LoiKhoTaiKhoan) -> LoiOpenAI:
    ma = e.ma
    if ma == "kho_hong":
        return LoiOpenAI(
            500, "Kho tài khoản đã lưu bị hỏng; đã đóng cửa, không lưu một phần.",
            code="kho_hong")
    if ma == "invalid_email":
        return LoiOpenAI(400, "Địa chỉ email không hợp lệ.", code="invalid_email")
    if ma == "khong_tim_thay":
        return LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.", code="khong_tim_thay")
    return LoiOpenAI(400, "Giá trị không hợp lệ.", code="invalid_value")


@tai_khoan.get("/da-luu")
def liet_ke_tai_khoan_da_luu(nhom: str | None = None):
    """Liệt kê tài khoản đã lưu: email CHE, nhãn, nhóm, bậc gói, cờ có/không
    bí mật, trạng thái. KHÔNG bao giờ trả mật khẩu/TOTP secret.

    NV197c: ``?nhom=<id-nhom>`` lọc theo nhóm (so khớp chính xác).
    """
    try:
        ds = kho_tai_khoan_mod.liet_ke()
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    if nhom is not None:
        ds = [b for b in ds if b.get("nhom") == nhom]
    return {"tai_khoan": ds}


@tai_khoan.get("/nhom-loai")
def gom_tai_khoan_theo_nhom_loai():
    """Gom tài khoản theo nhóm → loại (NV218, thiết kế chủ máy 21/10).

    Trả ``{"nhom_loai": {nhom: {loai: {"tai_khoan": [...], "dang_dung": <id>|None}}}}``.
    ``dang_dung`` = id tài khoản đang được chọn đầu trong (nhóm, loại)
    (dùng được: không ``tam_dung``, chưa hết hạn mức). KHÔNG bao giờ trả
    mật khẩu/TOTP secret.
    """
    try:
        gom = kho_tai_khoan_mod.gom_theo_nhom_loai()
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    return {"nhom_loai": gom}


@tai_khoan.get("/han-muc")
def doc_han_muc_tai_khoan():
    """Hạn mức THỤ ĐỘNG đã đọc được từ tiêu đề phản hồi nhà cung cấp, theo từng
    tài khoản (chỉ vai quản trị). «khong_biet» khi nhà cung cấp không gửi tiêu đề
    — không bịa số."""
    from app import luong_dung_tai_khoan as ldtk
    try:
        ds = [{"id": tk["id"], "han_muc": ldtk.lay_han_muc(tk["id"]) or "khong_biet"}
              for tk in kho_tai_khoan_mod.liet_ke()]
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    return {"tai_khoan": ds}


@tai_khoan.get("/luong-dung")
def doc_luong_dung(request: Request):
    """Thống kê lượng dùng theo tài khoản (chỉ vai quản trị; không lộ của người khác
    vì chỉ quản trị được gọi). Không có tai_khoan_id → tổng quan mọi tài khoản có số
    liệu. Có tai_khoan_id → chi tiết theo ngày. Tham số: tai_khoan_id (tuỳ chọn),
    tu_ngay, den_ngay (YYYY-MM-DD, tuỳ chọn), trang (mặc định 1), moi_trang (mặc định
    30, tối đa 100)."""
    from app import luong_dung_tai_khoan as ldtk
    qp = request.query_params
    try:
        trang = int(qp.get("trang") or 1)
        moi_trang = int(qp.get("moi_trang") or 30)
    except (TypeError, ValueError):
        raise LoiOpenAI(400, "trang/moi_trang phải là số nguyên.", code="invalid_value") from None
    if trang < 1 or not 1 <= moi_trang <= 100:
        raise LoiOpenAI(400, "trang ≥ 1; moi_trang trong 1..100.", code="invalid_value")
    tai_khoan_id = (qp.get("tai_khoan_id") or "").strip()
    if not tai_khoan_id:
        tom = ldtk.tom_tat_moi_tai_khoan()
        hang = [{"tai_khoan_id": id_, "hom_nay": tt["dung"]["hom_nay"],
                 "7_ngay": tt["dung"]["7_ngay"],
                 "han_muc": tt["han_muc"]} for id_, tt in sorted(tom.items())]
        bat_dau = (trang - 1) * moi_trang
        return {"tai_khoan": hang[bat_dau:bat_dau + moi_trang],
                "trang": trang, "moi_trang": moi_trang, "tong_hang": len(hang)}
    try:
        tk = kho_tai_khoan_mod.liet_ke()
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    if not any(t.get("id") == tai_khoan_id for t in tk):
        raise LoiOpenAI(404, "Tài khoản không có.", code="not_found")
    for truong in ("tu_ngay", "den_ngay"):
        gia_tri = qp.get(truong)
        if gia_tri and (not isinstance(gia_tri, str) or len(gia_tri) != 10 or gia_tri[4] != "-" or gia_tri[7] != "-"):
            raise LoiOpenAI(400, f"{truong} phải dạng YYYY-MM-DD.", code="invalid_value")
    ket_qua = ldtk.thong_ke(tai_khoan_id, tu_ngay=qp.get("tu_ngay") or None,
                            den_ngay=qp.get("den_ngay") or None)
    if ket_qua is None:
        return {"tai_khoan_id": tai_khoan_id, "ngay": [], "tong": None,
                "trang": trang, "moi_trang": moi_trang, "tong_hang": 0}
    hang = ket_qua["ngay"]
    bat_dau = (trang - 1) * moi_trang
    return {"tai_khoan_id": tai_khoan_id,
            "ngay": hang[bat_dau:bat_dau + moi_trang],
            "tong": ket_qua["tong"],
            "trang": trang, "moi_trang": moi_trang, "tong_hang": len(hang)}


@tai_khoan.post("/da-luu")
async def luu_tai_khoan_da_luu(request: Request):
    """Lưu/cập nhật một tài khoản (theo email hoặc id, trùng → cập nhật) hoặc hàng loạt
    qua {"tai_khoan": [{email, mat_khau, totp_secret?, nhan?}]}.

    Thao tác chạm bí mật bên thứ ba: phiên web phải đã xác nhận lại (step-up);
    Bearer quản trị được miễn (đã gửi khoá bí mật cao nhất).

    NV197c: nhận thêm ``module_id`` (module đã cài — nhóm suy từ manifest của
    nó) và ``bac_goi`` (bậc gói do người dùng khai, không đoán). Không truyền
    → giữ nguyên giá trị cũ; ``module_id: ""`` → xoá gắn kết.
    NV218: nhận thêm ``loai_tai_khoan`` + ``dich_dung`` (phân loại theo bảng
    khai báo của đúng nhóm; sai bảng → 400). Không truyền → giữ nguyên cũ."""
    kiem_tra_step_up(request)
    than = await _than_json(request)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)

    def _module_va_nhom(muc: dict) -> tuple:
        """``(module_id, nhom)`` từ một mục body.

        Không có key → ``(None, None)`` (giữ nguyên cũ); ``""`` → ``("", "")``
        (xoá gắn kết); module chưa cài → 404; nhom suy từ
        ``manifest.nhom.id`` của module đó.
        """
        mid = muc.get("module_id")
        if mid is None:
            return None, None
        if not isinstance(mid, str):
            raise kho_tai_khoan_mod.LoiKhoTaiKhoan("invalid_value")
        mid = mid.strip()
        if not mid:
            return "", ""
        _kiem_module_da_cai_tai_khoan(request, mid)
        return mid, _nhom_cua_module(request, mid)

    try:
        ds = than.get("tai_khoan")
        if isinstance(ds, list):
            luu, loi = [], []
            for i, muc in enumerate(ds, start=1):
                try:
                    if not isinstance(muc, dict):
                        raise kho_tai_khoan_mod.LoiKhoTaiKhoan("invalid_value")
                    mid, nhom = _module_va_nhom(muc)
                    bk = kho_tai_khoan_mod.luu_tai_khoan(
                        muc.get("email"), muc.get("mat_khau"),
                        muc.get("totp_secret"), muc.get("nhan", ""),
                        trang_thai=muc.get("trang_thai"),
                        id=muc.get("id") or None,
                        module_id=mid, nhom=nhom,
                        bac_goi=muc.get("bac_goi"),
                        loai_tai_khoan=_chuoi_tuy_chon(muc, "loai_tai_khoan"),
                        dich_dung=_chuoi_tuy_chon(muc, "dich_dung"))
                    luu.append(bk)
                except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
                    if e.ma == "kho_hong":
                        raise
                    loi.append({"dong": i, "ma": e.ma})
            nhat_ky_mod.ghi(
                "quan_tri", "thong_tin",
                f"Lưu hàng loạt tài khoản đã lưu ({len(luu)} xong, {len(loi)} lỗi)",
                nguoi_thuc_hien=nguoi_thuc_hien)
            return {"luu": luu, "loi": loi}
        tai_id = _chuoi_tuy_chon(than, "id")
        email_val = _chuoi_tuy_chon(than, "email")
        if not tai_id and not email_val:
            raise LoiOpenAI(400, "Thiếu trường «email».", code="invalid_value")
        mid, nhom = _module_va_nhom(than)
        bk = kho_tai_khoan_mod.luu_tai_khoan(
            email_val,
            _chuoi_bat_buoc(than, "mat_khau"),
            _chuoi_tuy_chon(than, "totp_secret"),
            _chuoi_tuy_chon(than, "nhan") or "",
            trang_thai=_chuoi_tuy_chon(than, "trang_thai"),
            id=tai_id or None,
            module_id=mid, nhom=nhom,
            bac_goi=_chuoi_tuy_chon(than, "bac_goi"),
            loai_tai_khoan=_chuoi_tuy_chon(than, "loai_tai_khoan"),
            dich_dung=_chuoi_tuy_chon(than, "dich_dung"))
        nhat_ky_mod.ghi(
            "quan_tri", "thong_tin",
            f"Lưu tài khoản đã lưu ({bk['email_che']})",
            nguoi_thuc_hien=nguoi_thuc_hien)
        return {"tai_khoan": bk}
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None


@tai_khoan.delete("/da-luu/{id}")
def xoa_tai_khoan_da_luu(id: str, request: Request):
    """Xoá tài khoản đã lưu theo id. Id lạ → 404 (không lộ có/không).

    Phiên web phải đã xác nhận lại (step-up); Bearer quản trị được miễn."""
    kiem_tra_step_up(request)
    try:
        da_xoa = kho_tai_khoan_mod.xoa_tai_khoan(id)
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    if not da_xoa:
        raise LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.", code="khong_tim_thay")
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin", "Xoá tài khoản đã lưu",
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request))
    return {"da_xoa": True}


@tai_khoan.post("/da-luu/{id}/tam-dung")
async def dat_tam_dung_tai_khoan_da_luu(id: str, request: Request):
    """Tạm dừng / bật lại một tài khoản đã lưu (NV197b).

    Body: {"tam_dung": true|false} (phải là boolean thật). Tài khoản tạm dừng
    bị lõi BỎ QUA khi chọn/đăng nhập (không chạm bí mật, không gọi module);
    bật lại mới dùng được. Không tự bật lại theo giờ.

    Thao tác đổi trạng thái chạm bí mật bên thứ ba: phiên web phải đã xác nhận
    lại (step-up); Bearer <redacted>ản trị được miễn. Id lạ → 404 (không lộ có/không).
    """
    kiem_tra_step_up(request)
    than = await _than_json(request)
    tam_dung = than.get("tam_dung")
    if not isinstance(tam_dung, bool):
        raise LoiOpenAI(400, "Thiếu trường «tam_dung» (true/false).",
                        code="invalid_value")
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    try:
        bk = kho_tai_khoan_mod.dat_tam_dung(id, tam_dung)
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    if bk is None:
        raise LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.",
                        code="khong_tim_thay")
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"{'Tạm dừng' if tam_dung else 'Bật lại'} tài khoản đã lưu ({bk['email_che']})",
        nguoi_thuc_hien=nguoi_thuc_hien)
    return {"tai_khoan": bk}


@tai_khoan.post("/da-luu/{id}/dang-nhap")
async def dang_nhap_bang_tai_khoan_da_luu(id: str, request: Request):
    """Đăng nhập bằng tài khoản đã lưu: server tự lấy bí mật từ kho rồi gọi
    hợp đồng ``dang_nhap(dong)`` của module. Trình duyệt/giao diện KHÔNG nhận
    lại mật khẩu.

    Server lấy bí mật từ kho (thao tác nhạy cảm): phiên web phải đã xác nhận
    lại (step-up); Bearer quản trị được miễn."""
    kiem_tra_step_up(request)
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    _kiem_module_da_cai_tai_khoan(request, module_id)
    # NV197b: kiểm cờ tạm dừng TRƯỚC khi chạm bí mật — tài khoản tạm dừng
    # không được đăng nhập, lỗi rõ ràng, bí mật không rời kho.
    ban_ghi = kho_tai_khoan_mod.tim_theo_id(id)
    if ban_ghi is not None and ban_ghi.get("trang_thai") == kho_tai_khoan_mod.TRANG_THAI_TAM_DUNG:
        raise LoiOpenAI(403, "Tài khoản đã tạm dừng; bật lại mới đăng nhập được.",
                        code="tam_dung")
    try:
        bi_mat = kho_tai_khoan_mod.lay_bi_mat_dang_nhap(id)
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    if bi_mat is None:
        raise LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.", code="khong_tim_thay")
    mat_khau, totp = bi_mat
    email = ban_ghi["email"] if ban_ghi else ""
    mod = request.app.state.so.dang_chay.get(module_id)
    ham = getattr(mod, "dang_nhap", None) if mod is not None else None
    if not callable(ham):
        mat_khau.xoa()
        if totp is not None:
            totp.xoa()
        raise LoiOpenAI(502, "Module chưa triển khai đăng nhập.", code="chua_ho_tro")
    dong = tai_khoan_hang_loat_mod.DongDaTach(
        dong=0, email=email, loai="mat_khau", mat_khau=mat_khau, totp=totp)
    # NV210: module sở hữu của tài khoản (dữ liệu) để module đăng nhập chọn
    # đúng luồng; lõi không đoán.
    dong.module_cua_tai_khoan = ban_ghi.get("module_id") or "" if ban_ghi else ""
    try:
        kq = await ham(dong)
    finally:
        dong.xoa_bi_mat()
    thanh_cong = bool(getattr(kq, "thanh_cong", False))
    if thanh_cong:
        # NV210: đưa JWT mới (nếu module đăng nhập trả) vào module SỞ HỮU tài khoản — theo khả năng
        # module khai, không theo tên; token bị xoá khỏi kết quả ngay sau đó.
        gan_ket_qua_dang_nhap_mod.ap_dung(request.app.state.so, kq, dong.module_cua_tai_khoan)
        # NV197c: học module sở hữu từ lần đăng nhập THÀNH CÔNG — nhóm suy từ
        # manifest của module đó. Ghi sổ phụ: lỗi không làm hỏng kết quả đăng nhập.
        try:
            kho_tai_khoan_mod.dat_module(
                id, module_id, _nhom_cua_module(request, module_id))
        except Exception:
            log.warning("tai-khoan: không ghi được module sở hữu cho %s", id)
        # NV214: nối cookie phiên vào module sở hữu (nếu module đó khai
        # nhan_cookie và module thực thi báo ho_so_dang_nhap) — lỗi nối
        # không làm hỏng kết quả đăng nhập, cookie không ra phản hồi/log.
        try:
            await _noi_cookie_dang_nhap_vao_module(
                request.app.state.so, mod,
                (ban_ghi.get("module_id") or "") if ban_ghi else "", kq)
        except Exception as e:
            log.warning("tai-khoan: nối cookie đăng nhập lỗi: %s",
                        type(e).__name__)
    return {
        "thanh_cong": thanh_cong,
        "can_nguoi": bool(getattr(kq, "can_nguoi", False)),
        "cho_2fa": bool(getattr(kq, "cho_2fa", False)),
        "huong_dan": getattr(kq, "huong_dan", "") or "",
        "ma_loi": getattr(kq, "ma_loi", "") or "",
    }


@tai_khoan.post("/da-luu/{id}/ma-2fa")
async def gui_ma_2fa_tai_khoan_da_luu(id: str, request: Request):
    """Gửi mã 2FA cho lượt đăng nhập đang chờ mã (module triển khai `nhap_ma_2fa`,
    NV145b). Mã đi qua BiMatTam và bị xoá ngay sau khi gửi.

    Thao tác chạm bí mật: phiên web phải đã xác nhận lại (step-up);
    Bearer <redacted>ản trị được miễn."""
    kiem_tra_step_up(request)
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    _kiem_module_da_cai_tai_khoan(request, module_id)
    ban_ghi = kho_tai_khoan_mod.tim_theo_id(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.", code="khong_tim_thay")
    email = ban_ghi.get("email") or ""
    mod = request.app.state.so.dang_chay.get(module_id)
    ham = getattr(mod, "nhap_ma_2fa", None) if mod is not None else None
    if not callable(ham):
        raise LoiOpenAI(502, "Module chưa triển khai nhập mã 2FA.", code="chua_ho_tro")
    ma = _chuoi_bat_buoc(than, "ma")
    bi_mat_ma = tai_khoan_hang_loat_mod.BiMatTam(ma)
    try:
        # NV210: module sở hữu để module chọn đúng đường 2FA của luồng
        kq = await ham(email, bi_mat_ma,
                       module_cua_tai_khoan=ban_ghi.get("module_id") or "")
    finally:
        bi_mat_ma.xoa()
    if not isinstance(kq, dict):
        kq = {}
    return {"thanh_cong": bool(kq.get("ok")),
            "ma_loi": kq.get("ma_loi", "") or "",
            "huong_dan": kq.get("huong_dan", "") or ""}


@tai_khoan.get("/da-luu/{id}/dang-nhap-trang-thai")
async def trang_thai_dang_nhap_tai_khoan(id: str, request: Request,
                                        module_id: str = ""):
    """Hỏi trạng thái lượt đăng nhập nền (module triển khai `tien_do`
    — NV145b). Chỉ đọc, không chạm bí mật nên không cần step-up; id tài khoản
    không đoán được (chống IDOR như các tuyến da-luu khác)."""
    _kiem_module_da_cai_tai_khoan(request, module_id)
    ban_ghi = kho_tai_khoan_mod.tim_theo_id(id)
    if ban_ghi is None:
        raise LoiOpenAI(404, "Không tìm thấy tài khoản đã lưu.", code="khong_tim_thay")
    email = ban_ghi.get("email") or ""
    mod = request.app.state.so.dang_chay.get(module_id)
    ham = getattr(mod, "tien_do", None) if mod is not None else None
    if not callable(ham):
        raise LoiOpenAI(502, "Module chưa triển khai theo dõi tiến trình.",
                        code="chua_ho_tro")
    kq = await ham(email, module_cua_tai_khoan=ban_ghi.get("module_id") or "")
    if not isinstance(kq, dict):
        kq = {"ok": False}
    return {k: kq.get(k) for k in ("ok", "giai_doan", "buoc", "cho_2fa",
                                  "can_nguoi", "ma_loi", "huong_dan")}


@tai_khoan.get("/tu-dang-nhap-lai")
def lay_cong_tac_tu_dang_nhap_lai():
    """Đọc công tắc «tự đăng nhập lại khi phiên hết hạn» (mặc định BẬT)."""
    return {"bat": kho_tai_khoan_mod.tu_dang_nhap_lai_bat()}


@tai_khoan.put("/tu-dang-nhap-lai")
async def dat_cong_tac_tu_dang_nhap_lai(request: Request):
    """Bật/tắt «tự đăng nhập lại khi phiên hết hạn». Thân: {"bat": bool}."""
    than = await _than_json(request)
    bat = than.get("bat")
    if not isinstance(bat, bool):
        raise LoiOpenAI(400, "Trường «bat» phải là true/false.", code="invalid_value")
    try:
        moi = kho_tai_khoan_mod.dat_tu_dang_nhap_lai(bat)
    except kho_tai_khoan_mod.LoiKhoTaiKhoan as e:
        raise _loi_kho_tai_khoan(e) from None
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Đặt công tắc tự đăng nhập lại: {'BẬT' if moi else 'TẮT'}",
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request))
    return {"bat": moi}


@tai_khoan.post("/dan-hang-loat")
async def dan_hang_loat(request: Request):
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    _kiem_module_da_cai_tai_khoan(request, module_id)
    van_ban = _chuoi_tuy_chon(than, "van_ban") or ""
    danh_sach, loi, bo_trung = tai_khoan_hang_loat_mod.tach_dong(van_ban)
    try:
        tai_khoan = [
            {"dong": tk.dong, "email": tk.email, "loai": tk.loai} for tk in danh_sach
        ]
    finally:
        # Văn bản dán không dùng nữa sau bước tách — xoá bí mật khỏi bộ nhớ ngay.
        for tk in danh_sach:
            tk.xoa_bi_mat()
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Tách danh sách dán hàng loạt cho module «{_nhac_lai(module_id)}»: "
        f"{len(tai_khoan)} dòng đúng, {len(loi)} dòng lỗi",
        module=module_id,
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {
        "tai_khoan": tai_khoan,
        "loi": [{"dong": l.dong, "loi": l.loi} for l in loi],
        "bo_trung": [{"dong": b.dong, "email": b.email} for b in bo_trung],
    }


@tai_khoan.post("/dang-nhap-hang-loat/bat-dau")
async def bat_dau_dang_nhap_hang_loat(request: Request):
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    _kiem_module_da_cai_tai_khoan(request, module_id)
    van_ban = _chuoi_tuy_chon(than, "van_ban") or ""
    bo_qua_email = than.get("bo_qua_email", [])
    if not isinstance(bo_qua_email, list) or any(not isinstance(e, str) for e in bo_qua_email):
        raise LoiOpenAI(400, "«bo_qua_email» phải là danh sách chuỗi.", code="invalid_value")
    bo_qua = {e.strip().lower() for e in bo_qua_email}

    danh_sach, _loi, _trung = tai_khoan_hang_loat_mod.tach_dong(van_ban)
    giu = [tk for tk in danh_sach if tk.email.strip().lower() not in bo_qua]
    giu_id = {id(tk) for tk in giu}
    for tk in danh_sach:
        if id(tk) not in giu_id:
            tk.xoa_bi_mat()
    if not giu:
        raise LoiOpenAI(400, "Không có tài khoản hợp lệ nào để chạy.", code="khong_co_tai_khoan")

    ham_dang_nhap = _ham_dang_nhap_cua_module(request, module_id)
    quan_ly = request.app.state.quan_ly_luot_chay
    try:
        thong_tin = quan_ly.bat_dau(giu, ham_dang_nhap, module_id)
    except tai_khoan_hang_loat_mod.DaCoLuotChay:
        raise LoiOpenAI(409, "Đã có một lượt đăng nhập hàng loạt đang chạy.", code="da_co_luot_chay") from None
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Bắt đầu lượt đăng nhập hàng loạt cho module «{_nhac_lai(module_id)}»: "
        f"{thong_tin['tong']} tài khoản",
        module=module_id,
        luot_id=thong_tin["luot_id"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return thong_tin


@tai_khoan.get("/dang-nhap-hang-loat/trang-thai")
def trang_thai_dang_nhap_hang_loat(request: Request):
    tt = request.app.state.quan_ly_luot_chay.trang_thai()
    if tt is None:
        raise LoiOpenAI(404, "Chưa có lượt đăng nhập hàng loạt nào.", code="luot_khong_co")
    return tt


@tai_khoan.post("/dang-nhap-hang-loat/dung")
def dung_dang_nhap_hang_loat(request: Request):
    if not request.app.state.quan_ly_luot_chay.dung():
        raise LoiOpenAI(404, "Không có lượt đăng nhập hàng loạt nào đang chạy.", code="luot_khong_chay")
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        "Dừng lượt đăng nhập hàng loạt theo yêu cầu",
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"ok": True}


@tai_khoan.post("/da-luu/dang-nhap-theo-module")
async def dang_nhap_da_luu_theo_module(request: Request):
    """Đăng nhập hàng loạt các tài khoản đã lưu thuộc một module sở hữu (NV210).

    Thân: {"module_id": "<executor>", "module_so_huu": "<owning>",
    "chi_chua_co_ho_so": true}. Lọc theo DỮ LIỆU (``module_id`` của bản ghi —
    NV197c), không đoán. ``chi_chua_co_ho_so``: loại tài khoản mà module sở
    hữu đã có ``ho_so`` (qua khả năng ``liet_ke_tai_khoan`` nếu module khai).
    Chạy TUẦN TỰ, giãn cách, dừng sớm qua ``QuanLyLuotChay`` (không dồn dập từ
    một IP). Chạm bí mật → cần step-up; Bearer quản trị được miễn.
    """
    kiem_tra_step_up(request)
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    _kiem_module_da_cai_tai_khoan(request, module_id)
    module_so_huu = _chuoi_bat_buoc(than, "module_so_huu")
    _kiem_module_da_cai_tai_khoan(request, module_so_huu)
    chi_chua_co_ho_so = bool(than.get("chi_chua_co_ho_so", True))
    mod = request.app.state.so.dang_chay.get(module_id)
    ham = getattr(mod, "dang_nhap", None) if mod is not None else None
    if not callable(ham):
        raise LoiOpenAI(502, "Module chưa triển khai đăng nhập.", code="chua_ho_tro")
    # Email đã có hồ sơ ở module sở hữu → bỏ qua khi chỉ lấy «chưa có hồ sơ»
    da_co_ho_so: set[str] = set()
    if chi_chua_co_ho_so:
        mod_sh = request.app.state.so.dang_chay.get(module_so_huu)
        liet_ke = getattr(mod_sh, "liet_ke_tai_khoan", None)
        if callable(liet_ke):
            try:
                for r in liet_ke() or []:
                    if isinstance(r, dict) and r.get("ho_so"):
                        e = str(r.get("email") or "").strip().lower()
                        if e:
                            da_co_ho_so.add(e)
            except Exception as e:
                # KHÔNG nuốt: không biết tài khoản nào đã có hồ sơ thì «đăng nhập hết» là mở rộng thao tác
                # trên tài khoản thật (nguy cơ bị nhà cung cấp hạn chế). Dừng, báo rõ, không chạy gì.
                log.warning("đăng nhập hàng loạt: không đọc được danh sách hồ sơ của %s: %s",
                            module_so_huu, type(e).__name__)
                raise LoiOpenAI(502, "Không đọc được danh sách hồ sơ hiện có của module sở hữu — không chạy "
                                     "hàng loạt để tránh đăng nhập lại những tài khoản đã có hồ sơ.",
                                code="khong_doc_duoc_ho_so") from None
    chon: list = []
    for bg in kho_tai_khoan_mod.liet_ke():
        if not isinstance(bg, dict):
            continue
        if str(bg.get("module_id") or "") != module_so_huu:
            continue
        if str(bg.get("trang_thai") or "") == kho_tai_khoan_mod.TRANG_THAI_TAM_DUNG:
            continue
        # `liet_ke()` chỉ trả dạng CÔNG KHAI (email đã che, không có trường «email»): lấy email thật từ
        # bản ghi nội bộ theo id. Bản đầu dùng bg["email"] nên email luôn rỗng → mọi dòng hỏng.
        noi_bo = kho_tai_khoan_mod.tim_theo_id(bg.get("id"))
        email = str((noi_bo or {}).get("email") or "")
        if not email:
            continue
        if chi_chua_co_ho_so and email.strip().lower() in da_co_ho_so:
            continue
        bm = kho_tai_khoan_mod.lay_bi_mat_dang_nhap(bg.get("id"))
        if bm is None:
            continue
        mat_khau, totp = bm
        d = tai_khoan_hang_loat_mod.DongDaTach(
            0, email, "mat_khau", mat_khau=mat_khau, totp=totp)
        d.module_cua_tai_khoan = module_so_huu
        chon.append(d)
    if not chon:
        raise LoiOpenAI(400, "Không có tài khoản nào đủ điều kiện để chạy.",
                        code="khong_co_tai_khoan")

    so_loi = request.app.state.so

    async def ham_chay(dong):
        kq = await ham(dong)
        if getattr(kq, "thanh_cong", False):
            # NV210: mỗi tài khoản đăng nhập xong thì gắn JWT mới + hồ sơ vào module sở hữu.
            gan_ket_qua_dang_nhap_mod.ap_dung(so_loi, kq, module_so_huu)
            # NV214: và nối cookie phiên vào module sở hữu — lỗi nối không làm hỏng kết quả từng dòng.
            try:
                await _noi_cookie_dang_nhap_vao_module(so_loi, mod, module_so_huu, kq)
            except Exception as e:
                log.warning("dang-nhap-theo-module: nối cookie lỗi: %s", type(e).__name__)
        return kq

    quan_ly = request.app.state.quan_ly_luot_chay
    try:
        thong_tin = quan_ly.bat_dau(chon, ham_chay, module_id)
    except tai_khoan_hang_loat_mod.DaCoLuotChay:
        for d in chon:
            d.xoa_bi_mat()
        raise LoiOpenAI(409, "Đã có một lượt đăng nhập hàng loạt đang chạy.",
                        code="da_co_luot_chay") from None
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri",
        "thong_tin",
        f"Bắt đầu lượt đăng nhập hàng loạt theo module «{_nhac_lai(module_so_huu)}»: "
        f"{thong_tin['tong']} tài khoản",
        module=module_id,
        luot_id=thong_tin["luot_id"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return thong_tin


# ───────────── ĐĂNG NHẬP TỰ ĐỘNG + KHÔI PHỤC NHIỀU TẦNG (QT) — NV88 ─────────────
#
# Thêm tài khoản → TỰ ĐĂNG NHẬP → TỰ LẤY COOKIE → TỰ KHÔI PHỤC NHIỀU TẦNG.
# Bước đăng nhập nằm TRONG MODULE (`dang_nhap`/`dang_nhap_tu_dong`); lõi chỉ
# điều phối qua app/khoi_phuc_phien.py (tầng a→d) và tái dùng LuotChay
# (giãn cách 30s, dừng sớm). Không có tên nhà cung cấp nào ở tầng này.
# Mật khẩu/cookie KHÔNG BAO GIỜ xuất hiện trong phản hồi, log hay nhật ký.

_MA_400_KHOA = "khoa_khong_hop_le"


def _truong_bi_mat_cua_module(module) -> tuple[str, bool] | None:
    """Trường bí mật đầu tiên mà manifest module khai (ưu tiên danh sách bí mật).
    Trả (khoá trường, là_danh_sách) hoặc None."""
    truong = (getattr(module, "manifest", None) or {}).get("truong_cau_hinh") or []
    for kieu, la_ds in (("danh_sach_bi_mat", True), ("bi_mat", False)):
        for t in truong:
            if isinstance(t, dict) and t.get("kieu") == kieu and isinstance(t.get("khoa"), str):
                return t["khoa"], la_ds
    return None


def _module_khoi_phuc(request: Request, module_id: str):
    """Module đã cài cho khôi phục; chưa cài → 404."""
    _kiem_module_da_cai_tai_khoan(request, module_id)
    module = request.app.state.so.the_hien_cho_dang_nhap(module_id)
    if module is None:
        raise LoiOpenAI(404, f"Module «{_nhac_lai(module_id)}» chưa chạy.", code="module_chua_chay")
    return module


def _kiem_khoa_thuoc_module(module, module_id: str, khoa: str) -> None:
    """IDOR: khoá phải đúng khuôn và thuộc module này; không thì 404 (không lộ)."""
    try:
        khoa_hop_le = khoi_phuc_phien_mod.kiem_khoa(khoa)
    except ValueError:
        raise LoiOpenAI(400, "Trường «khoa» không hợp lệ.", code=_MA_400_KHOA)
    biet = getattr(module, "co_tai_khoan", None)
    if callable(biet) and not biet(khoa_hop_le):
        # Lỗi của module KHÔNG được nuốt: bỏ qua bước này khi module hỏng là «mở cửa khi lỗi» (thao tác chạy trên khoá chưa kiểm).
        raise LoiOpenAI(404, "Không có tài khoản này trong module.", code="tai_khoan_khong_co")


async def _luu_chung_chi_vao_module(so: Any, module: Any, module_id: str, chung_chi: str) -> dict:
    """Lưu chứng chỉ phiên (cookie/token) vừa đăng nhập vào CẤU HÌNH của module qua `so.cai()` — MỘT đường chung cho mọi cách
    thêm tài khoản (kiểu «qua danh tính» và kiểu «trực tiếp»). Tên trường bí mật do manifest của module quyết (không ghi cứng trong lõi).
    Trả kết quả `cai()` (có khoá `ok`); lỗi lưu → LoiOpenAI; chứng chỉ thô không giữ lại."""
    truong_dich = _truong_bi_mat_cua_module(module)
    if truong_dich is None:
        chung_chi = None
        raise LoiOpenAI(501, f"Module «{_nhac_lai(module_id)}» không khai trường "
                             "bí mật để nhận tài khoản.", code="module_chua_ho_tro")
    khoa_truong, la_danh_sach = truong_dich
    try:
        return await so.cai(
            module_id, {khoa_truong: {"them": [chung_chi]} if la_danh_sach else chung_chi})
    except (LoiSo, kho.KhoHong) as e:
        raise _loi_so(e) from None
    except Exception as e:
        log.warning("lưu tài khoản %s lỗi: %s", module_id, type(e).__name__)
        raise LoiOpenAI(502, "Không lưu được tài khoản mới.", code="loi_luu") from None
    finally:
        chung_chi = None  # noqa: F841 — không giữ chuỗi thô


def _truong_nhan_cookie_cua_module(module) -> tuple[str, bool, str] | None:
    """Trường cấu hình mà manifest module khai ``nhan_cookie`` (nhận cookie
    phiên từ lượt đăng nhập): (khoá trường, là_danh_sách, tên miền). Không
    khai → None. Lõi chỉ đọc KHẢ NĂNG, không biết tên nhà cung cấp nào."""
    truong = (getattr(module, "manifest", None) or {}).get("truong_cau_hinh") or []
    for t in truong:
        if not isinstance(t, dict):
            continue
        nc = t.get("nhan_cookie")
        if not isinstance(nc, dict):
            continue
        mien = nc.get("mien")
        khoa = t.get("khoa")
        if (not isinstance(mien, str) or not mien
                or not isinstance(khoa, str) or not khoa
                or t.get("kieu") not in kho.KIEU_BI_MAT):
            continue
        return khoa, t.get("kieu") == "danh_sach_bi_mat", mien
    return None


async def _noi_cookie_dang_nhap_vao_module(so: Any, mod_thuc_thi: Any,
                                           module_so_huu: str, kq: Any) -> bool:
    """NV214 — nối nốt chuỗi đăng nhập: module thực thi đăng nhập THÀNH CÔNG
    thì lấy cookie phiên qua khả năng ``trinh_duyet_cookie`` của nó (đúng tên
    miền do module sở hữu khai ``nhan_cookie`` trong manifest) rồi đưa vào
    module sở hữu bằng ``so.cai`` {giu, them} (thêm + khử trùng chuỗi y hệt).

    Không thay «cookie cũ của tài khoản đó» theo chuỗi email: dữ liệu đã lưu
    không ánh xạ cookie→tài khoản nên không nhận diện được bằng dữ liệu chắc
    chắn — thêm mới thay vì đoán (cùng ngữ nghĩa với tuyến cookie-sang-module).

    Mọi lỗi ở đây chỉ ghi log (kiểu lỗi, KHÔNG nội dung cookie) — không bao giờ
    làm hỏng kết quả đăng nhập. Cookie thô bị xoá ngay sau khi dùng. Trả True
    nếu đã nối được cookie vào module sở hữu."""
    ho_so = getattr(kq, "ho_so_dang_nhap", "") or ""
    if not isinstance(ho_so, str) or not ho_so:
        return False
    mod_sh = (getattr(so, "dang_chay", None) or {}).get(module_so_huu)
    if mod_sh is None:
        return False
    truong = _truong_nhan_cookie_cua_module(mod_sh)
    if truong is None:
        return False
    khoa_truong, la_danh_sach, mien = truong
    lay = getattr(mod_thuc_thi, "trinh_duyet_cookie", None)
    if not callable(lay):
        return False
    cookie: str | None = None
    try:
        try:
            kq_lay = await lay(ho_so, mien)
        except Exception as e:
            log.warning("noi-cookie: lấy cookie từ module thực thi lỗi: %s",
                        type(e).__name__)
            return False
        cookie = kq_lay.get("cookie") if isinstance(kq_lay, dict) else None
        if (not isinstance(cookie, str) or not cookie
                or len(cookie) > novnc_mod.TRAN_COOKIE
                or not novnc_mod.MAU_CHUOI_COOKIE.fullmatch(cookie)):
            log.warning("noi-cookie: kết quả lấy cookie sai cấu trúc (%s)",
                        type(cookie).__name__)
            return False
        try:
            kq_cai = await so.cai(
                module_so_huu,
                {khoa_truong: {"them": [cookie]} if la_danh_sach else cookie})
        except Exception as e:
            # Lỗi của sổ/kho/module: chỉ ghi kiểu lỗi, không nội dung cookie.
            log.warning("noi-cookie: cài cookie vào module sở hữu lỗi: %s",
                        type(e).__name__)
            return False
        return bool(kq_cai.get("ok"))
    finally:
        cookie = None  # noqa: F841 — không giữ chuỗi thô


async def _xu_ly_ket_qua_tai_dung(
    so: Any,
    module: Any,
    module_id: str,
    ho_so: str,
    ban_ghi: dict[str, Any],
    kq: Any,
    nguoi_thuc_hien: str,
) -> dict[str, Any]:
    if not isinstance(kq, tai_khoan_hang_loat_mod.KetQuaDangNhap):
        log.warning("tai-dung: module %s trả kiểu lạ (%s) — đóng cửa",
                    module_id, type(kq).__name__)
        raise LoiOpenAI(502, "Module trả kết quả sai kiểu.", code="loi_module")
    if kq.ma_loi == "can_nguoi":
        url = khoi_phuc_phien_mod.mo_novnc_mac_dinh(module_id, ho_so)
        return {"thanh_cong": False, "can_nguoi": True, "ma_loi": "can_nguoi",
                "huong_dan": "Provider cần bạn thao tác trực tiếp trong hồ sơ "
                             "(CAPTCHA/2FA/xác minh). Hãy mở noVNC đăng nhập "
                             "lại trong hồ sơ rồi thử lại.",
                "url_novnc": url}
    if not kq.thanh_cong or not kq.cookie:
        return {"thanh_cong": False, "can_nguoi": False,
                "ma_loi": kq.ma_loi or "loi_module"}
    kq_cai = await _luu_chung_chi_vao_module(so, module, module_id, kq.cookie)
    khoa_che = "…" + hashlib.sha256(
        str(kq.cookie or "").encode("utf-8")).hexdigest()[-4:]
    if not kq_cai.get("ok"):
        # Module từ chối lưu (khoá sai/không hợp lệ): không báo thành công,
        # không gắn danh tính, không ghi «đã thêm».
        return {"thanh_cong": False, "can_nguoi": False,
                "ma_loi": "luu_that_bai", "da_luu": False}
    try:
        danh_tinh_mod.gan(ban_ghi["id"], module_id)
    except Exception as e:
        log.warning("tai-dung: gắn danh tính %s vào %s lỗi: %s",
                    ban_ghi["id"], module_id, type(e).__name__)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Tái dùng hồ sơ trình duyệt cho «{_nhac_lai(module_id)}»: đã thêm tài khoản",
        module=module_id, id_danh_tinh=ban_ghi["id"],
        nguoi_thuc_hien=nguoi_thuc_hien)
    return {"thanh_cong": True, "can_nguoi": False, "khoa": khoa_che,
            "danh_tinh": ban_ghi["id"], "da_luu": bool(kq_cai.get("ok"))}


# ───────────── NV122: mã xác minh qua email ─────────────

def _xac_minh_email_cua_module(module, module_id: str) -> dict:
    """Cấu hình «xac_minh_email» từ MANIFEST của module đăng nhập (nguồn sự thật
    duy nhất cho tu_mien/do_dai); module không khai → 400 (cờ tích vô nghĩa)."""
    cfg = (getattr(module, "manifest", None) or {}).get("xac_minh_email")
    if not isinstance(cfg, dict):
        raise LoiOpenAI(400, f"Module «{_nhac_lai(module_id)}» không khai «xac_minh_email» "
                             "nên không dùng được cờ «lấy mã từ hộp thư».",
                        code="khong_ho_tro_ma_email")
    return cfg


def _hop_thu_cua_dong(dong) -> str:
    """Hộp thư của một tài khoản: True → trùng email tài khoản; chuỗi → hộp thư
    quản trị chỉ định rõ. Một tài khoản chỉ dùng được hai dạng này."""
    return dong.email if dong.ma_tu_hop_thu is True else dong.ma_tu_hop_thu


async def _xu_ly_can_ma_email(app, so, module, module_id: str, dong,
                              cfg: dict, sau_luc: str | None,
                              da_goi_ma: set) -> "tai_khoan_hang_loat_mod.KetQuaDangNhap":
    """NV122: module báo `can_ma_email` (trang đang đòi mã gửi qua email), cờ
    tích BẬT và module khai `xac_minh_email` → đọc mã từ hộp thư rồi điền qua
    `nhap_ma_xac_minh`. Mỗi tài khoản tối đa MỘT lần gọi lay_ma.

    Mã bọc trong `BiMatTam`, xoá ngay sau khi điền; không lấy được mã → trả
    ma_loi rõ để giao diện báo «Không đọc được mã từ hộp thư — nhập tay hoặc
    dùng ứng dụng xác thực». Lấy được + điền thành công → trả kết quả như đăng
    nhập thành công (tầng gọi lưu chứng chỉ bằng `_luu_chung_chi_vao_module`).
    """
    KQ = tai_khoan_hang_loat_mod.KetQuaDangNhap
    if id(dong) in da_goi_ma:
        return KQ(False, ma_loi="da_goi_ma")
    da_goi_ma.add(id(dong))
    hop_thu = _hop_thu_cua_dong(dong)
    tu_mien = cfg.get("tu_mien") or []
    do_dai = cfg.get("do_dai") or [6, 6]
    ma, ma_loi = await ma_xac_minh_mod.lay_ma_xac_minh(
        app, hop_thu, list(tu_mien), (int(do_dai[0]), int(do_dai[1])),
        sau_luc or "", ma_xac_minh_mod.CHO_GIAY_MAC_DINH)
    if not ma:
        return KQ(False, ma_loi=ma_loi or "loi_module")
    nhap = getattr(module, "nhap_ma_xac_minh", None)
    if not callable(nhap):
        return KQ(False, ma_loi="loi_module")
    bi_mat = tai_khoan_hang_loat_mod.BiMatTam(ma)
    ma = None  # noqa: F841 — không giữ chuỗi mã thô sau khi đã bọc
    try:
        kq2 = await nhap(dong, bi_mat, trinh_duyet=None)
    except Exception as e:
        log.warning("nhap_ma_xac_minh của %s lỗi: %s", module_id, type(e).__name__)
        return KQ(False, ma_loi="loi_module")
    finally:
        bi_mat.xoa()
    if not isinstance(kq2, KQ):
        log.warning("nhap_ma_xac_minh của %s trả kiểu lạ (%s) — đóng cửa",
                    module_id, type(kq2).__name__)
        return KQ(False, ma_loi="loi_module")
    if not kq2.thanh_cong:
        ma2 = kq2.ma_loi or "loi_module"
        if not tai_khoan_hang_loat_mod._MAU_MA_LOI.fullmatch(ma2):
            ma2 = "loi_module"
        return KQ(False, ma_loi=ma2)
    return kq2


@tai_khoan.post("/dang-nhap-tu-dong/bat-dau")
async def bat_dau_dang_nhap_tu_dong(request: Request):
    """Đăng nhập tự động hàng loạt: tái dùng LuotChay, gọi `mod.dang_nhap` hoặc kiểu danh tính."""
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    kieu = than.get("kieu")
    if kieu is None:
        kieu = "truc_tiep"
    if not isinstance(kieu, str) or kieu not in ("truc_tiep", "danh_tinh"):
        raise LoiOpenAI(400, "Trường «kieu» không hợp lệ (chỉ nhận \"truc_tiep\" hoặc \"danh_tinh\").",
                        code="invalid_value")

    module = _module_khoi_phuc(request, module_id)
    if kieu == "truc_tiep":
        if not callable(getattr(module, "dang_nhap", None)):
            raise LoiOpenAI(400, f"Module «{_nhac_lai(module_id)}» chưa hỗ trợ đăng nhập.",
                            code="module_chua_ho_tro")
    else:  # kieu == "danh_tinh"
        mod_dn = _module_dang_nhap_danh_tinh(request, bat_buoc=False)
        if not callable(getattr(module, "tai_dung_ho_so", None)) or mod_dn is None:
            raise LoiOpenAI(400, "Thiếu điều kiện đăng nhập qua danh tính "
                                 "(cần provider hỗ trợ tái dùng hồ sơ và module đăng nhập danh tính).",
                            code="thieu_module_dang_nhap_danh_tinh")
        if _truong_bi_mat_cua_module(module) is None:
            raise LoiOpenAI(400, f"Module «{_nhac_lai(module_id)}» không khai trường bí mật để nhận tài khoản.",
                            code="module_chua_ho_tro")
        mod_td = _tim_module_trinh_duyet(request)
        if mod_td is None:
            raise LoiOpenAI(503, "Chưa có dịch vụ trình duyệt (cần module có khả năng «trinh_duyet»).",
                            code="chua_co_trinh_duyet")

    co_van_ban = "van_ban" in than and than.get("van_ban") is not None
    co_tai_khoan = "tai_khoan" in than and than.get("tai_khoan") is not None
    if co_van_ban and co_tai_khoan:
        raise LoiOpenAI(400, "Không được gửi đồng thời «van_ban» và «tai_khoan».", code="invalid_value")
    if not co_van_ban and not co_tai_khoan:
        raise LoiOpenAI(400, "Phải cung cấp một trong hai trường: «tai_khoan» hoặc «van_ban».", code="invalid_value")

    bo_qua_email = than.get("bo_qua_email", [])
    if not isinstance(bo_qua_email, list) or any(not isinstance(e, str) for e in bo_qua_email):
        raise LoiOpenAI(400, "«bo_qua_email» phải là danh sách chuỗi.", code="invalid_value")
    bo_qua = {e.strip().lower() for e in bo_qua_email}

    if co_tai_khoan:
        tai_khoan_raw = than.get("tai_khoan")
        try:
            danh_sach, _trung = tai_khoan_hang_loat_mod.tach_danh_sach_tai_khoan(tai_khoan_raw)
        except ValueError as e:
            raise LoiOpenAI(400, str(e), code="invalid_value") from None
    else:
        van_ban = _chuoi_tuy_chon(than, "van_ban") or ""
        danh_sach, _loi, _trung = tai_khoan_hang_loat_mod.tach_dong(van_ban, nang_cap_totp=True)
    giu = [tk for tk in danh_sach if tk.email.strip().lower() not in bo_qua]
    giu_id = {id(tk) for tk in giu}
    for tk in danh_sach:
        if id(tk) not in giu_id:
            tk.xoa_bi_mat()
    if not giu:
        raise LoiOpenAI(400, "Không có tài khoản hợp lệ nào để chạy.", code="khong_co_tai_khoan")

    app = request.app
    so = _so(request)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    dang_chay = (getattr(app.state, "so", None) is not None and getattr(app.state.so, "dang_chay", None)) or {}

    # NV122 — cờ «lấy mã từ hộp thư» theo từng tài khoản (cột hop_thu thứ tư của
    # van_ban; NV120 gộp với mảng tai_khoan có cấu trúc qua tai_khoan[i].ma_tu_hop_thu).
    # Kiểm ĐỒNG BỘ trước khi chạy: module phải khai xac_minh_email và phải có
    # hộp thư khớp — không bắt người dùng chờ vô ích.
    cfg_xac_minh = None
    co_hop_thu = [tk for tk in giu if tk.ma_tu_hop_thu]
    if co_hop_thu:
        cfg_xac_minh = _xac_minh_email_cua_module(module, module_id)
        for tk in co_hop_thu:
            hop_thu = tk.email if tk.ma_tu_hop_thu is True else tk.ma_tu_hop_thu
            if not ma_xac_minh_mod.co_hop_thu(so, hop_thu):
                for t2 in giu:
                    t2.xoa_bi_mat()
                raise LoiOpenAI(400, "Không có hộp thư đọc được nào khớp «lấy mã từ hộp thư» "
                                    "của tài khoản này.", code="khong_co_hop_thu")

    if kieu == "truc_tiep":
        # Mỗi tài khoản (dong) tối đa MỘT lần gọi lay_ma trong cả lượt.
        da_goi_ma: set[int] = set()

        async def ham_chay(dong: tai_khoan_hang_loat_mod.DongDaTach) -> tai_khoan_hang_loat_mod.KetQuaDangNhap:
            # Đăng nhập xong phải LƯU vào cấu hình (đường chung) — nếu không tài khoản chỉ nằm trong bộ nhớ module và mất khi
            # khởi động lại; module chưa có tài khoản cũng nhờ đó mới chạy được.
            # NV122: đọc sau_luc (lúc bắt đầu bước đăng nhập) để chỉ nhận thư tới sau mốc này.
            sau_luc = (ma_xac_minh_mod.luc_hien_tai_iso()
                       if (dong.ma_tu_hop_thu and cfg_xac_minh is not None) else None)
            kq = await module.dang_nhap(dong)
            # NV122: module báo trang đang đòi mã gửi qua email + cờ tích BẬT +
            # module khai xac_minh_email → đọc mã từ hộp thư rồi điền; cờ TẮT thì
            # giữ hành vi hiện có (không bao giờ tự chuyển sang đọc hộp thư).
            if (isinstance(kq, tai_khoan_hang_loat_mod.KetQuaDangNhap) and not kq.thanh_cong
                    and kq.ma_loi == "can_ma_email"
                    and dong.ma_tu_hop_thu and cfg_xac_minh is not None):
                kq = await _xu_ly_can_ma_email(app, so, module, module_id, dong,
                                               cfg_xac_minh, sau_luc, da_goi_ma)
            # Chỉ khi module KHAI trường bí mật để nhận tài khoản; không khai thì module tự quản tài khoản của nó (hành vi cũ).
            if (isinstance(kq, tai_khoan_hang_loat_mod.KetQuaDangNhap) and kq.thanh_cong and kq.cookie
                    and _truong_bi_mat_cua_module(module) is not None):
                try:
                    kq_cai = await _luu_chung_chi_vao_module(so, module, module_id, kq.cookie)
                except LoiOpenAI:
                    return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="luu_that_bai")
                if not kq_cai.get("ok"):
                    return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="luu_that_bai")
            return kq
    else:
        mod_dn = _module_dang_nhap_danh_tinh(app, bat_buoc=False)
        mod_td = _tim_module_trinh_duyet(app)

        async def ham_dong_danh_tinh(dong: tai_khoan_hang_loat_mod.DongDaTach) -> tai_khoan_hang_loat_mod.KetQuaDangNhap:
            try:
                email_chuan = danh_tinh_mod.chuan_hoa_email(dong.email)
            except Exception:
                dong.xoa_bi_mat()
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="email_khong_hop_le")

            ban_ghi_co_san = danh_tinh_mod.tim_theo_email(email_chuan)
            da_tao_dt = False
            da_tao_hs = False
            dt_id = None
            ho_so = None

            try:
                if ban_ghi_co_san is not None:
                    dt_id = ban_ghi_co_san["id"]
                    if ban_ghi_co_san.get("ho_so_trinh_duyet"):
                        ho_so = ban_ghi_co_san["ho_so_trinh_duyet"]
                    else:
                        try:
                            ws = await mod_td.trinh_duyet_tao(ten=email_chuan)
                        except Exception as e:
                            log.warning("danh-tinh-dong: tạo hồ sơ cho %s lỗi: %s", dt_id, type(e).__name__)
                            dong.xoa_bi_mat()
                            return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
                        hs = ws.get("ho_so") if isinstance(ws, dict) else None
                        if not isinstance(hs, str) or not MAU_HO_SO_TRINH_DUYET.fullmatch(hs):
                            dong.xoa_bi_mat()
                            return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
                        ho_so = hs
                        da_tao_hs = True
                        danh_tinh_mod.cap_nhat_trang_thai(dt_id, ho_so=ho_so)
                else:
                    try:
                        ban_ghi = danh_tinh_mod.tao(email_chuan, None)
                        dt_id = ban_ghi["id"]
                        da_tao_dt = True
                    except Exception as e:
                        log.warning("danh-tinh-dong: tạo danh tính cho %s lỗi: %s", email_chuan, type(e).__name__)
                        dong.xoa_bi_mat()
                        return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")

                    try:
                        ws = await mod_td.trinh_duyet_tao(ten=email_chuan)
                    except Exception as e:
                        log.warning("danh-tinh-dong: tạo hồ sơ cho %s lỗi: %s", dt_id, type(e).__name__)
                        await _don_danh_tinh_do_dang(app, mod_td, dt_id, None)
                        dong.xoa_bi_mat()
                        return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
                    hs = ws.get("ho_so") if isinstance(ws, dict) else None
                    if not isinstance(hs, str) or not MAU_HO_SO_TRINH_DUYET.fullmatch(hs):
                        await _don_danh_tinh_do_dang(app, mod_td, dt_id, None)
                        dong.xoa_bi_mat()
                        return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
                    ho_so = hs
                    da_tao_hs = True
                    danh_tinh_mod.cap_nhat_trang_thai(dt_id, ho_so=ho_so)
            except Exception as e:
                log.warning("danh-tinh-dong: lỗi chuẩn bị danh tính/hồ sơ: %s", type(e).__name__)
                if da_tao_dt and dt_id:
                    await _don_danh_tinh_do_dang(app, mod_td, dt_id, ho_so if da_tao_hs else None)
                elif da_tao_hs and ho_so:
                    try:
                        await mod_td.trinh_duyet_dong(ho_so, giu_phien=False)
                    except Exception as e:
                        log.warning("danh-tinh-dong: trinh_duyet_dong lỗi: %s", type(e).__name__)
                dong.xoa_bi_mat()
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")

            # Bước B — đăng nhập tài khoản email trong hồ sơ
            td = khoi_phuc_phien_mod.lay_trinh_duyet_that(ho_so, dang_chay)
            if td is None:
                dong.xoa_bi_mat()
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")

            try:
                async with khoi_phuc_phien_mod.phien_dieu_khien_that(td):
                    kq_dn = await mod_dn.dang_nhap(
                        ho_so, email_chuan, dong.mat_khau, td, ma_2fa=dong.totp)
            except Exception as e:
                if getattr(e, "ma", None) == 409:
                    return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="ho_so_dang_ban")
                log.warning("danh-tinh-dong: mod_dn lỗi: %s", type(e).__name__)
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
            finally:
                await _dong_trinh_duyet_that(td)

            try:
                if kq_dn.thanh_cong:
                    danh_tinh_mod.cap_nhat_trang_thai(dt_id, trang_thai="dang_nhap")
                elif getattr(kq_dn, "can_nguoi", False):
                    danh_tinh_mod.cap_nhat_trang_thai(dt_id, trang_thai="can_nguoi")
            except Exception as e:
                log.warning("danh-tinh-dong: cap_nhat_trang_thai %s lỗi: %s", dt_id, type(e).__name__)

            if getattr(kq_dn, "can_nguoi", False):
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="can_nguoi")
            if getattr(kq_dn, "cho_2fa", False):
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="cho_2fa")
            if not kq_dn.thanh_cong:
                ma = getattr(kq_dn, "ma_loi", "") or "loi_module"
                if not tai_khoan_hang_loat_mod._MAU_MA_LOI.fullmatch(ma):
                    ma = "loi_module"
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi=ma)

            # Bước C — tái dùng hồ sơ cho nhà cung cấp
            td2 = khoi_phuc_phien_mod.lay_trinh_duyet_that(ho_so, dang_chay)
            if td2 is None:
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
            try:
                async with khoi_phuc_phien_mod.phien_dieu_khien_that(td2):
                    kq_td = await module.tai_dung_ho_so(ho_so, td2)
            except Exception as e:
                if getattr(e, "ma", None) == 409:
                    return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="ho_so_dang_ban")
                log.warning("danh-tinh-dong: tai_dung_ho_so %s lỗi: %s", module_id, type(e).__name__)
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")
            finally:
                await _dong_trinh_duyet_that(td2)

            ban_ghi_dt = danh_tinh_mod.tim_theo_email(email_chuan) or {"id": dt_id}
            try:
                res_td = await _xu_ly_ket_qua_tai_dung(
                    so=so,
                    module=module,
                    module_id=module_id,
                    ho_so=ho_so,
                    ban_ghi=ban_ghi_dt,
                    kq=kq_td,
                    nguoi_thuc_hien=nguoi_thuc_hien,
                )
            except LoiOpenAI as e:
                ma = getattr(e, "code", None) or "loi_module"
                if not tai_khoan_hang_loat_mod._MAU_MA_LOI.fullmatch(ma):
                    ma = "loi_module"
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi=ma)
            except Exception as e:
                log.warning("danh-tinh-dong: _xu_ly_ket_qua_tai_dung lỗi: %s", type(e).__name__)
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="loi_module")

            if res_td.get("thanh_cong"):
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(True)
            if res_td.get("can_nguoi"):
                return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi="can_nguoi")
            ma = res_td.get("ma_loi") or "loi_module"
            if not tai_khoan_hang_loat_mod._MAU_MA_LOI.fullmatch(ma):
                ma = "loi_module"
            return tai_khoan_hang_loat_mod.KetQuaDangNhap(False, ma_loi=ma)

        ham_chay = ham_dong_danh_tinh

    quan_ly = request.app.state.quan_ly_luot_chay
    try:
        thong_tin = quan_ly.bat_dau(giu, ham_chay, module_id)
    except tai_khoan_hang_loat_mod.DaCoLuotChay:
        for tk in giu:
            tk.xoa_bi_mat()
        raise LoiOpenAI(409, "Đã có một lượt đăng nhập hàng loạt đang chạy.", code="da_co_luot_chay") from None

    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Bắt đầu lượt đăng nhập TỰ ĐỘNG ({kieu}) cho module «{_nhac_lai(module_id)}»: "
        f"{thong_tin['tong']} tài khoản",
        module=module_id, luot_id=thong_tin["luot_id"],
        nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return thong_tin


@tai_khoan.post("/khoi-phuc")
async def khoi_phuc_tai_khoan_route(request: Request):
    """Chạy khôi phục nhiều tầng (a)→(d) cho một tài khoản của một module."""
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    module = _module_khoi_phuc(request, module_id)
    khoa = _chuoi_bat_buoc(than, "khoa")
    _kiem_khoa_thuoc_module(module, module_id, khoa)
    email = _chuoi_tuy_chon(than, "email") or ""
    tai_khoan = khoi_phuc_phien_mod.TaiKhoanCanKhoiPhuc(khoa=khoa, email=email)
    dang_chay = (getattr(request.app.state, "so", None) is not None
                 and getattr(request.app.state.so, "dang_chay", None)) or {}
    kq = await khoi_phuc_phien_mod.khoi_phuc(module, tai_khoan,
                                             dang_chay=dang_chay)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Khôi phục tài khoản {khoa} ({_nhac_lai(module_id)}): tầng {kq.tang}",
        module=module_id, nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return kq.sang_dict()


@tai_khoan.get("/khoi-phuc/trang-thai")
def trang_thai_khoi_phuc_route(request: Request, module_id: str = "", khoa: str = ""):
    """Trạng thái khôi phục của một tài khoản (không chứa bí mật)."""
    if not module_id or not khoa:
        raise LoiOpenAI(400, "Thiếu «module_id» hoặc «khoa».", code="invalid_value")
    module = _module_khoi_phuc(request, module_id)
    _kiem_khoa_thuoc_module(module, module_id, khoa)
    tt = khoi_phuc_phien_mod.lay_trang_thai(module_id, khoa)
    return {"module_id": module_id, "khoa": khoa, **tt}


@tai_khoan.post("/khoi-phuc/xoa-can-nguoi")
async def xoa_can_nguoi_route(request: Request):
    """Người đã xử lý tay xong → xoá trạng thái can_nguoi và bộ đếm."""
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    module = _module_khoi_phuc(request, module_id)
    khoa = _chuoi_bat_buoc(than, "khoa")
    _kiem_khoa_thuoc_module(module, module_id, khoa)
    khoi_phuc_phien_mod.xoa_can_nguoi(module_id, khoa)
    nguoi_thuc_hien = dinh_danh_nguoi_thuc_hien(request)
    nhat_ky_mod.ghi(
        "quan_tri", "thong_tin",
        f"Xoá trạng thái cần-người của {khoa} ({_nhac_lai(module_id)})",
        module=module_id, nguoi_thuc_hien=nguoi_thuc_hien,
    )
    return {"ok": True}


@tai_khoan.post("/tai-dung")
async def tai_dung_ho_so_provider(request: Request):
    """Tái dùng hồ sơ trình duyệt đã đăng nhập cho một provider (NV92 P3).

    Thân: {"module_id", "ho_so" (mã workspace)}. Chạy
    ``module.tai_dung_ho_so(ho_so, trinh_duyet)`` — provider tự làm bước đăng
    nhập bằng danh tính đã lưu của nó TRONG hồ sơ, lấy phiên, thêm tài khoản của
    CHÍNH nó. Tài khoản mỗi provider ĐỘC LẬP. Chứng chỉ phiên mới được lưu vào
    cấu hình module qua đường cai() có sẵn; phản hồi chỉ mang khoá che.
    Gặp CAPTCHA/2FA/xác minh → can_nguoi + noVNC, KHÔNG cố vượt.
    """
    than = await _than_json(request)
    module_id = _chuoi_bat_buoc(than, "module_id")
    ho_so = _chuoi_bat_buoc(than, "ho_so")
    module = _module_khoi_phuc(request, module_id)
    tai_dung = getattr(module, "tai_dung_ho_so", None)
    if not callable(tai_dung):
        raise LoiOpenAI(501, f"Module «{_nhac_lai(module_id)}» chưa hỗ trợ tái "
                             "dùng hồ sơ.", code="module_chua_ho_tro")
    if not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
        raise LoiOpenAI(400, "«ho_so» không hợp lệ.", code="invalid_value")
    ban_ghi = _danh_tinh_theo_ho_so(ho_so)
    dang_chay = (getattr(request.app.state, "so", None) is not None
                 and getattr(request.app.state.so, "dang_chay", None)) or {}
    td = khoi_phuc_phien_mod.lay_trinh_duyet_that(ho_so, dang_chay)
    if td is None:
        raise LoiOpenAI(503, "Không lấy được bộ điều khiển trình duyệt thật.",
                        code="chua_co_trinh_duyet_that")
    # NV94-P3: xong thì DONG (đóng httpx.Client của TrinhDuyetThat).
    try:
        # Giành quyền điều khiển máy cho đúng lượt này rồi NHẢ LUÔN
        # (kể cả khi lỗi) — hồ sơ không kẹt «đang bận».
        try:
            async with khoi_phuc_phien_mod.phien_dieu_khien_that(td):
                kq = await tai_dung(ho_so, td)
        finally:
            await _dong_trinh_duyet_that(td)
    except Exception as e:
        if getattr(e, "ma", None) == 409:
            raise LoiOpenAI(409, "Hồ sơ trình duyệt đang bận (có người hoặc "
                                 "máy khác đang điều khiển). Thử lại sau.",
                            code="ho_so_dang_ban") from None
        log.warning("tai-dung: module %s lỗi: %s", module_id, type(e).__name__)
        raise LoiOpenAI(502, f"Module lỗi khi tái dùng hồ sơ ({type(e).__name__}).",
                        code="loi_module") from None
    return await _xu_ly_ket_qua_tai_dung(
        so=_so(request),
        module=module,
        module_id=module_id,
        ho_so=ho_so,
        ban_ghi=ban_ghi,
        kq=kq,
        nguoi_thuc_hien=dinh_danh_nguoi_thuc_hien(request),
    )


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


# ───────────── Bảo mật (/api/bao-mat) — chỉ quản trị (dependencies=QT) ─────────────

@bao_mat.get("/khoa-dang-nhap", dependencies=KHONG_LUU)
def liet_ke_khoa_dang_nhap_quan_tri():
    """Danh sách khoá đăng nhập ĐANG hiệu lực: theo tài khoản (tên), theo cặp (IP, tên) và theo IP. Chỉ admin (router QT).
    Dùng khi người dùng quên mật khẩu, thử nhiều lần và bị chặn — đường đăng nhập cố ý không báo «đang bị khoá»."""
    khoa = [{"loai": "tai_khoan" if m["loai"] == "ten" else "cap_ip_ten", "ten": m["ten"], "ip": m["ip"],
             "con_lai_giay": m["con_lai_giay"]} for m in nguoi_dung_mod.liet_ke_khoa_dang_nhap()]
    khoa += [{"loai": "ip", "ten": None, "ip": m["ip"], "con_lai_giay": m["con_lai_giay"]}
             for m in gioi_han_mod.liet_ke_ip_bi_khoa()]
    return JSONResponse({"khoa": khoa}, headers={"Cache-Control": "no-store"})


@bao_mat.post("/khoa-dang-nhap/bo-chan", dependencies=KHONG_LUU)
async def bo_chan_dang_nhap_quan_tri(request: Request):
    """Bỏ chặn đăng nhập. Thân JSON: {"loai": "tai_khoan"|"ip"|"tat_ca", "ten": "...", "ip": "..."}.
    tai_khoan → bỏ khoá tên đó (kể cả khoá theo cặp IP); ip → bỏ chặn IP đó (cả khoá cặp lẫn khoá IP);
    tat_ca → bỏ mọi chặn. Có nhật ký quản trị; thao tác lặp lại là vô hại."""
    than = await _than_json(request)
    loai = _chuoi_tuy_chon(than, "loai")
    if loai == "tai_khoan":
        ten = _chuoi_tuy_chon(than, "ten")
        if not ten:
            raise LoiOpenAI(400, "Cần tên tài khoản cần bỏ chặn.", code="invalid_input")
        so = nguoi_dung_mod.bo_chan_dang_nhap(ten=ten)
        doi_tuong = f"tài khoản «{_nhac_lai(ten)}»"
    elif loai == "ip":
        ip = _chuoi_tuy_chon(than, "ip")
        if not ip:
            raise LoiOpenAI(400, "Cần địa chỉ IP cần bỏ chặn.", code="invalid_input")
        so = nguoi_dung_mod.bo_chan_dang_nhap(ip=ip) + gioi_han_mod.bo_chan_ip(ip.strip()[:45])
        doi_tuong = f"IP {_nhac_lai(ip)}"
    elif loai == "tat_ca":
        so = nguoi_dung_mod.bo_chan_dang_nhap() + gioi_han_mod.bo_chan_ip(None)
        doi_tuong = "mọi chặn đăng nhập"
    else:
        raise LoiOpenAI(400, "«loai» phải là tai_khoan, ip hoặc tat_ca.", code="invalid_input")
    nhat_ky_mod.ghi("quan_tri", "canh_bao", f"Quản trị bỏ chặn đăng nhập: {doi_tuong} ({so} mục)")
    return JSONResponse({"ok": True, "da_bo": so}, headers={"Cache-Control": "no-store"})


@bao_mat.get("/trang-thai")
def lay_trang_thai_bao_mat():
    mangs = ip_cho_phep_mod._doc_danh_sach()
    trong = mangs is None
    bang_ron = (
        "Cổng IP quản trị đang tắt (WEB2API_QT_IP_CHO_PHEP trống). Bất kỳ IP nào cũng có thể truy cập nếu có quyền quản trị."
        if trong
        else None
    )
    so_mang = len(mangs) if isinstance(mangs, list) else 0
    return {
        "cong_ip_quan_tri_trong": trong,
        "bang_ron": bang_ron,
        "so_mang": so_mang,
    }

