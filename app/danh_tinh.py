"""Kho danh tính của lõi (NV67 nhóm 1, việc 1 — NV72).

Sổ danh bạ tập trung: mỗi email chỉ nhập MỘT lần thành một «danh tính»; các module
provider gắn tài khoản của mình vào danh tính đó thay vì mỗi module giữ một bản email
riêng. Lõi KHÔNG chứa tên nhà cung cấp nào: bản ghi chỉ có id, email, nhãn và danh
sách id module đã gắn.

Lưu ở `<WEB2API_DATA>/bi_mat/danh_tinh.json` qua `kho.doc_bi_mat`/`kho.ghi_bi_mat`:
- Mã hoá AES-256-GCM khi nghỉ (qua `app.ham_khoa`, khi đã cấu hình `WEB2API_KHOA_CHINH`),
  tệp quyền 0600, thư mục cha 0700.
- id ổn định sinh từ email đã chuẩn hoá: cùng email luôn cùng id, kể cả sau khi xoá.

ĐÓNG KHI LỖI: tệp tồn tại mà không đọc/phân tích được → `LoiKhoDanhTinh` (503), không
bao giờ coi là rỗng (sẽ lặng lẽ cho tạo trùng) và không bao giờ ghi đè.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

from app import kho
from app.kham_pha import ID_HOP_LE
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.danh_tinh")

#: Email: một @, không khoảng trắng; miền có ít nhất một dấu chấm. (Chỉ nhận dạng khuôn,
#: không thay thế việc xác minh sở hữu — đó là việc của luồng đăng nhập ở nhóm 3.)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{1,63}\Z")
EMAIL_TOI_DA = 254
NHAN_TOI_DA = 100

#: Trạng thái đăng nhập của danh tính gắn hồ sơ trình duyệt (NV92).
#: - "chua_dang_nhap": mới tạo, chưa thử đăng nhập;
#: - "dang_nhap": phiên đăng nhập còn sống trong hồ sơ trình duyệt;
#: - "can_nguoi": gặp CAPTCHA/2FA/xác minh — cần người mở trình duyệt xử lý tay;
#: - "het_han": phiên chết, chờ đăng nhập lại.
TRANG_THAI_DANG_NHAP = frozenset(
    {"chua_dang_nhap", "dang_nhap", "can_nguoi", "het_han"})

#: Tên hồ sơ trình duyệt: ID workspace của dịch vụ trình duyệt (16–64 ký tự hex),
#: hoặc "" khi chưa gắn hồ sơ nào.
MAU_HO_SO_TRINH_DUYET = re.compile(r"[a-f0-9]{16,64}\Z")

_KHOA_LUONG = threading.RLock()


class LoiKhoDanhTinh(LoiOpenAI):
    """Tệp kho danh tính tồn tại nhưng không đọc/phân tích được. 503, không lộ chi tiết tệp ra phản hồi."""

    def __init__(self) -> None:
        super().__init__(
            503,
            "Kho danh tính không đọc được. Khôi phục tệp bi_mat/danh_tinh.json từ bản sao lưu rồi thử lại.",
            loai="server_error",
            code="identity_store_unreadable",
        )


def chuan_hoa_email(email: Any) -> str:
    """Chuẩn hoá email: bỏ khoảng trắng hai đầu + casefold (hoa/thường coi như một).
    Sai khuôn → 400 `invalid_email`."""
    if not isinstance(email, str):
        raise LoiOpenAI(400, "Trường «email» phải là chuỗi.", code="invalid_value")
    e = email.strip().casefold()
    if not e or len(e) > EMAIL_TOI_DA or not EMAIL_RE.match(e):
        raise LoiOpenAI(400, "Địa chỉ email không hợp lệ.", code="invalid_email")
    return e


def _id_tu_email(email_chuan: str) -> str:
    """id ổn định sinh từ email đã chuẩn hoá (`dt-` + 16 ký tự hex của SHA-256)."""
    return "dt-" + hashlib.sha256(email_chuan.encode("utf-8")).hexdigest()[:16]


def kiem_nhan(nhan: Any) -> str:
    """Nhãn hiển thị do người dùng đặt. Trống/null → "". Chống ký tự điều khiển (phá bố cục log/giao diện)."""
    if nhan is None or (isinstance(nhan, str) and not nhan.strip()):
        return ""
    if not isinstance(nhan, str):
        raise LoiOpenAI(400, "Trường «nhan» phải là chuỗi.", code="invalid_value")
    n = nhan.strip()
    if len(n) > NHAN_TOI_DA:
        raise LoiOpenAI(400, f"Nhãn không quá {NHAN_TOI_DA} ký tự.", code="invalid_value")
    if any(ord(c) < 32 for c in n):
        raise LoiOpenAI(400, "Nhãn chứa ký tự điều khiển.", code="invalid_value")
    return n


def kiem_module_id(module_id: Any) -> str:
    """module_id phải là id module hợp lệ (đúng khuôn `ID_HOP_LE` của lõi)."""
    if not isinstance(module_id, str) or not ID_HOP_LE.match(module_id):
        raise LoiOpenAI(400, "«module_id» phải là id module hợp lệ.", code="invalid_value")
    return module_id


def ban_ghi_cong_khai(d: dict[str, Any]) -> dict[str, Any]:
    """Bản ghi trả ra API: chỉ bốn trường lõi hiểu + danh sách đã gắn. Không bí mật ở đây
    (cookie/phiên của từng tài khoản nằm trong kho riêng của module sở hữu — nhóm 4).

    NV92: thêm `trang_thai_dang_nhap` và `ho_so_trinh_duyet` (bản ghi cũ thiếu
    thì nhận giá trị mặc định, không vỡ).
    """
    trang_thai = d.get("trang_thai_dang_nhap")
    if trang_thai not in TRANG_THAI_DANG_NHAP:
        trang_thai = "chua_dang_nhap"
    ho_so = d.get("ho_so_trinh_duyet")
    if not isinstance(ho_so, str) or (ho_so and not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so)):
        ho_so = ""
    return {
        "id": d.get("id"),
        "email": d.get("email"),
        "nhan": d.get("nhan", ""),
        "ngay_tao": d.get("ngay_tao"),
        "gan": sorted(m for m in (d.get("gan") or []) if isinstance(m, str)),
        "trang_thai_dang_nhap": trang_thai,
        "ho_so_trinh_duyet": ho_so,
    }


def _doc() -> list[dict[str, Any]]:
    """Đọc kho. Chưa có tệp → []. Hỏng/sai cấu trúc → LoiKhoDanhTinh (không lọc lặng lẽ:
    lọc rồi lần ghi sau sẽ xoá mất bản ghi)."""
    try:
        du_lieu = kho.doc_bi_mat("danh_tinh")
    except kho.KhoHong as e:
        log.warning("Kho danh tính hỏng: %s", e)
        raise LoiKhoDanhTinh() from None
    # Chú ý: kho.doc_bi_mat trả {} khi chưa có tệp (không phải None) — {} ở đây nghĩa là kho rỗng.
    if not du_lieu:
        return []
    ds = du_lieu.get("danh_tinh")
    if not isinstance(ds, list) or not all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in ds):
        log.warning("Kho danh tính sai cấu trúc")
        raise LoiKhoDanhTinh()
    return [dict(d) for d in ds]


def _ghi(ds: list[dict[str, Any]]) -> None:
    kho.ghi_bi_mat("danh_tinh", {"danh_tinh": ds})


def _tim(ds: list[dict[str, Any]], id_: str) -> dict[str, Any]:
    for d in ds:
        if d.get("id") == id_:
            return d
    raise LoiOpenAI(404, "Không tìm thấy danh tính.", code="danh_tinh_khong_co")


def tao(email: Any, nhan: Any = None) -> dict[str, Any]:
    """Tạo danh tính. Email chuẩn hoá (hoa/thường như nhau); trùng → 400 `email_ton_tai`.
    Kiểm-rồi-ghi trong cùng vùng găng: hai yêu cầu đồng thời không cùng lọt."""
    email_chuan = chuan_hoa_email(email)
    nhan_chuan = kiem_nhan(nhan)
    with _KHOA_LUONG:
        ds = _doc()
        for d in ds:
            if d.get("email") == email_chuan:
                raise LoiOpenAI(400, "Email đã có trong sổ danh tính.", code="email_ton_tai")
        ban_ghi = {
            "id": _id_tu_email(email_chuan),
            "email": email_chuan,
            "nhan": nhan_chuan,
            "ngay_tao": kho.bay_gio_iso(),
            "gan": [],
            "trang_thai_dang_nhap": "chua_dang_nhap",
            "ho_so_trinh_duyet": "",
        }
        ds.append(ban_ghi)
        _ghi(ds)
        return ban_ghi_cong_khai(ban_ghi)


def danh_sach() -> list[dict[str, Any]]:
    with _KHOA_LUONG:
        return [ban_ghi_cong_khai(d) for d in _doc()]


def lay(id_: Any) -> dict[str, Any] | None:
    """Bản ghi công khai theo id; None nếu không thấy (route quyết định 404)."""
    if not isinstance(id_, str) or not id_:
        return None
    with _KHOA_LUONG:
        for d in _doc():
            if d.get("id") == id_:
                return ban_ghi_cong_khai(d)
    return None


def tim_theo_email(email: Any) -> dict[str, Any] | None:
    """Tìm danh tính theo email (chỉ đọc); không thấy hoặc email sai khuôn -> None."""
    try:
        email_chuan = chuan_hoa_email(email)
    except LoiOpenAI:
        return None
    # Khớp theo CHÍNH khoá `tao()` dùng để chống trùng (trường email đã chuẩn hoá), không theo id sinh lại từ email:
    # id chỉ là nhãn, bản ghi cũ hay nhập tay có thể mang id khác — tra theo id sẽ bỏ sót rồi `tao()` báo trùng.
    with _KHOA_LUONG:
        for d in _doc():
            if d.get("email") == email_chuan:
                return ban_ghi_cong_khai(d)
    return None


def gan(id_: str, module_id: str) -> dict[str, Any]:
    """Gắn danh tính vào một provider (module). Lũy đẳng: gắn lại module đã gắn thì không trùng.
    module_id đã kiểm khuôn trước khi gọi (route còn kiểm module đã cài)."""
    module_id = kiem_module_id(module_id)
    with _KHOA_LUONG:
        ds = _doc()
        tim = _tim(ds, id_)
        gan_ds = tim.get("gan")
        if not isinstance(gan_ds, list):
            gan_ds = tim["gan"] = []
        if module_id not in gan_ds:
            gan_ds.append(module_id)
        _ghi(ds)
        return ban_ghi_cong_khai(tim)


def go(id_: str, module_id: str) -> dict[str, Any]:
    """Gỡ danh tính khỏi một provider. Chưa gắn → 404 `khong_gan`."""
    module_id = kiem_module_id(module_id)
    with _KHOA_LUONG:
        ds = _doc()
        tim = _tim(ds, id_)
        gan_ds = [m for m in (tim.get("gan") or []) if isinstance(m, str)]
        if module_id not in gan_ds:
            raise LoiOpenAI(404, "Danh tính chưa gắn với module này.", code="khong_gan")
        tim["gan"] = [m for m in gan_ds if m != module_id]
        _ghi(ds)
        return ban_ghi_cong_khai(tim)


def tim_theo_ho_so(ho_so: str) -> dict[str, Any] | None:
    """Tìm danh tính theo tên hồ sơ trình duyệt đã gắn; không thấy → None."""
    if not isinstance(ho_so, str) or not ho_so:
        return None
    with _KHOA_LUONG:
        for d in _doc():
            if d.get("ho_so_trinh_duyet") == ho_so:
                return ban_ghi_cong_khai(d)
    return None


def cap_nhat_trang_thai(id_: str, trang_thai: str | None = None,
                        ho_so: str | None = None) -> dict[str, Any]:
    """Cập nhật trạng thái đăng nhập / tên hồ sơ trình duyệt của một danh tính (NV92).

    `trang_thai` phải thuộc TRANG_THAI_DANG_NHAP; `ho_so` phải đúng khuôn ID
    workspace hoặc "" (gỡ gắn). Không thấy id → 404. Trả bản ghi công khai mới.
    """
    with _KHOA_LUONG:
        ds = _doc()
        tim = _tim(ds, id_)
        if trang_thai is not None:
            if trang_thai not in TRANG_THAI_DANG_NHAP:
                raise LoiOpenAI(400, "«trang_thai» không hợp lệ.", code="invalid_value")
            tim["trang_thai_dang_nhap"] = trang_thai
        if ho_so is not None:
            if ho_so and not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
                raise LoiOpenAI(400, "«ho_so» không hợp lệ.", code="invalid_value")
            tim["ho_so_trinh_duyet"] = ho_so
        _ghi(ds)
        return ban_ghi_cong_khai(tim)


def nhan_ho_so(ho_so: str, email: Any, nhan: Any = None,
               trang_thai: str = "chua_dang_nhap") -> tuple[dict[str, Any], bool]:
    """«Nhận workspace làm tài khoản» (NV155c): gắn hồ sơ trình duyệt ĐÃ ĐÓNG vào
    danh tính của `email`, trong MỘT vùng găng — chống IDOR/cướp hồ sơ:

    - Hồ sơ đã gắn với danh tính KHÁC → 409 `ho_so_da_gan` (không cướp).
    - Email đã có danh tính gắn hồ sơ KHÁC → 409 `email_da_gan_ho_so_khac`.
    - Email có danh tính chưa gắn hồ sơ → gắn (kèm trạng thái mới kiểm) —
      da_co_san=True; đã gắn ĐÚNG hồ sơ này → luỹ đẳng, chỉ cập nhật trạng thái.
    - Email chưa có danh tính → tạo mới kèm hồ sơ + trạng thái — da_co_san=False.

    `ho_so` phải ĐÃ qua `MAU_HO_SO_TRINH_DUYET` ở biên (route kiểm trước khi gọi);
    `trang_thai` phải thuộc TRANG_THAI_DANG_NHAP. Trả (bản ghi công khai, da_co_san).
    Lỗi 409 không nêu email/hồ sơ của danh tính khác vào thông báo.
    """
    email_chuan = chuan_hoa_email(email)
    nhan_chuan = kiem_nhan(nhan)
    if trang_thai not in TRANG_THAI_DANG_NHAP:
        raise LoiOpenAI(400, "«trang_thai» không hợp lệ.", code="invalid_value")
    with _KHOA_LUONG:
        ds = _doc()
        for d in ds:
            if d.get("ho_so_trinh_duyet") == ho_so and d.get("email") != email_chuan:
                raise LoiOpenAI(
                    409, "Hồ sơ trình duyệt này đã gắn với một danh tính khác.",
                    loai="invalid_request_error", code="ho_so_da_gan")
        for d in ds:
            if d.get("email") == email_chuan:
                hs = d.get("ho_so_trinh_duyet") or ""
                if hs and hs != ho_so:
                    raise LoiOpenAI(
                        409, "Email này đã gắn với một hồ sơ trình duyệt khác.",
                        loai="invalid_request_error", code="email_da_gan_ho_so_khac")
                if not hs:
                    d["ho_so_trinh_duyet"] = ho_so
                d["trang_thai_dang_nhap"] = trang_thai
                _ghi(ds)
                return ban_ghi_cong_khai(d), True
        ban_ghi = {
            "id": _id_tu_email(email_chuan),
            "email": email_chuan,
            "nhan": nhan_chuan,
            "ngay_tao": kho.bay_gio_iso(),
            "gan": [],
            "trang_thai_dang_nhap": trang_thai,
            "ho_so_trinh_duyet": ho_so,
        }
        ds.append(ban_ghi)
        _ghi(ds)
        return ban_ghi_cong_khai(ban_ghi), False


def xoa(id_: str) -> bool:
    """Xoá danh tính. Còn tài khoản provider trỏ tới → 409 `con_tai_khoan` kèm danh sách
    module (tránh mồ côi). Không thấy → 404."""
    with _KHOA_LUONG:
        ds = _doc()
        tim = _tim(ds, id_)
        con = sorted(m for m in (tim.get("gan") or []) if isinstance(m, str))
        if con:
            raise LoiOpenAI(
                409,
                "Không thể xoá danh tính vì còn tài khoản provider đang trỏ tới: "
                + ", ".join(con)
                + ". Gỡ từng tài khoản trước.",
                loai="invalid_request_error",
                code="con_tai_khoan",
            )
        ds = [d for d in ds if d.get("id") != id_]
        _ghi(ds)
        return True


# ── Hợp đồng «danh tính dùng chung» cho module đăng nhập bằng email (NV91-P1)
#
# Mô hình (chốt 17/10 theo c2a + NV92): «danh tính dùng chung» = HỒ SƠ ĐĂNG NHẬP —
# một hồ sơ trình duyệt = một email đã đăng nhập sống. Mỗi provider gắn tài khoản
# ĐỘC LẬP của nó vào hồ sơ đó («tái dùng» ở tab Provider). Lõi chỉ giữ email +
# nhãn; cookie/phiên/mật khẩu nằm trong kho RIÊNG của từng module (quyết định
# 1.2 — xem docs/THAM-CHIEU.md), nên gỡ/hỏng module này không ảnh hưởng module kia.


def chua_gan(module_id: str) -> list[dict[str, Any]]:
    """Danh tính CHƯA gắn vào module này (để giao diện hiện «email có sẵn»).
    module_id sai khuôn → 400 (qua `kiem_module_id`)."""
    module_id = kiem_module_id(module_id)
    with _KHOA_LUONG:
        return [ban_ghi_cong_khai(d) for d in _doc()
                if module_id not in (d.get("gan") or [])]


def tao_va_gan(email: Any, nhan: Any, module_id: str) -> tuple[dict[str, Any], bool]:
    """Tạo-nếu-chưa-có + gắn vào module, trong một vùng găng (không tạo trùng
    khi hai yêu cầu đồng thời). Email đã có → chỉ gắn thêm, KHÔNG tạo trùng.
    Trả (bản ghi công khai, da_co_san)."""
    email_chuan = chuan_hoa_email(email)
    nhan_chuan = kiem_nhan(nhan)
    module_id = kiem_module_id(module_id)
    with _KHOA_LUONG:
        ds = _doc()
        for d in ds:
            if d.get("email") == email_chuan:
                gan_ds = d.get("gan")
                if not isinstance(gan_ds, list):
                    gan_ds = d["gan"] = []
                if module_id not in gan_ds:
                    gan_ds.append(module_id)
                    _ghi(ds)
                return ban_ghi_cong_khai(d), True
        ban_ghi = {
            "id": _id_tu_email(email_chuan),
            "email": email_chuan,
            "nhan": nhan_chuan,
            "ngay_tao": kho.bay_gio_iso(),
            "gan": [module_id],
        }
        ds.append(ban_ghi)
        _ghi(ds)
        return ban_ghi_cong_khai(ban_ghi), False


def go_theo_module(module_id: str) -> int:
    """Gỡ module khỏi MỌI danh tính (gọi khi gỡ module/tài khoản — không để
    «gắn» mồ côi chặn xoá danh tính sau này). Trả số danh tính đã gỡ."""
    module_id = kiem_module_id(module_id)
    with _KHOA_LUONG:
        ds = _doc()
        dem = 0
        for d in ds:
            gan_ds = [m for m in (d.get("gan") or []) if isinstance(m, str)]
            if module_id in gan_ds:
                d["gan"] = [m for m in gan_ds if m != module_id]
                dem += 1
        if dem:
            _ghi(ds)
        return dem
