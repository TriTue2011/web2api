"""NV181 — module khai thực thể phải DÙNG ĐƯỢC qua lõi thật.

Bối cảnh (đo thật 20/10/2026): van-phong, youtube-phat, chitieu, hoa-don khai
thực thể nhưng lõi KHÔNG nạp được (thuc_the/trang_thai đồng bộ hoặc thiếu,
cung_cap thiếu «thiet_bi», mặc định dich_vu_url loopback bị chính lõi chặn
khi cài) → mọi hành động qua /api/thiet-bi trả 404 dù test gọi thẳng module
vẫn xanh. Bộ test này cài TỪNG module THẬT (sao nguyên thư mục modules/)
vào một lõi tạm (khuôn `moi_truong` của test_web2api_thiet_bi.py) và chứng
minh: GET /api/thiet-bi THẤY thực thể (kèm trạng thái), POST hanh-dong chạy
được hành động an toàn nhất, rủi ro đúng mức khai. youtube-phat gọi qua một
dịch vụ GIẢ chạy thật trên loopback (chỉ trong test).
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-test-nv181-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_DICH_VU = "khoa-gia-nv181-0123456789abcdef"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    """Lõi tạm sạch (cùng khuôn test_web2api_thiet_bi.py)."""
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


def cai_module_that(client, data_dir, module_id: str, cau_hinh: dict) -> dict:
    """Sao thư mục module THẬT vào lõi tạm, nạp nóng + cài; trả JSON cài."""
    dich = data_dir / "modules" / module_id
    shutil.copytree(GOC / "modules" / module_id, dich,
                    ignore=shutil.ignore_patterns("__pycache__"))
    r_nap = client.post("/api/modules/nap-lai", headers=QT)
    assert r_nap.status_code == 200, r_nap.text
    assert module_id in r_nap.json()["them"], r_nap.json()
    r_cai = client.post(f"/api/modules/{module_id}/cai", headers=QT,
                        json={"cau_hinh": cau_hinh})
    assert r_cai.status_code == 200, r_cai.text
    return r_cai.json()


def lay_thuc_the(client, module_id: str) -> dict[str, dict]:
    r = client.get(f"/api/thiet-bi?module={module_id}", headers=QT)
    assert r.status_code == 200, r.text
    return {e["id"]: e for e in r.json()["thuc_the"]}


def goi_hanh_dong(client, id_thuc_the: str, hanh_dong: str, tham_so=None):
    return client.post(f"/api/thiet-bi/{id_thuc_the}/hanh-dong", headers=QT,
                       json={"hanh_dong": hanh_dong, "tham_so": tham_so or {},
                             "xac_nhan": True})


# ── van-phong ─────────────────────────────────────────────────────────────

def test_van_phong_qua_loi_that(moi_truong):
    client, data_dir, _so = moi_truong
    cai_module_that(client, data_dir, "van-phong",
                    {"dich_vu_url": "http://127.0.0.1:8799",
                     "khoa_dich_vu": "khoa-gia-van-phong-0123456789"})
    tt = lay_thuc_the(client, "van-phong")
    assert set(tt) == {"van_phong.office_doc", "van_phong.office_ghi",
                       "van_phong.ocr_va_soan_thao"}, sorted(tt)
    # Rủi ro khai theo thực thể (doc tách khỏi ghi).
    assert tt["van_phong.office_doc"]["rui_ro_mac_dinh"] == "doc"
    assert tt["van_phong.office_ghi"]["rui_ro_mac_dinh"] == "ghi"
    assert tt["van_phong.ocr_va_soan_thao"]["rui_ro_mac_dinh"] == "ghi"
    # trang_thai nối được: chi tiết thực thể có trạng thái (không None).
    r_ct = client.get("/api/thiet-bi/van_phong.office_doc", headers=QT)
    assert r_ct.status_code == 200
    assert r_ct.json()["trang_thai"]["gia_tri"] in ("bat", "tat")
    # Hành động an toàn nhất chạy THẬT qua lõi: kiểm an toàn một tệp zip
    # (thuần cục bộ, không gọi dịch vụ phụ).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "xin chao")
    r = goi_hanh_dong(client, "van_phong.ocr_va_soan_thao", "kiem_tai_lieu",
                      {"tep_b64": base64.b64encode(buf.getvalue()).decode(),
                       "ten": "a.zip"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ── chitieu ───────────────────────────────────────────────────────────────

def test_chitieu_qua_loi_that(moi_truong):
    client, data_dir, _so = moi_truong
    cai_module_that(client, data_dir, "chitieu", {})
    tt = lay_thuc_the(client, "chitieu")
    assert set(tt) == {"chi_tieu.doc", "chi_tieu.ghi"}, sorted(tt)
    assert tt["chi_tieu.doc"]["rui_ro_mac_dinh"] == "doc"
    assert tt["chi_tieu.ghi"]["rui_ro_mac_dinh"] == "ghi"
    r_ct = client.get("/api/thiet-bi/chi_tieu.doc", headers=QT)
    assert r_ct.status_code == 200
    assert r_ct.json()["trang_thai"]["gia_tri"] == "san_sang"
    # Hành động đọc chạy thật (SQLite cục bộ): xem danh sách hũ.
    r = goi_hanh_dong(client, "chi_tieu.doc", "xem_hu", {})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "hu" in r.json()
    # Hành động ghi KHÔNG lọt qua thực thể đọc (lõi chặn theo tính năng).
    r_sai = goi_hanh_dong(client, "chi_tieu.doc", "ghi_chi",
                          {"hu": "an uong", "so_tien": 1000})
    assert r_sai.status_code == 400, r_sai.text


# ── hoa-don ───────────────────────────────────────────────────────────────

XML_HOA_DON = """<?xml version="1.0" encoding="utf-8"?>
<HDon><DLHDon>
<TTChung><KHMSHDon>1</KHMSHDon><KHHDon>AA/23E</KHHDon><SHDon>123</SHDon><NLap>2026-10-01</NLap></TTChung>
<NDHDon>
<NBan><Ten>Cong ty TNHH Vi Du</Ten><MST>0101234567</MST></NBan>
<NMua><Ten>Cong ty Mua Hang</Ten><MST>0307654321</MST></NMua>
<DSHHDVu>
<HHDVu><STT>1</STT><TChat>1</TChat><THHDVu>Giay in A4</THHDVu><MHHDVu>GIA4</MHHDVu><DVTinh>Ram</DVTinh><SLuong>10</SLuong><DGia>80000</DGia><STCKhau>0</STCKhau><ThTien>800000</ThTien><TSuat>8%</TSuat></HHDVu>
</DSHHDVu>
<TToan><TgTCThue>800000</TgTCThue><TgTThue>64000</TgTThue><TTCKTMai>0</TTCKTMai><TgTTTBSo>864000</TgTTTBSo></TToan>
</NDHDon></DLHDon></HDon>"""


def test_hoa_don_qua_loi_that(moi_truong):
    client, data_dir, _so = moi_truong
    cai_module_that(client, data_dir, "hoa-don", {})
    tt = lay_thuc_the(client, "hoa-don")
    assert set(tt) == {"hoa_don.cong_cu"}, sorted(tt)
    assert tt["hoa_don.cong_cu"]["rui_ro_mac_dinh"] == "doc"
    r_ct = client.get("/api/thiet-bi/hoa_don.cong_cu", headers=QT)
    assert r_ct.status_code == 200
    assert r_ct.json()["trang_thai"]["gia_tri"] == "san_sang"
    # Hành động đọc chạy thật (thuần tính toán): đọc XML hoá đơn.
    r = goi_hanh_dong(client, "hoa_don.cong_cu", "doc_hoa_don",
                      {"xml": XML_HOA_DON})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_youtube_dinh_tuyen_theo_thuc_the_truc_tiep():
    """Lớp phòng thủ trong module: sai cặp thực thể/hành động bị từ chối
    TRƯỚC khi gọi mạng (lõi cũng chặn theo tính năng, nhưng module không
    được phụ thuộc vào lõi để đúng hợp đồng của chính nó)."""
    import asyncio
    import importlib.util
    import inspect

    thu_muc = GOC / "modules" / "youtube-phat"
    spec = importlib.util.spec_from_file_location(
        "youtube_phat_nv181", thu_muc / "module.py")
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    lop = next(v for _, v in inspect.getmembers(md, inspect.isclass)
               if getattr(v, "__module__", "") == md.__name__
               and hasattr(v, "nap") and not issubclass(v, BaseException))
    manifest = json.loads((thu_muc / "manifest.json").read_text(encoding="utf-8"))
    the = lop(manifest)
    the.nap({"dich_vu_url": "http://127.0.0.1:9", "khoa_dich_vu": KHOA_DICH_VU})
    # Hành động đọc qua thực thể ĐIỀU KHIỂN → từ chối (không gọi mạng).
    kq = asyncio.run(the.thuc_hien("youtube_phat.dieu_khien", "tim",
                                   {"tu_khoa": "x"}))
    assert kq["ok"] is False and kq["loi"] == "hanh_dong_khong_ho_tro"
    # Hành động điều khiển qua thực thể XEM → từ chối.
    kq = asyncio.run(the.thuc_hien("youtube_phat.xem", "phat_len_loa",
                                   {"nguon": "youtube", "muc_tieu": "abc",
                                    "thiet_bi": ["media_player.x"]}))
    assert kq["ok"] is False and kq["loi"] == "hanh_dong_khong_ho_tro"
    # Thực thể lạ → khong_thay_thuc_the.
    kq = asyncio.run(the.thuc_hien("youtube_phat.la", "tim", {"tu_khoa": "x"}))
    assert kq["ok"] is False and kq["loi"] == "khong_thay_thuc_the"


# ── youtube-phat: dịch vụ GIẢ chạy thật trên loopback ─────────────────────

class _DichVuGia(BaseHTTPRequestHandler):
    """Dich-vu/youtube-phat giả: chỉ các route integration module gọi."""

    def log_message(self, *args):  # im lặng
        pass

    def _tra(self, obj, ma=200):
        than = json.dumps(obj).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(than)))
        self.end_headers()
        self.wfile.write(than)

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {KHOA_DICH_VU}":
            return self._tra({"error": "unauthorized"}, 401)
        if self.path.startswith("/api/integration/health"):
            return self._tra({"success": True, "status": "ok"})
        if self.path.startswith("/api/integration/search"):
            return self._tra({"success": True, "source": "youtube", "items": [
                {"id": "abc123", "title": "Bai test NV181", "channel": "Kenh",
                 "duration": 123, "source": "youtube",
                 "url": "https://www.youtube.com/watch?v=abc123"}]})
        if self.path.startswith("/api/integration/status"):
            return self._tra({"success": True, "state": "playing",
                              "session": {"session_id": "s1", "state": "playing"},
                              "item": {"title": "Bai test NV181"}})
        if self.path.startswith("/api/integration/playlists"):
            return self._tra({"success": True, "playlists": []})
        return self._tra({"error": "not_found"}, 404)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {KHOA_DICH_VU}":
            return self._tra({"error": "unauthorized"}, 401)
        dai = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(dai) if dai else None
        if self.path.startswith("/api/integration/session"):
            return self._tra({"success": True,
                              "session": {"session_id": "s1", "state": "playing"}})
        return self._tra({"error": "not_found"}, 404)


@pytest.fixture
def dich_vu_gia():
    may = ThreadingHTTPServer(("127.0.0.1", 0), _DichVuGia)
    luong = threading.Thread(target=may.serve_forever, daemon=True)
    luong.start()
    try:
        yield f"http://127.0.0.1:{may.server_address[1]}"
    finally:
        may.shutdown()


def test_youtube_phat_qua_loi_that(moi_truong, dich_vu_gia, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    client, data_dir, _so = moi_truong
    kq_cai = cai_module_that(client, data_dir, "youtube-phat",
                             {"dich_vu_url": dich_vu_gia,
                              "khoa_dich_vu": KHOA_DICH_VU})
    assert kq_cai.get("ok") is not False, kq_cai
    tt = lay_thuc_the(client, "youtube-phat")
    # Hai thực thể tách theo rủi ro; giao diện gọi đúng id từng loại.
    assert set(tt) == {"youtube_phat.xem", "youtube_phat.dieu_khien"}, sorted(tt)
    assert tt["youtube_phat.xem"]["rui_ro_mac_dinh"] == "doc"
    assert tt["youtube_phat.dieu_khien"]["rui_ro_mac_dinh"] == "ghi"
    # trang_thai đọc trạng thái THẬT từ dịch vụ giả.
    r_ct = client.get("/api/thiet-bi/youtube_phat.xem", headers=QT)
    assert r_ct.status_code == 200
    assert r_ct.json()["trang_thai"]["gia_tri"] == "playing"
    # Hành động THẬT qua lõi trên thực thể XEM: tìm kiếm.
    r = goi_hanh_dong(client, "youtube_phat.xem", "tim", {"tu_khoa": "test"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["ket_qua"][0]["ma_bai"] == "abc123"
    # Hành động điều khiển KHÔNG lọt qua thực thể xem (lõi 400 theo tính năng),
    # và hành động đọc KHÔNG lọt qua thực thể điều khiển.
    r_sai1 = goi_hanh_dong(client, "youtube_phat.xem", "phat_len_loa",
                           {"nguon": "youtube", "muc_tieu": "abc123",
                            "thiet_bi": ["media_player.x"]})
    assert r_sai1.status_code == 400, r_sai1.text
    r_sai2 = goi_hanh_dong(client, "youtube_phat.dieu_khien", "tim",
                           {"tu_khoa": "test"})
    assert r_sai2.status_code == 400, r_sai2.text


def test_youtube_phat_cai_thieu_url_bi_tu_choi(moi_truong):
    """Hồi quy NV181: mặc định cũ http://127.0.0.1:8713 bị chính lõi chặn khi
    cài → nay KHÔNG còn mặc định; cài mà không khai địa chỉ phải bị từ chối
    ngay với hướng dẫn, không âm thầm lưu địa chỉ loopback chết."""
    client, data_dir, _so = moi_truong
    dich = data_dir / "modules" / "youtube-phat"
    shutil.copytree(GOC / "modules" / "youtube-phat", dich,
                    ignore=shutil.ignore_patterns("__pycache__"))
    client.post("/api/modules/nap-lai", headers=QT)
    r = client.post("/api/modules/youtube-phat/cai", headers=QT,
                    json={"cau_hinh": {"khoa_dich_vu": KHOA_DICH_VU}})
    # Lõi bề mặt hoá lỗi cài thành HTTP 400 + ok:false + lý do rõ ràng.
    assert r.status_code == 400, r.text
    than = r.json()
    assert than.get("ok") is False, than
    assert "Địa chỉ dịch vụ" in (than.get("loi") or "")
