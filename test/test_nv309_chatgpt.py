"""NV309-chatgpt — chứng minh session đăng nhập cho chatgpt-web ở mức test.

Luật tuần tự 17:52: Grok xong ở mức test (NV309-a), Claude xong ở mức test
(muse/nv309-claude), giờ tới CHATGPT. Không làm Gemini trong lượt này,
không làm NV309-d, không đổi giao diện trang tài khoản AI.

Nền: web2api main 8ed35ab + patch NV309-a rồi NV309-c (áp sạch theo thứ tự
(a) → (c), đã kiểm). Bằng chứng xếp theo TỪNG TRƯỜNG HỢP (cách đăng nhập ×
kết quả) để báo cáo tổng hợp cuối cùng bao được toàn bộ provider ở từng
trường hợp:

1. Email-direct → session/cookie (mở trang đăng nhập → điền email + mật khẩu
   → bấm nút đăng nhập → đọc cookie phiên → kiểm phiên → trả session).
   Ô/nút theo code module: khóa logic "email", "mat_khau", "dang_nhap" —
   controller thật mới map các khóa này sang ô thật, nên selector ô thật
   CHƯA được xác nhận bằng tay (xem báo cáo).
2. Email-direct không controller → fail-closed (trinh_duyet_none, chưa từng
   mở trình duyệt).
3. Challenge/CAPTCHA/Cloudflare trước khi điền → thu_thach_truoc_khi_dien,
   dừng trước khi điền, KHÔNG gọi solver, không có gate giai_captcha nào
   được bật (module này không có đường gọi solver).
   BÀI HỌC TỪ CLAUDE: tập tín hiệu dừng trước khi điền của đường email vốn
   chỉ có 3 (captcha, xac_minh, cloudflare) — thiếu "xac_minh_thiet_bi" và
   "thu_thach_truy_cap" dù cả hai đều nằm trong _TIN_HIEU_CAN_NGUOI của
   chính module (đường Google). Hai tín hiệu đó lọt qua bước điền rồi mới
   hỏng ở khong_lay_duoc_phien (hoặc thành công giả qua controller giả).
   Ngoại lệ có chủ đích: "2fa" KHÔNG đưa vào tập dừng blanket — 2FA có
   kênh TOTP riêng của module (_xu_ly_2fa): có TOTP thì điền mã tiếp, chỉ
   dừng khi không có TOTP. Đưa 2fa vào tập dừng sẽ phá luồng TOTP này.
4. Google tái dùng hồ sơ đi tắt → session (hồ sơ đã có phiên thì đọc cookie
   phiên, không bấm Google).
5. Google SSO bấm lại khi hồ sơ chưa có phiên → session. Nút theo code:
   _BO_CHON_DANG_NHAP (nút «Log in») rồi _BO_CHON_GOOGLE (nút «Continue
   with Google», đo trên trang thật 18/10/2026 nhưng có dự phòng theo vị
   trí cấu trúc). Các bước popup OAuth / chọn tài khoản Google / đồng ý
   KHÔNG có trong bộ điều khiển trừu tượng — luồng thật có thể cần thêm
   bước chỉ kiểm thật mới thấy (xem báo cáo).
6. Khóa serial/gap/hold Google của (c) trên đường chatgpt-web: đang giữ
   captcha → lượt khác bị chặn cho_captcha_toan_cuc, không chạm trình
   duyệt; chủ hold chạy lại thành công → gỡ hold; CAPTCHA/Cloudflare →
   can_nguoi + đặt hold đúng chủ. Gap test = 0 nhờ env trong conftest;
   production mặc định 25 giây KHÔNG đổi.
7. Vòng đời controller theo (a): module không tự đóng controller dù thành
   công hay can_nguoi (đóng là việc của lõi/người gọi); hồ sơ sẵn → tái
   dùng (da_tao=False); không hồ sơ → tạo workspace mới (da_tao=True);
   dong_workspace_moi chỉ đóng workspace mới tạo, chỉ ở trạng thái cuối,
   không đóng khi can_nguoi, không bao giờ đóng workspace sẵn có.
8. Không phá đường chatgpt-web đã có: đăng nhập lại / tự làm mới phiên
   (khoi_phuc T0–T3) vẫn chạy; tập liên quan xanh, không hồi quy so với
   nền (a)+(c).

Trình duyệt GIẢ, máy chủ HTTP GIẢ, tài khoản GIẢ. Không Internet, không
Chrome, không tài khoản thật, không đăng nhập thật. Mọi chuỗi bí mật trong
file đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from app import khoa_dang_nhap_serial as khoa_google
from app import kho
from app.khoi_phuc_phien import (
    TrinhDuyetGia,
    dong_workspace_moi,
    lay_trinh_duyet_cho_tai_khoan,
)
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap

from ho_tro_nv93 import TrinhDuyetChuyenDoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv309chatgpt_mod_test"

TOKEN_GIA = "token-gia-nv309chatgpt-0001"
EMAIL_GIA = "ban-gia-nv309chatgpt@example.com"
HO_SO_GIA = "hs-gia-nv309chatgpt-0001"
CHU_KHAC = "chu-khac-nv309chatgpt@example.com"

LOGIN = "https://chatgpt.com/auth/login"
GOC_URL = "https://chatgpt.com/"


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
    return (sys.modules[f"{TEN_GOI}.module"],
            sys.modules[f"{TEN_GOI}.client"])


mod_module, mod_client = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _dat_lai_khoa_google():
    khoa_google.dat_lai()
    yield
    khoa_google.dat_lai()


def _may_chu_gia():
    """Máy chủ giả: /backend-api/me, /backend-api/accounts/check."""

    def xu_ly(req):
        auth = req.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if req.url.path == "/backend-api/me":
            if token == TOKEN_GIA:
                return httpx.Response(200, json={"email": EMAIL_GIA})
            return httpx.Response(401, text="het han")
        if req.url.path == "/backend-api/accounts/check":
            if token == TOKEN_GIA:
                return httpx.Response(200, json={
                    "accounts": {"default": {"plan_type": "plus"}}})
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("chatgpt-web")
    transport = _may_chu_gia()

    def tao_client(token):
        return mod_client.ChatGPTWebClient(
            token, base_url="http://127.0.0.1:9",
            transport=transport)

    m._tao_client = tao_client
    return m


def _dong_mat_khau():
    return DongDaTach(dong=1, email=EMAIL_GIA, loai="mat_khau",
                      mat_khau=BiMatTam("mat-khau-gia"))


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


def _trang_login(tin_hieu, cookie=None):
    return {LOGIN: {"tin_hieu": list(tin_hieu),
                    "cookie": dict(cookie or {})}}


class TrinhDuyetGiaDong(TrinhDuyetGia):
    """TrinhDuyetGia + ghi nhật ký dong() (để chứng minh ai đóng)."""

    async def dong(self):
        self.hanh_dong.append(("dong",))


class TotpGia:
    """TOTP giả: lay() trả mã cố định, xoa() là no-op."""

    def lay(self):
        return "123456"

    def xoa(self):
        return None


class TinHieuSauDien(TrinhDuyetGia):
    """TrinhDuyetGia + thử thách xuất hiện SAU khi điền (mô phỏng trang đổi
    trạng thái sau khi bấm nút đăng nhập)."""

    def __init__(self, trang, tin_hieu_sau):
        super().__init__(trang)
        self._tin_hieu_sau = set(tin_hieu_sau)

    async def tin_hieu(self, quy_tac=None):
        base = await super().tin_hieu(quy_tac)
        if any(h[0] == "dien" for h in self.hanh_dong):
            return set(base) | self._tin_hieu_sau
        return base


# ── Trường hợp 1: email-direct → session ────────────────────────────────

def test_email_direct_thanh_cong(mod):
    """Mở trang đăng nhập → điền email + mật khẩu → bấm đăng nhập → đọc
    cookie phiên → kiểm phiên → trả session."""
    td = TrinhDuyetGiaDong(_trang_login([], {"access_token": TOKEN_GIA}))
    kq = chay(mod.dang_nhap(_dong_mat_khau(), trinh_duyet=td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    buoc = [(h[0], h[1]) for h in td.hanh_dong]
    assert buoc[0] == ("mo_trang", LOGIN)
    assert ("dien", "email") in buoc
    assert ("dien", "mat_khau") in buoc
    assert ("bam", "dang_nhap") in buoc
    # Thứ tự: điền email → điền mật khẩu → bấm đăng nhập.
    assert (buoc.index(("dien", "email"))
            < buoc.index(("dien", "mat_khau"))
            < buoc.index(("bam", "dang_nhap")))
    assert ("dong",) not in td.hanh_dong  # module không tự đóng controller
    assert ("vuot_thu_thach",) not in td.hanh_dong  # không gọi solver
    ds = mod.liet_ke_tai_khoan()
    assert any(d["email"] == EMAIL_GIA for d in ds)


def test_email_direct_qua_khoi_phuc_t2(mod):
    """NV271 còn nguyên ở tầng T2: khoi_phuc_tu_dong đường email qua
    controller lõi cấp → thành công ở T2 (khoa rỗng nên T0/T1/T3 bỏ qua)."""
    td = TrinhDuyetGiaDong(_trang_login([], {"access_token": TOKEN_GIA}))
    kq = chay(mod.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, mat_khau=BiMatTam("mat-khau-gia"),
        trinh_duyet=td, cach_dang_nhap="email"))
    assert kq.thanh_cong and kq.tang == "T2", kq.ma_loi
    assert ("dien", "email") in [(h[0], h[1]) for h in td.hanh_dong]


def test_email_direct_2fa_voi_totp(mod):
    """Sau mật khẩu hiện 2FA mà có TOTP → điền mã một lần rồi đọc session
    (kênh TOTP riêng của module, không phải can_nguoi)."""
    td = TrinhDuyetGiaDong(_trang_login(["2fa"], {"access_token": TOKEN_GIA}))
    kq = chay(mod._dang_nhap_bang_trinh_duyet(
        EMAIL_GIA, BiMatTam("mat-khau-gia"), td, totp=TotpGia()))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    buoc = [(h[0], h[1]) for h in td.hanh_dong]
    assert ("dien", "ma_xac_minh") in buoc
    assert ("bam", "xac_nhan_2fa") in buoc


def test_email_direct_2fa_khong_totp_thi_dung_truoc_khi_dien(mod):
    """Hiện 2FA từ đầu mà không có TOTP (cũng không có TOTP đã lưu) →
    dừng trước khi điền, không điền email/mật khẩu."""
    td = TrinhDuyetGiaDong(_trang_login(["2fa"], {}))
    kq = chay(mod._dang_nhap_bang_trinh_duyet(
        EMAIL_GIA, BiMatTam("mat-khau-gia"), td, totp=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not any(h[0] == "dien" for h in td.hanh_dong)


# ── Trường hợp 2: email-direct không controller → fail-closed ───────────

def test_email_direct_khong_controller_thi_trinh_duyet_none(mod):
    """Không controller (không truyền, không factory) → trinh_duyet_none mà
    chưa từng mở trình duyệt (SAI S1 đã sửa ở (a): lõi cấp controller, còn
    module tự fail-closed khi không có)."""
    kq = chay(mod.dang_nhap(_dong_mat_khau(), trinh_duyet=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "trinh_duyet_none"


def test_khoi_phuc_t2_google_khong_controller_thi_trinh_duyet_none(mod):
    """T2 đường Google mà không có controller → T2 trinh_duyet_none."""
    kq = chay(mod.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, cach_dang_nhap="google",
        trinh_duyet=None, ho_so_google=""))
    assert not kq.thanh_cong
    assert kq.tang == "T2" and kq.ma_loi == "trinh_duyet_none"


# ── Trường hợp 3: challenge trước/sau khi điền → dừng, không solver ─────

@pytest.mark.parametrize("tin_hieu", ["captcha", "xac_minh", "cloudflare"])
def test_challenge_truoc_khi_dien(mod, tin_hieu):
    """Challenge đã có trước khi điền → dừng trước khi điền, không điền
    gì, không gọi solver."""
    td = TrinhDuyetGiaDong(_trang_login([tin_hieu], {}))
    kq = chay(mod._dang_nhap_bang_trinh_duyet(
        EMAIL_GIA, BiMatTam("mat-khau-gia"), td, totp=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not any(h[0] == "dien" for h in td.hanh_dong)
    assert ("vuot_thu_thach",) not in td.hanh_dong


@pytest.mark.parametrize("tin_hieu", ["xac_minh_thiet_bi", "thu_thach_truy_cap"])
def test_challenge_moi_truoc_khi_dien(mod, tin_hieu):
    """BÀI HỌC TỪ CLAUDE: hai tín hiệu này nằm trong _TIN_HIEU_CAN_NGUOI
    của chính module (đường Google) nhưng đường email vốn không kiểm →
    phải dừng trước khi điền như mọi challenge khác (đỏ-trước-xanh: FAIL
    trên cây chỉ-(a)+(c) vì lọt qua bước điền rồi thành công giả)."""
    td = TrinhDuyetGiaDong(_trang_login([tin_hieu],
                                       {"access_token": TOKEN_GIA}))
    kq = chay(mod._dang_nhap_bang_trinh_duyet(
        EMAIL_GIA, BiMatTam("mat-khau-gia"), td, totp=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"
    assert not any(h[0] == "dien" for h in td.hanh_dong)


@pytest.mark.parametrize("tin_hieu", ["xac_minh_thiet_bi", "thu_thach_truy_cap"])
def test_challenge_moi_sau_khi_dien(mod, tin_hieu):
    """Thử thách xuất hiện sau khi điền → kiểm sau-điền cũng phải dừng
    (không đọc cookie rồi thành công giả)."""
    td = TinHieuSauDien(
        _trang_login([], {"access_token": TOKEN_GIA}), [tin_hieu])
    kq = chay(mod._dang_nhap_bang_trinh_duyet(
        EMAIL_GIA, BiMatTam("mat-khau-gia"), td, totp=None))
    assert not kq.thanh_cong
    assert kq.ma_loi == "thu_thach_truoc_khi_dien"


def test_khong_goi_solver_tinh():
    """Module không có đường gọi solver: không gọi vuot_thu_thach, không
    nhắc 2captcha; không có gate giai_captcha nào để bật."""
    src = (THU_MUC / "module.py").read_text(encoding="utf-8")
    assert ".vuot_thu_thach(" not in src
    assert "2captcha" not in src
    assert "giai_captcha" not in src


# ── Trường hợp 4: Google tái dùng hồ sơ đi tắt ──────────────────────────

def test_google_tai_dung_di_tat(mod):
    """Hồ sơ đã có phiên ChatGPT → mở trang đăng nhập, thấy da_dang_nhap
    thì đi tắt: không bấm Google, không điền gì, đọc cookie phiên."""
    td = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td, chu_giu=EMAIL_GIA))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "mo_trang" in ten_buoc
    assert "bam" not in ten_buoc
    assert "dien" not in ten_buoc
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA
    assert ds[0]["email"] == EMAIL_GIA


def test_google_qua_dang_nhap_nv300(mod):
    """NV300: dang_nhap loại mat_khau nhưng cach_dang_nhap='google' thì đổi
    đường sang tái dùng hồ sơ, không đòi mật khẩu, không điền."""
    dong = _dong_mat_khau()
    dong.cach_dang_nhap = "google"
    dong.ho_so_google = _ho_so_gia()
    td = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq = chay(mod.dang_nhap(dong, trinh_duyet=td))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    assert not any(h[0] == "dien" for h in td.hanh_dong)


# ── Trường hợp 5: Google SSO bấm lại ────────────────────────────────────

def test_google_sso_bam_lai(mod):
    """Hồ sơ chưa có phiên → bấm nút «Log in» rồi nút «Continue with
    Google» → SSO chuyển về với phiên đã đăng nhập → đọc cookie phiên.
    Bộ điều khiển trừu tượng không có bước popup OAuth / chọn tài khoản /
    đồng ý — luồng thật có thể cần thêm bước chỉ kiểm thật mới thấy."""
    td = TrinhDuyetChuyenDoi(
        trang={LOGIN: {"tin_hieu": [], "cookie": {}},
               GOC_URL: {"tin_hieu": [], "cookie": {}}},
        sau_bam={LOGIN: {
            "url": GOC_URL,
            "trang": {"tin_hieu": ["da_dang_nhap"],
                      "cookie": {"access_token": TOKEN_GIA}},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td, chu_giu=EMAIL_GIA))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    da_bam = [h[1] for h in td.hanh_dong if h[0] == "bam"]
    assert mod._BO_CHON_DANG_NHAP in da_bam  # nút «Log in»
    assert mod._BO_CHON_GOOGLE in da_bam  # nút «Continue with Google»
    assert not any(h[0] == "dien" for h in td.hanh_dong)
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["goi"] == "plus"  # suy từ /accounts/check


# ── Trường hợp 6: serial/gap/hold Google của (c) ────────────────────────

def test_google_dang_giu_captcha_thi_chan_khong_cham_trinh_duyet(mod):
    """Đang giữ captcha cho chủ khác → lượt này bị chặn bằng mã
    cho_captcha_toan_cuc, KHÔNG chạm trình duyệt."""
    khoa_google.dat_giu_captcha(CHU_KHAC)
    td = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td, chu_giu=EMAIL_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == khoa_google.MA_LOI_GIU
    assert td.hanh_dong == []


def test_google_chu_hold_chay_lai_thanh_cong_go_hold(mod):
    """Chủ hold chạy lại (người đã xử lý captcha xong) → đi tắt thành công
    → gỡ hold toàn cục."""
    khoa_google.dat_giu_captcha(EMAIL_GIA)
    td = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td, chu_giu=EMAIL_GIA))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA, kq.ma_loi
    assert khoa_google.dang_giu() is False


@pytest.mark.parametrize("tin_hieu", ["captcha", "cloudflare"])
def test_google_challenge_dat_hold_dung_chu(mod, tin_hieu):
    """Đường Google gặp captcha/cloudflare → can_nguoi + đặt hold toàn cục
    cho đúng chủ (email của lượt này)."""
    td = TrinhDuyetGiaDong(_trang_login([tin_hieu], {}))
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td, chu_giu=EMAIL_GIA))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    dang_giu, chu, _phut = khoa_google.thong_tin_giu()
    assert dang_giu is True and chu == EMAIL_GIA


def test_google_gap_duoc_ton_trong_va_mac_dinh_production_khong_doi(
        mod, monkeypatch):
    """Gap Google: lượt thứ hai phải chờ nốt giãn cách (đo bằng env 0.5s
    thay cho 25s production). Hằng production vẫn 25 giây, không đổi."""
    assert khoa_google.GIAN_CACH_GIAY == 25.0
    monkeypatch.setenv("WEB2API_KHOA_DANG_NHAP_GIAN_CACH_GIAY", "0.5")
    td1 = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq1 = chay(mod.tai_dung_ho_so(_ho_so_gia(), td1, chu_giu=EMAIL_GIA))
    assert kq1.thanh_cong, kq1.ma_loi
    td2 = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    bat_dau = time.monotonic()
    kq2 = chay(mod.tai_dung_ho_so(_ho_so_gia(), td2, chu_giu=EMAIL_GIA))
    cho = time.monotonic() - bat_dau
    assert kq2.thanh_cong, kq2.ma_loi
    assert cho >= 0.3  # đã chờ nốt gap 0.5s (trừ thời gian lượt đầu chạy)


def test_khoi_phuc_t2_google_qua_controller_loi_cap(mod):
    """NV271/T2 đường Google: khoi_phuc_tu_dong cach='google' + controller
    lõi cấp + chu_giu=email → thành công ở T2."""
    td = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq = chay(mod.khoi_phuc_tu_dong(
        "", email=EMAIL_GIA, cach_dang_nhap="google",
        trinh_duyet=td, ho_so_google=_ho_so_gia()))
    assert kq.thanh_cong and kq.tang == "T2", kq.ma_loi
    assert khoa_google.dang_giu() is False


# ── Trường hợp 7: vòng đời controller theo (a) ──────────────────────────

def test_module_khong_tu_dong_controller(mod):
    """Module không bao giờ tự đóng controller (dù thành công, can_nguoi
    hay đường Google): đóng là việc của người gọi (lõi) theo luật NV309-a."""
    td_ok = TrinhDuyetGiaDong(_trang_login([], {"access_token": TOKEN_GIA}))
    kq = chay(mod.dang_nhap(_dong_mat_khau(), trinh_duyet=td_ok))
    assert kq.thanh_cong
    assert ("dong",) not in td_ok.hanh_dong
    td_cn = TrinhDuyetGiaDong(_trang_login(["captcha"], {}))
    kq2 = chay(mod.dang_nhap(_dong_mat_khau(), trinh_duyet=td_cn))
    assert not kq2.thanh_cong
    assert ("dong",) not in td_cn.hanh_dong
    td_gg = TrinhDuyetGiaDong(_trang_login(
        ["da_dang_nhap"], {"access_token": TOKEN_GIA}))
    kq3 = chay(mod.tai_dung_ho_so(_ho_so_gia(), td_gg, chu_giu=EMAIL_GIA))
    assert kq3.thanh_cong
    assert ("dong",) not in td_gg.hanh_dong


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
    """Cầu nối lõi: hồ sơ sẵn có → tái dùng (không tạo mới, không đóng sau
    lượt); không hồ sơ → tạo workspace mới; chỉ workspace mới tạo mới được
    đóng, và chỉ ở trạng thái cuối (người gọi tự quyết khi nào gọi
    dong_workspace_moi — không gọi khi can_nguoi, không bao giờ gọi cho
    workspace sẵn có)."""
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
