"""Module muse2api — Muse (muse.ai) qua giao thức trực tiếp.

Xác thực bằng cookie của tab muse.ai đã đăng nhập — miễn phí, không cần
API key, không browser. Luồng: cookie → access_token → hatch token →
WebSocket Noise XX tới máy ảo → POST /chat/stream + poll /chat/history.

Thay thế bản Model API trả phí trước đây (giữ lại ở branch `model-api`
của repo TriTue2011/muse2api).

Khả năng: chat (stream / không stream). Giao thức nội bộ của web app đã
được reverse-engineer, không chính thức — Meta đổi web có thể làm module
ngừng hoạt động.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import secrets
import threading
import time
import uuid
from typing import AsyncIterator

from .chat import ChatSession, build_prompt
from .protocol import AuthError, Gateway, GatewayError, fetch_access_token, fetch_session_info

log = logging.getLogger("web2api.module.muse2api")

MODEL_ID = "muse"


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi module về mã trung lập của lõi."""
    if ma in (401, 403):
        return "khoa_hong"  # cookie sai / hết hạn
    if ma == 429:
        return "het_han_muc"
    if ma == 400:
        return "yeu_cau_sai"
    if ma is not None and ma >= 500:
        return "may_chu_loi"
    return None


def _ds_cookie(gia_tri) -> list[str]:
    # Cookie header chứa dấu cách sau ';' nên KHÔNG dùng ds_khoa
    # (nó tách theo khoảng trắng và làm vỡ cookie).
    if isinstance(gia_tri, str):
        gia_tri = [gia_tri]
    if not isinstance(gia_tri, list):
        return []
    return [c.strip() for c in gia_tri if isinstance(c, str) and c.strip()]


def _che(cookie: str) -> str:
    dau = cookie.split(";")[0].strip()
    if "=" in dau:
        k, v = dau.split("=", 1)
        v = v.strip()
        return f"{k}={v[:4]}…{v[-4:]}" if len(v) > 8 else f"{k}=…"
    return (cookie[:8] + "…") if len(cookie) > 8 else "…"


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


class _LoiModule(Exception):
    """Lỗi đã map sẵn thành tuple kết quả chuẩn (status, headers, body)."""

    def __init__(self, status: int, message: str, type_: str):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"message": message, "type": type_}}

    def ket_qua(self):
        return self.status, {"content-type": "application/json"}, \
            json.dumps(self.payload, ensure_ascii=False).encode()


class _TaiKhoan:
    """Một bộ cookie + kết nối Gateway duy trì. Khóa theo tài khoản vì
    lịch sử chat dùng chung và Noise decrypt không chịu được đọc xen kẽ."""

    def __init__(self, ten: str, cookies: str):
        self.ten = ten
        self.cookies = cookies
        self.khoa = threading.Lock()
        self.node_id = secrets.token_hex(8)
        self._gw: Gateway | None = None
        self.khoe = True

    def dam_bao(self) -> Gateway:
        if self._gw is None:
            self._gw = Gateway(self.cookies)
        return self._gw

    def bo(self):
        gw, self._gw = self._gw, None
        if gw is not None:
            try:
                gw.close()
            except Exception:  # noqa: BLE001 - best effort
                pass


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._accs: list[_TaiKhoan] = []
        self._vi_tri = 0
        self._khoa_chon = threading.Lock()

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    # ── Vòng đời ────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        cookies = _ds_cookie(cau_hinh.get("cookies"))
        self._accs = [_TaiKhoan(f"acc{i + 1}", c) for i, c in enumerate(cookies)]
        self._vi_tri = 0

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng HTTPS bootstrap (auth/check + session):
        không tốn tiền, không cần chat thật."""
        cookies = _ds_cookie(cau_hinh.get("cookies"))
        if not cookies:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập cookie nào (trường cookies). Lấy từ tab muse.ai "
                           "đã đăng nhập: DevTools (F12) → Application → Cookies."}
        kqs = await asyncio.gather(
            *[asyncio.to_thread(self._kiem_cookie, c) for c in cookies])
        ds, tot = [], []
        for c, (ok, loi) in zip(cookies, kqs):
            ds.append({"khoa": _che(c), "ok": ok, "loi": loi})
            if ok:
                tot.append(c)
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": "Không cookie nào đăng nhập được muse.ai — đăng nhập lại "
                           "trên trình duyệt rồi export lại cookie."}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"cookies": tot}}

    @staticmethod
    def _kiem_cookie(cookies: str) -> tuple[bool, str | None]:
        try:
            fetch_access_token(cookies)
            fetch_session_info(cookies)
            return True, None
        except AuthError as e:
            return False, f"cookie không hợp lệ/hết hạn: {e}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    async def models(self) -> list[dict]:
        return [{"id": f"{self.tien_to}/{MODEL_ID}", "object": "model",
                 "owned_by": "muse2api", "kha_nang": ["chat"]}]

    async def suc_khoe(self) -> dict:
        async def _kiem(acc: _TaiKhoan):
            ok, loi = await asyncio.to_thread(self._kiem_cookie, acc.cookies)
            return {"khoa": _che(acc.cookies), "ok": ok, "loi": loi,
                    "dang_dung": acc._gw is not None}
        ds = await asyncio.gather(*[_kiem(a) for a in self._accs]) if self._accs else []
        return {"ok": any(d["ok"] for d in ds), "khoa": ds}

    async def dung(self) -> None:
        for acc in self._accs:
            acc.bo()

    # ── Khả năng chat ────────────────────────────────────────────────────

    async def chat(self, body: dict, stream: bool):
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return _LoiModule(400, "'messages' phải là mảng không rỗng",
                              "yeu_cau_sai").ket_qua()
        prompt = build_prompt(msgs)
        if not prompt.strip():
            return _LoiModule(400, "prompt rỗng", "yeu_cau_sai").ket_qua()
        if not self._accs:
            return _LoiModule(401, "Module chưa được cấu hình cookie",
                              "khoa_hong").ket_qua()
        model = f"{self.tien_to}/{MODEL_ID}"
        if stream:
            return 200, {"content-type": "text/event-stream",
                         "cache-control": "no-cache",
                         "x-accel-buffering": "no"}, self._chay_stream(prompt, model)
        try:
            text = await asyncio.to_thread(self._chat_blocking, prompt)
        except _LoiModule as e:
            return e.ket_qua()
        return 200, {"content-type": "application/json"}, json.dumps({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0}}, ensure_ascii=False).encode()

    # ── nội bộ (chạy trong worker thread) ────────────────────────────────

    def _chon(self) -> _TaiKhoan:
        with self._khoa_chon:
            khoe = [a for a in self._accs if a.khoe] or self._accs
            if not khoe:
                raise _LoiModule(401, "Chưa có tài khoản nào", "khoa_hong")
            acc = khoe[self._vi_tri % len(khoe)]
            self._vi_tri += 1
            return acc

    def _ket_noi(self, acc: _TaiKhoan) -> Gateway:
        """Kết nối; thử lại 1 lần nếu lỗi mạng (không thử lại khi lỗi auth)."""
        try:
            return acc.dam_bao()
        except AuthError:
            acc.khoe = False
            acc.bo()
            raise
        except Exception as e1:  # noqa: BLE001
            acc.bo()
            try:
                return acc.dam_bao()
            except AuthError:
                acc.khoe = False
                acc.bo()
                raise
            except Exception as e2:  # noqa: BLE001
                acc.bo()
                raise GatewayError(-1, f"{e1}; {e2}") from e2

    def _chat_blocking(self, prompt: str) -> str:
        acc = self._chon()
        acc.khoa.acquire()
        try:
            try:
                gw = self._ket_noi(acc)
            except AuthError as e:
                raise _LoiModule(401, f"Cookie không hợp lệ/hết hạn: {e}",
                                 "khoa_hong") from e
            except Exception as e:  # noqa: BLE001
                raise _LoiModule(502, f"Không kết nối được muse.ai: {e}",
                                 "may_chu_loi") from e
            sess = ChatSession(gw, acc.node_id)
            try:
                return sess.run_collect(prompt)
            except AuthError as e:
                acc.khoe = False
                acc.bo()
                raise _LoiModule(401, f"Cookie hết hạn giữa chừng: {e}",
                                 "khoa_hong") from e
            except TimeoutError as e:
                acc.bo()
                raise _LoiModule(504, str(e), "timeout_error") from e
            except Exception as e:  # noqa: BLE001
                acc.bo()
                raise _LoiModule(502, f"Lỗi khi chat: {e}",
                                 "may_chu_loi") from e
        finally:
            acc.khoa.release()

    async def _chay_stream(self, prompt: str, model: str) -> AsyncIterator[bytes]:
        q: queue.Queue = queue.Queue()
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        def _worker():
            try:
                acc = self._chon()
            except _LoiModule as e:
                q.put(("error", e))
                return
            acc.khoa.acquire()
            try:
                try:
                    gw = self._ket_noi(acc)
                except AuthError as e:
                    q.put(("error", _LoiModule(
                        401, f"Cookie không hợp lệ/hết hạn: {e}", "khoa_hong")))
                    return
                except Exception as e:  # noqa: BLE001
                    q.put(("error", _LoiModule(
                        502, f"Không kết nối được muse.ai: {e}", "may_chu_loi")))
                    return
                sess = ChatSession(gw, acc.node_id)
                try:
                    for delta in sess.run(prompt):
                        q.put(("delta", delta))
                    q.put(("done", None))
                except AuthError as e:
                    acc.khoe = False
                    acc.bo()
                    q.put(("error", _LoiModule(
                        401, f"Cookie hết hạn giữa chừng: {e}", "khoa_hong")))
                except Exception as e:  # noqa: BLE001
                    acc.bo()
                    q.put(("error", _LoiModule(
                        502, f"Lỗi khi chat: {e}", "may_chu_loi")))
            finally:
                acc.khoa.release()

        threading.Thread(target=_worker, daemon=True).start()
        first = True
        while True:
            kind, _payload = await asyncio.to_thread(q.get)
            if kind == "delta":
                delta = {"role": "assistant", "content": _payload} if first \
                    else {"content": _payload}
                first = False
                yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": delta,
                                         "finish_reason": None}]})
            elif kind == "done":
                yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {},
                                         "finish_reason": "stop"}]})
                yield b"data: [DONE]\n\n"
                return
            else:
                # Lỗi giữa stream: dòng trạng thái đã là 200, chỉ kết thúc stream.
                return
