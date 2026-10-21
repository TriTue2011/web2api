# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/workspaces.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""User-controlled persistent browser workspaces, independent of automation jobs."""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .browser_pool import HoSoDangBan, _PROFILE_SLUG
from .settings import settings


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class OpenWorkspace(BaseModel):
    url: str = Field(default="", max_length=2048)


async def click_checkbox(page):
    """One normal click, never modify DOM state, inject tokens or solve challenges."""
    for frame in page.frames:
        parsed = urlsplit(frame.url)
        if (parsed.scheme != "https" or parsed.hostname not in ("www.google.com", "www.recaptcha.net", "recaptcha.google.com")
                or parsed.path not in ("/recaptcha/api2/anchor", "/recaptcha/enterprise/anchor")):
            continue
        checkbox = frame.locator('#recaptcha-anchor[role="checkbox"]').first
        try:
            if not await checkbox.is_visible(timeout=500):
                continue
            if await checkbox.get_attribute("aria-checked") == "true":
                return {"status": "already_checked", "message": "Ô đã được tích; trang vẫn có thể yêu cầu xác minh tiếp."}
            await checkbox.click(timeout=2000)
            return {"status": "clicked", "message": "Đã bấm ô một lần. Nếu xuất hiện thử thách, hãy hoàn thành trên noVNC."}
        except Exception:
            return {"status": "needs_user", "message": "Không bấm được ô xác minh; hãy thao tác trực tiếp trên noVNC."}
    return {"status": "not_found", "message": "Không thấy ô reCAPTCHA đang hiển thị trong cửa sổ này."}


# W2A: Hỗ trợ gắn thêm dependency kiểm tra nhóm tính năng (mặc định thuộc nhóm dang_nhap).
def create_router(pool, authenticate, check_feature=None):
    deps = [Depends(authenticate)]
    if check_feature is not None:
        deps.append(Depends(check_feature("dang_nhap")))
    router = APIRouter(prefix="/v1/workspaces", dependencies=deps)

    def directory(profile):
        root = (settings.data_dir / "profiles").resolve()
        if not _PROFILE_SLUG.fullmatch(profile):
            raise HTTPException(400, "Tên hồ sơ không hợp lệ.")
        path = root / profile
        if path.is_symlink() or not path.is_dir():
            raise HTTPException(404, "Không tìm thấy workspace.")
        return path

    @router.get("")
    async def list_workspaces():
        root = settings.data_dir / "profiles"
        rows = []
        if root.exists():
            for child in sorted(root.iterdir()):
                if not _PROFILE_SLUG.fullmatch(child.name) or child.is_symlink() or not child.is_dir():
                    continue
                name = child.name
                try:
                    data = json.loads((child / ".workspace.json").read_text())
                    name = str(data.get("name") or name)[:100]
                except (OSError, ValueError, AttributeError):
                    pass
                rows.append({"profile": child.name, "name": name, "open": pool.is_loaded(child.name),
                             "manual": pool.is_manual(child.name)})
        return {"workspaces": rows}

    @router.post("")
    async def create(body: NewWorkspace):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Nhập tên workspace.")
        # Labels never determine the cookie directory: even identical names
        # create separate accounts, with no localpart/email slug collisions.
        profile = "workspace-" + secrets.token_hex(12)
        path = pool._profile_dir(profile)
        (path / ".workspace.json").write_text(json.dumps({"name": name}, ensure_ascii=False))
        return {"profile": profile, "name": name}

    @router.post("/{profile}/open")
    async def open_workspace(profile: str, body: OpenWorkspace):
        directory(profile)
        url = body.url.strip()
        if url:
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError()
            except ValueError:
                raise HTTPException(400, "URL cần là http(s), không chứa mật khẩu.") from None
        try:
            context = await pool.get(profile, headless=False, manual=True, cho_toi_da=5)
        except HoSoDangBan:
            raise HTTPException(409, "Hồ sơ đang có tác vụ; hãy chờ tác vụ xong rồi mở workspace.") from None
        pages = [page for page in context.pages if not page.is_closed()]
        page = pages[-1] if pages else await context.new_page()
        await page.bring_to_front()
        if url or page.url == "about:blank":
            try:
                await page.goto(url or "https://myaccount.google.com/", wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                # Preserve the window/session on navigation failure so the user
                # can correct the address or continue a partially loaded login.
                return {"profile": profile, "open": True, "warning": "Trang chưa tải xong. Mở noVNC để kiểm tra."}
        return {"profile": profile, "open": True}

    @router.post("/{profile}/close")
    async def close_workspace(profile: str):
        directory(profile)
        try:
            closed = await pool.close_profile(profile, user_requested=True, cho_toi_da=5)
        except HoSoDangBan:
            raise HTTPException(409, "Hồ sơ đang có tác vụ; chưa đóng được.") from None
        return {"profile": profile, "closed": closed, "session_preserved": True}

    @router.post("/{profile}/checkbox")
    async def checkbox(profile: str):
        directory(profile)
        context = pool.get_cached(profile)
        if context is None:
            raise HTTPException(409, "Mở workspace trước khi bấm ô xác minh.")
        # This is also available while an onboarding job waits for a human.
        pages = [page for page in context.pages if not page.is_closed()]
        for page in reversed(pages):
            result = await click_checkbox(page)
            if result["status"] != "not_found":
                return result
        return {"status": "not_found", "message": "Không thấy ô reCAPTCHA; hãy kiểm tra cửa sổ noVNC."}

    return router
