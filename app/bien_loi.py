"""Biên lỗi của lõi: dưới /v1 MỌI lỗi ra khuôn OpenAI {"error": {"message", "type", "code"}}; lỗi chưa bắt không lộ
chi tiết nội bộ hay khoá — cả trong phản hồi lẫn nhật ký.

Khách /v1 là SDK/ứng dụng nói chuẩn OpenAI: chúng đọc `error.message`/`error.code`; gặp {"detail": …} của FastAPI
thì chỉ thấy lỗi mơ hồ. Lỗi do chính lõi ném đã là `LoiOpenAI`; ở đây bọc nốt ba nguồn còn lại:
  - HTTPException của Starlette (StaticFiles với phương thức lạ…) → giữ mã HTTP và header, `code` suy từ tên chuẩn
    của mã (HTTPStatus: 404 → not_found, 405 → method_not_allowed), không bảng tay;
  - RequestValidationError (tham số sai kiểu) → 400 như OpenAI, nêu trường sai, KHÔNG phản chiếu giá trị khách gửi;
  - Exception chưa bắt → 500 thông điệp chung + mã tra nhật ký.
/api/* và trang web GIỮ khuôn cũ ({"detail"}, 422, «Internal Server Error») — giao diện đang dựa vào đó.

Vì sao lỗi chưa bắt đi bằng middleware chứ không `add_exception_handler(Exception)`: ServerErrorMiddleware của
Starlette gửi phản hồi xong LUÔN ném lại lỗi, và uvicorn in «Exception in ASGI application» kèm traceback có NGUYÊN
chuỗi lỗi — module ném lỗi chứa khoá là khoá vào nhật ký. Middleware ở đây (trong ServerErrorMiddleware, ngoài
ExceptionMiddleware) nuốt lỗi sau khi tự ghi bản đã che: loại lỗi, vị trí mã (tệp:dòng), thông điệp đã che; mọi chuỗi
khách điều khiển (phương thức, đường dẫn, thông điệp) đã thoát ký tự điều khiển — không chèn được dòng giả. Lỗi giữa
một stream đã gửi đầu phản hồi thì không còn gửi được 500: trả về để uvicorn cắt kết nối (khách thấy luồng đứt).
"""

from __future__ import annotations

import logging
import re
import secrets
import traceback
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import kho, xac_thuc
from app.loi_openai import lam_sach_thong_diep, than_loi

log = logging.getLogger("web2api.loi")

#: Chuỗi liền mạch dạng token (khoá API, token, khoá quản trị sinh tự động đều thuộc dạng này) — che hết khi ghi
#: nhật ký. Nguyên tắc theo HÌNH chuỗi, không theo tiền tố nhà cung cấp; che thừa một định danh dài còn hơn sót khoá.
_DANG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,}")


def la_duong_v1(duong: str) -> bool:
    return duong == "/v1" or duong.startswith("/v1/")


def code_theo_ma(ma: int) -> str:
    try:
        return re.sub(r"\W+", "_", HTTPStatus(ma).phrase.lower()).strip("_")
    except ValueError:  # mã ngoài chuẩn
        return str(ma)


def che_chuoi(van_ban: str) -> str:
    """Che khoá quản trị (đúng chuỗi — kể cả khoá người vận hành tự đặt có dấu cách) và mọi chuỗi dạng token."""
    try:
        khoa = xac_thuc.khoa_quan_tri()
    except Exception:  # tệp khoá hỏng/rỗng: vẫn phải ghi được nhật ký lỗi, phần dạng token vẫn được che
        khoa = ""
    if khoa:
        van_ban = van_ban.replace(khoa, kho.che(khoa))
    return _DANG_TOKEN.sub(lambda m: kho.che(m.group()), van_ban)


def thoat_dieu_khien(van_ban: str) -> str:
    """Ký tự không in được (xuống dòng, CR, ESC…) thành dạng thoát (`\\n`, `\\x1b`): chuỗi khách điều khiển vào nhật
    ký không chèn được dòng giả hay mã màu terminal, mà vẫn giữ đủ thông tin để tra."""
    return "".join(k if k.isprintable() else k.encode("unicode_escape").decode("ascii") for k in van_ban)


async def xu_ly_http(request: Request, e: StarletteHTTPException):
    if not la_duong_v1(request.url.path):
        if isinstance(e.detail, str):
            e.detail = lam_sach_thong_diep(e.detail)
        return await http_exception_handler(request, e)
    thong_diep = e.detail if isinstance(e.detail, str) and e.detail else code_theo_ma(e.status_code)
    return JSONResponse(than_loi(e.status_code, thong_diep, code=code_theo_ma(e.status_code)),
                        status_code=e.status_code, headers=e.headers)


async def xu_ly_tham_so(request: Request, e: RequestValidationError):
    if not la_duong_v1(request.url.path):
        return await request_validation_exception_handler(request, e)
    # Chỉ VỊ TRÍ và lời giải thích của lỗi; bỏ "input" (giá trị khách gửi — có thể là chuỗi dài hay khoá).
    phan = ["/".join(str(x) for x in loi.get("loc", ())) + ": " + str(loi.get("msg", "")) for loi in e.errors()]
    return JSONResponse(than_loi(400, "Tham số không hợp lệ — " + "; ".join(phan),
                                 code="invalid_request_error"), status_code=400)


def _mo_ta_loi(e: BaseException) -> str:
    """Chuỗi lỗi (cả nguyên nhân __cause__/__context__) đã che: loại, thông điệp đã che, vị trí mã từng khung."""
    dong, da_thay = [], set()
    while e is not None and id(e) not in da_thay and len(da_thay) < 5:
        da_thay.add(id(e))
        for k in traceback.extract_tb(e.__traceback__):
            dong.append(f"  {k.filename}:{k.lineno} trong {k.name}")
        # Thông điệp có thể mang chuỗi khách gửi: thoát từng phần, còn «\n» nối các dòng ở đây là của lõi.
        dong.append(f"{type(e).__module__}.{type(e).__qualname__}: {thoat_dieu_khien(che_chuoi(str(e))[:500])}")
        e = e.__cause__ or e.__context__
    return "\n".join(dong)


class BatLoiChuaBat:
    """Middleware ASGI thuần (không BaseHTTPMiddleware — cái đó bọc lại luồng thân và stream)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        da_bat_dau = False

        async def gui(tin: Message) -> None:
            nonlocal da_bat_dau
            if tin["type"] == "http.response.start":
                da_bat_dau = True
            await send(tin)

        try:
            await self.app(scope, receive, gui)
        except Exception as e:
            ma_tra = secrets.token_hex(4)
            duong = scope.get("path", "")
            log.error("Lỗi chưa bắt [mã tra %s] %s %s%s\n%s", ma_tra, thoat_dieu_khien(str(scope.get("method"))[:20]),
                      thoat_dieu_khien(che_chuoi(duong[:200])),
                      " (giữa phản hồi đã gửi — cắt kết nối)" if da_bat_dau else "", _mo_ta_loi(e))
            if da_bat_dau:
                return
            if la_duong_v1(duong):
                phan_hoi = JSONResponse(than_loi(500, f"Lỗi nội bộ của web2api (mã tra nhật ký {ma_tra}). Người "
                                                      "vận hành tìm mã này trong nhật ký container để biết chi tiết.",
                                                 code="internal_error"), status_code=500)
            else:
                phan_hoi = PlainTextResponse("Internal Server Error", status_code=500)  # như mặc định của Starlette
            await phan_hoi(scope, receive, send)


def cai_bien_loi(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, xu_ly_http)
    app.add_exception_handler(RequestValidationError, xu_ly_tham_so)
    app.add_middleware(BatLoiChuaBat)
