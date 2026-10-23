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

#: Trạng thái TỪNG ĐƯỜNG DÙNG của một tài khoản (NV232) — danh sách ĐÓNG.
#: Một tài khoản có nhiều đường (đích dùng trong ``dung_cho``), mỗi đường một
#: trạng thái riêng: «san_sang» dùng được ngay; «het_han_muc» bỏ qua tới
#: ``moc_hoi_lai`` (epoch) rồi tự hồi; «can_xac_minh_dien_thoai» và
#: «can_dang_nhap_lai» cần người xử lý, không tự thử lại; «chua_lien_ket»
#: đường chưa gắn thông tin đăng nhập riêng. Bảng hành vi/hiển thị của các
#: trạng thái này nằm ở ``app/duong_dung.py`` (bảng khai báo).
TRANG_THAI_DUONG_HOP_LE = (
    "san_sang",
    "het_han_muc",
    "can_xac_minh_dien_thoai",
    "can_dang_nhap_lai",
    "chua_lien_ket",
)

DOI_DAI_EMAIL = 254
DOI_DAI_MAT_KHAU = 256
DOI_DAI_TOTP = 256
DOI_DAI_NHAN = 120
DOI_DAI_MODULE_ID = 64
DOI_DAI_NHOM = 64
DOI_DAI_BAC_GOI = 64
DOI_DAI_LOAI_TK = 64
DOI_DAI_DICH_DUNG = 64

# ── Phân loại AI theo thiết kế chủ máy 21/10/2026 (NV218) ────────────────────
# «Tài khoản» lưu theo NHÓM; mỗi nhóm có các LOẠI; một số (nhóm, loại) có
# ĐÍCH DÙNG. Tất cả là BẢNG KHAI BÁO (dữ liệu), kiểm hợp lệ tra bảng — không
# rải if theo tên cụ thể.
#
# NV256 (cổng kiến trúc): tên cụ thể KHÔNG còn chép cứng trong mã lõi —
# nguồn duy nhất là tệp dữ liệu ``du_lieu/phan_loai_ai.json`` ở gốc repo
# (đọc lúc import) + trường ``nhom`` trong ``manifest.json`` của từng module
# (hợp nhất lúc import: module khai nhóm mới thì nhóm đó tự thành nhóm hợp
# lệ mà không phải sửa mã lõi; bảng trong tệp dữ liệu vẫn là gốc cho
# loại/đích dùng/tên hiển thị/thứ tự ưu tiên).

def _tai_bang_phan_loai_ai() -> dict:
    """Đọc bảng phân loại AI từ tệp dữ liệu + hợp nhất ``nhom`` của module."""
    import json
    from pathlib import Path

    goc = Path(__file__).resolve().parent.parent
    try:
        bang = json.loads((goc / "du_lieu" / "phan_loai_ai.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"không đọc được bảng phân loại AI: {e}") from e
    try:
        thu_muc_module = goc / "modules"
        if thu_muc_module.is_dir():
            for man in sorted(thu_muc_module.glob("*/manifest.json")):
                try:
                    nhom = json.loads(man.read_text(encoding="utf-8")).get("nhom") or {}
                except (OSError, ValueError):
                    continue
                nid = nhom.get("id")
                if isinstance(nid, str) and nid and nid not in bang["nhom"]:
                    bang["nhom"].append(nid)
                    nten = nhom.get("ten")
                    bang["ten_nhom"][nid] = nten if isinstance(nten, str) and nten else nid
    except OSError:
        pass
    return bang


_BANG_PHAN_LOAI_AI = _tai_bang_phan_loai_ai()

#: Nhóm tài khoản AI — đọc từ dữ liệu (giữ đúng thứ tự trong tệp).
NHOM_AI = tuple(_BANG_PHAN_LOAI_AI["nhom"])

#: Loại tài khoản theo nhóm AI.
LOAI_TAI_KHOAN_THEO_NHOM = {
    nhom: tuple(_BANG_PHAN_LOAI_AI["loai_theo_nhom"].get(nhom, ()))
    for nhom in NHOM_AI
}

#: Đích dùng theo (nhóm, loại); (nhóm, loại) chưa khai báo thì không có
#: đích dùng (chỉ nhận "").
DICH_DUNG_THEO_NHOM_LOAI = {
    (nhom, loai): tuple(ds)
    for nhom, theo_loai in _BANG_PHAN_LOAI_AI["dich_dung"].items()
    for loai, ds in theo_loai.items()
}

#: Bí danh nhóm CŨ → nhóm AI hiện hành (NV224) — BẢNG KHAI BÁO. Bản ghi cũ
#: mang nhóm cũ vẫn đọc được: nhãn hiệu lực đổi êm sang nhóm hiện hành ở
#: lớp đọc (``ban_cong_khai``), dữ liệu gốc trong kho không bị viết lại/mất.
NHOM_CU_SANG_NHOM_AI = dict(_BANG_PHAN_LOAI_AI["nhom_cu_sang_nhom_ai"])

#: Thứ tự ưu tiên ĐÍCH DÙNG khi chọn «đang dùng #1» (NV224) — BẢNG KHAI BÁO:
#: trong một nhóm, tài khoản đích đứng trước trong bảng được ưu tiên.
#: Đích không có trong bảng (kể cả "") xếp sau cùng, giữ thứ tự liệt kê.
THU_TU_UU_TIEN_DICH_DUNG = tuple(_BANG_PHAN_LOAI_AI["thu_tu_uu_tien_dich_dung"])


def nhom_hieu_luc(nhom: str) -> str:
    """Nhóm hiệu lực của một bản ghi: bí danh cũ → nhóm hiện hành (NV224)."""
    return NHOM_CU_SANG_NHOM_AI.get(nhom, nhom)


def _thu_tu_dich_dung(dich_dung) -> int:
    """Vị trí ưu tiên của một đích dùng trong bảng khai báo (NV224)."""
    try:
        return THU_TU_UU_TIEN_DICH_DUNG.index(dich_dung)
    except ValueError:
        return len(THU_TU_UU_TIEN_DICH_DUNG)
#: Tên HIỂN THỊ của nhóm/loại/đích dùng (NV225) — dữ liệu trình bày đi kèm
#: bảng phân loại, để giao diện lõi chỉ việc dùng, không chép tên cụ thể
#: vào mã trang (luật độc lập mã lõi).
TEN_NHOM_AI = dict(_BANG_PHAN_LOAI_AI["ten_nhom"])
TEN_LOAI_TAI_KHOAN = dict(_BANG_PHAN_LOAI_AI["ten_loai"])
TEN_DICH_DUNG = dict(_BANG_PHAN_LOAI_AI["ten_dich_dung"])

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


def kiem_loai_tai_khoan(loai, nhom) -> str:
    """Kiểm loại tài khoản (NV218): "" = chưa phân loại.

    None → "". Loại phải thuộc bảng ``LOAI_TAI_KHOAN_THEO_NHOM[nhom]`` —
    tra BẢNG DỮ LIỆU, không rải if theo tên nhà cung cấp. Nhóm ngoài 5 nhóm
    AI (hoặc chưa có nhóm) thì chưa phân loại được → chỉ nhận "".
    """
    if loai is None:
        return ""
    if isinstance(loai, bool) or not isinstance(loai, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(loai, "loai_tai_khoan")
    l = loai.strip()
    if len(l) > DOI_DAI_LOAI_TK:
        raise LoiKhoTaiKhoan("invalid_value")
    if not l:
        return ""
    cho_phep = LOAI_TAI_KHOAN_THEO_NHOM.get(nhom, ())
    if l not in cho_phep:
        raise LoiKhoTaiKhoan("invalid_value")
    return l


def kiem_dich_dung(dich_dung, nhom, loai) -> str:
    """Kiểm đích dùng (NV218): "" = chưa khai báo.

    None → "". Đích dùng phải thuộc bảng
    ``DICH_DUNG_THEO_NHOM_LOAI[(nhom, loai)]`` — tra BẢNG DỮ LIỆU.
    (nhóm, loại) chưa khai báo thì chỉ nhận "".
    """
    if dich_dung is None:
        return ""
    if isinstance(dich_dung, bool) or not isinstance(dich_dung, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(dich_dung, "dich_dung")
    d = dich_dung.strip()
    if len(d) > DOI_DAI_DICH_DUNG:
        raise LoiKhoTaiKhoan("invalid_value")
    if not d:
        return ""
    cho_phep = DICH_DUNG_THEO_NHOM_LOAI.get((nhom, loai), ())
    if d not in cho_phep:
        raise LoiKhoTaiKhoan("invalid_value")
    return d
#: NV223: cách đăng nhập của tài khoản dịch vụ — «truc_tiep» = đăng nhập
#: thẳng bằng bí mật của chính tài khoản; giá trị «qua tài khoản lưu» (xem
#: ``DANG_NHAP_BANG_QUA_TAI_KHOAN_LUU``) = đăng nhập QUA một tài khoản đã
#: lưu khác (``tai_khoan_google_id``). "" = chưa khai (giữ hành vi cũ theo
#: module sở hữu). Danh sách ĐÓNG — không dò chuỗi email.
_DANG_NHAP_BANG = _BANG_PHAN_LOAI_AI["dang_nhap_bang"]
DANG_NHAP_BANG_CHO_PHEP = tuple(_DANG_NHAP_BANG["cho_phep"])

#: Giá trị «đăng nhập bằng» nghĩa là qua một tài khoản đã lưu khác — mã so
#: sánh bằng hằng này, không chép cứng giá trị cụ thể (cổng kiến trúc).
DANG_NHAP_BANG_QUA_TAI_KHOAN_LUU = _DANG_NHAP_BANG["qua_tai_khoan_luu"]

#: Nhóm của tài khoản được dùng để đăng nhập hộ.
DANG_NHAP_BANG_NHOM_TAI_KHOAN_LUU = _DANG_NHAP_BANG["nhom_tai_khoan_luu"]

#: Tuỳ chọn «đăng nhập bằng» cho giao diện — danh sách (id, nhãn), nguồn
#: duy nhất cho API ``/api/tai-khoan/bang-phan-loai`` và trang quản trị.
DANG_NHAP_BANG_TUY_CHON = tuple(
    (tuy_chon["id"], tuy_chon["nhan"]) for tuy_chon in _DANG_NHAP_BANG["tuy_chon"]
)


def kiem_dang_nhap_bang(dang_nhap_bang) -> str:
    """Kiểm trường «đăng nhập bằng» (NV223): None → "" (chưa khai)."""
    if dang_nhap_bang is None:
        return ""
    if isinstance(dang_nhap_bang, bool) or not isinstance(dang_nhap_bang, str):
        raise LoiKhoTaiKhoan("invalid_value")
    s = dang_nhap_bang.strip().lower()
    if s not in DANG_NHAP_BANG_CHO_PHEP:
        raise LoiKhoTaiKhoan("invalid_value")
    return s


def kiem_tai_khoan_google_id(tai_khoan_google_id) -> str:
    """Kiểm id bản ghi tài khoản Google dùng để đăng nhập hộ (NV223).

    None → "". Chỉ là một id bản ghi trong kho — kho không tự kiểm sự tồn
    tại ở đây (tầng route kiểm khi đăng nhập: thiếu/tạm dừng → lỗi rõ).
    """
    if tai_khoan_google_id is None:
        return ""
    if isinstance(tai_khoan_google_id, bool) \
            or not isinstance(tai_khoan_google_id, str):
        raise LoiKhoTaiKhoan("invalid_value")
    _kiem_ky_tu(tai_khoan_google_id, "tai_khoan_google_id")
    s = tai_khoan_google_id.strip()
    if len(s) > DOI_DAI_MODULE_ID:
        raise LoiKhoTaiKhoan("invalid_value")
    return s


def kiem_dung_cho(dung_cho, nhom, loai) -> list:
    """Kiểm DANH SÁCH đích dùng (NV224): một tài khoản dùng cho nhiều đích.

    Phải là list/tuple; mỗi mục kiểm như ``kiem_dich_dung`` của đúng
    (nhóm, loại); khử trùng giữ thứ tự; mục "" bị bỏ (chưa khai báo).
    """
    if isinstance(dung_cho, (str, bytes)) or not isinstance(dung_cho, (list, tuple)):
        raise LoiKhoTaiKhoan("invalid_value")
    kq: list[str] = []
    for muc in dung_cho:
        d = kiem_dich_dung(muc, nhom, loai)
        if d and d not in kq:
            kq.append(d)
    return kq


def _dung_cho_hieu_luc(ban_ghi: dict) -> list:
    """Danh sách đích dùng hiệu lực của bản ghi (NV224).

    Bản ghi cũ chưa có trường ``dung_cho``: suy từ ``dich_dung`` đơn đã lưu
    (không mất đích nào). ``dich_dung`` hiện tại luôn có mặt trong danh sách.
    """
    tho = ban_ghi.get("dung_cho")
    kq = [d for d in tho if isinstance(d, str) and d] if isinstance(tho, list) else []
    dd = ban_ghi.get("dich_dung") or ""
    if dd and dd not in kq:
        kq.append(dd)
    return kq


def _dich_duong_cua_ban_ghi(ban_ghi: dict) -> list:
    """Mọi đường dùng của bản ghi (NV232): đích đã khai báo cho (nhóm, loại)
    của bản ghi CỘNG các đích trong ``dung_cho`` hiệu lực."""
    nhom = nhom_hieu_luc(ban_ghi.get("nhom", "") or "")
    loai = ban_ghi.get("loai_tai_khoan", "") or ""
    kq = list(DICH_DUNG_THEO_NHOM_LOAI.get((nhom, loai), ()))
    for d in _dung_cho_hieu_luc(ban_ghi):
        if d not in kq:
            kq.append(d)
    return kq


def lay_trang_thai_duong(id_hoac_ban_ghi) -> dict:
    """Trạng thái TỪNG ĐƯỜNG của một tài khoản (NV232), đã CHUẨN HOÁ.

    Nhận id (chuỗi) hoặc chính bản ghi nội bộ. Trả
    ``{dich: {"trang_thai": <giá trị đóng>, "moc_hoi_lai": epoch}}`` cho
    mọi đường của tài khoản. Đường chưa từng ghi trạng thái: đường trùng
    ``dich_dung`` chính → ``san_sang`` (đã có thông tin đăng nhập của chính
    tài khoản), đường khác → ``chua_lien_ket``. ``het_han_muc`` đã qua
    ``moc_hoi_lai`` → đọc thành ``san_sang`` (tự hồi, không cần ghi lại).
    Id lạ/bản ghi nhập → {}.
    """
    if isinstance(id_hoac_ban_ghi, dict):
        ban_ghi = id_hoac_ban_ghi
    elif isinstance(id_hoac_ban_ghi, str) and not isinstance(id_hoac_ban_ghi, bool):
        ban_ghi = tim_theo_id(id_hoac_ban_ghi)
    else:
        return {}
    if not isinstance(ban_ghi, dict) or ban_ghi.get("loai") == "nhap":
        return {}
    luu = ban_ghi.get("trang_thai_duong")
    luu = luu if isinstance(luu, dict) else {}
    bay_gio = int(time.time())
    dich_chinh = ban_ghi.get("dich_dung") or ""
    kq: dict = {}
    for dich in _dich_duong_cua_ban_ghi(ban_ghi):
        muc = luu.get(dich)
        if isinstance(muc, dict) and muc.get("trang_thai") in TRANG_THAI_DUONG_HOP_LE:
            tt = muc["trang_thai"]
            moc = muc.get("moc_hoi_lai", 0)
            moc = moc if isinstance(moc, int) and not isinstance(moc, bool) else 0
            if tt == "het_han_muc" and (not moc or moc <= bay_gio):
                tt, moc = "san_sang", 0
            kq[dich] = {"trang_thai": tt, "moc_hoi_lai": moc if tt == "het_han_muc" else 0}
        else:
            kq[dich] = {
                "trang_thai": "san_sang" if dich == dich_chinh else "chua_lien_ket",
                "moc_hoi_lai": 0,
            }
    return kq


def dat_trang_thai_duong(id_, duong, trang_thai, moc=None):
    """Đặt trạng thái MỘT ĐƯỜNG của tài khoản (NV232).

    ``trang_thai`` phải thuộc ``TRANG_THAI_DUONG_HOP_LE``; ``duong`` phải là
    đường dùng của bản ghi (đã khai báo cho (nhóm, loại) hoặc trong
    ``dung_cho`` hiệu lực). ``het_han_muc`` bắt buộc ``moc`` là số nguyên
    dương (epoch giây — thời điểm đường tự hồi ``san_sang`` khi đọc);
    trạng thái khác luôn lưu ``moc_hoi_lai = 0`` (bỏ qua moc truyền vào).
    Id lạ → None (không tạo bản ghi mới). Bản ghi nhập → lỗi invalid_value
    (bản ghi nhập không có mô hình đường dùng). Trả dạng công khai đã cập nhật.
    """
    if not isinstance(id_, str) or not id_:
        return None
    du_lieu = _doc()
    ban_ghi = du_lieu.get(id_)
    if ban_ghi is None:
        return None
    if not isinstance(ban_ghi, dict) or ban_ghi.get("loai") == "nhap":
        raise LoiKhoTaiKhoan("invalid_value")
    if not isinstance(duong, str) or duong not in _dich_duong_cua_ban_ghi(ban_ghi):
        raise LoiKhoTaiKhoan("invalid_value")
    if not isinstance(trang_thai, str) or trang_thai not in TRANG_THAI_DUONG_HOP_LE:
        raise LoiKhoTaiKhoan("invalid_value")
    if trang_thai == "het_han_muc":
        if isinstance(moc, bool) or not isinstance(moc, int) or moc <= 0:
            raise LoiKhoTaiKhoan("invalid_value")
        moc_luu = moc
    else:
        moc_luu = 0
    tt = ban_ghi.get("trang_thai_duong")
    tt = dict(tt) if isinstance(tt, dict) else {}
    tt[duong] = {"trang_thai": trang_thai, "moc_hoi_lai": moc_luu}
    ban_ghi["trang_thai_duong"] = tt
    _ghi(du_lieu)
    return ban_cong_khai(ban_ghi)


def them_dung_cho(id_, dich):
    """Thêm đích dùng vào ``dung_cho`` (NV232) + tạo mục trạng thái đường.

    ``dich`` phải thuộc bảng khai báo của (nhóm, loại) hiệu lực của bản ghi,
    nếu không → ``LoiKhoTaiKhoan("invalid_value")``. Thêm trùng → giữ nguyên
    (không nhân đôi). Đường mới chưa có mục trạng thái → ``chua_lien_ket``
    (trừ khi đã có mục hợp lệ — giữ nguyên). Id lạ → None. Bản ghi nhập →
    lỗi invalid_value. Trả dạng công khai đã cập nhật.
    """
    if not isinstance(id_, str) or not id_:
        return None
    du_lieu = _doc()
    ban_ghi = du_lieu.get(id_)
    if ban_ghi is None:
        return None
    if not isinstance(ban_ghi, dict) or ban_ghi.get("loai") == "nhap":
        raise LoiKhoTaiKhoan("invalid_value")
    nhom = nhom_hieu_luc(ban_ghi.get("nhom", "") or "")
    loai = ban_ghi.get("loai_tai_khoan", "") or ""
    if not isinstance(dich, str) \
            or dich not in DICH_DUNG_THEO_NHOM_LOAI.get((nhom, loai), ()):
        raise LoiKhoTaiKhoan("invalid_value")
    dung_cho = _dung_cho_hieu_luc(ban_ghi)
    if dich not in dung_cho:
        dung_cho.append(dich)
    ban_ghi["dung_cho"] = dung_cho
    tt = ban_ghi.get("trang_thai_duong")
    tt = dict(tt) if isinstance(tt, dict) else {}
    muc = tt.get(dich)
    if not (isinstance(muc, dict)
            and muc.get("trang_thai") in TRANG_THAI_DUONG_HOP_LE):
        tt[dich] = {"trang_thai": "chua_lien_ket", "moc_hoi_lai": 0}
    ban_ghi["trang_thai_duong"] = tt
    _ghi(du_lieu)
    return ban_cong_khai(ban_ghi)


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
        # Trường NV218 (tuỳ chọn, bản ghi cũ không có): có thì phải là chuỗi.
        for truong in ("loai_tai_khoan", "dich_dung"):
            if truong in ban_ghi and not isinstance(ban_ghi[truong], str):
                raise LoiKhoTaiKhoan("kho_hong")
        # Trường NV224 (tuỳ chọn, bản ghi cũ không có): danh sách chuỗi.
        if "dung_cho" in ban_ghi:
            dc = ban_ghi["dung_cho"]
            if not isinstance(dc, list) or any(
                    not isinstance(x, str) for x in dc):
                raise LoiKhoTaiKhoan("kho_hong")
        # Trường NV232 (tuỳ chọn, bản ghi cũ không có): bản đồ đích dùng →
        # {"trang_thai": <giá trị đóng>, "moc_hoi_lai": epoch >= 0}.
        if "trang_thai_duong" in ban_ghi:
            tt_duong = ban_ghi["trang_thai_duong"]
            if not isinstance(tt_duong, dict):
                raise LoiKhoTaiKhoan("kho_hong")
            for dich, muc in tt_duong.items():
                if not isinstance(dich, str) or not isinstance(muc, dict):
                    raise LoiKhoTaiKhoan("kho_hong")
                if muc.get("trang_thai") not in TRANG_THAI_DUONG_HOP_LE:
                    raise LoiKhoTaiKhoan("kho_hong")
                moc = muc.get("moc_hoi_lai", 0)
                if isinstance(moc, bool) or not isinstance(moc, int) or moc < 0:
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
            # NV218: bản ghi nhập chưa phân loại AI.
            "loai_tai_khoan": "",
            "dich_dung": "",
            "dung_cho": [],
            # NV232: bản ghi nhập không có mô hình đường dùng.
            "trang_thai_duong": {},
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
        # NV224: nhãn nhóm hiệu lực — bí danh cũ («openai») đổi êm sang nhóm
        # hiện hành («chatgpt») ở lớp đọc, dữ liệu gốc không đổi.
        "nhom": nhom_hieu_luc(ban_ghi.get("nhom", "")),
        "bac_goi": ban_ghi.get("bac_goi") or BAC_GOI_KHONG_BIET,
        # NV218: loại + đích dùng theo bảng khai báo ("" = chưa phân loại).
        "loai_tai_khoan": ban_ghi.get("loai_tai_khoan", ""),
        "dich_dung": ban_ghi.get("dich_dung", ""),
        # NV223: cách đăng nhập + id tài khoản Google đăng nhập hộ (không bí mật)
        "dang_nhap_bang": ban_ghi.get("dang_nhap_bang", ""),
        "tai_khoan_google_id": ban_ghi.get("tai_khoan_google_id", ""),
        # NV224: MỌI đích dùng của bản ghi (tích lũy, không đè).
        "dung_cho": _dung_cho_hieu_luc(ban_ghi),
        # NV232: trạng thái TỪNG ĐƯỜNG (đã chuẩn hoá: het_han_muc quá mốc
        # đọc thành san_sang) — giao diện hiện theo từng đường.
        "trang_thai_duong": lay_trang_thai_duong(ban_ghi),
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
                  bac_goi=None, loai_tai_khoan=None, dich_dung=None, dang_nhap_bang=None,
                  tai_khoan_google_id=None, dung_cho=None) -> dict:
    """Lưu mới hoặc cập nhật theo email hoặc id (trùng email → cập nhật, không trùng lặp).

    `trang_thai`: None → giữ nguyên cũ (mặc định bật); "" hoặc "tam_dung"
    → đặt mới (kiểm qua ``kiem_trang_thai``, giá trị khác → invalid_value).
    `module_id`/`nhom`/`bac_goi` (NV197c): None → giữ nguyên cũ; "" hoặc chuỗi
    hợp lệ → đặt mới. `nhom` do tầng route suy từ manifest module trước khi
    truyền vào — kho không tự suy.
    `loai_tai_khoan`/`dich_dung` (NV218): None → giữ nguyên cũ; "" hoặc giá trị
    trong bảng khai báo của đúng (nhóm, loại) → đặt mới.
    `dang_nhap_bang`/`tai_khoan_google_id` (NV223): None → giữ nguyên cũ;
    kiểm chéo SAU khi hợp nhất: ``dang_nhap_bang == "google"`` mà thiếu
    ``tai_khoan_google_id`` → ``invalid_value`` (không lưu dở).
    `dung_cho` (NV224): danh sách MỌI đích dùng của bản ghi — None → giữ
    nguyên; truyền vào thì CỘNG THÊM (khử trùng, không đè mất đích cũ);
    `dich_dung` đặt mới cũng được cộng vào danh sách.
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

        # NV218: None → giữ nguyên cũ; "" / giá trị trong bảng → đặt mới.
        # Kiểm chéo với NHÓM MỚI (nhom_moi đã chốt ở trên). Đổi nhóm làm loại/
        # đích dùng cũ mất hiệu lực → xoá phân loại (không đoán loại mới).
        loai_moi = cu.get("loai_tai_khoan", "") if cu else ""
        if loai_tai_khoan is not None:
            loai_moi = kiem_loai_tai_khoan(loai_tai_khoan, nhom_moi)
        elif loai_moi and loai_moi not in LOAI_TAI_KHOAN_THEO_NHOM.get(nhom_moi, ()):
            loai_moi = ""
        dich_dung_moi = cu.get("dich_dung", "") if cu else ""
        if dich_dung is not None:
            dich_dung_moi = kiem_dich_dung(dich_dung, nhom_moi, loai_moi)
        elif dich_dung_moi and dich_dung_moi not in DICH_DUNG_THEO_NHOM_LOAI.get(
                (nhom_moi, loai_moi), ()):
            dich_dung_moi = ""
        # NV223: None → giữ nguyên cũ; kiểm chéo sau hợp nhất.
        dnb_moi = cu.get("dang_nhap_bang", "") if cu else ""
        if dang_nhap_bang is not None:
            dnb_moi = kiem_dang_nhap_bang(dang_nhap_bang)
        tk_gg_moi = cu.get("tai_khoan_google_id", "") if cu else ""
        if tai_khoan_google_id is not None:
            tk_gg_moi = kiem_tai_khoan_google_id(tai_khoan_google_id)
        if dnb_moi == DANG_NHAP_BANG_QUA_TAI_KHOAN_LUU and not tk_gg_moi:
            raise LoiKhoTaiKhoan("invalid_value")

        # NV224: dung_cho TÍCH LŨY — bắt đầu từ danh sách hiệu lực cũ, cộng
        # thêm các đích truyền vào (đã kiểm theo bảng của (nhóm, loại) MỚI),
        # cộng cả dich_dung vừa đặt; cuối cùng lọc theo bảng của (nhóm,
        # loại) mới như NV218 (đổi nhóm/loại làm đích cũ mất hiệu lực).
        dung_cho_moi = _dung_cho_hieu_luc(cu) if cu else []
        if dung_cho is not None:
            for d in kiem_dung_cho(dung_cho, nhom_moi, loai_moi):
                if d not in dung_cho_moi:
                    dung_cho_moi.append(d)
        if dich_dung_moi and dich_dung_moi not in dung_cho_moi:
            dung_cho_moi.append(dich_dung_moi)
        _cho_phep_dc = set(DICH_DUNG_THEO_NHOM_LOAI.get((nhom_moi, loai_moi), ()))
        dung_cho_moi = [d for d in dung_cho_moi if d in _cho_phep_dc]

        # NV232: trạng thái từng đường — giữ nguyên mục đã lưu còn hợp lệ;
        # đường thuộc (nhóm, loại) MỚI hoặc dung_cho MỚI mà chưa có mục thì
        # mặc định: đường trùng đích chính → san_sang, còn lại chua_lien_ket.
        # Đường không còn thuộc bản ghi (đổi nhóm/loại) bị loại khỏi bản đồ.
        tt_duong_moi: dict = {}
        if cu and isinstance(cu.get("trang_thai_duong"), dict):
            for d, muc in cu["trang_thai_duong"].items():
                if (isinstance(d, str) and isinstance(muc, dict)
                        and muc.get("trang_thai") in TRANG_THAI_DUONG_HOP_LE):
                    moc_cu = muc.get("moc_hoi_lai", 0)
                    tt_duong_moi[d] = {
                        "trang_thai": muc["trang_thai"],
                        "moc_hoi_lai": moc_cu if isinstance(moc_cu, int)
                        and not isinstance(moc_cu, bool) and moc_cu >= 0 else 0,
                    }
        _dich_ban_ghi_moi = list(
            DICH_DUNG_THEO_NHOM_LOAI.get((nhom_moi, loai_moi), ()))
        for d in dung_cho_moi:
            if d not in _dich_ban_ghi_moi:
                _dich_ban_ghi_moi.append(d)
        for d in _dich_ban_ghi_moi:
            if d not in tt_duong_moi:
                tt_duong_moi[d] = {
                    "trang_thai": "san_sang" if d == dich_dung_moi
                    else "chua_lien_ket",
                    "moc_hoi_lai": 0,
                }
        tt_duong_moi = {d: m for d, m in tt_duong_moi.items()
                        if d in _dich_ban_ghi_moi}
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
            "loai_tai_khoan": loai_moi,
            "dich_dung": dich_dung_moi,
            "dang_nhap_bang": dnb_moi,
            "tai_khoan_google_id": tk_gg_moi,
            "dung_cho": dung_cho_moi,
            "trang_thai_duong": tt_duong_moi,
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


def gom_theo_nhom_loai() -> dict:
    """Gom tài khoản theo nhóm → loại (NV218, thiết kế chủ máy 21/10).

    Trả ``{nhom: {loai: {"tai_khoan": [...], "dang_dung": <id>|None}}}``.
    ``dang_dung`` = id tài khoản đang được chọn đầu trong (nhóm, loại):
    bỏ qua ``tam_dung`` và tài khoản đang hết hạn mức theo
    ``luong_dung_tai_khoan``; trong số còn lại ƯU TIÊN theo đích dùng —
    bảng ``THU_TU_UU_TIEN_DICH_DUNG`` (NV224: Codex trước web), hoà thì
    giữ thứ tự liệt kê. None khi không có tài khoản dùng được. Mỗi tài
    khoản mang thêm cờ ``dang_dung``. Nhóm là nhãn HIỆU LỰC (bản ghi cũ
    nhóm «openai» gom về «chatgpt» — NV224).
    """
    try:
        from app import luong_dung_tai_khoan as ldtk
    except Exception:
        ldtk = None
    gom: dict[str, dict[str, dict]] = {}
    for b in liet_ke():
        nhom = b.get("nhom") or ""
        loai = b.get("loai_tai_khoan") or ""
        muc = gom.setdefault(nhom, {}).setdefault(
            loai, {"tai_khoan": [], "dang_dung": None})
        muc["tai_khoan"].append(b)
    for g_nhom in gom.values():
        for muc in g_nhom.values():
            chon = None
            # NV224: xét theo thứ tự ưu tiên đích dùng trước (bảng khai
            # báo), ổn định theo thứ tự liệt kê khi hoà.
            thu_tu_xet = sorted(
                enumerate(muc["tai_khoan"]),
                key=lambda p: (_thu_tu_dich_dung(p[1].get("dich_dung")), p[0]))
            for _i, b in thu_tu_xet:
                if b.get("trang_thai") == TRANG_THAI_TAM_DUNG:
                    continue
                if ldtk is not None:
                    try:
                        if ldtk.con_het_han_muc(b["id"]):
                            continue
                    except Exception:
                        pass
                chon = b["id"]
                break
            muc["dang_dung"] = chon
            for b in muc["tai_khoan"]:
                b["dang_dung"] = (chon is not None and b["id"] == chon)
    return gom


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
