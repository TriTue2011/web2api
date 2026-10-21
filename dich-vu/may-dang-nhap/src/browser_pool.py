# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/browser_pool.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Patchright browser pool with per-profile persistent contexts.

Each "profile" is a directory under settings.data_dir/profiles/<name>/ that
Patchright treats as user-data-dir. Cookies, localStorage, IndexedDB and
extension state persist across restarts so a one-time manual Google login
(via the headful VNC flow) keeps working for headless automation later.

Robustness:
  • Detects dead contexts (Chrome killed via VNC, crash, OOM) and re-creates.
  • Tracks the headless mode each context was launched with so a
    /v1/session/manual-login call (headless=False) never re-uses a cached
    headless context — would cause noVNC to show an empty desktop because
    the live Chrome window is in another (headless) display.
  • Removes Chrome's SingletonLock / SingletonSocket / SingletonCookie
    leftover files in the user-data-dir before re-launching, otherwise
    the new Chrome refuses to start ("profile is already in use").
  • Subscribes to context.on("close") so user-driven window closes (clicking
    [X] in VNC) immediately drop the cache instead of waiting for the next
    call to detect it dead.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Any

from patchright.async_api import BrowserContext
try:
    from cloakbrowser import launch_persistent_context_async as _cloak_launch
    _CLOAK_AVAILABLE = True
except ImportError:
    _CLOAK_AVAILABLE = False
    from patchright.async_api import (
        Playwright,
        async_playwright,
    )

from .settings import settings

logger = logging.getLogger(__name__)


_DEFAULT_VIEWPORT = {"width": 1366, "height": 768}

# Chrome UA — used only for Chromium / CloakBrowser.
# The major version is detected from the actual Chrome binary so the UA string
# always matches navigator.userAgentData / sec-ch-ua (Client Hints). A mismatch
# — e.g. UA claims Chrome 130 while the binary (and Client Hints) report 148 —
# is a reCAPTCHA Enterprise bot signal that Google began enforcing more strictly.
def _detect_chrome_major(default: str = "148") -> str:
    import re as _re
    import subprocess as _sp
    for _bin in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        try:
            _out = _sp.run([_bin, "--version"], capture_output=True, text=True, timeout=5).stdout
            _m = _re.search(r"\b(\d+)\.\d+", _out)
            if _m:
                return _m.group(1)
        except Exception:
            continue
    return default


_CHROME_MAJOR = _detect_chrome_major()

# Google Chrome is amd64-only; on arm64 the image ships Debian chromium instead.
#
# Image KHÔNG còn tải bản Chromium riêng của patchright (tiết kiệm 651 MB), nên
# nhánh dự phòng bắt buộc phải trỏ vào trình duyệt CÓ THẬT trong image:
#   amd64 → Google Chrome (channel="chrome")
#   arm64 → chromium của Debian (executable_path=/usr/bin/chromium)
# Trước đây nhánh arm64 trả channel="chromium", tức bản patchright tải về — bỏ
# bản đó mà giữ nguyên dòng cũ là arm64 chết ngay lúc mở trình duyệt.
import os.path as _os_path
import shutil as _shutil


def _chrome_launch_kwargs() -> dict[str, str]:
    if _shutil.which("google-chrome") or _shutil.which("google-chrome-stable"):
        return {"channel": "chrome"}
    for _duong in ("/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if _os_path.exists(_duong):
            return {"executable_path": _duong}
    # Không có trình duyệt hệ thống (máy dev): để patchright tự tìm bản của nó.
    return {"channel": "chromium"}


_CHROME_LAUNCH_KWARGS = _chrome_launch_kwargs()

_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CHROME_MAJOR}.0.0.0 Safari/537.36"
)

# Firefox UA — used when settings.browser == "firefox" so Google serves
# the Firefox-compatible page variant, not a Chrome-optimized one.
_FIREFOX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) "
    "Gecko/20100101 Firefox/138.0"
)


def _get_user_agent() -> str:
    """Return user-agent appropriate for the configured browser engine."""
    return _FIREFOX_USER_AGENT if settings.browser.lower() == "firefox" else _CHROME_USER_AGENT


# Injected into every Chromium page before any other script runs.
# Firefox uses a shorter variant — setting `window.chrome` on Firefox
# is a red flag because real Firefox doesn't have it.
_STEALTH_SCRIPT_CHROMIUM = """
// 1. navigator.webdriver — dead giveaway
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// 2. chrome.runtime — real Chrome always has this
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {} };

// 3. plugins — headless Chrome reports empty array
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [1, 2, 3, 4, 5];
        arr.item = i => undefined;
        arr.namedItem = n => undefined;
        arr.refresh = () => {};
        return arr;
    }
});

// 4. languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['vi-VN', 'vi', 'en-US', 'en']
});

// 5. permissions — headless Chrome often denies notifications
const _origQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (params) => (
    params.name === 'notifications'
        ? Promise.resolve({ state: 'prompt', onchange: null })
        : _origQuery(params)
);

// 6. hardwareConcurrency — real machines have >1 cores
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });

// 7. deviceMemory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// 8. platform — hide Linux from VPS
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
"""

# Firefox stealth script — does NOT set window.chrome (Firefox doesn't
# have it; setting it would be a bot signal). Keeps the other critical
# overrides that Firefox's engine supports.
_STEALTH_SCRIPT_FIREFOX = """
// 1. navigator.webdriver — dead giveaway
Object.defineProperty(navigator, 'webdriver', { get: () => false });

// 2. languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['vi-VN', 'vi', 'en-US', 'en']
});

// 3. permissions — avoid "Notifications blocked" bot signal
const _origQueryFF = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (params) => (
    params.name === 'notifications'
        ? Promise.resolve({ state: 'prompt', onchange: null })
        : _origQueryFF(params)
);

// 4. hardwareConcurrency — real machines have >1 cores
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });

// 5. deviceMemory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// 6. platform — hide Linux from VPS
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// 7. plugins — FirefoxPluginArray is native but we spoof length >0
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [1, 2, 3, 4, 5];
        arr.item = i => undefined;
        arr.namedItem = n => undefined;
        arr.refresh = () => {};
        return arr;
    }
});
"""


def _select_stealth_script() -> str:
    """Return the stealth init script appropriate for the configured browser."""
    return _STEALTH_SCRIPT_FIREFOX if settings.browser.lower() == "firefox" else _STEALTH_SCRIPT_CHROMIUM
# Chrome single-instance lock files that linger after a crash and block
# the next launch with "Profile is already in use".
# Tên profile hợp lệ: chỉ chữ/số/gạch. Không dấu chấm, không gạch chéo → không
# có "..", không có đường dẫn tuyệt đối.
_PROFILE_SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}")

_CHROME_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
# Firefox profile lock files that persist after crash/kill
_FIREFOX_LOCK_FILES = ("lock", ".parentlock", "parent.lock")


class HoSoDangBan(RuntimeError):
    """Hồ sơ đang được một việc khác dùng, chờ quá hạn cho phép.

    Khác một lỗi thật: trình duyệt và phiên đăng nhập đều lành lặn, chỉ là tới
    lượt chưa. Nơi bắt được ngoại lệ này phải nói đúng như vậy thay vì báo hỏng
    — báo hỏng thì tầng khôi phục phía trên lại đi đăng nhập lại một tài khoản
    không có vấn đề gì.
    """

    def __init__(self, profile: str, cho_toi_da: float):
        super().__init__(
            f"Hồ sơ {profile} đang bận (một việc khác đang dùng trình duyệt) — "
            f"đã chờ {cho_toi_da:.0f}s")
        self.profile = profile
        self.cho_toi_da = cho_toi_da


@dataclass
class _PoolEntry:
    ctx: BrowserContext
    page: Any
    headless: bool
    last_used: float = 0.0


class BrowserPool:
    """Holds one Playwright runtime and lazily creates persistent contexts
    on demand. Contexts are reused for the lifetime of the process and only
    closed on shutdown so noVNC clients can keep seeing the same browser
    window after a login completes."""

    def __init__(self) -> None:
        self._playwright = None
        self._contexts: dict[str, _PoolEntry] = {}
        self._manual_profiles: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._evict_task: asyncio.Task | None = None
        # Hồ sơ đang có một luồng ĐĂNG NHẬP chạy dở.
        #
        # Vì sao cần: các luồng đăng nhập lấy context bằng `get()` rồi thao tác
        # BÊN NGOÀI khoá hồ sơ (khác `page()` — cái đó giữ khoá suốt lượt). Nên
        # `close_profile()` của một việc khác vẫn đóng được trình duyệt ngay giữa
        # lúc đang đăng nhập, và lần `page.goto` đang chạy chết với
        # `net::ERR_ABORTED`. Đo thật 31/07: tạo video Flow trên hồ sơ
        # google-nguoi-a đóng trình duyệt đúng lúc phục hồi phiên Claude đang
        # mở claude.ai ⇒ báo "đăng nhập lại Google cũng không xong" tuy tài khoản
        # không hỏng.
        #
        # TÁI PHÁT 10/08/2026, cùng hồ sơ, vì cờ này chỉ được truyền ở 8 lời gọi
        # trong `main.py` — còn các bước DỌN DẸP cuối mỗi lượt onboard/đăng nhập
        # thì không. Tầng T2 (onboard ChatGPT) kết thúc lúc 05:48:57 và đóng hồ
        # sơ đúng 2 giây sau khi tầng T3 vừa nhận context, giết `page.goto` giữa
        # chừng: "Target page, context or browser has been closed".
        #
        # LUẬT: mọi lời gọi `close_profile` mang nghĩa "xong việc của TÔI, đừng
        # để trình duyệt nằm lại" phải truyền `bo_qua_khi_dang_nhap=True`. Nhả
        # muộn thì chỉ tốn RAM tới lượt quét nhàn rỗi; đóng nhầm thì giết lượt
        # đăng nhập của việc khác.
        self._dang_dang_nhap: set[str] = set()

    def is_manual(self, profile: str) -> bool:
        return profile in self._manual_profiles

    def dau_dang_nhap(self, profile: str) -> None:
        """Đánh dấu hồ sơ đang đăng nhập — việc khác đừng đóng trình duyệt."""
        self._dang_dang_nhap.add(profile)

    def xong_dang_nhap(self, profile: str) -> None:
        self._dang_dang_nhap.discard(profile)

    def dang_dang_nhap(self, profile: str) -> bool:
        return profile in self._dang_dang_nhap

    async def start(self) -> None:
        browser = settings.browser.lower()
        if _CLOAK_AVAILABLE and browser != "firefox":
            # CloakBrowser is Chromium-only — skip if Firefox is preferred
            if self._evict_task is None:
                self._evict_task = asyncio.create_task(self._eviction_loop())
            logger.info("cloakbrowser ready (stealth mode, Cloudflare bypass enabled)")
            return
        if self._playwright is None:
            from patchright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._evict_task = asyncio.create_task(self._eviction_loop())
            logger.info("patchright started (engine=%s)", browser)

    async def stop(self) -> None:
        if self._evict_task:
            self._evict_task.cancel()
        for name, entry in list(self._contexts.items()):
            try:
                await entry.ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        self._manual_profiles.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _eviction_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                to_evict = []
                async with self._global_lock:
                    for profile, entry in list(self._contexts.items()):
                        # ĐANG ĐĂNG NHẬP thì không đụng vào, dù `last_used` đã cũ.
                        # `auto_login` gọi `pool.get()` ĐÚNG MỘT LẦN rồi lái trang
                        # suốt 420 giây mà không chạm lại pool, nên `last_used`
                        # đứng im từ giây đầu và tới phút thứ 5 vòng này đóng ngay
                        # trình duyệt của lượt đăng nhập ĐANG chạy.
                        #
                        # Đo thật 24/08/2026 (google-nguoi-b): mở context
                        # 15:26:39 → "auto-evicting idle" 15:31:51 → hai phút cuối
                        # lái một trang đã đóng, mọi thao tác ném lỗi rồi bị nuốt,
                        # kết luận "Không lọt được ô mật khẩu". Và người được mời
                        # ra noVNC gõ captcha thì không còn trình duyệt nào để gõ.
                        #
                        # Đây đúng cái bẫy `close_profile` đã có cờ
                        # `bo_qua_khi_dang_nhap` để tránh — vòng quét này thì chưa.
                        if self.dang_dang_nhap(profile) or self.is_manual(profile):
                            continue
                        # Fallback cleanup for automation that did not close
                        # its context explicitly. HTTP warmup holds no tabs.
                        if now - entry.last_used > 300:
                            lock = self._locks.get(profile)
                            if lock and not lock.locked():
                                to_evict.append(profile)
                for profile in to_evict:
                    lock = await self._lock_for(profile)
                    if not lock.locked():
                        async with lock:
                            entry = self._contexts.get(profile)
                            # Kiểm lại cờ: lượt đăng nhập có thể vừa bắt đầu trong
                            # lúc đang xếp hàng chờ khoá.
                            if (entry and now - entry.last_used > 300
                                    and not self.dang_dang_nhap(profile) and not self.is_manual(profile)):
                                logger.info("auto-evicting idle profile=%s", profile)
                                await self._evict(profile)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("eviction loop error: %s", e)

    def _profile_dir(self, profile: str) -> Path:
        # Tên profile đến từ request. Ghép thẳng vào đường dẫn thì "../../etc"
        # hay một đường dẫn tuyệt đối sẽ tạo thư mục profile trình duyệt (và ghi
        # dữ liệu vào đó) ở BẤT KỲ đâu trên đĩa. Chỉ nhận slug, rồi kiểm lại sau
        # khi resolve — kiểm chuỗi thôi là chưa đủ khi có symlink giữa đường.
        ten = str(profile or "").strip()
        if not _PROFILE_SLUG.fullmatch(ten):
            raise ValueError(
                f"Tên profile không hợp lệ: {ten!r}. Chỉ chấp nhận chữ, số, '-' và '_' (1-64 ký tự)."
            )
        goc = (settings.data_dir / "profiles").resolve()
        path = (goc / ten).resolve()
        try:
            path.relative_to(goc)
        except ValueError:
            raise ValueError(f"Tên profile thoát ra ngoài thư mục profiles: {ten!r}") from None
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _clear_singleton_locks(self, profile: str) -> None:
        """Remove lock files left by a previous crash (Chrome + Firefox)."""
        root = self._profile_dir(profile)
        for name in _CHROME_LOCK_FILES + _FIREFOX_LOCK_FILES:
            try:
                p = root / name
                if p.exists() or p.is_symlink():
                    p.unlink()
                    logger.info("cleared stale lock profile=%s file=%s", profile, name)
            except Exception as exc:
                logger.debug("could not unlink %s: %s", name, exc)

    def _clear_crash_flag(self, profile: str) -> None:
        """Patch Default/Preferences to set exit_type=Normal so Chrome/Chromium
        does NOT show the 'Restore pages?' dialog on next launch.
        This is the most reliable way — the --disable-session-crashed-bubble flag
        only works on real Chrome, not on Chromium-based builds like CloakBrowser.
        """
        import json as _json
        prefs_file = self._profile_dir(profile) / "Default" / "Preferences"
        if not prefs_file.exists():
            return
        try:
            data = _json.loads(prefs_file.read_text(encoding="utf-8"))
            profile_data = data.get("profile", {})
            changed = False
            if profile_data.get("exit_type") not in ("Normal", "SessionEnded"):
                profile_data["exit_type"] = "Normal"
                profile_data["exited_cleanly"] = True
                changed = True
            # Tắt trình quản lý mật khẩu + cảnh báo rò rỉ mật khẩu (popup "Thay đổi
            # mật khẩu của bạn" chặn auto-login). password_manager_leak_detection=
            # false → Chrome không kiểm tra/nhắc mật khẩu bị lộ.
            if profile_data.get("password_manager_leak_detection") is not False:
                profile_data["password_manager_leak_detection"] = False
                changed = True
            cred = data.get("credentials_enable_service")
            if cred is not False:
                data["credentials_enable_service"] = False
                changed = True
            pm = data.get("profile_password_manager_enabled")
            if pm is not False:
                data["profile_password_manager_enabled"] = False
                changed = True
            if changed:
                data["profile"] = profile_data
                prefs_file.write_text(_json.dumps(data), encoding="utf-8")
                logger.info("patched prefs (crash+password) profile=%s", profile)
        except Exception as exc:
            logger.debug("could not patch prefs %s: %s", profile, exc)

    async def _lock_for(self, profile: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(profile)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[profile] = lock
            return lock

    async def _is_alive(self, ctx: BrowserContext) -> bool:
        """Quick liveness probe — cookies() round-trips to the Chrome target,
        so a dead/disconnected context throws immediately."""
        try:
            await asyncio.wait_for(ctx.cookies(), timeout=2.5)
            return True
        except Exception as exc:
            logger.info("context liveness probe failed: %s", exc)
            return False

    def _attach_close_handler(self, profile: str, ctx: BrowserContext) -> None:
        """Drop cache entry the moment Chrome (or the user clicking [X] in
        VNC) closes the context. Saves us a liveness round-trip later."""
        def _on_close():
            entry = self._contexts.get(profile)
            if entry is not None and entry.ctx is ctx:
                self._contexts.pop(profile, None)
                self._manual_profiles.discard(profile)
                logger.info("context closed (auto-drop) profile=%s", profile)
        try:
            ctx.on("close", _on_close)
        except Exception:
            pass

    async def _open_context(self, profile: str, headless: bool) -> tuple[BrowserContext, Any]:
        user_data_dir = self._profile_dir(profile)
        browser = settings.browser.lower()
        self._clear_singleton_locks(profile)
        if browser != "firefox":
            self._clear_crash_flag(profile)  # Chromium/Chrome-specific
        env = None
        if not headless:
            env = {"DISPLAY": settings.display}

        # Optional residential / mobile proxy (datacenter IPs often blocked by CF)
        # (Cloudflare managed challenge hard-blocks datacenter ranges).
        # Env: CAPTCHA_SOLVER_BROWSER_PROXY=http://user:pass@host:port
        #   or socks5://user:pass@host:port
        import os as _os
        proxy_url = (
            _os.environ.get("CAPTCHA_SOLVER_BROWSER_PROXY")
            or _os.environ.get("BROWSER_PROXY")
            or ""
        ).strip()
        proxy_cfg = None
        if proxy_url:
            proxy_cfg = {"server": proxy_url}
            logger.info("browser proxy enabled for profile=%s server=%s", profile, proxy_url.split("@")[-1])
        
        browser = settings.browser.lower()
        if _CLOAK_AVAILABLE and browser != "firefox":
            # CloakBrowser: source-level Chromium patches, passes Cloudflare Turnstile automatically.
            # No add_init_script needed — CloakBrowser has 49 source-level C++ patches.
            # backend='patchright' is NOT used because it breaks init scripts and isn't needed.
            if env:
                for k, v in env.items():
                    _os.environ[k] = v
            launch_kwargs: dict[str, Any] = dict(
                user_data_dir=str(user_data_dir),
                headless=headless,
                viewport=_DEFAULT_VIEWPORT,
                locale="vi-VN",
                timezone="Asia/Ho_Chi_Minh",
                user_agent=_get_user_agent(),
                humanize=True,
                human_preset='careful',
                args=[
                    "--no-first-run",
                    # GHIM kho mật khẩu, đừng để Chromium tự dò.
                    #
                    # Trên Linux, Chromium mã hoá cookie và mật khẩu bằng khoá
                    # lấy từ Gnome Keyring / KWallet, và TỰ DÒ backend theo môi
                    # trường desktop ở MỖI lần khởi động. Container này không có
                    # keyring lẫn dbus (đo 29/08/2026: không `gnome-keyring-daemon`,
                    # không `dbus-launch`, `DBUS_SESSION_BUS_ADDRESS` rỗng), nên
                    # nó đang rơi về `basic` — khoá cố định, chạy được.
                    #
                    # Vấn đề nằm ở chữ "tự dò": ngày nào image nền có thêm dbus
                    # hoặc Secret Service, backend đổi, và MỌI cookie đã mã hoá
                    # theo khoá cũ thành không giải mã được. Hậu quả là mất sạch
                    # phiên của tất cả tài khoản cùng một lúc, không một thông
                    # báo lỗi nào — chỉ thấy đồng loạt đòi đăng nhập lại.
                    #
                    # Ghim `basic` để kết quả không phụ thuộc môi trường. Không
                    # hạ thấp bảo mật gì thêm: hiện đã là `basic` rồi.
                    "--password-store=basic",
                    "--disable-session-crashed-bubble",
                    "--disable-infobars",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    # Tắt cảnh báo "mật khẩu bị lộ" (Password Leak Detection) + lời
                    # nhắc lưu/đổi mật khẩu — nó bật popup chặn luồng auto-login.
                    "--disable-features=TranslateUI,BlinkGenPropertyTrees,PasswordLeakDetection,PasswordLeakToggleMove,AutofillEnableAccountWalletStorage",
                    "--disable-dev-shm-usage",
                    "--disable-popup-blocking",
                    "--disk-cache-size=1048576",
                    "--media-cache-size=1048576",
                ],
            )
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg
            try:
                context = await _cloak_launch(**launch_kwargs)
            except TypeError:
                # Older cloakbrowser without proxy kw — fall through without proxy.
                if proxy_cfg:
                    logger.warning("cloakbrowser does not accept proxy=; launching without proxy")
                    launch_kwargs.pop("proxy", None)
                context = await _cloak_launch(**launch_kwargs)
        elif browser == "firefox":
            # Firefox: no Google Safe Browsing → bypasses "unsafe browser" error
            # Google trusts Firefox sign-in more than automated Chromium on VPS IPs.
            assert self._playwright is not None
            context = await self._playwright.firefox.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                viewport=_DEFAULT_VIEWPORT,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                user_agent=_get_user_agent(),
                env=env,
                ignore_default_args=["--enable-automation"],
            )
            await context.add_init_script(_select_stealth_script())
        else:
            # Patchright Chromium with stealth patches
            assert self._playwright is not None
            context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                no_viewport=False,
                viewport=_DEFAULT_VIEWPORT,
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                user_agent=_get_user_agent(),
                env=env,
                args=[
                    "--no-first-run",
                    # GHIM kho mật khẩu, đừng để Chromium tự dò.
                    #
                    # Trên Linux, Chromium mã hoá cookie và mật khẩu bằng khoá
                    # lấy từ Gnome Keyring / KWallet, và TỰ DÒ backend theo môi
                    # trường desktop ở MỖI lần khởi động. Container này không có
                    # keyring lẫn dbus (đo 29/08/2026: không `gnome-keyring-daemon`,
                    # không `dbus-launch`, `DBUS_SESSION_BUS_ADDRESS` rỗng), nên
                    # nó đang rơi về `basic` — khoá cố định, chạy được.
                    #
                    # Vấn đề nằm ở chữ "tự dò": ngày nào image nền có thêm dbus
                    # hoặc Secret Service, backend đổi, và MỌI cookie đã mã hoá
                    # theo khoá cũ thành không giải mã được. Hậu quả là mất sạch
                    # phiên của tất cả tài khoản cùng một lúc, không một thông
                    # báo lỗi nào — chỉ thấy đồng loạt đòi đăng nhập lại.
                    #
                    # Ghim `basic` để kết quả không phụ thuộc môi trường. Không
                    # hạ thấp bảo mật gì thêm: hiện đã là `basic` rồi.
                    "--password-store=basic",
                    "--disable-session-crashed-bubble",
                    "--disable-infobars",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-popup-blocking",
                    "--disk-cache-size=1048576",
                    "--media-cache-size=1048576",
                ],
                ignore_default_args=["--enable-automation"],
                **_CHROME_LAUNCH_KWARGS,
            )
            await context.add_init_script(_select_stealth_script())

        self._attach_close_handler(profile, context)
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        if browser == "firefox":
            mode = "patchright-firefox"
        elif _CLOAK_AVAILABLE:
            mode = "cloakbrowser"
        else:
            mode = "patchright-chromium (via real Chrome)"
        logger.info("opened context profile=%s headless=%s engine=%s", profile, headless, mode)
        return context, page

    async def _evict(self, profile: str) -> None:
        """Drop + close a cached context (called when stale or mode-mismatched)."""
        entry = self._contexts.pop(profile, None)
        if entry is not None:
            try:
                await entry.ctx.close()
            except Exception:
                pass

    async def get(
        self,
        profile: str = "default",
        headless: bool = True,
        force_recreate: bool = False,
        cho_toi_da: float | None = None,
        manual: bool = False,
    ) -> BrowserContext:
        """Return a context for the given profile, creating one if needed.

        Contexts are NOT closed after a call so a manual-login session
        (headless=False) keeps the browser window alive for the user.

        If `force_recreate=True`, any cached context is closed and a fresh
        one is launched — useful when the UI offers a "restart browser"
        button or when the headless/headful mode changes.

        `cho_toi_da`: số giây tối đa chờ tới lượt dùng hồ sơ; hết giờ thì ném
        `HoSoDangBan`. Mặc định None = chờ vô hạn, giữ nguyên hành vi cũ cho mọi
        nơi gọi sẵn có.

        VÌ SAO CẦN: mỗi hồ sơ chỉ một việc dùng trình duyệt tại một thời điểm.
        Đo thật 09/08/2026: một lượt khôi phục ChatGPT giữ hồ sơ
        `google-nguoi-a` (bên gọi đã hết hạn chờ 180s và bỏ cuộc,
        nhưng tác vụ phía máy chủ vẫn chạy), rồi người dùng bấm "Chỉ đăng nhập"
        trên đúng hồ sơ đó — lượt đăng nhập nằm im trong `get()` 366 giây, giao
        diện vẫn hiện "Đang đăng nhập…". Không phân biệt được với đang chạy, mà
        noVNC lại chiếu cửa sổ ChatGPT của việc kia nên càng khó hiểu.
        """
        await self.start()
        lock = await self._lock_for(profile)
        if cho_toi_da is None:
            await lock.acquire()
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=cho_toi_da)
            except asyncio.TimeoutError:
                logger.info("get: hồ sơ %s bận quá %.0fs — bỏ lượt", profile, cho_toi_da)
                raise HoSoDangBan(profile, cho_toi_da) from None
        try:
            if (self.is_manual(profile) and not manual) or (manual and self.dang_dang_nhap(profile)):
                raise HoSoDangBan(profile, 0)
            entry = self._contexts.get(profile)
            if entry is not None and not force_recreate:
                # Reuse only when the mode matches AND the context is still alive.
                if entry.headless == headless and await self._is_alive(entry.ctx):
                    entry.last_used = time.time()
                    if manual:
                        self._manual_profiles.add(profile)
                    return entry.ctx
                logger.info(
                    "evicting stale context profile=%s reason=%s",
                    profile,
                    "mode_mismatch" if entry.headless != headless else "dead",
                )
                await self._evict(profile)
            elif force_recreate and entry is not None:
                logger.info("force-recreate profile=%s", profile)
                await self._evict(profile)

            ctx, page = await self._open_context(profile, headless=headless)
            self._contexts[profile] = _PoolEntry(ctx=ctx, page=page, headless=headless, last_used=time.time())
            if manual:
                self._manual_profiles.add(profile)
            return ctx
        finally:
            lock.release()

    async def read_cookies(self, profile: str, url: str) -> list[dict]:
        """Read saved cookies without retaining a new browser or navigating.

        Existing windows (including manual login) stay open. A cold reader owns
        its temporary context under the profile lock and always closes it.
        """
        if not _PROFILE_SLUG.fullmatch(profile):
            raise ValueError("Tên hồ sơ không hợp lệ")
        directory = settings.data_dir / "profiles" / profile
        if directory.is_symlink() or not directory.is_dir():
            return []
        await self.start()
        lock = await self._lock_for(profile)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=5)
        except asyncio.TimeoutError:
            raise HoSoDangBan(profile, 5) from None
        try:
            entry = self._contexts.get(profile)
            if entry is not None:
                return await entry.ctx.cookies(url)
            ctx, _ = await self._open_context(profile, headless=True)
            try:
                return await ctx.cookies(url)
            finally:
                await ctx.close()
        finally:
            lock.release()

    async def close_profile(self, profile: str, *, bo_qua_khi_dang_nhap: bool = False,
                            cho_toi_da: float | None = None, user_requested: bool = False) -> bool:
        """Đóng trình duyệt của hồ sơ.

        `bo_qua_khi_dang_nhap=True`: KHÔNG đóng nếu hồ sơ đang có luồng đăng nhập
        chạy dở. Dùng cho các việc chỉ muốn nhả RAM (tạo ảnh/video Flow) — nhả
        muộn 5 phút theo hạn nhàn rỗi thì chỉ tốn RAM, còn đóng sai lúc thì làm
        chết cả lượt đăng nhập của việc khác.

        `cho_toi_da`: giống `get()` — số giây tối đa chờ tới lượt, hết giờ thì ném
        `HoSoDangBan`. Mặc định None = chờ vô hạn, giữ nguyên hành vi cũ.

        VÌ SAO CẦN: hàm này chờ khoá hồ sơ y như `get()`, nên nó cũng treo y như
        `get()` — chỉ khác là nó bị gọi ở chỗ KHÔNG ai đặt hạn. Đo thật 09/08/2026
        (nguoi.a@example.com): `start_auto_login` gọi nó trong lúc một lượt onboard
        ChatGPT đang giữ hồ sơ, handler HTTP `/v1/session/auto-login-saved` nằm im
        và KHÔNG BAO GIỜ trả lời — access log của solver không có nổi một dòng cho
        yêu cầu đó. Bên gọi hết hạn 30 giây rồi báo "Google chặn", trong khi trình
        duyệt còn chưa mở.
        """
        if self.is_manual(profile) and not user_requested:
            return False
        if bo_qua_khi_dang_nhap and self.dang_dang_nhap(profile):
            logger.info("close_profile BỎ QUA profile=%s — đang có luồng đăng nhập", profile)
            return False
        lock = await self._lock_for(profile)
        if cho_toi_da is None:
            await lock.acquire()
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=cho_toi_da)
            except asyncio.TimeoutError:
                logger.info("close_profile: hồ sơ %s bận quá %.0fs — bỏ lượt đóng",
                            profile, cho_toi_da)
                raise HoSoDangBan(profile, cho_toi_da) from None
        try:
            # Recheck after waiting: the user may have opened this workspace.
            if self.is_manual(profile) and not user_requested:
                return False
            if bo_qua_khi_dang_nhap and self.dang_dang_nhap(profile):
                return False
            self._manual_profiles.discard(profile)
            entry = self._contexts.pop(profile, None)
            if entry is None:
                return False
            try:
                await entry.ctx.close()
            except Exception:
                pass
            return True
        finally:
            lock.release()

    def is_loaded(self, profile: str) -> bool:
        return profile in self._contexts

    def get_cached(self, profile: str) -> BrowserContext | None:
        entry = self._contexts.get(profile)
        return entry.ctx if entry else None

    @asynccontextmanager
    async def page(self, profile: str = "default", headless: bool = True) -> AsyncIterator:
        await self.start()
        lock = await self._lock_for(profile)
        # Fast Failover: if the profile is already busy, refuse immediately
        # with HTTP 429 so the upstream chatgpt2api router rotates to the
        # next account (web_proxy.AccountBusyError → next profile in loop).
        # Queuing on a single account just stacks latency.
        #
        # ĐANG ĐĂNG NHẬP cũng là BẬN, dù khoá hồ sơ đang rảnh: `auto_login` gọi
        # `get()` một lần rồi NHẢ KHOÁ, và giữ trình duyệt suốt 420 giây. Chỗ này
        # nhìn thấy khoá rảnh nên vô tư mở chồng lên — khác chế độ headless thì
        # đóng luôn trình duyệt đăng nhập dở dang, cùng chế độ thì hai Chrome
        # chung một user-data-dir.
        #
        # Đo thật 24/08/2026 (google-nguoi-b): lượt đăng nhập headful chạy từ
        # 15:56:40 (đang ở màn thử thách reCAPTCHA, chờ người gõ trên noVNC);
        # 16:00:01 một cú bấm "Tái dùng" trên giao diện — tức
        # get-or-create-project, headless — mở tiếp trên đúng hồ sơ đó; lượt đăng
        # nhập kết thúc `state=failed` lúc 16:03:48.
        #
        # 429 là câu trả lời ĐÚNG chứ không phải lỗi: `_flow_session_trang_thai`
        # đọc 429 thành 'ban' (bận) chứ không phải 'mất phiên', nên không kéo
        # theo một lượt khôi phục oan.
        if lock.locked() or self.dang_dang_nhap(profile) or self.is_manual(profile):
            from fastapi import HTTPException
            ly_do = "đang có luồng đăng nhập" if self.dang_dang_nhap(profile) else "already busy"
            logger.info("fast-failover profile=%s %s → 429", profile, ly_do)
            raise HTTPException(status_code=429, detail="Account Busy")
        await lock.acquire()
        try:
            if self.dang_dang_nhap(profile) or self.is_manual(profile):
                from fastapi import HTTPException
                raise HTTPException(status_code=429, detail="Account Busy")
            entry = self._contexts.get(profile)
            if entry is not None:
                if entry.headless != headless or not await self._is_alive(entry.ctx):
                    await self._evict(profile)
                    entry = None
            if entry is None:
                ctx, p = await self._open_context(profile, headless=headless)
                _attach_model_tracker(p, profile)
                entry = _PoolEntry(ctx=ctx, page=p, headless=headless, last_used=time.time())
                self._contexts[profile] = entry
                
            entry.last_used = time.time()
            try:
                yield entry.page
            finally:
                entry.last_used = time.time()
        finally:
            lock.release()


def _attach_model_tracker(page, profile: str) -> None:
    """Subscribe to network responses on `page` and pipe the bodies of
    Gemini Web / ChatGPT Web RPC frames through the passive model
    tracker. New model names ("Nano Banana 3", "Lyria 2", ...) get
    learned the first time the user's account actually uses them, so
    the /v1/models catalogue keeps up with upstream renames without
    code edits.

    Best-effort: any failure inside the handler is swallowed so a
    misbehaving response can't break the chat / image / music flows
    we're sharing the page with.
    """
    try:
        from .solvers.model_tracker import extract_gemini_models, record
    except Exception:
        return

    async def _handler(response):
        try:
            url = response.url
            if "BardChatUi" in url or "/_/BardChatUi" in url or "gemini.google.com" in url:
                provider = "gemini_web"
            else:
                return
            # Only inspect text-like content — image binaries are noise.
            ct = (response.headers.get("content-type") or "").lower()
            if "json" not in ct and "javascript" not in ct and "text" not in ct:
                return
            try:
                body = await response.text()
            except Exception:
                return
            for name in extract_gemini_models(body):
                record(provider, profile, name)
        except Exception:
            pass

    try:
        page.on("response", _handler)
    except Exception as exc:
        logger.debug("model tracker hook failed: %s", exc)


pool = BrowserPool()
