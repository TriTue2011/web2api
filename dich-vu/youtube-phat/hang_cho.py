"""CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/hang_cho.py, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a.
Đổi so với bản gốc: thêm đầu tệp này; giữ nguyên.

Queue — bài phát kế tiếp, mỗi máy / mỗi loa một danh sách, lưu trong data dir.

Chủ máy 23/09/2026: trang YouTube của c2a cũng có Queue như thẻ Home Assistant
(thẻ 0.27.0). Cùng luật với thẻ (`custom_components/tritue_youtube_player/queue.py`
bên repo youtube): khoá `media_player.<loa tích đầu tiên>` hoặc `device:<mã máy>`,
hết bài thì bài kế lần lượt hoặc trộn trong các bài chưa phát. Kho RIÊNG của c2a:
phiên do trang c2a mở có controller "c2a" và do `tu_chuyen_bai` chuyển bài, còn
Queue của thẻ nằm trong .storage của Home Assistant.

Bài vào Queue đi qua `playlists.normalize_item` — MỘT bộ chuẩn hoá cho cả
playlist lẫn Queue, không viết khuôn mã thứ hai."""



from __future__ import annotations

import json
import random
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

try:
    from .playlists import normalize_item
except ImportError:  # chay truc tiep: python3 server.py
    from playlists import normalize_item

MAX_ITEMS = 200
MAX_QUEUES = 300
KHOA = re.compile(r"^(media_player\.[a-z0-9_]+|device:[a-z0-9-]{8,64})$")
UID = re.compile(r"^[a-f0-9]{8,32}$")
MODES = ("video", "audio")
ORDERS = ("sequential", "shuffle")


def rong() -> dict[str, Any]:
    return {"items": [], "current": None, "mode": "video", "order": "sequential", "played": []}


def khoa_hop_le(value: Any) -> str:
    key = str(value or "")
    if not KHOA.fullmatch(key):
        raise ValueError("invalid_queue_key")
    return key


def _muc(value: Any, uid: str | None = None) -> dict[str, Any] | None:
    item = normalize_item(value)
    if item is None:
        return None
    uid = uid or str((value or {}).get("uid") or "")
    return {**item, "uid": uid} if UID.fullmatch(uid) else None


def chuan_hoa(value: Any) -> dict[str, Any]:
    q = rong()
    if not isinstance(value, dict):
        return q
    seen: set[str] = set()
    for raw in value.get("items") or []:
        item = _muc(raw)
        if item and item["uid"] not in seen:
            seen.add(item["uid"])
            q["items"].append(item)
    q["items"] = q["items"][:MAX_ITEMS]
    uids = {i["uid"] for i in q["items"]}
    q["current"] = value.get("current") if value.get("current") in uids else None
    if value.get("mode") in MODES:
        q["mode"] = value["mode"]
    if value.get("order") in ORDERS:
        q["order"] = value["order"]
    q["played"] = [u for u in dict.fromkeys(value.get("played") or []) if u in uids]
    return q


def bai_ke(q: dict[str, Any], rng: random.Random | None = None) -> dict[str, Any] | None:
    """Bài sau `current`: kế tiếp theo thứ tự, hoặc ngẫu nhiên trong bài chưa phát."""
    items = q["items"]
    if not items:
        return None
    if q["order"] == "shuffle":
        da = set(q["played"]) | {q["current"]}
        con = [i for i in items if i["uid"] not in da]
        return (rng or random).choice(con) if con else None
    uids = [i["uid"] for i in items]
    vt = uids.index(q["current"]) + 1 if q["current"] in uids else 0
    return items[vt] if vt < len(items) else None


def bai_truoc(q: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Bài TRƯỚC `current` (nút lùi): lần lượt = bài đứng trước; trộn = bài vừa phát
    trước bài này. Cùng luật `pick_prev` của thẻ. None = đã ở đầu."""
    items = q["items"]
    uids = [i["uid"] for i in items]
    if q["current"] not in uids:
        return q, None
    if q["order"] == "shuffle":
        lich_su = [u for u in q["played"] if u in uids]
        if len(lich_su) < 2 or lich_su[-1] != q["current"]:
            return q, None
        truoc = lich_su[-2]
        return {**q, "current": truoc, "played": lich_su[:-1]}, items[uids.index(truoc)]
    vt = uids.index(q["current"]) - 1
    return (_chon(q, uids[vt]), items[vt]) if vt >= 0 else (q, None)


def _chon(q: dict[str, Any], uid: str) -> dict[str, Any]:
    return {**q, "current": uid, "played": [*[u for u in q["played"] if u != uid], uid]}


def thay_doi(q: dict[str, Any], payload: dict[str, Any], uid_moi: Callable[[], str]
             ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Áp một lệnh của trang; trả (Queue mới, bài cần phát hoặc None). ValueError = mã lỗi."""
    lenh = payload.get("action")
    if lenh == "add":
        raw = payload.get("items")
        if not isinstance(raw, list) or not raw:
            raise ValueError("invalid_items")
        them = [_muc(x, uid_moi()) for x in raw if isinstance(x, dict)]
        if not them or None in them:
            raise ValueError("invalid_items")
        if len(q["items"]) + len(them) > MAX_ITEMS:
            raise ValueError("queue_full")
        return {**q, "items": [*q["items"], *them]}, None
    if lenh == "remove":
        uid = payload.get("uid")
        uids = [i["uid"] for i in q["items"]]
        if uid not in uids:
            raise ValueError("item_not_found")
        current = q["current"]
        if current == uid:
            # Bài kế theo thứ tự vẫn là bài đứng sau bài vừa xoá.
            vt = uids.index(uid)
            current = uids[vt - 1] if vt > 0 else None
        return {**q, "items": [i for i in q["items"] if i["uid"] != uid], "current": current,
                "played": [u for u in q["played"] if u != uid]}, None
    if lenh == "clear":
        return {**q, "items": [], "current": None, "played": []}, None
    if lenh == "set":
        moi = dict(q)
        if "mode" in payload:
            if payload["mode"] not in MODES:
                raise ValueError("invalid_mode")
            moi["mode"] = payload["mode"]
        if "order" in payload:
            if payload["order"] not in ORDERS:
                raise ValueError("invalid_order")
            if payload["order"] != q["order"]:
                moi["played"] = [q["current"]] if q["current"] else []
            moi["order"] = payload["order"]
        return moi, None
    if lenh == "select":
        item = next((i for i in q["items"] if i["uid"] == payload.get("uid")), None)
        if item is None:
            raise ValueError("item_not_found")
        return _chon(q, item["uid"]), item
    if lenh == "next":
        item = bai_ke(q)
        return (_chon(q, item["uid"]), item) if item else (q, None)
    if lenh == "prev":
        return bai_truoc(q)
    raise ValueError("invalid_action")


class KhoHangCho:
    """File JSON; mỗi lần đổi ghi lại cả tệp qua tệp tạm (như PlaylistStore)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._doc: dict[str, dict[str, Any]] | None = None

    def _nap(self) -> dict[str, dict[str, Any]]:
        if self._doc is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            queues = data.get("queues") if isinstance(data, dict) else None
            self._doc = {k: chuan_hoa(v) for k, v in (queues or {}).items()
                         if isinstance(k, str) and KHOA.fullmatch(k)} if isinstance(queues, dict) else {}
        return self._doc

    def _luu(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tam = self.path.with_suffix(".tmp")
        tam.write_text(json.dumps({"queues": self._doc}, ensure_ascii=False), encoding="utf-8")
        tam.replace(self.path)

    def lay(self, key: Any) -> dict[str, Any]:
        key = khoa_hop_le(key)
        with self._lock:
            return json.loads(json.dumps(self._nap().get(key) or rong()))

    def co_bai_ke(self, key: str) -> bool:
        try:
            return bai_ke(self.lay(key)) is not None
        except ValueError:
            return False

    def doi(self, payload: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if not isinstance(payload, dict):
            raise ValueError("invalid_request")
        key = khoa_hop_le(payload.get("key"))
        with self._lock:
            doc = self._nap()
            cu = doc.get(key) or rong()
            moi, item = thay_doi(cu, payload, lambda: secrets.token_hex(8))
            if moi != cu:
                if moi["items"] or moi["mode"] != "video" or moi["order"] != "sequential":
                    doc[key] = moi
                else:
                    doc.pop(key, None)
                if len(doc) > MAX_QUEUES:
                    doc.pop(key, None)
                    raise ValueError("too_many_queues")
                self._luu()
            return json.loads(json.dumps(moi)), item

    def tiep(self, key: str) -> dict[str, Any] | None:
        """Chuyển Queue sang bài kế và trả bài đó (None = hết)."""
        _q, item = self.doi({"key": key, "action": "next"})
        return item


_kho: KhoHangCho | None = None


def kho() -> KhoHangCho:
    """Kho Queue của tiến trình, nằm cạnh playlists.json trong data dir của trình phát."""
    try:
        from . import dich_vu
    except ImportError:  # chay truc tiep: python3 server.py
        import dich_vu

    global _kho
    duong = dich_vu.core().data_dir / "queue.json"
    if _kho is None or _kho.path != duong:     # data dir đổi (test dựng core mới)
        _kho = KhoHangCho(duong)
    return _kho
