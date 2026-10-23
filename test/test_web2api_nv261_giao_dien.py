"""NV261 — giao diện đo tài nguyên trên trang Hệ thống → Module (Chrome thật).

Hợp đồng chủ máy: KHÔNG đo khi mở trang, KHÔNG hẹn giờ — chỉ đo khi bấm «Đo ngay»/«Đo lại»;
trạng thái «Chưa đo» → «Đang đo…» → «Đo lúc HH:MM:SS»; một ảnh đo dùng chung; chấm cam chỉ
xuất hiện SAU khi đo (3 module đứng đầu RAM/CPU); bảng Tổng có RAM/CPU/GPU + «Chưa quy cho
module»; bảng chi tiết module có số đo kèm nhãn nguồn.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

KHOA_QT = "khoa-qt-nv261-ui"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MA_MODULE = '''
DEM = {f"khoa-{i}": bytearray(32768) for i in range(256)}  # ~8 MB dict đệm toàn cục


class Module:
    def __init__(self, manifest):
        self.manifest = manifest

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass
'''


def _manifest(id_, ten):
    return {
        "id": id_, "ten": ten, "mo_ta": "Module giả cho test đo tài nguyên NV261",
        "loai": "tien_ich", "loai_ten": "Tiện ích", "loai_thu_tu": 46, "hang": "B",
        "giao_thuc": "cuc_bo", "tien_to": id_.replace("-", "_"), "cung_cap": [],
        "phien_ban": "0.1.0",
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": []},
        "truong_cau_hinh": [], "yeu_cau_goi": [],
    }


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for id_, ten in (("gia-do-261", "Giả đo 261"), ("gia-chua-cai-261", "Giả chưa cài 261")):
        mod = data / "modules" / id_
        mod.mkdir(parents=True)
        (mod / "manifest.json").write_text(json.dumps(_manifest(id_, ten), ensure_ascii=False),
                                           encoding="utf-8")
        (mod / "module.py").write_text(MA_MODULE, encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with MayChuThat(app, lifespan="on") as w:
        # Một module ĐÃ CÀI (đang chạy), một module để nguyên CHƯA CÀI.
        r = httpx.post(f"{w.url}/api/modules/gia-do-261/cai", headers=QT, json={"cau_hinh": {}})
        assert r.status_code == 200, r.text
        yield w


def _mo_trang_module(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong.dc.html")
    trang.cho("!!document.querySelector('[data-o=\"gia-do-261\"]')", han=20,
              mo_ta="lưới module dựng xong")


def _so_luot_do(trang) -> int:
    return trang.js("performance.getEntriesByType('resource')"
                    ".filter(e => e.name.includes('do-tai-nguyen')).length")


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_khong_do_nen_khi_mo_trang(trang, moi_truong):
    _mo_trang_module(trang, moi_truong)
    time.sleep(3.5)  # chờ vài giây KHÔNG bấm gì: không được có yêu cầu đo nào
    assert _so_luot_do(trang) == 0
    khu_do = trang.chu("#tai-nguyen")
    assert khu_do and "Chưa đo" in khu_do, khu_do
    assert trang.js("!!document.querySelector('#nut-do-tai-nguyen')")
    # Chấm cam chỉ có SAU khi đo.
    assert trang.js("document.querySelectorAll('.cham-dung-nhieu').length") == 0


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_bam_do_roi_do_lai_va_chi_tiet_co_nhan_nguon(trang, moi_truong):
    _mo_trang_module(trang, moi_truong)
    trang.bam("document.querySelector('#nut-do-tai-nguyen')", "nút Đo ngay")
    trang.cho("document.querySelector('#tai-nguyen').innerText.includes('Đo lúc')", han=25,
              mo_ta="đo xong hiện «Đo lúc»")
    assert _so_luot_do(trang) == 1
    tong = trang.chu("#bang-tong-tai-nguyen")
    for nhan in ("RAM máy", "RAM lõi web2api", "CPU lõi web2api", "GPU", "Chưa quy cho module"):
        assert nhan in tong, (nhan, tong)
    # Sau khi đo: chấm cam xuất hiện (module giả ~8 MB đứng đầu RAM).
    assert trang.js("document.querySelectorAll('.cham-dung-nhieu').length") >= 1
    trang.chup("nv261-1-tong-sau-do")

    # Chi tiết module ĐÃ CÀI: số đo kèm nhãn nguồn đầy đủ.
    trang.bam("document.querySelector('[data-o=\"gia-do-261\"]')", "ô module đã cài")
    trang.cho("document.querySelector('#chi-tiet').innerText.includes('RAM đo')", han=10,
              mo_ta="chi tiết hiện dòng RAM đo")
    ct = trang.chu("#chi-tiet")
    assert "ước lượng ±15%" in ct, ct
    assert "lấy mẫu" in ct and "CPU đo" in ct, ct
    assert "dùng chung lõi" in ct, ct
    trang.chup("nv261-2-chi-tiet-da-cai")

    # Chi tiết module CHƯA CÀI: vẫn có số đo từ cùng một ảnh (không gọi đo mới).
    trang.bam("document.querySelector('[data-o=\"gia-chua-cai-261\"]')", "ô module chưa cài")
    trang.cho("document.querySelector('#chi-tiet').innerText.includes('RAM đo')", han=10,
              mo_ta="chi tiết module chưa cài hiện RAM đo")
    assert _so_luot_do(trang) == 1
    trang.chup("nv261-3-chi-tiet-chua-cai")

    # «Đo lại»: chụp một ảnh mới (lượt gọi thứ hai). Cổng tần suất tính từ lúc đo xong → chờ 1,5s.
    time.sleep(1.5)
    trang.bam("document.querySelector('#nut-do-lai-tai-nguyen')", "nút Đo lại")
    trang.cho("performance.getEntriesByType('resource')"
              ".filter(e => e.name.includes('do-tai-nguyen')).length >= 2", han=25,
              mo_ta="Đo lại gọi API lần hai")
    trang.cho("document.querySelector('#tai-nguyen').innerText.includes('Đo lúc')", han=25,
              mo_ta="sau Đo lại vẫn hiện «Đo lúc»")
