"""Test NV102-LÕI — module API và làm hết hạn đệm (gemini-api, grok-api, ModuleTuXa).

Máy chủ HTTP giả trên 127.0.0.1, không ra ngoài mạng.
Chuỗi khoá dùng trong allowlist .gitleaks.toml (sk-khoa-tu-xa-RIENG-7777, AIzaSyTOT-mot-000000000000000000000001).
Kiểm tra ca 12 và ca 14 theo đặc tả NV102.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.chuyen_tiep import ChuyenTiepOpenAI
from app.tu_xa import ModuleTuXa

GOC = Path(__file__).resolve().parents[1]

KHOA_GEMINI_GIA = "AIzaSyTOT-mot-000000000000000000000001"
KHOA_GROK_GIA = "sk-khoa-tu-xa-RIENG-7777"


def _tai_module(ten_goi: str, thu_muc: Path):
    for ten in [t for t in list(sys.modules) if t == ten_goi or t.startswith(ten_goi + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(ten_goi)
    goi.__path__ = [str(thu_muc)]
    sys.modules[ten_goi] = goi
    spec = importlib.util.spec_from_file_location(f"{ten_goi}.module", thu_muc / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{ten_goi}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Máy chủ giả dùng chung ───────────────────────────────────────────────────


class MayChuGia(ThreadingHTTPServer):
    def __init__(self, handler_cls):
        super().__init__(("127.0.0.1", 0), handler_cls)
        self.url = f"http://127.0.0.1:{self.server_port}"


# ── Ca 12: ChuyenTiepOpenAI và ModuleTuXa và grok-api ─────────────────────────


def test_chuyen_tiep_lam_het_han_dem():
    async def _chay():
        ct = ChuyenTiepOpenAI(
            ten="Test",
            tien_to="test",
            base_url="http://127.0.0.1:9999",
            khoa=["k1"],
        )
        ct._dem_models = [{"id": "test/m1"}]
        ct._dem_luc = 1000.0

        ct.lam_het_han_dem_models()
        assert ct._dem_luc == 0.0
        # Bản đệm cũ vẫn được giữ phòng khi gọi lỗi
        assert ct._dem_models == [{"id": "test/m1"}]
        await ct.dung()

    asyncio.run(_chay())


def test_module_tu_xa_lam_moi_model():
    async def _chay():
        ban_ghi = {
            "id": "tu-xa-test",
            "ten": "Từ Xa Test",
            "tien_to": "tx",
            "base_url": "http://127.0.0.1:9999/v1",
        }
        mod = ModuleTuXa(ban_ghi, "k1")
        mod._ct._dem_models = [{"id": "tx/m1"}]
        mod._ct._dem_luc = 5000.0

        await mod.lam_moi_model()
        assert mod._ct._dem_luc == 0.0
        assert mod._ct._dem_models == [{"id": "tx/m1"}]
        await mod.dung()

    asyncio.run(_chay())


def test_grok_api_lam_moi_model():
    async def _chay():
        thu_muc = GOC / "modules" / "grok-api"
        mod_pkg = _tai_module("grok_api_test_pkg", thu_muc)
        manifest = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
        m = mod_pkg.Module(manifest)
        m.nap({"api_keys": [KHOA_GROK_GIA]})
        m._ct._dem_models = [{"id": "grok-api/grok-1"}]
        m._ct._dem_luc = 3000.0

        await m.lam_moi_model()
        assert m._ct._dem_luc == 0.0
        assert m._ct._dem_models == [{"id": "grok-api/grok-1"}]
        await m.dung()

    asyncio.run(_chay())


# ── Ca 12: gemini-api lam_moi_model() ─────────────────────────────────────────


def test_gemini_api_lam_moi_model(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    # Dựng handler giả cho Gemini
    danh_sach_models_tra_ve = [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    ]
    ma_tra_ve = 200

    class GeminiHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal ma_tra_ve, danh_sach_models_tra_ve
            duong = self.path.split("?")[0].rstrip("/")
            if duong.endswith("/models"):
                if ma_tra_ve != 200:
                    self.send_response(ma_tra_ve)
                    self.end_headers()
                    self.wfile.write(b'{"error": "server error"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if "openai" in duong:
                    resp = {"data": [{"id": m["name"].removeprefix("models/")} for m in danh_sach_models_tra_ve]}
                else:
                    resp = {"models": danh_sach_models_tra_ve}
                self.wfile.write(json.dumps(resp).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = MayChuGia(GeminiHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()

    thu_muc = GOC / "modules" / "gemini-api"
    mod_pkg = _tai_module("gemini_api_test_pkg", thu_muc)
    manifest = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
    m = mod_pkg.Module(manifest)
    m.nap({
        "api_keys": [KHOA_GEMINI_GIA],
        "base_url": server.url,
    })

    async def _chay():
        try:
            # Lần đầu lấy danh sách
            ds1 = await m.models()
            assert any(x["id"] == "gemini/gemini-2.5-flash" for x in ds1)

            # Cập nhật danh sách trên máy chủ giả
            danh_sach_models_tra_ve.append(
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]}
            )

            # Khi chưa lam_moi_model, do còn đệm nên models() vẫn trả ds cũ
            ds_dem = await m.models()
            assert not any(x["id"] == "gemini/gemini-2.5-pro" for x in ds_dem)

            # Gọi lam_moi_model -> hết hạn đệm và tải lại ngay
            await m.lam_moi_model()
            ds2 = await m.models()
            assert any(x["id"] == "gemini/gemini-2.5-pro" for x in ds2)

            # Khi máy chủ giả trả 500 ở lần làm mới: lam_moi_model ném lỗi VÀ models() vẫn trả được bản cũ
            nonlocal ma_tra_ve
            ma_tra_ve = 500
            with pytest.raises(Exception):
                await m.lam_moi_model()

            ds_van_con = await m.models()
            assert any(x["id"] == "gemini/gemini-2.5-pro" for x in ds_van_con)

        finally:
            await m.dung()
            server.shutdown()
            server.server_close()

    asyncio.run(_chay())


# ── Ca 14: Giao diện web/He-thong-AI.dc.html ───────────────────────────────────


def test_14_giao_dien_he_thong_ai():
    p = GOC / "web" / "He-thong-AI.dc.html"
    assert p.is_file()
    noi_dung = p.read_text(encoding="utf-8")

    # Kiểm tra lời gọi API làm mới model
    assert "/lam-moi-model" in noi_dung
    assert "encodeURIComponent(pvCur.id)" in noi_dung

    # Kiểm tra nhãn nút
    assert 'id="nut-lam-moi-md"' in noi_dung

