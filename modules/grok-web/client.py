"""Khách hàng giao thức WebSocket cho Grok web (grok.com).

Cách giao tiếp (học từ kho tham khảo công khai, triển khai lại độc lập):

- Mở WebSocket tới ``wss://grok.com/ws/mgw/?uid=<uid>``, xác thực bằng cookie
  (bắt buộc có cookie ``sso``), tiêu đề ``Origin: https://grok.com``.
- ``uid`` lấy từ cookie ``x-userid``; thiếu thì hỏi
  ``https://grok.com/api/auth/session`` (qua transport ghim IP của lõi).
- Luồng một lượt chat:
  1. Gửi sự kiện ``session.create`` (model = chế độ, phiên tạm — không lưu lịch sử).
  2. Đợi ``session.created`` (lấy session_id) và ``conversation.attached``.
  3. Gửi ``conversation.item.create`` (tin nhắn người dùng, gói trong input_chunks).
  4. Gửi ``response.create`` rồi đọc luồng sự kiện trả lời:
     - ``response.chunk`` → ``chunk.text.text`` (bỏ kênh ANALYSIS/REASONING),
     - ``response.output_text.delta`` → ``delta``,
     - ``response.grok.output`` có ``stream_error`` → hết lượt/lỗi luồng,
     - ``response.done`` → ``status`` phải là ``"completed"``.
- Phân loại lỗi BẰNG MÃ TRẠNG THÁI/CẤU TRÚC SỰ KIỆN, không dò chữ:
  bắt tay 401/403 hoặc sự kiện ``error`` có mã thuộc nhóm phiên → lỗi phiên
  (đổi tài khoản); ``stream_error`` có kind thuộc nhóm hạn mức → hết lượt
  (đổi tài khoản); ``response.done`` với status khác ``completed`` → lỗi.
- Xong nhưng không có chữ nào cũng coi là lỗi để lớp trên chuyển tài khoản.

Cookie KHÔNG BAO GIỜ vào log: chỉ ghi chỉ số tài khoản và loại lỗi.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import uuid

import httpx

from app.chuyen_tiep import TransportGhimIp
from app.tu_xa import _dia_chi_bi_chan, cho_phep_loopback_cho_test

log = logging.getLogger("web2api.module.grok_web.client")

UA_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) "
    "Gecko/20100101 Firefox/144.0"
)

WS_DUONG_MAC_DINH = "wss://grok.com/ws/mgw/"
GOC_HTTPS_MAC_DINH = "https://grok.com"

CHEO_IM_GIUA_TIN = 45.0   # im quá lâu giữa hai sự kiện thì coi là treo
TONG_TOI_DA = 300.0        # trần một lượt chat

#: Mã lỗi cấu trúc ở sự kiện ``error``/``stream_error`` được coi là lỗi phiên.
MA_LOI_PHIEN = {
    "unauthorized", "forbidden", "auth_error", "invalid_session",
    "session_expired", "missing_sso", "invalid_user", "auth_required",
}
#: Kind của ``stream_error`` (và mã ``error``) được coi là hết lượt.
MA_LOI_HAN_MUC = {
    "usage_limit", "usage_limit_reached", "rate_limit", "rate_limit_exceeded",
    "quota_exceeded", "too_many_requests",
}
#: Kênh chunk không phải văn bản trả lời.
KENH_BO_QUA = {"ANALYSIS", "REASONING"}


class LoiGrokWeb(Exception):
    """Lỗi chung của giao thức Grok web."""


class LoiPhienGrokWeb(LoiGrokWeb):
    """Lỗi phiên/xác thực — lớp trên nên đổi sang tài khoản khác."""


class LoiHanMucGrokWeb(LoiGrokWeb):
    """Hết lượt dùng của tài khoản/chế độ — lớp trên nên đổi tài khoản."""


class LoiGrokWebRong(LoiGrokWeb):
    """Phiên xong nhưng không có chữ nào — lớp trên nên đổi tài khoản."""


class LoiMayChuGrokWeb(LoiGrokWeb):
    """grok.com kết thúc phiên với trạng thái lỗi — lớp trên nên đổi tài khoản."""


class LoiThuThachTruyCap(LoiGrokWeb):
    """Máy chủ trả thử thách truy cập (Cloudflare Turnstile).

    KHÔNG phải lỗi tài khoản: module gặp thì báo «cần người» ngay, không thử
    lại, không tự giải thử thách dưới mọi hình thức."""


def _la_thu_thach_truy_cap(dap) -> bool:
    """Nhận ra thử thách bằng CẤU TRÚC, không dò chữ: mã 403/503 kèm tiêu đề
    ``cf-mitigated: challenge``."""
    if dap.status_code not in (403, 503):
        return False
    return (dap.headers.get("cf-mitigated") or "").strip().lower() == "challenge"


def _kiem_ip_phien(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        if isinstance(ip, ipaddress.IPv6Address):
            return "IPv6 loopback"
        return None if cho_phep_loopback_cho_test() else "loopback (chính container)"
    if ip.is_private:
        return "mạng riêng tư (private IP)"
    return _dia_chi_bi_chan(ip)


async def lay_uid_tu_phien(cookies: dict[str, str], goc_https: str) -> str:
    """Lấy uid từ cookie ``x-userid``; thiếu thì hỏi ``/api/auth/session``.

    Yêu cầu HTTP dự phòng đi qua TransportGhimIp (phân giải lại ngay trước
    khi nối, chặn dải riêng/loopback trừ khi test cho phép).
    """
    uid = (cookies.get("x-userid") or "").strip()
    if uid:
        return uid
    url = goc_https.rstrip("/") + "/api/auth/session"
    jar = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        async with httpx.AsyncClient(
            transport=TransportGhimIp(_kiem_ip_phien), timeout=15.0
        ) as kh:
            dap = await kh.get(
                url,
                headers={
                    "Cookie": jar,
                    "User-Agent": UA_FIREFOX,
                    "Referer": "https://grok.com/",
                },
            )
    except Exception as e:
        raise LoiPhienGrokWeb(
            "không lấy được uid (thiếu cookie x-userid và gọi phiên thất bại)"
        ) from e
    if _la_thu_thach_truy_cap(dap):
        raise LoiThuThachTruyCap("grok.com trả thử thách truy cập (Cloudflare)")
    if dap.status_code in (401, 403):
        raise LoiPhienGrokWeb("cookie bị grok.com từ chối khi lấy uid")
    try:
        du_lieu = dap.json()
        uid = str(du_lieu.get("session", {}).get("userId") or "")
    except Exception as e:
        raise LoiGrokWeb("phản hồi phiên không phải JSON") from e
    if not uid:
        raise LoiPhienGrokWeb("phiên không chứa userId (cookie có thể hết hạn)")
    return uid


class KhachGrokWeb:
    """Một phiên chat Grok web trên đúng MỘT tài khoản (cookie)."""

    def __init__(
        self,
        ws_url: str,
        cookies: dict[str, str],
        uid: str,
        che_do_im_giua_tin: float = CHEO_IM_GIUA_TIN,
    ) -> None:
        self._ws_url = ws_url
        self._cookies = cookies
        self._uid = uid
        self._cheo_im = che_do_im_giua_tin

    def _tieu_de(self) -> dict[str, str]:
        jar = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        return {
            "Cookie": jar,
            "Origin": "https://grok.com",
            "User-Agent": UA_FIREFOX,
        }

    @staticmethod
    def _su_kien(loai: str, session_id: str | None = None, **them) -> str:
        goi: dict = {
            "event": {
                "type": loai,
                "event_id": f"evt_{uuid.uuid4().hex}",
                **them,
            }
        }
        if session_id:
            goi["session_id"] = session_id
        return json.dumps(goi, ensure_ascii=False)

    def _sk_tao_phien(self, che_do: str) -> str:
        return json.dumps(
            {
                "event": {
                    "type": "session.create",
                    "event_id": f"evt_init_{uuid.uuid4().hex}",
                    "session": {
                        "model": che_do,
                        "x_grok": {
                            "capabilities": {
                                "x_grok_web_search": True,
                                "x_grok_code_execution": True,
                            },
                            "is_temporary": True,
                            "source": "web",
                            "x_grok_agent_mode": False,
                        },
                    },
                }
            },
            ensure_ascii=False,
        )

    def _sk_tin_nhan(self, session_id: str, loi_nhan: str) -> str:
        return json.dumps(
            {
                "session_id": session_id,
                "event": {
                    "type": "conversation.item.create",
                    "event_id": f"evt_item_{uuid.uuid4().hex}",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "x_grok": {
                            "client_message_id": f"cmi_{uuid.uuid4().hex}",
                            "input_chunks": [{"text": {"text": loi_nhan}}],
                        },
                    },
                },
            },
            ensure_ascii=False,
        )

    async def _nhan_sk(self, ws) -> dict:
        try:
            tho = await asyncio.wait_for(ws.recv(), timeout=self._cheo_im)
        except asyncio.TimeoutError as e:
            raise LoiGrokWeb("grok.com im quá lâu giữa luồng") from e
        try:
            return json.loads(tho)
        except Exception as e:
            raise LoiGrokWeb("grok.com trả sự kiện không phải JSON") from e

    async def _doi_su_kien(self, ws, cac_loai: set[str]) -> dict:
        """Đọc tới khi gặp một trong các loại sự kiện mong đợi; lỗi thì ném."""
        while True:
            goi = await self._nhan_sk(ws)
            su_kien = goi.get("event") or {}
            loai = su_kien.get("type", "")
            self._kiem_loi_su_kien(loai, su_kien)
            if loai in cac_loai:
                return su_kien

    def _kiem_loi_su_kien(self, loai: str, su_kien: dict) -> None:
        if loai == "error":
            loi = su_kien.get("error") or {}
            ma = str(loi.get("code") or loi.get("type") or "").lower()
            if ma in MA_LOI_PHIEN:
                raise LoiPhienGrokWeb(f"grok.com báo lỗi phiên ({ma or 'error'})")
            if ma in MA_LOI_HAN_MUC:
                raise LoiHanMucGrokWeb(f"grok.com báo hết lượt ({ma or 'error'})")
            raise LoiGrokWeb(f"grok.com báo lỗi ({ma or 'error'})")

    async def _mo_ws(self):
        try:
            import websockets
        except ImportError as e:
            raise ImportError(
                "Gói 'websockets' chưa được cài đặt. Cần cài 'websockets' "
                "để kết nối tới grok.com."
            ) from e
        url = f"{self._ws_url}?uid={self._uid}"
        try:
            return await websockets.connect(
                url,
                additional_headers=self._tieu_de(),
                open_timeout=20.0,
                close_timeout=5.0,
                max_size=10 * 1024 * 1024,
            )
        except Exception as e:
            ma = getattr(getattr(e, "response", None), "status_code", None)
            if ma in (401, 403):
                raise LoiPhienGrokWeb(
                    f"bắt tay WebSocket bị từ chối (HTTP {ma})"
                ) from e
            raise LoiGrokWeb(f"không mở được WebSocket tới grok.com: {e}") from e

    async def chat(self, che_do: str, loi_nhan: str):
        """Một lượt chat: yield từng mảnh văn bản cho tới khi xong."""
        ws = await self._mo_ws()
        try:
            await ws.send(self._sk_tao_phien(che_do))
            sk_da_tao = await self._doi_su_kien(ws, {"session.created"})
            session_id = str((sk_da_tao.get("session") or {}).get("id") or "")
            if not session_id:
                raise LoiGrokWeb("session.created không có session id")
            await self._doi_su_kien(ws, {"conversation.attached"})
            await ws.send(self._sk_tin_nhan(session_id, loi_nhan))
            await ws.send(self._su_kien("response.create", session_id=session_id))

            co_chu = False
            while True:
                su_kien = await self._doi_su_kien(
                    ws,
                    {
                        "response.chunk",
                        "response.output_text.delta",
                        "response.grok.output",
                        "response.done",
                    },
                )
                loai = su_kien.get("type", "")
                if loai == "response.chunk":
                    chunk = su_kien.get("chunk") or {}
                    van_ban = chunk.get("text") or {}
                    kenh = chunk.get("channel") or van_ban.get("channel") or ""
                    if str(kenh).upper() in KENH_BO_QUA:
                        continue
                    manh = van_ban.get("text") or ""
                    if manh:
                        co_chu = True
                        yield manh
                elif loai == "response.output_text.delta":
                    manh = su_kien.get("delta") or ""
                    if manh:
                        co_chu = True
                        yield manh
                elif loai == "response.grok.output":
                    dau_ra = su_kien.get("output") or {}
                    loi_luong = dau_ra.get("stream_error")
                    if loi_luong:
                        kind = str(loi_luong.get("kind") or "").lower()
                        if kind in MA_LOI_HAN_MUC:
                            raise LoiHanMucGrokWeb(
                                f"grok.com báo hết lượt (kind={kind or 'stream_error'})"
                            )
                        raise LoiGrokWeb(
                            f"grok.com báo lỗi luồng (kind={kind or 'stream_error'})"
                        )
                elif loai == "response.done":
                    trang_thai = su_kien.get("status") or ""
                    if trang_thai != "completed":
                        raise LoiMayChuGrokWeb(
                            f"grok.com kết thúc với trạng thái {trang_thai or 'không rõ'}"
                        )
                    if not co_chu:
                        raise LoiGrokWebRong("grok.com xong nhưng không trả chữ nào")
                    return
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def chay(self, che_do: str, loi_nhan: str):
        """Bọc chat() trong trần thời gian toàn lượt."""
        try:
            async with asyncio.timeout(TONG_TOI_DA):
                async for manh in self.chat(che_do, loi_nhan):
                    yield manh
        except TimeoutError as e:
            raise LoiGrokWeb("quá trần thời gian một lượt chat") from e
