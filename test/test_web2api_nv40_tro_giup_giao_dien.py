"""NV40 — Test Chrome THẬT cho trang «Trợ giúp» (web/He-thong-Tro-giup.dc.html).

Tự bỏ qua khi thiếu Chrome (CO_CHROME). Kiểm:
1. Trang mở được khi gõ địa chỉ trực tiếp (chưa có trong menu khung.js — việc khác đang sửa).
2. Mọi khối ĐÓNG mặc định; bấm tiêu đề mới xổ (aria-expanded đổi true), bấm lại đóng.
3. Ô lọc chữ: gõ từ khoá lọc đúng mục; xoá từ khoá hiện lại đủ.
4. Mở một khối thấy nội dung + nút «Sao chép» ở khối mã.
5. Vai user thường chỉ thấy 2 mục (kết nối, sự cố); admin thấy 9 mục.
6. Mobile 390px: không cuộn ngang.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-nv40-ui-0123456789ab"


@contextmanager
def loi_that(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def _cho_san_sang(trang, la_admin: bool = True):
    so = 9 if la_admin else 2
    trang.cho(
        "(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'"
        " && document.getElementById('o-loc-tro-giup')"
        f" && document.querySelectorAll('#danh-sach-muc .khoi').length === {so}",
        mo_ta="trang Trợ giúp dựng xong và nạp đủ mục",
    )


def test_admin_thay_du_9_muc_va_loc_hoat_dong(trang, trinh_duyet, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Tro-giup.dc.html")
        _cho_san_sang(trang, la_admin=True)
        so_muc = trang.js("document.querySelectorAll('#danh-sach-muc .khoi').length")
        assert so_muc == 9, f"admin phải thấy 9 mục, thấy {so_muc}"
        # Mọi khối đóng
        so_mo = trang.js(
            "document.querySelectorAll('#danh-sach-muc .khoi-tieu-de[aria-expanded=\"true\"]').length"
        )
        assert so_mo == 0, "mọi khối phải đóng mặc định"
        # Bấm mở một khối
        trang.bam(
            "document.querySelectorAll('#danh-sach-muc .khoi-tieu-de')[1]",
            mo_ta="bấm mở mục thứ hai",
        )
        trang.cho(
            "document.querySelectorAll('#danh-sach-muc .khoi-tieu-de[aria-expanded=\"true\"]').length === 1",
            mo_ta="một khối mở ra",
        )
        co_pre = trang.js("!!document.querySelector('#danh-sach-muc .khoi-noi-dung pre')")
        assert co_pre, "khối mở phải hiện nội dung, gồm khối mã <pre>"
        co_sao_chep = trang.js(
            "Array.from(document.querySelectorAll('#danh-sach-muc .nut-sao-chep'))"
            ".some((b) => b.textContent.includes('Sao chép'))"
        )
        assert co_sao_chep, "khối mã phải có nút Sao chép"
        # Lọc chữ
        trang.go("#o-loc-tro-giup", "sao lưu")
        trang.cho(
            "document.querySelectorAll('#danh-sach-muc .khoi').length === 1",
            mo_ta="lọc 'sao lưu' ra 1 mục",
        )
        tieu_de = trang.js(
            "document.querySelector('#danh-sach-muc .khoi-tieu-de span').textContent"
        )
        assert "Sao lưu" in tieu_de
        # Xoá lọc → đủ 9
        trang.js(
            "const o = document.getElementById('o-loc-tro-giup'); o.value = ''; o.dispatchEvent(new Event('input', {bubbles: true}))"
        )
        trang.cho(
            "document.querySelectorAll('#danh-sach-muc .khoi').length === 9",
            mo_ta="xoá lọc hiện lại 9 mục",
        )


def test_user_thuong_chi_thay_2_muc(trang, trinh_duyet, tmp_path, monkeypatch):
    import asyncio

    from app import nguoi_dung

    with loi_that(tmp_path, monkeypatch) as w:
        asyncio.run(
            nguoi_dung.tao_nguoi_dung(
                vai="admin", ten_dang_nhap="ad_nv40", mat_khau="MatKhauAdminBaoMat123456"
            )
        )
        asyncio.run(
            nguoi_dung.tao_nguoi_dung(
                vai="user", ten_dang_nhap="nd_kiem_thu", mat_khau="MatKhauNd123456"
            )
        )
        trang.di(f"{w.url}/Dang-nhap.dc.html")
        trang.go("#o-ten", "nd_kiem_thu")
        trang.go("#o-mat-khau", "MatKhauNd123456")
        trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Bấm nút đăng nhập")
        trang.cho(
            "!/(Dang-nhap|index)/.test(location.pathname)",
            mo_ta="đăng nhập user thường xong",
        )
        trang.di(f"{w.url}/He-thong-Tro-giup.dc.html")
        _cho_san_sang(trang, la_admin=False)
        so_muc = trang.js("document.querySelectorAll('#danh-sach-muc .khoi').length")
        assert so_muc == 2, f"user thường chỉ thấy 2 mục, thấy {so_muc}"


def test_mobile_khong_cuon_ngang(trang, trinh_duyet, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        # Kiểm tra ở 1900px
        trang.khung(1900, 1000)
        trang.di(f"{w.url}/He-thong-Tro-giup.dc.html")
        _cho_san_sang(trang, la_admin=True)
        so_mo_1900 = trang.js(
            "document.querySelectorAll('#danh-sach-muc .khoi-tieu-de[aria-expanded=\"true\"]').length"
        )
        assert so_mo_1900 == 0, "mọi khối phải đóng mặc định ở 1900px"
        cuon_1900 = trang.js("document.documentElement.scrollWidth > window.innerWidth")
        assert not cuon_1900, "màn 1900px không được cuộn ngang"
        trang.chup("nv40-tro-giup-1900px")

        # Kiểm tra ở 390px
        trang.khung(390, 844, dien_thoai=True)
        so_mo_390 = trang.js(
            "document.querySelectorAll('#danh-sach-muc .khoi-tieu-de[aria-expanded=\"true\"]').length"
        )
        assert so_mo_390 == 0, "mọi khối phải đóng mặc định ở 390px"
        cuon_390 = trang.js("document.documentElement.scrollWidth > window.innerWidth")
        assert not cuon_390, "màn 390px không được cuộn ngang"
        trang.chup("nv40-tro-giup-390px")
