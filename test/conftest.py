"""Cho test của web2api import được gói `app` của RIÊNG web2api, và chặn mọi đường ra Internet thật.

Vì sao không đặt __init__.py ở thư mục này: repo gốc đã có gói `test` (517 file) và
thư viện chuẩn cũng có `test`; thêm __init__.py thì pytest đặt tên module thành
`test.…` và đụng tên. Không có nó, pytest chỉ thêm thư mục này vào sys.path.

Lưới chặn mạng: test chạy trên MÁY CHỦ THẬT, mà mặc định module gọi
generativelanguage.googleapis.com và vòng nền gọi api.github.com. Một test quên trỏ
về máy chủ giả sẽ tiêu hạn mức thật — nên mọi yêu cầu không phải 127.0.0.1 bị đẩy
vào proxy chết (cổng 9, discard) và hỏng ngay, còn máy chủ giả cục bộ đi thẳng nhờ NO_PROXY.
"""

import sys
from pathlib import Path

import pytest

GOC_WEB2API = Path(__file__).resolve().parents[1]
if str(GOC_WEB2API) not in sys.path:
    sys.path.insert(0, str(GOC_WEB2API))

#: Biến môi trường dùng chung cho test trong tiến trình VÀ tiến trình uvicorn con (test_web2api_kiem_nghiem).
MOI_TRUONG_CHAN_MANG = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "ALL_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "127.0.0.1,localhost",
    # Cổng 9 trên loopback không ai nghe → gọi GitHub "thật" hỏng ngay thay vì ra mạng.
    "WEB2API_GITHUB_API": "http://127.0.0.1:9",
}


@pytest.fixture(autouse=True)
def chan_mang_that(monkeypatch):
    for k, v in MOI_TRUONG_CHAN_MANG.items():
        monkeypatch.setenv(k, v)
    for k in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    for k in ("WEB2API_GIT_REPO", "WEB2API_GIT_NHANH", "WEB2API_GIT_THU_MUC", "WEB2API_GIT_TOKEN",
              "WEB2API_ADMIN_KEY", "WEB2API_TEST_CHO_PHEP_LOOPBACK", "WEB2API_MAY_RAM_MB", "WEB2API_MAY_CPU"):
        monkeypatch.delenv(k, raising=False)
