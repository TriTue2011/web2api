"""NV210 (điều phối nối) — đăng nhập xong thì JWT MỚI + HỒ SƠ phải vào khoá sổ của module SỞ HỮU tài khoản.

Muse viết hàm trả ``jwt_moi`` nhưng để «móc nối còn lại» cho tầng ứng dụng: không có nơi nào dùng nó, nên
đăng nhập ChatGPT xong token mới và hồ sơ bị bỏ rơi (lớp lỗi «viết hàm nhưng không cắm vào»). Test này đi qua
LÕI THẬT (TestClient) với module GIẢ: executor (đăng nhập) trả ``jwt_moi``, module sở hữu khai
``gan_jwt_tu_dang_nhap``. Lõi nối theo KHẢ NĂNG, không theo tên module.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv210-noi-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
TOKEN_MOI = "eyJ-jwt-gia-nv210-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
HO_SO = "chatgpt-hs-gia-nv210"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


class ChuSoHuuGia:
    """Module sở hữu tài khoản: khai khả năng gắn JWT; ghi lại mọi lời gọi."""

    def __init__(self, ho_so_co_san=(), nem=None):
        self.goi: list[tuple] = []
        self.manifest = {"id": "so-huu-gia", "nhom": {"id": "nhom-gia", "ten": "Nhóm giả"}}
        self._ho_so_co_san = list(ho_so_co_san)
        self._nem = nem

    def gan_jwt_tu_dang_nhap(self, email, token, ho_so):
        if self._nem:
            raise self._nem
        self.goi.append((email, token, ho_so))
        return {"ok": True}

    def liet_ke_tai_khoan(self):
        if self._nem == "liet_ke":
            raise RuntimeError("loi-liet-ke")
        return [{"email": e, "ho_so": "hs"} for e in self._ho_so_co_san]


def _cai(so, module_id, doi_tuong, manifest=None):
    man = manifest or getattr(doi_tuong, "manifest", None) or {"id": module_id}
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id, manifest=man)
    so.dang_chay[module_id] = doi_tuong


def _executor(thanh_cong=True, co_jwt=True):
    da_goi: list[str] = []

    async def dang_nhap(dong):
        da_goi.append(dong.email)
        jwt = {"access_token": TOKEN_MOI, "ho_so": HO_SO, "email": dong.email} if (thanh_cong and co_jwt) else None
        return SimpleNamespace(thanh_cong=thanh_cong, can_nguoi=False, cho_2fa=False, huong_dan="",
                               ma_loi="" if thanh_cong else "dang_nhap_that_bai", jwt_moi=jwt)

    return SimpleNamespace(manifest={"id": "ex-gia"}, dang_nhap=dang_nhap, da_goi=da_goi)


def _luu(client, email, module_id="so-huu-gia"):
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json={
        "email": email, "mat_khau": "MatKhauGia123!", "module_id": module_id})
    assert r.status_code == 200, r.text
    return r.json()["tai_khoan"]["id"]


def _dang_nhap(client, tk_id):
    return client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=_QT, json={"module_id": "ex-gia"})


# ── một tài khoản ───────────────────────────────────────────────

def test_dang_nhap_xong_gan_jwt_vao_module_so_huu(moi_truong):
    client, so = moi_truong
    chu = ChuSoHuuGia(); _cai(so, "so-huu-gia", chu); _cai(so, "ex-gia", _executor())
    tk = _luu(client, "nguoi-1@example.com")
    r = _dang_nhap(client, tk)
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert chu.goi == [("nguoi-1@example.com", TOKEN_MOI, HO_SO)]
    assert TOKEN_MOI not in r.text, "token mới KHÔNG được đi ra phản hồi"


def test_loi_khi_gan_khong_lam_hong_ket_qua_dang_nhap(moi_truong, caplog):
    client, so = moi_truong
    chu = ChuSoHuuGia(nem=RuntimeError("hong-trong-module")); _cai(so, "so-huu-gia", chu); _cai(so, "ex-gia", _executor())
    r = _dang_nhap(client, _luu(client, "nguoi-2@example.com"))
    assert r.status_code == 200 and r.json()["thanh_cong"] is True
    assert TOKEN_MOI not in r.text and TOKEN_MOI not in caplog.text


def test_module_so_huu_khong_khai_ham_thi_bo_qua_im_lang(moi_truong):
    client, so = moi_truong
    _cai(so, "so-huu-gia", SimpleNamespace(manifest={"id": "so-huu-gia"}))
    _cai(so, "ex-gia", _executor())
    r = _dang_nhap(client, _luu(client, "nguoi-3@example.com"))
    assert r.status_code == 200 and r.json()["thanh_cong"] is True
    assert TOKEN_MOI not in r.text


def test_dang_nhap_that_bai_thi_khong_gan(moi_truong):
    client, so = moi_truong
    chu = ChuSoHuuGia(); _cai(so, "so-huu-gia", chu); _cai(so, "ex-gia", _executor(thanh_cong=False))
    r = _dang_nhap(client, _luu(client, "nguoi-4@example.com"))
    assert r.status_code == 200 and r.json()["thanh_cong"] is False
    assert chu.goi == []


def test_khong_co_jwt_moi_thi_khong_goi_ham_gan(moi_truong):
    client, so = moi_truong
    chu = ChuSoHuuGia(); _cai(so, "so-huu-gia", chu); _cai(so, "ex-gia", _executor(co_jwt=False))
    r = _dang_nhap(client, _luu(client, "nguoi-5@example.com"))
    assert r.status_code == 200 and r.json()["thanh_cong"] is True
    assert chu.goi == []


# ── hàng loạt (tuyến chưa từng có test) ──────────────────────────────

def _cho_luot_xong(client, toi_da=20.0):
    han = time.time() + toi_da
    while time.time() < han:
        r = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=_QT)
        if r.status_code == 200 and r.json().get("trang_thai_luot") != "dang_chay":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("lượt hàng loạt không kết thúc")


def test_hang_loat_gan_jwt_cho_tung_tai_khoan_va_bo_qua_da_co_ho_so(moi_truong):
    client, so = moi_truong
    chu = ChuSoHuuGia(ho_so_co_san=["da-co@example.com"])
    _cai(so, "so-huu-gia", chu); ex = _executor(); _cai(so, "ex-gia", ex)
    for e in ("a1@example.com", "a2@example.com", "da-co@example.com"):
        _luu(client, e)
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT, json={
        "module_id": "ex-gia", "module_so_huu": "so-huu-gia", "chi_chua_co_ho_so": True})
    assert r.status_code == 200, r.text
    assert r.json()["tong"] == 2, "tài khoản đã có hồ sơ phải bị bỏ qua"
    _cho_luot_xong(client)
    assert sorted(e for e, _, _ in chu.goi) == ["a1@example.com", "a2@example.com"]
    assert sorted(ex.da_goi) == ["a1@example.com", "a2@example.com"]
    assert TOKEN_MOI not in json.dumps(client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=_QT).json())


def test_hang_loat_khong_doc_duoc_danh_sach_ho_so_thi_dung_khong_chay(moi_truong):
    """Lỗi liệt kê hồ sơ KHÔNG được nuốt rồi đăng nhập lại cả lô: dừng 502, không gọi executor."""
    client, so = moi_truong
    chu = ChuSoHuuGia(nem="liet_ke")
    _cai(so, "so-huu-gia", chu); ex = _executor(); _cai(so, "ex-gia", ex)
    _luu(client, "b1@example.com"); _luu(client, "b2@example.com")
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=_QT, json={
        "module_id": "ex-gia", "module_so_huu": "so-huu-gia", "chi_chua_co_ho_so": True})
    assert r.status_code == 502, r.text
    assert r.json()["error"]["code"] == "khong_doc_duoc_ho_so"
    assert ex.da_goi == []


def test_hang_loat_chua_dang_nhap_thi_401(moi_truong):
    client, _so = moi_truong
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", json={
        "module_id": "ex-gia", "module_so_huu": "so-huu-gia"})
    assert r.status_code in (401, 403)


# ── hàm nối (đơn vị): token luôn bị xoá khỏi kết quả, không bao giờ ném ───────────────────

def _kq_co_jwt():
    return SimpleNamespace(thanh_cong=True, jwt_moi={"access_token": TOKEN_MOI, "ho_so": HO_SO, "email": "e@example.com"})


def test_ap_dung_luon_xoa_jwt_moi_khoi_ket_qua_du_gan_duoc_hay_khong():
    from app import gan_ket_qua_dang_nhap as g
    chu = ChuSoHuuGia()
    so = SimpleNamespace(dang_chay={"chu": chu, "khong-ham": SimpleNamespace()})
    for module_so_huu, mong_doi in (("chu", True), ("khong-ham", False), ("khong-co", False), ("", False)):
        kq = _kq_co_jwt()
        assert g.ap_dung(so, kq, module_so_huu) is mong_doi
        assert kq.jwt_moi is None, "token mới không được nằm lại trong kết quả"


def test_ap_dung_khong_nem_khi_ham_gan_hong_va_khong_log_token(caplog):
    from app import gan_ket_qua_dang_nhap as g
    so = SimpleNamespace(dang_chay={"chu": ChuSoHuuGia(nem=RuntimeError(TOKEN_MOI))})
    kq = _kq_co_jwt()
    assert g.ap_dung(so, kq, "chu") is False
    assert kq.jwt_moi is None
    assert TOKEN_MOI not in caplog.text, "log chỉ ghi tên kiểu lỗi, không kèm nội dung"
