"""NV258 P1 — KHU TÀI KHOẢN lưu toàn bộ tài khoản + API: lược đồ + API.

Giá trị GIẢ toàn bộ (quy ước spec: không dùng khoá thật). Bao phủ:
- lưu khoá API/token/cookie KHÔNG cần email (id theo bí mật);
- kiểm theo loại (gmail/web cần email+mật khẩu; api cần khoá; token/cookie
  cần giá trị; kieu_bi_mat đóng trong 4 giá trị);
- dia_chi: URL qua bộ kiểm SSRF của lõi (chỉ http/https), email kiểm như
  email đăng nhập;
- bản ghi CŨ đọc lại đúng (kieu_bi_mat đọc như mat_khau);
- bí mật KHÔNG lộ qua API (chỉ dạng che + cờ «đã có») và KHÔNG vào nhật ký;
- route cũ KHÔNG đổi hợp đồng phản hồi (chỉ THÊM trường).
"""

from __future__ import annotations

import json

import pytest

_KHOA_QT = "khoa-qt-nv258-p1-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}

_KHOA_GIA = "sk-gia-nv258-0123456789abcdef"
_TOKEN_GIA = "tok-gia-nv258-0123456789"
_COOKIE_GIA = "ck-gia-nv258-0123456789"
_MK_GIA = "MatKhauGia123!"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


def _nhat_ky_text(data_dir) -> str:
    p = data_dir / "nhat_ky" / "nhat_ky.jsonl"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


@pytest.fixture()
def kho_tam(tmp_path, monkeypatch):
    """Kho THẬT trên thư mục tạm — cho test gọi thẳng module (không HTTP)."""
    d = tmp_path / "data-kho"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(d))
    from app import kho_tai_khoan as k
    return k


# ── (a) lưu khoá API/token/cookie không cần email ──────────────────────────

def test_luu_khoa_api_khong_can_email(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA,
        "dia_chi": "https://api.nha-cung-cap-gia.example/v1",
        "nhan": "khoá giả NV258",
    })
    assert r.status_code == 200, r.text
    bk = r.json()["tai_khoan"]
    assert "email" not in bk and "@" not in bk["email_che"]
    assert bk["kieu_bi_mat"] == "khoa_api"
    assert bk["co_bi_mat"] is True
    assert bk["dia_chi_che"].startswith("https://")
    assert "api.nha-cung-cap-gia.example" not in bk["dia_chi_che"]


def test_luu_token_va_cookie(moi_truong):
    client, _ = moi_truong
    for kieu, gia_tri in (("token", _TOKEN_GIA), ("cookie", _COOKIE_GIA)):
        r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
            "kieu_bi_mat": kieu, "bi_mat": gia_tri, "nhan": "giả"})
        assert r.status_code == 200, r.text
        bk = r.json()["tai_khoan"]
        assert bk["kieu_bi_mat"] == kieu
        assert bk["co_bi_mat"] is True
        assert "email" not in bk and "@" not in bk["email_che"]


def test_trung_khoa_cap_nhat_khong_trung(moi_truong):
    client, _ = moi_truong
    b1 = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA, "nhan": "một"}).json()["tai_khoan"]
    b2 = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA, "nhan": "hai"}).json()["tai_khoan"]
    assert b1["id"] == b2["id"]
    ds = client.get("/api/tai-khoan/da-luu", headers=_QT).json()["tai_khoan"]
    assert sum(1 for t in ds if t["id"] == b1["id"]) == 1


# ── (b) kiểm theo loại ─────────────────────────────────────────────────────

def test_khoa_api_thieu_bi_mat_400(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"kieu_bi_mat": "khoa_api", "nhan": "thiếu khoá"})
    assert r.status_code == 400, r.text


def test_kieu_bi_mat_la_400(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "khoa_bua", "bi_mat": "x"})
    assert r.status_code == 400, r.text


def test_dia_chi_url_sai_400(moi_truong):
    client, _ = moi_truong
    for url_xau in ("ftp://may-gia.example/k", "javascript:alert(1)",
                    "http://nguoi:pass@may-gia.example/",
                    "https://may-gia.example/?q=1",
                    "https://127.0.0.1/", "https://localhost:8080/"):
        r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
            "kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA + url_xau[:3],
            "dia_chi": url_xau, "loai_tai_khoan": "api"})
        assert r.status_code == 400, (url_xau, r.text)


def test_dia_chi_email_sai_400(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "nguoi-gia@example.com", "mat_khau": _MK_GIA,
        "dia_chi": "không-phải-email", "loai_tai_khoan": "gmail"})
    assert r.status_code == 400, r.text


def test_mat_khau_cu_van_bat_buoc_email(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"mat_khau": _MK_GIA})
    assert r.status_code == 400, r.text
    r = client.post("/api/tai-khoan/da-luu", headers=_QT,
                    json={"email": "nguoi-gia@example.com"})
    assert r.status_code == 400, r.text


def test_bi_mat_thay_mat_khau_cho_kieu_mac_dinh(moi_truong):
    """Đường mới: gửi bi_mat mà không khai kieu_bi_mat → đọc như mat_khau."""
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "nguoi-gia@example.com", "bi_mat": _MK_GIA})
    assert r.status_code == 200, r.text
    bk = r.json()["tai_khoan"]
    assert bk["kieu_bi_mat"] == "mat_khau"
    assert bk["email_che"]


# ── (c) tương thích bản ghi cũ ──────────────────────────────────────────────

def test_ban_ghi_cu_doc_lai_dung(kho_tam):
    k = kho_tam

    bk = k.luu_tai_khoan("cu-nv258@example.com", _MK_GIA)
    assert bk["kieu_bi_mat"] == "mat_khau"
    assert bk["dia_chi_che"] == ""
    assert bk["co_bi_mat"] is True
    # «mat_khau»/«bi_mat» thật KHÔNG có trong bản công khai.
    assert "mat_khau" not in bk and "bi_mat" not in bk
    assert "totp_secret" not in json.dumps(bk)
    # Hợp đồng cũ còn nguyên.
    for truong in ("id", "email_che", "nhan", "trang_thai", "co_mat_khau"):
        assert truong in bk


def test_lay_bi_mat_ban_ghi_cu(kho_tam):
    k = kho_tam

    bk = k.luu_tai_khoan("cu2-nv258@example.com", _MK_GIA)
    got = k.lay_bi_mat_dang_nhap(bk["id"])
    assert got is not None
    mat_khau, _totp = got
    try:
        assert mat_khau.lay() == _MK_GIA
    finally:
        mat_khau.xoa()


def test_lay_bi_mat_ban_ghi_khoa_api(kho_tam):
    k = kho_tam

    bk = k.luu_tai_khoan(None, None, bi_mat=_KHOA_GIA, kieu_bi_mat="khoa_api")
    got = k.lay_bi_mat_dang_nhap(bk["id"])
    assert got is not None
    bi_mat, _totp = got
    try:
        assert bi_mat.lay() == _KHOA_GIA
    finally:
        bi_mat.xoa()


def test_cap_nhat_khong_truyen_bi_mat_giu_cu(kho_tam):
    k = kho_tam

    bk = k.luu_tai_khoan("giucz-nv258@example.com", _MK_GIA)
    bk2 = k.luu_tai_khoan(None, None, id=bk["id"], nhan="đổi nhãn")
    assert bk2["id"] == bk["id"]
    got = k.lay_bi_mat_dang_nhap(bk["id"])
    assert got is not None
    mat_khau, _totp = got
    try:
        assert mat_khau.lay() == _MK_GIA
    finally:
        mat_khau.xoa()


# ── (d) bí mật không lộ qua API / nhật ký ───────────────────────────────────

def test_bi_mat_khong_lo_qua_api(moi_truong):
    client, _ = moi_truong
    client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA,
        "dia_chi": "https://api.nha-cung-cap-gia.example/v1"})
    for duong in ("/api/tai-khoan/da-luu", "/api/tai-khoan/nhom-loai",
                  "/api/tai-khoan/bang-phan-loai"):
        r = client.get(duong, headers=_QT)
        assert r.status_code == 200, r.text
        assert _KHOA_GIA not in r.text
        assert _TOKEN_GIA not in r.text
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    for t in r.json()["tai_khoan"]:
        assert "bi_mat" not in t and "mat_khau" not in t
        assert "totp_secret" not in t


def test_bi_mat_khong_vao_nhat_ky(moi_truong):
    client, data_dir = moi_truong
    client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "kieu_bi_mat": "token", "bi_mat": _TOKEN_GIA, "nhan": "giả"})
    client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "nk-nv258@example.com", "mat_khau": _MK_GIA})
    nk = _nhat_ky_text(data_dir)
    assert _TOKEN_GIA not in nk
    assert _MK_GIA not in nk
    assert _KHOA_GIA not in nk


# ── (e) che địa chỉ ────────────────────────────────────────────────────────

def test_che_dia_chi_email():
    from app.kho_tai_khoan import che_dia_chi, che_email

    assert che_dia_chi("nguoi@example.com") == che_email("nguoi@example.com")
    assert "nguoi" not in che_dia_chi("nguoi@example.com")


def test_che_dia_chi_url():
    from app.kho_tai_khoan import che_dia_chi

    che = che_dia_chi("https://api.nha-cung-cap-gia.example/v1/khoa")
    assert che.startswith("https://")
    assert "api.nha-cung-cap-gia.example" not in che
    assert "/v1" not in che


def test_che_dia_chi_ip_che_het():
    from app.kho_tai_khoan import che_dia_chi

    assert che_dia_chi("http://10.0.0.9:8080/") == "http://***"
    assert che_dia_chi("") == ""


# ── (f) hợp đồng route cũ không đổi ─────────────────────────────────────────

def test_hop_dong_route_cu_khong_doi(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": "hd-nv258@example.com", "mat_khau": _MK_GIA,
        "nhan": "hợp đồng cũ"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"tai_khoan"}
    bk = body["tai_khoan"]
    # Các trường cũ vẫn hiện diện và đúng ngữ nghĩa.
    assert bk["email_che"] and "@" not in bk["email_che"].split("@")[0]
    # Ngữ nghĩa «thanh_cong» thuộc route đăng nhập — P1 không đụng tới.


def test_bang_phan_loai_co_kieu_bi_mat(moi_truong):
    client, _ = moi_truong
    r = client.get("/api/tai-khoan/bang-phan-loai", headers=_QT)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["kieu_bi_mat"] == ["mat_khau", "khoa_api", "token", "cookie"]
    assert j["ten_kieu_bi_mat"]["khoa_api"] == "Khoá API"


def test_luu_hang_loat_khoa_api(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={"tai_khoan": [
        {"kieu_bi_mat": "khoa_api", "bi_mat": _KHOA_GIA, "nhan": "k1"},
        {"email": "hang-loat-nv258@example.com", "mat_khau": _MK_GIA},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["luu"]) == 2 and not body["loi"]
    assert body["luu"][0]["kieu_bi_mat"] == "khoa_api"
    assert body["luu"][1]["kieu_bi_mat"] == "mat_khau"
