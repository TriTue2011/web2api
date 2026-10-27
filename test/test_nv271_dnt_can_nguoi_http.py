# test_nv271_dnt_can_nguoi_http.py — Test HTTP qua lõi thật cho ma trận Ô 4.
# Dừng ở «cần người» khi gặp thử thách/xác minh (need_captcha, need_human, need_manual)
# trên 6 đường: chatgpt, openai, claude-web, claude-web-email, grok-web, auto-login-saved.
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

KHOA_QT = "khoa-qt-nv271-can-nguoi-0123456789"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MK_GIA = "mat-khau-gia-can-nguoi-01"


class DichVuGiaCanNguoi:
    """Dịch vụ giả phục vụ ma trận 6 đường onboard × 3 trạng thái cần người."""

    def __init__(self):
        self.so_lan_bat_dau = {}  # duong -> count
        self.so_lan_solve = 0
        self.trang_thai_tra = "need_manual"
        self.ma_loi_tra = ""
        self.cac_than_nhan = []

    def app(self):
        dv = self

        async def health(request):
            return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap", "status": "ok"})

        async def profiles(request):
            return JSONResponse({"ok": True, "profiles": []})

        async def accounts_saved(request):
            body = await request.json()
            return JSONResponse({"ok": True, "email": body.get("email")})

        def _tao_bat_dau(ten_duong):
            async def bat_dau(request):
                dv.so_lan_bat_dau[ten_duong] = dv.so_lan_bat_dau.get(ten_duong, 0) + 1
                body = await request.json()
                dv.cac_than_nhan.append((ten_duong, body))
                hs = body.get("profile", "")
                return JSONResponse({
                    "ok": True,
                    "profile": hs,
                    "state": "starting",
                    "message": "Bắt đầu onboard",
                })
            return bat_dau

        def _tao_trang_thai(ten_duong):
            async def trang_thai(request):
                hs = request.path_params.get("profile", "")
                res = {
                    "ok": True,
                    "profile": hs,
                    "state": dv.trang_thai_tra,
                    "message": f"Dịch vụ dừng: {dv.trang_thai_tra}",
                    "error": dv.ma_loi_tra,
                }
                return JSONResponse(res)
            return trang_thai

        async def solve_trap(request):
            dv.so_lan_solve += 1
            return JSONResponse({"ok": False, "error": "trap_called"})

        return Starlette(routes=[
            Route("/health", health),
            Route("/v1/profiles", profiles),
            Route("/v1/accounts/saved", accounts_saved, methods=["POST"]),

            # 1. auto-login-saved (Google)
            Route("/v1/session/auto-login-saved", _tao_bat_dau("auto-login-saved"), methods=["POST"]),
            Route("/v1/session/{profile}/auto-login-status", _tao_trang_thai("auto-login-saved")),

            # 2. chatgpt (ChatGPT qua Google)
            Route("/v1/chatgpt/onboard", _tao_bat_dau("chatgpt"), methods=["POST"]),
            Route("/v1/chatgpt/{profile}/onboard-status", _tao_trang_thai("chatgpt")),
            Route("/v1/chatgpt/{profile}/refresh-jwt", lambda r: JSONResponse({"ok": False})),

            # 3. openai (ChatGPT trực tiếp)
            Route("/v1/openai-native/onboard", _tao_bat_dau("openai"), methods=["POST"]),
            Route("/v1/openai-native/{profile}/onboard-status", _tao_trang_thai("openai")),
            Route("/v1/openai-native/{profile}/token", lambda r: JSONResponse({"ok": False})),

            # 4. claude-web (Claude qua Google)
            Route("/v1/claude-web/onboard", _tao_bat_dau("claude-web"), methods=["POST"]),
            Route("/v1/claude-web/{profile}/onboard-status", _tao_trang_thai("claude-web")),
            Route("/v1/claude-web/{profile}/session", lambda r: JSONResponse({"ok": False})),

            # 5. claude-web-email (Claude trực tiếp)
            Route("/v1/claude-web/onboard-email", _tao_bat_dau("claude-web-email"), methods=["POST"]),

            # 6. grok-web (Grok qua Google)
            Route("/v1/grok-web/onboard", _tao_bat_dau("grok-web"), methods=["POST"]),
            Route("/v1/grok-web/{profile}/onboard-status", _tao_trang_thai("grok-web")),
            Route("/v1/grok-web/{profile}/session", lambda r: JSONResponse({"ok": False})),

            # Bẫy giải captcha/challenge
            Route("/v1/solve/{rest:path}", solve_trap, methods=["POST", "GET"]),
        ])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGiaCanNguoi()
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
    monkeypatch.setenv("C2A_DN_GAP_GOOGLE_GIAY", "0")
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    for mod_name in ("may-dang-nhap", "chatgpt-web", "claude-web", "grok-web", "gemini-web"):
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

        yield client, so


# Danh sách 6 đường onboard
DUONG_ONBOARD = [
    "chatgpt",
    "openai",
    "claude-web",
    "claude-web-email",
    "grok-web",
    "auto-login-saved",
]

# Danh sách 3 trạng thái dừng-chờ-người
TRANG_THAI_CAN_NGUOI = [
    ("need_captcha", ""),
    ("need_human", "ma_la_xyz"),
    ("need_manual", ""),
]


@pytest.mark.parametrize("duong", DUONG_ONBOARD)
@pytest.mark.parametrize("tt_dich_vu, ma_loi_dv", TRANG_THAI_CAN_NGUOI)
def test_ma_tran_o4_dung_o_can_nguoi(moi_truong_loi, dich_vu_gia, duong, tt_dich_vu, ma_loi_dv):
    """Ma trận Ô 4: 6 đường onboard × 3 trạng thái dừng → can_nguoi: True,
    huong_dan tiếng Việt chỉ rõ vào trình duyệt/noVNC, đúng 1 req bắt đầu, 0 solve."""
    client, so = moi_truong_loi

    dich_vu_gia.trang_thai_tra = tt_dich_vu
    dich_vu_gia.ma_loi_tra = ma_loi_dv

    # Chuẩn bị tài khoản theo từng đường
    email_tk = f"user.{duong}.{tt_dich_vu}@example.com"
    email_g = f"google.{duong}.{tt_dich_vu}@example.com"

    # Tạo tài khoản Google phụ nếu cần
    tk_g_id = None
    if duong in ("chatgpt", "claude-web", "grok-web"):
        r_g = client.post("/api/tai-khoan/da-luu", headers=QT, json={
            "email": email_g, "mat_khau": "mat-khau-g-01", "dang_nhap_bang": "truc_tiep"})
        assert r_g.status_code == 200, r_g.text
        tk_g_id = r_g.json()["tai_khoan"]["id"]

    body_tk = {
        "email": email_tk,
        "mat_khau": MK_GIA,
    }

    if duong == "auto-login-saved":
        body_tk["dang_nhap_bang"] = ""
    elif duong == "chatgpt":
        body_tk["module_id"] = "chatgpt-web"
        body_tk["dang_nhap_bang"] = "google"
        body_tk["tai_khoan_google_id"] = tk_g_id
    elif duong == "openai":
        body_tk["module_id"] = "chatgpt-web"
        body_tk["dang_nhap_bang"] = "truc_tiep"
    elif duong == "claude-web":
        body_tk["module_id"] = "claude-web"
        body_tk["dang_nhap_bang"] = "google"
        body_tk["tai_khoan_google_id"] = tk_g_id
    elif duong == "claude-web-email":
        body_tk["module_id"] = "claude-web"
        body_tk["dang_nhap_bang"] = "truc_tiep"
    elif duong == "grok-web":
        body_tk["module_id"] = "grok-web"
        body_tk["dang_nhap_bang"] = "google"
        body_tk["tai_khoan_google_id"] = tk_g_id

    r_tk = client.post("/api/tai-khoan/da-luu", headers=QT, json=body_tk)
    assert r_tk.status_code == 200, r_tk.text
    tk_id = r_tk.json()["tai_khoan"]["id"]

    # Lần gọi 1: Đăng nhập
    r_dn = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                       json={"module_id": "may-dang-nhap"})
    assert r_dn.status_code == 200, r_dn.text
    kq = r_dn.json()

    # Khẳng định hợp đồng Ô 4
    assert kq["thanh_cong"] is False
    assert kq["can_nguoi"] is True
    assert kq["ma_loi"] != ""
    assert kq["huong_dan"] != ""
    # Hướng dẫn là tiếng Việt và chỉ dẫn việc người cần làm
    hd_lower = kq["huong_dan"].lower()
    assert any(w in hd_lower for w in ("trình duyệt", "novnc", "thủ công", "xác minh", "kiểm tra lại", "đăng nhập lại"))

    # Dịch vụ giả nhận ĐÚNG 1 request bắt đầu onboard
    assert dich_vu_gia.so_lan_bat_dau.get(duong, 0) == 1
    # 0 yêu cầu solve
    assert dich_vu_gia.so_lan_solve == 0
    # Mật khẩu không rò
    assert MK_GIA not in r_dn.text

    # Đo hành vi thật của lần gọi thứ hai ngay sau
    so_lan_truoc = dich_vu_gia.so_lan_bat_dau.get(duong, 0)
    r_dn2 = client.post(f"/api/tai-khoan/da-luu/{tk_id}/dang-nhap", headers=QT,
                        json={"module_id": "may-dang-nhap"})
    assert r_dn2.status_code == 200, r_dn2.text
    kq2 = r_dn2.json()
    # Ghi nhận: lần 2 có bị chặn hay không phụ thuộc vào mã lỗi vĩnh viễn (need_captcha -> chặn vĩnh viễn; need_manual / need_human -> đo được)
    # Hành vi đo được của lần 2: số lần bắt đầu onboard sau lần 2 so với trước,
    # và kết quả lần 2 vẫn là «cần người» (không biến thành thành công giả).
    so_lan_sau = dich_vu_gia.so_lan_bat_dau.get(duong, 0)
    assert kq2["thanh_cong"] is False
    assert kq2["can_nguoi"] is True or kq2["ma_loi"] in ("dang_chay", "qua_tran_thu")
    print(f"LAN2 duong={duong} tt={tt_dich_vu} bat_dau {so_lan_truoc}->{so_lan_sau} ma_loi={kq2['ma_loi']} can_nguoi={kq2['can_nguoi']}")
