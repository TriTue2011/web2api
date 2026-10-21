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
import base64
import ipaddress
import json
import logging
import os
import re
import socket
import time
import urllib.parse
from typing import Any

import httpx

from app.chuyen_tiep import TransportGhimIp, doc_phan_hoi_co_tran, PhanHoiQuaLon
from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach, KetQuaDangNhap
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

def _ten_ho_so_dich_vu(dinh_danh: Any) -> str:
    """Tên hồ sơ trình duyệt mà DỊCH VỤ chấp nhận, suy từ địa chỉ email.

    Dịch vụ chỉ nhận chữ/số/«-»/«_» (browser_pool._PROFILE_SLUG) và tự ghép hồ sơ với
    tài khoản trong két theo phần trước «@» (accounts_db.resolve_account). Quy ước
    của c2a: ``google-`` + phần trước «@» với MỌI ký tự không phải chữ/số đổi thành
    «-». Truyền thẳng email làm tên hồ sơ làm lượt đăng nhập sập ngay
    (ValueError «Tên profile không hợp lệ») — đo thật 21/10/2026. Đã là tên hồ sơ
    (không có «@») thì giữ nguyên."""
    s = str(dinh_danh or "").strip().lower()
    if "@" not in s:
        return s
    phan_dau = re.sub(r"[^a-z0-9]", "-", s.split("@", 1)[0])[:56]
    return ("google-" + phan_dau) if phan_dau.strip("-") else ""


#: Module ID của các tài khoản đi luồng đăng nhập ChatGPT — DỮ LIỆU từ bản ghi
#: tài khoản đã lưu (NV197c ghi sau lần đăng nhập thành công). KHÔNG đoán theo
#: chuỗi email: email chứa chuỗi «chatgpt» nhưng module_id khác vẫn đi luồng
#: Google; email trần nhưng module_id thuộc tập này vẫn đi luồng ChatGPT.
_LUONG_CHATGPT_MODULE_ID = frozenset({"chatgpt-web"})


def _la_luong_chatgpt(module_cua: Any) -> bool:
    """Tài khoản có thuộc luồng ChatGPT không — theo module sở hữu (dữ liệu)."""
    return str(module_cua or "").strip() in _LUONG_CHATGPT_MODULE_ID


#: NV223: module sở hữu → «luồng» của tài khoản (dữ liệu khai báo; KHÔNG suy
#: từ email/tên miền). Chỉ các module có đường onboard riêng phía dịch vụ.
_LUONG_THEO_MODULE_ID = {
    "chatgpt-web": "chatgpt",
    "claude-web": "claude",
    "grok-web": "grok",
}

#: NV223: (luồng, ``dang_nhap_bang``) → khoá đường onboard trong
#: ``DICH_VU_DANG_NHAP``. Cặp KHÔNG có trong bảng = không hỗ trợ → lỗi rõ,
#: không tiến hành đăng nhập. «truc_tiep» chỉ có ở ChatGPT (đường
#: openai-native của dịch vụ); Claude/Grok phía dịch vụ chỉ có đường qua
#: Google. ``dang_nhap_bang == ""`` (chưa khai) KHÔNG qua bảng này — giữ
#: nguyên hai đường cũ NV145b/NV210.
_DUONG_ONBOARD_THEO_LUONG = {
    ("chatgpt", "google"): "chatgpt",
    ("chatgpt", "truc_tiep"): "openai",
    ("claude", "google"): "claude-web",
    ("grok", "google"): "grok-web",
}

_DANG_NHAP_BANG_HOP_LE = ("", "truc_tiep", "google")


def _ten_ho_so_theo_dich_vu(dich_vu: str, email: str) -> str:
    """Tên hồ sơ cho đường onboard NV223: tiền tố theo KHOÁ DỊCH VỤ (phần
    trước «-web» nếu có — dữ liệu trong ``DICH_VU_DANG_NHAP``) + slug
    localpart của email TÀI KHOẢN (hồ sơ thuộc về tài khoản đích, không
    thuộc về tài khoản Google đăng nhập hộ)."""
    s = str(email or "").strip().lower()
    phan_dau = re.sub(r"[^a-z0-9]", "-", s.split("@", 1)[0])[:56]
    tien_to = str(dich_vu or "").split("-", 1)[0]
    return (f"{tien_to}-{phan_dau}") if phan_dau.strip("-") else ""


def _ten_ho_so_chatgpt(email: str) -> str:
    """Tên hồ sơ ChatGPT mà dịch vụ chấp nhận: ``chatgpt-`` + slug localpart.

    Cùng quy tắc slug với Google (chỉ chữ/số/«-»/«_», ≤ 64 ký tự); gửi email
    trần làm tên hồ sơ làm lượt đăng nhập sập ngay (đo thật 21/10/2026).
    Dịch vụ ghép hồ sơ với tài khoản theo localpart TRONG CÙNG MỘT KHO
    (kho ``openai`` cho ChatGPT) — không suy kiểu tài khoản bằng chuỗi.
    """
    s = str(email or "").strip().lower()
    phan_dau = re.sub(r"[^a-z0-9]", "-", s.split("@", 1)[0])[:56]
    return ("chatgpt-" + phan_dau) if phan_dau.strip("-") else ""


def _ho_so_theo_luong(dinh_danh: Any, module_cua: Any = "") -> str:
    """Tên hồ sơ dịch vụ cho email/hồ sơ, theo ĐÚNG luồng của tài khoản.

    Nhận cả email (lõi truyền email) hoặc tên hồ sơ đã có. Luồng ChatGPT dùng
    tiền tố ``chatgpt-``; các luồng khác giữ nguyên quy ước cũ.
    """
    s = str(dinh_danh or "")
    if "@" not in s:
        return _kiem_ho_so(s)
    if _la_luong_chatgpt(module_cua):
        return _kiem_ho_so(_ten_ho_so_chatgpt(s))
    return _kiem_ho_so_dich_vu(s)


def _email_tu_jwt(token: str) -> str:
    """Claim email trong JWT — giải mã payload KHÔNG xác thực chữ ký.

    Chỉ đọc claim của token mà dịch vụ vừa trả cho chính lượt này (dữ liệu
    chắc chắn để ghép khoá, không đoán chuỗi). Token hỏng → "".
    """
    try:
        phan = str(token or "").split(".")
        if len(phan) != 3:
            return ""
        dem = "=" * (-len(phan[1]) % 4)
        tai_lieu = json.loads(base64.urlsafe_b64decode(phan[1] + dem))
        if not isinstance(tai_lieu, dict):
            return ""
        ho_so = tai_lieu.get("https://api.openai.com/profile")
        if isinstance(ho_so, dict):
            email = ho_so.get("email")
            if isinstance(email, str) and email.strip():
                return email.strip()
        email = tai_lieu.get("email")
        return email.strip() if isinstance(email, str) else ""
    except Exception:
        return ""


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


def _kiem_ho_so_dich_vu(dinh_danh: Any) -> str:
    """Như ``_kiem_ho_so`` nhưng nhận cả EMAIL của tài khoản (lõi truyền email) và đổi sang
    tên hồ sơ dịch vụ chấp nhận. Chỉ dùng ở các đường NV145b (đăng nhập, mã 2FA, tiến độ,
    cookie); đường cũ (``bat_dau_dang_nhap``…) vẫn so khớp theo email nên giữ ``_kiem_ho_so``."""
    return _kiem_ho_so(_ten_ho_so_dich_vu(dinh_danh) if "@" in str(dinh_danh or "") else dinh_danh)


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



# ── NV145b: hợp đồng dang_nhap / nhap_ma_2fa / tien_do / trinh_duyet_cookie ────
# Nút «Lưu và đăng nhập» (POST /api/tai-khoan/da-luu/{id}/dang-nhap) và tự đăng
# nhập lại (thu_dang_nhap_lai) gọi module.dang_nhap(dong: DongDaTach).
# Mật khẩu/TOTP chỉ tồn tại trong bộ nhớ của lượt gọi: gửi sang KÉT CỦA DỊCH VỤ
# (POST /v1/accounts/saved) rồi ghi đè ngay — module KHÔNG giữ bản nào sau đó.
# Kho NV144 vẫn giữ bản ghi tài khoản (nhãn + bí mật mã hoá) vì lõi bắt buộc đọc
# bí mật từ kho trước khi gọi module; đó là kiến trúc lõi, NV145b không đổi.

def _so_nguyen_moi_truong(ten: str, mac_dinh: int, cho_phep_0: bool = False) -> int:
    try:
        v = int(str(os.environ.get(ten, "") or "").strip())
    except ValueError:
        return mac_dinh
    if v < 0 or (v == 0 and not cho_phep_0):
        return mac_dinh
    return v


def _tran_thu_moi_gio() -> int:
    """Trần số lần thử đăng nhập mỗi giờ cho mỗi tài khoản Google (mặc định 3)."""
    return _so_nguyen_moi_truong("C2A_DN_TRAN_THU_GIO", 3)


def _cua_so_tran_thu_giay() -> int:
    return _so_nguyen_moi_truong("C2A_DN_CUA_SO_GIO_GIAY", 3600)


def _cho_dang_nhap_giay() -> int:
    """Hết giờ chờ một lượt đăng nhập nền (mặc định 180s): không treo HTTP quá lâu."""
    return _so_nguyen_moi_truong("C2A_DN_CHO_DANG_NHAP_GIAY", 180)


def _nghi_luy_thua_co_so() -> int:
    """Cơ số nghỉ luỹ thừa sau các lần hỏng liên tiếp (giây, mặc định 5; 0 = tắt)."""
    return _so_nguyen_moi_truong("C2A_DN_NGHI_LUY_THUA_GIAY", 5, cho_phep_0=True)


def _phan_loai_loi_vinh_vien(loi: str) -> str | None:
    """Lỗi vĩnh viễn (sai mật khẩu / bị khoá / cần xác minh điện thoại) → mã lõi;
    None = lỗi tạm thời, được thử lại trong trần."""
    s = (loi or "").lower()
    if any(k in s for k in ("sai mật khẩu", "wrong password", "incorrect password",
                            "invalid password", "mật khẩu không đúng")):
        return "sai_mat_khau"
    if any(k in s for k in ("bị khoá", "bi khoa", "locked", "disabled", "vô hiệu",
                            "vo hieu", "suspended")):
        return "tai_khoan_bi_khoa"
    if any(k in s for k in ("xác minh", "xac minh", "điện thoại", "dien thoai",
                            "phone verification", "verify it's you")):
        return "can_xac_minh_dien_thoai"
    return None


# ── NV209: mã «cần người» của dịch vụ may-dang-nhap (bảng DUY NHẤT ở module;
# phân loại URL→mã lỗi nằm ở dịch vụ dich-vu/may-dang-nhap/src/auto_login.py) ──
_HUONG_DAN_CAN_NGUOI = {
    "xac_minh_selfie": "Google đòi xác minh bằng ảnh selfie — mở tài khoản trên "
        "điện thoại/máy tính của chủ tài khoản, hoàn tất một lần, rồi bấm "
        "Đăng nhập lại.",
    "xac_minh_dien_thoai": "Google đòi xác minh qua điện thoại (gọi điện/SMS) — "
        "mở app Gmail/Google trên điện thoại của chủ tài khoản, hoàn tất một "
        "lần, rồi bấm Đăng nhập lại.",
    "xac_minh_khac": "Google đòi một bước xác minh danh tính khác — mở tài "
        "khoản trên điện thoại/máy tính của chủ tài khoản, hoàn tất một lần, "
        "rồi bấm Đăng nhập lại.",
    "tai_khoan_bi_khoa": "Tài khoản Google đã bị khoá/vô hiệu hoá — kiểm tra "
        "thủ công, xử lý xong rồi lưu lại tài khoản để thử tiếp.",
    "dang_nhap_bi_tu_choi": "Google từ chối lượt đăng nhập — kiểm tra thủ công, "
        "xử lý xong rồi lưu lại tài khoản để thử tiếp.",
}
_HUONG_DAN_CAN_NGUOI_CHUNG = "Cần người xử lý bước xác minh của Google — mở tài " \
    "khoản trên điện thoại/máy tính của chủ tài khoản, hoàn tất một lần, rồi " \
    "bấm Đăng nhập lại."
# Mã lỗi vĩnh viễn trong nhóm cần người: không thể tự hết → đánh dấu, KHÔNG thử lại.
_LOI_VINH_VIEN_CAN_NGUOI = frozenset({"tai_khoan_bi_khoa", "dang_nhap_bi_tu_choi"})


def _sach_ma_loi_can_nguoi(ma: str) -> str:
    """Mã lỗi cần người từ dịch vụ: chỉ chữ/số/gạch dưới, ≤ 64 ký tự.
    Dịch vụ có thể thêm mã mới (tương lai) — vẫn đi qua với hướng dẫn chung."""
    ma = (ma or "").strip().lower()
    return ma if re.fullmatch(r"[a-z0-9_]{1,64}", ma) else ""


def _huong_dan_can_nguoi(ma_loi: str) -> str:
    """Hướng dẫn tiếng Việt cho mã cần người; mã lạ → hướng dẫn chung."""
    return _HUONG_DAN_CAN_NGUOI.get(ma_loi, _HUONG_DAN_CAN_NGUOI_CHUNG)


def _ket_qua_dang_nhap(thanh_cong: bool, ma_loi: str = "",
                       ma_trang_thai: int | None = None, can_nguoi: bool = False,
                       cho_2fa: bool = False, huong_dan: str = "",
                       jwt_moi: dict | None = None,
                       ho_so: str | None = None) -> "KetQuaDangNhap":
    """Dựng kết quả theo khuôn lõi (KetQuaDangNhap) + 3 tín hiệu mở rộng mà
    tuyến /da-luu/{id}/dang-nhap đọc qua getattr: can_nguoi, cho_2fa, huong_dan.
    KHÔNG bao giờ đính kèm mật khẩu/cookie/token — ngoại lệ NV210: ``jwt_moi``
    (JWT vừa lấy + ``ho_so``) chỉ để tầng ứng dụng đưa vào khoá sổ chatgpt-web
    qua đường cai {giu, them}; tuyến lõi không trả nó ra trình duyệt.
    NV214: ``ho_so`` (tên hồ sơ dịch vụ vừa đăng nhập xong) để tầng ứng dụng
    nối cookie phiên vào module sở hữu qua ``trinh_duyet_cookie``.
    """
    kq = KetQuaDangNhap(thanh_cong=thanh_cong, cookie=None, ma_loi=ma_loi,
                        ma_trang_thai=ma_trang_thai)
    object.__setattr__(kq, "can_nguoi", bool(can_nguoi))
    object.__setattr__(kq, "cho_2fa", bool(cho_2fa))
    object.__setattr__(kq, "huong_dan", str(huong_dan or ""))
    object.__setattr__(kq, "jwt_moi",
                       dict(jwt_moi) if isinstance(jwt_moi, dict) else None)
    object.__setattr__(kq, "ho_so_dang_nhap", str(ho_so or ""))
    return kq


#: Tên miền → đường xuất cookie phiên Google trên dịch vụ (dùng cho trinh_duyet_cookie)
_MIEN_SANG_DUONG_COOKIE = {
    "gemini.google.com": "/v1/gemini-web/{ho_so}/cookies",
}

#: Regex kiểm chuỗi cookie 'k1=v1; k2=v2' (không ký tự điều khiển)
_RE_CHUOI_COOKIE = re.compile(r"^[ -~]+$")


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
        #: Móc nối tầng ứng dụng (NV210): callable(email, token, ho_so) được gọi
        #: sau khi luồng ChatGPT thành công để đưa JWT vào khoá sổ chatgpt-web
        #: qua đường cai {giu, them}. Mặc định None (không gắn).
        self._moc_gan_jwt = None

    def dat_moc_gan_jwt(self, ham) -> None:
        """Đặt móc gắn JWT (NV210).

        Tầng khởi động ứng dụng nối: ``mod.dat_moc_gan_jwt(gan_jwt_vao_khoa)``
        để sau mỗi lần đăng nhập ChatGPT thành công, JWT mới + ``ho_so`` được
        đưa vào khoá sổ chatgpt-web (thay khoá cũ có cùng claim email hoặc thêm
        mới). Móc không được ném; lỗi móc không làm hỏng kết quả đăng nhập.
        """
        self._moc_gan_jwt = ham if callable(ham) else None

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
            {"hanh_dong": k, "rui_ro": "doc", "mo_ta": f"Hành động đọc «{k}» trên dịch vụ máy đăng nhập."}
            for k, v in HANH_DONG.items()
            if v[2] == "doc"
        ]
        ghi_actions = [
            {"hanh_dong": k, "rui_ro": "ghi", "mo_ta": f"Hành động ghi «{k}» trên dịch vụ máy đăng nhập."}
            for k, v in HANH_DONG.items()
            if v[2] == "ghi"
        ]
        return [
            {
                "id": f"{self.tien_to}.cong_cu_doc",
                "ten": "Công cụ đọc trạng thái máy đăng nhập",
                "loai": "cong_cu",
                "khu": "he_thong",
                "thuoc_tinh": {"dich_vu": "may-dang-nhap"},
                "tinh_nang": doc_actions,
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": f"{self.tien_to}.cong_cu_ghi",
                "ten": "Công cụ thao tác máy đăng nhập",
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

    # -- NV207: làm mới JWT qua hồ sơ trình duyệt (module chatgpt-web gọi) -----

    async def danh_sach_ho_so(self) -> dict:
        """GET /v1/profiles — danh sách hồ sơ trình duyệt của dịch vụ.

        NV207: vòng làm mới JWT của chatgpt-web dùng để kiểm hồ sơ đã lưu
        còn tồn tại trước khi gọi làm mới (ghép chắc chắn, không đoán chuỗi).
        """
        return await self._goi("GET", "/v1/profiles")

    async def lam_moi_jwt(self, ho_so: str) -> dict:
        """GET /v1/chatgpt/{ho_so}/refresh-jwt — làm mới JWT qua hồ sơ.

        NV207: `ho_so` đã kiểm khuôn (chống path traversal). Kết quả theo hợp
        đồng của dịch vụ: {"ok": True, "access_token": ...} hoặc
        {"ok": False, "ma_loi": ...} — HTTP 401 (ma_loi "sai_khoa_dich_vu")
        nghĩa là hồ sơ đã đăng xuất (lỗi vĩnh viễn phía hồ sơ).
        """
        try:
            hs_sach = _kiem_ho_so(ho_so)
        except ValueError:
            return {"ok": False, "ma_loi": "tham_so_khong_hop_le",
                    "loi": "Tên hồ sơ không hợp lệ", "nguon_ngoai": True}
        return await self._goi("GET", f"/v1/chatgpt/{hs_sach}/refresh-jwt")

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

    # ── NV145b: hợp đồng dang_nhap / nhap_ma_2fa / tien_do ────────────────────

    def _nv145b_trang_thai(self) -> dict:
        """Sổ theo dõi lượt thử theo tài khoản (khởi tạo lười)."""
        st = self.__dict__.get("_nv145b")
        if st is None:
            st = {"thu": {}, "vinh_vien": {}, "hong_lt": {}}
            self.__dict__["_nv145b"] = st
        return st

    async def dang_nhap(self, dong: DongDaTach) -> KetQuaDangNhap:
        """Đăng nhập qua dịch vụ may-dang-nhap (NV145b + NV210).

        Nhận email/mật khẩu/TOTP qua DongDaTach (BiMatTam/BiMatTotp), gửi sang
        KÉT CỦA DỊCH VỤ (POST /v1/accounts/saved) rồi ghi đè bộ đệm bí mật ngay.
        Chọn luồng theo module sở hữu của tài khoản (``dong.module_cua_tai_khoan``,
        dữ liệu — KHÔNG đoán theo chuỗi email):
          - module_id thuộc luồng ChatGPT (vd ``chatgpt-web``) → luồng
            ``POST /v1/chatgpt/onboard`` (NV210);
          - còn lại → luồng Google ``auto-login-saved`` (NV145b).
        Trả kết quả theo khuôn lõi: thành công / cần 2FA / cần người / lỗi có mã.
        KHÔNG trả mật khẩu, cookie, token trong kết quả hay log — ngoại lệ
        NV210: ``jwt_moi`` chỉ để tầng ứng dụng đưa vào khoá sổ chatgpt-web.
        """
        # 0. Kiểm tra tham số trước khi chạm bí mật
        email_tho = str(getattr(dong, "email", "") or "").strip()
        try:
            _kiem_email(email_tho)
        except ValueError:
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        mat_khau = getattr(dong, "mat_khau", None)
        if not isinstance(mat_khau, BiMatTam) or mat_khau.da_xoa():
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        # NV223: kiểm «đăng nhập bằng» + cặp (luồng, cách đăng nhập) hỗ trợ
        # TRƯỚC khi chạm bí mật — sai giá trị / cặp không hỗ trợ / thiếu bí
        # mật Google → dừng ngay, dịch vụ không bị gọi, bí mật không bị lấy.
        dang_nhap_bang = str(
            getattr(dong, "dang_nhap_bang", "") or "").strip().lower()
        if dang_nhap_bang not in _DANG_NHAP_BANG_HOP_LE:
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        dich_vu_key = None
        if dang_nhap_bang:
            luong = _LUONG_THEO_MODULE_ID.get(
                str(getattr(dong, "module_cua_tai_khoan", "") or "").strip())
            dich_vu_key = _DUONG_ONBOARD_THEO_LUONG.get(
                (luong, dang_nhap_bang)) if luong else None
            if dich_vu_key is None:
                return _ket_qua_dang_nhap(
                    False, ma_loi="khong_ho_tro", can_nguoi=True,
                    huong_dan="Cách đăng nhập này chưa được hỗ trợ cho tài "
                              "khoản thuộc module này — chọn cách đăng nhập "
                              "khác hoặc đăng nhập thủ công.")
            if dang_nhap_bang == "google":
                google_email = str(
                    getattr(dong, "google_email", "") or "").strip()
                google_mk = getattr(dong, "google_mat_khau", None)
                try:
                    _kiem_email(google_email)
                except ValueError:
                    return _ket_qua_dang_nhap(
                        False, ma_loi="tham_so_khong_hop_le")
                if not isinstance(google_mk, BiMatTam) or google_mk.da_xoa():
                    return _ket_qua_dang_nhap(
                        False, ma_loi="thieu_tai_khoan_google", can_nguoi=True,
                        huong_dan="Tài khoản khai đăng nhập qua Google nhưng "
                                  "chưa gắn được tài khoản Google đã lưu — "
                                  "kiểm tra lại trường «Đăng nhập bằng».")
        khoa_tk = email_tho.lower()
        st = self._nv145b_trang_thai()

        # 1. Lỗi vĩnh viễn đã đánh dấu: dừng hẳn, không thử lại
        vv = st["vinh_vien"].get(khoa_tk)
        if vv:
            return _ket_qua_dang_nhap(
                False, ma_loi=vv, can_nguoi=True,
                huong_dan="Tài khoản đã bị đánh dấu «cần người» (lỗi vĩnh viễn: "
                           "sai mật khẩu / bị khoá / cần xác minh điện thoại). "
                           "Kiểm tra thủ công rồi lưu lại tài khoản để thử tiếp.")

        # 2. Trần thử / giờ / tài khoản (chống nhà cung cấp khoá tài khoản)
        bay_gio = time.time()
        cua_so = _cua_so_tran_thu_giay()
        lich_su = [t for t in st["thu"].get(khoa_tk, []) if bay_gio - t < cua_so]
        if len(lich_su) >= _tran_thu_moi_gio():
            return _ket_qua_dang_nhap(
                False, ma_loi="qua_tran_thu",
                huong_dan="Đã thử quá trần cho phép trong một giờ cho tài khoản "
                           "này — đợi hết giờ rồi thử lại, tránh bị khoá.")
        # Nghỉ luỹ thừa sau các lần hỏng liên tiếp
        hong_lt = st["hong_lt"].get(khoa_tk, 0)
        co_so_nghi = _nghi_luy_thua_co_so()
        if hong_lt > 0 and co_so_nghi > 0:
            await asyncio.sleep(min(2 ** min(hong_lt, 6) * co_so_nghi, 120))
        lich_su.append(time.time())
        st["thu"][khoa_tk] = lich_su

        # 3. Lấy bí mật rồi GHI ĐÈ NGAY (lay_va_xoa ghi đè bộ đệm bằng 0)
        try:
            mk = mat_khau.lay_va_xoa()
        except Exception:
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        if not mk:
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        totp = getattr(dong, "totp", None)
        # Cần KHOÁ TOTP GỐC cho két dịch vụ (BiMatTotp.lay() chỉ trả mã 6 số
        # tại thời điểm gọi — mã đó hết hạn sau 30s, không dùng được cho lượt
        # đăng nhập nền kéo dài nhiều phút của dịch vụ).
        totp_secret = BiMatTam.lay(totp) if isinstance(totp, BiMatTotp) else ""

        # 4. Chọn luồng: NV223 — khi tài khoản KHAI «đăng nhập bằng» thì đi
        # đường onboard theo bảng khai báo; chưa khai → giữ hai đường cũ.
        if dich_vu_key is not None:
            return await self._dang_nhap_theo_bang(
                dong, email_tho, khoa_tk, st, hong_lt, mk, totp_secret,
                dang_nhap_bang, dich_vu_key)
        if _la_luong_chatgpt(getattr(dong, "module_cua_tai_khoan", "")):
            return await self._dang_nhap_chatgpt(
                email_tho, khoa_tk, st, hong_lt, mk, totp_secret)
        return await self._dang_nhap_google(
            email_tho, khoa_tk, st, hong_lt, mk, totp_secret)

    async def _dang_nhap_google(self, email_tho: str, khoa_tk: str, st: dict,
                                hong_lt: int, mk: str,
                                totp_secret: str) -> KetQuaDangNhap:
        """Luồng Google (NV145b): auto-login-saved + chờ kết quả.

        Nhận bí mật thô (đã lấy ra khỏi BiMatTam ở ``dang_nhap``); PHẢI ghi đè
        trước khi hàm trả về, mọi nhánh.
        """
        # 4. Gửi sang KÉT CỦA DỊCH VỤ (kho bí mật gốc cho lượt đăng nhập này),
        # rồi huỷ bản sao cục bộ ngay sau lời gọi.
        try:
            luu = await self._goi("POST", "/v1/accounts/saved", json_body={
                "email": email_tho, "password": mk,
                "totp_secret": totp_secret, "loai": "google"})
        finally:
            mk = "\x00" * len(mk)
            totp_secret = "\x00" * len(totp_secret)
            del mk, totp_secret
        if not isinstance(luu, dict) or not luu.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(luu, khoa_tk)

        # 5. Bắt đầu lượt đăng nhập nền trên dịch vụ (trả ngay phía dịch vụ)
        bat_dau = await self._goi(
            "POST", "/v1/session/auto-login-saved",
            json_body={"profile": _ten_ho_so_dich_vu(email_tho)})
        if not isinstance(bat_dau, dict) or not bat_dau.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(bat_dau, khoa_tk)
        if bat_dau.get("state") == "failed":
            return self._ket_qua_tu_phien_dich_vu(bat_dau, khoa_tk, st)

        # 6. Chờ kết quả trong trần (nghỉ luỹ thừa giữa các lần hỏi trạng thái)
        return await self._cho_ket_qua_dang_nhap(email_tho, khoa_tk, st)

    async def _dang_nhap_chatgpt(self, email_tho: str, khoa_tk: str, st: dict,
                                 hong_lt: int, mk: str,
                                 totp_secret: str) -> KetQuaDangNhap:
        """Luồng ChatGPT (NV210): onboard qua ``POST /v1/chatgpt/onboard``.

        Dịch vụ tự bù mật khẩu/TOTP từ két của nó (``bu_credential``) nên lời
        gọi onboard chỉ cần ``profile`` + ``email``. Thành công → lấy JWT mới
        qua ``refresh-jwt``, trả trong ``jwt_moi`` (kèm ``ho_so``) để tầng ứng
        dụng đưa vào khoá sổ chatgpt-web; đồng thời gọi móc ``_moc_gan_jwt``
        nếu đã nối. Nhận bí mật thô; PHẢI ghi đè trước khi hàm trả về.
        """
        hs = _ten_ho_so_chatgpt(email_tho)
        if not hs:
            mk = "\x00" * len(mk)
            totp_secret = "\x00" * len(totp_secret)
            del mk, totp_secret
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        # 4. Gửi sang KÉT CỦA DỊCH VỤ (kho ``openai`` — tách khỏi kho Google
        # của cùng email), rồi huỷ bản sao cục bộ ngay sau lời gọi.
        try:
            luu = await self._goi("POST", "/v1/accounts/saved", json_body={
                "email": email_tho, "password": mk,
                "totp_secret": totp_secret, "loai": "openai"})
        finally:
            mk = "\x00" * len(mk)
            totp_secret = "\x00" * len(totp_secret)
            del mk, totp_secret
        if not isinstance(luu, dict) or not luu.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(luu, khoa_tk)

        # 5. Bắt đầu lượt onboard nền (dịch vụ tự bù bí mật từ két)
        bat_dau = await self._goi(
            "POST", "/v1/chatgpt/onboard",
            json_body={"profile": hs, "email": email_tho,
                       "password": "", "totp_secret": ""})
        if not isinstance(bat_dau, dict) or not bat_dau.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(bat_dau, khoa_tk)
        if bat_dau.get("state") == "failed":
            return self._ket_qua_chatgpt_tu_phien(bat_dau, khoa_tk, st)

        # 6. Chờ kết quả trong trần
        return await self._cho_ket_qua_chatgpt(email_tho, khoa_tk, st, hs)

    async def _dang_nhap_theo_bang(self, dong: DongDaTach, email_tho: str,
                                   khoa_tk: str, st: dict, hong_lt: int,
                                   mk: str, totp_secret: str,
                                   dang_nhap_bang: str,
                                   dich_vu_key: str) -> KetQuaDangNhap:
        """Luồng NV223: onboard theo ``dang_nhap_bang`` + bảng khai báo.

        - «google»: bí mật gửi két dịch vụ là của TÀI KHOẢN GOOGLE được chọn
          (loai «google», email Google — dịch vụ tự bù khi onboard theo
          email); bí mật của chính tài khoản đích KHÔNG dùng, huỷ ngay.
        - «truc_tiep»: bí mật của chính tài khoản (loai «openai»).
        Hồ sơ luôn theo email của TÀI KHOẢN ĐÍCH. Nhận bí mật thô; PHẢI ghi
        đè trước khi hàm trả về, mọi nhánh.
        """
        muc = DICH_VU_DANG_NHAP[dich_vu_key]
        if dang_nhap_bang == "google":
            mk = "\x00" * len(mk)
            totp_secret = "\x00" * len(totp_secret)
            del mk, totp_secret
            email_dn = str(getattr(dong, "google_email", "") or "").strip()
            google_mk = getattr(dong, "google_mat_khau", None)
            try:
                mk_dn = google_mk.lay_va_xoa() \
                    if isinstance(google_mk, BiMatTam) else ""
            except Exception:
                mk_dn = ""
            google_totp = getattr(dong, "google_totp", None)
            totp_dn = BiMatTam.lay(google_totp) \
                if isinstance(google_totp, BiMatTotp) else ""
            loai_ket = "google"
            if not mk_dn:
                totp_dn = "\x00" * len(totp_dn)
                del totp_dn
                return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        else:
            email_dn, mk_dn, totp_dn = email_tho, mk, totp_secret
            loai_ket = "openai"
        hs = _ten_ho_so_theo_dich_vu(dich_vu_key, email_tho)
        if not hs:
            mk_dn = "\x00" * len(mk_dn)
            totp_dn = "\x00" * len(totp_dn)
            del mk_dn, totp_dn
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        # Gửi sang KÉT CỦA DỊCH VỤ rồi huỷ bản sao cục bộ ngay sau lời gọi.
        try:
            luu = await self._goi("POST", "/v1/accounts/saved", json_body={
                "email": email_dn, "password": mk_dn,
                "totp_secret": totp_dn, "loai": loai_ket})
        finally:
            mk_dn = "\x00" * len(mk_dn)
            totp_dn = "\x00" * len(totp_dn)
            del mk_dn, totp_dn
        if not isinstance(luu, dict) or not luu.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(luu, khoa_tk)
        # Bắt đầu lượt onboard (dịch vụ tự bù bí mật từ két theo email)
        bat_dau = await self._goi(
            muc["bat_dau"][0], muc["bat_dau"][1],
            json_body={"profile": hs, "email": email_dn,
                       "password": "", "totp_secret": ""})
        if not isinstance(bat_dau, dict) or not bat_dau.get("ok"):
            st["hong_lt"][khoa_tk] = hong_lt + 1
            return self._loi_dich_vu_thanh_ket_qua(bat_dau, khoa_tk)
        if str(bat_dau.get("state") or "") in (
                "failed", "need_code", "need_captcha", "need_tap",
                "need_human", "need_manual"):
            return self._ket_qua_onboard_tu_phien(bat_dau, khoa_tk, st, hs)
        return await self._cho_ket_qua_onboard(
            email_tho, khoa_tk, st, hs, dich_vu_key)

    async def _cho_ket_qua_onboard(self, email_tho: str, khoa_tk: str,
                                   st: dict, hs: str,
                                   dich_vu_key: str) -> KetQuaDangNhap:
        """Hỏi trạng thái onboard (đường theo bảng) tới kết quả cuối/trần.

        Trạng thái cuối cần người (need_manual — vd hàng rào chống bot phía
        dịch vụ) trả NGAY khi thấy: không thử lại vòng, không tự vượt.
        """
        muc = DICH_VU_DANG_NHAP[dich_vu_key]
        duong_tt = muc["trang_thai"][1].format(ho_so=hs)
        han = time.time() + _cho_dang_nhap_giay()
        nghi = 2.0
        while True:
            tt = await self._goi(muc["trang_thai"][0], duong_tt)
            if isinstance(tt, dict) and tt.get("ok"):
                trang_thai = str(tt.get("state") or "none")
                if trang_thai == "success":
                    return await self._hoan_tat_onboard(
                        email_tho, hs, khoa_tk, st, dich_vu_key)
                if trang_thai in ("need_code", "need_captcha", "need_tap",
                                  "need_human", "need_manual", "failed"):
                    return self._ket_qua_onboard_tu_phien(tt, khoa_tk, st, hs)
            if time.time() >= han:
                break
            await asyncio.sleep(min(nghi, max(0.0, han - time.time())))
            nghi = min(nghi * 1.5, 10.0)
        return _ket_qua_dang_nhap(
            False, ma_loi="dang_chay",
            huong_dan="Lượt đăng nhập vẫn đang chạy — bấm «Kiểm tra trạng "
                      "thái» để hỏi tiếp, không bấm đăng nhập lại.")

    async def _hoan_tat_onboard(self, email_tho: str, hs: str, khoa_tk: str,
                                st: dict, dich_vu_key: str) -> KetQuaDangNhap:
        """Đóng gói kết quả khi onboard NV223 thành công, theo khoá dịch vụ:
        chatgpt → JWT qua refresh-jwt (tái dùng NV210); openai → access
        token qua endpoint token; claude-web/grok-web → gắn ``ho_so`` để lõi
        nối cookie phiên vào module sở hữu (NV214)."""
        if dich_vu_key == "chatgpt":
            return await self._lay_jwt_chatgpt(email_tho, hs, khoa_tk, st)
        st["hong_lt"].pop(khoa_tk, None)
        if dich_vu_key == "openai":
            muc = DICH_VU_DANG_NHAP[dich_vu_key]
            kq = await self._goi(
                muc["cookie"][0], muc["cookie"][1].format(ho_so=hs))
            token = str(kq.get("access_token") or "") \
                if isinstance(kq, dict) else ""
            if not token:
                st["hong_lt"][khoa_tk] = st["hong_lt"].get(khoa_tk, 0) + 1
                return _ket_qua_dang_nhap(
                    False, ma_loi="khong_lay_duoc_jwt",
                    huong_dan="Onboard xong nhưng chưa lấy được token — bấm "
                              "«Kiểm tra trạng thái» để thử lại.")
            email = _email_tu_jwt(token) \
                or str(kq.get("email") or "") or email_tho
            jwt_moi = {"access_token": token, "ho_so": hs, "email": email}
            moc = self._moc_gan_jwt
            if callable(moc):
                try:
                    moc(email, token, hs)
                except Exception as e:
                    log.warning("openai-native: móc gắn token vào khoá sổ "
                                "lỗi: %s", type(e).__name__)
            return _ket_qua_dang_nhap(True, jwt_moi=jwt_moi)
        return _ket_qua_dang_nhap(True, ho_so=hs)

    def _ket_qua_onboard_tu_phien(self, phien: dict, khoa_tk: str,
                                  st: dict, ho_so: str = "") -> KetQuaDangNhap:
        """Ánh xạ trạng thái onboard (bảng NV223) → kết quả lõi.

        Thông điệp trung tính (không nêu tên nhà cung cấp — đường này dùng
        chung). ``need_manual``/``need_captcha``: KHÔNG tự vượt chống bot,
        KHÔNG giải CAPTCHA — trả «cần người». Lỗi vĩnh viễn dùng đúng bộ
        phân loại chung ``_phan_loai_loi_vinh_vien``.
        """
        trang_thai = str(phien.get("state") or "none")
        loi = str(phien.get("error") or phien.get("message") or "")
        if trang_thai == "success":
            st["hong_lt"].pop(khoa_tk, None)
            return _ket_qua_dang_nhap(True, ho_so=ho_so)
        if trang_thai == "need_code":
            return _ket_qua_dang_nhap(
                False, ma_loi="cho_2fa", cho_2fa=True,
                huong_dan="Dịch vụ đang chờ mã 2FA — nhập mã vào ô «Mã 2FA» "
                          "rồi bấm gửi (dịch vụ tự điền mã TOTP nếu tài "
                          "khoản đăng nhập đã lưu khoá TOTP).")
        if trang_thai == "need_captcha":
            st["vinh_vien"][khoa_tk] = "gap_captcha"
            return _ket_qua_dang_nhap(
                False, ma_loi="gap_captcha", can_nguoi=True,
                huong_dan="Trang đăng nhập hiện thử thách CAPTCHA — hệ "
                          "thống không tự giải. Mở trình duyệt xử lý thủ "
                          "công rồi đăng nhập lại.")
        if trang_thai == "need_tap":
            return _ket_qua_dang_nhap(
                False, ma_loi="can_nguoi", can_nguoi=True,
                huong_dan="Đang chờ bạn xác nhận trên điện thoại — bấm xác "
                          "nhận rồi bấm «Kiểm tra lại».")
        if trang_thai == "need_manual":
            # Hàng rào chống bot phía dịch vụ: cần người, không thử lại vòng
            return _ket_qua_dang_nhap(
                False, ma_loi="can_nguoi", can_nguoi=True,
                huong_dan="Trang đăng nhập cần người xử lý thủ công (hàng "
                          "rào chống bot) — hệ thống không tự vượt. Xử lý "
                          "thủ công rồi đăng nhập lại.")
        if trang_thai == "need_human":
            ma_loi = _sach_ma_loi_can_nguoi(str(phien.get("error") or "")) \
                or "can_nguoi"
            if ma_loi in _LOI_VINH_VIEN_CAN_NGUOI:
                st["vinh_vien"][khoa_tk] = ma_loi
            return _ket_qua_dang_nhap(
                False, ma_loi=ma_loi, can_nguoi=True,
                huong_dan=_huong_dan_can_nguoi(ma_loi))
        if trang_thai == "failed":
            ma_vv = _phan_loai_loi_vinh_vien(loi)
            if ma_vv:
                st["vinh_vien"][khoa_tk] = ma_vv
                return _ket_qua_dang_nhap(
                    False, ma_loi=ma_vv, can_nguoi=True,
                    huong_dan="Lỗi vĩnh viễn, hệ thống dừng thử để bảo vệ "
                              "tài khoản — kiểm tra thủ công rồi lưu lại "
                              "tài khoản.")
            st["hong_lt"][khoa_tk] = st["hong_lt"].get(khoa_tk, 0) + 1
            return _ket_qua_dang_nhap(False, ma_loi="dang_nhap_that_bai")
        return _ket_qua_dang_nhap(False, ma_loi="dich_vu_loi")

    def _ket_qua_chatgpt_tu_phien(self, phien: dict, khoa_tk: str,
                                 st: dict) -> KetQuaDangNhap:
        """Ánh xạ trạng thái phiên ChatGPT → kết quả theo khuôn lõi.

        Phân loại lỗi vĩnh viễn dùng đúng bộ phân loại chung
        ``_phan_loai_loi_vinh_vien`` (KHÔNG thêm danh sách từ khoá riêng cho
        ChatGPT). Lỗi vĩnh viễn → «cần người», không thử lại. Nhánh ``success``
        trả thành công trần — JWT lấy riêng ở ``_lay_jwt_chatgpt``.
        """
        trang_thai = str(phien.get("state") or "none")
        loi = str(phien.get("error") or phien.get("message") or "")
        if trang_thai == "success":
            st["hong_lt"].pop(khoa_tk, None)
            return _ket_qua_dang_nhap(True)
        if trang_thai == "need_code":
            return _ket_qua_dang_nhap(
                False, ma_loi="cho_2fa", cho_2fa=True,
                huong_dan="ChatGPT đang chờ mã 2FA — nhập mã vào ô «Mã 2FA» "
                           "rồi bấm gửi (dịch vụ tự điền mã TOTP nếu tài khoản "
                           "đã lưu khoá TOTP).")
        if trang_thai == "need_captcha":
            # KHÔNG giải CAPTCHA: đánh dấu cần người
            st["vinh_vien"][khoa_tk] = "gap_captcha"
            return _ket_qua_dang_nhap(
                False, ma_loi="gap_captcha", can_nguoi=True,
                huong_dan="ChatGPT hiện thử thách CAPTCHA — hệ thống không tự "
                           "giải. Mở trình duyệt xử lý thủ công rồi đăng nhập lại.")
        if trang_thai == "need_tap":
            return _ket_qua_dang_nhap(
                False, ma_loi="can_nguoi", can_nguoi=True,
                huong_dan="ChatGPT đang chờ bạn chạm «Có, đó là tôi» trên điện "
                           "thoại — bấm trên điện thoại rồi bấm «Kiểm tra lại».")
        if trang_thai == "failed":
            ma_vv = _phan_loai_loi_vinh_vien(loi)
            if ma_vv:
                st["vinh_vien"][khoa_tk] = ma_vv
                return _ket_qua_dang_nhap(
                    False, ma_loi=ma_vv, can_nguoi=True,
                    huong_dan="Lỗi vĩnh viễn, hệ thống dừng thử để bảo vệ tài "
                               "khoản — kiểm tra thủ công rồi lưu lại tài khoản.")
            st["hong_lt"][khoa_tk] = st["hong_lt"].get(khoa_tk, 0) + 1
            return _ket_qua_dang_nhap(False, ma_loi="dang_nhap_that_bai")
        return _ket_qua_dang_nhap(False, ma_loi="dich_vu_loi")

    async def _cho_ket_qua_chatgpt(self, email_tho: str, khoa_tk: str,
                                   st: dict, hs: str) -> KetQuaDangNhap:
        """Hỏi trạng thái onboard ChatGPT tới khi có kết quả cuối hoặc hết trần."""
        han = time.time() + _cho_dang_nhap_giay()
        nghi = 2.0
        while True:
            tt = await self._goi("GET", f"/v1/chatgpt/{hs}/onboard-status")
            if isinstance(tt, dict) and tt.get("ok"):
                trang_thai = str(tt.get("state") or "none")
                if trang_thai == "success":
                    return await self._lay_jwt_chatgpt(
                        email_tho, hs, khoa_tk, st)
                if trang_thai in ("need_code", "need_captcha", "failed"):
                    return self._ket_qua_chatgpt_tu_phien(tt, khoa_tk, st)
                if trang_thai == "need_tap" and time.time() >= han:
                    return self._ket_qua_chatgpt_tu_phien(tt, khoa_tk, st)
            if time.time() >= han:
                break
            await asyncio.sleep(min(nghi, max(0.0, han - time.time())))
            nghi = min(nghi * 1.5, 10.0)
        return _ket_qua_dang_nhap(
            False, ma_loi="dang_chay",
            huong_dan="Lượt đăng nhập ChatGPT vẫn đang chạy — bấm «Kiểm tra "
                       "trạng thái» để hỏi tiếp, không bấm đăng nhập lại.")

    async def _lay_jwt_chatgpt(self, email_tho: str, hs: str,
                               khoa_tk: str, st: dict) -> KetQuaDangNhap:
        """Lấy JWT mới qua ``GET /v1/chatgpt/{ho_so}/refresh-jwt`` sau onboard
        thành công; đóng gói ``jwt_moi`` + gọi móc gắn khoá sổ."""
        jwt = await self.lam_moi_jwt(hs)
        if not isinstance(jwt, dict) or not jwt.get("ok") \
                or not jwt.get("access_token"):
            st["hong_lt"][khoa_tk] = st["hong_lt"].get(khoa_tk, 0) + 1
            return _ket_qua_dang_nhap(
                False, ma_loi="khong_lay_duoc_jwt",
                huong_dan="Onboard xong nhưng chưa lấy được JWT — bấm "
                           "«Kiểm tra trạng thái» để thử lại.")
        token = str(jwt.get("access_token") or "")
        email = _email_tu_jwt(token) or str(jwt.get("captured_email") or "") \
            or email_tho
        jwt_moi = {"access_token": token, "ho_so": hs, "email": email}
        moc = self._moc_gan_jwt
        if callable(moc):
            try:
                moc(email, token, hs)
            except Exception as e:
                log.warning("chatgpt: móc gắn JWT vào khoá sổ lỗi: %s",
                            type(e).__name__)
        return _ket_qua_dang_nhap(True, jwt_moi=jwt_moi)

    def _loi_dich_vu_thanh_ket_qua(self, kq_goi: Any, khoa_tk: str) -> KetQuaDangNhap:
        """Lỗi gọi dịch vụ (mạng/HTTP/xác thực) → kết quả lõi, không lộ chi tiết."""
        ma = kq_goi.get("ma_loi") if isinstance(kq_goi, dict) else ""
        if ma == "sai_khoa_dich_vu":
            return _ket_qua_dang_nhap(
                False, ma_loi="sai_khoa_dich_vu",
                huong_dan="Khoá dịch vụ may-dang-nhap không đúng — kiểm tra cấu "
                           "hình module (khoa_dich_vu).")
        if ma in ("khong_noi_duoc_dich_vu", "het_gio"):
            return _ket_qua_dang_nhap(
                False, ma_loi="dich_vu_sap",
                huong_dan="Không nối được dịch vụ may-dang-nhap — kiểm tra "
                           "container dịch vụ có đang chạy không.")
        return _ket_qua_dang_nhap(False, ma_loi="dich_vu_loi")

    def _ket_qua_tu_phien_dich_vu(self, phien: dict, khoa_tk: str,
                                 st: dict, ho_so: str = "") -> KetQuaDangNhap:
        """Ánh xạ trạng thái phiên dịch vụ → kết quả theo khuôn lõi."""
        trang_thai = str(phien.get("state") or "none")
        loi = str(phien.get("error") or phien.get("message") or "")
        if trang_thai == "success":
            st["hong_lt"].pop(khoa_tk, None)
            return _ket_qua_dang_nhap(True, ho_so=ho_so)
        if trang_thai == "need_code":
            return _ket_qua_dang_nhap(
                False, ma_loi="cho_2fa", cho_2fa=True,
                huong_dan="Google đang chờ mã 2FA — nhập mã vào ô «Mã 2FA» rồi "
                           "bấm gửi (dịch vụ tự điền mã TOTP nếu tài khoản đã "
                           "lưu khoá TOTP).")
        if trang_thai == "need_captcha":
            # KHÔNG giải CAPTCHA (spec NV145b): đánh dấu cần người
            st["vinh_vien"][khoa_tk] = "gap_captcha"
            return _ket_qua_dang_nhap(
                False, ma_loi="gap_captcha", can_nguoi=True,
                huong_dan="Google hiện thử thách CAPTCHA — hệ thống không tự "
                           "giải. Mở trình duyệt xử lý thủ công rồi đăng nhập lại.")
        if trang_thai == "failed":
            ma_vv = _phan_loai_loi_vinh_vien(loi)
            if ma_vv:
                st["vinh_vien"][khoa_tk] = ma_vv
                return _ket_qua_dang_nhap(
                    False, ma_loi=ma_vv, can_nguoi=True,
                    huong_dan="Lỗi vĩnh viễn, hệ thống dừng thử để bảo vệ tài "
                               "khoản — kiểm tra thủ công rồi lưu lại tài khoản.")
            st["hong_lt"][khoa_tk] = st["hong_lt"].get(khoa_tk, 0) + 1
            return _ket_qua_dang_nhap(False, ma_loi="dang_nhap_that_bai")
        # need_tap: Google gửi yêu cầu chạm lên điện thoại — chờ người bấm
        if trang_thai == "need_tap":
            return _ket_qua_dang_nhap(
                False, ma_loi="can_nguoi", can_nguoi=True,
                huong_dan="Google đang chờ bạn chạm «Có, đó là tôi» trên điện "
                           "thoại — bấm trên điện thoại rồi bấm «Kiểm tra lại».")
        # NV209: dịch vụ đã nhận ra trang xác minh danh tính và DỪNG
        # (need_human kèm mã lỗi) — module ánh xạ sang can_nguoi + hướng dẫn
        # tiếng Việt cụ thể. Lỗi vĩnh viễn (bị khoá, bị từ chối) → đánh dấu,
        # KHÔNG thử lại; lỗi có thể tự hết (selfie, điện thoại) → được thử lại.
        if trang_thai == "need_human":
            ma_loi = _sach_ma_loi_can_nguoi(str(phien.get("error") or "")) \
                or "can_nguoi"
            if ma_loi in _LOI_VINH_VIEN_CAN_NGUOI:
                st["vinh_vien"][khoa_tk] = ma_loi
            return _ket_qua_dang_nhap(
                False, ma_loi=ma_loi, can_nguoi=True,
                huong_dan=_huong_dan_can_nguoi(ma_loi))
        return _ket_qua_dang_nhap(False, ma_loi="dich_vu_loi")

    async def _cho_ket_qua_dang_nhap(self, email_tho: str, khoa_tk: str,
                                    st: dict) -> KetQuaDangNhap:
        """Hỏi trạng thái phiên tới khi có kết quả cuối hoặc hết trần chờ."""
        try:
            hs = _kiem_ho_so_dich_vu(email_tho)
        except ValueError:
            return _ket_qua_dang_nhap(False, ma_loi="tham_so_khong_hop_le")
        han = time.time() + _cho_dang_nhap_giay()
        nghi = 2.0
        while True:
            # Hỏi ngay lần đầu (lỗi nhanh như sai mật khẩu trả về sớm),
            # các lần sau nghỉ luỹ thừa giữa các lần hỏi.
            tt = await self._goi("GET", f"/v1/session/{hs}/auto-login-status")
            if isinstance(tt, dict) and tt.get("ok"):
                trang_thai = str(tt.get("state") or "none")
                # NV209: need_human là trạng thái cuối — trả ngay, không chờ hết trần
                if trang_thai in ("success", "need_code", "need_captcha",
                                  "need_human", "failed"):
                    return self._ket_qua_tu_phien_dich_vu(tt, khoa_tk, st, hs)
                if trang_thai == "need_tap" and time.time() >= han:
                    # Hết trần mà người dùng chưa chạm điện thoại
                    return self._ket_qua_tu_phien_dich_vu(tt, khoa_tk, st, hs)
            # Chưa có kết quả cuối (starting/running/need_tap/none/lỗi hỏi):
            # nghỉ rồi hỏi tiếp tới hết trần.
            if time.time() >= han:
                break
            await asyncio.sleep(min(nghi, max(0.0, han - time.time())))
            nghi = min(nghi * 1.5, 10.0)
        # Hết trần mà phiên vẫn chạy: trả ngay, giao diện hỏi tiếp qua tien_do
        return _ket_qua_dang_nhap(
            False, ma_loi="dang_chay",
            huong_dan="Lượt đăng nhập vẫn đang chạy — bấm «Kiểm tra trạng thái» "
                       "để hỏi tiếp, không bấm đăng nhập lại.")

    async def nhap_ma_2fa(self, ho_so: str, ma: Any, trinh_duyet=None,
                          url_novnc: str | None = None,
                          module_cua_tai_khoan: str = "") -> dict:
        """Gửi mã 2FA cho phiên đang ở trạng thái need_code (hợp đồng NV145b).

        Nhận mã qua BiMatTam (xoá ngay sau khi gửi) hoặc chuỗi. Không log mã.
        ``module_cua_tai_khoan`` chọn đúng đường 2FA của luồng (ChatGPT dùng
        ``/v1/chatgpt/{ho_so}/onboard-2fa-code``).
        """
        try:
            hs = _ho_so_theo_luong(ho_so, module_cua_tai_khoan)
        except ValueError:
            return {"ok": False, "ma_loi": "tham_so_khong_hop_le"}
        ma_str = ma.lay_va_xoa() if isinstance(ma, BiMatTam) else str(ma or "").strip()
        try:
            if not re.fullmatch(r"[0-9]{6,8}", ma_str):
                return {"ok": False, "ma_loi": "ma_2fa_sai_dinh_dang"}
            if _la_luong_chatgpt(module_cua_tai_khoan):
                kq = await self._goi(
                    "POST", f"/v1/chatgpt/{hs}/onboard-2fa-code",
                    json_body={"code": ma_str})
            else:
                kq = await self._goi(
                    "POST", f"/v1/session/{hs}/auto-login-2fa-code",
                    json_body={"code": ma_str})
        finally:
            ma_str = "\x00" * len(ma_str)
            del ma_str
        if not isinstance(kq, dict) or not kq.get("ok"):
            ma_loi = kq.get("ma_loi") if isinstance(kq, dict) else ""
            # 409 của dịch vụ = phiên không ở trạng thái cần mã
            if ma_loi == "loi_dich_vu":
                return {"ok": False, "ma_loi": "khong_cho_2fa",
                        "huong_dan": "Phiên không còn chờ mã 2FA — đăng nhập lại."}
            return {"ok": False, "ma_loi": "dich_vu_loi"}
        return {"ok": True}

    async def tien_do(self, ho_so: str, module_cua_tai_khoan: str = "") -> dict:
        """Trạng thái lượt đăng nhập nền cho giao diện hỏi (NV145b + NV210).

        ``module_cua_tai_khoan`` chọn đúng đường trạng thái của luồng (ChatGPT
        dùng ``/v1/chatgpt/{ho_so}/onboard-status``).
        Trả: giai_doan (dang_chay/cho_2fa/can_nguoi/thanh_cong/loi), buoc (câu
        trạng thái), cho_2fa, can_nguoi, ma_loi, huong_dan. Không bí mật.
        """
        try:
            hs = _ho_so_theo_luong(ho_so, module_cua_tai_khoan)
        except ValueError:
            return {"ok": False, "ma_loi": "tham_so_khong_hop_le"}
        if _la_luong_chatgpt(module_cua_tai_khoan):
            tt = await self._goi("GET", f"/v1/chatgpt/{hs}/onboard-status")
        else:
            tt = await self._goi("GET", f"/v1/session/{hs}/auto-login-status")
        if not isinstance(tt, dict) or not tt.get("ok"):
            return {"ok": False, "ma_loi": "dich_vu_loi"}
        trang_thai = str(tt.get("state") or "none")
        buoc = str(tt.get("message") or "")
        if trang_thai == "success":
            return {"ok": True, "giai_doan": "thanh_cong", "buoc": buoc,
                    "cho_2fa": False, "can_nguoi": False, "ma_loi": ""}
        if trang_thai == "need_code":
            return {"ok": True, "giai_doan": "cho_2fa", "buoc": buoc,
                    "cho_2fa": True, "can_nguoi": False, "ma_loi": "cho_2fa",
                    "huong_dan": "Nhập mã 2FA rồi bấm gửi."}
        if trang_thai in ("need_captcha", "need_tap"):
            return {"ok": True, "giai_doan": "can_nguoi", "buoc": buoc,
                    "cho_2fa": False, "can_nguoi": True, "ma_loi": "can_nguoi",
                    "huong_dan": "Mở điện thoại/trình duyệt xử lý bước xác minh "
                                 "rồi bấm «Kiểm tra lại»."}
        # NV209: need_human của dịch vụ → can_nguoi + mã lỗi + hướng dẫn
        if trang_thai == "need_human":
            ma_loi = _sach_ma_loi_can_nguoi(str(tt.get("error") or "")) \
                or "can_nguoi"
            return {"ok": True, "giai_doan": "can_nguoi", "buoc": buoc,
                    "cho_2fa": False, "can_nguoi": True, "ma_loi": ma_loi,
                    "huong_dan": _huong_dan_can_nguoi(ma_loi)}
        if trang_thai == "failed":
            return {"ok": True, "giai_doan": "loi", "buoc": buoc,
                    "cho_2fa": False, "can_nguoi": True, "ma_loi": "loi",
                    "huong_dan": "Lượt đăng nhập hỏng — kiểm tra rồi thử lại."}
        return {"ok": True, "giai_doan": "dang_chay", "buoc": buoc,
                "cho_2fa": False, "can_nguoi": False, "ma_loi": "dang_chay"}

    async def trinh_duyet_cookie(self, ho_so: str, mien: str) -> dict:
        """Xuất cookie phiên Google của hồ sơ đã đăng nhập (NV145b).

        Khả năng `trinh_duyet_cookie`: tuyến /api/trinh-duyet/{module}/{ho_so}/
        cookie-sang-module dùng sẵn để chuyển phiên sang trường nhan_cookie của
        module AI đích (vd gemini-web). Cookie không đi qua log/phản hồi thừa.
        """
        try:
            hs = _kiem_ho_so_dich_vu(ho_so)
        except ValueError:
            return {"ok": False, "loi": "tham_so_khong_hop_le"}
        mau = _MIEN_SANG_DUONG_COOKIE.get(str(mien or "").strip().lower())
        if not mau:
            return {"ok": False, "loi": "mien_khong_ho_tro"}
        kq = await self._goi("GET", mau.replace("{ho_so}", hs))
        if not isinstance(kq, dict) or not kq.get("ok"):
            return {"ok": False, "loi": "khong_doc_duoc_cookie"}
        cookies = kq.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            return {"ok": False, "loi": "chua_co_phien"}
        chuoi = "; ".join(f"{ten}={gia_tri}" for ten, gia_tri in cookies.items()
                          if isinstance(ten, str) and isinstance(gia_tri, str)
                          and ten and gia_tri)
        if not chuoi or not _RE_CHUOI_COOKIE.fullmatch(chuoi) or len(chuoi) > 16384:
            return {"ok": False, "loi": "cookie_khong_hop_le"}
        return {"cookie": chuoi}
