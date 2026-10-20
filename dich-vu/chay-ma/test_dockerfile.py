"""Kiểm tĩnh Dockerfile của dịch vụ chay-ma (NV171).

- docker CLI 27.5.1 (nhị phân tĩnh): GHIM SHA-256 từng kiến trúc (amd64/arm64),
  Dockerfile tự `sha256sum -c` sau khi tải (không tin mạng). SHA-256 tự tính
  lại từ tệp tải về ngày 20/10/2026.
- Ảnh nền python:3.13-slim GHIM DIGEST THẬT (tra Docker Hub ngày 20/10/2026,
  dùng lại giá trị NV161 đã kiểm).
- Chạy uid không root; không `curl | sh`; COPY đủ server.py; không sửa
  Dockerfile/docker-compose.yml gốc của repo.
"""

from __future__ import annotations

import re
from pathlib import Path

GOC = Path(__file__).resolve().parent
DOCKERFILE = (GOC / "Dockerfile").read_text(encoding="utf-8")

SHA_DOCKER_X64 = "4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640"
SHA_DOCKER_ARM64 = "e6b53725a73763ab3f988c73f8772eaed429754c1a579db5ff11f21990fd1817"
DIGEST_PYTHON = "8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024"


def test_ghim_sha256_docker_cli_va_tu_kiem():
    assert SHA_DOCKER_X64 in DOCKERFILE, "thiếu SHA-256 docker CLI amd64"
    assert SHA_DOCKER_ARM64 in DOCKERFILE, "thiếu SHA-256 docker CLI arm64"
    assert "docker-27.5.1" in DOCKERFILE, "phải ghim phiên bản docker CLI 27.5.1"
    assert "sha256sum -c" in DOCKERFILE, "phải tự kiểm sha256sum -c sau khi tải"
    assert "download.docker.com" in DOCKERFILE


def test_ghim_digest_anh_nen():
    assert f"python:3.13-slim@sha256:{DIGEST_PYTHON}" in DOCKERFILE, \
        "ảnh nền phải ghim digest thật"
    assert "python:3.13-slim\n" not in DOCKERFILE.replace("@sha256:", ""), \
        "không dùng tag trôi cho ảnh nền"


def test_khong_chay_root():
    assert re.search(r"^USER\s+(?!0\b)(?!root\b)\S+", DOCKERFILE, flags=re.M), \
        "Dockerfile phải chuyển sang người dùng không root trước khi chạy"


def test_copy_du_server():
    assert re.search(r"^COPY\s+.*server\.py", DOCKERFILE, flags=re.M), \
        "phải COPY server.py vào ảnh"


def test_khong_curl_pipe_sh():
    assert not re.search(r"\|\s*(ba)?sh(\s|;|$)", DOCKERFILE), "cấm mẫu curl|sh"


def test_khong_sua_dockerfile_goc():
    goc_repo = GOC.parents[1]
    goc = (goc_repo / "Dockerfile").read_text(encoding="utf-8")
    assert "chay-ma" not in goc.lower(), "KHÔNG được đưa chay-ma vào Dockerfile gốc"
    assert "chay-ma" not in (goc_repo / "docker-compose.yml").read_text(encoding="utf-8"), \
        "KHÔNG được sửa docker-compose.yml gốc (dịch vụ nối bằng compose riêng)"
