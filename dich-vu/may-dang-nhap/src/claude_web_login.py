# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/claude_web_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Claude Web login (claude.ai via Google account).

Mirror of gemini_web_login.py: reuses auto_login.do_google_login_steps for
the Google email/password/2FA dance and click_google_oauth_consent for the
"Continue as <email>" shortcut when the profile already holds a Google
session (from Flow / ChatGPT / Gemini onboard) — that's the "reuse Google
account" path đại ca wants: no second 2FA.

Unlike Gemini Web (no standalone token), claude.ai DOES expose a usable
credential: the `sessionKey` cookie. After login we scrape it so the main
chatgpt2api app can call claude.ai's API with it (see api/claude.py).
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

_CLAUDE_HOME = "https://claude.ai/"
_CLAUDE_LOGIN = "https://claude.ai/login"
_SESSION_COOKIE = "sessionKey"

# "Continue with Google" button on the claude.ai login screen.
_GOOGLE_BTN_SELECTORS = (
    'button[data-provider="google"]',
    'button:has-text("Continue with Google")',
    'button:has-text("Tiếp tục với Google")',
    'a[href*="accounts.google.com"]',
    'button:has-text("Google")',
    'div[role="button"]:has-text("Google")',
)


@dataclass
class ClaudeWebLoginSession(LoginSession):
    """Captures the claude.ai sessionKey cookie on success."""
    session_key: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["session_key"] = self.session_key
        base["session_key_preview"] = (self.session_key[:18] + "...") if self.session_key else ""
        base["has_session_key"] = bool(self.session_key)
        return base


_sessions: dict[str, ClaudeWebLoginSession] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_session(profile: str) -> Optional[ClaudeWebLoginSession]:
    return _sessions.get(profile)


async def get_saved_session(profile: str) -> Optional[ClaudeWebLoginSession]:
    """Recover a stored cookie after restart; validity is checked by Claude HTTP."""
    session = get_session(profile)
    if session and session.session_key and not pool.is_loaded(profile):
        return session
    cookies = await pool.read_cookies(profile, _CLAUDE_HOME)
    key = next((str(c["value"]) for c in cookies if c.get("name") == _SESSION_COOKIE and c.get("value")), "")
    if not key:
        return None
    if session is None:
        session = ClaudeWebLoginSession(profile=profile, email="", state="stored",
                                        message="Đã đọc phiên lưu trên đĩa", completed_at=time.time())
        _sessions[profile] = session
    session.session_key = key
    return session


def submit_2fa_code(profile: str, code: str) -> bool:
    session = _sessions.get(profile)
    if not session or session.state != "need_code":
        return False
    session.pending_code = code.strip()
    session.message = "Đã nhận mã, đang submit..."
    return True


async def _scrape_session_key(ctx) -> str:
    """Read the claude.ai `sessionKey` cookie value from the context."""
    try:
        cookies = await ctx.cookies("https://claude.ai")
    except Exception:
        try:
            cookies = await ctx.cookies()
        except Exception:
            return ""
    for c in cookies:
        if c.get("name") == _SESSION_COOKIE and str(c.get("value") or ""):
            return str(c.get("value"))
    return ""


async def _pick_google_account(page, email: str) -> bool:
    """On Google's account chooser ("Choose an account to continue to Claude")
    inside the OAuth popup, click the tile for `email`. Returns True if a tile
    was clicked. No-op (False) when the chooser/email isn't present — the
    consent vocab in click_google_oauth_consent can't match account tiles
    (they show the email/name, not an affirmative button), so we need this.
    """
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
                        if (id.includes('@') || t.includes('@')) {
                            r.click(); return true;
                        }
                    }
                }
                return false;
            }""",
            email,
        )
        if clicked:
            logger.info("claude_login: picked Google account %s", email)
            await asyncio.sleep(2.0)
            # Try clicking "Continue" / "Tiếp tục" button that appears after picking an account
            try:
                await page.evaluate("""() => {
                    const all = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                    for (const el of all) {
                        if (!el.offsetParent) continue;
                        const t = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (t === 'continue' || t === 'tiếp tục' || t === 'tiep tuc') {
                            el.click();
                            return;
                        }
                    }
                }""")
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return bool(clicked)
    except Exception:
        return False


async def start_claude_web_login(
    profile: str,
    email: str,
    password: str,
    totp_secret: str = "",
    prefer_method: str = "auth",
) -> ClaudeWebLoginSession:
    """Kick off background Claude Web login.

    If the profile already has a Google session (from another onboard), the
    Google login is skipped — claude.ai rides the SSO via OAuth consent.
    """
    old_task = _tasks.pop(profile, None)
    if old_task and not old_task.done():
        old_task.cancel()

    session = ClaudeWebLoginSession(
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


async def _run(session: ClaudeWebLoginSession, password: str) -> None:
    # Đóng browser khi XONG (success HOẶC failed): sessionKey đã được scrape vào
    # session (memory) + cookies persist trên disk, nên không cần giữ cửa sổ
    # headful trên noVNC. Trước đây success để mở → user phải nhấn X tay. Reuse
    # sau tự mở lại headless. CancelledError (login mới chiếm profile) propagate
    # → KHÔNG close (né race).
    # Đánh dấu hồ sơ đang đăng nhập: việc khác (tạo ảnh/video Flow) gọi
    # close_profile sẽ BỎ QUA, không đóng trình duyệt giữa lúc đang mở
    # trang đăng nhập (nếu đóng thì page.goto chết với net::ERR_ABORTED).
    pool.dau_dang_nhap(session.profile)
    try:
        await _run_inner(session, password)
    finally:
        pool.xong_dang_nhap(session.profile)
    if session.state in ("success", "failed"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
            logger.info("closed browser after %s onboard profile=%s", session.state, session.profile)
        except Exception:
            logger.debug("close_profile after onboard skipped", exc_info=True)

async def _run_inner(session: ClaudeWebLoginSession, password: str) -> None:
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

        # ── Already have a claude.ai session on this profile? short-circuit ──
        session.state = "running"
        session.message = "Mở claude.ai..."
        await page.goto(_CLAUDE_HOME, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3.0)

        existing = await _scrape_session_key(ctx)
        if existing:
            session.session_key = existing
            session.state = "success"
            session.message = "Profile đã có Claude session — không cần đăng nhập"
            session.completed_at = time.time()
            return

        # ── Open the login screen + click "Continue with Google" ──
        session.message = "Mở trang đăng nhập claude.ai..."
        try:
            if "/login" not in (page.url or ""):
                await page.goto(_CLAUDE_LOGIN, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(2.0)
        except Exception:
            pass

        # Cloudflare có thể chặn bằng trang "Thực hiện xác minh bảo mật" — lúc đó
        # nút Google CHƯA tồn tại. Chờ (và thử click) qua trước khi đi tìm nút,
        # thay vì báo "không tìm thấy nút" rồi bỏ cuộc.
        try:
            from .solvers.turnstile import pass_challenge
            session.message = "Đang qua xác minh Cloudflare..."
            if not await pass_challenge(page, timeout=45.0, log_prefix="claude_login: "):
                session.message = ("Cloudflare đang chặn — vào VNC tích 'Xác minh bạn là "
                                   "con người' giúp em, phiên vẫn đang mở.")
        except Exception as exc:
            logger.debug("claude_login: bỏ qua bước Cloudflare: %s", exc)

        session.message = "Click 'Continue with Google'..."
        # claude.ai opens Google OAuth in a POPUP window — capture any new page
        # the click spawns so we drive the login there, not the opener tab.
        popup_holder: dict = {}
        def _on_popup(p):
            popup_holder.setdefault("page", p)
        ctx.on("page", _on_popup)

        google_clicked = False
        for _gname in (
            "Continue with Google",
            "Tiếp tục với Google",
            "Continue with google",
        ):
            try:
                loc = page.get_by_role("button", name=_gname)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(timeout=5_000, force=True)
                    google_clicked = True
                    logger.info("claude_login: clicked Google via role %r", _gname)
                    break
            except Exception:
                continue
        for sel in _GOOGLE_BTN_SELECTORS:
            if google_clicked:
                break
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=5_000)
                    google_clicked = True
                    logger.info("claude_login: clicked Google via %s", sel)
                    break
            except Exception:
                continue
        if not google_clicked:
            try:
                google_clicked = await page.evaluate("""() => {
                    const all = document.querySelectorAll('button, a, div[role="button"]');
                    for (const el of all) {
                        if (!el.offsetParent) continue;
                        const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                        const h = el.getAttribute('href') || '';
                        if (t.includes('google') || h.includes('accounts.google.com')) { el.click(); return true; }
                    }
                    return false;
                }""")
            except Exception:
                google_clicked = False
        if not google_clicked:
            try: ctx.remove_listener("page", _on_popup)
            except Exception: pass
            session.state = "failed"
            # Phân biệt: bị Cloudflare chặn ≠ trang đổi giao diện mất nút.
            _blocked = False
            try:
                from .solvers.turnstile import is_challenge_showing
                _blocked = await is_challenge_showing(page)
            except Exception:
                pass
            session.error = (
                "Cloudflare vẫn chặn (trang xác minh chưa qua) — vào VNC tích ô "
                "'Xác minh bạn là con người' rồi chạy lại."
                if _blocked else
                "Không tìm thấy nút 'Continue with Google' trên claude.ai"
            )
            session.completed_at = time.time()
            return

        # Resolve where Google OAuth landed: popup window (claude.ai default) or
        # same-tab redirect (fallback). `auth_page` is what we drive from here.
        auth_page = page
        for _ in range(30):
            pop = popup_holder.get("page")
            if pop is not None:
                auth_page = pop
                try:
                    await auth_page.wait_for_load_state("domcontentloaded", timeout=10_000)
                except Exception:
                    pass
                logger.info("claude_login: OAuth popup captured url=%s", getattr(auth_page, "url", "?"))
                break
            if "accounts.google.com" in (page.url or ""):
                auth_page = page
                logger.info("claude_login: OAuth same-tab redirect")
                break
            await asyncio.sleep(0.5)
        try: ctx.remove_listener("page", _on_popup)
        except Exception: pass

        # Settle on accounts.google.com.
        try:
            await auth_page.wait_for_url("**/accounts.google.com/**", timeout=20_000)
            logger.info("claude_login: at google page %s", auth_page.url)
        except Exception:
            logger.warning("claude_login: auth page not on accounts.google.com (url=%s)",
                            getattr(auth_page, "url", "?"))

        try:
            on_google = "accounts.google.com" in (auth_page.url or "")
        except Exception:
            on_google = False

        if on_google:
            session.message = "Chọn tài khoản Google + consent..."
            # Reuse path: account chooser shows our tile — click it first.
            picked = await _pick_google_account(auth_page, session.email)
            if picked:
                await asyncio.sleep(1.5)
            # After picking (or if no tile appeared), Google usually demands a
            # PASSWORD + 2FA re-auth because claude uses prompt=consent — this
            # lands on .../challenge/pwd. do_google_login_steps handles it
            # (it skips the email step when the password field is already shown,
            # and auto-fills the TOTP code for accounts with a totp_secret).
            # Picking WITHOUT entering the password was the bug that left new
            # accounts stuck on the login screen → no OAuth code → no sessionKey.
            # Picking navigates to a password/2FA challenge a few seconds later,
            # so POLL for the login form (URL contains "challenge" or a password/
            # email input appears) before deciding — checking too early sees only
            # the account chooser and wrongly skips the password step.
            need_login = False
            for _ in range(8):
                try:
                    u = auth_page.url or ""
                    if "accounts.google.com" not in u:
                        break  # already left Google (consent auto-granted)
                    if "challenge" in u or await auth_page.locator(
                            'input[type="password"], input[type="email"]').count() > 0:
                        need_login = True
                        break
                except Exception:
                    break
                await asyncio.sleep(1.2)
            if need_login:
                session.message = "Nhập mật khẩu Google..."
                ok = await do_google_login_steps(session, auth_page, ctx, password, session.prefer_method)
                if not ok:
                    return
                await _pick_google_account(auth_page, session.email)
            # Grant OAuth consent. The granular-consent screen ("Chọn nội dung
            # Claude có thể truy cập" + checkbox) may render a beat late and can
            # need several passes, so RETRY until it's clicked or the popup
            # leaves Google (OAuth code posted back). Picking-but-not-consenting
            # was the bug that left new accounts stuck without a sessionKey.
            for _ in range(12):
                try:
                    if auth_page.is_closed() or "accounts.google.com" not in (auth_page.url or ""):
                        break
                except Exception:
                    break
                await click_google_oauth_consent(auth_page, timeout=3.0)
                await asyncio.sleep(1.5)

        # Actively CLOSE the OAuth popup instead of relying on it auto-closing.
        # claude.ai sometimes leaves the popup open → the opener never hydrates
        # and a human has to press X to end the task ("báo thành công mà không
        # kết thúc"). Only close a real popup, never the opener tab itself.
        if auth_page is not page:
            for _ in range(6):  # popup may still be finishing the OAuth redirect
                try:
                    if auth_page.is_closed():
                        break
                    u = (auth_page.url or "")
                    if "accounts.google.com" not in u:  # left Google → OAuth done
                        await auth_page.close()
                        logger.info("claude_login: closed OAuth popup after consent")
                        break
                except Exception:
                    break
                await asyncio.sleep(1.0)
            else:
                try:
                    await auth_page.close()
                except Exception:
                    pass

        # Wait for the opener tab (claude.ai) to hydrate the session, then scrape.
        session.message = "Chờ claude.ai nhận session..."
        try:
            await page.bring_to_front()
        except Exception:
            pass
        try:
            await page.wait_for_url("**/claude.ai/**", timeout=30_000)
        except Exception:
            logger.warning("claude_login: opener not on claude.ai (url=%s)", getattr(page, "url", "?"))
        # The opener may need a reload to pick up the post-OAuth cookie.
        try:
            await page.reload(wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass
        await asyncio.sleep(3.0)

        for _ in range(20):
            key = await _scrape_session_key(ctx)
            if key:
                session.session_key = key
                session.state = "success"
                session.message = "Đăng nhập Claude Web thành công"
                session.completed_at = time.time()
                return
            await asyncio.sleep(1.5)

        # Soft-fail: Google cookies present but claude.ai sessionKey not seen.
        if await _already_logged_in(ctx):
            session.state = "failed"
            session.error = "Google OK nhưng chưa lấy được sessionKey của claude.ai (thử lại / kiểm tra noVNC)"
        else:
            session.state = "failed"
            session.error = f"Không hoàn tất đăng nhập claude.ai (url={getattr(page, 'url', '?')})"
        session.completed_at = time.time()

    except asyncio.CancelledError:
        session.state = "failed"
        session.error = "Bị huỷ (có yêu cầu login mới)"
        session.completed_at = time.time()
        raise
    except Exception as exc:
        logger.exception("claude_web_login crashed profile=%s", session.profile)
        session.state = "failed"
        session.error = str(exc)
        session.completed_at = time.time()


# ── Đăng nhập Claude Web bằng Email (NV271 - Ô 2 & Ô 4) ──────────────────────
# W2A: khối này (đến hết tệp) là phần THÊM của web2api — đường email + mã một lần,
# dừng ở thử thách (need_manual); không gọi bộ vượt, không import `.solvers`.

_CHO_O_EMAIL_GIAY = 15.0
_CHO_MA_EMAIL_GIAY = 300.0
_THU_THACH_IFRAME_SRC = "challenges.cloudflare.com"
_HEADER_CF_MITIGATED = "cf-mitigated"


async def _phat_hien_thu_thach_cau_truc(page, resp=None) -> bool:
    """Nhận biết thử thách Cloudflare bằng cấu trúc (header HTTP hoặc thẻ iframe),
    không dùng danh sách từ khoá chữ trên trang, không vượt."""
    if resp is not None:
        try:
            status = getattr(resp, "status", 200)
            if status == 403:
                hdrs = getattr(resp, "headers", {}) or {}
                for k, v in hdrs.items():
                    if k.lower() == _HEADER_CF_MITIGATED and "challenge" in str(v).lower():
                        return True
        except Exception:
            pass
    try:
        if await page.locator(f'iframe[src*="{_THU_THACH_IFRAME_SRC}"]').count() > 0:
            return True
    except Exception:
        pass
    return False


async def start_claude_web_email_login(
    profile: str,
    email: str,
) -> ClaudeWebLoginSession:
    """Bắt đầu luồng đăng nhập Claude Web bằng Email + mã xác nhận một lần."""
    old_task = _tasks.pop(profile, None)
    if old_task and not old_task.done():
        old_task.cancel()

    session = ClaudeWebLoginSession(
        profile=profile,
        email=email,
        state="starting",
        message="Khởi tạo Chrome",
    )
    _sessions[profile] = session

    task = asyncio.create_task(_run_email(session))
    _tasks[profile] = task
    return session


async def _run_email(session: ClaudeWebLoginSession) -> None:
    pool.dau_dang_nhap(session.profile)
    try:
        await _run_email_inner(session)
    finally:
        pool.xong_dang_nhap(session.profile)
    # Ở trạng thái need_manual thì GIỮ cửa sổ (người vào noVNC xử lý).
    # Chỉ đóng trình duyệt khi đã xong hẳn (success hoặc failed).
    if session.state in ("success", "failed"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
            logger.info("closed browser after %s email onboard profile=%s", session.state, session.profile)
        except Exception:
            logger.debug("close_profile after email onboard skipped", exc_info=True)


async def _run_email_inner(session: ClaudeWebLoginSession) -> None:
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

        # ── 1. Đã có sessionKey trong hồ sơ? Xong luôn ──
        session.state = "running"
        session.message = "Mở claude.ai..."
        resp = None
        try:
            resp = await page.goto(_CLAUDE_HOME, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass
        await asyncio.sleep(2.0)

        existing = await _scrape_session_key(ctx)
        if existing:
            session.session_key = existing
            session.state = "success"
            session.message = "Profile đã có Claude session — không cần đăng nhập"
            session.completed_at = time.time()
            return

        # ── 2. Kiểm tra thử thách Cloudflare tại trang chủ ──
        if await _phat_hien_thu_thach_cau_truc(page, resp):
            session.state = "need_manual"
            session.message = "Cloudflare đang chặn — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
            session.completed_at = time.time()
            return

        # ── 3. Mở trang đăng nhập ──
        session.message = "Mở trang đăng nhập claude.ai..."
        resp_login = None
        try:
            if "/login" not in (getattr(page, "url", "") or ""):
                resp_login = await page.goto(_CLAUDE_LOGIN, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(2.0)
        except Exception:
            pass

        if await _phat_hien_thu_thach_cau_truc(page, resp_login):
            session.state = "need_manual"
            session.message = "Cloudflare đang chặn — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
            session.completed_at = time.time()
            return

        # ── 4. Tìm ô nhập email ──
        o_email = None
        han_tim_email = time.time() + _CHO_O_EMAIL_GIAY
        while time.time() < han_tim_email:
            for sel in ('input[type="email"]', 'input[name="email"]', 'input[autocomplete="email"]'):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        o_email = loc
                        break
                except Exception:
                    pass
            if o_email is not None:
                break
            # Nếu trong lúc chờ phát hiện iframe thử thách
            if await _phat_hien_thu_thach_cau_truc(page):
                session.state = "need_manual"
                session.message = "Cloudflare đang chặn — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
                session.completed_at = time.time()
                return
            await asyncio.sleep(0.5)

        if o_email is None:
            # Không thấy ô email và không rõ đang ở đâu
            session.state = "need_manual"
            session.message = "Không tìm thấy ô nhập email trên claude.ai — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
            session.completed_at = time.time()
            return

        # ── 5. Điền email và bấm tiếp tục ──
        session.message = "Điền email đăng nhập..."
        await o_email.fill(session.email, timeout=8_000)

        # Bấm nút tiếp tục bằng email
        nut_gui = False
        for sel in (
            'button:has-text("Continue with email")',
            'button:has-text("Tiếp tục với email")',
            'button:has-text("Continue")',
            'button:has-text("Tiếp tục")',
            'button[type="submit"]',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=5_000)
                    nut_gui = True
                    break
            except Exception:
                pass
        if not nut_gui:
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass

        await asyncio.sleep(2.0)

        # Kiểm tra thử thách sau khi điền email
        if await _phat_hien_thu_thach_cau_truc(page):
            session.state = "need_manual"
            session.message = "Cloudflare đang chặn — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
            session.completed_at = time.time()
            return

        # ── 6. Chờ mã xác nhận một lần gửi về email ──
        session.state = "need_code"
        session.message = "Mã xác nhận đã gửi về email — nhập mã vào ô Mã 2FA rồi bấm gửi."

        han_cho_ma = time.time() + _CHO_MA_EMAIL_GIAY
        while time.time() < han_cho_ma:
            if session.pending_code:
                break
            await asyncio.sleep(0.5)

        if not session.pending_code:
            session.state = "failed"
            session.error = "Hết hạn chờ nhập mã xác nhận từ email"
            session.completed_at = time.time()
            return

        ma_nhap = session.pending_code
        session.pending_code = ""

        # ── 7. Điền mã xác nhận ──
        session.message = "Đang điền mã xác nhận..."
        o_ma = None
        for sel in (
            'input[autocomplete="one-time-code"]',
            'input[name="code"]',
            'input[type="tel"]',
            'input[type="text"]',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    o_ma = loc
                    break
            except Exception:
                pass

        if o_ma is None:
            if await _phat_hien_thu_thach_cau_truc(page):
                session.state = "need_manual"
                session.message = "Cloudflare đang chặn — vào noVNC xử lý thử thách/xác minh rồi bấm Kiểm tra lại."
                session.completed_at = time.time()
                return
            session.state = "failed"
            session.error = "Không tìm thấy ô nhập mã xác nhận trên trang"
            session.completed_at = time.time()
            return

        await o_ma.fill(ma_nhap, timeout=8_000)

        # Bấm nút xác nhận mã nếu có
        for sel in (
            'button:has-text("Verify")',
            'button:has-text("Xác nhận")',
            'button:has-text("Continue")',
            'button:has-text("Tiếp tục")',
            'button[type="submit"]',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=5_000)
                    break
            except Exception:
                pass

        await asyncio.sleep(2.0)

        # Kiểm tra nếu trang đòi thêm bước lạ sau khi nhập mã
        if await _phat_hien_thu_thach_cau_truc(page):
            session.state = "need_manual"
            session.message = "Trang yêu cầu thêm bước xác minh — vào noVNC xử lý rồi bấm Kiểm tra lại."
            session.completed_at = time.time()
            return

        # ── 8. Chờ đọc sessionKey ──
        session.message = "Chờ claude.ai xác nhận phiên..."
        for _ in range(20):
            key = await _scrape_session_key(ctx)
            if key:
                session.session_key = key
                session.state = "success"
                session.message = "Đăng nhập Claude Web thành công"
                session.completed_at = time.time()
                return
            await asyncio.sleep(1.0)

        # Nếu không có cookie sessionKey sau 20 vòng
        session.state = "failed"
        session.error = "Không lấy được sessionKey sau khi nhập mã (mã không đúng hoặc đã hết hạn)"
        session.completed_at = time.time()

    except asyncio.CancelledError:
        session.state = "failed"
        session.error = "Bị huỷ (có yêu cầu login mới)"
        session.completed_at = time.time()
        raise
    except Exception as exc:
        logger.exception("claude_web_email_login crashed profile=%s", session.profile)
        session.state = "failed"
        session.error = str(exc)
        session.completed_at = time.time()

