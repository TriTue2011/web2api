"""yt-dlp GIẢ cho test đợt A NV153 (venv test KHÔNG có yt-dlp thật).

``python -m yt_dlp`` nạp gói này qua PYTHONPATH (conftest đặt). Đọc argv và
trả JSON canned theo kịch bản trong biến môi trường ``YTDLP_GIA_KICH_BAN``;
KHÔNG gọi mạng.

Kịch bản:
  tim_kiem_ok   (mặc định) flat-playlist JSON cho search_youtube
  tim_kiem_loi  exit 1  -> search_unavailable (502)
  am_thanh_ok   dump-single-json, formats[0].url = $GOOGLEVIDEO_GIA_URL
  am_thanh_loi  exit 2  -> stream_provider_failed (502)
  url_doc_hai   formats[0].url = http://evil.example/... (test không proxy mở)
  url_la        formats[0].url = http://googlevideo.com.evil.example/... (test host lạ)
"""

import json
import os
import sys


def _tim_kiem():
    return {
        "entries": [
            {
                "id": "dTeH03NpB1A",
                "title": "Bai tim kiem gia 1",
                "channel": "Kenh gia",
                "duration": 185,
                "thumbnails": [],
            },
            {
                "id": "aBcDeFgHiJ1",
                "title": "Bai tim kiem gia 2",
                "channel": "Kenh gia 2",
                "duration": 240,
                "thumbnails": [],
            },
        ]
    }


def _am_thanh(url):
    return {
        "id": "dTeH03NpB1A",
        "title": "Bai am thanh gia",
        "formats": [
            {
                "url": url,
                "acodec": "mp4a.40.2",
                "vcodec": "none",
                "ext": "m4a",
            }
        ],
    }


def main() -> int:
    kich_ban = os.environ.get("YTDLP_GIA_KICH_BAN", "tim_kiem_ok")
    argv = sys.argv[1:]
    la_tim_kiem = "--flat-playlist" in argv

    if kich_ban == "tim_kiem_loi":
        print("yt-dlp gia: that bai tim kiem", file=sys.stderr)
        return 1
    if kich_ban == "am_thanh_loi":
        print("yt-dlp gia: that bai giai am thanh", file=sys.stderr)
        return 2
    if kich_ban == "url_doc_hai":
        json.dump(_am_thanh("http://evil.example/videoplayback?x=1"), sys.stdout)
        return 0
    if kich_ban == "url_la":
        json.dump(
            _am_thanh("http://googlevideo.com.evil.example/videoplayback?x=1"),
            sys.stdout,
        )
        return 0
    if kich_ban == "am_thanh_ok" or not la_tim_kiem:
        url = os.environ.get(
            "GOOGLEVIDEO_GIA_URL", "http://127.0.0.1:9/videoplayback?x=1"
        )
        json.dump(_am_thanh(url), sys.stdout)
        return 0
    json.dump(_tim_kiem(), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
