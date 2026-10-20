"""CHÉP từ c2a (TriTue2011/chatgpt2api), services/youtube_phat/session.py, commit d879b03 — CHỈ ĐỌC nguồn, không sửa c2a.
Đổi so với bản gốc: thêm đầu tệp này; giữ nguyên (phiên chỉ RAM, không lưu đĩa — đúng quy tắc «dừng là dừng hẳn»).

Clean-room playback-session model shared by the add-on API and Web UI."""

# Chuyển nguyên từ add-on TriTue YouTube Player
# (github.com/TriTue2011/youtube, youtube_player/app/session.py, bản 0.7.0 — nhiều phiên
# theo nhóm loa) — chủ máy 14/09/2026: "tích hợp trực tiếp trên dự án, và ha có thể
# kết nối đến"; "Làm mỗi loa một bài hoặc chạy cùng 1 bài". Giữ nguyên tên hàm và
# mã lỗi để tích hợp HA của repo nói chuyện được với c2a như với add-on.


from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


MEDIA_PLAYER_ENTITY_ID = re.compile(r"^media_player\.[a-z0-9_]+$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}
DIRECT_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}
ALLOWED_DIRECT_CONTENT_TYPES = {
    *DIRECT_AUDIO_TYPES.values(),
    "application/x-mpegurl",
}
MAX_QUEUE_ITEMS = 30


def _bounded_text(value, maximum):
    text = str(value or "").strip()
    return text[:maximum]


def _safe_url(value):
    url = _bounded_text(value, 4096)
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return url


def normalize_session_item(source, item):
    """Return bounded media metadata safe to expose through Home Assistant."""
    if not isinstance(item, dict):
        item = {}
    normalized = {
        "source": source,
        "kind": _bounded_text(item.get("kind"), 32) or "song",
        "id": _bounded_text(item.get("id"), 4096),
        "url": _safe_url(item.get("url")),
        "title": _bounded_text(item.get("title") or item.get("id"), 512),
        "artist": _bounded_text(
            item.get("artist") or item.get("channel"), 512
        ),
        "album": _bounded_text(item.get("album"), 512),
        "thumbnail": _safe_url(item.get("thumbnail")),
        "duration": 0,
    }
    try:
        normalized["duration"] = max(0, int(float(item.get("duration") or 0)))
    except (TypeError, ValueError, OverflowError):
        pass
    for key in ("embed_url",):
        if value := _safe_url(item.get(key)):
            normalized[key] = value
    for key in ("playlist_id", "media_content_type"):
        if value := _bounded_text(item.get(key), 256):
            normalized[key] = value
    return normalized


def validate_output_entity_ids(value):
    """Validate, deduplicate and bound physical Home Assistant targets."""
    if not isinstance(value, list):
        raise ValueError("invalid_output_entity_ids")
    output = []
    for candidate in value:
        entity_id = str(candidate or "").strip()
        if not MEDIA_PLAYER_ENTITY_ID.fullmatch(entity_id):
            raise ValueError("invalid_output_entity_ids")
        if entity_id not in output:
            output.append(entity_id)
    if len(output) > 16:
        raise ValueError("invalid_output_entity_ids")
    return output


def build_direct_http_item(target, media_content_type=""):
    """Validate one direct HTTP audio URL without fetching the remote resource."""
    target = _safe_url(target)
    if not target or len(target) > 2048:
        raise ValueError("invalid_http_audio_target")
    parsed = urlsplit(target)
    hostname = (parsed.hostname or "").lower()
    if hostname in YOUTUBE_HOSTS or hostname.endswith(".youtube.com"):
        raise ValueError("invalid_http_audio_target")
    suffix = PurePosixPath(parsed.path).suffix.lower()
    inferred_type = DIRECT_AUDIO_TYPES.get(suffix, "audio/mpeg")
    content_type = _bounded_text(media_content_type, 128) or inferred_type
    if content_type not in ALLOWED_DIRECT_CONTENT_TYPES:
        raise ValueError("invalid_http_audio_target")
    filename = unquote(PurePosixPath(parsed.path).name) or parsed.hostname
    return normalize_session_item(
        "http",
        {
            "kind": "audio",
            "id": target,
            "url": target,
            "title": filename,
            "artist": "HTTP Audio",
            "media_content_type": content_type,
        },
    )


SESSION_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
WEB_SESSION_ID = "web"
MAX_SESSIONS = 32


def validate_session_id(value):
    session_id = str(value or "").strip()
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid_session_id")
    return session_id


class PlaybackSession:
    """In-memory playback sessions shared by every Home Assistant client.

    One session per group of speakers: each speaker belongs to at most one
    session, so "every speaker its own song" and "several speakers, one song"
    are both just sessions. Playing on a speaker moves it out of any other
    session; a session left without speakers ends. The web page player (no
    speakers) keeps the reserved session "web".

    API v1 clients keep working: ``snapshot()`` is the most recently changed
    session, ``stop()`` without a session stops by revision or stops all.
    """

    def __init__(self):
        self._search_queues = {}
        self._revision = 0
        self._sessions = {}

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @classmethod
    def _idle_session(cls, revision, session_id=""):
        return {
            "session_id": session_id,
            "revision": revision,
            "state": "idle",
            "position": 0,
            "duration": 0,
            "updated_at": cls._timestamp(),
            "volume_level": None,
            "item": None,
            "queue": {"index": -1, "items": []},
            "output_entity_ids": [],
            "supported_actions": [],
            "controller": "",
            "auto_advance": False,
        }

    def remember_search(self, source, results):
        queue = [
            normalize_session_item(source, item)
            for item in list(results or [])[:MAX_QUEUE_ITEMS]
            if isinstance(item, dict)
        ]
        self._search_queues[source] = queue

    def _session_id_for(self, requested, outputs):
        if requested:
            return validate_session_id(requested)
        if not outputs:
            return WEB_SESSION_ID
        wanted = set(outputs)
        for session_id, session in self._sessions.items():
            if set(session["output_entity_ids"]) == wanted:
                return session_id
        return f"s{self._revision + 1}"

    def start(
        self,
        source,
        target,
        *,
        fallback_item=None,
        output_entity_ids=None,
        media_content_type="",
        volume_level=None,
        session_id=None,
        controller="",
        auto_advance=True,
        queue_items=None,
    ):
        outputs = validate_output_entity_ids(output_entity_ids or [])
        session_id = self._session_id_for(session_id, outputs)
        if volume_level is not None:
            try:
                volume_level = float(volume_level)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("invalid_volume_level") from error
            if not 0 <= volume_level <= 1:
                raise ValueError("invalid_volume_level")
        if source == "http":
            item = build_direct_http_item(target, media_content_type)
            queue = [item]
            index = 0
        else:
            fallback = normalize_session_item(source, fallback_item or {})
            target_text = str(target or "").strip()

            def find(candidates):
                return next(
                    (
                        position
                        for position, candidate in enumerate(candidates)
                        if candidate.get("source", source) == source
                        and (
                            target_text in {candidate.get("id"), candidate.get("url")}
                            or (
                                fallback.get("id")
                                and fallback.get("id") == candidate.get("id")
                            )
                        )
                    ),
                    -1,
                )

            # Next/previous inside a session keeps that session's own queue,
            # even after someone searched for something else.
            existing = self._sessions.get(session_id)
            queue = existing["queue"]["items"] if existing else []
            index = -1
            if queue_items:
                # Playing a saved playlist: its songs are the queue.
                queue = [dict(candidate) for candidate in queue_items if isinstance(candidate, dict)]
                index = find(queue)
            if index < 0:
                queue = existing["queue"]["items"] if existing else []
                index = find(queue)
            if index < 0:
                queue = self._search_queues.get(source, [])
                index = find(queue)
            if index >= 0:
                item = dict(queue[index])
                for key in ("embed_url", "playlist_id"):
                    if fallback.get(key):
                        item[key] = fallback[key]
            else:
                item = fallback
                queue = [item]
                index = 0
            if media_content_type:
                item["media_content_type"] = _bounded_text(
                    media_content_type, 128
                )
        if not item.get("id"):
            raise ValueError("invalid_session_target")
        # A speaker plays one thing at a time: take it out of other sessions.
        for other_id in list(self._sessions):
            if other_id == session_id:
                continue
            other = self._sessions[other_id]
            if not other["output_entity_ids"]:
                continue
            remaining = [e for e in other["output_entity_ids"] if e not in outputs]
            if not remaining:
                del self._sessions[other_id]
            elif len(remaining) != len(other["output_entity_ids"]):
                self._revision += 1
                other["output_entity_ids"] = remaining
                other["revision"] = self._revision
        self._revision += 1
        self._sessions[session_id] = {
            "session_id": session_id,
            "revision": self._revision,
            "state": "playing",
            "position": 0,
            "duration": item.get("duration", 0),
            "updated_at": self._timestamp(),
            "volume_level": volume_level,
            "item": dict(item),
            "queue": {
                "index": index,
                "items": [dict(candidate) for candidate in queue],
            },
            "output_entity_ids": outputs,
            "supported_actions": ["stop"],
            "controller": _bounded_text(controller, 64),
            "auto_advance": bool(auto_advance),
        }
        while len(self._sessions) > MAX_SESSIONS:
            oldest = min(self._sessions, key=lambda key: self._sessions[key]["revision"])
            del self._sessions[oldest]
        return self.snapshot(session_id)

    def stop(self, expected_revision=None, session_id=None):
        if session_id:
            session_id = validate_session_id(session_id)
            session = self._sessions.get(session_id)
            if session is None or (
                expected_revision is not None and expected_revision != session["revision"]
            ):
                return {"stopped": False, "session": self.snapshot(session_id)}
            del self._sessions[session_id]
            self._revision += 1
            return {"stopped": True, "session": self._idle_session(self._revision, session_id)}
        if expected_revision is not None:
            match = next(
                (key for key, value in self._sessions.items() if value["revision"] == expected_revision),
                None,
            )
            if match is None:
                return {"stopped": False, "session": self.snapshot()}
            del self._sessions[match]
            self._revision += 1
            return {"stopped": True, "session": self._idle_session(self._revision, match)}
        self._sessions.clear()
        self._revision += 1
        return {"stopped": True, "session": self._idle_session(self._revision)}

    def set_outputs(self, session_id, output_entity_ids):
        """Change which speakers a session plays on without restarting its song.

        Speakers added here leave their other sessions; a session left without
        speakers ends (returns stopped)."""
        session_id = validate_session_id(session_id)
        outputs = validate_output_entity_ids(output_entity_ids)
        session = self._sessions.get(session_id)
        if session is None:
            return {"stopped": False, "session": self._idle_session(self._revision, session_id)}
        if not outputs:
            return self.stop(session_id=session_id)
        for other_id in list(self._sessions):
            other = self._sessions[other_id]
            if other_id == session_id or not other["output_entity_ids"]:
                continue
            remaining = [e for e in other["output_entity_ids"] if e not in outputs]
            if not remaining:
                del self._sessions[other_id]
            elif len(remaining) != len(other["output_entity_ids"]):
                self._revision += 1
                other["output_entity_ids"] = remaining
                other["revision"] = self._revision
        self._revision += 1
        session["output_entity_ids"] = outputs
        session["revision"] = self._revision
        session["updated_at"] = self._timestamp()
        return {"stopped": False, "session": self._copy(session)}

    @staticmethod
    def _copy(session):
        return {
            **session,
            "item": dict(session["item"]) if session["item"] else None,
            "queue": {
                "index": session["queue"]["index"],
                "items": [dict(item) for item in session["queue"]["items"]],
            },
            "output_entity_ids": list(session["output_entity_ids"]),
            "supported_actions": list(session["supported_actions"]),
        }

    def snapshot(self, session_id=None):
        """One session: the requested one, else the most recently changed."""
        if session_id:
            session = self._sessions.get(session_id)
            return self._copy(session) if session else self._idle_session(self._revision, session_id)
        if not self._sessions:
            return self._idle_session(self._revision)
        latest = max(self._sessions.values(), key=lambda value: value["revision"])
        return self._copy(latest)

    def snapshots(self):
        """Every active session, most recently changed first."""
        return [
            self._copy(session)
            for session in sorted(self._sessions.values(), key=lambda value: -value["revision"])
        ]
