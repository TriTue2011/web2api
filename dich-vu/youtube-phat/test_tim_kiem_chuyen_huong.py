"""Test NV153 đợt D3 — `resolve_facebook_share` phải kiểm host ở MỖI bước
chuyển hướng.

Mã cũ: `urlopen(Request(share_url))` — urllib tự theo 3xx vô điều kiện. URL
đầu đã qua `facebook_share_url` (host Facebook) nhưng ĐÍCH sau chuyển hướng
thì không ai kiểm: một link chia sẻ bị chuyển hướng độc có thể dẫn dịch vụ
tới 127.0.0.1 / IP LAN (SSRF). Test dưới dựng máy chủ giả trả 302 sang một
máy chủ khác chứa mã `impl_…` giả — mã cũ nuốt trọn và trả mã đó, mã mới
phải từ chối bằng SearchUnavailableError.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request

import pytest

import search
from search import SearchUnavailableError, resolve_facebook_share

MA_GIA = "1807802260572674"
TRANG_GIA = f"<html><body>media_id: \"x;y;;9::impl_{MA_GIA}\"</body></html>"


class _XuLy(BaseHTTPRequestHandler):
    """Máy chủ giả: `cac_duong[path] = (status, {header}, body)`."""

    cac_duong: dict = {}

    def do_GET(self):  # noqa: N802 (tên theo http.server)
        status, headers, body = self.cac_duong.get(
            self.path, (404, {}, "khong co"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):  # tắt log ồn của http.server
        pass


@pytest.fixture()
def hai_may_chu():
    """(cổng A «facebook giả», cổng B «đích độc») trên loopback."""
    cac_may = []
    for _ in range(2):
        may = ThreadingHTTPServer(("127.0.0.1", 0), _XuLy)
        threading.Thread(target=may.serve_forever, daemon=True).start()
        cac_may.append(may)
    yield cac_may[0].server_address[1], cac_may[1].server_address[1]
    for may in cac_may:
        may.shutdown()


def test_chuyen_huong_sang_loopback_bi_tu_choi(hai_may_chu):
    cong_a, cong_b = hai_may_chu
    _XuLy.cac_duong = {
        "/share/v/abc123": (
            302, {"Location": f"http://localhost:{cong_b}/trang"}, ""),
        "/trang": (200, {}, TRANG_GIA),
    }
    with pytest.raises(SearchUnavailableError):
        resolve_facebook_share(f"http://127.0.0.1:{cong_a}/share/v/abc123",
                               timeout=5)


def test_chuyen_huong_sang_ip_lan_bi_tu_choi(hai_may_chu):
    cong_a, _cong_b = hai_may_chu
    _XuLy.cac_duong = {
        "/share/r/xyz789": (
            302, {"Location": "http://192.168.1.1/trang"}, ""),
    }
    with pytest.raises(SearchUnavailableError):
        resolve_facebook_share(f"http://127.0.0.1:{cong_a}/share/r/xyz789",
                               timeout=5)


def _yeu_cau_goc():
    return Request("https://www.facebook.com/share/v/abc123",
                   headers=search.FACEBOOK_PAGE_HEADERS)


def test_handler_cho_phep_dich_facebook():
    xu_ly = search._XuLyChuyenHuongFb()
    moi = xu_ly.redirect_request(
        _yeu_cau_goc(), None, 302, "Found", {},
        "https://www.facebook.com/reel/1234567890123")
    assert moi is not None
    assert moi.full_url == "https://www.facebook.com/reel/1234567890123"


@pytest.mark.parametrize("dich", [
    "http://127.0.0.1:8713/api/integration/status",
    "http://192.168.10.38:8099/liet-ke",
    "https://evil.example.invalid/trang",
    "http://8.8.8.8/trang",
    "ftp://www.facebook.com/trang",
])
def test_handler_tu_choi_dich_la(dich):
    xu_ly = search._XuLyChuyenHuongFb()
    with pytest.raises(SearchUnavailableError):
        xu_ly.redirect_request(_yeu_cau_goc(), None, 302, "Found", {}, dich)


def test_handler_gioi_han_so_buoc_chuyen_huong():
    xu_ly = search._XuLyChuyenHuongFb()
    for _ in range(search._CHUYEN_HUONG_TOI_DA):
        assert xu_ly.redirect_request(
            _yeu_cau_goc(), None, 302, "Found", {},
            "https://www.facebook.com/reel/1234567890123") is not None
    with pytest.raises(SearchUnavailableError):
        xu_ly.redirect_request(
            _yeu_cau_goc(), None, 302, "Found", {},
            "https://www.facebook.com/reel/1234567890123")
