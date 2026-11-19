"""NV309-gemini — chứng minh session đăng nhập cho gemini-web ở mức test.

Luật tuần tự 17:52: Grok và Claude đã xong ở mức test (NV309-a, NV309-claude),
giờ tới GEMINI. Không làm ChatGPT trong lượt này, không làm NV309-d.

Nền: web2api main 8ed35ab + patch NV309-a rồi NV309-c (áp sạch theo thứ
tự (a) → (c), đã kiểm; delta claude là nhánh riêng cho module claude-web,
KHÔNG áp chung). Bằng chứng xếp theo TỪNG TRƯỜNG HỢP (cách đăng nhập ×
kết quả) để báo cáo tổng hợp cuối cùng bao được toàn bộ provider ở từng
trường hợp:

1. Tái dùng hồ sơ Google đã đăng nhập → session (đường chính NV93: hồ sơ
   đã có phiên thì đi tắt, không bấm lại; cach_dang_nhap + ho_so do lõi
   truyền theo NV300).
2. Đường Google SSO bấm lại (hồ sơ chưa có phiên): bấm «Đăng nhập» → chọn
   tài khoản Google → chờ phiên hình thành → đọc session → trả session.
   Các bước popup/chọn tài khoản/đồng ý là SUY RA TỪ CODE (selector đo
   trên trang khách thật 18/10/2026 theo chú thích trong code) — chỉ kiểm
   thật mới chốt selector.
3. Challenge/CAPTCHA/Cloudflare → can_nguoi, dừng ngay, KHÔNG vượt, KHÔNG
   port solver captcha của c2a, gate giai_captcha giữ TẮT. Kiểm riêng
   Cloudflare trước khi điền (bài học từ delta Claude: tín hiệu can_nguoi
   phải đủ, không để lọt qua bước điền rồi mới hỏng).
4. Khóa serial/gap/hold Google của (c) trên đường gemini-web.
5. Vòng đời controller theo NV309-a.
6. Không phá đường gemini-web đã có (đăng nhập lại/tự làm mới phiên);
   không đổi giao diện trang tài khoản AI.

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

from app import khoa_dang_nhap_google as khoa_google
from app.khoi_phuc_phien import TrinhDuyetGia, lay_trinh_duyet_cho_tai_khoan, dong_workspace_moi
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach

from ho_tro_nv93 import TrinhDuyetChuyenDoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "gemini-web"
TEN_GOI = "gemini_web_nv309gemini_mod_test"

EMAIL_GIA = "ban-gia-nv309gemini@example.com"
HO_SO_GIA = "hs-gia-nv309gemini-0001"
PSID_GIA = "psid-gia-nv309gemini-0001"
PSIDTS_GIA = "psidts-gia-nv309gemini-0001"
CHU_GIU_GIA = "chu-giu-gia-nv309gemini"

TAI_DUNG_URL = "https://gemini.google.com/"
DANG_NHAP_URL = ("https://accounts.google.com/signin/v2/identifier"
                 "?flowName=GlifWebSignIn&flowEntry=ServiceLogin")


def _tai_goi():
    for ten in [t for t in list(sys.modules)
                if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("client", "schema_validator", "converter", "module"):
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


class KhachGia:
    """Client Gemini-web giả: init() + lay_model_kha_dung() đều thành công."""

    def __init__(self, *args, **kwargs):
        pass

    async def init(self):
        return None

    async def lay_model_kha_dung(self):
        return ["model-gia-nv309gemini"]


def _tao_client_thanh_cong(m):
    m._tao_client = lambda psid, psidts: KhachGia()


class TrinhDuyetGiaDong(TrinhDuyetGia):
    """TrinhDuyetGia + ghi nhật ký dong() (để chứng minh ai đóng)."""

    async def dong(self):
        self.hanh_dong.append(("dong",))


def _dong_mat_khau():
    return DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                      mat_khau=BiMatTam("mat-khau-gia"))


def _dong_google():
    dong = DongDaTach(dong=1, email=EMAIL_GIA, loai="google")
    dong.ho_so_google = HO_SO_GIA
    return dong


def _dong_mat_khau_cach_google():
    dong = DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                      mat_khau=BiMatTam("mat-khau-gia"))
    dong.cach_dang_nhap = "google"
    dong.ho_so_google = HO_SO_GIA
    return dong


def _td_tai_dung_phien():
    return TrinhDuyetGiaDong(trang={
        TAI_DUNG_URL: {"tin_hieu": [],
                       "cookie": {"__Secure-1PSID": PSID_GIA,
                                  "__Secure-1PSIDTS": PSIDTS_GIA}}})


def _td_dang_nhap_phien():
    return TrinhDuyetGiaDong(trang={
        DANG_NHAP_URL: {"tin_hieu": [],
                        "cookie": {"__Secure-1PSID": PSID_GIA,
                                   "__Secure-1PSIDTS": PSIDTS_GIA}}})


# ── Trường hợp 1: tái dùng hồ sơ Google → session ───────────────────────

def test_google_di_tat_tra_session():
    """Hồ sơ đã có phiên Google: đi tắt, không bấm, trả cookie phiên;
    chủ hold (nếu có) được gỡ sau khi thành công."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_tai_dung_phien()
    kq = chay(m.dang_nhap(_dong_google(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("__Secure-1PSID=" + PSID_GIA) in (kq.cookie or "")
    assert ("mo_trang", TAI_DUNG_URL) in td.hanh_dong
    assert not [a for a in td.hanh_dong if a[0] == "bam"]
    assert m._trinh_duyet_da_dung is td
    assert not khoa_google.dang_giu()


def test_mat_khau_cach_google_thanh_duong_tai_dung():
    """NV300: lần gọi mat_khau mang cach_dang_nhap='google' thì đổi sang
    đường tái dùng hồ sơ, không đòi điền mật khẩu."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_tai_dung_phien()
    kq = chay(m.dang_nhap(_dong_mat_khau_cach_google(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("__Secure-1PSID=" + PSID_GIA) in (kq.cookie or "")
    assert not [a for a in td.hanh_dong if a[0] == "dien"]


def test_khoi_phuc_t2_google_qua_controller_loi_cap():
    """T2 đường Google qua controller lõi cấp (chu_giu=email theo
    NV309-c): đi tắt thành công, tang T2."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_tai_dung_phien()
    kq = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, cach_dang_nhap="google",
        ho_so_google=HO_SO_GIA, trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert kq.tang == "T2"
    assert m._trinh_duyet_da_dung is td


# ── Trường hợp 2: Google SSO bấm lại ────────────────────────────────────

def test_google_bam_sso_tra_session():
    """Hồ sơ chưa có phiên: bấm «Đăng nhập» → chọn tài khoản Google →
    chờ phiên hình thành → đọc cookie → trả session.

    Lưu ý: các bước popup/chọn tài khoản/đồng ý của SSO thật là SUY RA
    TỪ CODE (selector _BO_CHON_DANG_NHAP/_BO_CHON_GOOGLE đo trên trang
    khách thật 18/10/2026 theo chú thích trong code); bộ điều khiển trừu
    tượng không có bước popup — luồng SSO thật có thể cần thêm bước mà
    chỉ kiểm thật mới thấy."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetChuyenDoi(
        trang={TAI_DUNG_URL: {"tin_hieu": [], "cookie": {}}},
        sau_bam={TAI_DUNG_URL: {"trang": {
            "tin_hieu": [],
            "cookie": {"__Secure-1PSID": PSID_GIA,
                       "__Secure-1PSIDTS": PSIDTS_GIA}}}})
    kq = chay(m.dang_nhap(_dong_google(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("__Secure-1PSID=" + PSID_GIA) in (kq.cookie or "")
    assert ("bam", m._BO_CHON_DANG_NHAP) in td.hanh_dong
    assert ("bam", m._BO_CHON_GOOGLE) in td.hanh_dong
    assert not [a for a in td.hanh_dong if a[0] == "dien"]


# ── Trường hợp 3: challenge/CAPTCHA/Cloudflare → can_nguoi ─────────────

def test_tai_dung_captcha_thi_can_nguoi_va_dat_hold():
    """CAPTCHA ở đường tái dùng hồ sơ: can_nguoi ngay, không bấm, đặt
    giữ captcha toàn cục cho đúng chủ hold."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={TAI_DUNG_URL: {"tin_hieu": ["captcha"]}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu=CHU_GIU_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert not [a for a in td.hanh_dong if a[0] == "bam"]
    assert khoa_google.dang_giu()
    assert khoa_google.thong_tin_giu()[1] == CHU_GIU_GIA


def test_tai_dung_cloudflare_thi_can_nguoi_va_dat_hold():
    """Cloudflare ở đường tái dùng hồ sơ: can_nguoi + đặt hold (cloudflare
    thuộc TIN_HIEU_CAPTCHA của khóa lõi, không chỉ riêng 'captcha')."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={TAI_DUNG_URL: {"tin_hieu": ["cloudflare"]}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu=CHU_GIU_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert khoa_google.dang_giu()
    assert khoa_google.thong_tin_giu()[1] == CHU_GIU_GIA


def test_tai_dung_2fa_thi_can_nguoi_khong_dat_hold():
    """2FA ở đường tái dùng hồ sơ: can_nguoi nhưng KHÔNG đặt hold toàn
    cục (2fa không thuộc TIN_HIEU_CAPTCHA — hold chỉ dành cho thử thách
    chống bot, không phải xác minh người dùng)."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = TrinhDuyetGiaDong(trang={TAI_DUNG_URL: {"tin_hieu": ["2fa"]}})
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu=CHU_GIU_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert not khoa_google.dang_giu()


def test_email_cloudflare_truoc_khi_dien_thi_thu_thach_dung_truoc_khi_dien():
    """Cloudflare xuất hiện TRƯỚC khi điền ở đường email: dừng trước khi
    điền, trả thu_thach_truoc_khi_dien, không gọi vuot_thu_thach."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(trang={DANG_NHAP_URL: {"tin_hieu": ["cloudflare"]}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not [a for a in td.hanh_dong if a[0] == "dien"]
    assert ("vuot_thu_thach",) not in td.hanh_dong


def test_email_thu_thach_truy_cap_truoc_khi_dien_thi_thu_thach():
    """Bài học từ delta Claude: thử thách truy cập xuất hiện TRƯỚC khi
    điền cũng phải dừng trước khi điền (tập tín hiệu dừng trước khi điền
    phải đủ, không để lọt qua bước điền rồi mới hỏng)."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(
        trang={DANG_NHAP_URL: {"tin_hieu": ["thu_thach_truy_cap"]}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not [a for a in td.hanh_dong if a[0] == "dien"]
    assert ("vuot_thu_thach",) not in td.hanh_dong


def test_email_xac_minh_thiet_bi_truoc_khi_dien_thi_thu_thach():
    """Xác minh thiết bị xuất hiện TRƯỚC khi điền cũng phải dừng trước
    khi điền (chú thích hàm vốn đã nói «xác minh thiết bị → dừng», nhưng
    kiểm thành viên tập hợp theo tên chính xác nên 'xac_minh_thiet_bi'
    không khớp 'xac_minh')."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(
        trang={DANG_NHAP_URL: {"tin_hieu": ["xac_minh_thiet_bi"]}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not [a for a in td.hanh_dong if a[0] == "dien"]


def test_duong_email_gemini_bi_hold_chan_truoc_khi_mo_trang():
    """Đăng nhập Google trực tiếp bằng email cũng phải qua hold toàn cục."""
    khoa_google.dat_giu_captcha("chu-giu-khac-gia")
    m = mod_moi()
    td = TrinhDuyetGiaDong(trang={DANG_NHAP_URL: {"tin_hieu": []}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == khoa_google.MA_LOI_GIU
    assert td.hanh_dong == []


def test_duong_email_gemini_cung_email_khac_ho_so_khong_go_hold():
    """Đăng nhập cùng email trong hồ sơ khác không được nhận quyền chủ hold."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    chu_cu = khoa_google.dinh_danh_chu_hold(EMAIL_GIA)
    khoa_google.dat_giu_captcha(chu_cu)
    td = _td_dang_nhap_phien()
    td.ho_so_id = "ho-so-b-gia"
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == khoa_google.MA_LOI_GIU
    assert td.hanh_dong == []
    assert khoa_google.thong_tin_giu()[1] == chu_cu


def test_hold_khong_dung_fallback_id_ho_so_lam_chu():
    """Hai hồ sơ cùng email là hai chủ hold khác nhau; hồ sơ kia không gỡ."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    chu_a = khoa_google.dinh_danh_chu_hold(EMAIL_GIA, "ho-so-a-gia")
    chu_b = khoa_google.dinh_danh_chu_hold(EMAIL_GIA, "ho-so-b-gia")
    assert chu_a != chu_b
    khoa_google.dat_giu_captcha(chu_a)
    td = _td_tai_dung_phien()
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu=chu_b))
    assert not kq.thanh_cong and kq.ma_loi == khoa_google.MA_LOI_GIU
    assert khoa_google.thong_tin_giu()[1] == chu_a


def test_email_thu_thach_sau_mat_khau_thi_thu_thach():
    """Thử thách truy cập xuất hiện SAU khi bấm đăng nhập (kiểm sau mật
    khẩu) cũng phải trả thu_thach_truoc_khi_dien, không trôi tới
    khong_lay_duoc_phien."""
    m = mod_moi()
    td = TrinhDuyetChuyenDoi(
        trang={DANG_NHAP_URL: {"tin_hieu": [], "cookie": {}}},
        sau_bam={DANG_NHAP_URL: {"trang": {
            "tin_hieu": ["thu_thach_truy_cap"], "cookie": {}}}})
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"


def test_khoi_phuc_thu_thach_thanh_can_nguoi_va_hoan():
    """Tầng khôi phục: thu_thach_truoc_khi_dien → can_nguoi=True + đặt
    hoãn captcha riêng của module; thử lại ngay → captcha_hoan."""
    m = mod_moi()
    td = TrinhDuyetGiaDong(trang={DANG_NHAP_URL: {"tin_hieu": ["cloudflare"]}})
    kq = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, mat_khau=BiMatTam("mat-khau-gia"),
        trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.tang == "T2"
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert kq.can_nguoi is True
    td2 = TrinhDuyetGiaDong(trang={DANG_NHAP_URL: {"tin_hieu": []}})
    kq2 = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, mat_khau=BiMatTam("mat-khau-gia"),
        trinh_duyet=td2))
    assert not kq2.thanh_cong
    assert kq2.ma_loi == "captcha_hoan"
    assert td2.hanh_dong == []


def test_khong_co_vuot_thu_thach_va_khong_solver_trong_module():
    """Tĩnh: module gemini-web không gọi vuot_thu_thach, không nhập
    solver captcha của c2a, không có gate giai_captcha — gặp thử thách
    chỉ có đường can_nguoi/thu_thach_truoc_khi_dien."""
    nguon = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert ".vuot_thu_thach(" not in nguon
    assert "vuot_thu_thach_toi_da(" not in nguon
    assert "2captcha" not in nguon
    assert "giai_captcha" not in nguon


# ── Trường hợp 4: khóa serial/gap/hold Google của (c) ───────────────────

def test_google_dang_giu_thi_luot_khac_bi_chan_khong_cham_trinh_duyet():
    """Đang giữ captcha toàn cục (chủ khác): lượt Google khác bị chặn
    bằng mã cho_captcha_toan_cuc, KHÔNG chạm tới trình duyệt."""
    khoa_google.dat_giu_captcha("chu-giu-khac-gia")
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_tai_dung_phien()
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu="chu-khac-gia"))
    assert not kq.thanh_cong
    assert kq.ma_loi == khoa_google.MA_LOI_GIU
    assert td.hanh_dong == []


def test_google_chu_hold_chay_lai_thanh_cong_thi_go_hold():
    """Chủ hold được chạy lại: đi tắt thành công → gỡ hold toàn cục."""
    khoa_google.dat_giu_captcha(CHU_GIU_GIA)
    assert khoa_google.dang_giu()
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_tai_dung_phien()
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, td, chu_giu=CHU_GIU_GIA))
    assert kq.thanh_cong, kq.ma_loi
    assert not khoa_google.dang_giu()


def test_google_hai_luot_tuan_tu_deu_xong():
    """Khóa serial nhả đúng sau mỗi lượt: hai lượt tái dùng tuần tự đều
    thành công (không kẹt khóa)."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    kq1 = chay(m.tai_dung_ho_so(HO_SO_GIA, _td_tai_dung_phien(),
                                chu_giu=CHU_GIU_GIA))
    assert kq1.thanh_cong, kq1.ma_loi
    kq2 = chay(m.tai_dung_ho_so(HO_SO_GIA, _td_tai_dung_phien(),
                                chu_giu=CHU_GIU_GIA))
    assert kq2.thanh_cong, kq2.ma_loi


# ── Trường hợp 5: vòng đời controller theo NV309-a ──────────────────────

def test_module_khong_tu_dong_controller():
    """Module không bao giờ tự đóng controller (dù thành công hay
    can_nguoi): đóng là việc của người gọi (lõi) theo luật NV309-a."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td_ok = _td_tai_dung_phien()
    kq = chay(m.dang_nhap(_dong_google(), trinh_duyet=td_ok))
    assert kq.thanh_cong
    assert ("dong",) not in td_ok.hanh_dong
    td_can = TrinhDuyetGiaDong(trang={TAI_DUNG_URL: {"tin_hieu": ["captcha"]}})
    kq2 = chay(m.dang_nhap(_dong_google(), trinh_duyet=td_can))
    assert not kq2.thanh_cong and kq2.ma_loi == "can_nguoi"
    assert ("dong",) not in td_can.hanh_dong


def test_tai_dung_khong_controller_thi_can_nguoi():
    """Fail-closed: tái dùng hồ sơ mà lõi không cấp controller → can_nguoi
    (điểm chặn mới của (c)), chưa từng mở trình duyệt."""
    m = mod_moi()
    kq = chay(m.tai_dung_ho_so(HO_SO_GIA, None, chu_giu=CHU_GIU_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_email_khong_controller_thi_trinh_duyet_none():
    """Fail-closed ở đường email: lõi không cấp controller và module
    không có factory nào → trinh_duyet_none (đối tác của SAI S1 — đường
    này chỉ chạy được khi lõi cấp controller)."""
    m = mod_moi()
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "trinh_duyet_none"


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


def test_vong_doi_khoi_phuc_phien_tai_dung_khong_tao_moi():
    """Cầu nối lõi: hồ sơ sẵn có → tái dùng (không tạo mới, không đóng
    sau lượt); không hồ sơ → tạo workspace mới; chỉ workspace mới tạo
    mới được đóng, và chỉ ở trạng thái cuối.

    Lưu ý khuôn: id hồ sơ lõi là hex 16–64 ký tự (MAU_HO_SO_TRINH_DUYET);
    «gemini-web-<hash>» của ho_so_trinh_duyet là id xem noVNC, không
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


# ── Trường hợp 6: không phá đường gemini-web đã có ─────────────────────

def test_email_truc_tiep_tra_session():
    """Đường email trực tiếp còn nguyên: điền email + mật khẩu → đọc
    cookie __Secure-1PSID → kiểm phiên bằng init() → trả cookie."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_dang_nhap_phien()
    kq = chay(m.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert ("__Secure-1PSID=" + PSID_GIA) in (kq.cookie or "")
    assert ("mo_trang", DANG_NHAP_URL) in td.hanh_dong
    assert ("dien", "email") in td.hanh_dong
    assert ("dien", "mat_khau") in td.hanh_dong
    assert ("bam", "dang_nhap") in td.hanh_dong
    assert m._trinh_duyet_da_dung is td


def test_khoi_phuc_t2_email_tra_thanh_cong():
    """T2 email còn nguyên: đăng nhập lại bằng mật khẩu đã truyền qua
    controller lõi cấp → tang T2."""
    m = mod_moi()
    _tao_client_thanh_cong(m)
    td = _td_dang_nhap_phien()
    kq = chay(m.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, mat_khau=BiMatTam("mat-khau-gia"),
        trinh_duyet=td))
    assert kq.thanh_cong, kq.ma_loi
    assert kq.tang == "T2"


def test_khoi_phuc_t2_email_khong_mat_khau_thi_thieu():
    """T2 email không có mật khẩu (không truyền, kho riêng rỗng) →
    thieu_mat_khau, không vỡ, không tự bịa mật khẩu."""
    m = mod_moi()
    td = _td_dang_nhap_phien()
    kq = chay(m.khoi_phuc_tu_dong("", email=EMAIL_GIA, trinh_duyet=td))
    assert not kq.thanh_cong
    assert kq.tang == "T2"
    assert kq.ma_loi == "thieu_mat_khau"
