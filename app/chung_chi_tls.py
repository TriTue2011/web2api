"""Đo vân tay TLS + vé «Tin chứng chỉ này» (NV295).

Mô hình «tin ở lần đầu» cho module nối thiết bị LAN dùng chứng chỉ tự ký:
- Lõi TỰ ĐO vân tay (bắt tay TLS với CERT_NONE, chỉ lấy chứng chỉ rồi đóng —
  không gửi mật khẩu, không đăng nhập), rồi giao diện chỉ hiện MẶT NẠ
  (8 ký tự đầu…cuối) cùng nút «Tin chứng chỉ này».
- Bấm tin: lõi đo LẠI và so khớp với bản đã hiển thị (so mặt nạ), khác thì
  từ chối; khớp thì lưu vân tay đầy đủ vào cấu hình rồi kiểm lại ngay
  (qua đường cài cấu hình chung của lõi — module tự kiểm lại khi cài).

Vé tin KHÔNG chứa vân tay (chỉ module + host + cổng + hạn + HMAC bằng khoá
ngẫu nhiên của tiến trình — khởi động lại là vé cũ hết hiệu lực, người dùng
lưu lại là có vé mới). Vé không dùng một lần có chủ đích: vé cũ dùng lại
vẫn phải qua bước đo lại + so mặt nạ, chứng chỉ đổi là bị từ chối.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import time

from app.tu_xa import kiem_ip_lan

__all__ = [
    "ma_hien_thi",
    "do_van_tay",
    "tao_ve",
    "doc_ve",
    "ghep_de_nghi_tin",
    "tin_chung_chi",
]

_KHOA_VE = os.urandom(32)  # khoá tiến trình: vé chết khi khởi động lại
_HAN_VE_MAC_DINH_GIAY = 600
_VAN_TAY_HEX = set("0123456789abcdef")


def ma_hien_thi(van_tay: str) -> str:
    """Mặt nạ hiển thị: 8 ký tự đầu…cuối. Phần giữa không bao giờ ra giao diện."""
    v = (van_tay or "").strip().lower()
    if len(v) != 64 or any(c not in _VAN_TAY_HEX for c in v):
        raise ValueError("van_tay phải là 64 ký tự hex (SHA-256)")
    return f"{v[:8]}…{v[-8:]}"


def do_van_tay(host: str, port: int, het_gio: float = 8.0) -> str:
    """Đo vân tay SHA-256 của chứng chỉ đích — CHỈ bắt tay rồi đóng.

    Không gửi gì sau bắt tay, không đăng nhập. Dùng khi đề nghị tin (lần đầu
    hoặc chứng chỉ đổi); đích PHẢI là địa chỉ đã qua kiểm LAN của lõi
    (module kiểm trước khi gọi — hàm này không tự kiểm lại).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=het_gio) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True) or b""
    return hashlib.sha256(der).hexdigest()


def _ky(payload: bytes) -> str:
    return hmac.new(_KHOA_VE, payload, hashlib.sha256).hexdigest()


def tao_ve(module_id: str, host: str, port: int, ttl_giay: int = _HAN_VE_MAC_DINH_GIAY) -> str:
    """Vé tin: base64url(payload).hmac — payload chỉ có module/host/cổng/hạn."""
    payload = json.dumps(
        {"m": module_id, "h": host, "p": int(port),
         "e": int(time.time()) + int(ttl_giay)},
        separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b64}.{_ky(payload)}"


def doc_ve(ve: str, module_id: str) -> tuple[str, int] | None:
    """Kiểm vé: đúng module, còn hạn, HMAC khớp → (host, cổng). Sai → None."""
    try:
        b64, mac = str(ve).split(".", 1)
        payload = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        data = json.loads(payload.decode())
        if data.get("m") != module_id:
            return None
        if int(data.get("e", 0)) < int(time.time()):
            return None
        if not hmac.compare_digest(mac, _ky(payload)):
            return None
        host, port = str(data["h"]), int(data["p"])
        if not host or not 1 <= port <= 65535:
            return None
        return host, port
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None


def ghep_de_nghi_tin(kq: dict, module_id: str) -> dict | None:
    """Chuyển đề nghị tin của module thành khối công khai cho giao diện.

    Module trả ``tin_chung_chi`` = {ly_do, ma_hien_thi, thong_bao, host, cong};
    lõi chỉ chuyển tiếp {ly_do, ma_hien_thi, thong_bao} + vé đã ký — host/cổng
    KHÔNG ra giao diện (vé mang chúng, có HMAC).

    Từ hướng 19:30, lý do duy nhất còn được đề nghị là ``doi`` (chứng chỉ
    đổi); lần đầu tự ký đã tự ghim, không còn đề nghị ``tu_ky``.
    """
    if not isinstance(kq, dict):
        return None
    de_nghi = kq.get("tin_chung_chi")
    if not isinstance(de_nghi, dict):
        return None
    ly_do = de_nghi.get("ly_do")
    ma = de_nghi.get("ma_hien_thi")
    thong_bao = de_nghi.get("thong_bao")
    host = de_nghi.get("host")
    cong = de_nghi.get("cong")
    if ly_do not in ("doi",):
        return None
    if not isinstance(ma, str) or "…" not in ma:
        return None
    if not isinstance(thong_bao, str) or not thong_bao.strip():
        return None
    if not isinstance(host, str) or not host:
        return None
    try:
        cong = int(cong)
    except (TypeError, ValueError):
        return None
    if not 1 <= cong <= 65535:
        return None
    try:
        host = str(kiem_ip_lan(host))
    except ValueError:
        return None
    return {"ly_do": ly_do, "ma_hien_thi": ma, "thong_bao": thong_bao,
            "ve": tao_ve(module_id, host, cong)}


async def tin_chung_chi(so, module_id: str, cau_hinh: dict, ve: str,
                        ma_hien_thi_da_hien: str) -> dict:
    """Hành động «Tin chứng chỉ này»: kiểm vé → đo lại → so mặt nạ → lưu + kiểm.

    - Vé sai/hết hạn/sai module → từ chối, không chạm cấu hình.
    - Đo lại thất bại hoặc mặt nạ khác bản đã hiển thị → từ chối
      (chứng chỉ đã đổi sau khi hiển thị — không tin mù).
    - Khớp → gọi đường cài chung của lõi với vân tay đầy đủ vừa đo;
      module tự kiểm lại kết nối khi cài (nối + đăng nhập thật).
    """
    if not isinstance(cau_hinh, dict):
        return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Thân cần dạng {\"cau_hinh\": {...}}."}
    da_kiem = doc_ve(ve, module_id)
    if da_kiem is None:
        return {"ok": False, "ket_qua_tung_khoa": [],
                "loi": "Vé tin đã hết hạn hoặc không hợp lệ — lưu lại để nhận đề nghị mới."}
    host, cong = da_kiem
    try:
        host = str(kiem_ip_lan(host))
    except ValueError:
        return {"ok": False, "ket_qua_tung_khoa": [],
                "loi": "Địa chỉ trong vé tin không phải IP LAN hợp lệ — không kết nối."}
    try:
        van_tay = await asyncio.to_thread(do_van_tay, host, cong)
    except (OSError, ssl.SSLError) as e:
        return {"ok": False, "ket_qua_tung_khoa": [],
                "loi": f"Không đo lại được chứng chỉ ({type(e).__name__}) — thử lưu lại."}
    try:
        ma_moi = ma_hien_thi(van_tay)
    except ValueError:
        return {"ok": False, "ket_qua_tung_khoa": [],
                "loi": "Chứng chỉ đo được không hợp lệ — thử lưu lại."}
    if not hmac.compare_digest(ma_moi.encode("utf-8"),
                               str(ma_hien_thi_da_hien or "").encode("utf-8")):
        return {"ok": False, "ket_qua_tung_khoa": [],
                "loi": "Chứng chỉ đã đổi so với bản vừa hiển thị — không tin. Lưu lại để xem mã mới."}
    cau_hinh_tin = dict(cau_hinh)
    cau_hinh_tin["van_tay_tls"] = van_tay
    return await so.cai(module_id, cau_hinh_tin)
