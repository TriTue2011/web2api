"""Hợp đồng module (đầu `app/kham_pha.py`): `suc_khoe()` và `dung()` là `async`.

Lõi `await` hai hàm này (`app/so_module.py`, `app/tuyen.py`). Module khai đồng bộ thì `GET /api/modules/<id>/suc-khoe` trả 500
và mỗi lần dừng ghi một traceback. Test duyệt MỌI module theo cấu trúc (không bảng tên module) nên module mới khai sai cũng bị bắt.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent / "modules"
HAM_ASYNC = ("suc_khoe", "dung")


def _module_py() -> list[pathlib.Path]:
    return sorted(GOC.glob("*/module.py"))


def test_co_module_de_duyet():
    assert _module_py(), "không tìm thấy modules/*/module.py"


@pytest.mark.parametrize("duong", _module_py(), ids=lambda p: p.parent.name)
def test_suc_khoe_va_dung_la_async(duong):
    cay = ast.parse(duong.read_text(encoding="utf-8"))
    sai = [
        f"{lop.name}.{ham.name} (dòng {ham.lineno})"
        for lop in ast.walk(cay) if isinstance(lop, ast.ClassDef) and lop.name == "Module"
        for ham in lop.body
        if isinstance(ham, ast.FunctionDef) and ham.name in HAM_ASYNC
    ]
    assert not sai, f"{duong.parent.name}: khai đồng bộ, hợp đồng là async: {sai}"
