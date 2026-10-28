"""Lõi trình phát — phần `PlayerServer` của add-on, bỏ lớp HTTP.

CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/dich_vu.py, commit d879b03 — CHỈ ĐỌC nguồn.

Đổi so với bản gốc:
- Bỏ `from services.config import DATA_DIR`: `PlayerCore.__init__(self, thu_muc_du_lieu: str, lich_su_toi_da: int = 20)`; dữ liệu nằm ở `<thu_muc_du_lieu>/youtube_phat/` (playlists.json, history.json, integration_token, hmac_key, cai_dat.json, zing_keys*.json).
- Bỏ `from utils.log import logger` → `logging.getLogger("youtube_phat")` (stdlib).
- `resolve_integration_token` chuyển sang `cau_hinh.giai_quyet_token_tich_hop` (giữ nguyên logic: tệp integration_token 0600, chưa có thì `secrets.token_urlsafe(32)`, ghi qua .tmp + chmod 0600 + rename nguyên tử).
- Bỏ `chuan_hoa_url_lan`/`doc_url_lan`/`luu_url_lan` (đọc `cai_dat.json` "url_lan"); thay bằng `public_base_url_mac_dinh()` đọc YT_PUBLISH_IP + YT_PORT → "http://<ip>:<port>", None nếu không đặt (server.py sẽ dùng Host của request).
- Singleton `core()` → `tao_core(thu_muc_du_lieu: str)` factory + cache module `_CORE` + `_dat_lai_cho_test()` xoá cache (cho test). Giữ lại tên `core()` làm bí danh tương thích vì `hang_cho.kho()` (chép nguyên, không được sửa) vẫn gọi `dich_vu.core()` — nó trả core trên thư mục dữ liệu mặc định (YT_DATA_DIR).
- Docstring bỏ các nhắc "HA/c2a" nói về hành vi: dịch vụ mới KHÔNG gọi HA trực tiếp (đã soát: `play()` và toàn bộ lõi không hề gọi HA); chiều ngược lại — HA/tích hợp ngoài gọi dịch vụ này qua API.
- Giữ nguyên mọi tên hàm/phương thức/mã lỗi còn lại và toàn bộ logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

try:
    from . import gioi_han
except ImportError:  # chạy trực tiếp: python3 server.py
    import gioi_han
try:
    from .cau_hinh import CONG_MAC_DINH, giai_quyet_token_tich_hop, thu_muc_dich_vu
except ImportError:  # chay truc tiep: python3 server.py
    from cau_hinh import CONG_MAC_DINH, giai_quyet_token_tich_hop, thu_muc_dich_vu
try:
    from .playlists import PlaylistError, PlaylistStore, is_share_code, read_share_code, share_code
except ImportError:  # chay truc tiep: python3 server.py
    from playlists import PlaylistError, PlaylistStore, is_share_code, read_share_code, share_code
try:
    from .search import (
        fetch_youtube_playlist,
        search_facebook,
        search_youtube,
        search_zing,
        youtube_playlist_id,
    )
except ImportError:  # chay truc tiep: python3 server.py
    from search import (
        fetch_youtube_playlist,
        search_facebook,
        search_youtube,
        search_zing,
        youtube_playlist_id,
    )
try:
    from .session import PlaybackSession
except ImportError:  # chay truc tiep: python3 server.py
    from session import PlaybackSession
try:
    from .streaming import (
        StreamUnavailableError,
        build_signed_stream_url,
        fetch_zing_playlist,
        fetch_zing_web_keys,
        resolve_facebook_audio,
        resolve_facebook_video,
        resolve_youtube_audio,
        resolve_youtube_video,
        resolve_zing_stream,
        stream_cache_seconds,
        validate_stream_target,
        validate_zing_target,
        youtube_video_target,
        zing_playlist_id,
    )
except ImportError:  # chay truc tiep: python3 server.py
    from streaming import (
        StreamUnavailableError,
        build_signed_stream_url,
        fetch_zing_playlist,
        fetch_zing_web_keys,
        resolve_facebook_audio,
        resolve_facebook_video,
        resolve_youtube_audio,
        resolve_youtube_video,
        resolve_zing_stream,
        stream_cache_seconds,
        validate_stream_target,
        validate_zing_target,
        youtube_video_target,
        zing_playlist_id,
    )

logger = logging.getLogger("youtube_phat")

# Tham số của khung nhúng — GIỮ KHỚP với `youtube_player/app/server.py` bên repo
# add-on. Hai bản song song trôi khỏi nhau đúng một lần trong ngày 20/09/2026 (bài
# Facebook thêm được ở bản này mà rơi im lặng ở bản kia), nên từ đây thứ gì sửa ở
# một bên thì soi sang bên kia ngay.
#   enablejsapi=1  — mở đường postMessage để TRANG biết khung báo lỗi; thiếu nó thì
#                    YouTube tự vẽ hộp lỗi và giao diện không hay biết gì.
#   playsinline=1  — điện thoại phát ngay trong trang thay vì bung toàn màn.
#   rel=0          — hết bài không gợi ý video của kênh khác.
# Riêng `origin` KHÔNG đặt ở đây: chỉ trình duyệt mới biết trang mở bằng địa chỉ
# nào.
EMBED_PARAMS = "enablejsapi=1&playsinline=1&rel=0"

OEMBED_UA = "Mozilla/5.0 (X11; Linux x86_64) c2a-youtube-phat"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
APP_VERSION = "0.6.2-c2a"
API_VERSION = "1"
MAX_HISTORY = 20


FACEBOOK_ID = re.compile(r"^[0-9]{5,25}$")
FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
}


def normalize_facebook_target(raw_target):
    """Mã video Facebook, từ chính mã số hoặc từ một link người dùng dán.

    Nhận /reel/<mã>, /watch/?v=<mã>, /<trang>/videos/<tên bài>/<mã>/. KHÔNG nhận link
    chia sẻ /share/v/<mã ngắn> — mã trong đó là mã BÀI VIẾT, không phải mã video, và
    bộ bóc luồng không đọc được dạng ấy. Việc lần từ link chia sẻ ra mã video do
    `search.resolve_facebook_share` lo, ở tầng tìm kiếm."""
    target = str(raw_target or "").strip()
    if FACEBOOK_ID.fullmatch(target):
        return target
    parsed = urlsplit(target if "//" in target else f"https://{target}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in FACEBOOK_HOSTS:
        raise ValueError("invalid_facebook_target")
    phan = [doan for doan in parsed.path.split("/") if doan]
    video_id = ""
    if parsed.path.rstrip("/").endswith("/watch") or parsed.path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif phan and phan[0] == "reel" and len(phan) > 1:
        video_id = phan[1]
    elif "videos" in phan:
        # Quét tìm đoạn LÀ CHUỖI SỐ: Facebook chèn tên bài vào giữa "videos" và mã.
        sau = phan[phan.index("videos") + 1 :]
        video_id = next((doan for doan in sau if FACEBOOK_ID.fullmatch(doan)), "")
    if not FACEBOOK_ID.fullmatch(video_id):
        raise ValueError("invalid_facebook_target")
    return video_id


def facebook_video_target(video_id, max_height):
    """Đích cho phần HÌNH: "<mã>:<chiều cao>". Facebook chỉ có hai bậc gộp sẵn
    (hd/sd) nên chiều cao ở đây chỉ dùng để chọn giữa hai bậc ấy."""
    try:
        height = int(max_height or 0)
    except (TypeError, ValueError):
        height = 0
    return f"{video_id}:{720 if height >= 720 or height == 0 else 480}"


def normalize_target(raw_target):
    target = str(raw_target or "").strip()
    video_id = target if VIDEO_ID.fullmatch(target) else None
    playlist_id = None

    if video_id is None:
        parsed = urlsplit(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
            raise ValueError("invalid_youtube_target")
        if host == "youtu.be":
            video_id = parsed.path.strip("/").split("/", 1)[0]
        elif parsed.path == "/watch":
            query = parse_qs(parsed.query)
            video_id = query.get("v", [""])[0]
            playlist_id = query.get("list", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        elif parsed.path == "/playlist":
            playlist_id = parse_qs(parsed.query).get("list", [""])[0]

    if VIDEO_ID.fullmatch(video_id or ""):
        target = {
            "kind": "video",
            "id": video_id,
            "embed_url": (
                f"https://www.youtube-nocookie.com/embed/{video_id}"
                f"?autoplay=1&{EMBED_PARAMS}"
            ),
        }
        if PLAYLIST_ID.fullmatch(playlist_id or ""):
            target["playlist_id"] = playlist_id
            target["embed_url"] = (
                f"https://www.youtube-nocookie.com/embed/{video_id}"
                f"?list={playlist_id}&autoplay=1&{EMBED_PARAMS}"
            )
        return target

    if PLAYLIST_ID.fullmatch(playlist_id or ""):
        return {
            "kind": "playlist",
            "id": playlist_id,
            "embed_url": (
                "https://www.youtube-nocookie.com/embed/videoseries"
                f"?list={playlist_id}&autoplay=1&{EMBED_PARAMS}"
            ),
        }

    raise ValueError("invalid_youtube_target")


# Tên bài của một mã video, nhớ suốt phiên chạy: tên không đổi, mà mỗi lượt hỏi tốn
# một lượt HTTP.
_TEN_YOUTUBE: dict[str, dict[str, str]] = {}
_TEN_YOUTUBE_TOI_DA = 512


def ten_bai_youtube(video_id, *, timeout=4):
    """Tên bài, tên kênh và ảnh bìa của một mã video — hỏi thẳng YouTube bằng oEmbed.

    VÌ SAO CẦN: phiên phát chỉ biết tên bài khi bài ấy nằm trong một danh sách mà
    người dùng vừa tìm (hàng đợi phiên, playlist, kết quả tìm). Bấm tích loa cho bài
    đang nghe thì thẻ chỉ gửi MỘT MÃ VIDEO, không nằm trong danh sách nào, nên phiên
    lấy luôn mã làm tên — chủ máy thấy "dTeH03NpB1A" hiện ra thay cho tên bài (ảnh
    chụp 21/09/2026), và ảnh bìa cũng trống.

    Không biết tên thì ĐI HỎI NGUỒN, đừng lấy thứ sẵn có trong tay làm tên. oEmbed là
    đường rẻ nhất: một lượt HTTP công khai, không khoá, không cần yt-dlp; hỏng thì
    trả về rỗng và mọi thứ giữ nguyên như cũ.
    """
    ma = str(video_id or "").strip()
    if not VIDEO_ID.fullmatch(ma):
        return {}
    if ma in _TEN_YOUTUBE:
        return dict(_TEN_YOUTUBE[ma])
    dia_chi = "https://www.youtube.com/oembed?format=json&url=" + quote(
        f"https://www.youtube.com/watch?v={ma}", safe=""
    )
    try:
        with urlopen(Request(dia_chi, headers={"User-Agent": OEMBED_UA}), timeout=timeout) as tra:
            du_lieu = json.loads(tra.read(65536).decode("utf-8", "replace"))
    except (OSError, ValueError) as loi:
        logger.info(json.dumps({"event": "youtube_oembed_hong", "ma": ma, "loi": str(loi)[:120]}))
        return {}
    if not isinstance(du_lieu, dict):
        return {}
    ten = {
        "title": str(du_lieu.get("title") or "").strip(),
        "artist": str(du_lieu.get("author_name") or "").strip(),
        "thumbnail": str(du_lieu.get("thumbnail_url") or "").strip(),
        "url": f"https://www.youtube.com/watch?v={ma}",
    }
    if not ten["title"]:
        return {}
    if len(_TEN_YOUTUBE) >= _TEN_YOUTUBE_TOI_DA:
        _TEN_YOUTUBE.pop(next(iter(_TEN_YOUTUBE)))
    _TEN_YOUTUBE[ma] = ten
    return dict(ten)


class PlayerCore:
    def __init__(self, thu_muc_du_lieu: str, lich_su_toi_da: int = MAX_HISTORY):
        # Thư mục dữ liệu của dịch vụ: <thu_muc_du_lieu>/youtube_phat/
        self.data_dir = thu_muc_dich_vu(thu_muc_du_lieu)
        self.max_history = lich_su_toi_da
        self.integration_token = giai_quyet_token_tich_hop(self.data_dir)
        self.history_lock = threading.Lock()
        self.player_lock = threading.Lock()
        self.search_lock = threading.Lock()
        self.zing_result_lock = threading.Lock()
        self.stream_lock = threading.Lock()
        self.zing_result_cache: dict[str, float] = {}
        self.stream_cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self.prefetching: set[tuple[str, str]] = set()
        self.prefetch_streams = True
        #: Khoá TUẦN TỰ cho bài kế/bài trước/tua (NV153 đợt D4). Ý tưởng từ thẻ
        #: HA trong kho youtube 0.27.12 (thẻ giữ khoá quanh thao tác tự chuyển
        #: bài để bấm liên tiếp không sinh phiên đôi; kho youtube CHỈ ĐỌC, lấy
        #: ý chứ không chép mã): mọi lần chuyển bài/tua giữ khoá này từ lúc
        #: kiểm trạng thái tới lúc xong — bấm liên tiếp 5 lần = đúng 5 lần
        #: chuyển, không bỏ sót, không sinh hai phiên. Riêng với player_lock
        #: (dùng ở play/stop/record_session): đây là lock độc lập cho riêng ba
        #: thao tác chuyển/tua để đột biến «bỏ khoá» có đường đỏ rõ ràng.
        self._khoa_chuyen_bai = threading.Lock()
        self.playback_session = PlaybackSession()
        self.playlists = PlaylistStore(self.data_dir / "playlists.json")

    @property
    def history_path(self):
        return self.data_dir / "history.json"

    def load_history(self):
        with self.history_lock:
            if not self.history_path.exists():
                return []
            try:
                value = json.loads(self.history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
            return value if isinstance(value, list) else []

    def add_history(self, target):
        with self.history_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            history = []
            if self.history_path.exists():
                try:
                    value = json.loads(self.history_path.read_text(encoding="utf-8"))
                    history = value if isinstance(value, list) else []
                except (OSError, json.JSONDecodeError):
                    history = []
            history = [target] + [item for item in history if item != target]
            history = history[: self.max_history]
            temporary_path = self.history_path.with_suffix(".json.tmp")
            temporary_path.write_text(
                json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary_path.replace(self.history_path)

    def get_session(self):
        with self.player_lock:
            return self.playback_session.snapshot()

    def get_sessions(self):
        with self.player_lock:
            return self.playback_session.snapshot(), self.playback_session.snapshots()

    def set_session_outputs(self, session_id, output_entity_ids):
        with self.player_lock:
            return self.playback_session.set_outputs(session_id, output_entity_ids)

    def play(self, target, *, raw_target=""):
        """Bắt đầu phát một bài YouTube đã chuẩn hoá — chỉ đổi trạng thái phiên.

        ĐÃ SOÁT (đợt A NV153): lõi KHÔNG gọi Home Assistant trực tiếp ở bất kỳ
        đường nào; `play()` chỉ start phiên trong RAM, ghi lịch sử và trả session.
        Việc điều khiển loa thật là trách nhiệm của tầng gọi API (server.py) hoặc
        của tích hợp HA phía ngoài gọi vào dịch vụ này — dịch vụ không gọi ngược.
        """
        with self.player_lock:
            session = self.playback_session.start(
                "youtube", raw_target or target.get("id"), fallback_item=target
            )
        self.add_history(session["item"])
        return session

    def record_session(
        self,
        source,
        target,
        *,
        output_entity_ids,
        media_content_type="",
        volume_level=None,
        session_id=None,
        controller="",
        auto_advance=True,
        playlist_id=None,
    ):
        queue_items = self.playlists.get(playlist_id)["items"] if playlist_id else None
        if source == "youtube":
            fallback = normalize_target(target)
            # Không nằm trong danh sách nào thì tên bài sẽ rơi về chính mã video —
            # đi hỏi YouTube một lượt, xem «ten_bai_youtube».
            if fallback.get("kind") == "video" and not fallback.get("title"):
                fallback.update(ten_bai_youtube(fallback.get("id", "")))
        elif source == "zing":
            target = self.require_public_zing_result(target)
            fallback = {"source": "zing", "kind": "song", "id": target, "url": target}
        elif source == "facebook":
            # Thiếu nhánh này thì nghe trên máy chạy còn PHÁT RA LOA hỏng: đường ra loa
            # đi qua đây, đường nghe trên máy thì không.
            video_id = normalize_facebook_target(target)
            target = video_id
            fallback = {
                "source": "facebook",
                "kind": "video",
                "id": video_id,
                "url": f"https://www.facebook.com/watch/?v={video_id}",
            }
        elif source == "http":
            fallback = None
        else:
            raise ValueError("unsupported_session_source")
        with self.player_lock:
            session = self.playback_session.start(
                source,
                target,
                fallback_item=fallback,
                output_entity_ids=output_entity_ids,
                media_content_type=media_content_type,
                volume_level=volume_level,
                session_id=session_id,
                controller=controller,
                auto_advance=auto_advance,
                queue_items=queue_items,
            )
        self.add_history(session["item"])
        self.prefetch_next(session)
        return session

    def prefetch_next(self, session):
        """Giải sẵn luồng bài kế của nhóm loa trong nền, để bấm bài kế và tự
        chuyển bài bắt đầu ngay thay vì chờ yt-dlp."""
        queue = (session or {}).get("queue") or {}
        items = queue.get("items") or []
        index = int(queue.get("index", -1)) + 1
        if not self.prefetch_streams or not session.get("output_entity_ids") or not 0 < index < len(items):
            return
        self.prefetch(items[index])

    def prefetch(self, item):
        """Giải sẵn luồng của một bài (trong nền); bài không giải được thì bỏ qua."""
        item = item if isinstance(item, dict) else {}
        source = item.get("source")
        target = item.get("url") or item.get("id")
        if not self.prefetch_streams or source not in {"youtube", "zing", "facebook"} or item.get("kind") not in {"video", "song"} or not target:
            return
        key = (source, target)
        with self.stream_lock:
            if key in self.prefetching:
                return
            self.prefetching.add(key)

        def run():
            try:
                self.prepare_stream(source, target)
            except (ValueError, StreamUnavailableError, OSError):
                pass  # giải sẵn hỏng chỉ có nghĩa bài kế sẽ giải lúc bấm
            finally:
                with self.stream_lock:
                    self.prefetching.discard(key)

        threading.Thread(target=run, name="youtube-phat-giai-san", daemon=True).start()

    def stop(self, expected_revision=None, session_id=None):
        with self.player_lock:
            return self.playback_session.stop(expected_revision, session_id)

    def bai_ke(self, session_id=None):
        """Bài kế trong hàng đợi của phiên (NV153 đợt D4).

        Giữ `_khoa_chuyen_bai` suốt lần chuyển: bấm liên tiếp không sinh hai
        phiên, không bỏ sót bài. «Dừng là dừng hẳn» được ưu tiên: phiên đã
        dừng → ValueError("session_not_found") (route dịch 404), KHÔNG hồi
        sinh phiên / phát ma. Hết hàng đợi → ValueError("het_hang_doi").
        """
        with self._khoa_chuyen_bai:
            with self.player_lock:
                ss = self.playback_session.advance(1, session_id)
        self.prefetch_next(ss)
        return ss

    def bai_truoc(self, session_id=None):
        """Bài trước trong hàng đợi của phiên (NV153 đợt D4)."""
        with self._khoa_chuyen_bai:
            with self.player_lock:
                ss = self.playback_session.advance(-1, session_id)
        self.prefetch_next(ss)
        return ss

    def tua(self, session_id, giay):
        """Đặt vị trí phát của phiên (NV153 đợt D4) — chỉ trạng thái.

        Byte thật do `GET /api/stream/{token}?t=<giây>` tính từ mục lục
        sidx/tỉ lệ thời lượng, đồng bộ với rơ-le `ro_le` (rơ-le mở lại từ
        `dau` đã tính). Giây không hợp lệ → ValueError("invalid_seek_position"),
        phiên không còn → ValueError("session_not_found").
        """
        with self._khoa_chuyen_bai:
            with self.player_lock:
                return self.playback_session.dat_vi_tri(giay, session_id)

    def playlist_action(self, payload):
        """Một lệnh playlist (từ giao diện web hoặc tích hợp ngoài). Trả {"playlists": [...], ...}.

        list · create {name, items} · rename {id, name} · delete {id} · add {id | name, items}
        · remove {id, index} · move {id, index, to} · export {id} → code · import {text, name}
        (link playlist YouTube, link album/playlist Zing MP3, hoặc mã chia sẻ)."""
        payload = payload if isinstance(payload, dict) else {}
        action = str(payload.get("action") or "list")
        store = self.playlists
        extra = {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        if action == "list":
            pass
        elif action == "create":
            extra["playlist"] = store.create(payload.get("name"), items)
        elif action == "rename":
            extra["playlist"] = store.rename(payload.get("id"), payload.get("name"))
        elif action == "delete":
            store.delete(payload.get("id"))
        elif action == "add":
            playlist_id = payload.get("id") or store.create(payload.get("name"))["id"]
            extra["playlist"], extra["added"] = store.add(playlist_id, items)
        elif action == "remove":
            extra["playlist"] = store.remove(payload.get("id"), payload.get("index"))
        elif action == "move":
            extra["playlist"] = store.move(payload.get("id"), payload.get("index"), payload.get("to"))
        elif action == "export":
            extra["code"] = share_code(store.get(payload.get("id")))
        elif action == "import":
            name, found, source_url = self._read_playlist_source(str(payload.get("text") or ""))
            if not found:
                raise PlaylistError("playlist_empty")
            extra["playlist"] = store.create(str(payload.get("name") or "").strip() or name, found, source_url=source_url)
        else:
            raise PlaylistError("invalid_playlist_action")
        return {"playlists": store.list(), **extra}

    def _read_playlist_source(self, text):
        text = text.strip()
        if is_share_code(text):
            name, found = read_share_code(text)
            return name or "Playlist chia sẻ", found, ""
        if youtube_playlist_id(text):
            name, found = fetch_youtube_playlist(text)
            return name, found, text
        if zing_playlist_id(text):
            keys = self.zing_keys()
            try:
                name, found = fetch_zing_playlist(text, **keys)
            except StreamUnavailableError:
                name, found = fetch_zing_playlist(text, **self.zing_keys(lam_moi=True))
            return name, found, text
        raise PlaylistError("invalid_playlist_link")

    def search(self, source, query, limit):
        """Run one metadata search at a time to bound child processes."""
        with self.search_lock:
            if source == "youtube":
                results = search_youtube(query, limit=limit)
                with self.player_lock:
                    self.playback_session.remember_search(source, results)
                return results
            if source == "zing":
                results = search_zing(query, limit=limit)
                self.remember_public_zing_results(results)
                with self.player_lock:
                    self.playback_session.remember_search(source, results)
                return results
            if source == "facebook":
                # Chỉ tra cứu được bằng LINK DÁN VÀO (kể cả link chia sẻ);
                # `search_facebook` tự ném `invalid_search_query` nếu đưa vào chữ.
                results = search_facebook(query, limit=limit)
                with self.player_lock:
                    self.playback_session.remember_search(source, results)
                return results
            raise ValueError("invalid_search_source")

    def remember_public_zing_results(self, results, *, ttl=3600):
        """Temporarily authorize Zing URLs that passed public search filters."""
        now = time.monotonic()
        with self.zing_result_lock:
            self.zing_result_cache = gioi_han.cat_bo_nho_dem({
                url: expiry
                for url, expiry in self.zing_result_cache.items()
                if expiry > now
            })
            for item in results:
                if (
                    not isinstance(item, dict)
                    or item.get("source") != "zing"
                    or item.get("kind") != "song"
                ):
                    continue
                try:
                    target_url = validate_zing_target(item.get("url"))
                except ValueError:
                    continue
                self.zing_result_cache[target_url] = now + int(ttl)

    def require_public_zing_result(self, target_url):
        """Accept only a Zing URL recently returned by public search (or saved in a playlist)."""
        target_url = validate_zing_target(target_url)
        if self.playlists.contains("zing", target_url):
            return target_url
        now = time.monotonic()
        with self.zing_result_lock:
            expiry = self.zing_result_cache.get(target_url, 0)
            if expiry <= now:
                self.zing_result_cache.pop(target_url, None)
                raise ValueError("unverified_zing_target")
        return target_url

    def create_stream_url(self, source, target, public_base_url):
        """Create a signed LAN URL a speaker can fetch without logging in."""
        if not public_base_url:
            raise ValueError("public_base_url_required")
        return build_signed_stream_url(
            public_base_url,
            target,
            self.integration_token,
            source=source,
            ttl=3600,
        )

    def prepare_stream(self, source, target, max_height=720):
        """Authorize, resolve and briefly cache one stream before a speaker uses it.

        `youtube_video` = the picture only (no sound) for a browser, up to `max_height`."""
        if source == "zing":
            target = self.require_public_zing_result(target)
        elif source in {"youtube", "youtube_video"}:
            normalized = normalize_target(target)
            if normalized.get("kind") != "video" or not normalized.get("id"):
                raise ValueError("youtube_audio_requires_video")
            target = normalized["id"] if source == "youtube" else youtube_video_target(normalized["id"], max_height)
        elif source in {"facebook", "facebook_video"}:
            video_id = normalize_facebook_target(target)
            target = video_id if source == "facebook" else facebook_video_target(video_id, max_height)
        else:
            raise ValueError("unsupported_stream_source")
        return target, self._resolve_stream(source, target)

    ZING_KEYS_TTL = 24 * 3600

    def _doc_khoa(self, ten):
        try:
            value = json.loads((self.data_dir / ten).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not value.get("api_key") or not value.get("api_secret"):
            return None
        return value

    def zing_keys(self, *, lam_moi=False):
        """apiKey/secret của web zingmp3.vn — công khai trong mã trang, không phải
        khoá riêng, nhưng không để trong git (gitleaks chặn).

        Thứ tự: `zing_keys.json` do chủ máy đặt tay (nếu có) → bản tự lấy lưu đệm
        24 giờ (`zing_keys_web.json`) → tải lại từ zingmp3.vn. Tải hỏng thì dùng
        bản đệm cũ nếu còn; không có gì thì chỉ Zing không phát được."""
        tay = self._doc_khoa("zing_keys.json")
        if tay:
            return {"api_key": str(tay["api_key"]), "api_secret": str(tay["api_secret"])}
        dem = self._doc_khoa("zing_keys_web.json")
        if dem and not lam_moi and time.time() - float(dem.get("luc") or 0) < self.ZING_KEYS_TTL:
            return {"api_key": str(dem["api_key"]), "api_secret": str(dem["api_secret"])}
        try:
            keys = fetch_zing_web_keys()
        except StreamUnavailableError as error:
            logger.warning({"event": "youtube_phat_khong_lay_duoc_khoa_zing", "loi": str(error),
                            "dung_ban_dem_cu": bool(dem)})
            if dem:
                return {"api_key": str(dem["api_key"]), "api_secret": str(dem["api_secret"])}
            raise
        path = self.data_dir / "zing_keys_web.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tam = path.with_suffix(".json.tmp")
        tam.write_text(json.dumps({**keys, "luc": time.time()}), encoding="utf-8")
        tam.replace(path)
        return keys

    def _resolve_stream(self, source, target):
        """Resolve and briefly cache one already validated audio stream."""
        key = (source, target)
        with self.stream_lock:
            cached = self.stream_cache.get(key)
            if cached and cached[0] >= time.monotonic():
                return dict(cached[1])
        # Hai nhánh Facebook PHẢI đứng trước `else`: nhánh cuối là đường Zing (kèm cơ
        # chế lấy lại khoá rồi thử lại), nên thiếu chúng thì đích Facebook bị đem đi
        # giải bằng bộ giải Zing và hỏng ở một chỗ chẳng liên quan gì.
        if source == "youtube":
            resolved = resolve_youtube_audio(target)
        elif source == "youtube_video":
            resolved = resolve_youtube_video(target)
        elif source == "facebook":
            resolved = resolve_facebook_audio(target)
        elif source == "facebook_video":
            resolved = resolve_facebook_video(target)
        else:
            try:
                resolved = resolve_zing_stream(target, **self.zing_keys())
            except StreamUnavailableError as error:
                if str(error) != "stream_provider_failed":
                    raise
                # Zing có thể đã đổi khoá trong mã web: lấy lại một lần rồi thử lại.
                resolved = resolve_zing_stream(target, **self.zing_keys(lam_moi=True))
        with self.stream_lock:
            now = time.monotonic()
            self.stream_cache = {
                cached_key: value
                for cached_key, value in self.stream_cache.items()
                if value[0] >= now
            }
            self.stream_cache[key] = (now + stream_cache_seconds(resolved.get("url")), dict(resolved))
            self.stream_cache = gioi_han.cat_bo_nho_dem(self.stream_cache, het_han=lambda gia_tri: gia_tri[0])
        return dict(resolved)

    def forget_stream(self, source, target):
        with self.stream_lock:
            self.stream_cache.pop((source, target), None)

    def resolve_stream(self, source, target):
        """Return the prepared stream, resolving again after cache expiry."""
        return self._resolve_stream(source, validate_stream_target(source, target))


def public_base_url_mac_dinh() -> str | None:
    """URL gốc mặc định cho loa: đọc YT_PUBLISH_IP (+ YT_PORT) → "http://<ip>:<port>".

    Không đặt YT_PUBLISH_IP thì trả None — server.py sẽ tự tính theo Host của
    request. Thay thế `doc_url_lan`/`public_base_url_cau_hinh` của c2a (đọc
    cai_dat.json "url_lan" ở tab YouTube); dịch vụ độc lập không còn tab ấy.
    """
    ip = (os.environ.get("YT_PUBLISH_IP") or "").strip().rstrip("/")
    if not ip:
        return None
    cong = (os.environ.get("YT_PORT") or "").strip() or str(CONG_MAC_DINH)
    if "://" in ip:
        return ip  # chủ máy tự đưa URL đầy đủ
    return f"http://{ip}:{cong}"


_CORE: PlayerCore | None = None
_CORE_LOCK = threading.Lock()


def tao_core(thu_muc_du_lieu: str) -> PlayerCore:
    """Factory + cache module: tạo PlayerCore cho thư mục dữ liệu, lần gọi đầu thắng.

    Test gọi `_dat_lai_cho_test()` trước để xoá cache rồi tạo lại.
    """
    global _CORE
    with _CORE_LOCK:
        if _CORE is None:
            _CORE = PlayerCore(thu_muc_du_lieu)
        return _CORE


def _dat_lai_cho_test() -> None:
    """Xoá cache core của module (cho test)."""
    global _CORE
    with _CORE_LOCK:
        _CORE = None


def core() -> PlayerCore:
    """Bí danh tương thích: `hang_cho.kho()` (chép nguyên từ c2a, không được sửa)
    vẫn gọi `dich_vu.core()`. Trả core trên thư mục dữ liệu mặc định (YT_DATA_DIR)."""
    try:
        from .cau_hinh import doc_cau_hinh
    except ImportError:  # chay truc tiep: python3 server.py
        from cau_hinh import doc_cau_hinh

    return tao_core(doc_cau_hinh().thu_muc_du_lieu)
