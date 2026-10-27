"""NV273: lam_moi_model() cho 4 module còn thiếu (claude-web, grok-web,
openaicodex, muse2api).

Mỗi module độc lập: test của module này không import module kia, không dùng
logic chung trong lõi. Tất cả qua dịch vụ GIẢ cục bộ (máy chủ giả loopback
hoặc httpx.MockTransport) — không gọi dịch vụ ngoài thật, không đăng nhập thật.

Bốn ca cho mỗi module, theo đúng file NV273 §2d:
1. thành công (có model) — danh sách giữ nguyên, không ném;
2. lỗi xác thực — ném RuntimeError mang mã «xac_thuc», giữ danh sách cũ;
3. mạng hỏng — ném RuntimeError mang mã «mang», giữ danh sách cũ;
4. phản hồi sai khuôn — ném RuntimeError mang mã «sai_khuon», giữ danh sách cũ.

LƯU Ý TRUNG THỰC (NV273 §2b): cả 4 module này KHÔNG có endpoint liệt kê
model hợp lệ cho đúng loại xác thực web của chúng trong repo (claude.ai chỉ
trả tổ chức, grok.com chỉ trả chế độ tĩnh, Codex OAuth không có endpoint
liệt kê, muse.ai chỉ có đúng một model trong giao thức hiện tại). Vì vậy
lam_moi_model() KHÔNG bịa endpoint: nguồn của nó là danh sách tĩnh đã khai
trong manifest («model_tinh»), và bước «lấy lại» là kiểm phiên thật của
từng tài khoản qua đường đã có của chính module đó; kiểm hỏng thì giữ
danh sách cũ và báo mã lỗi rõ. Xem thêm hoi-nv273-endpoint-liet-ke.md.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from gia import MayChuThat, cong_trong

GOC = Path(__file__).resolve().parents[1]


def chay(coro):
    return asyncio.run(coro)


def nap_goi(ten: str, thu_muc: str):
    """Nạp modules/<thu_muc> như một gói riêng (giống lõi nạp)."""
    if ten in sys.modules:
        sys.modules.pop(ten, None)
    goi = __import__("types").ModuleType(ten)
    goi.__path__ = [str(GOC / "modules" / thu_muc)]
    sys.modules[ten] = goi
    spec = importlib.util.spec_from_file_location(
        ten + ".module", GOC / "modules" / thu_muc / "module.py",
        submodule_search_locations=[str(GOC / "modules" / thu_muc)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten + ".module"] = mod
    spec.loader.exec_module(mod)
    manifest = json.loads((GOC / "modules" / thu_muc / "manifest.json")
                          .read_text(encoding="utf-8"))
    return mod, manifest


def ids(ds):
    return [m["id"] for m in ds]


# ══════════════════════════════ claude-web ══════════════════════════════

class ClaudeGia:
    """claude.ai giả: chỉ /api/organizations, hành vi theo chuỗi con cookie."""

    def __init__(self):
        async def organizations(request):
            cookie = request.headers.get("Cookie", "")
            if "het-han" in cookie:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            if "loi-500" in cookie:
                return JSONResponse({"detail": "upstream"}, status_code=500)
            if "json-hong" in cookie:
                return PlainTextResponse("khong-phai-json", status_code=200)
            return JSONResponse([{"uuid": "org-gia-1",
                                  "capabilities": ["chat"],
                                  "name": "Tổ chức giả"}])

        self.app = Starlette(routes=[Route("/api/organizations", organizations)])

    def __enter__(self):
        self._srv = MayChuThat(self.app)
        self._srv.__enter__()
        self.url = self._srv.url
        return self

    def __exit__(self, *a):
        self._srv.__exit__(*a)


def claude_module(url, cookie, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod, manifest = nap_goi("nv273_claude_web", "claude-web")
    m = mod.Module(manifest)
    m.nap({"cookies": [cookie], "base_url": url})
    return m


def test_claude_lam_moi_thanh_cong_giu_danh_sach(monkeypatch):
    with ClaudeGia() as srv:
        m = claude_module(srv.url, "tot-session-key-aaaabbbbcccc", monkeypatch)
        truoc = ids(chay(m.models()))
        assert truoc == manifest_ids("claude-web")
        chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_claude_lam_moi_xac_thuc_giu_danh_sach(monkeypatch):
    with ClaudeGia() as srv:
        m = claude_module(srv.url, "het-han-401-jjjj", monkeypatch)
        truoc = ids(chay(m.models()))
        with pytest.raises(RuntimeError, match="xac_thuc"):
            chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_claude_lam_moi_mang_hong_giu_danh_sach(monkeypatch):
    with ClaudeGia() as srv:
        m = claude_module(srv.url, "loi-500-mmmm", monkeypatch)
        truoc = ids(chay(m.models()))
        with pytest.raises(RuntimeError, match="mang"):
            chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_claude_lam_moi_sai_khuon_giu_danh_sach(monkeypatch):
    with ClaudeGia() as srv:
        m = claude_module(srv.url, "json-hong-zzzz", monkeypatch)
        truoc = ids(chay(m.models()))
        with pytest.raises(RuntimeError, match="sai_khuon"):
            chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_claude_khong_tai_khoan_khong_nem(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod, manifest = nap_goi("nv273_claude_web2", "claude-web")
    m = mod.Module(manifest)
    m.nap({})
    truoc = ids(chay(m.models()))
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


# ══════════════════════════════ grok-web ═══════════════════════════════

class GrokGia:
    """grok.com giả: chỉ /api/auth/session, hành vi theo giá trị sso."""

    def __init__(self):
        async def phien(request):
            cookie = request.headers.get("Cookie", "")
            sso = ""
            for phan in cookie.split(";"):
                phan = phan.strip()
                if phan.startswith("sso="):
                    sso = phan[4:]
            if sso == "het-han":
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            if sso == "sai-khuon":
                return JSONResponse({"session": {}}, status_code=200)
            return JSONResponse({"session": {"userId": "uid-gia"}})

        self.app = Starlette(routes=[Route("/api/auth/session", phien)])

    def __enter__(self):
        self._srv = MayChuThat(self.app)
        self._srv.__enter__()
        self.url = self._srv.url
        return self

    def __exit__(self, *a):
        self._srv.__exit__(*a)


def grok_module(url_gia, sso, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod, manifest = nap_goi("nv273_grok_web", "grok-web")
    m = mod.Module(manifest)
    # Không có x-userid → lam_moi_model buộc phải hỏi /api/auth/session.
    ws_url = url_gia.replace("http://", "ws://")
    chay(m.nap({"tai_khoan": [f"sso={sso}; phien=abc123"],
                "ws_url": ws_url}))
    return m


def test_grok_lam_moi_thanh_cong_giu_danh_sach(monkeypatch):
    with GrokGia() as srv:
        m = grok_module(srv.url, "tot_1", monkeypatch)
        truoc = ids(chay(m.models()))
        assert truoc == manifest_ids("grok-web")
        chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_grok_lam_moi_xac_thuc_giu_danh_sach(monkeypatch):
    with GrokGia() as srv:
        m = grok_module(srv.url, "het-han", monkeypatch)
        truoc = ids(chay(m.models()))
        with pytest.raises(RuntimeError, match="xac_thuc"):
            chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_grok_lam_moi_mang_hong_giu_danh_sach(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    mod, manifest = nap_goi("nv273_grok_web2", "grok-web")
    m = mod.Module(manifest)
    cong = cong_trong()  # cổng trống, không ai nghe → lỗi kết nối thật
    chay(m.nap({"tai_khoan": ["sso=tot_1; phien=abc123"],
                "ws_url": f"ws://127.0.0.1:{cong}"}))
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="mang"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_grok_lam_moi_sai_khuon_giu_danh_sach(monkeypatch):
    with GrokGia() as srv:
        m = grok_module(srv.url, "sai-khuon", monkeypatch)
        truoc = ids(chay(m.models()))
        with pytest.raises(RuntimeError, match="sai_khuon"):
            chay(m.lam_moi_model())
        assert ids(chay(m.models())) == truoc


def test_grok_khong_tai_khoan_khong_nem(monkeypatch):
    mod, manifest = nap_goi("nv273_grok_web3", "grok-web")
    m = mod.Module(manifest)
    chay(m.nap({}))
    truoc = ids(chay(m.models()))
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


# ══════════════════════════════ openaicodex ════════════════════════════

def _b64(obj) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")


def tao_jwt_codex(email="nguoi.a@example.com"):
    payload = {
        "email": email,
        "exp": int(time.time()) + 8 * 3600,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acc-123", "chatgpt_plan_type": "plus"},
        "https://api.openai.com/profile": {"email": email},
    }
    return f"{_b64({'alg': 'none'})}.{_b64(payload)}.chu-ky-gia"


def codex_module(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod, manifest = nap_goi("nv273_openaicodex", "openaicodex")
    from app import kho as kho_mod
    mod.oauth.don_bo_dem_lam_moi()
    m = mod.Module(dict(manifest))
    m.kho = kho_mod.kho_rieng("openaicodex")
    tk = mod._TaiKhoan(tao_jwt_codex(), "refresh-gia-123")
    tk.trang_thai = "hoat_dong"
    m._tai_khoan = [tk]
    return m


def _transport_wham_ok(urls):
    def xu_ly(req: httpx.Request) -> httpx.Response:
        urls.append(str(req.url))
        assert "/backend-api/wham/usage" in str(req.url)
        return httpx.Response(200, json={})
    return httpx.MockTransport(xu_ly)


def test_codex_lam_moi_thanh_cong_giu_danh_sach(tmp_path, monkeypatch):
    m = codex_module(tmp_path, monkeypatch)
    urls = []
    m._gan_transport_gia(_transport_wham_ok(urls))
    truoc = ids(chay(m.models()))
    assert truoc == manifest_ids("openaicodex")
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc
    assert urls, "lam_moi_model phai kiem phien qua /wham/usage"


def test_codex_lam_moi_khong_xoay_token(tmp_path, monkeypatch):
    """Quyêt định chủ máy 2026-10-28: lam_moi_model KHÔNG làm mới token.

    Chứng minh bằng 3 cách cùng lúc: token trước/sau không đổi, không có
    yêu cầu nào tới endpoint OAuth, và _lam_moi_tai_khoan có bị gọi thì
    test sẽ nổ ngay.
    """
    m = codex_module(tmp_path, monkeypatch)
    urls = []
    m._gan_transport_gia(_transport_wham_ok(urls))
    tk = m._tai_khoan[0]
    access_truoc, refresh_truoc = tk.access_token, tk.refresh_token

    async def _cam_goi(*a, **k):
        raise AssertionError("lam_moi_model khong duoc goi _lam_moi_tai_khoan")
    m._lam_moi_tai_khoan = _cam_goi

    chay(m.lam_moi_model())
    assert tk.access_token == access_truoc
    assert tk.refresh_token == refresh_truoc
    assert urls and all("auth.openai.com" not in u and "/oauth/token" not in u
                        for u in urls)


def test_codex_lam_moi_xac_thuc_giu_danh_sach(tmp_path, monkeypatch):
    m = codex_module(tmp_path, monkeypatch)
    # NV274: làm mới danh sách KHÔNG ép xoay token; chỉ khi token sắp hết hạn mới gọi máy chủ OAuth.
    m._tai_khoan[0].expires_at = time.time() + 600

    def xu_ly(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})
    m._gan_transport_gia(httpx.MockTransport(xu_ly))
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="xac_thuc"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_codex_lam_moi_mang_hong_giu_danh_sach(tmp_path, monkeypatch):
    m = codex_module(tmp_path, monkeypatch)
    # NV274: làm mới danh sách KHÔNG ép xoay token; chỉ khi token sắp hết hạn mới gọi máy chủ OAuth.
    m._tai_khoan[0].expires_at = time.time() + 600

    def xu_ly(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("đứt mạng tạm", request=req)
    m._gan_transport_gia(httpx.MockTransport(xu_ly))
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="mang"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_codex_lam_moi_sai_khuon_giu_danh_sach(tmp_path, monkeypatch):
    m = codex_module(tmp_path, monkeypatch)
    urls = []
    m._gan_transport_gia(_transport_wham_ok(urls))
    m.manifest = dict(m.manifest)
    m.manifest["model_tinh"] = "khong-phai-danh-sach"
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="sai_khuon"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_codex_khong_tai_khoan_khong_nem(tmp_path, monkeypatch):
    m = codex_module(tmp_path, monkeypatch)
    m._tai_khoan = []
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == []


# ══════════════════════════════ muse2api ══════════════════════════════

def muse_module(monkeypatch, ket_qua_kiem):
    mod, manifest = nap_goi("nv273_muse2api", "muse2api")
    m = mod.Module(dict(manifest))
    m.nap({"cookies": ["hatch_sess=abc123def456; hatch_gw=xyz789"]})

    def kiem_gia(cookies):
        return ket_qua_kiem
    m._kiem_cookie = kiem_gia
    return m


def test_muse_lam_moi_thanh_cong_giu_danh_sach(monkeypatch):
    m = muse_module(monkeypatch, (True, None, False))
    truoc = ids(chay(m.models()))
    assert truoc == manifest_ids("muse2api")
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_muse_lam_moi_xac_thuc_giu_danh_sach(monkeypatch):
    m = muse_module(monkeypatch, (False, "cookie hết hạn", True))
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="xac_thuc"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_muse_lam_moi_mang_hong_giu_danh_sach(monkeypatch):
    m = muse_module(monkeypatch, (False, "network error", False))
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="mang"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_muse_lam_moi_sai_khuon_giu_danh_sach(monkeypatch):
    m = muse_module(monkeypatch, (True, None, False))
    m.manifest = dict(m.manifest)
    m.manifest["model_tinh"] = []
    truoc = ids(chay(m.models()))
    with pytest.raises(RuntimeError, match="sai_khuon"):
        chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


def test_muse_khong_tai_khoan_khong_nem(monkeypatch):
    mod, manifest = nap_goi("nv273_muse2api2", "muse2api")
    m = mod.Module(dict(manifest))
    m.nap({})
    truoc = ids(chay(m.models()))
    chay(m.lam_moi_model())
    assert ids(chay(m.models())) == truoc


# ══════════════════════════════ bảng chung ════════════════════════════

def manifest_ids(thu_muc: str) -> list[str]:
    return list(json.loads((GOC / "modules" / thu_muc / "manifest.json")
                           .read_text(encoding="utf-8"))["model_tinh"])


def test_bang_nv273_moi_module_ai_co_lam_moi_model():
    """Bảng nghiệm thu NV273: 9 module AI đều có lam_moi_model."""
    for thu_muc in ("antigravity", "chatgpt-web", "gemini-api", "grok-api",
                    "gemini-web", "claude-web", "grok-web", "openaicodex",
                    "muse2api"):
        mod, manifest = nap_goi(f"nv273_bang_{thu_muc}", thu_muc)
        assert callable(getattr(mod.Module(dict(manifest)), "lam_moi_model",
                                None)), thu_muc
