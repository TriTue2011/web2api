"""NV179 — Chrome THẬT: (NV219 cập nhật, NV248 — bài học NV98) mục «Danh tính»
đã BỎ KHỎI phần AI (He-thong-AI.dc.html) có chủ ý — trang AI không còn tab
#danh-tinh, không còn khối [data-muc-danh-tinh]; kho danh_tinh lõi vẫn tồn
tại cho các module khác dùng.

Soát NV155b (4.1/4.2/4.3 + liên kết chéo 5.2) chỉ ra: khối chọn danh tính của
«Thêm tài khoản» lọc bỏ danh tính CHƯA gắn hồ sơ trình duyệt — những danh tính đó
không hiện ở đâu; email đã che không có chú thích; hai nghĩa «tái dùng» không
liên kết chéo. Test này mở trang thật và kiểm:
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from app import danh_tinh, novnc, so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc
from test_web2api_nv155c_nhan_workspace import MANIFEST_HSG, ModuleHSG
from test_web2api_nv155c_ui import (
    KHOA_DV_UI,
    DichVuWorkspaceNV155c,
    _dang_nhap_quan_tri_lna,
    _mo_trang_lna,
)
from ui_chung import CO_CHROME, trang, trinh_duyet  # noqa: F401
from ui_mau import module_ai

GOC_UI = Path(__file__).resolve().parents[1]
KHOA_QT_UI = "khoa-quan-tri-nv179-ui-0123456789ab"
QT_UI = {"Authorization": f"Bearer {KHOA_QT_UI}"}
HO_SO_UI = "cc" * 16
NHAN_XSS = '<img src=x onerror="window.__xss179=1">'


@pytest.fixture
def moi_truong_ui179(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(GOC_UI / "modules" / "vnc-xa", data / "modules" / "vnc-xa",
                    ignore=shutil.ignore_patterns("__pycache__"))
    ghi_thu_muc(data / "modules" / "ai-gia", module_ai())
    monkeypatch.setenv("WEB2API_DATA", str(data))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT_UI)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for so_ in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so_.clear()
    dv = DichVuWorkspaceNV155c()
    with MayChuThat(dv.app()) as s_dv:
        with MayChuThat(app, lifespan="on") as w:
            r = httpx.post(f"{w.url}/api/modules/vnc-xa/cai", headers=QT_UI,
                           json={"cau_hinh": {"dich_vu_url": s_dv.url, "khoa_dich_vu": KHOA_DV_UI}})
            assert r.status_code == 200, r.text
            r = httpx.post(f"{w.url}/api/modules/ai-gia/cai", headers=QT_UI,
                           json={"cau_hinh": {"api_keys": ["khoa-tot-gia-nv179"]}})
            assert r.status_code == 200 and r.json()["ok"], r.text
            app.state.so.dang_chay["ho-so-google"] = ModuleHSG(MANIFEST_HSG)
            a = danh_tinh.tao("co-phien@example.com", "Tài khoản chính")
            danh_tinh.cap_nhat_trang_thai(a["id"], ho_so=HO_SO_UI)
            b = danh_tinh.tao("chua-gan@example.com", "Chưa gắn")
            c = danh_tinh.tao("xss@example.com", NHAN_XSS)
            yield w, dv, {"a": a["id"], "b": b["id"], "c": c["id"]}
    for so_ in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so_.clear()


def _hang(id_: str) -> str:
    return f"document.querySelector('[data-muc-danh-tinh=\"{id_}\"]')"


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_muc_danh_tinh_chrome(trang, moi_truong_ui179, tmp_path):
    """NV219 cập nhật (NV248, bài học NV98): mục «Danh tính» đã bỏ khỏi phần
    AI có chủ ý — trang AI không còn tab #danh-tinh, không còn khối
    [data-muc-danh-tinh], nav không còn liên kết tới nó. Kho danh_tinh lõi
    vẫn tồn tại (cho module khác dùng) nhưng không hiện ở trang AI."""
    w, dv, ids = moi_truong_ui179
    dv.tin_hieu_tra = {"da_dang_nhap"}
    dv.ws[HO_SO_UI] = {"ten": "Hồ sơ danh tính", "loai": "firefox", "mo": False}
    _dang_nhap_quan_tri_lna(trang, w.url, tmp_path)
    _mo_trang_lna(trang, f"{w.url}/He-thong-AI.dc.html", tmp_path,
                  cho="!!document.getElementById('bang-ai')")

    # Không còn khối danh tính nào trên trang AI
    assert trang.js("document.querySelectorAll('[data-muc-danh-tinh]').length") == 0
    assert trang.js("document.getElementById('muc-danh-tinh')") is None
    # Tab AI không còn mục danh_tinh
    assert trang.js("document.querySelector('#bang-ai').dataset.muc") != "danh_tinh"
    # Nav không còn liên kết tới #danh-tinh
    assert trang.js("[...document.querySelectorAll('nav a')].every((a) => !(a.getAttribute('href') || '').endsWith('#danh-tinh'))")
    # Kho lõi vẫn còn (NV219 giữ lõi cho module khác) — chỉ không hiện ở AI
    assert danh_tinh.lay(ids["a"]) is not None
    assert danh_tinh.lay(ids["b"]) is not None
