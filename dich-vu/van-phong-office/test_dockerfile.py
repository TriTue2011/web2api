"""Kiểm tĩnh Dockerfile của dịch vụ van-phong-office (NV138).

- OfficeCLI v1.0.140: GHIM SHA-256 từng kiến trúc (amd64/arm64), Dockerfile tự
  `sha256sum -c` sau khi tải (không tin mạng).
- Chạy uid không root; biến OFFICECLI_SKIP_UPDATE=1 có mặt; ảnh chạy
  `officecli config autoUpdate false` lúc dựng (kiểm tra cập nhật nền bị tắt).
- COPY đủ mô-đun cục bộ mà server.py nhập.
- Không `curl | sh`, không sửa Dockerfile/docker-compose.yml gốc của repo.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GOC = Path(__file__).resolve().parent
DOCKERFILE = (GOC / "Dockerfile").read_text(encoding="utf-8")

SHA_X64 = "cee68cc2108074e5ae5ad114e1cd5cab4514da8ead4983d5d94aa0acba4f41e8"
SHA_ARM64 = "924dd58f57891d1b3fe8cf77f06990f58f8f0c0ddf6fe22e1d91c7f10d3e7576"


def test_ghim_sha256_tung_kien_truc_va_tu_kiem():
    assert SHA_X64 in DOCKERFILE, "thiếu SHA-256 cho amd64 (officecli-linux-x64)"
    assert SHA_ARM64 in DOCKERFILE, "thiếu SHA-256 cho arm64 (officecli-linux-arm64)"
    assert "officecli-linux-x64" in DOCKERFILE
    assert "officecli-linux-arm64" in DOCKERFILE
    assert "sha256sum -c" in DOCKERFILE, "phải tự kiểm sha256sum -c sau khi tải"
    assert "1.0.140" in DOCKERFILE, "phải ghim phiên bản OfficeCLI 1.0.140"


def test_khong_chay_root():
    assert re.search(r"^USER\s+(?!0\b)(?!root\b)\S+", DOCKERFILE, flags=re.M), \
        "Dockerfile phải chuyển sang người dùng không root trước khi chạy"


def test_tat_kiem_tra_cap_nhat_tu_dong():
    assert "OFFICECLI_SKIP_UPDATE=1" in DOCKERFILE, "thiếu biến tắt kiểm tra cập nhật"
    assert re.search(r"officecli\s+config\s+autoUpdate\s+false", DOCKERFILE), \
        "ảnh phải chạy `officecli config autoUpdate false` lúc dựng"


def test_copy_du_mo_dun_cuc_bo():
    cay = ast.parse((GOC / "server.py").read_text(encoding="utf-8"))
    ten = set()
    for nut in ast.walk(cay):
        if isinstance(nut, ast.ImportFrom) and nut.module and nut.level == 0:
            ten.add(nut.module.split(".")[0])
        elif isinstance(nut, ast.Import):
            ten.update(a.name.split(".")[0] for a in nut.names)
    cuc_bo = {t for t in ten if (GOC / f"{t}.py").is_file()}
    dong_copy = " ".join(l for l in DOCKERFILE.splitlines() if l.strip().startswith("COPY "))
    thieu = sorted(t for t in cuc_bo if f"{t}.py" not in dong_copy)
    assert not thieu, f"Dockerfile không COPY: {thieu}"


def test_khong_curl_pipe_sh():
    # Cấm mẫu «| sh» / «| bash» (ống sang shell), nhưng cho phép «| sha256sum».
    assert not re.search(r"\|\s*(ba)?sh(\s|;|$)", DOCKERFILE), "cấm mẫu curl|sh"


def test_khong_sua_dockerfile_goc():
    goc_repo = GOC.parents[1]
    goc = (goc_repo / "Dockerfile").read_text(encoding="utf-8")
    assert "officecli" not in goc.lower(), "KHÔNG được đưa nhị phân vào Dockerfile gốc"
    assert "van-phong-office" not in (goc_repo / "docker-compose.yml").read_text(encoding="utf-8"), \
        "KHÔNG được sửa docker-compose.yml gốc (dịch vụ nối bằng compose riêng)"
