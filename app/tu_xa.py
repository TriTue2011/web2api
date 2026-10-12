"""Module TỪ XA: một dịch vụ nói chuẩn OpenAI đăng ký bằng URL — lõi không nạp mã nào của nó.

Dành cho module nặng/GPU/cần gói lõi không có (vd `fw-tach-am`, `vn-translate-gpu` trên .220):
chạy ở container/máy riêng, web2api chỉ chuyển tiếp /v1 bằng CÙNG bộ chuyển tiếp của
lõi (đổi tên model bỏ tiền tố, giữ trường lạ, stream từng dòng, chuẩn hoá lỗi).

Chặn SSRF khi đăng ký: lõi sẽ gửi yêu cầu (kèm khoá) tới URL này theo lệnh của quản trị, nên
không được trỏ vào chính container (loopback — cổng quản trị nội bộ, admin_key…), link-local
169.254.0.0/16 (metadata đám mây: 169.254.169.254 trả khoá IAM) hay địa chỉ không định tuyến.
NHƯNG cho phép LAN riêng tư 10/8, 172.16/12, 192.168/16: module GPU nằm đúng trong mạng nhà
(.220), chặn chúng là chặn đúng mục đích của tính năng. Cho phép cả http:// vì dịch vụ trong
LAN gần như không có chứng chỉ TLS.
Giới hạn đã biết: tên miền được phân giải lúc ĐĂNG KÝ; DNS đổi sau đó (rebinding) không bị kiểm lại.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

from app.chuyen_tiep import ChuyenTiepOpenAI

log = logging.getLogger("web2api.tu_xa")


def cho_phep_loopback_cho_test() -> bool:
    """Chỉ test bật (máy chủ giả chạy trên 127.0.0.1). Production không đặt biến này."""
    return os.environ.get("WEB2API_TEST_CHO_PHEP_LOOPBACK") == "1"


def _dia_chi_bi_chan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return None if cho_phep_loopback_cho_test() else "loopback (chính container)"
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if not (ip.is_private or ip.is_global):
        return "dải địa chỉ đặc biệt"
    return None


async def kiem_url_lan(url: str) -> str:
    """Chuẩn hoá base_url của module từ xa; ValueError nêu lý do nếu bị chặn."""
    s = str(url or "").strip().rstrip("/")
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError("base_url phải dạng http(s)://<máy>[:cổng]/<đường> — thường kết thúc bằng /v1")
    if phan.username or phan.password:
        raise ValueError("base_url không được chứa tài khoản/mật khẩu; dùng trường api_key")
    try:
        ds = [ipaddress.ip_address(phan.hostname)]
    except ValueError:
        try:
            thong_tin = await asyncio.get_running_loop().getaddrinfo(phan.hostname, phan.port or 0,
                                                                     type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {phan.hostname!r}: {e}") from None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        ly_do = _dia_chi_bi_chan(ip)
        if ly_do:
            raise ValueError(f"base_url trỏ vào {ip} — bị chặn: {ly_do}")
    return s


class ModuleTuXa:
    """Thể hiện phục vụ /v1 cho một bản ghi tu_xa.json (cùng giao diện với lớp Module có mã)."""

    def __init__(self, ban_ghi: dict, api_key: str | None):
        # Hợp đồng của module từ xa CHÍNH LÀ chuẩn OpenAI chat (chỉ chuyển tiếp chat + models) → khai như một
        # manifest: loại "ai", cung cấp "chat". Giao diện đọc lời khai này qua /api/tinh-nang như mọi module.
        self.manifest = {"id": ban_ghi["id"], "ten": ban_ghi["ten"], "tien_to": ban_ghi["tien_to"],
                         "loai": "ai", "giao_thuc": "openai", "cung_cap": ["chat"]}
        self.tien_to = ban_ghi["tien_to"]
        self._ct = ChuyenTiepOpenAI(ten=ban_ghi["ten"], tien_to=ban_ghi["tien_to"], base_url=ban_ghi["base_url"],
                                    khoa=[api_key] if api_key else [])

    async def chat(self, body: dict, stream: bool):
        return await self._ct.chat(body, stream)

    async def models(self) -> list[dict]:
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        await self._ct.dung()
