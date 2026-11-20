"""NV305 — Mật khẩu xem trình duyệt (VNC) do chủ máy tự đặt trong «Bảo mật & người dùng».

Đường mới của lõi (quản trị, step-up, giới hạn tần suất):
- PUT /api/trinh-duyet/mat-khau-vnc — đặt mật khẩu qua module may-dang-nhap (module gọi
  dịch vụ bằng khoá dịch vụ đã cấu hình); 1–8 ký tự ASCII in được, không khoảng trắng;
  không bao giờ trả mật khẩu qua API.
- GET /api/trinh-duyet/mat-khau-vnc/trang-thai — chỉ trả {"da_dat": bool} (đã từng đặt qua
  giao diện hay chưa), không trả mật khẩu.

Test qua module GIẢ, không gọi dịch vụ thật, không mật khẩu thật.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import gioi_han, nhat_ky, novnc
from app.main import app

GOC = Path(__file__).resolve().parents[1]
HTML_BAO_MAT = (GOC / "web" / "He-thong-Bao-mat.dc.html").read_text(encoding="utf-8")
KHOA_QT = "khoa-quan-tri-nv305-mat-khau-vnc-123456"
MODULE = "may-dang-nhap"
MK_HOP_LE = "Nv305-T1"  # 8 ký tự, chỉ để test, không phải mật khẩu thật
MK_NGAN = "x"


class ModMkVncGia:
    """Module giả: ghi lại mật khẩu nhận được, không gọi mạng, không lưu gì."""

    def __init__(self, loi=None, da_dat=False, da_nap_lai=True):
        self.loi = loi
        self.da_dat = da_dat
        self.da_nap_lai = da_nap_lai
        self.nhan = []
        self.so_lan_trang_thai = 0

    async def dat_mat_khau_vnc(self, mat_khau: str) -> dict:
        self.nhan.append(mat_khau)
        if isinstance(self.loi, Exception):
            raise self.loi
        if self.loi is not None:
            return self.loi
        return {"ok": True, "da_nap_lai": self.da_nap_lai}

    async def trang_thai_mat_khau_vnc(self) -> dict:
        self.so_lan_trang_thai += 1
        if isinstance(self.loi, Exception):
            raise self.loi
        if self.loi is not None:
            return self.loi
        return {"ok": True, "da_dat": self.da_dat}


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.delenv("WEB2API_TIN_PROXY", raising=False)
    gioi_han.khoi_dong()
    novnc._GIOI_HAN_MK_VNC.clear()
    yield
    with TestClient(app) as c:
        c.app.state.so.dang_chay.pop(MODULE, None)
    novnc._GIOI_HAN_MK_VNC.clear()


def _qt():
    return {"Authorization": f"Bearer {KHOA_QT}"}


def _dat(c, mk, headers=None):
    return c.put("/api/trinh-duyet/mat-khau-vnc", json={"mat_khau": mk},
                 headers=headers if headers is not None else _qt())


def _goi(c, phuong_thuc, headers=None):
    if phuong_thuc == "PUT":
        return _dat(c, MK_HOP_LE, headers=headers)
    return c.get("/api/trinh-duyet/mat-khau-vnc/trang-thai",
                 headers=headers if headers is not None else _qt())


@pytest.mark.parametrize("module_id", ["vnc-nv305-tuy-y", "dich-vu-vnc-khac"])
def test_module_id_tuy_y_van_dat_va_doc_trang_thai(moi_truong, module_id):
    with TestClient(app) as c:
        mod = ModMkVncGia(da_dat=True)
        c.app.state.so.dang_chay[module_id] = mod
        # Thuộc tính có tên phương thức nhưng không callable không phải ứng viên.
        khong_ho_tro = ModMkVncGia()
        khong_ho_tro.dat_mat_khau_vnc = "khong-callable"
        khong_ho_tro.trang_thai_mat_khau_vnc = None
        c.app.state.so.dang_chay[MODULE] = khong_ho_tro
        r = _dat(c, MK_HOP_LE)
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "da_nap_lai": True}
        r = _goi(c, "GET")
        assert r.status_code == 200, r.text
        assert r.json() == {"da_dat": True}
        assert mod.nhan == [MK_HOP_LE]
        assert mod.so_lan_trang_thai == 1
        assert khong_ho_tro.nhan == [] and khong_ho_tro.so_lan_trang_thai == 0


@pytest.mark.parametrize("phuong_thuc", ["GET", "PUT"])
def test_khong_co_callable_vnc_bi_404(moi_truong, phuong_thuc):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        mod.dat_mat_khau_vnc = None
        mod.trang_thai_mat_khau_vnc = "khong-callable"
        c.app.state.so.dang_chay[MODULE] = mod
        r = _goi(c, phuong_thuc)
        assert r.status_code == 404, r.text
        assert mod.nhan == [] and mod.so_lan_trang_thai == 0


@pytest.mark.parametrize("phuong_thuc", ["GET", "PUT"])
@pytest.mark.parametrize("tach_phuong_thuc", [False, True])
def test_hai_ung_vien_bi_409_khong_doc_a_ghi_b(moi_truong, phuong_thuc, tach_phuong_thuc):
    with TestClient(app) as c:
        mod_a, mod_b = ModMkVncGia(), ModMkVncGia()
        if tach_phuong_thuc:
            mod_a.trang_thai_mat_khau_vnc = None
            mod_b.dat_mat_khau_vnc = None
        c.app.state.so.dang_chay[MODULE] = mod_a
        c.app.state.so.dang_chay["vnc-nv305-thu-hai"] = mod_b
        r = _goi(c, phuong_thuc)
        assert r.status_code == 409, r.text
        for mod in (mod_a, mod_b):
            assert mod.nhan == [] and mod.so_lan_trang_thai == 0


@pytest.mark.parametrize("phuong_thuc,thieu", [
    ("PUT", "dat_mat_khau_vnc"), ("GET", "trang_thai_mat_khau_vnc"),
])
def test_mot_ung_vien_thieu_phuong_thuc_tuyen_bi_501(moi_truong, phuong_thuc, thieu):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        setattr(mod, thieu, None)
        c.app.state.so.dang_chay["vnc-nv305-mot-phuong-thuc"] = mod
        r = _goi(c, phuong_thuc)
        assert r.status_code == 501, r.text
        assert mod.nhan == [] and mod.so_lan_trang_thai == 0


@pytest.mark.parametrize("phuong_thuc", ["GET", "PUT"])
@pytest.mark.parametrize("ma_loi,thong_diep", [
    (None, None),
    ("ma-la", None),
    ("sai_khoa_dich_vu", "Khoá dịch vụ không đúng hoặc hết hạn"),
    ("tinh_nang_tat", "Tính năng xem trình duyệt đang tắt trên dịch vụ"),
    ("ngoai_le", None),
])
def test_loi_module_chua_mat_khau_ngan_khong_lo_ra_ngoai(
        moi_truong, phuong_thuc, ma_loi, thong_diep, caplog):
    mk = "Q7!p"  # ASCII ngắn, không dựa vào bộ che token dài.
    loi = {"ok": False, "ma_loi": ma_loi, "loi": f"Khong xu ly duoc {mk}"}
    if ma_loi == "ngoai_le":
        loi = RuntimeError(f"Khong xu ly duoc {mk}")
    with TestClient(app) as c, caplog.at_level(logging.DEBUG):
        mod = ModMkVncGia(loi=loi)
        c.app.state.so.dang_chay[MODULE] = mod
        r = _dat(c, mk) if phuong_thuc == "PUT" else _goi(c, "GET")
        assert r.status_code == 502, r.text
        assert mod.nhan == ([mk] if phuong_thuc == "PUT" else [])
        assert mod.so_lan_trang_thai == (1 if phuong_thuc == "GET" else 0)
        assert mk not in caplog.text
        assert mk not in json.dumps(nhat_ky.doc(gioi_han=500), ensure_ascii=False)
        assert mk not in r.text
        if thong_diep is not None:
            assert r.json()["error"]["message"] == thong_diep


@pytest.mark.parametrize("phuong_thuc", ["GET", "PUT"])
@pytest.mark.parametrize("loai", ["user", "guest"])
def test_user_va_khoa_khach_bi_403_khong_goi_module(moi_truong, phuong_thuc, loai):
    from app import nguoi_dung

    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        if loai == "guest":
            r = c.post("/api/khoa-khach", json={"nhan": "nv305-khach"}, headers=_qt())
            assert r.status_code == 200, r.text
            headers = {"Authorization": f"Bearer {r.json()['khoa']['khoa']}"}
        else:
            asyncio.run(nguoi_dung.tao_nguoi_dung(
                vai="user", ten_dang_nhap="user-nv305", mat_khau="MatKhauGia-nv305-000"))
            r = c.post("/api/dang-nhap", json={
                "ten_dang_nhap": "user-nv305", "mat_khau": "MatKhauGia-nv305-000"})
            assert r.status_code == 200, r.text
            headers = {"X-CSRF-Token": r.json()["csrf"]}
        r = _goi(c, phuong_thuc, headers=headers)
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] != "step_up_required"
        assert mod.nhan == [] and mod.so_lan_trang_thai == 0


def test_khong_khoa_bi_401(moi_truong):
    with TestClient(app) as c:
        c.app.state.so.dang_chay[MODULE] = ModMkVncGia()
        r = c.put("/api/trinh-duyet/mat-khau-vnc", json={"mat_khau": MK_HOP_LE})
        assert r.status_code == 401, r.text


def test_chua_cai_module_bi_404_va_khong_goi_module(moi_truong):
    with TestClient(app) as c:
        r = _dat(c, MK_HOP_LE)
        assert r.status_code == 404, r.text


@pytest.mark.parametrize("mk_xau", ["", "123456789", "ab cd", "mậtkhẩu1", "ab\ncd", 123, None])
def test_mat_khau_sai_dinh_dang_bi_400_va_khong_goi_module(moi_truong, mk_xau):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        r = _dat(c, mk_xau)
        assert r.status_code == 400, f"{mk_xau!r} phải 400, được {r.status_code}: {r.text}"
        assert mod.nhan == []


def test_dat_thanh_cong_khong_lo_mat_khau(moi_truong):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        r = _dat(c, MK_HOP_LE)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True and j["da_nap_lai"] is True
        assert mod.nhan == [MK_HOP_LE]
        assert MK_HOP_LE not in r.text and "mat_khau" not in r.text


def test_dat_1_ky_tu_cung_duoc(moi_truong):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        r = _dat(c, MK_NGAN)
        assert r.status_code == 200, r.text
        assert mod.nhan == [MK_NGAN]


def test_dich_vu_loi_thanh_502(moi_truong):
    with TestClient(app) as c:
        mod = ModMkVncGia(loi={"ok": False, "ma_loi": "sai_khoa_dich_vu",
                               "loi": "Khoá dịch vụ không đúng hoặc hết hạn"})
        c.app.state.so.dang_chay[MODULE] = mod
        r = _dat(c, MK_HOP_LE)
        assert r.status_code == 502, r.text
        assert MK_HOP_LE not in r.text


def test_phien_web_chua_xac_nhan_lai_bi_403_step_up(moi_truong):
    """Phiên web (cookie) chưa xác nhận lại phải bị 403 step_up_required; module không được gọi."""
    from app import nguoi_dung

    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="qt-nv305",
                                              mat_khau="MatKhauGia-nv305-000"))
        r = c.post("/api/dang-nhap", json={"ten_dang_nhap": "qt-nv305",
                                           "mat_khau": "MatKhauGia-nv305-000"})
        assert r.status_code == 200, r.text
        r2 = c.put("/api/trinh-duyet/mat-khau-vnc", json={"mat_khau": MK_HOP_LE},
                   cookies={"w2a_phien": r.cookies.get("w2a_phien")},
                   headers={"X-CSRF-Token": r.json()["csrf"]})
        assert r2.status_code == 403, f"phiên web chưa step-up phải 403, được {r2.status_code}: {r2.text}"
        assert r2.json()["error"]["code"] == "step_up_required"
        assert mod.nhan == []


def test_vuot_gioi_han_tan_suat_bi_429(moi_truong):
    with TestClient(app) as c:
        mod = ModMkVncGia()
        c.app.state.so.dang_chay[MODULE] = mod
        for _ in range(10):
            r = _dat(c, MK_HOP_LE)
            assert r.status_code == 200, r.text
        r = _dat(c, MK_HOP_LE)
        assert r.status_code == 429, f"lần thứ 11 phải 429, được {r.status_code}: {r.text}"


def test_trang_thai_chi_tra_bool_khong_tra_mat_khau(moi_truong):
    with TestClient(app) as c:
        mod = ModMkVncGia(da_dat=True)
        c.app.state.so.dang_chay[MODULE] = mod
        r = c.get("/api/trinh-duyet/mat-khau-vnc/trang-thai", headers=_qt())
        assert r.status_code == 200, r.text
        assert r.json() == {"da_dat": True}
        mod.da_dat = False
        r = c.get("/api/trinh-duyet/mat-khau-vnc/trang-thai", headers=_qt())
        assert r.json() == {"da_dat": False}


def test_trang_thai_khong_khoa_bi_401_va_chua_cai_bi_404(moi_truong):
    with TestClient(app) as c:
        r = c.get("/api/trinh-duyet/mat-khau-vnc/trang-thai")
        assert r.status_code == 401, r.text
        r = c.get("/api/trinh-duyet/mat-khau-vnc/trang-thai", headers=_qt())
        assert r.status_code == 404, r.text


def test_giao_dien_co_o_mat_khau_dung_vi_tri():
    """Ô mật khẩu nằm trong trang Bảo mật & người dùng, khu vực chỉ quản trị, kèm ghi chú giới hạn 8 ký tự."""
    i = HTML_BAO_MAT.index('id="mat-khau-xem-trinh-duyet"')
    khoi = HTML_BAO_MAT[max(0, i - 600):i + 2000]
    assert "laAdmin" in khoi
    assert "Mật khẩu xem trình duyệt" in khoi
    assert "8 ký tự" in khoi
    assert "api/trinh-duyet/mat-khau-vnc" in HTML_BAO_MAT
    assert "••••• đã đặt" in khoi
