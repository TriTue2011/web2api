"""NV10 — ép kiểu bool lỏng trên dữ liệu khách rồi coi là xác nhận.

Lớp lỗi: `bool(than.get("x"))` biến chuỗi "false"/"0" thành True.
Sửa: hàm dùng chung chỉ nhận đúng bool hoặc vắng mặt/null; kiểu khác → 400.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-nv10-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

GIA_TRI_SAI = ["false", "0", "no", 1, 1.5, [], {}, ""]
# null (None) được coi như vắng mặt → giá trị mặc định (đúng hành vi cũ), không 400.


def mau_module_thiet_bi(id_="thiet-bi-gia", tien_to="tbgia") -> dict[str, bytes]:
    manifest = {
        "id": id_, "ten": "Thiết bị giả", "mo_ta": "giả", "loai": "ung_dung", "hang": "A",
        "giao_thuc": "cuc_bo", "tien_to": tien_to, "cung_cap": ["thiet_bi"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False,
                       "mang_ra": [], "ghi_chu": ""},
    }
    ma = '''
import json

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self.da_chay = []
    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh
    async def cai(self, cau_hinh):
        return {"ok": True}
    async def thuc_the(self, loc=None):
        return [{
            "id": f"{self.tien_to}.khoa-cua", "ten": "Khoá cửa", "loai": "khoa",
            "khu": "cong", "thuoc_tinh": {"dong": True},
            "tinh_nang": ["mo_khoa", "khoa"], "rui_ro_mac_dinh": "nguy_hiem",
        }]
    async def trang_thai(self, ids):
        return {}
    async def thuc_hien(self, id, hanh_dong, tham_so):
        self.da_chay.append({"id": id, "hanh_dong": hanh_dong})
        return {"ok": True}
    async def chat(self, body, stream=False):
        return 501, {}, b"{}"
    async def models(self):
        return []
    async def suc_khoe(self):
        return {"ok": True}
    async def dung(self):
        pass
'''
    return {"manifest.json": json.dumps(manifest, ensure_ascii=False).encode(),
            "module.py": ma.encode()}


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    with TestClient(app) as c:
        so = SoModule()
        c.app.state.so = so
        ghi_thu_muc(data_dir / "modules" / "thiet-bi-gia",
                    mau_module_thiet_bi())
        c.post("/api/modules/nap-lai", headers=QT)
        c.post("/api/modules/thiet-bi-gia/cai", headers=QT, json={"cau_hinh": {}})
        yield c, so


def _module(so):
    return so.dang_chay["thiet-bi-gia"]


# ── xac_nhan: hành động nguy hiểm ──

@pytest.mark.parametrize("gia_tri", GIA_TRI_SAI)
def test_xac_nhan_sai_kieu_400_va_khong_chay(client, gia_tri):
    c, so = client
    mod = _module(so)
    mod.da_chay.clear()
    r = c.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=QT,
               json={"hanh_dong": "mo_khoa", "xac_nhan": gia_tri})
    assert r.status_code == 400, f"xac_nhan={gia_tri!r}"
    assert r.json()["error"]["code"] == "invalid_request"
    assert mod.da_chay == [], "lệnh nguy hiểm KHÔNG được gửi đi"


def test_xac_nhan_true_van_chay(client):
    c, so = client
    mod = _module(so)
    mod.da_chay.clear()
    r = c.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=QT,
               json={"hanh_dong": "mo_khoa", "xac_nhan": True})
    assert r.status_code == 200
    assert len(mod.da_chay) == 1


def test_xac_nhan_vang_mat_409_nhu_cu(client):
    c, so = client
    r = c.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=QT,
               json={"hanh_dong": "mo_khoa"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "can_xac_nhan"


def test_xac_nhan_null_nhu_vang_mat(client):
    c, so = client
    r = c.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=QT,
               json={"hanh_dong": "mo_khoa", "xac_nhan": None})
    assert r.status_code == 409  # null = vắng mặt = chưa xác nhận


# ── stream ──

@pytest.mark.parametrize("gia_tri", GIA_TRI_SAI)
def test_stream_sai_kieu_400(client, gia_tri):
    c, _ = client
    r = c.post("/v1/chat/completions", headers=QT,
               json={"model": "bat-ky/model", "messages": [], "stream": gia_tri})
    assert r.status_code == 400, f"stream={gia_tri!r}"


def test_stream_true_false_hop_le(client):
    c, _ = client
    # True/False là bool hợp lệ → không 400 (module giả trả 501, không quan trọng)
    for gt in (True, False):
        r = c.post("/v1/chat/completions", headers=QT,
                   json={"model": "bat-ky/model", "messages": [], "stream": gt})
        assert r.status_code != 400, f"stream={gt!r}"


def test_stream_vang_mat_khong_400(client):
    c, _ = client
    r = c.post("/v1/chat/completions", headers=QT,
               json={"model": "bat-ky/model", "messages": []})
    assert r.status_code != 400


# ── bat (khoá khách) ──

def test_bat_sai_kieu_400(client):
    c, _ = client
    r_tao = c.post("/api/khoa-khach", headers=QT,
                   json={"nhan": "test", "pham_vi": ["chat"]})
    kid = r_tao.json()["khoa"]["id"]
    r = c.patch(f"/api/khoa-khach/{kid}", headers=QT, json={"bat": "false"})
    assert r.status_code == 400


def test_bat_true_false_hop_le(client):
    c, _ = client
    r_tao = c.post("/api/khoa-khach", headers=QT,
                   json={"nhan": "test", "pham_vi": ["chat"]})
    kid = r_tao.json()["khoa"]["id"]
    assert c.patch(f"/api/khoa-khach/{kid}", headers=QT,
                   json={"bat": False}).status_code == 200
    assert c.patch(f"/api/khoa-khach/{kid}", headers=QT,
                   json={"bat": True}).status_code == 200


# ── Việc 2: đọc kho lỗi → 503, không ghi đè ──

def test_kho_hong_khong_ghi_de_hang_cho(client):
    """Tệp phe_duyet.json hỏng: thêm mục mới → 503, tệp giữ nguyên (không bị ghi đè rỗng)."""
    from app import kho as kho_mod
    c, so = client
    # Khoá khách có quyền thiet_bi:ghi → hành động nguy hiểm đi vào hàng chờ
    r_tao = c.post("/api/khoa-khach", headers=QT,
                   json={"nhan": "khach", "pham_vi": ["thiet_bi:ghi"]})
    hd = {"Authorization": f"Bearer {r_tao.json()['khoa']['khoa']}"}
    p = kho_mod.thu_muc_du_lieu() / "phe_duyet.json"
    p.write_text("{json hong", encoding="utf-8")
    r = c.post("/api/thiet-bi/tbgia.khoa-cua/hanh-dong", headers=hd,
               json={"hanh_dong": "mo_khoa"})
    # Vào hàng chờ → đọc kho hỏng → 503, không ghi đè
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "store_unreadable"
    assert p.read_text(encoding="utf-8") == "{json hong"


def test_kho_hong_khong_ghi_de_rui_ro(client):
    """Tệp thiet_bi.json hỏng: lưu ghi đè rủi ro → 503, tệp giữ nguyên."""
    from app import kho as kho_mod
    c, so = client
    p = kho_mod.thu_muc_du_lieu() / "thiet_bi.json"
    p.write_text("[mang hong]", encoding="utf-8")
    r = c.put("/api/thiet-bi/tbgia.den/rui-ro", headers=QT, json={"rui_ro": "ghi"})
    assert r.status_code == 503
    assert p.read_text(encoding="utf-8") == "[mang hong]"


# ── đơn vị: hàm dùng chung ──

def test_ham_bool_dung_chung():
    from app.tuyen import _lay_bool
    assert _lay_bool({}, "x") is False
    assert _lay_bool({}, "x", True) is True
    assert _lay_bool({"x": True}, "x") is True
    assert _lay_bool({"x": False}, "x") is False
    assert _lay_bool({"x": None}, "x") is False  # null như vắng mặt
    for gv in GIA_TRI_SAI:
        with pytest.raises(Exception) as ei:
            _lay_bool({"x": gv}, "x")
        assert getattr(ei.value, "status_code", None) == 400
