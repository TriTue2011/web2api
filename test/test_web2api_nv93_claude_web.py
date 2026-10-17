"""Test NV93 cho module claude-web: «hồ sơ Google + tái dùng».

- `tai_dung_ho_so(ho_so, trinh_duyet)`: claude.ai → đi tắt nếu có cookie
  `sessionKey`; else /login → «Continue with Google» (popup) → chọn tài khoản
  Google → về claude.ai → đọc `sessionKey`.
- Org: /api/organizations → org có capability «chat».
- Gói: Free/Pro suy từ cấu trúc.
- CAPTCHA/2FA → can_nguoi.

Trình duyệt GIẢ, máy chủ HTTP giả. Không Internet, không Chrome.
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
from app.tai_khoan_hang_loat import KetQuaDangNhap

from ho_tro_nv93 import TrinhDuyetChuyenDoi, TrinhDuyetLoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "claude-web"
TEN_GOI = "claude_web_nv93_mod_test"

SESSION_KEY_GIA = "sessionkey-gia-nv93-0001"
EMAIL_GIA = "ban-gia-nv93@example.com"
HO_SO_GIA = "hs-gia-nv93-0001"
ORG_GIA = "org-gia-nv93-0001"


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
        cookie = req.headers.get("cookie", "")
        if req.url.path == "/api/organizations":
            if f"sessionKey={SESSION_KEY_GIA}" in cookie:
                return httpx.Response(200, json=[
                    {"uuid": ORG_GIA, "capabilities": ["chat"]}])
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("claude-web")
    transport = _may_chu_gia()

    def tao_client(session_key):
        return mod_client.ClaudeWebClient(
            session_key, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


def _td_co_phien():
    return TrinhDuyetGia(trang={
        "https://claude.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"sessionKey": SESSION_KEY_GIA},
        }})


# ───────────── tai_dung_ho_so ─────────────

def test_tai_dung_ho_so_di_tat(mod):
    """Hồ sơ đã có cookie sessionKey còn hiệu lực → đi tắt: không bấm,
    không điền gì thêm."""
    td = _td_co_phien()
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" not in ten_buoc
    assert "dien" not in ten_buoc
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA


def test_tai_dung_ho_so_luong_day_du(mod):
    """Chưa có sessionKey → /login → «Continue with Google» (popup) → chọn
    tài khoản Google trong hồ sơ → về claude.ai đã đăng nhập
    (chuyển trạng thái sau bấm)."""
    td = TrinhDuyetChuyenDoi(
        trang={
            "https://claude.ai/login": {"tin_hieu": [], "cookie": {}},
            "https://claude.ai/": {"tin_hieu": [], "cookie": {}},
        },
        sau_bam={
            "https://claude.ai/login": {
                "url": "https://claude.ai/",
                "trang": {"tin_hieu": ["da_dang_nhap"],
                          "cookie": {"sessionKey": SESSION_KEY_GIA}},
            },
        })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" in ten_buoc  # bấm «Continue with Google»
    assert "dien" not in ten_buoc  # không nhập lại email/mật khẩu


def test_tai_dung_ho_so_gap_2fa_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://claude.ai/login": {
            "tin_hieu": ["2fa"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


# ───────────── org + go_tai_khoan + không lộ bí mật ─────────────

def test_org_duoc_doc_va_go_tai_khoan(mod):
    chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert SESSION_KEY_GIA not in json.dumps(ds)
    mod.go_tai_khoan(ds[0]["khoa"])
    assert mod.liet_ke_tai_khoan() == []


def test_hai_module_cung_ho_so_doc_lap(tmp_path, monkeypatch):
    """Hai module (kho riêng id khác nhau, cùng thư mục dữ liệu) cùng một hồ
    sơ → gỡ bên này không động tài khoản bên kia."""
    mod1 = _mod_moi(tmp_path, monkeypatch)
    mod2 = _mod_moi(tmp_path, monkeypatch)
    mod2.kho = kho.kho_rieng("chatgpt-web")  # id kho khác, cùng thư mục dữ liệu

    chay(mod1.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    chay(mod2.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    assert len(mod1.liet_ke_tai_khoan()) == 1
    assert len(mod2.liet_ke_tai_khoan()) == 1
    mod1.go_tai_khoan(mod1.liet_ke_tai_khoan()[0]["khoa"])
    assert mod1.liet_ke_tai_khoan() == []
    assert len(mod2.liet_ke_tai_khoan()) == 1  # không bị động


# ───────────── tai_dung_ho_so: các trường hợp lỗi ─────────────

def test_tai_dung_ho_so_khong_co_trinh_duyet_thi_can_nguoi(mod):
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), None))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_tai_dung_ho_so_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://claude.ai/login": {
            "tin_hieu": ["captcha"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_khong_lay_duoc_phien(mod):
    """Đi tắt nhưng không đọc được sessionKey → lỗi rõ, không thêm tài khoản."""
    td = TrinhDuyetGia(trang={
        "https://claude.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_lay_duoc_phien"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_phien_khong_dung_duoc(mod):
    """sessionKey đọc được nhưng máy chủ giả báo hết hạn → lỗi rõ."""
    td = TrinhDuyetGia(trang={
        "https://claude.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"sessionKey": "sessionkey-het-han-gia"},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "phien_khong_dung_duoc"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_het_gio_cho_phien(monkeypatch, mod):
    """Không bao giờ đăng nhập được → hết lượt chờ → lỗi rõ (sleep giả)."""
    async def ngu_gia(giay):
        return None
    monkeypatch.setattr(asyncio, "sleep", ngu_gia)
    td = TrinhDuyetGia(trang={
        "https://claude.ai/login": {"tin_hieu": [], "cookie": {}},
        "https://claude.ai/": {"tin_hieu": [], "cookie": {}},
    })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_lay_duoc_phien"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_loi_la_dong_cua(mod):
    """Trình duyệt ném lỗi lạ ở chỗ quyết định → đóng cửa."""
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), TrinhDuyetLoi()))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi
    assert mod.liet_ke_tai_khoan() == []


def test_bi_mat_khong_vao_log(mod, caplog):
    """Bí mật (sessionKey) không lọt vào log khi tái dùng hồ sơ."""
    import logging
    with caplog.at_level(logging.DEBUG):
        chay(mod.tai_dung_ho_so(_ho_so_gia(), _td_co_phien()))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert SESSION_KEY_GIA not in nhat_ky
