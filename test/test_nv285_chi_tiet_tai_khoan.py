"""NV285 — API chi tiết một tài khoản (limit, đã dùng, phiên).

Module GIẢ, tài khoản GIẢ (example.com), không gọi mạng, không tài khoản
thật. Chuỗi bí mật giả cấy vào module để quét rò rỉ.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_KHOA_QT = "khoa-qt-nv285-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE = "chatgpt-web"
_TOKEN_GIA = "TOKEN-GIA-NV285-9f8e7d6c5b4a"
_COOKIE_GIA = "COOKIE-GIA-NV285-1a2b3c4d5e6f"


class _ModuleGia:
    """Module giả có/không có khả năng chi_tiet/lam_moi (tuỳ bảng)."""

    def __init__(self, bang_chi_tiet=None, thu_tu_email=None):
        self._bang = bang_chi_tiet or {}
        self._thu_tu = thu_tu_email or []
        self.lam_moi_goi = []

    def chi_tiet_tai_khoan(self, email):
        return self._bang.get(str(email or "").strip().lower())

    async def lam_moi_han_muc(self, email):
        self.lam_moi_goi.append(str(email or "").strip().lower())
        return self._bang.get(str(email or "").strip().lower())

    def liet_ke_tai_khoan(self):
        return [{"email": e} for e in self._thu_tu]


class _ModuleKhongChiTiet:
    def liet_ke_tai_khoan(self):
        return []


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


def _cai(so, fake):
    fake.manifest = {"id": _MODULE, "nhom": {"id": "chatgpt", "ten": "ChatGPT"}}
    so.catalog[_MODULE] = SimpleNamespace(tien_to=_MODULE, manifest=fake.manifest)
    so.dang_chay[_MODULE] = fake


def _tao(client_or_none, email, ket_qua=None, han_muc=None):
    """Tạo tài khoản qua kho thật (không qua HTTP để set đủ nhóm/loại/đích)."""
    from app import kho_tai_khoan as ktk

    bk = ktk.luu_tai_khoan(email=email, mat_khau="MatKhauGia123!",
                           module_id=_MODULE, nhom="chatgpt",
                           loai_tai_khoan="web", dich_dung="web")
    if ket_qua is not None:
        ktk.ghi_ket_qua_dang_nhap(bk["id"], ket_qua,
                                  co_phien=(ket_qua == "thanh_cong"))
    return bk


def _chi_tiet(client, tk_id, headers=_QT):
    return client.get(f"/api/tai-khoan/da-luu/{tk_id}/chi-tiet", headers=headers)


_MUC_ANH = {"ten": "image_gen", "nhan": "Tạo ảnh", "con_lai": 5, "tong": 20,
            "da_dung_pct": None, "reset_luc": None, "reset_sau_giay": 3600}


def test_chi_tiet_du_truong_voi_module_co_han_muc(moi_truong):
    client, so = moi_truong
    fake = _ModuleGia({
        "ct1-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1700000000,
                                  "muc": [_MUC_ANH],
                                  "token": _TOKEN_GIA, "cookie": _COOKIE_GIA},
    }, thu_tu_email=["ct1-nv285@example.com"])
    _cai(so, fake)
    bk = _tao(client, "ct1-nv285@example.com", ket_qua="thanh_cong")
    r = _chi_tiet(client, bk["id"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == bk["id"]
    assert d["loai_hien_thi"] == "Web"
    assert d["goi"] == "plus"
    assert d["dang_nhap"]["trang_thai"] == "ok"
    assert d["dung"] == {"hom_nay": 0, "7_ngay": 0, "thanh_cong": 0,
                         "that_bai": 0, "theo_ngay": [], "lan_dung_cuoi": 0}
    assert {"dich": "web", "trang_thai": "san_sang", "moc_hoi_lai": 0} in d["duong"]
    assert d["han_muc"]["nguon"] == "module"
    assert d["han_muc"]["cap_nhat_luc"] == 1700000000
    muc = d["han_muc"]["muc"][0]
    assert muc["ten"] == "image_gen" and muc["con_lai"] == 5 and muc["tong"] == 20
    assert muc["hang"] == 1
    assert muc["giu_hang_1_cua_nhom"]["tai_khoan_id"] == bk["id"]
    # Chuỗi bí mật cấy trong trường lạ của module KHÔNG lọt ra phản hồi.
    assert _TOKEN_GIA not in r.text and _COOKIE_GIA not in r.text
    assert "MatKhauGia123!" not in r.text


def test_chi_tiet_module_khong_co_han_muc_tra_khong_biet(moi_truong):
    client, so = moi_truong
    _cai(so, _ModuleKhongChiTiet())
    bk = _tao(client, "ct2-nv285@example.com", ket_qua="thanh_cong")
    r = _chi_tiet(client, bk["id"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["han_muc"] == {"nguon": "khong_biet", "cap_nhat_luc": 0, "muc": []}
    assert d["goi"] == "khong_biet"


def test_chi_tiet_han_muc_tu_tieu_de_khi_module_khong_khai(moi_truong):
    from app import luong_dung_tai_khoan as ldtk

    client, so = moi_truong
    _cai(so, _ModuleKhongChiTiet())
    bk = _tao(client, "ct3-nv285@example.com", ket_qua="thanh_cong")
    assert ldtk.ghi_luot(bk["id"], ma=200,
                         headers={"x-ratelimit-remaining": "7"}) is True
    d = _chi_tiet(client, bk["id"]).json()
    assert d["han_muc"]["nguon"] == "tieu_de"
    assert d["han_muc"]["muc"] == []  # tiêu đề không có mục từng tính năng


def test_lam_moi_gian_cach_lan_hai_429(moi_truong):
    client, so = moi_truong
    fake = _ModuleGia({"lm-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1,
                                                "muc": [_MUC_ANH]}})
    _cai(so, fake)
    bk = _tao(client, "lm-nv285@example.com", ket_qua="thanh_cong")
    r1 = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/chi-tiet/lam-moi", headers=_QT)
    assert r1.status_code == 200, r1.text
    assert r1.json()["ok"] is True
    assert fake.lam_moi_goi == ["lm-nv285@example.com"]
    r2 = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/chi-tiet/lam-moi", headers=_QT)
    assert r2.status_code == 429, r2.text
    assert 0 < r2.json()["cho_lai_sau_giay"] <= 60
    assert fake.lam_moi_goi == ["lm-nv285@example.com"]  # lần hai không gọi module


def test_lam_moi_module_khong_ho_tro_502_va_tam_dung_403(moi_truong):
    from app import kho_tai_khoan as ktk

    client, so = moi_truong
    _cai(so, _ModuleKhongChiTiet())
    bk = _tao(client, "lm2-nv285@example.com", ket_qua="thanh_cong")
    r = client.post(f"/api/tai-khoan/da-luu/{bk['id']}/chi-tiet/lam-moi", headers=_QT)
    assert r.status_code == 502, r.text
    bk2 = _tao(client, "lm3-nv285@example.com", ket_qua="thanh_cong")
    ktk.dat_tam_dung(bk2["id"], True)
    r = client.post(f"/api/tai-khoan/da-luu/{bk2['id']}/chi-tiet/lam-moi", headers=_QT)
    assert r.status_code == 403, r.text


def test_chi_tiet_404_va_xac_thuc(moi_truong):
    client, so = moi_truong
    _cai(so, _ModuleGia({}))
    bk = _tao(client, "ct4-nv285@example.com")
    r = client.get("/api/tai-khoan/da-luu/tk-khong-co/chi-tiet", headers=_QT)
    assert r.status_code == 404, r.text
    r = _chi_tiet(client, bk["id"], headers={})
    assert r.status_code == 401, r.text
    # Khoá lạ (không khớp khoá nào) cũng 401; khoá khách đúng nhưng sai
    # phạm vi (chỉ chat, không phải quản trị) → 403.
    r = _chi_tiet(client, bk["id"],
                  headers={"Authorization": "Bearer khoa-sai-nv285"})
    assert r.status_code == 401, r.text
    from app import khoa_khach as kk

    _muc, khoa_khach = kk.tao_khoa(nhan="khach-nv285", pham_vi=["chat"])
    r = _chi_tiet(client, bk["id"],
                  headers={"Authorization": f"Bearer {khoa_khach}"})
    assert r.status_code == 403, r.text
    # IDOR: chi-tiet của A không chứa gì của tài khoản khác.
    bk2 = _tao(client, "khac-nv285@example.com")
    r = _chi_tiet(client, bk["id"])
    assert "khac-nv285" not in r.text


def test_dung_thong_ke_thanh_cong_that_bai_tach_duoc(moi_truong):
    from app import luong_dung_tai_khoan as ldtk

    client, so = moi_truong
    _cai(so, _ModuleGia({}))
    bk = _tao(client, "tk-nv285@example.com", ket_qua="thanh_cong")
    for _ in range(3):
        assert ldtk.ghi_luot(bk["id"], ma=200) is True
    for _ in range(2):
        assert ldtk.ghi_luot(bk["id"], ma=500) is True
    d = _chi_tiet(client, bk["id"]).json()
    assert d["dung"]["hom_nay"] == 3
    assert d["dung"]["7_ngay"] == 3
    assert d["dung"]["thanh_cong"] == 3
    assert d["dung"]["that_bai"] == 2
    assert len(d["dung"]["theo_ngay"]) == 1
    assert d["dung"]["theo_ngay"][0]["so_luot"] == 3
    assert d["dung"]["lan_dung_cuoi"] > 0


def test_tong_quan_nhom_va_hang_xoay_vong(moi_truong):
    client, so = moi_truong
    bang = {
        "a-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1, "muc": [
            {"ten": "image_gen", "nhan": "Tạo ảnh", "con_lai": 5, "tong": 20}]},
        "d-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1, "muc": [
            {"ten": "image_gen", "nhan": "Tạo ảnh", "con_lai": 3, "tong": 20}]},
        "b-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1, "muc": [
            {"ten": "image_gen", "nhan": "Tạo ảnh", "con_lai": 0, "tong": 20}]},
        "c-nv285@example.com": {"goi": "plus", "cap_nhat_luc": 1, "muc": [
            {"ten": "image_gen", "nhan": "Tạo ảnh", "con_lai": 9, "tong": 20}]},
    }
    thu_tu = ["d-nv285@example.com", "a-nv285@example.com",
              "b-nv285@example.com", "c-nv285@example.com"]
    _cai(so, _ModuleGia(bang, thu_tu_email=thu_tu))
    ids = {
        "a": _tao(client, "a-nv285@example.com", ket_qua="thanh_cong")["id"],
        "d": _tao(client, "d-nv285@example.com", ket_qua="thanh_cong")["id"],
        "b": _tao(client, "b-nv285@example.com", ket_qua="thanh_cong")["id"],
        "c": _tao(client, "c-nv285@example.com")["id"],  # chua: chưa đăng nhập
    }
    r = client.get("/api/tai-khoan/tong-quan?nha=chatgpt&duong=web", headers=_QT)
    assert r.status_code == 200, r.text
    tq = r.json()
    assert tq["tong"] == 4
    assert tq["binh_thuong"] == 2  # a, d ok và còn lượt
    assert tq["chua_biet"] == 1
    assert tq["can_xu_ly"] == 0 and tq["loi"] == 0 and tq["tam_dung"] == 0
    # b ok nhưng cạn: không rơi vào ô trạng thái nào, chỉ đếm ở het_limit.
    assert tq["het_limit_theo_muc"] == [
        {"ten": "image_gen", "nhan": "Tạo ảnh",
         "so_tai_khoan_can": 1, "so_tai_khoan_co_so_lieu": 4}]
    assert tq["co_so_lieu_han_muc"] is True
    assert len(tq["giu_hang_1"]) == 1
    giu = tq["giu_hang_1"][0]
    assert giu["nhom"] == "chatgpt/web" and giu["muc"] == "image_gen"
    assert giu["tai_khoan_id"] == ids["d"]  # thứ tự module: d trước a
    # Hạng trong chi-tiet từng tài khoản.
    assert _chi_tiet(client, ids["d"]).json()["han_muc"]["muc"][0]["hang"] == 1
    assert _chi_tiet(client, ids["a"]).json()["han_muc"]["muc"][0]["hang"] == 2
    assert _chi_tiet(client, ids["b"]).json()["han_muc"]["muc"][0]["hang"] is None
    assert _chi_tiet(client, ids["c"]).json()["han_muc"]["muc"][0]["hang"] is None
    ct_a = _chi_tiet(client, ids["a"]).json()["han_muc"]["muc"][0]
    assert ct_a["giu_hang_1_cua_nhom"]["tai_khoan_id"] == ids["d"]


def test_tong_quan_nhom_khong_so_lieu_rong(moi_truong):
    client, so = moi_truong
    _cai(so, _ModuleGia({}))
    _tao(client, "x-nv285@example.com", ket_qua="thanh_cong")
    r = client.get("/api/tai-khoan/tong-quan?nha=google&duong=web", headers=_QT)
    assert r.status_code == 200, r.text
    tq = r.json()
    assert tq["tong"] == 0
    assert tq["het_limit_theo_muc"] == []
    assert tq["co_so_lieu_han_muc"] is False
    assert tq["giu_hang_1"] == []
