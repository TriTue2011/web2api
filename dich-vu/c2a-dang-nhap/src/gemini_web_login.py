# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/gemini_web_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
# W2A: Bỏ ký tự U+FEFF vô hình trước docstring gốc để tránh SyntaxError trên Python 3.13.
"""Gemini Web login (gemini.google.com via Google account).

Reuses auto_login.do_google_login_steps for the email/password/2FA dance.

Unlike ChatGPT Web (which scrapes /api/auth/session for a JWT), Gemini
Web doesn't expose a standalone bearer token — everything goes through
the logged-in browser session. So instead of returning an access_token,
this module just confirms the profile has a valid Gemini session and
the chat / image / music capabilities can read/write the page.

The actual chat / image / music handlers live in solvers/gemini_web.py
and operate on the persistent browser context.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from .auto_login import (
    LoginSession,
    _already_logged_in,
    click_google_oauth_consent,
    do_google_login_steps,
)
from .browser_pool import pool

logger = logging.getLogger(__name__)


_GEMINI_HOME = "https://gemini.google.com/"
# Cookies that prove a logged-in Gemini session (subset of Google's).
_GEMINI_LOGIN_COOKIES = ("__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-3PSID", "SID")


@dataclass
class GeminiWebLoginSession(LoginSession):
    """No access_token — Gemini Web only confirms the session is alive."""
    pass


_sessions: dict[str, GeminiWebLoginSession] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_session(profile: str) -> Optional[GeminiWebLoginSession]:
    return _sessions.get(profile)


def submit_2fa_code(profile: str, code: str) -> bool:
    session = _sessions.get(profile)
    if not session or session.state != "need_code":
        return False
    session.pending_code = code.strip()
    session.message = "Đã nhận mã, đang submit..."
    return True


async def start_gemini_web_login(profile: str, email: str, password: str, totp_secret: str = "", prefer_method: str = "auth") -> GeminiWebLoginSession:
    """Kick off background Gemini Web login.

    If the profile already has a valid Google session (from Flow or
    ChatGPT onboard), this short-circuits to success without re-running
    the email/password flow — Gemini just uses Google's SSO cookie.
    """
    old_task = _tasks.pop(profile, None)
    if old_task and not old_task.done():
        old_task.cancel()

    session = GeminiWebLoginSession(
        profile=profile,
        email=email,
        state="starting",
        message="Khởi tạo Chrome",
        totp_secret=totp_secret,
        prefer_method=prefer_method,
    )
    _sessions[profile] = session

    task = asyncio.create_task(_run(session, password))
    _tasks[profile] = task
    return session


async def _gemini_session_ready(page) -> bool:
    """Check if Gemini Web is actually logged in.

    Gemini renders the chat editor even for anonymous users (preview
    mode), so contenteditable presence alone is a false positive.
    Reliable signal: absence of a top-bar 'Đăng nhập' / 'Sign in' button.
    """
    try:
        result = await page.evaluate(
            """() => {
                // Has prompt editor visible?
                const ces = Array.from(document.querySelectorAll('[contenteditable=true]'));
                const has_editor = ces.some(e => e.offsetWidth > 200 && e.offsetHeight > 0);
                if (!has_editor) return {ready: false, reason: 'no_editor'};

                // Find any visible 'Đăng nhập' / 'Sign in' / 'Log in' button.
                // If present, user is NOT logged in (preview mode).
                const buttons = Array.from(document.querySelectorAll('button, a'));
                const login_btn = buttons.find(b => {
                    if (b.offsetWidth === 0) return false;
                    const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t === 'đăng nhập' || t === 'sign in' || t === 'log in';
                });
                if (login_btn) return {ready: false, reason: 'login_button_visible'};

                // Look for a logged-in indicator: user avatar (usually
                // <img alt="Google Account"> or a button with class
                // gb_d / gb_g / similar Google account chip).
                const account_btn = document.querySelector(
                    'a[aria-label*="Google Account"], a[href*="accounts.google.com/SignOutOptions"], '
                    + 'img[alt*="Google Account"], a[aria-label*="Tài khoản Google"]'
                );
                return {ready: !!account_btn, reason: account_btn ? 'account_chip' : 'no_account_chip'};
            }"""
        )
        if isinstance(result, dict):
            logger.info("gemini_session_ready: %s", result)
            return bool(result.get("ready"))
        return False
    except Exception as exc:
        logger.warning("gemini_session_ready check failed: %s", exc)
        return False


async def _gemini_send_hello(page) -> bool:
    """Send a 'xin chào' greeting in Gemini to ACTIVATE the account.

    A freshly logged-in Google session lands Gemini in *preview/guest* mode:
    the account chip may show, yet the __Secure-1PSID stays UNAUTHENTICATED
    (status 1016) — so the API rejects vision/image/music. The account only
    flips to AVAILABLE (status 1000, authenticated 1PSID) after the first real
    conversation turn. So we type a greeting, send it, and wait for a reply;
    the cookie extracted afterwards is the authenticated one. Returns True if a
    model response was seen.
    """
    try:
        editor = None
        for sel in (
            "rich-textarea .ql-editor[contenteditable=true]",
            "div.ql-editor[contenteditable=true]",
            "[contenteditable=true]",
        ):
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=8_000)
                editor = loc
                break
            except Exception:
                continue
        if editor is None:
            logger.warning("gemini_send_hello: no prompt editor found")
            return False

        await editor.click()
        await asyncio.sleep(0.3)
        await page.keyboard.type("xin chào", delay=40)
        await asyncio.sleep(0.4)

        sent = False
        for sel in (
            "button[aria-label*='Gửi']",
            "button[aria-label*='Send']",
            "button.send-button",
            "button[mattooltip*='Gửi']",
            "button[mattooltip*='Send']",
        ):
            try:
                btn = page.locator(sel).first
                if await btn.count() and await btn.is_enabled():
                    await btn.click()
                    sent = True
                    break
            except Exception:
                continue
        if not sent:
            await page.keyboard.press("Enter")

        logger.info("gemini_send_hello: message sent, waiting for reply")

        for _ in range(40):
            try:
                has_resp = await page.evaluate(
                    """() => {
                        const els = document.querySelectorAll(
                          'model-response, message-content.model-response-text, '
                          + '.response-container, .model-response-text');
                        for (const e of els) {
                          if ((e.innerText || '').trim().length > 0) return true;
                        }
                        return false;
                    }"""
                )
                if has_resp:
                    logger.info("gemini_send_hello: reply received (account activated)")
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
        logger.warning("gemini_send_hello: no reply within timeout")
        return False
    except Exception as exc:
        logger.warning("gemini_send_hello failed: %s", exc)
        return False


async def _run(session: GeminiWebLoginSession, password: str) -> None:
    # Onboard FAIL -> dong browser ngay de khoi dot CPU tren Xvfb.
    # CancelledError (login moi chiem profile) propagate -> KHONG close (ne race).
    # Đánh dấu hồ sơ đang đăng nhập: việc khác (tạo ảnh/video Flow) gọi
    # close_profile sẽ BỎ QUA, không đóng trình duyệt giữa lúc đang mở
    # trang đăng nhập (nếu đóng thì page.goto chết với net::ERR_ABORTED).
    pool.dau_dang_nhap(session.profile)
    try:
        await _run_inner(session, password)
    finally:
        pool.xong_dang_nhap(session.profile)
    # Đóng browser khi XONG (success HOẶC failed): cookies persist trên disk +
    # session lưu memory, không cần giữ cửa sổ headful trên noVNC (trước success
    # để mở → phải nhấn X tay). Reuse sau tự mở lại headless.
    if session.state in ("success", "failed"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
            logger.info("closed browser after %s onboard profile=%s", session.state, session.profile)
        except Exception:
            logger.debug("close_profile after onboard skipped", exc_info=True)


async def _pick_google_account(page, email: str) -> bool:
    """On Google's account chooser, click the tile for `email` (SSO reuse) so we
    DON'T fall back to the full email/password login (which Google blocks
    headless on VPS IPs). Mirrors claude_web_login._pick_google_account — this is
    exactly what lets Claude reuse the Google session while gma couldn't."""
    try:
        if "accounts.google.com" not in (page.url or ""):
            return False
        clicked = await page.evaluate(
            """(email) => {
                email = (email || '').toLowerCase();
                if (email) {
                    const direct = document.querySelector(`[data-identifier="${email}"]`);
                    if (direct && direct.offsetParent) { direct.click(); return true; }
                }
                const rows = document.querySelectorAll('div[data-identifier], div[data-email], li[data-identifier]');
                for (const r of rows) {
                    if (r.offsetHeight === 0 || !r.offsetParent) continue;
                    const id = (r.getAttribute('data-identifier') || r.getAttribute('data-email') || '').toLowerCase();
                    const t = (r.innerText || '').toLowerCase();
                    if (email) {
                        if (id === email || t.includes(email)) { r.click(); return true; }
                    } else {
                        if (id.includes('@') || t.includes('@')) { r.click(); return true; }
                    }
                }
                return false;
            }""",
            email,
        )
        if clicked:
            logger.info("gemini_login: picked Google account %s", email)
            await asyncio.sleep(2.0)
            try:
                await page.evaluate("""() => {
                    const all = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                    for (const el of all) {
                        if (!el.offsetParent) continue;
                        const t = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (t === 'continue' || t === 'tiếp tục' || t === 'tiep tuc') { el.click(); return; }
                    }
                }""")
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return bool(clicked)
    except Exception:
        return False


async def _run_inner(session: GeminiWebLoginSession, password: str) -> None:
    try:
        session.state = "starting"
        session.message = "Đang mở Chrome (headful → noVNC)"
        ctx = await pool.get(profile=session.profile, headless=False, force_recreate=True)

        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        session.state = "running"
        session.message = "Mở gemini.google.com..."
        await page.goto(_GEMINI_HOME, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3.0)
        # Cloudflare "xác minh bảo mật" → chờ/click qua trước khi thao tác.
        try:
            from .solvers.turnstile import pass_challenge
            await pass_challenge(page, timeout=45.0, log_prefix="gemini_login: ")
        except Exception:
            pass

        # If already authenticated (has account chip + no login button),
        # still send a greeting to ACTIVATE the account (guest→authenticated
        # 1PSID) before short-circuiting to success.
        if await _gemini_session_ready(page):
            session.message = "Profile đã có session — gửi 'xin chào' để activate..."
            await _gemini_send_hello(page)
            session.state = "success"
            session.message = "Profile đã có Gemini session (đã activate)"
            session.completed_at = time.time()
            return

        # Gemini renders preview mode for anonymous users — no auto-redirect
        # to login. Click the 'Đăng nhập' button to trigger the OAuth flow.
        session.message = "Click 'Đăng nhập' trên Gemini Web..."
        clicked = await page.evaluate("""
            () => {
                const buttons = Array.from(document.querySelectorAll('button, a'));
                const btn = buttons.find(b => {
                    if (b.offsetWidth === 0) return false;
                    const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t === 'đăng nhập' || t === 'sign in' || t === 'log in';
                });
                if (!btn) return false;
                btn.click();
                return true;
            }
        """)
        if not clicked:
            session.state = "failed"
            session.error = "Không tìm thấy nút 'Đăng nhập' trên gemini.google.com"
            session.completed_at = time.time()
            return

        # Wait for redirect to accounts.google.com.
        try:
            await page.wait_for_url("**/accounts.google.com/**", timeout=20_000)
            logger.info("gemini_login: at google login page %s", page.url)
        except Exception:
            logger.warning("gemini_login: no accounts.google.com redirect after login click")

        try:
            on_google = "accounts.google.com" in page.url
        except Exception:
            on_google = False

        # SSO reuse: if Google shows the ACCOUNT CHOOSER, click the account tile
        # (like Claude) BEFORE anything else — this rides the logged-in Google
        # session so we never hit the email/password form Google blocks headless.
        try:
            picked = await _pick_google_account(page, session.email)
            if picked:
                session.message = "Đã chọn tài khoản Google (SSO)..."
                await asyncio.sleep(1.5)
        except Exception:
            picked = False

        # Pre-consent: when Google has a session cookie for the profile,
        # it skips the email/password form and shows a one-button confirm
        # page. Click it first so do_google_login_steps doesn't fail on
        # the missing email input.
        try:
            pre_consent = await click_google_oauth_consent(page, timeout=6.0)
            if pre_consent:
                session.message = "Đã bấm OAuth consent..."
                await asyncio.sleep(2.0)
        except Exception:
            pre_consent = False

        if on_google and not pre_consent and not picked:
            ok = await do_google_login_steps(session, page, ctx, password, session.prefer_method)
            if not ok:
                return

            # Post-login consent — appears after fresh email/password too.
            try:
                if await click_google_oauth_consent(page, timeout=8.0):
                    session.message = "Đã bấm OAuth consent..."
                    await asyncio.sleep(2.0)
            except Exception as exc:
                logger.debug("gemini_login: consent click skipped: %s", str(exc)[:120])

            # After Google login, wait for redirect back to gemini.google.com.
            try:
                await page.wait_for_url("**/gemini.google.com/**", timeout=30_000)
            except Exception:
                logger.warning("gemini_login: no return to gemini (url=%s)",
                                getattr(page, "url", "?"))
            await asyncio.sleep(3.0)
        elif pre_consent or picked:
            # SSO/consent path (account picked or one-click confirm): click any
            # remaining OAuth consent, then wait for redirect back to gemini.
            try:
                await click_google_oauth_consent(page, timeout=8.0)
            except Exception:
                pass
            try:
                await page.wait_for_url("**/gemini.google.com/**", timeout=15_000)
            except Exception:
                pass

            # Picking the account often lands on a password/2FA challenge
            # (session needs re-auth). If we're still on accounts.google.com,
            # run the email-optional login dance to fill password + 2FA, then
            # click consent and wait for the gemini redirect again.
            try:
                still_google = "accounts.google.com" in (getattr(page, "url", "") or "")
            except Exception:
                still_google = False
            if still_google:
                session.message = "Google yêu cầu xác thực lại — nhập mật khẩu..."
                ok = await do_google_login_steps(session, page, ctx, password, session.prefer_method)
                if not ok:
                    return
                try:
                    if await click_google_oauth_consent(page, timeout=8.0):
                        await asyncio.sleep(2.0)
                except Exception:
                    pass
                try:
                    await page.wait_for_url("**/gemini.google.com/**", timeout=30_000)
                except Exception:
                    logger.warning("gemini_login: SSO+pwd path no return (url=%s)",
                                    getattr(page, "url", "?"))
            await asyncio.sleep(3.0)

        # After login, send a greeting to ACTIVATE the account so the extracted
        # 1PSID is authenticated (status 1000) rather than guest (1016).
        session.message = "Đăng nhập xong — gửi 'xin chào' để activate account..."
        await _gemini_send_hello(page)

        # Verify Gemini is now actually logged in (account chip visible).
        for _ in range(20):
            if await _gemini_session_ready(page):
                session.state = "success"
                session.message = "Đăng nhập Gemini Web thành công (đã activate)"
                session.completed_at = time.time()
                return
            await asyncio.sleep(1.0)

        # Fall back — if Google login cookies present, treat as soft-success.
        if await _already_logged_in(ctx):
            session.state = "success"
            session.message = "Google OK nhưng Gemini chưa hydrate đủ — chat thử có thể work"
            session.completed_at = time.time()
            return

        session.state = "failed"
        session.error = f"Không thấy Gemini logged-in (url={getattr(page, 'url', '?')})"
        session.completed_at = time.time()

    except asyncio.CancelledError:
        session.state = "failed"
        session.error = "Bị huỷ (có yêu cầu login mới)"
        session.completed_at = time.time()
        raise
    except Exception as exc:
        logger.exception("gemini_web_login crashed profile=%s", session.profile)
        session.state = "failed"
        session.error = str(exc)
        session.completed_at = time.time()

