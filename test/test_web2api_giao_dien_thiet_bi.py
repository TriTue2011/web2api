"""Kiểm thử giao diện Thiết bị / Ứng dụng (web/He-thong-Thiet-bi.dc.html) bằng Chrome THẬT.

Tuân thủ khuôn mẫu test/test_web2api_giao_dien.py và test/ui_chung.py:
- Chạy với lõi thật (MayChuThat) + dữ liệu tạm.
- Kiểm tra điều kiện hiện tab 'thiet_bi' trong nav hệ thống.
- Kiểm tra các phần tử DOM: ô tìm kiếm, chip lọc, danh sách thiết bị phân nhóm Khu -> Loại,
  khối hàng chờ phê duyệt và nút hành động.
- Kiểm tra độ tương thích di động: không tràn ngang ở 390 px.

LƯU Ý: Hộp cát không có Chrome nên các test này sẽ tự động bỏ qua (skipped) nhờ CO_CHROME.
Người điều phối sẽ chạy test này trên môi trường có Chrome thật.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")


KHOA_QT = "khoa-quan-tri-test-ui-thiet-bi-0123"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


def mau_module_thiet_bi_ui(id_="thiet-bi-ui", tien_to="tbui") -> dict[str, bytes]:
    manifest = {
        "id": id_,
        "ten": "Thiết bị UI Mẫu",
        "mo_ta": "Module thiết bị cho test giao diện",
        "loai": "ung_dung",
        "giao_thuc": "cuc_bo",
        "tien_to": tien_to,
        "cung_cap": ["thiet_bi"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma = f'''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
    def nap(self, c): pass
    async def cai(self, c): return {{"ok": True}}
    async def chat(self, b, s=False): return 501, {{}}, b"{{}}"
    async def models(self): return []
    async def suc_khoe(self): return {{"ok": True}}
    async def dung(self): pass
    async def thuc_the(self, loc=None):
        return [
            {{
                "id": f"{{self.tien_to}}.den_bep",
                "ten": "Đèn bếp",
                "loai": "den",
                "khu": "Bếp",
                "thuoc_tinh": {{"do_sang": 100}},
                "tinh_nang": ["bat", "tat"],
                "rui_ro_mac_dinh": "ghi",
            }},
            {{
                "id": f"{{self.tien_to}}.cua_cuon",
                "ten": "Cửa cuốn gara",
                "loai": "cua",
                "khu": "Gara",
                "thuoc_tinh": {{"trang_thai": "dong"}},
                "tinh_nang": ["mo_cua", "dong_cua"],
                "rui_ro_mac_dinh": "nguy_hiem",
            }},
        ]
    async def trang_thai(self, ids):
        return {{
            f"{{self.tien_to}}.den_bep": {{"gia_tri": "on", "hop_le": True}},
            f"{{self.tien_to}}.cua_cuon": {{"gia_tri": "closed", "hop_le": True}},
        }}
    async def thuc_hien(self, id, hanh_dong, tham_so):
        return {{"ok": True, "id": id, "hanh_dong": hanh_dong}}
'''
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@contextmanager
def loi_that_ui(tmp_path, monkeypatch, modules: dict | None = None):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    for id_, tep in (modules or {}).items():
        ghi_thu_muc(data_dir / "modules" / id_, tep)

    with MayChuThat(app, lifespan="on") as w:
        yield w


def nav(trang) -> list[str]:
    return trang.js("[...document.querySelectorAll('[data-nav]')].map((a) => a.dataset.nav)")


def test_ui_thiet_bi_chua_cai_module(trang, tmp_path, monkeypatch):
    """Khi chưa có module thiết bị: tab thiet_bi không hiện trong thanh điều hướng."""
    with loi_that_ui(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        # Tab thiet_bi chưa có trong DOM
        assert "thiet_bi" not in nav(trang)


def test_ui_thiet_bi_khi_da_cai_module(trang, tmp_path, monkeypatch):
    """Khi đã cài module thiết bị: tab thiet_bi hiện và trang hiển thị danh sách thực thể."""
    mods = {"thiet-bi-ui": mau_module_thiet_bi_ui("thiet-bi-ui", "tbui")}
    with loi_that_ui(tmp_path, monkeypatch, modules=mods) as w:
        # Cài module
        r_cai = httpx.post(f"{w.url}/api/modules/thiet-bi-ui/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200

        dang_nhap_quan_tri(trang, w.url)

        # Tab thiet_bi xuất hiện trong nav
        assert "thiet_bi" in nav(trang)

        # Mở trang thiết bị
        trang.di(f"{w.url}/He-thong-Thiet-bi.dc.html")
        trang.cho("document.querySelector('.khu-nhom')")

        # Kiểm tra tiêu đề khu
        khu_text = trang.js("[...document.querySelectorAll('.khu-tieu-de')].map((e) => e.textContent)")
        assert any("Bếp" in k for k in khu_text)
        assert any("Gara" in k for k in khu_text)

        # Kiểm tra thực thể
        tb_ten = trang.js("[...document.querySelectorAll('.tb-ten')].map((e) => e.textContent)")
        assert "Đèn bếp" in tb_ten
        assert "Cửa cuốn gara" in tb_ten

        # Kiểm tra không tràn ngang ở 390 px (mobile)
        trang.cdp.goi("Emulation.setDeviceMetricsOverride", {
            "width": 390,
            "height": 844,
            "deviceScaleFactor": 3,
            "mobile": True,
        }, trang.s)
        time.sleep(0.1)
        khong_tran = trang.js("document.documentElement.scrollWidth <= window.innerWidth")
        assert khong_tran is True
