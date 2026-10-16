"""Quản lý tài khoản người dùng, mật khẩu scrypt và thiết lập ban đầu của web2api.

Thiết kế bảo mật:
- Tệp `<DATA>/nguoi_dung.json` quyền 0600, ghi nguyên tử.
- Mật khẩu băm bằng hashlib.scrypt (n=2**14, r=8, p=1, dklen=32, muối ngẫu nhiên 16 byte),
  lưu dạng tự mô tả `scrypt$14$8$1$<muối b64>$<băm b64>`, so bằng hmac.compare_digest.
- Tính băm trong asyncio.to_thread với semaphore 2 để bảo vệ RAM container 256MB.
- Không bao giờ ghi mật khẩu/băm/token vào nhật ký hay phản hồi lỗi.
- Băm giả (chay_bam_gia) khi đăng nhập sai tên, tên bị khoá, hoặc tài khoản bị tắt để chống timing attack.
- Khoá tên tài khoản khi sai liên tiếp 5 lần trong 15 phút (cấu hình qua biến môi trường).
- Bất biến: luôn còn ít nhất một admin đang bật trong hệ thống (chặn hạ vai, tắt hoặc xoá admin cuối cùng).
- Kho hỏng ĐÓNG KHI LỖI: tệp tồn tại mà không đọc/phân tích được → `LoiKhoNguoiDung` (503), không bao giờ coi là
  «chưa có ai» (sẽ mở lại thiết lập lần đầu) và không bao giờ ghi đè tệp. Chỉ tệp KHÔNG tồn tại mới là kho rỗng.
- Kiểm-rồi-ghi trong CÙNG một vùng găng: trùng tên và «chỉ khi chưa có admin» được kiểm lại ngay trong lần khoá ghi.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from app import kho, nhat_ky
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.nguoi_dung")

TEN_DANG_NHAP_RE = re.compile(r"^[a-z0-9._-]{3,32}\Z")
BANG_MAT_KHAU = "23456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
BANG_BASE32 = "abcdefghijklmnopqrstuvwxyz234567"
MUOI_GIA = b"\x00" * 16

_KHOA_LUONG = threading.RLock()
_KHOA_THEO_DOI = threading.Lock()
_THIET_BI_DA_BIET: OrderedDict[str, dict[str, Any]] = OrderedDict()


class _TheoDoiDict(OrderedDict):
    def clear(self) -> None:
        super().clear()
        _THIET_BI_DA_BIET.clear()


_THEO_DOI_SAI: OrderedDict[str, dict[str, Any]] = _TheoDoiDict()
TOI_DA_THEO_DOI = 10000

_SEMAPHORE_SCRYPT = asyncio.Semaphore(2)
_KHOA_HANG_CHO = threading.Lock()
_dem_hang_cho_scrypt = 0
MAC_DINH_HANG_CHO_SCRYPT = 16


class LoiHangChoQuaTai(LoiOpenAI):
    """Hàng chờ băm mật khẩu quá trần."""

    def __init__(self, retry_after: int = 1) -> None:
        super().__init__(
            429,
            "Hàng chờ xác thực quá tải. Vui lòng thử lại sau.",
            loai="rate_limit_error",
            code="rate_limit_exceeded",
            headers={"Retry-After": str(retry_after)},
        )


def hang_cho_scrypt_toi_da() -> int:
    val = os.environ.get("WEB2API_DANG_NHAP_HANG_CHO_TOI_DA") or os.environ.get("WEB2API_SCRYPT_HANG_CHO_TOI_DA")
    if val:
        try:
            n = int(val)
            return n if n > 0 else MAC_DINH_HANG_CHO_SCRYPT
        except (ValueError, TypeError):
            pass
    return MAC_DINH_HANG_CHO_SCRYPT


_HOOK_VAO_HANG_CHO = None


@asynccontextmanager
async def giu_slot_scrypt():
    """Giữ một vị trí trong hàng chờ băm scrypt (đang chờ + đang băm). Quá trần -> 429 Retry-After ngay."""
    global _dem_hang_cho_scrypt
    with _KHOA_HANG_CHO:
        tran = hang_cho_scrypt_toi_da()
        if _dem_hang_cho_scrypt >= tran:
            raise LoiHangChoQuaTai()
        _dem_hang_cho_scrypt += 1
        if _HOOK_VAO_HANG_CHO is not None:
            try:
                _HOOK_VAO_HANG_CHO(_dem_hang_cho_scrypt)
            except Exception:
                pass
    try:
        async with _SEMAPHORE_SCRYPT:
            yield
    finally:
        with _KHOA_HANG_CHO:
            _dem_hang_cho_scrypt -= 1

#: Chính sách độ dài mật khẩu — dùng chung cho đặt mật khẩu và đăng nhập (dài hơn trần → sai, không băm chuỗi dài).
MAT_KHAU_TOI_THIEU = 12
MAT_KHAU_TOI_DA = 128

VAI_HOP_LE = ("admin", "user")
#: Trần phạm vi mặc định của vai user (khi tạo không nêu, và khi hạ vai admin → user không kèm trần mới).
PHAM_VI_MAC_DINH_USER = ["chat"]


class LoiKhoNguoiDung(LoiOpenAI):
    """Tệp nguoi_dung.json tồn tại nhưng không đọc/phân tích được. 503, không lộ chi tiết tệp ra phản hồi."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "Kho tài khoản người dùng không đọc được. Khôi phục tệp nguoi_dung.json từ bản sao lưu rồi thử lại; "
            "thiết lập lần đầu bị khoá để không ghi đè tài khoản hiện có.",
            loai="server_error",
            code="user_store_unreadable",
        )


def _p_nguoi_dung() -> Path:
    return kho.thu_muc_du_lieu() / "nguoi_dung.json"


def _p_ma_thiet_lap() -> Path:
    return kho.thu_muc_du_lieu() / "ma_thiet_lap"


def _so_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        val = int(os.environ.get(ten, mac_dinh))
        return val if val > 0 else mac_dinh
    except (TypeError, ValueError):
        return mac_dinh


def dang_nhap_sai_toi_da() -> int:
    return _so_nguyen("WEB2API_DANG_NHAP_SAI_TOI_DA", 5)


def dang_nhap_khoa_phut() -> int:
    return _so_nguyen("WEB2API_DANG_NHAP_KHOA_PHUT", 15)


def dang_nhap_toan_cuc_sai_toi_da() -> int:
    return _so_nguyen("WEB2API_DANG_NHAP_TOAN_CUC_SAI_TOI_DA", 50)



# ───────────── Băm và kiểm tra mật khẩu ─────────────

def _bam_scrypt(mat_khau: str, muoi: bytes) -> bytes:
    return hashlib.scrypt(
        mat_khau.encode("utf-8"),
        salt=muoi,
        n=16384,
        r=8,
        p=1,
        maxmem=32 * 1024 * 1024,
        dklen=32,
    )


def _bam_mat_khau_dong_bo(mat_khau: str, muoi: bytes | None = None) -> str:
    muoi_that = muoi if muoi is not None else secrets.token_bytes(16)
    dk = _bam_scrypt(mat_khau, muoi_that)
    muoi_b64 = base64.b64encode(muoi_that).decode("ascii")
    bam_b64 = base64.b64encode(dk).decode("ascii")
    return f"scrypt$14$8$1${muoi_b64}${bam_b64}"


def _kiem_tra_mat_khau_dong_bo(mat_khau: str, chuoi_bam: str) -> bool:
    if not chuoi_bam or not isinstance(chuoi_bam, str):
        return False
    parts = chuoi_bam.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        log2_n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        muoi = base64.b64decode(parts[4])
        bam_goc = base64.b64decode(parts[5])
        n = 1 << log2_n
        dk = hashlib.scrypt(
            mat_khau.encode("utf-8"),
            salt=muoi,
            n=n,
            r=r,
            p=p,
            maxmem=32 * 1024 * 1024,
            dklen=len(bam_goc),
        )
        return hmac.compare_digest(dk, bam_goc)
    except Exception:
        return False


async def bam_mat_khau(mat_khau: str) -> str:
    async with giu_slot_scrypt():
        return await asyncio.to_thread(_bam_mat_khau_dong_bo, mat_khau)


async def kiem_tra_mat_khau(mat_khau: str, chuoi_bam: str) -> bool:
    """So mật khẩu với băm. Không phải chuỗi hoặc dài hơn MAT_KHAU_TOI_DA → sai, vẫn chạy MỘT lượt băm giả
    (thời gian đồng nhất, không băm chuỗi dài do khách gửi)."""
    if not isinstance(mat_khau, str) or len(mat_khau) > MAT_KHAU_TOI_DA:
        await chay_bam_gia()
        return False
    async with giu_slot_scrypt():
        return await asyncio.to_thread(_kiem_tra_mat_khau_dong_bo, mat_khau, chuoi_bam)


async def chay_bam_gia() -> None:
    """Thực hiện một lượt băm giả đồng nhất tham số scrypt để thời gian không phân biệt được."""
    async with giu_slot_scrypt():
        await asyncio.to_thread(_bam_mat_khau_dong_bo, "mat_khau_gia_cho_thoi_gian_dong_nhat", MUOI_GIA)


def kiem_tra_chinh_sach_mat_khau(mat_khau: str, ten_dang_nhap: str = "") -> None:
    if not isinstance(mat_khau, str) or len(mat_khau) < MAT_KHAU_TOI_THIEU or len(mat_khau) > MAT_KHAU_TOI_DA:
        raise LoiOpenAI(
            400,
            f"Mật khẩu phải từ {MAT_KHAU_TOI_THIEU} đến {MAT_KHAU_TOI_DA} ký tự.",
            loai="invalid_request_error",
            code="invalid_password",
        )
    if ten_dang_nhap and mat_khau.strip().lower() == ten_dang_nhap.strip().lower():
        raise LoiOpenAI(
            400,
            "Mật khẩu không được trùng với tên đăng nhập.",
            loai="invalid_request_error",
            code="password_same_as_username",
        )


def kiem_tra_ten_dang_nhap(ten: str) -> str:
    ten_s = ten.strip().lower() if isinstance(ten, str) else ""
    if not TEN_DANG_NHAP_RE.match(ten_s):
        raise LoiOpenAI(
            400,
            "Tên đăng nhập phải từ 3 đến 32 ký tự, chỉ gồm chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang [a-z0-9._-].",
            loai="invalid_request_error",
            code="invalid_username",
        )
    return ten_s


def sinh_mat_khau_ngau_nhien() -> str:
    return "".join(secrets.choice(BANG_MAT_KHAU) for _ in range(20))


def sinh_ten_dang_nhap_ngau_nhien() -> str:
    return "nd-" + "".join(secrets.choice(BANG_BASE32) for _ in range(6))


# ───────────── Quản lý kho người dùng trên đĩa ─────────────

def tran_pham_vi(nd: dict[str, Any]) -> list[str]:
    """Trần phạm vi HIỆU LỰC của một người dùng: admin → «*» theo vai; user → `pham_vi_toi_da` đã lưu (thiếu → rỗng)."""
    if nd.get("vai") == "admin":
        return ["*"]
    pv = nd.get("pham_vi_toi_da", [])
    return [x for x in pv if isinstance(x, str)] if isinstance(pv, list) else []


def kiem_vai(vai: Any) -> str:
    """Kiểm KIỂU ở biên: vai phải là chuỗi ∈ VAI_HOP_LE (không phân biệt hoa thường)."""
    if not isinstance(vai, str) or vai.strip().lower() not in VAI_HOP_LE:
        raise LoiOpenAI(400, "Vai người dùng phải là «admin» hoặc «user».", code="invalid_value")
    return vai.strip().lower()


def ban_ghi_sach(nd: dict[str, Any]) -> dict[str, Any]:
    """Bản ghi an toàn không chứa mật khẩu hay băm mật khẩu."""
    return {
        "id": nd.get("id"),
        "ten_dang_nhap": nd.get("ten_dang_nhap"),
        "vai": nd.get("vai"),
        "bat": bool(nd.get("bat", True)),
        "pham_vi_toi_da": list(nd.get("pham_vi_toi_da", ["*"] if nd.get("vai") == "admin" else [])),
        "tao_luc": nd.get("tao_luc"),
    }


def _doc_nguoi_dung() -> list[dict[str, Any]]:
    """Đọc kho. Tệp KHÔNG tồn tại → [] (chưa có ai). Tệp tồn tại mà đọc lỗi, JSON hỏng hoặc sai cấu trúc (gốc không
    phải object, `nguoi_dung` không phải list, phần tử không phải object có `id`) → LoiKhoNguoiDung.

    Không lọc lặng lẽ phần tử hỏng: lọc rồi lần ghi sau sẽ xoá mất chúng (đọc mất rồi ghi đè).
    """
    p = _p_nguoi_dung()
    try:
        du_lieu = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("Kho người dùng hỏng (%s): %s", p, e)
        raise LoiKhoNguoiDung() from None
    if du_lieu is None:
        return []
    ds = du_lieu.get("nguoi_dung") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list) or not all(isinstance(u, dict) and "id" in u for u in ds):
        log.warning("Kho người dùng sai cấu trúc: %s", p)
        raise LoiKhoNguoiDung()
    return [dict(u) for u in ds]


def kiem_tra_kho() -> None:
    """Ném LoiKhoNguoiDung nếu kho tồn tại mà không đọc được (dùng ở cổng thiết lập để báo rõ, không đọc thân)."""
    with _KHOA_LUONG:
        _doc_nguoi_dung()


def _ghi_nguoi_dung(ds: list[dict[str, Any]]) -> None:
    p = _p_nguoi_dung()
    du_lieu = {"nguoi_dung": ds}
    kho.ghi_nguyen_tu(p, kho.json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=0o600)


def danh_sach_nguoi_dung() -> list[dict[str, Any]]:
    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        return [ban_ghi_sach(u) for u in ds]


def tim_theo_id(id_: str) -> dict[str, Any] | None:
    if not id_:
        return None
    with _KHOA_LUONG:
        for u in _doc_nguoi_dung():
            if u.get("id") == id_:
                return u
    return None


def tim_theo_ten(ten: str) -> dict[str, Any] | None:
    if not ten:
        return None
    ten_chuan = ten.strip().lower()
    with _KHOA_LUONG:
        for u in _doc_nguoi_dung():
            if str(u.get("ten_dang_nhap", "")).lower() == ten_chuan:
                return u
    return None


def _co_admin_bat(ds: list[dict[str, Any]]) -> bool:
    return any(u.get("vai") == "admin" and u.get("bat", True) for u in ds)


def co_admin() -> bool:
    """Đã có admin đang bật chưa. ĐÓNG KHI LỖI: kho hỏng → True (coi như đã có), để thiết lập lần đầu không mở lại."""
    with _KHOA_LUONG:
        try:
            return _co_admin_bat(_doc_nguoi_dung())
        except LoiKhoNguoiDung:
            return True


# ───────────── Thiết lập ban đầu ─────────────

def doc_ma_thiet_lap() -> str | None:
    p = _p_ma_thiet_lap()
    try:
        val = p.read_text(encoding="utf-8").strip()
        return val or None
    except FileNotFoundError:
        return None


def xoa_ma_thiet_lap() -> None:
    p = _p_ma_thiet_lap()
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def khoi_tao_thiet_lap_lan_dau() -> None:
    """Khởi động tạo <DATA>/ma_thiet_lap (quyền 0600) khi chưa có admin nào và ghi nhật ký một dòng.

    Kho hỏng: không tạo, không xoá mã — chỉ ghi một cảnh báo hướng dẫn khôi phục (ứng dụng vẫn khởi động).
    """
    try:
        kiem_tra_kho()
    except LoiKhoNguoiDung:
        nhat_ky.ghi(
            "he_thong",
            "canh_bao",
            f"Kho tài khoản {_p_nguoi_dung()} không đọc được — khôi phục từ bản sao lưu; thiết lập lần đầu bị khoá.",
        )
        return
    if co_admin():
        xoa_ma_thiet_lap()
        return
    p = _p_ma_thiet_lap()
    if p.is_file():
        return
    ma = "".join(secrets.choice(BANG_MAT_KHAU) for _ in range(20))
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ma + "\n")
    nhat_ky.ghi(
        "quan_tri",
        "canh_bao",
        f"Hệ thống cần thiết lập tài khoản admin lần đầu. Mã thiết lập lưu tại: {p}",
        duong_dan=str(p),
    )


# ───────────── Khoá đăng nhập hai tầng: (IP, tên) và tên toàn cục ─────────────

def ten_co_the_ton_tai(ten: Any) -> str | None:
    """Tên chuẩn hoá nếu nó CÓ THỂ là một tài khoản (đúng TEN_DANG_NHAP_RE), ngược lại None.

    Bộ theo dõi đăng nhập sai chỉ nhận khoá qua hàm này: khoá của nó luôn ≤ 32 ký tự, nên RAM bị chặn bởi
    TOI_DA_THEO_DOI × 32 byte bất kể khách gửi gì (tên 24 MB từng ghim 24 MB vĩnh viễn). Cắt độ dài TRƯỚC `.lower()`
    để không sao chép chuỗi khổng lồ.
    """
    if not isinstance(ten, str) or len(ten) > 64:
        return None
    ten_chuan = ten.strip().lower()
    return ten_chuan if TEN_DANG_NHAP_RE.match(ten_chuan) else None


def _khoa_cap(ip: str | None, ten_chuan: str) -> str | None:
    if not ip or not isinstance(ip, str):
        return None
    ip_s = ip.strip()[:45]
    return f"{ip_s}:{ten_chuan}" if ip_s else None


def kiem_tra_khoa_ten(ten: str, ip: str | None = None) -> None:
    ten_chuan = ten_co_the_ton_tai(ten)
    if ten_chuan is None:
        return  # tên không thể tồn tại: không có gì để khoá (đường đăng nhập đã trả 401 trước đó)
    now = time.monotonic()
    with _KHOA_THEO_DOI:
        # Tầng 1: kiểm tra khoá theo cặp (IP, tên) nếu có IP
        k_cap = _khoa_cap(ip, ten_chuan)
        if k_cap:
            muc_cap = _THEO_DOI_SAI.get(k_cap)
            if muc_cap and muc_cap.get("khoa_den"):
                con_lai = muc_cap["khoa_den"] - now
                if con_lai > 0:
                    raise LoiOpenAI(
                        401,
                        "Sai tên đăng nhập hoặc mật khẩu.",
                        loai="invalid_request_error",
                        code="invalid_credentials",
                    )
                muc_cap["khoa_den"] = None
                muc_cap["so_sai"] = 0

        # Tầng 2: kiểm tra khoá tên toàn cục (chống tấn công phân tán nhiều IP)
        muc_ten = _THEO_DOI_SAI.get(ten_chuan)
        if muc_ten and muc_ten.get("khoa_den"):
            con_lai = muc_ten["khoa_den"] - now
            if con_lai > 0:
                raise LoiOpenAI(
                    401,
                    "Sai tên đăng nhập hoặc mật khẩu.",
                    loai="invalid_request_error",
                    code="invalid_credentials",
                )
            muc_ten["khoa_den"] = None
            muc_ten["so_sai"] = 0


def ghi_nhan_dang_nhap_sai(ten: str, ip: str | None = None) -> None:
    ten_chuan = ten_co_the_ton_tai(ten)
    if ten_chuan is None:
        return
    toi_da_cap = dang_nhap_sai_toi_da()
    toi_da_toan_cuc = dang_nhap_toan_cuc_sai_toi_da()
    khoa_giay = dang_nhap_khoa_phut() * 60
    now = time.monotonic()
    with _KHOA_THEO_DOI:
        # Tầng 1: Đếm và khoá theo cặp (IP, tên)
        k_cap = _khoa_cap(ip, ten_chuan)
        if k_cap:
            if len(_THEO_DOI_SAI) >= TOI_DA_THEO_DOI and k_cap not in _THEO_DOI_SAI:
                _THEO_DOI_SAI.popitem(last=False)
            muc_cap = _THEO_DOI_SAI.setdefault(k_cap, {"so_sai": 0, "khoa_den": None, "het_han": now + khoa_giay})
            if muc_cap.get("khoa_den") and muc_cap["khoa_den"] <= now:
                muc_cap["khoa_den"] = None
                muc_cap["so_sai"] = 0
            elif not muc_cap.get("khoa_den") and now > muc_cap.get("het_han", 0):
                muc_cap["so_sai"] = 0
            muc_cap["so_sai"] = muc_cap.get("so_sai", 0) + 1
            muc_cap["het_han"] = now + khoa_giay
            if muc_cap["so_sai"] >= toi_da_cap:
                muc_cap["khoa_den"] = now + khoa_giay
            _THEO_DOI_SAI.move_to_end(k_cap)

        # Tầng 2: Đếm và khoá theo tên toàn cục
        if len(_THEO_DOI_SAI) >= TOI_DA_THEO_DOI and ten_chuan not in _THEO_DOI_SAI:
            _THEO_DOI_SAI.popitem(last=False)
        muc_ten = _THEO_DOI_SAI.setdefault(ten_chuan, {"so_sai": 0, "khoa_den": None, "het_han": now + khoa_giay})
        if muc_ten.get("khoa_den") and muc_ten["khoa_den"] <= now:
            muc_ten["khoa_den"] = None
            muc_ten["so_sai"] = 0
        elif not muc_ten.get("khoa_den") and now > muc_ten.get("het_han", 0):
            muc_ten["so_sai"] = 0
        muc_ten["so_sai"] = muc_ten.get("so_sai", 0) + 1
        muc_ten["het_han"] = now + khoa_giay
        if muc_ten["so_sai"] >= toi_da_toan_cuc:
            muc_ten["khoa_den"] = now + khoa_giay
        _THEO_DOI_SAI.move_to_end(ten_chuan)


def ghi_nhan_dang_nhap_dung(ten: str, ip: str | None = None) -> int:
    ten_chuan = ten_co_the_ton_tai(ten)
    if ten_chuan is None:
        return 0
    with _KHOA_THEO_DOI:
        k_cap = _khoa_cap(ip, ten_chuan)
        so_sai_cap = 0
        if k_cap:
            muc_cap = _THEO_DOI_SAI.pop(k_cap, None)
            if muc_cap:
                so_sai_cap = muc_cap.get("so_sai", 0)
        else:
            muc_ten = _THEO_DOI_SAI.pop(ten_chuan, None)
            return muc_ten.get("so_sai", 0) if muc_ten else 0

        so_sai_ten = 0
        if ten_chuan in _THEO_DOI_SAI:
            muc_ten = _THEO_DOI_SAI[ten_chuan]
            so_sai_ten = muc_ten.get("so_sai", 0)
            if so_sai_cap > 0:
                muc_ten["so_sai"] = max(0, muc_ten.get("so_sai", 0) - so_sai_cap)
                if muc_ten["so_sai"] == 0 and not muc_ten.get("khoa_den"):
                    _THEO_DOI_SAI.pop(ten_chuan, None)

        return max(so_sai_cap, so_sai_ten)


def kiem_va_ghi_nhan_thiet_bi(ten: str, ip: str, ua: str) -> bool:
    """Kiểm tra và ghi nhận dấu thiết bị = sha256(ip|ua) cho tài khoản.

    Trả về True nếu đây là dấu CHƯA từng thấy của tài khoản (thiết bị mới),
    False nếu đã từng thấy. Lưu bộ nhớ có trần (TOI_DA_THEO_DOI).
    """
    ten_chuan = ten_co_the_ton_tai(ten)
    if ten_chuan is None:
        return False
    ip_str = ip.strip() if ip else ""
    ua_str = ua.strip() if ua else ""
    dau = hashlib.sha256(f"{ip_str}|{ua_str}".encode("utf-8")).hexdigest()
    khoa = f"{ten_chuan}:{dau}"
    with _KHOA_THEO_DOI:
        if khoa in _THIET_BI_DA_BIET:
            _THIET_BI_DA_BIET.move_to_end(khoa)
            return False
        if len(_THIET_BI_DA_BIET) >= TOI_DA_THEO_DOI:
            _THIET_BI_DA_BIET.popitem(last=False)
        _THIET_BI_DA_BIET[khoa] = {"thoi_gian": time.monotonic()}
        return True


# ───────────── Thao tác người dùng ─────────────

def _kiem_trung_ten(ds: list[dict[str, Any]], ten_chuan: str) -> None:
    for u in ds:
        if str(u.get("ten_dang_nhap", "")).lower() == ten_chuan:
            raise LoiOpenAI(400, f"Tên đăng nhập «{ten_chuan}» đã tồn tại.", code="username_taken")


def _loi_da_co_admin() -> LoiOpenAI:
    return LoiOpenAI(
        409,
        "Hệ thống đã có tài khoản quản trị viên. Không thể thiết lập lại.",
        loai="invalid_request_error",
        code="already_setup",
    )


async def tao_nguoi_dung(
    vai: str = "user",
    ten_dang_nhap: str | None = None,
    mat_khau: str | None = None,
    pham_vi_toi_da: list[str] | None = None,
    chi_khi_chua_co_admin: bool = False,
    so: Any = None,
) -> tuple[dict[str, Any], str, str]:
    """Tạo người dùng mới. Nếu thiếu tên hoặc mật khẩu thì tự sinh ngẫu nhiên (chỉ đường admin tạo người dùng dùng).
    Trả về (bản_ghi_sạch, tên_đăng_nhập, mật_khẩu_thô). Mật khẩu thô chỉ hiện một lần duy nhất.

    Băm mật khẩu chạy NGOÀI khoá (chậm); vì vậy trùng tên và `chi_khi_chua_co_admin` (thiết lập lần đầu) được kiểm
    LẠI trong chính lần khoá ghi — kiểm-rồi-ghi trong cùng vùng găng, hai lời gọi đồng thời không cùng lọt.
    """
    vai_chuan = kiem_vai(vai)
    pv = (list(PHAM_VI_MAC_DINH_USER) if vai_chuan == "user" else ["*"]) if pham_vi_toi_da is None else \
        _chuan_hoa_tran(pham_vi_toi_da, so=so)

    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        if chi_khi_chua_co_admin and _co_admin_bat(ds):
            raise _loi_da_co_admin()

        if ten_dang_nhap:
            ten_chuan = kiem_tra_ten_dang_nhap(ten_dang_nhap)
            _kiem_trung_ten(ds, ten_chuan)
        else:
            for _ in range(100):
                thu = sinh_ten_dang_nhap_ngau_nhien()
                if not any(str(u.get("ten_dang_nhap", "")).lower() == thu for u in ds):
                    ten_chuan = thu
                    break
            else:
                raise RuntimeError("Không thể tạo tên đăng nhập ngẫu nhiên duy nhất.")

        mat_khau_tho = mat_khau if mat_khau else sinh_mat_khau_ngau_nhien()
        kiem_tra_chinh_sach_mat_khau(mat_khau_tho, ten_chuan)

    bam = await bam_mat_khau(mat_khau_tho)
    id_nd = f"nd_{secrets.token_hex(6)}"
    tao_luc = kho.bay_gio_iso()

    ban_ghi = {
        "id": id_nd,
        "ten_dang_nhap": ten_chuan,
        "vai": vai_chuan,
        "bam_mat_khau": bam,
        "bat": True,
        "pham_vi_toi_da": pv,
        "tao_luc": tao_luc,
    }

    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        if chi_khi_chua_co_admin and _co_admin_bat(ds):
            raise _loi_da_co_admin()
        _kiem_trung_ten(ds, ten_chuan)
        ds.append(ban_ghi)
        _ghi_nguoi_dung(ds)

    return ban_ghi_sach(ban_ghi), ten_chuan, mat_khau_tho


def _chuan_hoa_tran(pham_vi_toi_da: Any, so: Any = None) -> list[str]:
    from app.khoa_khach import chuan_hoa_pham_vi
    return chuan_hoa_pham_vi(pham_vi_toi_da, cho_phep_rong=True, ten_truong="pham_vi_toi_da", kiem_tra_module=True, so=so)


def sua_nguoi_dung(
    id_: str,
    vai: str | None = None,
    bat: bool | None = None,
    pham_vi_toi_da: list[str] | None = None,
    so: Any = None,
) -> dict[str, Any]:
    """Sửa vai/bật-tắt/trần. None = giữ nguyên. Kiểm KIỂU trước khi đọc kho: `bat` phải là bool thật, `vai` là chuỗi
    hợp lệ, `pham_vi_toi_da` là danh sách mã hợp lệ (rỗng được) → sai → 400 invalid_value.

    Hạ vai admin → user mà không kèm `pham_vi_toi_da`: trần về PHAM_VI_MAC_DINH_USER (không để trần «*» lưu từ lúc
    làm admin tiếp tục hiệu lực cho khoá đã cấp).
    """
    if bat is not None and not isinstance(bat, bool):
        raise LoiOpenAI(400, "Trường «bat» phải là true hoặc false.", code="invalid_value")
    vai_moi = None if vai is None else kiem_vai(vai)
    tran_moi = None if pham_vi_toi_da is None else _chuan_hoa_tran(pham_vi_toi_da, so=so)
    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        tim = None
        for u in ds:
            if u.get("id") == id_:
                tim = u
                break
        if not tim:
            raise LoiOpenAI(404, f"Không tìm thấy người dùng có id {id_!r}", code="user_not_found")

        # BẤT BIẾN: luôn còn ít nhất 1 admin đang bật
        la_admin_bat = (tim.get("vai") == "admin" and tim.get("bat", True))
        so_admin_bat = sum(1 for u in ds if u.get("vai") == "admin" and u.get("bat", True))

        muon_ha_vai = vai_moi is not None and vai_moi != "admin"
        muon_tat = bat is False

        if la_admin_bat and (muon_ha_vai or muon_tat) and so_admin_bat <= 1:
            raise LoiOpenAI(
                409,
                "Không thể tắt hoặc hạ quyền của tài khoản admin cuối cùng.",
                loai="invalid_request_error",
                code="last_admin_invariant",
            )

        if vai_moi is not None:
            if tim.get("vai") == "admin" and vai_moi == "user" and tran_moi is None:
                tim["pham_vi_toi_da"] = list(PHAM_VI_MAC_DINH_USER)
            tim["vai"] = vai_moi
        if bat is not None:
            tim["bat"] = bat
        if tran_moi is not None:
            tim["pham_vi_toi_da"] = tran_moi

        _ghi_nguoi_dung(ds)
        return ban_ghi_sach(tim)


async def dat_lai_mat_khau_nguoi_dung(id_: str) -> tuple[dict[str, Any], str]:
    """Sinh mật khẩu ngẫu nhiên mới cho người dùng. Trả về (bản_ghi_sạch, mật_khẩu_mới)."""
    with _KHOA_LUONG:
        u = tim_theo_id(id_)
        if not u:
            raise LoiOpenAI(404, f"Không tìm thấy người dùng có id {id_!r}", code="user_not_found")
        ten = str(u.get("ten_dang_nhap", ""))

    mat_khau_moi = sinh_mat_khau_ngau_nhien()
    bam = await bam_mat_khau(mat_khau_moi)

    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        tim = None
        for item in ds:
            if item.get("id") == id_:
                tim = item
                break
        if not tim:
            raise LoiOpenAI(404, f"Không tìm thấy người dùng có id {id_!r}", code="user_not_found")
        tim["bam_mat_khau"] = bam
        _ghi_nguoi_dung(ds)
        sach = ban_ghi_sach(tim)

    return sach, mat_khau_moi


async def doi_mat_khau_chinh_minh(id_: str, mat_khau_cu: str, mat_khau_moi: str, ip: str | None = None) -> None:
    with _KHOA_LUONG:
        u = tim_theo_id(id_)
        if not u:
            raise LoiOpenAI(404, "Không tìm thấy người dùng.", code="user_not_found")
        ten = str(u.get("ten_dang_nhap", ""))
        bam_cu = str(u.get("bam_mat_khau", ""))

    # Thử mật khẩu cũ đếm chung bộ khoá theo tên với đăng nhập: phiên bị trộm không dò được mật khẩu không giới hạn.
    try:
        kiem_tra_khoa_ten(ten, ip=ip)
    except LoiOpenAI:
        await chay_bam_gia()
        raise LoiOpenAI(401, "Mật khẩu cũ không chính xác.", code="incorrect_password") from None
    dung = await kiem_tra_mat_khau(mat_khau_cu, bam_cu)
    if not dung:
        ghi_nhan_dang_nhap_sai(ten, ip=ip)
        raise LoiOpenAI(401, "Mật khẩu cũ không chính xác.", code="incorrect_password")

    ghi_nhan_dang_nhap_dung(ten, ip=ip)

    kiem_tra_chinh_sach_mat_khau(mat_khau_moi, ten)
    bam_moi = await bam_mat_khau(mat_khau_moi)

    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        tim = None
        for item in ds:
            if item.get("id") == id_:
                tim = item
                break
        if not tim:
            raise LoiOpenAI(404, "Không tìm thấy người dùng.", code="user_not_found")
        # Kiểm-rồi-ghi trong CÙNG vùng găng: mật khẩu cũ được xác minh NGOÀI khoá (băm chậm); trong lúc đó admin có thể
        # đã đặt lại mật khẩu (hoặc phiên khác đã đổi). Ghi vô điều kiện sẽ lặng lẽ huỷ thay đổi đó → 409.
        if str(tim.get("bam_mat_khau", "")) != bam_cu:
            raise LoiOpenAI(409, "Mật khẩu vừa được thay đổi ở nơi khác. Tải lại trang rồi thử lại.",
                            loai="invalid_request_error", code="password_changed_concurrently")
        tim["bam_mat_khau"] = bam_moi
        _ghi_nguoi_dung(ds)


def xoa_nguoi_dung(id_: str) -> bool:
    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()
        tim = None
        for u in ds:
            if u.get("id") == id_:
                tim = u
                break
        if not tim:
            raise LoiOpenAI(404, f"Không tìm thấy người dùng có id {id_!r}", code="user_not_found")

        # BẤT BIẾN: không thể xoá admin ĐANG BẬT cuối cùng (xoá admin đã tắt không làm giảm số admin bật)
        if tim.get("vai") == "admin" and tim.get("bat", True):
            so_admin = sum(1 for u in ds if u.get("vai") == "admin" and u.get("bat", True))
            if so_admin <= 1:
                raise LoiOpenAI(
                    409,
                    "Không thể xoá tài khoản admin cuối cùng.",
                    loai="invalid_request_error",
                    code="last_admin_invariant",
                )

        ds = [u for u in ds if u.get("id") != id_]
        _ghi_nguoi_dung(ds)
        return True
