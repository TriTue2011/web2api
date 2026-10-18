"""Test NV102-WEB cho hai module grok-web và claude-web: hỗ trợ model_bo_sung.

Kiểm tra:
1. grok-web:
   - model_bo_sung (id="grok/expert-2") xuất hiện trong models(), cho phép ghi đè nhãn của model tĩnh.
   - Chat tới model bổ sung gửi đúng mã xuống máy chủ WebSocket.
   - Chat tới model không có trả 400 invalid_request_error với mã model_not_found.
   - auto/fast vẫn hoạt động bình thường (auto chuyển thành fast).
2. claude-web:
   - model_bo_sung (id="clw/model-moi-9") xuất hiện trong models(), ghi đè nhãn nếu có.
   - Chat tới model bổ sung gửi đúng mã lên máy chủ claude.ai.
   - Tệp bổ sung hỏng/trường lạ không làm ném ngoại lệ, giữ bảng tĩnh.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from websockets.asyncio.server import serve

from app import model_bo_sung

GOC = Path(__file__).resolve().parents[1]


def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test grok-web
# ---------------------------------------------------------------------------

TEN_GROK = "grok_web_nv102_test"
THU_MUC_GROK = GOC / "modules" / "grok-web"


def _tai_grok():
    for ten in [t for t in list(sys.modules) if t == TEN_GROK or t.startswith(TEN_GROK + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GROK)
    goi.__path__ = [str(THU_MUC_GROK)]
    sys.modules[TEN_GROK] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GROK}.{ten_tep}", THU_MUC_GROK / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GROK}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GROK}.module"],
            sys.modules[f"{TEN_GROK}.client"],
            sys.modules[f"{TEN_GROK}.converter"])


grok_module, grok_client, grok_converter = _tai_grok()
MANIFEST_GROK = json.loads((THU_MUC_GROK / "manifest.json").read_text(encoding="utf-8"))


class MayChuGrokGia:
    def __init__(self):
        self.nhat_ky = []
        self.url = ""
        self._san_sang = threading.Event()
        self._vong = None
        self._dung = None

    async def _xu_ly_http(self, ket_noi, yeu_cau):
        if yeu_cau.path == "/api/auth/session":
            dap = ket_noi.respond(200, json.dumps({"session": {"userId": "uid_test"}}))
            dap.headers["Content-Type"] = "application/json"
            return dap
        return None

    async def _xu_ly_ws(self, ws):
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "session.create"
        che_do = sk["event"]["session"]["model"]
        await ws.send(json.dumps({"event": {"type": "session.created", "session": {"id": "sess_1"}}}))
        await ws.send(json.dumps({"event": {"type": "conversation.attached", "conversation": {"id": "conv_1"}}}))
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "conversation.item.create"
        prompt = sk["event"]["item"]["x_grok"]["input_chunks"][0]["text"]["text"]
        self.nhat_ky.append({"che_do": che_do, "prompt": prompt})
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "response.create"
        await ws.send(json.dumps({
            "event": {"type": "response.chunk", "chunk": {"text": {"text": "Phản hồi Grok"}}}
        }))
        await ws.send(json.dumps({
            "event": {"type": "response.done", "status": "completed"}
        }))

    def _chay(self):
        async def main():
            self._dung = asyncio.Event()
            async with serve(self._xu_ly_ws, "127.0.0.1", 0,
                             process_request=self._xu_ly_http) as may_chu:
                cong = may_chu.sockets[0].getsockname()[1]
                self.url = f"ws://127.0.0.1:{cong}"
                self._san_sang.set()
                await self._dung.wait()

        self._vong = asyncio.new_event_loop()
        asyncio.set_event_loop(self._vong)
        self._vong.run_until_complete(main())

    def __enter__(self):
        self._luong = threading.Thread(target=self._chay, daemon=True)
        self._luong.start()
        if not self._san_sang.wait(10):
            raise RuntimeError("máy chủ Grok giả không khởi động được")
        return self

    def __exit__(self, *exc):
        if self._vong and self._dung:
            self._vong.call_soon_threadsafe(self._dung.set)
        self._luong.join(timeout=5)


def nap_grok_module(tai_khoan, ws_co_so):
    mod = grok_module.Module(MANIFEST_GROK)
    chay(mod.nap({
        "tai_khoan": list(tai_khoan),
        "ws_url": ws_co_so,
        "co_so_url": "http://127.0.0.1:9",
    }))
    return mod


def test_1_grok_model_bo_sung_xuat_hien_va_ghi_de_nhan(monkeypatch):
    """model_bo_sung xuất hiện trong models() và ghi đè nhãn nếu trùng."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [
        {"id": "grok/expert-2", "nhan": "Grok Expert 2"},
        {"id": "grok/fast", "nhan": "Siêu nhanh"},
    ] if mod_id == "grok-web" else [])

    mod = nap_grok_module(["sso=cookie_test; x-userid=uid_1"], "ws://127.0.0.1:9")
    danh_sach = chay(mod.models())
    ids = {m["id"]: m for m in danh_sach}

    assert "grok/expert-2" in ids
    assert ids["grok/expert-2"]["nhan"] == "Grok Expert 2"
    assert ids["grok/fast"]["nhan"] == "Siêu nhanh"


def test_2_grok_chat_model_bo_sung_gui_dung_ma(monkeypatch):
    """Chat tới model bổ sung gửi đúng mã sang kết nối WebSocket."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [
        {"id": "grok/expert-2", "nhan": "Grok Expert 2"}
    ] if mod_id == "grok-web" else [])

    with MayChuGrokGia() as may_chu:
        mod = nap_grok_module(["sso=cookie_test; x-userid=uid_1"], may_chu.url)
        ma, _, raw = chay(mod.chat({
            "model": "grok/expert-2",
            "messages": [{"role": "user", "content": "Alo"}],
        }, stream=False))

        assert ma == 200
        assert len(may_chu.nhat_ky) == 1
        assert may_chu.nhat_ky[0]["che_do"] == "expert-2"


def test_3_grok_chat_model_la_tra_400_model_not_found(monkeypatch):
    """Chat tới model lạ không có trong cả tĩnh lẫn bổ sung trả 400 rõ ràng."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [])
    mod = nap_grok_module(["sso=cookie_test; x-userid=uid_1"], "ws://127.0.0.1:9")

    ma, _, raw = chay(mod.chat({
        "model": "grok/khong-ton-tai-xyz",
        "messages": [{"role": "user", "content": "Alo"}],
    }, stream=False))

    assert ma == 400
    du_lieu = json.loads(raw.decode("utf-8"))
    assert du_lieu["error"]["code"] == "model_not_found"
    assert du_lieu["error"]["type"] == "invalid_request_error"
    assert "khong-ton-tai-xyz" in du_lieu["error"]["message"]


def test_4_grok_chat_auto_va_fast(monkeypatch):
    """auto và fast đều hợp lệ: auto chuyển về fast."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [])
    with MayChuGrokGia() as may_chu:
        mod = nap_grok_module(["sso=cookie_test; x-userid=uid_1"], may_chu.url)

        # grok/auto
        ma1, _, _ = chay(mod.chat({
            "model": "grok/auto",
            "messages": [{"role": "user", "content": "Alo"}],
        }, stream=False))
        assert ma1 == 200
        assert may_chu.nhat_ky[-1]["che_do"] == "fast"

        # grok/fast
        ma2, _, _ = chay(mod.chat({
            "model": "grok/fast",
            "messages": [{"role": "user", "content": "Alo"}],
        }, stream=False))
        assert ma2 == 200
        assert may_chu.nhat_ky[-1]["che_do"] == "fast"


# ---------------------------------------------------------------------------
# Test claude-web
# ---------------------------------------------------------------------------

TEN_CLW = "claude_web_nv102_test"
THU_MUC_CLW = GOC / "modules" / "claude-web"


def _tai_claude():
    for ten in [t for t in list(sys.modules) if t == TEN_CLW or t.startswith(TEN_CLW + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_CLW)
    goi.__path__ = [str(THU_MUC_CLW)]
    sys.modules[TEN_CLW] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_CLW}.{ten_tep}", THU_MUC_CLW / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_CLW}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_CLW}.module"],
            sys.modules[f"{TEN_CLW}.client"],
            sys.modules[f"{TEN_CLW}.converter"])


clw_module, clw_client, clw_converter = _tai_claude()
MANIFEST_CLW = json.loads((THU_MUC_CLW / "manifest.json").read_text(encoding="utf-8"))


def tao_transport_claude(nhat_ky):
    def xu_ly(req: httpx.Request):
        duong = req.url.path
        if duong == "/api/organizations":
            return httpx.Response(200, json=[{"uuid": "org-test-1", "capabilities": ["chat"]}])
        if "/chat_conversations" in duong and not duong.endswith("/completion"):
            return httpx.Response(200, json={"uuid": "conv-test-1"})
        if duong.endswith("/completion"):
            than = json.loads(req.content.decode("utf-8")) if req.content else {}
            nhat_ky.append({"loai": "completion", "than": than})
            sk = b'data: {"completion": "Ph\\xc3\\xa2n h\\xc3\\xb4i Claude"}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, content=sk, headers={"Content-Type": "text/event-stream"})
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(xu_ly)


def nap_claude_module(monkeypatch, transport, cookies):
    mod = clw_module.Module(MANIFEST_CLW)
    mod.nap({"cookies": list(cookies)})
    for acc in mod._accs:
        acc.may_khach._khach = httpx.AsyncClient(transport=transport)
    return mod


def test_5_claude_model_bo_sung_xuat_hien_va_ghi_de_nhan(monkeypatch):
    """model_bo_sung của claude-web xuất hiện trong models() và ghi đè nhãn."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [
        {"id": "clw/model-moi-9", "nhan": "Model Mới 9"},
        {"id": "clw/sonnet", "nhan": "Sonnet Bản Mới"},
    ] if mod_id == "claude-web" else [])

    transport = tao_transport_claude([])
    mod = nap_claude_module(monkeypatch, transport, ["sessionKey=sk-test-12345678"])

    danh_sach = chay(mod.models())
    ids = {m["id"]: m for m in danh_sach}

    assert "clw/model-moi-9" in ids
    assert ids["clw/model-moi-9"]["nhan"] == "Model Mới 9"
    assert ids["clw/sonnet"]["nhan"] == "Sonnet Bản Mới"


def test_6_claude_chat_model_bo_sung_gui_dung_ma(monkeypatch):
    """Chat tới model bổ sung gửi đúng tên model lên claude.ai."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [
        {"id": "clw/model-moi-9", "nhan": "Model Mới 9"}
    ] if mod_id == "claude-web" else [])

    nhat_ky = []
    transport = tao_transport_claude(nhat_ky)
    mod = nap_claude_module(monkeypatch, transport, ["sessionKey=sk-test-12345678"])

    ma, _, raw = chay(mod.chat({
        "model": "clw/model-moi-9",
        "messages": [{"role": "user", "content": "Alo"}],
    }, stream=False))

    assert ma == 200
    assert len(nhat_ky) == 1
    assert nhat_ky[0]["than"].get("model") == "model-moi-9"


def test_7_claude_model_bo_sung_hong_khong_nem(monkeypatch):
    """model_bo_sung hỏng hoặc rỗng không làm vỡ models(), vẫn giữ bảng tĩnh."""
    monkeypatch.setattr(model_bo_sung, "doc", lambda mod_id: [
        {"khong_co_id": 123},
        None,
    ] if mod_id == "claude-web" else [])

    transport = tao_transport_claude([])
    mod = nap_claude_module(monkeypatch, transport, ["sessionKey=sk-test-12345678"])

    danh_sach = chay(mod.models())
    assert len(danh_sach) > 0
    ids = [m["id"] for m in danh_sach]
    assert "clw/sonnet" in ids
