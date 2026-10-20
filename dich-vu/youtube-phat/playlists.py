"""CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/playlists.py, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a.
Đổi so với bản gốc: thêm đầu tệp này; giữ nguyên logic (TTPL1. mở được ở cả hai).

Playlists shared by the whole household, stored next to the player data.

Owner 14/09/2026: "thêm các bài hát yêu thích vào playlist để nghe hoặc nghe playlist
của người khác chia sẻ. Có thể tạo nhiều playlist khác nhau" — chung cả nhà; chia sẻ
bằng link playlist YouTube/album Zing và bằng mã chia sẻ của playlist tự tạo.

The same file lives in c2a (services/youtube_phat/playlists.py) and in the add-on
(youtube_player/app/playlists.py) and has no imports from either project, so a share
code made on one opens on the other. Share code: "TTPL1." + base64url(zlib(JSON))."""


from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit

MAX_PLAYLISTS = 100
MAX_ITEMS = 500
MAX_NAME = 80
SHARE_PREFIX = "TTPL1."
MAX_SHARE_CODE = 300_000
MAX_SHARE_JSON = 2_000_000
PLAYLIST_ID = re.compile(r"^[a-z0-9]{12}$")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
#: Mã video Facebook là một chuỗi SỐ — khác hẳn khuôn 11 ký tự của YouTube, nên mỗi
#: nguồn một khuôn riêng thay vì một khuôn lỏng cho cả hai.
FACEBOOK_ID = re.compile(r"^[0-9]{5,25}$")
ZING_SONG_ID = re.compile(r"^[A-Za-z0-9]{8,16}$")


class PlaylistError(ValueError):
    """Invalid request; the message is a stable error code."""


def _text(value, maximum):
    return " ".join(str(value or "").split())[:maximum]


def _https_url(value):
    url = str(value or "").strip()[:2048]
    parsed = urlsplit(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def normalize_item(item):
    """Bounded song entry, or None when it can't be played later."""
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or "").strip().lower()
    item_id = str(item.get("id") or "").strip()
    url = str(item.get("url") or "").strip()
    if source == "youtube":
        if not VIDEO_ID.fullmatch(item_id):
            return None
        url = f"https://www.youtube.com/watch?v={item_id}"
        kind = "video"
    elif source == "zing":
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        song_id = parsed.path.rsplit("/", 1)[-1].removesuffix(".html")
        if (
            parsed.scheme != "https"
            or not (host == "zingmp3.vn" or host.endswith(".zingmp3.vn"))
            or not parsed.path.startswith("/bai-hat/")
            or not ZING_SONG_ID.fullmatch(song_id)
        ):
            return None
        item_id = song_id
        url = f"https://zingmp3.vn{parsed.path}"
        kind = "song"
    elif source == "facebook":
        # Thiếu nhánh này thì thêm bài Facebook vào playlist trả None IM LẶNG —
        # chủ máy báo 20/09/2026: "ghim video face được nhưng thêm playlist không
        # được". Ghim đi đường khác (kho gợi ý của tích hợp) nên nó chạy, còn
        # playlist đi qua đây nên rơi.
        if not FACEBOOK_ID.fullmatch(item_id):
            return None
        url = f"https://www.facebook.com/watch/?v={item_id}"
        kind = "video"
    elif source == "http":
        url = _https_url(url or item_id)
        if not url:
            return None
        item_id = url
        kind = "audio"
    else:
        return None
    try:
        duration = max(0, int(float(item.get("duration") or 0)))
    except (TypeError, ValueError, OverflowError):
        duration = 0
    return {
        "source": source,
        "kind": kind,
        "id": item_id,
        "url": url,
        "title": _text(item.get("title") or item_id, 300),
        "channel": _text(item.get("channel") or item.get("artist"), 200),
        "duration": duration,
        "thumbnail": _https_url(item.get("thumbnail")),
    }


def _key(item):
    return (item["source"], item["id"])


def share_code(playlist):
    """Compact code another c2a or Home Assistant player can import."""
    compact = {
        "n": playlist["name"],
        "i": [[it["source"], it["id"], it["url"], it["title"], it["channel"], it["duration"], it["thumbnail"]]
              for it in playlist["items"]],
    }
    raw = zlib.compress(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    return SHARE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def is_share_code(text):
    return str(text or "").strip().startswith(SHARE_PREFIX)


def read_share_code(code):
    """(name, items) from a share code; PlaylistError("invalid_share_code") otherwise."""
    code = "".join(str(code or "").split())
    if not code.startswith(SHARE_PREFIX) or len(code) > MAX_SHARE_CODE:
        raise PlaylistError("invalid_share_code")
    body = code[len(SHARE_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        decompressor = zlib.decompressobj()
        text = decompressor.decompress(raw, MAX_SHARE_JSON)
        if decompressor.unconsumed_tail:
            raise PlaylistError("invalid_share_code")
        data = json.loads(text)
    except (ValueError, zlib.error) as error:
        raise PlaylistError("invalid_share_code") from error
    if not isinstance(data, dict) or not isinstance(data.get("i"), list):
        raise PlaylistError("invalid_share_code")
    keys = ("source", "id", "url", "title", "channel", "duration", "thumbnail")
    items = [normalize_item(dict(zip(keys, row))) for row in data["i"][:MAX_ITEMS] if isinstance(row, list)]
    return _text(data.get("n"), MAX_NAME), [item for item in items if item]


class PlaylistStore:
    """JSON-file store; every change rewrites the file atomically."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._playlists = None

    def _load(self):
        if self._playlists is not None:
            return self._playlists
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        playlists = []
        for entry in (data.get("playlists") if isinstance(data, dict) else None) or []:
            if not isinstance(entry, dict) or not PLAYLIST_ID.fullmatch(str(entry.get("id") or "")):
                continue
            items = [normalize_item(item) for item in entry.get("items") or []]
            playlists.append({
                "id": entry["id"],
                "name": _text(entry.get("name"), MAX_NAME) or "Playlist",
                "created_at": int(entry.get("created_at") or 0),
                "updated_at": int(entry.get("updated_at") or 0),
                "source_url": _https_url(entry.get("source_url")),
                "items": [item for item in items if item][:MAX_ITEMS],
            })
        self._playlists = playlists[:MAX_PLAYLISTS]
        return self._playlists

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"playlists": self._playlists}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def _find(self, playlist_id):
        playlist = next((p for p in self._load() if p["id"] == str(playlist_id or "")), None)
        if playlist is None:
            raise PlaylistError("playlist_not_found")
        return playlist

    @staticmethod
    def _copy(playlist):
        return {**playlist, "items": [dict(item) for item in playlist["items"]]}

    def list(self):
        with self._lock:
            return [self._copy(p) for p in self._load()]

    def get(self, playlist_id):
        with self._lock:
            return self._copy(self._find(playlist_id))

    def create(self, name, items=(), *, source_url=""):
        name = _text(name, MAX_NAME)
        if not name:
            raise PlaylistError("playlist_name_required")
        with self._lock:
            playlists = self._load()
            if len(playlists) >= MAX_PLAYLISTS:
                raise PlaylistError("too_many_playlists")
            now = int(time.time())
            clean, seen = [], set()
            for item in items:
                item = normalize_item(item)
                if item and _key(item) not in seen and len(clean) < MAX_ITEMS:
                    seen.add(_key(item))
                    clean.append(item)
            playlist = {"id": secrets.token_hex(6), "name": name, "created_at": now, "updated_at": now,
                        "source_url": _https_url(source_url), "items": clean}
            playlists.append(playlist)
            self._save()
            return self._copy(playlist)

    def rename(self, playlist_id, name):
        name = _text(name, MAX_NAME)
        if not name:
            raise PlaylistError("playlist_name_required")
        with self._lock:
            playlist = self._find(playlist_id)
            playlist["name"], playlist["updated_at"] = name, int(time.time())
            self._save()
            return self._copy(playlist)

    def delete(self, playlist_id):
        with self._lock:
            playlist = self._find(playlist_id)
            self._playlists.remove(playlist)
            self._save()

    def add(self, playlist_id, items):
        """Append songs not already in the playlist; returns (playlist, added count)."""
        with self._lock:
            playlist = self._find(playlist_id)
            seen = {_key(item) for item in playlist["items"]}
            added = 0
            for item in items:
                item = normalize_item(item)
                if not item or _key(item) in seen:
                    continue
                if len(playlist["items"]) >= MAX_ITEMS:
                    raise PlaylistError("playlist_full")
                seen.add(_key(item))
                playlist["items"].append(item)
                added += 1
            if added:
                playlist["updated_at"] = int(time.time())
                self._save()
            return self._copy(playlist), added

    def remove(self, playlist_id, index):
        with self._lock:
            playlist = self._find(playlist_id)
            if not isinstance(index, int) or not 0 <= index < len(playlist["items"]):
                raise PlaylistError("invalid_playlist_index")
            del playlist["items"][index]
            playlist["updated_at"] = int(time.time())
            self._save()
            return self._copy(playlist)

    def move(self, playlist_id, index, to):
        with self._lock:
            playlist = self._find(playlist_id)
            items = playlist["items"]
            if not all(isinstance(v, int) and 0 <= v < len(items) for v in (index, to)):
                raise PlaylistError("invalid_playlist_index")
            items.insert(to, items.pop(index))
            playlist["updated_at"] = int(time.time())
            self._save()
            return self._copy(playlist)

    def contains(self, source, target):
        """Whether a song (by id or url) is in any playlist — saved Zing songs stay playable."""
        target = str(target or "")
        with self._lock:
            return any(item["source"] == source and target in {item["id"], item["url"]}
                       for playlist in self._load() for item in playlist["items"])
