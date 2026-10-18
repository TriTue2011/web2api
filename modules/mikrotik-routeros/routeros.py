"""Máy khách API RouterOS CHỈ ĐỌC cho module mikrotik-routeros (thư viện chuẩn).

Giao thức câu-từ nhị phân của RouterOS (tham khảo routeros_api.py của chủ kho, viết lại
cho web2api): mỗi câu là dãy từ, mỗi từ có độ dài mã hoá biến thiên, câu kết thúc bằng
từ rỗng (byte 0x00). Đăng nhập sau v6.43: lệnh ``/login`` với ``=name=`` và ``=password=``.

CHỈ ĐỌC THEO CẤU TẠO: ``goi_doc`` chỉ nhận lệnh là đường dẫn kết thúc bằng «/print»
(không dấu cách, «;», «=», «?», xuống dòng…); mọi lệnh khác bị từ chối TRƯỚC khi gửi —
không byte lệnh nào tới router. Lọc theo cấu trúc lệnh, không liệt kê tên lệnh.

Bảo mật:
- Mật khẩu không bao giờ vào thông báo lỗi, ngoại lệ hay log (mọi thông điệp lỗi do
  máy khách tự dựng; lời router bị cắt ≤120 ký tự và không chứa bí mật).
- Vân tay SHA-256 của chứng chỉ được kiểm TRƯỚC khi gửi mật khẩu (sai → đóng, không login).
- Hết giờ kết nối + đọc (socket timeout); trần độ dài mỗi câu (1 MiB) và trần số câu
  mỗi lượt đọc (50) chống bộ nhớ phình; câu sai khuôn/ngắt giữa chừng → lỗi, không sập.
"""

from __future__ import annotations

import hashlib
import re
import socket
import ssl

__all__ = [
    "RouterOSError",
    "LoiKetNoi",
    "LoiChungChi",
    "LoiDangNhap",
    "ket_noi",
    "Phien",
    "van_tay_cua",
    "CONG_MAC_DINH",
]

CONG_MAC_DINH = 8729  # api-ssl (TLS)
TRAN_DO_DAI_CAU = 1024 * 1024  # 1 MiB — câu dài hơn là rác/tấn công, bỏ ngay
TRAN_SO_CAU = 50  # số câu !re tối đa mỗi lượt đọc — chống bộ nhớ phình
_TRAN_LOI_ROUTER = 120  # lời router trong ngoại lệ bị cắt tới đây


class RouterOSError(Exception):
    """Lỗi chung của máy khách RouterOS (thông điệp không chứa bí mật)."""


class LoiKetNoi(RouterOSError):
    """Không tới được cổng API (mạng, cổng đóng, hết giờ kết nối)."""


class LoiChungChi(RouterOSError):
    """Chứng chỉ của router không khớp vân tay đã ghim hoặc không kiểm được."""


class LoiDangNhap(RouterOSError):
    """Router từ chối đăng nhập (sai tài khoản/mật khẩu)."""


def _ma_do_dai(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return (n | 0x8000).to_bytes(2, "big")
    if n < 0x200000:
        return (n | 0xC00000).to_bytes(3, "big")
    if n < 0x10000000:
        return (n | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + n.to_bytes(4, "big")


def van_tay_cua(der: bytes) -> str:
    """Vân tay SHA-256 (hex chữ thường) của chứng chỉ DER."""
    return hashlib.sha256(der or b"").hexdigest()


def _van_tay_khop(da_ghim: str, hien_tai: str) -> bool:
    g = (da_ghim or "").strip().lower()
    h = (hien_tai or "").strip().lower()
    return bool(g) and bool(h) and g == h


def _lenh_doc_hop_le(lenh: object) -> bool:
    """Lệnh đọc hợp lệ theo cấu trúc: đường dẫn kết thúc «/print», không ký tự đặc biệt.

    Nguyên tắc: chỉ dạng «/a/b/c/print». Mọi biến thể khác (dấu cách đầu/cuối, «;»,
    «=», «?», xuống dòng, không bắt đầu bằng «/», không kết thúc bằng «/print»)
    đều bị từ chối — không liệt kê tên lệnh cụ thể.
    """
    if not isinstance(lenh, str) or not lenh:
        return False
    if not lenh.startswith("/") or not lenh.endswith("/print"):
        return False
    return not any(c in lenh for c in " ;=\t\n\r\x00?")


_TRUONG_PROPLIST = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _proplist_hop_le(proplist: str) -> bool:
    """Danh sách trường cách nhau bằng dấu phẩy; mỗi tên chỉ chữ/số/«_ . -».

    Ngăn chèn «;», «=», truy vấn vào tham số ``=.proplist=``.
    """
    if not proplist:
        return True
    return all(_TRUONG_PROPLIST.match(p) for p in proplist.split(","))


class Phien:
    """Phiên đã mở tới RouterOS (đồng bộ; module gọi qua ``asyncio.to_thread``)."""

    def __init__(self, sock, het_gio: float):
        self._s = sock
        self._het_gio = het_gio
        try:
            self._s.settimeout(het_gio)
        except OSError:
            pass

    def _doc(self, n: int) -> bytes:
        b = b""
        while len(b) < n:
            try:
                c = self._s.recv(n - len(b))
            except (OSError, ssl.SSLError) as e:
                raise RouterOSError("hết giờ hoặc lỗi khi đọc phản hồi của router") from e
            if not c:
                raise RouterOSError("router đóng kết nối giữa chừng khi đang đọc")
            b += c
        return b

    def _doc_do_dai(self) -> int:
        c = self._doc(1)[0]
        if c < 0x80:
            return c
        if c < 0xC0:
            return ((c & 0x3F) << 8) | self._doc(1)[0]
        if c < 0xE0:
            return ((c & 0x1F) << 16) | int.from_bytes(self._doc(2), "big")
        if c < 0xF0:
            return ((c & 0x0F) << 24) | int.from_bytes(self._doc(3), "big")
        n = int.from_bytes(self._doc(4), "big")
        if n > TRAN_DO_DAI_CAU:
            raise RouterOSError("câu trả lời của router vượt trần độ dài cho phép")
        return n

    def _doc_cau(self) -> list[str]:
        tu: list[str] = []
        while True:
            n = self._doc_do_dai()
            if n > TRAN_DO_DAI_CAU:
                raise RouterOSError("từ trong câu trả lời của router vượt trần độ dài cho phép")
            if n == 0:
                return tu
            tu.append(self._doc(n).decode("utf-8", errors="replace"))

    def _gui_va_nhan(self, tu: list[str]) -> list[dict[str, str]]:
        for w in tu:
            b = w.encode("utf-8")
            if len(b) > TRAN_DO_DAI_CAU:
                raise RouterOSError("từ gửi tới router vượt trần độ dài cho phép")
            self._s.sendall(_ma_do_dai(len(b)) + b)
        self._s.sendall(b"\x00")
        ra: list[dict[str, str]] = []
        while True:
            cau = self._doc_cau()
            if not cau:
                continue
            d: dict[str, str] = {}
            for w in cau[1:]:
                k, _, v = w[1:].partition("=")
                d[k] = v
            if cau[0] == "!re":
                ra.append(d)
                if len(ra) > TRAN_SO_CAU:
                    raise RouterOSError("router trả quá nhiều câu trong một lượt đọc")
            elif cau[0] == "!done":
                return ra
            elif cau[0] in ("!trap", "!fatal"):
                loi_router = str(d.get("message") or "")[:_TRAN_LOI_ROUTER]
                if cau[0] == "!trap":
                    # Đọc nốt tới !done để đồng bộ luồng, nhưng không dùng nội dung.
                    try:
                        while (c := self._doc_cau()) and c[0] != "!done":
                            pass
                    except RouterOSError:
                        pass
                raise RouterOSError(f"router từ chối lệnh: {loi_router}")

    def dang_nhap(self, ten: str, mat_khau: str) -> None:
        """Đăng nhập bằng /login. Sai → LoiDangNhap (không kèm mật khẩu, không nguyên văn lời router)."""
        try:
            self._gui_va_nhan(["/login", f"=name={ten}", f"=password={mat_khau}"])
        except RouterOSError as e:
            if isinstance(e, LoiDangNhap):
                raise
            raise LoiDangNhap("router từ chối đăng nhập (kiểm lại tài khoản và mật khẩu)") from e

    def goi_doc(self, lenh: str, proplist: str = "") -> list[dict[str, str]]:
        """CHỈ lệnh đọc «…/print». Lệnh không đúng khuôn bị từ chối TRƯỚC khi gửi."""
        if not _lenh_doc_hop_le(lenh):
            raise RouterOSError("chỉ cho phép lệnh đọc có dạng «/…/print» (không tham số lạ)")
        if not _proplist_hop_le(proplist):
            raise RouterOSError("danh sách trường (.proplist) chứa ký tự không hợp lệ")
        tu = [lenh] + ([f"=.proplist={proplist}"] if proplist else [])
        return self._gui_va_nhan(tu)

    def dong(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass


def ket_noi(host: str, port: int, ten: str, mat_khau: str, *,
            tls: bool = True, kiem_chung_chi: bool = True, van_tay: str = "",
            het_gio: float = 8.0) -> Phien:
    """Mở phiên tới RouterOS.

    - ``tls=True``: bọc TLS (cổng api-ssl 8729). Nếu có ``van_tay``: lấy chứng chỉ,
      kiểm vân tay SHA-256 TRƯỚC khi gửi mật khẩu (sai → LoiChungChi, chưa hề login).
      Nếu không có vân tay: kiểm chứng chỉ theo hệ thống khi ``kiem_chung_chi``.
    - ``tls=False``: nối trần — chỉ dùng khi quản trị bật cờ «cho phép không mã hoá».
    - Thông điệp lỗi không bao giờ chứa mật khẩu hay tài khoản.

    Lưu ý: ``ket_noi`` chỉ mở kênh và kiểm vân tay, KHÔNG đăng nhập — gọi
    ``Phien.dang_nhap`` riêng sau đó.
    """
    try:
        s = socket.create_connection((host, port), timeout=het_gio)
    except OSError as e:
        raise LoiKetNoi(f"không tới được {host}:{port} ({type(e).__name__})") from e
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        if van_tay:
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.verify_mode = ssl.CERT_REQUIRED if kiem_chung_chi else ssl.CERT_NONE
        try:
            t = ctx.wrap_socket(s, server_hostname=host)
        except (OSError, ssl.SSLError) as e:
            try:
                s.close()
            except OSError:
                pass
            raise LoiKetNoi(f"không bắt tay TLS được với {host}:{port} ({type(e).__name__})") from e
        if van_tay:
            hien_tai = van_tay_cua(t.getpeercert(binary_form=True) or b"")
            if not _van_tay_khop(van_tay, hien_tai):
                try:
                    t.close()
                except OSError:
                    pass
                raise LoiChungChi("chứng chỉ của router khác vân tay đã ghim "
                                  "(đổi chứng chỉ? cấu hình lại để ghim vân tay mới)")
        s = t
    p = Phien(s, het_gio)
    return p
