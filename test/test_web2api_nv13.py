# NV13: che bí mật trong log hệ thống ở gốc.
from __future__ import annotations

import logging
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

from app.che_log import CheBiMat, cai_dat
from app.main import app as _app  # noqa: F401

# Token GIẢ theo allowlist .gitleaks.toml
TOKEN_GIA = "bot123456:TEST-FAKE-TOKEN-abcdef1234567890abcdef1234567890"
KHOA_GOOGLE_GIA = "AIzaSyUI-TOT-0000000000000000000000tot1"
KHOA_GHP_GIA = "ghp_TOKENRIENG1234567890"
KHOA_TU_XA_GIA = "sk-khoa-tu-xa-RIENG-7777"
KHOA_HEX32_GIA = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def loc_che(caplog):
    """Gắn filter vào handler của caplog với nhà máy trần (chứng minh bộ lọc làm việc)."""
    f = CheBiMat()
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(f)
    if caplog.handler not in root.handlers:
        caplog.handler.addFilter(f)
    caplog.set_level(logging.DEBUG)
    cu_factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        yield f
    finally:
        logging.setLogRecordFactory(cu_factory)
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
    assert "getUpdates" in caplog.text


def test_che_token_trong_traceback(loc_che, caplog):
    """Token trong ngoại lệ (traceback) phải bị che."""
    log = logging.getLogger("web2api.test_nv13")
    try:
        raise RuntimeError(f"Lỗi gọi https://api.example.com/{TOKEN_GIA}/getUpdates")
    except RuntimeError:
        log.exception("Gọi API thất bại")
    assert TOKEN_GIA not in caplog.text
    assert "RuntimeError" in caplog.text


def test_chuoi_thuong_khong_bi_doi(loc_che, caplog):
    """Chuỗi thường (không dạng token) không bị đổi."""
    log = logging.getLogger("web2api.test_nv13")
    log.info("Khởi động xong, nghe ở cổng 8080")
    assert "Khởi động xong, nghe ở cổng 8080" in caplog.text


def test_khong_loc_thi_lo_token(caplog):
    """Đột biến: gỡ filter → token lọt nguyên (chứng minh filter là chỗ che)."""
    log = logging.getLogger("web2api.test_nv13_khong_loc")
    cu = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        caplog.set_level(logging.DEBUG)
        log.warning("Không gọi được https://api.example.com/%s/getUpdates", TOKEN_GIA)
        assert TOKEN_GIA in caplog.text
    finally:
        logging.setLogRecordFactory(cu)


def test_import_app_main_da_cai_che_log(caplog):
    """Nối dây thật: nạp app.main là mọi dòng log đã được che."""
    caplog.set_level(logging.DEBUG)
    logging.getLogger("web2api.test_nv13_noi_day").warning("lỗi %s", TOKEN_GIA)
    assert TOKEN_GIA not in caplog.text


def test_hieu_nang_100k_dong(loc_che):
    """Đo THẬT 100 000 lần gọi trực tiếp bộ lọc, khẳng định dưới trần 200 µs/dòng (< 20s)."""
    r = logging.LogRecord("web2api.perf", logging.WARNING, __file__, 1,
                          "Dòng log thường không có bí mật %d", (123,), None)
    bat_dau = time.perf_counter()
    for _ in range(100_000):
        loc_che.filter(r)
    het = time.perf_counter() - bat_dau
    assert het < 20.0, f"100k dòng mất {het:.2f}s — quá chậm (> 200 µs/dòng)"


def test_cai_dat_che_ca_khi_khong_co_handler_nao_cua_logger_goc():
    """Dưới uvicorn logger gốc không có handler: che lúc tạo bản ghi và lần hai không bọc thêm."""
    cu = logging.getLogRecordFactory()
    try:
        cai_dat()
        nha_may_1 = logging.getLogRecordFactory()
        cai_dat()
        nha_may_2 = logging.getLogRecordFactory()
        assert nha_may_1 is nha_may_2, "cai_dat lần hai không được bọc lồng thêm"

        r = logging.getLogger("web2api.thu").makeRecord(
            "web2api.thu", logging.WARNING, __file__, 1, "loi %s", (TOKEN_GIA,), None)
        assert TOKEN_GIA not in r.getMessage()
    finally:
        logging.setLogRecordFactory(cu)


def test_ten_bien_moi_truong_khong_bi_che_nhung_token_van_bi_che():
    from app.bien_loi import che_chuoi

    assert "WEB2API_THAN_TOI_DA_MB" in che_chuoi("WEB2API_THAN_TOI_DA_MB='x' không hợp lệ")
    assert TOKEN_GIA not in che_chuoi(f"gọi {TOKEN_GIA}")
    assert "ABCDEFGH12345678abcdefgh" not in che_chuoi("khoá ABCDEFGH12345678abcdefgh")


# ── T1: exc_info giữ nguyên, che ở dạng văn bản ─────────────────────────

def test_t1_exc_info_khong_mat_chuoi_nguyen_nhan_va_che_token(loc_che):
    """T1: raise RuntimeError from ConnectionError(URL có token) giữ cả hai khối và direct cause, token bị che."""
    log = logging.getLogger("web2api.test_t1")
    url_co_token = f"https://api.example.com/{TOKEN_GIA}/getUpdates"
    r = None
    try:
        try:
            raise ConnectionError(f"Không kết nối được {url_co_token}")
        except ConnectionError as e:
            raise RuntimeError("outer error message") from e
    except RuntimeError:
        r = log.makeRecord("web2api.test_t1", logging.ERROR, __file__, 1,
                           "Lỗi thực thi", (), sys.exc_info())
        loc_che.filter(r)

    # exc_info gốc được GIỮ NGUYÊN (không bị ép thành Exception)
    assert r.exc_info[0] is RuntimeError
    assert isinstance(r.exc_info[1], RuntimeError)

    # Văn bản định dạng qua Formatter chuẩn dùng exc_text đã che
    formatted = logging.Formatter().format(r)
    assert "outer error message" in formatted
    assert "ConnectionError" in formatted
    assert "direct cause" in formatted
    assert TOKEN_GIA not in formatted
    assert "getUpdates" in formatted


def test_t1_exception_group_giu_loi_con(loc_che):
    """T1: ExceptionGroup giữ đủ các lỗi con khi định dạng."""
    log = logging.getLogger("web2api.test_t1_group")
    r = None
    try:
        raise ExceptionGroup("nhom loi", [ValueError("loi 1"), TypeError("loi 2")])
    except ExceptionGroup:
        r = log.makeRecord("web2api.test_t1_group", logging.ERROR, __file__, 1,
                           "Gặp nhóm lỗi", (), sys.exc_info())
        loc_che.filter(r)

    assert r.exc_info[0] is ExceptionGroup
    formatted = logging.Formatter().format(r)
    assert "ExceptionGroup" in formatted
    assert "ValueError: loi 1" in formatted
    assert "TypeError: loi 2" in formatted


def test_t1_keyerror_khong_hai_lop_nhay(loc_che):
    """T1: KeyError không bị biến dạng hai lớp nháy."""
    log = logging.getLogger("web2api.test_t1_key")
    r = None
    try:
        raise KeyError("khoa_x")
    except KeyError:
        r = log.makeRecord("web2api.test_t1_key", logging.ERROR, __file__, 1,
                           "Lỗi khoá", (), sys.exc_info())
        loc_che.filter(r)

    formatted = logging.Formatter().format(r)
    assert "KeyError: 'khoa_x'" in formatted
    assert "KeyError: \"'khoa_x'\"" not in formatted


def test_t1_uvicorn_formatter_neu_co(loc_che):
    """T1: Formatter của uvicorn (nếu có trong venv) định dạng đúng record đã lọc."""
    try:
        from uvicorn.logging import DefaultFormatter
    except ImportError:
        pytest.skip("uvicorn không có sẵn")
    log = logging.getLogger("web2api.test_t1_uvicorn")
    r = None
    try:
        raise RuntimeError("loi uvicorn")
    except RuntimeError:
        r = log.makeRecord("web2api.test_t1_uvicorn", logging.ERROR, __file__, 1,
                           "Lỗi uvicorn", (), sys.exc_info())
        loc_che.filter(r)
    formatted = DefaultFormatter().format(r)
    assert "RuntimeError: loi uvicorn" in formatted


# ── L1, L2, L3: đối số và chuỗi mẫu ──────────────────────────────────────

def test_l1_decimal_path_so_giu_nguyen_khi_format(loc_che, caplog):
    """L1: Decimal với %d, Path với %r, số giữ nguyên kiểu, không ném TypeError."""
    log = logging.getLogger("web2api.test_l1")
    p = Path("/tmp/duong_dan_test")
    log.info("So %d, so thuc %.2f, path %r", Decimal("100"), 3.14, p)
    assert "So 100, so thuc 3.14" in caplog.text
    assert str(p) in caplog.text


def test_l1_doi_so_str_hong_khong_lam_hong_ca_bo(loc_che):
    """L1: một đối số có __str__ ném lỗi không làm hỏng filter."""
    class HongLoi(Exception):
        def __str__(self):
            raise RuntimeError("str bi loi")

    r = logging.LogRecord("web2api.test_l1_hong", logging.WARNING, __file__, 1,
                          "Loi: %s, token: %s", (HongLoi(), TOKEN_GIA), None)
    loc_che.filter(r)
    assert r.args[0] == "<không che được>"
    assert TOKEN_GIA not in r.args[1]


def test_l1_dict_args(loc_che):
    """L1: dict args với %()s được che từng phần tử."""
    r = logging.LogRecord("web2api.test_l1_dict", logging.INFO, __file__, 1,
                          "Token %(t)s, so %(s)d", {"t": TOKEN_GIA, "s": Decimal("42")}, None)
    loc_che.filter(r)
    assert TOKEN_GIA not in r.args["t"]
    assert r.args["s"] == Decimal("42")
    formatted = logging.Formatter().format(r)
    assert "so 42" in formatted


def test_l2_chuoi_mau_co_phan_tram_khong_bi_che(loc_che):
    """L2: chuỗi mẫu có % khi args khác rỗng KHÔNG bị che (tránh hỏng định dạng)."""
    r = logging.LogRecord("web2api.test_l2", logging.INFO, __file__, 1,
                          "%s_abcdefghijklmnopqrstuvwxyz", ("z",), None)
    loc_che.filter(r)
    assert r.msg == "%s_abcdefghijklmnopqrstuvwxyz"
    formatted = logging.Formatter().format(r)
    assert formatted.endswith("z_abcdefghijklmnopqrstuvwxyz")


def test_l2_fstring_chua_token_bi_che(loc_che, caplog):
    """L2: khi args rỗng (f-string), msg chứa token bị che."""
    log = logging.getLogger("web2api.test_l2_fstring")
    log.info(f"Yêu cầu gửi tới {TOKEN_GIA}")
    assert TOKEN_GIA not in caplog.text


def test_l3_record_msg_ngoai_le_args_rong(loc_che, caplog):
    """L3: record.msg là ngoại lệ và args rỗng → đổi thành chuỗi đã che."""
    log = logging.getLogger("web2api.test_l3")
    log.info(ValueError(f"Thông điệp chứa {TOKEN_GIA}"))
    assert TOKEN_GIA not in caplog.text
    assert "Thông điệp chứa" in caplog.text


# ── T2: định danh vận hành không bị che thừa trong log hệ thống ──────────

def test_t2_dinh_danh_van_hanh_khong_bi_che(loc_che, caplog):
    """T2: tên model, đường dẫn, tên tệp thuần chữ thường + chấm/gạch không bị che."""
    log = logging.getLogger("web2api.test_t2")
    log.info("Model: %s", "gemini-2.5-flash-image-preview")
    log.info("Model 2: %s", "gpt-4o-mini-transcribe-2025-12-15")
    log.info("Tệp: %s", "nguon_git_trang_thai.json.hong")
    log.info("Đường dẫn: %s", "/opt/claude-c2a/agy-dieu-phoi/wt/va-loi-lo-muse/app/main.py")
    log.info("Lỗi thượng nguồn: %s", "noi-dung-thuong-nguon-gia")

    assert "gemini-2.5-flash-image-preview" in caplog.text
    assert "gpt-4o-mini-transcribe-2025-12-15" in caplog.text
    assert "nguon_git_trang_thai.json.hong" in caplog.text
    assert "/opt/claude-c2a/agy-dieu-phoi/wt/va-loi-lo-muse/app/main.py" in caplog.text
    assert "noi-dung-thuong-nguon-gia" in caplog.text


def test_t2_khoa_ngau_nhien_va_hex_bi_che(loc_che, caplog):
    """T2: khoá API ngẫu nhiên có cả HOA và số, hex 32, và cấu trúc URL/Bearer bị che."""
    log = logging.getLogger("web2api.test_t2_khoa")
    log.warning("Khóa Google: %s", KHOA_GOOGLE_GIA)
    log.warning("Khóa GitHub: %s", KHOA_GHP_GIA)
    log.warning("Khóa từ xa: %s", KHOA_TU_XA_GIA)
    log.warning("Hex 32: %s", KHOA_HEX32_GIA)
    log.warning("Bearer token: Bearer sk-TEST-TOKEN-1234567890abcdef")
    log.warning("API key URL: api_key=KEY-SECRET-1234567890")
    log.warning("Telegram URL: https://api.telegram.org/bot123456:ABC-DEF-123456/getUpdates")

    assert KHOA_GOOGLE_GIA not in caplog.text
    assert KHOA_GHP_GIA not in caplog.text
    assert KHOA_TU_XA_GIA not in caplog.text
    assert KHOA_HEX32_GIA not in caplog.text
    assert "sk-TEST-TOKEN-1234567890abcdef" not in caplog.text
    assert "KEY-SECRET-1234567890" not in caplog.text
    assert "ABC-DEF-123456" not in caplog.text
    assert "getUpdates" in caplog.text
