"""Module zalo-bot — kênh gửi ra (chuẩn `kenh_ra`) tới Zalo Bot: chỉ gửi văn bản thuần.

Thiết kế bảo mật (từ bài học đắt giá của c2a, đo thật):
- Mỗi ĐÍCH khai rõ (id → bot → chat_id); KHÔNG có «bot mặc định»; token bot được
  chọn THEO ĐÍCH ở đúng lời gọi mạng (không biến/ngữ cảnh toàn cục).
- Token nằm trong URL (`POST /bot<TOKEN>/sendMessage`) → chặn log URL của
  httpx/httpcore trong lúc gọi; không log URL/token/chat_id (chỉ id đích + tên
  kiểu ngoại lệ).
- Địa chỉ gọi CỐ ĐỊNH; ghi đè gốc chỉ cho test qua tham số nội bộ `_goc_kiem_thu`
  (KHÔNG đọc từ cấu hình — chống SSRF).
"""

from __future__ import annotations

import contextlib
import json
import logging

import httpx

log = logging.getLogger(__name__)

GOC_ZALO = "https://bot-api.zaloplatforms.com"
GIOI_HAN_TIN = 1990
SO_TIN_TOI_DA = 5
DAU_ELLIPSIS = "…"

#: Mã lỗi ngắn trả cho lõi/người gọi — không lộ `description` thô của Zalo.
MA_LOI = (
    "chua_cau_hinh", "dich_sai", "noi_dung_sai", "chat_sai", "yeu_cau_sai",
    "het_han_muc", "may_chu_loi", "phan_hoi_sai", "mang_loi", "loi_khong_ngu",
)


#: Token đang dùng (chỉ thêm) — bộ lọc log chặn mọi dòng của thư viện HTTP có chứa token (token nằm TRONG URL; httpx ghi URL ở mức INFO).
_TOKEN_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy")


class _ChanRoToken(logging.Filter):
    """Bỏ dòng log chứa token. Cài MỘT lần lên logger của thư viện HTTP — KHÔNG đổi mức log toàn cục (đổi quanh mỗi lời gọi rồi
    khôi phục sẽ kẹt logger ở mức cao khi hai lượt gửi chạy xen kẽ)."""
    _zalo_bot_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        return not any(t in noi_dung for t in _TOKEN_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_zalo_bot_loc", False)]:
            lg.removeFilter(f)   # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanRoToken())


_cai_bo_loc_log()


def _tach_tin(van_ban: str) -> list[str]:
    """Tách văn bản dài thành tin ≤ 1990 ký tự ở ranh giới dòng.

    Dòng đơn dài hơn 1990 thì cắt cứng. Tối đa 5 tin; phần dư cắt kèm «…»
    (tin cuối giữ ≤ 1990 kể cả dấu «…»).
    """
    dong_nho: list[str] = []
    for dong in van_ban.split("\n"):
        while len(dong) > GIOI_HAN_TIN:
            dong_nho.append(dong[:GIOI_HAN_TIN])
            dong = dong[GIOI_HAN_TIN:]
        dong_nho.append(dong)
    phan: list[str] = []
    hien: list[str] = []
    dai = 0
    for dong in dong_nho:
        them = len(dong) + (1 if hien else 0)
        if dai + them > GIOI_HAN_TIN:
            phan.append("\n".join(hien))
            hien = [dong]
            dai = len(dong)
        else:
            hien.append(dong)
            dai += them
    if hien:
        phan.append("\n".join(hien))
    if len(phan) > SO_TIN_TOI_DA:
        phan = phan[:SO_TIN_TOI_DA]
        # Bỏ bớt các dòng cuối của tin thứ 5 để tin cuối gồm các dòng NGUYÊN VẸN
        # và vẫn kết thúc bằng «…» trong giới hạn 1990 ký tự.
        dong_cuoi = phan[-1].split("\n")
        while dong_cuoi and len("\n".join(dong_cuoi) + DAU_ELLIPSIS) > GIOI_HAN_TIN:
            dong_cuoi.pop()
        phan[-1] = ("\n".join(dong_cuoi) + DAU_ELLIPSIS) if dong_cuoi else DAU_ELLIPSIS
    return phan


def _ten_bot_mac_dinh(i: int) -> str:
    return f"bot-{i + 1}"


class Module:
    """Kênh gửi ra Zalo Bot qua `POST /bot<TOKEN>/sendMessage`."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self._goc = (_goc_kiem_thu or GOC_ZALO).rstrip("/")
        self._bots: list[dict] = []   # [{"ten":..., "token":...}]
        self._dich: list[dict] = []   # [{"id","ten","chat_id","bot": tên bot}]

    # ── phân tích cấu hình ──

    def _phan_tich(self, cau_hinh: dict) -> tuple[list[dict], list[dict]]:
        """Trả (bots, dich); ném ValueError kèm lý do ngắn khi cấu hình sai."""
        if not isinstance(cau_hinh, dict):
            raise ValueError("cấu hình phải là dict")
        tokens = cau_hinh.get("bot_token")
        if isinstance(tokens, str):
            tokens = [t.strip() for t in tokens.replace(",", "\n").split("\n") if t.strip()]
        if not isinstance(tokens, list) or not tokens or not all(
                isinstance(t, str) and t.strip() for t in tokens):
            raise ValueError("bot_token: cần danh sách token không rỗng")
        ten_bot = cau_hinh.get("ten_bot")
        if isinstance(ten_bot, str) and ten_bot.strip():
            ds_ten = [t.strip() for t in ten_bot.replace("\n", ",").split(",") if t.strip()]
        else:
            ds_ten = []
        bots = [{"ten": ds_ten[i] if i < len(ds_ten) and ds_ten[i] else _ten_bot_mac_dinh(i),
                 "token": t.strip()} for i, t in enumerate(tokens)]
        ten_hop_le = {b["ten"] for b in bots}

        def _bot_cua(muc) -> str:
            b = muc.get("bot")
            if isinstance(b, int) and 0 <= b < len(bots):
                return bots[b]["ten"]
            if isinstance(b, str):
                b = b.strip()
                if b in ten_hop_le:
                    return b
                if b.isdigit() and 0 <= int(b) < len(bots):
                    return bots[int(b)]["ten"]
            raise ValueError(f"đích {muc.get('id')!r}: bot không hợp lệ (không có «bot mặc định»)")

        tho = cau_hinh.get("danh_sach_dich")
        if tho in (None, ""):
            return bots, []
        if not isinstance(tho, str):
            raise ValueError("danh_sach_dich: cần chuỗi JSON")
        try:
            ds = json.loads(tho)
        except Exception:
            raise ValueError("danh_sach_dich: JSON không hợp lệ")
        if not isinstance(ds, list):
            raise ValueError("danh_sach_dich: cần mảng JSON")
        dich = []
        for muc in ds:
            if not isinstance(muc, dict):
                raise ValueError("danh_sach_dich: mỗi đích là dict")
            d_id = str(muc.get("id") or "").strip()
            chat_id = str(muc.get("chat_id") or "").strip()
            if not d_id or not chat_id:
                raise ValueError("đích thiếu id hoặc chat_id")
            dich.append({"id": d_id, "ten": str(muc.get("ten") or d_id),
                         "chat_id": chat_id, "bot": _bot_cua(muc)})
        return bots, dich

    # ── hợp đồng module ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình; lỗi → module rỗng + warning (không ném)."""
        try:
            self._bots, self._dich = self._phan_tich(cau_hinh)
            _TOKEN_DANG_DUNG.update(b["token"] for b in self._bots)
        except Exception as e:
            log.warning("zalo-bot: cấu hình sai (%s) — module rỗng", e)
            self._bots, self._dich = [], []

    async def cai(self, cau_hinh: dict) -> dict:
        """Cài đặt: lỗi → {"ok": False, "loi": ...}; ok → cau_hinh_luu đã chuẩn hoá."""
        try:
            bots, dich = self._phan_tich(cau_hinh)
        except Exception as e:
            return {"ok": False, "loi": f"cấu hình sai: {e}"}
        self._bots, self._dich = bots, dich
        _TOKEN_DANG_DUNG.update(x["token"] for x in bots)
        return {"ok": True, "cau_hinh_luu": {
            "bot_token": [b["token"] for b in bots],
            "ten_bot": ", ".join(b["ten"] for b in bots),
            "danh_sach_dich": json.dumps(
                [{"id": d["id"], "ten": d["ten"], "chat_id": d["chat_id"], "bot": d["bot"]}
                 for d in dich], ensure_ascii=False),
        }}

    def kenh_ra(self) -> list[dict]:
        """Danh sách đích gửi ra — KHÔNG chứa token/chat_id thô."""
        return [{"id": d["id"], "ten": d["ten"]} for d in self._dich]

    async def suc_khoe(self) -> dict:
        return {"ok": True, "so_bot": len(self._bots), "so_dich": len(self._dich)}

    async def dung(self) -> None:
        return None

    # ── gửi tin ──

    def _token_theo_dich(self, d: dict) -> str:
        for b in self._bots:
            if b["ten"] == d["bot"]:
                return b["token"]
        raise KeyError(f"không có token cho bot {d['bot']!r}")

    async def gui_tin(self, dich: dict, noi_dung: dict) -> dict:
        """Gửi văn bản tới đích. Trả {"ok": True} chỉ khi Zalo trả ok là bool True thật."""
        if not self._dich:
            return {"ok": False, "ma": "chua_cau_hinh"}
        kenh_id = str((dich or {}).get("kenh_id") or (dich or {}).get("id") or "").strip()
        d = next((x for x in self._dich if x["id"] == kenh_id), None)
        if d is None:
            return {"ok": False, "ma": "dich_sai"}
        van_ban = (noi_dung or {}).get("van_ban")
        if not isinstance(van_ban, str) or not van_ban.strip():
            return {"ok": False, "ma": "noi_dung_sai"}
        try:
            token = self._token_theo_dich(d)
        except KeyError:
            return {"ok": False, "ma": "dich_sai"}
        chat_id = d["chat_id"]
        d_id = d["id"]
        timeout = httpx.Timeout(10.0, read=30.0)
        # KHÔNG retry: lõi NV107 đã thử lại theo chu kỳ của nó.
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for tin in _tach_tin(van_ban):
                    r = await client.post(
                        f"{self._goc}/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": tin},
                    )
                    ma = self._phan_loai(r)
                    if ma is not None:
                        return {"ok": False, "ma": ma}
        except httpx.HTTPError as e:
            # Chỉ log id đích + tên kiểu ngoại lệ — str(e) có thể chứa URL (token).
            log.warning("zalo-bot: đích %s mang_loi (%s)", d_id, type(e).__name__)
            return {"ok": False, "ma": "mang_loi"}
        except Exception as e:
            log.warning("zalo-bot: đích %s loi_khong_ngu (%s)", d_id, type(e).__name__)
            return {"ok": False, "ma": "loi_khong_ngu"}
        return {"ok": True}

    @staticmethod
    def _phan_loai(r: httpx.Response) -> str | None:
        """Trả None khi gửi thành công; ngược lại trả mã lỗi ngắn."""
        if r.status_code == 429:
            return "het_han_muc"
        if r.status_code >= 500:
            return "may_chu_loi"
        try:
            than = r.json()
        except Exception:
            return "phan_hoi_sai"
        if isinstance(than, dict) and than.get("ok") is True:
            return None
        ma_so = than.get("error_code") if isinstance(than, dict) else None
        if ma_so == 410 or r.status_code == 410:
            return "chat_sai"
        if (isinstance(ma_so, int) and 400 <= ma_so < 500) or 400 <= r.status_code < 500:
            return "yeu_cau_sai"
        if isinstance(than, dict) and than.get("ok") is not True:
            return "phan_hoi_sai"
        return "loi_khong_ngu"
