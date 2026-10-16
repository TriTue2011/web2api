"""Kiểm thử app/tai_url_khong_tin.py: bộ tải URL không tin cậy."""

import asyncio
import ipaddress
import socket
import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from app.tai_url_khong_tin import (
    LoiTaiUrl,
    cho_phep_loopback_cho_test,
    kiem_ip_khong_tin,
    tai_url,
)
from gia import MayChuThat


def test_kiem_ip_chan_loopback_khi_khong_co_bien_moi_truong(monkeypatch):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    assert not cho_phep_loopback_cho_test()

    v4_loop = ipaddress.ip_address("127.0.0.1")
    v6_loop = ipaddress.ip_address("::1")
    assert kiem_ip_khong_tin(v4_loop) is not None
    assert "loopback" in kiem_ip_khong_tin(v4_loop)
    assert kiem_ip_khong_tin(v6_loop) is not None


def test_kiem_ip_cho_phep_loopback_cho_test(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    assert cho_phep_loopback_cho_test()

    v4_loop = ipaddress.ip_address("127.0.0.1")
    v6_loop = ipaddress.ip_address("::1")
    assert kiem_ip_khong_tin(v4_loop) is None
    assert kiem_ip_khong_tin(v6_loop) is None


@pytest.mark.parametrize("ip_str,mo_ta", [
    ("10.0.0.1", "LAN 10/8"),
    ("172.16.0.1", "LAN 172.16/12"),
    ("172.31.255.254", "LAN 172.16/12"),
    ("192.168.1.1", "LAN 192.168/16"),
    ("169.254.169.254", "link-local / metadata"),
    ("100.64.0.1", "CGNAT"),
    ("100.127.255.254", "CGNAT"),
    ("0.0.0.0", "không định tuyến"),
    ("224.0.0.1", "multicast"),
    ("240.0.0.1", "reserved"),
    ("192.88.99.1", "6to4 relay anycast (RFC 3068 / RFC 7526)"),
    ("fc00::1", "IPv6 ULA"),
    ("fd12:3456:789a::1", "IPv6 ULA"),
    ("fec0::1", "IPv6 site-local (RFC 3879)"),
    ("fe80::1", "IPv6 link-local"),
    ("::ffff:192.168.1.1", "IPv4-mapped IPv6 private"),
    ("2002:a9fe:a9fe::", "6to4 mang link-local 169.254.169.254"),
    ("2002:0a00:0001::", "6to4 mang 10.0.0.1"),
    ("2001:0:a9fe:a9fe:0:0:0:1", "Teredo mang 169.254.169.254"),
])
def test_kiem_ip_chan_cac_dai_rieng_va_dac_biet(ip_str, mo_ta, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    ip = ipaddress.ip_address(ip_str)
    ly_do = kiem_ip_khong_tin(ip)
    assert ly_do is not None, f"IP {ip_str} ({mo_ta}) phải bị chặn dù loopback bật"


@pytest.mark.parametrize("ip_str,mo_ta", [
    ("::ffff:127.0.0.1", "IPv4-mapped IPv6 loopback"),
    ("2002:7f00:0001::", "6to4 mang loopback"),
    ("2001:0:7f00:1:0:0:0:1", "Teredo mang loopback"),
])
def test_kiem_ip_chan_ipv6_nhung_loopback(ip_str, mo_ta, monkeypatch):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    ip = ipaddress.ip_address(ip_str)
    ly_do = kiem_ip_khong_tin(ip)
    assert ly_do is not None, f"IP {ip_str} ({mo_ta}) phải bị chặn khi loopback tắt"


@pytest.mark.parametrize("url,ma_loi", [
    ("ftp://example.com/tep.txt", "scheme_khong_hop_le"),
    ("file:///etc/passwd", "scheme_khong_hop_le"),
    ("data:text/plain;base64,QUJD", "scheme_khong_hop_le"),
    ("http://admin:matkhau@example.com/anh.png", "userinfo_bi_chan"),
    ("http://user@example.com/anh.png", "userinfo_bi_chan"),
    ("http://:pass@example.com/anh.png", "userinfo_bi_chan"),
    ("http:///duong/dan", "thieu_ten_may"),
    ("http://host:abc/", "url_khong_hop_le"),
    ("http://host:99999/", "url_khong_hop_le"),
    ("http://[::1/", "url_khong_hop_le"),
    ("http://" + "a" * 300 + ".com/anh.png", "url_khong_hop_le"),
])
def test_tu_choi_url_sai_cu_phap_hoac_userinfo(url, ma_loi):
    with pytest.raises(LoiTaiUrl) as exc:
        asyncio.run(tai_url(url, tran_byte=1024, tran_giay=5.0))
    assert exc.value.ma == ma_loi


def test_tu_choi_host_ngoai_danh_sach_cho_phep(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    with pytest.raises(LoiTaiUrl) as exc:
        asyncio.run(
            tai_url(
                "http://127.0.0.1:8000/anh.png",
                tran_byte=1024,
                tran_giay=5.0,
                cho_phep_host={"example.com", "cdn.example.com"},
            )
        )
    assert exc.value.ma == "host_khong_cho_phep"


def test_tai_url_thanh_cong_va_khong_gui_auth_cookie(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    da_nhan_headers = {}

    async def endpoint(req: Request):
        da_nhan_headers.update(dict(req.headers))
        return PlainTextResponse("NOI_DUNG_ANH_GIA", media_type="image/png")

    app = Starlette(routes=[Route("/anh.png", endpoint)])
    with MayChuThat(app) as srv:
        url = f"{srv.url}/anh.png"
        data, ct = asyncio.run(
            tai_url(
                url,
                tran_byte=1024,
                tran_giay=5.0,
                tieu_de={
                    "Authorization": "Bearer bi_mat_khong_duoc_gui",
                    "Cookie": "session=bi_mat_khong_duoc_gui",
                    "Proxy-Authorization": "Basic bi_mat",
                    "X-Tuy-Chon": "gia_tri_tot",
                },
            )
        )
        assert data == b"NOI_DUNG_ANH_GIA"
        assert ct == "image/png"

        # Kiểm tra tiêu đề nhạy cảm không lọt tới máy chủ đích
        assert "authorization" not in da_nhan_headers
        assert "cookie" not in da_nhan_headers
        assert "proxy-authorization" not in da_nhan_headers
        assert da_nhan_headers.get("x-tuy-chon") == "gia_tri_tot"


def test_tai_url_cat_khi_vuot_tran_byte(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def endpoint(req: Request):
        return PlainTextResponse("A" * 5000, media_type="text/plain")

    app = Starlette(routes=[Route("/lon", endpoint)])
    with MayChuThat(app) as srv:
        url = f"{srv.url}/lon"
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(url, tran_byte=1000, tran_giay=5.0))
        assert exc.value.ma == "qua_dung_luong"


def test_tai_url_cat_khi_vuot_tran_thoi_gian(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def endpoint(req: Request):
        await asyncio.sleep(1.0)
        return PlainTextResponse("xong", media_type="text/plain")

    app = Starlette(routes=[Route("/cham", endpoint)])
    with MayChuThat(app) as srv:
        url = f"{srv.url}/cham"
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(url, tran_byte=1000, tran_giay=0.2))
        assert exc.value.ma == "qua_thoi_gian"


def test_tai_url_loi_http_khong_lo_than_phan_hoi(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    bi_mat = "BI_MAT_NOI_BO_12345_KHONG_DUOC_LO"

    async def endpoint(req: Request):
        return PlainTextResponse(f"Lỗi: {bi_mat}", status_code=500)

    app = Starlette(routes=[Route("/loi", endpoint)])
    with MayChuThat(app) as srv:
        url = f"{srv.url}/loi"
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(url, tran_byte=1000, tran_giay=5.0))
        assert exc.value.ma == "loi_http"
        assert "500" in exc.value.ly_do
        # Đảm bảo thân phản hồi không có trong lỗi
        assert bi_mat not in str(exc.value)


def test_tai_url_chuyen_huong_thanh_cong(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def r1(req: Request):
        return RedirectResponse(url="/r2")

    async def r2(req: Request):
        return RedirectResponse(url="/dich")

    async def dich(req: Request):
        return PlainTextResponse("DICH_CUOI", media_type="text/plain")

    app = Starlette(routes=[
        Route("/r1", r1),
        Route("/r2", r2),
        Route("/dich", dich),
    ])
    with MayChuThat(app) as srv:
        data, ct = asyncio.run(tai_url(f"{srv.url}/r1", tran_byte=1000, tran_giay=5.0))
        assert data == b"DICH_CUOI"


def test_tai_url_chan_chuyen_huong_qua_3_chang(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def vong(req: Request):
        ch = int(req.query_params.get("n", "1"))
        return RedirectResponse(url=f"/vong?n={ch + 1}")

    app = Starlette(routes=[Route("/vong", vong)])
    with MayChuThat(app) as srv:
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(f"{srv.url}/vong?n=1", tran_byte=1000, tran_giay=5.0))
        assert exc.value.ma == "chuyen_huong_qua_nhieu"


def test_tai_url_chan_chuyen_huong_sang_ip_rieng(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def chuyen_lan(req: Request):
        return RedirectResponse(url="http://192.168.1.1/router-login")

    app = Starlette(routes=[Route("/ssrf", chuyen_lan)])
    with MayChuThat(app) as srv:
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(f"{srv.url}/ssrf", tran_byte=1000, tran_giay=5.0))
        assert exc.value.ma == "ip_bi_chan"


def test_tai_url_chan_chuyen_huong_ha_scheme_that(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    from unittest.mock import patch

    # Dùng httpx.MockTransport chính thống thay vì mock hàm send của client
    def mock_handler(request: httpx.Request):
        if request.url.scheme == "https":
            return httpx.Response(
                302,
                headers={"location": "http://example.com/khong_ma_hoa"},
            )
        return httpx.Response(200, text="OK")

    transport = httpx.MockTransport(mock_handler)

    with patch("app.tai_url_khong_tin.kiem_dns_va_ip", return_value=[ipaddress.ip_address("1.1.1.1")]):
        with patch("app.tai_url_khong_tin.TransportGhimIp", return_value=transport):
            with pytest.raises(LoiTaiUrl) as exc:
                asyncio.run(
                    tai_url("https://example.com/secure", tran_byte=1000, tran_giay=5.0)
                )
            assert exc.value.ma == "ha_scheme"
            assert "hạ cấp" in exc.value.ly_do


def test_tai_url_tu_choi_content_encoding_gzip_bom(monkeypatch):
    # B1: Phản hồi có Content-Encoding khác identity (ví dụ gzip) phải bị từ chối
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    import gzip

    async def endpoint(req: Request):
        raw = gzip.compress(b"A" * 5000)
        return Response(content=raw, headers={"content-encoding": "gzip", "content-type": "application/octet-stream"})

    app = Starlette(routes=[Route("/bom", endpoint)])
    with MayChuThat(app) as srv:
        url = f"{srv.url}/bom"
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(url, tran_byte=10000, tran_giay=5.0))
        assert exc.value.ma == "content_encoding_khong_cho_phep"
        assert "gzip" in exc.value.ly_do


def test_tai_url_doi_origin_go_sach_tieu_de(monkeypatch):
    # B8: Chuyển hướng sang origin khác (khác cổng/host) phải xoá sạch các tiêu đề người gọi truyền
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    srv2_headers = {}

    async def ep2(req: Request):
        srv2_headers.update(dict(req.headers))
        return PlainTextResponse("OK")

    app2 = Starlette(routes=[Route("/dich", ep2)])

    with MayChuThat(app2) as srv2:
        async def ep1(req: Request):
            return RedirectResponse(url=f"{srv2.url}/dich")

        app1 = Starlette(routes=[Route("/chuyen", ep1)])
        with MayChuThat(app1) as srv1:
            data, ct = asyncio.run(
                tai_url(
                    f"{srv1.url}/chuyen",
                    tran_byte=1000,
                    tran_giay=5.0,
                    tieu_de={"X-Api-Key": "secret123", "X-Custom": "custom_val"},
                )
            )
            assert data == b"OK"
            assert "x-api-key" not in srv2_headers
            assert "x-custom" not in srv2_headers
            assert srv2_headers.get("accept-encoding") == "identity"


def test_tai_url_chuyen_huong_sang_scheme_la(monkeypatch):
    # B7: Chuyển hướng sang Location có scheme lạ như javascript:alert(1)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    async def chuyen_js(req: Request):
        return Response(status_code=302, headers={"location": "javascript:alert(1)"})

    app = Starlette(routes=[Route("/js", chuyen_js)])
    with MayChuThat(app) as srv:
        with pytest.raises(LoiTaiUrl) as exc:
            asyncio.run(tai_url(f"{srv.url}/js", tran_byte=1000, tran_giay=5.0))
        assert exc.value.ma in ("scheme_khong_hop_le", "url_khong_hop_le")


def test_tai_url_chan_dns_rebinding(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    # Giả lập getaddrinfo lần đầu (ở tai_url) trả về 127.0.0.1 sạch,
    # nhưng lần hai (ở TransportGhimIp khi connect) trả về 192.168.1.1 (LAN)
    from unittest.mock import patch

    lan_goi = 0
    def mock_getaddrinfo(host, port, *args, **kwargs):
        nonlocal lan_goi
        lan_goi += 1
        if lan_goi == 1:
            # Lần đầu: kiểm tra DNS trước khi gọi
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        # Lần hai: lúc TransportGhimIp kết nối -> trả IP LAN bị cấm
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", port))]

    loop = asyncio.new_event_loop()
    try:
        with patch.object(loop, "getaddrinfo", side_effect=mock_getaddrinfo):
            with patch("asyncio.get_running_loop", return_value=loop):
                with pytest.raises(LoiTaiUrl) as exc:
                    loop.run_until_complete(
                        tai_url("http://rebinding.test/anh.png", tran_byte=1000, tran_giay=5.0)
                    )
                assert exc.value.ma == "ip_bi_chan"
    finally:
        loop.close()
