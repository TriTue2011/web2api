"""NV272: gemini-web và grok-web khởi động được khi chưa có cookie.

Lớp lỗi: module có khả năng tự đăng nhập (manifest khai ``dang_nhap_bang``)
bị lõi chặn ở ``_tao_tu_kho`` vì thiếu trường bí mật bắt buộc, nên không có
chỗ chạy để đăng nhập tự động tự lấy cookie về.
Đã grep chỗ cùng lớp: lệnh liệt kê 25 module có trường bí mật bắt buộc
trong ``modules/*/manifest.json`` (xem báo cáo), trong đó 5 module khai
``dang_nhap_bang``; sửa theo nguyên tắc (cấu trúc manifest), không theo
danh sách tên module/nhà cung cấp trong lõi.
"""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-qt-nv272"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    for ten in ("gemini-web", "grok-web", "gemini-api"):
        shutil.copytree(GOC / "modules" / ten, tmp_path / "modules" / ten)
    # Module ĐÃ CÀI nhưng KHÔNG có bí mật: gemini-web/grok-web trống cookie,
    # gemini-api (không tự đăng nhập) trống api_keys — làm đối chứng.
    (tmp_path / "modules.json").write_text(json.dumps({"modules": [
        {"id": "gemini-web"},
        {"id": "grok-web"},
        {"id": "gemini-api"},
    ]}))
    return tmp_path


def _muc(mods, id_):
    return next((m for m in mods if m.get("id") == id_), None)


def test_khoi_dong_khong_cookie_va_chat_tra_can_dang_nhap(du_lieu):
    with TestClient(app) as c:
        mods = c.get("/api/modules", headers=QT).json()["modules"]
        # 1. Khởi động: hai module tự đăng nhập chạy, module đối chứng không.
        assert _muc(mods, "gemini-web")["da_cau_hinh"] is True
        assert _muc(mods, "grok-web")["da_cau_hinh"] is True
        assert _muc(mods, "gemini-api")["da_cau_hinh"] is False
        # 2. Gọi chat chưa có phiên: mã rõ can_dang_nhap, không phải lỗi chung.
        r = c.post("/v1/chat/completions", headers=QT, json={
            "model": "gemini-web/gemini-3-flash",
            "messages": [{"role": "user", "content": "alo"}],
        })
        assert r.json()["error"]["code"] == "can_dang_nhap", r.text
        r = c.post("/v1/chat/completions", headers=QT, json={
            "model": "grok/fast",
            "messages": [{"role": "user", "content": "alo"}],
        })
        assert r.json()["error"]["code"] == "can_dang_nhap", r.text
