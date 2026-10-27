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
