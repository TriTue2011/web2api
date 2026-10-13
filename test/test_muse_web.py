"""Chạy lại toàn bộ test của muse-web từ thư mục test/.

Bảo đảm khi chạy `./.venv/bin/python -m pytest test` thì test của muse-web cũng được chạy.
"""

import os

os.environ["PY_IGNORE_IMPORTMISMATCH"] = "1"

import importlib.util
from pathlib import Path
import sys

GOC_MUSE_WEB = Path(__file__).resolve().parents[1] / "dich-vu" / "muse-web"
if str(GOC_MUSE_WEB) not in sys.path:
    sys.path.insert(0, str(GOC_MUSE_WEB))

_tep_goc = GOC_MUSE_WEB / "test_muse_web.py"
_spec = importlib.util.spec_from_file_location("_dich_vu_muse_web_tests", _tep_goc)
if _spec and _spec.loader:
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _k, _v in _mod.__dict__.items():
        if _k.startswith("Test") or _k.startswith("test_"):
            globals()[_k] = _v
