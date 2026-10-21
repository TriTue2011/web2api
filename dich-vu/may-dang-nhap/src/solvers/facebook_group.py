# NGUỒN: TriTue2011/chatgpt2api@d879b034fa21 — captcha-solver/src/solvers/facebook_group.py (chép từ c2a, KHÔNG viết lại; đã thay danh tính thật của nhà bằng ví dụ). Chỗ chỉnh cho web2api đánh dấu «# W2A:».
"""Đăng bài vào NHÓM Facebook bằng trình duyệt — vì Meta đã gỡ Groups API.

Meta xoá `publish_to_groups` + endpoint đăng `/group/feed` từ 22/04/2024: mọi
công cụ bên thứ ba mất đường API cùng lúc. Đường còn lại là thao tác như người
trên www.facebook.com bằng phiên đăng nhập THẬT — cùng bản chất với zalo_personal
(zca-js) đang chạy trong stack này. Chủ máy đã được nói rõ rủi ro (checkpoint /
khoá tài khoản) và chọn dùng tài khoản chính (13/08).

Phiên nằm trong profile "facebook" của browser_pool (user-data-dir bền qua
restart). Đăng nhập MỘT lần qua noVNC (/v1/session/manual-login) là các lần sau
chạy headless được — đúng UX của flow Google login sẵn có.

Selector Facebook đổi thường xuyên; mọi bước bám role/aria-label (ổn định hơn
class) và có ảnh chụp màn hình khi hỏng để vá từ xa: /data/fbgroup-loi.png.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..browser_pool import pool
from ..settings import settings

logger = logging.getLogger(__name__)

# Placeholder ô soạn bài trong nhóm — tiếng Việt và tiếng Anh, vì UI theo ngôn
# ngữ tài khoản. Bám chữ vì nút không có aria-label riêng.
_O_SOAN_RE = re.compile(
    r"(viết gì đó|bạn viết gì|bạn đang nghĩ gì|write something|what's on your mind)",
    re.I)
# Nút Đăng trong hộp thoại soạn — aria-label theo ngôn ngữ.
_NUT_DANG = 'div[role="dialog"] [aria-label="Đăng"], div[role="dialog"] [aria-label="Post"]'
_O_NHAP = 'div[role="dialog"] div[role="textbox"][contenteditable="true"]'


async def _chup_loi(page: Any, buoc: str) -> str:
    """Ảnh chụp lúc hỏng — bằng chứng duy nhất vá được selector từ xa."""
    duong = str(settings.data_dir / "fbgroup-loi.png")
    try:
        await page.screenshot(path=duong, full_page=False)
    except Exception:
        return ""
    logger.warning("fbgroup: hỏng ở bước %s — ảnh tại %s", buoc, duong)
    return duong


async def post_to_group(group_id: str, message: str, *, profile: str = "facebook",
                        headless: bool = True, timeout: int = 90) -> dict[str, Any]:
    """Đăng `message` vào nhóm `group_id` (id số hoặc slug trong URL nhóm).

    Trả {"status": "ok"|"chua_dang_nhap"|"loi", ...}. Link trong message để
    nguyên chữ — Facebook tự nhận và dựng thẻ xem trước sau vài giây.
    """
    gid = str(group_id).strip().strip("/")
    if not gid or not message.strip():
        return {"status": "loi", "detail": "thiếu group_id hoặc message"}

    async with pool.page(profile=profile, headless=headless) as page:
        await page.goto(f"https://www.facebook.com/groups/{gid}",
                        wait_until="domcontentloaded", timeout=timeout * 1000)

        # Chưa đăng nhập → Facebook đá về trang login hoặc bung form email.
        if "/login" in page.url or await page.locator('input[name="email"]').count():
            return {"status": "chua_dang_nhap",
                    "detail": "Profile chưa có phiên Facebook — cần đăng nhập "
                              "một lần qua noVNC (/v1/session/manual-login)."}

        # 1) Bấm ô "Viết gì đó…" để bung hộp thoại soạn bài.
        o_soan = page.locator('div[role="button"]', has_text=_O_SOAN_RE).first
        try:
            await o_soan.click(timeout=15_000)
        except Exception:
            anh = await _chup_loi(page, "tim_o_soan")
            return {"status": "loi", "buoc": "tim_o_soan", "anh": anh,
                    "detail": "Không thấy ô soạn bài — không phải thành viên "
                              "nhóm, nhóm chờ duyệt bài, hoặc Facebook đổi giao diện."}

        # 2) Gõ nội dung vào ô nhập (Lexical editor — insert_text ăn sự kiện
        #    input chuẩn, không cần gõ từng phím).
        try:
            o_nhap = page.locator(_O_NHAP).first
            await o_nhap.click(timeout=15_000)
            await page.keyboard.insert_text(message)
        except Exception:
            anh = await _chup_loi(page, "go_noi_dung")
            return {"status": "loi", "buoc": "go_noi_dung", "anh": anh,
                    "detail": "Không gõ được nội dung vào hộp soạn."}

        # Có link → chờ Facebook dựng thẻ xem trước, bài đăng ra mới có preview.
        if "http://" in message or "https://" in message:
            await page.wait_for_timeout(5_000)

        # 3) Bấm Đăng rồi chờ hộp thoại đóng — đóng nghĩa là đã gửi.
        try:
            nut = page.locator(_NUT_DANG).first
            await nut.click(timeout=15_000)
            await page.locator('div[role="dialog"]').first.wait_for(
                state="detached", timeout=45_000)
        except Exception:
            anh = await _chup_loi(page, "bam_dang")
            return {"status": "loi", "buoc": "bam_dang", "anh": anh,
                    "detail": "Bấm Đăng không xong — nhóm có thể bật duyệt bài "
                              "chậm, hoặc Facebook chặn."}

        return {"status": "ok", "group": gid, "url": page.url}
