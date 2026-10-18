"""Test NV103: muse2api «đăng nhập bằng Google» trong hồ sơ trình duyệt.

Lỗi cấu trúc: _QUY_TAC_TIN_HIEU của muse2api thiếu luật cookie «đã đăng nhập»
→ tín hiệu `da_dang_nhap` không bao giờ xuất hiện → vòng chờ 20s luôn hết và
trả `khong_ho_tro` dù đăng nhập thật đã thành công (đường tai_dung_ho_so NV93).

Các ca test KHÔNG giả trực tiếp tín hiệu: trang giả chỉ khai cookie/URL —
tín hiệu `da_dang_nhap` phải do bộ đánh giá LUẬT của TrinhDuyetGia sinh ra từ
cookie `hatch_sess` (cùng khuôn server.py `doc_tin_hieu`).

Trình duyệt GIẢ. Không Internet, không muse.ai thật.
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
from app.tai_khoan_hang_loat import KetQuaDangNhap

from ho_tro_nv93 import TrinhDuyetChuyenDoi

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "muse2api"
TEN_GOI = "muse2api_nv103_mod_test"

HATCH_SESS_GIA = "hatch-sess-gia-nv103-0001"
EMAIL_GIA = "ban-gia-nv103@example.com"
HO_SO_GIA = "hs-gia-nv103-0001"


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
        spec = importlib.util.spec_from_file_location(f"{TEN_GOI}.{ten_tep}", p)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{TEN_GOI}.{ten_tep}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{TEN_GOI}.module"]


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


def chay(coro):
    return asyncio.run(coro)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    m = Module(dict(MANIFEST))
    m.kho = kho.kho_rieng("muse2api")
    return m


def _ho_so_gia():
    return {"id": HO_SO_GIA, "email": EMAIL_GIA, "trang_thai": "da_dang_nhap"}


def _ngu_ngay(monkeypatch, mod_ngu):
    """Vòng chờ 20 lần sleep(1) trong module thành tức thì (chỉ trong test)."""
    async def _khong_cho(giay):
        return None
    monkeypatch.setattr(mod_ngu.asyncio, "sleep", _khong_cho)


# ───────────── (a) cookie hatch_sess → da_dang_nhap qua luật thật ─────────────

def test_tin_hieu_da_dang_nhap_tu_cookie_qua_luat(mod):
    """Bộ đánh giá luật sinh `da_dang_nhap` từ cookie hatch_sess (không giả
    trực tiếp tín hiệu): trang giả chỉ khai cookie, không khai tin_hieu."""
    td = TrinhDuyetChuyenDoi(
        trang={"https://muse.ai/": {"cookie": {"hatch_sess": HATCH_SESS_GIA}}})
    chay(td.mo_trang("https://muse.ai/"))
    tin_hieu = chay(td.tin_hieu(mod.quy_tac_tin_hieu()))
    assert "da_dang_nhap" in tin_hieu


def test_tai_dung_ho_so_cookie_hatch_sess_thanh_cong(mod, monkeypatch, caplog):
    """Sau bấm «Google» hồ sơ có cookie hatch_sess → tai_dung_ho_so thành công,
    tài khoản xuất hiện với khoá mờ tk-<băm>, hatch_sess thô không vào log."""
    _ngu_ngay(monkeypatch, mod_module)
    td = TrinhDuyetChuyenDoi(
        trang={"https://muse.ai/": {"cookie": {"hatch_native_auth_csrf": "abc"}}},
        sau_bam={"https://muse.ai/": {
            "url": None,
            "trang": {"cookie": {"hatch_sess": HATCH_SESS_GIA}},
        }})
    with caplog.at_level("INFO", logger="web2api.module.muse2api"):
        kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert kq.thanh_cong, kq.ma_loi
    ds = mod.liet_ke_tai_khoan()
    assert len(ds) == 1
    khoa = ds[0]["khoa"]
    assert khoa == "tk-" + __import__("hashlib").sha256(
        HATCH_SESS_GIA.encode("utf-8")).hexdigest()[:12]
    log_gop = "".join(caplog.text)
    assert HATCH_SESS_GIA not in log_gop
    assert "web2api" in log_gop  # chắc chắn caplog đã bắt log của module


# ───────────── (b) không bao giờ có hatch_sess → khong_ho_tro ─────────────

def test_tai_dung_ho_so_khong_vao_duoc(mod, monkeypatch):
    """Hết vòng chờ mà không có cookie hatch_sess → `khong_ho_tro` (NV90-C),
    không thêm tài khoản nào."""
    _ngu_ngay(monkeypatch, mod_module)
    td = TrinhDuyetChuyenDoi(
        trang={"https://muse.ai/": {"cookie": {}}})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_ho_tro"
    assert mod.liet_ke_tai_khoan() == []


# ───────────── (c) CAPTCHA/Cloudflare/2fa/xác minh → can_nguoi NGAY ─────────────

@pytest.mark.parametrize("co", ["captcha", "cloudflare", "2fa", "xac_minh"])
def test_tai_dung_ho_so_gap_thu_thach_can_nguoi_ngay(mod, monkeypatch, co):
    """Cờ thử thách/xác minh ngay trang đầu → `can_nguoi` NGAY, không bấm
    thêm nút nào (không cố vượt Cloudflare/CAPTCHA)."""
    _ngu_ngay(monkeypatch, mod_module)
    td = TrinhDuyetChuyenDoi(
        trang={"https://muse.ai/": {"tin_hieu": [co]}})
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert isinstance(kq, KetQuaDangNhap)
    assert not kq.thanh_cong
    assert kq.ma_loi == "can_nguoi"
    assert [h for h in td.hanh_dong if h[0] == "bam"] == []
    assert mod.liet_ke_tai_khoan() == []


# ───────────── (d) chống nhận nhầm tiền tố cookie ─────────────

def test_cookie_native_auth_khong_tinh_la_dang_nhap(mod, monkeypatch):
    """Chỉ có cookie `hatch_native_auth_*` (như số đo thật trên muse.ai chưa
    đăng nhập) KHÔNG được coi là đã đăng nhập → `khong_ho_tro`."""
    _ngu_ngay(monkeypatch, mod_module)
    td = TrinhDuyetChuyenDoi(
        trang={"https://muse.ai/": {"cookie": {
            "datr": "x",
            "hatch_native_auth_csrf": "x",
            "hatch_native_auth_temp": "x",
            "hatch_native_auth_device": "x",
            "hatch_native_auth_state": "x",
        }}})
    chay(td.mo_trang("https://muse.ai/"))
    assert "da_dang_nhap" not in chay(td.tin_hieu(mod.quy_tac_tin_hieu()))
    kq = chay(mod.tai_dung_ho_so(_ho_so_gia(), td))
    assert not kq.thanh_cong
    assert kq.ma_loi == "khong_ho_tro"
    assert mod.liet_ke_tai_khoan() == []


# ───────────── (C) lớp lỗi: mọi module tai_dung_ho_so phải có luật cookie ─────────────

def test_cau_truc_moi_module_tai_dung_ho_so_co_luat_cookie_da_dang_nhap(tmp_path, monkeypatch):
    """Lớp lỗi NV103: module web có tai_dung_ho_so nhưng thiếu luật cookie
    «đã đăng nhập» → đường tái dùng luôn hỏng. Quét MỌI module theo cấu trúc,
    không liệt kê tên module."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    thu_muc_mod = GOC / "modules"
    thieu = []
    for thu_muc in sorted(thu_muc_mod.iterdir()):
        tep_mod = thu_muc / "module.py"
        if not thu_muc.is_dir() or not tep_mod.exists():
            continue
        nguon = tep_mod.read_text(encoding="utf-8")
        if "async def tai_dung_ho_so" not in nguon:
            continue
        if "def quy_tac_tin_hieu" not in nguon:
            thieu.append(f"{thu_muc.name}: thieu quy_tac_tin_hieu")
            continue
        ten_goi = f"nv103_cau_truc_{thu_muc.name}"
        goi = types.ModuleType(ten_goi)
        goi.__path__ = [str(thu_muc)]
        sys.modules[ten_goi] = goi
        spec = importlib.util.spec_from_file_location(f"{ten_goi}.module", tep_mod)
        mod_test = importlib.util.module_from_spec(spec)
        sys.modules[f"{ten_goi}.module"] = mod_test
        spec.loader.exec_module(mod_test)
        try:
            tep_manifest = thu_muc / "manifest.json"
            cau_hinh = (json.loads(tep_manifest.read_text(encoding="utf-8"))
                        if tep_manifest.exists() else {"ten": "x"})
            obj = mod_test.Module(dict(cau_hinh))
        except Exception as e:  # module cần manifest thật mới khởi tạo
            thieu.append(f"{thu_muc.name}: khong khoi tao duoc ({e})")
            continue
        quy_tac = obj.quy_tac_tin_hieu() or []
        co_luat_cookie = any(
            isinstance(q, dict) and q.get("loai") == "cookie"
            and q.get("tin_hieu") == "da_dang_nhap" and q.get("ten")
            for q in quy_tac)
        if not co_luat_cookie:
            thieu.append(f"{thu_muc.name}: thieu luat cookie cho da_dang_nhap")
        for ten in [t for t in list(sys.modules) if t.startswith(ten_goi)]:
            del sys.modules[ten]
    assert thieu == [], f"module thieu luat cookie da_dang_nhap: {thieu}"
