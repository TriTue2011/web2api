"""Kiểm tĩnh Dockerfile + package của dịch vụ zalo-ca-nhan (NV172).

- Ảnh nền GHIM DIGEST thật (node:22-alpine), không dùng tag trần.
- Chạy người dùng không root; chỉ nghe loopback; EXPOSE 8703.
- package.json ghim zca-js ĐÚNG phiên bản (không «^»); package-lock.json có
  integrity sha512 cho zca-js; `npm ci` trong Dockerfile.
- Không `curl | sh`, không sửa Dockerfile/docker-compose.yml gốc của repo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

GOC = Path(__file__).resolve().parent
DOCKERFILE = (GOC / "Dockerfile").read_text(encoding="utf-8")
PACKAGE = json.loads((GOC / "package.json").read_text(encoding="utf-8"))
LOCK = json.loads((GOC / "package-lock.json").read_text(encoding="utf-8"))

DIGEST_NODE = "sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402"


def test_anh_nen_ghim_digest():
    assert f"node:22-alpine@{DIGEST_NODE}" in DOCKERFILE, "phải ghim digest thật cho node:22-alpine"
    assert "FROM node:latest" not in DOCKERFILE


def test_khong_chay_root():
    assert re.search(r"^USER\s+node\s*$", DOCKERFILE, flags=re.M), "phải USER node (không root)"


def test_loopback_va_cong():
    assert "ZALO_HOST=127.0.0.1" in DOCKERFILE
    assert "EXPOSE 8703" in DOCKERFILE


def test_npm_ci_va_ghim_phien_ban():
    assert "npm ci" in DOCKERFILE, "Dockerfile phải dùng npm ci theo lock"
    assert PACKAGE["dependencies"]["zca-js"] == "2.1.2", "zca-js phải ghim đúng phiên bản"
    muc = LOCK["packages"]["node_modules/zca-js"]
    assert muc["version"] == "2.1.2"
    assert str(muc.get("integrity", "")).startswith("sha512-"), "lock phải có integrity sha512"


def test_khong_curl_pipe_sh():
    assert not re.search(r"\|\s*(ba)?sh(\s|;|$)", DOCKERFILE), "cấm mẫu curl|sh"
