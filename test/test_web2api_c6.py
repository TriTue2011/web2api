"""Kiểm thử Chuẩn C6: Trình duyệt từ xa qua noVNC (lõi cấp vé/phiên/proxy + module vnc-xa).

Thiết kế đang kiểm (xem đầu app/novnc.py):
- Vé 60 giây, dùng một lần. `GET /novnc/dang-nhap?ve=` chỉ trả trang có NÚT; đổi vé bằng `POST` (bot xem trước
  liên kết chỉ GET nên không giành được vé). Đổi vé + kiểm trần + tạo phiên là MỘT thao tác nguyên tử.
- Không cookie. Phiên 256 bit nằm TRONG ĐƯỜNG DẪN `/novnc/s/<phien>/…`; (module, hồ sơ) suy ra từ phiên.
- MỌI phản hồi dưới `/novnc/s/` mang CSP `sandbox` (không allow-same-origin → origin mờ: script của đích không đọc được
  localStorage chứa khoá quản trị, không gọi /api bằng quyền quản trị), ACAO *, nosniff, no-referrer, no-store, XFO DENY.
- Header hai chiều theo DANH SÁCH CHO PHÉP; chỉ GET/HEAD; ghép đường theo đoạn, mã hoá lại từng đoạn.
- Đích GHIM lúc `mo`: phân giải một lần, kiểm mọi địa chỉ, kết nối bằng IP đã kiểm; userinfo của `dich` tách ra
  thành Basic auth, không bao giờ lộ.
- WebSocket đăng ký ngay khi nhận, kiểm lại sau khi nối backend, bị đóng khi thu hồi hoặc phiên hết hạn.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import nhat_ky, novnc, so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_DICH_VU_GIA = "sk-khoa-tu-xa-RIENG-7777"
#: Bí mật GIẢ của websockify phía dịch vụ (userinfo trong `dich`), đặc trưng để quét rò rỉ.
BI_MAT_DICH_GIA = "bimat-dich-GIA-000-ZZQ9"
BASIC_DUNG = "Basic " + base64.b64encode(f"w2a:{BI_MAT_DICH_GIA}".encode()).decode()
MAU_PHIEN = re.compile(r"^/novnc/s/([A-Za-z0-9_-]{40,})/vnc\.html\?")


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    # Sổ vé/phiên/hồ sơ sống trong RAM của tiến trình: mỗi test bắt đầu sạch.
    for so in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so.clear()
    yield tmp_path
    for so in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so.clear()


# ───────────────────────────── Dịch vụ + noVNC giả ─────────────────────────────

#: Trang noVNC GIẢ mô phỏng đúng cách noVNC 1.6 dựng đường WebSocket: `new URL(path, location.href)`.
TRANG_VNC = """<!doctype html><html><head><meta charset="utf-8"><title>noVNC</title>
<script type="module" src="app.js"></script></head>
<body><h1>noVNC Mock Interface</h1><pre id="kq"></pre></body></html>"""

#: Script của ĐÍCH (coi như bị chiếm): import module, mở WebSocket, cố đọc bộ nhớ của origin web2api và gọi /api.
APP_JS = """import { GIA_TRI } from './phu.js';
const KQ = { module: GIA_TRI, origin: String(window.origin) };
window.KQ = KQ;
function thu(f) { try { return String(f()); } catch (e) { return 'loi:' + e.name; } }
KQ.ls = thu(() => localStorage.getItem('web2api_khoa'));
KQ.cookie = thu(() => document.cookie);
const q = new URLSearchParams(location.search);
const url = new URL(q.get('path') || 'websockify', location.href);
url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
KQ.ws_url = url.pathname;
const cho_ws = new Promise((ok) => {
  let ws;
  try { ws = new WebSocket(url.href, ['binary']); } catch (e) { KQ.ws = 'loi:' + e.name; return ok(); }
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => ws.send(new TextEncoder().encode('xin-chao'));
  ws.onmessage = (ev) => { KQ.ws = new TextDecoder().decode(ev.data); ws.close(); ok(); };
  ws.onerror = () => { KQ.ws = KQ.ws || 'loi:ws'; ok(); };
  setTimeout(() => { KQ.ws = KQ.ws || 'loi:het-gio'; ok(); }, 5000);
});
const khoa = (KQ.ls && !KQ.ls.startsWith('loi:') && KQ.ls !== 'null') ? KQ.ls : '';
const cho_api = fetch('/api/trinh-duyet/phien', { method: 'DELETE',
    headers: khoa ? { Authorization: 'Bearer ' + khoa } : {} })
  .then((r) => { KQ.api = 'http:' + r.status; }, (e) => { KQ.api = 'loi:' + e.name; });
Promise.all([cho_ws, cho_api]).then(() => { KQ.xong = true;
  document.getElementById('kq').textContent = JSON.stringify(KQ); });
"""


class MayChuDichVuBackendGia:
    """Máy chủ ASGI giả lập đồng thời: (1) noVNC HTTP + WebSocket và (2) API dịch vụ trình duyệt.

    `bi_mat`: đặt thì MỌI tệp tĩnh và WebSocket đòi Basic auth `w2a:<bi_mat>` như websockify của dịch vụ thật.
    `cho_ws`: đặt (threading.Event) thì WebSocket chờ sự kiện đó TRƯỚC khi chấp nhận — backend chậm.
    """

    def __init__(self, nhan: str = "A"):
        self.nhan = nhan
        self.yeu_cau: list[dict[str, Any]] = []
        self.ws_den: list[dict[str, str]] = []
        self.ho_so_da_mo: dict[str, dict] = {}
        self.bi_mat: str | None = None
        self.cho_ws: threading.Event | None = None
        self.chuyen_toi = ""

    def _ghi(self, request) -> dict:
        y = {"method": request.method, "raw_path": request.scope["raw_path"].decode("latin-1"),
             "query": request.scope["query_string"].decode("latin-1"), "headers": dict(request.headers)}
        self.yeu_cau.append(y)
        return y

    def _sai_auth(self, headers) -> bool:
        return self.bi_mat is not None and headers.get("authorization") != (
            "Basic " + base64.b64encode(f"w2a:{self.bi_mat}".encode()).decode())

    def _tu_choi(self):
        return Response("can xac thuc", status_code=401, headers={"WWW-Authenticate": 'Basic realm="gia"'})

    async def vnc_html(self, request):
        self._ghi(request)
        if self._sai_auth(request.headers):
            return self._tu_choi()
        return Response(TRANG_VNC.replace("Mock Interface", f"Mock Interface {self.nhan}"), media_type="text/html")

    async def app_js(self, request):
        self._ghi(request)
        if self._sai_auth(request.headers):
            return self._tu_choi()
        return Response(APP_JS, media_type="text/javascript")

    async def phu_js(self, request):
        self._ghi(request)
        return Response("export const GIA_TRI = 'phu-ok';", media_type="text/javascript")

    async def doc_ac(self, request):
        """Đích cố cài cookie trùng tên, chuyển hướng, CSP riêng nới lỏng, lộ Server."""
        self._ghi(request)
        return Response("<p>doc ac</p>", media_type="text/html", headers={
            "Set-Cookie": "web2api_novnc=chiem; Path=/", "Location": "http://ac.example/",
            "Content-Security-Policy": "default-src *", "Server": "may-ac/1.0", "X-Thu": "ro-ri",
            "WWW-Authenticate": 'Basic realm="ac"', "Access-Control-Allow-Credentials": "true",
            "ETag": '"e1"', "Last-Modified": "Mon, 12 Oct 2026 00:00:00 GMT"})

    async def nen(self, request):
        """Luôn nén gzip, kể cả khi khách xin identity (máy chủ không tuân thủ)."""
        self._ghi(request)
        return Response(gzip.compress(b"noi-dung-da-giai-nen " * 50), media_type="text/javascript",
                        headers={"Content-Encoding": "gzip"})

    async def chuyen(self, request):
        """Đích trả 302 sang máy khác (bị chiếm, cố dắt lõi đi SSRF)."""
        self._ghi(request)
        return Response("", status_code=302, headers={"Location": self.chuyen_toi})

    async def khong_doi(self, request):
        self._ghi(request)
        return Response(b"", status_code=304, headers={"ETag": '"e1"'})

    async def con_lai(self, request):
        y = self._ghi(request)
        if self._sai_auth(request.headers):
            return self._tu_choi()
        return Response(f"ok:{y['raw_path']}?{y['query']}", media_type="text/plain")

    async def ws_websockify(self, websocket: WebSocket):
        self.ws_den.append(dict(websocket.headers))
        if self._sai_auth(websocket.headers):
            await websocket.close(code=1008)
            return
        if self.cho_ws is not None:
            while not self.cho_ws.is_set():
                await asyncio.sleep(0.02)
        sub = websocket.scope.get("subprotocols") or []
        await websocket.accept(subprotocol=sub[0] if sub else None)
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.receive":
                    if msg.get("bytes") is not None:
                        await websocket.send_bytes(b"echo:" + msg["bytes"])
                    elif msg.get("text") is not None:
                        await websocket.send_text("echo:" + msg["text"])
                elif msg["type"] == "websocket.disconnect":
                    break
        except Exception:
            pass

    # API dịch vụ trình duyệt
    async def api_danh_sach(self, request):
        if request.headers.get("authorization", "") != f"Bearer {KHOA_DICH_VU_GIA}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse(list(self.ho_so_da_mo.values()))

    async def api_mo(self, request):
        if request.headers.get("authorization", "") != f"Bearer {KHOA_DICH_VU_GIA}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        than = await request.json()
        ho_so = than.get("ho_so", "default")
        item = {"ho_so": ho_so, "loai": than.get("loai", "chromium"),
                "dich": request.base_url._url.rstrip("/"), "trang_thai": "dang_chay"}
        self.ho_so_da_mo[ho_so] = item
        return JSONResponse(item)

    async def api_dong(self, request):
        if request.headers.get("authorization", "") != f"Bearer {KHOA_DICH_VU_GIA}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        ho_so = request.path_params.get("ho_so", "")
        than = await request.json()
        self.ho_so_da_mo.pop(ho_so, None)
        return JSONResponse({"ok": True, "giu_phien": than.get("giu_phien", False)})

    def build_app(self):
        return Starlette(routes=[
            Route("/vnc.html", self.vnc_html),
            Route("/app.js", self.app_js),
            Route("/phu.js", self.phu_js),
            Route("/doc-ac.html", self.doc_ac),
            Route("/nen.js", self.nen),
            Route("/chuyen", self.chuyen),
            Route("/khong-doi.js", self.khong_doi),
            WebSocketRoute("/websockify", self.ws_websockify),
            Route("/ho-so", self.api_danh_sach, methods=["GET"]),
            Route("/ho-so/mo", self.api_mo, methods=["POST"]),
            Route("/ho-so/{ho_so}/dong", self.api_dong, methods=["POST"]),
            Route("/{duong:path}", self.con_lai, methods=["GET", "HEAD", "POST", "PUT", "DELETE"]),
        ])


@pytest.fixture
def backend_gia():
    bg = MayChuDichVuBackendGia("A")
    with MayChuThat(bg.build_app()) as s:
        bg.url = s.url
        yield bg


@pytest.fixture
def backend_gia2():
    bg = MayChuDichVuBackendGia("B")
    with MayChuThat(bg.build_app()) as s:
        bg.url = s.url
        yield bg


class ModuleTrinhDuyetGia:
    """`dich` trả về có thể mang userinfo (hợp đồng dịch vụ mới): đặt `dich` tuỳ ý."""

    def __init__(self, dich: str):
        self.manifest = {"id": "gia-browser", "loai": "tien_ich", "cung_cap": ["trinh_duyet"]}
        self.dich = dich
        self.cac_ho_so: dict[str, dict] = {}
        self.dong_da_goi: list[dict] = []
        self.loi_dong: Exception | None = None

    async def trinh_duyet_mo(self, ho_so: str, loai: str) -> dict[str, Any]:
        res = {"ho_so": ho_so, "loai": loai, "dich": self.dich}
        self.cac_ho_so[ho_so] = res
        return res

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool) -> dict[str, Any]:
        self.dong_da_goi.append({"ho_so": ho_so, "giu_phien": giu_phien})
        if self.loi_dong is not None:
            raise self.loi_dong
        self.cac_ho_so.pop(ho_so, None)
        return {"ok": True}

    async def trinh_duyet_ds(self) -> list[dict[str, Any]]:
        # Dịch vụ thật có thể trả kèm `dich` (có bí mật) — lõi phải lọc theo hợp đồng.
        return [{"ho_so": k, "loai": v["loai"], "trang_thai": "dang_chay", "dich": v["dich"]}
                for k, v in self.cac_ho_so.items()]

    async def models(self) -> list[dict]:
        return []

    async def suc_khoe(self) -> dict:
        return {"ok": True}

    async def dung(self) -> None:
        pass


def _cai(client_or_app, dich: str, ten: str = "gia-browser") -> ModuleTrinhDuyetGia:
    so = client_or_app.app.state.so if hasattr(client_or_app, "app") else client_or_app.state.so
    mod = ModuleTrinhDuyetGia(dich)
    so.dang_chay[ten] = mod
    return mod


def _mo(client, ten: str = "gia-browser", **them) -> tuple[str, str]:
    r = client.post(f"/api/trinh-duyet/{ten}/mo", json={"loai": "chromium", **them}, headers=QT)
    assert r.status_code == 200, r.text
    return r.json()["ho_so"], r.json()["ve"]


def _vao(client, ve: str) -> str:
    """Đổi vé (POST) → trả phiên trong đường dẫn."""
    r = client.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
    assert r.status_code == 303, r.text
    m = MAU_PHIEN.match(r.headers["location"])
    assert m, r.headers["location"]
    return m.group(1)


HEADER_BAO_VE = {
    "content-security-policy": "sandbox allow-scripts allow-forms allow-pointer-lock",
    "access-control-allow-origin": "*",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
}


def _co_header_bao_ve(r) -> None:
    for k, v in HEADER_BAO_VE.items():
        assert r.headers.get_list(k) == [v], f"{k}: {r.headers.get_list(k)} (mã {r.status_code})"
    assert "allow-same-origin" not in r.headers["content-security-policy"]


# ───────────────────────────── #5/#7: vé, trang nút, POST ─────────────────────────────

def test_ve_va_xac_thuc_quyen(du_lieu, backend_gia):
    """Vé: GET không tiêu thụ; POST tiêu thụ đúng một lần; sai/hết hạn 401; khách 403; không khoá 401."""
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        assert client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}).status_code == 401

        rk = client.post("/api/khoa-khach", json={"nhan": "Khách Test", "pham_vi": ["chat"]}, headers=QT)
        khoa_k = rk.json()["khoa"]["khoa"]
        hk = {"Authorization": f"Bearer {khoa_k}"}
        assert client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}, headers=hk).status_code == 403

        r_mo = client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}, headers=QT)
        data = r_mo.json()
        ho_so, ve = data["ho_so"], data["ve"]
        assert re.match(r"^[a-f0-9]{16,64}$", ho_so)
        assert data["url"] == f"/novnc/dang-nhap?ve={ve}"
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=hk).status_code == 403

        # GET (bot xem trước liên kết) nhiều lần KHÔNG tiêu thụ vé
        for _ in range(3):
            r_get = client.get(data["url"], follow_redirects=False)
            assert r_get.status_code == 200
            assert "set-cookie" not in r_get.headers
        r1 = client.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
        assert r1.status_code == 303
        assert "set-cookie" not in r1.headers, "không còn cookie phiên"
        assert MAU_PHIEN.match(r1.headers["location"])

        r2 = client.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
        assert r2.status_code == 401
        assert "Vé không hợp lệ hoặc đã hết hạn" in r2.json()["error"]["message"]
        r_sai = client.post("/novnc/dang-nhap", data={"ve": "ve_hoan_toan_sai_123"}, follow_redirects=False)
        assert r_sai.status_code == 401
        assert client.post("/novnc/dang-nhap", follow_redirects=False).status_code == 415  # không Content-Type
        assert client.post("/novnc/dang-nhap", content=b"", follow_redirects=False,
                           headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 401

        ve_moi = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=QT).json()["ve"]
        novnc._VE[hashlib.sha256(ve_moi.encode()).hexdigest()]["het_han"] = time.time() - 10
        assert client.post("/novnc/dang-nhap", data={"ve": ve_moi}, follow_redirects=False).status_code == 401


def test_get_dang_nhap_la_trang_nut_khong_phan_xa_xss(du_lieu, backend_gia):
    """GET /novnc/dang-nhap: trang nhỏ có form POST + nút; không script; vé lạ không phản xạ vào HTML."""
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        r = client.get(f"/novnc/dang-nhap?ve={ve}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert re.search(r'<form[^>]+method="post"[^>]+action="/novnc/dang-nhap"', r.text)
        assert f'value="{ve}"' in r.text and "<button" in r.text and "<script" not in r.text
        csp = r.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "form-action 'self'" in csp and "frame-ancestors 'none'" in csp
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-frame-options"] == "DENY"
        assert len(novnc._VE) == 1 and not novnc._PHIEN

        doc = '"><script>alert(1)</script>'
        r_xss = client.get("/novnc/dang-nhap", params={"ve": doc})
        assert "<script>alert(1)" not in r_xss.text
        assert r_xss.status_code == 400


def test_32_post_dong_thoi_cung_mot_ve_chi_mot_phien(du_lieu, backend_gia):
    """32 POST đồng thời cùng một vé (uvicorn thật, nhiều luồng) → đúng một 303, còn lại 401."""
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        _cai(app, backend_gia.url)
        _, ve = _mo(h)
        rao = threading.Barrier(32)

        def mot_lan(_):
            with httpx.Client(base_url=w.url, timeout=15) as hh:
                rao.wait()
                return hh.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False).status_code

        with ThreadPoolExecutor(32) as ex:
            ma = list(ex.map(mot_lan, range(32)))
        assert ma.count(303) == 1, ma
        assert ma.count(401) == 31, ma
        assert len(novnc._PHIEN) == 1


# ───────────────────────────── #1: phiên trong đường dẫn, cô lập ─────────────────────────────

def test_phien_trong_duong_dan_co_lap_ho_so(du_lieu, backend_gia, backend_gia2):
    """(module, hồ sơ) suy ra từ phiên: phiên A chỉ thấy đích A; phiên lạ 401; đường cũ có hồ sơ trong URL đã bỏ."""
    with TestClient(app) as client:
        _cai(client, backend_gia.url, "mod-a")
        _cai(client, backend_gia2.url, "mod-b")
        ho_so_a, ve_a = _mo(client, "mod-a")
        ho_so_b, ve_b = _mo(client, "mod-b")
        pa, pb = _vao(client, ve_a), _vao(client, ve_b)
        assert len(pa) >= 43 and pa != pb  # token_urlsafe(32) = 256 bit

        assert "Mock Interface A" in client.get(f"/novnc/s/{pa}/vnc.html").text
        assert "Mock Interface B" in client.get(f"/novnc/s/{pb}/vnc.html").text
        assert client.get(f"/novnc/s/{'x' * 43}/vnc.html").status_code == 401
        assert client.get(f"/novnc/s/{pa[:-1]}/vnc.html").status_code == 401
        # Không còn đường mà khách tự chọn hồ sơ
        r_cu = client.get(f"/novnc/mod-b/{ho_so_b}/vnc.html")
        assert r_cu.status_code in (401, 404)
        assert "Mock Interface" not in r_cu.text
        # Phiên noVNC không thay được Bearer ở /api
        assert client.get("/api/phien", headers={"Authorization": f"Bearer {pa}"}).status_code == 401
        # Sổ phiên tra theo băm: không lưu phiên thô
        assert pa not in novnc._PHIEN and hashlib.sha256(pa.encode()).hexdigest() in novnc._PHIEN


def test_moi_phan_hoi_duoi_novnc_s_co_header_bao_ve(du_lieu, backend_gia):
    """CSP sandbox + ACAO * + nosniff + no-referrer + no-store + XFO DENY trên 200, 400, 401, 404, 405, 502."""
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        r200 = client.get(f"/novnc/s/{p}/vnc.html")
        assert r200.status_code == 200
        _co_header_bao_ve(r200)
        r400 = client.get(f"/novnc/s/{p}/core//rfb.js")
        assert r400.status_code == 400
        _co_header_bao_ve(r400)
        r401 = client.get(f"/novnc/s/{'y' * 43}/vnc.html")
        assert r401.status_code == 401
        _co_header_bao_ve(r401)
        r404 = client.get("/novnc/s/")
        assert r404.status_code == 404
        _co_header_bao_ve(r404)
        r405 = client.post(f"/novnc/s/{p}/vnc.html")
        assert r405.status_code == 405
        _co_header_bao_ve(r405)

    # 502: đích chết (mở hồ sơ trỏ vào cổng không ai nghe)
    with TestClient(app) as client:
        _cai(client, "http://127.0.0.1:9")
        _, ve = _mo(client)
        p = _vao(client, ve)
        r502 = client.get(f"/novnc/s/{p}/vnc.html")
        assert r502.status_code == 502
        _co_header_bao_ve(r502)


# ───────────────────────────── #6: header hai chiều theo danh sách cho phép ─────────────────────────────

def test_header_hai_chieu_danh_sach_cho_phep(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        backend_gia.yeu_cau.clear()
        r = client.get(f"/novnc/s/{p}/doc-ac.html", headers={
            "Authorization": f"Bearer {KHOA_QT}", "Cookie": "web2api_novnc=abc; khac=1", "Origin": "http://ac.example",
            "Referer": f"http://web2api/novnc/s/{p}/vnc.html", "X-Forwarded-For": "1.2.3.4",
            "X-Forwarded-Host": "ac.example", "X-Api-Key": "k", "Proxy-Authorization": "Basic eDp5",
            "X-Custom": "khong-chuyen", "Accept-Language": "vi", "If-None-Match": '"e0"', "Range": "bytes=0-10",
            "User-Agent": "trinh-duyet-test", "Accept-Encoding": "gzip, br"})
        assert r.status_code == 200
        gui = backend_gia.yeu_cau[-1]["headers"]
        assert set(gui) <= {"host", "accept", "accept-language", "if-none-match", "if-modified-since", "range",
                            "user-agent", "accept-encoding", "connection", "content-length"}, gui
        assert gui["accept-language"] == "vi" and gui["range"] == "bytes=0-10" and gui["user-agent"] == "trinh-duyet-test"
        assert gui["accept-encoding"] == "identity"
        assert p not in json.dumps(gui)

        # Phản hồi: chỉ content-type, etag, last-modified (+ header bảo vệ của lõi)
        assert "set-cookie" not in r.headers and "location" not in r.headers and "server" not in r.headers
        assert "x-thu" not in r.headers and "www-authenticate" not in r.headers
        assert "access-control-allow-credentials" not in r.headers
        assert r.headers["etag"] == '"e1"' and r.headers["last-modified"].startswith("Mon, 12 Oct 2026")
        _co_header_bao_ve(r)


# ───────────────────────────── #8: ghép đường dẫn ─────────────────────────────

def test_lam_sach_duong_theo_doan():
    ok = {"vnc.html": "vnc.html", "core/rfb.js": "core/rfb.js", "a..b": "a..b", "a?b=c": "a%3Fb%3Dc",
          "#frag": "%23frag", "%2e%2e": "%252e%252e", "thu muc/": "thu%20muc/", "": ""}
    for vao, ra in ok.items():
        assert novnc.lam_sach_duong_proxy(vao) == ra, vao
    for xau in ("..", "../x", "a/../b", ".", "a/./b", "a//b", "/a", "a\\b", "a\x00b", "a\nb", "a\x7fb"):
        with pytest.raises(novnc.LoiOpenAI):
            novnc.lam_sach_duong_proxy(xau)


def test_duong_va_query_toi_dich(du_lieu, backend_gia):
    # uvicorn thật: TestClient giải mã đường dẫn HAI lần (httpx .path đã giải mã rồi lại unquote) nên che mất ca %252e.
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as client:
        _cai(app, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)

        def toi(duong: str):
            backend_gia.yeu_cau.clear()
            r = client.get(f"/novnc/s/{p}/{duong}")
            return r, (backend_gia.yeu_cau[-1] if backend_gia.yeu_cau else None)

        r, y = toi("a%3fb=c")
        assert r.status_code == 200 and y["raw_path"] == "/a%3Fb%3Dc" and y["query"] == ""
        r, y = toi("%23frag")
        assert r.status_code == 200 and y["raw_path"] == "/%23frag"
        r, y = toi("a..b")
        assert r.status_code == 200 and y["raw_path"] == "/a..b"
        r, y = toi("%252e%252e")
        assert r.status_code == 200 and y["raw_path"] == "/%252e%252e"
        r, y = toi("x.js?a=1&b=c%26d")
        assert r.status_code == 200 and y["raw_path"] == "/x.js"
        assert dict(httpx.QueryParams(y["query"])) == {"a": "1", "b": "c&d"}
        for xau in ("a%5Cb", "%2e%2e/x", "%2E%2E", "core//rfb.js", "a/%2e/b", "a%00b"):
            r, y = toi(xau)
            assert r.status_code == 400, xau
            assert y is None, f"{xau} lọt tới đích: {y}"


# ───────────────────────────── #4: chỉ GET/HEAD, ghim đích ─────────────────────────────

def test_proxy_chi_get_va_head(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        backend_gia.yeu_cau.clear()
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            r = client.request(m, f"/novnc/s/{p}/thu.txt", content=b"x")
            assert r.status_code == 405, m
        assert backend_gia.yeu_cau == []
        r_head = client.head(f"/novnc/s/{p}/thu.txt")
        assert r_head.status_code == 200
        assert backend_gia.yeu_cau[-1]["method"] == "HEAD"


def test_dich_ghim_ip_luc_mo_va_khong_doi_host(du_lieu, backend_gia, monkeypatch):
    """Phân giải MỘT lần lúc `mo`, kiểm mọi địa chỉ; sau đó DNS đổi (rebinding) cũng không đổi nơi kết nối."""
    cong = backend_gia.url.rsplit(":", 1)[1]
    bang = {"may-dich.lan": ["127.0.0.1"], "may-khac.lan": ["127.0.0.1"]}

    async def phan_giai_gia(ten: str, cong_: int) -> list[str]:
        return list(bang[ten])

    monkeypatch.setattr(novnc, "_phan_giai", phan_giai_gia)
    with TestClient(app) as client:
        mod = _cai(client, f"http://may-dich.lan:{cong}")
        ho_so, ve = _mo(client)
        p = _vao(client, ve)
        # DNS bị đổi sang metadata đám mây: proxy vẫn tới đúng IP đã kiểm, Host giữ tên gốc
        bang["may-dich.lan"] = ["169.254.169.254"]
        backend_gia.yeu_cau.clear()
        r = client.get(f"/novnc/s/{p}/vnc.html")
        assert r.status_code == 200
        assert backend_gia.yeu_cau[-1]["headers"]["host"] == f"may-dich.lan:{cong}"
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"ghim")
            assert ws.receive_bytes() == b"echo:ghim"
        assert backend_gia.ws_den[-1]["host"] == f"may-dich.lan:{cong}"

        # Mở lại cùng hồ sơ: tên nay trỏ dải bị chặn → 502
        assert client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium", "ho_so": ho_so},
                           headers=QT).status_code == 502
        # Một trong các địa chỉ bị chặn cũng đủ từ chối
        bang["may-dich.lan"] = ["127.0.0.1", "169.254.169.254"]
        assert client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium", "ho_so": ho_so},
                           headers=QT).status_code == 502
        # Mở lại cùng hồ sơ mà đích đổi sang máy khác → 502, đích ghim cũ giữ nguyên
        bang["may-dich.lan"] = ["127.0.0.1"]
        mod.dich = f"http://may-khac.lan:{cong}"
        r_doi = client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium", "ho_so": ho_so}, headers=QT)
        assert r_doi.status_code == 502
        assert "may-khac" not in r_doi.text
        assert client.get(f"/novnc/s/{p}/vnc.html").status_code == 200
        # Cùng máy, bí mật mới (dịch vụ khởi động lại) → được
        mod.dich = f"http://w2a:moi@may-dich.lan:{cong}"
        assert client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium", "ho_so": ho_so},
                           headers=QT).status_code == 200


def test_chan_ssrf_khi_tat_co_loopback(du_lieu, monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "0")
        for dich in ("http://127.0.0.1:9999", "http://w2a:x@127.0.0.1:9999", "http://169.254.169.254/",
                     "ftp://10.0.0.1/", "http://[::1]:80"):
            _cai(client, dich, "ssrf-mod")
            r = client.post("/api/trinh-duyet/ssrf-mod/mo", json={"loai": "chromium"}, headers=QT)
            assert r.status_code == 502, dich
            assert "w2a:x" not in r.text
        assert not novnc._HO_SO and not novnc._VE


# ───────────────────────────── Tương thích dịch vụ: userinfo → Basic auth ─────────────────────────────

def test_basic_auth_toi_dich_va_khong_lo_bi_mat(du_lieu, backend_gia, caplog):
    caplog.set_level(logging.DEBUG)
    backend_gia.bi_mat = BI_MAT_DICH_GIA
    host = backend_gia.url.removeprefix("http://")
    van_ban: list[str] = []
    with TestClient(app) as client:
        mod = _cai(client, f"http://w2a:{BI_MAT_DICH_GIA}@{host}")
        r_mo = client.post("/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}, headers=QT)
        van_ban.append(r_mo.text)
        ho_so, ve = r_mo.json()["ho_so"], r_mo.json()["ve"]
        p = _vao(client, ve)
        r = client.get(f"/novnc/s/{p}/vnc.html")
        van_ban += [r.text, json.dumps(dict(r.headers))]
        assert r.status_code == 200
        assert backend_gia.yeu_cau[-1]["headers"]["authorization"] == BASIC_DUNG
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"auth")
            assert ws.receive_bytes() == b"echo:auth"
        assert backend_gia.ws_den[-1]["authorization"] == BASIC_DUNG

        r_ds = client.get("/api/trinh-duyet/gia-browser", headers=QT)
        van_ban.append(r_ds.text)
        assert r_ds.status_code == 200 and r_ds.json() == [{"ho_so": ho_so, "loai": "chromium", "trang_thai": "dang_chay"}]

        # Bí mật sai (dịch vụ đổi bí mật): backend 401 → proxy 502 chung, không hỏi mật khẩu trình duyệt
        backend_gia.bi_mat = "bi-mat-khac"
        r_sai = client.get(f"/novnc/s/{p}/vnc.html")
        van_ban += [r_sai.text, json.dumps(dict(r_sai.headers))]
        assert r_sai.status_code == 502 and "www-authenticate" not in r_sai.headers
        with pytest.raises(WebSocketDisconnect) as loi_ws:
            with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
                ws.receive_bytes()
        assert loi_ws.value.code == 1011  # không nối được đích: từ chối trước khi chấp nhận
        r_dong = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/dong", headers=QT)
        van_ban.append(r_dong.text)
        mod.loi_dong = RuntimeError(f"dich vu loi http://w2a:{BI_MAT_DICH_GIA}@{host}")
        _, ve2 = _mo(client)
        r_dong2 = client.post(f"/api/trinh-duyet/gia-browser/{next(iter(mod.cac_ho_so))}/dong", headers=QT)
        van_ban.append(r_dong2.text)

    van_ban.append(json.dumps(nhat_ky.doc(kenh="trinh_duyet", gioi_han=500), ensure_ascii=False))
    # Log PHÍA LÕI (web2api.*, uvicorn.*): không phiên, không vé. Log của httpx phía TEST (khách gọi testserver) thì
    # đương nhiên có URL khách gọi — chỉ quét bí mật của đích trong đó.
    log_loi = [rec.getMessage() for rec in caplog.records if rec.name.startswith(("web2api", "uvicorn"))]
    log_tat_ca = [rec.getMessage() for rec in caplog.records]
    tat_ca = "\n".join(van_ban + log_tat_ca)
    assert BI_MAT_DICH_GIA not in tat_ca
    assert BASIC_DUNG.split()[1] not in tat_ca
    phia_loi = "\n".join(van_ban[1:] + log_loi)
    assert p not in phia_loi and ve not in phia_loi


# ───────────────────────────── #2: đóng khi module lỗi vẫn thu hồi ─────────────────────────────

def test_dong_khi_module_loi_van_thu_hoi(du_lieu, backend_gia):
    with TestClient(app) as client:
        mod = _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        p = _vao(client, ve)
        ve_cho = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=QT).json()["ve"]
        mod.loi_dong = RuntimeError("chi-tiet-noi-bo-KHONG-DUOC-LO")
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"truoc")
            assert ws.receive_bytes() == b"echo:truoc"
            r = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/dong", headers=QT)
            assert r.status_code == 502
            assert r.json()["da_thu_hoi"] is True
            assert "chi-tiet-noi-bo" not in r.text and "RuntimeError" in r.text
            with pytest.raises(WebSocketDisconnect) as dong_ws:
                ws.receive_bytes()
            assert dong_ws.value.code == 1008  # thu hồi, không phải kết thúc bình thường 1000
        assert client.get(f"/novnc/s/{p}/vnc.html").status_code == 401
        assert client.post("/novnc/dang-nhap", data={"ve": ve_cho}, follow_redirects=False).status_code == 401
        assert not novnc._PHIEN and not novnc._VE and not novnc._HO_SO and not novnc._WS
        dong = [d for d in nhat_ky.doc(kenh="trinh_duyet", gioi_han=100) if "dong" in json.dumps(d)]
        assert dong and "chi-tiet-noi-bo" not in json.dumps(dong, ensure_ascii=False)


def test_loi_module_khong_nhung_nguyen_van(du_lieu):
    class ModNem(ModuleTrinhDuyetGia):
        async def trinh_duyet_mo(self, ho_so, loai):
            raise ValueError("bi-mat-trong-loi-mo")

        async def trinh_duyet_ds(self):
            raise KeyError("bi-mat-trong-loi-ds")

    with TestClient(app) as client:
        client.app.state.so.dang_chay["mod-nem"] = ModNem("http://127.0.0.1:9")
        r_mo = client.post("/api/trinh-duyet/mod-nem/mo", json={"loai": "chromium"}, headers=QT)
        r_ds = client.get("/api/trinh-duyet/mod-nem", headers=QT)
        assert r_mo.status_code == 502 and r_ds.status_code == 502
        assert "bi-mat-trong-loi" not in r_mo.text + r_ds.text
        assert "ValueError" in r_mo.text and "KeyError" in r_ds.text


# ───────────────────────────── #3: WebSocket không sống sót ─────────────────────────────

def test_ws_dong_ho_so_giua_luc_bat_tay(du_lieu, backend_gia):
    """`dong` chen giữa lúc lõi đang nối backend (backend chậm) → socket không được sống."""
    from websockets.sync.client import connect as ws_connect

    backend_gia.cho_ws = threading.Event()
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        _cai(app, backend_gia.url)
        ho_so, ve = _mo(h)
        r = h.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
        p = MAU_PHIEN.match(r.headers["location"]).group(1)
        kq: dict[str, Any] = {}

        def noi():
            try:
                with ws_connect(f"{w.url.replace('http', 'ws')}/novnc/s/{p}/websockify", proxy=None,
                                open_timeout=10) as ws:
                    ws.send(b"x")
                    kq["nhan"] = ws.recv(timeout=3)
            except Exception as e:  # noqa: BLE001 - mong đợi bị từ chối/đóng
                kq["loi"] = type(e).__name__

        t = threading.Thread(target=noi)
        t.start()
        han = time.monotonic() + 5
        while not backend_gia.ws_den and time.monotonic() < han:
            time.sleep(0.02)
        assert backend_gia.ws_den, "lõi chưa nối backend"
        assert novnc._WS, "socket phải được đăng ký NGAY khi nhận, trước khi backend chấp nhận"
        assert h.post(f"/api/trinh-duyet/gia-browser/{ho_so}/dong", headers=QT).status_code == 200
        backend_gia.cho_ws.set()
        t.join(10)
        assert "nhan" not in kq, kq
        # Kiểm lại phiên SAU khi nối backend → từ chối ngay ở bắt tay (403), không chấp nhận rồi mới đóng.
        assert kq.get("loi") == "InvalidStatus", kq
        han = time.monotonic() + 3
        while novnc._WS and time.monotonic() < han:
            time.sleep(0.02)
        assert not novnc._WS


def test_ws_dong_khi_phien_het_han(du_lieu, backend_gia, monkeypatch):
    """Phiên hết hạn (đồng hồ giả +9 giờ) → socket đang chạy bị đóng, phiên bị dọn."""
    monkeypatch.setattr(novnc, "CHU_KY_KIEM_WS_GIAY", 0.05)
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"1")
            assert ws.receive_bytes() == b"echo:1"
            goc = time.time
            monkeypatch.setattr(novnc, "_bay_gio", lambda: goc() + 9 * 3600)
            with pytest.raises(WebSocketDisconnect) as dong_ws:
                ws.receive_bytes()
            assert dong_ws.value.code == 1008  # thu hồi, không phải kết thúc bình thường 1000
        assert not novnc._PHIEN and not novnc._WS


def test_ws_don_phien_het_han_dong_socket(du_lieu, backend_gia, monkeypatch):
    """Phiên hết hạn bị DỌN bởi thao tác khác (đổi vé mới) → socket của nó cũng bị đóng ngay."""
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        p = _vao(client, ve)
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"1")
            assert ws.receive_bytes() == b"echo:1"
            novnc._PHIEN[hashlib.sha256(p.encode()).hexdigest()]["het_han"] = time.time() - 1
            ve2 = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=QT).json()["ve"]
            _vao(client, ve2)
            with pytest.raises(WebSocketDisconnect) as dong_ws:
                ws.receive_bytes()
            assert dong_ws.value.code == 1008  # thu hồi, không phải kết thúc bình thường 1000
        assert len(novnc._PHIEN) == 1 and not novnc._WS


def test_ws_khung_lon_hon_1mib(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        lon = b"\x07" * (3 * 1024 * 1024)
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(lon)
            assert ws.receive_bytes() == b"echo:" + lon


def test_ws_thieu_phien_bi_tu_choi_va_thu_hoi_dong_socket(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        with pytest.raises(WebSocketDisconnect) as tu_choi:
            with client.websocket_connect(f"/novnc/s/{'z' * 43}/websockify") as ws:
                ws.receive_bytes()
        assert tu_choi.value.code == 1008
        p = _vao(client, ve)
        with client.websocket_connect(f"/novnc/s/{p}/websockify", headers={
                "Cookie": "web2api_novnc=abc", "Origin": "http://ac.example", "Authorization": f"Bearer {KHOA_QT}",
                "X-Forwarded-For": "1.2.3.4", "X-Api-Key": "k", "Referer": f"http://web2api/novnc/s/{p}/"}) as ws:
            ws.send_bytes(b"ping-c6")
            assert ws.receive_bytes() == b"echo:ping-c6"
            bat_tay = backend_gia.ws_den[-1]
            for ten in ("cookie", "origin", "authorization", "x-forwarded-for", "x-api-key", "referer"):
                assert ten not in bat_tay, (ten, bat_tay)
            assert p not in json.dumps(bat_tay)
            assert client.delete("/api/trinh-duyet/phien", headers=QT).json() == {"ok": True, "so_phien": 1}
            with pytest.raises(WebSocketDisconnect) as dong_ws:
                ws.receive_bytes()
            assert dong_ws.value.code == 1008  # thu hồi, không phải kết thúc bình thường 1000


# ───────────────────────────── #9: trần phiên ─────────────────────────────

def test_tran_phien_theo_ho_so_va_toan_cuc_429(du_lieu, backend_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_NOVNC_PHIEN_TOI_DA", "6")
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        phien = [_vao(client, ve)]
        for _ in range(novnc.TRAN_PHIEN_MOI_HO_SO - 1):
            phien.append(_vao(client, client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=QT).json()["ve"]))
        ve_thua = client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/ve", headers=QT).json()["ve"]
        r = client.post("/novnc/dang-nhap", data={"ve": ve_thua}, follow_redirects=False)
        assert r.status_code == 429
        for p in phien:  # không phiên nào bị đẩy ra
            assert client.get(f"/novnc/s/{p}/vnc.html").status_code == 200

        # Trần toàn cục (6): hồ sơ thứ hai được 2 phiên, phiên thứ 7 bị 429; 6 phiên cũ vẫn dùng được
        ho_so2, ve2 = _mo(client)
        phien.append(_vao(client, ve2))
        phien.append(_vao(client, client.post(f"/api/trinh-duyet/gia-browser/{ho_so2}/ve", headers=QT).json()["ve"]))
        ve3 = client.post(f"/api/trinh-duyet/gia-browser/{ho_so2}/ve", headers=QT).json()["ve"]
        assert client.post("/novnc/dang-nhap", data={"ve": ve3}, follow_redirects=False).status_code == 429
        for p in phien:
            assert client.get(f"/novnc/s/{p}/vnc.html").status_code == 200
        # 429 không đốt vé: thu hồi bớt rồi dùng lại vé đó được
        client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/dong", headers=QT)
        assert client.post("/novnc/dang-nhap", data={"ve": ve3}, follow_redirects=False).status_code == 303


# ───────────────────────────── #10: chức năng ─────────────────────────────

def test_proxy_giai_nen_than(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        r = client.get(f"/novnc/s/{p}/nen.js", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert "content-encoding" not in r.headers
        assert r.content == b"noi-dung-da-giai-nen " * 50


def test_dong_mac_dinh_giu_phien(du_lieu, backend_gia):
    with TestClient(app) as client:
        mod = _cai(client, backend_gia.url)
        ho_so1, _ = _mo(client)
        r = client.post(f"/api/trinh-duyet/gia-browser/{ho_so1}/dong", headers=QT)  # không thân
        assert r.status_code == 200 and r.json()["da_thu_hoi"] is True
        assert mod.dong_da_goi[-1] == {"ho_so": ho_so1, "giu_phien": True}
        ho_so2, _ = _mo(client)
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so2}/dong", json={}, headers=QT).status_code == 200
        assert mod.dong_da_goi[-1] == {"ho_so": ho_so2, "giu_phien": True}
        ho_so3, _ = _mo(client)
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so3}/dong", json={"giu_phien": "false"},
                           headers=QT).status_code == 400
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so3}/dong", json={"giu_phien": False},
                           headers=QT).status_code == 200
        assert mod.dong_da_goi[-1] == {"ho_so": ho_so3, "giu_phien": False}
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so3}/ve", headers=QT).status_code == 404


# ───────────────────────────── #1c: không ghi phiên vào log ─────────────────────────────

class _GomLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.dong: list[str] = []

    def emit(self, record):
        self.dong.append(record.getMessage())


def test_log_uvicorn_khong_ghi_phien_va_ve():
    gom = _GomLog()
    lg = logging.getLogger("uvicorn.error")
    cu = lg.level
    lg.addHandler(gom)
    lg.setLevel(logging.INFO)
    try:
        phien = "P" * 43
        lg.info('%s - "WebSocket %s" [accepted]', "1.2.3.4:5", f"/novnc/s/{phien}/websockify")
        logging.getLogger("uvicorn.access").addHandler(gom)
        logging.getLogger("uvicorn.access").warning(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", f"/novnc/s/{phien}/vnc.html?path=websockify", "1.1", 200)
        logging.getLogger("uvicorn.access").warning(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/novnc/dang-nhap?ve=VEBIMAT123&x=1", "1.1", 200)
    finally:
        lg.removeHandler(gom)
        logging.getLogger("uvicorn.access").removeHandler(gom)
        lg.setLevel(cu)
    assert gom.dong and all(phien not in d and "VEBIMAT123" not in d for d in gom.dong), gom.dong
    assert '"WebSocket /novnc/s/…/websockify"' in gom.dong[0]


def test_log_uvicorn_that_khi_noi_ws(du_lieu, backend_gia):
    """uvicorn ghi dòng «WebSocket <đường>» ở mức INFO kể cả khi --no-access-log: phải không có phiên."""
    from websockets.sync.client import connect as ws_connect

    gom = _GomLog()
    lg = logging.getLogger("uvicorn.error")
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        cu = lg.level
        lg.addHandler(gom)
        lg.setLevel(logging.INFO)
        try:
            _cai(app, backend_gia.url)
            _, ve = _mo(h)
            r = h.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
            p = MAU_PHIEN.match(r.headers["location"]).group(1)
            with ws_connect(f"{w.url.replace('http', 'ws')}/novnc/s/{p}/websockify", proxy=None) as ws:
                ws.send(b"a")
                assert ws.recv(timeout=5) == b"echo:a"
        finally:
            lg.removeHandler(gom)
            lg.setLevel(cu)
    assert any("WebSocket /novnc/s/…/websockify" in d for d in gom.dong), gom.dong
    assert all(p not in d for d in gom.dong)


# ───────────────────────────── Hợp đồng, module, grep ─────────────────────────────

def test_khong_bi_mat_nao_vao_nhat_ky(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        client.get(f"/novnc/dang-nhap?ve={ve}")
        p = _vao(client, ve)
        client.get(f"/novnc/s/{p}/vnc.html")
        client.post(f"/api/trinh-duyet/gia-browser/{ho_so}/dong", json={"giu_phien": False}, headers=QT)
        van_ban_log = json.dumps(nhat_ky.doc(kenh="trinh_duyet", gioi_han=100), ensure_ascii=False)
        assert ve not in van_ban_log and p not in van_ban_log
        assert KHOA_QT not in van_ban_log and KHOA_DICH_VU_GIA not in van_ban_log


def test_hop_dong_va_loi_hinh_dang(du_lieu, backend_gia):
    with TestClient(app) as client:
        class ModRong:
            manifest = {"id": "mod-rong", "cung_cap": []}

            async def models(self):
                return []

            async def dung(self):
                pass

        client.app.state.so.dang_chay["mod-rong"] = ModRong()
        assert client.post("/api/trinh-duyet/mod-rong/mo", json={"loai": "chromium"}, headers=QT).status_code == 501

        class ModSaiHinh(ModRong):
            manifest = {"id": "mod-sai", "cung_cap": ["trinh_duyet"]}

            async def trinh_duyet_mo(self, ho_so, loai):
                return {"ho_so": ho_so}  # thiếu 'dich'

        client.app.state.so.dang_chay["mod-sai"] = ModSaiHinh()
        assert client.post("/api/trinh-duyet/mod-sai/mo", json={"loai": "chromium"}, headers=QT).status_code == 502
        assert client.post("/api/trinh-duyet/mod-sai/mo", json={"loai": "chromium", "ho_so": "sai-mau-khong-phai-hex!"},
                           headers=QT).status_code == 400
        assert client.post("/api/trinh-duyet/khong-ton-tai/mo", json={"loai": "chromium"}, headers=QT).status_code == 404


def test_module_trinh_duyet_chuan(du_lieu, backend_gia):
    """Module vnc-xa với dịch vụ giả: cai(), mở, đóng, liệt kê; phân loại lỗi."""
    import importlib.util

    mod_tep = Path(__file__).resolve().parents[1] / "modules" / "vnc-xa" / "module.py"
    spec = importlib.util.spec_from_file_location("mod_trinh_duyet_test", mod_tep)
    mod_obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod_obj)
    manifest = json.loads((mod_tep.parent / "manifest.json").read_text(encoding="utf-8"))
    m = mod_obj.Module(manifest)

    kq_cai = asyncio.run(m.cai({"dich_vu_url": backend_gia.url, "khoa_dich_vu": KHOA_DICH_VU_GIA}))
    assert kq_cai["ok"] is True
    assert kq_cai["cau_hinh_luu"]["dich_vu_url"] == backend_gia.url.rstrip("/")
    kq_sai = asyncio.run(m.cai({"dich_vu_url": backend_gia.url, "khoa_dich_vu": "khoa-sai-9999"}))
    assert kq_sai["ok"] is False and "Khoá dịch vụ không đúng" in kq_sai["loi"]

    m.nap(kq_cai["cau_hinh_luu"])
    mo_res = asyncio.run(m.trinh_duyet_mo("abcdef0123456789abcdef01", "chromium"))
    assert mo_res["ho_so"] == "abcdef0123456789abcdef01" and mo_res["dich"] == backend_gia.url.rstrip("/")
    assert any(x["ho_so"] == "abcdef0123456789abcdef01" for x in asyncio.run(m.trinh_duyet_ds()))
    assert asyncio.run(m.trinh_duyet_dong("abcdef0123456789abcdef01", giu_phien=True))["ok"] is True

    phan_loai_loi = mod_obj.phan_loai_loi
    assert phan_loai_loi(401, {}, {}) == "khoa_hong"
    assert phan_loai_loi(403, {}, {}) == "khoa_hong"
    assert phan_loai_loi(429, {}, {}) == "het_han_muc"
    assert phan_loai_loi(500, {}, {}) == "may_chu_loi"
    assert phan_loai_loi(400, {}, {}) == "yeu_cau_sai"
    assert phan_loai_loi(200, {}, {}) == "khong_ro"


def test_grep_tu_khoa_trong_app_va_web():
    """grep chromium|firefox|novnc trong app và web chỉ ở novnc.py, chỗ nối router,
    chú thích hợp đồng, và app/khoi_phuc_phien.py (gọi novnc.cap_ve để mở noVNC
    khi khôi phục cần người — tích hợp chính đáng, đã duyệt NV88)."""
    goc = Path(__file__).resolve().parents[1]
    res = subprocess.run(["grep", "-rniE", "chromium|firefox|novnc", "app", "web"], cwd=goc, capture_output=True,
                         text=True)
    for line in (x for x in res.stdout.splitlines() if x.strip()):
        assert any(cho in line for cho in ("app/novnc.py", "app/tuyen.py", "app/main.py", "app/kham_pha.py",
                                           "app/khoi_phuc_phien.py")), line


# ───────────────────────────── Chrome thật ─────────────────────────────

def _di_qua_trang_ve(trang, url: str) -> None:
    """Mở liên kết vé như người dùng: có nút thì bấm (thiết kế mới), không thì trang tự chuyển (thiết kế cũ)."""
    trang.cdp.goi("Page.navigate", {"url": url}, trang.s)
    trang.cho("document.readyState === 'complete' || location.pathname.endsWith('/vnc.html')",
              mo_ta="tải trang vé")
    if trang.js("!!document.querySelector('form button')"):
        trang.bam("document.querySelector('form button')", "nút Mở trình duyệt")
    trang.cho("location.pathname.endsWith('/vnc.html')", han=10, mo_ta="tới giao diện noVNC")


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_giao_dien_novnc_dang_nhap_chrome(trang, du_lieu, backend_gia):
    """Chrome thật: mở liên kết vé → trang nút → bấm → giao diện noVNC (đích giả) hiện ra."""
    with MayChuThat(app, lifespan="on") as w:
        _cai(app, backend_gia.url)
        res_mo = httpx.post(f"{w.url}/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}, headers=QT)
        assert res_mo.status_code == 200
        trang.cdp.goi("Page.navigate", {"url": w.url + res_mo.json()["url"]}, trang.s)
        trang.cho("!!document.querySelector('form button')", mo_ta="trang có nút Mở trình duyệt")
        assert not novnc._PHIEN, "GET trang vé không được tạo phiên"
        trang.bam("document.querySelector('form button')", "nút Mở trình duyệt")
        trang.cho("location.pathname.endsWith('/vnc.html')", han=10, mo_ta="chuyển sang giao diện noVNC")
        trang.cho("document.body && document.body.innerText.includes('noVNC Mock Interface')", mo_ta="trang đích")
        assert re.match(r"^/novnc/s/[A-Za-z0-9_-]{43,}/vnc\.html$", trang.js("location.pathname"))


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_trang_dich_khong_doc_duoc_khoa_quan_tri_chrome(trang, du_lieu, backend_gia):
    """#1 bằng Chrome thật: script của ĐÍCH (bị chiếm) chạy qua proxy.

    Được: tải module-script (import thêm module), WebSocket hai chiều qua /novnc/s/<phien>/websockify.
    Không được: đọc localStorage['web2api_khoa'] của origin web2api, đọc cookie, gọi /api bằng quyền quản trị.
    """
    with MayChuThat(app, lifespan="on") as w:
        _cai(app, backend_gia.url)
        # Quản trị đã đăng nhập giao diện web2api trên trình duyệt này: khoá nằm ở localStorage của origin.
        trang.cdp.goi("Page.navigate", {"url": f"{w.url}/health"}, trang.s)
        trang.cho("document.readyState === 'complete'")
        trang.js(f"localStorage.setItem('web2api_khoa', {json.dumps(KHOA_QT)}); 1")
        res_mo = httpx.post(f"{w.url}/api/trinh-duyet/gia-browser/mo", json={"loai": "chromium"}, headers=QT)
        _di_qua_trang_ve(trang, w.url + res_mo.json()["url"])
        trang.cho("window.KQ && window.KQ.xong", han=15, mo_ta="script của đích chạy xong")
        kq = trang.js("JSON.stringify(window.KQ)")
        kq = json.loads(kq)
        assert kq["ls"] != KHOA_QT and kq["ls"].startswith("loi:"), kq
        assert kq["cookie"].startswith("loi:"), kq
        assert kq["origin"] == "null", kq
        assert kq["api"].startswith("loi:"), kq  # /api không cho origin mờ đọc/gọi (không ACAO, preflight hỏng)
        assert kq["module"] == "phu-ok", kq
        assert kq["ws"] == "echo:xin-chao", kq
        assert re.match(r"^/novnc/s/[A-Za-z0-9_-]{43,}/websockify$", kq["ws_url"]), kq
        # Phía máy chủ: lời gọi DELETE /api không có hiệu lực, phiên vẫn còn
        assert len(novnc._PHIEN) == 1
        assert not [d for d in nhat_ky.doc(kenh="trinh_duyet", gioi_han=100) if d.get("them", {}).get("hanh_dong") == "thu_hoi_phien"]


# ───────────────────────────── Soát lần hai ─────────────────────────────

def test_dang_nhap_chi_nhan_urlencoded_co_tran(du_lieu, backend_gia, monkeypatch):
    """POST /novnc/dang-nhap: multipart → 415 TRƯỚC khi đọc thân (không tệp tạm); vượt trần → 413, kể cả chunked."""
    import starlette.formparsers as fp

    tao_tep_tam = []

    class TepTamDem(fp.SpooledTemporaryFile):
        def __init__(self, *a, **k):
            tao_tep_tam.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(fp, "SpooledTemporaryFile", TepTamDem)
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        r = client.post("/novnc/dang-nhap", data={"ve": ve}, files={"tep": ("a.bin", b"\0" * (3 * 1024 * 1024))},
                        follow_redirects=False)
        assert r.status_code == 415, r.text
        assert tao_tep_tam == [], "multipart không được đọc thành tệp tạm"
        r = client.post("/novnc/dang-nhap", content=b"ve=" + b"a" * 10000,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert r.status_code == 413

        def tung_khuc():
            for _ in range(64):
                yield b"a" * 1024

        r = client.post("/novnc/dang-nhap", content=tung_khuc(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert r.status_code == 413
        assert "content-length" not in {k.lower() for k in r.request.headers}
        assert client.post("/novnc/dang-nhap", content=b"ve=" + ve.encode(),
                           headers={"Content-Type": "text/plain"}, follow_redirects=False).status_code == 415
        # vé vẫn còn nguyên sau các lần bị từ chối; đường hợp lệ (có charset) vẫn đổi được
        r = client.post("/novnc/dang-nhap", content=f"ve={ve}".encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                        follow_redirects=False)
        assert r.status_code == 303 and MAU_PHIEN.match(r.headers["location"])
        assert ve not in r.text


def test_go_module_thu_hoi_phien(du_lieu, backend_gia, monkeypatch):
    """Gỡ (hoặc thay) module của hồ sơ → phiên cũ 401, WebSocket đang mở bị đóng."""
    monkeypatch.setattr(novnc, "CHU_KY_KIEM_WS_GIAY", 0.05)
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
            ws.send_bytes(b"1")
            assert ws.receive_bytes() == b"echo:1"
            client.app.state.so.dang_chay.pop("gia-browser")
            with pytest.raises(WebSocketDisconnect) as dong_ws:
                ws.receive_bytes()
            assert dong_ws.value.code == 1008  # thu hồi, không phải kết thúc bình thường 1000
        assert client.get(f"/novnc/s/{p}/vnc.html").status_code == 401
        # Cài lại (đối tượng module MỚI cùng id) cũng không làm sống lại phiên cũ
        _cai(client, backend_gia.url)
        _, ve2 = _mo(client)
        p2 = _vao(client, ve2)
        _cai(client, backend_gia.url)
        assert client.get(f"/novnc/s/{p2}/vnc.html").status_code == 401
        with pytest.raises(WebSocketDisconnect) as tu_choi:
            with client.websocket_connect(f"/novnc/s/{p2}/websockify") as ws:
                ws.receive_bytes()
        assert tu_choi.value.code == 1008
        assert not novnc._PHIEN and not novnc._HO_SO


def test_access_log_uvicorn_that_giu_hinh_dang_args(du_lieu, backend_gia):
    """access_log bật (uvicorn thật): AccessFormatter cần đủ 5 đối số — lọc không được làm hỏng dòng log."""
    import uvicorn.logging

    class GomDinhDang(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.setFormatter(uvicorn.logging.AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s',
                                                              use_colors=False))
            self.dong: list[str] = []
            self.loi: list[str] = []

        def emit(self, record):
            try:
                self.dong.append(self.format(record))
            except Exception as e:  # noqa: BLE001 - chính là điều cần bắt
                self.loi.append(repr(e))

    gom = GomDinhDang()
    lg = logging.getLogger("uvicorn.access")
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        w._server.config.access_log = True
        cu = lg.level
        lg.addHandler(gom)
        lg.setLevel(logging.INFO)
        try:
            _cai(app, backend_gia.url)
            _, ve = _mo(h)
            assert h.get(f"/novnc/dang-nhap?ve={ve}").status_code == 200
            r = h.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False)
            p = MAU_PHIEN.match(r.headers["location"]).group(1)
            assert h.get(f"/novnc/s/{p}/vnc.html?a=1").status_code == 200
        finally:
            lg.removeHandler(gom)
            lg.setLevel(cu)
    assert gom.loi == [], gom.loi
    assert any('"GET /novnc/s/…/vnc.html?a=1 HTTP/1.1" 200' in d for d in gom.dong), gom.dong
    assert any("/novnc/dang-nhap?ve=…" in d for d in gom.dong), gom.dong
    assert all(p not in d and ve not in d for d in gom.dong)


def test_chan_6to4_va_teredo_nhung_ipv4_bi_chan(du_lieu, monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "0")
        for dich in ("http://[2002:7f00:1::]:6080", "http://[2002:a9fe:a9fe::]/",
                     "http://[2001:0:7f00:1:0:0:0:1]:80", "http://[2001:0:4136:e378:8000:63bf:5601:5601]:80"):
            _cai(client, dich, "ssrf6")
            r = client.post("/api/trinh-duyet/ssrf6/mo", json={"loai": "chromium"}, headers=QT)
            assert r.status_code == 502, (dich, r.text)
        assert not novnc._HO_SO


def test_mau_ve_ho_so_khong_nhan_xuong_dong_cuoi(du_lieu, backend_gia):
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        ho_so, ve = _mo(client)
        # Tham số đường dẫn %0A giải mã thành «\n» cuối: `$` cũ nhận, fullmatch từ chối (400, không phải 404)
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so}%0A/ve", headers=QT).status_code == 400
        assert client.post(f"/api/trinh-duyet/gia-browser/{ho_so}%0A/dong", headers=QT).status_code == 400
        assert client.get("/novnc/dang-nhap", params={"ve": ve + "\n"}).status_code == 400
        assert client.post("/novnc/dang-nhap", data={"ve": ve + "\n"}, follow_redirects=False).status_code == 401
        assert client.post("/novnc/dang-nhap", data={"ve": ve}, follow_redirects=False).status_code == 303


def test_proxy_khong_theo_chuyen_huong_cua_dich(du_lieu, backend_gia, backend_gia2):
    """Đích trả 302 sang máy khác → khách nhận 502 chung, máy thứ hai KHÔNG nhận yêu cầu; 304 vẫn chuyển nguyên."""
    backend_gia.chuyen_toi = f"{backend_gia2.url}/bi-mat"
    with TestClient(app) as client:
        _cai(client, backend_gia.url)
        _, ve = _mo(client)
        p = _vao(client, ve)
        r = client.get(f"/novnc/s/{p}/chuyen", follow_redirects=False)
        assert r.status_code == 502 and "location" not in r.headers
        assert backend_gia2.yeu_cau == []
        r304 = client.get(f"/novnc/s/{p}/khong-doi.js", headers={"If-None-Match": '"e1"'})
        assert r304.status_code == 304 and r304.headers["etag"] == '"e1"'


def test_log_khong_lo_dich_ws_khi_noi_that_bai(du_lieu, backend_gia, caplog):
    """Đích từ chối Basic khi nối WebSocket: log mọi mức (DEBUG) không có URL ws của đích, bí mật hay base64."""
    caplog.set_level(logging.DEBUG)
    backend_gia.bi_mat = "bi-mat-dung-cua-dich"
    host = backend_gia.url.removeprefix("http://")
    with TestClient(app) as client:
        _cai(client, f"http://w2a:{BI_MAT_DICH_GIA}@{host}")
        _, ve = _mo(client)
        p = _vao(client, ve)
        with pytest.raises(WebSocketDisconnect) as tu_choi:
            with client.websocket_connect(f"/novnc/s/{p}/websockify") as ws:
                ws.receive_bytes()
        assert tu_choi.value.code == 1011
    log_tat_ca = "\n".join(f"{rec.name}: {rec.getMessage()}" for rec in caplog.records)
    assert "Không nối được WebSocket tới đích" in log_tat_ca
    assert f"ws://{host}" not in log_tat_ca and "/websockify" not in log_tat_ca.replace(f"/novnc/s/{p}/websockify", "")
    assert BI_MAT_DICH_GIA not in log_tat_ca and BASIC_DUNG.split()[1] not in log_tat_ca
