"""Bộ tải URL KHÔNG TIN cho attachment_url, image_url (CHUAN-BAO-MAT-MODULE.md mục 3.6).

Áp dụng cho mọi URL do khách, mô hình, hoặc trang web bên ngoài cung cấp:
- Chỉ cho phép giao thức http/https; từ chối userinfo (tài khoản/mật khẩu trong URL);
- Chặn toàn bộ dải IP riêng tư, loopback, link-local, CGNAT, metadata đám mây, IPv6 ULA;
  (khác với chính sách tu_xa.py vốn cho phép LAN vì URL do quản trị viên khai);
- Phân giải DNS và ghim IP kết nối bằng TransportGhimIp (chống tấn công DNS rebinding);
- Chuyển hướng THỦ CÔNG tối đa 3 chặng, kiểm tra lại đích sau mỗi chặng;
- Tuyệt đối cấm hạ cấp giao thức https -> http khi chuyển hướng;
- Không chuyển tiếp tiêu đề Authorization và Cookie;
- Đọc theo luồng có trần dung lượng (byte) và trần thời gian tổng (giây);
- Ném ngoại lệ riêng LoiTaiUrl(ma, ly_do) mà KHÔNG làm lộ nội dung phản hồi đích.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import urllib.parse
from typing import Callable

import httpx

from app.chuyen_tiep import TransportGhimIp, lam_sach_content_type

log = logging.getLogger("web2api.tai_url_khong_tin")


class LoiTaiUrl(Exception):
    """Ngoại lệ khi tải URL không tin cậy.

    Không chứa thân phản hồi đích để tránh rò rỉ dữ liệu ngoài ý muốn.
    """

    def __init__(self, ma: str, ly_do: str):
        self.ma = ma
        self.ly_do = ly_do
        super().__init__(f"[{ma}] {ly_do}")


def cho_phep_loopback_cho_test() -> bool:
    """Chỉ cho phép loopback khi biến môi trường kiểm thử được bật."""
    return os.environ.get("WEB2API_TEST_CHO_PHEP_LOOPBACK") == "1"


def kiem_ip_khong_tin(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Kiểm tra địa chỉ IP theo chính sách không tin cậy nghiêm ngặt.

    Trả về chuỗi lý do nếu bị chặn, hoặc None nếu hợp lệ.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_loopback:
        return None if cho_phep_loopback_cho_test() else "loopback (chính container/máy chủ)"

    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16 hoặc fe80::/10)"

    if ip.is_private:
        return "địa chỉ riêng tư (LAN 10/8, 172.16/12, 192.168/16)"

    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến, multicast hoặc dự trữ"

    if isinstance(ip, ipaddress.IPv4Address):
        # CGNAT: 100.64.0.0/10
        if ip in ipaddress.IPv4Network("100.64.0.0/10"):
            return "CGNAT (100.64.0.0/10)"
        # 6to4 Relay Anycast (RFC 3068 / RFC 7526 deprecated): 192.88.99.0/24
        if ip in ipaddress.IPv4Network("192.88.99.0/24"):
            return "6to4 relay anycast (192.88.99.0/24)"
        if not ip.is_global:
            return "dải IPv4 không công khai"

    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 ULA: fc00::/7
        if ip in ipaddress.IPv6Network("fc00::/7"):
            return "IPv6 ULA (fc00::/7)"
        # IPv6 site-local (RFC 3879 deprecated): fec0::/10
        if ip in ipaddress.IPv6Network("fec0::/10"):
            return "IPv6 site-local (fec0::/10)"
        # IPv4 nhúng trong 6to4 (2002::/16) và Teredo (2001::/32)
        nhung = [ip.sixtofour] if ip.sixtofour else []
        if ip.teredo:
            nhung += list(ip.teredo)
        for v4 in nhung:
            chan_v4 = kiem_ip_khong_tin(v4)
            if chan_v4:
                return f"IPv4 nhúng {v4}: {chan_v4}"
        if not ip.is_global:
            return "dải IPv6 không công khai"

    return None


def kiem_url_co_ban(url: str, cho_phep_host: set[str] | list[str] | Callable[[str], bool] | None = None) -> urllib.parse.SplitResult:
    """Kiểm tra cú pháp URL, scheme, userinfo và danh sách host cho phép."""
    s = str(url or "").strip()
    try:
        phan = urllib.parse.urlsplit(s)
    except (ValueError, UnicodeError) as e:
        raise LoiTaiUrl("url_khong_hop_le", f"URL không hợp lệ: {e}") from None

    scheme = phan.scheme.lower()
    if scheme not in ("http", "https"):
        raise LoiTaiUrl("scheme_khong_hop_le", f"chỉ chấp nhận http hoặc https, nhận được {phan.scheme!r}")
    if phan.username or phan.password:
        raise LoiTaiUrl("userinfo_bi_chan", "URL không được chứa userinfo (tài khoản/mật khẩu)")
    if not phan.hostname:
        raise LoiTaiUrl("thieu_ten_may", "URL thiếu tên máy chủ")

    # Kiểm tra cổng và độ dài tên máy chủ
    try:
        _ = phan.port
    except ValueError as e:
        raise LoiTaiUrl("url_khong_hop_le", f"cổng URL không hợp lệ: {e}") from None

    if phan.port is not None and not (1 <= phan.port <= 65535):
        raise LoiTaiUrl("url_khong_hop_le", f"cổng URL {phan.port} nằm ngoài khoảng 1-65535")

    try:
        _ = phan.hostname.encode("idna")
    except (UnicodeError, ValueError) as e:
        raise LoiTaiUrl("url_khong_hop_le", f"tên máy chủ không hợp lệ: {e}") from None

    if len(phan.hostname) > 255:
        raise LoiTaiUrl("url_khong_hop_le", f"tên máy chủ quá dài ({len(phan.hostname)} ký tự)")

    if cho_phep_host is not None:
        hop_le = False
        if callable(cho_phep_host):
            hop_le = bool(cho_phep_host(phan.hostname))
        elif isinstance(cho_phep_host, (set, list, tuple)):
            hop_le = phan.hostname in cho_phep_host
        if not hop_le:
            raise LoiTaiUrl("host_khong_cho_phep", f"tên máy {phan.hostname} không nằm trong danh sách cho phép")

    return phan


async def kiem_dns_va_ip(hostname: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Phân giải DNS và kiểm tra toàn bộ địa chỉ IP giải được."""
    try:
        ip = ipaddress.ip_address(hostname)
        ly_do = kiem_ip_khong_tin(ip)
        if ly_do:
            raise LoiTaiUrl("ip_bi_chan", f"IP {hostname} bị chặn: {ly_do}")
        return [ip]
    except ValueError:
        pass

    try:
        tt = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise LoiTaiUrl("loi_dns", f"không phân giải được tên máy {hostname!r}: {e}") from None

    ds: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for x in tt:
        ip_obj = ipaddress.ip_address(x[4][0])
        if ip_obj not in ds:
            ds.append(ip_obj)

    if not ds:
        raise LoiTaiUrl("loi_dns", f"tên máy {hostname!r} không có địa chỉ IP")

    sach = [ip for ip in ds if kiem_ip_khong_tin(ip) is None]
    if not sach:
        ly_do = kiem_ip_khong_tin(ds[0])
        raise LoiTaiUrl("ip_bi_chan", f"tên máy {hostname!r} phân giải ra {ds[0]} — bị chặn: {ly_do}")

    return sach


async def tai_url(
    url: str,
    *,
    tran_byte: int,
    tran_giay: float,
    cho_phep_host: set[str] | list[str] | Callable[[str], bool] | None = None,
    tieu_de: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """Tải nội dung từ URL không tin cậy một cách an toàn.

    - tran_byte: số byte tối đa được đọc (vượt quá ném LoiTaiUrl('qua_dung_luong')).
    - tran_giay: thời gian tổng tối đa cho toàn bộ quá trình (vượt quá ném LoiTaiUrl('qua_thoi_gian')).
    - cho_phep_host: bộ lọc host tuỳ chọn.
    - tieu_de: các tiêu đề bổ sung (luôn loại bỏ Authorization và Cookie).

    Trả về bộ đôi (dữ_liệu_bytes, content_type_sạch).
    """
    if tran_byte <= 0:
        raise ValueError("tran_byte phải lớn hơn 0")
    if tran_giay <= 0:
        raise ValueError("tran_giay phải lớn hơn 0")

    try:
        phan = kiem_url_co_ban(url, cho_phep_host)
        cong_khoi_tao = phan.port or (443 if phan.scheme.lower() == "https" else 80)
        await kiem_dns_va_ip(phan.hostname, cong_khoi_tao)

        goc_origin = (phan.scheme.lower(), phan.hostname.lower(), cong_khoi_tao)

        # Lọc tiêu đề nhạy cảm: không bao giờ gửi Authorization hay Cookie
        # Luôn ép Accept-Encoding: identity để chống bom nén gzip
        hdrs: dict[str, str] = {"accept-encoding": "identity"}
        if tieu_de:
            for k, v in tieu_de.items():
                k_lower = k.lower().strip()
                if k_lower in ("authorization", "cookie", "proxy-authorization", "accept-encoding"):
                    continue
                hdrs[k] = v

        transport = TransportGhimIp(kiem_ip=kiem_ip_khong_tin)
        client = httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=tran_giay)

        try:
            async with asyncio.timeout(tran_giay):
                current_url = url
                so_chuyen_huong_con_lai = 3

                while True:
                    try:
                        req = client.build_request("GET", current_url, headers=hdrs)
                        res = await client.send(req, stream=True)
                    except httpx.ConnectError as e:
                        from app.chuyen_tiep import LoiDichBiChan
                        if isinstance(e, LoiDichBiChan):
                            raise LoiTaiUrl("ip_bi_chan", str(e)) from None
                        raise LoiTaiUrl("loi_ket_noi", f"không thể kết nối: {e}") from None
                    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as e:
                        raise LoiTaiUrl("url_khong_hop_le", f"URL không hợp lệ: {e}") from None
                    except httpx.HTTPError as e:
                        raise LoiTaiUrl("loi_mang", f"lỗi mạng: {type(e).__name__}") from None

                    # Xử lý chuyển hướng thủ công (301, 302, 303, 307, 308)
                    if res.status_code in (301, 302, 303, 307, 308):
                        if so_chuyen_huong_con_lai <= 0:
                            await res.aclose()
                            raise LoiTaiUrl("chuyen_huong_qua_nhieu", "vượt quá 3 chặng chuyển hướng")
                        so_chuyen_huong_con_lai -= 1

                        loc = res.headers.get("location")
                        if not loc:
                            await res.aclose()
                            raise LoiTaiUrl("thieu_location", "phản hồi chuyển hướng thiếu tiêu đề Location")

                        try:
                            tiep = urllib.parse.urljoin(current_url, loc)
                        except (ValueError, UnicodeError) as e:
                            await res.aclose()
                            raise LoiTaiUrl("url_khong_hop_le", f"Location chuyển hướng không hợp lệ: {e}") from None

                        try:
                            cur_scheme = urllib.parse.urlsplit(current_url).scheme.lower()
                            nxt_phan_tam = urllib.parse.urlsplit(tiep)
                            nxt_scheme = nxt_phan_tam.scheme.lower()
                        except (ValueError, UnicodeError) as e:
                            await res.aclose()
                            raise LoiTaiUrl("url_khong_hop_le", f"Location chuyển hướng không hợp lệ: {e}") from None

                        if cur_scheme == "https" and nxt_scheme == "http":
                            await res.aclose()
                            raise LoiTaiUrl("ha_scheme", "từ chối chuyển hướng hạ cấp từ https xuống http")

                        nxt_phan = kiem_url_co_ban(tiep, cho_phep_host)
                        nxt_port = nxt_phan.port or (443 if nxt_scheme == "https" else 80)
                        await kiem_dns_va_ip(nxt_phan.hostname, nxt_port)

                        # B8: Khi đổi origin, gỡ toàn bộ tiêu đề do người gọi truyền (chỉ giữ accept-encoding)
                        nxt_origin = (nxt_scheme, nxt_phan.hostname.lower(), nxt_port)
                        if nxt_origin != goc_origin:
                            hdrs = {"accept-encoding": "identity"}

                        await res.aclose()
                        current_url = tiep
                        continue

                    # Không phải chuyển hướng: phải là 2xx (thường là 200)
                    if not (200 <= res.status_code < 300):
                        await res.aclose()
                        raise LoiTaiUrl("loi_http", f"mã trạng thái HTTP {res.status_code}")

                    # B1: Từ chối phản hồi có Content-Encoding khác identity (chống bom gzip)
                    ce = (res.headers.get("content-encoding") or "").strip().lower()
                    if ce and ce != "identity":
                        await res.aclose()
                        raise LoiTaiUrl(
                            "content_encoding_khong_cho_phep",
                            f"từ chối phản hồi có Content-Encoding {ce!r}, chỉ chấp nhận identity",
                        )

                    # B1: Đọc thân theo luồng nhị phân thô (aiter_raw) có trần dung lượng
                    dem = bytearray()
                    try:
                        async for khuc in res.aiter_raw():
                            if len(dem) + len(khuc) > tran_byte:
                                raise LoiTaiUrl("qua_dung_luong", f"vượt trần dung lượng {tran_byte} bytes")
                            dem.extend(khuc)
                    finally:
                        await res.aclose()

                    ct = lam_sach_content_type(res.headers.get("content-type", "application/octet-stream"))
                    return bytes(dem), ct

        except TimeoutError:
            raise LoiTaiUrl("qua_thoi_gian", f"vượt trần thời gian {tran_giay}s") from None
        finally:
            await client.aclose()

    except LoiTaiUrl:
        raise
    except (ValueError, UnicodeError) as e:
        raise LoiTaiUrl("url_khong_hop_le", f"URL không hợp lệ: {e}") from None
    except Exception as e:
        raise LoiTaiUrl("loi_khong_xac_dinh", f"lỗi khi tải URL: {type(e).__name__}") from None
