# _gia_goi.py — hỗ trợ test của dich-vu/may-dang-nhap (KHÔNG đặt tên conftest.py: tên đó trùng test/conftest.py và làm hỏng `from conftest import …` của các test lõi khi chạy chung một lượt pytest).
# Mỗi tệp test_c2a_*.py `import _gia_goi` ở đầu tệp để có thư mục dữ liệu tạm và mô-đun giả.
import os
import sys
import tempfile
from unittest.mock import MagicMock

# Cấu hình đường dẫn dữ liệu và khoá tạm khi chạy test nếu chưa đặt
if "CAPTCHA_SOLVER_DATA_DIR" not in os.environ:
    _tmp_dir = tempfile.mkdtemp(prefix="w2a_c2a_test_")
    os.environ["CAPTCHA_SOLVER_DATA_DIR"] = _tmp_dir
    if "ACCOUNTS_DB" not in os.environ:
        os.environ["ACCOUNTS_DB"] = os.path.join(_tmp_dir, "accounts.db")

if "CAPTCHA_SOLVER_KEY_DIR" not in os.environ:
    _tmp_key_dir = tempfile.mkdtemp(prefix="w2a_c2a_test_key_")
    os.environ["CAPTCHA_SOLVER_KEY_DIR"] = _tmp_key_dir


# Tạo mô-đun giả cho các gói chưa cài trong .venv nếu cần
def _cai_mo_dun_gia(ten: str) -> MagicMock:
    if ten not in sys.modules:
        m = MagicMock()
        m.__name__ = ten
        m.__file__ = f"<mock_{ten}>"
        sys.modules[ten] = m
    return sys.modules[ten]

cac_goi = [
    "patchright",
    "patchright.async_api",
    "cloakbrowser",
    "playwright",
    "playwright.async_api",
    "playwright_recaptcha",
    "speech_recognition",
    "pydub",
]

for g in cac_goi:
    try:
        __import__(g)
    except ImportError:
        _cai_mo_dun_gia(g)
