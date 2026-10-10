"""Test giao diện trang «Module» (web/Cai-module.dc.html) bằng Chrome THẬT, điều khiển qua DevTools.

Trang chạy trên dc-lite (web/support.js) và nói chuyện với API GIẢ (gia_api_ui.py) — giả để ép
được 401, lỗi 500, luồng SSE nhỏ giọt/dồn cục, nguồn git chưa cấu hình, tên module chứa HTML độc.
Hình dạng phản hồi của API giả chép theo mã lõi thật, nên test này KHÔNG chứng minh trang chạy
với lõi thật — chỉ chứng minh trang làm đúng với hợp đồng đó.

Điều khiển Chrome bằng CDP từ Python (gói `websockets` có trong uv.lock) thay vì node: test cần
xen kẽ đổi trạng thái API giả với thao tác trên trang, làm trong cùng tiến trình thì gọn hơn.
Mỗi test chạy trong một browser context RIÊNG — localStorage (nơi lưu khoá quản trị) không rò
từ test này sang test khác.

Đặt WEB2API_THU_MUC_ANH=<thư mục> để lưu ảnh chụp màn hình các trạng thái chính (1280×800, 390×844).
"""

from __future__ import annotations

import base64
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

from gia_api_ui import KHOA_QT, XSS_COMMIT, XSS_MODEL, XSS_TEN, XSS_TEN_2, MayChuGia

CHROME = os.environ.get("WEB2API_CHROME", "/usr/bin/google-chrome")
THU_MUC_ANH = os.environ.get("WEB2API_THU_MUC_ANH", "")

pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="cần Chrome")


# ───────────────────────────── CDP ─────────────────────────────

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
            v = self.js(expr)
            if v:
                return v
            if time.monotonic() > cuoi:
                chu = self.js("document.body ? document.body.innerText.slice(0, 3000) : ''")
                raise AssertionError(f"Chờ quá {han}s: {mo_ta or expr}\n--- chữ trên trang ---\n{chu}")
            time.sleep(0.05)

    def di(self, url: str):
        self.cdp.goi("Page.navigate", {"url": url}, self.s)
        self.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
                 mo_ta="dc-lite dựng xong trang")

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
def gia():
    with MayChuGia() as m:
        yield m


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


# ───────────────────────────── tiện ─────────────────────────────

def _mo_trang(trang, gia, khoa: str | None = KHOA_QT):
    trang.di(gia.url + "/Cai-module.dc.html")
    trang.cho("document.querySelector('[data-module=\"gemini-gia\"]')", mo_ta="catalog hiện")
    if khoa is not None:
        trang.go("#o-khoa", khoa)
        trang.bam("document.getElementById('nut-luu-khoa')")
        trang.cho("document.querySelector('#khoa-quan-tri [data-co-khoa]')", mo_ta="huy hiệu khoá đã lưu")


def _the_module(id_: str) -> str:
    return f"document.querySelector('[data-module={json.dumps(id_)}]')"


def _mo_form(trang, id_: str = "gemini-gia"):
    trang.bam(_the_module(id_) + ".querySelector('.nut-mo-form')", "nút mở form")
    trang.cho(_the_module(id_) + ".querySelector('form.form-cai')", mo_ta="form cài hiện")


def _o(id_: str, khoa: str) -> str:
    return f"[data-module={json.dumps(id_)}] [data-truong={json.dumps(khoa)}] .o"


# Mỗi dòng kết quả từng khoá → [tot|hong (theo dấu SVG ✓/✗ đang hiện), chữ].
_DONG_KQ = """[...document.querySelectorAll('{}')].map((l) => [
    l.querySelector('.dau-tot svg') ? 'tot' : l.querySelector('.dau-hong svg') ? 'hong' : '?', l.innerText.replace(/\\s+/g, ' ').trim()])"""

KHOA_1 = "AIzaSy-KHOA-MOT-tot-1111"
KHOA_2 = "AIzaSy-KHOA-HAI-hong-2222"
BI_MAT_PHU = "bi-mat-phu-RAT-RIENG-9999"


def _dien_va_cai(trang, khoa_api=(KHOA_1, KHOA_2), bi_mat=BI_MAT_PHU):
    _mo_form(trang)
    trang.go(_o("gemini-gia", "api_keys"), "\n".join(khoa_api))
    if bi_mat:
        trang.go(_o("gemini-gia", "ma_phu"), bi_mat)
    trang.bam(_the_module("gemini-gia") + ".querySelector('.nut-cai')", "nút Kiểm tra và cài")


# ───────────────────────────── test ─────────────────────────────

def test_nhap_khoa_hien_catalog_va_form_sinh_tu_manifest(trang, gia):
    trang.di(gia.url + "/Cai-module.dc.html")
    # Chưa có khoá: catalog (API mở) vẫn hiện, khối cần khoá nói rõ phải nhập khoá, không gọi API cần khoá.
    trang.cho(_the_module("gemini-gia"), mo_ta="catalog")
    assert "Chưa có khoá" in trang.chu("#khoa-quan-tri")
    assert "Nhập khoá quản trị" in trang.chu("#thong-bao")
    assert not gia.api.goi_toi("GET", "/api/modules/thong-bao")
    assert "docker exec <tên container> cat /data/admin_key" in trang.chu("#khoa-quan-tri")

    trang.go("#o-khoa", KHOA_QT)
    assert trang.js("document.getElementById('o-khoa').type") == "password"
    trang.bam("document.getElementById('nut-hien-khoa')")
    trang.cho("document.getElementById('o-khoa').type === 'text'", mo_ta="nút Hiện đổi ô sang chữ thường")
    trang.bam("document.getElementById('nut-luu-khoa')")
    trang.cho("document.querySelector('[data-thong-bao=\"dich-gia\"]')", mo_ta="thông báo git tải sau khi lưu khoá")
    assert trang.js("localStorage.getItem('web2api_khoa')") == KHOA_QT
    assert all(x["auth"] == f"Bearer {KHOA_QT}" for x in gia.api.goi_toi("GET", "/api/modules/thong-bao"))

    the = trang.chu("[data-module='gemini-gia']")
    assert "Gemini giả" in the and "Chưa cài" in the
    # Bảng tài nguyên: null → «chưa đo», không bịa số; số thật thì hiện đúng.
    bang = trang.js("[...document.querySelectorAll('[data-module=\"gemini-gia\"] .bang-dong')].map((d) => d.innerText.replace(/\\s+/g, ' ').trim())")
    assert bang == ["RAM thêm chưa đo", "Đĩa 12 MB", "GPU Không cần", "Mạng ra gia.example"], bang
    may = trang.chu("[data-module='gemini-gia'] .may-nay")
    assert "Máy bạn còn 6 GB RAM trống" in may and "container giới hạn 2 GB" in may, may
    # Module xss-mod: mọi ô tài nguyên null → «chưa đo»; cần 999999 MB → cảnh báo thiếu RAM.
    assert trang.js("document.querySelectorAll('[data-module=\"xss-mod\"] .chua-do').length") == 3
    assert "Có thể thiếu RAM" in trang.chu("[data-module='xss-mod']")

    # Form sinh từ manifest: ba kiểu trường → ba loại ô.
    _mo_form(trang)
    kieu = trang.js("""[...document.querySelectorAll('[data-module="gemini-gia"] [data-truong]')].map((d) => {
        const o = d.querySelector('.o'); return [d.dataset.truong, o.tagName, o.type, o.placeholder,
        d.querySelector('label').innerText.replace(/\\s+/g, ' ').trim(), document.querySelector('label[for="' + o.id + '"]') !== null]; })""")
    assert kieu == [
        ["api_keys", "TEXTAREA", "textarea", "Mỗi dòng một khoá", "Khoá API (bắt buộc)", True],
        ["base_url", "INPUT", "text", "https://gia.example/v1beta/openai", "Địa chỉ gốc", True],
        ["ma_phu", "INPUT", "password", "", "Mã bí mật phụ (bắt buộc)", True],
    ], kieu
    assert "Một hoặc nhiều khoá." in trang.chu("[data-module='gemini-gia'] [data-truong='api_keys']")
    trang.chup("1280-form-cai")


def test_thieu_truong_bat_buoc_khong_gui(trang, gia):
    _mo_trang(trang, gia)
    _mo_form(trang)
    trang.go(_o("gemini-gia", "api_keys"), KHOA_1)  # thiếu «Mã bí mật phụ»
    trang.bam(_the_module("gemini-gia") + ".querySelector('.nut-cai')")
    loi = trang.cho(_the_module("gemini-gia") + "?.querySelector('.loi-form')?.innerText", mo_ta="báo thiếu trường")
    assert "Còn thiếu trường bắt buộc" in loi and "Mã bí mật phụ" in loi and "Khoá API" not in loi, loi
    # Chỉ khoảng trắng cũng là thiếu.
    trang.go(_o("gemini-gia", "ma_phu"), "   ")
    trang.bam(_the_module("gemini-gia") + ".querySelector('.nut-cai')")
    time.sleep(0.3)
    assert not gia.api.goi_toi("POST", "/api/modules/gemini-gia/cai"), "thiếu trường bắt buộc mà vẫn gửi"


def test_cai_thanh_cong_hien_tung_khoa_va_khoa_bien_mat(trang, gia):
    _mo_trang(trang, gia)
    _dien_va_cai(trang)
    trang.cho(_the_module("gemini-gia") + "?.querySelector('.dung-ngay')", mo_ta="khối Dùng ngay")

    dong = trang.js(_DONG_KQ.format("[data-module=\"gemini-gia\"] .ket-qua-cai li"))
    assert len(dong) == 2, dong
    assert dong[0][0] == "tot" and "…1111 · dùng được" in dong[0][1] and "123 ms · 42 model" in dong[0][1], dong
    assert dong[1][0] == "hong" and "…2222 · không dùng được" in dong[1][1] and "Lý do: HTTP 400: API key not valid" in dong[1][1], dong
    assert "Đã cài Gemini giả" in trang.chu("[data-module='gemini-gia'] .ket-qua-cai")

    # Thân gửi đi: đủ ba trường; base_url để trống → lấy mặc định của manifest.
    goi = gia.api.goi_toi("POST", "/api/modules/gemini-gia/cai")
    assert len(goi) == 1 and goi[0]["auth"] == f"Bearer {KHOA_QT}"
    assert json.loads(goi[0]["body"]) == {"cau_hinh": {"api_keys": [KHOA_1, KHOA_2], "ma_phu": BI_MAT_PHU,
                                                       "base_url": "https://gia.example/v1beta/openai"}}
    # Khoá chỉ đi qua đúng thân POST đó, không ở URL/header nào khác.
    for x in gia.api.nhat_ky:
        if x is not goi[0]:
            assert KHOA_1 not in json.dumps(x) and BI_MAT_PHU not in json.dumps(x), x

    # Khoá API không còn ở đâu trên trang: DOM, giá trị ô, localStorage, state của thành phần.
    html = trang.js("document.documentElement.outerHTML")
    gia_tri_o = trang.js("[...document.querySelectorAll('input, textarea, select')].map((o) => o.value)")
    kho = trang.js("JSON.stringify(Object.entries(localStorage))")
    state = trang.state_thanh_phan()
    assert any(KHOA_QT in s for s in state), "queryObjects không thấy thể hiện của trang — phép kiểm state vô nghĩa"
    for bi_mat in (KHOA_1, KHOA_2, BI_MAT_PHU):
        assert bi_mat not in html, f"{bi_mat} còn trong DOM"
        assert not any(bi_mat in v for v in gia_tri_o), f"{bi_mat} còn trong ô nhập"
        assert bi_mat not in kho, f"{bi_mat} trong localStorage"
        assert not any(bi_mat in s for s in state), f"{bi_mat} còn trong state"
    assert json.loads(kho) == [["web2api_khoa", KHOA_QT]]

    # Dùng ngay: địa chỉ cơ sở, ví dụ model, curl chỉ có chữ giữ chỗ.
    assert trang.chu("[data-module='gemini-gia'] .dia-chi-co-so") == gia.url + "/v1"
    assert trang.chu("[data-module='gemini-gia'] .vi-du-model") == "gemini/gemini-2.5-flash"
    curl = trang.chu("[data-module='gemini-gia'] .lenh-curl")
    assert "Bearer <KHOÁ_QUẢN_TRỊ>" in curl and KHOA_QT not in curl and gia.url + "/v1/chat/completions" in curl, curl
    # Sau khi cài, danh sách «đã cài» và catalog được tải lại.
    trang.cho("document.querySelector('[data-da-cai=\"gemini-gia\"]')", mo_ta="module vừa cài xuất hiện ở «Module đã cài»")
    trang.cho(_the_module("gemini-gia") + ".querySelector('.trang-thai').innerText === 'Đã cài'", mo_ta="trạng thái Đã cài")
    trang.chup("1280-cai-xong")

    trang.bam(_the_module("gemini-gia") + ".querySelector('.nut-thu-chat')")
    trang.cho("document.getElementById('chat-model')?.value === 'gemini/gemini-2.5-flash'", mo_ta="Thử chat chọn sẵn model")


def test_cai_that_bai_hien_loi(trang, gia):
    gia.api.che_do["cai"] = "tu_choi"
    _mo_trang(trang, gia)
    _dien_va_cai(trang, khoa_api=(KHOA_1,))
    kq = trang.cho(_the_module("gemini-gia") + "?.querySelector('.ket-qua-cai')?.innerText", mo_ta="kết quả cài hỏng")
    assert "Chưa cài được: Không khoá nào gọi được" in kq, kq
    assert "…1111 · không dùng được" in kq and "API key not valid" in kq, kq
    assert [d[0] for d in trang.js(_DONG_KQ.format("[data-module=\"gemini-gia\"] .ket-qua-cai li"))] == ["hong"]
    assert "dán lại" in kq
    assert not trang.js("!!document.querySelector('[data-module=\"gemini-gia\"] .dung-ngay')")
    # Hỏng cũng xoá khoá khỏi ô — form vẫn mở để dán lại.
    assert trang.js(f"document.querySelector({json.dumps(_o('gemini-gia', 'api_keys'))}).value") == ""
    assert KHOA_1 not in trang.js("document.documentElement.outerHTML")
    assert not any(KHOA_1 in s for s in trang.state_thanh_phan())

    # Lõi sập (500 kiểu OpenAI) → hiện đúng thông điệp lỗi.
    gia.api.che_do["cai"] = "500"
    trang.go(_o("gemini-gia", "api_keys"), KHOA_1)
    trang.go(_o("gemini-gia", "ma_phu"), BI_MAT_PHU)
    trang.bam(_the_module("gemini-gia") + ".querySelector('.nut-cai')")
    trang.cho(_the_module("gemini-gia") + ".querySelector('.ket-qua-cai')?.innerText.includes('Lõi gặp sự cố khi cài (giả)')",
              mo_ta="thông điệp lỗi 500")


def test_401_hien_khoa_sai(trang, gia):
    _mo_trang(trang, gia, khoa="khoa-SAI-hoan-toan-000000")
    loi = trang.cho("document.getElementById('khoa-loi')?.innerText", mo_ta="báo khoá sai ngay khi tải khối cần khoá")
    assert "Khoá sai hoặc thiếu" in loi and "401" in loi, loi
    assert trang.chu("#khoa-quan-tri [data-co-khoa]") == "Khoá bị từ chối …0000"
    # Lệnh do người bấm cũng vậy (đường bấm riêng: test_khong_co_khoa_bam_cai_cung_bao_thieu).
    _dien_va_cai(trang, khoa_api=(KHOA_1,))
    trang.cho("document.querySelector('[data-module=\"gemini-gia\"] .ket-qua-cai')?.innerText.includes('khoá quản trị sai hoặc thiếu')",
              mo_ta="401 khi cài hiện ở kết quả cài")
    assert "Khoá sai hoặc thiếu" in trang.chu("#khoa-loi")
    trang.chup("1280-khoa-sai")
    # Sửa khoá đúng → thông báo lỗi biến mất, khối cần khoá tải được.
    trang.js("document.getElementById('o-khoa').select()")
    trang.go("#o-khoa", KHOA_QT)
    trang.bam("document.getElementById('nut-luu-khoa')")
    trang.cho("!document.getElementById('khoa-loi') && document.querySelector('[data-thong-bao=\"dich-gia\"]')",
              mo_ta="khoá đúng xoá báo lỗi")


def test_khong_co_khoa_bam_cai_cung_bao_thieu(trang, gia):
    _mo_trang(trang, gia, khoa=None)
    _dien_va_cai(trang, khoa_api=(KHOA_1,))
    loi = trang.cho("document.getElementById('khoa-loi')?.innerText", mo_ta="báo thiếu khoá")
    assert "Khoá sai hoặc thiếu" in loi and "chưa có khoá" in loi, loi


def test_thong_bao_git_va_cai(trang, gia):
    _mo_trang(trang, gia)
    trang.cho("document.querySelector('[data-thong-bao=\"dich-gia\"]')")
    assert trang.chu("#so-thong-bao") == "2"
    assert trang.js("!!document.querySelector('#thanh-tab .cham')"), "thanh trên thiếu chấm báo"
    moi = trang.chu("[data-thong-bao='dich-gia']")
    assert "Mới" in moi and "dich-gia · bản 0.2.0" in moi and "abcdef1" in moi and "abcdef12" not in moi, moi
    assert "Tri Tue: feat: thêm dịch" in moi and "chi tiết dài" not in moi, moi
    cap_nhat = trang.chu("[data-thong-bao='gemini-gia']")
    assert "Có bản mới" in cap_nhat and "bản 0.1.0 → 0.1.1" in cap_nhat and XSS_COMMIT in cap_nhat, cap_nhat
    assert trang.js("document.querySelector('[data-thong-bao=\"hong\"] .nut-cai-git').disabled") is True
    assert "thiếu manifest.json" in trang.chu("[data-thong-bao='hong']")
    ng = trang.chu("#nguon-git")
    assert "TriTue2011/web2api" in ng and "còn 4999 lượt" in ng and "đã có …abcd" in ng, ng

    trang.bam("document.querySelector('[data-thong-bao=\"dich-gia\"] .nut-cai-git')")
    # Cài xong máy chủ bỏ mục khỏi danh sách thông báo: tin phải nằm ở mức khối và còn SAU lần tải lại.
    trang.cho("!document.querySelector('[data-thong-bao=\"dich-gia\"]') && document.querySelector('[data-module=\"dich-gia\"]')",
              mo_ta="mục rời danh sách thông báo, module vào catalog")
    time.sleep(0.3)
    tin = trang.chu("#thong-bao .tin-cai-git")
    assert tin and "Đã tải Dịch giả bản 0.2.0 về máy" in tin and "cần cấu hình" in tin, tin
    assert trang.js("document.querySelector('#thong-bao .tin-cai-git a').getAttribute('href')") == "#the-dich-gia"
    assert trang.js("!!document.getElementById('the-dich-gia')?.querySelector('form.form-cai')"), "form cấu hình chưa mở sẵn"
    assert trang.chu("#so-thong-bao") == "1"
    goi = gia.api.goi_toi("POST", "/api/nguon-git/cai/dich-gia")
    assert len(goi) == 1 and goi[0]["auth"] == f"Bearer {KHOA_QT}"
    trang.chup("1280-thong-bao")


def test_chua_cau_hinh_git_giai_thich_va_luu_nguon(trang, gia):
    gia.api.nguon_git["cau_hinh"] = None
    _mo_trang(trang, gia)
    gt = trang.cho("document.querySelector('.tb-chua-cau-hinh')?.innerText", mo_ta="giải thích chưa cấu hình")
    assert "Chưa cấu hình nguồn git" in gt and "Cấu hình nguồn git" in gt, gt
    assert trang.js("document.querySelector('.tb-chua-cau-hinh a').getAttribute('href')") == "#nguon-git"
    trang.cho("document.getElementById('ng-repo')", mo_ta="form nguồn git mở sẵn")
    assert trang.js("document.getElementById('ng-nhanh').value") == "main"

    trang.go("#ng-repo", "chu/kho-module")
    trang.js("document.getElementById('ng-chu-ky').select()")
    trang.go("#ng-chu-ky", "600")
    trang.bam("document.getElementById('ng-tu-dong')")
    trang.bam("document.querySelector('.form-ng button[type=submit]')")
    trang.cho("document.querySelector('.form-ng')?.innerText.includes('Đã lưu')", mo_ta="lưu nguồn git")
    than = json.loads(gia.api.goi_toi("PUT", "/api/nguon-git")[0]["body"])
    # Ô token trống thì KHÔNG gửi khoá token (máy chủ coi "token": "" là xoá token cũ); số là số nguyên.
    assert than == {"repo": "chu/kho-module", "nhanh": "main", "thu_muc": "modules", "chu_ky_giay": 600,
                    "tu_dong": True, "yeu_cau_chu_ky": False}, than

    # Có gõ token → gửi, rồi ô token rỗng, token không còn trên trang.
    token = "ghp_TOKENRIENG1234567890"
    trang.go(".form-ng input[type=password]", token)
    trang.bam("document.querySelector('.form-ng button[type=submit]')")
    trang.cho(f"document.querySelector('#nguon-git')?.innerText.includes('…{token[-4:]}')", mo_ta="token đã che hiện ra")
    assert json.loads(gia.api.goi_toi("PUT", "/api/nguon-git")[1]["body"])["token"] == token
    assert token not in trang.js("document.documentElement.outerHTML")
    assert trang.js("document.querySelector('.form-ng input[type=password]').value") == ""
    assert not any(token in s for s in trang.state_thanh_phan())


def test_thong_bao_503_giai_thich(trang, gia):
    gia.api.che_do["thong_bao"] = "503"
    _mo_trang(trang, gia)
    gt = trang.cho("document.querySelector('.tb-chua-cau-hinh')?.innerText", mo_ta="giải thích khi 503")
    assert "HTTP 503" in gt and "Cấu hình nguồn git" in gt, gt
    assert not trang.js("!!document.querySelector('#thong-bao .bao-loi[role=alert]')")


def test_chat_stream_tung_khuc_theo_thu_tu(trang, gia):
    _mo_trang(trang, gia)
    trang.cho("document.getElementById('chat-model')?.value === 'gemini/gemini-2.5-flash'")
    assert trang.js("document.getElementById('chat-truyen').checked") is True
    trang.go("#chat-nhap", "Chào")
    trang.bam("document.getElementById('nut-gui')")
    thay = []
    cuoi = time.monotonic() + 8
    while time.monotonic() < cuoi:
        t = trang.chu("#tra-loi")
        if not thay or thay[-1] != t:
            thay.append(t)
        if (trang.chu("#chat-tt") or "").startswith("Xong"):
            break
        time.sleep(0.02)
    # Đọc lần cuối SAU khi thấy «Xong»: lần đọc chữ ở trên có thể đứng trước lần dựng cuối.
    if thay[-1] != trang.chu("#tra-loi"):
        thay.append(trang.chu("#tra-loi"))
    day_du = "".join(gia.api.che_do["khuc"])
    assert thay[-1] == day_du, thay
    giua = [t for t in thay if t and t != day_du]
    assert len(giua) >= 3, f"không thấy chữ hiện dần: {thay}"
    for a, b in zip(thay, thay[1:]):
        assert b.startswith(a), f"khúc sai thứ tự: {a!r} → {b!r}"
    body = json.loads(gia.api.goi_toi("POST", "/v1/chat/completions")[0]["body"])
    assert body == {"model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "Chào"}], "stream": True}


def test_chat_dung_giua_chung(trang, gia):
    gia.api.che_do.update(khuc=[f"k{i} " for i in range(30)], tre_khuc=0.25)
    _mo_trang(trang, gia)
    trang.cho("document.getElementById('chat-model')")
    trang.go("#chat-nhap", "Kể dài")
    trang.bam("document.getElementById('nut-gui')")
    trang.cho("document.getElementById('tra-loi').innerText.includes('k1 ')", mo_ta="đã có vài khúc")
    trang.bam("document.getElementById('nut-dung')")
    trang.cho("(document.getElementById('chat-tt')?.innerText || '').includes('Đã dừng')", mo_ta="báo đã dừng")
    luc_dung = trang.chu("#tra-loi")
    time.sleep(1.0)
    assert trang.chu("#tra-loi") == luc_dung, "vẫn nối chữ sau khi bấm Dừng"
    assert "k29" not in luc_dung
    assert trang.js("document.getElementById('nut-gui').disabled") is False
    assert trang.js("document.getElementById('nut-dung').disabled") is True


def test_chat_80_khuc_mot_lan_khong_vuong_chot_dc_lite(trang, gia):
    gia.api.che_do.update(khuc=[f"{i}," for i in range(80)], don_mot_lan=True)
    _mo_trang(trang, gia)
    trang.cho("document.getElementById('chat-model')")
    trang.go("#chat-nhap", "x")
    trang.bam("document.getElementById('nut-gui')")
    trang.cho("(document.getElementById('chat-tt')?.innerText || '').startsWith('Xong')", mo_ta="xong")
    assert trang.chu("#tra-loi") == "".join(f"{i}," for i in range(80))
    assert not trang.js("!!document.getElementById('dc-loi')"), trang.chu("#dc-loi")


def test_chat_loi_va_khong_stream(trang, gia):
    _mo_trang(trang, gia)
    trang.cho("document.getElementById('chat-model')")
    gia.api.che_do["chat"] = "loi"
    trang.go("#chat-nhap", "x")
    trang.bam("document.getElementById('nut-gui')")
    loi = trang.cho("document.getElementById('chat-loi')?.innerText", mo_ta="lỗi OpenAI")
    assert loi == "Model «gemini/khong-co» không tồn tại (giả)", loi
    gia.api.che_do["chat"] = "ok"
    trang.bam("document.getElementById('chat-truyen')")
    trang.bam("document.getElementById('nut-gui')")
    trang.cho("(document.getElementById('chat-tt')?.innerText || '').startsWith('Xong')")
    assert trang.chu("#tra-loi") == "".join(gia.api.che_do["khuc"])
    assert json.loads(gia.api.goi_toi("POST", "/v1/chat/completions")[-1]["body"])["stream"] is False


def test_tab_web_va_khong_chen_html_tho(trang, gia):
    _mo_trang(trang, gia)
    trang.cho("document.querySelector('[data-thong-bao=\"gemini-gia\"]') && document.getElementById('chat-model')")
    tab = trang.js("[...document.querySelectorAll('#thanh-tab a[data-tab-module]')].map((a) => [a.dataset.tabModule, a.innerText.trim(), a.href])")
    # Tab hợp lệ hiện; tab javascript: bị lọc bỏ.
    assert tab == [["tien-ich", "Tab giả", gia.url + "/m/tien-ich/index.html"]], tab
    assert trang.js("[...document.querySelectorAll('a')].every((a) => !/^\\s*javascript:/i.test(a.getAttribute('href') || ''))")
    # Chuỗi HTML độc hiện thành CHỮ ở tên module, tên đã cài, lời commit, model.
    assert trang.chu("[data-module='xss-mod'] .ten-module") == XSS_TEN
    assert XSS_TEN_2 in trang.chu("[data-da-cai='xau']")
    assert XSS_COMMIT in trang.chu("[data-thong-bao='gemini-gia']")
    assert XSS_MODEL in trang.js("[...document.querySelectorAll('#chat-model option')].map((o) => o.textContent)")
    assert "<b>mô tả</b><script>window.__xss=6</script>" in trang.chu("[data-module='xss-mod']")
    assert trang.js("document.querySelectorAll('x-dc img, x-dc script, x-dc b').length") == 0
    assert trang.js("typeof window.__xss") == "undefined"
    # Mục hỏng của modules.json vẫn hiện, không làm sập trang.
    assert "Mục hỏng trong modules.json" in trang.chu("#da-cai")


def test_suc_khoe_va_go(trang, gia):
    _mo_trang(trang, gia)
    trang.cho("document.querySelector('[data-da-cai=\"tien-ich\"]')")
    the = "document.querySelector('[data-da-cai=\"tien-ich\"]')"
    assert "…wxyz" in trang.chu("[data-da-cai='tien-ich']")
    trang.bam(the + ".querySelector('.nut-suc-khoe')")
    sk = trang.cho(the + ".querySelector('.suc-khoe')?.innerText", mo_ta="kết quả sức khoẻ")
    dong = trang.js(_DONG_KQ.format("[data-da-cai=\"tien-ich\"] .suc-khoe li"))
    assert dong[0][0] == "tot" and "…wxyz · dùng được" in dong[0][1] and "55 ms · 7 model" in dong[0][1], dong
    assert dong[1][0] == "hong" and "HTTP 429: hết hạn mức" in dong[1][1], dong

    trang.bam(the + ".querySelector('.nut-go')")
    trang.cho(the + ".querySelector('.xac-nhan-go')", mo_ta="bước xác nhận gỡ")
    assert not gia.api.goi_toi("DELETE", "/api/modules/tien-ich"), "gỡ mà chưa xác nhận"
    trang.bam(the + ".querySelector('.nut-go-han')")
    trang.cho("!document.querySelector('[data-da-cai=\"tien-ich\"]')", mo_ta="module biến mất sau khi gỡ")
    assert len(gia.api.goi_toi("DELETE", "/api/modules/tien-ich")) == 1
    # Gỡ xong thì tab của nó cũng biến mất khỏi thanh trên.
    assert not trang.js("!!document.querySelector('#thanh-tab a[data-tab-module=\"tien-ich\"]')")


def test_module_tu_xa(trang, gia):
    _mo_trang(trang, gia)
    trang.bam("document.querySelector('.form-tu-xa button[type=submit]')")
    assert "Còn thiếu: mã, tên, tiền tố, địa chỉ gốc" in trang.cho("document.querySelector('.form-tu-xa [role=alert]')?.innerText")
    khoa = "sk-khoa-tu-xa-RIENG-7777"
    trang.go("#tx-id", "may-gpu")
    trang.go("#tx-ten", "Máy GPU")
    trang.go("#tx-tien-to", "gpu")
    trang.go("#tx-url", "http://192.168.1.20:8000/v1")
    trang.go(".form-tu-xa input[type=password]", khoa)
    trang.bam("document.querySelector('.form-tu-xa button[type=submit]')")
    trang.cho("document.querySelector('.form-tu-xa')?.innerText.includes('Đã đăng ký Máy GPU')", mo_ta="đăng ký xong")
    than = json.loads(gia.api.goi_toi("POST", "/api/modules/tu-xa")[0]["body"])
    assert than == {"id": "may-gpu", "ten": "Máy GPU", "tien_to": "gpu", "base_url": "http://192.168.1.20:8000/v1", "api_key": khoa}
    assert "…7777" in trang.chu(".form-tu-xa") and "33 ms" in trang.chu(".form-tu-xa")
    assert khoa not in trang.js("document.documentElement.outerHTML")
    assert trang.js("document.querySelector('.form-tu-xa input[type=password]').value") == ""
    assert not any(khoa in s for s in trang.state_thanh_phan())
    trang.cho("document.querySelector('[data-da-cai=\"may-gpu\"]')", mo_ta="module từ xa vào danh sách đã cài")


def test_catalog_loi_va_dang_tai(trang, gia):
    gia.api.che_do["catalog"] = "cham"
    trang.di(gia.url + "/Cai-module.dc.html")
    assert "Đang đọc danh sách module" in trang.chu("#catalog")
    trang.cho(_the_module("gemini-gia"))
    gia.api.che_do["catalog"] = "500"
    trang.di(gia.url + "/Cai-module.dc.html")
    loi = trang.cho("document.querySelector('#catalog [role=alert]')?.innerText", mo_ta="lỗi catalog")
    assert "Sổ module hỏng (giả)" in loi, loi
    gia.api.che_do["catalog"] = "ok"
    trang.bam("document.querySelector('#catalog [role=alert] button')")
    trang.cho(_the_module("gemini-gia"), mo_ta="Thử lại tải được")
    assert "thiếu manifest.json" in trang.chu("#catalog")


@pytest.mark.parametrize("rong,cao", [(390, 844), (1280, 800)])
def test_khong_tran_ngang_va_nut_du_lon(trang, gia, rong, cao):
    trang.khung(rong, cao, dien_thoai=rong < 600)
    _mo_trang(trang, gia)
    trang.cho("document.querySelector('[data-thong-bao=\"dich-gia\"]') && document.getElementById('chat-model')")
    # Bày ra hết: form cài, kết quả từng khoá, khối Dùng ngay (curl dài), sức khoẻ, form nguồn git, chat.
    _dien_va_cai(trang)
    trang.cho(_the_module("gemini-gia") + "?.querySelector('.dung-ngay')")
    _mo_form(trang, "xss-mod")
    trang.bam("document.querySelector('[data-da-cai=\"tien-ich\"] .nut-suc-khoe')")
    trang.cho("document.querySelector('[data-da-cai=\"tien-ich\"] .suc-khoe')")
    trang.bam("document.querySelector('.nut-mo-ng')")
    trang.cho("document.getElementById('ng-repo')")
    trang.go("#chat-nhap", "x")
    trang.bam("document.getElementById('nut-gui')")
    trang.cho("(document.getElementById('chat-tt')?.innerText || '').startsWith('Xong')")
    time.sleep(0.2)
    do = trang.js("({ sw: document.documentElement.scrollWidth, bw: document.body.scrollWidth, w: window.innerWidth })")
    assert do["sw"] <= do["w"] and do["bw"] <= do["w"], f"tràn ngang ở {rong}px: {do}"
    # Phần tử nào nhô ra ngoài khung (thủ phạm nếu bị overflow:hidden che mất).
    nho = trang.js("""[...document.querySelectorAll('x-dc *')].filter((e) => { const r = e.getBoundingClientRect();
        return r.width > 0 && r.right > window.innerWidth + 0.5 && !e.closest('.thanh-tab'); }).slice(0, 5).map((e) => e.tagName + '.' + e.className)""")
    assert nho == [], f"phần tử tràn khỏi khung {rong}px: {nho}"
    if rong < 600:
        thap = trang.js("""[...document.querySelectorAll('x-dc button, x-dc a.nut, #thanh-tab a')].filter((b) => b.getClientRects().length)
            .map((b) => [b.innerText.trim(), Math.round(b.getBoundingClientRect().height)]).filter(([, h]) => h < 40)""")
        assert thap == [], f"nút thấp hơn 40 px: {thap}"
    trang.js("window.scrollTo(0, 0)")
    trang.chup(f"{rong}-day-du")
