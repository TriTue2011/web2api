"""Test NV77 — gemini-web (NV219 cập nhật, NV256): tính năng gắn danh tính vào
tài khoản AI đã BỎ theo quyết định chủ máy 21/10 («BỎ danh tính» khỏi phần AI)
— các test gắn danh_tinh_id vào nap() của module đã xoá. File giữ lại các
hợp đồng còn hiệu lực:
- Tài khoản cũ (cookie chuỗi) chạy y như trước.
- cai() với cookies cũ giữ đúng chuỗi gốc, không lộ cookie thô.
- Manifest khai can_module ["vnc-xa"].
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from app import kho
from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv77_test"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))

def chay(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Máy chủ giả tối thiểu: init và batchexecute luôn 200.
# ---------------------------------------------------------------------------

def _lam_gia():
    import httpx

    def xu_ly(req):
        duong = req.url.path
        if duong == "/":
            return httpx.Response(200, text='<html>"SNlM0e":"snl-gia"</html>')
        if duong == "/_/BardChatUi/data/batchexecute":
            inner = json.dumps([None, None, None, None, [["cid-1", "xin chao"]]])
            than = json.dumps(["wrb.fr", "hNvQH", inner, None, None, None, "generic"])
            return httpx.Response(200, text=")]}'\n" + than)
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def ck(psid):
    return f"__Secure-1PSID={psid}; __Secure-1PSIDTS=ts-{psid}"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(data))
    transport = _lam_gia()

    def tao_client(self, psid, psidts):
        return mod_client.GeminiWebClient(psid, psidts, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    return data


def _mod(moi_truong):
    mod = Module(MANIFEST)
    mod.kho = kho.kho_rieng("gemini-web")
    return mod


# ---------------------------------------------------------------------------
# Tương thích ngược: tài khoản cũ không gắn vẫn chạy y như trước.
# ---------------------------------------------------------------------------

def test_tai_khoan_cu_chay_khong_can_danh_tinh(moi_truong):
    """NV219 cập nhật (NV256): tài khoản cookie cũ vẫn nap + chat 200 sau khi
    tính năng gắn danh tính bị gỡ — không còn trường danh_tinh_id trên tài
    khoản, cấu hình nap() bỏ qua khoá "danh_tinh" lạ."""
    mod = _mod(moi_truong)
    mod.nap({"cookies": [ck("psid-cu")]})
    assert len(mod._tai_khoan) == 1
    tk = mod._tai_khoan[0]
    assert not hasattr(tk, "danh_tinh_id")
    mod.nap({"cookies": [ck("psid-cu")], "danh_tinh": ["dt-la-bo-qua"]})
    assert len(mod._tai_khoan) == 1
    ma, _, _ = chay(mod.chat({"model": "gemini-web/3.5-flash",
                              "messages": [{"role": "user", "content": "hi"}]}, False))
    assert ma == 200
    # Hiển thị vẫn dạng che bí mật như cũ.
    assert tk.ten_che().startswith("…")


def test_cai_cu_luu_dung_chuoi_goc(moi_truong):
    """cai() với cookies cũ: ok, cau_hinh_luu giữ đúng chuỗi gốc từng tài khoản."""
    mod = _mod(moi_truong)
    kq = chay(mod.cai({"cookies": [ck("psid-1"), ck("psid-2")]}))
    assert kq["ok"] is True, kq
    luu = kq["cau_hinh_luu"]["cookies"]
    assert luu == [ck("psid-1"), ck("psid-2")]
    # Không lộ cookie thô trong kết quả từng khoá.
    for muc in kq["ket_qua_tung_khoa"]:
        assert "psid-1" not in muc["khoa"] and "psid-2" not in muc["khoa"]


# ---------------------------------------------------------------------------
# Gắn danh tính.
# ---------------------------------------------------------------------------

def test_manifest_khai_can_module_vnc_xa():
    assert MANIFEST["can_module"] == ["vnc-xa"]
    assert MANIFEST["hang"] == "A"
    assert kiem_manifest(MANIFEST) is None
