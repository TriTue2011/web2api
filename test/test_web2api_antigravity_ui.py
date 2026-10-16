"""Test giao diện cho module antigravity bằng Chrome THẬT (CDP của ui_chung, không phải Playwright).

Khuôn mẫu: test/test_web2api_giao_dien.py (ô bàn cờ `[data-o=…]`, bảng chi tiết `#chi-tiet`, form `#lien-ket`).
Module chạy là mã THẬT trong modules/antigravity/; cầu nối là bản giả `CauNoiGia` của test_antigravity_module.py.
Không có Chrome thì cả tệp tự bỏ qua.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from test_antigravity_module import KHOA_CAU_NOI, CauNoiGia
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
    assert trang.js("[...document.querySelectorAll('#cho-ai code')].map((c) => c.textContent)") == manifest["cung_cap"]
    assert "agy" in trang.chu("#can-gi")


def test_cau_hinh_antigravity_qua_giao_dien(loi, trang, tmp_path):
    """Điền địa chỉ + khoá cầu nối trên form của bảng chi tiết → lõi gọi `cai()` thật của module tới cầu nối giả →
    liên kết xong; khoá cầu nối không còn trong DOM hay state của trang, chỉ nằm ở kho bí mật."""
    cn = CauNoiGia()
    with MayChuThat(cn.app) as cau_noi:
        dang_nhap_quan_tri(trang, loi.url)
        trang.di(f"{loi.url}/He-thong.dc.html#module-antigravity")
        o_dia_chi = "#chi-tiet [data-truong='dia_chi_cau_noi'] input[data-kieu='chuoi']"
        o_khoa = "#chi-tiet [data-truong='khoa_cau_noi'] input[type='password']"
        trang.cho(f"!!document.querySelector({json.dumps(o_dia_chi)}) && !!document.querySelector({json.dumps(o_khoa)})",
                  han=15, mo_ta="form có hai trường của manifest")
        trang.js(f"const i = document.querySelector({json.dumps(o_dia_chi)});"
                 "i.focus(); i.value = ''; i.dispatchEvent(new Event('input', {bubbles: true}))")
        trang.go(o_dia_chi, cau_noi.url)
        trang.go(o_khoa, KHOA_CAU_NOI)
        trang.bam("document.getElementById('nut-luu-cau-hinh')")
        trang.cho("document.getElementById('kq-ok')", han=15, mo_ta="liên kết thành công")
        trang.cho(o("antigravity") + "?.dataset.tt === 'dang-chay'", han=15, mo_ta="ô «Đang chạy»")
        assert cn.da_goi_models  # cai() thật đã gọi /v1/models của cầu nối bằng khoá vừa điền

        assert json.loads((tmp_path / "bi_mat" / "antigravity.json").read_text())["khoa_cau_noi"] == KHOA_CAU_NOI
        html = trang.js("document.documentElement.outerHTML")
        assert KHOA_CAU_NOI not in html
        assert not any(KHOA_CAU_NOI in s for s in trang.state_thanh_phan())
