"""Đường riêng của module dưới tiền tố /m/<id>/api/... (NV277).

LÝ DO CÓ ĐƯỜNG NÀY — và đường nào MỞ:
- Lõi trước đây chỉ có đường tĩnh GET /m/<id>/<tệp> cho tab web của module
  (app/tuyen.py: tep_module) và các đường /v1, /api/* chuẩn. Một module kiểu
  máy phát cho hệ thống nhà cần đường riêng giống dịch vụ phụ cũ
  (/api/integration/* + đường luồng cho loa), nhưng KHÔNG được tự gắn route
  (hợp đồng app/kham_pha.py: «module không có route»).
- Vì vậy LÕI sở hữu tập đường này. Module chỉ khai trong manifest
    "duong_module": {"lan_chi": true}
  và cài HAI phương thức (thiếu thì đường trả 404, lõi không vỡ):
    async tich_hop(duong, phuong_thuc, query, than, goc) -> (mã, headers, nội dung)
        duong: phần sau /api/ (vd "integration/search"); than: dict JSON hoặc
        None khi thân rỗng; goc: origin (scheme://host[:cổng]) mà chính bên gọi
        đã dùng để tới lõi — để module dựng URL tuyệt đối cho thiết bị khác
        (app/dia_chi.origin_cua_may_chu, đã chuẩn hoá); nội dung trả về: dict
        (thành JSON), bytes, hoặc async-iterator[bytes] (lõi stream từng khúc).
    async luong(ve, khoang, phuong_thuc) -> (mã, headers, nội dung)
        ve: vé luồng đã đăng ký (xem dưới); khoang: header Range của loa
        (hoặc None); nội dung: bytes hoặc async-iterator[bytes].
- XÁC THỰC đường integration (NV289) — KHÔNG có bí mật riêng do người dùng nhập
  tay. Dùng đúng khuôn khoá khách phạm vi `mcp` (app/cong_mcp.py): khoá khách do
  CHÍNH lõi sinh (app/khoa_khach.py: hiện một lần, lưu băm SHA-256, thu hồi/tắt
  được), qua app/xac_thuc.nguoi_goi (có khoá IP khi dò khoá sai). Phạm vi ĐÃ LƯU
  của khoá phải gồm toàn phần tử dạng `tich_hop@<module>` (cú pháp «khả năng
  giới hạn theo module» có sẵn của khoa_khach) và chứa `tich_hop@<id trong đường
  dẫn>`: «người dùng chọn module nào được dùng khoá» = các phần tử đó. Không dựa
  vào phạm vi hiệu lực (khách có thể thu hẹp bằng header) và KHÔNG dùng
  khop_pham_vi/pham_vi_nam_trong_tran (để «*» và «*@m» không lọt). Thiếu header
  Bearer hoặc khoá sai/đã thu hồi → 401; khoá quản trị, khoá «*», khoá của module
  khác hoặc khoá hợp lệ nhưng module không nằm trong phạm vi → 403 (cùng quy ước
  NV281); module không chạy/không khai đường → 404; khoá sai nhiều → 429.
  Thân POST tối đa 400_000 byte (trần lớn nhất của dịch vụ cũ, cho playlists
  import); JSON hỏng → 400 invalid_request.
- ĐƯỜNG LUỒNG MỞ (không Bearer — loa không gửi được header, như dịch vụ
  cũ): xác thực bằng VÉ KÝ có hạn, DÙNG LẠI app/url_ky.py của lõi (HMAC
  phủ phương thức GET + đường dẫn + hạn; KHÔNG mang cơ chế HMAC riêng của
  dịch vụ cũ vào lõi). Vé giả/hết hạn/sai đường → 404 TRỐNG, không nêu lý
  do (không lộ module nào tồn tại hay vé nào sai ở đâu). Vé do module tạo
  qua tao_duong_luong() khi trả POST integration/stream, hạn mặc định
  3600 giây. HEAD dùng cùng vé GET (loa gọi HEAD cùng URL trước khi GET).
- GIỚI HẠN LAN: cả hai nhóm đường chỉ chấp nhận nguồn LAN, xem `_la_lan`:
  peer là loopback/IP riêng/link-local và KHÔNG kèm header chuyển tiếp
  (X-Forwarded-For, CF-Connecting-IP, Forwarded, X-Real-IP) → LAN thật. Có header
  chuyển tiếp mà peer không phải proxy lõi tin (loopback hoặc CIDR trong
  WEB2API_TRUST_PROXY, cơ chế tin cậy proxy có sẵn của app/gioi_han.py) → ĐÓNG:
  lõi không biết header nào là thật, và đường hầm cloudflared tới từ IP gateway
  Docker (IP riêng) chính là ca này. Peer tin cậy thì khách thật (duyệt XFF từ
  phải sang trái) và CF-Connecting-IP đều phải là IP riêng. Nguồn Internet →
  404 trống (đường luồng) hoặc 403 (integration). Chỉ trong test, khi
  WEB2API_TEST_CHO_PHEP_LOOPBACK=1, peer không phân tích được (TestClient) mới
  qua.
- KHÔNG có tên nhà cung cấp nào trong tệp này: mọi logic đặc thù nằm ở
  module, lõi chỉ chuyển tiếp (mã, headers, nội dung) và kiểm xác thực.
- Handler KHÔNG nuốt lỗi âm thầm (test nv45): mọi except Exception đều
  ghi một dòng log chỉ nêu kiểu lỗi (type(e).__name__), rồi trả 502 chung.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app import dia_chi, khoa_khach, xac_thuc
from app import gioi_han as gioi_han_mod
from app import url_ky as url_ky_mod
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.duong_module")

router = APIRouter(prefix="/m")

#: Trần thân POST integration (byte) — bằng trần lớn nhất của dịch vụ cũ.
TRAN_THAN = 400_000
#: Hạn vé luồng mặc định (giây) — bằng hạn stream_url của dịch vụ cũ.
HAN_VE_MAC_DINH_GIAY = 3600
#: Khả năng của khoá khách để gọi vào đường riêng của module; luôn đi kèm
#: «@<module>» (khoa_khach.la_ma_pham_vi). Là khả năng NGUY HIỂM (kham_pha) nên
#: khoá «*» và trần «*» của người dùng không bao gồm nó.
MA_TICH_HOP = "tich_hop"


def tao_duong_luong(module_id: str, ve: str, goc: str = "",
                    het_han_giay: int = HAN_VE_MAC_DINH_GIAY) -> str:
    """Đường luồng đã ký cho loa: <goc>/m/<id>/api/stream/<ve>?_het_han&_ky.

    Module gọi hàm này khi trả POST integration/stream. `goc` là origin do lõi
    truyền vào `tich_hop` (loa cần URL TUYỆT ĐỐI, tới được qua LAN); rỗng thì
    chỉ trả đường dẫn. Chỉ ký đường dẫn, không nhúng khoá hay bí mật vào URL.
    """
    duong = f"/m/{module_id}/api/stream/{ve}"
    return goc.rstrip("/") + url_ky_mod.ky_url(duong, het_han_giay=het_han_giay,
                                               phuong_thuc="GET")


def _lay_so(request: Request):
    return getattr(request.app.state, "so", None)


def _lay_module(so, id_: str):
    """(thể hiện, manifest) hoặc (None, None) — chịu cả catalog dạng object/dict."""
    if so is None:
        return None, None
    dang_chay = getattr(so, "dang_chay", {}) or {}
    the = dang_chay.get(id_)
    if the is None:
        return None, None
    catalog = getattr(so, "catalog", {}) or {}
    km = catalog.get(id_)
    if km is None:
        man = getattr(the, "manifest", {}) or {}
    elif isinstance(km, dict):
        man = km.get("manifest", {}) or {}
    else:
        man = getattr(km, "manifest", {}) or {}
    return the, man


def _khai_duong(man: dict) -> dict | None:
    khai = man.get("duong_module") if isinstance(man, dict) else None
    return khai if isinstance(khai, dict) else None


def _ip_la_noi_bo(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        dia_chi_ip = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    if isinstance(dia_chi_ip, ipaddress.IPv6Address) and dia_chi_ip.ipv4_mapped is not None:
        dia_chi_ip = dia_chi_ip.ipv4_mapped
    return bool(dia_chi_ip.is_private or dia_chi_ip.is_loopback or dia_chi_ip.is_link_local)


#: Header mà proxy/đường hầm gắn vào khi chuyển tiếp yêu cầu của người khác. Khách LAN
#: nối thẳng không bao giờ gửi chúng. Là bốn tên chuẩn của giao thức, không phải
#: danh sách đoán từ khoá: có bất kỳ cái nào nghĩa là «yêu cầu này đã qua trung gian».
_HEADER_CHUYEN_TIEP = ("x-forwarded-for", "cf-connecting-ip", "forwarded", "x-real-ip")


def _la_lan(request: Request) -> bool:
    """Nguồn có phải LAN không (xem docstring đầu tệp). Đóng khi không chắc.

    Đường hầm (cloudflared) và proxy ngược nối vào lõi từ IP riêng của gateway
    Docker, nên chỉ nhìn peer là sai. Quy tắc: yêu cầu có header chuyển tiếp mà
    peer không phải proxy lõi tin (WEB2API_TRUST_PROXY, loopback) thì KHÔNG
    phải LAN — khách Internet tự gõ XFF=IP riêng cũng không qua được.
    """
    client = getattr(request, "client", None)
    host = client.host if client else ""
    try:
        peer = ipaddress.ip_address(host)
    except ValueError:
        # TestClient (peer «testclient» không phân tích được): chỉ cho qua khi
        # test đã bật cờ loopback, đúng quy ước test của kho.
        return os.environ.get("WEB2API_TEST_CHO_PHEP_LOOPBACK", "") == "1"
    xff = request.headers.get("x-forwarded-for")
    cf = request.headers.get("cf-connecting-ip")
    if not any(request.headers.get(h) is not None for h in _HEADER_CHUYEN_TIEP):
        return _ip_la_noi_bo(host)
    if not (peer.is_loopback or gioi_han_mod.la_proxy_tin_cay(host)):
        return False  # không biết header nào là thật: đóng
    if xff is None and cf is None:
        return False  # chỉ có Forwarded/X-Real-IP: lõi không phân tích, đóng
    if xff is not None:
        ip, loai = gioi_han_mod.ip_hieu_luc_phan_loai(host, xff)
        if loai != "ip" or not _ip_la_noi_bo(ip):
            return False
    return cf is None or _ip_la_noi_bo(cf)


def _loi_json(ma: int, loi: str) -> JSONResponse:
    return JSONResponse(status_code=ma, content={"error": loi},
                        headers={"Cache-Control": "no-store"})


def _tra_ve(ma: int, headers: dict, noi_dung):
    """Chuyển (mã, headers, nội dung) của module thành Response của lõi.

    dict → JSON; bytes → Response; iterator → StreamingResponse (bỏ
    content-length/content-encoding do module khai, vì lõi stream từng
    khúc — đúng lưu ý trong app/kham_pha.py).
    """
    headers = {str(k): str(v) for k, v in (headers or {}).items()}
    headers.setdefault("Cache-Control", "no-store")
    if isinstance(noi_dung, dict):
        return JSONResponse(status_code=ma, content=noi_dung, headers=headers)
    if isinstance(noi_dung, (bytes, bytearray)):
        return Response(status_code=ma, content=bytes(noi_dung), headers=headers)
    if hasattr(noi_dung, "__aiter__"):
        headers.pop("content-length", None)
        headers.pop("Content-Length", None)
        headers.pop("content-encoding", None)
        headers.pop("Content-Encoding", None)
        return StreamingResponse(noi_dung, status_code=ma, headers=headers)
    return Response(status_code=ma, content=str(noi_dung).encode("utf-8"), headers=headers)


async def _kiem_tra_lan_va_module(request: Request, id_: str, *, mo: bool):
    """Trả (thể hiện, manifest, phản_hồi_lỗi) — phản_hồi_lỗi None khi qua."""
    if not _la_lan(request):
        # Đường luồng: 404 trống để không lộ lý do; integration: 403 rõ.
        return None, None, (Response(status_code=404) if mo else _loi_json(403, "forbidden"))
    so = _lay_so(request)
    the, man = _lay_module(so, id_)
    khai = _khai_duong(man)
    if the is None or khai is None:
        return None, None, (Response(status_code=404) if mo else _loi_json(404, "not_found"))
    return the, man, None


@router.get("/{id_}/api/stream/{ve}")
@router.head("/{id_}/api/stream/{ve}")
async def luong_cua_module(id_: str, ve: str, request: Request):
    # LƯU Ý: route này đăng ký TRƯỚC route integration (xem main.py) nên
    # /m/<id>/api/stream/<vé> không rơi vào handler integration.
    # Vé ký cho GET; HEAD dùng cùng vé (loa gọi HEAD cùng URL trước GET).
    the, man, loi = await _kiem_tra_lan_va_module(request, id_, mo=True)
    if loi is not None:
        return loi
    duong = f"/m/{id_}/api/stream/{ve}"
    # query_params của Starlette không phải dict (kiem_chu_ky chỉ nhận
    # dict/str/list) nên chuyển sang dict trước khi kiểm.
    if not url_ky_mod.kiem_chu_ky("GET", duong, dict(request.query_params)):
        return Response(status_code=404)
    if not hasattr(the, "luong"):
        return Response(status_code=404)
    try:
        ma, headers, noi_dung = await the.luong(ve, request.headers.get("range"),
                                                request.method)
    except Exception as e:
        log.warning("Module %s lỗi khi phục vụ luồng: %s", id_, type(e).__name__)
        return Response(status_code=404)
    if ma == 404:
        return Response(status_code=404)
    if request.method == "HEAD":
        # HEAD chỉ lấy header, không đẩy byte: đóng iterator của module
        # ngay nếu có, rồi trả header với thân rỗng.
        if hasattr(noi_dung, "__aiter__"):
            try:
                await noi_dung.aclose()
            except Exception as e:
                log.warning("Đóng iterator luồng HEAD lỗi: %s", type(e).__name__)
        return Response(status_code=ma, content=b"",
                        headers={str(k): str(v) for k, v in (headers or {}).items()})
    return _tra_ve(ma, headers, noi_dung)


async def _kiem_khoa(request: Request, id_: str) -> JSONResponse | None:
    """Kiểm khoá khách cho đường integration của module `id_`. None = qua.

    Cùng khuôn cổng MCP (app/cong_mcp.py): Bearer bắt buộc (không nhận cookie
    phiên), danh tính phải là khoá khách (khoá quản trị bị từ chối), xét phạm vi
    ĐÃ LƯU của khoá. Chuỗi khoá không bao giờ vào log hay phản hồi.
    """
    loai_auth, _, gia_tri = request.headers.get("authorization", "").partition(" ")
    if loai_auth.lower() != "bearer" or not gia_tri.strip():
        return _loi_json(401, "invalid_auth")
    try:
        danh_tinh = await xac_thuc.nguoi_goi(request)
    except LoiOpenAI as e:
        if e.ma in (403, 429, 503):
            return JSONResponse(status_code=e.ma, content={"error": "forbidden" if e.ma == 403
                                else "rate_limited" if e.ma == 429 else "unavailable"},
                                headers={"Cache-Control": "no-store", **dict(e.headers or {})})
        return _loi_json(401, "invalid_auth")
    if danh_tinh.get("loai") != "khach":
        return _loi_json(403, "forbidden")
    khach = khoa_khach.tim_theo_khoa_tho(gia_tri.strip())
    pham_vi = list((khach or {}).get("pham_vi") or [])
    tien_to = MA_TICH_HOP + "@"
    if (not pham_vi or not all(isinstance(p, str) and p.startswith(tien_to) for p in pham_vi)
            or tien_to + id_ not in pham_vi):
        return _loi_json(403, "forbidden")
    return None


@router.api_route("/{id_}/api/{duong:path}", methods=["GET", "POST"])
async def tich_hop_cua_module(id_: str, duong: str, request: Request):
    the, man, loi = await _kiem_tra_lan_va_module(request, id_, mo=False)
    if loi is not None:
        return loi
    loi = await _kiem_khoa(request, id_)
    if loi is not None:
        return loi
    if not hasattr(the, "tich_hop"):
        return _loi_json(404, "not_found")
    # Trần thân + JSON.
    than_tho = await request.body()
    if len(than_tho) > TRAN_THAN:
        return _loi_json(400, "invalid_request")
    than = None
    if than_tho:
        try:
            than = json.loads(than_tho.decode("utf-8"))
        except (ValueError, RecursionError):
            return _loi_json(400, "invalid_request")
        if not isinstance(than, dict):
            return _loi_json(400, "invalid_request")
    try:
        ma, headers, noi_dung = await the.tich_hop(
            duong, request.method, dict(request.query_params), than,
            dia_chi.origin_cua_may_chu(request))
    except Exception as e:
        log.warning("Module %s lỗi khi xử lý integration: %s", id_, type(e).__name__)
        return _loi_json(502, "module_error")
    return _tra_ve(ma, headers, noi_dung)
