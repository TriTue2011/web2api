"""NV263 — Bỏ tab «Trình duyệt» khỏi thanh điều hướng (không xoá tab_web).

- modules/vnc-xa/manifest.json: tab_web giữ nguyên (nhan/trang) + cờ
  `an_khoi_thanh: true`.
- GET /api/tinh-nang: mục tab của vnc-xa VẪN có (trang Module nhúng iframe
  và trang AI dùng đường dẫn trực tiếp) nhưng mang cờ `an_khoi_thanh: true`.
- web/khung.js: navHeThong BỎ QUA mục tab có cờ — thanh điều hướng
  (điện thoại 390px lẫn máy tính) không còn «Trình duyệt».
- Trang /m/vnc-xa/index.html vẫn mở được trực tiếp.
- Trang AI: không còn liên kết «Mở tab Trình duyệt»; thay bằng liên kết
  «Mở trình duyệt» mở thẳng trang module.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]

KHOA_QT = "khoa-quan-tri-test-nv263-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


class _DichVuTrinhDuyetGia(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/ho-so") or self.path == "/ho-so":
            body = json.dumps({"ho_so": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):  # noqa: ANN002, ANN202
        pass


@pytest.fixture(scope="module")
def dich_vu_trinh_duyet_gia():
    srv = HTTPServer(("127.0.0.1", 0), _DichVuTrinhDuyetGia)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="function")
def moi_truong(tmp_path_factory, monkeypatch, dich_vu_trinh_duyet_gia):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.so_module import SoModule
    from gia import ghi_thu_muc

    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    data_dir = tmp_path_factory.mktemp("nv263-data")
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)

    tep = {}
    for p in (GOC / "modules" / "vnc-xa").iterdir():
        if p.is_file():
            tep[p.name] = p.read_bytes()
    # thư mục web/ của module (trang workspace)
    for p in (GOC / "modules" / "vnc-xa" / "web").iterdir():
        if p.is_file():
            tep[f"web/{p.name}"] = p.read_bytes()
    ghi_thu_muc(data_dir / "modules" / "vnc-xa", tep)

    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        r = client.post("/api/modules/nap-lai", headers=QT)
        assert r.status_code == 200, r.text
        r = client.post("/api/modules/vnc-xa/cai", headers=QT, json={
            "cau_hinh": {"dich_vu_url": dich_vu_trinh_duyet_gia,
                         "khoa_dich_vu": "khoa-gia-nv263"}})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True, r.text
        yield client


def _tab_vnc_xa(moi_truong):
    client = moi_truong
    r = client.get("/api/tinh-nang", headers=QT)
    assert r.status_code == 200, r.text
    tabs = [t for t in r.json().get("tab", []) if t.get("module") == "vnc-xa"]
    assert tabs, "vnc-xa đang chạy phải có mục tab trong tinh-nang"
    return tabs[0]


def test_manifest_giu_tab_web_va_co_an_khoi_thanh():
    man = json.loads((GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8"))
    tab = man.get("tab_web")
    assert isinstance(tab, dict), "KHÔNG được xoá tab_web khỏi manifest"
    assert tab.get("nhan") == "Trình duyệt"
    assert tab.get("trang") == "web/index.html"
    assert tab.get("an_khoi_thanh") is True, "tab_web phải khai an_khoi_thanh: true"


def test_tinh_nang_truyen_co_an_khoi_thanh(moi_truong):
    tab = _tab_vnc_xa(moi_truong)
    assert tab.get("an_khoi_thanh") is True
    assert tab.get("duong") == "m/vnc-xa/index.html"


def test_khung_js_bo_qua_tab_an_khoi_thanh():
    src = (GOC / "web" / "khung.js").read_text(encoding="utf-8")
    assert "an_khoi_thanh" in src, "khung.js phải đọc cờ an_khoi_thanh"
    # navHeThong là nơi duy nhất dựng mục tab module trên thanh điều hướng
    i = src.find("function navHeThong")
    assert i >= 0
    doan = src[i:i + 2500]
    assert "an_khoi_thanh" in doan, "navHeThong phải bỏ qua tab có cờ an_khoi_thanh"


def test_trang_vnc_xa_van_mo_duoc_truc_tiep(moi_truong):
    r = moi_truong.get("/m/vnc-xa/index.html", headers=QT)
    assert r.status_code == 200, r.text[:200]
    assert "Workspace trình duyệt" in r.text


def test_ai_page_khong_con_mo_tab_trinh_duyet():
    src = (GOC / "web" / "He-thong-AI.dc.html").read_text(encoding="utf-8")
    assert "Mở tab {{them.td.nhanTrinhDuyet}}" not in src
    assert "mở tab Trình duyệt để đăng nhập tay" not in src
    # Chủ máy chốt lại 28/10/2026 («tôi đã yêu cầu bỏ trình duyệt mà sao vẫn còn»): KHÔNG còn liên kết «Mở trình duyệt» rời; trình
    # duyệt từ xa chỉ mở bằng nút «Đăng nhập tay» của từng tài khoản (test_nv274_dang_nhap_tay.py).
    assert "Mở trình duyệt" not in src
    assert "them.td.duongTrinhDuyet" not in src
    assert 'id="k268-dn-tay"' in src


def test_quyen_tab_web_khong_doi():
    """tab_web vẫn là quyền bool ở cấp manifest (NV15b) — thêm cờ ẩn không
    làm mất quyền, cũng không sinh quyền mới khi so với bản chưa có tab."""
    from app.quyen_module import diff_quyen
    man = json.loads((GOC / "modules" / "vnc-xa" / "manifest.json").read_text(encoding="utf-8"))
    man_khong_tab = {k: v for k, v in man.items() if k != "tab_web"}
    kq = diff_quyen(man_khong_tab, man)
    assert any(m.get("loai") == "tab_web" for m in kq["them"]), kq
    # cùng manifest (không đổi) → không sinh diff
    kq2 = diff_quyen(man, man)
    assert kq2["them"] == [] and kq2["bot"] == [], kq2
