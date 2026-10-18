"""Ảnh dựng từ Dockerfile phải chứa MỌI mô-đun cục bộ mà server.py nhập.

Lỗi thật (18/10/2026): server.py nhập `marionette` (NV92) nhưng Dockerfile chỉ COPY server.py và xac_thuc_ws.py, nên ảnh dựng mới
chết ngay lúc khởi động: «ModuleNotFoundError: No module named 'marionette'». Test trong thư mục chạy được vì mọi tệp nằm cạnh nhau;
chỉ dựng ảnh thật mới lộ.

Chủ kho đã duyệt sửa Dockerfile (18/10/2026): COPY và chmod đã có `marionette.py`; test này giờ chạy thường (không còn xfail).
"""

from __future__ import annotations

import ast
from pathlib import Path

GOC = Path(__file__).resolve().parent


def _mo_dun_cuc_bo_duoc_nhap() -> set[str]:
    cay = ast.parse((GOC / "server.py").read_text(encoding="utf-8"))
    ten: set[str] = set()
    for n in ast.walk(cay):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            ten.add(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            ten.update(a.name.split(".")[0] for a in n.names)
    return {t for t in ten if (GOC / f"{t}.py").is_file()}


def test_server_nhap_it_nhat_mot_mo_dun_cuc_bo():
    """Khoá lại để phép kiểm bên dưới không rỗng khi server.py đổi cách nhập."""
    assert "marionette" in _mo_dun_cuc_bo_duoc_nhap()


def test_dockerfile_copy_du_mo_dun_cuc_bo():
    dockerfile = (GOC / "Dockerfile").read_text(encoding="utf-8")
    dong_copy = " ".join(l for l in dockerfile.splitlines() if l.strip().startswith("COPY "))
    thieu = sorted(t for t in _mo_dun_cuc_bo_duoc_nhap() if f"{t}.py" not in dong_copy)
    assert not thieu, f"Dockerfile không COPY: {thieu}"
