"""CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/search.py, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a.
Đổi so với bản gốc: thêm đầu tệp này; giữ nguyên toàn bộ (không import nội bộ c2a).

Metadata-only search providers used by the player service."""


# Chuyển nguyên từ add-on TriTue YouTube Player
# (github.com/TriTue2011/youtube, youtube_player/app/search.py, bản 0.6.2) — chủ máy
# 14/09/2026: "tích hợp trực tiếp trên dự án, và ha có thể kết nối đến". Giữ
# nguyên tên hàm và mã lỗi để tích hợp HA của repo nói chuyện được với c2a như
# với add-on; chỉ đổi đúng chỗ ghi "c2a:".


from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
from urllib.parse import parse_qs, quote_plus, urlsplit
from urllib.request import Request, urlopen


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,80}$")
YOUTUBE_URL_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
MAX_QUERY_LENGTH = 120
MAX_URL_LENGTH = 2048
ZING_ID = re.compile(r"^[A-Z0-9]{8,12}$")
ZING_SEARCH_URL = "https://ac.zingmp3.vn/v1/web/ac-suggestions"
SEARCH_RESPONSE_LIMIT = 2_000_000


class SearchUnavailableError(RuntimeError):
    """The metadata provider could not complete a search."""


def parse_search_payload(payload, *, limit):
    """Convert yt-dlp flat search output into the stable integration shape."""
    results = []
    entries = payload.get("entries") if isinstance(payload, dict) else []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("id") or "")
        if not VIDEO_ID.fullmatch(video_id):
            continue
        thumbnails = entry.get("thumbnails") or []
        thumbnail = next(
            (
                str(candidate.get("url"))
                for candidate in reversed(thumbnails)
                if isinstance(candidate, dict) and candidate.get("url")
            ),
            str(entry.get("thumbnail") or ""),
        )
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        duration = entry.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        results.append(
            {
                "source": "youtube",
                "kind": "video",
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": str(entry.get("title") or video_id),
                "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                "duration": duration,
                "thumbnail": thumbnail,
            }
        )
        if len(results) >= limit:
            break
    return results


FACEBOOK_ID = re.compile(r"^[0-9]{5,25}$")
FACEBOOK_URL_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
}
FACEBOOK_SHARE_PATH = re.compile(r"^/share/[a-z]/[A-Za-z0-9_-]{4,64}/?$", re.IGNORECASE)
# Mã video nằm trong trường NỘI BỘ của Facebook, dạng
#   media_id: "<mã trang>;<mã bài viết>;;9::impl_<mã video>"
# Đo 19/09/2026 trên trang trả về cho một link chia sẻ thật: chuỗi này xuất hiện nhiều
# lần, còn thẻ canonical và og:url thì chỉ trỏ tới BÀI VIẾT nên không dùng được.
FACEBOOK_MEDIA_ID = re.compile(r"impl_([0-9]{5,25})")
# Thẻ chuẩn của trang. Đo 19/09/2026 trên HAI dạng chia sẻ và chúng giấu mã ở hai nơi
# khác nhau — đó là lý do phải đọc cả hai chỗ:
#   /share/r/… (chia sẻ reel)  -> canonical = facebook.com/reel/<mã video>  (dùng được)
#                                 KHÔNG có trường impl_
#   /share/v/… (chia sẻ video) -> canonical = facebook.com/…/posts/<mã BÀI VIẾT> (vô dụng)
#                                 có trường impl_<mã video>
# Thẻ canonical/og:url là thẻ HTML có tài liệu nên bền hơn trường nội bộ → thử trước.
FACEBOOK_CANONICAL = re.compile(
    r'<(?:link[^>]*rel="canonical"[^>]*href|meta[^>]*property="og:url"[^>]*content)="([^"]+)"',
    re.IGNORECASE,
)
# Thiếu nhóm đầu đề này là Facebook đá sang trang đăng nhập rồi trả 400 — đo được:
# gọi trần thì 400 với 3.676 byte, gọi đủ đầu đề thì 200 với 298.659 byte.
FACEBOOK_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
# Facebook nhét số liệu vào ĐẦU trường tiêu đề. Đo 19/09/2026 trên dữ liệu thật:
# "49K views · 1.2K reactions | Có những chuyện có lẽ nên để trong lòng…". Để nguyên
# thì hiện một dòng rác trước tên bài. Chỉ cắt khi phần đứng trước dấu "|" ĐÚNG LÀ
# cụm số liệu — tên bài thật có chứa dấu "|" thì giữ nguyên.
FACEBOOK_COUNT_PREFIX = re.compile(
    r"^\s*[\d.,]+\s*[KMB]?\s*(views?|reactions?|comments?|shares?|likes?)"
    r"(\s*·\s*[\d.,]+\s*[KMB]?\s*(views?|reactions?|comments?|shares?|likes?))*\s*\|\s*",
    re.IGNORECASE,
)


def facebook_url_query(query):
    """Mã video Facebook trong một link người ta dán, hoặc None.

    Nhận /watch/?v=<mã>, /reel/<mã>/, /<trang>/videos/<tên bài>/<mã>/. KHÔNG nhận link
    chia sẻ /share/v/<mã ngắn>: mã trong đó là mã BÀI VIẾT, không phải mã video — xem
    `resolve_facebook_share` lo việc lần ra mã thật."""
    text = str(query or "").strip()
    if FACEBOOK_ID.fullmatch(text):
        return text
    if "//" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in FACEBOOK_URL_HOSTS:
        return None
    phan = [doan for doan in parsed.path.split("/") if doan]
    video_id = ""
    if parsed.path.rstrip("/").endswith("/watch"):
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    elif phan and phan[0] == "reel" and len(phan) > 1:
        video_id = phan[1]
    elif "videos" in phan:
        # Quét tìm đoạn LÀ CHUỖI SỐ, không lấy đoạn kế tiếp: Facebook chèn tên bài vào
        # giữa, ví dụ /Emgaibay.686868/videos/có-những-chuyện-…/1807802260572674/
        sau = phan[phan.index("videos") + 1 :]
        video_id = next((doan for doan in sau if FACEBOOK_ID.fullmatch(doan)), "")
    return video_id if FACEBOOK_ID.fullmatch(video_id) else None


def facebook_share_url(query):
    """Địa chỉ link chia sẻ Facebook (/share/v/…, /share/r/…), hoặc None."""
    text = str(query or "").strip()
    if "//" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in FACEBOOK_URL_HOSTS:
        return None
    return text if FACEBOOK_SHARE_PATH.fullmatch(parsed.path) else None


def resolve_facebook_share(share_url, *, timeout=20):
    """Mã video đứng sau một link chia sẻ Facebook.

    ĐƯỜNG DỰ PHÒNG, không bền: nó đọc một trường nội bộ không có tài liệu của trang
    Facebook, nên Facebook đổi trang là hỏng. Hỏng thì ném `SearchUnavailableError` để
    người dùng thấy lý do, chứ không trả None rồi biến thành "không tìm thấy".

    Vì sao phải làm: mã trong link chia sẻ KHÔNG phải mã video và cũng không suy ra
    được — đo được mã bài viết 1135095972510953 còn mã video 1807802260572674."""
    request = Request(share_url, headers=FACEBOOK_PAGE_HEADERS)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(SEARCH_RESPONSE_LIMIT + 1)
            if len(body) > SEARCH_RESPONSE_LIMIT:
                raise SearchUnavailableError("search_response_too_large")
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
                if len(body) > SEARCH_RESPONSE_LIMIT:
                    raise SearchUnavailableError("search_response_too_large")
    except SearchUnavailableError:
        raise
    except (OSError, ValueError, gzip.BadGzipFile) as error:
        raise SearchUnavailableError("search_provider_failed") from error
    trang = body.decode("utf-8", "ignore")
    # 1) Thẻ chuẩn trước — và KHÔNG viết bộ phân tích mới: địa chỉ lấy từ thẻ đem đưa
    #    thẳng vào `facebook_url_query`, vốn đã hiểu /reel/, /watch?v= và /videos/.
    for dia_chi in FACEBOOK_CANONICAL.findall(trang)[:4]:
        ma = facebook_url_query(dia_chi.replace("&amp;", "&"))
        if ma:
            return ma
    # 2) Đường lùi: trường nội bộ, chỉ dùng khi thẻ chuẩn trỏ vào bài viết chứ không
    #    trỏ vào video (đúng trường hợp của dạng /share/v/).
    found = FACEBOOK_MEDIA_ID.search(trang)
    if not found:
        raise SearchUnavailableError("facebook_share_unreadable")
    return found.group(1)


def _facebook_title(raw, fallback):
    """Tên bài đã bỏ cụm số liệu Facebook chèn sẵn ở đầu."""
    title = " ".join(str(raw or "").split())
    cleaned = FACEBOOK_COUNT_PREFIX.sub("", title).strip()
    return cleaned or title or fallback


def parse_facebook_payload(payload, *, limit):
    """Dựng mục hiển thị từ dữ liệu thô của một video Facebook.

    Viết riêng thay vì dùng lại bản YouTube: bản ấy gắn cứng nguồn "youtube", ghép địa
    chỉ thành link watch của YouTube, lấy ảnh dự phòng từ i.ytimg.com, và lọc mọi mục
    theo mã ĐÚNG 11 KÝ TỰ — mã Facebook là chuỗi số dài hơn nên sẽ bị loại âm thầm.
    Zing cũng có bản dựng riêng vì đúng lý do này."""
    results = []
    entries = payload.get("entries") if isinstance(payload, dict) else []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("id") or "")
        if not FACEBOOK_ID.fullmatch(video_id):
            continue
        thumbnails = entry.get("thumbnails") or []
        thumbnail = next(
            (
                str(candidate.get("url"))
                for candidate in reversed(thumbnails)
                if isinstance(candidate, dict) and candidate.get("url")
            ),
            str(entry.get("thumbnail") or ""),
        )
        duration = entry.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        results.append(
            {
                "source": "facebook",
                "kind": "video",
                "id": video_id,
                "url": f"https://www.facebook.com/watch/?v={video_id}",
                "title": _facebook_title(entry.get("title"), video_id),
                "channel": str(entry.get("channel") or entry.get("uploader") or ""),
                "duration": duration,
                "thumbnail": thumbnail,
            }
        )
        if len(results) >= limit:
            break
    return results


def search_facebook(query, *, limit=1, timeout=30):
    """Tra cứu một video Facebook từ LINK DÁN VÀO.

    Cố ý không nhận tìm theo từ khoá: Facebook không có đường tìm kiếm công khai để
    gọi, nên hứa suông chỉ sinh ra lỗi mơ hồ. Dán link thì báo rõ, gõ chữ thì báo rõ."""
    video_id = facebook_url_query(query)
    if video_id is None:
        # Link CHIA SẺ: mã trong đó là mã bài viết, phải lần ra mã video từ trang.
        share_url = facebook_share_url(query)
        if share_url is None:
            raise ValueError("invalid_search_query")
        video_id = resolve_facebook_share(share_url, timeout=timeout)
    _validated_query_and_limit(query, limit, max_length=MAX_URL_LENGTH)
    # c2a: container KHÔNG có lệnh `yt-dlp` trên PATH, chỉ có thư viện trong venv —
    # giống hệt `search_youtube` ngay dưới. Bản đầu tôi chép nguyên lệnh của add-on
    # (chuỗi "yt-dlp" trần) nên trên máy thật nó trả `search_process_failed` rồi bị bọc
    # thành `search_unavailable`: người dùng chỉ thấy "không tìm được lúc này". Đo
    # 19/09/2026 ngay trong container: `command -v yt-dlp` -> not found, trong khi tìm
    # kiếm YouTube vẫn chạy vì nó gọi qua trình thông dịch.
    command = [
        sys.executable, "-m", "yt_dlp",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        f"https://www.facebook.com/watch/?v={video_id}",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SearchUnavailableError("search_process_failed") from error
    if completed.returncode != 0:
        raise SearchUnavailableError("search_provider_failed")
    try:
        entry = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise SearchUnavailableError("invalid_search_response") from error
    return parse_facebook_payload({"entries": [entry]}, limit=1)


def youtube_url_query(query):
    """Return ("video", id) or ("playlist", id) when the query is a YouTube link.

    Accepts what people paste from the app or browser: watch links with extra
    parameters (app=desktop, list=RD..., pp=..., si=...), youtu.be, Shorts,
    embed and live links, playlist pages, with or without "https://". A watch
    link inside a mix or playlist still means that one video.
    """
    text = str(query or "").strip()
    if not text or len(text) > MAX_URL_LENGTH or any(char.isspace() for char in text):
        return None
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_URL_HOSTS:
        return None
    values = parse_qs(parsed.query)
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path == "/watch":
        video_id = values.get("v", [""])[0]
    elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
        video_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    else:
        video_id = ""
    if VIDEO_ID.fullmatch(video_id):
        return ("video", video_id)
    playlist_id = values.get("list", [""])[0]
    if parsed.path == "/playlist" and PLAYLIST_ID.fullmatch(playlist_id):
        return ("playlist", playlist_id)
    return None


def _validated_query_and_limit(query, limit, *, max_length=MAX_QUERY_LENGTH):
    query = str(query or "").strip()
    if not 1 <= len(query) <= max_length:
        raise ValueError("invalid_search_query")
    try:
        limit = int(limit)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_search_limit") from error
    if not 1 <= limit <= 30:
        raise ValueError("invalid_search_limit")
    return query, limit


def _zing_suggestions(payload):
    """Yield suggestion entries from Zing's nested autocomplete response."""
    data = payload.get("data") if isinstance(payload, dict) else None
    groups = data.get("items") if isinstance(data, dict) else []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        suggestions = group.get("suggestions")
        if isinstance(suggestions, list):
            yield from suggestions


def parse_zing_payload(payload, *, limit):
    """Convert public Zing song suggestions into the stable search shape."""
    results = []
    for entry in _zing_suggestions(payload):
        if not isinstance(entry, dict) or entry.get("type") != 1:
            continue
        song_id = str(entry.get("id") or "")
        song_url = str(entry.get("link") or "")
        parsed_url = urlsplit(song_url)
        song_host = (parsed_url.hostname or "").lower()
        if (
            not ZING_ID.fullmatch(song_id)
            or parsed_url.scheme != "https"
            or not (
                song_host == "zingmp3.vn"
                or song_host.endswith(".zingmp3.vn")
            )
            or not parsed_url.path.startswith("/bai-hat/")
            or entry.get("status") != 1
            or entry.get("privacy") != 1
            or entry.get("playStatus") != 2
        ):
            continue
        artists = entry.get("artists") or []
        artist_names = [
            str(artist.get("name"))
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        ]
        duration = entry.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        results.append(
            {
                "source": "zing",
                "kind": "song",
                "id": song_id,
                "url": song_url,
                "title": str(entry.get("title") or song_id),
                "channel": ", ".join(artist_names),
                "duration": duration,
                "thumbnail": str(entry.get("thumb") or ""),
            }
        )
        if len(results) >= limit:
            break
    return results


def search_youtube(query, *, limit=20, timeout=30):
    """Search song metadata without downloading or resolving media streams.

    A pasted YouTube link looks up exactly that video (or that playlist's
    videos) instead of searching for the link's text.
    """
    link = youtube_url_query(query)
    if link is not None:
        _, limit = _validated_query_and_limit(query, limit, max_length=MAX_URL_LENGTH)
    else:
        query, limit = _validated_query_and_limit(query, limit)

    # c2a: tìm trên YouTube thường, không phải tab "Bài hát" của YouTube Music.
    # Tab đó chỉ gồm bài có hồ sơ âm nhạc chính thức, nên nhạc AI / cover đăng
    # dạng video bị lọc mất dù phát được — đo 14/09/2026 với "trót tin vào lời
    # hứa": `#songs` không có llPioQNSBLY, `ytsearch` có ở vị trí 3, cùng 3,8 s,
    # và trả thêm tên kênh + thời lượng (tab Bài hát để trống cả hai).
    if link is None:
        search_url = f"ytsearch{limit}:{query}"
    elif link[0] == "video":
        search_url = f"https://www.youtube.com/watch?v={link[1]}"
    else:
        search_url = f"https://www.youtube.com/playlist?list={link[1]}"
    # c2a: container không có lệnh `yt-dlp` trên PATH, chỉ có thư viện trong venv.
    command = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--playlist-end",
        str(limit),
        search_url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SearchUnavailableError("search_process_failed") from error
    if completed.returncode != 0:
        raise SearchUnavailableError("search_provider_failed")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise SearchUnavailableError("invalid_search_response") from error
    if link is not None and link[0] == "video":
        # One video comes back as its own info object, not a list of entries.
        return parse_search_payload({"entries": [payload]}, limit=1)
    return parse_search_payload(payload, limit=limit)


def youtube_playlist_id(text):
    """ID playlist trong một link YouTube (trang /playlist hoặc link xem có `list=`)."""
    raw = str(text or "").strip()
    if not raw or len(raw) > MAX_URL_LENGTH or any(char.isspace() for char in raw):
        return None
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in YOUTUBE_URL_HOSTS:
        return None
    playlist_id = parse_qs(parsed.query).get("list", [""])[0]
    return playlist_id if PLAYLIST_ID.fullmatch(playlist_id) else None


def fetch_youtube_playlist(text, *, limit=500, timeout=120):
    """(tên, các bài) của cả một playlist YouTube công khai — để lưu nguyên playlist."""
    playlist_id = youtube_playlist_id(text)
    if playlist_id is None:
        raise ValueError("invalid_playlist_link")
    command = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--dump-single-json", "--skip-download", "--no-warnings", "--ignore-errors",
        "--playlist-end", str(int(limit)),
        f"https://www.youtube.com/playlist?list={playlist_id}",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SearchUnavailableError("search_process_failed") from error
    if completed.returncode != 0:
        raise SearchUnavailableError("search_provider_failed")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise SearchUnavailableError("invalid_search_response") from error
    title = str(payload.get("title") or "").strip() if isinstance(payload, dict) else ""
    return title or "Playlist YouTube", parse_search_payload(payload, limit=int(limit))


def search_zing(query, *, limit=20, timeout=10):
    """Search public Zing song metadata through its autocomplete endpoint."""
    query, limit = _validated_query_and_limit(query, limit)
    request = Request(
        f"{ZING_SEARCH_URL}?query={quote_plus(query)}&num={limit}",
        headers={
            "Accept": "application/json",
            "User-Agent": "TriTue-YouTube-Player/0.6",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(SEARCH_RESPONSE_LIMIT + 1)
            if len(body) > SEARCH_RESPONSE_LIMIT:
                raise SearchUnavailableError("search_response_too_large")
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
                if len(body) > SEARCH_RESPONSE_LIMIT:
                    raise SearchUnavailableError("search_response_too_large")
        payload = json.loads(body)
    except SearchUnavailableError:
        raise
    except (OSError, ValueError, gzip.BadGzipFile) as error:
        raise SearchUnavailableError("search_provider_failed") from error
    if not isinstance(payload, dict) or payload.get("err") != 0:
        raise SearchUnavailableError("invalid_search_response")
    return parse_zing_payload(payload, limit=limit)
