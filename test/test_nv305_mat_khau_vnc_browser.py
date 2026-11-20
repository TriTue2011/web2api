"""NV305: Chrome CDP → trang Bảo mật → API thật → module GIẢ, chỉ loopback.

Không mock Khung.goi/fetch hay step-up; dữ liệu và tài khoản đều tạm, mật khẩu giả.
"""

from __future__ import annotations

import asyncio

import pytest

from app import nguoi_dung, novnc, so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

MK_VNC_GIA = "Nv305!t1"
MK_USER_GIA = "MatKhauNguoiDungGiaNv305123"


class ModuleVncGia:
    def __init__(self):
        self.da_dat = False
        self.da_nap_lai = True
        self.loi = False
        self.loi_trang_thai = False
        self.nhan = []
        self.so_lan_trang_thai = 0

    async def trang_thai_mat_khau_vnc(self):
        self.so_lan_trang_thai += 1
        if self.loi_trang_thai:
            return {"ok": False, "loi": "Chi tiet gia khong duoc hien"}
        return {"ok": True, "da_dat": self.da_dat}

    async def dat_mat_khau_vnc(self, mat_khau):
        self.nhan.append(mat_khau)
        if self.loi:
            return {"ok": False, "loi": f"Khong dat duoc {mat_khau}"}
        self.da_dat = True
        return {"ok": True, "da_nap_lai": self.da_nap_lai}


@pytest.fixture
def loi_nv305(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-gia-nv305-browser-0123456789")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    monkeypatch.setattr(novnc, "_GIOI_HAN_MK_VNC", {})
    mod = ModuleVncGia()
    with MayChuThat(app, lifespan="on") as w:
        # ID tuỳ ý: test phải đi qua cơ chế chọn theo callable của lõi.
        app.state.so.dang_chay["man-hinh-gia-nv305"] = mod
        try:
            yield w, mod
        finally:
            app.state.so.dang_chay.pop("man-hinh-gia-nv305")


def _mo_bao_mat(trang, w):
    trang.di(f"{w.url}/He-thong-Bao-mat.dc.html", mo_muc=False)
    trang.cho("!!document.getElementById('o-mat-khau-vnc')", mo_ta="ô đặt mật khẩu VNC của quản trị")


def _xac_nhan_lai(trang, mat_khau):
    trang.cho("!!document.querySelector('dialog[open] input[type=password]')", mo_ta="hộp step-up thật")
    trang.go("dialog[open] input[type=password]", mat_khau)
    trang.bam("[...document.querySelectorAll('dialog[open] button')].find(b => b.textContent === 'Xác nhận')")
    trang.cho("!document.querySelector('dialog[open]')", mo_ta="đóng hộp xác nhận")


@pytest.mark.parametrize("da_nap_lai", [True, False], ids=["co-hieu-luc", "cho-bat-lai"])
def test_dat_qua_step_up_xoa_o_va_doc_lai_trang_thai(trang, loi_nv305, da_nap_lai):
    w, mod = loi_nv305
    mod.da_nap_lai = da_nap_lai
    _, mk_qt = dang_nhap_quan_tri(trang, w.url)
    _mo_bao_mat(trang, w)
    trang.cho("!!document.getElementById('vnc-chua-dat')", mo_ta="trạng thái chưa đặt từ API")
    assert mod.so_lan_trang_thai >= 1
    assert trang.chu("#nut-dat-mat-khau-vnc") == "Đặt"
    assert trang.js("document.getElementById('o-mat-khau-vnc').type") == "password"
    assert trang.js("document.querySelector('label[for=\"o-mat-khau-vnc\"]') !== null")
    trang.go("#o-mat-khau-vnc", MK_VNC_GIA)
    trang.bam("document.getElementById('nut-dat-mat-khau-vnc')")
    trang.cho("!!document.querySelector('dialog[open]')", mo_ta="PUT bị chặn tới khi step-up")
    assert mod.nhan == [], "chưa xác nhận lại thì module chưa nhận mật khẩu"
    _xac_nhan_lai(trang, mk_qt)
    trang.cho("!!document.getElementById('tin-mat-khau-vnc')", mo_ta="kết quả đặt từ API thật")
    assert mod.nhan == [MK_VNC_GIA], "step-up chỉ thử lại đúng một lần tới module"
    assert trang.js("document.getElementById('o-mat-khau-vnc').value") == ""
    assert all(MK_VNC_GIA not in s for s in trang.state_thanh_phan())
    assert MK_VNC_GIA not in trang.chu("#mat-khau-xem-trinh-duyet")
    tin = trang.chu("#tin-mat-khau-vnc")
    assert ("Người đang xem phải nhập lại" if da_nap_lai else "có hiệu lực khi bật lại") in tin
    assert trang.js("document.getElementById('tin-mat-khau-vnc').getAttribute('role')") == "status"
    assert trang.chu("#nut-dat-mat-khau-vnc") == "Đổi"

    so_lan = mod.so_lan_trang_thai
    _mo_bao_mat(trang, w)
    trang.cho("!!document.getElementById('vnc-da-dat')", mo_ta="GET phản ánh mật khẩu đã đặt sau khi tải lại")
    assert mod.so_lan_trang_thai > so_lan
    assert trang.js("document.getElementById('o-mat-khau-vnc').value") == ""
    assert trang.js("!document.getElementById('vnc-chua-dat')")
    trang.khung(390, 844, dien_thoai=True)
    assert trang.js("document.documentElement.scrollWidth <= window.innerWidth")


def test_loi_dat_khong_bao_thanh_cong_khong_lo_bi_mat_va_thu_lai_duoc(trang, loi_nv305):
    w, mod = loi_nv305
    mod.loi = True
    _, mk_qt = dang_nhap_quan_tri(trang, w.url)
    _mo_bao_mat(trang, w)
    trang.cho("!!document.getElementById('vnc-chua-dat')")
    trang.go("#o-mat-khau-vnc", MK_VNC_GIA)
    trang.bam("document.getElementById('nut-dat-mat-khau-vnc')")
    _xac_nhan_lai(trang, mk_qt)
    trang.cho("!!document.getElementById('loi-mat-khau-vnc')", mo_ta="lỗi module qua API thật")
    assert trang.chu("#loi-mat-khau-vnc") == "Dịch vụ không đặt được mật khẩu xem trình duyệt"
    assert MK_VNC_GIA not in trang.chu("#mat-khau-xem-trinh-duyet")
    assert trang.js("!document.getElementById('tin-mat-khau-vnc')")
    assert not trang.js("document.getElementById('nut-dat-mat-khau-vnc').disabled")
    assert not mod.da_dat
    mod.loi = False
    trang.bam("document.getElementById('nut-dat-mat-khau-vnc')")
    trang.cho("!!document.getElementById('tin-mat-khau-vnc')", mo_ta="thử lại thành công")
    assert mod.nhan == [MK_VNC_GIA, MK_VNC_GIA]
    assert trang.js("document.getElementById('o-mat-khau-vnc').value") == ""
    assert trang.js("!document.getElementById('loi-mat-khau-vnc')")


def test_loi_trang_thai_khong_hien_nham_chua_dat(trang, loi_nv305):
    w, mod = loi_nv305
    mod.loi_trang_thai = True
    dang_nhap_quan_tri(trang, w.url)
    _mo_bao_mat(trang, w)
    trang.cho("!!document.getElementById('loi-trang-thai-vnc')", mo_ta="báo không đọc được trạng thái")
    assert "Chưa kiểm tra được" in trang.chu("#loi-trang-thai-vnc")
    assert trang.js("!document.getElementById('vnc-chua-dat') && !document.getElementById('vnc-da-dat')")
    assert "Chi tiet gia" not in trang.chu("#mat-khau-xem-trinh-duyet")


def test_user_khong_thay_o_va_khong_doc_trang_thai(trang, loi_nv305):
    w, mod = loi_nv305
    asyncio.run(nguoi_dung.tao_nguoi_dung(
        vai="admin", ten_dang_nhap="quan-tri-gia-nv305", mat_khau=MK_USER_GIA))
    asyncio.run(nguoi_dung.tao_nguoi_dung(
        vai="user", ten_dang_nhap="user-gia-nv305", mat_khau=MK_USER_GIA))
    trang.di(f"{w.url}/Dang-nhap.dc.html")
    trang.go("#o-ten", "user-gia-nv305")
    trang.go("#o-mat-khau", MK_USER_GIA)
    trang.bam("document.getElementById('nut-dang-nhap')")
    trang.cho("!/(Dang-nhap|index)/.test(location.pathname)")
    trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung#mat-khau-xem-trinh-duyet", mo_muc=False)
    trang.cho("!!document.querySelector('[data-mo-muc=\"doi-mat-khau\"]')", mo_ta="trang Bảo mật của user dựng xong")
    assert trang.js("!document.getElementById('mat-khau-xem-trinh-duyet') && !document.getElementById('o-mat-khau-vnc')")
    assert mod.so_lan_trang_thai == 0 and mod.nhan == []
