"""NV53 — test trước cho cookie phiên: tossing, tiền tố __Host-, cờ bảo mật.

Chỉ test, không sửa app/.

Bối cảnh: đường đăng nhập web đặt cookie phiên. Kẻ tấn công có thể đặt cookie cùng tên
từ subdomain hoặc qua HTTP để ghi đè cookie của người dùng (cookie tossing). Biện pháp
chuẩn: tiền tố __Host- (yêu cầu Secure, Path=/, không Domain), HttpOnly, SameSite=Strict.

Phạm vi:
1. HTTPS (trực tiếp hoặc X-Forwarded-Proto qua proxy tin cậy) → cookie `__Host-w2a_phien`,
   Secure, Path=/, KHÔNG Domain. HTTP thường → `w2a_phien`, không Secure.
2. Cookie phiên luôn HttpOnly + SameSite=Strict; giao diện không đặt cookie phiên bằng JS.
3. [ĐỎ] HTTPS mà chỉ nhận `w2a_phien` (không tiền tố __Host-) → không phải phiên hợp lệ → 401.
4. Đăng xuất xoá đúng cookie (cùng tên, cùng Path), phiên không dùng được nữa.
5. X-Forwarded-Proto từ khách không tin cậy → không thành Secure/__Host- giả.

Đột biến chứng minh (đều đỏ khi giữ nguyên dòng dự phòng trong `lay_token_tu_cookie`):
- NV53-03a: `lay_token_tu_cookie` chấp nhận `w2a_phien` trên HTTPS → tossing lọt (200 thay vì 401).
- NV53-03b: cùng gốc — cố định phiên: nạn nhân bị coi là đã đăng nhập bằng phiên của kẻ
  tấn công (200 thay vì 401).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung, phien_web
from app.main import app
from app.so_module import SoModule

KHOA_QT_GIA = "sk-test-nv53-quan-tri-0000000000000000"
MK_TOT = "MatKhauTot123456"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_GIA)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "100")
    monkeypatch.setenv("WEB2API_RL_CUA_SO_GIAY", "60")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "100")
    monkeypatch.setenv("WEB2API_TRUST_PROXY", "testclient,127.0.0.1,::1")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    monkeypatch.delenv("WEB2API_PUBLIC_URL", raising=False)
    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    yield data_dir


def _tao_tai_khoan(vai: str, ten: str) -> None:
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK_TOT))


def _dang_nhap(client: TestClient, ten: str, headers: dict | None = None):
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": ten, "mat_khau": MK_TOT},
        headers=headers or {},
    )
    assert r.status_code == 200, r.text
    return r


def _ten_cookie_cac(sc_list: list[str]) -> list[str]:
    ten = []
    for sc in sc_list:
        m = re.match(r"\s*([^=;]+)=", sc)
        if m:
            ten.append(m.group(1).strip().lower())
    return ten


def _thuoc_tinh_co(sc_list: list[str], attr: str) -> bool:
    """Cờ dạng `; Secure` / `; HttpOnly` (không giá trị) có mặt trong Set-Cookie nào không."""
    for sc in sc_list:
        parts = [p.strip().lower() for p in sc.split(";")]
        if attr.lower() in parts:
            return True
    return False


def _gia_tri_thuoc_tinh(sc_list: list[str], khoa: str) -> str | None:
    for sc in sc_list:
        for p in sc.split(";"):
            p = p.strip()
            if p.lower().startswith(khoa.lower() + "="):
                return p.split("=", 1)[1].strip().lower()
    return None


def _lay_token(sc_list: list[str], ten_cookie: str) -> str | None:
    for sc in sc_list:
        m = re.search(rf"{re.escape(ten_cookie)}=([^;]*)", sc, re.IGNORECASE)
        if m and m.group(1):
            return m.group(1)
    return None


def _lam_client(https: bool = False) -> TestClient:
    if https:
        c = TestClient(app, base_url="https://testserver")
    else:
        c = TestClient(app)
    c.app.state.so = SoModule()
    return c


# ── 1. Đặt tên + cờ theo HTTPS/HTTP ──────────────────────────────────────────


def test_https_truc_tiep_cookie_host_secure_khong_domain(moi_truong):
    """HTTPS trực tiếp → `__Host-w2a_phien`, Secure, Path=/, không Domain, không Max-Age (xanh)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client(https=True) as c:
        r = _dang_nhap(c, "admin")
        sc = r.headers.get_list("set-cookie")
    ten = _ten_cookie_cac(sc)
    assert "__host-w2a_phien" in ten, sc
    assert _thuoc_tinh_co(sc, "secure"), sc
    assert _gia_tri_thuoc_tinh(sc, "path") == "/", sc
    assert _gia_tri_thuoc_tinh(sc, "domain") is None, sc
    assert _gia_tri_thuoc_tinh(sc, "max-age") is None, sc


def test_https_qua_proxy_tin_cay_cookie_host_secure(moi_truong, monkeypatch):
    """X-Forwarded-Proto: https qua proxy tin cậy (WEB2API_TIN_PROXY=1) → __Host- + Secure (xanh)."""
    monkeypatch.setenv("WEB2API_TIN_PROXY", "1")
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = c.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin", "mat_khau": MK_TOT},
            headers={"x-forwarded-proto": "https"},
        )
        assert r.status_code == 200, r.text
        sc = r.headers.get_list("set-cookie")
    assert "__host-w2a_phien" in _ten_cookie_cac(sc), sc
    assert _thuoc_tinh_co(sc, "secure"), sc
    assert _gia_tri_thuoc_tinh(sc, "domain") is None, sc


def test_http_thuong_cookie_thuong_khong_secure(moi_truong):
    """HTTP thường (thử cục bộ) → `w2a_phien`, KHÔNG Secure, Path=/, vẫn đăng nhập được (xanh)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = _dang_nhap(c, "admin")
        sc = r.headers.get_list("set-cookie")
    ten = _ten_cookie_cac(sc)
    assert "w2a_phien" in ten, sc
    assert "__host-w2a_phien" not in ten, sc
    assert not _thuoc_tinh_co(sc, "secure"), sc
    assert _gia_tri_thuoc_tinh(sc, "path") == "/", sc


# ── 2. HttpOnly + SameSite=Strict luôn có; không đặt bằng JS ─────────────────


@pytest.mark.parametrize("https", [False, True])
def test_cookie_phien_luon_httponly_va_samesite_strict(moi_truong, https):
    """Cookie phiên luôn HttpOnly và SameSite=Strict trên cả HTTP và HTTPS (xanh)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client(https=https) as c:
        r = _dang_nhap(c, "admin")
        sc = r.headers.get_list("set-cookie")
    assert _thuoc_tinh_co(sc, "httponly"), sc
    assert _gia_tri_thuoc_tinh(sc, "samesite") == "strict", sc


def test_giao_dien_khong_dat_cookie_phien_bang_javascript():
    """Không có `document.cookie` trong web/ — cookie phiên không thể bị đặt/ghi đè bằng JS (xanh)."""
    goc = Path(__file__).resolve().parents[1] / "web"
    co = []
    for p in goc.rglob("*"):
        if not p.is_file() or p.suffix not in {".js", ".html"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "document.cookie" in text:
            co.append(p.name)
    assert co == [], f"giao diện đặt cookie bằng JS: {co}"


# ── 3. Chống tossing: chỉ chấp nhận đúng tên theo kênh ────────────────────────


def test_nv53_03a_tossing_https_chi_cookie_khong_tien_to_bi_401(moi_truong):
    """HTTPS + chỉ cookie `w2a_phien` (kẻ tấn công đặt từ subdomain/HTTP) → 401, không phải phiên."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client(https=True) as c:
        r = _dang_nhap(c, "admin")
        token = _lay_token(r.headers.get_list("set-cookie"), "__Host-w2a_phien")
        assert token, "đăng nhập HTTPS phải đặt __Host-w2a_phien"
    with _lam_client(https=True) as c2:
        r2 = c2.get("/api/toi", headers={"Cookie": f"w2a_phien={token}"})
        assert r2.status_code == 401, f"muốn 401, nhận {r2.status_code}: {r2.text[:120]}"


def test_nv53_03b_tossing_co_dinh_phien_https_bi_401(moi_truong):
    """Kẻ tấn công đăng nhập lấy token, đặt `w2a_phien=<token kẻ tấn công>` vào trình duyệt nạn
    nhân → yêu cầu HTTPS của nạn nhân phải 401, không được coi là phiên kẻ tấn công."""
    _tao_tai_khoan("user", "ke-tan-cong")
    with _lam_client(https=True) as c:
        r = _dang_nhap(c, "ke-tan-cong")
        token_ke = _lay_token(r.headers.get_list("set-cookie"), "__Host-w2a_phien")
        assert token_ke
    with _lam_client(https=True) as c2:
        r2 = c2.get("/api/toi", headers={"Cookie": f"w2a_phien={token_ke}"})
        assert r2.status_code == 401, f"muốn 401, nhận {r2.status_code}: {r2.text[:120]}"


def test_https_uu_tien_cookie_host_khi_co_ca_hai(moi_truong):
    """HTTPS + cả hai cookie: `__Host-` (thật) thắng `w2a_phien` (bị tossing) — đúng danh tính (xanh)."""
    _tao_tai_khoan("admin", "admin")
    _tao_tai_khoan("user", "khach")
    with _lam_client(https=True) as c:
        token_admin = _lay_token(_dang_nhap(c, "admin").headers.get_list("set-cookie"), "__Host-w2a_phien")
    with _lam_client(https=True) as c2:
        token_khach = _lay_token(_dang_nhap(c2, "khach").headers.get_list("set-cookie"), "__Host-w2a_phien")
    assert token_admin and token_khach and token_admin != token_khach
    with _lam_client(https=True) as c3:
        r = c3.get(
            "/api/toi",
            headers={"Cookie": f"__Host-w2a_phien={token_admin}; w2a_phien={token_khach}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ten_dang_nhap"] == "admin"


def test_cookie_sai_dang_tren_https_401(moi_truong):
    """Cookie sai dạng trên HTTPS → 401, không ngoại lệ (xanh, phòng thủ)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client(https=True) as c:
        r = c.get("/api/toi", headers={"Cookie": "w2a_phien=token-gia-khong-hop-le"})
        assert r.status_code == 401


def test_khong_cookie_goi_duong_phien_401(moi_truong):
    """Không cookie → 401 (xanh, phòng thủ)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = c.get("/api/toi")
        assert r.status_code == 401


def test_bearer_bi_tu_choi_tren_duong_cookie_401(moi_truong):
    """Bearer khoá quản trị không dùng được đường cookie → 401 (xanh, phòng thủ)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = c.get("/api/toi", headers={"Authorization": f"Bearer {KHOA_QT_GIA}"})
        assert r.status_code == 401


# ── 4. Đăng xuất ─────────────────────────────────────────────────────────────


def test_dang_xuat_xoa_dung_cookie_va_phien_het_hieu_luc(moi_truong):
    """Đăng xuất (HTTP): xoá đúng `w2a_phien` cùng Path=/, token cũ không dùng được nữa (xanh)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = _dang_nhap(c, "admin")
        sc = r.headers.get_list("set-cookie")
        token = _lay_token(sc, "w2a_phien")
        csrf = r.json()["csrf"]
        assert token and csrf
    with _lam_client() as c2:
        r2 = c2.post(
            "/api/dang-xuat",
            headers={"Cookie": f"w2a_phien={token}", "x-csrf-token": csrf},
        )
        assert r2.status_code == 200, r2.text
        xoa = r2.headers.get_list("set-cookie")
        assert any(
            s.lower().startswith("w2a_phien=")
            and "path=/" in s.lower()
            and ("max-age=0" in s.lower() or "expires=" in s.lower())
            for s in xoa
        ), xoa
    with _lam_client() as c3:
        r3 = c3.get("/api/toi", headers={"Cookie": f"w2a_phien={token}"})
        assert r3.status_code == 401


def test_dang_xuat_https_xoa_cookie_host_co_secure(moi_truong):
    """Đăng xuất (HTTPS): lệnh xoá `__Host-w2a_phien` có Secure + Path=/ (trình duyệt không bỏ qua) (xanh)."""
    _tao_tai_khoan("admin", "admin")
    with _lam_client(https=True) as c:
        r = _dang_nhap(c, "admin")
        sc = r.headers.get_list("set-cookie")
        token = _lay_token(sc, "__Host-w2a_phien")
        csrf = r.json()["csrf"]
        assert token and csrf
    with _lam_client(https=True) as c2:
        r2 = c2.post(
            "/api/dang-xuat",
            headers={"Cookie": f"__Host-w2a_phien={token}", "x-csrf-token": csrf},
        )
        assert r2.status_code == 200, r2.text
        xoa = r2.headers.get_list("set-cookie")
        lenh = [s for s in xoa if s.lower().startswith("__host-w2a_phien=")]
        assert lenh, xoa
        assert _thuoc_tinh_co(lenh, "secure"), xoa
        assert _gia_tri_thuoc_tinh(lenh, "path") == "/", xoa
    with _lam_client(https=True) as c3:
        r3 = c3.get("/api/toi", headers={"Cookie": f"__Host-w2a_phien={token}"})
        assert r3.status_code == 401


# ── 5. X-Forwarded-Proto không tin cậy ────────────────────────────────────────


def test_x_forwarded_proto_khong_tin_cay_khong_thanh_secure_gia(moi_truong, monkeypatch):
    """Khách tự gửi X-Forwarded-Proto: https mà proxy chưa được tin cậy → vẫn `w2a_phien`,
    không Secure, không __Host- giả (xanh)."""
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    _tao_tai_khoan("admin", "admin")
    with _lam_client() as c:
        r = c.post(
            "/api/dang-nhap",
            json={"ten_dang_nhap": "admin", "mat_khau": MK_TOT},
            headers={"x-forwarded-proto": "https"},
        )
        assert r.status_code == 200, r.text
        sc = r.headers.get_list("set-cookie")
    ten = _ten_cookie_cac(sc)
    assert "w2a_phien" in ten, sc
    assert "__host-w2a_phien" not in ten, sc
    assert not _thuoc_tinh_co(sc, "secure"), sc
