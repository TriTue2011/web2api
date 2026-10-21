# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/turnstile.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Cloudflare Turnstile token extractor.

Strategy (in order):
  1. Open URL with the persistent profile so cf_clearance cookies are reused.
  2. Wait for the widget to publish a token to the hidden input. Many sites
     auto-pass once cookies are warm — this is the fast path (~1-3 s).
  3. If no token after a few seconds, locate the Turnstile iframe and click
     the visible checkbox (handles "managed" challenges where one click is
     enough to satisfy the challenge).
  4. If still no token within the soft-timeout, fall back to 2captcha/
     CapSolver (only when CAPTCHA_SOLVER_2CAPTCHA_KEY is set).
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..browser_pool import pool
from ..settings import settings
from . import twocaptcha

logger = logging.getLogger(__name__)


async def _try_click_checkbox(page) -> bool:
    """Tích ô "Xác minh bạn là con người" của Turnstile.

    Widget nằm trong iframe challenges.cloudflare.com. BẪY: thẻ
    `input[type=checkbox]` thật bị Cloudflare ẩn (opacity:0, kích thước ~0) —
    thứ người dùng thấy là lớp style phủ trên. Vì vậy KHÔNG chờ
    `state="visible"` (luôn timeout → bỏ qua click âm thầm), mà thử lần lượt:
      1. click cưỡng bức (force) vào chính input — bỏ qua kiểm tra hiển thị;
      2. click nhãn/khung trong iframe (managed challenge đôi khi chỉ có label);
      3. click theo TỌA ĐỘ vào ô vuông bên trái iframe (cách cuối, luôn được
         khi DOM bên trong bị che hoàn toàn).
    """
    frame_sel = "iframe[src*='challenges.cloudflare.com']"

    # 1) input thật, click cưỡng bức (không đòi visible)
    try:
        cb = page.frame_locator(frame_sel).locator("input[type='checkbox']").first
        await cb.wait_for(state="attached", timeout=3000)
        await cb.click(timeout=3000, force=True)
        logger.info("turnstile: đã click input[checkbox] (force)")
        return True
    except Exception as exc:
        logger.debug("turnstile: click input thất bại: %s", exc)

    # 2) nhãn / khung bên trong iframe
    for sel in ("label", "#challenge-stage", "div.cb-lb", "body"):
        try:
            el = page.frame_locator(frame_sel).locator(sel).first
            await el.wait_for(state="attached", timeout=1500)
            await el.click(timeout=2500, force=True)
            logger.info("turnstile: đã click %r trong iframe", sel)
            return True
        except Exception:
            continue

    # 3) tọa độ: ô vuông nằm sát lề trái, giữa theo chiều dọc của iframe
    try:
        box = await page.locator(frame_sel).first.bounding_box()
        if box and box["width"] > 20 and box["height"] > 10:
            await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
            logger.info("turnstile: đã click theo tọa độ iframe (%.0f,%.0f)",
                        box["x"] + 28, box["y"] + box["height"] / 2)
            return True
    except Exception as exc:
        logger.debug("turnstile: click tọa độ thất bại: %s", exc)

    logger.warning("turnstile: KHÔNG click được ô xác minh (cả 3 cách)")
    return False


async def is_challenge_showing(page) -> bool:
    """Trang đang bị Cloudflare chặn (Turnstile / "Xác minh bạn là con người")?"""
    try:
        if await page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0:
            return True
    except Exception:
        pass
    try:
        return bool(await page.evaluate(
            """() => {
                if (document.querySelector('#cf-chl-widget, .cf-turnstile, #challenge-form')) return true;
                const t = (document.body && document.body.innerText || '').toLowerCase();
                return t.includes('verify you are human')
                    || t.includes('xác minh bạn là con người')
                    || t.includes('thực hiện xác minh bảo mật')
                    || t.includes('checking your browser');
            }"""
        ))
    except Exception:
        return False


async def pass_challenge(page, timeout: float = 45.0, log_prefix: str = "") -> bool:
    """Chờ (và thử click) qua thử thách Cloudflare trước khi thao tác tiếp.

    Dùng cho MỌI luồng đăng nhập (claude / chatgpt / gemini / codex): sau khi mở
    trang mà gặp "Thực hiện xác minh bảo mật" thì đừng vội bỏ cuộc — phần lớn tự
    qua sau vài giây; nếu còn thì thử click ô checkbox một lần.

    Trả True = đường đã thông (không có thử thách, hoặc đã qua).
    Trả False = hết `timeout` mà vẫn bị chặn → caller nên báo người dùng vào VNC
    tự tích, ĐỪNG giết phiên."""
    if not await is_challenge_showing(page):
        return True
    logger.info("%sCloudflare challenge — chờ qua (tối đa %.0fs)", log_prefix, timeout)
    started = time.monotonic()
    deadline = started + timeout
    clicks = 0
    last_click = 0.0
    while time.monotonic() < deadline:
        await asyncio.sleep(1.5)
        if not await is_challenge_showing(page):
            logger.info("%sCloudflare challenge đã qua", log_prefix)
            return True
        # Đợi ~5s cho nó tự qua (cookie ấm thì tự thoát), rồi thử click.
        # THỬ LẠI mỗi 8s tối đa 3 lần: widget hay render lại (reset/expire) nên
        # 1 lần click là hụt — trước đây chỉ click 1 lần rồi ngồi chờ hết giờ.
        now = time.monotonic()
        if now - started > 5.0 and clicks < 3 and now - last_click > 8.0:
            last_click = now
            clicks += 1
            await _try_click_checkbox(page)
    still = await is_challenge_showing(page)
    if still:
        logger.warning("%sCloudflare challenge CHƯA qua sau %.0fs", log_prefix, timeout)
    return not still


async def _read_token(page) -> str | None:
    return await page.evaluate(
        """() => {
            const inp = document.querySelector("input[name='cf-turnstile-response']");
            if (inp && inp.value && inp.value.length > 20) return inp.value;
            if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                try { return window.turnstile.getResponse() || null; } catch(e) {}
            }
            return null;
        }"""
    )


async def solve_turnstile(
    url: str,
    sitekey: str | None = None,
    profile: str = "default",
    headless: bool = True,
    timeout: int | None = None,
    allow_paid_fallback: bool = True,
) -> dict:
    """Open `url`, wait for the Turnstile widget to emit a token, return it.

    Args:
        url: Page that hosts the Turnstile challenge.
        sitekey: Optional — sanity check the rendered widget matches.
        profile: Persistent profile (reuses cf_clearance cookies).
        headless: When False the browser shows on Xvfb display → noVNC.
        timeout: Override default solve timeout.
        allow_paid_fallback: If solver fails and 2captcha is configured,
            try it before giving up.
    """
    soft_deadline = time.time() + (timeout or settings.solve_timeout)

    async with pool.page(profile=profile, headless=headless) as page:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait for the widget container to mount.
        try:
            await page.wait_for_selector(
                "div.cf-turnstile, iframe[src*='challenges.cloudflare.com']",
                timeout=15_000,
            )
        except Exception as exc:
            raise RuntimeError(f"turnstile widget not present on {url}: {exc}") from exc

        if sitekey:
            actual = await page.evaluate(
                "() => document.querySelector('.cf-turnstile')?.dataset?.sitekey"
            )
            if actual and actual != sitekey:
                logger.warning("turnstile sitekey mismatch: page=%s arg=%s", actual, sitekey)
        # If sitekey not provided, grab it for fallback path
        resolved_sitekey = sitekey or await page.evaluate(
            "() => document.querySelector('.cf-turnstile')?.dataset?.sitekey || null"
        )

        # Fast path — poll for ~6 s waiting for cookie-based auto-pass.
        fast_deadline = time.time() + 6
        while time.time() < fast_deadline:
            token = await _read_token(page)
            if token:
                logger.info("turnstile token via cookie-pass len=%d", len(token))
                return {
                    "token": token,
                    "expires_at": time.time() + 110,
                    "profile": profile,
                    "method": "cookie",
                }
            await asyncio.sleep(0.4)

        # Auto-click attempt — handles "managed" widgets that show a visible checkbox.
        if await _try_click_checkbox(page):
            click_deadline = min(soft_deadline, time.time() + 25)
            while time.time() < click_deadline:
                token = await _read_token(page)
                if token:
                    logger.info("turnstile token after auto-click len=%d", len(token))
                    return {
                        "token": token,
                        "expires_at": time.time() + 110,
                        "profile": profile,
                        "method": "auto_click",
                    }
                await asyncio.sleep(0.5)

        # Keep polling until the soft deadline in case Cloudflare validates
        # silently after the checkbox click or after additional fingerprint
        # checks complete.
        while time.time() < soft_deadline:
            token = await _read_token(page)
            if token:
                logger.info("turnstile token via slow-poll len=%d", len(token))
                return {
                    "token": token,
                    "expires_at": time.time() + 110,
                    "profile": profile,
                    "method": "slow",
                }
            await asyncio.sleep(0.5)

    # 2captcha fallback — paid, slow (~30-60 s), but ~95 % success rate.
    if allow_paid_fallback and twocaptcha.is_enabled() and resolved_sitekey:
        logger.info("falling back to 2captcha for %s", url)
        result = await twocaptcha.solve_turnstile_2captcha(url=url, sitekey=resolved_sitekey)
        result["profile"] = profile
        result["method"] = "2captcha"
        return result

    raise TimeoutError(f"turnstile solve timed out after {settings.solve_timeout}s")
