"""Kiểm tĩnh Dockerfile của dich-vu/rclone (NV161).

Không cần Docker daemon: đọc nội dung Dockerfile và khẳng định các thuộc tính
bảo mật nền: ghim digest ảnh nền THẬT, ghim SHA-256 TỪNG kiến trúc + kiểm
`sha256sum -c` lúc dựng, uid không root, không `curl|sh`.
"""

from __future__ import annotations

from pathlib import Path

DUONG = Path(__file__).resolve().parent / "Dockerfile"


def _noi_dung() -> str:
    return DUONG.read_text(encoding="utf-8")


def test_from_ghim_digest_that():
    n = _noi_dung()
    dong = [d for d in n.splitlines() if d.startswith("FROM ")]
    assert dong, "không thấy FROM"
    assert "@sha256:" in dong[0], "ảnh nền phải ghim digest"
    # digest tra Docker Hub ngày 20/10/2026 cho python:3.13-slim
    assert "sha256:8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024" in dong[0]


def test_rclone_ghim_sha256_ca_hai_kien_truc():
    n = _noi_dung()
    # SHA-256 tự tính lại 20/10/2026, đối chiếu khớp SHA256SUMS chính thức v1.75.2
    assert "349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702" in n  # amd64
    assert "7e1e8d69654941b7b7df84ee74c5f7fb09cce7d5496947fb7bf16b55ff427d10" in n  # arm64
    assert "sha256sum -c" in n
    assert "v1.75.2" in n


def test_chay_user_khong_root():
    n = _noi_dung()
    assert "USER 1000:1000" in n
    assert "USER root" not in n


def test_khong_curl_pipe_sh():
    import re
    n = _noi_dung().lower()
    assert not re.search(r"\|\s*(sh|bash|zsh)\b", n), "không được pipe vào shell"


def test_copy_du_server_va_cmd():
    n = _noi_dung()
    assert "COPY server.py /app/server.py" in n
    assert 'CMD ["python3", "/app/server.py"]' in n
    assert "/usr/local/bin/rclone" in n


def test_khoa_khong_ghi_vao_anh():
    n = _noi_dung()
    for dong in n.splitlines():
        if dong.startswith("ENV") and "RCLONE_KHOA=" in dong:
            raise AssertionError("khoá không được ghi vào ảnh qua ENV")
