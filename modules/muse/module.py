"""Module Muse (Meta) — Cổng giao thức Noise XX WebSocket tới máy ảo Meta AI VM.

Giao thức: Noise_XX_25519_AESGCM_SHA256 bọc khung Protobuf HTTP
Yêu cầu:   Khoá thiết bị (Recovery Key 32 bytes) và Delegation Token
Khả năng:  chat (stream)
Trạng thái: Thử nghiệm — chưa kiểm với dịch vụ thật
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any, AsyncIterator, Optional

from .client import MuseClient
from .converter import (
    chuyen_dong_muse_sang_sse_openai,
    chuyen_yeu_cau_chat_openai,
    gom_dong_muse_thanh_json_openai,
    tach_model_muse,
)
from .protobuf import ServiceType

log = logging.getLogger("web2api.module.muse")

_MAC_DINH_MODELS = [
    "meta-llama/Llama-3.3-70B-Instruct",
    "meta-llama/Llama-4-Scout-17B-16E",
    "meta-llama/Llama-4-Maverick-17B-128E",
]


def _giai_ma_khoa_thiet_bi(khoa_str: str) -> bytes:
    """Chuyển đổi khoá thiết bị từ chuỗi hex hoặc base64/base32 thành bytes."""
    s = khoa_str.strip()
    # Thử hex
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    # Thử base64
    try:
        b = base64.b64decode(s + "=" * (-len(s) % 4))
        if len(b) == 32:
            return b
    except Exception:
        pass
    # Thử utf-8 nếu đủ 32 bytes
    b = s.encode("utf-8")
    if len(b) == 32:
        return b
    raise ValueError("Khoá thiết bị phải có kích thước đúng 32 bytes (chuỗi hex 64 ký tự hoặc base64)")


class Module:
    def __init__(self, manifest: dict):
        self._manifest = manifest
        self._tien_to = manifest.get("tien_to", "muse")
        self._device_priv_key: Optional[bytes] = None
        self._delegation_token: str = ""
        self._ws_url: str = ""
        self._client: Optional[MuseClient] = None

    # ── Vòng đời ────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình đã được kiểm tra (không gọi mạng)."""
        khoa_raw = cau_hinh.get("device_key", "")
        if khoa_raw:
            try:
                self._device_priv_key = _giai_ma_khoa_thiet_bi(khoa_raw)
            except Exception as e:
                log.warning("Không giải mã được device_key: %s", e)
                self._device_priv_key = None
        else:
            self._device_priv_key = None

        self._delegation_token = cau_hinh.get("delegation_token", "")
        self._ws_url = cau_hinh.get("ws_url", "")

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm tra cấu hình cài đặt.

        Nếu chưa có device_key hoặc luồng đăng ký OTP chưa thực hiện, trả về ok=False
        kèm hướng dẫn rõ ràng. Nếu có đầy đủ khoá và ws_url, kiểm tra kết nối thử.
        """
        khoa_raw = cau_hinh.get("device_key", "").strip()
        if not khoa_raw:
            return {
                "ok": False,
                "loi": (
                    "Muse yêu cầu đăng ký thiết bị (OTP từ Meta) để cấp khoá thiết bị và delegation token. "
                    "Hiện tại chưa hỗ trợ đăng ký tự động qua web2api — vui lòng nhập 'device_key' thủ công."
                ),
                "ket_qua_tung_khoa": [
                    {
                        "khoa": "device_key",
                        "ok": False,
                        "ly_do": "Chưa có khoá thiết bị (cần OTP đăng ký từ Meta)",
                    }
                ],
            }

        try:
            priv_bytes = _giai_ma_khoa_thiet_bi(khoa_raw)
        except Exception as e:
            return {
                "ok": False,
                "loi": f"Định dạng device_key không hợp lệ: {e}",
                "ket_qua_tung_khoa": [
                    {
                        "khoa": "device_key",
                        "ok": False,
                        "ly_do": str(e),
                    }
                ],
            }

        ws_url = cau_hinh.get("ws_url", "").strip()
        delegation_token = cau_hinh.get("delegation_token", "").strip()

        # Nếu có ws_url (ví dụ trong môi trường test máy chủ giả), kiểm tra bắt tay thử
        if ws_url:
            cli = MuseClient(
                url=ws_url,
                device_priv_key=priv_bytes,
                delegation_token=delegation_token,
                timeout=5.0,
            )
            try:
                await cli.ket_noi()
                await cli.dong()
            except Exception as e:
                return {
                    "ok": False,
                    "loi": f"Không thể bắt tay Noise XX với máy chủ Muse: {e}",
                    "ket_qua_tung_khoa": [
                        {
                            "khoa": "device_key",
                            "ok": False,
                            "ly_do": f"Lỗi kết nối kiểm thử: {e}",
                        }
                    ],
                }

        return {
            "ok": True,
            "ket_qua_tung_khoa": [
                {
                    "khoa": "device_key",
                    "ok": True,
                    "ly_do": "Khoá thiết bị 32 bytes hợp lệ",
                }
            ],
            "cau_hinh_luu": {
                "device_key": khoa_raw,
                "delegation_token": delegation_token,
                "ws_url": ws_url,
            },
        }

    # ── Chat ────────────────────────────────────────────────────────────

    async def chat(self, body: dict, stream: bool):
        """Chuyển tiếp yêu cầu chat OpenAI tới dịch vụ daemon của Muse."""
        if not self._device_priv_key:
            err = {
                "error": {
                    "message": "Module muse chưa được cấu hình khoá thiết bị ('device_key')",
                    "type": "module_not_configured",
                    "code": "missing_credentials",
                }
            }
            return 404, {"content-type": "application/json"}, json.dumps(err).encode("utf-8")

        ws_url = self._ws_url or "wss://hatch.metaaivm.com/v1/noise"
        model = body.get("model", "")
        model_name = tach_model_muse(model)

        cli = MuseClient(
            url=ws_url,
            device_priv_key=self._device_priv_key,
            delegation_token=self._delegation_token,
        )

        try:
            await cli.ket_noi()
        except Exception as e:
            await cli.dong()
            err = {
                "error": {
                    "message": f"Lỗi kết nối tới Muse: {e}",
                    "type": "upstream_error",
                    "code": "muse_connection_failed",
                }
            }
            return 502, {"content-type": "application/json"}, json.dumps(err).encode("utf-8")

        try:
            req_data = chuyen_yeu_cau_chat_openai(body)
            body_bytes = json.dumps(req_data, ensure_ascii=False).encode("utf-8")
            stream_id = await cli.gui_yeu_cau(
                verb="POST",
                path="/chat/stream",
                headers={
                    "Content-Type": "application/json",
                    "x-app-id": "hatch-web",
                },
                body=body_bytes,
                service=ServiceType.SERVICE_DAEMON,
            )
            app_resp, luong_chunks = await cli.nhan_phan_hoi(stream_id)

            if app_resp.status != 200:
                await cli.dong()
                err_text = app_resp.body.decode("utf-8", errors="replace") if app_resp.body else f"HTTP {app_resp.status}"
                err = {
                    "error": {
                        "message": f"Máy chủ Muse trả về lỗi: {err_text}",
                        "type": "upstream_error",
                        "code": f"muse_http_{app_resp.status}",
                    }
                }
                return app_resp.status, {"content-type": "application/json"}, json.dumps(err).encode("utf-8")

            if stream:
                async def _stream_wrapper() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in chuyen_dong_muse_sang_sse_openai(luong_chunks, model):
                            yield chunk
                    finally:
                        await cli.dong()

                return 200, {"content-type": "text/event-stream"}, _stream_wrapper()
            else:
                try:
                    res = await gom_dong_muse_thanh_json_openai(luong_chunks, model)
                    return 200, {"content-type": "application/json"}, json.dumps(res, ensure_ascii=False).encode("utf-8")
                finally:
                    await cli.dong()

        except Exception as e:
            await cli.dong()
            err = {
                "error": {
                    "message": f"Lỗi trong quá trình trao đổi với Muse: {e}",
                    "type": "upstream_error",
                    "code": "muse_stream_error",
                }
            }
            return 502, {"content-type": "application/json"}, json.dumps(err).encode("utf-8")

    # ── Models ──────────────────────────────────────────────────────────

    async def models(self) -> list[dict]:
        """Liệt kê danh sách mô hình được Muse hỗ trợ."""
        return [
            {
                "id": f"{self._tien_to}/{m}",
                "object": "model",
                "owned_by": "muse",
            }
            for m in _MAC_DINH_MODELS
        ]

    # ── Sức khoẻ ────────────────────────────────────────────────────────

    async def suc_khoe(self) -> dict:
        """Kiểm tra tình trạng module — tuyệt đối không để lộ khoá bí mật."""
        if not self._device_priv_key:
            return {
                "ok": False,
                "trang_thai": "chưa cấu hình",
                "chi_tiet": "Thiếu khoá thiết bị (device_key)",
            }
        return {
            "ok": True,
            "trang_thai": "sẵn sàng",
            "ghi_chu": "Thử nghiệm — chưa kiểm với dịch vụ thật",
        }

    # ── Dừng ────────────────────────────────────────────────────────────

    async def dung(self) -> None:
        """Đóng các kết nối đang mở."""
        if self._client:
            await self._client.dong()
            self._client = None
