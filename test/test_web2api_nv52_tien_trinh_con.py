"""NV52 — TEST TRƯỚC cho việc chạy module hạng B trong tiến trình con có ngữ cảnh hạn chế.

Bối cảnh: báo cáo NV48 mục 4 bước 4 chốt hướng cô lập module hạng B — chạy trong
tiến trình con riêng với: biến môi trường đã lọc (không có khoá, không có biến của
lõi), thư mục dữ liệu riêng của module, mạng ra theo danh sách cho phép, và không
được import các gói `app.*`. Trên main hiện tại CHƯA có bất kỳ cơ chế nào trong
đó (grep `tien_trinh_con|chay_module_cach_ly` trong app/ → 0; `subprocess` duy
nhất ở app/he_thong.py là chạy lệnh hệ thống, không liên quan module). Vì vậy mọi
test hành vi tiến trình con trong tệp này đều đánh dấu xfail(strict=True): đỏ bây
giờ, xanh khi mã được viết.

HỢP ĐỒNG ĐỀ XUẤT (mã tương lai phải thoả mãn để các test dưới xanh):

    app/tien_trinh_con.chay_module_cach_ly(thu_muc_module, mang_ra=None,
                                          thoi_gian_cho=30.0,
                                          bien_moi_truong=None) -> dict

- `thu_muc_module`: thư mục chứa manifest.json + module.py của module hạng B.
- `mang_ra`: allowlist host cho mạng ra (lấy từ manifest `tai_nguyen.mang_ra`).
- `thoi_gian_cho`: giây; quá hạn → tiến trình con bị dừng, lõi không treo.
- `bien_moi_truong`: biến môi trường bổ sung cho tiến trình con.

Tiến trình con chạy một «runner» exec module.py với ngữ cảnh hạn chế; runner cung
cấp cho module hàm `bao_cao(d: dict)` để gửi kết quả về (runner in JSON ra
stdout). Hàm trả về:

    {"trang_thai": "ok" | "het_gio" | "chet",
     "ma_thoat": int | None,
     "ket_qua": dict,      # những gì module đã bao_cao()
     "loi": str | None}    # mô tả lỗi rõ ràng khi bị chặn/đổ vỡ

Ràng buộc ngữ cảnh hạn chế (đây chính là điều các test kiểm):
1. Biến môi trường của tiến trình con ĐÃ LỌC: không chứa khoá (mọi biến tên chứa
   KEY/TOKEN/SECRET/PASSWORD, không phân biệt hoa thường) và không chứa biến của
   lõi (mọi biến bắt đầu bằng `WEB2API_`, `CHATGPT2API_`).
2. Tiến trình con không đọc được tệp trong `<WEB2API_DATA>/bi_mat/`, không
   đọc/ghi được tệp NGOÀI thư mục module (chỉ được đọc/ghi trong thư mục của nó).
3. Socket tới host không nằm trong `mang_ra` → bị chặn NGAY với lỗi rõ ràng
   (không treo, không chờ timeout mạng).
4. Tiến trình con chết (kể cả os._exit) → lõi vẫn sống; `trang_thai="chet"`,
   `ma_thoat` ghi đúng. Treo quá `thoi_gian_cho` → bị dừng, `trang_thai="het_gio"`.
5. Không import được bất kỳ gói `app.*` nào trong tiến trình con.
6. Module hạng A KHÔNG bị ép vào tiến trình con — vẫn nạp và chạy trong tiến
   trình lõi như cũ (test phòng thủ xanh).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from app import kham_pha

try:
    from app.tien_trinh_con import chay_module_cach_ly
except ImportError:  # chưa có trên main → các test hành vi dưới đỏ đúng chỗ
    chay_module_cach_ly = None

LY_DO_XFAIL = "NV52: chưa có app.tien_trinh_con.chay_module_cach_ly trên main"
XF = pytest.mark.xfail(strict=True, reason=LY_DO_XFAIL)

MAU_MODULE = """\
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


def _viet_module_gia(tmp_path: Path, id_: str, payload: str) -> Path:
    d = tmp_path / id_
    d.mkdir()
    manifest = {"id": id_, "ten": f"Module gia NV52 {id_}", "mo_ta": "gia", "loai": "ai", "hang": "A",
                "giao_thuc": "local", "tien_to": "nv52gia", "truong_cau_hinh": [],
                "tai_nguyen": {"mang_ra": []}}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "module.py").write_text(MAU_MODULE.format(PAYLOAD=payload), encoding="utf-8")
    return d


def _chay(d: Path, **kw):
    """Gọi hợp đồng tương lai; đỏ ngay nếu API chưa tồn tại."""
    assert chay_module_cach_ly is not None, LY_DO_XFAIL
    return chay_module_cach_ly(d, **kw)


# ───────────────────────── 1. Biến môi trường đã lọc ─────────────────────────

@XF
def test_nv52_env_khong_thay_khoa_quan_tri(tmp_path, monkeypatch):
    """Khoá quản trị của lõi (WEB2API_ADMIN_KEY) không lọt vào tiến trình con."""
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "sk-test-quan-tri-nv52-000")
    d = _viet_module_gia(tmp_path, "nv52-env1",
                         'import os\nbao_cao({"khoa": os.environ.get("WEB2API_ADMIN_KEY", "")})')
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert kq["ket_qua"]["khoa"] == ""


@XF
def test_nv52_env_khong_thay_bien_cua_loi(tmp_path, monkeypatch):
    """Biến của lõi (WEB2API_DATA, CHATGPT2API_AUTH_KEY) không lọt vào tiến trình con."""
    monkeypatch.setenv("WEB2API_DATA", "/duong-dan-loi-gia-nv52")
    monkeypatch.setenv("CHATGPT2API_AUTH_KEY", "test-auth-gia-nv52")
    d = _viet_module_gia(tmp_path, "nv52-env2",
                         'import os\nbao_cao({"data": os.environ.get("WEB2API_DATA", ""),'
                         ' "auth": os.environ.get("CHATGPT2API_AUTH_KEY", "")})')
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert kq["ket_qua"]["data"] == ""
    assert kq["ket_qua"]["auth"] == ""


# ─────────────── 2. Không đọc/ghi ngoài thư mục module ───────────────

@XF
def test_nv52_khong_doc_duoc_thu_muc_bi_mat(tmp_path):
    """Module hạng B không đọc được tệp trong <WEB2API_DATA>/bi_mat/."""
    data = tmp_path / "data"
    (data / "bi_mat").mkdir(parents=True)
    (data / "bi_mat" / "khoa_gia.txt").write_text("sk-test-bi-mat-nv52-000", encoding="utf-8")
    duong = data / "bi_mat" / "khoa_gia.txt"
    payload = (
        "import pathlib\n"
        f"_p = pathlib.Path({str(duong)!r})\n"
        "try:\n"
        '    _nd = _p.read_text(encoding="utf-8")\n'
        "except OSError as _e:\n"
        '    _nd = "KHONG_DOC_DUOC:" + type(_e).__name__\n'
        'bao_cao({"bi_mat": _nd})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-file1", payload)
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert str(kq["ket_qua"]["bi_mat"]).startswith("KHONG_DOC_DUOC")


@XF
def test_nv52_khong_doc_duoc_ngoai_thu_muc(tmp_path):
    """Module hạng B không đọc được tệp ngoài thư mục của nó."""
    ngoai = tmp_path / "ngoai.txt"
    ngoai.write_text("du-lieu-ngoai-nv52", encoding="utf-8")
    payload = (
        "import pathlib\n"
        f"_p = pathlib.Path({str(ngoai)!r})\n"
        "try:\n"
        '    _nd = _p.read_text(encoding="utf-8")\n'
        "except OSError as _e:\n"
        '    _nd = "KHONG_DOC_DUOC:" + type(_e).__name__\n'
        'bao_cao({"ngoai": _nd})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-file2", payload)
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert str(kq["ket_qua"]["ngoai"]).startswith("KHONG_DOC_DUOC")


@XF
def test_nv52_khong_ghi_duoc_ngoai_thu_muc(tmp_path):
    """Module hạng B không ghi được tệp ra ngoài thư mục của nó."""
    dich = tmp_path / "bi-ghi-boi-module-nv52.txt"
    payload = (
        f"_dich = {str(dich)!r}\n"
        "try:\n"
        '    open(_dich, "w", encoding="utf-8").write("x")\n'
        '    _kq = "GHI_DUOC"\n'
        "except OSError as _e:\n"
        '    _kq = "KHONG_GHI_DUOC:" + type(_e).__name__\n'
        'bao_cao({"ghi_ngoai": _kq})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-file3", payload)
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert not dich.exists()
    assert str(kq["ket_qua"]["ghi_ngoai"]).startswith("KHONG_GHI_DUOC")


# ─────────────────── 3. Mạng ra theo danh sách cho phép ───────────────────

def _may_chu_cuc_bo():
    """Máy chủ TCP loopback cho test mạng; trả (socket, cổng)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


@XF
def test_nv52_socket_ngoai_allowlist_bi_chan_ngay(tmp_path):
    """Kết nối tới host không trong allowlist → bị chặn NGAY, lỗi rõ ràng, không treo."""
    srv, cong = _may_chu_cuc_bo()
    nhan = []

    def _phuc_vu():
        try:
            ket, _ = srv.accept()
            nhan.append(ket.recv(16))
            ket.close()
        finally:
            srv.close()

    t = threading.Thread(target=_phuc_vu, daemon=True)
    t.start()
    payload = (
        "import socket, time\n"
        "_bd = time.monotonic()\n"
        "try:\n"
        f'    _s = socket.create_connection(("127.0.0.1", {cong}), timeout=5)\n'
        '    _s.sendall(b"ping")\n'
        '    _s.close()\n'
        '    _kq = "KET_NOI_DUOC"\n'
        "except OSError as _e:\n"
        '    _kq = "BI_CHAN:" + type(_e).__name__\n'
        '_het = round(time.monotonic() - _bd, 2)\n'
        'bao_cao({"mang": _kq, "het_giay": _het})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-net1", payload)
    bat_dau = time.monotonic()
    kq = _chay(d, mang_ra=[], thoi_gian_cho=15)
    tong = time.monotonic() - bat_dau
    t.join(timeout=10)
    assert kq["trang_thai"] == "ok"
    # Bị chặn: lõi không nhận được gì; lỗi rõ ràng và nhanh (không treo tới hết giờ).
    assert nhan != [b"ping"]
    assert str(kq["ket_qua"]["mang"]).startswith("BI_CHAN")
    assert kq["ket_qua"]["het_giay"] < 10
    assert tong < 15


@XF
def test_nv52_socket_trong_allowlist_van_di_duoc(tmp_path):
    """Host CÓ trong allowlist vẫn kết nối được — allowlist là cho phép, không phải cấm hết."""
    srv, cong = _may_chu_cuc_bo()
    nhan = []

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
    payload = (
        "import socket\n"
        "try:\n"
        f'    _s = socket.create_connection(("127.0.0.1", {cong}), timeout=5)\n'
        '    _s.sendall(b"ping")\n'
        '    _tl = _s.recv(16)\n'
        '    _s.close()\n'
        '    _kq = "KET_NOI_DUOC:" + _tl.decode("utf-8", "replace")\n'
        "except OSError as _e:\n"
        '    _kq = "BI_CHAN:" + type(_e).__name__\n'
        'bao_cao({"mang": _kq})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-net2", payload)
    kq = _chay(d, mang_ra=["127.0.0.1"], thoi_gian_cho=15)
    t.join(timeout=10)
    assert kq["trang_thai"] == "ok"
    assert kq["ket_qua"]["mang"] == "KET_NOI_DUOC:pong"
    assert nhan == [b"ping"]


# ─────────────── 4. Tiến trình con chết/treo, lõi vẫn sống ───────────────

@XF
def test_nv52_tien_trinh_con_chet_loi_van_song(tmp_path):
    """Tiến trình con tự kết thúc (os._exit) → lõi vẫn sống, báo trạng thái + mã thoát."""
    d = _viet_module_gia(tmp_path, "nv52-song1", "import os\nos._exit(3)\n")
    kq = _chay(d, thoi_gian_cho=10)
    # Test này chạy tới đây được nghĩa là lõi (tiến trình pytest) vẫn sống.
    assert kq["trang_thai"] == "chet"
    assert kq["ma_thoat"] == 3


@XF
def test_nv52_tien_trinh_con_treo_bi_dung_dung_gio(tmp_path):
    """Tiến trình con treo quá thoi_gian_cho → bị dừng, lõi không treo theo."""
    d = _viet_module_gia(tmp_path, "nv52-song2", "import time\ntime.sleep(600)\n")
    bat_dau = time.monotonic()
    kq = _chay(d, thoi_gian_cho=3)
    tong = time.monotonic() - bat_dau
    assert kq["trang_thai"] == "het_gio"
    assert tong < 20  # dừng đúng hẹn, không chờ hết 600 giây


# ─────────────────── 5. Không import được gói app.* ───────────────────

@XF
def test_nv52_khong_import_duoc_goi_app(tmp_path, monkeypatch):
    """Trong tiến trình con, `import app.*` bị chặn — module không với tới hàm lõi."""
    monkeypatch.setenv("WEB2API_ADMIN_KEY", "sk-test-quan-tri-nv52-000")
    payload = (
        "try:\n"
        "    import app.xac_thuc as _xt\n"
        '    _kq = "NHAP_DUOC:" + str(_xt.khoa_quan_tri())\n'
        "except Exception as _e:\n"
        '    _kq = "BI_CHAN:" + type(_e).__name__\n'
        'bao_cao({"nhap_app": _kq})\n'
    )
    d = _viet_module_gia(tmp_path, "nv52-imp1", payload)
    kq = _chay(d, thoi_gian_cho=10)
    assert kq["trang_thai"] == "ok"
    assert str(kq["ket_qua"]["nhap_app"]).startswith("BI_CHAN")


# ─────────────── Phòng thủ: hạng A vẫn chạy trong lõi ───────────────

def test_nv52_hang_a_van_nap_va_chay_trong_tien_trinh_loi(tmp_path):
    """Hạng A KHÔNG bị ép vào tiến trình con: nap_ma vẫn exec trong tiến trình lõi như cũ."""
    pid_loi = os.getpid()
    dau_ra = tmp_path / "pid.txt"
    d = tmp_path / "nv52-hanga"
    d.mkdir()
    manifest = {"id": "nv52-hanga", "ten": "Hang A NV52", "mo_ta": "gia", "loai": "ai", "hang": "A",
                "giao_thuc": "local", "tien_to": "nv52a", "truong_cau_hinh": [],
                "tai_nguyen": {}}
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "module.py").write_text(
        "import os\n"
        f"open({str(dau_ra)!r}, 'w', encoding='utf-8').write(str(os.getpid()))\n"
        "class Module:\n"
        "    def __init__(self, manifest):\n"
        "        self.manifest = manifest\n"
        "    def nap(self, cau_hinh):\n"
        "        return None\n"
        "    async def cai(self, cau_hinh):\n"
        "        return {'ok': True}\n"
        "    async def suc_khoe(self):\n"
        "        return {'ok': True}\n"
        "    async def dung(self):\n"
        "        return None\n", encoding="utf-8")
    try:
        kham_pha.nap_ma(d, manifest, "vtnv52" * 4)
    finally:
        kham_pha.go_ma(kham_pha.ten_ma(manifest["id"], "vtnv52" * 4))
    # Mã hạng A chạy NGAY trong tiến trình lõi → PID trùng tiến trình pytest.
    assert dau_ra.read_text(encoding="utf-8") == str(pid_loi)
