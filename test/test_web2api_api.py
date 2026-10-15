"""Hợp đồng API của khung web2api (container mới, rỗng).

Mỗi test đặt WEB2API_DATA về thư mục tạm để không bao giờ đọc/ghi /data thật.
"""

import json
import logging
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import he_thong
from app.main import app, PHIEN_BAN

KHOA_QT = "AIzaSyKIEMNGHIEM-000000000000000000009"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    # `with` để chạy lifespan: sổ module (app.state.so) được dựng lúc khởi động.
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "phien_ban": PHIEN_BAN}
    assert PHIEN_BAN == "0.1.0-khung"


def test_he_thong_du_khoa_va_dung_kieu(client, tmp_path):
    r = client.get("/api/he-thong", headers=QT)
    assert r.status_code == 200
    j = r.json()
    assert j["phien_ban"] == PHIEN_BAN
    # RAM của máy (từ /proc/meminfo) và của container (cgroup) tách riêng, không trộn.
    ram = j["ram"]
    assert isinstance(ram["may"]["tong"], int) and ram["may"]["tong"] > 0
    assert isinstance(ram["may"]["trong"], int) and 0 <= ram["may"]["trong"] <= ram["may"]["tong"]
    assert set(ram["container"]) == {"gioi_han", "dang_dung"}
    for k in ("gioi_han", "dang_dung"):
        assert ram["container"][k] is None or isinstance(ram["container"][k], int)
    cpu = j["cpu"]
    assert isinstance(cpu["so_nhan_may"], int) and cpu["so_nhan_may"] >= 1
    assert cpu["gioi_han_container"] is None or isinstance(cpu["gioi_han_container"], (int, float))
    dia = j["dia"]
    assert dia["thu_muc"] == str(tmp_path)
    assert isinstance(dia["tong"], int) and isinstance(dia["trong"], int) and dia["trong"] <= dia["tong"]
    assert dia["loi"] is None
    assert j["gpu"] is None or isinstance(j["gpu"], list)


def test_dia_thu_muc_khong_ton_tai_bao_loi_khong_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "chua-co"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    j = TestClient(app).get("/api/he-thong", headers=QT).json()
    assert j["dia"]["tong"] is None and j["dia"]["trong"] is None
    assert "chua-co" in j["dia"]["loi"]


def test_cgroup_max_la_khong_gioi_han(tmp_path):
    (tmp_path / "memory.max").write_text("max\n")
    (tmp_path / "memory.current").write_text("12345\n")
    (tmp_path / "cpu.max").write_text("max 100000\n")
    assert he_thong.doc_ram_container(tmp_path) == {"gioi_han": None, "dang_dung": 12345}
    assert he_thong.doc_cpu_container(tmp_path) is None


def test_cgroup_co_gioi_han(tmp_path):
    (tmp_path / "memory.max").write_text("268435456\n")
    (tmp_path / "memory.current").write_text("1000\n")
    (tmp_path / "cpu.max").write_text("150000 100000\n")
    assert he_thong.doc_ram_container(tmp_path) == {"gioi_han": 268435456, "dang_dung": 1000}
    assert he_thong.doc_cpu_container(tmp_path) == 1.5


def test_cgroup_thieu_file(tmp_path):
    assert he_thong.doc_ram_container(tmp_path / "khong") == {"gioi_han": None, "dang_dung": None}
    assert he_thong.doc_cpu_container(tmp_path / "khong") is None


def test_gpu_khong_co_nvidia_smi(monkeypatch):
    monkeypatch.setattr(he_thong.shutil, "which", lambda _: None)
    assert he_thong.doc_gpu() is None


def test_gpu_nvidia_smi_loi(monkeypatch, caplog):
    monkeypatch.setattr(he_thong.shutil, "which", lambda _: "/usr/bin/nvidia-smi")

    def hong(*a, **k):
        raise subprocess.TimeoutExpired("nvidia-smi", 5)

    monkeypatch.setattr(he_thong.subprocess, "run", hong)
    with caplog.at_level(logging.WARNING):
        assert he_thong.doc_gpu() is None
    assert "nvidia-smi" in caplog.text


def test_gpu_doc_dung_dau_ra(monkeypatch):
    monkeypatch.setattr(he_thong.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        he_thong.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "NVIDIA GeForce RTX 3060, 12288, 3021\n", ""),
    )
    assert he_thong.doc_gpu() == [{"ten": "NVIDIA GeForce RTX 3060", "vram_tong_mb": 12288, "vram_dung_mb": 3021}]


def test_modules_chua_co_file_la_rong(client):
    r = client.get("/api/modules", headers=QT)
    assert r.status_code == 200
    assert r.json() == {"modules": [], "canh_bao": None, "thong_bao_so": 0}


def test_modules_doc_tu_file(client, tmp_path):
    (tmp_path / "modules.json").write_text(json.dumps({"modules": [{"id": "gemini-web", "ten": "Gemini web"}]}))
    assert client.get("/api/modules", headers=QT).json() == {
        "modules": [{"id": "gemini-web", "ten": "Gemini web"}],
        "canh_bao": None,
        "thong_bao_so": 0,
    }


@pytest.mark.parametrize("noi_dung", ["{hỏng", '{"modules": "không phải danh sách"}', "[1, 2]"])
def test_modules_hong_tra_rong_va_ghi_log(client, tmp_path, caplog, noi_dung):
    (tmp_path / "modules.json").write_text(noi_dung)
    with caplog.at_level(logging.WARNING):
        j = client.get("/api/modules", headers=QT).json()
    assert j["modules"] == []
    assert "modules.json" in j["canh_bao"]
    # Không nuốt im lặng: log có cảnh báo nêu rõ đường dẫn file.
    assert any(r.levelno == logging.WARNING and "modules.json" in r.getMessage() for r in caplog.records)


def test_goc_tra_trang_chon_trang_dau(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # index.html: đọc /api/tinh-nang rồi chuyển sang Studio hoặc Hệ thống → Module (đường tương đối).
    assert 'src="./khung.js"' in r.text and "taiTinhNang" in r.text and "Dang-nhap.dc.html" in r.text


def test_phuc_vu_support_js_va_trang_dc(client):
    r = client.get("/support.js")
    assert r.status_code == 200 and "DCLogic" in r.text
    for trang in ("/He-thong.dc.html", "/He-thong-AI.dc.html", "/Studio.dc.html", "/khung.js", "/khung.css"):
        assert client.get(trang).status_code == 200, trang
    for cu in ("/Khoi-dau.dc.html", "/Cai-module.dc.html"):  # trang cũ đã thay
        assert client.get(cu).status_code == 404, cu


@pytest.mark.parametrize(
    "duong",
    [
        "/%2e%2e/app/main.py",  # httpx giữ nguyên %2e%2e (đã đo), app nhận "/../app/main.py"
        "/..%2fapp%2fmain.py",
        "/%2e%2e/VERSION",
        "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ],
)
def test_chan_thoat_khoi_thu_muc_web(client, duong):
    r = client.get(duong)
    assert r.status_code == 404
    assert "import" not in r.text and "root:" not in r.text and "0.1.0" not in r.text


def test_api_khong_bi_mount_che(client):
    # /api/* đăng ký TRƯỚC mount "/" nên không rơi vào StaticFiles.
    assert client.get("/api/modules", headers=QT).headers["content-type"].startswith("application/json")
    assert client.get("/api/khong-co", headers=QT).status_code == 404


def test_ram_cpu_may_mac_dinh_doc_tu_proc(client):
    j = client.get("/api/he-thong", headers=QT).json()
    assert j["ram"]["may"]["nguon"] == "doc_tu_proc" and j["cpu"]["nguon"] == "doc_tu_proc"


def test_ram_cpu_may_do_nguoi_van_hanh_khai(client, monkeypatch):
    # Trong LXC, /proc/meminfo của container bị giới hạn chỉ thấy phần được cấp (đo 10/10/2026) → cho khai số máy.
    monkeypatch.setenv("WEB2API_MAY_RAM_MB", "12288")
    monkeypatch.setenv("WEB2API_MAY_CPU", "8")
    j = client.get("/api/he-thong", headers=QT).json()
    assert j["ram"]["may"] == {"tong": 12288 * 1024 * 1024, "trong": None, "nguon": "khai_bao"}
    assert j["cpu"]["so_nhan_may"] == 8 and j["cpu"]["nguon"] == "khai_bao"
    assert set(j["ram"]["container"]) == {"gioi_han", "dang_dung"}  # số container vẫn đọc cgroup


@pytest.mark.parametrize("gia_tri", ["abc", "0", "-5", "1.5"])
def test_khai_bao_sai_thi_bo_qua_va_canh_bao(client, monkeypatch, caplog, gia_tri):
    monkeypatch.setenv("WEB2API_MAY_RAM_MB", gia_tri)
    with caplog.at_level(logging.WARNING):
        j = client.get("/api/he-thong", headers=QT).json()
    assert j["ram"]["may"]["nguon"] == "doc_tu_proc" and "WEB2API_MAY_RAM_MB" in caplog.text
