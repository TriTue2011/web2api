# test_nv271_dnt_claude_http.py — Test HTTP qua lõi thật cho luồng Claude đăng nhập thẳng bằng email.
# NV271: Ô 2 — Claude email onboard (C1, C2, C3, C4, C5 + leo thang quyền / IDOR).
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
GOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC / "test"))

from gia import MayChuThat, ghi_thu_muc  # noqa: E402

KHOA_QT = "khoa-qt-nv271-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
EMAIL_CLAUDE = "chu.claude@example.com"
MK_CLAUDE = "mat-khau-gia-claude-01"
TOTP_CLAUDE = "JBSWY3DPEHPK3PXP"


class DichVuGiaClaude:
    """Dịch vụ giả mô phỏng may-dang-nhap cho Claude email và Google."""

    def __init__(self):
        self.yeu_cau_onboard_email = []
        self.yeu_cau_onboard = []
        self.yeu_cau_saved = []
        self.yeu_cau_2fa = []
        self.trang_thai_theo_ho_so = {}
        self.moi_hoi_lan_dau = set()

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap", "status": "ok"})

        async def profiles(request):
            return JSONResponse({"ok": True, "profiles": []})

        async def saved_accounts(request):
            body = await request.json()
            dv.yeu_cau_saved.append(body)
            return JSONResponse({"ok": True, "email": body.get("email")})

        async def onboard_email(request):
            body = await request.json()
            dv.yeu_cau_onboard_email.append(body)
            hs = body.get("profile", "")
            state = dv.trang_thai_theo_ho_so.get(hs, "need_code")
            return JSONResponse({
                "ok": True,
                "profile": hs,
                "state": "starting" if state != "success" else "success",
                "message": "Khởi tạo onboard email",
            })

        async def onboard_google(request):
            body = await request.json()
            dv.yeu_cau_onboard.append(body)
            hs = body.get("profile", "")
            return JSONResponse({
                "ok": True,
                "profile": hs,
                "state": "starting",
                "message": "Khởi tạo onboard google",
            })

        async def onboard_status(request):
            hs = request.path_params.get("profile", "")
            if hs not in dv.moi_hoi_lan_dau:
                dv.moi_hoi_lan_dau.add(hs)
                st = dv.trang_thai_theo_ho_so.get(hs, "need_code")
            else:
                st = dv.trang_thai_theo_ho_so.get(hs, "need_code")
            return JSONResponse({
                "ok": True,
                "profile": hs,
                "state": st,
                "message": f"Trạng thái {st}",
                "error": "",
            })

        async def onboard_2fa(request):
            hs = request.path_params.get("profile", "")
            body = await request.json()
            dv.yeu_cau_2fa.append((hs, body))
            return JSONResponse({"ok": True, "profile": hs, "submitted": True})

        async def session_cookie(request):
            hs = request.path_params.get("profile", "")
            return JSONResponse({"ok": True, "profile": hs, "session_key": "phien-gia-nv271-thu"})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", saved_accounts, methods=["POST"]),
            Route("/v1/claude-web/onboard-email", onboard_email, methods=["POST"]),
            Route("/v1/claude-web/onboard", onboard_google, methods=["POST"]),
            Route("/v1/claude-web/{profile}/onboard-status", onboard_status),
            Route("/v1/claude-web/{profile}/onboard-2fa-code", onboard_2fa, methods=["POST"]),
            Route("/v1/claude-web/{profile}/session", session_cookie),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaClaude()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


@pytest.fixture
def moi_truong_loi(tmp_path, monkeypatch, dich_vu_gia):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.so_module import SoModule

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setenv("C2A_DN_CHO_DANG_NHAP_GIAY", "25")
    monkeypatch.setenv("C2A_DN_NGHI_LUY_THUA_GIAY", "0")
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    tep = {}
    for mod_name in ("may-dang-nhap", "claude-web", "grok-web"):
        mod_src = GOC / "modules" / mod_name
        if mod_src.is_dir():
            tep_mod = {p.name: p.read_bytes() for p in mod_src.iterdir() if p.is_file()}
            ghi_thu_muc(data_dir / "modules" / mod_name, tep_mod)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so

        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200, r.text
        r = client.post("/api/modules/may-dang-nhap/cai", headers=QT, json={
            "cau_hinh": {"dich_vu_url": dich_vu_gia.url, "khoa_dich_vu": "khoa-gia"}})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True, r.text

        # Mock thêm phương thức gan_jwt_tu_dang_nhap trên module claude-web đang chạy
        mod_claude = so.dang_chay.get("claude-web")
        if mod_claude is not None:
            mod_claude.da_nhan_jwt = []
            mod_claude.gan_jwt_tu_dang_nhap = lambda e, t, h: mod_claude.da_nhan_jwt.append((e, t, h)) or {"ok": True}

        # Tạo sẵn một khoá khách cho bài kiểm leo thang quyền
        from app.khoa_khach import tao_khoa
        _, khoa_khach_tho = tao_khoa(nhan="Khach test", pham_vi=["chat"])
        client.khoa_khach_tho = khoa_khach_tho

        yield client, so


def test_c1_claude_thang_need_code_roi_nhap_ma_2fa(moi_truong_loi, dich_vu_gia):
    """C1: Claude thẳng, dịch vụ trả need_code → cho_2fa: True, đúng 1 req onboard-email,
    không chạm bí mật, không lưu két. Sau đó nhập mã 2FA → dịch vụ nhận mã."""
    client, so = moi_truong_loi

    # Lưu tài khoản Claude với dang_nhap_bang = "truc_tiep"
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_CLAUDE,
        "mat_khau": MK_CLAUDE,
        "totp_secret": TOTP_CLAUDE,
        "module_id": "claude-web",
        "dang_nhap_bang": "truc_tiep",
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    # Đăng nhập
    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    kq = r_dn.json()
    assert kq["thanh_cong"] is False
    assert kq["cho_2fa"] is True
    assert "google" not in kq["huong_dan"].lower()

    # Dịch vụ giả nhận ĐÚNG 1 request onboard-email với chỉ profile + email
    assert len(dich_vu_gia.yeu_cau_onboard_email) == 1
    than_ob = dich_vu_gia.yeu_cau_onboard_email[0]
    assert set(than_ob.keys()) == {"profile", "email"}
    assert than_ob["email"] == EMAIL_CLAUDE
    # 0 yêu cầu /v1/accounts/saved
    assert len(dich_vu_gia.yeu_cau_saved) == 0
    # Mật khẩu không xuất hiện ở request hay response
    assert MK_CLAUDE not in json.dumps(than_ob)
    assert MK_CLAUDE not in r_dn.text

    # Nhập mã 2FA
    r_2fa = client.post(f"/api/tai-khoan/da-luu/{tk_id}/ma-2fa", headers=QT,
                        json={"module_id": "may-dang-nhap", "ma": "654321"})
    assert r_2fa.status_code == 200, r_2fa.text
    assert r_2fa.json()["thanh_cong"] is True
    assert len(dich_vu_gia.yeu_cau_2fa) == 1
    hs_nhan, body_2fa = dich_vu_gia.yeu_cau_2fa[0]
    assert hs_nhan == than_ob["profile"]
    assert body_2fa.get("code") == "654321"

    # Chuyển trạng thái dịch vụ sang success
    dich_vu_gia.trang_thai_theo_ho_so[than_ob["profile"]] = "success"
    r_dn2 = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                        json={"module_id": "may-dang-nhap"})
    assert r_dn2.status_code == 200, r_dn2.text
    assert r_dn2.json()["thanh_cong"] is True


def test_c2_claude_thang_da_co_phien_success_ngay(moi_truong_loi, dich_vu_gia):
    """C2: Claude thẳng, hồ sơ đã có phiên (dịch vụ trả success ngay) → thanh_cong: True."""
    client, so = moi_truong_loi

    # Cấu hình dịch vụ trả success ngay
    hs_mong = "claude-chu-claude"
    dich_vu_gia.trang_thai_theo_ho_so[hs_mong] = "success"

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_CLAUDE,
        "mat_khau": MK_CLAUDE,
        "module_id": "claude-web",
        "dang_nhap_bang": "truc_tiep",
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    assert r_dn.json()["thanh_cong"] is True
    assert r_dn.json()["cho_2fa"] is False


def test_c3_claude_qua_google_hoi_quy(moi_truong_loi, dich_vu_gia):
    """C3: Claude qua Google vẫn đi đường cũ (/v1/claude-web/onboard + /v1/accounts/saved loai google)."""
    client, so = moi_truong_loi

    # Tạo tài khoản Google đăng nhập hộ
    r_g = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": "tai.khoan.google@example.com",
        "mat_khau": "mat-khau-google-01",
        "dang_nhap_bang": "truc_tiep",
    })
    assert r_g.status_code == 200, r_g.text
    tk_g_id = r_g.json()["tai_khoan"]["id"]

    # Tài khoản Claude qua Google
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_CLAUDE,
        "mat_khau": MK_CLAUDE,
        "module_id": "claude-web",
        "dang_nhap_bang": "google",
        "tai_khoan_google_id": tk_g_id,
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    dich_vu_gia.trang_thai_theo_ho_so["claude-chu-claude"] = "success"
    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    # Phải đi qua onboard Google và accounts/saved
    assert len(dich_vu_gia.yeu_cau_onboard) == 1
    assert len(dich_vu_gia.yeu_cau_saved) == 1
    assert dich_vu_gia.yeu_cau_saved[0].get("loai") == "google"
    # KHÔNG gọi onboard-email
    assert len(dich_vu_gia.yeu_cau_onboard_email) == 0


def test_c4_cap_khong_ho_tro_grok_truc_tiep_tu_choi(moi_truong_loi, dich_vu_gia):
    """C4: Cặp chưa hỗ trợ (grok + truc_tiep) bị từ chối trước khi chạm bí mật: can_nguoi: True, ma_loi: khong_ho_tro."""
    client, so = moi_truong_loi

    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": "chu.grok@example.com",
        "mat_khau": "mat-khau-grok-01",
        "module_id": "grok-web",
        "dang_nhap_bang": "truc_tiep",
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    kq = r_dn.json()
    assert kq["thanh_cong"] is False
    assert kq["can_nguoi"] is True
    assert kq["ma_loi"] == "khong_ho_tro"
    # Dịch vụ giả 0 yêu cầu
    assert len(dich_vu_gia.yeu_cau_onboard_email) == 0
    assert len(dich_vu_gia.yeu_cau_onboard) == 0
    assert len(dich_vu_gia.yeu_cau_saved) == 0


def test_c5_claude_khong_totp_van_dang_nhap_duoc(moi_truong_loi, dich_vu_gia):
    """C5: Tài khoản Claude lưu KHÔNG kèm TOTP vẫn đăng nhập thẳng được."""
    client, so = moi_truong_loi

    dich_vu_gia.trang_thai_theo_ho_so["claude-chu-claude"] = "success"
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL_CLAUDE,
        "mat_khau": MK_CLAUDE,
        # totp_secret omitted
        "module_id": "claude-web",
        "dang_nhap_bang": "truc_tiep",
    })
    assert r.status_code == 200, r.text
    tk_id = r.json()["tai_khoan"]["id"]

    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    assert r_dn.json()["thanh_cong"] is True


def test_bao_mat_leo_thang_quyen_va_idor(moi_truong_loi):
    """Kiểm tra LEO THANG QUYỀN và IDOR: không khoá → 401, vai thấp → 403, ID lạ → 404."""
    client, so = moi_truong_loi

    # 1. Không có Authorization header → 401
    r_no_auth = client.post("/api/tai-khoan/da-luu/tk-khong-ton-tai/dang-nhap",
                            json={"module_id": "may-dang-nhap"})
    assert r_no_auth.status_code == 401

    # 2. Khoá khách (vai thấp) gọi đường quản trị → 403
    r_khach = client.post("/api/tai-khoan/da-luu/tk-khong-ton-tai/dang-nhap",
                          headers={"Authorization": f"Bearer {client.khoa_khach_tho}"},
                          json={"module_id": "may-dang-nhap"})
    assert r_khach.status_code == 403

    # 3. IDOR: ID không tồn tại hoặc ID tài khoản của người khác → 404
    r_idor = client.post("/api/tai-khoan/da-luu/id-gia-khong-ton-tai-xyz/dang-nhap",
                         headers=QT, json={"module_id": "may-dang-nhap"})
    assert r_idor.status_code == 404
