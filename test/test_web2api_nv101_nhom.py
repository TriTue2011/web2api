"""Kiểm thử trường «nhom» trong manifest (NV101).

- Trường «nhom»: {"id": "<id>", "ten": "<tên hiển thị>"}, tuỳ chọn.
- Hợp lệ: xuất hiện trong /api/modules và /api/tinh-nang.
- Sai khuôn (id hoa, quá dài, ký tự lạ, ten rỗng, không phải đối tượng, thiếu khoá):
  module VẪN nạp bình thường, «nhom» bị bỏ (null), ghi log.warning, không văng lỗi.
- Không khai «nhom»: null.
- Mã nhóm lạ: hiện nguyên tên (bảng mở).
- Thả module giả mang nhóm lạ rồi gỡ: tự xuất hiện rồi biến mất.
- Đổi «nhom» của module đã cài: không bị quyen_module coi là đổi quyền (chống rug-pull).
- Mọi modules/*/manifest.json trong kho khai đúng bảng chuẩn.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import quyen_module
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "AIzaSyTOT-mot-000000000000000000000001"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

BANG_NHOM_CHUAN = {
    "gemini-api": {"id": "google", "ten": "Google"},
    "gemini-web": {"id": "google", "ten": "Google"},
    "antigravity": {"id": "google", "ten": "Google"},
    "ho-so-google": {"id": "google", "ten": "Google"},
    "chatgpt-web": {"id": "chatgpt", "ten": "ChatGPT"},
    "grok-web": {"id": "grok", "ten": "Grok"},
    "grok-api": {"id": "grok", "ten": "Grok"},
    "claude-web": {"id": "claude", "ten": "Claude"},
    "muse": {"id": "muse", "ten": "Muse"},
    "muse2api": {"id": "muse", "ten": "Muse"},
}

MA_MODULE_GIA = (
    "class Module:\n"
    "    def __init__(self, m): self.manifest = m\n"
    "    def nap(self, c): pass\n"
    "    async def cai(self, c): return {'ok': True}\n"
    "    async def models(self): return []\n"
    "    async def suc_khoe(self): return {'ok': True}\n"
    "    async def dung(self): pass\n"
)


def test_manifest_trong_kho_khai_dung_nhom():
    """Mọi module mẫu có mặt trong kho phải khai đúng nhóm theo bảng quy định."""
    for mod_id, nhom_mong_doi in BANG_NHOM_CHUAN.items():
        mf_path = GOC / "modules" / mod_id / "manifest.json"
        if not mf_path.exists():
            continue
        mf = json.loads(mf_path.read_text(encoding="utf-8"))
        assert mf.get("nhom") == nhom_mong_doi, f"Module {mod_id} khai sai nhóm: {mf.get('nhom')}"

    # vnc-xa không thuộc nhóm nào
    vnc_path = GOC / "modules" / "vnc-xa" / "manifest.json"
    if vnc_path.exists():
        mf_vnc = json.loads(vnc_path.read_text(encoding="utf-8"))
        assert mf_vnc.get("nhom") is None


def test_quyen_module_khong_coi_nhom_la_doi_quyen():
    """Đổi trường nhom trong manifest không bị quyen_module coi là đổi quyền."""
    cu = {"id": "test-mod", "nhom": {"id": "nhom-a", "ten": "Nhóm A"}}
    moi = {"id": "test-mod", "nhom": {"id": "nhom-b", "ten": "Nhóm B"}}
    diff = quyen_module.diff_quyen(cu, moi)
    assert not diff["them"], f"Đổi nhóm bị coi là thêm quyền: {diff['them']}"
    assert not diff["bot"], f"Đổi nhóm bị coi là bớt quyền: {diff['bot']}"


def test_nhom_hop_le_trong_api(tmp_path, monkeypatch):
    """Module khai nhom hợp lệ -> có trong /api/modules và /api/tinh-nang."""
    d_data = tmp_path / "data"
    monkeypatch.setenv("WEB2API_DATA", str(d_data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    m_dir = d_data / "modules" / "mod-nhom-la"
    m_dir.mkdir(parents=True)
    manifest = {
        "id": "mod-nhom-la", "ten": "Mod Lạ", "mo_ta": "Mô tả", "loai": "ai",
        "hang": "A", "giao_thuc": "openai", "tien_to": "modnhomla",
        "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
        "truong_cau_hinh": [], "yeu_cau_goi": [],
        "nhom": {"id": "nhom-la-9", "ten": "Nhóm Lạ 9"},
    }
    (m_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (m_dir / "module.py").write_text(MA_MODULE_GIA, encoding="utf-8")

    with TestClient(app) as client:
        r = client.get("/api/tinh-nang", headers=QT)
        assert r.status_code == 200
        j = r.json()
        item = next((m for m in j["modules"] if m["id"] == "mod-nhom-la"), None)
        assert item is not None
        assert item["nhom"] == {"id": "nhom-la-9", "ten": "Nhóm Lạ 9"}

        r_mod = client.get("/api/modules", headers=QT)
        assert r_mod.status_code == 200
        j_mod = r_mod.json()
        item_mod = next((m for m in j_mod["modules"] if m.get("id") == "mod-nhom-la"), None)
        assert item_mod is not None
        assert item_mod["nhom"] == {"id": "nhom-la-9", "ten": "Nhóm Lạ 9"}


@pytest.mark.parametrize("nhom_sai", [
    "chuoi-khong-phai-dict",
    {"id": "HOA", "ten": "Tên"},
    {"id": "dau cach", "ten": "Tên"},
    {"id": "a" * 33, "ten": "Tên"},
    {"id": "hop-le", "ten": ""},
    {"id": "hop-le", "ten": "   "},
    {"id": "hop-le", "ten": "x" * 41},
    {"id": "hop-le"},
    {"ten": "Thiếu ID"},
])
def test_nhom_sai_khuon_thi_bo_va_log_warning(tmp_path, monkeypatch, caplog, nhom_sai):
    """Sai khuôn -> module vẫn nạp bình thường, nhom thành null, log cảnh báo."""
    d_data = tmp_path / "data"
    monkeypatch.setenv("WEB2API_DATA", str(d_data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    m_dir = d_data / "modules" / "mod-nhom-sai"
    m_dir.mkdir(parents=True)
    manifest = {
        "id": "mod-nhom-sai", "ten": "Mod Sai", "mo_ta": "Mô tả", "loai": "ai",
        "hang": "A", "giao_thuc": "openai", "tien_to": "modnhomsai",
        "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
        "truong_cau_hinh": [], "yeu_cau_goi": [],
        "nhom": nhom_sai,
    }
    (m_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (m_dir / "module.py").write_text(MA_MODULE_GIA, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            r = client.get("/api/tinh-nang", headers=QT)
            assert r.status_code == 200
            j = r.json()
            item = next((m for m in j["modules"] if m["id"] == "mod-nhom-sai"), None)
            assert item is not None
            assert item["nhom"] is None
            assert any("nhom" in rec.message for rec in caplog.records)


def test_nhom_tu_dong_xuat_hien_va_bien_mat_khi_tha_go(tmp_path, monkeypatch):
    """Module mang nhóm lạ thả vào rồi gỡ thì tự xuất hiện rồi biến mất."""
    d_data = tmp_path / "data"
    monkeypatch.setenv("WEB2API_DATA", str(d_data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    with TestClient(app) as client:
        # Ban đầu chưa có mod-tha-go
        r1 = client.get("/api/tinh-nang", headers=QT)
        assert not any(m["id"] == "mod-tha-go" for m in r1.json()["modules"])

        # Thả module vào
        m_dir = d_data / "modules" / "mod-tha-go"
        m_dir.mkdir(parents=True)
        manifest = {
            "id": "mod-tha-go", "ten": "Mod Thả Gỡ", "mo_ta": "Mô tả", "loai": "ai",
            "hang": "A", "giao_thuc": "openai", "tien_to": "modthago",
            "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
            "truong_cau_hinh": [], "yeu_cau_goi": [],
            "nhom": {"id": "nhom-dong", "ten": "Nhóm Động"},
        }
        (m_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (m_dir / "module.py").write_text(MA_MODULE_GIA, encoding="utf-8")

        r_nap = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200

        r2 = client.get("/api/tinh-nang", headers=QT)
        item2 = next((m for m in r2.json()["modules"] if m["id"] == "mod-tha-go"), None)
        assert item2 is not None
        assert item2["nhom"] == {"id": "nhom-dong", "ten": "Nhóm Động"}

        # Gỡ module (xoá thư mục và nạp lại)
        shutil.rmtree(m_dir)
        r_nap2 = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap2.status_code == 200

        r3 = client.get("/api/tinh-nang", headers=QT)
        assert not any(m["id"] == "mod-tha-go" for m in r3.json()["modules"])
