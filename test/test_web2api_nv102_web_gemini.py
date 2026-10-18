"""Test NV102-WEB cho module gemini-web: khám phá model từ HTML trang chủ.

Kiểm tra:
1. HTML trang chủ có khối <script id="model-list"> chứa model mới gemini-9-moi:
   models() nhận ra gemini-web/gemini-9-moi, nhãn hiển thị đúng.
2. Chat tới gemini-web/gemini-9-moi thành công (200), f.req gửi đúng tên model.
3. Chat tới model không có trong danh sách trả về lỗi 400 rõ ràng.
4. Lỗi khối model-list (không có khối hoặc JSON hỏng): giữ bảng dự phòng mặc định, không vỡ.
5. Lọc dữ liệu không tin được qua trich_model_tu_html:
   - Mã dài hơn 128 ký tự bị loại bỏ
   - Mã chứa ký tự điều khiển bị loại bỏ
   - Danh sách cắt ở trần TOI_DA_MODEL.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app.lam_moi_model import TOI_DA_MODEL

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv102_test"


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
            sys.modules[f"{TEN_GOI}.client"],
            sys.modules[f"{TEN_GOI}.converter"])


mod_module, mod_client, mod_converter = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


class KhoGia:
    def __init__(self):
        self.du_lieu = {}

    def doc(self, ten: str):
        return self.du_lieu.get(ten)

    def luu(self, ten: str, giatri):
        self.du_lieu[ten] = giatri


def tao_html_trang_chu(ds_models=None, json_hong=False):
    snlm0e = 'WIZ_global_data = {"SNlM0e":"snlm0e-gia-xyz"};'
    if json_hong:
        khoi_models = '<script id="model-list">{"models": [chua-dong-json'
    elif ds_models is not None:
        khoi_models = f'<script id="model-list">{json.dumps({"models": ds_models})}</script>'
    else:
        khoi_models = ""
    return f"<html><head><script>{snlm0e}</script>{khoi_models}</head><body>Gemini</body></html>"


def tao_transport_gia(html_trang_chu=None, phan_hoi_chat=None):
    nhat_ky = []

    def xu_ly(req: httpx.Request):
        duong = req.url.path
        if duong == "/":
            html = html_trang_chu() if callable(html_trang_chu) else (html_trang_chu or tao_html_trang_chu())
            return httpx.Response(200, text=html)

        if duong == "/_/BardChatUi/data/batchexecute":
            content_str = req.content.decode("utf-8") if req.content else ""
            nhat_ky.append({"loai": "batchexecute", "content": content_str})
            if callable(phan_hoi_chat):
                return phan_hoi_chat(req)

            inner = json.dumps([None, None, None, None, [["cid-1", "Phản hồi Gemini web"]]])
            khung = json.dumps(["wrb.fr", "hNvQH", inner, None, None, None, "generic"])
            than = f")]}}'\n\n{len(khung)}\n{khung}\n"
            return httpx.Response(200, text=than, headers={"Content-Type": "application/json"})

        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(xu_ly)
    transport.nhat_ky = nhat_ky
    return transport


def nap_test_module(monkeypatch, transport, cookies):
    def tao_client(self, psid, psidts=None):
        return mod_client.GeminiWebClient(psid, psidts, transport=transport)

    monkeypatch.setattr(Module, "_tao_client", tao_client)
    mod = Module(MANIFEST)
    mod.kho = KhoGia()
    mod.nap({"cookies": list(cookies)})
    return mod


def test_1_nhan_ra_model_moi_tu_html(monkeypatch):
    """HTML trang chủ chứa gemini-9-moi → models() hiển thị model mới."""
    html = tao_html_trang_chu(["gemini-9-moi", "gemini-3-flash"])
    transport = tao_transport_gia(html_trang_chu=html)
    mod = nap_test_module(monkeypatch, transport, ["__Secure-1PSID=psid-tot"])

    chay(mod.lam_moi_model())

    danh_sach = chay(mod.models())
    ids = [m["id"] for m in danh_sach]
    assert "gemini-web/gemini-9-moi" in ids
    item = next(m for m in danh_sach if m["id"] == "gemini-web/gemini-9-moi")
    assert item["nhan"] == "gemini-9-moi"
    assert len(item["available_cho"]) == 1


def test_2_chat_model_moi_thanh_cong(monkeypatch):
    """Chat với model gemini-9-moi thành công và gửi đúng mã trong f.req."""
    html = tao_html_trang_chu(["gemini-9-moi"])
    transport = tao_transport_gia(html_trang_chu=html)
    mod = nap_test_module(monkeypatch, transport, ["__Secure-1PSID=psid-tot"])
    chay(mod.lam_moi_model())

    ma, _, raw = chay(mod.chat({
        "model": "gemini-web/gemini-9-moi",
        "messages": [{"role": "user", "content": "Xin chào"}],
    }, stream=False))

    assert ma == 200
    du_lieu = json.loads(raw.decode("utf-8"))
    assert du_lieu["choices"][0]["message"]["content"] == "Phản hồi Gemini web"

    # Kiểm tra f.req gửi đúng tên model
    reqs = [r for r in transport.nhat_ky if r["loai"] == "batchexecute"]
    assert len(reqs) == 1
    assert "gemini-9-moi" in reqs[0]["content"]


def test_3_chat_model_khong_co_tra_400(monkeypatch):
    """Chat tới model lạ không có trong danh sách trả lỗi 400 rõ ràng."""
    transport = tao_transport_gia(html_trang_chu=tao_html_trang_chu())
    mod = nap_test_module(monkeypatch, transport, ["__Secure-1PSID=psid-tot"])

    ma, _, raw = chay(mod.chat({
        "model": "gemini-web/khong-ton-tai-xyz",
        "messages": [{"role": "user", "content": "Xin chào"}],
    }, stream=False))

    assert ma == 400
    du_lieu = json.loads(raw.decode("utf-8"))
    assert "khong-ton-tai-xyz" in du_lieu["error"]["message"]


def test_4_loi_khoi_giu_nguyen_bang_cu(monkeypatch):
    """HTML không có khối hoặc JSON hỏng: giữ bảng dự phòng mặc định, không vỡ."""
    # Khối JSON hỏng
    html_hong = tao_html_trang_chu(json_hong=True)
    transport = tao_transport_gia(html_trang_chu=html_hong)
    mod = nap_test_module(monkeypatch, transport, ["__Secure-1PSID=psid-tot"])

    with pytest.raises(RuntimeError):
        chay(mod.lam_moi_model())

    danh_sach = chay(mod.models())
    assert len(danh_sach) > 0
    ids = [m["id"] for m in danh_sach]
    assert "gemini-web/gemini-3-flash" in ids


def test_5_loc_ma_khong_tin_duoc():
    """trich_model_tu_html loại bỏ mã quá dài, chứa ký tự điều khiển, và cắt ở trần TOI_DA_MODEL."""
    ma_dai = "m" * 150
    ma_chua_ctrl = "gemini\nflash"
    nhieu_model = [f"model-{i}" for i in range(TOI_DA_MODEL + 50)]

    html = (
        f'<script id="model-list">{json.dumps({"models": [ma_dai, ma_chua_ctrl, *nhieu_model]})}</script>'
    )
    trich = mod_client.trich_model_tu_html(html)

    # Không chứa mã quá dài và mã chứa ký tự điều khiển
    assert ma_dai not in trich
    assert ma_chua_ctrl not in trich
    # Số lượng không vượt quá trần TOI_DA_MODEL
    assert len(trich) <= TOI_DA_MODEL
