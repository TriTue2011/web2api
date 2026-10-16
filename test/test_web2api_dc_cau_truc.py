"""Mọi trang web/*.dc.html phải có đúng một cặp <x-dc>…</x-dc> và một khối script dc-lite.

Lớp lỗi: trang viết tay thiếu thẻ đóng thì dc-lite không khởi động và hiện nguyên «{{…}}» cho người dùng;
test API không bắt được, chỉ test Chrome mới thấy. Kiểm tĩnh nên chạy cả khi máy không có Chrome.
"""
from __future__ import annotations

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
TRANG = sorted(WEB.glob("*.dc.html"))


def test_co_trang_de_kiem():
    assert TRANG, "không tìm thấy trang .dc.html nào"


@pytest.mark.parametrize("duong", TRANG, ids=lambda p: p.name)
def test_trang_co_cap_x_dc_day_du(duong: Path):
    ma = duong.read_text(encoding="utf-8")
    assert ma.count("<x-dc>") == 1, f"{duong.name}: phải có đúng một <x-dc>"
    assert ma.count("</x-dc>") == 1, f"{duong.name}: thiếu hoặc thừa </x-dc>"
    assert ma.index("<x-dc>") < ma.index("</x-dc>")
    assert ma.count('<script type="text/x-dc" data-dc-script') == 1, f"{duong.name}: phải có đúng một khối script dc-lite"
    assert ma.index("</x-dc>") < ma.index('<script type="text/x-dc" data-dc-script'), f"{duong.name}: script dc-lite phải nằm sau </x-dc>"
