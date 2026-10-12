"""Đọc thân yêu cầu có trần — MỘT bộ đếm byte cho mọi đường đọc thân của lõi (thân JSON và multipart).

Container mặc định chỉ có 256 MB RAM; `request.json()`/`request.body()` của Starlette nạp TRỌN thân không giới hạn.
Hai bước, cùng một chỗ cho mọi người gọi:
  1. Content-Length lớn hơn trần (kể cả số dài bao nhiêu chữ số) → 413 NGAY, chưa đọc byte nào; không phải số
     nguyên không âm → 400.
  2. Đếm từng byte THẬT khi đọc luồng: header có thể nói dối, hoặc vắng hẳn khi khách gửi chunked. Vượt trần giữa
     luồng → 413 ngay khúc đó, phần còn lại không được đọc vào RAM.

Trần đọc MỖI lượt từ biến môi trường (đổi không cần khởi động lại; test đặt trần nhỏ). Lỗi 413 đúng khuôn OpenAI,
nêu trần và tên biến để người vận hành biết nâng ở đâu.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request

from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.than_co_tran")


@dataclass(frozen=True)
class Tran:
    bien: str           # biến môi trường người vận hành dùng để đổi trần
    mac_dinh_mb: float
    pham_vi: str        # thân nào bị tính — nêu trong thông điệp 413

    def so_mb(self) -> float:
        gia_tri = os.environ.get(self.bien, "").strip()
        mb = self.mac_dinh_mb
        if gia_tri:
            try:
                mb = float(gia_tri)
                if not 0 < mb < 1024 * 1024:  # bắt cả nan, inf
                    raise ValueError
            except ValueError:
                log.warning("%s=%r không hợp lệ (cần số MB > 0, hữu hạn), dùng mặc định %s",
                            self.bien, gia_tri[:40], self.mac_dinh_mb)
                mb = self.mac_dinh_mb
        return mb

    def loi_vuot(self, mb: float) -> LoiOpenAI:
        return LoiOpenAI(413, f"Thân yêu cầu vượt trần {mb:g} MB ({self.pham_vi}; "
                              f"người vận hành đổi bằng {self.bien})", code="request_too_large")


#: 25 MB như trần multipart: chat nhiều ảnh base64 nặng ngang tệp gửi kèm.
THAN_JSON = Tran("WEB2API_THAN_TOI_DA_MB", 25.0, "thân JSON")
#: 25 MB — trần tệp của chuẩn OpenAI cho nhận dạng giọng nói.
THAN_MULTIPART = Tran("WEB2API_TEP_TOI_DA_MB", 25.0, "tổng mọi tệp và trường")


def luong_dem(request: Request, tran: Tran) -> AsyncIterator[bytes]:
    """Kiểm Content-Length NGAY khi gọi (chưa đọc byte nào) rồi trả luồng thân có ĐẾM byte thật."""
    mb = tran.so_mb()
    so_byte = int(mb * 1024 * 1024)
    dai = request.headers.get("content-length")
    if dai is not None:
        dai = dai.strip()
        if not (dai.isascii() and dai.isdigit()):
            raise LoiOpenAI(400, "Header Content-Length không hợp lệ", code="invalid_content_length")
        # So số chữ số TRƯỚC khi gọi int(): chuỗi hơn 4300 chữ số làm int() ném ValueError (giới hạn của Python).
        # Nhiều chữ số có nghĩa hơn chính trần thì chắc chắn lớn hơn trần; int() chỉ còn nhận chuỗi ngắn ngang trần.
        so = dai.lstrip("0") or "0"
        if len(so) > len(str(so_byte)) or int(so) > so_byte:
            raise tran.loi_vuot(mb)
    return _dem(request, tran, mb, so_byte)


async def _dem(request: Request, tran: Tran, mb: float, so_byte: int) -> AsyncIterator[bytes]:
    da_doc = 0
    async for khuc in request.stream():
        da_doc += len(khuc)
        if da_doc > so_byte:
            raise tran.loi_vuot(mb)
        yield khuc


async def doc_het(request: Request, tran: Tran) -> bytearray:
    """Trọn thân (≤ trần). bytearray chứ không bytes: `json.loads` nhận thẳng, khỏi thêm một bản sao."""
    ra = bytearray()
    async for khuc in luong_dem(request, tran):
        ra += khuc
    return ra
