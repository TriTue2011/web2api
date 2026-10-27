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
import logging
import time

log = logging.getLogger("web2api.duong_dung")

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


# ── Nối đường chat theo tài khoản (NV271) ───────────────────────────────────

def loi_tu_phan_hoi(ma: int, headers: dict | None = None) -> LoiDuong:
    """Tạo LoiDuong từ mã HTTP và headers phản hồi theo tín hiệu cấu trúc (NV271).

    - 429: đọc tiêu đề «retry-after» (chữ số nguyên giây >= 1) -> {"dat_lai_sau_giay": ...};
    - 403: đọc tiêu đề «x-web2api-loi-loai» == "chan_bot" -> {"loai": "chan_bot"}.
    Tiêu đề đọc không phân biệt hoa thường. Không đọc thân lỗi, không khớp chuỗi.
    """
    hd = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    tin_hieu: dict = {}
    if ma == 429:
        ra = hd.get("retry-after", "").strip()
        if ra.isdigit():
            val = int(ra)
            if val >= 0:
                tin_hieu["dat_lai_sau_giay"] = val
    elif ma == 403:
        loai = hd.get("x-web2api-loi-loai", "").strip().lower()
        if loai == "chan_bot":
            tin_hieu["loai"] = "chan_bot"
    return LoiDuong(ma, tin_hieu)


class KetQuaChatDuong:
    """Kết quả một lượt chạy chat theo tài khoản/đường."""

    def __init__(self, ma: int, headers: dict, noi_dung: object, id_module_phuc_vu: str, duong: str):
        self.ma = ma
        self.headers = headers
        self.noi_dung = noi_dung
        self.id_module_phuc_vu = id_module_phuc_vu
        self.duong = duong


async def chay_chat_theo_tai_khoan(so: object, request: object, id_module: str,
                                  than: dict, stream: bool) -> KetQuaChatDuong | None:
    """Chạy chat theo tài khoản đã lưu: ưu tiên đường theo THU_TU_DUONG_UU_TIEN.

    Áp dụng khi module khách gọi thuộc một nhóm có tài khoản trong kho,
    tài khoản có đường dùng được qua hook ghim, cùng model trần và đủ quyền.
    Không thoả mãn điều kiện áp dụng -> trả None để rơi về luồng cũ.
    """
    cat = getattr(so, "catalog", None) or {}
    mod_cat = cat.get(id_module)
    manifest_khach = getattr(mod_cat, "manifest", None) if mod_cat is not None else None
    dang_chay = getattr(so, "dang_chay", None) or {}
    the_hien_khach = dang_chay.get(id_module)
    if not isinstance(manifest_khach, dict):
        manifest_khach = getattr(the_hien_khach, "manifest", None) if the_hien_khach is not None else None
    if not isinstance(manifest_khach, dict):
        return None

    nhom_khach = manifest_khach.get("nhom")
    nhom_id = nhom_khach.get("id") if isinstance(nhom_khach, dict) else getattr(nhom_khach, "id", None)
    if not nhom_id or not isinstance(nhom_id, str):
        return None

    model_khach = str((than or {}).get("model") or "")
    model_tran = model_khach.split("/", 1)[1] if "/" in model_khach else model_khach

    from app.loi_openai import LoiOpenAI
    from app.xac_thuc import kiem_tra_pham_vi

    ban_do_duong: dict[str, tuple[str, object, str]] = {}
    for mid, mod in dang_chay.items():
        muc_cat = cat.get(mid)
        man = getattr(muc_cat, "manifest", None) if muc_cat is not None else None
        if not isinstance(man, dict):
            man = getattr(mod, "manifest", None)
        if not isinstance(man, dict):
            continue

        m_nhom = man.get("nhom")
        m_nhom_id = m_nhom.get("id") if isinstance(m_nhom, dict) else getattr(m_nhom, "id", None)
        if m_nhom_id != nhom_id:
            continue

        duong_ten = man.get("duong")
        if not isinstance(duong_ten, str) or not duong_ten.strip():
            continue
        duong_ten = duong_ten.strip()

        co_fn = getattr(mod, "co_tai_khoan_email", None)
        chat_fn = getattr(mod, "chat", None)
        if not callable(co_fn) or not callable(chat_fn):
            continue

        models_fn = getattr(mod, "models", None)
        if not callable(models_fn):
            continue

        tien_to = getattr(mod, "tien_to", None)
        if not isinstance(tien_to, str) or not tien_to:
            tt_cat = getattr(muc_cat, "tien_to", None) if muc_cat is not None else None
            tien_to = tt_cat if isinstance(tt_cat, str) and tt_cat else man.get("tien_to", mid)

        try:
            ds_models = await models_fn()
        except Exception as e:  # module hỏng khi liệt kê model: bỏ đường đó, các đường khác vẫn chạy
            log.warning("Bỏ qua đường %s của module %s: models() lỗi (%s)", duong_ten, mid, type(e).__name__)
            continue
        ids = [m["id"] for m in (ds_models or []) if isinstance(m, dict) and "id" in m]
        if f"{tien_to}/{model_tran}" not in ids:
            continue

        if mid != id_module:
            try:
                kiem_tra_pham_vi(request, "chat", module=mid)
            except LoiOpenAI:
                continue

        if duong_ten not in ban_do_duong:
            ban_do_duong[duong_ten] = (mid, mod, tien_to)

    if not ban_do_duong:
        return None

    from app import kho_tai_khoan
    from app.goi_kha_nang import goi

    try:
        ds_cong_khai = kho_tai_khoan.liet_ke()
    except kho_tai_khoan.LoiKhoTaiKhoan as e:
        log.warning("Không đọc được kho tài khoản, gọi module như cũ: %s", e)
        return None

    danh_sach_hop_le = []
    for b in ds_cong_khai:
        if b.get("loai") == "nhap":
            continue
        if b.get("trang_thai") == kho_tai_khoan.TRANG_THAI_TAM_DUNG:
            continue
        b_nhom = b.get("nhom") or ""
        if kho_tai_khoan.nhom_hieu_luc(b_nhom) != nhom_id:
            continue
        tk_nb = kho_tai_khoan.tim_theo_id(b.get("id"))
        if not isinstance(tk_nb, dict):
            continue
        email = tk_nb.get("email")
        if not email or not isinstance(email, str):
            continue
        tt_duong = kho_tai_khoan.lay_trang_thai_duong(b.get("id"))
        if not isinstance(tt_duong, dict):
            continue

        co_duong_dung = False
        for d, muc in tt_duong.items():
            if d in ban_do_duong:
                _mid, mod, _tt = ban_do_duong[d]
                if mod.co_tai_khoan_email(email):
                    tt = muc.get("trang_thai") if isinstance(muc, dict) else None
                    if BANG_TRANG_THAI_DUONG.get(tt, {}).get("dung_duoc"):
                        co_duong_dung = True
                        break
        if co_duong_dung:
            danh_sach_hop_le.append((tk_nb, tt_duong))

    if not danh_sach_hop_le:
        return None

    phan_hoi_cuoi: tuple[int, dict, object, str, str] | None = None
    danh_sach_chay = []

    for tk_nb, tt_duong in danh_sach_hop_le:
        tk_id = tk_nb["id"]
        email = tk_nb["email"]
        thuc_thi = {}

        for d in tt_duong:
            if d in ban_do_duong:
                mid_d, mod_d, tt_d = ban_do_duong[d]
                if mod_d.co_tai_khoan_email(email):
                    def _tao_thuc_thi(m=mod_d, mid_=mid_d, tt_=tt_d, d_=d, em=email):
                        async def _goi():
                            nonlocal phan_hoi_cuoi
                            than_moi = dict(than or {})
                            than_moi["model"] = f"{tt_}/{model_tran}"
                            ma, hd, nd = await goi(m, "chat", than_moi, stream=stream, tai_khoan_email=em)
                            if ma != 200:
                                phan_hoi_cuoi = (ma, hd, nd, mid_, d_)
                                loi = loi_tu_phan_hoi(ma, hd)
                                loi.phan_hoi_goc = (ma, hd, nd, mid_, d_)
                                raise loi
                            return (ma, hd, nd, mid_, d_)
                        return _goi
                    thuc_thi[d] = _tao_thuc_thi()

        def _tao_cap_nhat(id_=tk_id):
            def _cap_nhat(duong, trang_thai, moc):
                try:
                    kho_tai_khoan.dat_trang_thai_duong(id_, duong, trang_thai, moc)
                except Exception as e:
                    log.warning("Cập nhật trạng thái đường thất bại: %s", e)
            return _cap_nhat

        danh_sach_chay.append((tk_id, thuc_thi, tt_duong, _tao_cap_nhat()))

    try:
        _id_tk, kq_duong = await chay_qua_danh_sach(danh_sach_chay)
        ma, hd, nd, mid_phuc_vu, duong_dung = kq_duong.gia_tri
        return KetQuaChatDuong(ma, hd, nd, mid_phuc_vu, duong_dung)
    except LoiDuong as e:
        if hasattr(e, "phan_hoi_goc"):
            ma, hd, nd, mid_phuc_vu, duong_dung = e.phan_hoi_goc
            return KetQuaChatDuong(ma, hd, nd, mid_phuc_vu, duong_dung)
        raise
    except LoiHetDuong:
        if phan_hoi_cuoi is not None:
            ma, hd, nd, mid_phuc_vu, duong_dung = phan_hoi_cuoi
            return KetQuaChatDuong(ma, hd, nd, mid_phuc_vu, duong_dung)
        return None
