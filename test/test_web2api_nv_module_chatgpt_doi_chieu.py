"""Đối chiếu chatgpt-web với chatgpt2api — phần ghi bền token sau đăng nhập.

chatgpt2api: mọi tầng lấy được JWT đều đi qua _cgf_ghi_pool để ghi vào pool
(services/account_recovery.py). chatgpt-web fb8aa25 đọc access_token từ
cookie và chỉ _them_token vào bộ nhớ, không ghi bền — sau khởi động lại
nap() không dựng lại token đó (ban_ghi "phien" không được ap_dung_ban_ghi
dùng để tạo tài khoản). Sửa: đường đăng nhập bằng trình duyệt đi qua
gan_jwt_tu_dang_nhap (NV210, đã có trong module), hỏng thì rơi về bộ nhớ.

Trình duyệt là GIẢ, máy chủ là GIẢ, không Internet, không Chrome.
Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import BiMatTam

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_module_doi_chieu_test"

TOKEN_GIA_OK = "token-gia-doi-chieu-0001"
MAT_KHAU_GIA = "mat-khau-gia-doi-chieu-001"
EMAIL_GIA = "ban-gia-doi-chieu@example.com"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
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


def _may_chu_gia():
    def xu_ly(req):
        auth = req.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if req.url.path == "/backend-api/me":
            if token == TOKEN_GIA_OK or token.startswith("token-tu-"):
                return httpx.Response(200, json={"email": EMAIL_GIA})
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
            token, base_url="http://127.0.0.1:9", transport=transport)
    m._tao_client = tao_client
    return m


def _trinh_duyet(tin_hieu, cookie):
    return TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {"tin_hieu": tin_hieu, "cookie": cookie}})


def test_dang_nhap_trinh_duyet_ghi_ben_qua_gan_jwt(mod, monkeypatch):
    """Đường trình duyệt gọi gan_jwt với đúng (email, token) sau thành công."""
    goi = []
    monkeypatch.setattr(
        mod, "gan_jwt_tu_dang_nhap",
        lambda e, t, h=None: goi.append((e, t)) or {"ok": True, "khoa": "tk-gia", "thay_the": False})
    mod._luu_mat_khau("tk-gia-1", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"], {"access_token": "token-tu-phien-gia-0010"})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-1", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong
    assert goi == [(EMAIL_GIA, "token-tu-phien-gia-0010")]


def test_ghi_ben_hong_van_dang_nhap_thanh_cong(mod, monkeypatch):
    """gan_jwt hỏng/ném lỗi -> rơi về bộ nhớ, lần đăng nhập vẫn thành công."""
    monkeypatch.setattr(
        mod, "gan_jwt_tu_dang_nhap",
        lambda e, t, h=None: {"ok": False, "ma_loi": "loi_luu_cau_hinh"})
    mod._luu_mat_khau("tk-gia-2", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td = _trinh_duyet(["thanh_cong"], {"access_token": "token-tu-phien-gia-0011"})
    kq = chay(mod.khoi_phuc_tu_dong("tk-gia-2", EMAIL_GIA, trinh_duyet=td))
    assert kq.thanh_cong
    assert mod._tim_token(Module._khoa_tu_token("token-tu-phien-gia-0011")) is not None

    def gan_nem(e, t, h=None):
        raise RuntimeError("gia-lap-loi-kho")
    monkeypatch.setattr(mod, "gan_jwt_tu_dang_nhap", gan_nem)
    mod._luu_mat_khau("tk-gia-3", EMAIL_GIA, BiMatTam(MAT_KHAU_GIA))
    td2 = _trinh_duyet(["thanh_cong"], {"access_token": "token-tu-phien-gia-0012"})
    kq2 = chay(mod.khoi_phuc_tu_dong("tk-gia-3", EMAIL_GIA, trinh_duyet=td2))
    assert kq2.thanh_cong
