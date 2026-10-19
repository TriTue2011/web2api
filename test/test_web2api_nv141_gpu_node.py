"""NV141 — gói dựng phía máy GPU (dich-vu/gpu-node/): test trước (đỏ).

Kiểm lớp cổng nút với dịch vụ phía sau GIẢ và nvidia-smi GIẢ, cùng kiểm tĩnh
Dockerfile/docker-compose.yml (ghim ảnh, không root, không 0.0.0.0 mặc định).
Không cần GPU thật, không mạng ra ngoài: máy chủ phụ đều là giả cục bộ.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

GOC_REPO = Path(__file__).resolve().parents[1]
GPU_NODE = GOC_REPO / "dich-vu" / "gpu-node"
CONG_DIR = GPU_NODE / "cong"


def _nap_cong():
    spec = importlib.util.spec_from_file_location("cong_node", CONG_DIR / "cong.py")
    assert spec and spec.loader, "thiếu dich-vu/gpu-node/cong/cong.py"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cong_node"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- phụ trợ


class DichVuGia(BaseHTTPRequestHandler):
    """Máy chủ phụ giả: ghi lại yêu cầu, trả JSON cố định."""

    nhan_duoc: list = []

    def _tra(self):
        dai = int(self.headers.get("Content-Length", 0) or 0)
        than = self.rfile.read(dai) if dai else b""
        type(self).nhan_duoc.append((self.command, self.path, than))
        noi = json.dumps({"ok": True, "duong": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(noi)))
        self.end_headers()
        self.wfile.write(noi)

    do_GET = _tra
    do_POST = _tra

    def log_message(self, *a):
        pass


@contextmanager
def may_chu_gia():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), DichVuGia)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


def _goi(co_so: str, duong: str, khoa: str | None = None, phuong_thuc="GET",
         du_lieu: bytes | None = None, tieu_de: dict | None = None):
    td = dict(tieu_de or {})
    if khoa is not None:
        td["Authorization"] = f"Bearer {khoa}"
    req = urllib.request.Request(co_so + duong, data=du_lieu, headers=td, method=phuong_thuc)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


@pytest.fixture()
def cong_mod():
    return _nap_cong()


@pytest.fixture()
def du_lieu(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def _smi_gia(tmp_path, noi_dung: str, ten="nvidia-smi") -> str:
    p = tmp_path / ten
    p.write_text("#!/bin/sh\n" + noi_dung + "\n", encoding="utf-8")
    p.chmod(0o755)
    return str(p)


@contextmanager
def cong_chay(cong, du_lieu, dich_vu_gia: dict, smi: str | None = None,
              them: dict | None = None):
    khoa = "khoa-kiem-thu-" + "x" * 40
    cau_hinh = {
        "dia_chi": "127.0.0.1", "cong": 0, "khoa": khoa, "thu_muc_du_lieu": str(du_lieu),
        "dich_vu": dich_vu_gia, "gioi_han_dong_thoi": 1, "han_cho_giay": 1,
        "ip_duoc_phep": [], "ten": "nut-kiem-thu", "id": "gpu-node-kiem-thu",
        "phien_ban": "1.0.0", "nvidia_smi": smi or "nvidia-smi-khong-ton-tai",
        "mo_hinh": ["large-v3"],
    }
    if them:
        cau_hinh.update(them)
    may_chu = cong.tao_may_chu(cau_hinh)
    cong.khoi_tao_khoa(str(du_lieu), khoa_ghi_de=khoa)
    t = threading.Thread(target=may_chu.serve_forever, daemon=True)
    t.start()
    co_so = f"http://127.0.0.1:{may_chu.server_address[1]}"
    try:
        yield co_so, khoa
    finally:
        may_chu.shutdown()


# ---------------------------------------------------------------- cổng nút


def test_xac_thuc_thieu_khoa_tra_401(cong_mod, du_lieu, tmp_path):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, _k):
        ma, _, _ = _goi(co_so, "/health")
        assert ma == 401


def test_xac_thuc_sai_khoa_tra_401(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, _k):
        ma, _, _ = _goi(co_so, "/health", khoa="sai-hoan-toan")
        assert ma == 401


def test_health_dung_hop_dong(cong_mod, du_lieu, tmp_path):
    smi = _smi_gia(tmp_path, 'echo "RTX 2060 SUPER, 8192, 1200, 55, 12"')
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}, smi=smi) as (co_so, khoa):
        ma, _, than = _goi(co_so, "/health", khoa=khoa)
        assert ma == 200
        h = json.loads(than)
        assert h["ok"] is True
        assert h["phien_ban"] == "1.0.0"
        assert len(h["gpu"]) == 1
        g = h["gpu"][0]
        assert g["ten"] == "RTX 2060 SUPER"
        assert g["vram_tong_mb"] == 8192 and g["vram_dung_mb"] == 1200
        assert g["nhiet_c"] == 55 and g["tai_pct"] == 12
        assert h["mo_hinh_da_nap"] == ["large-v3"]
        assert h["hang_doi"] == {"dang_chay": 0, "cho": 0}


def test_health_khong_card_thi_gpu_rong(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, khoa):
        ma, _, than = _goi(co_so, "/health", khoa=khoa)
        assert ma == 200
        h = json.loads(than)
        assert h["ok"] is True
        assert h["gpu"] == []


def test_health_smi_in_rac_khong_do(cong_mod, du_lieu, tmp_path):
    smi = _smi_gia(tmp_path, 'echo "RAC KHONG PHAI CSV"')
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}, smi=smi) as (co_so, khoa):
        ma, _, than = _goi(co_so, "/health", khoa=khoa)
        assert ma == 200
        assert json.loads(than)["gpu"] == []


def test_manifest_well_known(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(
        cong_mod, du_lieu, {"nghe": gia, "thi-giac": gia},
        them={"mo_hinh": ["large-v3", "Qwen3-VL-2B-Q4_K_M"]},
    ) as (co_so, khoa):
        ma, _, than = _goi(co_so, "/.well-known/w2a-node", khoa=khoa)
        assert ma == 200
        m = json.loads(than)
        assert m["id"] == "gpu-node-kiem-thu"
        assert set(m["kha_nang"]) >= {"nghe", "thi-giac"}
        assert "large-v3" in m["mo_hinh"]
        assert m["gioi_han_dong_thoi"] == 1


def test_hang_doi_vuot_gioi_han_tra_429(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, khoa):
        # Giữ chặt khe duy nhất từ phía test.
        khoa_sem = cong_mod._semaphore_toan_cuc
        assert khoa_sem.acquire(blocking=False)
        try:
            ma, tieu_de, _ = _goi(co_so, "/v1/audio/transcriptions", khoa=khoa,
                                  phuong_thuc="POST", du_lieu=b"x")
            assert ma == 429
            assert "Retry-After" in tieu_de
        finally:
            khoa_sem.release()


def test_ip_khong_duoc_phep_tra_403(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(
        cong_mod, du_lieu, {"nghe": gia}, them={"ip_duoc_phep": ["192.0.2.9"]}
    ) as (co_so, khoa):
        ma, _, _ = _goi(co_so, "/health", khoa=khoa)
        assert ma == 403


def test_ip_mac_dinh_rong_thi_cho_phep(cong_mod, du_lieu):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, khoa):
        ma, _, _ = _goi(co_so, "/health", khoa=khoa)
        assert ma == 200


def test_khoa_sinh_mot_lan_khong_lap_log(cong_mod, tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    khoa1 = cong_mod.khoi_tao_khoa(str(d))
    ra1 = capsys.readouterr().out
    assert khoa1 in ra1  # in ra console đúng một lần
    tep = d / "khoa"
    assert stat.S_IMODE(tep.stat().st_mode) == 0o600
    khoa2 = cong_mod.khoi_tao_khoa(str(d))
    ra2 = capsys.readouterr().out
    assert khoa1 == khoa2
    assert khoa1 not in ra2  # lần sau không in lại khoá


def test_chuyen_tiep_dung_dich_vu(cong_mod, du_lieu):
    DichVuGia.nhan_duoc.clear()
    with may_chu_gia() as gia, cong_chay(
        cong_mod, du_lieu, {"nghe": gia, "thi-giac": gia, "tach-am": gia}
    ) as (co_so, khoa):
        _goi(co_so, "/v1/audio/transcriptions", khoa=khoa, phuong_thuc="POST", du_lieu=b"am")
        _goi(co_so, "/v1/chat/completions", khoa=khoa, phuong_thuc="POST", du_lieu=b"{}")
        _goi(co_so, "/v1/audio/tach-am", khoa=khoa, phuong_thuc="POST", du_lieu=b"am")
        ma, _, _ = _goi(co_so, "/duong/la", khoa=khoa)
        assert ma == 404
        duong = [p for _, p, _ in DichVuGia.nhan_duoc]
        assert "/v1/audio/transcriptions" in duong
        assert "/v1/chat/completions" in duong
        assert "/v1/audio/tach-am" in duong


def test_log_khong_chua_khoa(cong_mod, du_lieu, tmp_path):
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, khoa):
        _goi(co_so, "/health", khoa="sai")
        for f in du_lieu.glob("*.log"):
            assert khoa not in f.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------- kiểm tĩnh


def _doc(*phan):
    return (GPU_NODE / Path(*phan)).read_text(encoding="utf-8")


def test_dockerfile_ghim_anh():
    for f in ["cong/Dockerfile", "nghe/Dockerfile", "tach-am/Dockerfile"]:
        noi = _doc(*f.split("/"))
        for dong in (l.strip() for l in noi.splitlines()):
            if dong.startswith("FROM "):
                anh = dong.split()[1]
                assert ":latest" not in anh, f"{f}: ảnh chưa ghim ({anh})"
                assert re.search(r":[\w.\-]+(@sha256:)?", anh), f"{f}: thiếu phiên bản ghim ({anh})"


def test_dockerfile_chay_khong_root():
    for f in ["cong/Dockerfile", "nghe/Dockerfile", "tach-am/Dockerfile"]:
        noi = _doc(*f.split("/"))
        assert re.search(r"^USER\s+\S+", noi, flags=re.M), f"{f}: thiếu USER không root"


def test_dockerfile_copy_du_mo_dun():
    # Bài học NV từ dich-vu/trinh-duyet: Dockerfile phải COPY mọi .py cục bộ mà mã nhập.
    import ast
    goc = GPU_NODE / "cong"
    cay = ast.parse((goc / "cong.py").read_text(encoding="utf-8"))
    nhap = {n.module.split(".")[0] for n in ast.walk(cay)
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    nhap |= {a.name.split(".")[0] for n in ast.walk(cay) if isinstance(n, ast.Import) for a in n.names}
    cuc_bo = {t for t in nhap if (goc / f"{t}.py").is_file()}
    dockerfile = (goc / "Dockerfile").read_text(encoding="utf-8")
    dong_copy = " ".join(l for l in dockerfile.splitlines() if l.strip().startswith("COPY "))
    thieu = sorted(t for t in cuc_bo if f"{t}.py" not in dong_copy)
    assert not thieu, f"Dockerfile cong không COPY: {thieu}"


def test_compose_khong_cong_0_0_0_0_mac_dinh():
    noi = _doc("docker-compose.yml")
    # Chỉ cấm 0.0.0.0 ở cổng CÔNG BỐ ra máy chủ; listen 0.0.0.0 nội bộ
    # container vẫn cần để ánh xạ cổng hoạt động.
    assert not re.search(r'^\s*-\s*"0\.0\.0\.0:', noi, flags=re.M), \
        "cổng công bố không được bind 0.0.0.0 mặc định"
    assert "${GPU_NODE_BIND:-127.0.0.1}" in noi


def test_compose_an_toan():
    noi = _doc("docker-compose.yml")
    assert "cap_drop:" in noi and "- ALL" in noi
    assert "no-new-privileges:true" in noi
    assert "profiles:" in noi


def test_compose_mot_lenh_duoc():
    noi = _doc("docker-compose.yml")
    for dv in ["cong", "nghe", "thi-giac", "tach-am"]:
        assert re.search(rf"^\s{{2}}{dv}:", noi, flags=re.M), f"thiếu dịch vụ {dv}"


def test_thi_giac_dung_llama_server_gguf():
    noi = _doc("docker-compose.yml")
    assert "llama-server" in noi or "llama.cpp" in noi
    assert "Qwen3-VL-2B" in noi
    assert "mmproj" in noi


# ---------------------------------------------------------------- nghe


def _nap_nghe():
    spec = importlib.util.spec_from_file_location(
        "nghe_gpu", GPU_NODE / "nghe" / "nghe.py")
    assert spec and spec.loader, "thiếu dich-vu/gpu-node/nghe/nghe.py"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nghe_gpu"] = mod
    spec.loader.exec_module(mod)
    return mod


def _doan_mau():
    return [{
        "bat_dau": 0.0, "ket_thuc": 1.2, "chu": "xin chào", "tu_tin": -0.15,
        "tu": [
            {"chu": "xin", "bat_dau": 0.0, "ket_thuc": 0.4, "xac_suat": 0.99},
            {"chu": "chào", "bat_dau": 0.4, "ket_thuc": 1.2, "xac_suat": 0.97},
        ],
    }]


def test_nghe_verbose_json_co_moc_tung_tu_va_tu_tin():
    nghe = _nap_nghe()
    ra = nghe.dinh_dang_verbose_json(_doan_mau(), "vi", 0.95, 1.2)
    assert ra["language"] == "vi"
    assert [w["word"] for w in ra["words"]] == ["xin", "chào"]
    assert ra["words"][0]["start"] == 0.0 and ra["words"][1]["end"] == 1.2
    assert ra["segments"][0]["avg_logprob"] == -0.15
    assert ra["text"] == "xin chào"


def test_nghe_tu_choi_dinh_dang_la():
    nghe = _nap_nghe()
    assert nghe not in (None,)
    # Endpoint kiểm định dạng trước khi chạm model — kiểm bằng mã nguồn.
    noi = (GPU_NODE / "nghe" / "nghe.py").read_text(encoding="utf-8")
    assert '("json", "text", "verbose_json")' in noi
    assert "HTTPException(400" in noi


def test_readme_tung_buoc_tieng_viet():
    noi = _doc("README.md")
    for cum in ["docker compose up", "khoá", "curl", "GPU_NODE_BIND", "CHƯA kiểm"]:
        assert cum in noi, f"README thiếu: {cum}"
    assert "Tailscale" in noi or "WireGuard" in noi or "Cloudflare Tunnel" in noi


# ---------------------------------------------------------------- Lỗi theo soát độc lập NV141


def test_nghe_chap_nhan_truong_file_chuan_openai(monkeypatch):
    """Lỗi 12: Client chuẩn OpenAI gửi trường 'file' thay vì 'tep'."""
    from fastapi.testclient import TestClient
    nghe = _nap_nghe()

    class MoHinhGia:
        def transcribe(self, duong, **kw):
            class Seg:
                start = 0.0
                end = 1.0
                text = "xin chao"
                avg_logprob = -0.1
                words = []
            class Info:
                language = "vi"
                language_probability = 0.98
                duration = 1.0
            return [Seg()], Info()

    monkeypatch.setattr(nghe, "_lay_mo_hinh", lambda: MoHinhGia())
    client = TestClient(nghe.app)
    files = {"file": ("test.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")}
    data = {"model": "whisper-1", "response_format": "json"}
    resp = client.post("/v1/audio/transcriptions", files=files, data=data)
    assert resp.status_code == 200
    assert resp.json() == {"text": "xin chao"}


def test_nghe_van_chap_nhan_truong_tep_cu(monkeypatch):
    """Lỗi 12: Giữ tương thích ngược cho trường 'tep' cũ."""
    from fastapi.testclient import TestClient
    nghe = _nap_nghe()

    class MoHinhGia:
        def transcribe(self, duong, **kw):
            class Seg:
                start = 0.0
                end = 1.0
                text = "tep cu"
                avg_logprob = -0.1
                words = []
            class Info:
                language = "vi"
                language_probability = 0.98
                duration = 1.0
            return [Seg()], Info()

    monkeypatch.setattr(nghe, "_lay_mo_hinh", lambda: MoHinhGia())
    client = TestClient(nghe.app)
    files = {"tep": ("test.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")}
    data = {"model": "whisper-1", "response_format": "json"}
    resp = client.post("/v1/audio/transcriptions", files=files, data=data)
    assert resp.status_code == 200
    assert resp.json() == {"text": "tep cu"}


def test_nghe_thieu_ca_hai_truong_tra_422():
    """Lỗi 12: Thiếu cả 'file' và 'tep' trả về 422."""
    from fastapi.testclient import TestClient
    nghe = _nap_nghe()
    client = TestClient(nghe.app)
    data = {"model": "whisper-1"}
    resp = client.post("/v1/audio/transcriptions", data=data)
    assert resp.status_code == 422


def test_chuyen_tiep_nghe_qua_cong_voi_truong_file(cong_mod, du_lieu, monkeypatch):
    """Lỗi 12: Client gửi multipart 'file' qua cổng cong tới dịch vụ nghe."""
    import uvicorn
    nghe = _nap_nghe()

    class MoHinhGia:
        def transcribe(self, duong, **kw):
            class Seg:
                start = 0.0
                end = 1.0
                text = "qua cong thanh cong"
                avg_logprob = -0.1
                words = []
            class Info:
                language = "vi"
                language_probability = 0.98
                duration = 1.0
            return [Seg()], Info()

    monkeypatch.setattr(nghe, "_lay_mo_hinh", lambda: MoHinhGia())
    cfg = uvicorn.Config(nghe.app, host="127.0.0.1", port=0, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.01)
    port = srv.servers[0].sockets[0].getsockname()[1]
    co_so_nghe = f"http://127.0.0.1:{port}"
    try:
        with cong_chay(cong_mod, du_lieu, {"nghe": co_so_nghe}) as (co_so_cong, khoa):
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"whisper-1\r\n"
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                f"Content-Type: audio/wav\r\n\r\n"
                f"fake-wav-content\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8")
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            }
            ma, _, than = _goi(co_so_cong, "/v1/audio/transcriptions", khoa=khoa,
                               phuong_thuc="POST", du_lieu=body, tieu_de=headers)
            assert ma == 200
            assert json.loads(than)["text"] == "qua cong thanh cong"
    finally:
        srv.should_exit = True


def test_xac_thuc_tu_choi_bearer_rong_hoac_chi_dau_cach(cong_mod, du_lieu):
    """Lỗi 13: 'Bearer ' rỗng, 'Bearer ' có dấu cách thừa hoặc không có dấu cách đều trả 401."""
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, _k):
        # Bearer rỗng
        ma, _, _ = _goi(co_so, "/health", tieu_de={"Authorization": "Bearer "})
        assert ma == 401
        # Bearer chỉ có dấu cách
        ma, _, _ = _goi(co_so, "/health", tieu_de={"Authorization": "Bearer    "})
        assert ma == 401
        # Bearer không có dấu cách
        ma, _, _ = _goi(co_so, "/health", tieu_de={"Authorization": "Bearer"})
        assert ma == 401


def test_xac_thuc_tu_choi_khoa_khong_ascii(cong_mod, du_lieu):
    """Lỗi 13: Khoá chứa ký tự không-ASCII trả 401."""
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, _k):
        ma, _, _ = _goi(co_so, "/health", tieu_de={"Authorization": "Bearer khoá-sai"})
        assert ma == 401


def test_xac_thuc_tu_choi_khi_cau_hinh_khoa_rong(cong_mod, du_lieu):
    """Lỗi 13: Nếu cấu hình máy chủ có khoá rỗng, gọi Bearer rỗng vẫn phải bị 401."""
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}, them={"khoa": ""}) as (co_so, _k):
        ma, _, _ = _goi(co_so, "/health", tieu_de={"Authorization": "Bearer "})
        assert ma == 401


def test_khoi_tao_khoa_sinh_lai_neu_tep_rong(cong_mod, tmp_path):
    """Lỗi 13: Tệp khoá rỗng có sẵn -> sinh lại khoá mới không chấp nhận khoá rỗng."""
    d = tmp_path / "data_rong"
    d.mkdir()
    tep = d / "khoa"
    tep.write_text("   \n", encoding="utf-8")
    khoa = cong_mod.khoi_tao_khoa(str(d))
    assert khoa != ""
    assert len(khoa) >= 20
    assert tep.read_text(encoding="utf-8").strip() == khoa
    assert stat.S_IMODE(tep.stat().st_mode) == 0o600


def test_khoi_tao_khoa_quyen_600_ngay_khi_tao(cong_mod, tmp_path):
    """Lỗi 13: Tệp khoá phải được tạo bằng os.open với 0o600 không có khoảng hở quyền."""
    d = tmp_path / "data_perm"
    d.mkdir()
    ma_nguon = (GPU_NODE / "cong" / "cong.py").read_text(encoding="utf-8")
    assert "os.O_CREAT" in ma_nguon
    assert "os.O_EXCL" in ma_nguon
    assert "0o600" in ma_nguon
    khoa = cong_mod.khoi_tao_khoa(str(d))
    tep = d / "khoa"
    assert stat.S_IMODE(tep.stat().st_mode) == 0o600


def test_compose_co_khoi_tao_chown_quyen_du_lieu():
    """Lỗi 14: docker-compose.yml có init service hoặc giải pháp chown thư mục cho uid 1000."""
    noi = _doc("docker-compose.yml")
    assert "khoi-tao" in noi or "chown" in noi or "init" in noi
    assert "1000" in noi


def test_hang_doi_cho_do_that_khi_co_yeu_cau_cho(cong_mod, du_lieu):
    """Lỗi 18: hang_doi.cho trong /health phản ánh số yêu cầu đang chờ slot."""
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}, them={"han_cho_giay": 5}) as (co_so, khoa):
        sem = cong_mod._semaphore_toan_cuc
        assert sem.acquire(blocking=False)
        cho_xong = threading.Event()

        def _gui_yeu_cau_cho():
            _goi(co_so, "/v1/audio/transcriptions", khoa=khoa, phuong_thuc="POST", du_lieu=b"cho")
            cho_xong.set()

        t = threading.Thread(target=_gui_yeu_cau_cho, daemon=True)
        t.start()
        time.sleep(0.1)
        try:
            ma, _, than = _goi(co_so, "/health", khoa=khoa)
            assert ma == 200
            h = json.loads(than)
            assert h["hang_doi"]["cho"] == 1
        finally:
            sem.release()
            t.join(timeout=2)


def test_chuyen_tiep_bo_authorization_va_hop_by_hop(cong_mod, du_lieu):
    """Lỗi 19: Cổng chuyển tiếp không đưa header Authorization xuống dịch vụ nội bộ."""
    class MayChuGhiHeader(BaseHTTPRequestHandler):
        cac_headers = []

        def do_POST(self):
            dai = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(dai)
            MayChuGhiHeader.cac_headers.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), MayChuGhiHeader)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    co_so_noi_bo = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with cong_chay(cong_mod, du_lieu, {"nghe": co_so_noi_bo}) as (co_so, khoa):
            tieu_de = {
                "X-Custom-Header": "gia-tri-hop-le",
                "Keep-Alive": "timeout=5",
            }
            ma, _, _ = _goi(co_so, "/v1/audio/transcriptions", khoa=khoa,
                            phuong_thuc="POST", du_lieu=b"payload", tieu_de=tieu_de)
            assert ma == 200
            assert len(MayChuGhiHeader.cac_headers) > 0
            hd = {k.lower(): v for k, v in MayChuGhiHeader.cac_headers[-1].items()}
            assert "authorization" not in hd
            assert "keep-alive" not in hd
            assert hd.get("x-custom-header") == "gia-tri-hop-le"
    finally:
        srv.shutdown()


def test_chan_content_length_khong_hop_le_hoac_am_hoac_qua_lon(cong_mod, du_lieu):
    """Lỗi 19: Chặn Content-Length âm/chữ (400) hoặc vượt trần (413)."""
    with may_chu_gia() as gia, cong_chay(cong_mod, du_lieu, {"nghe": gia}) as (co_so, khoa):
        # Content-Length âm
        ma, _, _ = _goi(co_so, "/v1/audio/transcriptions", khoa=khoa,
                        phuong_thuc="POST", du_lieu=b"data", tieu_de={"Content-Length": "-5"})
        assert ma == 400

        # Content-Length không phải số
        ma, _, _ = _goi(co_so, "/v1/audio/transcriptions", khoa=khoa,
                        phuong_thuc="POST", du_lieu=b"data", tieu_de={"Content-Length": "abc"})
        assert ma == 400

        # Content-Length quá lớn (> 100MB)
        ma, _, _ = _goi(co_so, "/v1/audio/transcriptions", khoa=khoa,
                        phuong_thuc="POST", du_lieu=b"", tieu_de={"Content-Length": str(200 * 1024 * 1024)})
        assert ma == 413

