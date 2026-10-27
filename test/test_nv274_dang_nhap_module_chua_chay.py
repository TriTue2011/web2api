"""NV274: đăng nhập tài khoản đã lưu qua module CHƯA chạy (vd gemini-web chưa có cookie nên chưa được nạp).

Lỗi đã đo trên server thật (28/10/2026): đăng nhập Google qua cổng MCP trả `chua_ho_tro` vì route chỉ nhìn
`so.dang_chay`; module cần đăng nhập để LẤY cookie lại không chạy khi chưa có cookie (vòng tròn). Lõi đã có
`the_hien_cho_dang_nhap` (thể hiện tạm) cho đường dán hàng loạt; route này dùng cùng nguyên tắc, và việc nối cookie
vào module sở hữu chưa chạy chỉ cần manifest trong catalog (`so.cai` dựng thể hiện chạy)."""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app import tuyen
from app.main import app
from app.so_module import SoModule
from app.tai_khoan_hang_loat import KetQuaDangNhap

KHOA_QT = "khoa-qt-nv274-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MODULE = "gia-chua-chay"


class _LopGia:
    """Module giả CHƯA chạy: lớp có `dang_nhap`, không nằm trong `dang_chay`."""
    lan = 0

    def nap(self, cau_hinh):
        pass

    async def dang_nhap(self, tai_khoan):
        type(self).lan += 1
        return KetQuaDangNhap(True)


@pytest.fixture()
def may(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    _LopGia.lan = 0
    with TestClient(app) as c:
        so = SoModule()
        so.catalog[MODULE] = SimpleNamespace(
            id=MODULE, tien_to=MODULE, lop=_LopGia, tao=lambda: _LopGia(), manifest={"id": MODULE})
        c.app.state.so = so
        yield c


def test_route_dang_nhap_goi_module_chua_chay(may):
    r = may.post("/api/tai-khoan/da-luu", headers=QT, json={"email": "a@example.com", "mat_khau": "MatKhauGia123!"})
    assert r.status_code == 200, r.text
    id_ = r.json()["tai_khoan"]["id"]
    assert MODULE not in may.app.state.so.dang_chay
    kq = may.post(f"/api/tai-khoan/da-luu/{id_}/dang-nhap", headers=QT, json={"module_id": MODULE})
    assert kq.status_code == 200, kq.text            # trước: 502 chua_ho_tro
    assert kq.json()["thanh_cong"] is True
    assert _LopGia.lan == 1


def test_noi_cookie_vao_module_so_huu_chua_chay():
    """Module sở hữu chưa chạy: lõi vẫn nối cookie qua `so.cai` (chỉ cần manifest trong catalog)."""
    cai = mock.AsyncMock(return_value={"ok": True})
    so = SimpleNamespace(dang_chay={}, cai=cai, catalog={"chu": SimpleNamespace(manifest={"truong_cau_hinh": [
        {"khoa": "cookies", "kieu": "danh_sach_bi_mat", "nhan_cookie": {"mien": "example.com"}}]})})
    thuc_thi = SimpleNamespace(trinh_duyet_cookie=mock.AsyncMock(return_value={"cookie": "a=b"}))
    kq = SimpleNamespace(ho_so_dang_nhap="ab12cd34")
    ok = asyncio.run(tuyen._noi_cookie_dang_nhap_vao_module(so, thuc_thi, "chu", kq))
    assert ok is True
    cai.assert_awaited_once_with("chu", {"cookies": {"them": ["a=b"]}})


def test_noi_cookie_khong_co_module_so_huu_trong_catalog_thi_bo_qua():
    so = SimpleNamespace(dang_chay={}, cai=mock.AsyncMock(), catalog={})
    thuc_thi = SimpleNamespace(trinh_duyet_cookie=mock.AsyncMock(return_value={"cookie": "a=b"}))
    ok = asyncio.run(tuyen._noi_cookie_dang_nhap_vao_module(so, thuc_thi, "khong-co", SimpleNamespace(ho_so_dang_nhap="ab12")))
    assert ok is False
    so.cai.assert_not_called()
