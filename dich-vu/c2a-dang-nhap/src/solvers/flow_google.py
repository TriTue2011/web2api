# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/flow_google.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Google Labs Flow image generator.

End-to-end: open https://labs.google/fx/vi/tools/flow/project/<id> with the
persistent "google-fx" profile (must be logged in to a Google account), let
the React app initialise, harvest the ya29 OAuth bearer token from the
first outbound googleapis.com request, ask the page's own grecaptcha
runtime for a fresh reCAPTCHA Enterprise token, then POST to
aisandbox-pa.googleapis.com from INSIDE the browser context so Chrome
attaches its proprietary x-browser-validation / x-client-data headers.

Setup is a one-time noVNC login:
  POST /v1/session/manual-login {"profile":"google-fx","url":"https://labs.google/fx/vi/tools/flow"}
After that this function works headlessly until the Google session cookie
naturally expires (typically months).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from ..auto_login import _pick_authenticator_method
from ..browser_pool import pool

logger = logging.getLogger(__name__)


# Defaults — strongest model (Nano Banana Pro), 16:9 landscape, 1 image per
# request. Override per-call by passing model / aspect_ratio / count.
#
# GEM_PIX_2 chứ không phải NANO_BANANA_PRO: đo 09/08/2026 cho thấy tên sau
# không phải hằng số có thật của Flow (API trả 400 INVALID_ARGUMENT). Đường
# DOM không lộ ra vì nó chọn model bằng cách bấm NHÃN dropdown.
DEFAULT_MODEL = "GEM_PIX_2"
DEFAULT_TOOL = "PINHOLE"
DEFAULT_ASPECT = "IMAGE_ASPECT_RATIO_LANDSCAPE"
DEFAULT_COUNT = 1
API_HOST = "https://aisandbox-pa.googleapis.com"

# Aspect ratio labels in the Flow UI dropdown (Vietnamese locale).
_ASPECT_LABELS = {
    "IMAGE_ASPECT_RATIO_LANDSCAPE":      "16:9",
    "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE":  "4:3",
    "IMAGE_ASPECT_RATIO_SQUARE":         "1:1",
    "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR":   "3:4",
    "IMAGE_ASPECT_RATIO_PORTRAIT":       "9:16",
}

# Flow UI model labels (matches the dropdown text in the screenshot).
#
# Tên NỘI BỘ ở cột trái đã đo thẳng vào API 09/08/2026: GEM_PIX_2, NARWHAL,
# HARBOR_SEAL hợp lệ; NANO_BANANA_PRO trả 400 (không tồn tại); IMAGEN_3_5 trả
# 404 (có thật nhưng tài khoản không có). Nhãn "Nano Banana Pro" không đổi —
# chỉ tên nội bộ sau nó đổi từ NANO_BANANA_PRO sang GEM_PIX_2.
#
# "Nano Banana 2 Lite" là nhãn SUY RA cho HARBOR_SEAL, chưa đọc được từ giao
# diện thật. Bấm hụt nhãn không còn im lặng: `generate_image` đọc ngược chip
# model rồi từ chối bấm Tạo nếu lệch, nên sai nhãn là lỗi ồn ào chứ không phải
# tiêu tín dụng vào model sai như sự cố 08/08/2026.
_MODEL_LABELS = {
    "GEM_PIX_2":       "Nano Banana Pro",
    "NARWHAL":         "Nano Banana 2",
    "HARBOR_SEAL":     "Nano Banana 2 Lite",
    "IMAGEN_3_5":      "Imagen 4",
    # Bí danh cũ — cấu hình đã lưu có thể còn gửi những tên này.
    "NANO_BANANA_PRO": "Nano Banana Pro",
    "IMAGEN_4":        "Imagen 4",
}

# When the request interceptor overrides imageModelName, map our friendly
# constants to the actual Flow API enum values. IMAGEN_4 isn't recognized
# by the Flow API — it must be IMAGEN_3_5.
_MODEL_API_VALUE = {
    "GEM_PIX_2":       "GEM_PIX_2",
    "NARWHAL":         "NARWHAL",
    "HARBOR_SEAL":     "HARBOR_SEAL",
    "IMAGEN_4":        "IMAGEN_3_5",   # UI alias → real API value
    "IMAGEN_3_5":      "IMAGEN_3_5",
    # Tên chết: API trả 400. Dịch sang tên còn sống để cấu hình cũ không gãy.
    "NANO_BANANA_PRO": "GEM_PIX_2",
}


def _fingerprint(image_url_obj: dict) -> str:
    """Stable identifier for a returned image so callers can dedupe."""
    for key in ("imageId", "mediaId", "id"):
        v = image_url_obj.get(key)
        if isinstance(v, str) and v:
            return v
    return str(image_url_obj)[:80]


def _extract_image_refs(payload: Any) -> list[dict]:
    """Walk the Flow API response and pull out every "image-like" record.

    Flow's batchGenerateImages response shape (Dec 2026):
        {"media": [
            {"name": "<media-id>", "image": {"generatedImage": {
                "fifeUrl": "https://flow-content.google/image/...",
                "mediaGenerationId": "...", "seed": 12345,
                "aspectRatio": "...", "modelNameType": "NARWHAL", ...
            }}}
        ]}
    We accept any dict that exposes one of the known URL fields or raw bytes.
    """
    out: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            url = (
                node.get("fifeUrl")        # primary CDN URL for Flow images
                or node.get("mediaUrl")
                or node.get("imageUrl")
                or node.get("gcsUri")
                or node.get("publicUrl")
                or node.get("url")
            )
            data = node.get("encodedImage") or node.get("imageBytes") or node.get("bytes")
            if url or data:
                out.append({
                    "url": url,
                    "data": data,
                    "mime": node.get("mimeType") or node.get("contentType") or "image/png",
                    "id": node.get("mediaGenerationId") or _fingerprint(node),
                    "seed": node.get("seed"),
                    "model": node.get("modelNameType"),
                    "aspect": node.get("aspectRatio"),
                    "prompt": node.get("prompt"),
                })
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return out


async def _capture_bearer(page, timeout_s: float = 25.0) -> str:
    """Observe outgoing requests and grab the first ya29 OAuth bearer."""
    captured: dict[str, str] = {}

    def _on_request(request) -> None:
        if captured.get("token"):
            return
        try:
            auth = (request.headers.get("authorization") or "").strip()
        except Exception:
            return
        if auth.startswith("Bearer ya29."):
            captured["token"] = auth[len("Bearer "):]
            logger.info(
                "captured ya29 bearer len=%d via %s",
                len(captured["token"]),
                request.url[:80],
            )

    page.on("request", _on_request)

    deadline = time.time() + timeout_s
    # Nudge the page to issue auth-bearing calls if it hasn't already.
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    while time.time() < deadline:
        if captured.get("token"):
            return captured["token"]
        await asyncio.sleep(0.4)

    raise RuntimeError(
        "Could not capture ya29 token. Profile likely not logged in. "
        "Run POST /v1/session/manual-login with profile='google-fx' and "
        "sign in via the noVNC URL, then retry."
    )


async def _get_recaptcha_token(page, action: str = "flow_generate") -> tuple[str, str]:
    """Đúc reCAPTCHA bằng execute gốc của trang. Hàm công khai đã bị Flow bọc.

    Bản Flow 22/09/2026 gán action extension_hijack_detected cho mọi token
    đi qua grecaptcha.enterprise.execute. Google trả unusual activity.
    Ưu tiên hàm đã giữ trước lúc bọc; không có thì gỡ action độc ngay trong Object.assign.
    """
    from .flow_rpc import FLOW_MINT_JS, install_captcha_hook

    await install_captcha_hook(page)
    info = await page.evaluate(FLOW_MINT_JS, action)
    if not isinstance(info, dict) or info.get("error"):
        raise RuntimeError(f"reCAPTCHA execute failed: {info}")
    token = info.get("token")
    sitekey = info.get("sitekey", "")
    if not token:
        raise RuntimeError(f"reCAPTCHA returned empty token: {info}")
    return token, sitekey


async def _set_dropdown(page, label_text: str, log_what: str) -> bool:
    """Robust selection for Google Flow UI (Tablist or Dropdown Menu)."""
    if not label_text:
        return False
        
    try:
        # 1. Thử tìm và click trực tiếp nếu element đã hiển thị sẵn (ví dụ: nút aspect ratio hoặc menu đã mở)
        locs = page.locator(f"text='{label_text}'")
        count = await locs.count()
        
        clicked = False
        for i in range(count - 1, -1, -1):
            el = locs.nth(i)
            if await el.is_visible():
                box = await el.bounding_box()
                if box:
                    await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    clicked = True
                    await asyncio.sleep(0.5)
                    break
                    
        # Nếu không thấy (vì menu đang đóng), tìm nút Trigger để mở menu
        if not clicked:
            trigger_box = await page.evaluate("""([txt, lbl]) => {
                const btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
                let keywords = [];
                if (lbl === 'model') keywords = ['nano', 'banana', 'imagen', 'pro'];
                else if (lbl === 'aspect') keywords = ['16:9', '9:16', '1:1', 'portrait', 'landscape', 'square', '4:3', '3:4'];
                else if (lbl === 'count') keywords = ['1x', 'x1', '2x', 'x2', '3x', '4x'];
                else keywords = [txt];
                
                let trigger = btns.find(b => {
                    const bt = (b.textContent || '').toLowerCase().trim();
                    return keywords.some(k => bt.includes(k));
                });
                if (trigger) {
                    const r = trigger.getBoundingClientRect();
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""", [label_text.lower(), log_what])
            
            if trigger_box:
                await page.mouse.click(trigger_box['x'], trigger_box['y'])
                await asyncio.sleep(1.5) # Đợi React render xong menu
            else:
                logger.warning("flow_dropdown_skip %s=%s (Trigger not found)", log_what, label_text)
                return False
        
        # Sau khi click trigger (hoặc click lần 1), kiểm tra xem nó có mở ra một dropdown list không
        await asyncio.sleep(0.5)
        locs = page.locator(f"text='{label_text}'")
        count = await locs.count()
        
        # Thường option trong menu sẽ là phần tử cuối cùng hiển thị trên màn hình
        for i in range(count - 1, -1, -1):
            el = locs.nth(i)
            if await el.is_visible():
                # Kiểm tra xem đây có phải trigger không
                is_trigger = await el.evaluate("(node) => { return node.hasAttribute('aria-haspopup') || node.hasAttribute('aria-expanded'); }")
                if not is_trigger:
                    box = await el.bounding_box()
                    if box:
                        await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                        logger.info("flow_dropdown_set %s=%s (Physical Option Click)", log_what, label_text)
                        await asyncio.sleep(0.5)
                        return True
                        
        logger.info("flow_dropdown_set %s=%s (Physical Trigger Click/Direct Toggle)", log_what, label_text)
        return True

    except Exception as e:
        logger.warning("flow_dropdown error: %s", e)

    return False


async def _humanize(page, moves: int = 7) -> None:
    """Emit human-like mouse movement + scroll + dwell so reCAPTCHA Enterprise
    v3 sees genuine interaction signals before grecaptcha.execute().

    Synthetic/no cursor movement (overly fast, perfectly straight, or absent)
    is a known score-lowering signal; real curved movement at human pace with
    pauses and a little scrolling raises the score. Best-effort, never raises.
    """
    import random as _r
    try:
        w, h = 1366, 768
        try:
            vp = page.viewport_size or {}
            w, h = vp.get("width", w), vp.get("height", h)
        except Exception:
            pass
        x, y = _r.randint(60, w - 60), _r.randint(90, h - 90)
        for _ in range(moves):
            nx = max(5, min(w - 5, x + _r.randint(-260, 260)))
            ny = max(5, min(h - 5, y + _r.randint(-190, 190)))
            # steps>1 makes Playwright interpolate → smooth, curved-ish path
            await page.mouse.move(nx, ny, steps=_r.randint(10, 28))
            x, y = nx, ny
            await asyncio.sleep(_r.uniform(0.15, 0.55))
            if _r.random() < 0.45:
                try:
                    await page.mouse.wheel(0, _r.randint(-280, 420))
                except Exception:
                    pass
                await asyncio.sleep(_r.uniform(0.2, 0.5))
        # final "reading" dwell — reCAPTCHA scores time-on-page positively
        await asyncio.sleep(_r.uniform(1.8, 3.2))
    except Exception as _exc:
        logger.warning("flow_humanize_failed: %s", _exc)


# Dấu hiệu Playwright báo trang/ngữ cảnh/trình duyệt đã đóng. Gặp cái này thì
# MỌI thao tác sau đều chết y hệt: thử lại là phí sạch ngân sách, mà hỏi trang
# để chẩn đoán còn ném tiếp một lỗi khác đè mất nguyên nhân thật.
_DAU_HIEU_TRANG_DONG = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "context has been closed",
    "session closed",
)


def _trang_da_dong(exc: BaseException) -> bool:
    loi = str(exc).lower()
    return any(d in loi for d in _DAU_HIEU_TRANG_DONG)


async def generate_image(
    project_id: str,
    prompt: str,
    aspect_ratio: str = DEFAULT_ASPECT,
    model: str = DEFAULT_MODEL,
    count: int = DEFAULT_COUNT,
    tool: str = DEFAULT_TOOL,
    profile: str = "google-fx",
    headless: bool = True,
    timeout: int = 90,
) -> dict:
    """Run the full Flow batchGenerateImages flow and return image refs.

    Args:
        count: 1-4. Flow's UI supports 1x/2x/3x/4x. We best-effort drive
            the dropdown; if Flow stored a different default on the project
            you may get a different number back.

    Returns:
        {
          "images": [{"url"|"data": ..., "mime": ..., "id": ...}, ...],
          "raw":    <full API response>,
          "elapsed_ms": int,
          "model": str,
        }
    """
    count = max(1, min(4, int(count or 1)))
    started = time.time()
    flow_url = f"https://labs.google/fx/vi/tools/flow/project/{project_id}"
    api_url = f"{API_HOST}/v1/projects/{project_id}/flowMedia:batchGenerateImages"

    async with pool.page(profile=profile, headless=headless) as page:
        # TRICK 1: Tẩy sạch cookie & storage đánh dấu Bot của reCAPTCHA (Surgical remove để giữ login)
        try:
            await page.goto("https://labs.google/fx", wait_until="domcontentloaded", timeout=15000)
            await page.evaluate("""() => {
                const keys = [];
                for (let i = 0; i < window.localStorage.length; i++) {
                    const k = window.localStorage.key(i);
                    if (k && (k.includes('recaptcha') || k.includes('grecaptcha') || k.startsWith('rc::'))) keys.push(k);
                }
                keys.forEach(k => window.localStorage.removeItem(k));
                
                const sk = [];
                for (let i = 0; i < window.sessionStorage.length; i++) {
                    const k = window.sessionStorage.key(i);
                    if (k && (k.includes('recaptcha') || k.includes('grecaptcha') || k.startsWith('rc::'))) sk.push(k);
                }
                sk.forEach(k => window.sessionStorage.removeItem(k));
                
                document.cookie.split(';').forEach(c => {
                    let name = c.split('=')[0].trim();
                    if(name.includes('grecaptcha') || name.startsWith('_ga') || name.includes('recaptcha')) {
                        document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
                        document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=.google.com';
                        document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=.labs.google';
                    }
                });
            }""")
            logger.info("flow_stealth: surgically cleared reCAPTCHA cache")
        except Exception as exc:
            logger.debug("flow_stealth_clear_failed: %s", exc)

        await _prime_flow_session(page, profile)
        await page.goto(flow_url, wait_until="domcontentloaded", timeout=30_000)

        # Flow renders the prompt input as a contenteditable DIV (not a
        # textarea — the only textarea on the page is the hidden
        # g-recaptcha-response shadow input). Wait for a sizeable
        # contenteditable to appear.
        try:
            await page.wait_for_function(
                """() => {
                    const ces = Array.from(document.querySelectorAll('[contenteditable=\"true\"]'));
                    return ces.some(e => e.offsetWidth > 200 && e.offsetHeight > 0);
                }""",
                timeout=60_000,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Flow UI never hydrated (timeout). Profile may be logged out. "
                f"Re-run /v1/session/manual-login with profile='{profile}'. ({exc})"
            ) from exc

        # ── Step 0: Remove the welcome dialog OVERLAY layer FIRST.
        # On freshly-created projects, Flow renders a Radix dialog with:
        #   • An overlay <div data-state="open"> (no role, viewport-sized)
        #     that intercepts ALL mouse events.
        #   • A dialog <div role="dialog"> with the actual content.
        # Removing just the overlay (not the dialog) lets mouse events
        # reach the workspace BELOW (where the prompt input lives at
        # y≈658, well below the dialog box).
        #
        # Why remove overlay early (not just before submit click):
        # Slate.js (the prompt editor) requires a REAL mouse click to
        # activate before keyboard events register in its React state.
        # JS .focus() + page.keyboard.type fires DOM key events, but
        # Slate ignores them — the submit button stays aria-disabled=true
        # because Slate state shows empty prompt. We confirmed this via
        # probe: after JS focus + execCommand insertText, ce_text had
        # our text in DOM but submit_aria_disabled was still 'true'.
        await page.evaluate("""
            () => {
                document.querySelectorAll('[data-state="open"]').forEach(el => {
                    if (el.getAttribute('role')) return;  // keep dialog content
                    const r = el.getBoundingClientRect();
                    if (r.width >= window.innerWidth * 0.8 && r.height >= window.innerHeight * 0.8) {
                        el.remove();
                    }
                });
            }
        """)
        await asyncio.sleep(0.3)

        # ── Step 1: Real mouse click on the prompt input to activate
        # Slate. Now that overlay is gone, the click reaches the editor.
        focused = await page.evaluate("""
            () => {
                const ces = Array.from(document.querySelectorAll('[contenteditable=true]'));
                // pick the largest visible contenteditable (the prompt input)
                const target = ces
                    .map(e => ({e, w: e.offsetWidth, h: e.offsetHeight}))
                    .filter(x => x.w > 200 && x.h > 0)
                    .sort((a, b) => (b.w * b.h) - (a.w * a.h))[0];
                if (!target) {
                    return {
                        found: false,
                        debug_ce_count: ces.length,
                        debug_dims: ces.map(e => ({w: e.offsetWidth, h: e.offsetHeight, role: e.getAttribute('role')})),
                    };
                }
                target.e.focus();
                // Place caret at end (so subsequent keys append)
                const sel = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(target.e);
                range.collapse(false);
                sel.removeAllRanges();
                sel.addRange(range);
                return {found: true, w: target.w, h: target.h};
            }
        """)
        if not focused.get("found"):
            raise RuntimeError(
                f"Could not find/focus prompt contenteditable. "
                f"ce_count={focused.get('debug_ce_count')} "
                f"dims={focused.get('debug_dims')}"
            )
        logger.info("flow_prompt_focused w=%d h=%d", focused.get("w", 0), focused.get("h", 0))

        # Real mouse click on the prompt — Slate needs this to activate
        # its React event handlers. After this, keyboard.type populates
        # Slate's state correctly and the submit button un-disables.
        try:
            prompt_locator = page.locator("[contenteditable='true']").first
            await prompt_locator.click(timeout=5000)
            logger.info("flow_prompt_mouse_clicked")
        except Exception as exc:
            logger.warning("flow_prompt mouse click failed: %s — keys may go to wrong target", str(exc)[:100])

        # Inject the prompt via InputEvent('beforeinput'). Slate.js (the
        # editor Flow uses) listens for beforeinput specifically —
        # page.keyboard.type fires raw keydown/keypress/keyup which the
        # browser CDP delivers, but Slate's React handlers don't pick
        # those up. So the typed text appeared in the DOM but Slate's
        # internal state stayed empty and the submit button stayed
        # aria-disabled="true". Confirmed by v13 probe.
        await page.evaluate(
            """
            (text) => {
                const ce = document.querySelector('[contenteditable=true]');
                if (!ce) return false;
                ce.focus();
                const e1 = new InputEvent('beforeinput', {
                    inputType: 'insertText',
                    data: text,
                    bubbles: true,
                    cancelable: true,
                });
                ce.dispatchEvent(e1);
                // If beforeinput wasn't preventDefault'd, fire input too.
                const e2 = new InputEvent('input', {
                    inputType: 'insertText',
                    data: text,
                    bubbles: true,
                    cancelable: true,
                });
                ce.dispatchEvent(e2);
                return true;
            }
            """,
            prompt,
        )
        await asyncio.sleep(0.5)

        # Verify Slate accepted the prompt (submit un-disables when the
        # editor has non-empty content).
        submit_state = await page.evaluate("""
            () => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const submit = buttons.find(b => /arrow_forward[\\s\\n]+(Tạo|Generate|Create|Send|Submit)/i.test(b.innerText||''));
                if (submit && submit.getAttribute('aria-disabled') !== 'true') submit.click(); return submit ? submit.getAttribute('aria-disabled') : 'no-submit';
            }
        """)
        logger.info("flow_prompt_injected submit_aria_disabled=%s", submit_state)
        if submit_state == "true":
            raise RuntimeError(
                "Slate did not accept the prompt — submit button stayed "
                "aria-disabled=true after InputEvent dispatch"
            )

        # 2) Set model / aspect / count via Flow's UI pill buttons BEFORE
        # clicking submit. This replaces the old request-interception path
        # because page.route() hangs in CloakBrowser (CDP Fetch.enable
        # never returns). DOM-driven settings are less robust against Flow
        # UI reskins, but they avoid the CDP hang entirely.
        api_patterns = ["flowMedia:batchGenerateImages", "flowCreationAgent:streamChat"]

        _ASPECT_LABEL = {
            "IMAGE_ASPECT_RATIO_LANDSCAPE": "16:9",
            "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": "4:3",
            "IMAGE_ASPECT_RATIO_SQUARE": "1:1",
            "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR": "3:4",
            "IMAGE_ASPECT_RATIO_PORTRAIT": "9:16",
        }
        # Nhãn hiện trên dropdown của Flow, tra theo tên NỘI BỘ mà phía dịch vụ
        # gửi xuống (`services/image_providers/flow_google.py::_MODEL_ALIASES`).
        #
        # Hai khoá `NARWHAL` và `IMAGEN_3_5` từng THIẾU ở đây, mà chúng lại đúng
        # là thứ `flow/banana-2` và `flow/imagen-4` gửi xuống. Thiếu nhãn thì
        # `_set_dropdown` bấm hụt, dropdown giữ nguyên lựa chọn cũ, và Flow dựng
        # bằng model còn sót lại từ lần trước — sự cố 08/08/2026: một yêu cầu
        # TẠO ẢNH chạy 98 giây rồi trừ tín dụng video Omni Flash 8 giây.
        _MODEL_LABEL = {
            "GEM_PIX_2": "Nano Banana Pro",
            "NARWHAL": "Nano Banana 2",
            "HARBOR_SEAL": "Nano Banana 2 Lite",
            "IMAGEN_3_5": "Imagen 4",
            # Giữ tên cũ làm bí danh phòng cấu hình cũ còn gửi chúng.
            # NANO_BANANA_PRO là tên CHẾT (API trả 400 khi đo 09/08/2026) nhưng
            # vẫn phải có nhãn ở đây, vì đường DOM chọn model bằng cách bấm nhãn
            # chứ không gửi tên nội bộ — thiếu nhãn là bấm hụt.
            "NANO_BANANA_PRO": "Nano Banana Pro",
            "NANO_BANANA_2": "Nano Banana 2",
            "IMAGEN_4": "Imagen 4",
        }
        aspect_label = _ASPECT_LABEL.get(aspect_ratio, aspect_ratio)
        model_label = _MODEL_LABEL.get(model, model)
        
        # Open the settings menu if it's closed
        try:
            menu_btn = page.locator("button[aria-haspopup='menu']").first
            if await menu_btn.count() > 0 and await menu_btn.get_attribute("aria-expanded") == "false":
                await menu_btn.click(timeout=1500)
                await page.wait_for_timeout(500)
        except Exception as e:
            logger.debug("flow_menu_open skipped: %s", e)

        # ── KIỂM CHỨNG TỶ LỆ KHUNG HÌNH ──────────────────────────────────────
        #
        # Chỗ này từng bấm xong là đi tiếp, với lý lẽ "bấm hụt tỷ lệ chỉ ra ảnh
        # sai kích thước — khó chịu nhưng rẻ". Không rẻ: Flow NHỚ lựa chọn theo
        # từng hồ sơ, nên một lượt VIDEO 9:16 để dropdown lại ở 9:16, rồi mọi
        # lượt ẢNH sau đó lặng lẽ ra ảnh dọc dù người dùng xin 16:9 — không log
        # nào ghi, không ai biết cho tới khi nhìn tận mắt tấm ảnh.
        async def _doc_ty_le_dang_chon() -> str:
            try:
                return await page.evaluate("""() => {
                  const ds = Array.from(document.querySelectorAll('button, div[role=button], [aria-haspopup]'));
                  const t = ds.find(b => {
                    const s = (b.innerText || '').trim();
                    return s.length < 30 && /arrow_drop_down/i.test(s)
                           && /\\d+\\s*:\\s*\\d+/.test(s);
                  });
                  if (!t) return "";
                  const m = (t.innerText || '').match(/\\d+\\s*:\\s*\\d+/);
                  return m ? m[0].replace(/\\s+/g, '') : "";
                }""")
            except Exception as _exc:
                logger.warning("flow_image: không đọc được tỷ lệ đang chọn: %s", _exc)
                return ""

        _ty_le_that = ""
        for _lan in range(2):
            await _set_dropdown(page, aspect_label, "aspect")
            _ty_le_that = await _doc_ty_le_dang_chon()
            if not _ty_le_that or _ty_le_that == aspect_label:
                break
            logger.warning("flow_image: tỷ lệ đang là %r nhưng xin %r — bấm lại",
                           _ty_le_that, aspect_label)
        # Đọc không ra thì đi tiếp (giao diện có thể đã đổi) — chỉ chặn khi đọc
        # ĐƯỢC và thấy sai, y như nhánh model bên dưới.
        if _ty_le_that and _ty_le_that != aspect_label:
            raise RuntimeError(
                f"Flow đang để khung hình {_ty_le_that!r} chứ không phải "
                f"{aspect_label!r} — dừng, chưa bấm Tạo. Flow nhớ lựa chọn theo "
                f"hồ sơ, nên một lượt video dọc để lại dropdown ở 9:16 và mọi "
                f"lượt ảnh sau đó ra ảnh dọc mà không ai hay.")

        # ── KIỂM CHỨNG MODEL — giống hệt cách đường VIDEO đã làm từ 02/08 ────
        #
        # Model là thứ duy nhất trong ba dropdown mà chọn hụt sẽ đổi hẳn LOẠI sản
        # phẩm và tiêu tín dụng của loại khác. Tỉ lệ khung hình hay số lượng bấm
        # hụt thì chỉ ra ảnh sai kích thước — nay tỷ lệ cũng được kiểm ở trên,
        # vì "sai kích thước" hoá ra là lỗi im lặng kéo dài nhiều ngày.
        #
        # KHÔNG tin vào giá trị trả về của `_set_dropdown`: có nhánh nó trả
        # `clicked`, tức "đã bấm một cái gì đó", chứ không phải "đã chọn đúng
        # mục". Phải đọc ngược cái chip đang hiện rồi so.
        _dat_ok = await _set_dropdown(page, model_label, "model")
        _model_that = ""
        try:
            _model_that = await page.evaluate("""() => {
              const ds = Array.from(document.querySelectorAll('button, div[role=button], [aria-haspopup]'));
              const t = ds.find(b => {
                const s = (b.innerText || '').trim();
                return s.length < 60 && /arrow_drop_down/i.test(s)
                       && /veo|omni|nano|banana|imagen/i.test(s);
              });
              return t ? (t.innerText || '').replace(/arrow_drop_down/ig, '').replace(/\\s+/g, ' ').trim() : "";
            }""")
        except Exception as _exc:
            logger.warning("flow_image: không đọc được model đang chọn: %s", _exc)
        logger.info("flow_image: MODEL đang chọn = %r (yêu cầu %r, đặt được=%s)",
                    _model_that, model_label, _dat_ok)

        def _chuan(s: str) -> str:
            return "".join(c for c in (s or "").lower() if c.isalnum())

        if _model_that and _chuan(model_label) not in _chuan(_model_that):
            raise RuntimeError(
                f"Flow đang để model {_model_that!r} chứ không phải "
                f"{model_label!r} — dừng, chưa bấm Tạo. Sự cố 08/08/2026: bước "
                f"này bấm hụt rồi vẫn tạo, một yêu cầu ẢNH chạy 98 giây và trừ "
                f"tín dụng video Omni Flash 8 giây.")
        if not _dat_ok and not _model_that:
            raise RuntimeError(
                f"Không đặt được model {model_label!r} và cũng không đọc được "
                f"model đang chọn — dừng để không tạo bằng model còn sót lại của "
                f"lượt trước. Nếu Flow vừa đổi giao diện thì cập nhật bảng nhãn.")
        
        # New Google Flow UI count labels: 1x, x2, x3, x4
        count_label = "1x" if count == 1 else f"x{count}"
        await _set_dropdown(page, count_label, "count")
        
        # Close the settings menu to avoid intercepting other clicks
        try:
            if await menu_btn.count() > 0 and await menu_btn.get_attribute("aria-expanded") == "true":
                await menu_btn.click(timeout=1500)
        except Exception:
            pass

        logger.info("flow_dropdowns_done aspect=%s model=%s count=%d", aspect_label, model_label, count)

        # 2.5) reCAPTCHA token helper — re-fetched FRESH on every submit attempt.
        # Enterprise score is borderline on a GPU-less server, and each token
        # rolls a new score, so the retry loop below calls this each try.
        async def _refresh_recaptcha() -> None:
            try:
                tok, sitekey = await _get_recaptcha_token(page, action="flow_generate")
                await page.evaluate(
                    """(token) => {
                        const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
                        if (ta) { ta.value = token; return true; }
                        const ta2 = document.querySelector('textarea[id*="recaptcha" i]');
                        if (ta2) { ta2.value = token; return true; }
                        const ta3 = document.querySelector('#g-recaptcha-response');
                        if (ta3) { ta3.value = token; return true; }
                        return false;
                    }""",
                    tok,
                )
                logger.info("flow_recaptcha_ok token_preview=%s", tok[:25])
            except Exception as _exc:
                logger.warning("flow_recaptcha_failed: %s", _exc)

        # 3) Click the "Tạo" submit button. On fresh projects the welcome
        # dialog overlay intercepts mouse clicks, so JS .click() is more
        # reliable — it dispatches the synthetic event directly to the
        # element and bypasses the overlay entirely.
        async def _click_generate() -> None:
            logger.info("flow_click_generate_enter")
            # Overlay was already removed at the start, but re-do it in
            # case React re-rendered the welcome dialog overlay.
            try:
                await page.evaluate("""
                    () => {
                        document.querySelectorAll('[data-state="open"]').forEach(el => {
                            if (el.getAttribute('role')) return;
                            const r = el.getBoundingClientRect();
                            if (r.width >= window.innerWidth * 0.8 && r.height >= window.innerHeight * 0.8) {
                                el.remove();
                            }
                        });
                    }
                """)
            except Exception as _exc:
                logger.warning("flow_click_overlay_remove_failed: %s", _exc)
            await asyncio.sleep(0.2)
            logger.info("flow_click_overlay_done")

            # Step 2: Find the submit button — text "arrow_forward\\nTạo"
            # (Material icon name + label as two lines).
            submit_btn = page.locator(
                "button:has-text('arrow_forward'):has-text('Tạo'), "
                "button:has-text('arrow_forward'):has-text('Generate'), "
                "button:has-text('arrow_forward'):has-text('Create')"
            ).last
            logger.info("flow_click_submit_btn_count=%d", await submit_btn.count())
            try:
                await submit_btn.click(timeout=8000)
                logger.info("flow_submit_clicked via_locator")
                return
            except Exception as exc:
                logger.warning("flow_submit locator click failed: %s — trying JS dispatch", str(exc)[:120])

            # Step 3: Fallback — full PointerEvent + MouseEvent sequence via
            # JS. React's onClick handler needs pointerdown→pointerup→click
            # to fire reliably; a bare .click() sometimes doesn't trigger
            # the synthetic event.
            dispatched = await page.evaluate("""
                () => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    let btn = buttons.find(b => /arrow_forward[\\s\\n]+(Tạo|Generate|Create|Send|Submit)/i.test(b.innerText || ''));
                    if (!btn) btn = buttons.find(b => /arrow_(forward|upward)/i.test(b.innerText || ''));
                    if (!btn) {
                        const taoes = buttons.filter(b => /Tạo/.test(b.innerText||'') && !/add_2/.test(b.innerText||''));
                        btn = taoes[taoes.length - 1];
                    }
                    if (!btn) return {clicked: false};
                    const r = btn.getBoundingClientRect();
                    const x = r.left + r.width/2, y = r.top + r.height/2;
                    const opts = {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, view: window};
                    btn.dispatchEvent(new PointerEvent('pointerdown', opts));
                    btn.dispatchEvent(new MouseEvent('mousedown', opts));
                    btn.dispatchEvent(new PointerEvent('pointerup', opts));
                    btn.dispatchEvent(new MouseEvent('mouseup', opts));
                    btn.dispatchEvent(new MouseEvent('click', opts));
                    btn.click();
                    return {clicked: true, text: (btn.innerText || '').slice(0, 50).replace(/\\n/g, '|')};
                }
            """)
            if dispatched.get("clicked"):
                logger.info("flow_submit_clicked via_dispatch text=%s", dispatched.get("text"))
                return
            logger.warning("flow_submit: no button found, trying Ctrl+Enter")
            await page.keyboard.press("Control+Enter")

        async def _reprep() -> bool:
            """For a RETRY: re-navigate to the project + re-enter the prompt so a
            subsequent submit click fires a genuinely NEW flowMedia POST. A bare
            re-click after a rejected submit fires nothing (Slate is cleared /
            the button re-disables), so a fresh page load is required. Returns
            True if the prompt was accepted (submit enabled)."""
            try:
                await page.goto(flow_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_function(
                    "() => { const c = Array.from(document.querySelectorAll('[contenteditable=true]')); return c.some(e => e.offsetWidth > 200 && e.offsetHeight > 0); }",
                    timeout=45_000,
                )
            except Exception as exc:
                logger.warning("flow_prime: no /project/ redirect: %s", str(exc)[:120])
                if "accounts.google.com" in page.url:
                    raise RuntimeError(f"Flow UI never hydrated. Profile is logged out. URL: {page.url[:100]}") from exc
                return False
            try:
                await page.evaluate("""() => {
                    document.querySelectorAll('[data-state="open"]').forEach(el => {
                        if (el.getAttribute('role')) return;
                        const r = el.getBoundingClientRect();
                        if (r.width >= window.innerWidth * 0.8 && r.height >= window.innerHeight * 0.8) { el.remove(); }
                    });
                }""")
            except Exception:
                pass
            await asyncio.sleep(0.3)
            try:
                await page.evaluate("""
                    () => {
                        const ces = Array.from(document.querySelectorAll('[contenteditable=true]'));
                        const target = ces
                            .map(e => ({e, w: e.offsetWidth, h: e.offsetHeight}))
                            .filter(x => x.w > 200 && x.h > 0)
                            .sort((a, b) => (b.w * b.h) - (a.w * a.h))[0];
                        if (target) {
                            target.e.focus();
                            const sel = window.getSelection();
                            const range = document.createRange();
                            range.selectNodeContents(target.e);
                            range.collapse(false);
                            sel.removeAllRanges();
                            sel.addRange(range);
                        }
                    }
                """)
                await page.locator("[contenteditable='true']").first.click(timeout=5000)
            except Exception:
                pass
            await page.evaluate(
                """(text) => {
                    const ce = document.querySelector('[contenteditable=true]');
                    if (!ce) return false;
                    ce.focus();
                    const sel = window.getSelection();
                    const rng = document.createRange();
                    rng.selectNodeContents(ce);
                    sel.removeAllRanges();
                    sel.addRange(rng);
                    // clear any leftover text first so we don't append on retry
                    ce.dispatchEvent(new InputEvent('beforeinput', {inputType: 'deleteContentBackward', bubbles: true, cancelable: true}));
                    ce.dispatchEvent(new InputEvent('input', {inputType: 'deleteContentBackward', bubbles: true, cancelable: true}));
                    ce.dispatchEvent(new InputEvent('beforeinput', {inputType: 'insertText', data: text, bubbles: true, cancelable: true}));
                    ce.dispatchEvent(new InputEvent('input', {inputType: 'insertText', data: text, bubbles: true, cancelable: true}));
                    return true;
                }""",
                prompt,
            )
            await asyncio.sleep(0.5)
            state = await page.evaluate(
                """() => {
                    const b = Array.from(document.querySelectorAll('button')).find(b => /arrow_forward[\\s\\n]+(Tạo|Generate|Create|Send|Submit)/i.test(b.innerText || ''));
                    return b ? b.getAttribute('aria-disabled') : 'no-submit';
                }"""
            )
            logger.info("flow_reprep submit_aria_disabled=%s", state)
            return state != "true"

        # 4) Submit with reCAPTCHA retry, bounded by a WALL-CLOCK budget so the
        # call always returns before the caller's HTTP timeout (no hangs). The
        # score is borderline on a GPU-less server, so: humanize ONCE up front
        # (the interaction history persists for the page session and benefits
        # every subsequent execute()), then retry cheaply with a fresh token
        # until one passes or the budget runs out. We stop launching new
        # attempts once too little time remains for a winning attempt (~45s to
        # generate) so a success is never cut off mid-flight.
        _budget = max(90, timeout - 15)   # seconds for the whole retry phase
        _deadline = started + _budget
        _GEN_RESERVE = 85                 # retry re-nav (~15s) + a successful POST (~60s)
        _per_try = 80
        response = None
        last_err = ""
        _attempt = 0
        images = []
        logger.info("flow_waiting_for_post budget_s=%d", _budget)
        # Heavy humanize once — the biggest controllable score signal.
        await _humanize(page, moves=8)
        while time.time() < _deadline:
            remaining = _deadline - time.time()
            if remaining < _GEN_RESERVE:
                logger.info("flow_budget_low remaining=%.0fs — stop (no room for a win)", remaining)
                break
            _attempt += 1
            if _attempt > 1:
                # A bare re-click after a reject fires NO new POST (Slate
                # cleared / submit re-disabled). Re-navigate + re-enter the
                # prompt to arm a genuinely fresh submit, then a light humanize.
                if not await _reprep():
                    last_err = "re-prep failed (prompt not accepted / nav failed)"
                    logger.warning("flow_reprep failed attempt=%d", _attempt)
                    await asyncio.sleep(1.0)
                    continue
                await _humanize(page, moves=4)
            await _refresh_recaptcha()
            try:
                logger.info("flow_submit attempt=%d budget_left=%.0fs", _attempt, _deadline - time.time())
                
                # Get baseline images to know when new ones arrive
                old_imgs = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('img')).map(img => img.src);
                }''')
                
                await _click_generate()
                
                # Wait for images to appear in the DOM
                logger.info("flow_waiting_for_dom_images")
                
                new_imgs = []
                wait_time = int(_deadline - time.time())
                for _ in range(max(1, wait_time // 2)):
                    await page.wait_for_timeout(2000)
                    curr_imgs = await page.evaluate('''() => {
                        return Array.from(document.querySelectorAll('img')).map(img => img.src);
                    }''')
                    diff = set(curr_imgs) - set(old_imgs)
                    # Ignore avatars, google icons, and svgs
                    diff = {src for src in diff if '/a/' not in src and 'avatar' not in src and not src.endswith('.svg') and 'gstatic.com' not in src}
                    if diff:
                        new_imgs = list(diff)
                        break
                    # Upstream error toast (daily limit / policy / generic fail)
                    # → no image will ever come; bail with the real reason
                    # instead of burning the rest of the budget in silence.
                    try:
                        toast = await page.evaluate('''() => {
                            const sel = '[role="alert"], [class*="toast"], [class*="Toast"], [class*="snackbar"]';
                            return Array.from(document.querySelectorAll(sel))
                                .map(e => (e.innerText || "").trim()).filter(Boolean).join(" | ");
                        }''')
                    except Exception:
                        toast = ""
                    if toast:
                        low_t = toast.lower()
                        if any(k in low_t for k in ("limit", "quota", "error", "fail", "can't", "cannot", "unable", "wrong")):
                            last_err = f"Flow UI error: {toast[:250]}"
                            logger.warning("flow_error_toast attempt=%d: %s", _attempt, toast[:200])
                            break
                
                if new_imgs:
                    images = []
                    for src in new_imgs:
                        # Convert both blob: and normal URLs to base64 within the browser context
                        # to avoid httpx 400 Bad Request errors for protected internal URLs
                        b64 = await page.evaluate('''async (url) => {
                            const response = await fetch(url);
                            const blob = await response.blob();
                            return new Promise((resolve, reject) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result);
                                reader.onerror = reject;
                                reader.readAsDataURL(blob);
                            });
                        }''', src)
                        images.append({"url": b64, "mime": "image/jpeg"})
                    logger.info("flow_extracted_images count=%d", len(images))
                    break
                    
            except Exception as exc:
                last_err = f"DOM extraction failed: {exc}"
                logger.warning("flow_dom_extract attempt=%d failed: %s", _attempt, str(exc)[:120])

                # Trình duyệt đã đóng → dừng hẳn, đừng quay vòng trên một trang
                # đã chết cho tới hết ngân sách.
                if _trang_da_dong(exc):
                    logger.warning("flow_page_closed attempt=%d — dừng vòng thử lại", _attempt)
                    break

                # Chẩn đoán reCAPTCHA/giới hạn ngày. ĐÂY LÀ ĐƯỜNG PHỤ, KHÔNG
                # ĐƯỢC PHÉP NÉM: bản cũ gọi `page.content()` trần trong chính
                # khối bắt lỗi, nên khi trang đã đóng thì bộ xử lý lỗi tự ném
                # lỗi mới, thoát khỏi vòng thử lại và biến một lần hỏng tạm
                # thành 502 mang thông báo sai chỗ ("Page.content: Target page,
                # context or browser has been closed") — che mất nguyên nhân
                # thật vốn đã nằm sẵn trong `last_err`.
                try:
                    body_text = await page.content()
                except Exception:
                    body_text = ""
                low = body_text.lower()
                is_recaptcha = "recaptcha" in low or "unusual" in low or "permission" in low
                if is_recaptcha:
                    logger.warning("flow_recaptcha_reject attempt=%d — retry fresh token", _attempt)
                    await asyncio.sleep(1.2)
                    continue
                    
                await asyncio.sleep(1.0)
                continue

    if not images:
        if not last_err:
            # No exception and no error toast — capture what the page actually
            # shows so the failure is diagnosable instead of an empty reason.
            try:
                snippet = await page.evaluate(
                    "() => (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 300)")
                last_err = f"no new images in DOM; page says: {snippet}"
            except Exception:
                last_err = "no new images in DOM (page text unavailable)"
        raise RuntimeError(
            f"Flow generate failed after {_attempt} attempts within {_budget}s budget: {last_err}"
        )

    return {
        "images": images,
        "raw": {"extracted_from_dom": True},
        "elapsed_ms": int((time.time() - started) * 1000),
        "model": model,
        "cookies": await page.context.cookies(),
    }


async def _freshen_google(page, profile: str) -> bool:
    """Google đòi lại mật khẩu giữa lượt hâm nóng → gõ mật khẩu + TOTP đã lưu.

    VÌ SAO CẦN. `_prime_flow_session` chỉ biết BẤM chọn tài khoản. Khi Google
    hỏi lại mật khẩu cho labs.google (`/challenge/pwd`) — chuyện xảy ra dù cookie
    còn nguyên và còn hạn — nó bấm đi bấm lại cùng cái tile cho tới khi Google
    trả `/signin/rejected`, và mọi lượt tạo ảnh sau đó rơi về trang marketing
    ("no new images in DOM"). Ghi nhận trong code từ 24/08, lối thoát tới nay
    vẫn là bấm tay "Chỉ đăng nhập" — đo lại 09/09 vẫn đúng chuỗi đó.

    Mật khẩu và hạt giống TOTP đã nằm sẵn trong solver (`accounts_db`), nên chỗ
    này tự đi tiếp được. Trả True khi đã gõ được gì đó.

    CỐ Ý KHÔNG tự vượt CAPTCHA: gặp `/challenge/recaptcha` thì trả False ngay để
    bên gọi báo người xử lý trên noVNC. Thử máy móc ở đó chỉ làm Google nâng mức
    chặn — đúng thứ cơ chế tạm dừng toàn hệ thống đang cố tránh.
    """
    try:
        u = page.url or ""
    except Exception:
        return False
    if "/challenge/recaptcha" in u:
        logger.info("flow_prime: gặp reCAPTCHA — nhường cho người, không thử tự qua")
        return False
    if "/challenge/pwd" not in u and "/signin/challenge" not in u:
        return False

    try:
        from ..accounts_db import resolve_account
    except Exception:
        return False
    if not profile:
        return False
    acct = resolve_account(profile) or {}
    matkhau = str(acct.get("password") or "")
    hat = str(acct.get("totp_secret") or "")
    if not matkhau:
        logger.info("flow_prime: Google đòi mật khẩu nhưng hồ sơ %s chưa lưu mật khẩu", profile)
        return False

    da_go = False
    for sel in ('input[type="password"]', 'input[name="Passwd"]',
                'input[autocomplete="current-password"]'):
        try:
            o = page.locator(sel).first
            if await o.is_visible(timeout=1500):
                await o.fill(matkhau)
                await o.press("Enter")
                da_go = True
                logger.info("flow_prime: đã gõ lại mật khẩu cho %s", profile)
                await asyncio.sleep(4.0)
                break
        except Exception:
            continue
    if not da_go:
        return False

    # 2FA: chỉ đi tiếp được khi có hạt giống TOTP. Không có thì dừng ở đây —
    # bên gọi sẽ báo cần người bấm.
    if not hat:
        return True
    try:
        import pyotp
    except Exception:
        return True
    try:
        if "/challenge/totp" not in (page.url or ""):
            await _pick_authenticator_method(page)
            await asyncio.sleep(2.0)
    except Exception:
        pass
    for sel in ('input[name="totpPin"]', 'input[id="totpPin"]',
                'input[type="tel"]', 'input[type="text"]'):
        try:
            o = page.locator(sel).first
            if await o.is_visible(timeout=1500):
                await o.fill(pyotp.TOTP(hat).now())
                await o.press("Enter")
                logger.info("flow_prime: đã gõ mã 2FA cho %s", profile)
                await asyncio.sleep(4.0)
                break
        except Exception:
            continue
    return True


async def _prime_flow_session(page, profile: str = "") -> None:
    """Prime the Flow session so subsequent project URLs render the app
    (not the marketing landing page).

    Without this, navigating straight to /tools/flow/project/<id> on a
    just-launched Chrome — even with valid Google login cookies — shows
    Google's marketing CTA page. The session has to be "warmed" by
    visiting /tools/flow root AND clicking through it.

    Empirically verified: passive wait on /tools/flow (even 100s+)
    NEVER converts the marketing landing to the app. The user MUST click
    "Create with Google Flow" to fire Google's entitlement check, which
    then redirects to /project/<auto-uuid> and primes the session for
    all subsequent /project/<id> navigations on the same context.
    """
    async def _auto_handle_google_oauth(pg) -> bool:
        """On the labs.google (AI Test Kitchen) OAuth account-chooser/consent,
        click the account row FIRST, then any consent button. Returns True if
        it clicked anything. Only silent when the base Google session is still
        alive (a dead session lands on challenge/pwd → needs _freshen_google)."""
        try:
            if "accounts.google.com" not in (pg.url or ""):
                return False
        except Exception:
            return False
        logger.info("flow_prime: on Google OAuth (%s) — clicking through", pg.url[:80])

        # Google đòi lại mật khẩu → gõ mật khẩu + TOTP đã lưu, ĐỪNG bấm lại tile.
        # Bấm lặp ở màn này chính là thứ dẫn tới `/signin/rejected` (đo 09/09).
        try:
            if "/challenge/" in (pg.url or "") and await _freshen_google(pg, profile):
                await asyncio.sleep(2.0)
                return True
        except Exception as exc:
            logger.warning("flow_prime: freshen lỗi: %s", str(exc)[:100])

        acted = False
        try:
            picked = await pg.evaluate("""() => {
                let el = document.querySelector('[data-identifier],[data-email]');
                if (!el) {
                    const rows = Array.from(document.querySelectorAll('li[data-item-index],div[role="link"],ul li'));
                    el = rows.find(r => /@/.test(r.innerText||'') && r.offsetWidth > 100);
                }
                if (!el) return false;
                (el.closest('[role="link"],li') || el).click();
                return true;
            }""")
            if picked:
                acted = True
                logger.info("flow_prime: clicked account tile on OAuth chooser")
                await asyncio.sleep(2.5)
        except Exception as exc:
            logger.warning("flow_prime: account-tile click failed: %s", str(exc)[:100])
        try:
            consented = await pg.evaluate("""() => {
                const b = Array.from(document.querySelectorAll('button,div[role="button"]'))
                    .find(x => /tiếp tục|continue|cho phép|allow|đồng ý|xác nhận|confirm/i.test(x.innerText || ''));
                if (b) { b.click(); return (b.innerText || '').slice(0, 30); }
                return null;
            }""")
            if consented:
                acted = True
                logger.info("flow_prime: clicked consent '%s'", consented)
                await asyncio.sleep(2.5)
        except Exception:
            pass
        return acted

    try:
        await page.goto(
            "https://labs.google/fx/vi/tools/flow",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
    except Exception as exc:
        logger.warning("flow_prime: goto root failed: %s", exc)
        return

    async def _app_shell_visible() -> bool:
        """Đã vào được giao diện làm việc chưa (khác trang marketing).

        Bộ dò cũ chỉ tìm "dự án mới"/"new project"/"add_2". Đo 09/09: Google đã
        đổi giao diện — trang thật giờ hiện "Tất cả nội dung nghe nhìn", "Hình
        ảnh", "Nhân vật", "Cảnh", "Thùng rác" — nên bộ dò báo trượt dù phiên ĐÃ
        vào được app, và luồng kết thúc bằng "priming ended without app shell".

        Google cũng chuyển tên miền `labs.google/fx/...` → `flow.google.com`;
        đứng trên tên miền đó mà không còn nút marketing thì coi như đã vào.
        """
        try:
            if await page.evaluate(
                """() => Array.from(document.querySelectorAll('button,a')).some(e => {
                    const t = (e.innerText || e.getAttribute('aria-label') || '').trim();
                    return /^pro$|dự án mới|new project|add_2|chỉnh sửa dự án/i.test(t);
                })"""
            ):
                return True
        except Exception:
            return False
        # Giao diện mới: thanh bên trái của studio.
        try:
            if await page.evaluate(
                """() => {
                    const t = document.body ? (document.body.innerText || '') : '';
                    const co = /tất cả nội dung nghe nhìn|all media|nhân vật|characters|thùng rác|trash/i.test(t);
                    const marketing = /tạo bằng google flow|create with google flow/i.test(t);
                    return co && !marketing;
                }"""
            ):
                return True
        except Exception:
            pass
        return False

    # Poll loop: the landing shows a transient "Đang tải" spinner, then either
    # the app shell, the marketing CTA, or a Google OAuth re-consent (when the
    # labs.google app session expired). Handle whichever appears, up to 45s.
    deadline = time.time() + 45
    marketing_clicked = False
    while time.time() < deadline:
        await asyncio.sleep(1.5)
        if await _app_shell_visible():
            logger.info("flow_prime: app shell visible (primed)")
            return
        try:
            cur = (page.url or "").lower()
        except Exception:
            cur = ""
        if "accounts.google.com" in cur:
            await _auto_handle_google_oauth(page)
            try:
                await page.wait_for_url(
                    lambda u: "accounts.google.com" not in (u or ""), timeout=15_000
                )
            except Exception:
                pass
            continue
        if not marketing_clicked:
            try:
                btn = page.locator(
                    'button:has-text("Create with Google Flow"), '
                    'button:has-text("Tạo bằng Google Flow")'
                ).first
                if await btn.count() > 0:
                    await btn.scroll_into_view_if_needed(timeout=3_000)
                    await btn.click(timeout=5_000)
                    marketing_clicked = True
                    logger.info("flow_prime: clicked 'Create with Google Flow'")
                    try:
                        await page.wait_for_url("**/project/*", timeout=8_000)
                        logger.info("flow_prime: redirected to %s", page.url)
                    except Exception:
                        pass
                    continue
            except Exception as exc:
                logger.warning("flow_prime: marketing click failed: %s", str(exc)[:100])
    logger.info("flow_prime: priming ended without app shell (url=%s)", page.url[:80])


# Dò dự án cũ mấy lượt trước khi kết luận "tài khoản chưa có dự án nào".
_SO_LUOT_DO_DU_AN = 3
_NGHI_DO_DU_AN_S = 2.5


def _loi_chua_vao_duoc_flow(url: str) -> str:
    """Câu giải thích khi luồng dừng lại ở accounts.google.com thay vì vào Flow.

    Câu cũ — "Could not find 'Dự án mới' … Account may not have Flow access" —
    gửi người đọc đi kiểm quyền Flow của tài khoản, trong khi ba trang dưới đây
    là ba nguyên nhân khác hẳn, và cả ba đều cần đúng một việc: chạy "Chỉ đăng
    nhập" cho hồ sơ đó rồi mới "Tái dùng" lại.

    Đo thật 24/08/2026: `_prime_flow_session` chỉ biết BẤM chọn tài khoản, không
    gõ được mật khẩu. Nên khi Google hỏi lại mật khẩu cho labs.google
    (`/challenge/pwd`), nó bấm đi bấm lại vào cùng cái tile, và Google kết thúc
    bằng `/signin/rejected?app_domain=https://labs.google`.
    """
    u = url or ""
    dau = f" (trang dừng ở {u[:120]})"
    if "/challenge/recaptcha" in u:
        return (f"Google đang bắt xác minh reCAPTCHA{dau}. Phải có người gõ: bấm "
                f"'Chỉ đăng nhập' cho hồ sơ này — nút đó mở trình duyệt THẤY ĐƯỢC "
                f"trên noVNC cổng 6080 — gõ xong captcha thì hệ thống tự đi tiếp. "
                f"Nút 'Tái dùng' chạy ẩn nên noVNC chỉ hiện màn hình đen.")
    if "/challenge/pwd" in u:
        return (f"Google đòi nhập lại mật khẩu cho labs.google{dau}. Lượt 'Tái dùng' "
                f"chỉ bấm chọn tài khoản chứ không gõ mật khẩu được — bấm 'Chỉ đăng "
                f"nhập' cho hồ sơ này rồi 'Tái dùng' lại.")
    if "/signin/rejected" in u:
        return (f"Google TỪ CHỐI lượt đăng nhập vào labs.google từ phiên này{dau} — "
                f"thường là hệ quả của việc bị hỏi lại mật khẩu mà lượt 'Tái dùng' "
                f"không trả lời được. Bấm 'Chỉ đăng nhập' cho hồ sơ này rồi 'Tái "
                f"dùng' lại.")
    return (f"Hồ sơ chưa vào được labs.google, còn dừng ở trang đăng nhập Google{dau}. "
            f"Bấm 'Chỉ đăng nhập' cho hồ sơ này rồi 'Tái dùng' lại.")


async def get_or_create_project(
    profile: str,
    headless: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    """Return the UUID of a Flow project the logged-in account owns,
    creating a fresh one if none exist. The profile MUST already be
    logged in (typically via /v1/session/auto-login or manual noVNC).

    Returns:
        {
          "project_id": "<uuid>",
          "action": "use_existing" | "created",
          "project_count": int,
          "elapsed_ms": int,
        }
    """
    started = time.time()
    async with pool.page(profile=profile, headless=headless) as page:
        # Prime session — handles the marketing-landing detour itself.
        await _prime_flow_session(page, profile)

        # If priming clicked "Create with Google Flow", we may already be
        # on /project/<auto-uuid>. Grab that UUID — it's a perfectly
        # usable existing project.
        import re
        cur = page.url
        m = re.search(r"/project/([0-9a-f-]+)", cur, re.I)
        if m:
            return {
                "project_id": m.group(1),
                "action": "created",  # via warmup click
                "project_count": 0,
                "elapsed_ms": int((time.time() - started) * 1000),
            }

        # Dự án CŨ: dò nhiều lượt rồi mới dám kết luận "tài khoản chưa có dự án".
        #
        # Một lượt quét DOM duy nhất là quá sớm. Danh sách dự án render sau khi
        # trang đã domcontentloaded, nên "không thấy link /project/ nào" phần lớn
        # là "chưa kịp hiện" chứ không phải "chưa có dự án" — mà kết luận sai đó
        # đi thẳng xuống nhánh bấm "Dự án mới", tức ĐẺ THÊM một dự án nữa vào
        # tài khoản đã có sẵn dự án. Có dự án cũ thì dùng lại, đừng tạo mới.
        existing: list[str] = []
        for lan in range(_SO_LUOT_DO_DU_AN):
            result = await page.evaluate(
                """() => {
                    const links = Array.from(document.querySelectorAll('a[href*="/project/"]'))
                        .map(a => (a.href.match(/\\/project\\/([0-9a-f-]+)/i) || [])[1])
                        .filter(Boolean);
                    return {existing: links};
                }"""
            )
            existing = result.get("existing", [])
            if existing:
                break
            if lan < _SO_LUOT_DO_DU_AN - 1:
                await asyncio.sleep(_NGHI_DO_DU_AN_S)
        if existing:
            return {
                "project_id": existing[0],
                "action": "use_existing",
                "project_count": len(existing),
                "elapsed_ms": int((time.time() - started) * 1000),
            }

        # Chưa vào được ứng dụng Flow thì DỪNG, đừng đi bấm "Dự án mới": trang
        # đăng nhập của Google cũng "không có dự án nào" theo đúng phép dò trên,
        # và ba lượt bấm + re-prime phía dưới chỉ tốn thêm ~20 giây để rồi ném ra
        # một câu đổ lỗi cho quyền Flow của tài khoản.
        if "accounts.google.com" in (page.url or ""):
            raise RuntimeError(_loi_chua_vao_duoc_flow(page.url))

        # No projects — click "Dự án mới" / "New project" button.
        clicked = await page.evaluate(
            """() => {
                const btn = Array.from(document.querySelectorAll('button')).find(
                    b => /add_2|dự án mới|new project/i.test(
                      b.innerText || b.getAttribute('aria-label') || ''
                    )
                );
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not clicked:
            # Fallback 1: wait 5s and retry (page may still be hydrating)
            logger.warning("get_or_create_project: 'New project' button not found, waiting 5s and retrying...")
            await asyncio.sleep(5)
            clicked = await page.evaluate(
                """() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(
                        b => /add_2|dự án mới|new project|create|tạo/i.test(
                          b.innerText || b.getAttribute('aria-label') || ''
                        )
                    );
                    if (!btn) return false;
                    btn.click();
                    return true;
                }"""
            )
        if not clicked:
            # Fallback 2: re-prime session (clears marketing landing) then retry
            logger.warning("get_or_create_project: still not found, re-priming session...")
            await _prime_flow_session(page, profile)
            cur2 = page.url
            m2 = re.search(r"/project/([0-9a-f-]+)", cur2, re.I)
            if m2:
                return {
                    "project_id": m2.group(1),
                    "action": "created",
                    "project_count": 0,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }
            clicked = await page.evaluate(
                """() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(
                        b => /add_2|dự án mới|new project/i.test(
                          b.innerText || b.getAttribute('aria-label') || ''
                        )
                    );
                    if (!btn) return false;
                    btn.click();
                    return true;
                }"""
            )
        if not clicked:
            u = page.url
            if "accounts.google.com" in u:
                raise RuntimeError(_loi_chua_vao_duoc_flow(u))
            raise RuntimeError(
                f"Could not find 'Dự án mới' / 'New project' button (page: {u[:120]}). "
                "Account may not have Flow access or session is expired."
            )



        try:
            await page.wait_for_url("**/project/*", timeout=20_000)
        except Exception as exc:
            raise RuntimeError(
                f"New-project click did not redirect to /project/<uuid> ({exc})"
            ) from exc

        import re
        m = re.search(r"/project/([0-9a-f-]+)", page.url, re.I)
        if not m:
            raise RuntimeError(f"Could not extract UUID from URL: {page.url}")
        return {
            "project_id": m.group(1),
            "action": "created",
            "project_count": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

def _ghi_anh_tam(nguon: str, ten: str) -> str | None:
    """Ghi ảnh (data URL / base64 thuần / http URL) ra tệp tạm để nạp lên Flow.

    Trả về đường dẫn, hoặc None nếu không lấy được ảnh.
    """
    import base64
    import tempfile
    from pathlib import Path

    raw = b""
    s = (nguon or "").strip()
    if not s:
        return None
    if s.startswith(("http://", "https://")):
        try:
            import httpx
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                r = client.get(s)
                r.raise_for_status()
                raw = r.content
        except Exception as exc:
            logger.warning("flow_video: tải ảnh %s lỗi: %s", ten, str(exc)[:120])
            return None
    else:
        try:
            raw = base64.b64decode(s.split(",", 1)[1] if s.startswith("data:") else s)
        except Exception as exc:
            logger.warning("flow_video: giải mã ảnh %s lỗi: %s", ten, str(exc)[:120])
            return None
    if not raw:
        return None
    duoi = ".png"
    if raw[:3] == b"\xff\xd8\xff":
        duoi = ".jpg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        duoi = ".webp"
    p = Path(tempfile.gettempdir()) / f"flow_{ten}{duoi}"
    p.write_bytes(raw)
    return str(p)


async def _gan_khung_hinh(page, anh_dau: str | None, anh_cuoi: str | None,
                          mo_bang_cai_dat) -> None:
    """Gán ảnh ĐẦU và/hoặc ẢNH CUỐI cho video qua tab "Khung hình" của Flow.

    Chuỗi bấm dưới đây ĐO THẬT trên giao diện Flow 31/07/2026 (không suy đoán):

      chip cài đặt  →  tab "Khung hình"  (button[role=tab], có aria-selected)
      →  ô "Bắt đầu" / "Kết thúc"  (div 50×50, cursor:pointer)  → mở hộp chọn media
      →  mục "Tệp tải lên" ở cột trái
      →  nút "Tải nội dung nghe nhìn lên"  → mở hộp chọn tệp của trình duyệt
      →  tệp xuất hiện trong danh sách và được chọn sẵn (có xem trước)
      →  nút "Thêm vào câu lệnh"  → ảnh vào ô

    Lưu ý: mặc định Flow đang ở tab "Thành phần" (ảnh tham chiếu), KHÔNG phải
    "Khung hình" — bỏ bước bấm tab thì ảnh vào sai chỗ. Lỗi ở bất kỳ bước nào
    cũng chỉ ghi log rồi bỏ qua: video không có ảnh đầu vẫn hơn là không có video.
    """
    async def _bam(js, mo_ta, tham=None, cho=2.0) -> bool:
        box = await (page.evaluate(js, tham) if tham is not None else page.evaluate(js))
        if not box:
            logger.warning("flow_khung: KHÔNG thấy %s", mo_ta)
            return False
        await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
        logger.info("flow_khung: đã bấm %s", mo_ta)
        await asyncio.sleep(cho)
        return True

    # Mẫu chữ phải NEO hai đầu. Nếu để lỏng thì "khung hình" khớp luôn tooltip
    # của nút hoán đổi ("Hoán đổi khung hình đầu tiên và cuối cùng") và ta bấm
    # nhầm nút đó — đo thật: nút này nằm ngay cạnh hai ô ảnh.
    _JS_THEO_CHU = """(mau) => {
      const ds = Array.from(document.querySelectorAll('button, [role=tab], div[role=button]'));
      const re = new RegExp(mau, 'i');
      const t = ds.find(b => {
        const s = (b.innerText || '').trim().replace(/\\n/g, ' ');
        return s.length < 50 && re.test(s);
      });
      if (!t) return null;
      const r = t.getBoundingClientRect();
      if (!r.width || !r.height) return null;
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }"""

    async def _tab_khung_dang_chon() -> str | None:
        return await page.evaluate("""() => {
          const t = Array.from(document.querySelectorAll('button[role=tab]'))
            .find(b => /^(crop_free\\s*)?khung h\\u00ecnh$/i.test(
              (b.innerText || '').trim().replace(/\\n/g, ' ')));
          return t ? t.getAttribute('aria-selected') : null;
        }""")
    # Ô ảnh: div nhỏ 30–90px, cursor:pointer, chữ đúng bằng nhãn.
    _JS_O = """(nhan) => {
      const t = Array.from(document.querySelectorAll('div')).find(e => {
        if ((e.innerText || '').trim() !== nhan) return false;
        const r = e.getBoundingClientRect();
        return r.width > 30 && r.width < 90 && r.height > 30 && r.height < 90
               && getComputedStyle(e).cursor === 'pointer';
      });
      if (!t) return null;
      const r = t.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }"""

    # Tab "Khung hình" chỉ tồn tại khi bảng cài đặt đang MỞ. Các bước trước có
    # thể đã đóng nó (bấm ra ngoài, chọn xong dropdown), nên mở lại nếu không thấy.
    if await _tab_khung_dang_chon() is None:
        await mo_bang_cai_dat()
        await asyncio.sleep(1.2)
    # CHỈ bấm khi tab CHƯA được chọn. Bấm vào tab đang chọn làm Flow đóng bảng —
    # đo thật 31/07: lượt trước đã ở "Khung hình", ta bấm thêm một lần và bảng
    # đóng, lần đọc aria-selected sau đó ra None nên cả phần ảnh bị bỏ.
    if await _tab_khung_dang_chon() != "true":
        await _bam(_JS_THEO_CHU, "tab 'Khung hình'", r"^(crop_free\s*)?khung hình$")
    da_chon = await _tab_khung_dang_chon()
    if da_chon != "true":
        logger.warning("flow_khung: tab Khung hình aria-selected=%r (mong 'true') — "
                       "ảnh có thể vào tab 'Thành phần' sai chỗ, bỏ qua", da_chon)
        return
    logger.info("flow_khung: tab 'Khung hình' đang được chọn (đã kiểm chứng)")

    for nhan, nguon, ten in (("Bắt đầu", anh_dau, "dau"), ("Kết thúc", anh_cuoi, "cuoi")):
        if not nguon:
            continue
        duong_dan = _ghi_anh_tam(nguon, ten)
        if not duong_dan:
            continue
        if not await _bam(_JS_O, f"ô '{nhan}'", nhan, cho=2.5):
            continue
        await _bam(_JS_THEO_CHU, "mục 'Tệp tải lên'", r"tệp tải lên")
        nut = await page.evaluate(_JS_THEO_CHU, r"tải nội dung nghe nhìn lên")
        if not nut:
            logger.warning("flow_khung: KHÔNG thấy nút tải lên cho ô '%s'", nhan)
            continue
        try:
            async with page.expect_file_chooser(timeout=15_000) as fc:
                await page.mouse.click(nut["x"] + nut["w"] / 2, nut["y"] + nut["h"] / 2)
            chooser = await fc.value
            await chooser.set_files(duong_dan)
            logger.info("flow_khung: đã nạp tệp cho ô '%s': %s", nhan, duong_dan)
        except Exception as exc:
            logger.warning("flow_khung: nạp tệp cho ô '%s' lỗi: %s", nhan, str(exc)[:140])
            continue
        # Chờ ĐÚNG dấu hiệu: TÊN TỆP xuất hiện trong danh sách "Tệp tải lên".
        #
        # Không chờ nút "Thêm vào câu lệnh": nút đó có sẵn trong hộp thoại từ
        # trước (đo thật 31/07), nên bấm theo nó là bấm khi tệp còn đang lên —
        # ô ảnh vẫn trống. Tên tệp trên danh sách mới là bằng chứng đã lên xong.
        ten_tep = duong_dan.rsplit("/", 1)[-1]
        _JS_HANG_TEP = """(ten) => {
          const ds = Array.from(document.querySelectorAll('div, li, button'));
          const t = ds.find(e => {
            const s = (e.innerText || '').trim();
            if (!s.includes(ten)) return false;
            const r = e.getBoundingClientRect();
            return r.width > 100 && r.height > 20 && r.height < 120;
          });
          if (!t) return null;
          const r = t.getBoundingClientRect();
          return {x: r.x, y: r.y, w: r.width, h: r.height};
        }"""
        hang = None
        for _ in range(24):
            await asyncio.sleep(2.0)
            hang = await page.evaluate(_JS_HANG_TEP, ten_tep)
            if hang:
                break
        if not hang:
            logger.warning("flow_khung: tệp %r KHÔNG lên danh sách sau 48s — bỏ ô '%s'",
                           ten_tep, nhan)
            continue
        logger.info("flow_khung: tệp %r đã lên danh sách, bấm chọn", ten_tep)
        await page.mouse.click(hang["x"] + hang["w"] / 2, hang["y"] + hang["h"] / 2)
        await asyncio.sleep(1.5)

        them = await page.evaluate(_JS_THEO_CHU, r"thêm vào câu lệnh")
        if not them:
            logger.warning("flow_khung: KHÔNG thấy nút 'Thêm vào câu lệnh' cho ô '%s'", nhan)
            continue
        await page.mouse.click(them["x"] + them["w"] / 2, them["y"] + them["h"] / 2)
        await asyncio.sleep(2.5)
        co_anh = await page.evaluate("""(nhan) => {
          const t = Array.from(document.querySelectorAll('div')).find(e => {
            const r = e.getBoundingClientRect();
            return r.width > 30 && r.width < 90 && r.height > 30 && r.height < 90
                   && getComputedStyle(e).cursor === 'pointer'
                   && (e.innerText || '').trim() === nhan;
          });
          // Ô còn nguyên chữ nhãn ⇒ CHƯA có ảnh; có ảnh thì Flow thay bằng thumbnail.
          return !t;
        }""", nhan)
        logger.info("flow_khung: ô '%s' %s", nhan,
                    "ĐÃ có ảnh (nhãn biến mất)" if co_anh else "VẪN trống (nhãn còn)")


async def flow_generate_video(
    project_id: str,
    prompt: str,
    aspect_ratio: str = "16:9",
    model: str = "flow/veo-3.1-lite",
    duration: str | None = None,
    count: int = 1,
    image_b64: str | None = None,
    last_frame_b64: str | None = None,
    profile: str = "google-fx",
    headless: bool = True,
    timeout: int = 300,
) -> dict:
    """Tạo video qua Google Flow:
    1. Đăng ký network interceptor TRƯỚC khi navigate (không miss response).
    2. Click UI "Tạo" để Google's own JS xử lý reCAPTCHA (bypass 403).
    3. Interceptor bắt op_name + bearer từ batchAsyncGenerateVideoText response.
    4. Poll batchCheckAsyncVideoGenerationStatus cho đến SUCCESSFUL.
    5. Lấy fifeUrl từ operations[0].operation.metadata.video.fifeUrl.
    """
    import json as _json, time, asyncio, re as _re
    started = time.time()

    model_name_map = {
        "flow/veo-3.1-lite":    "veo_3_1_t2v_lite",
        "veo-3.1-lite":         "veo_3_1_t2v_lite",
        "flow/veo-3.1-fast":    "veo_3_1_t2v_fast",
        "veo-3.1-fast":         "veo_3_1_t2v_fast",
        "flow/veo-3.1-quality": "veo_3_1_t2v_quality",
        "veo-3.1-quality":      "veo_3_1_t2v_quality",
        "flow/omni-flash":      "abra_t2v_10s",
        "omni-flash":           "abra_t2v_10s",
        "veo/veo-3.1-generate-preview": "veo_3_1_t2v_lite",
        "veo-3.1-generate-preview": "veo_3_1_t2v_lite",
    }
    is_portrait = aspect_ratio == "9:16"
    model_key = model_name_map.get(model, "veo_3_1_t2v_lite")
    if is_portrait and not model_key.endswith("_portrait"):
        model_key += "_portrait"
    aspect_label = "9:16" if is_portrait else "16:9"
    aspect_enum = "VIDEO_ASPECT_RATIO_PORTRAIT" if is_portrait else "VIDEO_ASPECT_RATIO_LANDSCAPE"

    async with pool.page(profile=profile, headless=headless) as page:
        # ── Interceptor đăng ký TRƯỚC mọi navigation ─────────────────────
        _cap: dict = {
            "bearer":    None,
            "op_name":   None,
            "op_scene":  None,
            "op_status": None,
            "media_name": None,
            "video_url": None,
        }

        async def _on_resp(resp):
            try:
                url = resp.url
                # Bắt bearer từ request header
                if "aisandbox-pa.googleapis.com" in url and not _cap["bearer"]:
                    ah = resp.request.headers.get("authorization", "")
                    if ah.startswith("Bearer "):
                        _cap["bearer"] = ah[7:]
                        logger.info("flow_video: bearer captured len=%d", len(_cap["bearer"]))

                # THÂN YÊU CẦU THẬT mà giao diện Flow gửi đi. Đường REST
                # (`flow_rest.py`) phải dựng lại đúng thân này, và 09/08/2026
                # nó nhận 403 "The caller does not have permission" ở đường
                # video trong khi đường ảnh chạy tốt — nghĩa là còn lệch ở đâu
                # đó mà thông báo của Google không chỉ ra. Ghi lại một lần cho
                # có bản đối chiếu; không có bí mật nào trong thân (bearer nằm
                # ở header), nên cắt 900 ký tự là đủ mà không lộ gì.
                if "aisandbox-pa.googleapis.com/v1/video:" in url and resp.request.method == "POST":
                    try:
                        logger.info("flow_video: THÂN YÊU CẦU THẬT %s -> %s",
                                    url.rsplit("/", 1)[-1],
                                    (resp.request.post_data or "")[:900])
                    except Exception:
                        pass

                # Bắt thông tin gen từ batchAsyncGenerateVideoText response
                if "batchAsyncGenerateVideoText" in url and resp.request.method == "POST":
                    try:
                        body = await resp.json()
                        if "remainingCredits" in body:
                            _cap["remaining_credits"] = body["remainingCredits"]
                            
                        # N8N format: operations[]
                        ops = body.get("operations", [])
                        if ops:
                            op = ops[0]
                            _cap["op_name"]   = op.get("operation", {}).get("name") or op.get("name")
                            _cap["op_scene"]  = op.get("sceneId", "")
                            _cap["op_status"] = op.get("status", "")
                            logger.info("flow_video: op_name=%s status=%s", _cap["op_name"], _cap["op_status"])
                        
                        # UI Web format: media[]
                        media_arr = body.get("media", [])
                        if media_arr:
                            _cap["media_name"] = media_arr[0].get("name")
                            logger.info("flow_video: media_name=%s", _cap["media_name"])
                    except Exception as e:
                        logger.warning("flow_video: parse gen response failed: %s", e)
                        
                # UI Load profile
                if "getUserProfile" in url:
                    try:
                        body = await resp.json()
                        if "remainingCredits" in body:
                            _cap["remaining_credits"] = body["remainingCredits"]
                    except Exception:
                        pass

                # Bắt fifeUrl nếu có sẵn
                if "batchCheckAsyncVideoGenerationStatus" in url or "aisandbox" in url or "media.poll" in url:
                    try:
                        text = await resp.text()
                        if "aisandbox" in url:
                            logger.info("--- FLOW API RESPONSE CAPTURE ---")
                            logger.info("URL: %s", url)
                            if len(text) < 2000:
                                logger.info("RESP: %s", text)
                            else:
                                logger.info("RESP (truncated): %s", text[:2000])
                            logger.info("-------------------------------------")

                        if "media.poll" in url and "MEDIA_URL_TYPE_MP4" in text or "gcsUri" in text or "fifeUrl" in text or "storage.googleapis.com" in text:
                            logger.info("flow_video: media.poll returned: %s", text[:800])
                            
                        m = _re.search(r'"fifeUrl"\s*:\s*"([^"]+)"', text)
                        if not m:
                            m = _re.search(r'"gcsUri"\s*:\s*"([^"]+)"', text)
                        if not m:
                            m = _re.search(r'"url"\s*:\s*"([^"]+\.mp4[^"]*)"', text)
                            
                        if m and not _cap["video_url"]:
                            _cap["video_url"] = m.group(1)
                            logger.info("flow_video: fifeUrl intercepted from %s", url)
                    except Exception:
                        pass
            except Exception:
                pass

        async def _on_req(req: Request):
            try:
                url = req.url
                if "aisandbox-pa.googleapis.com/v1/video" in url:
                    logger.info("--- FLOW API RAW REQUEST CAPTURE ---")
                    logger.info("METHOD: %s", req.method)
                    logger.info("URL: %s", url)
                    try:
                        body = req.post_data_json if req.method == "POST" else None
                        if body:
                            logger.info("POST_DATA: %s", json.dumps(body, ensure_ascii=False, separators=(',', ':')))
                            if "generateVideoText" in url:
                                media_arr = body.get("media", [])
                                if media_arr:
                                    _cap["media_name"] = media_arr[0].get("name")
                    except:
                        pass
                    logger.info("------------------------------------")
            except Exception:
                pass

        page.on("request", _on_req)
        page.on("response", _on_resp)

        # ── Warm up + navigate ────────────────────────────────────────────
        await _prime_flow_session(page, profile)
        nav_url = f"https://labs.google/fx/vi/tools/flow/project/{project_id}"
        await page.goto(nav_url, wait_until="domcontentloaded", timeout=30_000)

        try:
            await page.wait_for_function(
                "() => document.querySelector('[contenteditable=true]') !== null",
                timeout=45_000,
            )
        except Exception as exc:
            raise RuntimeError(f"Flow UI never hydrated: {exc}") from exc

        # ── Xóa overlay ───────────────────────────────────────────────────
        await page.evaluate("""() => {
            document.querySelectorAll('[data-state="open"]').forEach(el => {
                if (!el.getAttribute('role')) el.remove();
            });
        }""")

        # ── Chuyển sang tab Video — BẮT BUỘC, có KIỂM CHỨNG ───────────────
        #
        # Bản cũ so khớp `/^video$/i` với innerText của nút. KHÔNG BAO GIỜ khớp:
        # Flow dùng Material Symbols nên innerText của nút tab là "videocam\nVideo"
        # (tên ligature của icon dính liền nhãn) — đúng khuôn mà chính file này đã
        # xử lý cho nút gửi: /arrow_forward[\s\n]+(Tạo|Generate)/.
        #
        # Hậu quả đo thật 31/07 (chủ máy xem trên noVNC): giao diện ĐỨNG NGUYÊN ở
        # tab "Hình ảnh" với model ảnh Nano Banana 2, credit còn 1050 (KHÔNG hết
        # hạn mức). Nghĩa là mọi lượt "tạo video" thực chất đi tạo ẢNH, rồi:
        #   · veo-3.1-lite/fast/omni-flash → chờ video trong DOM đến hết hạn (~360s)
        #   · veo-3.1-quality             → "Không click được nút Tạo: disabled"
        # Bản cũ chỉ bấm rồi TIN, không kiểm lại, nên sai này im lặng hoàn toàn.
        async def _dang_o_tab_video() -> bool:
            """Đọc CHIP model cạnh nút gửi — nơi duy nhất phản ánh chế độ hiện tại.

            KHÔNG dùng document.body.innerText: mọi ảnh cũ trong thư viện đều dán
            nhãn model đã tạo nó ("🍌 Nano Banana 2"), nên điều kiện "body không
            chứa Nano Banana" KHÔNG BAO GIỜ đúng được — bộ kiểm chứng luôn báo
            thất bại dù đã chuyển tab. Đã sập bẫy này ngày 31/07.

            DOM thật (đo 31/07): button[aria-haspopup=menu] có innerText
            "🍌 Nano Banana 2|crop_16_9|x1" ở chế độ ảnh; ở chế độ video chip đổi
            sang tên Veo/Omni.
            """
            return bool(await page.evaluate("""() => {
              // Tìm chip cài đặt TRỰC TIẾP, không đi qua nút gửi.
              //
              // Bản cũ neo vào nút gửi (arrow_forward) rồi mới lần ra khung nhập.
              // Trang nào không có nút gửi là mù hoàn toàn: hàm trả false, vòng
              // lặp kết luận "vẫn ở chế độ tạo ẢNH" rồi ném lỗi — dù chip ngay
              // trên trang đã ghi "Video · 8s". Đo thật 02/08 (profile
              // google-nguoi-a): dump DOM thấy 0 nút chứa arrow_forward, ô nhập
              // textarea offsetParent=null, mà chip 'Video · 8s|crop_16_9|x1' thì
              // hiện rõ. Cùng lúc profile google-nguoi-b CÓ nút gửi — nên đây
              // là trạng thái theo từng trang, không phải Flow đổi giao diện.
              //
              // Dấu hiệu nhận chip: có 'crop_' (icon tỷ lệ khung) — thứ chỉ chip
              // cài đặt mới có. Nhãn model của tác phẩm cũ trong thư viện không
              // kèm 'crop_', nên không lẫn (đúng cái bẫy chú thích 31/07 nói).
              const chip = Array.from(document.querySelectorAll('button[aria-haspopup=menu]'))
                .find(b => /crop_/.test(b.innerText || ''));
              if (!chip) return false;
              const s = (chip.innerText || '').trim();
              // DOM thật 31/07 — chip đổi hẳn nội dung theo chế độ:
              //   chế độ ẢNH  : "🍌 Nano Banana 2|crop_16_9|x1"
              //   chế độ VIDEO: "Video · 8s|crop_16_9|x1"
              // Trước đó tôi tìm "veo|omni" trong chip nên KHÔNG BAO GIỜ khớp dù
              // đã chuyển tab thành công — lần thứ ba sập bẫy "kiểm bằng dấu hiệu
              // đoán chứ không phải dấu hiệu đọc được từ DOM".
              return /(^|[\\s|])video(\\s*[·.]|\\b)/i.test(s) && !/nano\\s*banana|imagen/i.test(s);
            }"""))

        async def _bam_chuot_that(mo_ta: str, tim_js: str) -> bool:
            """Bấm bằng CHUỘT THẬT vào phần tử do `tim_js` trả về toạ độ.

            KHÔNG dùng element.click() trong JS: giao diện Flow là React và menu
            chỉ mở khi nhận pointer event thật. Đo thật 31/07: gọi JS .click() lên
            chip model xong `aria-expanded` VẪN là false, bảng cài đặt không mở.
            Cách này trùng với `_set_dropdown` phía dưới trong cùng file (đã chạy
            được cho phần tạo ảnh) — dùng page.mouse.click theo bounding box.
            """
            box = await page.evaluate(tim_js)
            if not box:
                return False
            try:
                await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
                logger.info("flow_video: đã bấm chuột thật vào %s", mo_ta)
                return True
            except Exception as exc:
                logger.warning("flow_video: bấm %s lỗi: %s", mo_ta, exc)
                return False

        # Mọi tìm kiếm dưới đây PHẢI giới hạn trong KHUNG NHẬP, không quét cả trang.
        #
        # Vì sao: thư viện hiển thị mỗi tác phẩm kèm TÊN MODEL đã tạo nó. Sau khi có
        # một video Veo trong thư viện, quét cả trang tìm "veo|nano banana" sẽ bám
        # vào nhãn của tác phẩm cũ thay vì chip cài đặt. Đo thật 31/07: chạy lẻ một
        # lượt thì THÀNH CÔNG (113s, video 2,7 MB), nhưng chạy loạt 5 model thì cả 4
        # model flow trượt trong 12–16s — đúng vì thư viện đã có video Veo từ lượt
        # trước, và trước đó chưa có nên lượt lẻ mới chạy được.
        _JS_KHUNG = """
          const bs = Array.from(document.querySelectorAll('button'));
          const send = bs.find(b => /arrow_forward/i.test(b.innerText || ''));
          if (!send) return null;
          let khung = send;
          for (let i = 0; i < 5 && khung.parentElement; i++) khung = khung.parentElement;
        """

        # Chip cài đặt cạnh nút gửi — nơi MỞ RA bảng chứa cặp tab Hình ảnh/Video.
        # Đo thật: khi bảng chưa mở, trong DOM KHÔNG có nút "Video" nào cả.
        _JS_CHIP = """() => {
          %s
          const ds = Array.from(khung.querySelectorAll('button[aria-haspopup=menu], button, div[role=button]'));
          const chip = ds.find(b => {
            const s = (b.innerText || b.textContent || '').trim();
            if (!s || s.length > 60) return false;
            // Ở chế độ tạo ẢNH chip ghi tên model ("🍌 Nano Banana 2|crop_16_9|x1"),
            // nhưng ở chế độ tạo VIDEO nó ghi "Video · 4s|crop_16_9|x1" — KHÔNG có
            // tên model nào. Đo thật 31/07: thiếu nhánh 'video ·' thì lượt sau (khi
            // Flow còn nhớ chế độ video) không mở được bảng cài đặt, nên model/thời
            // lượng/số lượng đều giữ nguyên của lượt trước.
            return /nano\\s*banana|veo|omni|imagen|video\\s*[\\u00b7.]/i.test(s);
          });
          if (!chip) return null;
          const r = chip.getBoundingClientRect();
          if (!r.width || !r.height) return null;
          return {x: r.x, y: r.y, w: r.width, h: r.height};
        }""" % _JS_KHUNG

        # Tab "Video": chữ đứng RIÊNG, cho phép tên icon đứng trước ("videocam").
        # Bảng bật lên KHÔNG nằm trong khung nhập (portal ra body) nên phần này
        # vẫn quét cả trang — nhưng chặn theo độ dài chữ nên không bắt nhãn thư viện.
        _JS_TAB = """() => {
          const ds = Array.from(document.querySelectorAll('button, [role=tab], div[role=button]'));
          const vt = ds.find(b => {
            const s = (b.innerText || b.textContent || '').trim();
            if (s.length > 24) return false;
            // LOẠI nút điều hướng thư viện: 'videocam|Xem video', 'image|Xem hình
            // ảnh', 'delete|Xem thùng rác'… Chúng khớp /(^|\\s)video$/ nên bản cũ
            // bấm vào đấy, chỉ đổi cách hiển thị thư viện chứ không đổi chế độ.
            // Đo thật 02/08: log ghi "đã bấm chuột thật vào tab Video" ĐỦ 3 lần
            // rồi vẫn báo không chuyển được — vì cả 3 lần bấm nhầm 'Xem video'.
            if (/(^|[\\s\\n|])Xem\\s/i.test(s)) return false;
            return /(^|[\\s\\n])video$/i.test(s);
          });
          if (!vt) return null;
          const r = vt.getBoundingClientRect();
          if (!r.width || !r.height) return null;
          return {x: r.x, y: r.y, w: r.width, h: r.height};
        }"""

        async def _mo_bang_cai_dat() -> bool:
            return await _bam_chuot_that("chip model (mở bảng cài đặt)", _JS_CHIP)

        async def _bam_tab_video() -> bool:
            return await _bam_chuot_that("tab Video", _JS_TAB)

        for _lan in range(3):
            if await _dang_o_tab_video():
                break
            # Cặp tab nằm TRONG bảng cài đặt → phải mở bảng trước mỗi lần thử.
            if await _mo_bang_cai_dat():
                logger.info("flow_video: đã mở bảng cài đặt (lần %d)", _lan + 1)
                await asyncio.sleep(1.2)
            if not await _bam_tab_video():
                try:   # dự phòng: locator khớp chuỗi con
                    loc = page.locator("button, [role=tab]").filter(
                        has_text=_re.compile(r"(^|\s)Video$", _re.I)).first
                    if await loc.count():
                        await loc.click(timeout=3000)
                except Exception:
                    pass
            await asyncio.sleep(2.0)
        if not await _dang_o_tab_video():
            # DỪNG ở đây thay vì đi tiếp rồi tạo ẢNH và báo "hết hạn chờ video".
            # Liệt kê RỘNG (kèm div[role=button], nới độ dài) để lần sau đọc log là
            # biết bảng cài đặt có mở ra hay không, không phải đoán.
            _thay = await page.evaluate(
                """() => Array.from(document.querySelectorAll('button,[role=tab],div[role=button]'))
                     .map(b => (b.innerText||'').trim().replace(/\\n/g,'|'))
                     .filter(s => s && s.length < 60).slice(0, 40)""")
            # Dump DOM THẬT quanh khung nhập: chip mở bảng cài đặt KHÔNG phải
            # <button> (đo 31/07: không xuất hiện trong danh sách nút), nên phải
            # xem cấu trúc thật mới biết bấm vào đâu, đừng đoán tiếp.
            _quanh = await page.evaluate("""() => {
              const bs = Array.from(document.querySelectorAll('button'));
              const send = bs.find(b => /arrow_forward/i.test(b.innerText||''));
              if (!send) return 'KHÔNG thấy nút gửi';
              let box = send;
              for (let i = 0; i < 4 && box.parentElement; i++) box = box.parentElement;
              const ds = Array.from(box.querySelectorAll('*')).slice(0, 60).map(e => {
                const t = (e.innerText || '').trim().replace(/\\n/g, '|').slice(0, 40);
                if (!t) return null;
                const at = ['role', 'aria-haspopup', 'aria-expanded', 'aria-label', 'data-testid']
                  .map(a => e.hasAttribute(a) ? a + '=' + e.getAttribute(a) : null)
                  .filter(Boolean).join(' ');
                return e.tagName.toLowerCase() + (at ? '[' + at + ']' : '') + ' :: ' + t;
              }).filter(Boolean);
              return [...new Set(ds)].slice(0, 30);
            }""")
            # Tả ĐÚNG thứ đọc được, đừng kết luận "vẫn ở chế độ tạo ẢNH".
            #
            # Câu cũ khẳng định chế độ ảnh trong khi thực tế chip đã là "Video · 8s"
            # và vấn đề là KHÔNG CÓ khung nhập. Chủ máy đọc thông báo đó rồi hỏi
            # "sửa flow kiểu gì lại lỗi rồi" — thông báo đẩy người đọc đi sai hướng
            # đắt hơn nhiều so với việc nó dài thêm một dòng.
            _chip = await page.evaluate("""() => {
              const c = Array.from(document.querySelectorAll('button[aria-haspopup=menu]'))
                .find(b => /crop_/.test(b.innerText || ''));
              return c ? (c.innerText || '').trim().replace(/\\n/g, '|') : "";
            }""")
            _co_o_nhap = await page.evaluate(
                """() => Array.from(document.querySelectorAll('textarea, [contenteditable=true]'))
                     .some(e => !!e.offsetParent)""")
            _ly_do = (
                "khung nhập không có trên trang (không thấy nút gửi, ô nhập ẩn) — "
                "dự án này đang ở chế độ xem thư viện, không phải màn soạn"
                if not _co_o_nhap else
                f"chip cài đặt đọc được là {_chip!r}, không khớp chế độ video")
            raise RuntimeError(
                f"Không vào được màn soạn video của Flow: {_ly_do}. "
                f"chip={_chip!r} · có_ô_nhập={_co_o_nhap}. "
                f"Các nút thấy được: {_thay} || DOM quanh khung nhập: {_quanh}")
        logger.info("flow_video: đã chuyển sang tab Video (đã kiểm chứng)")

        # Đúng chế độ video rồi nhưng KHÔNG có khung soạn thì vẫn vô ích — và tệ
        # hơn là nó thất bại RẤT CHẬM: `_type_prompt` nuốt lỗi ("type prompt
        # error") rồi vòng bấm "Tạo" quay tới hết ngân sách (≥300s) mới bỏ cuộc.
        # Chặn ngay tại đây, và diễn đạt lỗi chứa "không vào được màn soạn" +
        # "chưa bấm Tạo" để api/veo_video.py nhận ra là lỗi TRƯỚC khi tiêu tín dụng
        # và đổi sang tài khoản Flow khác thay vì trả 502 cho người dùng.
        _khung = await page.evaluate("""() => ({
          oNhap: Array.from(document.querySelectorAll('textarea, [contenteditable=true]'))
                   .some(e => !!e.offsetParent),
          nutGui: Array.from(document.querySelectorAll('button'))
                   .some(b => /arrow_forward/i.test(b.innerText || '')),
        })""")
        # CHỈ đòi Ô NHẬP, KHÔNG đòi nút gửi.
        #
        # Nút gửi của Flow chỉ hiện SAU khi ô nhập có chữ — đòi nó trước khi gõ là
        # đòi thứ chưa thể tồn tại. Đo thật 02/08 (google-nguoi-a): bản đầu của
        # chốt này bắt cả hai, gặp `ô nhập hiện=True, nút gửi=False` rồi bỏ tài
        # khoản đó oan trong khi khung soạn ĐÃ có, chỉ chưa gõ gì.
        if not _khung.get("oNhap"):
            raise RuntimeError(
                "Không vào được màn soạn video của Flow: dự án này không có khung "
                f"soạn (ô nhập hiện={_khung.get('oNhap')}, "
                f"nút gửi={_khung.get('nutGui')}). Chưa bấm Tạo, chưa tiêu tín dụng "
                "— cần thử tài khoản Flow khác hoặc mở lại dự án trên noVNC.")
        if not _khung.get("nutGui"):
            logger.info("flow_video: chưa thấy nút gửi — bình thường, nó hiện sau "
                        "khi ô nhập có chữ; đi tiếp để gõ câu lệnh")
        # Ghi lại các nút CÓ THẬT trong bảng ở chế độ video. Chủ máy hỏi "tab video
        # có chọn thời lượng không" — cứ ghi ra rồi đọc log, đừng đoán. Nếu không có
        # nút 4s/8s/10s thì lời gọi _set_dropdown(duration) bên dưới là vô nghĩa.
        try:
            _nut_bang = await page.evaluate(
                """() => Array.from(document.querySelectorAll('button,[role=tab],[role=menuitem],div[role=button]'))
                     .map(b => (b.innerText||'').trim().replace(/\\n/g,'|'))
                     .filter(s => s && s.length < 30).slice(0, 40)""")
            logger.info("flow_video: nút trong bảng chế độ video = %s", _nut_bang)
        except Exception:
            pass
        # ── Tắt chế độ "Tác nhân" (Agent) nếu đang bật ────────────────────
        try:
            # Nếu bật Tác nhân, Google sẽ khóa/ẩn menu chọn Model
            agent_btn = page.locator("button[aria-pressed='true']:has-text('Tác nhân'), button[aria-pressed='true']:has-text('Agent')").first
            # count() không wait, nên ta dùng try/except click trực tiếp với timeout ngắn
            await agent_btn.click(timeout=1500)
            logger.info("flow_video: Disabled 'Tác nhân' (Agent) mode to unlock model selection")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # ── Hàm chọn giá trị trong giao diện ──────────────────────────────
        async def _set_dropdown(pg: Page, target_text: str, label: str):
            try:
                # 1. Thử tìm và click trực tiếp nếu element đã hiển thị sẵn (ví dụ: nút aspect ratio hoặc menu đã mở)
                locs = pg.locator(f"text='{target_text}'")
                count = await locs.count()
                
                clicked = False
                for i in range(count - 1, -1, -1):
                    el = locs.nth(i)
                    if await el.is_visible():
                        box = await el.bounding_box()
                        if box:
                            await pg.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                            clicked = True
                            await asyncio.sleep(0.5)
                            break
                            
                # Nếu không thấy (vì menu đang đóng), tìm nút Trigger để mở menu
                if not clicked:
                    trigger_box = await pg.evaluate("""([txt, lbl]) => {
                        const btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        let keywords = [];
                        if (lbl === 'model') keywords = ['veo', 'omni', 'flash'];
                        else if (lbl === 'aspect') keywords = ['16:9', '9:16', '1:1', 'portrait', 'landscape', 'square'];
                        else if (lbl === 'duration') keywords = ['4s', '8s', '5s', '10s'];
                        else if (lbl === 'count') keywords = ['1x', 'x1', '2x', 'x2', '3x', '4x'];
                        else keywords = [txt];
                        
                        let trigger = btns.find(b => {
                            const bt = (b.textContent || '').toLowerCase().trim();
                            return keywords.some(k => bt.includes(k));
                        });
                        if (trigger) {
                            const r = trigger.getBoundingClientRect();
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                        return null;
                    }""", [target_text, label])
                    
                    if trigger_box:
                        await pg.mouse.click(trigger_box['x'], trigger_box['y'])
                        await asyncio.sleep(1.5) # Đợi React render xong menu
                    else:
                        logger.warning("flow_dropdown_skip %s=%s (Trigger not found)", label, target_text)
                        return False

                # Sau khi click trigger (hoặc click lần 1), kiểm tra xem nó có mở ra một dropdown list không
                # Bằng cách tìm lại đúng text đó, nếu có nhiều hơn 1 element visible -> có dropdown
                await asyncio.sleep(0.5)
                locs = pg.locator(f"text='{target_text}'")
                count = await locs.count()

                # Thường option trong menu sẽ là phần tử cuối cùng hiển thị trên màn hình
                for i in range(count - 1, -1, -1):
                    el = locs.nth(i)
                    if await el.is_visible():
                        # Kiểm tra xem đây có phải trigger không (trigger thường có aria-haspopup hoặc aria-expanded)
                        is_trigger = await el.evaluate("(node) => { return node.hasAttribute('aria-haspopup') || node.hasAttribute('aria-expanded'); }")
                        if not is_trigger:
                            box = await el.bounding_box()
                            if box:
                                await pg.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                                logger.info("flow_dropdown_set %s=%s (Physical Option Click)", label, target_text)
                                await asyncio.sleep(0.5)
                                return True

                # Tới đây nghĩa là KHÔNG bấm được mục nào trong menu. Nếu bước 1 đã
                # bấm thẳng vào mục đang hiện (hàng kiểu tablist) thì coi như xong;
                # còn nếu bước 1 không bấm gì thì ta chỉ vừa mở menu rồi thôi — đó
                # là THẤT BẠI, đừng ghi log như đã đặt được.
                logger.info("flow_dropdown_set %s=%s (Physical Trigger Click/Direct Toggle, clicked=%s)",
                            label, target_text, clicked)
                await asyncio.sleep(0.5)
                return clicked

            except Exception as e:
                logger.warning("flow_dropdown_error %s=%s: %s", label, target_text, e)

            await asyncio.sleep(0.5)
            return False

        # ── Set model/aspect/duration/count dropdowns ─────────────────────

        async def _bao_dam_bang_mo(vi_sao: str) -> None:
            """Mở lại bảng cài đặt nếu nó đã đóng.

            Chọn model làm Flow vẽ lại bảng và ĐÓNG nó — đo thật 31/07: lượt đặt
            model xong thì hàng thời lượng biến mất, `_set_dropdown(duration)`
            không tìm thấy gì rồi im lặng bỏ qua, và phần kiểm chứng đọc ra rỗng.
            Nên trước mỗi hàng phải chắc bảng còn mở.
            """
            co_hang = await page.evaluate("""() =>
              Array.from(document.querySelectorAll('[aria-selected]'))
                .some(b => /^(x\\d|\\dx|\\d+s)$/i.test((b.innerText || '').trim()))
            """)
            if not co_hang:
                logger.info("flow_video: bảng cài đặt đã đóng — mở lại (%s)", vi_sao)
                await _mo_bang_cai_dat()
                await asyncio.sleep(1.2)

        # 1. Aspect Ratio
        await _bao_dam_bang_mo("trước khi đặt tỷ lệ")
        if aspect_ratio == "16:9":
            await _set_dropdown(page, "16:9", "aspect")
        elif aspect_ratio == "9:16":
            await _set_dropdown(page, "9:16", "aspect")
        elif aspect_ratio == "1:1":
            await _set_dropdown(page, "1:1", "aspect")

        # 2. Số bản ghi — hàng "x1/x2/x3/x4" trên bảng cài đặt.
        #
        # Trước đây dòng này là `_set_dropdown(page, "1x", "count")` với chú thích
        # "luôn = 1": tham số `count` của hàm bị BỎ HẲN. Người dùng chọn x4 ở tab
        # Tạo Video, được báo giá 4 video, rồi nhận về 1 video. Nhãn đúng theo
        # giao diện Flow: video 1 bản là "1x", nhiều bản là "x2"/"x3"/"x4" (giống
        # generate_image ở trên). Tín dụng nhân lên theo số bản — xem bảng giá ở
        # khối "Thời lượng" phía dưới.
        # Nhãn trong bảng VIDEO là "x1/x2/x3/x4" — KHÔNG phải "1x" như bên tạo ảnh.
        # Đo thật 31/07: gửi "1x" thì _set_dropdown không tìm thấy mục nào, rơi về
        # bấm trigger, và hàng số lượng giữ nguyên 'x2' của lượt trước ⇒ người dùng
        # xin 1 video nhưng bị trừ tín dụng của 2. Phần kiểm chứng bên dưới bắt
        # được đúng ca này ("LỆCH so_luong — yêu cầu '1x' nhưng giao diện đang 'x2'").
        count = max(1, min(4, int(count or 1)))
        await _bao_dam_bang_mo("trước khi đặt số bản ghi")
        await _set_dropdown(page, f"x{count}", "count")
        
        # 3. Model
        _MODEL_LABEL = {
            "veo_3_1_t2v":         "Veo 3.1",
            "veo_3_1_t2v_lite":    "Veo 3.1 - Lite",
            "veo_3_1_t2v_fast":    "Veo 3.1 - Fast",
            "veo_3_1_t2v_quality": "Veo 3.1 - Quality",
            "abra_t2v_8s":         "Omni Flash",
            "abra_t2v_10s":        "Omni Flash",
        }
        model_lbl = _MODEL_LABEL.get(model_key.replace("_portrait", ""), "Veo 3.1 - Lite")

        # Ghi lại DANH SÁCH MODEL THẬT kèm số tín dụng, TRƯỚC khi chọn. Bảng
        # _MODEL_LABEL ở trên là nhãn ta ĐOÁN; nếu Flow đổi tên hoặc đổi giá thì
        # _set_dropdown lặng lẽ không tìm thấy và Flow dùng model đang chọn sẵn —
        # tạo ra video bằng model KHÁC model người dùng yêu cầu mà không ai biết.
        # Mở menu model rồi dump ra để đối chiếu (chủ máy yêu cầu 31/07).
        try:
            # PHẢI dùng _bao_dam_bang_mo (chỉ mở khi đang đóng), KHÔNG bấm thẳng
            # vào chip. Chip là nút BẬT/TẮT: hàng "số bản ghi" ngay trên đã để bảng
            # ở trạng thái MỞ, nên bấm chip thêm một cái là ĐÓNG nó lại. Đo thật
            # 02/08 (lượt kiểm chứng 12:11): bấm chip lúc 12:11:13 → 12:11:14 đọc
            # "DANH SÁCH MODEL = []" rồi "flow_dropdown_skip model=Veo 3.1 - Lite
            # (Trigger not found)", và ngay dòng sau là "bảng cài đặt đã đóng — mở
            # lại". Model không bao giờ được đặt; Flow chạy bằng model còn sót của
            # lượt trước (Omni Flash 8s, 12 tín dụng) dù người dùng chọn Lite.
            await _bao_dam_bang_mo("trước khi chọn model")
            # Trong bảng, danh sách model còn nằm sau MỘT lớp nữa: nút xổ có chữ
            # "<tên model> arrow_drop_down". Bấm nốt lớp đó mới thấy các lựa chọn
            # kèm số tín dụng. Đo thật: bỏ bước này thì chỉ đọc được đúng nhãn của
            # nút đang đóng ("Veo 3.1 - Lite | arrow_drop_down").
            _JS_XO_MODEL = """() => {
              const ds = Array.from(document.querySelectorAll('button, div[role=button], [aria-haspopup]'));
              const t = ds.find(b => {
                const s = (b.innerText || '').trim();
                return s.length < 60 && /arrow_drop_down/i.test(s)
                       && /veo|omni|nano|banana|imagen/i.test(s);
              });
              if (!t) return null;
              const r = t.getBoundingClientRect();
              if (!r.width || !r.height) return null;
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }"""
            if await _bam_chuot_that("nút xổ danh sách model", _JS_XO_MODEL):
                await asyncio.sleep(1.5)
            _ds_model = await page.evaluate("""() => {
              const ds = Array.from(document.querySelectorAll(
                '[role=menuitem], [role=option], [role=menuitemradio], li, button, div[role=button]'));
              return [...new Set(ds.map(e => (e.innerText || '').trim().replace(/\\n/g, ' · '))
                .filter(s => s && s.length < 110 &&
                  /veo|omni|nano|banana|imagen|tín dụng|credit/i.test(s)))].slice(0, 30);
            }""")
            logger.info("flow_video: DANH SÁCH MODEL + TÍN DỤNG = %s", _ds_model)
            logger.info("flow_video: ta sẽ chọn nhãn = %r (từ model_key=%r)", model_lbl, model_key)
        except Exception as _exc:
            logger.warning("flow_video: không đọc được danh sách model: %s", _exc)

        _dat_model_ok = await _set_dropdown(page, model_lbl, "model")

        # ── KIỂM CHỨNG MODEL — hàng đắt nhất trong bảng, phải soi kỹ nhất ─────
        #
        # Đặt model trượt thì Flow im lặng dùng model còn sót của lượt trước, và
        # người dùng trả tiền cho model họ KHÔNG chọn: xin Lite (10 tín dụng)
        # nhận Omni Flash 8s (12), hoặc tệ hơn là dính Quality (100). Bộ kiểm
        # chứng chung bên dưới chỉ soi thời lượng + số lượng nên không bắt được.
        # Thà dừng và báo lỗi còn hơn tiêu tín dụng vào model sai.
        _model_that = ""
        try:
            _model_that = await page.evaluate("""() => {
              const ds = Array.from(document.querySelectorAll('button, div[role=button], [aria-haspopup]'));
              const t = ds.find(b => {
                const s = (b.innerText || '').trim();
                return s.length < 60 && /arrow_drop_down/i.test(s)
                       && /veo|omni|nano|banana|imagen/i.test(s);
              });
              return t ? (t.innerText || '').replace(/arrow_drop_down/ig, '').replace(/\\s+/g, ' ').trim() : "";
            }""")
        except Exception as _exc:
            logger.warning("flow_video: không đọc được model đang chọn: %s", _exc)
        logger.info("flow_video: MODEL đang chọn = %r (yêu cầu %r, đặt được=%s)",
                    _model_that, model_lbl, _dat_model_ok)

        def _chuan(s: str) -> str:
            return "".join(c for c in (s or "").lower() if c.isalnum())

        if _model_that and _chuan(model_lbl) not in _chuan(_model_that):
            logger.error("flow_video: LỆCH MODEL — yêu cầu %r nhưng giao diện đang %r",
                         model_lbl, _model_that)
            return {"state": "failed", "error_code": "model_mismatch",
                    "error": f"Flow đang để model {_model_that!r} chứ không phải "
                             f"{model_lbl!r} — dừng để không tiêu tín dụng vào "
                             f"model sai. Chưa bấm Tạo."}
        if not _dat_model_ok and not _model_that:
            logger.error("flow_video: không đặt được model %r và cũng không đọc "
                         "được model đang chọn — dừng", model_lbl)
            return {"state": "failed", "error_code": "model_unverified",
                    "error": f"Không đặt được model {model_lbl!r} trên giao diện "
                             f"Flow và không đọc được model đang chọn — dừng để "
                             f"không tiêu tín dụng vào model sai. Chưa bấm Tạo."}

        # Số tín dụng KHÔNG nằm trong menu model — Flow chỉ ghi MỘT dòng cho lựa
        # chọn HIỆN TẠI ("Quá trình tạo sẽ tốn N tín dụng"). Đọc dòng đó sau khi đã
        # chọn model để biết đúng giá của model này, và để phát hiện sớm nếu Flow
        # đổi giá hoặc nếu ta chọn nhầm model.
        try:
            _tin_dung = await page.evaluate("""() => {
              const ds = Array.from(document.querySelectorAll('*'));
              for (const e of ds) {
                const s = (e.innerText || '').trim();
                if (s && s.length < 80 && /tín dụng|credit/i.test(s)) return s.replace(/\\n/g, ' ');
              }
              return null;
            }""")
            logger.info("flow_video: TÍN DỤNG cho %r = %r", model_lbl, _tin_dung)
        except Exception:
            pass

        # ── Thời lượng: CHỈ Omni Flash có hàng chọn 4s/6s/8s/10s ──────────────
        #
        # Bảng giá THẬT, đọc từ giao diện Flow (chủ máy chụp noVNC 31/07/2026),
        # cột "Quá trình tạo sẽ tốn N tín dụng", mỗi video (x1):
        #
        #   Omni Flash          4s → 7 tín dụng   6s → 10   8s → 12   10s → 15
        #   Veo 3.1 - Lite      10 tín dụng   (KHÔNG có hàng chọn thời lượng)
        #   Veo 3.1 - Fast      20 tín dụng   (KHÔNG có hàng chọn thời lượng)
        #   Veo 3.1 - Quality   100 tín dụng  (KHÔNG có hàng chọn thời lượng)
        #
        # Chọn x2/x3/x4 thì số tín dụng NHÂN LÊN theo số video.
        #
        # Vì vậy gọi _set_dropdown(duration) cho model Veo là VÔ NGHĨA: hàng đó
        # không tồn tại, hàm sẽ không tìm thấy rồi im lặng bỏ qua. Tệ hơn, bộ tìm
        # trigger của nó nhận cả '4s','8s','5s','10s' làm từ khoá nên có thể bấm
        # nhầm phần tử khác trên trang. Chỉ đặt thời lượng khi model có hàng đó.
        _CO_CHON_THOI_LUONG = model_key.startswith("abra_")   # Omni Flash
        dur_da_dat: str | None = None    # thời lượng THẬT SỰ đã đặt, để trả về
        if duration and _CO_CHON_THOI_LUONG:
            dur_str = str(duration)
            if not dur_str.endswith("s"):
                dur_str += "s"
            await _bao_dam_bang_mo("trước khi đặt thời lượng")
            await _set_dropdown(page, dur_str, "duration")
            dur_da_dat = dur_str
        elif duration:
            logger.info(
                "flow_video: BỎ QUA duration=%r — model %r không có hàng chọn thời "
                "lượng (chỉ Omni Flash có). Muốn đổi độ dài thì dùng flow/omni-flash.",
                duration, model_lbl)

        # ── KIỂM CHỨNG các lựa chọn vừa đặt ───────────────────────────────
        #
        # Bảng cài đặt của Flow đánh dấu lựa chọn bằng `aria-selected` (đo thật
        # 31/07: hàng 4s/6s/8s/10s và hàng x1..x4 đều có). Nhờ đó biết ta có chọn
        # ĐÚNG hay không, thay vì bấm rồi tin — `_set_dropdown` không tìm thấy
        # mục nào thì im lặng bỏ qua, và Flow chạy bằng lựa chọn còn sót lại của
        # lượt trước.
        try:
            await _bao_dam_bang_mo("trước khi kiểm chứng")
            _dang_chon = await page.evaluate("""() => {
              const ra = {};
              for (const b of Array.from(document.querySelectorAll('[aria-selected=true]'))) {
                const s = (b.innerText || '').trim().replace(/\\n/g, '|');
                if (!s || s.length > 40) continue;
                if (/^\\d+s$/i.test(s)) ra.thoi_luong = s;
                else if (/^x\\d$/i.test(s)) ra.so_luong = s;
                else if (/^crop_\\d+_\\d+\\|/.test(s) || /^\\d+:\\d+$/.test(s)) ra.ty_le = s;
                else if (/khung hình|thành phần/i.test(s)) ra.che_do_anh = s;
                else if (/hình ảnh|video/i.test(s)) ra.tab = s;
              }
              const chip = Array.from(document.querySelectorAll('button[aria-haspopup=menu]'))
                .map(b => (b.innerText || '').trim().replace(/\\n/g, '|'))
                .find(s => s && s.length < 60);
              if (chip) ra.chip = chip;
              return ra;
            }""")
            logger.info("flow_video: ĐANG CHỌN = %s", _dang_chon)
            _mong = {"thoi_luong": dur_da_dat, "so_luong": f"x{count}"}
            for _k, _v in _mong.items():
                if not _v:
                    continue
                _that = str(_dang_chon.get(_k) or "")
                # 'x1' trên giao diện hiện là '1x' ở hàng số lượng — so lỏng theo số.
                if _that.replace("x", "") != _v.replace("x", "").replace("s", "").rstrip("s") \
                        and _that.rstrip("s") != _v.rstrip("s"):
                    logger.warning("flow_video: LỆCH %s — yêu cầu %r nhưng giao diện đang %r",
                                   _k, _v, _that or "(không đọc được)")
        except Exception as _exc:
            logger.warning("flow_video: không kiểm chứng được lựa chọn: %s", _exc)

        # ── Ảnh đầu / ảnh cuối (tab "Khung hình") ─────────────────────────
        if image_b64 or last_frame_b64:
            await _gan_khung_hinh(page, image_b64, last_frame_b64, _mo_bang_cai_dat)

        # ── Humanize ──────────────────────────────────────────────────────
        await _humanize(page, moves=8)

        # ── Nhập prompt ───────────────────────────────────────────────────
        async def _type_prompt():
            try:
                box = page.locator("[contenteditable='true']").first
                # Click vật lý để tạo event isTrusted=true, qua mặt ReCAPTCHA
                await box.click(force=True)
                await asyncio.sleep(0.2)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)
                
                # Dùng insert_text (tương đương với hành động Paste/Ctrl+V của người thật)
                await page.keyboard.insert_text(prompt)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning("flow_video: type prompt error: %s", e)
            await asyncio.sleep(1.0)
            
            # Secondary check: if still disabled, type another char natively
            disabled = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                let btn = btns.find(b => {
                    const txt = (b.textContent || '');
                    return /arrow_forward/i.test(txt) && /(Tạo|Generate|Create|Send)/i.test(txt);
                });
                if (!btn) btn = btns.find(b => /^(Tạo|Generate|Create)$/i.test((b.textContent||'').trim()));
                return btn ? btn.getAttribute('aria-disabled') === 'true' : true;
            }""")
            if disabled:
                try:
                    await page.keyboard.type(" ")
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

        await _type_prompt()

        # Lấy danh sách các video cũ trên DOM trước khi click
        old_vids = await page.evaluate("() => Array.from(document.querySelectorAll('video')).map(v => v.src)")

        # ── Click nút "Tạo" - retry tối đa 3 lần ────────────────────────
        _budget = max(300, timeout)
        _deadline = started + _budget
        _attempt = 0

        while time.time() < _deadline:
            _attempt += 1
            logger.info("flow_video_submit attempt=%d", _attempt)

            await _humanize(page, moves=3)

            clicked = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                let btn = btns.find(b => {
                    const txt = (b.textContent || '');
                    return /arrow_forward/i.test(txt) && /(Tạo|Generate|Create|Send)/i.test(txt);
                });
                if (!btn) btn = btns.find(b => /^(Tạo|Generate|Create)$/i.test((b.textContent||'').trim()) && b.getAttribute('aria-disabled') !== 'true');
                if (!btn) return {ok: false, reason: 'not_found'};
                if (btn.getAttribute('aria-disabled') === 'true') return {ok: false, reason: 'disabled'};
                btn.click();
                return {ok: true};
            }""")
            logger.info("flow_video_click=%s", clicked)

            if not clicked.get("ok"):
                if _attempt >= 3:
                    raise RuntimeError(f"Không click được nút Tạo sau {_attempt} lần: {clicked.get('reason')}")
                await _type_prompt()
                await asyncio.sleep(1.5)
                continue

            # ── Chờ video render trên DOM (không phụ thuộc cấu trúc API) ──
            #
            # Chọn x2/x3/x4 thì Flow sinh ngần ấy video và trừ ngần ấy tín dụng.
            # Trước đây khối này dùng `.find()` rồi `break` ngay ở video ĐẦU
            # TIÊN, nên chọn 4 video là trả tiền 4 nhưng chỉ nhận về 1. Nay gom
            # cho đủ `count`; hết giờ mà chưa đủ thì lấy những cái đã có và GHI
            # LOG rõ thiếu mấy cái — không im lặng cắt bớt.
            wait_start = time.time()
            nav_detected = False
            video_urls: list[str] = []
            remaining_credits = None

            while time.time() - wait_start < 300:
                # Trình duyệt đóng giữa lúc chờ render thì mọi `page.evaluate`
                # bên dưới ném lỗi thô của Playwright, lọt thẳng ra ngoài thành
                # 502 khó hiểu. Kiểm một lần ở đầu mỗi vòng để dừng sớm với lý
                # do đọc được — cùng bệnh đã sửa ở `generate_image`.
                if page.is_closed():
                    raise RuntimeError(
                        "Trình duyệt đóng giữa lúc chờ video render — chưa lấy được "
                        f"video nào sau {int(time.time() - wait_start)}s")
                # Phát hiện UI navigate sang gallery
                if not nav_detected:
                    nav = await page.evaluate("""() =>
                        Array.from(document.querySelectorAll('button')).some(b => /arrow_back/i.test(b.innerText||''))
                    """)
                    if nav:
                        nav_detected = True
                        logger.info("flow_video: UI navigated (submit OK) - waiting for video DOM")

                if nav_detected:
                    # Kiểm tra xem có video nào MỚI load xong chưa
                    moi = await page.evaluate("""(old_vids) => {
                        const progress = document.querySelector('div[role="progressbar"]');
                        if (progress) return [];   // Vẫn đang gen

                        const vids = Array.from(document.querySelectorAll('video')).map(v => v.src);
                        const news = vids.filter(src => src && !old_vids.includes(src));
                        if (news.length) return news;

                        // Chưa có thẻ video, thử click vào thumbnail đầu tiên
                        // Trong Google Labs, gallery có các ảnh img hoặc div
                        const firstThumb = document.querySelector('img[alt*="Hiện tượng"], img[src*="blob:"], main img, [data-slate-editor] ~ div img');
                        if (firstThumb) {
                            firstThumb.click();
                            console.log("Clicked thumbnail to open video modal");
                        }

                        return [];
                    }""", old_vids)

                    for src in (moi or []):
                        if src not in video_urls:
                            video_urls.append(src)
                    if video_urls:
                        logger.info("flow_video: có %d/%d video mới trên DOM",
                                    len(video_urls), count)
                    if len(video_urls) >= count:
                        break

                await asyncio.sleep(4)

            if video_urls:
                if len(video_urls) < count:
                    logger.warning(
                        "flow_video: CHỈ lấy được %d/%d video sau %ds — đã trừ tín dụng cho "
                        "%d video. Kiểm tra lại hàng 'Số bản ghi' trên giao diện Flow.",
                        len(video_urls), count, int(time.time() - wait_start), count)
                break

            # Miss response - reload + retry
            logger.warning("flow_video: no video_url after 300s, retrying (attempt %d)", _attempt)
            if _attempt >= 3:
                raise RuntimeError("Không lấy được video từ DOM sau 3 lần click Tạo (quá timeout)")
            await page.goto(nav_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                await page.wait_for_function(
                    "() => document.querySelector('[contenteditable=true]') !== null",
                    timeout=20_000,
                )
            except Exception:
                pass
            await _type_prompt()
            await asyncio.sleep(0.5)

        if not video_urls:
            raise RuntimeError(f"Timeout {int(time.time()-started)}s waiting for video DOM.")

        video_urls = video_urls[:count]
        logger.info("flow_video: got %d video_url, cái đầu %s",
                    len(video_urls), video_urls[0][:100])

        # ── Download bytes trong browser (có session cookies) ─────────────
        _TAI_MOT = """async (src) => {
            const r = await fetch(src);
            const blob = await r.blob();
            return new Promise((res, rej) => {
                const reader = new FileReader();
                reader.onloadend = () => res(reader.result);
                reader.onerror = rej;
                reader.readAsDataURL(blob);
            });
        }"""
        b64_list: list[str] = []
        for _i, _src in enumerate(video_urls):
            try:
                _b64 = await page.evaluate(_TAI_MOT, _src)
                logger.info("flow_video: b64[%d] len=%d", _i, len(_b64 or ""))
            except Exception as exc:
                logger.warning("flow_video: download video %d failed: %s", _i, exc)
                _b64 = ""
            b64_list.append(_b64 or "")

        # ── Lấy số dư tín dụng ────────────────────────────────────────────
        remaining_credits = _cap.get("remaining_credits")
        if remaining_credits is None:
            try:
                credits_str = await page.evaluate("""async () => {
                    const r = await fetch('https://labs.google/fx/api/trpc/user.getUserProfile?batch=1', {
                        headers: {'x-trpc-source': 'nextjs-react'}
                    });
                    const data = await r.json();
                    return data[0].result.data.json.remainingCredits;
                }""")
                if credits_str is None:
                    # Fallback text extract from UI
                    credits_str = await page.evaluate("""() => {
                        const el = document.querySelector('[class*="TokenIndicator"]');
                        return el ? parseInt((el.innerText || '0').replace(/\\D/g, ''), 10) : null;
                    }""")
                if credits_str is not None:
                    remaining_credits = int(credits_str)
                    logger.info("flow_video: active poll remaining_credits=%s", remaining_credits)
            except Exception as e:
                logger.warning("flow_video: fetch credits failed: %s", e)

        return {
            "created": int(time.time()),
            "data": [{
                "url": _u,
                "b64_json": b64_list[_i],
                "metadata": {
                    "source":           "flow_ui_intercept",
                    "elapsed_s":        int(time.time() - started),
                    "model":            model_key,
                    "duration":         dur_da_dat,
                    "count_requested":  count,
                    "count_returned":   len(video_urls),
                    "remainingCredits": remaining_credits,
                    "operation_name":   _cap.get("op_name"),
                }
            } for _i, _u in enumerate(video_urls)]
        }

