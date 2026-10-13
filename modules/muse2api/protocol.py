"""Direct protocol client for the muse.ai personal gateway.

Adapted from nikships/muse-cli (MIT License, Copyright (c) 2026 Nik Ships),
file src/muse_cli/gateway.py. The transport, framing and route table are
unchanged; only the CLI-specific bits (arg parsing, cookie-file loading) were
replaced with a small programmatic API.

How it works (see muse-cli/docs/PROTOCOL.md):
  1. HTTPS bootstrap with the user's muse.ai cookies (Chrome TLS fingerprint):
       POST /api/auth/check  -> access_token
       GET  /api/session     -> vm_id
       POST /api/hatch/token -> hatch token
  2. wss://hatch.metaaivm.com/v1/noise?vm_id=..&auth_token=..
     Noise_XX_25519_AESGCM_SHA256 handshake (initiator, ephemeral static key,
     empty payloads).
  3. Protobuf envelopes: NoiseTransportFrame -> ServiceRequest/ServiceResponse
     -> ServiceFrame{stream_id, ...} carrying an HTTP-like
     ApplicationRequest{verb, path, headers, body, end_body}.
  4. Chat: POST /chat/stream (fire-and-forget) then poll GET /chat/history
     for new message.assistant events.

Internal APIs are unversioned and may change without notice.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

from curl_cffi import requests as rq
from curl_cffi.requests import WebSocket
from noise.connection import NoiseConnection, Keypair
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

_HERE = Path(__file__).resolve().parent
_PROTO_DIR = Path(__file__).resolve().parent / "_proto"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
GATEWAY_HOST = "hatch.metaaivm.com"

_pool = descriptor_pool.DescriptorPool()
for _i in (0, 1):
    _fd = descriptor_pb2.FileDescriptorProto()
    _fd.ParseFromString((_PROTO_DIR / f"desc{_i}.bin").read_bytes())
    _pool.Add(_fd)


def _msg(name):
    return message_factory.GetMessageClass(_pool.FindMessageTypeByName(name))


NoiseTransportFrame = _msg("ingress_rev_proxy.NoiseTransportFrame")
ServiceRequest = _msg("hatch.noise.ServiceRequest")
ServiceResponse = _msg("hatch.noise.ServiceResponse")
ServiceFrame = _msg("hatch.noise.ServiceFrame")
ApplicationRequest = _msg("hatch.noise.ApplicationRequest")

with open(_PROTO_DIR / "routes.json", encoding="utf-8") as fh:
    ROUTES = {e["method"]: e for e in json.load(fh)}

SERVICE_IDS = {"daemon": 0, "sentinel": 1, "vault": 2, "authd": 3}


class AuthError(RuntimeError):
    """Cookies rejected / expired — the user must re-export them."""


class GatewayError(RuntimeError):
    def __init__(self, status, payload):
        super().__init__(f"gateway status={status} payload={payload!r}")
        self.status = status
        self.payload = payload


def _hatch_headers(cookies: str, access_token: str | None = None) -> dict:
    h = {
        "User-Agent": UA,
        "Referer": "https://muse.ai/",
        "Origin": "https://muse.ai",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": cookies,
    }
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def fetch_access_token(cookies: str) -> str:
    try:
        r = rq.post("https://muse.ai/api/auth/check", headers=_hatch_headers(cookies),
                    data=b"", impersonate="chrome", timeout=20)
    except Exception as e:
        raise GatewayError(-1, f"auth/check network error: {e}") from e
    if r.status_code in (401, 403):
        raise AuthError(f"auth/check -> {r.status_code} (cookies expired? re-export)")
    if r.status_code != 200:
        raise GatewayError(r.status_code, f"auth/check -> {r.status_code}")
    try:
        return r.json()["access_token"]
    except (ValueError, KeyError) as e:
        raise GatewayError(-1, f"auth/check returned unexpected body: {e}") from e


def fetch_session_info(cookies: str) -> dict:
    """Discover the user's assigned personal VM (id + gateway URL)."""
    try:
        r = rq.get("https://muse.ai/api/session", headers=_hatch_headers(cookies),
                   impersonate="chrome", timeout=20)
    except Exception as e:
        raise GatewayError(-1, f"api/session network error: {e}") from e
    if r.status_code in (401, 403):
        raise AuthError(f"api/session -> {r.status_code} (cookies expired? re-export)")
    if r.status_code != 200:
        raise GatewayError(r.status_code, f"api/session -> {r.status_code}")
    try:
        info = r.json()
    except ValueError:
        raise GatewayError(-1, "api/session returned non-JSON")
    if not isinstance(info, dict) or "vm_id" not in info:
        # Chỉ nêu TÊN khoá: giá trị phiên có thể chứa thông tin tài khoản.
        khoa = sorted(info) if isinstance(info, dict) else type(info).__name__
        raise GatewayError(-1, f"VM unavailable (session keys: {khoa}); retry shortly")
    return info


def fetch_hatch_token(cookies: str, access_token: str, vm_id: str) -> str:
    try:
        r = rq.post(
            "https://muse.ai/api/hatch/token",
            headers=_hatch_headers(cookies, access_token),
            json={"vmAddress": f"wss://{vm_id}.metaaivm.com/", "vmName": vm_id},
            impersonate="chrome", timeout=20,
        )
    except Exception as e:
        raise GatewayError(-1, f"hatch/token network error: {e}") from e
    if r.status_code in (401, 403):
        raise AuthError(f"hatch/token -> {r.status_code}")
    if r.status_code != 200:
        raise GatewayError(r.status_code, f"hatch/token -> {r.status_code}")
    try:
        return r.json()["token"]
    except (ValueError, KeyError) as e:
        raise GatewayError(-1, f"hatch/token returned unexpected body: {e}") from e


class Gateway:
    """One authenticated connection to the personal VM gateway.

    Blocking (sync) by design — run it in a worker thread from async code.
    Not thread-safe for concurrent frame consumers: hold the account lock
    for the whole request.
    """

    HAN_BAT_TAY = 20.0  # giây, tổng cho connect + bắt tay Noise

    def __init__(self, cookies: str, vm_id: str | None = None,
                 access_token: str | None = None, hatch_token: str | None = None):
        if vm_id is None:
            vm_id = fetch_session_info(cookies)["vm_id"]
        self.vm_id = vm_id
        access_token = access_token or fetch_access_token(cookies)
        hatch_token = hatch_token or fetch_hatch_token(cookies, access_token, vm_id)
        url = (f"wss://{GATEWAY_HOST}/v1/noise?vm_id={vm_id}"
               f"&auth_token={urllib.parse.quote(hatch_token, safe='')}")
        self.ws = WebSocket()
        # Hạn chót cho bắt tay (connect + Noise XX 3 bước), cùng mẫu cờ như request().
        het_han = threading.Event()
        hs_timer = threading.Timer(self.HAN_BAT_TAY, self._het_han, (het_han,))
        hs_timer.daemon = True
        hs_timer.start()
        try:
            self.ws.connect(url, impersonate="chrome", timeout=20, verify=True)
            noise = NoiseConnection.from_name(b"Noise_XX_25519_AESGCM_SHA256")
            noise.set_as_initiator()
            # Ephemeral static key: no persistent device key needed.
            noise.set_keypair_from_private_bytes(Keypair.STATIC, os.urandom(32))
            noise.start_handshake()
            self.ws.send_bytes(bytes(noise.write_message(b"")))
            m2, _ = self.ws.recv()
            noise.read_message(bytes(m2))
            self.ws.send_bytes(bytes(noise.write_message(b"")))
            if not noise.handshake_finished:
                raise GatewayError(-1, "Noise handshake chưa hoàn tất")
        except BaseException as e:
            hs_timer.cancel()
            self.close()  # không để rò kết nối khi bắt tay hỏng
            if het_han.is_set():
                raise TimeoutError(
                    f"Noise handshake quá hạn {self.HAN_BAT_TAY:g}s") from e
            if isinstance(e, Exception) and not isinstance(e, GatewayError):
                raise GatewayError(-1, f"handshake failed: {type(e).__name__}: {e}") from e
            raise
        hs_timer.cancel()
        self.noise = noise
        self.stream = 1
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

    def _het_han(self, co: threading.Event):
        """Gọi từ Timer thread khi quá hạn: bật cờ rồi đóng ws để ép recv() lỗi."""
        co.set()
        self.close()

    # -- low-level framing -------------------------------------------------
    def _send_envelope(self, service_id: int, frame_bytes: bytes):
        outer = ServiceRequest(service=service_id, payload=frame_bytes)
        chunk_id = struct.unpack("<q", os.urandom(8))[0]
        fr = NoiseTransportFrame(chunk_id=chunk_id, chunk_index=0, total_chunks=1,
                                 payload=outer.SerializeToString())
        with self._send_lock:
            self.ws.send_bytes(bytes(self.noise.encrypt(fr.SerializeToString())))

    def _read_frame(self):
        with self._recv_lock:
            data, _flags = self.ws.recv()
            pt = bytes(self.noise.decrypt(bytes(data)))
        ntf = NoiseTransportFrame()
        ntf.ParseFromString(pt)
        sr = ServiceResponse()
        sr.ParseFromString(ntf.payload)
        sf = ServiceFrame()
        sf.ParseFromString(sr.payload)
        return sf

    def _open(self, method: str, path_params=None, body=None, query=None) -> int:
        route = ROUTES[method]
        path = route["path"]
        for k, v in (path_params or {}).items():
            path = path.replace("{" + k + "}", urllib.parse.quote(str(v), safe=""))
        if query:
            qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
            if qs:
                path = path + ("&" if "?" in path else "?") + qs
        raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = ApplicationRequest(verb=route["http"], path=path, body=raw, end_body=True)
        h = req.headers.add()
        h.key = "x-request-id"
        h.value = str(uuid.uuid4())
        if raw:
            h2 = req.headers.add()
            h2.key = "content-type"
            h2.value = "application/json"
        frame = ServiceFrame(stream_id=self.stream)
        frame.request.CopyFrom(req)
        sid = self.stream
        self.stream += 1
        self._send_envelope(SERVICE_IDS.get(route.get("service", "daemon"), 0),
                            frame.SerializeToString())
        return sid

    # -- unary request/response --------------------------------------------
    def request(self, method: str, path_params=None, body=None, query=None,
                timeout: float = 30) -> bytes:
        route = ROUTES[method]
        if route["http"] == "GET" and body is not None and query is None:
            query, body = body, None
        status, chunks = None, []
        # Timer thật: recv() có thể chặn vĩnh viễn khi máy chủ im lặng,
        # nên dùng threading.Timer đóng ws sau hạn chót — recv() sẽ lỗi.
        # Cờ riêng mỗi lượt, bật TRƯỚC khi đóng ws: chỉ khi cờ bật mới là
        # hết hạn thật; ws/curl/Noise hỏng vì lý do khác là lỗi kết nối.
        het_han = threading.Event()
        timer = threading.Timer(timeout, self._het_han, (het_han,))
        timer.daemon = True
        timer.start()
        try:
            sid = self._open(method, path_params, body, query)
            while True:
                sf = self._read_frame()
                if sf.stream_id != sid:
                    continue
                kind = sf.WhichOneof("kind")
                if kind == "response":
                    status = sf.response.status
                    if sf.response.body:
                        chunks.append(bytes(sf.response.body))
                    if sf.response.end_body:
                        break
                elif kind == "body_chunk":
                    chunks.append(bytes(sf.body_chunk.data))
                    if sf.body_chunk.end_body:
                        break
                elif kind == "reset":
                    raise GatewayError(-1, f"stream reset {sf.reset.code}: {sf.reset.reason}")
        except GatewayError:
            raise
        except Exception as e:
            if het_han.is_set():
                raise TimeoutError(f"no response for {method} within {timeout:g}s") from e
            raise GatewayError(-1, f"connection error on {method}: "
                                   f"{type(e).__name__}: {e}") from e
        finally:
            timer.cancel()
        if status is None:
            raise GatewayError(-1, f"{method}: stream ended without a response status")
        payload = b"".join(chunks)
        if status < 200 or status >= 300:
            raise GatewayError(status, payload[:500].decode("utf-8", "replace"))
        return payload

    def call_json(self, method: str, path_params=None, body=None, query=None,
                  timeout: float = 30):
        raw = self.request(method, path_params, body, query, timeout)
        try:
            d = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise GatewayError(-1, f"non-JSON response: {raw[:200]!r}")
        if isinstance(d, dict) and d.get("ok") is False:
            raise GatewayError(-1, f"api error: {d.get('error')}")
        return d.get("result", d) if isinstance(d, dict) else d

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
