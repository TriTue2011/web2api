"""NV117 — module zalo-bot NHẬN TIN qua webhook (chuẩn tin vào của lõi, mục 12.3b).

Zalo Bot không có getUpdates dùng được khi webhook đang bật, nên nhận tin qua
cửa webhook chung của lõi (docs/CHUAN-MODULE.md mục 7): module chỉ phiên dịch
payload Zalo → tin chuẩn, trả {"ok": True, "tin_vao": [...]}; lõi chuyển vào
kenh_vao.nhan_tin rồi bỏ khoá tin_vao khỏi phản hồi.

Dạng payload (ĐỌC TỪ TÀI LIỆU CHÍNH THỨC bot.zapps.me/docs, trang webhook,
ngày đọc 19/10/2026): thân là JSON object `{"ok": true, "result": {...}}`;
`result.event_name` nhận `message.text.received` / `message.image.received` /
`message.sticker.received` / `message.voice.received` /
`message.unsupported.received`; `result.message` có `from: {id, display_name,
is_bot}`, `chat: {id, chat_type}` (chat_type là "PRIVATE"/"GROUP" — IN HOA),
`text` (tin văn bản), `message_id`, `date` là epoch MILI giây (mẫu
1750316131602). Mọi request kèm header `X-Bot-Api-Secret-Token` mang
secret_token — header KHÔNG tới module (lõi chỉ đưa thân JSON), nên xác thực
thực tế ở module là bí mật trong đường dẫn của cửa webhook lõi.

Quy tắc module (không bịa, làm theo cấu trúc):
- Nhiều bot CHUNG một URL webhook: payload không nói bot nào → bắt buộc ánh xạ
  chat.id → đích đã khai → bot của đích. Chat lạ → bỏ tin, đếm bo_qua_chat_la.
- Chỉ nhận sự kiện CÓ message.text là chuỗi (ảnh/sticker/voice/sự kiện khác tự rớt).
- la_nhom theo chat_type của Zalo ("PRIVATE" → riêng, còn lại → nhóm; không
  phân biệt hoa/thường, không chat_type → nhóm cho an toàn).
- luc từ trường date (epoch mili giây theo tài liệu chính thức; giây vẫn nhận
  để tương thích).
- id_tin ghép tên bot + message_id để hai bot chung webhook không đụng nhau.
- Mọi chuỗi ngoài là dữ liệu KHÔNG TIN: cắt độ dài, bỏ ký tự điều khiển,
  không vào log; token không vào log/suc_khoe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import kenh_vao  # noqa: E402
from app.kham_pha import doc_manifest, kiem_manifest, nap_ma, van_tay  # noqa: E402

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "zalo-bot"


def _lop_module():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, f"manifest zalo-bot hỏng: {loi}"
    loi = kiem_manifest(m)
    assert loi is None, f"manifest zalo-bot không đạt chuẩn: {loi}"
    lop, _ = nap_ma(THU_MUC, m, van_tay(THU_MUC))
    return lop

TOKEN_A = "TOKEN-GIA-ZALO-BOT-A-0123456789abcdef"
TOKEN_B = "TOKEN-GIA-ZALO-BOT-B-abcdef0123456789"
CHAT_A = "1000000001"   # chat riêng của bot A
CHAT_B = "1000000002"   # chat riêng của bot B
CHAT_NHOM = "2000000099"  # nhóm của bot A
NGUOI_1 = "3000000011"
NGUOI_2 = "3000000022"


def _cau_hinh():
    return {
        "bot_token": [TOKEN_A, TOKEN_B],
        "ten_bot": "bot-a, bot-b",
        "danh_sach_dich": json.dumps([
            {"id": "rieng-a", "ten": "Riêng A", "chat_id": CHAT_A, "bot": "bot-a"},
            {"id": "rieng-b", "ten": "Riêng B", "chat_id": CHAT_B, "bot": "bot-b"},
            {"id": "nhom-a", "ten": "Nhóm A", "chat_id": CHAT_NHOM, "bot": "bot-a"},
        ]),
    }


def _module():
    mod = _lop_module()({"id": "zalo-bot", "tien_to": "zalo"})
    mod.nap(_cau_hinh())
    return mod


def _payload(chat_id=CHAT_A, nguoi=NGUOI_1, text="xin chào", message_id="m1",
             event_name="message.text.received", chat_type="PRIVATE",
             ten="Người Dùng", date=1790000000000, bo_text=False):
    message = {
        "from": {"id": nguoi, "display_name": ten, "is_bot": False},
        "chat": {"id": chat_id, "chat_type": chat_type},
        "message_id": message_id,
        "date": date,
    }
    if not bo_text:
        message["text"] = text
    return {"result": {"event_name": event_name, "message": message}}


def _goi_webhook(mod, payload):
    return asyncio.run(mod.webhook(payload))


# ── chuẩn hoá đúng ──────────────────────────────────────────────────────────

def test_chuan_hoa_dung_tin_van_ban():
    mod = _module()
    kq = _goi_webhook(mod, _payload())
    assert kq["ok"] is True
    assert len(kq["tin_vao"]) == 1
    tin = kq["tin_vao"][0]
    assert tin["nguoi_gui"] == NGUOI_1, "người gửi là from.id, KHÔNG phải chat.id"
    assert tin["noi_gui"] == "rieng-a"
    assert tin["la_nhom"] is False
    assert tin["van_ban"] == "xin chào"
    assert tin["ten_tu_khai"] == "Người Dùng"
    assert tin["luc"].startswith("2026-"), tin["luc"]
    assert tin["id_tin"], "phải có id_tin"


def test_anh_xa_chat_sang_bot_dung_2_bot():
    mod = _module()
    tin_a = _goi_webhook(mod, _payload(chat_id=CHAT_A, message_id="m1"))["tin_vao"][0]
    tin_b = _goi_webhook(mod, _payload(chat_id=CHAT_B, message_id="m1"))["tin_vao"][0]
    assert tin_a["noi_gui"] == "rieng-a"
    assert tin_b["noi_gui"] == "rieng-b"
    assert "bot-a" in tin_a["id_tin"] and "bot-b" in tin_b["id_tin"], \
        "id_tin phải cho biết bot nào để không trùng giữa hai bot"


def test_nhom_nguoi_gui_khac_chat_id():
    mod = _module()
    tin = _goi_webhook(mod, _payload(chat_id=CHAT_NHOM, nguoi=NGUOI_2,
                                    chat_type="GROUP"))["tin_vao"][0]
    assert tin["nguoi_gui"] == NGUOI_2
    assert tin["nguoi_gui"] != CHAT_NHOM
    assert tin["la_nhom"] is True
    assert tin["noi_gui"] == "nhom-a"


def test_id_tin_2_bot_khong_dung_nhau():
    mod = _module()
    a = _goi_webhook(mod, _payload(chat_id=CHAT_A, message_id="trung"))["tin_vao"][0]["id_tin"]
    b = _goi_webhook(mod, _payload(chat_id=CHAT_B, message_id="trung"))["tin_vao"][0]["id_tin"]
    assert a != b


def test_ten_tu_khai_cat_va_lam_sach():
    mod = _module()
    tin = _goi_webhook(mod, _payload(ten="Kẻ\x00Xâm\x07Nhập" + "y" * 200))["tin_vao"][0]
    assert "\x00" not in tin["ten_tu_khai"] and "\x07" not in tin["ten_tu_khai"]
    assert len(tin["ten_tu_khai"]) <= 60


# ── bỏ đúng ─────────────────────────────────────────────────────────────────

def test_chat_la_bi_bo_va_dem():
    mod = _module()
    truoc = asyncio.run(mod.suc_khoe())["bo_qua_chat_la"]
    kq = _goi_webhook(mod, _payload(chat_id="9999999999"))
    assert kq == {"ok": True, "tin_vao": []}, "chat lạ: bỏ tin, không trả lời nơi lạ"
    assert asyncio.run(mod.suc_khoe())["bo_qua_chat_la"] == truoc + 1


def test_su_kien_anh_va_sticker_bi_bo():
    mod = _module()
    for ev in ("message.image.received", "message.sticker.received",
               "message.voice.received", "message.unsupported.received"):
        kq = _goi_webhook(mod, _payload(event_name=ev, bo_text=True))
        assert kq["tin_vao"] == [], f"sự kiện {ev} không có text phải bị bỏ"


def test_luc_nhan_mili_giay_va_giay():
    mod = _module()
    tin_ms = _goi_webhook(mod, _payload(date=1790000000000))["tin_vao"][0]
    assert tin_ms["luc"].startswith("2026-"), f"date mili giây phải ra ISO: {tin_ms['luc']}"
    tin_s = _goi_webhook(mod, _payload(date=1790000000))["tin_vao"][0]
    assert tin_s["luc"].startswith("2026-"), f"date giây vẫn nhận: {tin_s['luc']}"
    assert tin_ms["luc"] == tin_s["luc"], "cùng mốc thời gian phải ra cùng ISO"


def test_chat_type_khong_phan_biet_hoa_thuong():
    mod = _module()
    for ct, la_nhom in (("PRIVATE", False), ("private", False),
                        ("GROUP", True), ("group", True), (None, True)):
        tin = _goi_webhook(mod, _payload(chat_type=ct))["tin_vao"][0]
        assert tin["la_nhom"] is la_nhom, f"chat_type={ct!r} phải la_nhom={la_nhom}"


def test_payload_thieu_truong_khong_do_webhook():
    mod = _module()
    for xau in ({"result": {}},
                {"result": {"event_name": "message.text.received"}},
                {"result": {"message": "khong-phai-dict"}},
                {"result": {"message": {"chat": {"id": CHAT_A}}}},
                {},
                {"result": None},
                "chuoi-rac"):
        kq = _goi_webhook(mod, xau)
        assert kq == {"ok": True, "tin_vao": []}, f"payload xấu {xau!r:.40} không được đổ webhook"


def test_payload_qua_lon_khong_do_va_bi_cat():
    mod = _module()
    kq = _goi_webhook(mod, _payload(text="x" * 200_000))
    assert kq["ok"] is True
    assert len(kq["tin_vao"]) == 1
    assert len(kq["tin_vao"][0]["van_ban"]) <= 4000, "văn bản vượt trần lõi phải bị cắt"


def test_su_kien_id_on_dinh_va_rong_khi_xau():
    mod = _module()
    p = _payload()
    assert mod.su_kien_id(p) == mod.su_kien_id(p) and mod.su_kien_id(p)
    assert mod.su_kien_id({"result": {}}) == ""
    assert mod.su_kien_id("rac") == ""


def test_manifest_khai_webhook_dung_muc_7():
    m, loi = doc_manifest(THU_MUC)
    assert loi is None, loi
    assert kiem_manifest(m) is None
    wh = m.get("webhook")
    assert isinstance(wh, dict), "manifest phải khai webhook (CHUAN-MODULE mục 7)"
    bm = wh.get("bi_mat")
    truong_bm = {t["khoa"] for t in m.get("truong_cau_hinh", []) if t.get("kieu") == "bi_mat"}
    assert bm in truong_bm, "webhook.bi_mat phải là khoá cấu hình kieu bi_mat"
    assert isinstance(wh.get("tran_byte"), int) and wh["tran_byte"] > 0
    assert callable(getattr(_lop_module()({"id": "zalo-bot"}), wh.get("id_su_kien", "su_kien_id"), None))


# ── sức khoẻ và bảo mật ─────────────────────────────────────────────────────

def test_suc_khoe_co_che_do_va_dem_khong_lo_bi_mat():
    mod = _module()
    _goi_webhook(mod, _payload(text="nội dung nhạy cảm cần giấu"))
    sk = asyncio.run(mod.suc_khoe())
    assert sk["che_do_nhan"] == "webhook"
    assert isinstance(sk["bo_qua_chat_la"], int)
    assert isinstance(sk["loi_cuoi"], str) and len(sk["loi_cuoi"]) <= 32
    tron = json.dumps(sk, ensure_ascii=False)
    assert TOKEN_A not in tron and TOKEN_B not in tron, "token không được vào suc_khoe"
    assert "nội dung nhạy cảm cần giấu" not in tron, "nội dung tin không được vào suc_khoe"


def test_token_khong_vao_log(caplog):
    mod = _module()
    for lg in ("httpx", "httpcore"):
        logging.getLogger(lg).setLevel(logging.DEBUG)
    with caplog.at_level(logging.DEBUG):
        _goi_webhook(mod, _payload())
        _goi_webhook(mod, _payload(chat_id="la"))
    nhat_ky = caplog.text
    assert TOKEN_A not in nhat_ky and TOKEN_B not in nhat_ky


# ── ghép đầu-cuối với lõi ───────────────────────────────────────────────────

def _moi_truong_kenh_vao(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import so_thiet_bi
    from app.main import app
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-qt-gia-nv117")
    kenh_vao._dat_lai_trang_thai()
    c = TestClient(app)
    c.__enter__()
    so = c.app.state.so
    so.dang_chay["zalo-bot"] = SimpleNamespace(gui_tin=None)
    so_thiet_bi.dang_ky_lenh_so_thiet_bi()
    return c, so


def test_ghep_dau_cuoi_nguoi_la_khong_co_tra_loi(tmp_path, monkeypatch):
    c, so = _moi_truong_kenh_vao(tmp_path, monkeypatch)
    try:
        mod = _module()
        tin = _goi_webhook(mod, _payload())["tin_vao"][0]

        async def _chay():
            return await kenh_vao.nhan_tin(c.app, "zalo-bot", tin)
        kq = asyncio.run(_chay())
        assert kq.get("vai") == "la", "người chưa cấp quyền là người lạ"
        assert kq.get("bo_qua") is True, "người lạ không được trả lời"
    finally:
        c.__exit__(None, None, None)
        so.dang_chay.pop("zalo-bot", None)
        kenh_vao._dat_lai_trang_thai()
