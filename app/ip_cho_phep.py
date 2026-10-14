"""Cổng IP cho đường quản trị (cẩm nang §25) — một hàm, gọi ở đầu `can_quan_tri` và đầu `POST /api/thiet-lap`
(đường mở tạo admin lần đầu: cũng là đường cấp quyền quản trị nên chịu cùng cổng).

`WEB2API_QT_IP_CHO_PHEP` = danh sách CIDR cách nhau dấu phẩy (vd "192.0.2.0/24,2001:db8::/32").
TRỐNG hoặc không đặt = TẮT, không hạn chế (mặc định, để không khoá nhầm ai).
Có giá trị: yêu cầu vào đường quản trị mà IP hiệu lực KHÔNG nằm trong danh sách
→ 403 `ip_not_allowed` (thân lỗi chuẩn OpenAI, thông báo chung: không nêu IP của
khách, không nêu danh sách), ghi nhật ký kênh `quan_tri` mức cảnh báo (IP đã làm sạch,
đường đã cắt gọn; không ghi khoá), tối đa MỘT dòng mỗi IP mỗi phút để kẻ quét ngoài Internet
không làm ngập sổ nhật ký. Đây không phải lỗi xác thực nên KHÔNG đếm vào bộ giới hạn sai khoá
của `gioi_han`.

IP khách lấy đúng một cách duy nhất: `gioi_han.ip_hieu_luc_phan_loai`
(duyệt X-Forwarded-For từ PHẢI sang TRÁI bỏ qua proxy tin cậy WEB2API_TRUST_PROXY).
- Loopback trực tiếp không kèm X-Forwarded-For = ĐƯỢC PHÉP (healthcheck,
  `docker exec ...` để khôi phục khi tự khoá mình).
- Peer không phân tích được thành IP = TỪ CHỐI (đóng khi lỗi).

Đọc biến MỖI lượt (đổi không cần khởi động lại). Mục CIDR hỏng: bỏ qua và ghi
cảnh báo; có giá trị mà TẤT CẢ đều hỏng → chặn mọi thứ trừ loopback trực tiếp
(đóng khi lỗi), không coi như không hạn chế.

Lưu ý sau proxy (vd Cloudflare Tunnel): peer trực tiếp là gateway Docker/IP LAN
của cloudflared, IP khách thật lấy theo XFF nên admin ở nhà đi qua Cloudflare bị
tính là IP Internet — muốn qua cổng thì dùng địa chỉ LAN trực tiếp, xem
docs/TRIEN-KHAI.md. IPv4-mapped IPv6 (::ffff:192.0.2.5) được so như IPv4.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time

from app import gioi_han, nhat_ky
from app.loi_openai import LoiOpenAI, nhac_lai

log = logging.getLogger("web2api.ip_cho_phep")

TEN_BIEN = "WEB2API_QT_IP_CHO_PHEP"

#: Chống ngập nhật ký: IP -> lúc ghi dòng cảnh báo gần nhất (monotonic). Trần số IP nhớ, bỏ cũ nhất khi vượt.
_GIAN_CACH_GHI_GIAY = 60.0
_TOI_DA_IP_NHO = 1024
_LAN_GHI_CUOI: dict[str, float] = {}


def _doc_danh_sach():
    """Đọc biến MỖI lượt gọi.

    Trả None = tắt (trống/không đặt); list mạng hợp lệ; "hong" = có giá trị
    mà tất cả mục đều hỏng (đóng khi lỗi).
    """
    tho = os.environ.get(TEN_BIEN, "")
    cac_muc = [m.strip() for m in tho.split(",") if m.strip()]
    if not cac_muc:
        return None
    mangs = []
    for muc in cac_muc:
        try:
            mangs.append(ipaddress.ip_network(muc, strict=False))
        except ValueError:
            log.warning("Bỏ qua CIDR hỏng trong %s: %s", TEN_BIEN, nhac_lai(muc))
    if not mangs:
        log.warning("%s có giá trị nhưng tất cả CIDR đều hỏng — chặn mọi IP trừ loopback trực tiếp", TEN_BIEN)
        return "hong"
    return mangs


def kiem_tra_ip_quan_tri(request) -> None:
    """Cổng IP cho đường quản trị — gọi NGAY ĐẦU `can_quan_tri` (và đầu thiết lập lần đầu), trước mọi xác thực.

    Qua → trả None. Không qua → ném LoiOpenAI(403, code="ip_not_allowed").
    """
    mangs = _doc_danh_sach()
    if mangs is None:
        return
    client = request.client
    host = client.host if client else ""
    ip, loai = gioi_han.ip_hieu_luc_phan_loai(host, request.headers.get("x-forwarded-for"))
    if loai == "vong_lap":
        return  # loopback trực tiếp, không XFF: healthcheck / khôi phục khi tự khoá
    if loai == "ip" and isinstance(mangs, list):
        try:
            dia_chi = ipaddress.ip_address(ip)
        except ValueError:
            dia_chi = None
        if dia_chi is not None:
            if isinstance(dia_chi, ipaddress.IPv6Address) and dia_chi.ipv4_mapped is not None:
                dia_chi = dia_chi.ipv4_mapped  # ::ffff:192.0.2.5 so như IPv4
            for mang in mangs:
                try:
                    if dia_chi in mang:
                        return
                except TypeError:
                    continue  # IPv4 so với mạng IPv6 và ngược lại: không khớp
    _tu_choi(request, ip if loai == "ip" else None)


def _nen_ghi(ip_sach: str) -> bool:
    """Tối đa một dòng cảnh báo mỗi IP mỗi `_GIAN_CACH_GHI_GIAY`; bộ nhớ có trần."""
    bay_gio = time.monotonic()
    cu = _LAN_GHI_CUOI.get(ip_sach)
    if cu is not None and bay_gio - cu < _GIAN_CACH_GHI_GIAY:
        return False
    if len(_LAN_GHI_CUOI) >= _TOI_DA_IP_NHO:
        _LAN_GHI_CUOI.pop(next(iter(_LAN_GHI_CUOI)))  # bỏ IP cũ nhất (dict giữ thứ tự chèn)
    _LAN_GHI_CUOI.pop(ip_sach, None)
    _LAN_GHI_CUOI[ip_sach] = bay_gio
    return True


def _tu_choi(request, ip: str | None) -> None:
    """403 ip_not_allowed + (có giới hạn tần suất) một dòng nhật ký kênh quan_tri; không ghi khoá."""
    ip_sach = ip if ip else "khong-xac-dinh"  # ip đã là chuỗi IP hợp lệ hoặc None — không ghi host thô
    if _nen_ghi(ip_sach):
        duong = nhac_lai(request.url.path)  # đường do khách điều khiển: cắt gọn, bỏ ký tự điều khiển
        nhat_ky.ghi(
            "quan_tri",
            "canh_bao",
            f"Từ chối 403 ip_not_allowed trên đường quản trị: {request.method} {duong} từ IP {ip_sach}",
            duong=duong,
            phuong_thuc=request.method,
            ip=ip_sach,
        )
    raise LoiOpenAI(
        403,
        "Không có quyền truy cập.",
        loai="permission_error",
        code="ip_not_allowed",
    )
