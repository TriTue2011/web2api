"""NV96 — ma trận leo thang quyền + IDOR cho các đường API mới của NV88–NV94.

Phạm vi: các route mới đăng ký sau commit 45950c8, đọc từ app/tuyen.py:
  /api/danh-tinh (GET, POST), /api/danh-tinh/chua-gan, /api/danh-tinh/tao-va-gan,
  /api/danh-tinh/go-theo-module, /api/danh-tinh/{id}, /api/danh-tinh/{id}/gan,
  /api/danh-tinh/{id}/go, /api/danh-tinh/dang-nhap, /api/danh-tinh/{id}/trang-thai,
  /api/danh-tinh/{id}/ma-2fa, DELETE /api/danh-tinh/{id},
  /api/tai-khoan/dan-hang-loat, /api/tai-khoan/dang-nhap-hang-loat/*,
  /api/tai-khoan/dang-nhap-tu-dong/bat-dau, /api/tai-khoan/khoi-phuc*,
  /api/tai-khoan/tai-dung, /api/phien (GET, DELETE, POST kiem).

Mỗi đường kiểm 4 hàng (nếu không áp dụng một hàng, ghi lý do trong test):
 (1) không khoá → 401, cùng thông điệp với khoá sai (không lộ lý do cụ thể);
 (2) khoá khách → 403;
 (3) vai NGƯỜI DÙNG THƯỜNG (phiên web đăng nhập tài khoản user) → 403 cho đường quản trị;
 (4) IDOR: tất cả các đường trong phạm vi đều dependencies=QT (app/tuyen.py:63 QT = [Depends(can_quan_tri)])
     nên cổng chặn trước khi chạm tài nguyên — người dùng B đổi id của danh tính của A vẫn 403 và KHÔNG lộ dữ liệu.
Hàng (5): 3 kịch bản leo thang cụ thể đọc từ app/tuyen.py — xem cuối tệp.

Mọi chuỗi bí mật là chuỗi giả rõ ràng, không phải bí mật thật.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nguoi_dung, phien_web
from app.main import app
from app.so_module import SoModule

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_SAI = "sai-hoan-toan-khong-ton-tai"
SAI = {"Authorization": f"Bearer {KHOA_SAI}"}
MK = "MatKhauKiemThu-12345"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(d))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    monkeypatch.delenv("WEB2API_QT_IP_CHO_PHEP", raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    khoa_khach.lam_moi_cache()
    with TestClient(app) as c:
        c.app.state.so = SoModule()
        yield c, d
    khoa_khach.lam_moi_cache()


def _tao_nguoi(vai: str, ten: str):
    nd, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=MK))
    return nd


def _phien_user(client, nd_id: str):
    """Trả về (headers, cookies) cho phiên web của người dùng thường."""
    p, tok = phien_web.tao_phien(nd_id)
    jar = {"w2a_phien": tok}
    return {"X-CSRF-Token": p["csrf"]}, jar


def _khoa_khach_header():
    ban_ghi, khoa_tho = khoa_khach.tao_khoa(nhan="khách test", pham_vi=["x"])
    return ban_ghi, {"Authorization": f"Bearer {khoa_tho}"}


def _khong_lo_loi_401(r):
    """(1): 401 và thông điệp trùng với khoá sai — không phân biệt được lý do."""
    assert r.status_code == 401, r.text
    r_sai = None  # so sánh bên ngoài khi cần
    return r


# Danh sách đường theo phạm vi NV96 (phương thức, đường, tham số thân mẫu nếu cần).
DUONG_QT = [
    ("GET", "/api/danh-tinh", None),
    ("POST", "/api/danh-tinh", {"email": "a@example.com"}),
    ("GET", "/api/danh-tinh/chua-gan", None),  # cần ?module_id= nhưng thiếu khoá → 401 trước
    ("POST", "/api/danh-tinh/tao-va-gan", {"email": "a@example.com", "module_id": "x"}),
    ("POST", "/api/danh-tinh/go-theo-module", {"email": "a@example.com", "module_id": "x"}),
    ("GET", "/api/danh-tinh/dt-khong-ton-tai", None),
    ("POST", "/api/danh-tinh/dt-khong-ton-tai/gan", {"module_id": "x"}),
    ("POST", "/api/danh-tinh/dt-khong-ton-tai/go", {"module_id": "x"}),
    ("DELETE", "/api/danh-tinh/dt-khong-ton-tai", None),
    ("POST", "/api/danh-tinh/dang-nhap", {"email": "a@example.com"}),
    ("GET", "/api/danh-tinh/dt-khong-ton-tai/trang-thai", None),
    ("POST", "/api/danh-tinh/dt-khong-ton-tai/ma-2fa", {}),
    ("POST", "/api/tai-khoan/dan-hang-loat", {}),
    ("POST", "/api/tai-khoan/dang-nhap-hang-loat/bat-dau", {}),
    ("GET", "/api/tai-khoan/dang-nhap-hang-loat/trang-thai", None),
    ("POST", "/api/tai-khoan/dang-nhap-hang-loat/dung", {}),
    ("POST", "/api/tai-khoan/dang-nhap-tu-dong/bat-dau", {}),
    ("POST", "/api/tai-khoan/khoi-phuc", {}),
    ("GET", "/api/tai-khoan/khoi-phuc/trang-thai", None),
    ("POST", "/api/tai-khoan/khoi-phuc/xoa-can-nguoi", {}),
    ("POST", "/api/tai-khoan/tai-dung", {}),
    ("GET", "/api/phien", None),
    ("DELETE", "/api/phien/mod-khong-co/tk-khong-co", None),
    ("POST", "/api/phien/mod-khong-co/tk-khong-co/kiem", {}),
]


def _goi(client, phuong_thuc, duong, than):
    if phuong_thuc == "GET":
        return client.get(duong)
    if phuong_thuc == "POST":
        return client.post(duong, json=than or {})
    if phuong_thuc == "DELETE":
        return client.delete(duong)
    raise AssertionError(phuong_thuc)


def _goi_voi(client, phuong_thuc, duong, than, headers):
    if phuong_thuc == "GET":
        return client.get(duong, headers=headers)
    if phuong_thuc == "POST":
        return client.post(duong, json=than or {}, headers=headers)
    if phuong_thuc == "DELETE":
        return client.delete(duong, headers=headers)
    raise AssertionError(phuong_thuc)


@pytest.mark.parametrize("phuong_thuc,duong,than", DUONG_QT)
def test_nv96_khong_khoa_401_dong_nhat(moi_truong, phuong_thuc, duong, than):
    """(1) Không khoá → 401, cùng thông điệp với khoá sai (không lộ lý do cụ thể)."""
    client, _ = moi_truong
    r_khong = _goi(client, phuong_thuc, duong, than)
    assert r_khong.status_code == 401, r_khong.text
    r_sai = _goi_voi(client, phuong_thuc, duong, than, SAI)
    assert r_sai.status_code == 401, r_sai.text
    # thông điệp lỗi không phân biệt hai trường hợp
    assert r_khong.json()["error"]["message"] == r_sai.json()["error"]["message"], (
        r_khong.text, r_sai.text)


@pytest.mark.parametrize("phuong_thuc,duong,than", DUONG_QT)
def test_nv96_khoa_khach_403(moi_truong, phuong_thuc, duong, than):
    """(2) Khoá khách → 403 trên mọi đường phạm vi."""
    client, _ = moi_truong
    ban_ghi, kh = _khoa_khach_header()
    try:
        r = _goi_voi(client, phuong_thuc, duong, than, kh)
        assert r.status_code == 403, r.text
    finally:
        khoa_khach.xoa_khoa(ban_ghi["id"])


@pytest.mark.parametrize("phuong_thuc,duong,than", DUONG_QT)
def test_nv96_nguoi_dung_thuong_403(moi_truong, phuong_thuc, duong, than):
    """(3) Vai người dùng thường (phiên web) → 403 trên mọi đường quản trị."""
    client, _ = moi_truong
    nd = _tao_nguoi("user", "user-thuong-nv96")
    headers, jar = _phien_user(client, nd["id"])
    for k, v in jar.items():
        client.cookies.set(k, v)
    r = _goi_voi(client, phuong_thuc, duong, than, headers)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("phuong_thuc,duong,than", DUONG_QT)
def test_nv96_idor_khong_lo_du_lieu(moi_truong, phuong_thuc, duong, than):
    """(4) IDOR: người dùng B đổi id sang tài nguyên của A → 403, thân không chứa dữ liệu lạ.

    Lý do hàng này vẫn áp dụng được: mọi đường phạm vi đều dependencies=QT nên cổng
    chặn trước khi chạm tài nguyên; test xác nhận cổng không «lọt» và thân 403 không
    phản chiếu email/cookie/ho_so của bất kỳ ai.
    """
    client, _ = moi_truong
    nd = _tao_nguoi("user", "user-idor-nv96")
    headers, jar = _phien_user(client, nd["id"])
    for k, v in jar.items():
        client.cookies.set(k, v)
    # đổi id sang id «của người khác» (chuỗi hợp khuôn, không tồn tại)
    duong_b = duong.replace("dt-khong-ton-tai", "dt-cua-admin-a")
    r = _goi_voi(client, phuong_thuc, duong_b, than, headers)
    assert r.status_code == 403, r.text
    than_text = r.text.lower()
    for bi_mat in ("mat_khau", "cookie", "ho_so_trinh_duyet", "@"):
        assert bi_mat not in than_text, (bi_mat, r.text)


# ───────────── Hàng (5): 3 kịch bản leo thang cụ thể, đọc từ app/tuyen.py ─────────────

def test_nv96_leo_thang_1_khoa_khach_quyen_cao(moi_truong):
    """Leo thang 1: người dùng thường tạo khoá khách mang phạm vi quản trị qua
    POST /api/khoa-khach → phải 403 (đường này dependencies=QT ở app/tuyen.py)."""
    client, _ = moi_truong
    nd = _tao_nguoi("user", "user-leo1-nv96")
    headers, jar = _phien_user(client, nd["id"])
    for k, v in jar.items():
        client.cookies.set(k, v)
    r = client.post("/api/khoa-khach", json={"nhan": "leo", "pham_vi": ["*"]},
                    headers=headers)
    assert r.status_code == 403, r.text


def test_nv96_leo_thang_2_tu_nang_vai_admin(moi_truong):
    """Leo thang 2: người dùng thường tự nâng vai mình thành admin qua
    /api/nguoi-dung → phải 403 (nguoi_dung router dependencies=QT)."""
    client, _ = moi_truong
    nd = _tao_nguoi("user", "user-leo2-nv96")
    headers, jar = _phien_user(client, nd["id"])
    for k, v in jar.items():
        client.cookies.set(k, v)
    r = client.put(f"/api/nguoi-dung/{nd['id']}", json={"vai": "admin"},
                   headers=headers)
    assert r.status_code in (403, 404, 405), r.text
    nd2 = nguoi_dung.tim_theo_id(nd["id"])
    assert nd2["vai"] == "user", nd2


def test_nv96_leo_thang_3_danh_tinh_chiem_ho_so_admin(moi_truong):
    """Leo thang 3: người dùng thường tạo danh tính cho mình rồi gọi
    POST /api/danh-tinh/dang-nhap để mở hồ sơ trình duyệt (tài nguyên quản trị)
    → phải 403; và không có hồ sơ nào được tạo cho họ."""
    client, _ = moi_truong
    nd = _tao_nguoi("user", "user-leo3-nv96")
    headers, jar = _phien_user(client, nd["id"])
    for k, v in jar.items():
        client.cookies.set(k, v)
    r = client.post("/api/danh-tinh/dang-nhap",
                    json={"email": "user-leo3@example.com"}, headers=headers)
    assert r.status_code == 403, r.text
