"""Test NV271 — Chuỗi đăng nhập HTTP qua lõi thật + thống nhất luật JWT.

Mọi ca đi qua TestClient (HTTP) vào ứng dụng thật, dịch vụ đăng nhập là máy chủ giả.
Không Internet, không Chrome.

Bao gồm:
- A1: ChatGPT THẲNG (email + mật khẩu + TOTP qua refresh-jwt hoặc onboard openai-native)
- A2: ChatGPT QUA TÀI KHOẢN GOOGLE trong kho (két nhận bí mật Google, không lộ mật khẩu ChatGPT)
- A3: GROK THẲNG (qua grok-web chế độ truc_tiep, TrinhDuyetGia)
- A4: JWT qua HTTP (ChatGPT thẳng với refresh-jwt và token onboard kiểm thử đa dạng)
- A5: Nhất quán giữa hai nơi hiểu exp (may-dang-nhap và lam_moi_jwt)
- A6: Qua HTTP: GET /api/modules/{id}/jwt-trang-thai và đường làm mới với token không có exp
- BẢO MẬT: Kiểm tra leo thang quyền (401 không khoá, 403 khoá khách) và IDOR (404 id lạ)
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

GOC = Path(__file__).resolve().parents[1]
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))
if str(GOC / "test") not in sys.path:
    sys.path.insert(0, str(GOC / "test"))

from app.khoi_phuc_phien import TrinhDuyetGia
from app.main import app
from app.so_module import SoModule
from gia import MayChuThat, ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-nv271-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_KHACH = "khoa-khach-test-nv271-9876543210fedcba"
KHACH = {"Authorization": f"Bearer {KHOA_KHACH}"}

EMAIL_GPT = "chu.openai.goc@example.com"
MK_GPT = "mat-khau-openai-gia-01"
TOTP_GPT = "JBSWY3DPEHPK3PXP"

EMAIL_GOOGLE = "tai.khoan.google@example.com"
MK_GOOGLE = "mat-khau-google-gia-02"
TOTP_GOOGLE = "KRSXG5DSMFWHK3DMJBSWY3DPEHPK3PXQ"

EMAIL_GROK = "chu.grok@example.com"
MK_GROK = "mat-khau-grok-gia-03"
SSO_GROK = "sso-gia-ok-9999"
UID_GROK = "uid-gia-9999"


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt_gia(email: str = EMAIL_GPT, exp: int | None = None, extra: dict | None = None) -> str:
    payload = {"https://api.openai.com/profile": {"email": email}, "email": email}
    if exp is not None:
        payload["exp"] = exp
    else:
        payload["exp"] = int(time.time()) + 30 * 86400
    if extra:
        payload.update(extra)
    return f"aaa.{_b64url(payload)}.bbb"


def _jwt_gia_khong_exp(email: str = EMAIL_GPT) -> str:
    payload = {"https://api.openai.com/profile": {"email": email}, "email": email}
    return f"aaa.{_b64url(payload)}.bbb"


def _jwt_tu_payload_raw(payload_dict: dict) -> str:
    return f"aaa.{_b64url(payload_dict)}.bbb"


class ModuleSoHuuGia:
    """Module sở hữu giả có gan_jwt_tu_dang_nhap ghi lại token chuyển tiếp."""

    def __init__(self, id_="chatgpt-web"):
        self.id = id_
        self.jwt_da_nhan: list[dict] = []

    def gan_jwt_tu_dang_nhap(self, email: str, access_token: str, ho_so: str) -> dict:
        self.jwt_da_nhan.append({
            "email": email,
            "access_token": access_token,
            "ho_so": ho_so,
        })
        return {"ok": True}


class DichVuGiaNV271:
    """Dịch vụ đăng nhập giả ghi lại MỌI yêu cầu (đường + thân) để kiểm chứng."""

    def __init__(self):
        self.yeu_cau_da_nhan: list[dict] = []
        self.refresh_jwt_ok = True
        self.refresh_token_override: str | None = None
        self.token_override: str | None = None
        self.onboard_status: dict[str, str] = {}

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap", "status": "ok"})

        async def profiles(request):
            return JSONResponse({"ok": True, "profiles": []})

        async def luu_tai_khoan(request):
            than = await request.json()
            dv.yeu_cau_da_nhan.append({
                "duong": "/v1/accounts/saved",
                "phuong_thuc": "POST",
                "than": dict(than),
            })
            return JSONResponse({"ok": True, "email": than.get("email")})

        async def refresh_jwt(request):
            profile = request.path_params["profile"]
            dv.yeu_cau_da_nhan.append({
                "duong": f"/v1/chatgpt/{profile}/refresh-jwt",
                "phuong_thuc": "GET",
                "profile": profile,
            })
            if not dv.refresh_jwt_ok:
                return JSONResponse({"profile": profile, "ok": False, "error": "phiên chết"})
            token = dv.refresh_token_override or _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 30 * 86400)
            return JSONResponse({
                "profile": profile,
                "ok": True,
                "method": "scrape",
                "access_token": token,
                "captured_email": EMAIL_GPT,
            })

        async def openai_onboard(request):
            than = await request.json()
            dv.yeu_cau_da_nhan.append({
                "duong": "/v1/openai-native/onboard",
                "phuong_thuc": "POST",
                "than": dict(than),
            })
            return JSONResponse({"profile": than.get("profile"), "state": "starting", "ok": True})

        async def openai_status(request):
            profile = request.path_params["profile"]
            state = dv.onboard_status.get(profile, "success")
            return JSONResponse({"profile": profile, "state": state, "ok": True})

        async def openai_token(request):
            profile = request.path_params["profile"]
            dv.yeu_cau_da_nhan.append({
                "duong": f"/v1/openai-native/{profile}/token",
                "phuong_thuc": "GET",
                "profile": profile,
            })
            token = dv.token_override or _jwt_gia(EMAIL_GPT)
            return JSONResponse({"profile": profile, "email": EMAIL_GPT, "access_token": token, "ok": True})

        async def chatgpt_onboard(request):
            than = await request.json()
            dv.yeu_cau_da_nhan.append({
                "duong": "/v1/chatgpt/onboard",
                "phuong_thuc": "POST",
                "than": dict(than),
            })
            return JSONResponse({"profile": than.get("profile"), "state": "starting", "ok": True})

        async def chatgpt_status(request):
            profile = request.path_params["profile"]
            state = dv.onboard_status.get(profile, "success")
            return JSONResponse({"profile": profile, "state": state, "ok": True})

        async def auto_login_saved(request):
            than = await request.json()
            dv.yeu_cau_da_nhan.append({
                "duong": "/v1/session/auto-login-saved",
                "phuong_thuc": "POST",
                "than": dict(than),
            })
            return JSONResponse({"profile": than.get("profile"), "state": "starting", "ok": True})

        async def auto_login_status(request):
            profile = request.path_params["profile"]
            return JSONResponse({"profile": profile, "state": "success", "ok": True})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", luu_tai_khoan, methods=["POST"]),
            Route("/v1/chatgpt/{profile}/refresh-jwt", refresh_jwt),
            Route("/v1/chatgpt/onboard", chatgpt_onboard, methods=["POST"]),
            Route("/v1/chatgpt/{profile}/onboard-status", chatgpt_status),
            Route("/v1/openai-native/onboard", openai_onboard, methods=["POST"]),
            Route("/v1/openai-native/{profile}/onboard-status", openai_status),
            Route("/v1/openai-native/{profile}/token", openai_token),
            Route("/v1/session/auto-login-saved", auto_login_saved, methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", auto_login_status),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaNV271()
    with MayChuThat(dv.app()) as mc:
        dv.url = mc.url
        yield dv


@pytest.fixture(autouse=True)
def _don_trang_thai_dang_nhap_google():
    """Dọn trạng thái TOÀN CỤC của luồng Google trước và sau mỗi test."""
    def _don():
        for m in list(sys.modules.values()):
            if m is not None and hasattr(m, "_LAN_DANG_NHAP_GOOGLE_THEO_TK"):
                try:
                    m._LAN_DANG_NHAP_GOOGLE_THEO_TK.clear()
                    m._LAN_DANG_NHAP_GOOGLE_XONG = 0.0
                    m._CAPTCHA_HOLD_DEN = 0.0
                    m._CAPTCHA_HOLD_HO_SO = ""
                except Exception:
                    pass
    _don()
    yield
    _don()


@pytest.fixture
def moi_truong_http(tmp_path, monkeypatch, dich_vu_gia):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Chép module may-dang-nhap vào data_dir
    tep_mdn = {}
    for p in (GOC / "modules" / "may-dang-nhap").iterdir():
        if p.is_file():
            tep_mdn[p.name] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "may-dang-nhap", tep_mdn)

    # Chép module grok-web vào data_dir
    tep_gw = {}
    for p in (GOC / "modules" / "grok-web").iterdir():
        if p.is_file():
            tep_gw[p.name] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "grok-web", tep_gw)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so

        # Nạp lại module
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200, r.text

        # Cài may-dang-nhap trỏ máy chủ giả
        r = client.post("/api/modules/may-dang-nhap/cai", headers=QT, json={
            "cau_hinh": {
                "dich_vu_url": dich_vu_gia.url,
                "khoa_dich_vu": "khoa-dich-vu-gia"
            }
        })
        assert r.status_code == 200 and r.json().get("ok") is True, r.text

        # Cài grok-web ở chế độ trực tiếp
        r = client.post("/api/modules/grok-web/cai", headers=QT, json={
            "cau_hinh": {
                "tai_khoan": ["sso=sso-ban-dau; x-userid=uid-ban-dau"],
                "che_do_dang_nhap": "truc_tiep"
            }
        })
        assert r.status_code == 200 and r.json().get("ok") is True, r.text

        # Gắn module sở hữu giả chatgpt-web
        mod_so_huu = ModuleSoHuuGia("chatgpt-web")
        so.dang_chay["chatgpt-web"] = mod_so_huu
        so.catalog["chatgpt-web"] = SimpleNamespace(
            tien_to="chatgpt-web",
            manifest={"id": "chatgpt-web", "nhom": {"id": "chatgpt"}}
        )

        yield client, so, mod_so_huu, data_dir


# ═════════════════════════════════════════════════════════════════════════════
# A1: ChatGPT THẲNG (email + mật khẩu + TOTP)
# ═════════════════════════════════════════════════════════════════════════════

def test_a1_chatgpt_thang_refresh_jwt_con_han_khong_onboard(moi_truong_http, dich_vu_gia):
    """A1(i): refresh-jwt trả token còn hạn 30 ngày -> thanh_cong: true,
    dịch vụ KHÔNG nhận onboard, module sở hữu giả nhận đúng token."""
    client, so, mod_so_huu, _ = moi_truong_http
    token_30_ngay = _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 30 * 86400)
    dich_vu_gia.refresh_token_override = token_30_ngay

    # Lưu tài khoản ChatGPT trực tiếp
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "totp_secret": TOTP_GPT,
        "dang_nhap_bang": "truc_tiep",
        "module_id": "chatgpt-web"
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    # Đăng nhập bằng may-dang-nhap
    r = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True

    # Khẳng định dịch vụ KHÔNG nhận onboard
    cac_duong = [req["duong"] for req in dich_vu_gia.yeu_cau_da_nhan]
    assert "/v1/openai-native/onboard" not in cac_duong
    assert "/v1/chatgpt/onboard" not in cac_duong

    # Khẳng định module sở hữu giả nhận token
    assert len(mod_so_huu.jwt_da_nhan) == 1
    assert mod_so_huu.jwt_da_nhan[0]["access_token"] == token_30_ngay


def test_a1_chatgpt_thang_refresh_jwt_hong_chay_onboard_nhan_token(moi_truong_http, dich_vu_gia):
    """A1(ii): refresh-jwt hỏng (ok: false) -> dịch vụ nhận /v1/accounts/saved
    có loai: 'openai' và totp_secret là khoá TOTP gốc (không phải mã 6 số),
    rồi /v1/openai-native/onboard với hồ sơ có tiền tố openai-..., rồi đọc
    token ở /token -> thanh_cong: true, token đến module sở hữu giả."""
    client, so, mod_so_huu, _ = moi_truong_http
    dich_vu_gia.refresh_jwt_ok = False
    token_moi = _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 20 * 86400)
    dich_vu_gia.token_override = token_moi

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "totp_secret": TOTP_GPT,
        "dang_nhap_bang": "truc_tiep",
        "module_id": "chatgpt-web"
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    r = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True

    # Khẳng định dịch vụ nhận /v1/accounts/saved có loai: openai và totp_secret gốc
    saved_reqs = [req for req in dich_vu_gia.yeu_cau_da_nhan if req["duong"] == "/v1/accounts/saved"]
    assert len(saved_reqs) >= 1
    saved_body = saved_reqs[0]["than"]
    assert saved_body["loai"] == "openai"
    assert saved_body["totp_secret"] == TOTP_GPT
    assert saved_body["password"] == MK_GPT

    # Khẳng định dịch vụ nhận /v1/openai-native/onboard với profile theo tiền tố openai-
    onboard_reqs = [req for req in dich_vu_gia.yeu_cau_da_nhan if req["duong"] == "/v1/openai-native/onboard"]
    assert len(onboard_reqs) == 1
    assert onboard_reqs[0]["than"]["profile"].startswith("openai-")

    # Khẳng định token đến module sở hữu giả
    assert len(mod_so_huu.jwt_da_nhan) == 1
    assert mod_so_huu.jwt_da_nhan[0]["access_token"] == token_moi


def test_a1_chatgpt_thang_khong_lo_mat_khau_token_ra_phan_hoi(moi_truong_http, dich_vu_gia):
    """A1(iii): Phản hồi HTTP và mọi thứ đã ghi KHÔNG chứa mật khẩu giả
    ở dạng có thể đọc ngược ra ngoài phản hồi (văn bản phản hồi không chứa
    mật khẩu, không chứa token)."""
    client, so, mod_so_huu, _ = moi_truong_http
    token_bi_mat = _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 30 * 86400)
    dich_vu_gia.refresh_token_override = token_bi_mat

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "totp_secret": TOTP_GPT,
        "dang_nhap_bang": "truc_tiep",
        "module_id": "chatgpt-web"
    })
    tk_id = r.json()["tai_khoan"]["id"]

    r = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    # Khẳng định văn bản phản hồi không chứa mật khẩu và không chứa token
    assert MK_GPT not in r.text
    assert token_bi_mat not in r.text
    assert TOTP_GPT not in r.text


# ═════════════════════════════════════════════════════════════════════════════
# A2: ChatGPT QUA TÀI KHOẢN GOOGLE trong kho
# ═════════════════════════════════════════════════════════════════════════════

def test_a2_chatgpt_qua_google_dung_bi_mat_google_khong_lo_mat_khau_chatgpt(moi_truong_http, dich_vu_gia):
    """A2: Lưu tài khoản Google (email + mật khẩu + TOTP khác) rồi tài khoản
    ChatGPT dang_nhap_bang='google' + tai_khoan_google_id.
    Khẳng định:
      - dịch vụ nhận /v1/accounts/saved với loai: 'google' mang email + mk của tài khoản GOOGLE;
      - mật khẩu của chính tài khoản ChatGPT KHÔNG bao giờ xuất hiện trong bất kỳ yêu cầu nào;
      - /v1/chatgpt/onboard có reuse_session: true;
      - kết quả thanh_cong: true và token đến module sở hữu giả."""
    client, so, mod_so_huu, _ = moi_truong_http
    token_moi = _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 30 * 86400)
    dich_vu_gia.refresh_token_override = token_moi

    # Lưu tài khoản Google
    rg = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GOOGLE,
        "mat_khau": MK_GOOGLE,
        "totp_secret": TOTP_GOOGLE,
        "nhan": "google-chinh"
    })
    assert rg.status_code == 200, rg.text
    gid = rg.json()["tai_khoan"]["id"]

    # Lưu tài khoản ChatGPT đăng nhập bằng Google
    rc = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "dang_nhap_bang": "google",
        "tai_khoan_google_id": gid,
        "module_id": "chatgpt-web"
    })
    assert rc.status_code == 200, rc.text
    cid = rc.json()["tai_khoan"]["id"]

    # Đăng nhập qua may-dang-nhap
    r = client.post(f"/api/tai-khoan/da-luu/{cid}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True

    # Khẳng định dịch vụ nhận /v1/accounts/saved với loai 'google'
    saved_reqs = [req for req in dich_vu_gia.yeu_cau_da_nhan if req["duong"] == "/v1/accounts/saved"]
    assert len(saved_reqs) >= 1
    assert saved_reqs[0]["than"]["loai"] == "google"
    assert saved_reqs[0]["than"]["email"] == EMAIL_GOOGLE
    assert saved_reqs[0]["than"]["password"] == MK_GOOGLE

    # Mật khẩu của tài khoản ChatGPT KHÔNG BAO GIỜ xuất hiện trong bất kỳ yêu cầu nào
    for req in dich_vu_gia.yeu_cau_da_nhan:
        blob = json.dumps(req)
        assert MK_GPT not in blob

    # Khẳng định /v1/chatgpt/onboard có reuse_session: true
    onboard_reqs = [req for req in dich_vu_gia.yeu_cau_da_nhan if req["duong"] == "/v1/chatgpt/onboard"]
    assert len(onboard_reqs) == 1
    assert onboard_reqs[0]["than"].get("reuse_session") is True

    # Token đến module sở hữu giả
    assert len(mod_so_huu.jwt_da_nhan) == 1
    assert mod_so_huu.jwt_da_nhan[0]["access_token"] == token_moi


def test_a2_chatgpt_qua_google_tai_khoan_google_tam_dung_tra_403_khong_goi_dich_vu(moi_truong_http, dich_vu_gia):
    """A2 phụ: Tài khoản Google ở trạng thái tạm dừng -> 403 mà dịch vụ nhận 0 yêu cầu."""
    client, so, mod_so_huu, _ = moi_truong_http

    # Lưu tài khoản Google
    rg = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GOOGLE,
        "mat_khau": MK_GOOGLE,
        "totp_secret": TOTP_GOOGLE,
        "nhan": "google-chinh"
    })
    gid = rg.json()["tai_khoan"]["id"]

    # Lưu tài khoản ChatGPT đăng nhập qua Google
    rc = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "dang_nhap_bang": "google",
        "tai_khoan_google_id": gid,
        "module_id": "chatgpt-web"
    })
    cid = rc.json()["tai_khoan"]["id"]

    # Đặt tài khoản Google tạm dừng
    rt = client.post(f"/api/tai-khoan/da-luu/{gid}/tam-dung", headers=QT, json={"tam_dung": True})
    assert rt.status_code == 200, rt.text

    so_yeu_cau_truoc = len(dich_vu_gia.yeu_cau_da_nhan)

    # Đăng nhập ChatGPT -> mong đợi 403 tam_dung
    r = client.post(f"/api/tai-khoan/da-luu/{cid}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 403, r.text
    assert r.json().get("error", {}).get("code") == "tam_dung"

    # Dịch vụ nhận 0 yêu cầu mới
    assert len(dich_vu_gia.yeu_cau_da_nhan) == so_yeu_cau_truoc


# ═════════════════════════════════════════════════════════════════════════════
# A3: GROK THẲNG (chế độ truc_tiep qua grok-web, TrinhDuyetGia)
# ═════════════════════════════════════════════════════════════════════════════

def test_a3_grok_thang_che_do_truc_tiep_trinh_duyet_gia_thanh_cong(moi_truong_http):
    """A3: Grok thẳng chạy qua modules/grok-web ở chế độ truc_tiep,
    gài _tao_trinh_duyet bằng TrinhDuyetGia -> form được điền (email+mk),
    thanh_cong: true, cookie không rò ra phản hồi."""
    client, so, _, _ = moi_truong_http

    # Trình duyệt giả trả cookie đã có sso và x-userid (lay_uid_tu_phien không gọi mạng)
    td = TrinhDuyetGia(trang={
        "https://x.com/login": {
            "tin_hieu": ["thanh_cong"],
            "cookie": {"sso": SSO_GROK, "x-userid": UID_GROK},
        }
    })
    so.dang_chay["grok-web"]._tao_trinh_duyet = lambda: td

    # Lưu tài khoản Grok
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GROK,
        "mat_khau": MK_GROK,
        "module_id": "grok-web"
    })
    assert r.status_code == 200, r.text
    grok_tk_id = r.json()["tai_khoan"]["id"]

    # Đăng nhập với module_id="grok-web"
    r = client.post(f"/api/tai-khoan/da-luu/{grok_tk_id}/dang-nhap", headers=QT,
                    json={"module_id": "grok-web"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True

    # Khẳng định form được điền
    assert ("dien", "email") in td.hanh_dong
    assert ("dien", "mat_khau") in td.hanh_dong

    # Khẳng định cookie và mật khẩu không rò ra phản hồi HTTP
    assert SSO_GROK not in r.text
    assert UID_GROK not in r.text
    assert MK_GROK not in r.text


# ═════════════════════════════════════════════════════════════════════════════
# A4: JWT qua HTTP (ChatGPT thẳng, refresh-jwt và token onboard tuỳ biến)
# ═════════════════════════════════════════════════════════════════════════════

def test_a4_a_chatgpt_thang_jwt_khong_co_exp_thanh_cong(moi_truong_http, dich_vu_gia, caplog):
    """A4(a): refresh-jwt trả token KHÔNG có exp -> thanh_cong: true,
    KHÔNG gọi onboard, module sở hữu giả nhận token; token không lọt ra log/đáp."""
    client, so, mod_so_huu, _ = moi_truong_http
    token_khong_exp = _jwt_gia_khong_exp(EMAIL_GPT)
    dich_vu_gia.refresh_token_override = token_khong_exp

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "totp_secret": TOTP_GPT,
        "dang_nhap_bang": "truc_tiep",
        "module_id": "chatgpt-web"
    })
    tk_id = r.json()["tai_khoan"]["id"]

    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                        json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is True

    # KHÔNG gọi onboard
    cac_duong = [req["duong"] for req in dich_vu_gia.yeu_cau_da_nhan]
    assert "/v1/openai-native/onboard" not in cac_duong

    # Module sở hữu giả nhận token
    assert len(mod_so_huu.jwt_da_nhan) == 1
    assert mod_so_huu.jwt_da_nhan[0]["access_token"] == token_khong_exp

    # Token không xuất hiện trong phản hồi hay nhật ký
    assert token_khong_exp not in r.text
    assert token_khong_exp not in caplog.text


@pytest.mark.parametrize("ten_ca, token_loi", [
    ("payload_hong", "aaa.payload-khong-phai-base64.bbb"),
    ("exp_la_chuoi", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": "abc"})),
    ("exp_la_bool", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": True})),
    ("exp_het_han_42h", _jwt_gia(EMAIL_GPT, exp=int(time.time()) - 42 * 3600)),
    ("exp_duoi_nguong_1h", _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 3600)),
])
def test_a4_chatgpt_thang_jwt_loi_chay_onboard_nhung_that_bai(
        moi_truong_http, dich_vu_gia, caplog, ten_ca, token_loi):
    """A4(b, c, d, e, f): refresh-jwt trả token hỏng/sai kiểu/hết hạn -> bị
    từ chối ở refresh-jwt nên chạy tiếp onboard; token onboard cũng hỏng ->
    thanh_cong: false, module sở hữu giả KHÔNG nhận token; token không lọt ra log/đáp."""
    client, so, mod_so_huu, _ = moi_truong_http
    dich_vu_gia.refresh_token_override = token_loi
    dich_vu_gia.token_override = token_loi  # Cả token onboard cũng lỗi

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_GPT,
        "mat_khau": MK_GPT,
        "totp_secret": TOTP_GPT,
        "dang_nhap_bang": "truc_tiep",
        "module_id": "chatgpt-web"
    })
    tk_id = r.json()["tai_khoan"]["id"]

    with caplog.at_level(logging.DEBUG):
        r = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                        json={"module_id": "may-dang-nhap"})
    assert r.status_code == 200, r.text
    kq = r.json()
    assert kq["thanh_cong"] is False

    # Đã chạy tiếp onboard
    cac_duong = [req["duong"] for req in dich_vu_gia.yeu_cau_da_nhan]
    assert "/v1/openai-native/onboard" in cac_duong

    # Module sở hữu giả KHÔNG nhận token
    assert len(mod_so_huu.jwt_da_nhan) == 0

    # Token không rò ra phản hồi hay nhật ký
    assert token_loi not in r.text
    assert token_loi not in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# A5: Nhất quán giữa HAI nơi hiểu exp (may-dang-nhap và lam_moi_jwt)
# ═════════════════════════════════════════════════════════════════════════════

def _tai_mdn():
    spec = importlib.util.spec_from_file_location(
        "may_dang_nhap_mod_a5", str(GOC / "modules" / "may-dang-nhap" / "module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tai_lam_moi_jwt():
    spec = importlib.util.spec_from_file_location(
        "chatgpt_web_lam_moi_jwt_a5", str(GOC / "modules" / "chatgpt-web" / "lam_moi_jwt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("ten_ca, token, mong_doi_loai", [
    ("thieu_exp", _jwt_gia_khong_exp(EMAIL_GPT), "thieu"),
    ("payload_hong", "khong.phai.jwt", "hong"),
    ("exp_chuoi", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": "abc"}), "hong"),
    ("exp_bool", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": True}), "hong"),
    ("exp_am", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": -5}), "hong"),
    ("exp_khong", _jwt_tu_payload_raw({"email": EMAIL_GPT, "exp": 0}), "hong"),
    ("exp_hop_le_tuong_lai", _jwt_gia(EMAIL_GPT, exp=int(time.time()) + 30 * 86400), "co"),
    ("exp_hop_le_qua_khu", _jwt_gia(EMAIL_GPT, exp=int(time.time()) - 3600), "co"),
])
def test_a5_nhat_quan_hai_noi_hieu_exp(ten_ca, token, mong_doi_loai):
    """A5: Nhất quán giữa HAI nơi hiểu exp:
    - may-dang-nhap._jwt_con_han: thiếu exp = True; exp hỏng/sai = False.
    - lam_moi_jwt.phan_loai_exp: phân loại đúng thieu / hong / co.
    Trên mã CHƯA sửa, lam_moi_jwt chưa có phan_loai_exp hoặc coi thiếu exp là hỏng -> ĐỎ."""
    mdn = _tai_mdn()
    lmj = _tai_lam_moi_jwt()

    # 1. Kiểm tra phía may-dang-nhap
    con_han_mdn = mdn._jwt_con_han(token)
    if mong_doi_loai == "thieu":
        assert con_han_mdn is True, "may-dang-nhap: thiếu exp phải dùng được (True)"
    elif mong_doi_loai == "hong":
        assert con_han_mdn is False, "may-dang-nhap: token hỏng/sai kiểu phải bị từ chối (False)"
    elif ten_ca == "exp_hop_le_tuong_lai":
        assert con_han_mdn is True
    elif ten_ca == "exp_hop_le_qua_khu":
        assert con_han_mdn is False

    # 2. Kiểm tra phía lam_moi_jwt (đòi hỏi hàm phan_loai_exp theo đúng luật thống nhất)
    assert hasattr(lmj, "phan_loai_exp"), "lam_moi_jwt phải có hàm phan_loai_exp"
    loai_lmj, exp_lmj = lmj.phan_loai_exp(token)
    assert loai_lmj == mong_doi_loai, f"lam_moi_jwt phân loại sai ca {ten_ca}: {loai_lmj} != {mong_doi_loai}"
    if mong_doi_loai in ("thieu", "hong"):
        assert exp_lmj is None
    else:
        assert isinstance(exp_lmj, int) and exp_lmj > 0


# ═════════════════════════════════════════════════════════════════════════════
# A6: Qua HTTP: GET /api/modules/{id}/jwt-trang-thai và đường làm mới
# ═════════════════════════════════════════════════════════════════════════════

def _tai_goi_chatgpt_web():
    import types
    ten_goi = "chatgpt_web_nv271_pkg"
    thu_muc = GOC / "modules" / "chatgpt-web"
    for ten in [t for t in list(sys.modules) if t == ten_goi or t.startswith(ten_goi + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(ten_goi)
    goi.__path__ = [str(thu_muc)]
    sys.modules[ten_goi] = goi
    for ten_tep in ("client", "converter", "lam_moi_jwt", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{ten_goi}.{ten_tep}", thu_muc / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{ten_goi}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{ten_goi}.module"], sys.modules[f"{ten_goi}.lam_moi_jwt"]


def test_a6_chatgpt_web_token_khong_exp_khong_danh_dau_can_nguoi(tmp_path, monkeypatch):
    """A6: GET /api/modules/{id}/jwt-trang-thai và đường làm mới của chatgpt-web
    với token KHÔNG có exp:
      - GET /jwt-trang-thai trả canh_bao: false, het_han: null
      - lam_moi_jwt trả ok: true, ma: 'khong_co_exp', KHÔNG đánh dấu can_nguoi
    Trên mã CHƯA sửa, lam_moi đánh dấu can_nguoi và trả ma: 'exp_khong_doc_duoc' -> ĐỎ."""
    from app import kho

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    # Nạp module chatgpt-web như một gói
    mod_module, _ = _tai_goi_chatgpt_web()

    manifest = json.loads((GOC / "modules" / "chatgpt-web" / "manifest.json").read_text(encoding="utf-8"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("chatgpt-web")

    token_khong_exp = _jwt_gia_khong_exp(EMAIL_GPT)
    tk = mod_module._TaiKhoan(token_khong_exp)
    tk.email = EMAIL_GPT
    with m._khoa:
        m._tai_khoan = [tk]
        m._dich_vu_dang_nhap_url = "http://127.0.0.1:9"
        m._khoa_dich_vu_dang_nhap = "khoa-gia"

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        so.dang_chay["chatgpt-web"] = m
        so.catalog["chatgpt-web"] = SimpleNamespace(tien_to="chatgpt-web", manifest=manifest)

        # 1. Gọi GET /api/modules/chatgpt-web/jwt-trang-thai qua HTTP TestClient
        r = client.get("/api/modules/chatgpt-web/jwt-trang-thai", headers=QT)
        assert r.status_code == 200, r.text
        tt = r.json()
        assert len(tt["khoa"]) == 1
        muc_khoa = tt["khoa"][0]
        assert muc_khoa["canh_bao"] is False
        assert muc_khoa["het_han"] is None
        assert muc_khoa["con_lai_ngay"] is None

        # 2. Gọi đường làm mới cho khoá
        khoa = m._khoa_tu_token(token_khong_exp)
        kq = asyncio.run(m.lam_moi_jwt(khoa))

        # Khẳng định: thiếu exp -> ok: True, ma: khong_co_exp, KHÔNG can_nguoi
        assert kq.get("ok") is True, f"Kỳ vọng ok=True nhưng nhận được {kq}"
        assert kq.get("ma") == "khong_co_exp", f"Kỳ vọng ma='khong_co_exp' nhưng nhận được {kq.get('ma')}"
        assert kq.get("can_nguoi") is not True

        # Sổ trạng thái không bị đánh dấu can_nguoi
        tt_sau = m._doc_trang_thai_jwt().get(khoa, {})
        assert tt_sau.get("trang_thai") != "can_nguoi"

        # Gọi lại GET qua HTTP: canh_bao vẫn là False
        r2 = client.get("/api/modules/chatgpt-web/jwt-trang-thai", headers=QT)
        assert r2.status_code == 200
        assert r2.json()["khoa"][0]["canh_bao"] is False


# ═════════════════════════════════════════════════════════════════════════════
# BẢO MẬT: Kiểm tra leo thang quyền & IDOR
# ═════════════════════════════════════════════════════════════════════════════

def test_bao_mat_dang_nhap_khong_khoa_tra_401(moi_truong_http):
    """Bảo mật: POST /api/tai-khoan/da-luu/{id}/dang-nhap không khoá -> 401."""
    client, _, _, _ = moi_truong_http
    r = client.post("/api/tai-khoan/da-luu/tk-bat-ky/dang-nhap", json={"module_id": "may-dang-nhap"})
    assert r.status_code == 401, r.text


def test_bao_mat_dang_nhap_khoa_khach_tra_403(moi_truong_http):
    """Bảo mật: POST /api/tai-khoan/da-luu/{id}/dang-nhap bằng khoá khách -> 403."""
    client, _, _, _ = moi_truong_http
    r_kk = client.post("/api/khoa-khach", headers=QT, json={"nhan": "test-nv271", "pham_vi": ["chat"]})
    assert r_kk.status_code == 200, r_kk.text
    khoa_khach = r_kk.json()["khoa"]["khoa"]
    r = client.post("/api/tai-khoan/da-luu/tk-bat-ky/dang-nhap", headers={"Authorization": f"Bearer {khoa_khach}"},
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 403, r.text


def test_bao_mat_dang_nhap_idor_tai_khoan_la_tra_404(moi_truong_http):
    """Bảo mật: POST /api/tai-khoan/da-luu/{id}/dang-nhap với ID không tồn tại -> 404."""
    client, _, _, _ = moi_truong_http
    r = client.post("/api/tai-khoan/da-luu/id-khong-ton-tai-000/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap"})
    assert r.status_code == 404, r.text


def test_bao_mat_jwt_trang_thai_khong_khoa_tra_401(moi_truong_http):
    """Bảo mật: GET /api/modules/{id}/jwt-trang-thai không khoá -> 401."""
    client, _, _, _ = moi_truong_http
    r = client.get("/api/modules/chatgpt-web/jwt-trang-thai")
    assert r.status_code == 401, r.text


def test_bao_mat_jwt_trang_thai_khoa_khach_tra_403(moi_truong_http):
    """Bảo mật: GET /api/modules/{id}/jwt-trang-thai bằng khoá khách -> 403."""
    client, _, _, _ = moi_truong_http
    r_kk = client.post("/api/khoa-khach", headers=QT, json={"nhan": "test-nv271", "pham_vi": ["chat"]})
    assert r_kk.status_code == 200, r_kk.text
    khoa_khach = r_kk.json()["khoa"]["khoa"]
    r = client.get("/api/modules/chatgpt-web/jwt-trang-thai", headers={"Authorization": f"Bearer {khoa_khach}"})
    assert r.status_code == 403, r.text
