
"""NV155c E2 — Chrome THẬT: nút «Nhận làm tài khoản» trên thẻ workspace đã đóng
của trang vnc-xa (/m/vnc-xa/index.html).

Kiểm: nút chỉ hiện trên thẻ ĐÃ ĐÓNG; bấm → form hỏi email; nhận thành công →
thông báo kết quả + bước kế (Hệ thống → AI → Thêm tài khoản → Qua danh tính);
tên workspace chứa HTML hiện thành chữ, không chạy (XSS); email sai → báo lỗi
ngay phía client. Trang tĩnh còn lại (innerHTML…) đã có test trong
test_web2api_vnc_xa_ui.py.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import novnc, so_module
from app.main import app
from gia import MayChuThat
from test_web2api_nv155c_nhan_workspace import (
    MANIFEST_HSG, ModuleHSG, QT,
)
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

GOC_UI = Path(__file__).resolve().parents[1]
KHOA_QT_UI = "<redacted>"
QT_UI = {"Authorization": f"Bearer {KHOA_QT_UI}"}
KHOA_DV_UI = "khoa-dich-vu-GIA-155c-0000"
HO_SO_UI = "cc" * 16
HO_SO_UI_MO = "dd" * 16
TEN_XSS_UI = '<img src=x onerror="window.__xss=1">'


class DichVuWorkspaceNV155c:
    """Dịch vụ trình duyệt giả theo hợp đồng của module vnc-xa + API hồ sơ.

    NV155c-SUA: dịch vụ THẬT không cho /tin-hieu trên hồ sơ ĐÃ ĐÓNG (404) —
    kiểm phiên của workspace đã đóng đọc qua KHO COOKIE của hồ sơ
    (GET /ho-so/{id}/cookie). Tin hiệu giả `tin_hieu_tra` được dịch sang
    kho cookie tương ứng (có «da_dang_nhap» → có cookie SID của google.com).
    """

    def __init__(self):
        self.ws: dict[str, dict] = {}
        self.tin_hieu_tra: set = set()

    def _sai(self, request) -> bool:
        return request.headers.get("authorization") != f"Bearer {KHOA_DV_UI}"

    async def ho_so(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        if request.method == "POST":
            than = await request.json()
            id_ = "ee" * 16
            self.ws[id_] = {"ten": than["ten"], "loai": than.get("loai", "firefox"), "mo": False}
            return JSONResponse({"ho_so": id_, "ten": than["ten"]})
        return JSONResponse([{"ho_so": k, "ten": v["ten"], "loai": v["loai"],
                              "trang_thai": "dang_mo" if v["mo"] else "dong",
                              "dang_mo": v["mo"], "thu_cong": v["mo"], "luc_mo": None}
                             for k, v in sorted(self.ws.items())])

    async def tin_hieu(self, request):
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        return JSONResponse({"tin_hieu": sorted(self.tin_hieu_tra)})

    async def cookie(self, request):
        # Hợp đồng như dịch vụ THẬT: GET /ho-so/{id}/cookie?mien=<miền> →
        # {"cookie": "ten=gia_tri; ..."} đọc từ kho của hồ sơ ĐÃ ĐÓNG.
        if self._sai(request):
            return JSONResponse({"detail": "sai"}, status_code=401)
        id_ = request.path_params["id"]
        if id_ not in self.ws:
            return JSONResponse({"detail": "không có hồ sơ"}, status_code=404)
        mien = request.query_params.get("mien", "")
        chuoi = ""
        # «da_dang_nhap» giả → kho cookie có cookie phiên của google.com
        # (khớp quy tắc cookie của ModuleHSG).
        if "da_dang_nhap" in self.tin_hieu_tra and mien.endswith("google.com"):
            chuoi = "SID=gia-tri-gia-lap"
        return JSONResponse({"cookie": chuoi})

    def app(self):
        return Starlette(routes=[
            Route("/ho-so", self.ho_so, methods=["GET", "POST"]),
            Route("/ho-so/{id}/tin-hieu", self.tin_hieu, methods=["POST"]),
            Route("/ho-so/{id}/cookie", self.cookie, methods=["GET"]),
        ])


@pytest.fixture
def moi_truong_ui(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(GOC_UI / "modules" / "vnc-xa", data / "modules" / "vnc-xa",
                    ignore=shutil.ignore_patterns("__pycache__"))
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
            app.state.so.dang_chay["ho-so-google"] = ModuleHSG(MANIFEST_HSG)
            yield w, dv
    for so_ in (novnc._VE, novnc._PHIEN, novnc._HO_SO, novnc._WS):
        so_.clear()


def _the(ho_so: str) -> str:
    return f"[...document.querySelectorAll('.ws')].find(o => o.dataset.hoSo === '{ho_so}')"


def _mo_trang_lna(trang, url: str, tmp_path, cho: str | None = None):
    """Mở trang qua file:// + meta-refresh (vượt chặn Local Network Access của
    Chromium ≥138: about:blank là «public» nên bị chặn khi điều hướng thẳng
    tới 127.0.0.1). Cookie/phiên vẫn dùng chung ngữ cảnh trình duyệt."""
    boot = Path(tmp_path) / f"boot-{abs(hash(url)) % 100000}.html"
    boot.write_text(f'<meta http-equiv="refresh" content="0;url={url}">', encoding="utf-8")
    trang.cdp.goi("Page.navigate", {"url": f"file://{boot}"}, trang.s)
    trang.cho(cho or "(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
              han=20, mo_ta="trang đích dựng xong (qua LNA bypass)")


def _dang_nhap_quan_tri_lna(trang, base_url: str, tmp_path,
                            ten: str = "admin_kiem_thu_nv155c",
                            mat_khau: str = "MatKhauAdminBaoMat123456"):
    import asyncio
    from app import nguoi_dung
    try:
        asyncio.run(nguoi_dung.tao_nguoi_dung(vai="admin", ten_dang_nhap=ten, mat_khau=mat_khau))
    except Exception:
        pass
    _mo_trang_lna(trang, f"{base_url}/Dang-nhap.dc.html", tmp_path)
    trang.go("#o-ten", ten)
    trang.go("#o-mat-khau", mat_khau)
    trang.bam("document.getElementById('nut-dang-nhap')", mo_ta="Bấm nút đăng nhập")
    trang.cho("!/(Dang-nhap|index)/.test(location.pathname) && "
              "(document.querySelector('x-dc')||{getAttribute(){}}).getAttribute('data-dc') === 'san-sang'",
              han=20, mo_ta="Đăng nhập xong và trang đích dựng xong")


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_nut_nhan_lam_tai_khoan_chrome(trang, moi_truong_ui, tmp_path):
    w, dv = moi_truong_ui
    dv.ws[HO_SO_UI] = {"ten": TEN_XSS_UI, "loai": "firefox", "mo": False}
    dv.ws[HO_SO_UI_MO] = {"ten": "Đang mở", "loai": "firefox", "mo": True}
    dv.tin_hieu_tra = {"da_dang_nhap"}
    _dang_nhap_quan_tri_lna(trang, w.url, tmp_path)
    _mo_trang_lna(trang, f"{w.url}/m/vnc-xa/index.html", tmp_path,
                cho="document.readyState === 'complete' && !!document.getElementById('ds-ws')")
    trang.cho(f"!!{_the(HO_SO_UI)}", han=10, mo_ta="thẻ workspace đã đóng")
    # Nút chỉ hiện trên thẻ ĐÃ ĐÓNG, không hiện trên thẻ đang mở
    assert trang.js(f"!!{_the(HO_SO_UI)}.querySelector('button.nhan')")
    assert trang.js(f"!{_the(HO_SO_UI_MO)}.querySelector('button.nhan')")
    # Tên chứa HTML hiện thành CHỮ, không chạy
    assert trang.js("window.__xss === undefined")

    # Bấm → form hỏi email
    trang.bam(f"{_the(HO_SO_UI)}.querySelector('button.nhan')", mo_ta="bấm Nhận làm tài khoản")
    trang.cho(f"!!{_the(HO_SO_UI)}.querySelector('.form-nhan')", mo_ta="form email")

    # Email sai khuôn → báo lỗi ngay phía client, không gọi server
    trang.js(f"{_the(HO_SO_UI)}.querySelector('.form-nhan input').value = 'khong-phai-email'")
    trang.bam(f"{_the(HO_SO_UI)}.querySelector('.form-nhan .nhan-luu')", mo_ta="Nhận với email sai")
    trang.cho("document.getElementById('thong-bao').textContent.includes('Email không hợp lệ')",
              mo_ta="báo lỗi email")

    # Email đúng → thành công, có bước kế
    trang.js(f"{_the(HO_SO_UI)}.querySelector('.form-nhan input').value = 'ui-tai-khoan@example.com'")
    trang.bam(f"{_the(HO_SO_UI)}.querySelector('.form-nhan .nhan-luu')", mo_ta="Nhận với email đúng")
    trang.cho(f"{_the(HO_SO_UI)}.querySelector('.ket-qua-nhan').textContent.includes('Đã nhận làm tài khoản')",
              mo_ta="kết quả nhận")
    chu = trang.js(f"{_the(HO_SO_UI)}.querySelector('.ket-qua-nhan').textContent")
    assert "ui-tai-khoan@example.com" in chu
    assert "Qua danh tính" in chu
    assert "phiên đăng nhập đã kiểm được" in chu
    assert "Đã nhận workspace làm tài khoản." in trang.chu("#thong-bao")
    assert trang.js("window.__xss === undefined")
