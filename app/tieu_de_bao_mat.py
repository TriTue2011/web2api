"""Middleware TIÊU ĐỀ BẢO MẬT: gắn header bảo vệ vào MỌI phản hồi HTTP, CSP cho trang HTML, và Cache-Control cho tệp giao diện tĩnh.

Khác với middleware bảo vệ riêng của tuyến trình duyệt từ xa (THAY header) và
HEADER_TRANG_MODULE (đặt ở /m/), middleware này chỉ ĐẶT khi phản hồi CHƯA có header
đó — không đè giá trị riêng của route. Trang HTML nhận CSP với băm SHA-256 của các
khối script nội tuyến tương ứng (tính từ tệp trong web/); không thêm CSP vào JSON/API/tệp tĩnh.
KHÔNG thêm HSTS. Đứng NGOÀI CÙNG trong app/main.py để thấy cả phản hồi do middleware trong
hay trình xử lý ngoại lệ tạo ra (401/404/403, trang tĩnh, StreamingResponse).

Tệp giao diện tĩnh (.html, .js, .css và trang gốc /) nhận Cache-Control: no-cache khi chưa có
để trình duyệt luôn hỏi lại server bằng ETag trước khi dùng, tránh lỗi lệch phiên bản sau nâng cấp.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re
import urllib.parse

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: (tên thường, giá trị) — so tên theo chữ thường vì ASGI dùng bytes.
TIEU_DE_BAO_MAT: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)

#: Đuôi tệp giao diện tĩnh luôn cần kiểm lại qua ETag (Cache-Control: no-cache).
DUOI_GIAO_DIEN_TINH: tuple[str, ...] = (".html", ".js", ".css")

THU_MUC_WEB = Path(__file__).resolve().parent.parent / "web"
MAU_SCRIPT_NOI_TUYEN = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
MAU_LINK_STYLESHEET = re.compile(r"<link\b[^>]*\brel=[\"']?stylesheet[\"']?[^>]*>", re.IGNORECASE)
MAU_HREF = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
MAU_TEN_TEP_HTML = re.compile(r"[A-Za-z0-9_.-]+\.html")

_CACHE_CSP: dict[str, tuple[float, bytes]] = {}
_CSP_MAC_DINH: bytes | None = None


def tao_csp(bam_scripts: list[str], nguon_css: tuple[str, ...] = ()) -> str:
    """Tạo chuỗi Content-Security-Policy cho trang HTML.

    script-src cần 'unsafe-eval' vì bộ dựng giao diện dc-lite (web/support.js) khởi tạo Component bằng new Function.
    Các khối script nội tuyến được cấp phép bằng băm SHA-256 tương ứng; không dùng 'unsafe-inline'.
    style-src nhận thêm đúng các origin https mà CHÍNH trang khai bằng thẻ <link rel="stylesheet"> (đọc từ tệp, không
    bảng tên); font-src cho https: vì phông chữ là tài nguyên thụ động (không chạy mã) và tệp phông nằm ở origin khác
    với tệp CSS. Cả hai đo bằng Chrome thật: thiếu thì phông chữ của giao diện bị chặn.
    connect-src có blob: vì Studio tải video về thành blob rồi fetch lại chính blob đó (đo bằng Chrome thật).
    """
    ds_script = ["'self'", "'unsafe-eval'"]
    for b in bam_scripts:
        ds_script.append(f"'sha256-{b}'")
    phan_script = " ".join(ds_script)
    phan_style = " ".join(["'self'", "'unsafe-inline'", *nguon_css])
    return (
        f"default-src 'self'; "
        f"script-src {phan_script}; "
        f"style-src {phan_style}; "
        f"font-src 'self' data: https:; "
        f"img-src 'self' data: blob:; "
        f"media-src 'self' blob: data:; "
        f"connect-src 'self' blob:; "
        f"object-src 'none'; "
        f"base-uri 'none'; "
        f"form-action 'self'; "
        f"frame-ancestors 'none'"
    )


def lay_csp_mac_dinh() -> bytes:
    global _CSP_MAC_DINH
    if _CSP_MAC_DINH is None:
        _CSP_MAC_DINH = tao_csp([]).encode("utf-8")
    return _CSP_MAC_DINH


def tinh_bam_script_tu_tep(p: Path) -> list[str]:
    """Đọc tệp và tính danh sách băm SHA-256 chuẩn base64 của các khối script nội tuyến."""
    try:
        noi_dung = p.read_text(encoding="utf-8")
    except OSError:
        return []
    matches = MAU_SCRIPT_NOI_TUYEN.findall(noi_dung)
    return [base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode("ascii") for m in matches]


def tim_nguon_stylesheet_tu_tep(p: Path) -> tuple[str, ...]:
    """Các origin https mà trang tự khai trong thẻ <link rel="stylesheet" href="https://…"> (không trùng, giữ thứ tự)."""
    try:
        noi_dung = p.read_text(encoding="utf-8")
    except OSError:
        return ()
    ra: list[str] = []
    for the in MAU_LINK_STYLESHEET.findall(noi_dung):
        m = MAU_HREF.search(the)
        if not m:
            continue
        try:
            tach = urllib.parse.urlsplit(m.group(1))
        except ValueError:
            continue
        if tach.scheme.lower() != "https" or not tach.hostname or tach.username is not None or "@" in tach.netloc:
            continue
        nguon = f"https://{tach.netloc.lower()}"
        if nguon not in ra:
            ra.append(nguon)
    return tuple(ra)


def lay_csp_cho_duong_dan(path: str) -> bytes:
    """Lấy CSP dạng bytes cho đường dẫn yêu cầu, có bộ nhớ đệm theo mtime tệp."""
    ten_tep: str | None = None
    if path == "/":
        ten_tep = "index.html"
    elif path.startswith("/") and path.count("/") == 1 and path.endswith(".html"):
        ung_vien = path[1:]
        if MAU_TEN_TEP_HTML.fullmatch(ung_vien) and ".." not in ung_vien:
            ten_tep = ung_vien

    if not ten_tep:
        return lay_csp_mac_dinh()

    tep = (THU_MUC_WEB / ten_tep).resolve()
    thu_muc_goc = THU_MUC_WEB.resolve()
    if tep.parent != thu_muc_goc or not tep.is_file():
        return lay_csp_mac_dinh()

    try:
        mtime = tep.stat().st_mtime
    except OSError:
        return lay_csp_mac_dinh()

    khoa_cache = str(tep)
    if khoa_cache in _CACHE_CSP:
        mtime_cu, csp_cu = _CACHE_CSP[khoa_cache]
        if mtime_cu == mtime:
            return csp_cu

    bams = tinh_bam_script_tu_tep(tep)
    csp_bytes = tao_csp(bams, tim_nguon_stylesheet_tu_tep(tep)).encode("utf-8")
    _CACHE_CSP[khoa_cache] = (mtime, csp_bytes)
    return csp_bytes


def la_tep_giao_dien_tinh(path: str) -> bool:
    """Xác định đường dẫn có phải tệp giao diện tĩnh của lõi (web/ và trang gốc /) hay không.

    Loại trừ các tuyến riêng biệt (/m/, /api/, /v1/) để giữ nguyên chính sách cache của chúng. Tuyến nào đã tự đặt
    Cache-Control (ví dụ trình duyệt từ xa đặt no-store) thì middleware không đè — xem điều kiện ở `gui`.
    """
    if path.startswith(("/m/", "/api/", "/v1/")):
        return False
    return path == "/" or path.endswith(DUOI_GIAO_DIEN_TINH)


class MiddlewareTieuDeBaoMat:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        la_tinh = la_tep_giao_dien_tinh(path)

        async def gui(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                co = {k.lower() for k, _ in msg.get("headers", [])}
                them = [(k, v) for k, v in TIEU_DE_BAO_MAT if k not in co]
                if la_tinh and b"cache-control" not in co:
                    them.append((b"cache-control", b"no-cache"))
                if b"content-security-policy" not in co:
                    for k, v in msg.get("headers", []):
                        if k.lower() == b"content-type" and b"text/html" in v.lower():
                            them.append((b"content-security-policy", lay_csp_cho_duong_dan(path)))
                            break
                if them:
                    msg = {**msg, "headers": list(msg.get("headers", [])) + them}
            await send(msg)

        await self.app(scope, receive, gui)
