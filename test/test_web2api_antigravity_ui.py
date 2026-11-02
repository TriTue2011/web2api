"""Test giao diện cho module antigravity bằng Chrome THẬT (CDP của ui_chung, không phải Playwright).

Khuôn mẫu: test/test_web2api_giao_dien.py (ô bàn cờ `[data-o=…]`, bảng chi tiết `#chi-tiet`, form `#lien-ket`).
Module chạy là mã THẬT trong modules/antigravity/.
Không có Chrome thì cả tệp tự bỏ qua.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

GOC = Path(__file__).resolve().parents[1]
THU_MUC_ANTIGRAVITY = GOC / "modules" / "antigravity"
KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"


def tep_antigravity() -> dict[str, bytes]:
    return {
        str(p.relative_to(THU_MUC_ANTIGRAVITY)): p.read_bytes()
        for p in THU_MUC_ANTIGRAVITY.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def o(id_: str) -> str:
    return f"document.querySelector('[data-o={json.dumps(id_)}]')"


@pytest.fixture
def loi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    ghi_thu_muc(tmp_path / "modules" / "antigravity", tep_antigravity())
    with MayChuThat(app, lifespan="on") as w:
        yield w


def test_o_module_antigravity_hien_tren_trang_he_thong(loi, trang):
    """Đã cài, chưa liên kết: ô `antigravity` có trên lưới, trạng thái «chờ khoá»; `#module-antigravity` mở bảng chi
    tiết của CHÍNH module đó với tên, khả năng và phần «Cần gì để chạy» lấy từ manifest."""
    manifest = json.loads((THU_MUC_ANTIGRAVITY / "manifest.json").read_text(encoding="utf-8"))
    dang_nhap_quan_tri(trang, loi.url)
    trang.di(f"{loi.url}/He-thong.dc.html#module-antigravity")
    trang.cho(o("antigravity"), han=15, mo_ta="ô module antigravity")
    assert trang.js(o("antigravity") + ".dataset.tt") == "cho-khoa"
    trang.cho("document.getElementById('chi-tiet-ten')?.innerText === " + json.dumps(manifest["ten"]), han=10,
              mo_ta="bảng chi tiết của antigravity")
    trang.bam("document.getElementById('tab-tong-quan')")   # NV296: chờ khoá thì mở sẵn tab «Kết nối»; khả năng nằm ở «Tổng quan»
    trang.cho("document.getElementById('cho-ai')", han=10, mo_ta="tab Tổng quan")
    assert trang.js("[...document.querySelectorAll('#cho-ai code')].map((c) => c.textContent)") == manifest["cung_cap"]
    # NV280: module ĐÃ cài → khối «Đang dùng» (không có «Cần có»); mô tả MỘT câu ≤ 110 ký tự; có mã (id) + «Cần cài trước».
    assert trang.js("document.getElementById('can-gi')") is None and trang.js("!!document.getElementById('dang-dung')")
    assert "agy" in trang.chu("#mo-ta") and len(trang.chu("#mo-ta")) <= 110
    assert trang.chu("#ct-trang-thai") == "Đã cài" and trang.chu("#ct-id") == "antigravity"
    assert "cần cài trước" in trang.chu("#phu-thuoc").lower()


def test_khoa_antigravity_nhap_ngay_trong_tab_ket_noi(loi, trang):
    """NV296: module có trường bí mật BẮT BUỘC → bảng Module có tab «Kết nối» mở sẵn, ô nhập nằm ngay trong bảng; không
    còn nút «Thêm khoá», không liên kết sang trang Bảo mật & người dùng."""
    dang_nhap_quan_tri(trang, loi.url)
    trang.di(f"{loi.url}/He-thong.dc.html#module-antigravity")
    trang.cho("document.getElementById('lien-ket')", han=15, mo_ta="tab Kết nối")
    assert trang.chu("#tab-ket-noi") == "Kết nối"
    assert trang.js("document.getElementById('tab-ket-noi').getAttribute('aria-selected')") == "true"
    assert trang.js("document.querySelectorAll('#lien-ket input, #lien-ket textarea').length") > 0
    assert trang.js("document.getElementById('lk-khoa-bao-mat')") is None
    assert trang.js("document.getElementById('nut-them-khoa')") is None
