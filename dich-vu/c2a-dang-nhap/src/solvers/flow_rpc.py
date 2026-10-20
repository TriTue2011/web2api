# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/flow_rpc.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Ảnh Flow trên flow.google.com (Angular), dùng cookie thay cho OAuth cũ.

Hợp đồng đo 07/09/2026 từ request thật và client wO1vlb:
ogiZ0b = FlowService.BatchGenerateImages, maseQ = FlowService.UploadImage.
Mảng JSON là protobuf: chỉ số Python = số trường protobuf - 1.

Bản 22/09/2026 của Flow bọc grecaptcha.enterprise.execute để gắn action
extension_hijack_detected. Token đúc qua hàm công khai bị từ chối
PUBLIC_ERROR_UNUSUAL_ACTIVITY. Request của trang còn có f.sid, bl, hl.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
import uuid
from urllib.parse import quote, urlsplit

logger = logging.getLogger("src.solvers.flow_rpc")

from .flow_rest import LoiFlowRest, TY_LE_ANH, kiem_model_anh


# Client _.nTa: LANDSCAPE=3, LANDSCAPE_4_3=5, SQUARE=1,
# PORTRAIT_3_4=4, PORTRAIT=2. Không suy enum theo thứ tự dropdown.
ASPECTS = {"16:9": 3, "4:3": 5, "1:1": 1, "3:4": 4, "9:16": 2}
ASPECTS.update({TY_LE_ANH[label]: value for label, value in list(ASPECTS.items())})

READY = """() => location.hostname === 'accounts.google.com' ||
    location.pathname === '/about' ||
    (!!window.grecaptcha?.enterprise?.execute && !!window.WIZ_global_data?.SNlM0e)"""

FLOW_CAPTCHA_INIT = """() => {
    if (window.__c2a_flow) return;
    let pristine = null;
    const save = (fn, enterprise) => {
        if (pristine || typeof fn !== 'function') return;
        try { if (String(fn).includes('extension_hijack_detected')) return; } catch (e) {}
        pristine = fn.bind(enterprise);
    };
    const watchExecute = (enterprise) => {
        if (!enterprise) return;
        save(enterprise.execute, enterprise);
        let current = enterprise.execute;
        try {
            Object.defineProperty(enterprise, 'execute', {
                configurable: true, enumerable: true,
                get() { return current; },
                set(fn) { current = fn; save(fn, enterprise); },
            });
        } catch (e) {}
    };
    const watchEnterprise = (grecaptcha) => {
        if (!grecaptcha) return;
        watchExecute(grecaptcha.enterprise);
        let current = grecaptcha.enterprise;
        try {
            Object.defineProperty(grecaptcha, 'enterprise', {
                configurable: true, enumerable: true,
                get() { return current; },
                set(obj) { current = obj; watchExecute(obj); },
            });
        } catch (e) {}
    };
    let current = window.grecaptcha;
    watchEnterprise(current);
    try {
        Object.defineProperty(window, 'grecaptcha', {
            configurable: true, enumerable: true,
            get() { return current; },
            set(obj) { current = obj; watchEnterprise(obj); },
        });
    } catch (e) {}
    Object.defineProperty(window, '__c2a_flow', {
        value: { get pristine() { return pristine; } },
        configurable: false, enumerable: false,
    });
}"""

POST_RPC = """async ({rpc, body, timeout}) => {
    const wiz = window.WIZ_global_data || {};
    const at = wiz.SNlM0e;
    if (!at) return {status: 401, text: ''};
    const form = new URLSearchParams({at,
        'f.req': JSON.stringify([[[rpc, JSON.stringify(body), null, 'generic']]])});
    const hl = (document.documentElement.lang || navigator.language || 'en').split('-')[0];
    const query = new URLSearchParams({
        rpcids: rpc,
        'source-path': location.pathname || '/',
        bl: wiz.cfb2h || '',
        'f.sid': wiz.FdrFJe || '',
        hl,
        _reqid: String(Math.floor(Math.random() * 900000) + 100000),
        rt: 'c',
    });
        const account = (location.pathname.match(new RegExp('^/u/\\d+')) || [''])[0];
    const response = await fetch(account + '/_/AiSandboxAngularFrontend/data/batchexecute?' + query, {
        method: 'POST', credentials: 'include',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'x-same-domain': '1',
        },
        body: form, signal: AbortSignal.timeout(timeout),
    });
    if (new URL(response.url).hostname !== location.hostname)
        return {status: 401, text: ''};
    return {status: response.status, text: await response.text()};
}"""

FLOW_MINT_JS = """async (action) => {
    const findSitekey = () => {
        const el = document.querySelector('[data-sitekey]');
        if (el) return el.getAttribute('data-sitekey');
        for (const s of document.querySelectorAll('script[src*="recaptcha"]')) {
            const m = s.src.match(/render=([^&]+)/);
            if (m) return decodeURIComponent(m[1]);
        }
        const clients = window.___grecaptcha_cfg?.clients || {};
        for (const client of Object.values(clients)) {
            if (client?.sitekey) return client.sitekey;
            if (client?.K?.K?.sitekey) return client.K.K.sitekey;
        }
        return null;
    };
    const sitekey = findSitekey() || '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';
    if (!window.grecaptcha?.enterprise?.execute && !window.__c2a_flow?.pristine) {
        if (!document.querySelector('script[data-cs-injected]')) {
            const sc = document.createElement('script');
            sc.src = 'https://www.google.com/recaptcha/enterprise.js?render=' + sitekey;
            sc.async = true;
            sc.dataset.csInjected = '1';
            document.head.appendChild(sc);
        }
        for (let i = 0; i < 150; i++) {
            if (window.grecaptcha?.enterprise?.execute || window.__c2a_flow?.pristine) break;
            await new Promise(r => setTimeout(r, 200));
        }
    }
    const pristine = window.__c2a_flow?.pristine;
    const execute = window.grecaptcha?.enterprise?.execute;
    if (typeof pristine !== 'function' && typeof execute !== 'function')
        return {error: 'grecaptcha.enterprise.execute never registered', sitekey};
    if (window.grecaptcha?.enterprise?.ready)
        await new Promise(r => window.grecaptcha.enterprise.ready(r));
    const realAssign = Object.assign;
    let poisoned = false;
    try {
        if (typeof execute === 'function' && String(execute).includes('extension_hijack_detected'))
            poisoned = true;
    } catch (e) {}
    try {
        let token;
        if (typeof pristine === 'function') {
            token = await pristine(sitekey, {action});
        } else if (poisoned) {
            Object.assign = function (target, ...sources) {
                const result = realAssign.call(this, target, ...sources);
                if (result && result.action === 'extension_hijack_detected') result.action = action;
                return result;
            };
            token = await execute(sitekey, {action});
        } else {
            token = await execute(sitekey, {action});
        }
        return token ? {token: String(token), sitekey, pristine: typeof pristine === 'function'} : {error: 'empty token', sitekey};
    } catch (e) {
        return {error: String(e?.message || e), sitekey};
    } finally {
        Object.assign = realAssign;
    }
}"""


def context(project_id: str, recaptcha: str) -> list:
    # clientContext.tool=22 (PINHOLE), projectId=6, recaptchaContext=11.
    return [None, 22, None, None, None, project_id, None, None, None, None, [recaptcha, 1]]


def image_body(project_id: str, prompt: str, model: str, aspect_ratio: str,
               count: int, recaptcha: str, media_ids: list[str]) -> list:
    model = kiem_model_anh(model)
    if aspect_ratio not in ASPECTS:
        raise ValueError(f"Tỷ lệ ảnh Flow không hợp lệ: {aspect_ratio}")
    ctx = context(project_id, recaptcha)
    requests = []
    for _ in range(max(1, min(4, count))):
        # imageInputs=3: name=1, imageInputType=5 (REFERENCE=1).
        requests.append([
            None, None, [[name, None, None, None, 1] for name in media_ids] or None,
            random.randint(0, 2**31 - 1), ASPECTS[aspect_ratio], model, None,
            ctx, [[[prompt]]], None, None, None, str(uuid.uuid4()), str(uuid.uuid4()),
        ])
    return [None, requests, 1, ctx, [str(uuid.uuid4())]]


def upload_body(project_id: str, raw: bytes, recaptcha: str) -> list:
    import io
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format)
    if not mime:
        raise ValueError("Không xác định được định dạng ảnh tham chiếu")
    return [context(project_id, recaptcha), base64.b64encode(raw).decode("ascii"),
            mime, True, None, None, None, False]


def ly_do_loi(chi_tiet: list) -> str:
    """Chuỗi mô tả trong khối chi tiết lỗi batchexecute (lý do ErrorInfo, thông điệp),
    tối đa 300 ký tự. Bỏ chuỗi dài hay liền một khối (giống token, id) — nhật ký không được
    mang bí mật."""
    ra: list[str] = []

    def _di(x) -> None:
        if isinstance(x, str):
            ten_loi = re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", x)        # PUBLIC_ERROR_…
            khoi = re.search(r"[^\s]{30,}", x) and not ten_loi         # token, id, cookie
            if 2 < len(x) <= 160 and (ten_loi or " " in x or "." in x) and not khoi \
                    and x not in ra:
                ra.append(x)
        elif isinstance(x, list):
            for y in x:
                _di(y)
        elif isinstance(x, dict):
            for y in x.values():
                _di(y)

    _di(chi_tiet)
    return "; ".join(ra)[:300]


def rpc_result(text: str, rpc: str) -> list:
    # XSSI + từng khối [độ dài, JSON]; JSON nằm trên một dòng, không cắt theo
    # số ký tự vì độ dài trên dây tính byte UTF-8.
    for line in text.splitlines():
        if not line.startswith("[["):
            continue
        try:
            rows = json.loads(line)
        except ValueError:
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) < 3 or row[1] != rpc:
                continue
            if row[0] == "wrb.fr" and isinstance(row[2], str):
                try:
                    result = json.loads(row[2])
                except ValueError:
                    break
                if isinstance(result, list):
                    return result
            code = row[2] if row[0] == "er" else None
            chi_tiet = ""
            if len(row) > 5 and isinstance(row[5], list) and row[5]:
                code = row[5][0]
                chi_tiet = ly_do_loi(row[5][1:])
            if not isinstance(code, int):
                code = None
            status = {3: 400, 7: 403, 8: 429, 16: 401}.get(code, 502)
            # Giữ LÝ DO Google trả (ErrorInfo reason, thông điệp): 23/09/2026 Flow bị 403 mã 7
            # sáu ngày liền mà không ai biết vì sao — nhật ký chỉ có con số.
            raise LoiFlowRest(status, f"Flow RPC {rpc}: HTTP {status} (mã {code}"
                                      f"{': ' + chi_tiet if chi_tiet else ''})")
    raise LoiFlowRest(502, f"Flow RPC {rpc} không trả kết quả hợp lệ")


async def post_rpc(page, rpc: str, body: list, timeout: float) -> list:
    result = await page.evaluate(POST_RPC, {
        "rpc": rpc, "body": body, "timeout": int(timeout * 1000),
    })
    status = result.get("status", 502)
    if status != 200:
        # Không đưa HTML/CSRF/cookie trong phản hồi vào nhật ký.
        raise LoiFlowRest(status, f"Flow RPC {rpc}: HTTP {status}")
    return rpc_result(result.get("text", ""), rpc)



async def install_captcha_hook(page) -> None:
    """Gắn bẫy execute trước lần tải trang kế. Trang đang mở thì đường đúc token xử lý."""
    context = getattr(page, "context", None)
    add = getattr(context, "add_init_script", None)
    if add is None:
        return
    await add(FLOW_CAPTCHA_INIT)


def _la_trang_du_an(path: str, project_id: str) -> bool:
    """/project/<id> hoặc /u/<n>/project/<id>. n là chỉ số tài khoản trên trình duyệt."""
    return re.fullmatch(rf"(?:/u/\d+)?/project/{re.escape(project_id)}", path.rstrip("/")) is not None


async def open_project(page, project_id: str, profile: str) -> None:
    url = f"https://flow.google.com/project/{quote(project_id, safe='')}"
    await install_captcha_hook(page)
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_function(READY, timeout=30_000)
    if urlsplit(page.url).path == "/about":
        # Trang marketing cũng xuất hiện khi phiên Google còn sống. Bấm CTA
        # một lần để mở ứng dụng; không bấm lặp ở màn mật khẩu/captcha.
        clicked = await page.evaluate("""() => {
            const button = Array.from(document.querySelectorAll('button')).find(
                b => /Tạo bằng Google Flow|Create with Google Flow/.test(b.innerText));
            if (button) button.click();
            return !!button;
        }""")
        if clicked:
            await page.wait_for_url(lambda u: urlsplit(u).path != "/about", timeout=30_000)
            if urlsplit(page.url).hostname == "flow.google.com":
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_function(READY, timeout=30_000)
    current = urlsplit(page.url)
    logger.info("flow open profile=%s path=%s", profile, current.path)
    if current.hostname != "flow.google.com" or not _la_trang_du_an(current.path, project_id):
        raise LoiFlowRest(401, f"Phiên Google Flow của {profile} cần đăng nhập lại Google.")


def image_result(result: list) -> tuple[list[str], list[str]]:
    ids, urls = [], []
    try:
        for media in result[0]:
            name, url = media[0], media[6][0][13]
            if not isinstance(name, str) or not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("missing image")
            ids.append(name)
            urls.append(url)
    except (IndexError, TypeError, ValueError) as exc:
        raise LoiFlowRest(502, "Flow trả kết quả thiếu mã hoặc link ảnh") from exc
    if not urls:
        raise LoiFlowRest(502, "Flow không trả ảnh nào")
    return ids, urls


async def generate_image(*, profile: str, project_id: str, prompt: str, model: str,
                         aspect_ratio: str, count: int, anh_tham_chieu: list[bytes] | None,
                         headless: bool, timeout: float) -> dict:
    from ..browser_pool import pool
    from .flow_google import _get_recaptcha_token

    started = time.monotonic()
    model = kiem_model_anh(model)
    if aspect_ratio not in ASPECTS:
        raise ValueError(f"Tỷ lệ ảnh Flow không hợp lệ: {aspect_ratio}")
    async with pool.page(profile=profile, headless=headless) as page:
        await open_project(page, project_id, profile)
        ids = []
        for raw in anh_tham_chieu or []:
            token, _ = await _get_recaptcha_token(page, action="UPLOAD_IMAGE")
            uploaded = await post_rpc(page, "maseQ", upload_body(project_id, raw, token), timeout)
            try:
                name = uploaded[0][0]
                if not isinstance(name, str) or not name:
                    raise ValueError("missing mediaId")
            except (IndexError, TypeError, ValueError) as exc:
                raise LoiFlowRest(502, "Flow upload không trả mã ảnh tham chiếu") from exc
            ids.append(name)
        # Token tạo ảnh luôn được lấy SAU khi upload hoàn tất.
        token, _ = await _get_recaptcha_token(page, action="IMAGE_GENERATION")
        result = await post_rpc(page, "ogiZ0b", image_body(
            project_id, prompt, model, aspect_ratio, count, token, ids), timeout)
    media_ids, urls = image_result(result)
    return {"media_ids": media_ids, "urls": urls, "model": model, "project_id": project_id,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
