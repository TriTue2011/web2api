"""Giới hạn tần suất khi SAI khoá xác thực — chống đoán khoá (brute-force).

Đếm các lần xác thực SAI (401) theo cặp (IP, băm SHA-256 của khoá thử) trong cửa
sổ trượt. Vượt ngưỡng (WEB2API_RL_LOI_XAC_THUC, mặc định 30) → khoá cặp đó tạm
thời (WEB2API_RL_KHOA_GIAY, mặc định 60s): yêu cầu dùng ĐÚNG khoá đã bị khoá
nhận 429 kèm Retry-After, thân lỗi theo khuôn OpenAI của repo.

NV47: đếm theo cặp (IP, băm khoá) thay vì chỉ IP — một kẻ gửi sai MỘT khoá không
khoá được người dùng khoá ĐÚNG cùng IP (sau NAT/proxy). Khoá đúng KHÔNG bị chặn
bởi bộ đếm của khoá sai. Chống dò khoá (mỗi lần một khoá khác nhau): số khoá sai
RIÊNG BIỆT trong cửa sổ vượt TOI_DA_KHOA_SAI_RIENG_BIET (mặc định 10) → khoá IP
như cơ chế cũ. Chỉ lưu băm SHA-256 (cắt 32 ký tự), không lưu khoá thô.

Kiểm khoá TRƯỚC khi chạy xác thực (đầu `nguoi_goi`).

Không đếm 403 (khoá khách thiếu quyền) hay 429 trần đồng thời — chỉ đường 401 của
`nguoi_goi` gọi `ghi_loi_xac_thuc`. Không ghi khoá vào log; chỉ ghi một dòng kênh
`quan_tri` khi khoá/mở khoá. Đường xác thực thành công chỉ thêm một tra cứu
dict — không chậm đi đáng kể; so khoá vẫn dùng hmac.compare_digest như cũ.

IP khách: `request.client.host`. Chỉ tin `X-Forwarded-For` khi peer trực tiếp là loopback hoặc nằm
trong CIDR ở WEB2API_TRUST_PROXY (rỗng = không tin ai ngoài loopback), và lấy khách thật bằng cách
duyệt XFF từ phải sang trái bỏ qua proxy tin cậy (xem `ip_hieu_luc`). Loopback trực tiếp không kèm
XFF được miễn — healthcheck của Docker chạy từ đó.
"""

from __future__ import annotations

import hashlib
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

#: Số khoá sai RIÊNG BIỆT trong cửa sổ thì khoá IP (chống dò khoá — NV47).
TOI_DA_KHOA_SAI_RIENG_BIET_MAC_DINH = 10


def _bam_khoa_thu(authorization: str | None) -> str:
    """Băm SHA-256 (cắt 32 ký tự hex) của khoá thử trong header Authorization.

    Chỉ băm phần sau «Bearer »; header vắng/không phải Bearer → băm chuỗi rỗng.
    Không bao giờ lưu khoá thô vào bộ đếm."""
    loai, _, gia_tri = (authorization or "").partition(" ")
    khoa = gia_tri.strip() if loai.lower() == "bearer" else ""
    return hashlib.sha256(khoa.encode("utf-8")).hexdigest()[:32]


def _so_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        return int(os.environ.get(ten, mac_dinh))
    except (TypeError, ValueError):
        return mac_dinh


class GioiHanSaiKhoa:
    """Bộ đếm lỗi 401 theo cặp (IP, băm khoá thử). An toàn luồng (Lock quanh đoạn tới hạn ngắn, không await)."""

    def __init__(self, toi_da_loi: int = 30, cua_so_giay: float = 60.0, khoa_giay: float = 60.0,
                 proxy_tin_cay: tuple = (), toi_da_ip: int = TOI_DA_IP_MAC_DINH,
                 toi_da_khoa_sai_rieng_biet: int = TOI_DA_KHOA_SAI_RIENG_BIET_MAC_DINH,
                 dong_ho=time.monotonic):
        self.toi_da_loi = toi_da_loi
        self.cua_so = cua_so_giay
        self.khoa_giay = khoa_giay
        self.proxy_tin_cay = tuple(proxy_tin_cay)
        self.toi_da_ip = toi_da_ip
        self.toi_da_khoa_sai_rieng_biet = toi_da_khoa_sai_rieng_biet
        self._dong_ho = dong_ho
        self._khoa_luong = threading.Lock()
        # ip -> {"loi": {bam_khoa: deque[mốc giây]}, "khoa_den": giây | None,
        #        "khoa_cap": {bam_khoa: giây}}; OrderedDict để bỏ cũ nhất.
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

    def _tin_cay(self, ip) -> bool:
        return any(ip in mang for mang in self.proxy_tin_cay)

    def ip_hieu_luc(self, client_host: str | None, x_forwarded_for: str | None) -> str | None:
        """IP dùng để tính giới hạn; None = miễn (loopback trực tiếp không qua proxy / peer không xác định).

        Proxy (Cloudflare, nginx…) NỐI THÊM IP kết nối vào CUỐI `X-Forwarded-For`, nên phần bên trái do chính khách gõ
        và không tin được. Duyệt từ PHẢI sang TRÁI, bỏ qua các proxy tin cậy (WEB2API_TRUST_PROXY) — địa chỉ đầu tiên
        không tin cậy là khách thật. Peer loopback chỉ được miễn khi KHÔNG kèm XFF (healthcheck); có XFF nghĩa là proxy
        chạy cùng máy (vd cloudflared), phải tính theo khách thật chứ không miễn cả luồng."""
        host = (client_host or "").strip()
        if not host:
            return None
        try:
            truc_tiep = ipaddress.ip_address(host)
        except ValueError:
            return None
        if not x_forwarded_for:
            return None if truc_tiep.is_loopback else host
        if not (truc_tiep.is_loopback or self._tin_cay(truc_tiep)):
            return host  # peer không phải proxy tin cậy: bỏ XFF, kẻ gõ header không đổi được IP của mình
        for muc in reversed(x_forwarded_for.split(",")):
            try:
                ip = ipaddress.ip_address(muc.strip())
            except ValueError:
                return host  # XFF hỏng: không đoán, dùng peer trực tiếp
            if not (ip.is_loopback or self._tin_cay(ip)):
                return str(ip)
        return None if truc_tiep.is_loopback else host

    def _ban_ghi(self, ip: str) -> dict:
        ban_ghi = self._ip.get(ip)
        if ban_ghi is None:
            while len(self._ip) >= self.toi_da_ip:
                self._ip.popitem(last=False)  # bỏ IP cũ nhất
            ban_ghi = {"loi": {}, "khoa_den": None, "khoa_cap": {}}
            self._ip[ip] = ban_ghi
        else:
            self._ip.move_to_end(ip)
        return ban_ghi

    def _don_moc_cu(self, ban_ghi: dict, bay_gio: float) -> None:
        han = bay_gio - self.cua_so
        for bam, moc in list(ban_ghi["loi"].items()):
            while moc and moc[0] <= han:
                moc.popleft()
            if not moc:
                del ban_ghi["loi"][bam]

    def kiem_tra(self, ip: str, bam_khoa: str = "") -> None:
        """Cặp (IP, băm khoá) hoặc IP đang bị khoá → LoiOpenAI 429 kèm Retry-After.

        Hết hạn → mở (ghi nhật ký một dòng). Khoá đúng không bao giờ bị chặn bởi
        bộ đếm của khoá sai vì mỗi khoá có bộ đếm riêng (NV47).
        """
        if not self.bat:
            return
        with self._khoa_luong:
            ban_ghi = self._ip.get(ip)
            if not ban_ghi:
                return
            bay_gio = self._dong_ho()
            con_lai = 0.0
            if ban_ghi["khoa_den"]:
                if ban_ghi["khoa_den"] > bay_gio:
                    con_lai = max(con_lai, ban_ghi["khoa_den"] - bay_gio)
                else:
                    ban_ghi["khoa_den"] = None
                    nhat_ky.ghi("quan_tri", "thong_tin",
                                f"Mở khoá IP {ip} (hết hạn khoá {int(self.khoa_giay)}s)", ip=ip)
            den_cap = (ban_ghi["khoa_cap"] or {}).get(bam_khoa)
            if den_cap:
                if den_cap > bay_gio:
                    con_lai = max(con_lai, den_cap - bay_gio)
                else:
                    del ban_ghi["khoa_cap"][bam_khoa]
            if con_lai <= 0:
                return
            raise LoiOpenAI(
                429,
                "Sai khoá xác thực quá nhiều lần — tạm bị khoá. Thử lại sau.",
                loai="rate_limit_error",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(max(1, math.ceil(con_lai)))},
            )

    def liet_ke_khoa(self) -> list[dict]:
        """IP/cặp đang bị khoá (còn hạn) kèm số giây còn lại — cho quản trị xem."""
        bay_gio = self._dong_ho()
        with self._khoa_luong:
            ra = []
            for ip, b in self._ip.items():
                if b["khoa_den"] and b["khoa_den"] > bay_gio:
                    ra.append({"ip": ip, "con_lai_giay": int(b["khoa_den"] - bay_gio) + 1})
                for bam, den in (b["khoa_cap"] or {}).items():
                    if den > bay_gio:
                        ra.append({"ip": ip, "bam_khoa": bam,
                                   "con_lai_giay": int(den - bay_gio) + 1})
            return sorted(ra, key=lambda m: (m["ip"], m.get("bam_khoa") or ""))

    def bo_chan(self, ip: str | None = None) -> int:
        """Bỏ khoá + xoá bộ đếm lỗi của một IP (hoặc mọi IP khi `ip` None). Trả số mục đã bỏ chặn."""
        with self._khoa_luong:
            muc_tieu = [ip] if ip is not None else list(self._ip.keys())
            so = 0
            for k in muc_tieu:
                ban_ghi = self._ip.pop(k, None)
                if ban_ghi is not None and (ban_ghi["khoa_den"] or ban_ghi["khoa_cap"]):
                    so += 1
            return so

    def ghi_loi(self, ip: str, bam_khoa: str = "") -> None:
        """Đếm một lần xác thực sai (401) cho cặp (IP, băm khoá thử).

        Cặp vượt ngưỡng → khoá cặp đó tạm thời (ghi nhật ký một dòng). Số khoá sai
        RIÊNG BIỆT trong cửa sổ vượt ngưỡng dò khoá → khoá IP (chống brute-force
        đổi khoá liên tục). Khoá đúng không bao giờ tăng bộ đếm sai.
        """
        if not self.bat:
            return
        bay_gio = self._dong_ho()
        with self._khoa_luong:
            ban_ghi = self._ban_ghi(ip)
            self._don_moc_cu(ban_ghi, bay_gio)
            if ban_ghi["khoa_den"] and ban_ghi["khoa_den"] > bay_gio:
                return  # IP đang bị khoá vì dò khoá: không đếm thêm
            moc = ban_ghi["loi"].setdefault(bam_khoa, deque())
            moc.append(bay_gio)
            khoa_cap = ban_ghi["khoa_cap"]
            if len(moc) > self.toi_da_loi and not (khoa_cap.get(bam_khoa) or 0) > bay_gio:
                khoa_cap[bam_khoa] = bay_gio + self.khoa_giay
                nhat_ky.ghi("quan_tri", "canh_bao",
                            f"Khoá cặp (IP, khoá) {ip} {int(self.khoa_giay)}s do {len(moc)} lần sai khoá "
                            f"xác thực trong {int(self.cua_so)}s", ip=ip)
                return
            if len(ban_ghi["loi"]) > self.toi_da_khoa_sai_rieng_biet:
                ban_ghi["khoa_den"] = bay_gio + self.khoa_giay
                nhat_ky.ghi("quan_tri", "canh_bao",
                            f"Khoá IP {ip} {int(self.khoa_giay)}s do dò khoá "
                            f"({len(ban_ghi['loi'])} khoá sai riêng biệt trong {int(self.cua_so)}s)",
                            ip=ip)


def ip_hieu_luc_phan_loai(client_host: str | None, x_forwarded_for: str | None) -> tuple[str | None, str]:
    """Như `GioiHanSaiKhoa.ip_hieu_luc` nhưng phân biệt vì sao không có IP.

    Trả (ip, loai):
    - (ip, "vong_lap"): peer là loopback TRỰC TIẾP và không kèm X-Forwarded-For.
    - (ip, "ip"): IP hiệu lực là một địa chỉ IP hợp lệ.
    - (None, "khong_xac_dinh"): peer rỗng/không phân tích được thành IP, hoặc có XFF
      nhưng không rút ra được IP khách (kể cả peer loopback kèm XFF) — bên gọi nên
      ĐÓNG khi lỗi thay vì miễn.

    Không đổi hành vi của `ip_hieu_luc`; chỉ thêm phân loại cho cổng IP đường quản trị.
    """
    host = (client_host or "").strip()
    if not host:
        return None, "khong_xac_dinh"
    try:
        truc_tiep = ipaddress.ip_address(host)
    except ValueError:
        return None, "khong_xac_dinh"
    if not x_forwarded_for and truc_tiep.is_loopback:
        return str(truc_tiep), "vong_lap"
    ip = _bo().ip_hieu_luc(host, x_forwarded_for)
    if ip is None:
        return None, "khong_xac_dinh"
    return ip, "ip"


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


def liet_ke_ip_bi_khoa() -> list[dict]:
    return _bo().liet_ke_khoa()


def bo_chan_ip(ip: str | None = None) -> int:
    return _bo().bo_chan(ip)


def lay_ip_an_toan(request) -> str:
    """Lấy IP khách an toàn qua bộ kiểm proxy XFF (dùng chung cho đăng nhập / trình duyệt từ xa).

    - Duyệt XFF từ phải sang trái qua ip_hieu_luc (chỉ tin khi peer là proxy tin cậy hoặc loopback có proxy).
    - Peer ngoài trực tiếp không tin cậy thì bỏ XFF, dùng chính peer (chống giả mạo XFF).
    - Cắt tối đa 45 ký tự (chuẩn IPv6 tối đa) chống phình bộ nhớ.
    - Fallback '127.0.0.1' nếu không xác định được IP.
    """
    client = getattr(request, "client", None)
    host = client.host if client else ""
    ip = _bo().ip_hieu_luc(host, request.headers.get("x-forwarded-for"))
    if ip:
        return ip[:45]
    if host:
        return host[:45]
    return "127.0.0.1"


def _lay_ip(request) -> str | None:
    client = request.client
    host = client.host if client else ""
    return _bo().ip_hieu_luc(host, request.headers.get("x-forwarded-for"))


def _lay_ip_va_bam(request) -> tuple[str | None, str]:
    ip = _lay_ip(request)
    bam = _bam_khoa_thu(request.headers.get("authorization"))
    return ip, bam


def kiem_tra_truoc_xac_thuc(request) -> None:
    """Gọi ĐẦU `nguoi_goi`: cặp (IP, băm khoá thử) hoặc IP đang bị khoá → LoiOpenAI 429 kèm Retry-After."""
    bo = _bo()
    if not bo.bat:
        return
    ip, bam = _lay_ip_va_bam(request)
    if ip is None:
        return
    bo.kiem_tra(ip, bam)


def ghi_loi_xac_thuc(request) -> None:
    """Gọi ở mỗi đường 401 của `nguoi_goi`: đếm một lần sai cho cặp (IP, băm khoá thử)."""
    bo = _bo()
    if not bo.bat:
        return
    ip, bam = _lay_ip_va_bam(request)
    if ip is None:
        return
    bo.ghi_loi(ip, bam)
