# NV262 — Đổi tên trường tai_khoan_google_id → tai_khoan_dnh_id, tương thích ngược.
#
# ĐO qua lõi thật (TestClient). Tên MỚI/TÊN CŨ viết bằng literal trong file
# này để test vẫn chạy được trên nền chưa có NV262 (đỏ), và sau NV262 (xanh).

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEN_MOI = "tai_khoan_dnh_id"
TEN_CU = "tai_khoan_google_id"
_KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE_GIA = "mod-dnh-gia-nv262"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


def _luu(client, **kw):
    than = {"email": kw.pop("email"), "mat_khau": kw.pop("mat_khau", "MatKhauGia123!")}
    than.update(kw)
    return client.post("/api/tai-khoan/da-luu", headers=_QT, json=than)


def _hai_tai_khoan(client, **kw_tk):
    """Tài khoản «đăng nhập hộ» + tài khoản dùng nó; trả (gid, tkid)."""
    g = _luu(client, email="tk-dnh@example.com")
    assert g.status_code == 200, g.text
    gid = g.json()["tai_khoan"]["id"]
    kw = {"email": "tk-chinh@example.com", "dang_nhap_bang": "google", TEN_MOI: gid}
    kw.update(kw_tk)
    t = _luu(client, **kw)
    assert t.status_code == 200, t.text
    return gid, t.json()["tai_khoan"]["id"]


def _chuyen_kho_ve_khuon_cu():
    """Viết lại kho theo khuôn BẢN GHI CŨ (khoá cũ, giá trị dang_nhap_bang cũ).

    Giả lập kho thật đã lưu từ NV223 (kho mã hoá, không đọc được trong test).
    """
    from app import kho_tai_khoan as ktk

    du_lieu = ktk._doc()
    for ban_ghi in du_lieu.values():
        if TEN_MOI in ban_ghi:
            ban_ghi[TEN_CU] = ban_ghi.pop(TEN_MOI)
    ktk._ghi(du_lieu)


def test_luu_bang_ten_moi_doc_ra_ten_moi(moi_truong):
    """API nhận tên mới; bản lưu và bản công khai dùng tên mới."""
    client, _so = moi_truong
    gid, tkid = _hai_tai_khoan(client)
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200
    bk = next(b for b in r.json()["tai_khoan"] if b["id"] == tkid)
    assert bk[TEN_MOI] == gid
    # Bản lưu thô: khoá mới, KHÔNG còn khoá cũ.
    from app import kho_tai_khoan as ktk

    tho = ktk.tim_theo_id(tkid)
    assert tho[TEN_MOI] == gid
    assert TEN_CU not in tho


def test_api_chap_nhan_ten_cu(moi_truong):
    """Tương thích ngược API vào: tên cũ vẫn được chấp nhận, lưu thành tên mới."""
    client, _so = moi_truong
    g = _luu(client, email="tk-dnh@example.com")
    gid = g.json()["tai_khoan"]["id"]
    r = _luu(client, email="tk-chinh@example.com", dang_nhap_bang="google",
             **{TEN_CU: gid})
    assert r.status_code == 200, r.text
    bk = r.json()["tai_khoan"]
    assert bk[TEN_MOI] == gid
    from app import kho_tai_khoan as ktk

    tho = ktk.tim_theo_id(bk["id"])
    assert tho[TEN_MOI] == gid
    assert TEN_CU not in tho


def test_api_uu_tien_ten_moi_khi_co_ca_hai(moi_truong):
    """Cả hai tên trong thân yêu cầu → tên mới thắng."""
    client, _so = moi_truong
    g1 = _luu(client, email="tk-a@example.com")
    g2 = _luu(client, email="tk-b@example.com")
    gid_moi = g1.json()["tai_khoan"]["id"]
    gid_cu = g2.json()["tai_khoan"]["id"]
    r = _luu(client, email="tk-chinh@example.com", dang_nhap_bang="google",
             **{TEN_MOI: gid_moi, TEN_CU: gid_cu})
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"][TEN_MOI] == gid_moi


def test_api_ra_co_ten_moi_va_ten_cu_trong_giai_doan_chuyen(moi_truong):
    """API ra: tên mới + (giai đoạn chuyển) tên cũ CÙNG giá trị."""
    client, _so = moi_truong
    _gid, tkid = _hai_tai_khoan(client)
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    bk = next(b for b in r.json()["tai_khoan"] if b["id"] == tkid)
    assert bk[TEN_MOI] == bk[TEN_CU]
    assert bk[TEN_MOI] != ""


def test_ban_ghi_khoa_cu_doc_ra_dung(moi_truong):
    """Bản ghi lưu kiểu CŨ đọc ra đúng qua tên mới (không cần cập nhật)."""
    client, _so = moi_truong
    gid, tkid = _hai_tai_khoan(client)
    _chuyen_kho_ve_khuon_cu()
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    assert r.status_code == 200
    bk = next(b for b in r.json()["tai_khoan"] if b["id"] == tkid)
    assert bk[TEN_MOI] == gid
    assert bk[TEN_CU] == gid


def test_cap_nhat_ban_ghi_cu_chuyen_sang_khoa_moi(moi_truong):
    """Cập nhật bản ghi cũ → lưu thành khoá mới, xoá khoá cũ."""
    client, _so = moi_truong
    gid, tkid = _hai_tai_khoan(client)
    _chuyen_kho_ve_khuon_cu()
    r = _luu(client, email="tk-chinh@example.com", nhan="nhan moi")
    assert r.status_code == 200, r.text
    assert r.json()["tai_khoan"][TEN_MOI] == gid
    from app import kho_tai_khoan as ktk

    tho = ktk.tim_theo_id(tkid)
    assert tho[TEN_MOI] == gid
    assert TEN_CU not in tho, "khoá cũ phải bị xoá khi cập nhật"


def test_dang_nhap_ho_doc_duoc_ban_ghi_khoa_cu(moi_truong):
    """Route đăng nhập hộ đọc được bản ghi lưu kiểu CŨ (tuyen.py đọc tương thích)."""
    client, so = moi_truong

    class ModuleGia:
        def __init__(self):
            self.da_nhan = []

        async def dang_nhap(self, dong):
            self.da_nhan.append(dong)
            return SimpleNamespace(thanh_cong=True, can_nguoi=False,
                                   cho_2fa=False, huong_dan="", ma_loi="",
                                   jwt_moi=None, ho_so_dang_nhap="")

    fake = ModuleGia()
    fake.manifest = {"id": _MODULE_GIA}
    so.catalog[_MODULE_GIA] = SimpleNamespace(tien_to=_MODULE_GIA)
    so.dang_chay[_MODULE_GIA] = fake

    _gid, tkid = _hai_tai_khoan(client, module_id=_MODULE_GIA)
    _chuyen_kho_ve_khuon_cu()
    r = client.post(f"/api/tai-khoan/da-luu/{tkid}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE_GIA})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert len(fake.da_nhan) == 1
    assert getattr(fake.da_nhan[0], "dnh_email", "") == "tk-dnh@example.com"
