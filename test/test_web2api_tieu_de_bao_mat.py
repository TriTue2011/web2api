"""Tiêu đề bảo mật toàn cục: mọi phản hồi HTTP mang 3 header bảo vệ, không đè header riêng."""

import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

BA_HEADER = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    return tmp_path


def _khop_ba_header(headers):
    for ten, gia_tri in BA_HEADER.items():
        assert headers.get(ten) == gia_tri, (ten, dict(headers))


def test_api_json_co_header_bao_mat(du_lieu):
    with TestClient(app) as c:
        r = c.get("/v1/models", headers=QT)
        assert r.status_code == 200, r.text
        _khop_ba_header(r.headers)
        assert "content-security-policy" not in r.headers  # không CSP toàn cục


def test_trang_html_co_header_bao_mat(du_lieu):
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        _khop_ba_header(r.headers)
        assert "content-security-policy" not in r.headers


def test_loi_401_co_header_bao_mat(du_lieu):
    with TestClient(app) as c:
        r = c.get("/v1/models")  # không khoá
        assert r.status_code == 401, r.text
        _khop_ba_header(r.headers)


def test_loi_404_co_header_bao_mat(du_lieu):
    with TestClient(app) as c:
        r = c.get("/duong-khong-ton-tai-xyz")
        assert r.status_code == 404, r.text
        _khop_ba_header(r.headers)


def test_novnc_giu_header_rieng_khong_bi_de(du_lieu):
    with TestClient(app) as c:
        r = c.get("/novnc/dang-nhap")
        assert r.status_code in (200, 400), r.text
        # CSP riêng của /novnc còn nguyên → middleware toàn cục không đè/không lột
        assert "default-src 'none'" in r.headers.get("content-security-policy", "")
        # header đã có thì không thêm trùng
        assert r.headers.get_list("x-frame-options") == ["DENY"]
        _khop_ba_header(r.headers)


def test_luong_streaming_co_header_ngay_tu_dau():
    # Import trong hàm để trên mã cũ (chưa có module) test này đỏ dạng lỗi import,
    # còn 5 test tích hợp trên vẫn đỏ sạch dạng assertion.
    from app.tieu_de_bao_mat import MiddlewareTieuDeBaoMat

    async def app_nho(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return

        async def gen():
            yield b"data: 1\n\n"
            yield b"data: 2\n\n"

        resp = StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"X-Frame-Options": "SAMEORIGIN"})
        await resp(scope, receive, send)

    with TestClient(MiddlewareTieuDeBaoMat(app_nho)) as c:
        r = c.get("/su-kien")
        assert r.status_code == 200, r.text
        assert "text/event-stream" in r.headers["content-type"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers.get_list("x-frame-options") == ["SAMEORIGIN"]  # giữ nguyên, không đè
        assert "data: 2" in r.text
