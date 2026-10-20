# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/chatgpt_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""ChatGPT onboard via Google OAuth.

Flow: chatgpt.com → click Login → click "Continue with Google"
→ Google OAuth (email/password/2FA) → redirect back to chatgpt.com
→ scrape JWT access_token.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from .browser_pool import HoSoDangBan, pool
from .auto_login import click_google_oauth_consent

try:
    import pyotp
    _HAS_PYOTP = True
except ImportError:
    pyotp = None  # type: ignore
    _HAS_PYOTP = False

logger = logging.getLogger(__name__)

_CHATGPT_URL = "https://chatgpt.com/"
_CHATGPT_LOGIN_URL = "https://chatgpt.com/auth/login"
# Auth0 universal login for ChatGPT — bypasses Cloudflare on chatgpt.com
_AUTH0_LOGIN_URL = "https://auth.openai.com/u/login"
_AUTH_OPENAI = "https://auth.openai.com/"

# Selectors for "Continue with Google" on auth.openai.com
_GOOGLE_BTN_SELECTORS = (
    'button[data-provider="google"]',
    'button:has-text("Continue with Google")',
    'button:has-text("Tiếp tục với Google")',
    'a[href*="accounts.google.com"]',
    'button[aria-label*="Google"]',
    'form[action*="accounts.google.com"] button',
    # Auth0 / social-login button patterns (used by OpenAI)
    'button[class*="google"]',
    'button[class*="Google"]',
    'div[class*="google"][role="button"]',
    'div[class*="Google"][role="button"]',
    'button[class*="social"]',
    'div[class*="social"]',
    # Direct match on any element whose text starts/contains Google
    '*:has-text("Continue with Google"):not(html):not(body)',
    '*:has-text("Tiếp tục với Google"):not(html):not(body)',
    # Links that look like Google OAuth redirects
    'a[href*="accounts.google.com/o/oauth2"]',
    'a[href*="accounts.google.com/signin/oauth"]',
    # Generic: any button inside a form pointing at Google
    'form[action*="google"] button',
    'form[action*="google"] input[type="submit"]',
)

# Selectors for Google 2FA code input
_2FA_CODE_SELECTORS = (
    'input[type="tel"][autocomplete="one-time-code"]',
    'input[name="totpPin"]',
    'input[id="totpPin"]',
    'input[type="tel"]:not([disabled])',
)

# Cookies that prove a Google login completed
_GOOGLE_LOGIN_COOKIES = ("__Secure-1PSID", "__Secure-3PSID", "SID")


async def _type_human_like(locator, text: str) -> None:
    """Type text character-by-character with randomized delays."""
    # Random pause before starting (human looks at phone)
    await asyncio.sleep(random.uniform(0.4, 1.2))
    for i, ch in enumerate(text):
        await locator.press(ch, delay=random.randint(80, 350))
        # Occasionally pause mid-code
        if i == 2 and random.random() < 0.6:
            await asyncio.sleep(random.uniform(0.2, 0.6))


async def _safe_click(page, *selectors: str, timeout: int = 5000) -> bool:
    """Try each selector; click the first visible match. Returns True on success."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


# Selectors ONLY for Authenticator/TOTP — do NOT match SMS/phone.
# Google's 2FA method picker uses div.l5PPKe[jsname="fmcmS"] inside an <li>.
# We target the clickable element that CONTAINS "Google Authenticator" text.
_AUTHENTICATOR_OPTION_SELECTORS = (
    'li[data-challengetype="9"]',
    'div[data-challengetype="9"]',
    # Direct match for the Google Authenticator option div
    'div.l5PPKe:has(strong:has-text("Google Authenticator"))',
    'div[jsname="fmcmS"]:has(strong:has-text("Google Authenticator"))',
    # Fallback: any li containing Google Authenticator text
    'li:has(strong:has-text("Google Authenticator"))',
    'li:has(div:has-text("Google Authenticator"))',
    # Click parent li of the option div
    'li:has(div.l5PPKe:has-text("Google Authenticator"))',
)

_METHOD_SELECTOR_HINTS = (
    "choose how you", "chọn cách",
    "try another way", "thử cách khác",
    "get a verification code", "nhập mã xác minh",
    "google authenticator", "authenticator",
    "ứng dụng xác thực",
)


async def _pick_authenticator_method(page) -> bool:
    """When Google shows the method picker, click the Authenticator entry.
    NEVER clicks phone/SMS options — only Authenticator."""
    try:
        body_text = (await page.locator("body").inner_text(timeout=600)).lower()
    except Exception:
        return False

    # Broad check: are we on a 2FA-related page? Google v3 shows various
    # texts like "Verify it's you", "Xác minh danh tính", "2-Step Verification",
    # "Choose how you'll sign in", or the phone prompt itself.
    _2FA_PAGE_HINTS = _METHOD_SELECTOR_HINTS + (
        "verify it's you", "xác minh danh tính", "xac minh danh tinh",
        "2-step verification", "xác minh 2 bước",
        "google prompt", "gửi lời nhắc",
        "open your google app", "mở ứng dụng google",
        "tap yes", "nhấn có",
        "unlock your phone", "mở khóa điện thoại",
        "more ways to verify", "thêm cách xác minh",
        "verify your identity", "xác minh danh tính của bạn",
    )
    is_2fa_page = any(h in body_text for h in _2FA_PAGE_HINTS)

    # Also check URL for 2FA paths
    try:
        url = page.url or ""
        is_2fa_page = is_2fa_page or "challenge" in url or "signin/v2/challenge" in url
    except Exception:
        pass

    if not is_2fa_page:
        return False

    # Log body and li elements for debugging
    logger.info("chatgpt_login: 2FA page detected, body_snippet=%s", body_text[:250])
    try:
        all_lis = page.locator('ul li, div[role="list"] li, section li, li')
        lic = await all_lis.count()
        if lic > 0:
            li_texts = []
            for i in range(min(lic, 10)):
                try:
                    txt = (await all_lis.nth(i).inner_text(timeout=300))[:50]
                    if txt.strip():
                        li_texts.append(txt.strip())
                except Exception:
                    pass
            logger.info("chatgpt_login: first %d li texts: %s", min(lic, 10), li_texts)
    except Exception:
        pass

    # Strategy: find ANY element that contains "Google Authenticator" text and click it
    # Going from most specific to least specific
    authenticator_selectors = (
        'li:has(strong:text-is("Google Authenticator"))',
        'li:has(strong:has-text("Google Authenticator"))',
        'div[jsname="fmcmS"]:has(strong:has-text("Google Authenticator"))',
        'div.l5PPKe:has(strong:has-text("Google Authenticator"))',
        'div[jsname="fmcmS"]:has-text("Google Authenticator")',
        'div.l5PPKe:has-text("Google Authenticator")',
    )

    for sel in authenticator_selectors:
        try:
            # Get ALL matching elements (there may be duplicates)
            locs = page.locator(sel)
            cnt = await locs.count()
            if cnt == 0:
                continue
            # Click the first visible one
            for i in range(cnt):
                el = locs.nth(i)
                try:
                    if await el.is_visible(timeout=500):
                        await el.click(timeout=2500)
                        logger.info("chatgpt_login: picked Authenticator via %s[%d]", sel, i)
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    # Last resort: find by iterating all <li> elements
    try:
        all_lis = page.locator('li')
        lic = await all_lis.count()
        for i in range(min(lic, 12)):
            li = all_lis.nth(i)
            try:
                txt = (await li.inner_text(timeout=300)).lower()
                if "google authenticator" in txt:
                    await li.click(timeout=2500)
                    logger.info("chatgpt_login: picked Authenticator via li[%d], text='%s'", i, txt[:50])
                    return True
            except Exception:
                continue
    except Exception:
        pass

    logger.warning("chatgpt_login: Authenticator option NOT found on method picker!")
    return False


async def _wait_for_google_login(page, timeout: int = 60) -> bool:
    """Wait until Google login cookies appear (meaning OAuth completed).

    Guard against stale cookies: __Secure-1PSID/SID linger in the persistent
    profile after a server-side logout, so cookie presence alone would return
    True immediately even while the page is still parked on the Google
    sign-in / challenge screen. Only accept the cookies once we are NOT on an
    accounts.google.com sign-in URL (same class of bug fixed in auto_login)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cookies = await page.context.cookies()
        names = {c["name"] for c in cookies}
        if any(ck in names for ck in _GOOGLE_LOGIN_COOKIES):
            try:
                u = (page.url or "").lower()
            except Exception:
                u = ""
            on_signin = "accounts.google.com" in u and any(
                m in u for m in (
                    "/signin/identifier", "/signin/v2", "/signin/challenge",
                    "/challenge/pwd", "/challenge/", "accountchooser",
                )
            )
            if not on_signin:
                return True
        await asyncio.sleep(1.0)
    return False


def _jwt_payload(tok: str) -> dict:
    """Phần payload của một JWS (3 đoạn). Không phải JWS, hay không giải mã được → {}."""
    import base64
    import json

    phan = str(tok or "").split(".")
    if len(phan) != 3 or not tok.startswith("eyJ"):
        return {}
    try:
        p = phan[1] + "=" * (-len(phan[1]) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def la_access_token(tok: str) -> bool:
    """Chuỗi này đúng là access token của ChatGPT: JWS đọc được, mang claim xác thực của OpenAI.

    Đo 29/09/2026: 17 tài khoản hotmail đăng nhập "thành công" nhưng token nhận về là cookie phiên
    MÃ HOÁ (JWE ``{"alg":"dir"…}``, 5 đoạn) — cũng bắt đầu bằng ``eyJ`` nên lọt qua mọi lần quét cũ;
    backend-api từ chối, c2a đánh lỗi rồi xoá dòng không email. Nhận theo NỘI DUNG, không theo tiền tố."""
    d = _jwt_payload(tok)
    return bool(d) and any(k in d for k in ("https://api.openai.com/auth", "https://api.openai.com/profile", "scp"))


def _email_trong_token(tok: str) -> Optional[str]:
    d = _jwt_payload(tok)
    e = d.get("email") or (d.get("https://api.openai.com/profile") or {}).get("email")
    return e if isinstance(e, str) and "@" in e else None


async def _scrape_chatgpt_token(page) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """After redirect back to chatgpt.com, scrape the JWT and email.

    Returns (access_token, captured_email, access_token_preview).
    """
    access_token = None
    captured_email = None
    access_token_preview = None

    # Preferred: /api/auth/session returns the real Bearer accessToken that
    # chatgpt.com/backend-api accepts. (Scanning localStorage/cookies below
    # grabbed the wrong eyJ value / the opaque NextAuth session cookie, which
    # decodes to an empty payload and gets 401 "could not parse" from the API.)
    # Vài nhịp: ngay sau khi chuyển hướng về chatgpt.com phiên có lúc chưa sẵn — hỏi một lần rồi rơi
    # xuống các đường quét dưới là cách đã nhận nhầm cookie phiên (29/09/2026).
    for lan in range(5):
        try:
            result = await page.evaluate(
                """async () => {
                    try {
                        const r = await fetch('/api/auth/session', { credentials: 'include' });
                        const t = await r.text();
                        try { return { status: r.status, json: JSON.parse(t) }; }
                        catch (e) { return { status: r.status }; }
                    } catch (e) { return { status: 0, error: String(e) }; }
                }"""
            )
            if isinstance(result, dict) and result.get("status") == 200:
                j = result.get("json") or {}
                at = j.get("accessToken")
                if isinstance(at, str) and la_access_token(at):
                    access_token = at
                    access_token_preview = at[:40] + "..."
                    user = j.get("user") or {}
                    if isinstance(user, dict) and user.get("email"):
                        captured_email = user.get("email")
                    logger.info("chatgpt_login: got accessToken from /api/auth/session")
                    break
        except Exception:
            pass
        if lan < 4:
            await asyncio.sleep(1.5)

    # Try localStorage / sessionStorage first
    for storage_key in ("localStorage", "sessionStorage"):
        try:
            token = await page.evaluate(
                f"""(() => {{
                    const keys = Object.keys({storage_key});
                    for (const k of keys) {{
                        const v = {storage_key}.getItem(k);
                        if (v && v.startsWith('eyJ') && v.split('.').length === 3 && v.length > 100) return v;
                    }}
                    return null;
                }})()"""
            )
            if token and not access_token and la_access_token(token):
                logger.info("chatgpt_login: found JWT in %s", storage_key)
                access_token = token
                access_token_preview = token[:40] + "..." if len(token) > 40 else token
                break
        except Exception:
            continue

    # Fallback: only accept a *real* JWT cookie (3 base64 segments). The opaque
    # NextAuth `__Secure-next-auth.session-token` is NOT a ChatGPT accessToken
    # and was previously logged as "found token" while causing free recovery to
    # hang/fail (backend-api 401 "could not parse").
    if not access_token:
        try:
            cookies = await page.context.cookies()
            for c in cookies:
                val = str(c.get("value") or "")
                name = str(c.get("name") or "")
                if not la_access_token(val):
                    continue
                # Prefer known JWT cookie names if present
                if "token" not in name.lower() and "auth" not in name.lower():
                    continue
                access_token = val
                access_token_preview = access_token[:40] + "..."
                logger.info("chatgpt_login: found JWT cookie %s", name)
                break
        except Exception:
            pass

    if access_token and not captured_email:
        captured_email = _email_trong_token(access_token)
    # Try to get email from the page (chỉ khi phiên/token chưa cho — đừng đè email đã chắc)
    try:
        captured_email = captured_email or await page.evaluate("""(() => {
            const el = document.querySelector('[data-testid="account-email"], [title*="@"]');
            if (el) return el.textContent?.trim() || el.getAttribute("title");
            const userEl = document.querySelector('[class*="user"], [class*="profile"], [class*="account"]');
            if (userEl) {
                const text = userEl.textContent || '';
                const match = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/);
                return match ? match[0] : null;
            }
            return null;
        })()""")
    except Exception:
        pass

    return access_token, captured_email, access_token_preview


@dataclass
class ChatGPTOnboardSession:
    profile: str = "chatgpt-default"
    email: str = ""
    state: str = "none"  # none, starting, running, need_tap, need_code, success, failed
    message: str = ""
    tap_number: Optional[str] = None
    elapsed_sec: float = 0.0
    error: Optional[str] = None
    access_token: Optional[str] = None
    expires: Optional[str] = None
    captured_email: Optional[str] = None
    access_token_preview: Optional[str] = None
    totp_secret: str = ""
    pending_code: Optional[str] = None
    prefer_method: str = "auth"
    # When True + the profile already has a Google session, skip the Google
    # login (and the profile nuke) — ChatGPT rides the existing SSO cookie.
    reuse_session: bool = False
    completed_at: Optional[float] = None
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "email": self.email,
            "state": self.state,
            "message": self.message,
            "tap_number": self.tap_number,
            "elapsed_sec": int(time.time() - self.started_at),
            "error": self.error,
            "access_token": self.access_token,
            "expires": self.expires,
            "captured_email": self.captured_email,
            "access_token_preview": self.access_token_preview,
        }


# In-memory session store
_sessions: dict[str, ChatGPTOnboardSession] = {}


def get_session(profile: str) -> Optional[ChatGPTOnboardSession]:
    return _sessions.get(profile)


# Hạn chờ khi đóng trình duyệt của hồ sơ trước lúc xoá nó. Chạy trong handler
# HTTP nên phải có hạn — xem `browser_pool.close_profile`.
_HAN_DONG_HO_SO_S = 15.0


async def _has_google_session(profile: str) -> bool:
    """Best-effort: does `profile` already hold Google SSO cookies? Read-only —
    never wipes. Lets ChatGPT ride a Google session that a prior Gemini/Flow/
    auto-login established on the same profile (reuse_session mode)."""
    try:
        ctx = pool.get_cached(profile)
        if ctx is None:
            # CÓ HẠN: hàm này chạy trong handler HTTP. Hồ sơ đang bận thì thà trả
            # lời "không thấy phiên" còn hơn treo cả yêu cầu (xem `close_profile`).
            ctx = await pool.get(profile=profile, headless=False, cho_toi_da=10.0)
        cookies = await ctx.cookies()
        names = {c.get("name") for c in cookies}
        return any(ck in names for ck in _GOOGLE_LOGIN_COOKIES)
    except Exception as exc:
        logger.warning("chatgpt_login: google-session probe failed for %s: %s", profile, exc)
        return False


async def start_chatgpt_onboard(
    profile: str = "chatgpt-default",
    email: str = "",
    password: str = "",
    totp_secret: str = "",
    prefer_method: str = "auth",
    reuse_session: bool = False,
) -> ChatGPTOnboardSession:
    """Launch ChatGPT onboard in background. Returns immediately with initial state.

    reuse_session=True + an existing Google session on the profile → skip the
    Google login AND the profile nuke, going straight to ChatGPT SSO. Lets
    multi-onboard add ChatGPT without a second Google 2FA. Default False keeps
    the original fresh-login behavior exactly."""
    # Derive 2FA path: Authenticator(TOTP) when a secret is saved, else fall
    # to device-tap (need_tap) — user approves "Yes, it's me" on their phone.
    _prefer = prefer_method or ("auth" if (totp_secret and totp_secret.strip()) else "tap")
    session = ChatGPTOnboardSession(
        profile=profile,
        email=email,
        totp_secret=totp_secret,
        prefer_method=_prefer,
        reuse_session=reuse_session,
        state="starting",
        message="Khoi dong trinh duyet...",
    )
    _sessions[profile] = session

    # KHÔNG có mật khẩu thì KHÔNG tồn tại đường đăng nhập Google mới — chỉ có thể
    # cưỡi phiên sẵn có. Xoá hồ sơ trong tình huống đó là tự tay phá thứ duy nhất
    # còn dùng được, rồi chạy một lượt đăng nhập chắc chắn hỏng.
    #
    # Đo thật 09/08/2026 (nguoi.a@example.com): tầng T2 của auto-recovery gọi
    # endpoint này hai lần với email="" password="". Lần hai đặt
    # reuse_session=False nên nhánh dưới XOÁ hồ sơ ("nuked profile
    # google-nguoi-a" — mất luôn phiên Google đang sống), rồi đăng nhập với
    # email rỗng: đoạn dò tile so khớp chuỗi rỗng nên bấm trúng phần tử đầu tiên
    # gặp được ("clicked account tile for  on chooser screen"), sau đó lặp "bấm
    # lại vào mail" 35 lần suốt 7 phút rưỡi và giữ khoá hồ sơ. Tài khoản đi từ
    # "chỉ hỏng token" thành "mất luôn phiên Google", còn tầng T3 xếp hàng phía
    # sau thì chết theo.
    co_mat_khau = bool((password or "").strip())
    # `and` chập mạch có chủ ý: chỉ dò phiên khi phiên thật sự là đường đi, giữ
    # nguyên hành vi cũ cho lượt "đăng nhập mới có mật khẩu".
    cuoi_duoc = (reuse_session or not co_mat_khau) and await _has_google_session(profile)
    if cuoi_duoc:
        # Keep the existing Google cookies — that's what reuse mode is for.
        logger.info("chatgpt_login: cưỡi session Google sẵn có cho %s — bỏ qua nuke", profile)
        session.reuse_session = True
        session.message = "Tai su dung session Google san co..."
    elif not co_mat_khau:
        session.state = "failed"
        session.error = ("Hồ sơ không có phiên Google mà lượt gọi cũng không kèm mật "
                         "khẩu — không có đường nào để đăng nhập. Đăng nhập lại tài "
                         "khoản Google trước rồi tái dùng.")
        session.completed_at = time.time()
        logger.warning("chatgpt_login: %s — không phiên Google, không mật khẩu → bỏ "
                       "lượt thay vì xoá hồ sơ rồi thử vô ích", profile)
        return session
    else:
        # Kill any existing browser context and nuke old profile.
        # We must wait for Chrome processes to fully exit before deleting,
        # otherwise shutil.rmtree fails silently (ignore_errors=True) and
        # the new context reuses old cookies → Google skips login screen.
        #
        # CÓ HẠN như mọi lời gọi `close_profile` trong handler HTTP. Hết giờ thì
        # DỪNG chứ không đi tiếp: bỏ qua bước xoá rồi vẫn onboard là đăng nhập
        # đè lên hồ sơ của việc khác đang chạy.
        session.reuse_session = False
        try:
            await pool.close_profile(profile, cho_toi_da=_HAN_DONG_HO_SO_S)
        except HoSoDangBan as exc:
            session.state = "failed"
            session.error = f"{exc} — thử lại sau khi việc kia xong."
            session.completed_at = time.time()
            logger.warning("chatgpt_login: %s đang bận, không xoá hồ sơ giữa chừng", profile)
            return session
        # KHÔNG xoá cả hồ sơ nữa — chỉ xoá cookie Google, và việc đó do
        # `_run_onboard_v2` làm ngay khi mở trình duyệt.
        #
        # Việc cần làm ở đây chỉ là "để Google hiện lại màn đăng nhập", tức xoá
        # COOKIE. Xoá cả `user-data-dir` làm thêm một chuyện không ai cần: vứt
        # luôn danh tính thiết bị (Local State, dấu vân tay, lịch sử, cache).
        # Lần đăng nhập kế tiếp vì thế trông như một máy hoàn toàn lạ đang gõ
        # mật khẩu của tài khoản — đúng thứ khiến Google đòi xác minh thêm.
        #
        # Đo 29/08/2026: mỗi lượt onboard không-tái-dùng là một lần xoá sạch hồ
        # sơ, nên tài khoản phải đăng nhập lại rất nhiều và lần nào cũng có nguy
        # cơ dính captcha. Giữ hồ sơ lại thì thiết bị quen mặt dần.

    asyncio.create_task(_chay_onboard_co_han(session, password))
    return session


# Trần thời gian cho MỘT lượt onboard. Các bước bên trong đã có hạn riêng, nhưng
# chúng CỘNG DỒN được: đo thật 09/08/2026 một lượt chạy 608 giây và vẫn đang
# chạy, dù vòng vào-ô-mật-khẩu chỉ có ngân sách 420 giây.
_NGAN_SACH_ONBOARD_S = 480.0

# Đang chờ NGƯỜI thì không tính giờ: đồng hồ chỉ đếm lúc máy tự làm. Cắt ngang
# lúc người đang gõ captcha trên noVNC là phá đúng thứ mình vừa nhờ họ làm.
_CHO_NGUOI = ("need_code", "need_captcha", "need_tap")


async def _chay_onboard_co_han(session: ChatGPTOnboardSession, password: str) -> None:
    """Chạy onboard, và ĐẢM BẢO nhả trình duyệt kể cả khi nó treo.

    VÌ SAO CẦN: mỗi hồ sơ chỉ một việc dùng trình duyệt tại một thời điểm. Một
    lượt onboard treo không chỉ tự hỏng — nó GIỮ KHOÁ HỒ SƠ, nên mọi việc khác
    trên cùng tài khoản (đăng nhập lại, khôi phục, tạo ảnh) xếp hàng phía sau và
    cũng chết theo. Bên gọi `_cgf_onboard_once` bỏ cuộc sau 180 giây, nhưng tác
    vụ này thì không biết điều đó và cứ chạy tiếp — chủ máy nhìn noVNC thấy
    trình duyệt nằm đó mãi: "xong lỗi cũng không thấy đóng workspace lại".

    Vòng canh giờ chạy song song thay vì bọc `asyncio.wait_for` quanh cả tác vụ,
    vì cần TẠM DỪNG đồng hồ khi phiên đang chờ người (`need_code`/`need_captcha`
    /`need_tap`) — những lúc đó chờ lâu là đúng, không phải treo.
    """
    viec = asyncio.create_task(_run_onboard_v2(session, password))
    han = time.time() + _NGAN_SACH_ONBOARD_S
    try:
        while not viec.done():
            await asyncio.sleep(5.0)
            if session.state in _CHO_NGUOI:
                han = time.time() + _NGAN_SACH_ONBOARD_S   # dừng đồng hồ
                continue
            if time.time() > han:
                logger.warning("chatgpt_login: onboard quá %.0fs — dừng và nhả hồ sơ %s",
                               _NGAN_SACH_ONBOARD_S, session.profile)
                viec.cancel()
                break
        await asyncio.gather(viec, return_exceptions=True)
    finally:
        if session.state not in ("success", "failed"):
            session.state = "failed"
            session.error = (f"Quá {_NGAN_SACH_ONBOARD_S:.0f}s chưa xong — đã dừng và "
                             f"nhả trình duyệt để việc khác trên hồ sơ này chạy được")
            session.completed_at = time.time()
        # `_run_onboard_v2` tự đóng ở đường bình thường; ở đường bị huỷ thì nó
        # không chạy tới đó, nên phải đóng tại đây. Đóng hai lần là vô hại —
        # nhưng đóng trúng lượt đăng nhập của việc khác thì không.
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi dừng onboard bỏ qua", exc_info=True)


async def _nuke_profile(profile: str, max_wait: float = 10.0) -> None:
    """Delete browser profile directory, retrying until files are unlocked.

    SAFETY: if another session (gemini_web, flow, etc.) is currently using
    this profile through the browser pool, we skip deletion entirely.
    Only nuke profiles that are NOT actively in use by another component.
    """
    if pool.is_loaded(profile):
        logger.info("chatgpt_login: profile %s is loaded in pool, skipping nuke", profile)
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
            logger.info("chatgpt_login: nuked profile %s", profile)
            return
        except PermissionError:
            logger.warning("chatgpt_login: profile %s locked, retrying in 1s...", profile)
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.warning("chatgpt_login: rmtree attempt failed: %s, retrying...", exc)
            await asyncio.sleep(1.0)
    # Last resort: rename out of the way so next launch gets a clean dir
    try:
        backup = _profile_dir.with_name(_profile_dir.name + f".old-{int(time.time())}")
        shutil.move(str(_profile_dir), str(backup))
        logger.warning("chatgpt_login: could not delete, renamed to %s", backup.name)
    except Exception as exc:
        logger.error("chatgpt_login: profile cleanup failed completely: %s", exc)


async def _run_onboard(session: ChatGPTOnboardSession, password: str) -> None:
    """Main onboard orchestration."""
    started_at = time.time()
    try:
        async with pool.page(profile=session.profile, headless=False) as page:
            # ── Step 0: Purge ALL Google cookies (belt-and-suspenders) ──
            # Even after nuking the profile, Chrome may restore cookies from
            # sync or a leftover session. This ensures we ALWAYS start fresh.
            if not session.reuse_session:
                session.state = "running"
                session.message = "Dang xoa Google cookies cu..."
                _google_domains = (
                    "https://accounts.google.com/",
                    "https://google.com/",
                    "https://myaccount.google.com/",
                    "https://mail.google.com/",
                )
                for _gdom in _google_domains:
                    try:
                        gc = await page.context.cookies(_gdom)
                        for c in gc:
                            try:
                                await page.context.clear_cookies(name=c.get("name"), domain=c.get("domain", ""))
                            except Exception:
                                pass
                        if gc:
                            logger.info("chatgpt_login: cleared %d cookies for %s", len(gc), _gdom)
                    except Exception:
                        pass

            # ── Step 1: Clear stale Auth0 cookies so we see the login widget ──
            # (not the "Your session has ended" page which redirects to
            # chatgpt.com/api/auth/error → Cloudflare Turnstile).
            session.state = "running"
            session.message = "Dang xoa cookie Auth0 cu..."
            logger.info("chatgpt_login: clearing auth.openai.com cookies")
            try:
                auth0_cookies = await page.context.cookies("https://auth.openai.com/")
                if auth0_cookies:
                    for c in auth0_cookies:
                        try:
                            await page.context.clear_cookies(name=c.get("name"), domain="auth.openai.com")
                        except Exception:
                            pass
                    logger.info("chatgpt_login: cleared %d auth0 cookies", len(auth0_cookies))
            except Exception as exc:
                logger.warning("chatgpt_login: cookie clear failed: %s", exc)

            # ── Step 2: Navigate to auth.openai.com (Auth0, bypasses Cloudflare) ──
            session.message = "Dang mo trang dang nhap OpenAI..."
            logger.info("chatgpt_login: navigating to %s", _AUTH0_LOGIN_URL)
            await page.goto(_AUTH0_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3.0)

            # ── Step 3: Handle "Session ended" landing page if it still appears ──
            # If Auth0 still shows "Your session has ended" with a login button,
            # click it to reach the universal login widget.
            session.message = "Dang kiem tra trang Auth0..."
            auth0_login_clicked = False
            _AUTH0_LOGIN_BTN_SELECTORS = (
                'button:has-text("Đăng nhập")',
                'button:has-text("Log in")',
                'button:has-text("Sign in")',
                'a:has-text("Đăng nhập")',
                'a:has-text("Log in")',
                'a:has-text("Sign in")',
                'button[class*="login"]',
                'button[class*="Login"]',
                'a[class*="login"]',
                'a[class*="Login"]',
                'button[data-action*="login"]',
                'a[href*="login"]',
                '[class*="auth0"] button',
                'form[action*="login"] button',
            )
            for pg in page.context.pages:
                try:
                    if pg.is_closed():
                        continue
                except Exception:
                    continue
                for sel in _AUTH0_LOGIN_BTN_SELECTORS:
                    try:
                        loc = pg.locator(sel).first
                        if await loc.count() > 0:
                            await loc.click(timeout=5_000)
                            auth0_login_clicked = True
                            logger.info("chatgpt_login: clicked Auth0 login via %s", sel)
                            break
                    except Exception:
                        continue
                if auth0_login_clicked:
                    break

            if not auth0_login_clicked:
                # JS fallback: click any visible button with login text
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    try:
                        clicked = await pg.evaluate("""() => {
                            const all = document.querySelectorAll('button, a, div[role="button"]');
                            for (const el of all) {
                                if (!el.offsetParent) continue;
                                const txt = (el.innerText || '').toLowerCase().trim();
                                if (txt === 'đăng nhập' || txt === 'log in' || txt === 'sign in' || txt === 'login') {
                                    el.click();
                                    return txt;
                                }
                            }
                            return null;
                        }""")
                        if clicked:
                            auth0_login_clicked = True
                            logger.info("chatgpt_login: JS fallback clicked Auth0 login — %s", clicked)
                            break
                    except Exception:
                        continue

            if auth0_login_clicked:
                logger.info("chatgpt_login: Auth0 login clicked, waiting for widget to load...")
                await asyncio.sleep(4.0)
            else:
                logger.warning("chatgpt_login: no Auth0 login button found, page may already show widget")

            # ── Step 4: Click "Continue with Google" on auth.openai.com ──
            session.message = "Dang tim nut Google..."

            # Track whether we used a proper OAuth URL (with client_id,
            # redirect_uri, etc.) or fell back to bare accounts.google.com.
            # Step 6.5 needs to know: if bare login, we must navigate back
            # to auth.openai.com to trigger the real OAuth consent flow.
            _google_oauth_used = False

            # Check all open pages for the Google button
            google_clicked = False
            google_page = None
            for pg in page.context.pages:
                try:
                    if pg.is_closed():
                        continue
                except Exception:
                    continue

                # Role names first (VN welcome-back "Tiếp tục với Google")
                for _gname in (
                    "Tiếp tục với Google",
                    "Continue with Google",
                    "Continue with google",
                ):
                    try:
                        loc = pg.get_by_role("button", name=_gname)
                        if await loc.count() > 0 and await loc.first.is_visible():
                            await loc.first.click(timeout=5_000, force=True)
                            google_clicked = True
                            _google_oauth_used = True
                            logger.info(
                                "chatgpt_login: clicked Google via role %r on %s",
                                _gname, pg.url,
                            )
                            break
                    except Exception:
                        continue
                if google_clicked:
                    break

                for sel in _GOOGLE_BTN_SELECTORS:
                    try:
                        loc = pg.locator(sel).first
                        if await loc.count() > 0:
                            await loc.click(timeout=5_000)
                            google_clicked = True
                            _google_oauth_used = True
                            logger.info("chatgpt_login: clicked Google via %s on %s", sel, pg.url)
                            break
                    except Exception:
                        continue
                if google_clicked:
                    break

            if not google_clicked:
                # ── Fallback A: dump page buttons for debugging, then JS click ──
                logger.warning("chatgpt_login: selectors failed, analyzing page...")
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    try:
                        url = pg.url or "?"
                        title = await pg.title() or ""
                        # Dump all visible buttons/links using locator (not evaluate)
                        try:
                            all_btns = pg.locator('button:visible, a:visible, [role="button"]:visible')
                            btn_count = await all_btns.count()
                            btn_texts = []
                            for i in range(min(btn_count, 20)):
                                try:
                                    txt = await all_btns.nth(i).inner_text(timeout=500)
                                    btn_texts.append(txt.strip()[:60] if txt else "")
                                except Exception:
                                    btn_texts.append("(error)")
                            logger.warning("chatgpt_login: page %s title=%r visible_buttons=%s", url[:100], title[:60], btn_texts)
                        except Exception as exc:
                            logger.warning("chatgpt_login: button scan error: %s", exc)
                    except Exception:
                        pass

                # Try JS click on visible Google-related element
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    try:
                        clicked = await pg.evaluate("""() => {
                            const all = document.querySelectorAll('button, a, div[role="button"], span[role="button"], input[type="submit"]');
                            for (const el of all) {
                                if (!el.offsetParent) continue;
                                const txt = (el.innerText || el.getAttribute('aria-label') || el.value || '').toLowerCase();
                                const href = el.getAttribute('href') || '';
                                const cls = (el.className || '').toLowerCase();
                                if (txt.includes('google') || href.includes('accounts.google.com') || cls.includes('google')) {
                                    el.click();
                                    return 'clicked: ' + txt.slice(0, 50);
                                }
                            }
                            return null;
                        }""")
                        if clicked:
                            google_clicked = True
                            _google_oauth_used = True
                            logger.info("chatgpt_login: JS fallback clicked Google — %s", clicked)
                            break
                    except Exception:
                        continue

            if not google_clicked:
                # ── Check if any page already landed on Google (JS clicks may have
                #    opened a new tab even when they returned no match string). ──
                google_page = None
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    pg_url = pg.url or ""
                    if "accounts.google.com" in pg_url:
                        google_clicked = True
                        _google_oauth_used = True
                        google_page = pg
                        logger.info("chatgpt_login: found existing Google page: %s", pg_url[:120])
                        break

            if not google_clicked:
                # ── Fallback B: extract the REAL Google OAuth URL from the page.
                #    A bare Google sign-in page WITHOUT OAuth parameters
                #    (client_id, redirect_uri, state, nonce, response_type)
                #    causes Google Error 400 — Google doesn't know where to
                #    redirect after login. We MUST use the actual OAuth URL
                #    that was generated during the Auth0 → Google flow. ──
                logger.warning("chatgpt_login: all button attempts failed, extracting Google OAuth URL from DOM...")
                session.message = "Dang trich xuat Google OAuth URL..."
                oauth_url: Optional[str] = None

                # Search ALL pages for a Google OAuth link (including hidden DOM nodes
                # and inside shadow DOM where Auth0 widgets render).
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    try:
                        oauth_url = await pg.evaluate("""() => {
                            // Anchor tags with Google OAuth / signin URLs
                            const links = document.querySelectorAll('a[href*="accounts.google.com"]');
                            for (const a of links) {
                                const h = a.getAttribute('href');
                                // Prefer OAuth URLs with proper params
                                if (h.includes('oauth') || h.includes('openidrealm') || h.includes('redirect_uri')) return h;
                            }
                            // Any href to Google (even without oauth keyword)
                            for (const a of links) {
                                const h = a.getAttribute('href');
                                if (h && h.includes('accounts.google.com')) return h;
                            }
                            // Form actions
                            const forms = document.querySelectorAll('form[action*="accounts.google.com"]');
                            for (const f of forms) {
                                return f.getAttribute('action');
                            }
                            // data attributes
                            const all = document.querySelectorAll(
                                '[data-url*="accounts.google"], [data-href*="accounts.google"], '
                                + '[onclick*="accounts.google"]'
                            );
                            for (const el of all) {
                                const d = el.getAttribute('data-url') || el.getAttribute('data-href');
                                if (d && d.includes('accounts.google.com')) return d;
                            }
                            return null;
                        }""")
                        if oauth_url:
                            logger.info("chatgpt_login: extracted Google OAuth URL from DOM: %s", oauth_url[:200])
                            break
                    except Exception as exc:
                        logger.warning("chatgpt_login: OAuth URL extraction failed: %s", exc)

                if oauth_url:
                    try:
                        google_page = await page.context.new_page()
                        await google_page.goto(oauth_url, wait_until="domcontentloaded", timeout=30_000)
                        google_clicked = True
                        _google_oauth_used = True
                        logger.info("chatgpt_login: navigated to extracted OAuth URL")
                    except Exception as exc:
                        logger.error("chatgpt_login: OAuth URL navigation failed: %s", exc)

                if not google_clicked:
                    # ── Fallback C: Auth0 supports `connection=google-oauth2`
                    #    query parameter on the /authorize or /login endpoint,
                    #    which bypasses the widget and redirects directly to
                    #    Google's OAuth consent screen with all required params. ──
                    logger.warning("chatgpt_login: no OAuth URL in DOM, trying Auth0 connection param...")
                    session.message = "Dang thu Auth0 Google redirect..."
                    for pg in page.context.pages:
                        try:
                            if pg.is_closed():
                                continue
                        except Exception:
                            continue
                        pg_url = pg.url or ""
                        # auth.openai.com/authorize?... or auth.openai.com/u/login
                        if "auth.openai.com" not in pg_url:
                            continue
                        try:
                            sep = "&" if "?" in pg_url else "?"
                            auth0_google_url = f"{pg_url}{sep}connection=google-oauth2"
                            google_page = await page.context.new_page()
                            await google_page.goto(auth0_google_url, wait_until="domcontentloaded", timeout=30_000)
                            google_clicked = True
                            _google_oauth_used = True
                            logger.info("chatgpt_login: Auth0 connection param redirected to Google")
                            break
                        except Exception as exc:
                            logger.warning("chatgpt_login: Auth0 connection param failed: %s", exc)

                if not google_clicked:
                    # ── Fallback D: navigate to main Google page for login,
                    #    then come back to auth.openai.com for OAuth consent.
                    #    accounts.google.com handles sign-in correctly (no 400
                    #    error like the bare v3/signin/identifier API endpoint). ──
                    logger.warning("chatgpt_login: OAuth methods exhausted, falling back to accounts.google.com...")
                    session.message = "Dang mo accounts.google.com..."
                    try:
                        google_page = await page.context.new_page()
                        await google_page.goto(
                            "https://accounts.google.com/?hl=vi",
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        google_clicked = True
                        logger.info("chatgpt_login: opened accounts.google.com for direct login")
                    except Exception as exc:
                        logger.error("chatgpt_login: accounts.google.com navigation failed: %s", exc)
                        session.state = "failed"
                        session.error = "Khong the mo Google — Auth0 widget khong load, thu lai sau"
                        return

            await asyncio.sleep(2.0)

            # ── Step 4: Google login (email) ──
            # First check if we're already logged into Google. If so, look for
            # OAuth consent screen instead of email form.
            session.message = "Kiem tra trang thai Google..."
            # Wait for the Google page (or any page) to settle
            _wait_pages = [google_page] if (google_clicked and google_page) else page.context.pages
            for _pg in _wait_pages:
                try:
                    if _pg.is_closed():
                        continue
                except Exception:
                    continue
                try:
                    await _pg.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
            await asyncio.sleep(1.0)

            already_logged_into_google = False
            for pg in page.context.pages:
                try:
                    if pg.is_closed():
                        continue
                except Exception:
                    continue
                pg_url = pg.url or ""
                if "myaccount.google.com" in pg_url:
                    already_logged_into_google = True
                    logger.info("chatgpt_login: already logged into Google (url=%s), using OAuth consent", pg_url[:100])
                    break

            if already_logged_into_google:
                # Profile has Google session — just need OAuth consent for ChatGPT
                session.message = "Da co Google session, dang OAuth consent..."
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    if "accounts.google.com" not in (pg.url or ""):
                        continue
                    consent_clicked = await click_google_oauth_consent(pg, timeout=15.0)
                    if consent_clicked:
                        logger.info("chatgpt_login: OAuth consent clicked, waiting for redirect...")
                        session.message = "Da click OAuth consent, dang cho redirect..."
                        for _ in range(20):
                            await asyncio.sleep(3.0)
                            for p in page.context.pages:
                                try:
                                    if p.is_closed():
                                        continue
                                except Exception:
                                    continue
                                if "chatgpt.com" in (p.url or "") and "auth" not in (p.url or ""):
                                    session.state = "success"
                                    session.message = "Da redirect ve chatgpt.com"
                                    break
                            if session.state == "success":
                                break
                        if session.state == "success":
                            break
                    else:
                        logger.warning("chatgpt_login: consent not found on Google page")
                if session.state == "success":
                    # Scrape token directly from the pre-existing Google session flow
                    token, captured, preview = await _scrape_chatgpt_token(page)
                    if token:
                        session.access_token = token
                        session.captured_email = captured or session.email
                        session.access_token_preview = preview
                        session.message = f"Lay token thanh cong ({captured or session.email})"
                        logger.info("chatgpt_login: scraped token via OAuth consent path preview=%s", preview)
                    else:
                        session.state = "failed"
                        session.error = "Dang nhap OK nhung khong scrape duoc JWT"
                    return
                else:
                    session.state = "failed"
                    session.error = "Co Google session nhung khong hoan tat OAuth cho ChatGPT"
                    return
            else:
                # Need fresh Google login
                email_filled = False
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue

                    try:
                        pg_url = pg.url or "?"
                        pg_title = await pg.title() or ""
                        logger.info("chatgpt_login: checking page url=%s title=%r for email input", pg_url[:120], pg_title[:60])
                    except Exception:
                        pass

                    # Check for Google "unsafe browser" block page
                    try:
                        body = (await pg.locator("body").inner_text(timeout=1000)).lower()
                        if any(k in body for k in (
                            "browser or app may not be secure",
                            "trình duyệt hoặc ứng dụng này có thể không an toàn",
                            "couldn't sign you in",
                            "không thể đăng nhập",
                        )):
                            logger.error("chatgpt_login: Google blocked the browser! body snippet=%s", body[:300])
                            session.state = "failed"
                            session.error = "Google chặn trình duyệt — thử đăng nhập thủ công qua noVNC"
                            return
                    except Exception:
                        pass

                    email_selectors = (
                        'input[type="email"]',
                        'input[name="identifier"]',
                        '#identifierId',
                        'input[autocomplete="username"]',
                        'input[type="text"][name="identifier"]',
                        'input[type="text"][autocomplete*="email"]',
                    )
                    for sel in email_selectors:
                        try:
                            loc = pg.locator(sel).first
                            if await loc.count() > 0:
                                await loc.click(timeout=3000)
                                await asyncio.sleep(random.uniform(0.2, 0.4))
                                await loc.fill(session.email)
                                await asyncio.sleep(random.uniform(0.3, 0.6))
                                email_filled = True
                                logger.info("chatgpt_login: filled email via %s", sel)
                                break
                        except Exception:
                            continue

                    if email_filled:
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        # Google v3: press Enter first (most reliable), then click Next as fallback
                        try:
                            await loc.press("Enter", delay=random.randint(100, 300))
                            logger.info("chatgpt_login: pressed Enter on email field")
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(0.3, 0.5))
                        clicked = await _safe_click(
                            pg,
                            'button:has-text("Next")',
                            'button:has-text("Tiếp theo")',
                            'span[jsname="V67aGc"]',
                            '#identifierNext',
                            'button[jsname="LgbsSe"]:visible',
                            'div[role="button"]:has-text("Next")',
                            'div[role="button"]:has-text("Tiếp theo")',
                        )
                        if not clicked:
                            try:
                                await pg.evaluate("""() => {
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
                            except Exception:
                                pass
                        logger.info("chatgpt_login: submitted email, clicked=%s", clicked)
                        break

                if not email_filled:
                    logger.info("chatgpt_login: waiting for email input to appear...")
                    for pg in page.context.pages:
                        try:
                            if pg.is_closed():
                                continue
                        except Exception:
                            continue
                        for sel in ('input[type="email"]', 'input[name="identifier"]', '#identifierId'):
                            try:
                                loc = pg.locator(sel).first
                                await loc.wait_for(state="visible", timeout=15_000)
                                await loc.click(timeout=3000)
                                await asyncio.sleep(random.uniform(0.2, 0.4))
                                await loc.fill(session.email)
                                await asyncio.sleep(random.uniform(0.3, 0.6))
                                email_filled = True
                                logger.info("chatgpt_login: filled email via wait+%s", sel)
                                break
                            except Exception:
                                continue
                        if email_filled:
                            await asyncio.sleep(random.uniform(0.5, 1.0))
                            try:
                                await loc.press("Enter", delay=random.randint(100, 300))
                                logger.info("chatgpt_login: pressed Enter on email (wait path)")
                            except Exception:
                                pass
                            await asyncio.sleep(random.uniform(0.3, 0.5))
                            clicked = await _safe_click(
                                pg,
                                'button:has-text("Next")',
                                'button:has-text("Tiếp theo")',
                                'span[jsname="V67aGc"]',
                                '#identifierNext',
                                'button[jsname="LgbsSe"]:visible',
                                'div[role="button"]:has-text("Next")',
                                'div[role="button"]:has-text("Tiếp theo")',
                            )
                            if not clicked:
                                try:
                                    await pg.evaluate("""() => {
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
                                except Exception:
                                    pass
                            logger.info("chatgpt_login: submitted email (wait path), clicked=%s", clicked)
                            break

                if not email_filled:
                    for pg in page.context.pages:
                        try:
                            if pg.is_closed():
                                continue
                        except Exception:
                            continue
                        try:
                            url = pg.url or "?"
                            body = await pg.locator("body").inner_text(timeout=3000)
                            logger.error("chatgpt_login: PAGE BODY url=%s body_first_500=%s", url[:120], body[:500])
                        except Exception as exc:
                            logger.error("chatgpt_login: body read failed url=%s err=%s", getattr(pg, "url", "?"), exc)
                    logger.error("chatgpt_login: email input not found after waiting!")
                    session.state = "failed"
                    session.error = "Google khong hien thi o email — co the trinh duyet bi chan"
                    return

            # ── Step 5: Google login (password) ──
            await asyncio.sleep(3.0)
            session.message = "Dang nhap mat khau..."

            # Debug: log what page Google shows after email
            for pg in page.context.pages:
                try:
                    if pg.is_closed():
                        continue
                except Exception:
                    continue
                try:
                    url = pg.url or "?"
                    title = await pg.title() or ""
                    body = (await pg.locator("body").inner_text(timeout=2000))[:400]
                    logger.info("chatgpt_login: after email submit, url=%s title=%r body=%s", url[:120], title[:80], body[:300])
                except Exception:
                    pass

            pw_filled = False
            _PW_SELECTORS = (
                'input[type="password"]',
                'input[name="Passwd"]',
                'input[name="password"]',
                'input[autocomplete="current-password"]',
                '#password input[type="password"]',
            )
            for pg in page.context.pages:
                try:
                    if pg.is_closed():
                        continue
                except Exception:
                    continue

                # Wait for password field to appear (Google v3 transitions take time)
                for sel in _PW_SELECTORS:
                    try:
                        loc = pg.locator(sel).first
                        await loc.wait_for(state="visible", timeout=20_000)
                        await loc.click(timeout=3000)
                        await asyncio.sleep(random.uniform(0.3, 0.5))
                        await loc.fill(password)
                        await asyncio.sleep(random.uniform(0.3, 0.6))
                        pw_filled = True
                        logger.info("chatgpt_login: filled password via wait+%s", sel)
                        break
                    except Exception:
                        continue

                if pw_filled:
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    clicked = await _safe_click(
                        pg,
                        'button:has-text("Next")',
                        'button:has-text("Tiếp theo")',
                        '#passwordNext',
                        'button[jsname="LgbsSe"]:visible',
                        'div[role="button"]:has-text("Next")',
                        'div[role="button"]:has-text("Tiếp theo")',
                    )
                    if not clicked:
                        try:
                            await pg.evaluate("""() => {
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
                        except Exception:
                            pass
                    try:
                        await loc.press("Enter", delay=random.randint(100, 300))
                    except Exception:
                        pass
                    logger.info("chatgpt_login: submitted password, clicked=%s", clicked)
                    break

            if not pw_filled:
                # Try waiting for password input
                logger.info("chatgpt_login: waiting for password input...")
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    for sel in ('input[type="password"]', 'input[name="Passwd"]'):
                        try:
                            loc = pg.locator(sel).first
                            await loc.wait_for(state="visible", timeout=15_000)
                            await loc.click(timeout=3000)
                            await asyncio.sleep(random.uniform(0.2, 0.4))
                            await loc.fill(password)
                            await asyncio.sleep(random.uniform(0.3, 0.6))
                            pw_filled = True
                            logger.info("chatgpt_login: filled password via wait+%s", sel)
                            break
                        except Exception:
                            continue
                    if pw_filled:
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        clicked = await _safe_click(
                            pg,
                            'button:has-text("Next")',
                            'button:has-text("Tiếp theo")',
                            '#passwordNext',
                            'button[jsname="LgbsSe"]:visible',
                            'div[role="button"]:has-text("Next")',
                            'div[role="button"]:has-text("Tiếp theo")',
                        )
                        if not clicked:
                            try:
                                await pg.evaluate("""() => {
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
                            except Exception:
                                pass
                        try:
                            await loc.press("Enter", delay=random.randint(100, 300))
                        except Exception:
                            pass
                        logger.info("chatgpt_login: submitted password (wait path), clicked=%s", clicked)
                        break

            if not pw_filled:
                logger.error("chatgpt_login: password input not found!")
                session.state = "failed"
                session.error = "Google khong hien thi o mat khau — co the trinh duyet bi chan"
                return

            # ── Step 6: Handle 2FA if needed ──
            session.elapsed_sec = time.time() - started_at
            auth_picked = False       # Only pick Authenticator once
            code_attempt = 0          # Track retries for TOTP
            for _ in range(40):       # up to ~4 min polling
                await asyncio.sleep(3.0)
                session.elapsed_sec = time.time() - started_at

                # Check if we're already on chatgpt.com (login succeeded without 2FA)
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    if "chatgpt.com" in (pg.url or "") and "auth" not in (pg.url or ""):
                        session.state = "success"
                        session.message = "Da dang nhap ChatGPT (khong can 2FA)"
                        break

                if session.state == "success":
                    break

                # Check for 2FA on any page
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue

                    # Auto-pick Authenticator — only ONCE per session.
                    # Google v3 defaults to phone prompt. If Authenticator isn't
                    # visible in the method list, click "Try another way" to
                    # reveal it, then try picking Authenticator again.
                    if not auth_picked:
                        picked = await _pick_authenticator_method(pg)
                        if not picked:
                            # Authenticator not visible — try expanding the list
                            try_another_way_selectors = (
                                'button:has-text("Try another way")',
                                'button:has-text("Thử cách khác")',
                                'a:has-text("Try another way")',
                                'a:has-text("Thử cách khác")',
                                'span:has-text("Try another way")',
                                'span:has-text("Thử cách khác")',
                                'div[role="button"]:has-text("Try another way")',
                                'div[role="button"]:has-text("Thử cách khác")',
                            )
                            for tsel in try_another_way_selectors:
                                try:
                                    tloc = pg.locator(tsel).first
                                    if await tloc.count() > 0 and await tloc.is_visible(timeout=500):
                                        await tloc.click(timeout=3000)
                                        logger.info("chatgpt_login: clicked Try another way via %s", tsel)
                                        await asyncio.sleep(3.0)
                                        break
                                except Exception:
                                    continue
                            # Try picking Authenticator again after expanding
                            picked = await _pick_authenticator_method(pg)
                        if picked:
                            auth_picked = True
                            session.message = "Da chon Google Authenticator, dang cho code..."
                            logger.info("chatgpt_login: auto-picked Authenticator")
                            await asyncio.sleep(3.0)
                            continue

                    # Detect phone tap prompt
                    try:
                        tap_el = pg.locator(
                            'div[jsname="RjfePd"] span, '
                            '[data-tap-target], '
                            'text=/tap.*\\d{2}/i'
                        ).first
                        if await tap_el.count() > 0:
                            text = await tap_el.text_content() or ""
                            import re
                            nums = re.findall(r"\d{2}", text)
                            if nums:
                                session.tap_number = nums[0]
                                session.state = "need_tap"
                                session.message = f"Mo app Google tren dien thoai, bam so {nums[0]}"
                                logger.info("chatgpt_login: need_tap=%s", nums[0])
                                continue
                    except Exception:
                        pass

                    # Detect TOTP/2FA code input
                    for sel in _2FA_CODE_SELECTORS:
                        try:
                            loc = pg.locator(sel).first
                            if await loc.count() > 0:
                                visible = await loc.is_visible()
                                if not visible:
                                    continue

                                # Generate code — retry with fresh code if previous attempt failed
                                if session.totp_secret and _HAS_PYOTP:
                                    secret = session.totp_secret.replace(" ", "")
                                    code = pyotp.TOTP(secret).now()
                                    logger.info("chatgpt_login: TOTP code=%s (attempt %d)", code, code_attempt + 1)
                                    session.state = "need_code"
                                    session.message = f"Da tu sinh ma 2FA (lan {code_attempt + 1})"
                                    # Wait for fresh window if retrying
                                    if code_attempt > 0:
                                        remaining = 30 - (int(time.time()) % 30)
                                        if remaining < 5:
                                            await asyncio.sleep(remaining + 2)
                                            code = pyotp.TOTP(secret).now()
                                            logger.info("chatgpt_login: refreshed TOTP code=%s", code)
                                    await asyncio.sleep(1.0)
                                else:
                                    session.state = "need_code"
                                    session.message = "Can ma 2FA"
                                    code_deadline = time.time() + 180
                                    while time.time() < code_deadline and not session.pending_code:
                                        await asyncio.sleep(0.5)
                                    if not session.pending_code:
                                        session.state = "failed"
                                        session.error = "Khong nhan duoc ma 2FA trong 3 phut"
                                        return
                                    code = session.pending_code
                                    session.pending_code = None

                                # Fill code - use fill() for reliability on input[type=tel]
                                await loc.click(timeout=2000)
                                await loc.fill("")
                                await asyncio.sleep(random.uniform(0.3, 0.6))
                                await loc.fill(code)
                                code_attempt += 1
                                # Verify code was actually typed
                                try:
                                    actual = await loc.input_value()
                                    logger.info("chatgpt_login: typed=%s actual=%s (attempt %d)", code, actual, code_attempt)
                                except Exception:
                                    logger.info("chatgpt_login: filled 2FA code (attempt %d)", code_attempt)
                                await asyncio.sleep(random.uniform(0.4, 0.8))
                                await _safe_click(
                                    pg,
                                    'button:has-text("Next")',
                                    'button:has-text("Tiếp theo")',
                                    'span[jsname="V67aGc"]',
                                    '#totpNext button',
                                    '#totpNext span',
                                    'button[jsname="LgbsSe"]:visible',
                                )
                                session.state = "running"
                                session.message = f"Da gui ma (lan {code_attempt}), dang xac minh..."
                                await asyncio.sleep(5.0)

                                # Check immediately if code was accepted
                                try:
                                    body = (await pg.locator("body").inner_text(timeout=1000)).lower()
                                    if any(e in body for e in ("wrong", "sai", "incorrect", "không đúng", "invalid", "khong hop le")):
                                        logger.warning("chatgpt_login: code rejected, will retry")
                                        session.state = "running"  # reset to allow re-detection
                                        if code_attempt >= 3:
                                            session.state = "failed"
                                            session.error = "Sai ma TOTP 3 lan lien tiep"
                                            return
                                        await asyncio.sleep(3.0)
                                        break
                                except Exception:
                                    pass

                                break  # break for sel loop
                        except Exception:
                            continue

            # ── Step 6.5: If we only logged into accounts.google.com
            #    (not via OAuth redirect), navigate back to auth.openai.com
            #    so Auth0 can detect the Google session and complete the
            #    ChatGPT OAuth flow with proper consent screen. ──
            session.elapsed_sec = time.time() - started_at
            if not _google_oauth_used:
                # Check if Google login succeeded (cookies present) but we're
                # NOT on chatgpt.com yet — meaning OAuth wasn't triggered.
                on_chatgpt = False
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    if "chatgpt.com" in (pg.url or "") and "auth" not in (pg.url or ""):
                        on_chatgpt = True
                        break
                if not on_chatgpt:
                    session.message = "Dang quay lai auth.openai.com de hoan tat OAuth..."
                    logger.info("chatgpt_login: accounts.google.com login OK, navigating to auth.openai.com for OAuth")
                    try:
                        oauth_page = page
                        await oauth_page.goto(_AUTH0_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                        await asyncio.sleep(3.0)
                        # Find and click "Continue with Google" — Auth0 should
                        # now auto-detect the Google session and skip to consent.
                        google_clicked = False
                        for sel in _GOOGLE_BTN_SELECTORS:
                            try:
                                loc = oauth_page.locator(sel).first
                                if await loc.count() > 0:
                                    await loc.click(timeout=5_000)
                                    google_clicked = True
                                    logger.info("chatgpt_login: post-login Google OAuth click via %s", sel)
                                    break
                            except Exception:
                                continue
                        if not google_clicked:
                            # JS fallback
                            try:
                                await oauth_page.evaluate("""() => {
                                    const all = document.querySelectorAll('button, a, div[role="button"]');
                                    for (const el of all) {
                                        if (!el.offsetParent) continue;
                                        const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                                        const h = el.getAttribute('href') || '';
                                        if (t.includes('google') || h.includes('accounts.google.com')) {
                                            el.click();
                                            return 'clicked';
                                        }
                                    }
                                    return null;
                                }""")
                                google_clicked = True
                                logger.info("chatgpt_login: post-login Google OAuth via JS fallback")
                            except Exception:
                                pass
                        if google_clicked:
                            session.message = "Da click Google OAuth, dang cho redirect..."
                            logger.info("chatgpt_login: post-login OAuth triggered, waiting for chatgpt.com redirect")
                        else:
                            logger.warning("chatgpt_login: post-login Google button still not found, waiting anyway...")
                    except Exception as exc:
                        logger.warning("chatgpt_login: post-login OAuth navigation failed: %s", exc)

            # ── Step 7: Wait for redirect back to chatgpt.com ──
            session.elapsed_sec = time.time() - started_at
            _chatgpt_verify_reported = False
            for _ in range(20):  # up to ~60s
                await asyncio.sleep(3.0)
                session.elapsed_sec = time.time() - started_at
                for pg in page.context.pages:
                    try:
                        if pg.is_closed():
                            continue
                    except Exception:
                        continue
                    url = pg.url or ""

                    # ── Detect ChatGPT email verification page ──
                    # After Google login, ChatGPT may require a one-time code sent
                    # to the account email. This is different from Google 2FA.
                    if not _chatgpt_verify_reported:
                        try:
                            body = (await pg.locator("body").inner_text(timeout=1000)).lower()
                            _chatgpt_verify_hints = (
                                "verification code", "mã xác minh",
                                "check your email", "kiểm tra email",
                                "we sent a code", "chúng tôi đã gửi",
                                "enter the code", "nhập mã",
                                "verify your email", "xác minh email",
                                "one-time code", "mã dùng một lần",
                            )
                            if any(h in body for h in _chatgpt_verify_hints):
                                logger.warning("chatgpt_login: ChatGPT verification page detected! body_snippet=%s", body[:300])
                                session.state = "need_code"
                                session.message = "ChatGPT yeu cau ma xac minh email — nhap ma tu email"
                                _chatgpt_verify_reported = True
                                break
                        except Exception:
                            pass

                    if "chatgpt.com" in url and "auth" not in url and session.state != "need_code":
                        session.state = "success"
                        session.message = "Da redirect ve chatgpt.com"
                        break
                if session.state in ("success", "need_code"):
                    break

            # ── Step 8: Scrape JWT token ──
            if session.state == "success":
                token, captured, preview = await _scrape_chatgpt_token(page)
                if token:
                    session.access_token = token
                    session.captured_email = captured or session.email
                    session.access_token_preview = preview
                    session.message = f"Lay token thanh cong ({captured or session.email})"
                    logger.info("chatgpt_login: scraped token preview=%s", preview)
                else:
                    session.state = "failed"
                    session.error = "Login OK nhung khong scrape duoc JWT"
                    logger.warning("chatgpt_login: login OK but no JWT found")

            if session.state not in ("success", "failed"):
                session.state = "failed"
                session.error = "Het thoi gian ma chua hoan tat"

    except Exception as exc:
        logger.exception("chatgpt_login: onboard failed")
        session.state = "failed"
        session.error = str(exc)[:200]



async def _run_onboard_v2(session, password: str) -> None:
    """Onboard via accounts.google.com-DIRECT (captcha-wait + auto pwd/TOTP via
    auto_login.do_google_login_steps) then ChatGPT SSO (Continue-with-Google,
    no captcha since Google already logged in) then scrape token. Replaces the
    Auth0-first flow that hit Cloudflare. Proven flow from account #1."""
    try:
        async with pool.page(profile=session.profile, headless=False) as page:
            ctx = page.context
            from .auto_login import do_google_login_steps, click_google_oauth_consent
            # Step 1: Google login DIRECT — skipped in reuse_session mode, where
            # the profile already holds a Google session (multi-onboard / cross
            # provider). Step 2's ChatGPT SSO rides that existing session.
            if getattr(session, "reuse_session", False):
                session.state = "running"
                session.message = "Tai su dung Google session san co, bo qua login Google..."
                logger.info("onboard_v2: reuse_session — skipping Google login")
            else:
                # Xoá ĐÚNG cookie Google, giữ nguyên phần còn lại của hồ sơ.
                # Thay cho lệnh xoá cả `user-data-dir` trước đây: cùng đạt mục
                # đích "Google hiện lại màn đăng nhập", nhưng không vứt danh
                # tính thiết bị. `_run_onboard` (bản cũ, không còn được gọi) đã
                # làm đúng bước này từ trước — nay chuyển sang runner đang chạy.
                session.state = "running"
                session.message = "Dang xoa Google cookies cu..."
                for _gdom in ("https://accounts.google.com/", "https://google.com/",
                              "https://myaccount.google.com/", "https://mail.google.com/"):
                    try:
                        gc = await ctx.cookies(_gdom)
                        for c in gc:
                            try:
                                await ctx.clear_cookies(name=c.get("name"),
                                                        domain=c.get("domain", ""))
                            except Exception:
                                pass
                        if gc:
                            logger.info("onboard_v2: xoá %d cookie của %s", len(gc), _gdom)
                    except Exception as exc:
                        logger.warning("onboard_v2: xoá cookie %s hỏng: %s", _gdom, str(exc)[:80])

                session.state = "running"
                session.message = "Mo accounts.google.com (qua trang chu Google de tranh block)..."
                navigated = False
                try:
                    await page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=30_000)
                    await asyncio.sleep(2.0)
                    
                    # Force click via JS to bypass the 'Make Chrome your own' overlay
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
                except Exception as exc:
                    logger.warning("onboard_v2: google.com trick failed: %s", exc)
                
                if not navigated:
                    # Fallback to direct
                    try:
                        await page.goto("https://accounts.google.com/signin/v2/identifier?hl=en",
                                        wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        await page.goto("https://accounts.google.com/", wait_until="domcontentloaded", timeout=30_000)
                
                await asyncio.sleep(2.0)
                ok = await do_google_login_steps(session, page, ctx, password,
                                                 prefer_method=session.prefer_method or "auth")
                if not ok:
                    if session.state != "failed":
                        session.state = "failed"
                        session.error = session.error or "Google login failed"
                    session.completed_at = time.time()
                    return
            # Step 2: ChatGPT SSO via chatgpt.com (proper OAuth params, unlike
            # auth.openai.com/u/login which lacks client_id/redirect_uri).
            session.state = "running"
            session.message = "Dang nhap ChatGPT (Continue with Google)..."
            await page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(4.0)
            # Cloudflare "xác minh bảo mật" → chờ/click qua trước khi tìm nút.
            try:
                from .solvers.turnstile import pass_challenge
                await pass_challenge(page, timeout=45.0, log_prefix="chatgpt_login: ")
            except Exception:
                pass
            # Some variants land on the marketing page → click the login button.
            for _lsel in ('[data-testid="login-button"]', 'button:has-text("Log in")',
                          'button:has-text("Dang nhap")', 'button:has-text("Đăng nhập")'):
                try:
                    await page.locator(_lsel).first.click(timeout=3000)
                    await asyncio.sleep(3.0)
                    break
                except Exception:
                    continue
            # Click "Continue with Google" — button renders after redirect to
            # auth.openai.com; loop because timing varies.
            _g_clicked = False
            for _ in range(20):
                for _gname in (
                    "Tiếp tục với Google",
                    "Continue with Google",
                    "Continue with google",
                ):
                    try:
                        _el = page.get_by_role("button", name=_gname)
                        if await _el.count() > 0 and await _el.first.is_visible(timeout=800):
                            await _el.first.click(force=True)
                            logger.info("onboard_v2: clicked Google via role %r", _gname)
                            _g_clicked = True
                            break
                    except Exception:
                        continue
                if _g_clicked:
                    break
                for _gsel in _GOOGLE_BTN_SELECTORS:
                    try:
                        _el = page.locator(_gsel).first
                        if await _el.is_visible(timeout=800):
                            await _el.click()
                            logger.info("onboard_v2: clicked Continue-with-Google via %s", _gsel)
                            _g_clicked = True
                            break
                    except Exception:
                        continue
                if _g_clicked:
                    break
                await asyncio.sleep(1.5)
            await asyncio.sleep(3.0)
            # Google account picker / consent (logged-in session → auto-pick).
            await asyncio.sleep(3.0)
            # Step 2b+3: poll loop — handle Google account-picker / consent +
            # ChatGPT workspace picker, retry scrape until a DECODABLE token
            # appears. Scraping too early returned garbage (SSO not finished).
            import base64 as _b64x, json as _jsonx
            def _decodes_ok(t):
                try:
                    _p = t.split(".")[1]; _p += "=" * (-len(_p) % 4)
                    return bool(_jsonx.loads(_b64x.urlsafe_b64decode(_p)))
                except Exception:
                    return False
            token = None; email = None; preview = None
            for _poll in range(40):
                _u = page.url or ""
                # Early-exit nếu Google yêu cầu nhập mật khẩu — session hết hạn
                if "/challenge/pwd" in _u or "/v3/signin/challenge" in _u or "/signin/identifier" in _u:
                    logger.warning("onboard_v2: Google session expired — redirected to login/challenge: %s", _u[:100])
                    session.state = "failed"
                    session.error = "Google session het han — can dang nhap lai qua noVNC truoc khi tai dung"
                    session.completed_at = time.time()
                    return
                if "accounts.google.com" in _u:
                    try:
                        await page.evaluate(
                            """(em) => {
                                const els = Array.from(document.querySelectorAll('div[data-identifier],li,div[role=link],div[role=button],a,div[jsname]'));
                                for (const e of els) {
                                    const id = (e.getAttribute('data-identifier')||'').toLowerCase();
                                    if (id && id === em) { e.click(); return 'id'; }
                                }
                                for (const e of els) {
                                    if ((e.innerText||'').toLowerCase().includes(em)) { e.click(); return 'text'; }
                                }
                                return null;
                            }""", session.email.lower())
                    except Exception:
                        pass
                    try:
                        await click_google_oauth_consent(page, timeout=3)
                    except Exception:
                        pass
                if "chatgpt.com" in _u:
                    try:
                        await page.evaluate(
                            """(em) => {
                                const btns = Array.from(document.querySelectorAll('button,a,div[role=button]'));
                                for (const b of btns) { if ((b.innerText||'').toLowerCase().includes(em)) { b.click(); return 'email'; } }
                                for (const b of btns) { const t=(b.innerText||'').toLowerCase(); if (t.includes('personal')||t.includes('cá nhân')) { b.click(); return 'personal'; } }
                                return null;
                            }""", session.email.lower())
                    except Exception:
                        pass
                    try:
                        token, email, preview = await _scrape_chatgpt_token(page)
                    except Exception:
                        token = None
                    if token and _decodes_ok(token):
                        break
                    token = None
                session.message = "Dang hoan tat SSO ChatGPT... (%d)" % _poll
                await asyncio.sleep(3.0)
            if token and _decodes_ok(token):
                session.access_token = token
                session.access_token_preview = preview
                session.captured_email = email or session.email
                session.state = "success"
                session.message = "Dang nhap thanh cong"
                try:
                    from .main import _update_chatgpt2api_token as _upd
                    await _upd(token)
                except Exception:
                    pass
            else:
                session.state = "failed"
                session.error = "ChatGPT SSO chua hoan tat (token rac/khong co) - kiem tra noVNC"
            session.completed_at = time.time()
    except Exception as exc:
        session.state = "failed"
        session.error = ("onboard_v2 error: %s" % exc)[:200]
        session.completed_at = time.time()
        logger.exception("onboard_v2 failed")
    
    # Close browser after SSO so it doesn't linger in noVNC.
    # `bo_qua_khi_dang_nhap=True`: đây CHÍNH LÀ dòng đã giết tầng T3 lúc
    # 05:48:57 ngày 10/08/2026 — lượt onboard này kết thúc và đóng hồ sơ đúng 2
    # giây sau khi lượt đăng nhập Google vừa nhận context.
    if session.state in ("failed", "success"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
            logger.info("closed browser after chatgpt onboard profile=%s state=%s", session.profile, session.state)
        except Exception:
            logger.debug("close_profile after chatgpt onboard skipped", exc_info=True)

