"""NV51 — test trước (đỏ) cho xác nhận lại thao tác nguy hiểm (step-up).

Bối cảnh: báo cáo NV46 v2, mục 3(b) — các thao tác nguy hiểm của quản trị phải yêu cầu
xác nhận lại trong cửa sổ ngắn sau đăng nhập. Hiện tại KHÔNG có cơ chế nào: đăng nhập
xong là làm được ngay mọi thao tác nguy hiểm. Mọi test đánh dấu xfail(strict=True) dưới
đây đều ĐỎ trên main hiện tại và sẽ XANH khi cơ chế step-up được triển khai.

Hành vi mong đợi (theo spec NV51):
- Không xác nhận lại → 401 hoặc 403, lý do chung chung (không lộ lý do sai).
- Xác nhận hết hạn → buộc làm lại (lại 401/403).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-qt-nv51-aaa-000000000000000000000000"
KHOA_SAI = "khoa-sai-nv51-bbb-000000000000000000000000"
MK_ADMIN = "MatKhauAdminGia000000000000"
MK_USER = "MatKhauUserGia000000000000"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "30")
    monkeypatch.setenv("WEB2API_RL_KHOA_GIAY", "60")
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "5")
    monkeypatch.setenv("WEB2API_DANG_NHAP_KHOA_PHUT", "15")
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client, data_dir


def _dang_nhap_admin(client):
    """Tạo admin, đăng nhập web, trả về (cookies, headers CSRF)."""
    asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="admin", ten_dang_nhap="admin-stepup", mat_khau=MK_ADMIN
        )
    )
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "admin-stepup", "mat_khau": MK_ADMIN},
    )
    assert r.status_code == 200, f"đăng nhập admin phải 200, nhận {r.status_code}"
    tok = r.cookies.get("w2a_phien")
    csrf = r.json()["csrf"]
    assert tok and csrf
    return {"w2a_phien": tok}, {"X-CSRF-Token": csrf}


# ─────────────────────────────────────────────────────────────────────────────
# STEP-UP: thao tác nguy hiểm ngay sau đăng nhập, chưa xác nhận lại → 401/403
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True, reason="NV51 SU1: đặt lại mật khẩu người khác chưa đòi xác nhận lại"
)
def test_stepup_dat_lai_mat_khau_doi_xac_nhan_lai(moi_truong):
    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    nan_nhan, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="nan-nhan-su1", mat_khau=MK_USER
        )
    )
    r = client.post(
        f"/api/nguoi-dung/{nan_nhan['id']}/dat-lai-mat-khau",
        cookies=cookies,
        headers=headers,
    )
    assert r.status_code in (401, 403), (
        f"đặt lại mật khẩu người khác mà chưa xác nhận lại phải bị 401/403, "
        f"nhận {r.status_code}"
    )


@pytest.mark.xfail(
    strict=True, reason="NV51 SU2: tạo tài khoản admin mới chưa đòi xác nhận lại"
)
def test_stepup_tao_admin_moi_doi_xac_nhan_lai(moi_truong):
    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    r = client.post(
        "/api/nguoi-dung",
        json={
            "ten_dang_nhap": "admin-moi-su2",
            "mat_khau": "MatKhauMoiGia000000000000",
            "vai": "admin",
        },
        cookies=cookies,
        headers=headers,
    )
    assert r.status_code in (401, 403), (
        f"tạo admin mới mà chưa xác nhận lại phải bị 401/403, nhận {r.status_code}"
    )


@pytest.mark.xfail(
    strict=True, reason="NV51 SU3: hạ vai admin xuống user chưa đòi xác nhận lại"
)
def test_stepup_ha_vai_admin_doi_xac_nhan_lai(moi_truong):
    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    admin2, _, _ = asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="admin", ten_dang_nhap="admin2-su3", mat_khau=MK_ADMIN
        )
    )
    r = client.patch(
        f"/api/nguoi-dung/{admin2['id']}",
        json={"vai": "user"},
        cookies=cookies,
        headers=headers,
    )
    assert r.status_code in (401, 403), (
        f"hạ vai admin mà chưa xác nhận lại phải bị 401/403, nhận {r.status_code}"
    )


@pytest.mark.xfail(
    strict=True, reason="NV51 SU4: xoay khoá khách chưa đòi xác nhận lại"
)
def test_stepup_xoay_khoa_khach_doi_xac_nhan_lai(moi_truong):
    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    # dựng khoá bằng Bearer (không qua phiên) để bước dựng không bị ảnh hưởng
    # bởi step-up khi cơ chế này được triển khai
    r_tao = client.post(
        "/api/khoa-khach",
        json={"nhan": "khoa-gia-su4"},
        headers={"Authorization": "Bearer " + KHOA_QT},
    )
    assert r_tao.status_code == 200
    kid = r_tao.json()["khoa"]["id"]
    r = client.post(
        f"/api/khoa-khach/{kid}/xoay", json={}, cookies=cookies, headers=headers
    )
    assert r.status_code in (401, 403), (
        f"xoay khoá mà chưa xác nhận lại phải bị 401/403, nhận {r.status_code}"
    )


@pytest.mark.xfail(
    strict=True, reason="NV51 SU5: duyệt phê duyệt chưa đòi xác nhận lại"
)
def test_stepup_duyet_phe_duyet_doi_xac_nhan_lai(moi_truong):
    from app import thiet_bi

    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    muc = thiet_bi.them_hang_cho_phe_duyet(
        ai_de_xuat="khoa-khach-gia",
        thuc_the="mod-gia.thuc-the-su5",
        hanh_dong="hanh-dong-nguy-hiem",
        tham_so={},
    )
    r = client.post(
        f"/api/phe-duyet/{muc['id']}/duyet", cookies=cookies, headers=headers
    )
    assert r.status_code in (401, 403), (
        f"duyệt phê duyệt mà chưa xác nhận lại phải bị 401/403, nhận {r.status_code}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="NV51 SU6: phản hồi khi chưa xác nhận lại phải chung chung, không lộ lý do sai",
)
def test_stepup_ly_do_chung_chung_khong_lo(moi_truong):
    """Khi thiếu xác nhận lại, mã lỗi phải chung chung: không được phân biệt
    «sai mật khẩu xác nhận» với «tài khoản không tồn tại» qua mã trạng thái
    hay nội dung (tránh dò tài khoản)."""
    client, _ = moi_truong
    cookies, headers = _dang_nhap_admin(client)
    r = client.post(
        "/api/nguoi-dung/khong-ton-tai-su6/dat-lai-mat-khau",
        cookies=cookies,
        headers=headers,
    )
    assert r.status_code in (401, 403), (
        f"thiếu xác nhận lại phải 401/403 kể cả khi id không tồn tại, "
        f"nhận {r.status_code}"
    )
    than = r.text.lower()
    assert "không tồn tại" not in than and "not found" not in than, (
        "phản hồi thiếu xác nhận lại không được lộ id có tồn tại hay không"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHÒNG THỦ (xanh trên main): luật xác thực cơ bản cho các đường nguy hiểm
# ─────────────────────────────────────────────────────────────────────────────


def test_phong_thu_khong_khoa_401(moi_truong):
    """Không khoá → 401 trên mọi thao tác nguy hiểm."""
    client, _ = moi_truong
    assert client.post("/api/nguoi-dung", json={}).status_code == 401
    assert (
        client.post("/api/nguoi-dung/x/dat-lai-mat-khau").status_code == 401
    )
    assert client.patch("/api/nguoi-dung/x", json={}).status_code == 401
    assert client.delete("/api/nguoi-dung/x").status_code == 401
    assert client.post("/api/khoa-khach/x/xoay", json={}).status_code == 401
    assert client.post("/api/phe-duyet/x/duyet").status_code == 401
    assert client.put("/api/dia-chi", json={"url": None}).status_code == 401


def test_phong_thu_khoa_sai_401(moi_truong):
    """Khoá sai → 401, không lộ thêm thông tin."""
    client, _ = moi_truong
    h = {"Authorization": f"Bearer {KHOA_SAI}"}
    assert client.post("/api/nguoi-dung", json={}, headers=h).status_code == 401
    assert (
        client.post("/api/phe-duyet/x/duyet", headers=h).status_code == 401
    )


def test_phong_thu_khoa_khach_403_duong_quan_tri(moi_truong):
    """Khoá khách gọi đường quản trị → 403."""
    client, _ = moi_truong
    hk = {"Authorization": "Bearer " + KHOA_QT}
    r = client.post(
        "/api/khoa-khach", json={"nhan": "khoa-gia-phong-thu"}, headers=hk
    )
    assert r.status_code == 200
    khoa_tho = r.json()["khoa"]["khoa"]
    hg = {"Authorization": f"Bearer {khoa_tho}"}
    assert client.post("/api/nguoi-dung", json={}, headers=hg).status_code == 403
    assert (
        client.post("/api/phe-duyet/x/duyet", headers=hg).status_code == 403
    )
    assert (
        client.put("/api/dia-chi", json={"url": None}, headers=hg).status_code
        == 403
    )


def test_phong_thu_vai_user_403_duong_quan_tri(moi_truong):
    """Tài khoản vai user (phiên web) gọi đường quản trị → 403."""
    client, _ = moi_truong
    asyncio.run(
        nguoi_dung.tao_nguoi_dung(
            vai="user", ten_dang_nhap="user-thuong-pt", mat_khau=MK_USER
        )
    )
    r = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "user-thuong-pt", "mat_khau": MK_USER},
    )
    assert r.status_code == 200
    cookies = {"w2a_phien": r.cookies.get("w2a_phien")}
    headers = {"X-CSRF-Token": r.json()["csrf"]}
    assert (
        client.post("/api/nguoi-dung", json={}, cookies=cookies, headers=headers).status_code
        == 403
    )
    assert (
        client.put("/api/dia-chi", json={"url": None}, cookies=cookies, headers=headers).status_code
        == 403
    )


def test_phong_thu_tai_khoan_moi_khong_tu_co_quyen_cao(moi_truong):
    """Tài khoản mới tạo với vai user không tự có quyền cao: không gọi được
    đường quản trị và không tự nâng vai."""
    client, _ = moi_truong
    hk = {"Authorization": "Bearer " + KHOA_QT}
    r = client.post(
        "/api/nguoi-dung",
        json={
            "ten_dang_nhap": "user-moi-pt",
            "mat_khau": MK_USER,
            "vai": "user",
        },
        headers=hk,
    )
    assert r.status_code == 200
    assert r.json()["nguoi_dung"]["vai"] == "user"
    uid = r.json()["nguoi_dung"]["id"]
    # user tự nâng vai chính mình → 403 (đường quản trị)
    r_dn = client.post(
        "/api/dang-nhap",
        json={"ten_dang_nhap": "user-moi-pt", "mat_khau": MK_USER},
    )
    cookies = {"w2a_phien": r_dn.cookies.get("w2a_phien")}
    headers = {"X-CSRF-Token": r_dn.json()["csrf"]}
    r2 = client.patch(
        f"/api/nguoi-dung/{uid}", json={"vai": "admin"}, cookies=cookies, headers=headers
    )
    assert r2.status_code == 403
