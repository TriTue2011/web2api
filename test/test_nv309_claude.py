"""NV309-claude — chứng minh session đăng nhập cho claude-web ở mức test.

Luật tuần tự 17:52: Grok đã xong ở mức test (NV309-a), giờ tới CLAUDE.
Không làm ChatGPT/Gemini trong lượt này, không làm NV309-d.

Nền: web2api main 8ed35ab + patch NV309-a rồi NV309-c (áp sạch theo thứ
tự (a) → (c), đã kiểm). Bằng chứng xếp theo TỪNG TRƯỜNG HỢP (cách đăng
nhập × kết quả) để báo cáo tổng hợp cuối cùng bao được toàn bộ provider
ở từng trường hợp:

1. Email-direct → cookie/session (đường trực tiếp qua controller lõi cấp).
2. Google tái dùng hồ sơ → session, qua khóa serial/gap/hold lõi của (c).
3. Challenge/CAPTCHA → can_nguoi, dừng, KHÔNG vượt, KHÔNG port solver
   captcha của c2a, gate giai_captcha giữ TẮT.
4. Vòng đời controller theo NV309-a (workspace mới chỉ đóng ở trạng thái
   cuối, KHÔNG đóng khi can_nguoi, KHÔNG BAO GIỜ đóng workspace sẵn có;
   hồ sơ tái dùng thì không tạo mới, không đóng sau lượt).
5. Không phá đường Claude email + mã một lần đã có (NV271).

Trình duyệt GIẢ, máy chủ HTTP GIẢ, tài khoản GIẢ. Không Internet, không
Chrome, không tài khoản thật, không đăng nhập thật. Mọi chuỗi bí mật
trong file đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from app import khoa_dang_nhap_serial as khoa_google
from app.khoi_phuc_phien import TrinhDuyetGia, lay_trinh_duyet_cho_tai_khoan, dong_workspace_moi
from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach

from ho_tro_nv93 import TrinhDuyetChuyenDoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "claude-web"
TEN_GOI = "claude_web_nv309claude_mod_test"

EMAIL_GIA = "ban-gia-nv309claude@example.com"
HO_SO_GIA = "hs-gia-nv309claude-0001"
SESSION_KEY_GIA = "phien-gia-nv309claude-0001"
ORG_GIA = "org-gia-nv309claude-0001"
TOTP_SECRET_GIA = "AAAAAAAAAAAAAAAA"  # base32 toàn ký tự A — rõ ràng là giả

LOGIN = "https://claude.ai/login"
GOC_URL = "https://claude.ai/"


def _tai_goi():
    for ten in [t for t in list(sys.modules)
                if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
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


@pytest.fixture(autouse=True)
def _dat_lai_khoa_google():
    khoa_google.dat_lai()
    yield
    khoa_google.dat_lai()


def mod_moi():
    return Module(dict(MANIFEST))


class MayKhachGia:
    """MayKhachClaudeWeb giả: lay_org_id thành công (mặc định)."""

    def __init__(self, cookie, base_url):
        self.cookie = cookie
        self.base_url = base_url

    async def lay_org_id(self):
        return ORG_GIA

    async def dong(self):
        return None


class KhachOrgGia:
    """Client kiểm phiên giả cho đường Google (_tao_client)."""

    def __init__(self):
        pass

    async def lay_org(self):
        return ORG_GIA

    async def dong(self):
        return None


class TrinhDuyetGiaDong(TrinhDuyetGia):
    """TrinhDuyetGia + ghi nhật ký dong() (để chứng minh ai đóng)."""

    async def dong(self):
        self.hanh_dong.append(("dong",))


def _may_khach_thanh_cong(monkeypatch):
    monkeypatch.setattr(mod_module, "MayKhachClaudeWeb", MayKhachGia)


def _tao_client_thanh_cong(m):
    m._tao_client = lambda session_key: KhachOrgGia()


def _dong_mat_khau():
    return DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                      mat_khau=BiMatTam("mat-khau-gia"))


def _dong_google():
    dong = DongDaTach(dong=1, email=EMAIL_GIA, loai="google")
    dong.ho_so_google = HO_SO_GIA
    return dong


def _td_login_phien():
    return TrinhDuyetGiaDong(trang={
        LOGIN: {"tin_hieu": [], "cookie": {"sessionKey": SESSION_KEY_GIA}}})


# ── Trường hợp 1: email-direct → cookie/session ────────────────────────

def test_email_direct_qua_controller_loi_cap_tra_session(monkeypatch):
    """Đường trực tiếp claude-web qua controller lõi cấp: điền email +
    mật khẩu → đọc sessionKey → kiểm phiên → trả cookie phiên."""
    _may_khach_thanh_cong(monkeypatch)
    m = mod_moi()
    td = _td_login_phien()
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert "sessionKey=" + SESSION_KEY_GIA in (kq.cookie or "")
    assert ("mo_trang", LOGIN) in td.hanh_dong
    assert ("dien", "email") in td.hanh_dong
    assert ("dien", "mat_khau") in td.hanh_dong
    assert ("bam", "dang_nhap") in td.hanh_dong
    assert m._trinh_duyet_da_dung is td


def test_email_direct_khong_co_controller_thi_can_nguoi():
    """Fail-closed khi lõi không cấp controller và module không có
    factory nào: chưa từng mở trình duyệt đã trả can_nguoi (đối tác
    của SAI S1 — đường này chỉ chạy được khi lõi cấp controller)."""
    m = mod_moi()
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_email_direct_challenge_cloudflare_thi_can_nguoi():
    """Thử thách Cloudflare trước khi điền → can_nguoi, dừng trước khi
    điền, không gọi vuot_thu_thach, không cố vượt."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(trang={LOGIN: {"tin_hieu": ["cloudflare"]}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert not [a for a in td.hanh_dong if a[0] == "dien"]
    assert ("vuot_thu_thach",) not in td.hanh_dong


# ── Trường hợp 2: Google tái dùng hồ sơ → session (qua khóa lõi) ───────

def test_google_di_tat_tra_session(monkeypatch):
    """Hồ sơ đã có phiên claude.ai: đi tắt, không bấm Google, trả
    session; chủ hold (nếu có) được gỡ sau khi thành công."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={
        GOC_URL: {"tin_hieu": ["da_dang_nhap"],
                  "cookie": {"sessionKey": SESSION_KEY_GIA}}})
    kq = chay(m.dang_nhap(_dong_google(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert "sessionKey=" + SESSION_KEY_GIA in (kq.cookie or "")
    assert ("mo_trang", GOC_URL) in td.hanh_dong
    assert not [a for a in td.hanh_dong if a[0] == "bam"]
    assert not khoa_google.dang_giu()


def test_google_bam_continue_tra_session(monkeypatch):
    """Hồ sơ chưa có phiên: bấm «Continue with Google» → chờ phiên hình
    thành sau SSO → đọc sessionKey → trả session."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetChuyenDoi(
        trang={GOC_URL: {"tin_hieu": [], "cookie": {}},
               LOGIN: {"tin_hieu": [], "cookie": {}}},
        sau_bam={LOGIN: {"trang": {"tin_hieu": ["da_dang_nhap"],
                                   "cookie": {"sessionKey": SESSION_KEY_GIA}}}})
    kq = chay(m.dang_nhap(_dong_google(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert "sessionKey=" + SESSION_KEY_GIA in (kq.cookie or "")
    assert ("bam", m._BO_CHON_GOOGLE) in td.hanh_dong


def test_google_dang_giu_thi_luot_khac_bi_chan_khong_cham_trinh_duyet(monkeypatch):
    """Đang giữ captcha toàn cục (chủ khác): lượt Google khác bị chặn
    bằng mã cho_captcha_toan_cuc, KHÔNG chạm tới trình duyệt."""
    khoa_google.dat_giu_captcha("chu-giu-gia")
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={
        GOC_URL: {"tin_hieu": ["da_dang_nhap"],
                  "cookie": {"sessionKey": SESSION_KEY_GIA}}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu="chu-khac-gia"))
    assert not kq.thanh_cong
    assert kq.ma_loi == khoa_google.MA_LOI_GIU
    assert td.hanh_dong == []


def test_google_chu_hold_chay_lai_thanh_cong_thi_go_hold(monkeypatch):
    """Chủ hold được chạy lại: đi tắt thành công → gỡ hold toàn cục."""
    khoa_google.dat_giu_captcha("chu-giu-gia")
    assert khoa_google.dang_giu()
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={
        GOC_URL: {"tin_hieu": ["da_dang_nhap"],
                  "cookie": {"sessionKey": SESSION_KEY_GIA}}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu="chu-giu-gia"))
    assert kq.thanh_cong, kq.ma_loi
    assert not khoa_google.dang_giu()


# ── Trường hợp 3: challenge/CAPTCHA → can_nguoi, dừng, không vượt ──────

def test_email_captcha_truoc_khi_dien_thi_can_nguoi_dung_truoc_khi_dien():
    """CAPTCHA trước khi điền ở đường email: can_nguoi, dừng trước khi
    điền, không gọi vuot_thu_thach."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(trang={LOGIN: {"tin_hieu": ["captcha"]}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert not [a for a in td.hanh_dong if a[0] == "dien"]
    assert ("vuot_thu_thach",) not in td.hanh_dong


def test_google_captcha_thi_can_nguoi_va_dat_hold(monkeypatch):
    """CAPTCHA ở đường Google: can_nguoi ngay + đặt giữ captcha toàn
    cục (lượt Google khác sẽ bị chặn cho tới khi chủ hold chạy lại)."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={GOC_URL: {"tin_hieu": ["captcha"]}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu="chu-giu-gia"))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert not [a for a in td.hanh_dong if a[0] == "bam"]
    assert khoa_google.dang_giu()
    assert khoa_google.thong_tin_giu()[1] == "chu-giu-gia"


def test_khong_co_vuot_thu_thach_va_khong_solver_trong_module():
    """Tĩnh: module claude-web không gọi vuot_thu_thach, không gọi
    helper vượt, không nhập solver captcha của c2a (gate giai_captcha
    giữ TẮT — gặp thử thách chỉ có đường can_nguoi)."""
    nguon = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert ".vuot_thu_thach(" not in nguon
    assert "vuot_thu_thach_toi_da(" not in nguon
    assert "2captcha" not in nguon


# ── Trường hợp 4: vòng đời controller theo NV309-a ──────────────────────

class ModTrinhDuyetGia:
    """Module trình duyệt giả cho cầu nối lõi (mẫu NV309-a)."""

    def __init__(self, td):
        self.manifest = {"cung_cap": ["trinh_duyet"]}
        self._td = td
        self.da_tao: list[str] = []
        self.da_dong: list = []

    def trinh_duyet_that(self, ho_so):
        return self._td

    async def trinh_duyet_tao(self, ten):
        self.da_tao.append(ten)
        # Id hồ sơ lõi phải đúng khuôn hex (MAU_HO_SO_TRINH_DUYET).
        return {"ho_so": "fedcba9876543210", "ten": ten}

    async def trinh_duyet_dong(self, ho_so, giu_phien):
        self.da_dong.append((ho_so, giu_phien))
        return {}


def test_module_khong_tu_dong_controller(monkeypatch):
    """Module không bao giờ tự đóng controller (dù thành công hay
    can_nguoi): đóng là việc của người gọi (lõi) theo luật NV309-a."""
    _may_khach_thanh_cong(monkeypatch)
    m = mod_moi()
    td_thanh_cong = _td_login_phien()
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td_thanh_cong))
    assert kq.thanh_cong
    assert ("dong",) not in td_thanh_cong.hanh_dong
    td_can_nguoi = TrinhDuyetGiaDong(trang={LOGIN: {"tin_hieu": ["captcha"]}})
    kq2 = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td_can_nguoi))
    assert not kq2.thanh_cong and kq2.ma_loi == "can_nguoi"
    assert ("dong",) not in td_can_nguoi.hanh_dong


def test_vong_doi_khoi_phuc_phien_tai_dung_khong_tao_moi():
    """Cầu nối lõi: hồ sơ sẵn có → tái dùng (không tạo mới, không
    đóng sau lượt); không hồ sơ → tạo workspace mới; chỉ workspace
    mới tạo mới được đóng, và chỉ ở trạng thái cuối.

    Lưu ý khuôn: id hồ sơ lõi là hex 16–64 ký tự (MAU_HO_SO_TRINH_DUYET);
    «claude-web-<hash>» của ho_so_trinh_duyet là id xem noVNC, không
    phải id hồ sơ lõi, nên test này dùng id hồ sơ dạng hex."""
    hs_ho_so = "0123456789abcdef"
    td = TrinhDuyetGiaDong(trang={})
    mod_td = ModTrinhDuyetGia(td)
    dang_chay = {"trinh-duyet-gia": mod_td}
    td1, hs1, da_tao1 = chay(lay_trinh_duyet_cho_tai_khoan(
        dang_chay, ho_so=hs_ho_so, email=EMAIL_GIA))
    assert td1 is td and hs1 == hs_ho_so and da_tao1 is False
    td2, hs2, da_tao2 = chay(lay_trinh_duyet_cho_tai_khoan(
        dang_chay, ho_so="", email=EMAIL_GIA))
    assert td2 is td and da_tao2 is True
    chay(dong_workspace_moi(dang_chay, hs2))
    assert mod_td.da_dong == [(hs2, False)]
    assert all(hs != hs_ho_so for hs, _ in mod_td.da_dong)


# ── Trường hợp 5: không phá đường email + mã một lần (NV271) ───────────

def test_khoi_phuc_t2_email_otp_tra_thanh_cong(monkeypatch):
    """NV271 còn nguyên: T2 email + TOTP đã lưu qua controller lõi cấp
    — sau mật khẩu hiện 2FA thì điền mã một lần rồi đọc session."""
    _may_khach_thanh_cong(monkeypatch)
    m = mod_moi()
    td = TrinhDuyetChuyenDoi(
        trang={LOGIN: {"tin_hieu": [], "cookie": {}}},
        sau_bam={LOGIN: {"trang": {"tin_hieu": ["2fa"],
                                   "cookie": {"sessionKey": SESSION_KEY_GIA}}}})
    kq = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, mat_khau=BiMatTam("mat-khau-gia"),
        totp=BiMatTotp(TOTP_SECRET_GIA), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert kq.tang == "T2"
    assert ("dien", "ma_xac_minh") in td.hanh_dong
    assert ("bam", "xac_nhan_2fa") in td.hanh_dong


def test_khoi_phuc_t2_google_qua_controller_loi_cap(monkeypatch):
    """T2 đường Google qua controller lõi cấp (chu_giu=email theo
    NV309-c): đi tắt thành công, tang T2."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={
        GOC_URL: {"tin_hieu": ["da_dang_nhap"],
                  "cookie": {"sessionKey": SESSION_KEY_GIA}}})
    kq = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, cach_dang_nhap="google",
        ho_so_google=HO_SO_GIA, trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert kq.tang == "T2"
    assert m._trinh_duyet_da_dung is td
