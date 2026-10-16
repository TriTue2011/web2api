"""Lỗi dạng OpenAI {"error": {"message", "type", "code"}} cho mọi API của lõi.

Khách của web2api là thư viện/ứng dụng nói chuẩn OpenAI (SDK openai, ứng dụng nhà thông minh…):
chúng đọc `error.message` để hiện cho người dùng. Trả lỗi kiểu FastAPI ({"detail": …})
thì khách chỉ thấy "Unknown error".
"""

from __future__ import annotations

import re

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

#: Trần ký tự cho toàn bộ thông báo lỗi trả về khách. Đủ lớn để mọi thông báo lõi hiện có
#: không bị cắt, đủ nhỏ để không phản chiếu chuỗi khách điều khiển hàng nghìn ký tự.
TRAN_THONG_DIEP = 400
#: Trần cho một token liền mạch (không khoảng trắng) tránh phản chiếu chuỗi ID/đường dẫn độc hại.
TRAN_TOKEN = 120

#: Mã thoát ANSI terminal (\x1b[...m)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
#: Ký tự xuống dòng, về đầu dòng, tab
_DIEU_KHIEN_KHOANG_TRANG = re.compile(r"[\r\n\t]+")
#: Khoảng trắng lặp (2+ dấu cách liên tiếp) — thu gọn thành một.
_KHOANG_TRANG_LAP = re.compile(r"  +")
#: Token liền mạch không có khoảng trắng dài quá trần TRAN_TOKEN (120 ký tự)
_TOKEN_DAI = re.compile(r"\S{121,}")


def lam_sach_thong_diep(thong_diep: str | None, tran: int = TRAN_THONG_DIEP) -> str:
    """Làm sạch thông báo lỗi trước khi trả về khách hoặc ghi nhật ký:
    - Bỏ mã màu/thoát ANSI, ký tự điều khiển (\\r, \\n, \\t, null byte...).
    - Thu gọn khoảng trắng lặp.
    - Cắt các token liền mạch không khoảng trắng dài quá TRAN_TOKEN (120 ký tự).
    - Cắt toàn bộ thông báo ở trần `tran` (mặc định 400 ký tự).

    Chuỗi hợp lệ ngắn không đổi một ký tự; chuỗi rỗng hoặc None trả về chuỗi rỗng."""
    if not thong_diep:
        return ""
    # 1. Bỏ mã màu ANSI
    sach = _ANSI_ESCAPE.sub("", str(thong_diep))
    # 2. Đổi ký tự xuống dòng, tab thành dấu cách để không dính chữ
    sach = _DIEU_KHIEN_KHOANG_TRANG.sub(" ", sach)
    # 3. Loại bỏ mọi ký tự không in được khác (null byte, backspace, chuông...)
    sach = "".join(k for k in sach if k.isprintable())
    # 4. Thu gọn khoảng trắng lặp và cắt khoảng trắng đầu/cuối
    sach = _KHOANG_TRANG_LAP.sub(" ", sach).strip()
    # 5. Cắt token liền mạch dài > TRAN_TOKEN (chống phản chiếu chuỗi dài khách gửi)
    sach = _TOKEN_DAI.sub(lambda m: m.group()[:TRAN_TOKEN] + "…", sach)
    # 6. Cắt trần toàn bộ thông báo
    if len(sach) > tran:
        sach = sach[:tran] + "…"
    return sach


def nhac_lai(chuoi: str | None, tran: int = TRAN_TOKEN) -> str:
    """Chuỗi khách điều khiển (đường dẫn, id, tên model...) để nêu lại trong thông báo lỗi hoặc log:
    bỏ ký tự điều khiển, cắt trần (mặc định TRAN_TOKEN = 120 ký tự)."""
    return lam_sach_thong_diep(chuoi, tran=tran)


def loai_theo_ma(ma: int) -> str:
    return LOAI_THEO_MA.get(ma, "api_error" if ma >= 500 else "invalid_request_error")


def than_loi(ma: int, thong_diep: str, loai: str | None = None, code=None) -> dict:
    return {"error": {"message": lam_sach_thong_diep(thong_diep), "type": loai or loai_theo_ma(ma), "code": code if code is not None else ma}}


class LoiOpenAI(Exception):
    def __init__(self, ma: int, thong_diep: str, loai: str | None = None, code=None, headers: dict | None = None):
        super().__init__(thong_diep)
        self.ma = ma
        self.than = than_loi(ma, thong_diep, loai, code)
        self.headers = headers

    @property
    def status_code(self) -> int:
        return self.ma

    @property
    def code(self) -> Any:
        return self.than.get("error", {}).get("code")


async def xu_ly_loi_openai(_: Request, e: LoiOpenAI) -> JSONResponse:
    return JSONResponse(e.than, status_code=e.ma, headers=e.headers)
