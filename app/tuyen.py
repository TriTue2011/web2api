"""Các tuyến API của lõi: /v1 (chuẩn OpenAI), /api/modules, /api/tinh-nang, /api/nguon-git, /m/<id>/.

Quyền: MỞ cho GET /api/modules, /api/modules/catalog và /api/tinh-nang (không chứa bí mật, không lộ repo);
mọi tuyến khác ở đây đòi khoá quản trị (xác thực theo từng tuyến bằng `dependencies`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPMethod

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.routing import Match
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from app import dia_chi as dia_chi_mod, kho
from app.loi_openai import LoiOpenAI
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiSo, SoModule
from app.than_co_tran import THAN_JSON, doc_het
from app.xac_thuc import can_quan_tri
from app.yeu_cau_da_phan import YeuCauDaPhan, doc_multipart, la_multipart

log = logging.getLogger("web2api.tuyen")
QT = [Depends(can_quan_tri)]

v1 = APIRouter(prefix="/v1", dependencies=QT)
#: Bắt mọi đường /v1 mà router `v1` không có — main.py include NGAY SAU `v1` (route đăng ký sau vào `v1` vẫn đứng
#: trước nó). Thiếu nó thì mount StaticFiles ở gốc nuốt mất và trả 405 {"detail"} cho mọi POST lạ.
v1_khong_co = APIRouter(prefix="/v1", dependencies=QT)
modules = APIRouter(prefix="/api/modules")
tinh_nang = APIRouter()
nguon_git = APIRouter(prefix="/api/nguon-git", dependencies=QT)
dia_chi = APIRouter(prefix="/api", dependencies=QT)
tep_module = APIRouter()


def _so(request: Request) -> SoModule:
    return request.app.state.so


def _ng(request: Request) -> NguonGit:
    return request.app.state.nguon_git


async def _than_json(request: Request) -> dict:
    # Không dùng request.json(): nó nạp trọn thân không giới hạn (và giữ thêm một bản trong request suốt lượt gọi).
    try:
        than = json.loads(await doc_het(request, THAN_JSON))
    except ValueError:  # gồm cả UnicodeDecodeError
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


def _module_dang_chay(so: SoModule, id_: str, tien_to: str):
    m = so.dang_chay.get(id_)
    if m is None:  # tìm thấy tiền tố mà không chạy → mã đã cài (catalog), chưa liên kết; module từ xa luôn chạy
        raise LoiOpenAI(404, f"Module {id_!r} (tiền tố {tien_to}/) đã cài nhưng chưa có khoá/cấu hình — mở "
                             f"Hệ thống → tab Module → ô «{so.catalog[id_].manifest['ten']}» trên trang web2api để "
                             f"thêm khoá (hoặc POST /api/modules/{id_}/cai)", code="module_not_configured")
    return m


def _module_theo_model(so: SoModule, than: dict):
    """(id, thể hiện) của module có tiền tố ghi trong `model` — 404 rõ ràng nếu không thuộc ai/chưa liên kết."""
    model = than.get("model")
    if not isinstance(model, str) or not model:
        raise LoiOpenAI(400, "Thiếu trường «model» (dạng <tiền tố>/<tên>; danh sách ở GET /v1/models)",
                        code="missing_model")
    tien_to, co_gach, _ = model.partition("/")
    id_ = so.tim_theo_tien_to(tien_to) if co_gach else None
    if id_ is None:
        cai = ", ".join(so.tien_to_dang_chay()) or "(chưa có module nào đã liên kết)"
        raise LoiOpenAI(404, f"Model {model!r} không thuộc module nào. Model phải có dạng <tiền tố>/<tên>; "
                             f"tiền tố đang dùng được: {cai}. Xem GET /v1/models.", code="model_not_found")
    return id_, _module_dang_chay(so, id_, tien_to)


def _module_theo_ma_video(so: SoModule, ma: str):
    """Mã video ra ngoài là "<tien_to>:<mã của module>"; tiền tố không chứa «:» (TIEN_TO_HOP_LE) nên tách ở
    dấu «:» đầu tiên là đủ — không cần bảng mã → module."""
    tien_to, co_cham, trong = ma.partition(":")
    id_ = so.tim_theo_tien_to(tien_to) if co_cham and trong else None
    if id_ is None:
        raise LoiOpenAI(404, f"Không có video {ma!r} (mã dạng <tiền tố>:<mã>, lấy từ POST /v1/videos)",
                        code="video_not_found")
    return id_, _module_dang_chay(so, id_, tien_to), trong


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


def _tra_ve(ma: int, headers: dict, noi_dung) -> Response:
    headers = dict(headers)
    kieu = headers.pop("content-type", None)
    if isinstance(noi_dung, (bytes, str)):
        # Thân đã có trọn: Starlette tự tính content-length đúng; số của module (có thể lệch) bị bỏ.
        headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
        return Response(noi_dung, status_code=ma, headers=headers, media_type=kieu)
    return StreamingResponse(noi_dung, status_code=ma, headers=headers, media_type=kieu)


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
    # Đọc thân như dict THÔ (không qua mô hình Pydantic): mô hình sẽ lặng lẽ bỏ trường lạ
    # như extra_content.google.thought_signature.
    than = await _than_json(request)
    _, m = _module_theo_model(_so(request), than)
    return _tra_ve(*await m.chat(than, bool(than.get("stream"))))


@v1.post("/images/generations")
async def v1_anh(request: Request):
    than = await _than_json(request)
    id_, m = _module_theo_model(_so(request), than)
    _can_phuong_thuc(m, id_, "tạo ảnh", "anh")
    return _tra_ve(*await m.anh(than))


@v1.post("/images/edits")
async def v1_anh_sua(request: Request):
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(_so(request), yc.truong)
    _can_phuong_thuc(m, id_, "sửa ảnh", "anh_sua")
    _can_tep(yc, "image")
    _can_tep(yc, "mask", toi_thieu=0, toi_da=1)
    prompt = yc.truong.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise LoiOpenAI(400, "Thiếu trường «prompt» (mô tả cần sửa gì)", code="missing_prompt")
    return _tra_ve(*await m.anh_sua(yc))


@v1.post("/audio/speech")
async def v1_noi(request: Request):
    than = await _than_json(request)
    id_, m = _module_theo_model(_so(request), than)
    _can_phuong_thuc(m, id_, "đọc văn bản thành giọng nói", "noi")
    return _tra_ve(*await m.noi(than))


@v1.post("/audio/transcriptions")
async def v1_nghe(request: Request):
    yc = await doc_multipart(request)
    id_, m = _module_theo_model(_so(request), yc.truong)
    _can_phuong_thuc(m, id_, "nhận dạng giọng nói", "nghe")
    _can_tep(yc, "file", toi_da=1)
    return _tra_ve(*await m.nghe(yc))


_VIDEO = ("video_tao", "video_trang_thai", "video_noi_dung")


@v1.post("/videos")
async def v1_video_tao(request: Request):
    # SDK openai gửi videos.create bằng multipart/form-data: trường chữ thành thân JSON (giá trị vẫn là chuỗi —
    # module tự ép kiểu, như với thân JSON do khách gõ).
    yc = await doc_multipart(request) if la_multipart(request) else None
    than = dict(yc.truong) if yc else await _than_json(request)
    so = _so(request)
    id_, m = _module_theo_model(so, than)
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
    so = _so(request)
    id_, m, trong = _module_theo_ma_video(so, ma)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _gan_tien_to_ma_video(so.tien_to_cua(id_), *await m.video_trang_thai(trong))


@v1.get("/videos/{ma}/content")
async def v1_video_noi_dung(ma: str, request: Request):
    id_, m, trong = _module_theo_ma_video(_so(request), ma)
    _can_phuong_thuc(m, id_, "tạo video", *_VIDEO)
    return _tra_ve(*await m.video_noi_dung(trong))


@v1_khong_co.api_route("", methods=list(HTTPMethod), include_in_schema=False)
@v1_khong_co.api_route("/{duong:path}", methods=list(HTTPMethod), include_in_schema=False)
async def duong_v1_khong_co(request: Request):
    """Đường có trong router `v1` nhưng sai phương thức → 405 + Allow; không có hẳn → 404 `unknown_endpoint`.

    Suy từ CHÍNH router `v1` (route nào khớp đường mà không khớp phương thức — Match.PARTIAL của Starlette), không từ
    bảng tay: thêm đường mới vào `v1` là tự đúng. Nêu lại đường khách gọi nhưng chỉ phần path (query có thể mang
    khoá, vd ?key=…), đã cắt ngắn và bỏ ký tự điều khiển — không phản chiếu chuỗi dài/độc."""
    scope = dict(request.scope)
    cho_phep = sorted({pt for r in v1.routes if r.matches(scope)[0] is Match.PARTIAL for pt in r.methods})
    duong = "".join(k for k in request.url.path if k.isprintable())
    duong = duong if len(duong) <= 120 else duong[:120] + "…"
    goi = f"{request.method} {duong}"
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
                   tab_web=km.duong_tab())
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


# ───────────── /api/tinh-nang ─────────────

@tinh_nang.get("/api/tinh-nang")
async def xem_tinh_nang(request: Request):
    """MỞ (như GET /api/modules): khung giao diện cần nó TRƯỚC khi người dùng dán khoá quản trị — trang đầu
    là trang Module khi chưa cài gì. Không bí mật, không cấu hình, không tên repo."""
    return _so(request).tinh_nang()


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
    # Mở khi module ĐÃ CÀI (mã trên đĩa) và khai tab riêng — kể cả lúc chưa liên kết: tệp tĩnh của module,
    # không chứa bí mật, và có khi chính là nơi người dùng thêm khoá.
    if km is None or not km.manifest.get("tab_web"):
        raise HTTPException(404)
    goc = (km.thu_muc / "web").resolve()
    p = (goc / duong).resolve()
    # resolve() rồi so tổ tiên: chặn «..» lẫn symlink trỏ ra ngoài thư mục web/ của module.
    if not p.is_relative_to(goc) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


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

