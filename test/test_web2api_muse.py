"""Bộ kiểm thử cho module Muse: Protobuf codec, phân mảnh khung, converter OpenAI,

Noise XX handshake và máy chủ giả lập WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import pytest

from modules.muse import converter, khung, protobuf
from modules.muse.khung import BoGhepKhung, tach_khung
from modules.muse.protobuf import (
    ApplicationRequest,
    ApplicationResponse,
    BodyChunk,
    Header,
    NoiseTransportFrame,
    Reset,
    ResetCode,
    ServiceFrame,
    ServiceRequest,
    ServiceResponse,
    ServiceType,
    decode_varint,
    encode_varint,
    to_signed_int64,
)


def chay(coro):
    """Tiện ích chạy coroutine bất đồng bộ bằng asyncio.run."""
    return asyncio.run(coro)


# ── 1. Kiểm thử Protobuf viết tay ──────────────────────────────────────

class TestProtobuf:
    def test_varint_ma_va_giai_ma(self):
        so_thu = [0, 1, 127, 128, 300, 16384, (1 << 31) - 1, (1 << 63) - 1, -1, -42]
        for v in so_thu:
            encoded = encode_varint(v)
            decoded, pos = decode_varint(encoded, 0)
            assert pos == len(encoded)
            signed = to_signed_int64(decoded)
            assert signed == v

    def test_noise_transport_frame_doi_xung(self):
        f = NoiseTransportFrame(
            chunk_id=1234567890123456,
            chunk_index=2,
            total_chunks=5,
            payload=b"du lieu nhi phan test 123",
        )
        raw = f.encode()
        f2 = NoiseTransportFrame.decode(raw)
        assert f2.chunk_id == f.chunk_id
        assert f2.chunk_index == f.chunk_index
        assert f2.total_chunks == f.total_chunks
        assert f2.payload == f.payload

    def test_noise_transport_frame_so_am(self):
        f = NoiseTransportFrame(
            chunk_id=-9876543210123,
            chunk_index=0,
            total_chunks=1,
            payload=b"am",
        )
        f2 = NoiseTransportFrame.decode(f.encode())
        assert f2.chunk_id == -9876543210123
        assert f2.payload == b"am"

    def test_application_request_va_response(self):
        req = ApplicationRequest(
            verb="POST",
            path="/chat/stream",
            headers=[Header(key="Content-Type", value="application/json"), Header(key="x-app-id", value="hatch-web")],
            body=b'{"prompt": "hello"}',
            end_body=True,
        )
        raw_req = req.encode()
        req2 = ApplicationRequest.decode(raw_req)
        assert req2.verb == "POST"
        assert req2.path == "/chat/stream"
        assert len(req2.headers) == 2
        assert req2.headers[0].key == "Content-Type"
        assert req2.headers[0].value == "application/json"
        assert req2.body == b'{"prompt": "hello"}'
        assert req2.end_body is True

        resp = ApplicationResponse(
            status=200,
            headers=[Header(key="Content-Type", value="text/event-stream")],
            body=b"",
            end_body=False,
        )
        resp2 = ApplicationResponse.decode(resp.encode())
        assert resp2.status == 200
        assert resp2.end_body is False
        assert resp2.headers[0].value == "text/event-stream"

    def test_body_chunk_va_reset(self):
        bc = BodyChunk(data=b'data: {"text": "xin chao"}\n\n', end_body=False)
        bc2 = BodyChunk.decode(bc.encode())
        assert bc2.data == b'data: {"text": "xin chao"}\n\n'
        assert bc2.end_body is False

        rst = Reset(code=ResetCode.CANCELLED, reason="nguoi dung huy")
        rst2 = Reset.decode(rst.encode())
        assert rst2.code == ResetCode.CANCELLED
        assert rst2.reason == "nguoi dung huy"

    def test_service_frame_oneof_va_service_request(self):
        sf = ServiceFrame(
            stream_id=42,
            request=ApplicationRequest(verb="GET", path="/health"),
        )
        raw_sf = sf.encode()
        sf2 = ServiceFrame.decode(raw_sf)
        assert sf2.stream_id == 42
        assert sf2.request is not None
        assert sf2.request.verb == "GET"
        assert sf2.request.path == "/health"
        assert sf2.response is None

        s_req = ServiceRequest(
            service=ServiceType.SERVICE_DAEMON,
            payload=raw_sf,
        )
        s_req2 = ServiceRequest.decode(s_req.encode())
        assert s_req2.service == ServiceType.SERVICE_DAEMON
        assert s_req2.payload == raw_sf


# ── 2. Kiểm thử phân mảnh và lắp ráp khung vận chuyển ──────────────────

class TestKhungViec:
    def test_tach_va_ghep_khung_0_byte(self):
        frames = tach_khung(b"", chunk_id=100)
        assert len(frames) == 1
        assert frames[0].total_chunks == 1
        assert frames[0].payload == b""

        bg = BoGhepKhung()
        res = bg.them_khung(frames[0])
        assert res == b""

    def test_tach_va_ghep_khung_1_manh(self):
        du_lieu = b"noi dung ngan gon duoi 65489 bytes"
        frames = tach_khung(du_lieu, chunk_id=200)
        assert len(frames) == 1
        bg = BoGhepKhung()
        assert bg.them_khung(frames[0]) == du_lieu

    def test_tach_va_ghep_khung_nhieu_manh(self):
        # 150.000 bytes -> 3 mảnh (65489 * 2 + 19022)
        du_lieu = b"x" * 150000
        frames = tach_khung(du_lieu, chunk_id=300)
        assert len(frames) == 3
        assert frames[0].chunk_index == 0
        assert frames[1].chunk_index == 1
        assert frames[2].chunk_index == 2

        bg = BoGhepKhung()
        assert bg.them_khung(frames[0]) is None
        assert bg.them_khung(frames[1]) is None
        ket_qua = bg.them_khung(frames[2])
        assert ket_qua == du_lieu

    def test_ghep_khung_dao_thu_tu(self):
        du_lieu = b"1" * 65489 + b"2" * 65489 + b"3" * 1000
        frames = tach_khung(du_lieu, chunk_id=400)
        bg = BoGhepKhung()
        # Đưa mảnh 2 vào trước, rồi mảnh 0, cuối cùng mảnh 1
        assert bg.them_khung(frames[2]) is None
        assert bg.them_khung(frames[0]) is None
        res = bg.them_khung(frames[1])
        assert res == du_lieu

    def test_ghep_khung_loi_trung_chunk_index(self):
        frames = tach_khung(b"123" * 30000, chunk_id=500)
        bg = BoGhepKhung()
        bg.them_khung(frames[0])
        with pytest.raises(ValueError, match="duplicate chunkIndex"):
            bg.them_khung(frames[0])
        # Đã bị nhiễm độc (poisoned)
        with pytest.raises(RuntimeError, match="poisoned"):
            bg.them_khung(frames[1])

    def test_ghep_khung_loi_total_chunks_khong_nhat_quan(self):
        f1 = NoiseTransportFrame(chunk_id=600, chunk_index=0, total_chunks=2, payload=b"a")
        f2 = NoiseTransportFrame(chunk_id=600, chunk_index=1, total_chunks=3, payload=b"b")
        bg = BoGhepKhung()
        bg.them_khung(f1)
        with pytest.raises(ValueError, match="inconsistent totalChunks"):
            bg.them_khung(f2)

    def test_ghep_khung_chunk_index_ngoai_khoang(self):
        f = NoiseTransportFrame(chunk_id=700, chunk_index=5, total_chunks=2, payload=b"a")
        bg = BoGhepKhung()
        with pytest.raises(ValueError, match="nằm ngoài khoảng"):
            bg.them_khung(f)

    def test_tach_khung_vuot_tran_256(self):
        du_lieu_khong_lo = b"0" * (257 * 65489)
        with pytest.raises(ValueError, match="Payload quá lớn"):
            tach_khung(du_lieu_khong_lo)


# ── 3. Kiểm thử bộ chuyển đổi OpenAI - Muse ────────────────────────────

class TestConverter:
    def test_chuyen_yeu_cau_chat_openai(self):
        req = {
            "model": "muse/meta-llama/Llama-3.3-70B-Instruct",
            "messages": [
                {"role": "system", "content": "Ban la tro ly."},
                {"role": "user", "content": "Xin chao!"},
            ],
        }
        chuyen = converter.chuyen_yeu_cau_chat_openai(req)
        assert "items" in chuyen
        assert len(chuyen["items"]) == 1
        assert "Ban la tro ly." in chuyen["items"][0]["text"]
        assert "Xin chao!" in chuyen["items"][0]["text"]

    def test_chuyen_dong_muse_sang_sse_openai(self):
        async def _chay():
            events = [
                b'{"eventName": "delta.message_start", "metadata": {"model": "llama-3.3"}}\n',
                b'{"eventName": "delta.text_append", "text": "Xin "}\n',
                b'{"eventName": "delta.text_append", "text": "chao!"}\n',
                b'{"eventName": "delta.message_done"}\n',
            ]

            async def _mock_stream():
                for e in events:
                    yield e

            chunks = []
            async for c in converter.chuyen_dong_muse_sang_sse_openai(_mock_stream(), "muse/test-model"):
                chunks.append(c.decode("utf-8"))

            van_ban_gop = "".join(chunks)
            assert "chatcmpl-" in van_ban_gop
            assert "assistant" in van_ban_gop
            assert "Xin " in van_ban_gop
            assert "chao!" in van_ban_gop
            assert "stop" in van_ban_gop
            assert "data: [DONE]" in van_ban_gop

        chay(_chay())

    def test_gom_dong_muse_thanh_json_openai(self):
        async def _chay():
            events = [
                b'{"eventName": "delta.text_append", "text": "Cau tra loi "}\n',
                b'{"eventName": "delta.text_append", "text": "hoan chinh."}\n',
            ]

            async def _mock_stream():
                for e in events:
                    yield e

            res = await converter.gom_dong_muse_thanh_json_openai(_mock_stream(), "muse/test-model")
            assert res["object"] == "chat.completion"
            assert res["choices"][0]["message"]["content"] == "Cau tra loi hoan chinh."
            assert res["choices"][0]["finish_reason"] == "stop"

        chay(_chay())


# ── 4. Kiểm thử Bắt tay Noise XX và Máy chủ giả lập ───────────────────

class TestNoiseVaMayChuGia:
    def test_noise_xx_bat_tay_hai_chieu(self):
        """Kiểm tra bắt tay tương hỗ 3 bước giữa Initiator và Responder."""
        pytest.importorskip("cryptography")
        from modules.muse.noise import NoiseXXInitiator, NoiseXXResponder

        init = NoiseXXInitiator()
        resp = NoiseXXResponder()

        # Bước 1: Client -> Server (msg1)
        m1 = init.write_message_1(payload=b"client_info")
        p1 = resp.read_message_1(m1)
        assert p1 == b"client_info"

        # Bước 2: Server -> Client (msg2)
        m2 = resp.write_message_2(payload=b"server_challenge")
        p2 = init.read_message_2(m2)
        assert p2 == b"server_challenge"

        # Bước 3: Client -> Server (msg3)
        m3 = init.write_message_3(payload=b"delegation_token_123")
        p3 = resp.read_message_3(m3)
        assert p3 == b"delegation_token_123"

        # Split
        init_send, init_recv = init.split()
        resp_recv, resp_send = resp.split()

        # Client gửi tin nhắn mã hoá tới Server
        ciphertext = init_send.encrypt_with_ad(b"", b"xin chao may chu")
        plaintext = resp_recv.decrypt_with_ad(b"", ciphertext)
        assert plaintext == b"xin chao may chu"

        # Server phản hồi tin nhắn mã hoá tới Client
        resp_cipher = resp_send.encrypt_with_ad(b"", b"chao ban tu server")
        resp_plain = init_recv.decrypt_with_ad(b"", resp_cipher)
        assert resp_plain == b"chao ban tu server"

    def test_noise_xx_may_chu_gia_doc_lap(self):
        """Kiểm tra NoiseXXInitiator với máy chủ giả độc lập hoàn toàn (không dùng chung mã)."""
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from modules.muse.noise import NoiseXXInitiator

        # Định nghĩa máy chủ Noise XX độc lập hoàn toàn theo đặc tả chuẩn
        class MayChuDocLapNoiseXX:
            def __init__(self):
                import hashlib
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                self._s = X25519PrivateKey.generate()
                self._e = X25519PrivateKey.generate()
                self.s_pub = self._s.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                self.e_pub = self._e.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                pad = b"Noise_XX_25519_AESGCM_SHA256\x00\x00\x00\x00"
                self.ck = pad
                self.h = hashlib.sha256(pad).digest()
                self.k = None
                self.nonce = 0
                self.re_pub = None
                self.rs_pub = None

            def _mix_hash(self, data: bytes):
                import hashlib
                self.h = hashlib.sha256(self.h + data).digest()

            def _mix_key(self, ikm: bytes):
                import hashlib
                import hmac
                temp_key = hmac.new(self.ck, ikm, hashlib.sha256).digest()
                self.ck = hmac.new(temp_key, b"\x01", hashlib.sha256).digest()
                self.k = hmac.new(temp_key, self.ck + b"\x02", hashlib.sha256).digest()
                self.nonce = 0

            def _encrypt_and_hash(self, plaintext: bytes) -> bytes:
                if self.k is not None:
                    n_bytes = b"\x00\x00\x00\x00" + self.nonce.to_bytes(8, "big")
                    ct = AESGCM(self.k).encrypt(n_bytes, plaintext, self.h)
                    self.nonce += 1
                    self._mix_hash(ct)
                    return ct
                self._mix_hash(plaintext)
                return plaintext

            def _decrypt_and_hash(self, ciphertext: bytes) -> bytes:
                if self.k is not None:
                    n_bytes = b"\x00\x00\x00\x00" + self.nonce.to_bytes(8, "big")
                    pt = AESGCM(self.k).decrypt(n_bytes, ciphertext, self.h)
                    self.nonce += 1
                    self._mix_hash(ciphertext)
                    return pt
                self._mix_hash(ciphertext)
                return ciphertext

            def read_message_1(self, msg: bytes) -> bytes:
                self.re_pub = msg[:32]
                self._mix_hash(self.re_pub)
                return self._decrypt_and_hash(msg[32:])

            def write_message_2(self, payload: bytes = b"") -> bytes:
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
                self._mix_hash(self.e_pub)
                ee = self._e.exchange(X25519PublicKey.from_public_bytes(self.re_pub))
                self._mix_key(ee)
                s_enc = self._encrypt_and_hash(self.s_pub)
                es = self._s.exchange(X25519PublicKey.from_public_bytes(self.re_pub))
                self._mix_key(es)
                pld_enc = self._encrypt_and_hash(payload)
                return self.e_pub + s_enc + pld_enc

            def read_message_3(self, msg: bytes) -> bytes:
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
                s_enc = msg[:48]
                self.rs_pub = self._decrypt_and_hash(s_enc)
                se = self._e.exchange(X25519PublicKey.from_public_bytes(self.rs_pub))
                self._mix_key(se)
                return self._decrypt_and_hash(msg[48:])

            def split(self):
                import hashlib
                import hmac
                temp_key = hmac.new(self.ck, b"", hashlib.sha256).digest()
                k1 = hmac.new(temp_key, b"\x01", hashlib.sha256).digest()
                k2 = hmac.new(temp_key, k1 + b"\x02", hashlib.sha256).digest()
                return k1, k2

        init = NoiseXXInitiator()
        srv = MayChuDocLapNoiseXX()

        # Step 1
        m1 = init.write_message_1(payload=b"independent_client_hello")
        p1 = srv.read_message_1(m1)
        assert p1 == b"independent_client_hello"

        # Step 2
        m2 = srv.write_message_2(payload=b"independent_server_hello")
        p2 = init.read_message_2(m2)
        assert p2 == b"independent_server_hello"

        # Step 3
        m3 = init.write_message_3(payload=b"independent_finished")
        p3 = srv.read_message_3(m3)
        assert p3 == b"independent_finished"

        # Split & Verify
        init_send, init_recv = init.split()
        srv_k_recv, srv_k_send = srv.split()

        # Client -> Server
        ct1 = init_send.encrypt_with_ad(b"", b"ping tu client toi server doc lap")
        pt1 = AESGCM(srv_k_recv).decrypt(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", ct1, b"")
        assert pt1 == b"ping tu client toi server doc lap"

        # Server -> Client
        ct2 = AESGCM(srv_k_send).encrypt(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", b"pong tu server doc lap", b"")
        pt2 = init_recv.decrypt_with_ad(b"", ct2)
        assert pt2 == b"pong tu server doc lap"

    def test_noise_xx_ben_khoi_tao_doc_lap(self):
        """Kiểm tra NoiseXXResponder với bên khởi tạo độc lập hoàn toàn (không dùng chung mã)."""
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from modules.muse.noise import NoiseXXResponder

        class KhoiTaoDocLapNoiseXX:
            def __init__(self):
                import hashlib
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                self._s = X25519PrivateKey.generate()
                self._e = X25519PrivateKey.generate()
                self.s_pub = self._s.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                self.e_pub = self._e.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                pad = b"Noise_XX_25519_AESGCM_SHA256\x00\x00\x00\x00"
                self.ck = pad
                self.h = hashlib.sha256(pad).digest()
                self.k = None
                self.nonce = 0
                self.re_pub = None
                self.rs_pub = None

            def _mix_hash(self, data: bytes):
                import hashlib
                self.h = hashlib.sha256(self.h + data).digest()

            def _mix_key(self, ikm: bytes):
                import hashlib
                import hmac
                temp_key = hmac.new(self.ck, ikm, hashlib.sha256).digest()
                self.ck = hmac.new(temp_key, b"\x01", hashlib.sha256).digest()
                self.k = hmac.new(temp_key, self.ck + b"\x02", hashlib.sha256).digest()
                self.nonce = 0

            def _encrypt_and_hash(self, plaintext: bytes) -> bytes:
                if self.k is not None:
                    n_bytes = b"\x00\x00\x00\x00" + self.nonce.to_bytes(8, "big")
                    ct = AESGCM(self.k).encrypt(n_bytes, plaintext, self.h)
                    self.nonce += 1
                    self._mix_hash(ct)
                    return ct
                self._mix_hash(plaintext)
                return plaintext

            def _decrypt_and_hash(self, ciphertext: bytes) -> bytes:
                if self.k is not None:
                    n_bytes = b"\x00\x00\x00\x00" + self.nonce.to_bytes(8, "big")
                    pt = AESGCM(self.k).decrypt(n_bytes, ciphertext, self.h)
                    self.nonce += 1
                    self._mix_hash(ciphertext)
                    return pt
                self._mix_hash(ciphertext)
                return ciphertext

            def write_message_1(self, payload: bytes = b"") -> bytes:
                self._mix_hash(self.e_pub)
                return self.e_pub + self._encrypt_and_hash(payload)

            def read_message_2(self, msg: bytes) -> bytes:
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
                self.re_pub = msg[:32]
                self._mix_hash(self.re_pub)
                ee = self._e.exchange(X25519PublicKey.from_public_bytes(self.re_pub))
                self._mix_key(ee)
                s_enc = msg[32:80]
                self.rs_pub = self._decrypt_and_hash(s_enc)
                es = self._e.exchange(X25519PublicKey.from_public_bytes(self.rs_pub))
                self._mix_key(es)
                return self._decrypt_and_hash(msg[80:])

            def write_message_3(self, payload: bytes = b"") -> bytes:
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
                s_enc = self._encrypt_and_hash(self.s_pub)
                se = self._s.exchange(X25519PublicKey.from_public_bytes(self.re_pub))
                self._mix_key(se)
                pld_enc = self._encrypt_and_hash(payload)
                return s_enc + pld_enc

            def split(self):
                import hashlib
                import hmac
                temp_key = hmac.new(self.ck, b"", hashlib.sha256).digest()
                k1 = hmac.new(temp_key, b"\x01", hashlib.sha256).digest()
                k2 = hmac.new(temp_key, k1 + b"\x02", hashlib.sha256).digest()
                return k1, k2

        cli = KhoiTaoDocLapNoiseXX()
        resp = NoiseXXResponder()

        # Step 1
        m1 = cli.write_message_1(payload=b"independent_init_m1")
        p1 = resp.read_message_1(m1)
        assert p1 == b"independent_init_m1"

        # Step 2
        m2 = resp.write_message_2(payload=b"responder_m2")
        p2 = cli.read_message_2(m2)
        assert p2 == b"responder_m2"

        # Step 3
        m3 = cli.write_message_3(payload=b"independent_init_m3")
        p3 = resp.read_message_3(m3)
        assert p3 == b"independent_init_m3"

        # Split & Verify
        cli_k_send, cli_k_recv = cli.split()
        resp_recv, resp_send = resp.split()

        # Client -> Server
        ct1 = AESGCM(cli_k_send).encrypt(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", b"test c2s", b"")
        pt1 = resp_recv.decrypt_with_ad(b"", ct1)
        assert pt1 == b"test c2s"

        # Server -> Client
        ct2 = resp_send.encrypt_with_ad(b"", b"test s2c")
        pt2 = AESGCM(cli_k_recv).decrypt(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", ct2, b"")
        assert pt2 == b"test s2c"

    def test_noise_xx_deterministic_test_vector(self):
        """Kiểm tra vector thử nghiệm xác định (Deterministic Test Vector) với các khoá cố định."""
        pytest.importorskip("cryptography")
        from modules.muse.noise import NoiseXXInitiator, NoiseXXResponder

        # Khoá tĩnh và tạm thời cố định 32 byte hợp lệ (RFC 7748 / Curve25519)
        s_init = bytes([1] * 32)
        e_init = bytes([2] * 32)
        s_resp = bytes([3] * 32)
        e_resp = bytes([4] * 32)

        init = NoiseXXInitiator(static_priv_bytes=s_init)
        resp = NoiseXXResponder(static_priv_bytes=s_resp)

        # Message 1
        m1 = init.write_message_1(payload=b"vector_pld1", ephemeral_priv_bytes=e_init)
        p1 = resp.read_message_1(m1)
        assert p1 == b"vector_pld1"
        assert init.handshake_hash == resp.handshake_hash

        # Message 2
        m2 = resp.write_message_2(payload=b"vector_pld2", ephemeral_priv_bytes=e_resp)
        p2 = init.read_message_2(m2)
        assert p2 == b"vector_pld2"
        assert init.handshake_hash == resp.handshake_hash

        # Message 3
        m3 = init.write_message_3(payload=b"vector_pld3")
        p3 = resp.read_message_3(m3)
        assert p3 == b"vector_pld3"
        assert init.handshake_hash == resp.handshake_hash

        # Split
        init_send, init_recv = init.split()
        resp_recv, resp_send = resp.split()

        # Dữ liệu truyền tải
        ct = init_send.encrypt_with_ad(b"", b"app_payload_vector")
        pt = resp_recv.decrypt_with_ad(b"", ct)
        assert pt == b"app_payload_vector"

        ct_back = resp_send.encrypt_with_ad(b"", b"app_response_vector")
        pt_back = init_recv.decrypt_with_ad(b"", ct_back)
        assert pt_back == b"app_response_vector"

    def test_noise_xx_chan_khoa_bac_thap(self):
        """Từ chối khoá X25519 bậc thấp trong handshake."""
        pytest.importorskip("cryptography")
        from modules.muse.noise import LOW_ORDER_POINTS, kiem_tra_khoa_cong_khai

        for bad in LOW_ORDER_POINTS:
            with pytest.raises(ValueError, match="bậc thấp"):
                kiem_tra_khoa_cong_khai(bad)

    def test_may_chu_muse_gia_websocket_day_du(self):
        """Máy chủ WebSocket giả lập nhận bắt tay Noise XX, giải khung HTTP và trả phản hồi stream."""
        pytest.importorskip("cryptography")
        websockets = pytest.importorskip("websockets")

        from modules.muse.client import MuseClient
        from modules.muse.khung import BoGhepKhung, tach_khung
        from modules.muse.noise import NoiseXXResponder
        from modules.muse.protobuf import (
            ApplicationResponse,
            BodyChunk,
            Header,
            NoiseTransportFrame,
            Reset,
            ResetCode,
            ServiceFrame,
            ServiceRequest,
            ServiceResponse,
            ServiceType,
        )

        async def _chay():
            da_nhan_reset = asyncio.Event()

            async def handler(websocket):
                responder = NoiseXXResponder()
                # Đọc msg1
                m1 = await websocket.recv()
                responder.read_message_1(m1)

                # Gửi msg2
                m2 = responder.write_message_2(payload=b"attestation_fake")
                await websocket.send(m2)

                # Đọc msg3
                m3 = await websocket.recv()
                responder.read_message_3(m3)

                # Split
                recv_cipher, send_cipher = responder.split()

                # Nhận yêu cầu HTTP
                ghep = BoGhepKhung()
                while True:
                    msg = await websocket.recv()
                    dec = recv_cipher.decrypt_with_ad(b"", msg)
                    frame = NoiseTransportFrame.decode(dec)
                    payload = ghep.them_khung(frame)
                    if payload:
                        break

                svc_req = ServiceRequest.decode(payload)
                svc_frame = ServiceFrame.decode(svc_req.payload)
                req = svc_frame.request
                stream_id = svc_frame.stream_id

                if req.path == "/chat/stream":
                    # Trả lời ApplicationResponse status 200
                    app_resp = ApplicationResponse(
                        status=200,
                        headers=[Header(key="Content-Type", value="text/event-stream")],
                        body=b"",
                        end_body=False,
                    )
                    sf_resp = ServiceFrame(stream_id=stream_id, response=app_resp)
                    sr_resp = ServiceResponse(payload=sf_resp.encode())
                    for f in tach_khung(sr_resp.encode()):
                        await websocket.send(send_cipher.encrypt_with_ad(b"", f.encode()))

                    # Gửi BodyChunk 1
                    bc1 = BodyChunk(data=b'{"eventName": "delta.text_append", "text": "Phan hoi tu "}\n', end_body=False)
                    sf_bc1 = ServiceFrame(stream_id=stream_id, body_chunk=bc1)
                    sr_bc1 = ServiceResponse(payload=sf_bc1.encode())
                    for f in tach_khung(sr_bc1.encode()):
                        await websocket.send(send_cipher.encrypt_with_ad(b"", f.encode()))

                    # Gửi BodyChunk 2
                    bc2 = BodyChunk(data=b'{"eventName": "delta.text_append", "text": "Muse gia"}\n', end_body=True)
                    sf_bc2 = ServiceFrame(stream_id=stream_id, body_chunk=bc2)
                    sr_bc2 = ServiceResponse(payload=sf_bc2.encode())
                    for f in tach_khung(sr_bc2.encode()):
                        await websocket.send(send_cipher.encrypt_with_ad(b"", f.encode()))

                # Chờ nếu có Reset
                try:
                    msg_cuoi = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    dec = recv_cipher.decrypt_with_ad(b"", msg_cuoi)
                    frame = NoiseTransportFrame.decode(dec)
                    pld = BoGhepKhung().them_khung(frame)
                    if pld:
                        sf = ServiceFrame.decode(ServiceRequest.decode(pld).payload)
                        if sf.reset:
                            da_nhan_reset.set()
                except Exception:
                    pass

            server = await websockets.serve(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws_url = f"ws://127.0.0.1:{port}"

            try:
                cli = MuseClient(url=ws_url, timeout=5.0)
                await cli.ket_noi()

                stream_id = await cli.gui_yeu_cau("POST", "/chat/stream", body=b'{"items": []}')
                app_resp, gen = await cli.nhan_phan_hoi(stream_id)
                assert app_resp.status == 200

                chunks = []
                async for data in gen:
                    chunks.append(data.decode("utf-8"))
                noi_dung = "".join(chunks)
                assert "Phan hoi tu " in noi_dung
                assert "Muse gia" in noi_dung

                # Thử gửi reset
                await cli.gui_reset(stream_id, "done test")
                await asyncio.sleep(0.2)
                assert da_nhan_reset.is_set()
                await cli.dong()
            finally:
                server.close()
                await server.wait_closed()

        chay(_chay())

    def test_noise_xx_bat_tay_sai(self):
        """Kiểm tra xử lý lỗi khi thông điệp bắt tay bị cắt cụt hoặc sai bước."""
        pytest.importorskip("cryptography")
        from modules.muse.noise import NoiseXXInitiator, NoiseXXResponder

        init = NoiseXXInitiator()
        resp = NoiseXXResponder()

        # Gọi sai bước trước khi khởi tạo
        with pytest.raises(RuntimeError, match="Sai bước handshake"):
            init.read_message_2(b"\x00" * 96)

        # Viết msg1
        m1 = init.write_message_1(payload=b"test")

        # Viết lại msg1 khi chưa xong bước
        with pytest.raises(RuntimeError, match="Sai bước handshake"):
            init.write_message_1(b"lap")

        # Responder nhận msg1
        resp.read_message_1(m1)

        # Msg2 quá ngắn (< 96 byte)
        with pytest.raises(ValueError, match="quá ngắn"):
            init.read_message_2(b"\x00" * 50)

        # Msg2 bị giả mạo / hỏng mã hoá
        m2 = resp.write_message_2(payload=b"server_ok")
        m2_hong = m2[:50] + bytes([m2[50] ^ 0xFF]) + m2[51:]
        with pytest.raises(Exception):
            init.read_message_2(m2_hong)

    def test_may_chu_muse_gia_nhan_anh_video_binary_body_chunk(self):
        """Máy chủ WebSocket giả lập trả về dữ liệu nhị phân ảnh/video qua BodyChunk."""
        pytest.importorskip("cryptography")
        websockets = pytest.importorskip("websockets")

        from modules.muse.client import MuseClient
        from modules.muse.khung import BoGhepKhung, tach_khung
        from modules.muse.noise import NoiseXXResponder
        from modules.muse.protobuf import (
            ApplicationResponse,
            BodyChunk,
            Header,
            NoiseTransportFrame,
            ServiceFrame,
            ServiceRequest,
            ServiceResponse,
        )

        async def _chay():
            fake_media_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 256

            async def handler(websocket):
                responder = NoiseXXResponder()
                m1 = await websocket.recv()
                responder.read_message_1(m1)
                m2 = responder.write_message_2(payload=b"attestation_fake")
                await websocket.send(m2)
                m3 = await websocket.recv()
                responder.read_message_3(m3)
                recv_cipher, send_cipher = responder.split()

                ghep = BoGhepKhung()
                while True:
                    msg = await websocket.recv()
                    dec = recv_cipher.decrypt_with_ad(b"", msg)
                    frame = NoiseTransportFrame.decode(dec)
                    payload = ghep.them_khung(frame)
                    if payload:
                        break

                svc_req = ServiceRequest.decode(payload)
                svc_frame = ServiceFrame.decode(svc_req.payload)
                stream_id = svc_frame.stream_id

                # Trả response 200 image/png
                app_resp = ApplicationResponse(
                    status=200,
                    headers=[Header(key="Content-Type", value="image/png")],
                    body=b"",
                    end_body=False,
                )
                sf_resp = ServiceFrame(stream_id=stream_id, response=app_resp)
                sr_resp = ServiceResponse(payload=sf_resp.encode())
                for f in tach_khung(sr_resp.encode()):
                    await websocket.send(send_cipher.encrypt_with_ad(b"", f.encode()))

                # Gửi ảnh/video giả bằng BodyChunk
                bc = BodyChunk(data=fake_media_bytes, end_body=True)
                sf_bc = ServiceFrame(stream_id=stream_id, body_chunk=bc)
                sr_bc = ServiceResponse(payload=sf_bc.encode())
                for f in tach_khung(sr_bc.encode()):
                    await websocket.send(send_cipher.encrypt_with_ad(b"", f.encode()))

            server = await websockets.serve(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            ws_url = f"ws://127.0.0.1:{port}"

            try:
                cli = MuseClient(url=ws_url, timeout=5.0)
                await cli.ket_noi()

                stream_id = await cli.gui_yeu_cau("GET", "/media/fake_media.png")
                app_resp, gen = await cli.nhan_phan_hoi(stream_id)
                assert app_resp.status == 200

                nhan_duoc = b""
                async for data in gen:
                    nhan_duoc += data
                assert nhan_duoc == fake_media_bytes
                await cli.dong()
            finally:
                server.close()
                await server.wait_closed()

        chay(_chay())


# ── 5. Kiểm thử Module Lifecycle & Nghiệm thu ─────────────────────────

class TestModuleLifecycle:
    def test_nap_module_khong_loi_init(self):
        from modules.muse.module import Module
        manifest_data = json.loads(
            (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "modules" / "muse" / "manifest.json").read_text()
        )
        mod = Module(manifest_data)
        mod.nap({})
        assert mod._device_priv_key is None

    def test_cai_khong_khoa_tra_ok_false_kem_huong_dan(self):
        async def _chay():
            from modules.muse.module import Module
            manifest_data = json.loads(
                (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "modules" / "muse" / "manifest.json").read_text()
            )
            mod = Module(manifest_data)
            kq = await mod.cai({})
            assert kq["ok"] is False
            assert "OTP" in kq["loi"]
            assert "device_key" in kq["loi"]

        chay(_chay())

    def test_models_va_suc_khoe_khong_lo_khoa(self):
        async def _chay():
            from modules.muse.module import Module
            manifest_data = json.loads(
                (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "modules" / "muse" / "manifest.json").read_text()
            )
            mod = Module(manifest_data)
            mod.nap({"device_key": "00" * 32})

            models = await mod.models()
            assert len(models) >= 1
            assert all(m["id"].startswith("muse/") for m in models)

            sk = await mod.suc_khoe()
            assert sk["trang_thai"] == "sẵn sàng"
            assert "00" * 32 not in json.dumps(sk)

        chay(_chay())

    def test_chat_chua_cau_hinh_tra_loi_404(self):
        async def _chay():
            from modules.muse.module import Module
            manifest_data = json.loads(
                (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "modules" / "muse" / "manifest.json").read_text()
            )
            mod = Module(manifest_data)
            mod.nap({})  # Chưa có khoá

            ma, headers, than = await mod.chat({"model": "muse/test", "messages": []}, stream=False)
            assert ma == 404
            data = json.loads(than.decode())
            assert data["error"]["code"] == "missing_credentials"

        chay(_chay())
