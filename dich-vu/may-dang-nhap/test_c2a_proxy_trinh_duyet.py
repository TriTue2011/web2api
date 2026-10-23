# test_c2a_proxy_trinh_duyet.py — proxy cấu hình cho trình duyệt PHẢI tới được
# MỌI nhánh launch trong browser_pool._open_context.
#
# Cớ: đo thật 23/10/2026 trên môi trường có proxy egress — nhánh CloakBrowser
# truyền `proxy` từ đầu, nhưng nhánh Firefox (playwright gốc) và nhánh
# Patchright-Chromium đều KHÔNG truyền, nên khi CAPTCHA_SOLVER_BROWSER_PROXY
# được đặt, hai nhánh đó mở trình duyệt không proxy: trong môi trường bắt buộc
# đi qua proxy thì mọi `goto` chết (SSL_ERROR_UNKNOWN / không tải được trang),
# còn trên máy không proxy thì lỗi ẩn hoàn toàn. Lượt onboard ChatGPT gốc chỉ
# chạy được sau khi truyền proxy cho Firefox.
#
# Test này không mở trình duyệt thật: thay runtime bằng bản giả ghi lại
# kwargs của launch_persistent_context, rồi khẳng định đường truyền proxy
# của từng nhánh.
from __future__ import annotations

import asyncio
import os
import sys

import pytest

try:
    import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả khi chạy pytest trên máy)
except ImportError:
    _APP = "/app"
    if os.path.isdir(os.path.join(_APP, "src")) and _APP not in sys.path:
        sys.path.insert(0, _APP)

import src.browser_pool as bp  # noqa: E402
from src.browser_pool import BrowserPool  # noqa: E402

_PROXY_TEST = "http://127.0.0.1:9999"


class _TrangGia:
    pass


class _ContextGia:
    def __init__(self) -> None:
        self.pages = [_TrangGia()]

    async def add_init_script(self, *args, **kwargs) -> None:
        pass

    def on(self, *args, **kwargs) -> None:
        pass


class _TrinhDuyetGia:
    """Ghi lại kwargs của launch_persistent_context thay vì mở trình duyệt."""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def launch_persistent_context(self, **kwargs):
        self.kwargs = kwargs
        return _ContextGia()


class _PlaywrightGia:
    def __init__(self) -> None:
        self.firefox = _TrinhDuyetGia()
        self.chromium = _TrinhDuyetGia()


def _pool_gia() -> tuple[BrowserPool, _PlaywrightGia, _PlaywrightGia]:
    pool = BrowserPool()
    goc = _PlaywrightGia()          # runtime playwright gốc (nhánh Firefox)
    thuong = _PlaywrightGia()       # runtime patchright (nhánh Chromium)
    pool._playwright_goc = goc
    pool._playwright = thuong
    return pool, goc, thuong


def _mo_context(engine: str, co_proxy: bool, monkeypatch) -> dict:
    monkeypatch.setattr(bp.settings, "browser", engine)
    monkeypatch.setattr(bp, "_CLOAK_AVAILABLE", False)
    if co_proxy:
        monkeypatch.setenv("CAPTCHA_SOLVER_BROWSER_PROXY", _PROXY_TEST)
    else:
        monkeypatch.delenv("CAPTCHA_SOLVER_BROWSER_PROXY", raising=False)
        monkeypatch.delenv("BROWSER_PROXY", raising=False)
    pool, goc, thuong = _pool_gia()
    asyncio.run(pool._open_context("ho-so-test-proxy", headless=True))
    gia = goc.firefox if engine == "firefox" else thuong.chromium
    assert gia.kwargs is not None, "nhánh launch không được gọi"
    return gia.kwargs


def test_firefox_nhan_proxy_khi_da_cau_hinh(monkeypatch):
    kwargs = _mo_context("firefox", True, monkeypatch)
    assert kwargs.get("proxy") == {"server": _PROXY_TEST}


def test_firefox_khong_proxy_khi_chua_cau_hinh(monkeypatch):
    kwargs = _mo_context("firefox", False, monkeypatch)
    assert "proxy" not in kwargs


def test_chromium_nhan_proxy_khi_da_cau_hinh(monkeypatch):
    kwargs = _mo_context("chromium", True, monkeypatch)
    assert kwargs.get("proxy") == {"server": _PROXY_TEST}


def test_chromium_khong_proxy_khi_chua_cau_hinh(monkeypatch):
    kwargs = _mo_context("chromium", False, monkeypatch)
    assert "proxy" not in kwargs
