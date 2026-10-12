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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from app import dia_chi as dia_chi_mod, kho, khoa_khach as khoa_khach_mod, nhat_ky as nhat_ky_mod, thiet_bi as thiet_bi_mod
from app.loi_openai import LoiOpenAI
from app.nguon_git import LoiGit, NguonGit
from app.so_module import LoiSo, SoModule
from app.than_co_tran import THAN_JSON, doc_het
from app.xac_thuc import can_quan_tri, kiem_tra_pham_vi, xac_thuc_v1
from app.yeu_cau_da_phan import YeuCauDaPhan, doc_multipart, la_multipart

log = logging.getLogger("web2api.tuyen")
QT = [Depends(can_quan_tri)]
V1_AUTH = [Depends(xac_thuc_v1)]

v1 = APIRouter(prefix="/v1", dependencies=V1_AUTH)
#: Bắt mọi đường /v1 mà router `v1` không có — main.py include NGAY SAU `v1` (route đăng ký sau vào `v1` vẫn đứng
#: trước nó). Thiếu nó thì mount StaticFiles ở gốc nuốt mất và trả 405 {"detail"} cho mọi POST lạ.
v1_khong_co = APIRouter(prefix="/v1", dependencies=V1_AUTH)
modules = APIRouter(prefix="/api/modules")
tinh_nang = APIRouter()
nguon_git = APIRouter(prefix="/api/nguon-git", dependencies=QT)
dia_chi = APIRouter(prefix="/api", dependencies=QT)
nhat_ky = APIRouter(prefix="/api/nhat-ky", dependencies=QT)
khoa_khach = APIRouter(prefix="/api/khoa-khach", dependencies=QT)
thiet_bi = APIRouter(prefix="/api/thiet-bi", dependencies=V1_AUTH)
phe_duyet = APIRouter(prefix="/api/phe-duyet", dependencies=QT)
tep_module = APIRouter()


def _so(request: Request) -> SoModule:
    return request.app.state.so


def _ng(request: Request) -> NguonGit:
    return request.app.state.nguon_git


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


def _loi_so(e: LoiSo | LoiGit) -> LoiOpenAI:
    return LoiOpenAI(e.ma, str(e))


# ───────────── /v1 ─────────────

def _nhom_cua_model(item: dict) -> str:
    kn = item.get("kha_nang")
    if isinstance(kn, list) and kn:
        dau = kn[0]
        return str(dau).strip() if dau else "chat"
    if isinstance(kn, str) and kn.strip():
        return kn.strip()
    return "chat"


@v1.get("/models")
async def v1_models(request: Request):
    so = _so(request)
    cac = list(so.dang_chay.items())
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
    danh_tinh = getattr(request.state, "danh_tinh", None)
    if isinstance(danh_tinh, dict) and danh_tinh.get("loai") == "khach":
        pham_vi = danh_tinh.get("pham_vi", [])
        if "*" not in pham_vi:
            data = [
                m for m in data
                if any(kn in pham_vi for kn in (m.get("kha_nang") or ["chat"]))
            ]
    return {"object": "list", "data": data}


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


def _module_theo_model(request: Request, than: dict):
    """(id, thể hiện) của module có tiền tố ghi trong `model` — 404 rõ ràng nếu không thuộc ai/chưa liên kết."""
    so = _so(request)
    model = than.get("model")
    if isinstance(model, str):
        request.state.model_khach = model  # ghi TRƯỚC mọi lỗi: lượt 404 model_not_found vẫn có model trong nhật ký
    if not isinstance(model, str) or not model:
        raise LoiOpenAI(400, "Thiếu trường «model» (dạng <tiền tố>/<tên>; danh sách ở GET /v1/models)",
                        code="missing_model")
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
    kiem_tra_pham_vi(request, "chat")
    # Đọc thân như dict THÔ (không qua mô hình Pydantic): mô hình sẽ lặng lẽ bỏ trường lạ
    # như chữ ký suy nghĩ hay trường riêng của nhà cung cấp.
    than = await _than_json(request)
    _, m = _module_theo_model(request, than)
    return _tra_ve(*await m.chat(than, bool(than.get("stream"))))


@v1.post("/images/generations")
async def v1_anh(request: Request):
    kiem_tra_pham_vi(request, "anh")
    than = await _than_json(request)
    id_, m = _module_theo_model(request, than)
    _can_phuong_thuc(m, id_, "tạo ảnh", "anh")
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
    return _tra_ve(*await m.anh_sua(yc))


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


@v1_khong_co.api_route("", methods=list(HTTPMethod), include_in_schema=False)
@v1_khong_co.api_route("/{duong:path}", methods=list(HTTPMethod), include_in_schema=False)
async def duong_v1_khong_co(request: Request):
    """Đường có trong router `v1` nhưng sai phương thức → 405 + Allow; không có hẳn → 404 `unknown_endpoint`.

    Suy từ CHÍNH router `v1` (route nào khớp đường mà không khớp phương thức — Match.PARTIAL của Starlette), không từ
    bảng tay: thêm đường mới vào `v1` là tự đúng. Nêu lại đường khách gọi nhưng chỉ phần path (query có thể mang
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


async def _model_cua_module(id_: str, request: Request) -> list[dict]:
    """Danh sách model gốc của module đang chạy (404 nếu không có / chưa liên kết, 502 nếu module lỗi)."""
    so = _so(request)
    if id_ not in so.catalog and id_ not in so.tu_xa:
        raise LoiOpenAI(404, f"Không có module {id_!r}")
    m = so.dang_chay.get(id_)
    if m is None:
        raise LoiOpenAI(404, f"Module {id_!r} chưa liên kết", code="module_not_configured")
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


#: Trần của `truoc_ngay` (100 năm): số lớn hơn không mang nghĩa gì và làm phép trừ ngày tràn.
TRAN_TRUOC_NGAY = 36500


@nhat_ky.delete("")
def xoa_nhat_ky(truoc_ngay: float | None = None):
    if truoc_ngay is None:
        raise LoiOpenAI(400, "Thiếu tham số «truoc_ngay» (số ngày; 0 = xoá mọi bản ghi tới hiện tại)")
    if not 0 <= truoc_ngay <= TRAN_TRUOC_NGAY:  # NaN cũng trượt phép so này
        raise LoiOpenAI(400, f"Tham số «truoc_ngay» phải trong khoảng 0…{TRAN_TRUOC_NGAY}")
    from app import nhat_ky as kho_nhat_ky
    return {"ok": True, "da_xoa": kho_nhat_ky.xoa_cu(truoc_ngay)}


# ───────────── /api/khoa-khach — quản lý khoá khách (chỉ quản trị) ─────────────

@khoa_khach.get("")
def ds_khoa_khach():
    return {"ok": True, "khoa": khoa_khach_mod.danh_sach_khoa()}


@khoa_khach.post("")
async def tao_khoa_khach(request: Request):
    than = await _than_json(request)
    ban_ghi, khoa_tho = khoa_khach_mod.tao_khoa(
        nhan=than.get("nhan", ""),
        pham_vi=than.get("pham_vi"),
    )
    return {"ok": True, "khoa": {**ban_ghi, "khoa": khoa_tho}}


@khoa_khach.patch("/{id_}")
async def sua_khoa_khach(id_: str, request: Request):
    than = await _than_json(request)
    kq = khoa_khach_mod.sua_khoa(
        id_khoa=id_,
        bat=than.get("bat"),
        nhan=than.get("nhan"),
        pham_vi=than.get("pham_vi"),
    )
    return {"ok": True, "khoa": kq}


@khoa_khach.patch("")
async def sua_khoa_khach_khong_path(request: Request):
    than = await _than_json(request)
    id_ = than.get("id")
    if not id_:
        raise LoiOpenAI(400, "Thiếu trường «id» trong thân yêu cầu.")
    kq = khoa_khach_mod.sua_khoa(
        id_khoa=id_,
        bat=than.get("bat"),
        nhan=than.get("nhan"),
        pham_vi=than.get("pham_vi"),
    )
    return {"ok": True, "khoa": kq}


@khoa_khach.delete("/{id_}")
def xoa_khoa_khach(id_: str):
    da_xoa = khoa_khach_mod.xoa_khoa(id_khoa=id_)
    return {"ok": True, "da_xoa": da_xoa}


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
    xac_nhan = bool(than.get("xac_nhan", False))
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

