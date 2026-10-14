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
_THEO_DOI_SAI: OrderedDict[str, dict[str, Any]] = OrderedDict()
TOI_DA_THEO_DOI = 10000

_SEMAPHORE_SCRYPT = asyncio.Semaphore(2)


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
    async with _SEMAPHORE_SCRYPT:
        return await asyncio.to_thread(_bam_mat_khau_dong_bo, mat_khau)


async def kiem_tra_mat_khau(mat_khau: str, chuoi_bam: str) -> bool:
    async with _SEMAPHORE_SCRYPT:
        return await asyncio.to_thread(_kiem_tra_mat_khau_dong_bo, mat_khau, chuoi_bam)


async def chay_bam_gia() -> None:
    """Thực hiện một lượt băm giả đồng nhất tham số scrypt để thời gian không phân biệt được."""
    async with _SEMAPHORE_SCRYPT:
        await asyncio.to_thread(_bam_mat_khau_dong_bo, "mat_khau_gia_cho_thoi_gian_dong_nhat", MUOI_GIA)


def kiem_tra_chinh_sach_mat_khau(mat_khau: str, ten_dang_nhap: str = "") -> None:
    if not isinstance(mat_khau, str) or len(mat_khau) < 12 or len(mat_khau) > 128:
        raise LoiOpenAI(
            400,
            "Mật khẩu phải từ 12 đến 128 ký tự.",
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
    ten_s = str(ten or "").strip().lower()
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
    p = _p_nguoi_dung()
    try:
        du_lieu = kho.doc_json(p)
        if isinstance(du_lieu, dict) and isinstance(du_lieu.get("nguoi_dung"), list):
            return [dict(u) for u in du_lieu["nguoi_dung"] if isinstance(u, dict) and "id" in u]
        return []
    except Exception as e:
        log.warning("Lỗi đọc tệp %s: %s", p, e)
        return []


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


def co_admin() -> bool:
    with _KHOA_LUONG:
        for u in _doc_nguoi_dung():
            if u.get("vai") == "admin" and u.get("bat", True):
                return True
    return False


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
    """Khởi động tạo <DATA>/ma_thiet_lap (quyền 0600) khi chưa có admin nào và ghi nhật ký một dòng."""
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


# ───────────── Khoá đăng nhập theo tên ─────────────

def kiem_tra_khoa_ten(ten: str) -> None:
    ten_chuan = ten.strip().lower()
    with _KHOA_THEO_DOI:
        muc = _THEO_DOI_SAI.get(ten_chuan)
        if muc and muc.get("khoa_den"):
            con_lai = muc["khoa_den"] - time.monotonic()
            if con_lai > 0:
                raise LoiOpenAI(
                    401,
                    "Sai tên đăng nhập hoặc mật khẩu.",
                    loai="invalid_request_error",
                    code="invalid_credentials",
                )
            muc["khoa_den"] = None
            muc["so_sai"] = 0


def ghi_nhan_dang_nhap_sai(ten: str) -> None:
    ten_chuan = ten.strip().lower()
    toi_da = dang_nhap_sai_toi_da()
    khoa_giay = dang_nhap_khoa_phut() * 60
    with _KHOA_THEO_DOI:
        if len(_THEO_DOI_SAI) >= TOI_DA_THEO_DOI and ten_chuan not in _THEO_DOI_SAI:
            _THEO_DOI_SAI.popitem(last=False)
        muc = _THEO_DOI_SAI.setdefault(ten_chuan, {"so_sai": 0, "khoa_den": None})
        muc["so_sai"] = muc.get("so_sai", 0) + 1
        if muc["so_sai"] >= toi_da:
            muc["khoa_den"] = time.monotonic() + khoa_giay
            _THEO_DOI_SAI.move_to_end(ten_chuan)


def ghi_nhan_dang_nhap_dung(ten: str) -> None:
    ten_chuan = ten.strip().lower()
    with _KHOA_THEO_DOI:
        _THEO_DOI_SAI.pop(ten_chuan, None)


# ───────────── Thao tác người dùng ─────────────

async def tao_nguoi_dung(
    vai: str = "user",
    ten_dang_nhap: str | None = None,
    mat_khau: str | None = None,
    pham_vi_toi_da: list[str] | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Tạo người dùng mới. Nếu thiếu tên hoặc mật khẩu thì tự sinh ngẫu nhiên.
    Trả về (bản_ghi_sạch, tên_đăng_nhập, mật_khẩu_thô). Mật khẩu thô chỉ hiện một lần duy nhất."""
    vai_chuan = str(vai or "user").strip().lower()
    if vai_chuan not in ("admin", "user"):
        raise LoiOpenAI(400, "Vai người dùng phải là «admin» hoặc «user».", code="invalid_role")

    with _KHOA_LUONG:
        ds = _doc_nguoi_dung()

        if ten_dang_nhap:
            ten_chuan = kiem_tra_ten_dang_nhap(ten_dang_nhap)
            for u in ds:
                if str(u.get("ten_dang_nhap", "")).lower() == ten_chuan:
                    raise LoiOpenAI(400, f"Tên đăng nhập «{ten_chuan}» đã tồn tại.", code="username_taken")
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

    if pham_vi_toi_da is not None:
        pv = list(pham_vi_toi_da)
    else:
        pv = ["*"] if vai_chuan == "admin" else ["chat"]

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
        ds.append(ban_ghi)
        _ghi_nguoi_dung(ds)

    return ban_ghi_sach(ban_ghi), ten_chuan, mat_khau_tho


def sua_nguoi_dung(
    id_: str,
    vai: str | None = None,
    bat: bool | None = None,
    pham_vi_toi_da: list[str] | None = None,
) -> dict[str, Any]:
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

        muon_ha_vai = vai is not None and vai.lower() != "admin"
        muon_tat = bat is not None and not bat

        if la_admin_bat and (muon_ha_vai or muon_tat) and so_admin_bat <= 1:
            raise LoiOpenAI(
                409,
                "Không thể tắt hoặc hạ quyền của tài khoản admin cuối cùng.",
                loai="invalid_request_error",
                code="last_admin_invariant",
            )

        if vai is not None:
            vai_s = vai.strip().lower()
            if vai_s not in ("admin", "user"):
                raise LoiOpenAI(400, "Vai người dùng phải là «admin» hoặc «user».", code="invalid_role")
            tim["vai"] = vai_s
        if bat is not None:
            tim["bat"] = bool(bat)
        if pham_vi_toi_da is not None:
            tim["pham_vi_toi_da"] = list(pham_vi_toi_da)

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


async def doi_mat_khau_chinh_minh(id_: str, mat_khau_cu: str, mat_khau_moi: str) -> None:
    with _KHOA_LUONG:
        u = tim_theo_id(id_)
        if not u:
            raise LoiOpenAI(404, "Không tìm thấy người dùng.", code="user_not_found")
        ten = str(u.get("ten_dang_nhap", ""))
        bam_cu = str(u.get("bam_mat_khau", ""))

    dung = await kiem_tra_mat_khau(mat_khau_cu, bam_cu)
    if not dung:
        raise LoiOpenAI(401, "Mật khẩu cũ không chính xác.", code="incorrect_password")

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

        # BẤT BIẾN: không thể xoá admin cuối cùng
        if tim.get("vai") == "admin":
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
