"""Giới hạn tần suất khi SAI khoá xác thực — chống đoán khoá (brute-force).

Chỉ đếm các lần xác thực SAI (401) theo IP trong cửa sổ trượt. Vượt ngưỡng
(WEB2API_RL_LOI_XAC_THUC, mặc định 30) → khoá IP đó tạm thời (WEB2API_RL_KHOA_GIAY,
mặc định 60s): mọi yêu cầu từ IP bị khoá nhận 429 kèm Retry-After, thân lỗi theo
khuôn OpenAI của repo. Kiểm khoá TRƯỚC khi chạy xác thực (đầu `nguoi_goi`).

Không đếm 403 (khoá khách thiếu quyền) hay 429 trần đồng thời — chỉ đường 401 của
`nguoi_goi` gọi `ghi_loi_xac_thuc`. Không ghi khoá vào log; chỉ ghi một dòng kênh
`quan_tri` khi khoá/mở khoá IP. Đường xác thực thành công chỉ thêm một tra cứu
dict — không chậm đi đáng kể; so khoá vẫn dùng hmac.compare_digest như cũ.

IP khách: `request.client.host`. Chỉ tin `X-Forwarded-For` khi peer trực tiếp nằm
trong CIDR ở WEB2API_TRUST_PROXY (rỗng = không tin). Loopback trực tiếp (127.0.0.1,
::1) được miễn — healthcheck của Docker chạy từ đó. Lấy IP gốc (leftmost) của XFF.
"""

from __future__ import annotations

import ipaddress
import math
import os
import threading
import time
from collections import OrderedDict, deque

from app import nhat_ky
from app.loi_openai import LoiOpenAI

#: Trần bộ nhớ: tối đa số IP theo dõi, bỏ cái cũ nhất khi vượt.
TOI_DA_IP_MAC_DINH = 10000


def _so_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        return int(os.environ.get(ten, mac_dinh))
    except (TypeError, ValueError):
        return mac_dinh


class GioiHanSaiKhoa:
    """Bộ đếm lỗi 401 theo IP. An toàn luồng (Lock quanh đoạn tới hạn ngắn, không await)."""

    def __init__(self, toi_da_loi: int = 30, cua_so_giay: float = 60.0, khoa_giay: float = 60.0,
                 proxy_tin_cay: tuple = (), toi_da_ip: int = TOI_DA_IP_MAC_DINH,
                 dong_ho=time.monotonic):
        self.toi_da_loi = toi_da_loi
        self.cua_so = cua_so_giay
        self.khoa_giay = khoa_giay
        self.proxy_tin_cay = tuple(proxy_tin_cay)
        self.toi_da_ip = toi_da_ip
        self._dong_ho = dong_ho
        self._khoa_luong = threading.Lock()
        # ip -> {"loi": deque[mốc giây], "khoa_den": giây | None}; OrderedDict để bỏ cũ nhất.
        self._ip: OrderedDict[str, dict] = OrderedDict()

    @property
    def bat(self) -> bool:
        return self.toi_da_loi > 0

    @classmethod
    def tu_moi_truong(cls, dong_ho=time.monotonic) -> "GioiHanSaiKhoa":
        proxy = []
        for muc in os.environ.get("WEB2API_TRUST_PROXY", "").split(","):
            muc = muc.strip()
            if not muc:
                continue
            try:
                proxy.append(ipaddress.ip_network(muc, strict=False))
            except ValueError:
                continue  # CIDR hỏng → bỏ qua, không tin
        return cls(
            toi_da_loi=max(0, _so_nguyen("WEB2API_RL_LOI_XAC_THUC", 30)),
            cua_so_giay=max(1, _so_nguyen("WEB2API_RL_CUA_SO_GIAY", 60)),
            khoa_giay=max(0, _so_nguyen("WEB2API_RL_KHOA_GIAY", 60)),
            proxy_tin_cay=tuple(proxy),
            dong_ho=dong_ho,
        )

    def ip_hieu_luc(self, client_host: str | None, x_forwarded_for: str | None) -> str | None:
        """IP dùng để tính giới hạn; None = miễn (loopback trực tiếp / peer không xác định)."""
        host = (client_host or "").strip()
        if not host:
            return None
        try:
            truc_tiep = ipaddress.ip_address(host)
        except ValueError:
            return None
        if truc_tiep.is_loopback:
            return None
        if x_forwarded_for and self.proxy_tin_cay:
            if any(truc_tiep in mang for mang in self.proxy_tin_cay):
                dau = x_forwarded_for.split(",")[0].strip()
                try:
                    ipaddress.ip_address(dau)
                    return dau
                except ValueError:
                    pass  # XFF hỏng → dùng peer trực tiếp
        return host

    def _ban_ghi(self, ip: str) -> dict:
        ban_ghi = self._ip.get(ip)
        if ban_ghi is None:
            while len(self._ip) >= self.toi_da_ip:
                self._ip.popitem(last=False)  # bỏ IP cũ nhất
            ban_ghi = {"loi": deque(), "khoa_den": None}
            self._ip[ip] = ban_ghi
        else:
            self._ip.move_to_end(ip)
        return ban_ghi

    def kiem_tra(self, ip: str) -> None:
        """IP đang bị khoá → LoiOpenAI 429 kèm Retry-After. Hết hạn → mở (ghi nhật ký một dòng)."""
        if not self.bat:
            return
        with self._khoa_luong:
            ban_ghi = self._ip.get(ip)
            if not ban_ghi or not ban_ghi["khoa_den"]:
                return
            con_lai = ban_ghi["khoa_den"] - self._dong_ho()
            if con_lai <= 0:
                ban_ghi["khoa_den"] = None
                nhat_ky.ghi("quan_tri", "thong_tin",
                            f"Mở khoá IP {ip} (hết hạn khoá {int(self.khoa_giay)}s)", ip=ip)
                return
            raise LoiOpenAI(
                429,
                "Sai khoá xác thực quá nhiều lần — IP tạm bị khoá. Thử lại sau.",
                loai="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(max(1, math.ceil(con_lai)))},
            )

    def ghi_loi(self, ip: str) -> None:
        """Đếm một lần xác thực sai (401). Vượt ngưỡng → khoá tạm thời (ghi nhật ký một dòng)."""
        if not self.bat:
            return
        bay_gio = self._dong_ho()
        with self._khoa_luong:
            ban_ghi = self._ban_ghi(ip)
            moc = ban_ghi["loi"]
            han = bay_gio - self.cua_so
            while moc and moc[0] <= han:
                moc.popleft()
            if ban_ghi["khoa_den"] and ban_ghi["khoa_den"] > bay_gio:
                return  # đang bị khoá: không đếm thêm
            moc.append(bay_gio)
            if len(moc) > self.toi_da_loi:
                ban_ghi["khoa_den"] = bay_gio + self.khoa_giay
                nhat_ky.ghi("quan_tri", "canh_bao",
                            f"Khoá IP {ip} {int(self.khoa_giay)}s do {len(moc)} lần sai khoá "
                            f"xác thực trong {int(self.cua_so)}s", ip=ip)


_BO: GioiHanSaiKhoa | None = None


def _bo() -> GioiHanSaiKhoa:
    global _BO
    if _BO is None:
        _BO = GioiHanSaiKhoa.tu_moi_truong()
    return _BO


def khoi_dong() -> None:
    """Đọc lại cấu hình từ biến môi trường — gọi một lần lúc khởi động (lifespan)."""
    global _BO
    _BO = GioiHanSaiKhoa.tu_moi_truong()


def _lay_ip(request) -> str | None:
    client = request.client
    host = client.host if client else ""
    return _bo().ip_hieu_luc(host, request.headers.get("x-forwarded-for"))


def kiem_tra_truoc_xac_thuc(request) -> None:
    """Gọi ĐẦU `nguoi_goi`: IP đang bị khoá → LoiOpenAI 429 kèm Retry-After."""
    bo = _bo()
    if not bo.bat:
        return
    ip = _lay_ip(request)
    if ip is None:
        return
    bo.kiem_tra(ip)


def ghi_loi_xac_thuc(request) -> None:
    """Gọi ở mỗi đường 401 của `nguoi_goi`: đếm một lần sai theo IP."""
    bo = _bo()
    if not bo.bat:
        return
    ip = _lay_ip(request)
    if ip is None:
        return
    bo.ghi_loi(ip)
