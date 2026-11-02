"""Test M2 cho NV279: Hàm thuần tính tốc độ Mbit/s và xử lý thời lượng RouterOS."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "mikrotik-routeros"

# Nạp toc_do.py theo đường dẫn, KHÔNG thêm thư mục module vào sys.path
# (nếu thêm, test khác `import module` sẽ nhầm sang module.py của mikrotik-routeros).
_spec = importlib.util.spec_from_file_location("nv279_mikrotik_toc_do", THU_MUC_MODULE / "toc_do.py")
_toc_do = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_toc_do)
tinh_toc_do_mbit = _toc_do.tinh_toc_do_mbit
doc_thoi_luong_giay = _toc_do.doc_thoi_luong_giay
TRAN_VAT_LY = _toc_do.TRAN_VAT_LY


def test_tinh_toc_do_mau_dau_va_dt_nho():
    # Mẫu đầu (truoc=None) -> None
    assert tinh_toc_do_mbit(None, (1_000, 10.0, 100)) is None

    # dt < 1s -> None
    truoc = (1_000, 10.0, 100)
    nay = (2_000, 10.5, 100)
    assert tinh_toc_do_mbit(truoc, nay) is None

    # dt = 0 -> None
    nay_dt0 = (2_000, 10.0, 100)
    assert tinh_toc_do_mbit(truoc, nay_dt0) is None


def test_tinh_toc_do_binh_thuong():
    # 1 000 000 byte trong 8 s -> 1.0 Mbit/s
    truoc = (0, 0.0, 10)
    nay = (1_000_000, 8.0, 18)
    assert tinh_toc_do_mbit(truoc, nay) == 1.0

    # Bằng nhau -> 0.0
    nay_bang = (0, 8.0, 18)
    assert tinh_toc_do_mbit(truoc, nay_bang) == 0.0


def test_tinh_toc_do_tran_bo_dem_32_bit():
    # Tràn 32-bit: truoc=4_294_967_000, nay=1_000, dt=1 s
    # delta = (2**32 - 4_294_967_000) + 1_000 = 296 + 1_000 = 1_296 byte
    # 1_296 * 8 / 1e6 = 0.010368 -> round 3 chữ số: 0.01
    truoc = (4_294_967_000, 10.0, 100)
    nay = (1_000, 11.0, 101)
    assert tinh_toc_do_mbit(truoc, nay, bit=32) == 0.01


def test_tinh_toc_do_router_khoi_dong_lai():
    # Uptime giảm (so với mẫu trước) -> None
    truoc = (10_000_000, 100.0, 500)
    nay = (500, 110.0, 10)  # uptime từ 500 giảm về 10
    assert tinh_toc_do_mbit(truoc, nay) is None


def test_tinh_toc_do_giam_qua_tran_vat_ly():
    # Bộ đếm giảm 10**12 -> 500 không có dấu hiệu tràn hợp lý
    truoc = (10**12, 100.0, 1000)
    nay = (500, 105.0, 1005)
    # Giả thiết tràn 64-bit: delta ~ 2**64 -> vận tốc suy ra vượt xa 400_000 Mbit/s
    assert tinh_toc_do_mbit(truoc, nay, bit=64) is None


def test_chuoi_3_mau_qua_khoi_dong_lai():
    # Kịch bản chuỗi: baseline -> tính được -> reset router (trả None) -> tính được tiếp
    m1 = (1_000_000, 10.0, 100)
    m2 = (2_000_000, 18.0, 108)  # dt=8s, delta=1_000_000 -> 1.0 Mbit/s
    assert tinh_toc_do_mbit(m1, m2) == 1.0

    # Router khởi động lại: uptime về 2, byte về 50_000
    m3 = (50_000, 20.0, 2)
    assert tinh_toc_do_mbit(m2, m3) is None

    # Lượt tiếp theo lấy m3 làm mẫu gốc
    m4 = (1_050_000, 28.0, 10)  # dt=8s, delta=1_000_000 -> 1.0 Mbit/s
    assert tinh_toc_do_mbit(m3, m4) == 1.0


def test_dau_vao_khong_hop_le():
    assert tinh_toc_do_mbit("khong-phai-so", (1000, 10.0)) is None
    assert tinh_toc_do_mbit((-5, 10.0), (1000, 15.0)) is None
    assert tinh_toc_do_mbit((100, 10.0), (True, 15.0)) is None


@pytest.mark.parametrize("xau,ket_qua", [
    ("1w2d3h4m5s", 1 * 7 * 86400 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5),
    ("45s", 45),
    ("2d3h", 2 * 86400 + 3 * 3600),
    ("10m", 600),
    ("500ms", 1),
    ("never", None),
    ("none", None),
    ("", None),
    ("   ", None),
    (None, None),
    (12345, None),
    ("sai_khuon_hoan_toan", None),
    ("03:14:25", 3 * 3600 + 14 * 60 + 25),
    ("2d03:14:25", 2 * 86400 + 3 * 3600 + 14 * 60 + 25),
])
def test_doc_thoi_luong_giay(xau, ket_qua):
    assert doc_thoi_luong_giay(xau) == ket_qua
