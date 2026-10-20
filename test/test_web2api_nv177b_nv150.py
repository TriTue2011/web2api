"""Test NV177B — vá theo dõi NV150 (giãn cách khi lần thử đã chạm kết nối)
+ đưa 2 test thăm dò của soát NV177 vào bộ test (khoá theo tiền tố, bộ đếm).

Chạy với bộ phát giả của test_web2api_nv150_zte_e2633.py (không mạng ngoài).
"""

from __future__ import annotations

import asyncio

import pytest

from test_web2api_nv150_zte_e2633 import (  # noqa: F401
    _cau_hinh,
    chay,
    cho_phep_loopback,
    dong_ho,
    manifest,
    may_chu_gia,
    mod,
)

_VAN_TAY_SAI = "0" * 64  # đúng khuôn 64 hex nhưng không khớp chứng chỉ máy giả


def _mod_van_tay_sai(mod, manifest, may_chu_gia, dong_ho):
    m = mod.Module(manifest, dong_ho=dong_ho)
    m.nap(_cau_hinh(may_chu_gia["dia_chi"], van_tay=_VAN_TAY_SAI))
    return m


# ── Vá theo dõi: mốc giãn cách khi đã chạm bước kết nối ──────────────────────

def test_van_tay_sai_lan_1_loi_van_tay_lan_2_bi_cho_thu_lai(
        mod, manifest, may_chu_gia, cho_phep_loopback, dong_ho):
    """Vân tay sai → lần 1 ZteLoiVanTay; lần 2 (ngay) PHẢI bị ZteDangChoThuLai
    vì lần thử đã chạm bước kết nối (đối chiếu vân tay tới thiết bị)."""
    m = _mod_van_tay_sai(mod, manifest, may_chu_gia, dong_ho)
    try:
        with pytest.raises(mod.ZteLoiVanTay):
            chay(m.thuc_the(None))
        with pytest.raises(mod.ZteDangChoThuLai):
            chay(m.thuc_the(None))
    finally:
        chay(m.dung())


def test_bo_dem_khong_tang_khi_chua_qua_kiem_van_tay(
        mod, manifest, may_chu_gia, cho_phep_loopback, dong_ho):
    """Thăm dò 1 của NV177: bộ đếm so_lan_dang_nhap chỉ tăng SAU bước kiểm
    vân tay — vân tay sai thì bộ đếm giữ 0 (đỏ trên bản pre-agy: không có
    thuộc tính này)."""
    m = _mod_van_tay_sai(mod, manifest, may_chu_gia, dong_ho)
    try:
        with pytest.raises(mod.ZteLoiVanTay):
            chay(m.thuc_the(None))
        assert m._khach.so_lan_dang_nhap == 0
    finally:
        chay(m.dung())


# ── Thăm dò 2 của NV177: khoá theo tiền tố ───────────────────────────────────

def test_khoa_tien_to_chung_cho_cung_tien_to(mod, manifest):
    """Hai thể hiện cùng tiền tố dùng CHUNG một khoá; khác tiền tố khác khoá."""
    m1 = mod.Module(manifest)
    m2 = mod.Module(manifest)
    assert m1._khoa is m2._khoa
    mh_khac = dict(manifest)
    mh_khac["tien_to"] = manifest["tien_to"] + "-khac"
    m3 = mod.Module(mh_khac)
    assert m3._khoa is not m1._khoa
    assert mod._lay_khoa_tien_to(manifest["tien_to"]) is m1._khoa


def test_khoa_tien_to_chan_dang_nhap_chong_cheo(
        mod, manifest, may_chu_gia, cho_phep_loopback, dong_ho):
    """Thể hiện 1 giữ khoá → suc_khoe() của thể hiện 2 PHẢI CHỜ (không chạy
    song song vào cùng bộ phát)."""
    m1 = mod.Module(manifest, dong_ho=dong_ho)
    m1.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    m2 = mod.Module(manifest, dong_ho=dong_ho)
    m2.nap(_cau_hinh(may_chu_gia["dia_chi"]))

    async def _chay():
        try:
            await m1._khoa.acquire()
            task = asyncio.create_task(m2.suc_khoe())
            await asyncio.sleep(0.3)
            assert not task.done(), "thể hiện 2 phải chờ khoá tiền tố"
            m1._khoa.release()
            kq = await asyncio.wait_for(task, timeout=10)
            assert kq["ok"] is True
        finally:
            await m1.dung()
            await m2.dung()

    asyncio.run(_chay())


def test_khoa_tien_to_khong_ket_vong_lap(
        mod, manifest, may_chu_gia, cho_phep_loopback, dong_ho):
    """Thăm dò 3 của NV177: khoá tầng tiến trình không kẹt khi chạy qua hai
    vòng lặp asyncio.run liên tiếp."""
    m1 = mod.Module(manifest, dong_ho=dong_ho)
    m1.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    m2 = mod.Module(manifest, dong_ho=dong_ho)
    m2.nap(_cau_hinh(may_chu_gia["dia_chi"]))
    try:
        assert asyncio.run(m1.suc_khoe())["ok"] is True
        assert asyncio.run(m2.suc_khoe())["ok"] is True
    finally:
        chay(m1.dung())
        chay(m2.dung())
