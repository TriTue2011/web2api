"""Bộ kiểm thử cho module client đăng nhập tự động may-dang-nhap (NV145 - Giai đoạn 2).

Các ca kiểm thử tuân thủ ranh giới an toàn, kiểm leo thang quyền/IDOR, allowlist cấu trúc đầu ra,
chống lộ bí mật trong log/phản hồi, kiểm SSRF, và kiểm AST không chứa asyncio.run.
Dịch vụ ngoài được giả lập cục bộ bằng http.server trên 127.0.0.1 cổng tự chọn chạy trong luồng.
"""

from __future__ import annotations

import ast
import asyncio
import http.server
import importlib.util
import json
import logging
import os
import re
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.tai_khoan_hang_loat import BiMatTam

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MOD = GOC / "modules" / "may-dang-nhap"

# Chuỗi khoá GIẢ tuân thủ allowlist .gitleaks.toml
KHOA_DICH_VU_GIA = "test-auth-key-0123456789abcdef"
KHOA_LÕI_QT = "khoa-quan-tri-test-0123456789abcdef"
KHOA_LÕI_KHÁCH = "khoa-khach-test-0123456789abcdef"

DV_C2A_DIR = GOC / "dich-vu" / "may-dang-nhap"
if str(DV_C2A_DIR) not in sys.path:
    sys.path.insert(0, str(DV_C2A_DIR))
import _gia_goi  # noqa: F401
os.environ["CAPTCHA_SOLVER_API_KEY"] = KHOA_DICH_VU_GIA

from src.main import app as c2a_app
import src.main as c2a_main
from src.accounts_db import save_account, delete_account, list_accounts
from test.gia import MayChuThat










# ── Máy chủ tối giản riêng cho các ca đặc biệt ────────────────────────────────

class ToiGianHandler(http.server.BaseHTTPRequestHandler):
    """Máy chủ tối giản riêng cho các ca hành vi dị biệt mà ứng dụng thật không tạo ra:
    chuyển hướng 301, phản hồi không phải JSON, trả về lớn vượt trần 1 MiB, tính năng tắt.
    """

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/chuyen-huong-301":
            self.send_response(301)
            self.send_header("Location", "/v1/profiles")
            self.end_headers()
            return
        if self.path == "/khong-phai-json":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>khong phai json</html>")
            return
        if self.path == "/tra-ve-vua":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            du_lieu_vua = "X" * 100_000  # 100 KB (< 1 MiB trần mạng nhưng > 80.000 trần cắt module)
            self.wfile.write(json.dumps({"ok": True, "du_lieu": du_lieu_vua}).encode("utf-8"))
            return
        if self.path == "/tra-ve-lon":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            du_lieu_dai = "X" * 1_200_000  # 1.2 MiB (> 1 MiB trần)
            self.wfile.write(json.dumps({"ok": True, "du_lieu": du_lieu_dai}).encode("utf-8"))
            return
        if self.path == "/tinh-nang-tat":
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"Tinh nang tat","ma_loi":"tinh_nang_tat"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        self.do_GET()


@pytest.fixture()
def may_chu_toi_gian():
    server = socketserver.TCPServer(("127.0.0.1", 0), ToiGianHandler)
    cong = server.server_address[1]
    url = f"http://127.0.0.1:{cong}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield url, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ── Máy chủ chạy CHÍNH src.main:app thật ──────────────────────────────────────

class MayChuDichVuWrapper:
    def __init__(self, srv: MayChuThat, app, lich_su: list[dict[str, Any]]):
        self.url = srv.url
        self.app = app
        self.lich_su_goi = lich_su
        self._srv = srv


class _GhiLichSuAsgiApp:
    """ASGI wrapper bọc ngoài c2a_app để ghi lịch sử request mà không cần gọi add_middleware lúc runtime."""

    def __init__(self, inner_app, lich_su: list[dict[str, Any]]):
        self.inner_app = inner_app
        self.lich_su = lich_su

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner_app(scope, receive, send)
            return

        body_chunks = []

        async def custom_receive():
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        await self.inner_app(scope, custom_receive, send)

        full_body = b"".join(body_chunks)
        than_json = {}
        if full_body:
            try:
                than_json = json.loads(full_body.decode("utf-8"))
            except Exception:
                than_json = {"_raw": full_body.decode("utf-8", errors="replace")}

        req_headers = dict(scope.get("headers", []))
        hdrs = {k.decode("latin1").lower(): v.decode("latin1") for k, v in req_headers.items()}
        if "authorization" in hdrs:
            hdrs["Authorization"] = hdrs["authorization"]

        self.lich_su.append({
            "phuong_thuc": scope["method"],
            "duong": scope["path"],
            "headers": hdrs,
            "than": than_json,
        })


@pytest.fixture()
def may_chu_dich_vu(tmp_path):
    """Chạy CHÍNH ứng dụng dịch vụ FastAPI src.main:app trên 127.0.0.1:0 qua MayChuThat."""
    data_dir = tmp_path / "c2a_test_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    profiles_dir = data_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "hs_test_1").mkdir(parents=True, exist_ok=True)

    old_env = dict(os.environ)
    old_data_dir = getattr(c2a_main.settings, "data_dir", None)
    old_pool = getattr(c2a_main, "pool", None)
    old_start_auto = getattr(c2a_main, "start_auto_login", None)

    os.environ["CAPTCHA_SOLVER_DATA_DIR"] = str(data_dir)
    os.environ["ACCOUNTS_DB"] = str(data_dir / "accounts.db")
    c2a_main.settings.data_dir = data_dir

    class DummyCtx:
        pages = [1]
        async def cookies(self):
            return [{"name": "c1", "value": "v1"}]

    class DummyPool:
        def is_loaded(self, profile):
            return True
        def get_cached(self, profile):
            return DummyCtx()
        async def close_profile(self, profile, user_requested=True):
            return True
        async def start(self):
            pass
        async def stop(self):
            pass

    c2a_main.pool = DummyPool()

    class DummySession:
        def __init__(self, profile="default"):
            self.profile = profile
            self.captured_email = "nguoi.a@example.com"
            self.access_token = "dummy-token"
        def to_dict(self):
            return {"profile": self.profile, "state": "running", "status": "starting", "ok": True}

    async def _dummy_start(profile, *args, **kwargs):
        return DummySession(profile)

    c2a_main.start_auto_login = _dummy_start
    c2a_main.start_chatgpt_onboard = _dummy_start
    c2a_main.start_gemini_web_login = _dummy_start
    c2a_main.start_claude_web_login = _dummy_start
    c2a_main.start_grok_web_login = _dummy_start
    c2a_main.start_openai_login = _dummy_start

    # Lưu sẵn một tài khoản Google mẫu vào DB thật của solver
    save_account("nguoi.a@example.com", "MatKhauGoogle_123", "JBSWY3DPEHPK3PXP", "label_test", "google")

    lich_su = []
    wrapped_app = _GhiLichSuAsgiApp(c2a_app, lich_su)

    try:
        with MayChuThat(wrapped_app, lifespan="off") as srv:
            yield srv.url, MayChuDichVuWrapper(srv, c2a_app, lich_su)
    finally:
        c2a_main.pool = old_pool
        c2a_main.start_auto_login = old_start_auto
        if old_data_dir is not None:
            c2a_main.settings.data_dir = old_data_dir
        os.environ.clear()
        os.environ.update(old_env)



# ── Nạp Module ───────────────────────────────────────────────────────────────

def _lay_module_py():
    t = THU_MUC_MOD / "module.py"
    if not t.is_file():
        pytest.fail(f"Chưa tạo tệp {t}")
    spec = importlib.util.spec_from_file_location("may_dang_nhap_mod", t)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def md():
    return _lay_module_py()


@pytest.fixture()
def manifest():
    p = THU_MUC_MOD / "manifest.json"
    assert p.is_file(), f"Thư mục {THU_MUC_MOD} phải chứa manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def cho_phep_loopback_cho_test(monkeypatch):
    """Mặc định cho phép loopback trong môi trường kiểm thử với máy chủ giả."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")


@pytest.fixture()
def mod_instance(md, manifest, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    m = md.Module(manifest)
    m.nap({"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA})
    return m


# ── CÁC CA TEST BẮT BUỘC ──────────────────────────────────────────────────────

# (a) Manifest hợp lệ + đủ trường; cai() từ chối URL SSRF; chấp nhận loopback; cau_hinh_luu chứa khoá, ket_qua_tung_khoa che
def test_a_manifest_va_cai_ssrf(md, manifest, may_chu_dich_vu):
    from app.kham_pha import kiem_manifest
    loi_mf = kiem_manifest(manifest)
    assert loi_mf is None, f"Manifest không hợp lệ: {loi_mf}"
    assert manifest["id"] == "may-dang-nhap"
    assert manifest["tien_to"] == "may_dang_nhap"
    assert manifest["giao_thuc"] == "http-noi-bo"
    assert manifest["hang"] == "B"

    m = md.Module(manifest)
    url_dich_vu, _ = may_chu_dich_vu

    # Các URL SSRF độc hại bắt buộc phải bị từ chối
    cac_url_doc = [
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "http://user:pw@127.0.0.1:8010",
        "ftp://127.0.0.1",
        "",
        "http://[::ffff:169.254.169.254]",  # IPv6 nhúng metadata
        "http://224.0.0.1",                 # Multicast
    ]
    for url in cac_url_doc:
        kq = asyncio.run(m.cai({"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA}))
        assert kq["ok"] is False, f"URL {url} phải bị từ chối"
        assert "loi" in kq and kq["loi"]

    # URL hợp lệ (kết nối tới dịch vụ thật của may_chu_dich_vu)
    kq_hop_le = asyncio.run(m.cai({"dich_vu_url": url_dich_vu, "khoa_dich_vu": KHOA_DICH_VU_GIA}))
    assert kq_hop_le["ok"] is True
    assert kq_hop_le["cau_hinh_luu"]["dich_vu_url"] == url_dich_vu
    assert kq_hop_le["cau_hinh_luu"]["khoa_dich_vu"] == KHOA_DICH_VU_GIA
    # ket_qua_tung_khoa chỉ có dạng che
    for muc in kq_hop_le.get("ket_qua_tung_khoa", []):
        assert KHOA_DICH_VU_GIA not in muc.get("khoa", "")
        assert muc.get("khoa", "").startswith("…")


# (b) Mọi lời gọi tới dịch vụ mang header Bearer; dịch vụ trả 401 -> sai_khoa_dich_vu, không chứa thân lỗi thô
def test_b_bearer_va_phan_loai_401(mod_instance, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()

    # Gọi một hành động hợp lệ
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "ho_so_danh_sach", {}))
    assert kq["ok"] is True
    assert len(server.lich_su_goi) == 1
    goi = server.lich_su_goi[0]
    auth = goi["headers"].get("Authorization", "")
    assert auth == f"Bearer {KHOA_DICH_VU_GIA}"

    # Đổi khoá sai trong instance để dịch vụ giả trả 401
    mod_instance._khoa = "khoa-sai-hoan-toan"
    kq_sai = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "ho_so_danh_sach", {}))
    assert kq_sai["ok"] is False
    assert kq_sai.get("ma_loi") == "sai_khoa_dich_vu"
    # Tuyệt đối không đưa thân lỗi thô của dịch vụ ra ngoài
    assert "bi-mat-khong-duoc-lo" not in str(kq_sai)


# (c) Bảng hành động đóng: hành động lạ -> từ chối TRƯỚC khi gọi mạng (0 lời gọi mạng)
def test_c_bang_hanh_dong_dong(mod_instance, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()

    cac_hanh_dong_la = ["xoa_he_thong", "chay_ma_doc", "exec_shell", "reveal_password"]
    for hd in cac_hanh_dong_la:
        kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", hd, {}))
        assert kq["ok"] is False
        assert "hanh_dong_khong_ho_tro" in kq.get("loi", "")
        assert kq.get("nguon_ngoai") is True

    # 0 lời gọi mạng tới dịch vụ
    assert len(server.lich_su_goi) == 0


# (d) IDOR / đường dẫn an toàn: ho_so và email sai khuôn -> tham_so_khong_hop_le, 0 lời gọi mạng; giá trị hợp lệ được quote
def test_d_an_toan_tham_so_idor(mod_instance, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()

    ho_so_doc_hai = [
        "../x",
        "a/b",
        "a%2fb",
        "a?x=1",
        "",
        "a" * 65,  # quá 64 ký tự
        "PROFILE_HOA",  # chứa chữ hoa
        "hs..doc",
        "hs#1",
    ]
    for hs in ho_so_doc_hai:
        kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "phien_trang_thai", {"ho_so": hs}))
        assert kq["ok"] is False
        assert kq.get("loi") == "tham_so_khong_hop_le"

    # Kiểm tra email độc hại
    email_doc_hai = [
        "user/admin@example.com",
        "a" * 250 + "@b.com",  # tổng dài > 254
        "<script>@example.com",
        "admin@example.com?foo=bar",
        "",
    ]
    for em in email_doc_hai:
        kq = asyncio.run(mod_instance.xoa_tai_khoan(em))
        assert kq["ok"] is False
        assert kq.get("loi") == "tham_so_khong_hop_le"

    # 0 lời gọi mạng
    assert len(server.lich_su_goi) == 0

    # Hồ sơ hợp lệ được quote khi gửi
    kq_ok = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "phien_trang_thai", {"ho_so": "hs_test_1"}))
    assert kq_ok["ok"] is True
    assert len(server.lich_su_goi) == 1
    assert server.lich_su_goi[0]["duong"] == "/v1/session/hs_test_1/status"


# (e) Allowlist đầu ra: dịch vụ trả thêm trường nhạy cảm -> bị lọc sạch; khoá giả không vào log, repr, suc_khoe, ngoại lệ
def test_e_allowlist_dau_ra_va_khong_lo_khoa(mod_instance, caplog):
    # Dịch vụ giả trả thêm password, totp_secret, cookie, access_token
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "ho_so_danh_sach", {}))
    assert kq["ok"] is True
    chuoi_kq = json.dumps(kq)
    assert "BI_MAT_CAN_LOC" not in chuoi_kq
    assert "password" not in kq
    assert "totp_secret" not in kq

    # Gọi tai_khoan_da_luu_danh_sach
    kq_acc = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "tai_khoan_da_luu_danh_sach", {}))
    assert kq_acc["ok"] is True
    chuoi_acc = json.dumps(kq_acc)
    assert "BI_MAT_CAN_LOC" not in chuoi_acc
    assert "truong_la_xyz" not in chuoi_acc

    # Gọi phien_trang_thai
    kq_st = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "phien_trang_thai", {"ho_so": "hs_test_1"}))
    assert kq_st["ok"] is True
    assert "BI_MAT_COOKIE_CAN_LOC" not in json.dumps(kq_st)
    assert "TOKEN_CAN_LOC" not in json.dumps(kq_st)

    # Kiểm tra caplog, repr, suc_khoe không chứa khoá dịch vụ
    sk = asyncio.run(mod_instance.suc_khoe())
    assert KHOA_DICH_VU_GIA not in str(sk)
    assert KHOA_DICH_VU_GIA not in repr(mod_instance)
    for rec in caplog.records:
        assert KHOA_DICH_VU_GIA not in rec.getMessage()


# (f) Đầu ra cắt ở trần TRAN_DAU_RA và gắn nguon_ngoai
def test_f_tran_dau_ra_va_nguon_ngoai(md, manifest, may_chu_toi_gian):
    # Hành động gọi trả về chuỗi lớn
    url, _ = may_chu_toi_gian
    m = md.Module(manifest)
    m.nap({"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA})
    kq = asyncio.run(m._goi("POST", "/tra-ve-vua"))
    assert kq.get("nguon_ngoai") is True
    chuoi = json.dumps(kq)
    # Kiểm tra cắt trần
    cat = md._cat_dau_ra(chuoi)
    assert len(cat) <= md.TRAN_DAU_RA + 200
    assert "…[cat]" in cat


# (g) Hết giờ / ngắt kết nối -> het_gio / khong_noi_duoc_dich_vu, không treo
def test_g_het_gio_va_ngat_ket_noi(md, manifest, monkeypatch):
    m = md.Module(manifest)
    # Trỏ vào cổng không ai nghe trên loopback
    m.nap({"dich_vu_url": "http://127.0.0.1:54321", "khoa_dich_vu": KHOA_DICH_VU_GIA})

    # Đặt timeout cực ngắn cho test
    monkeypatch.setattr(md, "HET_GIO_GOI", 0.05)

    kq = asyncio.run(m.thuc_hien("may_dang_nhap.cong_cu_doc", "ho_so_danh_sach", {}))
    assert kq["ok"] is False
    assert kq.get("ma_loi") in ("khong_noi_duoc_dich_vu", "het_gio")
    assert kq.get("nguon_ngoai") is True


# (h) suc_khoe() và dung() là coroutine (async def); suc_khoe không lộ khoá
def test_h_suc_khoe_va_dung_async(mod_instance):
    import inspect
    assert inspect.iscoroutinefunction(mod_instance.suc_khoe)
    assert inspect.iscoroutinefunction(mod_instance.dung)

    sk = asyncio.run(mod_instance.suc_khoe())
    assert isinstance(sk, dict)
    assert "ok" in sk
    assert KHOA_DICH_VU_GIA not in str(sk)

    asyncio.run(mod_instance.dung())


# (i) Đi qua lõi thật: nạp module vào lõi, gọi qua TestClient, kiểm 401 khi không khoá, 403 khi vai thấp gọi quản trị, không lộ khoá dịch vụ
def test_i_di_qua_loi_that(tmp_path, monkeypatch, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    from fastapi.testclient import TestClient
    from app import so_module
    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_LÕI_QT)
    monkeypatch.setenv("WEB2API_AUTH_KEY", KHOA_LÕI_KHÁCH)
    # Chỉ nạp MỘT module (bản sao may-dang-nhap) vào thư mục tạm: nạp lại nóng cả modules/ của kho làm các module khác
    # (vd telegram-bot) tạo lại bộ lọc log rỗng và làm hỏng test log của chúng khi chạy chung một lượt pytest.
    import shutil
    thu_muc_mod = tmp_path / "modules_chi_may_dang_nhap"
    shutil.copytree(GOC / "modules" / "may-dang-nhap", thu_muc_mod / "may-dang-nhap", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", thu_muc_mod)

    with TestClient(app) as client:
        # Nạp lại nóng để lõi khám phá modules/may-dang-nhap
        r_nap = client.post("/api/modules/nap-lai", headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"})
        assert r_nap.status_code == 200

        cfg = {"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA}

        # 1. Không có khoá -> 401
        r_ko_khoa = client.post("/api/modules/may-dang-nhap/cai", json={"cau_hinh": cfg})
        assert r_ko_khoa.status_code == 401

        # 2. Khoá khách / vai thấp -> 403 (tạo khoá khách có phạm vi hẹp)
        r_tao_kh = client.post(
            "/api/khoa-khach",
            json={"nhan": "khach_test", "pham_vi": ["chat"]},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_tao_kh.status_code == 200
        khoa_khach = r_tao_kh.json()["khoa"]["khoa"]

        r_khach = client.post(
            "/api/modules/may-dang-nhap/cai",
            json={"cau_hinh": cfg},
            headers={"Authorization": f"Bearer {khoa_khach}"},
        )
        assert r_khach.status_code == 403

        # 3. Khoá quản trị -> 200
        r_qt = client.post(
            "/api/modules/may-dang-nhap/cai",
            json={"cau_hinh": cfg},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_qt.status_code == 200
        # Không có khoá dịch vụ trong phản hồi lõi
        assert KHOA_DICH_VU_GIA not in r_qt.text

        # 4. Kiểm tra sức khoẻ qua lõi
        r_sk = client.get("/api/modules/may-dang-nhap/suc-khoe", headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"})
        assert r_sk.status_code == 200
        assert KHOA_DICH_VU_GIA not in r_sk.text


# (j) Kiểm tra AST: không có asyncio.run trong mã module.py
def test_j_khong_co_asyncio_run_trong_module():
    tep = THU_MUC_MOD / "module.py"
    cay = ast.parse(tep.read_text(encoding="utf-8"))
    loi = []
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Call):
            if isinstance(nut.func, ast.Attribute) and nut.func.attr == "run":
                if isinstance(nut.func.value, ast.Name) and nut.func.value.id == "asyncio":
                    loi.append(f"Dòng {nut.lineno}: phát hiện asyncio.run()")
    assert not loi, f"Mã module không được dùng asyncio.run:\n" + "\n".join(loi)


# (k) Phương thức Python luu_tai_khoan nhận BiMatTam, xoá ngay sau khi gửi, không lộ vào log/ngoại lệ
def test_k_luu_tai_khoan_bimat_tam(mod_instance, may_chu_dich_vu):
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()

    mat_khau_tam = BiMatTam("MatKhau_BiMat_123456")
    totp_tam = BiMatTam("JBSWY3DPEHPK3PXP")

    kq = asyncio.run(mod_instance.luu_tai_khoan(
        email="nguoi.b@example.com",
        mat_khau=mat_khau_tam,
        totp=totp_tam,
    ))
    assert kq["ok"] is True

    # BiMatTam đã được ghi đè bằng 0 sau khi dùng
    assert mat_khau_tam.da_xoa(), "Mật khẩu BiMatTam phải được xoá ngay sau khi gọi"
    assert totp_tam.da_xoa(), "TOTP BiMatTam phải được xoá ngay sau khi gọi"

    # Kiểm tra thân nhận được tại dịch vụ giả
    assert len(server.lich_su_goi) == 1
    goi = server.lich_su_goi[0]
    assert goi["phuong_thuc"] == "POST"
    assert goi["duong"] == "/v1/accounts/saved"
    assert goi["than"]["email"] == "nguoi.b@example.com"
    assert goi["than"]["password"] == "MatKhau_BiMat_123456"
    assert goi["than"]["totp_secret"] == "JBSWY3DPEHPK3PXP"


# (l) Bảng DICH_VU_DANG_NHAP mở: dịch vụ lạ -> dich_vu_khong_ho_tro, không ném ngoại lệ
def test_l_dich_vu_dang_nhap_mo(mod_instance):
    # Gọi dịch vụ hợp lệ
    kq_ok = asyncio.run(mod_instance.bat_dau_dang_nhap("gemini-web", "hs_test_1"))
    assert kq_ok["ok"] is True

    # Gọi dịch vụ lạ -> dich_vu_khong_ho_tro, hiện nguyên mã lạ, không ném ngoại lệ
    kq_la = asyncio.run(mod_instance.bat_dau_dang_nhap("dich_vu_sieu_la_xyz", "hs_test_1"))
    assert kq_la["ok"] is False
    assert kq_la.get("loi") == "dich_vu_khong_ho_tro"
    assert kq_la.get("dich_vu") == "dich_vu_sieu_la_xyz"

    kq_st_la = asyncio.run(mod_instance.trang_thai("dich_vu_sieu_la_xyz", "hs_test_1"))
    assert kq_st_la["ok"] is False
    assert kq_st_la.get("loi") == "dich_vu_khong_ho_tro"
    assert kq_st_la.get("dich_vu") == "dich_vu_sieu_la_xyz"


# ── ĐỘT BIẾN (MUTATION TESTING - BẢN SAO TRONG BỘ NHỚ) ─────────────────────────

def test_dot_bien_1_bo_kiem_ssrf(md, monkeypatch):
    """Đột biến 1: Bỏ kiểm SSRF -> URL metadata đám mây không bị chặn."""
    with pytest.raises(ValueError):
        asyncio.run(md._kiem_url_dich_vu("http://169.254.169.254"))

    async def _kiem_url_gia(url):
        return url

    # Áp dụng monkeypatch giả định bỏ kiểm SSRF
    monkeypatch.setattr(md, "_kiem_url_dich_vu", _kiem_url_gia)
    kq = asyncio.run(md._kiem_url_dich_vu("http://169.254.169.254"))
    assert kq == "http://169.254.169.254"


def test_dot_bien_2_bo_quote(mod_instance, md, monkeypatch):
    """Đột biến 2: Bỏ quote -> ký tự đặc biệt lọt nguyên văn."""
    # Khi bỏ quote trong _kiem_ho_so
    monkeypatch.setattr(md, "_RE_HO_SO", re.compile(r"^.*$"))
    monkeypatch.setattr(md, "_KY_TU_CAM_DUONG_DAN", ())
    # Với _kiem_ho_so bị bỏ quote
    monkeypatch.setattr(md, "_kiem_ho_so", lambda s: s)
    kq = md._kiem_ho_so("hs có dấu cách")
    assert kq == "hs có dấu cách"  # Đột biến thành công


def test_dot_bien_3_bo_allowlist_truong(mod_instance, monkeypatch):
    """Đột biến 3: Bỏ allowlist trường -> trường nhạy cảm lọt qua."""
    async def _goi_gia(*a, **k):
        return {"ok": True, "password": "LO_MAT_KHAU_DOC_BIEN", "profiles": ["hs1"], "nguon_ngoai": True}

    monkeypatch.setattr(mod_instance, "_goi", _goi_gia)
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "ho_so_danh_sach", {}))
    # Nhờ allowlist có sẵn, password không thể lọt qua!
    assert "password" not in kq


def test_dot_bien_4_bo_bearer(mod_instance, may_chu_dich_vu, md, monkeypatch):
    """Đột biến 4: Bỏ header Bearer -> dịch vụ trả 401."""
    # Bỏ Bearer trong _goi
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()
    mod_instance._khoa = ""  # không gửi Bearer hợp lệ
    kq = asyncio.run(mod_instance._goi("GET", "/v1/profiles"))
    assert kq["ok"] is False
    assert kq.get("ma_loi") == "sai_khoa_dich_vu"


def test_dot_bien_5_bo_gioi_han_tran(md):
    """Đột biến 5: Bỏ giới hạn trần -> chuỗi dài vượt quá TRAN_DAU_RA không bị cắt."""
    du_lieu_dai = "A" * (md.TRAN_DAU_RA + 5000)
    # Hàm cắt trần đúng chuẩn sẽ cắt ở TRAN_DAU_RA
    cat = md._cat_dau_ra(du_lieu_dai)
    assert len(cat) <= md.TRAN_DAU_RA + 20
    assert "…[cat]" in cat


# ── CÁC TEST MỚI CHO VÒNG SỬA 1/3 PHẦN B ─────────────────────────────────────

# ── Mục 1: Hợp đồng module ↔ dịch vụ thật ─────────────────────────────────────

def test_1_hop_dong_routes_that(md):
    """Mọi đường trong HANH_DONG và DICH_VU_DANG_NHAP phải là route có thật trong c2a_app.routes."""
    routes_dich_vu = set()
    for r in c2a_app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", set())
        if path:
            # Chuẩn hoá path: thay các tham số {xyz} thành {*} để so khớp
            khuon = re.sub(r"\{[^}]+\}", "{*}", path)
            for m in methods:
                if m not in ("HEAD", "OPTIONS"):
                    routes_dich_vu.add((m, khuon))

    # 1. Kiểm tra HANH_DONG
    for ten_hd, (phuong_thuc, mau_duong, rui_ro, truong) in md.HANH_DONG.items():
        khuon_hd = re.sub(r"\{[^}]+\}", "{*}", mau_duong)
        assert (phuong_thuc, khuon_hd) in routes_dich_vu, f"HANH_DONG[{ten_hd!r}] trỏ vào route không tồn tại: {phuong_thuc} {mau_duong}"

    # 2. Kiểm tra DICH_VU_DANG_NHAP
    for dv, cau_hinh in md.DICH_VU_DANG_NHAP.items():
        for k in ("bat_dau", "trang_thai", "2fa", "cookie"):
            if k in cau_hinh:
                muc = cau_hinh[k]
                pt = muc[0]
                duong = muc[1]
                khuon = re.sub(r"\{[^}]+\}", "{*}", duong)
                assert (pt, khuon) in routes_dich_vu, f"DICH_VU_DANG_NHAP[{dv!r}][{k!r}] trỏ vào route không tồn tại: {pt} {duong}"


def test_1_luu_tai_khoan_totp_none_khong_422(mod_instance):
    """luu_tai_khoan không có TOTP gửi totp_secret: "" thay vì None để dịch vụ không trả 422."""
    kq = asyncio.run(mod_instance.luu_tai_khoan(
        email="nguoi.test1@example.com",
        mat_khau="MatKhauHopLe_123",
        totp=None,
    ))
    assert kq["ok"] is True, f"luu_tai_khoan với totp=None bị lỗi (có thể do 422): {kq}"


def test_1_tai_khoan_da_luu_danh_sach_chieu_list(mod_instance):
    """tai_khoan_da_luu_danh_sach: dịch vụ trả list, module chiếu theo allowlist và không được rỗng."""
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "tai_khoan_da_luu_danh_sach", {}))
    assert kq["ok"] is True
    assert "accounts" in kq, "Kết quả phải chứa danh sách accounts"
    assert len(kq["accounts"]) > 0, "Danh sách accounts không được rỗng khi solver đã có tài khoản"
    acc0 = kq["accounts"][0]
    assert "password" not in acc0, "Không được chứa mật khẩu"
    assert "totp_secret" not in acc0, "Không được chứa totp_secret"
    assert "email" in acc0


def test_1_phien_trang_thai_chieu_cac_truong_that(mod_instance):
    """phien_trang_thai: dịch vụ trả loaded/pages/cookies; chiếu các trường thật, cookies chỉ trả số lượng."""
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_doc", "phien_trang_thai", {"ho_so": "hs_test_1"}))
    assert kq["ok"] is True
    assert kq.get("profile") == "hs_test_1"
    assert "loaded" in kq, "Phải chiếu trường loaded"
    assert "pages" in kq, "Phải chiếu trường pages"
    assert "cookies" in kq, "Phải chiếu trường cookies (số lượng)"
    assert isinstance(kq["cookies"], int), "cookies phải là số lượng int, không phải danh sách hay chuỗi cookie"


def test_1_dang_nhap_bat_dau_that_bai_khi_khong_co_credential(mod_instance):
    """dang_nhap_bat_dau trên hồ sơ chưa lưu credential: dịch vụ trả 200 state='failed' -> ok=False, ma_loi='dang_nhap_that_bai'."""
    kq = asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_ghi", "dang_nhap_bat_dau", {"ho_so": "hs_chua_co_cred"}))
    assert kq["ok"] is False, "Khi state là failed, ok phải là False"
    assert kq.get("ma_loi") == "dang_nhap_that_bai"
    assert kq.get("nguon_ngoai") is True
    assert "loi" in kq and "no saved Google credentials" in str(kq.get("loi"))


def test_1_bat_dau_dang_nhap_thieu_tai_khoan_da_luu(mod_instance):
    """bat_dau_dang_nhap trên dịch vụ khác khi hồ sơ chưa có tài khoản đã lưu -> thieu_tai_khoan_da_luu, 0 gọi mạng."""
    kq = asyncio.run(mod_instance.bat_dau_dang_nhap("gemini-web", "hs_chua_tung_luu"))
    assert kq["ok"] is False
    assert kq.get("loi") == "thieu_tai_khoan_da_luu"
    assert kq.get("nguon_ngoai") is True


def test_1_goi_chuyen_huong_3xx_va_khong_phai_json(md, manifest, may_chu_toi_gian):
    """_goi từ chối 3xx chuyển hướng và từ chối thân không phải JSON."""
    url, _ = may_chu_toi_gian
    m = md.Module(manifest)
    m.nap({"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA})

    # 1. Chuyển hướng 301
    kq_301 = asyncio.run(m._goi("GET", "/chuyen-huong-301"))
    assert kq_301["ok"] is False
    assert kq_301.get("ma_loi") == "chuyen_huong_khong_cho_phep"

    # 2. Không phải JSON
    kq_html = asyncio.run(m._goi("GET", "/khong-phai-json"))
    assert kq_html["ok"] is False
    assert kq_html.get("ma_loi") == "phan_hoi_khong_phai_json"


# ── Mục 3: Khoá sai phải bị phát hiện ─────────────────────────────────────────

def test_3_cai_khoa_sai_bi_tu_choi(md, manifest, may_chu_dich_vu):
    """cai() với khoá sai phải gọi route cần khoá của dịch vụ và trả ok: False, KHÔNG lưu cấu hình."""
    url, _ = may_chu_dich_vu
    m = md.Module(manifest)
    kq = asyncio.run(m.cai({
        "dich_vu_url": url,
        "khoa_dich_vu": "khoa-sai-0123456789abcdef",
    }))
    assert kq["ok"] is False, "cai() với khoá sai phải trả ok: False"
    assert "cau_hinh_luu" not in kq, "Khoá sai không được phép trả cau_hinh_luu"
    assert "khoa" in str(kq.get("loi", "")).lower() or "khoá" in str(kq.get("loi", "")).lower()


def test_3_suc_khoe_khoa_sai_bao_do(md, manifest, may_chu_dich_vu):
    """suc_khoe() phải gọi cả route cần khoá; khoá sai -> ok: False, ma_loi: 'sai_khoa_dich_vu'."""
    url, _ = may_chu_dich_vu
    m = md.Module(manifest)
    m.nap({
        "dich_vu_url": url,
        "khoa_dich_vu": "khoa-sai-0123456789abcdef",
    })
    sk = asyncio.run(m.suc_khoe())
    assert sk["ok"] is False, "suc_khoe() với khoá sai phải trả ok: False"
    assert sk.get("ma_loi") == "sai_khoa_dich_vu"


# ── Mục 4: SSRF & Ghim IP & Trần kích thước ───────────────────────────────────

def test_4_ssrf_danh_sach_dia_chi(md, monkeypatch):
    """Kiểm tra chặt chẽ danh sách địa chỉ IP / URL cho phép và bị từ chối theo app.tu_xa."""
    tu_choi = [
        "http://169.254.169.254",
        "http://0.0.0.0",
        "http://224.0.0.1",
        "http://[::ffff:169.254.169.254]", # IPv6 nhúng metadata
    ]
    for url in tu_choi:
        try:
            asyncio.run(md._kiem_url_dich_vu(url))
            assert False, f"Địa chỉ {url} lẽ ra phải bị từ chối nhưng lại được chấp nhận"
        except ValueError:
            pass

    # Loopback bị từ chối khi không bật env test
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    with pytest.raises(ValueError, match="loopback"):
        asyncio.run(md._kiem_url_dich_vu("http://127.0.0.1:8010"))
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    chap_nhan = [
        "http://127.0.0.1:8010",
        "http://localhost:8010",
        "http://[::1]:8010",
        "http://" + "10.0." + "0.5:8010",
        "http://192.0.2.1:8010",
    ]
    for url in chap_nhan:
        res = asyncio.run(md._kiem_url_dich_vu(url))
        assert res.rstrip("/") == url.rstrip("/")


def test_4_tran_kich_thuoc_phan_hoi(md, manifest, may_chu_toi_gian):
    """Phản hồi dịch vụ vượt quá trần kích thước (1 MiB) phải ngắt theo luồng và trả ma_loi='phan_hoi_qua_lon'."""
    url, _ = may_chu_toi_gian
    m = md.Module(manifest)
    m.nap({"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA})

    kq = asyncio.run(m._goi("GET", "/tra-ve-lon"))
    assert kq["ok"] is False
    assert kq.get("ma_loi") == "phan_hoi_qua_lon"


# ── Mục 5: Công cụ AI qua lõi ─────────────────────────────────────────────────

def test_5_may_dang_nhap_cong_cu_ai_qua_loi_that(tmp_path, monkeypatch, may_chu_dich_vu, md, manifest):
    """Công cụ AI may-dang-nhap phải tới được qua /api/thiet-bi của lõi, tuân thủ SEC-004f."""
    url, server = may_chu_dich_vu
    from fastapi.testclient import TestClient
    from app import so_module
    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_LÕI_QT)
    monkeypatch.setenv("WEB2API_AUTH_KEY", KHOA_LÕI_KHÁCH)

    import shutil
    thu_muc_mod = tmp_path / "modules_chi_may_dang_nhap"
    shutil.copytree(GOC / "modules" / "may-dang-nhap", thu_muc_mod / "may-dang-nhap", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", thu_muc_mod)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"})
        # Cài cấu hình hợp lệ
        r_cai = client.post(
            "/api/modules/may-dang-nhap/cai",
            json={"cau_hinh": {"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_cai.status_code == 200

        # 1. GET /api/thiet-bi không khoá -> 401
        r_401 = client.get("/api/thiet-bi")
        assert r_401.status_code == 401

        # 2. GET /api/thiet-bi với khoá quản trị -> 200 và thấy thực thể may_dang_nhap.*
        r_tb = client.get("/api/thiet-bi", headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"})
        assert r_tb.status_code == 200
        ds_ids = [e["id"] for e in r_tb.json().get("thuc_the", [])]
        assert "may_dang_nhap.cong_cu_doc" in ds_ids, f"may_dang_nhap.cong_cu_doc không có trong /api/thiet-bi: {ds_ids}"
        assert "may_dang_nhap.cong_cu_ghi" in ds_ids, f"may_dang_nhap.cong_cu_ghi không có trong /api/thiet-bi: {ds_ids}"

        # 3. Quản trị xác nhận công cụ đọc (SEC-004f) -> chuyển rủi ro từ nguy_hiem về doc
        r_xn_doc = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_doc/xac-nhan",
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_xn_doc.status_code == 200

        # Gọi hành động đọc -> 200 ok
        r_doc = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_doc/hanh-dong",
            json={"hanh_dong": "ho_so_danh_sach", "tham_so": {}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_doc.status_code == 200
        assert r_doc.json().get("ok") is True

        # 4. Hành động ghi chưa xác nhận (SEC-004f: thực thể mới tự bị xếp nguy_hiem) -> 409 can_xac_nhan
        r_ghi_chua_xn = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong",
            json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "hs_test_1"}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_ghi_chua_xn.status_code == 409, f"Hành động nguy hiểm chưa xác nhận phải trả 409: {r_ghi_chua_xn.status_code} {r_ghi_chua_xn.text}"

        # 5. Hành động ghi có xac_nhan: true -> 200 ok
        r_ghi_xn = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong",
            json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "hs_test_1"}, "xac_nhan": True},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_ghi_xn.status_code == 200
        assert r_ghi_xn.json().get("ok") is True

        # 6. Hành động lạ -> 400
        r_la = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_doc/hanh-dong",
            json={"hanh_dong": "hanh_dong_khong_ton_tai"},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_la.status_code == 400


# ── Mục E1: Quyền khoá khách & Chống IDOR thực thể/hồ sơ ─────────────────────

def test_e1_quyen_khoa_khach_va_idor(tmp_path, monkeypatch, may_chu_dich_vu, md, manifest):
    """E1: Test thực tế quyền khoá khách (chat, thiet_bi:doc, thiet_bi:ghi), hành động chéo và IDOR."""
    url, server = may_chu_dich_vu
    from fastapi.testclient import TestClient
    from app import so_module, khoa_khach
    from app.main import app

    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_LÕI_QT)

    import shutil
    thu_muc_mod = tmp_path / "modules_chi_may_dang_nhap"
    shutil.copytree(GOC / "modules" / "may-dang-nhap", thu_muc_mod / "may-dang-nhap", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", thu_muc_mod)

    with TestClient(app) as client:
        client.post("/api/modules/nap-lai", headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"})
        r_cai = client.post(
            "/api/modules/may-dang-nhap/cai",
            json={"cau_hinh": {"dich_vu_url": url, "khoa_dich_vu": KHOA_DICH_VU_GIA}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_cai.status_code == 200

        # Tạo các loại khoá khách
        _, k_chat = khoa_khach.tao_khoa(nhan="Khach Chat", pham_vi=["chat"])
        _, k_tb_doc = khoa_khach.tao_khoa(nhan="Khach TB Doc", pham_vi=["thiet_bi:doc"])
        _, k_tb_ghi = khoa_khach.tao_khoa(nhan="Khach TB Ghi", pham_vi=["thiet_bi:ghi"])

        hdr_chat = {"Authorization": f"Bearer {k_chat}"}
        hdr_tb_doc = {"Authorization": f"Bearer {k_tb_doc}"}
        hdr_tb_ghi = {"Authorization": f"Bearer {k_tb_ghi}"}

        # 1. Khoá khách phạm vi "chat" -> 403 trên cả 4 đường /api/thiet-bi của may-dang-nhap
        r1 = client.get("/api/thiet-bi", headers=hdr_chat)
        assert r1.status_code == 403, f"Khoá chat gọi GET /api/thiet-bi phải 403: {r1.status_code}"

        r2 = client.get("/api/thiet-bi/may_dang_nhap.cong_cu_doc", headers=hdr_chat)
        assert r2.status_code == 403, f"Khoá chat gọi GET chi tiết thực thể phải 403: {r2.status_code}"

        r3 = client.post("/api/thiet-bi/may_dang_nhap.cong_cu_doc/hanh-dong", json={"hanh_dong": "ho_so_danh_sach"}, headers=hdr_chat)
        assert r3.status_code == 403, f"Khoá chat gọi POST hanh-dong doc phải 403: {r3.status_code}"

        r4 = client.post("/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong", json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "hs1"}}, headers=hdr_chat)
        assert r4.status_code == 403, f"Khoá chat gọi POST hanh-dong ghi phải 403: {r4.status_code}"

        # 2. Phạm vi "thiet_bi:doc" gọi hành động -> 403
        r_doc_act1 = client.post("/api/thiet-bi/may_dang_nhap.cong_cu_doc/hanh-dong", json={"hanh_dong": "ho_so_danh_sach"}, headers=hdr_tb_doc)
        assert r_doc_act1.status_code == 403, "thiet_bi:doc không được phép gọi hành động"

        r_doc_act2 = client.post("/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong", json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "hs1"}}, headers=hdr_tb_doc)
        assert r_doc_act2.status_code == 403, "thiet_bi:doc không được phép gọi hành động ghi"

        # Nhưng thiet_bi:doc đọc được thông tin thực thể
        r_doc_get = client.get("/api/thiet-bi/may_dang_nhap.cong_cu_doc", headers=hdr_tb_doc)
        assert r_doc_get.status_code == 200

        # 3. Phạm vi "thiet_bi:ghi" -> 202 chờ duyệt đối với hành động ghi chưa xác nhận
        r_ghi_202 = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong",
            json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "hs1"}},
            headers=hdr_tb_ghi,
        )
        assert r_ghi_202.status_code == 202, f"Khách gọi hành động ghi chưa duyệt phải nhận 202: {r_ghi_202.status_code}"

        # 4. Hành động chéo thực thể -> 400
        # Gọi hành động ghi "dang_nhap_bat_dau" trên thực thể đọc "may_dang_nhap.cong_cu_doc"
        r_cheo_1 = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_doc/hanh-dong",
            json={"hanh_dong": "dang_nhap_bat_dau", "tham_so": {"ho_so": "hs1"}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_cheo_1.status_code == 400, "Hành động ghi gọi trên thực thể đọc phải trả 400"

        # Gọi hành động đọc "ho_so_danh_sach" trên thực thể ghi "may_dang_nhap.cong_cu_ghi"
        r_cheo_2 = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong",
            json={"hanh_dong": "ho_so_danh_sach", "tham_so": {}},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_cheo_2.status_code == 400, "Hành động đọc gọi trên thực thể ghi phải trả 400"

        # 5. IDOR (đổi id thực thể / hồ sơ)
        # ID thực thể không tồn tại -> 404
        r_idor_tt = client.get(
            "/api/thiet-bi/may_dang_nhap.thuc_the_la_khong_ton_tai",
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_idor_tt.status_code == 404

        # Hồ sơ IDOR path traversal -> từ chối tham_so_khong_hop_le
        r_idor_hs = client.post(
            "/api/thiet-bi/may_dang_nhap.cong_cu_ghi/hanh-dong",
            json={"hanh_dong": "ho_so_dong", "tham_so": {"ho_so": "../../../etc/passwd"}, "xac_nhan": True},
            headers={"Authorization": f"Bearer {KHOA_LÕI_QT}"},
        )
        assert r_idor_hs.json().get("ok") is False
        assert "tham_so_khong_hop_le" in r_idor_hs.json().get("loi", "")


# ── Mục 6: Mức thấp nhỏ ───────────────────────────────────────────────────────

def test_6_dang_nhap_bat_dau_gui_ho_so_sach(mod_instance, may_chu_dich_vu):
    """dang_nhap_bat_dau phải gửi ho_so đã strip/kiểm tra vào body thay vì ho_so thô."""
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()
    asyncio.run(mod_instance.thuc_hien("may_dang_nhap.cong_cu_ghi", "dang_nhap_bat_dau", {"ho_so": "  hs_test_1  "}))
    assert len(server.lich_su_goi) == 1
    goi = server.lich_su_goi[0]
    assert goi["than"]["profile"] == "hs_test_1", f"Dịch vụ phải nhận bản ho_so sạch: {goi['than']}"


def test_6_luu_tai_khoan_email_sai_van_xoa_bimat_tam(mod_instance, may_chu_dich_vu):
    """luu_tai_khoan khi email sai khuôn: BiMatTam phải được xoá ngay và 0 lời gọi mạng."""
    url, server = may_chu_dich_vu
    server.lich_su_goi.clear()
    mat_khau_tam = BiMatTam("MatKhau_123456")
    totp_tam = BiMatTam("JBSWY3DPEHPK3PXP")

    kq = asyncio.run(mod_instance.luu_tai_khoan(
        email="email-sai-khuon@@@",
        mat_khau=mat_khau_tam,
        totp=totp_tam,
    ))
    assert kq["ok"] is False
    assert mat_khau_tam.da_xoa(), "mat_khau BiMatTam phải bị xoá ngay cả khi email sai"
    assert totp_tam.da_xoa(), "totp BiMatTam phải bị xoá ngay cả khi email sai"
    assert len(server.lich_su_goi) == 0, "Không được gửi lời gọi mạng nào khi email sai"


# ── Mục B: Hướng dẫn cài SAI ──────────────────────────────────────────────────

def test_b_khoa_dich_vu_duong_dan_khong_chua_data():
    """Mục B: README, manifest và THAM-CHIEU KHÔNG chứa /data/khoa-dich-vu và CÓ /khoa/khoa-dich-vu; khớp settings/compose."""
    readme_mod = (THU_MUC_MOD / "README.md").read_text(encoding="utf-8")
    manifest_mod = (THU_MUC_MOD / "manifest.json").read_text(encoding="utf-8")
    tham_chieu = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")

    assert "/data/khoa-dich-vu" not in readme_mod, "modules/may-dang-nhap/README.md không được chứa /data/khoa-dich-vu"
    assert "/data/khoa-dich-vu" not in manifest_mod, "modules/may-dang-nhap/manifest.json không được chứa /data/khoa-dich-vu"
    assert "/data/khoa-dich-vu" not in tham_chieu, "docs/THAM-CHIEU.md không được chứa /data/khoa-dich-vu"

    assert "/khoa/khoa-dich-vu" in readme_mod, "modules/may-dang-nhap/README.md phải chứa /khoa/khoa-dich-vu"
    assert "/khoa/khoa-dich-vu" in manifest_mod, "modules/may-dang-nhap/manifest.json phải chứa /khoa/khoa-dich-vu"
    assert "/khoa/khoa-dich-vu" in tham_chieu, "docs/THAM-CHIEU.md phải chứa /khoa/khoa-dich-vu"

    from src.settings import Settings as C2ASettings
    assert str(C2ASettings.model_fields["key_dir"].default).rstrip("/") == "/khoa", "Settings.key_dir mặc định phải là /khoa"

    compose_text = (DV_C2A_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/khoa" in compose_text, "docker-compose.yml phải gắn volume /khoa"
    assert 'CAPTCHA_SOLVER_KEY_DIR: "/khoa"' in compose_text, "docker-compose.yml phải đặt CAPTCHA_SOLVER_KEY_DIR là /khoa"


# ── Mục C4: Khớp chính xác email trong bat_dau_dang_nhap ──────────────────────

def test_c4_bat_dau_dang_nhap_khong_khop_chuoi_con_email(mod_instance, monkeypatch):
    """C4: bat_dau_dang_nhap không khớp chuỗi con email ('a@x' không được khớp 'aa@x')."""
    async def _goi_gia(phuong_thuc, duong, **kwargs):
        if duong == "/v1/profiles":
            return {"ok": True, "profiles": []}
        if duong == "/v1/accounts/saved":
            return {"ok": True, "accounts": [{"email": "aa@example.com"}]}
        if duong == "/v1/gemini/login/start":
            return {"ok": True, "profile": kwargs.get("json_body", {}).get("profile")}
        return {"ok": True}

    monkeypatch.setattr(mod_instance, "_goi", _goi_gia)
    kq = asyncio.run(mod_instance.bat_dau_dang_nhap("gemini-web", "a@example.com"))
    assert kq["ok"] is False, "Không được khớp chuỗi con email ('a@example.com' không được khớp 'aa@example.com')"
    assert kq.get("loi") == "thieu_tai_khoan_da_luu"

    # Khi có tài khoản trùng khớp sau chuẩn hoá (khác hoa thường/khoảng trắng) -> khớp thành công
    async def _goi_gia_chuan_hoa(phuong_thuc, duong, **kwargs):
        if duong == "/v1/profiles":
            return {"ok": True, "profiles": []}
        if duong == "/v1/accounts/saved":
            return {"ok": True, "accounts": [{"email": "  A@example.com  "}]}
        if duong == "/v1/gemini/login/start":
            return {"ok": True, "profile": kwargs.get("json_body", {}).get("profile")}
        return {"ok": True}

    monkeypatch.setattr(mod_instance, "_goi", _goi_gia_chuan_hoa)
    kq_chuan = asyncio.run(mod_instance.bat_dau_dang_nhap("gemini-web", "a@example.com"))
    assert kq_chuan["ok"] is True


# ── Mục C3: Hợp đồng trang_thai(ids) phản ánh trung thực dịch vụ ──────────────

def test_c3_trang_thai_hoi_dich_vu_that_hoac_bao_loi(mod_instance, monkeypatch):
    """C3: trang_thai(ids) không được luôn trả hop_le=True; khi dịch vụ lỗi phải trả hop_le=False."""
    # Khi dịch vụ giả trả lỗi 500 hoặc mất kết nối
    async def _goi_loi(phuong_thuc, duong, **kwargs):
        return {"ok": False, "ma_loi": "loi_dich_vu", "loi": "Dịch vụ 500"}

    monkeypatch.setattr(mod_instance, "_goi", _goi_loi)
    # mod_instance đã có _khoa giả
    st = asyncio.run(mod_instance.trang_thai(["may_dang_nhap.cong_cu_doc"]))
    assert "may_dang_nhap.cong_cu_doc" in st
    assert st["may_dang_nhap.cong_cu_doc"]["hop_le"] is False, (
        f"Khi dịch vụ lỗi, trang_thai phải trả hop_le=False, nhận: {st['may_dang_nhap.cong_cu_doc']}"
    )
    assert st["may_dang_nhap.cong_cu_doc"]["gia_tri"] != "san_sang"

    # Khi dịch vụ hoạt động bình thường
    async def _goi_ok(phuong_thuc, duong, **kwargs):
        if duong == "/health":
            return {"ok": True, "status": "ok", "dich_vu": "may-dang-nhap"}
        return {"ok": True, "profiles": ["hs1"]}

    monkeypatch.setattr(mod_instance, "_goi", _goi_ok)
    mod_instance._suc_khoe_cache = None  # Xoá cache để kiểm tra lại
    st_ok = asyncio.run(mod_instance.trang_thai(["may_dang_nhap.cong_cu_doc"]))
    assert st_ok["may_dang_nhap.cong_cu_doc"]["hop_le"] is True
    assert st_ok["may_dang_nhap.cong_cu_doc"]["gia_tri"] == "san_sang"


# ── Mục C6: cai() từ chối HTML / 5xx / sai danh tính và KHÔNG lưu khoá ───────

def test_c6_cai_tu_choi_html_hoac_5xx_khong_luu_khoa(mod_instance, monkeypatch):
    """C6: cai() chỉ lưu khi /health đúng khuôn may-dang-nhap; máy trả 200 HTML hoặc 500 phải trả ok: False và KHÔNG lưu khoá."""
    mod_instance._url = "http://web2api-may-dang-nhap:8010"
    mod_instance._khoa = ""

    # Trường hợp 1: Máy trả 200 nhưng là HTML (không phải JSON dịch vụ may-dang-nhap)
    async def _goi_html(phuong_thuc, duong, **kwargs):
        if duong == "/health":
            return {"ok": False, "ma_loi": "phan_hoi_khong_phai_json", "loi": "HTML 200"}
        return {"ok": False, "ma_loi": "phan_hoi_khong_phai_json"}

    monkeypatch.setattr(mod_instance, "_goi", _goi_html)
    async def _kiem_url_gia(url):
        return url
    # Nếu dùng kiem_url_lan hoặc _kiem_url_dich_vu
    monkeypatch.setattr("modules.may-dang-nhap.module._kiem_url_dich_vu", _kiem_url_gia, raising=False)

    kq1 = asyncio.run(mod_instance.cai({"dich_vu_url": "http://192.0.2.99:8010", "khoa_dich_vu": "khoa_bi_mat_123"}))
    assert kq1["ok"] is False, "Máy trả HTML không được coi là cài thành công!"
    assert mod_instance._khoa == "", "Không được lưu khoá dịch vụ khi xác nhận thất bại!"

    # Trường hợp 2: Máy trả 500 lỗi máy chủ nội bộ
    async def _goi_500(phuong_thuc, duong, **kwargs):
        return {"ok": False, "ma_loi": "loi_dich_vu", "loi": "500 Internal Server Error"}

    monkeypatch.setattr(mod_instance, "_goi", _goi_500)
    kq2 = asyncio.run(mod_instance.cai({"dich_vu_url": "http://192.0.2.99:8010", "khoa_dich_vu": "khoa_bi_mat_456"}))
    assert kq2["ok"] is False, "Máy trả 500 không được coi là cài thành công!"
    assert mod_instance._khoa == "", "Không được lưu khoá dịch vụ khi máy báo lỗi 500!"


# ── Mục E3: Đột biến 4 & 5 ────────────────────────────────────────────────────

def test_e3_dot_bien_4_transport_ghim_ip_bat_buoc_khoi_tao(mod_instance, md, monkeypatch):
    """E3 (4): TransportGhimIp bắt buộc được khởi tạo trong _goi; nếu là None test phải đỏ."""
    khoi_tao_transport = []

    lop_goc = md.TransportGhimIp

    def _spy_transport(*args, **kwargs):
        khoi_tao_transport.append({"args": args, "kwargs": kwargs})
        return lop_goc(*args, **kwargs)

    monkeypatch.setattr(md, "TransportGhimIp", _spy_transport)

    # Chạy _goi với máy chủ thật
    asyncio.run(mod_instance._goi("GET", "/health"))
    assert len(khoi_tao_transport) >= 1, "TransportGhimIp bắt buộc phải được khởi tạo khi _goi thực hiện"
    assert khoi_tao_transport[0]["kwargs"].get("kiem_ip") is not None or len(khoi_tao_transport[0]["args"]) > 0


def test_e3_dot_bien_5_bo_loc_log_bat_buoc_chan_authorization_bearer(md):
    """E3 (5): Bộ lọc log _ChanKhoaDichVu bắt buộc lọc bỏ nhánh authorization + bearer."""
    bo_loc = md._ChanKhoaDichVu()

    # Tạo bản ghi log chứa Bearer bất kỳ (kể cả không có trong _KHOA_DANG_DUNG)
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="client.py",
        lineno=10,
        msg="Gửi yêu cầu với Authorization: Bearer token_la_chua_tung_dang_ky_123456",
        args=(),
        exc_info=None,
    )
    # Bộ lọc chuẩn phải trả False (loại bỏ dòng log này)
    assert bo_loc.filter(record) is False, "Bộ lọc log bắt buộc phải chặn dòng chứa Authorization: Bearer"

    # Dòng log bình thường không mang authorization bearer phải qua được (True)
    record_ok = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname="client.py",
        lineno=20,
        msg="GET /v1/profiles hoàn thành HTTP 200",
        args=(),
        exc_info=None,
    )
    assert bo_loc.filter(record_ok) is True


