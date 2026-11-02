"""NV281 — API khoá của module (lưu, xoá, không lộ). NV296 đổi CHỖ NHẬP: tab «Kết nối» trong bảng chi tiết Module,
không phải trang «Bảo mật & người dùng» — API dưới đây không đổi.

Đi qua LÕI THẬT (TestClient) với module GIẢ có một trường bí mật bắt buộc:
- dán khoá qua đường lưu hiện có (POST /api/modules/{id}/cai) → module vào `dang_chay`
  (tự chạy, không cần cài lại);
- xoá khoá qua route mới DELETE /api/modules/{id}/khoa → module rời `dang_chay`,
  về trạng thái «cần khoá», mã vẫn nằm trên đĩa (catalog còn);
- khoá không bao giờ lộ nguyên văn qua bất kỳ phản hồi/log nào;
- quyền: không khoá quản trị → 401/403, khoá sai → 403, id lạ → 404.
Phần giao diện (tab «Kết nối» của bảng chi tiết Module, K.noiThemKhoa trỏ về đó, neo cũ
#khoa-module-<id> ở trang Bảo mật chuyển về tab này) được kiểm bằng test trình duyệt ở
test_web2api_nv296_tab_ket_noi.py và test_web2api_giao_dien.py, vì nó nằm ở lớp .dc.html, không ở API.
"""

from __future__ import annotations

import json

import pytest

_KHOA_QT = "khoa-qt-nv281-khoa-module-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_KHOA_SAI = {"Authorization": "Bearer khoa-sai-khong-phai-quan-tri"}
KHOA_GIA = "khoa-module-gia-nv281-aaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-nv281"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so, data_dir


class ModGia:
    """Module giả: một khoá API bắt buộc + một trường thường."""

    def __init__(self, manifest=None):
        self.manifest = manifest
        self.kho = None
        self.da_nap = None

    async def cai(self, cau_hinh):
        if not cau_hinh.get("api_key"):
            return {"ok": False, "loi": "thieu khoa"}
        return {"ok": True,
                "ket_qua_tung_khoa": [{"khoa": "api_key", "ok": True}],
                "cau_hinh_luu": cau_hinh}

    def nap(self, cau_hinh):
        self.da_nap = cau_hinh

    async def dung(self):
        pass


def _cai_catalog(so, tmp_path):
    from app.kham_pha import ModuleKhamPha

    manifest = {
        "id": "mod-gia", "ten": "Module giả NV281", "loai": "app", "loai_ten": "App",
        "tien_to": "mod-gia", "cung_cap": [],
        "truong_cau_hinh": [
            {"khoa": "api_key", "nhan": "Khoá API", "kieu": "bi_mat", "bat_buoc": True},
            {"khoa": "base_url", "nhan": "Base URL", "kieu": "chuoi", "bat_buoc": False},
        ],
    }
    so.catalog["mod-gia"] = ModuleKhamPha(
        id="mod-gia", nguon="du_lieu", thu_muc=tmp_path / "mod-gia",
        manifest=manifest, lop=ModGia, van_tay="", ten_ma="mod-gia")


def test_khong_khoa_quan_tri_thi_khong_duoc_doc_hay_xoa_khoa(moi_truong):
    client, so, data = moi_truong
    _cai_catalog(so, data)
    for method, url in (("get", "/api/modules/mod-gia/cau-hinh"),
                        ("delete", "/api/modules/mod-gia/khoa")):
        r = getattr(client, method)(url)
        assert r.status_code in (401, 403), (method, r.status_code, r.text)
    # Khoá sai hoàn toàn → 401 (đo thực tế; can_quan_tri coi khoá không khớp là invalid_api_key).
    r = client.delete("/api/modules/mod-gia/khoa", headers=_KHOA_SAI)
    assert r.status_code == 401, r.text
    # Khoá khách HỢP LỆ nhưng không phải quản trị → 403.
    r_k = client.post("/api/khoa-khach", headers=_QT,
                      json={"nhan": "khach-nv281", "pham_vi": ["chat"]})
    assert r_k.status_code == 200, r_k.text
    hd_khach = {"Authorization": f"Bearer {r_k.json()['khoa']['khoa']}"}
    r = client.delete("/api/modules/mod-gia/khoa", headers=hd_khach)
    assert r.status_code == 403, r.text
    r = client.get("/api/modules/mod-gia/cau-hinh", headers=hd_khach)
    assert r.status_code == 403, r.text


def test_dan_khoa_roi_module_tu_chay_khong_can_cai_lai(moi_truong):
    client, so, _data = moi_truong
    _cai_catalog(so, _data)
    assert "mod-gia" not in so.dang_chay
    r = client.get("/api/modules/mod-gia/cau-hinh", headers=_QT)
    assert r.status_code == 200, r.text
    assert r.json()["da_cau_hinh"] is False
    r = client.post("/api/modules/mod-gia/cai", headers=_QT,
                    json={"cau_hinh": {"api_key": KHOA_GIA}})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "mod-gia" in so.dang_chay, "lưu khoá xong module phải tự chạy"
    assert KHOA_GIA not in r.text
    r = client.get("/api/modules", headers=_QT)
    muc = next(m for m in r.json()["modules"] if m["id"] == "mod-gia")
    assert muc["da_cau_hinh"] is True
    r = client.get("/api/tinh-nang", headers=_QT)
    muc = next(m for m in r.json()["modules"] if m["id"] == "mod-gia")
    assert muc["da_lien_ket"] is True


def test_khoa_khong_lo_qua_pham_hoi_sau_khi_luu(moi_truong):
    client, so, _data = moi_truong
    _cai_catalog(so, _data)
    client.post("/api/modules/mod-gia/cai", headers=_QT,
                json={"cau_hinh": {"api_key": KHOA_GIA}})
    for url in ("/api/modules/mod-gia/cau-hinh", "/api/modules", "/api/tinh-nang"):
        r = client.get(url, headers=_QT)
        assert r.status_code == 200, r.text
        assert KHOA_GIA not in r.text, url


def test_xoa_khoa_roi_module_ve_can_khoa_ma_khong_bi_go(moi_truong, caplog):
    client, so, data = moi_truong
    _cai_catalog(so, data)
    client.post("/api/modules/mod-gia/cai", headers=_QT,
                json={"cau_hinh": {"api_key": KHOA_GIA}})
    assert "mod-gia" in so.dang_chay
    r = client.delete("/api/modules/mod-gia/khoa", headers=_QT)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "id": "mod-gia", "da_xoa": True}
    assert "mod-gia" not in so.dang_chay, "xoá khoá xong module phải rời dang_chay"
    assert "mod-gia" in so.catalog, "xoá khoá KHÔNG được gỡ mã module"
    assert not (data / "bi_mat" / "mod-gia.json").exists(), "tệp bí mật phải bị xoá"
    muc = [m for m in json.loads((data / "modules.json").read_text())["modules"]
           if m["id"] == "mod-gia"]
    assert muc == [], "mục modules.json phải bị xoá"
    r = client.get("/api/modules", headers=_QT)
    muc = next(m for m in r.json()["modules"] if m["id"] == "mod-gia")
    assert muc["da_cau_hinh"] is False
    assert KHOA_GIA not in r.text and KHOA_GIA not in caplog.text


def test_xoa_khoa_module_chua_cai_thi_404(moi_truong):
    client, _so, _data = moi_truong
    r = client.delete("/api/modules/khong-co/khoa", headers=_QT)
    assert r.status_code == 404, r.text


def test_xoa_khoa_khi_chua_co_khoa_luu_van_200_va_khong_hong(moi_truong):
    client, so, data = moi_truong
    _cai_catalog(so, data)
    r = client.delete("/api/modules/mod-gia/khoa", headers=_QT)
    assert r.status_code == 200, r.text
    assert r.json()["da_xoa"] is False
    assert "mod-gia" in so.catalog
