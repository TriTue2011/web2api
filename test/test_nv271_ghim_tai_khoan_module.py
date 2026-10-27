"""Test cấp module cho NV271: hợp đồng ghim tài khoản và tín hiệu lỗi.

Kiểm tra 1b + 1c trên hai module thật (openaicodex và chatgpt-web):
1. openaicodex:
   - co_tai_khoan_email (hoa-thường, đúng, sai).
   - chat(..., tai_khoan_email=...) ghim đúng tài khoản (kể cả khi đứng sau trong danh sách).
   - Email không có -> 503 và 0 lần gọi mạng.
   - 429 trả tiêu đề retry-after là chữ số khớp mốc hồi.
2. chatgpt-web:
   - co_tai_khoan_email.
   - Ghim đúng tài khoản theo email.
   - 403 chặn bot (LoiChanBot) trả tiêu đề x-web2api-loi-loai: chan_bot.
   - 401 / 429 không có tiêu đề đó.
3. loi_tu_phan_hoi ở lõi:
   - Phân loại lỗi từ mã HTTP + tiêu đề có cấu trúc.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from app import kho
from app.duong_dung import LoiDuong, loi_tu_phan_hoi, phan_loai_loi

GOC = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Nạp module openaicodex cô lập
# ---------------------------------------------------------------------------
THU_MUC_CODEX = GOC / "modules" / "openaicodex"
TEN_GOI_CODEX = "openai_codex_nv271_test"


def _tai_goi_codex():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI_CODEX or t.startswith(TEN_GOI_CODEX + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI_CODEX)
    goi.__path__ = [str(THU_MUC_CODEX)]
    sys.modules[TEN_GOI_CODEX] = goi
    for ten_tep in ("oauth", "han_muc", "hoi_phuc", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI_CODEX}.{ten_tep}", THU_MUC_CODEX / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI_CODEX}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI_CODEX}.module"]


# ---------------------------------------------------------------------------
# Nạp module chatgpt-web cô lập
# ---------------------------------------------------------------------------
THU_MUC_WEB = GOC / "modules" / "chatgpt-web"
TEN_GOI_WEB = "chatgpt_web_nv271_test"


def _tai_goi_web():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI_WEB or t.startswith(TEN_GOI_WEB + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI_WEB)
    goi.__path__ = [str(THU_MUC_WEB)]
    sys.modules[TEN_GOI_WEB] = goi
    for ten_tep in ("client", "converter", "module"):
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI_WEB}.{ten_tep}", THU_MUC_WEB / f"{ten_tep}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI_WEB}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return (sys.modules[f"{TEN_GOI_WEB}.module"],
            sys.modules[f"{TEN_GOI_WEB}.client"])


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def tao_jwt(email="nguoi.a@example.com", plan="plus", het_sau_giay=8 * 3600, account_id="acc-123"):
    payload = {
        "email": email,
        "exp": int(time.time()) + het_sau_giay,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
        },
        "https://api.openai.com/profile": {"email": email},
    }
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.chu-ky-gia"


# ===========================================================================
# 1. Test đơn vị loi_tu_phan_hoi
# ===========================================================================

def test_loi_tu_phan_hoi_429_co_retry_after():
    ma = 429
    headers = {"Retry-After": "600"}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 429
    assert err.tin_hieu.get("dat_lai_sau_giay") == 600

    loai, moc = phan_loai_loi(err)
    assert loai == "het_han_muc"
    bay_gio = int(time.time())
    assert bay_gio + 550 <= moc <= bay_gio + 650


def test_loi_tu_phan_hoi_429_khong_tieu_de():
    ma = 429
    headers = {}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 429
    assert "dat_lai_sau_giay" not in err.tin_hieu

    loai, moc = phan_loai_loi(err)
    assert loai == "het_han_muc"
    bay_gio = int(time.time())
    # Mặc định 3600s
    assert bay_gio + 3500 <= moc <= bay_gio + 3700


def test_loi_tu_phan_hoi_429_retry_after_khong_phai_chu_so():
    ma = 429
    headers = {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 429
    assert "dat_lai_sau_giay" not in err.tin_hieu

    loai, moc = phan_loai_loi(err)
    assert loai == "het_han_muc"
    bay_gio = int(time.time())
    assert bay_gio + 3500 <= moc <= bay_gio + 3700


def test_loi_tu_phan_hoi_403_chan_bot():
    ma = 403
    headers = {"X-Web2api-Loi-Loai": "chan_bot"}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 403
    assert err.tin_hieu.get("loai") == "chan_bot"

    loai, moc = phan_loai_loi(err)
    assert loai == "chan_bot"
    assert moc == 0


def test_loi_tu_phan_hoi_403_tron():
    ma = 403
    headers = {}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 403
    assert err.tin_hieu.get("loai") != "chan_bot"

    loai, moc = phan_loai_loi(err)
    assert loai == "can_dang_nhap_lai"
    assert moc == 0


def test_loi_tu_phan_hoi_503():
    ma = 503
    headers = {}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 503

    loai, moc = phan_loai_loi(err)
    assert loai == "loi_tam_thoi"
    assert moc == 0


def test_loi_tu_phan_hoi_400():
    ma = 400
    headers = {}
    err = loi_tu_phan_hoi(ma, headers)
    assert isinstance(err, LoiDuong)
    assert err.ma_http == 400

    loai, moc = phan_loai_loi(err)
    assert loai == "loi_khac"
    assert moc == 0


# ===========================================================================
# 2. openaicodex: co_tai_khoan_email + chat ghim + retry-after
# ===========================================================================

@pytest.fixture
def mod_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data-codex"))
    mod_module = _tai_goi_codex()
    manifest = json.loads((THU_MUC_CODEX / "manifest.json").read_text(encoding="utf-8"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("openaicodex")
    return m


def test_codex_co_tai_khoan_email_va_ghim(mod_codex):
    token_b = tao_jwt(email="user.b@example.com")
    token_a = tao_jwt(email="user.a@example.com")

    # Nạp B đứng trước, A đứng sau
    mod_codex.kho.luu("tai_khoan", {"tai_khoan": [
        {"access_token": token_b, "refresh_token": "rf-b", "email": "user.b@example.com",
         "expires_at": time.time() + 8 * 3600, "trang_thai": "hoat_dong"},
        {"access_token": token_a, "refresh_token": "rf-a", "email": "user.a@example.com",
         "expires_at": time.time() + 8 * 3600, "trang_thai": "hoat_dong"},
    ]})
    mod_codex.nap({})

    # Kiểm co_tai_khoan_email (không phân biệt hoa thường)
    assert mod_codex.co_tai_khoan_email("user.a@example.com") is True
    assert mod_codex.co_tai_khoan_email("USER.A@EXAMPLE.COM") is True
    assert mod_codex.co_tai_khoan_email("user.b@example.com") is True
    assert mod_codex.co_tai_khoan_email("user.c@example.com") is False

    # MockTransport đếm lời gọi cho từng token
    dem_loi_goi = {token_a: 0, token_b: 0}

    def xu_ly(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if token in dem_loi_goi:
            dem_loi_goi[token] += 1
        sse_data = 'data: {"type": "response.completed", "response": {}}\n\n'
        return httpx.Response(200, content=sse_data.encode(),
                              headers={"content-type": "text/event-stream"})

    mod_codex._gan_transport_gia(httpx.MockTransport(xu_ly))

    # Ghim tài khoản A (dù A đứng sau B trong danh sách)
    ma, _headers, _than = asyncio.run(mod_codex.chat(
        {"model": "openaicodex/gpt-5.5", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="user.a@example.com",
    ))
    assert ma == 200
    assert dem_loi_goi[token_a] == 1
    assert dem_loi_goi[token_b] == 0

    # Ghim email không có -> 503 và 0 lần gọi mạng
    ma_k, _h_k, _than_k = asyncio.run(mod_codex.chat(
        {"model": "openaicodex/gpt-5.5", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="khong-ton-tai@example.com",
    ))
    assert ma_k == 503
    assert dem_loi_goi[token_a] == 1
    assert dem_loi_goi[token_b] == 0


def test_codex_429_tra_retry_after(mod_codex):
    token = tao_jwt(email="rate.limit@example.com")
    mod_codex.kho.luu("tai_khoan", {"tai_khoan": [
        {"access_token": token, "refresh_token": "rf-rl", "email": "rate.limit@example.com",
         "expires_at": time.time() + 8 * 3600, "trang_thai": "hoat_dong"},
    ]})
    mod_codex.nap({})

    now = int(time.time())
    reset_epoch = now + 600

    def xu_ly(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": "usage_limit_reached", "resets_at": reset_epoch}},
            headers={
                "x-codex-primary-used-percent": "100",
                "x-codex-primary-window-minutes": "43200",
                "x-codex-primary-reset-at": str(reset_epoch),
            },
        )

    mod_codex._gan_transport_gia(httpx.MockTransport(xu_ly))

    ma, headers, _than = asyncio.run(mod_codex.chat(
        {"model": "openaicodex/gpt-5.5", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="rate.limit@example.com",
    ))
    assert ma == 429
    # Tiêu đề retry-after phải là số nguyên giây >= 1
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert "retry-after" in headers_lower
    ra = headers_lower["retry-after"]
    assert ra.isdigit()
    assert 550 <= int(ra) <= 650


# ===========================================================================
# 3. chatgpt-web: co_tai_khoan_email + chat ghim + chan_bot header
# ===========================================================================

@pytest.fixture
def mod_web(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data-web"))
    mod_module, mod_client = _tai_goi_web()
    manifest = json.loads((THU_MUC_WEB / "manifest.json").read_text(encoding="utf-8"))
    m = mod_module.Module(dict(manifest))
    m.kho = kho.kho_rieng("chatgpt-web")
    return m, mod_client


def test_chatgpt_web_co_tai_khoan_email_va_ghim(mod_web):
    m, mod_client = mod_web

    # Cài đặt hai tài khoản với email
    tk1 = "token-web-1111"
    tk2 = "token-web-2222"
    m.kho.luu("tai_khoan", {"ban_ghi": {
        m._khoa_tu_token(tk1): {"email": "web.one@example.com", "goi": "plus", "ho_so": "hs1", "trang_thai": "hoat_dong"},
        m._khoa_tu_token(tk2): {"email": "web.two@example.com", "goi": "plus", "ho_so": "hs2", "trang_thai": "hoat_dong"},
    }})
    m.nap({"tai_khoan": [tk1, tk2]})

    assert m.co_tai_khoan_email("web.one@example.com") is True
    assert m.co_tai_khoan_email("WEB.ONE@EXAMPLE.COM") is True
    assert m.co_tai_khoan_email("web.two@example.com") is True
    assert m.co_tai_khoan_email("web.three@example.com") is False

    dem_loi_goi = {tk1: 0, tk2: 0}

    class ClientGia:
        def __init__(self, token):
            self.token = token
        async def generate_content(self, tin_nhan, ten_model, slug_da_giai=None):
            dem_loi_goi[self.token] += 1
            return "nội dung web giả"

    m._tao_client = lambda token: ClientGia(token)

    # Ghim tk2 (web.two@example.com)
    ma, _h, than = asyncio.run(m.chat(
        {"model": "chatgpt-web/gpt-4o", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="web.two@example.com",
    ))
    assert ma == 200
    assert dem_loi_goi[tk1] == 0
    assert dem_loi_goi[tk2] == 1

    # Ghim email không có -> 503
    ma_k, _h_k, _than_k = asyncio.run(m.chat(
        {"model": "chatgpt-web/gpt-4o", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="khong-co@example.com",
    ))
    assert ma_k == 503
    assert dem_loi_goi[tk1] == 0
    assert dem_loi_goi[tk2] == 1


def test_chatgpt_web_403_chan_bot_tra_tieu_de(mod_web):
    m, mod_client = mod_web
    tk = "token-web-bot"
    m.kho.luu("tai_khoan", {"ban_ghi": {
        m._khoa_tu_token(tk): {"email": "bot@example.com", "goi": "plus", "ho_so": "hs-bot", "trang_thai": "hoat_dong"},
    }})
    m.nap({"tai_khoan": [tk]})

    class ClientChanBot:
        def __init__(self, token):
            pass
        async def generate_content(self, tin_nhan, ten_model, slug_da_giai=None):
            raise mod_client.LoiChanBot("chặn bot")

    m._tao_client = lambda token: ClientChanBot(token)

    ma, headers, _than = asyncio.run(m.chat(
        {"model": "chatgpt-web/gpt-4o", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="bot@example.com",
    ))
    assert ma == 403
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert headers_lower.get("x-web2api-loi-loai") == "chan_bot"


def test_chatgpt_web_401_khong_co_tieu_de_chan_bot(mod_web):
    m, mod_client = mod_web
    tk = "token-web-401"
    m.kho.luu("tai_khoan", {"ban_ghi": {
        m._khoa_tu_token(tk): {"email": "xac_thuc@example.com", "goi": "plus", "ho_so": "hs", "trang_thai": "hoat_dong"},
    }})
    m.nap({"tai_khoan": [tk]})

    class ClientXacThuc:
        def __init__(self, token):
            pass
        async def generate_content(self, tin_nhan, ten_model, slug_da_giai=None):
            raise mod_client.LoiXacThuc("hết hạn")

    m._tao_client = lambda token: ClientXacThuc(token)

    ma, headers, _than = asyncio.run(m.chat(
        {"model": "chatgpt-web/gpt-4o", "messages": [{"role": "user", "content": "chào"}]},
        False,
        tai_khoan_email="xac_thuc@example.com",
    ))
    assert ma == 401
    headers_lower = {k.lower(): v for k, v in headers.items()}
    assert "x-web2api-loi-loai" not in headers_lower
