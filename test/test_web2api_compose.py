"""docker-compose.yml ở gốc kho phải dùng được cho NGƯỜI MỚI — kiểm bằng chữ (không cần docker).

Cú pháp đầy đủ được kiểm thêm bằng `docker compose -f docker-compose.yml config -q` khi có docker
(test cuối, tự bỏ qua nếu máy không có docker). Những điều dưới đây là quyết định đã chốt, đổi thì
README phải đổi theo.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
COMPOSE = (GOC / "docker-compose.yml").read_text(encoding="utf-8")


def _dong(khoa: str) -> str:
    m = re.search(rf"^\s+{re.escape(khoa)}:\s*(.+)$", COMPOSE, flags=re.M)
    assert m, f"compose thiếu {khoa}"
    return m.group(1).strip()


def test_mot_tep_keo_anh_ghcr_va_dung_tai_cho_duoc():
    assert _dong("image") == "ghcr.io/tritue2011/web2api:latest"
    assert _dong("build") == "."
    assert _dong("container_name") == "web2api"
    assert not (GOC / "docker-compose.build.yml").exists()  # MỘT tệp compose


def test_du_lieu_mac_dinh_la_volume_co_ten_khong_can_chown():
    # Thư mục bind chưa có → Docker tạo thuộc root → uid 1000 không ghi được khoá quản trị.
    assert "- ${WEB2API_DATA_DIR:-web2api-data}:/data" in COMPOSE
    assert re.search(r"^volumes:\n  web2api-data:\n(?:\s+#.*\n)*\s+name: web2api-data$", COMPOSE, flags=re.M)


def test_gioi_han_va_an_toan():
    assert _dong("user") == '"1000:1000"'
    assert _dong("restart") == "unless-stopped"
    assert _dong("mem_limit") and _dong("cpus") and _dong("pids_limit")
    # Luật bảo mật số 5: mặc định CHỈ nghe trên máy chủ; mở ra mạng nhà phải tường minh (WEB2API_BIND).
    assert '"${WEB2API_BIND:-127.0.0.1}:${WEB2API_PORT:-3031}:8000"' in COMPOSE
    assert "cap_drop:" in COMPOSE and "- ALL" in COMPOSE
    assert "no-new-privileges:true" in COMPOSE
    assert _dong("com.centurylinklabs.watchtower.enable") == '"false"'
    assert "healthcheck:" not in COMPOSE  # lấy HEALTHCHECK từ ảnh
    assert "HEALTHCHECK" in (GOC / "Dockerfile").read_text(encoding="utf-8")
    assert "required: false" in COMPOSE  # .env tuỳ chọn


def test_khong_tu_cai_va_khong_token_trong_compose():
    assert "tu_dong" not in COMPOSE.replace("Tự cài (tu_dong) KHÔNG bật", "")
    assert not re.search(r"^\s+WEB2API_GIT_TOKEN:", COMPOSE, flags=re.M)
    assert not re.search(r"^\s+WEB2API_ADMIN_KEY:", COMPOSE, flags=re.M)


@pytest.mark.skipif(shutil.which("docker") is None, reason="cần docker để kiểm cú pháp compose")
def test_cu_phap_compose_hop_le():
    r = subprocess.run(["docker", "compose", "-f", str(GOC / "docker-compose.yml"), "config", "-q"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_ma_hoa_khi_nghi_bat_buoc_va_khoa_nam_ngoai_du_lieu():
    """Compose mẫu bật mã hoá bắt buộc; khoá chính là tệp gắn riêng NGOÀI /data, chỉ đọc, thiếu tệp thì lỗi rõ."""
    assert 'WEB2API_BAT_BUOC_MA_HOA: "1"' in COMPOSE
    assert "WEB2API_KHOA_CHINH_TEP: /run/web2api/khoa-chinh" in COMPOSE
    assert not re.search(r"^\s+WEB2API_KHOA_CHINH:", COMPOSE, flags=re.M)  # không nhét khoá vào compose
    khoi = re.search(r"- type: bind\n\s+source: \./khoa-chinh\n\s+target: (\S+)\n\s+read_only: true\n\s+bind:\n\s+create_host_path: false", COMPOSE)
    assert khoi, "thiếu khối gắn tệp khoá chính (bind, read_only, create_host_path: false)"
    assert not khoi.group(1).startswith("/data"), "khoá chính không được nằm trong thư mục dữ liệu"
    assert re.search(r"^khoa-chinh$", (GOC / ".gitignore").read_text(encoding="utf-8"), flags=re.M)  # không commit khoá
    for tep in ("README.md", "README.en.md"):
        txt = (GOC / tep).read_text(encoding="utf-8")
        assert "openssl rand -base64 32 > khoa-chinh" in txt and "docs/TRIEN-KHAI.md" in txt, f"{tep} thiếu hướng dẫn tạo khoá"
