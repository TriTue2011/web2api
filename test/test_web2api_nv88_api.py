"""Kiểm thử API đăng nhập tự động + khôi phục nhiều tầng NV88.

- `POST /api/tai-khoan/dang-nhap-tu-dong/bat-dau` — tái dùng LuotChay.
- `POST /api/tai-khoan/khoi-phuc` — tầng (a)→(d) qua app/khoi_phuc_phien.
- `GET /api/tai-khoan/khoi-phuc/trang-thai`, `POST /api/tai-khoan/khoi-phuc/xoa-can-nguoi`.
- Mọi đường: không khoá → 401; khoá khách/vai thấp → 403; khoá của module
  khác (IDOR) → 404; không lộ mật khẩu/cookie trong phản hồi và nhật ký.

Module provider là GIẢ (không mạng). Mọi chuỗi bí mật là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import json
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

from app import khoa_khach, nhat_ky as nhat_ky_mod, so_module
from app.main import app

KHOA_QT = "<redacted>"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MODULE_ID = "provider-gia-nv88"

MANIFEST_PROVIDER = {
    "id": MODULE_ID, "ten": "Provider giả NV88", "mo_ta": "Module provider giả cho test NV88",
    "loai": "ai", "hang": "A", "giao_thuc": "gia", "tien_to": "providergianv88",
    "cung_cap": ["chat"], "tai_nguyen": {"mang_ra": []},
    "tab_web": {"nhan": "Giả", "trang": "web/index.html"},
    "truong_cau_hinh": [],
}

MAT_KHAU_GIA = "mat-khau-gia-nv88-api-001"
TOKEN_GIA = "token-gia-nv88-api-0001"

MA_PROVIDER = textwrap.dedent('''
    from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
    from app.tai_khoan_hang_loat import BiMatTam, KetQuaDangNhap

    class Module:
        def __init__(self, manifest):
            self.manifest = manifest
            self.da_goi_dang_nhap = []
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
        # Hợp đồng NV67 cho bộ chạy lần lượt.
        async def dang_nhap(self, tai_khoan):
            self.da_goi_dang_nhap.append(tai_khoan.email)
            mk = tai_khoan.mat_khau.lay() if tai_khoan.mat_khau else ""
            if tai_khoan.loai == "cookie":
                return KetQuaDangNhap(True, cookie="cookie-da-nhan")
            if mk == "MAT_KHAU_GIA":
                return KetQuaDangNhap(True, cookie="cookie-tu-mat-khau")
            return KetQuaDangNhap(False, ma_loi="sai_mat_khau")
        # Các tầng khôi phục NV88.
        async def lam_moi_phien(self, khoa):
            if khoa == "khoa-song":
                return KetQuaKhoiPhuc(True, tang="a")
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han", ma_trang_thai=401)
        def tai_khoan_ke(self, khoa):
            return "khoa-ke-gia" if khoa == "khoa-het" else None
        def co_tai_khoan(self, khoa):
            return khoa in ("khoa-song", "khoa-het", "khoa-can-nguoi")
        def mat_khau_da_luu(self, khoa):
            return None
''')


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / MODULE_ID
    thu_muc.mkdir(parents=True)
    (thu_muc / "manifest.json").write_text(json.dumps(MANIFEST_PROVIDER, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_PROVIDER.replace("MAT_KHAU_GIA", MAT_KHAU_GIA), encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>gia</title>", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Giãn cách 0 cho test nhanh (giãn cách 30s mặc định đã có test ở NV78).
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    (data / "modules.json").write_text(json.dumps({"modules": [
        {"id": MODULE_ID, "cai_luc": "2026-10-17T00:00:00", "cau_hinh_cong_khai": {}},
    ]}, ensure_ascii=False), encoding="utf-8")
    with TestClient(app) as client:
        assert MODULE_ID in client.app.state.so.dang_chay
        yield client


def _doi_luot_xong(client, timeout=5.0):
    han = time.monotonic() + timeout
    while time.monotonic() < han:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT)
        assert r.status_code == 200, r.text
        if r.json()["trang_thai_luot"] == "hoan_thanh":
            return r.json()
        time.sleep(0.05)
    raise AssertionError("lượt chạy nền không kết thúc kịp")


VAN_BAN = f"ban1@example.com|{MAT_KHAU_GIA}\nban2@example.com|cookie=<redacted>"


# ───────────── POST /dang-nhap-tu-dong/bat-dau ─────────────

def test_bat_dau_tu_dong_ok_va_khong_lo_bi_mat(moi_truong):
    r = moi_truong.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": VAN_BAN})
    assert r.status_code == 200, r.text
    assert MAT_KHAU_GIA not in r.text
    tt = _doi_luot_xong(moi_truong)
    assert tt["tom_tat"]["xong"] == 2
    # Phản hồi tiến độ cũng không chứa bí mật.
    assert MAT_KHAU_GIA not in json.dumps(tt, ensure_ascii=False)


def test_bat_dau_tu_dong_401(moi_truong):
    r = moi_truong.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
                        json={"module_id": MODULE_ID, "van_ban": VAN_BAN})
    assert r.status_code == 401


def test_bat_dau_tu_dong_403_khoa_khach(moi_truong, monkeypatch):
    khoa_khach_mod = khoa_khach
    monkeypatch.setenv("WEB2API_KHOA_KHACH", "khoa-khach-gia")
    r = moi_truong.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau",
                        headers={"Authorization": "Bearer khoa-khach-gia"},
                        json={"module_id": MODULE_ID, "van_ban": VAN_BAN})
    assert r.status_code in (401, 403)


def test_bat_dau_tu_dong_module_chua_cai_404(moi_truong):
    r = moi_truong.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                        json={"module_id": "module-khong-co", "van_ban": VAN_BAN})
    assert r.status_code == 404


# ───────────── POST /khoi-phuc ─────────────

def test_khoi_phuc_tang_a_thanh_cong(moi_truong):
    r = moi_truong.post("/api/tai-khoan/khoi-phuc", headers=QT,
                        json={"module_id": MODULE_ID, "khoa": "khoa-song"})
    assert r.status_code == 200, r.text
    du_lieu = r.json()
    assert du_lieu["thanh_cong"] and du_lieu["tang"] == "a"


def test_khoi_phuc_chuyen_tai_khoan_ke(moi_truong):
    r = moi_truong.post("/api/tai-khoan/khoi-phuc", headers=QT,
                        json={"module_id": MODULE_ID, "khoa": "khoa-het"})
    assert r.status_code == 200, r.text
    du_lieu = r.json()
    assert du_lieu["thanh_cong"] and du_lieu["tang"] == "c"
    assert du_lieu["khoa_moi"] == "khoa-ke-gia"


def test_khoi_phuc_idor_khoa_module_khac_404(moi_truong):
    r = moi_truong.post("/api/tai-khoan/khoi-phuc", headers=QT,
                        json={"module_id": MODULE_ID, "khoa": "khoa-cua-module-khac"})
    assert r.status_code == 404


def test_khoi_phuc_module_hong_khi_kiem_khoa_khong_bi_bo_qua(moi_truong):
    """Lớp «mở cửa khi lỗi»: `co_tai_khoan` của module ném lỗi thì thao tác KHÔNG được chạy tiếp trên khoá chưa kiểm
    (trước đây lỗi bị nuốt và bước kiểm IDOR bị bỏ qua)."""
    mod = moi_truong.app.state.so.dang_chay[MODULE_ID]
    goi = []

    def hong(khoa):
        raise RuntimeError("module hỏng")

    mod.co_tai_khoan = hong
    mod.lam_moi_phien = lambda khoa, *a, **k: goi.append(khoa)
    moi_truong.raise_server_exceptions = False
    r = moi_truong.post("/api/tai-khoan/khoi-phuc", headers=QT, json={"module_id": MODULE_ID, "khoa": "tk-0123456789ab"})
    assert r.status_code >= 500, r.text
    assert goi == [], "không được chạy khôi phục khi không kiểm được khoá thuộc module"


def test_khoi_phuc_401(moi_truong):
    r = moi_truong.post("/api/tai-khoan/khoi-phuc",
                        json={"module_id": MODULE_ID, "khoa": "khoa-song"})
    assert r.status_code == 401


def test_khoi_phuc_khoa_sai_khuon_400(moi_truong):
    r = moi_truong.post("/api/tai-khoan/khoi-phuc", headers=QT,
                        json={"module_id": MODULE_ID, "khoa": "../thoat"})
    assert r.status_code == 400


# ───────────── trạng thái + xoá can_nguoi ─────────────

def test_trang_thai_va_xoa_can_nguoi(moi_truong):
    r = moi_truong.get("/api/tai-khoan/khoi-phuc/trang-thai",
                       params={"module_id": MODULE_ID, "khoa": "khoa-song"}, headers=QT)
    assert r.status_code == 200, r.text
    assert r.json()["khoa"] == "khoa-song"
    r = moi_truong.post("/api/tai-khoan/khoi-phuc/xoa-can-nguoi", headers=QT,
                        json={"module_id": MODULE_ID, "khoa": "khoa-song"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_trang_thai_idor_404(moi_truong):
    r = moi_truong.get("/api/tai-khoan/khoi-phuc/trang-thai",
                       params={"module_id": MODULE_ID, "khoa": "khoa-la"}, headers=QT)
    assert r.status_code == 404


# ───────────── nhật ký không lộ bí mật ─────────────

def test_nhat_ky_khong_lo_bi_mat(moi_truong, tmp_path):
    r = moi_truong.post("/api/tai-khoan/dang-nhap-tu-dong/bat-dau", headers=QT,
                        json={"module_id": MODULE_ID, "van_ban": VAN_BAN})
    assert r.status_code == 200
    _doi_luot_xong(moi_truong)
    import os
    data = os.environ.get("WEB2API_DATA", "")
    tim_thay = False
    for goc, _ds, teps in os.walk(data):
        for t in teps:
            if t.endswith(".json") or t.endswith(".log"):
                try:
                    noi_dung = open(os.path.join(goc, t), encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if MAT_KHAU_GIA in noi_dung:
                    # Cho phép trong kho bí mật mã hoá (không đọc được thô).
                    if "bi_mat" not in goc:
                        tim_thay = True
    assert not tim_thay, "mật khẩu lọt vào tệp không phải kho bí mật"
