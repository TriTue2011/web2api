"""NV183 — test bảo vệ: bộ test không được phụ thuộc thứ tự chạy.

Ba lớp ô nhiễm đã đo trên nền NEN179 (xem conftest.py, fixture
`on_dinh_trang_thai_toan_cuc`):
 (a) nạp lại module qua lõi hoán đổi bộ lọc log toàn cục (nạn nhân thật:
     test_token_khong_vao_log_qua_get của facebook — đỏ ở thứ tự thường —
     và test token của telegram — đỏ ở thứ tự đảo ngược);
 (b) `asyncio.run()` để lại «no current event loop» cho test dùng
     `asyncio.get_event_loop()` (nạn nhân thật: 10 test nv165_tien_ich);
 (c) env/cwd đặt trực tiếp rò sang test sau.

Tệp này khoá cả ba lớp ở quy mô nhỏ + một test meta chạy hai tệp mẫu
(test_nv183_mau_a rò rỉ có chủ ý, test_nv183_mau_b khẳng định sạch) theo
CẢ HAI thứ tự bằng tiến trình con.
"""
import asyncio
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

GOC = Path(__file__).resolve().parents[1]
THU_MUC_TEST = Path(__file__).resolve().parent


def _nap_module_facebook(ten_goi: str):
    """Nạp modules/facebook/module.py bằng spec — mỗi lần nạp là một lần
    `_cai_bo_loc_log()` chạy: gỡ bộ lọc của bản trước, cài bộ lọc mới."""
    duong = GOC / "modules" / "facebook" / "module.py"
    spec = importlib.util.spec_from_file_location(f"{ten_goi}.module", duong)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bo_loc_facebook_dang_gan():
    return [f for f in logging.getLogger("httpx").filters
            if getattr(f, "_facebook_loc", False)]


# LƯU Ý: không nạp module ở cấp tệp — nạp ở cấp tệp là hoán đổi bộ lọc ngay
# lúc THU THẬP, trước khi fixture kịp chụp trạng thái, và sẽ phá chính test
# nạn nhân của module đó trong bộ đầy đủ (đã vấp ở vòng đo lại đầu tiên).
# Mọi lần nạp nằm trong test: fixture khôi phục đúng trạng thái trước-test
# khi test kết thúc, ở mọi thứ tự tệp.
_TRANG_THAI_TRUOC: list = []
_DA_GHI_TRUOC = False


def test_a_nap_lai_module_hoan_doi_bo_loc():
    """Mô phỏng lớp (a): ghi lại bộ lọc đang gắn, nạp module hai lần — mỗi lần
    nạp hoán đổi bộ lọc ngay trong test. Fixture phải trả lại ĐÚNG trạng thái
    trước-test khi test kết thúc (kiểm ở test_b)."""
    global _DA_GHI_TRUOC
    _TRANG_THAI_TRUOC[:] = _bo_loc_facebook_dang_gan()
    _DA_GHI_TRUOC = True
    _nap_module_facebook("facebook_nv183_meta_goc")
    bo_loc_goc = _bo_loc_facebook_dang_gan()
    assert bo_loc_goc, "nạp module facebook phải cài đúng một bộ lọc lên httpx"
    _nap_module_facebook("facebook_nv183_meta_ban_hai")
    assert _bo_loc_facebook_dang_gan() != bo_loc_goc


def test_b_bo_loc_duoc_khoi_phuc_ve_trang_thai_truoc_test():
    """Sau test_a, bộ lọc đang gắn phải bằng đúng trạng thái trước test_a —
    tức fixture đã khôi phục (đúng đối tượng), dù test_a nạp lại module."""
    assert _DA_GHI_TRUOC, "test_a phải chạy trước và ghi lại trạng thái"
    assert _bo_loc_facebook_dang_gan() == _TRANG_THAI_TRUOC


def test_c_asyncio_run_de_lai_khong_con_loop():
    """Mô phỏng lớp (b): asyncio.run() kết thúc bằng set_event_loop(None)."""
    asyncio.run(asyncio.sleep(0))


def test_d_get_event_loop_van_dung_duoc():
    """Test sau vẫn phải lấy được event loop (nạn nhân thật: nv165)."""
    loop = asyncio.get_event_loop()
    assert loop.run_until_complete(asyncio.sleep(0, result=42)) == 42


def test_meta_hai_tep_mau_ca_hai_thu_tu_deu_xanh():
    """Chạy test_nv183_mau_a + test_nv183_mau_b bằng tiến trình con theo cả
    hai thứ tự: mẫu A rò env + filter, mẫu B khẳng định sạch. Không có
    fixture khôi phục thì thứ tự [A, B] đỏ ở mẫu B."""
    mau_a = str(THU_MUC_TEST / "test_nv183_mau_a.py")
    mau_b = str(THU_MUC_TEST / "test_nv183_mau_b.py")
    for thu_tu in ([mau_a, mau_b], [mau_b, mau_a]):
        kq = subprocess.run(
            [sys.executable, "-m", "pytest", *thu_tu, "-q",
             "-p", "no:cacheprovider", "--tb=short"],
            cwd=GOC, capture_output=True, text=True, timeout=600,
        )
        assert kq.returncode == 0, (
            f"thứ tự {[Path(p).name for p in thu_tu]} đỏ:\n"
            f"{kq.stdout[-2000:]}\n{kq.stderr[-500:]}")
