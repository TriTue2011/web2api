"""Test NV93 cho module chatgpt-web: «hồ sơ Google + tái dùng».

Hợp đồng (nv93-bo-sung-hop-dong.txt):
- `tai_dung_ho_so(ho_so, trinh_duyet)` → KetQuaDangNhap: «Đăng nhập bằng Google»
  TRONG hồ sơ, lấy phiên, thêm tài khoản của CHÍNH module; hồ sơ đã có phiên
  provider thì «đi tắt»; CAPTCHA/Cloudflare/2FA/xác minh → can_nguoi.
- `liet_ke_tai_khoan()` → danh sách không bí mật.
- `go_tai_khoan(khoa)` — gỡ MỘT tài khoản của module này.
- `models()` theo TÀI KHOẢN/GÓI: mã ổn định, nhãn tách riêng; định tuyến chỉ
  chọn tài khoản có model đó; thiếu → lỗi rõ, KHÔNG fallback âm thầm.

Trình duyệt là GIẢ (TrinhDuyetGia), máy chủ HTTP là giả (httpx.MockTransport).
Không Internet, không Chrome. Chuỗi bí mật trong test đều là chuỗi GIẢ rõ ràng.
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
THU_MUC = GOC / "modules" / "chatgpt-web"
TEN_GOI = "chatgpt_web_nv93_mod_test"

TOKEN_GIA_OK = "token-gia-nv93-ok-0001"
TOKEN_GIA_FREE = "token-gia-nv93-free-0002"
EMAIL_GIA = "ban-gia-nv93@example.com"
HO_SO_GIA = "hs-gia-nv93-0001"


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


def _may_chu_gia(goi_theo_token=None):
    """Máy chủ giả: /backend-api/me, /backend-api/accounts/check, /api/auth/session."""
    goi_theo_token = goi_theo_token or {}

    def xu_ly(req):
        auth = req.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if req.url.path == "/backend-api/me":
            if token in (TOKEN_GIA_OK, TOKEN_GIA_FREE):
                return httpx.Response(200, json={"email": EMAIL_GIA})
            return httpx.Response(401, text="het han")
        if req.url.path == "/backend-api/accounts/check":
            if token in (TOKEN_GIA_OK, TOKEN_GIA_FREE):
                goi = goi_theo_token.get(token, "plus" if token == TOKEN_GIA_OK else "free")
                return httpx.Response(200, json={
                    "accounts": {"default": {"plan_type": goi}}})
            return httpx.Response(401, text="het han")
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch, goi_theo_token=None):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("chatgpt-web")
    transport = _may_chu_gia(goi_theo_token)

    def tao_client(token):
        return mod_client.ChatGPTWebClient(
            token, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


# ───────────── tai_dung_ho_so: đi tắt ─────────────

def test_tai_dung_ho_so_di_tat_khi_ho_so_da_co_phien(mod):
    """Hồ sơ đã có phiên ChatGPT còn hiệu lực → dùng luôn, không bấm gì thêm.

    (Theo mẫu NV92-P3: module vẫn mở trang đăng nhập để kiểm tín hiệu, thấy
    «da_dang_nhap» thì đi tắt — không bam, không bao giờ dien.)
    """
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_OK},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA_OK, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "mo_trang" in ten_buoc
    assert "bam" not in ten_buoc  # đi tắt: không bấm gì
    assert "dien" not in ten_buoc  # không bao giờ điền mật khẩu
    # tài khoản đã thêm với ho_so
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA
    assert ds[0]["email"] == EMAIL_GIA


# ───────────── tai_dung_ho_so: luồng đầy đủ ─────────────

def test_tai_dung_ho_so_luong_day_du(mod):
    """Hồ sơ chưa có phiên → mở trang đăng nhập → bấm Google → SSO chuyển về
    trang đã đăng nhập (TrinhDuyetChuyenDoi mô phỏng chuyển trạng thái sau bấm)."""
    td = TrinhDuyetChuyenDoi(
        trang={
            "https://chatgpt.com/auth/login": {"tin_hieu": [], "cookie": {}},
            "https://chatgpt.com/": {"tin_hieu": [], "cookie": {}},
        },
        sau_bam={
            "https://chatgpt.com/auth/login": {
                "url": "https://chatgpt.com/",
                "trang": {"tin_hieu": ["da_dang_nhap"],
                          "cookie": {"access_token": TOKEN_GIA_OK}},
            },
        })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert kq.thanh_cong and kq.cookie == TOKEN_GIA_OK, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "mo_trang" in ten_buoc
    assert "bam" in ten_buoc  # bấm nút đăng nhập + «Continue with Google»
    assert "dien" not in ten_buoc  # không nhập lại email/mật khẩu
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["goi"] == "plus"  # suy từ /accounts/check


# ───────────── tai_dung_ho_so: can_nguoi ─────────────

def test_tai_dung_ho_so_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://auth.openai.com/u/login": {
            "tin_hieu": ["captcha"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_gap_cloudflare_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://auth.openai.com/u/login": {
            "tin_hieu": ["cloudflare"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


# ───────────── go_tai_khoan ─────────────

def test_go_tai_khoan_chi_go_mot(mod):
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_OK},
        }})
    chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    # thêm tài khoản thứ hai (giả lập bằng token khác)
    td2 = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_FREE},
        }})
    chay(mod.tai_dung_ho_so({"id": "hs-khac", "email": "k@example.com"}, td2))
    assert len(mod.liet_ke_tai_khoan()) == 2
    khoa_go = mod.liet_ke_tai_khoan()[0]["khoa"]
    mod.go_tai_khoan(khoa_go)
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["khoa"] != khoa_go


# ───────────── liet_ke_tai_khoan: không lộ bí mật ─────────────

def test_liet_ke_tai_khoan_khong_lo_bi_mat(mod):
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_OK},
        }})
    chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    tk = ds[0]
    # không có trường bí mật
    assert "phien" not in tk and "token" not in tk and "cookie" not in tk
    # văn bản không chứa token
    assert TOKEN_GIA_OK not in json.dumps(ds)
    # có các trường cần thiết
    assert tk["khoa"].startswith("tk-")
    assert tk["email"] == EMAIL_GIA
    assert tk["ho_so"] == HO_SO_GIA
    assert "goi" in tk and "trang_thai" in tk


# ───────────── models() theo gói ─────────────

def test_models_theo_goi_plus_va_free(tmp_path, monkeypatch):
    mod = _mod_moi(tmp_path, monkeypatch,
                   goi_theo_token={TOKEN_GIA_OK: "plus", TOKEN_GIA_FREE: "free"})
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_OK},
        }})
    chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    td2 = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_FREE},
        }})
    chay(mod.tai_dung_ho_so({"id": "hs-free", "email": "f@example.com"}, td2))

    ds_model = chay(mod.models())
    id_model = [m["id"] for m in ds_model]
    # hợp của hai tài khoản
    assert len(id_model) == len(set(id_model))
    # mỗi model kèm thông tin gói dùng được
    for m in ds_model:
        assert "goi" in m or "available_cho" in m


def test_dinh_tuyen_chi_chon_tai_khoan_co_model(tmp_path, monkeypatch):
    """Yêu cầu model chỉ Plus có → không rơi về tài khoản Free."""
    mod = _mod_moi(tmp_path, monkeypatch,
                   goi_theo_token={TOKEN_GIA_OK: "plus", TOKEN_GIA_FREE: "free"})
    # chỉ thêm tài khoản Free
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_FREE},
        }})
    chay(mod.tai_dung_ho_so({"id": "hs-free", "email": "f@example.com"}, td))
    # model chỉ Plus có → lỗi rõ (4xx), không chạy model khác
    ma, _td, _body = chay(mod.chat({"model": "chatgpt-web/o3", "messages": [{"role": "user", "content": "hi"}]},
                      stream=False))
    assert ma in (400, 404), f"mong 400/404, được {ma}"
    assert b"o3" in _body.lower()


# ───────────── tai_dung_ho_so: các trường hợp lỗi ─────────────

def test_tai_dung_ho_so_khong_co_trinh_duyet_thi_can_nguoi(mod):
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), None))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_tai_dung_ho_so_gap_xac_minh_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["xac_minh_thiet_bi"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_khong_lay_duoc_phien(mod):
    """Đi tắt nhưng không đọc được token → lỗi rõ, không thêm tài khoản."""
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_lay_duoc_phien"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_phien_khong_dung_duoc(mod):
    """Token đọc được nhưng máy chủ giả báo hết hạn → lỗi rõ."""
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": "token-het-han-gia"},
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
        "https://chatgpt.com/auth/login": {"tin_hieu": [], "cookie": {}},
        "https://chatgpt.com/": {"tin_hieu": [], "cookie": {}},
    })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_lay_duoc_phien"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_loi_la_dong_cua(mod):
    """Trình duyệt ném lỗi lạ ở chỗ quyết định → đóng cửa: không thành công
    âm thầm, mã lỗi rõ, không thêm tài khoản."""
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), TrinhDuyetLoi()))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi  # mã lỗi rõ, không rỗng
    assert mod.liet_ke_tai_khoan() == []


def test_bi_mat_khong_vao_log(mod, caplog):
    """Bí mật (token) không lọt vào log khi tái dùng hồ sơ."""
    import logging
    td = TrinhDuyetGia(trang={
        "https://chatgpt.com/auth/login": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"access_token": TOKEN_GIA_OK},
        }})
    with caplog.at_level(logging.DEBUG):
        chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN_GIA_OK not in nhat_ky


def test_hai_module_cung_ho_so_doc_lap(tmp_path, monkeypatch):
    """Hai module (kho riêng id khác nhau) cùng một hồ sơ → gỡ bên này không
    động tài khoản bên kia, không động hồ sơ Google."""
    mod1 = _mod_moi(tmp_path, monkeypatch)
    mod2 = _mod_moi(tmp_path, monkeypatch)
    mod2.kho = kho.kho_rieng("gemini-web")  # id kho khác, cùng thư mục dữ liệu

    def td_moi():
        return TrinhDuyetGia(trang={
            "https://chatgpt.com/auth/login": {
                "tin_hieu": ["da_dang_nhap"],
                "cookie": {"access_token": TOKEN_GIA_OK},
            }})

    chay(mod1.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    chay(mod2.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    assert len(mod1.liet_ke_tai_khoan()) == 1
    assert len(mod2.liet_ke_tai_khoan()) == 1
    mod1.go_tai_khoan(mod1.liet_ke_tai_khoan()[0]["khoa"])
    assert mod1.liet_ke_tai_khoan() == []
    assert len(mod2.liet_ke_tai_khoan()) == 1  # không bị động
