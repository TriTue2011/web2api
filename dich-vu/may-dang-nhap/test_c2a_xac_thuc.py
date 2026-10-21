# test_c2a_xac_thuc.py — Kiểm tra xác thực nội bộ bắt buộc cho may-dang-nhap
from __future__ import annotations

import hmac
import os
import re
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    import pydantic_settings  # noqa: F401
except ImportError:
    pytest.fail("Môi trường thiếu gói pydantic_settings bắt buộc; không được bỏ qua bài kiểm.")
import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)


KHOA_TEST_HOP_LE = "khoa-gia-0123456789abcdef-ghi-ro-la-gia"
KHOA_SAI = "khoa-sai-0123456789abcdef-ghi-ro-la-gia"


def test_khoa_sai_dinh_dang_nem_loi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.settings import Settings, lay_khoa_dich_vu

    # 1. Khoá quá ngắn (< 24)
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", "ngan-123")
    with pytest.raises(RuntimeError, match="tối thiểu 24 ký tự"):
        lay_khoa_dich_vu(data_dir=tmp_path)

    # 2. Khoá đơn điệu (< 10 ký tự khác nhau)
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with pytest.raises(RuntimeError, match="đơn điệu"):
        lay_khoa_dich_vu(data_dir=tmp_path)

    # 3. Khoá là change-me
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", "change-me")
    with pytest.raises(RuntimeError):
        lay_khoa_dich_vu(data_dir=tmp_path)

    # Kiểm tra thông báo lỗi tiếng Việt KHÔNG chứa giá trị khoá bí mật
    khoa_bi_mat = "bi-mat-dai-hon-24-ky-tu-nhung-aaaaaaaaaaaa"
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", khoa_bi_mat)
    try:
        lay_khoa_dich_vu(data_dir=tmp_path)
    except RuntimeError as exc:
        assert khoa_bi_mat not in str(exc)


def test_sinh_khoa_va_ghi_file_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.settings import lay_khoa_dich_vu

    monkeypatch.delenv("CAPTCHA_SOLVER_API_KEY", raising=False)
    tep_khoa = tmp_path / "khoa-dich-vu"
    assert not tep_khoa.exists()

    # Lần 1: sinh khoá và ghi tệp
    khoa1 = lay_khoa_dich_vu(data_dir=tmp_path)
    assert len(khoa1) >= 24
    assert len(set(khoa1)) >= 10
    assert tep_khoa.exists()

    # Kiểm tra quyền 0600
    st_mode = stat.S_IMODE(tep_khoa.stat().st_mode)
    assert st_mode == 0o600

    # Lần 2: đọc lại cùng khoá
    khoa2 = lay_khoa_dich_vu(data_dir=tmp_path)
    assert khoa2 == khoa1

    # Tệp có quyền rộng (0644) -> được siết lại 0600
    tep_khoa.chmod(0o644)
    khoa3 = lay_khoa_dich_vu(data_dir=tmp_path)
    assert khoa3 == khoa1
    assert stat.S_IMODE(tep_khoa.stat().st_mode) == 0o600


def test_caplog_khong_chua_khoa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    import logging
    from src.settings import lay_khoa_dich_vu

    monkeypatch.delenv("CAPTCHA_SOLVER_API_KEY", raising=False)
    with caplog.at_level(logging.INFO):
        khoa = lay_khoa_dich_vu(data_dir=tmp_path)
    # caplog không được chứa chuỗi khoá
    assert khoa not in caplog.text


def kiem_ma_xac_thuc(code: str) -> list[str]:
    loi = []
    if "hmac.compare_digest" not in code:
        loi.append("Thiếu hmac.compare_digest trong xác thực")
    if re.search(r"token\s*==\s*", code):
        loi.append("Phát hiện so sánh token bằng toán tử == (nguy cơ timing attack)")
    if re.search(r"if\s+not\s+.*api_key.*:\s*return", code):
        loi.append("Phát hiện nhánh bypass tắt khoá xác thực")
    return loi


def test_kiem_ma_xac_thuc_va_dot_bien():
    src_main = Path(__file__).resolve().parent / "src" / "main.py"
    code_that = src_main.read_text(encoding="utf-8")
    loi = kiem_ma_xac_thuc(code_that)
    assert not loi, f"Mã nguồn xác thực có lỗi: {loi}"

    # Đột biến 1: thay hmac.compare_digest bằng so sánh khác -> bắt được lỗi
    code_dot_bien_1 = code_that.replace("hmac.compare_digest", "so_sanh_khac")
    assert any("Thiếu hmac.compare_digest" in err for err in kiem_ma_xac_thuc(code_dot_bien_1))

    # Đột biến 2: thêm nhánh bypass if not settings.api_key: return -> bắt được lỗi
    code_dot_bien_2 = code_that + "\nif not settings.api_key: return\n"
    assert any("bypass" in err for err in kiem_ma_xac_thuc(code_dot_bien_2))


def test_tat_ca_routes_doi_xac_thuc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)

    routes_kiem_tra = []

    def _duyet_routes(route_list):
        for r in route_list:
            if hasattr(r, "routes"):
                _duyet_routes(r.routes)
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", set())
            if not path or path == "/health":
                continue
            phuong_thuc = [m for m in methods if m not in ("HEAD", "OPTIONS")]
            for m in phuong_thuc:
                routes_kiem_tra.append((m, path))

    _duyet_routes(app.routes)
    routes_kiem_tra = sorted(set(routes_kiem_tra))
    assert len(routes_kiem_tra) >= 20, f"Số lượng route quá ít ({len(routes_kiem_tra)}), nghi ngờ bỏ sót route"

    for m, p in routes_kiem_tra:
        url = p.replace("{email}", "nguoi.a@example.com")
        url = re.sub(r"\{[a-zA-Z0-9_]+\}", "default", url)

        # 1. Không có Authorization header -> 401
        res = client.request(m, url)
        assert res.status_code == 401, f"{m} {url} không có header mà trả {res.status_code}"

        # 2. Authorization rỗng -> 401
        res = client.request(m, url, headers={"Authorization": "Bearer "})
        assert res.status_code == 401, f"{m} {url} token rỗng mà trả {res.status_code}"

        # 3. Authorization sai khoá -> 401
        res = client.request(m, url, headers={"Authorization": f"Bearer {KHOA_SAI}"})
        assert res.status_code == 401, f"{m} {url} sai token mà trả {res.status_code}"

        # 4. Sai kiểu (không Bearer) -> 401
        res = client.request(m, url, headers={"Authorization": f"Token {KHOA_TEST_HOP_LE}"})
        assert res.status_code == 401, f"{m} {url} sai kiểu Token mà trả {res.status_code}"



def test_khoa_dung_khong_bi_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    # Gọi endpoint kiểm tra trạng thái profile
    res = client.get("/v1/session/default/status", headers={"Authorization": f"Bearer {KHOA_TEST_HOP_LE}"})
    assert res.status_code != 401


def test_health_khong_can_khoa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ok"
    assert "novnc" in data


def test_cors_da_bo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    res = client.options("/health", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in res.headers

    res = client.get("/health", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in res.headers


def test_docs_openapi_da_tat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_dot_bien_so_sanh_bang_hmac(monkeypatch: pytest.MonkeyPatch):
    """Bảo đảm so sánh khoá dùng hmac.compare_digest thay vì ==."""
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST_HOP_LE)
    from src.main import require_api_key

    called = False
    original_compare = hmac.compare_digest

    def mock_compare(a, b):
        nonlocal called
        called = True
        return original_compare(a, b)

    with patch("src.main.hmac.compare_digest", side_effect=mock_compare):
        require_api_key(f"Bearer {KHOA_TEST_HOP_LE}")
    assert called, "require_api_key PHẢI gọi hmac.compare_digest để chống timing attack"


class _FakePool:
    async def start(self):
        pass

    async def stop(self):
        pass


def test_lifespan_kiem_khoa_fail_fast_khoa_sai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Khi khoá sai khuôn (quá ngắn), lifespan fail-fast ném RuntimeError lúc khởi động."""
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", "khoa-ngan")
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))

    from fastapi.testclient import TestClient
    import src.main
    from src.main import app

    monkeypatch.setattr(src.main, "pool", _FakePool())

    with pytest.raises(RuntimeError, match="tối thiểu 24 ký tự"):
        with TestClient(app):
            pass


def test_lifespan_kiem_khoa_fail_fast_tu_sinh_file_0600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Khi không đặt biến, lifespan tự sinh tệp khoa-dich-vu mode 0600 ngay lúc khởi động."""
    monkeypatch.delenv("CAPTCHA_SOLVER_API_KEY", raising=False)
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(tmp_path))
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))

    from fastapi.testclient import TestClient
    import src.main
    from src.main import app
    from src.settings import settings

    monkeypatch.setattr(settings, "key_dir", tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(src.main, "pool", _FakePool())

    tep_khoa = tmp_path / "khoa-dich-vu"
    assert not tep_khoa.exists()

    with TestClient(app):
        assert tep_khoa.exists()
        assert stat.S_IMODE(tep_khoa.stat().st_mode) == 0o600
        noi_dung = tep_khoa.read_text(encoding="utf-8").strip()
        assert len(noi_dung) >= 24


def test_lay_khoa_dich_vu_tep_hong_nem_runtime_error_tieng_viet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Khi tệp khoa-dich-vu rỗng hoặc không hợp lệ, lay_khoa_dich_vu ném RuntimeError tiếng Việt và không ghi đè."""
    monkeypatch.delenv("CAPTCHA_SOLVER_API_KEY", raising=False)
    monkeypatch.setenv("CAPTCHA_SOLVER_KEY_DIR", str(tmp_path))

    from src.settings import lay_khoa_dich_vu

    tep_khoa = tmp_path / "khoa-dich-vu"

    # 1. Tệp rỗng
    tep_khoa.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="rỗng"):
        lay_khoa_dich_vu(key_dir=tmp_path)
    assert tep_khoa.read_text(encoding="utf-8") == "", "Không được tự ý ghi đè lên tệp rỗng"

    # 2. Tệp quá ngắn (< 24 ký tự)
    tep_khoa.write_text("ngan", encoding="utf-8")
    with pytest.raises(RuntimeError, match="24 ký tự"):
        lay_khoa_dich_vu(key_dir=tmp_path)
    assert tep_khoa.read_text(encoding="utf-8") == "ngan", "Không được tự ý ghi đè lên tệp sai chuẩn"


