"""Test tab riêng + thực thể trạng thái của module youtube (NV301 mảnh tab).

Mảnh này chỉ gồm: manifest `tab_web`, trang tĩnh modules/youtube/web/index.html,
thực thể đọc trạng thái (thuc_the/trang_thai) và hành động đọc `trang_thai`
qua thuc_hien — chưa có nút điều khiển (điều khiển đi theo mảnh lõi/giao diện).
Trang tĩnh không chứa bí mật và mọi lệnh đều đi qua hành động thực thể của lõi
(phiên quản trị), KHÔNG gọi thẳng đường integration (đường đó cần khoá khách
tich_hop@youtube, trình duyệt không có).

Chạy async qua asyncio.run (kho chưa có pytest-asyncio), theo khuôn
test_module_youtube_nv277.py. Không Internet thật.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

from app.kham_pha import kiem_manifest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube"
MANIFEST_PATH = THU_MUC_MODULE / "manifest.json"
MODULE_PY = THU_MUC_MODULE / "module.py"
TRANG_TAB = THU_MUC_MODULE / "web" / "index.html"
VIDEO_ID = "dQw4w9WgXcQ"


def tai_module():
    ten = "mod_youtube_tab_test"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, MODULE_PY, submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


def tao_module(tmp_path):
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=str(tmp_path))

    class TrichXuatGia:
        async def tim_kiem(self, source, query, limit):
            return []

        async def giai_am_thanh(self, source, target):
            return {"url": "fake://am-thanh-1", "title": "Bài giả",
                    "duration": 213, "source": source}

    m.trich_xuat = TrichXuatGia()
    return m


def test_manifest_co_tab_web_hop_le():
    man = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert kiem_manifest(man) is None
    tab = man.get("tab_web")
    assert isinstance(tab, dict)
    assert isinstance(tab.get("nhan"), str) and tab["nhan"].strip()
    assert tab.get("trang") == "web/index.html"
    assert TRANG_TAB.is_file()


def test_trang_tab_khong_chua_bi_mat_va_goi_qua_thuc_the():
    html = TRANG_TAB.read_text(encoding="utf-8")
    js = (THU_MUC_MODULE / "web" / "trang.js").read_text(encoding="utf-8")
    assert "trang.js" in html
    # Trang gọi lõi qua hành động thực thể, không gọi thẳng integration.
    assert "api/thiet-bi" in js
    assert "hanh-dong" in js
    # Trang tĩnh phục vụ cả khi chưa liên kết: không được chứa khoá/token.
    assert "Bearer" not in html + js
    assert "tich_hop@" not in html + js
    assert "integration" not in html + js
    assert 'id="trang-thai"' in html or "id='trang-thai'" in html


def test_thuc_the_co_thuc_the_trang_thai_khong_lo_bi_mat(tmp_path):
    m = tao_module(tmp_path)
    ds = asyncio.run(m.thuc_the())
    assert ds, "module youtube phải có ít nhất một thực thể cho trang Đời sống"
    tt = [e for e in ds if str(e.get("id", "")).startswith("youtube.")]
    assert tt
    for e in tt:
        assert isinstance(e.get("ten"), str) and e["ten"].strip()
        assert isinstance(e.get("mo_ta"), str) and e["mo_ta"].strip()
        assert isinstance(e.get("tinh_nang"), list)
    dump = json.dumps(ds, ensure_ascii=False)
    assert "Bearer" not in dump
    assert "tich_hop@" not in dump


def test_trang_thai_chua_phat_dang_phat_dung(tmp_path):
    m = tao_module(tmp_path)
    ds = asyncio.run(m.thuc_the())
    eid = ds[0]["id"]
    tt = asyncio.run(m.trang_thai([eid]))
    assert tt[eid]["gia_tri"] == "chua_phat"
    ma, _, _ = asyncio.run(m.tich_hop("integration/play", "POST", {},
                                      {"source": "youtube", "target": VIDEO_ID}))
    assert ma == 200
    tt = asyncio.run(m.trang_thai([eid]))
    assert tt[eid]["gia_tri"] == "dang_phat"
    ma, _, _ = asyncio.run(m.tich_hop("integration/stop", "POST", {}, {}))
    assert ma == 200
    tt = asyncio.run(m.trang_thai([eid]))
    assert tt[eid]["gia_tri"] == "dung"


def test_thuc_hien_trang_thai_va_loi(tmp_path):
    m = tao_module(tmp_path)
    ds = asyncio.run(m.thuc_the())
    eid = ds[0]["id"]
    kq = asyncio.run(m.thuc_hien(eid, "trang_thai", {}))
    assert kq.get("ok") is True
    assert kq.get("trang_thai") == "chua_phat"
    assert asyncio.run(m.thuc_hien("youtube.khong-co", "trang_thai", {})).get("ok") is False
    assert asyncio.run(m.thuc_hien(eid, "hanh_dong_khong_co", {})).get("ok") is False
