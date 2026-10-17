"""Test NV93 cho module grok-web: «hồ sơ Google + tái dùng».

- Cloudflare của grok.com: NHẬN BẰNG CẤU TRÚC (403/503 + `cf-mitigated: challenge`)
  → can_nguoi NGAY, không thử lại (NV90-A). KHÔNG tự tích/giải.
- Sau khi người qua (noVNC): «Sign in with Google» → SSO → đọc TẤT CẢ cookie
  grok.com trừ `cf_*` (giữ cf_clearance, __cf_bm).
- KHÔNG đoán tên cookie phiên: «đã đăng nhập» = phiên dùng được; chỗ chưa chắc
  ghi «CHƯA KIỂM».

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
THU_MUC = GOC / "modules" / "grok-web"
TEN_GOI = "grok_web_nv93_mod_test"

SSO_GIA = "sso-gia-nv93-0001"
CF_CLEARANCE_GIA = "cf-clearance-gia-0002"
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


def _may_chu_gia(cloudflare=False):
    def xu_ly(req):
        if cloudflare:
            return httpx.Response(
                403, text="xac minh",
                headers={"cf-mitigated": "challenge"})
        return httpx.Response(404, text="khong co")

    return httpx.MockTransport(xu_ly)


def _mod_moi(tmp_path, monkeypatch, cloudflare=False):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("grok-web")
    transport = _may_chu_gia(cloudflare)

    def tao_client(cookies):
        return mod_client.GrokWebClient(
            cookies, base_url="http://127.0.0.1:9",
            transport=transport)
    mod._tao_client = tao_client
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


# ───────────── Cloudflare → can_nguoi ─────────────

def test_gap_cloudflare_thi_can_nguoi_ngay(mod):
    """Trang báo thử thách (tín hiệu cloudflare) → can_nguoi, không thử lại."""
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {
            "tin_hieu": ["cloudflare"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_http_403_cf_mitigated_thi_can_nguoi(tmp_path, monkeypatch):
    """HTTP 403 + cf-mitigated: challenge → can_nguoi NGAY (nhận bằng cấu trúc,
    theo NV90-A; KHÔNG tự tích/giải, không thử lại)."""
    mod = _mod_moi(tmp_path, monkeypatch, cloudflare=True)
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {
            "tin_hieu": [],
            "cookie": {"sso": SSO_GIA},
        }})
    # kiểm phiên qua HTTP gặp 403 cf-mitigated → coi như thử thách
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


# ───────────── sau khi qua: đọc cookie, không đoán tên ─────────────

def test_tai_dung_ho_so_luong_day_du(mod):
    """Sau khi người qua thử thách (noVNC): bấm «Sign in with Google» → SSO
    trong hồ sơ → về grok.com đã đăng nhập (chuyển trạng thái sau bấm)."""
    td = TrinhDuyetChuyenDoi(
        trang={
            "https://grok.com/": {"tin_hieu": [], "cookie": {}},
        },
        sau_bam={
            "https://grok.com/": {
                "url": None,
                "trang": {"tin_hieu": ["da_dang_nhap"],
                          "cookie": {"sso": SSO_GIA,
                                     "nguoi_dung": "u-gia-0005"}},
            },
        })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" in ten_buoc  # bấm «Sign in with Google»
    assert "dien" not in ten_buoc
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA


def test_doc_tat_ca_cookie_tru_cf(mod):
    """Đọc tất cả cookie grok.com trừ cf_* (giữ cf_clearance, __cf_bm).

    KHÔNG đoán tên cookie phiên: «đã đăng nhập» = phiên dùng được; chỗ chưa
    chắc ghi «CHƯA KIỂM» trong mã/báo cáo.
    """
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {
                "sso": SSO_GIA,
                "cf_clearance": CF_CLEARANCE_GIA,
                "__cf_bm": "bm-gia-0003",
                "cf_chl_rc_ni": "chan-gia-0004",  # phải bỏ
                "nguoi_dung": "u-gia-0005",
            },
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert kq.thanh_cong, kq.ma_loi
    # cookie đã lưu không chứa cf_chl_*
    # (kiểm gián tiếp: liệt kê không lộ, và kho không có cf_chl_)
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert "cf_chl_rc_ni" not in json.dumps(ds)


def test_go_tai_khoan_khong_dong_ho_so(mod):
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"sso": SSO_GIA},
        }})
    chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert len(mod.liet_ke_tai_khoan()) == 1
    mod.go_tai_khoan(mod.liet_ke_tai_khoan()[0]["khoa"])
    assert mod.liet_ke_tai_khoan() == []


# ───────────── tai_dung_ho_so: các trường hợp lỗi ─────────────

def test_tai_dung_ho_so_khong_co_trinh_duyet_thi_can_nguoi(mod):
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), None))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_tai_dung_ho_so_loi_la_dong_cua(mod):
    """Trình duyệt ném lỗi lạ ở chỗ quyết định → đóng cửa."""
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), TrinhDuyetLoi()))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi
    assert mod.liet_ke_tai_khoan() == []


def test_bi_mat_khong_vao_log(mod, caplog):
    """Bí mật (cookie) không lọt vào log khi tái dùng hồ sơ."""
    import logging
    td = TrinhDuyetGia(trang={
        "https://grok.com/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"sso": SSO_GIA},
        }})
    with caplog.at_level(logging.DEBUG):
        chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert SSO_GIA not in nhat_ky


def test_hai_module_cung_ho_so_doc_lap(tmp_path, monkeypatch):
    """Hai module (kho riêng id khác nhau) cùng một hồ sơ → gỡ bên này không
    động tài khoản bên kia."""
    mod1 = _mod_moi(tmp_path, monkeypatch)
    mod2 = _mod_moi(tmp_path, monkeypatch)
    mod2.kho = kho.kho_rieng("chatgpt-web")  # id kho khác, cùng thư mục dữ liệu

    def td_moi():
        return TrinhDuyetGia(trang={
            "https://grok.com/": {
                "tin_hieu": ["da_dang_nhap"],
                "cookie": {"sso": SSO_GIA},
            }})

    chay(mod1.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    chay(mod2.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    assert len(mod1.liet_ke_tai_khoan()) == 1
    assert len(mod2.liet_ke_tai_khoan()) == 1
    mod1.go_tai_khoan(mod1.liet_ke_tai_khoan()[0]["khoa"])
    assert mod1.liet_ke_tai_khoan() == []
    assert len(mod2.liet_ke_tai_khoan()) == 1  # không bị động


def test_models_tra_danh_sach_on_dinh(mod):
    """models() trả danh sách mã ổn định (nhãn tách riêng nếu có)."""
    ds = chay(mod.models())
    assert isinstance(ds, list)
    for m in ds:
        assert "id" in m and isinstance(m["id"], str) and m["id"]
