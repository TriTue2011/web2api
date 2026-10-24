"""NV267 — Giao diện khu «Tài khoản» (NV258-P1 nền lại lên 2f0dbb6): form Kiểu bí mật + Địa chỉ, danh sách chip.

Chrome THẬT (CDP) + lõi THẬT. NV258-P1 từng CHƯA kiểm Chrome — các ca này đo thật:
- form thêm có ô chọn «Kiểu bí mật» 4 giá trị (Mật khẩu/Khoá API/Token/Cookie),
  ô «Địa chỉ» mới, nhãn ô bí mật đổi theo kiểu, TOTP chỉ hiện khi kiểu mat_khau;
- chọn «Khoá API»: không bắt buộc email, lưu được → danh sách hiện chip
  «Khoá API» + địa chỉ đã che, KHÔNG lộ khoá thô trên trang;
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
    # Neo #tai-khoan → tab «Tài khoản» (tabTheoNeo). Mở form «＋ Tài khoản»,
    # chọn loại «Tài khoản» rồi nguồn AI giả → khối «Tài khoản đã lưu» (dl-*)
    # của NV144 mới dựng (them.laTaiKhoan).
    trang.di(w.url + "/He-thong-AI.dc.html#tai-khoan")
    trang.cho("document.getElementById('nut-them')", mo_ta="nút ＋ Tài khoản")
    trang.bam("document.getElementById('nut-them')")
    trang.cho("document.querySelector('#form-them')", mo_ta="form thêm tài khoản")
    loai = "document.querySelector('[data-loai-them=\"tai_khoan\"]')"
    if trang.js(f"!!{loai}"):
        trang.bam(loai)
    trang.cho("document.querySelector('#dl-kieu-bi-mat')", han=15,
              mo_ta="ô chọn Kiểu bí mật")


def chon_kieu(trang, kieu):
    trang.js("(() => { const s = document.querySelector('#dl-kieu-bi-mat');"
             f" s.value = {kieu!r};"
             " s.dispatchEvent(new Event('change', {bubbles: true})); return s.value; })()")
    trang.cho(f"document.querySelector('#dl-kieu-bi-mat').value === {kieu!r}",
              han=5, mo_ta=f"ô chọn kiểu = {kieu}")


def test_form_co_o_kieu_bi_mat_va_dia_chi(trang, tmp_path, monkeypatch):
    """1280px: form có đủ ô Kiểu bí mật (4 giá trị), ô Địa chỉ; mặc định nhãn «Mật khẩu», TOTP hiện."""
    with loi_that(tmp_path, monkeypatch) as w:
        vao_trang_tai_khoan(trang, w)
        tuy_chon = trang.js("[...document.querySelectorAll('#dl-kieu-bi-mat option')]"
                            ".map((o) => [o.value, o.textContent.trim()])")
        assert [v for v, _ in tuy_chon] == ["mat_khau", "khoa_api", "token", "cookie"], tuy_chon
        assert tuy_chon[1][1] == "Khoá API"
        # Giá trị mặc định mat_khau (có thể cần một nhịp render để ổn định).
        trang.cho("['mat_khau', ''].includes(document.querySelector('#dl-kieu-bi-mat').value)", han=10,
                  mo_ta="ô chọn có giá trị hợp lệ")
        assert trang.js("document.querySelector('#dl-dia-chi') !== null"), "thiếu ô Địa chỉ"
        # Mặc định: nhãn «Mật khẩu», TOTP hiện.
        nhan = trang.js("(() => { const l = document.querySelector('#dl-mat-khau').closest('label');"
                        " return l ? l.childNodes[0].textContent.trim() : ''; })()")
        assert nhan == "Mật khẩu", nhan
        assert trang.js("document.querySelector('#dl-totp') !== null"), "TOTP phải hiện với kiểu mat_khau"


def test_chon_khoa_api_doi_nhan_va_an_totp(trang, tmp_path, monkeypatch):
    """1280px: chọn «Khoá API» → nhãn ô bí mật thành «Khoá API», TOTP biến khỏi DOM."""
    with loi_that(tmp_path, monkeypatch) as w:
        vao_trang_tai_khoan(trang, w)
        chon_kieu(trang, "khoa_api")
        nhan = trang.js("(() => { const l = document.querySelector('#dl-mat-khau').closest('label');"
                        " return l ? l.childNodes[0].textContent.trim() : ''; })()")
        assert nhan == "Khoá API", nhan
        trang.cho("document.querySelector('#dl-totp') === null", han=5,
                  mo_ta="TOTP phải ẩn khi kiểu là khoá")
        goi_y = trang.js("document.querySelector('#dl-mat-khau').placeholder")
        assert "khoá" in goi_y.lower() or "token" in goi_y.lower(), goi_y


def test_luu_khoa_api_qua_form_khong_can_email(trang, tmp_path, monkeypatch):
    """1280px: điền khoá API qua form (không email) → lưu được; danh sách hiện chip + địa chỉ che, không lộ khoá."""
    with loi_that(tmp_path, monkeypatch) as w:
        vao_trang_tai_khoan(trang, w)
        chon_kieu(trang, "khoa_api")
        trang.go("#dl-mat-khau", KHOA_GIA)
        trang.go("#dl-dia-chi", "https://api.nha-cung-cap-gia.example/v1")
        trang.cho("!document.querySelector('#nut-dl-luu').disabled", han=5,
                  mo_ta="nút Lưu phải bật khi đã có khoá")
        trang.bam("document.querySelector('#nut-dl-luu')", "nút Lưu tài khoản")
        xac_nhan_lai_step_up(trang)  # POST tai-khoan/da-luu đòi step-up
        trang.cho("document.querySelector('[data-tk-da-luu]')", han=15,
                  mo_ta="hàng tài khoản mới trong danh sách")
        chu = trang.js("document.body.innerText")
        assert "Khoá API" in chu, "danh sách phải hiện chip «Khoá API»"
        assert KHOA_GIA not in chu, "KHÔNG được lộ khoá thô trên trang"
        assert "api.nha-cung-cap-gia.example" not in chu, "địa chỉ phải ở dạng che"


def test_390px_form_hien_day_du(trang, tmp_path, monkeypatch):
    """390×800: form Tài khoản dựng đủ ô, không tràn ngang, lưu tài khoản thường được."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.khung(390, 800, dien_thoai=True)
        vao_trang_tai_khoan(trang, w)
        assert trang.js("document.documentElement.scrollWidth <= window.innerWidth"), \
            "tràn ngang ở 390px"
        for oid in ("#dl-kieu-bi-mat", "#dl-dia-chi", "#dl-email", "#dl-mat-khau", "#dl-totp"):
            assert trang.js(f"document.querySelector({oid!r}) !== null"), f"thiếu {oid} ở 390px"
        trang.go("#dl-email", "rong-nv267@example.com")
        trang.go("#dl-mat-khau", MK_GIA)
        trang.bam("document.querySelector('#nut-dl-luu')", "nút Lưu tài khoản")
        xac_nhan_lai_step_up(trang)  # POST tai-khoan/da-luu đòi step-up
        trang.cho("document.querySelector('[data-tk-da-luu]')", han=15,
                  mo_ta="hàng tài khoản mới ở 390px")


def test_sang_toi_form_van_dung(trang, tmp_path, monkeypatch):
    """Chế độ tối (emulate): form vẫn dựng đủ, chọn kiểu khoá được."""
    with loi_that(tmp_path, monkeypatch) as w:
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "dark"}]}, trang.s)
        vao_trang_tai_khoan(trang, w)
        chon_kieu(trang, "token")
        assert trang.js("document.querySelector('#dl-totp') === null"), \
            "TOTP phải ẩn ở chế độ tối khi kiểu là token"
        # Trả lại chế độ sáng cho các test sau.
        trang.cdp.goi("Emulation.setEmulatedMedia",
                      {"features": [{"name": "prefers-color-scheme", "value": "light"}]}, trang.s)
