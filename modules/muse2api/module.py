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
    """Chỉ hiện dạng …abcd (4 ký tự cuối của GIÁ TRỊ cookie đầu).

    Không hiện tên cookie lẫn 4 ký tự đầu; giá trị ≤ 8 ký tự → …
    """
    dau = cookie.split(";")[0].strip()
    if "=" in dau:
        _, v = dau.split("=", 1)
        v = v.strip()
        return f"…{v[-4:]}" if len(v) > 8 else "…"
    return f"…{cookie[-4:]}" if len(cookie) > 8 else "…"


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
        ds, giu = [], []
        co_dat = False
        for c, (ok, loi, bi_tu_choi) in zip(cookies, kqs):
            muc = {"khoa": _che(c), "ok": ok, "loi": loi}
            ds.append(muc)
            if ok:
                co_dat = True
                giu.append(c)
            elif not bi_tu_choi:
                # Lỗi tạm: vẫn giữ trong cấu hình lưu
                giu.append(c)
        if not co_dat:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": "Không cookie nào đăng nhập được muse.ai — đăng nhập lại "
                           "trên trình duyệt rồi export lại cookie."}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"cookies": giu}}

    @staticmethod
    def _kiem_cookie(cookies: str) -> tuple[bool, str | None, bool]:
        """Kiểm cookie. Trả về (ok, loi, bi_tu_choi)."""
        try:
            fetch_access_token(cookies)
            fetch_session_info(cookies)
            return True, None, False
        except AuthError as e:
            return False, f"cookie không hợp lệ/hết hạn: {e}", True
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}", False

    async def models(self) -> list[dict]:
        return [{"id": f"{self.tien_to}/{MODEL_ID}", "object": "model",
                 "owned_by": "muse2api", "kha_nang": ["chat"]}]

    async def suc_khoe(self) -> dict:
        async def _kiem(acc: _TaiKhoan):
            ok, loi, _ = await asyncio.to_thread(self._kiem_cookie, acc.cookies)
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
        # Bước 6: phần tử messages không phải dict → 400
        for phan_tu in msgs:
            if not isinstance(phan_tu, dict):
                return _LoiModule(400, "mỗi phần tử của 'messages' phải là đối tượng",
                                  "yeu_cau_sai").ket_qua()
        prompt = build_prompt(msgs)
        if not prompt.strip():
            return _LoiModule(400, "prompt rỗng", "yeu_cau_sai").ket_qua()
        if not self._accs:
            return _LoiModule(401, "Module chưa được cấu hình cookie",
                              "khoa_hong").ket_qua()
        model = f"{self.tien_to}/{MODEL_ID}"

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        co_huy = threading.Event()

        def _day(kind: str, payload=None):
            loop.call_soon_threadsafe(q.put_nowait, (kind, payload))

        def _luot(acc: _TaiKhoan, accs_da_thu: set) -> bool:
            """Một tài khoản, tối đa 2 lần (lần 2 = nối lại khi kết nối cũ
            chết trước khi gửi). True = đã xong (đẩy kết quả hoặc khách ngắt),
            False = chuyển sang tài khoản kế."""
            for lan in range(2):
                sess = None
                try:
                    sess = ChatSession(acc.dam_bao(), acc.node_id)
                    for delta in sess.run(prompt, co_huy=co_huy):
                        if co_huy.is_set():
                            return True
                        acc.khoe = True  # có trả lời thật = cookie còn sống
                        _day("delta", delta)
                    if not co_huy.is_set():
                        _day("done")
                    return True
                except Exception as e:  # noqa: BLE001 - phân loại theo kiểu bên dưới
                    da_gui = sess is not None and sess.da_gui
                    if isinstance(e, AuthError) and not da_gui and any(
                            a not in accs_da_thu for a in self._accs):
                        acc.khoe = False
                        acc.bo()
                        log.warning("Tài khoản %s bị từ chối, chuyển sang tài khoản kế", acc.ten)
                        return False
                    if lan == 0 and sess is not None and not da_gui and self._la_loi_ket_noi(e):
                        log.warning("Kết nối của %s đã chết trước khi gửi, nối lại", acc.ten)
                        acc.bo()
                        continue
                    _day("error", self._map_loi(e, acc))
                    return True

        def _worker():
            try:
                accs_da_thu: set[_TaiKhoan] = set()
                while not co_huy.is_set():
                    acc = self._chon_chua_thu(accs_da_thu)  # hết tài khoản → _LoiModule
                    accs_da_thu.add(acc)
                    with acc.khoa:
                        # Khách đã ngắt khi còn xếp hàng chờ khoá → không gửi gì.
                        if co_huy.is_set() or _luot(acc, accs_da_thu):
                            return
            except _LoiModule as e:
                _day("error", e)
            except BaseException as e:  # noqa: BLE001 - worker không bao giờ chết im lặng
                log.exception("muse2api: worker hỏng ngoài dự kiến")
                _day("error", _LoiModule(502, f"Lỗi nội bộ module: {type(e).__name__}",
                                         "may_chu_loi"))

        threading.Thread(target=_worker, daemon=True).start()

        try:
            # Chờ sự kiện đầu tiên để quyết định mã trạng thái
            kind, payload = await q.get()

            if kind == "error":
                co_huy.set()
                return payload.ket_qua()

            if not stream:
                chunks = []
                if kind == "delta":
                    chunks.append(payload)
                while kind != "done":
                    kind, payload = await q.get()
                    if kind == "delta":
                        chunks.append(payload)
                    elif kind == "error":
                        co_huy.set()
                        return payload.ket_qua()
        except asyncio.CancelledError:
            co_huy.set()  # khách ngắt khi đang chờ → worker dừng, không gửi/không poll tiếp
            raise

        if not stream:
            text = "".join(chunks)
            return 200, {"content-type": "application/json"}, json.dumps({
                "id": chat_id,
                "object": "chat.completion", "created": created,
                "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0}}, ensure_ascii=False).encode()

        async def _sinh_sse() -> AsyncIterator[bytes]:
            try:
                curr_kind = kind
                curr_payload = payload
                first_chunk = True
                while True:
                    if curr_kind == "delta":
                        delta = {"role": "assistant", "content": curr_payload} if first_chunk \
                            else {"content": curr_payload}
                        first_chunk = False
                        yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                                    "created": created, "model": model,
                                    "choices": [{"index": 0, "delta": delta,
                                                 "finish_reason": None}]})
                    elif curr_kind == "done":
                        yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                                    "created": created, "model": model,
                                    "choices": [{"index": 0, "delta": {},
                                                 "finish_reason": "stop"}]})
                        yield b"data: [DONE]\n\n"
                        return
                    elif curr_kind == "error":
                        yield _sse({"error": {"message": str(curr_payload),
                                              "type": curr_payload.payload["error"]["type"]}})
                        return
                    curr_kind, curr_payload = await q.get()
            finally:
                co_huy.set()

        return 200, {"content-type": "text/event-stream",
                     "cache-control": "no-cache",
                     "x-accel-buffering": "no"}, _sinh_sse()

    # ── nội bộ ──────────────────────────────────────────────────────────

    def _chon_chua_thu(self, da_thu: set[_TaiKhoan]) -> _TaiKhoan:
        """Xoay vòng trong số tài khoản CHƯA thử ở yêu cầu này, ưu tiên tài
        khoản khoẻ; không còn ai khoẻ thì vẫn xoay vòng qua tất cả (khoe chỉ
        là gợi ý thứ tự, không loại hẳn — 401/403 có thể chỉ thoáng qua)."""
        with self._khoa_chon:
            chua_thu = [a for a in self._accs if a not in da_thu]
            if not chua_thu:
                raise _LoiModule(401, "Tất cả tài khoản cookie đều hỏng hoặc chưa có", "khoa_hong")
            ung_vien = [a for a in chua_thu if a.khoe] or chua_thu
            acc = ung_vien[self._vi_tri % len(ung_vien)]
            self._vi_tri += 1
            return acc

    def _map_loi(self, e: Exception, acc: _TaiKhoan) -> _LoiModule:
        """Ánh xạ ngoại lệ → _LoiModule, đặt acc.khoe = False CHỈ khi AuthError.

        Thông báo cho khách chỉ dựng từ CẤU TRÚC (kiểu ngoại lệ, mã trạng
        thái); nội dung phản hồi thượng nguồn trong str(e) chỉ vào log.
        """
        log.warning("muse2api %s: %s: %s", acc.ten, type(e).__name__, e)
        ma = e.status if isinstance(e, GatewayError) else None
        chi_tiet = type(e).__name__ + (f" status={ma}" if isinstance(ma, int) and ma > 0 else "")
        if isinstance(e, AuthError):
            acc.khoe = False
            acc.bo()
            return _LoiModule(401, f"Cookie không hợp lệ/hết hạn ({chi_tiet})", "khoa_hong")
        if isinstance(e, TimeoutError):
            acc.bo()
            return _LoiModule(504, f"Hết hạn chờ muse.ai ({chi_tiet})", "timeout_error")
        if ma == 429:
            return _LoiModule(429, f"Hết hạn mức ({chi_tiet})", "het_han_muc")
        acc.bo()
        return _LoiModule(502, f"Lỗi máy chủ hoặc kết nối: {chi_tiet}", "may_chu_loi")

    def _la_loi_ket_noi(self, e: Exception) -> bool:
        """True nếu lỗi kiểu kết nối (không phải auth, không phải timeout, không phải 429)."""
        if isinstance(e, (AuthError, TimeoutError)):
            return False
        if isinstance(e, GatewayError) and getattr(e, "status", None) == 429:
            return False
        return True
