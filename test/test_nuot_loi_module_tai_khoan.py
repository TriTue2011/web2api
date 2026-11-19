"""Regression test cho lỗi đọc kho khi chọn module thực thi."""

import pytest

from app import cong_mcp_cong_cu as cmcu


def test_module_thuc_thi_dong_khi_kho_tai_khoan_loi(monkeypatch):
    def nem_loi(_tk_id):
        raise OSError("loi kho khong duoc lo ra")

    monkeypatch.setattr(cmcu.kho_tai_khoan, "tim_theo_id", nem_loi)

    with pytest.raises(cmcu.LoiCongCu) as loi:
        cmcu._module_thuc_thi("tk-gia", {}, {"mod-tai-khoan": "mod-thuc-thi"})

    assert loi.value.ma == "kho_loi"
    assert loi.value.thong_diep == "Không đọc được kho tài khoản."
