"""Đường dùng của một tài khoản AI (NV232).

Một tài khoản có thể có nhiều «đường dùng» (codex, web, …). Nguyên tắc chạy
một lượt: **Codex trước** — hết hạn mức hoặc lỗi thuộc diện chuyển đường thì
sang **WEB cùng tài khoản trong cùng lượt**; mỗi đường có trạng thái RIÊNG
(``trang_thai_duong`` trong kho). Khi mọi đường của tài khoản đều không chạy
được → sang tài khoản kế trong danh sách.

Phân loại lỗi theo NGUYÊN TẮC (mã HTTP + tín hiệu cấu trúc), CẤM danh sách
từ khoá theo nhà cung cấp:

- 429 → ``het_han_muc`` (+ mốc hồi: ``dat_lai_sau_giay`` / ``moc_hoi_lai``
  trong tín hiệu, mặc định 3600 giây);
- 401 → ``can_dang_nhap_lai``;
- 403 + ``{"loai": "chan_bot"}`` → ``chan_bot`` (lỗi môi trường của lượt:
  KHÔNG ghi trạng thái, đường không bị đánh hỏng);
- 403 khác → ``can_dang_nhap_lai``;
- 5xx → ``loi_tam_thoi`` (chuyển đường trong lượt, không ghi trạng thái);
- còn lại → ``loi_khac`` (không chuyển đường — ném lại cho tầng gọi).

GIỚI HẠN CỨNG (theo spec): chat web hiện không gọi được do chặn bot (403)
ở mọi tài khoản — thiết kế này vẫn đúng khi giới hạn được gỡ; ``LoiChanBot``
tiếp tục sang tài khoản/đường kế, không bao giờ dừng lượt.
"""

from __future__ import annotations

import inspect
import time

# ── Bảng trạng thái đường (khớp TRANG_THAI_DUONG_HOP_LE của kho) ────────────
# ten: nhãn hiển thị (giao diện đổ từ /bang-phan-loai, không chép tay).
# can_nguoi: trạng thái này có cần NGƯỜI can thiệp mới gỡ được không.
# dung_duoc: đường ở trạng thái này có được gọi trong lượt mới không.
BANG_TRANG_THAI_DUONG = {
    "san_sang": {
        "ten": "Sẵn sàng",
        "can_nguoi": False,
        "dung_duoc": True,
    },
    "het_han_muc": {
        "ten": "Hết hạn mức",
        "can_nguoi": False,
        "dung_duoc": False,
    },
    "can_xac_minh_dien_thoai": {
        "ten": "Cần xác minh điện thoại",
        "can_nguoi": True,
        "dung_duoc": False,
    },
    "can_dang_nhap_lai": {
        "ten": "Cần đăng nhập lại",
        "can_nguoi": True,
        "dung_duoc": False,
    },
    "chua_lien_ket": {
        "ten": "Chưa liên kết",
        "can_nguoi": True,
        "dung_duoc": False,
    },
}

#: Thứ tự ưu tiên đường trong một lượt: Codex TRƯỚC, rồi web, rồi các đường
#: khác theo thứ tự khai báo của tài khoản.
THU_TU_DUONG_UU_TIEN = ("codex", "web")

#: Mốc hồi mặc định khi 429 không kèm tín hiệu thời gian (giây).
MAC_DINH_MOC_HOI_LAI_GIAY = 3600


class LoiDuong(Exception):
    """Lỗi của một đường, mang MÃ HTTP + TÍN HIỆU CẤU TRÚC để phân loại.

    ``tin_hieu`` là dict tuỳ chọn, ví dụ ``{"dat_lai_sau_giay": 600}`` cho
    429, ``{"loai": "chan_bot"}`` cho 403 chặn bot.
    """

    def __init__(self, ma_http, tin_hieu=None):
        super().__init__(f"duong loi: http {ma_http}")
        self.ma_http = ma_http
        self.tin_hieu = tin_hieu if isinstance(tin_hieu, dict) else {}


class LoiHetDuong(Exception):
    """Mọi đường của tài khoản (hoặc mọi tài khoản trong danh sách) đều
    không chạy được trong lượt này."""


class KetQuaDuong:
    """Kết quả một lượt chạy thành công: đường đã dùng + giá trị trả về."""

    def __init__(self, duong, gia_tri):
        self.duong = duong
        self.gia_tri = gia_tri

    def __repr__(self):
        return f"KetQuaDuong(duong={self.duong!r}, gia_tri={self.gia_tri!r})"


def phan_loai_loi(exc):
    """Phân loại lỗi theo nguyên tắc (mã HTTP + tín hiệu cấu trúc).

    Trả ``(loai, moc)`` trong đó loai ∈ {het_han_muc, can_dang_nhap_lai,
    chan_bot, loi_tam_thoi, loi_khac}; ``moc`` là epoch hồi lại cho
    ``het_han_muc`` (0 cho các loại khác). Ngoại lệ không phải ``LoiDuong``
    → ``loi_khac`` (tầng chạy ném lại, không chuyển đường).
    """
    if not isinstance(exc, LoiDuong):
        return ("loi_khac", 0)
    ma = exc.ma_http
    tin_hieu = exc.tin_hieu
    if ma == 429:
        moc = None
        tuyet_doi = tin_hieu.get("moc_hoi_lai")
        if isinstance(tuyet_doi, int) and not isinstance(tuyet_doi, bool) \
                and tuyet_doi > 0:
            moc = tuyet_doi
        else:
            giay = tin_hieu.get("dat_lai_sau_giay")
            if isinstance(giay, int) and not isinstance(giay, bool) \
                    and giay >= 0:
                moc = int(time.time()) + giay
        if moc is None:
            moc = int(time.time()) + MAC_DINH_MOC_HOI_LAI_GIAY
        return ("het_han_muc", moc)
    if ma == 401:
        return ("can_dang_nhap_lai", 0)
    if ma == 403:
        if tin_hieu.get("loai") == "chan_bot":
            return ("chan_bot", 0)
        return ("can_dang_nhap_lai", 0)
    if isinstance(ma, int) and ma >= 500:
        return ("loi_tam_thoi", 0)
    return ("loi_khac", 0)


def chon_duong(trang_thai_duong):
    """Đường sẽ chạy đầu trong lượt mới: codex trước, rồi web, rồi các
    đường khác theo thứ tự khai báo. Bỏ qua đường có trạng thái
    ``dung_duoc`` = False. Không còn đường dùng được → None."""
    if not isinstance(trang_thai_duong, dict):
        return None
    uu_tien = [d for d in THU_TU_DUONG_UU_TIEN if d in trang_thai_duong]
    uu_tien += [d for d in trang_thai_duong if d not in uu_tien]
    for duong in uu_tien:
        muc = trang_thai_duong[duong]
        tt = muc.get("trang_thai") if isinstance(muc, dict) else None
        if BANG_TRANG_THAI_DUONG.get(tt, {}).get("dung_duoc"):
            return duong
    return None


async def chay_qua_tai_khoan(thuc_thi, trang_thai_duong, cap_nhat=None):
    """Chạy một lượt qua các đường của CHÍNH tài khoản.

    ``thuc_thi``: bản đồ đường → hàm thực thi (đồng bộ hoặc coroutine).
    ``trang_thai_duong``: trạng thái từng đường đã chuẩn hoá
    (``lay_trang_thai_duong``). ``cap_nhat(duong, trang_thai, moc)``:
    callback ghi trạng thái đường (ví dụ vào kho); không bắt buộc.

    - Đường chưa sẵn sàng (không ``dung_duoc``): BỎ QUA HẲN, không gọi.
    - 429 → ghi ``het_han_muc`` (+ mốc), sang đường kế trong cùng lượt.
    - 401 / 403-thường → ghi ``can_dang_nhap_lai``, sang đường kế.
    - 403 + ``{"loai": "chan_bot"}`` → KHÔNG ghi trạng thái (lỗi môi
      trường của lượt), sang đường kế.
    - 5xx → ``loi_tam_thoi``: sang đường kế, không ghi trạng thái.
    - Lỗi khác → ném lại, không chuyển đường.
    - Hết đường → ``LoiHetDuong``.
    """
    if not isinstance(thuc_thi, dict) or not thuc_thi:
        raise LoiHetDuong("khong co duong thuc thi")
    uu_tien = [d for d in THU_TU_DUONG_UU_TIEN if d in thuc_thi]
    uu_tien += [d for d in thuc_thi if d not in uu_tien]
    loi_cuoi = None
    for duong in uu_tien:
        muc = trang_thai_duong.get(duong) \
            if isinstance(trang_thai_duong, dict) else None
        tt = muc.get("trang_thai") if isinstance(muc, dict) else None
        if not BANG_TRANG_THAI_DUONG.get(tt, {}).get("dung_duoc"):
            continue
        try:
            kq = thuc_thi[duong]()
            if inspect.isawaitable(kq):
                kq = await kq
            return KetQuaDuong(duong, kq)
        except LoiDuong as e:
            loai, moc = phan_loai_loi(e)
            if loai == "het_han_muc":
                if cap_nhat is not None:
                    cap_nhat(duong, "het_han_muc", moc)
                loi_cuoi = e
                continue
            if loai == "can_dang_nhap_lai":
                if cap_nhat is not None:
                    cap_nhat(duong, "can_dang_nhap_lai", 0)
                loi_cuoi = e
                continue
            if loai in ("chan_bot", "loi_tam_thoi"):
                # Lỗi môi trường của lượt: không đánh hỏng đường.
                loi_cuoi = e
                continue
            raise
    raise LoiHetDuong(f"het duong trong luot: {loi_cuoi!r}")


async def chay_qua_danh_sach(danh_sach):
    """Chạy qua danh sách tài khoản theo thứ tự.

    Mỗi mục là ``(id_tai_khoan, thuc_thi, trang_thai_duong[, cap_nhat])``.
    Tài khoản hết đường (``LoiHetDuong``) → sang tài khoản kế. Trả
    ``(id_tai_khoan, KetQuaDuong)`` của tài khoản chạy thành công đầu tiên.
    Hết danh sách → ``LoiHetDuong``.
    """
    for muc in danh_sach or []:
        id_, thuc_thi, tt_duong = muc[0], muc[1], muc[2]
        cap_nhat = muc[3] if len(muc) > 3 else None
        try:
            kq = await chay_qua_tai_khoan(thuc_thi, tt_duong,
                                          cap_nhat=cap_nhat)
        except LoiHetDuong:
            continue
        return id_, kq
    raise LoiHetDuong("het danh sach tai khoan")


# ── Phân loại thất bại liên kết Codex (tín hiệu cấu trúc) ───────────────────

#: Các mã lỗi CÓ CẤU TRÚC nghĩa là «cần xác minh điện thoại». So khớp chính
#: xác theo bảng — không dò chuỗi trong thông điệp lỗi.
MA_LOI_CAN_XAC_MINH_DIEN_THOAI = (
    "phone_verification_required",
    "can_xac_minh_dien_thoai",
    "xac_minh_dien_thoai",
    "phone_required",
)


def phan_loai_lien_ket_that_bai(ket_qua):
    """Phân loại kết quả OAuth liên kết thất bại bằng TÍN HIỆU CẤU TRÚC.

    ``ma_loi`` trong bảng ``MA_LOI_CAN_XAC_MINH_DIEN_THOAI`` hoặc cờ boolean
    ``can_xac_minh_dien_thoai`` → ``can_xac_minh_dien_thoai``. Còn lại →
    ``loi_khac``. Trả ``(loai, ma_loi, huong_dan)``.
    """
    kq = ket_qua if isinstance(ket_qua, dict) else {}
    ma_loi = kq.get("ma_loi")
    ma_chuan = ma_loi.strip().lower() if isinstance(ma_loi, str) else ""
    if ma_chuan in MA_LOI_CAN_XAC_MINH_DIEN_THOAI:
        return ("can_xac_minh_dien_thoai", ma_loi,
                "Mở ứng dụng của nhà cung cấp, xác minh số điện thoại của "
                "tài khoản, rồi bấm «Thử liên kết Codex lại».")
    if kq.get("can_xac_minh_dien_thoai") is True:
        return ("can_xac_minh_dien_thoai", "can_xac_minh_dien_thoai",
                "Mở ứng dụng của nhà cung cấp, xác minh số điện thoại của "
                "tài khoản, rồi bấm «Thử liên kết Codex lại».")
    return ("loi_khac",
            ma_loi if isinstance(ma_loi, str) and ma_loi else "khong_ro",
            "Liên kết Codex thất bại; kiểm tra lại rồi thử liên kết lại.")


def che_email_kieu_module(email):
    """Mặt nạ email theo ĐÚNG kiểu module dùng (``a***@mien``).

    Quy ước chung giữa các module trong lõi: tên[:1] + "***@" + miền
    (mọi module OAuth đều dùng). Dùng để đối chiếu
    ``email_che`` module trả về với email tài khoản đã lưu — khớp mới coi
    liên kết là của đúng tài khoản.
    """
    try:
        ten, mien = str(email).split("@", 1)
    except ValueError:
        return "…"
    if not ten or not mien:
        return "…"
    return ten[:1] + "***@" + mien


def han_muc_theo_duong(ldtk, tai_khoan_id, duong):
    """Hạn mức TRUNG THỰC theo đường (NV232).

    Chỉ gán số liệu khi có họ tiêu đề RIÊNG của đường: ``codex`` ← các khoá
    ``codex_*`` của ``luong_dung_tai_khoan.lay_han_muc`` (đọc thụ động từ
    tiêu đề x-codex-*). Đường khác / không có số liệu riêng → ``khong_biet``.
    KHÔNG gán số hạn mức cấp tài khoản cho một đường cụ thể.
    """
    if ldtk is None or duong != "codex":
        return "khong_biet"
    try:
        hm = ldtk.lay_han_muc(tai_khoan_id)
    except Exception:
        return "khong_biet"
    if not isinstance(hm, dict):
        return "khong_biet"
    rieng = {k: v for k, v in hm.items()
             if isinstance(k, str) and k.startswith("codex_")}
    return rieng if rieng else "khong_biet"


async def tim_module_oauth(so, nhom):
    """Tìm module OAuth của nhóm trong ``so.dang_chay`` (NV232).

    Module hợp lệ: ``manifest.nhom.id`` (hoặc ``manifest["nhom"]["id"]``)
    bằng ``nhom`` của tài khoản, và có thực thể loại ``dang_nhap`` phơi
    ĐỦ HAI hành động ``bat_dau_dang_nhap`` + ``dan_callback`` (tái dùng
    luồng đăng nhập thiết bị sẵn có của module — không viết luồng mới).
    Trả ``(module_id, entity_id)`` hoặc None.
    """
    dang_chay = getattr(so, "dang_chay", None) or {}
    for mid, mod in dang_chay.items():
        manifest = getattr(mod, "manifest", None)
        nhom_m = None
        if isinstance(manifest, dict):
            nhom_m = manifest.get("nhom")
        elif manifest is not None:
            nhom_m = getattr(manifest, "nhom", None)
        nhom_id = nhom_m.get("id") if isinstance(nhom_m, dict) \
            else getattr(nhom_m, "id", None)
        if nhom_id != nhom:
            continue
        thuc_the = getattr(mod, "thuc_the", None)
        if not callable(thuc_the):
            continue
        try:
            ds = thuc_the()
            if inspect.isawaitable(ds):
                ds = await ds
        except Exception:
            continue
        if not isinstance(ds, list):
            continue
        for thuc_the_muc in ds:
            if not isinstance(thuc_the_muc, dict):
                continue
            if thuc_the_muc.get("loai") != "dang_nhap":
                continue
            hanh_dong = set()
            for tinh_nang in thuc_the_muc.get("tinh_nang") or []:
                if isinstance(tinh_nang, dict) and tinh_nang.get("hanh_dong"):
                    hanh_dong.add(tinh_nang["hanh_dong"])
            if {"bat_dau_dang_nhap", "dan_callback"} <= hanh_dong:
                return mid, thuc_the_muc.get("id")
    return None
