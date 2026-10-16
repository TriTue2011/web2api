"""NV19 — lấp lỗ hổng NV14 nhóm auth (CHỈ THÊM TEST, không sửa mã).

1. app/xac_thuc.py:can_quan_tri bỏ kiểm IP → ma trận quyền phải có case
   IP ngoài allowlist → 403 ip_not_allowed.
2. app/ip_cho_phep.py bỏ _TOI_DA_IP_NHO → _nen_ghi phải đẩy IP cũ nhất khi vượt trần.
3. app/xac_thuc.py thay hmac.compare_digest bằng == → spy hmac.compare_digest
   phải được gọi khi so khoá quản trị (KHÔNG đo thời gian).
"""

from __future__ import annotations

import asyncio
import hmac

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import gioi_han, ip_cho_phep, xac_thuc
from app.main import app

KHOA_QT = "khoa-quan-tri-nv19-auth-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
TEN_BIEN_IP = "WEB2API_QT_IP_CHO_PHEP"
IP_TRONG = "192.0.2.5"    # TEST-NET-1 (RFC 5737), nằm trong 192.0.2.0/24
IP_NGOAI = "203.0.113.9"  # TEST-NET-3 (RFC 5737), ngoài danh sách


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "0")  # tắt đếm sai khoá
    monkeypatch.delenv(TEN_BIEN_IP, raising=False)
    monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
    ip_cho_phep._LAN_GHI_CUOI.clear()
    return monkeypatch


def _dat_ip(client, ip: str) -> None:
    """Gán IP peer cho TestClient (scope['client'] của starlette lấy từ đây mỗi request)."""
    client._transport.client = (ip, 1234)


def _la_ip_not_allowed(r) -> bool:
    try:
        return r.status_code == 403 and r.json()["error"]["code"] == "ip_not_allowed"
    except Exception:
        return False


def test_can_quan_tri_ip_ngoai_allowlist_403(moi_truong):
    """NV14#1: bỏ kiểm IP trong can_quan_tri → IP ngoài danh sách phải 403 ip_not_allowed."""
    moi_truong.setenv(TEN_BIEN_IP, "192.0.2.0/24")
    with TestClient(app) as c:
        _dat_ip(c, IP_NGOAI)
        r = c.get("/api/khoa-khach", headers=QT)
    assert _la_ip_not_allowed(r), f"muốn 403 ip_not_allowed, được {r.status_code} {r.text[:200]}"


def test_can_quan_tri_ip_trong_allowlist_200(moi_truong):
    """Đối chứng: IP trong danh sách vẫn qua cổng (test trên không 403 vì lý do khác)."""
    moi_truong.setenv(TEN_BIEN_IP, "192.0.2.0/24")
    with TestClient(app) as c:
        _dat_ip(c, IP_TRONG)
        r = c.get("/api/khoa-khach", headers=QT)
    assert r.status_code == 200, f"muốn 200, được {r.status_code} {r.text[:200]}"


def test_nen_ghi_day_ip_cu_nhat_khi_vuot_tran(moi_truong):
    """NV14#2: bỏ _TOI_DA_IP_NHO → ghi vượt trần phải đẩy IP cũ nhất, giữ đúng trần."""
    d = ip_cho_phep._LAN_GHI_CUOI
    assert len(d) == 0
    for i in range(ip_cho_phep._TOI_DA_IP_NHO):
        d[f"10.200.{i // 256}.{i % 256}"] = 0.0  # chèn trực tiếp, giả vờ đã ghi từ lâu
    cu_nhat = next(iter(d))
    assert ip_cho_phep._nen_ghi("203.0.113.200") is True
    assert len(d) == ip_cho_phep._TOI_DA_IP_NHO, f"vượt trần: {len(d)}"
    assert cu_nhat not in d, "IP cũ nhất không bị đẩy"
    assert "203.0.113.200" in d


def test_so_khoa_quan_tri_dung_compare_digest(moi_truong, monkeypatch):
    """NV14#3: thay compare_digest bằng == → hmac.compare_digest phải được gọi với khoá quản trị.

    Gọi trực tiếp xac_thuc.nguoi_goi (không qua TestClient) để middleware ở app/main.py
    (cũng gọi compare_digest mỗi request /api/*) không làm nhiễu bộ đếm spy.
    """
    goi: list[tuple[bytes, bytes]] = []
    goc = hmac.compare_digest

    def spy(a, b):
        goi.append((a, b))
        return goc(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    gioi_han.khoi_dong()  # đọc lại env WEB2API_RL_LOI_XAC_THUC=0 của fixture
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/khoa-khach",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {KHOA_QT}".encode("utf-8")),
        ],
    }
    danh_tinh = asyncio.run(xac_thuc.nguoi_goi(Request(scope)))
    assert danh_tinh.get("loai") == "quan_tri", f"xác thực khoá đúng phải ra quan_tri: {danh_tinh}"
    assert any(b == KHOA_QT.encode("utf-8") for _, b in goi), \
        "so khoá quản trị không qua hmac.compare_digest"
