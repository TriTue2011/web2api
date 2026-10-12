"""Khoá quản trị — cổng duy nhất cho /v1/* và mọi API đổi trạng thái hay lộ cấu hình.

Vì sao bắt buộc ngay từ module đầu tiên: /v1/* tiêu hạn mức Gemini (và mọi dịch vụ
khác) của chủ máy; mở trần trên LAN thì bất kỳ máy nào trong nhà — kể cả thiết bị IoT
bị chiếm — dùng không giới hạn.

Nguồn khoá: biến `WEB2API_ADMIN_KEY`; không đặt thì sinh một lần (secrets.token_urlsafe(32),
256 bit) và lưu `<WEB2API_DATA>/admin_key` quyền 0600. KHÔNG BAO GIỜ in khoá ra log —
log chỉ nêu đường dẫn tệp để người vận hành `docker exec … cat` lấy.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets

from fastapi import Request

from app import kho
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.xac_thuc")


#: Khoá đọc từ tệp, nhớ theo danh tính tệp: {(đường, thiết bị, inode, kích thước, mtime_ns): khoá}. che_chuoi gọi
#: khoa_quan_tri() cho TỪNG chuỗi ghi vào nhật ký (mỗi lượt /v1 vài chuỗi) — một stat thay cho mở-đọc-đóng tệp.
#: Sửa tại chỗ hay thay tệp đổi danh tính nên khoá mới có hiệu lực ngay ở lần gọi sau.
_KHOA_TU_TEP: dict[tuple, str] = {}


def khoa_quan_tri() -> str:
    tu_bien = os.environ.get("WEB2API_ADMIN_KEY", "").strip()
    if tu_bien:
        return tu_bien
    p = kho.thu_muc_du_lieu() / "admin_key"
    try:
        st = os.stat(p)
        danh_tinh = (p, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        khoa = _KHOA_TU_TEP.get(danh_tinh)
        if khoa is None:
            khoa = p.read_text(encoding="utf-8").strip()
            _KHOA_TU_TEP.clear()
            if khoa:
                _KHOA_TU_TEP[danh_tinh] = khoa
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        khoa = secrets.token_urlsafe(32)
        try:
            # O_EXCL: hai tiến trình/luồng cùng khởi động thì chỉ một bên sinh, bên kia đọc lại.
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return khoa_quan_tri()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(khoa + "\n")
        log.warning("Đã sinh khoá quản trị lần đầu, lưu ở %s (quyền 0600). Đọc bằng: docker exec <tên> cat %s",
                    p, p)
        return khoa
    if not khoa:
        # Khoá rỗng mà vẫn chạy thì "Bearer " rỗng cũng khớp → mở toang. Dừng to còn hơn.
        raise RuntimeError(f"{p} rỗng — xoá tệp này để web2api sinh khoá mới, hoặc đặt WEB2API_ADMIN_KEY")
    return khoa


async def can_quan_tri(request: Request) -> None:
    """Phụ thuộc FastAPI: yêu cầu `Authorization: Bearer <khoá quản trị>`; sai/thiếu → 401 kiểu OpenAI."""
    loai, _, gia_tri = request.headers.get("authorization", "").partition(" ")
    gia_tri = gia_tri.strip()
    # compare_digest: thời gian so không phụ thuộc vị trí ký tự sai đầu tiên → không dò khoá theo độ trễ.
    if loai.lower() != "bearer" or not gia_tri or not hmac.compare_digest(
        gia_tri.encode("utf-8"), khoa_quan_tri().encode("utf-8")
    ):
        raise LoiOpenAI(
            401,
            "Thiếu hoặc sai khoá quản trị. Gửi header «Authorization: Bearer <khoá>»; "
            "khoá nằm ở biến WEB2API_ADMIN_KEY hoặc tệp <WEB2API_DATA>/admin_key.",
            loai="invalid_request_error",
            code="invalid_api_key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.nguoi_goi = "quan_tri"  # nhật ký `cuoc_goi` (app/nhat_ky.py): ai đã gọi lượt này
