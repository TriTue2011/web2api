"""NV36 — Test Chrome THẬT cho trang «Phiên tài khoản» (web/He-thong-Phien.dc.html).

Tự bỏ qua khi thiếu Chrome (CO_CHROME). Kiểm:
1. Trang mở được khi gõ địa chỉ trực tiếp (chưa có trong menu khung.js — việc khác đang sửa).
2. Khối nhóm module ĐÓNG mặc định; bấm tiêu đề mới xổ (aria-expanded đổi true).
3. Danh sách đúng: tài khoản, trạng thái; nút Kiểm ngay / Xoá phiên hoạt động; danh sách cập nhật.
4. Không có chuỗi bí mật của phiên trong DOM (cookie/token giả).
5. Trạng thái rỗng: giải thích module nào cần đăng nhập mới có phiên, không nút vô nghĩa.
6. Mobile 390px: không cuộn ngang.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from app import phien, so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-nv36-ui-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
BI_MAT_GIA = "phien-bi-mat-gia-nv36-ui-000"


@contextmanager
def loi_that(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def _gieo():
    phien.luu("mod-nv36", "tk-mot", {"token": BI_MAT_GIA, "cookie": "phien=xyz"}, het_han=None)
    phien.luu("mod-nv36", "tk-hai", {"token": BI_MAT_GIA + "-2"}, het_han="2000-01-01T00:00:00+00:00")


def _cho_san_sang(trang):
    trang.cho(
        "(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'"
        " && document.getElementById('tom-tat-phien')",
        mo_ta="trang phiên dựng xong",
    )


def test_ui_phien_nhom_dong_mac_dinh_bam_moi_xo(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _gieo()
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Phien.dc.html")
        _cho_san_sang(trang)

        # Khối đóng mặc định: aria-expanded=false, vùng nội dung chưa có trong DOM
        aria = trang.js("document.querySelector('[data-mo-nhom=\"mod-nv36\"]').getAttribute('aria-expanded')")
        assert aria == "false"
        assert trang.js("document.querySelectorAll('[data-phien]').length") == 0

        # Bấm tiêu đề mới xổ
        trang.bam("document.querySelector('[data-mo-nhom=\"mod-nv36\"]')", mo_ta="mở nhóm mod-nv36")
        trang.cho("document.querySelector('[data-mo-nhom=\"mod-nv36\"]').getAttribute('aria-expanded') === 'true'")
        assert trang.js("document.querySelectorAll('[data-phien]').length") == 2

        # Danh sách đúng: tài khoản + trạng thái
        ten = trang.js("[...document.querySelectorAll('.phien-ten')].map((e) => e.textContent)")
        assert sorted(ten) == ["tk-hai", "tk-mot"]
        tt = trang.js("[...document.querySelectorAll('.huy-trang-thai')].map((e) => e.textContent)")
        assert "Cần đăng nhập lại" in tt  # tk-hai hết hạn

        # Không có chuỗi bí mật trong DOM
        dom = trang.js("document.documentElement.innerHTML")
        assert BI_MAT_GIA not in dom


def test_ui_phien_xoa_hai_buoc(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _gieo()
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Phien.dc.html")
        _cho_san_sang(trang)
        trang.bam("document.querySelector('[data-mo-nhom=\"mod-nv36\"]')", mo_ta="mở nhóm")
        trang.cho("document.querySelectorAll('[data-phien]').length === 2")

        # Bấm «Xoá phiên» lần 1 → hiện xác nhận hai bước, chưa xoá
        trang.bam("document.querySelector('[data-xoa=\"mod-nv36/tk-mot\"]')", mo_ta="bấm xoá lần 1")
        trang.cho("document.querySelector('[data-xoa-xac-nhan=\"mod-nv36/tk-mot\"]')")
        assert trang.js("document.querySelectorAll('[data-phien]').length") == 2

        # Bấm «Xoá» lần 2 → xoá thật, danh sách cập nhật
        trang.bam("document.querySelector('[data-xoa-xac-nhan=\"mod-nv36/tk-mot\"]')", mo_ta="xác nhận xoá")
        trang.cho("document.querySelectorAll('[data-phien]').length === 1")
        ten = trang.js("[...document.querySelectorAll('.phien-ten')].map((e) => e.textContent)")
        assert ten == ["tk-hai"]


def test_ui_phien_rong_giai_thich(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Phien.dc.html")
        _cho_san_sang(trang)
        txt = trang.js("document.getElementById('trang-thai-rong').textContent")
        assert "cần đăng nhập" in txt
        # Không nút vô nghĩa khi rỗng
        assert trang.js("document.querySelectorAll('[data-phien]').length") == 0
        assert trang.js("document.querySelectorAll('[data-kiem]').length") == 0


def test_ui_phien_mobile_khong_cuon_ngang(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _gieo()
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(390, 844, dien_thoai=True)
        trang.di(f"{w.url}/He-thong-Phien.dc.html")
        _cho_san_sang(trang)
        trang.bam("document.querySelector('[data-mo-nhom=\"mod-nv36\"]')", mo_ta="mở nhóm")
        trang.cho("document.querySelectorAll('[data-phien]').length === 2")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth")
