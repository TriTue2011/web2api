"""Lỗi dạng OpenAI {"error": {"message", "type", "code"}} cho mọi API của lõi.

Khách của web2api là thư viện/ứng dụng nói chuẩn OpenAI (SDK openai, Home Assistant…):
chúng đọc `error.message` để hiện cho người dùng. Trả lỗi kiểu FastAPI ({"detail": …})
thì khách chỉ thấy "Unknown error".
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

#: Loại lỗi OpenAI theo mã HTTP; mã ≥500 không có trong bảng → api_error.
LOAI_THEO_MA = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "invalid_request_error",
    413: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
}


def loai_theo_ma(ma: int) -> str:
    return LOAI_THEO_MA.get(ma, "api_error" if ma >= 500 else "invalid_request_error")


def than_loi(ma: int, thong_diep: str, loai: str | None = None, code=None) -> dict:
    return {"error": {"message": thong_diep, "type": loai or loai_theo_ma(ma), "code": code if code is not None else ma}}


class LoiOpenAI(Exception):
    def __init__(self, ma: int, thong_diep: str, loai: str | None = None, code=None, headers: dict | None = None):
        super().__init__(thong_diep)
        self.ma = ma
        self.than = than_loi(ma, thong_diep, loai, code)
        self.headers = headers


async def xu_ly_loi_openai(_: Request, e: LoiOpenAI) -> JSONResponse:
    return JSONResponse(e.than, status_code=e.ma, headers=e.headers)
