"""Test RIÊNG cho module claude-web: luồng đăng nhập tự động độc lập trong module.

Module này tự chứa T0-T3, kênh TOTP, khóa serial, hoãn captcha, mã lỗi riêng
— không import logic của module khác, không dùng logic khôi phục của lõi làm
logic chính. Trình duyệt là GIẢ, máy chủ là GIẢ, không Internet, không Chrome.
Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.

Lưu ý: test này chỉ kiểm thang + kênh TOTP + mã lỗi bằng mock. Nó KHÔNG
chứng minh SSO Google thật trên claude.ai (xem báo cáo: bước popup OAuth/
chọn tài khoản của chatgpt2api không có trong giao diện trình duyệt trừu
tượng của web2api).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import kho
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "claude-web"
TEN_GOI = "claude_web_module_tu_dong_test"

COOKIE_GIA_OK = "sessionKey=sessionKey-gia-claude-ok"
PHIEN_MOI = "sessionKey-gia-claude-ok"
MAT_KHAU_GIA = "mat-khau-gia-claude-001"
EMAIL_GIA = "ban-gia-claude@example.com"
TOTP_SECRET_GIA = "JBSWY3DPEHPK3PXP"
URL_DANG_NHAP = "https://claude.ai/login"


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
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


def _may_chu_gia():
    def xu_ly(req):
        cookie = req.headers.get("cookie", "")
        if req.url.path == "/api/organizations":
            if "sessionKey-gia-claude-ok" in cookie:
                return httpx.Response(200, json=[{"uuid": "org-gia-1",
                                                  "capabilities": ["chat"]}])
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")
    return httpx.MockTransport(xu_ly)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("claude-web")
    transport = _may_chu_gia()

    def khach_http(self):
        return httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(mod_client.MayKhachClaudeWeb, "_khach_http", khach_http)
    return m


def _trinh_duyet(tin_hieu, phien=PHIEN_MOI):
    return TrinhDuyetGia(trang={
        URL_DANG_NHAP: {"tin_hieu": tin_hieu, "cookie": {"sessionKey": phien}}})


def test_doc_lap_khong_import_module_kia():
    nguon = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert "modules/gemini-web" not in nguon
    assert "modules/chatgpt-web" not in nguon
    assert "khoi_phuc_theo_tang" not in nguon
    assert "cong_mcp" not in nguon


def test_t0_phien_con_song(mod):
    tk = DongDaTach(dong=1, email=EMAIL_GIA, loai="cookie", cookie=BiMatTam(COOKIE_GIA_OK))
    assert chay(mod.dang_nhap(tk)).thanh_cong
    khoa = Module._khoa_tu_cookie(COOKIE_GIA_OK)
    kq = chay(mod.khoi_phuc_tu_dong(khoa, EMAIL_GIA))
    assert kq.thanh_cong and kq.tang == "T0"


def test_t1_nghi_roi_kiem_lai_len(mod):
    lan = {"n": 0}

    async def lam_moi_gia(khoa):
        lan["n"] += 1
        if lan["n"] == 1:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han")
        return KetQuaKhoiPhuc(True, tang="a")

    mod.lam_moi_phien = lam_moi_gia
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-t1", EMAIL_GIA, nghi_t1_giay=0.01))
    assert kq.thanh_cong and kq.tang == "T1"


def test_t2_dang_nhap_lai_bang_mat_khau_da_luu(mod):
    mod._luu_mat_khau("tk-gia-1", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"])
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-1", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong and kq.tang in ("T2", "T3")


def test_t2_qua_factory_dang_ky_rieng_cua_module(mod):
    mod._luu_mat_khau("tk-gia-2", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"])
    mod.dang_ky_trinh_duyet(lambda: td)
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-2", EMAIL_GIA))
    assert kq.thanh_cong


def test_trinh_duyet_none_la_ma_rieng_khong_phai_can_nguoi(mod):
    mod._luu_mat_khau("tk-gia-3", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-3", EMAIL_GIA))
    assert not kq.thanh_cong and kq.ma_loi == "trinh_duyet_none" and not kq.can_nguoi


def test_thieu_mat_khau(mod):
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-0", EMAIL_GIA))
    assert not kq.thanh_cong and kq.ma_loi == "thieu_mat_khau"


def test_2fa_co_totp_tu_dien_ma(mod):
    mod._luu_mat_khau("tk-gia-4", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    assert mod.luu_totp("tk-gia-4", EMAIL_GIA, TOTP_SECRET_GIA) is True
    td = _trinh_duyet(["2fa"])
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-4", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong
    assert ("dien", "ma_xac_minh") in td.hanh_dong


def test_2fa_khong_totp_dung_o_thu_thach(mod):
    mod._luu_mat_khau("tk-gia-5", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["2fa"])
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-5", EMAIL_GIA, trinh_duyet=td))
    assert not kq.thanh_cong and kq.ma_loi == "thu_thach_truoc_khi_dien" and kq.can_nguoi
    assert ("dien", "ma_xac_minh") not in td.hanh_dong


def test_captcha_hoan_va_dat_lai(mod):
    mod._luu_mat_khau("tk-gia-6", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td1 = _trinh_duyet(["captcha"], phien="")
    kq1 = chay(mod.khoi_phuc_tu_dong("tk-gia-6", EMAIL_GIA, trinh_duyet=td1))
    assert not kq1.thanh_cong and kq1.ma_loi == "thu_thach_truoc_khi_dien"
    mod._luu_mat_khau("tk-gia-7", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td2 = _trinh_duyet(["thanh_cong"])
    kq2 = chay(mod.khoi_phuc_tu_dong("tk-gia-7", EMAIL_GIA, trinh_duyet=td2))
    assert not kq2.thanh_cong and kq2.ma_loi == "captcha_hoan"
    mod.dat_lai_trang_thai_tu_dong()
    td3 = _trinh_duyet(["thanh_cong"])
    kq3 = chay(mod.khoi_phuc_tu_dong("tk-gia-7", EMAIL_GIA, trinh_duyet=td3))
    assert kq3.thanh_cong


def test_luu_totp_sai_khuon_bi_tu_choi(mod):
    assert mod.luu_totp("tk-gia-8", EMAIL_GIA, "khong-phai-base32!!!") is False
    assert mod.totp_da_luu("tk-gia-8") is None


def test_duong_cu_can_nguoi_khi_khong_trinh_duyet(mod):
    kq = chay(mod._dang_nhap_bang_trinh_duyet(EMAIL_GIA, BiMatTam(MAT_KHAU_GIA), None))
    assert not kq.thanh_cong and kq.ma_loi == "can_nguoi"


def test_captcha_chan_ca_chinh_khoa_vua_gap_captcha(mod):
    """Captcha là thử thách theo IP/trình duyệt: khoá vừa gặp nó KHÔNG được thử lại ngay (mỗi lần thử lại là thêm một lần gặp captcha)."""
    mod._luu_mat_khau("tk-gia-captcha", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td1 = _trinh_duyet(["captcha"], phien="")
    kq1 = chay(mod.khoi_phuc_tu_dong("tk-gia-captcha", EMAIL_GIA, trinh_duyet=td1))
    assert not kq1.thanh_cong and kq1.ma_loi in ("thu_thach_truoc_khi_dien", "captcha")
    td2 = _trinh_duyet(["thanh_cong"])
    kq2 = chay(mod.khoi_phuc_tu_dong("tk-gia-captcha", EMAIL_GIA, trinh_duyet=td2))
    assert not kq2.thanh_cong and kq2.ma_loi == "captcha_hoan"
    assert not td2.hanh_dong, "trình duyệt không được chạm vào trong cửa sổ hoãn"
