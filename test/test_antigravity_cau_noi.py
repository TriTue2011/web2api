"""Chạy lại toàn bộ test của cầu nối Antigravity từ thư mục test/.

Bảo đảm khi chạy `./.venv/bin/python -m pytest test` thì test cầu nối cũng được chạy.
"""

from pathlib import Path
import sys

GOC_CAU_NOI = Path(__file__).resolve().parents[1] / "dich-vu" / "antigravity-cau-noi"
if str(GOC_CAU_NOI) not in sys.path:
    sys.path.insert(0, str(GOC_CAU_NOI))

from test_cau_noi import *  # noqa: F401, F403
