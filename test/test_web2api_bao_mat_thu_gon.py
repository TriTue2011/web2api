"""Chrome THẬT: tab «Bảo mật & người dùng» theo quy ước giao diện 17/10 của chủ máy.

1. Mỗi mục LUÔN đóng mỗi lần tải; nút mục có aria-expanded/aria-controls; bấm mới mở; Escape đóng.
2. Mỗi dữ liệu trong danh sách chỉ MỘT dòng (cao vừa một dòng chữ) và có gạch phân cách giữa các dòng, dòng đầu
   không gạch.
3. Điện thoại 390px: mở hết mục vẫn không cuộn ngang.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from app import nguoi_dung, so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-bao-mat-thu-gon-0123456789"
TEN_DAI = "nguoi-dung-ten-rat-dai-" + "x" * 8


@contextmanager
def loi_that(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def _cho_dung_xong(trang, so=4):
    """Tab Người dùng có 4 mục thu gọn (tạo tài khoản, danh sách, tạo khoá khách, danh sách khoá khách);
    tab Bảo mật có 6 (2FA, khoá đăng nhập, sao lưu, tạo khoá của tôi, danh sách khoá của tôi, đổi mật khẩu)."""
    trang.cho(f"document.querySelectorAll('[data-mo-muc]').length === {so}", mo_ta=f"{so} mục thu gọn")


def test_moi_muc_dong_mac_dinh_bam_moi_mo_escape_dong(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        _cho_dung_xong(trang)
        assert trang.js("document.querySelectorAll('[data-mo-muc][aria-expanded=\"true\"]').length") == 0
        assert trang.js("document.querySelectorAll('.muc-gap-nd').length") == 0, "mục đóng không dựng nội dung"
        # nút mục khai aria-controls trỏ tới vùng nội dung
        assert trang.js("[...document.querySelectorAll('[data-mo-muc]')].every(b => !!b.getAttribute('aria-controls'))")

        trang.bam("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]')")
        trang.cho("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]').getAttribute('aria-expanded') === 'true'")
        assert trang.js("!!document.getElementById('nd-danh-sach-nguoi-dung')")
        assert trang.js("document.querySelectorAll('[data-mo-muc][aria-expanded=\"true\"]').length") == 1, "chỉ mục vừa bấm mở"

        # Escape (phím tại nút mục) đóng lại
        trang.js("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]').dispatchEvent("
                 "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        trang.cho("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]').getAttribute('aria-expanded') === 'false'")

        # tải lại: không nhớ trạng thái
        trang.bam("document.querySelector('[data-mo-muc=\"tao-khoa\"]')")
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        _cho_dung_xong(trang)
        assert trang.js("document.querySelectorAll('[data-mo-muc][aria-expanded=\"true\"]').length") == 0


def test_hang_danh_sach_mot_dong_co_gach_phan_cach(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap=TEN_DAI, mat_khau="MatKhauNguoiDung123456"))
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        _cho_dung_xong(trang)
        trang.khung(1280, 900)
        trang.bam("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]')")
        trang.cho("document.querySelectorAll('.nguoi-dung-hang').length >= 2", mo_ta="hai tài khoản trong danh sách")
        hang = trang.js(
            "[...document.querySelectorAll('.nguoi-dung-hang')].map(r => ({cao: Math.round(r.getBoundingClientRect().height),"
            " vien: getComputedStyle(r).borderTopWidth}))")
        assert all(h["cao"] <= 56 for h in hang), f"mỗi hàng phải một dòng (cao ≤ 56px): {hang}"
        assert hang[0]["vien"] == "0px", "dòng đầu không gạch"
        assert all(h["vien"] == "1px" for h in hang[1:]), f"các dòng sau có gạch phân cách: {hang}"
        # nút thao tác cùng hàng với tên (không xuống dòng riêng)
        assert trang.js(
            "(() => { const r = document.querySelector('.nguoi-dung-hang'); const t = r.querySelector('.nguoi-dung-nhan');"
            " const b = r.querySelector('.nguoi-dung-tac-vu button');"
            " return Math.abs(t.getBoundingClientRect().top - b.getBoundingClientRect().top) < 30; })()")


def test_dien_thoai_390px_mo_het_khong_tran_ngang(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="user", ten_dang_nhap=TEN_DAI, mat_khau="MatKhauNguoiDung123456"))
        dang_nhap_quan_tri(trang, w.url)
        trang.khung(390, 844, dien_thoai=True)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        _cho_dung_xong(trang)
        trang.mo_het_muc()
        trang.cho("document.querySelectorAll('.nguoi-dung-hang').length >= 2")
        rong = trang.js("({sw: document.documentElement.scrollWidth, iw: window.innerWidth})")
        assert rong["sw"] <= rong["iw"], f"không được cuộn ngang ở 390px: {rong}"
        # Điện thoại: hàng đóng chỉ MỘT dòng (tên + nhãn); bấm hàng mới hiện chi tiết và nút
        cao = trang.js("[...document.querySelectorAll('.nguoi-dung-hang')].map(r => Math.round(r.getBoundingClientRect().height))")
        assert all(c <= 56 for c in cao), f"hàng đóng phải một dòng ở 390px: {cao}"
        assert trang.js("getComputedStyle(document.querySelector('.nguoi-dung-hang .nguoi-dung-tac-vu')).display") == "none"
        trang.bam("document.querySelector('.nguoi-dung-hang .nguoi-dung-thong-tin')")
        trang.cho("document.querySelector('.nguoi-dung-hang.hang-mo')", mo_ta="hàng mở chi tiết")
        assert trang.js("getComputedStyle(document.querySelector('.nguoi-dung-hang.hang-mo .nguoi-dung-tac-vu')).display") == "flex"
        assert trang.js("document.querySelector('.nguoi-dung-hang .nguoi-dung-thong-tin').getAttribute('aria-expanded')") == "true"
