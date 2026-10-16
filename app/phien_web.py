"""Quản lý phiên web, cookie bảo mật và chống CSRF của web2api.

Thiết kế bảo mật:
- Tệp `<DATA>/phien_web.json` quyền 0600, ghi nguyên tử.
- Token phiên 256 bit (secrets.token_urlsafe(32)), chỉ lưu băm SHA-256 (không lưu token thô).
- Hết hạn hai lớp: nhàn rỗi (WEB2API_PHIEN_WEB_RANH_GIO, mặc định 8h) và tuyệt đối (WEB2API_PHIEN_WEB_TOI_DA_NGAY, mặc định 7 ngày).
- Tối đa 5 phiên mỗi người dùng — tự động thu hồi phiên cũ nhất khi vượt trần.
- Cookie: tên w2a_phien hoặc __Host-w2a_phien khi qua HTTPS; HttpOnly, SameSite=Strict, Path=/, Secure khi HTTPS; không Domain; cookie phiên không Max-Age.
- CSRF: token ngẫu nhiên gắn với phiên; bắt buộc header X-CSRF-Token cho POST/PUT/PATCH/DELETE bằng cookie; so sánh thời gian hằng số; Bearer token được miễn.
- Token phiên/CSRF do khách gửi phải đúng DẠNG token_urlsafe(32) (43 ký tự [A-Za-z0-9_-]) trước khi băm/so — sai dạng
  (không ASCII, quá dài, khoảng trắng, NUL, xuống dòng) = không có phiên / CSRF sai, không bao giờ ngoại lệ 500.
- Kho phiên hỏng: đóng khi lỗi (không phiên nào hợp lệ, tạo phiên 503) và không ghi đè tệp.
- `dung_cuoi` chỉ ghi ra đĩa khi đã cũ ≥ GHI_DUNG_CUOI_GIAY (không fsync mỗi yêu cầu bằng cookie).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Request, Response

from app import dia_chi, kho, nhat_ky
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.phien_web")

_KHOA_LUONG = threading.RLock()
TOI_DA_PHIEN_MOI_NGUOI = 5
#: Dạng đúng của token phiên và token CSRF: secrets.token_urlsafe(32) = 43 ký tự base64url, không đệm.
DANG_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
#: Chỉ ghi lại `dung_cuoi` khi đã cũ hơn ngần này giây (hạn nhàn rỗi tính bằng giờ nên sai số 60 giây không đáng kể).
GHI_DUNG_CUOI_GIAY = 60


def la_dang_token(x: object) -> bool:
    return isinstance(x, str) and DANG_TOKEN.fullmatch(x) is not None


class LoiKhoPhien(LoiOpenAI):
    def __init__(self) -> None:
        super().__init__(503, "Kho phiên đăng nhập không đọc được. Khôi phục tệp phien_web.json hoặc xoá nó để đăng nhập lại.",
                         loai="server_error", code="session_store_unreadable")


def _p_phien_web() -> Path:
    return kho.thu_muc_du_lieu() / "phien_web.json"


def _so_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        val = int(os.environ.get(ten, mac_dinh))
        return val if val > 0 else mac_dinh
    except (TypeError, ValueError):
        return mac_dinh


def phien_ranh_gio() -> int:
    return _so_nguyen("WEB2API_PHIEN_WEB_RANH_GIO", 8)


def phien_toi_da_ngay() -> int:
    return _so_nguyen("WEB2API_PHIEN_WEB_TOI_DA_NGAY", 7)


def _bam_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _doc_phien() -> list[dict[str, Any]]:
    """Tệp không tồn tại → []; tồn tại mà hỏng/sai cấu trúc → LoiKhoPhien (không lọc lặng lẽ rồi ghi đè)."""
    p = _p_phien_web()
    try:
        du_lieu = kho.doc_json(p)
    except kho.KhoHong as e:
        log.warning("Kho phiên hỏng (%s): %s", p, e)
        raise LoiKhoPhien() from None
    if du_lieu is None:
        return []
    ds = du_lieu.get("phien") if isinstance(du_lieu, dict) else None
    if not isinstance(ds, list) or not all(isinstance(x, dict) and "bam" in x for x in ds):
        log.warning("Kho phiên sai cấu trúc: %s", p)
        raise LoiKhoPhien()
    return [dict(x) for x in ds]


def kiem_tra_kho() -> None:
    """Ném LoiKhoPhien nếu kho phiên tồn tại mà không đọc được. Gọi TRƯỚC thao tác ghi nào còn phải tạo/thu hồi phiên
    sau đó — để không xảy ra cảnh đã đổi xong (xoá người, đặt mật khẩu…) rồi mới 503 ở bước phiên."""
    with _KHOA_LUONG:
        _doc_phien()


def _ghi_phien(ds: list[dict[str, Any]]) -> None:
    p = _p_phien_web()
    du_lieu = {"phien": ds}
    kho.ghi_nguyen_tu(p, kho.json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=0o600)


def tao_phien(nguoi_dung_id: str) -> tuple[dict[str, Any], str]:
    """Tạo phiên mới: sinh token 256-bit, lưu băm SHA-256 kèm CSRF token.
    Bỏ phiên cũ nhất nếu người dùng đã có 5 phiên. Trả về (bản_ghi_phiên, token_thô)."""
    token_tho = secrets.token_urlsafe(32)
    bam = _bam_token(token_tho)
    csrf = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc)
    tao_luc = now.isoformat(timespec="seconds")
    dung_cuoi = tao_luc
    het_han_tuyet_doi = (now + timedelta(days=phien_toi_da_ngay())).isoformat(timespec="seconds")

    ban_ghi = {
        "bam": bam,
        "nguoi_dung_id": nguoi_dung_id,
        "tao_luc": tao_luc,
        "dung_cuoi": dung_cuoi,
        "het_han_tuyet_doi": het_han_tuyet_doi,
        "csrf": csrf,
    }

    with _KHOA_LUONG:
        ds = _doc_phien()
        # Lọc danh sách phiên của người dùng này
        phien_cua_nd = [p for p in ds if p.get("nguoi_dung_id") == nguoi_dung_id]
        if len(phien_cua_nd) >= TOI_DA_PHIEN_MOI_NGUOI:
            # Sắp xếp theo dung_cuoi tăng dần để bỏ phiên cũ nhất
            phien_cua_nd.sort(key=lambda x: str(x.get("dung_cuoi", "")))
            can_bo = phien_cua_nd[: len(phien_cua_nd) - TOI_DA_PHIEN_MOI_NGUOI + 1]
            ds = [p for p in ds if p not in can_bo]

        ds.append(ban_ghi)
        _ghi_phien(ds)

    return ban_ghi, token_tho


def xac_thuc_token(token: str) -> dict[str, Any] | None:
    """Xác thực token phiên: kiểm tra băm SHA-256, hạn nhàn rỗi và hạn tuyệt đối.
    Cập nhật dung_cuoi nếu hợp lệ; xoá nếu hết hạn."""
    if not la_dang_token(token):
        return None
    bam_tim = _bam_token(token)
    now = datetime.now(timezone.utc)
    bay_gio_str = now.isoformat(timespec="seconds")

    with _KHOA_LUONG:
        try:
            ds = _doc_phien()
        except LoiKhoPhien:
            return None  # đóng khi lỗi: kho phiên hỏng thì không phiên nào hợp lệ
        tim = None
        for p in ds:
            if hmac.compare_digest(bam_tim.encode("utf-8"), str(p.get("bam", "")).encode("utf-8")):
                tim = p
                break

        if not tim:
            return None

        # Kiểm tra hạn tuyệt đối
        try:
            het_han_td = datetime.fromisoformat(str(tim.get("het_han_tuyet_doi", "")).replace("Z", "+00:00"))
            if now > het_han_td:
                ds.remove(tim)
                _ghi_phien(ds)
                return None
        except Exception:
            ds.remove(tim)
            _ghi_phien(ds)
            return None

        # Kiểm tra hạn nhàn rỗi
        try:
            dc = datetime.fromisoformat(str(tim.get("dung_cuoi", "")).replace("Z", "+00:00"))
            if now - dc > timedelta(hours=phien_ranh_gio()):
                ds.remove(tim)
                _ghi_phien(ds)
                return None
        except Exception:
            ds.remove(tim)
            _ghi_phien(ds)
            return None

        # Hợp lệ: cập nhật dung_cuoi — chỉ ghi đĩa khi đã cũ ≥ GHI_DUNG_CUOI_GIAY
        if now - dc >= timedelta(seconds=GHI_DUNG_CUOI_GIAY):
            tim["dung_cuoi"] = bay_gio_str
            _ghi_phien(ds)
        return dict(tim)


_DA_CANH_BAO_TIN_PROXY = False


def xoa_canh_bao_tin_proxy() -> None:
    """Xoá cờ cảnh báo tin_proxy (dùng cho test)."""
    global _DA_CANH_BAO_TIN_PROXY
    _DA_CANH_BAO_TIN_PROXY = False


def la_https(request: Request) -> bool:
    """Xác định yêu cầu có qua HTTPS hay không (xét cả proxy tin cậy nếu có bật WEB2API_TIN_PROXY).

    Nếu địa chỉ công khai đã cấu hình là https:// và yêu cầu đến từ host đó, tự động coi là HTTPS
    để bật cờ Secure cho cookie bảo vệ người dùng, đồng thời ghi cảnh báo nhật ký một lần nếu
    WEB2API_TIN_PROXY chưa được bật.
    """
    global _DA_CANH_BAO_TIN_PROXY
    if request.url.scheme == "https":
        return True
    if dia_chi.la_tin_proxy():
        x_proto = request.headers.get("x-forwarded-proto", "").lower().strip()
        if x_proto == "https":
            return True

    # Địa chỉ công khai đã cấu hình là https:// và yêu cầu đến từ host đó
    dia_chi_ck = dia_chi.dia_chi_cong_khai_da_cau_hinh()
    if dia_chi_ck:
        try:
            tach_ck = urllib.parse.urlsplit(dia_chi_ck)
            if tach_ck.scheme.lower() == "https":
                host_req = dia_chi._mot_gia_tri(request, "host") or ""
                auth = dia_chi._chuan_hoa_authority(host_req)
                if auth:
                    tach_req = urllib.parse.urlsplit(f"//{auth}")
                    if (tach_req.hostname or "").lower() == (tach_ck.hostname or "").lower():
                        if not dia_chi.la_tin_proxy() and not _DA_CANH_BAO_TIN_PROXY:
                            _DA_CANH_BAO_TIN_PROXY = True
                            nhat_ky.ghi(
                                "he_thong",
                                "canh_bao",
                                "Địa chỉ công khai dùng HTTPS nhưng WEB2API_TIN_PROXY chưa bật — đã tự bật Secure cho cookie theo Host",
                                public_url=dia_chi_ck,
                                tin_proxy=False,
                            )
                        return True
        except Exception:
            pass

    return False


def lay_token_tu_cookie(request: Request) -> str | None:
    """Đọc token từ cookie (ưu tiên __Host-w2a_phien rồi đến w2a_phien)."""
    return request.cookies.get("__Host-w2a_phien") or request.cookies.get("w2a_phien")


def xac_thuc_cookie(request: Request) -> dict[str, Any] | None:
    """Đọc cookie phiên từ request và xác thực."""
    token = lay_token_tu_cookie(request)
    if not token:
        return None
    return xac_thuc_token(token)


def dat_cookie_phien(response: Response, request: Request, token: str) -> None:
    """Đặt cookie phiên bảo mật: HttpOnly, SameSite=Strict, Path=/, Secure khi HTTPS; không Domain, không Max-Age."""
    dung_https = la_https(request)
    ten_cookie = "__Host-w2a_phien" if dung_https else "w2a_phien"
    response.set_cookie(
        key=ten_cookie,
        value=token,
        httponly=True,
        samesite="strict",
        secure=dung_https,
        path="/",
    )


def xoa_cookie_phien(response: Response) -> None:
    """Xoá cả hai cookie phiên, cùng thuộc tính như lúc đặt (trình duyệt bỏ lệnh xoá `__Host-` thiếu Secure)."""
    response.delete_cookie("__Host-w2a_phien", path="/", secure=True, httponly=True, samesite="strict")
    response.delete_cookie("w2a_phien", path="/", httponly=True, samesite="strict")


def kiem_tra_csrf(request: Request, phien: dict[str, Any]) -> None:
    """Bắt buộc header X-CSRF-Token cho các phương thức thay đổi trạng thái khi xác thực bằng cookie."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token_gui = request.headers.get("x-csrf-token", "").strip()
        token_phien = str(phien.get("csrf", ""))
        # Kiểm DẠNG trước (độ dài cố định, chỉ base64url) rồi mới so bytes thời gian hằng số — không ngoại lệ với chuỗi lạ.
        if not la_dang_token(token_gui) or not la_dang_token(token_phien) or not hmac.compare_digest(
            token_gui.encode("utf-8"), token_phien.encode("utf-8")
        ):
            raise LoiOpenAI(
                403,
                "Sai hoặc thiếu token CSRF.",
                loai="permission_error",
                code="csrf_token_invalid",
            )


def thu_hoi_phien(bam: str) -> bool:
    """Thu hồi một phiên theo băm SHA-256."""
    with _KHOA_LUONG:
        ds = _doc_phien()
        truoc = len(ds)
        ds = [p for p in ds if p.get("bam") != bam]
        if len(ds) < truoc:
            _ghi_phien(ds)
            return True
        return False


def thu_hoi_phien_nguoi_dung(nguoi_dung_id: str, tru_bam: str | None = None) -> int:
    """Thu hồi toàn bộ phiên của một người dùng (tuỳ chọn giữ lại phiên hiện tại)."""
    with _KHOA_LUONG:
        ds = _doc_phien()
        truoc = len(ds)
        ds = [p for p in ds if p.get("nguoi_dung_id") != nguoi_dung_id or (tru_bam and p.get("bam") == tru_bam)]
        da_xoa = truoc - len(ds)
        if da_xoa > 0:
            _ghi_phien(ds)
        return da_xoa
