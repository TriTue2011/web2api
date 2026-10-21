# NV210 — BƯỚC 1 (đo, đỏ trước) + kiểm đỏ→xanh.
#
# Đăng nhập ChatGPT Web từ «Tài khoản đã lưu» (email + mật khẩu + OTP):
# module may-dang-nhap chọn luồng /v1/chatgpt/onboard theo module_id của tài
# khoản (DỮ LIỆU), không đoán theo chuỗi email; thành công thì lấy JWT mới qua
# refresh-jwt, đưa vào khóa chatgpt-web (ghép bằng claim email trong JWT) và
# gắn ho_so để NV207 làm mới được về sau.
"""BƯỚC 1 — đo qua lõi thật + dịch vụ GIẢ (kiểm tên hồ sơ nghiêm như thật)."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import re
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
from gia import MayChuThat  # noqa: E402 (như test_nv145b_may_dang_nhap.py)


def _tai_module_mdn():
    spec = importlib.util.spec_from_file_location(
        "may_dang_nhap_mod_nv210", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _tai_module_mdn()
EMAIL = "nguoi.dung@example.com"
MAT_KHAU = "mat-khau-gia-nv210-01"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _jwt_gia(email: str) -> str:
    payload = {"https://api.openai.com/profile": {"email": email},
               "exp": int(time.time()) + 30 * 86400}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"aaa.{b64}.bbb"


class DichVuGiaChatGPT:
    """Dịch vụ may-dang-nhap giả: luồng ChatGPT + kiểm slug nghiêm như thật."""

    def __init__(self):
        self.che_do = "thanh_cong"
        self.da_luu = []            # thân POST /v1/accounts/saved
        self.onboard_nhan = []      # thân POST /v1/chatgpt/onboard
        self.trang_thai_hoi = []    # profile hỏi onboard-status
        self.ma_2fa = []            # (profile, code)
        self.google_bat_dau = []    # thân POST /v1/session/auto-login-saved
        self.jwt_lay = []           # profile gọi refresh-jwt
        self.so_lan_goi = 0
        self._chuoi = []
        self._chi_so = 0

    def _dat_chuoi(self):
        che_do = self.che_do
        if che_do == "thanh_cong":
            self._chuoi = ["starting", "running", "success"]
        elif che_do == "can_2fa":
            self._chuoi = ["starting", "need_code"]
        elif che_do == "sai_mat_khau":
            self._chuoi = ["starting", "running", "failed"]
        elif che_do == "loi_tam_thoi":
            self._chuoi = ["starting", "running", "failed"]
        elif che_do == "dang_chay_mai":
            self._chuoi = ["starting", "running"]
        else:
            self._chuoi = ["starting", "failed"]
        self._chi_so = 0

    def _tiep(self):
        if self._chi_so < len(self._chuoi) - 1:
            self._chi_so += 1
        return self._chuoi[self._chi_so]

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap",
                                 "status": "ok"})

        async def profiles(request):
            return JSONResponse({"profiles": [], "ok": True})

        async def luu_tai_khoan(request):
            than = await request.json()
            dv.da_luu.append(than)
            dv.so_lan_goi += 1
            return JSONResponse({"email": than.get("email"), "ok": True})

        async def onboard(request):
            than = await request.json()
            dv._dat_chuoi()
            dv.so_lan_goi += 1
            dv.onboard_nhan.append(than)
            # NHƯ DỊCH VỤ THẬT (browser_pool._PROFILE_SLUG): chỉ chữ/số/-/_ ≤ 64
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(than.get("profile") or "")):
                return JSONResponse({"profile": than.get("profile"), "state": "failed",
                                     "message": "", "error": "Tên profile không hợp lệ",
                                     "ok": True})
            return JSONResponse({"profile": than.get("profile"),
                                 "state": "starting", "message": "Khởi tạo", "ok": True})

        async def trang_thai(request):
            tt = dv._tiep()
            dv.so_lan_goi += 1
            dv.trang_thai_hoi.append(request.path_params["profile"])
            tra = {"profile": request.path_params["profile"], "state": tt,
                   "message": f"giả: {tt}", "error": "", "ok": True}
            if tt == "failed" and dv.che_do == "sai_mat_khau":
                tra["error"] = "Sai mật khẩu — OpenAI từ chối"
            if tt == "failed" and dv.che_do == "loi_tam_thoi":
                tra["error"] = "Lỗi mạng nội bộ thoáng qua"
            return JSONResponse(tra)

        async def ma_2fa(request):
            than = await request.json()
            dv.ma_2fa.append((request.path_params["profile"], than.get("code")))
            dv.so_lan_goi += 1
            return JSONResponse({"submitted": True, "ok": True})

        async def refresh_jwt(request):
            dv.jwt_lay.append(request.path_params["profile"])
            dv.so_lan_goi += 1
            # captured_email của dịch vụ thật đến từ scrape trang — có thể
            # không đáng tin; mã phải ưu tiên claim email TRONG JWT.
            return JSONResponse({"profile": request.path_params["profile"],
                                 "ok": True, "method": "scrape",
                                 "access_token": _jwt_gia(EMAIL),
                                 "access_token_preview": "aaa…bbb",
                                 "captured_email": "sai-lech@example.com"})

        async def google_bat_dau(request):
            than = await request.json()
            dv._dat_chuoi()
            dv.so_lan_goi += 1
            dv.google_bat_dau.append(than)
            return JSONResponse({"profile": than.get("profile"),
                                 "state": "starting", "message": "Khởi tạo", "ok": True})

        async def google_trang_thai(request):
            tt = dv._tiep()
            dv.so_lan_goi += 1
            # NHƯ DỊCH VỤ THẬT: kho phiên Google không có hồ sơ chatgpt-*
            # → không thể trả success cho nhầm luồng.
            if str(request.path_params["profile"]).startswith("chatgpt-"):
                tt = "none"
            return JSONResponse({"profile": request.path_params["profile"], "state": tt,
                                 "message": f"giả: {tt}", "error": "", "ok": True})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", luu_tai_khoan, methods=["POST"]),
            Route("/v1/chatgpt/onboard", onboard, methods=["POST"]),
            Route("/v1/chatgpt/{profile}/onboard-status", trang_thai),
            Route("/v1/chatgpt/{profile}/onboard-2fa-code", ma_2fa, methods=["POST"]),
            Route("/v1/chatgpt/{profile}/refresh-jwt", refresh_jwt),
            Route("/v1/session/auto-login-saved", google_bat_dau, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", google_trang_thai),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaChatGPT()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


@pytest.fixture
def mod(dich_vu_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    manifest = {"tien_to": "may_dang_nhap", "ten": "may-dang-nhap (test)"}
    m = MOD.Module(manifest)
    m._url = dich_vu_gia.url
    m._khoa = "khoa-gia"
    return m


def _dong(email=EMAIL, mk=MAT_KHAU, totp=TOTP_SECRET, module_cua_tai_khoan="chatgpt-web"):
    d = DongDaTach(dong=0, email=email, loai="mat_khau",
                   mat_khau=BiMatTam(mk),
                   totp=BiMatTotp(totp) if totp else None)
    d.module_cua_tai_khoan = module_cua_tai_khoan
    return d


def _chay(coro):
    return asyncio.run(coro)


# ── 1. Chọn luồng theo module_id của tài khoản (dữ liệu), không đoán chuỗi ────

def test_luong_chatgpt_theo_module_id(mod, dich_vu_gia):
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    assert len(dich_vu_gia.onboard_nhan) == 1
    assert dich_vu_gia.google_bat_dau == []
    than = dich_vu_gia.onboard_nhan[0]
    assert than["profile"] == "chatgpt-nguoi-dung"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", than["profile"])
    assert than["email"] == EMAIL
    assert dich_vu_gia.da_luu and dich_vu_gia.da_luu[0].get("loai") == "openai"


def test_khong_doan_luong_theo_chuoi_email(mod, dich_vu_gia):
    # email chứa chuỗi "chatgpt" nhưng module_id rỗng → vẫn luồng Google
    kq = _chay(mod.dang_nhap(_dong(email="nguoi.chatgpt@example.com",
                                   module_cua_tai_khoan="")))
    assert kq.thanh_cong
    assert dich_vu_gia.onboard_nhan == []
    assert len(dich_vu_gia.google_bat_dau) == 1
    # và ngược lại: module_id=chatgpt-web với email gmail trần → luồng ChatGPT
    kq2 = _chay(mod.dang_nhap(_dong(email="ai.b@example.com",
                                    module_cua_tai_khoan="chatgpt-web")))
    assert kq2.thanh_cong
    assert len(dich_vu_gia.onboard_nhan) == 1
    assert dich_vu_gia.onboard_nhan[0]["profile"] == "chatgpt-ai-b"


def test_ten_ho_so_chatgpt_hop_le_voi_email_kho(mod, dich_vu_gia):
    kq = _chay(mod.dang_nhap(_dong(email="nguoi.e+nhan@example.com")))
    assert kq.thanh_cong, getattr(kq, "ma_loi", "")
    hs = dich_vu_gia.onboard_nhan[0]["profile"]
    assert hs == "chatgpt-nguoi-e-nhan"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", hs)


# ── 2. Thành công: JWT mới + ho_so gắn vào khóa (ghép bằng claim email) ───────

def test_thanh_cong_gan_jwt_va_ho_so(mod, dich_vu_gia):
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong
    jwt_moi = getattr(kq, "jwt_moi", None)
    assert isinstance(jwt_moi, dict)
    assert jwt_moi["ho_so"] == "chatgpt-nguoi-dung"
    assert dich_vu_gia.jwt_lay == ["chatgpt-nguoi-dung"]
    # claim email giải được từ token (dữ liệu chắc chắn để ghép khóa)
    from modules_chatgpt_web_helper import email_cua  # noqa
    assert email_cua(jwt_moi["access_token"]) == EMAIL
    # email ghép khóa phải lấy từ CLAIM trong JWT, không tin trường
    # captured_email của dịch vụ (fake cố tình trả lệch)
    assert jwt_moi["email"] == EMAIL


def test_ket_qua_khong_chua_bi_mat_tran(mod, dich_vu_gia, caplog):
    with caplog.at_level(logging.DEBUG):
        kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert MAT_KHAU not in nhat_ky
    assert TOTP_SECRET not in nhat_ky
    token = getattr(kq, "jwt_moi", {}).get("access_token", "")
    assert token and token not in nhat_ky


# ── 3. Lỗi vĩnh viễn → cần người, không thử lại ───────────────────────────────

def test_loi_vinh_vien_chatgpt_can_nguoi(mod, dich_vu_gia):
    dich_vu_gia.che_do = "sai_mat_khau"
    kq = _chay(mod.dang_nhap(_dong()))
    assert not kq.thanh_cong
    assert getattr(kq, "can_nguoi", False)
    assert getattr(kq, "ma_loi", "") == "sai_mat_khau"
    so_lan = dich_vu_gia.so_lan_goi
    kq2 = _chay(mod.dang_nhap(_dong()))
    assert getattr(kq2, "can_nguoi", False)
    assert dich_vu_gia.so_lan_goi == so_lan  # không gọi dịch vụ nữa


def test_loi_tam_thoi_chatgpt_duoc_thu_lai(mod, dich_vu_gia):
    dich_vu_gia.che_do = "loi_tam_thoi"
    kq = _chay(mod.dang_nhap(_dong()))
    assert not kq.thanh_cong
    assert not getattr(kq, "can_nguoi", False)
    assert getattr(kq, "ma_loi", "") == "dang_nhap_that_bai"


# ── 4. 2FA + tiến độ theo đúng hợp đồng NV145b ────────────────────────────────

def test_2fa_va_tien_do_luong_chatgpt(mod, dich_vu_gia):
    dich_vu_gia.che_do = "can_2fa"
    kq = _chay(mod.dang_nhap(_dong()))
    assert getattr(kq, "cho_2fa", False)
    r = _chay(mod.nhap_ma_2fa(EMAIL, BiMatTam("123456"),
                              module_cua_tai_khoan="chatgpt-web"))
    assert r.get("ok")
    assert dich_vu_gia.ma_2fa == [("chatgpt-nguoi-dung", "123456")]
    td = _chay(mod.tien_do(EMAIL, module_cua_tai_khoan="chatgpt-web"))
    assert td["giai_doan"] in ("cho_2fa", "dang_chay")
    assert td["cho_2fa"] or td["giai_doan"] == "dang_chay"


def test_tien_do_thanh_cong_luong_chatgpt(mod, dich_vu_gia):
    kq = _chay(mod.dang_nhap(_dong()))
    assert kq.thanh_cong
    td = _chay(mod.tien_do(EMAIL, module_cua_tai_khoan="chatgpt-web"))
    assert td["giai_doan"] == "thanh_cong"


# ── 5. Lõi không nhắc tên module/nhà cung cấp (doc_lap) ───────────────────────

def test_loi_khong_nhac_ten_module_nha_cung_cap():
    for ten in ("app/tuyen.py", "app/tai_khoan_hang_loat.py"):
        noi_dung = (GOC / ten).read_text(encoding="utf-8")
        assert '"chatgpt-web"' not in noi_dung, ten
        assert "'chatgpt-web'" not in noi_dung, ten
        assert '"chatgpt"' not in noi_dung, ten
        assert "'chatgpt'" not in noi_dung, ten
