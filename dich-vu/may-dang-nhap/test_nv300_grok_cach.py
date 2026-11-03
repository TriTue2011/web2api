# test_nv300_grok_cach.py — NV300: dịch vụ Grok Web phải theo CÁCH của tài khoản.
# Lỗi thật chủ máy báo: «thêm Grok bằng email nhưng nó đăng nhập qua Google». Gốc: màn
# đăng nhập grok.com luôn hiện nút Google, mà «Đường 1» hễ thấy nút là bấm và coi như xong,
# đường email chỉ chạy khi KHÔNG có nút. Trình duyệt GIẢ, không Internet.
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gia_goi  # noqa: F401

from src import grok_web_login as gwl
from src.browser_pool import pool

_NGU_THAT = asyncio.sleep


class _TrangGia:
    url = "https://grok.com/"

    def __init__(self):
        self.da_mo = []

    async def goto(self, url, **k):
        self.da_mo.append(url)

    async def bring_to_front(self):
        pass


class _CtxGia:
    def __init__(self, trang):
        self.pages = [trang]

    def on(self, *a, **k):
        pass


def _chay(method):
    nhat = {"google": 0, "email": 0}
    trang = _TrangGia()
    # nhìn trang: (1) trang chủ có nút Google, chưa có ô email; (2) màn đăng nhập có cả hai;
    # (3) sau đăng nhập đã vào ứng dụng.
    kich_ban = [
        {"chan": False, "oSoan": False, "nutGoogle": True, "oEmail": False},
        {"chan": False, "oSoan": False, "nutGoogle": True, "oEmail": True},
        {"chan": False, "oSoan": True, "nutGoogle": False, "oEmail": False},
    ]
    chi_so = {"i": 0}

    async def nhin(page):
        v = kich_ban[min(chi_so["i"], len(kich_ban) - 1)]
        chi_so["i"] += 1
        return v

    async def bam_google(page):
        nhat["google"] += 1
        return True

    async def dien(page, email, mk):
        nhat["email"] += 1
        return True

    async def doc_cookie(profile):
        return {"sso": "gia"}

    async def lay_ctx(**k):
        return _CtxGia(trang)

    async def ngu_nhanh(d, *a, **k):
        return await _NGU_THAT(0)

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(gwl, "_nhin_trang", nhin)
        mp.setattr(gwl, "_bam_nut_google", bam_google)
        mp.setattr(gwl, "_dien_email_mat_khau", dien)
        mp.setattr(gwl, "doc_cookie_phien", doc_cookie)
        mp.setattr(pool, "get", lay_ctx)
        mp.setattr(asyncio, "sleep", ngu_nhanh)
        session = gwl.GrokWebLoginSession(profile="grok-gia", email="chu@example.com",
                                          state="starting", message="",
                                          method=method)
        asyncio.run(gwl._chay_trong(session, "mat-khau-gia"))
    finally:
        mp.undo()
    return session, nhat, trang


def test_cach_email_khong_bam_google_du_man_hinh_co_nut_google():
    session, nhat, trang = _chay("email")
    assert nhat == {"google": 0, "email": 1}, nhat
    assert session.state == "success", (session.state, session.error)
    assert gwl._GROK_LOGIN in trang.da_mo      # chưa có ô email → mở màn đăng nhập


def test_cach_google_chi_di_duong_google():
    session, nhat, _ = _chay("google")
    assert nhat == {"google": 1, "email": 0}, nhat


def test_khong_noi_cach_giu_hanh_vi_cu_google_truoc():
    _, nhat, _ = _chay("")
    assert nhat["google"] == 1 and nhat["email"] == 0


def test_request_chi_nhan_cach_hop_le():
    from src.main import GrokWebOnboardReq
    assert GrokWebOnboardReq(method="email").method == "email"
    assert GrokWebOnboardReq().method == ""
    with pytest.raises(Exception):
        GrokWebOnboardReq(method="x")


@pytest.mark.parametrize("trang_thai,dong", [("success", True), ("failed", True), ("need_manual", False)])
def test_chay_dong_trinh_duyet_khi_xong_hoac_loi_giu_khi_cho_nguoi(monkeypatch, trang_thai, dong):
    """NV300 vòng đời: xong/hỏng → đóng ngay; chờ người → giữ (vòng quét nhàn rỗi đóng sau hạn)."""
    da_dong = []

    async def chay_trong(session, mk):
        session.state = trang_thai

    async def close_profile(profile, **k):
        da_dong.append(profile)
        return True

    monkeypatch.setattr(gwl, "_chay_trong", chay_trong)
    monkeypatch.setattr(pool, "close_profile", close_profile)
    session = gwl.GrokWebLoginSession(profile="grok-vd", email="a@example.com", state="starting", message="")
    asyncio.run(gwl._chay(session, "mk-gia"))
    assert (da_dong == ["grok-vd"]) is dong
    assert not pool.dang_dang_nhap("grok-vd")        # cờ «đang đăng nhập» luôn được nhả
