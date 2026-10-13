"""Chrome THẬT: trang «Workspace trình duyệt» của module vnc-xa (/m/vnc-xa/index.html).

Bố cục/nhãn/nút theo thẻ Workspace trình duyệt + mục «Tái dùng» của c2a. Chạy đủ vòng trên lõi thật (uvicorn, lifespan
bật) + module vnc-xa THẬT (chép vào <WEB2API_DATA>/modules) + dịch vụ trình duyệt GIẢ + module đích giả trên đĩa:
nhập khoá → tạo workspace → chọn dịch vụ → Mở (liên kết vé tab mới) → Đóng · giữ phiên → Tái dùng: lấy cookie sang
module → kết quả ĐÃ CHE. XSS: tên workspace (tệp trình duyệt ghi được), tên module, lời lỗi của module đích và URL vé
giả `javascript:` đều phải hiện thành CHỮ / bị từ chối, không chạy.
"""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import kho, novnc, so_module
from app.main import app
from gia import MayChuThat
from test_web2api_cookie_sang_module import MA_DICH, MANIFEST_DICH
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_DV = "khoa-dich-vu-GIA-0000000000000000"
COOKIE = "phien=GIA-ui-cookie-0001; phu=GIA-ui-phu-0001"
TEN_XSS = '<img src=x onerror="window.__xss=1">'
TAP_JS = GOC / "modules" / "vnc-xa" / "web" / "trang.js"


class DichVuWorkspaceGia:
    """Dịch vụ trình duyệt giả nói đúng hợp đồng HTTP của dich-vu/trinh-duyet (workspace + cookie)."""

    def __init__(self):
        self.ws: dict[str, dict] = {}
        self.goi: list[dict] = []
        self.cookie = COOKIE
        self.url = ""

    def _sai(self, request) -> bool:
        return request.headers.get("authorization") != f"Bearer {KHOA_DV}"

    async def ho_so(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        if request.method == "POST":
            than = await request.json()
            id_ = secrets.token_hex(12)
            self.ws[id_] = {"ten": than["ten"], "loai": than.get("loai", "firefox"), "mo": False}
            self.goi.append({"tao": than})
            return JSONResponse({"ho_so": id_, "ten": than["ten"]})
        return JSONResponse([{"ho_so": k, "ten": v["ten"], "loai": v["loai"],
                              "trang_thai": "dang_mo" if v["mo"] else "dong", "dang_mo": v["mo"], "thu_cong": v["mo"],
                              "luc_mo": None} for k, v in sorted(self.ws.items())])

    async def mo(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        than = await request.json()
        self.goi.append({"mo": than})
        self.ws[than["ho_so"]]["mo"] = True
        return JSONResponse({"ho_so": than["ho_so"], "loai": than["loai"], "dich": self.url})

    async def dong(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        than = await request.json()
        self.goi.append({"dong": request.path_params["id"], **than})
        self.ws[request.path_params["id"]]["mo"] = False
        return JSONResponse({"ok": True, "session_preserved": True})

    async def lay_cookie(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        self.goi.append({"cookie": request.path_params["id"], "mien": request.query_params.get("mien")})
        return JSONResponse({"cookie": self.cookie, "so_cookie": 2})

    def app(self):
        return Starlette(routes=[Route("/ho-so", self.ho_so, methods=["GET", "POST"]),
                                 Route("/ho-so/mo", self.mo, methods=["POST"]),
                                 Route("/ho-so/{id}/dong", self.dong, methods=["POST"]),
                                 Route("/ho-so/{id}/cookie", self.lay_cookie)])


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(GOC / "modules" / "vnc-xa", data / "modules" / "vnc-xa",
                    ignore=shutil.ignore_patterns("__pycache__"))
    dich = data / "modules" / "dich-gia"
    dich.mkdir(parents=True)
    man = dict(MANIFEST_DICH, ten="Đích " + TEN_XSS)
    man.pop("tab_web", None)
    (dich / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (dich / "module.py").write_text(MA_DICH, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for so in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so.clear()
    dv = DichVuWorkspaceGia()
    with MayChuThat(dv.app()) as s_dv:
        dv.url = s_dv.url
        with MayChuThat(app, lifespan="on") as w:
            r = httpx.post(f"{w.url}/api/modules/vnc-xa/cai", headers=QT,
                           json={"cau_hinh": {"dich_vu_url": s_dv.url, "khoa_dich_vu": KHOA_DV}})
            assert r.status_code == 200, r.text
            yield w, dv
    for so in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so.clear()


def _mo_trang(trang, url: str) -> None:
    trang.cdp.goi("Page.navigate", {"url": url}, trang.s)
    trang.cho("document.readyState === 'complete' && !!document.getElementById('ds-ws')", mo_ta="trang workspace")


def _hang(ten: str) -> str:
    return (f"[...document.querySelectorAll('.ws')].find(o => o.querySelector('.ws-ten').textContent === "
            f"{json.dumps(ten)})")


def test_trang_js_khong_chen_html():
    ma = TAP_JS.read_text(encoding="utf-8")
    for cam in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert cam not in ma, cam
    html = (GOC / "modules" / "vnc-xa" / "web" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html and " on" + "click=" not in html, "không script/sự kiện inline (CSP script-src 'self')"


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_vong_workspace_va_tai_dung_chrome(trang, moi_truong):
    w, dv = moi_truong
    # Workspace có sẵn với tên chứa HTML (tên nằm trong tệp mà trình duyệt cùng uid sửa được)
    dv.ws["aaaaaaaaaaaaaaaaaaaaaaaa"] = {"ten": TEN_XSS, "loai": "firefox", "mo": False}
    _mo_trang(trang, f"{w.url}/m/vnc-xa/index.html")

    # (a) chưa có khoá → ô nhập khoá
    trang.cho("!document.getElementById('o-khoa').hidden", mo_ta="ô khoá quản trị")
    trang.go("#khoa", KHOA_QT)
    trang.bam("document.getElementById('luu-khoa')")
    trang.cho(f"!!({_hang(TEN_XSS)})", mo_ta="danh sách workspace")
    assert trang.js("localStorage.getItem('web2api_khoa')") == KHOA_QT
    assert trang.js("getComputedStyle(document.getElementById('o-khoa')).display") == "none"
    assert trang.js(f"{_hang(TEN_XSS)}.querySelector('.ws-tt').textContent") == "Đã đóng · giữ phiên"

    # Tạo workspace: nút tắt khi ô trống
    assert trang.js("document.getElementById('tao').disabled") is True
    trang.go("#ten", "Muse cá nhân")
    trang.bam("document.getElementById('tao')")
    trang.cho(f"!!({_hang('Muse cá nhân')})", mo_ta="workspace mới")
    assert "Đã tạo hồ sơ riêng. Bấm Mở để đăng nhập." in trang.chu("#thong-bao")
    assert dv.goi[-1] == {"tao": {"ten": "Muse cá nhân", "loai": "firefox"}}

    # Chọn dịch vụ → trang cần mở
    trang.bam("[...document.querySelectorAll('#chon-dv button')].find(b => b.textContent === 'Muse')")
    assert trang.js("document.getElementById('url').value") == "https://muse.ai/"
    assert trang.js("document.querySelector('#chon-dv [data-url=\"https://muse.ai/\"]').getAttribute('aria-pressed')") == "true"

    # Mở → liên kết vé tab mới, cùng origin, noopener
    trang.bam(f"{_hang('Muse cá nhân')}.querySelector('button.mo')")
    trang.cho(f"!!{_hang('Muse cá nhân')}.querySelector('a.vao')", mo_ta="liên kết Vào trình duyệt")
    a = trang.js(f"(() => {{ const a = {_hang('Muse cá nhân')}.querySelector('a.vao'); "
                 "return {href: a.href, target: a.target, rel: a.rel}; })()")
    assert a["href"].startswith(w.url + "/novnc/dang-nhap?ve=") and a["target"] == "_blank"
    assert "noopener" in a["rel"] and "noreferrer" in a["rel"]
    mo = [g["mo"] for g in dv.goi if "mo" in g][-1]
    assert mo["loai"] == "firefox" and mo["url"] == "https://muse.ai/"
    assert trang.js(f"{_hang('Muse cá nhân')}.querySelector('.ws-tt').textContent") == "Đang mở thủ công"
    # Tái dùng chỉ liệt kê workspace ĐÃ ĐÓNG
    assert trang.js("[...document.getElementById('ws-tai-dung').options].map(o => o.value)") == ["aaaaaaaaaaaaaaaaaaaaaaaa"]

    # Vé hết hạn (60 giây) → liên kết ẩn, hiện «Mở lại» xin vé mới
    trang.js("(() => { const t = Date.now(); Date.now = () => t + 61000; })()")
    trang.bam("document.getElementById('lam-moi')")
    trang.cho(f"!{_hang('Muse cá nhân')}.querySelector('a.vao') && !!{_hang('Muse cá nhân')}.querySelector('button.mo-lai')",
              mo_ta="liên kết hết hạn → nút Mở lại")
    trang.bam(f"{_hang('Muse cá nhân')}.querySelector('button.mo-lai')")
    trang.cho(f"!!{_hang('Muse cá nhân')}.querySelector('a.vao')", mo_ta="vé mới sau Mở lại")
    assert "url" not in [g["mo"] for g in dv.goi if "mo" in g][-1]
    # «Mở» workspace ĐANG MỞ mà ô URL có giá trị → báo ngay: URL chỉ áp dụng khi mở mới (không gửi url)
    trang.bam(f"{_hang('Muse cá nhân')}.querySelector('button.mo')")
    trang.cho("document.getElementById('thong-bao').textContent.includes('URL chỉ áp dụng khi mở mới')")
    assert "url" not in [g["mo"] for g in dv.goi if "mo" in g][-1]

    # Đóng · giữ phiên
    trang.bam(f"{_hang('Muse cá nhân')}.querySelector('button.dong')")
    trang.cho("document.getElementById('thong-bao').textContent.includes('Đã đóng trình duyệt, giữ dữ liệu đăng nhập.')")
    assert {"giu_phien": True}.items() <= [g for g in dv.goi if "dong" in g][-1].items()
    ho_so_moi = next(k for k, v in dv.ws.items() if v["ten"] == "Muse cá nhân")
    trang.cho(f"[...document.getElementById('ws-tai-dung').options].some(o => o.value === '{ho_so_moi}')")

    # Tái dùng → lấy cookie sang module (trường bí mật lấy từ manifest, tên module chứa HTML hiện thành chữ)
    # Chỉ trường module đích KHAI nhan_cookie (không có «Khoá API»); nhiều trường → phải chọn, không chọn sẵn mục đầu
    nhan = trang.js("[...document.getElementById('dich').options].map(o => o.textContent)")
    assert nhan == ["— Chọn module nhận —", f"Đích {TEN_XSS} — Danh sách cookie · vi-du.example",
                    f"Đích {TEN_XSS} — Một cookie · vi-du.example"], nhan
    trang.js(f"(() => {{ const s = document.getElementById('ws-tai-dung'); s.value = '{ho_so_moi}'; "
             "s.dispatchEvent(new Event('change')); })()")
    assert trang.js("document.getElementById('lay-cookie').disabled") is True
    assert trang.js("document.getElementById('mien').value") == ""
    trang.js("(() => { const s = document.getElementById('dich'); s.value = '0'; "
             "s.dispatchEvent(new Event('change')); })()")
    assert trang.js("document.getElementById('mien').value") == "vi-du.example", "tên miền tự điền theo bản khai"
    assert trang.js("document.getElementById('mien').readOnly") is True
    trang.bam("document.getElementById('lay-cookie')")
    trang.cho("document.getElementById('ket-qua').textContent.includes('Thành công')", mo_ta="kết quả tái dùng")
    assert "Đã thêm 1 cookie" in trang.chu("#ket-qua")
    assert kho.doc_bi_mat("dich-gia")["cookies"] == [COOKIE]
    assert dv.goi[-1] == {"cookie": ho_so_moi, "mien": "vi-du.example"}
    html = trang.js("document.documentElement.outerHTML")
    for manh in ("GIA-ui-cookie-0001", "GIA-ui-phu-0001"):
        assert manh not in html, "cookie không bao giờ ra giao diện"

    # Lời lỗi của module đích chứa HTML → hiện thành chữ
    dv.cookie = "phien=GIA-XSS-TU-CHOI-0009"
    trang.bam("document.getElementById('lay-cookie')")
    trang.cho("document.getElementById('ket-qua').textContent.includes('Chưa thêm cookie mới')", mo_ta="kết quả")
    assert "Thành công" not in trang.chu("#ket-qua"), "da_them = 0 không được báo Thành công"
    assert "có thể đã tự bỏ" in trang.chu("#ket-qua"), "có khoá bị từ chối → cảnh báo module có thể tự bỏ khoá"
    assert trang.js("document.querySelector('#ket-qua .trang-thai').classList.contains('tot')") is False
    # Lời lỗi tự do của module đích (ở đây chứa HTML) KHÔNG được lõi chuyển tiếp — trang hiện câu của lõi.
    assert "window.__xss=4" not in trang.chu("#ket-qua")
    assert "Module đích từ chối khoá này" in trang.chu("#ket-qua")
    assert kho.doc_bi_mat("dich-gia")["cookies"] == [COOKIE], "cai() thất bại → không lưu"

    # URL vé giả `javascript:` (phản hồi bị chiếm) → không gắn vào href
    trang.js("""(() => { const f = window.fetch; window.fetch = (u, o) => String(u).endsWith('/mo')
        ? Promise.resolve(new Response(JSON.stringify({ho_so: 'x', ve: 'x', url: 'javascript:window.__xss=2'}),
            {status: 200, headers: {'content-type': 'application/json'}}))
        : f(u, o); })()""")
    trang.bam(f"{_hang(TEN_XSS)}.querySelector('button.mo')")
    trang.cho("document.getElementById('thong-bao').textContent.includes('Địa chỉ vào trình duyệt không hợp lệ.')")
    assert trang.js("[...document.querySelectorAll('a')].some(a => a.href.startsWith('javascript:'))") is False
    # Đúng đường trang vé nhưng KHÁC origin → cũng từ chối
    trang.js("window.__thong_bao_cu = ''; document.getElementById('thong-bao').textContent = ''")
    trang.js("""(() => { const f = window.fetch; window.fetch = (u, o) => String(u).endsWith('/mo')
        ? Promise.resolve(new Response(JSON.stringify({ho_so: 'x', ve: 'x', url: 'https://evil.example/novnc/dang-nhap?ve=x'}),
            {status: 200, headers: {'content-type': 'application/json'}}))
        : f(u, o); })()""")
    trang.bam(f"{_hang(TEN_XSS)}.querySelector('button.mo')")
    trang.cho("document.getElementById('thong-bao').textContent.includes('Địa chỉ vào trình duyệt không hợp lệ.')")
    assert trang.js("[...document.querySelectorAll('a')].some(a => a.href.includes('evil.example'))") is False

    # Không thẻ <img> nào được dựng từ dữ liệu, không script nào chạy
    assert trang.js("document.querySelectorAll('img').length") == 0
    assert trang.js("typeof window.__xss") == "undefined"
    assert trang.js(f"{_hang(TEN_XSS)}.querySelector('.ws-ten').textContent") == TEN_XSS


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_dien_thoai_khong_tran_ngang_nut_du_to_chrome(trang, moi_truong):
    w, dv = moi_truong
    dv.ws["bbbbbbbbbbbbbbbbbbbbbbbb"] = {"ten": "Tên workspace rất dài " * 6, "loai": "firefox", "mo": True}
    trang.khung(360, 800, dien_thoai=True)
    _mo_trang(trang, f"{w.url}/m/vnc-xa/index.html")
    trang.js(f"localStorage.setItem('web2api_khoa', {json.dumps(KHOA_QT)})")
    _mo_trang(trang, f"{w.url}/m/vnc-xa/index.html")
    trang.cho("document.querySelectorAll('.ws').length === 1", mo_ta="danh sách trên điện thoại")
    assert trang.js("document.documentElement.scrollWidth") <= 360
    cao = trang.js("[...document.querySelectorAll('button')].filter(b => b.offsetParent).map(b => b.offsetHeight)")
    assert cao and min(cao) >= 40, cao


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_csp_chan_script_inline_va_ket_noi_ngoai_chrome(trang, moi_truong):
    """CSP của /m/ có tác dụng THẬT trong Chrome: script inline chèn vào trang không chạy; fetch tới origin khác
    (dịch vụ giả, kể cả no-cors) bị chặn."""
    w, dv = moi_truong
    _mo_trang(trang, f"{w.url}/m/vnc-xa/index.html")
    trang.js("""(() => { window.__vi_pham = [];
        document.addEventListener('securitypolicyviolation', (e) => window.__vi_pham.push(e.violatedDirective));
        const s = document.createElement('script'); s.textContent = 'window.__inline = 1'; document.body.append(s);
        const a = document.createElement('div'); a.setAttribute('onclick', 'window.__inline = 2'); document.body.append(a);
        a.click(); })()""")
    assert trang.js("typeof window.__inline") == "undefined"
    kq = trang.js(f"fetch({json.dumps(dv.url + '/ho-so')}, {{mode: 'no-cors'}}).then(() => 'qua', () => 'chan')")
    assert kq == "chan"
    trang.cho("window.__vi_pham.some(d => d.startsWith('connect-src')) && window.__vi_pham.some(d => d.startsWith('script-src'))",
              mo_ta="trình duyệt báo vi phạm CSP")
