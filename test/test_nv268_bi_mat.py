"""NV268 — đường lấy bí mật khu «Tài khoản» (POST /api/tai-khoan/bi-mat).

Hợp đồng (từ viec-nv268):
- Chỉ quản trị viên; CHỈ POST (không có GET → 405); Cache-Control: no-store.
- hanh_dong=bi_mat: trả bí mật thô + kiểu, CHỈ khi người dùng bấm «Hiện»/«Chép».
- hanh_dong=otp: mã 6 số TÍNH Ở MÁY CHỦ theo RFC 6238 (SHA-1, 30s) + số giây
  còn lại; khoá TOTP KHÔNG BAO GIỜ rời máy chủ.
- Người dùng thường (khoá khách) → 403; không khoá → 401.
- Nhật ký chỉ ghi ai/bản ghi nào/lúc nào/hành động — KHÔNG ghi giá trị bí mật.
- Phản hồi danh sách (/da-luu, /nhom-loai) KHÔNG chứa bí mật.

Giá trị GIẢ toàn bộ (quy ước spec): không in/ghi khoá thật vào test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import nhat_ky as nhat_ky_mod
from app import so_module, xac_thuc_2fa
from app import kho_tai_khoan as ktk
from app.main import app

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

# Toàn bộ GIẢ (quy ước spec).
MAT_KHAU_GIA = "MatKhauGiaNV268#1"
KHOA_API_GIA = "AIza-Gia-NV268-0123456789abcdef"
TOTP_GIA = "JBSWY3DPEHPK3PXP"  # base32 ví dụ chuẩn, không phải khoá thật
EMAIL_GIA = "nv268-gia@example.com"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with TestClient(app) as client:
        yield client


def _tao(client, **kw):
    bk = ktk.luu_tai_khoan(
        email=kw.get("email", EMAIL_GIA),
        mat_khau=kw.get("mat_khau", MAT_KHAU_GIA),
        totp_secret=kw.get("totp_secret", TOTP_GIA),
        nhan=kw.get("nhan", "giả"),
        nhom=kw.get("nhom", "google"),
        loai_tai_khoan=kw.get("loai_tai_khoan", "gmail"),
        dia_chi=kw.get("dia_chi", EMAIL_GIA),
        kieu_bi_mat=kw.get("kieu_bi_mat", "mat_khau"),
        bi_mat=kw.get("bi_mat"),
    )
    return bk["id"]


def _lay(client, id_, hanh_dong, headers=QT):
    return client.post("/api/tai-khoan/bi-mat", headers=headers,
                       json={"id": id_, "hanh_dong": hanh_dong})


# ── RFC 6238: vector chuẩn Appendix B (khoá SHA-1 20 byte ASCII) ─────────────

@pytest.mark.parametrize("moc,ky_vong", [
    (59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
    (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130"),
])
def test_totp_dung_rfc_6238(moc, ky_vong):
    assert xac_thuc_2fa.sinh_totp(b"12345678901234567890",
                                  moc_thoi_gian=moc) == ky_vong


# ── Đường lấy bí mật ─────────────────────────────────────────────────────────

def test_hien_bi_mat_tra_gia_tri_va_no_store(moi_truong):
    client = moi_truong
    id_ = _tao(client)
    r = _lay(client, id_, "bi_mat")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    assert body["bi_mat"] == MAT_KHAU_GIA
    assert body["kieu"] == "mat_khau"
    assert "totp" not in r.text.lower()


def test_hien_khoa_api_tra_dung_kieu(moi_truong):
    client = moi_truong
    id_ = _tao(client, email="api-gia@example.com",
               nhom="chatgpt", loai_tai_khoan="api",
               dia_chi="https://api.example.com",
               mat_khau=None, bi_mat=KHOA_API_GIA, kieu_bi_mat="khoa_api",
               totp_secret=None)
    r = _lay(client, id_, "bi_mat")
    assert r.status_code == 200, r.text
    assert r.json() == {"kieu": "khoa_api", "bi_mat": KHOA_API_GIA}


def test_otp_tinh_o_may_chu_khoa_khong_roi(moi_truong):
    client = moi_truong
    id_ = _tao(client)
    r = _lay(client, id_, "otp")
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    body = r.json()
    assert body["ma"] == xac_thuc_2fa.sinh_totp(TOTP_GIA)
    assert body["ma"].isdigit() and len(body["ma"]) == 6
    assert 1 <= body["con_lai_giay"] <= 30
    assert TOTP_GIA not in r.text  # khoá TOTP KHÔNG BAO GIỜ rời máy chủ


def test_otp_khong_co_totp_400(moi_truong):
    client = moi_truong
    id_ = _tao(client, totp_secret=None)
    r = _lay(client, id_, "otp")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "khong_co_totp"


def test_id_la_404(moi_truong):
    client = moi_truong
    r = _lay(client, "tk-0000000000000000", "bi_mat")
    assert r.status_code == 404


def test_thieu_id_400_va_hanh_dong_la_400(moi_truong):
    client = moi_truong
    assert client.post("/api/tai-khoan/bi-mat", headers=QT,
                       json={"hanh_dong": "bi_mat"}).status_code == 400
    assert _lay(client, "tk-x", "xoa").status_code == 400
    assert client.post("/api/tai-khoan/bi-mat", headers=QT,
                       json="khong-phai-doi-tuong").status_code == 400


def test_chi_post_khong_co_get(moi_truong):
    client = moi_truong
    # Lõi trả 404 cho phương thức không đăng ký trên đường đã có (hành vi
    # chung của mọi route chỉ-POST trong app, không riêng NV268) — không có
    # đường GET nào trả bí mật.
    assert client.get("/api/tai-khoan/bi-mat", headers=QT).status_code == 404


def test_khong_khoa_401_khoa_khach_403(moi_truong):
    client = moi_truong
    id_ = _tao(client)
    assert _lay(client, id_, "bi_mat", headers={}).status_code == 401
    from app import khoa_khach as kk_mod
    _muc, khoa_tho = kk_mod.tao_khoa(nhan="khach-nv268")
    hk = {"Authorization": f"Bearer {khoa_tho}"}
    assert _lay(client, id_, "bi_mat", headers=hk).status_code == 403
    assert _lay(client, id_, "otp", headers=hk).status_code == 403


def test_danh_sach_khong_chua_bi_mat(moi_truong):
    client = moi_truong
    _tao(client)
    for duong in ("/api/tai-khoan/da-luu", "/api/tai-khoan/nhom-loai"):
        r = client.get(duong, headers=QT)
        assert r.status_code == 200, duong
        assert MAT_KHAU_GIA not in r.text, duong
        assert TOTP_GIA not in r.text, duong


def test_nhat_ky_khong_chua_bi_mat(moi_truong):
    client = moi_truong
    id_ = _tao(client)
    assert _lay(client, id_, "bi_mat").status_code == 200
    assert _lay(client, id_, "otp").status_code == 200
    cac = nhat_ky_mod.doc(kenh="quan_tri", muc="thong_tin")
    van_ban = " ".join(str(b.get("tom_tat", "")) + " " + str(b.get("them", ""))
                       for b in cac)
    assert "Xem bí mật tài khoản đã lưu" in van_ban
    assert "Đọc mã OTP tài khoản đã lưu" in van_ban
    assert MAT_KHAU_GIA not in van_ban
    assert TOTP_GIA not in van_ban
    assert KHOA_API_GIA not in van_ban
