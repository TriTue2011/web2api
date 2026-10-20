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

DOI_DAI_EMAIL = 254
DOI_DAI_MAT_KHAU = 256
DOI_DAI_TOTP = 256
DOI_DAI_NHAN = 120

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
        if (
            not isinstance(id_, str)
            or not isinstance(ban_ghi, dict)
            or not isinstance(ban_ghi.get("email"), str)
            or not isinstance(ban_ghi.get("mat_khau"), str)
            or id_ != ma_tai_khoan(ban_ghi["email"])
        ):
            raise LoiKhoTaiKhoan("kho_hong")
    return du_lieu


def _ghi(du_lieu: dict) -> None:
    """Ghi toàn bộ kho (nguyên tử qua ``app.kho``)."""
    kho.ghi_bi_mat(TEN_KHO, du_lieu)


def ban_cong_khai(ban_ghi: dict) -> dict:
    """Dạng công khai: email CHE, cờ có/không bí mật — KHÔNG bí mật thô.

    Nhãn được escape HTML ở biên ra (chống XSS khi giao diện chèn vào DOM).
    """
    email = ban_ghi.get("email", "")
    return {
        "id": ban_ghi.get("id", ""),
        "email_che": che_email(email),
        "nhan": html.escape(ban_ghi.get("nhan", ""), quote=False),
        "co_mat_khau": bool(ban_ghi.get("mat_khau")),
        "co_totp": bool(ban_ghi.get("totp_secret")),
        "trang_thai": ban_ghi.get("trang_thai", ""),
        "cap_nhat": ban_ghi.get("cap_nhat", 0),
    }


def luu_tai_khoan(email, mat_khau, totp_secret=None, nhan="",
                  trang_thai="", id=None) -> dict:
    """Lưu mới hoặc cập nhật theo email hoặc id (trùng email → cập nhật, không trùng lặp).

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
            if email is None or (isinstance(email, str) and (email.startswith("…") or email == che_email(cu["email"]))):
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

        ban_ghi = {
            "id": id_,
            "email": email_chuan,
            "nhan": nhan_sach,
            "mat_khau": mk,
            "totp_secret": totp_cuoi,
            "trang_thai": cu.get("trang_thai", "") if cu else "",
            "cap_nhat": int(time.time()),
        }
        if isinstance(trang_thai, str) and trang_thai.strip():
            ban_ghi["trang_thai"] = kiem_nhan(trang_thai)
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
    id_ = ban_ghi["id"]
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
    _ghi_trang_thai({**_doc_trang_thai(), id_: {
        "hong_lien_tiep": 0 if thanh_cong else int(tt.get("hong_lien_tiep", 0)) + 1,
        "lan_cuoi": bay_gio,
    }})
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
