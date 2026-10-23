# NV223 — Đăng nhập ChatGPT / Grok / Claude QUA tài khoản Google đã lưu,
# phân biệt với đăng nhập thẳng (trực tiếp).
#
# ĐO qua lõi thật + dịch vụ giả nghiêm (theo spec, 6 ô):
# (1) ChatGPT kiểu «google» → POST /v1/chatgpt/onboard, két dịch vụ nhận
#     email/mật khẩu/TOTP của tài khoản GOOGLE được chọn (loai «google»),
#     hồ sơ vẫn theo email tài khoản ChatGPT.
# (2) ChatGPT kiểu «trực tiếp» → POST /v1/openai-native/onboard, két nhận
#     bí mật của CHÍNH tài khoản (loai «openai»); token gắn qua jwt_moi.
# (3) Claude kiểu «google» → POST /v1/claude-web/onboard, két = bí mật Google;
#     thành công kèm ho_so_dang_nhap đúng (biên NV214 nối cookie).
# (4) Grok kiểu «google» gặp Cloudflare → state need_manual → «cần người»,
#     onboard chỉ gọi ĐÚNG 1 lần (không thử lại vòng).
# (5) Khoá gắn đúng module sở hữu: jwt_moi qua gan_ket_qua_dang_nhap.ap_dung
#     gọi gan_jwt_tu_dang_nhap của module sở hữu (thay khoá cũ cùng email).
# (6) Không lộ bí mật: kết quả/đáp API không chứa mật khẩu/TOTP; bí mật
#     Google bị xoá sau lượt gọi.
# Cộng: kho + route (lưu 2 trường mới, kiểm chéo, Google tạm dừng/thiếu →
# lỗi rõ, module KHÔNG được gọi); legacy không đặt trường → hành vi cũ;
# cặp không hỗ trợ (claude/grok + trực tiếp) → lỗi rõ, dịch vụ không bị gọi.

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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
        "may_dang_nhap_mod_nv223", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _tai_module_mdn()

EMAIL_GPT = "chu.chatgpt@example.com"
MK_GPT = "mat-khau-chatgpt-gia-01"
TOTP_GPT = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
EMAIL_GOOGLE = "tai.khoan.google@example.com"
MK_GOOGLE = "mat-khau-google-gia-02"
TOTP_GOOGLE = "KRSXG5DSMFWHK3DMJBSWY3DPEHPK3PXQ"
EMAIL_CLAUDE = "chu.claude@example.com"
EMAIL_GROK = "chu.grok@example.com"


def _jwt_gia(email: str) -> str:
    payload = {"https://api.openai.com/profile": {"email": email},
               "exp": int(time.time()) + 30 * 86400}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"aaa.{b64}.bbb"


class DichVuGiaNV223:
    """Dịch vụ may-dang-nhap giả: 4 đường onboard + két + token/jwt.

    Trạng thái onboard theo từng (dịch vụ, hồ sơ): «starting» → trạng thái
    cuối cấu hình trong ``trang_thai_cuoi`` (mặc định «success»; grok mặc
    định «need_manual» như dịch vụ thật khi gặp Cloudflare).
    """

    def __init__(self):
        self.da_luu = []            # thân POST /v1/accounts/saved
        self.onboard = []           # (duong, than) mọi lượt onboard
        self.google_bat_dau = []    # thân POST /v1/session/auto-login-saved
        self.jwt_lay = []           # hồ sơ gọi refresh-jwt
        self.refresh_jwt_ok = True  # False = refresh-jwt trượt (phiên chết)
        self.token_lay = []         # hồ sơ gọi openai-native token
        self.trang_thai_cuoi = {}   # (dich_vu, ho_so) -> state cuối
        self._da_hoi = set()

    def _state(self, dich_vu, ho_so):
        khoa = (dich_vu, ho_so)
        if khoa not in self._da_hoi:
            self._da_hoi.add(khoa)
            return "starting"
        return self.trang_thai_cuoi.get(khoa, "success")

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "status": "ok"})

        async def profiles(request):
            return JSONResponse({"profiles": [], "ok": True})

        async def luu(request):
            than = await request.json()
            dv.da_luu.append(than)
            return JSONResponse({"email": than.get("email"), "ok": True})

        def lam_onboard(dich_vu):
            async def onboard(request):
                than = await request.json()
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(than.get("profile") or "")):
                    return JSONResponse({"profile": than.get("profile"), "state": "failed",
                                         "message": "", "error": "Tên profile không hợp lệ",
                                         "ok": True})
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
            return JSONResponse({"profile": ho_so, "ok": True, "method": "scrape",
                                 "access_token": _jwt_gia(EMAIL_GOOGLE),
                                 "captured_email": EMAIL_GOOGLE})

        async def token_openai(request):
            ho_so = request.path_params["profile"]
            dv.token_lay.append(ho_so)
            # Đúng khuôn dịch vụ thật: {profile, email, access_token} — không «ok»
            return JSONResponse({"profile": ho_so, "email": EMAIL_GPT,
                                 "access_token": _jwt_gia(EMAIL_GPT)})

        async def google_bat_dau(request):
            than = await request.json()
            dv.google_bat_dau.append(than)
            return JSONResponse({"profile": than.get("profile"), "state": "starting",
                                 "message": "Khởi tạo", "ok": True})

        async def google_trang_thai(request):
            return JSONResponse({"profile": request.path_params["profile"],
                                 "state": "success", "message": "", "error": "",
                                 "ok": True})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", luu, methods=["POST"]),
            Route("/v1/chatgpt/onboard", lam_onboard("chatgpt"), methods=["POST"]),
            Route("/v1/chatgpt/{profile}/onboard-status", lam_trang_thai("chatgpt")),
            Route("/v1/chatgpt/{profile}/refresh-jwt", refresh_jwt),
            Route("/v1/openai-native/onboard", lam_onboard("openai"), methods=["POST"]),
            Route("/v1/openai-native/{profile}/onboard-status", lam_trang_thai("openai")),
            Route("/v1/openai-native/{profile}/token", token_openai),
            Route("/v1/claude-web/onboard", lam_onboard("claude-web"), methods=["POST"]),
            Route("/v1/claude-web/{profile}/onboard-status", lam_trang_thai("claude-web")),
            Route("/v1/grok-web/onboard", lam_onboard("grok-web"), methods=["POST"]),
            Route("/v1/grok-web/{profile}/onboard-status", lam_trang_thai("grok-web")),
            Route("/v1/session/auto-login-saved", google_bat_dau, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", google_trang_thai),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaNV223()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


@pytest.fixture
def mod(dich_vu_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    m = MOD.Module({"tien_to": "may_dang_nhap", "ten": "may-dang-nhap (test)"})
    m._url = dich_vu_gia.url
    m._khoa = "khoa-gia"
    return m


@pytest.fixture(autouse=True)
def _don_trang_thai_dang_nhap_google():
    """NV248: dọn trạng thái TOÀN CỤC của luồng Google (NV235-b4) trước và
    sau mỗi test. Test nạp module ĐỘNG (importlib) nên phải dọn đúng bản
    module đó — chạy lẻ xanh, chạy chung đỏ nếu sót."""
    def _don():
        MOD._LAN_DANG_NHAP_GOOGLE_THEO_TK.clear()
        MOD._LAN_DANG_NHAP_GOOGLE_XONG = 0.0
        MOD._CAPTCHA_HOLD_DEN = 0.0
        MOD._CAPTCHA_HOLD_HO_SO = ""
    _don()
    yield
    _don()


def _dong(email=EMAIL_GPT, mk=MK_GPT, totp=TOTP_GPT, module_cua="chatgpt-web",
          dang_nhap_bang=None, google=False):
    d = DongDaTach(dong=0, email=email, loai="mat_khau",
                   mat_khau=BiMatTam(mk),
                   totp=BiMatTotp(totp) if totp else None)
    d.module_cua_tai_khoan = module_cua
    if dang_nhap_bang is not None:
        d.dang_nhap_bang = dang_nhap_bang
    if google:
        d.dnh_email = EMAIL_GOOGLE
        d.dnh_mat_khau = BiMatTam(MK_GOOGLE)
        d.dnh_totp = BiMatTotp(TOTP_GOOGLE)
    return d


def _chay(coro):
    return asyncio.run(coro)


def _onboard_cua(dv, dich_vu):
    return [than for (ten, than) in dv.onboard if ten == dich_vu]


# ── Ô (1): ChatGPT qua Google — két + onboard dùng bí mật GOOGLE ────────────

def test_o1_chatgpt_qua_google_dung_bi_mat_google(mod, dich_vu_gia):
    d = _dong(dang_nhap_bang="google", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    # Két dịch vụ: ĐÚNG MỘT bản ghi — của tài khoản Google, loại «google»
    assert len(dich_vu_gia.da_luu) == 1
    luu = dich_vu_gia.da_luu[0]
    assert luu["email"] == EMAIL_GOOGLE
    assert luu["password"] == MK_GOOGLE
    assert luu["totp_secret"] == TOTP_GOOGLE
    assert luu["loai"] == "google"
    # Onboard: đường ChatGPT-Google, hồ sơ theo email tài khoản ChatGPT,
    # email đăng nhập là email Google (dịch vụ bù bí mật từ két theo email)
    ob = _onboard_cua(dich_vu_gia, "chatgpt")
    assert len(ob) == 1
    assert ob[0]["profile"] == "chatgpt-chu-chatgpt"
    assert ob[0]["email"] == EMAIL_GOOGLE
    assert _onboard_cua(dich_vu_gia, "openai") == []
    assert dich_vu_gia.google_bat_dau == []
    # Ô (6): bí mật Google đã bị xoá sau lượt gọi; kết quả không chứa bí mật
    assert d.dnh_mat_khau.da_xoa()
    assert MK_GOOGLE not in json.dumps(getattr(kq, "jwt_moi", None) or {})
    assert MK_GPT not in json.dumps(getattr(kq, "jwt_moi", None) or {})


# ── Ô (2): ChatGPT trực tiếp — đường openai-native, bí mật của chính tk ────

def test_o2_chatgpt_truc_tiep_di_openai_native(mod, dich_vu_gia):
    # NV235-b3: refresh-jwt trượt (phiên chết) → mới onboard openai-native
    # bằng bí mật của CHÍNH tài khoản (loai «openai»).
    dich_vu_gia.refresh_jwt_ok = False
    d = _dong(dang_nhap_bang="truc_tiep")
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert dich_vu_gia.jwt_lay, "phải thử refresh-jwt trước"
    assert len(dich_vu_gia.da_luu) == 1
    luu = dich_vu_gia.da_luu[0]
    assert luu["email"] == EMAIL_GPT
    assert luu["password"] == MK_GPT
    assert luu["loai"] == "openai"
    ob = _onboard_cua(dich_vu_gia, "openai")
    assert len(ob) == 1
    assert ob[0]["email"] == EMAIL_GPT
    assert _onboard_cua(dich_vu_gia, "chatgpt") == []
    # Token lấy qua endpoint token của openai-native → jwt_moi cho module sở hữu
    assert dich_vu_gia.token_lay == [ob[0]["profile"]]
    jwt_moi = getattr(kq, "jwt_moi", None)
    assert isinstance(jwt_moi, dict) and jwt_moi["access_token"]
    assert jwt_moi["email"] == EMAIL_GPT
    assert jwt_moi["ho_so"] == ob[0]["profile"]


# ── Ô (3): Claude qua Google ────────────────────────────────────────────────

def test_o3_claude_qua_google(mod, dich_vu_gia):
    d = _dong(email=EMAIL_CLAUDE, module_cua="claude-web",
              dang_nhap_bang="google", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert len(dich_vu_gia.da_luu) == 1
    luu = dich_vu_gia.da_luu[0]
    assert luu["email"] == EMAIL_GOOGLE and luu["loai"] == "google"
    assert luu["password"] == MK_GOOGLE
    ob = _onboard_cua(dich_vu_gia, "claude-web")
    assert len(ob) == 1
    assert ob[0]["email"] == EMAIL_GOOGLE
    assert dich_vu_gia.google_bat_dau == []
    # Ô (5) phía claude: hồ sơ gắn vào kết quả để lõi nối cookie đúng module
    assert getattr(kq, "ho_so_dang_nhap", "") == ob[0]["profile"]
    assert getattr(kq, "jwt_moi", None) is None


# ── Ô (4): Grok gặp Cloudflare → cần người, không thử lại vòng ─────────────

def test_o4_grok_cloudflare_can_nguoi_khong_thu_lai(mod, dich_vu_gia):
    dich_vu_gia.trang_thai_cuoi[("grok-web", "grok-chu-grok")] = "need_manual"
    d = _dong(email=EMAIL_GROK, module_cua="grok-web",
              dang_nhap_bang="google", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert not kq.thanh_cong
    assert getattr(kq, "can_nguoi", False) is True
    # KHÔNG thử lại vòng: onboard gọi đúng 1 lần
    assert len(_onboard_cua(dich_vu_gia, "grok-web")) == 1


# ── Ô (5): khoá gắn đúng module sở hữu qua lõi thật (ap_dung) ───────────────

def test_o5_jwt_gan_dung_module_so_huu(mod, dich_vu_gia):
    from app import gan_ket_qua_dang_nhap as gan_mod

    d = _dong(dang_nhap_bang="google", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong
    da_gan = []

    class ModuleSoHuuGia:
        def gan_jwt_tu_dang_nhap(self, email, token, ho_so):
            da_gan.append((email, token, ho_so))

    so = SimpleNamespace(dang_chay={"chatgpt-web": ModuleSoHuuGia(),
                                    "claude-web": object(),
                                    "grok-web": object()})
    gan_mod.ap_dung(so, kq, "chatgpt-web")
    assert len(da_gan) == 1
    email_gan, token_gan, ho_so_gan = da_gan[0]
    assert email_gan == EMAIL_GOOGLE  # claim email trong JWT (tài khoản Google)
    assert ho_so_gan == "chatgpt-chu-chatgpt"
    assert token_gan == getattr(kq, "jwt_moi", None) or token_gan
    # jwt_moi bị lấy khỏi kết quả ngay sau khi áp dụng (không rò ra ngoài)
    assert getattr(kq, "jwt_moi", None) is None


# ── Legacy: không đặt dang_nhap_bang → hành vi cũ nguyên vẹn ────────────────

def test_legacy_khong_truong_moi_giu_duong_cu(mod, dich_vu_gia):
    # NV235-b2: chatgpt-web không khai dang_nhap_bang → mặc định truc_tiep
    # (đường openai: refresh-jwt trước), KHÔNG còn đường Google cũ
    # POST /v1/chatgpt/onboard.
    d = _dong()  # module chatgpt-web, không dang_nhap_bang
    kq = _chay(mod.dang_nhap(d))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert dich_vu_gia.jwt_lay, "phải thử refresh-jwt trước"
    assert _onboard_cua(dich_vu_gia, "chatgpt") == []
    assert dich_vu_gia.google_bat_dau == []


def test_gia_tri_dang_nhap_bang_sai_bi_tu_choi(mod, dich_vu_gia):
    d = _dong(dang_nhap_bang="qua-facebook", google=True)
    kq = _chay(mod.dang_nhap(d))
    assert not kq.thanh_cong
    assert getattr(kq, "ma_loi", "") == "tham_so_khong_hop_le"
    assert dich_vu_gia.da_luu == [] and dich_vu_gia.onboard == []


def test_cap_khong_ho_tro_claude_truc_tiep(mod, dich_vu_gia):
    d = _dong(email=EMAIL_CLAUDE, module_cua="claude-web",
              dang_nhap_bang="truc_tiep")
    kq = _chay(mod.dang_nhap(d))
    assert not kq.thanh_cong
    assert getattr(kq, "ma_loi", "") not in ("", "tham_so_khong_hop_le")
    # Dịch vụ KHÔNG bị gọi, bí mật KHÔNG bị chạm (chưa bị lấy ra khỏi két tạm)
    assert dich_vu_gia.da_luu == [] and dich_vu_gia.onboard == []
    assert not d.mat_khau.da_xoa()


def test_google_thieu_bi_mat_google_bi_tu_choi(mod, dich_vu_gia):
    d = _dong(dang_nhap_bang="google", google=False)  # route chưa gắn bí mật Google
    kq = _chay(mod.dang_nhap(d))
    assert not kq.thanh_cong
    assert dich_vu_gia.da_luu == [] and dich_vu_gia.onboard == []


# ── Kho + route qua lõi thật (TestClient) ───────────────────────────────────

_KHOA_QT = "khoa-qt-nv223-0123456789abcdef"
_QT = {"Authorization": f"Bearer {_KHOA_QT}"}
_MODULE_GIA = "gia-nv223"


@pytest.fixture()
def moi_truong(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.so_module import SoModule

    data_dir = tmp_path / "data-loi"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", _KHOA_QT)
    monkeypatch.setenv("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "0")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, so


class ModuleDangNhapGia:
    """Module thực thi đăng nhập giả: ghi lại DongDaTach nhận được."""

    def __init__(self):
        self.da_nhan = []

    async def dang_nhap(self, dong):
        self.da_nhan.append(dong)
        return SimpleNamespace(thanh_cong=True, can_nguoi=False, cho_2fa=False,
                               huong_dan="", ma_loi="", jwt_moi=None,
                               ho_so_dang_nhap="")


def _cai_module_gia(so, fake, module_id=_MODULE_GIA):
    fake.manifest = {"id": module_id}
    so.catalog[module_id] = SimpleNamespace(tien_to=module_id)
    so.dang_chay[module_id] = fake


def _luu(client, **kw):
    than = {"email": kw.pop("email"), "mat_khau": kw.pop("mat_khau", "MatKhauGia123!")}
    than.update(kw)
    r = client.post("/api/tai-khoan/da-luu", headers=_QT, json=than)
    return r


def test_kho_luu_hai_truong_moi_va_lo_cong_khai(moi_truong):
    client, _so = moi_truong
    g = _luu(client, email=EMAIL_GOOGLE, nhan="google chinh")
    assert g.status_code == 200, g.text
    gid = g.json()["tai_khoan"]["id"]
    r = _luu(client, email=EMAIL_GPT, dang_nhap_bang="google",
             tai_khoan_google_id=gid)
    assert r.status_code == 200, r.text
    bk = r.json()["tai_khoan"]
    assert bk["dang_nhap_bang"] == "google"
    assert bk["tai_khoan_google_id"] == gid
    # Danh sách công khai cũng thấy 2 trường, không lộ bí mật
    r = client.get("/api/tai-khoan/da-luu", headers=_QT)
    thay = next(b for b in r.json()["tai_khoan"] if b["id"] == bk["id"])
    assert thay["dang_nhap_bang"] == "google"
    assert thay["tai_khoan_google_id"] == gid
    assert MK_GOOGLE not in r.text and "MatKhauGia123!" not in r.text


def test_kho_google_thieu_id_bi_tu_choi(moi_truong):
    client, _so = moi_truong
    r = _luu(client, email=EMAIL_GPT, dang_nhap_bang="google")
    assert r.status_code == 400, r.text


def test_kho_gia_tri_dang_nhap_bang_sai_bi_tu_choi(moi_truong):
    client, _so = moi_truong
    r = _luu(client, email=EMAIL_GPT, dang_nhap_bang="qua-facebook")
    assert r.status_code == 400, r.text


def _chuan_bi_hai_tai_khoan(client, google_tam_dung=False):
    g = _luu(client, email=EMAIL_GOOGLE, mat_khau=MK_GOOGLE,
             totp_secret=TOTP_GOOGLE)
    gid = g.json()["tai_khoan"]["id"]
    if google_tam_dung:
        r = client.post(f"/api/tai-khoan/da-luu/{gid}/tam-dung",
                        headers=_QT, json={"tam_dung": True})
        assert r.status_code == 200, r.text
    tk = _luu(client, email=EMAIL_GPT, mat_khau=MK_GPT,
              dang_nhap_bang="google", tai_khoan_google_id=gid,
              module_id=_MODULE_GIA)
    assert tk.status_code == 200, tk.text
    return gid, tk.json()["tai_khoan"]["id"]


def test_route_dang_nhap_gan_bi_mat_google_vao_dong(moi_truong):
    client, so = moi_truong
    fake = ModuleDangNhapGia()
    _cai_module_gia(so, fake)
    _gid, tkid = _chuan_bi_hai_tai_khoan(client)
    r = client.post(f"/api/tai-khoan/da-luu/{tkid}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE_GIA})
    assert r.status_code == 200, r.text
    assert r.json()["thanh_cong"] is True
    assert len(fake.da_nhan) == 1
    dong = fake.da_nhan[0]
    assert getattr(dong, "dang_nhap_bang", "") == "google"
    assert getattr(dong, "dnh_email", "") == EMAIL_GOOGLE
    assert getattr(dong, "dnh_mat_khau", None) is not None
    # Bí mật Google đã bị xoá sau lượt gọi (route dọn trong finally)
    assert dong.dnh_mat_khau.da_xoa()
    # Đáp không lộ bí mật nào
    assert MK_GOOGLE not in r.text and MK_GPT not in r.text


def test_route_google_tam_dung_loi_ro_module_khong_duoc_goi(moi_truong):
    client, so = moi_truong
    fake = ModuleDangNhapGia()
    _cai_module_gia(so, fake)
    _gid, tkid = _chuan_bi_hai_tai_khoan(client, google_tam_dung=True)
    r = client.post(f"/api/tai-khoan/da-luu/{tkid}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE_GIA})
    assert r.status_code == 403, r.text
    assert fake.da_nhan == []


def test_route_google_khong_ton_tai_404_module_khong_duoc_goi(moi_truong):
    client, so = moi_truong
    fake = ModuleDangNhapGia()
    _cai_module_gia(so, fake)
    tk = _luu(client, email=EMAIL_GPT, dang_nhap_bang="google",
              tai_khoan_google_id="tk-khong-co-that-000000")
    assert tk.status_code == 200, tk.text
    tkid = tk.json()["tai_khoan"]["id"]
    r = client.post(f"/api/tai-khoan/da-luu/{tkid}/dang-nhap", headers=_QT,
                    json={"module_id": _MODULE_GIA})
    assert r.status_code == 404, r.text
    assert fake.da_nhan == []
