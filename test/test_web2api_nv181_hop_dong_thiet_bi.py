"""NV181 — module khai thực thể phải NỐI ĐƯỢC vào lõi (hợp đồng C1 kiểm bằng CẤU TRÚC, không bằng tên module).

Lớp lỗi (đo thật 20/10/2026 qua lõi thật): lõi (`app/thiet_bi.py::_module_thiet_bi_hop_le`) chỉ nạp thực thể của module khi
  (1) manifest `cung_cap` có «thiet_bi», và (2) module có ĐỦ ba hàm `thuc_the`, `trang_thai`, `thuc_hien`;
rồi gọi `await asyncio.wait_for(m.thuc_the(None), ...)`. Module nào khai thực thể nhưng
  - `thuc_the` là hàm ĐỒNG BỘ → lõi báo «object list can't be used in 'await' expression» và bỏ qua,
  - thiếu `trang_thai`, hoặc `cung_cap` không có «thiet_bi» → lõi lặng lẽ không thấy gì,
nên công cụ/giao diện của module không bao giờ dùng được (404 ở /api/thiet-bi/{id}/hanh-dong), mà test gọi thẳng hàm module vẫn xanh.
Đã dính: van-phong (có sẵn từ NV138), youtube-phat, chitieu, hoa-don, rclone, nhaptaikhoan.

Test này đọc cây AST của `module.py` (không chạy mã) và áp cho MỌI module có định nghĩa `thuc_the`: không danh sách tên module.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
MODULES = sorted(p for p in (GOC / "modules").iterdir() if (p / "manifest.json").is_file() and (p / "module.py").is_file())


def _ham(cay: ast.AST, ten: str):
    """Mọi định nghĩa hàm/phương thức tên `ten` trong tệp (trả list các nút Function/AsyncFunction)."""
    return [n for n in ast.walk(cay) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == ten]


@pytest.mark.parametrize("thu_muc", MODULES, ids=lambda p: p.name)
def test_module_khai_thuc_the_thi_noi_duoc_vao_loi(thu_muc):
    cay = ast.parse((thu_muc / "module.py").read_text(encoding="utf-8"))
    thuc_the = _ham(cay, "thuc_the")
    if not thuc_the:
        pytest.skip("module không khai thực thể (không dùng chuẩn thiet_bi)")
    manifest = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
    loi = []
    if "thiet_bi" not in (manifest.get("cung_cap") or []):
        loi.append(f"manifest `cung_cap` thiếu «thiet_bi» (đang là {manifest.get('cung_cap')})")
    for ten in ("thuc_the", "trang_thai", "thuc_hien"):
        ds = _ham(cay, ten)
        if not ds:
            loi.append(f"thiếu hàm `{ten}`")
        elif not all(isinstance(n, ast.AsyncFunctionDef) for n in ds):
            loi.append(f"`{ten}` phải là `async def` (lõi gọi `await`)")
    assert not loi, f"module «{thu_muc.name}» khai thực thể nhưng lõi KHÔNG nạp được: " + "; ".join(loi)
