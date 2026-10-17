"""Test NV90 (phần B) cho module grok-api MỚI: Grok qua API chính thức của xAI.

- manifest hợp chuẩn lõi (`kiem_manifest`), `mang_ra` khai đủ `api.x.ai`,
  `yeu_cau_goi` đúng gói dùng.
- `cai()`: kiểm từng khoá bằng GET /models THẬT (máy chủ giả); khoá sai →
  ok=false; base_url ghi đè qua `app/tu_xa.py` (chống SSRF); mặc định
  https://api.x.ai/v1.
- `models()`: danh sách lấy từ nhà cung cấp, không viết cứng tên model.
- `chat()`: xoay khoá khi 401 (phân loại bằng cấu trúc mã HTTP: 401/403 khoá
  sai, 404 model không có, 429 hạn mức); stream SSE.
- Khoá không lộ trong log/phản hồi (dạng che …abcd).

Máy chủ xAI là GIẢ (http.server trên 127.0.0.1 +
WEB2API_TEST_CHO_PHEP_LOOPBACK=1). Không Internet, không khoá thật.
Chuỗi khoá trong test đều GIẢ rõ ràng (dạng xai-GIA-…).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-api"
TEN_GOI = "grok_api_nv90_mod_test"

KHOA_GIA_OK = "xai-GIA-ok-0001"
KHOA_GIA_OK_2 = "xai-GIA-ok-0002"
KHOA_GIA_SAI = "xai-GIA-sai-0003"
MODEL_GIA_1 = "grok-gia-chat-1"
MODEL_GIA_2 = "grok-gia-chat-2"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    spec = importlib.util.spec_from_file_location(f"{TEN_GOI}.module", THU_MUC / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{TEN_GOI}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


# ───────────── máy chủ xAI giả ─────────────

class _TrangThai:
    """Ghi lại header Authorization của yêu cầu chat gần nhất (để kiểm xoay khoá)."""
    khoa_chat_cuoi: str | None = None


class _XuLyXAI(BaseHTTPRequestHandler):
    def _tra(self, ma: int, body: bytes, kieu: str = "application/json"):
        self.send_response(ma)
        self.send_header("Content-Type", kieu)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _khoa(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[len("Bearer "):] if auth.startswith("Bearer ") else ""

    def _kiem_khoa(self) -> bool:
        khoa = self._khoa()
        if khoa in (KHOA_GIA_OK, KHOA_GIA_OK_2):
            return True
        self._tra(401, json.dumps({"error": {"message": "Incorrect API key provided",
                                             "type": "authentication_error",
                                             "code": 401}}).encode("utf-8"))
        return False

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            if not self._kiem_khoa():
                return
            body = json.dumps({"object": "list", "data": [
                {"id": MODEL_GIA_1, "object": "model", "created": 1760000000,
                 "owned_by": "xai"},
                {"id": MODEL_GIA_2, "object": "model", "created": 1760000000,
                 "owned_by": "xai"},
            ]}).encode("utf-8")
            self._tra(200, body)
            return
        self._tra(404, b"{}")

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._tra(404, b"{}")
            return
        if not self._kiem_khoa():
            return
        _TrangThai.khoa_chat_cuoi = self._khoa()
        dai = int(self.headers.get("Content-Length") or 0)
        than = json.loads(self.rfile.read(dai) or b"{}")
        model = than.get("model", MODEL_GIA_1)
        if than.get("stream"):
            sse = (
                'data: {"id":"chatcmpl-gia","object":"chat.completion.chunk","created":1,'
                f'"model":"{model}","choices":[{{"index":0,"delta":{{"role":"assistant"}},'
                '"finish_reason":null}}]}\n\n'
                'data: {"id":"chatcmpl-gia","object":"chat.completion.chunk","created":1,'
                f'"model":"{model}","choices":[{{"index":0,"delta":{{"content":"Xin chao"}},'
                '"finish_reason":null}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            self._tra(200, sse, "text/event-stream")
            return
        body = json.dumps({
            "id": "chatcmpl-gia", "object": "chat.completion", "created": 1,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": "Xin chào từ Grok giả"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }).encode("utf-8")
        self._tra(200, body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def may_chu_xai(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    may_chu = ThreadingHTTPServer(("127.0.0.1", 0), _XuLyXAI)
    import threading
    t = threading.Thread(target=may_chu.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{may_chu.server_port}/v1"
    may_chu.shutdown()
    t.join()


def _mod_moi(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    return Module(dict(MANIFEST))


# ───────────── manifest ─────────────

def test_manifest_hop_chuan_loi():
    assert kiem_manifest(MANIFEST) is None
    assert MANIFEST["id"] == "grok-api"
    assert MANIFEST["tien_to"] == "grok-api"
    assert MANIFEST["hang"] == "A"
    assert "api.x.ai" in MANIFEST["tai_nguyen"]["mang_ra"]
    assert "chat" in MANIFEST["cung_cap"]
    assert "httpx" in MANIFEST["yeu_cau_goi"]


# ───────────── cai(): kiểm khoá bằng GET /models thật ─────────────

def test_cai_khoa_hop_le(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK], "base_url": may_chu_xai}))
    assert kq["ok"] is True, kq["loi"]
    assert kq["cau_hinh_luu"]["api_keys"] == [KHOA_GIA_OK]
    assert kq["cau_hinh_luu"]["base_url"] == may_chu_xai
    assert all(m["ok"] for m in kq["ket_qua_tung_khoa"])
    chay(mod.dung())


def test_cai_loai_khoa_sai(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_SAI], "base_url": may_chu_xai}))
    assert kq["ok"] is False
    assert kq["ket_qua_tung_khoa"][0]["ok"] is False
    chay(mod.dung())


def test_cai_giu_khoa_tot_bo_khoa_sai(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_SAI, KHOA_GIA_OK], "base_url": may_chu_xai}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["api_keys"] == [KHOA_GIA_OK]
    chay(mod.dung())


def test_cai_base_url_mac_dinh():
    assert mod_module.chuan_hoa_base_url("") == "https://api.x.ai/v1"
    assert mod_module.chuan_hoa_base_url(None) == "https://api.x.ai/v1"
    assert mod_module.chuan_hoa_base_url("https://api.x.ai/v1/") == "https://api.x.ai/v1"
    assert mod_module.chuan_hoa_base_url("https://proxy.nha/v1") == "https://proxy.nha/v1"
    with pytest.raises(ValueError):
        mod_module.chuan_hoa_base_url("ftp://x.ai/v1")


def test_cai_chan_base_url_ssrf(monkeypatch, tmp_path):
    """base_url trỏ mạng riêng → cai() từ chối (qua app/tu_xa.py)."""
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK], "base_url": "http://192.168.9.9/v1"}))
    assert kq["ok"] is False
    chay(mod.dung())


# ───────────── models(): từ nhà cung cấp, không viết cứng ─────────────

def test_models_lay_tu_nha_cung_cap(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK], "base_url": may_chu_xai}))
    assert kq["ok"]
    mod.nap(kq["cau_hinh_luu"])
    ds = chay(mod.models())
    ids = [m["id"] for m in ds]
    assert f"grok-api/{MODEL_GIA_1}" in ids
    assert f"grok-api/{MODEL_GIA_2}" in ids
    chay(mod.dung())


# ───────────── chat(): xoay khoá theo cấu trúc mã HTTP ─────────────

def test_chat_xoay_khoa_khi_401(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    _TrangThai.khoa_chat_cuoi = None
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK_2], "base_url": may_chu_xai}))
    assert kq["ok"]
    mod.nap(kq["cau_hinh_luu"])
    # Chèn khoá sai lên đầu để lõi phải xoay (mô phỏng thứ tự xoay).
    mod._ct.khoa.insert(0, KHOA_GIA_SAI)
    ma, _hd, noi_dung = chay(mod.chat(
        {"model": f"grok-api/{MODEL_GIA_1}",
         "messages": [{"role": "user", "content": "Xin chào"}]}, False))
    assert ma == 200, noi_dung[:200]
    assert _TrangThai.khoa_chat_cuoi == KHOA_GIA_OK_2
    body = json.loads(noi_dung)
    assert "Grok giả" in body["choices"][0]["message"]["content"]
    chay(mod.dung())


def test_chat_stream(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK], "base_url": may_chu_xai}))
    assert kq["ok"]
    mod.nap(kq["cau_hinh_luu"])
    async def chay_stream():
        ma, _hd, luong = await mod.chat(
            {"model": f"grok-api/{MODEL_GIA_1}", "stream": True,
             "messages": [{"role": "user", "content": "Xin chào"}]}, True)
        ra = b""
        async for manh in luong:
            ra += manh
        return ma, ra

    ma, du_lieu = chay(chay_stream())
    assert ma == 200
    # Lõi chuẩn hoá header của luồng (tieu_de_cho_khach): chỉ kiểm thân SSE.
    assert b"data: [DONE]" in du_lieu
    chay(mod.dung())


def test_phan_loai_loi_theo_cau_truc():
    pl = mod_module.phan_loai_loi
    assert pl(401, {}, {}) == "khoa_hong"
    assert pl(403, {}, {}) == "khoa_hong"
    assert pl(404, {}, {}) == "khong_co_o_khoa"
    assert pl(429, {}, {}) == "het_han_muc"
    assert pl(500, {}, {}) == "may_chu_loi"
    assert pl(200, {}, {}) is None


# ───────────── khoá không lộ ─────────────

def test_khoa_khong_vao_log(monkeypatch, tmp_path, may_chu_xai, caplog):
    mod = _mod_moi(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="web2api.chuyen_tiep.grok-api"):
        kq = chay(mod.cai({"api_keys": [KHOA_GIA_SAI], "base_url": may_chu_xai}))
        assert not kq["ok"]
    ban_log = "\n".join(r.getMessage() for r in caplog.records)
    assert KHOA_GIA_SAI not in ban_log
    assert "…0003" in ban_log or "0003" not in ban_log.replace("…0003", "")
    chay(mod.dung())


def test_suc_khoe_che_khoa(monkeypatch, tmp_path, may_chu_xai):
    mod = _mod_moi(monkeypatch, tmp_path)
    kq = chay(mod.cai({"api_keys": [KHOA_GIA_OK], "base_url": may_chu_xai}))
    assert kq["ok"]
    mod.nap(kq["cau_hinh_luu"])
    sk = chay(mod.suc_khoe())
    assert sk["ok"] is True
    for muc in sk["khoa"]:
        assert KHOA_GIA_OK not in muc["khoa"]
        assert muc["khoa"].startswith("…")
    chay(mod.dung())
