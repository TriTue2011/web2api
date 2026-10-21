# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/codex_google_onboard.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Codex OAuth onboard bằng cách RIDE session Google có sẵn trong profile
`google-<name>` của captcha-solver — không cần password/2FA/nuke profile.

Dùng khi token Codex của một tài khoản Google chết (OpenAI revoke) và cần lấy
token mới tự động. Mở auth_url (do chatgpt2api sinh, PKCE), bấm "Continue with
Google", để `click_google_oauth_consent` xử lý account chooser + consent, rồi
bắt URL callback localhost chứa ?code=... trả về cho phía chatgpt2api exchange.
"""
import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel

from .browser_pool import pool
from .auto_login import click_google_oauth_consent
from .chatgpt_login import _GOOGLE_BTN_SELECTORS
from .claude_web_login import _pick_google_account

logger = logging.getLogger(__name__)


def _openai_error_kind(url: str) -> str:
    """`kind` trong payload base64 của https://auth.openai.com/error?payload=…

    Ví dụ payload giải mã: {"kind": "AccountDeactivated", "requestId": "…"}.
    Trả "" nếu không đọc được — người gọi còn nhánh đối chiếu chữ trên trang.
    """
    import base64
    import json
    from urllib.parse import urlparse, parse_qs

    raw = (parse_qs(urlparse(url).query).get("payload") or [""])[0]
    if not raw:
        return ""
    try:
        pad = "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw + pad))
        return str(data.get("kind") or "")
    except Exception:
        return ""


class CodexGoogleOnboardReq(BaseModel):
    profile: str            # vd: google-nguoi-d
    auth_url: str           # URL OAuth Codex do chatgpt2api sinh (có state+PKCE)
    email: str = ""         # để bấm đúng tile ở màn account chooser
    headless: bool = True


async def _click_google_btn(page) -> bool:
    """Click "Tiếp tục với Google" / Continue with Google on OpenAI login."""
    # Playwright role/name (VN + EN) — OpenAI welcome-back page uses this label.
    for name in (
        "Tiếp tục với Google",
        "Continue with Google",
        "Continue with google",
        "Google",
    ):
        try:
            loc = page.get_by_role("button", name=name)
            if await loc.count() > 0:
                btn = loc.first
                if await btn.is_visible():
                    await btn.click(timeout=5000, force=True)
                    logger.info("codex-g: clicked Google btn via role name=%r", name)
                    return True
        except Exception:
            continue
    selectors = list(_GOOGLE_BTN_SELECTORS) + [
        'button:has-text("Tiếp tục với Google")',
        'a:has-text("Tiếp tục với Google")',
        'button:has-text("Continue with Google")',
        'div[role="button"]:has-text("Tiếp tục với Google")',
        'div[role="button"]:has-text("Google")',
        # OpenAI sometimes wraps icon+text
        'button:has-text("Google")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=5000, force=True)
                logger.info("codex-g: clicked Google btn via %s", sel)
                return True
        except Exception:
            continue
    # JS: exact-ish match (avoid Apple/Microsoft)
    try:
        ok = await page.evaluate(
            """() => {
              const re = /tiếp\\s*tục\\s*với\\s*google|continue\\s*with\\s*google/i;
              const bad = /apple|microsoft|github|microsoft|đăng ký|sign up/i;
              const nodes = document.querySelectorAll(
                'button,a,[role="button"],div[role="button"]'
              );
              for (const n of nodes) {
                const t = (n.innerText || n.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!t || t.length > 60) continue;
                if (bad.test(t) && !/google/i.test(t)) continue;
                if (re.test(t) || (/google/i.test(t) && /tiếp|continue/i.test(t))) {
                  n.scrollIntoView({block:'center'});
                  n.click();
                  return t;
                }
              }
              return null;
            }"""
        )
        if ok:
            logger.info("codex-g: JS-clicked Google btn %r", ok)
            return True
    except Exception as exc:
        logger.warning("codex-g: JS Google click failed: %s", exc)
    return False


# Shared rule (OpenAI welcome-back + Google accountchooser):
#   if target email appears in the list → click that tile (refresh that account)
#   else → click "use another account" / "đăng nhập vào tài khoản khác"
_PICK_OR_OTHER_JS = """
(email) => {
  const want = (email || '').toLowerCase().replace(/\\s+/g, '');
  const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, '');
  const nodes = Array.from(
    document.querySelectorAll(
      'button, a, [role="button"], [role="link"], div[data-identifier], li[data-identifier], div[data-email]'
    )
  );

  if (want) {
    // Prefer data-identifier / data-email exact match (Google)
    for (const n of nodes) {
      const id = norm(n.getAttribute('data-identifier') || n.getAttribute('data-email') || '');
      if (id === want) { n.click(); return { action: 'tile', via: 'data-id', email: want }; }
    }
    // Text match (OpenAI tiles may wrap email across lines — strip whitespace)
    for (const n of nodes) {
      const t = norm(n.innerText || n.textContent || '');
      if (!t || t.length > 200) continue;
      if (t.includes(want)) { n.click(); return { action: 'tile', via: 'text', email: want }; }
    }
  }

  // No matching tile → use another account (same wording on both screens)
  const reOther = /sử dụng một tài khoản khác|use another account|đăng nhập vào tài khoản khác|log in to another account|sign in to another account/i;
  for (const n of nodes) {
    const t = (n.innerText || n.textContent || '').trim().replace(/\\s+/g, ' ');
    if (reOther.test(t) && t.length < 80) {
      n.click();
      return { action: 'other', text: t };
    }
  }
  // Broaden: some Google rows are plain divs without role
  for (const n of document.querySelectorAll('div, li, span')) {
    const t = (n.innerText || '').trim().replace(/\\s+/g, ' ');
    if (reOther.test(t) && t.length < 60 && n.children.length < 4) {
      n.click();
      return { action: 'other', text: t, via: 'broad' };
    }
  }
  return { action: 'none' };
}
"""


async def _pick_account_or_other(page, email: str, *, where: str) -> bool:
    """If `email` is on the chooser list, click it; otherwise click 'another account'."""
    try:
        result = await page.evaluate(_PICK_OR_OTHER_JS, (email or "").strip())
    except Exception as exc:
        logger.warning("codex-g: %s pick-or-other failed: %s", where, exc)
        return False
    if not result or result.get("action") == "none":
        logger.info("codex-g: %s no tile for %r and no other-account btn", where, email)
        return False
    if result.get("action") == "tile":
        logger.info("codex-g: %s clicked tile %s (via %s)", where, email, result.get("via"))
    else:
        logger.info(
            "codex-g: %s target %r not in list → another account (%r)",
            where,
            email,
            result.get("text"),
        )
    await asyncio.sleep(2.0)
    return True


async def _google_chooser_pick(page, email: str) -> bool:
    """Google accountchooser: pick target email, else 'Sử dụng một tài khoản khác'."""
    if await _pick_account_or_other(page, email, where="google-chooser"):
        return True
    # Fallback to existing helper for Google-specific data-identifier quirks
    if email:
        try:
            if await _pick_google_account(page, email):
                logger.info("codex-g: _pick_google_account ok %s", email)
                await asyncio.sleep(2.0)
                return True
        except Exception:
            pass
    return False


async def _bam_tiep_tuc(page, btn) -> bool:
    """Bấm nút "Tiếp tục" ở màn chọn không gian làm việc. True nếu bấm được.

    Vì sao phải viết riêng — đo thật 18/08 trên máy chủ đang chạy:

    Bản cũ gọi thẳng ``btn.click(force=True, timeout=3000)`` bên trong một khối
    ``except Exception: pass``. Cú bấm ném lỗi, ngoại lệ bị nuốt, vòng lặp quay
    lại bấm tiếp — 24 lần liên tiếp trong 96 giây rồi hết giờ. Nhật ký chỉ thấy
    dòng "consent Tiếp tục" lặp đi lặp lại, không một chữ nào về nguyên nhân.

    Cách nhận ra: các lần bấm cách nhau ~4,1 giây, trong khi một cú bấm THÀNH
    CÔNG phải mất tối thiểu 2,5 + 6 + 1,2 ≈ 9,7 giây mới quay lại. Ngắn hơn một
    nửa nghĩa là click ném ngay lập tức.

    Bấm được thì phần còn lại chạy trót lọt — đo trên đúng trang đó:
    ``/api/accounts/workspace/select`` 200 → ``/api/accounts/consent`` 302 →
    ``localhost:1455/auth/callback?code=…``. Tức phần trình duyệt không hỏng,
    chỉ mỗi cú bấm là hỏng.

    Ba đường, dừng ở đường nào ăn: click thường (đợi đủ lâu để trang render
    xong) → click bằng JS (bỏ qua mọi lớp phủ) → gửi thẳng form.
    """
    try:
        await btn.click(timeout=8000)
        return True
    except Exception as exc:
        logger.info("codex-g: click thường hỏng (%s) — thử bằng JS",
                    str(exc)[:120].replace("\n", " "))
    try:
        if await page.evaluate(
            """() => {
                const bs = Array.from(document.querySelectorAll('button'));
                const b = bs.find(x => /Tiếp tục|Continue/i.test(x.innerText || ''));
                if (!b) return false;
                b.click();
                return true;
            }"""
        ):
            logger.info("codex-g: đã bấm Tiếp tục bằng JS")
            return True
    except Exception as exc:
        logger.info("codex-g: click JS hỏng: %s", str(exc)[:120])
    try:
        if await page.evaluate(
            """() => {
                const f = document.querySelector('form');
                if (!f) return false;
                if (typeof f.requestSubmit === 'function') f.requestSubmit();
                else f.submit();
                return true;
            }"""
        ):
            logger.info("codex-g: đã gửi thẳng form chọn workspace")
            return True
    except Exception as exc:
        logger.info("codex-g: gửi form hỏng: %s", str(exc)[:120])
    return False


async def run_codex_google_onboard(req: CodexGoogleOnboardReq) -> dict[str, Any]:
    ctx = await pool.get(profile=req.profile, headless=req.headless)
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    captured: list[str] = []

    def on_request(request):
        u = request.url
        if "localhost" in u and "code=" in u:
            captured.append(u)

    page.on("request", on_request)

    try:
        await page.goto(req.auth_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2.5)
        # Cloudflare "xác minh bảo mật" → chờ/click qua trước khi vào vòng OAuth.
        try:
            from .solvers.turnstile import pass_challenge
            await pass_challenge(page, timeout=45.0, log_prefix="codex_onboard: ")
        except Exception:
            pass

        # Loop until OAuth callback — handle OpenAI login screens + Google chooser
        deadline = time.time() + 120
        google_btn_clicked = False
        mfa_alt_clicked = False
        rejected_tries = 0
        while time.time() < deadline and not captured:
            url = ""
            content = ""
            try:
                url = page.url or ""
                content = await page.content()
            except Exception:
                await asyncio.sleep(1.0)
                continue

            # DEBUG: in màn hình THẬT mỗi vòng. Không có dòng này thì lúc kẹt
            # (màn OpenAI không khớp nhánh nào, vd chọn workspace) log im lặng
            # tới khi hết 120s, không biết đường nào mà lần.
            try:
                _dbg = (await page.locator("body").inner_text(timeout=1500))[:280]
                _dbg = " | ".join(x.strip() for x in _dbg.splitlines() if x.strip())
            except Exception:
                _dbg = ""
            logger.info("codex-g: screen url=%s body=%s", url[:110], _dbg[:260])

            # ── OpenAI đá sang trang lỗi tài khoản bị xóa/vô hiệu hóa ──────────
            # Google trả danh tính bình thường, OpenAI tra ra "không có tài
            # khoản" rồi đứng ở /error vĩnh viễn. Không có nhánh này thì vòng
            # lặp chụp lại đúng một trang chết 1,2s/lần cho tới hết 120s rồi trả
            # "no callback; stuck at …" — một thất bại vô danh mà chatgpt2api
            # không đọc được, nên nó báo nhầm thành "kiểm tra mật khẩu/TOTP".
            # Đo thật 02/08 (nguoi.b@example.com): 131 giây đứng nhìn một trang
            # không bao giờ đổi, lặp lại mỗi 2 tiếng theo lượt quét định kỳ.
            if "auth.openai.com/error" in url and (
                _openai_error_kind(url) == "AccountDeactivated"
                or "đã bị xóa hoặc vô hiệu hóa" in content
                or "has been deleted or deactivated" in content
            ):
                logger.warning("codex-g: account_deactivated — dừng ngay url=%s", url[:160])
                return {
                    "state": "failed",
                    "error_code": "account_deactivated",
                    "error": "OpenAI: tài khoản đã bị xóa hoặc vô hiệu hóa",
                }

            # ── OpenAI MFA challenge (mặc định đòi PASSKEY — bot không ký được) ──
            # Bấm "Hãy thử một phương pháp khác" để lộ các cách thay thế
            # (authenticator/TOTP, mã qua email…) rồi xử lý bằng pyotp/IMAP sẵn có.
            if "/mfa-challenge/" in url:
                if not mfa_alt_clicked:
                    mfa_alt_clicked = True
                    ok_alt = False
                    for _sel in (
                        'a:has-text("Hãy thử một phương pháp khác")',
                        'button:has-text("Hãy thử một phương pháp khác")',
                        'a:has-text("Try another method")',
                        'button:has-text("Try another method")',
                        '[role="button"]:has-text("phương pháp khác")',
                        '[role="link"]:has-text("phương pháp khác")',
                        'a:has-text("phương pháp khác")',
                    ):
                        try:
                            loc = page.locator(_sel).first
                            if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                                await loc.click(timeout=4000, force=True)
                                ok_alt = True
                                break
                        except Exception:
                            continue
                    logger.info("codex-g: mfa-challenge -> clicked 'phuong phap khac'=%s", ok_alt)
                    await asyncio.sleep(2.5)
                    continue
                await asyncio.sleep(1.5)
                continue

            # ── Google accountchooser / consent ──
            if "accounts.google.com" in url:
                # Màn TỪ CHỐI "Trình duyệt hoặc ứng dụng này có thể không an
                # toàn" (/signin/rejected). Đo thật 30/07: vòng lặp cũ không có
                # nhánh này nên rơi xuống consent-click vô nghĩa, đọc lại màn
                # hình 8s/lần cho tới hết 120s — người vận hành thấy "treo",
                # trong khi trên trang CÓ SẴN nút "Thử lại" (BotGuard chập chờn,
                # bấm lại có thể lọt). Bấm tối đa 4 lần; vẫn bị từ chối thì đây
                # là ngõ cụt của trình duyệt tự động — dừng NGAY với lý do rõ,
                # đừng đốt nốt ngân sách.
                if "/signin/rejected" in url or "không an toàn" in content \
                        or "may not be secure" in content:
                    rejected_tries += 1
                    if rejected_tries > 4:
                        return {"state": "failed",
                                "error": "Google từ chối trình duyệt tự động "
                                         "(signin/rejected — 'trình duyệt không "
                                         "an toàn'), đã bấm Thử lại 4 lần"}
                    logger.info("codex-g: signin/rejected — bấm 'Thử lại' lần %d",
                                rejected_tries)
                    try:
                        await page.locator(
                            'button:has-text("Thử lại"), a:has-text("Thử lại"), '
                            'button:has-text("Try again"), a:has-text("Try again")'
                        ).first.click(timeout=4000)
                    except Exception:
                        pass
                    await asyncio.sleep(3.0)
                    continue
                if "accountchooser" in url or "Chọn tài khoản" in content or "Choose an account" in content:
                    await _google_chooser_pick(page, req.email or "")
                else:
                    if req.email:
                        await _pick_google_account(page, req.email)
                    await click_google_oauth_consent(page, timeout=6.0)
                await asyncio.sleep(1.5)
                continue

            # ── OpenAI ──
            if "auth.openai.com" in url or "chatgpt.com" in url:
                # PRIORITY: always click "Tiếp tục với Google" when visible.
                # Bug trước: trang "Chào mừng trở lại" (/log-in) bị nhầm là
                # account-chooser → chỉ tìm tile email, KHÔNG bao giờ bấm Google.
                has_google_btn = (
                    "Tiếp tục với Google" in content
                    or "Continue with Google" in content
                    or "log-in" in url
                    or "log-in-or-create" in url
                )
                if has_google_btn:
                    try:
                        n_btn = await page.locator(
                            'button:has-text("Tiếp tục với Google"), '
                            'button:has-text("Continue with Google"), '
                            'button:has-text("Google")'
                        ).count()
                    except Exception:
                        n_btn = 0
                    if n_btn > 0 or "Tiếp tục với Google" in content or "Continue with Google" in content:
                        if await _click_google_btn(page):
                            google_btn_clicked = True
                            logger.info(
                                "codex-g: clicked Tiếp tục với Google url=%s",
                                url[:120],
                            )
                            await asyncio.sleep(3.0)
                            continue

                # Welcome back account tiles / choose-an-account (no Google btn path)
                if (
                    "choose-an-account" in url
                    or "Chọn một tài khoản" in content
                    or (
                        ("Chào mừng trở lại" in content or "Welcome back" in content)
                        and "Tiếp tục với Google" not in content
                        and "Continue with Google" not in content
                    )
                ):
                    await _pick_account_or_other(
                        page, req.email or "", where="openai-chooser"
                    )
                    await asyncio.sleep(2.0)
                    continue

                # Codex consent after existing-account tile OR after Google login:
                # /sign-in-with-chatgpt/codex/consent → email pill + Hủy + Tiếp tục
                # → click Tiếp tục → localhost:1455/auth/callback?code=... (same capture)
                try:
                    is_codex_consent = (
                        "/codex/consent" in url
                        or "sign-in-with-chatgpt" in url
                        or (
                            ("ChatGPT" in content or "Codex" in content)
                            and ("Hủy" in content or "Cancel" in content)
                        )
                    )
                    has_google = await page.locator(
                        'button:has-text("Google"), button:has-text("Tiếp tục với Google")'
                    ).count() > 0
                    has_email = await page.locator(
                        'input[type="email"], input[name="email"]'
                    ).count() > 0
                    if has_google and not google_btn_clicked and not is_codex_consent:
                        if await _click_google_btn(page):
                            google_btn_clicked = True
                            await asyncio.sleep(3.0)
                            continue
                    cont = page.locator(
                        'button:has-text("Tiếp tục"), button:has-text("Continue"), '
                        'button[value="accept"], button:has-text("Allow"), '
                        'button:has-text("Authorize"), button:has-text("Đồng ý")'
                    )
                    if (
                        await cont.count() > 0
                        and not has_email
                        and "choose-an-account" not in url
                        and "log-in-or-create" not in url
                        and (is_codex_consent or not has_google)
                    ):
                        # Prefer black primary "Tiếp tục" (not Hủy)
                        primary = page.locator(
                            'button:has-text("Tiếp tục"), button:has-text("Continue")'
                        )
                        btn = primary.last if await primary.count() > 0 else cont.last
                        label = ""
                        try:
                            label = (await btn.inner_text()).strip()
                        except Exception:
                            pass
                        if "Tạo tài khoản" in label or "Google" in label:
                            pass
                        else:
                            logger.info(
                                "codex-g: consent Tiếp tục (%r) url=%s",
                                label or "primary",
                                url[:100],
                            )
                            if not await _bam_tiep_tuc(page, btn):
                                # Bấm hỏng thì đừng ngồi chờ callback không bao
                                # giờ tới — quay lại vòng, lần sau trang đã
                                # render xong thì bấm lại.
                                await asyncio.sleep(1.0)
                                continue
                            await asyncio.sleep(2.5)
                            for _ in range(15):
                                if captured:
                                    break
                                await asyncio.sleep(0.4)
                except Exception as exc:
                    # KHÔNG nuốt im lặng: đúng chỗ này đã giấu nguyên nhân thật
                    # suốt nhiều ngày — xem chú thích ở _bam_tiep_tuc.
                    logger.info("codex-g: nhánh consent lỗi: %s: %s",
                                type(exc).__name__, str(exc)[:160])

            await asyncio.sleep(1.2)

        # 3) Chờ callback localhost (ERR_CONNECTION_REFUSED nên bắt ở request)
        deadline = time.time() + 15
        while time.time() < deadline and not captured:
            await asyncio.sleep(0.5)

        if not captured:
            cur = ""
            try:
                cur = page.url
            except Exception:
                pass
            return {"state": "failed", "error": f"no callback; stuck at {cur[:120]}"}

        return {"state": "success", "redirect_url": captured[0]}
    except Exception as e:
        logger.exception("codex google onboard error")
        return {"state": "failed", "error": str(e)[:200]}
    finally:
        # ĐÓNG TAB NGAY khi xong (thành công hay thất bại): xong việc rồi thì cái
        # tab chỉ còn đốt RAM/CPU trên Xvfb. Trước đây phải chờ browser_pool tự
        # dọn khi rỗi (~5 phút) — đo thật 30/07: onboard xong 21:00:33 mà tới
        # 21:05 mới thấy "auto-evicting idle profile". Cookie/session vẫn được
        # giữ vì close_profile ghi user-data-dir xuống đĩa, không xoá profile.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
            logger.info("codex-g: đã đóng browser profile=%s", req.profile)
        except Exception as close_err:
            logger.warning({"event": "codex_google_onboard_close_error",
                            "profile": req.profile,
                            "error": str(close_err)[:160]})
