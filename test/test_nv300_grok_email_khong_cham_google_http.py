"""NV300 — hồi quy MỨC CAO (qua lõi thật + dịch vụ giả ghi MỌI lời gọi):

Tài khoản Grok thêm bằng EMAIL (cach_dang_nhap=email) tuyệt đối KHÔNG được làm
dịch vụ chạm đường Google nào (`/v1/session/*`, `auto-login*`, hồ sơ `google-*`).
Lỗi thật chủ máy báo sau khi lên server: trình duyệt xem trực tiếp mở trang đăng nhập
Google và gõ email outlook.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

GOC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOC / "test"))

from gia import MayChuThat  # noqa: E402
from test_nv271_dnt_claude_http import QT, moi_truong_loi  # noqa: E402,F401

EMAIL = "chu.grok-email@example.com"
MK = "mat-khau-gia-grok-email-01"


class DichVuGhiMoi:
    """Ghi (phương thức, đường, thân) của MỌI lời gọi; trả khuôn hợp lệ cho đường Grok/két."""

    def __init__(self):
        self.goi = []

    def app(self):
        dv = self

        async def tat_ca(request):
            try:
                than = await request.json()
            except Exception:
                than = {}
            dv.goi.append((request.method, request.url.path, than))
            p = request.url.path
            if p.endswith("/onboard-status"):
                return JSONResponse({"ok": True, "profile": p.split("/")[3], "state": "need_manual",
                                     "message": "", "error": ""})
            if p == "/v1/profiles":
                return JSONResponse({"ok": True, "profiles": []})
            if p == "/health":
                return JSONResponse({"ok": True, "dich_vu": "may-dang-nhap", "status": "ok"})
            return JSONResponse({"ok": True, "profile": (than or {}).get("profile", ""),
                                 "state": "starting", "message": "", "email": (than or {}).get("email")})

        return Starlette(routes=[Route("/{p:path}", tat_ca, methods=["GET", "POST"])])


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGhiMoi()
    with MayChuThat(dv.app()) as may_chu:
        dv.url = may_chu.url
        yield dv


def _khong_google(dv):
    duong = [p for (_, p, _) in dv.goi]
    xau = [p for p in duong if "google" in p.lower() or "auto-login" in p.lower()]
    assert not xau, f"đường Google bị gọi cho tài khoản cach=email: {xau}\nchuỗi: {duong}"
    for (_, p, than) in dv.goi:
        assert "google" not in str((than or {}).get("profile", "")).lower(), (p, than)
        assert (than or {}).get("loai") != "google", (p, than)


def test_them_grok_bang_email_roi_dang_nhap_khong_cham_google(moi_truong_loi, dich_vu_gia):
    client, so = moi_truong_loi
    dich_vu_gia.goi.clear()          # bỏ các lời gọi lúc cài module (sức khoẻ…)
    # Đúng thứ tự giao diện «Thêm tài khoản»: lưu (kèm cách email) rồi đăng nhập bằng module có trình duyệt.
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MK, "module_id": "grok-web", "cach_dang_nhap": "email"})
    assert r.status_code == 200, r.text
    tk = r.json()["tai_khoan"]
    assert tk["cach_dang_nhap"] == "email"
    r = client.post(f"/api/tai-khoan/da-luu/{tk['id']}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap", "kiem_tra_lai": False})
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/tai-khoan/da-luu/{tk['id']}/dang-nhap-trang-thai?module_id=may-dang-nhap", headers=QT)
    assert r2.status_code in (200, 404), r2.text
    assert dich_vu_gia.goi, "dịch vụ phải được gọi"
    # Lời gọi ĐẦU TIÊN tới đường Grok, không phải Google.
    dau = [p for (_, p, _) in dich_vu_gia.goi if p not in ("/health", "/v1/profiles")][0]
    assert dau.startswith("/v1/accounts/saved") or "/grok-web/" in dau, dau
    _khong_google(dich_vu_gia)
    onb = [t for (m, p, t) in dich_vu_gia.goi if p == "/v1/grok-web/onboard"]
    assert len(onb) == 1 and onb[0]["method"] == "email"


def test_dang_nhap_hang_loat_theo_module_cung_khong_cham_google(moi_truong_loi, dich_vu_gia):
    client, so = moi_truong_loi
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MK, "module_id": "grok-web", "cach_dang_nhap": "email"})
    assert r.status_code == 200, r.text
    dich_vu_gia.goi.clear()
    r = client.post("/api/tai-khoan/da-luu/dang-nhap-theo-module", headers=QT, json={
        "module_id": "may-dang-nhap", "module_so_huu": "grok-web", "chi_chua_co_ho_so": False})
    assert r.status_code in (200, 202), r.text
    import time
    for _ in range(60):
        t = client.get("/api/tai-khoan/dang-nhap-hang-loat/trang-thai", headers=QT).json()
        if t.get("trang_thai_luot") == "hoan_thanh":
            break
        time.sleep(0.5)
    _khong_google(dich_vu_gia)


def test_ban_ghi_grok_chua_luu_cach_van_di_duong_thang_khong_cham_google(moi_truong_loi, dich_vu_gia):
    """Gốc lỗi thật: giao diện/bản cũ lưu bản ghi chỉ có mật khẩu (không `cach_dang_nhap`);
    cách «email» hiện ra chỉ là SUY RA nên bên thực thi phải mặc định đường thẳng, không Google."""
    client, so = moi_truong_loi
    r = client.post("/api/tai-khoan/da-luu", headers=QT, json={
        "email": EMAIL, "mat_khau": MK, "module_id": "grok-web"})      # KHÔNG gửi cách
    assert r.status_code == 200, r.text
    tk = r.json()["tai_khoan"]
    dich_vu_gia.goi.clear()
    r = client.post(f"/api/tai-khoan/da-luu/{tk['id']}/dang-nhap", headers=QT,
                    json={"module_id": "may-dang-nhap", "kiem_tra_lai": False})
    assert r.status_code == 200, r.text
    _khong_google(dich_vu_gia)
    onb = [t for (m, p, t) in dich_vu_gia.goi if p == "/v1/grok-web/onboard"]
    assert len(onb) == 1 and onb[0]["method"] == "email"
