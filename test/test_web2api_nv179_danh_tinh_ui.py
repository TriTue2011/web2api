"""NV179 — Chrome THẬT: mục «Danh tính» ở Hệ thống → AI (He-thong-AI.dc.html#danh-tinh).

Soát NV155b (4.1/4.2/4.3 + liên kết chéo 5.2) chỉ ra: khối chọn danh tính của
«Thêm tài khoản» lọc bỏ danh tính CHƯA gắn hồ sơ trình duyệt — những danh tính đó
không hiện ở đâu; email đã che không có chú thích; hai nghĩa «tái dùng» không
liên kết chéo. Test này mở trang thật và kiểm:

- Mục «Danh tính» liệt kê MỌI danh tính: cả danh tính chưa gắn hồ sơ.
- Email hiện dạng che + có chú thích «đã che»; email thô không xuất hiện.
- Nhãn chứa HTML hiện thành CHỮ, không chạy (XSS).
- Nút «Kiểm lại đăng nhập» chỉ có ở danh tính đã gắn hồ sơ; bấm → trạng thái
  đổi theo tín hiệu của dịch vụ giả («đã đăng nhập»).
- Xoá cần HAI lần bấm (hỏi lại); xoá xong mất khỏi danh sách và khỏi kho.
- Có đoạn phân biệt hai nghĩa «tái dùng» + liên kết tới mục này ở khối chọn.
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
    w, dv, ids = moi_truong_ui179
    dv.tin_hieu_tra = {"da_dang_nhap"}
    _dang_nhap_quan_tri_lna(trang, w.url, tmp_path)
    _mo_trang_lna(trang, f"{w.url}/He-thong-AI.dc.html#danh-tinh", tmp_path,
                  cho=f"!!{_hang(ids['a'])}")

    # MỌI danh tính đều hiện — kể cả danh tính chưa gắn hồ sơ (phát hiện 4.1)
    assert trang.js("document.querySelectorAll('[data-muc-danh-tinh]').length") == 3
    assert "Chưa gắn hồ sơ trình duyệt" in trang.js(f"{_hang(ids['b'])}.textContent")
    assert "Đã gắn hồ sơ trình duyệt" in trang.js(f"{_hang(ids['a'])}.textContent")
    # Khung đã đăng ký mục «Danh tính» và tab đang mở đúng là nó
    assert trang.js("document.querySelector('#bang-ai').dataset.muc") == "danh_tinh"
    assert "Danh tính" in trang.js("document.querySelector('h1').textContent")
    # Điều hướng thật: mở bảng xổ «AI» → có liên kết tới #danh-tinh
    trang.bam("[...document.querySelectorAll('nav button')].find((b) => b.textContent.trim() === 'AI')",
              mo_ta="mở bảng xổ AI")
    trang.cho("!![...document.querySelectorAll('nav a')].find((a) => (a.getAttribute('href') || '').endsWith('#danh-tinh'))",
              han=10, mo_ta="liên kết Danh tính trong bảng xổ")

    # Email che + chú thích «đã che»; email thô không lọt ra trang (4.3)
    assert "c***@example.com" in trang.js(f"{_hang(ids['a'])}.textContent")
    assert "co-phien@example.com" not in trang.js("document.body.innerText")
    assert "đã che" in trang.js("document.getElementById('muc-danh-tinh').textContent")

    # Nhãn thù địch hiện thành CHỮ, không chạy
    assert trang.js("window.__xss179 === undefined")
    assert "<img src=x" in trang.js(f"{_hang(ids['c'])}.textContent")

    # Phân biệt hai nghĩa «tái dùng» (liên kết chéo 5.2)
    chu_muc = trang.js("document.getElementById('muc-danh-tinh').textContent")
    assert "Qua danh tính" in chu_muc and "Lấy cookie sang module" in chu_muc

    # «Kiểm lại đăng nhập»: chỉ danh tính có hồ sơ mới có nút; bấm → đã đăng nhập
    assert trang.js(f"!{_hang(ids['b'])}.querySelector('[data-dt-kiem]')")
    trang.bam(f"{_hang(ids['a'])}.querySelector('[data-dt-kiem]')", mo_ta="Kiểm lại đăng nhập")
    trang.cho(f"{_hang(ids['a'])}.textContent.includes('đã đăng nhập')",
              han=10, mo_ta="trạng thái sau kiểm lại")
    assert danh_tinh.lay(ids["a"])["trang_thai_dang_nhap"] == "dang_nhap"

    # Xoá: lần bấm đầu chỉ HỎI LẠI, danh tính còn nguyên; lần hai mới xoá
    trang.bam(f"{_hang(ids['b'])}.querySelector('[data-dt-xoa]')", mo_ta="Xoá (lần 1)")
    trang.cho(f"{_hang(ids['b'])}.querySelector('[data-dt-xoa]').textContent.includes('Bấm lần nữa')",
              han=10, mo_ta="nút xoá đổi nhãn hỏi lại")
    assert trang.js(f"!!{_hang(ids['b'])}")
    assert danh_tinh.lay(ids["b"]) is not None
    trang.bam(f"{_hang(ids['b'])}.querySelector('[data-dt-xoa]')", mo_ta="Xoá (lần 2)")
    trang.cho(f"!{_hang(ids['b'])}", han=10, mo_ta="danh tính biến mất sau xoá")
    assert danh_tinh.lay(ids["b"]) is None
    assert trang.js("window.__xss179 === undefined")
