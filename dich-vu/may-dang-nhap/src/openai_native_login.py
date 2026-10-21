# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/openai_native_login.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Đăng nhập ChatGPT bằng TÀI KHOẢN OPENAI GỐC — email + mật khẩu + TOTP.

Khác `chatgpt_login.py` ở chỗ nào
--------------------------------
`chatgpt_login.py` mở `auth.openai.com` rồi đi tìm nút "Continue with Google",
sau đó điền email/mật khẩu vào form của **Google**. Nó chỉ phục vụ tài khoản
Google.

Nhưng phần lớn tài khoản ChatGPT mua theo lô là tài khoản **OpenAI gốc**: email
và mật khẩu do OpenAI giữ, 2FA cũng của OpenAI. Địa chỉ email có thể là
`@gmail.com` hay `@icloud.com` — điều đó không nói lên gì cả, vì nó chỉ là địa
chỉ liên lạc chứ không phải danh tính đăng nhập. Đo thật 08/08/2026: một tài
khoản `@gmail.com` gõ vào ô "Địa chỉ email" của ChatGPT đi thẳng sang trang mật
khẩu của OpenAI rồi tới trang "Kiểm tra ứng dụng xác thực", không chạm Google
lần nào.

Bốn màn hình, tất cả trên miền của OpenAI:

    1. "Đăng nhập hoặc đăng ký"  → ô Địa chỉ email  → Tiếp tục
    2. "Nhập mật khẩu của bạn"   → ô Mật khẩu       → Tiếp tục
    3. "Kiểm tra ứng dụng xác thực của bạn" → mã 6 số → Tiếp tục
    4. về chatgpt.com            → lấy access token

Luồng này ĐƠN GIẢN HƠN đường Google: không nhảy miền, không có bước OAuth
consent, không phải dò nút giữa nhiều tab. Nên nó là module riêng chứ không phải
một nhánh nhồi thêm vào `chatgpt_login.py` — hàm ở đó đã dài 1778 dòng với một
loạt fallback chỉ đúng cho Google.

Về mã TOTP
----------
Mã 6 số chỉ đổi mỗi 30 giây. Sinh lại mã NGAY sau khi bị từ chối sẽ ra đúng con
số vừa bị từ chối, và ba lần thử cháy hết trong hai giây mà không thử được gì
mới. `_ma_moi()` chờ hết cửa sổ hiện tại rồi mới lấy mã kế tiếp.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from .auto_login import LoginSession
from .browser_pool import pool

try:
    import pyotp
    _CO_PYOTP = True
except ImportError:  # pragma: no cover - môi trường thiếu gói
    pyotp = None  # type: ignore
    _CO_PYOTP = False

logger = logging.getLogger(__name__)

_CHATGPT_HOME = "https://chatgpt.com/"
# Đo thật 09/08/2026 trên máy chủ: `https://auth.openai.com/u/login` KHÔNG còn
# là trang nhập email. Nó trả về màn chắn "Phiên của bạn đã kết thúc" — không có
# một thẻ <input> nào, chỉ có link "Đăng nhập" trỏ sang
# `chatgpt.com/auth/login_with`, mà đường đó lại rơi vào thử thách Cloudflare
# (`__cf_chl_rt_tk`) cũng không có ô nhập. Vào thẳng `/log-in` thì ra đúng trang
# "Chào mừng trở lại" với ô `input[name="email"]`.
_AUTH_LOGIN = "https://auth.openai.com/log-in"

# Ô email ở màn hình 1. Auth0 đổi `name`/`id` theo bản dựng nên bắt bằng nhiều
# dấu hiệu, ưu tiên cái cụ thể nhất trước.
_O_EMAIL = (
    'input[name="email"]',
    'input[id="email-input"]',
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[placeholder*="Địa chỉ email"]',
    'input[placeholder*="Email address"]',
)

_O_MAT_KHAU = (
    'input[name="password"]',
    'input[id="password"]',
    'input[type="password"]',
    'input[autocomplete="current-password"]',
)

# Ô mã 6 số ở màn hình 3. KHÔNG bắt `input[type="text"]` trần — trang này có thể
# còn ô email ẩn, điền mã vào đó thì hỏng mà không rõ vì sao.
_O_MA = (
    'input[autocomplete="one-time-code"]',
    'input[name="code"]',
    'input[id="code"]',
    'input[inputmode="numeric"]',
    'input[name="otp"]',
)

# Link "Đăng nhập" trên màn chắn "Phiên của bạn đã kết thúc" — xem `_qua_man_chan`.
_NUT_MAN_CHAN = (
    'a[href*="login_with"]',
    'a:has-text("Đăng nhập")',
    'a:has-text("Log in")',
)

_NUT_TIEP_TUC = (
    'button[type="submit"]',
    'button:has-text("Tiếp tục")',
    'button:has-text("Continue")',
    'button:has-text("Đăng nhập")',
    'button:has-text("Log in")',
)

# Chữ báo mã sai, để phân biệt "mã sai" với "trang chưa kịp chuyển".
_BAO_MA_SAI = ("không chính xác", "incorrect", "invalid", "không hợp lệ",
               "try again", "thử lại")

SO_LAN_THU_MA = 3


@dataclass
class OpenAILoginSession(LoginSession):
    """Kết quả cần lấy là access token của chatgpt.com."""
    access_token: str = ""
    captured_email: str = ""
    access_token_preview: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["access_token_preview"] = self.access_token_preview
        base["captured_email"] = self.captured_email
        base["has_token"] = bool(self.access_token)
        return base


_sessions: dict[str, OpenAILoginSession] = {}
_tasks: dict[str, asyncio.Task] = {}


def get_session(profile: str) -> Optional[OpenAILoginSession]:
    return _sessions.get(profile)


def submit_2fa_code(profile: str, code: str) -> bool:
    """Đường tay cho tài khoản KHÔNG có hạt giống TOTP."""
    session = _sessions.get(profile)
    if not session or session.state != "need_code":
        return False
    session.pending_code = code.strip()
    session.message = "Đã nhận mã, đang điền..."
    return True


def _ma_hien_tai(seed: str) -> str:
    return pyotp.TOTP(seed.replace(" ", "")).now()


async def _ma_moi(seed: str) -> str:
    """Chờ sang cửa sổ 30 giây kế tiếp rồi trả mã MỚI.

    Gọi `.now()` lại ngay sau khi mã bị từ chối chỉ ra đúng con số vừa bị từ
    chối — ba lần thử cháy hết trong hai giây mà chưa hề thử một mã khác.
    """
    con_lai = 30 - int(time.time()) % 30
    await asyncio.sleep(con_lai + 1)
    return _ma_hien_tai(seed)


async def _dien(page, selectors: tuple[str, ...], gia_tri: str,
                cho_giay: float = 20.0) -> bool:
    """Chờ ô xuất hiện rồi điền. True nếu điền được."""
    for sel in selectors:
        try:
            o = page.locator(sel).first
            await o.wait_for(state="visible", timeout=int(cho_giay * 1000))
            await o.click(timeout=3000)
            await o.fill(gia_tri)
            return True
        except Exception:
            continue
    return False


async def _qua_man_chan(page) -> bool:
    """Bấm qua màn chắn "Phiên của bạn đã kết thúc" nếu đang đứng ở đó.

    Đo thật 09/08/2026 trên máy chủ. Chỉ cần đã ghé `chatgpt.com` một lần — mà
    bước dò phiên sẵn có ở đầu luồng thì LUÔN ghé — là trang đăng nhập của
    OpenAI trả về màn chắn này thay vì form. Nó không có lấy một thẻ `<input>`
    nào, nên `_dien` chờ hết 6 selector × 20 giây rồi báo "không tìm thấy ô
    email"; đọc thông báo đó ai cũng tưởng bị Cloudflare chặn, nhưng không phải.

    Đã thử và LOẠI hai cách: đổi sang URL đăng nhập khác (cùng ra màn chắn), và
    xoá cookie trước khi vào (vẫn ra màn chắn). Đường đúng là bấm chính cái link
    "Đăng nhập" mà màn chắn đưa ra — bấm xong trang thành "Chào mừng trở lại"
    với ô email, vẫn ở nguyên URL cũ.
    """
    try:
        if await page.locator(_O_EMAIL[0]).first.count() > 0:
            return False          # đã là form đăng nhập, không có màn chắn nào
    except Exception:
        pass
    for sel in _NUT_MAN_CHAN:
        try:
            nut = page.locator(sel).first
            if await nut.count() > 0 and await nut.is_visible():
                await nut.click(timeout=5000)
                await asyncio.sleep(6.0)
                logger.info("openai_login: qua man chan phien ket thuc")
                return True
        except Exception:
            continue
    return False


async def _bam_tiep_tuc(page) -> bool:
    for sel in _NUT_TIEP_TUC:
        try:
            nut = page.locator(sel).first
            if await nut.count() > 0 and await nut.is_visible():
                await nut.click(timeout=5000)
                return True
        except Exception:
            continue
    # Không thấy nút thì Enter — Auth0 submit form bằng phím Enter được.
    try:
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


async def _co_o_ma(page) -> bool:
    for sel in _O_MA:
        try:
            if await page.locator(sel).first.count() > 0:
                return True
        except Exception:
            continue
    return False


async def _bao_sai(page) -> bool:
    try:
        chu = (await page.inner_text("body")).lower()
    except Exception:
        return False
    return any(h in chu for h in _BAO_MA_SAI)


async def start_openai_login(
    profile: str,
    email: str,
    password: str,
    totp_secret: str = "",
) -> OpenAILoginSession:
    """Chạy nền việc đăng nhập một tài khoản OpenAI gốc."""
    cu = _tasks.pop(profile, None)
    if cu and not cu.done():
        cu.cancel()

    session = OpenAILoginSession(
        profile=profile,
        email=email,
        state="starting",
        message="Khởi tạo Chrome",
        totp_secret=totp_secret,
    )
    _sessions[profile] = session
    _tasks[profile] = asyncio.create_task(_run(session, password))
    return session


async def _run(session: OpenAILoginSession, password: str) -> None:
    # Đánh dấu hồ sơ đang đăng nhập để việc khác (tạo ảnh/video Flow) không đóng
    # trình duyệt giữa chừng — đóng lúc đang mở trang đăng nhập thì `page.goto`
    # chết với net::ERR_ABORTED.
    pool.dau_dang_nhap(session.profile)
    try:
        await _run_inner(session, password)
    except asyncio.CancelledError:
        raise
    except Exception as exc:      # noqa: BLE001 - biên ngoài cùng của tác vụ nền
        session.state = "failed"
        session.error = str(exc)[:300]
        session.message = "Đăng nhập lỗi"
        session.completed_at = time.time()
        logger.exception("openai_login: that bai profile=%s", session.profile)
    finally:
        pool.xong_dang_nhap(session.profile)
    if session.state in ("success", "failed"):
        try:
            await pool.close_profile(session.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau onboard bo qua", exc_info=True)


async def _run_inner(session: OpenAILoginSession, password: str) -> None:
    from .chatgpt_login import _scrape_chatgpt_token

    session.state = "starting"
    session.message = "Đang mở Chrome (headful → noVNC)"
    ctx = await pool.get(profile=session.profile, headless=False, force_recreate=True)
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()
    try:
        await page.bring_to_front()
    except Exception:
        pass

    # ── Hồ sơ đã đăng nhập sẵn? lấy token luôn, khỏi động vào mật khẩu ──
    session.state = "running"
    session.message = "Mở chatgpt.com..."
    await page.goto(_CHATGPT_HOME, wait_until="domcontentloaded", timeout=45_000)
    await asyncio.sleep(3.0)
    token, captured, preview = await _scrape_chatgpt_token(page)
    if token:
        session.access_token = token
        session.captured_email = captured or session.email
        session.access_token_preview = preview or ""
        session.state = "success"
        session.message = "Hồ sơ đã có phiên ChatGPT — không cần đăng nhập lại"
        session.completed_at = time.time()
        return

    # ── Màn hình 1: email ──
    # Bắt đầu từ địa chỉ đăng nhập mà CHÍNH chatgpt.com cấp (NextAuth → auth.openai.com/api/accounts/
    # authorize): đăng nhập xong OpenAI mới trả phiên về chatgpt.com. Đo 30/09/2026 (chụp màn hình ảo):
    # vào thẳng auth.openai.com/log-in thì email + mật khẩu + TOTP đều qua mà chatgpt.com vẫn "Đăng
    # nhập / Đăng ký miễn phí"; mở authorize SAU đó thì OpenAI lại đòi /log-in — phiên kiểu kia vô dụng.
    # Code cũ quét nhầm cookie phiên mã hoá rồi báo thành công (17 tài khoản hotmail).
    session.message = "Mở trang đăng nhập OpenAI..."
    if not await _dua_phien_ve_chatgpt(page):
        await page.goto(_AUTH_LOGIN, wait_until="domcontentloaded", timeout=45_000)
    await asyncio.sleep(2.5)
    await _qua_man_chan(page)

    session.message = "Điền email..."
    if not await _dien(page, _O_EMAIL, session.email):
        raise RuntimeError(
            "Không tìm thấy ô email trên auth.openai.com — trang có thể đang "
            "chặn (Cloudflare) hoặc đã đổi giao diện. Mở noVNC xem màn hình.")
    await _bam_tiep_tuc(page)
    await asyncio.sleep(3.0)

    # ── Màn hình 2: mật khẩu ──
    session.message = "Điền mật khẩu..."
    if not await _dien(page, _O_MAT_KHAU, password):
        raise RuntimeError(
            "Không tới được trang mật khẩu. Email có thể sai, hoặc tài khoản "
            "này đăng nhập bằng Google/Apple chứ không có mật khẩu OpenAI. "
            "Mở noVNC xem màn hình đang dừng ở đâu để biết là cái nào.")
    await _bam_tiep_tuc(page)
    await asyncio.sleep(4.0)

    # ── Màn hình 3: mã xác thực (chỉ khi tài khoản bật 2FA) ──
    if await _co_o_ma(page):
        await _qua_buoc_2fa(session, page)

    # ── Màn hình 4: về chatgpt.com, lấy token ──
    session.message = "Đang lấy token..."
    da_bam_lai = False
    for _ in range(6):
        await asyncio.sleep(5.0)
        logger.info("openai_login: lay token o %s", (page.url or "").split("?")[0])
        loi = await _loi_openai(page)
        if loi:
            # Trang lỗi của chính OpenAI (vd account_deactivated — tài khoản bị xoá/vô hiệu hoá). Đo
            # 30/09/2026: 17 tài khoản hotmail đứng ở trang này; code cũ bỏ qua nó, sang chatgpt.com, quét
            # nhầm cookie phiên rồi báo thành công.
            raise RuntimeError(f"OpenAI báo lỗi error_code={loi[0]}: {loi[1]}")
        token, captured, preview = await _scrape_chatgpt_token(page)
        if token:
            session.access_token = token
            session.captured_email = captured or session.email
            session.access_token_preview = preview or ""
            session.state = "success"
            session.message = f"Lấy token thành công ({session.captured_email})"
            session.completed_at = time.time()
            logger.info("openai_login: xong profile=%s", session.profile)
            return
        if "chatgpt.com" not in (page.url or ""):
            try:
                await page.goto(_CHATGPT_HOME, wait_until="domcontentloaded",
                                timeout=45_000)
            except Exception:
                pass
        elif not da_bam_lai and await _dua_phien_ve_chatgpt(page):
            # Vẫn chưa về chatgpt.com (vd phải đi đường dự phòng /log-in): thử luồng của chatgpt.com
            # một lần — auth.openai.com còn phiên hợp lệ thì tự chuyển về.
            da_bam_lai = True
            session.message = "Đưa phiên OpenAI về chatgpt.com..."

    raise RuntimeError(
        "Đăng nhập xong nhưng không lấy được access token. Mở noVNC xem trang "
        "đang dừng ở đâu — thường là còn một bước xác minh nữa.")


async def _loi_openai(page) -> tuple[str, str] | None:
    """Trang lỗi của auth.openai.com ghi rõ ``error_code: <mã>`` — trả (mã, câu giải thích) nếu có."""
    if "openai.com" not in (page.url or ""):
        return None
    try:
        chu = await page.evaluate("() => (document.body && document.body.innerText) || ''")
    except Exception:
        return None
    import re
    m = re.search(r"error_code:\s*([A-Za-z0-9_.-]+)", str(chu or ""))
    if not m:
        return None
    giai_thich = next((d.strip() for d in str(chu).splitlines() if len(d.strip()) > 30), "")
    return m.group(1), giai_thich[:160]


async def _dua_phien_ve_chatgpt(page) -> bool:
    """chatgpt.com chưa đăng nhập: xin chính chatgpt.com (NextAuth) địa chỉ đăng nhập qua OpenAI rồi mở nó —
    auth.openai.com đã có phiên thì tự chuyển về chatgpt.com kèm phiên. True = đã mở được địa chỉ đó."""
    try:
        kq = await page.evaluate(
            """async () => {
                const pv = await (await fetch('/api/auth/providers', {credentials: 'include'})).json();
                const ten = Object.keys(pv || {});
                const id = ten.includes('openai') ? 'openai' : (ten.find(k => k !== 'google' && k !== 'apple'
                           && k !== 'microsoft-entra-id' && k !== 'email') || ten[0] || 'openai');
                const c = await (await fetch('/api/auth/csrf', {credentials: 'include'})).json();
                const r = await fetch('/api/auth/signin/' + id, {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: new URLSearchParams({csrfToken: c.csrfToken, callbackUrl: 'https://chatgpt.com/',
                                               json: 'true'})});
                let j = {};
                try { j = await r.json(); } catch (e) {}
                return {providers: ten, id: id, status: r.status, url: j.url || ''};
            }""")
    except Exception as exc:
        logger.info("openai_login: xin dia chi dang nhap cua chatgpt.com loi: %s", str(exc)[:200])
        return False
    url = str((kq or {}).get("url") or "")
    logger.info("openai_login: nextauth providers=%s id=%s status=%s url=%s", (kq or {}).get("providers"),
                (kq or {}).get("id"), (kq or {}).get("status"), url.split("?")[0])
    if not url.startswith("https://"):
        return False
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(4.0)
    except Exception:
        return False
    logger.info("openai_login: sau nextauth trang dung o %s", (page.url or "").split("?")[0])
    return True


async def _qua_buoc_2fa(session: OpenAILoginSession, page) -> None:
    """Điền mã 6 số. Có hạt giống thì tự sinh, không thì chờ người nhập."""
    seed = (session.totp_secret or "").strip()

    if not seed or not _CO_PYOTP:
        # Không có hạt giống: dừng ở `need_code` cho giao diện hiện ô nhập.
        session.state = "need_code"
        session.message = ("Tài khoản bật 2FA nhưng chưa có hạt giống TOTP — "
                           "nhập mã 6 số thủ công")
        for _ in range(120):          # chờ tối đa 10 phút
            await asyncio.sleep(5.0)
            if session.pending_code:
                ma = session.pending_code
                session.pending_code = None
                session.state = "running"
                await _dien(page, _O_MA, ma, cho_giay=5.0)
                await _bam_tiep_tuc(page)
                await asyncio.sleep(4.0)
                return
        raise RuntimeError("Hết thời gian chờ mã 2FA nhập tay")

    for lan in range(SO_LAN_THU_MA):
        ma = _ma_hien_tai(seed) if lan == 0 else await _ma_moi(seed)
        session.message = f"Điền mã xác thực (lần {lan + 1})"
        # KHÔNG ghi mã ra log: nó còn sống tới hết cửa sổ 30 giây, và log thường
        # được gửi đi nơi khác.
        logger.info("openai_login: dien ma TOTP lan %d profile=%s",
                    lan + 1, session.profile)
        if not await _dien(page, _O_MA, ma, cho_giay=15.0):
            raise RuntimeError("Không tìm thấy ô nhập mã xác thực")
        await _bam_tiep_tuc(page)
        await asyncio.sleep(5.0)

        # Qua được thì ô mã biến mất. Còn ô mã + báo sai ⇒ thử mã kế tiếp.
        if not await _co_o_ma(page):
            return
        if not await _bao_sai(page):
            # Ô còn đó mà không báo sai: trang chưa kịp chuyển, chờ thêm.
            await asyncio.sleep(5.0)
            if not await _co_o_ma(page):
                return

    raise RuntimeError(
        f"Sai mã TOTP {SO_LAN_THU_MA} lần liên tiếp. Hạt giống có thể không "
        f"phải của tài khoản này, hoặc đồng hồ máy chủ lệch quá 30 giây.")
