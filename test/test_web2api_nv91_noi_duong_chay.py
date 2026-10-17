"""Test nối tự làm mới vào đường chạy thật của 4 module web (NV91-P2).

Mỗi module phát hiện LỖI PHIÊN bằng CẤU TRÚC (mã 401/403, lớp ngoại lệ phiên —
không dò chữ) rồi gọi `app.tu_lam_moi.bao_loi_phien` cho ĐÚNG tài khoản đó;
lỗi khác (hết hạn mức, máy chủ) thì KHÔNG kích hoạt. Kiểm bằng máy chủ GIẢ:
không mạng thật, không trình duyệt thật.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app import tu_lam_moi as tlm

GOC = Path(__file__).resolve().parents[1]


def _tai_module(ten_thu_muc):
    """Nạp module.<ten> qua importlib (giống test module hiện có)."""
    ten_goi = f"nv91_noi_{ten_thu_muc.replace('-', '_')}"
    for ten in [t for t in list(sys.modules) if t == ten_goi or t.startswith(ten_goi + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(ten_goi)
    goi.__path__ = [str(GOC / "modules" / ten_thu_muc)]
    sys.modules[ten_goi] = goi
    for ten_tep in ("client", "converter", "module"):
        duong = GOC / "modules" / ten_thu_muc / f"{ten_tep}.py"
        if not duong.is_file():
            continue
        spec = importlib.util.spec_from_file_location(f"{ten_goi}.{ten_tep}", duong)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{ten_goi}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{ten_goi}.module"]


@pytest.fixture
def ghi_kich_hoat(monkeypatch):
    """Chích bao_loi_phien: ghi lại (module, khoa) được kích hoạt."""
    cac_lan_goi = []

    def gia(module, khoa, ma_loi=""):
        cac_lan_goi.append((module, khoa, ma_loi))
        return None

    monkeypatch.setattr(tlm, "bao_loi_phien", gia)
    return cac_lan_goi


def test_chatgpt_web_loi_phien_kich_hoat_dung_tai_khoan(ghi_kich_hoat):
    mod_m = _tai_module("chatgpt-web")
    mod = mod_m.Module({"id": "chatgpt-web", "tien_to": "cgt"})
    tk = mod_m._TaiKhoan("token-gia-001")
    mod._tai_khoan = [tk]
    mod._danh_dau_loi(tk, "khoa_hong", 0)
    assert len(ghi_kich_hoat) == 1
    goi_mod, khoa, _ = ghi_kich_hoat[0]
    assert goi_mod is mod
    assert khoa == mod._khoa_tu_token("token-gia-001")


def test_chatgpt_web_loi_khac_khong_kich_hoat(ghi_kich_hoat):
    mod_m = _tai_module("chatgpt-web")
    mod = mod_m.Module({"id": "chatgpt-web", "tien_to": "cgt"})
    tk = mod_m._TaiKhoan("token-gia-001")
    mod._danh_dau_loi(tk, "het_han_muc", 0)
    mod._danh_dau_loi(tk, "may_chu_loi", 0)
    assert ghi_kich_hoat == []


def test_chatgpt_web_khoa_tai_khoan_va_danh_dau_khoe():
    mod_m = _tai_module("chatgpt-web")
    mod = mod_m.Module({"id": "chatgpt-web", "tien_to": "cgt"})
    mod._tai_khoan = [mod_m._TaiKhoan("token-gia-001"), mod_m._TaiKhoan("token-gia-002")]
    cac_khoa = mod.khoa_tai_khoan()
    assert cac_khoa == [mod._khoa_tu_token("token-gia-001"),
                        mod._khoa_tu_token("token-gia-002")]
    mod.danh_dau_hong(cac_khoa[0], "het_han")
    assert cac_khoa[0] in mod._hong
    mod.danh_dau_khoe(cac_khoa[0])
    assert cac_khoa[0] not in mod._hong


def test_gemini_web_loi_phien_kich_hoat(ghi_kich_hoat):
    mod_m = _tai_module("gemini-web")
    mod = mod_m.Module({"id": "gemini-web", "tien_to": "gmw"})
    tk = mod_m._TaiKhoan("psid-gia-001", None)
    mod._tai_khoan = [tk]
    mod._danh_dau_loi(tk, "khoa_hong", 0)
    assert len(ghi_kich_hoat) == 1
    assert ghi_kich_hoat[0][1] == mod._khoa_tu_chuoi(tk.chuoi_goc)
    assert mod.khoa_tai_khoan() == [mod._khoa_tu_chuoi(tk.chuoi_goc)]
    mod.danh_dau_hong(mod._khoa_tu_chuoi(tk.chuoi_goc), "het_han")
    mod.danh_dau_khoe(mod._khoa_tu_chuoi(tk.chuoi_goc))
    assert mod._khoa_tu_chuoi(tk.chuoi_goc) not in mod._hong


def test_grok_web_loi_phien_kich_hoat_loi_khac_khong(ghi_kich_hoat):
    mod_m = _tai_module("grok-web")
    client_m = sys.modules["nv91_noi_grok_web.client"]
    mod = mod_m.Module({"id": "grok-web", "tien_to": "grw"})
    jar = {"sso": "gia-001", "sso-rw": "gia-002"}
    mod._tai_khoan = [jar]
    mod._danh_dau_loi(0, client_m.LoiPhienGrokWeb("phien het han"))
    assert len(ghi_kich_hoat) == 1
    assert ghi_kich_hoat[0][1] == mod._khoa_tu_jar(jar)
    mod._danh_dau_loi(0, client_m.LoiHanMucGrokWeb("het luot"))
    mod._danh_dau_loi(0, client_m.LoiMayChuGrokWeb("loi may chu"))
    assert len(ghi_kich_hoat) == 1  # chỉ lỗi phiên kích hoạt
    assert mod.khoa_tai_khoan() == [mod._khoa_tu_jar(jar)]


def test_claude_web_chat_401_kich_hoat_429_khong(ghi_kich_hoat):
    mod_m = _tai_module("claude-web")
    client_m = sys.modules["nv91_noi_claude_web.client"]
    mod = mod_m.Module({"id": "claude-web", "tien_to": "clw"})
    cookie = "sessionKey=gia-001"

    class MayKhachGia:
        def __init__(self, ma):
            self._ma = ma

        async def lay_org_id(self):
            raise client_m.LoiClaudeWeb(self._ma, "gia")

    for ma, ky_vong in ((401, True), (403, True), (429, False), (500, False)):
        acc = mod_m._TaiKhoan.__new__(mod_m._TaiKhoan)
        acc.cookie = cookie
        acc.ten_che = "…001"
        acc.khoe = True
        acc.may_khach = MayKhachGia(ma)
        mod._accs = [acc]
        ma_tra, _, _ = asyncio.run(
            mod.chat({"model": "clw/sonnet",
                      "messages": [{"role": "user", "content": "xin chào"}]}, False))
        assert ma_tra in (401, 429, 502)

    cac_khoa_kich_hoat = [k for _, k, _ in ghi_kich_hoat]
    assert cac_khoa_kich_hoat == [mod._khoa_tu_cookie(cookie)] * 2  # 401 và 403
    assert mod.khoa_tai_khoan() == [mod._khoa_tu_cookie(cookie)]


def test_don_dep_sau_go_huy_tac_vu_va_go_gan_danh_tinh(tmp_path, monkeypatch):
    """SoModule._don_dep_sau_go: huỷ tác vụ nền + gỡ «gắn» danh tính, không ném."""
    import asyncio as _asyncio

    from app import danh_tinh, so_module

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    da_huy = []

    async def huy_gia(module_id):
        da_huy.append(module_id)
        return 0

    monkeypatch.setattr(tlm, "huy_cua_module", huy_gia)
    da_go_vong = []
    monkeypatch.setattr(tlm.quan_ly(), "go_vong",
                        lambda module_id: da_go_vong.append(module_id) or False)
    danh_tinh.tao("nv91-x@example.com")
    dt = danh_tinh.danh_sach()[0]
    danh_tinh.gan(dt["id"], "mod-gia-nv91")

    so = so_module.SoModule.__new__(so_module.SoModule)
    _asyncio.run(so._don_dep_sau_go("mod-gia-nv91"))

    assert da_huy == ["mod-gia-nv91"]
    assert da_go_vong == ["mod-gia-nv91"]
    assert danh_tinh.lay(dt["id"])["gan"] == []
