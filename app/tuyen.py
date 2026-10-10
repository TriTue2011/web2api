"""Các tuyến API của lõi: /v1 (chuẩn OpenAI), /api/modules, /api/nguon-git, /m/<id>/ (giao diện của module).

Quyền: MỞ cho GET /api/modules và /api/modules/catalog (không chứa bí mật, không lộ repo);
mọi tuyến khác ở đây đòi khoá quản trị (xác thực theo từng tuyến bằng `dependencies`).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app import kho
from app.loi_openai import LoiOpenAI
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiSo, SoModule
from app.xac_thuc import can_quan_tri

log = logging.getLogger("web2api.tuyen")
QT = [Depends(can_quan_tri)]

v1 = APIRouter(prefix="/v1", dependencies=QT)
modules = APIRouter(prefix="/api/modules")
nguon_git = APIRouter(prefix="/api/nguon-git", dependencies=QT)
tep_module = APIRouter()


def _so(request: Request) -> SoModule:
    return request.app.state.so


def _ng(request: Request) -> NguonGit:
    return request.app.state.nguon_git


async def _than_json(request: Request) -> dict:
    try:
        than = await request.json()
    except ValueError:
        raise LoiOpenAI(400, "Thân yêu cầu không phải JSON hợp lệ") from None
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")
    return than


def _loi_so(e: LoiSo | LoiGit) -> LoiOpenAI:
    return LoiOpenAI(e.ma, str(e))


# ───────────── /v1 ─────────────

@v1.get("/models")
async def v1_models(request: Request):
    so = _so(request)
    cac = list(so.dang_chay.items())
    ket_qua = await asyncio.gather(*(m.models() for _, m in cac), return_exceptions=True)
    data = []
    for (id_, _), kq in zip(cac, ket_qua):
        if isinstance(kq, Exception):
            log.warning("Module %s lỗi khi liệt kê model, bỏ qua: %s: %s", id_, type(kq).__name__, kq)
            continue
        if isinstance(kq, BaseException):
            raise kq
        data.extend(kq)
    return {"object": "list", "data": data}


@v1.post("/chat/completions")
async def v1_chat(request: Request):
    # Đọc thân như dict THÔ (không qua mô hình Pydantic): mô hình sẽ lặng lẽ bỏ trường lạ
    # như extra_content.google.thought_signature.
    than = await _than_json(request)
    model = than.get("model")
    if not isinstance(model, str) or not model:
        raise LoiOpenAI(400, "Thiếu trường «model» (dạng <tiền tố>/<tên>, ví dụ gemini/gemini-2.5-flash)",
                        code="missing_model")
    so = _so(request)
    tien_to, co_gach, _ = model.partition("/")
    id_ = so.tim_theo_tien_to(tien_to) if co_gach else None
    if id_ is None:
        cai = ", ".join(so.tien_to_dang_chay()) or "(chưa cài module nào)"
        raise LoiOpenAI(404, f"Model {model!r} không thuộc module nào. Model phải có dạng <tiền tố>/<tên>; "
                             f"tiền tố đang dùng được: {cai}. Xem GET /v1/models.", code="model_not_found")
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} (tiền tố {tien_to}/) chưa cài — cài bằng POST /api/modules/{id_}/cai",
                        code="module_not_installed")
    ma, headers, noi_dung = await m.chat(than, bool(than.get("stream")))
    headers = dict(headers)
    kieu = headers.pop("content-type", None)
    if isinstance(noi_dung, (bytes, str)):
        return Response(noi_dung, status_code=ma, headers=headers, media_type=kieu)
    return StreamingResponse(noi_dung, status_code=ma, headers=headers, media_type=kieu)


# ───────────── /api/modules ─────────────

@modules.get("")
async def ds_modules(request: Request):
    """Module đã cài (đọc modules.json, tương thích bản khung) + module từ xa. Không bí mật, không tên repo."""
    so = _so(request)
    ds, canh_bao = kho.doc_modules()
    if canh_bao:
        log.warning("%s", canh_bao)
    ra = []
    for muc in ds:
        muc = dict(muc) if isinstance(muc, dict) else {"id": None, "hong": repr(muc)}
        km = so.catalog.get(muc.get("id"))
        if km is not None:
            muc.setdefault("ten", km.manifest["ten"])
            if muc["id"] in so.dang_chay and km.duong_tab():
                muc["tab_web"] = km.duong_tab()
        ra.append(muc)
    ra += [so.cong_khai_tu_xa(i) for i in so.tu_xa]
    return {"modules": ra, "canh_bao": canh_bao, "thong_bao_so": _ng(request).so_thong_bao()}


@modules.get("/catalog")
async def catalog(request: Request):
    so = _so(request)
    ds = []
    for km in so.catalog.values():
        m = km.manifest
        ds.append({k: m.get(k) for k in ("id", "ten", "mo_ta", "phien_ban", "loai", "giao_thuc", "tien_to",
                                         "truong_cau_hinh", "tai_nguyen", "yeu_cau_goi", "tab_web")}
                  | {"nguon": km.nguon, "da_cai": km.id in so.dang_chay})
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
    m = _so(request).dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} chưa cài hoặc không tồn tại")
    return await m.suc_khoe()


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
    try:
        return await _ng(request).cai(id_)
    except (LoiGit, LoiSo) as e:
        raise _loi_so(e) from None


# ───────────── /m/<id>/ — giao diện đi theo module ─────────────

@tep_module.get("/m/{id_}/{duong:path}")
async def tep_cua_module(id_: str, duong: str, request: Request):
    so = _so(request)
    km = so.catalog.get(id_)
    if km is None or id_ not in so.dang_chay or not km.manifest.get("tab_web"):
        raise HTTPException(404)
    goc = (km.thu_muc / "web").resolve()
    p = (goc / duong).resolve()
    # resolve() rồi so tổ tiên: chặn «..» lẫn symlink trỏ ra ngoài thư mục web/ của module.
    if not p.is_relative_to(goc) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)
