# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/chatgpt_web.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
'''ChatGPT Web (chatgpt.com) DOM-scrape helpers.

Uses the same browser_pool as the rest of the captcha-solver so profiles
that were logged in via /v1/chatgpt/onboard can be reused for direct
chat / image-gen / vision calls against the chatgpt.com SPA.
'''

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from typing import Any

import httpx

from ..browser_pool import pool

logger = logging.getLogger(__name__)

_CHATGPT_HOME = 'https://chatgpt.com/'


async def _scrape_models(page) -> list[dict[str, Any]]:
    '''Fetch /backend-api/models inside the page context.'''
    try:
        result = await page.evaluate('''
            async () => {
                const r = await fetch('/backend-api/models', { credentials: 'include' });
                const text = await r.text();
                try { return {status: r.status, json: JSON.parse(text)}; }
                catch { return {status: r.status, text: text.slice(0, 500)}; }
            }
        ''')
        if isinstance(result, dict) and result.get('status') == 200:
            data = result.get('json', {})
            models = data.get('models', []) if isinstance(data, dict) else []
            return models
        return []
    except Exception:
        return []


async def _wait_for_ready(page, timeout: int = 20) -> None:
    """Wait until the chatgpt.com page is interactive (prompt editor visible)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            editor = page.locator('#prompt-textarea, [data-testid=chat-input], div[contenteditable=true]').first
            if await editor.count() > 0:
                try:
                    await editor.wait_for(state='visible', timeout=3000)
                except Exception:
                    await asyncio.sleep(0.5)
                    continue
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)


async def _resolve_image_to_file(image: str) -> tuple[str, str]:
    """Resolve a data: URL or http(s) URL to a temp file. Returns (filepath, mime)."""
    if image.startswith("data:"):
        header, b64 = image.split(",", 1)
        mime = header.split(";")[0].replace("data:", "")
        data = base64.b64decode(b64)
    elif image.startswith(("http://", "https://")):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            r = await client.get(image)
            r.raise_for_status()
        data = r.content
        mime = (r.headers.get("content-type") or "image/png").split(";")[0].strip()
    else:
        raise ValueError("image must be data: URL or http(s) URL")

    ext = mime.split("/")[1] if "/" in mime else "png"
    tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name, mime


async def list_models(
    profile: str = 'chatgpt-default',
    headless: bool = True,
    timeout: int = 30,
) -> dict[str, Any]:
    started = time.time()
    async with pool.page(profile=profile, headless=headless) as page:
        try:
            await page.goto(_CHATGPT_HOME, wait_until='domcontentloaded', timeout=timeout * 1000)
            await asyncio.sleep(2.0)
        except Exception:
            pass
        models = await _scrape_models(page)
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            'profile': profile,
            'count': len(models),
            'models': models,
            'elapsed_ms': elapsed_ms,
        }


async def chat(
    profile: str = 'chatgpt-default',
    prompt: str = '',
    timeout: int = 90,
    headless: bool = False,
) -> dict[str, Any]:
    started = time.time()
    stages = {}
    async with pool.page(profile=profile, headless=headless) as page:
        # Navigate
        nav_ok = False
        try:
            await page.goto(_CHATGPT_HOME, wait_until='domcontentloaded', timeout=20_000)
            nav_ok = True
        except Exception as e:
            logger.warning('chatgpt_web nav failed: %s', str(e)[:120])
        if nav_ok:
            await asyncio.sleep(2.0)
        stages['nav_ok'] = nav_ok

        await _wait_for_ready(page, timeout=15)
        ready = await page.locator('#prompt-textarea, [data-testid=chat-input], div[contenteditable=true]').count() > 0
        stages['editor_visible'] = ready

        # Type and send via JS to bypass CloakBrowser stability checks
        sent = False
        if ready:
            try:
                # Use JS to type into contenteditable and click send
                sent = await page.evaluate("""
                    async (promptText) => {
                        // Find editor
                        const editor = document.querySelector('#prompt-textarea, [data-testid=chat-input], div[contenteditable=true]');
                        if (!editor) return false;

                        // Focus and type
                        editor.focus();
                        if (editor.getAttribute('contenteditable') === 'true' || editor.isContentEditable) {
                            editor.innerText = promptText;
                            editor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: promptText}));
                        } else {
                            // Input/textarea fallback
                            editor.value = promptText;
                            editor.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                        await new Promise(r => setTimeout(r, 300));

                        // Find send button - try data-testid, then aria-label, then the SVG button
                        let btn = document.querySelector('button[data-testid="send-button"]');
                        if (!btn) {
                            btn = document.querySelector('button[aria-label*="Send"], button[aria-label*="Gửi"], button[aria-label*="submit"]');
                        }
                        if (!btn) {
                            // Look for button containing only an SVG (send icon)
                            const allBtns = document.querySelectorAll('button');
                            for (const b of allBtns) {
                                const hasSvg = b.querySelector('svg');
                                const text = (b.innerText || '').trim();
                                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                                if (hasSvg && !text && (aria.includes('send') || aria.includes('gửi') || aria.includes('submit'))) {
                                    btn = b;
                                    break;
                                }
                            }
                        }
                        if (!btn) return false;

                        btn.click();
                        return true;
                    }
                """, prompt)
                if sent:
                    stages['send'] = 'js_ok'
                else:
                    # Fallback: Playwright approach
                    editor = page.locator('#prompt-textarea, [data-testid=chat-input], div[contenteditable=true]').first
                    await editor.click()
                    await editor.fill(prompt)
                    await asyncio.sleep(0.3)
                    send_btn = page.locator('button[data-testid="send-button"]').first
                    if await send_btn.count() > 0:
                        await send_btn.evaluate('el => el.click()')
                    else:
                        # Last resort: keyboard Enter
                        await page.keyboard.press('Enter')
                    sent = True
                    stages['send'] = 'playwright_fallback'
            except Exception as exc:
                logger.warning('chatgpt_web chat type/send failed: %s', str(exc)[:120])
                stages['send_error'] = str(exc)[:120]

        if not sent:
            stages['send'] = 'failed'

        # Wait for response with polling
        reply_text = ''
        deadline = time.time() + min(timeout, 90)
        while time.time() < deadline:
            await asyncio.sleep(2)
            try:
                reply_el = page.locator('[data-message-author-role=assistant]').last
                if await reply_el.count() > 0:
                    text = await reply_el.inner_text()
                    if text and text != reply_text:
                        reply_text = text
                        # Keep waiting for more content
            except Exception:
                pass
            # Check for stop button (means still generating)
            try:
                stop_btn = page.locator('button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="Dừng"]').first
                if await stop_btn.count() > 0:
                    continue  # still generating
                if reply_text:
                    break  # done generating
            except Exception:
                pass

        elapsed_ms = int((time.time() - started) * 1000)
        logger.info('chatgpt_web chat result: sent=%s reply_len=%d elapsed=%dms stages=%s',
                    sent, len(reply_text), elapsed_ms, json.dumps(stages))
        return {
            'profile': profile,
            'prompt': prompt,
            'reply': reply_text,
            'elapsed_ms': elapsed_ms,
            'stages': stages,
        }


async def generate_image(
    profile: str = 'chatgpt-default',
    prompt: str = '',
    timeout: int = 120,
    headless: bool = False,
) -> dict[str, Any]:
    return {
        'profile': profile,
        'prompt': prompt,
        'images': [],
        'error': 'chatgpt_web generate_image not implemented',
    }


async def analyze_image(
    profile: str = 'chatgpt-default',
    image: str = '',
    prompt: str = 'Phan tich noi dung anh nay mot cach chi tiet.',
    timeout: int = 120,
    headless: bool = False,
) -> dict[str, Any]:
    """Upload an image to chatgpt.com and ask a question about it.

    Uses JS-based interactions to bypass CloakBrowser stability checks
    (same strategy as the chat() function).

    Args:
        image: either a `data:image/<mime>;base64,<...>` data URL or an
               https URL to an image.
        prompt: text question to ask alongside the image.
        timeout: max seconds to wait for the full response.

    Returns:
        {"text": <ChatGPT's analysis>, "elapsed_ms": int}
    """
    started = time.time()
    stages: dict[str, Any] = {}

    # 1. Resolve image → temp file on disk
    tmp_path, mime = await _resolve_image_to_file(image)
    stages['image_resolved'] = True
    stages['image_mime'] = mime

    try:
        async with pool.page(profile=profile, headless=headless) as page:
            # 2. Navigate to chatgpt.com if not already there
            if 'chatgpt.com' not in (page.url or ''):
                await page.goto(_CHATGPT_HOME, wait_until='domcontentloaded', timeout=30_000)
            await _wait_for_ready(page, timeout=30)
            stages['page_ready'] = True

            # 3. Upload image — use direct file input (most reliable with CloakBrowser).
            #    Avoid clicking the "+" popover menu which triggers CloakBrowser
            #    stability checks on animating elements.
            upload_ok = False
            try:
                file_input = page.locator('input[type="file"]').first
                await file_input.set_input_files(tmp_path)
                upload_ok = True
                stages['upload_method'] = 'direct_input'
                logger.info('chatgpt_web: uploaded image %s (mime=%s) via direct input',
                            tmp_path, mime)
            except Exception as exc:
                logger.warning('chatgpt_web: direct file input failed: %s', str(exc)[:120])
                # Fallback: try clicking + button via JS, then file chooser
                try:
                    await page.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                'button[aria-label*="Thêm"], button[aria-label*="Attach"], '
                                'button[aria-label*="Add file"]'
                            );
                            if (btn) btn.click();
                        }
                    """)
                    await asyncio.sleep(0.5)
                    async with page.expect_file_chooser(timeout=8_000) as fc_info:
                        await page.evaluate("""
                            () => {
                                const el = document.querySelector(
                                    'div[role="menuitem"]:has-text("ảnh"), '
                                    + 'div[role="menuitem"]:has-text("tệp"), '
                                    + 'div[role="menuitem"]:has-text("file"), '
                                    + 'div[role="menuitem"]:has-text("Upload"), '
                                    + 'button:has-text("Tải ảnh"), '
                                    + 'button:has-text("Upload")'
                                );
                                if (el) el.click();
                            }
                        """)
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(tmp_path)
                    upload_ok = True
                    stages['upload_method'] = 'file_chooser_fallback'
                except Exception as exc2:
                    logger.error('chatgpt_web: all upload methods failed: %s', str(exc2)[:120])
                    stages['upload_error'] = str(exc2)[:120]

            # 4. Wait for image to process (upload + thumbnail appears)
            if upload_ok:
                await asyncio.sleep(4.0)
                stages['image_uploaded'] = True

            # 5. Type prompt and send via JS (same as chat() function)
            sent = False
            if upload_ok:
                try:
                    sent = await page.evaluate("""
                        async (promptText) => {
                            const editor = document.querySelector(
                                '#prompt-textarea, [data-testid=chat-input], '
                                + 'div[contenteditable=true]'
                            );
                            if (!editor) return false;

                            editor.focus();
                            if (editor.getAttribute('contenteditable') === 'true' || editor.isContentEditable) {
                                editor.innerText = promptText;
                                editor.dispatchEvent(new InputEvent('input', {
                                    bubbles: true, inputType: 'insertText', data: promptText
                                }));
                            } else {
                                editor.value = promptText;
                                editor.dispatchEvent(new Event('input', {bubbles: true}));
                            }
                            await new Promise(r => setTimeout(r, 500));

                            let btn = document.querySelector('button[data-testid="send-button"]');
                            if (!btn) {
                                btn = document.querySelector(
                                    'button[aria-label*="Send"], button[aria-label*="Gửi"], '
                                    + 'button[aria-label*="submit"]'
                                );
                            }
                            if (!btn) {
                                const allBtns = document.querySelectorAll('button');
                                for (const b of allBtns) {
                                    const hasSvg = b.querySelector('svg');
                                    const text = (b.innerText || '').trim();
                                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                                    if (hasSvg && !text && (
                                        aria.includes('send') || aria.includes('gửi') || aria.includes('submit')
                                    )) {
                                        btn = b;
                                        break;
                                    }
                                }
                            }
                            if (!btn) return false;
                            btn.click();
                            return true;
                        }
                    """, prompt)
                    stages['send'] = 'js_ok' if sent else 'js_no_button'
                except Exception as exc:
                    logger.warning('chatgpt_web vision: JS type/send failed: %s', str(exc)[:120])
                    stages['send_error'] = str(exc)[:120]

            if not sent and upload_ok:
                # Fallback: Playwright approach
                try:
                    editor = page.locator(
                        '#prompt-textarea, [data-testid=chat-input], div[contenteditable=true]'
                    ).first
                    await editor.click()
                    await asyncio.sleep(0.3)
                    await editor.fill(prompt)
                    await asyncio.sleep(0.3)
                    send_btn = page.locator('button[data-testid="send-button"]').first
                    if await send_btn.count() > 0:
                        await send_btn.evaluate('el => el.click()')
                    else:
                        await page.keyboard.press('Enter')
                    sent = True
                    stages['send'] = 'playwright_fallback'
                except Exception as exc2:
                    stages['send_fallback_error'] = str(exc2)[:120]

            if not sent:
                stages['send'] = 'failed'

            # 6. Poll for assistant response
            reply_text = ''
            if sent:
                deadline = time.time() + min(timeout, 120)
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    try:
                        reply_el = page.locator('[data-message-author-role=assistant]').last
                        if await reply_el.count() > 0:
                            text = await reply_el.inner_text()
                            if text and text != reply_text:
                                reply_text = text
                    except Exception:
                        pass
                    try:
                        stop_btn = page.locator(
                            'button[data-testid="stop-button"], '
                            'button[aria-label*="Stop"], '
                            'button[aria-label*="Dừng"]'
                        ).first
                        if await stop_btn.count() > 0:
                            continue
                        if reply_text:
                            break
                    except Exception:
                        pass
            else:
                # Even without send, wait and try to extract
                await asyncio.sleep(10)

            # 7. Fallback extraction if no reply found
            if not reply_text:
                try:
                    body_text = await page.locator('body').inner_text()
                    if 'Unable to' in body_text or 'unable to' in body_text:
                        reply_text = f'[ChatGPT could not process: {body_text[:300]}]'
                    else:
                        reply_text = body_text[-2000:] if len(body_text) > 2000 else body_text
                except Exception:
                    pass

            elapsed_ms = int((time.time() - started) * 1000)
            logger.info(
                'chatgpt_web analyze_image: sent=%s reply_len=%d elapsed=%dms stages=%s',
                sent, len(reply_text), elapsed_ms, json.dumps(stages)
            )
            return {
                'profile': profile,
                'prompt': prompt,
                'text': reply_text,
                'elapsed_ms': elapsed_ms,
                'stages': stages,
            }
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
