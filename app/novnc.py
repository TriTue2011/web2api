"""Lõi quản lý vé, phiên ngắn hạn và proxy noVNC (Chuẩn C6).

Bối cảnh: máy chủ không có màn hình; người dùng đăng nhập tay (OTP/captcha) vào dịch vụ rồi giữ phiên trong một
hồ sơ trình duyệt bền vững. noVNC mở bằng tab mới nên không gắn được tiêu đề Authorization, và tải hàng chục tệp
tĩnh cùng một WebSocket. Không bao giờ đưa khoá quản trị lên URL.

Mô hình đe doạ: nội dung noVNC đến từ DỊCH VỤ trình duyệt (bên không hoàn toàn tin). Nếu nó bị chiếm, script của nó
không được chạm tới origin của web2api — nơi giao diện quản trị giữ khoá ở localStorage.

Cơ chế:
1. Quản trị gọi `POST /api/trinh-duyet/{module}/mo` → lõi mở hồ sơ, GHIM đích và cấp vé (60 giây, một lần, RAM).
2. `GET /novnc/dang-nhap?ve=…` chỉ trả một trang nhỏ có NÚT (không script); bấm nút gửi `POST /novnc/dang-nhap`
   (vé trong thân form). Bot xem trước liên kết của ứng dụng nhắn tin chỉ GET nên không giành được vé.

   (vé trong thân form). Bot xem trước liên kết của các ứng dụng nhắn tin chỉ GET nên không giành được vé.
   POST đổi vé NGUYÊN TỬ (kiểm vé + trần + tạo phiên trong một khoá) → 303 sang `/novnc/s/<phien>/vnc.html`.
3. KHÔNG cookie. Phiên (256 bit) nằm TRONG ĐƯỜNG DẪN `/novnc/s/<phien>/…`; sổ phiên tra theo sha256 của phiên;
   (module, hồ sơ) suy ra từ phiên — khách không tự chọn được hồ sơ. Trần: 4 phiên mỗi (module, hồ sơ),
   `WEB2API_NOVNC_PHIEN_TOI_DA` phiên toàn cục; vượt trần thì từ chối phiên MỚI (429), không đẩy phiên đang dùng.
4. MỌI phản hồi dưới `/novnc/s/` (kể cả lỗi) mang CSP `sandbox allow-scripts allow-forms allow-pointer-lock` (KHÔNG
   allow-same-origin → tài liệu có origin mờ «null»: không đọc được localStorage/cookie của web2api, gọi /api chỉ
   như một origin lạ không có khoá), `Access-Control-Allow-Origin: *` (module-script/fetch từ origin mờ tải được tài
   nguyên của chính phiên; không kèm thông tin xác thực nào), nosniff, no-referrer, no-store, `X-Frame-Options: DENY`.
   Đã kiểm noVNC 1.6.0 thật (bản Debian của dịch vụ) chạy được trong sandbox này.
5. Proxy chỉ GET/HEAD; header hai chiều theo DANH SÁCH CHO PHÉP (đây là kiểm soát an ninh, không phải đoán);
   đường dẫn kiểm theo đoạn rồi mã hoá lại từng đoạn; query đi bằng `params`.
6. Đích (`dich`) do dịch vụ trả có thể mang userinfo `http://w2a:<bi_mat>@host:port`: tách ra thành Basic auth,
   chỉ giữ trong RAM, không vào log/nhật ký/phản hồi. Tên máy phân giải MỘT lần lúc `mo`, mọi địa chỉ phải qua
   kiểm SSRF; mọi kết nối sau đó đi thẳng tới IP đã kiểm (không proxy môi trường) với `Host` gốc.
7. WebSocket `/novnc/s/<phien>/websockify`: đăng ký vào sổ NGAY khi nhận (trước khi nối backend), kiểm lại phiên sau
   khi nối; thu hồi/đóng hồ sơ/hết hạn phiên đều đặt cờ huỷ và chính tác vụ phục vụ socket đóng nó.
8. Đóng hồ sơ: THU HỒI TRƯỚC (vé, phiên, socket, đích ghim) rồi mới gọi module.
9. Nhật ký kênh `trinh_duyet` không bao giờ ghi vé, phiên, bí mật; log uvicorn được lọc che đoạn phiên và vé.
10. Cookie sang module (`POST /api/trinh-duyet/{module}/{ho_so}/cookie-sang-module`, chỉ quản trị): đăng nhập xong
   trong trình duyệt trên server thì lõi lấy cookie của một tên miền qua phương thức TUỲ CHỌN `trinh_duyet_cookie` của
   module nguồn và đưa vào trường BÍ MẬT (nhận bằng `kieu` trong manifest, không bằng tên) của module đích QUA ĐÚNG
   đường cài có sẵn (`SoModule.cai`: module đích tự kiểm bằng `cai()` rồi lõi mới lưu, bí mật vào kho 0600). Cookie chỉ
   đi dịch vụ → lõi → module đích trong RAM của lời gọi: không vào phản hồi, log, nhật ký; không lưu biến toàn cục.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx
import websockets
from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import dia_chi, gioi_han, kho, nhat_ky, phien_web
from app.kham_pha import ID_HOP_LE, MAU_MIEN
from app.loi_openai import LoiOpenAI, than_loi
from app.so_module import LoiSo, SoModule
from app.than_co_tran import THAN_JSON, doc_het
from app.tu_xa import _dia_chi_bi_chan
from app.xac_thuc import can_quan_tri

log = logging.getLogger("web2api.novnc")
#: Logger riêng cho kết nối WebSocket tới đích: websockets ghi NGUYÊN header bắt tay (gồm Authorization Basic mang
#: bí mật của đích) ở mức DEBUG — chốt mức INFO để dòng đó không bao giờ ra, dù log gốc đặt DEBUG.
_LOG_WS_DICH = logging.getLogger("web2api.novnc.ws_dich")
_LOG_WS_DICH.setLevel(logging.INFO)
QT = [Depends(can_quan_tri)]

TOI_DA_VE = 10000
THOI_HAN_VE_GIAY = 60
THOI_HAN_PHIEN_GIAY = 8 * 3600  # 8 giờ
MAC_DINH_PHIEN_TOI_DA = 16
TRAN_PHIEN_MOI_HO_SO = 4
#: Chu kỳ tác vụ phục vụ WebSocket tự kiểm phiên còn hiệu lực (hạn chót tuyệt đối cho socket đang chạy).
CHU_KY_KIEM_WS_GIAY = 30.0
#: Khung noVNC (cập nhật màn hình) vượt 1 MiB mặc định của websockets → đóng 1009.
WS_KHUNG_TOI_DA = 16 * 1024 * 1024
#: Mã đóng WebSocket khi quyền truy cập bị thu hồi (đóng hồ sơ, thu hồi, phiên hết hạn, module bị gỡ/thay).
MA_DONG_THU_HOI = 1008
#: Thân POST đổi vé: chỉ `ve=<43 ký tự>`; trần nhỏ để đường không xác thực không tốn RAM/đĩa.
THAN_DOI_VE_TOI_DA = 4096

#: Dùng với fullmatch (không `$`: `$` nhận thêm một «\n» cuối).
MAU_HO_SO = re.compile(r"[a-f0-9]{16,64}")
#: Tên miền hỏi cookie — cùng khuôn với dịch vụ trình duyệt: nhãn [a-z0-9-] không «-» đầu/cuối, ÍT NHẤT hai nhãn,
#: ≤ 253 ký tự. Một nhãn đơn là cả một đuôi tên miền (cookie của mọi trang dưới nó) → từ chối.
#: Chuỗi cookie module nguồn trả: ASCII in được (không CR/LF/ký tự điều khiển — đi thẳng vào tiêu đề HTTP của module
#: đích), có trần độ dài.
MAU_CHUOI_COOKIE = re.compile(r"[\x20-\x7e]+")
TRAN_COOKIE = 64 * 1024
#: Workspace (hồ sơ có tên hiển thị): tên 1..100 ký tự in được; trang bắt đầu http(s) ≤ 2048 ký tự.
TRAN_TEN_WORKSPACE = 100
TRAN_URL_BAT_DAU = 2048
#: Dạng che khoá chuẩn của lõi/module (`kho.che`): «…» + tối đa 4 ký tự in được.
MAU_KHOA_CHE = re.compile(r"…[\x21-\x7e]{0,4}")
#: Câu của LÕI thay cho mọi chuỗi tự do module đích trả (`loi` tổng và từng khoá): chuỗi đó có thể chứa cookie dưới
#: dạng bất kỳ (mã hoá, cắt, cookie cũ đã lưu) — không lọc được theo nội dung, nên không bao giờ chuyển tiếp.
LOI_KHOA_DICH = "Module đích từ chối khoá này (chi tiết xem nhật ký của module)"
LOI_TONG_DICH = "Module đích từ chối cookie (chi tiết xem nhật ký của module)"
#: Lỗi có cấu trúc từ `trinh_duyet_cookie` (thuộc tính `ma`) mà lõi chuyển tiếp — kèm câu của LÕI, không chép câu module.
LOI_COOKIE_NGUON = {
    400: "Dịch vụ trình duyệt từ chối yêu cầu (mã hồ sơ hoặc tên miền không hợp lệ)",
    404: ("Hồ sơ không có hoặc chưa có kho cookie — mở trình duyệt, đăng nhập, đóng (giữ phiên) rồi thử lại. "
          "Nếu cookie đăng nhập là cookie phiên (không hạn) thì trình duyệt có thể không ghi nó ra đĩa — khi đó dán "
          "cookie tay vào cấu hình module"),
    409: "Hồ sơ đang mở — bấm «Đóng · giữ phiên» rồi lấy cookie",
    501: "Dịch vụ trình duyệt không hỗ trợ lấy cookie cho loại hồ sơ này",
}
MAU_VE = re.compile(r"[A-Za-z0-9_-]{20,128}")

#: Tiêu đề YÊU CẦU được chuyển tới đích (cho phép); còn lại (Origin, Referer, Cookie, Authorization của khách,
#: X-Forwarded-*, …) bị bỏ.
YC_CHO_PHEP = ("accept", "accept-language", "if-none-match", "if-modified-since", "range", "user-agent")
#: Tiêu đề PHẢN HỒI của đích được chuyển về khách (cho phép); Set-Cookie, Location, CSP của đích, Server… bị bỏ.
PH_CHO_PHEP = ("content-type", "etag", "last-modified", "accept-ranges", "content-range")

HEADER_BAO_VE_PROXY: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", b"sandbox allow-scripts allow-forms allow-pointer-lock"),
    (b"access-control-allow-origin", b"*"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
)
HEADER_BAO_VE_TRANG_VE: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy",
     b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
)

_KHOA = threading.RLock()
# sha256(ve) -> {"module", "ho_so", "het_han"}
_VE: dict[str, dict[str, Any]] = {}
# sha256(phien) -> {"module", "ho_so", "tao_luc", "het_han"}
_PHIEN: dict[str, dict[str, Any]] = {}
# (module, ho_so) -> đích đã GHIM: {"loai", "scheme", "host", "port", "duong", "ip", "host_hdr", "auth", "luc_mo"}
_HO_SO: dict[tuple[str, str], dict[str, Any]] = {}
# sha256(phien) -> cờ huỷ của từng WebSocket đang phục vụ (tác vụ phục vụ tự đóng socket khi cờ bật)
_WS: dict[str, set[asyncio.Event]] = {}


def _bay_gio() -> float:
    return time.time()


def _bam(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def tran_phien() -> int:
    val = os.environ.get("WEB2API_NOVNC_PHIEN_TOI_DA", "").strip()
    try:
        n = int(val)
    except ValueError:
        return MAC_DINH_PHIEN_TOI_DA
    return n if n > 0 else MAC_DINH_PHIEN_TOI_DA


def sinh_ho_so_id() -> str:
    return secrets.token_hex(12)


def kiem_tra_ho_so_id(ho_so: str) -> None:
    if not isinstance(ho_so, str) or not MAU_HO_SO.fullmatch(ho_so):
        raise LoiOpenAI(400, "Mã hồ sơ không hợp lệ (cần 16-64 ký tự hex thường)")


def _so(request: Request) -> SoModule:
    return request.app.state.so


def kiem_url_bat_dau(url: Any) -> str | None:
    """Trang đầu khi trình duyệt khởi động: http(s), có tên máy, không userinfo, ASCII in được không khoảng trắng
    (đi vào đối số tiến trình ở dịch vụ). Rỗng/không gửi → None. Sai → 400 không phản chiếu."""
    if url is None or url == "":
        return None
    sai = LoiOpenAI(400, f"Trường «url» cần là http(s), có tên máy, không chứa mật khẩu, tối đa {TRAN_URL_BAT_DAU} ký tự")
    if not isinstance(url, str) or len(url) > TRAN_URL_BAT_DAU or not re.fullmatch(r"[\x21-\x7e]+", url):
        raise sai
    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
            raise ValueError()
    except ValueError:
        raise sai from None
    return url


# ───────────── Sổ vé / phiên / socket (mọi hàm *_khoa gọi khi đang giữ _KHOA) ─────────────

def _thu_hoi_phien_khoa(bams: list[str]) -> None:
    for b in bams:
        _PHIEN.pop(b, None)
        for ev in _WS.pop(b, ()):
            ev.set()


def _don_het_han_khoa(now: float) -> None:
    for k in [k for k, v in _VE.items() if now > v["het_han"]]:
        _VE.pop(k, None)
    _thu_hoi_phien_khoa([k for k, v in _PHIEN.items() if now > v["het_han"]])


def _phien_khoa(bam_phien: str) -> dict[str, Any] | None:
    """Phiên còn hiệu lực (hết hạn thì thu hồi luôn, kể cả socket của nó)."""
    p = _PHIEN.get(bam_phien)
    if p is not None and _bay_gio() > p["het_han"]:
        _thu_hoi_phien_khoa([bam_phien])
        return None
    return p


def _phien_va_dich_khoa(bam_phien: str, so: SoModule) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Phiên còn hiệu lực VÀ module đã mở hồ sơ vẫn là đối tượng đang chạy. Module bị gỡ/thay/nạp lại → thu hồi cả
    hồ sơ (công tắc tắt module cắt luôn đường noVNC), không cần đường gỡ module biết tới noVNC."""
    p = _phien_khoa(bam_phien)
    if p is None:
        return None
    hs = _HO_SO.get((p["module"], p["ho_so"]))
    if hs is None:
        return None
    if so.dang_chay.get(p["module"]) is not hs["mod"]:
        thu_hoi_ho_so(p["module"], p["ho_so"])
        return None
    return p, hs


def lay_nguoi_dung_id(request: Request) -> str | None:
    """Lấy ID người dùng từ request.state.danh_tinh hoặc request.state.nguoi_dung.

    Khoá quản trị cũ hoặc khoá khách không gắn với người dùng thì trả None.
    """
    danh_tinh = getattr(request.state, "danh_tinh", None)
    if isinstance(danh_tinh, dict):
        dt_id = str(danh_tinh.get("id") or "")
        if dt_id.startswith("nguoi_dung:"):
            return dt_id.split(":", 1)[1]
    nguoi_dung = getattr(request.state, "nguoi_dung", None)
    if isinstance(nguoi_dung, dict) and nguoi_dung.get("id"):
        return str(nguoi_dung["id"])
    return None


def cap_ve(module: str, ho_so: str, nguoi_dung_id: str | None = None) -> str:
    ve = secrets.token_urlsafe(32)
    with _KHOA:
        now = _bay_gio()
        _don_het_han_khoa(now)
        while len(_VE) >= TOI_DA_VE:
            het_han_keys = [k for k, v in _VE.items() if now > v["het_han"]]
            if het_han_keys:
                for k in het_han_keys:
                    _VE.pop(k, None)
                    if len(_VE) < TOI_DA_VE:
                        break
            else:
                _VE.pop(next(iter(_VE)), None)
        _VE[_bam(ve)] = {
            "module": module,
            "ho_so": ho_so,
            "het_han": now + THOI_HAN_VE_GIAY,
            "nguoi_dung_id": nguoi_dung_id,
        }
    return ve


def doi_ve_lay_phien(ve: str) -> tuple[int, str | None, dict[str, Any] | None]:
    """Nguyên tử: kiểm vé → kiểm trần → tiêu thụ vé + tạo phiên. Trả (mã, phiên, thông tin vé).

    401: vé sai/hết hạn/hồ sơ đã đóng. 429: chạm trần — vé KHÔNG bị tiêu thụ (dùng lại được trong hạn).
    """
    if not isinstance(ve, str) or not MAU_VE.fullmatch(ve):
        return 401, None, None
    bam_ve = _bam(ve)
    with _KHOA:
        now = _bay_gio()
        _don_het_han_khoa(now)
        tt = _VE.get(bam_ve)
        if tt is None:
            return 401, None, None
        khoa_hs = (tt["module"], tt["ho_so"])
        if khoa_hs not in _HO_SO:
            _VE.pop(bam_ve, None)
            return 401, None, None
        cung_ho_so = sum(1 for p in _PHIEN.values() if (p["module"], p["ho_so"]) == khoa_hs)
        if cung_ho_so >= TRAN_PHIEN_MOI_HO_SO or len(_PHIEN) >= tran_phien():
            return 429, None, tt
        _VE.pop(bam_ve, None)
        phien = secrets.token_urlsafe(32)  # 256 bit
        _PHIEN[_bam(phien)] = {
            "module": tt["module"],
            "ho_so": tt["ho_so"],
            "tao_luc": now,
            "het_han": now + THOI_HAN_PHIEN_GIAY,
            "nguoi_dung_id": tt.get("nguoi_dung_id"),
        }
        return 200, phien, tt


def thu_hoi_phien_theo_nguoi_dung(nguoi_dung_id: str) -> int:
    """Thu hồi toàn bộ vé và phiên noVNC của một người dùng, và mọi vé/phiên không gắn tài khoản cụ thể.

    Đóng WebSocket đang mở.
    """
    if not isinstance(nguoi_dung_id, str) or not nguoi_dung_id.strip():
        return 0
    with _KHOA:
        for k in [k for k, v in _VE.items() if v.get("nguoi_dung_id") == nguoi_dung_id or not v.get("nguoi_dung_id")]:
            _VE.pop(k, None)
        cac_phien = [k for k, v in _PHIEN.items() if v.get("nguoi_dung_id") == nguoi_dung_id or not v.get("nguoi_dung_id")]
        _thu_hoi_phien_khoa(cac_phien)
        return len(cac_phien)


def thu_hoi_ho_so(module: str, ho_so: str) -> None:
    """Thu hồi mọi quyền truy cập của hồ sơ: vé, phiên, socket (cờ huỷ) và đích ghim."""
    with _KHOA:
        for k in [k for k, v in _VE.items() if (v["module"], v["ho_so"]) == (module, ho_so)]:
            _VE.pop(k, None)
        _thu_hoi_phien_khoa([k for k, v in _PHIEN.items() if (v["module"], v["ho_so"]) == (module, ho_so)])
        _HO_SO.pop((module, ho_so), None)


def thu_hoi_tat_ca_phien() -> int:
    with _KHOA:
        so_luong = len(_PHIEN)
        _VE.clear()
        _thu_hoi_phien_khoa(list(_PHIEN))
        for evs in _WS.values():
            for ev in evs:
                ev.set()
        _WS.clear()
    return so_luong


# ───────────── Đích: tách userinfo, phân giải một lần, kiểm SSRF, ghim IP ─────────────

async def _phan_giai(ten: str, cong: int) -> list[str]:
    thong_tin = await asyncio.get_running_loop().getaddrinfo(ten, cong, type=socket.SOCK_STREAM)
    ds: list[str] = []
    for x in thong_tin:
        if x[4][0] not in ds:
            ds.append(x[4][0])
    return ds


async def ghim_dich(dich: str) -> dict[str, Any]:
    """Kiểm và ghim đích. ValueError mang lý do KHÔNG chứa URL/bí mật."""
    phan = urlsplit(str(dich or "").strip())
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError("đích phải dạng http(s)://<máy>:<cổng>")
    if phan.query or phan.fragment:
        raise ValueError("đích không được có query/fragment")
    try:
        cong = phan.port or (443 if phan.scheme == "https" else 80)
    except ValueError:
        raise ValueError("cổng của đích không hợp lệ") from None
    host = phan.hostname
    auth = (unquote(phan.username), unquote(phan.password or "")) if phan.username is not None else None
    try:
        ds = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            ds = await _phan_giai(host, cong)
        except OSError:
            raise ValueError("không phân giải được tên máy của đích") from None
    if not ds:
        raise ValueError("không phân giải được tên máy của đích")
    for ip in ds:
        ly_do = _dia_chi_bi_chan(ipaddress.ip_address(ip))
        if ly_do:
            raise ValueError(f"đích trỏ vào dải bị chặn: {ly_do}")
    host_url = f"[{host}]" if ":" in host else host
    mac_dinh = cong == (443 if phan.scheme == "https" else 80)
    return {
        "scheme": phan.scheme,
        "host": host,
        "port": cong,
        "duong": phan.path.rstrip("/"),
        "ip": ds[0],
        "host_hdr": host_url if mac_dinh else f"{host_url}:{cong}",
        "auth": auth,
    }


def _goc_ip(hs: dict[str, Any]) -> str:
    ip = hs["ip"]
    return f"{hs['scheme']}://{'[' + ip + ']' if ':' in ip else ip}:{hs['port']}{hs['duong']}"


def _basic(auth: tuple[str, str]) -> str:
    return "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")


def _ten_loi(e: BaseException) -> str:
    return type(e).__name__


def lam_sach_duong_proxy(duong: str) -> str:
    """Kiểm theo ĐOẠN rồi mã hoá lại từng đoạn. Đoạn `.`/`..`, đoạn rỗng giữa chừng (`//`), `\\` và ký tự điều khiển
    bị từ chối; tên như `a..b` hợp lệ; `?`/`#` đã giải mã trở thành một phần tên tệp (`%3F`, `%23`)."""
    s = str(duong or "")
    if s == "":
        return ""
    for ch in s:
        if ord(ch) < 32 or ord(ch) == 127 or ch == "\\":
            raise LoiOpenAI(400, "Đường dẫn chứa ký tự không hợp lệ")
    doan = s.split("/")
    cuoi_rong = doan[-1] == ""
    if cuoi_rong:
        doan = doan[:-1]
    for d in doan:
        if d in ("", ".", ".."):
            raise LoiOpenAI(400, "Đường dẫn chứa đoạn không hợp lệ")
    return "/".join(quote(d, safe="") for d in doan) + ("/" if cuoi_rong else "")


# ───────────── Lọc log: che đoạn phiên và vé ─────────────

_MAU_LOG = re.compile(r"(/novnc/s/)[^/\s?\"']+|([?&]ve=)[^&\s\"']+")


def _che_log(chu: str) -> str:
    return _MAU_LOG.sub(lambda m: (m.group(1) or m.group(2)) + "…", chu)


class BoLocLogNoVNC(logging.Filter):
    """uvicorn ghi đường dẫn WebSocket ở mức INFO qua `uvicorn.error` (kể cả khi `--no-access-log`) và đường dẫn HTTP
    qua `uvicorn.access`: che `/novnc/s/<phien>` thành `/novnc/s/…` và `ve=<vé>` thành `ve=…`."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Giữ ĐÚNG hình dạng `args`: AccessFormatter của uvicorn đọc 5 đối số theo vị trí; chỉ thay chuỗi đã che.
        if isinstance(record.msg, str):
            record.msg = _che_log(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_che_log(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _che_log(v) if isinstance(v, str) else v for k, v in record.args.items()}
        return True


_BO_LOC_LOG = BoLocLogNoVNC()
for _ten in ("uvicorn.error", "uvicorn.access"):
    logging.getLogger(_ten).addFilter(_BO_LOC_LOG)


# ───────────── Header bảo vệ cho mọi phản hồi dưới /novnc ─────────────

class MiddlewareBaoVeNoVNC:
    """Gắn header bảo vệ vào MỌI phản hồi HTTP dưới `/novnc/` — kể cả lỗi của trình xử lý ngoại lệ, 405 tự động và
    đường không khớp rơi xuống StaticFiles. Trang vé (`/novnc/dang-nhap`) dùng CSP riêng (không script, form về chính
    origin); mọi đường khác dưới `/novnc/` dùng CSP sandbox. Header cùng tên do đích/route đặt bị thay."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == "/novnc" or path.startswith("/novnc/")):
            await self.app(scope, receive, send)
            return
        bo = HEADER_BAO_VE_TRANG_VE if path == "/novnc/dang-nhap" else HEADER_BAO_VE_PROXY
        ten_bo = {k for k, _ in bo}

        async def gui(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = [(k, v) for k, v in msg.get("headers", []) if k.lower() not in ten_bo]
                msg = {**msg, "headers": headers + list(bo)}
            await send(msg)

        await self.app(scope, receive, gui)


# ───────────── Tuyến API quản trị (/api/trinh-duyet) ─────────────

api_router = APIRouter(prefix="/api/trinh-duyet", dependencies=QT)


def _module_co(request: Request, module: str, phuong_thuc: str, viec: str):
    mod = _so(request).dang_chay.get(module)
    if mod is None:
        raise LoiOpenAI(404, f"Không tìm thấy module «{module}»")
    if not hasattr(mod, phuong_thuc):
        raise LoiOpenAI(501, f"Module «{module}» không hỗ trợ {viec}")
    return mod


@api_router.post("/{module}/mo")
async def api_mo_trinh_duyet(module: str, request: Request):
    try:
        than = await request.json()
    except Exception:
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON hợp lệ")
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")

    loai = than.get("loai")
    if not isinstance(loai, str) or not loai.strip():
        raise LoiOpenAI(400, "Thiếu trường «loai» (loại trình duyệt)")
    loai = loai.strip()

    ho_so = than.get("ho_so")
    if ho_so is not None:
        if not isinstance(ho_so, str):
            raise LoiOpenAI(400, "Trường «ho_so» phải là chuỗi")
        ho_so = ho_so.strip()
        kiem_tra_ho_so_id(ho_so)
    else:
        ho_so = sinh_ho_so_id()
    url = kiem_url_bat_dau(than.get("url"))

    mod = _module_co(request, module, "trinh_duyet_mo", "mở trình duyệt")
    try:
        # `url` chỉ truyền khi có: module cũ chỉ nhận (ho_so, loai) vẫn chạy.
        kq = await mod.trinh_duyet_mo(ho_so=ho_so, loai=loai, **({"url": url} if url else {}))
    except Exception as e:
        log.warning("Module %s lỗi khi gọi trinh_duyet_mo: %s", module, _ten_loi(e))
        raise LoiOpenAI(502, f"Module lỗi khi mở trình duyệt ({_ten_loi(e)})")

    if not isinstance(kq, dict) or "ho_so" not in kq or "dich" not in kq:
        raise LoiOpenAI(502, "Kết quả từ module không đúng cấu trúc (cần có ho_so và dich)")

    ho_so_kq = str(kq.get("ho_so") or ho_so).strip()
    kiem_tra_ho_so_id(ho_so_kq)
    try:
        dich = await ghim_dich(str(kq.get("dich") or ""))
    except ValueError as e:
        raise LoiOpenAI(502, f"Đích của dịch vụ trình duyệt không hợp lệ hoặc bị chặn: {e}")

    with _KHOA:
        cu = _HO_SO.get((module, ho_so_kq))
        if cu is not None and (cu["scheme"], cu["host"], cu["port"], cu["duong"]) != (
                dich["scheme"], dich["host"], dich["port"], dich["duong"]):
            doi = True
        else:
            doi = False
            _HO_SO[(module, ho_so_kq)] = {**dich, "mod": mod, "loai": str(kq.get("loai") or loai),
                                         "luc_mo": datetime.now(timezone.utc).isoformat()}
    if doi:
        nhat_ky.ghi("trinh_duyet", "canh_bao", f"Từ chối mở lại hồ sơ {ho_so_kq} ({module}): đích đổi sang máy khác",
                    module=module, ho_so=ho_so_kq, hanh_dong="mo", ket_qua="that_bai")
        raise LoiOpenAI(502, "Đích của hồ sơ đang mở đã đổi sang máy khác; đóng hồ sơ rồi mở lại")

    nguoi_dung_id = lay_nguoi_dung_id(request)
    ve = cap_ve(module, ho_so_kq, nguoi_dung_id=nguoi_dung_id)
    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Mở hồ sơ trình duyệt {ho_so_kq} ({module})",
                module=module, ho_so=ho_so_kq, hanh_dong="mo", ket_qua="thanh_cong")
    return {"ho_so": ho_so_kq, "ve": ve, "url": f"/novnc/dang-nhap?ve={ve}"}


@api_router.post("/{module}/{ho_so}/ve")
async def api_xin_ve(module: str, ho_so: str, request: Request):
    kiem_tra_ho_so_id(ho_so)
    if _so(request).dang_chay.get(module) is None:
        raise LoiOpenAI(404, f"Không tìm thấy module «{module}»")
    with _KHOA:
        if (module, ho_so) not in _HO_SO:
            raise LoiOpenAI(404, f"Hồ sơ {ho_so} chưa được mở")
    nguoi_dung_id = lay_nguoi_dung_id(request)
    ve = cap_ve(module, ho_so, nguoi_dung_id=nguoi_dung_id)
    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Cấp vé mới cho hồ sơ {ho_so} ({module})",
                module=module, ho_so=ho_so, hanh_dong="xin_ve", ket_qua="thanh_cong")
    return {"ho_so": ho_so, "ve": ve, "url": f"/novnc/dang-nhap?ve={ve}"}


#: Trường của một mục hồ sơ theo hợp đồng `trinh_duyet_ds` — chỉ những trường này ra khỏi lõi (dịch vụ có thể trả
#: kèm `dich` mang bí mật).
_TRUONG_DS = ("ho_so", "ten", "loai", "trang_thai", "dang_mo", "thu_cong", "luc_mo")


@api_router.post("/{module}")
async def api_tao_workspace(module: str, request: Request):
    """Tạo workspace (hồ sơ có tên hiển thị; ID do dịch vụ sinh ngẫu nhiên, không suy từ tên). Không mở trình duyệt."""
    try:
        than = json.loads(await doc_het(request, THAN_JSON))
    except (ValueError, RecursionError):
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON hợp lệ") from None
    ten = than.get("ten") if isinstance(than, dict) else None
    ten = ten.strip() if isinstance(ten, str) else ""
    if not ten or len(ten) > TRAN_TEN_WORKSPACE or not ten.isprintable():
        raise LoiOpenAI(400, f"Nhập tên workspace (1–{TRAN_TEN_WORKSPACE} ký tự, không xuống dòng)")
    mod = _module_co(request, module, "trinh_duyet_tao", "tạo workspace")
    try:
        kq = await mod.trinh_duyet_tao(ten=ten)
    except Exception as e:
        log.warning("Module %s lỗi khi gọi trinh_duyet_tao: %s", module, _ten_loi(e))
        raise LoiOpenAI(502, f"Module lỗi khi tạo workspace ({_ten_loi(e)})") from None
    ho_so = kq.get("ho_so") if isinstance(kq, dict) else None
    if not isinstance(ho_so, str) or not MAU_HO_SO.fullmatch(ho_so):
        raise LoiOpenAI(502, "Kết quả tạo workspace từ module không đúng cấu trúc (cần ho_so hợp lệ)")
    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Tạo workspace {ho_so} ({module})", module=module, ho_so=ho_so,
                hanh_dong="tao", ket_qua="thanh_cong")
    return {"ho_so": ho_so, "ten": ten}


@api_router.get("/{module}")
async def api_danh_sach_ho_so(module: str, request: Request):
    mod = _module_co(request, module, "trinh_duyet_ds", "liệt kê hồ sơ")
    try:
        ds = await mod.trinh_duyet_ds()
    except Exception as e:
        log.warning("Module %s lỗi khi gọi trinh_duyet_ds: %s", module, _ten_loi(e))
        raise LoiOpenAI(502, f"Module lỗi khi liệt kê hồ sơ ({_ten_loi(e)})")
    if not isinstance(ds, list):
        raise LoiOpenAI(502, "Kết quả liệt kê hồ sơ từ module phải là danh sách")
    return [{k: x[k] for k in _TRUONG_DS if k in x} for x in ds if isinstance(x, dict)]


@api_router.post("/{module}/{ho_so}/dong")
async def api_dong_trinh_duyet(module: str, ho_so: str, request: Request):
    kiem_tra_ho_so_id(ho_so)
    giu_phien = True  # mặc định an toàn: không nói rõ thì KHÔNG xoá hồ sơ (cookie đã đăng nhập)
    if await request.body():
        try:
            than = await request.json()
        except Exception:
            raise LoiOpenAI(400, "Thân yêu cầu phải là JSON hợp lệ")
        if not isinstance(than, dict):
            raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")
        giu_phien = than.get("giu_phien", True)
        if not isinstance(giu_phien, bool):
            raise LoiOpenAI(400, "Trường «giu_phien» phải là true/false")

    # THU HỒI TRƯỚC: module lỗi thì quyền truy cập vẫn đã bị cắt.
    thu_hoi_ho_so(module, ho_so)
    mod = _module_co(request, module, "trinh_duyet_dong", "đóng trình duyệt")
    try:
        kq = await mod.trinh_duyet_dong(ho_so=ho_so, giu_phien=giu_phien)
    except Exception as e:
        log.warning("Module %s lỗi khi gọi trinh_duyet_dong: %s", module, _ten_loi(e))
        nhat_ky.ghi("trinh_duyet", "loi", f"Đóng hồ sơ {ho_so} ({module}): module lỗi, quyền truy cập đã thu hồi",
                    module=module, ho_so=ho_so, hanh_dong="dong", ket_qua="that_bai", da_thu_hoi=True,
                    kieu_loi=_ten_loi(e))
        than_ph = than_loi(502, f"Module lỗi khi đóng trình duyệt ({_ten_loi(e)}); vé và phiên đã bị thu hồi")
        return JSONResponse({**than_ph, "da_thu_hoi": True}, status_code=502)

    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Đóng hồ sơ trình duyệt {ho_so} ({module})",
                module=module, ho_so=ho_so, hanh_dong="dong", ket_qua="thanh_cong", da_thu_hoi=True)
    ok = bool(kq.get("ok", True)) if isinstance(kq, dict) else True
    return {"ok": ok, "da_thu_hoi": True}


@api_router.delete("/phien")
async def api_thu_hoi_het_phien():
    so_phien = thu_hoi_tat_ca_phien()
    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Thu hồi toàn bộ {so_phien} phiên noVNC",
                hanh_dong="thu_hoi_phien", ket_qua="thanh_cong")
    return {"ok": True, "so_phien": so_phien}


# ───────────── Cookie từ hồ sơ trình duyệt → cấu hình module đích ─────────────

def _gia_tri_da_luu(so: SoModule, km, truong: str) -> list[str]:
    """Giá trị đã lưu của một trường bí mật (đọc kho như `SoModule.cai`), chuẩn về danh sách chuỗi."""
    muc = so.muc_da_luu(km.id)
    if not muc:
        return []
    v = kho.doc_cau_hinh(km.id, km.truong_cau_hinh, muc).get(truong)
    if isinstance(v, str):
        return [v]
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _khoa_che(chu: Any) -> str:
    """`khoa` module đích trả chỉ được ra ngoài khi ĐÚNG dạng che «…» + tối đa 4 ký tự; khác thì «…»."""
    return chu if isinstance(chu, str) and MAU_KHOA_CHE.fullmatch(chu) else "…"


@api_router.post("/{module}/{ho_so}/cookie-sang-module")
async def api_cookie_sang_module(module: str, ho_so: str, request: Request):
    kiem_tra_ho_so_id(ho_so)
    try:
        than = json.loads(await doc_het(request, THAN_JSON))
    except (ValueError, RecursionError):
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON hợp lệ") from None
    if not isinstance(than, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là đối tượng JSON")
    mien, module_dich, truong = than.get("mien"), than.get("module_dich"), than.get("truong")
    so = _so(request)
    km = so.catalog.get(module_dich) if isinstance(module_dich, str) and ID_HOP_LE.fullmatch(module_dich) else None
    if km is None:
        raise LoiOpenAI(404, "Không có module đích này (cần id của module đã cài, xem GET /api/modules/catalog)")
    # Chỉ trường mà CHÍNH module đích khai `nhan_cookie` (kham_pha đã kiểm hình dạng: trường bí mật + tên miền chặt):
    # người gọi không ghi được cookie vào khoá API của module khác, và không chọn tên miền.
    khai = next((t for t in km.truong_cau_hinh if isinstance(truong, str) and t["khoa"] == truong), None)
    if khai is None or khai["kieu"] not in kho.KIEU_BI_MAT or not isinstance(khai.get("nhan_cookie"), dict):
        raise LoiOpenAI(400, "Trường «truong» phải là trường mà module đích khai nhận cookie (nhan_cookie trong manifest)")
    if mien not in (None, "", khai["nhan_cookie"]["mien"]):
        raise LoiOpenAI(400, "Trường «mien» khác tên miền mà module đích khai nhận cookie — bỏ trống để dùng tên miền đã khai")
    mien = khai["nhan_cookie"]["mien"]  # kham_pha.kiem_manifest đã kiểm theo MAU_MIEN lúc nạp module
    la_danh_sach = khai["kieu"] == "danh_sach_bi_mat"
    mod = _module_co(request, module, "trinh_duyet_cookie", "lấy cookie từ hồ sơ")

    boi_canh = {"module": module, "ho_so": ho_so, "module_dich": module_dich, "truong": truong, "mien": mien,
                "hanh_dong": "cookie_sang_module"}
    tom_tat = f"Cookie hồ sơ {ho_so} ({module}) sang {module_dich}/{truong}"
    try:
        kq = await mod.trinh_duyet_cookie(ho_so=ho_so, mien=mien)
    except Exception as e:
        ma = getattr(e, "ma", None)
        nhat_ky.ghi("trinh_duyet", "loi", f"{tom_tat}: module nguồn lỗi", **boi_canh, ket_qua="that_bai",
                    kieu_loi=_ten_loi(e), ma=ma if type(ma) is int else None)
        if type(ma) is int and ma in LOI_COOKIE_NGUON:
            raise LoiOpenAI(ma, LOI_COOKIE_NGUON[ma]) from None
        log.warning("Module %s lỗi khi gọi trinh_duyet_cookie: %s", module, _ten_loi(e))
        raise LoiOpenAI(502, f"Module lỗi khi lấy cookie ({_ten_loi(e)})") from None
    cookie = kq.get("cookie") if isinstance(kq, dict) else None
    if not isinstance(cookie, str):
        raise LoiOpenAI(502, "Kết quả lấy cookie từ module không đúng cấu trúc (cần {\"cookie\": chuỗi})")
    cookie = cookie.strip()
    if not cookie:
        nhat_ky.ghi("trinh_duyet", "canh_bao", f"{tom_tat}: hồ sơ chưa có cookie cho tên miền", **boi_canh,
                    ket_qua="that_bai")
        raise LoiOpenAI(404, "Hồ sơ chưa có cookie nào của tên miền này — đăng nhập trong trình duyệt, đóng (giữ phiên) "
                             "rồi thử lại. Nếu cookie đăng nhập là cookie phiên (không hạn) thì trình duyệt có thể không "
                             "ghi nó ra đĩa — khi đó dán cookie tay vào cấu hình module")
    if len(cookie) > TRAN_COOKIE or not MAU_CHUOI_COOKIE.fullmatch(cookie):
        raise LoiOpenAI(502, "Chuỗi cookie từ module không hợp lệ (ký tự điều khiển hoặc quá dài)")

    try:
        truoc = _gia_tri_da_luu(so, km, truong)
        # Dạng sửa {"them": [...]} của đường cài có sẵn: giữ mọi khoá đã lưu, thêm vào cuối, khử trùng chuỗi y hệt.
        kq_cai = await so.cai(module_dich, {truong: {"them": [cookie]} if la_danh_sach else cookie})
        sau = _gia_tri_da_luu(so, km, truong) if kq_cai["ok"] else []
    except (LoiSo, kho.KhoHong) as e:  # lời nhắn của sổ/kho không chứa giá trị khoá (như POST /api/modules/{id}/cai)
        nhat_ky.ghi("trinh_duyet", "loi", f"{tom_tat}: không cài được", **boi_canh, ket_qua="that_bai",
                    kieu_loi=_ten_loi(e))
        raise LoiOpenAI(e.ma if isinstance(e, LoiSo) else 500, str(e)) from None
    except Exception as e:  # mã module đích: lỗi của nó, không nhúng nguyên văn (có thể chứa cookie)
        nhat_ky.ghi("trinh_duyet", "loi", f"{tom_tat}: module đích lỗi khi kiểm", **boi_canh, ket_qua="that_bai",
                    kieu_loi=_ten_loi(e))
        log.warning("Module %s lỗi khi kiểm cookie: %s", module_dich, _ten_loi(e))
        raise LoiOpenAI(502, f"Module đích lỗi khi kiểm cookie ({_ten_loi(e)})") from None

    da_them = int(bool(kq_cai["ok"]) and cookie not in truoc and cookie in sau)
    # Chỉ dữ liệu CÓ CẤU TRÚC đi ra: ok (bool), khoa (đúng dạng che), câu lỗi của LÕI — không chuỗi tự do nào của module.
    ket_qua = [{"khoa": _khoa_che(x.get("khoa")), "ok": bool(x.get("ok")),
                "loi": None if x.get("ok") else LOI_KHOA_DICH}
               for x in kq_cai.get("ket_qua_tung_khoa") or [] if isinstance(x, dict)]
    ra = {"ok": bool(kq_cai["ok"]), "ket_qua_tung_khoa": ket_qua, "da_them": da_them}
    if not ra["ok"]:
        ra["loi"] = LOI_TONG_DICH
    nhat_ky.ghi("trinh_duyet", "thong_tin" if ra["ok"] else "canh_bao",
                f"{tom_tat}: {'đã cài' if ra['ok'] else 'module đích từ chối'}", **boi_canh,
                ket_qua="thanh_cong" if ra["ok"] else "that_bai", da_them=da_them)
    return JSONResponse(ra, status_code=200 if ra["ok"] else 400)


# ───────────── Tuyến noVNC (/novnc) ─────────────

novnc_router = APIRouter(prefix="/novnc")

#: Không thẻ khai báo charset/viewport: charset đi ở header của HTMLResponse; chữ to để bấm được trên điện thoại.
TRANG_VE = """<!doctype html>
<html lang="vi"><head>
<title>Mở trình duyệt từ xa</title>
<style>body{{font:40px system-ui,sans-serif;margin:0;padding:16px;display:grid;place-items:center;min-height:90vh}}
form{{max-width:28rem;text-align:center}}button{{font:inherit;padding:.75em 1.5em;cursor:pointer}}</style></head>
<body><form method="post" action="/novnc/dang-nhap">
<p>Bấm nút để mở phiên trình duyệt từ xa. Liên kết chỉ dùng được một lần trong 60 giây.</p>
<input type="hidden" name="ve" value="{ve}">
<button type="submit">Mở trình duyệt</button>
</form></body></html>"""


def _loi_json(ma: int, thong_diep: str) -> JSONResponse:
    return JSONResponse(than_loi(ma, thong_diep), status_code=ma)


@novnc_router.get("/dang-nhap")
async def novnc_trang_ve(request: Request):
    """Trang nút, KHÔNG tiêu thụ vé (bot xem trước liên kết chỉ GET)."""
    if "origin" in request.headers:
        if not dia_chi.origin_hop_le(request, request.headers.get("origin")):
            nhat_ky.ghi("trinh_duyet", "canh_bao", "Origin không hợp lệ khi truy cập trang vé",
                        hanh_dong="trang_ve", ket_qua="that_bai")
            return _loi_json(403, "Truy cập bị từ chối.")
    ve = request.query_params.get("ve", "")
    if not MAU_VE.fullmatch(ve):
        return HTMLResponse("<!doctype html><p>Liên kết không hợp lệ.</p>", status_code=400)
    return HTMLResponse(TRANG_VE.format(ve=html.escape(ve, quote=True)))


@novnc_router.post("/dang-nhap")
async def novnc_doi_ve(request: Request):
    gioi_han.kiem_tra_truoc_xac_thuc(request)
    # Không dùng request.form(): với multipart, phần TỆP không có trần và bị ghi ra tệp tạm trước khi kịp từ chối
    # (cùng lý do ở app/yeu_cau_da_phan.py). Chỉ nhận urlencoded, kiểm loại TRƯỚC khi đọc, đọc có trần.
    loai = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if loai != "application/x-www-form-urlencoded":
        return _loi_json(415, "Chỉ nhận application/x-www-form-urlencoded.")
    try:
        khai = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        return _loi_json(400, "Content-Length không hợp lệ.")
    if khai > THAN_DOI_VE_TOI_DA:
        return _loi_json(413, "Thân yêu cầu quá lớn.")
    than = bytearray()
    async for khuc in request.stream():
        than += khuc
        if len(than) > THAN_DOI_VE_TOI_DA:
            return _loi_json(413, "Thân yêu cầu quá lớn.")
    try:
        truong = parse_qs(than.decode("ascii"), max_num_fields=8)
    except (UnicodeDecodeError, ValueError):
        truong = {}
    ds_ve = truong.get("ve", [])
    ve_raw = ds_ve[0] if len(ds_ve) == 1 else ""
    co_ve = bool(ve_raw)
    co_origin = "origin" in request.headers

    if co_origin:
        if not dia_chi.origin_hop_le(request, request.headers.get("origin")):
            nhat_ky.ghi("trinh_duyet", "canh_bao", "Origin không hợp lệ",
                        hanh_dong="doi_ve", ket_qua="that_bai")
            return _loi_json(403, "Truy cập bị từ chối.")
    elif co_ve:
        nhat_ky.ghi("trinh_duyet", "canh_bao", "Thiếu Origin khi đổi vé",
                    hanh_dong="doi_ve", ket_qua="that_bai")
        return _loi_json(403, "Truy cập bị từ chối.")

    ma, phien, tt = doi_ve_lay_phien(ve_raw)
    if ma == 429:
        nhat_ky.ghi("trinh_duyet", "canh_bao", "Từ chối đổi vé: chạm trần số phiên", module=tt["module"],
                    ho_so=tt["ho_so"], hanh_dong="doi_ve", ket_qua="that_bai")
        return _loi_json(429, "Đã đủ số phiên noVNC cho phép; đóng bớt phiên rồi thử lại trong hạn của vé.")
    if ma != 200:
        gioi_han.ghi_loi_xac_thuc(request)
        gioi_han.kiem_tra_truoc_xac_thuc(request)
        nhat_ky.ghi("trinh_duyet", "canh_bao", "Từ chối đổi vé: vé không hợp lệ hoặc đã hết hạn",
                    hanh_dong="doi_ve", ket_qua="that_bai")
        return _loi_json(401, "Vé không hợp lệ hoặc đã hết hạn.")
    nhat_ky.ghi("trinh_duyet", "thong_tin", f"Đổi vé lấy phiên cho hồ sơ {tt['ho_so']} ({tt['module']})",
                module=tt["module"], ho_so=tt["ho_so"], hanh_dong="doi_ve", ket_qua="thanh_cong")
    # noVNC 1.6 dựng đường WebSocket bằng new URL(path, location.href): «websockify» → /novnc/s/<phien>/websockify.
    return RedirectResponse(f"/novnc/s/{phien}/vnc.html?autoconnect=1&resize=scale&path=websockify", status_code=303)


@novnc_router.websocket("/s/{phien}/websockify")
async def novnc_websocket_proxy(websocket: WebSocket, phien: str):
    bam_phien = _bam(phien)
    huy = asyncio.Event()
    so = websocket.app.state.so
    with _KHOA:
        kq = _phien_va_dich_khoa(bam_phien, so)
        p, hs = kq if kq else (None, None)
        if hs is not None:
            _WS.setdefault(bam_phien, set()).add(huy)  # đăng ký NGAY, trước khi nối backend
    if hs is None:
        await websocket.close(code=1008)
        return

    backend = None
    da_nhan = False
    ma_dong = 1000
    try:
        dich_ws = f"{'wss' if hs['scheme'] == 'https' else 'ws'}://{hs['host_hdr']}{hs['duong']}/websockify"
        them: dict[str, Any] = {}
        if hs["auth"] is not None:
            them["additional_headers"] = {"Authorization": _basic(hs["auth"])}
        try:
            backend = await websockets.connect(
                dich_ws, host=hs["ip"], port=hs["port"], proxy=None, max_size=WS_KHUNG_TOI_DA, open_timeout=10,
                logger=_LOG_WS_DICH,
                subprotocols=websocket.scope.get("subprotocols") or None, **them)
        except Exception as e:
            log.warning("Không nối được WebSocket tới đích %s: %s", hs["host_hdr"], _ten_loi(e))
            await websocket.close(code=1011)
            return

        with _KHOA:  # kiểm lại: đóng/thu hồi/hết hạn có thể đã chen vào lúc đang nối backend
            kq_lai = _phien_va_dich_khoa(bam_phien, so)
            con_hieu_luc = not huy.is_set() and kq_lai is not None and kq_lai[1] is hs
        if not con_hieu_luc:
            await websocket.close(code=1008)
            return

        await websocket.accept(subprotocol=backend.subprotocol)
        da_nhan = True

        async def khach_toi_dich():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    await backend.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await backend.send(msg["text"])

        async def dich_toi_khach():
            async for data in backend:
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(data)

        t_huy = asyncio.create_task(huy.wait())
        tac_vu = {asyncio.create_task(khach_toi_dich()), asyncio.create_task(dich_toi_khach()), t_huy}
        try:
            while True:
                with _KHOA:
                    kq_nay = _phien_va_dich_khoa(bam_phien, so)
                if kq_nay is None:  # hết hạn / module bị gỡ hoặc thay
                    ma_dong = MA_DONG_THU_HOI
                    break
                cho = max(0.0, min(CHU_KY_KIEM_WS_GIAY, kq_nay[0]["het_han"] - _bay_gio()))
                xong, _ = await asyncio.wait(tac_vu, timeout=cho, return_when=asyncio.FIRST_COMPLETED)
                if t_huy in xong:  # bị thu hồi từ nơi khác (đóng hồ sơ, DELETE phiên, dọn phiên hết hạn)
                    ma_dong = MA_DONG_THU_HOI
                if xong:
                    break
        finally:
            for t in tac_vu:
                t.cancel()
            await asyncio.gather(*tac_vu, return_exceptions=True)
    finally:
        with _KHOA:
            evs = _WS.get(bam_phien)
            if evs is not None:
                evs.discard(huy)
                if not evs:
                    _WS.pop(bam_phien, None)
        if backend is not None:
            try:
                await backend.close()
            except Exception:  # noqa: BLE001 - đang dọn
                pass
        if da_nhan:
            try:
                await websocket.close(code=ma_dong)
            except Exception:  # noqa: BLE001 - khách đã ngắt
                pass


@novnc_router.api_route("/s/{phien}/{duong:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def novnc_http_proxy(phien: str, duong: str, request: Request):
    if request.method not in ("GET", "HEAD"):
        return JSONResponse(than_loi(405, "Proxy noVNC chỉ cho GET và HEAD."), status_code=405,
                            headers={"Allow": "GET, HEAD"})
    with _KHOA:
        kq = _phien_va_dich_khoa(_bam(phien), _so(request))
    if kq is None:
        return _loi_json(401, "Phiên không hợp lệ, đã hết hạn hoặc đã bị thu hồi.")
    hs = kq[1]
    try:
        duong_ma = lam_sach_duong_proxy(duong)
    except LoiOpenAI as e:
        return JSONResponse(e.than, status_code=e.ma)

    headers = {k: v for k, v in request.headers.items() if k.lower() in YC_CHO_PHEP}
    headers["host"] = hs["host_hdr"]
    headers["accept-encoding"] = "identity"
    phan_mo_rong = {"sni_hostname": hs["host"]} if hs["scheme"] == "https" else {}
    client = httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=False)
    try:
        yc = client.build_request(request.method, f"{_goc_ip(hs)}/{duong_ma}",
                                  params=request.query_params.multi_items(), headers=headers,
                                  extensions=phan_mo_rong)
        r = await client.send(yc, stream=True,
                              auth=httpx.BasicAuth(*hs["auth"]) if hs["auth"] is not None else None)
    except Exception as e:
        await client.aclose()
        log.warning("Lỗi kết nối proxy HTTP tới đích %s: %s", hs["host_hdr"], _ten_loi(e))
        return _loi_json(502, "Không kết nối được tới dịch vụ đích.")

    if r.status_code in (401, 407):  # lõi ↔ dịch vụ lệch xác thực: không để trình duyệt hỏi mật khẩu
        await r.aclose()
        await client.aclose()
        log.warning("Đích %s từ chối xác thực của lõi (HTTP %d)", hs["host_hdr"], r.status_code)
        return _loi_json(502, "Dịch vụ đích từ chối kết nối của lõi.")

    if 300 <= r.status_code < 400 and r.status_code != 304:  # không theo, không chuyển tiếp chuyển hướng của đích
        await r.aclose()
        await client.aclose()
        log.warning("Đích %s trả chuyển hướng HTTP %d — bỏ", hs["host_hdr"], r.status_code)
        return _loi_json(502, "Dịch vụ đích trả chuyển hướng; proxy không theo.")

    headers_tra = {k: v for k, v in r.headers.items() if k.lower() in PH_CHO_PHEP}

    async def _stream_va_dong():
        try:
            async for khuc in r.aiter_bytes():  # đã giải nén nếu đích vẫn nén bất chấp identity
                yield khuc
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(_stream_va_dong(), status_code=r.status_code, headers=headers_tra)


# Đăng ký mốc thu hồi với kho phiên web khi người dùng bị thu hồi toàn bộ phiên
phien_web.dang_ky_moc_thu_hoi(thu_hoi_phien_theo_nguoi_dung)
