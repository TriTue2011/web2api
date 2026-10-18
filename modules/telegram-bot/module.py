"""Module telegram-bot — KÊNH GỬI RA (chuẩn `kenh_ra`) tới Telegram.

Chỉ GỬI RA văn bản qua Bot API công khai của Telegram
(`POST https://api.telegram.org/bot<TOKEN>/sendMessage`). Không nhận tin vào,
không webhook, không bot trả lời — kênh chat hai chiều không thuộc lõi.

Thiết kế theo cấu trúc, không theo danh sách:
- Đích xác định BỞI ĐÍCH ĐÃ KHAI: `kenh_id` → đích → (bot token, chat_id, chu_de).
  Không có «bot mặc định», không đoán đích đầu danh sách (bài học c2a: gửi nhầm
  đích mà vẫn `ok:true` là mất tiền oan).
- Địa chỉ gọi CỐ ĐỊNH `https://api.telegram.org` (chống SSRF); ghi đè gốc chỉ tồn
  tại dưới dạng thuộc tính nội bộ `_goc_test` và chỉ có hiệu lực khi biến môi
  trường test `WEB2API_TEST_CHO_PHEP_LOOPBACK=1` được đặt VÀ giá trị là loopback.
- BẢO MẬT token (token nằm TRONG URL):
  + trong lúc gọi, logger `httpx`/`httpcore` bị nâng lên WARNING (httpx ghi URL ở
    INFO/DEBUG);
  + không bao giờ `str()` ngoại lệ httpx (chúng có thể in URL chứa token) — log và
    kết quả chỉ dùng tên kiểu ngoại lệ;
  + `kenh_ra()` chỉ trả `[{id, ten}]`, không token, không chat_id thô;
  + kết quả chỉ có `{"ok": bool, "ma": <mã ngắn>}` — `description` của Telegram
    không bao giờ lộ ra.
- Tin dài: tách ở ranh giới dòng, tối đa 5 tin, phần dư cắt kèm «…»; không gửi
  `parse_mode` (văn bản thuần — tránh lỗi định dạng và tránh bị chèn markup).
- Lỗi mạng (kể cả hết giờ): thử lại tối đa 1 lần; 4xx không thử lại; 429
  (`retry_after`) → báo thất bại, không ngủ dài.

Mã này chạy TRONG tiến trình lõi và được nạp từ <WEB2API_DATA>/modules/telegram-bot/.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from urllib.parse import urlsplit

import httpx

from app.tu_xa import cho_phep_loopback_cho_test

#: Địa chỉ gọi CỐ ĐỊNH — không đọc từ cấu hình người dùng (chống SSRF).
GOC_MAC_DINH = "https://api.telegram.org"
#: Giới hạn ký tự của một tin Telegram.
GIOI_HAN_TIN = 4096
#: Số tin tối đa cho một lần gửi (phần dư cắt kèm «…»).
TIN_TOI_DA = 5
#: Hết giờ kết nối/đọc/ghi.
_HET_GIO = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
#: Lỗi mạng được thử lại tối đa 1 lần (TransportError bao cả ConnectError,
#: TimeoutException, ReadError…).
_LOI_MANG = httpx.TransportError

log = logging.getLogger("web2api.module.telegram_bot")


@contextlib.contextmanager
def _khoa_log_token():
    """Nâng logger httpx/httpcore lên WARNING trong lúc gọi.

    Token nằm trong URL nên httpx sẽ ghi URL (kèm token) ở mức INFO/DEBUG.
    Khôi phục mức cũ sau khi gọi xong.
    """
    cac_logger = (logging.getLogger("httpx"), logging.getLogger("httpcore"))
    muc_cu = [(lg, lg.level) for lg in cac_logger]
    for lg in cac_logger:
        if lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for lg, muc in muc_cu:
            lg.setLevel(muc)


def _kiem_dich(gia_tri) -> list[dict]:
    """Chuẩn hoá + thẩm định cấu trúc danh sách đích (nguyên tắc, không danh sách
    tên trường cứng ngoài id/chat_id/chu_de/ten).

    Chấp nhận list hoặc chuỗi JSON. Mỗi đích: `id` (chuỗi khác rỗng, duy nhất),
    `chat_id` (số hoặc chuỗi khác rỗng), `chu_de` (số nguyên, tuỳ chọn),
    `ten` (hiển thị, thiếu thì lấy `id`). Sai cấu trúc → ValueError nêu rõ trường.
    """
    if gia_tri is None or gia_tri == "":
        return []
    if isinstance(gia_tri, str):
        try:
            gia_tri = json.loads(gia_tri)
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError("Trường «dich» không phải JSON hợp lệ.") from e
    if not isinstance(gia_tri, list):
        raise ValueError("Trường «dich» phải là danh sách các đích.")
    da_thay = set()
    ra: list[dict] = []
    for i, muc in enumerate(gia_tri):
        vi_tri = f"đích thứ {i + 1}"
        if not isinstance(muc, dict):
            raise ValueError(f"{vi_tri} phải là đối tượng (có «id», «chat_id»).")
        ma = muc.get("id")
        if not isinstance(ma, str) or not ma.strip():
            raise ValueError(f"{vi_tri} thiếu «id» (chuỗi khác rỗng do chủ máy đặt).")
        ma = ma.strip()
        if ma in da_thay:
            raise ValueError(f"Đích «{ma}» bị trùng «id».")
        da_thay.add(ma)
        chat_id = muc.get("chat_id")
        if (isinstance(chat_id, bool) or not isinstance(chat_id, (int, str))
                or (isinstance(chat_id, str) and not chat_id.strip())):
            raise ValueError(f"Đích «{ma}» thiếu «chat_id» hợp lệ.")
        chu_de = muc.get("chu_de")
        if chu_de is not None:
            try:
                chu_de = int(str(chu_de).strip())
            except (ValueError, TypeError) as e:
                raise ValueError(f"Đích «{ma}»: «chu_de» phải là số nguyên.") from e
        ten = muc.get("ten")
        if not isinstance(ten, str) or not ten.strip():
            ten = ma
        ra.append({"id": ma, "ten": ten.strip(), "chat_id": chat_id, "chu_de": chu_de})
    return ra


def _tach_tin(van_ban: str) -> list[str]:
    """Tách tin dài ở ranh giới dòng, mỗi phần ≤ 4096 ký tự, tối đa 5 phần.

    Phần dư sau 5 phần được gộp vào phần cuối và cắt kèm «…». Không có ranh giới
    dòng trong 4096 ký tự đầu thì cắt cứng.
    """
    phan: list[str] = []
    con = van_ban
    while con and len(phan) < TIN_TOI_DA:
        if len(con) <= GIOI_HAN_TIN:
            phan.append(con)
            con = ""
        else:
            vi_tri = con.rfind("\n", 0, GIOI_HAN_TIN + 1)
            cat = vi_tri if vi_tri > 0 else GIOI_HAN_TIN
            phan.append(con[:cat])
            con = con[cat + 1:] if vi_tri > 0 else con[cat:]
    if con:
        gop = phan[-1] + "\n" + con if phan else con
        phan[-1] = (gop[:GIOI_HAN_TIN - 1] + "…") if len(gop) > GIOI_HAN_TIN else gop + "…"
    return phan


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng kenh_ra)."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "telegram")
        self._token = ""
        self._dich: dict[str, dict] = {}
        # Tham số NỘI BỘ chỉ cho test: gốc máy chủ giả. Không đọc từ cấu hình
        # người dùng; chỉ có hiệu lực khi WEB2API_TEST_CHO_PHEP_LOOPBACK=1.
        self._goc_test: str | None = None

    def _goc(self) -> str:
        goc_test = self._goc_test
        if goc_test and cho_phep_loopback_cho_test():
            phan = urlsplit(str(goc_test))
            if (phan.scheme == "http" and phan.hostname in ("127.0.0.1", "localhost")
                    and not phan.username and not phan.password):
                return str(goc_test).rstrip("/")
            log.warning("telegram-bot: bỏ qua gốc test không hợp lệ (chỉ nhận http loopback)")
        return GOC_MAC_DINH

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        cau_hinh = cau_hinh or {}
        self._token = str(cau_hinh.get("bot_token") or "").strip()
        try:
            self._dich = {d["id"]: d for d in _kiem_dich(cau_hinh.get("dich"))}
        except ValueError as e:
            # Cấu hình hỏng không được làm sập tiến trình: coi như chưa có đích.
            log.warning("telegram-bot: cấu hình đích sai (%s)", e)
            self._dich = {}

    def kenh_ra(self) -> list[dict]:
        """Danh sách đích — KHÔNG chứa bí mật (token, chat_id thô)."""
        return [{"id": d["id"], "ten": d["ten"]} for d in self._dich.values()]

    async def _post(self, url: str, than: dict):
        async with httpx.AsyncClient(timeout=_HET_GIO) as client:
            return await client.post(url, json=than,
                                     headers={"Content-Type": "application/json"})

    async def _get(self, url: str):
        async with httpx.AsyncClient(timeout=_HET_GIO) as client:
            return await client.get(url)

    async def _gui_mot_phan(self, dich: dict, van_ban: str) -> dict:
        """Gửi một phần tin (≤ 4096 ký tự). Lỗi mạng thử lại tối đa 1 lần."""
        than = {"chat_id": dich["chat_id"], "text": van_ban,
                "disable_web_page_preview": True}
        if dich.get("chu_de") is not None:
            than["message_thread_id"] = dich["chu_de"]
        # KHÔNG gửi parse_mode: văn bản thuần, tránh lỗi định dạng và tránh bị chèn.
        url = f"{self._goc()}/bot{self._token}/sendMessage"

        dap = None
        for lan_thu in range(2):  # lần đầu + thử lại tối đa 1 lần
            try:
                with _khoa_log_token():
                    dap = await self._post(url, than)
                break
            except _LOI_MANG as e:
                # Chỉ lỗi mạng mới thử lại. KHÔNG str(e): ngoại lệ httpx có thể
                # in URL chứa token — log chỉ tên kiểu ngoại lệ.
                log.warning("telegram-bot: lỗi mạng lần %d (%s)", lan_thu + 1,
                            type(e).__name__)
                dap = e
                continue
            except Exception as e:
                log.warning("telegram-bot: lỗi không xác định (%s)", type(e).__name__)
                return {"ok": False, "ma": "loi_gui"}
        if isinstance(dap, Exception):
            ma = "het_gio" if isinstance(dap, httpx.TimeoutException) else "mang_loi"
            return {"ok": False, "ma": ma}

        if dap.status_code == 429:
            # Bị giới hạn: báo thất bại, KHÔNG ngủ theo retry_after (lõi đã có hạn dòng).
            return {"ok": False, "ma": "bi_gioi_han"}
        if dap.status_code >= 400:
            return {"ok": False, "ma": "telegram_tu_choi"}  # 4xx: không thử lại
        try:
            than_tra = dap.json()
        except Exception:
            return {"ok": False, "ma": "telegram_loi"}
        if isinstance(than_tra, dict) and than_tra.get("ok") is True:
            return {"ok": True}
        # ok:false hoặc thân lạ — description của Telegram KHÔNG lộ ra ngoài.
        return {"ok": False, "ma": "telegram_loi"}

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        """Gửi văn bản tới đích đã khai. Trả {"ok": bool, "ma"?: mã ngắn}.

        Đích được tra BỞI kenh_id trong danh sách đã khai — không có «bot mặc
        định», không đoán. `ok` luôn là bool thật (lõi bắt buộc).
        """
        if not self._token or not self._dich:
            return {"ok": False, "ma": "chua_cau_hinh"}
        dich = dich or {}
        kenh_id = dich.get("kenh_id")
        kenh_id = kenh_id.strip() if isinstance(kenh_id, str) else ""
        muc = self._dich.get(kenh_id)
        if muc is None:
            return {"ok": False, "ma": "dich_khong_co"}
        van_ban = (noi_dung or {}).get("van_ban")
        if not isinstance(van_ban, str) or not van_ban.strip():
            return {"ok": False, "ma": "noi_dung_thieu"}
        for phan in _tach_tin(van_ban):
            ket_qua = await self._gui_mot_phan(muc, phan)
            if not ket_qua["ok"]:
                return ket_qua
        return {"ok": True}

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm token bằng GET /getMe THẬT (rẻ, không tốn gì) và thẩm định cấu
        trúc danh sách đích. Chỉ giữ cấu hình gọi được."""
        cau_hinh = cau_hinh or {}
        token = str(cau_hinh.get("bot_token") or "").strip()
        if not token:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập token bot (trường bot_token).", "cau_hinh_luu": {}}
        try:
            dich = _kiem_dich(cau_hinh.get("dich"))
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e),
                    "cau_hinh_luu": {}}
        if not dich:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa khai đích nào (trường «dich»).", "cau_hinh_luu": {}}

        url = f"{self._goc()}/bot{token}/getMe"
        try:
            with _khoa_log_token():
                dap = await self._get(url)
            than = dap.json()
            hop_le = isinstance(than, dict) and than.get("ok") is True
        except _LOI_MANG as e:
            # KHÔNG str(e): ngoại lệ httpx có thể in URL chứa token.
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": f"Không gọi được máy chủ Telegram ({type(e).__name__}).",
                    "cau_hinh_luu": {}}
        except Exception as e:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": f"Lỗi không xác định khi kiểm token ({type(e).__name__}).",
                    "cau_hinh_luu": {}}
        khoa_che = f"…{token[-4:]}"
        if not hop_le:
            return {"ok": False,
                    "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": False}],
                    "loi": "Token bot không dùng được (máy chủ từ chối).",
                    "cau_hinh_luu": {}}
        self.nap({"bot_token": token, "dich": dich})
        return {"ok": True,
                "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": True}],
                "loi": None,
                "cau_hinh_luu": {"bot_token": token, "dich": dich}}

    async def dung(self) -> None:
        pass
