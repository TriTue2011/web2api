"""Mẫu A cho test meta NV183: cố ý rò rỉ trạng thái toàn cục (không tự dọn).

Nếu fixture `on_dinh_trang_thai_toan_cuc` trong conftest hoạt động, rò rỉ này bị
khôi phục sau mỗi test và thứ tự chạy không ảnh hưởng kết quả.
"""
import logging
import os


def test_mau_a_ro_ri_env():
    os.environ["NV183_MAU_RI"] = "1"
    assert os.environ["NV183_MAU_RI"] == "1"


def test_mau_a_ro_ri_log_filter():
    root = logging.getLogger()
    truoc = len(root.filters)
    root.addFilter(lambda record: True)
    assert len(root.filters) == truoc + 1
