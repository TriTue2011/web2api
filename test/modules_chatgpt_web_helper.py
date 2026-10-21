"""NV210 — helper cho test: xuất lại ``email_cua`` THẬT từ module chatgpt-web.

Test ``test_nv210_chatgpt_dang_nhap.py`` kiểm tra claim email trong JWT mà luồng
đăng nhập ChatGPT trả về có giải mã được (dữ liệu chắc chắn để ghép khoá).
Dùng đúng hàm giải mã của module chatgpt-web (không viết bộ giải mã thứ hai).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_GOC = Path(__file__).resolve().parent.parent


def _tai_lam_moi_jwt():
    p = _GOC / "modules" / "chatgpt-web" / "lam_moi_jwt.py"
    spec = importlib.util.spec_from_file_location(
        "nv210_chatgpt_web_lam_moi_jwt", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


email_cua = _tai_lam_moi_jwt().email_cua
