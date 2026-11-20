"""Trích xuất nguồn cho module youtube (NV277) — chuyển từ dich-vu/youtube-phat.

Chức năng ở đây cố tình NHỎ hơn dịch vụ cũ: chỉ giữ phần lõi cần cho
Home Assistant (tìm YouTube/Zing, giải link âm thanh YouTube). Lý do ghi
thẳng trong báo cáo NV277:
- Tìm YouTube: yt-dlp `ytsearch` (nạp lười, thiếu gói → lỗi rõ 502).
  NV301 bổ sung: kết quả tìm có `channel`/`thumbnail`/`kind` (hình dạng
  dịch vụ cũ), giải âm thanh trả thêm `channel`/`thumbnail`, và lấy
  playlist YouTube công khai qua yt-dlp (extract_flat).
- Tìm Zing: endpoint gợi ý công khai của Zing (urllib, giống search.py của
  dịch vụ cũ: chỉ nhận entry type=1, status=1, privacy=1, playStatus=2,
  link https://...zingmp3.vn/bai-hat/...), giới hạn phản hồi 1 MiB.
- Giải luồng YouTube: yt-dlp, chọn format chỉ-âm-thanh tốt nhất, rồi tới
  format tốt nhất có âm thanh. NV304c bổ sung: giải luồng Zing (port từ
  streaming.py của dịch vụ cũ: khoá API web lấy từ trang chủ zingmp3.vn,
  ký HMAC-SHA512, chỉ nhận host CDN cho phép, ưu tiên 320 rồi 128) và
  giải luồng Facebook (yt-dlp trên https://www.facebook.com/watch/?v=<id>,
  ưu tiên luồng chỉ-tiếng, không có thì tệp gộp nhỏ hơn, chỉ nhận host
  fbcdn.net). Không giải được → TrichXuatUnavailable rõ ràng, lõi dịch
  thành 502, KHÔNG tự bịa link hay chuyển sang nguồn khác.
- Mọi hàm chặn đều chạy qua asyncio.to_thread ở phía module.py, KHÔNG
  gọi urllib/yt-dlp trực tiếp trong mã async.
- KHÔNG có logic vượt/né/giải chống bot: yt-dlp chạy đúng như người dùng
  chạy ngoài dòng lệnh; nguồn đòi đăng nhập/xác minh → lỗi rõ 502.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urljoin, urlsplit

import httpx

log = logging.getLogger("web2api.youtube.trich_xuat")

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,}")
ZING_ID = re.compile(r"[A-Za-z0-9]{8,16}")
YOUTUBE_URL_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
ZING_SEARCH_URL = "https://ac.zingmp3.vn/v1/web/ac-suggestions"
SEARCH_RESPONSE_LIMIT = 1_048_576
_KIEU_AM_THANH = {
    "mp4": "audio/mp4", "m4a": "audio/mp4", "webm": "audio/webm",
    "mp3": "audio/mpeg", "ogg": "audio/ogg", "oga": "audio/ogg",
    "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac",
    "wav": "audio/wav",
}


class TrichXuatUnavailable(Exception):
    """Nguồn không giải được / gói trích xuất thiếu — dịch thành 502."""


def _validated_query_and_limit(query, limit, *, max_length=500):
    query = str(query or "").strip()
    if not 1 <= len(query) <= max_length:
        raise ValueError("invalid_search_query")
    try:
        limit = int(limit)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_search_query") from error
    if not 1 <= limit <= 30:
        raise ValueError("invalid_search_query")
    return query, limit


def youtube_url_query(query):
    """Trả ("video", id) / ("playlist", id) nếu query là link YouTube, else None."""
    text = str(query or "").strip()
    if not text or len(text) > 2048 or any(ch.isspace() for ch in text):
        return None
    if "://" not in text:
        text = f"https://{text}"
    from urllib.parse import parse_qs

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


def _zing_suggestions(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    groups = data.get("items") if isinstance(data, dict) else []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        suggestions = group.get("suggestions")
        if isinstance(suggestions, list):
            yield from suggestions


def parse_zing_payload(payload, *, limit):
    """Chuyển gợi ý Zing thành dạng kết quả ổn định (giống dịch vụ cũ)."""
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
            or not (song_host == "zingmp3.vn" or song_host.endswith(".zingmp3.vn"))
            or not parsed_url.path.startswith("/bai-hat/")
            or entry.get("status") != 1
            or entry.get("privacy") != 1
            or entry.get("playStatus") != 2
        ):
            continue
        artists = entry.get("artists") or []
        artist_names = [str(a.get("name")) for a in artists
                        if isinstance(a, dict) and a.get("name")]
        duration = entry.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        results.append({
            "id": song_id,
            "title": str(entry.get("title") or ""),
            "artists": artist_names,
            # NV301: hình dạng dịch vụ cũ cần `channel` + `thumbnail`.
            "channel": ", ".join(artist_names),
            "thumbnail": str(entry.get("thumbnail") or entry.get("thumb") or ""),
            "kind": "song",
            "duration": duration,
            "url": song_url,
            "source": "zing",
        })
        if len(results) >= limit:
            break
    return results


def search_zing(query, *, limit=20, timeout=10):
    """Tìm Zing qua endpoint gợi ý công khai (chặn — gọi qua to_thread).

    Dùng httpx sync (đã là gói của lõi) thay vì urllib.urlopen để khỏi
    vướng B310; httpx tự giải nén gzip nên chỉ cần chặn theo độ dài
    nội dung đã giải nén.
    """
    import httpx

    query, limit = _validated_query_and_limit(query, limit)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.get(
                f"{ZING_SEARCH_URL}?query={quote_plus(query)}&num={limit}",
                headers={"Accept": "application/json",
                         "User-Agent": "TriTue-YouTube-Player/0.6"})
            body = resp.content
            if len(body) > SEARCH_RESPONSE_LIMIT:
                raise TrichXuatUnavailable("search_response_too_large")
        payload = json.loads(body)
    except TrichXuatUnavailable:
        raise
    except Exception as e:  # OSError / ValueError / JSON lỗi → lỗi nguồn rõ
        raise TrichXuatUnavailable("search_provider_failed") from e
    if not isinstance(payload, dict) or payload.get("err") != 0:
        raise TrichXuatUnavailable("invalid_search_response")
    return parse_zing_payload(payload, limit=limit)


def _yt_dlp():
    try:
        import yt_dlp
    except ImportError as error:
        raise TrichXuatUnavailable("yt_dlp_missing") from error
    return yt_dlp


def _deno_path() -> str:
    """Deno (gói PyPI `deno`) cho yt-dlp giải thử thách JS của YouTube — như dịch vụ cũ: ưu tiên tệp chạy
    cạnh python, không thì tìm trên PATH. Không có thì "" (yt-dlp tự báo thiếu một số định dạng)."""
    canh = Path(sys.executable).with_name("deno")
    if canh.is_file():
        return str(canh)
    return shutil.which("deno") or ""


def _tuy_chon_chung() -> dict:
    """Tuỳ chọn yt-dlp dùng chung. Container chạy uid 1000 không có thư mục nhà ghi được: bộ nhớ đệm của
    yt-dlp và deno đặt trong thư mục tạm, không đoán $HOME."""
    goc_tam = Path(tempfile.gettempdir())
    os.environ.setdefault("DENO_DIR", str(goc_tam / "deno"))
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "socket_timeout": 20, "extractor_retries": 1,
            "cachedir": str(goc_tam / "yt-dlp")}
    if deno := _deno_path():
        opts["js_runtimes"] = {"deno": {"path": deno}}
    return opts


def _muc_youtube(entry):
    """Một entry yt-dlp (flat) → mục tìm kiếm ổn định (hình dạng dịch vụ cũ).

    Kênh từ `channel`/`uploader`; ảnh bìa từ `thumbnails` (lấy ảnh cuối
    có URL, như parse_search_payload của dịch vụ cũ), rồi `thumbnail`,
    rồi ảnh mặc định i.ytimg.com — không bao giờ để trống với YouTube."""
    if not isinstance(entry, dict):
        return None
    vid = str(entry.get("id") or "")
    if not VIDEO_ID.fullmatch(vid):
        return None
    thumbnails = entry.get("thumbnails") or []
    thumbnail = next(
        (str(c.get("url")) for c in reversed(thumbnails)
         if isinstance(c, dict) and c.get("url")),
        str(entry.get("thumbnail") or ""),
    )
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    try:
        duration = int(entry.get("duration")) if entry.get("duration") is not None else None
    except (TypeError, ValueError):
        duration = None
    return {
        "source": "youtube",
        "kind": "video",
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": str(entry.get("title") or vid),
        "channel": str(entry.get("channel") or entry.get("uploader") or ""),
        "artists": [],
        "duration": duration,
        "thumbnail": thumbnail,
    }


def search_youtube(query, *, limit=20):
    """Tìm YouTube qua yt-dlp ytsearch (chặn — gọi qua to_thread)."""
    query, limit = _validated_query_and_limit(query, limit)
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in (data or {}).get("entries") or []:
        muc = _muc_youtube(entry)
        if muc:
            results.append(muc)
    return results[:limit]


def lay_danh_sach_phat_youtube(playlist_id, *, limit=500):
    """(tên, các bài) của một playlist YouTube công khai — để nhập playlist.

    Giống fetch_youtube_playlist của dịch vụ cũ nhưng qua thư viện yt-dlp
    (module không chạy tiến trình con): extract_flat, không tải, giới hạn
    500 bài như cũ. Playlist riêng tư/không tồn tại → yt-dlp ném →
    TrichXuatUnavailable (đường integration dịch thành 502 rõ)."""
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(
                f"https://www.youtube.com/playlist?list={playlist_id}",
                download=False)
    except Exception as e:
        raise TrichXuatUnavailable("playlist_unavailable") from e
    if not isinstance(data, dict):
        raise TrichXuatUnavailable("playlist_unavailable")
    ten = str(data.get("title") or "").strip() or "Playlist YouTube"
    muc = []
    for entry in data.get("entries") or []:
        item = _muc_youtube(entry)
        if item:
            muc.append(item)
        if len(muc) >= int(limit):
            break
    return ten, muc


def resolve_youtube_audio(video_id):
    """Giải link âm thanh trực tiếp cho một video YouTube (chặn)."""
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "skip_download": True, "format": "bestaudio/best"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}",
                                download=False)
    if not isinstance(info, dict):
        raise TrichXuatUnavailable("resolve_failed")
    chosen = info
    url = info.get("url")
    if not url:
        for fmt in info.get("formats") or []:
            if isinstance(fmt, dict) and fmt.get("url") and fmt.get("acodec") != "none":
                chosen = fmt
                url = fmt["url"]
                break
    if not url:
        raise TrichXuatUnavailable("resolve_failed")
    # NV304d: khôi phục kiểm host của dịch vụ cũ (YOUTUBE_STREAM_HOSTS)
    # — bị đánh rơi khi viết lại resolve này ở NV301 (Facebook/Zing
    # vẫn còn lọc host, YouTube thì không).
    if not _host_la_cho_phep(url, YOUTUBE_STREAM_HOSTS):
        raise TrichXuatUnavailable("youtube_host_rejected")
    thumbnails = info.get("thumbnails") or []
    thumbnail = next(
        (str(c.get("url")) for c in reversed(thumbnails)
         if isinstance(c, dict) and c.get("url")),
        str(info.get("thumbnail") or ""),
    )
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    ext = str(chosen.get("ext") or urlsplit(url).path.rsplit(".", 1)[-1]).lower()
    return {
        "url": url,
        "content_type": _KIEU_AM_THANH.get(ext, "audio/mpeg"),
        "title": str(info.get("title") or ""),
        "duration": info.get("duration"),
        "source": "youtube",
        "video_id": video_id,
        # NV301: siêu dữ liệu cho phiên (title/channel/thumbnail) — thêm
        # trường, không đổi hợp đồng cũ của hàm này.
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "thumbnail": thumbnail,
    }


# ── NV304c: Zing + Facebook (port từ dich-vu/youtube-phat/streaming.py)
# Zing: khoá API web KHÔNG nằm trong mã — lấy từ trang chủ zingmp3.vn
# (bó main.min.js), Zing đổi khoá thì tự theo. urllib của dịch vụ cũ
# đổi sang httpx (module đã dùng httpx cho search Zing; httpx tự giải
# nén gzip nên chỉ chặn theo độ dài nội dung đã giải nén).

ZING_API_BASE = "https://zingmp3.vn"
ZING_HOME_URL = "https://zingmp3.vn/"
ZING_API_PATH = "/api/v2/song/get/streaming"
ZING_PLAYLIST_PATH = "/api/v2/page/get/playlist"
ZING_WEB_BUNDLE = re.compile(
    r'src="(https://[a-z0-9.-]+\.zmdcdn\.me/[^"]+/main\.min\.js)"')
ZING_WEB_KEY_PAIR = re.compile(
    r'="([A-Za-z0-9]{32})",[A-Za-z_$][\w$]{0,3}="([A-Za-z0-9]{32})"')
ZING_WEB_HOSTS = ("zingmp3.vn",)
ZING_BUNDLE_HOSTS = ("zmdcdn.me",)
ZING_WEB_VERSION = "1.20.4"
ZING_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
ZING_CDN_HOSTS = ("zmdcdn.me", "zadn.vn", "zing.vn", "zingmp3.vn")
ZING_CONTENT_TYPES = {
    "aac": "audio/aac", "m3u8": "application/vnd.apple.mpegurl",
    "flac": "audio/flac", "m4a": "audio/mp4", "mp3": "audio/mpeg",
    "ogg": "audio/ogg", "opus": "audio/opus", "wav": "audio/wav",
    "webm": "audio/webm",
}
ZING_HOME_LIMIT = 1_048_576
ZING_BUNDLE_LIMIT = 8_388_608
ZING_KEYS_TTL = 3600
FACEBOOK_STREAM_HOSTS = ("fbcdn.net",)
YOUTUBE_STREAM_HOSTS = ("googlevideo.com",)
FACEBOOK_ID = re.compile(r"^[0-9]{5,25}$")


def _host_la_cho_phep(url, hosts):
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.username is not None
                or parts.port not in (None, 443)
                or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", host)):
            return False
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in hosts)


def _lay_zing(client, url, hosts, *, headers=None):
    """Kiểm đích HTTPS trước mỗi lượt GET, kể cả Location của lượt trước."""
    for _ in range(11):
        if not _host_la_cho_phep(url, hosts):
            raise TrichXuatUnavailable("zing_target_rejected")
        resp = client.get(url, headers=headers, follow_redirects=False)
        location = resp.headers.get("location")
        if getattr(resp, "status_code", 200) not in (301, 302, 303, 307, 308) or not location:
            return resp
        url = urljoin(url, location)
    raise TrichXuatUnavailable("zing_too_many_redirects")


def extract_zing_web_keys(script):
    """(api_key, api_secret) từ mã bó web Zing — None nếu không tìm thấy."""
    m = ZING_WEB_KEY_PAIR.search(str(script or ""))
    if not m:
        return None
    return {"api_key": m.group(1), "api_secret": m.group(2)}


def _build_zing_api_url(song_id, version, current_time, api_key, api_secret,
                        path=ZING_API_PATH):
    params = {"id": song_id, "ctime": str(current_time), "version": version}
    canonical = "".join(f"{k}={params[k]}" for k in sorted(params))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    sig = hmac.new(api_secret.encode(), f"{path}{digest}".encode(),
                   hashlib.sha512).hexdigest()
    query = urlencode([*sorted(params.items()), ("apiKey", api_key), ("sig", sig)])
    return f"{ZING_API_BASE}{path}?{query}"


def fetch_zing_web_keys(client=None):
    """Khoá API web Zing: GET trang chủ → bó main.min.js → trích cặp key.

    client: httpx.Client (hoặc giả có .get/.cookies) — test chích giả.
    """
    own = client is None
    if own:
        client = httpx.Client(timeout=10,
                              headers={"User-Agent": ZING_USER_AGENT})
    try:
        home = _lay_zing(client, ZING_HOME_URL, ZING_WEB_HOSTS)
        if len(home.content) > ZING_HOME_LIMIT:
            raise TrichXuatUnavailable("zing_home_too_large")
        m = ZING_WEB_BUNDLE.search(home.content.decode("utf-8", "ignore"))
        if not m:
            raise TrichXuatUnavailable("zing_bundle_not_found")
        bundle = _lay_zing(client, m.group(1), ZING_BUNDLE_HOSTS)
        if len(bundle.content) > ZING_BUNDLE_LIMIT:
            raise TrichXuatUnavailable("zing_bundle_too_large")
        keys = extract_zing_web_keys(bundle.content.decode("utf-8", "ignore"))
        if not keys:
            raise TrichXuatUnavailable("zing_keys_not_found")
        return keys
    finally:
        if own:
            client.close()


def _zing_version(client):
    try:
        return client.cookies.get("zmp3_app_version.1") or ZING_WEB_VERSION
    except Exception:
        return ZING_WEB_VERSION


def fetch_zing_playlist(text, api_key=None, api_secret=None, client=None,
                        now=None):
    """(tên, các bài) của album/playlist Zing — chỉ bài streamingStatus 1.

    Bài VIP (status 2) bị bỏ như dịch vụ cũ; link bài dạng /bai-hat/.
    """
    pid = zing_playlist_id(text)
    if not pid:
        raise ValueError("invalid_zing_playlist")
    own = client is None
    if own:
        client = httpx.Client(timeout=10,
                              headers={"User-Agent": ZING_USER_AGENT})
    try:
        if api_key is None or api_secret is None:
            keys = fetch_zing_web_keys(client=client)
            api_key, api_secret = keys["api_key"], keys["api_secret"]
        _lay_zing(client, ZING_HOME_URL, ZING_WEB_HOSTS)  # lấy cookie phiên bản web
        url = _build_zing_api_url(pid, _zing_version(client),
                                  int(now or time.time()), api_key, api_secret,
                                  path=ZING_PLAYLIST_PATH)
        # URL mang apiKey và sig: không chuyển tiếp tới bất kỳ đích nào khác.
        resp = client.get(url, headers={"User-Agent": ZING_USER_AGENT},
                          follow_redirects=False)
        payload = json.loads(resp.content.decode("utf-8", "ignore"))
        if payload.get("err") != 0:
            raise TrichXuatUnavailable("zing_playlist_failed")
        data = payload.get("data") or {}
        items = ((data.get("song") or {}).get("items")) or []
        muc = []
        for it in items:
            if not isinstance(it, dict) or it.get("streamingStatus") != 1:
                continue
            link = str(it.get("link") or "")
            if "/bai-hat/" not in link:
                continue
            encode_id = str(it.get("encodeId") or "")
            if not ZING_ID.fullmatch(encode_id):
                continue
            muc.append({"source": "zing", "kind": "song", "id": encode_id,
                        "url": f"https://zingmp3.vn{link}",
                        "title": str(it.get("title") or ""),
                        "channel": str(it.get("artistsNames") or ""),
                        "duration": it.get("duration"),
                        "thumbnail": str(it.get("thumbnailM") or "")})
        return str(data.get("title") or ""), muc
    finally:
        if own:
            client.close()


def zing_playlist_id(text):
    """ID 8–16 ký tự từ link album/playlist Zing — None nếu không phải."""
    try:
        parts = urlsplit(str(text or "").strip())
    except ValueError:
        return None
    if not _host_la_cho_phep(parts.geturl(), ZING_WEB_HOSTS):
        return None
    seg = [s for s in parts.path.split("/") if s]
    if len(seg) < 2 or seg[0] not in ("album", "playlist"):
        return None
    tail = seg[-1].removesuffix(".html")
    return tail if ZING_ID.fullmatch(tail) else None


def resolve_zing_stream(target_url, api_key=None, api_secret=None, client=None,
                        now=None):
    """Giải link âm thanh trực tiếp cho một bài Zing (chặn)."""
    own = client is None
    if own:
        client = httpx.Client(timeout=10,
                              headers={"User-Agent": ZING_USER_AGENT})
    try:
        if api_key is None or api_secret is None:
            keys = fetch_zing_web_keys(client=client)
            api_key, api_secret = keys["api_key"], keys["api_secret"]
        # Cố định origin; không chuyển query/fragment của bên gọi thành Referer.
        target = str(target_url or "").strip()
        if ZING_ID.fullmatch(target):
            target = f"https://zingmp3.vn/bai-hat/{target}.html"
        parts = urlsplit(target)
        if not _host_la_cho_phep(target, ZING_WEB_HOSTS):
            raise TrichXuatUnavailable("zing_target_rejected")
        if not parts.path.startswith("/bai-hat/"):
            raise TrichXuatUnavailable("zing_target_rejected")
        song_id = parts.path.rstrip("/").split("/")[-1].removesuffix(".html")
        if not ZING_ID.fullmatch(song_id):
            raise TrichXuatUnavailable("zing_target_rejected")
        target = ZING_API_BASE + parts.path
        resp = _lay_zing(client, target, ZING_WEB_HOSTS,
                        headers={"User-Agent": ZING_USER_AGENT})
        final = urlsplit(str(resp.url or target))
        song_id = final.path.rstrip("/").split("/")[-1].removesuffix(".html")
        if (not _host_la_cho_phep(final.geturl(), ZING_WEB_HOSTS)
                or not final.path.startswith("/bai-hat/") or not ZING_ID.fullmatch(song_id)):
            raise TrichXuatUnavailable("zing_redirect_rejected")
        final_url = final._replace(query="", fragment="").geturl()
        api_url = _build_zing_api_url(song_id, _zing_version(client),
                                      int(now or time.time()),
                                      api_key, api_secret)
        api = client.get(api_url, headers={"User-Agent": ZING_USER_AGENT,
                                           "Referer": final_url}, follow_redirects=False)
        payload = json.loads(api.content.decode("utf-8", "ignore"))
        if payload.get("err") != 0:
            raise TrichXuatUnavailable("zing_stream_failed")
        data = payload.get("data") or {}
        for chat in ("320", "128"):
            url = data.get(chat)
            if url and _host_la_cho_phep(url, ZING_CDN_HOSTS):
                ext = urlsplit(url).path.rsplit(".", 1)[-1].lower()
                return {"url": url, "title": "", "duration": data.get("duration"),
                        "source": "zing",
                        "headers": {"Referer": final_url,
                                    "User-Agent": ZING_USER_AGENT},
                        "content_type": ZING_CONTENT_TYPES.get(ext, "audio/mpeg")}
        raise TrichXuatUnavailable("zing_stream_not_found")
    finally:
        if own:
            client.close()


def resolve_facebook_audio(video_id):
    """Giải link âm thanh cho video Facebook (chặn, qua yt-dlp).

    Địa chỉ chuẩn /watch/?v=<id> (phủ cả reel lẫn video thường — dịch vụ
    cũ từng dùng /video.php?v= rồi kết luận sai «chỉ dành cho người dùng
    đã đăng ký»). Chọn luồng chỉ-tiếng nếu có, không có thì tệp gộp nhỏ
    hơn (chọn theo MÃ ĐỊNH DẠNG vì yt-dlp báo codec/chiều cao đều NA với
    tệp gộp mp4 của Facebook). Chỉ nhận host fbcdn.net.
    """
    if not FACEBOOK_ID.fullmatch(str(video_id or "")):
        raise TrichXuatUnavailable("invalid_facebook_id")
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.facebook.com/watch/?v={video_id}",
                                download=False)
    if not isinstance(info, dict):
        raise TrichXuatUnavailable("resolve_failed")
    formats = [f for f in (info.get("formats") or []) if isinstance(f, dict)]
    audio_only = [f for f in formats
                  if f.get("acodec") not in (None, "none")
                  and f.get("vcodec") in (None, "none") and f.get("url")]
    if audio_only:
        chosen = audio_only[-1]
    else:
        # Tệp gộp: ưu tiên mã sd rồi hd (nhỏ hơn trước), không lọc codec.
        by_id = {str(f.get("format_id")): f for f in formats if f.get("url")}
        chosen = by_id.get("sd") or by_id.get("hd")
        if chosen is None and info.get("url"):
            chosen = info
    if not chosen or not chosen.get("url"):
        raise TrichXuatUnavailable("resolve_failed")
    if not _host_la_cho_phep(chosen["url"], FACEBOOK_STREAM_HOSTS):
        raise TrichXuatUnavailable("facebook_host_rejected")
    headers = chosen.get("http_headers") or info.get("http_headers")
    if not isinstance(headers, dict):
        headers = {"User-Agent": ZING_USER_AGENT}
    extension = str(chosen.get("ext") or urlsplit(chosen["url"]).path.rsplit(".", 1)[-1]).lower()
    content_type = (_KIEU_AM_THANH.get(extension, "audio/mp4")
                    if audio_only else "video/mp4")
    return {"url": chosen["url"], "title": str(info.get("title") or ""),
            "duration": info.get("duration"), "source": "facebook",
            "headers": dict(headers), "content_type": content_type,
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "thumbnail": str(info.get("thumbnail") or "")}


class TrichXuat:
    """Cổng trích xuất async của module: mọi hàm chặn chạy trong to_thread."""

    def __init__(self):
        # Đệm khoá web Zing trong instance, TTL 3600 s như lõi dịch vụ cũ
        # đệm zing_keys — tránh GET trang chủ + bó JS mỗi lần giải.
        self._zing_keys = None
        self._zing_keys_luc = 0

    def _lay_khoa_zing(self):
        import time as _time
        if (self._zing_keys is not None
                and _time.time() - self._zing_keys_luc < ZING_KEYS_TTL):
            return self._zing_keys
        keys = fetch_zing_web_keys()
        self._zing_keys = keys
        self._zing_keys_luc = _time.time()
        return keys

    async def tim_kiem(self, source, query, limit):
        if source == "youtube":
            return await asyncio.to_thread(search_youtube, query, limit=limit)
        if source == "zing":
            return await asyncio.to_thread(search_zing, query, limit=limit)
        raise TrichXuatUnavailable("search_source_unavailable")

    async def giai_am_thanh(self, source, target_id):
        if source == "youtube":
            return await asyncio.to_thread(resolve_youtube_audio, target_id)
        if source == "zing":
            keys = await asyncio.to_thread(self._lay_khoa_zing)
            return await asyncio.to_thread(
                resolve_zing_stream, target_id,
                api_key=keys["api_key"], api_secret=keys["api_secret"])
        if source == "facebook":
            return await asyncio.to_thread(resolve_facebook_audio, target_id)
        raise TrichXuatUnavailable("resolve_source_unavailable")

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        """(tên, các bài) của một playlist theo nguồn.

        YouTube qua yt-dlp (không cần khoá). NV304c bổ sung Zing qua API
        web có ký (khoá lấy từ trang chủ, đệm trong instance)."""
        if source == "youtube":
            return await asyncio.to_thread(
                lay_danh_sach_phat_youtube, playlist_id, limit=limit)
        if source == "zing":
            keys = await asyncio.to_thread(self._lay_khoa_zing)
            return await asyncio.to_thread(
                fetch_zing_playlist, playlist_id,
                api_key=keys["api_key"], api_secret=keys["api_secret"])
        raise TrichXuatUnavailable("playlist_source_unavailable")
