"""Đọc thân multipart/form-data của các đường /v1 nhận tệp thành `YeuCauDaPhan` — module KHÔNG tự phân tích multipart.

Trần: TỔNG thân yêu cầu (tệp + trường chữ + phần bao của multipart) ≤ WEB2API_TEP_TOI_DA_MB (mặc định 25 — trần tệp
của chuẩn OpenAI cho nhận dạng giọng nói). Kiểm Content-Length TRƯỚC khi đọc (413 ngay, không tốn đĩa) rồi ĐẾM từng
byte thật khi đọc: header có thể nói dối, hoặc vắng hẳn khi khách gửi chunked.

Không dùng `request.form()` của Starlette: trần `max_part_size` của nó chỉ áp cho trường CHỮ (phần tệp không giới
hạn), và lỗi của nó ra dạng {"detail": …} chứ không phải dạng lỗi OpenAI mà khách đọc được.

Tệp tạm: Starlette giữ mỗi phần tệp trong SpooledTemporaryFile (RAM tới 1 MB, quá thì ra tệp tạm ở thư mục tạm mặc
định). Lõi đọc mỗi tệp đúng MỘT lần thành bytes rồi đóng mọi tệp tạm trong `finally` — kể cả khi đọc hỏng giữa
chừng — TRƯỚC khi trả cho đường gọi module. Bộ nhớ đỉnh vì vậy ≈ một lần tổng dung lượng tệp (bản bytes đưa module),
không cộng thêm bản trong tệp tạm.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from fastapi import Request
from starlette.formparsers import MultiPartException, MultiPartParser

from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.yeu_cau_da_phan")

TEP_TOI_DA_MB_MAC_DINH = 25.0
#: Sửa ảnh của chuẩn OpenAI nhận tới 16 ảnh + 1 mặt nạ; dư ra một chút, vẫn chặn kiểu gửi hàng nghìn phần nhỏ.
SO_TEP_TOI_DA = 20
SO_TRUONG_TOI_DA = 100
#: Một trường CHỮ (prompt dài nhất của chuẩn OpenAI ~32 000 ký tự ≈ 128 KB UTF-8).
TRUONG_TOI_DA_BYTE = 1024 * 1024


@dataclass(frozen=True)
class TepTai:
    ten_truong: str    # tên trường, đã bỏ hậu tố «[]» (image[] → image)
    ten_tep: str       # tên tệp KHÁCH KHAI — không tin được, không dùng làm đường dẫn
    content_type: str  # khách khai; vắng thì "application/octet-stream"
    noi_dung: bytes


@dataclass(frozen=True)
class YeuCauDaPhan:
    #: Trường chữ, tên đã bỏ hậu tố «[]». Gửi MỘT lần và không có «[]» → str; có «[]» hoặc lặp → list[str] theo
    #: thứ tự gửi — đúng hình thân JSON của cùng tham số OpenAI (timestamp_granularities[] → danh sách).
    truong: dict[str, str | list[str]]
    tep: list[TepTai]

    def tep_ten(self, ten: str) -> list[TepTai]:
        return [t for t in self.tep if t.ten_truong == ten]


def la_multipart(request: Request) -> bool:
    return request.headers.get("content-type", "").partition(";")[0].strip().lower() == "multipart/form-data"


def tran_byte() -> int:
    """Trần tổng thân multipart, đọc MỖI lượt (đổi biến môi trường không cần khởi động lại; test đặt trần nhỏ)."""
    gia_tri = os.environ.get("WEB2API_TEP_TOI_DA_MB", "").strip()
    mb = TEP_TOI_DA_MB_MAC_DINH
    if gia_tri:
        try:
            mb = float(gia_tri)
            if not 0 < mb < 1024 * 1024:  # bắt cả nan, inf
                raise ValueError
        except ValueError:
            log.warning("WEB2API_TEP_TOI_DA_MB=%r không hợp lệ (cần số MB > 0, hữu hạn), dùng mặc định %s",
                        gia_tri[:40], TEP_TOI_DA_MB_MAC_DINH)
            mb = TEP_TOI_DA_MB_MAC_DINH
    return int(mb * 1024 * 1024)


def _qua_tran(tran: int) -> LoiOpenAI:
    return LoiOpenAI(413, f"Thân yêu cầu vượt trần {tran / 1024 / 1024:g} MB (tổng mọi tệp và trường; "
                          "người vận hành đổi bằng WEB2API_TEP_TOI_DA_MB)", code="request_too_large")


async def doc_multipart(request: Request) -> YeuCauDaPhan:
    if not la_multipart(request):
        kieu = request.headers.get("content-type", "").partition(";")[0].strip()[:80] or "không có Content-Type"
        raise LoiOpenAI(415, f"{request.url.path} cần thân multipart/form-data (như SDK openai gửi), nhận: {kieu}",
                        code="unsupported_media_type")
    tran = tran_byte()
    dai = request.headers.get("content-length")
    if dai is not None:
        if not dai.strip().isdigit():
            raise LoiOpenAI(400, "Header Content-Length không hợp lệ", code="invalid_content_length")
        if int(dai) > tran:
            raise _qua_tran(tran)

    async def dem():
        da_doc = 0
        async for khuc in request.stream():
            da_doc += len(khuc)
            if da_doc > tran:  # MultiPartParser đóng mọi tệp tạm đã mở rồi ném tiếp lỗi này nguyên dạng
                raise _qua_tran(tran)
            yield khuc

    form = None
    try:
        try:
            form = await MultiPartParser(request.headers, dem(), max_files=SO_TEP_TOI_DA, max_fields=SO_TRUONG_TOI_DA,
                                         max_part_size=TRUONG_TOI_DA_BYTE).parse()
        except MultiPartException as e:
            raise LoiOpenAI(400, f"Thân multipart không hợp lệ: {e.message}", code="invalid_multipart") from None
        gom: dict[str, list[str]] = {}
        co_ngoac: set[str] = set()
        tep: list[TepTai] = []
        for ten, gia_tri in form.multi_items():
            goc = ten.removesuffix("[]")
            if isinstance(gia_tri, str):
                gom.setdefault(goc, []).append(gia_tri)
                if goc != ten:
                    co_ngoac.add(goc)
            else:
                tep.append(TepTai(goc, gia_tri.filename or "", gia_tri.content_type or "application/octet-stream",
                                  await gia_tri.read()))
        truong = {k: v if (k in co_ngoac or len(v) > 1) else v[0] for k, v in gom.items()}
        return YeuCauDaPhan(truong, tep)
    finally:
        if form is not None:
            await form.close()
