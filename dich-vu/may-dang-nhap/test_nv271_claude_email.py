# test_nv271_claude_email.py — Test dịch vụ may-dang-nhap luồng Claude email.
# NV271: Ô 2 - Đăng nhập Claude bằng email + mã một lần; Ô 4 - Dừng ở need_manual khi gặp thử thách.
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gia_goi  # noqa: F401

from src import claude_web_login
from src.browser_pool import pool


_PHIEN_GIA = "phien-" + "gia-nv271-mot"  # chuỗi giả, ghép để quét bí mật không nhầm

def _chay(coro):
    return asyncio.run(coro)


_SLEEP_THAT = asyncio.sleep


@pytest.fixture(autouse=True)
def _ngu_nhanh(monkeypatch):
    """Bỏ các nhịp chờ cố định (2 giây) của luồng để test chạy nhanh."""
    async def nhanh(d, *a, **k):
        return await _SLEEP_THAT(min(float(d), 0.01), *a, **k)
    monkeypatch.setattr(asyncio, "sleep", nhanh)


async def _doi_trang_thai(session, trang_thai, toi_da_s=5.0):
    han = asyncio.get_running_loop().time() + toi_da_s
    while session.state != trang_thai:
        if asyncio.get_running_loop().time() > han:
            raise TimeoutError(f"state={session.state!r}, mong {trang_thai!r}")
        await _SLEEP_THAT(0.01)


async def _chay_den_het(profile, ctx, email="chu.claude@example.com"):
    """Chạy luồng email trong MỘT vòng sự kiện tới khi tác vụ nền xong."""
    dong = AsyncMock()
    with patch.object(pool, "get", AsyncMock(return_value=ctx)), \
         patch.object(pool, "close_profile", dong):
        session = await claude_web_login.start_claude_web_email_login(profile=profile, email=email)
        await asyncio.wait_for(claude_web_login._tasks[profile], timeout=5.0)
    return session, dong


class _PhanHoiGia:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {}


class _DinhViGia:
    def __init__(self, trang, sel):
        self._trang = trang
        self._sel = sel
        self.dien_noi_dung = []

    @property
    def first(self):
        return self

    async def count(self):
        return self._trang._dem(self._sel)

    async def is_visible(self, timeout=None):
        return self._trang._dem(self._sel) > 0

    async def fill(self, text, timeout=None):
        self.dien_noi_dung.append(text)
        self._trang._da_dien[self._sel] = text

    async def click(self, timeout=None, force=False):
        self._trang._da_click.append(self._sel)


class _TrangGia:
    def __init__(self, *, status_goto=200, headers_goto=None, co_iframe_cf=False,
                 co_o_email=True, co_o_ma=True):
        self.url = "about:blank"
        self._status_goto = status_goto
        self._headers_goto = headers_goto or {}
        self._co_iframe_cf = co_iframe_cf
        self._co_o_email = co_o_email
        self._co_o_ma = co_o_ma
        self._da_dien = {}
        self._da_click = []
        self.keyboard = MagicMock()
        self.keyboard.press = AsyncMock()

    async def goto(self, url, wait_until="domcontentloaded", timeout=30000):
        self.url = url
        return _PhanHoiGia(self._status_goto, self._headers_goto)

    async def bring_to_front(self):
        pass

    def locator(self, sel):
        return _DinhViGia(self, sel)

    def get_by_role(self, role, name=None):
        return _DinhViGia(self, f"role={role},name={name}")

    def _dem(self, sel):
        if "challenges.cloudflare.com" in sel:
            return 1 if self._co_iframe_cf else 0
        if 'input[type="email"]' in sel or 'input[name="email"]' in sel:
            return 1 if self._co_o_email else 0
        if 'input[type="text"]' in sel or 'input[name="code"]' in sel or 'input[autocomplete="one-time-code"]' in sel or 'input[type="tel"]' in sel:
            return 1 if self._co_o_ma else 0
        if "button" in sel or "role=button" in sel:
            return 1
        return 0


class _CtxGia:
    def __init__(self, trang, cookies_list=None):
        self._trang = trang
        self._cookies = list(cookies_list or [])

    @property
    def pages(self):
        return [self._trang]

    async def new_page(self):
        return self._trang

    async def cookies(self, url=None):
        return list(self._cookies)


def _bay_chong_bot(*args, **kwargs):
    raise AssertionError("pass_challenge bị gọi — vi phạm LUẬT CỨNG về chống bot!")


@pytest.fixture(autouse=True)
def _chan_pass_challenge(monkeypatch):
    """Chặn mọi lời gọi tới pass_challenge/solvers."""
    try:
        from src.solvers import turnstile
        monkeypatch.setattr(turnstile, "pass_challenge", _bay_chong_bot)
    except Exception:
        pass


def test_iframe_thu_thach_dung_o_need_manual():
    """Trang có iframe thử thách → need_manual, KHÔNG điền gì, KHÔNG đóng cửa sổ."""
    trang = _TrangGia(co_iframe_cf=True)
    session, dong = _chay(_chay_den_het("claude-test-cf", _CtxGia(trang)))
    assert session.state == "need_manual"
    assert "noVNC" in session.message
    assert trang._da_dien == {}
    assert dong.call_count == 0


def test_phan_hoi_403_cf_mitigated_dung_o_need_manual():
    trang = _TrangGia(status_goto=403, headers_goto={"cf-mitigated": "challenge"})
    session, dong = _chay(_chay_den_het("claude-test-403", _CtxGia(trang)))
    assert session.state == "need_manual"
    assert trang._da_dien == {}
    assert dong.call_count == 0


def test_403_khong_co_tieu_de_thu_thach_khong_bi_coi_la_thu_thach():
    """403 trơn (không cf-mitigated) và không khung nhúng → đi tiếp luồng thường."""
    trang = _TrangGia(status_goto=403, headers_goto={}, co_o_email=True)

    async def kich_ban():
        ctx = _CtxGia(trang)
        with patch.object(pool, "get", AsyncMock(return_value=ctx)), \
             patch.object(pool, "close_profile", AsyncMock()):
            session = await claude_web_login.start_claude_web_email_login(
                profile="claude-test-403-tron", email="chu.claude@example.com")
            await _doi_trang_thai(session, "need_code")
            trang_thai = session.state
            claude_web_login._tasks["claude-test-403-tron"].cancel()
        return trang_thai

    assert _chay(kich_ban()) == "need_code"


def test_khong_thay_o_email_dung_o_need_manual(monkeypatch):
    monkeypatch.setattr(claude_web_login, "_CHO_O_EMAIL_GIAY", 0.05)
    trang = _TrangGia(co_o_email=False)
    session, dong = _chay(_chay_den_het("claude-test-no-email", _CtxGia(trang)))
    assert session.state == "need_manual"
    assert trang._da_dien == {}
    assert dong.call_count == 0


def test_ho_so_da_co_session_key_success_ngay():
    trang = _TrangGia()
    ctx = _CtxGia(trang, cookies_list=[{"name": "sessionKey", "value": _PHIEN_GIA}])
    session, dong = _chay(_chay_den_het("claude-test-existing", ctx))
    assert session.state == "success"
    assert session.session_key == _PHIEN_GIA
    assert trang._da_dien == {}
    assert dong.call_count >= 1


def test_luong_thuan_email_need_code_nhap_ma_success(caplog):
    trang = _TrangGia(co_o_email=True, co_o_ma=True)
    ctx = _CtxGia(trang)

    async def kich_ban():
        with patch.object(pool, "get", AsyncMock(return_value=ctx)), \
             patch.object(pool, "close_profile", AsyncMock()):
            session = await claude_web_login.start_claude_web_email_login(
                profile="claude-test-flow", email="chu.claude@example.com")
            await _doi_trang_thai(session, "need_code")
            assert "email" in session.message.lower()
            assert trang._da_dien  # đã điền email
            ctx._cookies.append({"name": "sessionKey", "value": "phien-gia-nv271-after-code"})
            assert claude_web_login.submit_2fa_code("claude-test-flow", "123456") is True
            await asyncio.wait_for(claude_web_login._tasks["claude-test-flow"], timeout=5.0)
            return session

    with caplog.at_level(logging.DEBUG):
        session = _chay(kich_ban())
    assert session.state == "success"
    assert session.session_key == "phien-gia-nv271-after-code"
    log_text = caplog.text
    assert "chu.claude@example.com" not in log_text
    assert "123456" not in log_text
    assert "phien-gia-nv271-after-code" not in log_text


def test_dien_ma_nhung_khong_co_cookie_thi_failed():
    trang = _TrangGia(co_o_email=True, co_o_ma=True)
    ctx = _CtxGia(trang)

    async def kich_ban():
        dong = AsyncMock()
        with patch.object(pool, "get", AsyncMock(return_value=ctx)), \
             patch.object(pool, "close_profile", dong):
            session = await claude_web_login.start_claude_web_email_login(
                profile="claude-test-wrong-code", email="chu.claude@example.com")
            await _doi_trang_thai(session, "need_code")
            claude_web_login.submit_2fa_code("claude-test-wrong-code", "999999")
            await asyncio.wait_for(claude_web_login._tasks["claude-test-wrong-code"], timeout=5.0)
        return session, dong

    session, dong = _chay(kich_ban())
    assert session.state == "failed"
    assert session.error
    assert dong.call_count >= 1


def test_khong_import_solvers_trong_duong_email():
    """Hàng rào luật cứng: mã đường email không tham chiếu tới solvers/pass_challenge."""
    import inspect
    nguon = "".join(inspect.getsource(getattr(claude_web_login, ten)) for ten in (
        "start_claude_web_email_login", "_run_email", "_run_email_inner",
        "_phat_hien_thu_thach_cau_truc"))
    assert "solvers" not in nguon and "pass_challenge" not in nguon
