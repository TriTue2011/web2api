"""Test giao diện cho NV61: Xác nhận cài module mới từ git trên Chrome thật.

Lưu ý: Test này cần Chrome thật (DevTools Protocol qua websockets).
Trong môi trường hộp cát không có Chrome (CO_CHROME=False), test sẽ tự động bỏ qua.
Người điều phối ngoài sandbox sẽ chạy kiểm thử này.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gia import GitHubGia, MayChuThat, tep_kho
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixtures
from ui_mau import module_ai
from app import so_module
from app.main import app

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-giao-dien-nv61-123456"
REPO = "TriTue2011/web2api"


@pytest.fixture
def moi_truong_nv61(tmp_path, monkeypatch):
    """Môi trường lõi thật + GitHub giả chứa 1 module mới chưa cài."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")

    mod_moi = module_ai("mod-moi-nv61", ten="Module Mới NV61")
    gh = GitHubGia(REPO)
    gh.day(tep_kho({"mod-moi-nv61": mod_moi}), thong_diep="thêm module mới")

    with MayChuThat(gh.app) as s_gh:
        monkeypatch.setenv("WEB2API_GITHUB_API", s_gh.url)
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
        monkeypatch.setenv("WEB2API_GIT_REPO", REPO)
        with MayChuThat(app, lifespan="on") as w:
            w.gh = gh
            yield w


def test_ui_nv61_hop_xac_nhan_cai_moi_va_huy(moi_truong_nv61, trang):
    """Bấm Cài module mới -> hiện hộp xác nhận -> bấm Huỷ -> đóng hộp, không cài."""
    w = moi_truong_nv61
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong.dc.html")

    # Đợi ô module xuất hiện trong lưới
    trang.cho("document.querySelector('[data-o=\"mod-moi-nv61\"]')", mo_ta="ô module mod-moi-nv61 xuất hiện")

    # Mở chi tiết
    trang.js("document.querySelector('[data-o=\"mod-moi-nv61\"]').click()")
    trang.cho("document.getElementById('chi-tiet-ten')", mo_ta="bảng chi tiết mở")

    # Nút Cài phải có
    trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài hiển thị")
    assert trang.js("document.getElementById('xac-nhan-cai') === null")

    # Bấm nút Cài -> Lượt 1 nhận 409 can_xac_nhan_cai_moi -> Hiện hộp xác nhận
    trang.js("document.getElementById('nut-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') !== null", mo_ta="hộp xác nhận cài đặt hiện ra")

    # Kiểm tra nội dung hộp xác nhận
    assert trang.js("document.getElementById('xac-nhan-cai').getAttribute('role') === 'alertdialog'")
    assert trang.js("document.getElementById('nut-dong-y-cai') !== null")
    assert trang.js("document.getElementById('nut-huy-cai') !== null")
    assert trang.js("document.getElementById('nut-cai') === null")  # Nút cài chính đã ẩn

    # Bấm Huỷ
    trang.js("document.getElementById('nut-huy-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') === null", mo_ta="hộp xác nhận đã đóng")
    assert trang.js("document.getElementById('nut-cai') !== null")  # Nút cài chính xuất hiện lại


def test_ui_nv61_xac_nhan_dong_y_cai_thanh_cong(moi_truong_nv61, trang):
    """Bấm Cài module mới -> hiện hộp xác nhận -> bấm Đồng ý cài -> cài thành công."""
    w = moi_truong_nv61
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong.dc.html")

    trang.cho("document.querySelector('[data-o=\"mod-moi-nv61\"]')", mo_ta="ô module mod-moi-nv61 xuất hiện")
    trang.js("document.querySelector('[data-o=\"mod-moi-nv61\"]').click()")
    trang.cho("document.getElementById('nut-cai')", mo_ta="nút Cài hiển thị")

    # Bấm Cài để mở hộp xác nhận
    trang.js("document.getElementById('nut-cai').click()")
    trang.cho("document.getElementById('xac-nhan-cai') !== null", mo_ta="hộp xác nhận hiện ra")

    # Bấm Đồng ý cài
    trang.js("document.getElementById('nut-dong-y-cai').click()")

    # Cài xong: module khai chat -> tự điều hướng sang Studio (hành vi có sẵn của «Cài»)
    trang.cho(
        "document.querySelector('x-dc')?.dataset.dc === 'san-sang' && location.pathname.endsWith('/Studio.dc.html') && !!document.getElementById('studio-chua-khoa')",
        han=20,
        mo_ta="tự sang Studio sau khi cài thành công",
    )
    assert trang.js("document.querySelector('#studio-chua-khoa a[data-them-khoa=\"mod-moi-nv61\"]') !== null")

    # Chứng minh cài thành công bằng DỮ LIỆU: gọi /api/modules bằng phiên quản trị
    mods = trang.js("fetch('/api/modules').then(r => r.json()).then(d => d.modules || [])")
    assert any(m.get("id") == "mod-moi-nv61" for m in mods)

    # Quan sát danh sách module ở Hệ thống -> Module: ô mod-moi-nv61 chuyển sang trạng thái đã cài (chờ khoá)
    trang.di(f"{w.url}/He-thong.dc.html")
    trang.cho("document.querySelector('[data-o=\"mod-moi-nv61\"]')", mo_ta="ô module mod-moi-nv61 hiển thị")
    assert trang.js("document.querySelector('[data-o=\"mod-moi-nv61\"]').dataset.tt") == "cho-khoa"
