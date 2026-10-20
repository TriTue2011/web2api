"""NV189 — test ĐỘT BIẾN (mutation): giết mutant của từng lớp sửa NV189.

Mỗi ca ĐỎ trên mã gốc (trước NV189) và XANH trên mã đã sửa:
- M1 (lớp A): bộ sinh JSON cameranha dựng được mảng JSON hợp lệ từ mo_ta
  văn xuôi (gốc trả BI_MAT_GIA không phải JSON → cài đỏ).
- M2 (lớp B): zalo-ca-nhan cai({}) ok + dùng URL mặc định (gốc ném
  ValueError khi dich_vu_url rỗng dù manifest bat_buoc=False).
- M3 (lớp B): suc_khoe có khoá «ok» cho grok-web và muse (gốc thiếu khoá,
  trái hợp đồng lõi app/kham_pha.py).
- M4 (lớp B): register TẮT báo «đang tắt», không còn dương tính giả
  «chưa cấu hình» (gốc gộp hai trạng thái).
- M5 (lớp A): maytram bat=true có thực thể / bat=false rỗng — fail-closed
  giữ nguyên (gốc thuc_the=[] khi tắt bị smoke đọc thành «module chết»).
- M6 (lớp A): openaicodex không công bố hành động «doc» → smoke SKIP thay vì
  FAIL (gốc: assert chon ném AssertionError).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parent.parent
if str(GOC) not in sys.path:
    sys.path.insert(0, str(GOC))

from test_web2api_nv184_smoke_moi_module import (  # noqa: E402
    _hanh_dong_doc_hoac_skip,
    _mang_json_tu_mo_ta,
)


def _tai_module(ten_thu_muc: str):
    """Nạp modules/<ten-thu-muc>/module.py (tên thư mục có gạch ngang).
    Đăng ký package cha giả trong sys.modules để relative import
    (from .client import ...) chạy được."""
    from types import ModuleType

    ten_goi = f"nv189_{ten_thu_muc.replace('-', '_')}"
    duong = GOC / "modules" / ten_thu_muc
    goi = ModuleType(ten_goi)
    goi.__path__ = [str(duong)]  # type: ignore[attr-defined]
    sys.modules[ten_goi] = goi
    spec = importlib.util.spec_from_file_location(
        f"{ten_goi}.module", duong / "module.py")
    tep = importlib.util.module_from_spec(spec)
    sys.modules[f"{ten_goi}.module"] = tep
    spec.loader.exec_module(tep)
    return tep


zalo_mod = _tai_module("zalo-ca-nhan")
grok_mod = _tai_module("grok-web")
muse_mod = _tai_module("muse")
register_mod = _tai_module("register")
maytram_mod = _tai_module("maytram")
codex_mod = _tai_module("openaicodex")


def _chay(coro):
    return asyncio.run(coro)


# ── M1: bộ sinh JSON cameranha ────────────────────────────────────────────

MO_TA_CAMERA = json.load(
    open(GOC / "modules" / "cameranha" / "manifest.json")
)["truong_cau_hinh"]


def _mo_ta_danh_sach_camera() -> str:
    for t in MO_TA_CAMERA:
        if t.get("khoa") == "danh_sach_camera":
            return str(t.get("mo_ta") or "")
    raise AssertionError("manifest cameranha thiếu trường danh_sach_camera")


def test_m1_mo_ta_go2rtc_sinh_json_hop_le():
    mo_ta = _mo_ta_danh_sach_camera()
    assert "go2rtc" in mo_ta.lower()  # tiền đề: mo_ta thật có nhắc go2rtc
    ra = _mang_json_tu_mo_ta(mo_ta, "http://127.0.0.1:18999")
    ds = json.loads(ra)  # GỐC: trả BI_MAT_GIA → json.loads nổ
    assert isinstance(ds, list) and len(ds) == 1
    muc = ds[0]
    assert muc["kind"] == "go2rtc"
    assert muc["base"] == "http://127.0.0.1:18999"
    assert muc["src"]  # tên luồng sinh được


def test_m1_mo_ta_rtsp_only_dung_ip_rieng_gia():
    mo_ta = "Mảng JSON, mỗi phần tử: name (tên, vd 'Sân'), kind 'rtsp', url"
    ra = _mang_json_tu_mo_ta(mo_ta, "http://127.0.0.1:18999")
    ds = json.loads(ra)
    assert ds[0]["kind"] == "rtsp"
    url = ds[0]["url"]
    assert url.startswith("rtsp://") and "127.0.0.1" not in url


# ── M2: zalo-ca-nhan cai({}) ok + mặc định ────────────────────────────────

def test_m2_zalo_cai_rong_ok_va_dung_mac_dinh():
    m = zalo_mod.Module({})
    kq = _chay(m.cai({}))
    assert kq["ok"] is True, f"cai({{}}) đỏ: {kq}"  # GỐC: ok=False (cau_hinh_sai)
    luu = kq["cau_hinh_luu"]
    assert luu["dich_vu_url"] == "http://127.0.0.1:8703"


def test_m2_zalo_nap_rong_khong_tat_module():
    m = zalo_mod.Module({})
    m.nap({})  # GỐC: ValueError nội bộ → module tắt + warning
    assert m._dich_vu_url == "http://127.0.0.1:8703"


# ── M3: suc_khoe có khoá «ok» ─────────────────────────────────────────────

def test_m3_grok_web_suc_khoe_co_ok():
    m = grok_mod.Module()
    kq = _chay(m.suc_khoe())  # chưa cấu hình tài khoản
    assert "ok" in kq and isinstance(kq["ok"], bool)  # GỐC: thiếu khoá ok
    assert kq["ok"] is False


def test_m3_muse_suc_khoe_co_ok():
    m = muse_mod.Module({})
    kq = _chay(m.suc_khoe())  # chưa có device_key
    assert "ok" in kq and isinstance(kq["ok"], bool)  # GỐC: thiếu khoá ok
    assert kq["ok"] is False


# ── M4: register tắt ≠ chưa cấu hình ──────────────────────────────────────

def test_m4_register_tat_khong_bao_chua_cau_hinh():
    m = register_mod.Module({"tien_to": "register"})
    m._cau_hinh = {}  # nap({}) cũng được nhưng ghi đĩa; đặt trực tiếp đủ
    m._tt = {}
    kq = _chay(m.suc_khoe())
    assert kq["ok"] is False
    van = json.dumps(kq, ensure_ascii=False)
    assert "đang tắt" in van
    # GỐC: khi tắt vẫn báo «chưa cấu hình» → dương tính giả cho smoke (d)
    assert "chưa cấu hình" not in van


# ── M5: maytram fail-closed giữ nguyên ────────────────────────────────────

def test_m5_maytram_tat_khong_thuc_the(tmp_path):
    m = maytram_mod.Module({"tien_to": "maytram"}, tmp_path)
    m.nap({"bat": "false"})
    assert _chay(m.thuc_the(None)) == []  # fail-closed: tắt là đúng


def test_m5_maytram_bat_co_thuc_the(tmp_path):
    m = maytram_mod.Module({"tien_to": "maytram"}, tmp_path)
    m.nap({"bat": "true"})
    cua = _chay(m.thuc_the(None))
    assert len(cua) >= 1  # ít nhất thực thể quản lý
    assert any(e["id"].startswith("maytram.") for e in cua)


# ── M6: openaicodex không doc → skip ───────────────────────────────────────

def test_m6_openaicodex_khong_doc_thi_skip():
    m = codex_mod.Module({"tien_to": "openaicodex"})
    cua = _chay(m.thuc_the(None))  # thực thể thật, không cần đăng nhập
    assert len(cua) >= 1
    # GỐC: assert chon → AssertionError; nay phải SKIP (hàm thật của smoke)
    with pytest.raises(pytest.skip.Exception) as ghi:
        _hanh_dong_doc_hoac_skip(cua, "openaicodex")
    assert "không có hành động rủi ro «doc»" in str(ghi.value)
    assert "ghi" in str(ghi.value)  # nêu rui_ro thực tế tìm được


def test_m6_con_doc_thi_khong_skip():
    cua = [{"id": "x.1", "rui_ro_mac_dinh": "ghi",
            "tinh_nang": [{"hanh_dong": "xem", "rui_ro": "doc"}]}]
    assert _hanh_dong_doc_hoac_skip(cua, "x") == ("x.1", "xem")
