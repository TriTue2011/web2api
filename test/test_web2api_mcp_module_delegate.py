"""Test lớp chuyển tiếp MỎNG ở cổng MCP: cổng chỉ gọi module.khoi_phuc_tu_dong.

Không kiểm logic tầng (logic đó ở trong module, đã test riêng từng module).
Module không có khoi_phuc_tu_dong -> delegate trả None (đường cũ fallback).
Không đăng nhập thật, không key MCP, không trình duyệt thật.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from app import cong_mcp_cong_cu
from app.khoi_phuc_phien import KetQuaKhoiPhuc


class SoGia:
    def __init__(self, mod):
        self.dang_chay = {"mod-gia": mod}


class ModGia:
    def __init__(self, kq):
        self.kq = kq
        self.da_goi = None

    async def khoi_phuc_tu_dong(self, khoa, email, nghi_t1_giay=0.0):
        self.da_goi = (khoa, email, nghi_t1_giay)
        return self.kq


def test_delegate_goi_module_va_dich_ket_qua(monkeypatch):
    monkeypatch.setattr(cong_mcp_cong_cu.kho_tai_khoan, "tim_theo_id",
                        lambda tk_id: {"email": "ban-gia@example.com"})
    mod = ModGia(KetQuaKhoiPhuc(True, tang="T2"))
    kq = asyncio.run(cong_mcp_cong_cu._thu_khoi_phuc_module(SoGia(mod), "tk-gia-1", "mod-gia"))
    assert kq == {"thanh_cong": True, "ma_loi": "", "can_nguoi": False, "huong_dan": ""}
    assert mod.da_goi[0] == "tk-gia-1" and mod.da_goi[1] == "ban-gia@example.com"


def test_delegate_tra_none_khi_module_khong_co_ham(monkeypatch):
    monkeypatch.setattr(cong_mcp_cong_cu.kho_tai_khoan, "tim_theo_id",
                        lambda tk_id: {"email": "ban-gia@example.com"})
    kq = asyncio.run(cong_mcp_cong_cu._thu_khoi_phuc_module(SoGia(types.SimpleNamespace()), "tk-gia-1", "mod-gia"))
    assert kq is None


class TdCoDong:
    async def gianh(self):
        return None

    async def nha(self):
        return None

    async def dong(self):
        self.da_dong = getattr(self, "da_dong", 0) + 1


class ModGiaCoController:
    def __init__(self, bi_huy=False):
        self.bi_huy = bi_huy

    async def khoi_phuc_tu_dong(self, khoa, email, nghi_t1_giay=0.0,
                                trinh_duyet=None):
        if self.bi_huy:
            raise asyncio.CancelledError()
        raise RuntimeError("loi-gia")


def test_delegate_dong_controller_va_workspace_khi_module_loi(monkeypatch):
    from app import khoi_phuc_phien as kp

    td = TdCoDong()
    mod = ModGiaCoController()
    monkeypatch.setattr(cong_mcp_cong_cu.kho_tai_khoan, "tim_theo_id",
                        lambda tk_id: {"email": "ban-gia@example.com"})
    async def lay(*args, **kw):
        return td, "ho-so-gia", True
    monkeypatch.setattr(kp, "lay_trinh_duyet_cho_tai_khoan", lay)
    da_dong_workspace = []
    async def dong_workspace(*args):
        da_dong_workspace.append(args[-1])
    monkeypatch.setattr(kp, "dong_workspace_moi", dong_workspace)

    with pytest.raises(RuntimeError):
        asyncio.run(cong_mcp_cong_cu._thu_khoi_phuc_module(
            SoGia(mod), "tk-gia-1", "mod-gia"))
    assert td.da_dong == 1
    assert da_dong_workspace == ["ho-so-gia"]


def test_delegate_dong_controller_va_workspace_khi_luot_bi_huy(monkeypatch):
    from app import khoi_phuc_phien as kp

    td = TdCoDong()
    mod = ModGiaCoController(bi_huy=True)
    monkeypatch.setattr(cong_mcp_cong_cu.kho_tai_khoan, "tim_theo_id",
                        lambda tk_id: {"email": "ban-gia@example.com"})
    async def lay(*args, **kw):
        return td, "ho-so-gia", True
    monkeypatch.setattr(kp, "lay_trinh_duyet_cho_tai_khoan", lay)
    da_dong_workspace = []
    async def dong_workspace(*args):
        da_dong_workspace.append(args[-1])
    monkeypatch.setattr(kp, "dong_workspace_moi", dong_workspace)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cong_mcp_cong_cu._thu_khoi_phuc_module(
            SoGia(mod), "tk-gia-1", "mod-gia"))
    assert td.da_dong == 1
    assert da_dong_workspace == ["ho-so-gia"]
