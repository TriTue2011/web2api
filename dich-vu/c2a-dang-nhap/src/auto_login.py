# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/auto_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Google account auto-login orchestration with 2FA support.

Three states the user cares about:
  • running  — Playwright is busy on Chrome (typing email/password,
               waiting for Google's UI). UI shows "Đang chạy: <step>".
  • need_code — Google asked for an SMS/authenticator code. UI shows
                a code-entry input that POSTs to /2fa-code.
  • need_tap — Google sent a "tap this number" notification to the
               user's phone. UI shows the number to tap.
  • success / failed — terminal.

Anti-bot reality: Google often blocks headless-detected Chrome in a
container, especially from a VPS IP without a real device history.
If auto-login stalls, the noVNC window is still open and the user can
finish manually — the saved cookies still persist in user-data-dir.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from .browser_pool import HoSoDangBan, pool

try:
    import pyotp
    _HAS_PYOTP = True
except ImportError:
    pyotp = None  # type: ignore
    _HAS_PYOTP = False

logger = logging.getLogger(__name__)


_GOOGLE_SIGNIN_URL = (
    "https://accounts.google.com/signin/v2/identifier"
    "?hl=vi&service=accountsettings"
)

# Ngân sách cho GIAI ĐOẠN vào ô mật khẩu: bấm "Thử lại" + bấm lại vào mail, lặp
# tới khi lọt. ~4s/vòng nên 420s ≈ 100 lượt thử — BotGuard chập chờn, thử đủ
# nhiều sẽ lọt. Đừng hạ số này: hạ là cắt bớt số lần thử, đúng thứ làm tầng 3
# bỏ cuộc sớm rồi báo "Google chặn" oan.
_VAO_O_MAT_KHAU_S = 420.0

# Cookies that prove a Google login completed.
_GOOGLE_LOGIN_COOKIES = ("__Secure-1PSID", "__Secure-3PSID", "SID")

async def _type_human_like(locator, text: str) -> None:
    """Type text character-by-character with randomized delays to evade bot detection."""
    # Random pause before starting (human looks at phone, then starts typing)
    await asyncio.sleep(random.uniform(0.4, 1.2))
    for i, ch in enumerate(text):
        await locator.press(ch, delay=random.randint(80, 350))
        # Occasionally pause mid-code (human glances at phone between digits)
        if i == 2 and random.random() < 0.6:
            await asyncio.sleep(random.uniform(0.2, 0.6))


# Selectors Google uses for the 2FA code input (varies by challenge type).
_2FA_CODE_SELECTORS = (
    'input[type="tel"][autocomplete="one-time-code"]',
    'input[name="totpPin"]',
    'input[id="totpPin"]',
    'input[autocomplete="one-time-code"]',
    'input[type="tel"]:not([disabled])',
)

# Selectors for the tap-match number Google displays. They change often;
# we look for any short numeric digit in a heading-like element. Best-
# effort — if we can't extract it, we still report state="need_tap" so
# the user knows to look at noVNC.
_TAP_MATCH_SELECTORS = (
    'samp',
    'div[role="heading"] >> visible=true',
    '.eFajbf',
    '.NQ5OL',
)

# Selectors for the "Choose how you'll sign in" page. When Google shows
# a list of 2FA methods (tap-on-device / Authenticator / SMS / recovery
# email), this page sits between password and the actual challenge.
# `_detect_state` would otherwise treat it as "working" and the 4-minute
# deadline expires before any code field shows up. We pick the TOTP /
# Authenticator option automatically because it's the fastest fully-
# automatable path — user only has to read the 6-digit Authenticator
# code and POST it to /v1/session/{profile}/auto-login-2fa-code.
# IMPORTANT: text-based selectors MUST come before data-challengetype
# because when "Tap Yes" is disabled, data-challengetype="9" can match
# the wrong element (the disabled tap option instead of Authenticator).
_AUTHENTICATOR_OPTION_SELECTORS = (
    'li:has-text("Google Authenticator")',
    'li:has-text("ứng dụng xác thực")',
    'div[role="link"]:has-text("Google Authenticator")',
    'div[role="link"]:has-text("authenticator")',
    'div:has-text("Google Authenticator"):not(:has(div))',
    'li[data-challengetype="9"]:has-text("Authenticator")',
    'li[data-challengetype="9"]:has-text("xác thực")',
    'div[data-challengetype="9"]:has-text("Authenticator")',
    'div[data-challengetype="9"]:has-text("xác thực")',
    'li[data-challengetype="9"]',          # last resort
    'div[data-challengetype="9"]',          # last resort
)

# Selectors for phone-call / SMS challenge when Google offers the method
# picker. After clicking, Google calls or texts the enrolled number and
# then shows a code input — same need_code state as Authenticator.
_PHONE_OPTION_SELECTORS = (
    'li:has-text("cuộc gọi đến số")',
    'li:has-text("nhận cuộc gọi")',
    'li:has-text("nhận mã qua")',
    'div[role="link"]:has-text("cuộc gọi đến số")',
    'div[role="link"]:has-text("nhận cuộc gọi")',
    'div[role="link"]:has-text("nhận mã qua")',
    'li:has-text("số điện thoại"):not(:has-text("sử dụng"))',
    'div[role="link"]:has-text("số điện thoại"):not(:has-text("sử dụng"))',
)

_METHOD_SELECTOR_HINTS = (
    "choose how you", "chọn cách",
    "try another way", "thử cách khác",
    "google authenticator",
    "nhận mã xác minh", "ứng dụng xác thực",
)


async def _pick_authenticator_method(page) -> bool:
    """When Google shows the method picker, click the Authenticator entry."""
    for sel in _AUTHENTICATOR_OPTION_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=2500)
            logger.info("auto_login: picked Authenticator method via selector=%s", sel)
            return True
        except Exception:
            continue
            
    for t in ("authenticator", "ứng dụng xác thực"):
        try:
            loc = page.locator(f'text=/{t}/i').last
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                logger.info("auto_login: picked Authenticator method via text=/%s/i", t)
                return True
        except Exception:
            pass

    # Broad JS scan — Google's current picker labels the Authenticator row in
    # several ways (VI/EN) as a div[role=link]/li, not a button. Match a wide
    # phrase set and LOG every option seen so the labels are visible even when
    # nothing matched (drives the next selector tweak instead of guessing).
    try:
        res = await page.evaluate(r"""() => {
            const norm = s => (s || '').replace(/\s+/g, ' ').trim();
            const rows = Array.from(document.querySelectorAll(
                'li[data-challengetype],div[data-challengetype],div[role="link"],li'
            ));
            const re = /(google )?authenticator|ứng dụng xác thực|trình tạo mã|trình xác thực|mã xác minh.*ứng dụng|ứng dụng.*mã xác minh|xác thực.*(google|ứng dụng)/i;
            const seen = [];
            let hit = null;
            for (const r of rows) {
                const t = norm(r.innerText);
                if (!t || t.length > 120) continue;
                if (!seen.includes(t)) seen.push(t);
                if (!hit && re.test(t) && r.offsetWidth > 80) hit = r;
            }
            if (hit) { (hit.closest('li,div[role="link"]') || hit).click(); return {clicked: norm(hit.innerText).slice(0, 60), options: seen.slice(0, 12)}; }
            return {clicked: null, options: seen.slice(0, 12)};
        }""")
        logger.info("auto_login: method-picker options=%r", (res or {}).get("options"))
        if (res or {}).get("clicked"):
            logger.info("auto_login: picked Authenticator via JS scan: %s", res["clicked"])
            return True
    except Exception as exc:
        logger.warning("auto_login: authenticator JS scan failed: %s", str(exc)[:100])

    # KHÔNG nói "picker visible": hàm này chưa bao giờ kiểm điều đó. Câu cũ
    # khẳng định bảng chọn đang hiện, và lượt 12/09/2026 21:35–21:39 đã bị đọc
    # nhầm đúng vì nó — trang lúc ấy không có lấy một dòng chọn nào.
    logger.info("auto_login: no Authenticator row matched on this page")
    return False


async def _pick_phone_method(page) -> bool:
    """When Google shows the method picker, click the phone-call / SMS entry."""
    for sel in _PHONE_OPTION_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=2500)
            logger.info("auto_login: picked phone method via selector=%s", sel)
            return True
        except Exception:
            continue
            
    for t in ("cuộc gọi", "nhận cuộc gọi", "số điện thoại", "nhận mã qua", "gọi đến số"):
        try:
            loc = page.locator(f'text=/{t}/i').last
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                logger.info("auto_login: picked phone method via text=/%s/i", t)
                return True
        except Exception:
            pass

    logger.info("auto_login: no phone row matched on this page")
    return False


async def _pick_tap_method(page) -> bool:
    """When Google shows the method picker, click the 'Tap Yes on your phone' entry."""
    
    _TAP_OPTION_SELECTORS = (
        'li:has-text("Nhấn vào Có trên điện thoại")',
        'li:has-text("Nhấn Có trên điện thoại")',
        'li:has-text("Tap Yes on your phone")',
        'li:has-text("Tap Yes on your phone or tablet")',
        'div[role="link"]:has-text("Nhấn vào Có")',
        'div[role="link"]:has-text("Tap Yes")',
        'div[data-challengetype="12"]',
        'li[data-challengetype="12"]',
    )
    for sel in _TAP_OPTION_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                logger.info("auto_login: picked Tap method via selector=%s", sel)
                return True
        except Exception:
            continue
            
    for t in ("nhấn có", "tap yes", "nhấn vào có"):
        try:
            loc = page.locator(f'text=/{t}/i').last
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                logger.info("auto_login: picked Tap method via text=/%s/i", t)
                return True
        except Exception:
            pass

    logger.info("auto_login: no Tap row matched on this page")
    return False


# Phrases that mark a Google OAuth consent / account-confirm page.
# Match against the body text (lowercased). Any one of these means we
# should look for an affirmative button to click.
_CONSENT_SCREEN_HINTS = (
    "continue as", "tiếp tục với tư cách", "tiep tuc voi tu cach",
    "wants access", "muốn truy cập", "muon truy cap",
    "to continue to", "để tiếp tục với", "de tiep tuc voi",
    "by continuing, google will share", "khi tiếp tục, google sẽ chia sẻ",
    "review the permissions", "xem lại quyền", "xem lai quyen",
    "google sẽ chia sẻ", "google se chia se",
    # Granular-consent screen (enable_granular_consent=true) — checkbox list
    "select what", "chọn nội dung", "chon noi dung",
    "what * can access", "can access", "có thể truy cập", "co the truy cap",
    "share some", "chia sẻ một số", "chia se mot so",
    "allow this app", "cho phép ứng dụng này", "cho phep ung dung nay",
    "confirm your identity", "xác nhận danh tính", "xac nhan danh tinh",
    "verify it's you", "xác minh là bạn", "xac minh la ban",
    "choose an account", "chọn một tài khoản", "chon mot tai khoan",
    "sign in to", "đăng nhập vào", "dang nhap vao",
    "đăng nhập lại vào", "dang nhap lai vao",
    "bạn đang đăng nhập", "ban dang dang nhap",
    "đăng nhập bằng google", "dang nhap bang google",
)

# Affirmative button text — matched against innerText / aria-label
# (lowercased, trimmed). Includes both EN and VI variants. We match by
# equality OR startswith because Google often nests extra spans inside
# the button (e.g. "Continue\n>").
_CONSENT_AFFIRMATIVE_VOCAB = (
    "continue",
    "tiếp tục", "tiep tuc",
    "allow", "allow access",
    "cho phép", "cho phep",
    "confirm", "xác nhận", "xac nhan",
    "i agree", "đồng ý", "dong y",
    "next", "tiếp theo", "tiep theo",
    "yes, continue", "có, tiếp tục",
    "accept", "chấp nhận", "chap nhan",
)


async def click_google_oauth_consent(page, timeout: float = 8.0) -> bool:
    """Auto-click Google's OAuth consent / "Continue as <email>" / "Allow"
    pages that appear after a service (ChatGPT, Gemini, ...) redirects to
    accounts.google.com and the user has cookies for the Google account
    but the target app needs explicit consent.

    Safe to call multiple times — it no-ops unless body text contains
    one of `_CONSENT_SCREEN_HINTS`, so it won't fire on the email or
    password steps (which have their own dedicated handlers).

    Returns True if a click was performed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = page.url
        except Exception:
            url = ""
        if "accounts.google.com" not in url:
            return False
        try:
            body_text = (await page.locator("body").inner_text(timeout=800)).lower()
        except Exception:
            await asyncio.sleep(0.5)
            continue
        if not any(h in body_text for h in _CONSENT_SCREEN_HINTS):
            await asyncio.sleep(0.7)
            continue
        try:
            clicked_label = await page.evaluate(
                """
                (vocab) => {
                    // Granular consent: tick any unchecked permission boxes
                    // (native + Material role=checkbox) before hitting Continue.
                    document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').forEach(cb => {
                        if (!cb.offsetParent) return;
                        const checked = cb.checked || cb.getAttribute('aria-checked') === 'true';
                        if (!checked && !cb.disabled) { try { cb.click(); } catch (e) {} }
                    });
                    const sel = 'button, a, div[role="button"], input[type="submit"]';
                    const btns = Array.from(document.querySelectorAll(sel));
                    for (const b of btns) {
                        if (!b.offsetParent) continue;
                        let text = (b.innerText || b.getAttribute('aria-label') || b.value || '').trim().toLowerCase();
                        if (!text) continue;
                        // Strip surrounding whitespace and trailing punctuation.
                        text = text.replace(/\\s+/g, ' ');
                        for (const v of vocab) {
                            if (text === v || text.startsWith(v + ' ') || text.startsWith(v + ',') || text.startsWith(v + '.')) {
                                b.click();
                                return text;
                            }
                        }
                    }
                    return null;
                }
                """,
                list(_CONSENT_AFFIRMATIVE_VOCAB),
            )
            if clicked_label:
                logger.info("auto_login: clicked OAuth consent button text=%r url=%s",
                            clicked_label[:60], url)
                await asyncio.sleep(1.5)
                return True
        except Exception as exc:
            logger.debug("auto_login: consent click eval failed: %s", str(exc)[:120])
        await asyncio.sleep(0.7)
    return False


@dataclass
class LoginSession:
    profile: str
    email: str
    state: str = "pending"
    message: str = ""
    tap_number: Optional[str] = None
    pending_code: Optional[str] = None
    totp_secret: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None
    # Selects how to satisfy a 2FA challenge when Google offers a picker.
    # "auth" → click Authenticator → state=need_code, user POSTs 6-digit.
    # "tap"  → skip picker, fall through to tap-on-phone (state=need_tap).
    prefer_method: str = "auth"

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "email": self.email,
            "state": self.state,
            "message": self.message,
            "tap_number": self.tap_number,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_sec": int((self.completed_at or time.time()) - self.started_at),
            "error": self.error,
        }


_sessions: dict[str, LoginSession] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_session(profile: str) -> Optional[LoginSession]:
    return _sessions.get(profile)


def list_sessions() -> list[dict]:
    return [s.to_dict() for s in _sessions.values()]


def submit_2fa_code(profile: str, code: str) -> bool:
    """Feed an SMS / TOTP / backup code to a waiting auto-login.
    Returns False if there's no session waiting for a code."""
    session = _sessions.get(profile)
    if not session or session.state != "need_code":
        return False
    session.pending_code = code.strip()
    session.message = "Đã nhận mã, đang submit..."
    return True


async def _nuke_profile(profile: str, max_wait: float = 10.0) -> None:
    """Delete browser profile directory, retrying until files are unlocked.

    SAFETY: if another session (gemini_web, flow, etc.) is currently using
    this profile through the browser pool, we skip deletion entirely.
    Only nuke profiles that are NOT actively in use by another component.
    """
    if pool.is_loaded(profile):
        logger.info("auto_login: profile %s is loaded in pool, skipping nuke", profile)
        return

    import shutil
    from .settings import settings as _settings
    _profile_dir = _settings.data_dir / "profiles" / profile
    if not _profile_dir.exists():
        return
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            shutil.rmtree(str(_profile_dir), ignore_errors=False)
            logger.info("auto_login: nuked profile %s", profile)
            return
        except PermissionError:
            logger.warning("auto_login: profile %s locked, retrying in 1s...", profile)
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.warning("auto_login: rmtree attempt failed: %s, retrying...", exc)
            await asyncio.sleep(1.0)
    # Last resort: rename out of the way
    try:
        backup = _profile_dir.with_name(_profile_dir.name + f".old-{int(time.time())}")
        shutil.move(str(_profile_dir), str(backup))
        logger.warning("auto_login: could not delete, renamed to %s", backup.name)
    except Exception as exc:
        logger.error("auto_login: profile cleanup failed completely: %s", exc)


# Hạn chờ khi dọn context cũ lúc khởi động một lượt đăng nhập. Ngắn có chủ ý:
# đây chỉ là dọn dẹp, còn việc chờ tới lượt dùng hồ sơ là của `_run_inner`
# (hạn 120s, báo "Hồ sơ đang bận"). Xem `start_auto_login`.
_HAN_DON_CONTEXT_S = 5.0


async def start_auto_login(
    profile: str,
    email: str,
    password: str,
    prefer_method: str = "auth",
    totp_secret: Optional[str] = None,
) -> LoginSession:
    """Kick off background auto-login. Returns the LoginSession immediately
    so the UI can start polling /auto-login-status.

    `prefer_method` selects the 2FA path when Google offers a picker:
    "auth" = click Authenticator (need_code), "tap" = wait for the
    tap-on-device prompt (need_tap), "phone" = click phone call / SMS
    option (need_code after Google calls/texts).

    If `totp_secret` is provided and pyotp is installed, 2FA codes are
    auto-generated instead of waiting for manual input.
    """
    old_task = _tasks.pop(profile, None)
    if old_task and not old_task.done():
        old_task.cancel()

    session = LoginSession(
        profile=profile,
        email=email,
        state="starting",
        message="Khởi tạo Chrome",
        prefer_method=prefer_method if prefer_method in ("auth", "tap", "phone") else "auth",
        totp_secret=totp_secret,
    )
    _sessions[profile] = session

    # Dọn context cũ trước khi dùng lại — CÓ HẠN, và hết giờ KHÔNG phải lỗi.
    #
    # Hàm này phải trả lời NGAY để bên gọi có phiên mà poll; mọi việc chờ tới
    # lượt dùng hồ sơ thuộc về `_run_inner` (đã có hạn 120s và kết thúc bằng lý
    # do đọc được: "Hồ sơ đang bận — chưa tới lượt").
    #
    # Đo thật 09/08/2026 (google-nguoi-a): một lượt onboard ChatGPT đang giữ
    # hồ sơ, `close_profile` chờ vô hạn ngay tại đây nên handler
    # `/v1/session/auto-login-saved` KHÔNG BAO GIỜ trả lời — solver không ghi nổi
    # một dòng access log cho yêu cầu đó. Tầng T3 của auto-recovery hết hạn 30
    # giây, nuốt lỗi, rồi báo "không vào được ô mật khẩu (Google chặn)" cho một
    # trình duyệt chưa từng mở.
    try:
        await pool.close_profile(profile, cho_toi_da=_HAN_DON_CONTEXT_S)
    except HoSoDangBan:
        logger.info("auto_login: hồ sơ %s đang bận lúc dọn context — "
                    "để _run_inner chờ có hạn rồi báo lý do", profile)
    # await _nuke_profile(profile)  # REMOVED to preserve ChatGPT/Codex cache

    task = asyncio.create_task(_run(session, password))
    _tasks[profile] = task
    return session


# URL fragments that mean an interactive Google sign-in / challenge is still
# in progress. The persistent profile keeps __Secure-1PSID/SID cookies even
# after Google signs the account out server-side, so cookie presence ALONE is
# a false "logged in". If a live page is parked on one of these, a real
# re-login is required regardless of leftover cookies.
_SIGNIN_URL_MARKERS = (
    "/signin/identifier", "/signin/v2", "/signin/challenge", "/challenge/pwd",
    "/challenge/", "/signin/rejected", "/signin/selectchallenge",
    "accountchooser", "/speedbump", "servicelogin",
)


async def _already_logged_in(ctx) -> bool:
    try:
        cookies = await ctx.cookies()
        if not any(c["name"] in _GOOGLE_LOGIN_COOKIES for c in cookies):
            return False
        # Cookie presence ≠ live session. If any open page is sitting on a
        # Google sign-in / challenge URL, we are actively being asked to
        # re-authenticate → NOT logged in. Without this guard the leftover
        # __Secure-1PSID/SID made auto-login declare "đã đăng nhập sẵn" and
        # close the browser right on the password page (bug 2026-07-25:
        # Flow/Gemini recovery reported success without ever re-logging in).
        try:
            pages = list(ctx.pages)
        except Exception:
            pages = []
        for pg in pages:
            try:
                u = (pg.url or "").lower()
            except Exception:
                continue
            if "accounts.google.com" in u and any(m in u for m in _SIGNIN_URL_MARKERS):
                return False
        return True
    except Exception:
        return False


async def _detect_state(page) -> tuple[str, Optional[str]]:
    """Inspect the current Google sign-in page and classify it.

    Returns (state, extra_info) where state is one of:
      success, need_code, need_tap, error, working
    extra_info is the tap number when state=="need_tap"."""
    try:
        url = page.url
    except Exception:
        return "working", None

    # Success heuristic — Google redirects away from accounts.google.com
    # after sign-in, typically to myaccount.google.com or service URL.
    if "myaccount.google.com" in url or "google.com/accounts/Logout" in url:
        return "success", None
    if "accounts.google.com" not in url and "ServiceLogin" not in url:
        return "success", None

    # Generic error banner
    try:
        err_count = await page.locator(
            'div[jsname="B34EJ"], .Ekjuhf, .dEOOab'
        ).first.count()
        if err_count > 0:
            try:
                err_text = await page.locator(
                    'div[jsname="B34EJ"], .Ekjuhf, .dEOOab'
                ).first.inner_text(timeout=600)
                if err_text and len(err_text) < 220:
                    return "error", err_text.strip()
            except Exception:
                pass
    except Exception:
        pass

    # Code input visible?
    for sel in _2FA_CODE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=400):
                return "need_code", None
        except Exception:
            continue

    # Tap-match: page typically says "Check your phone" or has a samp
    # element with a number 0-99.
    try:
        body_text = (await page.locator("body").inner_text(timeout=600)).lower()
    except Exception:
        body_text = ""
    if any(k in body_text for k in (
        "check your phone", "kiểm tra điện thoại",
        "tap yes", "nhấn có", "trên thiết bị của bạn",
    )):
        # Try extracting the number
        for sel in _TAP_MATCH_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    txt = (await loc.inner_text(timeout=400)).strip()
                    if txt.isdigit() and 1 <= len(txt) <= 3:
                        return "need_tap", txt
            except Exception:
                continue
        return "need_tap", None

    return "working", None


async def _safe_click(page, *selectors, timeout: int = 2500) -> bool:
    for sel in selectors:
        try:
            await page.locator(sel).first.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def do_google_login_steps(
    session: LoginSession,
    page,
    ctx,
    password: str,
    prefer_method: str = "auth",
) -> bool:
    """Shared Google login email/password/2FA dance.

    Assumes `page` is already navigated to an accounts.google.com page
    (signin form OR an OAuth redirect target). Drives the form forward
    and handles 2FA prompts.

    `prefer_method` selects how to satisfy a 2FA challenge when Google
    offers a method picker:
      "auth"  — click the Authenticator option; flow advances to need_code
                and the user POSTs the 6-digit code via the existing
                /auto-login-2fa-code endpoint.
      "tap"   — skip the picker; let Google fall through to the
                tap-on-device prompt (need_tap). The user opens Gmail/
                Google app on their phone and taps "Yes, it's me".
      "phone" — click the phone-call / SMS option; Google calls or texts
                the enrolled number, then shows a code input (need_code).

    Updates `session.state` / `.message` / `.error` in-place. Returns
    True on success, False on failure. Caller decides what to do next
    (e.g. scrape session cookies, navigate elsewhere, etc).
    """
    if not password:
        session.state = "failed"
        session.error = "Không có mật khẩu để tự động đăng nhập (reuse_only=True hoặc thiếu dữ liệu)."
        session.completed_at = time.time()
        return False

    # KHÔNG có email thì DỪNG NGAY — không tồn tại lượt đăng nhập nào để thử.
    #
    # Email rỗng không làm vòng dưới hỏng một cách ồn ào; nó làm vòng dưới chạy
    # ĐỦ 420 giây một cách vô nghĩa: `includes("")` luôn đúng nên đoạn dò tile
    # bấm trúng phần tử đầu tiên gặp được, `press_sequentially("")` gõ đúng 0 ký
    # tự, rồi vẫn bấm "Tiếp theo" nên Google trả "Hãy nhập email hoặc số điện
    # thoại" trên một ô trống. Lặp bốn mươi lượt trong bảy phút, và ĐÓ mới là
    # thứ khiến Google bung reCAPTCHA — không phải tài khoản bị chặn.
    #
    # Đo thật 29/08/2026 (google-nguoi-c): "clicked account tile for
    #  on chooser screen" (email trống) 00:31:32 → "bấm lại vào mail lần 40"
    # 00:37:53, URL đứng im ở /v3/signin/identifier với cùng một `dsh=`.
    #
    # Gốc đã vá ở `main.py::bu_credential` (bù email cùng bản ghi với mật khẩu).
    # Chốt chặn ở đây để KHÔNG một người gọi nào — hôm nay hay mai sau — đẩy
    # được lượt đăng nhập rỗng vào trang Google lần nữa.
    if not (session.email or "").strip():
        session.state = "failed"
        session.error = ("Không có địa chỉ email để đăng nhập — hồ sơ chưa lưu tài khoản, "
                         "hoặc lượt gọi quên gửi email. Lưu tài khoản cho hồ sơ này rồi "
                         "chạy lại; KHÔNG thử đăng nhập với ô email trống.")
        session.completed_at = time.time()
        logger.warning("auto_login: %s — email rỗng, bỏ lượt thay vì bấm 'Tiếp theo' "
                       "trên ô trống (thứ làm Google bung captcha)", session.profile)
        return False
    
    async def _click_try_again() -> bool:
        # 1. Attribute-based reliable locators
        for _sel in (
            'a[href*="/restart"]',
            '[aria-label="Thử lại"]',
            '[aria-label="Try again"]',
            'div[jsname="LgbsSe"]:has-text("Thử lại")',
            'div[jsname="LgbsSe"]:has-text("Try again")',
            'button:has-text("Thử lại")',
            'button:has-text("Try again")'
        ):
            try:
                _loc = page.locator(_sel).first
                if await _loc.is_visible(timeout=500):
                    await _loc.click()
                    return True
            except Exception:
                continue
                
        # 2. Text-based exact matches (Playwright native)
        for _t in ("Thử lại", "Try again", "Thử đăng nhập lại"):
            try:
                _loc = page.locator(f'text="{_t}"').last
                if await _loc.is_visible(timeout=500):
                    await _loc.click()
                    return True
            except Exception:
                pass

        # 3. Fallback generic JS clicker that ignores empty text
        try:
            if await page.evaluate("""() => { 
                const a=document.querySelectorAll('*'); 
                for(const el of a){ 
                    if(!el.offsetParent) continue; 
                    if(el.tagName !== 'BUTTON' && el.tagName !== 'A' && el.tagName !== 'SPAN' && el.tagName !== 'DIV' && el.tagName !== 'INPUT') continue;
                    const t=(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').normalize('NFC').trim().toLowerCase(); 
                    if(t.includes('thử lại') || t.includes('try again') || t.includes('thử đăng nhập lại')){
                        el.click();return true;
                    } 
                } 
                return false; 
            }"""):
                return True
        except Exception:
            pass
        return False

    # ── Google block detection: check if Google served an error page ──
    # Wait out the transient "Đang tải" (loading) spinner first — checking too
    # early matches hidden/placeholder text and yields a false "blocked".
    _BLOCK_TEXTS = (
        "browser or app may not be secure",
        "trình duyệt hoặc ứng dụng này có thể không an toàn",
        "couldn't sign you in",
        "không thể đăng nhập",
    )
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    for _ in range(8):
        try:
            body = (await page.locator("body").inner_text(timeout=2000)).strip().lower()
        except Exception:
            body = ""
        if not body or body in ("đang tải", "đang tải...", "loading", "loading…", "loading..."):
            await asyncio.sleep(1.0)
            continue
        matched = next((k for k in _BLOCK_TEXTS if k in body), None)
        if matched:
            # KHÔNG fail ngay — BotGuard chập chờn. Vòng lặp email bên dưới sẽ
            # bấm 'Thử lại' + nhập lại mail liên tục đến khi lọt ô mật khẩu.
            logger.info("auto_login: BotGuard block at start (matched=%r) — will retry", matched)
        break

    # ── Handle Account Chooser ──
    # Google often shows the "Choose an account" screen instead of an email input
    # if the profile already has history.
    # Khởi tạo TRƯỚC try: page.evaluate lỗi (trang đang điều hướng) thì các nhánh
    # bên dưới vẫn đọc clicked_tile → NameError, cả lượt đăng nhập chết vì một
    # lỗi tạm thời.
    clicked_tile = False
    try:
        clicked_tile = await page.evaluate(
            """(em) => {
                const els = Array.from(document.querySelectorAll('div[data-identifier],li,div[role=link],div[role=button],a,div[jsname]'));
                for (const e of els) {
                    const id = (e.getAttribute('data-identifier')||'').toLowerCase();
                    if (id && id === em) { e.click(); return true; }
                }
                for (const e of els) {
                    if ((e.innerText||'').toLowerCase().includes(em)) { e.click(); return true; }
                }
                return false;
            }""", session.email.lower()
        )
        if clicked_tile:
            logger.info("auto_login: clicked account tile for %s on chooser screen", session.email)
            await asyncio.sleep(2.0)
    except Exception:
        pass

    # LUÔN dò ô mật khẩu THẬT, không suy từ việc đã bấm được tile.
    #
    # Bản cũ: `pwd_already = clicked_tile` — bấm được tile là TIN LUÔN rằng ô mật
    # khẩu đã hiện rồi điền mật khẩu vào đó. Nhưng bấm tile KHÔNG đảm bảo sang
    # được màn hình mật khẩu: Google có thể quay về màn chọn email, hoặc trang
    # còn đang tải. Đo thật 30/07 (nguoi.b@example.com):
    #     clicked account tile for nguoi.b@example.com on chooser screen
    #     password field assumed/present — skipping email step (SSO pick)
    #     after email, url=…/v3/signin/identifier?…        ← VẪN Ở BƯỚC EMAIL
    #     captcha detected for google-nguoi-b
    # Tức nó điền mật khẩu khi trang còn ở bước email; Google thấy hành vi lạ nên
    # bung captcha. Người gõ tay không bao giờ sai bước này nên KHÔNG bị captcha —
    # captcha là HẬU QUẢ của lỗi tự động, không phải Google chặn tài khoản.
    _PWD_SELECTORS = ('input[type="password"]', 'input[name="Passwd"]',
                      'input[autocomplete="current-password"]', 'input[name="password"]')

    async def _pwd_visible(timeout: int = 1000):
        for _sel in _PWD_SELECTORS:
            try:
                _loc = page.locator(_sel).first
                if await _loc.is_visible(timeout=timeout):
                    return _loc
            except Exception:
                continue
        return None

    async def _bam_lai_vao_mail() -> bool:
        """BẤM LẠI VÀO MAIL — đúng thao tác của lần đầu.

        THỨ TỰ QUAN TRỌNG: có Ô NHẬP email hiện ra thì ĐIỀN, đó là ưu tiên tuyệt
        đối. Chỉ khi KHÔNG có ô nhập mới đi tìm tile — vì màn "Chọn tài khoản"
        không có ô nhập, còn màn identifier thì có; hai màn đó loại trừ nhau.

        Bản cũ làm ngược: dò tile TRƯỚC, và nhánh dò thứ hai nhận bất kỳ phần tử
        nào có innerText CHỨA email, không hề kiểm phần tử đó có hiển thị không.
        Trên màn identifier vẫn có phần tử ẩn dính chuỗi email, nên nó "bấm"
        trúng một thứ vô hình, trả True, và nhánh điền email KHÔNG BAO GIỜ chạy.

        Đo thật 09/08/2026 (google-nguoi-a): log ghi "bấm lại vào
        mail lần 125" trong khi ảnh noVNC cho thấy ô email vẫn TRỐNG TRƠN. Vòng
        lặp quay đủ 420 giây rồi báo "không vào được ô mật khẩu (Google chặn
        hoặc đổi giao diện)" — đổ lỗi cho Google, trong khi chưa từng gõ nổi một
        ký tự. Vì tầng T3 của khôi phục nhiều tầng gọi đúng luồng này, mọi lượt
        tự khôi phục tài khoản Google đều trượt theo cùng một lý do sai.
        """
        try:
            for _sel in ('input[type="email"]', 'input#identifierId',
                         'input[name="identifier"]', 'input[autocomplete="username"]',
                         'input[autocomplete="email"]'):
                try:
                    el = page.locator(_sel).first
                    if await el.is_visible(timeout=500):
                        await el.fill("")
                        await el.press_sequentially(session.email, delay=50)
                        await asyncio.sleep(0.6)
                        # ĐỌC LẠI ô trước khi bấm "Tiếp theo". Gõ xong không
                        # đồng nghĩa giá trị đã nằm trong ô: trang có thể vừa
                        # render lại giữa chừng (mất sạch những gì đã gõ), hoặc
                        # `session.email` rỗng nên vừa gõ đúng 0 ký tự. Bấm
                        # "Tiếp theo" lúc đó là nộp form TRỐNG — Google trả
                        # "Hãy nhập email hoặc số điện thoại" và tính đó là một
                        # lượt nộp hỏng. Bốn mươi lượt như vậy trong bảy phút
                        # (đo 29/08/2026) là đủ để nó bung captcha.
                        try:
                            da_vao = (await el.input_value(timeout=1000) or "").strip()
                        except Exception:
                            da_vao = ""
                        if da_vao.lower() != (session.email or "").strip().lower():
                            logger.info("auto_login: ô email chưa nhận đủ chữ "
                                        "(đang là %r) — KHÔNG bấm Tiếp theo, thử lại vòng sau",
                                        da_vao[:40])
                            return False
                        if not await _safe_click(
                            page, '#identifierNext button', '#identifierNext',
                            'button:has-text("Next")', 'button:has-text("Tiếp theo")',
                            'span[jsname="V67aGc"]', 'button[jsname="LgbsSe"]:visible',
                            'div[role="button"]:has-text("Next")',
                            'div[role="button"]:has-text("Tiếp theo")',
                        ):
                            await page.evaluate("""() => {
                                const all = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                                for (const el of all) {
                                    if (!el.offsetParent) continue;
                                    const t = (el.innerText || '').trim().toLowerCase();
                                    if (t === 'next' || t === 'tiếp theo' || t === 'tiep theo') { el.click(); return; }
                                }
                            }""")
                        return True
                except Exception:
                    continue

            # Không có ô nhập → đang ở màn "Chọn tài khoản". Bấm đúng tile của
            # email này. BẮT BUỘC kiểm hiển thị: phần tử ẩn vẫn nhận `click()`
            # mà không xảy ra gì, và trả True là nuốt mất cả vòng thử.
            tile = await page.evaluate(
                """(em) => {
                    const hien = (e) => {
                        if (!e.offsetParent) return false;
                        const r = e.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    };
                    const els = Array.from(document.querySelectorAll(
                        'div[data-identifier],li,div[role=link],div[role=button],a,div[jsname]'));
                    for (const e of els) {
                        const id = (e.getAttribute('data-identifier')||'').toLowerCase();
                        if (id && id === em && hien(e)) { e.click(); return true; }
                    }
                    for (const e of els) {
                        if (!hien(e)) continue;
                        if ((e.innerText||'').toLowerCase().includes(em)) { e.click(); return true; }
                    }
                    return false;
                }""", session.email.lower()
            )
            if tile:
                return True
        except Exception:
            pass
        return False

    _CAPTCHA_SELECTORS = ('img#captchaimg', 'img[src*="Captcha"]', 'input[name="ca"]',
                          'input[aria-label*="văn bản" i]', 'input[aria-label*="hear" i]')

    # Trang THỬ THÁCH reCAPTCHA của Google — thứ mà các bộ chọn ảnh ở trên không
    # bao giờ thấy, vì captcha nằm trong iframe reCAPTCHA chứ không phải
    # `img#captchaimg` của captcha ảnh đời cũ.
    #
    # Đo thật 24/08/2026 (google-nguoi-b): phiên Flow chết, kiểm phiên rơi vào
    # `accounts.google.com/v3/signin/challenge/recaptcha`, rồi lượt đăng nhập lại
    # quay 135 vòng "bấm lại vào mail" trong 420 giây, đóng máy với lý do "Không
    # lọt được ô mật khẩu" — trong khi việc cần làm chỉ là một người gõ captcha.
    # Càng quay thì càng giống bot, tức càng khó thoát cái thử thách đó.
    #
    # Chỉ nhận ĐÚNG nhánh recaptcha: `/challenge/pwd` là màn mật khẩu bình
    # thường, `/challenge/totp` là 2FA — hai cái đó máy tự đi tiếp được.
    _CAPTCHA_URL_PATHS = ("/challenge/recaptcha",)
    # Tự tích ô xong, trang vẫn nằm ở `/challenge/recaptcha` thêm một lúc rồi
    # Google mới chuyển sang ô mật khẩu — đo 13/09/2026: 13:05:11,1 → 13:05:13,2
    # và 13:29:10,0 → 13:29:12,0, cả hai 2,0 giây. Còn thấy captcha trong khoảng
    # này KHÔNG có nghĩa là cần người; quá mốc này mà vẫn còn thì mới cần.
    _CHO_CHUYEN_TRANG_S = 10.0

    def _url_hien_tai() -> str:
        try:
            return (page.url or "").lower()
        except Exception:
            return ""

    # ── MỘT vòng kiên trì tới khi vào được ô mật khẩu ────────────────────────
    # Làm đúng như người dùng bấm "Chỉ đăng nhập" rồi ngồi thử: Google chặn thì
    # bấm "Thử lại", XONG BẤM LẠI VÀO MAIL như lần đầu, rồi xem đã có ô mật khẩu
    # chưa — lặp nhiều lần, BotGuard chập chờn nên thử đủ nhiều sẽ lọt.
    #
    # Bản trước chia làm hai vòng (email 90s rồi mật khẩu 300s) và ở nhánh bí thì
    # tự bấm "Sử dụng một tài khoản khác": vừa CẮT BỚT số lần thử, vừa thêm một
    # bước KHÔNG có trong luồng "Chỉ đăng nhập" — bấm cái đó là rời màn chọn tài
    # khoản nên không bao giờ bấm lại vào mail nữa.
    session.message = "Đang vào ô mật khẩu..."
    pwd_input = None
    captcha_flagged = False
    captcha_da_thu_tu_giai = False
    tu_qua_xong_luc = 0.0
    block_retries = 0
    tile_clicks = 0
    vong = 0
    url_hien = ""
    pwd_deadline = time.time() + _VAO_O_MAT_KHAU_S
    while time.time() < pwd_deadline:
        vong += 1

        # ĐÃ đăng nhập sẵn → XONG, đây là đích của "Chỉ đăng nhập" chứ không phải
        # lỗi: Google nhảy thẳng myaccount.google.com nên ô mật khẩu KHÔNG BAO
        # GIỜ hiện. Đo thật 30/07 (nguoi.d@example.com): trang đang ở
        # myaccount mà vẫn dò ô email tới hết giờ rồi báo "BotGuard chặn gắt",
        # kéo cả thang khôi phục Codex trượt theo.
        if await _already_logged_in(ctx):
            session.state = "success"
            session.message = "Google login OK (đã đăng nhập sẵn)"
            session.completed_at = time.time()
            logger.info("auto_login: đã đăng nhập sẵn (%s) — xong sau %d vòng",
                        session.profile, vong)
            return True

        pwd_input = await _pwd_visible(2000)
        if pwd_input is not None:
            logger.info("auto_login: vào được ô mật khẩu sau %d vòng "
                        "(Thử lại %d lần, bấm vào mail %d lần) — %s",
                        vong, block_retries, tile_clicks, session.profile)
            break

        url_hien = _url_hien_tai()
        # Nhận ra reCAPTCHA bằng CHÍNH KHUNG của nó, không chỉ bằng đường dẫn.
        #
        # `_CAPTCHA_URL_PATHS` là một danh sách đường dẫn — mà danh sách thì
        # luôn thiếu. Chủ máy dán ảnh 12/09/2026 lúc 19:07: trang "Xác minh
        # danh tính của bạn" hiện đúng hộp "Tôi không phải là người máy", nhưng
        # nếu Google không đặt nó ở `/challenge/recaptcha` thì cả nhánh tự giải
        # lẫn nhánh báo `need_captcha` đều KHÔNG chạy — ô nằm im, người dùng
        # ngồi đợi một thứ không bao giờ tới.
        #
        # `iframe[src*="/recaptcha/"]` là dấu hiệu bám vào chính thứ cần nhận:
        # widget nào của reCAPTCHA cũng nạp khung `api2/anchor` (và `bframe`
        # khi bung thử thách) — cũng đúng thứ `playwright_recaptcha` bám vào
        # (`anchor_frame`/`bframe_frame`). Đổi URL không làm hỏng dấu hiệu này.
        co_khung_recaptcha = False
        if not captcha_flagged:
            try:
                # `count()` TRẢ NGAY. `is_visible(timeout=800)` thì chờ đủ 800ms
                # mỗi vòng khi không có khung — vòng này vốn chỉ nghỉ 2s/lượt,
                # cộng thêm 0,8s là cắt mất ~40% số lần thử trong cùng ngân
                # sách, đúng thứ mà chú thích "BotGuard chập chờn nên thử đủ
                # nhiều sẽ lọt" ở dưới đang dựa vào.
                co_khung_recaptcha = await page.locator(
                    'iframe[src*="/recaptcha/"]').count() > 0
            except Exception:
                co_khung_recaptcha = False
        # Thấy captcha thì CHƯA gọi người: `need_captcha` nghĩa là "phải có người
        # gõ trên noVNC", mà máy còn chưa thử. Bản trước đặt nó ngay ở đây, trước
        # cả lần tự tích ô — nên trong 4–6 giây máy đang tích, bên khôi phục
        # (`account_recovery._freshen_google`, hỏi 5 giây một lần) đọc được đúng
        # lúc đó, bỏ cuộc, khoá MỌI lượt đăng nhập Google 6 giờ và nhắn "chờ bạn
        # giải captcha" — dù ô đã tự tích xong ngay sau. Đo 13/09/2026 13:05:
        # ba hồ sơ Flow bị hoãn vì thế và nằm chết tới khi có người chạy tay.
        if not captcha_flagged and (
                co_khung_recaptcha or any(p in url_hien for p in _CAPTCHA_URL_PATHS)):
            session.message = "Google bắt xác minh reCAPTCHA — đang thử tự qua..."
            logger.info("auto_login: reCAPTCHA challenge cho %s (url=%s) — thử tự qua "
                        "trước khi gọi người", session.profile, url_hien)
            captcha_flagged = True

        try:
            body = (await page.locator("body").inner_text(timeout=1500)).strip().lower()
        except Exception:
            body = ""
        if body in ("", "đang tải", "đang tải...", "loading", "loading…", "loading..."):
            await asyncio.sleep(1.5)
            continue

        # Captcha: người gõ trên noVNC → KHÔNG bấm/điền gì thêm kẻo phá trang của
        # họ. Vòng vẫn quay để bắt ô mật khẩu ngay khi captcha xong.
        if not captcha_flagged:
            for _csel in _CAPTCHA_SELECTORS:
                try:
                    if await page.locator(_csel).first.is_visible(timeout=800):
                        session.message = "Google yêu cầu captcha — đang thử tự qua..."
                        logger.info("auto_login: captcha detected for %s — thử tự qua "
                                    "trước khi gọi người", session.profile)
                        captcha_flagged = True
                        break
                except Exception:
                    continue
        if captcha_flagged:
            # THỬ TỰ GIẢI MỘT LẦN trước khi làm phiền người. Trước 12/09/2026
            # nhánh này chỉ `sleep` rồi chờ người gõ trên noVNC — mà chủ máy
            # đúng là mắc ở đó nên không vào nổi ô mật khẩu.
            #
            # Chỉ thử ĐÚNG MỘT LẦN: hỏng thì quay lại đường cũ (người gõ tay),
            # không thử vòng vòng — càng đấm nhiều càng giống bot, chính là thứ
            # làm Google siết thêm (xem chú thích ở `_CAPTCHA_URL_PATHS`).
            if not captcha_da_thu_tu_giai:
                captcha_da_thu_tu_giai = True
                try:
                    from .solvers.recaptcha import (
                        solve_recaptcha_v2_tren_trang, tich_o_recaptcha,
                    )
                    # BƯỚC 1 — tự bấm ô, y như người dùng bấm. Rẻ, nhanh, và
                    # không phụ thuộc ngôn ngữ giao diện. Rất nhiều lượt Google
                    # chấp nhận ngay cú bấm mà không bung thử thách nào.
                    session.message = "Đang tự tích ô \"Tôi không phải là người máy\"..."
                    logger.info("auto_login: thử tự tích ô reCAPTCHA cho %s", session.profile)
                    if await tich_o_recaptcha(page):
                        session.state = "running"
                        session.message = "Đã tự tích ô — đang vào ô mật khẩu..."
                        logger.info("auto_login: tự tích ô reCAPTCHA XONG cho %s",
                                    session.profile)
                        captcha_flagged = False
                        tu_qua_xong_luc = time.time()
                        continue
                    # BƯỚC 2 — Google bung thử thách. Thử đường âm thanh.
                    # W2A: Cổng kiểm tra nhóm tính năng giai_captcha trước khi gọi bộ giải âm thanh (tránh gửi âm thanh khi tính năng tắt).
                    from .settings import lay_cac_nhom_tinh_nang_bat
                    if "giai_captcha" not in lay_cac_nhom_tinh_nang_bat():
                        logger.info(
                            "auto_login: nhóm tính năng giai_captcha đang TẮT cho %s — không tự giải reCAPTCHA bằng âm thanh, nhường người giải trên noVNC",
                            session.profile,
                        )
                        session.state = "need_captcha"
                        session.message = (
                            "Không tự qua được reCAPTCHA (tính năng tự giải đang tắt) — "
                            "gõ trên noVNC, hệ thống sẽ TỰ tiếp tục password+2FA"
                        )
                        continue

                    session.message = "Đang tự giải reCAPTCHA (thử thách âm thanh)..."
                    logger.info("auto_login: thử tự giải reCAPTCHA cho %s", session.profile)
                    await solve_recaptcha_v2_tren_trang(page)
                    session.state = "running"
                    session.message = "Đã tự giải reCAPTCHA — đang vào ô mật khẩu..."
                    logger.info("auto_login: tự giải reCAPTCHA XONG cho %s", session.profile)
                    captcha_flagged = False
                    tu_qua_xong_luc = time.time()
                    continue
                except Exception as exc:
                    # Nói RÕ vì sao, rồi nhường lại cho người — im lặng ở đây là
                    # chủ máy ngồi đợi một thứ không bao giờ tới.
                    # Nói rõ HỎNG Ở BƯỚC NÀO: nhánh này bắt cả bước tích ô lẫn
                    # bước giải âm thanh, ghi chung một câu "không giải được"
                    # là chỉ sai chỗ cho người đọc log lần sau.
                    logger.info("auto_login: không tự qua được reCAPTCHA "
                                "(tích ô hoặc giải âm thanh) cho %s (%s: %s) — "
                                "nhường người gõ trên noVNC",
                                session.profile, type(exc).__name__, str(exc)[:160])
                    session.state = "need_captcha"
                    session.message = ("Không tự qua được reCAPTCHA — gõ trên noVNC, "
                                       "hệ thống sẽ TỰ tiếp tục password+2FA")
            elif (session.state != "need_captcha"
                  and time.time() - tu_qua_xong_luc >= _CHO_CHUYEN_TRANG_S):
                # Đã tự qua một lần mà quá lúc trang lẽ ra phải chuyển vẫn còn
                # captcha → lúc này mới thật sự cần người.
                session.state = "need_captcha"
                session.message = ("Google vẫn bắt captcha sau khi đã tự qua một lần — "
                                   "gõ trên noVNC, hệ thống sẽ TỰ tiếp tục password+2FA")
                logger.info("auto_login: captcha vẫn còn %ds sau lần tự qua cho %s "
                            "(url=%s) — chờ người giải", int(_CHO_CHUYEN_TRANG_S),
                            session.profile, url_hien)
            await asyncio.sleep(2.0)
            continue

        # Google chặn ("trình duyệt không an toàn" / "không thể đăng nhập") →
        # bấm "Thử lại" trước, rồi bấm lại vào mail ở dưới.
        if any(b in body for b in _BLOCK_TEXTS):
            block_retries += 1
            session.state = "running"
            session.message = f"Google chặn — bấm Thử lại lần {block_retries}..."
            # PHẢI ra logger: session.message chỉ ai poll status mới thấy; thất
            # bại câm lặng là thứ đã làm mất 20 phút chẩn đoán.
            if block_retries == 1 or block_retries % 5 == 0:
                logger.info("auto_login: Google chặn — bấm Thử lại lần %d (%s, url=%s)",
                            block_retries, session.profile, url_hien)
            await _click_try_again()
            await asyncio.sleep(2.0)

        # BẤM LẠI VÀO MAIL — mỗi vòng, không giãn nhịp.
        if await _bam_lai_vao_mail():
            tile_clicks += 1
            session.message = (f"Đã bấm lại vào mail lần {tile_clicks} "
                               f"— chờ ô mật khẩu...")
            if tile_clicks == 1 or tile_clicks % 5 == 0:
                logger.info("auto_login: bấm lại vào mail lần %d (%s, url=%s)",
                            tile_clicks, session.profile, url_hien)
        await asyncio.sleep(2.0)
    if pwd_input is None:
        session.state = "failed"
        session.error = (
            f"Không lọt được ô mật khẩu sau {int(_VAO_O_MAT_KHAU_S)}s / {vong} vòng "
            f"(bấm Thử lại {block_retries} lần, bấm lại vào mail {tile_clicks} lần"
            f"{', captcha chưa ai giải' if captcha_flagged else ''}, url={url_hien or '?'})"
        )
        session.completed_at = time.time()
        return False
    try:
        session.state = "running"
        session.message = "Điền mật khẩu..."
        await asyncio.sleep(0.6)
        await pwd_input.fill("")
        await pwd_input.press_sequentially(password, delay=40)
        await asyncio.sleep(0.6)
        clicked = await _safe_click(
            page, 
            '#passwordNext button', 
            '#passwordNext',
            'button:has-text("Next")',
            'button:has-text("Tiếp theo")',
            'span[jsname="V67aGc"]', 
            'button[jsname="LgbsSe"]:visible',
            'div[role="button"]:has-text("Next")',
            'div[role="button"]:has-text("Tiếp theo")'
        )
        if not clicked:
            await page.evaluate("""() => {
                const all = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                for (const el of all) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t === 'next' || t === 'tiếp theo' || t === 'tiep theo') {
                        el.click();
                        return;
                    }
                }
            }""")
    except Exception as exc:
        session.state = "failed"
        session.error = f"Không điền được mật khẩu: {exc}"
        session.completed_at = time.time()
        return False

    # ── 2FA poll loop ──
    deadline = time.time() + 240
    picker_clicked = False
    # Vòng này TRƯỚC 12/09/2026 không ghi lại thứ nó nhìn thấy. Lượt hỏng
    # 21:35–21:39 vì thế chỉ để lại bốn phút log "method-picker ..." giống hệt
    # nhau: không biết trang nào đang mở, không biết còn dòng chọn nào không.
    # Mọi kiểu hỏng trông y như nhau — đúng lớp lỗi đã sửa ở `tich_o_recaptcha`.
    dau_van_truoc = None
    duong_trang = ""
    so_dong_chon = 0
    co_khung_captcha = False
    captcha_sau_mk_da_thu = False
    while time.time() < deadline:
        await asyncio.sleep(2.0)

        # Chụp trang MỘT LẦN mỗi vòng. Ghi ĐƯỜNG DẪN, bỏ query: query mang
        # `TL=` là mã phiên, không được rơi vào log.
        try:
            duong_trang = urlparse(page.url or "").path
        except Exception:
            duong_trang = ""
        try:
            so_dong_chon = await page.locator(
                'li[data-challengetype],div[data-challengetype],'
                'div[role="link"],li').count()
        except Exception:
            so_dong_chon = 0
        try:
            co_khung_captcha = await page.locator(
                'iframe[src*="/recaptcha/"]').count() > 0
        except Exception:
            co_khung_captcha = False
        dau_van = (duong_trang, so_dong_chon, co_khung_captcha)
        if dau_van != dau_van_truoc:
            logger.info("auto_login: vòng 2FA — trang=%s, %d dòng chọn, "
                        "khung reCAPTCHA=%s", duong_trang or "?",
                        so_dong_chon, co_khung_captcha)
            dau_van_truoc = dau_van

        # ĐÍCH TRƯỚC, DÒ SAU — đúng nếp vòng trước mật khẩu. Kiểm "đã đăng nhập"
        # nằm ở `_detect_state` CUỐI vòng, sau khối dò bảng chọn cách xác minh;
        # mà trang ứng dụng Google (Gemini) có sẵn nhiều `li` và chữ "Tùy chọn
        # khác", nên khối dò cứ bấm rồi `continue`, không bao giờ tới chỗ kiểm.
        # Đo 13/09/2026 13:29 (nguoi-a): đã vào tới trang `/` của Gemini, quay
        # đủ 4 phút rồi báo "Hết 4 phút mà chưa hoàn tất 2FA" — trong khi phiên
        # Flow kiểm lại ngay sau đó là `ok`.
        if await _already_logged_in(ctx):
            session.message = "Google login OK"
            logger.info("auto_login: vòng 2FA — đã đăng nhập (trang=%s) cho %s",
                        duong_trang or "?", session.profile)
            return True

        # Google bung reCAPTCHA SAU mật khẩu. Vòng trước-mật-khẩu có hẳn một
        # nhánh cho `/challenge/recaptcha`; vòng này thì KHÔNG có nhánh nào,
        # nên gặp captcha là quay đủ 240 giây rồi chết với lý do "Hết 4 phút
        # mà chưa hoàn tất 2FA" — người đọc không hề biết là vướng captcha.
        #
        # Bấm ô đúng MỘT lần rồi thôi: hàm này đã tích được thật lúc 21:34
        # cùng ngày, còn đấm vòng vòng chỉ càng giống bot.
        if co_khung_captcha and not captcha_sau_mk_da_thu:
            captcha_sau_mk_da_thu = True
            try:
                from .solvers.recaptcha import tich_o_recaptcha
                # KHÔNG đặt `need_captcha` trước khi thử — cùng lý do với vòng
                # trước mật khẩu (xem chú thích ở chỗ nhận ra reCAPTCHA): bên
                # khôi phục đọc được trạng thái đó giữa lúc máy đang tích là bỏ
                # cuộc và khoá mọi lượt đăng nhập 6 giờ.
                session.message = ("Google bắt reCAPTCHA sau mật khẩu — "
                                   "đang tự tích ô...")
                logger.info("auto_login: reCAPTCHA SAU mật khẩu cho %s "
                            "(trang=%s) — thử tự tích ô",
                            session.profile, duong_trang or "?")
                if await tich_o_recaptcha(page):
                    session.state = "running"
                    session.message = "Đã tự tích ô — đang chờ bước 2FA..."
                    logger.info("auto_login: tự tích ô reCAPTCHA sau mật khẩu "
                                "XONG cho %s", session.profile)
                    await asyncio.sleep(2.0)
                    continue
            except Exception as exc:
                logger.info("auto_login: tự tích ô sau mật khẩu hỏng cho %s "
                            "(%s: %s)", session.profile,
                            type(exc).__name__, str(exc)[:160])
            session.state = "need_captcha"
            session.message = ("Google bắt reCAPTCHA sau mật khẩu — gõ trên "
                               "noVNC, hệ thống sẽ TỰ tiếp tục 2FA")
            await asyncio.sleep(2.0)
            continue

        # 1. If Google shows the method picker, pick the method automatically
        # KHÔNG dòng chọn nào thì KHÔNG có bảng chọn. Gọi ba hàm dò bên dưới
        # khi ấy vừa tốn ~15 giây mỗi vòng cho những bộ chọn chắc chắn trượt,
        # vừa đẻ ra ba dòng log nói "picker visible" mà chẳng ai kiểm.
        if not picker_clicked and so_dong_chon > 0:
            try:
                if session.totp_secret and _HAS_PYOTP:
                    if await _pick_authenticator_method(page):
                        picker_clicked = True
                        session.message = "Phát hiện mã TOTP tự động, đã chọn Google Authenticator..."
                        await asyncio.sleep(1.5)
                        continue
                
                if session.prefer_method == "phone":
                    if await _pick_phone_method(page):
                        picker_clicked = True
                        session.message = "Đã chọn xác minh qua số điện thoại, đang chờ code..."
                        await asyncio.sleep(1.5)
                        continue
                elif session.prefer_method == "tap":
                    if await _pick_tap_method(page):
                        picker_clicked = True
                        session.message = "Đã chọn Nhấn Có trên điện thoại..."
                        await asyncio.sleep(1.5)
                        continue
                else: # default to auth
                    if await _pick_authenticator_method(page):
                        picker_clicked = True
                        session.message = "Đã chọn Google Authenticator, đang chờ code..."
                        await asyncio.sleep(1.5)
                        continue
                    elif await _pick_tap_method(page):
                        picker_clicked = True
                        session.message = "Không có Authenticator, đã chọn Nhấn Có trên điện thoại..."
                        await asyncio.sleep(1.5)
                        continue
            except Exception:
                pass

        # 2. Expand "Try another way" if needed
        if not picker_clicked:
            clicked_other = await _safe_click(
                page,
                'button:has-text("Thử cách khác")', 'button:has-text("Try another way")',
                'div[role="button"]:has-text("Thử cách khác")', 'div[role="button"]:has-text("Try another way")',
                'span[jsname="V67aGc"]:has-text("Thử cách khác")', 'span[jsname="V67aGc"]:has-text("Try another way")'
            )
            if not clicked_other:
                try:
                    clicked_other = await page.evaluate(r"""() => {
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            let t = (el.innerText || el.textContent || '').normalize('NFC').trim().toLowerCase();
                            t = t.replace(/\s+/g, ' ');
                            if (t.includes('thử cách khác') || t.includes('try another way') || t.includes('chọn cách khác') || t.includes('tùy chọn khác')) {
                                const target = el.closest('button, a, div[role="button"]') || el;
                                target.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                except Exception:
                    pass
            
            if clicked_other:
                logger.info("auto_login: clicked 'Try another way' button to open method picker")
                await asyncio.sleep(2.0)
                continue

        state, info = await _detect_state(page)

        if state == "success":
            if await _already_logged_in(ctx):
                session.message = "Google login OK"
                return True
            continue

        if state == "error":
            session.state = "failed"
            session.error = info or "Google báo lỗi"
            session.completed_at = time.time()
            return False

        if state == "need_tap":
            session.state = "need_tap"
            session.tap_number = info
            session.message = (
                f"Bấm số {info} trên điện thoại"
                if info else
                "Mở app Gmail/Google trên điện thoại và bấm 'Có' để xác minh"
            )
            continue

        if state == "need_code":
            # Có TOTP thì MÁY tự sinh và điền mã — chưa cần người. Cùng lớp với
            # `need_captcha` đặt trước lúc tự tích ô: `need_code` nằm trong
            # `account_recovery._CAN_NGUOI`, bên khôi phục đọc được giữa lúc máy
            # đang điền là bỏ cuộc và nhắn chủ máy "chờ bạn xác nhận 2FA".
            if not (session.totp_secret and _HAS_PYOTP):
                session.state = "need_code"
            if not picker_clicked:
                has_other = False
                for _sel in (
                    'button:has-text("Thử cách khác")', 'button:has-text("Try another way")',
                    'div[role="button"]:has-text("Thử cách khác")', 'div[role="button"]:has-text("Try another way")',
                    'span[jsname="V67aGc"]:has-text("Thử cách khác")', 'span[jsname="V67aGc"]:has-text("Try another way")'
                ):
                    try:
                        _loc = page.locator(_sel).first
                        if await _loc.is_visible(timeout=500):
                            has_other = True
                            break
                    except Exception:
                        pass
                if not has_other:
                    try:
                        has_other = await page.evaluate(r"""() => {
                            const all = document.querySelectorAll('*');
                            for (const el of all) {
                                let t = (el.innerText || el.textContent || '').normalize('NFC').trim().toLowerCase();
                                t = t.replace(/\s+/g, ' ');
                                if (t.includes('thử cách khác') || t.includes('try another way') || t.includes('chọn cách khác') || t.includes('tùy chọn khác')) return true;
                            }
                            return false;
                        }""")
                    except Exception:
                        pass
                
                if not has_other:
                    try:
                        body_text = await page.evaluate("() => document.body.innerText")
                        logger.error("auto_login: 'Thử cách khác' NOT FOUND in need_code! Body text: %r", (body_text or "")[:1000].replace('\n', ' '))
                    except Exception:
                        pass
                
                if has_other:
                    session.message = "Google yêu cầu Security Code, bỏ qua để bấm Thử cách khác..."
                    await asyncio.sleep(1.0)
                    continue

            if session.totp_secret and _HAS_PYOTP:
                secret = session.totp_secret.replace(' ', '')
                code = pyotp.TOTP(secret).now()
                session.message = "Da tu sinh ma 2FA"
                logger.info("auto_login: TOTP auto for %s code=%s", session.profile, code)
                await asyncio.sleep(1.0)
            else:
                session.message = "Can ma 2FA"
                code_deadline = time.time() + 180
                while time.time() < code_deadline and not session.pending_code:
                    await asyncio.sleep(1.0)
                    if await _already_logged_in(ctx):
                        session.state = "success"
                        session.message = "Google login OK"
                        return True
                    if not picker_clicked:
                        try:
                            has_other = await page.evaluate("""() => {
                                const btns = Array.from(document.querySelectorAll('button, div[role="button"], span[role="button"], a, li'));
                                for (const b of btns) {
                                    if (!b.offsetParent) continue;
                                    const t = (b.innerText || '').trim().toLowerCase();
                                    if (t.includes('thử cách khác') || t.includes('try another way')) return true;
                                }
                                return false;
                            }""")
                            if has_other:
                                break
                        except Exception:
                            pass
                if not session.pending_code:
                    if await _already_logged_in(ctx):
                        session.state = "success"
                        session.message = "Google login OK"
                        return True
                    session.state = "failed"
                    session.error = "Khong nhan duoc ma 2FA trong 3 phut"
                    session.completed_at = time.time()
                    return False
                code = session.pending_code
                session.pending_code = None
            # Fill code with fill() for reliability on input[type=tel]
            filled = False
            for sel in _2FA_CODE_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=2000)
                        await loc.fill('')
                        await asyncio.sleep(random.uniform(0.3, 0.6))
                        await loc.fill(code)
                        await asyncio.sleep(0.3)
                        await loc.press("Enter")
                        try:
                            actual = await loc.input_value()
                            logger.info("auto_login: typed=%s actual=%s via selector=%s", code, actual, sel)
                        except Exception:
                            logger.info("auto_login: filled 2FA code via selector=%s", sel)
                        filled = True
                        break
                except Exception:
                    continue
            if not filled:
                logger.warning("auto_login: could not fill 2FA code with any selector")
            await asyncio.sleep(0.5)
            clicked = await _safe_click(
                page,
                'button:has-text("Next")', 'button:has-text("Tiếp theo")',
                'span[jsname="V67aGc"]',
                '#totpNext button', '#submit',
                'button[jsname="LgbsSe"]:visible',
            )
            if not clicked:
                await page.evaluate("""() => {
                    const all = document.querySelectorAll('button, div[role="button"], span[role="button"]');
                    for (const el of all) {
                        if (!el.offsetParent) continue;
                        const t = (el.innerText || '').trim().toLowerCase();
                        if (t.includes('tiếp theo') || t.includes('next')) { el.click(); return true; }
                    }
                    return false;
                }""")
            session.state = "running"
            session.message = "Đã gửi mã, đang xác minh..."
            await asyncio.sleep(5.0)
            continue

    session.state = "failed"
    # Nói ĐANG Ở TRANG NÀO. Bản cũ chỉ có "Hết 4 phút mà chưa hoàn tất 2FA",
    # nên lượt 12/09/2026 21:39 không cho biết gì ngoài việc nó đã hết giờ.
    session.error = (
        f"Hết 4 phút mà chưa hoàn tất 2FA (trang={duong_trang or '?'}, "
        f"{so_dong_chon} dòng chọn, "
        f"khung reCAPTCHA={'có' if co_khung_captcha else 'không'})"
    )
    session.completed_at = time.time()
    return False


async def _run(session: LoginSession, password: str) -> None:
    # Onboard FAIL or SUCCESS -> dong browser ngay de khoi dot CPU tren Xvfb, luu cache.
    # CancelledError (login moi chiem profile) propagate -> KHONG close (ne race).
    # Đánh dấu hồ sơ đang đăng nhập: việc khác (tạo ảnh/video Flow) gọi
    # close_profile sẽ BỎ QUA, không đóng trình duyệt giữa lúc đang mở
    # trang đăng nhập (nếu đóng thì page.goto chết với net::ERR_ABORTED).
    pool.dau_dang_nhap(session.profile)
    try:
        await _run_inner(session, password)
    finally:
        pool.xong_dang_nhap(session.profile)
    if session.state in ("failed", "success"):
        try:
            # Lượt của MÌNH đã xong (cờ vừa được gỡ ở trên). Cờ còn bật nghĩa là
            # một lượt đăng nhập KHÁC đang chạy trên cùng hồ sơ — đừng đóng.
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
            logger.info("closed browser after onboard profile=%s state=%s", session.profile, session.state)
        except Exception:
            logger.debug("close_profile after onboard skipped", exc_info=True)

async def _run_inner(session: LoginSession, password: str) -> None:
    """Playwright orchestration for accounts.google.com direct login.
    Updates session.state in-place; UI polls /v1/session/{profile}/
    auto-login-status to see progress."""
    try:
        session.state = "starting"
        session.message = "Đang mở Chrome (headful → noVNC)"
        # KHÔNG force_recreate để giữ lại cache/cookie của các dịch vụ khác (ChatGPT, Codex)
        #
        # CÓ HẠN CHỜ: mỗi hồ sơ chỉ một việc dùng trình duyệt tại một thời điểm.
        # Không đặt hạn thì lượt đăng nhập nằm im vô hạn khi hồ sơ đang bận, mà
        # giao diện vẫn hiện "Đang đăng nhập…" — đo thật 09/08/2026: kẹt 366
        # giây vì một lượt khôi phục ChatGPT đang giữ cùng hồ sơ. 120 giây đủ
        # cho một lượt Flow/onboard bình thường nhả ra, mà không bắt người dùng
        # nhìn màn hình chết.
        try:
            ctx = await pool.get(profile=session.profile, headless=False,
                                 force_recreate=False, cho_toi_da=120.0)
        except HoSoDangBan as exc:
            session.state = "failed"
            session.message = "Hồ sơ đang bận — chưa tới lượt"
            session.error = (f"{exc} — việc khác (khôi phục ChatGPT / tạo ảnh Flow) "
                             f"đang dùng trình duyệt của hồ sơ này. Thử lại sau.")
            session.completed_at = time.time()
            logger.warning("auto_login: bỏ lượt vì hồ sơ bận profile=%s", session.profile)
            return

        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        session.state = "running"
        session.message = "Mở trang accounts.google.com (qua trang chủ Google)..."
        navigated = False
        try:
            await page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2.0)
            
            clicked = await page.evaluate("""() => {
                const links = document.querySelectorAll('a');
                for (const a of links) {
                    if (a.href && a.href.includes('accounts.google.com/ServiceLogin')) {
                        a.click();
                        return true;
                    }
                }
                return false;
            }""")
            
            if clicked:
                try:
                    await page.wait_for_url("**/accounts.google.com/**", timeout=10000)
                    navigated = True
                except Exception:
                    pass
        except Exception:
            pass
            
        if not navigated:
            await page.goto(_GOOGLE_SIGNIN_URL, wait_until="domcontentloaded", timeout=30_000)

        ok = await do_google_login_steps(session, page, ctx, password)
        if ok:
            session.state = "success"
            session.message = "Đăng nhập thành công"
            session.completed_at = time.time()

    except asyncio.CancelledError:
        session.state = "failed"
        session.error = "Bị huỷ (có yêu cầu auto-login mới)"
        session.completed_at = time.time()
        raise
    except Exception as exc:
        logger.exception("auto-login crashed profile=%s", session.profile)
        session.state = "failed"
        session.error = str(exc)
        session.completed_at = time.time()
