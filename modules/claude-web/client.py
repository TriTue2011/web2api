"""Máy khách HTTP cho API web nội bộ của claude.ai.

Luồng mỗi lượt chat: GET /api/organizations → chọn tổ chức có khả năng «chat»
→ POST /api/organizations/{org}/chat_conversations (tạo hội thoại mới mỗi lượt,
tránh lẫn lịch sử) → POST …/completion với Accept: text/event-stream.

Xác thực bằng header Cookie do người dùng dán (chuỗi «k1=v1; k2=v2» đầy đủ hoặc
giá trị sessionKey). Không tự đăng nhập, không tự động hoá trình duyệt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import httpx

from app.chuyen_tiep import TransportGhimIp
from app.tu_xa import _dia_chi_bi_chan

from . import converter as cv

LOG = logging.getLogger("web2api.module.claude-web")

BASE_MAC_DINH = "https://claude.ai"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


class LoiClaudeWeb(Exception):
    """Lỗi từ claude.ai, phân loại bằng mã HTTP (không dò chữ trong thân)."""

    def __init__(self, ma: int, thong_bao: str):
        super().__init__(f"claude.ai trả {ma}: {thong_bao}")
        self.ma = ma
        self.thong_bao = thong_bao


class MayKhachClaudeWeb:
    """Một phiên claude.ai gắn với đúng một bộ cookie."""

    def __init__(self, cookie: str, base_url: str = BASE_MAC_DINH):
        self._cookie = cookie
        self._goc = (base_url or BASE_MAC_DINH).rstrip("/")
        self._org_id = ""
        self._khach: httpx.AsyncClient | None = None

    # ── hạ tầng HTTP ────────────────────────────────────────────────

    def _tieu_de(self, them: dict | None = None) -> dict:
        td = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self._goc,
            "Referer": self._goc + "/chats",
            "anthropic-client-platform": "web_claude_ai",
            "Cookie": self._cookie,
        }
        if them:
            td.update(them)
        return td

    def _khach_http(self) -> httpx.AsyncClient:
        if self._khach is None:
            self._khach = httpx.AsyncClient(
                transport=TransportGhimIp(_dia_chi_bi_chan),
                timeout=httpx.Timeout(30.0, read=300.0),
            )
        return self._khach

    async def dong(self) -> None:
        khach, self._khach = self._khach, None
        if khach is not None:
            await khach.aclose()

    def _nem_loi(self, r: httpx.Response, buoc: str) -> LoiClaudeWeb:
        """Dựng lỗi đã che: chỉ giữ mã trạng thái, không mang thân phản hồi
        (thân có thể chứa nội dung nhạy cảm của tài khoản)."""
        return LoiClaudeWeb(r.status_code, f"{buoc} thất bại")

    # ── các bước của luồng ──────────────────────────────────────────

    async def lay_org_id(self) -> str:
        """GET /api/organizations → uuid của tổ chức có khả năng «chat»."""
        if self._org_id:
            return self._org_id
        try:
            r = await self._khach_http().get(f"{self._goc}/api/organizations",
                                             headers=self._tieu_de())
        except httpx.HTTPError as e:
            raise LoiClaudeWeb(502, f"không nối được claude.ai ({type(e).__name__})") from e
        if r.status_code != 200:
            raise self._nem_loi(r, "lấy danh sách tổ chức")
        try:
            ds = r.json()
        except ValueError as e:
            raise LoiClaudeWeb(502, "claude.ai trả JSON hỏng ở bước lấy tổ chức") from e
        org_id = ""
        if isinstance(ds, list) and ds:
            co_chat = [o for o in ds
                       if isinstance(o, dict) and "chat" in (o.get("capabilities") or [])]
            org_id = str((co_chat[0] if co_chat else ds[0]).get("uuid") or "")
        elif isinstance(ds, dict):
            org_id = str(ds.get("uuid") or "")
        if not org_id:
            raise LoiClaudeWeb(401, "không tìm thấy tổ chức (cookie hết hạn?)")
        self._org_id = org_id
        return org_id

    async def tao_hoi_thoai(self, org_id: str) -> str:
        conv = uuid.uuid4().hex
        try:
            r = await self._khach_http().post(
                f"{self._goc}/api/organizations/{org_id}/chat_conversations",
                headers=self._tieu_de(), json={"uuid": conv, "name": ""})
        except httpx.HTTPError as e:
            raise LoiClaudeWeb(502, f"không nối được claude.ai ({type(e).__name__})") from e
        if r.status_code not in (200, 201):
            raise self._nem_loi(r, "tạo hội thoại")
        try:
            return str(r.json().get("uuid") or conv)
        except ValueError:
            return conv

    async def mo_luong(self, org_id: str, conv_id: str, prompt: str,
                       model_noi_bo: str) -> httpx.Response:
        """POST …/completion, trả Response đang mở (đọc được status ngay, thân
        đọc dần). Người gọi phải đóng response khi xong."""
        url = (f"{self._goc}/api/organizations/{org_id}/chat_conversations/"
               f"{conv_id}/completion")
        than = cv.than_hoan_thanh(prompt, model_noi_bo)
        khach = self._khach_http()
        yeu_cau = khach.build_request(
            "POST", url, json=than,
            headers=self._tieu_de({"Accept": "text/event-stream"}))
        try:
            r = await khach.send(yeu_cau, stream=True)
        except httpx.HTTPError as e:
            raise LoiClaudeWeb(502, f"không nối được claude.ai ({type(e).__name__})") from e
        if r.status_code != 200:
            try:
                await r.aread()
            finally:
                await r.aclose()
            raise self._nem_loi(r, "gửi lời nhắn")
        return r

    # ── đọc SSE ─────────────────────────────────────────────────────

    async def doc_su_kien(self, r: httpx.Response):
        """Đọc dần response SSE, sinh từng sự kiện dict của claude.ai.

        Sự kiện «error» → ném LoiClaudeWeb(502). Gọi trong `async with` của
        chính generator này để response tự đóng khi hết/hỏng.
        """
        try:
            async for dong in r.aiter_lines():
                dong = dong.strip()
                if not dong or dong.startswith(":") or dong.startswith("event:"):
                    continue
                if dong.startswith("data:"):
                    dong = dong[5:].strip()
                if dong == "[DONE]":
                    return
                try:
                    sk = json.loads(dong)
                except ValueError:
                    continue
                if not isinstance(sk, dict):
                    continue
                if cv.la_su_kien_loi(sk):
                    raise LoiClaudeWeb(502, "claude.ai báo lỗi giữa luồng")
                yield sk
                if cv.la_su_kien_dung(sk):
                    return
        finally:
            await r.aclose()

    # ── kiểm tra cookie lúc cài đặt ─────────────────────────────────

    async def kiem_tra(self) -> tuple[bool, str | None, bool]:
        """(dùng_được, lý_do, là_lỗi_xác_thực). Chỉ gọi GET /api/organizations."""
        try:
            await self.lay_org_id()
        except LoiClaudeWeb as e:
            if e.ma in (401, 403):
                return False, "cookie không hợp lệ hoặc hết hạn", True
            return False, f"lỗi tạm thời (HTTP {e.ma})", False
        except Exception as e:  # noqa: BLE001 — mạng lạ: coi là tạm thời, giữ cookie
            LOG.warning("Kiểm tra cookie claude-web lỗi lạ: %s", type(e).__name__)
            return False, "lỗi kết nối tạm thời", False
        return True, None, False


class ClaudeWebClient:
    """Client NHẸ cho luồng tái dùng hồ sơ (NV93): kiểm ``sessionKey`` qua
    ``GET /api/organizations`` và lấy org có capability «chat».

    Chỉ dùng sessionKey đọc từ hồ sơ Google (không đoán thêm cookie nào);
    claude.ai thật có thể cần thêm cookie — CHƯA KIỂM với dịch vụ thật.
    """

    def __init__(self, session_key: str,
                 base_url: str = BASE_MAC_DINH,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 30.0):
        self._session_key = str(session_key or "")
        self._goc = (base_url or BASE_MAC_DINH).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._goc,
            cookies={"sessionKey": self._session_key},
            transport=transport,
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=10.0,
                                  pool=10.0),
            headers={"User-Agent": UA, "Accept": "*/*"},
        )

    async def lay_org(self) -> str:
        """GET /api/organizations → uuid của org có khả năng «chat».

        401/403 → LoiClaudeWeb (phiên không dùng được); JSON hỏng/thiếu org →
        LoiClaudeWeb rõ ràng (không đoán).
        """
        try:
            r = await self._client.get("/api/organizations")
        except httpx.HTTPError as e:
            raise LoiClaudeWeb(
                502, f"không nối được claude.ai ({type(e).__name__})") from e
        if r.status_code in (401, 403):
            raise LoiClaudeWeb(r.status_code,
                               "sessionKey không hợp lệ hoặc hết hạn")
        if r.status_code != 200:
            raise LoiClaudeWeb(r.status_code, "lấy danh sách tổ chức thất bại")
        try:
            ds = r.json()
        except ValueError as e:
            raise LoiClaudeWeb(
                502, "claude.ai trả JSON hỏng ở bước lấy tổ chức") from e
        org_id = ""
        if isinstance(ds, list) and ds:
            co_chat = [o for o in ds
                       if isinstance(o, dict)
                       and "chat" in (o.get("capabilities") or [])]
            org_id = str((co_chat[0] if co_chat else ds[0]).get("uuid") or "")
        elif isinstance(ds, dict):
            org_id = str(ds.get("uuid") or "")
        if not org_id:
            raise LoiClaudeWeb(401, "không tìm thấy tổ chức (phiên hết hạn?)")
        return org_id

    async def dong(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass
