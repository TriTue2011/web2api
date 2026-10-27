"""P1 — cổng MCP đăng nhập qua thang khôi phục T0–T3 (không phải một phát duy nhất).

Chỉ dùng module giả và kho tài khoản giả (monkeypatch): không đăng nhập
thật, không chạm nhà cung cấp, không dùng key MCP. Các ca chứng minh:
- module có tầng khôi phục → MCP đi qua c2a, T2 thành công → thanh_cong;
- module trả trinh_duyet_none → that_bai với đúng mã đó, không chìm thành can_nguoi;
- module chỉ có dang_nhap (không có tầng khôi phục) → MCP giữ đường cũ (trả False);
- tài khoản tạm dừng / không có / đăng nhập qua tài khoản lưu khác → xử lý đúng, không gọi module.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import kho_tai_khoan as ktk
from app import khoi_phuc_phien as kp
from app import cong_mcp_cong_cu as cmcu
from app.khoi_phuc_phien import (
    KetQuaKhoiPhuc,
    TaiKhoanCanKhoiPhuc,
    khoi_phuc_theo_tang_c2a,
)
from app.tai_khoan_hang_loat import BiMatTam


@pytest.fixture(autouse=True)
def sach(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(kp, "T_DEBOUNCE_GIAY", 0.0)
    monkeypatch.setattr(kp, "CAPTCHA_HOAN_GIAY", 0.0)
    kp._LAN_KHOI_PHUC_THEO_KHOA.clear()
    kp.dat_lai_trang_thai_t2()
    yield
    kp._LAN_KHOI_PHUC_THEO_KHOA.clear()
    kp.dat_lai_trang_thai_t2()


class ModKhoiPhucGia:
    manifest = {"id": "mod-khoi-phuc-gia"}
    tien_to = "mod-khoi-phuc-gia"

    def __init__(self, ket_qua_b=None):
        self.ket_qua_b = ket_qua_b
        self.da_goi_b = 0
        self.da_goi_lam_moi = 0

    async def lam_moi_phien(self, khoa):
        self.da_goi_lam_moi += 1
        return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han")

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.da_goi_b += 1
        return self.ket_qua_b if self.ket_qua_b is not None else KetQuaKhoiPhuc(True, tang="b")


class ModChiDangNhap:
    manifest = {"id": "mod-chi-dang-nhap"}
    tien_to = "mod-chi-dang-nhap"
    da_goi = 0

    async def dang_nhap(self, dong):
        self.da_goi += 1
        return SimpleNamespace(thanh_cong=True, can_nguoi=False, cho_2fa=False,
                               ma_loi="", huong_dan="")


def _phien(tk_id):
    return {"phien_id": "p1", "tai_khoan_id": tk_id, "trang_thai": "dang_chay",
            "ma_loi": "", "huong_dan": "", "bat_dau_luc": "2026-10-27T00:00:00Z",
            "ket_thuc_luc": None}


def _dat_kho_gia(monkeypatch, ban_ghi, mat_khau="mat-khau-gia"):
    monkeypatch.setattr(cmcu.kho_tai_khoan, "tim_theo_id", lambda _id: ban_ghi)
    monkeypatch.setattr(cmcu.kho_tai_khoan, "lay_bi_mat_dang_nhap",
                        lambda _id: (BiMatTam(mat_khau), None))


def _chay(phien, so, tk_id, mod_id):
    return asyncio.run(cmcu._thu_khoi_phuc_tang(phien, so, tk_id, mod_id))


def test_mcp_qua_c2a_t2_thanh_cong(monkeypatch):
    mod = ModKhoiPhucGia()
    so = SimpleNamespace(dang_chay={"mod-khoi-phuc-gia": mod})
    tk_id = "tk-mcp-c2a"
    _dat_kho_gia(monkeypatch, {"email": "a@example.com", "trang_thai": "", "dang_nhap_bang": ""})
    phien = _phien(tk_id)
    assert _chay(phien, so, tk_id, "mod-khoi-phuc-gia") is True
    assert mod.da_goi_b == 1, "phải đi qua T2, không phải một phát dang_nhap"
    assert phien["trang_thai"] == "thanh_cong"
    assert phien["ma_loi"] == ""


def test_trinh_duyet_none_la_ma_rieng_khong_phai_can_nguoi(monkeypatch):
    mod = ModKhoiPhucGia(KetQuaKhoiPhuc(False, tang="b", ma_loi="trinh_duyet_none"))
    so = SimpleNamespace(dang_chay={"mod-khoi-phuc-gia": mod})
    tk_id = "tk-mcp-none"
    _dat_kho_gia(monkeypatch, {"email": "a@example.com", "trang_thai": "", "dang_nhap_bang": ""})
    phien = _phien(tk_id)
    assert _chay(phien, so, tk_id, "mod-khoi-phuc-gia") is True
    assert phien["trang_thai"] == "that_bai"
    assert phien["ma_loi"] == "trinh_duyet_none"


def test_thu_thach_truoc_khi_dien_van_can_nguoi(monkeypatch):
    mod = ModKhoiPhucGia(KetQuaKhoiPhuc(False, tang="b", ma_loi="thu_thach_truoc_khi_dien",
                                        can_nguoi=True))
    so = SimpleNamespace(dang_chay={"mod-khoi-phuc-gia": mod})
    tk_id = "tk-mcp-can-nguoi"
    _dat_kho_gia(monkeypatch, {"email": "a@example.com", "trang_thai": "", "dang_nhap_bang": ""})
    phien = _phien(tk_id)
    assert _chay(phien, so, tk_id, "mod-khoi-phuc-gia") is True
    assert phien["trang_thai"] == "can_nguoi"


def test_module_khong_co_tang_khoi_phuc_giu_duong_cu(monkeypatch):
    mod = ModChiDangNhap()
    so = SimpleNamespace(dang_chay={"mod-chi-dang-nhap": mod})
    tk_id = "tk-mcp-cu"
    _dat_kho_gia(monkeypatch, {"email": "a@example.com", "trang_thai": "", "dang_nhap_bang": ""})
    phien = _phien(tk_id)
    assert _chay(phien, so, tk_id, "mod-chi-dang-nhap") is False
    assert phien["trang_thai"] == "dang_chay"
    assert mod.da_goi == 0, "đường c2a không được gọi module chưa có tầng khôi phục"


def test_tam_dung_va_khong_tim_thay(monkeypatch):
    mod = ModKhoiPhucGia()
    so = SimpleNamespace(dang_chay={"mod-khoi-phuc-gia": mod})
    _dat_kho_gia(monkeypatch, {"email": "a@example.com",
                              "trang_thai": ktk.TRANG_THAI_TAM_DUNG, "dang_nhap_bang": ""})
    phien = _phien("tk-tam-dung")
    assert _chay(phien, so, "tk-tam-dung", "mod-khoi-phuc-gia") is True
    assert phien["trang_thai"] == "that_bai" and phien["ma_loi"] == "tam_dung"
    assert mod.da_goi_b == 0

    monkeypatch.setattr(cmcu.kho_tai_khoan, "tim_theo_id", lambda _id: None)
    phien2 = _phien("tk-khong-co")
    assert _chay(phien2, so, "tk-khong-co", "mod-khoi-phuc-gia") is True
    assert phien2["trang_thai"] == "that_bai" and phien2["ma_loi"] == "khong_tim_thay"


def test_dang_nhap_qua_tai_khoan_luu_khac_giu_duong_cu(monkeypatch):
    mod = ModKhoiPhucGia()
    so = SimpleNamespace(dang_chay={"mod-khoi-phuc-gia": mod})
    _dat_kho_gia(monkeypatch, {"email": "a@example.com", "trang_thai": "",
                              "dang_nhap_bang": ktk.DANG_NHAP_BANG_QUA_TAI_KHOAN_LUU})
    phien = _phien("tk-ho")
    assert _chay(phien, so, "tk-ho", "mod-khoi-phuc-gia") is False
    assert mod.da_goi_b == 0


def test_c2a_bo_t3_khi_khoa_khong_phai_khoa_noi_bo():
    """T2 thành công nhưng lam_moi(khoa lạ) không xác nhận được: mặc định
    (xac_nhan=True) → can_nguoi cuối đường; MCP (False) → thanh_cong ở T2."""
    mod = ModKhoiPhucGia()
    tk = TaiKhoanCanKhoiPhuc(khoa="tk-la", email="a@example.com", mat_khau=None)
    kq_mac_dinh = asyncio.run(khoi_phuc_theo_tang_c2a(mod, tk, nghi_t1_giay=0.0))
    assert not kq_mac_dinh.thanh_cong and kq_mac_dinh.can_nguoi

    kp._LAN_KHOI_PHUC_THEO_KHOA.clear()
    kp.dat_lai_trang_thai_t2()
    mod2 = ModKhoiPhucGia()
    kq_mcp = asyncio.run(khoi_phuc_theo_tang_c2a(
        mod2, tk, nghi_t1_giay=0.0, xac_nhan_lai_sau_dang_nhap=False))
    assert kq_mcp.thanh_cong and kq_mcp.tang == "T2"
    assert mod2.da_goi_b == 1
