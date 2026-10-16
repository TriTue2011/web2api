"""NV19 — lấp lỗ hổng NV14 nhóm phien/thiet_bi (CHỈ THÊM TEST, không sửa mã).

9. app/phien.py bỏ kiểm ID_HOP_LE → tên module lạ bị từ chối khi liệt kê.
10. app/thiet_bi.py bỏ lọc RUI_RO_HOP_LE → mã rủi ro lạ bị loại (đóng khi lỗi).
"""

from __future__ import annotations

import json

import pytest

from app import kho, phien, thiet_bi


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-nv19-ptb-0123456789abcdef")
    return data_dir


def _ghi_phien(goc, ten_mod: str) -> None:
    d = goc / ten_mod
    d.mkdir(parents=True, exist_ok=True)
    (d / "tk1.json").write_text(json.dumps({
        "tai_khoan": "tk1", "du_lieu": {"a": 1},
        "het_han": None, "trang_thai": "con_song",
    }), encoding="utf-8")


def test_liet_ke_bo_qua_thu_muc_ten_module_la(moi_truong):
    """NV14#9: bỏ kiểm ID_HOP_LE → thư mục tên module lạ không được liệt kê."""
    goc = kho.thu_muc_du_lieu() / "bi_mat" / "phien"
    _ghi_phien(goc, "MOD LA")   # chữ hoa + khoảng trắng: không khớp ID_HOP_LE
    _ghi_phien(goc, "mod..x")   # dấu chấm: không khớp ID_HOP_LE
    _ghi_phien(goc, "modhop")   # đối chứng: tên hợp lệ
    ds = phien.liet_ke()
    ten_thay = [m["module"] for m in ds]
    assert "MOD LA" not in ten_thay, f"tên module lạ lọt vào liệt kê: {ten_thay}"
    assert "mod..x" not in ten_thay, f"tên module lạ lọt vào liệt kê: {ten_thay}"
    assert "modhop" in ten_thay, f"module hợp lệ biến mất khỏi liệt kê: {ten_thay}"


def test_doc_ghi_de_rui_ro_loc_ma_la(moi_truong):
    """NV14#10: bỏ lọc RUI_RO_HOP_LE → mã rủi ro lạ bị loại, chỉ giữ mã hợp lệ."""
    p = kho.thu_muc_du_lieu() / "thiet_bi.json"
    p.write_text(json.dumps({"ghi_de_rui_ro": {
        "thuc_the_1": "doc",
        "thuc_the_2": "SIEU_NGUY_HIEM",  # mã lạ: không trong {"doc", "ghi", "nguy_hiem"}
        "thuc_the_3": "ghi",
    }}), encoding="utf-8")
    assert thiet_bi.doc_ghi_de_rui_ro() == {"thuc_the_1": "doc", "thuc_the_3": "ghi"}
