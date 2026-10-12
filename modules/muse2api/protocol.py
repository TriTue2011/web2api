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
import queue as queue_mod
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
    r = rq.post("https://muse.ai/api/auth/check", headers=_hatch_headers(cookies),
                data=b"", impersonate="chrome", timeout=20)
    if r.status_code != 200:
        raise AuthError(f"auth/check -> {r.status_code} (cookies expired? re-export)")
    try:
        return r.json()["access_token"]
    except (ValueError, KeyError) as e:
        raise AuthError(f"auth/check returned unexpected body: {e}") from e


def fetch_session_info(cookies: str) -> dict:
    """Discover the user's assigned personal VM (id + gateway URL)."""
    r = rq.get("https://muse.ai/api/session", headers=_hatch_headers(cookies),
               impersonate="chrome", timeout=20)
    if r.status_code != 200:
        raise AuthError(f"api/session -> {r.status_code} (cookies expired? re-export)")
    try:
        info = r.json()
    except ValueError:
        raise AuthError("api/session returned non-JSON")
    if "vm_id" not in info:
        raise GatewayError(-1, f"VM unavailable ({info!r}); retry shortly")
    return info


def fetch_hatch_token(cookies: str, access_token: str, vm_id: str) -> str:
    r = rq.post(
        "https://muse.ai/api/hatch/token",
        headers=_hatch_headers(cookies, access_token),
        json={"vmAddress": f"wss://{vm_id}.metaaivm.com/", "vmName": vm_id},
        impersonate="chrome", timeout=20,
    )
    if r.status_code != 200:
        raise AuthError(f"hatch/token -> {r.status_code}")
    try:
        return r.json()["token"]
    except (ValueError, KeyError) as e:
        raise AuthError(f"hatch/token returned unexpected body: {e}") from e


class Gateway:
    """One authenticated connection to the personal VM gateway.

    Blocking (sync) by design — run it in a worker thread from async code.
    Not thread-safe for concurrent frame consumers: hold the account lock
    for the whole request.
    """

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
        assert noise.handshake_finished
        self.noise = noise
        self.stream = 1
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()

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
        sid = self._open(method, path_params, body, query)
        status, chunks, deadline = None, [], time.time() + timeout
        while time.time() < deadline:
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
        if status is None:
            raise TimeoutError(f"no response for {method}")
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
