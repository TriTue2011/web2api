"""Module client mỏng may-dang-nhap — cầu nối gọi dịch vụ dich-vu/may-dang-nhap (NV145 - Giai đoạn 2).

Chỉ đóng vai trò cầu nối HTTP nội bộ (FastAPI cổng 8010, Bearer).
Mọi logic đăng nhập, điều khiển trình duyệt và két mật khẩu đều nằm ở container riêng.
Đầu ra dịch vụ là DỮ LIỆU KHÔNG TIN: kiểm SSRF, cắt ở trần, gắn nguon_ngoai=True.

Thiết kế bảo mật:
- Kiểm SSRF: chỉ cho phép http/https, loopback và mạng Docker nội bộ; chặn link-local/metadata,
  multicast, reserved; kiểm tra lại URL trước mỗi lời gọi mạng.
- Chặn lộ bí mật: một logging.Filter cài lên thư viện HTTP loại bỏ dòng chứa khoá hoặc Bearer.
- Bảng hành động đóng: từ chối các hành động lạ trước khi gọi mạng; allowlist cấu trúc trường đầu ra.
- An toàn tham số: ho_so và email kiểm regex chặt chẽ, quote toàn bộ, từ chối ký tự đường dẫn (IDOR/path traversal).
- Các đường nhạy cảm (nhận/lưu mật khẩu, 2FA, cookie) không lộ qua công cụ AI (thuc_hien);
  phương thức Python riêng nhận mật khẩu qua BiMatTam và xoá ngay sau khi gửi.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.parse
from typing import Any

import httpx

from app.chuyen_tiep import TransportGhimIp, doc_phan_hoi_co_tran, PhanHoiQuaLon
from app.tai_khoan_hang_loat import BiMatTam
from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

log = logging.getLogger(__name__)

#: Trần cắt đầu ra phía module — 80.000 ký tự
TRAN_DAU_RA = 80_000
#: Hết giờ gọi dịch vụ — 30 giây
HET_GIO_GOI = 30.0

#: Khoá đang dùng — bộ lọc log chặn mọi dòng chứa khoá này
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
)

#: Regex kiểm tra an toàn tham số (hỗ trợ hồ sơ dạng slug hoặc email a-z, 0-9, . _ @ -)
_RE_HO_SO = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_KY_TU_CAM_DUONG_DAN = ("/", "\\", "..", "%", "?", "#")


# ── Bộ lọc nhật ký chặn khoá dịch vụ ──────────────────────────────────────────

class _ChanKhoaDichVu(logging.Filter):
    """Bỏ dòng log chứa khoá dịch vụ. Cài MỘT lần lên logger của thư viện HTTP.

    Chặn theo cấu trúc: mẫu «authorization» + «bearer».
    Chặn theo giá trị: mọi khoá trong _KHOA_DANG_DUNG.
    """

    _may_dang_nhap_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            # Gặp lỗi khi lấy nội dung dòng log thì bỏ dòng đó (đóng khi lỗi)
            return False
        thap = noi_dung.lower()
        if "authorization" in thap and "bearer" in thap:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG if k)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_may_dang_nhap_loc", False)]:
            lg.removeFilter(f)
        lg.addFilter(_ChanKhoaDichVu())


_cai_bo_loc_log()


# ── Kiểm tra SSRF URL dịch vụ (Dùng đúng bộ kiểm chung của lõi app.tu_xa) ────
_dia_chi_bi_chan_dich_vu = _dia_chi_bi_chan
_kiem_url_dich_vu = kiem_url_lan


# ── An toàn tham số ──────────────────────────────────────────────────────────

def _kiem_ho_so(ho_so: Any) -> str:
    s = str(ho_so or "").strip()
    if not s or len(s) > 64:
        raise ValueError("tham_so_khong_hop_le")
    for cam in _KY_TU_CAM_DUONG_DAN:
        if cam in s:
            raise ValueError("tham_so_khong_hop_le")
    for c in s:
        if ord(c) < 32:
            raise ValueError("tham_so_khong_hop_le")
    if not _RE_HO_SO.match(s):
        raise ValueError("tham_so_khong_hop_le")
    return urllib.parse.quote(s, safe="@")


def _kiem_email(email: Any) -> str:
    s = str(email or "").strip()
    if not s or len(s) > 254:
        raise ValueError("tham_so_khong_hop_le")
    for cam in _KY_TU_CAM_DUONG_DAN:
        if cam in s:
            raise ValueError("tham_so_khong_hop_le")
    for c in s:
        if ord(c) < 32:
            raise ValueError("tham_so_khong_hop_le")
    if not _RE_EMAIL.match(s):
        raise ValueError("tham_so_khong_hop_le")
    return urllib.parse.quote(s, safe="")


def _cat_dau_ra(dau_ra: Any) -> str:
    s = dau_ra if isinstance(dau_ra, str) else json.dumps(dau_ra, ensure_ascii=False)
    if len(s) > TRAN_DAU_RA:
        s = s[:TRAN_DAU_RA] + "\n…[cat]"
    return s


# ── Bảng hành động ĐÓNG (Công cụ AI qua thuc_hien) ───────────────────────────

#: Bảng hành động đóng: ten -> (phuong_thuc, mau_duong, rui_ro, truong_cho_phep)
HANH_DONG = {
    "ho_so_danh_sach": (
        "GET",
        "/v1/profiles",
        "doc",
        {"ok", "profiles", "count"},
    ),
    "phien_trang_thai": (
        "GET",
        "/v1/session/{ho_so}/status",
        "doc",
        {"profile", "loaded", "pages", "cookies", "stale"},
    ),
    "dang_nhap_bat_dau": (
        "POST",
        "/v1/session/auto-login-saved",
        "ghi",
        {"profile", "started", "status", "message", "state", "ok"},
    ),
    "dang_nhap_trang_thai": (
        "GET",
        "/v1/session/{ho_so}/auto-login-status",
        "doc",
        {"profile", "state", "message", "started_at", "updated_at", "success", "error", "ok"},
    ),
    "ho_so_dong": (
        "POST",
        "/v1/session/{ho_so}/close",
        "ghi",
        {"profile", "closed", "status", "message", "ok"},
    ),
    "tai_khoan_da_luu_danh_sach": (
        "GET",
        "/v1/accounts/saved",
        "doc",
        {"ok", "accounts", "count"},
    ),
}

#: Bảng ánh xạ dịch vụ đăng nhập (bảng MỞ)
DICH_VU_DANG_NHAP = {
    "google": {
        "bat_dau": ("POST", "/v1/session/auto-login-saved", lambda hs: {"profile": hs}),
        "trang_thai": ("GET", "/v1/session/{ho_so}/auto-login-status", None),
        "2fa": ("POST", "/v1/session/{ho_so}/auto-login-2fa-code"),
    },
    "gemini-web": {
        "bat_dau": ("POST", "/v1/gemini-web/onboard", lambda hs: {"profile": hs, "email": "", "password": "", "totp_secret": ""}),
        "trang_thai": ("GET", "/v1/gemini-web/{ho_so}/onboard-status", None),
        "2fa": ("POST", "/v1/gemini-web/{ho_so}/onboard-2fa-code"),
        "cookie": ("GET", "/v1/gemini-web/{ho_so}/cookies"),
    },
    "chatgpt": {
        "bat_dau": ("POST", "/v1/chatgpt/onboard", lambda hs: {"profile": hs, "email": "", "password": "", "totp_secret": ""}),
        "trang_thai": ("GET", "/v1/chatgpt/{ho_so}/onboard-status", None),
        "2fa": ("POST", "/v1/chatgpt/{ho_so}/onboard-2fa-code"),
        "cookie": ("GET", "/v1/chatgpt/{ho_so}/refresh-jwt"),
    },
    "claude-web": {
        "bat_dau": ("POST", "/v1/claude-web/onboard", lambda hs: {"profile": hs, "email": "", "password": "", "totp_secret": ""}),
        "trang_thai": ("GET", "/v1/claude-web/{ho_so}/onboard-status", None),
        "2fa": ("POST", "/v1/claude-web/{ho_so}/onboard-2fa-code"),
        "cookie": ("GET", "/v1/claude-web/{ho_so}/session"),
    },
    "grok-web": {
        "bat_dau": ("POST", "/v1/grok-web/onboard", lambda hs: {"profile": hs, "email": "", "password": "", "totp_secret": ""}),
        "trang_thai": ("GET", "/v1/grok-web/{ho_so}/onboard-status", None),
        "2fa": ("POST", "/v1/grok-web/{ho_so}/onboard-2fa-code"),
        "cookie": ("GET", "/v1/grok-web/{ho_so}/session"),
    },
    "openai": {
        "bat_dau": ("POST", "/v1/openai-native/onboard", lambda hs: {"profile": hs, "email": "", "password": "", "totp_secret": ""}),
        "trang_thai": ("GET", "/v1/openai-native/{ho_so}/onboard-status", None),
        "2fa": ("POST", "/v1/openai-native/{ho_so}/onboard-2fa-code"),
        "cookie": ("GET", "/v1/openai-native/{ho_so}/token"),
    },
}


# ── Lớp Module ───────────────────────────────────────────────────────────────

class Module:
    """Module client mỏng kết nối dịch vụ may-dang-nhap."""

    def __init__(self, manifest: dict, _goc_kiem_thu: str | None = None) -> None:
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "may_dang_nhap")
        self._url = "http://web2api-may-dang-nhap:8010"
        self._khoa = ""
        self._suc_khoe_cache: dict | None = None
        self._suc_khoe_cache_luc: float = 0.0
        self._CACHE_TTL_GIAY: float = 15.0

    # ── Vòng đời ─────────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        url = str(cau_hinh.get("dich_vu_url") or "http://web2api-may-dang-nhap:8010").strip().rstrip("/")
        self._url = url
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        self._khoa = khoa
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)

    async def cai(self, cau_hinh: dict) -> dict:
        cau_hinh = cau_hinh or {}
        url = str(cau_hinh.get("dich_vu_url") or "http://web2api-may-dang-nhap:8010")
        try:
            url_sach = await _kiem_url_dich_vu(url)
        except ValueError as e:
            return {"ok": False, "loi": f"dia_chi_dich_vu_khong_hop_le: {e}"}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()

        # Kiểm tra xác thực với dịch vụ thật: phải đúng danh tính may-dang-nhap và status: ok
        url_cu, khoa_cu = self._url, self._khoa
        self._url, self._khoa = url_sach, khoa
        try:
            kq_health = await self._goi("GET", "/health")
            kq_auth = await self._goi("GET", "/v1/profiles")
        finally:
            self._url, self._khoa = url_cu, khoa_cu

        # Kiểm tra /health phải trả đúng JSON khuôn dịch vụ may-dang-nhap
        if (
            not isinstance(kq_health, dict)
            or not kq_health.get("ok")
            or kq_health.get("dich_vu") != "may-dang-nhap"
            or kq_health.get("status") != "ok"
        ):
            loi_msg = (
                kq_health.get("loi")
                if isinstance(kq_health, dict)
                else "Dịch vụ không phản hồi đúng danh tính may-dang-nhap"
            ) or "Dịch vụ không phản hồi đúng khuôn /health của may-dang-nhap"
            return {"ok": False, "loi": f"dich_vu_khong_hop_le: {loi_msg}"}

        # Kiểm tra /v1/profiles xác thực khoá dịch vụ
        if not isinstance(kq_auth, dict) or not kq_auth.get("ok"):
            loi_msg = (
                kq_auth.get("loi")
                if isinstance(kq_auth, dict)
                else "Khoá dịch vụ không đúng hoặc hết hạn"
            ) or "Khoá dịch vụ không đúng hoặc hết hạn"
            return {"ok": False, "loi": loi_msg}

        self._url = url_sach
        self._khoa = khoa
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)

        khoa_che = f"…{khoa[-4:]}" if len(khoa) >= 4 else "…"
        ket_qua_tung_khoa = [{"khoa": khoa_che, "ok": True}] if khoa else []
        return {
            "ok": True,
            "loi": None,
            "ket_qua_tung_khoa": ket_qua_tung_khoa,
            "cau_hinh_luu": {
                "dich_vu_url": url_sach,
                "khoa_dich_vu": khoa,
            },
        }

    async def suc_khoe(self) -> dict:
        if not self._khoa:
            return {
                "ok": False,
                "ma_loi": "chua_cau_hinh",
                "loi": "Chưa cấu hình khoá dịch vụ",
                "dich_vu_url": self._url,
                "nguon_ngoai": True,
            }

        kq_health = await self._goi("GET", "/health")
        kq_auth = await self._goi("GET", "/v1/profiles")

        if kq_auth.get("ma_loi") == "sai_khoa_dich_vu":
            return {
                "ok": False,
                "ma_loi": "sai_khoa_dich_vu",
                "loi": "Khoá dịch vụ không đúng hoặc hết hạn",
                "dich_vu_url": self._url,
                "nguon_ngoai": True,
            }

        if not kq_health.get("ok") or not kq_auth.get("ok"):
            ma_loi = kq_auth.get("ma_loi") or kq_health.get("ma_loi") or "loi_dich_vu"
            loi = kq_auth.get("loi") or kq_health.get("loi")
            return {
                "ok": False,
                "ma_loi": ma_loi,
                "loi": loi,
                "dich_vu_url": self._url,
                "nguon_ngoai": True,
            }

        return {
            "ok": True,
            "dich_vu_url": self._url,
            "nguon_ngoai": True,
        }

    async def dung(self) -> None:
        return None

    # ── Gọi HTTP nội bộ an toàn ──────────────────────────────────────────────

    async def _goi(
        self,
        phuong_thuc: str,
        duong: str,
        json_body: dict | None = None,
    ) -> dict:
        # Kiểm tra lại SSRF trước khi gọi mạng chống DNS đổi sau lúc cài
        try:
            url_goc = await _kiem_url_dich_vu(self._url)
        except ValueError as e:
            return {
                "ok": False,
                "ma_loi": "dia_chi_dich_vu_khong_hop_le",
                "loi": str(e),
                "nguon_ngoai": True,
            }

        url_day_du = f"{url_goc}{duong}"
        headers = {"Authorization": f"Bearer {self._khoa}"} if self._khoa else {}

        transport = TransportGhimIp(kiem_ip=_dia_chi_bi_chan_dich_vu)
        try:
            async with httpx.AsyncClient(transport=transport, timeout=HET_GIO_GOI, follow_redirects=False) as client:
                req = client.build_request(
                    method=phuong_thuc,
                    url=url_day_du,
                    json=json_body,
                    headers=headers,
                )
                res = await client.send(req, stream=True)
                try:
                    than_bytes = await doc_phan_hoi_co_tran(res, tran_bytes=1024 * 1024)
                except PhanHoiQuaLon:
                    return {
                        "ok": False,
                        "ma_loi": "phan_hoi_qua_lon",
                        "loi": "Phản hồi từ dịch vụ vượt quá trần kích thước cho phép (1 MiB)",
                        "nguon_ngoai": True,
                    }
        except httpx.TimeoutException:
            return {
                "ok": False,
                "ma_loi": "het_gio",
                "loi": "Hết giờ kết nối tới dịch vụ",
                "nguon_ngoai": True,
            }
        except httpx.RequestError as e:
            log.warning("khong_noi_duoc_dich_vu: %s", type(e).__name__)
            return {
                "ok": False,
                "ma_loi": "khong_noi_duoc_dich_vu",
                "loi": "Không kết nối được tới dịch vụ",
                "nguon_ngoai": True,
            }

        # Chuyển hướng 3xx bị từ chối
        if 300 <= res.status_code < 400:
            return {
                "ok": False,
                "ma_loi": "chuyen_huong_khong_cho_phep",
                "loi": f"Dịch vụ chuyển hướng HTTP {res.status_code} không được phép",
                "nguon_ngoai": True,
            }

        # Phân loại theo mã trạng thái
        if res.status_code == 401:
            return {
                "ok": False,
                "ma_loi": "sai_khoa_dich_vu",
                "loi": "Khoá dịch vụ không đúng hoặc hết hạn",
                "nguon_ngoai": True,
            }

        if res.status_code == 403:
            ma_loi = "sai_khoa_dich_vu"
            try:
                du_lieu = json.loads(than_bytes.decode("utf-8"))
                if isinstance(du_lieu, dict) and du_lieu.get("ma_loi") == "tinh_nang_tat":
                    ma_loi = "tinh_nang_tat"
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                pass
            return {
                "ok": False,
                "ma_loi": ma_loi,
                "loi": "Bị từ chối quyền truy cập hoặc tính năng đang tắt",
                "nguon_ngoai": True,
            }

        if res.status_code == 404:
            return {
                "ok": False,
                "ma_loi": "khong_thay",
                "loi": "Không tìm thấy tài nguyên trên dịch vụ",
                "nguon_ngoai": True,
            }

        if 500 <= res.status_code < 600:
            return {
                "ok": False,
                "ma_loi": "dich_vu_loi",
                "loi": "Dịch vụ gặp lỗi máy chủ nội bộ",
                "nguon_ngoai": True,
            }

        if res.status_code >= 400:
            return {
                "ok": False,
                "ma_loi": "loi_dich_vu",
                "loi": f"Dịch vụ báo lỗi HTTP {res.status_code}",
                "nguon_ngoai": True,
            }

        try:
            than = json.loads(than_bytes.decode("utf-8"))
        except Exception:
            return {
                "ok": False,
                "ma_loi": "phan_hoi_khong_phai_json",
                "loi": "Phản hồi từ dịch vụ không phải định dạng JSON hợp lệ",
                "nguon_ngoai": True,
            }

        if not isinstance(than, dict):
            than = {"du_lieu": than}

        than["ok"] = True
        than["nguon_ngoai"] = True
        return than

    # ── Công cụ AI qua lõi (thuc_the / trang_thai / thuc_hien) ────────────────

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        doc_actions = [
            {"hanh_dong": k, "rui_ro": "doc", "mo_ta": f"Hành động đọc «{k}» trên dịch vụ c2a."}
            for k, v in HANH_DONG.items()
            if v[2] == "doc"
        ]
        ghi_actions = [
            {"hanh_dong": k, "rui_ro": "ghi", "mo_ta": f"Hành động ghi «{k}» trên dịch vụ c2a."}
            for k, v in HANH_DONG.items()
            if v[2] == "ghi"
        ]
        return [
            {
                "id": f"{self.tien_to}.cong_cu_doc",
                "ten": "Công cụ đọc trạng thái đăng nhập c2a",
                "loai": "cong_cu",
                "khu": "he_thong",
                "thuoc_tinh": {"dich_vu": "may-dang-nhap"},
                "tinh_nang": doc_actions,
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": f"{self.tien_to}.cong_cu_ghi",
                "ten": "Công cụ thao tác đăng nhập c2a",
                "loai": "cong_cu",
                "khu": "he_thong",
                "thuoc_tinh": {"dich_vu": "may-dang-nhap"},
                "tinh_nang": ghi_actions,
                "rui_ro_mac_dinh": "ghi",
            },
        ]

    async def trang_thai(self, *args, **kwargs) -> dict:
        """Tra cứu trạng thái — đa hình:

        1. Chuẩn «thiet_bi» của lõi: trang_thai(ids: list[str]) -> dict[str, dict]
        2. Tra cứu trạng thái đăng nhập: trang_thai(dich_vu: str, ho_so: str) -> dict
        """
        if len(args) == 1 and (isinstance(args[0], list) or args[0] is None):
            ids = args[0] or [f"{self.tien_to}.cong_cu_doc", f"{self.tien_to}.cong_cu_ghi"]
            if not self._khoa:
                return {
                    i: {
                        "gia_tri": "chua_cau_hinh",
                        "don_vi": None,
                        "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "hop_le": False,
                    }
                    for i in ids
                }
            # Hỏi dịch vụ với bộ đệm ngắn để phản ánh trạng thái trung thực
            now = time.time()
            if self._suc_khoe_cache is not None and (now - self._suc_khoe_cache_luc) < self._CACHE_TTL_GIAY:
                kq_sk = self._suc_khoe_cache
            else:
                kq_sk = await self.suc_khoe()
                self._suc_khoe_cache = kq_sk
                self._suc_khoe_cache_luc = now

            hop_le = bool(kq_sk.get("ok"))
            gia_tri = "san_sang" if hop_le else "khong_kha_dung"

            return {
                i: {
                    "gia_tri": gia_tri,
                    "don_vi": None,
                    "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "hop_le": hop_le,
                }
                for i in ids
            }

        dich_vu = args[0] if len(args) > 0 else kwargs.get("dich_vu", "")
        ho_so = args[1] if len(args) > 1 else kwargs.get("ho_so", "")
        return await self.trang_thai_dang_nhap(dich_vu, ho_so)

    async def trang_thai_dang_nhap(self, dich_vu: str, ho_so: str) -> dict:
        """Tra cứu trạng thái đăng nhập dịch vụ theo bảng mở DICH_VU_DANG_NHAP."""
        try:
            hs_sach = _kiem_ho_so(ho_so)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

        cau_hinh = DICH_VU_DANG_NHAP.get(dich_vu)
        if not cau_hinh:
            return {
                "ok": False,
                "loi": "dich_vu_khong_ho_tro",
                "dich_vu": dich_vu,
                "nguon_ngoai": True,
            }

        phuong_thuc, mau_duong, _ = cau_hinh["trang_thai"]
        duong = mau_duong.replace("{ho_so}", hs_sach)
        return await self._goi(phuong_thuc, duong)

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None = None) -> dict:
        tham_so = tham_so or {}

        # 1. Bảng hành động đóng: từ chối TRƯỚC khi gọi mạng
        cau_hinh_hd = HANH_DONG.get(hanh_dong)
        if not cau_hinh_hd:
            return {
                "ok": False,
                "loi": "hanh_dong_khong_ho_tro",
                "nguon_ngoai": True,
            }

        phuong_thuc, mau_duong, rui_ro, truong_cho_phep = cau_hinh_hd

        # 2. Kiểm tra tham số an toàn (IDOR / Path traversal)
        duong = mau_duong
        body = None

        if "{ho_so}" in mau_duong:
            ho_so_tho = tham_so.get("ho_so", "")
            try:
                ho_so_sach = _kiem_ho_so(ho_so_tho)
            except ValueError:
                return {
                    "ok": False,
                    "loi": "tham_so_khong_hop_le",
                    "nguon_ngoai": True,
                }
            duong = mau_duong.replace("{ho_so}", ho_so_sach)

        if hanh_dong == "dang_nhap_bat_dau":
            ho_so_tho = tham_so.get("ho_so", "")
            try:
                ho_so_sach = _kiem_ho_so(ho_so_tho)
            except ValueError:
                return {
                    "ok": False,
                    "loi": "tham_so_khong_hop_le",
                    "nguon_ngoai": True,
                }
            body = {"profile": ho_so_sach}

        # 3. Gọi dịch vụ
        kq = await self._goi(phuong_thuc, duong, json_body=body)
        if not kq.get("ok"):
            return kq

        # Đăng nhập bắt đầu thất bại (state == "failed")
        if hanh_dong == "dang_nhap_bat_dau" and kq.get("state") == "failed":
            return {
                "ok": False,
                "ma_loi": "dang_nhap_that_bai",
                "loi": _cat_dau_ra(kq.get("error") or kq.get("message") or "Đăng nhập thất bại"),
                "nguon_ngoai": True,
            }

        # 4. Chiếu theo danh sách trường được phép (Allowlist cấu trúc)
        kq_sach: dict[str, Any] = {
            "ok": True,
            "nguon_ngoai": True,
        }

        if hanh_dong == "tai_khoan_da_luu_danh_sach":
            raw_accs = kq.get("accounts") or kq.get("du_lieu") or []
            accs_sach = []
            truong_acc = {"email", "label", "loai", "created_at", "updated_at", "has_totp"}
            for acc in raw_accs:
                if isinstance(acc, dict):
                    accs_sach.append({ak: av for ak, av in acc.items() if ak in truong_acc})
            kq_sach["accounts"] = accs_sach
            kq_sach["count"] = len(accs_sach)
        else:
            for k, v in kq.items():
                if k in truong_cho_phep:
                    if k == "cookies":
                        if isinstance(v, (list, dict, set)):
                            v = len(v)
                        kq_sach["cookies"] = int(v)
                    else:
                        kq_sach[k] = v

        # 5. Cắt trần đầu ra
        chuoi_json = json.dumps(kq_sach, ensure_ascii=False)
        if len(chuoi_json) > TRAN_DAU_RA:
            kq_sach = {
                "ok": True,
                "dau_ra": _cat_dau_ra(chuoi_json),
                "nguon_ngoai": True,
            }

        return kq_sach

    # ── Phương thức Python riêng (không lộ qua công cụ AI) ───────────────────

    async def luu_tai_khoan(
        self,
        email: str,
        mat_khau: BiMatTam | str,
        totp: BiMatTam | str | None = None,
    ) -> dict:
        """Lưu tài khoản vào dịch vụ c2a.

        Mật khẩu và mã TOTP nhận qua BiMatTam và được xoá ngay sau khi gửi.
        Tuyệt đối không lưu vào log hay trả về trong kết quả.
        """
        mk_str = ""
        totp_str = ""
        try:
            try:
                _kiem_email(email)
            except ValueError:
                return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

            if isinstance(mat_khau, BiMatTam):
                mk_str = mat_khau.lay()
            else:
                mk_str = str(mat_khau or "")

            if isinstance(totp, BiMatTam):
                totp_str = totp.lay()
            elif totp:
                totp_str = str(totp)
            else:
                totp_str = ""

            kq = await self._goi(
                "POST",
                "/v1/accounts/saved",
                json_body={
                    "email": email,
                    "password": mk_str,
                    "totp_secret": totp_str,
                },
            )
            return {
                "ok": bool(kq.get("ok")),
                "email": email,
                "nguon_ngoai": True,
            }
        finally:
            if isinstance(mat_khau, BiMatTam):
                mat_khau.xoa()
            if isinstance(totp, BiMatTam):
                totp.xoa()
            del mk_str, totp_str

    async def xoa_tai_khoan(self, email: str) -> dict:
        """Xoá tài khoản đã lưu."""
        try:
            email_sach = _kiem_email(email)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

        kq = await self._goi("DELETE", f"/v1/accounts/saved/{email_sach}")
        return {
            "ok": bool(kq.get("ok")),
            "nguon_ngoai": True,
        }

    async def bat_dau_dang_nhap(self, dich_vu: str, ho_so: str) -> dict:
        """Bắt đầu đăng nhập dịch vụ theo bảng mở DICH_VU_DANG_NHAP."""
        try:
            hs_sach = _kiem_ho_so(ho_so)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

        cau_hinh = DICH_VU_DANG_NHAP.get(dich_vu)
        if not cau_hinh:
            return {
                "ok": False,
                "loi": "dich_vu_khong_ho_tro",
                "dich_vu": dich_vu,
                "nguon_ngoai": True,
            }

        if dich_vu != "google":
            # Kiểm tra xem hồ sơ đã có trên dịch vụ hoặc đã có tài khoản lưu chưa
            kq_profiles = await self._goi("GET", "/v1/profiles")
            profiles = set(kq_profiles.get("profiles") or [])
            if hs_sach not in profiles:
                # Kiểm tra thêm trong danh sách tài khoản
                kq_accs = await self._goi("GET", "/v1/accounts/saved")
                accs = kq_accs.get("du_lieu") or kq_accs.get("accounts") or []
                co_tk = False
                hs_chuan_hoa = hs_sach.strip().lower()
                for a in accs:
                    if isinstance(a, dict):
                        em = str(a.get("email") or "").strip().lower()
                        if hs_chuan_hoa == em:
                            co_tk = True
                            break
                if not co_tk:
                    return {
                        "ok": False,
                        "loi": "thieu_tai_khoan_da_luu",
                        "dich_vu": dich_vu,
                        "nguon_ngoai": True,
                    }

        phuong_thuc, mau_duong, ham_body = cau_hinh["bat_dau"]
        duong = mau_duong.replace("{ho_so}", hs_sach)
        body = ham_body(hs_sach) if callable(ham_body) else {"profile": hs_sach}
        if isinstance(body, dict):
            body.setdefault("email", "")
            body.setdefault("password", "")
            body.setdefault("totp_secret", "")
        return await self._goi(phuong_thuc, duong, json_body=body)

    async def gui_ma_2fa(self, dich_vu: str, ho_so: str, ma: BiMatTam | str) -> dict:
        """Gửi mã xác thực 2FA cho phiên đăng nhập."""
        try:
            hs_sach = _kiem_ho_so(ho_so)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

        cau_hinh = DICH_VU_DANG_NHAP.get(dich_vu)
        if not cau_hinh:
            return {
                "ok": False,
                "loi": "dich_vu_khong_ho_tro",
                "dich_vu": dich_vu,
                "nguon_ngoai": True,
            }

        phuong_thuc, mau_duong = cau_hinh["2fa"]
        duong = mau_duong.replace("{ho_so}", hs_sach)

        ma_str = ma.lay() if isinstance(ma, BiMatTam) else str(ma or "")
        try:
            return await self._goi(phuong_thuc, duong, json_body={"code": ma_str})
        finally:
            if isinstance(ma, BiMatTam):
                ma.xoa()
            del ma_str

    async def lay_cookie(self, dich_vu: str, ho_so: str) -> dict:
        """Lấy cookie đã đăng nhập của hồ sơ."""
        try:
            hs_sach = _kiem_ho_so(ho_so)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le", "nguon_ngoai": True}

        cau_hinh = DICH_VU_DANG_NHAP.get(dich_vu)
        if not cau_hinh or "cookie" not in cau_hinh:
            return {
                "ok": False,
                "loi": "khong_ho_tro",
                "dich_vu": dich_vu,
                "nguon_ngoai": True,
            }

        phuong_thuc, mau_duong = cau_hinh["cookie"]
        duong = mau_duong.replace("{ho_so}", hs_sach)
        return await self._goi(phuong_thuc, duong)

