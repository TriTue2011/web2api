"""NV56 — test trước cho bề mặt trình duyệt: header bảo mật toàn cục và mật khẩu không phản chiếu.

Phạm vi:
1. Mọi trang HTML trả về phải có CSP + 3 header bảo mật (đi qua danh sách đường trang thật).
2. Đếm khối <script> nội tuyến trong web/*.html (số đo thật, ghim để phát hiện thêm mới).
3. Mật khẩu người dùng nhập không bao giờ xuất hiện trong thuộc tính value do máy chủ
   sinh, trong phản hồi JSON, hay trong URL — kể cả khi đăng nhập sai.
4. Cookie phiên HttpOnly + SameSite=Strict (đã đúng — giữ xanh làm phòng thủ).

Ngoại lệ có chủ đích (ghi rõ, không phải lỗi): hai đường quản trị trả mật khẩu MỘT LẦN
cho chính quản trị viên vừa tạo/đặt lại (POST /api/nguoi-dung, POST
/api/nguoi-dung/{id}/dat-lai-mat-khau) — quản trị cần giá trị để trao cho người dùng.
Test ghim hành vi: chỉ quản trị gọi được, và đó là HAI nơi duy nhất trả mật khẩu.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung, novnc, phien_web
from app.main import app
from app.so_module import SoModule

KHOA_QT = "<redacted>"
MK_ADMIN = "MatKhauAdminTot123456"
MK_GIA = "mk-gia-nv56"
MK_XSS = 'mk-gia-nv56"><script>alert(1)</script>'
MK_NHAY = "mk-gia-nv56'\"onmouseover='x"

#: Mọi trang HTML do StaticFiles phục vụ (đọc từ thư mục web/ thật).
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
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1,::1")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "1000")
    monkeypatch.setenv("WEB2API_DANG_NHAP_TOAN_CUC_SAI_TOI_DA", "10000")
    nguoi_dung._THEO_DOI_SAI.clear()
    novnc._VE.clear()
    phien_web._PHIENS.clear() if hasattr(phien_web, "_PHIENS") else None
    gioi_han.khoi_dong()
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


def _tao_admin():
    asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin", mat_khau=MK_ADMIN)
    )


def _dang_nhap_cookie(client):
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": MK_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


# ── 1. Header bảo mật trên mọi trang HTML ─────────────────────────────

def test_csp_tren_moi_trang_html(moi_truong):
    """Mọi trang HTML trả về phải có Content-Security-Policy."""
    client, _ = moi_truong
    thieu = []
    for trang in TRANG_HTML:
        r = client.get(trang)
        assert r.status_code == 200, trang
        if "content-security-policy" not in {k.lower() for k, _ in r.headers.items()}:
            thieu.append(trang)
    assert not thieu, f"thiếu CSP: {thieu}"


def test_3_header_bao_mat_tren_moi_trang_html(moi_truong):
    """Middleware gắn nosniff/DENY/no-referrer cho mọi trang (đã đúng — giữ xanh)."""
    client, _ = moi_truong
    for trang in TRANG_HTML:
        r = client.get(trang)
        assert r.status_code == 200, trang
        h = {k.lower(): v for k, v in r.headers.items()}
        assert h.get("x-content-type-options") == "nosniff", trang
        assert h.get("x-frame-options") == "DENY", trang
        assert h.get("referrer-policy") == "no-referrer", trang


def test_trang_ve_novnc_co_csp_sandbox(moi_truong):
    """Trang vé noVNC đã có CSP sandbox (đã đúng — giữ xanh)."""
    client, _ = moi_truong
    r = client.get("/novnc/ve-khong-hop-le")
    h = {k.lower(): v for k, v in r.headers.items()}
    assert "content-security-policy" in h


# ── 2. Đếm script nội tuyến (số đo thật) ───────────────────────────────

def test_so_khoi_script_noi_tuyen_dung_12(moi_truong):
    """Đo thật: 12 khối <script> không src trong web/*.html (mỗi trang 1 khối khởi động
    dc-lite — có chủ đích). Ghim số để phát hiện khối mới thêm vào."""
    thu_muc_web = os.path.join(os.path.dirname(__file__), "..", "web")
    tong = 0
    chi_tiet = {}
    for ten in sorted(os.listdir(thu_muc_web)):
        if not ten.endswith(".html"):
            continue
        with open(os.path.join(thu_muc_web, ten), encoding="utf-8") as f:
            noi_dung = f.read()
        tat_ca = len(re.findall(r"<script(?![^>]*\bsrc=)", noi_dung))
        chi_tiet[ten] = tat_ca
        tong += tat_ca
    assert tong == 12, f"đo được {tong} khối nội tuyến, chi tiết: {chi_tiet}"
    assert all(v == 1 for v in chi_tiet.values()), chi_tiet


# ── 3. Mật khẩu không phản chiếu ──────────────────────────────────────

@pytest.mark.parametrize("mat_khau", [MK_XSS, MK_NHAY, "sai-hoan-toan"])
def test_dang_nhap_sai_khong_phan_chieu_mat_khau(moi_truong, mat_khau):
    """Đăng nhập sai (kể cả mật khẩu chứa <script>/dấu nháy) → 401 đồng nhất,
    thân JSON không chứa mật khẩu đã gửi."""
    client, _ = moi_truong
    _tao_admin()
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": mat_khau},
    )
    assert r.status_code == 401
    assert mat_khau not in r.text
    assert "<script>" not in r.text


def test_dang_nhap_thieu_mat_khau_khong_phan_chieu(moi_truong):
    client, _ = moi_truong
    _tao_admin()
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin"})
    assert r.status_code == 401
    assert "mat_khau" not in r.text or MK_GIA not in r.text


def test_phan_hoi_dang_nhap_thanh_cong_khong_chua_mat_khau(moi_truong):
    """Đăng nhập đúng: JSON trả về không chứa mật khẩu đã nhập (chỉ csrf + bản ghi sạch)."""
    client, _ = moi_truong
    _tao_admin()
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": MK_ADMIN},
    )
    assert r.status_code == 200
    assert MK_ADMIN not in r.text
    assert "bam_mat_khau" not in r.text


def test_doi_mat_khau_phan_hoi_khong_chua_mat_khau(moi_truong):
    """Đổi mật khẩu chính mình: phản hồi {"ok": true}, không echo mật khẩu cũ/mới."""
    client, _ = moi_truong
    _tao_admin()
    csrf = _dang_nhap_cookie(client)
    mk_moi = "mk-gia-nv56-doi-moi-123"
    r = client.post(
        "/api/toi/mat-khau",
        json={"mat_khau_cu": MK_ADMIN, "mat_khau_moi": mk_moi},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert MK_ADMIN not in r.text
    assert mk_moi not in r.text


def test_danh_sach_nguoi_dung_khong_lo_mat_khau(moi_truong):
    """GET /api/nguoi-dung: bản ghi sạch, không có mat_khau/bam_mat_khau."""
    client, _ = moi_truong
    _tao_admin()
    r = client.get("/api/nguoi-dung", headers={"Authorization": f"Bearer {KHOA_QT}"})
    assert r.status_code == 200
    assert "mat_khau" not in r.text
    assert "bam_mat_khau" not in r.text


def test_mat_khau_khong_trong_url(moi_truong):
    """Mật khẩu trong query string không bị phản chiếu vào thân phản hồi."""
    client, _ = moi_truong
    _tao_admin()
    r = client.get(f"/api/dang-nhap?mat_khau={MK_GIA}")
    assert MK_GIA not in r.text


def test_tao_nguoi_dung_tra_mat_khau_mot_lan_cho_quan_tri(moi_truong):
    """CÓ CHỦ ĐÍCH: POST /api/nguoi-dung (chỉ quản trị) trả mật khẩu MỘT LẦN để quản
    trị trao cho người dùng. Ghim: chỉ quản trị gọi được."""
    client, _ = moi_truong
    _tao_admin()
    r = client.post(
        "/api/nguoi-dung",
        headers={"Authorization": f"Bearer {KHOA_QT}"},
        json={"ten_dang_nhap": "user1", "mat_khau": "MkUser1Tot123456", "vai": "user"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mat_khau"] == "MkUser1Tot123456"
    # khoá khách / không khoá không gọi được
    r2 = client.post("/api/nguoi-dung", json={"ten_dang_nhap": "x"})
    assert r2.status_code == 401


def test_dat_lai_mat_khau_tra_mat_khau_moi_mot_lan_cho_quan_tri(moi_truong):
    """CÓ CHỦ ĐÍCH: POST /api/nguoi-dung/{id}/dat-lai-mat-khau (chỉ quản trị) trả
    mật khẩu sinh ngẫu nhiên MỘT LẦN. Ghim: chỉ quản trị gọi được."""
    client, _ = moi_truong
    _tao_admin()
    r = client.post(
        "/api/nguoi-dung",
        headers={"Authorization": f"Bearer {KHOA_QT}"},
        json={"ten_dang_nhap": "user2", "vai": "user"},
    )
    uid = r.json()["nguoi_dung"]["id"]
    r = client.post(
        f"/api/nguoi-dung/{uid}/dat-lai-mat-khau",
        headers={"Authorization": f"Bearer {KHOA_QT}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mat_khau_moi"]
    assert "bam_mat_khau" not in r.text
    r2 = client.post(f"/api/nguoi-dung/{uid}/dat-lai-mat-khau")
    assert r2.status_code == 401


# ── 4. Cookie phiên (đã đúng — giữ xanh) ──────────────────────────────

def test_cookie_phien_httponly_samesite_strict(moi_truong):
    client, _ = moi_truong
    _tao_admin()
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin", "mat_khau": MK_ADMIN},
    )
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie.replace(" ", "")


# ── Luật bắt buộc chung ───────────────────────────────────────────────

def test_khong_khoa_duong_quan_tri_401(moi_truong):
    client, _ = moi_truong
    assert client.get("/api/nguoi-dung").status_code == 401
    assert client.get("/api/khoa-khach").status_code == 401
