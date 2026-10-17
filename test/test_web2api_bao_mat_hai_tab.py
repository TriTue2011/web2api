"""Chrome THẬT: tab «Bảo mật & người dùng» chia hai tab con (Bảo mật | Người dùng) + 2FA + bỏ chặn đăng nhập.

Chủ máy 17/10: 2FA phải nằm trong Bảo mật & người dùng, trang chia hai tab riêng biệt; khoá đăng nhập phải có chỗ bỏ chặn
vì người dùng có thể quên mật khẩu. Kiểm:
1. Hai tab: admin thấy cả hai (mặc định Bảo mật; ?tab=nguoi-dung mở tab Người dùng; phím mũi tên đổi tab); nội dung mỗi tab tách
   biệt. Vai user chỉ có tab Bảo mật và không có mục quản trị (khoá đăng nhập, sao lưu), kể cả khi gõ ?tab=nguoi-dung.
2. 2FA đủ vòng qua giao diện: bật (mật khẩu → khoá → mã) → 10 mã khôi phục hiện MỘT lần → đăng nhập hỏi mã → vào được.
3. Khoá đăng nhập: người bị chặn hiện ở danh sách, bấm «Bỏ chặn» thì biến mất và đăng nhập lại được.
4. Quản trị «Tắt 2FA» hộ người dùng (hai lần bấm).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import httpx
import pytest

from app import nguoi_dung, so_module, xac_thuc_2fa
from app.main import app
from gia import MayChuThat
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

pytestmark = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")

KHOA_QT = "khoa-quan-tri-hai-tab-0123456789abcdef"
MK_ND = "MatKhauNguoiDungHaiTab123456"
MK_QT = "MatKhauAdminBaoMat123456"  # mật khẩu mặc định của dang_nhap_quan_tri


@contextmanager
def loi_that(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_SAI_TOI_DA", "3")
    monkeypatch.setenv("WEB2API_RL_LOI_XAC_THUC", "1000")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    nguoi_dung._THEO_DOI_SAI.clear()
    with MayChuThat(app, lifespan="on") as w:
        yield w


def _tao_nd(ten, vai="user", mk=MK_ND):
    asyncio.run(nguoi_dung.tao_nguoi_dung(vai=vai, ten_dang_nhap=ten, mat_khau=mk))


def _dang_nhap_nd(trang, base, ten, mk=MK_ND):
    """Đăng nhập một tài khoản CÓ SẴN qua trang Dang-nhap (không tạo admin như dang_nhap_quan_tri)."""
    trang.di(f"{base}/Dang-nhap.dc.html")
    trang.go("#o-ten", ten)
    trang.go("#o-mat-khau", mk)
    trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="nút đăng nhập")


def _cho_dc(trang):
    trang.cho("(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
              mo_ta="trang dựng xong")


def _muc(trang):
    return trang.js("[...document.querySelectorAll('[data-mo-muc]')].map(b => b.dataset.moMuc)")


def test_hai_tab_admin_va_user(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _tao_nd("nguoi-thuong")
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html", mo_muc=False)
        trang.cho("document.getElementById('tab-bao-mat') && document.getElementById('tab-nguoi-dung')", mo_ta="hai tab")
        sel = lambda t: trang.js(f"document.getElementById('{t}').getAttribute('aria-selected')")  # noqa: E731
        assert sel("tab-bao-mat") == "true" and sel("tab-nguoi-dung") == "false", "mặc định Bảo mật"
        trang.cho("document.querySelectorAll('[data-mo-muc]').length === 6", mo_ta="mục tab Bảo mật")
        bm = _muc(trang)
        assert set(bm) == {"hai-buoc", "khoa-dang-nhap", "sao-luu-khoi-phuc", "tao-khoa-toi", "danh-sach-khoa-toi", "doi-mat-khau"}, bm

        # bấm tab Người dùng: nội dung tách biệt, địa chỉ ghi nhớ tab
        trang.bam("document.getElementById('tab-nguoi-dung')")
        trang.cho("document.querySelectorAll('[data-mo-muc]').length === 4", mo_ta="mục tab Người dùng")
        assert set(_muc(trang)) == {"tao-nguoi-dung", "danh-sach-nguoi-dung", "tao-khoa", "danh-sach-khoa"}
        assert sel("tab-nguoi-dung") == "true" and "tab=nguoi-dung" in trang.js("location.search")
        # phím mũi tên đổi tab
        trang.js("document.getElementById('tab-con').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}))")
        trang.cho("document.getElementById('tab-bao-mat').getAttribute('aria-selected') === 'true'", mo_ta="mũi tên về tab Bảo mật")
        # gõ thẳng ?tab=nguoi-dung
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        trang.cho("document.querySelectorAll('[data-mo-muc]').length === 4")

        # vai user: chỉ tab Bảo mật, không có mục quản trị, kể cả gõ ?tab=nguoi-dung
        trang.cdp.goi("Network.clearBrowserCookies", {}, trang.s)
        _dang_nhap_nd(trang, w.url, "nguoi-thuong")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname)", mo_ta="đã đăng nhập")
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        trang.cho("document.querySelectorAll('[data-mo-muc]').length >= 3", mo_ta="mục của user")
        assert trang.js("document.getElementById('tab-nguoi-dung')") is None, "user không có tab Người dùng"
        muc_user = set(_muc(trang))
        assert muc_user == {"hai-buoc", "tao-khoa-toi", "danh-sach-khoa-toi", "doi-mat-khau"}, muc_user
        assert "khoa-dang-nhap" not in muc_user and "sao-luu-khoi-phuc" not in muc_user


def test_bat_2fa_qua_giao_dien_roi_dang_nhap_bang_ma(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _tao_nd("qt-phu", "admin", MK_QT)  # chưa có admin thì trang đăng nhập chuyển sang Thiết lập
        _tao_nd("nguoi-2fa-ui")
        _dang_nhap_nd(trang, w.url, "nguoi-2fa-ui")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname)", mo_ta="đã đăng nhập")
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html", mo_muc=False)
        trang.cho("document.querySelector('[data-mo-muc=\"hai-buoc\"]')", mo_ta="mục 2FA")
        assert trang.js("document.querySelector('[data-mo-muc=\"hai-buoc\"]').getAttribute('aria-expanded')") == "false"
        assert "Tắt" in trang.chu("[data-mo-muc=\"hai-buoc\"] .dem-phai")
        trang.bam("document.querySelector('[data-mo-muc=\"hai-buoc\"]')")
        trang.cho("document.getElementById('o-2fa-mk')", mo_ta="ô mật khẩu")
        # bật cần mật khẩu: để trống → lời nhắc, sai → lỗi, đúng → hiện khoá
        trang.bam("document.getElementById('bat-dau-2fa')")
        trang.cho("document.getElementById('loi-2fa')", mo_ta="nhắc nhập mật khẩu")
        trang.go("#o-2fa-mk", "mat-khau-sai-12345")
        trang.bam("document.getElementById('bat-dau-2fa')")
        trang.cho("document.getElementById('loi-2fa') && /không chính xác/.test(document.getElementById('loi-2fa').innerText)",
                  mo_ta="sai mật khẩu bị từ chối")
        trang.js("document.getElementById('o-2fa-mk').value = ''")
        trang.go("#o-2fa-mk", MK_ND)
        trang.bam("document.getElementById('bat-dau-2fa')")
        trang.cho("document.getElementById('bi-mat-2fa')", mo_ta="hiện khoá bí mật")
        bi_mat = trang.chu("#bi-mat-2fa").strip()
        assert len(bi_mat) >= 26 and bi_mat.isalnum() and bi_mat.upper() == bi_mat
        assert not xac_thuc_2fa.da_bat_2fa(nguoi_dung.tim_theo_ten("nguoi-2fa-ui")["id"]), "chưa xác nhận thì CHƯA bật"
        # mã sai → lỗi; mã đúng → bật + 10 mã khôi phục
        trang.go("#o-2fa-ma", "000000")
        trang.bam("document.getElementById('xac-nhan-2fa')")
        trang.cho("document.getElementById('loi-2fa')", mo_ta="mã sai")
        trang.js("document.getElementById('o-2fa-ma').value = ''")
        trang.go("#o-2fa-ma", xac_thuc_2fa.sinh_totp(bi_mat))
        trang.bam("document.getElementById('xac-nhan-2fa')")
        trang.cho("document.getElementById('ds-ma-khoi-phuc')", mo_ta="hiện mã khôi phục")
        assert trang.js("document.querySelectorAll('#ds-ma-khoi-phuc .ma').length") == 10
        assert "Đang bật" in trang.chu("[data-mo-muc=\"hai-buoc\"] .dem-phai")
        trang.bam("document.getElementById('da-luu-ma-khoi-phuc')")
        trang.cho("!document.getElementById('ds-ma-khoi-phuc')", mo_ta="mã khôi phục biến mất sau khi lưu")
        assert xac_thuc_2fa.da_bat_2fa(nguoi_dung.tim_theo_ten("nguoi-2fa-ui")["id"])

        # Đăng nhập lại: hỏi mã 2FA; mã đúng của bước thời gian MỚI (chống phát lại) → vào được
        trang.cdp.goi("Network.clearBrowserCookies", {}, trang.s)
        _dang_nhap_nd(trang, w.url, "nguoi-2fa-ui")
        trang.cho("document.getElementById('o-ma-2fa')", mo_ta="ô nhập mã 2FA")
        assert "Dang-nhap" in trang.js("location.pathname")
        trang.go("#o-ma-2fa", xac_thuc_2fa.sinh_totp(bi_mat, time.time() + 30))
        trang.bam("document.getElementById('nut-dang-nhap')")
        trang.cho("!/(Dang-nhap|index)/.test(location.pathname)", mo_ta="vào được sau khi nhập mã")


def test_bo_chan_dang_nhap_tren_giao_dien(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _tao_nd("nguoi-quen-mk-ui")
        for _ in range(3):
            httpx.post(f"{w.url}/api/dang-nhap", json={"ten_dang_nhap": "nguoi-quen-mk-ui", "mat_khau": "sai-mat-khau-12345"})
        r = httpx.post(f"{w.url}/api/dang-nhap", json={"ten_dang_nhap": "nguoi-quen-mk-ui", "mat_khau": MK_ND})
        assert r.status_code == 401, "đang bị chặn: mật khẩu đúng vẫn bị từ chối"

        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html", mo_muc=False)
        trang.cho("document.querySelector('[data-mo-muc=\"khoa-dang-nhap\"]')", mo_ta="mục khoá đăng nhập")
        assert trang.js("document.querySelector('[data-mo-muc=\"khoa-dang-nhap\"]').getAttribute('aria-expanded')") == "false"
        assert "1 đang chặn" in trang.chu("#dem-khoa-dn") or "đang chặn" in trang.chu("#dem-khoa-dn")
        trang.bam("document.querySelector('[data-mo-muc=\"khoa-dang-nhap\"]')")
        trang.cho("document.querySelector('[data-khoa-dn]')", mo_ta="hàng bị chặn")
        hang = trang.chu("[data-khoa-dn]")
        assert "nguoi-quen-mk-ui" in hang and "Tài khoản" in hang
        # một dòng: cao vừa một dòng chữ ở 1280
        trang.khung(1280, 900)
        assert trang.js("Math.round(document.querySelector('[data-khoa-dn]').getBoundingClientRect().height)") <= 56
        trang.bam("document.querySelector('[data-khoa-dn] [data-hd=\"bo-chan\"]')")
        trang.cho("document.getElementById('khong-khoa-dn')", mo_ta="danh sách rỗng sau khi bỏ chặn")
        assert "Đã bỏ chặn" in trang.chu("#tin-khoa-dn")
        r2 = httpx.post(f"{w.url}/api/dang-nhap", json={"ten_dang_nhap": "nguoi-quen-mk-ui", "mat_khau": MK_ND})
        assert r2.status_code == 200, r2.text


def test_admin_tat_2fa_ho_nguoi_dung_hai_lan_bam(trang, tmp_path, monkeypatch):
    with loi_that(tmp_path, monkeypatch) as w:
        _tao_nd("nguoi-mat-dt")
        uid = nguoi_dung.tim_theo_ten("nguoi-mat-dt")["id"]
        bi_mat, _ = xac_thuc_2fa.bat_dau_dang_ky(uid, "nguoi-mat-dt")
        assert xac_thuc_2fa.xac_nhan_dang_ky(uid, xac_thuc_2fa.sinh_totp(bi_mat))[0]
        dang_nhap_quan_tri(trang, w.url)
        trang.di(f"{w.url}/He-thong-Bao-mat.dc.html?tab=nguoi-dung", mo_muc=False)
        trang.bam("document.querySelector('[data-mo-muc=\"danh-sach-nguoi-dung\"]')")
        sel = "[data-nd-id=\"" + uid + "\"]"
        trang.cho(f"document.querySelector('{sel} [data-hd=\"tat-2fa-nd\"]')", mo_ta="nút Tắt 2FA")
        assert "2FA" in trang.chu(sel + " .nguoi-dung-nhan")
        trang.bam(f"document.querySelector('{sel} [data-hd=\"tat-2fa-nd\"]')")
        trang.cho(f"/Xác nhận/.test(document.querySelector('{sel} [data-hd=\"tat-2fa-nd\"]').innerText)", mo_ta="hỏi xác nhận")
        assert xac_thuc_2fa.da_bat_2fa(uid), "bấm một lần CHƯA tắt"
        trang.bam(f"document.querySelector('{sel} [data-hd=\"tat-2fa-nd\"]')")
        trang.cho(f"!document.querySelector('{sel} [data-hd=\"tat-2fa-nd\"]')", mo_ta="nút biến mất sau khi tắt")
        assert not xac_thuc_2fa.da_bat_2fa(uid)
