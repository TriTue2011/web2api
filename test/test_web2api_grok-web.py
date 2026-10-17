"""Bộ kiểm thử cho module grok-web: converter OpenAI, client WebSocket và xoay
tài khoản — tất cả qua máy chủ Grok GIẢ chạy cục bộ (không mạng thật).

Máy chủ giả nói đúng giao thức đã học: bắt tay WebSocket ``?uid=`` bằng cookie,
``session.create`` → ``session.created`` + ``conversation.attached``,
``conversation.item.create`` + ``response.create`` → các sự kiện
``response.chunk`` / ``response.output_text.delta`` / ``response.grok.output`` /
``response.done``. Kịch bản lỗi được chọn bằng giá trị cookie ``sso``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from websockets.asyncio.server import serve

from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "grok-web"

TRA_LOI = [("Xin ", "chào, "), ("tôi là ", "Grok.")]
VAN_BAN_DAY_DU = "".join(a + b for a, b in TRA_LOI)


def chay(coro):
    return asyncio.run(coro)


def tai_grok_web():
    """Nạp modules/grok-web như lõi nạp (gói riêng, import tương đối chạy được)."""
    ten = "web2api_module_grok_web_test"
    sys.modules.pop(ten, None)
    tep = THU_MUC_MODULE / "module.py"
    manifest = json.loads((THU_MUC_MODULE / "manifest.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        ten, tep, submodule_search_locations=[str(THU_MUC_MODULE)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod, manifest


def cookie(sso: str, uid: str = "uid_1") -> str:
    return f"sso={sso}; x-userid={uid}; phien=abc123"


def lay_sso(cookie_jar: str) -> str:
    for muc in cookie_jar.split(";"):
        muc = muc.strip()
        if muc.startswith("sso="):
            return muc[4:]
    return ""


class MayChuGrokGia:
    """Máy chủ Grok giả: WebSocket + ``/api/auth/session`` trên cùng một cổng."""

    def __init__(self):
        self.nhat_ky: list[dict] = []  # mỗi lượt chat: {sso, uid, che_do, prompt}
        self.url = ""
        self._san_sang = threading.Event()
        self._vong = None
        self._dung = None

    # ── HTTP: uid dự phòng + từ chối bắt tay ──────────────────────────

    async def _xu_ly_http(self, ket_noi, yeu_cau):
        cookie_jar = yeu_cau.headers.get("Cookie", "")
        sso = lay_sso(cookie_jar)
        if yeu_cau.path == "/api/auth/session":
            if not sso:
                return ket_noi.respond(401, "thieu sso")
            dap = ket_noi.respond(200, json.dumps({"session": {"userId": "uid_tu_phien"}}))
            dap.headers["Content-Type"] = "application/json"
            return dap
        if sso == "het_han" or not sso:
            return ket_noi.respond(401, "het han")
        if sso == "cam":
            return ket_noi.respond(403, "cam")
        return None  # cho bắt tay WebSocket đi tiếp

    # ── Kịch bản theo sso ─────────────────────────────────────────────

    def _kich_ban(self, sso: str) -> list[dict]:
        ev = lambda d: {"event": d}  # noqa: E731
        chunks = [
            ev({"type": "response.chunk", "chunk": {"text": {"text": a + b}}})
            for a, b in TRA_LOI
        ]
        done = ev({"type": "response.done", "status": "completed"})
        if sso == "het_luot":
            return [ev({"type": "response.grok.output",
                        "output": {"stream_error": {"kind": "usage_limit"}}})]
        if sso == "loi_phien_su_kien":
            # Lỗi phiên dạng sự kiện (thay vì HTTP 401 lúc bắt tay).
            return [ev({"type": "error", "error": {"code": "unauthorized"}})]
        if sso == "loi_may_chu":
            return [ev({"type": "response.done", "status": "failed"})]
        if sso == "rong":
            return [done]
        if sso == "phan_tich":
            return [
                ev({"type": "response.chunk",
                    "chunk": {"channel": "ANALYSIS",
                              "text": {"text": "suy nghĩ nội bộ"}}}),
                ev({"type": "response.output_text.delta", "delta": "Trả lời "}),
                ev({"type": "response.chunk",
                    "chunk": {"text": {"text": "thật."}}}),
                done,
            ]
        if sso == "loi_giua_chung":
            return [
                ev({"type": "response.chunk", "chunk": {"text": {"text": "một "}}}),
                ev({"type": "error", "error": {"code": "internal_error"}}),
            ]
        return chunks + [done]

    # ── Xử lý WebSocket ───────────────────────────────────────────────

    async def _xu_ly_ws(self, ws):
        cookie_jar = ws.request.headers.get("Cookie", "")
        sso = lay_sso(cookie_jar)
        path_day_du = ws.request.path or ""
        uid = ""
        if "?" in path_day_du:
            uid = dict(parse_qsl(path_day_du.split("?", 1)[1])).get("uid", "")
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "session.create"
        che_do = sk["event"]["session"]["model"]
        await ws.send(json.dumps({"event": {"type": "session.created",
                                            "session": {"id": "sess_1"}}}))
        await ws.send(json.dumps({"event": {"type": "conversation.attached",
                                            "conversation": {"id": "conv_1"}}}))
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "conversation.item.create"
        prompt = sk["event"]["item"]["x_grok"]["input_chunks"][0]["text"]["text"]
        self.nhat_ky.append({"sso": sso, "uid": uid, "che_do": che_do,
                             "prompt": prompt})
        sk = json.loads(await ws.recv())
        assert sk["event"]["type"] == "response.create"
        if sso == "im_lang":
            await asyncio.sleep(30)  # client phải tự ngắt vì im quá lâu
            return
        for su_kien in self._kich_ban(sso):
            await ws.send(json.dumps(su_kien))

    # ── Vòng đời ─────────────────────────────────────────────────────

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
        self._vong.call_soon_threadsafe(self._dung.set)
        self._luong.join(10)


@pytest.fixture()
def may_chu():
    with MayChuGrokGia() as mc:
        yield mc


@pytest.fixture()
def bo_module():
    return tai_grok_web()


def lap_module(bo_module, tai_khoan: list[str], ws_url: str):
    mod, manifest = bo_module
    md = mod.Module(manifest)
    chay(md.nap({"tai_khoan": tai_khoan, "ws_url": ws_url}))
    return md


def than(body: dict) -> dict:
    return {"model": "grok/fast",
            "messages": [{"role": "user", "content": "Grok là gì?"}],
            **body}


# ── Manifest ──────────────────────────────────────────────────────────

def test_manifest_hop_le(bo_module):
    _, manifest = bo_module
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "grok-web"
    assert manifest["tien_to"] == "grok"
    assert manifest["hang"] == "A"
    assert manifest["tai_nguyen"]["mang_ra"] == ["grok.com", "x.com"]  # NV89: miền trang đăng nhập
    assert "chat" in manifest["cung_cap"]
    truong = {t["khoa"]: t for t in manifest["truong_cau_hinh"]}
    assert truong["tai_khoan"]["kieu"] == "danh_sach_bi_mat"
    assert truong["tai_khoan"]["bat_buoc"] is True


# ── Chat không stream ─────────────────────────────────────────────────

def test_chat_khong_stream_thanh_cong(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)
    ma, tieu_de, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 200
    goi = json.loads(noi_dung)
    assert goi["object"] == "chat.completion"
    assert goi["model"] == "grok/fast"
    assert goi["choices"][0]["message"]["content"] == VAN_BAN_DAY_DU
    assert goi["choices"][0]["finish_reason"] == "stop"
    assert len(may_chu.nhat_ky) == 1
    assert "Grok là gì?" in may_chu.nhat_ky[0]["prompt"]
    assert may_chu.nhat_ky[0]["che_do"] == "fast"
    assert may_chu.nhat_ky[0]["uid"] == "uid_1"


def test_chat_khong_stream_che_do_expert(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({"model": "grok/expert"}), stream=False))
    assert ma == 200
    assert json.loads(noi_dung)["model"] == "grok/expert"
    assert may_chu.nhat_ky[0]["che_do"] == "expert"


def test_chat_che_do_la_ve_fast(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({"model": "grok/khong_ton_tai"}), stream=False))
    assert ma == 200
    assert json.loads(noi_dung)["model"] == "grok/fast"


def test_chat_tin_nhan_rong_400(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)
    ma, _, noi_dung = chay(md.chat({"model": "grok/fast", "messages": []}, stream=False))
    assert ma == 400


# ── Chat stream ───────────────────────────────────────────────────────

def test_chat_stream_tung_dong(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)

    async def chay_het():
        # Một vòng loop duy nhất như lõi thật: chat() và iterator chạy cùng loop.
        ma, tieu_de, luong = await md.chat(than({}), stream=True)
        tho = b"".join([khuc async for khuc in luong])
        return ma, tieu_de, tho

    ma, tieu_de, tho = chay(chay_het())
    assert ma == 200
    assert tieu_de["content-type"] == "text/event-stream"
    tho = tho.decode("utf-8")
    cac_dong = [d for d in tho.split("\n\n") if d.strip()]
    assert cac_dong[-1] == "data: [DONE]"
    su_kien = [json.loads(d[len("data: "):]) for d in cac_dong[:-1]]
    assert su_kien[0]["choices"][0]["delta"]["role"] == "assistant"
    noi_dung = "".join(s["choices"][0]["delta"].get("content", "") for s in su_kien)
    assert noi_dung == VAN_BAN_DAY_DU
    assert su_kien[-1]["choices"][0]["finish_reason"] == "stop"


# ── Xoay tài khoản ────────────────────────────────────────────────────

def test_chuyen_tai_khoan_khi_bat_tay_401(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("het_han"), cookie("tot_2", "uid_2")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert json.loads(noi_dung)["choices"][0]["message"]["content"] == VAN_BAN_DAY_DU
    assert [e["sso"] for e in may_chu.nhat_ky] == ["tot_2"]
    assert may_chu.nhat_ky[0]["uid"] == "uid_2"


def test_chuyen_tai_khoan_khi_bat_tay_403(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("cam"), cookie("tot_2")], may_chu.url)
    ma, _, _ = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert [e["sso"] for e in may_chu.nhat_ky] == ["tot_2"]


def test_chuyen_tai_khoan_khi_het_luot(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("het_luot"), cookie("tot_2")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert json.loads(noi_dung)["choices"][0]["message"]["content"] == VAN_BAN_DAY_DU
    assert [e["sso"] for e in may_chu.nhat_ky] == ["het_luot", "tot_2"]


def test_het_luot_het_tai_khoan_tra_429(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("het_luot")], may_chu.url)
    ma, tieu_de, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 429
    assert tieu_de.get("retry-after") == "60"
    assert json.loads(noi_dung)["error"]["code"] == "het_luot"


def test_chuyen_tai_khoan_khi_loi_may_chu(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("loi_may_chu"), cookie("tot_2")], may_chu.url)
    ma, _, _ = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert [e["sso"] for e in may_chu.nhat_ky] == ["loi_may_chu", "tot_2"]


def test_loi_may_chu_het_tai_khoan_tra_502(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("loi_may_chu")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 502
    assert json.loads(noi_dung)["error"]["code"] == "loi_may_chu"


def test_chuyen_tai_khoan_khi_loi_phien_dang_su_kien(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("loi_phien_su_kien"), cookie("tot_2")], may_chu.url)
    ma, _, _ = chay(md.chat(than({}), stream=False))
    assert ma == 200
    # Tài khoản lỗi vẫn kịp gửi lời nhắn trước khi nhận sự kiện error → có mặt
    # trong nhật ký, nhưng câu trả lời tới từ tài khoản 2.
    assert [e["sso"] for e in may_chu.nhat_ky] == ["loi_phien_su_kien", "tot_2"]


def test_chuyen_tai_khoan_khi_tra_loi_rong(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("rong"), cookie("tot_2")], may_chu.url)
    ma, _, _ = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert [e["sso"] for e in may_chu.nhat_ky] == ["rong", "tot_2"]


def test_tat_ca_het_han_tra_401(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("het_han")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 401
    assert json.loads(noi_dung)["error"]["code"] == "loi_phien"


def test_tai_khoan_het_luot_duoc_nghi(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("het_luot"), cookie("tot_2")], may_chu.url)
    chay(md.chat(than({}), stream=False))
    assert 0 in md._nghi_den  # tài khoản hết lượt bị đánh dấu nghỉ
    n_truoc = len(may_chu.nhat_ky)
    chay(md.chat(than({}), stream=False))
    # Lượt hai đi thẳng tới tài khoản còn tốt, không thử lại tài khoản đang nghỉ.
    assert [e["sso"] for e in may_chu.nhat_ky[n_truoc:]] == ["tot_2"]


def test_khong_xoay_tai_khoan_khi_da_co_chu(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("loi_giua_chung"), cookie("tot_2")], may_chu.url)

    async def chay_het():
        # Một vòng loop duy nhất như lõi thật: chat() và iterator chạy cùng loop.
        ma, _, luong = await md.chat(than({}), stream=True)
        tho = b"".join([khuc async for khuc in luong])
        return ma, tho

    ma, tho = chay(chay_het())
    assert ma == 200
    tho = tho.decode("utf-8")
    assert "một " in tho
    assert "[DONE]" not in tho  # lỗi giữa stream: kết thúc không [DONE]
    # Đã có chữ rồi thì KHÔNG xoay sang tài khoản 2 (tránh hỏi Grok hai lần).
    assert [e["sso"] for e in may_chu.nhat_ky] == ["loi_giua_chung"]


# ── Bảo mật: cookie không vào log hay phản hồi ─────────────────────────

def test_cookie_khong_vao_log_hay_phan_hoi(bo_module, may_chu, caplog):
    canary = "BI_MAT_CANARY_9f8e7d6c5b4a"
    md = lap_module(bo_module, [f"sso=het_han; x-userid=u; bi_mat={canary}"], may_chu.url)
    with caplog.at_level(logging.INFO, logger="web2api.module.grok_web"):
        ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 401
    assert canary not in noi_dung.decode("utf-8")
    assert canary not in caplog.text

    md2 = lap_module(bo_module, [f"sso=het_luot; x-userid=u; bi_mat={canary}"], may_chu.url)
    with caplog.at_level(logging.INFO, logger="web2api.module.grok_web"):
        ma, _, noi_dung = chay(md2.chat(than({}), stream=False))
    assert ma == 429
    assert canary not in noi_dung.decode("utf-8")
    assert canary not in caplog.text

    # Cả khi thành công, phản hồi cũng không chứa cookie.
    md3 = lap_module(bo_module, [f"sso=tot_1; x-userid=u; bi_mat={canary}"], may_chu.url)
    ma, _, noi_dung = chay(md3.chat(than({}), stream=False))
    assert ma == 200
    assert canary not in noi_dung.decode("utf-8")


# ── uid dự phòng qua /api/auth/session ────────────────────────────────

def test_uid_lay_tu_phien_http_khi_thieu_x_userid(bo_module, may_chu, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    md = lap_module(bo_module, ["sso=tot_3; phien=xyz"], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 200
    assert json.loads(noi_dung)["choices"][0]["message"]["content"] == VAN_BAN_DAY_DU
    assert may_chu.nhat_ky[0]["uid"] == "uid_tu_phien"


# ── Lọc kênh + delta ──────────────────────────────────────────────────

def test_bo_qua_kenh_phan_tich_nhan_delta(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("phan_tich")], may_chu.url)
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 200
    noi_dung = json.loads(noi_dung)["choices"][0]["message"]["content"]
    assert "suy nghĩ nội bộ" not in noi_dung
    assert noi_dung == "Trả lời thật."


# ── Im quá lâu ────────────────────────────────────────────────────────

def test_im_qua_lau_thanh_loi(bo_module, may_chu):
    mod, _ = bo_module
    khach = mod.KhachGrokWeb(may_chu.url,
                             {"sso": "im_lang", "x-userid": "u"},
                             uid="u", che_do_im_giua_tin=0.2)

    async def doc():
        async for _ in khach.chay("fast", "alo"):
            pass

    with pytest.raises(mod.LoiGrokWeb):
        chay(doc())


# ── Không có tài khoản ────────────────────────────────────────────────

def test_khong_co_tai_khoan_nao(bo_module, may_chu):
    mod, manifest = bo_module
    md = mod.Module(manifest)
    chay(md.nap({}))
    ma, _, noi_dung = chay(md.chat(than({}), stream=False))
    assert ma == 404
    assert json.loads(noi_dung)["error"]["code"] == "missing_credentials"
    suc_khoe = chay(md.suc_khoe())
    assert suc_khoe["trang_thai"] == "chưa cấu hình"
    assert len(chay(md.models())) == 4
    assert chay(md.cai({}))["ok"] is False


# ── Cài đặt / kiểm tra cấu hình ───────────────────────────────────────

def test_cai_kiem_dinh_dang(bo_module, may_chu, monkeypatch):
    # NV89: ws_url do người dùng ghi đè đi qua app/tu_xa.py — loopback phải được
    # test cho phép rõ bằng WEB2API_TEST_CHO_PHEP_LOOPBACK.
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod, manifest = bo_module
    md = mod.Module(manifest)
    # Thiếu cookie sso → từ chối.
    assert chay(md.cai({"tai_khoan": ["x-userid=u; phien=xyz"]}))["ok"] is False
    # ws_url sai scheme → từ chối.
    assert chay(md.cai({"tai_khoan": [cookie("tot_1")],
                        "ws_url": "http://127.0.0.1/x"}))["ok"] is False
    # ws_url trỏ máy chủ giả → bắt tay thử rồi đóng, ok.
    assert chay(md.cai({"tai_khoan": [cookie("tot_1")],
                        "ws_url": may_chu.url}))["ok"] is True


# ── Danh sách model / sức khoẻ ────────────────────────────────────────

def test_danh_sach_model(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1")], may_chu.url)
    ds = chay(md.models())
    assert [m["id"] for m in ds] == ["grok/fast", "grok/auto",
                                    "grok/expert", "grok/heavy"]
    assert all(m["object"] == "model" for m in ds)


def test_suc_khoe(bo_module, may_chu):
    md = lap_module(bo_module, [cookie("tot_1"), cookie("tot_2")], may_chu.url)
    sk = chay(md.suc_khoe())
    assert sk["trang_thai"] == "sẵn sàng"
    assert "cookie" not in json.dumps(sk, ensure_ascii=False).lower()


# ── Converter đơn vị ──────────────────────────────────────────────────

def test_converter_tach_che_do(bo_module):
    _, manifest = bo_module
    import sys as _sys
    conv = _sys.modules["web2api_module_grok_web_test.converter"]
    assert conv.tach_che_do("grok/expert") == "expert"
    assert conv.tach_che_do("expert") == "expert"
    assert conv.tach_che_do("grok/la") == "fast"
    assert conv.tach_che_do("") == "fast"


def test_converter_hop_van_ban(bo_module):
    import sys as _sys
    conv = _sys.modules["web2api_module_grok_web_test.converter"]
    van_ban = conv.hop_van_ban([
        {"role": "system", "content": "Bạn là trợ lý."},
        {"role": "user", "content": [{"type": "text", "text": "Chào"},
                                     {"type": "image_url",
                                      "image_url": {"url": "data:..."}}]},
        {"role": "assistant", "content": "Vâng."},
    ])
    assert "Bạn là trợ lý." in van_ban
    assert "Chào" in van_ban
    assert "data:..." not in van_ban  # media bị bỏ ở giai đoạn này
    assert "Vâng." in van_ban
