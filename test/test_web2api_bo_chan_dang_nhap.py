"""Bỏ chặn đăng nhập (tab Bảo mật): người dùng quên mật khẩu, thử nhiều lần, bị chặn → quản trị bỏ chặn được.

Đường đăng nhập CỐ Ý không báo «đang bị khoá» (một thông báo chung «sai tên hoặc mật khẩu») nên người dùng không tự biết;
vì vậy quản trị cần danh sách khoá đang hiệu lực + nút bỏ chặn. Kiểm:
1. Sai quá ngưỡng → mật khẩu ĐÚNG cũng bị từ chối; danh sách thấy khoá; bỏ chặn → vào được.
2. Bỏ chặn theo tài khoản / theo IP / tất cả; chỉ đúng đối tượng được bỏ.
3. Leo thang quyền: không khoá → 401; vai user → 403 (cả xem lẫn bỏ chặn); thân sai → 400; lặp lại vô hại.
4. Danh sách không lộ mật khẩu, số lần sai hay bất kỳ bí mật nào.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nguoi_dung
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-bo-chan-0123456789abcdef"
MK = "MatKhauDungBoChan123456"
MK_ADMIN = "MatKhauQuanTriBoChan123456"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "3")
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")  # không để khoá IP can thiệp phần thử khoá tên
    nguoi_dung._THEO_DOI_SAI.clear()
    gioi_han.khoi_dong()
    with TestClient(app) as client:
        client._transport.client = ("127.0.0.1", 1234)
        client.app.state.so = SoModule()
        yield client


def _tao(ten, vai, mk):
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=mk))


def _dang_nhap_admin(client):
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "qt-bo-chan", "mat_khau": MK_ADMIN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def _lam_sai(client, ten, so_lan):
    for _ in range(so_lan):
        client.post("/api/dang-nhap", json={"ten_dang_nhap": ten, "mat_khau": "sai-mat-khau-12345"})


def test_bi_chan_roi_bo_chan_thi_vao_duoc(moi_truong):
    client = moi_truong
    _tao("nguoi-quen-mk", "user", MK)
    _tao("qt-bo-chan", "admin", MK_ADMIN)
    _lam_sai(client, "nguoi-quen-mk", 3)
    # Mật khẩu ĐÚNG vẫn bị từ chối (đang bị chặn) và thông báo không lộ việc khoá
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nguoi-quen-mk", "mat_khau": MK})
    assert r.status_code == 401 and "khoá" not in r.text.lower()

    client.cookies.clear()
    hdr = _dang_nhap_admin(client)
    ds = client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"]
    tk = [m for m in ds if m["ten"] == "nguoi-quen-mk"]
    assert tk and all(m["con_lai_giay"] > 0 for m in tk), ds
    assert set(tk[0]) == {"loai", "ten", "ip", "con_lai_giay"}, "không lộ trường nào khác (số lần sai, mật khẩu…)"

    r_bo = client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tai_khoan", "ten": "nguoi-quen-mk"}, headers=hdr)
    assert r_bo.status_code == 200 and r_bo.json()["da_bo"] >= 1
    assert not [m for m in client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"] if m["ten"] == "nguoi-quen-mk"]
    # Lặp lại vô hại
    r_lai = client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tai_khoan", "ten": "nguoi-quen-mk"}, headers=hdr)
    assert r_lai.status_code == 200 and r_lai.json()["da_bo"] == 0

    client.cookies.clear()
    r_vao = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nguoi-quen-mk", "mat_khau": MK})
    assert r_vao.status_code == 200 and "csrf" in r_vao.json()


def test_bo_chan_chi_dung_doi_tuong(moi_truong):
    client = moi_truong
    for ten in ("nguoi-a", "nguoi-b"):
        _tao(ten, "user", MK)
    _tao("qt-bo-chan", "admin", MK_ADMIN)
    _lam_sai(client, "nguoi-a", 3)
    _lam_sai(client, "nguoi-b", 3)
    client.cookies.clear()
    hdr = _dang_nhap_admin(client)
    client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tai_khoan", "ten": "nguoi-a"}, headers=hdr)
    con = {m["ten"] for m in client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"]}
    assert "nguoi-a" not in con and "nguoi-b" in con, "chỉ bỏ chặn tài khoản được chọn"
    # tất cả
    r = client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tat_ca"}, headers=hdr)
    assert r.status_code == 200
    assert client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"] == []


def test_bo_chan_theo_ip_gom_khoa_cap_va_khoa_ip(moi_truong):
    client = moi_truong
    _tao("qt-bo-chan", "admin", MK_ADMIN)
    # khoá cặp (IP, tên) bằng bộ đếm trực tiếp + khoá IP bằng bộ giới hạn xác thực
    for _ in range(3):
        nguoi_dung.ghi_nhan_dang_nhap_sai("nguoi-c", ip="10.9.8.7")
    bo = gioi_han._bo()
    for _ in range(bo.toi_da_loi + 1):
        bo.ghi_loi("10.9.8.7")
    hdr = _dang_nhap_admin(client)
    ds = client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"]
    loai = {(m["loai"], m["ip"]) for m in ds if m["ip"] == "10.9.8.7"}
    assert ("cap_ip_ten", "10.9.8.7") in loai and ("ip", "10.9.8.7") in loai, ds
    r = client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "ip", "ip": "10.9.8.7"}, headers=hdr)
    assert r.status_code == 200 and r.json()["da_bo"] >= 2
    assert not [m for m in client.get("/api/bao-mat/khoa-dang-nhap").json()["khoa"] if m["ip"] == "10.9.8.7"]


def test_phan_quyen_bo_chan(moi_truong):
    client = moi_truong
    _tao("nguoi-thuong", "user", MK)
    _tao("nguoi-bi-chan", "user", MK)
    _lam_sai(client, "nguoi-bi-chan", 3)
    # không khoá → 401
    client.cookies.clear()
    assert client.get("/api/bao-mat/khoa-dang-nhap").status_code == 401
    assert client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json={"loai": "tat_ca"}).status_code == 401
    # vai user (phiên hợp lệ) → 403 cả xem lẫn bỏ chặn; không tự bỏ chặn cho mình hay người khác
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nguoi-thuong", "mat_khau": MK})
    hdr = {"X-CSRF-Token": r.json()["csrf"]}
    assert client.get("/api/bao-mat/khoa-dang-nhap").status_code == 403
    for than in ({"loai": "tat_ca"}, {"loai": "tai_khoan", "ten": "nguoi-bi-chan"}, {"loai": "ip", "ip": "127.0.0.1"}):
        assert client.post("/api/bao-mat/khoa-dang-nhap/bo-chan", json=than, headers=hdr).status_code == 403, than
    # khoá của người bị chặn còn nguyên
    assert any(m["ten"] == "nguoi-bi-chan" for m in nguoi_dung.liet_ke_khoa_dang_nhap())


def test_than_sai_bi_tu_choi(moi_truong):
    client = moi_truong
    _tao("qt-bo-chan", "admin", MK_ADMIN)
    hdr = _dang_nhap_admin(client)
    url = "/api/bao-mat/khoa-dang-nhap/bo-chan"
    assert client.post(url, json={"loai": "la"}, headers=hdr).status_code == 400
    assert client.post(url, json={"loai": "tai_khoan"}, headers=hdr).status_code == 400
    assert client.post(url, json={"loai": "ip"}, headers=hdr).status_code == 400
    assert client.post(url, json={"loai": "tai_khoan", "ten": 123}, headers=hdr).status_code == 400
    # tên không thể là tài khoản (ký tự lạ) → không khớp mục nào, không lỗi, không bỏ nhầm
    _tao("nguoi-giu", "user", MK)
    _lam_sai(client, "nguoi-giu", 3)
    r = client.post(url, json={"loai": "tai_khoan", "ten": "../../x"}, headers=hdr)
    assert r.status_code == 200 and r.json()["da_bo"] == 0
    assert any(m["ten"] == "nguoi-giu" for m in nguoi_dung.liet_ke_khoa_dang_nhap())


def test_2fa_sai_ma_van_tinh_vao_khoa_dang_nhap(moi_truong):
    """Mật khẩu đúng nhưng người dùng bật 2FA: chưa xoá bộ đếm sai ở bước một (chỉ xoá khi qua bước hai)."""
    client = moi_truong
    _tao("nguoi-2fa", "user", MK)
    uid = nguoi_dung.tim_theo_ten("nguoi-2fa")["id"]
    from app import xac_thuc_2fa
    bi_mat, _ = xac_thuc_2fa.bat_dau_dang_ky(uid, "nguoi-2fa")
    assert xac_thuc_2fa.xac_nhan_dang_ky(uid, xac_thuc_2fa.sinh_totp(bi_mat))[0]
    _lam_sai(client, "nguoi-2fa", 2)  # 2/3 lần sai
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "nguoi-2fa", "mat_khau": MK})
    assert r.status_code == 401 and r.json()["error"]["code"] == "can_ma_2fa"
    # đếm sai KHÔNG bị xoá: một lần sai nữa là bị chặn
    _lam_sai(client, "nguoi-2fa", 1)
    assert any(m["ten"] == "nguoi-2fa" for m in nguoi_dung.liet_ke_khoa_dang_nhap())
