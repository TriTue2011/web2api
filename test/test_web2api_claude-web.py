"""Module claude-web: đo trên máy chủ claude.ai GIẢ cục bộ, qua lõi thật.

Không gọi mạng thật (conftest chặn Internet; module chỉ được phép ra 127.0.0.1
nhờ WEB2API_TEST_CHO_PHEP_LOOPBACK=1).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
import threading
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app import kham_pha
from app.main import app
from gia import MayChuThat

GOC = Path(__file__).resolve().parents[1]
GOC_MODULE = GOC / "modules" / "claude-web"
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

# Cookie giả: máy chủ giả nhận diện theo chuỗi con trong header Cookie.
TOT = "tot-session-key-aaaabbbbcccc"
TOT_2 = "tot-session-key-ddddeeeeffff"
CHAM = "cham-cho-khuc-2-gggg"
MODEL = "model-that-trong-sse-hhhh"
LOI_GIUA = "loi-giua-luong-iiii"
C401 = "het-han-401-jjjj"
C403 = "bi-cam-403-kkkk"
C429 = "het-han-muc-429-llll"
C500 = "loi-may-chu-500-mmmm"
ORG500 = "org-loi-500-nnnn"


def _nap_goi_module():
    """Nạp modules/claude-web như một gói (giống lõi nạp), để test trực tiếp."""
    ten = "claude_web_goi_test"
    if ten in sys.modules:
        return sys.modules[ten]
    goi = types.ModuleType(ten)
    goi.__path__ = [str(GOC_MODULE)]
    sys.modules[ten] = goi
    spec = importlib.util.spec_from_file_location(
        ten + ".module", GOC_MODULE / "module.py",
        submodule_search_locations=[str(GOC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten + ".module"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def goi_module():
    return _nap_goi_module()


@pytest.fixture(scope="module")
def cv_mod(goi_module):
    import importlib
    return importlib.import_module("claude_web_goi_test.converter")


# ────────────────────────── claude.ai GIẢ ──────────────────────────

def _sse(dong: list[str]) -> StreamingResponse:
    async def _gen():
        for d in dong:
            yield d.encode("utf-8")
    return StreamingResponse(_gen(), media_type="text/event-stream")


class ClaudeGia:
    """Máy chủ claude.ai giả: organizations, chat_conversations, completion."""

    def __init__(self):
        self.hanh_vi: dict[str, str] = {}  # chuỗi con trong cookie → kiểu
        self.nhat_ky: list[dict] = []      # mọi lượt gọi completion
        self.cho_khuc_2 = threading.Event()

        async def organizations(request):
            cookie = request.headers.get("cookie", "")
            kieu = self._kieu(cookie)
            if kieu in ("401", "403"):
                return JSONResponse({"detail": "unauthorized"}, status_code=int(kieu))
            if kieu == "org-500":
                return JSONResponse({"detail": "upstream"}, status_code=500)
            return JSONResponse([{"uuid": "org-gia-1", "capabilities": ["chat"],
                                  "name": "Tổ chức giả"}])

        async def tao_hoi_thoai(request):
            cookie = request.headers.get("cookie", "")
            if self._kieu(cookie) in ("401", "403"):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return JSONResponse({"uuid": "conv-gia-1", "name": ""})

        async def completion(request):
            cookie = request.headers.get("cookie", "")
            kieu = self._kieu(cookie)
            than = await request.json()
            self.nhat_ky.append({"cookie": cookie, "prompt": than.get("prompt"),
                                 "model": than.get("model", "<khong-co-truong>")})
            if kieu in ("429", "500"):
                return JSONResponse({"detail": "upstream"}, status_code=int(kieu))
            if kieu == "loi-giua":
                return _sse(['data: {"completion": "nửa câu"}\n\n',
                             'data: {"type": "error", "error": {"message": "boom"}}\n\n'])
            if kieu == "model":
                return _sse(['data: {"type": "message_start", "message": '
                             '{"model": "claude-sonnet-5"}}\n\n',
                             'data: {"completion": "Xong"}\n\n',
                             "data: [DONE]\n\n"])
            if kieu == "cham":
                async def _gen():
                    yield 'data: {"completion": "Một"}\n\n'.encode("utf-8")
                    self.cho_khuc_2.wait(10)
                    yield 'data: {"completion": "Hai"}\n\n'.encode("utf-8")
                    yield b"data: [DONE]\n\n"
                return StreamingResponse(_gen(), media_type="text/event-stream")
            return _sse(['data: {"completion": "Xin "}\n\n',
                         'data: {"completion": "chào bạn!"}\n\n',
                         "data: [DONE]\n\n"])

        self.app = Starlette(routes=[
            Route("/api/organizations", organizations),
            Route("/api/organizations/{org}/chat_conversations", tao_hoi_thoai,
                  methods=["POST"]),
            Route("/api/organizations/{org}/chat_conversations/{conv}/completion",
                  completion, methods=["POST"]),
        ])

    def _kieu(self, cookie: str) -> str:
        for k, v in self.hanh_vi.items():
            if k in cookie:
                return v
        return "tot"


@pytest.fixture(scope="module")
def _claude_srv():
    g = ClaudeGia()
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def claude(_claude_srv):
    _claude_srv.hanh_vi.clear()
    _claude_srv.nhat_ky.clear()
    _claude_srv.cho_khuc_2.clear()
    return _claude_srv


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    shutil.copytree(GOC_MODULE, tmp_path / "modules" / "claude-web")
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        yield c


def cai(c, claude, cookies, base=None):
    return c.post("/api/modules/claude-web/cai", headers=QT,
                  json={"cau_hinh": {"cookies": cookies,
                                     "base_url": base or claude.url}})


def mod(c):
    return c.app.state.so.dang_chay["claude-web"]


def tin(model="clw/sonnet", **them):
    return {"model": model,
            "messages": [{"role": "system", "content": "Bạn là trợ lý."},
                         {"role": "user", "content": "Xin chào"}],
            **them}


def khong_ro_cookie(*van_ban):
    for vb in van_ban:
        for k in (TOT, TOT_2, CHAM, MODEL, LOI_GIUA, C401, C403, C429, C500, ORG500):
            assert k not in vb, f"cookie {k[-8:]} bị lộ"


# ── 1. Manifest ─────────────────────────────────────────────────────

class TestManifest:
    def test_kiem_manifest_ok(self):
        manifest, loi = kham_pha.doc_manifest(GOC_MODULE)
        assert manifest is not None, loi
        assert kham_pha.kiem_manifest(manifest) is None

    def test_hang_a_va_mang_ra(self, goi_module):
        manifest, _ = kham_pha.doc_manifest(GOC_MODULE)
        assert manifest["hang"] == "A"
        assert manifest["tai_nguyen"]["mang_ra"] == ["claude.ai"]
        assert manifest["tien_to"] == "clw"
        assert manifest["id"] == "claude-web"
        cookies = [t for t in manifest["truong_cau_hinh"] if t["khoa"] == "cookies"][0]
        assert cookies["kieu"] == "danh_sach_bi_mat"
        assert cookies["nhan_cookie"] == {"mien": "claude.ai"}

    def test_goi_thieu(self):
        manifest, _ = kham_pha.doc_manifest(GOC_MODULE)
        assert kham_pha.goi_thieu(manifest) == []

    def test_nap_ma_du_chuan(self):
        manifest, _ = kham_pha.doc_manifest(GOC_MODULE)
        vt = kham_pha.van_tay(GOC_MODULE)
        lop, _ = kham_pha.nap_ma(GOC_MODULE, manifest, vt)
        for pt in ("nap", "cai", "models", "chat", "suc_khoe", "dung"):
            assert callable(getattr(lop, pt, None)), f"thiếu {pt}"


# ── 2. Cài đặt: kiểm cookie ─────────────────────────────────────────

class TestCai:
    def test_cookie_tot(self, client, claude):
        r = cai(client, claude, [TOT])
        assert r.status_code == 200, r.text
        kq = r.json()
        assert kq["ok"] is True
        assert kq["ket_qua_tung_khoa"][0]["ok"] is True
        assert TOT not in json.dumps(kq)  # dạng che
        assert kq["ket_qua_tung_khoa"][0]["cookie"].endswith("…" + TOT[-4:])

    def test_cookie_401_bi_loai(self, client, claude):
        claude.hanh_vi[C401] = "401"
        r = cai(client, claude, [C401])
        assert r.status_code == 400, r.text
        kq = r.json()
        assert kq["ok"] is False
        assert kq["ket_qua_tung_khoa"][0]["ok"] is False

    def test_cookie_500_duoc_giu_lai(self, client, claude):
        """Lỗi tạm lúc cài không bắt dán lại: giữ cookie, ok=false."""
        claude.hanh_vi[ORG500] = "org-500"
        r = cai(client, claude, [TOT, ORG500])
        assert r.status_code == 200, r.text
        kq = r.json()
        assert kq["ok"] is True
        assert [m["ok"] for m in kq["ket_qua_tung_khoa"]] == [True, False]
        m = mod(client)
        assert len(m._accs) == 2  # cả hai đều được nạp để thử lại sau

    def test_thieu_cookie(self, client, claude):
        r = cai(client, claude, [])
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_base_url_sai(self, client, claude):
        r = cai(client, claude, [TOT], base="khong-phai-url")
        assert r.status_code == 400
        assert r.json()["ok"] is False


# ── 3. Chat không stream ────────────────────────────────────────────

class TestChatKhongStream:
    def test_thanh_cong(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["choices"][0]["message"]["content"] == "Xin chào bạn!"
        assert d["choices"][0]["finish_reason"] == "stop"
        assert d["object"] == "chat.completion"

    def test_prompt_gom_dung_dinh_dang(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        goi = claude.nhat_ky[-1]
        assert goi["prompt"] == ("System: Bạn là trợ lý.\n\nUser: Xin chào\n\nAssistant:")

    def test_bi_danh_model(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        client.post("/v1/chat/completions", headers=QT,
                    json=tin(model="clw/sonnet", stream=False))
        assert claude.nhat_ky[-1]["model"] == "claude-sonnet-5"

    def test_auto_bo_truong_model(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        client.post("/v1/chat/completions", headers=QT,
                    json=tin(model="clw/auto", stream=False))
        assert claude.nhat_ky[-1]["model"] == "<khong-co-truong>"

    def test_model_that_tu_message_start(self, client, claude):
        claude.hanh_vi[MODEL] = "model"
        assert cai(client, claude, [MODEL]).status_code == 200
        r = client.post("/v1/chat/completions", headers=QT,
                        json=tin(model="clw/auto", stream=False))
        assert r.status_code == 200
        assert r.json()["model"] == "claude-sonnet-5"


# ── 4. Stream từng dòng ─────────────────────────────────────────────

class TestStream:
    def test_stream_co_role_va_done(self, du_lieu, claude):
        with MayChuThat(app, lifespan="on") as w:
            with httpx.Client(base_url=w.url, timeout=15) as h:
                r = h.post("/api/modules/claude-web/cai", headers=QT,
                           json={"cau_hinh": {"cookies": [TOT], "base_url": claude.url}})
                assert r.status_code == 200, r.text
                dong = []
                with h.stream("POST", "/v1/chat/completions", headers=QT,
                              json=tin(stream=True)) as s:
                    assert s.status_code == 200
                    assert s.headers["content-type"].startswith("text/event-stream")
                    for d in s.iter_lines():
                        if d:
                            dong.append(d)
        co_noi_dung = [d for d in dong if d and d != "data: [DONE]"]
        assert len(co_noi_dung) == 3  # role+chunk1, chunk2, stop
        d0 = json.loads(co_noi_dung[0][6:])
        assert d0["choices"][0]["delta"]["role"] == "assistant"
        assert d0["choices"][0]["delta"]["content"] == "Xin "
        d1 = json.loads(co_noi_dung[1][6:])
        assert d1["choices"][0]["delta"]["content"] == "chào bạn!"
        d2 = json.loads(co_noi_dung[2][6:])
        assert d2["choices"][0]["finish_reason"] == "stop"
        assert dong[-1] == "data: [DONE]"

    def test_khong_gom_bo_dem(self, du_lieu, claude):
        """Khúc 1 tới tay khách TRƯỚC khi máy chủ giả nhả khúc 2."""
        claude.hanh_vi[CHAM] = "cham"
        with MayChuThat(app, lifespan="on") as w:
            with httpx.Client(base_url=w.url, timeout=15) as h:
                r = h.post("/api/modules/claude-web/cai", headers=QT,
                           json={"cau_hinh": {"cookies": [CHAM], "base_url": claude.url}})
                assert r.status_code == 200, r.text
                dong = []
                with h.stream("POST", "/v1/chat/completions", headers=QT,
                              json=tin(stream=True)) as s:
                    assert s.status_code == 200
                    for d in s.iter_lines():
                        if d:
                            dong.append(d)
                        if "Một" in d:
                            claude.cho_khuc_2.set()
        assert any("Một" in d for d in dong) and any("Hai" in d for d in dong)
        assert dong[-1] == "data: [DONE]"


# ── 5. Xoay tài khoản theo mã lỗi ───────────────────────────────────

class TestXoayTaiKhoan:
    def test_401_chuyen_sang_tai_khoan_2(self, client, claude, caplog):
        assert cai(client, claude, [C401, TOT]).status_code == 200
        claude.hanh_vi[C401] = "401"  # cookie hết hạn SAU khi cài
        with caplog.at_level("WARNING", logger="web2api.module.claude-web"):
            r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == "Xin chào bạn!"
        m = mod(client)
        assert m._accs[0].khoe is False and m._accs[1].khoe is True
        khong_ro_cookie(caplog.text, r.text)

    def test_403_chuyen_tai_khoan(self, client, claude):
        assert cai(client, claude, [C403, TOT]).status_code == 200
        claude.hanh_vi[C403] = "403"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200
        assert mod(client)._accs[0].khoe is False

    def test_429_chuyen_tai_khoan(self, client, claude):
        assert cai(client, claude, [C429, TOT]).status_code == 200
        claude.hanh_vi[C429] = "429"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200
        assert [x["cookie"] for x in claude.nhat_ky] != []
        assert mod(client)._accs[0].khoe is False

    def test_tat_ca_429_ra_429(self, client, claude):
        assert cai(client, claude, [C429]).status_code == 200
        claude.hanh_vi[C429] = "429"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 429, r.text
        assert r.json()["error"]["type"] == "het_han_muc"
        khong_ro_cookie(r.text)

    def test_500_chuyen_tai_khoan(self, client, claude):
        assert cai(client, claude, [C500, TOT]).status_code == 200
        claude.hanh_vi[C500] = "500"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200
        # Lỗi tạm không đánh dấu hỏng: tài khoản 1 vẫn khoẻ để thử lại sau.
        assert mod(client)._accs[0].khoe is True

    def test_tat_ca_500_ra_502(self, client, claude):
        assert cai(client, claude, [C500]).status_code == 200
        claude.hanh_vi[C500] = "500"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 502, r.text
        assert r.json()["error"]["type"] == "may_chu_loi"
        khong_ro_cookie(r.text)

    def test_tat_ca_401_ra_401(self, client, claude):
        assert cai(client, claude, [C401]).status_code == 200
        claude.hanh_vi[C401] = "401"
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 401, r.text
        assert r.json()["error"]["type"] == "khoa_hong"
        khong_ro_cookie(r.text)

    def test_tai_khoan_hong_duoc_thu_lai(self, client, claude):
        """Không còn ai khoẻ → lượt sau thử lại tài khoản đã đánh dấu hỏng."""
        assert cai(client, claude, [C401, TOT]).status_code == 200
        claude.hanh_vi[C401] = "401"
        claude.hanh_vi[TOT] = "401"
        m = mod(client)
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 401
        assert m._accs[0].khoe is False and m._accs[1].khoe is False
        # Cookie hồi phục → lượt sau thử lại tài khoản đầu và khoẻ lại.
        claude.hanh_vi.clear()
        r = client.post("/v1/chat/completions", headers=QT, json=tin(stream=False))
        assert r.status_code == 200
        assert m._accs[0].khoe is True


# ── 6. Lỗi giữa stream ──────────────────────────────────────────────

class TestLoiGiuaStream:
    def test_error_khong_done(self, du_lieu, claude):
        claude.hanh_vi[LOI_GIUA] = "loi-giua"
        with MayChuThat(app, lifespan="on") as w:
            with httpx.Client(base_url=w.url, timeout=15) as h:
                r = h.post("/api/modules/claude-web/cai", headers=QT,
                           json={"cau_hinh": {"cookies": [LOI_GIUA],
                                              "base_url": claude.url}})
                assert r.status_code == 200, r.text
                dong = []
                with h.stream("POST", "/v1/chat/completions", headers=QT,
                              json=tin(stream=True)) as s:
                    assert s.status_code == 200  # đã có delta → 200
                    for d in s.iter_lines():
                        if d:
                            dong.append(d)
        full = "\n".join(dong)
        assert "nửa câu" in full
        assert '"error"' in full
        assert "data: [DONE]" not in dong
        khong_ro_cookie(full)


# ── 7. Bí mật không lộ ──────────────────────────────────────────────

class TestBaoMat:
    def test_suc_khoe_chi_hien_che(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.get("/api/modules/claude-web/suc-khoe", headers=QT)
        assert r.status_code == 200
        txt = r.text
        assert TOT not in txt
        assert r.json()["tai_khoan"][0]["cookie"] == "…" + TOT[-4:]

    def test_cau_hinh_cong_khai_che(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.get("/api/modules/claude-web/cau-hinh", headers=QT)
        assert r.status_code == 200
        assert TOT not in r.text

    def test_che_dung_quy_uoc(self, goi_module):
        assert goi_module._che("sessionKey=abc123def456789012") == "…9012"
        assert goi_module._che("k1=xyz987654321; k2=v2") == "…4321"
        assert goi_module._che("k1=v1; k2=v2") == "…"
        assert goi_module._che("abcd") == "…"
        assert goi_module._che("giá-trị-trần-không-bằng") == "…bằng"

    def test_phan_loai_chi_theo_ma(self, goi_module):
        assert goi_module.phan_loai_ma(401) == "xac_thuc"
        assert goi_module.phan_loai_ma(403) == "xac_thuc"
        assert goi_module.phan_loai_ma(429) == "han_muc"
        assert goi_module.phan_loai_ma(529) == "han_muc"
        assert goi_module.phan_loai_ma(500) == "tam_thoi"
        assert goi_module.phan_loai_ma(503) == "tam_thoi"


# ── 8. Không có tài khoản ───────────────────────────────────────────

class TestKhongTaiKhoan:
    def test_chat_khong_tai_khoan_401(self, goi_module):
        mm = goi_module
        manifest, _ = kham_pha.doc_manifest(GOC_MODULE)
        m = mm.Module(manifest)
        m.nap({})
        import asyncio
        ma, _, body = asyncio.run(m.chat(tin(stream=False), stream=False))
        assert ma == 401
        assert json.loads(body)["error"]["type"] == "chua_co_tai_khoan"

    def test_models_khong_can_tai_khoan(self, goi_module):
        import asyncio
        manifest, _ = kham_pha.doc_manifest(GOC_MODULE)
        m = goi_module.Module(manifest)
        m.nap({})
        ds = asyncio.run(m.models())
        ids = [x["id"] for x in ds]
        assert "clw/sonnet" in ids and "clw/opus" in ids and "clw/haiku" in ids
        assert "clw/auto" in ids
        assert all(x.startswith("clw/") for x in ids)


# ── 9. Yêu cầu sai hình dạng ────────────────────────────────────────

class TestYeuCauSai:
    def test_messages_rong(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.post("/v1/chat/completions", headers=QT,
                        json={"model": "clw/sonnet", "messages": []})
        assert r.status_code == 400, r.text

    def test_thieu_model(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.post("/v1/chat/completions", headers=QT,
                        json={"messages": [{"role": "user", "content": "Hi"}]})
        assert r.status_code == 400, r.text

    def test_co_anh_bao_chua_ho_tro(self, client, claude):
        assert cai(client, claude, [TOT]).status_code == 200
        r = client.post("/v1/chat/completions", headers=QT, json={
            "model": "clw/sonnet",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "đây là gì"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]}]})
        assert r.status_code == 400, r.text
        assert "chưa hỗ trợ ảnh" in r.json()["error"]["message"]


# ── 10. Converter ───────────────────────────────────────────────────

class TestConverter:
    def test_giai_model(self, cv_mod):
        cv = cv_mod
        assert cv.giai_model("clw/sonnet") == "claude-sonnet-5"
        assert cv.giai_model("clw/opus") == "claude-opus-4-8"
        assert cv.giai_model("clw/haiku") == "claude-haiku-4-5"
        assert cv.giai_model("clw/auto") == ""
        assert cv.giai_model("clw/") == ""
        assert cv.giai_model("clw/model-la-xyz") == "model-la-xyz"  # lạ: cho qua

    def test_xay_dung_prompt(self, cv_mod):
        cv = cv_mod
        p = cv.xay_dung_prompt([{"role": "system", "content": "S"},
                                {"role": "user", "content": "Hỏi"},
                                {"role": "assistant", "content": "Đáp"},
                                {"role": "user", "content": "Tiếp"}])
        assert p == "System: S\n\nUser: Hỏi\n\nAssistant: Đáp\n\nUser: Tiếp\n\nAssistant:"

    def test_prompt_list_text(self, cv_mod):
        cv = cv_mod
        p = cv.xay_dung_prompt([{"role": "user", "content": [
            {"type": "text", "text": "một"}, {"type": "text", "text": "hai"}]}])
        assert p == "User: một hai\n\nAssistant:"
