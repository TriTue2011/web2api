"""Bộ kiểm thử cho module muse2api: cổng giả (Gateway giả), phân loại lỗi, stream, nối lại.

Không gọi mạng thật (conftest chặn Internet). Gateway giả thay cho lớp thật: có
call_json, _open, close. ChatSession rút nhịp ngắn (poll ~0.05s, quiet ~0.3s, wait ~1.5s).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from curl_cffi.requests.websockets import WebSocket, WebSocketClosed, WebSocketError

from app import kham_pha
from modules.muse2api import module as mm, chat as cc, protocol as pp


# ── Tiện ích ──────────────────────────────────────────────────────────────

def chay(coro):
    """Chạy coroutine bất đồng bộ."""
    return asyncio.run(coro)


# ── Rút nhịp ChatSession mặc định cho mọi test ────────────────────────────

@pytest.fixture(autouse=True)
def rut_nhip_chatsession(monkeypatch):
    """Rút nhịp ChatSession mặc định để kiểm thử chạy nhanh (trong vài giây)."""
    orig_init = cc.ChatSession.__init__

    def _fake_init(self, gw, node_id, poll_interval=0.05, wait_s=1.0, quiet_s=0.2):
        orig_init(self, gw, node_id, poll_interval=poll_interval, wait_s=wait_s, quiet_s=quiet_s)

    monkeypatch.setattr(cc.ChatSession, "__init__", _fake_init)


# ── Sự kiện lịch sử mẫu ──────────────────────────────────────────────────

def su_kien(seq: int, text: str, msg_id: str | None = None):
    return {"event_name": "message.assistant", "seq": seq,
            "payload": {"display_text": text,
                        "message_id": msg_id or f"m{seq}"}}


# ── Gateway GIẢ ──────────────────────────────────────────────────────────

class GatewayGia:
    """Gateway giả thay cho lớp thật. Hỗ trợ call_json, _open, close."""

    def __init__(self):
        self.lich_su: list[dict] = []  # sự kiện lịch sử trả về khi call_json("chat.history")
        self.open_calls: list[dict] = []  # ghi lại các lượt gọi _open
        self.call_json_calls: list[dict] = []
        self.da_dong = False
        self.loi_call_json: Exception | None = None
        self.loi_call_json_lan: int = 0
        self._dem_loi = 0

    def call_json(self, method: str, body=None, query=None, timeout=30, **kw):
        self.call_json_calls.append({"method": method, "body": body})
        if self.loi_call_json and self._dem_loi < self.loi_call_json_lan:
            self._dem_loi += 1
            raise self.loi_call_json
        return {"chat_events": list(self.lich_su)}

    def _open(self, method: str, body=None, **kw):
        self.open_calls.append({"method": method, "body": body})
        # Khi prompt được gửi đi, nếu trong lich_su có sự kiện assistant
        # mà seq <= baseline cũ, ta nâng seq lên để ChatSession nhận ra câu trả lời mới
        for e in self.lich_su:
            if e.get("event_name") == "message.assistant" and "seq" in e:
                e["seq"] = e.get("seq", 0) + 100 * len(self.open_calls)
        return len(self.open_calls)

    def close(self):
        self.da_dong = True


# ── Fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def mod():
    m = mm.Module({"tien_to": "muse2api", "id": "muse2api"})
    return m


@pytest.fixture
def mod_1cookie(mod, monkeypatch):
    """Module với 1 cookie, Gateway giả."""
    gw = GatewayGia()
    mod.nap({"cookies": ["hatch_sess=abc123def456; hatch_gw=xyz789"]})
    acc = mod._accs[0]
    acc._gw = gw
    return mod, acc, gw


# ── Ca 1: Không stream, thành công ──────────────────────────────────────

class TestKhongStreamThanhCong:
    def test_noi_dung_dung(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        # Sau khi gửi prompt, lịch sử sẽ có câu trả lời
        gw.lich_su = [su_kien(10, "Xin chào bạn!")]

        async def _chay():
            ma, hd, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 200
            d = json.loads(body)
            assert d["choices"][0]["message"]["content"] == "Xin chào bạn!"
            assert d["choices"][0]["finish_reason"] == "stop"

        chay(_chay())


# ── Ca 2: Stream thành công ──────────────────────────────────────────────

class TestStreamThanhCong:
    def test_stream_co_role_va_done(self, mod_1cookie):
        mod, acc, gw = mod_1cookie

        lan = [0]
        _orig_call = gw.call_json

        def _call_json_tang_dan(method, **kw):
            if method == "chat.history":
                lan[0] += 1
                if lan[0] <= 1:
                    return {"chat_events": []}  # lần đầu: trống (baseline)
                return {"chat_events": [su_kien(10, "Chào")]}
            return _orig_call(method, **kw)

        gw.call_json = _call_json_tang_dan

        async def _chay():
            ket = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=True)
            ma, hd, gen = ket
            assert ma == 200
            chunks = []
            async for c in gen:
                chunks.append(c.decode())
            full = "".join(chunks)
            assert '"role": "assistant"' in full or '"role":"assistant"' in full
            assert "stop" in full
            assert "[DONE]" in full

        chay(_chay())


# ── Ca 3: Stream + cookie hỏng → 401, không 200; Timeout → 504 ──────────

class TestStreamAuthError:
    def test_stream_cookie_hong_401(self, mod):
        mod.nap({"cookies": ["bad_cookie_1234567890"]})
        acc = mod._accs[0]

        # Gateway ném AuthError khi tạo
        def _dam_bao_loi():
            raise pp.AuthError("cookie expired")

        acc.dam_bao = _dam_bao_loi

        async def _chay():
            ma, hd, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=True)
            assert ma == 401
            d = json.loads(body)
            assert "error" in d

        chay(_chay())

    def test_stream_timeout_504(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        # call_json ném TimeoutError ngay từ đầu
        gw.loi_call_json = TimeoutError("no response for chat.history")
        gw.loi_call_json_lan = 100

        async def _chay():
            ma, hd, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=True)
            assert ma == 504
            d = json.loads(body)
            assert "error" in d

        chay(_chay())


# ── Ca 4: Lỗi giữa stream ───────────────────────────────────────────────

class TestLoiGiuaStream:
    def test_co_error_khong_done(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        lan = [0]

        def _call_json_loi_giua(method, **kw):
            if method == "chat.history":
                lan[0] += 1
                if lan[0] <= 1:
                    return {"chat_events": []}  # baseline
                if lan[0] <= 3:
                    return {"chat_events": [su_kien(10, "Phần đầu")]}
                # Sau đó lỗi liên tiếp
                raise pp.GatewayError(-1, "connection reset")
            return {"chat_events": []}

        gw.call_json = _call_json_loi_giua

        async def _chay():
            ket = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=True)
            ma, hd, gen = ket
            assert ma == 200  # đã có delta → trả 200
            chunks = []
            async for c in gen:
                chunks.append(c.decode())
            full = "".join(chunks)
            # Có nội dung đầu
            assert "Phần đầu" in full
            # Có sự kiện error
            assert '"error"' in full
            # KHÔNG có [DONE]
            assert "[DONE]" not in full
            # KHÔNG có finish_reason: stop
            assert '"finish_reason": "stop"' not in full and '"finish_reason":"stop"' not in full

        chay(_chay())


# ── Ca 5: _last_seq lỗi → không nuốt lỗi và không trả câu cũ ────────────

class TestLastSeqLoi:
    def test_khong_tra_cau_cu(self):
        gw = GatewayGia()
        # Lịch sử có sẵn câu cũ seq 40
        gw.lich_su = [su_kien(40, "Câu trả lời CŨ")]

        sess = cc.ChatSession(gw, "node-test")
        baseline = sess._last_seq()
        assert baseline == 40

        # Giờ nếu _last_seq lỗi thì phải ném ngoại lệ, KHÔNG được nuốt về 0
        gw_loi = GatewayGia()
        gw_loi.loi_call_json = pp.GatewayError(-1, "network error")
        gw_loi.loi_call_json_lan = 100
        sess2 = cc.ChatSession(gw_loi, "node-test")
        with pytest.raises(pp.GatewayError):
            sess2._last_seq()


# ── Ca 6: Nối lại sau ws chết ────────────────────────────────────────────

class _NoiseThang:
    """Noise «trong suốt»: thay phần mã hoá để dựng khung thật bằng protobuf."""

    def encrypt(self, d):
        return d

    def decrypt(self, d):
        return d


class WsKichBan:
    """ws giả theo kịch bản: recv() lần lượt trả khung (bytes) hoặc ném ngoại lệ.

    Hết kịch bản thì ném WebSocketClosed — đúng kiểu curl_cffi ném khi đầu kia đóng.
    """

    def __init__(self, kich_ban):
        self.kich_ban = list(kich_ban)
        self.da_gui: list[bytes] = []
        self.so_close = 0

    def connect(self, *a, **kw):
        pass

    def send_bytes(self, data):
        self.da_gui.append(data)

    def recv(self):
        muc = self.kich_ban.pop(0) if self.kich_ban else WebSocketClosed("peer closed")
        if isinstance(muc, BaseException):
            raise muc
        return muc, 0

    def close(self):
        self.so_close += 1


class WsImLang(WsKichBan):
    """ws giả im lặng: recv() chặn tới khi close() rồi ném như ws thật đã đóng."""

    def __init__(self):
        super().__init__([])
        self._dong = threading.Event()

    def recv(self):
        self._dong.wait()
        raise WebSocketError("Invalid active socket")

    def close(self):
        super().close()
        self._dong.set()


def gw_that(ws):
    """Gateway THẬT (request/_open/_read_frame thật), chỉ thay ws và Noise.

    gw.da_mo ghi tên route của mỗi lượt _open (để đếm chat.stream).
    """
    gw = object.__new__(pp.Gateway)
    gw.vm_id = "vm-gia"
    gw.ws = ws
    gw.noise = _NoiseThang()
    gw.stream = 1
    gw._send_lock = threading.Lock()
    gw._recv_lock = threading.Lock()
    gw.da_mo = []

    def _open(method, *a, **kw):
        gw.da_mo.append(method)
        return pp.Gateway._open(gw, method, *a, **kw)

    gw._open = _open
    return gw


def khung_tra_loi(sid: int, body: bytes, status: int = 200) -> bytes:
    """Một khung phản hồi đúng cấu trúc protobuf của cổng (chưa mã hoá)."""
    sf = pp.ServiceFrame(stream_id=sid)
    sf.response.status = status
    sf.response.body = body
    sf.response.end_body = True
    sr = pp.ServiceResponse(payload=sf.SerializeToString())
    return pp.NoiseTransportFrame(payload=sr.SerializeToString()).SerializeToString()


class _Resp:
    """Phản hồi HTTP giả mức curl_cffi: có status_code và json() như thật."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        return json.loads(self._body)  # thân không JSON → ValueError như thật


class TestNoiLai:
    """ws chết dùng ngoại lệ THẬT của curl_cffi đi qua Gateway.request() thật."""

    def test_ws_chet_phia_nhan_chua_gui_thi_noi_lai(self, mod, monkeypatch):
        mod.nap({"cookies": ["cookie_noi_lai_1234567890"]})
        acc = mod._accs[0]
        gw_chet = gw_that(WsKichBan([WebSocketClosed("peer closed")]))
        acc._gw = gw_chet

        gw2 = GatewayGia()
        gw2.lich_su = [su_kien(20, "Trả lời 2")]
        so_lan_tao = []
        monkeypatch.setattr(mm, "Gateway", lambda *a, **kw: so_lan_tao.append(1) or gw2)

        ma, _, body = chay(mod.chat(
            {"messages": [{"role": "user", "content": "Hi 2"}]}, stream=False))
        assert ma == 200, body
        assert json.loads(body)["choices"][0]["message"]["content"] == "Trả lời 2"
        assert len(so_lan_tao) == 1
        # Lời nhắn mở đúng MỘT lần trên cả hai kết nối
        mo = gw_chet.da_mo + [c["method"] for c in gw2.open_calls]
        assert mo.count("chat.stream") == 1
        assert gw_chet.da_mo == ["chat.history"]

    def test_ws_chet_sau_khi_da_gui_khong_noi_lai(self, mod, monkeypatch):
        mod.nap({"cookies": ["cookie_noi_lai_1234567890"]})
        acc = mod._accs[0]
        # Baseline (sid=1) trả lời được, sau đó ws chết ở mọi lượt poll.
        gw = gw_that(WsKichBan([khung_tra_loi(1, b'{"chat_events": []}')]))
        acc._gw = gw
        so_lan_tao = []
        monkeypatch.setattr(mm, "Gateway", lambda *a, **kw: so_lan_tao.append(1))

        ma, _, body = chay(mod.chat(
            {"messages": [{"role": "user", "content": "Hi"}]}, stream=False))
        assert ma == 502, body  # lỗi kết nối, không phải 504
        assert so_lan_tao == []  # KHÔNG nối lại: lời nhắn đã gửi
        assert gw.da_mo.count("chat.stream") == 1


# ── #1: Nối lại mà dam_bao ném AuthError → không được treo ──────────────

class TestNoiLaiAuthError:
    """Kết nối cũ chết trước khi gửi; lúc nối lại cookie bị từ chối (401 thật
    từ /api/session qua fetch_session_info trong Gateway.__init__)."""

    def _dung(self, mod, monkeypatch, cookies):
        mod.nap({"cookies": cookies})
        acc = mod._accs[0]
        # Kết nối cũ: Gateway thật, ws đầu kia đã đóng (ngoại lệ thật của curl_cffi).
        acc._gw = gw_that(WsKichBan([WebSocketClosed("peer closed")]))
        # Nối lại đi qua Gateway.__init__ thật → /api/session trả 401 → AuthError.
        monkeypatch.setattr(pp, "rq", type("RqGia", (), {
            "get": staticmethod(lambda *a, **kw: _Resp(401, {})),
            "post": staticmethod(lambda *a, **kw: _Resp(401, {}))}))
        return acc

    @pytest.mark.parametrize("stream", [False, True])
    def test_mot_tai_khoan_ra_401_khong_treo(self, mod, monkeypatch, stream):
        acc = self._dung(mod, monkeypatch, ["cookie_A_1234567890xx"])

        async def _chay():
            ma, _, body = await asyncio.wait_for(mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=stream), 5)
            assert ma == 401, body
            assert json.loads(body)["error"]["type"] == "khoa_hong"
            assert acc.khoe is False

        chay(_chay())

    @pytest.mark.parametrize("stream", [False, True])
    def test_hai_tai_khoan_chuyen_sang_b(self, mod, monkeypatch, stream):
        acc_a = self._dung(mod, monkeypatch,
                           ["cookie_A_1234567890xx", "cookie_B_1234567890yy"])
        gw_b = GatewayGia()
        gw_b.lich_su = [su_kien(10, "Từ B")]
        mod._accs[1]._gw = gw_b

        async def _chay():
            ma, _, kq = await asyncio.wait_for(mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=stream), 5)
            assert ma == 200
            if stream:
                kq = b"".join([c async for c in kq])
                assert "Từ B" in kq.decode() and b"[DONE]" in kq
            else:
                assert json.loads(kq)["choices"][0]["message"]["content"] == "Từ B"
            assert acc_a.khoe is False

        chay(_chay())


# ── #2: Gateway.request() không bọc lỗi ws thành TimeoutError ───────────

class TestGatewayLoiWs:
    @pytest.mark.parametrize("loi", [WebSocketClosed("peer closed"),
                                     OSError("connection reset")])
    def test_recv_loi_la_gateway_error(self, loi):
        gw = gw_that(WsKichBan([loi]))
        t0 = time.time()
        with pytest.raises(pp.GatewayError) as ei:
            gw.request("chat.history", body={"limit": 1}, timeout=5)
        assert not isinstance(ei.value, TimeoutError)
        assert ei.value.status == -1
        assert time.time() - t0 < 1.0

    def test_ws_that_chua_ket_noi_la_gateway_error(self):
        """WebSocket thật của curl_cffi không có socket: send/recv ném WebSocketError."""
        gw = gw_that(WebSocket())
        with pytest.raises(pp.GatewayError):
            gw.request("chat.history", body={"limit": 1}, timeout=5)

    def test_giai_ma_noise_hong_la_gateway_error(self):
        gw = gw_that(WsKichBan([b"rac"]))

        class NoiseHong(_NoiseThang):
            def decrypt(self, d):
                raise ValueError("bad tag")

        gw.noise = NoiseHong()
        with pytest.raises(pp.GatewayError):
            gw.request("chat.history", body={"limit": 1}, timeout=5)

    def test_het_luong_khong_co_status_la_gateway_error(self):
        sf = pp.ServiceFrame(stream_id=1)
        sf.body_chunk.data = b"x"
        sf.body_chunk.end_body = True
        sr = pp.ServiceResponse(payload=sf.SerializeToString())
        khung = pp.NoiseTransportFrame(payload=sr.SerializeToString()).SerializeToString()
        gw = gw_that(WsKichBan([khung]))
        with pytest.raises(pp.GatewayError) as ei:
            gw.request("chat.history", body={"limit": 1}, timeout=5)
        assert not isinstance(ei.value, TimeoutError)


# ── Ca 7: 5xx/429 khi lấy token → không biến thành AuthError ────────────

class TestPhanLoai5xx429:
    def test_5xx_khong_phai_auth_error(self, mod):
        mod.nap({"cookies": ["cookie_5xx_test_1234567890"]})
        acc = mod._accs[0]

        def _dam_bao_5xx():
            raise pp.GatewayError(500, "Internal Server Error")

        acc.dam_bao = _dam_bao_5xx

        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 502  # GatewayError → 502 may_chu_loi
            assert acc.khoe is True  # KHÔNG đặt khoe = False

        chay(_chay())

    def test_429_khong_phai_auth_error(self, mod):
        mod.nap({"cookies": ["cookie_429_test_1234567890"]})
        acc = mod._accs[0]

        def _dam_bao_429():
            raise pp.GatewayError(429, "Rate limited")

        acc.dam_bao = _dam_bao_429

        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 429
            assert acc.khoe is True

        chay(_chay())

    def test_401_that_thi_khoe_false(self, mod):
        mod.nap({"cookies": ["cookie_401_test_1234567890"]})
        acc = mod._accs[0]

        def _dam_bao_401():
            raise pp.AuthError("cookies expired")

        acc.dam_bao = _dam_bao_401

        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 401
            assert acc.khoe is False

        chay(_chay())


# ── Ca 8: messages chứa phần tử không dict → 400 ────────────────────────

class TestYeuCauSai:
    def test_phan_tu_khong_dict(self, mod_1cookie):
        mod, acc, gw = mod_1cookie

        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": ["chuỗi trần"]}, stream=False)
            assert ma == 400
            d = json.loads(body)
            assert "đối tượng" in d["error"]["message"]

        chay(_chay())

    def test_thieu_cookie(self, mod):
        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 401

        chay(_chay())

    def test_messages_rong(self, mod_1cookie):
        mod, acc, gw = mod_1cookie

        async def _chay():
            ma, _, body = await mod.chat({"messages": []}, stream=False)
            assert ma == 400

        chay(_chay())


# ── Ca 9: recv chặn → TimeoutError ───────────────────────────────────────

class TestRecvTimeout:
    def test_request_timeout_voi_recv_chan(self):
        """ws giả có recv() chặn → request(..., timeout=0.3) phải ném TimeoutError."""

        class WsGia:
            def __init__(self):
                self._ev = threading.Event()
                self.da_close = False

            def recv(self):
                self._ev.wait()  # chặn cho tới khi close() được gọi
                raise OSError("ws closed")

            def close(self):
                self.da_close = True
                self._ev.set()

            def send_bytes(self, data):
                pass

            def connect(self, *a, **kw):
                pass

        ws = WsGia()

        class GwTest(pp.Gateway):
            def __init__(self):
                self.ws = ws
                self.stream = 1
                self._send_lock = threading.Lock()
                self._recv_lock = threading.Lock()

                class FakeNoise:
                    def encrypt(self, data):
                        return data

                    def decrypt(self, data):
                        return data

                self.noise = FakeNoise()

        gw = GwTest()
        t0 = time.time()
        with pytest.raises(TimeoutError):
            gw.request("chat.history", body={"limit": 1}, timeout=0.3)
        dt = time.time() - t0
        assert dt < 2.0
        assert ws.da_close


# ── Ca 10: Khách ngắt stream → khoá nhả ─────────────────────────────────

class TestKhachNgatStream:
    def test_khoa_nha_khi_khach_ngat(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        lan = [0]

        def _call_json_cham(method, **kw):
            if method == "chat.history":
                lan[0] += 1
                if lan[0] <= 1:
                    return {"chat_events": []}  # baseline
                return {"chat_events": [su_kien(10, "Phần 1")]}
            return {"chat_events": []}

        gw.call_json = _call_json_cham

        async def _chay():
            ket = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=True)
            ma, hd, gen = ket
            assert ma == 200
            # Nhận chunk đầu
            chunk = await gen.__aiter__().__anext__()
            assert chunk
            # Đóng stream (khách ngắt)
            await gen.aclose()
            # Khoá phải được nhả
            for _ in range(40):
                if not acc.khoa.locked():
                    break
                await asyncio.sleep(0.05)
            assert not acc.khoa.locked()

        chay(_chay())


# ── Ca 11: Hai cookie, A bị AuthError ───────────────────────────────────

class TestHaiCookieAuthError:
    def test_a_hong_b_dung(self, mod):
        mod.nap({"cookies": ["cookie_A_bad_1234567890xx", "cookie_B_ok_1234567890yy"]})
        acc_a = mod._accs[0]
        acc_b = mod._accs[1]

        # A ném AuthError khi kết nối
        def _dam_bao_a_loi():
            raise pp.AuthError("cookie A expired")

        acc_a.dam_bao = _dam_bao_a_loi

        gw_b = GatewayGia()
        gw_b.lich_su = [su_kien(10, "Từ B")]
        acc_b._gw = gw_b

        async def _chay():
            # Lượt 1: A bị AuthError trước khi gửi -> tự chuyển sang B ngay trong cùng yêu cầu!
            ma1, _, body1 = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma1 == 200
            assert json.loads(body1)["choices"][0]["message"]["content"] == "Từ B"
            assert acc_a.khoe is False

            # Lượt 2: A đã khoe=False nên dùng tiếp B
            gw_b.lich_su = [su_kien(20, "Từ B lượt 2")]
            ma2, _, body2 = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma2 == 200
            assert json.loads(body2)["choices"][0]["message"]["content"] == "Từ B lượt 2"

        chay(_chay())

    def test_ca_hai_deu_hong_thi_401(self, mod):
        mod.nap({"cookies": ["cookie_A_bad_1234567890xx", "cookie_B_bad_1234567890yy"]})
        for acc in mod._accs:
            def _loi():
                raise pp.AuthError("expired")
            acc.dam_bao = _loi

        async def _chay():
            ma, _, body = await mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False)
            assert ma == 401
            d = json.loads(body)
            assert "error" in d

        chay(_chay())


# ── Ca 12: Mọi manifest qua goi_thieu ───────────────────────────────────

GOC = Path(__file__).resolve().parents[1]


class TestGoiThieu:
    def test_moi_manifest_goi_thieu_rong(self):
        """Mọi modules/*/manifest.json phải có yeu_cau_goi đã cài đủ."""
        thu_muc_modules = GOC / "modules"
        if not thu_muc_modules.is_dir():
            pytest.skip("không có thư mục modules/")
        co_manifest = False
        for p in sorted(thu_muc_modules.iterdir()):
            if not p.is_dir() or p.name.startswith((".", "_")):
                continue
            manifest, loi = kham_pha.doc_manifest(p)
            if manifest is None:
                continue
            co_manifest = True
            thieu = kham_pha.goi_thieu(manifest)
            assert thieu == [], (
                f"modules/{p.name}/manifest.json: gói thiếu {thieu} "
                f"(yeu_cau_goi = {manifest.get('yeu_cau_goi')})")
        assert co_manifest, "không tìm thấy manifest.json nào trong modules/"

    def test_nap_ma_muse2api(self):
        """nap_ma nạp được module muse2api."""
        thu_muc = GOC / "modules" / "muse2api"
        if not thu_muc.is_dir():
            pytest.skip("modules/muse2api không có")
        manifest, loi = kham_pha.doc_manifest(thu_muc)
        assert manifest is not None, f"manifest lỗi: {loi}"
        vt = kham_pha.van_tay(thu_muc)
        lop, ten = kham_pha.nap_ma(thu_muc, manifest, vt)
        assert lop is not None
        assert hasattr(lop, "chat")


# ── Ca bổ sung: che cookie đúng quy ước ─────────────────────────────────

class TestCheCookie:
    def test_che_chi_hien_4_ky_tu_cuoi(self):
        assert mm._che("hatch_sess=abc123def456789012; hatch_gw=xyz") == "…9012"

    def test_che_ngan(self):
        assert mm._che("hatch_sess=ab; foo=x") == "…"

    def test_che_khong_co_bang(self):
        assert mm._che("abcdef1234567890") == "…7890"

    def test_che_ngan_khong_bang(self):
        assert mm._che("abcd") == "…"


# ── Ca bổ sung: cai() phân loại đúng cookie từ chối vs lỗi tạm ──────────

class TestCaiPhanLoai:
    def test_cai_giu_cookie_loi_tam(self, mod, monkeypatch):
        """Cookie lỗi tạm (5xx/mạng) vẫn giữ trong cau_hinh_luu nhưng ok: false."""
        def _fake_kiem(cookies: str):
            if "bad" in cookies:
                return False, "cookie expired", True  # AuthError
            if "temp" in cookies:
                return False, "network error", False  # GatewayError/tạm
            return True, None, False  # OK

        monkeypatch.setattr(mod, "_kiem_cookie", _fake_kiem)

        kq = chay(mod.cai({"cookies": ["cookie_ok_12345678", "cookie_temp_12345678", "cookie_bad_12345678"]}))
        assert kq["ok"] is True
        # cau_hinh_luu giữ cookie ok và cookie temp, loại bỏ cookie bad
        luu = kq["cau_hinh_luu"]["cookies"]
        assert "cookie_ok_12345678" in luu
        assert "cookie_temp_12345678" in luu
        assert "cookie_bad_12345678" not in luu


# ── Ca bổ sung: build_prompt với role không phải chuỗi ───────────────────

class TestBuildPrompt:
    def test_role_khong_phai_chuoi(self):
        """role không phải chuỗi → ép str trước capitalize."""
        prompt = cc.build_prompt([{"role": 123, "content": "test"}])
        assert "123" in prompt.lower() or "Test" in prompt

    # #4: hình dạng content lạ không được ném ngoại lệ
    def test_text_null(self):
        assert cc.build_prompt(
            [{"role": "user", "content": [{"type": "text", "text": None}]}]) == ""

    def test_text_la_so_bo_qua_giu_phan_chuoi(self):
        p = cc.build_prompt([{"role": "user", "content": [
            {"type": "text", "text": 5}, {"type": "text", "text": "chào"}]}])
        assert p == "User: chào"

    @pytest.mark.parametrize("content", [{"type": "text", "text": "x"}, 42, 4.5])
    def test_content_dict_hoac_so_khong_nem(self, content):
        cc.build_prompt([{"role": "user", "content": content}])

    def test_chat_text_null_ra_400(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        ma, _, body = chay(mod.chat({"messages": [
            {"role": "user", "content": [{"type": "text", "text": None}]}]}, stream=False))
        assert ma == 400
        assert gw.open_calls == []


# ── #3: Khách ngắt khi còn xếp hàng chờ khoá → không gửi lời nhắn ──────

class TestHuyKhiXepHang:
    @pytest.mark.parametrize("stream", [False, True])
    def test_b_bi_huy_khong_mo_chat_stream(self, mod_1cookie, stream):
        mod, acc, gw = mod_1cookie
        gw.lich_su = [su_kien(10, "trả lời")]

        async def _chay():
            acc.khoa.acquire()  # «A» đang giữ khoá tài khoản
            try:
                b = asyncio.create_task(mod.chat(
                    {"messages": [{"role": "user", "content": "B"}]}, stream=stream))
                await asyncio.sleep(0.2)  # B đã vào hàng chờ khoá
                b.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await b
            finally:
                acc.khoa.release()
            await asyncio.sleep(0.3)  # cho worker của B kịp chạy nếu còn sống
            for _ in range(60):
                if not acc.khoa.locked():
                    break
                await asyncio.sleep(0.05)
            assert not acc.khoa.locked()
            assert gw.open_calls == []
            assert gw.call_json_calls == []

        chay(_chay())


# ── #5a: _last_seq chịu được "seq": null ────────────────────────────────

class TestLastSeqNull:
    def test_seq_null(self):
        gw = GatewayGia()
        gw.lich_su = [{"event_name": "message.assistant", "seq": None,
                       "payload": {"display_text": "x"}}, su_kien(7, "y")]
        assert cc.ChatSession(gw, "node-test")._last_seq() == 7


# ── #5b, #5e: bắt tay Noise trong Gateway.__init__ ──────────────────────

class TestBatTay:
    def test_bat_tay_hong_thi_dong_ws(self, monkeypatch):
        ws = WsKichBan([b"khong-phai-noise"])  # thông điệp 2 rác → read_message hỏng
        monkeypatch.setattr(pp, "WebSocket", lambda: ws)
        with pytest.raises(pp.GatewayError):
            pp.Gateway("c=gia", vm_id="vm-gia", access_token="tok-gia",
                       hatch_token="hatch-gia")
        assert ws.so_close >= 1

    def test_han_chot_bat_tay(self, monkeypatch):
        ws = WsImLang()
        monkeypatch.setattr(pp, "WebSocket", lambda: ws)
        monkeypatch.setattr(pp.Gateway, "HAN_BAT_TAY", 0.3, raising=False)
        kq: list = []

        def _tao():
            try:
                pp.Gateway("c=gia", vm_id="vm-gia", access_token="tok-gia",
                           hatch_token="hatch-gia")
                kq.append(None)
            except BaseException as e:  # noqa: BLE001 - ghi lại để kiểm
                kq.append(e)

        t = threading.Thread(target=_tao, daemon=True)
        t0 = time.time()
        t.start()
        t.join(2)
        try:
            assert not t.is_alive(), "bắt tay không dừng trong 2 giây"
            assert isinstance(kq[0], TimeoutError), kq
            assert time.time() - t0 < 2.0
            assert ws.so_close >= 1
        finally:
            ws.close()  # gỡ chặn nếu test đỏ


# ── #5d: _kiem_cookie THẬT với HTTP giả ở mức curl_cffi ─────────────────

class TestKiemCookieThat:
    def test_chi_401_403_bi_loai(self, mod, monkeypatch):
        tra_loi = {
            "c_ok": _Resp(200, {"vm_id": "vm-gia"}),
            "c_401": _Resp(401, {}),
            "c_403": _Resp(403, {}),
            "c_429": _Resp(429, {}),
            "c_503": _Resp(503, {}),
            "c_khongjson": _Resp(200, "<html>bảo trì</html>"),
        }

        def _get(url, headers=None, **kw):
            return tra_loi[headers["Cookie"].split("=")[0]]

        def _post(url, headers=None, **kw):
            return _Resp(200, {"access_token": "tok-gia"})

        monkeypatch.setattr(pp, "rq", type("RqGia", (), {
            "get": staticmethod(_get), "post": staticmethod(_post)}))
        cookies = [f"{k}=gia_tri_1234567890" for k in tra_loi]
        kq = chay(mod.cai({"cookies": cookies}))
        assert kq["ok"] is True
        ok = {c.split("=")[0]: m["ok"] for c, m in zip(cookies, kq["ket_qua_tung_khoa"])}
        assert ok == {"c_ok": True, "c_401": False, "c_403": False,
                      "c_429": False, "c_503": False, "c_khongjson": False}
        giu = [c.split("=")[0] for c in kq["cau_hinh_luu"]["cookies"]]
        assert giu == ["c_ok", "c_429", "c_503", "c_khongjson"]


# ── A: tài khoản hỏng thoáng qua phải được thử lại, khoe hồi phục ───────

class TestXoayVongKhiKhongAiKhoe:
    def test_a_hong_b_thoang_qua_roi_hoi_phuc(self, mod):
        mod.nap({"cookies": ["cookie_A_het_han_1234567890", "cookie_B_tot_1234567890"]})
        acc_a, acc_b = mod._accs

        def _a_luon_hong():
            raise pp.AuthError("auth/check -> 401")

        gw_b = GatewayGia()
        gw_b.lich_su = [su_kien(10, "Từ B")]
        lan_b = [0]

        def _b_thoang_qua():
            lan_b[0] += 1
            if lan_b[0] == 1:
                raise pp.AuthError("auth/check -> 403")  # 403 thoáng qua đúng một lần
            return gw_b

        acc_a.dam_bao = _a_luon_hong
        acc_b.dam_bao = _b_thoang_qua
        hoi = {"messages": [{"role": "user", "content": "Hi"}]}

        async def _chay():
            ma, _, _ = await asyncio.wait_for(mod.chat(hoi, stream=False), 5)
            assert ma == 401
            assert acc_a.khoe is False and acc_b.khoe is False
            for _ in range(3):  # B đã hồi phục: mọi lượt sau phải tới được B
                ma, _, body = await asyncio.wait_for(mod.chat(hoi, stream=False), 5)
                assert ma == 200, body
                assert json.loads(body)["choices"][0]["message"]["content"] == "Từ B"
            assert acc_b.khoe is True

        chay(_chay())

    def test_khoe_lai_sau_luot_thanh_cong(self, mod_1cookie):
        mod, acc, gw = mod_1cookie
        acc.khoe = False
        gw.lich_su = [su_kien(10, "ok")]
        ma, _, _ = chay(mod.chat({"messages": [{"role": "user", "content": "Hi"}]},
                                 stream=False))
        assert ma == 200
        assert acc.khoe is True


# ── C: nội dung phản hồi thượng nguồn không đi tới khách API ────────────

class TestKhongLoNoiDungThuongNguon:
    EMAIL = "ai.do@vi-du.invalid"

    def _rq_phien(self, monkeypatch):
        monkeypatch.setattr(pp, "rq", type("RqGia", (), {
            "get": staticmethod(lambda *a, **kw: _Resp(200, {"email": self.EMAIL,
                                                             "trang_thai": "dang_cap"})),
            "post": staticmethod(lambda *a, **kw: _Resp(200, {"access_token": "tok-gia"}))}))

    def test_vm_unavailable_chi_neu_ten_khoa(self, monkeypatch):
        self._rq_phien(monkeypatch)
        with pytest.raises(pp.GatewayError) as ei:
            pp.fetch_session_info("c=gia")
        assert self.EMAIL not in str(ei.value)
        assert "email" in str(ei.value)  # tên khoá vẫn có để chẩn đoán

    def test_chat_502_khong_chua_noi_dung_phien(self, mod, monkeypatch):
        mod.nap({"cookies": ["cookie_vm_chua_san_1234567890"]})
        self._rq_phien(monkeypatch)
        ma, _, body = chay(mod.chat({"messages": [{"role": "user", "content": "Hi"}]},
                                    stream=False))
        assert ma == 502
        assert self.EMAIL not in body.decode()

    def test_than_loi_thuong_nguon_chi_vao_log(self, mod, monkeypatch, caplog):
        mod.nap({"cookies": ["cookie_5xx_1234567890"]})

        def _loi():
            raise pp.GatewayError(500, "noi-dung-thuong-nguon-gia")

        mod._accs[0].dam_bao = _loi
        with caplog.at_level("WARNING", logger="web2api.module.muse2api"):
            ma, _, body = chay(mod.chat(
                {"messages": [{"role": "user", "content": "Hi"}]}, stream=False))
        assert ma == 502
        thong_bao = json.loads(body)["error"]["message"]
        assert "noi-dung-thuong-nguon-gia" not in thong_bao
        assert thong_bao.startswith("Lỗi máy chủ hoặc kết nối")
        assert "500" in thong_bao
        # Log hệ thống giữ nguyên nội dung thuần chữ thường; loại lỗi và mã còn đủ để tra.
        assert "GatewayError" in caplog.text and "status=500" in caplog.text
        assert "noi-dung-thuong-nguon-gia" in caplog.text
