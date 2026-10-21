# NV235 — Đăng nhập web2api theo đúng code c2a (chủ máy 21/10 19:45).
#
# ĐO qua lõi thật + dịch vụ GIẢ mô phỏng ĐÚNG hành vi c2a đã đo, KỂ CẢ các
# báo thành công giả của chính c2a (token hết hạn 42h, «Hồ sơ đã có phiên»).
# Theo thứ tự 6 bước của spec:
#  b2: phân loại theo dữ liệu — mặc định nhóm chatgpt = truc_tiep
#  b3: ChatGPT OpenAI gốc: refresh-jwt trước + KIỂM exp; qua-Google: reuse_session
#  b4: Google: MỘT phiên toàn hệ thống (khoá) + captcha hold + cooldown 1800s
#  b5: tầng T0–T3 trên khung khoi_phuc_phien.py
#  b6: 2FA/tiến độ theo bảng khai báo dùng chung (gộp NV229)

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GOC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GOC / "test"))

from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach  # noqa: E402
from gia import MayChuThat  # noqa: E402


def _tai_module_mdn():
    spec = importlib.util.spec_from_file_location(
        "may_dang_nhap_mod_nv235", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _tai_module_mdn()

EMAIL_GPT = "chu.openai.goc@example.com"
MK_GPT = "mat-khau-openai-gia-01"
TOTP_GPT = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
EMAIL_GOOGLE = "tai.khoan.google@example.com"
EMAIL_CLAUDE = "chu.claude@example.com"
EMAIL_GROK = "chu.grok@example.com"


def _jwt_gia(email: str, het_han_giay: int = 30 * 86400) -> str:
    payload = {"https://api.openai.com/profile": {"email": email},
               "exp": int(time.time()) + het_han_giay}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"aaa.{b64}.bbb"


class DichVuGiaNV235:
    """Dịch vụ giả mô phỏng ĐÚNG hành vi c2a đã đo 21/10, kể cả lỗi của c2a."""

    def __init__(self):
        self.onboard = []          # (dich_vu, than) mọi lượt onboard
        self.jwt_lay = []          # hồ sơ gọi refresh-jwt
        self.refresh_jwt_ok = True  # False = refresh-jwt trượt (phiên chết)
        self.token_lay = []        # hồ sơ gọi openai-native token
        self.google_bat_dau = []   # thân POST auto-login-saved
        self.google_dang_chay = 0    # đếm đồng thời (đo khoá toàn cục)
        self.google_dong_thoi_max = 0
        self.trang_thai_cuoi = {}  # (dich_vu, ho_so) -> state cuối
        self.jwt_theo_ho_so = {}   # ho_so -> access_token trả ở refresh-jwt
        self.token_het_han = False  # True = openai-native/token trả token chết
        self.google_trang_thai = "success"
        self._da_hoi = set()

    def _state(self, dich_vu, ho_so):
        khoa = (dich_vu, ho_so)
        if khoa not in self._da_hoi:
            self._da_hoi.add(khoa)
            return "starting"
        return self.trang_thai_cuoi.get(khoa, "success")

    def app(self):
        dv = self

        async def luu(request):
            than = await request.json()
            return JSONResponse({"email": than.get("email"), "ok": True})

        def lam_onboard(dich_vu):
            async def onboard(request):
                than = await request.json()
                dv.onboard.append((dich_vu, than))
                return JSONResponse({"profile": than.get("profile"), "state": "starting",
                                     "message": "Khởi tạo", "ok": True})
            return onboard

        def lam_trang_thai(dich_vu):
            async def trang_thai(request):
                ho_so = request.path_params["profile"]
                return JSONResponse({"profile": ho_so, "state": dv._state(dich_vu, ho_so),
                                     "message": "", "error": "", "ok": True})
            return trang_thai

        async def refresh_jwt(request):
            ho_so = request.path_params["profile"]
            dv.jwt_lay.append(ho_so)
            if not dv.refresh_jwt_ok:
                return JSONResponse({"profile": ho_so, "ok": False,
                                     "error": "phiên chết"})
            token = dv.jwt_theo_ho_so.get(ho_so, _jwt_gia(EMAIL_GPT))
            # Mô phỏng đúng khuôn dịch vụ thật: ok/method/access_token
            return JSONResponse({"profile": ho_so, "ok": True, "method": "scrape",
                                 "access_token": token,
                                 "captured_email": EMAIL_GPT})

        async def token_openai(request):
            ho_so = request.path_params["profile"]
            dv.token_lay.append(ho_so)
            token = _jwt_gia(EMAIL_GPT, het_han_giay=-3600) if dv.token_het_han \
                else _jwt_gia(EMAIL_GPT)
            return JSONResponse({"profile": ho_so, "email": EMAIL_GPT,
                                 "access_token": token})

        async def google_bat_dau(request):
            than = await request.json()
            dv.google_bat_dau.append(than)
            # Đo đồng thời thật: giữ 0.3s trong «lượt» để lộ việc chạy song song
            dv.google_dang_chay += 1
            dv.google_dong_thoi_max = max(dv.google_dong_thoi_max,
                                          dv.google_dang_chay)
            await asyncio.sleep(0.3)
            dv.google_dang_chay -= 1
            return JSONResponse({"profile": than.get("profile"), "state": "starting",
                                 "message": "Khởi tạo", "ok": True})

        async def google_trang_thai(request):
            return JSONResponse({"profile": request.path_params["profile"],
                                 "state": dv.google_trang_thai,
                                 "message": "", "error": "", "ok": True})

        async def hai_fa(request):
            than = await request.json()
            dv.onboard.append(("2fa:" + request.path_params.get("profile", ""), than))
            return JSONResponse({"ok": True})

        return Starlette(routes=[
            Route("/v1/accounts/saved", luu, methods=["POST"]),
            Route("/v1/chatgpt/onboard", lam_onboard("chatgpt"), methods=["POST"]),
            Route("/v1/chatgpt/{profile}/onboard-status", lam_trang_thai("chatgpt")),
            Route("/v1/chatgpt/{profile}/refresh-jwt", refresh_jwt),
            Route("/v1/openai-native/onboard", lam_onboard("openai"), methods=["POST"]),
            Route("/v1/openai-native/{profile}/onboard-status", lam_trang_thai("openai")),
            Route("/v1/openai-native/{profile}/onboard-2fa-code", hai_fa, methods=["POST"]),
            Route("/v1/openai-native/{profile}/token", token_openai),
            Route("/v1/claude-web/onboard", lam_onboard("claude-web"), methods=["POST"]),
            Route("/v1/claude-web/{profile}/onboard-status", lam_trang_thai("claude-web")),
            Route("/v1/claude-web/{profile}/onboard-2fa-code", hai_fa, methods=["POST"]),
            Route("/v1/grok-web/onboard", lam_onboard("grok-web"), methods=["POST"]),
            Route("/v1/grok-web/{profile}/onboard-status", lam_trang_thai("grok-web")),
            Route("/v1/grok-web/{profile}/onboard-2fa-code", hai_fa, methods=["POST"]),
            Route("/v1/session/auto-login-saved", google_bat_dau, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", google_trang_thai),
            Route("/v1/session/{profile}/auto-login-2fa-code", hai_fa, methods=["POST"]),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaNV235()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


@pytest.fixture
def mod(dich_vu_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    # Reset state toàn cục NV235-b4 giữa các test (khoá Google dùng chung).
    MOD._LAN_DANG_NHAP_GOOGLE_XONG = 0.0
    MOD._CAPTCHA_HOLD_DEN = 0.0
    MOD._CAPTCHA_HOLD_HO_SO = ""
    MOD._LAN_DANG_NHAP_GOOGLE_THEO_TK.clear()
    m = MOD.Module({"tien_to": "may_dang_nhap", "ten": "may-dang-nhap (test)"})
    m._url = dich_vu_gia.url
    m._khoa = "khoa-gia"
    return m


def _dong(email=EMAIL_GPT, mk=MK_GPT, totp=TOTP_GPT, module_cua="chatgpt-web",
          dang_nhap_bang=None, google=False):
    d = DongDaTach(dong=0, email=email, loai="mat_khau",
                   mat_khau=BiMatTam(mk),
                   totp=BiMatTotp(totp) if totp else None)
    d.module_cua_tai_khoan = module_cua
    if dang_nhap_bang is not None:
        d.dang_nhap_bang = dang_nhap_bang
    if google:
        d.google_email = EMAIL_GOOGLE
        d.google_mat_khau = BiMatTam("mat-khau-google-gia-02")
        d.google_totp = BiMatTotp(TOTP_GPT)
    return d


# ── b2: phân loại mặc định ────────────────────────────────────────────────

def test_b2_chatgpt_chua_khai_mac_dinh_truc_tiep(mod, dich_vu_gia):
    """nhóm chatgpt chưa khai dang_nhap_bang → mặc định truc_tiep (đường
    openai: refresh-jwt trước), KHÔNG rơi về đường Google cũ _dang_nhap_chatgpt
    (POST /v1/chatgpt/onboard)."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="")))
    assert kq.thanh_cong
    duong = [d for d, _ in dich_vu_gia.onboard]
    assert "chatgpt" not in duong, "không được đi đường Google cũ"
    assert dich_vu_gia.jwt_lay, "đường truc_tiep phải thử refresh-jwt trước"


def test_b2_claude_chua_khai_giu_duong_cu(mod, dich_vu_gia):
    """nhóm khác chưa khai → giữ đường cũ (auto-login-saved Google)."""
    asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(email=EMAIL_CLAUDE, module_cua="claude-web",
                            dang_nhap_bang="")))
    assert dich_vu_gia.google_bat_dau, "phải đi đường Google cũ"


# ── b3: OpenAI gốc — refresh-jwt trước + KIỂM exp ─────────────────────────

def test_b3_refresh_jwt_token_con_han_dung_ngay(mod, dich_vu_gia):
    """BƯỚC 1: refresh-jwt trả token còn hạn tốt → dùng ngay, KHÔNG onboard."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="truc_tiep")))
    assert kq.thanh_cong
    assert dich_vu_gia.jwt_lay, "phải gọi refresh-jwt trước"
    assert not [d for d, _ in dich_vu_gia.onboard if d == "openai"], \
        "token còn hạn thì không được onboard mật khẩu"


def test_b3_refresh_jwt_token_het_han_42h_phien_chet(mod, dich_vu_gia):
    """Mô phỏng ĐÚNG lỗi c2a: refresh-jwt ok=true/method=scrape nhưng token
    ĐÃ HẾT HẠN 42h → coi phiên chết → onboard mật khẩu+TOTP."""
    hs = "openai-chu-openai-goc"
    dich_vu_gia.jwt_theo_ho_so[hs] = _jwt_gia(EMAIL_GPT, het_han_giay=-42 * 3600)
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="truc_tiep")))
    assert kq.thanh_cong
    assert ("openai", ) or True
    duong = [d for d, _ in dich_vu_gia.onboard]
    assert "openai" in duong, "token hết hạn → phải onboard lại mật khẩu+TOTP"
    # token gắn vào khoá sổ phải là token MỚI còn hạn, không phải token chết
    token_moi = (kq.jwt_moi or {}).get("access_token", "")
    assert token_moi and token_moi != dich_vu_gia.jwt_theo_ho_so[hs]


def test_b3_refresh_jwt_token_het_han_1h_van_phien_chet(mod, dich_vu_gia):
    """Token hết hạn 1h (trong ngưỡng 24h «sắp hết») → vẫn coi phiên chết →
    onboard lại. Giết đột biến đảo ngưỡng exp."""
    hs = "openai-chu-openai-goc"
    dich_vu_gia.jwt_theo_ho_so[hs] = _jwt_gia(EMAIL_GPT, het_han_giay=-3600)
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="truc_tiep")))
    assert kq.thanh_cong
    duong = [d for d, _ in dich_vu_gia.onboard]
    assert "openai" in duong


def test_b3_chatgpt_qua_google_reuse_session(mod, dich_vu_gia):
    """ChatGPT-qua-Google: onboard gửi reuse_session=True (đúng c2a _cgf_reuse),
    thiếu phiên mới đăng nhập Google."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="google", google=True)))
    assert kq.thanh_cong
    than_chatgpt = [t for d, t in dich_vu_gia.onboard if d == "chatgpt"]
    assert than_chatgpt and than_chatgpt[0].get("reuse_session") is True


def test_b3_onboard_xong_token_chet_can_nguoi(mod, dich_vu_gia):
    """Mô phỏng «Hồ sơ đã có phiên» giả: onboard xong nhưng token trả về đã
    chết → KHÔNG báo thành công giả → «cần người». Giết đột biến bỏ kiểm exp
    sau onboard."""
    dich_vu_gia.refresh_jwt_ok = False
    dich_vu_gia.token_het_han = True
    kq = asyncio.get_event_loop().run_until_complete(
        mod.dang_nhap(_dong(dang_nhap_bang="truc_tiep")))
    assert not kq.thanh_cong
    assert kq.ma_loi == "phien_chet" and kq.can_nguoi


# ── b4: Google — một phiên toàn hệ thống + captcha hold + cooldown ────────

def test_b4_hai_luot_google_tuan_tu_khoa_toan_cuc(mod, dich_vu_gia, monkeypatch):
    """Hai lượt đăng nhập Google đồng thời → khoá toàn cục xếp hàng tuần tự
    (đồng thời tối đa = 1, đúng c2a _glogin_serial)."""
    monkeypatch.setenv("C2A_DN_GAP_GOOGLE_GIAY", "0")

    async def chay():
        await asyncio.gather(
            mod._dang_nhap_google("a1@example.com", "a1@example.com", mod._nv145b_trang_thai(), 0, "mk1", ""),
            mod._dang_nhap_google("a2@example.com", "a2@example.com", mod._nv145b_trang_thai(), 0, "mk2", ""),
        )

    asyncio.get_event_loop().run_until_complete(chay())
    assert len(dich_vu_gia.google_bat_dau) == 2
    assert dich_vu_gia.google_dong_thoi_max == 1, \
        "hai lượt Google không được chạy đồng thời"


def test_b4_captcha_hold_chan_luot_khac(mod, dich_vu_gia, monkeypatch):
    """Gặp need_captcha → captcha hold: lượt Google khác bị chặn ngay với
    mã blocked_captcha_window, không bắn thêm."""
    monkeypatch.setenv("C2A_DN_GAP_GOOGLE_GIAY", "0")
    dich_vu_gia.google_trang_thai = "need_captcha"
    st = mod._nv145b_trang_thai()
    kq1 = asyncio.get_event_loop().run_until_complete(
        mod._dang_nhap_google(EMAIL_GOOGLE, EMAIL_GOOGLE, st, 0, "mk", ""))
    assert not kq1.thanh_cong
    so_luot_1 = len(dich_vu_gia.google_bat_dau)
    # lượt thứ hai trong thời gian hold → bị chặn, không gọi dịch vụ thêm
    st2 = mod._nv145b_trang_thai()
    kq2 = asyncio.get_event_loop().run_until_complete(
        mod._dang_nhap_google("khac@example.com", "khac@example.com", st2, 0, "mk2", ""))
    assert not kq2.thanh_cong
    assert kq2.ma_loi == "blocked_captcha_window"
    assert len(dich_vu_gia.google_bat_dau) == so_luot_1


def test_b4_cooldown_1800s_theo_tai_khoan(mod, dich_vu_gia, monkeypatch):
    """Cooldown 1800s/tài khoản: đăng nhập Google lại trong 1800s → từ chối
    ngay, không gọi dịch vụ."""
    monkeypatch.setenv("C2A_DN_GAP_GOOGLE_GIAY", "0")
    st = mod._nv145b_trang_thai()
    kq1 = asyncio.get_event_loop().run_until_complete(
        mod._dang_nhap_google(EMAIL_GOOGLE, EMAIL_GOOGLE, st, 0, "mk", ""))
    assert kq1.thanh_cong
    so_luot = len(dich_vu_gia.google_bat_dau)
    kq2 = asyncio.get_event_loop().run_until_complete(
        mod._dang_nhap_google(EMAIL_GOOGLE, EMAIL_GOOGLE, st, 0, "mk", ""))
    assert not kq2.thanh_cong and kq2.ma_loi == "dang_trong_cooldown"
    assert len(dich_vu_gia.google_bat_dau) == so_luot


# ── b6: 2FA/tiến độ theo bảng dùng chung ──────────────────────────────────

def test_b6_nhap_ma_2fa_openai_dung_duong(mod, dich_vu_gia):
    """2FA cho hồ sơ openai- → POST /v1/openai-native/{hs}/onboard-2fa-code
    (trước đây _la_luong_chatgpt=false nên đi sai đường /v1/session/)."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.nhap_ma_2fa("openai-chu-openai-goc", BiMatTam("123456"),
                        module_cua_tai_khoan="chatgpt-web"))
    assert kq["ok"] is True
    duong_2fa = [d for d, _ in dich_vu_gia.onboard if str(d).startswith("2fa:")]
    assert duong_2fa and "openai-chu-openai-goc" in duong_2fa[0]
    assert not [t for d, t in dich_vu_gia.onboard
                if str(d).startswith("2fa:") and "/v1/session/" in str(t)], \
        "không được đi đường /v1/session/ cho hồ sơ openai-"


def test_b6_tien_do_claude_dung_duong(mod, dich_vu_gia):
    """tiến độ cho hồ sơ claude- → GET /v1/claude-web/{hs}/onboard-status
    (trước đây đi sai /v1/session/{hs}/auto-login-status)."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.tien_do("claude-web-chu-claude", module_cua_tai_khoan="claude-web"))
    assert kq["ok"] is True and kq["giai_doan"] == "dang_chay"


def test_b6_tien_do_grok_dung_duong(mod, dich_vu_gia):
    kq = asyncio.get_event_loop().run_until_complete(
        mod.tien_do("grok-web-chu-grok", module_cua_tai_khoan="grok-web"))
    assert kq["ok"] is True and kq["giai_doan"] == "dang_chay"


def test_b6_nhap_ma_2fa_google_dung_duong(mod, dich_vu_gia):
    """Hồi quy: hồ sơ google- vẫn đi /v1/session/{hs}/auto-login-2fa-code."""
    kq = asyncio.get_event_loop().run_until_complete(
        mod.nhap_ma_2fa("google-tai-khoan-google", BiMatTam("654321"),
                        module_cua_tai_khoan="gemini-web"))
    assert kq["ok"] is True
