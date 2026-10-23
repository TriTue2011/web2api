"""Hạ tầng Chrome THẬT (DevTools Protocol) cho test giao diện — dùng chung cho test_web2api_giao_dien*.py.

Điều khiển Chrome bằng CDP từ Python (gói `websockets`): test xen kẽ đổi trạng thái lõi/máy chủ giả với thao tác
trên trang trong cùng tiến trình. Mỗi test chạy trong một browser context RIÊNG — localStorage (nơi lưu khoá
quản trị) không rò từ test này sang test khác. Chuyển nguyên văn từ test_web2api_cai_module_ui.py (trang cũ đã bỏ).

Đặt WEB2API_THU_MUC_ANH=<thư mục> để lưu ảnh chụp các trạng thái chính.
"""

from __future__ import annotations

import base64
import html
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

CHROME = os.environ.get("WEB2API_CHROME", "/usr/bin/google-chrome")
THU_MUC_ANH = os.environ.get("WEB2API_THU_MUC_ANH", "")
CO_CHROME = Path(CHROME).exists()

# ───────────────────────────── CDP ─────────────────────────────

# Bootstrap vượt chặn LNA (mượn cơ chế NV251 đã đo thật trên Chromium 152):
# điều hướng thẳng about:blank → http://127.0.0.1 bị chặn (ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS),
# nhưng file:// → http://127.0.0.1 thì ĐƯỢC PHÉP. Chỉ dùng trong test giao diện.
_THU_MUC_BOOT_LNA: Path | None = None


def _di_qua_file_bootstrap(trang: "Trang", url: str) -> None:
    """Mở `url` qua trang đệm file:// + meta-refresh (vượt chặn LNA của Chromium 152)."""
    global _THU_MUC_BOOT_LNA
    if _THU_MUC_BOOT_LNA is None:
        _THU_MUC_BOOT_LNA = Path(tempfile.gettempdir()) / "w2a-lna-boot"
        _THU_MUC_BOOT_LNA.mkdir(parents=True, exist_ok=True)
    boot = _THU_MUC_BOOT_LNA / f"boot-{abs(hash(url)) % 1000000}.html"
    boot.write_text(
        f'<!doctype html><meta http-equiv="refresh" content="0;url={html.escape(url, quote=True)}">',
        encoding="utf-8",
    )
    trang.cdp.goi("Page.navigate", {"url": f"file://{boot}"}, trang.s)


class CDP:
    """Một kết nối WebSocket tới trình duyệt (chế độ flatten: lệnh của từng trang mang sessionId)."""

    def __init__(self, ws_url: str):
        from contextlib import ExitStack

        from websockets.sync.client import connect

        # proxy=None: conftest đặt HTTP_PROXY trỏ cổng chết để chặn Internet; DevTools ở loopback.
        # Mở qua ExitStack: websockets 17 cảnh báo khi connect() không dùng như context manager.
        self._ngan = ExitStack()
        self.ws = self._ngan.enter_context(connect(ws_url, max_size=None, proxy=None, open_timeout=10))
        self.id = 0

    def goi(self, method: str, params: dict | None = None, session: str | None = None, han: float = 30):
        self.id += 1
        i = self.id
        msg = {"id": i, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        cuoi = time.monotonic() + han
        while True:
            m = json.loads(self.ws.recv(timeout=max(0.05, cuoi - time.monotonic())))
            if m.get("id") == i:
                if "error" in m:
                    raise RuntimeError(f"CDP {method}: {m['error']}")
                return m.get("result", {})

    def dong(self):
        self._ngan.close()


class Trang:
    def __init__(self, cdp: CDP, session: str):
        self.cdp = cdp
        self.s = session

    def js(self, expr: str):
        r = self.cdp.goi("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, self.s)
        if r.get("exceptionDetails"):
            raise AssertionError(f"JS lỗi: {expr[:200]}\n{json.dumps(r['exceptionDetails'], ensure_ascii=False)[:800]}")
        return r["result"].get("value")

    def cho(self, expr: str, han: float = 8, mo_ta: str = ""):
        cuoi = time.monotonic() + han
        while True:
            try:
                v = self.js(expr)
            except RuntimeError as e:
                # Trang TỰ điều hướng đúng lúc đang hỏi (vd «Cài» xong chuyển sang Studio): DevTools báo «Inspected target
                # navigated or closed» thay vì trả giá trị — hỏi lại ở trang mới. Khớp theo chữ vì mã lỗi -32000 của CDP
                # dùng chung cho mọi lỗi khác; lỗi khác vẫn ném ra.
                if "navigated or closed" not in str(e):
                    raise
                v = None
            if v:
                return v
            if time.monotonic() > cuoi:
                chu = self.js("document.body ? document.body.innerText.slice(0, 3000) : ''")
                raise AssertionError(f"Chờ quá {han}s: {mo_ta or expr}\n--- chữ trên trang ---\n{chu}")
            time.sleep(0.05)

    def di(self, url: str, mo_muc: bool = True):
        """Mở trang và đợi dựng xong. Trang có mục thu gọn (`[data-mo-muc]`, mặc định ĐÓNG theo quy ước giao diện) được
        mở hết để test thao tác như cũ; test kiểm «mặc định đóng» truyền mo_muc=False.

        NV256 (cơ chế từ NV251): mở qua đệm file:// + meta-refresh để vượt chặn Local Network Access
        của Chromium ≥138 (about:blank là «public» nên bị chặn khi điều hướng thẳng tới 127.0.0.1).
        Cookie/phiên vẫn dùng chung ngữ cảnh trình duyệt."""
        boot = Path(tempfile.gettempdir()) / f"w2a-boot-{abs(hash(url)) % 1000000}.html"
        boot.write_text(f'<meta http-equiv="refresh" content="0;url={url}">', encoding="utf-8")
        self.cdp.goi("Page.navigate", {"url": f"file://{boot}"}, self.s)
        self.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
                 han=20, mo_ta="dc-lite dựng xong trang (qua LNA bypass)")
        if mo_muc:
            self.mo_het_muc()

    def mo_het_muc(self):
        """Bấm mở mọi mục thu gọn đang đóng (mục có nút `[data-mo-muc]`); không có mục nào thì không làm gì. Mục dựng
        muộn (sau khi dữ liệu về) cũng được mở: lặp tới khi không còn mục đóng."""
        for _ in range(8):
            try:
                n = self.js("(() => { const ds = [...document.querySelectorAll('[data-mo-muc][aria-expanded=\"false\"]')];"
                            " ds.forEach(b => b.click()); return ds.length; })()")
            except RuntimeError as e:
                # Trang TỰ chuyển đi đúng lúc hỏi (vd 401 → trang đăng nhập): không còn mục nào để mở, giống `cho()`.
                if "navigated or closed" not in str(e):
                    raise
                return
            if not n:
                return
            time.sleep(0.15)

    def go(self, sel: str, chu: str):
        """Gõ như người (Input.insertText là sự kiện tin cậy) vào ô `sel`."""
        assert self.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); if (!e) return false; e.focus(); return document.activeElement === e; }})()"), f"không focus được {sel}"
        for i, dong in enumerate(chu.split("\n")):
            if i:
                self.cdp.goi("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter",
                                                        "windowsVirtualKeyCode": 13, "text": "\r"}, self.s)
                self.cdp.goi("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter",
                                                        "windowsVirtualKeyCode": 13}, self.s)
            if dong:
                self.cdp.goi("Input.insertText", {"text": dong}, self.s)
        time.sleep(0.05)

    def bam(self, expr_phan_tu: str, mo_ta: str = ""):
        ok = self.js(f"(() => {{ const e = {expr_phan_tu}; if (!e) return false; e.click(); return true; }})()")
        assert ok, f"không thấy phần tử để bấm: {mo_ta or expr_phan_tu}"
        time.sleep(0.05)

    def chu(self, sel: str) -> str:
        return self.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); return e ? e.innerText : null; }})()")

    def khung(self, rong: int, cao: int, dien_thoai: bool = False):
        self.cdp.goi("Emulation.setDeviceMetricsOverride", {"width": rong, "height": cao, "deviceScaleFactor": 1,
                                                            "mobile": dien_thoai}, self.s)

    def chup(self, ten: str):
        if not THU_MUC_ANH:
            return
        Path(THU_MUC_ANH).mkdir(parents=True, exist_ok=True)
        r = self.cdp.goi("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, self.s)
        (Path(THU_MUC_ANH) / f"{ten}.png").write_bytes(base64.b64decode(r["data"]))

    def state_thanh_phan(self) -> list[str]:
        """JSON của MỌI thể hiện DCLogic còn sống trên heap (Runtime.queryObjects) — đọc state thật
        của trang mà không cần trang tự phơi thể hiện ra window."""
        r = self.cdp.goi("Runtime.evaluate", {"expression": "DCLogic.prototype"}, self.s)
        ds = self.cdp.goi("Runtime.queryObjects", {"prototypeObjectId": r["result"]["objectId"]}, self.s)
        kq = self.cdp.goi("Runtime.callFunctionOn", {
            "objectId": ds["objects"]["objectId"], "returnByValue": True,
            "functionDeclaration": "function () { return this.map((o) => JSON.stringify(o)); }"}, self.s)
        return kq["result"]["value"]


def _cong_trong() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def trinh_duyet():
    cong = _cong_trong()
    ho_so = tempfile.mkdtemp(prefix="cdp-ui-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-proxy-server", f"--remote-debugging-port={cong}",
         f"--user-data-dir={ho_so}", "--window-size=1280,800", "--no-first-run",
         "--host-resolver-rules=MAP fonts.googleapis.com ~NOTFOUND, MAP fonts.gstatic.com ~NOTFOUND", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mo = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ws = None
    for _ in range(200):
        try:
            ws = json.loads(mo.open(f"http://127.0.0.1:{cong}/json/version", timeout=1).read())["webSocketDebuggerUrl"]
            break
        except OSError:
            time.sleep(0.05)
    assert ws, "Chrome không mở cổng DevTools"
    cdp = CDP(ws)
    yield cdp
    cdp.dong()
    proc.kill()
    proc.wait(10)
    shutil.rmtree(ho_so, ignore_errors=True)


@pytest.fixture
def trang(trinh_duyet):
    cdp = trinh_duyet
    ctx = cdp.goi("Target.createBrowserContext", {"disposeOnDetach": True})["browserContextId"]
    tid = cdp.goi("Target.createTarget", {"url": "about:blank", "browserContextId": ctx})["targetId"]
    s = cdp.goi("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    t = Trang(cdp, s)
    t.khung(1280, 800)
    yield t
    cdp.goi("Target.closeTarget", {"targetId": tid})
    cdp.goi("Target.disposeBrowserContext", {"browserContextId": ctx})


def dang_nhap_quan_tri(
    trang: Trang,
    base_url: str,
    ten: str = "admin_kiem_thu",
    mat_khau: str = "MatKhauAdminBaoMat123456",
) -> tuple[str, str]:
    """Đăng nhập quản trị viên qua giao diện Dang-nhap.dc.html (phiên cookie w2a_phien)."""
    import asyncio
    from app import nguoi_dung

    try:
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap=ten, mat_khau=mat_khau))
    except Exception:
        # Bỏ qua nếu tài khoản admin đã có trong kho
        pass

    trang.di(f"{base_url}/Dang-nhap.dc.html")
    trang.go("#o-ten", ten)
    trang.go("#o-mat-khau", mat_khau)
    trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Bấm nút đăng nhập")
    trang.cho(
        "!/(Dang-nhap|index)/.test(location.pathname) && (document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
        mo_ta="Đăng nhập xong và trang đích dựng xong",
    )
    return ten, mat_khau

