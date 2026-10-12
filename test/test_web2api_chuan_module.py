"""Kiểm thử cơ chế kiểm tra chuẩn module lúc nạp (HOP_DONG_KHA_NANG và kiem_chuan_module).

Nguyên tắc:
- Lõi đặt chuẩn, module đổi theo.
- Khả năng đã biết trong HOP_DONG_KHA_NANG phải có đủ phương thức bắt buộc.
- Thiếu phương thức: module vẫn nạp được vào catalog (không sập tiến trình lõi), nhưng bị đánh dấu
  dung_chuan=False, chuẩn bị cảnh báo vào so.loi / nhat_ky, và lõi không mở đường cho khả năng lỗi.
- Khả năng lạ chưa biết: mã mở, không bị chặn hay coi là lỗi chuẩn.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kham_pha, so_module
from app.kham_pha import HOP_DONG_KHA_NANG, ModuleKhamPha, kiem_chuan_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-chuan-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


class LopThieuThucHien:
    """Lớp khai thiet_bi nhưng thiếu phương thức thuc_hien."""
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def chat(self, b, s=False): return 501, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
    async def thuc_the(self, loc=None): return []
    async def trang_thai(self, ids): return {}
    # Không có thuc_hien


class LopKhaNangLa:
    """Lớp khai khả năng lạ chưa có trong chuẩn lõi."""
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def chat(self, b, s=False): return 501, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass


class LopVideoThieuPhuongThuc:
    """Lớp khai video nhưng thiếu video_trang_thai và video_noi_dung."""
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def chat(self, b, s=False): return 501, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
    async def video_tao(self, body): return {}
    # Thiếu video_trang_thai và video_noi_dung


class LopDungChuanToanDien:
    """Lớp có đầy đủ phương thức cho cả chat và thiet_bi."""
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def chat(self, b, s=False): return 200, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
    async def thuc_the(self, loc=None): return []
    async def trang_thai(self, ids): return {}
    async def thuc_hien(self, id, hd, ts): return {"ok": True}


def test_hop_dong_kha_nang_co_chuan_thiet_bi():
    """Bảng HOP_DONG_KHA_NANG chứa đầy đủ các khả năng cốt lõi bao gồm thiet_bi."""
    assert "thiet_bi" in HOP_DONG_KHA_NANG
    assert HOP_DONG_KHA_NANG["thiet_bi"] == ("thuc_the", "trang_thai", "thuc_hien")
    assert "chat" in HOP_DONG_KHA_NANG
    assert "video" in HOP_DONG_KHA_NANG


def test_kiem_chuan_thieu_thuc_hien():
    """Module khai thiet_bi thiếu thuc_hien bị phát hiện lỗi chuẩn."""
    loi = kiem_chuan_module(["thiet_bi"], LopThieuThucHien)
    assert "thiet_bi" in loi
    assert "thuc_hien" in loi["thiet_bi"]

    km = ModuleKhamPha(
        id="mod-loi",
        nguon="du_lieu",
        thu_muc=Path("/tmp/mod-loi"),
        manifest={"id": "mod-loi", "tien_to": "ml", "cung_cap": ["thiet_bi"]},
        lop=LopThieuThucHien,
        van_tay="vt1",
        ten_ma="ma1",
    )
    assert km.dung_chuan is False
    assert "thiet_bi" not in km.kha_nang_chuan
    assert km.chuan_loi == {"thiet_bi": ["thuc_hien"]}


def test_kiem_chuan_kha_nang_la_ma_mo():
    """Khả năng lạ không có trong HOP_DONG_KHA_NANG được coi là hợp lệ (mã mở)."""
    loi = kiem_chuan_module(["kha_nang_la_chua_biet"], LopKhaNangLa)
    assert loi == {}

    km = ModuleKhamPha(
        id="mod-la",
        nguon="du_lieu",
        thu_muc=Path("/tmp/mod-la"),
        manifest={"id": "mod-la", "tien_to": "mla", "cung_cap": ["kha_nang_la_chua_biet"]},
        lop=LopKhaNangLa,
        van_tay="vt2",
        ten_ma="ma2",
    )
    assert km.dung_chuan is True
    assert "kha_nang_la_chua_biet" in km.kha_nang_chuan


def test_kiem_chuan_video_thieu_phuong_thuc():
    """Module video thiếu các phương thức video_trang_thai, video_noi_dung bị phát hiện."""
    loi = kiem_chuan_module(["video"], LopVideoThieuPhuongThuc)
    assert "video" in loi
    thieu = loi["video"]
    assert "video_trang_thai" in thieu
    assert "video_noi_dung" in thieu


def test_kiem_chuan_dung_chuan_toan_dien():
    """Module khai báo và cài đặt đủ chuẩn thì dung_chuan là True."""
    loi = kiem_chuan_module(["chat", "thiet_bi"], LopDungChuanToanDien)
    assert loi == {}

    km = ModuleKhamPha(
        id="mod-tot",
        nguon="du_lieu",
        thu_muc=Path("/tmp/mod-tot"),
        manifest={"id": "mod-tot", "tien_to": "mt", "cung_cap": ["chat", "thiet_bi"]},
        lop=LopDungChuanToanDien,
        van_tay="vt3",
        ten_ma="ma3",
    )
    assert km.dung_chuan is True
    assert set(km.kha_nang_chuan) == {"chat", "thiet_bi"}


def test_nap_module_chuan_loi_vao_so(tmp_path, monkeypatch):
    """Module không đúng chuẩn vẫn nạp được vào catalog nhưng cảnh báo và không mở tuyến thiet_bi."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)

    # Viết module thiếu thuc_hien lên đĩa
    mod_path = data_dir / "modules" / "mod-lech-chuan"
    manifest = {
        "id": "mod-lech-chuan",
        "ten": "Module lệch chuẩn",
        "mo_ta": "Thiếu thuc_hien",
        "loai": "ung_dung",
        "giao_thuc": "cuc_bo",
        "tien_to": "mlc",
        "cung_cap": ["thiet_bi"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
    def nap(self, c): pass
    async def cai(self, c): return {"ok": True}
    async def chat(self, b, s=False): return 501, {}, b"{}"
    async def models(self): return []
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
    async def thuc_the(self, loc=None):
        return [{"id": "mlc.den", "ten": "Đèn", "loai": "den", "tinh_nang": ["bat"], "rui_ro_mac_dinh": "ghi"}]
    async def trang_thai(self, ids):
        return {"mlc.den": {"gia_tri": "on", "hop_le": True}}
    # Cố tình thiếu thuc_hien
'''
    ghi_thu_muc(mod_path, {
        "manifest.json": json.dumps(manifest).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    })

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so

        # Nạp lại: module vào được catalog, không làm đổ vỡ tiến trình
        r_nap = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        assert "mod-lech-chuan" in r_nap.json()["them"]

        # Module có trong catalog nhưng bị đánh dấu chuan_loi
        km = so.catalog.get("mod-lech-chuan")
        assert km is not None
        assert km.dung_chuan is False
        assert "thiet_bi" in km.chuan_loi

        # Cài đặt module
        r_cai = client.post("/api/modules/mod-lech-chuan/cai", headers=QT, json={"cau_hinh": {}})
        assert r_cai.status_code == 200

        # Vì thiet_bi bị lỗi chuẩn, tuyến /api/thiet-bi tự động bỏ qua module này
        r_tb = client.get("/api/thiet-bi", headers=QT)
        assert r_tb.status_code == 200
        assert not any(e["id"].startswith("mlc.") for e in r_tb.json()["thuc_the"])
