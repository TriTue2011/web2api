"""Test RIÊNG cho module grok-web: luồng đăng nhập tự động độc lập trong module.

Module này tự chứa T0-T3, kênh TOTP, khóa serial, hoãn captcha, mã lỗi riêng
— không import logic của module kia, không dùng logic khôi phục của lõi làm
logic chính. Trình duyệt là GIẢ, không Internet, không Chrome.
Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.

Giới hạn ghi rõ trong test này (không phải test che): chế độ mặc định là
truc_tiep (form x.com/login); chế độ google chỉ tái dùng hồ sơ Google đã
đăng nhập nên test google chỉ kiểm đường hồ sơ chưa đăng nhập → thất bại,
không kiểm đăng nhập Google thật.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_module_tu_dong_test"

SSO_GIA_OK = "sso-gia-module-grok-ok"
UID_GIA = "uid-gia-module-grok"
MAT_KHAU_GIA = "mat-khau-gia-module-grok-001"
EMAIL_GIA = "ban-gia-module-grok@example.com"
TOTP_SECRET_GIA = "JBSWY3DPEHPK3PXP"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", THU_MUC / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("grok-web")
    return m


def _trinh_duyet(tin_hieu, cookie):
    return TrinhDuyetGia(trang={
        "https://x.com/login": {"tin_hieu": tin_hieu, "cookie": cookie}})


def test_doc_lap_khong_import_module_kia():
    nguon = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert "modules/chatgpt-web" not in nguon
    assert "modules/gemini-web" not in nguon
    assert "khoi_phuc_theo_tang" not in nguon
    assert "cong_mcp" not in nguon


def test_t0_phien_con_song(mod):
    mod._tai_khoan = [{"sso": SSO_GIA_OK, "x-userid": UID_GIA}]
    khoa = mod._khoa_tu_jar({"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq = chay(mod.khoi_phuc_tu_dong(khoa, EMAIL_GIA))
    assert kq.thanh_cong and kq.tang == "T0"


def test_t2_dang_nhap_lai_bang_mat_khau_da_luu(mod):
    mod._luu_mat_khau("tk-gia-1", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-1", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong and kq.tang in ("T2", "T3")


def test_t2_qua_factory_dang_ky_rieng_cua_module(mod):
    mod._luu_mat_khau("tk-gia-2", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    mod.dang_ky_trinh_duyet(lambda: td)
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-2", EMAIL_GIA))
    assert kq.thanh_cong


def test_trinh_duyet_none_la_ma_rieng_khong_phai_can_nguoi(mod):
    mod._luu_mat_khau("tk-gia-3", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-3", EMAIL_GIA))
    assert not kq.thanh_cong and kq.ma_loi == "trinh_duyet_none" and not kq.can_nguoi


def test_thieu_mat_khau_che_do_truc_tiep(mod):
    td = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-4", EMAIL_GIA, trinh_duyet=td))
    assert not kq.thanh_cong and kq.ma_loi == "thieu_mat_khau"


def test_2fa_co_totp_tu_dien_ma(mod):
    mod._luu_mat_khau("tk-gia-5", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    assert mod.luu_totp("tk-gia-5", EMAIL_GIA, TOTP_SECRET_GIA) is True
    td = _trinh_duyet(["2fa"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-5", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong
    assert ("dien", "ma_xac_minh") in td.hanh_dong


def test_2fa_khong_totp_dung_o_can_nguoi(mod):
    mod._luu_mat_khau("tk-gia-6", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["2fa"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-6", EMAIL_GIA, trinh_duyet=td))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"
    assert ("dien", "ma_xac_minh") not in td.hanh_dong


def test_captcha_truoc_khi_dien_va_hoan(mod):
    mod._luu_mat_khau("tk-gia-7", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td1 = _trinh_duyet(["captcha"], {})
    kq1 = chay(mod.khoi_phuc_tu_dong("tk-gia-7", EMAIL_GIA, trinh_duyet=td1))
    assert not kq1.thanh_cong and kq1.ma_loi == "thu_thach_truoc_khi_dien"
    mod._luu_mat_khau("tk-gia-8", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td2 = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq2 = chay(mod.khoi_phuc_tu_dong("tk-gia-8", EMAIL_GIA, trinh_duyet=td2))
    assert not kq2.thanh_cong and kq2.ma_loi == "captcha_hoan"
    mod.dat_lai_trang_thai_tu_dong()
    td3 = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq3 = chay(mod.khoi_phuc_tu_dong("tk-gia-8", EMAIL_GIA, trinh_duyet=td3))
    assert kq3.thanh_cong


def test_luu_totp_sai_khuon_bi_tu_choi(mod):
    assert mod.luu_totp("tk-gia-9", EMAIL_GIA, "khong-phai-base32!!!") is False
    assert mod.totp_da_luu("tk-gia-9") is None
    assert mod.totp_da_luu(EMAIL_GIA) is None


def test_che_do_google_khong_dien_totp_ho_so_chua_dang_nhap(mod):
    # Chế độ google chỉ tái dùng hồ sơ: trình duyệt giả không có tín hiệu
    # da_dang_nhap và không có cookie hồ sơ → thất bại, và tuyệt đối không
    # điền ma_xac_minh (tai_dung_ho_so không bao giờ dien()).
    mod._che_do_dang_nhap = "google"
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {"tin_hieu": [], "cookie": {}}})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-10", EMAIL_GIA, trinh_duyet=td))
    assert not kq.thanh_cong
    assert ("dien", "ma_xac_minh") not in td.hanh_dong


def test_captcha_chan_ca_chinh_khoa_vua_gap_captcha(mod):
    """Captcha là thử thách theo IP/trình duyệt: khoá vừa gặp nó KHÔNG được thử lại ngay (mỗi lần thử lại là thêm một lần gặp captcha)."""
    mod._luu_mat_khau("tk-gia-captcha", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td1 = _trinh_duyet(["captcha"], {})
    kq1 = chay(mod.khoi_phuc_tu_dong("tk-gia-captcha", EMAIL_GIA, trinh_duyet=td1))
    assert not kq1.thanh_cong and kq1.ma_loi in ("thu_thach_truoc_khi_dien", "captcha")
    td2 = _trinh_duyet(["thanh_cong"], {"sso": SSO_GIA_OK, "x-userid": UID_GIA})
    kq2 = chay(mod.khoi_phuc_tu_dong("tk-gia-captcha", EMAIL_GIA, trinh_duyet=td2))
    assert not kq2.thanh_cong and kq2.ma_loi == "captcha_hoan"
    assert not td2.hanh_dong, "trình duyệt không được chạm vào trong cửa sổ hoãn"
