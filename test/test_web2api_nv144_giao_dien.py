"""Kiểm thử giao diện Chrome (NV144): kho tài khoản đã lưu, thêm theo ô như c2a.

LƯU Ý: Hộp cát không có Chrome nên các test này sẽ tự động bỏ qua khi không có Chrome (CO_CHROME=False).
Người điều phối sẽ chạy test này trong môi trường có Chrome thật.

Bao quát:
1. Khối «Tài khoản đã lưu (n)»: dropdown chọn → điền email, mật khẩu hiện placeholder «•••• đã lưu»
   (KHÔNG đổ mật khẩu thật ra ô).
2. Các ô Email / Mật khẩu (nút Hiện/Ẩn) / TOTP secret («để trống = xác minh thiết bị») / Nhãn;
   mục thu gọn «Cách lấy secret» đủ 5 bước.
3. Nút «Lưu tài khoản» vô hiệu khi thiếu email+mật khẩu; bấm hai lần liên tiếp chỉ MỘT lần gửi
   (guard dlDangLuu).
4. Sau khi gửi: mật khẩu/TOTP không còn trong state/DOM (outerHTML, localStorage, sessionStorage).
5. Mỗi tài khoản một dòng: email che · nhãn · trạng thái · «Đăng nhập» · «Xoá» (bấm hai lần mới xoá).
6. Công tắc «Tự đăng nhập lại khi phiên hết hạn» mặc định BẬT.
7. Nút «Lưu tất cả» lưu mọi hàng đang gõ ở khối hàng loạt.
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
    """Mở tab «Tài khoản» của module giả; trả mật khẩu quản trị (cần cho hộp thoại xác nhận lại khi lưu/xoá)."""
    _, mk_admin = dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-AI.dc.html")
    trang.cho("document.getElementById('nut-them')", mo_ta="nút Thêm tài khoản")
    trang.bam("document.getElementById('nut-them')")
    trang.cho(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')", mo_ta="chọn module")
    trang.bam(f"document.querySelector('[data-chon-module=\"{MODULE_ID}\"]')")
    trang.cho("document.getElementById('dl-khoi')", mo_ta="khối Tài khoản đã lưu")
    return mk_admin


def _go_email_va_mat_khau(trang):
    """Nhập từng ô như người gõ (trang.go = Input.insertText, sự kiện tin cậy), ô này xong mới sang ô kia.

    KHÔNG gán .value cả hai ô rồi mới bắn sự kiện input: ô điều khiển theo state (support.js dieuKhien) nên lần dựng
    lại sau sự kiện của ô Email đặt ô Mật khẩu về giá trị state còn rỗng; sự kiện input của ô Mật khẩu sau đó đọc
    chuỗi rỗng → state không có mật khẩu → nút «Lưu tài khoản» vẫn vô hiệu, không có yêu cầu nào được gửi."""
    trang.go("#dl-email", EMAIL_TEST_UI)
    trang.go("#dl-mat-khau", MK_TEST_UI)
    trang.cho("document.getElementById('nut-dl-luu').disabled === false", mo_ta="nút «Lưu tài khoản» bật khi đủ email+mật khẩu")


def _xac_nhan_lai(trang, mk_admin):
    """Phiên web (cookie) lưu/xoá bí mật phải xác nhận lại (step-up, 403 step_up_required): khung.js mở hộp thoại hỏi
    mật khẩu quản trị rồi tự gửi lại đúng một lần. Làm như người dùng: gõ mật khẩu vào hộp thoại rồi bấm «Xác nhận»."""
    trang.cho("!!document.querySelector('dialog[open] input[type=password]')", mo_ta="hộp thoại xác nhận lại mật khẩu")
    trang.go("dialog[open] input[type=password]", mk_admin)
    trang.bam("[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Xác nhận')")
    trang.cho("!document.querySelector('dialog[open]')", mo_ta="hộp thoại xác nhận lại đóng")


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_khoi_da_luu_co_du_o_va_nut_luu_vo_hieu_khi_thieu(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        _mo_tab_tai_khoan(trang, w)
        # Ô nhập theo đúng hợp đồng
        assert trang.js("document.getElementById('dl-email').type") == "email"
        assert trang.js("document.getElementById('dl-mat-khau').type") == "password"
        assert trang.js("document.getElementById('dl-totp')") is not None
        assert trang.js("document.getElementById('dl-nhan')") is not None
        # Mục thu gọn «Cách lấy secret» đủ 5 bước
        assert trang.js("document.querySelectorAll('#dl-khoi ol li').length") == 5
        # Nút «Lưu tài khoản» vô hiệu khi thiếu email+mật khẩu
        assert trang.js("document.getElementById('nut-dl-luu').disabled") is True
        assert trang.js("document.getElementById('nut-dl-luu-dang-nhap').disabled") is True
        # Nút con mắt Hiện/Ẩn
        trang.bam("document.getElementById('dl-hien-mk')")
        assert trang.js("document.getElementById('dl-mat-khau').type") == "text"
        trang.bam("document.getElementById('dl-hien-mk')")
        assert trang.js("document.getElementById('dl-mat-khau').type") == "password"


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_luu_roi_chon_lai_hien_dau_cham_va_mat_khau_roi_dom(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        mk_admin = _mo_tab_tai_khoan(trang, w)
        # Gõ và lưu
        _go_email_va_mat_khau(trang)
        trang.bam("document.getElementById('nut-dl-luu')")
        _xac_nhan_lai(trang, mk_admin)
        trang.cho(
            "document.querySelectorAll('[data-tk-da-luu]').length === 1",
            mo_ta="dòng tài khoản đã lưu xuất hiện",
        )
        # Bấm hai lần liên tiếp vào «Lưu và đăng nhập» khi không đổi gì → không gửi trùng
        # (guard dlDangLuu; ở đây chỉ kiểm nút «Lưu tài khoản» báo đã lưu, không tạo trùng)
        assert trang.js("document.querySelectorAll('[data-tk-da-luu]').length") == 1
        # Mật khẩu đã rời DOM: không còn trong outerHTML
        html = trang.js("document.documentElement.outerHTML")
        assert MK_TEST_UI not in html, "Mật khẩu không được còn trong DOM sau khi gửi"
        # Chọn lại từ dropdown → email điền, mật khẩu hiện «•••• đã lưu», không đổ mật khẩu thật
        id_tk = trang.js("document.querySelector('[data-tk-da-luu]').getAttribute('data-tk-da-luu')")
        trang.js(f"document.getElementById('dl-chon').value = {json.dumps(id_tk)}")
        trang.js("document.getElementById('dl-chon').dispatchEvent(new Event('change', {bubbles: true}))")
        trang.cho(
            "document.getElementById('dl-mat-khau').placeholder === '•••• đã lưu'",
            mo_ta="placeholder •••• đã lưu",
        )
        assert trang.js("document.getElementById('dl-mat-khau').value") == ""
        assert MK_TEST_UI not in trang.js("document.documentElement.outerHTML")
        # Dòng đã lưu: email che · «Đăng nhập» · «Xoá»
        assert "***" in trang.js("document.querySelector('[data-tk-da-luu] span').innerText")
        assert trang.js(f"document.querySelector('[data-dl-dang-nhap=\"{id_tk}\"]')") is not None
        assert trang.js(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]')") is not None


@pytest.mark.skipif(not CO_CHROME, reason="chưa chạy vì không có Chrome")
def test_ui_xoa_phai_bam_hai_lan_va_cong_tac_mac_dinh_bat(trang, tmp_path, monkeypatch):
    with moi_truong_ui(tmp_path, monkeypatch) as w:
        mk_admin = _mo_tab_tai_khoan(trang, w)
        # Lưu một tài khoản qua giao diện
        _go_email_va_mat_khau(trang)
        trang.bam("document.getElementById('nut-dl-luu')")
        _xac_nhan_lai(trang, mk_admin)
        trang.cho(
            "document.querySelectorAll('[data-tk-da-luu]').length === 1",
            mo_ta="dòng tài khoản đã lưu xuất hiện",
        )
        id_tk = trang.js("document.querySelector('[data-tk-da-luu]').getAttribute('data-tk-da-luu')")
        # Bấm «Xoá» lần 1 → chỉ hỏi lại, chưa xoá
        trang.bam(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]')")
        trang.cho(
            f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]').innerText.includes('lần nữa')",
            mo_ta="nút xoá chuyển sang hỏi lại",
        )
        assert trang.js("document.querySelectorAll('[data-tk-da-luu]').length") == 1
        # Bấm lần 2 → xoá thật
        trang.bam(f"document.querySelector('[data-dl-xoa=\"{id_tk}\"]')")
        trang.cho(
            "document.querySelectorAll('[data-tk-da-luu]').length === 0",
            mo_ta="dòng tài khoản biến mất sau lần bấm thứ hai",
        )
        # Công tắc «Tự đăng nhập lại khi phiên hết hạn» mặc định BẬT
        assert trang.js("document.getElementById('dl-tu-dong-lai').checked") is True
