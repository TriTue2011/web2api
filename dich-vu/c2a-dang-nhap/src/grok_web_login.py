# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/grok_web_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Đăng nhập Grok Web (grok.com) — bản PHỤ THUỘC MỘT CÚ TÍCH TAY.

Khác hẳn `claude_web_login.py` ở đúng một điểm, và điểm ấy do ĐO chứ không do
chọn: **không tự qua được Cloudflare của grok.com**. Số đo 20/09/2026:

    turnstile: KHÔNG click được ô xác minh (cả 3 cách)
    grok_do  : Cloudflare challenge CHƯA qua sau 60s
    nút thấy được trên trang: "Cloudflare", "Quyền riêng tư"

Tức chưa tới được màn đăng nhập, nên cả hai đường (tài khoản Google và
email/mật khẩu) đều chưa có gì để bấm. Thử lại với hồ sơ đã có sẵn
`cf_clearance` cũng vẫn bị chặn, và cookie `cf_chl_rc_ni` cho thấy Cloudflare
đang hạn chế phát thử thách cho trình duyệt này.

Nên module này KHÔNG giả vờ tự lo được. Nó chia việc làm hai nửa rạch ròi:

  - Nửa CHỈ NGƯỜI LÀM ĐƯỢC: tích ô "Xác minh bạn là con người". Gặp cảnh ấy thì
    trạng thái là ``need_manual`` kèm đường noVNC, chứ không quay vòng thử lại
    cho hết giờ rồi báo "thất bại" — báo thế là đổ lỗi nhầm chỗ và giấu mất
    việc thật sự cần làm.
  - Nửa TỰ LÀM ĐƯỢC: sau khi cửa đã thông, bấm "Sign in with Google" (dùng lại
    hồ sơ Google sẵn có, không phải nhập lại 2FA) hoặc điền thẳng email/mật
    khẩu, rồi đọc cookie phiên ra cho `api/grok.py` dùng.

KHÔNG ĐOÁN TÊN COOKIE. Chưa từng đăng nhập được vào grok.com lần nào nên không
ai biết chắc phiên nằm ở cookie tên gì. Vì vậy chỗ này lấy TẤT CẢ cookie của
grok.com trừ nhóm `cf_*` của Cloudflare, và kết luận "đã đăng nhập" bằng cách
NHÌN TRANG (có ô soạn tin, không còn màn xác minh) chứ không bằng cách dò một
cái tên tự nghĩ ra. Khi có phiên thật rồi thì ghim tên lại sau.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .auto_login import (
    LoginSession,
    click_google_oauth_consent,
    do_google_login_steps,
)
from .browser_pool import pool

logger = logging.getLogger(__name__)

_GROK_HOME = "https://grok.com/"
_GROK_LOGIN = "https://grok.com/?showLogin=true"

# Cookie do Cloudflare đặt — không phải bằng chứng đăng nhập.
_CF_PREFIX = "cf_"

# Nút "đăng nhập bằng Google" trên màn đăng nhập của Grok.
_GOOGLE_BTN_SELECTORS = (
    'button[data-provider="google"]',
    'button:has-text("Sign in with Google")',
    'button:has-text("Continue with Google")',
    'button:has-text("Đăng nhập bằng Google")',
    'button:has-text("Tiếp tục với Google")',
    'a[href*="accounts.google.com"]',
    'div[role="button"]:has-text("Google")',
)


@dataclass
class GrokWebLoginSession(LoginSession):
    """Giữ cookie phiên của grok.com khi đăng nhập xong."""

    cookies: dict = field(default_factory=dict)
    novnc_url: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["cookie_names"] = sorted(self.cookies)
        base["has_session"] = bool(self.cookies)
        base["novnc_url"] = self.novnc_url
        return base


_sessions: dict[str, GrokWebLoginSession] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_session(profile: str) -> Optional[GrokWebLoginSession]:
    return _sessions.get(profile)


def submit_2fa_code(profile: str, code: str) -> bool:
    session = _sessions.get(profile)
    if not session or session.state != "need_code":
        return False
    session.pending_code = code.strip()
    session.message = "Đã nhận mã, đang submit..."
    return True


async def doc_cookie_phien(profile: str) -> dict[str, str]:
    """Cookie grok.com đọc từ hồ sơ trên đĩa, trình duyệt không cần đang mở.

    Giữ ``sso`` và ``cf_clearance`` / ``__cf_bm``. Đo 01/10/2026: websocket
    ``/ws/mgw/`` chỉ chấp nhận khi có clearance của đúng máy này. Vẫn bỏ
    ``cf_chl_*`` — đó là dấu Cloudflare đang hạn chế trình duyệt, không phải phiên.
    """
    try:
        cookies = await pool.read_cookies(profile, _GROK_HOME)
    except Exception:
        logger.debug("grok_login: không đọc được cookie hồ sơ %s", profile, exc_info=True)
        return {}
    giu_cf = {"cf_clearance", "__cf_bm"}
    out: dict[str, str] = {}
    for c in cookies:
        name = str(c.get("name") or "")
        value = c.get("value")
        if not name or not value:
            continue
        if name.startswith(_CF_PREFIX) and name not in giu_cf:
            continue
        out[name] = str(value)
    return out


async def _nhin_trang(page) -> dict[str, Any]:
    """Trang đang ở tình trạng nào — nhìn DOM, không suy từ tên cookie.

    Ba tình trạng phân biệt được và dẫn tới ba việc khác hẳn nhau:
      chan     → Cloudflare đang chặn, cần một cú tích tay;
      dang_nhap→ đã vào được ứng dụng (có ô soạn tin);
      can_login→ cửa đã thông nhưng chưa đăng nhập.
    """
    try:
        return await page.evaluate(
            """() => {
                const chu = (document.body?.innerText || '');
                const chan = /xác minh bảo mật|just a moment|checking your browser|verify you are human/i.test(chu)
                    || /cf-chl|challenges\\.cloudflare\\.com/.test(document.documentElement.innerHTML.slice(0, 4000));
                const oSoan = !!document.querySelector('textarea, [contenteditable="true"]');
                const nutGoogle = [...document.querySelectorAll('button, a, div[role="button"]')]
                    .some((el) => el.offsetParent
                        && /google/i.test(el.innerText || el.getAttribute('aria-label') || ''));
                return { chan, oSoan, nutGoogle, tieuDe: document.title.slice(0, 80) };
            }"""
        )
    except Exception:
        return {"chan": False, "oSoan": False, "nutGoogle": False, "tieuDe": ""}


async def _bam_nut_google(page) -> bool:
    for sel in _GOOGLE_BTN_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=5_000)
                logger.info("grok_login: bấm nút Google qua %s", sel)
                return True
        except Exception:
            continue
    try:
        return bool(await page.evaluate(
            """() => {
                for (const el of document.querySelectorAll('button, a, div[role="button"]')) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || el.getAttribute('aria-label') || '').toLowerCase();
                    const h = el.getAttribute('href') || '';
                    if (t.includes('google') || h.includes('accounts.google.com')) { el.click(); return true; }
                }
                return false;
            }"""
        ))
    except Exception:
        return False


async def _dien_email_mat_khau(page, email: str, password: str) -> bool:
    """Đường đăng nhập thẳng: gõ email rồi mật khẩu vào đúng ô đang hiện.

    Grok chia hai bước (email trước, mật khẩu sau) nên phải điền rồi chờ, không
    gõ cả hai một lượt.
    """
    if not email or not password:
        return False
    try:
        o_email = page.locator('input[type="email"], input[name="email"], input[autocomplete="username"]').first
        if await o_email.count() > 0:
            await o_email.fill(email, timeout=8_000)
            await page.keyboard.press("Enter")
            await asyncio.sleep(3.0)
        o_mk = page.locator('input[type="password"]').first
        for _ in range(6):
            if await o_mk.count() > 0 and await o_mk.is_visible():
                break
            await asyncio.sleep(1.5)
        if await o_mk.count() == 0:
            return False
        await o_mk.fill(password, timeout=8_000)
        await page.keyboard.press("Enter")
        await asyncio.sleep(4.0)
        return True
    except Exception:
        logger.debug("grok_login: điền email/mật khẩu không xong", exc_info=True)
        return False


async def start_grok_web_login(
    profile: str = "grok-web-default",
    email: str = "",
    password: str = "",
    totp_secret: str = "",
    prefer_method: str = "auth",
    novnc_url: str = "",
) -> GrokWebLoginSession:
    """Chạy nền một lượt đăng nhập Grok Web."""
    cu = _tasks.pop(profile, None)
    if cu and not cu.done():
        cu.cancel()

    session = GrokWebLoginSession(
        profile=profile,
        email=email,
        state="starting",
        message="Khởi tạo Chrome",
        totp_secret=totp_secret,
        prefer_method=prefer_method,
        novnc_url=novnc_url,
    )
    _sessions[profile] = session
    _tasks[profile] = asyncio.create_task(_chay(session, password))
    return session


async def _chay(session: GrokWebLoginSession, password: str) -> None:
    pool.dau_dang_nhap(session.profile)
    try:
        await _chay_trong(session, password)
    finally:
        pool.xong_dang_nhap(session.profile)
    # Ở trạng thái «need_manual» thì CỐ Ý GIỮ cửa sổ: chủ máy sắp vào noVNC tích
    # ô xác minh, đóng đi là đóng đúng thứ họ cần. Chỉ dọn khi đã xong hẳn.
    if session.state in ("success", "failed"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("grok_login: bỏ qua bước đóng hồ sơ", exc_info=True)


async def _chay_trong(session: GrokWebLoginSession, password: str) -> None:
    try:
        session.state = "running"
        session.message = "Đang mở Chrome (có cửa sổ, xem qua noVNC)"
        ctx = await pool.get(profile=session.profile, headless=False, force_recreate=True)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        session.message = "Mở grok.com..."
        await page.goto(_GROK_HOME, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(5.0)
        trang = await _nhin_trang(page)

        # ── Đã đăng nhập sẵn trên hồ sơ này? Xong luôn. ──
        if trang.get("oSoan") and not trang.get("chan"):
            session.cookies = await doc_cookie_phien(session.profile)
            session.state = "success"
            session.message = "Hồ sơ đã có phiên Grok — không cần đăng nhập lại"
            session.completed_at = time.time()
            return

        # ── Cloudflare chặn: CHỈ NGƯỜI TÍCH ĐƯỢC. ──
        if trang.get("chan"):
            session.state = "need_manual"
            session.message = (
                "Cloudflare đang chặn grok.com. Đo được là bộ giải tự động không qua "
                "được cửa này, nên cần một cú tích tay: mở noVNC, tích ô "
                "“Xác minh bạn là con người”, rồi gọi lại onboard. Cửa sổ vẫn đang mở."
            )
            logger.info("grok_login: cần tích tay, hồ sơ=%s", session.profile)
            return

        # ── Cửa đã thông: tới phần tự làm được. ──
        session.message = "Mở màn đăng nhập Grok..."
        if not trang.get("nutGoogle"):
            try:
                await page.goto(_GROK_LOGIN, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(3.0)
            except Exception:
                pass
            trang = await _nhin_trang(page)
            if trang.get("chan"):
                session.state = "need_manual"
                session.message = "Cloudflare chặn ở màn đăng nhập — cần tích tay qua noVNC."
                return

        da_vao = False

        # Đường 1: dùng lại tài khoản Google sẵn có trên hồ sơ (không phải nhập lại 2FA).
        if trang.get("nutGoogle"):
            session.message = "Bấm “Sign in with Google”..."
            hung: dict = {}
            ctx.on("page", lambda p: hung.setdefault("page", p))
            if await _bam_nut_google(page):
                trang_google = page
                for _ in range(30):
                    if hung.get("page") is not None:
                        trang_google = hung["page"]
                        try:
                            await trang_google.wait_for_load_state("domcontentloaded", timeout=10_000)
                        except Exception:
                            pass
                        break
                    if "accounts.google.com" in (page.url or ""):
                        break
                    await asyncio.sleep(0.5)
                if "accounts.google.com" in (getattr(trang_google, "url", "") or ""):
                    session.message = "Chọn tài khoản Google + đồng ý quyền..."
                    if await trang_google.locator(
                        'input[type="password"], input[type="email"]'
                    ).count() > 0:
                        if not await do_google_login_steps(
                            session, trang_google, ctx, password, session.prefer_method
                        ):
                            return
                    for _ in range(12):
                        try:
                            if trang_google.is_closed() or "accounts.google.com" not in (trang_google.url or ""):
                                break
                        except Exception:
                            break
                        await click_google_oauth_consent(trang_google, timeout=3.0)
                        await asyncio.sleep(1.5)
                da_vao = True

        # Đường 2: đăng nhập thẳng bằng email + mật khẩu.
        if not da_vao and session.email:
            session.message = "Đăng nhập thẳng bằng email..."
            da_vao = await _dien_email_mat_khau(page, session.email, password)

        if not da_vao:
            session.state = "failed"
            session.error = "Không tìm thấy đường đăng nhập nào trên grok.com (cả nút Google lẫn ô email)"
            session.completed_at = time.time()
            return

        # ── Chờ ứng dụng lên rồi đọc cookie phiên ──
        session.message = "Chờ Grok nhận phiên..."
        try:
            await page.bring_to_front()
        except Exception:
            pass
        for _ in range(20):
            trang = await _nhin_trang(page)
            if trang.get("oSoan") and not trang.get("chan"):
                session.cookies = await doc_cookie_phien(session.profile)
                if session.cookies:
                    session.state = "success"
                    session.message = "Đăng nhập Grok Web thành công"
                    session.completed_at = time.time()
                    return
            await asyncio.sleep(2.0)

        session.state = "failed"
        session.error = f"Chưa vào được Grok sau khi đăng nhập (địa chỉ={getattr(page, 'url', '?')})"
        session.completed_at = time.time()

    except asyncio.CancelledError:
        session.state = "failed"
        session.error = "Bị huỷ (có yêu cầu đăng nhập mới)"
        session.completed_at = time.time()
        raise
    except Exception as exc:
        logger.exception("grok_web_login hỏng, hồ sơ=%s", session.profile)
        session.state = "failed"
        session.error = str(exc)
        session.completed_at = time.time()
