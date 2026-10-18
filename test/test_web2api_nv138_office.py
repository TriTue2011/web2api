"""Module van-phong — phần công cụ office_* qua container phụ (NV138).

- Công cụ `office_*` chỉ gọi dịch vụ van-phong-office khi người dùng bật
  (`office_bat`, mặc định TẮT); chưa bật hoặc không nối được → báo «chưa bật»
  (đóng khi lỗi, không lộ chi tiết), đường zip+XML của NV132 do module NV132 giữ.
- Công cụ ghi (set/add/remove/merge/import…) rủi ro `ghi`; công cụ đọc rủi ro
  `doc`; hành động ngoài tập office_* → từ chối.
- Đầu ra dịch vụ là DỮ LIỆU KHÔNG TIN: cắt ở trần, gắn `nguon_ngoai=True`.
- Khoá dịch vụ không vào log (một logging.Filter theo khuôn zalo-bot).

Máy chủ dịch vụ GIẢ cục bộ (monkeypatch httpx của module).
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "van-phong"

# Chuỗi GIẢ rõ ràng — không phải bí mật thật.
KHOA_GIA = "khoa-gia-van-phong-0123456789abcdef"
URL_GIA = "http://127.0.0.1:18701"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("van_phong_nv138", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    return md


@pytest.fixture()
def md():
    return _module_py()


@pytest.fixture()
def mod(md):
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    m.nap({"office_bat": False, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    return m


def test_manifest_hop_le(md):
    from app.kham_pha import kiem_manifest
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(manifest) is None
    assert manifest["id"] == "van-phong"


def test_cong_cu_office_du_rui_ro_doc_ghi(mod):
    thuc_the = {e["id"]: e for e in mod.thuc_the()}
    doc = thuc_the["van_phong.office_doc"]
    ghi = thuc_the["van_phong.office_ghi"]
    assert doc["rui_ro_mac_dinh"] == "doc"
    assert ghi["rui_ro_mac_dinh"] == "ghi"
    ten_doc = {t["hanh_dong"] if isinstance(t, dict) else t for t in doc["tinh_nang"]}
    ten_ghi = {t["hanh_dong"] if isinstance(t, dict) else t for t in ghi["tinh_nang"]}
    assert {"office_view", "office_get", "office_query"} <= ten_doc
    assert {"office_set", "office_add", "office_remove", "office_merge", "office_import"} <= ten_ghi
    assert not (ten_doc & ten_ghi), "một công cụ không thể vừa đọc vừa ghi"
    for ten in ten_doc | ten_ghi:
        assert ten.startswith("office_"), f"công cụ {ten!r} phải có tiền tố office_"


def test_chua_bat_thi_bao_chua_bat_va_khong_goi_mang(mod, md, monkeypatch):
    da_goi = []
    monkeypatch.setattr(md.httpx, "Client", lambda *a, **k: da_goi.append(1) or 1 / 0)
    kq = mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"})
    import asyncio
    kq = asyncio.get_event_loop().run_until_complete(kq) if not isinstance(kq, dict) else kq
    assert kq["ok"] is False
    assert "chưa bật" in kq["loi"]
    assert not da_goi, "chưa bật thì không được gọi mạng"


def _gia_lap_dich_vu(md, monkeypatch, tra_ve=None, ma=200):
    class TraLoiGia:
        status_code = ma

        def json(self):
            return tra_ve if tra_ve is not None else {"ok": True, "dau_ra": "xong"}

    class ClientGia:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **k):
            ClientGia.da_goi = (url, k.get("json"))
            return TraLoiGia()

    ClientGia.da_goi = None
    monkeypatch.setattr(md.httpx, "Client", ClientGia)
    return ClientGia


def test_bat_thi_goi_dich_vu_va_gan_nguon_ngoai(mod, md, monkeypatch):
    import asyncio
    _gia_lap_dich_vu(md, monkeypatch, {"ok": True, "dau_ra": "noi-dung-tai-lieu"})
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.get_event_loop().run_until_complete(
        mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"}))
    assert kq["ok"] is True
    assert kq["nguon_ngoai"] is True


def test_dau_ra_qua_tran_bi_cat_va_gan_nguon_ngoai(mod, md, monkeypatch):
    import asyncio
    _gia_lap_dich_vu(md, monkeypatch, {"ok": True, "dau_ra": "x" * (md.TRAN_DAU_RA + 5000)})
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.get_event_loop().run_until_complete(
        mod.thuc_hien("van_phong.office_doc", "office_get", {"tep": "a.docx"}))
    assert len(kq["dau_ra"]) <= md.TRAN_DAU_RA + 100
    assert kq["nguon_ngoai"] is True


def test_khong_noi_duoc_dich_vu_bao_chua_bat(mod, md, monkeypatch):
    import asyncio

    class ClientHong:
        def __init__(self, *a, **k):
            raise ConnectionError("mang hong gia")

    monkeypatch.setattr(md.httpx, "Client", ClientHong)
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.get_event_loop().run_until_complete(
        mod.thuc_hien("van_phong.office_doc", "office_view", {"tep": "a.docx"}))
    assert kq["ok"] is False
    assert "chưa bật" in kq["loi"]


def test_hanh_dong_ngoai_tap_bi_tu_choi(mod, md):
    import asyncio
    mod.nap({"office_bat": True, "dich_vu_url": URL_GIA, "khoa_dich_vu": KHOA_GIA})
    kq = asyncio.get_event_loop().run_until_complete(
        mod.thuc_hien("van_phong.office_doc", "office_mcp", {}))
    assert kq["ok"] is False


def test_bo_loc_log_chan_khoa_dich_vu(md):
    logger = logging.getLogger("httpx")
    ban_ghi = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                                f"Authorization: Bearer {KHOA_GIA}", None, None)
    da_chan = [f for f in logger.filters if getattr(f, "_van_phong_loc", False)]
    assert da_chan, "thiếu logging.Filter chặn khoá dịch vụ"
    assert not any(f.filter(ban_ghi) for f in da_chan), "khoá dịch vụ lọt qua log"


def test_cai_tu_choi_url_la(md):
    import asyncio
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    m = md.Module(manifest)
    kq = asyncio.get_event_loop().run_until_complete(
        m.cai({"office_bat": True, "dich_vu_url": "http://169.254.169.254/", "khoa_dich_vu": KHOA_GIA}))
    assert kq["ok"] is False
