# test_c2a_trinh_duyet_that.py — NV206: test CHẠY TRÌNH DUYỆT THẬT cho dich-vu/may-dang-nhap.
#
# Chạy TRONG container (trình duyệt thật đã dựng trong ảnh):
#   docker exec -i web2api-may-dang-nhap python - < dich-vu/may-dang-nhap/test_c2a_trinh_duyet_that.py
# Chạy bằng pytest trên máy dev (tự skip khi môi trường không có trình duyệt):
#   cd dich-vu/may-dang-nhap && python -m pytest test_c2a_trinh_duyet_that.py -x -q
from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# ── Nạp src cho cả hai chế độ: pytest tại thư mục dịch vụ, hoặc
# `docker exec -i ... python - < file` (khi đó __file__ không tồn tại, src ở /app/src).
try:
    import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả khi chạy pytest trên máy)
    # Test này kiểm đường Firefox (cái NV206 sửa) — pin rõ, không phụ thuộc mặc định.
    os.environ.setdefault("CAPTCHA_SOLVER_BROWSER", "firefox")
except ImportError:
    _APP = "/app"
    if os.path.isdir(os.path.join(_APP, "src")) and _APP not in sys.path:
        sys.path.insert(0, _APP)
    os.environ.setdefault("CAPTCHA_SOLVER_DATA_DIR", "/tmp/nv206-test-data")
    os.environ.setdefault("CAPTCHA_SOLVER_BROWSER", "firefox")

from src.browser_pool import (  # noqa: E402
    _chon_runtime,
    kiem_tra_trinh_duyet,
    pool,
    trinh_duyet_san_sang,
)


# ── Bảng đo NV206 (không đoán): firefox PHẢI chạy bằng playwright chính gốc.
def test_chon_runtime_firefox_dung_playwright_goc():
    """Firefox không được lái bằng patchright (patchright chỉ vá cho Chromium/CDP;
    driver của nó làm hỏng mọi thao tác trang trên Firefox — đo thật NV206)."""
    assert _chon_runtime("firefox") == "playwright-goc"
    assert _chon_runtime("chromium") in ("cloakbrowser", "patchright")


def test_cau_hinh_sai_bi_tu_choi_tieng_viet():
    """Cấu hình trình duyệt không hợp lệ → từ chối ngay bằng tiếng Việt,
    không khởi động bình thường rồi hỏng giữa chừng."""
    with pytest.raises(RuntimeError, match="[Tt]rình duyệt"):
        kiem_tra_trinh_duyet("safari")
    with pytest.raises(RuntimeError, match="[Tt]rình duyệt"):
        kiem_tra_trinh_duyet("")


# ── Trang tĩnh phục vụ từ 127.0.0.1 cho test trình duyệt thật.
_TRANG = (
    "<!doctype html><html><head><title>trang-do-nv206</title></head><body>"
    '<h1 id="tieu_de">Xin chao NV206</h1>'
    '<button id="nut" onclick="document.getElementById(\'ket_qua\').textContent=\'DA-NHAN\'">Bam toi</button>'
    '<span id="ket_qua">chua</span>'
    '<script>document.cookie="phien_do_nv206=abc123; path=/";</script>'
    "</body></html>"
).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_TRANG)))
        self.end_headers()
        self.wfile.write(_TRANG)

    def log_message(self, *args):  # noqa: D102
        pass


def test_trinh_duyet_that_goto_evaluate_click_cookie():
    """Test CHẠY TRÌNH DUYỆT THẬT (không giả): mở hồ sơ bền qua pool.page(),
    goto + evaluate + click + đọc cookie trên trang tĩnh 127.0.0.1.
    Tự skip (ghi rõ lý do) khi môi trường không có trình duyệt."""
    import src.browser_pool as bp

    san_sang, ly_do = trinh_duyet_san_sang()
    if not san_sang:
        pytest.skip(f"môi trường không có trình duyệt thật: {ly_do}")

    async def _chay():
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        cong = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        goc = f"http://127.0.0.1:{cong}/"
        async with bp.pool.page(profile="nv206-do", headless=True) as page:
            await page.goto(goc, timeout=30000)
            assert await page.evaluate("document.title") == "trang-do-nv206"
            assert await page.evaluate(
                "document.getElementById('tieu_de').textContent"
            ) == "Xin chao NV206"
            await page.click("#nut", timeout=15000)
            await page.wait_for_function(
                "document.getElementById('ket_qua').textContent === 'DA-NHAN'",
                timeout=10000,
            )
            cookies = await bp.pool.get_cached("nv206-do").cookies(goc)
            ten = {c["name"] for c in cookies}
            assert "phien_do_nv206" in ten, f"thiếu cookie, nhận: {sorted(ten)}"
        srv.shutdown()
        return True

    assert asyncio.run(_chay()) is True


def test_bien_the_dung_anh_xa_sang_cau_hinh_chay():
    """NV206 — lỗi gốc thứ hai: BIEN_THE (build-arg) phải được ánh xạ sang
    CAPTCHA_SOLVER_BROWSER lúc chạy, nếu không ảnh firefox chạy với cấu hình
    chromium mà không có nhị phân (dịch vụ hỏng ngay từ đầu)."""
    goc = Path(__file__).resolve().parent
    df = (goc / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV BIEN_THE_ANH=${BIEN_THE}" in df, "Dockerfile phải ghi biến thể đã dựng vào ảnh"
    ep = (goc / "entrypoint.sh").read_text(encoding="utf-8")
    assert "CAPTCHA_SOLVER_BROWSER" in ep, "entrypoint.sh phải đặt CAPTCHA_SOLVER_BROWSER"
    assert "BIEN_THE_ANH" in ep, "entrypoint.sh phải ánh xạ từ BIEN_THE_ANH"
    comp = (goc / "docker-compose.yml").read_text(encoding="utf-8")
    bien_the = re.search(r"^\s*BIEN_THE:\s*(\S+)", comp, re.M).group(1)
    browser = re.search(r'^\s*CAPTCHA_SOLVER_BROWSER:\s*"?(\S+)"?', comp, re.M).group(1)
    assert bien_the.strip('"') == browser.strip('"'), (
        "compose: BIEN_THE=" + bien_the + " phải khớp CAPTCHA_SOLVER_BROWSER=" + browser
    )


def test_cau_hinh_khong_dung_duoc_pool_tu_choi_khoi_dong():
    """Điều kiện chung NV206: pool.start() với cấu hình trình duyệt không dùng
    được phải ném RuntimeError tiếng Việt, không khởi động bình thường.
    (Kiểm HÀNH VI qua pool.start(), không chỉ gọi hàm kiểm trực tiếp.)"""
    # Giả lập firefox nhưng thiếu playwright chính gốc → phải từ chối.
    import src.browser_pool as bp

    da_luu = dict(sys.modules)
    try:
        # Ép playwright thành "không có" để kiểm nhánh fail-fast.
        for ten in [t for t in sys.modules if t == "playwright" or t.startswith("playwright.")]:
            del sys.modules[ten]
        import builtins
        goc_import = builtins.__import__

        def _chan_playwright(name, *a, **k):
            if name == "playwright" or name.startswith("playwright."):
                raise ImportError("khong co playwright (gia lap cho test)")
            return goc_import(name, *a, **k)

        builtins.__import__ = _chan_playwright
        try:
            ok, ly_do = trinh_duyet_san_sang("firefox")
            assert ok is False, "thiếu playwright chính gốc mà vẫn báo sẵn sàng"
            assert "playwright" in ly_do.lower()
            # pool.start() PHẢI từ chối khởi động — không được chạy bình thường.
            pool = bp.BrowserPool()
            with pytest.raises(RuntimeError, match="từ chối khởi động"):
                asyncio.run(pool.start())
        finally:
            builtins.__import__ = goc_import
    finally:
        sys.modules.clear()
        sys.modules.update(da_luu)


if __name__ == "__main__":
    # Chế độ `docker exec -i ... python - < file`: chạy tay từng test, in kết quả.
    ket_qua = []
    for ten in (
        "test_chon_runtime_firefox_dung_playwright_goc",
        "test_cau_hinh_sai_bi_tu_choi_tieng_viet",
        "test_trinh_duyet_that_goto_evaluate_click_cookie",
        "test_bien_the_dung_anh_xa_sang_cau_hinh_chay",
        "test_cau_hinh_khong_dung_duoc_pool_tu_choi_khoi_dong",
    ):
        fn = globals()[ten]
        try:
            fn()
        except pytest.skip.Exception as e:  # noqa: PT012
            ket_qua.append((ten, f"SKIP: {e}"))
        except Exception as e:  # noqa: BLE001
            ket_qua.append((ten, f"FAIL: {type(e).__name__}: {e}"))
        else:
            ket_qua.append((ten, "PASS"))
    for ten, kq in ket_qua:
        print(f"{kq} :: {ten}")
    sys.exit(0 if all(kq.startswith(("PASS", "SKIP")) for _, kq in ket_qua) else 1)
