"""NV261 — đo tài nguyên theo yêu cầu: bộ đo (quét RAM theo tham chiếu, lấy mẫu CPU,
cgroup dịch vụ phụ), API POST /api/he-thong/do-tai-nguyen, chi phí nạp module.

Chuẩn so RAM: tracemalloc (đo phần cấp phát THẬT của 3 module giả) — sai số mục tiêu ≤ 20%.
Module giả A: bytearray lớn ở biến toàn cục. B: dict đệm toàn cục (ca 14b cũ bỏ sót 100%).
C: hàng đợi trong thể hiện (instance giữ deque chứa bytearray).
"""

from __future__ import annotations

import gc
import json
import sys
import threading
import time
import tracemalloc
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import do_tai_nguyen as dtn
from app.main import app

KHOA_QT = "khoa-qt-nv261-test"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
MB = 1024 * 1024


# ───────────────────────── quét RAM theo tham chiếu ─────────────────────────

def _module_gia(ten: str) -> types.ModuleType:
    m = types.ModuleType(ten)
    sys.modules[ten] = m
    return m


def _do_chuan_tracemalloc(khoi_tao) -> float:
    """MB cấp phát thật của phần dữ liệu do khoi_tao() tạo (đo bằng tracemalloc)."""
    gc.collect()
    tracemalloc.start()
    try:
        truoc = tracemalloc.get_traced_memory()[0]
        giu = khoi_tao()
        sau = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    mb = (sau - truoc) / MB
    del giu
    gc.collect()
    return mb


def _sai_so(do_duoc: float, chuan: float) -> float:
    return abs(do_duoc - chuan) / chuan


def test_quet_ram_bytearray_toan_cuc_sai_so_nho():
    ten = "web2api_module_gia_a_test"
    m = _module_gia(ten)
    try:
        def tao():
            m.DU_LIEU = bytearray(30 * MB)
            return m.DU_LIEU
        chuan = _do_chuan_tracemalloc(tao)
        assert 25 < chuan < 35
        byte_, day_du = dtn.quet_ram(list(m.__dict__.values()), time.monotonic() + 5)
        assert day_du
        assert _sai_so(byte_ / MB, chuan) <= 0.20, (byte_ / MB, chuan)
    finally:
        sys.modules.pop(ten, None)


def test_quet_ram_dict_dem_toan_cuc_khong_bao_0():
    """Ca hỏng của 14b CŨ: dict đệm toàn cục bị bỏ sót → báo 0. Bản mới KHÔNG được báo 0."""
    ten = "web2api_module_gia_b_test"
    m = _module_gia(ten)
    try:
        def tao():
            m.DEM = {f"khoa-{i}": bytearray(64 * 1024) for i in range(320)}  # ~20 MB
            return m.DEM
        chuan = _do_chuan_tracemalloc(tao)
        assert chuan > 15
        byte_, day_du = dtn.quet_ram(list(m.__dict__.values()), time.monotonic() + 5)
        assert day_du
        assert byte_ > 0, "dict đệm toàn cục bị báo 0 — lỗi 14b cũ"
        assert _sai_so(byte_ / MB, chuan) <= 0.20, (byte_ / MB, chuan)
    finally:
        sys.modules.pop(ten, None)


def test_quet_ram_hang_doi_trong_the_hien():
    ten = "web2api_module_gia_c_test"
    m = _module_gia(ten)
    try:
        from collections import deque

        class TheHien:
            def __init__(self):
                self.hang_doi = deque(bytearray(1024 * 1024) for _ in range(15))  # ~15 MB

        def tao():
            m.THE = TheHien()
            return m.THE
        chuan = _do_chuan_tracemalloc(tao)
        assert chuan > 10
        goc = list(m.__dict__.values()) + [m.THE]
        byte_, day_du = dtn.quet_ram(goc, time.monotonic() + 5)
        assert day_du
        assert _sai_so(byte_ / MB, chuan) <= 0.20, (byte_ / MB, chuan)
    finally:
        sys.modules.pop(ten, None)


def test_quet_ram_bo_qua_lop_va_ham_khong_dem_ma():
    m = _module_gia("web2api_module_gia_d_test")
    try:
        class LopLon:
            THUOC_TINH_LOP = bytearray(10 * MB)  # thuộc lớp: spec bảo bỏ qua lớp

        def ham():
            pass

        m.LopLon = LopLon
        m.ham = ham
        m.nho = bytearray(1024)
        byte_, _ = dtn.quet_ram(list(m.__dict__.values()), time.monotonic() + 5)
        # Chỉ đếm bytearray nhỏ + dict/globals; thuộc tính lớp 10 MB KHÔNG được tính.
        assert byte_ < 5 * MB, byte_
    finally:
        sys.modules.pop("web2api_module_gia_d_test", None)


def test_quet_ram_cat_o_han_thoi_gian():
    m = _module_gia("web2api_module_gia_e_test")
    try:
        m.DS = [bytearray(1024) for _ in range(5000)]
        _, day_du = dtn.quet_ram(list(m.__dict__.values()), time.monotonic() - 1)
        assert day_du is False
    finally:
        sys.modules.pop("web2api_module_gia_e_test", None)


def test_quet_ram_tham_chieu_dung_chung_dem_mot_lan_va_vong_tu_ket_thuc():
    m = _module_gia("web2api_module_gia_f_test")
    try:
        dung_chung = bytearray(5 * MB)
        m.A = [dung_chung, dung_chung, {"x": dung_chung}]  # 3 đường tới CÙNG một đối tượng
        vong = []
        vong.append(vong)  # tự tham chiếu: không có tập đã-thấy thì quét không bao giờ xong
        m.VONG = vong
        byte_, day_du = dtn.quet_ram(list(m.__dict__.values()), time.monotonic() + 5)
        assert day_du
        # Đếm đúng một lần: ~5 MB, không phải ~15 MB.
        assert byte_ < 7 * MB, byte_ / MB
        assert byte_ > 4 * MB, byte_ / MB
    finally:
        sys.modules.pop("web2api_module_gia_f_test", None)


# ───────────────────────── lấy mẫu CPU ─────────────────────────

def test_lay_mau_cpu_gan_cho_module_dang_chay(tmp_path):
    thu_muc = tmp_path / "modules" / "gia-ban"
    thu_muc.mkdir(parents=True)
    (thu_muc / "module.py").write_text(
        "import time\n\ndef quay():\n    cuoi = time.monotonic() + 2.6\n    x = 0\n"
        "    while time.monotonic() < cuoi:\n        x += 1\n    return x\n"
    )
    ten = "web2api_module_gia_ban_test"
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        ten, thu_muc / "module.py", submodule_search_locations=[str(thu_muc)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    try:
        luong = threading.Thread(target=mod.quay, daemon=True)
        luong.start()
        time.sleep(0.2)
        dem, tong = dtn.lay_mau_cpu(1.2, 0.01, {"gia-ban", "gia-ranh"})
        luong.join(timeout=5)
        assert tong > 0
        assert dem.get("gia-ban", 0) > 0, dem
        # Module «gia-ranh» không chạy gì: không được gán mẫu nào đáng kể.
        assert dem.get("gia-ranh", 0) == 0, dem
        # Phần lớn mẫu của luồng bận thuộc về gia-ban.
        assert dem["gia-ban"] >= tong * 0.3, (dem, tong)
    finally:
        sys.modules.pop(ten, None)


# ───────────────────────── cgroup dịch vụ phụ ─────────────────────────

def _cay_cgroup_gia(goc: Path) -> Path:
    p = goc / "system.slice" / "docker-dich-vu-gia.scope"
    p.mkdir(parents=True)
    (p / "memory.current").write_text("52428800\n")  # 50 MB
    (p / "cpu.stat").write_text("usage_usec 2000000\nnr_periods 10\n")
    return p


def test_tim_va_doc_cgroup_dich_vu(tmp_path):
    _cay_cgroup_gia(tmp_path)
    tim = dtn.tim_thu_muc_cgroup("dich-vu-gia", tmp_path)
    assert tim is not None and tim.name == "docker-dich-vu-gia.scope"
    ram, cpu_us = dtn.doc_cgroup_dich_vu(tim)
    assert ram == 52428800
    assert cpu_us == 2000000


def test_cgroup_khong_co_thi_chua_do_khong_uoc_luong(tmp_path):
    assert dtn.tim_thu_muc_cgroup("khong-he-co", tmp_path) is None


# ───────────────────────── do_tat_ca với sổ giả ─────────────────────────

class _KM:
    def __init__(self, id_, manifest, ten_ma):
        self.id = id_
        self.manifest = manifest
        self.ten_ma = ten_ma


class _SoGia:
    def __init__(self, catalog, dang_chay=None, tu_xa=None):
        self.catalog = catalog
        self.dang_chay = dang_chay or {}
        self.tu_xa = tu_xa or {}


def test_do_tat_ca_du_truong_va_nhan_nguon(monkeypatch):
    ten = "web2api_module_gia_do_test"
    m = _module_gia(ten)
    m.DEM = {"a": bytearray(2 * MB)}
    km_loi = _KM("gia-loi", {"id": "gia-loi", "tai_nguyen": {"gpu": True}}, ten)
    km_dv = _KM("gia-dv", {"id": "gia-dv", "dich_vu_phu": ["khong-co-container-nay"],
                           "tai_nguyen": {}}, "web2api_module_khong_ton_tai")
    so = _SoGia({"gia-loi": km_loi, "gia-dv": km_dv},
                dang_chay={}, tu_xa={"gia-xa": {"id": "gia-xa"}})
    monkeypatch.setattr(dtn.he_thong, "doc_gpu", lambda: None)
    try:
        kq = dtn.do_tat_ca(so, cua_so_cpu=0.3)
    finally:
        sys.modules.pop(ten, None)
    assert kq["cua_so_cpu_giay"] >= 0.3
    assert kq["luc"]
    mods = kq["module"]
    loi = mods["gia-loi"]
    assert loi["nguon"] == "uoc_luong" and loi["goc"] == "quet_tham_chieu" and loi["rieng"] is False
    assert loi["ram_mb"] > 1.5
    assert loi["gpu"] == "cần GPU, máy này không có"
    dv = mods["gia-dv"]
    assert dv["nguon"] == "chua_do" and dv["ram_mb"] is None and dv["rieng"] is True and dv["goc"] == "cgroup"
    xa = mods["gia-xa"]
    assert xa["nguon"] == "chua_do" and xa["rieng"] is True
    tong = kq["tong"]
    assert tong["ram_loi_mb"] > 0
    assert tong["gpu"] is None
    assert tong["chua_quy_cho_module_mb"] >= 0
    assert tong["chua_quy_cho_module_mb"] <= tong["ram_loi_mb"]


# ───────────────────────── chi phí nạp ─────────────────────────

def test_chi_phi_nap_ghi_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    assert dtn.doc_chi_phi_nap().get("gia-x") is None
    dtn.ghi_chi_phi_nap("gia-x", 12.34)
    assert dtn.doc_chi_phi_nap()["gia-x"] == pytest.approx(12.3, abs=0.1)
    dtn.ghi_chi_phi_nap("gia-x", 3.0)  # ghi đè bằng lần nạp mới nhất
    assert dtn.doc_chi_phi_nap()["gia-x"] == pytest.approx(3.0, abs=0.1)


# ───────────────────────── API ─────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app) as c:
        yield c


def test_api_can_quan_tri(client):
    r = client.post("/api/he-thong/do-tai-nguyen")
    assert r.status_code in (401, 403), r.status_code


def test_api_do_tai_nguyen_ok_no_store(client):
    r = client.post("/api/he-thong/do-tai-nguyen", headers=QT)
    assert r.status_code == 200, r.text
    assert r.headers.get("cache-control") == "no-store"
    j = r.json()
    assert set(j) >= {"luc", "cua_so_cpu_giay", "module", "tong"}
    assert j["cua_so_cpu_giay"] >= 2.0
    for mid, m in j["module"].items():
        assert m["nguon"] in ("do", "uoc_luong", "chua_do"), (mid, m)
        assert m["goc"] in ("cgroup", "quet_tham_chieu", "lay_mau"), (mid, m)
        assert isinstance(m["rieng"], bool)
    tong = j["tong"]
    assert tong["ram_loi_mb"] > 0 and tong["cpu_loi_pct"] >= 0
    assert "gpu" in tong and "chua_quy_cho_module_mb" in tong and "ram_may" in tong


def test_api_chan_tan_suat(client):
    import app.main as _main

    _main._LAN_DO_CUOI.clear()  # cô lập trạng thái giới hạn dùng chung của tiến trình test
    _main._DANG_DO.clear()
    r1 = client.post("/api/he-thong/do-tai-nguyen", headers=QT)
    assert r1.status_code == 200
    r2 = client.post("/api/he-thong/do-tai-nguyen", headers=QT)
    assert r2.status_code == 429, r2.status_code
