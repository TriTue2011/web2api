"""Quản lý địa chỉ truy cập công khai và soạn hướng dẫn kết nối của web2api.

Thứ tự ưu tiên 3 nguồn:
  (1) Giá trị đã đặt qua web: lưu <WEB2API_DATA>/dia_chi.json (ghi nguyên tử, công khai).
  (2) Biến môi trường: WEB2API_PUBLIC_URL.
  (3) Suy ra từ yêu cầu đang tới (Host), cờ dang_doan = True.

Chỉ tin X-Forwarded-Proto / X-Forwarded-Host khi WEB2API_TIN_PROXY=1. Header lạ/độc bị bỏ qua.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import urllib.parse
from typing import Any

from fastapi import Request

from app import kho
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.dia_chi")

#: Một nhãn tên máy: chữ/số/«-»/«_» (gạch dưới: tên container Docker), không mở/đóng bằng «-», ≤ 63 ký tự.
_NHAN = r"(?!-)[a-z0-9_-]{1,63}(?<!-)"
_TEN_MAY = re.compile(rf"^{_NHAN}(?:\.{_NHAN})*\.?$")
#: Đường dẫn tiền tố: chỉ ký tự «không dành riêng» của RFC 3986, «%XX» và «/». Ký tự shell/nháy/«\\» bị loại —
#: URL này đi nguyên văn vào lệnh curl và mã Python soạn sẵn ở /api/ket-noi (và địa chỉ gọi lại của module).
_DUONG = re.compile(r"^(?:/(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})*)*$")
TOI_DA_URL = 2048
TOI_DA_HEADER = 260


def _sai(thong_diep: str) -> LoiOpenAI:
    return LoiOpenAI(400, thong_diep, loai="invalid_request_error")


def _chuan_hoa_ten_may(ten: str | None) -> str | None:
    """Tên máy → dạng ASCII thường (IDN → punycode như curl/trình duyệt) hoặc IP; sai → None."""
    if not ten:
        return None
    try:
        ip = ipaddress.ip_address(ten)
        return f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    except ValueError:
        pass
    try:
        ascii_ = ten.encode("idna").decode("ascii").lower() if not ten.isascii() else ten.lower()
    except UnicodeError:
        return None
    return ascii_ if len(ascii_) <= 253 and _TEN_MAY.match(ascii_) else None


def _chuan_hoa_authority(gia_tri: str) -> str | None:
    """"tên-máy[:cổng]" (dạng của Host / X-Forwarded-Host) → dạng chuẩn hoá, hoặc None nếu sai bất kỳ điểm nào."""
    if not gia_tri or len(gia_tri) > TOI_DA_HEADER or not gia_tri.isprintable() or any(
            k in gia_tri for k in " /?#@\\"):
        return None
    try:
        tach = urllib.parse.urlsplit(f"//{gia_tri}")
        cong = tach.port
    except ValueError:  # cổng không phải số / ngoài 0–65535, IPv6 hỏng
        return None
    ten = _chuan_hoa_ten_may(tach.hostname)
    if ten is None or cong == 0:
        return None
    return ten + (f":{cong}" if cong is not None else "")


def kiem_tra_va_chuan_hoa_url(url: str) -> str:
    """Kiểm tra và chuẩn hoá URL công khai.

    Yêu cầu: chỉ http/https; không user:pass; không query/fragment; ≤ 2048 ký tự; không ký tự điều khiển/khoảng
    trắng; tên máy hợp lệ (IDN đổi sang punycode), cổng 1–65535; tiền tố đường dẫn tuỳ chọn chỉ gồm ký tự không dành
    riêng (bỏ «/» cuối). Sai → LoiOpenAI(400). Thông điệp lỗi KHÔNG nhắc lại URL (có thể mang mật khẩu).
    """
    if not isinstance(url, str):
        raise _sai("Địa chỉ URL phải là chuỗi ký tự")
    url = url.strip()
    if not url:
        raise _sai("Địa chỉ URL không được để trống")
    if len(url) > TOI_DA_URL:
        raise _sai(f"Địa chỉ URL quá dài (tối đa {TOI_DA_URL} ký tự)")
    # urlsplit lặng lẽ XOÁ tab/xuống dòng (bpo-43882) — phải chặn trước khi tách.
    if not url.isprintable() or any(k.isspace() for k in url):
        raise _sai("Địa chỉ URL chứa ký tự không hợp lệ (khoảng trắng hoặc ký tự điều khiển)")

    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise _sai("Địa chỉ URL không đúng định dạng") from None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise _sai("URL phải dùng giao thức http hoặc https")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise _sai("URL không được chứa thông tin xác thực (user:pass)")
    if parsed.query:
        raise _sai("URL không được chứa tham số query (?…)")
    if parsed.fragment:
        raise _sai("URL không được chứa đoạn neo (#…)")
    authority = _chuan_hoa_authority(parsed.netloc)
    if authority is None:
        raise _sai("URL thiếu hoặc sai tên máy chủ / cổng (cổng 1–65535; tên máy chỉ chữ, số, «-», «.»)")
    path = parsed.path.rstrip("/")
    if not _DUONG.match(path):
        raise _sai("Đường dẫn trong URL chỉ được gồm chữ, số, «-», «.», «_», «~», «/» và «%XX»")
    return f"{scheme}://{authority}{path}"


def la_tin_proxy() -> bool:
    """True nếu người vận hành bật WEB2API_TIN_PROXY=1."""
    return os.environ.get("WEB2API_TIN_PROXY", "").strip() == "1"


def _mot_gia_tri(request: Request, ten: str) -> str | None:
    """Giá trị DUY NHẤT của header (gộp mọi dòng trùng tên, tách theo dấu phẩy). Nhiều giá trị khác nhau → None:
    không đoán phần nào do proxy ghi, phần nào do khách gửi."""
    gia_tri = {x.strip() for dong in request.headers.getlist(ten) for x in dong.split(",") if x.strip()}
    return gia_tri.pop() if len(gia_tri) == 1 else None


def _loc_header_proto(proto: str | None) -> str | None:
    dau = (proto or "").lower()
    return dau if dau in ("http", "https") else None


def _may_chu_cua_ket_noi(request: Request) -> str:
    """Địa chỉ máy chủ của chính kết nối (scope["server"]) — dùng khi Host thiếu/sai, không bao giờ lấy lại Host."""
    server = request.scope.get("server")
    if server and server[0]:
        ten = _chuan_hoa_ten_may(str(server[0]))
        if ten is not None:
            return ten + (f":{server[1]}" if server[1] else "")
    return "localhost"


def suy_ra_dia_chi(request: Request) -> str:
    """Suy ra URL từ yêu cầu HTTP (Host header và scheme)."""
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    host = _chuan_hoa_authority(_mot_gia_tri(request, "host") or "") or _may_chu_cua_ket_noi(request)
    root_path = request.scope.get("root_path", "").rstrip("/")
    if not _DUONG.match(root_path):
        root_path = ""

    if la_tin_proxy():
        x_proto = _loc_header_proto(_mot_gia_tri(request, "x-forwarded-proto"))
        if x_proto:
            scheme = x_proto
        x_host = _chuan_hoa_authority(_mot_gia_tri(request, "x-forwarded-host") or "")
        if x_host:
            host = x_host

    return f"{scheme}://{host}{root_path}"


def _la_dia_chi_internet(host: str) -> bool:
    """Tên máy có phải địa chỉ Internet công khai không — phân loại bằng `ipaddress` (is_global), không bảng tên.
    `localhost`/`*.localhost` (RFC 6761) và `*.local` (mDNS, RFC 6762) là tên đặc biệt không ra Internet. Dạng IPv4
    cũ (`2130706433`, `127.1`) hiểu bằng inet_aton — đúng như curl/trình duyệt sẽ hiểu. Tên miền thường: công khai."""
    h = host.strip("[]").rstrip(".").lower()
    if h == "localhost" or h.endswith((".localhost", ".local")):
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(h))
        except OSError:
            return True
    return ip.is_global


def kiem_tra_canh_bao(url: str, dang_doan: bool) -> list[str]:
    """Phát hiện cảnh báo bảo mật và trạng thái cấu hình."""
    canh_bao = []
    if dang_doan:
        canh_bao.append(
            "Địa chỉ đang được suy đoán từ yêu cầu hiện tại, hãy đặt địa chỉ chính thức qua PUT /api/dia-chi hoặc biến WEB2API_PUBLIC_URL."
        )
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() == "http" and _la_dia_chi_internet(parsed.hostname or ""):
        canh_bao.append(
            "Đang chạy không mã hoá (HTTP công khai), khoá đi rõ chữ — "
            "đặt HTTPS (Cloudflare) trước khi đưa cho người ngoài."
        )
    return canh_bao


def lay_thong_tin_dia_chi(request: Request) -> dict[str, Any]:
    """Lấy thông tin địa chỉ truy cập công khai theo thứ tự ưu tiên 3 nguồn. Nguồn nào hỏng (tệp đã đặt hỏng, biến
    môi trường sai) thì bỏ qua nguồn đó NHƯNG nói rõ trong `canh_bao` — lặng lẽ rơi xuống «đoán» thì người vận hành
    không biết cấu hình của mình không có tác dụng."""
    hong: list[str] = []
    url, nguon = None, None
    try:
        da_dat = kho.doc_dia_chi_da_dat()
        if da_dat:
            url, nguon = kiem_tra_va_chuan_hoa_url(da_dat), "da_dat"
    except kho.KhoHong:
        hong.append("Tệp dia_chi.json trong thư mục dữ liệu bị hỏng — đã bỏ qua; đặt lại bằng PUT /api/dia-chi.")
    except LoiOpenAI as e:
        hong.append(f"Địa chỉ trong dia_chi.json không hợp lệ ({e}) — đã bỏ qua; đặt lại bằng PUT /api/dia-chi.")
    if url is None:
        tu_bien = os.environ.get("WEB2API_PUBLIC_URL", "").strip()
        if tu_bien:
            try:
                url, nguon = kiem_tra_va_chuan_hoa_url(tu_bien), "bien_moi_truong"
            except LoiOpenAI as e:
                hong.append(f"Biến WEB2API_PUBLIC_URL không hợp lệ ({e}) — đã bỏ qua.")
    if url is None:
        url, nguon = suy_ra_dia_chi(request), "suy_ra"
    dang_doan = nguon == "suy_ra"
    return {
        "url": url,
        "nguon": nguon,
        "dang_doan": dang_doan,
        "tin_proxy": la_tin_proxy(),
        "canh_bao": hong + kiem_tra_canh_bao(url, dang_doan),
    }


def dia_chi_hien_tai(request: Request) -> str:
    """Hàm dùng chung: URL gốc công khai của web2api (không «/» cuối).

    CHÚ Ý cho module dùng làm địa chỉ gọi lại OAuth/webhook: khi chưa đặt địa chỉ (nguồn «suy_ra») URL này dựng từ
    Host — tức do KHÁCH gửi (và X-Forwarded-* khi bật WEB2API_TIN_PROXY). Gửi nó cho bên thứ ba là để kẻ gửi Host giả
    nhận mã ủy quyền. Module cần địa chỉ tin được thì gọi `lay_thong_tin_dia_chi(request)` và từ chối khi
    `dang_doan` là true (nhắc người vận hành đặt địa chỉ ở Hệ thống hoặc WEB2API_PUBLIC_URL)."""
    return lay_thong_tin_dia_chi(request)["url"]


def soan_huong_dan_ket_noi(request: Request) -> dict[str, Any]:
    """Soạn sẵn hướng dẫn kết nối: base_url, ví dụ curl, SDK Python và ứng dụng."""
    info = lay_thong_tin_dia_chi(request)
    url = info["url"]
    base_url = f"{url}/v1"

    mau_curl = (
        f'curl -s -H "Authorization: Bearer <KHOÁ>" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -d \'{{"model": "<tiền-tố>/<tên-model>", "messages": [{{"role": "user", "content": "Xin chào"}}]}}\' \\\n'
        f'  {base_url}/chat/completions'
    )

    mau_python = (
        f'from openai import OpenAI\n\n'
        f'client = OpenAI(\n'
        f'    base_url="{base_url}",\n'
        f'    api_key="<KHOÁ>",\n'
        f')\n'
        f'tra_loi = client.chat.completions.create(\n'
        f'    model="<tiền-tố>/<tên-model>",\n'
        f'    messages=[{{"role": "user", "content": "Xin chào"}}],\n'
        f')\n'
        f'print(tra_loi.choices[0].message.content)'
    )

    ung_dung = {
        "base_url": base_url,
        "api_key": "<KHOÁ>",
        "mo_ta": "Điền vào phần cấu hình OpenAI của ứng dụng khách (như Open WebUI, LibreChat, Cline, Cursor…).",
    }

    trong_nha = {
        "base_url": "http://<IP-máy-chủ>:3031/v1",
        "curl": (
            'curl -s -H "Authorization: Bearer <KHOÁ>" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"model": "<tiền-tố>/<tên-model>", "messages": [{"role": "user", "content": "Xin chào"}]}\' \\\n'
            '  http://<IP-máy-chủ>:3031/v1/chat/completions'
        ),
        "python": (
            'from openai import OpenAI\n\n'
            'client = OpenAI(base_url="http://<IP-máy-chủ>:3031/v1", api_key="<KHOÁ>")\n'
            'tra_loi = client.chat.completions.create(\n'
            '    model="<tiền-tố>/<tên-model>",\n'
            '    messages=[{"role": "user", "content": "Xin chào"}],\n'
            ')\n'
            'print(tra_loi.choices[0].message.content)'
        ),
        "mo_ta": "Dùng trong mạng nội bộ (LAN). Thay <IP-máy-chủ> bằng địa chỉ IP của máy chạy web2api.",
    }

    qua_domain = {
        "base_url": "https://<tên-miền>/v1",
        "curl": (
            'curl -s -H "Authorization: Bearer <KHOÁ>" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"model": "<tiền-tố>/<tên-model>", "messages": [{"role": "user", "content": "Xin chào"}]}\' \\\n'
            '  https://<tên-miền>/v1/chat/completions'
        ),
        "python": (
            'from openai import OpenAI\n\n'
            'client = OpenAI(base_url="https://<tên-miền>/v1", api_key="<KHOÁ>")\n'
            'tra_loi = client.chat.completions.create(\n'
            '    model="<tiền-tố>/<tên-model>",\n'
            '    messages=[{"role": "user", "content": "Xin chào"}],\n'
            ')\n'
            'print(tra_loi.choices[0].message.content)'
        ),
        "mo_ta": "Dùng qua Internet với HTTPS (ví dụ qua Cloudflare Tunnel). Thay <tên-miền> bằng tên miền đã cấu hình.",
    }

    return {
        "base_url": base_url,
        "curl": mau_curl,
        "python": mau_python,
        "ung_dung": ung_dung,
        "trong_nha": trong_nha,
        "qua_domain": qua_domain,
        "canh_bao": info["canh_bao"],
    }
