"""Luồng OAuth PKCE của Codex + làm mới token.

Nguồn (chỉ đọc, chép theo LUẬT PORT): ~/workspace/c2a-nv146 (commit d879b03)
- services/oauth_service.py: generate_pkce, get_codex_auth_url,
  exchange_codex_code, CODEX_* (chỉ phần Codex — bỏ phần Antigravity/Google).
- services/codex_token_refresh.py: refresh_codex_token — khoá theo
  refresh_token, cache kết quả 60 giây, header X-Device-Id, phân loại lỗi
  UNRECOVERABLE / tạm.
- services/codex_callback_listener.py: bộ nghe :1455 loopback, chỉ log cấu
  trúc (không ghi request-line chứa code), tự đóng sau khi xong.

Chỉnh so với bản gốc: async + hàm `http` chích được cho test; kho pending
state có hạn dùng (TTL) thay vì tồn tại mãi; lỗi có cấu trúc (LoiOAuth) thay
vì ValueError/RuntimeError; không dùng curl_cffi (module chạy trong lõi dùng
httpx); bỏ TLS-fallback; redirect_uri và cổng nghe cho phép truyền tham số
cho test (mặc định giữ đúng giá trị gốc).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Awaitable, Callable

log = logging.getLogger("web2api.openai_codex.oauth")

# ── Cấu hình Codex OAuth (public client của Codex CLI, theo 9router) ─────────
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid profile email offline_access"

#: State dùng một lần có hạn (giây).
HAN_STATE_GIAY = 600

#: Mã lỗi không thể khôi phục: refresh_token đã chết, đừng thử lại.
UNRECOVERABLE_CODES = frozenset({
    "invalid_grant",
    "refresh_token_reused",
    "token_expired",
    "invalid_token",
    "invalid_request",
    "refresh_token_invalidated",
})

#: Khoá mutex theo refresh_token giữ tối đa (bảo vệ bộ nhớ).
_MAX_KHOA = 4096
#: Kết quả làm mới gần đây giữ lại (giây) — chặn hai lượt đồng thời cùng POST.
TTL_KET_QUA_GIAY = 60.0

#: Trần lùi dần khi làm mới lỗi (giây): 1, 2, 4, 8, 16, 32, 60, 60, ...
TRAN_LUI_GIAY = 60.0


class LoiOAuth(Exception):
    """Lỗi OAuth có cấu trúc: KHÔNG bao giờ chứa token/code thô."""

    def __init__(self, ma_loi: str, ma_trang_thai: int | None = None,
                 chi_tiet: str = ""):
        super().__init__(ma_loi)
        self.ma_loi = ma_loi
        self.ma = ma_trang_thai
        self.chi_tiet = str(chi_tiet)[:200]


#: http(method, url, *, data=..., headers=..., timeout=...) -> PhanHoi
HttpGoi = Callable[..., Awaitable[Any]]


async def _post_thuc(method: str, url: str, **kw) -> Any:
    """HTTP thật qua httpx (khi không chích giả cho test).

    Thân phản hồi được đọc trọn trong khối client nên trả ra ngoài vẫn dùng
    được .json()/.text.
    """
    import httpx
    headers = kw.get("headers") or {}
    timeout = float(kw.get("timeout") or 20)
    async with httpx.AsyncClient(timeout=timeout) as cli:
        if method == "POST":
            phan_hoi = await cli.post(url, data=kw.get("data"), headers=headers)
        else:
            phan_hoi = await cli.get(url, headers=headers)
        _ = phan_hoi.content  # ép đọc trọn thân trước khi đóng client
    return phan_hoi


def tao_pkce() -> dict[str, str]:
    """Sinh code_verifier/code_challenge(S256)/state."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return {
        "code_verifier": verifier,
        "code_challenge": challenge,
        "state": secrets.token_hex(16),
    }


class ChoOAuth:
    """Kho các luồng OAuth đang chờ callback: state → {verifier, redirect_uri}.

    State dùng MỘT lần, hết hạn sau HAN_STATE_GIAY (mặc định 10 phút).
    """

    def __init__(self, han_state_giay: float = HAN_STATE_GIAY):
        self._han = han_state_giay
        self._khoa = threading.Lock()
        self._cho: dict[str, dict[str, Any]] = {}

    def _don(self) -> None:
        han = time.time() - self._han
        for state in [s for s, v in self._cho.items()
                      if v.get("luc_tao", 0) < han]:
            self._cho.pop(state, None)

    def bat_dau(self, redirect_uri: str = CODEX_REDIRECT_URI) -> dict[str, str]:
        """Bắt đầu luồng: trả {auth_url, state}, lưu verifier theo state."""
        pkce = tao_pkce()
        params = {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": pkce["code_challenge"],
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
            "state": pkce["state"],
        }
        # Dựng query thủ công (%20 thay vì +) như bản gốc 9router.
        query = "&".join(
            f"{urllib.parse.quote(str(k), safe='')}="
            f"{urllib.parse.quote(str(v), safe='')}"
            for k, v in params.items())
        with self._khoa:
            self._don()
            self._cho[pkce["state"]] = {
                "code_verifier": pkce["code_verifier"],
                "redirect_uri": redirect_uri,
                "luc_tao": time.time(),
            }
        return {"auth_url": f"{CODEX_AUTH_URL}?{query}", "state": pkce["state"]}

    def _lay_va_xoa(self, state: str) -> dict[str, Any]:
        with self._khoa:
            self._don()
            cho = self._cho.pop(state, None)
        if not cho:
            raise LoiOAuth("state_khong_hop_le",
                           chi_tiet="state lạ, đã dùng, hoặc đã hết hạn")
        return cho

    async def doi_ma(self, code: str, state: str,
                     http: HttpGoi | None = None) -> dict[str, Any]:
        """Đổi authorization code lấy token. state lạ/hết hạn/đã dùng → lỗi,
        KHÔNG gọi mạng."""
        if not code or not isinstance(code, str):
            raise LoiOAuth("thieu_code")
        cho = self._lay_va_xoa(str(state))
        http = http or _post_thuc
        body = {
            "client_id": CODEX_CLIENT_ID,
            "code": code,
            "redirect_uri": cho["redirect_uri"],
            "code_verifier": cho["code_verifier"],
            "grant_type": "authorization_code",
        }
        try:
            phan_hoi = await http("POST", CODEX_TOKEN_URL,
                                  data=body,
                                  headers={"Content-Type":
                                           "application/x-www-form-urlencoded"},
                                  timeout=30)
        except Exception as exc:
            raise LoiOAuth("loi_mang_doi_ma",
                           chi_tiet=type(exc).__name__) from None
        if phan_hoi.status_code != 200:
            raise LoiOAuth("doi_ma_that_bai",
                           ma_trang_thai=phan_hoi.status_code,
                           chi_tiet=_ma_loi_tu_than(phan_hoi))
        try:
            du_lieu = phan_hoi.json()
        except Exception:
            raise LoiOAuth("doi_ma_sai_dang", ma_trang_thai=200) from None
        access = str(du_lieu.get("access_token") or "")
        refresh = str(du_lieu.get("refresh_token") or "")
        if not access:
            raise LoiOAuth("doi_ma_thieu_token", ma_trang_thai=200)
        expires_in = int(du_lieu.get("expires_in") or 0)
        email = _email_tu_jwt(access)
        log.info("oauth: đổi mã thành công (email=%s)", _che_email(email))
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
            "expires_at": time.time() + expires_in if expires_in > 0 else 0.0,
            "email": email,
        }


def _ma_loi_tu_than(phan_hoi: Any) -> str:
    """Trích mã lỗi có cấu trúc từ thân phản hồi (không dò chữ tự do)."""
    try:
        than = phan_hoi.json() if hasattr(phan_hoi, "json") else {}
    except Exception:
        than = {}
    if isinstance(than, dict):
        err = than.get("error")
        if isinstance(err, str):
            return err[:80]
        if isinstance(err, dict):
            return str(err.get("code") or err.get("error") or "")[:80]
    return ""


def _email_tu_jwt(token: str) -> str:
    claims = giai_claims(token)
    # Khoá claim của OpenAI (giống _CLAIM_PROFILE trong han_muc.py).
    profile = claims.get("https://" + "api.openai.com/profile") or {}
    return str(claims.get("email") or profile.get("email") or "")


def giai_claims(token: str) -> dict[str, Any]:
    """Giải payload JWT mà KHÔNG xác thực chữ ký: chỉ đọc claim của token
    chính mình đang giữ để biết email/gói — không dùng để cấp quyền."""
    try:
        phan = str(token or "").split(".")
        if len(phan) < 2:
            return {}
        p = phan[1] + "=" * (-len(phan[1]) % 4)
        du_lieu = json.loads(base64.urlsafe_b64decode(p))
        return du_lieu if isinstance(du_lieu, dict) else {}
    except Exception:
        return {}


def _che_email(email: str) -> str:
    email = str(email or "")
    if "@" not in email:
        return ""
    ten, mien = email.rsplit("@", 1)
    return (ten[:1] + "***@" + mien) if ten else "***@" + mien


def trich_code_state(url_callback: str) -> tuple[str, str]:
    """Trích (code, state) từ URL callback do người dùng dán.

    Từ chối mọi URL không phải loopback 127.0.0.1/localhost:1455/auth/callback
    (chống dán nhầm URL lạ / open redirect).
    """
    try:
        phan = urllib.parse.urlparse(str(url_callback or "").strip())
    except Exception:
        raise LoiOAuth("url_callback_sai") from None
    if phan.scheme not in ("http",):
        raise LoiOAuth("url_callback_sai", chi_tiet="scheme phải là http")
    if phan.hostname not in ("localhost", "127.0.0.1"):
        raise LoiOAuth("url_callback_sai", chi_tiet="host phải là loopback")
    if (phan.port or 80) != 1455:
        raise LoiOAuth("url_callback_sai", chi_tiet="cổng phải là 1455")
    if phan.path not in ("/auth/callback",):
        raise LoiOAuth("url_callback_sai", chi_tiet="đường dẫn lạ")
    qs = urllib.parse.parse_qs(phan.query)
    code = (qs.get("code") or [""])[0]
    state = (qs.get("state") or [""])[0]
    if not code or not state:
        raise LoiOAuth("url_callback_thieu_code_hoac_state")
    return code, state


def lui_dan_giay(lan: int) -> float:
    """Giây nghỉ trước lần thử lại thứ `lan` (0-based): 1,2,4,… trần 60."""
    return min(TRAN_LUI_GIAY, float(2 ** max(0, int(lan))))


def don_bo_dem_lam_moi() -> None:
    """Xoá toàn bộ cache/khoá làm mới trong tiến trình (chỉ dùng cho test)."""
    with _khoa_lam_moi_guard:
        _khoa_lam_moi.clear()
    with _ket_qua_khoa:
        _ket_qua_gan_day.clear()


# ── Làm mới token (port codex_token_refresh.py) ─────────────────────────────

_khoa_lam_moi: dict[str, threading.Lock] = {}
_khoa_lam_moi_guard = threading.Lock()
_ket_qua_gan_day: dict[str, tuple[float, dict]] = {}
_ket_qua_khoa = threading.Lock()


def _lay_khoa(refresh_token: str) -> threading.Lock:
    with _khoa_lam_moi_guard:
        if len(_khoa_lam_moi) > _MAX_KHOA:
            for khoa in list(_khoa_lam_moi):
                khoa_obj = _khoa_lam_moi[khoa]
                if khoa_obj.acquire(blocking=False):
                    try:
                        _khoa_lam_moi.pop(khoa, None)
                    finally:
                        khoa_obj.release()
                if len(_khoa_lam_moi) <= _MAX_KHOA // 2:
                    break
        return _khoa_lam_moi.setdefault(refresh_token, threading.Lock())


def _doc_gan_day(refresh_token: str) -> dict | None:
    with _ket_qua_khoa:
        muc = _ket_qua_gan_day.get(refresh_token)
        if muc is None:
            return None
        luc, ket_qua = muc
        if time.time() - luc > TTL_KET_QUA_GIAY:
            _ket_qua_gan_day.pop(refresh_token, None)
            return None
        return ket_qua


def _ghi_gan_day(refresh_token: str, ket_qua: dict) -> None:
    with _ket_qua_khoa:
        bay_gio = time.time()
        for k in [k for k, v in _ket_qua_gan_day.items()
                  if bay_gio - v[0] > TTL_KET_QUA_GIAY]:
            _ket_qua_gan_day.pop(k, None)
        _ket_qua_gan_day[refresh_token] = (bay_gio, ket_qua)


async def lam_moi_token(refresh_token: str, device_id: str | None = None,
                        http: HttpGoi | None = None,
                        tim_theo_refresh=None,
                        bo_qua_cache: bool = False) -> dict[str, Any] | None:
    """Đổi refresh_token lấy access_token mới (port refresh_codex_token).

    An toàn đua: khoá theo refresh_token; lượt thứ hai kiểm lại cache 60 giây
    và bản ghi đã lưu (qua `tim_theo_refresh`) trước khi POST — tránh hai
    lượt cùng đổi làm xoay token mất một trong hai.
    `bo_qua_cache=True`: bỏ qua CẢ HAI lớp cache và hỏi thẳng máy chủ OAuth —
    bắt buộc ở đường phản ứng 401 (token vừa bị từ chối thì «bản ghi còn hạn»
    không còn đáng tin) và khi cần biết refresh_token còn sống hay không.
    Trả: dict token mới | {"error": "unrecoverable", "code"} | None (lỗi tạm).
    """
    if not refresh_token or not isinstance(refresh_token, str):
        return None
    khoa = _lay_khoa(refresh_token)
    # threading.Lock dùng được trong async khi giữ ngắn (không await trong).
    khoa.acquire()
    try:
        if not bo_qua_cache:
            gan_day = _doc_gan_day(refresh_token)
            if gan_day is not None:
                return gan_day
        if callable(tim_theo_refresh):
            try:
                hien_co = tim_theo_refresh(refresh_token)
            except Exception:
                hien_co = None
            if hien_co:
                han = float(hien_co.get("expires_at") or 0)
                if han - time.time() > 3600 and not bo_qua_cache:
                    return {
                        "access_token": hien_co["access_token"],
                        "refresh_token": refresh_token,
                        "expires_in": int(han - time.time()),
                        "expires_at": han,
                    }
                if device_id is None:
                    device_id = str(hien_co.get("device_id") or "") or None
        ket_qua = await _doi_refresh_thuc(refresh_token, device_id,
                                          http=http or _post_thuc)
        if ket_qua and "error" not in ket_qua:
            _ghi_gan_day(refresh_token, ket_qua)
        return ket_qua
    finally:
        khoa.release()


async def _doi_refresh_thuc(refresh_token: str, device_id: str | None,
                            http: HttpGoi) -> dict[str, Any] | None:
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CODEX_CLIENT_ID,
        "scope": CODEX_SCOPE,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Accept": "application/json"}
    if device_id:
        headers["X-Device-Id"] = device_id
    try:
        phan_hoi = await http("POST", CODEX_TOKEN_URL, data=body,
                              headers=headers, timeout=20)
    except Exception as exc:
        log.warning("oauth: làm mới lỗi mạng: %s", type(exc).__name__)
        return None
    if phan_hoi.status_code == 200:
        try:
            du_lieu = phan_hoi.json()
        except Exception:
            log.warning("oauth: làm mới 200 nhưng thân không phải JSON")
            return None
        access = str(du_lieu.get("access_token") or "")
        if not access:
            return None
        moi = str(du_lieu.get("refresh_token") or refresh_token)
        het_sau = int(du_lieu.get("expires_in") or 0)
        log.info("oauth: làm mới token thành công")
        return {
            "access_token": access,
            "refresh_token": moi,
            "expires_in": het_sau,
            "expires_at": time.time() + het_sau if het_sau > 0 else 0.0,
        }
    # Không phải 200: phân loại theo CẤU TRÚC mã lỗi của nhà cung cấp.
    code = _ma_loi_tu_than(phan_hoi)
    if code in UNRECOVERABLE_CODES:
        log.warning("oauth: làm mới không thể khôi phục (mã %s, http %s)",
                    code, phan_hoi.status_code)
        return {"error": "unrecoverable", "code": code}
    log.warning("oauth: làm mới lỗi tạm (http %s, mã %s)",
                phan_hoi.status_code, code or "không rõ")
    return None


# ── Bộ nghe callback (port codex_callback_listener.py) ──────────────────────

class BoNgheCallback:
    """Máy chủ HTTP tí hon nhận GET /auth/callback.

    Chỉ bind 127.0.0.1 (loopback); tự đóng sau khi nhận MỘT callback xong
    (thành công hay thất bại) hoặc khi hết hạn; log KHÔNG ghi request-line
    (chứa ?code=…).
    """

    def __init__(self, cho: ChoOAuth, cong: int = 1455,
                 het_han_giay: float = 600.0):
        self._cho = cho
        self._cong_yeu_cau = cong
        self._het_han_giay = het_han_giay
        self._may_chu: HTTPServer | None = None
        self._luong: threading.Thread | None = None
        self._ket_qua: dict | None = None
        self._khoa = threading.Lock()
        self.cong_thuc = 0
        self.dia_chi_nghe = "127.0.0.1"

    def bat_dau(self) -> None:
        if self._luong and self._luong.is_alive():
            return
        ngoai = self

        class _XuLy(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # noqa: ARG002
                # KHÔNG log request-line: chứa ?code=…&state=…
                log.info("oauth: callback đã tới")

            def do_GET(self):  # noqa: N802
                phan = urllib.parse.urlparse(self.path)
                if phan.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"not found")
                    return
                qs = urllib.parse.parse_qs(phan.query)
                code = (qs.get("code") or [""])[0]
                state = (qs.get("state") or [""])[0]
                if not code or not state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"missing code/state")
                    ngoai._hoan_tat({"ok": False,
                                     "ma_loi": "thieu_code_hoac_state"})
                    return
                body = ("<html><body>Đăng nhập Codex xong — "
                        "bạn có thể đóng tab này.</body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                # Chỉ GHI NHẬN (code, state) để module đổi mã bằng đúng http
                # giả/thật của nó — không đổi mã ngay tại đây (tránh đua đổi
                # hai lần như bản c2a phải nuốt lỗi state-đã-dùng).
                ngoai._hoan_tat({"ok": True, "code": code, "state": state})

        try:
            self._may_chu = HTTPServer(("127.0.0.1", self._cong_yeu_cau), _XuLy)
        except OSError as exc:
            log.warning("oauth: không bind được cổng nghe: %s",
                        type(exc).__name__)
            self._hoan_tat({"ok": False, "ma_loi": "cong_ban"})
            return
        self.cong_thuc = self._may_chu.server_address[1]

        def _chay():
            try:
                assert self._may_chu is not None
                self._may_chu.serve_forever(poll_interval=0.2)
            except Exception:
                pass

        self._luong = threading.Thread(target=_chay, daemon=True,
                                       name="openai-codex-oauth-1455")
        self._luong.start()

        def _hen_gio():
            time.sleep(self._het_han_giay)
            self._hoan_tat({"ok": False, "ma_loi": "het_han_cho_callback"})

        threading.Thread(target=_hen_gio, daemon=True).start()
        log.info("oauth: bộ nghe callback đã mở (127.0.0.1:%s)", self.cong_thuc)

    def _hoan_tat(self, ket_qua: dict) -> None:
        with self._khoa:
            if self._ket_qua is not None:
                return
            self._ket_qua = ket_qua
        # Đóng từ LUỒNG KHÁC: handler chạy ngay trong luồng serve_forever,
        # shutdown() ở đây sẽ tự khoá chết.
        threading.Thread(target=self.dung, daemon=True).start()

    def lay_ket_qua(self, cho_giay: float = 5.0) -> dict | None:
        han = time.time() + cho_giay
        while time.time() < han:
            with self._khoa:
                if self._ket_qua is not None:
                    return dict(self._ket_qua)
            time.sleep(0.05)
        with self._khoa:
            return dict(self._ket_qua) if self._ket_qua else None

    def dung(self) -> None:
        try:
            if self._may_chu is not None:
                self._may_chu.shutdown()
                self._may_chu.server_close()
        except Exception:
            pass
        self._may_chu = None
