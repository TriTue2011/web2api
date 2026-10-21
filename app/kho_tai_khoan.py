"""Kho tài khoản đã lưu (NV144): email + nhãn + MẬT KHẨU + TOTP SECRET.

Người dùng thêm tài khoản theo ô trên giao diện; kho lưu bí mật để sau này
đăng nhập lại mà không cần gõ lại. Tệp kho nằm ở
``<WEB2API_DATA>/bi_mat/_tai_khoan_da_luu.json``, ghi qua ``app.kho``
(quyền 0600, tự động mã hoá khi nghỉ nếu đã đặt ``WEB2API_KHOA_CHINH``).

Lõi dùng chữ trung lập «tài khoản»/«nhà cung cấp», không tên nhà cung cấp nào.

Quy ước bảo mật:
- Bí mật chỉ rời kho qua :func:`lay_bi_mat_dang_nhap` dưới dạng ``BiMatTam``/
  ``BiMatTotp`` (bộ nhớ, xoá sau dùng); KHÔNG BAO GIỜ qua API liệt kê.
- Kho hỏng (không phải JSON / không đúng lược đồ) → ném ``LoiKhoTaiKhoan``
  (mã ``kho_hong``): người gọi phải ĐÓNG CỬA, không lưu một phần.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import re
import time

from app import kho
from app.tai_khoan_hang_loat import (
    _MAU_EMAIL,
    BiMatTam,
    BiMatTotp,
    DongDaTach,
    kiem_tra_va_tao_otp,
)

log = logging.getLogger("web2api.kho_tai_khoan")

#: Tên tệp trong ``bi_mat/`` (hợp lệ với ``ten_bi_mat_hop_le``).
TEN_KHO = "_tai_khoan_da_luu"
#: Tệp công tắc «tự đăng nhập lại» (không chứa bí mật).
TEN_CONG_TAC = "_tu_dang_nhap_lai"
#: Tệp trạng thái thử lại (số lần hỏng liên tiếp, lần thử cuối).
TEN_TRANG_THAI = "_tai_khoan_da_luu_trang_thai"

#: Trạng thái «tạm dừng» (NV197b): tài khoản còn lưu trong kho nhưng bị lõi
#: BỎ QUA khi chọn/đăng nhập (lấy ý từ c2a `account_service.update_account`
#: {"status": "disabled"}). Không tự bật lại theo giờ.
TRANG_THAI_TAM_DUNG = "tam_dung"

DOI_DAI_EMAIL = 254
DOI_DAI_MAT_KHAU = 256
DOI_DAI_TOTP = 256
DOI_DAI_NHAN = 120
DOI_DAI_MODULE_ID = 64
DOI_DAI_NHOM = 64
DOI_DAI_BAC_GOI = 64

#: Giá trị hiển thị khi chưa biết bậc gói (NV197c) — KHÔNG đoán từ dữ liệu khác.
BAC_GOI_KHONG_BIET = "khong_biet"

_MAU_KY_TU_CAM = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TIEN_TO_ID = "tk-"


class LoiKhoTaiKhoan(ValueError):
    """Lỗi kho tài khoản. ``ma`` là mã lỗi rút gọn cho API (không lộ giá trị)."""

    def __init__(self, ma: str):
        super().__init__(ma)
        self.ma = ma


def _kiem_ky_tu(gia_tri: str, ten_truong: str) -> str:
    """Từ chối ký tự điều khiển / surrogate → ``LoiKhoTaiKhoan("invalid_value")``."""
    if _MAU_KY_TU_CAM.search(gia_tri):
        raise LoiKhoTaiKhoan("invalid_value")
    try:
        gia_tri.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError):
        raise LoiKhoTaiKhoan("invalid_value")
    for ch in gia_tri:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise LoiKhoTaiKhoan("invalid_value")
    return gia_tri


def chuan_hoa_email(email) -> str:
    """Chuẩn hoá + kiểm email theo khuôn hẹp của ``tai_khoan_hang_loat``."""
    if isinstance(email, bool) or not isinstance(email, str):
        raise LoiKhoTaiKhoan("invalid_value")
    e = email.strip().casefold()
    _kiem_ky_tu(email, "email")
    if not e or len(e) > DOI_DAI_EMAIL or not _MAU_EMAIL.fullmatch(e):
        raise LoiKhoTaiKhoan("invalid_email")
    return e


def kiem_mat_khau(mat_khau) -> str:
    """Kiểm mật khẩu: chuỗi, không rỗng, có trần độ dài."""
    if isinstance(mat_khau, bool) or not isinstance(mat_khau, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(mat_khau, "mat_khau")
    if not mat_khau or len(mat_khau) > DOI_DAI_MAT_KHAU:
        raise LoiKhoTaiKhoan("invalid_value")
    return mat_khau


def kiem_totp_secret(totp_secret) -> str | None:
    """Kiểm TOTP secret: None/"" → None (không tích); phải là KHOÁ Base32.

    Mã 6–8 chữ số dùng một lần bị TỪ CHỐI (lưu lại vô nghĩa); chỉ nhận khoá
    Base32 qua ``kiem_tra_va_tao_otp`` rồi ép kiểu ``BiMatTotp``.
    """
    if totp_secret is None:
        return None
    if isinstance(totp_secret, bool) or not isinstance(totp_secret, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(totp_secret, "totp_secret")
    s = totp_secret.strip()
    if not s:
        return None
    if len(s) > DOI_DAI_TOTP:
        raise LoiKhoTaiKhoan("invalid_value")
    try:
        bm = kiem_tra_va_tao_otp(s)
    except ValueError:
        raise LoiKhoTaiKhoan("invalid_value") from None
    if bm is None:
        return None
    if not isinstance(bm, BiMatTotp):
        # Mã số dùng một lần (BiMatTam) — không lưu.
        bm.xoa()
        raise LoiKhoTaiKhoan("invalid_value")
    # Lấy KHOÁ Base32 đã chuẩn hoá (BiMatTotp.lay() tính mã 6 số thời điểm đó —
    # không phải khoá; đọc _buf mới là khoá mà kiem_tra_va_tao_otp đã kiểm).
    try:
        khoa = bytes(bm._buf).decode("ascii")
    finally:
        bm.xoa()
    return khoa


def kiem_nhan(nhan) -> str:
    """Kiểm nhãn: chuỗi ngắn, không bắt buộc."""
    if nhan is None:
        return ""
    if isinstance(nhan, bool) or not isinstance(nhan, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(nhan, "nhan")
    n = nhan.strip()
    if len(n) > DOI_DAI_NHAN:
        raise LoiKhoTaiKhoan("invalid_value")
    return n


def kiem_trang_thai(trang_thai) -> str:
    """Kiểm trạng thái tài khoản (NV197b): "" (đang dùng) hoặc "tam_dung".

    None → "" (mặc định bật). Giá trị liệt kê đóng — không dò chuỗi tự do.
    """
    if trang_thai is None:
        return ""
    if isinstance(trang_thai, bool) or not isinstance(trang_thai, str):
        raise LoiKhoTaiKhoan("invalid_value")
    t = trang_thai.strip()
    if t not in ("", TRANG_THAI_TAM_DUNG):
        raise LoiKhoTaiKhoan("invalid_value")
    return t


def kiem_module_id(module_id) -> str:
    """Kiểm id module sở hữu tài khoản (NV197c): "" = chưa gắn module nào.

    None → "". Không liệt kê đóng — module nào đã cài cũng nhận (tầng route
    kiểm module đã cài trước khi truyền vào).
    """
    if module_id is None:
        return ""
    if isinstance(module_id, bool) or not isinstance(module_id, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(module_id, "module_id")
    m = module_id.strip()
    if len(m) > DOI_DAI_MODULE_ID:
        raise LoiKhoTaiKhoan("invalid_value")
    return m


def kiem_nhom(nhom) -> str:
    """Kiểm nhóm tài khoản (NV197c): "" = chưa có nhóm.

    None → "". Không liệt kê đóng theo từ khoá — nhóm suy từ
    ``manifest.nhom.id`` của module sở hữu ở tầng route, kho chỉ lưu.
    """
    if nhom is None:
        return ""
    if isinstance(nhom, bool) or not isinstance(nhom, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(nhom, "nhom")
    n = nhom.strip()
    if len(n) > DOI_DAI_NHOM:
        raise LoiKhoTaiKhoan("invalid_value")
    return n


def kiem_bac_goi(bac_goi) -> str:
    """Kiểm bậc gói (NV197c): "" = chưa biết (hiển thị ``khong_biet``).

    None → "". Chỉ nhận giá trị đã biết (người dùng nhập tay hoặc nguồn tin
    cậy cung cấp) — KHÔNG đoán từ email/token/tên miền.
    """
    if bac_goi is None:
        return ""
    if isinstance(bac_goi, bool) or not isinstance(bac_goi, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(bac_goi, "bac_goi")
    b = bac_goi.strip()
    if len(b) > DOI_DAI_BAC_GOI:
        raise LoiKhoTaiKhoan("invalid_value")
    return b


def ma_tai_khoan(email_chuan: str) -> str:
    """ID ổn định từ email đã chuẩn hoá (``tk-`` + 16 hex SHA-256)."""
    return _TIEN_TO_ID + hashlib.sha256(email_chuan.encode("utf-8")).hexdigest()[:16]


def che_email(email: str) -> str:
    """Che email: ``…***@example.com`` — chỉ chừa tên miền.

    Không bao giờ lộ toàn bộ hay một phần định danh người dùng.
    """
    try:
        _dau, mien = str(email).split("@", 1)
    except ValueError:
        return "…"
    return ("…***@" + mien) if mien else "…"


def _doc() -> dict:
    """Đọc toàn bộ kho. Hỏng → ``LoiKhoTaiKhoan("kho_hong")`` (đóng cửa)."""
    try:
        du_lieu = kho.doc_bi_mat(TEN_KHO)
    except Exception as e:
        raise LoiKhoTaiKhoan("kho_hong") from e
    if not isinstance(du_lieu, dict):
        raise LoiKhoTaiKhoan("kho_hong")
    for id_, ban_ghi in du_lieu.items():
        if not isinstance(id_, str) or not isinstance(ban_ghi, dict):
            raise LoiKhoTaiKhoan("kho_hong")
        if ban_ghi.get("loai") == "nhap":
            # Bản ghi token nhập từ dịch vụ khác (NV197e): định danh bằng
            # (nguon, dinh_danh), không bắt buộc email.
            try:
                dinh_danh = ban_ghi.get("dinh_danh")
                nguon = ban_ghi.get("nguon")
                hop_le = (
                    id_ == ma_tai_khoan_nhap(nguon, dinh_danh)
                    and isinstance(nguon, str) and nguon in NGUON_NHAP_CHO_PHEP
                    and isinstance(dinh_danh, str) and dinh_danh
                    and isinstance(ban_ghi.get("bi_mat"), str) and ban_ghi.get("bi_mat")
                    and isinstance(ban_ghi.get("email"), str)
                    and isinstance(ban_ghi.get("nhan"), str)
                )
            except Exception:
                hop_le = False
            if not hop_le:
                raise LoiKhoTaiKhoan("kho_hong")
            continue
        if (
            not isinstance(ban_ghi.get("email"), str)
            or not isinstance(ban_ghi.get("mat_khau"), str)
            or id_ != ma_tai_khoan(ban_ghi["email"])
        ):
            raise LoiKhoTaiKhoan("kho_hong")
        # Trường NV197c (tuỳ chọn, bản ghi cũ không có): có thì phải là chuỗi.
        for truong in ("module_id", "nhom", "bac_goi"):
            if truong in ban_ghi and not isinstance(ban_ghi[truong], str):
                raise LoiKhoTaiKhoan("kho_hong")
    return du_lieu


def _ghi(du_lieu: dict) -> None:
    """Ghi toàn bộ kho (nguyên tử qua ``app.kho``)."""
    kho.ghi_bi_mat(TEN_KHO, du_lieu)


def ban_cong_khai(ban_ghi: dict) -> dict:
    """Dạng công khai: email CHE, cờ có/không bí mật — KHÔNG bí mật thô.

    Nhãn được escape HTML ở biên ra (chống XSS khi giao diện chèn vào DOM).
    NV197c: thêm ``module_id``/``nhom``/``bac_goi`` (nhóm suy từ module sở
    hữu, bậc gói chỉ khi đã biết — chưa biết hiển thị ``khong_biet``).
    """
    if ban_ghi.get("loai") == "nhap":
        # Bản ghi token nhập (NV197e): không bao giờ lộ bi_mat hay dinh_danh thô
        # (dinh_danh thường chính là email ở nguồn ngoài).
        # NV197c: bản ghi nhập nhóm theo NGUỒN NHẬP (danh sách đóng
        # NGUON_NHAP_CHO_PHEP), không ghi đè bằng module.
        email = ban_ghi.get("email", "")
        id_ = ban_ghi.get("id", "")
        nguon = ban_ghi.get("nguon", "")
        return {
            "id": id_,
            "loai": "nhap",
            "nguon": nguon,
            "nhom": nguon,
            "bac_goi": ban_ghi.get("bac_goi") or BAC_GOI_KHONG_BIET,
            "module_id": "",
            "email_che": che_email(email) if email else None,
            "nhan": html.escape(ban_ghi.get("nhan", ""), quote=False),
            "co_bi_mat": bool(ban_ghi.get("bi_mat")),
            "trang_thai": ban_ghi.get("trang_thai", ""),
            "cap_nhat": ban_ghi.get("cap_nhat", 0),
            # NV197d: lượng dùng hôm nay/7 ngày + hạn mức thụ động («khong_biet» khi chưa có số liệu).
            **_tom_tat_dung(id_),
        }
    email = ban_ghi.get("email", "")
    id_ = ban_ghi.get("id", "")
    return {
        "id": id_,
        "email_che": che_email(email),
        "nhan": html.escape(ban_ghi.get("nhan", ""), quote=False),
        "module_id": ban_ghi.get("module_id", ""),
        "nhom": ban_ghi.get("nhom", ""),
        "bac_goi": ban_ghi.get("bac_goi") or BAC_GOI_KHONG_BIET,
        "co_mat_khau": bool(ban_ghi.get("mat_khau")),
        "co_totp": bool(ban_ghi.get("totp_secret")),
        "trang_thai": ban_ghi.get("trang_thai", ""),
        "cap_nhat": ban_ghi.get("cap_nhat", 0),
        # NV197d: lượng dùng hôm nay/7 ngày + hạn mức thụ động («khong_biet» khi chưa có số liệu).
        **_tom_tat_dung(id_),
    }


def _tom_tat_dung(id_: str) -> dict:
    """Tóm tắt lượng dùng/hạn mức cho ban_cong_khai — không bao giờ ném."""
    try:
        from app import luong_dung_tai_khoan as ldtk
        tt = ldtk.tom_tat(id_)
        return {"dung": tt["dung"], "han_muc": tt["han_muc"]}
    except Exception:
        return {"dung": {"hom_nay": 0, "7_ngay": 0}, "han_muc": "khong_biet"}


def luu_tai_khoan(email, mat_khau, totp_secret=None, nhan="",
                  trang_thai=None, id=None, module_id=None, nhom=None,
                  bac_goi=None) -> dict:
    """Lưu mới hoặc cập nhật theo email hoặc id (trùng email → cập nhật, không trùng lặp).

    `trang_thai`: None → giữ nguyên cũ (mặc định bật); "" hoặc "tam_dung"
    → đặt mới (kiểm qua ``kiem_trang_thai``, giá trị khác → invalid_value).
    `module_id`/`nhom`/`bac_goi` (NV197c): None → giữ nguyên cũ; "" hoặc chuỗi
    hợp lệ → đặt mới. `nhom` do tầng route suy từ manifest module trước khi
    truyền vào — kho không tự suy.
    Trả bản công khai. Kho hỏng → ném ``LoiKhoTaiKhoan`` (không lưu dở).
    """
    mk = kiem_mat_khau(mat_khau)
    totp = kiem_totp_secret(totp_secret)
    nhan_sach = kiem_nhan(nhan)
    with kho.giu_khoa_kho():
        du_lieu = _doc()
        cu = None
        if id is not None:
            if isinstance(id, bool) or not isinstance(id, str):
                raise LoiKhoTaiKhoan("invalid_value")
            for k, ban_ghi_cu in du_lieu.items():
                if hmac.compare_digest(k, id):
                    cu = ban_ghi_cu
                    break
            if cu is None:
                raise LoiKhoTaiKhoan("khong_tim_thay")
            if email is None or (isinstance(email, str) and email == che_email(cu["email"])):
                email_chuan = cu["email"]
            else:
                email_chuan = chuan_hoa_email(email)
                if email_chuan != cu["email"]:
                    raise LoiKhoTaiKhoan("invalid_value")
            id_ = cu["id"]
        else:
            email_chuan = chuan_hoa_email(email)
            id_ = ma_tai_khoan(email_chuan)
            cu = du_lieu.get(id_)

        # Bảo toàn TOTP secret nếu không truyền trường totp_secret
        if totp_secret is None and cu:
            totp_cuoi = cu.get("totp_secret", "")
        else:
            totp_cuoi = totp or ""

        tt_moi = cu.get("trang_thai", "") if cu else ""
        if trang_thai is not None:
            tt_moi = kiem_trang_thai(trang_thai)

        # NV197c: None → giữ nguyên cũ; "" / chuỗi hợp lệ → đặt mới.
        module_moi = cu.get("module_id", "") if cu else ""
        if module_id is not None:
            module_moi = kiem_module_id(module_id)
        nhom_moi = cu.get("nhom", "") if cu else ""
        if nhom is not None:
            nhom_moi = kiem_nhom(nhom)
        bac_goi_moi = cu.get("bac_goi", "") if cu else ""
        if bac_goi is not None:
            bac_goi_moi = kiem_bac_goi(bac_goi)

        ban_ghi = {
            "id": id_,
            "email": email_chuan,
            "nhan": nhan_sach,
            "mat_khau": mk,
            "totp_secret": totp_cuoi,
            "trang_thai": tt_moi,
            "module_id": module_moi,
            "nhom": nhom_moi,
            "bac_goi": bac_goi_moi,
            "cap_nhat": int(time.time()),
        }
        du_lieu[id_] = ban_ghi
        _ghi(du_lieu)

        # Nguyên tắc vòng đời: đổi thông tin đăng nhập ⇒ trạng thái thử lại về gốc
        thong_tin_doi = (
            cu is None
            or cu.get("mat_khau") != mk
            or cu.get("totp_secret", "") != totp_cuoi
        )
        if thong_tin_doi:
            tt = _doc_trang_thai()
            if id_ in tt:
                del tt[id_]
                _ghi_trang_thai(tt)
    return ban_cong_khai(ban_ghi)


def liet_ke() -> list[dict]:
    """Liệt kê mọi tài khoản đã lưu (dạng công khai, không bí mật)."""
    du_lieu = _doc()
    ds = [ban_cong_khai(b) for b in du_lieu.values()]
    ds.sort(key=lambda d: d["cap_nhat"], reverse=True)
    return ds


def tim_theo_id(id_) -> dict | None:
    """Tìm bản ghi nội bộ theo id (so bằng ``hmac.compare_digest``, chống IDOR)."""
    if isinstance(id_, bool) or not isinstance(id_, str):
        return None
    du_lieu = _doc()
    for k, ban_ghi in du_lieu.items():
        if hmac.compare_digest(k, id_):
            return ban_ghi
    return None


def tim_theo_email(email) -> dict | None:
    """Tìm bản ghi nội bộ theo email (đã chuẩn hoá)."""
    try:
        email_chuan = chuan_hoa_email(email)
    except LoiKhoTaiKhoan:
        return None
    return tim_theo_id(ma_tai_khoan(email_chuan))


# ── Nhập tài khoản token từ dịch vụ khác (NV197e) ────────────────────────────
# Ý từ c2a (services/cpa_service.py CPAImportService + services/sub2api_service.py
# Sub2APIImportService @ d879b03, CHỈ ĐỌC): nhập chạy theo lô, từng mục lấy
# token rồi gọi add_accounts — kết quả từng mục: added/skipped/failed, lỗi
# từng mục không chặn cả lô. web2api: cùng kho TEN_KHO (không kho thứ hai),
# bản ghi loại "nhap" (token/cookie, không phải email+mật khẩu), khử trùng
# theo (nguon, dinh_danh), ghi NGUYÊN TỬ một lần dưới khoá kho.

#: Nguồn nhập cho phép — danh sách ĐÓNG (đúng các nguồn module đọc được),
#: không suy luận theo từ khoá.
NGUON_NHAP_CHO_PHEP = ("ninerouter", "sub2api", "cpa")
DOI_DAI_DINH_DANH_NHAP = 256
DOI_DAI_BI_MAT_NHAP = 4096


def ma_tai_khoan_nhap(nguon, dinh_danh) -> str:
    """ID ổn định cho tài khoản nhập: ``tk-`` + 16 hex SHA-256 của
    ``"nhap" + nguon + dinh_danh`` (phân tách bằng NUL để không lẫn)."""
    goc = "nhap\x00" + str(nguon or "") + "\x00" + str(dinh_danh or "")
    return _TIEN_TO_ID + hashlib.sha256(goc.encode("utf-8")).hexdigest()[:16]


def _kiem_nguon_nhap(nguon) -> str:
    if isinstance(nguon, bool) or not isinstance(nguon, str):
        raise LoiKhoTaiKhoan("nguon_khong_ho_tro")
    g = nguon.strip()
    if g not in NGUON_NHAP_CHO_PHEP:
        raise LoiKhoTaiKhoan("nguon_khong_ho_tro")
    return g


def _kiem_dinh_danh_nhap(dinh_danh) -> str:
    if isinstance(dinh_danh, bool) or not isinstance(dinh_danh, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(dinh_danh, "dinh_danh")
    d = dinh_danh.strip()
    if not d or len(d) > DOI_DAI_DINH_DANH_NHAP:
        raise LoiKhoTaiKhoan("invalid_value")
    return d


def _kiem_bi_mat_nhap(bi_mat) -> str:
    if isinstance(bi_mat, bool) or not isinstance(bi_mat, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(bi_mat, "bi_mat")
    b = bi_mat.strip()
    if not b or len(b) > DOI_DAI_BI_MAT_NHAP:
        raise LoiKhoTaiKhoan("invalid_value")
    return b


def nhap_nhieu_tai_khoan(danh_sach: list) -> list:
    """Ghi NGUYÊN TỬ một lô tài khoản nhập vào cùng kho.

    Mỗi mục: dict {"nguon", "dinh_danh", "bi_mat", "email"|None, "nhan"|""}.
    Trả list kết quả từng mục {"ket_qua": "da_them"|"trung"|"loi", "loi"?: mã}.
    Trùng (nguon, dinh_danh) → "trung", KHÔNG ghi đè bí mật cũ. Toàn bộ lô
    đọc-sửa-ghi MỘT lần dưới khoá kho, một lần `_ghi` → kho hỏng giữa chừng
    thì không có gì được ghi (không nửa vời).
    """
    with kho.giu_khoa_kho():
        du_lieu = _doc()
        da_thay = set()
        ket_qua = []
        for muc in danh_sach:
            try:
                nguon = _kiem_nguon_nhap(muc.get("nguon"))
                dinh_danh = _kiem_dinh_danh_nhap(muc.get("dinh_danh"))
                bi_mat = _kiem_bi_mat_nhap(muc.get("bi_mat"))
                email_tho = muc.get("email")
                email_chuan = chuan_hoa_email(email_tho) if email_tho else ""
                nhan_sach = kiem_nhan(muc.get("nhan") or "")
            except LoiKhoTaiKhoan as e:
                ket_qua.append({"ket_qua": "loi", "loi": e.ma})
                continue
            id_ = ma_tai_khoan_nhap(nguon, dinh_danh)
            if id_ in du_lieu or id_ in da_thay:
                ket_qua.append({"ket_qua": "trung"})
                continue
            du_lieu[id_] = {
                "id": id_,
                "loai": "nhap",
                "nguon": nguon,
                "dinh_danh": dinh_danh,
                "email": email_chuan,
                "nhan": nhan_sach,
                "bi_mat": bi_mat,
                "trang_thai": "",
                "cap_nhat": int(time.time()),
            }
            da_thay.add(id_)
            ket_qua.append({"ket_qua": "da_them"})
        if da_thay:
            _ghi(du_lieu)
    return ket_qua


def xoa_tai_khoan(id_) -> bool:
    """Xoá tài khoản theo id. Không có → False (người gọi đổi thành 404)."""
    if isinstance(id_, bool) or not isinstance(id_, str):
        return False
    with kho.giu_khoa_kho():
        du_lieu = _doc()
        muc_tieu = None
        for k in du_lieu:
            if hmac.compare_digest(k, id_):
                muc_tieu = k
                break
        if muc_tieu is None:
            return False
        del du_lieu[muc_tieu]
        _ghi(du_lieu)
        # Nguyên tắc vòng đời: tài khoản xoá ⇒ trạng thái thử lại bị xoá theo
        tt = _doc_trang_thai()
        if muc_tieu in tt:
            del tt[muc_tieu]
            _ghi_trang_thai(tt)
    return True


def dat_tam_dung(id_, tam_dung: bool) -> dict | None:
    """Đặt/gỡ cờ tạm dừng theo id, ghi nguyên tử dưới khoá kho (NV197b).

    ``tam_dung=True`` → ``trang_thai="tam_dung"``; False → "" (đang dùng).
    Tra cứu bằng ``hmac.compare_digest`` như mọi nơi khác (chống IDOR dò id).
    Trả bản công khai; không có → None (người gọi đổi thành 404).
    Đổi trạng thái KHÔNG reset trạng thái thử lại (không phải đổi bí mật).
    """
    if not isinstance(tam_dung, bool):
        raise LoiKhoTaiKhoan("invalid_value")
    if isinstance(id_, bool) or not isinstance(id_, str):
        return None
    with kho.giu_khoa_kho():
        du_lieu = _doc()
        muc_tieu = None
        for k in du_lieu:
            if hmac.compare_digest(k, id_):
                muc_tieu = k
                break
        if muc_tieu is None:
            return None
        ban_ghi = du_lieu[muc_tieu]
        ban_ghi["trang_thai"] = TRANG_THAI_TAM_DUNG if tam_dung else ""
        ban_ghi["cap_nhat"] = int(time.time())
        _ghi(du_lieu)
        return ban_cong_khai(ban_ghi)


def da_tam_dung(id_) -> bool:
    """Tài khoản có đang tạm dừng không (id lạ → False)."""
    ban_ghi = tim_theo_id(id_)
    return bool(ban_ghi) and ban_ghi.get("trang_thai") == TRANG_THAI_TAM_DUNG


def dat_module(id_, module_id, nhom) -> dict | None:
    """Ghi nhận module sở hữu tài khoản (NV197c) — gọi sau ĐĂNG NHẬP THÀNH CÔNG.

    `module_id`/`nhom` đã được kiểm ở tầng route (module đã cài, `nhom` suy từ
    ``manifest.nhom.id``). Ghi nguyên tử dưới khoá kho; bản ghi nhập (loại
    "nhap") giữ nhóm theo nguồn nhập, không ghi đè. Trả bản công khai;
    id lạ → None.
    """
    mid = kiem_module_id(module_id)
    nh = kiem_nhom(nhom)
    if isinstance(id_, bool) or not isinstance(id_, str):
        return None
    with kho.giu_khoa_kho():
        du_lieu = _doc()
        muc_tieu = None
        for k in du_lieu:
            if hmac.compare_digest(k, id_):
                muc_tieu = k
                break
        if muc_tieu is None:
            return None
        ban_ghi = du_lieu[muc_tieu]
        if ban_ghi.get("loai") == "nhap":
            return ban_cong_khai(ban_ghi)
        ban_ghi["module_id"] = mid
        ban_ghi["nhom"] = nh
        ban_ghi["cap_nhat"] = int(time.time())
        _ghi(du_lieu)
        return ban_cong_khai(ban_ghi)


def lay_bi_mat_dang_nhap(id_: str) -> tuple[BiMatTam, BiMatTotp | None] | None:
    """Lấy bí mật đăng nhập dưới dạng ``BiMatTam``/``BiMatTotp`` (nội bộ).

    Chỉ dùng cho đường «đăng nhập bằng tài khoản đã lưu» và tự đăng nhập lại.
    Người gọi phải ``xoa()`` sau khi dùng xong.
    """
    ban_ghi = tim_theo_id(id_)
    if ban_ghi is None or not ban_ghi.get("mat_khau"):
        return None
    mat_khau = BiMatTam(ban_ghi["mat_khau"])
    totp = None
    if ban_ghi.get("totp_secret"):
        totp = BiMatTotp(ban_ghi["totp_secret"])
    return mat_khau, totp


# ── Công tắc «tự đăng nhập lại» ──────────────────────────────────────────────

def tu_dang_nhap_lai_bat() -> bool:
    """Công tắc tự đăng nhập lại khi phiên hết hạn. Mặc định BẬT."""
    try:
        du_lieu = kho.doc_bi_mat(TEN_CONG_TAC)
    except Exception:
        return True
    if not isinstance(du_lieu, dict) or "bat" not in du_lieu:
        return True
    return bool(du_lieu["bat"])


def dat_tu_dang_nhap_lai(bat: bool) -> bool:
    """Đặt công tắc tự đăng nhập lại. Trả trạng thái mới."""
    if not isinstance(bat, bool):
        raise LoiKhoTaiKhoan("invalid_value")
    kho.ghi_bi_mat(TEN_CONG_TAC, {"bat": bat, "cap_nhat": int(time.time())})
    return bat


def gian_cach_giay() -> float:
    """Giãn cách giữa hai lần tự đăng nhập lại (giây)."""
    try:
        return max(0.0, float(os.environ.get("WEB2API_DANG_NHAP_GIAN_CACH_GIAY", "300")))
    except (TypeError, ValueError):
        return 300.0


def _con_het_han_muc(id_: str) -> int:
    """Epoch «hết hạn mức tới» còn hiệu lực, 0 nếu hết/không có (NV197d). Không bao giờ ném."""
    try:
        from app import luong_dung_tai_khoan as ldtk
        return ldtk.con_het_han_muc(id_)
    except Exception:
        return 0


def _doc_trang_thai() -> dict:
    try:
        du_lieu = kho.doc_bi_mat(TEN_TRANG_THAI)
    except Exception:
        return {}
    return du_lieu if isinstance(du_lieu, dict) else {}


def _ghi_trang_thai(du_lieu: dict) -> None:
    kho.ghi_bi_mat(TEN_TRANG_THAI, du_lieu)


async def thu_dang_nhap_lai(*, module, module_id: str, dinh_danh: str) -> dict:
    """Tự đăng nhập lại bằng tài khoản đã lưu (NV144 §4).

    `dinh_danh` là email của phiên cần khôi phục. Trả dict:
    - `{"thu": False, "ly_do": "da_tat"}` — công tắc đang TẮT.
    - `{"thu": False, "ly_do": "khong_co_tai_khoan"}` — không có tài khoản
      đã lưu khớp email (hoặc thiếu mật khẩu).
    - `{"thu": False, "ly_do": "tam_dung"}` — tài khoản đã tạm dừng (NV197b):
      bị BỎ QUA, không chạm bí mật, không gọi module; lỗi rõ ràng để tầng trên
      biết không còn tài khoản khả dụng.
    - `{"thu": False, "ly_do": "het_han_muc"}` — tài khoản hết hạn mức tới một
      giờ nào đó (NV197d): bị BỎ QUA đúng đến lúc đặt lại, không chạm bí mật.
    - `{"thu": False, "ly_do": "hong_2_lien_tiep"}` — đã hỏng 2 liên tiếp,
      dừng hẳn.
    - `{"thu": False, "ly_do": "gian_cach"}` — chưa hết giãn cách
      `WEB2API_DANG_NHAP_GIAN_CACH_GIAY` từ lần thử trước.
    - `{"thu": False, "ly_do": "khong_co_dang_nhap"}` — module không có hàm
      `dang_nhap` theo hợp đồng chung.
    - `{"thu": True, "thanh_cong": bool}` — đã thử gọi module.

    Chỉ gọi khi module báo phiên hết hạn/đăng nhập hỏng. Luồng: công tắc BẬT
    → tìm tài khoản đã lưu khớp email → giãn cách → dừng khi hỏng 2 liên
    tiếp → gọi `module.dang_nhap(dong)` với `BiMatTam`/`BiMatTotp` (hợp đồng
    chung, CHỈ GỌI, không sửa module). Bí mật được xoá khỏi bộ nhớ ngay sau
    lượt gọi.
    """
    if not tu_dang_nhap_lai_bat():
        return {"thu": False, "ly_do": "da_tat"}
    email = dinh_danh if isinstance(dinh_danh, str) else ""
    ban_ghi = tim_theo_email(email) if email else None
    if ban_ghi is None or not ban_ghi.get("mat_khau"):
        return {"thu": False, "ly_do": "khong_co_tai_khoan"}
    if ban_ghi.get("trang_thai") == TRANG_THAI_TAM_DUNG:
        # NV197b: tài khoản tạm dừng bị BỎ QUA khi chọn — không chạm bí mật,
        # không gọi module.dang_nhap; lỗi rõ ràng, không âm thầm dùng.
        return {"thu": False, "ly_do": "tam_dung"}
    id_ = ban_ghi["id"]
    if _con_het_han_muc(id_):
        # NV197d: tài khoản hết hạn mức tới một giờ nào đó bị BỎ QUA đúng đến lúc
        # đặt lại — không chạm bí mật, không gọi module; lõi chọn tài khoản khác.
        return {"thu": False, "ly_do": "het_han_muc"}
    cap_nhat_dau = ban_ghi.get("cap_nhat", 0)
    bay_gio = time.time()
    tt = _doc_trang_thai().get(id_, {})
    if int(tt.get("hong_lien_tiep", 0)) >= 2:
        log.info("tu-dang-nhap-lai: %s dừng (hỏng 2 liên tiếp)", che_email(email))
        return {"thu": False, "ly_do": "hong_2_lien_tiep"}
    lan_cuoi = float(tt.get("lan_cuoi", 0.0))
    if lan_cuoi > 0 and bay_gio - lan_cuoi < gian_cach_giay():
        return {"thu": False, "ly_do": "gian_cach"}

    dang_nhap = getattr(module, "dang_nhap", None)
    if not callable(dang_nhap):
        return {"thu": False, "ly_do": "khong_co_dang_nhap"}
    bi_mat = lay_bi_mat_dang_nhap(id_)
    if bi_mat is None:
        return {"thu": False, "ly_do": "khong_co_tai_khoan"}
    mat_khau, totp = bi_mat
    dong = DongDaTach(dong=0, email=ban_ghi["email"], loai="mat_khau",
                      mat_khau=mat_khau, totp=totp)
    try:
        kq = await dang_nhap(dong)
        thanh_cong = bool(getattr(kq, "thanh_cong", False))
    except Exception as e:
        log.warning("tu-dang-nhap-lai: %s lỗi: %s", che_email(email), type(e).__name__)
        thanh_cong = False
    finally:
        dong.xoa_bi_mat()
    # Ghi trạng thái cuối DƯỚI KHOÁ KHO, đọc lại bản ghi trước: trong lúc
    # lượt thử đang chạy (await ở trên), quản trị có thể đã đổi thông tin
    # (reset xoá trạng thái) hoặc xoá hẳn tài khoản. Nếu bản ghi đã mất hoặc
    # `cap_nhat` đã đổi thì BỎ ghi — trạng thái của CẤU HÌNH CŨ không được
    # hồi sinh rồi chặn oan mật khẩu mới (đua soát chéo NV177).
    with kho.giu_khoa_kho():
        try:
            ban_ghi_moi = _doc().get(id_)
        except LoiKhoTaiKhoan:
            ban_ghi_moi = None
        if ban_ghi_moi is not None and ban_ghi_moi.get("cap_nhat", 0) == cap_nhat_dau:
            tt_moi = _doc_trang_thai()
            tt_moi[id_] = {
                "hong_lien_tiep": 0 if thanh_cong else int(tt.get("hong_lien_tiep", 0)) + 1,
                "lan_cuoi": bay_gio,
            }
            _ghi_trang_thai(tt_moi)
    log.info("tu-dang-nhap-lai: %s %s", che_email(email),
             "xong" if thanh_cong else "hỏng")
    return {"thu": True, "thanh_cong": thanh_cong}


def email_cua_phien(module, khoa: str) -> str:
    """Email của phiên (module_id, khoa) qua sổ danh tính; không có → "".

    Dùng cho hook tự đăng nhập lại ở `app/tu_lam_moi` (khoa là khoá phiên,
    còn kho tài khoản tra theo email).
    """
    try:
        lay_dt_id = getattr(module, "danh_tinh_id_cua", None)
        if not callable(lay_dt_id):
            return ""
        dt_id = lay_dt_id(khoa)
        if not dt_id:
            return ""
        from app import danh_tinh
        ban_ghi = danh_tinh.lay(dt_id)
        if isinstance(ban_ghi, dict) and isinstance(ban_ghi.get("email"), str):
            return ban_ghi["email"]
    except Exception as e:
        log.warning("tu-dang-nhap-lai: không lấy được email phiên: %s",
                    type(e).__name__)
    return ""
