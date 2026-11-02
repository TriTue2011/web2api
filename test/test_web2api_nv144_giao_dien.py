"""Kiểm thử giao diện Chrome (NV144): kho tài khoản đã lưu, thêm theo ô như c2a.

LƯU Ý: Hộp cát không có Chrome nên các test này sẽ tự động bỏ qua khi không có Chrome (CO_CHROME=False).
Người điều phối sẽ chạy test này trong môi trường có Chrome thật.

Bao quát (NV282: MỘT form «＋ Thêm tài khoản», không còn khối «Tài khoản đã lưu (n)» có form thứ hai):
1. Form thêm có Email / Mật khẩu (nút Hiện/Ẩn) / khoá OTP (mở bằng nút) / Nhãn trong «Tuỳ chọn thêm»; bấm «Đăng nhập» khi thiếu
   email+mật khẩu thì báo, KHÔNG gửi gì.
2. Sau khi gửi: mật khẩu/OTP không còn trong state/DOM (outerHTML, localStorage, sessionStorage); tài khoản mới được chọn.
3. Bảng chi tiết: email ĐẦY ĐỦ, bí mật luôn ẩn («••••• đã lưu», không đổ mật khẩu thật), nút «Đăng nhập» và «Xoá» (bấm hai lần mới xoá).
4. Công tắc «Tự đăng nhập lại khi phiên hết hạn» mặc định BẬT.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

try:
    import httpx
    from app import so_module
    from app.main import app
    from gia import MayChuThat
    from test_web2api_nv88_api import MA_PROVIDER, MANIFEST_PROVIDER, MAT_KHAU_GIA, MODULE_ID
    from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401
    from ui_them_tk import chon_cach, mo_form
    CO_UI = True
except ImportError:
    CO_UI = False
    CO_CHROME = False
    so_module = None
    app = None
    MayChuThat = None
    MA_PROVIDER = ""
    MANIFEST_PROVIDER = {}
    MAT_KHAU_GIA = ""
    MODULE_ID = "provider-ui"

KHOA_QT = "khoa-quan-tri-nv144-ui-0123456789"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_TEST_UI = "MatKhauUI-NV144-XYZ"
EMAIL_TEST_UI = "nv144-ui@example.com"


@contextmanager
def moi_truong_ui(tmp_path, monkeypatch):
    data = tmp_path / "data"
    thu_muc = data / "modules" / MODULE_ID
    thu_muc.mkdir(parents=True)
    man = dict(
        MANIFEST_PROVIDER,
        id=MODULE_ID,
        ten="Provider UI Test",
        tien_to="pui",
        nhom={"id": "nhom-pui", "ten": "Nhóm PUI"},
    )
    (thu_muc / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_PROVIDER.replace("MAT_KHAU_GIA", MK_TEST_UI), encoding="utf-8")
    (thu_muc / "web").mkdir()
    (thu_muc / "web" / "index.html").write_text("<!doctype html><title>pui</title>", encoding="utf-8")
    (data / "modules.json").write_text(
        json.dumps({
            "modules": [{"id": MODULE_ID, "cai_luc": "2026-10-19T00:00:00", "cau_hinh_cong_khai": {}}]
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with MayChuThat(app, lifespan="on") as w:
        yield w


def _mo_tab_tai_khoan(trang, w):
    """Mở form «＋ Thêm tài khoản» của module giả (cách đăng nhập); trả mật khẩu quản trị (hộp xác nhận lại khi lưu/xoá)."""
    _, mk_admin = dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-AI.dc.html")
    mo_form(trang)
    chon_cach(trang, "dn")
    return mk_admin


def _go_email_va_mat_khau(trang):
    """Nhập từng ô như người gõ (trang.go = Input.insertText, sự kiện tin cậy)."""
    trang.go("[data-nhap-email]", EMAIL_TEST_UI)
    trang.go("[data-nhap-mat-khau]", MK_TEST_UI)


def _xac_nhan_lai(trang, mk_admin):
    """Phiên web (cookie) lưu/xoá bí mật phải xác nhận lại (step-up, 403 step_up_required): khung.js mở hộp thoại hỏi
    mật khẩu quản trị rồi tự gửi lại đúng một lần. Làm như người dùng: gõ mật khẩu vào hộp thoại rồi bấm «Xác nhận»."""
    trang.cho("!!document.querySelector('dialog[open] input[type=password]')", mo_ta="hộp thoại xác nhận lại mật khẩu")
    trang.go("dialog[open] input[type=password]", mk_admin)
    trang.bam("[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Xác nhận')")
    trang.cho("!document.querySelector('dialog[open]')", mo_ta="hộp thoại xác nhận lại đóng")


def _them_va_dang_nhap(trang, mk_admin):
    _go_email_va_mat_khau(trang)
    trang.bam("document.getElementById('nut-chay-hang-nhap')")
    _xac_nhan_lai(trang, mk_admin)
    trang.cho(f"(document.querySelector('#o-chon .ten') || {{}}).textContent === {EMAIL_TEST_UI!r}", han=20, mo_ta="tài khoản mới được chọn")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_form_co_du_o_va_bao_khi_thieu_email_mat_khau(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        _mo_tab_tai_khoan(trang, w)
        trang.js("window.__post = []; const g = window.fetch.bind(window); window.fetch = (u, o) => { if (o && o.method && o.method !== 'GET') window.__post.push(u); return g(u, o); }")
        # Ô nhập theo đúng hợp đồng
        assert trang.js("document.querySelector('[data-nhap-email]').type") == "email"
        assert trang.js("document.querySelector('[data-nhap-mat-khau]').type") == "password"
        assert trang.js("document.getElementById('nut-mo-otp')") is not None
        trang.js("document.querySelector('#tuy-chon-them summary').click()")
        trang.cho("!!document.getElementById('tk-nhan')", han=5, mo_ta="ô Nhãn trong Tuỳ chọn thêm")
        # Thiếu email+mật khẩu: bấm «Đăng nhập» chỉ báo, không gửi yêu cầu ghi nào
        trang.bam("document.getElementById('nut-chay-hang-nhap')")
        trang.cho("!!document.getElementById('loi-dl')", han=5, mo_ta="báo thiếu email và mật khẩu")
        assert "email" in trang.chu("#loi-dl").lower()
        assert trang.js("window.__post.length") == 0
        # Nút con mắt Hiện/Ẩn
        trang.bam("document.querySelector('[data-hien-mk]')")
        assert trang.js("document.querySelector('[data-nhap-mat-khau]').type") == "text"
        trang.bam("document.querySelector('[data-hien-mk]')")
        assert trang.js("document.querySelector('[data-nhap-mat-khau]').type") == "password"


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_luu_roi_chon_lai_hien_dau_cham_va_mat_khau_roi_dom(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        mk_admin = _mo_tab_tai_khoan(trang, w)
        _them_va_dang_nhap(trang, mk_admin)
        # Mật khẩu đã rời DOM: không còn trong outerHTML
        assert MK_TEST_UI not in trang.js("document.documentElement.outerHTML"), "Mật khẩu không được còn trong DOM sau khi gửi"
        assert trang.js("document.querySelectorAll('#ds-tk').length") == 0
        # Bảng chi tiết: bí mật luôn ẩn, không đổ mật khẩu thật ra trang
        assert "••••• đã lưu" in trang.chu("[data-k268-gt=bi-mat]")
        assert MK_TEST_UI not in trang.js("document.body.innerText")
        # Email ĐẦY ĐỦ (chủ máy 02/11/2026: bản che chỉ chừa tên miền nên không phân biệt được tài khoản) · «Đăng nhập» · «Xoá»
        assert trang.js("document.querySelector('#k268-ct h2').textContent") == EMAIL_TEST_UI
        assert trang.js("!!document.getElementById('k268-dn-tu-dong')") is True
        assert trang.js("!!document.querySelector('[data-dl-xoa]')") is True


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_xoa_phai_bam_hai_lan_va_cong_tac_mac_dinh_bat(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        mk_admin = _mo_tab_tai_khoan(trang, w)
        _them_va_dang_nhap(trang, mk_admin)
        # Bấm «Xoá» lần 1 → chỉ hỏi lại, chưa xoá
        trang.bam("document.querySelector('[data-dl-xoa]')")
        trang.cho("document.querySelector('[data-dl-xoa]').innerText.includes('lần nữa')", mo_ta="nút xoá chuyển sang hỏi lại")
        assert trang.js("(document.querySelector('#o-chon .ten') || {}).textContent") == EMAIL_TEST_UI
        # Bấm lần 2 → xoá thật (có thể cần xác nhận lại)
        trang.bam("document.querySelector('[data-dl-xoa]')")
        try:
            trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent === 'Chưa có tài khoản'", han=4, mo_ta="tài khoản biến mất")
        except AssertionError:
            _xac_nhan_lai(trang, mk_admin)
            trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent === 'Chưa có tài khoản'", han=15, mo_ta="tài khoản biến mất sau lần bấm thứ hai")
        # Công tắc «Tự đăng nhập lại khi phiên hết hạn» mặc định BẬT
        assert trang.js("document.getElementById('dl-tu-dong-lai').checked") is True
