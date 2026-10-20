"""Mẫu B cho test meta NV183: khẳng định trạng thái toàn cục sạch.

Chạy sau mẫu A trong cùng tiến trình mà KHÔNG có fixture khôi phục → ĐỎ
(rò rỉ từ mẫu A). Có fixture → XANH ở mọi thứ tự.
"""
import logging
import os


def test_mau_b_env_sach():
    assert "NV183_MAU_RI" not in os.environ


def test_mau_b_log_filter_sach():
    # Fixture NV183 phải gỡ filter lambda mà mẫu A gắn vào root logger
    root = logging.getLogger()
    assert not any(
        getattr(f, "__name__", "") == "<lambda>" for f in root.filters
    ), "rò rỉ filter từ test khác"
