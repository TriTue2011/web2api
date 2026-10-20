"""NV182 — hành động trên thực thể «nguy hiểm» do quản trị tự chạy qua PHIÊN WEB phải qua step-up (xác nhận lại).

Lớp lỗi (soát độc lập C1/K1): lõi chặn 403 `step_up_required` ở mọi thao tác quản trị nguy hiểm (tạo admin, đặt lại mật khẩu,
duyệt hàng chờ…) nhưng đường `/api/thiet-bi/{id}/hanh-dong` chạy thẳng thực thể nguy hiểm bằng phiên cookie CHƯA xác nhận lại.
Đúng nguyên tắc: quyết định theo MỨC RỦI RO HIỆU LỰC của thực thể (không theo tên module). Bearer quản trị được miễn như mọi nơi khác;
khách vẫn vào hàng chờ (202), không bị 403 step-up.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import nguoi_dung
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-qt-nv182-aaa-00000000000000000000000"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_ADMIN = "MatKhauAdminGia000000000000"

_MANIFEST = {
    "id": "tb-stepup", "ten": "Thiết bị step-up", "mo_ta": "Thử step-up", "phien_ban": "0.1.0", "hang": "A",
    "giao_thuc": "0.1", "loai": "thiet_bi", "loai_ten": "Thiết bị", "tien_to": "tbsu", "cung_cap": ["thiet_bi"],
    "truong_cau_hinh": [],
    "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": None},
}
_MA = """
class Module:
    def __init__(self, manifest):
        self.tien_to = manifest["tien_to"]
        self.so_lan = 0
    def nap(self, cau_hinh):
        pass
    async def cai(self, cau_hinh):
        return {"ok": True, "loi": None, "cau_hinh_luu": {}}
    async def thuc_the(self, id_thuc_the=None):
        return [{"id": f"{self.tien_to}.cua", "ten": "Cửa", "loai": "cua", "tinh_nang": ["mo"], "rui_ro_mac_dinh": "nguy_hiem"},
                {"id": f"{self.tien_to}.den", "ten": "Đèn", "loai": "den", "tinh_nang": ["bat"], "rui_ro_mac_dinh": "ghi"}]
    async def trang_thai(self, ids):
        return {i: {"gia_tri": "ok"} for i in ids}
    async def thuc_hien(self, id_thuc_the, hanh_dong, tham_so):
        return {"thanh_cong": True, "id": id_thuc_the}
    async def suc_khoe(self):
        return {"khoa": []}
    async def dung(self):
        pass
"""


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        ghi_thu_muc(data_dir / "modules" / "tb-stepup",
                    {"manifest.json": json.dumps(_MANIFEST).encode(), "module.py": _MA.encode()})
        client.post("/api/modules/nap-lai", headers=QT)
        assert client.post("/api/modules/tb-stepup/cai", headers=QT, json={"cau_hinh": {}}).status_code == 200
        for t in ("tbsu.cua", "tbsu.den"):
            assert client.post(f"/api/thiet-bi/{t}/xac-nhan", headers=QT).status_code == 200
        yield client


def _phien_admin(client):
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap="admin-nv182", mat_khau=MK_ADMIN))
    r = client.post("/api/dang-nhap", json={"ten_dang_nhap": "admin-nv182", "mat_khau": MK_ADMIN})
    assert r.status_code == 200
    return {"w2a_phien": r.cookies.get("w2a_phien")}, {"X-CSRF-Token": r.json()["csrf"]}


def _chay(client, tid, **kw):
    return client.post(f"/api/thiet-bi/{tid}/hanh-dong", json={"hanh_dong": "mo", "xac_nhan": True}, **kw)


def test_phien_chua_xac_nhan_lai_khong_chay_duoc_thuc_the_nguy_hiem(moi_truong):
    ck, h = _phien_admin(moi_truong)
    r = _chay(moi_truong, "tbsu.cua", cookies=ck, headers=h)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "step_up_required"


def test_bearer_quan_tri_duoc_mien_nhu_moi_noi_khac(moi_truong):
    assert _chay(moi_truong, "tbsu.cua", headers=QT).status_code == 200


def test_phien_da_xac_nhan_lai_chay_duoc(moi_truong):
    ck, h = _phien_admin(moi_truong)
    assert moi_truong.post("/api/toi/xac-nhan-lai", json={"mat_khau": MK_ADMIN}, cookies=ck, headers=h).status_code == 200
    assert _chay(moi_truong, "tbsu.cua", cookies=ck, headers=h).status_code == 200


def test_thuc_the_ghi_khong_bi_doi_step_up(moi_truong):
    ck, h = _phien_admin(moi_truong)
    r = moi_truong.post("/api/thiet-bi/tbsu.den/hanh-dong", json={"hanh_dong": "bat"}, cookies=ck, headers=h)
    assert r.status_code == 200, r.text


def test_khach_van_vao_hang_cho_khong_bi_403_step_up(moi_truong):
    k = moi_truong.post("/api/khoa-khach", headers=QT, json={"nhan": "khach", "pham_vi": ["thiet_bi:ghi"]})
    assert k.status_code == 200
    r = _chay(moi_truong, "tbsu.cua", headers={"Authorization": "Bearer " + k.json()["khoa"]["khoa"]})
    assert r.status_code == 202, r.text


def test_giao_dien_co_duong_xac_nhan_lai_khong_dung_innerhtml():
    """Lõi đã đòi step-up cho phiên web ⇒ khung giao diện phải có cách xác nhận lại, nếu không thao tác nguy hiểm bị khoá chết."""
    from pathlib import Path
    ma = (Path(__file__).resolve().parents[1] / "web" / "khung.js").read_text(encoding="utf-8")
    assert "step_up_required" in ma and "api/toi/xac-nhan-lai" in ma
    ham = ma[ma.index("function hoiMatKhauXacNhanLai"):ma.index("async function goi(")]
    assert "innerHTML" not in ham and "type = 'password'" in ham
