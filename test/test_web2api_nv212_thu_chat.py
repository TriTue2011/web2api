"""NV212 — nút «THỬ CHAT» thật cho từng nhà cung cấp AI (tách khỏi «Kiểm tra» sức khoẻ).

Đường quản trị POST /api/modules/{id}/thu-chat: gửi một câu hỏi ngắn qua ĐÚNG
đường /v1/chat/completions nội bộ (cùng định tuyến/phân loại lỗi như người dùng
thật, không đường tắt), trả {ok, model, do_tre_ms, tra_loi_ngan, ma_loi,
thong_diep_ngan} — không lộ token/khoá/thân lỗi thô của nhà cung cấp.

Chạy qua lõi thật (TestClient), module GIẢ trong tiến trình (không mạng ngoài).
"""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app

_KHOA_QT = "khoa-qt-nv212-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE_GIA = "gia-nv212"


class _ModuleGia:
    """Module giả: chat trả (ma, headers, noi_dung) như module thật."""

    def __init__(self, ma=200, tre=0.0, than_loi=b'{"error":{"message":"gia"}}',
                 ds_model=None, noi_dung_ok=None):
        self.manifest = {"id": _MODULE_GIA}
        self.tien_to = _MODULE_GIA
        self._ma = ma
        self._tre = tre
        self._than_loi = than_loi
        self._ds_model = ds_model if ds_model is not None else [
            {"id": f"{_MODULE_GIA}/mo-hinh-a", "kha_nang": ["chat"]},
            {"id": f"{_MODULE_GIA}/mo-hinh-b"},
        ]
        self._noi_dung_ok = noi_dung_ok
        self.luot_chat = []

    async def models(self):
        return [dict(m) for m in self._ds_model]

    async def chat(self, body, stream):
        self.luot_chat.append(dict(body))
        if self._tre:
            await asyncio.sleep(self._tre)
        if self._ma != 200:
            return (self._ma, {"content-type": "application/json"}, self._than_loi)
        noi = self._noi_dung_ok or {
            "id": "chatcmpl-gia", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                         "finish_reason": "stop"}],
            "usage": {}}
        return (200, {"content-type": "application/json"}, json.dumps(noi).encode())


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_THU_CHAT_GIAN_CACH_GIAY", "0")
    from app import tuyen as tuyen_mod
    tuyen_mod._THU_CHAT_LAN_CUOI.clear()
    tuyen_mod._THU_CHAT_KHOA.clear()
    from app.so_module import SoModule
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        yield client


def _cai(client, mod=None):
    mod = mod or _ModuleGia()
    so = client.app.state.so
    so.catalog[_MODULE_GIA] = SimpleNamespace(tien_to=_MODULE_GIA)
    so.dang_chay[_MODULE_GIA] = mod
    return mod


_DUONG = f"/api/modules/{_MODULE_GIA}/thu-chat"


def test_thu_chat_dat(moi_truong):
    mod = _cai(moi_truong)
    r = moi_truong.post(_DUONG, headers=_QT, json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    assert j["model"] == f"{_MODULE_GIA}/mo-hinh-a"
    assert isinstance(j["do_tre_ms"], int) and j["do_tre_ms"] >= 0
    assert j["tra_loi_ngan"] == "OK"
    assert j["ma_loi"] is None
    # Đi đúng đường chat như người dùng thật: thân gửi module có messages, max_tokens nhỏ, không stream
    assert len(mod.luot_chat) == 1
    than = mod.luot_chat[0]
    assert than["messages"] == [{"role": "user", "content": "Trả lời đúng một từ: OK"}]
    assert than["stream"] is False
    assert isinstance(than.get("max_tokens"), int) and than["max_tokens"] <= 64


def test_thu_chat_403_bi_chan_chong_bot(moi_truong):
    _cai(moi_truong, _ModuleGia(ma=403))
    r = moi_truong.post(_DUONG, headers=_QT, json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is False
    assert j["ma_loi"] == "bi_chan_chong_bot"
    assert "chống bot" in j["thong_diep_ngan"]


def test_thu_chat_429_het_han_muc(moi_truong):
    _cai(moi_truong, _ModuleGia(ma=429))
    j = moi_truong.post(_DUONG, headers=_QT, json={}).json()
    assert j["ok"] is False and j["ma_loi"] == "het_han_muc"
    assert "hạn mức" in j["thong_diep_ngan"]


def test_thu_chat_401_token_bi_tu_choi(moi_truong):
    _cai(moi_truong, _ModuleGia(ma=401))
    j = moi_truong.post(_DUONG, headers=_QT, json={}).json()
    assert j["ok"] is False and j["ma_loi"] == "token_bi_tu_choi"


def test_thu_chat_500_dich_vu_loi(moi_truong):
    _cai(moi_truong, _ModuleGia(ma=500))
    j = moi_truong.post(_DUONG, headers=_QT, json={}).json()
    assert j["ok"] is False and j["ma_loi"] == "dich_vu_loi"


def test_thu_chat_khong_lo_bi_mat(moi_truong):
    than = b'{"error":{"message":"sai khoa-bi-mat-gia-xyz"}}'
    _cai(moi_truong, _ModuleGia(ma=401, than_loi=than))
    r = moi_truong.post(_DUONG, headers=_QT, json={})
    assert "khoa-bi-mat-gia-xyz" not in r.text
    assert "sai khoa" not in r.text


def test_thu_chat_het_gio(moi_truong, monkeypatch):
    monkeypatch.setenv("WEB2API_THU_CHAT_TRAN_GIAY", "0.3")
    _cai(moi_truong, _ModuleGia(tre=5.0))
    j = moi_truong.post(_DUONG, headers=_QT, json={}).json()
    assert j["ok"] is False and j["ma_loi"] == "het_gio"


def test_thu_chat_chong_chong_lap(moi_truong):
    # Hai lượt chồng nhau: lượt sau bị 429, không dồn vào module
    mod = _cai(moi_truong, _ModuleGia(tre=1.0))
    ket_qua = {}

    def luot1():
        ket_qua["mot"] = moi_truong.post(_DUONG, headers=_QT, json={})

    t = threading.Thread(target=luot1)
    t.start()
    import time as _time
    _time.sleep(0.3)
    r2 = moi_truong.post(_DUONG, headers=_QT, json={})
    t.join()
    assert r2.status_code == 429, r2.text
    assert r2.json()["error"]["code"] == "thu_chat_dang_chay"
    assert len(mod.luot_chat) == 1
    assert ket_qua["mot"].status_code == 200


def test_thu_chat_chong_bam_don(moi_truong, monkeypatch):
    monkeypatch.setenv("WEB2API_THU_CHAT_GIAN_CACH_GIAY", "5")
    _cai(moi_truong)
    assert moi_truong.post(_DUONG, headers=_QT, json={}).status_code == 200
    r2 = moi_truong.post(_DUONG, headers=_QT, json={})
    assert r2.status_code == 429, r2.text
    assert r2.json()["error"]["code"] == "thu_chat_qua_nhanh"


def test_thu_chat_model_khong_thuoc_module(moi_truong):
    _cai(moi_truong)
    r = moi_truong.post(_DUONG, headers=_QT, json={"model": "module-khac/mo-hinh-x"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "model_khong_thuoc_module"


def test_thu_chat_chon_model_chi_dinh(moi_truong):
    mod = _cai(moi_truong)
    r = moi_truong.post(_DUONG, headers=_QT, json={"model": f"{_MODULE_GIA}/mo-hinh-b"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["model"] == f"{_MODULE_GIA}/mo-hinh-b"
    assert mod.luot_chat[0]["model"] == f"{_MODULE_GIA}/mo-hinh-b"


def test_thu_chat_chua_dang_nhap(moi_truong):
    _cai(moi_truong)
    r = moi_truong.post(_DUONG, json={})
    assert r.status_code in (401, 403), r.text


def test_thu_chat_module_chua_lien_ket(moi_truong):
    r = moi_truong.post("/api/modules/khong-co-dau/thu-chat", headers=_QT, json={})
    assert r.status_code == 404, r.text


def test_thu_chat_tra_loi_dai_bi_cat(moi_truong):
    noi = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "x" * 500},
                        "finish_reason": "stop"}]}
    _cai(moi_truong, _ModuleGia(noi_dung_ok=noi))
    j = moi_truong.post(_DUONG, headers=_QT, json={}).json()
    assert j["ok"] is True
    assert len(j["tra_loi_ngan"]) <= 80
