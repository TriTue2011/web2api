"""Test NV113 cho module telegram-bot MỚI: kênh gửi ra (chuẩn `kenh_ra`) tới Telegram.

- manifest hợp chuẩn lõi (`kiem_manifest`), `mang_ra` khai đủ `api.telegram.org`,
  `yeu_cau_goi` đúng gói dùng; địa chỉ gọi cố định, không đọc từ cấu hình người dùng.
- `gui_tin`: gửi đúng chat/chủ đề; tin dài tách ở ranh giới dòng (tối đa 5 tin, dư cắt «…»);
  KHÔNG gửi `parse_mode`; `ok:false`/4xx → thất bại không lộ `description`; 429 → thất bại
  (không ngủ dài); lỗi mạng → thử lại đúng 1 lần, không với 4xx.
- BẢO MẬT: token nằm trong URL → logger httpx/httpcore bị nâng lên WARNING trong lúc gọi;
  caplog chứng minh token không xuất hiện ở bất kỳ log/ngoại lệ/phản hồi nào (kể cả lỗi mạng).
  `kenh_ra()` chỉ trả [{id, ten}] — không token, không chat_id thô.
- Cổng lõi `POST /api/gui/telegram-bot`: thiếu `kenh_id` khi nhiều đích → 400 không gửi gì;
  phạm vi `gui:telegram_bot` (403 khi sai phạm vi, 401 khi không khoá); module chưa cấu hình
  → 503; leo thang quyền qua đường lõi.

Máy chủ Telegram là GIẢ (http.server trên 127.0.0.1 + WEB2API_TEST_CHO_PHEP_LOOPBACK=1).
Không Internet, không token thật. Chuỗi token trong test đều GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import gui_ra as gui_ra_mod
from app import khoa_khach
from app.kham_pha import kiem_manifest
from app.main import app

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "telegram-bot"
TEN_GOI = "telegram_bot_nv113_mod_test"

TOKEN_GIA = "610000001:AA-GIA-telegram-bot-token-cho-test-0000"
CHAT_NHA = 610000001
CHAT_NHA_KHACH = 610000002
DICH_GIA = [
    {"id": "nha", "ten": "Nhà", "chat_id": CHAT_NHA, "chu_de": 7},
    {"id": "khach", "ten": "Phòng khách", "chat_id": CHAT_NHA_KHACH},
]

KHOA_QT = "<redacted>"


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


# ───────────── máy chủ Telegram giả ─────────────

class _XuLyTelegram(BaseHTTPRequestHandler):
    ds_yeu_cau: list = []
    hanh_vi: str = "ok"  # ok | ok_sai | loi_400 | gioi_han_429

    def log_message(self, *a):
        pass

    def _doc_than(self):
        dai = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(dai) or b"{}")

    def do_POST(self):
        than = self._doc_than()
        _XuLyTelegram.ds_yeu_cau.append({"duong": self.path, "than": than})
        hv = _XuLyTelegram.hanh_vi
        if hv == "ok":
            self._tra(200, {"ok": True, "result": {"message_id": len(_XuLyTelegram.ds_yeu_cau)}})
        elif hv == "ok_sai":
            self._tra(200, {"ok": False, "error_code": 400,
                            "description": "Bad Request: GIA-chat khong ton tai"})
        elif hv == "loi_400":
            self._tra(400, {"ok": False, "error_code": 400,
                            "description": "Bad Request: GIA-van ban rong"})
        elif hv == "gioi_han_429":
            self._tra(429, {"ok": False, "error_code": 429,
                            "description": "Too Many Requests: retry after 60",
                            "parameters": {"retry_after": 60}})
        else:
            self._tra(500, {"ok": False})

    def do_GET(self):  # getMe — kiểm token lúc cai()
        _XuLyTelegram.ds_yeu_cau.append({"duong": self.path, "than": {}})
        if _XuLyTelegram.hanh_vi == "ok_sai":
            self._tra(200, {"ok": False, "error_code": 401,
                            "description": "Unauthorized: GIA-token sai"})
        else:
            self._tra(200, {"ok": True, "result": {"id": 1, "is_bot": True,
                                                  "first_name": "BotGiaTest"}})

    def _tra(self, ma, obj):
        du_lieu = json.dumps(obj).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(du_lieu)))
        self.end_headers()
        self.wfile.write(du_lieu)


@pytest.fixture()
def may_chu_telegram(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    _XuLyTelegram.ds_yeu_cau = []
    _XuLyTelegram.hanh_vi = "ok"
    may_chu = ThreadingHTTPServer(("127.0.0.1", 0), _XuLyTelegram)
    cong = may_chu.server_address[1]
    t = threading.Thread(target=may_chu.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True)
    t.start()
    yield f"http://127.0.0.1:{cong}", _XuLyTelegram
    may_chu.shutdown()


@pytest.fixture()
def mod_da_cai(may_chu_telegram):
    goc, _ = may_chu_telegram
    mod = Module(MANIFEST)
    mod._goc_test = goc  # tham số nội bộ CHỈ cho test, không từ cấu hình người dùng
    mod.nap({"bot_token": TOKEN_GIA, "dich": DICH_GIA})
    return mod


# ───────────── hợp đồng module ─────────────

def test_manifest_hop_chuan():
    assert kiem_manifest(MANIFEST) is None
    assert "kenh_ra" in MANIFEST["cung_cap"]
    assert "api.telegram.org" in MANIFEST["tai_nguyen"]["mang_ra"]
    assert "httpx" in MANIFEST["yeu_cau_goi"]
    # địa chỉ gọi cố định: không có trường cấu hình nào cho URL
    khoa_cau_hinh = [t["khoa"] for t in MANIFEST["truong_cau_hinh"]]
    assert not any("url" in k or "co_so" in k for k in khoa_cau_hinh), khoa_cau_hinh
    # bot_token là bí mật
    token_truong = next(t for t in MANIFEST["truong_cau_hinh"] if t["khoa"] == "bot_token")
    assert token_truong["kieu"] == "bi_mat"


def test_kenh_ra_khong_lo_bi_mat(mod_da_cai):
    ds = mod_da_cai.kenh_ra()
    assert ds == [{"id": "nha", "ten": "Nhà"}, {"id": "khach", "ten": "Phòng khách"}]
    s = json.dumps(ds, ensure_ascii=False)
    assert TOKEN_GIA not in s
    assert str(CHAT_NHA) not in s
    assert str(CHAT_NHA_KHACH) not in s


def test_gui_tin_dung_chat_va_chu_de(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "Xin chào"}))
    assert kq == {"ok": True}
    assert len(xu_ly.ds_yeu_cau) == 1
    yc = xu_ly.ds_yeu_cau[0]
    assert yc["duong"] == f"/bot{TOKEN_GIA}/sendMessage"
    assert yc["than"]["chat_id"] == CHAT_NHA
    assert yc["than"]["text"] == "Xin chào"
    assert yc["than"]["message_thread_id"] == 7
    assert yc["than"]["disable_web_page_preview"] is True
    assert "parse_mode" not in yc["than"]  # văn bản thuần, không parse_mode


def test_gui_tin_dich_khong_chu_de(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "khach"}, {"van_ban": "A"}))
    assert kq == {"ok": True}
    assert "message_thread_id" not in xu_ly.ds_yeu_cau[0]["than"]


def test_nhieu_dich_moi_dich_dung_chat(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    assert asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "1"})) == {"ok": True}
    assert asyncio.run(mod_da_cai.gui_tin({"kenh_id": "khach"}, {"van_ban": "2"})) == {"ok": True}
    chat_ids = [yc["than"]["chat_id"] for yc in xu_ly.ds_yeu_cau]
    assert chat_ids == [CHAT_NHA, CHAT_NHA_KHACH]


def test_tin_dai_tach_o_ranh_gioi_dong(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    dong = "dong-gia-0123456789 " * 3  # ~57 ký tự/dòng
    van_ban = "\n".join(f"{dong}{i:04d}" for i in range(90))  # ~5.2KB, 90 dòng
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": van_ban}))
    assert kq == {"ok": True}
    cac_tin = [yc["than"]["text"] for yc in xu_ly.ds_yeu_cau]
    assert len(cac_tin) == 2
    assert all(len(t) <= 4096 for t in cac_tin)
    assert "\n".join(cac_tin) == van_ban  # tách đúng ranh giới dòng, không mất chữ
    assert not cac_tin[0].endswith("\n")


def test_tin_dai_khong_xuong_dong_cat_cung(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    van_ban = "x" * 5000
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": van_ban}))
    assert kq == {"ok": True}
    cac_tin = [yc["than"]["text"] for yc in xu_ly.ds_yeu_cau]
    assert len(cac_tin) == 2
    assert len(cac_tin[0]) == 4096
    assert "".join(cac_tin) == van_ban


def test_tin_rat_dai_toi_da_5_tin_va_cat_ba_cham(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "y" * 30000}))
    assert kq == {"ok": True}
    cac_tin = [yc["than"]["text"] for yc in xu_ly.ds_yeu_cau]
    assert len(cac_tin) == 5
    assert all(len(t) <= 4096 for t in cac_tin)
    assert cac_tin[-1].endswith("…")


def test_ok_false_thanh_that_bai_khong_lo_mo_ta(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    xu_ly.hanh_vi = "ok_sai"
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq["ok"] is False
    assert "GIA-chat khong ton tai" not in json.dumps(kq, ensure_ascii=False)
    assert isinstance(kq.get("ma"), str) and kq["ma"]


def test_loi_4xx_khong_thu_lai(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    xu_ly.hanh_vi = "loi_400"
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq["ok"] is False
    assert len(xu_ly.ds_yeu_cau) == 1  # 4xx: không thử lại


def test_429_bao_that_bai_khong_ngu_dai(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    xu_ly.hanh_vi = "gioi_han_429"
    bat_dau = time.monotonic()
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    het = time.monotonic() - bat_dau
    assert kq == {"ok": False, "ma": "bi_gioi_han"}
    assert len(xu_ly.ds_yeu_cau) == 1
    assert het < 10  # không ngủ theo retry_after


def test_loi_mang_thu_lai_dung_mot_lan(monkeypatch, mod_da_cai):
    so_lan_goi = []

    async def gia_post(self, *a, **kw):
        so_lan_goi.append(1)
        raise httpx.ConnectError("GIA-loi-mang", request=httpx.Request("POST", "http://127.0.0.1:9/x"))

    monkeypatch.setattr(httpx.AsyncClient, "post", gia_post)
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq["ok"] is False
    assert kq["ma"] == "mang_loi"
    assert len(so_lan_goi) == 2  # 1 lần đầu + thử lại đúng 1 lần


def test_loi_mang_lan_dau_thanh_cong_lan_hai(monkeypatch, mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    that = {"post": httpx.AsyncClient.post}
    so_lan = []

    async def gia_post(self, *a, **kw):
        so_lan.append(1)
        if len(so_lan) == 1:
            raise httpx.ConnectError("GIA-loi-mang", request=httpx.Request("POST", "http://127.0.0.1:9/x"))
        return await that["post"](self, *a, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "post", gia_post)
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq == {"ok": True}
    assert len(xu_ly.ds_yeu_cau) == 1  # tin thứ hai tới nơi


def test_token_khong_ro_ri_qua_log_khi_goi_thanh_cong(mod_da_cai, may_chu_telegram, caplog):
    caplog.set_level(logging.DEBUG)  # hứng cả log DEBUG của httpx/httpcore
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq == {"ok": True}
    assert TOKEN_GIA not in caplog.text


def test_token_khong_ro_ri_qua_log_khi_loi_mang(monkeypatch, mod_da_cai, caplog):
    caplog.set_level(logging.DEBUG)

    async def gia_post(self, *a, **kw):
        raise httpx.ConnectError("GIA-loi-mang",
                                 request=httpx.Request("POST", f"http://127.0.0.1:9/bot{TOKEN_GIA}/x"))

    monkeypatch.setattr(httpx.AsyncClient, "post", gia_post)
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq["ok"] is False
    assert TOKEN_GIA not in caplog.text
    assert TOKEN_GIA not in json.dumps(kq)


def test_thieu_van_ban_khong_gui(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    assert asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {}))["ok"] is False
    assert asyncio.run(mod_da_cai.gui_tin({"kenh_id": "nha"}, {"van_ban": "  "}))["ok"] is False
    assert xu_ly.ds_yeu_cau == []


def test_dich_la_khong_gui(mod_da_cai, may_chu_telegram):
    _, xu_ly = may_chu_telegram
    kq = asyncio.run(mod_da_cai.gui_tin({"kenh_id": "khong-co"}, {"van_ban": "A"}))
    assert kq == {"ok": False, "ma": "dich_khong_co"}
    assert xu_ly.ds_yeu_cau == []


def test_chua_cau_hinh(may_chu_telegram):
    mod = Module(MANIFEST)  # chưa nap()
    assert mod.kenh_ra() == []
    kq = asyncio.run(mod.gui_tin({"kenh_id": "nha"}, {"van_ban": "A"}))
    assert kq["ok"] is False


def test_dich_dang_chuoi_json(may_chu_telegram):
    goc, _ = may_chu_telegram
    mod = Module(MANIFEST)
    mod._goc_test = goc
    mod.nap({"bot_token": TOKEN_GIA, "dich": json.dumps(DICH_GIA)})
    assert [d["id"] for d in mod.kenh_ra()] == ["nha", "khach"]


# ───────────── cai() ─────────────

def test_cai_token_hop_le(may_chu_telegram):
    goc, _ = may_chu_telegram
    mod = Module(MANIFEST)
    mod._goc_test = goc
    kq = asyncio.run(mod.cai({"bot_token": TOKEN_GIA, "dich": DICH_GIA}))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["bot_token"] == TOKEN_GIA


def test_cai_token_sai(may_chu_telegram):
    goc, xu_ly = may_chu_telegram
    xu_ly.hanh_vi = "ok_sai"
    mod = Module(MANIFEST)
    mod._goc_test = goc
    kq = asyncio.run(mod.cai({"bot_token": "GIA-sai", "dich": DICH_GIA}))
    assert kq["ok"] is False
    assert "GIA-token sai" not in json.dumps(kq, ensure_ascii=False)


def test_cai_thieu_token():
    mod = Module(MANIFEST)
    kq = asyncio.run(mod.cai({"dich": DICH_GIA}))
    assert kq["ok"] is False


def test_cai_dich_sai_cau_truc():
    mod = Module(MANIFEST)
    kq = asyncio.run(mod.cai({"bot_token": TOKEN_GIA, "dich": [{"id": "nha"}]}))
    assert kq["ok"] is False and "chat_id" in kq["loi"]
    kq2 = asyncio.run(mod.cai({"bot_token": TOKEN_GIA,
                               "dich": [{"id": "a", "chat_id": 1}, {"id": "a", "chat_id": 2}]}))
    assert kq2["ok"] is False and "trùng" in kq2["loi"]


# ───────────── cổng lõi POST /api/gui/telegram-bot ─────────────

MOD = "telegram-bot"


@pytest.fixture()
def cong_loi(monkeypatch, may_chu_telegram):
    monkeypatch.setenv("WEB2API_DATA", str(Path("/tmp")))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    for v in ("WEB2API_GUI_TIN_PHUT", "WEB2API_GUI_DONG_THOI",
              "WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE", "WEB2API_GUI_TEP_TOI_DA_MB",
              "WEB2API_GUI_THOI_GIAN_GIAY"):
        monkeypatch.delenv(v, raising=False)
    gui_ra_mod._dat_lai_trang_thai()
    goc, xu_ly = may_chu_telegram

    _, khoa_gui = khoa_khach.tao_khoa(nhan="khach gui", pham_vi=["gui:telegram_bot"])
    _, khoa_chat = khoa_khach.tao_khoa(nhan="khach chat", pham_vi=["chat"])

    mod = Module(MANIFEST)
    mod._goc_test = goc
    mod.nap({"bot_token": TOKEN_GIA, "dich": DICH_GIA})
    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay[MOD] = mod
        so.catalog[MOD] = SimpleNamespace(
            manifest={"id": MOD, "tien_to": "telegram", "cung_cap": ["kenh_ra"]})
        yield c, xu_ly, {"qt": KHOA_QT, "gui": khoa_gui, "chat": khoa_chat}
    so.dang_chay.pop(MOD, None)
    so.catalog.pop(MOD, None)
    gui_ra_mod._dat_lai_trang_thai()


def _gui(c, khoa=None, than=None):
    kw = {}
    if khoa:
        kw["headers"] = {"Authorization": f"Bearer {khoa}"}
    return c.post(f"/api/gui/{MOD}", json=than or {}, **kw)


def test_loi_thieu_kenh_id_khi_nhieu_dich_khong_gui_gi(cong_loi):
    c, xu_ly, khoa = cong_loi
    r = _gui(c, khoa["qt"], {"noi_dung": {"van_ban": "A"}})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "kenh_required"
    assert xu_ly.ds_yeu_cau == []  # không gửi gì


def test_loi_gui_thanh_cong_qua_kenh_id(cong_loi):
    c, xu_ly, khoa = cong_loi
    r = _gui(c, khoa["gui"], {"kenh_id": "khach", "noi_dung": {"van_ban": "A"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kenh": "khach"}
    assert xu_ly.ds_yeu_cau[0]["than"]["chat_id"] == CHAT_NHA_KHACH


def test_loi_dich_la_404(cong_loi):
    c, xu_ly, khoa = cong_loi
    r = _gui(c, khoa["qt"], {"kenh_id": "khong-co", "noi_dung": {"van_ban": "A"}})
    assert r.status_code == 404, r.text
    assert xu_ly.ds_yeu_cau == []


def test_loi_khong_khoa_401(cong_loi):
    c, _, _ = cong_loi
    r = _gui(c, None, {"kenh_id": "nha", "noi_dung": {"van_ban": "A"}})
    assert r.status_code == 401, r.text


def test_loi_khoa_sai_pham_vi_403(cong_loi):
    c, xu_ly, khoa = cong_loi
    r = _gui(c, khoa["chat"], {"kenh_id": "nha", "noi_dung": {"van_ban": "A"}})
    assert r.status_code == 403, r.text
    assert xu_ly.ds_yeu_cau == []


def test_loi_module_chua_cau_hinh_503(monkeypatch, may_chu_telegram):
    monkeypatch.setenv("WEB2API_DATA", str(Path("/tmp")))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    gui_ra_mod._dat_lai_trang_thai()
    mod = Module(MANIFEST)  # đã cài nhưng chưa nap → không có đích
    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay[MOD] = mod
        so.catalog[MOD] = SimpleNamespace(
            manifest={"id": MOD, "tien_to": "telegram", "cung_cap": ["kenh_ra"]})
        r = c.post(f"/api/gui/{MOD}", headers={"Authorization": f"Bearer {KHOA_QT}"},
                   json={"kenh_id": "nha", "noi_dung": {"van_ban": "A"}})
        assert r.status_code == 503, r.text
        assert r.json()["error"]["code"] == "chua_co_kenh"
        so.dang_chay.pop(MOD, None)
        so.catalog.pop(MOD, None)
    gui_ra_mod._dat_lai_trang_thai()


def test_suc_khoe_chua_cau_hinh_bao_khong_ok():
    """Khuôn lõi: mọi module phải có suc_khoe(); chưa cấu hình → ok False."""
    mod = Module(MANIFEST)
    kq = asyncio.run(mod.suc_khoe())
    assert kq["ok"] is False


def test_suc_khoe_da_cau_hinh_bao_ok_va_khong_goi_mang(may_chu_telegram):
    """Đã cấu hình → ok True kèm số đích; sức khoẻ KHÔNG gọi mạng ngoài."""
    mod = Module(MANIFEST)
    mod.nap({"bot_token": TOKEN_GIA, "dich": json.dumps(DICH_GIA)})
    _XuLyTelegram.ds_yeu_cau = []
    kq = asyncio.run(mod.suc_khoe())
    assert kq["ok"] is True
    assert kq["so_dich"] == 2
    assert _XuLyTelegram.ds_yeu_cau == []
