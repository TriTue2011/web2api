"""NV199a — ĐO đặc thù từng NHÀ CUNG CẤP c2a so với «module từ xa» OpenAI-tương-thích của web2api.

CHỈ ĐO, TUYỆT ĐỐI KHÔNG SỬA app/modules.

Với MỖI nhà cung cấp (custom_openai, agnes, nvidia_nim, opencode, supercode,
tokenrouter, ninerouter, web_proxy): một máy chủ GIẢ mô phỏng đúng đặc thù
(địa chỉ gốc mặc định, kiểu xác thực, tiêu đề lạ, cách liệt kê mô hình,
tham số bị cấm, định dạng stream, công cụ/vision/JSON, xử lý lỗi/hạn mức)
→ đăng ký «module từ xa» qua lõi THẬT (POST /api/modules/tu-xa)
→ gọi /v1/chat/completions qua lõi THẬT
→ đo «module từ xa» có chạy được không.

Kết quả đo ghi trong nv199a-ma-tran.md (bảng ĐẠT/ĐỎ + kết luận
PRESET / VÁ NHỎ / MODULE RIÊNG). Test đo ĐỎ (đo được nhà không chạy được)
vẫn là test XANH — đỏ ở đây là kết quả đo, không phải test hỏng.
"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app import so_module
from app.main import app
from gia import MayChuThat

KHOA_QT = "khoa-quan-tri-nv199a-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_XA = "khoa-xa-gia-nv199a-abcdef0123456789"


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def dang_ky(c, id_, ten, tien_to, base_url, api_key=KHOA_XA):
    than = {"id": id_, "ten": ten, "tien_to": tien_to, "base_url": base_url}
    if api_key is not None:
        than["api_key"] = api_key
    return c.post("/api/modules/tu-xa", json=than, headers=QT)


def chat(c, model, **them):
    than = {"model": model, "messages": [{"role": "user", "content": "xin chao"}], **them}
    return c.post("/v1/chat/completions", json=than, headers=QT)


# ── máy chủ giả ────────────────────────────────────────────────────────────

class CoSoGia:
    """Khung chung: ghi nhật ký mọi lượt gọi (đường, auth, tiêu đề lạ, thân)."""

    def __init__(self):
        self.nhat_ky: list[dict] = []
        self.url = ""

    def _ghi(self, req, duong, **them):
        self.nhat_ky.append({
            "duong": duong,
            "auth": req.headers.get("authorization"),
            "x_opencode_client": req.headers.get("x-opencode-client"),
            **them,
        })

    def _sai_khoa(self, req):
        if req.headers.get("authorization") != f"Bearer {KHOA_XA}":
            return JSONResponse({"error": {"message": "sai khoa", "type": "invalid_request_error"}},
                                status_code=401)
        return None


class GiaOpenAIChung(CoSoGia):
    """custom_openai kiểu deepseek: KHÔNG có /v1 — /models và /chat/completions nằm ngay gốc."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/models", self.models),
            Route("/chat/completions", self.chat, methods=["POST"]),
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return self._sai_khoa(req) or JSONResponse(
            {"object": "list", "data": [{"id": "mo-hinh-chung", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        sai = self._sai_khoa(req)
        if sai:
            return sai
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant",
                                                      "content": "mo:" + str(than["model"])},
                                          "finish_reason": "stop"}]})


class GiaAgnes(CoSoGia):
    """agnes: /v1 chuẩn, Bearer; nhận tools + vision; giữ trường lạ."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return self._sai_khoa(req) or JSONResponse(
            {"object": "list", "data": [{"id": "agnes-2.5-flash", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        sai = self._sai_khoa(req)
        if sai:
            return sai
        return JSONResponse({
            "object": "chat.completion", "model": than["model"],
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "agnes tra loi",
                                     "tool_calls": than.get("tools") or None},
                         "finish_reason": "stop"}],
            "x_truong_la_cua_agnes": than.get("x_truong_la"),
        })


class GiaNvidia(CoSoGia):
    """nvidia_nim: /v1 chuẩn, Bearer; stream có delta.reasoning_content."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return self._sai_khoa(req) or JSONResponse(
            {"object": "list", "data": [{"id": "openai/gpt-oss-120b", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        sai = self._sai_khoa(req)
        if sai:
            return sai
        if than.get("stream"):
            async def phat():
                yield ('data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                       '"reasoning_content":"suy luan","content":"tra loi"}}]}\n\n')
                yield "data: [DONE]\n\n"
            return StreamingResponse(phat(), media_type="text/event-stream")
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": "nvidia tra loi"},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}})


class GiaOpencode(CoSoGia):
    """opencode: /zen/v1; chat BẮT BUỘC tiêu đề x-opencode-client: desktop (lạ so với OpenAI),
    auth là Bearer «public»; model big-pickle dùng endpoint /messages định dạng Claude."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/zen/v1/models", self.models),
            Route("/zen/v1/chat/completions", self.chat, methods=["POST"]),
            Route("/zen/v1/messages", self.messages_claude, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return JSONResponse({"object": "list", "data": [
            {"id": "qwen-free", "object": "model"},
            {"id": "big-pickle", "object": "model"},
        ]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        if req.headers.get("x-opencode-client") != "desktop":
            return JSONResponse({"error": {"message": "thieu tieu de x-opencode-client",
                                           "type": "invalid_request_error"}}, status_code=400)
        if req.headers.get("authorization") != "Bearer public":
            return JSONResponse({"error": {"message": "sai khoa public", "type": "invalid_request_error"}},
                                status_code=401)
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": "opencode tra loi"},
                                          "finish_reason": "stop"}]})

    async def messages_claude(self, req):
        than = await req.json()
        self._ghi(req, "messages_claude", than=than)
        return JSONResponse({"content": [{"type": "text", "text": "claude dinh dang"}]})


class GiaSupercode(CoSoGia):
    """supercode: KHÔNG phải OpenAI — POST /api/ai/chat, thân {messages, provider, model, tools},
    trả NDJSON mỗi dòng {"type": "text"|"finish"}; token 32 ký tự qua device flow GitHub."""

    TOKEN = "tokengiadai32kytuchosupercode199a"

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/api/ai/chat", self.chat, methods=["POST"]),
        ])

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "api_ai_chat", than=than)
        if req.headers.get("authorization") != f"Bearer {self.TOKEN}":
            return JSONResponse({"error": "sai token"}, status_code=401)
        if set(than) != {"messages", "provider", "model", "tools"}:
            return JSONResponse({"error": f"than sai dinh dang: {sorted(than)}"}, status_code=400)

        async def phat():
            yield '{"type": "text", "content": "supercode"}\n'
            yield '{"type": "text", "content": " tra loi"}\n'
            yield '{"type": "finish", "reason": "stop", "usage": {"inputTokens": 4}}\n'

        return Response(b"".join([l async for l in phat()]), media_type="application/x-ndjson")


class GiaTokenRouter(CoSoGia):
    """tokenrouter: /v1 chuẩn, Bearer; stream hỗ trợ stream_options.include_usage (chunk usage cuối)."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return self._sai_khoa(req) or JSONResponse(
            {"object": "list", "data": [{"id": "kimi-k3", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        sai = self._sai_khoa(req)
        if sai:
            return sai
        if than.get("stream"):
            async def phat():
                yield 'data: {"choices":[{"index":0,"delta":{"content":"token"}}]}\n\n'
                if (than.get("stream_options") or {}).get("include_usage"):
                    yield ('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                           '"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\n')
                yield "data: [DONE]\n\n"
            return StreamingResponse(phat(), media_type="text/event-stream")
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": "tokenrouter tra loi"},
                                          "finish_reason": "stop"}]})


class GiaNinerouter(CoSoGia):
    """ninerouter: proxy OpenAI ở localhost; KHÔNG BẮT BUỘC auth (api_key có thể rỗng)."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        self._ghi(req, "models")
        return JSONResponse({"object": "list", "data": [{"id": "claude-free", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        self._ghi(req, "chat", than=than)
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": "9router tra loi"},
                                          "finish_reason": "stop"}]})


class GiaWebProxy(CoSoGia):
    """web_proxy (gmw/chat): KHÔNG có /chat/completions — chỉ có /v1/gemini-web/chat
    nhận {"profile","prompt","timeout","headless"} (prompt đã dẹp phẳng), trả {"text": ...}."""

    def __init__(self):
        super().__init__()
        self.app = Starlette(routes=[
            Route("/v1/gemini-web/chat", self.web_chat, methods=["POST"]),
        ])

    async def web_chat(self, req):
        than = await req.json()
        self._ghi(req, "web_chat", than=than)
        if req.headers.get("authorization") != f"Bearer {KHOA_XA}":
            return JSONResponse({"error": "sai khoa solver"}, status_code=401)
        if set(than) != {"profile", "prompt", "timeout", "headless"} or not isinstance(than.get("prompt"), str):
            return JSONResponse({"error": f"than sai dinh dang: {sorted(than)}"}, status_code=400)
        return JSONResponse({"text": "web tra loi: " + than["prompt"][:20], "elapsed_ms": 123})


# ── các phép đo ────────────────────────────────────────────────────────────

def test_do_custom_openai_khong_v1(du_lieu):
    """custom_openai kiểu deepseek (không /v1): preset base_url + api_key là đủ → ĐẠT."""
    g = GiaOpenAIChung()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-chung", "Custom OpenAI", "chung", g.url, KHOA_XA)
            assert r.status_code == 200, r.text
            r2 = chat(c, "chung/mo-hinh-chung")
            assert r2.status_code == 200, r2.text
            assert r2.json()["choices"][0]["message"]["content"] == "mo:mo-hinh-chung"
            nhan = g.nhat_ky[-1]
            assert nhan["auth"] == f"Bearer {KHOA_XA}"
            assert nhan["than"]["model"] == "mo-hinh-chung"  # tiền tố đã bóc
            ds = c.get("/v1/models", headers=QT).json()["data"]
            assert "chung/mo-hinh-chung" in [m["id"] for m in ds]


def test_do_agnes(du_lieu):
    """agnes: /v1 chuẩn + Bearer; tools/vision/trường lạ đi qua → ĐẠT."""
    g = GiaAgnes()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-agnes", "Agnes AI", "ag", g.url + "/v1", KHOA_XA)
            assert r.status_code == 200, r.text
            cong_cu = [{"type": "function", "function": {"name": "xem_gio", "parameters": {}}}]
            r2 = c.post("/v1/chat/completions", json={
                "model": "ag/agnes-2.5-flash",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "mo ta anh"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]}],
                "tools": cong_cu,
                "x_truong_la": {"giu": [1, None]},
            }, headers=QT)
            assert r2.status_code == 200, r2.text
            assert r2.json()["choices"][0]["message"]["content"] == "agnes tra loi"
            nhan = g.nhat_ky[-1]
            assert nhan["auth"] == f"Bearer {KHOA_XA}"
            assert nhan["than"]["model"] == "agnes-2.5-flash"
            assert nhan["than"]["tools"] == cong_cu
            assert nhan["than"]["x_truong_la"] == {"giu": [1, None]}
            assert nhan["than"]["messages"][0]["content"][1]["type"] == "image_url"


def test_do_nvidia_nim(du_lieu):
    """nvidia_nim: stream có delta.reasoning_content đi xuyên qua → ĐẠT."""
    g = GiaNvidia()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-nvidia", "NVIDIA NIM", "nv", g.url + "/v1", KHOA_XA)
            assert r.status_code == 200, r.text
            r2 = chat(c, "nv/openai/gpt-oss-120b")
            assert r2.status_code == 200, r2.text
            s2 = chat(c, "nv/openai/gpt-oss-120b", stream=True)
            assert s2.status_code == 200, s2.text
            assert "reasoning_content" in s2.text  # trường lạ của NVIDIA không bị lọc
            assert "data: [DONE]" in s2.text


def test_do_opencode_tieu_de_la_qua_header_them(du_lieu):
    """opencode: khai header_them={"x-opencode-client": "desktop"} ở đăng ký → chat 200 (NV199c vá).
    Không khai header → vẫn 400 như đo ở NV199a (xem test_web2api_nv199c_header_them.py)."""
    g = GiaOpencode()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            than = {"id": "do-opencode", "ten": "OpenCode", "tien_to": "oc",
                    "base_url": g.url + "/zen/v1", "api_key": "public",
                    "header_them": {"x-opencode-client": "desktop"}}
            r = c.post("/api/modules/tu-xa", json=than, headers=QT)
            assert r.status_code == 200, r.text
            # công khai chỉ nêu TÊN header, không lộ giá trị
            assert r.json()["module"]["cau_hinh_cong_khai"]["header_them"] == ["x-opencode-client"]
            r2 = chat(c, "oc/qwen-free")
            assert r2.status_code == 200, r2.text
            nhan = g.nhat_ky[-1]
            assert nhan["auth"] == "Bearer public"
            assert nhan["x_opencode_client"] == "desktop"
            # liệt kê model thì vẫn qua được (đường /models không đòi tiêu đề lạ)
            ds = c.get("/v1/models", headers=QT).json()["data"]
            assert "oc/qwen-free" in [m["id"] for m in ds]


def test_do_supercode_sai_giao_thuc(du_lieu):
    """supercode: POST /api/ai/chat + NDJSON, không phải /chat/completions + SSE → ĐO ĐƯỢC ĐỎ."""
    g = GiaSupercode()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-supercode", "SuperCode", "sc", g.url, GiaSupercode.TOKEN)
            assert r.status_code == 200, r.text
            r2 = chat(c, "sc/openrouter:glm-5.2")
            # lõi gọi /chat/completions — nhà này không có đường đó → 404 chuyển tiếp
            assert r2.status_code == 404, r2.text
            assert not [n for n in g.nhat_ky if n["duong"] == "api_ai_chat"]


def test_do_tokenrouter(du_lieu):
    """tokenrouter: stream_options.include_usage đi qua, chunk usage cuối còn nguyên → ĐẠT."""
    g = GiaTokenRouter()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-tokenrouter", "TokenRouter", "tr", g.url + "/v1", KHOA_XA)
            assert r.status_code == 200, r.text
            r2 = chat(c, "tr/kimi-k3")
            assert r2.status_code == 200, r2.text
            s2 = chat(c, "tr/kimi-k3", stream=True,
                      stream_options={"include_usage": True})
            assert s2.status_code == 200, s2.text
            assert '"usage"' in s2.text  # chunk usage cuối không bị nuốt
            assert '"total_tokens":4' in s2.text.replace(" ", "")
            nhan = [n for n in g.nhat_ky if n["duong"] == "chat"][-1]
            assert nhan["than"].get("stream_options") == {"include_usage": True}


def test_do_ninerouter_khong_can_khoa(du_lieu):
    """ninerouter: không cần api_key — lõi không gửi Authorization vẫn chạy → ĐẠT."""
    g = GiaNinerouter()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-9router", "9Router", "r9", g.url + "/v1", api_key=None)
            assert r.status_code == 200, r.text
            r2 = chat(c, "r9/claude-free")
            assert r2.status_code == 200, r2.text
            assert r2.json()["choices"][0]["message"]["content"] == "9router tra loi"
            nhan = g.nhat_ky[-1]
            assert nhan["auth"] is None  # lõi không tự bịa Authorization


def test_do_web_proxy_sai_giao_thuc(du_lieu):
    """web_proxy (gmw/chat): gọi solver /v1/gemini-web/chat bằng prompt dẹp phẳng,
    không phải /chat/completions → ĐO ĐƯỢC ĐỎ."""
    g = GiaWebProxy()
    with MayChuThat(g.app) as s:
        g.url = s.url
        with TestClient(app) as c:
            r = dang_ky(c, "do-gmw", "Gemini Web", "gmw", g.url + "/v1", KHOA_XA)
            assert r.status_code == 200, r.text
            r2 = chat(c, "gmw/chat")
            # lõi gọi /v1/chat/completions — nhà này chỉ có /v1/gemini-web/chat → 404
            assert r2.status_code == 404, r2.text
            assert not [n for n in g.nhat_ky if n["duong"] == "web_chat"]
