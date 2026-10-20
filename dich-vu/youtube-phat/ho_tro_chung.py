"""Helper dùng chung cho test đợt A NV153 (không phải mã dịch vụ)."""

from __future__ import annotations

import json
import subprocess
import sys
from urllib.parse import urlsplit

VIDEO_ID_GIA = "dTeH03NpB1A"  # khớp ^[A-Za-z0-9_-]{11}$


def dat_giai_lai_stream(monkeypatch, may_chu):
    """Cho route ``/api/stream`` giải luồng qua yt-dlp GIẢ + máy chủ giả.

    - ``streaming.resolve_youtube_audio`` thật vẫn chạy (giữ nguyên kiểm host),
      chỉ thay ``extractor`` bằng bản gọi tiến trình con ``python -m yt_dlp``
      (gói giả trong ho_tro_test, kịch bản qua ``YTDLP_GIA_KICH_BAN``).
    - Nới ``YOUTUBE_STREAM_HOSTS`` thêm ``127.0.0.1`` để URL của máy chủ giả
      qua được kiểm host (kiểm host thật vẫn được test riêng ở test_bao_mat).
    """
    import dich_vu
    import streaming

    def _extractor_gia(watch_url, timeout):
        hoan = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--dump-single-json",
             "--skip-download", "--no-warnings", watch_url],
            capture_output=True, text=True, timeout=timeout,
        )
        if hoan.returncode != 0:
            raise RuntimeError("yt-dlp gia that bai")
        return json.loads(hoan.stdout)

    def _giai_gia(video_id, *, timeout=20, extractor=None):
        return streaming.resolve_youtube_audio(
            video_id, timeout=timeout, extractor=_extractor_gia
        )

    monkeypatch.setenv("YTDLP_GIA_KICH_BAN", "am_thanh_ok")
    monkeypatch.setenv("GOOGLEVIDEO_GIA_URL", may_chu.url)
    monkeypatch.setattr(dich_vu, "resolve_youtube_audio", _giai_gia)
    monkeypatch.setattr(
        streaming, "YOUTUBE_STREAM_HOSTS", ("googlevideo.com", "127.0.0.1")
    )


def duong_dan_luong(core, video_id: str = VIDEO_ID_GIA) -> str:
    """Đường dẫn ``/api/stream/{token}`` đã ký cho một video giả."""
    url = core.create_stream_url("youtube", video_id, "http://127.0.0.1:9")
    phan = urlsplit(url)
    return phan.path + (("?" + phan.query) if phan.query else "")
