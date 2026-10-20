"""Test NV153 đợt D2 — Authorization không ASCII phải 401, không 500.

`xac_thuc.kiem_bearer` cũ dùng `hmac.compare_digest(str, str)`: header chứa
ký tự ngoài ASCII (vd `Bearer tök`) làm `compare_digest` ném TypeError →
server trả 500 thay vì 401. Trên dây, header là byte latin-1 (ASGI giải mã
latin-1) nên test gửi header dạng bytes thô `b"Bearer t\\xf6k"`.

Cùng lớp (đo bằng grep "compare_digest" dich-vu/ modules/):
- dich-vu/youtube-phat/streaming.py:148 — hai vế bị regex `[A-Za-z0-9_-]+`
  và b64 ép ASCII → không ném được.
- dich-vu/youtube-phat/streaming.py:283 — chữ ký do khách đưa có thể không
  ASCII, NHƯNG `verify_stream_token` đã bắt TypeError → InvalidStreamToken
  → 403. Test dưới khoá cả hai đường này.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import tieu_de_xac_thuc
from xac_thuc import LoiXacThuc, kiem_bearer

# Header như trên dây thật: «ö» là byte 0xF6 latin-1.
HEADER_KHONG_ASCII = [(b"authorization", b"Bearer t\xf6k")]


def test_kiem_bearer_khong_ascii_la_loi_xac_thuc_khong_phai_typeerror():
    with pytest.raises(LoiXacThuc):
        kiem_bearer("Bearer tök", "tok-dung")


def test_kiem_bearer_dung_token_ascii_van_qua():
    kiem_bearer("Bearer tok-dung", "tok-dung")  # không ném = qua


def test_kiem_bearer_sai_token_ascii_van_401():
    with pytest.raises(LoiXacThuc):
        kiem_bearer("Bearer tok-sai", "tok-dung")


def test_kiem_bearer_thieu_header_van_401():
    with pytest.raises(LoiXacThuc):
        kiem_bearer(None, "tok-dung")


@pytest.mark.parametrize("duong", [
    "/api/integration/health",
    "/api/integration/status",
    "/api/integration/playlists",
])
def test_server_header_khong_ascii_tra_401_khong_500(ung_dung, duong):
    app, _token, _thu_muc = ung_dung
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(duong, headers=HEADER_KHONG_ASCII)
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_auth"}


def test_server_header_dung_token_van_200(ung_dung):
    app, token, _thu_muc = ung_dung
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/integration/health", headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200


def test_stream_token_chu_ky_khong_ascii_tra_403_khong_500(ung_dung):
    # Token luồng có phần chữ ký không ASCII: verify_stream_token phải dịch
    # thành 403 invalid_stream_token (đường này dùng khoá phía path, không
    # phải Bearer — kiểm để khoá lớp lỗi ở cả hai chỗ so token của dịch vụ).
    app, _token, _thu_muc = ung_dung
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/stream/YWJj.tök")
    assert r.status_code in (400, 403)
    assert r.status_code != 500
