# test_c2a_tinh_nang.py — Kiểm tra công tắc tính năng và bảo vệ nhóm route
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import pydantic_settings  # noqa: F401
except ImportError:
    pytest.fail("Môi trường thiếu gói pydantic_settings bắt buộc; không được bỏ qua bài kiểm.")
import _gia_goi  # noqa: F401  (dữ liệu tạm + mô-đun giả; xem _gia_goi.py)


KHOA_TEST = "khoa-gia-0123456789abcdef-ghi-ro-la-gia"
HEADER_AUTH = {"Authorization": f"Bearer {KHOA_TEST}"}

# Bảng phân nhóm route mong muốn cho TOÀN BỘ route của may-dang-nhap
BANG_NHOM_MONG_MUON = {
    # Mở
    ("GET", "/health"): "mo",

    # dang_nhap (BẬT mặc định)
    ("POST", "/v1/session/manual-login"): "dang_nhap",
    ("POST", "/v1/session/auto-login"): "dang_nhap",
    ("POST", "/v1/session/auto-login-saved"): "dang_nhap",
    ("POST", "/v1/session/{profile}/auto-login-2fa-code"): "dang_nhap",
    ("GET", "/v1/session/auto-login-sessions"): "dang_nhap",
    ("GET", "/v1/session/{profile}/auto-login-status"): "dang_nhap",
    ("GET", "/v1/session/{profile}/warmup"): "dang_nhap",
    ("GET", "/v1/session/list"): "dang_nhap",
    ("GET", "/v1/session/{profile}/status"): "dang_nhap",
    ("POST", "/v1/session/{profile}/close"): "dang_nhap",
    ("GET", "/v1/profiles"): "dang_nhap",
    ("DELETE", "/v1/profiles/{profile}"): "dang_nhap",
    ("GET", "/v1/accounts/saved"): "dang_nhap",
    ("POST", "/v1/accounts/saved"): "dang_nhap",
    ("GET", "/v1/accounts/saved/{email}"): "dang_nhap",
    ("DELETE", "/v1/accounts/saved/{email}"): "dang_nhap",
    # lo_bi_mat (TẮT mặc định)
    ("GET", "/v1/accounts/saved/{email}/totp"): "lo_bi_mat",
    ("PUT", "/v1/accounts/saved/{email}/totp"): "lo_bi_mat",
    ("GET", "/v1/accounts/saved/{email}/reveal"): "lo_bi_mat",
    ("POST", "/v1/gemini-web/onboard"): "dang_nhap",
    ("GET", "/v1/gemini-web/{profile}/onboard-status"): "dang_nhap",
    ("POST", "/v1/gemini-web/{profile}/onboard-2fa-code"): "dang_nhap",
    ("POST", "/v1/gemini-web/{profile}/relogin-via-google"): "dang_nhap",
    ("GET", "/v1/gemini-web/{profile}/cookies"): "dang_nhap",
    ("POST", "/v1/openai-native/onboard"): "dang_nhap",
    ("GET", "/v1/openai-native/{profile}/onboard-status"): "dang_nhap",
    ("POST", "/v1/openai-native/{profile}/onboard-2fa-code"): "dang_nhap",
    ("GET", "/v1/openai-native/{profile}/token"): "dang_nhap",
    ("POST", "/v1/claude-web/onboard"): "dang_nhap",
    ("GET", "/v1/claude-web/{profile}/onboard-status"): "dang_nhap",
    ("POST", "/v1/claude-web/{profile}/onboard-2fa-code"): "dang_nhap",
    ("GET", "/v1/claude-web/{profile}/session"): "dang_nhap",
    ("POST", "/v1/claude-web/{profile}/relogin-via-google"): "dang_nhap",
    ("POST", "/v1/grok-web/onboard"): "dang_nhap",
    ("GET", "/v1/grok-web/{profile}/onboard-status"): "dang_nhap",
    ("POST", "/v1/grok-web/{profile}/onboard-2fa-code"): "dang_nhap",
    ("GET", "/v1/grok-web/{profile}/session"): "dang_nhap",
    ("POST", "/v1/chatgpt/onboard"): "dang_nhap",
    ("GET", "/v1/chatgpt/{profile}/onboard-status"): "dang_nhap",
    ("POST", "/v1/chatgpt/{profile}/onboard-2fa-code"): "dang_nhap",
    ("GET", "/v1/chatgpt/{profile}/refresh-jwt"): "dang_nhap",
    ("POST", "/v1/chatgpt/{profile}/relogin-via-google"): "dang_nhap",
    ("POST", "/v1/chatgpt/auto-refresh/start"): "dang_nhap",
    ("POST", "/v1/chatgpt/auto-refresh/stop"): "dang_nhap",
    ("GET", "/v1/chatgpt/auto-refresh/status"): "dang_nhap",
    ("GET", "/v1/workspaces"): "dang_nhap",
    ("POST", "/v1/workspaces"): "dang_nhap",
    ("POST", "/v1/workspaces/{profile}/open"): "dang_nhap",
    ("POST", "/v1/workspaces/{profile}/close"): "dang_nhap",
    ("POST", "/v1/workspaces/{profile}/checkbox"): "dang_nhap",

    # giai_captcha (TẮT mặc định)
    ("POST", "/v1/solve/turnstile"): "giai_captcha",
    ("POST", "/v1/solve/recaptcha2"): "giai_captcha",
    ("POST", "/v1/solve/recaptcha3"): "giai_captcha",

    # luong_anh (TẮT mặc định)
    ("POST", "/v1/google/flow/generate-image"): "luong_anh",
    ("POST", "/v1/google/flow/generate-video"): "luong_anh",
    ("POST", "/v1/google/flow/check-project"): "luong_anh",
    ("POST", "/v1/google/flow/rest/generate-image"): "luong_anh",
    ("POST", "/v1/google/flow/rest/generate-video"): "luong_anh",
    ("POST", "/v1/google/flow/rest/video-status"): "luong_anh",
    ("POST", "/v1/google/flow/get-or-create-project"): "luong_anh",

    # facebook (TẮT mặc định)
    ("POST", "/v1/facebook/group-post"): "facebook",

    # tu_dang_ky (TẮT mặc định)
    ("POST", "/v1/codex-google-onboard"): "tu_dang_ky",
    ("POST", "/v1/codex-onboard"): "tu_dang_ky",
    ("POST", "/v1/multi-onboard"): "tu_dang_ky",
    ("GET", "/v1/multi-onboard/{profile}/status"): "tu_dang_ky",

    # chay_tuy_y (TẮT mặc định)
    ("POST", "/v1/browser/run"): "chay_tuy_y",

    # ai_qua_trinh_duyet (TẮT mặc định)
    ("GET", "/v1/gemini-web/{profile}/plan"): "ai_qua_trinh_duyet",
    ("GET", "/v1/gemini-web/{profile}/models"): "ai_qua_trinh_duyet",
    ("POST", "/v1/gemini-web/chat"): "ai_qua_trinh_duyet",
    ("POST", "/v1/gemini-web/generate-image"): "ai_qua_trinh_duyet",
    ("POST", "/v1/gemini-web/generate-music"): "ai_qua_trinh_duyet",
    ("POST", "/v1/gemini-web/analyze-image"): "ai_qua_trinh_duyet",
    ("GET", "/v1/chatgpt-web/{profile}/models"): "ai_qua_trinh_duyet",
    ("POST", "/v1/chatgpt-web/chat"): "ai_qua_trinh_duyet",
    ("POST", "/v1/chatgpt-web/generate-image"): "ai_qua_trinh_duyet",
    ("POST", "/v1/chatgpt-web/analyze-image"): "ai_qua_trinh_duyet",
}


def _lay_tat_ca_routes(app):
    ds = []
    for r in app.routes:
        if hasattr(r, "path") and r.path:
            for m in r.methods:
                if m not in ("HEAD", "OPTIONS"):
                    ds.append((m, r.path, r))
        elif hasattr(r, "original_router"):
            for sub in r.original_router.routes:
                if hasattr(sub, "path") and sub.path:
                    for m in sub.methods:
                        if m not in ("HEAD", "OPTIONS"):
                            ds.append((m, sub.path, sub))
    return ds


def test_tat_ca_routes_duoc_xep_nhom_va_co_dependency():
    from src.main import app

    routes = _lay_tat_ca_routes(app)
    chua_xep = []
    thieu_dep = []

    for m, p, r in routes:
        if (m, p) not in BANG_NHOM_MONG_MUON:
            chua_xep.append(f"{m} {p}")
            continue

        nhom_mong_muon = BANG_NHOM_MONG_MUON[(m, p)]
        if nhom_mong_muon == "mo":
            continue

        deps = [d.dependency for d in getattr(r, "dependencies", [])]
        nhom_tim_thay = None
        for d in deps:
            if hasattr(d, "nhom"):
                nhom_tim_thay = d.nhom
                break
        if nhom_tim_thay is None:
            thieu_dep.append(f"{m} {p} (thiếu dependency can_tinh_nang, mong muốn: {nhom_mong_muon})")
        elif nhom_tim_thay != nhom_mong_muon:
            thieu_dep.append(f"{m} {p} (nhóm không khớp: có {nhom_tim_thay}, mong muốn: {nhom_mong_muon})")

    assert not chua_xep, f"Có route chưa được xếp nhóm: {chua_xep}"
    assert not thieu_dep, f"Lỗi dependency can_tinh_nang: {thieu_dep}"



def test_mac_dinh_nhom_tat_tra_403_tinh_nang_tat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("CAPTCHA_SOLVER_TINH_NANG_BAT", raising=False)

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)

    cac_route_thu = [
        ("POST", "/v1/solve/turnstile", {"url": "https://example.com"}),
        ("POST", "/v1/google/flow/generate-image", {}),
        ("POST", "/v1/facebook/group-post", {"group_id": "1", "message": "hi"}),
        ("POST", "/v1/codex-onboard", {}),
        ("POST", "/v1/browser/run", {"url": "https://example.com"}),
        ("POST", "/v1/gemini-web/chat", {}),
    ]

    for m, url, payload in cac_route_thu:
        res = client.request(m, url, json=payload, headers=HEADER_AUTH)
        assert res.status_code == 403, f"{m} {url} phải trả 403, nhận: {res.status_code}"
        data = res.json()
        assert data.get("ma_loi") == "tinh_nang_tat"
        assert "đang tắt" in data.get("detail", "")


def test_bat_tinh_nang_thi_khong_con_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    # Bật thêm giai_captcha
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap,giai_captcha")

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)

    with patch("src.main.solve_turnstile", new=AsyncMock(return_value={"status": "ok"})):
        res = client.post("/v1/solve/turnstile", json={"url": "https://example.com"}, headers=HEADER_AUTH)
        assert res.status_code != 403, f"Bật giai_captcha mà vẫn nhận 403: {res.status_code}"


def test_nhom_la_trong_cau_hinh_bi_bo_qua(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    import logging
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap,nhom_khong_hop_le_xyz")

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    with caplog.at_level(logging.WARNING):
        res = client.post("/v1/solve/turnstile", json={"url": "https://example.com"}, headers=HEADER_AUTH)
        assert res.status_code == 403
    assert "nhom_khong_hop_le_xyz" in caplog.text


def test_chua_co_khoa_tra_401_truoc_khi_kiem_nhom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)
    # Không có auth header -> 401, không phải 403
    res = client.post("/v1/solve/turnstile", json={"url": "https://example.com"})
    assert res.status_code == 401


def test_day_token_ngoai_mac_dinh_vo_hieu(monkeypatch: pytest.MonkeyPatch):
    import asyncio
    monkeypatch.delenv("CAPTCHA_SOLVER_TINH_NANG_BAT", raising=False)
    from src.main import _update_chatgpt2api_token

    goi_mang = 0

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            nonlocal goi_mang
            goi_mang += 1
            mock_res = MagicMock()
            mock_res.status_code = 200
            return mock_res

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        with patch("pathlib.Path.read_text", side_effect=AssertionError("Không được đọc file auth key")):
            asyncio.run(_update_chatgpt2api_token("fake_token_jwt"))
    assert goi_mang == 0, "Mặc định không được gọi mạng khi nhóm day_token_ngoai tắt"


def test_dot_bien_bo_dependency_route_do():
    """Mutation testing: nếu một route nhóm tắt bị bỏ dependency -> phát hiện ngay."""
    from src.main import app
    routes = _lay_tat_ca_routes(app)
    # Tìm một route trong nhóm tắt, ví dụ /v1/solve/turnstile
    route_target = None
    for m, p, r in routes:
        if p == "/v1/solve/turnstile":
            route_target = r
            break
    assert route_target is not None

    # Tạm thời bỏ dependencies
    old_deps = list(route_target.dependencies)
    try:
        route_target.dependencies = []
        with pytest.raises(AssertionError, match="thiếu dependency can_tinh_nang"):
            test_tat_ca_routes_duoc_xep_nhom_va_co_dependency()
    finally:
        route_target.dependencies = old_deps


def test_auto_login_cong_giai_captcha_chan_am_thanh(monkeypatch: pytest.MonkeyPatch):
    """Khi nhóm giai_captcha tắt -> luồng đăng nhập gặp reCAPTCHA KHÔNG gọi solve_recaptcha_v2_tren_trang (0 lần),
    chuyển trạng thái sang need_captcha. Khi bật giai_captcha -> được gọi 1 lần."""
    import asyncio
    from src.auto_login import LoginSession, do_google_login_steps

    async def _chay():
        # 1. Khi tắt giai_captcha (chỉ bật dang_nhap)
        monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap")

        session = LoginSession(profile="test-profile", email="nguoi.a@example.com")
        page = AsyncMock()
        page.url = "https://accounts.google.com/signin/v2/challenge/recaptcha"
        page.wait_for_load_state = AsyncMock()
        page.evaluate = AsyncMock(return_value=False)
        ctx = MagicMock()

        def mock_locator(sel, **kwargs):
            loc = AsyncMock()
            if sel == "body":
                loc.inner_text = AsyncMock(return_value="xác minh recaptcha")
            elif "recaptcha" in sel:
                loc.count = AsyncMock(return_value=1)
                loc.first.is_visible = AsyncMock(return_value=True)
            else:
                loc.count = AsyncMock(return_value=0)
                loc.first.is_visible = AsyncMock(return_value=False)
            return loc

        page.locator = MagicMock(side_effect=mock_locator)

        mock_solve = AsyncMock()
        mock_tich = AsyncMock(return_value=False)

        with patch("src.solvers.recaptcha.tich_o_recaptcha", mock_tich):
            with patch("src.solvers.recaptcha.solve_recaptcha_v2_tren_trang", mock_solve):
                try:
                    await asyncio.wait_for(
                        do_google_login_steps(session, page, ctx, password="test-password"),
                        timeout=1.0,
                    )
                except (asyncio.TimeoutError, Exception):
                    pass

        assert mock_solve.call_count == 0, f"giai_captcha tắt mà bộ giải vẫn bị gọi {mock_solve.call_count} lần"
        assert session.state == "need_captcha", f"Trạng thái session kỳ vọng 'need_captcha', nhận '{session.state}'"

        # 2. Khi bật giai_captcha
        monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap,giai_captcha")
        session2 = LoginSession(profile="test-profile-2", email="nguoi.a@example.com")
        mock_solve2 = AsyncMock()

        with patch("src.solvers.recaptcha.tich_o_recaptcha", mock_tich):
            with patch("src.solvers.recaptcha.solve_recaptcha_v2_tren_trang", mock_solve2):
                try:
                    await asyncio.wait_for(
                        do_google_login_steps(session2, page, ctx, password="test-password"),
                        timeout=0.8,
                    )
                except (asyncio.TimeoutError, Exception):
                    pass

        assert mock_solve2.call_count == 1, f"Bật giai_captcha mà bộ giải không được gọi (gọi {mock_solve2.call_count} lần)"

    asyncio.run(_chay())


def test_lo_bi_mat_mac_dinh_tat_tra_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mặc định GET reveal, GET totp, PUT totp trả 403 tinh_nang_tat kể cả khi có khoá và email đúng;
    bật lo_bi_mat mới chạy; luồng nội bộ resolve_account vẫn tra được credential không cần nhóm này."""
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.db"))
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap")

    from fastapi.testclient import TestClient
    from src.accounts_db import resolve_account, save_account
    from src.main import app

    save_account("nguoi.a@example.com", "mat-khau-test", "JBSWY3DPEHPK3PXP", "label-test")

    client = TestClient(app)

    # 1. Mặc định nhóm lo_bi_mat tắt -> 403 tinh_nang_tat
    res_reveal = client.get("/v1/accounts/saved/nguoi.a@example.com/reveal", headers=HEADER_AUTH)
    assert res_reveal.status_code == 403, f"GET reveal phải 403, nhận {res_reveal.status_code}"
    assert res_reveal.json().get("ma_loi") == "tinh_nang_tat"

    res_totp = client.get("/v1/accounts/saved/nguoi.a@example.com/totp", headers=HEADER_AUTH)
    assert res_totp.status_code == 403, f"GET totp phải 403, nhận {res_totp.status_code}"
    assert res_totp.json().get("ma_loi") == "tinh_nang_tat"

    res_set_totp = client.put(
        "/v1/accounts/saved/nguoi.a@example.com/totp",
        json={"totp_secret": "JBSWY3DPEHPK3PXP"},
        headers=HEADER_AUTH,
    )
    assert res_set_totp.status_code == 403, f"PUT totp phải 403, nhận {res_set_totp.status_code}"
    assert res_set_totp.json().get("ma_loi") == "tinh_nang_tat"

    # 2. Kiểm tra rằng luồng nội bộ resolve_account vẫn đọc được credential mà KHÔNG cần lo_bi_mat
    acct = resolve_account("nguoi.a@example.com")
    assert acct is not None
    assert acct["password"] == "mat-khau-test"
    assert acct["totp_secret"] == "JBSWY3DPEHPK3PXP"

    # 3. Khi bật lo_bi_mat -> các endpoint hoạt động bình thường
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap,lo_bi_mat")
    res_reveal_ok = client.get("/v1/accounts/saved/nguoi.a@example.com/reveal", headers=HEADER_AUTH)
    assert res_reveal_ok.status_code == 200
    assert res_reveal_ok.json()["password"] == "mat-khau-test"

    res_totp_ok = client.get("/v1/accounts/saved/nguoi.a@example.com/totp", headers=HEADER_AUTH)
    assert res_totp_ok.status_code == 200
    assert "code" in res_totp_ok.json()


def test_health_novnc_an_khi_tat_hien_khi_bat(monkeypatch: pytest.MonkeyPatch):
    """Mặc định noVNC tắt: GET /health trả 'novnc': None; khi đặt CAPTCHA_SOLVER_BAT_NOVNC=1 mới trả URL."""
    from fastapi.testclient import TestClient
    from src.main import app

    client = TestClient(app)

    # 1. Mặc định không bật biến CAPTCHA_SOLVER_BAT_NOVNC
    monkeypatch.delenv("CAPTCHA_SOLVER_BAT_NOVNC", raising=False)
    r1 = client.get("/health")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"
    assert r1.json().get("novnc") is None, f"Mặc định novnc phải là None, nhận: {r1.json().get('novnc')}"

    # 2. Khi bật biến CAPTCHA_SOLVER_BAT_NOVNC
    monkeypatch.setenv("CAPTCHA_SOLVER_BAT_NOVNC", "1")
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert r2.json().get("novnc") is not None
    assert "6080" in r2.json()["novnc"]


def test_a3_api_delete_profile_an_toan_duong_dan(monkeypatch: pytest.MonkeyPatch):
    """A3: api_delete_profile phải dùng _profile_dir/_PROFILE_SLUG + resolve(), từ chối ký tự ngoài slug bằng 400."""
    from fastapi.testclient import TestClient
    from src.main import app
    client = TestClient(app)

    # Bật tính năng dang_nhap và đặt khoá API test
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap")
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)

    # Các tên hồ sơ sai slug (chứa khoảng trắng, ký tự đặc biệt, unicode, dấu chấm...)
    ten_sai = [
        "profile with space",
        "profile!@#",
        "profile$123",
        "profile.dot",
        "a" * 65,  # Vượt quá 64 ký tự
    ]
    for ten in ten_sai:
        r = client.delete(f"/v1/profiles/{ten}", headers=HEADER_AUTH)
        assert r.status_code == 400, f"Hồ sơ '{ten}' phải bị từ chối 400, nhận: {r.status_code}"


# ── Mục E3: Đột biến 2 & 3 ────────────────────────────────────────────────────

def test_e3_dot_bien_2_va_3_get_account_khong_ro_password_va_totp(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """E3 (2 & 3): GET /v1/accounts/saved/{email} tuyệt đối KHÔNG trả password và KHÔNG trả dict(acct) rò totp_secret hay khoá."""
    from fastapi.testclient import TestClient
    from src.main import app
    from src.accounts_db import save_account

    data_dir = tmp_path / "acct_test"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAPTCHA_SOLVER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CAPTCHA_SOLVER_TINH_NANG_BAT", "dang_nhap")
    monkeypatch.setenv("CAPTCHA_SOLVER_API_KEY", KHOA_TEST)

    email = "test.mutations@example.com"
    mk_bi_mat = "MatKhauTuyetMat_KhongDuocRo_123"
    totp_bi_mat = "JBSWY3DPEHPK3PXP"
    save_account(email, mk_bi_mat, totp_bi_mat, "test_label", "google")

    client = TestClient(app)
    r = client.get(f"/v1/accounts/saved/{email}", headers=HEADER_AUTH)
    assert r.status_code == 200
    du_lieu = r.json()

    # Đột biến 2: Nếu trả password -> fail
    assert "password" not in du_lieu, f"Endpoint không được chứa trường 'password': {du_lieu}"
    assert mk_bi_mat not in str(du_lieu), "Mật khẩu thô bị rò rỉ trong phản hồi!"

    # Đột biến 3: Nếu trả dict(acct) -> fail (chứa totp_secret, hash hoặc các trường nội bộ)
    assert "totp_secret" not in du_lieu, f"Endpoint không được chứa 'totp_secret': {du_lieu}"
    assert totp_bi_mat not in str(du_lieu), "Hạt giống TOTP bị rò rỉ trong phản hồi!"
    cac_khoa_cam = {"password", "totp_secret", "khoa", "secret", "private_key", "salt"}
    assert not any(k in du_lieu for k in cac_khoa_cam), f"Phát hiện trường nhạy cảm trong phản hồi: {du_lieu}"


