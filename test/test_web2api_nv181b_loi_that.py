"""NV181b — ba module đợt rủi ro cao (cameranha, nhin-nha, chay-ma) phải NỐI ĐƯỢC vào lõi thật.

Bài học NV181: test gọi thẳng hàm module vẫn xanh trong khi lõi không nạp được
thực thể (manifest thiếu cung_cap/hang/tien_to/truong_cau_hinh, lớp không tên
«Module», thuc_the/trang_thai đồng bộ). Test này đi ĐÚNG đường của lõi:
thả module thật vào thư mục dữ liệu → POST /api/modules/nap-lai → cai qua API →
GET /api/thiet-bi thấy thực thể (rủi ro đúng mức, trạng thái có mặt) →
POST /api/thiet-bi/{id}/hanh-dong chạy được hành động an toàn nhất.
Dịch vụ phụ là GIẢ (HTTP server trong tiến trình); conftest chặn Internet.
"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule

KHOA_QT = "khoa-quan-tri-test-nv181b-0123456789ab"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
GOC_KHO = Path(__file__).resolve().parents[1]

KHOA_DICH_VU = "khoa-dich-vu-gia-nv181b-0123456789abcdef"


# ── Dịch vụ phụ GIẢ dùng chung (ML nhin-nha + chay-ma) ─────────────────────

class DichVuGia:
    """HTTP server giả: đếm lượt gọi theo đường; trả thân đóng hộp."""

    def __init__(self) -> None:
        self.dem: dict[str, int] = {}
        self._khoa_dem = threading.Lock()
        gia = self

        class XuLy(BaseHTTPRequestHandler):
            def log_message(self, *a):  # im lặng
                pass

            def _tra(self, than: dict) -> None:
                than_b = json.dumps(than).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(than_b)))
                self.end_headers()
                self.wfile.write(than_b)

            def _ghi(self) -> None:
                with gia._khoa_dem:
                    gia.dem[self.path] = gia.dem.get(self.path, 0) + 1

            def do_GET(self):  # noqa: N802 — tên theo http.server
                self._ghi()
                if self.path == "/suc-khoe":
                    self._tra({"ok": True, "yolo": {"san_sang": True},
                               "khuon_mat": {"san_sang": True}})
                else:
                    self._tra({"ok": True})

            def do_POST(self):  # noqa: N802
                dai = int(self.headers.get("Content-Length") or 0)
                if dai:
                    self.rfile.read(dai)
                self._ghi()
                if self.path == "/suc-khoe":
                    self._tra({"ok": True, "yolo": {"san_sang": True},
                               "khuon_mat": {"san_sang": True}})
                elif self.path == "/kiem-tinh":
                    self._tra({"goi_y": True, "ly_do": "",
                               "kiem_tinh": "khong thay van de"})
                else:
                    self._tra({"ok": True})

        self._may = ThreadingHTTPServer(("127.0.0.1", 0), XuLy)
        self.url = f"http://127.0.0.1:{self._may.server_address[1]}"
        self._luong = threading.Thread(target=self._may.serve_forever, daemon=True)
        self._luong.start()

    def tong_goi(self) -> int:
        with self._khoa_dem:
            return sum(self.dem.values())

    def dong(self) -> None:
        self._may.shutdown()
        self._may.server_close()


# ── Môi trường lõi thật (khuôn moi_truong của test_web2api_thiet_bi) ──────

@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "modules").mkdir(parents=True)
    anh_dir = tmp_path / "anh"
    anh_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # Trường bi_mat (danh_sach_camera, khoa_dich_vu…) lưu mã hoá bằng khoá chính;
    # thiếu khoá chính thì lõi đọc lại thành rỗng (đóng khi lỗi) — test phải có.
    monkeypatch.setenv("WEB2API_KHOA_CHINH", bytes(range(32)).hex())
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh_dir)
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        yield client, data_dir, so


@pytest.fixture
def dich_vu_gia():
    dv = DichVuGia()
    yield dv
    dv.dong()


def _nap_module(client, data_dir, id_module: str) -> None:
    nguon = GOC_KHO / "modules" / id_module
    dich = data_dir / "modules" / id_module
    shutil.copytree(nguon, dich, ignore=shutil.ignore_patterns("__pycache__"))
    r = client.post("/api/modules/nap-lai", headers=QT)
    assert r.status_code == 200, r.text
    assert id_module in r.json()["them"], r.json()


def _cai(client, id_module: str, cau_hinh: dict) -> dict:
    r = client.post(f"/api/modules/{id_module}/cai", headers=QT,
                    json={"cau_hinh": cau_hinh})
    assert r.status_code == 200, r.text
    than = r.json()
    assert than.get("ok") is not False, than
    return than


def _thuc_the(client, id_module: str) -> list[dict]:
    r = client.get(f"/api/thiet-bi?module={id_module}", headers=QT)
    assert r.status_code == 200, r.text
    return r.json()["thuc_the"]


def _hanh_dong(client, id_thuc_the: str, hanh_dong: str,
               tham_so: dict | None = None, xac_nhan: bool = True):
    return client.post(f"/api/thiet-bi/{id_thuc_the}/hanh-dong", headers=QT,
                       json={"hanh_dong": hanh_dong, "tham_so": tham_so or {},
                             "xac_nhan": xac_nhan, "ly_do": "test nv181b"})


# ── cameranha (NV167) ───────────────────────────────────────────────────────

def test_camera_noi_loi_that(moi_truong):
    client, data_dir, _so = moi_truong
    _nap_module(client, data_dir, "cameranha")

    # Chưa cấu hình: không thực thể camera nào hiện qua lõi.
    assert _thuc_the(client, "cameranha") == []

    ds = [{"name": "Cua", "kind": "go2rtc",
           "base": "http://10.0.0.6:1984", "src": "cua"}]
    _cai(client, "cameranha", {"danh_sach_camera": json.dumps(ds)})

    ds_tt = _thuc_the(client, "cameranha")
    theo_id = {e["id"]: e for e in ds_tt}
    assert "cam.cua" in theo_id, [e["id"] for e in ds_tt]
    doc = theo_id["cam.cua"]
    assert doc["rui_ro_mac_dinh"] == "doc"
    # Tính năng phải đi qua khoá «tinh_nang» của lõi (bản cũ dùng «hanh_dong»
    # nên lõi thấy danh sách rỗng và chặn mọi hành động).
    ten_hd = {tn if isinstance(tn, str) else tn.get("hanh_dong") or tn.get("ten")
              for tn in doc["tinh_nang"]}
    assert "canh_bao_moi_nhat" in ten_hd
    # trang_thai của module phải chảy về lõi (bản cũ lõi không nạp nổi module).
    assert doc["trang_thai"] is not None
    # Loa mặc định TẮT → không có thực thể ghi cam.phat_*.
    assert not any(i.startswith("cam.phat_") for i in theo_id)

    # Hành động an toàn nhất: xem cảnh báo gần nhất — thuần bộ nhớ, không mạng.
    r = _hanh_dong(client, "cam.cua", "canh_bao_moi_nhat")
    assert r.status_code == 200, r.text
    than = r.json()
    assert than.get("ok") is True, than
    assert than.get("canh_bao") == []


# ── nhin-nha (NV168) ────────────────────────────────────────────────────────

def test_nhin_nha_noi_loi_that(moi_truong, dich_vu_gia):
    client, data_dir, _so = moi_truong
    _nap_module(client, data_dir, "nhin-nha")

    # Cài ở trạng thái TẮT (mặc định): cài không được gọi dịch vụ ML.
    _cai(client, "nhin-nha", {"nhin_nha_bat": "0",
                              "dich_vu_url": dich_vu_gia.url})
    assert dich_vu_gia.tong_goi() == 0

    theo_id = {e["id"]: e for e in _thuc_the(client, "nhin-nha")}
    assert theo_id["nhin_nha.mat"]["rui_ro_mac_dinh"] == "doc"
    assert theo_id["nhin_nha.so_mat"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert theo_id["nhin_nha.nhan_dien"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert theo_id["nhin_nha.mat"]["trang_thai"] is not None

    # Đang tắt: hành động bị module từ chối «chua_bat», không chạm dịch vụ.
    r = _hanh_dong(client, "nhin_nha.mat", "quet_anh", {"anh_b64": ""})
    assert "chua_bat" in r.text, r.text
    assert dich_vu_gia.tong_goi() == 0

    # Bật với dịch vụ giả: hành động đọc trạng thái mô hình chạy qua lõi.
    _cai(client, "nhin-nha", {"nhin_nha_bat": "1",
                              "khoa_dich_vu": KHOA_DICH_VU,
                              "dich_vu_url": dich_vu_gia.url})
    r = _hanh_dong(client, "nhin_nha.mat", "trang_thai_mo_hinh")
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True, r.json()


# ── chay-ma (NV171) ─────────────────────────────────────────────────────────

def test_chay_ma_noi_loi_that(moi_truong, dich_vu_gia):
    client, data_dir, _so = moi_truong
    _nap_module(client, data_dir, "chay-ma")

    # Tắt mặc định: thực thể vẫn khai nhưng hành động bị từ chối, không gọi mạng.
    _cai(client, "chay-ma", {"chay_bat": "0", "dich_vu_url": dich_vu_gia.url})
    theo_id = {e["id"]: e for e in _thuc_the(client, "chay-ma")}
    assert theo_id["chay_ma.kiem"]["rui_ro_mac_dinh"] == "doc"
    # Thực thể chạy mã LUÔN nguy_hiem — tách riêng khỏi thực thể đọc.
    assert theo_id["chay_ma.chay"]["rui_ro_mac_dinh"] == "nguy_hiem"
    assert theo_id["chay_ma.kiem"]["trang_thai"] is not None

    r = _hanh_dong(client, "chay_ma.kiem", "kiem_tinh", {"ma": "print(1)"})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is False, r.json()
    assert dich_vu_gia.tong_goi() == 0

    # Bật + khoá dịch vụ: soi tĩnh chạy thật qua lõi tới dịch vụ giả.
    _cai(client, "chay-ma", {"chay_bat": "1", "khoa_dich_vu": KHOA_DICH_VU,
                             "dich_vu_url": dich_vu_gia.url})
    r = _hanh_dong(client, "chay_ma.kiem", "kiem_tinh", {"ma": "print(1)"})
    assert r.status_code == 200, r.text
    than = r.json()
    assert than.get("ok") is True, than
    assert than.get("goi_y") is True, than

    # «Xác nhận một lần» không mở đường chạy mã tuỳ ý: mỗi lượt gọi nguy hiểm
    # thiếu xac_nhan vẫn bị lõi chặn 409.
    r = _hanh_dong(client, "chay_ma.chay", "chay_python",
                   {"ma": "print(1)"}, xac_nhan=False)
    assert r.status_code == 409, r.text


def test_chay_ma_goi_y_tuong_thich_dich_vu_cu():
    """NV200: dịch vụ đời cũ trả khoá «co_the_chay» — module vẫn ánh xạ sang «goi_y» (C3, tương thích luân phiên)."""
    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location("chay_ma_nv200", Path("modules/chay-ma/module.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.Module({"id": "chay-ma"})
    m.nap({"chay_bat": "1", "khoa_dich_vu": "KHOA-GIA", "dich_vu_url": "http://127.0.0.1:9/"})

    async def goi_gia(_duong, _than):
        return {"co_the_chay": True, "ly_do": "", "kiem_tinh": "ok"}

    m._goi_http = goi_gia
    ket = asyncio.run(m.thuc_hien("chay_ma.kiem", "kiem_tinh", {"ma": "print(1)"}))
    assert ket.get("ok") is True, ket
    assert ket.get("goi_y") is True, ket
    assert "co_the_chay" not in ket, ket
