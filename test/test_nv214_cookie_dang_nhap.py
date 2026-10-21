# NV214 — đăng nhập xong thì cookie phiên TỰ vào module sở hữu (nối nốt chuỗi).
#
# Qua lõi thật (TestClient) + module GIẢ hai bên; thêm ca module gemini-web
# THẬT cho phần ghi bền (đọc lại cấu hình ĐÃ LƯU từ kho). Không mạng ngoài.
"""BƯỚC 1 — đo đỏ trước khi nối."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import kho as kho_mod
from app import kho_tai_khoan as ktk
from app.main import app
from app.so_module import SoModule
from app.tai_khoan_hang_loat import KetQuaDangNhap

_KHOA_QT = "khoa-qt-nv214-do-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_THUC_THI = "thuc-thi-gia-nv214"
_SO_HUU = "so-huu-gia-nv214"
_COOKIE = "__Secure-1PSID=phien-gia-nv214-abc; __Secure-1PSIDTS=ts-gia-xyz"
_MIEN = "mien-gia.test"
_EMAIL = "nguoi.dung@example.com"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from app.main import app as _app

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(_app) as client:
        client.app.state.so = SoModule()
        yield client


class _ThucThiGia:
    """Module thực thi đăng nhập giả: dang_nhap thành công + trinh_duyet_cookie."""

    manifest = {"id": _THUC_THI}

    def __init__(self):
        self.dang_nhap_goi = []
        self.cookie_goi = []  # (ho_so, mien)
        self.che_do_cookie = "ok"  # ok | loi | sai_dinh_dang

    async def dang_nhap(self, tai_khoan):
        self.dang_nhap_goi.append(getattr(tai_khoan, "email", ""))
        kq = KetQuaDangNhap(True)
        object.__setattr__(kq, "ho_so_dang_nhap", "ho-so-gia-nv214")
        return kq

    async def dung(self):
        return None

    async def trinh_duyet_cookie(self, ho_so, mien):
        self.cookie_goi.append((ho_so, mien))
        if self.che_do_cookie == "loi":
            return {"ok": False, "loi": "khong_doc_duoc_cookie"}
        if self.che_do_cookie == "sai_dinh_dang":
            return {"cookie": 12345}
        return {"cookie": _COOKIE}


class _SoHuuGia:
    """Module sở hữu giả: khai nhan_cookie, lưu cấu hình trong bộ nhớ."""

    manifest = {"id": _SO_HUU, "truong_cau_hinh": [
        {"khoa": "ma_phien", "kieu": "danh_sach_bi_mat",
         "nhan": "Mã phiên", "nhan_cookie": {"mien": _MIEN}}]}

    def __init__(self):
        self.da_cai = []
        self.da_nap = {}

    def nap(self, cau_hinh):
        self.da_nap = dict(cau_hinh or {})

    async def dung(self):
        return None

    async def cai(self, cau_hinh):
        # so.cai đã ghép {"them": [...]} thành danh sách đầy đủ trước khi gọi.
        self.da_cai.append(dict(cau_hinh))
        ds = cau_hinh.get("ma_phien")
        if isinstance(ds, dict):
            ds = ds.get("them", [])
        return {"ok": True, "ket_qua_tung_khoa": [],
                "cau_hinh_luu": {"ma_phien": list(ds or [])}}


def _dang_ky(client, mod, mid):
    so = client.app.state.so
    ns = SimpleNamespace(id=mid, tien_to=mid,
                         truong_cau_hinh=list(mod.manifest.get("truong_cau_hinh", [])),
                         tao=lambda: mod)
    so.catalog[mid] = ns
    so.dang_chay[mid] = mod
    return mod


def _them_tai_khoan(client, email=_EMAIL, module_so_huu=_SO_HUU):
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": email, "mat_khau": "MatKhauGia123!",
        "module_id": module_so_huu})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]


# ── 1. Tuyến đơn: đăng nhập xong → cookie vào module sở hữu ──────────────────

def test_dang_nhap_don_noi_cookie_vao_module_so_huu(moi_truong):
    client = moi_truong
    thuc_thi = _dang_ky(client, _ThucThiGia(), _THUC_THI)
    so_huu = _dang_ky(client, _SoHuuGia(), _SO_HUU)
    bk = _them_tai_khoan(client)

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True

    # Cookie đã vào module sở hữu qua đúng tên miền nó khai
    assert thuc_thi.cookie_goi == [("ho-so-gia-nv214", _MIEN)]
    assert so_huu.da_cai, "chưa gọi cai() của module sở hữu"
    assert _COOKIE in so_huu.da_nap.get("ma_phien", [])
    # Phản hồi không lộ cookie
    assert "__Secure-1PSID" not in r.text


def test_khong_co_truong_nhan_cookie_thi_bo_qua(moi_truong):
    client = moi_truong
    thuc_thi = _dang_ky(client, _ThucThiGia(), _THUC_THI)

    class _SoHuuKhongNhan:
        manifest = {"id": _SO_HUU, "truong_cau_hinh": [
            {"khoa": "khoa_api", "kieu": "bi_mat", "nhan": "Khoá API"}]}

        def nap(self, cau_hinh):
            pass

        async def cai(self, cau_hinh):
            raise AssertionError("không được gọi cai()")

    _dang_ky(client, _SoHuuKhongNhan(), _SO_HUU)
    bk = _them_tai_khoan(client)

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert thuc_thi.cookie_goi == [], "không được lấy cookie khi module không khai nhan_cookie"


def test_lay_cookie_loi_khong_hong_dang_nhap(moi_truong):
    client = moi_truong
    thuc_thi = _dang_ky(client, _ThucThiGia(), _THUC_THI)
    thuc_thi.che_do_cookie = "loi"
    so_huu = _dang_ky(client, _SoHuuGia(), _SO_HUU)
    bk = _them_tai_khoan(client)

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert so_huu.da_cai == [], "lấy cookie hỏng thì không được cài gì"
    assert "__Secure-1PSID" not in r.text


def test_thuc_thi_khong_co_trinh_duyet_cookie(moi_truong):
    client = moi_truong

    class _ThucThiKhongCookie:
        manifest = {"id": _THUC_THI}

        async def dang_nhap(self, tai_khoan):
            kq = KetQuaDangNhap(True)
            object.__setattr__(kq, "ho_so_dang_nhap", "ho-so-gia-nv214")
            return kq

    _dang_ky(client, _ThucThiKhongCookie(), _THUC_THI)
    so_huu = _dang_ky(client, _SoHuuGia(), _SO_HUU)
    bk = _them_tai_khoan(client)

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert so_huu.da_cai == []


def test_khong_ho_so_thi_khong_goi_trinh_duyet_cookie(moi_truong):
    client = moi_truong
    goi = []

    class _ThucThiKhongHoSo:
        manifest = {"id": _THUC_THI}

        async def dang_nhap(self, tai_khoan):
            return KetQuaDangNhap(True)  # không báo ho_so_dang_nhap

        async def trinh_duyet_cookie(self, ho_so, mien):
            goi.append((ho_so, mien))
            return {"cookie": "X=Y"}

        async def dung(self):
            return None

    _dang_ky(client, _ThucThiKhongHoSo(), _THUC_THI)
    _dang_ky(client, _SoHuuGia(), _SO_HUU)
    bk = _them_tai_khoan(client)

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert goi == [], f"đã gọi trinh_duyet_cookie khi thiếu ho_so: {goi}"


# ── 2. Tuyến hàng loạt dùng chung nối ────────────────────────────────────────
def _cho_luot_xong(client, timeout=20):
    bat_dau = time.monotonic()
    while time.monotonic() - bat_dau < timeout:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=_QT)
        assert r.status_code == 200, r.text
        tt = r.json()
        if tt["trang_thai_luot"] == "hoan_thanh":
            return tt
        time.sleep(0.2)
    raise AssertionError("lượt hàng loạt không xong trong trần")


def test_hang_loat_noi_cookie_vao_module_so_huu(moi_truong):
    client = moi_truong
    thuc_thi = _dang_ky(client, _ThucThiGia(), _THUC_THI)
    so_huu = _dang_ky(client, _SoHuuGia(), _SO_HUU)
    _them_tai_khoan(client)

    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT,
                    json={"module_id": _THUC_THI, "module_so_huu": _SO_HUU})
    assert r.status_code == 200, r.text
    tt = _cho_luot_xong(client)
    assert tt["tom_tat"]["xong"] >= 1, tt["tom_tat"]

    assert thuc_thi.cookie_goi == [("ho-so-gia-nv214", _MIEN)]
    assert so_huu.da_cai, "tuyến hàng loạt chưa nối cookie"
    assert _COOKIE in so_huu.da_nap.get("ma_phien", [])


# ── 3. Module gemini-web thật: ghi bền, đọc lại từ kho ──────────────────────

def test_gemini_web_that_ghi_ben_cookie(moi_truong, monkeypatch):
    import importlib.util
    import sys
    import types
    from pathlib import Path

    goc = Path(__file__).resolve().parent.parent
    # module.py dùng relative import (from .client import ...) nên phải nạp
    # qua một package giả trỏ đúng thư mục modules/gemini-web.
    pkg = types.ModuleType("gw_pkg_nv214")
    pkg.__path__ = [str(goc / "modules" / "gemini-web")]
    sys.modules["gw_pkg_nv214"] = pkg
    spec = importlib.util.spec_from_file_location(
        "gw_pkg_nv214.module", str(goc / "modules" / "gemini-web" / "module.py"))
    gw = importlib.util.module_from_spec(spec)
    sys.modules["gw_pkg_nv214.module"] = gw
    spec.loader.exec_module(gw)
    manifest = json.loads((goc / "modules" / "gemini-web" / "manifest.json").read_text())

    async def _init_gia(self, tk):
        return None  # bỏ qua kiểm mạng, vẫn qua nhánh «tốt» của cai()

    monkeypatch.setattr(gw.Module, "_init_tai_khoan", _init_gia)

    client = moi_truong
    thuc_thi = _dang_ky(client, _ThucThiGia(), _THUC_THI)
    gw_mod = gw.Module(manifest)
    _dang_ky(client, gw_mod, "gemini-web")
    bk = _them_tai_khoan(client, module_so_huu="gemini-web")

    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/dang-nhap",
                    headers=_QT, json={"module_id": _THUC_THI})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert thuc_thi.cookie_goi == [("ho-so-gia-nv214", "gemini.google.com")]

    # Đọc lại cấu hình ĐÃ LƯU từ kho (không qua bộ nhớ tiến trình)
    muc = next(m for m in kho_mod.doc_modules()[0] if m["id"] == "gemini-web")
    cau_hinh = kho_mod.doc_cau_hinh("gemini-web", manifest["truong_cau_hinh"], muc)
    assert _COOKIE in cau_hinh.get("cookies", [])
    assert "__Secure-1PSID" not in r.text
