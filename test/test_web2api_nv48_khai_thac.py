"""NV48 — kiểm chứng bằng chạy thật: module (mã Python chạy trong tiến trình lõi) có thể làm gì.

Mỗi kịch bản dựng một module GIẢ trong thư mục tạm rồi nạp bằng đúng đường lõi dùng lúc
quét (`app.kham_pha.nap_ma` — biên dịch + exec module.py, rồi tạo thử `Module(manifest)`).
Mã độc nằm ở CẤP CAO NHẤT của module.py nên chạy ngay khi lõi quét thư mục module —
trước mọi bước cài/duyệt.

Khai thác được ⇒ test xfail(strict=True) kèm lý do (bảng «module làm được» trong báo cáo).
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from app import kham_pha

XFAIL_LY_DO = "mã module chạy trong tiến trình lõi, không có cô lập — xem nv48-bao-cao.md"

MAU_MODULE = """\
import os

_DAU_RA = os.environ.get("NV48_DAU_RA", "")

def _ghi(ten, noi_dung):
    try:
        with open(os.path.join(_DAU_RA, ten), "w", encoding="utf-8") as f:
            f.write(str(noi_dung))
    except OSError:
        pass

{PAYLOAD}

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
    def nap(self, cau_hinh):
        return None
    async def cai(self, cau_hinh):
        return {"ok": True}
    async def suc_khoe(self):
        return {"ok": True}
    async def dung(self):
        return None
"""


def _viet_module_gia(tmp_path: Path, id_: str, payload: str):
    d = tmp_path / id_
    d.mkdir()
    manifest = {"id": id_, "ten": f"Module gia NV48 {id_}", "mo_ta": "gia", "loai": "ai",
                "giao_thuc": "local", "tien_to": "nv48gia", "truong_cau_hinh": [], "tai_nguyen": {}}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "module.py").write_text(MAU_MODULE.format(PAYLOAD=payload), encoding="utf-8")
    return d, manifest


def _nap(d: Path, manifest: dict):
    """Nạp như lõi lúc quét; trả tên mô-đun để dọn."""
    lop, ten = kham_pha.nap_ma(d, manifest, "vtnv48" * 4)
    return lop, ten


@pytest.fixture()
def dau_ra(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("NV48_DAU_RA", str(out))
    return out


# ───────────────────────────── kịch bản khai thác ─────────────────────────────

@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_doc_bien_moi_truong(tmp_path, monkeypatch, dau_ra):
    """Module đọc được biến môi trường của tiến trình lõi (vd WEB2API_ADMIN_KEY)."""
    monkeypatch.setenv("NV48_ADMIN_KEY", "khoa-gia-nv48-abc123")
    payload = '_ghi("bien_moi_truong.txt", os.environ.get("NV48_ADMIN_KEY", ""))'
    d, manifest = _viet_module_gia(tmp_path, "nv48-env", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    # Phòng thủ đúng: module không được thấy khoá → tệp không tồn tại hoặc rỗng.
    p = dau_ra / "bien_moi_truong.txt"
    assert not p.exists() or p.read_text(encoding="utf-8") == ""


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_doc_tep_bi_mat(tmp_path, monkeypatch, dau_ra):
    """Module đọc được tệp trong <WEB2API_DATA>/bi_mat/ (nơi khoá API các module khác)."""
    data = tmp_path / "data"
    (data / "bi_mat").mkdir(parents=True)
    (data / "bi_mat" / "khoa_gia.txt").write_text("sk-gia-bi-mat-nv48", encoding="utf-8")
    monkeypatch.setenv("WEB2API_DATA", str(data))
    payload = (
        'import pathlib\n'
        '_p = pathlib.Path(os.environ["WEB2API_DATA"]) / "bi_mat" / "khoa_gia.txt"\n'
        '_ghi("tep_bi_mat.txt", _p.read_text(encoding="utf-8") if _p.exists() else "")'
    )
    d, manifest = _viet_module_gia(tmp_path, "nv48-secret", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    p = dau_ra / "tep_bi_mat.txt"
    assert not p.exists() or p.read_text(encoding="utf-8") == ""


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_doc_ngoai_thu_muc(tmp_path, monkeypatch, dau_ra):
    """Module đọc được tệp NGOÀI thư mục của nó."""
    ngoai = tmp_path / "ngoai.txt"
    ngoai.write_text("du-lieu-ngoai-nv48", encoding="utf-8")
    monkeypatch.setenv("NV48_NGOAI", str(ngoai))
    payload = '_ghi("doc_ngoai.txt", open(os.environ["NV48_NGOAI"], encoding="utf-8").read())'
    d, manifest = _viet_module_gia(tmp_path, "nv48-read", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    p = dau_ra / "doc_ngoai.txt"
    assert not p.exists() or p.read_text(encoding="utf-8") == ""


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_ghi_ngoai_thu_muc(tmp_path, monkeypatch, dau_ra):
    """Module ghi được tệp RA NGOÀI thư mục của nó."""
    dich = dau_ra / "ghi_ngoai.txt"
    payload = f'_ghi("xong.txt", "ok")\nopen({str(dich)!r}, "w", encoding="utf-8").write("bi-ghi-boi-module")'
    d, manifest = _viet_module_gia(tmp_path, "nv48-write", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    # Phòng thủ đúng: module không ghi được ra ngoài → tệp không tồn tại.
    assert not dich.exists()


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_mo_ket_noi_mang_tho(tmp_path, monkeypatch, dau_ra):
    """Module mở được kết nối mạng bằng socket thô — `tai_nguyen.mang_ra` chỉ là khai báo,
    không có đường mã nào của lõi chặn socket của module."""
    nhan = []
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cong = srv.getsockname()[1]

    def _phuc_vu():
        try:
            ket, _ = srv.accept()
            nhan.append(ket.recv(16))
            ket.sendall(b"pong")
            ket.close()
        finally:
            srv.close()

    t = threading.Thread(target=_phuc_vu, daemon=True)
    t.start()
    monkeypatch.setenv("NV48_CONG", str(cong))
    payload = (
        'import socket\n'
        '_s = socket.create_connection(("127.0.0.1", int(os.environ["NV48_CONG"])), timeout=5)\n'
        '_s.sendall(b"ping")\n'
        '_ghi("mang_tho.txt", _s.recv(16).decode("utf-8", "replace"))\n'
        '_s.close()'
    )
    d, manifest = _viet_module_gia(tmp_path, "nv48-net", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    t.join(timeout=10)
    # Phòng thủ đúng: lõi chặn kết nối của module → không nhận được gì.
    assert nhan != [b"ping"]


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_chay_tien_trinh_con(tmp_path, monkeypatch, dau_ra):
    """Module chạy được tiến trình con (subprocess)."""
    payload = (
        'import subprocess, sys\n'
        '_r = subprocess.run([sys.executable, "-c", "print(\\"tien-trinh-con-chay\\")"],\n'
        '                    capture_output=True, text=True, timeout=20)\n'
        '_ghi("tien_trinh_con.txt", _r.stdout.strip())'
    )
    d, manifest = _viet_module_gia(tmp_path, "nv48-subp", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    p = dau_ra / "tien_trinh_con.txt"
    assert not p.exists() or p.read_text(encoding="utf-8") != "tien-trinh-con-chay"


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_nhap_mo_dun_loi_doc_khoa(tmp_path, monkeypatch, dau_ra):
    """Module import được `app.*` và GỌI hàm lõi — ở đây đọc khoá quản trị thật qua
    app.xac_thuc.khoa_quan_tri() (module thật trong kho cũng `from app.chuyen_tiep import …`)."""
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "khoa-quan-tri-gia-nv48")
    payload = (
        'import importlib\n'
        '_xt = importlib.import_module("app.xac_thuc")\n'
        '_ghi("khoa_loi.txt", _xt.khoa_quan_tri())'
    )
    d, manifest = _viet_module_gia(tmp_path, "nv48-core", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    p = dau_ra / "khoa_loi.txt"
    # Phòng thủ đúng: module không gọi được hàm lõi / không đọc được khoá.
    assert not p.exists() or p.read_text(encoding="utf-8") == ""


@pytest.mark.xfail(strict=True, reason=XFAIL_LY_DO)
def test_nv48_ma_chay_ngay_khi_quet(tmp_path, monkeypatch, dau_ra):
    """Mã ở cấp cao nhất của module.py CHẠY NGAY khi lõi quét (nap_ma), không đợi bước
    cài hay duyệt quyền — đặt thư mục vào <WEB2API_DATA>/modules/ là đủ để mã chạy ở
    lần quét kế (khởi động hoặc nút «nạp lại»)."""
    payload = '_ghi("quet_ngay.txt", "ma-chay-luc-quet")'
    d, manifest = _viet_module_gia(tmp_path, "nv48-scan", payload)
    try:
        _nap(d, manifest)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv48" * 4))
    p = dau_ra / "quet_ngay.txt"
    assert not p.exists()
