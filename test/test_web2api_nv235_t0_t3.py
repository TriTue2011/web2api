# NV235-b5 — thang khôi phục T0–T3 theo đúng c2a trên khung khoi_phuc_phien.py.
#
# T0 kiểm phiên rẻ → T1 nghỉ+kiểm lại → T2 đăng nhập lại → T3 kiểm lại;
# debounce 1800s; 'ban'/'chua_ro' hoãn im lặng; need_manual → cần người + noVNC.

from __future__ import annotations

import asyncio

import pytest

from app import khoi_phuc_phien as kp
from app.khoi_phuc_phien import (
    TaiKhoanCanKhoiPhuc, KetQuaKhoiPhuc,
    khoi_phuc_theo_tang_c2a,
)


def chay(coro):
    return asyncio.run(coro)


class ModuleGiaT0T3:
    def __init__(self, manifest=None):
        self.manifest = manifest or {"id": "mod-gia-nv235", "tien_to": "modgianv235"}
        self.kich_ban_b = None
        self.da_goi_b = 0

    async def dang_nhap_tu_dong(self, tai_khoan, trinh_duyet):
        self.da_goi_b += 1
        if isinstance(self.kich_ban_b, Exception):
            raise self.kich_ban_b
        return self.kich_ban_b if self.kich_ban_b is not None \
            else KetQuaKhoiPhuc(True, tang="b")


@pytest.fixture(autouse=True)
def nhanh(monkeypatch):
    monkeypatch.setattr(kp, "T1_NGHI_GIAY", 0.0)
    monkeypatch.setattr(kp, "T_DEBOUNCE_GIAY", 0.0)
    kp._LAN_KHOI_PHUC_THEO_KHOA.clear()


def _tk():
    return TaiKhoanCanKhoiPhuc(khoa="tk-mot", email="a@example.com",
                               mat_khau=None)


def test_t0_phien_con_song_xong_ngay():
    mod = ModuleGiaT0T3()

    async def kiem_phien():
        return "ok"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert kq.thanh_cong and kq.tang == "T0"
    assert mod.da_goi_b == 0, "T0 ok thì không được đăng nhập lại"


def test_t1_nghi_roi_phien_len_lai():
    mod = ModuleGiaT0T3()
    lan = {"n": 0}

    async def kiem_phien():
        lan["n"] += 1
        return "mat" if lan["n"] == 1 else "ok"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert kq.thanh_cong and kq.tang == "T1"
    assert mod.da_goi_b == 0, "T1 xong thì không cần T2"


def test_t2_t3_dang_nhap_roi_kiem_lai():
    mod = ModuleGiaT0T3()
    lan = {"n": 0}

    async def kiem_phien():
        lan["n"] += 1
        return "ok" if lan["n"] >= 4 else "mat"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert kq.thanh_cong and kq.tang == "T3"
    assert mod.da_goi_b == 1


def test_t0_ban_hoan_im_lang():
    """T0 gặp 'ban' → hoãn NGAY (đúng 1 lần kiểm), không vào T1/T2."""
    mod = ModuleGiaT0T3()
    dem = {"n": 0}

    async def kiem_phien():
        dem["n"] += 1
        return "ban"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert not kq.thanh_cong and kq.ma_loi == "hoan_phien_ban"
    assert mod.da_goi_b == 0
    assert dem["n"] == 1, "T0 ban phải hoãn ngay, không kiểm lại"


def test_need_manual_can_nguoi_khong_thu_lai_vong():
    """Grok need_manual (Cloudflare) → «cần người» + hướng dẫn, không T3."""
    mod = ModuleGiaT0T3()
    mod.kich_ban_b = KetQuaKhoiPhuc(False, tang="b", ma_loi="need_manual",
                                    can_nguoi=True,
                                    huong_dan="Cloudflare chặn — xử lý tay.")

    async def kiem_phien():
        return "mat"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert not kq.thanh_cong and kq.can_nguoi
    assert mod.da_goi_b == 1


def test_debounce_1800s_bo_luot(monkeypatch):
    monkeypatch.setattr(kp, "T_DEBOUNCE_GIAY", 1800.0)
    mod = ModuleGiaT0T3()
    kp._LAN_KHOI_PHUC_THEO_KHOA["mod-gia-nv235:tk-mot"] = \
        __import__("time").time()

    async def kiem_phien():
        return "ok"

    kq = chay(khoi_phuc_theo_tang_c2a(mod, _tk(), kiem_phien=kiem_phien))
    assert not kq.thanh_cong and kq.ma_loi == "dang_trong_debounce"
