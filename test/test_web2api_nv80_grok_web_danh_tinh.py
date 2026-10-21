"""Test NV80 (cập nhật NV219) — grok-web KHÔNG còn gắn danh tính chung.

NV219 gỡ «danh tính» khỏi phần AI theo thiết kế chủ máy (00-thiet-ke-ai-chu-may-21-10.txt).
File test gốc kiểm tra khuôn NV77 (gắn danh_tinh_id, tham chiếu kho danh tính);
nay cập nhật theo bài học NV98: mã đổi hành vi có chủ ý → cập nhật test cũ.

Hành vi mới:
- Module nạp bình thường với cookie, không cần danh tính.
- Cấu hình có khoá «danh_tinh» (dữ liệu cũ) bị bỏ qua êm, không lỗi.
- Không còn thuộc tính/hàm/phương thức nào nhắc danh_tinh.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import asyncio

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_nv219_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module


def test_ma_nguon_khong_nhac_danh_tinh():
    """Mã nguồn module không còn chuỗi 'danh_tinh' (NV219)."""
    src = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert "danh_tinh" not in src


def test_khong_con_ham_danh_tinh_id_cua():
    """Không còn phương thức danh_tinh_id_cua (NV94-d đã gỡ)."""
    assert not hasattr(Module, "danh_tinh_id_cua")


def test_nap_cookie_thuong_khong_can_danh_tinh():
    """Nạp cookie bình thường, không cần danh tính — chạy y như trước."""
    m = Module()
    asyncio.run(m.nap({"tai_khoan": ["sso=gia-tri-sso-123"]}))
    assert len(m._tai_khoan) == 1


def test_cau_hinh_cu_co_danh_tinh_bo_qua_em():
    """Dữ liệu cũ có khoá «danh_tinh»: bỏ qua êm, không lỗi (NV219)."""
    m = Module()
    # Không vỡ khi cấu hình còn sót khoá danh_tinh từ bản cũ
    asyncio.run(m.nap({"tai_khoan": ["sso=gia-tri-sso-123"], "danh_tinh": ["dt-cu"]}))
    assert len(m._tai_khoan) == 1
