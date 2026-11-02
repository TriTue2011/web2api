"""NV267 — Giao diện khu «Tài khoản»: khoá API/token/cookie đã lưu và form thêm gọn (cập nhật theo NV282).

Chrome THẬT (CDP) + lõi THẬT. NV282 thay form thứ hai «Kiểu bí mật / Địa chỉ» của NV258-P1 bằng MỘT form hiện dần; ý nghĩa các ca giữ nguyên:
- không còn ô «Kiểu bí mật» / «Địa chỉ» riêng; form chỉ có ô cần thiết (ô khoá/địa chỉ API nằm trong cách «khoá API»);
- tài khoản kiểu «Khoá API» (lưu không cần email) hiện loại «API» + địa chỉ đã che, KHÔNG lộ khoá thô trên trang;
- 390px và 1280px; sáng/tối (emulate prefers-color-scheme).
Giá trị GIẢ toàn bộ (quy ước spec).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401 — fixture
from ui_mau import module_ai
from ui_them_tk import chon_cach, mo_form

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-qt-nv267-chrome-0123456789abcdef"
KHOA_GIA = "sk-gia-nv267-chrome-0123456789abcdef"
MK_GIA = "MatKhauGia123!"
MK_ADMIN = "MatKhauAdminBaoMat123456"  # khớp dang_nhap_quan_tri trong ui_chung


def xac_nhan_lai_step_up(trang):
    """Xử lý dialog step-up của khung (403 step_up_required): nhập mật khẩu admin, bấm «Xác nhận»."""
    # Dialog tạo động bằng createElement → querySelector trần serialize thành {} (falsy) → phải dùng !!
    trang.cho("!!document.querySelector('dialog input[type=\"password\"]')", han=10,
              mo_ta="dialog xác nhận lại (step-up)")
    trang.go("dialog input[type=\"password\"]", MK_ADMIN)
    nut = "(() => { const ds = [...document.querySelectorAll('dialog button')];" \
          " return !!ds.find((b) => b.textContent.includes('Xác nhận')); })()"
    trang.cho(nut, han=5, mo_ta="nút Xác nhận step-up")
    trang.bam("(() => { const ds = [...document.querySelectorAll('dialog button')];"
              " return ds.find((b) => b.textContent.includes('Xác nhận')); })()",
              "nút Xác nhận step-up")


@contextmanager
def loi_that(tmp_path, monkeypatch):
    # Cài module AI giả (chỉ đặt file, chưa liên kết) — tab «Tài khoản»
    # đòi pham_vi 'da_cai' mới hiện. Thêm stub dang_nhap để module có
    # coKieuDangNhap (loại «Tài khoản» mới hiện trong form thêm).
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    tep = module_ai()
    tep["module.py"] = tep["module.py"].replace(
        b"    async def chat(self, body, stream):",
        b"    async def dang_nhap(self, tai_khoan, trinh_duyet):\n"
        b"        return {'thanh_cong': True}\n\n"
        b"    async def chat(self, body, stream):")
    ghi_thu_muc(tmp_path / "modules" / "ai-gia", tep)
    with MayChuThat(app, lifespan="on") as w:
        yield w


def vao_trang_tai_khoan(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
    mo_form(trang)


def test_form_moi_gon_khong_con_o_kieu_bi_mat_va_dia_chi_rieng(trang, tmp_path, monkeypatch):
    """1280px: MỘT form; hai cách (đăng nhập / khoá API); không còn các ô dl-* của form thứ hai."""
    with loi_that(tmp_path, monkeypatch) as w:
        vao_trang_tai_khoan(trang, w)
        for oid in ("#dl-kieu-bi-mat", "#dl-dia-chi", "#dl-email", "#dl-mat-khau", "#dl-totp", "#dl-khoi", "#nut-dl-luu"):
            assert trang.js(f"document.querySelector({oid!r}) === null"), f"{oid} thuộc form thứ hai đã bỏ"
        assert trang.js("document.querySelectorAll('form').length") == 1
        assert trang.js("document.querySelectorAll('[data-cach-them]').length") == 2, "module vừa có khoá API vừa tự đăng nhập → hai cách"
        # Cách «khoá API»: ô khoá (mật khẩu, không điền sẵn); địa chỉ nằm trong «Tuỳ chọn thêm»
        chon_cach(trang, "api")
        assert trang.js("document.querySelector('#form-them [data-truong=api_keys] input').type") == "password"
        assert trang.js("document.querySelector('#tuy-chon-them').open") is False
        # Cách «đăng nhập»: Email + Mật khẩu, khoá OTP chỉ khi bấm
        chon_cach(trang, "dn")
        assert trang.js("document.querySelector('[data-nhap-mat-khau]').type") == "password"
        assert trang.js("document.querySelector('[data-nhap-otp]') === null")


def test_khoa_api_da_luu_hien_loai_api_dia_chi_che_khong_lo_khoa(trang, tmp_path, monkeypatch):
    """Tài khoản kiểu «Khoá API» (không email): ô chọn ghi loại «API», địa chỉ che, khoá/địa chỉ thô không lọt vào trang."""
    with loi_that(tmp_path, monkeypatch) as w:
        import httpx
        r = httpx.post(f"{w.url}/api/tai-khoan/da-luu", headers={"Authorization": f"Bearer {KHOA_QT}"},
                       json={"bi_mat": KHOA_GIA, "kieu_bi_mat": "khoa_api", "dia_chi": "https://api.nha-cung-cap-gia.example/v1", "module_id": "ai-gia"})
        assert r.status_code == 200, r.text
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
        trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent && document.querySelector('#o-chon .ten').textContent !== 'Chưa có tài khoản'",
                  han=15, mo_ta="tài khoản khoá API được chọn")
        assert " API " in " " + trang.js("document.querySelector('#o-chon .phu').textContent") + " ", "ô chọn phải ghi loại «API»"
        chu = trang.js("document.body.innerText")
        html = trang.js("document.documentElement.outerHTML")
        assert KHOA_GIA not in chu and KHOA_GIA not in html, "KHÔNG được lộ khoá thô trên trang"
        assert "api.nha-cung-cap-gia.example" not in chu, "địa chỉ phải ở dạng che"
        assert "••••• đã lưu" in chu


def test_390px_form_hien_day_du(trang, tmp_path, monkeypatch):
    """390×800: form dựng đủ ô, không tràn ngang, thêm tài khoản thường được."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(390, 800, dien_thoai=True)
        vao_trang_tai_khoan(trang, w)
        chon_cach(trang, "dn")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth"), "tràn ngang ở 390px"
        for oid in ("[data-nhap-email]", "[data-nhap-mat-khau]", "#nut-mo-otp", "#nut-chay-hang-nhap"):
            assert trang.js(f"document.querySelector({oid!r}) !== null"), f"thiếu {oid} ở 390px"
        trang.go("[data-nhap-email]", "rong-nv267@example.com")
        trang.go("[data-nhap-mat-khau]", MK_GIA)
        trang.bam("document.querySelector('#nut-chay-hang-nhap')", "nút Đăng nhập")
        xac_nhan_lai_step_up(trang)  # POST tai-khoan/da-luu đòi step-up
        trang.cho("(document.querySelector('#o-chon .ten') || {}).textContent === 'rong-nv267@example.com'", han=20, mo_ta="tài khoản mới ở 390px")
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth")


def test_sang_toi_form_van_dung(trang, tmp_path, monkeypatch):
    """Chế độ tối (emulate): form vẫn dựng đủ, chọn cách thêm được (trang không có giao diện tối riêng: chỉ kiểm không vỡ)."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "dark"}]}, trang.s)
        vao_trang_tai_khoan(trang, w)
        chon_cach(trang, "dn")
        assert trang.js("!!document.querySelector('[data-nhap-email]')")
        assert trang.js("document.querySelector('[data-nhap-mat-khau]').type") == "password"
        # Trả lại chế độ sáng cho các test sau.
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "light"}]}, trang.s)
