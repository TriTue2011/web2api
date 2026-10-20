"""VÁ 1 (soát 2026-10-20, phát hiện T4 + T2 + T1) — test đỏ→xanh:

1. Step-up cho đường quản trị kho tài khoản đã lưu (NV144): POST /da-luu,
   DELETE /da-luu/{id}, POST /da-luu/{id}/dang-nhap gọi bằng PHIÊN WEB quản trị
   phải đã xác nhận lại (step-up) trong cửa sổ ngắn; Bearer quản trị được miễn
   (tương thích ngược). Không step-up → 403 step_up_required; hết cửa sổ → 403.
2. Khoá quản trị (admin_key) mã hoá khi nghỉ bằng khoá chính của app.ham_khoa:
   sinh mới → tệp trên đĩa là gói W2A1 (không thấy khoá dạng rõ); tệp cũ dạng
   rõ → vẫn đọc được và tự di trú sang mã hoá; không có khoá chính → giữ hành
   vi cũ (dạng rõ 0600); tệp đã mã hoá mà thiếu/sai khoá chính → từ chối rõ ràng.
3. docker-compose.yml mẫu: bật WEB2API_BAT_BUOC_MA_HOA=1.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung, phien_web, xac_thuc
from app import so_module
from app.main import app

KHOA_QT = "khoa-qt-va1-cccccccccccccccccccccccccccc"
MK_ADMIN = "MatKhauAdminVa1000000000000"
MODULE_ID = "provider-gia-va1"
EMAIL = "nguoi@example.com"
MAT_KHAU_LUU = "MatKhauLuuVa1-000000000"

MANIFEST_PROVIDER = {
    "id": MODULE_ID, "ten": "Provider giả VA1", "mo_ta": "Module giả cho test vá 1",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "providergiava1",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Giả", "trang": "web/index.html"},
    "truong_cau_hinh": [],
}

MA_PROVIDER = textwrap.dedent('''
    from app.tai_khoan_hang_loat import KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
        def nap(self, cau_hinh):
            pass
        async def cai(self, cau_hinh):
            return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
        async def models(self):
            return []
        async def suc_khoe(self):
            return {"ok": True}
        async def dung(self):
            pass
        async def dang_nhap(self, tai_khoan):
            return KetQuaDangNhap(True, ma_loi="")
''')


@pytest.fixture
def moi_truong_web(tmp_path, monkeypatch):
    """App + module giả + phiên web quản trị đã đăng nhập (CHƯA xác nhận lại)."""
    data = tmp_path / "data"
    thu_muc = data / "modules" / MODULE_ID
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST_PROVIDER, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_PROVIDER, encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": MODULE_ID, "cai_luc": "2026-10-20T00:00:00", "cau_hinh_cong_khai": {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    with TestClient(app) as client:
        assert MODULE_ID in client.app.state.so.dang_chay
        asyncio.run(nguoi_dung.tao_nguoi_dung(
            vai="admin", ten_dang_nhap="admin-va1", mat_khau=MK_ADMIN))
        r = client.post("/api/dang-nhap",
                        json={"ten_dang_nhap": "admin-va1", "mat_khau": MK_ADMIN})
        assert r.status_code == 200, f"đăng nhập web phải 200, nhận {r.status_code}"
        tok = r.cookies.get("w2a_phien")
        csrf = r.json()["csrf"]
        assert tok and csrf
        yield client, {"w2a_phien": tok}, {"X-CSRF-Token": csrf}


def _xac_nhan_lai(client, cookies, headers):
    r = client.post("/api/toi/xac-nhan-lai", json={"khoa_quan_tri": KHOA_QT},
                    cookies=cookies, headers=headers)
    assert r.status_code == 200, f"xác nhận lại phải 200, nhận {r.status_code}: {r.text[:200]}"


def _luu_bang_bearer(client):
    r = client.post("/api/tai-khoan/da-luu",
                    headers={"Authorization": f"Bearer {KHOA_QT}"},
                    json={"email": EMAIL, "mat_khau": MAT_KHAU_LUU, "nhan": "chính"})
    assert r.status_code == 200, f"lưu bằng Bearer phải 200, nhận {r.status_code}"
    return r.json()["tai_khoan"]["id"]


# ── 1. Step-up: không xác nhận lại → từ chối ─────────────────────────────────

def test_luu_khong_step_up_bi_tu_choi(moi_truong_web):
    client, cookies, headers = moi_truong_web
    r = client.post("/api/tai-khoan/da-luu",
                    json={"email": EMAIL, "mat_khau": MAT_KHAU_LUU, "nhan": "chính"},
                    cookies=cookies, headers=headers)
    assert r.status_code == 403, f"chưa step-up phải 403, nhận {r.status_code}"
    assert r.json()["error"]["code"] == "step_up_required"


def test_xoa_khong_step_up_bi_tu_choi(moi_truong_web):
    client, cookies, headers = moi_truong_web
    id_luu = _luu_bang_bearer(client)
    r = client.delete(f"/api/tai-khoan/da-luu/{id_luu}", cookies=cookies, headers=headers)
    assert r.status_code == 403, f"chưa step-up phải 403, nhận {r.status_code}"
    assert r.json()["error"]["code"] == "step_up_required"


def test_dang_nhap_khong_step_up_bi_tu_choi(moi_truong_web):
    client, cookies, headers = moi_truong_web
    id_luu = _luu_bang_bearer(client)
    r = client.post(f"/api/tai-khoan/da-luu/{id_luu}/dang-nhap",
                    json={"module_id": MODULE_ID}, cookies=cookies, headers=headers)
    assert r.status_code == 403, f"chưa step-up phải 403, nhận {r.status_code}"
    assert r.json()["error"]["code"] == "step_up_required"


# ── 1b. Step-up: đã xác nhận lại → cho qua ───────────────────────────────────

def test_co_step_up_thi_luu_duoc(moi_truong_web):
    client, cookies, headers = moi_truong_web
    _xac_nhan_lai(client, cookies, headers)
    r = client.post("/api/tai-khoan/da-luu",
                    json={"email": EMAIL, "mat_khau": MAT_KHAU_LUU, "nhan": "chính"},
                    cookies=cookies, headers=headers)
    assert r.status_code == 200, f"đã step-up phải 200, nhận {r.status_code}"


def test_co_step_up_thi_dang_nhap_duoc(moi_truong_web):
    client, cookies, headers = moi_truong_web
    id_luu = _luu_bang_bearer(client)
    _xac_nhan_lai(client, cookies, headers)
    r = client.post(f"/api/tai-khoan/da-luu/{id_luu}/dang-nhap",
                    json={"module_id": MODULE_ID}, cookies=cookies, headers=headers)
    assert r.status_code == 200, f"đã step-up phải 200, nhận {r.status_code}"
    assert r.json()["thanh_cong"] is True


def test_co_step_up_thi_xoa_duoc(moi_truong_web):
    client, cookies, headers = moi_truong_web
    id_luu = _luu_bang_bearer(client)
    _xac_nhan_lai(client, cookies, headers)
    r = client.delete(f"/api/tai-khoan/da-luu/{id_luu}", cookies=cookies, headers=headers)
    assert r.status_code == 200, f"đã step-up phải 200, nhận {r.status_code}"


# ── 1c. Step-up hết cửa sổ → từ chối ─────────────────────────────────────────

def test_step_up_het_cua_so_bi_tu_choi(moi_truong_web, monkeypatch):
    client, cookies, headers = moi_truong_web
    _xac_nhan_lai(client, cookies, headers)
    monkeypatch.setattr(phien_web, "stepup_cua_so_giay", lambda: -1)
    r = client.post("/api/tai-khoan/da-luu",
                    json={"email": EMAIL, "mat_khau": MAT_KHAU_LUU, "nhan": "chính"},
                    cookies=cookies, headers=headers)
    assert r.status_code == 403, f"step-up hết hạn phải 403, nhận {r.status_code}"
    assert r.json()["error"]["code"] == "step_up_required"


# ── 1d. Tương thích ngược: Bearer quản trị được miễn step-up ─────────────────

def test_bearer_admin_mien_step_up(moi_truong_web):
    client, _, _ = moi_truong_web
    r = client.post("/api/tai-khoan/da-luu",
                    headers={"Authorization": f"Bearer {KHOA_QT}"},
                    json={"email": EMAIL, "mat_khau": MAT_KHAU_LUU, "nhan": "chính"})
    assert r.status_code == 200, f"Bearer quản trị phải 200 (miễn step-up), nhận {r.status_code}"


# ── 2. admin_key mã hoá khi nghỉ ─────────────────────────────────────────────

KHOA_CHINH_BYTES = bytes(range(1, 33))  # 32 byte, đủ entropy
KHOA_CHINH_SAI = bytes(range(101, 133))


def _dat_khoa_chinh(monkeypatch, khoa_bytes: bytes | None):
    if khoa_bytes is None:
        monkeypatch.delenv("WEB2API_KHOA_CHINH", raising=False)
        monkeypatch.delenv("WEB2API_KHOA_CHINH_TEP", raising=False)
    else:
        monkeypatch.setenv(
            "WEB2API_KHOA_CHINH",
            base64.urlsafe_b64encode(khoa_bytes).decode("ascii"))


@pytest.fixture
def du_lieu_admin_key(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.delenv("WEB2API_ADMIN_KEY", raising=False)
    return data


def test_admin_key_sinh_moi_duoc_ma_hoa(du_lieu_admin_key, monkeypatch):
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_BYTES)
    khoa = xac_thuc.khoa_quan_tri()
    assert khoa
    raw = (du_lieu_admin_key / "admin_key").read_bytes()
    assert raw.startswith(b"W2A1"), "tệp admin_key sinh mới phải là gói mã hoá W2A1"
    assert khoa.encode("utf-8") not in raw, "đọc đĩa KHÔNG được thấy admin_key dạng rõ"
    # đọc lại (qua bộ nhớ đệm/đĩa) vẫn ra đúng khoá
    assert xac_thuc.khoa_quan_tri() == khoa


def test_admin_key_cu_dang_ro_tu_di_tru(du_lieu_admin_key, monkeypatch):
    khoa_cu = "khoa-quan-tri-cu-dang-ro-0123456789abcdef"
    p = du_lieu_admin_key / "admin_key"
    p.write_text(khoa_cu + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_BYTES)
    khoa = xac_thuc.khoa_quan_tri()
    assert khoa == khoa_cu, "khởi động với dữ liệu cũ dạng rõ phải vẫn chạy"
    raw = p.read_bytes()
    assert raw.startswith(b"W2A1"), "sau lần đọc đầu, tệp phải tự di trú sang mã hoá"
    assert khoa_cu.encode("utf-8") not in raw


def test_admin_key_cu_khong_khoa_chinh_giu_nguyen(du_lieu_admin_key, monkeypatch):
    khoa_cu = "khoa-quan-tri-cu-khong-khoa-chinh-0123456789"
    p = du_lieu_admin_key / "admin_key"
    p.write_text(khoa_cu + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
    _dat_khoa_chinh(monkeypatch, None)
    assert xac_thuc.khoa_quan_tri() == khoa_cu, "chưa cấu hình khoá chính: hành vi cũ giữ nguyên"
    assert p.read_bytes().startswith(khoa_cu.encode("utf-8"))


def test_admin_key_ma_hoa_thieu_khoa_chinh_tu_choi(du_lieu_admin_key, monkeypatch):
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_BYTES)
    khoa = xac_thuc.khoa_quan_tri()  # sinh + mã hoá
    assert khoa
    _dat_khoa_chinh(monkeypatch, None)
    with pytest.raises(RuntimeError):
        xac_thuc.khoa_quan_tri()


def test_admin_key_ma_hoa_sai_khoa_chinh_tu_choi(du_lieu_admin_key, monkeypatch):
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_BYTES)
    assert xac_thuc.khoa_quan_tri()
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_SAI)
    with pytest.raises(RuntimeError):
        xac_thuc.khoa_quan_tri()


def test_admin_key_quyen_0600(du_lieu_admin_key, monkeypatch):
    _dat_khoa_chinh(monkeypatch, KHOA_CHINH_BYTES)
    xac_thuc.khoa_quan_tri()
    quyen = stat.S_IMODE(os.stat(du_lieu_admin_key / "admin_key").st_mode)
    assert quyen == 0o600, f"tệp admin_key phải 0600, nhận {oct(quyen)}"


# ── 3. Compose mẫu bật mã hoá bắt buộc ───────────────────────────────────────

def test_compose_mau_bat_ma_hoa_bat_buoc():
    noi_dung = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert "WEB2API_BAT_BUOC_MA_HOA" in noi_dung
    assert 'WEB2API_BAT_BUOC_MA_HOA: "1"' in noi_dung or "WEB2API_BAT_BUOC_MA_HOA: '1'" in noi_dung \
        or "WEB2API_BAT_BUOC_MA_HOA: 1" in noi_dung
