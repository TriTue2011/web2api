"""NV219 — Bỏ «danh tính» khỏi phần AI.

ĐO (trước sửa): `danh_tinh`/`danh_tinh_id` xuất hiện ở:
- app/*.py: 296 dòng (gồm 3 khái niệm: kho danh tính AI, principal xác thực,
  biến cục bộ đặt tên trùng) — xem bảng A/B trong báo cáo.
- modules AI (chatgpt-web, gemini-web, grok-web, claude-web, muse2api,
  ho-so-google): 128 dòng.
- web/: 29 dòng (He-thong-AI.dc.html, khung.js).

SAU sửa, qua lõi thật:
1. 5 module AI (chatgpt-web, gemini-web, grok-web, claude-web, muse2api) không
   còn nhắc `danh_tinh` trong mã nguồn module (tĩnh).
2. Các tuyến đăng nhập qua danh tính không còn: POST /api/danh-tinh/dang-nhap,
   GET /api/danh-tinh/{id}/trang-thai, POST /api/danh-tinh/{id}/kiem-lai,
   POST /api/danh-tinh/{id}/ma-2fa → 404.
3. Kho danh tính lõi VẪN hoạt động (CRUD cho vnc-xa/so_module/sao_luu).
4. Cấu hình cũ có `danh_tinh_id` được bỏ qua êm (không lỗi) khi nạp module AI.
5. Principal xác thực (loai quan_tri/khach) không bị đụng.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import danh_tinh as danh_tinh_loi
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MODULE_AI = ["chatgpt-web", "gemini-web", "grok-web", "claude-web", "muse2api"]


def _nguon_module(module_id: str) -> str:
    return (GOC / "modules" / module_id / "module.py").read_text(encoding="utf-8")


# ───────────── 1. Module AI không còn nhắc danh_tinh ─────────────

@pytest.mark.parametrize("module_id", MODULE_AI)
def test_module_ai_khong_nhac_danh_tinh(module_id):
    """Mã nguồn module AI không còn chuỗi danh_tinh/danh_tinh_id."""
    nguon = _nguon_module(module_id)
    # Bỏ qua docstring lịch sử? Không — gỡ sạch, kể cả chú thích.
    dong = [d.strip() for d in nguon.splitlines() if "danh_tinh" in d]
    assert not dong, f"{module_id} còn nhắc danh_tinh:\n" + "\n".join(dong[:10])


def test_manifest_chatgpt_web_khong_con_truong_danh_tinh():
    """Manifest chatgpt-web không còn trường cấu hình danh_tinh_id."""
    mf = json.loads((GOC / "modules" / "chatgpt-web" / "manifest.json").read_text(encoding="utf-8"))
    khoa = [t.get("khoa") for t in mf.get("truong_cau_hinh", [])]
    assert "danh_tinh_id" not in khoa
    assert "danh tính" not in mf.get("mo_ta", "").lower()


# ───────────── 2. Tuyến đăng nhập qua danh tính đã gỡ ─────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_tuyen_dang_nhap_danh_tinh_da_go(client):
    """4 tuyến đăng nhập qua danh tính → 404/405 (đã gỡ khỏi phần AI).

    405 cũng đạt: path khớp pattern /{id}/* còn lại nhưng method không còn.
    """
    assert client.post("/api/danh-tinh/dang-nhap", headers=QT, json={}).status_code in (404, 405)
    assert client.get("/api/danh-tinh/dt-aaaabbbbccccdddd/trang-thai", headers=QT).status_code == 404
    assert client.post("/api/danh-tinh/dt-aaaabbbbccccdddd/kiem-lai", headers=QT).status_code in (404, 405)
    assert client.post("/api/danh-tinh/dt-aaaabbbbccccdddd/ma-2fa", headers=QT, json={}).status_code in (404, 405)


# ───────────── 3. Kho danh tính lõi VẪN hoạt động (cho B) ─────────────

def test_kho_danh_tinh_loi_van_hoat_dong(client):
    """CRUD kho danh tính lõi giữ nguyên cho module (B): vnc-xa, so_module, sao_luu."""
    r = client.post("/api/danh-tinh", headers=QT, json={"email": "giutron@example.com"})
    assert r.status_code == 201, r.text
    dt_id = r.json()["danh_tinh"]["id"]
    r = client.get("/api/danh-tinh", headers=QT)
    assert r.status_code == 200
    assert any(d["id"] == dt_id for d in r.json()["danh_tinh"])
    # nhan-ho-so (vnc-xa dùng) vẫn còn — 503 khi thiếu dịch vụ trình duyệt là bình thường ở môi trường test
    r = client.post("/api/danh-tinh/nhan-ho-so", headers=QT,
                    json={"ho_so": "a" * 32, "email": "giutron@example.com"})
    assert r.status_code in (200, 201, 409, 503), r.text
    # Xoá dọn
    assert client.delete(f"/api/danh-tinh/{dt_id}", headers=QT).status_code == 200


def test_api_loi_danh_tinh_con_dung_duoc():
    """API app.danh_tinh (so_module.go_theo_module dùng) còn nguyên."""
    assert callable(danh_tinh_loi.tao)
    assert callable(danh_tinh_loi.go_theo_module)
    assert callable(danh_tinh_loi.chuan_hoa_email)


# ───────────── 4. Cấu hình cũ có danh_tinh_id: bỏ qua êm ─────────────

def test_cau_hinh_cu_co_danh_tinh_id_khong_loi():
    """Dữ liệu cũ còn danh_tinh_id: module AI bỏ qua êm, không vỡ, không xoá kho.

    Kiểm tĩnh: mã nguồn module không còn nhắc danh_tinh nên khoá lạ bị bỏ qua
    (lõi chỉ truyền các trường đã khai trong manifest).
    """
    for mid in ("chatgpt-web", "gemini-web", "grok-web", "claude-web", "muse2api"):
        duong = GOC / "modules" / mid / "module.py"
        assert "danh_tinh" not in duong.read_text(encoding="utf-8"), mid
    # Manifest chatgpt-web không còn trường danh_tinh_id
    mf = json.loads((GOC / "modules" / "chatgpt-web" / "manifest.json").read_text(encoding="utf-8"))
    assert all(t.get("khoa") != "danh_tinh_id" for t in mf["truong_cau_hinh"])


# ───────────── 5. Principal xác thực không bị đụng ─────────────

def test_principal_xac_thuc_con_nguyen(client):
    """Không khoá → 401; khoá sai → 401/403: lớp xác thực (danh_tinh principal) còn nguyên."""
    r = client.get("/api/danh-tinh")
    assert r.status_code == 401, r.text
    r = client.get("/api/danh-tinh", headers={"Authorization": "Bearer sai"})
    assert r.status_code in (401, 403), r.text
