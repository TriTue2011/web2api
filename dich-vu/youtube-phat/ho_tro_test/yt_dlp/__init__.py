"""Gói yt_dlp GIẢ cho test — chỉ có __main__ (chạy ``python -m yt_dlp``).

Cố ý KHÔNG có lớp YoutubeDL: tiến trình test dùng bộ giải qua tiến trình con
hoặc monkeypatch, giống như venv thật không cài yt-dlp.
"""
