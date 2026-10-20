# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/main.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Captcha solver HTTP API.

Endpoints (all require Authorization: Bearer <CAPTCHA_SOLVER_API_KEY>):

    POST /v1/solve/turnstile          → solve Cloudflare Turnstile
    POST /v1/solve/recaptcha2         → solve reCAPTCHA v2 (checkbox/audio)
    POST /v1/solve/recaptcha3         → solve reCAPTCHA v3 (invisible)
    POST /v1/browser/run              → open URL, run JS, return DOM
    POST /v1/session/manual-login     → open URL in headful mode for VNC
    POST /v1/session/{profile}/close  → close a persistent profile's context
    GET  /v1/session/{profile}/status → quick health check on a profile
    GET  /health                      → unauthenticated liveness probe
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .accounts_db import (
    LOAI_GOOGLE,
    LOAI_OPENAI,
    delete_account,
    get_account,
    list_accounts,
    loai_theo_profile,
    resolve_account,
    save_account,
    set_totp,
)
from . import vault
from .auto_login import (
    get_session as get_login_session,
    list_sessions as list_login_sessions,
    start_auto_login,
    submit_2fa_code,
)
from .chatgpt_login import (
    get_session as get_chatgpt_session,
    start_chatgpt_onboard,
)
from .chatgpt_login import _sessions as chatgpt_sessions
from .gemini_web_login import (
    get_session as get_gemini_web_session,
    start_gemini_web_login,
    submit_2fa_code as submit_gemini_web_2fa_code,
)
from .claude_web_login import (
    get_session as get_claude_web_session,
    start_claude_web_login,
    submit_2fa_code as submit_claude_web_2fa_code,
)
from .openai_native_login import (
    get_session as get_openai_session,
    start_openai_login,
    submit_2fa_code as submit_openai_2fa_code,
)
from .solvers.gemini_web import (
    analyze_image as gemini_web_analyze_image,
    chat as gemini_web_chat,
    generate_image as gemini_web_generate_image,
    generate_music as gemini_web_generate_music,
    list_models as gemini_web_list_models,
    get_plan as gemini_web_get_plan,
)
from .browser_pool import HoSoDangBan, pool, _PROFILE_SLUG
from .settings import NHOM_TINH_NANG_HOP_LE, lay_cac_nhom_tinh_nang_bat, lay_khoa_dich_vu, settings  # W2A:
from .solvers.browser_run import browser_run
from .solvers.flow_google import (
    generate_image as flow_generate_image,
    get_or_create_project as flow_get_or_create_project,
)
from .solvers.flow_rpc import READY as FLOW_READY, open_project as flow_open_project
from .solvers.chatgpt_web import (
    analyze_image as chatgpt_web_analyze_image,
    chat as chatgpt_web_chat,
    generate_image as chatgpt_web_generate_image,
    list_models as chatgpt_web_list_models,
)
from .solvers.recaptcha import solve_recaptcha_v2, solve_recaptcha_v3
from .solvers.turnstile import solve_turnstile
from .codex_google_onboard import run_codex_google_onboard, CodexGoogleOnboardReq
from .github_codex_onboard import run_codex_onboard, CodexOnboardReq
from .workspaces import create_router as workspace_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("captcha-solver")

# Hạn chờ cho bước "nhả trình duyệt cũ trước khi đăng nhập lại".
_HAN_DON_TRUOC_DANG_NHAP_S = 20.0


async def _don_ho_so_truoc_khi_dang_nhap(profile: str) -> str:
    """Đóng trình duyệt cũ của hồ sơ trước khi đăng nhập lại. Trả "" nếu xong,
    hoặc câu lý do nếu hồ sơ đang bận — nơi gọi PHẢI dừng khi nhận được lý do.

    VÌ SAO PHẢI CÓ HẠN CHỜ: `close_profile` mặc định chờ khoá hồ sơ VÔ HẠN. Các
    đường đăng nhập lại gọi nó ngay trong handler HTTP, nên hồ sơ đang bận đồng
    nghĩa yêu cầu nằm im không bao giờ trả lời — đúng cơ chế đã gây sự cố
    nguoi.a@example.com ngày 09/08/2026 ở đường `auto-login-saved`. Nơi gọi
    (`jwt_refresh_scheduler`, `solver_selfheal`) hết hạn 30 giây rồi ghi log
    "self-heal thất bại", đưa người đọc đi sai hướng.

    Nặng hơn: handler bị bỏ dở vẫn ĐỨNG XẾP HÀNG. Lúc việc kia nhả hồ sơ thì nó
    tới lượt và đóng trình duyệt — nếu khi ấy đã có lượt đăng nhập khác nhận hồ
    sơ thì lượt đó chết, y hệt cảnh 05:48:57 ngày 10/08/2026.

    VÌ SAO HẾT GIỜ PHẢI DỪNG HẲN: đây là bước dọn BẮT BUỘC trước khi đăng nhập.
    Bỏ qua rồi vẫn đăng nhập tiếp nghĩa là đăng nhập đè lên hồ sơ mà việc khác
    đang dùng.
    """
    try:
        if pool.is_manual(profile):
            return "Workspace đang mở thủ công — đóng workspace trước khi đăng nhập lại."
        await pool.close_profile(profile, cho_toi_da=_HAN_DON_TRUOC_DANG_NHAP_S)
        if pool.is_manual(profile):
            return "Workspace vừa được mở thủ công — bỏ lượt đăng nhập lại."
        return ""
    except HoSoDangBan as exc:
        logger.warning("relogin: bỏ lượt vì hồ sơ %s đang bận", profile)
        return f"{exc} — thử lại sau khi việc kia xong."


# W2A: Xác thực Bearer bắt buộc dùng hmac.compare_digest chống timing attack, xoá bỏ nhánh tắt xác thực và khoá mặc định.
def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai khoá: cần 'Authorization: Bearer <khoá>'.")
    kieu, _, token = authorization.partition(" ")
    if kieu.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai khoá: cần 'Authorization: Bearer <khoá>'.")
    token = token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai khoá: cần 'Authorization: Bearer <khoá>'.")
    expected = lay_khoa_dich_vu()
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai khoá: cần 'Authorization: Bearer <khoá>'.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # W2A: Kiểm tra hoặc tự sinh khoá dịch vụ ngay lúc khởi động (fail-fast: khoá sai sẽ ném ngoại lệ dừng dịch vụ).
    lay_khoa_dich_vu()
    await pool.start()
    yield
    await pool.stop()


class LoiTinhNangTat(HTTPException):
    def __init__(self, nhom: str):
        super().__init__(
            status_code=403,
            detail=f"Tính năng '{nhom}' đang tắt trên máy chủ.",
        )
        self.nhom = nhom
        self.ma_loi = "tinh_nang_tat"


# W2A: Hàm phụ thuộc kiểm tra nhóm tính năng được bật trước khi chạy logic. Gắn thuộc tính nhom để test kiểm tra được đúng nhóm.
def can_tinh_nang(nhom: str):
    def _phu_thuoc(auth: None = Depends(require_api_key)) -> None:
        bat = lay_cac_nhom_tinh_nang_bat()
        if nhom not in bat:
            raise LoiTinhNangTat(nhom)
    _phu_thuoc.__name__ = f"can_tinh_nang_{nhom}"
    _phu_thuoc.__qualname__ = f"can_tinh_nang.<locals>.can_tinh_nang_{nhom}"
    _phu_thuoc.nhom = nhom  # W2A: Gắn thuộc tính nhóm cho dependency
    return _phu_thuoc


# W2A: Tắt docs_url, redoc_url, openapi_url theo luật kho để không lộ thông tin API khi chưa có khoá.
app = FastAPI(title="captcha-solver", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(workspace_router(pool, require_api_key, can_tinh_nang))


@app.exception_handler(LoiTinhNangTat)
async def _loi_tinh_nang_handler(request: Request, exc: LoiTinhNangTat):
    return JSONResponse(status_code=403, content={"detail": exc.detail, "ma_loi": exc.ma_loi})

# W2A: Bỏ CORSMiddleware vì dịch vụ chỉ phục vụ giao tiếp server-to-server nội bộ, không từ trình duyệt.


def _novnc_url(request: Request | None = None) -> str:
    """URL noVNC mà NGƯỜI DÙNG mở được, không phải địa chỉ nội bộ của container.

    `settings.novnc_external_url` mặc định "http://localhost:6080" — mà
    `localhost` là máy của người BẤM, nên mở từ điện thoại hay máy khác trong
    LAN là trỏ vào chính thiết bị đó và không thấy gì (đo 09/09).

    Nên khi biến chưa được đặt riêng, suy host TỪ CHÍNH REQUEST: người dùng vào
    bằng IP thì ra IP, vào bằng tên miền thì ra tên miền — không phải sửa cấu
    hình mỗi lần đổi đường vào. Có đặt biến thì tôn trọng biến (ai đặt là có ý).
    """
    dat_rieng = settings.novnc_external_url
    if dat_rieng and "localhost" not in dat_rieng and "127.0.0.1" not in dat_rieng:
        return dat_rieng
    host = ""
    if request is not None:
        # X-Forwarded-Host: khi đi qua Cloudflare Tunnel / reverse proxy thì
        # request.url.hostname là địa chỉ nội bộ, không mở lại được từ ngoài.
        host = (request.headers.get("x-forwarded-host")
                or request.headers.get("host") or "").split(",")[0].strip()
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if not host:
        return dat_rieng or "http://localhost:6080"
    scheme = "https" if (request and request.headers.get("x-forwarded-proto") == "https") else "http"
    return f"{scheme}://{host}:6080/vnc.html?host={host}&port=6080&autoconnect=1"


# W2A: Đường mở duy nhất không cần khoá theo luật kho (để Docker/máy chủ kiểm tra trạng thái sống còn), không chứa bí mật.
# noVNC mặc định tắt; chỉ trả URL khi bật biến CAPTCHA_SOLVER_BAT_NOVNC để tránh lộ cổng gỡ rối.
@app.get("/health")
async def health(request: Request) -> Any:
    bat_novnc = os.environ.get("CAPTCHA_SOLVER_BAT_NOVNC", "").strip().lower() in ("1", "true", "yes") or settings.bat_novnc
    ket_ok, ket_msg = vault.trang_thai_ket()
    kq: dict[str, Any] = {
        "status": "ok" if ket_ok else "degraded",
        "dich_vu": "c2a-dang-nhap",
        "novnc": _novnc_url(request) if bat_novnc else None,
    }
    if not ket_ok:
        kq["ket"] = "chua_san_sang"
        kq["canh_bao"] = ket_msg
        return JSONResponse(status_code=503, content=kq)
    return kq



class TurnstileReq(BaseModel):
    url: str
    sitekey: str | None = None
    profile: str = "default"
    headless: bool = True
    timeout: int | None = Field(default=None, ge=5, le=300)


class Recaptcha3Req(BaseModel):
    url: str
    sitekey: str
    action: str = "submit"
    profile: str = "default"
    headless: bool = True
    timeout: int | None = Field(default=None, ge=5, le=300)


class Recaptcha2Req(BaseModel):
    url: str
    profile: str = "default"
    headless: bool = True
    timeout: int | None = Field(default=None, ge=5, le=300)


class BrowserRunReq(BaseModel):
    url: str
    script: str | None = None
    wait_for: str | None = None
    profile: str = "default"
    headless: bool = True
    timeout: int = Field(default=30, ge=5, le=300)


class FacebookGroupPostReq(BaseModel):
    group_id: str
    message: str
    profile: str = "facebook"
    headless: bool = True
    timeout: int = Field(default=90, ge=15, le=300)


class ManualLoginReq(BaseModel):
    url: str
    profile: str = "default"
    # When true, kill any cached context for this profile and launch a
    # fresh Chrome. Use this from the "Mở lại noVNC" button when the
    # previous window died or noVNC shows a blank desktop.
    force: bool = False


class AutoLoginReq(BaseModel):
    profile: str = "google-fx"
    email: str
    password: str
    totp_secret: str = ""
    # "auth" → click Authenticator on the 2FA picker (state advances to
    # need_code). "tap" → skip the picker so Google falls through to the
    # tap-on-device prompt (state advances to need_tap).
    prefer_method: str = "auth"


class AutoLoginSavedReq(BaseModel):
    """Freshen Google session using password/TOTP already in accounts_db."""
    profile: str


class MultiOnboardReq(BaseModel):
    profile: str = "google-multi"
    email: str
    password: str
    # Same as AutoLoginReq.prefer_method but applied across all services.
    prefer_method: str = "auth"
    # Subset of {"gemini_web", "flow", "chatgpt", "claude", "codex"}. Order
    # matters — we trigger them sequentially after the shared Google login
    # succeeds. chatgpt/claude/codex ride the shared Google session so they
    # never re-nuke the profile or ask for a second 2FA.
    services: list[str] = Field(default_factory=lambda: ["gemini_web", "flow", "chatgpt"])
    # Chỉ cần khi services có "codex": URL OAuth (kèm state+PKCE) lấy từ
    # GET /api/oauth/codex/start của chatgpt2api. Không có thì bước codex báo lỗi
    # rõ ràng thay vì im lặng bỏ qua.
    codex_auth_url: str = ""
    # False = hiện cửa sổ trình duyệt trên VNC để người dùng theo dõi.
    headless: bool = True


def bu_credential(req) -> tuple[str, str, str]:
    """Trả (email, mật khẩu, hạt giống TOTP) — lấy từ kho khi request gửi rỗng.

    Từ 08/08/2026 máy chủ KHÔNG trả mật khẩu và hạt giống về trình duyệt nữa
    (`api_accounts_get`), vì trả ra là vô hiệu hoá toàn bộ lớp mã hoá của
    `vault.py`. Nhưng các thẻ onboard vẫn gửi đúng những trường đó lên, nên sau
    khi chọn một tài khoản đã lưu thì form rỗng và bấm đăng nhập là gửi mật khẩu
    RỖNG — hỏng mà không ai hiểu vì sao.

    Đây là mảnh còn thiếu của đường mà `/v1/session/auto-login-saved` đã đi:
    người gọi chỉ cần nói tài khoản nào, mật khẩu tự tra ở phía máy chủ và không
    rời khỏi tiến trình solver. Nhờ làm ở đây, MỌI thẻ onboard được vá cùng lúc
    mà không phải sửa từng file giao diện.

    Giá trị gửi lên vẫn được ưu tiên: người dùng gõ tay mật khẩu mới thì đó là ý
    định thật, kho không được đè lên.

    EMAIL CŨNG PHẢI ĐƯỢC BÙ, cùng bản ghi với mật khẩu. Bản cũ chỉ bù mật khẩu
    và hạt giống, nên một lượt gọi gửi cả ba trường rỗng đi ra khỏi đây với mật
    khẩu THẬT nhưng email RỖNG. Hai hệ quả nối nhau:

      · `start_chatgpt_onboard` có lá chắn "không mật khẩu thì đừng xoá hồ sơ";
        mật khẩu vừa được bù nên lá chắn không nổ, nhánh dưới XOÁ hồ sơ (mất
        phiên Google đang sống) rồi chạy đăng nhập mới.
      · `do_google_login_steps` chạy với email rỗng: đoạn dò tile so
        `innerText.includes("")` nên luôn đúng, và `press_sequentially("")` gõ
        đúng 0 ký tự rồi vẫn bấm "Tiếp theo".

    Đo thật 29/08/2026 (google-nguoi-c): log ghi "nuked profile"
    lúc 00:31:23, "clicked account tile for  on chooser screen" (email trống)
    lúc 00:31:32, rồi "bấm lại vào mail lần 1…40" từ 00:31:42 tới 00:37:53 —
    URL đứng im ở `/v3/signin/identifier` với cùng một `dsh=`, tức trang chưa
    hề tải lại lần nào. Bốn mươi lượt bấm "Tiếp theo" trên ô trống trong bảy
    phút là thứ khiến Google bung reCAPTCHA. Cả sáu đường onboard đều truyền
    thẳng `req.email` nên cả sáu cùng dính.
    """
    email = str(getattr(req, "email", "") or "").strip()
    mk = str(getattr(req, "password", "") or "").strip()
    tt = str(getattr(req, "totp_secret", "") or "").strip()
    if email and mk and tt:
        return email, mk, tt
    ho_so = str(getattr(req, "profile", "") or "").strip()
    khoa = email or ho_so
    if not khoa:
        return email, mk, tt
    # Kho nào là do TÊN HỒ SƠ quyết định, không phải email: cùng một địa chỉ
    # `@gmail.com` có thể có bản ghi ở cả hai kho với hai mật khẩu khác nhau.
    # Tra nhầm kho là gõ mật khẩu Google vào form OpenAI (hoặc ngược lại), và
    # sai vài lần liên tiếp thì tài khoản bị khoá.
    kho = loai_theo_profile(ho_so) if ho_so else None
    try:
        acct = resolve_account(khoa, kho) or {}
    except Exception:
        return email, mk, tt
    return (email or str(acct.get("email") or "").strip(),
            mk or str(acct.get("password") or "").strip(),
            tt or str(acct.get("totp_secret") or "").strip())


class TwoFactorCodeReq(BaseModel):
    code: str


class GetOrCreateProjectReq(BaseModel):
    profile: str = "google-fx"
    headless: bool = False
    timeout: int = Field(default=90, ge=20, le=300)


class GeminiWebOnboardReq(BaseModel):
    profile: str = "gemini-web-default"
    email: str
    password: str
    totp_secret: str = ""


class GeminiWebChatReq(BaseModel):
    profile: str = "gemini-web-default"
    prompt: str
    timeout: int = Field(default=90, ge=20, le=300)
    headless: bool = False


class ClaudeWebOnboardReq(BaseModel):
    profile: str = "claude-web-default"
    email: str = ""
    password: str = ""
    totp_secret: str = ""


class GrokWebOnboardReq(BaseModel):
    profile: str = "grok-web-default"
    email: str = ""
    password: str = ""
    totp_secret: str = ""




class GeminiWebImageReq(BaseModel):
    profile: str = "gemini-web-default"
    prompt: str
    count: int = Field(default=1, ge=1, le=4)
    timeout: int = Field(default=120, ge=30, le=300)
    headless: bool = False


class GeminiWebMusicReq(BaseModel):
    profile: str = "gemini-web-default"
    prompt: str
    # Nhạc Lyria mất ~100s, có lúc lâu hơn — trần rộng hơn ảnh.
    timeout: int = Field(default=240, ge=60, le=420)
    headless: bool = False


class GeminiWebVisionReq(BaseModel):
    profile: str = "gemini-web-default"
    image: str  # data:image/...;base64,... OR https URL
    prompt: str = "Phân tích nội dung ảnh này một cách chi tiết."
    timeout: int = Field(default=120, ge=30, le=300)
    headless: bool = False


class FlowImageReq(BaseModel):
    project_id: str
    prompt: str
    # Default 16:9 landscape. Other supported values matching Flow's pill
    # buttons: IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE, _SQUARE,
    # IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR,
    # _PORTRAIT (9:16).
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    # Strongest model by default. GEM_PIX_2 = Nano Banana Pro,
    # NARWHAL = Nano Banana 2, HARBOR_SEAL = Nano Banana 2 Lite.
    model: str = "GEM_PIX_2"
    # 1-4 images per request. Best-effort — Flow uses project default if
    # the dropdown click misses.
    count: int = Field(default=1, ge=1, le=4)
    tool: str = "PINHOLE"
    profile: str = "google-fx"
    # Flow's React app doesn't hydrate in true headless mode, so we default
    # to headful on Xvfb. Leave as-is unless you really know why.
    headless: bool = False
    timeout: int = Field(default=120, ge=15, le=300)
    # When true, the response is a single image/png body (the first image
    # downloaded from the Google CDN). Use this from Home Assistant or n8n
    # binary-handling nodes so you don't need a second HTTP call.
    return_binary: bool = False


class FlowVideoReq(BaseModel):
    prompt: str
    project_id: str = ""
    model: str = "flow/veo-3.1-lite"
    aspect_ratio: str = "16:9"
    duration: str | None = None
    count: int = 1
    image: str | None = None
    last_frame: str | None = None
    profile: str = "google-fx"
    headless: bool = True
    timeout: int = Field(default=300, ge=60, le=600)
    return_binary: bool = False


@app.post("/v1/solve/turnstile", dependencies=[Depends(can_tinh_nang("giai_captcha"))])
async def api_solve_turnstile(req: TurnstileReq) -> dict[str, Any]:
    try:
        return await solve_turnstile(
            url=req.url,
            sitekey=req.sitekey,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("turnstile solve failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/solve/recaptcha3", dependencies=[Depends(can_tinh_nang("giai_captcha"))])
async def api_solve_recaptcha3(req: Recaptcha3Req) -> dict[str, Any]:
    try:
        return await solve_recaptcha_v3(
            url=req.url,
            sitekey=req.sitekey,
            action=req.action,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("recaptcha3 solve failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/solve/recaptcha2", dependencies=[Depends(can_tinh_nang("giai_captcha"))])
async def api_solve_recaptcha2(req: Recaptcha2Req) -> dict[str, Any]:
    try:
        return await solve_recaptcha_v2(
            url=req.url,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("recaptcha2 solve failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/google/flow/generate-image", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_generate(req: FlowImageReq):
    """End-to-end Google Labs Flow image gen. Requires the `google-fx`
    profile to be logged in first via /v1/session/manual-login.

    Returns JSON by default. With `return_binary: true`, downloads the
    first generated image and returns it as `image/png` (handy for Home
    Assistant `rest_command`, n8n HTTP Request → Binary Data, etc).

    For `count > 1` we fan out N parallel calls to Flow (the API itself
    only generates 1 image per request — there's no batch field — so we
    parallelise the requests instead). Each call uses the same profile
    + project; the BrowserPool will serialise them if the profile only
    has one Chromium context, which keeps the same upper bound on
    parallelism as the user has logged-in accounts.
    """
    import asyncio

    async def _one() -> dict:
        return await flow_generate_image(
            project_id=req.project_id,
            prompt=req.prompt,
            aspect_ratio=req.aspect_ratio,
            model=req.model,
            count=1,  # always 1 per call — count handled at this layer
            tool=req.tool,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )

    try:
        n = max(1, min(4, int(req.count or 1)))
        if n == 1:
            result = await _one()
        else:
            # Fan-out, gather all (and surface the first exception if any).
            results = await asyncio.gather(*[_one() for _ in range(n)], return_exceptions=True)
            ok_results = [r for r in results if isinstance(r, dict)]
            failures = [r for r in results if not isinstance(r, dict)]
            if not ok_results:
                raise RuntimeError(f"all {n} parallel calls failed: {failures[0]}")
            # Merge: keep first result's metadata, concatenate `images`.
            result = dict(ok_results[0])
            merged_images = []
            for r in ok_results:
                merged_images.extend(r.get("images") or [])
            result["images"] = merged_images
            if failures:
                logger.warning("flow_partial_failure ok=%d failed=%d first_error=%s",
                                len(ok_results), len(failures), failures[0])
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("flow generate failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # ĐÓNG trình duyệt sau khi xong (kể cả khi lỗi) để nhả RAM/CPU.
        # Chủ máy yêu cầu 31/07: "sau khi lấy video thành công phải đóng trình
        # duyệt chứ, kể cả tạo ảnh, tạo video, đăng nhập lại gg".
        # Đo thật cùng ngày: 11 tiến trình chrome còn sống, một cái 60% CPU, load
        # 3,16 trên 4 lõi ⇒ health check (hạn 10s) bị quá hạn 6 lần liền và
        # container bị đánh dấu unhealthy. Các đường ĐĂNG NHẬP đã đóng sẵn
        # (auto_login, chatgpt_login, claude_web_login, codex onboard); riêng hai
        # đường tạo ảnh/tạo video Flow thì chưa — đây là chỗ rò tài nguyên.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi tạo ảnh Flow bỏ qua", exc_info=True)

    if not req.return_binary:
        return result

    images = result.get("images") or []
    if not images:
        raise HTTPException(status_code=502, detail="flow returned no images")
    first = images[0]
    url = first.get("url")
    if not url:
        raise HTTPException(status_code=502, detail="first image has no URL")
    if url.startswith("data:"):
        import base64
        header, b64data = url.split(",", 1)
        content = base64.b64decode(b64data)
        cdn_ct = header.split(";", 1)[0].split(":")[1]
        r_headers = {}
    else:
        try:
            cookies = {c["name"]: c["value"] for c in result.get("cookies", [])}
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, cookies=cookies) as client:
                r = await client.get(url)
                r.raise_for_status()
                content = r.content
                cdn_ct = (r.headers.get("content-type") or "").split(";")[0].strip()
                r_headers = r.headers
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("download flow image failed")
            raise HTTPException(status_code=502, detail=f"download failed: {exc}") from exc

    return Response(
        content=content,
        media_type=cdn_ct or first.get("mime") or "image/png",
        headers={
            "x-flow-image-id": str(first.get("id") or ""),
            "x-flow-model": str(first.get("model") or ""),
            "x-flow-seed": str(first.get("seed") or ""),
            "x-flow-elapsed-ms": str(result.get("elapsed_ms") or ""),
            "content-disposition": f'inline; filename="flow_{first.get("id","image")}.png"',
        },
    )


@app.post("/v1/google/flow/generate-video", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_generate_video(req: FlowVideoReq):
    """End-to-end Google Labs Flow Video generation."""
    try:
        from src.solvers.flow_google import flow_generate_video
        
        # We handle project_id exactly like the image route:
        # if project_id is empty, it needs get_or_create_project first.
        # But for now, assume the caller passed it or we wrap it in a helper.
        result = await flow_generate_video(
            project_id=req.project_id or "placeholder", # The UI usually sends it
            prompt=req.prompt,
            aspect_ratio=req.aspect_ratio,
            model=req.model,
            duration=req.duration,
            count=req.count,
            image_b64=req.image,
            last_frame_b64=req.last_frame,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        # GIỮ NGUYÊN mã trạng thái do lớp dưới đặt. Trước đây `except Exception`
        # bắt luôn HTTPException nên "429 Account Busy" (hồ sơ trình duyệt đang
        # bị lượt khác giữ) và "429 hết lượt" đều biến thành 502. Bên gọi không
        # phân biệt được "chờ chút rồi thử lại" với "hỏng thật".
        raise
    except Exception as exc:
        logger.exception("flow generate video failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # Xem ghi chú ở /v1/google/flow/generate-image: đóng trình duyệt để nhả
        # RAM/CPU, nếu không thì mỗi lượt tạo video để lại một Chromium sống và
        # health check của container bị quá hạn.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi tạo video Flow bỏ qua", exc_info=True)

    if not req.return_binary:
        return result

    try:
        import httpx
        url = result["data"][0]["url"]
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            r.raise_for_status()
            return Response(content=r.content, media_type="video/mp4")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Download video failed: {exc}")


# ── Flow qua REST (đường song song, không đụng ba endpoint DOM ở trên) ─────
#
# Ba endpoint dưới đây gọi thẳng API mà giao diện Flow gọi, trình duyệt chỉ còn
# dùng để xin access_token và đúc token reCAPTCHA (~1 giây/lượt thay vì 60-300
# giây điều khiển giao diện). Xem `src/solvers/flow_rest.py` để biết phép đo
# 09/08/2026 đã chốt được những gì.
#
# Đặt cạnh chứ không thay thế: đường DOM vẫn là mặc định cho tới khi đường này
# chạy đủ lâu trên thật. Quay lại chỉ là đổi bên gọi, không phải revert.


class FlowRestImageReq(BaseModel):
    project_id: str
    prompt: str
    # GEM_PIX_2 = "Nano Banana Pro", model mạnh nhất và đã đo là hợp lệ
    # 09/08/2026. NANO_BANANA_PRO bị API trả 400 nên không dùng làm mặc định.
    model: str = "GEM_PIX_2"
    aspect_ratio: str = "16:9"
    count: int = Field(default=1, ge=1, le=4)
    # Ảnh tham chiếu dạng base64; được đẩy lên Flow trước rồi gắn vào imageInputs.
    images_b64: list[str] = Field(default_factory=list)
    profile: str = "google-fx"
    headless: bool = True
    timeout: int = Field(default=180, ge=30, le=600)


class FlowRestVideoReq(BaseModel):
    project_id: str
    prompt: str
    # text_to_video | image_start | image_start_end | component
    mode: str = "text_to_video"
    model_label: str | None = None
    model_key: str | None = None
    aspect_ratio: str = "16:9"
    duration: str | None = None
    count: int = Field(default=1, ge=1, le=4)
    # [{name, image_b64}] — `name` là tên file, và ở chế độ "component" nó chính
    # là từ khoá phải xuất hiện trong prompt thì ảnh mới được trỏ tới.
    images: list[dict[str, str]] = Field(default_factory=list)
    profile: str = "google-fx"
    headless: bool = True
    timeout: int = Field(default=180, ge=30, le=600)
    # Chờ video xong rồi mới trả, kèm khoá `data` đúng hình dạng đường DOM cũ.
    # MẶC ĐỊNH TẮT: bật là dựng lại đúng cái bẫy giữ một request HTTP mở hàng
    # trăm giây mà đường này sinh ra để bỏ đi. Chỉ bật cho bên gọi không có
    # hàng đợi (hiện là `api/veo_video.py`).
    wait: bool = False
    wait_timeout: int = Field(default=600, ge=60, le=1800)
    # Bậc trả phí khai trong `clientContext`. Mặc định PAYGATE_TIER_ONE — đọc
    # từ telemetry thật của giao diện Flow. Mở ra được vì đây là thuộc tính của
    # TÀI KHOẢN chứ không phải hằng số: telemetry báo tier theo từng tài khoản,
    # nên tài khoản bậc khác sẽ cần giá trị khác.
    paygate_tier: str = "PAYGATE_TIER_ONE"


class FlowRestStatusReq(BaseModel):
    project_id: str
    gen_ids: list[str]
    profile: str = "google-fx"
    headless: bool = True
    timeout: int = Field(default=60, ge=10, le=300)


class FlowProjectCheckReq(BaseModel):
    """Non-billing Flow session check for the exact configured project."""
    project_id: str
    profile: str = "google-fx"
    headless: bool = True
    timeout: int = Field(default=120, ge=15, le=300)


def _loi_flow_rest(exc: Exception) -> HTTPException:
    """Giữ nguyên mã HTTP của Google để bên gọi phân biệt được loại hỏng.

    429 (hết ngạch) và 403 (reCAPTCHA) cần xử lý khác nhau hẳn: cái đầu là cho
    tài khoản nghỉ rồi xoay sang tài khoản khác, cái sau là đúc lại token. Gộp
    hết thành 502 như đường cũ thì bên gọi không phân biệt được.
    """
    from .solvers.flow_rest import LoiFlowRest
    if isinstance(exc, LoiFlowRest):
        return HTTPException(status_code=exc.status, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/google/flow/check-project", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_check_project(req: FlowProjectCheckReq) -> dict[str, Any]:
    """Verify that the saved Google session can open this Flow project and RPC."""
    page = None
    try:
        async with pool.page(profile=req.profile, headless=req.headless) as page:
            await flow_open_project(page, req.project_id, req.profile)
            ready = bool(await page.evaluate(FLOW_READY))
            return {"ready": ready, "project_id": req.project_id if ready else ""}
    except HTTPException:
        raise
    except Exception as exc:
        logger.info("flow project check failed profile=%s: %s", req.profile, str(exc)[:160])
        from urllib.parse import urlsplit
        current = urlsplit(page.url) if page is not None else None
        if (current and current.hostname == "accounts.google.com"
                and (current.path.endswith(("/identifier", "/challenge/pwd")) or current.path == "/ServiceLogin")):
            raise HTTPException(401, {"code": "flow_login_required", "message": "Flow yêu cầu đăng nhập Google"}) from exc
        raise _loi_flow_rest(exc) from exc
    finally:
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True, cho_toi_da=0.2)
        except HoSoDangBan:
            pass


@app.post("/v1/google/flow/rest/generate-image", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_rest_image(req: FlowRestImageReq) -> dict[str, Any]:
    """Tạo ảnh Flow qua REST. Đồng bộ — trả media_ids + link xem trước."""
    import base64 as _b64

    from .solvers.flow_rest import tao_anh
    try:
        return await tao_anh(
            profile=req.profile,
            project_id=req.project_id,
            prompt=req.prompt,
            model=req.model,
            aspect_ratio=req.aspect_ratio,
            count=req.count,
            anh_tham_chieu=[_b64.b64decode(x) for x in req.images_b64],
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("flow rest generate-image failed")
        raise _loi_flow_rest(exc) from exc
    finally:
        # Nhả trình duyệt y như ba endpoint DOM ở trên. THIẾU KHỐI NÀY là lỗi
        # thật, đo 09/08/2026: sau một buổi gọi thử, ba hồ sơ Flow còn nguyên ba
        # Chromium sống (`/v1/session/list` báo loaded=true, pages=1) và CPU/RAM
        # của container leo lên trông thấy. Đường REST mở trình duyệt ít hơn
        # đường DOM nhiều, nhưng "ít hơn" không phải "không cần dọn".
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau lượt Flow REST bỏ qua", exc_info=True)



@app.post("/v1/google/flow/rest/generate-video", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_rest_video(req: FlowRestVideoReq) -> dict[str, Any]:
    """Gửi lệnh tạo video Flow qua REST.

    Mặc định trả về `gen_ids` sau 1-2 giây; bên gọi tra tiến độ bằng
    /v1/google/flow/rest/video-status. Với `wait: true` thì chờ xong rồi trả
    thêm khoá `data` đúng hình dạng mà `api/veo_video.py` đang đợi.
    """
    import base64 as _b64

    from .solvers.flow_rest import lay_bearer, tao_video, upload_anh_tham_chieu

    try:
        anh: list[dict[str, Any]] = []
        if req.images:
            # Ảnh phải có mediaId trước khi gửi lệnh tạo. Lấy token một lần rồi
            # đẩy tuần tự — mỗi ảnh là một lời gọi HTTP, không cần trình duyệt.
            bearer = await lay_bearer(req.profile, req.headless)
            for it in req.images:
                du_lieu = _b64.b64decode(it.get("image_b64") or "")
                media_id = await upload_anh_tham_chieu(
                    bearer=bearer, project_id=req.project_id, du_lieu=du_lieu,
                )
                anh.append({"name": it.get("name") or "", "mediaId": media_id})
        return await tao_video(
            profile=req.profile,
            project_id=req.project_id,
            prompt=req.prompt,
            che_do=req.mode,
            model_label=req.model_label,
            model_key=req.model_key,
            aspect_ratio=req.aspect_ratio,
            duration=req.duration,
            count=req.count,
            anh=anh,
            headless=req.headless,
            timeout=req.timeout,
            cho_xong=req.wait,
            cho_toi_da=req.wait_timeout,
            tier=req.paygate_tier,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("flow rest generate-video failed")
        raise _loi_flow_rest(exc) from exc
    finally:
        # Nhả trình duyệt y như ba endpoint DOM ở trên. THIẾU KHỐI NÀY là lỗi
        # thật, đo 09/08/2026: sau một buổi gọi thử, ba hồ sơ Flow còn nguyên ba
        # Chromium sống (`/v1/session/list` báo loaded=true, pages=1) và CPU/RAM
        # của container leo lên trông thấy. Đường REST mở trình duyệt ít hơn
        # đường DOM nhiều, nhưng "ít hơn" không phải "không cần dọn".
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau lượt Flow REST bỏ qua", exc_info=True)



@app.post("/v1/google/flow/rest/video-status", dependencies=[Depends(can_tinh_nang("luong_anh"))])
async def api_flow_rest_video_status(req: FlowRestStatusReq) -> dict[str, Any]:
    """Tra tiến độ video. Token còn hạn thì không đụng tới trình duyệt."""
    from .solvers.flow_rest import trang_thai_video
    try:
        return await trang_thai_video(
            profile=req.profile,
            project_id=req.project_id,
            gen_ids=req.gen_ids,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("flow rest video-status failed")
        raise _loi_flow_rest(exc) from exc


@app.post(
    "/v1/google/flow/get-or-create-project",
    dependencies=[Depends(can_tinh_nang("luong_anh"))],
)
async def api_flow_get_or_create_project(req: GetOrCreateProjectReq) -> dict[str, Any]:
    """List Flow projects the logged-in account already owns and return
    the first one's UUID, or click "Dự án mới" to create a fresh one
    and return its UUID. The profile MUST already be logged in.

    Used by the chatgpt2api UI's "1-click add account" flow:
      1. POST /v1/session/auto-login {profile, email, password}
      2. Poll /v1/session/{profile}/auto-login-status until success
      3. POST /v1/google/flow/get-or-create-project {profile}
      4. PATCH /api/settings to add the {profile, project_id, label}
         to flow.accounts
    """
    try:
        return await flow_get_or_create_project(
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("flow get_or_create_project failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/browser/run", dependencies=[Depends(can_tinh_nang("chay_tuy_y"))])
async def api_browser_run(req: BrowserRunReq) -> dict[str, Any]:
    try:
        return await browser_run(
            url=req.url,
            script=req.script,
            wait_for=req.wait_for,
            profile=req.profile,
            headless=req.headless,
            timeout=req.timeout,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("browser run failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.post("/v1/facebook/group-post", dependencies=[Depends(can_tinh_nang("facebook"))])
async def api_facebook_group_post(req: FacebookGroupPostReq) -> dict[str, Any]:
    """Đăng bài vào nhóm Facebook bằng phiên trong profile (mặc định 'facebook').

    Đăng nhập một lần qua /v1/session/manual-login với url=facebook.com — sau
    đó gọi endpoint này chạy headless. Lỗi selector kèm ảnh chụp để vá từ xa.
    """
    from .solvers.facebook_group import post_to_group
    try:
        return await post_to_group(
            req.group_id, req.message,
            profile=req.profile, headless=req.headless, timeout=req.timeout,
        )
    except Exception as exc:
        logger.exception("facebook group-post failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/session/manual-login", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_manual_login(req: ManualLoginReq, request: Request) -> dict[str, Any]:
    """Open `url` in a headful browser on the noVNC display so the user can
    sign in manually. The profile's user-data-dir persists, so the next
    automated call with the same `profile` is already logged in.

    Reuses the first existing page in the context (so the user sees ONE
    Chrome window in noVNC, not a new tab on every click). If `force=True`,
    any cached context for this profile is killed and a fresh Chrome is
    launched — use this when noVNC shows "Connected... :99" but the
    desktop is blank (Chrome died between calls).

    """
    ctx = await pool.get(
        profile=req.profile,
        headless=False,
        force_recreate=req.force,
    )
    # Reuse first non-closed page so noVNC user keeps ONE window.
    page = None
    for p in list(ctx.pages):
        try:
            if not p.is_closed():
                page = p
                break
        except Exception:
            continue
    if page is None:
        page = await ctx.new_page()
    try:
        await page.bring_to_front()
    except Exception:
        pass
    await page.goto(req.url, wait_until="domcontentloaded", timeout=30_000)

    return {
        "profile": req.profile,
        "url": req.url,
        "open_in_browser": _novnc_url(request),
        "force": req.force,
        "message": (
            "Mở noVNC URL ở trên, đăng nhập tài khoản trong cửa sổ Chromium. "
            "Cookies sẽ được lưu vào profile '{}' để các lần gọi sau dùng headless. "
            "Nếu desktop trống → gọi lại endpoint này với force=true.".format(req.profile)
        ),
    }


@app.post("/v1/session/auto-login", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_login(req: AutoLoginReq, request: Request) -> dict[str, Any]:
    """Start a CLI-driven Google login. Returns immediately with the
    initial session state — UI polls /v1/session/{profile}/auto-login-status
    to track progress and feeds 2FA codes via /auto-login-2fa-code.

    Anti-bot reality: Google often blocks automation in container/VPS
    setups. If state stalls or fails, the noVNC window is still open
    — the user can finish the remaining steps manually and the saved
    cookies persist either way.
    """
    email_tk, mat_khau, hat_giong = bu_credential(req)
    session = await start_auto_login(
        profile=req.profile,
        email=email_tk,
        password=mat_khau,
        prefer_method=req.prefer_method,
        totp_secret=hat_giong,
    )
    # Chỉ ghi lại kho khi THẬT SỰ có giá trị mới. `save_account` ghi đè, nên gọi
    # nó với chuỗi rỗng là xoá mất mật khẩu đã lưu — mà form giờ luôn rỗng sau
    # khi chọn một tài khoản có sẵn.
    if req.email.strip() and str(req.password or "").strip():
        try: save_account(req.email, req.password, req.totp_secret, "")
        except Exception: pass
    return {
        **session.to_dict(),
        "novnc": _novnc_url(request),
        "note": "Theo dõi tiến trình ở /v1/session/{profile}/auto-login-status. "
                "Mở noVNC để giám sát/can thiệp khi cần.",
    }


# ── Multi-service onboard ────────────────────────────────────────────────
# One Google login → trigger Gemini Web + Flow + ChatGPT onboards in
# sequence on the same persistent profile so the user only goes through
# the 2FA prompt once. ChatGPT may still fail at the chatgpt.com Cloudflare
# wall but Gemini/Flow inherit the Google session and succeed silently.

_MULTI_STATES: dict[str, dict[str, Any]] = {}


def _multi_state(profile: str) -> dict[str, Any]:
    return _MULTI_STATES.setdefault(profile, {
        "profile": profile,
        "stage": "idle",
        "results": {},   # service → {"state": ..., "token": ..., "error": ...}
        "started_at": None,
        "completed_at": None,
        "error": None,
    })


async def _run_multi(req: MultiOnboardReq) -> None:
    """Background runner for /v1/multi-onboard. State is exposed via
    /v1/multi-onboard/{profile}/status; the UI polls it and feeds the
    auth code (if any) via the existing /auto-login-2fa-code endpoint
    on the SAME profile."""
    import asyncio as _asyncio
    # Giải credential MỘT lần cho cả lượt: form có thể rỗng vì người dùng chọn
    # tài khoản đã lưu thay vì gõ lại (máy chủ không trả mật khẩu về trình duyệt
    # nữa). Mọi bước dịch vụ bên dưới dùng chung hai biến này.
    email_tk, mat_khau, hat_giong = bu_credential(req)
    state = _multi_state(req.profile)
    state["stage"] = "google_login"
    state["started_at"] = time.time()
    state["error"] = None
    try:
        # Step 1 — Google login (shared session for every service below).
        await start_auto_login(
            profile=req.profile,
            email=email_tk,
            password=mat_khau,
            prefer_method=req.prefer_method,
        )
        deadline = time.time() + 360
        while time.time() < deadline:
            await _asyncio.sleep(2)
            sess = get_login_session(req.profile)
            if sess is None:
                continue
            if sess.state == "success":
                break
            if sess.state in ("failed", "error"):
                state["stage"] = "failed"
                state["error"] = f"google_login: {sess.error or sess.message}"
                state["completed_at"] = time.time()
                return
        else:
            state["stage"] = "failed"
            state["error"] = "google_login: timeout"
            state["completed_at"] = time.time()
            return

        # Step 2 — Per-service onboard. Each service reuses the same
        # persistent Chrome profile so Google cookies are already there.
        for svc in req.services:
            state["stage"] = svc
            state["results"][svc] = {"state": "running"}
            try:
                if svc == "gemini_web":
                    s = await start_gemini_web_login(
                        profile=req.profile, email=email_tk, password=mat_khau,
                    )
                elif svc == "flow":
                    # Flow login = Google session + open labs.google. The
                    # existing Google session is enough; we just record success.
                    state["results"][svc] = {"state": "success", "note": "uses shared Google session"}
                    continue
                elif svc == "chatgpt":
                    # ChatGPT rides the shared Google session via reuse_session
                    # (no profile nuke, no 2nd 2FA) — just SSO + scrape JWT.
                    await start_chatgpt_onboard(
                        profile=req.profile, email=email_tk, password=mat_khau,
                        reuse_session=True,
                    )
                    cgpt_deadline = time.time() + 240
                    while time.time() < cgpt_deadline:
                        await _asyncio.sleep(2)
                        cur = get_chatgpt_session(req.profile)
                        if cur is None:
                            continue
                        if cur.state == "success":
                            state["results"][svc] = {
                                "state": "success",
                                "token": getattr(cur, "access_token", None),
                                "email": getattr(cur, "captured_email", None) or req.email,
                            }
                            break
                        if cur.state in ("failed", "error"):
                            state["results"][svc] = {
                                "state": "failed",
                                "error": cur.error or cur.message,
                            }
                            break
                    else:
                        state["results"][svc] = {"state": "failed", "error": "timeout"}
                    continue
                elif svc in ("claude_web", "claude"):
                    # Claude rides the shared Google session — scrape sessionKey.
                    await start_claude_web_login(
                        profile=req.profile, email=email_tk, password=mat_khau,
                    )
                    cl_deadline = time.time() + 240
                    while time.time() < cl_deadline:
                        await _asyncio.sleep(2)
                        cur = get_claude_web_session(req.profile)
                        if cur is None:
                            continue
                        if cur.state == "success":
                            state["results"][svc] = {
                                "state": "success",
                                "session_key": getattr(cur, "session_key", None),
                                "email": req.email,
                            }
                            break
                        if cur.state in ("failed", "error"):
                            state["results"][svc] = {"state": "failed", "error": cur.error or cur.message}
                            break
                    else:
                        state["results"][svc] = {"state": "failed", "error": "timeout"}
                    continue

                elif svc == "codex":
                    # Codex CƯỠI phiên Google dùng chung vừa đăng nhập ở trên:
                    # mở auth_url (OAuth có state+PKCE do chatgpt2api sinh) →
                    # bấm "Tiếp tục với Google" → trang đồng ý → nhận
                    # localhost:1455/auth/callback?code=… Listener 1455 của
                    # gateway tự đổi mã lấy token và nạp vào pool.
                    # PHẢI đứng SAU bước Google: hồ sơ chưa có phiên Google thì
                    # OAuth dừng ở trang nhập email ("no callback; stuck at
                    # accounts.google.com/…/signin/identifier").
                    from .codex_google_onboard import (
                        CodexGoogleOnboardReq,
                        run_codex_google_onboard,
                    )
                    auth_url = str(getattr(req, "codex_auth_url", "") or "").strip()
                    if not auth_url:
                        state["results"][svc] = {
                            "state": "failed",
                            "error": "thiếu codex_auth_url (lấy từ GET /api/oauth/codex/start)",
                        }
                        continue
                    res = await run_codex_google_onboard(CodexGoogleOnboardReq(
                        profile=req.profile,
                        auth_url=auth_url,
                        email=email_tk,
                        headless=bool(getattr(req, "headless", True)),
                    ))
                    state["results"][svc] = {
                        "state": res.get("state") or "failed",
                        "redirect_url": res.get("redirect_url"),
                        "error": res.get("error"),
                    }
                    continue

                else:
                    state["results"][svc] = {"state": "skipped", "error": "unknown service"}
                    continue
                # Poll the service-specific session until terminal.
                svc_deadline = time.time() + 240
                while time.time() < svc_deadline:
                    await _asyncio.sleep(2)
                    cur = get_gemini_web_session(req.profile)
                    if cur is None:
                        continue
                    if cur.state == "success":
                        token = getattr(cur, "access_token", None)
                        state["results"][svc] = {
                            "state": "success",
                            "token": token,
                            "email": getattr(cur, "captured_email", None) or req.email,
                        }
                        break
                    if cur.state in ("failed", "error"):
                        state["results"][svc] = {
                            "state": "failed",
                            "error": cur.error or cur.message,
                        }
                        break
                else:
                    state["results"][svc] = {"state": "failed", "error": "timeout"}
            except Exception as exc:
                state["results"][svc] = {"state": "failed", "error": str(exc)[:200]}

        state["stage"] = "done"
        state["completed_at"] = time.time()
    except Exception as exc:
        state["stage"] = "failed"
        state["error"] = str(exc)[:300]
        state["completed_at"] = time.time()


@app.post("/v1/codex-google-onboard", dependencies=[Depends(can_tinh_nang("tu_dang_ky"))])
async def api_codex_google_onboard(req: CodexGoogleOnboardReq) -> dict[str, Any]:
    """Ride the existing Google session in a google-* profile to complete a
    Codex OAuth authorize; returns the localhost callback URL (?code=...)."""
    return await run_codex_google_onboard(req)


@app.post("/v1/codex-onboard", dependencies=[Depends(can_tinh_nang("tu_dang_ky"))])
async def api_codex_onboard(req: CodexOnboardReq) -> dict[str, Any]:
    """Codex auto-login qua GITHUB (hàng loạt): mở auth_url trong browser pool
    (hiện trên VNC :99), đăng nhập Github + lấy mã IMAP, trả callback URL."""
    return await run_codex_onboard(req)










@app.post("/v1/session/auto-login-saved", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_login_saved(req: AutoLoginSavedReq, request: Request) -> dict[str, Any]:
    """Freshen Google login KHÔNG cần truyền password — tự tra credentials đã
    lưu (accounts_db) theo profile/email. Dùng cho auto-recovery: caller chỉ
    biết profile, mật khẩu Google nằm trong solver (không lộ ra ngoài)."""
    acct = resolve_account(req.profile)
    if not acct or not acct.get("password"):
        return {"state": "failed", "error": "no saved Google credentials for this profile"}
    totp = str(acct.get("totp_secret") or "").strip()
    session = await start_auto_login(
        profile=req.profile,
        email=str(acct.get("email") or ""),
        password=str(acct.get("password") or ""),
        prefer_method="auth" if totp else "tap",
        totp_secret=totp,
    )
    return {**session.to_dict(), "novnc": _novnc_url(request)}


@app.post("/v1/multi-onboard", dependencies=[Depends(can_tinh_nang("tu_dang_ky"))])
async def api_multi_onboard(req: MultiOnboardReq, request: Request) -> dict[str, Any]:
    """Kick off one Google login + fan out to multiple service onboards.

    UI flow:
      1. POST /v1/multi-onboard {email, password, prefer_method, services}
      2. Poll /v1/multi-onboard/{profile}/status to track progress
      3. When the embedded Google session reports state=need_code, POST
         the 6-digit Authenticator code to
         /v1/session/{profile}/auto-login-2fa-code (existing endpoint —
         same profile shared between the multi-flow and the embedded
         auto-login session).
      4. When stage=done, response.results[service].token is the JWT to
         feed into chatgpt2api's account pool.
    """
    import asyncio as _asyncio
    state = _multi_state(req.profile)
    state.update({
        "profile": req.profile,
        "stage": "starting",
        "results": {},
        "started_at": time.time(),
        "completed_at": None,
        "error": None,
        "prefer_method": req.prefer_method,
        "services": list(req.services),
    })
    _asyncio.create_task(_run_multi(req))
    return {
        "profile": req.profile,
        "stage": state["stage"],
        "services": req.services,
        "prefer_method": req.prefer_method,
        "novnc": _novnc_url(request),
        "note": "Poll /v1/multi-onboard/{profile}/status. Khi state Google "
                "ở need_code, POST mã vào /v1/session/{profile}/auto-login-2fa-code.",
    }


@app.get("/v1/multi-onboard/{profile}/status", dependencies=[Depends(can_tinh_nang("tu_dang_ky"))])
async def api_multi_onboard_status(profile: str) -> dict[str, Any]:
    state = dict(_multi_state(profile))
    # Surface the embedded Google login state so the UI can detect
    # need_code / need_tap without polling a second endpoint.
    g = get_login_session(profile)
    if g is not None:
        state["google"] = {
            "state": g.state,
            "message": g.message,
            "tap_number": g.tap_number,
            "error": g.error,
        }
    return state


@app.get(
    "/v1/session/{profile}/auto-login-status",
    dependencies=[Depends(can_tinh_nang("dang_nhap"))],
)
async def api_auto_login_status(profile: str) -> dict[str, Any]:
    session = get_login_session(profile)
    if session is None:
        return {"profile": profile, "state": "none", "message": "Chưa có phiên auto-login"}
    return session.to_dict()


@app.post(
    "/v1/session/{profile}/auto-login-2fa-code",
    dependencies=[Depends(can_tinh_nang("dang_nhap"))],
)
async def api_auto_login_2fa_code(profile: str, req: TwoFactorCodeReq) -> dict[str, Any]:
    """Feed an SMS / TOTP / backup code to a session currently in
    state=need_code. Returns 409 if the session isn't asking for one."""
    ok = submit_2fa_code(profile, req.code)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Phiên không ở trạng thái cần mã (chỉ submit được khi state=need_code)",
        )
    return {"profile": profile, "submitted": True}


@app.get("/v1/session/auto-login-sessions", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_login_sessions() -> dict[str, Any]:
    """Snapshot of every auto-login session (running + recently finished)."""
    return {"sessions": list_login_sessions()}


@app.get("/v1/session/{profile}/warmup", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_session_warmup(profile: str, provider: str = "gemini_web") -> dict[str, Any]:
    """Khởi động ngầm trình duyệt (warm up) để giảm độ trễ cho câu hỏi đầu tiên."""
    # Đã vô hiệu hoá logic giữ Chrome chạy ngầm (warmup) để tiết kiệm 100% CPU.
    # Trình duyệt giờ đây sẽ CHỈ được bật lên khi có request chat thực tế từ người dùng.
    return {"profile": profile, "status": "warmed_up_bypassed_to_save_cpu"}


@app.get("/v1/profiles", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_list_profiles(prefix: str = "") -> dict[str, Any]:
    """List all available browser profiles matching a prefix."""
    try:
        from src.settings import settings
        import os
        profiles_dir = settings.data_dir / "profiles"
        if not profiles_dir.exists():
            return {"profiles": []}
        profiles = []
        for d in profiles_dir.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                profiles.append(d.name)
        return {"profiles": sorted(profiles)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list profiles")
        raise HTTPException(status_code=500, detail=str(exc)) from exc



# ── Gemini Web (gemini.google.com) ──────────────────────────────────────

@app.get("/v1/gemini-web/{profile}/plan", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_plan(profile: str, headless: bool = True, timeout: int = 30) -> dict[str, Any]:
    """Detected subscription tier of a Gemini Web profile.

    Wraps `solvers.gemini_web.get_plan` so the chatgpt2api provider-tree
    can show a free/plus/pro/ultra badge next to each onboarded
    profile. Plan classification is text-pattern based — there's no
    public API for it on Google's side.
    """
    try:
        return await gemini_web_get_plan(profile=profile, headless=headless, timeout=timeout)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web get_plan failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/gemini-web/{profile}/models", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_models(profile: str, headless: bool = True, timeout: int = 30) -> dict[str, Any]:
    """Live model list scraped from gemini.google.com's model picker.

    Returns `{"models": [...]}` shaped like the catalogue chatgpt2api
    exposes elsewhere — each item carries `id: "gmw/<slug>"` so the
    aggregator in /v1/models can drop them straight into the response.
    """
    try:
        models = await gemini_web_list_models(profile=profile, headless=headless, timeout=timeout)
        return {"profile": profile, "count": len(models), "models": models}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web list_models failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/gemini-web/onboard", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_gemini_web_onboard(req: GeminiWebOnboardReq, request: Request) -> dict[str, Any]:
    email_tk, mat_khau, hat_giong = bu_credential(req)
    prefer = getattr(req, "prefer_method", "auth" if hat_giong else "tap")
    session = await start_gemini_web_login(
        profile=req.profile, email=email_tk, password=mat_khau,
        totp_secret=hat_giong, prefer_method=prefer,
    )
    if req.email.strip() and str(req.password or "").strip():
        try: save_account(req.email, req.password, req.totp_secret, "")
        except Exception: pass
    return {
        **session.to_dict(),
        "novnc": _novnc_url(request),
        "note": "Theo dõi tiến trình ở /v1/gemini-web/{profile}/onboard-status. "
                "Khi state=success, gọi /v1/gemini-web/chat để chat.",
    }


@app.get("/v1/gemini-web/{profile}/onboard-status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_gemini_web_onboard_status(profile: str) -> dict[str, Any]:
    session = get_gemini_web_session(profile)
    if session is None:
        return {"profile": profile, "state": "none", "message": "Chưa có phiên onboard"}
    return session.to_dict()


@app.post("/v1/gemini-web/{profile}/onboard-2fa-code", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_gemini_web_onboard_2fa_code(profile: str, req: TwoFactorCodeReq) -> dict[str, Any]:
    ok = submit_gemini_web_2fa_code(profile, req.code)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Phiên không ở state=need_code",
        )
    return {"profile": profile, "submitted": True}


@app.post("/v1/gemini-web/{profile}/relogin-via-google", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_gemini_web_relogin_via_google(profile: str) -> dict[str, Any]:
    """Re-login gemini.google.com bằng credential đã lưu (resolve theo profile).
    Profile còn phiên Google → ride SSO (không nhập tay); mất phiên → tự
    email/password/2FA. Dùng cho self-heal khi 1PSID hết hạn."""
    acct = resolve_account(profile)
    if not acct:
        raise HTTPException(404, f"No saved credentials for profile '{profile}'")
    if (ban := await _don_ho_so_truoc_khi_dang_nhap(profile)):
        raise HTTPException(409, ban)
    session = await start_gemini_web_login(
        profile=profile, email=acct["email"], password=acct["password"],
        totp_secret=acct.get("totp_secret", ""),
    )
    return {**session.to_dict(), "note": "Poll /v1/gemini-web/{profile}/onboard-status."}


# ── ChatGPT bằng tài khoản OpenAI gốc (email + mật khẩu + TOTP) ─────────
#
# Khác `/v1/chatgpt/onboard`: đường đó bấm "Continue with Google" và chỉ phục vụ
# tài khoản Google. Đường này điền thẳng vào form của OpenAI, cho tài khoản mua
# theo lô — thứ mà đuôi email (@gmail hay @icloud) không nói lên được gì.

class OpenAIOnboardReq(BaseModel):
    profile: str = "openai-default"
    email: str = ""
    password: str = ""
    totp_secret: str = ""


@app.post("/v1/openai-native/onboard", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_openai_native_onboard(req: OpenAIOnboardReq, request: Request) -> dict[str, Any]:
    email_tk, mat_khau, hat_giong = bu_credential(req)
    session = await start_openai_login(
        profile=req.profile, email=email_tk, password=mat_khau,
        totp_secret=hat_giong,
    )
    if req.email.strip() and req.password.strip():
        try:
            # Kho RIÊNG: mật khẩu ở đây là mật khẩu của chính OpenAI. Ghi chung
            # kho Google là đè mất mật khẩu Google của cùng địa chỉ email đó.
            save_account(req.email, req.password, req.totp_secret, "", LOAI_OPENAI)
        except Exception:
            pass
    return {
        **session.to_dict(),
        "novnc": _novnc_url(request),
        "note": "Theo dõi /v1/openai-native/{profile}/onboard-status. "
                "Không có hạt giống TOTP thì phiên dừng ở state=need_code.",
    }


@app.get("/v1/openai-native/{profile}/onboard-status",
         dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_openai_native_status(profile: str) -> dict[str, Any]:
    session = get_openai_session(profile)
    if session is None:
        return {"profile": profile, "state": "none", "message": "Chưa có phiên onboard"}
    return session.to_dict()


@app.post("/v1/openai-native/{profile}/onboard-2fa-code",
          dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_openai_native_2fa_code(profile: str, req: TwoFactorCodeReq) -> dict[str, Any]:
    if not submit_openai_2fa_code(profile, req.code):
        raise HTTPException(status_code=409, detail="Phiên không ở state=need_code")
    return {"profile": profile, "submitted": True}


@app.get("/v1/openai-native/{profile}/token", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_openai_native_token(profile: str) -> dict[str, Any]:
    """Access token đã lấy được. Chỉ trả khi phiên đã success."""
    session = get_openai_session(profile)
    if session is None or not session.access_token:
        raise HTTPException(404, "Chưa có token cho hồ sơ này")
    return {"profile": profile, "email": session.captured_email,
            "access_token": session.access_token}


# ── Claude Web (claude.ai) ──────────────────────────────────────────────

@app.post("/v1/claude-web/onboard", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_claude_web_onboard(req: ClaudeWebOnboardReq, request: Request) -> dict[str, Any]:
    email_tk, mat_khau, hat_giong = bu_credential(req)
    prefer = getattr(req, "prefer_method", "auth" if hat_giong else "tap")
    session = await start_claude_web_login(
        profile=req.profile, email=email_tk, password=mat_khau,
        totp_secret=hat_giong, prefer_method=prefer,
    )
    # Chỉ lưu credential khi có email thực, tránh ghi đè rác vào DB
    if req.email.strip() and req.password.strip():
        try: save_account(req.email, req.password, req.totp_secret, "")
        except Exception: pass
    return {
        **session.to_dict(),
        "novnc": _novnc_url(request),
        "note": "Theo dõi /v1/claude-web/{profile}/onboard-status. Khi state=success, "
                "lấy sessionKey ở /v1/claude-web/{profile}/session.",
    }


@app.get("/v1/claude-web/{profile}/onboard-status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_claude_web_onboard_status(profile: str) -> dict[str, Any]:
    session = get_claude_web_session(profile)
    if session is None:
        return {"profile": profile, "state": "none", "message": "Chưa có phiên onboard"}
    return session.to_dict()


@app.post("/v1/claude-web/{profile}/onboard-2fa-code", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_claude_web_onboard_2fa_code(profile: str, req: TwoFactorCodeReq) -> dict[str, Any]:
    ok = submit_claude_web_2fa_code(profile, req.code)
    if not ok:
        raise HTTPException(status_code=409, detail="Phiên không ở state=need_code")
    return {"profile": profile, "submitted": True}


@app.get("/v1/claude-web/{profile}/session", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_claude_web_session(profile: str) -> dict[str, Any]:
    """Return the scraped claude.ai sessionKey for a logged-in profile.

    chatgpt2api's api/claude.py calls this to obtain the cookie instead of
    storing a static session_key in config.
    """
    from .claude_web_login import get_saved_session
    try:
        session = await get_saved_session(profile)
    except HoSoDangBan:
        raise HTTPException(429, "Account Busy") from None
    except ValueError:
        raise HTTPException(400, "Tên hồ sơ không hợp lệ") from None
    except Exception:
        raise HTTPException(503, "Không đọc được phiên Claude đã lưu") from None
    if session is None or not session.session_key:
        raise HTTPException(status_code=404, detail="Chưa có sessionKey (onboard Claude Web trước)")
    return {"profile": profile, "session_key": session.session_key, "email": session.email}


# ── Grok Web (grok.com) ─────────────────────────────────────────────────
#
# Cùng bộ bốn endpoint như Claude Web, thêm ĐÚNG MỘT trạng thái nữa:
# «need_manual». Đo 20/09/2026: bộ giải Turnstile không qua được cửa Cloudflare
# của grok.com (thử cả ba cách, 60 giây vẫn nguyên), kể cả khi hồ sơ đã có sẵn
# «cf_clearance». Nên chỗ này nói thẳng là cần một cú tích tay qua noVNC, thay
# vì quay vòng thử lại rồi báo "thất bại" — báo thế là đổ lỗi nhầm chỗ.

@app.post("/v1/grok-web/onboard", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_grok_web_onboard(req: GrokWebOnboardReq, request: Request) -> dict[str, Any]:
    from .grok_web_login import start_grok_web_login
    novnc = _novnc_url(request)
    # Đi qua «bu_credential» như MỌI đường onboard khác: lấy thẳng trường email của
    # yêu cầu là bỏ qua kho tài khoản đã lưu, nên lần đăng nhập lại nào không kèm
    # mật khẩu sẽ lặng lẽ chạy với ô rỗng. Có test ghim chuyện này và nó đã bắt
    # được tôi ở bản đầu — đừng viết lại kiểu cũ.
    email_tk, mat_khau, hat_giong = bu_credential(req)
    session = await start_grok_web_login(
        profile=req.profile, email=email_tk, password=mat_khau,
        totp_secret=hat_giong, novnc_url=novnc,
    )
    if req.email.strip() and req.password.strip():
        try: save_account(req.email, req.password, req.totp_secret, "")
        except Exception: pass
    return {
        **session.to_dict(),
        "novnc": novnc,
        "note": "Theo dõi /v1/grok-web/{profile}/onboard-status. state=need_manual nghĩa là "
                "cần mở noVNC tích ô xác minh của Cloudflare rồi gọi lại onboard. "
                "state=success thì lấy cookie ở /v1/grok-web/{profile}/session.",
    }


@app.get("/v1/grok-web/{profile}/onboard-status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_grok_web_onboard_status(profile: str) -> dict[str, Any]:
    from .grok_web_login import get_session as get_grok_session
    session = get_grok_session(profile)
    if session is None:
        return {"profile": profile, "state": "none", "message": "Chưa có phiên onboard"}
    return session.to_dict()


@app.post("/v1/grok-web/{profile}/onboard-2fa-code", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_grok_web_onboard_2fa_code(profile: str, req: TwoFactorCodeReq) -> dict[str, Any]:
    from .grok_web_login import submit_2fa_code as submit_grok_2fa
    if not submit_grok_2fa(profile, req.code):
        raise HTTPException(status_code=409, detail="Phiên không ở state=need_code")
    return {"profile": profile, "submitted": True}


@app.get("/v1/grok-web/{profile}/session", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_grok_web_session(profile: str) -> dict[str, Any]:
    """Cookie phiên grok.com của một hồ sơ đã đăng nhập.

    Đọc thẳng từ hồ sơ trên đĩa nên dùng được cả khi trình duyệt đã đóng. Trả
    về TẤT CẢ cookie của grok.com trừ nhóm «cf_» của Cloudflare — cố ý không
    lọc theo một cái tên tự nghĩ ra, vì chưa ai đăng nhập được lần nào nên
    không ai biết chắc phiên nằm ở cookie tên gì.
    """
    from .grok_web_login import doc_cookie_phien
    try:
        cookies = await doc_cookie_phien(profile)
    except HoSoDangBan:
        raise HTTPException(429, "Account Busy") from None
    except ValueError:
        raise HTTPException(400, "Tên hồ sơ không hợp lệ") from None
    if not cookies:
        raise HTTPException(status_code=404, detail="Chưa có phiên Grok (onboard trước)")
    return {"profile": profile, "cookies": cookies, "cookie_names": sorted(cookies)}


@app.post("/v1/claude-web/{profile}/relogin-via-google", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_claude_web_relogin_via_google(profile: str) -> dict[str, Any]:
    """Re-login claude.ai bằng credential đã lưu (resolve theo profile→email). Nếu
    profile đã có phiên Google → claude ride SSO, KHÔNG cần nhập tay (reuse)."""
    acct = resolve_account(profile)
    if not acct:
        raise HTTPException(404, f"No saved credentials for profile '{profile}'")
    # Nhả trình duyệt cũ TRƯỚC khi đăng nhập lại — giống hai nhánh gemini-web và
    # chatgpt. Thiếu bước này thì hồ sơ hỏng vẫn còn một Chromium sống, vừa ăn
    # RAM vừa có thể giữ khoá hồ sơ làm lượt đăng nhập mới phải chờ.
    if (ban := await _don_ho_so_truoc_khi_dang_nhap(profile)):
        raise HTTPException(409, ban)
    session = await start_claude_web_login(
        profile=profile,
        email=acct["email"],
        password=acct["password"],
        totp_secret=acct.get("totp_secret", ""),
    )
    return {**session.to_dict(), "note": "Poll /v1/claude-web/{profile}/onboard-status de theo doi."}


@app.post("/v1/gemini-web/chat", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_chat(req: GeminiWebChatReq) -> dict[str, Any]:
    """Send a prompt to gemini.google.com (DOM-scrape approach).

    Profile must already be logged in via /v1/gemini-web/onboard.
    Returns the assistant's text response + elapsed_ms.
    """
    try:
        return await gemini_web_chat(
            profile=req.profile, prompt=req.prompt,
            timeout=req.timeout, headless=req.headless,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web chat failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/gemini-web/generate-image", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_generate_image(req: GeminiWebImageReq) -> dict[str, Any]:
    """Generate image(s) via Imagen — activates the 'Tạo hình ảnh' tool
    in Gemini's + menu, types the prompt, scrapes <img> from response."""
    try:
        return await gemini_web_generate_image(
            profile=req.profile, prompt=req.prompt, count=req.count,
            timeout=req.timeout, headless=req.headless,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web generate_image failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # Tạo ảnh là việc nặng và thưa (như Flow) — nhả trình duyệt ngay thay vì
        # chờ 5 phút hết hạn nhàn rỗi của warm-pool. Nhánh /chat và
        # /analyze-image thì KHÔNG đóng: đó là đường hội thoại, đóng mỗi lượt là
        # mỗi lượt phải dựng lại Chromium.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi tạo ảnh gemini-web bỏ qua", exc_info=True)


@app.post("/v1/gemini-web/generate-music", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_generate_music(req: GeminiWebMusicReq) -> dict[str, Any]:
    """Tạo NHẠC qua gemini.google.com (Lyria) — gõ prompt, chờ thẻ nhạc, lấy mp4.
    Lyria không gọi được qua HTTP API nên phải đi trình duyệt (xem solver)."""
    try:
        return await gemini_web_generate_music(
            profile=req.profile, prompt=req.prompt,
            timeout=req.timeout, headless=req.headless,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web generate_music failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # Việc nặng, thưa — nhả trình duyệt ngay (như tạo ảnh/video Flow), tôn
        # trọng dấu đang-đăng-nhập để không đóng nhầm giữa lượt login.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi tạo nhạc gemini-web bỏ qua", exc_info=True)


@app.post("/v1/gemini-web/analyze-image", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_gemini_web_analyze_image(req: GeminiWebVisionReq) -> dict[str, Any]:
    """Upload an image to Gemini Web and ask a question about it.
    Accepts `image` as either a data:image/...;base64 URL or an https URL."""
    try:
        return await gemini_web_analyze_image(
            profile=req.profile, image=req.image, prompt=req.prompt,
            timeout=req.timeout, headless=req.headless,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("gemini_web analyze_image failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/session/list", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_session_list() -> dict[str, Any]:
    """List all known profiles (each is a chromium user-data-dir) along
    with whether they're currently held open in the pool."""
    root = settings.data_dir / "profiles"
    profiles: list[dict[str, Any]] = []
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            size = 0
            try:
                size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
            except Exception:
                pass
            profiles.append({
                "name": child.name,
                "loaded": pool.is_loaded(child.name),
                "size_bytes": size,
                "path": str(child),
            })
    return {"profiles": profiles, "count": len(profiles)}


@app.get("/v1/session/{profile}/status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_session_status(profile: str) -> dict[str, Any]:
    ctx = pool.get_cached(profile)
    if ctx is None:
        return {"profile": profile, "loaded": False, "pages": 0, "cookies": 0}
    try:
        cookies = await ctx.cookies()
    except Exception:
        # Context exists in cache but is dead — return zeroes; next /get
        # call will evict it.
        return {"profile": profile, "loaded": False, "pages": 0, "cookies": 0, "stale": True}
    return {
        "profile": profile,
        "loaded": True,
        "pages": len(ctx.pages),
        "cookies": len(cookies),
    }


@app.post("/v1/session/{profile}/close", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_session_close(profile: str) -> dict[str, Any]:
    closed = await pool.close_profile(profile, user_requested=True)
    return {"profile": profile, "closed": closed}


@app.get("/v1/gemini-web/{profile}/cookies", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_gemini_web_cookies(profile: str) -> dict[str, Any]:
    """Export Google session cookies từ profile đã login.

    Trả __Secure-1PSID / __Secure-1PSIDTS để chatgpt2api (gma/ provider,
    lib gemini_webapi) gọi thẳng HTTP API gemini.google.com — không cần DOM.
    Pattern y hệt /v1/claude-web/{profile}/session của Claude.
    """
    try:
        cookies = await pool.read_cookies(profile, "https://gemini.google.com")
    except HoSoDangBan:
        raise HTTPException(429, "Account Busy") from None
    except ValueError:
        raise HTTPException(400, "Tên hồ sơ không hợp lệ") from None
    except Exception:
        raise HTTPException(503, "Không đọc được cookie của hồ sơ") from None
    wanted = {"__Secure-1PSID", "__Secure-1PSIDTS"}
    out = {c["name"]: c["value"] for c in cookies
           if c.get("name") in wanted and c.get("value")}
    if "__Secure-1PSID" not in out:
        raise HTTPException(
            status_code=404,
            detail="profile has no Google session (__Secure-1PSID missing) — onboard first",
        )
    return {"profile": profile, "cookies": out}


# ── ChatGPT onboard ──────────────────────────────────────────────────────

class ChatGPTOnboardReq(BaseModel):
    profile: str = "chatgpt-default"
    email: str = ""
    password: str = ""
    totp_secret: str = ""
    reuse_session: bool = False


class ChatGPT2FACodeReq(BaseModel):
    code: str


@app.post("/v1/chatgpt/onboard", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_chatgpt_onboard(req: ChatGPTOnboardReq) -> dict[str, Any]:
    """Onboard ChatGPT via Google. If totp_secret provided, 2FA is automatic."""
    email_tk, mat_khau, hat_giong = bu_credential(req)
    prefer = getattr(req, "prefer_method", "auth" if hat_giong else "tap")
    session = await start_chatgpt_onboard(
        profile=req.profile,
        email=email_tk,
        password=mat_khau,
        totp_secret=hat_giong,
        prefer_method=prefer,
        reuse_session=req.reuse_session,
    )
    if req.email.strip() and str(req.password or "").strip():
        try: save_account(req.email, req.password, req.totp_secret, "")
        except Exception: pass
    return {
        **session.to_dict(),
        "note": "Poll /v1/chatgpt/{profile}/onboard-status. "
                "Mo noVNC de giam sat/can thiep khi can.",
    }


@app.get("/v1/chatgpt/{profile}/onboard-status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_chatgpt_onboard_status(profile: str) -> dict[str, Any]:
    session = get_chatgpt_session(profile)
    if not session:
        return {"profile": profile, "state": "none", "message": "Chua co phien onboard"}
    return session.to_dict()


@app.post("/v1/chatgpt/{profile}/onboard-2fa-code", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_chatgpt_onboard_2fa_code(profile: str, req: ChatGPT2FACodeReq) -> dict[str, Any]:
    session = get_chatgpt_session(profile)
    if not session or session.state != "need_code":
        raise HTTPException(400, "Session khong o state need_code")
    session.pending_code = req.code.strip()
    return {"profile": profile, "state": session.state, "message": "Da nhan ma"}


# Tiền tố tên profile của các đường onboard — dùng để lần ra localpart rồi tìm
# profile ANH EM của cùng một tài khoản.
_TIEN_TO_HO_SO = ("google-", "chatgpt-web-", "chatgpt-", "openai-",
                  "gemini-web-", "gemini-", "claude-web-", "claude-")


def _ho_so_ung_vien(profile: str) -> list[str]:
    """Các profile có thể chứa phiên ChatGPT của tài khoản này, theo thứ tự ưu tiên.

    Bộ lịch làm mới JWT (`services/jwt_refresh_scheduler._profile_for_email`)
    suy ra tên profile là `google-<localpart>` cho MỌI tài khoản, vì JWT không
    nói tài khoản đã đăng nhập bằng đường nào. Tài khoản OpenAI gốc lại nằm ở
    `openai-<localpart>` (quy ước của `openai-native-card.tsx`), nên yêu cầu làm
    mới trỏ vào một profile không phải của nó: tier 1 không có phiên nào để
    quét, và tier 2 chạy luồng Google cho một tài khoản không hề có mật khẩu
    Google — hỏng cả hai tầng, tài khoản kẹt tới lúc có người bấm tay.

    KHÔNG suy ra kiểu tài khoản bằng "thư mục nào có thật" như
    `account_recovery._profile_for`: ở đây thư mục `google-<localpart>` KHÔNG
    phải bằng chứng. Chính tier 1 tạo ra nó — `pool.page()` mở Chrome với
    user_data_dir đó, nên sau đúng một lần làm mới hụt, tài khoản OpenAI gốc
    cũng có một thư mục `google-` đầy đủ nhưng chưa từng đăng nhập. Ngược lại
    `openai-<localpart>` chỉ có thể do thẻ OpenAI gốc tạo, nên nó LÀ bằng chứng.
    """
    ten = str(profile or "").strip()
    local = ten
    for pfx in _TIEN_TO_HO_SO:
        if ten.startswith(pfx):
            local = ten[len(pfx):]
            break
    ds = [ten] if ten else []
    anh_em = f"openai-{local}" if local else ""
    if anh_em and anh_em != ten:
        try:
            if (settings.data_dir / "profiles" / anh_em).is_dir():
                ds.append(anh_em)
        except OSError:
            pass
    return ds


@app.get("/v1/chatgpt/{profile}/refresh-jwt", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_chatgpt_refresh_jwt(profile: str) -> dict[str, Any]:
    """Re-scrape JWT from session, or full re-login if expired.

    Strategy:
    1. Try scraping JWT from existing browser session (fast)
    2. If JWT missing/expired, look up saved credentials from accounts_db
    3. Run full re-login — luồng OpenAI gốc hay luồng Google tuỳ theo profile
    4. Return fresh JWT
    """
    ung_vien = _ho_so_ung_vien(profile)
    # Step 1: Try quick scrape from existing session
    for ten in ung_vien:
        try:
            async with pool.page(profile=ten, headless=True) as page:
                if "chatgpt.com" not in (page.url or ""):
                    await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20_000)
                    await asyncio.sleep(3.0)
                from .chatgpt_login import _scrape_chatgpt_token
                token, email, preview = await _scrape_chatgpt_token(page)
                if token:
                    logger.info("refresh_jwt: quick scrape OK for %s", ten)
                    await _update_chatgpt2api_token(token)   # đẩy JWT mới vào pool chatgpt2api
                    return {
                        "profile": ten,
                        "ok": True,
                        "method": "scrape",
                        "access_token": token,
                        "access_token_preview": preview,
                        "captured_email": email,
                    }
        except HTTPException as exc:
            if exc.status_code == 429:
                raise
        except Exception:
            pass
        finally:
            try:
                await pool.close_profile(ten, bo_qua_khi_dang_nhap=True, cho_toi_da=0.2)
            except HoSoDangBan:
                pass

    # Step 2: Quick scrape failed — try full re-login with saved credentials.
    # Đăng nhập lại vào ứng viên CUỐI: `_ho_so_ung_vien` chỉ thêm profile
    # `openai-` khi nó có thật, và chỉ thẻ OpenAI gốc tạo ra được nó — có nó
    # nghĩa là tài khoản này đi đường OpenAI gốc, không phải đường Google.
    profile = ung_vien[-1] if ung_vien else profile
    acct = resolve_account(profile)
    if not acct:
        return {"profile": profile, "ok": False, "error": "Session expired + no saved credentials found for this profile"}

    # Tài khoản OpenAI gốc giữ mật khẩu và 2FA ở OpenAI, không có đường Google
    # nào để đi — chạy `start_chatgpt_onboard` cho nó là bấm "Continue with
    # Google" rồi điền mật khẩu OpenAI vào form của Google, chắc chắn hỏng.
    la_openai_goc = profile.startswith("openai-")
    logger.info("refresh_jwt: session expired for %s, re-logging in with saved credentials (%s)",
                profile, "openai-goc" if la_openai_goc else "google")
    try:
        # Kill old session. Hồ sơ đang bận thì DỪNG — endpoint này trả lỗi bằng
        # trường `error` chứ không ném, nên giữ đúng dạng đó để nơi gọi
        # (`jwt_refresh_scheduler`) đọc được lý do thay vì hết hạn chờ.
        if (ban := await _don_ho_so_truoc_khi_dang_nhap(profile)):
            return {"profile": profile, "ok": False, "error": ban}

        # Start re-login. Hai luồng khác nhau ở cách đăng nhập, nhưng phiên trả
        # về cùng bộ trường (state / access_token / captured_email) nên vòng chờ
        # bên dưới dùng chung.
        if la_openai_goc:
            # `start_openai_login` tự sinh mã 6 số từ hạt giống trong kho, nên
            # không cần ai nhập tay — điều kiện để việc này chạy được không
            # người trông.
            await start_openai_login(
                profile=profile,
                email=acct["email"],
                password=acct["password"],
                totp_secret=acct.get("totp_secret", ""),
            )
            doc_phien = get_openai_session
        else:
            await start_chatgpt_onboard(
                profile=profile,
                email=acct["email"],
                password=acct["password"],
                totp_secret=acct.get("totp_secret", ""),
            )
            doc_phien = get_chatgpt_session

        # Wait for completion (poll up to 5 minutes)
        deadline = time.time() + 300
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            s = doc_phien(profile)
            if not s:
                continue
            if la_openai_goc and s.state == "need_code":
                # Tài khoản bật 2FA nhưng kho không có hạt giống → luồng đăng
                # nhập đang chờ người gõ mã 6 số, mà ở đây không có ai. Trả lời
                # ngay thay vì treo hết 5 phút rồi mới báo "timed out": người
                # vận hành cần biết là THIẾU HẠT GIỐNG, không phải mạng chậm.
                # Chỉ áp cho luồng OpenAI gốc — đường Gmail giữ nguyên hành vi cũ.
                return {"profile": profile, "ok": False,
                        "error": "Cần mã 2FA nhập tay — kho chưa có hạt giống TOTP "
                                 "cho tài khoản này, không tự đăng nhập lại được"}
            if s.state == "success":
                if s.access_token:
                    # Update chatgpt2api accounts pool
                    await _update_chatgpt2api_token(s.access_token)
                    return {
                        "profile": profile,
                        "ok": True,
                        "method": "relogin-openai" if la_openai_goc else "relogin",
                        "access_token": s.access_token,
                        "access_token_preview": s.access_token_preview,
                        "captured_email": s.captured_email,
                    }
                return {"profile": profile, "ok": False, "error": "Re-login OK but no token scraped"}
            if s.state == "failed":
                return {"profile": profile, "ok": False, "error": s.error or "Re-login failed"}

        return {"profile": profile, "ok": False, "error": "Re-login timed out after 5 min"}
    except Exception as exc:
        logger.exception("refresh_jwt: re-login error for %s", profile)
        return {"profile": profile, "ok": False, "error": str(exc)[:200]}


def _chatgpt2api_auth_key() -> str:
    """Admin key của chatgpt2api (= config.auth_key) để POST /api/accounts. Đọc env
    CHATGPT2API_AUTH_KEY, fallback file /data/chatgpt2api_auth_key (persist qua mount)."""
    k = os.environ.get("CHATGPT2API_AUTH_KEY", "").strip()
    if k:
        return k
    try:
        import pathlib
        f = pathlib.Path("/data/chatgpt2api_auth_key")
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


# W2A: Mặc định vô hiệu hoá đẩy token ra ngoài; chỉ chạy khi nhóm 'day_token_ngoai' có trong tinh_nang_bat VÀ có CHATGPT2API_URL hợp lệ.
async def _update_chatgpt2api_token(access_token: str) -> None:
    """POST fresh token to chatgpt2api's accounts pool (kèm admin auth)."""
    if "day_token_ngoai" not in lay_cac_nhom_tinh_nang_bat():
        return
    chatgpt2api_url = (os.environ.get("CHATGPT2API_URL") or "").strip()
    if not chatgpt2api_url:
        return
    try:
        headers = {"Content-Type": "application/json"}
        _ak = _chatgpt2api_auth_key()
        if _ak:
            headers["Authorization"] = f"Bearer {_ak}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{chatgpt2api_url}/api/accounts",
                json={"tokens": [access_token]},
                headers=headers,
            )
            if r.status_code < 400:
                logger.info("refresh_jwt: updated chatgpt2api pool OK")
            else:
                logger.warning("refresh_jwt: chatgpt2api returned %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("refresh_jwt: failed to update chatgpt2api: %s", str(exc)[:120])


@app.post("/v1/chatgpt/{profile}/relogin-via-google", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_chatgpt_relogin_via_google(profile: str) -> dict[str, Any]:
    """Force re-login via Google OAuth for an existing ChatGPT profile using saved credentials."""
    acct = resolve_account(profile)
    if not acct:
        raise HTTPException(404, f"No saved credentials for profile '{profile}'")

    if (ban := await _don_ho_so_truoc_khi_dang_nhap(profile)):
        raise HTTPException(409, ban)
    from .chatgpt_login import start_chatgpt_onboard
    session = await start_chatgpt_onboard(
        profile=profile,
        email=acct["email"],
        password=acct["password"],
        totp_secret=acct.get("totp_secret", ""),
    )
    return {
        **session.to_dict(),
        "note": "Poll /v1/chatgpt/{profile}/onboard-status de theo doi.",
    }


# ── Background token auto-refresh scheduler ──────────────────────────────

_refresh_task: Optional[asyncio.Task] = None
_refresh_running = False


async def _auto_refresh_loop(interval_minutes: int = 30):
    """Background loop that refreshes all saved accounts' tokens periodically."""
    global _refresh_running
    _refresh_running = True
    logger.info("auto_refresh: started (interval=%d min)", interval_minutes)

    while _refresh_running:
        try:
            from .accounts_db import list_accounts as db_list

            # CHỈ kho Google: vòng này đăng nhập lại bằng `start_chatgpt_onboard`
            # (bấm "Continue with Google"). Cho tài khoản OpenAI gốc vào đây là
            # gõ mật khẩu OpenAI vào form Google — sai liên tiếp thì bị khoá.
            accounts = db_list(LOAI_GOOGLE)
            for acct in accounts:
                if not _refresh_running:
                    break
                email = acct["email"]
                profile = "chatgpt-" + email.split("@")[0].replace(".", "-")

                try:
                    try:
                        logger.info("auto_refresh: refreshing %s (profile=%s)", email, profile)
                        # Quick scrape first
                        scrape_ok = False
                        async with pool.page(profile=profile, headless=True) as page:
                            if "chatgpt.com" not in (page.url or ""):
                                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=20_000)
                                await asyncio.sleep(3.0)
                            from .chatgpt_login import _scrape_chatgpt_token
                            token, _, preview = await _scrape_chatgpt_token(page)
                            if token:
                                await _update_chatgpt2api_token(token)
                                logger.info("auto_refresh: %s OK (scrape)", email)
                                scrape_ok = True
                        if scrape_ok:
                            continue
                    except Exception as exc:
                        logger.warning("auto_refresh: scrape failed for %s: %s", email, str(exc)[:100])

                    # Scrape failed — full re-login
                    try:
                        full = get_account(email, LOAI_GOOGLE)
                        if not full:
                            continue

                        # Vòng nền: hồ sơ bận thì BỎ QUA tài khoản này, sang cái
                        # kế. Đứng chờ ở đây là treo cả vòng quét 30 phút.
                        if (ban := await _don_ho_so_truoc_khi_dang_nhap(profile)):
                            logger.info("auto_refresh: bỏ qua %s — %s", email, ban)
                            continue
                        await asyncio.sleep(1.0)

                        session = await start_chatgpt_onboard(
                            profile=profile,
                            email=full["email"],
                            password=full["password"],
                            totp_secret=full.get("totp_secret", ""),
                        )

                        deadline = time.time() + 300
                        while time.time() < deadline and _refresh_running:
                            await asyncio.sleep(2.0)
                            s = get_chatgpt_session(profile)
                            if not s:
                                continue
                            if s.state == "success" and s.access_token:
                                await _update_chatgpt2api_token(s.access_token)
                                logger.info("auto_refresh: %s OK (relogin)", email)
                                break
                            if s.state == "failed":
                                logger.warning("auto_refresh: %s re-login failed: %s", email, s.error)
                                break
                    except Exception as exc:
                        logger.warning("auto_refresh: %s error: %s", email, str(exc)[:100])
                finally:
                    # Đảm bảo tắt ngay trình duyệt sau khi xử lý xong tài khoản này
                    # — trừ khi một lượt đăng nhập khác đang dùng đúng hồ sơ đó.
                    await pool.close_profile(profile, bo_qua_khi_dang_nhap=True)

        except Exception as exc:
            logger.warning("auto_refresh: loop error: %s", str(exc)[:120])

        # Sleep between refresh cycles
        for _ in range(interval_minutes * 60):
            if not _refresh_running:
                break
            await asyncio.sleep(1.0)

    logger.info("auto_refresh: stopped")


@app.post("/v1/chatgpt/auto-refresh/start", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_refresh_start(interval_minutes: int = 30) -> dict[str, Any]:
    """Start background auto-refresh for all saved accounts."""
    global _refresh_task, _refresh_running
    if _refresh_running:
        return {"ok": True, "message": "Auto-refresh da chay san", "interval_minutes": interval_minutes}
    _refresh_task = asyncio.create_task(_auto_refresh_loop(interval_minutes))
    return {"ok": True, "message": f"Auto-refresh started (interval={interval_minutes} min)"}


@app.post("/v1/chatgpt/auto-refresh/stop", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_refresh_stop() -> dict[str, Any]:
    """Stop background auto-refresh."""
    global _refresh_running, _refresh_task
    _refresh_running = False
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None
    return {"ok": True, "message": "Auto-refresh stopped"}


@app.get("/v1/chatgpt/auto-refresh/status", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_auto_refresh_status() -> dict[str, Any]:
    """Check auto-refresh status."""
    return {"running": _refresh_running}


# ── ChatGPT Web (chatgpt.com direct) ─────────────────────────────────────

class ChatGPTWebChatReq(BaseModel):
    profile: str = "chatgpt-default"
    prompt: str
    timeout: int = 90
    headless: bool = True


class ChatGPTWebImageReq(BaseModel):
    profile: str = "chatgpt-default"
    image: str  # data: URL or https URL
    prompt: str = "Phan tich noi dung anh nay mot cach chi tiet."
    timeout: int = 120
    headless: bool = True


@app.get("/v1/chatgpt-web/{profile}/models", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_chatgpt_web_models(profile: str) -> dict[str, Any]:
    return await chatgpt_web_list_models(profile=profile, headless=True, timeout=30)


@app.post("/v1/chatgpt-web/chat", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_chatgpt_web_chat(req: ChatGPTWebChatReq) -> dict[str, Any]:
    return await chatgpt_web_chat(
        profile=req.profile,
        prompt=req.prompt,
        timeout=req.timeout,
        headless=req.headless,
    )


@app.post("/v1/chatgpt-web/generate-image", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_chatgpt_web_generate_image(req: ChatGPTWebChatReq) -> dict[str, Any]:
    try:
        return await chatgpt_web_generate_image(
            profile=req.profile,
            prompt=req.prompt,
            timeout=req.timeout,
            headless=req.headless,
        )
    finally:
        # Xem ghi chú ở /v1/gemini-web/generate-image: tạo ảnh xong thì nhả
        # trình duyệt ngay, đường hội thoại thì giữ ấm.
        try:
            await pool.close_profile(req.profile, bo_qua_khi_dang_nhap=True)
        except Exception:
            logger.debug("close_profile sau khi tạo ảnh chatgpt-web bỏ qua", exc_info=True)


@app.post("/v1/chatgpt-web/analyze-image", dependencies=[Depends(can_tinh_nang("ai_qua_trinh_duyet"))])
async def api_chatgpt_web_analyze_image(req: ChatGPTWebImageReq) -> dict[str, Any]:
    return await chatgpt_web_analyze_image(
        profile=req.profile,
        image=req.image,
        prompt=req.prompt,
        timeout=req.timeout,
        headless=req.headless,
    )


# ── Saved Accounts (SQLite) ──────────────────────────────────────────────

class SaveAccountReq(BaseModel):
    email: str
    password: str
    totp_secret: str = ""
    label: str = ""
    # 'google' (mặc định) hay 'openai' — hai kho tách biệt, xem accounts_db.
    loai: str = "google"


@app.get("/v1/accounts/saved", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_accounts_list(loai: str = "") -> list[dict]:
    """List saved accounts (no passwords exposed).

    `loai=google` / `loai=openai` để lấy ĐÚNG một kho. Bỏ trống trả cả hai —
    chỉ dùng cho việc quản trị; giao diện luôn truyền loại, nếu không thẻ
    OpenAI gốc lại hiện tài khoản Google và người dùng chọn nhầm.
    """
    return list_accounts(loai.strip().lower() or None)


@app.get("/v1/accounts/saved/{email}", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_accounts_get(email: str, loai: str = "") -> dict[str, Any]:
    """Thông tin tài khoản — KHÔNG kèm mật khẩu lẫn hạt giống TOTP.

    Bản cũ trả `dict(acct)` nguyên vẹn, tức là mật khẩu Google và hạt giống
    TOTP đi thẳng về trình duyệt. Điều đó vô hiệu hoá toàn bộ lớp mã hoá của
    `vault.py`: mã hoá tại chỗ chẳng còn nghĩa gì khi có một endpoint giải mã
    sẵn rồi đưa ra ngoài.

    Không cần nó nữa: `/v1/session/auto-login-saved` đăng nhập bằng cách TỰ tra
    credential từ kho, chỉ nhận tên profile. Mật khẩu không rời khỏi tiến
    trình solver.
    """
    acct = get_account(email, loai.strip().lower() or None)
    if not acct:
        raise HTTPException(404, "Account not found")
    return {
        "email": acct.get("email"),
        "label": acct.get("label"),
        "created_at": acct.get("created_at"),
        "updated_at": acct.get("updated_at"),
        "has_password": bool(str(acct.get("password") or "").strip()),
        "has_totp": bool(str(acct.get("totp_secret") or "").strip()),
    }


@app.get("/v1/accounts/saved/{email}/totp", dependencies=[Depends(can_tinh_nang("lo_bi_mat"))])  # W2A:
async def api_accounts_totp(email: str, loai: str = "") -> dict[str, Any]:
    """MÃ TOTP hiện tại — không bao giờ trả hạt giống.

    Giao diện trước đây tự sinh mã trong trình duyệt, nên nó phải giữ HẠT
    GIỐNG trong `localStorage`. Hạt giống sinh ra mọi mã 6 số từ nay về sau;
    mã 6 số thì chết sau 30 giây. Đưa việc sinh mã về máy chủ là đổi một bí
    mật vĩnh viễn lấy một giá trị dùng một lần.
    """
    import time as _t

    acct = get_account(email, loai.strip().lower() or None)
    if not acct:
        raise HTTPException(404, "Account not found")
    seed = str(acct.get("totp_secret") or "").strip()
    if not seed:
        raise HTTPException(404, "Tài khoản này chưa có TOTP")
    try:
        import pyotp
    except Exception:
        raise HTTPException(503, "pyotp chưa cài trên máy chủ")
    try:
        # Hạt giống hay được phát dạng nhóm 4 ký tự cách nhau bằng khoảng trắng.
        # Đường đăng nhập thật (openai_native_login) đã strip; ở đây phải strip
        # theo, nếu không nút "Xem mã TOTP" báo "không hợp lệ" oan một hạt giống
        # mà đăng nhập vẫn dùng tốt.
        code = pyotp.TOTP(seed.replace(" ", "")).now()
    except Exception:
        # Hạt giống hỏng (hoặc mất VAULT_MASTER_KEY nên giải mã ra rỗng).
        raise HTTPException(422, "Hạt giống TOTP không hợp lệ")
    return {"code": code, "seconds_remaining": 30 - int(_t.time()) % 30}


class DatTotpReq(BaseModel):
    totp_secret: str = ""


@app.get("/v1/accounts/saved/{email}/reveal", dependencies=[Depends(can_tinh_nang("lo_bi_mat"))])  # W2A:
async def api_accounts_reveal(email: str, loai: str = "") -> dict[str, Any]:
    """Mật khẩu + hạt giống TOTP — CHỈ khi người quản trị bấm 👁 trên ô «Tài khoản đã lưu».

    Chủ máy duyệt 06/10/2026 («Hiện đủ, có nút 👁 mới lộ»): trước đó kho không bao giờ trả credential về trình duyệt
    (08/08), nên chọn tài khoản chỉ thấy email + cờ có/không. Không tự lộ khi chọn — phải gọi đúng đường này; mỗi lần
    xem ghi nhật ký (ai cũng truy được ai đã xem lúc nào). Cùng lớp khoá `require_api_key` như các đường kho khác.
    """
    acct = get_account(email, loai.strip().lower() or None)
    if not acct:
        raise HTTPException(404, "Account not found")
    logger.warning("vault_reveal email=%s loai=%s", acct.get("email") or email, loai or "google")
    return {"email": acct.get("email") or email, "password": str(acct.get("password") or ""),
            "totp_secret": str(acct.get("totp_secret") or ""), "label": acct.get("label") or ""}


@app.put("/v1/accounts/saved/{email}/totp", dependencies=[Depends(can_tinh_nang("lo_bi_mat"))])  # W2A:
async def api_accounts_set_totp(email: str, req: DatTotpReq, loai: str = "") -> dict[str, Any]:
    """Đặt/xoá hạt giống TOTP. Nhận vào, KHÔNG bao giờ trả ra."""
    if not set_totp(email, str(req.totp_secret or "").strip(), loai.strip().lower() or None):
        raise HTTPException(404, "Account not found")
    return {"ok": True, "has_totp": bool(str(req.totp_secret or "").strip())}


@app.post("/v1/accounts/saved", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_accounts_save(req: SaveAccountReq) -> dict[str, Any]:
    """Save or update a saved account."""
    email = req.email.strip()
    name_lower = email.lower()
    if name_lower.startswith(("google-", "chatgpt-", "claude-", "gemini-", "flow-")):
        raise HTTPException(
            status_code=400,
            detail="Vui lòng nhập địa chỉ email Google hợp lệ (chứa @), không nhập tên Profile (bắt đầu bằng google-, chatgpt-,...)"
        )
    if "@" not in email:
        email = f"{email}@gmail.com"
        
    return save_account(email, req.password, req.totp_secret, req.label, req.loai)


@app.delete("/v1/accounts/saved/{email}", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_accounts_delete(email: str, loai: str = "") -> dict[str, Any]:
    """Delete a saved account."""
    ok = delete_account(email, loai.strip().lower() or None)
    if not ok:
        raise HTTPException(404, "Account not found")
    return {"ok": True, "email": email}


@app.delete("/v1/profiles/{profile}", dependencies=[Depends(can_tinh_nang("dang_nhap"))])
async def api_delete_profile(profile: str) -> dict[str, Any]:
    """Close (if open) then PERMANENTLY delete a profile's user-data-dir.

    chatgpt2api calls this when an account is removed so the on-disk browser
    profile doesn't linger after a UI delete (the orphaned-profile desync).
    Irreversible — the login session for that profile is lost.
    """
    import shutil
    # W2A: Dùng _PROFILE_SLUG và resolve() kiểm tra an toàn đường dẫn, chống thoát thư mục profiles
    ten = str(profile or "").strip()
    if not _PROFILE_SLUG.fullmatch(ten):
        raise HTTPException(status_code=400, detail="invalid profile name")
    goc = (settings.data_dir / "profiles").resolve()
    profile_dir = (goc / ten).resolve()
    try:
        profile_dir.relative_to(goc)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid profile name")
    if profile_dir == goc:
        raise HTTPException(status_code=400, detail="invalid profile name")

    try:
        await pool.close_profile(ten, user_requested=True)
    except Exception:
        logger.warning("close before delete failed for profile=%s", ten)
    if not profile_dir.exists():
        return {"profile": ten, "deleted": False, "reason": "not_found"}
    try:
        shutil.rmtree(profile_dir)
    except Exception as exc:
        logger.exception("Failed to delete profile dir")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    logger.info("deleted profile dir profile=%s", ten)
    return {"profile": ten, "deleted": True}
