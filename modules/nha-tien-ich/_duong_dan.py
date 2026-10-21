"""Đường dẫn dữ liệu của module nha-tien-ich (port NV146 _duong_dan.py).

Mọi tệp trạng thái (kịch bản, cảnh báo, cảm biến ghép, nhật ký) nằm dưới kho
dữ liệu lõi ``WEB2API_DATA/modules/nha-tien-ich`` — thư mục 0700, tệp 0600.
"""
from __future__ import annotations

import os
from pathlib import Path


def goc() -> Path:
    g = os.environ.get("WEB2API_DATA")
    base = Path(g) if g else Path.home() / ".web2api"
    return base / "modules" / "nha-tien-ich"


def dam_bao() -> Path:
    d = goc()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def tep(ten: str) -> Path:
    return dam_bao() / ten
