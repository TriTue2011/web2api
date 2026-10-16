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


def test_tep_giao_dien_tinh_co_cache_control_no_cache(du_lieu):
    """(a) GET /khung.js, /support.js, /He-thong-AI.dc.html, /index.html, / đều có Cache-Control no-cache."""
    duong_dan_tinh = [
        "/khung.js",
        "/support.js",
        "/He-thong-AI.dc.html",
        "/index.html",
        "/",
    ]
    with TestClient(app) as c:
        for d in duong_dan_tinh:
            r = c.get(d)
            assert r.status_code == 200, f"{d} trả {r.status_code}: {r.text}"
            assert r.headers.get("cache-control") == "no-cache", f"{d} thiếu Cache-Control: no-cache: {dict(r.headers)}"
            _khop_ba_header(r.headers)


def test_gui_lai_etag_nhan_304_va_co_cache_control(du_lieu):
    """(b) Gửi lại If-None-Match đúng ETag → 304 và vẫn có Cache-Control: no-cache."""
    with TestClient(app) as c:
        r1 = c.get("/khung.js")
        assert r1.status_code == 200
        etag = r1.headers.get("etag")
        assert etag, "Tệp tĩnh phải có ETag từ bộ phục vụ tĩnh"

        r2 = c.get("/khung.js", headers={"if-none-match": etag})
        assert r2.status_code == 304, f"Mong đợi 304, nhận {r2.status_code}"
        assert r2.headers.get("cache-control") == "no-cache", "Phản hồi 304 phải giữ Cache-Control: no-cache"
        _khop_ba_header(r2.headers)

        # Kiểm tra thêm với trang gốc /
        r_goc = c.get("/")
        assert r_goc.status_code == 200
        etag_goc = r_goc.headers.get("etag")
        if etag_goc:
            r_goc_304 = c.get("/", headers={"if-none-match": etag_goc})
            assert r_goc_304.status_code == 304
            assert r_goc_304.headers.get("cache-control") == "no-cache"
            _khop_ba_header(r_goc_304.headers)


def test_api_json_khong_bi_gan_no_cache(du_lieu):
    """(c) /v1/models (JSON API) KHÔNG bị gán no-cache bởi quy tắc này nếu route không phải tệp tĩnh."""
    with TestClient(app) as c:
        r = c.get("/v1/models", headers=QT)
        assert r.status_code == 200, r.text
        # Quyết định: Tuyến API JSON động không bị gán no-cache bởi quy tắc tệp tĩnh
        # vì dữ liệu API động được tính toán tại chỗ theo thời gian thực và quyền hạn người gọi.
        assert "cache-control" not in r.headers
        _khop_ba_header(r.headers)


def test_novnc_va_m_khong_doi_hanh_vi(du_lieu):
    """(d) /novnc và /m/ không đổi hành vi (không bị đè hoặc can thiệp Cache-Control)."""
    with TestClient(app) as c:
        # /novnc có chính sách bảo vệ riêng (no-store) không bị đổi thành no-cache
        r_novnc = c.get("/novnc/dang-nhap")
        assert r_novnc.status_code in (200, 400)
        assert r_novnc.headers.get("cache-control") == "no-store"

        # /m/ giao diện module không bị gán no-cache bởi quy tắc tệp giao diện tĩnh của lõi
        r_m = c.get("/m/khong-co/index.html")
        assert r_m.status_code == 404
        assert "cache-control" not in r_m.headers


def test_canh_moi_ham_k_goi_trong_html_phai_co_trong_khung_hoac_duoc_bao_ve():
    """(e) Test canh: mọi hàm K.xxx mà web/*.html gọi phải nằm trong danh sách xuất của

    web/khung.js hoặc được bảo vệ bằng K.xxx ? / K.xxx &&.
    Hiện tại phimNav không xuất nhưng có bảo vệ nên phải xanh.
    """
    import re
    from pathlib import Path

    goc = Path(__file__).resolve().parents[1]
    khung_js = (goc / "web" / "khung.js").read_text(encoding="utf-8")
    m = re.search(r"window\.Khung\s*=\s*\{([^}]+)\}", khung_js)
    assert m, "Phải tìm thấy khối window.Khung trong web/khung.js"
    xuat = set(re.findall(r"\b[A-Za-z0-9_]+\b", m.group(1)))
    assert len(xuat) >= 40, f"Danh sách xuất của khung.js quá ít ({len(xuat)} hàm)"

    cac_trang_html = sorted((goc / "web").glob("*.html"))
    assert cac_trang_html, "Phải có các trang HTML trong web/"

    loi = []
    for p in cac_trang_html:
        noi_dung = p.read_text(encoding="utf-8")
        # Tìm mọi lời gọi hàm K.<ten>(...):
        for m_goi in re.finditer(r"\bK\.([A-Za-z0-9_]+)\s*\(", noi_dung):
            ten_ham = m_goi.group(1)
            if ten_ham in xuat:
                continue
            # Nếu hàm không xuất, kiểm tra xem có được bảo vệ không
            mau_bao_ve = rf"\bK\.{ten_ham}\s*[\?&]|K\?\.{ten_ham}"
            if not re.search(mau_bao_ve, noi_dung):
                loi.append(f"{p.name}: gọi K.{ten_ham}() không xuất trong khung.js và không được bảo vệ")

    assert not loi, "Phát hiện hàm K.xxx gọi từ HTML không xuất và không có bảo vệ:\n" + "\n".join(loi)

    # Kiểm tra giả định âm tính: đảm bảo test phát hiện được hàm không tồn tại và không bảo vệ
    mau_gia = "<div>K.hamChuaKhaiBaoChuaBaoVe()</div>"
    loi_gia = []
    for m_goi in re.finditer(r"\bK\.([A-Za-z0-9_]+)\s*\(", mau_gia):
        ten_ham = m_goi.group(1)
        if ten_ham in xuat:
            continue
        mau_bao_ve = rf"\bK\.{ten_ham}\s*[\?&]|K\?\.{ten_ham}"
        if not re.search(mau_bao_ve, mau_gia):
            loi_gia.append(ten_ham)
    assert len(loi_gia) == 1 and loi_gia[0] == "hamChuaKhaiBaoChuaBaoVe"

