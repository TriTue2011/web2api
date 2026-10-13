"""Plugin xác thực cho websockify: Basic auth với bí mật riêng từng hồ sơ, đọc từ TỆP.

websockify nạp lớp này qua ``--auth-plugin xac_thuc_ws.BasicTuTep --auth-source <đường tệp>``
(cộng ``--web-auth`` để cả trang noVNC tĩnh cũng đòi bí mật). Khác ``BasicHTTPAuth`` có sẵn:

- bí mật nằm trong tệp 0600 chứ không trên dòng lệnh (dòng lệnh lộ qua ``/proc/<pid>/cmdline``,
  ``ps`` và ``docker top`` cho mọi người dùng trên máy chủ);
- so bằng ``hmac.compare_digest`` (``BasicHTTPAuth`` so bằng ``==``).

Tệp chứa đúng một dòng ``<tên>:<bí mật>``. Thiếu tệp, tệp rỗng hay sai dạng thì ném lỗi ngay khi
websockify khởi động → tiến trình chết → server.py thấy và trả 502 (đóng khi hỏng, không mở trần).

Dấu kết nối đã xác thực: websockify xử lý MỖI kết nối trong một tiến trình con riêng (sống suốt phiên
WebSocket). Xác thực thành công thì tiến trình con đó ghi tệp ``<src>.ket-noi/<pid>`` (0600) chứa thời điểm
bắt đầu của nó (trường 22 của /proc/<pid>/stat). server.py chỉ coi hồ sơ «đang có người dùng» khi còn tiến
trình như vậy sống — kết nối treo CHƯA xác thực không giữ hồ sơ sống.

Chạy dưới python hệ thống của gói websockify (Debian), nên chỉ dùng thư viện chuẩn + websockify
và giữ tương thích Python 3.10.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os

from websockify.auth_plugins import AuthenticationError


class BasicTuTep:
    """Kiểm tiêu đề ``Authorization: Basic`` với cặp ``tên:bí mật`` đọc từ tệp ``src``."""

    def __init__(self, src=None):
        if not src:
            raise ValueError("thiếu --auth-source (đường tới tệp bí mật)")
        with open(src, "rb") as f:
            gia_tri = f.read().strip()
        ten, _, bi_mat = gia_tri.partition(b":")
        if not ten or not bi_mat:
            raise ValueError("tệp bí mật rỗng hoặc sai dạng <tên>:<bí mật>")
        self._mong_doi = gia_tri
        self._thu_muc_dau = str(src) + ".ket-noi"

    def authenticate(self, headers, target_host, target_port):
        tieu_de = headers.get("Authorization") or ""
        if not tieu_de.startswith("Basic "):
            self._tu_choi()
        try:
            nhan = base64.b64decode(tieu_de[6:].strip(), validate=True)
        except (binascii.Error, ValueError):
            self._tu_choi()
        if not hmac.compare_digest(nhan, self._mong_doi):
            self._tu_choi()
        self._ghi_dau()

    def _ghi_dau(self):
        # Không ghi được dấu chỉ làm hồ sơ có thể bị dọn idle sớm — không từ chối khách đã xác thực.
        try:
            with open("/proc/self/stat", "rb") as f:
                bat_dau = f.read().rsplit(b")", 1)[1].split()[19]
            fd = os.open(
                os.path.join(self._thu_muc_dau, str(os.getpid())),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.write(fd, bat_dau)
            finally:
                os.close(fd)
        except (OSError, IndexError):
            pass

    @staticmethod
    def _tu_choi():
        # Một mã cho mọi kiểu sai (thiếu, sai dạng, sai bí mật): không lộ lý do.
        raise AuthenticationError(
            response_code=401,
            response_headers={"WWW-Authenticate": 'Basic realm="trinh-duyet"'},
            response_msg="Unauthorized",
        )
