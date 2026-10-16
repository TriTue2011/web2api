"""Middleware TIÊU ĐỀ BẢO MẬT: gắn header bảo vệ vào MỌI phản hồi HTTP và Cache-Control cho tệp giao diện tĩnh.

Khác với middleware bảo vệ riêng của tuyến trình duyệt từ xa (THAY header) và
HEADER_TRANG_MODULE (đặt ở /m/), middleware này chỉ ĐẶT khi phản hồi CHƯA có header
đó — không đè giá trị riêng của route. KHÔNG thêm Content-Security-Policy toàn cục
(trang giao diện dùng script nội tuyến) và KHÔNG thêm HSTS. Đứng NGOÀI CÙNG trong
app/main.py để thấy cả phản hồi do middleware trong hay trình xử lý ngoại lệ tạo ra
(401/404/403, trang tĩnh, StreamingResponse).

Tệp giao diện tĩnh (.html, .js, .css và trang gốc /) nhận Cache-Control: no-cache khi chưa có
để trình duyệt luôn hỏi lại server bằng ETag trước khi dùng, tránh lỗi lệch phiên bản sau nâng cấp.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: (tên thường, giá trị) — so tên theo chữ thường vì ASGI dùng bytes.
TIEU_DE_BAO_MAT: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)

#: Đuôi tệp giao diện tĩnh luôn cần kiểm lại qua ETag (Cache-Control: no-cache).
DUOI_GIAO_DIEN_TINH: tuple[str, ...] = (".html", ".js", ".css")


def la_tep_giao_dien_tinh(path: str) -> bool:
    """Xác định đường dẫn có phải tệp giao diện tĩnh của lõi (web/ và trang gốc /) hay không.

    Loại trừ các tuyến riêng biệt (/novnc, /m/, /api/, /v1/) để giữ nguyên chính sách cache của chúng.
    """
    if path.startswith(("/novnc", "/m/", "/api/", "/v1/")):
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
                if them:
                    msg = {**msg, "headers": list(msg.get("headers", [])) + them}
            await send(msg)

        await self.app(scope, receive, gui)
