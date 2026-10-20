"""Module nhaptaikhoan — nhập hàng loạt tài khoản từ dịch vụ khác (PHẦN ĐỌC/XEM TRƯỚC).

NV164: kho tài khoản/danh tính của lõi (NV144, NV72) CHƯA gộp, nên module này
chỉ làm phần phân tích + xem trước và khai một giao diện «áp dụng» rõ ràng:

- `xem_truoc_tep`: phân tích tệp sao lưu 9router (JSON hoặc JSON.gz) do quản trị
  gửi; tệp là DỮ LIỆU KHÔNG TIN (trần kích thước từng lớp, gzip bung có trần,
  JSON thuần — không YAML tuỳ ý, không pickle).
- `xem_truoc_may_chu`: đọc danh sách qua API quản trị của máy chủ sub2api
  (đăng nhập JWT hoặc x-api-key) hoặc CLIProxyAPI/CPA (Bearer secret, CHỈ liệt
  kê tệp auth — không tải token).
- `ap_dung(danh_sach_da_duyet)`: hàm giao diện NHẬN danh sách đã duyệt và
  GHI vào kho tài khoản của lõi qua `app.kho_tai_khoan.nhap_nhieu_tai_khoan`
  (NV197e: cùng kho, không kho thứ hai; khử trùng theo (nguồn, định danh),
  ghi nguyên tử, báo cáo từng dòng không lộ bí mật).

An toàn:
- CHỈ quản trị: mọi hành động kiểm danh tính người gọi qua ngữ cảnh lõi
  (app.ngu_canh, SEC-002 — giao diện app.* chỉ đọc thứ hai được phép của module
  này, bên cạnh app.tu_xa) và ĐÓNG KHI LỖI (thiếu ngữ cảnh cũng từ chối).
  Hai thực thể TÁCH theo mức rủi ro (lõi tính rủi ro theo thực thể):
  «xem_truoc» chỉ đọc/xem trước (`rui_ro_mac_dinh: "doc"`, mọi hành động vẫn
  qua cổng quản trị nội bộ của module) và «ap_dung» là đường ghi tương lai
  (`rui_ro_mac_dinh: "nguy_hiem"`); hành động chỉ chạy dưới thực thể của nó.
- Đường máy chủ (NV164-SUA): địa chỉ kiểm bằng `app/tu_xa.kiem_url_lan` rồi nối
  qua client GHIM IP đã kiểm (`TransportGhimIp` của lõi) — chống DNS rebinding;
  thân phản hồi máy chủ nguồn đọc có trần 2 MB.
- Bí mật (token/cookie/mật khẩu/khoá API) chỉ hiện DẠNG CHE: khoá `…abcd`,
  email `a***@mien`; không vào log, không vào phản hồi lỗi (lỗi luôn là mã ngắn
  của module, không chép thân phản hồi hay URL chứa thông tin đăng nhập).
- Địa chỉ máy chủ do quản trị khai được kiểm bằng đúng `app/tu_xa.kiem_url_lan`
  (chống SSRF: chặn loopback/link-local/không định tuyến; LAN riêng được phép)
  ngay trước khi nối — module không viết bộ kiểm thứ hai.
- Một logging.Filter theo CẤU TRÚC chặn mọi dòng log của thư viện HTTP mang
  «Authorization … Bearer» (khuôn zalo-bot/van-phong, vá theo bài học NV116:
  mỗi bản nạp chỉ gỡ bộ lọc CỦA CHÍNH MÌNH, giữ bộ lọc của các bản khác).

Nguồn chép (LUẬT PORT — c2a là kho của chính chủ máy, commit d879b03,
đường /opt/claude-c2a/chatgpt2api, CHỈ ĐỌC):
- services/ninerouter_backup_import.py: `detect_9router_backup`,
  `extract_chatgpt_tokens`, `extract_all_oauth_tokens`, `extract_combos`
  (chỉ nhận diện + trích; phần GHI vào kho của c2a không chép).
- services/sub2api_service.py: `_clean`, `_unwrap_envelope`,
  `_extract_paged_items`, `_extract_access_token`, đăng nhập JWT và liệt kê
  tài khoản phân trang (curl_cffi → httpx theo `yeu_cau_goi` của lõi).
- services/cpa_service.py: `_management_headers`, `list_remote_files`
  (chỉ liệt kê; `fetch_remote_access_token` thuộc phía áp dụng — không chép).

Không chép: kho đăng ký máy chủ của c2a (module không giữ trạng thái — quản trị
khai theo từng lần xem trước, không lưu khoá của máy chủ lạ vào đĩa) và mọi
đường ghi (`account_service.add_accounts`, `state_backup.import_all`): kho đích
chưa có, chép sang sẽ thành ghi vào kho của c2a — sai phạm vi NV164.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import json
import logging
import zlib

import httpx

log = logging.getLogger(__name__)

#: Trần chuỗi base64 của tệp nhập (ký tự). Khớp TRẦN 8 KB của lõi: lõi tính
#: toàn bộ `tham_so` tuần tự hoá JSON ≤ 8192 byte trước khi gọi module
#: (app/thiet_bi.py `kiem_tra_tham_so`) — lược đồ khai maxLength lớn hơn trần
#: của lõi là hứa sai: tệp ~100 kết nối 9router (13.356 ký tự base64) bị lõi
#: trả 400 mơ hồ trong khi module hứa 8 MB (phát hiện (d) của soát độc lập).
#: 8000 ký tự base64 ≈ 6 KB JSON thô — vẫn đủ phần lớn tệp sao lưu thông thường.
TRAN_BASE64 = 8000
#: Trần tệp sau giải mã base64 (byte).
TRAN_TEP = 6 * 1024 * 1024
#: Trần nội dung sau khi bung gzip (byte) — chống bom nén.
TRAN_GZIP_BUNG = 10 * 1024 * 1024
#: Trần số mục trong một lần áp dụng (trần tham số 8 KB của lõi: token dài
#: ~vài trăm byte — 20 mục là mức an toàn; nhiều hơn thì chia nhiều lần gọi).
TRAN_AP_DUNG = 20
#: Trần số mục trong một bản xem trước.
TRAN_MUC_XEM_TRUOC = 2000
#: Trần số trang khi liệt kê tài khoản sub2api (trang 200 mục ⇒ tối đa 1000 tài khoản).
TRAN_SO_TRANG = 5
KICH_THUOC_TRANG = 200
HET_GIO_GOI = 30
#: Trần thân phản hồi của máy chủ nguồn (byte) — liệt kê 200 tài khoản/trang chỉ
#: cần vài trăm KB; máy chủ nguồn ĐỘC trả thân khổng lồ không được nhận hết vào
#: RAM của lõi (phát hiện B4 của soát độc lập: 60 MB được nhận đủ).
_TRAN_PHAN_HOI_MAY_CHU = 2 * 1024 * 1024

#: Provider của 9router mà token dùng được như tài khoản ChatGPT (đúng c2a).
_NHA_CUNG_CAP_NHAP_DUOC = ("codex", "cursor", "openai")


# ───────── Che bí mật ─────────

def _che_email(email) -> str:
    """a***@mien — giữ 1 ký tự đầu phần tên và tên miền; không email → «***»."""
    s = str(email or "").strip()
    if "@" not in s or s.startswith("@"):
        return "***"
    ten, _, mien = s.partition("@")
    if not ten or not mien:
        return "***"
    return f"{ten[0]}***@{mien}"


def _che_khoa(khoa) -> str:
    """…abcd (khoá ngắn hơn 4 ký tự → «…»)."""
    s = str(khoa or "")
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


def _lam_sach_chu(x, toi_da: int = 80) -> str:
    """Bỏ ký tự điều khiển/ngắt dòng và cắt ở trần — dữ liệu ngoài là không tin."""
    s = str(x or "")
    s = "".join(c for c in s if c.isprintable())
    return s.strip()[:toi_da]


def _che_ten(ten_tho) -> str:
    """B3: `ten` từ nguồn ngoài có DẠNG EMAIL thì che như email_che — tên kết nối
    9router/tên tài khoản thường CHÍNH là email, để nguyên là lộ (soát độc lập B3).
    Tên thường (không có «@») giữ nguyên."""
    ten = _lam_sach_chu(ten_tho)
    if "@" in ten:
        return _che_email(ten)
    return ten


# ───────── Bộ lọc log (khuôn zalo-bot/van-phong, vá bài học NV116) ─────────

_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy")


class _ChanBiMatHTTP(logging.Filter):
    """Bỏ dòng log của thư viện HTTP mang mẫu «Authorization … Bearer».

    Chặn theo CẤU TRÚC, không theo giá trị khoá cụ thể: mọi dòng như vậy đều là
    dòng mang bí mật xác thực. Khoá/mật khẩu của module này đi trong header
    Authorization/x-api-key và thân đăng nhập — không bao giờ là một phần của
    URL mà httpx ghi log."""

    _nhaptaikhoan_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        thap = noi_dung.lower()
        return not ("authorization" in thap and "bearer" in thap)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        # Bài học NV116: chỉ gỡ bộ lọc của CHÍNH bản này (đúng __name__ của bản
        # đang nạp), giữ nguyên bộ lọc của các bản nạp khác — gỡ nhầm bản khác
        # là để khoá của bản đó lọt log.
        for f in [f for f in lg.filters
                  if getattr(f, "_nhaptaikhoan_loc", False) and type(f).__module__ == __name__]:
            lg.removeFilter(f)
        lg.addFilter(_ChanBiMatHTTP())


_cai_bo_loc_log()


def _nguoi_goi_la_quan_tri() -> bool:
    """Người gọi hiện tại có phải quản trị không (đóng khi lỗi).

    Đọc ngữ cảnh gọi lõi đặt cho mỗi lời gọi (app.ngu_canh, SEC-002 — kênh CHỈ
    ĐỌC), đúng định nghĩa `la_quan_tri` của lõi (app/thiet_bi.py): danh tính
    «quan_tri» hoặc «noi_bo». Thiếu ngữ cảnh hoặc lỗi khi đọc → KHÔNG."""
    try:
        from app.ngu_canh import lay  # chỉ đọc, không gọi sâu vào lõi

        nc = lay()
    except Exception:
        return False
    if nc is None:
        return False
    return str((nc.danh_tinh or {}).get("loai") or "") in ("quan_tri", "noi_bo")


# ───────── Kết nối chống DNS rebinding + đọc phản hồi có trần ─────────

def _mo_client() -> httpx.AsyncClient:
    """Client HTTP ghim IP đã kiểm — chống DNS rebinding (phát hiện (c)).

    `kiem_url_lan` phân giải và kiểm một lần TRƯỚC khi nối; httpx phân giải lần
    nữa LÚC NỐI — tên miền đổi đáp án giữa hai lần (rebind.example: 192.168.1.50
    → 127.0.0.1) là vượt kiểm SSRF. Dùng đúng cách ghim của lõi (khuôn
    app/tu_xa.py: `TransportGhimIp(_dia_chi_bi_chan)`): mỗi yêu cầu phân giải
    lại NGAY TRƯỚC khi gửi, kiểm MỌI IP bằng bộ kiểm SSRF chung của lõi, nối
    thẳng tới IP đã kiểm; IP bị cấm → `LoiDichBiChan` (là `ConnectError`, module
    đã bắt `httpx.HTTPError` → «khong_noi_duoc_may_chu»)."""
    from app.chuyen_tiep import TransportGhimIp  # bộ ghim của lõi, khuôn app/tu_xa.py
    from app.tu_xa import _dia_chi_bi_chan  # bộ kiểm SSRF chung của lõi

    return httpx.AsyncClient(transport=TransportGhimIp(_dia_chi_bi_chan),
                             timeout=HET_GIO_GOI, verify=True)


class _PhanHoiQuaLon(Exception):
    """Thân phản hồi của máy chủ nguồn vượt trần (B4) — không kèm chi tiết thô."""


async def _doc_json(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    """Gửi một yêu cầu và đọc JSON với trần cứng (B4).

    Trả (status_code, du_lieu): du_lieu None khi thân không phải JSON. Vượt trần
    → ném `_PhanHoiQuaLon`; lỗi mạng → `httpx.HTTPError` như trước (người gọi
    đã bắt)."""
    from app.chuyen_tiep import PhanHoiQuaLon, doc_phan_hoi_co_tran

    req = client.build_request(method, url, **kwargs)
    r = await client.send(req, stream=True)
    try:
        tho = await doc_phan_hoi_co_tran(r, tran_bytes=_TRAN_PHAN_HOI_MAY_CHU)
    except PhanHoiQuaLon:
        raise _PhanHoiQuaLon from None
    try:
        return r.status_code, json.loads(tho.decode("utf-8"))
    except Exception:
        return r.status_code, None


# ───────── 9router (chép từ c2a services/ninerouter_backup_import.py @ d879b03) ─────────

def _sach(value) -> str:
    return str(value or "").strip()


def detect_9router_backup(data) -> bool:
    """Chép c2a: JSON có mảng `providerConnections` (trên cùng hoặc lồng trong `data`)."""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("providerConnections"), list):
        return True
    inner = data.get("data") or {}
    if isinstance(inner, dict) and isinstance(inner.get("providerConnections"), list):
        return True
    return False


def _ket_noi_cua(data: dict) -> list:
    connections = data.get("providerConnections") or []
    if not connections:
        inner = data.get("data") or {}
        connections = inner.get("providerConnections") or []
    return connections if isinstance(connections, list) else []


def _token_ket_noi(conn: dict) -> str:
    """accessToken ở cấp ngoài hoặc lồng trong `data` (đúng c2a)."""
    access_token = str(conn.get("accessToken") or "")
    if not access_token:
        conn_data = conn.get("data") or {}
        if isinstance(conn_data, dict):
            access_token = str(conn_data.get("accessToken") or "")
    return access_token.strip()


def _truong_ket_noi(conn: dict, khoa: str) -> str:
    """Trường ở cấp ngoài hoặc lồng trong `data` (email/refreshToken/expiresAt)."""
    gia_tri = str(conn.get(khoa) or "")
    if not gia_tri:
        conn_data = conn.get("data") or {}
        if isinstance(conn_data, dict):
            gia_tri = str(conn_data.get(khoa) or "")
    return gia_tri.strip()


def extract_all_oauth_tokens(data: dict) -> list[dict]:
    """Chép c2a: mọi kết nối OAuth {provider, name, access_token, refresh_token, expires_at}."""
    ra: list[dict] = []
    for conn in _ket_noi_cua(data):
        if not isinstance(conn, dict):
            continue
        access_token = _token_ket_noi(conn)
        if not access_token:
            continue
        ra.append({
            "provider": str(conn.get("provider") or "").strip().lower(),
            "name": str(conn.get("connectionName") or conn.get("name")
                        or conn.get("id") or conn.get("provider") or ""),
            "email": _truong_ket_noi(conn, "email"),
            "access_token": access_token,
            "refresh_token": _truong_ket_noi(conn, "refreshToken") or None,
            "expires_at": _truong_ket_noi(conn, "expiresAt") or None,
        })
    return ra


def extract_chatgpt_tokens(data: dict) -> list[str]:
    """Chép c2a: token ChatGPT dùng được — provider codex/cursor/openai, bắt đầu «eyJ»."""
    tokens: list[str] = []
    for conn in _ket_noi_cua(data):
        if not isinstance(conn, dict):
            continue
        provider = str(conn.get("provider") or "").strip().lower()
        access_token = _token_ket_noi(conn)
        if provider in _NHA_CUNG_CAP_NHAP_DUOC and access_token.startswith("eyJ"):
            tokens.append(access_token)
    return tokens


def extract_combos(data: dict) -> dict:
    """Chép c2a: combo {tên: [model]}."""
    combos = data.get("combos") or []
    if not combos and isinstance(data.get("data"), dict):
        combos = data["data"].get("combos") or []
    if not isinstance(combos, list):
        return {}
    ket_qua: dict = {}
    for combo in combos:
        if not isinstance(combo, dict):
            continue
        name = str(combo.get("name") or "").strip()
        models = combo.get("models") or []
        if name and isinstance(models, list):
            ket_qua[name] = [str(m) for m in models if m]
    return ket_qua


def _xem_truoc_9router(data: dict) -> dict:
    """Dựng bản xem trước ĐÃ CHE từ dữ liệu 9router đã phân tích."""
    tat_ca = extract_all_oauth_tokens(data)
    muc: list[dict] = []
    for t in tat_ca[:TRAN_MUC_XEM_TRUOC]:
        muc.append({
            "ten": _che_ten(t["name"]),
            "nha_cung_cap": _lam_sach_chu(t["provider"], 40),
            "co_the_nhap": t["provider"] in _NHA_CUNG_CAP_NHAP_DUOC
                           and t["access_token"].startswith("eyJ"),
            "email_che": _che_email(t["email"]) if t["email"] else None,
            "khoa_che": _che_khoa(t["access_token"]),
            "co_refresh": bool(t["refresh_token"]),
            "het_han": _lam_sach_chu(t["expires_at"]) if t["expires_at"] else None,
        })
    return {
        "ok": True,
        "nguon": "ninerouter",
        "tong": len(_ket_noi_cua(data)),
        "co_token": len(tat_ca),
        "tai_khoan": muc,
        "so_combo": len(extract_combos(data)),
        "nguon_ngoai": True,
    }


# ───────── sub2api (chép từ c2a services/sub2api_service.py @ d879b03) ─────────

class _SaiDangNhap(Exception):
    """Máy chủ nguồn từ chối thông tin đăng nhập (401/403)."""


def _unwrap_envelope(payload):
    """Chép c2a: bóc phong bì `{code, message, data}` của sub2api."""
    if isinstance(payload, dict) and "data" in payload and "code" in payload:
        return payload.get("data")
    return payload


def _extract_paged_items(payload) -> tuple[list, int]:
    """Chép c2a: (mục, tổng) từ phản hồi phân trang của sub2api."""
    inner = _unwrap_envelope(payload)
    if isinstance(inner, list):
        return inner, len(inner)
    if isinstance(inner, dict):
        for key in ("items", "data", "list"):
            value = inner.get(key)
            if isinstance(value, list):
                return value, int(inner.get("total") or len(value))
    return [], 0


def _extract_access_token(credentials) -> str:
    """Chép c2a: access_token / accessToken / token trong credentials."""
    if not isinstance(credentials, dict):
        return ""
    for key in ("access_token", "accessToken", "token"):
        value = _sach(credentials.get(key))
        if value:
            return value
    return ""


async def _dang_nhap_sub2api(client: httpx.AsyncClient, goc: str, email: str, mat_khau: str) -> str:
    """Chép c2a `_login`: POST /api/v1/auth/login → JWT. Không ghi log thân."""
    try:
        status, than = await _doc_json(
            client, "POST", f"{goc}/api/v1/auth/login",
            json={"email": email, "password": mat_khau},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        raise _LoiMayChu from None
    except _PhanHoiQuaLon:
        raise _LoiMayChu from None
    if status in (401, 403):
        raise _SaiDangNhap
    if status != 200:
        raise _LoiMayChu
    than = _unwrap_envelope(than)
    token = _sach(than.get("access_token")) if isinstance(than, dict) else ""
    if not token:
        raise _LoiMayChu
    return token


class _LoiMayChu(Exception):
    """Không nối được / phản hồi lạ từ máy chủ nguồn (không kèm chi tiết thô)."""


async def _xem_truoc_sub2api(url: str, tham_so: dict) -> dict:
    khoa_api = _sach(tham_so.get("khoa_api"))
    email = _sach(tham_so.get("email"))
    mat_khau = _sach(tham_so.get("mat_khau"))
    nhom = _sach(tham_so.get("nhom"))
    if not khoa_api and not (email and mat_khau):
        return {"ok": False, "loi": "thieu_xac_thuc"}
    goc = url.rstrip("/")
    async with _mo_client() as client:
        if khoa_api:
            headers = {"x-api-key": khoa_api, "Accept": "application/json"}
        else:
            try:
                token = await _dang_nhap_sub2api(client, goc, email, mat_khau)
            except _SaiDangNhap:
                return {"ok": False, "loi": "sai_dang_nhap"}
            except _LoiMayChu:
                return {"ok": False, "loi": "khong_noi_duoc_may_chu"}
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        muc: list[dict] = []
        try:
            for trang in range(1, TRAN_SO_TRANG + 1):
                params: dict = {"platform": "openai", "type": "oauth",
                                "page": trang, "page_size": KICH_THUOC_TRANG}
                if nhom:
                    params["group"] = nhom
                status, payload = await _doc_json(client, "GET", f"{goc}/api/v1/admin/accounts",
                                                  headers=headers, params=params)
                if status in (401, 403):
                    return {"ok": False, "loi": "sai_dang_nhap"}
                if status != 200:
                    return {"ok": False, "loi": "khong_noi_duoc_may_chu"}
                if payload is None:
                    return {"ok": False, "loi": "phan_hoi_sai"}
                data, tong = _extract_paged_items(payload)
                for account in data:
                    if not isinstance(account, dict):
                        continue
                    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
                    if not _extract_access_token(credentials):
                        continue
                    ma = account.get("id")
                    email_tk = _sach(credentials.get("email")) or _sach(account.get("name"))
                    muc.append({
                        "id": _lam_sach_chu(str(ma)) if ma is not None else _lam_sach_chu(_sach(credentials.get("chatgpt_account_id"))),
                        "ten": _che_ten(account.get("name")),
                        "email_che": _che_email(email_tk) if email_tk else None,
                        "goi": _lam_sach_chu(credentials.get("plan_type")),
                        "trang_thai": _lam_sach_chu(account.get("status")),
                        "co_refresh": bool(_sach(credentials.get("refresh_token"))),
                        "het_han": _lam_sach_chu(_sach(credentials.get("expires_at"))) or None,
                    })
                    if len(muc) >= TRAN_MUC_XEM_TRUOC:
                        break
                if len(muc) >= TRAN_MUC_XEM_TRUOC or not data \
                        or trang * KICH_THUOC_TRANG >= tong or len(data) < KICH_THUOC_TRANG:
                    break
        except _PhanHoiQuaLon:
            return {"ok": False, "loi": "phan_hoi_qua_lon"}
        except httpx.HTTPError:
            return {"ok": False, "loi": "khong_noi_duoc_may_chu"}
    return {"ok": True, "nguon": "sub2api", "tong": len(muc), "tai_khoan": muc,
            "nguon_ngoai": True}


# ───────── CPA / CLIProxyAPI (chép từ c2a services/cpa_service.py @ d879b03) ─────────

def _management_headers(secret_key: str) -> dict:
    """Chép c2a: header quản trị của CPA."""
    return {"Authorization": f"Bearer {secret_key}", "Accept": "application/json"}


async def _xem_truoc_cpa(url: str, tham_so: dict) -> dict:
    khoa = _sach(tham_so.get("khoa_api"))
    if not khoa:
        return {"ok": False, "loi": "thieu_xac_thuc"}
    goc = url.rstrip("/")
    try:
        async with _mo_client() as client:
            status, payload = await _doc_json(client, "GET", f"{goc}/v0/management/auth-files",
                                              headers=_management_headers(khoa))
    except _PhanHoiQuaLon:
        return {"ok": False, "loi": "phan_hoi_qua_lon"}
    except httpx.HTTPError:
        return {"ok": False, "loi": "khong_noi_duoc_may_chu"}
    if status in (401, 403):
        return {"ok": False, "loi": "sai_dang_nhap"}
    if status != 200:
        return {"ok": False, "loi": "khong_noi_duoc_may_chu"}
    if payload is None:
        return {"ok": False, "loi": "phan_hoi_sai"}
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return {"ok": False, "loi": "phan_hoi_sai"}
    muc: list[dict] = []
    for item in files[:TRAN_MUC_XEM_TRUOC]:
        if not isinstance(item, dict):
            continue
        name = _sach(item.get("name"))
        if not name:
            continue
        email = _sach(item.get("email") or item.get("account"))
        muc.append({"ten": _che_ten(name), "email_che": _che_email(email) if email else None})
    return {"ok": True, "nguon": "cpa", "tong": len(muc), "tai_khoan": muc,
            "nguon_ngoai": True}


# ───────── Phân tích tệp (dữ liệu không tin) ─────────

def _bung_gzip_co_tran(tho: bytes) -> bytes:
    """Bung gzip với trần cứng — bom nén bị từ chối thay vì bung hết vào RAM."""
    bo = zlib.decompressobj(31)  # wbits 31 = gzip
    ra = bytearray()
    con_lai = tho
    while con_lai:
        khuc = bo.decompress(con_lai, TRAN_GZIP_BUNG + 1 - len(ra))
        ra += khuc
        if len(ra) > TRAN_GZIP_BUNG:
            raise ValueError("tep_qua_lon")
        con_lai = bo.unconsumed_tail
        if not khuc and not con_lai:
            break
    ra += bo.flush()
    if len(ra) > TRAN_GZIP_BUNG:
        raise ValueError("tep_qua_lon")
    return bytes(ra)


def _xem_truoc_tep(tham_so: dict) -> dict:
    dang = str(tham_so.get("dang") or "").strip()
    if dang != "ninerouter":
        return {"ok": False, "loi": "dinh_dang_khong_ho_tro"}
    noi_dung = tham_so.get("noi_dung_base64")
    if not isinstance(noi_dung, str) or not noi_dung.strip():
        return {"ok": False, "loi": "thieu_tham_so"}
    if len(noi_dung) > TRAN_BASE64:
        return {"ok": False, "loi": "tep_qua_lon"}
    try:
        tho = base64.b64decode(noi_dung, validate=True)
    except (binascii.Error, ValueError):
        return {"ok": False, "loi": "noi_dung_sai"}
    if len(tho) > TRAN_TEP and tho[:2] != b"\x1f\x8b":
        return {"ok": False, "loi": "tep_qua_lon"}
    dinh_dang = "json"
    if tho[:2] == b"\x1f\x8b":
        dinh_dang = "json.gz"
        try:
            tho = _bung_gzip_co_tran(tho)
        except ValueError as e:
            return {"ok": False, "loi": str(e) if str(e) == "tep_qua_lon" else "noi_dung_sai"}
        except Exception:
            return {"ok": False, "loi": "noi_dung_sai"}
    try:
        data = json.loads(tho.decode("utf-8"))
    except Exception:
        return {"ok": False, "loi": "noi_dung_sai"}
    if not isinstance(data, dict) or not detect_9router_backup(data):
        return {"ok": False, "loi": "dinh_dang_khong_ho_tro"}
    kq = _xem_truoc_9router(data)
    kq["dinh_dang"] = dinh_dang
    return kq


# ───────── Áp dụng vào kho (NV197e) ─────────

def _kiem_muc_nhap(muc) -> str | None:
    """Kiểm hình dạng một mục nhập. Trả None nếu hợp lệ, "bo_qua" nếu thiếu
    bí mật (không có gì để lưu — không phải lỗi), hoặc mã lỗi rút gọn.

    Đây là kiểm SỚM ở tầng module (không chạm kho); kho kiểm lại có thẩm
    quyền trong `nhap_nhieu_tai_khoan`.
    """
    if not isinstance(muc, dict):
        return "danh_sach_sai_khuon"
    if str(muc.get("nguon") or "").strip() not in ("ninerouter", "sub2api", "cpa"):
        return "nguon_khong_ho_tro"
    dinh_danh = muc.get("dinh_danh")
    if not isinstance(dinh_danh, str) or not dinh_danh.strip():
        return "dinh_danh_sai"
    if len(dinh_danh) > 256 or any(not c.isprintable() for c in dinh_danh):
        return "dinh_danh_sai"
    bi_mat = muc.get("bi_mat")
    if bi_mat is None or (isinstance(bi_mat, str) and not bi_mat.strip()):
        return "bo_qua"
    if not isinstance(bi_mat, str) or len(bi_mat) > 4096 \
            or any(not c.isprintable() for c in bi_mat):
        return "bi_mat_sai"
    email = muc.get("email")
    if email is not None and email != "" \
            and (not isinstance(email, str) or len(email) > 320):
        return "email_sai"
    nhan = muc.get("nhan")
    if nhan is not None and nhan != "" \
            and (not isinstance(nhan, str) or len(nhan) > 120):
        return "nhan_sai"
    return None


def _tom_tat_ap_dung(dong: list[dict]) -> dict:
    """Gói kết quả từng dòng thành phản hồi tóm tắt (không bí mật)."""
    dem = {"da_them": 0, "trung": 0, "bo_qua": 0, "loi": 0}
    for d in dong:
        k = d.get("ket_qua")
        if k in dem:
            dem[k] += 1
    return {"ok": True, "tong": len(dong), "chi_tiet": dong, **dem}


# ───────── Module ─────────

_LUOC_DO_TEP = {
    "type": "object",
    "required": ["dang", "noi_dung_base64"],
    "additionalProperties": False,
    "properties": {
        "dang": {"type": "string", "enum": ["ninerouter"]},
        "noi_dung_base64": {"type": "string", "minLength": 1,
                            "maxLength": TRAN_BASE64, "bi_mat": True,
                            "mo_ta": "Nội dung tệp sao lưu 9router mã hoá base64. "
                                     "Trần 8000 ký tự (≈6 KB tệp thô) do lõi giới "
                                     "hạn tổng tham số 8 KB — nêu rõ ở đây thay vì "
                                     "để lõi trả 400 mơ hồ (phát hiện (d))."},
    },
}

_LUOC_DO_MAY_CHU = {
    "type": "object",
    "required": ["loai", "may_chu_url"],
    "additionalProperties": False,
    "properties": {
        "loai": {"type": "string", "enum": ["sub2api", "cpa"]},
        "may_chu_url": {"type": "string", "maxLength": 512},
        "khoa_api": {"type": "string", "maxLength": 4096, "bi_mat": True},
        "email": {"type": "string", "maxLength": 320},
        "mat_khau": {"type": "string", "maxLength": 4096, "bi_mat": True},
        "nhom": {"type": "string", "maxLength": 128},
    },
}

_LUOC_DO_AP_DUNG = {
    "type": "object",
    "required": ["danh_sach"],
    "additionalProperties": False,
    "properties": {
        "danh_sach": {
            "type": "array",
            "maxItems": TRAN_AP_DUNG,
            "items": {
                "type": "object",
                "required": ["nguon", "dinh_danh"],
                "additionalProperties": False,
                "properties": {
                    "nguon": {"type": "string",
                              "enum": ["ninerouter", "sub2api", "cpa"]},
                    "dinh_danh": {"type": "string", "minLength": 1,
                                  "maxLength": 256},
                    "bi_mat": {"type": "string", "maxLength": 4096,
                               "bi_mat": True,
                               "mo_ta": "Token/cookie/khoá truy cập của tài khoản "
                                        "ở dịch vụ nguồn. LẤY Ở ĐÂU: chỉ lấy từ "
                                        "kết quả XEM TRƯỚC của chính module này "
                                        "(xem_truoc_tep/xem_truoc_may_chu) — gửi "
                                        "vào đây những mục đã duyệt. Không tự "
                                        "đặt, không lấy từ nơi khác. Mục thiếu "
                                        "bí mật được BỎ QUA (không phải lỗi)."},
                    "email": {"type": "string", "maxLength": 320},
                    "nhan": {"type": "string", "maxLength": 120},
                },
            },
        },
    },
}


class Module:
    """Xem trước nhập tài khoản hàng loạt (chỉ đọc, chỉ quản trị, chưa nối kho)."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "nhaptaikhoan")

    # ── vòng đời ──────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        # Module không có trường cấu hình và không giữ trạng thái.
        return None

    async def cai(self, cau_hinh: dict) -> dict:
        return {"ok": True, "loi": None, "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}

    async def suc_khoe(self) -> dict:
        return {
            "ok": True,
            "dinh_dang": ["ninerouter-json", "ninerouter-json.gz", "sub2api", "cpa"],
            "ap_dung": "da_noi_kho_tai_khoan",
        }

    async def dung(self) -> None:
        return None

    # ── thực thể / hành động ──────────────────────────────────────────────
    #
    # (a)+(b) của soát độc lập: trước đây module khai MỘT thực thể đồng bộ,
    # thiếu `trang_thai`, manifest thiếu «thiet_bi» ⇒ lõi không bao giờ nạp
    # (404 qua lõi thật dù test gọi thẳng xanh). Nay: đủ ba hàm async +
    # `cung_cap: [..., "thiet_bi"]`, và TÁCH thực thể theo mức rủi ro vì lõi
    # tính rủi ro THEO THỰC THỂ: «xem_truoc» chỉ ĐỌC/xem trước (doc), «ap_dung»
    # là đường GHI tương lai (nguy_hiem). Cổng quản trị nội bộ vẫn kiểm MỌI
    # hành động (phòng thủ hai lớp).

    async def thuc_the(self, loc=None) -> list[dict]:
        return [
            {
                "id": f"{self.tien_to}.xem_truoc",
                "ten": "Xem trước nguồn nhập tài khoản (9router / sub2api / CPA)",
                "loai": "cong_cu",
                "thuoc_tinh": {"dich_vu": "nhaptaikhoan"},
                "tinh_nang": [
                    {"hanh_dong": "xem_truoc_tep", "rui_ro": "doc",
                     "mo_ta": "Phân tích tệp sao lưu 9router (JSON/JSON.gz, gửi base64, tối đa 8000 ký tự base64 ≈ 6 KB tệp thô do trần tham số 8 KB của lõi) và trả danh sách tài khoản DẠNG CHE. Chỉ quản trị.",
                     "tham_so": _LUOC_DO_TEP},
                    {"hanh_dong": "xem_truoc_may_chu", "rui_ro": "doc",
                     "mo_ta": "Đọc danh sách tài khoản qua API quản trị của máy chủ sub2api / CLIProxyAPI (CPA) và trả DẠNG CHE. Khoá/mật khẩu khai theo từng lần, không lưu. Chỉ quản trị.",
                     "tham_so": _LUOC_DO_MAY_CHU},
                ],
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": f"{self.tien_to}.ap_dung",
                "ten": "Áp dụng nhập tài khoản vào kho",
                "loai": "cong_cu",
                "thuoc_tinh": {"dich_vu": "nhaptaikhoan"},
                "tinh_nang": [
                    {"hanh_dong": "ap_dung", "rui_ro": "nguy_hiem",
                     "mo_ta": "Áp dụng danh sách ĐÃ DUYỆT vào kho tài khoản của lõi (xem trước trước, danh sách gửi vào chính là xác nhận; thực thể nguy_hiem: lõi đòi step-up + xac_nhan). Ghi nguyên tử, khử trùng theo (nguồn, định danh), báo cáo từng dòng không lộ bí mật. Chỉ quản trị.",
                     "tham_so": _LUOC_DO_AP_DUNG},
                ],
                "rui_ro_mac_dinh": "nguy_hiem",
            },
        ]

    async def trang_thai(self, ids=None) -> dict:
        ids = ids or [f"{self.tien_to}.xem_truoc", f"{self.tien_to}.ap_dung"]
        tt = {}
        for ent in ids:
            if ent == f"{self.tien_to}.ap_dung":
                tt[ent] = {"san_sang": True, "ap_dung": "da_noi_kho_tai_khoan"}
            elif ent == f"{self.tien_to}.xem_truoc":
                tt[ent] = {"san_sang": True,
                           "dinh_dang": ["ninerouter-json", "ninerouter-json.gz", "sub2api", "cpa"]}
        return tt

    def ap_dung(self, danh_sach) -> dict:
        """Áp dụng danh sách ĐÃ DUYỆT vào kho tài khoản của lõi (NV197e).

        Mỗi mục: {nguon ("ninerouter"|"sub2api"|"cpa"), dinh_danh, bi_mat,
        email?, nhan?}. Ghi qua `app.kho_tai_khoan.nhap_nhieu_tai_khoan`
        (cùng kho, không kho thứ hai), khử trùng theo (nguon, dinh_danh),
        ghi nguyên tử một lần. Báo cáo từng dòng: da_them/trung/bo_qua/loi —
        không lộ bí mật (định danh che, email thô không xuất hiện).

        Luồng gọi đúng: XEM TRƯỚC trước (xem_truoc_tep/xem_truoc_may_chu),
        duyệt rồi mới gọi hàm này — chính danh sách gửi vào là `xac_nhan`;
        thực thể ap_dung ở mức nguy_hiem nên lõi còn đòi step-up + xac_nhan.
        """
        if not isinstance(danh_sach, list) or not danh_sach:
            return {"ok": False, "loi": "danh_sach_rong"}
        if len(danh_sach) > TRAN_AP_DUNG:
            return {"ok": False, "loi": "danh_sach_qua_lon"}

        # Lượt 1 (không chạm kho): phân loại hình dạng từng mục.
        muc_hop_le: list[dict] = []
        dong: list[dict] = []
        for muc in danh_sach:
            ma = _kiem_muc_nhap(muc)
            khoa_che = _che_ten(muc.get("dinh_danh")) \
                if isinstance(muc, dict) else "…"
            if ma is None:
                muc_hop_le.append(muc)
                dong.append({"nguon": str(muc.get("nguon")),
                             "khoa_che": khoa_che, "_cho": len(muc_hop_le) - 1})
            elif ma == "bo_qua":
                dong.append({"nguon": str(muc.get("nguon")) if isinstance(muc, dict) else "",
                             "khoa_che": khoa_che, "ket_qua": "bo_qua"})
            else:
                dong.append({"nguon": str(muc.get("nguon")) if isinstance(muc, dict) else "",
                             "khoa_che": khoa_che, "ket_qua": "loi",
                             "loi": ma})

        # Lượt 2: ghi nguyên tử một lần dưới khoá kho (khử trùng trong kho).
        from app import kho_tai_khoan

        try:
            kq_kho = kho_tai_khoan.nhap_nhieu_tai_khoan(muc_hop_le)
        except kho_tai_khoan.LoiKhoTaiKhoan as e:
            for d in dong:
                if "_cho" in d:
                    d["ket_qua"] = "loi"
                    d["loi"] = e.ma
                    del d["_cho"]
            return _tom_tat_ap_dung(dong)
        except Exception:
            log.exception("nhaptaikhoan.ap_dung: lỗi kho không ngờ")
            for d in dong:
                if "_cho" in d:
                    d["ket_qua"] = "loi"
                    d["loi"] = "loi_noi_bo"
                    del d["_cho"]
            return _tom_tat_ap_dung(dong)

        for d in dong:
            if "_cho" in d:
                k = kq_kho[d["_cho"]]
                d["ket_qua"] = k["ket_qua"]
                if k["ket_qua"] == "loi":
                    d["loi"] = k["loi"]
                del d["_cho"]
        return _tom_tat_ap_dung(dong)

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        # Cổng quản trị TRƯỚC mọi xử lý: khách không tới được cả bước phân tích.
        if not _nguoi_goi_la_quan_tri():
            return {"ok": False, "loi": "chi_quan_tri"}
        # Hành động chỉ được chạy dưới thực thể của nó — không gọi hành động
        # nguy hiểm (ap_dung) qua thực thể rủi ro thấp (xem_truoc).
        _HANH_DONG_THEO_THUC_THE = {
            f"{self.tien_to}.xem_truoc": {"xem_truoc_tep", "xem_truoc_may_chu"},
            f"{self.tien_to}.ap_dung": {"ap_dung"},
        }
        cho_phep = _HANH_DONG_THEO_THUC_THE.get(id)
        if cho_phep is None:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        if hanh_dong not in cho_phep:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        if hanh_dong == "xem_truoc_tep":
            return _xem_truoc_tep(tham_so)
        if hanh_dong == "xem_truoc_may_chu":
            loai = str(tham_so.get("loai") or "").strip()
            if loai not in ("sub2api", "cpa"):
                return {"ok": False, "loi": "loai_khong_ho_tro"}
            url = str(tham_so.get("may_chu_url") or "").strip()
            if not url:
                return {"ok": False, "loi": "thieu_tham_so"}
            try:
                from app.tu_xa import kiem_url_lan  # bộ kiểm SSRF chung của lõi

                url_sach = await kiem_url_lan(url)
            except ValueError:
                return {"ok": False, "loi": "url_khong_hop_le"}
            if loai == "sub2api":
                return await _xem_truoc_sub2api(url_sach, tham_so)
            return await _xem_truoc_cpa(url_sach, tham_so)
        if hanh_dong == "ap_dung":
            return self.ap_dung(tham_so.get("danh_sach"))
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
