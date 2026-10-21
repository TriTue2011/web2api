"""Nối KẾT QUẢ đăng nhập vào MODULE SỞ HỮU tài khoản (NV210, điều phối nối).

Module đăng nhập (executor) có thể trả ``jwt_moi`` = {access_token, ho_so, email}. Lõi đưa nó cho
module SỞ HỮU tài khoản (``module_id`` của bản ghi — DỮ LIỆU, không phải tên cứng) nếu module đó khai
phương thức ``gan_jwt_tu_dang_nhap``; lõi không biết module nào, chỉ biết khả năng. Token KHÔNG đi
tiếp ra phản hồi/log: ``jwt_moi`` bị xoá khỏi kết quả ngay sau khi xử lý."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("web2api.gan_ket_qua_dang_nhap")


def ap_dung(so: Any, kq: Any, module_so_huu: str) -> bool:
    """True khi đã gắn JWT mới vào module sở hữu. Không bao giờ ném: lỗi gắn không được làm
    hỏng kết quả đăng nhập (người dùng vẫn thấy «đã đăng nhập»), chỉ ghi log không kèm token."""
    jwt = getattr(kq, "jwt_moi", None)
    try:
        object.__setattr__(kq, "jwt_moi", None)  # token không đi tiếp
    except Exception:  # noqa: BLE001 — kq lạ không cho gán: bỏ qua, không lộ gì thêm
        pass
    if not isinstance(jwt, dict) or not module_so_huu:
        return False
    mod = so.dang_chay.get(module_so_huu)
    ham = getattr(mod, "gan_jwt_tu_dang_nhap", None) if mod is not None else None
    if not callable(ham):
        return False
    try:
        kq_gan = ham(str(jwt.get("email") or ""), str(jwt.get("access_token") or ""), str(jwt.get("ho_so") or ""))
    except Exception as e:  # noqa: BLE001
        log.warning("gắn JWT mới vào module sở hữu lỗi: %s", type(e).__name__)
        return False
    ok = bool(isinstance(kq_gan, dict) and kq_gan.get("ok"))
    if not ok:
        log.warning("gắn JWT mới không thành công: %s", (kq_gan or {}).get("ma_loi") if isinstance(kq_gan, dict) else "?")
    return ok
