# NV13: che bí mật trong log hệ thống ở gốc.
#
# Lỗi: các dòng log.warning/exception(... %s, e) ghi nguyên lời nhắn lỗi có thể chứa URL với token.
# Sửa: logging.Filter ở gốc dùng chung che_chuoi — test gắn filter vào root logger (app/main.py là
# tệp cấm, dòng đăng ký ghi trong báo cáo).
import logging
import time

import pytest

# Nạp app trước để phá vòng import (bien_loi → xac_thuc → nhat_ky → bien_loi),
# theo mẫu các test hiện có (from app.main import app).
from app.main import app as _app  # noqa: F401
from app.che_log import CheBiMat

# Token GIẢ, rõ ràng không phải thật (trong test/ nên gitleaks tha theo paths allowlist).
# Dài và liền mạch để khớp hình _DANG_TOKEN mà che_chuoi che.
TOKEN_GIA = "bot123456:TEST-FAKE-TOKEN-abcdef1234567890abcdef1234567890"


@pytest.fixture()
def loc_che(caplog):
    """Gắn filter vào handler của caplog (mô phỏng dòng đăng ký ở main.py).

    LƯU Ý: Filter phải gắn vào Handler, không phải Logger — Python chỉ kiểm filter
    của logger tạo record, bỏ qua filter của logger cha khi lan truyền.
    """
    f = CheBiMat()
    # Gắn vào mọi handler hiện có của root (caplog thêm handler của nó vào root)
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(f)
    # Và vào handler của caplog nếu nó chưa có
    if caplog.handler not in root.handlers:
        caplog.handler.addFilter(f)
    caplog.set_level(logging.DEBUG)
    try:
        yield f
    finally:
        for h in root.handlers:
            h.removeFilter(f)
        try:
            caplog.handler.removeFilter(f)
        except ValueError:
            pass


def test_che_token_trong_thong_diep(loc_che, caplog):
    """Token trong thông điệp log phải bị che."""
    log = logging.getLogger("web2api.test_nv13")
    log.warning("Không gọi được https://api.example.com/%s/getUpdates: lỗi mạng", TOKEN_GIA)
    assert TOKEN_GIA not in caplog.text
    # Vẫn giữ được cấu trúc dòng (không mất log)
    assert "getUpdates" in caplog.text


def test_che_token_trong_traceback(loc_che, caplog):
    """Token trong ngoại lệ (traceback) phải bị che."""
    log = logging.getLogger("web2api.test_nv13")
    try:
        raise RuntimeError(f"Lỗi gọi https://api.example.com/{TOKEN_GIA}/getUpdates")
    except RuntimeError:
        log.exception("Gọi API thất bại")
    assert TOKEN_GIA not in caplog.text
    assert "RuntimeError" in caplog.text  # loại lỗi vẫn còn để tra


def test_chuoi_thuong_khong_bi_doi(loc_che, caplog):
    """Chuỗi thường (không dạng token) không bị đổi."""
    log = logging.getLogger("web2api.test_nv13")
    log.info("Khởi động xong, nghe ở cổng 8080")
    assert "Khởi động xong, nghe ở cổng 8080" in caplog.text


def test_khong_loc_thi_lo_token(caplog):
    """Đột biến: gỡ filter → token lọt nguyên (chứng minh filter là chỗ che)."""
    log = logging.getLogger("web2api.test_nv13_khong_loc")
    cu = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)  # nhà máy trần: không che (app.main đã cài nhà máy che)
    try:
        caplog.set_level(logging.DEBUG)
        log.warning("Không gọi được https://api.example.com/%s/getUpdates", TOKEN_GIA)
        assert TOKEN_GIA in caplog.text
    finally:
        logging.setLogRecordFactory(cu)


def test_import_app_main_da_cai_che_log(caplog):
    """Nối dây thật: chỉ cần nạp app.main là mọi dòng log của lõi đã được che (không cần gắn filter tay)."""
    caplog.set_level(logging.DEBUG)
    logging.getLogger("web2api.test_nv13_noi_day").warning("lỗi %s", TOKEN_GIA)
    assert TOKEN_GIA not in caplog.text


def test_hieu_nang_100k_dong(loc_che, caplog):
    """100 000 dòng log không làm chậm đường nóng quá mức (ngưỡng 5s cho cả 100k)."""
    log = logging.getLogger("web2api.test_nv13_perf")
    caplog.set_level(logging.CRITICAL + 1)  # tắt handler để đo thuần filter
    bat_dau = time.perf_counter()
    for _ in range(100_000):
        log.warning("Dòng log thường không có bí mật %d", 123)
    het = time.perf_counter() - bat_dau
    assert het < 5.0, f"100k dòng mất {het:.2f}s — quá chậm"


def test_cai_dat_che_ca_khi_khong_co_handler_nao_cua_logger_goc():
    """Dưới uvicorn logger gốc không có handler: che phải xảy ra lúc TẠO bản ghi, không nhờ handler."""
    from app import che_log

    cu = logging.getLogRecordFactory()
    try:
        che_log.cai_dat()
        che_log.cai_dat()  # lần hai không bọc thêm
        r = logging.getLogger("web2api.thu").makeRecord(
            "web2api.thu", logging.WARNING, __file__, 1, "loi %s", (TOKEN_GIA,), None)
        assert TOKEN_GIA not in r.getMessage()
        assert logging.getLogRecordFactory().__closure__ is not None
    finally:
        logging.setLogRecordFactory(cu)


def test_ten_bien_moi_truong_khong_bi_che_nhung_token_van_bi_che():
    from app.bien_loi import che_chuoi

    assert "WEB2API_THAN_TOI_DA_MB" in che_chuoi("WEB2API_THAN_TOI_DA_MB='x' không hợp lệ")
    assert TOKEN_GIA not in che_chuoi(f"gọi {TOKEN_GIA}")
    assert "ABCDEFGH12345678abcdefgh" not in che_chuoi("khoá ABCDEFGH12345678abcdefgh")  # lẫn chữ thường vẫn che
