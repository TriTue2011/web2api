"""NV36 — Trang «Phiên tài khoản»: hợp đồng API phục vụ giao diện + ma trận xác thực.

API /api/phien đã có; tệp này kiểm phần giao diện cần:
1. GET /api/phien trả ĐÚNG hình mà trang web/He-thong-Phien.dc.html dùng:
   {phien: [...], tong, can_dang_nhap_tong}, mỗi mục CHỈ chứa siêu dữ liệu
   (không bao giờ có du_lieu/token/cookie — kiểm allowlist chặt).
2. Ma trận xác thực đầy đủ cả 3 đường: không khoá → 401; khoá khách → 403;
   phiên cookie vai user → 403; phiên admin/khoá quản trị → 200.
3. IDOR/đường dẫn: module/tai_khoan lạ → 400; không tồn tại → 404;
   không chạm tệp ngoài thư mục phiên.
4. Kiểm tra tĩnh trang (không cần Chrome): khối thu gọn đóng mặc định,
   nút thật có aria-expanded/aria-controls, không innerHTML với dữ liệu API.

Test Chrome thật nằm ở test_web2api_nv36_phien_giao_dien.py (tự bỏ qua khi thiếu Chrome).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung, phien, phien_web, so_module
from app.main import app

KHOA_QT = "khoa-quan-tri-nv36-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
BI_MAT_GIA = "phien-bi-mat-gia-nv36-000"

CAC_TRUONG_CHO_PHEP = {
    "module", "tai_khoan", "luc_luu", "luc_kiem", "het_han", "trang_thai", "so_lan_loi",
}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def _gieo_phien():
    phien.luu("mod-nv36", "tk-mot", {"token": BI_MAT_GIA, "cookie": "phien=xyz"}, het_han=None)
    phien.luu("mod-nv36", "tk-hai", {"token": BI_MAT_GIA + "-2"}, het_han="2000-01-01T00:00:00+00:00")


# ───────────── 1. Hợp đồng dữ liệu cho giao diện ─────────────

def test_get_phien_chi_tra_sieu_du_lieu(du_lieu):
    _gieo_phien()
    with TestClient(app) as c:
        r = c.get("/api/phien", headers=QT)
        assert r.status_code == 200
        j = r.json()
        assert set(j) == {"phien", "tong", "can_dang_nhap_tong"}
        assert j["tong"] == 2
        assert j["can_dang_nhap_tong"] == 1  # tk-hai hết hạn
        for muc in j["phien"]:
            assert set(muc) <= CAC_TRUONG_CHO_PHEP, f"lộ trường lạ: {set(muc) - CAC_TRUONG_CHO_PHEP}"
        than = r.text
        assert BI_MAT_GIA not in than, "chuỗi bí mật lọt vào phản hồi JSON"


def test_get_phien_rong(du_lieu):
    with TestClient(app) as c:
        r = c.get("/api/phien", headers=QT)
        assert r.status_code == 200
        assert r.json() == {"phien": [], "tong": 0, "can_dang_nhap_tong": 0}


# ───────────── 2. Ma trận xác thực ─────────────

@pytest.mark.parametrize("phuong_thuc,duong", [
    ("GET", "/api/phien"),
    ("DELETE", "/api/phien/mod-nv36/tk-mot"),
    ("POST", "/api/phien/mod-nv36/tk-mot/kiem"),
])
def test_khong_khoa_tra_401(du_lieu, phuong_thuc, duong):
    _gieo_phien()
    with TestClient(app) as c:
        r = c.request(phuong_thuc, duong)
        assert r.status_code == 401, f"{phuong_thuc} {duong} không khoá phải 401"


@pytest.mark.parametrize("phuong_thuc,duong", [
    ("GET", "/api/phien"),
    ("DELETE", "/api/phien/mod-nv36/tk-mot"),
    ("POST", "/api/phien/mod-nv36/tk-mot/kiem"),
])
def test_khoa_sai_tra_401(du_lieu, phuong_thuc, duong):
    _gieo_phien()
    with TestClient(app) as c:
        r = c.request(phuong_thuc, duong, headers={"Authorization": "Bearer sai-hoan-toan"})
        assert r.status_code == 401


@pytest.mark.parametrize("phuong_thuc,duong", [
    ("GET", "/api/phien"),
    ("DELETE", "/api/phien/mod-nv36/tk-mot"),
    ("POST", "/api/phien/mod-nv36/tk-mot/kiem"),
])
def test_phien_user_bi_chan_403(du_lieu, phuong_thuc, duong):
    _gieo_phien()
    u_ban_ghi, _, _ = asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap="user-nv36", mat_khau="MatKhauUserNv36_123"))
    p_ban_ghi, token_tho = phien_web.tao_phien(u_ban_ghi["id"])
    with TestClient(app) as c:
        r = c.request(phuong_thuc, duong, cookies={"w2a_phien": token_tho},
                      headers={"X-CSRF-Token": p_ban_ghi["csrf"]})
        assert r.status_code == 403, f"{phuong_thuc} {duong} phiên user phải 403"


# ───────────── 3. IDOR / đường dẫn ─────────────

def test_xoa_kiem_khong_ton_tai_tra_404(du_lieu):
    with TestClient(app) as c:
        assert c.delete("/api/phien/mod-nv36/khong-co", headers=QT).status_code == 404
        assert c.post("/api/phien/mod-nv36/khong-co/kiem", headers=QT).status_code == 404


def test_id_chua_dau_cham_tra_400_khong_cham_tep_ngoai(du_lieu, tmp_path):
    with TestClient(app) as c:
        truoc = {p for p in tmp_path.rglob("*")}  # sau khi app đã khởi động xong
        # ".." nằm trong một đoạn đường → 400 ở tầng route, không chạm đĩa
        assert c.delete("/api/phien/..bad/acc", headers=QT).status_code == 400
        assert c.delete("/api/phien/a..b/acc", headers=QT).status_code == 400
        assert c.post("/api/phien/.../acc/kiem", headers=QT).status_code == 400
        sau = {p for p in tmp_path.rglob("*")} - truoc
        assert sau == set(), f"yêu cầu 400 không được chạm đĩa, nhưng đã tạo: {sau}"


def test_xoa_phien_xong_mat_khoi_danh_sach(du_lieu):
    _gieo_phien()
    with TestClient(app) as c:
        r = c.delete("/api/phien/mod-nv36/tk-mot", headers=QT)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        j = c.get("/api/phien", headers=QT).json()
        assert j["tong"] == 1
        assert [p["tai_khoan"] for p in j["phien"]] == ["tk-hai"]


# ───────────── 4. Kiểm tra tĩnh trang (không cần Chrome) ─────────────

TRANG = Path(__file__).resolve().parents[1] / "web" / "He-thong-Phien.dc.html"


def test_trang_ton_tai_va_khoi_thu_gon_dong_mac_dinh():
    html = TRANG.read_text(encoding="utf-8")
    # Nút thật <button> với aria-expanded + aria-controls cho mỗi nhóm module
    assert re.search(r'<button[^>]*aria-expanded="\{\{m\.moChu\}\}"[^>]*aria-controls="\{\{m\.idVung\}\}"', html), \
        "thiếu nút thu gọn đúng chuẩn (aria-expanded + aria-controls)"
    # Đóng mặc định: nhomMo khởi tạo rỗng, mo = !!s.nhomMo[mid] → false
    assert "nhomMo: {}" in html
    assert re.search(r"const mo = !!s\.nhomMo\[mid\]", html)


def test_trang_khong_innerHTML_voi_du_lieu_api():
    html = TRANG.read_text(encoding="utf-8")
    assert "innerHTML" not in html, "cấm innerHTML với dữ liệu từ API (dùng liên kết dữ liệu dc-lite)"


def test_trang_co_data_thuoc_tinh_cho_test_va_xss():
    html = TRANG.read_text(encoding="utf-8")
    assert 'data-phien="{{p.khoa}}"' in html
    assert 'data-kiem="{{p.khoa}}"' in html
    assert 'data-xoa="{{p.khoa}}"' in html
    assert 'data-mo-nhom="{{m.id}}"' in html
    # Tên tài khoản chỉ qua {{p.taiKhoan}} (textContent của dc-lite), không qua thuộc tính sự kiện
    assert "onClick=\"{{p.taiKhoan}}\"" not in html


def test_trang_xoa_phien_hai_buoc_xac_nhan():
    html = TRANG.read_text(encoding="utf-8")
    assert "Chắc chắn xoá?" in html
    assert 'data-xoa-xac-nhan="{{p.khoa}}"' in html
    assert "Giữ lại" in html
