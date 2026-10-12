"""Client giao thức WebSocket Noise XX cho Muse.

Quản lý kết nối, bắt tay Noise XX, phân mảnh/lắp ráp và đóng gói HTTP trên nền Noise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from modules.muse.khung import BoGhepKhung, tach_khung
from modules.muse.noise import CipherState, NoiseXXInitiator
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
)

log = logging.getLogger("web2api.module.muse.client")


class LoiGiaoThucMuse(Exception):
    """Lỗi giao thức kết nối Muse."""
    pass


class MuseClient:
    """Khách hàng kết nối WebSocket mã hoá Noise XX tới máy ảo Muse."""

    def __init__(
        self,
        url: str,
        device_priv_key: Optional[bytes] = None,
        delegation_token: str = "",
        timeout: float = 30.0,
    ):
        self._url = url
        self._device_priv_key = device_priv_key
        self._delegation_token = delegation_token
        self._timeout = timeout
        self._ws = None
        self._send_cipher: Optional[CipherState] = None
        self._recv_cipher: Optional[CipherState] = None
        self._ghep_khung = BoGhepKhung()
        self._next_stream_id = 1
        self._dong = False

    async def ket_noi(self) -> None:
        """Mở WebSocket và thực hiện bắt tay 3 bước Noise XX."""
        try:
            import websockets
        except ImportError as e:
            raise ImportError(
                "Gói 'websockets' chưa được cài đặt trong môi trường Python. "
                "Cần cài 'websockets' để kết nối tới Muse."
            ) from e

        try:
            self._ws = await websockets.connect(
                self._url,
                open_timeout=self._timeout,
                close_timeout=5.0,
                max_size=10 * 1024 * 1024,
            )
        except Exception as e:
            raise LoiGiaoThucMuse(f"Không thể mở kết nối WebSocket tới {self._url}: {e}") from e

        try:
            initiator = NoiseXXInitiator(self._device_priv_key)

            # Thông điệp 1: -> e
            payload1 = b"\x10\x01" if self._delegation_token else b""
            msg1 = initiator.write_message_1(payload1)
            await self._ws.send(msg1)

            # Thông điệp 2: <- e, ee, s, es
            msg2 = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
            if not isinstance(msg2, (bytes, bytearray)):
                raise LoiGiaoThucMuse("Tin nhắn 2 từ máy chủ không phải nhị phân")
            initiator.read_message_2(bytes(msg2))

            # Thông điệp 3: -> s, se
            payload3 = self._delegation_token.encode("utf-8") if self._delegation_token else b""
            msg3 = initiator.write_message_3(payload3)
            await self._ws.send(msg3)

            # Split kênh
            self._send_cipher, self._recv_cipher = initiator.split()
            log.info("Bắt tay Noise XX thành công với Muse")
        except Exception as e:
            await self.dong()
            raise LoiGiaoThucMuse(f"Bắt tay Noise XX thất bại: {e}") from e

    async def gui_yeu_cau(
        self,
        verb: str,
        path: str,
        headers: Optional[dict[str, str]] = None,
        body: bytes = b"",
        service: ServiceType = ServiceType.SERVICE_DAEMON,
    ) -> int:
        """Gửi yêu cầu HTTP bọc trong ServiceFrame -> ServiceRequest qua NoiseTransportFrame."""
        if not self._ws or not self._send_cipher or self._dong:
            raise LoiGiaoThucMuse("Kết nối chưa sẵn sàng hoặc đã đóng")

        stream_id = self._next_stream_id
        self._next_stream_id += 1

        hdrs = [Header(key=k, value=v) for k, v in (headers or {}).items()]
        app_req = ApplicationRequest(
            verb=verb,
            path=path,
            headers=hdrs,
            body=body,
            end_body=True,
        )
        svc_frame = ServiceFrame(
            stream_id=stream_id,
            request=app_req,
        )
        svc_req = ServiceRequest(
            service=service,
            payload=svc_frame.encode(),
        )

        frames = tach_khung(svc_req.encode())
        for f in frames:
            enc = self._send_cipher.encrypt_with_ad(b"", f.encode())
            await self._ws.send(enc)

        return stream_id

    async def gui_reset(self, stream_id: int, reason: str = "Client cancelled") -> None:
        """Gửi thông điệp Reset để huỷ luồng."""
        if not self._ws or not self._send_cipher or self._dong:
            return
        try:
            rst = Reset(code=ResetCode.CANCELLED, reason=reason)
            svc_frame = ServiceFrame(stream_id=stream_id, reset=rst)
            svc_req = ServiceRequest(
                service=ServiceType.SERVICE_DAEMON,
                payload=svc_frame.encode(),
            )
            for f in tach_khung(svc_req.encode()):
                enc = self._send_cipher.encrypt_with_ad(b"", f.encode())
                await self._ws.send(enc)
        except Exception as e:
            log.warning("Lỗi gửi Reset stream %d: %s", stream_id, e)

    async def nhan_phan_hoi(self, stream_id: int) -> tuple[ApplicationResponse, AsyncIterator[bytes]]:
        """Nhận phản hồi ban đầu và generator sinh các BodyChunk tiếp theo."""
        app_resp = None

        # Đọc cho đến khi nhận được ApplicationResponse
        while app_resp is None:
            sf = await self._doc_service_frame()
            if sf.stream_id != stream_id:
                continue
            if sf.reset is not None:
                raise LoiGiaoThucMuse(f"Luồng {stream_id} bị huỷ bởi máy chủ: {sf.reset.reason}")
            if sf.response is not None:
                app_resp = sf.response
                break

        async def _generator() -> AsyncIterator[bytes]:
            if app_resp.end_body:
                return
            try:
                while True:
                    sf = await self._doc_service_frame()
                    if sf.stream_id != stream_id:
                        continue
                    if sf.reset is not None:
                        raise LoiGiaoThucMuse(f"Luồng {stream_id} bị máy chủ ngắt: {sf.reset.reason}")
                    if sf.body_chunk is not None:
                        yield sf.body_chunk.data
                        if sf.body_chunk.end_body:
                            break
            except asyncio.CancelledError:
                await self.gui_reset(stream_id, "Stream cancelled by reader")
                raise

        return app_resp, _generator()

    async def _doc_service_frame(self) -> ServiceFrame:
        """Đọc một tin nhắn nhị phân từ ws, giải mã và lắp ráp thành ServiceFrame."""
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
            if not isinstance(raw, (bytes, bytearray)):
                continue
            decrypted = self._recv_cipher.decrypt_with_ad(b"", bytes(raw))
            frame = NoiseTransportFrame.decode(decrypted)
            assembled = self._ghep_khung.them_khung(frame)
            if assembled is not None:
                svc_resp = ServiceResponse.decode(assembled)
                if not svc_resp.payload:
                    raise LoiGiaoThucMuse("Nhận được ServiceResponse rỗng")
                return ServiceFrame.decode(svc_resp.payload)

    async def dong(self) -> None:
        """Đóng kết nối WebSocket và dọn dẹp tài nguyên."""
        self._dong = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._send_cipher = None
        self._recv_cipher = None
