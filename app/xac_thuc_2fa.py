"""Xác thực hai bước (2FA) bằng ứng dụng TOTP theo chuẩn RFC 6238 cho web2api.

Thiết kế bảo mật:
- Thuật toán TOTP HMAC-SHA1, 6 chữ số, chu kỳ 30 giây, chấp nhận lệch ±1 bước.
- Tự cài đặt hoàn toàn bằng thư viện chuẩn (hmac, hashlib, struct, base64, secrets).
- Bí mật TOTP 160-bit ngẫu nhiên (secrets.token_bytes(20)), mã hoá Base32 chữ hoa KHÔNG đệm «=».
- Chống phát lại (Replay protection): lưu `moc_buoc_cuoi`, từ chối bất kỳ mã nào có bước <= bước cuối đã dùng.
- So sánh mã bằng hmac.compare_digest.
- 10 mã khôi phục dùng một lần (16 ký tự phân nhóm 4), chỉ lưu bản băm SHA-256, dùng xong bị xoá vĩnh viễn.
- Kho dữ liệu đĩa: <DATA>/bi_mat/2fa/u_<user_id>.json (quyền 0600 nguyên tử, tự động mã hoá khi nghỉ qua ham_khoa).
- Vé tạm đăng nhập (Temporary token): sống 5 phút, lưu băm SHA-256 trong bộ nhớ có trần, gắn với IP hash, dùng một lần.
- Độc lập theo từng tài khoản: tài khoản này bật/tắt không ảnh hưởng tài khoản khác.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import struct
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Request

from app import kho
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.2fa")

_KHOA_LUONG = threading.RLock()
_KHOA_VE = threading.Lock()

BANG_BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
BANG_KHOI_PHUC = "23456789abcdefghijkmnopqrstuvwxyz"  # bỏ 0, 1, l, o để tránh nhầm lẫn

DO_DAI_BI_MAT_BYTES = 20  # 160 bit
THOI_HAN_VE_GIAY = 300   # 5 phút
TOI_DA_VE_BO_NHO = 1000
SO_MA_KHOI_PHUC = 10

TEN_TEP_USER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}\Z")

# Bộ nhớ lưu vé tạm: key là SHA-256 của vé, value là dict thông tin vé
_KHO_VE_TAM: OrderedDict[str, dict[str, Any]] = OrderedDict()


class LoiKho2FA(LoiOpenAI):
    def __init__(self) -> None:
        super().__init__(
            503,
            "Kho xác thực 2FA không đọc được. Khôi phục dữ liệu từ bản sao lưu.",
            loai="server_error",
            code="totp_store_unreadable",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1. THUẬT TOÁN TOTP RFC 6238 & HOTP RFC 4226
# ─────────────────────────────────────────────────────────────────────────────

def giai_ma_base32(bi_mat: str) -> bytes:
    """Giải mã chuỗi Base32 chuẩn RFC 4648, hỗ trợ không đệm và bỏ khoảng trắng."""
    s = re.sub(r"[\s=-]", "", bi_mat.strip().upper())
    pad = (8 - (len(s) % 8)) % 8
    try:
        return base64.b32decode((s + "=" * pad).encode("ascii"), casefold=True)
    except Exception as e:
        raise ValueError("Chuỗi bí mật Base32 không hợp lệ") from e


def ma_hoa_base32(du_lieu: bytes) -> str:
    """Mã hoá Base32 chữ hoa, bỏ phần đệm «=» thừa."""
    return base64.b32encode(du_lieu).decode("ascii").rstrip("=").upper()


def tao_bi_mat_totp() -> str:
    """Sinh bí mật TOTP 160 bit (20 byte) ngẫu nhiên an toàn, mã hoá Base32 không đệm."""
    raw = secrets.token_bytes(DO_DAI_BI_MAT_BYTES)
    return ma_hoa_base32(raw)


def sinh_totp(
    bi_mat: str | bytes,
    moc_thoi_gian: int | float | None = None,
    buoc_giay: int = 30,
    so_chu_so: int = 6,
) -> str:
    """Sinh mã TOTP HMAC-SHA1 theo RFC 6238.

    Hỗ trợ so_chu_so=8 để kiểm tra các vector chuẩn RFC 6238 Appendix B.
    """
    if isinstance(bi_mat, str):
        khoa_bytes = giai_ma_base32(bi_mat)
    elif isinstance(bi_mat, (bytes, bytearray)):
        khoa_bytes = bytes(bi_mat)
    else:
        raise ValueError("Bí mật TOTP phải là chuỗi Base32 hoặc bytes")

    t = int((time.time() if moc_thoi_gian is None else moc_thoi_gian) // buoc_giay)
    msg = struct.pack(">Q", t)
    h = hmac.new(khoa_bytes, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    bin_code = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    ma_so = bin_code % (10**so_chu_so)
    return str(ma_so).zfill(so_chu_so)


def kiem_tra_totp(
    bi_mat: str | bytes,
    ma: str,
    moc_buoc_cuoi: int = -1,
    moc_thoi_gian: int | float | None = None,
    buoc_giay: int = 30,
    so_chu_so: int = 6,
) -> tuple[bool, int]:
    """Kiểm tra mã TOTP người dùng nhập với chấp nhận lệch ±1 bước và CHỐNG PHÁT LẠI.

    Trả về: (hop_le: bool, buoc_thanh_cong: int)
    Nếu mã đúng và buoc > moc_buoc_cuoi: trả về (True, buoc).
    Nếu không đúng hoặc buoc <= moc_buoc_cuoi: trả về (False, -1).
    """
    ma_s = ma.strip()
    if not ma_s.isdigit() or len(ma_s) != so_chu_so:
        return False, -1

    t_ht = int((time.time() if moc_thoi_gian is None else moc_thoi_gian) // buoc_giay)
    # Duyệt các bước theo thứ tự t_ht, t_ht + 1, t_ht - 1
    cac_buoc = [t_ht, t_ht + 1, t_ht - 1]

    for b in cac_buoc:
        if b <= moc_buoc_cuoi:
            # Chống phát lại: mã ở bước đã dùng hoặc cũ hơn bước đã dùng bị từ chối
            continue
        try:
            ky_vong = sinh_totp(bi_mat, moc_thoi_gian=b * buoc_giay, buoc_giay=buoc_giay, so_chu_so=so_chu_so)
            if hmac.compare_digest(ma_s.encode("ascii"), ky_vong.encode("ascii")):
                return True, b
        except ValueError:
            continue  # bí mật Base32 hỏng: không mã nào khớp → từ chối (đóng cửa)

    return False, -1


def tao_uri_totp(ten_dang_nhap: str, bi_mat: str, issuer: str = "web2api") -> str:
    """Tạo URI otpauth:// chuẩn tương thích mọi ứng dụng xác thực."""
    ten_ma_hoa = quote(ten_dang_nhap)
    issuer_ma_hoa = quote(issuer)
    # Định dạng chuẩn: otpauth://totp/issuer:ten?secret=...&issuer=issuer&algorithm=SHA1&digits=6&period=30
    return (
        f"otpauth://totp/{issuer_ma_hoa}:{ten_ma_hoa}"
        f"?secret={bi_mat}&issuer={issuer_ma_hoa}&algorithm=SHA1&digits=6&period=30"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. MÃ KHÔI PHỤC (10 mã, 16 ký tự, chỉ lưu bản băm SHA-256)
# ─────────────────────────────────────────────────────────────────────────────

def _chuan_hoa_ma_khoi_phuc(ma: str) -> str:
    """Chuẩn hoá mã khôi phục: loại bỏ khoảng trắng, dấu gạch nối, viết thường."""
    return re.sub(r"[\s-]", "", ma.strip().lower())


def _bam_ma_khoi_phuc(ma_chuan: str) -> str:
    """Băm SHA-256 của mã khôi phục đã chuẩn hoá."""
    return hashlib.sha256(ma_chuan.encode("ascii")).hexdigest()


def sinh_danh_sach_ma_khoi_phuc(so_luong: int = SO_MA_KHOI_PHUC) -> tuple[list[str], list[str]]:
    """Sinh danh sách mã khôi phục dùng một lần.

    Mỗi mã gồm 16 ký tự phân tách nhóm 4 (xxxx-xxxx-xxxx-xxxx).
    Trả về (danh_sach_ma_tho, danh_sach_ma_bam).
    """
    tho = []
    bam = []
    for _ in range(so_luong):
        chuoi = "".join(secrets.choice(BANG_KHOI_PHUC) for _ in range(16))
        dinh_dang = f"{chuoi[0:4]}-{chuoi[4:8]}-{chuoi[8:12]}-{chuoi[12:16]}"
        tho.append(dinh_dang)
        bam.append(_bam_ma_khoi_phuc(chuoi))
    return tho, bam


def kiem_tra_va_dung_ma_khoi_phuc(ma: str, danh_sach_bam: list[str]) -> tuple[bool, list[str]]:
    """Kiểm tra mã khôi phục người dùng gửi lên.

    Nếu đúng: loại bỏ bản băm tương ứng (dùng một lần) và trả về (True, danh_sach_bam_moi).
    Nếu sai: trả về (False, danh_sach_bam).
    """
    chuan = _chuan_hoa_ma_khoi_phuc(ma)
    if len(chuan) != 16:
        return False, danh_sach_bam

    bam_gui = _bam_ma_khoi_phuc(chuan)
    danh_sach_moi = []
    khop = False

    for b in danh_sach_bam:
        if not khop and hmac.compare_digest(b.encode("ascii"), bam_gui.encode("ascii")):
            khop = True
            # Bỏ qua không thêm vào danh_sach_moi -> mã bị tiêu huỷ
            continue
        danh_sach_moi.append(b)

    return khop, danh_sach_moi


# ─────────────────────────────────────────────────────────────────────────────
# 3. KHO DỮ LIỆU ĐĨA (<DATA>/bi_mat/2fa/u_<user_id>.json)
# ─────────────────────────────────────────────────────────────────────────────

def _thu_muc_2fa() -> Path:
    tm = kho.thu_muc_du_lieu() / "bi_mat" / "2fa"
    tm.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(tm, 0o700)
    except OSError:
        pass
    return tm


def _duong_dan_tep_2fa(user_id: str) -> Path:
    uid_s = str(user_id).strip()
    if not TEN_TEP_USER_RE.match(uid_s):
        raise ValueError(f"ID người dùng không hợp lệ: {uid_s!r}")
    tm = _thu_muc_2fa()
    goc = tm.resolve()
    p = (goc / f"u_{uid_s}.json").resolve()
    if not p.is_relative_to(goc):
        raise ValueError(f"Đường dẫn vượt ngoài thư mục 2FA: {uid_s!r}")
    return p


def _doc_kho_user(user_id: str) -> dict[str, Any] | None:
    p = _duong_dan_tep_2fa(user_id)
    if not p.is_file():
        return None
    from app import ham_khoa
    try:
        dl = ham_khoa.doc_bi_mat_tep(p)
    except ham_khoa.KhoHong:
        log.warning("Kho 2FA của người dùng %s bị hỏng: %s", user_id, p)
        raise LoiKho2FA() from None
    if not isinstance(dl, dict):
        raise LoiKho2FA()
    return dl


def _ghi_kho_user(user_id: str, du_lieu: dict[str, Any]) -> None:
    p = _duong_dan_tep_2fa(user_id)
    from app import ham_khoa
    ham_khoa.ghi_bi_mat_tep(p, du_lieu, 0o600)


def da_bat_2fa(user_id: str) -> bool:
    """Kiểm tra tài khoản này đã kích hoạt 2FA hay chưa.

    Tệp 2FA hỏng/không đọc được thì ném `LoiKho2FA` (503), KHÔNG trả False: trả False lúc lỗi nghĩa là cổng 2FA bị bỏ qua
    (mở cửa khi lỗi). Đăng nhập của tài khoản đó dừng ở 503 cho tới khi quản trị tắt/đặt lại 2FA hoặc khôi phục sao lưu.
    """
    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id)
        if dl is None:
            return False
        return bool(dl.get("bat", False) and dl.get("bi_mat"))


def so_ma_khoi_phuc_con_lai(user_id: str) -> int:
    """Đếm số mã khôi phục chưa dùng của người dùng."""
    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id)
        if dl is None:
            return 0
        bam_ds = dl.get("ma_khoi_phuc_bam", [])
        return len(bam_ds) if isinstance(bam_ds, list) else 0


def bat_dau_dang_ky(user_id: str, ten_dang_nhap: str) -> tuple[str, str]:
    """Bắt đầu luồng đăng ký 2FA: sinh bí mật TOTP và URI otpauth://.

    Lưu bí mật ở trạng thái chờ xác nhận (`cho_xac_nhan`), chưa kích hoạt chính thức.
    """
    bi_mat = tao_bi_mat_totp()
    uri = tao_uri_totp(ten_dang_nhap, bi_mat)
    now_ts = time.time()

    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id) or {}
        dl["cho_xac_nhan"] = {
            "bi_mat": bi_mat,
            "tao_luc": now_ts,
        }
        _ghi_kho_user(user_id, dl)

    return bi_mat, uri


def xac_nhan_dang_ky(user_id: str, ma: str) -> tuple[bool, list[str]]:
    """Xác nhận mã 6 số với bí mật đang chờ: nếu đúng thì kích hoạt 2FA và cấp 10 mã khôi phục."""
    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id)
        if not dl or "cho_xac_nhan" not in dl or not isinstance(dl["cho_xac_nhan"], dict):
            return False, []

        cho = dl["cho_xac_nhan"]
        bi_mat_cho = cho.get("bi_mat")
        if not bi_mat_cho:
            return False, []

        hop_le, buoc = kiem_tra_totp(bi_mat_cho, ma)
        if not hop_le:
            return False, []

        # Mã hợp lệ: chính thức kích hoạt
        ma_tho, ma_bam = sinh_danh_sach_ma_khoi_phuc(SO_MA_KHOI_PHUC)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        dl["bat"] = True
        dl["bi_mat"] = bi_mat_cho
        dl["moc_buoc_cuoi"] = buoc
        dl["ma_khoi_phuc_bam"] = ma_bam
        dl["kich_hoat_luc"] = now_iso
        dl.pop("cho_xac_nhan", None)

        _ghi_kho_user(user_id, dl)
        return True, ma_tho


def tat_2fa(user_id: str) -> None:
    """Tắt 2FA và xoá bí mật cũng như các mã khôi phục của tài khoản."""
    with _KHOA_LUONG:
        p = _duong_dan_tep_2fa(user_id)
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def tao_lai_ma_khoi_phuc(user_id: str) -> list[str]:
    """Tạo lại 10 mã khôi phục mới cho tài khoản đã bật 2FA."""
    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id)
        if not dl or not dl.get("bat") or not dl.get("bi_mat"):
            raise LoiOpenAI(400, "Tài khoản chưa kích hoạt 2FA.", code="totp_not_active")

        ma_tho, ma_bam = sinh_danh_sach_ma_khoi_phuc(SO_MA_KHOI_PHUC)
        dl["ma_khoi_phuc_bam"] = ma_bam
        _ghi_kho_user(user_id, dl)
        return ma_tho


def xac_thuc_ma(user_id: str, ma: str) -> bool:
    """Xác thực mã đăng nhập: thử mã TOTP 6 số trước, nếu không khớp thì thử mã khôi phục.

    Cập nhật moc_buoc_cuoi hoặc tiêu huỷ mã khôi phục nếu xác thực thành công.
    """
    ma_s = ma.strip() if isinstance(ma, str) else ""
    if not ma_s:
        return False

    with _KHOA_LUONG:
        dl = _doc_kho_user(user_id)
        if not dl or not dl.get("bat") or not dl.get("bi_mat"):
            return False

        bi_mat = dl["bi_mat"]
        buoc_cuoi = dl.get("moc_buoc_cuoi", -1)

        # 1. Thử xác thực mã TOTP 6 số
        hop_le, buoc_moi = kiem_tra_totp(bi_mat, ma_s, moc_buoc_cuoi=buoc_cuoi)
        if hop_le:
            dl["moc_buoc_cuoi"] = buoc_moi
            _ghi_kho_user(user_id, dl)
            return True

        # 2. Thử xác thực mã khôi phục 16 ký tự
        danh_sach_bam = dl.get("ma_khoi_phuc_bam", [])
        if isinstance(danh_sach_bam, list) and danh_sach_bam:
            khop, ds_moi = kiem_tra_va_dung_ma_khoi_phuc(ma_s, danh_sach_bam)
            if khop:
                dl["ma_khoi_phuc_bam"] = ds_moi
                _ghi_kho_user(user_id, dl)
                return True

        return False


def dat_lai_2fa_cho_nguoi_dung(ten_dang_nhap: str) -> bool:
    """Lệnh đặt lại 2FA trên máy chủ (chỉ đặt lại cho tài khoản chỉ định)."""
    from app import nguoi_dung
    u = nguoi_dung.tim_theo_ten(ten_dang_nhap)
    if not u:
        return False
    tat_2fa(u["id"])
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 4. QUẢN LÝ VÉ TẠM (TEMPORARY TOKENS)
# ─────────────────────────────────────────────────────────────────────────────

def _bam_ve(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _ip_hash_tu_request(request: Request) -> str:
    ip_khach = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for")
    ip_str = f"{ip_khach}:{xff or ''}"
    return hashlib.sha256(ip_str.encode("utf-8")).hexdigest()


def tao_ve_tam(
    user_id: str,
    request: Request,
    muc_dich: str = "dang_nhap_2fa",
) -> str:
    """Sinh vé tạm ngẫu nhiên 256-bit sống 5 phút, gắn với user_id và client IP.

    Chỉ lưu bản băm SHA-256 của vé trong RAM có trần số lượng.
    """
    token_tho = secrets.token_urlsafe(32)
    bam = _bam_ve(token_tho)
    now_ts = time.time()
    ip_h = _ip_hash_tu_request(request)

    with _KHOA_VE:
        # Dọn dẹp vé hết hạn hoặc quá trần
        cac_het_han = [k for k, v in _KHO_VE_TAM.items() if now_ts - v.get("tao_luc", 0) > THOI_HAN_VE_GIAY]
        for k in cac_het_han:
            _KHO_VE_TAM.pop(k, None)

        while len(_KHO_VE_TAM) >= TOI_DA_VE_BO_NHO:
            _KHO_VE_TAM.popitem(last=False)

        _KHO_VE_TAM[bam] = {
            "nguoi_dung_id": str(user_id),
            "ip_hash": ip_h,
            "muc_dich": muc_dich,
            "tao_luc": now_ts,
        }

    return token_tho


def xac_thuc_va_huy_ve_tam(
    token: str,
    request: Request,
    muc_dich: str = "dang_nhap_2fa",
) -> dict[str, Any] | None:
    """Xác thực vé tạm và HUỶ NGAY LẬP TỨC (single-use).

    Kiểm tra thời hạn (5 phút), mục đích và client IP hash.
    Nếu sai, hết hạn hoặc không khớp -> trả về None (không phân biệt lý do).
    """
    if not isinstance(token, str) or len(token) < 32 or len(token) > 128:
        return None

    bam = _bam_ve(token)
    now_ts = time.time()
    ip_h = _ip_hash_tu_request(request)

    with _KHOA_VE:
        thong_tin = _KHO_VE_TAM.pop(bam, None)
        if thong_tin is None:
            return None

        # Kiểm tra thời hạn
        if now_ts - thong_tin.get("tao_luc", 0) > THOI_HAN_VE_GIAY:
            return None

        # Kiểm tra mục đích
        if thong_tin.get("muc_dich") != muc_dich:
            return None

        # Kiểm tra IP hash
        luu_ip = thong_tin.get("ip_hash", "")
        if not hmac.compare_digest(luu_ip.encode("ascii"), ip_h.encode("ascii")):
            return None

        return thong_tin
