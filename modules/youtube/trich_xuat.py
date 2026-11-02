"""Trích xuất nguồn cho module youtube (NV277) — chuyển từ dich-vu/youtube-phat.

Chức năng ở đây cố tình NHỎ hơn dịch vụ cũ: chỉ giữ phần lõi cần cho
Home Assistant (tìm YouTube/Zing, giải link âm thanh YouTube). Lý do ghi
thẳng trong báo cáo NV277:
- Tìm YouTube: yt-dlp `ytsearch` (nạp lười, thiếu gói → lỗi rõ 502).
- Tìm Zing: endpoint gợi ý công khai của Zing (urllib, giống search.py của
  dịch vụ cũ: chỉ nhận entry type=1, status=1, privacy=1, playStatus=2,
  link https://...zingmp3.vn/bai-hat/...), giới hạn phản hồi 1 MiB.
- Giải luồng YouTube: yt-dlp, chọn format chỉ-âm-thanh tốt nhất, rồi tới
  format tốt nhất có âm thanh. Giải luồng Zing/Facebook: CHƯA chuyển
  (dịch vụ cũ dùng luồng lấy khoá web riêng của từng nguồn, chưa kiểm
  chứng lại trong module này) → trả TrichXuatUnavailable rõ ràng, lõi
  dịch thành 502, KHÔNG tự bịa link hay chuyển sang nguồn khác.
- Mọi hàm chặn đều chạy qua asyncio.to_thread ở phía module.py, KHÔNG
  gọi urllib/yt-dlp trực tiếp trong mã async.
- KHÔNG có logic vượt/né/giải chống bot: yt-dlp chạy đúng như người dùng
  chạy ngoài dòng lệnh; nguồn đòi đăng nhập/xác minh → lỗi rõ 502.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

log = logging.getLogger("web2api.youtube.trich_xuat")

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{10,}")
ZING_ID = re.compile(r"[A-Za-z0-9]{8}")
YOUTUBE_URL_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
ZING_SEARCH_URL = "https://ac.zingmp3.vn/v1/web/ac-suggestions"
SEARCH_RESPONSE_LIMIT = 1_048_576


class TrichXuatUnavailable(Exception):
    """Nguồn không giải được / gói trích xuất thiếu — dịch thành 502."""


def _validated_query_and_limit(query, limit, *, max_length=500):
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


def search_youtube(query, *, limit=20):
    """Tìm YouTube qua yt-dlp ytsearch (chặn — gọi qua to_thread)."""
    query, limit = _validated_query_and_limit(query, limit)
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for entry in (data or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("id") or "")
        if not VIDEO_ID.fullmatch(vid):
            continue
        results.append({
            "id": vid,
            "title": str(entry.get("title") or ""),
            "artists": [],
            "duration": entry.get("duration"),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "source": "youtube",
        })
    return results[:limit]


def resolve_youtube_audio(video_id):
    """Giải link âm thanh trực tiếp cho một video YouTube (chặn)."""
    yt_dlp = _yt_dlp()
    opts = {**_tuy_chon_chung(), "skip_download": True, "format": "bestaudio/best"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}",
                                download=False)
    if not isinstance(info, dict):
        raise TrichXuatUnavailable("resolve_failed")
    url = info.get("url")
    if not url:
        for fmt in info.get("formats") or []:
            if isinstance(fmt, dict) and fmt.get("url") and fmt.get("acodec") != "none":
                url = fmt["url"]
                break
    if not url:
        raise TrichXuatUnavailable("resolve_failed")
    return {
        "url": url,
        "title": str(info.get("title") or ""),
        "duration": info.get("duration"),
        "source": "youtube",
        "video_id": video_id,
    }


class TrichXuat:
    """Cổng trích xuất async của module: mọi hàm chặn chạy trong to_thread."""

    async def tim_kiem(self, source, query, limit):
        if source == "youtube":
            return await asyncio.to_thread(search_youtube, query, limit=limit)
        if source == "zing":
            return await asyncio.to_thread(search_zing, query, limit=limit)
        raise TrichXuatUnavailable("search_source_unavailable")

    async def giai_am_thanh(self, source, target_id):
        if source == "youtube":
            return await asyncio.to_thread(resolve_youtube_audio, target_id)
        raise TrichXuatUnavailable("resolve_source_unavailable")
