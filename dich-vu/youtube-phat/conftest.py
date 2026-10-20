"""Cấu hình test đợt A NV153 — chặn mạng, yt-dlp giả, fixture app + máy chủ giả.

Chạy: /home/hatch/workspace/.venv-web2api/bin/python -m pytest dich-vu/youtube-phat/ -x -q
(từ gốc kho web2api-nv153).

KHÔNG gọi Internet: fixture session ``chan_mang`` vá ``socket.socket.connect`` —
mọi kết nối ra ngoài 127.0.0.1/::1 đều lỗi ngay bằng OSError. Máy chủ giả
(googlevideo) và TestClient đều chạy trên loopback nên vẫn đi được.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent  # dich-vu/youtube-phat
HO_TRO_TEST = GOC / "ho_tro_test"

# Import trực tiếp các module dịch vụ (đã vá import tương đối trong mã nguồn).
sys.path.insert(0, str(GOC))

# Tiến trình con ``python -m yt_dlp`` (search_youtube chạy qua subprocess) tìm
# thấy gói yt_dlp GIẢ qua PYTHONPATH. KHÔNG thêm vào sys.path của tiến trình
# test để ``import yt_dlp`` trong tiến trình này vẫn hỏng như venv thật.
_pp = os.environ.get("PYTHONPATH", "")
if str(HO_TRO_TEST) not in _pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = str(HO_TRO_TEST) + (os.pathsep + _pp if _pp else "")


def _tai_module(ten: str, duong: Path):
    spec = importlib.util.spec_from_file_location(ten, duong)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[ten] = module
    spec.loader.exec_module(module)
    return module


googlevideo_gia = _tai_module("googlevideo_gia", HO_TRO_TEST / "googlevideo_gia.py")

_MAY_CHU_CHO_PHEP = {"127.0.0.1", "::1", "localhost"}
_connect_goc = socket.socket.connect


def _connect_chan(self, dia_chi):
    may = dia_chi[0] if isinstance(dia_chi, (tuple, list)) else str(dia_chi)
    if str(may) in _MAY_CHU_CHO_PHEP:
        return _connect_goc(self, dia_chi)
    raise OSError(f"test chan mang: cam ket noi ra {may!r}")


@pytest.fixture(scope="session", autouse=True)
def chan_mang():
    """Chặn MỌI kết nối mạng ra ngoài localhost trong toàn bộ phiên test."""
    socket.socket.connect = _connect_chan
    yield
    socket.socket.connect = _connect_goc


@pytest.fixture()
def ung_dung(tmp_path, monkeypatch):
    """(app, token, thu_muc_du_lieu): app FastAPI mới tinh trên thư mục tạm."""
    import dich_vu
    import server

    dich_vu._dat_lai_cho_test()  # xoá cache core ở mức module — mỗi test một core
    monkeypatch.delenv("YT_PUBLISH_IP", raising=False)
    thu_muc = str(tmp_path / "du_lieu")
    app = server.tao_ung_dung(thu_muc)
    token = app.state.core.integration_token
    yield app, token, thu_muc
    dich_vu._dat_lai_cho_test()


@pytest.fixture()
def may_chu_gia():
    """Factory ``(kich_ban, du_lieu) -> MayChuGoogleVideoGia`` đã bật; tự tắt sau test."""

    cac_may_chu = []

    def _tao(kich_ban, du_lieu, **kwargs):
        may_chu = googlevideo_gia.MayChuGoogleVideoGia(kich_ban, du_lieu, **kwargs)
        may_chu.bat_dau()
        cac_may_chu.append(may_chu)
        return may_chu

    yield _tao
    for may_chu in cac_may_chu:
        may_chu.dung()


def tieu_de_xac_thuc(token: str) -> dict:
    """Header Authorization Bearer cho các route integration."""
    return {"Authorization": f"Bearer {token}"}
