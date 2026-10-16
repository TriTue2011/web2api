"""Bộ kiểm thử bảo mật 4 lỗ hổng (chuẩn bảo mật rút từ so sánh với c2a):

1. Phiên noVNC sống sau khi người dùng bị tắt/đổi mật khẩu/xoá tài khoản:
   - Vé và phiên noVNC ghi nguoi_dung_id của người cấp vé.
   - Thu hồi người dùng thu hồi luôn phiên noVNC, đóng WebSocket đang mở.
   - Người khác / phiên khác không bị ảnh hưởng.
2. Mức rủi ro thiếu/lạ của thiết bị rơi về «nguy_hiem» (nguyên tắc đóng khi lỗi):
   - Thiếu trường rui_ro_mac_dinh hoặc mã lạ -> rui_ro rơi về «nguy_hiem».
   - Khoá khách có quyền thiet_bi:ghi không điều khiển thẳng được (vào hàng chờ phê duyệt 202).
3. Tải video theo URI do upstream trả về không kiểm đích nội bộ (SSRF):
   - URI trỏ IP loopback (khi chưa cho phép) hoặc IP riêng (10.x, 172.16.x, 192.168.x) -> 502 ssrf_blocked.
   - Chuyển hướng 302 sang IP riêng -> 502 ssrf_blocked ở chặng chuyển hướng.
   - Chuyển hướng sang host khác -> header xác thực không đi theo.
4. Cloudflare Worker proxy Gemini là relay mở & hỗ trợ proxy_token:
   - Trường cấu hình proxy_token kiểu bi_mat được gửi qua header X-Proxy-Token khi đã đặt.
   - Không gửi X-Proxy-Token khi để trống.
   - proxy_token được che trong GET /api/modules/{id}/cau-hinh, không vào URL query hay log.
   - Worker từ chối 403 khi thiếu PROXY_TOKEN hoặc sai token, bỏ hoàn toàn nhánh query.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import socket
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import cong_cu, kho, nguoi_dung, nhat_ky, novnc, phien_web, thiet_bi
from app.chuyen_tiep import ChuyenTiepOpenAI, LoiDichBiChan, TransportGhimIp
from app.main import app
from app.so_module import SoModule
from gia import MayChuThat, ghi_thu_muc

GOC = Path(__file__).resolve().parents[1]


def lay_gemini_mod():
    tep = GOC / "modules" / "gemini-api" / "module.py"
    m = importlib.util.module_from_spec(importlib.util.spec_from_file_location("gemini_api_test_rieng", tep))
    exec(compile(tep.read_bytes(), str(tep), "exec"), m.__dict__)
    return m
KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(novnc, "_bay_gio", lambda: 100000.0)

    # Dọn dẹp trạng thái bộ nhớ
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()
    novnc._WS.clear()
    nguoi_dung._THEO_DOI_SAI.clear()

    with TestClient(app) as client:
        yield client, data_dir


# ==============================================================================
# MỤC 1: Phiên noVNC và thu hồi danh tính
# ==============================================================================

def test_muc_1_ve_va_phien_ghi_nguoi_dung_id():
    """Vé và phiên noVNC phải ghi lại nguoi_dung_id của người cấp."""
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()

    ve = novnc.cap_ve("mod-test", "0123456789abcdef", nguoi_dung_id="user-abc-123")
    bam_v = novnc._bam(ve)
    assert bam_v in novnc._VE
    assert novnc._VE[bam_v]["nguoi_dung_id"] == "user-abc-123"

    # Giả lập hồ sơ đã mở để đổi vé
    novnc._HO_SO[("mod-test", "0123456789abcdef")] = {
        "scheme": "http", "host": "127.0.0.1", "port": 5900, "duong": "",
        "ip": "127.0.0.1", "host_hdr": "127.0.0.1:5900", "auth": None, "luc_mo": "2026-10-16T00:00:00Z"
    }

    ma, phien, tt = novnc.doi_ve_lay_phien(ve)
    assert ma == 200
    assert phien is not None
    bam_p = novnc._bam(phien)
    assert bam_p in novnc._PHIEN
    assert novnc._PHIEN[bam_p]["nguoi_dung_id"] == "user-abc-123"


def test_muc_1_thu_hoi_phien_theo_nguoi_dung_dong_websocket():
    """Thu hồi phiên theo người dùng phải đóng websocket và vô hiệu hoá phiên HTTP."""
    novnc._VE.clear()
    novnc._PHIEN.clear()
    novnc._HO_SO.clear()
    novnc._WS.clear()

    novnc._HO_SO[("mod-test", "0123456789abcdef")] = {
        "scheme": "http", "host": "127.0.0.1", "port": 5900, "duong": "",
        "ip": "127.0.0.1", "host_hdr": "127.0.0.1:5900", "auth": None, "luc_mo": "2026-10-16T00:00:00Z"
    }

    # Cấp và đổi vé cho người dùng A
    ve_a = novnc.cap_ve("mod-test", "0123456789abcdef", nguoi_dung_id="user-A")
    _, phien_a, _ = novnc.doi_ve_lay_phien(ve_a)
    bam_pa = novnc._bam(phien_a)

    # Cấp và đổi vé cho người dùng B
    ve_b = novnc.cap_ve("mod-test", "0123456789abcdef", nguoi_dung_id="user-B")
    _, phien_b, _ = novnc.doi_ve_lay_phien(ve_b)
    bam_pb = novnc._bam(phien_b)

    # Đăng ký cờ WebSocket đang mở
    ev_a = asyncio.Event()
    ev_b = asyncio.Event()
    novnc._WS[bam_pa] = {ev_a}
    novnc._WS[bam_pb] = {ev_b}

    # Vé chưa đổi của người A
    ve_chua_doi_a = novnc.cap_ve("mod-test", "0123456789abcdef", nguoi_dung_id="user-A")
    bam_v_chua_doi = novnc._bam(ve_chua_doi_a)

    assert bam_pa in novnc._PHIEN
    assert bam_pb in novnc._PHIEN
    assert bam_v_chua_doi in novnc._VE

    # Thu hồi người dùng A
    da_thu_hoi = novnc.thu_hoi_phien_theo_nguoi_dung("user-A")
    assert da_thu_hoi == 1

    # Phiên A và vé của A bị xoá, cờ websocket A được bật
    assert bam_pa not in novnc._PHIEN
    assert bam_v_chua_doi not in novnc._VE
    assert ev_a.is_set() is True

    # Phiên B và cờ websocket B hoàn toàn không bị ảnh hưởng
    assert bam_pb in novnc._PHIEN
    assert ev_b.is_set() is False


def test_muc_1_tat_nguoi_dung_qua_api_thu_hoi_novnc(moi_truong):
    """Khi tài khoản người dùng bị tắt hoặc xoá qua API, phiên noVNC bị thu hồi ngay lập tức."""
    client, _ = moi_truong

    # Tạo tài khoản người dùng user
    r_tao = client.post("/api/nguoi-dung", headers=QT, json={
        "ten_dang_nhap": "user-novnc-test", "mat_khau": "MatKhauTot123456", "vai": "user"
    })
    assert r_tao.status_code == 200, r_tao.text
    nd_id = r_tao.json()["nguoi_dung"]["id"]

    # Đăng nhập lấy cookie phiên
    r_dn = client.post("/api/dang-nhap", json={
        "ten_dang_nhap": "user-novnc-test", "mat_khau": "MatKhauTot123456"
    })
    assert r_dn.status_code == 200

    # Cấp vé trực tiếp qua helper cap_ve gắn với nd_id
    ve = novnc.cap_ve("gia-mod", "0123456789abcdef", nguoi_dung_id=nd_id)
    novnc._HO_SO[("gia-mod", "0123456789abcdef")] = {
        "scheme": "http", "host": "127.0.0.1", "port": 5900, "duong": "",
        "ip": "127.0.0.1", "host_hdr": "127.0.0.1:5900", "auth": None, "luc_mo": "2026-10-16T00:00:00Z"
    }
    ma, phien, _ = novnc.doi_ve_lay_phien(ve)
    assert ma == 200
    bam_p = novnc._bam(phien)
    assert bam_p in novnc._PHIEN

    # Quản trị viên tắt người dùng
    r_tat = client.patch(f"/api/nguoi-dung/{nd_id}", headers=QT, json={"bat": False})
    assert r_tat.status_code == 200
    assert r_tat.json()["nguoi_dung"]["bat"] is False

    # Phiên noVNC phải bị thu hồi ngay lập tức
    assert bam_p not in novnc._PHIEN
    r_proxy = client.get(f"/novnc/s/{phien}/vnc.html")
    assert r_proxy.status_code == 401


def test_muc_1_dat_lai_mat_khau_thu_hoi_novnc(moi_truong):
    """Đặt lại mật khẩu người dùng phải thu hồi phiên noVNC."""
    client, _ = moi_truong

    r_tao = client.post("/api/nguoi-dung", headers=QT, json={
        "ten_dang_nhap": "admin-mk-test", "mat_khau": "MatKhauTot123456", "vai": "admin"
    })
    assert r_tao.status_code == 200
    nd_id = r_tao.json()["nguoi_dung"]["id"]

    ve = novnc.cap_ve("gia-mod", "0123456789abcdef", nguoi_dung_id=nd_id)
    novnc._HO_SO[("gia-mod", "0123456789abcdef")] = {
        "scheme": "http", "host": "127.0.0.1", "port": 5900, "duong": "",
        "ip": "127.0.0.1", "host_hdr": "127.0.0.1:5900", "auth": None, "luc_mo": "2026-10-16T00:00:00Z"
    }
    ma, phien, _ = novnc.doi_ve_lay_phien(ve)
    assert ma == 200
    bam_p = novnc._bam(phien)
    assert bam_p in novnc._PHIEN

    # Đặt lại mật khẩu
    r_reset = client.post(f"/api/nguoi-dung/{nd_id}/dat-lai-mat-khau", headers=QT)
    assert r_reset.status_code == 200

    # Phiên noVNC đã bị thu hồi
    assert bam_p not in novnc._PHIEN


# ==============================================================================
# MỤC 2: Mức rủi ro thiết bị đóng khi lỗi (rơi về nguy_hiem)
# ==============================================================================

def mau_module_thiet_bi_dong_khi_loi():
    manifest = {
        "id": "tb-an-toan",
        "ten": "Thiết bị an toàn",
        "mo_ta": "Mô tả thiết bị an toàn",
        "phien_ban": "0.1.0",
        "giao_thuc": "0.1",
        "loai": "thiet_bi",
        "loai_ten": "Thiết bị",
        "tien_to": "tbat",
        "cung_cap": ["thiet_bi"],
        "truong_cau_hinh": [],
        "tai_nguyen": {
            "ram_them_mb": None,
            "dia_mb": None,
            "gpu": False,
            "mang_ra": [],
            "ghi_chu": None,
        },
    }
    code = """
class Module:
    def __init__(self, manifest):
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        pass

    async def cai(self, cau_hinh):
        return {"ok": True, "loi": None, "cau_hinh_luu": {}}

    async def thuc_the(self, id_thuc_the=None):
        return [
            {
                "id": f"{self.tien_to}.cua_ga_ra",
                "ten": "Cửa ga-ra chính",
                "loai": "cua",
                "tinh_nang": ["mo", "dong"],
                # Quên khai rui_ro_mac_dinh -> phải rơi về nguy_hiem
            },
            {
                "id": f"{self.tien_to}.khoa_dien_tu",
                "ten": "Khoá điện tử",
                "loai": "khoa",
                "tinh_nang": ["mo_khoa"],
                "rui_ro_mac_dinh": "ma_la_chua_biet_xyz",  # Mã lạ -> phải rơi về nguy_hiem
            },
            {
                "id": f"{self.tien_to}.den_chieu_sang",
                "ten": "Đèn ban công",
                "loai": "den",
                "tinh_nang": ["bat", "tat"],
                "rui_ro_mac_dinh": "ghi",  # Khai tường minh ghi
            },
            {
                "id": f"{self.tien_to}.nhiet_ke",
                "ten": "Nhiệt kế phòng",
                "loai": "cam_bien",
                "tinh_nang": ["doc"],
                "rui_ro_mac_dinh": "doc",  # Khai tường minh doc
            }
        ]

    async def trang_thai(self, ids):
        return {i: {"gia_tri": "ok", "hop_le": True} for i in ids}

    async def thuc_hien(self, id_thuc_the, hanh_dong, tham_so):
        return {"thanh_cong": True, "id": id_thuc_the, "hanh_dong": hanh_dong}

    async def suc_khoe(self):
        return {"khoa": []}

    async def dung(self):
        pass
"""
    return {"manifest.json": json.dumps(manifest).encode("utf-8"), "module.py": code.encode("utf-8")}


def test_muc_2_rui_ro_thieu_va_la_roi_ve_nguy_hiem(moi_truong):
    """Thực thể không khai hoặc khai rủi ro lạ phải rơi về 'nguy_hiem'."""
    client, data_dir = moi_truong
    mod_path = data_dir / "modules" / "tb-an-toan"
    ghi_thu_muc(mod_path, mau_module_thiet_bi_dong_khi_loi())

    client.post("/api/modules/nap-lai", headers=QT)
    r_cai = client.post("/api/modules/tb-an-toan/cai", headers=QT, json={"cau_hinh": {}})
    assert r_cai.status_code == 200

    r = client.get("/api/thiet-bi", headers=QT)
    assert r.status_code == 200
    thuc_the = {x["id"]: x for x in r.json()["thuc_the"]}

    # Cửa ga-ra quên khai rui_ro_mac_dinh -> nguy_hiem
    assert thuc_the["tbat.cua_ga_ra"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert thuc_the["tbat.cua_ga_ra"]["rui_ro"] == "nguy_hiem"

    # Khoá điện tử khai mã lạ -> nguy_hiem
    assert thuc_the["tbat.khoa_dien_tu"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert thuc_the["tbat.khoa_dien_tu"]["rui_ro"] == "nguy_hiem"

    # Đèn và nhiệt kế giữ nguyên tường minh
    assert thuc_the["tbat.den_chieu_sang"]["rui_ro"] == "ghi"
    assert thuc_the["tbat.nhiet_ke"]["rui_ro"] == "doc"

    # GET từng thực thể cũng phải đóng khi lỗi
    r_single = client.get("/api/thiet-bi/tbat.cua_ga_ra", headers=QT)
    assert r_single.status_code == 200
    assert r_single.json()["thuc_the"]["rui_ro"] == "nguy_hiem"


def test_muc_2_khach_thiet_bi_ghi_khong_the_dieu_khien_thiet_bi_thieu_rui_ro(moi_truong):
    """Khoá khách có thiet_bi:ghi gọi thiết bị thiếu rui_ro_mac_dinh phải bị đưa vào hàng chờ phê duyệt (202)."""
    client, data_dir = moi_truong
    mod_path = data_dir / "modules" / "tb-an-toan"
    ghi_thu_muc(mod_path, mau_module_thiet_bi_dong_khi_loi())

    client.post("/api/modules/nap-lai", headers=QT)
    client.post("/api/modules/tb-an-toan/cai", headers=QT, json={"cau_hinh": {}})

    # Tạo khoá khách có quyền ghi
    r_k = client.post("/api/khoa-khach", headers=QT, json={
        "nhan": "Khách Test Ghi", "pham_vi": ["thiet_bi:ghi"]
    })
    assert r_k.status_code == 200
    khoa_khach = r_k.json()["khoa"]["khoa"]
    hd_khach = {"Authorization": f"Bearer {khoa_khach}"}

    # Gọi cửa ga-ra (thiếu rui_ro_mac_dinh -> nguy_hiem): khách gọi KHÔNG được chạy ngay mà phải vào hàng chờ 202
    r_gara = client.post("/api/thiet-bi/tbat.cua_ga_ra/hanh-dong", headers=hd_khach, json={
        "hanh_dong": "mo", "tham_so": {}
    })
    assert r_gara.status_code == 202, f"Phải vào hàng chờ phê duyệt 202, nhận {r_gara.status_code}: {r_gara.text}"
    assert "phe_duyet_id" in r_gara.json()

    # Gọi đèn (khai tường minh ghi): chạy thẳng 200
    r_den = client.post("/api/thiet-bi/tbat.den_chieu_sang/hanh-dong", headers=hd_khach, json={
        "hanh_dong": "bat", "tham_so": {}
    })
    assert r_den.status_code == 200, f"Đèn ghi phải chạy thẳng 200: {r_den.text}"


def test_muc_2_cong_cu_chuan_rui_ro_unit():
    """Hàm _chuan_rui_ro trả 'nguy_hiem' cho mọi giá trị thiếu hoặc không nằm trong danh mục."""
    assert cong_cu._chuan_rui_ro(None) == "nguy_hiem"
    assert cong_cu._chuan_rui_ro("") == "nguy_hiem"
    assert cong_cu._chuan_rui_ro("la_chua_biet") == "nguy_hiem"
    assert cong_cu._chuan_rui_ro("doc") == "doc"
    assert cong_cu._chuan_rui_ro("ghi") == "ghi"
    assert cong_cu._chuan_rui_ro("nguy_hiem") == "nguy_hiem"


# ==============================================================================
# MỤC 3: Tải video SSRF & kiểm tra đích nội bộ
# ==============================================================================

def test_muc_3_tai_video_tu_choi_ip_rieng_va_loopback(monkeypatch):
    """Tải video theo URI trả về phải từ chối IP loopback (khi chưa cho phép) và IP mạng riêng."""
    gemini_mod = lay_gemini_mod()

    mod = gemini_mod.Module({"id": "gemini-api", "tien_to": "gemini", "cung_cap": ["video"]})
    mod._ct = MagicMock()
    mod._ct.TIMEOUT_KET_NOI = 5.0
    mod._ct.TIMEOUT_DOC = 10.0
    mod._ct._dong_ho = MagicMock(return_value=1000.0)
    mod._ct.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    # 1. Loopback khi chưa bật WEB2API_TEST_CHO_PHEP_LOOPBACK
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    ma, hd, than = asyncio.run(mod._tai("http://127.0.0.1:8080/video.mp4", "fake-key"))
    assert ma == 502
    assert json.loads(than)["error"]["code"] == "ssrf_blocked"

    # 2. IP riêng tư 192.168.x.x
    ma, hd, than = asyncio.run(mod._tai("http://192.168.1.1:8080/video.mp4", "fake-key"))
    assert ma == 502
    assert json.loads(than)["error"]["code"] == "ssrf_blocked"

    # 3. IP riêng tư 10.x.x.x
    ma, hd, than = asyncio.run(mod._tai("http://10.0.0.1:8080/video.mp4", "fake-key"))
    assert ma == 502
    assert json.loads(than)["error"]["code"] == "ssrf_blocked"


def test_muc_3_chuyen_huong_sang_ip_rieng_bi_chan():
    """Chuyển hướng (302) sang IP riêng bị chặn ở chặng chuyển hướng."""
    gemini_mod = lay_gemini_mod()

    mod = gemini_mod.Module({"id": "gemini-api", "tien_to": "gemini", "cung_cap": ["video"]})
    mod._ct = MagicMock()
    mod._ct.TIMEOUT_KET_NOI = 5.0
    mod._ct.TIMEOUT_DOC = 10.0
    mod._ct._dong_ho = MagicMock(return_value=1000.0)
    mod._ct.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    # Mock client tải video: chặng 1 trả 302 chuyển hướng tới 192.168.1.50
    class MockRedirectTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.lan = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.lan += 1
            if self.lan == 1:
                return httpx.Response(302, headers={"location": "http://192.168.1.50/video.mp4"}, request=request)
            return httpx.Response(200, content=b"video", request=request)

    client_mock = httpx.AsyncClient(transport=TransportGhimIp(mod._kiem_ip, goc=MockRedirectTransport()))
    mod._client_tai = lambda: client_mock

    # Chặng đầu URL công khai TEST-NET-2 (198.51.100.1) qua kiểm, chặng 2 sang 192.168.1.50 bị chặn
    ma, hd, than = asyncio.run(mod._tai("http://198.51.100.1/video.mp4", "fake-key"))
    assert ma == 502
    assert json.loads(than)["error"]["code"] == "ssrf_blocked"


def test_muc_3_authorization_khong_di_theo_sang_host_khac():
    """Khi tải video hoặc chuyển hướng sang host khác, header x-goog-api-key và X-Proxy-Token không đi theo."""
    gemini_mod = lay_gemini_mod()

    mod = gemini_mod.Module({"id": "gemini-api", "tien_to": "gemini", "cung_cap": ["video"]})
    mod._ct = MagicMock()
    mod._ct.TIMEOUT_KET_NOI = 5.0
    mod._ct.TIMEOUT_DOC = 10.0
    mod._ct._dong_ho = MagicMock(return_value=1000.0)
    mod._ct.base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    mod._proxy_token = "token-proxy-bao-mat"

    yeu_cau_den: list[httpx.Request] = []

    class MockCaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            yeu_cau_den.append(request)
            return httpx.Response(200, content=b"ok-video", headers={"content-type": "video/mp4"}, request=request)

    # Chạy với host công khai khác với base_url
    client_mock = httpx.AsyncClient(transport=TransportGhimIp(lambda ip: None, goc=MockCaptureTransport()))
    mod._client_tai = lambda: client_mock

    ma, hd, _ = asyncio.run(mod._tai("http://198.51.100.2/cdn-video.mp4", "fake-api-key"))
    assert ma == 200
    assert len(yeu_cau_den) == 1
    req = yeu_cau_den[0]
    # Header bí mật không bao giờ lọt sang host khác
    assert "x-goog-api-key" not in req.headers
    assert "x-proxy-token" not in req.headers
    assert "authorization" not in req.headers


# ==============================================================================
# MỤC 4: Cloudflare Worker proxy Gemini & proxy_token
# ==============================================================================

def test_muc_4_proxy_token_header_gui_khi_dat():
    """Khi đặt proxy_token, module phải gửi header X-Proxy-Token tới proxy; không gửi khi để trống."""
    gemini_mod = lay_gemini_mod()

    # 1. Có đặt proxy_token
    mod = gemini_mod.Module({"id": "gemini-api", "tien_to": "gemini", "cung_cap": ["chat", "video", "anh"]})
    mod.nap({"api_keys": [K1], "base_url": "https://proxy.example.workers.dev", "proxy_token": "token-bi-mat-999"})

    # Header gốc
    h_goc = mod._tieu_de_goc(K1)
    assert h_goc["x-goog-api-key"] == K1
    assert h_goc["X-Proxy-Token"] == "token-bi-mat-999"

    # Header chat từ _ct
    h_chat = mod._ct._tieu_de(K1)
    assert h_chat["Authorization"] == f"Bearer {K1}"
    assert h_chat["X-Proxy-Token"] == "token-bi-mat-999"

    # 2. Không đặt proxy_token
    mod_trong = gemini_mod.Module({"id": "gemini-api", "tien_to": "gemini", "cung_cap": ["chat", "video", "anh"]})
    mod_trong.nap({"api_keys": [K1], "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"})
    h_trong_goc = mod_trong._tieu_de_goc(K1)
    assert "X-Proxy-Token" not in h_trong_goc
    h_trong_chat = mod_trong._ct._tieu_de(K1)
    assert "X-Proxy-Token" not in h_trong_chat


def test_muc_4_proxy_token_che_trong_cau_hinh_va_luu_an_toan(moi_truong):
    """Trường proxy_token phải được che trong GET /api/modules/gemini-api/cau-hinh."""
    client, data_dir = moi_truong
    # Chép module gemini-api vào thư mục dữ liệu
    shutil_src = GOC / "modules" / "gemini-api"
    dest = data_dir / "modules" / "gemini-api"
    ghi_thu_muc(dest, {
        "manifest.json": (shutil_src / "manifest.json").read_bytes(),
        "module.py": (shutil_src / "module.py").read_bytes(),
    })

    client.post("/api/modules/nap-lai", headers=QT)

    # Cài đặt với proxy_token
    token_tho = "sk-lan-0000000000000000000000000000xyzw"
    r_cai = client.post("/api/modules/gemini-api/cai", headers=QT, json={
        "cau_hinh": {
            "api_keys": [K1],
            "base_url": "https://proxy.example.workers.dev",
            "proxy_token": token_tho
        }
    })
    # cai() gọi sức khoẻ tới URL giả sẽ lỗi kết nối, nhưng ta có thể mock suc_khoe để lưu cấu hình
    # hoặc gọi trực tiếp Module.cai
    gemini_mod = lay_gemini_mod()
    mod = gemini_mod.Module(json.loads((shutil_src / "manifest.json").read_text(encoding="utf-8")))
    mod._tao = MagicMock()
    mock_ct = MagicMock()
    mock_ct.suc_khoe = AsyncMock(return_value={"khoa": [{"khoa": "…0001", "ok": True, "loi": None}]})
    mock_ct.dung = AsyncMock()
    mod._tao.return_value = mock_ct

    kq = asyncio.run(mod.cai({
        "api_keys": [K1],
        "base_url": "https://proxy.example.workers.dev",
        "proxy_token": token_tho
    }))
    assert kq["ok"] is True
    assert kq["cau_hinh_luu"]["proxy_token"] == token_tho

    # Lưu cấu hình vào đĩa để test endpoint GET cau-hinh
    truong_ch = mod.manifest.get("truong_cau_hinh", [])
    kho.luu_cai_dat("gemini-api", truong_ch, kq["cau_hinh_luu"])

    r_ch = client.get("/api/modules/gemini-api/cau-hinh", headers=QT)
    assert r_ch.status_code == 200
    ch = r_ch.json()["gia_tri"]
    # proxy_token phải được che, không lộ chuỗi thô
    assert ch["proxy_token"] != token_tho
    assert ch["proxy_token"].startswith("…")
    assert token_tho not in r_ch.text

    # Kiểm tra bí mật lưu riêng trong tệp bí mật
    bi_mat = kho.doc_bi_mat("gemini-api")
    assert bi_mat["proxy_token"] == token_tho


def test_muc_4_cloudflare_worker_tu_choi_khi_thieu_token():
    """Mã nguồn deploy/cloudflare-gemini-proxy.js phải bắt buộc PROXY_TOKEN và không có query token."""
    tep_js = GOC / "deploy" / "cloudflare-gemini-proxy.js"
    assert tep_js.is_file()
    js = tep_js.read_text(encoding="utf-8")

    # Không còn nhận token qua query parameter (lọt log)
    assert "proxy_token" not in js or "searchParams.get(\"proxy_token\")" not in js
    assert "searchParams.get" not in js

    # Bắt buộc kiểm tra env.PROXY_TOKEN và X-Proxy-Token
    assert "!env.PROXY_TOKEN" in js
    assert 'request.headers.get("X-Proxy-Token")' in js
    assert 'status: 403' in js


# ==============================================================================
# KIỂM TRA LEO THANG QUYỀN VÀ BẢO MẬT (LUẬT CỨNG BẢO MẬT)
# ==============================================================================

def test_bao_mat_leo_thang_quyen_401_403(moi_truong):
    """Mọi đường quản trị phải trả 401 khi không khoá, 403 khi dùng khoá khách vai thấp."""
    client, data_dir = moi_truong

    # 1. Tuyến trình duyệt (/api/trinh-duyet)
    r_401 = client.post("/api/trinh-duyet/gia/mo", json={"loai": "chromium"})
    assert r_401.status_code == 401, f"Không khoá phải 401: {r_401.status_code}"

    # Tạo khoá khách không có quyền quản trị
    r_k = client.post("/api/khoa-khach", headers=QT, json={
        "nhan": "Khách Thường", "pham_vi": ["chat"]
    })
    assert r_k.status_code == 200
    khoa_khach = r_k.json()["khoa"]["khoa"]
    hd_khach = {"Authorization": f"Bearer {khoa_khach}"}

    r_403 = client.post("/api/trinh-duyet/gia/mo", headers=hd_khach, json={"loai": "chromium"})
    assert r_403.status_code == 403, f"Khoá khách gọi quản trị phải 403: {r_403.status_code}"

    # 2. Tuyến quản lý người dùng (/api/nguoi-dung)
    assert client.get("/api/nguoi-dung").status_code == 401
    assert client.get("/api/nguoi-dung", headers=hd_khach).status_code == 403

    # 3. Tuyến phê duyệt (/api/phe-duyet)
    assert client.get("/api/phe-duyet").status_code == 401
    assert client.get("/api/phe-duyet", headers=hd_khach).status_code == 403
