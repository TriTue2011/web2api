"""Test NV164-SUA — vá theo soát độc lập (soat-doc-lap-A.md, phần B. nhaptaikhoan).

Đỏ trước / xanh sau, theo phát hiện của spec viec-nv164-sua.txt:
  (a) nối đúng vào lõi: cung_cap có «thiet_bi», thuc_the/trang_thai/thuc_hien là
      async, thực thể HIỆN qua GET /api/thiet-bi của lõi thật, hành động gọi được
      qua POST /api/thiet-bi/{id}/hanh-dong của lõi thật.
  (b) tách thực thể «xem trước» (rui_ro_mac_dinh «doc») khỏi thực thể «áp dụng»
      (nguy_hiem) — lõi tính rủi ro theo thực thể.
  (c) chống DNS rebinding: ghim IP đã kiểm lúc KẾT NỐI (TransportGhimIp của lõi).
  (d) trần 8 KB của lõi: giới hạn nêu rõ + lược đồ module khớp (maxLength),
      không để người dùng gửi tệp lớn rồi mới 400.
  B3 (Thấp): `ten` có dạng email bị lộ → che như email_che.
  B4 (Thấp): trường từ nguồn ngoài được làm sạch + cắt; thân phản hồi máy chủ
      nguồn đọc có trần (chống máy chủ độc trả 60 MB).
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import importlib.util
import inspect
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app import kho, nhat_ky, so_module
from app.main import app
from app.so_module import SoModule
from gia import ghi_thu_muc

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "nhaptaikhoan"

KHOA_QT = "khoa-quan-tri-test-nv164-sua-0123456789"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
API_KEY_GIA = "sub2api-api-key-gia-nv164-0123456789abcdef"  # khớp _FakeHandler của test cũ
TOKEN_9ROUTER_GIA = "eyJhbGciOiJSUzI1NiJ9.MOHINH-GIA-SUA-NV164.codex-token"


def _module_py():
    p = THU_MUC / "module.py"
    spec = importlib.util.spec_from_file_location("nhaptaikhoan_sua", p)
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
    m.nap({})
    return m


@pytest.fixture()
def ctx_quan_tri():
    from app import ngu_canh
    tok = ngu_canh.dat({"loai": "quan_tri", "id": "qt-test", "pham_vi": ["*"]}, nguon="nguoi")
    try:
        yield
    finally:
        ngu_canh.khoi_phuc(tok)


def _thuc_hien(mod, ent_id, hanh_dong, tham_so):
    return asyncio.run(mod.thuc_hien(ent_id, hanh_dong, tham_so))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _backup_9router(ten_cong="Codex chính"):
    return {"providerConnections": [
        {"provider": "codex", "connectionName": ten_cong,
         "data": {"accessToken": TOKEN_9ROUTER_GIA, "email": "nguoi.a@example.com"}}]}


# ───────── (a)+(b): hợp đồng lõi ─────────

def test_cung_cap_co_thiet_bi():
    manifest = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))
    assert "thiet_bi" in manifest["cung_cap"]


def test_ham_thuc_the_trang_thai_thuc_hien_la_async(md):
    assert inspect.iscoroutinefunction(md.Module.thuc_the)
    assert inspect.iscoroutinefunction(md.Module.trang_thai)
    assert inspect.iscoroutinefunction(md.Module.thuc_hien)


def test_tach_thuc_the_theo_rui_ro(mod):
    ents = {e["id"]: e for e in asyncio.run(mod.thuc_the(None))}
    assert set(ents) == {"nhaptaikhoan.xem_truoc", "nhaptaikhoan.ap_dung"}
    xem = ents["nhaptaikhoan.xem_truoc"]
    assert xem["rui_ro_mac_dinh"] == "doc"
    assert {a["hanh_dong"] for a in xem["tinh_nang"]} == {"xem_truoc_tep", "xem_truoc_may_chu"}
    assert all(a["rui_ro"] == "doc" for a in xem["tinh_nang"])
    ap = ents["nhaptaikhoan.ap_dung"]
    assert ap["rui_ro_mac_dinh"] == "nguy_hiem"
    assert {a["hanh_dong"] for a in ap["tinh_nang"]} == {"ap_dung"}
    assert all(a["rui_ro"] == "nguy_hiem" for a in ap["tinh_nang"])


def test_trang_thai_bao_phu_thuc_the(mod):
    tt = asyncio.run(mod.trang_thai(["nhaptaikhoan.xem_truoc", "nhaptaikhoan.ap_dung"]))
    assert isinstance(tt, dict)
    assert "nhaptaikhoan.xem_truoc" in tt
    assert "nhaptaikhoan.ap_dung" in tt


def test_ap_dung_chi_qua_thuc_the_ap_dung(mod, ctx_quan_tri):
    # Gọi ap_dung qua thực thể xem_truoc (doc) phải bị từ chối — hành động nguy hiểm
    # không được chạy dưới danh nghĩa thực thể rủi ro thấp.
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "ap_dung",
                    {"danh_sach": [{"nguon": "x", "dinh_danh": "y"}]})
    assert kq["ok"] is False


@pytest.fixture()
def moi_truong_loi_that(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True, exist_ok=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


def _cai_nhaptaikhoan(client, data_dir):
    mod_path = data_dir / "modules" / "nhaptaikhoan"
    ghi_thu_muc(mod_path, {
        "manifest.json": (THU_MUC / "manifest.json").read_bytes(),
        "module.py": (THU_MUC / "module.py").read_bytes(),
    })
    r_nap = client.post("/api/modules/nap-lai", headers=QT)
    assert r_nap.status_code == 200
    assert "nhaptaikhoan" in r_nap.json()["them"]
    r_cai = client.post("/api/modules/nhaptaikhoan/cai", headers=QT, json={"cau_hinh": {}})
    assert r_cai.status_code == 200


def test_qua_loi_that_thay_thuc_the_va_trang_thai(moi_truong_loi_that):
    """(a): thực thể HIỆN qua GET /api/thiet-bi của lõi thật (trước sửa: 404 / vắng)."""
    client, data_dir, _so = moi_truong_loi_that
    _cai_nhaptaikhoan(client, data_dir)
    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    ds = {e["id"]: e for e in r.json()["thuc_the"] if e["id"].startswith("nhaptaikhoan.")}
    assert "nhaptaikhoan.xem_truoc" in ds
    assert "nhaptaikhoan.ap_dung" in ds
    # trang_thai được lõi gọi và gắn vào thực thể (không None):
    assert ds["nhaptaikhoan.xem_truoc"]["trang_thai"] is not None
    assert ds["nhaptaikhoan.ap_dung"]["trang_thai"] is not None


def test_qua_loi_that_goi_hanh_dong(moi_truong_loi_that):
    """(a): hành động chạy được qua POST /api/thiet-bi/{id}/hanh-dong của lõi thật."""
    client, data_dir, _so = moi_truong_loi_that
    _cai_nhaptaikhoan(client, data_dir)
    # Thực thể mới → lõi coi nguy_hiem tới khi quản trị xác nhận: ghi đè về «doc»
    # đúng rủi ro đã khai, rồi gọi thẳng không cần xac_nhan.
    r_r = client.put("/api/thiet-bi/nhaptaikhoan.xem_truoc/rui-ro", headers=QT,
                     json={"rui_ro": "doc"})
    assert r_r.status_code == 200
    noi_dung = _b64(json.dumps(_backup_9router()).encode("utf-8"))
    r = client.post("/api/thiet-bi/nhaptaikhoan.xem_truoc/hanh-dong", headers=QT,
                    json={"hanh_dong": "xem_truoc_tep",
                          "tham_so": {"dang": "ninerouter", "noi_dung_base64": noi_dung}})
    assert r.status_code == 200
    kq = r.json()
    assert kq["ok"] is True and kq["nguon"] == "ninerouter"
    assert TOKEN_9ROUTER_GIA not in json.dumps(kq, ensure_ascii=False)
    # Thực thể ap_dung: nguy hiểm → quản trị gọi kèm xac_nhan, vẫn từ chối rõ ràng
    # «chưa nối kho» (không phải 404 «không tìm thấy thực thể»).
    r2 = client.post("/api/thiet-bi/nhaptaikhoan.ap_dung/hanh-dong", headers=QT,
                     json={"hanh_dong": "ap_dung",
                           "tham_so": {"danh_sach": [{"nguon": "x", "dinh_danh": "y"}]},
                           "xac_nhan": True})
    assert r2.status_code == 200
    assert r2.json()["loi"] == "chua_noi_kho_tai_khoan"


# ───────── (d): trần 8 KB của lõi ─────────

def test_luoc_do_tep_khop_tran_loi(md):
    # Tổng tham_so tuần tự hoá ≤ 8192: giới hạn nêu rõ trong lược đồ, không để
    # người dùng gửi tệp lớn rồi mới nhận 400 mơ hồ từ lõi.
    ml = md._LUOC_DO_TEP["properties"]["noi_dung_base64"]["maxLength"]
    assert ml <= 8000
    mo_ta = md._LUOC_DO_TEP["properties"]["noi_dung_base64"].get("mo_ta", "")
    assert "8" in mo_ta and ("KB" in mo_ta or "8000" in mo_ta)


def test_tep_qua_tran_loi_bi_tu_choi(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_tep",
                    {"dang": "ninerouter", "noi_dung_base64": _b64(b"x" * 8001)})
    assert kq["ok"] is False and kq["loi"] == "tep_qua_lon"


def test_qua_loi_that_tep_lon_tra_400_ro_rang(moi_truong_loi_that):
    """(d): tệp vượt trần 8 KB của lõi → 400 rõ ràng (trước sửa: module hứa 8 MB nhưng
    lõi 400, người dùng không hiểu)."""
    client, data_dir, _so = moi_truong_loi_that
    _cai_nhaptaikhoan(client, data_dir)
    client.put("/api/thiet-bi/nhaptaikhoan.xem_truoc/rui-ro", headers=QT, json={"rui_ro": "doc"})
    # ~100 kết nối 9router ≈ 13 KB JSON thô / ~17K ký tự base64 (số đo của soát A)
    ket_noi = [{"provider": "codex", "connectionName": f"ket-noi-{i}",
                "data": {"accessToken": "eyJ" + "x" * 60, "email": "nguoi.a@example.com"}}
               for i in range(100)]
    lon = _b64(json.dumps({"providerConnections": ket_noi}).encode())
    assert len(lon) > 8000
    r = client.post("/api/thiet-bi/nhaptaikhoan.xem_truoc/hanh-dong", headers=QT,
                    json={"hanh_dong": "xem_truoc_tep",
                          "tham_so": {"dang": "ninerouter", "noi_dung_base64": lon}})
    assert r.status_code == 400


# ───────── (c): DNS rebinding ─────────

def _stub_getaddrinfo(danh_sach):
    """Trả IP theo thứ tự các lần gọi: lần 1 cho kiem_url_lan, lần 2+ cho lúc kết nối."""
    so_lan = {"n": 0}

    def _gia(host, port, family=0, type=0, proto=0, flags=0):
        ip = danh_sach[min(so_lan["n"], len(danh_sach) - 1)]
        so_lan["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return _gia


def _may_chu_gia(handler, monkeypatch):
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    # httpx thường tôn trọng biến proxy môi trường (trust_env) — dọn để kết nối
    # trong test đi THẲNG, nếu không test rebinding xanh giả kể cả khi mất ghim.
    for bien in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(bien, raising=False)
    srv = HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        t.join(timeout=5)


@pytest.fixture()
def may_chu_sub2api_gia(monkeypatch):
    from test_web2api_nv164_nhap_tai_khoan import _FakeHandler  # noqa
    yield from _may_chu_gia(_FakeHandler, monkeypatch)


def test_rebinding_bi_chan(mod, ctx_quan_tri, monkeypatch, may_chu_sub2api_gia):
    """(c): tên phân giải lần 1 ra 192.168.1.50 (kiểm qua), lần 2 ra 127.0.0.1
    (rebinding) → PHẢI bị chặn, không nối loopback (trước sửa: ok True, dữ liệu
    từ máy loopback lọt về)."""
    monkeypatch.setattr(socket, "getaddrinfo",
                        _stub_getaddrinfo(["192.168.1.50", "127.0.0.1"]))
    cong = may_chu_sub2api_gia
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": f"http://rebind.example:{cong}",
                     "khoa_api": API_KEY_GIA})
    assert kq["ok"] is False
    assert kq["loi"] == "khong_noi_duoc_may_chu"


def test_ghim_ip_giu_ket_noi_binh_thuong(mod, ctx_quan_tri, monkeypatch, may_chu_sub2api_gia):
    """Đường lành: phân giải luôn ra 127.0.0.1 (test cho phép loopback) → nối được,
    chứng tỏ ghim IP không làm gãy luồng thật."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(socket, "getaddrinfo", _stub_getaddrinfo(["127.0.0.1"]))
    cong = may_chu_sub2api_gia
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_may_chu",
                    {"loai": "sub2api", "may_chu_url": f"http://rebind.example:{cong}",
                     "khoa_api": API_KEY_GIA})
    assert kq["ok"] is True and kq["tong"] == 2


def test_transport_ghim_ip_chan_ip_bi_cam(md):
    """Đơn vị: TransportGhimIp kiểm lại IP ngay trước khi nối — IP bị cấm → lỗi
    vận chuyển (ConnectError), không bao giờ chạm máy chủ."""
    from app.chuyen_tiep import LoiDichBiChan, TransportGhimIp
    from app.tu_xa import _dia_chi_bi_chan
    import httpx

    da_noi = []

    def _xu_ly(req):
        da_noi.append(req.url.host)
        return httpx.Response(200, json={})

    goc_gia = httpx.MockTransport(_xu_ly)
    tp = TransportGhimIp(_dia_chi_bi_chan, goc=goc_gia)
    import os
    os.environ.pop("WEB2API_TEST_CHO_PHEP_LOOPBACK", None)

    async def _chay():
        # 127.0.0.1 bị cấm khi không ở chế độ test → chặn trước khi nối
        import socket as sk
        goc_get = sk.getaddrinfo
        sk.getaddrinfo = lambda *a, **k: [(sk.AF_INET, sk.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        try:
            async with httpx.AsyncClient(transport=tp) as c:
                req = c.build_request("GET", "http://ten-doi.example/")
                with pytest.raises(LoiDichBiChan):
                    await tp.handle_async_request(req)
        finally:
            sk.getaddrinfo = goc_get

    asyncio.run(_chay())
    assert da_noi == []


# ───────── B3: che `ten` khi có dạng email ─────────

def test_ten_dang_email_bi_che_9router(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_tep",
                    {"dang": "ninerouter",
                     "noi_dung_base64": _b64(json.dumps(_backup_9router("nguoi.dung@example.com")).encode())})
    assert kq["ok"] is True
    ten = kq["tai_khoan"][0]["ten"]
    assert ten == "n***@example.com"
    assert "nguoi.dung@example.com" not in json.dumps(kq, ensure_ascii=False)


def test_ten_thuong_khong_bi_che(mod, ctx_quan_tri):
    kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_tep",
                    {"dang": "ninerouter",
                     "noi_dung_base64": _b64(json.dumps(_backup_9router("Codex chính")).encode())})
    assert kq["tai_khoan"][0]["ten"] == "Codex chính"


# ───────── B4: làm sạch trường + trần phản hồi ─────────

def test_truong_ngoai_dai_qua_bi_cat(mod, ctx_quan_tri, monkeypatch, may_chu_sub2api_gia):
    """Trường `goi` 2000 ký tự từ máy chủ nguồn bị cắt ở trần (trước sửa: còn nguyên)."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    class _HandlerDai(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api/v1/admin/accounts":
                tk = {"id": 99, "name": "TK dai", "status": "active",
                      "credentials": {"access_token": "token-x", "plan_type": "P" * 2000,
                                      "email": "nguoi.a@example.com"}}
                than = json.dumps({"code": 200, "data": {"items": [tk], "total": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(than)))
                self.end_headers()
                self.wfile.write(than)
            else:
                self.send_response(404)
                self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _HandlerDai)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        kq = _thuc_hien(mod, "nhaptaikhoan.xem_truoc", "xem_truoc_may_chu",
                        {"loai": "sub2api", "may_chu_url": f"http://127.0.0.1:{srv.server_address[1]}",
                         "khoa_api": API_KEY_GIA})
    finally:
        srv.shutdown()
        t.join(timeout=5)
    assert kq["ok"] is True
    assert len(kq["tai_khoan"][0]["goi"]) <= 80


def test_phan_hoi_qua_lon_bi_tu_choi(md, monkeypatch):
    """Máy chủ nguồn độc trả thân vượt trần → từ chối (trước sửa: nhận đủ 60 MB vào RAM)."""
    monkeypatch.setattr(md, "_TRAN_PHAN_HOI_MAY_CHU", 1024)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    class _HandlerLon(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            than = b'{"code": 200, "data": {"items": ' + b'"x",' * 3000 + b'[], "total": 0}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(than)))
            self.end_headers()
            self.wfile.write(than)

    srv = HTTPServer(("127.0.0.1", 0), _HandlerLon)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        kq = asyncio.run(md._xem_truoc_sub2api(f"http://127.0.0.1:{srv.server_address[1]}",
                                              {"khoa_api": API_KEY_GIA}))
    finally:
        srv.shutdown()
        t.join(timeout=5)
    assert kq["ok"] is False and kq["loi"] == "phan_hoi_qua_lon"
