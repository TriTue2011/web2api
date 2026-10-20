# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/github_codex_onboard.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
import asyncio
import imaplib
import email
import re
import time
import email.utils
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel
from .browser_pool import pool

logger = logging.getLogger(__name__)

class CodexOnboardReq(BaseModel):
    auth_url: str
    github_email: str
    github_password: str
    gmail_email: str
    gmail_app_password: str

# Senders we accept for verification codes (GitHub / OpenAI / Microsoft).
_CODE_SENDER_HINTS = (
    "github.com",
    "openai.com",
    "microsoft.com",
    "microsoftonline.com",
    "accountprotection.microsoft",
    "live.com",
    "outlook.com",
    "account.live.com",
)


def _message_body(msg: email.message.Message) -> str:
    content = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    raw = part.get_payload(decode=True)
                    if raw:
                        try:
                            content += raw.decode(errors="ignore") + "\n"
                        except Exception:
                            pass
        else:
            raw = msg.get_payload(decode=True)
            if raw:
                content = raw.decode(errors="ignore")
    except Exception:
        content = str(msg)
    return content


def _extract_code(content: str) -> Optional[str]:
    """Prefer standalone 6–8 digit codes (Microsoft often 6 or 7 digits)."""
    # Avoid matching years / long numbers embedded in URLs (#hex etc.)
    for pat in (
        r"(?:code|mã|ma|security code|verification code|one-time)[^\d]{0,40}(\d{6,8})\b",
        r"(?<!#)\b(\d{6,8})\b",
        r"(?<!#)\b(\d{6})\b",
    ):
        m = re.search(pat, content, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


async def _fetch_imap_code(
    gmail_email: str,
    gmail_app_password: str,
    since_timestamp: float,
    target_email: str,
    max_wait: int = 120,
) -> Optional[str]:
    """Poll shared Gmail IMAP for a verification code destined to target_email.

    Used for OpenAI passwordless, GitHub device verify, and Microsoft one-time codes.
    Account rows may omit per-line IMAP — caller passes the shared IMAP credentials.
    """
    deadline = time.time() + max_wait
    target_l = (target_email or "").lower()
    while time.time() < deadline:
        try:
            def do_imap():
                mail = imaplib.IMAP4_SSL("imap.gmail.com")
                mail.login(gmail_email, gmail_app_password)
                mail.select("inbox")
                # Prefer UNSEEN; fall back to recent ALL if nothing unread yet
                status, messages = mail.search(None, "(UNSEEN)")
                ids = messages[0].split() if messages and messages[0] else []
                if not ids:
                    status, messages = mail.search(None, "ALL")
                    ids = messages[0].split() if messages and messages[0] else []
                if not ids:
                    mail.logout()
                    return None

                code_match = None
                for msg_id in ids[::-1][:25]:  # newest first
                    status, data = mail.fetch(msg_id, "(BODY.PEEK[])")
                    if not data or not data[0] or not isinstance(data[0], tuple):
                        continue
                    msg = email.message_from_bytes(data[0][1])

                    date_header = msg.get("Date")
                    if date_header:
                        try:
                            msg_date = email.utils.parsedate_to_datetime(date_header)
                            if msg_date.tzinfo is None:
                                msg_date = msg_date.replace(tzinfo=timezone.utc)
                            if msg_date.timestamp() < since_timestamp - 60:
                                continue
                        except Exception:
                            pass

                    sender = str(msg.get("From", "")).lower()
                    if not any(h in sender for h in _CODE_SENDER_HINTS):
                        continue

                    full = str(msg).lower() + "\n" + _message_body(msg).lower()
                    # Forwarded mail: target outlook/github address appears in body/To
                    if target_l and target_l not in full:
                        continue

                    content = _message_body(msg)
                    code = _extract_code(content)
                    if code:
                        code_match = code
                        logger.info(
                            "FOUND CODE %s for %s | TO=%s FROM=%s",
                            code_match, target_email, msg.get("To"), msg.get("From"),
                        )
                        try:
                            mail.store(msg_id, "+FLAGS", "\\Seen")
                        except Exception:
                            pass
                        break

                mail.logout()
                return code_match

            code = await asyncio.to_thread(do_imap)
            if code:
                return code
        except Exception as e:
            logger.warning("IMAP Error: %s", str(e))
        await asyncio.sleep(4)
    return None


async def _microsoft_click_onetime_code(page) -> bool:
    """On Microsoft password screen, switch to one-time code login if available."""
    selectors = [
        'a#idA_PWD_SwitchToOTC',
        '#idA_PWD_SwitchToOTC',
        'a[id*="SwitchToOTC"]',
        'a:has-text("Đăng nhập bằng mã dùng một lần")',
        'span:has-text("Đăng nhập bằng mã dùng một lần")',
        'button:has-text("Đăng nhập bằng mã dùng một lần")',
        'div[role="button"]:has-text("Đăng nhập bằng mã dùng một lần")',
        'a:has-text("Sign in with a one-time code")',
        'a:has-text("Use a one-time code")',
        'a:has-text("Send a code")',
        'a:has-text("Use your verification code")',
        'a:has-text("verification code")',
        'a:has-text("mã dùng một lần")',
        'a:has-text("one-time code")',
        'a:has-text("one time code")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = await loc.count()
            if n <= 0:
                continue
            el = loc.first
            try:
                await el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            # force=True: Microsoft sometimes overlays the pill button
            try:
                await el.click(timeout=5000, force=True)
            except Exception:
                try:
                    await el.evaluate("e => e.click()")
                except Exception:
                    continue
            logger.info("Microsoft: clicked one-time code option (%s)", sel)
            await asyncio.sleep(2.5)
            return True
        except Exception:
            continue
    # JS sweep: any clickable containing OTC text
    try:
        clicked = await page.evaluate(
            """() => {
              const re = /mã dùng một lần|one-time code|one time code|verification code|send a code/i;
              const nodes = Array.from(document.querySelectorAll('a,button,span,div[role="button"]'));
              for (const n of nodes) {
                const t = (n.innerText || n.textContent || '').trim();
                if (t && re.test(t) && t.length < 80) {
                  n.click();
                  return t;
                }
              }
              return null;
            }"""
        )
        if clicked:
            logger.info("Microsoft: JS-clicked OTC control text=%r", clicked)
            await asyncio.sleep(2.5)
            return True
    except Exception as exc:
        logger.warning("Microsoft: JS OTC click failed: %s", exc)
    # Fallback: get_by_role
    try:
        for role in ("link", "button"):
            loc = page.get_by_role(role, name=re.compile(
                r"mã dùng một lần|one-time code|one time code|verification code|send a code",
                re.I,
            ))
            if await loc.count() > 0:
                await loc.first.click(force=True)
                await asyncio.sleep(2.5)
                return True
    except Exception:
        pass
    return False


def _is_ms_url(url: str) -> bool:
    u = (url or "").lower()
    return any(
        h in u
        for h in (
            "login.live.com",
            "login.microsoftonline.com",
            "login.microsoft.com",
            "account.live.com",
            "signup.live.com",
            "account.microsoft.com",
        )
    )


def _page_looks_ms_password(content: str, url: str) -> bool:
    c = content or ""
    return _is_ms_url(url) or any(
        s in c
        for s in (
            "Nhập mật khẩu của bạn",
            "Nhập mật khẩu",
            "Enter your password",
            "Enter password",
            "idA_PWD_SwitchToOTC",
            "Đăng nhập bằng mã dùng một lần",
            "Sign in with a one-time code",
        )
    )


def _page_looks_deactivated(content: str, url: str) -> bool:
    """OpenAI 'Lỗi xác thực' — tài khoản đã bị xóa/vô hiệu hóa (account_deactivated).

    Đây là lỗi VĨNH VIỄN: OAuth callback sẽ KHÔNG BAO GIỜ tới, nên phải thoát
    ngay (đóng tab + sang account kế) thay vì treo hết thời gian chờ OAuth.
    Bắt theo đúng error_code OpenAI render trên trang."""
    c = content or ""
    cl = c.lower()
    return (
        "account_deactivated" in cl
        or "đã bị xóa hoặc vô hiệu hóa" in c
        or "has been deleted or deactivated" in cl
    )


async def _handle_microsoft_otc_flow(page, req: "CodexOnboardReq") -> dict[str, Any] | None:
    """Drive Microsoft password → OTC → IMAP code. None = continue main loop; dict = terminal fail."""
    if not (req.gmail_email and req.gmail_app_password):
        return {
            "state": "failed",
            "error": "Microsoft OTC cần IMAP Gmail dùng chung (điền ở form hàng loạt)",
        }

    # Email step (if still shown)
    if await page.locator('input[type="email"], input[name="loginfmt"]').count() > 0:
        try:
            await page.fill('input[type="email"], input[name="loginfmt"]', req.github_email)
            await page.click(
                'input[type="submit"], button:has-text("Next"), button:has-text("Tiếp"), '
                'input[value="Next"], input[value="Tiếp theo"]'
            )
            await asyncio.sleep(2.5)
        except Exception as exc:
            logger.warning("Microsoft email step: %s", exc)

    content = ""
    url = ""
    try:
        content = await page.content()
    except Exception:
        pass
    try:
        url = page.url or ""
    except Exception:
        url = ""
    try:
        on_pwd = await page.locator('input[type="password"], input[name="passwd"]').count() > 0
    except Exception:
        on_pwd = False
    if not (on_pwd or _page_looks_ms_password(content, url)):
        return None  # not on MS password screen yet

    logger.info("Microsoft password screen detected for %s — switching to OTC", req.github_email)
    switched = await _microsoft_click_onetime_code(page)
    if not switched:
        logger.warning("Microsoft: OTC link not found for %s — password fallback", req.github_email)
        if await page.locator('input[type="password"], input[name="passwd"]').count() > 0:
            await page.fill('input[type="password"], input[name="passwd"]', req.github_password)
            await page.click(
                'input[type="submit"], button:has-text("Sign in"), '
                'button:has-text("Đăng nhập"), button:has-text("Tiếp tục"), input[value="Sign in"]'
            )
            await asyncio.sleep(2.5)
        return None

    # After OTC click: maybe "Send code" page, then code entry
    request_time = time.time() - 5
    for send_sel in (
        'button:has-text("Send code")',
        'button:has-text("Gửi mã")',
        'input[type="submit"][value="Next"]',
        'button:has-text("Next")',
        'button:has-text("Tiếp")',
        'button:has-text("Continue")',
        'button:has-text("Tiếp tục")',
    ):
        try:
            # Only click send if password field is gone (we left password form)
            if await page.locator('input[type="password"]').count() > 0:
                break
            loc = page.locator(send_sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                # Avoid clicking "Tiếp tục" on empty code form — only if no otc input yet
                if await page.locator('input[name="otc"], input#iOttText, input[autocomplete="one-time-code"]').count() > 0:
                    break
                await loc.first.click()
                await asyncio.sleep(2.0)
                break
        except Exception:
            pass

    logger.info("Microsoft: waiting for OTC via shared Gmail IMAP (%s)...", req.gmail_email)
    code = await _fetch_imap_code(
        req.gmail_email, req.gmail_app_password, request_time, req.github_email, max_wait=150,
    )
    if not code:
        return {
            "state": "failed",
            "error": (
                f"Không lấy được mã Microsoft (one-time) từ IMAP Gmail "
                f"({req.gmail_email}) cho {req.github_email}. "
                f"Kiểm tra forward Outlook→Gmail + App Password."
            ),
        }
    logger.info("Microsoft: got OTC %s for %s", code, req.github_email)
    if not await _microsoft_fill_otp(page, code):
        try:
            await page.locator('input:visible').first.press_sequentially(code, delay=80)
            await page.keyboard.press("Enter")
            await asyncio.sleep(2.5)
        except Exception as exc:
            return {"state": "failed", "error": f"Có mã {code} nhưng không điền form OTC: {exc}"}

    # Stay signed in?
    for stay in (
        'input[type="submit"][value="Yes"]',
        'button:has-text("Yes")',
        'button:has-text("Có")',
        '#idSIButton9',
    ):
        try:
            if await page.locator(stay).count() > 0 and await page.locator(stay).first.is_visible():
                await page.locator(stay).first.click()
                await asyncio.sleep(2.0)
                break
        except Exception:
            pass
    return None  # success path continues main loop


async def _microsoft_fill_otp(page, code: str) -> bool:
    """Fill Microsoft one-time code field and submit."""
    code_selectors = [
        'input[name="otc"]',
        'input#otc',
        'input[name="iOttText"]',
        'input#iOttText',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[type="tel"]',
        'input[aria-label*="code" i]',
        'input[aria-label*="mã" i]',
        'input[placeholder*="code" i]',
        'input[placeholder*="mã" i]',
    ]
    for sel in code_selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.fill("")
                await loc.first.press_sequentially(code, delay=80)
                await asyncio.sleep(0.5)
                # Submit
                for btn in (
                    'input[type="submit"]',
                    'button[type="submit"]',
                    'button:has-text("Verify")',
                    'button:has-text("Xác minh")',
                    'button:has-text("Next")',
                    'button:has-text("Tiếp")',
                    'button:has-text("Continue")',
                    'button:has-text("Sign in")',
                    'button:has-text("Đăng nhập")',
                ):
                    if await page.locator(btn).count() > 0:
                        await page.locator(btn).first.click()
                        break
                else:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(2.5)
                return True
        except Exception:
            continue
    return False

async def run_codex_onboard(req: CodexOnboardReq) -> dict[str, Any]:
    profile = f'codex-{req.github_email.split("@")[0]}'
    ctx = await pool.get(profile=profile, headless=False, force_recreate=True)
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    # Listen at request level - captures URL BEFORE browser tries to connect
    # This works even when localhost returns ERR_CONNECTION_REFUSED
    captured_callback_url: list[str] = []

    def _maybe_capture(url: str, source: str = "request") -> None:
        if not url or "code=" not in url:
            return
        # OAuth redirect: localhost / 127.0.0.1 / custom callback with code=
        if any(
            h in url
            for h in (
                "localhost",
                "127.0.0.1",
                "0.0.0.0",
                "callback",
                "oauth",
                "redirect",
            )
        ) or ("code=" in url and "state=" in url):
            if url not in captured_callback_url:
                logger.info("Captured OAuth URL via %s: %s", source, url[:200])
                captured_callback_url.append(url)

    def on_request(request):
        _maybe_capture(request.url, "request")

    def on_response(response):
        try:
            _maybe_capture(response.url, "response")
            # Follow redirects
            if response.status in (301, 302, 303, 307, 308):
                loc = response.headers.get("location") or ""
                _maybe_capture(loc, "redirect")
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    is_ms_account = any(
        d in req.github_email.lower()
        for d in ("outlook.com", "hotmail.com", "live.com", "msn.com")
    )
    openai_email_submitted = False
    github_login_done = False
    ms_otc_done = False
    openai_otp_done = False

    try:
        await page.goto(req.auth_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2.0)

        # State machine: poll until OAuth callback or timeout.
        # Critical: Outlook often lands on Microsoft password after OpenAI email
        # step — must NOT treat that as OpenAI "check your email" and hang on IMAP.
        loop_deadline = time.time() + 240
        while time.time() < loop_deadline and not captured_callback_url:
            try:
                url = page.url or ""
                content = await page.content()
            except Exception:
                await asyncio.sleep(1.0)
                continue

            # ── Tài khoản bị xóa/vô hiệu hóa (OpenAI 'Lỗi xác thực') ──
            # Lỗi vĩnh viễn → không đợi OAuth callback nữa. Thoát ngay để caller
            # đóng tab (finally: close_profile) và chuyển sang tài khoản kế.
            if _page_looks_deactivated(content, url):
                logger.warning({
                    "event": "codex_onboard_account_deactivated",
                    "email": (req.github_email or "")[:80],
                    "url": (url or "")[:120],
                })
                return {
                    "state": "failed",
                    "error": "account_deactivated",
                    "error_code": "account_deactivated",
                    "email": req.github_email,
                }

            # ── Microsoft password / OTC (highest priority for Outlook) ──
            if (not ms_otc_done) and (
                _is_ms_url(url)
                or _page_looks_ms_password(content, url)
                or (
                    await page.locator('input[type="password"], input[name="passwd"]').count() > 0
                    and (
                        "Đăng nhập bằng mã dùng một lần" in content
                        or "one-time code" in content.lower()
                        or "idA_PWD_SwitchToOTC" in content
                    )
                )
            ):
                logger.info("State=microsoft_password url=%s", url[:120])
                fail = await _handle_microsoft_otc_flow(page, req)
                if fail:
                    return fail
                ms_otc_done = True
                await asyncio.sleep(1.5)
                continue

            # ── OpenAI auth screen ──
            if "auth.openai.com" in url or "chatgpt.com" in url:
                try:
                    # PRIORITY: "Tiếp tục với Google" on welcome-back /log-in
                    # (same bug as codex_google_onboard: don't treat as tile-only chooser).
                    if (
                        "Tiếp tục với Google" in content
                        or "Continue with Google" in content
                    ) and not is_ms_account:
                        gbtn = page.locator(
                            'button:has-text("Tiếp tục với Google"), '
                            'button:has-text("Continue with Google"), '
                            'button[data-provider="google"]'
                        ).first
                        clicked_g = False
                        if await gbtn.count() > 0:
                            logger.info("OpenAI: priority click Tiếp tục với Google url=%s", url[:100])
                            try:
                                await gbtn.click(force=True, timeout=5000)
                            except Exception:
                                await gbtn.evaluate("e => e.click()")
                            clicked_g = True
                        else:
                            try:
                                clicked_g = bool(await page.evaluate(
                                    """() => {
                                      const re = /tiếp\\s*tục\\s*với\\s*google|continue\\s*with\\s*google/i;
                                      for (const n of document.querySelectorAll('button,a,[role="button"]')) {
                                        const t = (n.innerText||'').trim();
                                        if (re.test(t)) { n.click(); return true; }
                                      }
                                      return false;
                                    }"""
                                ))
                            except Exception:
                                clicked_g = False
                        if clicked_g:
                            await page.wait_for_load_state("domcontentloaded")
                            await asyncio.sleep(3.0)
                            continue

                    # Welcome-back / choose-an-account TILES only (no Google btn path)
                    # Gmail + Outlook: target email in list → click tile; else "another account"
                    if (
                        "choose-an-account" in url
                        or "Chọn một tài khoản" in content
                        or (
                            ("Chào mừng trở lại" in content or "Welcome back" in content)
                            and "Tiếp tục với Google" not in content
                            and "Continue with Google" not in content
                        )
                    ):
                        target = (req.github_email or "").strip()
                        try:
                            pick = await page.evaluate(
                                """(email) => {
                                  const want = (email || '').toLowerCase().replace(/\\s+/g, '');
                                  const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, '');
                                  const nodes = Array.from(
                                    document.querySelectorAll(
                                      'button, a, [role="button"], [role="link"], div[data-identifier], li, div[data-email]'
                                    )
                                  );
                                  if (want) {
                                    for (const n of nodes) {
                                      const id = norm(
                                        n.getAttribute('data-identifier') ||
                                        n.getAttribute('data-email') || ''
                                      );
                                      if (id === want) {
                                        n.click();
                                        return { action: 'tile', via: 'data-id' };
                                      }
                                    }
                                    for (const n of nodes) {
                                      const t = norm(n.innerText || n.textContent || '');
                                      if (t && t.length < 200 && t.includes(want)) {
                                        n.click();
                                        return { action: 'tile', via: 'text' };
                                      }
                                    }
                                  }
                                  const reOther = /đăng nhập vào tài khoản khác|log in to another account|use another account|sử dụng một tài khoản khác/i;
                                  for (const n of nodes) {
                                    const t = (n.innerText || n.textContent || '')
                                      .trim().replace(/\\s+/g, ' ');
                                    if (reOther.test(t) && t.length < 80) {
                                      n.click();
                                      return { action: 'other', text: t };
                                    }
                                  }
                                  return { action: 'none' };
                                }""",
                                target,
                            )
                            if pick and pick.get("action") == "tile":
                                logger.info(
                                    "OpenAI: clicked existing account tile %s (via %s)",
                                    target,
                                    pick.get("via"),
                                )
                                await asyncio.sleep(3.0)
                                continue
                            if pick and pick.get("action") == "other":
                                logger.info(
                                    "OpenAI: target %r not in list → another account (%r)",
                                    target,
                                    pick.get("text"),
                                )
                                await asyncio.sleep(2.5)
                                continue
                        except Exception as e:
                            logger.warning("OpenAI chooser pick-or-other: %s", e)

                    if is_ms_account:
                        # Email on OpenAI — then expect Microsoft redirect
                        if (
                            not openai_email_submitted
                            and await page.locator(
                                'input[type="email"], input[name="email"]'
                            ).count()
                            > 0
                        ):
                            logger.info(
                                "OpenAI: submit Outlook email %s then wait for Microsoft…",
                                req.github_email,
                            )
                            await page.fill(
                                'input[type="email"], input[name="email"]',
                                req.github_email,
                            )
                            await page.click(
                                'button[type="submit"], button:has-text("Tiếp tục"), '
                                'button:has-text("Continue")'
                            )
                            openai_email_submitted = True
                            await asyncio.sleep(4.0)
                            continue

                        # Real OpenAI email OTP only (NOT Microsoft password page)
                        if (
                            not openai_otp_done
                            and (
                                "Kiểm tra hộp thư" in content
                                or "Check your email" in content
                                or "Check your inbox" in content
                            )
                            and await page.locator(
                                'input[inputmode="numeric"]'
                            ).count()
                            > 0
                            and await page.locator('input[type="password"]').count()
                            == 0
                        ):
                            logger.info("OpenAI: waiting for email code via IMAP…")
                            code = await _fetch_imap_code(
                                req.gmail_email,
                                req.gmail_app_password,
                                time.time() - 30,
                                req.github_email,
                            )
                            if not code:
                                return {
                                    "state": "failed",
                                    "error": "Could not fetch OpenAI code from Gmail IMAP",
                                }
                            await page.locator(
                                'input[inputmode="numeric"]'
                            ).first.press_sequentially(code, delay=100)
                            await page.click(
                                'button[type="submit"], button:has-text("Tiếp tục"), '
                                'button:has-text("Continue")'
                            )
                            openai_otp_done = True
                            await asyncio.sleep(3.0)
                            continue
                    else:
                        # Gmail: log-in-or-create → "Tiếp tục với Google"
                        if (
                            "log-in-or-create" in url
                            or "Đăng nhập hoặc đăng ký" in content
                            or "Tiếp tục với Google" in content
                            or "Continue with Google" in content
                        ):
                            gbtn = page.locator(
                                'button:has-text("Tiếp tục với Google"), '
                                'button:has-text("Continue with Google"), '
                                'button[data-provider="google"], '
                                'button:has-text("Google")'
                            ).first
                            if await gbtn.count() > 0:
                                logger.info("OpenAI: clicking Tiếp tục với Google")
                                try:
                                    await gbtn.click(force=True)
                                except Exception:
                                    await gbtn.evaluate("e => e.click()")
                                await page.wait_for_load_state("domcontentloaded")
                                await asyncio.sleep(3.0)
                                continue
                            # JS fallback
                            try:
                                await page.evaluate(
                                    """() => {
                                      const re = /tiếp tục với google|continue with google/i;
                                      for (const n of document.querySelectorAll('button,a')) {
                                        if (re.test((n.innerText||'').trim())) { n.click(); return true; }
                                      }
                                      return false;
                                    }"""
                                )
                                await asyncio.sleep(3.0)
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning("OpenAI auth step: %s", e)

            # ── Google accountchooser (after Tiếp tục với Google) ──
            # Same rule as OpenAI chooser: tile if present, else another account
            if "accounts.google.com" in url and (
                "accountchooser" in url
                or "Chọn tài khoản" in content
                or "Choose an account" in content
                or "Sử dụng một tài khoản khác" in content
            ):
                target = (req.github_email or "").strip()
                try:
                    pick = await page.evaluate(
                        """(email) => {
                          const want = (email || '').toLowerCase().replace(/\\s+/g, '');
                          const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, '');
                          const nodes = Array.from(
                            document.querySelectorAll(
                              'button, a, [role="button"], [role="link"], div[data-identifier], li, div[data-email]'
                            )
                          );
                          if (want) {
                            for (const n of nodes) {
                              const id = norm(
                                n.getAttribute('data-identifier') ||
                                n.getAttribute('data-email') || ''
                              );
                              if (id === want) {
                                n.click();
                                return { action: 'tile', via: 'data-id' };
                              }
                            }
                            for (const n of nodes) {
                              const t = norm(n.innerText || n.textContent || '');
                              if (t && t.length < 200 && t.includes(want)) {
                                n.click();
                                return { action: 'tile', via: 'text' };
                              }
                            }
                          }
                          const reOther = /sử dụng một tài khoản khác|use another account|đăng nhập vào tài khoản khác|log in to another account/i;
                          for (const n of nodes) {
                            const t = (n.innerText || n.textContent || '')
                              .trim().replace(/\\s+/g, ' ');
                            if (reOther.test(t) && t.length < 80) {
                              n.click();
                              return { action: 'other', text: t };
                            }
                          }
                          for (const n of document.querySelectorAll('div, li, span')) {
                            const t = (n.innerText || '').trim().replace(/\\s+/g, ' ');
                            if (reOther.test(t) && t.length < 60 && n.children.length < 4) {
                              n.click();
                              return { action: 'other', text: t, via: 'broad' };
                            }
                          }
                          return { action: 'none' };
                        }""",
                        target,
                    )
                    if pick and pick.get("action") == "tile":
                        logger.info(
                            "Google chooser: clicked %s (via %s)",
                            target,
                            pick.get("via"),
                        )
                    elif pick and pick.get("action") == "other":
                        logger.info(
                            "Google chooser: target %r not in list → another account (%r)",
                            target,
                            pick.get("text"),
                        )
                    else:
                        logger.info(
                            "Google chooser: no tile for %r and no other-account btn",
                            target,
                        )
                except Exception as e:
                    logger.warning("Google chooser pick-or-other: %s", e)
                await asyncio.sleep(2.5)

            # ── GitHub login ──
            if (not github_login_done) and "github.com/login" in url:
                try:
                    await page.fill('input[name="login"]', req.github_email)
                    await page.fill('input[name="password"]', req.github_password)
                    await page.click('input[name="commit"]')
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(2.0)
                    content = await page.content()
                    if (
                        "Device Verification" in content
                        or await page.locator("#otp").count() > 0
                    ):
                        code = await _fetch_imap_code(
                            req.gmail_email,
                            req.gmail_app_password,
                            time.time() - 30,
                            req.github_email,
                        )
                        if not code:
                            return {
                                "state": "failed",
                                "error": "Could not fetch GitHub code from Gmail IMAP",
                            }
                        await page.locator("#otp").first.press_sequentially(
                            code, delay=100
                        )
                        if await page.locator('button:has-text("Verify")').count() > 0:
                            await page.click('button:has-text("Verify")')
                        await asyncio.sleep(2.0)
                    github_login_done = True
                except Exception as e:
                    logger.warning("GitHub login step: %s", e)

            # ── Consent / "Continue as email" (post-login, before OAuth callback) ──
            # Path A (existing OpenAI session tile on choose-an-account):
            #   click tile → /sign-in-with-chatgpt/codex/consent (email pill + Hủy + Tiếp tục)
            #   → click Tiếp tục → redirect localhost:1455/auth/callback?code=...
            # Path B (Google/new login) ends on the same consent URL — same Tiếp tục + capture.
            try:
                title = await page.title()
                if (
                    "Authorize" in title
                    or await page.locator(
                        'button:has-text("Authorize"), button[name="authorize"]'
                    ).count()
                    > 0
                ):
                    auth_btn = page.locator(
                        'button:has-text("Authorize"), button[name="authorize"]'
                    ).first
                    if await auth_btn.count() > 0:
                        logger.info("Clicking GitHub/OpenAI Authorize…")
                        await auth_btn.click()
                        await asyncio.sleep(2.0)

                is_codex_consent = (
                    "/codex/consent" in url
                    or "sign-in-with-chatgpt" in url
                    or (
                        ("Codex" in content or "ChatGPT" in content or "openai.com" in url)
                        and (
                            "Hủy" in content
                            or "Cancel" in content
                            or (
                                req.github_email
                                and req.github_email.split("@")[0].lower()
                                in content.lower()
                            )
                        )
                    )
                )
                has_continue = (
                    await page.locator(
                        'button:has-text("Tiếp tục"), button:has-text("Continue"), '
                        'button[type="submit"]'
                    ).count()
                    > 0
                )
                no_login_fields = (
                    await page.locator('input[type="password"]').count() == 0
                    and await page.locator('input[type="email"]').count() == 0
                )
                # Do not treat log-in-or-create ("Tiếp tục với Google") as consent
                is_google_entry = (
                    "log-in-or-create" in url
                    or "Tiếp tục với Google" in content
                    or "Continue with Google" in content
                )
                looks_continue_account = (
                    is_codex_consent
                    and has_continue
                    and no_login_fields
                    and not is_google_entry
                    and "choose-an-account" not in url
                )
                if looks_continue_account:
                    cont = page.locator(
                        'button:has-text("Tiếp tục"), button:has-text("Continue")'
                    ).last
                    if await cont.count() > 0:
                        logger.info(
                            "State=codex_consent email=%s url=%s — clicking Tiếp tục",
                            req.github_email,
                            url[:100],
                        )
                        try:
                            await cont.click(force=True)
                        except Exception:
                            await cont.evaluate("e => e.click()")
                        await asyncio.sleep(3.0)
                        # After continue, OAuth redirects to localhost:1455?code=
                        for _ in range(20):
                            if captured_callback_url:
                                break
                            await asyncio.sleep(0.5)
                        continue

                consent_btn = page.locator(
                    'button[value="accept"], button:has-text("Authorize"), '
                    'button:has-text("Allow"), button:has-text("Chấp nhận"), '
                    'button:has-text("Đồng ý"), button:has-text("Tiếp tục"), '
                    'button:has-text("Continue")'
                ).first
                if (
                    ("Codex" in content or "ChatGPT" in content)
                    and await consent_btn.count() > 0
                    and await page.locator('input[type="password"]').count() == 0
                    and not is_google_entry
                    and "choose-an-account" not in url
                ):
                    primary = page.locator(
                        'button:has-text("Tiếp tục"), button:has-text("Continue")'
                    )
                    if await primary.count() > 0:
                        logger.info("Clicking consent Tiếp tục/Continue…")
                        await primary.last.click(force=True)
                    else:
                        await consent_btn.click(force=True)
                    await asyncio.sleep(3.0)
                    for _ in range(20):
                        if captured_callback_url:
                            break
                        await asyncio.sleep(0.5)
            except Exception as exc:
                logger.warning("Consent/continue step: %s", exc)

            # Also check page URL / frames for code= if interceptor missed
            try:
                if "code=" in (page.url or "") and (
                    "localhost" in page.url or "127.0.0.1" in page.url or "callback" in page.url
                ):
                    captured_callback_url.append(page.url)
                    logger.info("Captured callback from page.url: %s", page.url[:160])
            except Exception:
                pass

            await asyncio.sleep(1.0)

        if not captured_callback_url:
            # Last chance: current URL or any page with code=
            try:
                current_url = page.url or ""
                if "code=" in current_url:
                    captured_callback_url.append(current_url)
            except Exception:
                pass
        if not captured_callback_url:
            # Safe URL access for error message (may have thrown above)
            stuck_url = ""
            try:
                stuck_url = page.url or "(unable to read)"
            except Exception:
                stuck_url = "(page unavailable)"
            return {
                "state": "failed",
                "error": f"Timeout waiting for OAuth redirect. Stuck at: {stuck_url}",
            }

        final_url = captured_callback_url[0]
        logger.info("Codex onboard success, final_url: %s", final_url)
        return {"state": "success", "redirect_url": final_url}

    except Exception as e:
        logger.exception('Codex onboard error')
        return {'state': 'failed', 'error': str(e)}
    finally:
        # Robust close: ensure next account's browser can launch even if this one crashed.
        try:
            await pool.close_profile(profile, bo_qua_khi_dang_nhap=True)
        except Exception as close_err:
            logger.warning({'event': 'codex_onboard_close_profile_error', 'profile': profile, 'error': str(close_err)[:160]})
