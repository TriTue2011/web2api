"""Test NV93 cho module muse2api: «hồ sơ Google + tái dùng».

- Giữ cơ chế cookie hiện có (hatch_sess, giao thức trực tiếp muse.ai).
- `tai_dung_ho_so(ho_so, trinh_duyet)`: THỬ «Đăng nhập bằng Google» trên muse.ai;
  nếu Muse không hỗ trợ Google SSO (NV90-C: SSO Meta → can_nguoi + hướng dẫn
  noVNC) → ghi rõ, giữ đường làm mới/đăng nhập lại hiện có.
- `liet_ke_tai_khoan()` không lộ bí mật; `go_tai_khoan(khoa)` gỡ một.

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

import pytest

from app import kho
from app.khoi_phuc_phien import TrinhDuyetGia
from app.tai_khoan_hang_loat import KetQuaDangNhap

from ho_tro_nv93 import TrinhDuyetChuyenDoi, TrinhDuyetLoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "muse2api"
TEN_GOI = "muse2api_nv93_mod_test"

HATCH_SESS_GIA = "hatch-sess-gia-nv93-0001"
EMAIL_GIA = "ban-gia-nv93@example.com"
HO_SO_GIA = "hs-gia-nv93-0001"


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    for ten_tep in ("chat", "module", "protocol"):
        p = THU_MUC / f"{ten_tep}.py"
        if not p.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            f"{TEN_GOI}.{ten_tep}", p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


def _mod_moi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    mod = Module(dict(MANIFEST))
    mod.kho = kho.kho_rieng("muse2api")
    return mod


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _mod_moi(tmp_path, monkeypatch)


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


# ───────────── tai_dung_ho_so ─────────────

def test_tai_dung_ho_so_co_che_google_sso(mod):
    """Muse hỗ trợ Google SSO trong hồ sơ → bấm Google → SSO chuyển về đã
    đăng nhập → lấy cookie hatch_sess (chuyển trạng thái sau bấm)."""
    td = TrinhDuyetChuyenDoi(
        trang={
            "https://muse.ai/": {"tin_hieu": [], "cookie": {}},
        },
        sau_bam={
            "https://muse.ai/": {
                "url": None,
                "trang": {"tin_hieu": ["da_dang_nhap"],
                          "cookie": {"hatch_sess": HATCH_SESS_GIA}},
            },
        })
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" in ten_buoc
    assert "dien" not in ten_buoc
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert ds[0]["ho_so"] == HO_SO_GIA


def test_tai_dung_ho_so_di_tat_khi_da_co_phien(mod):
    """Hồ sơ đã có cookie hatch_sess còn hiệu lực → đi tắt."""
    td = TrinhDuyetGia(trang={
        "https://muse.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"hatch_sess": HATCH_SESS_GIA},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong, kq.ma_loi
    ten_buoc = [h[0] for h in td.hanh_dong]
    assert "bam" not in ten_buoc
    assert "dien" not in ten_buoc


def test_tai_dung_ho_so_khong_google_sso_thi_can_nguoi_hoac_ghi_ro(mod):
    """Muse không có Google SSO (SSO Meta) → can_nguoi + hướng dẫn, không đoán."""
    td = TrinhDuyetGia(trang={
        "https://muse.ai/": {
            "tin_hieu": [],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    # không thành công âm thầm; phải là can_nguoi hoặc lỗi rõ
    assert not kq.thanh_cong
    assert kq.ma_loi in ("can_nguoi", "khong_ho_tro")


# ───────────── liet_ke / go_tai_khoan ─────────────

def test_liet_ke_khong_lo_bi_mat_va_go_tai_khoan(mod):
    td = TrinhDuyetGia(trang={
        "https://muse.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"hatch_sess": HATCH_SESS_GIA},
        }})
    chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    assert HATCH_SESS_GIA not in json.dumps(ds)
    mod.go_tai_khoan(ds[0]["khoa"])
    assert mod.liet_ke_tai_khoan() == []


# ───────────── tai_dung_ho_so: các trường hợp lỗi ─────────────

def test_tai_dung_ho_so_khong_co_trinh_duyet_thi_can_nguoi(mod):
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), None))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"


def test_tai_dung_ho_so_gap_captcha_thi_can_nguoi(mod):
    td = TrinhDuyetGia(trang={
        "https://muse.ai/": {
            "tin_hieu": ["captcha"],
            "cookie": {},
        }})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert mod.liet_ke_tai_khoan() == []


def test_tai_dung_ho_so_loi_la_dong_cua(mod):
    """Trình duyệt ném lỗi lạ ở chỗ quyết định → đóng cửa."""
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), TrinhDuyetLoi()))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi
    assert mod.liet_ke_tai_khoan() == []


def test_bi_mat_khong_vao_log(mod, caplog):
    """Bí mật (hatch_sess) không lọt vào log khi tái dùng hồ sơ."""
    import logging
    td = TrinhDuyetGia(trang={
        "https://muse.ai/": {
            "tin_hieu": ["da_dang_nhap"],
            "cookie": {"hatch_sess": HATCH_SESS_GIA},
        }})
    with caplog.at_level(logging.DEBUG):
        chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    nhat_ky = "\n".join(r.getMessage() for r in caplog.records)
    assert HATCH_SESS_GIA not in nhat_ky


def test_hai_module_cung_ho_so_doc_lap(tmp_path, monkeypatch):
    """Hai module (kho riêng id khác nhau) cùng một hồ sơ → gỡ bên này không
    động tài khoản bên kia."""
    mod1 = _mod_moi(tmp_path, monkeypatch)
    mod2 = _mod_moi(tmp_path, monkeypatch)
    mod2.kho = kho.kho_rieng("chatgpt-web")  # id kho khác, cùng thư mục dữ liệu

    def td_moi():
        return TrinhDuyetGia(trang={
            "https://muse.ai/": {
                "tin_hieu": ["da_dang_nhap"],
                "cookie": {"hatch_sess": HATCH_SESS_GIA},
            }})

    chay(mod1.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    chay(mod2.tai_dung_ho_so(_ho_so_gia(), td_moi()))
    assert len(mod1.liet_ke_tai_khoan()) == 1
    assert len(mod2.liet_ke_tai_khoan()) == 1
    mod1.go_tai_khoan(mod1.liet_ke_tai_khoan()[0]["khoa"])
    assert mod1.liet_ke_tai_khoan() == []
    assert len(mod2.liet_ke_tai_khoan()) == 1  # không bị động
