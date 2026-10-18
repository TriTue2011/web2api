"""Module zalo-bot (NV114): kênh gửi ra (chuẩn `kenh_ra`) tới Zalo Bot — chỉ gửi văn bản.

Mỗi test kèm đột biến (bảng trong nv114-bao-cao.md): đột biến tương ứng phải làm
test ĐỎ, mã thật XANH.

Máy chủ Zalo GIẢ cục bộ (ZaloGia): 4 bot giả, ghi lại token nhận được ở mỗi lời gọi
để kiểm «mỗi đích tới đúng bot của nó».
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import gui_ra as gui_ra_mod  # noqa: E402
from app import khoa_khach  # noqa: E402
from app.kham_pha import doc_manifest, kiem_manifest, nap_ma, van_tay  # noqa: E402
from app.loi_openai import LoiOpenAI  # noqa: E402
from app.main import app  # noqa: E402
from gia import MayChuThat  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "zalo-bot"

KHOA_QT = "<redacted>"

# Bốn bot GIẢ — chuỗi giả rõ ràng, không phải token thật.
TOKENS = [f"zalo-gia-token-{i}" for i in (1, 2, 3, 4)]
TEN_BOTS = "bot-nha, bot-cong-ty, bot-vo, bot-test"

DICH_JSON = json.dumps([
    {"id": "nha", "ten": "Nhà", "chat_id": "chat-nha-1", "bot": "bot-nha"},
    {"id": "cong-ty", "ten": "Công ty", "chat_id": "chat-cty-2", "bot": "bot-cong-ty"},
    {"id": "vo", "ten": "Vợ", "chat_id": "chat-vo-3", "bot": "bot-vo"},
    {"id": "test", "ten": "Test", "chat_id": "chat-test-4", "bot": "bot-test"},
], ensure_ascii=False)

CAU_HINH_DAY_DU = {
    "bot_token": list(TOKENS),
    "ten_bot": TEN_BOTS,
    "danh_sach_dich": DICH_JSON,
}


def _manifest():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    return m


def _lop():
    m = _manifest()
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    return lop, m


def _module(goc_url=None):
    lop, m = _lop()
    mod = lop(m, _goc_kiem_thu=goc_url) if goc_url else lop(m)
    return mod


# ───────────── máy chủ Zalo giả ─────────────

class ZaloGia:
    """Giả lập POST /bot<TOKEN>/sendMessage của Zalo Bot.

    - Ghi lại (token, chat_id, text) của MỌI lời gọi để kiểm «đích nào → bot nấy».
    - chat_id đã đăng ký với bot → {"ok": true}; chat_id lạ → 410
      {"ok": false, "error_code": 410} (đúng như Zalo thật trả khi sai bot/chat).
    - chat_id đặc biệt để kiểm các nhánh lỗi: "chat-loi-400", "chat-het-han-muc",
      "chat-loi-may-chu", "chat-ok-la".
    """

    def __init__(self, tokens=TOKENS):
        self.tokens = set(tokens)
        self.chat_cua_bot = {t: set() for t in tokens}
        self.da_nhan: list[dict] = []
        routes = [Route("/bot{token}/sendMessage", self.gui, methods=["POST"])]
        self.app = Starlette(routes=routes)

    def dang_ky(self, token, chat_id):
        self.chat_cua_bot[token].add(chat_id)

    async def gui(self, request):
        token = request.path_params["token"]
        try:
            than = await request.json()
        except Exception:
            than = {}
        chat_id = than.get("chat_id")
        text = than.get("text")
        self.da_nhan.append({"token": token, "chat_id": chat_id, "text": text,
                             "khoa_than": sorted(than.keys()) if isinstance(than, dict) else []})
        if token not in self.tokens:
            return JSONResponse({"ok": False, "error_code": 404,
                                 "description": "Not Found: wrong bot token"}, status_code=404)
        if chat_id == "chat-loi-400":
            return JSONResponse({"ok": False, "error_code": 400,
                                 "description": "Bad Request: text is empty"})
        if chat_id == "chat-het-han-muc":
            return JSONResponse({"ok": False, "error_code": 429,
                                 "description": "Too Many Requests", "retry_after": 5},
                                status_code=429)
        if chat_id == "chat-loi-may-chu":
            return JSONResponse({"ok": False, "error_code": 500,
                                 "description": "Internal Server Error"}, status_code=500)
        if chat_id == "chat-ok-la":
            return JSONResponse({"ok": "yes"})  # ok không phải bool
        if chat_id not in self.chat_cua_bot.get(token, set()):
            return JSONResponse({"ok": False, "error_code": 410,
                                 "description": "The chat_id is invaild"})
        return JSONResponse({"ok": True, "result": {"message_id": len(self.da_nhan)}})


@pytest.fixture
def zalo_gia():
    z = ZaloGia()
    z.dang_ky(TOKENS[0], "chat-nha-1")
    z.dang_ky(TOKENS[1], "chat-cty-2")
    z.dang_ky(TOKENS[2], "chat-vo-3")
    z.dang_ky(TOKENS[3], "chat-test-4")
    for dac_biet in ("chat-loi-400", "chat-het-han-muc", "chat-loi-may-chu", "chat-ok-la"):
        for t in TOKENS:
            z.dang_ky(t, dac_biet)
    with MayChuThat(z.app) as may_chu:
        z.url = may_chu.url
        yield z


def _mod_da_cau_hinh(zalo_gia, dich_json=DICH_JSON):
    mod = _module(zalo_gia.url)
    mod.nap({"bot_token": list(TOKENS), "ten_bot": TEN_BOTS, "danh_sach_dich": dich_json})
    return mod


# ───────────── manifest & hợp đồng ─────────────

def test_manifest_hop_le():
    m = _manifest()
    assert kiem_manifest(m) is None, kiem_manifest(m)
    assert m["id"] == "zalo-bot"
    assert m["cung_cap"] == ["kenh_ra"]
    assert m["hang"] in ("A", "B")
    assert m["tai_nguyen"]["mang_ra"] == ["bot-api.zaloplatforms.com"]
    khoa_truong = {t["khoa"] for t in m["truong_cau_hinh"]}
    assert "bot_token" in khoa_truong
    # Địa chỉ gọi CỐ ĐỊNH — không cho cấu hình URL (chống SSRF); ghi đè chỉ cho test.
    assert not any("url" in k.lower() or "dia_chi" in k.lower() for k in khoa_truong), khoa_truong


def test_lop_du_phuong_thuc_hop_dong():
    lop, m = _lop()
    for pt in ("nap", "cai", "suc_khoe", "dung", "gui_tin", "kenh_ra"):
        assert callable(getattr(lop, pt, None)), f"thiếu {pt}"


# ───────────── cai() ─────────────

def test_cai_thieu_bot_token_that_bai():
    mod = _module()
    r = asyncio.run(mod.cai({}))
    assert r["ok"] is False
    r = asyncio.run(mod.cai({"bot_token": []}))
    assert r["ok"] is False


def test_cai_dich_thieu_bot_that_bai():
    # KHÔNG có «bot mặc định»: đích không nêu bot thì cấu hình bị từ chối.
    mod = _module()
    dich = json.dumps([{"id": "nha", "ten": "Nhà", "chat_id": "chat-nha-1"}])
    r = asyncio.run(mod.cai({"bot_token": [TOKENS[0]], "danh_sach_dich": dich}))
    assert r["ok"] is False, r


def test_cai_dich_bot_la_that_bai():
    mod = _module()
    dich = json.dumps([{"id": "nha", "ten": "Nhà", "chat_id": "chat-nha-1", "bot": "bot-khong-co"}])
    r = asyncio.run(mod.cai({"bot_token": [TOKENS[0]], "ten_bot": "bot-nha",
                             "danh_sach_dich": dich}))
    assert r["ok"] is False, r


def test_cai_thanh_cong():
    mod = _module()
    r = asyncio.run(mod.cai(dict(CAU_HINH_DAY_DU)))
    assert r["ok"] is True, r
    assert r["cau_hinh_luu"]["bot_token"] == TOKENS


# ───────────── kenh_ra() ─────────────

def test_kenh_ra_khong_lo_bi_mat(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    ds = mod.kenh_ra()
    assert ds == [{"id": "nha", "ten": "Nhà"}, {"id": "cong-ty", "ten": "Công ty"},
                 {"id": "vo", "ten": "Vợ"}, {"id": "test", "ten": "Test"}]
    tho = json.dumps(ds, ensure_ascii=False)
    for t in TOKENS:
        assert t not in tho
    assert "chat-nha-1" not in tho


def test_chua_cau_hinh_kenh_ra_rong():
    mod = _module()
    mod.nap({})
    assert mod.kenh_ra() == []


# ───────────── gui_tin() với máy chủ giả ─────────────

def test_gui_thanh_cong_dung_bot_cua_dich(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    r = asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"}, {"van_ban": "xin chào"}))
    assert r == {"ok": True}, r
    assert len(zalo_gia.da_nhan) == 1
    goi = zalo_gia.da_nhan[0]
    assert goi["token"] == TOKENS[0]      # bot-nha
    assert goi["chat_id"] == "chat-nha-1"
    assert goi["text"] == "xin chào"


def test_bon_dich_moi_dich_dung_bot_rieng(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    for kid, token in (("nha", TOKENS[0]), ("cong-ty", TOKENS[1]),
                       ("vo", TOKENS[2]), ("test", TOKENS[3])):
        zalo_gia.da_nhan.clear()
        r = asyncio.run(mod.gui_tin({"kenh_id": kid, "ten": kid}, {"van_ban": "ping"}))
        assert r == {"ok": True}, (kid, r)
        assert zalo_gia.da_nhan[0]["token"] == token, kid


def test_chat_id_sai_410_that_bai_khong_thu_bot_khac(zalo_gia):
    dich = json.dumps([{"id": "sai", "ten": "Sai", "chat_id": "chat-khong-ton-tai", "bot": "bot-nha"}])
    mod = _mod_da_cau_hinh(zalo_gia, dich)
    zalo_gia.da_nhan.clear()
    r = asyncio.run(mod.gui_tin({"kenh_id": "sai", "ten": "Sai"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r
    assert r.get("ma") == "chat_sai", r
    assert len(zalo_gia.da_nhan) == 1  # không thử bot khác


def test_loi_400_that_bai_khong_lo_mo_ta_tho(zalo_gia):
    dich = json.dumps([{"id": "loi", "ten": "Lỗi", "chat_id": "chat-loi-400", "bot": "bot-nha"}])
    mod = _mod_da_cau_hinh(zalo_gia, dich)
    r = asyncio.run(mod.gui_tin({"kenh_id": "loi", "ten": "Lỗi"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r
    assert "Bad Request" not in json.dumps(r, ensure_ascii=False)


def test_ok_khong_phai_bool_that_bai(zalo_gia):
    # Lõi chỉ tin ok là bool True thật — module cũng vậy.
    dich = json.dumps([{"id": "la", "ten": "Lạ", "chat_id": "chat-ok-la", "bot": "bot-nha"}])
    mod = _mod_da_cau_hinh(zalo_gia, dich)
    r = asyncio.run(mod.gui_tin({"kenh_id": "la", "ten": "Lạ"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r


def test_429_bao_that_bai_ngay(zalo_gia):
    dich = json.dumps([{"id": "han", "ten": "Hạn", "chat_id": "chat-het-han-muc", "bot": "bot-nha"}])
    mod = _mod_da_cau_hinh(zalo_gia, dich)
    zalo_gia.da_nhan.clear()
    r = asyncio.run(mod.gui_tin({"kenh_id": "han", "ten": "Hạn"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r
    assert len(zalo_gia.da_nhan) == 1  # không ngủ chờ, không gửi lại


def test_tin_dai_tach_o_ranh_gioi_dong(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    dong = [f"dòng {i:03d} " + "x" * 90 for i in range(120)]  # mỗi dòng ~100 ký tự
    van_ban = "\n".join(dong)
    assert len(van_ban) > 1990 * 5
    zalo_gia.da_nhan.clear()
    r = asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"}, {"van_ban": van_ban}))
    assert r == {"ok": True}, r
    assert 1 < len(zalo_gia.da_nhan) <= 5
    for i, goi in enumerate(zalo_gia.da_nhan):
        assert len(goi["text"]) <= 1990
        # Tách ở ranh giới dòng: mỗi tin là các dòng nguyên vẹn; tin cuối có thể
        # kết thúc bằng «…» báo phần dư đã cắt (dòng «…» không phải dòng gốc).
        text = goi["text"]
        if i == len(zalo_gia.da_nhan) - 1 and text.endswith("…"):
            text = text[: -len("…")]
        for d in text.split("\n"):
            assert d in dong, d[:60]
    assert zalo_gia.da_nhan[-1]["text"].endswith("…")  # phần dư cắt kèm «…»


def test_chi_gui_van_ban_thuan(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"},
                            {"van_ban": "*đậm* _nghiêng_ [link](http://x)"}))
    goi = zalo_gia.da_nhan[-1]
    assert goi["khoa_than"] == ["chat_id", "text"]  # không parse_mode/markdown
    assert goi["text"] == "*đậm* _nghiêng_ [link](http://x)"  # gửi nguyên văn


def test_gui_dich_la_that_bai_khong_goi_mang(zalo_gia):
    mod = _mod_da_cau_hinh(zalo_gia)
    zalo_gia.da_nhan.clear()
    r = asyncio.run(mod.gui_tin({"kenh_id": "khong-co", "ten": "?"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r
    assert zalo_gia.da_nhan == []


def test_chua_cau_hinh_gui_that_bai(zalo_gia):
    mod = _module(zalo_gia.url)
    mod.nap({})
    zalo_gia.da_nhan.clear()
    r = asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"}, {"van_ban": "ping"}))
    assert r["ok"] is False, r
    assert r.get("ma") == "chua_cau_hinh"
    assert zalo_gia.da_nhan == []


def test_token_khong_ro_trong_log(zalo_gia, caplog):
    mod = _mod_da_cau_hinh(zalo_gia)
    with caplog.at_level(5):  # TRACE — bắt cả log nội bộ của httpx/httpcore
        r = asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"}, {"van_ban": "xin chào"}))
    assert r == {"ok": True}, r
    for rec in caplog.records:
        noi_dung = rec.getMessage()
        for t in TOKENS:
            assert t not in noi_dung, f"token rò trong log: {rec.name}: {noi_dung[:120]}"


def test_token_khong_ro_khi_loi_mang(caplog):
    # Trỏ tới cổng chết → ngoại lệ kết nối; ngoại lệ của httpx có thể in URL (chứa token).
    from gia import cong_trong
    mod = _module(f"http://127.0.0.1:{cong_trong()}")
    mod.nap(dict(CAU_HINH_DAY_DU))
    with caplog.at_level(5):
        r = asyncio.run(mod.gui_tin({"kenh_id": "nha", "ten": "Nhà"}, {"van_ban": "xin chào"}))
    assert r["ok"] is False, r
    assert r.get("ma") == "mang_loi"
    tho = json.dumps(r, ensure_ascii=False)
    for rec in caplog.records:
        noi_dung = rec.getMessage()
        for t in TOKENS:
            assert t not in noi_dung, f"token rò trong log: {rec.name}: {noi_dung[:120]}"
            assert t not in tho


# ───────────── qua cổng lõi /api/gui ─────────────

@pytest.fixture
def moi_truong_loi(zalo_gia, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")
    for v in ("WEB2API_GUI_TIN_PHUT", "WEB2API_GUI_DONG_THOI",
              "WEB2API_GUI_NOI_DUNG_TOI_DA_BYTE", "WEB2API_GUI_TEP_TOI_DA_MB",
              "WEB2API_GUI_THOI_GIAN_GIAY"):
        monkeypatch.delenv(v, raising=False)
    gui_ra_mod._dat_lai_trang_thai()
    _, khoa_chat = khoa_khach.tao_khoa(nhan="khach chat", pham_vi=["chat"])
    _, khoa_gui = khoa_khach.tao_khoa(nhan="khach gui zalo", pham_vi=["gui:zalo_bot"])
    mod = _mod_da_cau_hinh(zalo_gia)
    man = _manifest()
    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay["zalo-bot"] = mod
        so.catalog["zalo-bot"] = SimpleNamespace(manifest=man)
        yield c, zalo_gia, {"qt": KHOA_QT, "chat": khoa_chat, "gui": khoa_gui}
    for mid in ("zalo-bot",):
        so.dang_chay.pop(mid, None)
        so.catalog.pop(mid, None)
    gui_ra_mod._dat_lai_trang_thai()


def test_loi_thieu_kenh_id_khong_gui_di(moi_truong_loi):
    c, zalo_gia, _ = moi_truong_loi
    zalo_gia.da_nhan.clear()
    with pytest.raises(LoiOpenAI) as ei:
        asyncio.run(gui_ra_mod.gui_den_dich(c.app, "zalo-bot", None, {"van_ban": "ping"}))
    assert ei.value.ma == 400 and ei.value.code == "kenh_required"
    assert zalo_gia.da_nhan == []  # lõi không đoán đích — không gửi gì


def test_loi_module_chua_cau_hinh_503(monkeypatch, tmp_path, zalo_gia):
    mod = _module(zalo_gia.url)
    mod.nap({})
    with TestClient(app) as c:
        so = c.app.state.so
        so.dang_chay["zalo-bot"] = mod
        so.catalog["zalo-bot"] = SimpleNamespace(manifest=_manifest())
        try:
            with pytest.raises(LoiOpenAI) as ei:
                asyncio.run(gui_ra_mod.gui_den_dich(c.app, "zalo-bot", None, {"van_ban": "ping"}))
        finally:
            so.dang_chay.pop("zalo-bot", None)
            so.catalog.pop("zalo-bot", None)
    assert ei.value.ma == 503 and ei.value.code == "chua_co_kenh"


def _post_gui(c, khoa=None, json=None):
    kw = {"headers": {}}
    if khoa:
        kw["headers"]["Authorization"] = f"Bearer {khoa}"
    return c.post("/api/gui/zalo-bot", json=json, **kw)


def test_api_khong_khoa_401(moi_truong_loi):
    c, _, _ = moi_truong_loi
    r = _post_gui(c, json={"kenh_id": "nha", "noi_dung": {"van_ban": "x"}})
    assert r.status_code == 401, r.text


def test_api_khoa_khach_sai_pham_vi_403(moi_truong_loi):
    c, zalo_gia, khoa = moi_truong_loi
    zalo_gia.da_nhan.clear()
    r = _post_gui(c, khoa=khoa["chat"], json={"kenh_id": "nha", "noi_dung": {"van_ban": "x"}})
    assert r.status_code == 403, r.text
    assert zalo_gia.da_nhan == []


def test_api_khoa_khach_dung_pham_vi_gui_duoc(moi_truong_loi):
    c, zalo_gia, khoa = moi_truong_loi
    zalo_gia.da_nhan.clear()
    r = _post_gui(c, khoa=khoa["gui"], json={"kenh_id": "cong-ty", "noi_dung": {"van_ban": "bao dong"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kenh": "cong-ty"}
    assert zalo_gia.da_nhan[0]["token"] == TOKENS[1]
    assert zalo_gia.da_nhan[0]["chat_id"] == "chat-cty-2"


def test_api_quan_tri_gui_thanh_cong(moi_truong_loi):
    c, zalo_gia, khoa = moi_truong_loi
    zalo_gia.da_nhan.clear()
    r = _post_gui(c, khoa=khoa["qt"], json={"kenh_id": "nha", "noi_dung": {"van_ban": "bao may la"}})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "kenh": "nha"}
    assert zalo_gia.da_nhan[0]["token"] == TOKENS[0]


def test_gui_dong_thoi_khong_doi_muc_log_toan_cuc(zalo_gia):
    """Lỗi do người soát tìm ra (Claude, 18/10): chặn log URL bằng cách đổi mức log của httpx quanh mỗi lời gọi rồi khôi phục —
    hai lượt gửi chạy xen kẽ làm logger KẸT vĩnh viễn ở mức WARNING (lượt sau lưu lại mức đã bị lượt trước hạ). Phải chặn bằng bộ
    lọc cài MỘT lần, không đổi mức log toàn cục."""
    mod = _mod_da_cau_hinh(zalo_gia)
    lg = logging.getLogger("httpx")
    muc_dau = lg.level
    lg.setLevel(logging.INFO)

    async def _chay():
        return await asyncio.gather(*[
            mod.gui_tin({"kenh_id": k, "ten": k}, {"van_ban": "xin chào"}) for k in ("nha", "cong-ty", "vo", "test")])
    try:
        kq = asyncio.run(_chay())
        assert all(r == {"ok": True} for r in kq), kq
        assert lg.level == logging.INFO, f"logger httpx bị đổi mức toàn cục: {lg.level}"
    finally:
        lg.setLevel(muc_dau)
