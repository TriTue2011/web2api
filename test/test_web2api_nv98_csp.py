"""NV98 — test kiểm tra Content-Security-Policy (CSP) toàn cục cho mọi trang HTML.

Kiểm tra:
1. Mọi trang HTML trong web/*.html được phục vụ đều có CSP.
2. Băm SHA-256 base64 của mỗi khối script nội tuyến (tính độc lập) xuất hiện trong CSP tương ứng.
3. script-src KHÔNG chứa 'unsafe-inline'.
4. Script lạ / giả không xuất hiện trong CSP của trang.
5. CSP riêng của /m/* và /novnc/* không bị middleware đè.
6. Phản hồi JSON (/health, /v1/models 401) không có CSP.
7. Đường .html không tồn tại: vẫn có 3 header bảo mật; nếu trả HTML thì CSP không chứa băm nào.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app

KHOA_QT = "khoa-quan-tri-test-nv98-csp"
THU_MUC_WEB = Path(__file__).resolve().parent.parent / "web"

TRANG_HTML = [
    "/",
    "/index.html",
    "/Dang-nhap.dc.html",
    "/Thiet-lap.dc.html",
    "/He-thong.dc.html",
    "/He-thong-AI.dc.html",
    "/He-thong-Bao-mat.dc.html",
    "/He-thong-Phien.dc.html",
    "/He-thong-Thiet-bi.dc.html",
    "/He-thong-Tich-hop.dc.html",
    "/He-thong-Tro-giup.dc.html",
    "/Studio.dc.html",
]


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    return tmp_path


def _tinh_bam_doc_lap(noi_dung_html: str) -> list[str]:
    """Tính danh sách băm SHA-256 base64 của các khối script nội tuyến độc lập với mã sản phẩm."""
    matches = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", noi_dung_html, flags=re.DOTALL | re.IGNORECASE)
    return [base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode("ascii") for m in matches]


def _lay_phan_script_src(csp: str) -> str:
    """Tách riêng chỉ thị script-src từ chuỗi CSP."""
    for doan in csp.split(";"):
        doan = doan.strip()
        if doan.startswith("script-src "):
            return doan[len("script-src "):].strip()
    return ""


# ───────────────────────────── 1, 2, 3: CSP và băm script nội tuyến ─────────────────────────────

def test_moi_trang_html_co_csp_va_khop_bam_script_doc_lap(du_lieu):
    """Mọi trang HTML có CSP; băm của mọi khối script nội tuyến đều có mặt; script-src không có 'unsafe-inline'."""
    tong_css_ngoai = 0
    with TestClient(app) as client:
        for duong in TRANG_HTML:
            r = client.get(duong)
            assert r.status_code == 200, duong
            headers = {k.lower(): v for k, v in r.headers.items()}
            assert "content-security-policy" in headers, f"Trang {duong} thiếu CSP"
            csp = headers["content-security-policy"]

            # Chỉ thị bắt buộc
            assert "default-src 'self'" in csp, duong
            assert "object-src 'none'" in csp, duong
            assert "base-uri 'none'" in csp, duong
            assert "form-action 'self'" in csp, duong
            assert "connect-src 'self' blob:" in csp, duong  # Studio fetch lại blob video
            assert "font-src 'self' data: https:;" in csp, duong  # tệp phông nằm ở origin khác với tệp CSS
            assert "frame-ancestors 'none'" in csp, duong

            script_src = _lay_phan_script_src(csp)
            assert script_src, f"Trang {duong} thiếu chỉ thị script-src trong CSP"
            # (3) script-src KHÔNG chứa 'unsafe-inline'
            assert "'unsafe-inline'" not in script_src, f"Trang {duong} chứa 'unsafe-inline' trong script-src: {script_src}"
            # script-src chứa 'self' và 'unsafe-eval' (do support.js dùng new Function)
            assert "'self'" in script_src, duong
            assert "'unsafe-eval'" in script_src, duong

            # Đọc tệp gốc tương ứng
            ten_tep = "index.html" if duong == "/" else duong.lstrip("/")
            tep_that = THU_MUC_WEB / ten_tep
            assert tep_that.is_file(), f"Tệp {tep_that} không tồn tại"
            bams_doc_lap = _tinh_bam_doc_lap(tep_that.read_text(encoding="utf-8"))
            assert len(bams_doc_lap) >= 1, f"Trang {duong} phải có ít nhất 1 khối script"

            # style-src có đúng các origin https mà trang tự khai bằng <link rel="stylesheet"> (tính độc lập)
            style_src = next(d.strip() for d in csp.split(";") if d.strip().startswith("style-src "))
            noi_dung_html = tep_that.read_text(encoding="utf-8")
            href_ngoai = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="(https://[^/"]+)', noi_dung_html)
            tong_css_ngoai += len(href_ngoai)
            for nguon in href_ngoai:
                assert nguon in style_src.split(), f"Trang {duong} thiếu {nguon} trong style-src: {style_src}"

            # (2) Khẳng định mỗi băm độc lập đều có trong script-src
            for b in bams_doc_lap:
                assert f"'sha256-{b}'" in script_src, f"Trang {duong} thiếu băm 'sha256-{b}' trong script-src"
    assert tong_css_ngoai > 0, "không trang nào nạp CSS ngoài: giả định của test đã đổi"


# ───────────────────────────── 4: Khối script giả không nằm trong CSP ─────────────────────────────

def test_script_noi_tuyen_gia_khong_nam_trong_csp(du_lieu, tmp_path):
    """Một khối script lạ / giả thêm vào trang có băm khác và KHÔNG nằm trong CSP của trang hiện hành."""
    script_gia = "alert('xss-attacker-payload-test-nv98');"
    bam_gia = base64.b64encode(hashlib.sha256(script_gia.encode("utf-8")).digest()).decode("ascii")

    with TestClient(app) as client:
        r = client.get("/Dang-nhap.dc.html")
        csp = r.headers.get("content-security-policy", "")
        # Băm của payload lạ không thể có trong CSP
        assert f"'sha256-{bam_gia}'" not in csp

    # Tạo tệp HTML tạm chứa khối script giả đó và tính băm
    from app.tieu_de_bao_mat import tinh_bam_script_tu_tep
    tep_tam = tmp_path / "trang_tam.html"
    tep_tam.write_text(f"<!doctype html><html><body><script>{script_gia}</script></body></html>", encoding="utf-8")
    bams_tam = tinh_bam_script_tu_tep(tep_tam)
    assert bam_gia in bams_tam


# ───────────────────────────── 5: Không đè CSP riêng của /m/* và /novnc/* ─────────────────────────────

def test_csp_rieng_cua_m_va_novnc_khong_bi_de(du_lieu):
    """CSP riêng của /m/* (HEADER_TRANG_MODULE) và /novnc/* không bị MiddlewareTieuDeBaoMat đè."""
    with TestClient(app) as client:
        # /novnc/dang-nhap
        r_novnc = client.get("/novnc/dang-nhap")
        assert r_novnc.status_code in (200, 400)
        csp_novnc = r_novnc.headers.get("content-security-policy", "")
        # /novnc/dang-nhap có default-src 'none' (từ HEADER_BAO_VE_TRANG_VE)
        assert "default-src 'none'" in csp_novnc
        assert "default-src 'self'" not in csp_novnc

        # /m/*
        r_m = client.get("/m/khong-ton-tai/trang.html")
        assert r_m.status_code == 404
        csp_m = r_m.headers.get("content-security-policy", "")
        # HEADER_TRANG_MODULE có frame-ancestors 'self' (không phải 'none')
        assert "frame-ancestors 'self'" in csp_m


# ───────────────────────────── 6: Phản hồi JSON không có CSP ─────────────────────────────

def test_phan_hoi_json_khong_co_csp(du_lieu):
    """Phản hồi JSON (/health, /v1/models 401) không bị gán CSP."""
    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert "content-security-policy" not in {k.lower(): v for k, v in r_health.headers.items()}

        r_models_401 = client.get("/v1/models")
        assert r_models_401.status_code == 401
        assert "content-security-policy" not in {k.lower(): v for k, v in r_models_401.headers.items()}


# ───────────────────────────── 7: Đường .html không tồn tại ─────────────────────────────

def test_duong_html_khong_ton_tai_khong_co_bam_script(du_lieu):
    """Đường .html không tồn tại: vẫn mang 3 header bảo mật; nếu trả HTML thì CSP không chứa băm sha256 nào."""
    with TestClient(app) as client:
        r = client.get("/trang-khong-ton-tai-12345.html")
        headers = {k.lower(): v for k, v in r.headers.items()}
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"
        assert headers.get("referrer-policy") == "no-referrer"

        if "content-security-policy" in headers:
            csp = headers["content-security-policy"]
            # Không chứa bất kỳ băm sha256 nào
            assert "sha256-" not in csp


def test_nguon_stylesheet_chi_nhan_https_khong_userinfo(tmp_path):
    """Chỉ nhận origin https do trang khai; bỏ http:, userinfo, đường tương đối; không trùng lặp."""
    from app.tieu_de_bao_mat import tim_nguon_stylesheet_tu_tep, tao_csp

    tep = tmp_path / "t.html"
    tep.write_text(
        '<link rel="stylesheet" href="https://css.ke-la.example/a.css">'
        '<link rel="stylesheet" href="https://css.ke-la.example/b.css">'
        '<link rel="stylesheet" href="http://khong-tls.example/c.css">'
        '<link rel="stylesheet" href="https://user:mk@gai-userinfo.example/d.css">'
        '<link rel="stylesheet" href="/cuc-bo.css">'
        '<link rel="icon" href="https://khong-phai-css.example/x.png">',
        encoding="utf-8",
    )
    assert tim_nguon_stylesheet_tu_tep(tep) == ("https://css.ke-la.example",)
    assert "style-src 'self' 'unsafe-inline' https://css.ke-la.example;" in tao_csp([], ("https://css.ke-la.example",))
    assert tim_nguon_stylesheet_tu_tep(tmp_path / "khong-co.html") == ()
