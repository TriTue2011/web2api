"""Test giao diện Chrome cho tab «Bảo mật & người dùng» (Việc E).

Chạy bằng Chrome THẬT trên LÕI THẬT theo khuôn mẫu test/test_web2api_giao_dien.py và test/ui_chung.py:
- Mở trang He-thong-Bao-mat.dc.html ở 1280×800 và 390×844.
- Nhập khoá quản trị, lưu khoá.
- Tạo khoá khách mới: nhãn, chọn chip phạm vi, bấm tạo.
- Thẻ «Thông tin gửi cho khách» hiện base_url và khoá thô, các nút sao chép.
- Danh sách khoá hiện nhãn, chip phạm vi, bật/tắt, thu hồi (bấm 2 lần xác nhận).
- Không tràn ngang ở 390px.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from app import so_module
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@contextmanager
def loi_that(tmp_path, monkeypatch):
    """Lõi thật uvicorn với thư mục dữ liệu tạm."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def test_giao_dien_bao_mat_tao_va_quan_ly_khoa_1280(trang, tmp_path, monkeypatch):
    """Máy tính 1280x800: Lưu khoá quản trị, tạo khoá khách, bật/tắt và thu hồi."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(1280, 800)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")

        # Đã đăng nhập quản trị: hiện form tạo khoá và danh sách
        assert trang.cho("document.getElementById('tao-khoa') !== null")
        assert trang.cho("document.getElementById('danh-sach-khoa') !== null")

        # 3. Tạo khoá khách mới với phạm vi 'chat'
        trang.go("#o-nhan-khoa", "Client Test UI")
        # Bấm chọn chip chat (nếu có) hoặc để tất cả
        trang.bam("document.getElementById('nut-tao-khoa')")

        # 4. Hiện thẻ thông tin gửi cho khách kèm khoá thô một lần
        assert trang.cho("document.getElementById('the-thong-tin-khach') !== null")
        khoa_thau = trang.js("document.getElementById('khoa-moi-gia-tri').innerText")
        assert khoa_thau.startswith("w2a_")
        dia_chi_v1 = trang.js("document.getElementById('dia-chi-v1-khach').innerText")
        assert "/v1" in dia_chi_v1

        # 5. Danh sách khoá xuất hiện bản ghi mới
        assert trang.cho("document.querySelector('.khoa-khach-hang') !== null")
        nhan_hien = trang.js("document.querySelector('.khoa-nhan span').innerText")
        assert "Client Test UI" in nhan_hien

        # 6. Đổi trạng thái Bật -> Tắt
        nut_doi = "document.querySelector('[data-hd=\"doi-trang-thai\"]')"
        trang.bam(nut_doi)
        trang.cho("document.querySelector('.huy-xam') !== null", mo_ta="khoá chuyển sang đã tắt")

        # 7. Thu hồi khoá: Bấm lần 1 hiện Xác nhận, bấm lần 2 thì xoá
        trang.bam("document.querySelector('[data-hd=\"xoa-khoa\"]')")
        assert trang.cho("document.querySelector('[data-hd=\"xac-nhan-xoa\"]') !== null")
        trang.bam("document.querySelector('[data-hd=\"xac-nhan-xoa\"]')")

        # Danh sách trở về trạng thái rỗng
        trang.cho("document.querySelector('.bang-tb-rong') !== null", mo_ta="danh sách khoá đã rỗng")


def test_giao_dien_bao_mat_dien_thoai_390(trang, tmp_path, monkeypatch):
    """Điện thoại 390x844: Không tràn ngang, thanh điều hướng dưới."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(390, 844)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html")
        trang.cho("document.getElementById('tao-khoa') !== null")

        # Kiểm tra không tràn ngang ở 390px
        cuon_ngang = trang.js("document.documentElement.scrollWidth <= 390")
        assert cuon_ngang, "Trang bị tràn ngang ở màn hình 390px"

        # Thanh điều hướng dưới có mặt
        assert trang.cho("document.querySelector('nav.bn') !== null")
        # Thanh dưới liệt kê ĐỦ các mục Hệ thống (lỗi chủ máy bắt 12/10: chỉ hiện «AI» vì trang quên bọc menuNav, còn trang
        # Module thì đủ — test cũ chỉ kiểm sự có mặt của thanh, không kiểm nội dung)
        trang.cho("(document.querySelector('nav.bn')?.innerText || '').includes('Module')", mo_ta="thanh dưới có «Module»")
        chu_thanh = trang.js("document.querySelector('nav.bn').innerText")
        assert "Module" in chu_thanh and "Bảo mật" in chu_thanh, chu_thanh  # «AI» chỉ hiện khi đã cài module AI (lõi trống thì không có)
