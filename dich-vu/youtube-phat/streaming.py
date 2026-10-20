"""CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/streaming.py, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a.
Đổi so với bản gốc: thêm đầu tệp này; giữ nguyên toàn bộ (không import nội bộ c2a).

Short-lived, signed streaming support for public Zing songs and YouTube audio."""


# Chuyển nguyên từ add-on TriTue YouTube Player
# (github.com/TriTue2011/youtube, youtube_player/app/streaming.py, bản 0.6.2) — chủ máy
# 14/09/2026: "tích hợp trực tiếp trên dự án, và ha có thể kết nối đến". Giữ
# nguyên tên hàm và mã lỗi để tích hợp HA của repo nói chuyện được với c2a như
# với add-on; chỉ đổi đúng chỗ ghi "c2a:".


from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import io
import json
import math
import re
import shutil
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    Request,
    build_opener,
)


STREAM_SOURCES = ("zing", "youtube", "youtube_video", "facebook", "facebook_video")
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Facebook: reel hoặc video thường, mã là một chuỗi số.
#
# Đo 19/09/2026 trên một bài thật, CÙNG MỘT MÃ `1807802260572674`:
#   /watch/?v=<mã>    -> đúng video, 148,821 giây
#   /reel/<mã>/       -> đúng video, 148,821 giây
#   /video.php?v=<mã> -> "This video is only available for registered users"
# Câu "chỉ dành cho người dùng đã đăng ký" là lời từ chối cho DẠNG ĐỊA CHỈ không đọc
# được, KHÔNG phải lời nói về quyền xem — nó từng làm tôi kết luận sai ba lần liền.
# Dựng địa chỉ dạng "/watch/?v=" vì nó phủ được cả reel lẫn video thường.
FACEBOOK_VIDEO_ID = re.compile(r"^[0-9]{5,25}$")
FACEBOOK_VIDEO_TARGET = re.compile(r"^([0-9]{5,25}):(360|480|720|1080)$")
FACEBOOK_STREAM_HOSTS = ("fbcdn.net",)
# Facebook phục vụ hai tệp GỘP SẴN (hình avc1 + tiếng AAC trong cùng một mp4): "hd"
# và "sd". Đo 19/09/2026: tải 128 KB đầu của cả hai đều thấy "ftyp moov avc1 mp4a",
# máy chủ trả HTTP 206 kiểu video/mp4, từ video-hkg1-2.xx.fbcdn.net.
# QUAN TRỌNG: yt-dlp báo vcodec, acodec và height của hai định dạng này đều là NA,
# nên KHÔNG lọc được theo codec hay chiều cao như bên YouTube — chép khuôn
# `_youtube_video_format` sang đây sẽ lọc sạch mọi ứng viên rồi báo "không có định
# dạng phù hợp". Phải chọn theo MÃ ĐỊNH DẠNG.
FACEBOOK_MUXED_FORMATS = ("hd", "sd")
# Luồng HÌNH (không tiếng) của một video, cho trình duyệt khi YouTube không cho nhúng:
# đích "ID:chiều cao tối đa". Tiếng đi riêng (loa hoặc thẻ <audio>), hình chạy theo.
YOUTUBE_VIDEO_HEIGHTS = (360, 480, 720, 1080)
YOUTUBE_VIDEO_TARGET = re.compile(r"^([A-Za-z0-9_-]{11}):(360|480|720|1080)$")
# Trình duyệt nào cũng giải được avc1/mp4 (kể cả Safari cũ); vp9 rồi av1 chỉ khi thiếu.
YOUTUBE_VIDEO_CODECS = ("avc1", "vp9", "vp09", "av01")
YOUTUBE_AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio"
STREAM_CACHE_DEFAULT_SECONDS = 120
STREAM_CACHE_MAX_SECONDS = 5 * 3600
STREAM_EXPIRY_MARGIN_SECONDS = 600
YOUTUBE_STREAM_HOSTS = ("googlevideo.com",)
ZING_ID = re.compile(r"^[A-Za-z0-9]{8,16}$")
ZING_API_BASE = "https://zingmp3.vn"
ZING_API_PATH = "/api/v2/song/get/streaming"
# c2a: apiKey/secret ký yêu cầu của web zingmp3.vn KHÔNG nằm trong mã — gitleaks
# chặn commit. Khoá là của chính trang web Zing, công khai trong mã JavaScript
# của trang: `fetch_zing_web_keys` lấy về lúc chạy (ai cài cũng dùng được, Zing đổi
# khoá thì tự theo); lõi (`dich_vu.PlayerCore.zing_keys`) lưu đệm và truyền vào.
ZING_HOME_URL = "https://zingmp3.vn/"
ZING_WEB_BUNDLE = re.compile(r'src="(https://[a-z0-9.-]+\.zmdcdn\.me/[^"]+/main\.min\.js)"')
# Đo 14/09/2026 trong main.min.js bản 1.20.4: `var r="<apiKey>",i="<secret>",a={…}`.
ZING_WEB_KEY_PAIR = re.compile(r'="([A-Za-z0-9]{32})",[A-Za-z_$][\w$]{0,3}="([A-Za-z0-9]{32})"')
ZING_WEB_HOSTS = ("zingmp3.vn", "zmdcdn.me")
ZING_WEB_VERSION = "1.20.4"
ZING_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
CONTENT_TYPES = {
    "aac": "audio/aac",
    # Bài PHÁT TRỰC TIẾP không có tệp nhạc, chỉ có bản kê luồng HLS. Thiếu dòng này
    # thì nó rơi về mặc định "audio/mp4" — một lời khai SAI. Đo 20/09/2026 trên bài
    # trực tiếp thật: máy chủ gốc trả đúng "application/vnd.apple.mpegurl", còn ta
    # thì khai là mp4, khiến bên nhận tưởng phát được rồi mới hỏng.
    "m3u8": "application/vnd.apple.mpegurl",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "webm": "audio/webm",
}
VIDEO_CONTENT_TYPES = {"mp4": "video/mp4", "webm": "video/webm"}
ZING_CDN_HOSTS = ("zmdcdn.me", "zadn.vn", "zing.vn", "zingmp3.vn")


class InvalidStreamTokenError(ValueError):
    """The public stream token is invalid, tampered with, or expired."""


class StreamUnavailableError(RuntimeError):
    """The upstream public stream could not be resolved."""


class _ZingRedirectHandler(HTTPRedirectHandler):
    """Allow page redirects only to another validated public Zing song URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_zing_target(newurl)
        except ValueError as error:
            raise StreamUnavailableError("unsafe_stream_redirect") from error
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


_STREAM_TOKEN = re.compile(r"/api/stream/([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+")
_STREAM_TOKEN_KY = re.compile(r"/api/stream/([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)")


def luong_ky_boi(media_content_id: object, secret: str) -> bool:
    """Loa có đang phát luồng KÝ bằng ``secret`` (luồng của chính máy phát này) không.

    Chỉ so CHỮ KÝ, bỏ qua hạn dùng: bài dài phát quá hạn token vẫn là luồng của mình. Khác ``stream_target``
    (chỉ đọc payload) ở chỗ không nhận nhầm luồng của máy phát khác (add-on) đang phát trên cùng HA."""
    match = _STREAM_TOKEN_KY.search(str(media_content_id or ""))
    if not match or not secret:
        return False
    ky = _b64encode(hmac.new(str(secret).encode(), match.group(1).encode(), hashlib.sha256).digest())
    return hmac.compare_digest(match.group(2), ky)


def stream_target(media_content_id: object) -> str | None:
    """Bài loa đang phát, đọc từ link luồng đã ký mà loa báo lên HA.

    Phần payload của token là base64 JSON thường ({exp, source, target}); chỉ chữ
    ký là bí mật. None = không phải luồng của trình phát (tivi mở ứng dụng
    YouTube gốc, nguồn khác) nên không biết là bài nào."""
    match = _STREAM_TOKEN.search(str(media_content_id or ""))
    if not match:
        return None
    try:
        data = json.loads(_b64decode(match.group(1)))
    except (ValueError, TypeError):
        return None
    target = data.get("target") if isinstance(data, dict) else None
    return str(target) if target else None


def validate_zing_target(target_url: str) -> str:
    """Return a normalized public Zing song URL or raise ``ValueError``."""
    target_url = str(target_url or "").strip()
    parsed = urlsplit(target_url)
    host = (parsed.hostname or "").lower()
    song_id = parsed.path.rsplit("/", 1)[-1].removesuffix(".html")
    if (
        parsed.scheme != "https"
        or not (host == "zingmp3.vn" or host.endswith(".zingmp3.vn"))
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/bai-hat/")
        or not ZING_ID.fullmatch(song_id)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_zing_target")
    return target_url


def normalize_public_base_url(base_url: str) -> str:
    """Validate the LAN URL speakers use to reach this add-on.

    c2a: trình phát nằm dưới một tiền tố đường dẫn của c2a (vd `/yt`), nên
    nhận đường dẫn gốc bất kỳ — vẫn cấm tài khoản, query và fragment.
    """
    base_url = str(base_url or "").strip()
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_public_base_url")
    return base_url.rstrip("/")


def validate_stream_target(source: str, target: str) -> str:
    """Return a normalized target for one supported audio source, or raise."""
    if source == "zing":
        return validate_zing_target(target)
    if source == "youtube":
        video_id = str(target or "").strip()
        if not YOUTUBE_VIDEO_ID.fullmatch(video_id):
            raise ValueError("invalid_youtube_target")
        return video_id
    if source == "youtube_video":
        value = str(target or "").strip()
        if not YOUTUBE_VIDEO_TARGET.fullmatch(value):
            raise ValueError("invalid_youtube_target")
        return value
    if source == "facebook":
        video_id = str(target or "").strip()
        if not FACEBOOK_VIDEO_ID.fullmatch(video_id):
            raise ValueError("invalid_facebook_target")
        return video_id
    if source == "facebook_video":
        value = str(target or "").strip()
        if not FACEBOOK_VIDEO_TARGET.fullmatch(value):
            raise ValueError("invalid_facebook_target")
        return value
    raise ValueError("unsupported_stream_source")


def youtube_video_target(video_id: str, max_height) -> str:
    """Đích luồng hình: chiều cao chuẩn gần nhất không vượt `max_height` (360–1080)."""
    try:
        wanted = int(max_height)
    except (TypeError, ValueError):
        wanted = 720
    height = max([h for h in YOUTUBE_VIDEO_HEIGHTS if h <= wanted] or [YOUTUBE_VIDEO_HEIGHTS[0]])
    return validate_stream_target("youtube_video", f"{video_id}:{height}")


def create_stream_token(
    target: str,
    secret: str,
    *,
    source: str = "zing",
    now: int | None = None,
    ttl: int = 300,
) -> str:
    """Create a signed, URL-safe token for one Zing song or YouTube video."""
    target = validate_stream_target(source, target)
    secret = str(secret or "")
    if not secret:
        raise ValueError("invalid_stream_secret")
    if not 30 <= int(ttl) <= 7200:
        raise ValueError("invalid_stream_ttl")
    issued_at = int(time.time() if now is None else now)
    payload = _b64encode(
        json.dumps(
            {"exp": issued_at + int(ttl), "source": source, "target": target},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    signature = _b64encode(
        hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    )
    return f"{payload}.{signature}"


def verify_stream_token(
    token: str, secret: str, *, now: int | None = None
) -> tuple[str, str]:
    """Verify a stream token and return its ``(source, target)`` pair."""
    try:
        payload, provided_signature = str(token).split(".", 1)
        expected_signature = _b64encode(
            hmac.new(str(secret).encode(), payload.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise InvalidStreamTokenError("invalid_stream_token")
        value = json.loads(_b64decode(payload))
        current_time = int(time.time() if now is None else now)
        source = value.get("source")
        if source not in STREAM_SOURCES or int(value.get("exp", 0)) < current_time:
            raise InvalidStreamTokenError("expired_stream_token")
        return source, validate_stream_target(source, value.get("target"))
    except InvalidStreamTokenError:
        raise
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise InvalidStreamTokenError("invalid_stream_token") from error


def build_signed_stream_url(
    public_base_url: str,
    target: str,
    secret: str,
    *,
    source: str = "zing",
    now: int | None = None,
    ttl: int = 300,
) -> str:
    """Build the short-lived URL passed to a Home Assistant media player."""
    base_url = normalize_public_base_url(public_base_url)
    token = create_stream_token(target, secret, source=source, now=now, ttl=ttl)
    return f"{base_url}/api/stream/{token}"


def _read_limited_response(response, *, limit=1_000_000) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise StreamUnavailableError("stream_response_too_large")
    if str(response.headers.get("Content-Encoding") or "").lower() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                body = compressed.read(limit + 1)
        except (OSError, EOFError) as error:
            raise StreamUnavailableError("invalid_stream_response") from error
        if len(body) > limit:
            raise StreamUnavailableError("stream_response_too_large")
    return body


def extract_zing_web_keys(script: str) -> dict | None:
    """apiKey/secret từ mã JavaScript của web Zing, hoặc None nếu không thấy."""
    match = ZING_WEB_KEY_PAIR.search(str(script or ""))
    if not match:
        return None
    return {"api_key": match.group(1), "api_secret": match.group(2)}


def fetch_zing_web_keys(*, timeout: int = 20) -> dict:
    """Tải trang chủ zingmp3.vn, tìm bó mã main.min.js, trích apiKey/secret."""

    def _get(url: str, limit: int) -> str:
        with build_opener().open(
            Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": ZING_USER_AGENT}),
            timeout=timeout,
        ) as response:
            host = (urlsplit(response.geturl()).hostname or "").lower()
            if not any(host == h or host.endswith(f".{h}") for h in ZING_WEB_HOSTS):
                raise StreamUnavailableError("unsafe_stream_redirect")
            return _read_limited_response(response, limit=limit).decode("utf-8", "replace")

    try:
        bundle = ZING_WEB_BUNDLE.search(_get(ZING_HOME_URL, 1_000_000))
        keys = extract_zing_web_keys(_get(bundle.group(1), 8_000_000)) if bundle else None
    except StreamUnavailableError:
        raise
    except (OSError, ValueError) as error:
        raise StreamUnavailableError("zing_keys_unavailable") from error
    if not keys:
        raise StreamUnavailableError("zing_keys_unavailable")
    return keys


def _cookie_web_version(cookie_jar: CookieJar) -> str:
    for cookie in cookie_jar:
        if cookie.name == "zmp3_app_version.1" and re.fullmatch(
            r"\d+\.\d+\.\d+", str(cookie.value or "")
        ):
            return cookie.value
    return ZING_WEB_VERSION


def _build_zing_api_url(
    song_id: str, version: str, current_time: int, api_key: str, api_secret: str
) -> str:
    params = {
        "id": song_id,
        "ctime": str(current_time),
        "version": version,
    }
    canonical = "".join(f"{key}={params[key]}" for key in sorted(params))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    signature = hmac.new(
        api_secret.encode(),
        f"{ZING_API_PATH}{digest}".encode(),
        hashlib.sha512,
    ).hexdigest()
    query = urlencode({**params, "apiKey": api_key, "sig": signature})
    return f"{ZING_API_BASE}{ZING_API_PATH}?{query}"


ZING_PLAYLIST_PATH = "/api/v2/page/get/playlist"


def zing_playlist_id(text: str) -> str | None:
    """ID album/playlist trong link Zing MP3 (/album/…/ID.html, /playlist/…/ID.html)."""
    parsed = urlsplit(str(text or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (host == "zingmp3.vn" or host.endswith(".zingmp3.vn")):
        return None
    if not parsed.path.startswith(("/album/", "/playlist/")):
        return None
    playlist_id = parsed.path.rsplit("/", 1)[-1].removesuffix(".html")
    return playlist_id if ZING_ID.fullmatch(playlist_id) else None


def fetch_zing_playlist(
    text: str, *, api_key: str, api_secret: str, timeout: int = 20, now: int | None = None
) -> tuple[str, list[dict]]:
    """(tên, các bài công khai) của một album/playlist Zing MP3.

    Cùng cách ký như luồng bài hát, chỉ khác đường dẫn API. Đo 14/09/2026 với album
    n1mqFnz65jGl: 28 bài, mỗi bài có `streamingStatus` (1 nghe được, 2 là VIP — bỏ)."""
    playlist_id = zing_playlist_id(text)
    if playlist_id is None:
        raise ValueError("invalid_playlist_link")
    if not api_key or not api_secret:
        raise StreamUnavailableError("stream_provider_failed")
    cookie_jar = CookieJar()
    opener = build_opener(_ZingRedirectHandler(), HTTPCookieProcessor(cookie_jar))
    try:
        with opener.open(Request(ZING_HOME_URL, headers={"User-Agent": ZING_USER_AGENT}), timeout=timeout) as response:
            response.read(1)
        params = {"id": playlist_id, "ctime": str(int(round(time.time()) if now is None else now)),
                  "version": _cookie_web_version(cookie_jar)}
        digest = hashlib.sha256("".join(f"{k}={params[k]}" for k in sorted(params)).encode()).hexdigest()
        signature = hmac.new(api_secret.encode(), f"{ZING_PLAYLIST_PATH}{digest}".encode(), hashlib.sha512).hexdigest()
        url = f"{ZING_API_BASE}{ZING_PLAYLIST_PATH}?{urlencode({**params, 'apiKey': api_key, 'sig': signature})}"
        with opener.open(Request(url, headers={
            "Accept": "application/json", "Accept-Encoding": "gzip", "Referer": ZING_HOME_URL,
            "User-Agent": ZING_USER_AGENT,
        }), timeout=timeout) as response:
            payload = json.loads(_read_limited_response(response, limit=4_000_000))
    except StreamUnavailableError:
        raise
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StreamUnavailableError("stream_provider_failed") from error
    data = payload.get("data") if isinstance(payload, dict) and payload.get("err") == 0 else None
    if not isinstance(data, dict):
        raise StreamUnavailableError("stream_provider_failed")
    items = []
    for song in ((data.get("song") or {}).get("items") or []):
        if not isinstance(song, dict) or song.get("streamingStatus") != 1:
            continue
        link = str(song.get("link") or "")
        song_id = str(song.get("encodeId") or "")
        if not link.startswith("/bai-hat/") or not ZING_ID.fullmatch(song_id):
            continue
        items.append({
            "source": "zing", "kind": "song", "id": song_id, "url": f"https://zingmp3.vn{link}",
            "title": str(song.get("title") or song_id), "channel": str(song.get("artistsNames") or ""),
            "duration": song.get("duration"), "thumbnail": str(song.get("thumbnailM") or song.get("thumbnail") or ""),
        })
    return str(data.get("title") or "Album Zing MP3"), items


def resolve_zing_stream(
    target_url: str,
    *,
    api_key: str,
    api_secret: str,
    timeout: int = 30,
    now: int | None = None,
) -> dict:
    """Resolve one browser-playable public Zing song without downloading it."""
    target_url = validate_zing_target(target_url)
    if not api_key or not api_secret:
        raise StreamUnavailableError("zing_keys_missing")
    cookie_jar = CookieJar()
    opener = build_opener(_ZingRedirectHandler(), HTTPCookieProcessor(cookie_jar))
    try:
        with opener.open(
            Request(
                target_url,
                headers={
                    "Accept-Encoding": "identity",
                    "User-Agent": ZING_USER_AGENT,
                },
            ),
            timeout=timeout,
        ) as response:
            redirected_target = validate_zing_target(response.geturl())
            response.read(1)

        song_id = urlsplit(redirected_target).path.rsplit("/", 1)[-1].removesuffix(
            ".html"
        )
        api_url = _build_zing_api_url(
            song_id,
            _cookie_web_version(cookie_jar),
            int(round(time.time()) if now is None else now),
            api_key,
            api_secret,
        )
        with opener.open(
            Request(
                api_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Encoding": "gzip",
                    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
                    "Referer": redirected_target,
                    "User-Agent": ZING_USER_AGENT,
                },
            ),
            timeout=timeout,
        ) as response:
            payload = json.loads(_read_limited_response(response))
    except StreamUnavailableError:
        raise
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StreamUnavailableError("stream_provider_failed") from error

    if (
        not isinstance(payload, dict)
        or payload.get("err") != 0
        or not isinstance(payload.get("data"), dict)
    ):
        raise StreamUnavailableError("stream_provider_failed")

    streams = payload["data"]
    stream_url = next(
        (
            str(streams.get(quality) or "")
            for quality in ("320", "128")
            if str(streams.get(quality) or "").startswith(("http://", "https://"))
        ),
        "",
    )
    parsed_stream = urlsplit(stream_url)
    stream_host = (parsed_stream.hostname or "").lower()
    if (
        parsed_stream.scheme not in {"http", "https"}
        or not any(
            stream_host == suffix or stream_host.endswith(f".{suffix}")
            for suffix in ZING_CDN_HOSTS
        )
    ):
        raise StreamUnavailableError("unsupported_stream_format")
    extension = parsed_stream.path.rsplit(".", 1)[-1].lower()
    return {
        "url": stream_url,
        "headers": {
            "Referer": redirected_target,
            "User-Agent": ZING_USER_AGENT,
        },
        "content_type": CONTENT_TYPES.get(extension, "audio/mpeg"),
    }


def _youtube_audio_format(info: dict) -> dict:
    """Pick the concrete audio format yt-dlp selected from its JSON output."""
    if info.get("url"):
        return info
    requested = info.get("requested_downloads")
    if isinstance(requested, list) and requested and isinstance(requested[0], dict):
        return requested[0]
    formats = info.get("formats")
    if isinstance(formats, list):
        audio_only = [
            item
            for item in formats
            if isinstance(item, dict)
            and item.get("url")
            and item.get("acodec") not in (None, "none")
            and item.get("vcodec") in (None, "none")
        ]
        if audio_only:
            return audio_only[-1]
    raise StreamUnavailableError("stream_provider_failed")


def extract_with_yt_dlp(watch_url: str, timeout: int) -> dict:
    """Chạy yt-dlp ngay trong tiến trình, trả đúng thứ ``--dump-single-json`` in ra.

    Đo 14/09/2026 trong container c2a: mở tiến trình yt-dlp mới mất 4,8–7,1 giây
    mỗi bài (riêng nạp yt_dlp 2,2 giây); cùng việc đó trong tiến trình sống lâu
    chỉ 1,1–1,8 giây. Khoảng chờ đó là lúc loa chưa kêu và mỗi lần bấm bài kế.
    """
    import yt_dlp  # nạp lần đầu dùng, sau đó Python giữ sẵn

    options = {
        "format": YOUTUBE_AUDIO_FORMAT,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": timeout,
        "extractor_retries": 1,
    }
    if deno := deno_path():
        options["js_runtimes"] = {"deno": {"path": deno}}
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.sanitize_info(ydl.extract_info(watch_url, download=False))


def deno_path() -> str:
    """Deno cho yt-dlp giải thử thách JS của YouTube.

    yt-dlp 2026.8 báo "YouTube extraction without a JS runtime has been deprecated,
    and some formats may be missing" khi không có. Gói `deno` (PyPI) đặt tệp chạy cạnh
    python của venv — không nằm trên PATH của container — nên chỉ đường thẳng."""
    beside = Path(sys.executable).with_name("deno")
    if beside.is_file():
        return str(beside)
    return shutil.which("deno") or ""


def stream_cache_seconds(stream_url: str, *, now: float | None = None) -> int:
    """Link luồng đã giải được dùng lại bao lâu.

    Link googlevideo tự mang hạn ``expire`` (khoảng 6 giờ tới); dùng lại tới sát
    hạn đó thì phát lại, tua, loa hỏi từng đoạn không phải giải lại. Link không
    có hạn giữ 2 phút như cũ."""
    try:
        expire = int(parse_qs(urlsplit(str(stream_url)).query).get("expire", [""])[0])
    except ValueError:
        return STREAM_CACHE_DEFAULT_SECONDS
    remaining = expire - int(time.time() if now is None else now) - STREAM_EXPIRY_MARGIN_SECONDS
    return max(0, min(remaining, STREAM_CACHE_MAX_SECONDS))


def _youtube_video_format(info: dict, max_height: int) -> dict:
    """Luồng chỉ-hình tải thẳng (https, không m3u8) cao nhất không vượt `max_height`."""
    candidates = []
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or not item.get("url") or item.get("protocol") not in ("https", "http"):
            continue
        codec = str(item.get("vcodec") or "none").lower()
        height = item.get("height")
        if codec == "none" or not isinstance(height, int) or height > max_height:
            continue
        if str(item.get("ext") or "").lower() not in VIDEO_CONTENT_TYPES:
            continue
        rank = next((len(YOUTUBE_VIDEO_CODECS) - i for i, name in enumerate(YOUTUBE_VIDEO_CODECS) if codec.startswith(name)), 0)
        # Cao nhất trước; cùng cao thì mã hoá dễ giải hơn; chỉ-hình trước bản kèm tiếng.
        candidates.append((height, rank, item.get("acodec") in (None, "none"), float(item.get("tbr") or 0), item))
    if not candidates:
        raise StreamUnavailableError("unsupported_stream_format")
    # H.264 phát được trên iPhone, Chrome và Android. vp9 1080 cao hơn nhưng
    # iPhone không giải, nên bài chỉ có vp9 ở 1080 mà avc1 ở 720 thì lấy avc1.
    # Không có avc1 thì mới lấy mã còn lại.
    avc = [c for c in candidates if str(c[4].get("vcodec") or "").lower().startswith("avc1")]
    return max(avc or candidates, key=lambda c: c[:4])[4]


def resolve_youtube_video(target: str, *, timeout: int = 20, extractor=extract_with_yt_dlp) -> dict:
    """Luồng hình (không tiếng) của một video YouTube cho thẻ <video> của trình duyệt.

    Dùng khi YouTube từ chối khung nhúng (video hãng đĩa khi trang mở bằng địa chỉ IP).
    Đo 14/09/2026 trong Chrome: luồng chỉ-hình avc1/vp9/av1 1080p phát thẳng bằng
    <video> (1920×1080, ~30 hình/giây); bài M2M bị chặn nhúng phát 640×480 (bản gốc)."""
    video_id, max_height = validate_stream_target("youtube_video", target).split(":")
    try:
        info = extractor(f"https://www.youtube.com/watch?v={video_id}", timeout)
    except Exception as error:  # yt-dlp ném nhiều loại lỗi khác nhau cho cùng một hỏng
        raise StreamUnavailableError("stream_provider_failed") from error
    if not isinstance(info, dict):
        raise StreamUnavailableError("invalid_stream_response")
    selected = _youtube_video_format(info, int(max_height))
    stream_url = str(selected["url"])
    stream_host = (urlsplit(stream_url).hostname or "").lower()
    if not any(stream_host == suffix or stream_host.endswith(f".{suffix}") for suffix in YOUTUBE_STREAM_HOSTS):
        raise StreamUnavailableError("unsupported_stream_format")
    headers = {"User-Agent": ZING_USER_AGENT}
    upstream_headers = selected.get("http_headers") or info.get("http_headers")
    if isinstance(upstream_headers, dict):
        headers = {str(key): str(value) for key, value in upstream_headers.items() if key and value} or headers
    return {
        "url": stream_url,
        "headers": headers,
        "content_type": VIDEO_CONTENT_TYPES[str(selected.get("ext")).lower()],
        "height": selected.get("height"),
        "bitrate_kbps": round(float(selected.get("tbr") or 0)),
    }


def resolve_youtube_audio(
    video_id: str, *, timeout: int = 20, extractor=extract_with_yt_dlp
) -> dict:
    """Resolve one browser-free direct audio stream for a public YouTube video.

    yt-dlp extracts a short-lived, IP-bound ``googlevideo.com`` URL; the caller
    relays it through c2a so speakers never fetch YouTube directly.
    """
    video_id = validate_stream_target("youtube", video_id)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        info = extractor(watch_url, timeout)
    except Exception as error:  # yt-dlp ném nhiều loại lỗi khác nhau cho cùng một hỏng
        raise StreamUnavailableError("stream_provider_failed") from error
    if not isinstance(info, dict):
        raise StreamUnavailableError("invalid_stream_response")

    selected = _youtube_audio_format(info)
    stream_url = str(selected.get("url") or "")
    parsed_stream = urlsplit(stream_url)
    stream_host = (parsed_stream.hostname or "").lower()
    if parsed_stream.scheme not in {"http", "https"} or not any(
        stream_host == suffix or stream_host.endswith(f".{suffix}")
        for suffix in YOUTUBE_STREAM_HOSTS
    ):
        raise StreamUnavailableError("unsupported_stream_format")

    extension = str(selected.get("ext") or "").lower()
    headers = {"User-Agent": ZING_USER_AGENT}
    upstream_headers = selected.get("http_headers") or info.get("http_headers")
    if isinstance(upstream_headers, dict):
        headers = {
            str(key): str(value)
            for key, value in upstream_headers.items()
            if key and value
        } or headers
    return {
        "url": stream_url,
        "headers": headers,
        "content_type": CONTENT_TYPES.get(extension, "audio/mp4"),
    }


def doc_muc_luc_mp4(buf: bytes) -> tuple[int, list[tuple[int, int, float]]] | None:
    """(độ dài đoạn mở đầu, các khúc ``(offset, độ dài, giây)``) hoặc None.

    Tệp tiếng YouTube là MP4 cắt mảnh: mục lục mẫu trong ``moov`` rỗng, thời lượng
    thật nằm ở hộp ``sidx``. Một tệp liền khiến iPhone chờ một lượng tỷ lệ với
    cả bài. Trả về từng mảnh (~10 giây) để phát mảnh đầu rồi tải tiếp.

    None khi không có ``sidx`` đủ trong ``buf``, hoặc một mảnh trỏ tới mục lục
    khác chứ không phải tiếng — người gọi giữ đường tệp liền.
    """
    i = 0
    sidx: tuple[int, int] | None = None
    while i + 8 <= len(buf):
        size = int.from_bytes(buf[i:i + 4], "big")
        typ = buf[i + 4:i + 8]
        hdr = 8
        if size == 1:
            if i + 16 > len(buf):
                return None
            size = int.from_bytes(buf[i + 8:i + 16], "big")
            hdr = 16
        if size < hdr or i + size > len(buf):
            return None
        if typ == b"sidx":
            sidx = (i, size)
            break
        i += size
    if sidx is None:
        return None
    dau, size = sidx
    p = dau + (16 if int.from_bytes(buf[dau:dau + 4], "big") == 1 else 8)
    if p + 4 > dau + size:
        return None
    ver = buf[p]
    p += 4  # version + flags
    if p + 8 > dau + size:
        return None
    p += 4  # reference_ID
    timescale = int.from_bytes(buf[p:p + 4], "big")
    p += 4
    if timescale <= 0:
        return None
    if ver == 0:
        if p + 8 > dau + size:
            return None
        p += 4  # earliest_presentation_time
        first_offset = int.from_bytes(buf[p:p + 4], "big")
        p += 4
    else:
        if p + 16 > dau + size:
            return None
        p += 8
        first_offset = int.from_bytes(buf[p:p + 8], "big")
        p += 8
    if p + 4 > dau + size:
        return None
    p += 2  # reserved
    count = int.from_bytes(buf[p:p + 2], "big")
    p += 2
    if count <= 0 or p + count * 12 > dau + size:
        return None
    khuc: list[tuple[int, int, float]] = []
    vi_tri = dau + size + first_offset
    for _ in range(count):
        tu = int.from_bytes(buf[p:p + 4], "big")
        dai = tu & 0x7FFFFFFF
        if tu >> 31:
            return None
        giay_don = int.from_bytes(buf[p + 4:p + 8], "big")
        p += 12
        if dai <= 0:
            return None
        khuc.append((vi_tri, dai, giay_don / timescale))
        vi_tri += dai
    return dau, khuc


def danh_sach_hls(khoi_dai: int, khuc: list[tuple[int, int, float]], url_khuc: str) -> str:
    """Danh sách phát HLS: đoạn mở đầu một lần, rồi từng khúc theo byte.

    iPhone phát sau khúc đầu (~10 giây tiếng) dù cả bài dài hàng giờ. ``url_khuc``
    là địa chỉ trả đúng khoảng byte được hỏi, không phải cả tệp.
    """
    if khoi_dai < 8 or not khuc or not url_khuc:
        raise ValueError("danh_sach_hls_rong")
    muc = max(1, math.ceil(max(giay for _, _, giay in khuc) - 1e-6))
    dong = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{muc}",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f'#EXT-X-MAP:URI="{url_khuc}",BYTERANGE="{khoi_dai}@0"',
    ]
    for bat_dau, dai, giay in khuc:
        dong.append(f"#EXTINF:{giay:.3f},")
        dong.append(f"#EXT-X-BYTERANGE:{dai}@{bat_dau}")
        dong.append(url_khuc)
    dong.append("#EXT-X-ENDLIST")
    return "\n".join(dong) + "\n"


def _la_luong_chi_tieng(item: dict) -> bool:
    """Đúng khi mục này là luồng CHỈ CÓ TIẾNG.

    Bắt buộc `acodec` phải có thật, chứ không chỉ xét `vcodec` rỗng. Hai tệp gộp sẵn
    "hd"/"sd" của Facebook KHÔNG khai codec nào (yt-dlp báo NA cả hai), nên điều kiện
    chỉ dựa vào `vcodec` sẽ nhận nhầm chúng là luồng tiếng rồi gắn sai kiểu nội dung.

    Dùng CHUNG cho cả chỗ chọn định dạng lẫn chỗ quyết kiểu nội dung — hai nơi cùng
    một câu hỏi thì phải cùng một câu trả lời, tách ra là mầm lệch về sau."""
    return item.get("acodec") not in (None, "none") and item.get("vcodec") in (None, "none")


def _facebook_muxed_format(info: dict, *, want_hd: bool) -> dict:
    """Tệp mp4 gộp sẵn của Facebook: "hd" trước, "sd" dự phòng (hoặc ngược lại).

    Chọn theo MÃ ĐỊNH DẠNG chứ không theo codec/chiều cao — xem chú thích ở
    `FACEBOOK_MUXED_FORMATS`: yt-dlp báo ba trường đó là NA cho hai định dạng này."""
    found: dict[str, dict] = {}
    for item in info.get("formats") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        name = str(item.get("format_id") or "").lower()
        if name in FACEBOOK_MUXED_FORMATS and str(item.get("ext") or "").lower() == "mp4":
            found.setdefault(name, item)
    for name in (("hd", "sd") if want_hd else ("sd", "hd")):
        if name in found:
            return found[name]
    # yt-dlp rút gọn về một luồng duy nhất khi người gọi đã chọn sẵn định dạng.
    if info.get("url") and str(info.get("ext") or "").lower() == "mp4":
        return info
    raise StreamUnavailableError("unsupported_stream_format")


def _facebook_audio_format(info: dict) -> dict:
    """Luồng CHỈ CÓ TIẾNG nếu Facebook có (đo được: m4a, mp4a.40.5, ~73 kbps, 48 kHz);
    không có thì dùng tệp gộp sẵn nhỏ hơn — loa vẫn phát ra tiếng từ mp4."""
    audio_only = [
        item
        for item in info.get("formats") or []
        if isinstance(item, dict) and item.get("url") and _la_luong_chi_tieng(item)
    ]
    if audio_only:
        return audio_only[-1]
    return _facebook_muxed_format(info, want_hd=False)


def _facebook_stream(selected: dict, info: dict) -> dict:
    """Kiểm máy chủ phát rồi dựng bản ghi luồng, đúng hình dạng của các nguồn khác."""
    stream_url = str(selected.get("url") or "")
    parsed_stream = urlsplit(stream_url)
    stream_host = (parsed_stream.hostname or "").lower()
    if parsed_stream.scheme not in {"http", "https"} or not any(
        stream_host == suffix or stream_host.endswith(f".{suffix}")
        for suffix in FACEBOOK_STREAM_HOSTS
    ):
        raise StreamUnavailableError("unsupported_stream_format")
    headers = {"User-Agent": ZING_USER_AGENT}
    upstream_headers = selected.get("http_headers") or info.get("http_headers")
    if isinstance(upstream_headers, dict):
        headers = {
            str(key): str(value)
            for key, value in upstream_headers.items()
            if key and value
        } or headers
    return {"url": stream_url, "headers": headers}


def _facebook_info(video_id: str, timeout: int, extractor) -> dict:
    try:
        info = extractor(f"https://www.facebook.com/watch/?v={video_id}", timeout)
    except Exception as error:  # yt-dlp ném nhiều loại cho cùng một kiểu hỏng
        raise StreamUnavailableError("stream_provider_failed") from error
    if not isinstance(info, dict):
        raise StreamUnavailableError("invalid_stream_response")
    return info


def resolve_facebook_audio(
    target: str, *, timeout: int = 20, extractor=extract_with_yt_dlp
) -> dict:
    """TIẾNG của một video Facebook, cho loa — tách riêng khỏi phần hình, đúng như
    cặp `youtube` / `youtube_video` đang làm."""
    video_id = validate_stream_target("facebook", target)
    info = _facebook_info(video_id, timeout, extractor)
    selected = _facebook_audio_format(info)
    stream = _facebook_stream(selected, info)
    extension = str(selected.get("ext") or "").lower()
    stream["content_type"] = (
        CONTENT_TYPES.get(extension, "audio/mp4")
        if _la_luong_chi_tieng(selected)
        else "video/mp4"
    )
    return stream


def resolve_facebook_video(
    target: str, *, timeout: int = 20, extractor=extract_with_yt_dlp
) -> dict:
    """HÌNH của một video Facebook cho thẻ <video> của trình duyệt.

    Khác bên YouTube ở một điểm đã đo: Facebook chỉ có tệp gộp sẵn, nên luồng này
    mang theo cả tiếng. Bên hiển thị tự tắt tiếng khi đang phát ra loa."""
    video_id, max_height = validate_stream_target("facebook_video", target).split(":")
    info = _facebook_info(video_id, timeout, extractor)
    selected = _facebook_muxed_format(info, want_hd=int(max_height) >= 720)
    stream = _facebook_stream(selected, info)
    stream["content_type"] = VIDEO_CONTENT_TYPES.get(
        str(selected.get("ext") or "").lower(), "video/mp4"
    )
    if isinstance(selected.get("height"), int):
        stream["height"] = selected["height"]
    if selected.get("tbr"):
        stream["bitrate_kbps"] = round(float(selected["tbr"]))
    return stream
