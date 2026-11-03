# test_nv300_vong_doi_trinh_duyet.py — NV300: vòng đời trình duyệt của dịch vụ (đóng, trần đồng thời, không mồ côi).
# Chủ máy 03/11/2026: «sao không đóng trình duyệt, xử lý vụ này không tràn RAM hoặc leak».
# Bộ điều khiển GIẢ có đếm mở/đóng — không trình duyệt thật.
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gia_goi  # noqa: F401

from src import browser_pool as bp
from src.browser_pool import BrowserPool, TranNguCanh


class _DemMoDong:
    def __init__(self):
        self.dang_mo = 0
        self.tong_mo = 0
        self.tong_dong = 0


class _CtxGia:
    def __init__(self, dem):
        self._dem = dem
        self.pages = []
        self.dong = False
        dem.dang_mo += 1
        dem.tong_mo += 1

    async def close(self):
        if not self.dong:
            self.dong = True
            self._dem.dang_mo -= 1
            self._dem.tong_dong += 1

    async def cookies(self):
        return []

    def on(self, *a, **k):
        pass


@pytest.fixture
def bo_dem():
    return _DemMoDong()


@pytest.fixture
def pool(monkeypatch, bo_dem):
    p = BrowserPool()

    async def khong_lam_gi():
        return None

    async def mo_gia(profile, headless):
        return _CtxGia(bo_dem), None

    monkeypatch.setattr(p, "start", khong_lam_gi)
    monkeypatch.setattr(p, "_open_context", mo_gia)
    monkeypatch.setattr(bp, "CHO_CHO_TRONG_GIAY", 0.2)
    return p


def _chay(coro):
    return asyncio.run(coro)


def test_vuot_tran_thi_bao_ban_khong_mo_them(pool, bo_dem):
    async def kich_ban():
        for ten in ("hs-a", "hs-b"):
            await pool.get(profile=ten, headless=False)
            pool.dau_dang_nhap(ten)          # cả hai đang đăng nhập → không nhả được
        assert bo_dem.dang_mo == bp.TRAN_NGU_CANH_DONG_THOI == 2
        with pytest.raises(TranNguCanh):
            await pool.get(profile="hs-c", headless=False)
        assert bo_dem.dang_mo == 2 and bo_dem.tong_mo == 2      # không mở thêm
    _chay(kich_ban())


def test_vuot_tran_nhuong_cho_bang_nha_trinh_duyet_nhan_roi(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)       # nhàn rỗi, không đăng nhập
        await pool.get(profile="hs-b", headless=False)
        pool.dau_dang_nhap("hs-b")
        await pool.get(profile="hs-c", headless=False)       # nhả hs-a (nhàn rỗi) lấy chỗ
        assert not pool.is_loaded("hs-a") and pool.is_loaded("hs-b") and pool.is_loaded("hs-c")
        assert bo_dem.dang_mo == 2
    _chay(kich_ban())


def test_cho_nguoi_qua_han_khong_hoat_dong_thi_dong_ve_0(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)
        pool.dau_dang_nhap("hs-a")                           # như lượt đăng nhập chờ người (need_manual)
        bay_gio = time.time()
        # Trong hạn: giữ nguyên (không cắt giữa chừng lượt người đang xử lý).
        assert await pool._quet_nhan_roi(bay_gio + bp.HAN_KHONG_HOAT_DONG_GIAY - 5) == []
        assert bo_dem.dang_mo == 1
        # Quá hạn: đóng, bỏ cờ.
        assert await pool._quet_nhan_roi(bay_gio + bp.HAN_KHONG_HOAT_DONG_GIAY + 5) == ["hs-a"]
        assert bo_dem.dang_mo == 0 and not pool.dang_dang_nhap("hs-a")
    _chay(kich_ban())


def test_dang_co_nguoi_xem_thi_keo_dai_hang_cho(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)
        pool.dau_dang_nhap("hs-a")
        bay_gio = time.time()
        pool._contexts["hs-a"].last_used = bay_gio - bp.HAN_KHONG_HOAT_DONG_GIAY - 100   # lâu rồi không dùng
        pool.ghi_nhan_xem()                                  # nhưng chủ máy vừa xem màn hình
        assert await pool._quet_nhan_roi(bay_gio + 5) == []
        assert bo_dem.dang_mo == 1
        # Thôi xem quá hạn → đóng.
        assert await pool._quet_nhan_roi(bay_gio + bp.HAN_KHONG_HOAT_DONG_GIAY + 5) == ["hs-a"]
        assert bo_dem.dang_mo == 0
    _chay(kich_ban())


def test_mo_tay_giu_lau_hon_nhung_van_co_han(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-t", headless=False, manual=True)
        bay_gio = time.time()
        assert await pool._quet_nhan_roi(bay_gio + bp.HAN_KHONG_HOAT_DONG_GIAY + 5) == []
        assert await pool._quet_nhan_roi(bay_gio + bp.HAN_KHONG_HOAT_DONG_TAY_GIAY + 5) == ["hs-t"]
        assert bo_dem.dang_mo == 0
    _chay(kich_ban())


def test_nhan_roi_khong_dang_nhap_dong_sau_300_giay(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)
        await pool._lock_for("hs-a")
        bay_gio = time.time()
        assert await pool._quet_nhan_roi(bay_gio + 100) == []
        assert await pool._quet_nhan_roi(bay_gio + 400) == ["hs-a"]
        assert bo_dem.dang_mo == 0
    _chay(kich_ban())


def test_dich_vu_dung_thi_dong_het_khong_con_tien_trinh(pool, bo_dem):
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)
        await pool.get(profile="hs-b", headless=False, manual=True)
        pool.dau_dang_nhap("hs-a")
        assert bo_dem.dang_mo == 2
        await pool.stop()
        assert bo_dem.dang_mo == 0 and bo_dem.tong_dong == bo_dem.tong_mo
    _chay(kich_ban())


def test_huy_luot_dang_nhap_thi_dong_trinh_duyet(pool, bo_dem):
    """Huỷ (close_profile) đóng đúng hồ sơ, giải phóng chỗ cho yêu cầu kế."""
    async def kich_ban():
        await pool.get(profile="hs-a", headless=False)
        await pool.get(profile="hs-b", headless=False)
        assert await pool.close_profile("hs-a") is True
        assert bo_dem.dang_mo == 1
        await pool.get(profile="hs-c", headless=False)       # có chỗ ngay, không phải chờ
        assert bo_dem.dang_mo == 2
    _chay(kich_ban())


def test_hai_hoso_mo_cung_luc_khong_cung_lot_qua_tran(pool, bo_dem):
    async def kich_ban():
        kq = await asyncio.gather(*(pool.get(profile=f"hs-{i}", headless=False) for i in range(4)),
                                  return_exceptions=True)
        assert bo_dem.dang_mo <= bp.TRAN_NGU_CANH_DONG_THOI
        assert pool.so_ngu_canh_dang_mo() <= bp.TRAN_NGU_CANH_DONG_THOI
        assert sum(1 for k in kq if not isinstance(k, Exception)) == 4 \
            or any(isinstance(k, TranNguCanh) for k in kq)
    _chay(kich_ban())
