"""Test NV278 — module adguard (AdGuard Home trong LAN).

Máy chủ AdGuard GIẢ cục bộ (http.server, 127.0.0.1): không cần AdGuard thật,
không ra Internet (conftest chặn mọi đường không phải 127.0.0.1).
Bao phủ nghiệm thu NV278: đọc thống kê, bật/tắt bảo vệ có thời hạn, mật khẩu
sai, mất kết nối, địa chỉ ngoài LAN bị từ chối, và leo thang quyền ở tầng lõi
(không khoá -> 401, sai quyền -> 403, bao_ve nguy hiểm -> 202 hàng chờ /
409 thiếu xác nhận / 200 khi quản trị xác nhận).
"""

from __future__ import annotations

import base64
import json
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from modules.adguard import adguard as ag
from modules.adguard.module import Module

GOC = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((GOC / "modules" / "adguard" / "manifest.json").read_text(encoding="utf-8"))
TAI_KHOAN_GIA = "adguard-gia"
MAT_KHAU_GIA = "mat-khau-gia-khong-that"
KHOA_QT = "khoa-quan-tri-test-nv278-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

TRANG_THAI_GIA = {"protection_enabled": True, "dns_addresses": [], "dns_port": 53}
THONG_KE_GIA = {
    "num_dns_queries": 10000,
    "num_blocked_filtering": 5100,
    "avg_processing_time": 0.031,
    "top_blocked_domains": [
        {"ads.example.com": 400},
        {"tracker.example.net": 250},
        {"x" * 100: 10},
    ],
    "top_clients": [{"192.168.1.10": 900}, {"192.168.1.20": 300}],
}
LOC_GIA = {"enabled": True, "filters": [
    {"id": 1, "enabled": True}, {"id": 2, "enabled": False}, {"id": 3, "enabled": True},
]}
MAY_KHACH_GIA = {"clients": {
    "auto": [],
    "persistent": [{"ip": "192.168.1.10", "name": "dien-thoai-nha"}],
}}


def _lam_may_chu_gia(trang_thai):
    class XuLy(BaseHTTPRequestHandler):
        def log_message(self, *args):  # giữ test yên lặng
            pass

        def _xac_thuc(self):
            muon = "Basic " + base64.b64encode(
                f"{TAI_KHOAN_GIA}:{MAT_KHAU_GIA}".encode()).decode()
            return self.headers.get("Authorization") == muon

        def _gui_json(self, du_lieu, ma=200):
            than = json.dumps(du_lieu).encode()
            self.send_response(ma)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(than)))
            self.end_headers()
            self.wfile.write(than)

        def do_GET(self):
            if not self._xac_thuc():
                self.send_response(401)
                self.end_headers()
                return
            bang = {
                "/control/status": TRANG_THAI_GIA,
                "/control/stats": THONG_KE_GIA,
                "/control/filtering/status": LOC_GIA,
                "/control/clients": MAY_KHACH_GIA,
            }
            if self.path in bang:
                self._gui_json(bang[self.path])
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if not self._xac_thuc():
                self.send_response(401)
                self.end_headers()
                return
            dai = int(self.headers.get("Content-Length") or 0)
            than = self.rfile.read(dai) if dai else b""
            if self.path == "/control/protection":
                try:
                    du_lieu = json.loads(than.decode())
                except ValueError:
                    self.send_response(400)
                    self.end_headers()
                    return
                trang_thai["nhan"].append(du_lieu)
                trang_thai["bao_ve"] = bool(du_lieu.get("enabled"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), XuLy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def may_chu(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    trang_thai = {"nhan": [], "bao_ve": True}
    srv = _lam_may_chu_gia(trang_thai)
    yield srv, trang_thai
    srv.shutdown()
    srv.server_close()


def _module_da_nap(may_chu):
    srv, _ = may_chu
    mod = Module(MANIFEST)
    mod.nap({"dia_chi": f"127.0.0.1:{srv.server_address[1]}",
             "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA})
    return mod


def test_doc_thong_ke_day_du(may_chu):
    mod = _module_da_nap(may_chu)
    ds = asyncio.run(mod.thuc_the())
    ids = [e["id"] for e in ds]
    assert "adguard.bao_ve" in ids
    bao_ve = next(e for e in ds if e["id"] == "adguard.bao_ve")
    assert bao_ve["rui_ro_mac_dinh"] == "nguy_hiem"
    assert [t["hanh_dong"] for t in bao_ve["tinh_nang"]] == ["bao_ve"]
    # Top miền: giới hạn + cắt chuỗi; top máy khách: tên thay IP khi biết tên.
    assert bao_ve["thuoc_tinh"]["top_mien_bi_chan"][0] == {"mien": "ads.example.com", "so_lan": 400}
    assert len(bao_ve["thuoc_tinh"]["top_mien_bi_chan"][2]["mien"]) == 64
    assert bao_ve["thuoc_tinh"]["top_may_khach"][0] == {"may_khach": "dien-thoai-nha", "so_lan": 900}
    assert bao_ve["thuoc_tinh"]["top_may_khach"][1] == {"may_khach": "192.168.1.20", "so_lan": 300}
    tt = asyncio.run(mod.trang_thai(ids))
    assert tt["adguard.tong_truy_van"]["gia_tri"] == 10000
    assert tt["adguard.so_bi_chan"]["gia_tri"] == 5100
    assert tt["adguard.ti_le_chan"]["gia_tri"] == 51.0
    assert tt["adguard.thoi_gian_xu_ly_trung_binh"]["gia_tri"] == 0.031
    assert tt["adguard.so_bo_loc_dang_bat"]["gia_tri"] == 2
    assert tt["adguard.bao_ve"]["gia_tri"] == "bat"
    # Không trả nguyên nhật ký truy vấn: không thực thể nào mang trường querylog.
    assert "querylog" not in json.dumps(ds, ensure_ascii=False)


def test_bao_ve_bat_tat_co_thoi_han(may_chu):
    srv, trang_thai = may_chu
    mod = _module_da_nap(may_chu)
    ra = asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {"bat": False}))
    assert ra["ok"] is True
    assert trang_thai["nhan"][-1] == {"enabled": False, "duration": 300000}  # mặc định 5 phút
    assert trang_thai["bao_ve"] is False
    ra = asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {"bat": False, "thoi_gian_giay": 60}))
    assert trang_thai["nhan"][-1] == {"enabled": False, "duration": 60000}
    ra = asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {"bat": False, "thoi_gian_giay": 0}))
    assert trang_thai["nhan"][-1] == {"enabled": False, "duration": 0}  # vô thời hạn
    ra = asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {"bat": True}))
    assert trang_thai["nhan"][-1] == {"enabled": True}
    assert trang_thai["bao_ve"] is True


def test_bao_ve_tham_so_sai_va_hanh_dong_la(may_chu):
    mod = _module_da_nap(may_chu)
    assert (asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {})))["ok"] is False
    assert (asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve", {"bat": "co"})))["ok"] is False
    assert (asyncio.run(mod.thuc_hien("adguard.bao_ve", "bao_ve",
                                {"bat": False, "thoi_gian_giay": 99999})))["ok"] is False
    assert (asyncio.run(mod.thuc_hien("adguard.bao_ve", "sua_bo_loc", {"bat": True})))["ok"] is False
    assert (asyncio.run(mod.thuc_hien("adguard.tong_truy_van", "bao_ve", {"bat": True})))["ok"] is False


def test_mat_khau_sai_va_mat_ket_noi(may_chu, caplog):
    srv, _ = may_chu
    mod = Module(MANIFEST)
    mod.nap({"dia_chi": f"127.0.0.1:{srv.server_address[1]}",
             "tai_khoan": TAI_KHOAN_GIA, "mat_khau": "sai-hoan-toan"})
    with caplog.at_level("WARNING"):
        ds = asyncio.run(mod.thuc_the())
    tt = asyncio.run(mod.trang_thai(["adguard.bao_ve"]))
    assert tt["adguard.bao_ve"]["hop_le"] is False
    assert tt["adguard.bao_ve"]["loi"] == "sai_dang_nhap"
    assert MAT_KHAU_GIA not in caplog.text and "sai-hoan-toan" not in caplog.text
    assert "Authorization" not in caplog.text

    mod2 = Module(MANIFEST)
    mod2.nap({"dia_chi": "127.0.0.1:9", "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA})
    try:
        ag.MayChu("127.0.0.1", 9, TAI_KHOAN_GIA, MAT_KHAU_GIA).kiem_tra_dang_nhap()
        assert False, "cổng 9 phải từ chối kết nối"
    except ag.AdGuardError as e:
        assert e.ma_loi == "mang_loi"


def test_dia_chi_ngoai_lan_bi_tu_choi(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod = Module(MANIFEST)
    # Chỉ dùng IP literal: tên miền không xác định trong sandbox (DNS trả CGNAT
    # 198.18.x mà ipaddress coi là private), còn IP công cộng thật luôn bị từ chối.
    for dia_chi in ["8.8.8.8:3000", "169.254.169.254:3000"]:
        with pytest.raises(ValueError):
            mod.kiem_dia_chi(dia_chi)
    # Loopback khi TẮT cờ test thì bị từ chối.
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    with pytest.raises(ValueError):
        mod.kiem_dia_chi("127.0.0.1:3000")


# ── Leo thang quyền ở tầng lõi ──

@pytest.fixture
def moi_truong_loi(tmp_path, monkeypatch, may_chu):
    srv, trang_thai = may_chu
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    # Thả module adguard thật vào thư mục dữ liệu rồi nạp nóng qua API lõi.
    nguon = GOC / "modules" / "adguard"
    dich = data_dir / "modules" / "adguard"
    dich.mkdir(parents=True, exist_ok=True)
    for ten in ("manifest.json", "module.py", "adguard.py", "README.md"):
        shutil.copyfile(nguon / ten, dich / ten)
    with TestClient(app) as client:
        client.app.state.so = SoModule()
        r_nap = client.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        r_cai = client.post("/api/modules/adguard/cai", headers=QT, json={"cau_hinh": {
            "dia_chi": f"127.0.0.1:{srv.server_address[1]}",
            "tai_khoan": TAI_KHOAN_GIA, "mat_khau": MAT_KHAU_GIA}})
        assert r_cai.status_code == 200, r_cai.text
        yield client, trang_thai


def test_leo_thang_quyen_bao_ve_o_loi(moi_truong_loi):
    client, trang_thai = moi_truong_loi
    # Không khoá -> 401.
    assert client.post("/api/thiet-bi/adguard.bao_ve/hanh-dong",
                       json={"hanh_dong": "bao_ve", "tham_so": {"bat": False}}).status_code == 401
    # Khoá khách chỉ đọc -> 403 insufficient_scope.
    r_doc = client.post("/api/khoa-khach", headers=QT,
                        json={"nhan": "App Chi Doc", "pham_vi": ["thiet_bi:doc"]})
    assert r_doc.status_code == 200
    hd_doc = {"Authorization": f"Bearer {r_doc.json()['khoa']['khoa']}"}
    r_cam = client.post("/api/thiet-bi/adguard.bao_ve/hanh-dong", headers=hd_doc,
                        json={"hanh_dong": "bao_ve", "tham_so": {"bat": False}})
    assert r_cam.status_code == 403
    assert r_cam.json()["error"]["code"] == "insufficient_scope"
    # Khoá khách có quyền ghi gọi bao_ve nguy hiểm -> 202 hàng chờ, chưa thực thi.
    r_ghi = client.post("/api/khoa-khach", headers=QT,
                        json={"nhan": "App Nha", "pham_vi": ["thiet_bi:ghi", "thiet_bi:doc"]})
    hd_ghi = {"Authorization": f"Bearer {r_ghi.json()['khoa']['khoa']}"}
    r_cho = client.post("/api/thiet-bi/adguard.bao_ve/hanh-dong", headers=hd_ghi,
                        json={"hanh_dong": "bao_ve", "tham_so": {"bat": False, "thoi_gian_giay": 60}})
    assert r_cho.status_code == 202
    assert r_cho.json()["cho_phe_duyet"] is True
    assert trang_thai["nhan"] == []  # chưa thực thi khi chưa duyệt
    pd_id = r_cho.json()["phe_duyet_id"]
    # Quản trị duyệt -> thực thi, máy chủ giả nhận đúng thân POST.
    r_duyet = client.post(f"/api/phe-duyet/{pd_id}/duyet", headers=QT)
    assert r_duyet.status_code == 200
    assert trang_thai["nhan"][-1] == {"enabled": False, "duration": 60000}
    # Quản trị gọi trực tiếp thiếu xac_nhan -> 409 can_xac_nhan.
    r_qn = client.post("/api/thiet-bi/adguard.bao_ve/hanh-dong", headers=QT,
                       json={"hanh_dong": "bao_ve", "tham_so": {"bat": True}})
    assert r_qn.status_code == 409
    assert r_qn.json()["error"]["code"] == "can_xac_nhan"
    # Quản trị kèm xac_nhan -> 200 thực thi ngay.
    r_ok = client.post("/api/thiet-bi/adguard.bao_ve/hanh-dong", headers=QT,
                       json={"hanh_dong": "bao_ve", "tham_so": {"bat": True}, "xac_nhan": True})
    assert r_ok.status_code == 200
    assert trang_thai["nhan"][-1] == {"enabled": True}
