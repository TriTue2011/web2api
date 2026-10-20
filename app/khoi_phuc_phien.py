"""Bộ máy khôi phục phiên/tài khoản nhiều tầng (NV88).

Khi một tài khoản hỏng, lõi thử theo thứ tự rẻ → đắt, mỗi tầng là một khả năng
tuỳ chọn do MODULE triển khai (lõi không chứa tên nhà cung cấp nào):

  (a) ``lam_moi_phien(khoa)`` — làm mới phiên/token bằng cookie đang có;
  (b) ``dang_nhap_tu_dong(tai_khoan, trinh_duyet)`` — đăng nhập lại tự động bằng
      mật khẩu đã lưu; HAI lần hỏng liên tiếp → ngừng tự đăng nhập tài khoản đó
      (tránh bị khoá), cần người bấm tiếp;
  (c) ``tai_khoan_ke(khoa)`` — chuyển sang tài khoản/khoá kế khoẻ;
  (d) ``can_nguoi`` — hết cách: thông báo tiếng Việt + mở noVNC đúng workspace.

Tín hiệu «cần người» (CAPTCHA / 2FA / xác minh thiết bị) do MODULE phát hiện và
báo qua ``can_nguoi=True`` — lõi không dò chữ, không cố vượt, không xây bộ né
phát hiện bot. Gặp ở tầng nào thì dừng tài khoản đó ngay ở tầng đó.

An toàn bí mật: mật khẩu chỉ tồn tại trong `BiMatTam`, bị xoá khỏi bộ nhớ ngay
sau khi tầng (b) dùng xong (kể cả khi lỗi); không log, không trả trong kết quả,
không vào nhật ký. Trạng thái khôi phục lưu ở
`<WEB2API_DATA>/bi_mat/khoi_phuc_phien.json` (0600).
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import kho
from app import danh_tinh as danh_tinh_mod
from app.tai_khoan_hang_loat import BiMatTam, phan_loai_loi

log = logging.getLogger("web2api.khoi_phuc_phien")

#: Các tầng khôi phục, rẻ → đắt.
TANG_A = "a"
TANG_B = "b"
TANG_C = "c"
TANG_D = "d"

#: Mã «cần người» — tín hiệu xuyên tầng: module gặp CAPTCHA/2FA/xác minh thiết bị
#: thì báo can_nguoi=True (kèm ma_loi này để batch runner cũng hiểu).
MA_CAN_NGUOI = "can_nguoi"

#: Chốt dừng: ngần này lần hỏng LIÊN TIẾP ở tầng (b) thì ngừng tự đăng nhập
#: tài khoản đó (tránh bị nhà cung cấp khoá tài khoản).
HONG_B_TOI_DA = 2

#: Mã lỗi an toàn: ngắn, chỉ chữ thường/số/gạch dưới (giống tai_khoan_hang_loat).
_MAU_MA_LOI = re.compile(r"[a-z0-9_]{1,64}")

#: Khuôn mã hồ sơ trình duyệt: THỐNG NHẤT ở app/danh_tinh.MAU_HO_SO_TRINH_DUYET
#: (hex 16–64, do novnc.sinh_ho_so_id sinh). Mọi nơi khác dùng chung, không
#: định nghĩa riêng (NV94-d: từng có 3 regex khác nhau ở 3 file).
MAU_HO_SO_TRINH_DUYET = danh_tinh_mod.MAU_HO_SO_TRINH_DUYET

#: noVNC cũ (người mở tay): workspace "<module>-<hex>" giữ nguyên khuôn
#: rộng để tương thích ngược (NV94-d: chỉ đường điều khiển thật mới
#: bắt buộc hex thuần của danh tính).
_MAU_ID_WORKSPACE_NOVNC = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

#: Khoá tài khoản trong API: mờ (opaque), không chứa bí mật, không dấu gạch chéo.
_MAU_KHOA = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

_TEN_KHO_TRANG_THAI = "khoi_phuc_phien"


def kiem_khoa(khoa: Any) -> str:
    """Khoá tài khoản phải là chuỗi mờ đúng khuôn; sai → ValueError (route đổi thành 400)."""
    if not isinstance(khoa, str) or not _MAU_KHOA.fullmatch(khoa):
        raise ValueError(f"khoá tài khoản không hợp lệ: {khoa!r}")
    return khoa


# ── Kết quả ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KetQuaKhoiPhuc:
    """Kết quả một bước/tầng khôi phục. KHÔNG bao giờ chứa bí mật thô.

    Module triển khai các khả năng (a)/(b) thì trả về kiểu này (import từ
    `app.khoi_phuc_phien`). `can_nguoi=True` nghĩa là gặp CAPTCHA/2FA/xác minh
    thiết bị — lõi dừng tài khoản đó ngay, không thử tầng đắt hơn.
    """

    thanh_cong: bool
    tang: str | None = None
    ma_loi: str = ""
    ma_trang_thai: int | None = None
    can_nguoi: bool = False
    huong_dan: str = ""
    khoa_moi: str | None = None
    url_novnc: str | None = None

    def sang_dict(self) -> dict:
        """dict JSON được — chỉ các trường an toàn, không bí mật."""
        return {
            "thanh_cong": bool(self.thanh_cong),
            "tang": self.tang,
            "ma_loi": self.ma_loi,
            "ma_trang_thai": self.ma_trang_thai,
            "can_nguoi": bool(self.can_nguoi),
            "huong_dan": self.huong_dan,
            "khoa_moi": self.khoa_moi,
            "url_novnc": self.url_novnc,
        }


@dataclass
class TaiKhoanCanKhoiPhuc:
    """Tài khoản cần khôi phục. Mật khẩu (nếu có) nằm trong BiMatTam tạm."""

    khoa: str
    email: str = ""
    mat_khau: BiMatTam | None = None

    def __repr__(self) -> str:  # pragma: no cover - hiển thị
        return f"TaiKhoanCanKhoiPhuc(khoa={self.khoa!r})"


def _ma_loi_an_toan(ma_loi: str, ma_trang_thai: int | None) -> str:
    """Mã lỗi của module phải đúng hình; không thì suy từ mã trạng thái (không dò chữ)."""
    if ma_loi and _MAU_MA_LOI.fullmatch(ma_loi):
        return ma_loi
    return phan_loai_loi(ma_trang_thai)


def _chuan_hoa_ket_qua(kq: Any, tang: str) -> KetQuaKhoiPhuc:
    """Ép kết quả thô của module thành KetQuaKhoiPhuc hợp lệ, gắn tầng."""
    if not isinstance(kq, KetQuaKhoiPhuc):
        log.warning("khoi-phuc: module trả kiểu lạ ở tầng %s (%s) — coi như hỏng tầng",
                    tang, type(kq).__name__)
        return KetQuaKhoiPhuc(False, tang=tang, ma_loi="ket_qua_sai_kieu")
    ma_loi = _ma_loi_an_toan(kq.ma_loi, kq.ma_trang_thai)
    can_nguoi = bool(kq.can_nguoi) or ma_loi == MA_CAN_NGUOI
    return KetQuaKhoiPhuc(
        thanh_cong=bool(kq.thanh_cong) and not can_nguoi,
        tang=tang,
        ma_loi=MA_CAN_NGUOI if can_nguoi else ma_loi,
        ma_trang_thai=kq.ma_trang_thai,
        can_nguoi=can_nguoi,
        huong_dan=str(kq.huong_dan or ""),
        khoa_moi=kq.khoa_moi,
        url_novnc=kq.url_novnc,
    )


async def _goi_an_toan(fn, tang: str, *args) -> KetQuaKhoiPhuc:
    """Gọi khả năng của module; ngoại lệ lạ → hỏng tầng đó, không vỡ lõi."""
    try:
        ket_qua = fn(*args)
        if asyncio.iscoroutine(ket_qua):
            ket_qua = await ket_qua
        return _chuan_hoa_ket_qua(ket_qua, tang)
    except Exception as e:
        log.warning("khoi-phuc: ngoại lệ ở tầng %s: %s", tang, type(e).__name__)
        return KetQuaKhoiPhuc(False, tang=tang, ma_loi="loi_module")


# ── Bộ điều khiển trình duyệt (trừu tượng) ───────────────────────────────────

class BoDieuKhienTrinhDuyet(abc.ABC):
    """Giao diện tối thiểu để module điền form đăng nhập.

    Cài đặt thật nối tới workspace trình duyệt từ xa qua CDP (chưa có trong lượt này —
    xem báo cáo NV88); test dùng `TrinhDuyetGia` dưới đây. Module chỉ dùng các
    phương thức này, không tự mở trình duyệt.
    """

    @abc.abstractmethod
    async def mo_trang(self, url: str) -> None: ...

    @abc.abstractmethod
    async def dien(self, bo_chon: str, gia_tri: str) -> None: ...

    @abc.abstractmethod
    async def bam(self, bo_chon: str) -> None: ...

    @abc.abstractmethod
    async def doc_cookie(self, mien: str) -> dict: ...

    async def doc_cookie_ho_so_da_dong(self, mien: str) -> dict | None:
        """Cookie của `mien` trong KHO của hồ sơ ĐÃ ĐÓNG (chưa mở trình duyệt).

        Khác `doc_cookie` (đọc từ phiên đang mở qua điều khiển trực tiếp):
        đường này đọc kho cookie lưu trên đĩa của hồ sơ — dùng để kiểm tín
        hiệu phiên của workspace ĐÃ ĐÓNG. Mặc định trả None = «không hỗ trợ /
        không kiểm được» (bộ điều khiển cũ, dịch vụ lỗi, hồ sơ đang mở, hồ sơ
        Chromium…); cài đặt thật trả dict (rỗng = đã đọc được kho, miền đó
        không có cookie nào). Giá trị cookie là bí mật — KHÔNG vào log.
        """
        return None

    @abc.abstractmethod
    async def tin_hieu(self, quy_tac: list | None = None) -> set:
        """Tín hiệu trang hiện tại: script cấu trúc chung (mã mở) ∪ quy tắc của module.

        `quy_tac`: danh sách quy tắc CẤU TRÚC do module khai (kiểu
        ``module.quy_tac_tin_hieu()``): mỗi quy tắc có ``loai`` thuộc
        {"cookie", "url_chua", "tieu_de_chua", "bo_chon"} và ``tin_hieu`` là
        tên tín hiệu mã mở ([a-z0-9_]). KHÔNG dò chữ trên trang. None/[] →
        chỉ tín hiệu chung.
        """

    async def vuot_thu_thach(self) -> None:
        """Cố vượt thử thách truy cập trên trang hiện tại (mặc định: không hỗ trợ).

        Cài đặt thật nối CDP sau này sẽ thử các bước hợp lệ với trang (không
        che dấu vết, không gọi dịch vụ giải hộ). Module KHÔNG được coi việc gọi
        hàm này là đã vượt — sau mỗi lượt vẫn phải đọc lại tin_hieu() để chắc.
        Mặc định không làm gì: tín hiệu thử thách còn nguyên, module sẽ bó tay
        theo đúng luồng «cần người».
        """

    async def dong(self) -> None:
        """Đóng phiên điều khiển (mặc định không làm gì)."""


class TrinhDuyetGia(BoDieuKhienTrinhDuyet):
    """Trình duyệt giả cho test: các trang được lập kịch bản trước.

    `trang`: {url: {"tin_hieu": [...], "cookie": {ten: gia_tri}, ...}}.
    `tin_hieu()` trả hợp các tín hiệu của trang hiện tại, ví dụ {"captcha"},
    {"2fa"}, {"thanh_cong"}, {"thu_thach_truy_cap"} (thử thách truy cập kiểu
    trang chặn truy cập — mã mở, lõi không biết tên nhà cung cấp nào). Nhật ký
    `hanh_dong` chỉ ghi TÊN hành động (không ghi giá trị điền vào — tránh lộ
    bí mật nếu ai đó log nó).
    """

    def __init__(self, trang: dict | None = None,
                 cookie_theo_mien: dict | None = None):
        self._trang = dict(trang or {})
        self._hien_tai: str | None = None
        self.hanh_dong: list[tuple] = []
        #: Kho cookie giả của hồ sơ ĐÃ ĐÓNG: {mien: {ten: gia_tri}} — test đặt
        #: riêng; None → ghép từ cookie mọi trang đã lập kịch bản.
        self._cookie_theo_mien = (
            None if cookie_theo_mien is None
            else {str(m): dict(c) for m, c in cookie_theo_mien.items()})

    async def mo_trang(self, url: str) -> None:
        self._hien_tai = url
        self.hanh_dong.append(("mo_trang", url))

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        self.hanh_dong.append(("dien", bo_chon))

    async def bam(self, bo_chon: str) -> None:
        self.hanh_dong.append(("bam", bo_chon))

    async def doc_cookie(self, mien: str) -> dict:
        cau_hinh = self._trang.get(self._hien_tai or "", {})
        return dict(cau_hinh.get("cookie") or {})

    async def doc_cookie_ho_so_da_dong(self, mien: str) -> dict | None:
        if self._cookie_theo_mien is not None:
            return dict(self._cookie_theo_mien.get(str(mien), {}))
        gop: dict = {}
        for cau_hinh in self._trang.values():
            if isinstance(cau_hinh, dict):
                ck = cau_hinh.get("cookie")
                if isinstance(ck, dict):
                    for ten, gia_tri in ck.items():
                        gop.setdefault(str(ten), gia_tri)
        return gop

    async def tin_hieu(self, quy_tac: list | None = None) -> set:
        cau_hinh = self._trang.get(self._hien_tai or "", {})
        ra = set(cau_hinh.get("tin_hieu") or [])
        url = self._hien_tai or ""
        cookie = dict(cau_hinh.get("cookie") or {})
        tieu_de = str(cau_hinh.get("tieu_de") or "")
        phan_tu = cau_hinh.get("phan_tu") or []
        for q in (quy_tac or []):
            if not isinstance(q, dict):
                continue
            ten_th = q.get("tin_hieu")
            if not isinstance(ten_th, str) or not ten_th:
                continue
            loai = q.get("loai")
            if loai == "cookie" and q.get("ten") in cookie:
                ra.add(ten_th)
            elif loai == "url_chua" and isinstance(q.get("gia_tri"), str) \
                    and q["gia_tri"] in url:
                ra.add(ten_th)
            elif loai == "tieu_de_chua" and isinstance(q.get("gia_tri"), str) \
                    and q["gia_tri"] in tieu_de:
                ra.add(ten_th)
            elif loai == "bo_chon" and q.get("bo_chon") in phan_tu:
                ra.add(ten_th)
        return ra

    async def vuot_thu_thach(self) -> None:
        """Cố vượt thử thách trên trang hiện tại (giả lập theo kịch bản).

        Page config «vuot_thu_thach_thanh_cong»: True → xoá tín hiệu
        thu_thach_truy_cap khỏi trang hiện tại (giả lập vượt thành công);
        thiếu/False → tín hiệu còn nguyên (giả lập vượt thất bại)."""
        self.hanh_dong.append(("vuot_thu_thach",))
        cau_hinh = self._trang.get(self._hien_tai or "", {})
        if cau_hinh.get("vuot_thu_thach_thanh_cong"):
            cau_hinh["tin_hieu"] = [t for t in (cau_hinh.get("tin_hieu") or [])
                                    if t != "thu_thach_truy_cap"]


#: Số lượt tối đa cố vượt thử thách truy cập trước khi bó tay (patch bổ sung
#: NV90). Mọi module và tầng (b) của khoi_phuc dùng chung hằng này.
SO_LUOT_VUOT_THU_THACH_TOI_DA = 3


def la_captcha_chu(tin_hieu) -> bool:
    """Nhận diện captcha chữ (hình ảnh chữ méo cần đọc) bằng CẤU TRÚC.

    Mã MỞ, không tên nhà cung cấp: chỉ kiểm tín hiệu «captcha» trong tập tín
    hiệu do trình duyệt báo. Trả True → bên gọi PHẢI trả «cần người» NGAY,
    KHÔNG được cố giải: (a) ràng buộc cấm thêm gói mới nên không có OCR;
    (b) cấm dịch vụ giải hộ. «Phát hiện được, không giải được — cần người».
    """
    try:
        return "captcha" in set(tin_hieu or [])
    except Exception:
        return False


async def vuot_thu_thach_toi_da(trinh_duyet,
                                so_luot: int = SO_LUOT_VUOT_THU_THACH_TOI_DA,
                                quy_tac: list | None = None) -> bool:
    """Cố vượt thử thách truy cập qua hợp đồng BoDieuKhienTrinhDuyet.

    Mã MỞ, không tên nhà cung cấp — dùng chung cho mọi module web và cho tầng
    (b) «đăng nhập lại tự động» của khoi_phuc (NV91 phần 2): khi đăng nhập lại
    gặp thử thách thì gọi helper này thay vì bỏ cuộc ngay.

    `quy_tac`: quy tắc cấu trúc của module (NV94-b) — đọc tín hiệu qua
    `tin_hieu(quy_tac)`; bộ điều khiển cũ chưa nhận tham số thì tự về
    `tin_hieu()`.

    Trả True = tín hiệu «thu_thach_truy_cap» đã hết (được phép tiếp tục);
    False = vẫn còn sau N lượt → bên gọi báo «cần người». Lỗi lạ khi đọc tín
    hiệu hay khi cố vượt → False (ĐÓNG CỬA). Không bao giờ coi việc gọi
    vuot_thu_thach() là đã vượt — sau mỗi lượt đều đọc lại tin_hieu() để chắc.

    ĐIỂM CẮM CHO NV92: helper chỉ dùng hợp đồng BoDieuKhienTrinhDuyet
    (tin_hieu/vuot_thu_thach). Khi NV92 thay TrinhDuyetGia bằng TrinhDuyetThat
    (dịch vụ trình duyệt thật qua CDP), helper này vẫn chạy không đổi — chỉ
    cần TrinhDuyetThat hiện thực vuot_thu_thach() thật (thử bấm ô xác nhận của trang chặn
    trên trang hiện tại, không che dấu vết, không gọi dịch vụ giải hộ).
    """
    try:
        so_luot = max(1, int(so_luot))
    except Exception:
        so_luot = SO_LUOT_VUOT_THU_THACH_TOI_DA

    async def _doc() -> set:
        try:
            return set(await trinh_duyet.tin_hieu(quy_tac) or [])
        except TypeError:
            r = trinh_duyet.tin_hieu()
            if asyncio.iscoroutine(r):
                r = await r
            return set(r or [])

    for _ in range(so_luot):
        try:
            if "thu_thach_truy_cap" not in await _doc():
                return True
        except Exception:
            return False
        try:
            await trinh_duyet.vuot_thu_thach()
        except Exception:
            return False
    try:
        return "thu_thach_truy_cap" not in await _doc()
    except Exception:
        return False


# ── Cầu nối lõi: bộ điều khiển trình duyệt thật (NV92 P1) ────────────────────

def _quy_tac_cua(module: Any) -> list:
    """Quy tắc tín hiệu cấu trúc do module khai (NV94-b); không khai → [].

    Mã mở, không tên nhà cung cấp: lõi chỉ truyền tiếp cho
    `trinh_duyet.tin_hieu(quy_tac)`, dịch vụ thực thi bằng cấu trúc.
    """
    fn = getattr(module, "quy_tac_tin_hieu", None)
    if not callable(fn):
        return []
    try:
        q = fn()
    except Exception as e:
        log.warning("khoi-phuc: quy_tac_tin_hieu lỗi: %s", type(e).__name__)
        return []
    return list(q) if isinstance(q, list) else []


async def _tin_hieu_an_toan(trinh_duyet: Any, quy_tac: list) -> set:
    """Đọc tín hiệu, tương thích bộ điều khiển cũ chưa nhận `quy_tac`."""
    try:
        return set(await trinh_duyet.tin_hieu(quy_tac) or [])
    except TypeError:
        try:
            r = trinh_duyet.tin_hieu()
            if asyncio.iscoroutine(r):
                r = await r
            return set(r or [])
        except Exception:
            return set()
    except Exception:
        return set()


def _ho_so_that_cua(module: Any, khoa: str) -> str | None:
    """Mã hồ sơ Firefox THẬT (hex) của một tài khoản (NV94-d).

    Đi đúng nguyên tắc: tài khoản → danh tính (module khai
    ``danh_tinh_id_cua(khoa)``) → bản ghi danh tính → ``ho_so_trinh_duyet``
    (hex, đã qua kiểm khuôn ở app/danh_tinh). Không có → None, KHÔNG đoán từ
    ``ho_so_trinh_duyet(khoa)`` kiểu "<module>-<hex>" (noVNC cũ giữ riêng).
    """
    lay_dt = getattr(module, "danh_tinh_id_cua", None)
    if not callable(lay_dt):
        return None
    try:
        dt_id = lay_dt(khoa)
    except Exception as e:
        log.warning("khoi-phuc: danh_tinh_id_cua lỗi: %s", type(e).__name__)
        return None
    if not dt_id:
        return None
    try:
        ban_ghi = danh_tinh_mod.lay(dt_id)
    except Exception as e:
        log.warning("khoi-phuc: đọc danh tính lỗi: %s", type(e).__name__)
        return None
    ho_so = (ban_ghi or {}).get("ho_so_trinh_duyet") or ""
    if isinstance(ho_so, str) and MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
        return ho_so
    return None


def lay_trinh_duyet_that(ho_so: str, dang_chay: dict | None = None,
                         ) -> BoDieuKhienTrinhDuyet | None:
    """Xin bộ điều khiển trình duyệt THẬT cho một hồ sơ (NV92 P1).

    Tìm trong `dang_chay` (ánh xạ id → thể hiện module đang chạy) module đầu
    tiên khai khả năng ``trinh_duyet`` trong ``manifest["cung_cap"]`` và gọi
    ``trinh_duyet_that(ho_so)`` của nó — KHÔNG gọi tên module cụ thể nào, lõi
    không biết module đó là module nào.

    Trả None (đóng cửa, không đoán) khi: `ho_so` sai khuôn; không có module
    nào có khả năng ``trinh_duyet``; module chưa cấu hình dịch vụ; module ném
    ngoại lệ. Người gọi tự quyết mã lỗi trả ra (thường 503).
    """
    if not isinstance(ho_so, str) or not MAU_HO_SO_TRINH_DUYET.fullmatch(ho_so):
        return None
    for mod in (dang_chay or {}).values():
        try:
            cung_cap = (getattr(mod, "manifest", None) or {}).get("cung_cap") or []
            lay = getattr(mod, "trinh_duyet_that", None)
            if "trinh_duyet" not in cung_cap or not callable(lay):
                continue
            td = lay(ho_so)
        except Exception as e:
            log.warning("lay-trinh-duyet-that: module lỗi: %s", type(e).__name__)
            return None
        if isinstance(td, BoDieuKhienTrinhDuyet):
            return td
        log.warning("lay-trinh-duyet-that: module trả kiểu lạ (%s) — đóng cửa",
                    type(td).__name__)
        return None
    return None


@contextlib.asynccontextmanager
async def phien_dieu_khien_that(td: BoDieuKhienTrinhDuyet):
    """Phiên dùng bộ điều khiển trình duyệt thật: giành quyền trước, NHẢ LUÔN sau.

    `td` có `gianh`/`nha` (TrinhDuyetThat gọi dịch vụ qua HTTP) → gọi chúng;
    bộ điều khiển GIẢ trong test (không có hai phương thức này) → dùng trực
    tiếp. Ngoại lệ ở bất kỳ bước nào (kể cả khi giành lỗi) cũng phải nhả quyền
    trước khi lan ra — hồ sơ không bao giờ kẹt ở trạng thái «đang bận» làm
    noVNC/người khác không mở được.
    """
    gianh = getattr(td, "gianh", None)
    nha = getattr(td, "nha", None)
    if callable(gianh):
        await gianh()
    try:
        yield td
    finally:
        if callable(nha):
            await nha()


async def tin_hieu_tu_cookie_ho_so_da_dong(trinh_duyet: BoDieuKhienTrinhDuyet,
                                          quy_tac: list | None) -> set | None:
    """Đánh giá quy tắc tín hiệu bằng KHO COOKIE của hồ sơ ĐÃ ĐÓNG.

    Dịch vụ trình duyệt chỉ cho `tin_hieu()` qua phiên sống (hồ sơ phải MỞ và
    đã giành quyền); hồ sơ đã đóng → 404 nên đường kiểm cũ luôn «chua_dang_nhap».
    Kho cookie trên đĩa (API hồ sơ, không mở trình duyệt) cho phép kiểm đúng
    với dịch vụ thật ở trạng thái ĐÃ ĐÓNG mà nhan-ho-so yêu cầu.

    NGUYÊN TẮC (không bịa): chỉ quy tắc loai «cookie» (cần `mien` + `ten`) là
    đánh giá được từ kho; quy tắc url/tieu_de/bo_chon cần trang sống → bỏ qua.
    (Trong kho này mọi quy tắc «da_dang_nhap» đều là quy tắc cookie.) Mỗi miền
    chỉ đọc MỘT lần; bất kỳ miền nào đọc lỗi → cả lượt là «không kiểm được».

    Trả:
    - tập tín hiệu chứng minh được (có thể rỗng = đã kiểm, không khớp cookie);
    - None = không kiểm được (không có quy tắc cookie nào, bộ điều khiển
      không hỗ trợ, kho không đọc được).
    """
    quy_tac_cookie = [
        q for q in (quy_tac or [])
        if isinstance(q, dict) and q.get("loai") == "cookie"
        and isinstance(q.get("mien"), str) and isinstance(q.get("ten"), str)
        and isinstance(q.get("tin_hieu"), str) and q["tin_hieu"]
    ]
    if not quy_tac_cookie:
        return None
    doc = getattr(trinh_duyet, "doc_cookie_ho_so_da_dong", None)
    if not callable(doc):
        return None
    ra: set = set()
    for mien in sorted({q["mien"] for q in quy_tac_cookie}):
        try:
            r = doc(mien)
            if asyncio.iscoroutine(r):
                r = await r
        except Exception:
            return None
        if r is None:
            return None
        if not isinstance(r, dict):
            return None
        for q in quy_tac_cookie:
            if q["mien"] == mien and q["ten"] in r:
                ra.add(q["tin_hieu"])
    return ra


async def kiem_da_dang_nhap(trinh_duyet: BoDieuKhienTrinhDuyet,
                            quy_tac: list | None) -> tuple[bool, str]:
    """Kiểm «đã đăng nhập» của một hồ sơ bằng bộ kiểm tín hiệu đúng mọi đường.

    1. ĐƯỜNG ƯU TIÊN — kho cookie của hồ sơ ĐÃ ĐÓNG (không mở trình duyệt):
       đúng với dịch vụ trình duyệt thật ở đúng trạng thái nhan-ho-so yêu cầu.
    2. DỰ PHÒNG — phiên sống có kiểm soát: giành quyền (dịch vụ TỰ MỞ hồ sơ
       Firefox đóng, NV94-a) → đọc tín hiệu trang → nhả quyền LUÔN. Chỉ dùng
       khi đường (1) «không kiểm được» (hồ sơ Chromium, dịch vụ lỗi…).
    3. Cả hai đều không kiểm được → (False, «khong_kiem_duoc») — ĐÓNG CỬA.

    Trả (ket_qua, nguon) với nguon thuộc {"cookie_ho_so", "phien_truc_tiep",
    "khong_kiem_duoc"}. NV179 («Kiểm lại đăng nhập») dùng lại helper này.
    """
    th_cookie = await tin_hieu_tu_cookie_ho_so_da_dong(trinh_duyet, quy_tac)
    if th_cookie is not None:
        return "da_dang_nhap" in th_cookie, "cookie_ho_so"
    try:
        async with phien_dieu_khien_that(trinh_duyet):
            th = await _tin_hieu_an_toan(trinh_duyet, list(quy_tac or []))
    except Exception:
        return False, "khong_kiem_duoc"
    return "da_dang_nhap" in th, "phien_truc_tiep"


# ── Đường tới hồ sơ trình duyệt (chống đi ngược thư mục) ─────────────────────

def duong_ho_so_trinh_duyet(ten_id: str) -> Path:
    """Đường tới hồ sơ trình duyệt của một workspace: ghép từ ID đã kiểm,
    sau resolve() phải nằm dưới thư mục gốc. Sai khuôn/vượt gốc → ValueError."""
    if not isinstance(ten_id, str) or not MAU_HO_SO_TRINH_DUYET.fullmatch(ten_id):
        raise ValueError(f"id hồ sơ trình duyệt không hợp lệ: {ten_id!r}")
    goc = (kho.thu_muc_du_lieu() / "ho-so-trinh-duyet").resolve()
    p = (goc / ten_id).resolve()
    if not p.is_relative_to(goc):
        raise ValueError(f"đường hồ sơ vượt ngoài thư mục gốc: {ten_id!r}")
    return p


# ── Trạng thái khôi phục (lưu 0600) ──────────────────────────────────────────

def _doc_tat_ca() -> dict:
    try:
        du_lieu = kho.doc_bi_mat(_TEN_KHO_TRANG_THAI)
    except kho.KhoHong:
        log.warning("khoi-phuc: kho trạng thái hỏng — coi như rỗng, không ghi đè")
        return {}
    return du_lieu if isinstance(du_lieu, dict) else {}


def _ghi_tat_ca(du_lieu: dict) -> None:
    kho.ghi_bi_mat(_TEN_KHO_TRANG_THAI, du_lieu)


def _khoa_trang_thai(module_id: str, khoa: str) -> str:
    return f"{module_id}\x00{khoa}"


def _trang_thai_mac_dinh() -> dict:
    return {"hong_b_lien_tiep": 0, "ngung_tu_dang_nhap": False,
            "can_nguoi": False, "huong_dan": "", "ly_do": ""}


def lay_trang_thai(module_id: str, khoa: str) -> dict:
    """Trạng thái khôi phục của một tài khoản (bản sao; không thấy → mặc định)."""
    tt = _doc_tat_ca().get(_khoa_trang_thai(module_id, khoa))
    if not isinstance(tt, dict):
        return _trang_thai_mac_dinh()
    ra = _trang_thai_mac_dinh()
    for k in ra:
        if k in tt:
            ra[k] = tt[k]
    return ra


def _cap_nhat(module_id: str, khoa: str, **truong) -> dict:
    du_lieu = _doc_tat_ca()
    khoa_tt = _khoa_trang_thai(module_id, khoa)
    tt = lay_trang_thai(module_id, khoa)
    tt.update(truong)
    du_lieu[khoa_tt] = tt
    _ghi_tat_ca(du_lieu)
    return tt


def _tang_hong_b(module_id: str, khoa: str) -> int:
    tt = lay_trang_thai(module_id, khoa)
    return _cap_nhat(module_id, khoa,
                      hong_b_lien_tiep=int(tt.get("hong_b_lien_tiep") or 0) + 1)["hong_b_lien_tiep"]


def _dat_lai_hong_b(module_id: str, khoa: str) -> None:
    _cap_nhat(module_id, khoa, hong_b_lien_tiep=0)


def dat_can_nguoi(module_id: str, khoa: str, ly_do: str, huong_dan: str) -> None:
    _cap_nhat(module_id, khoa, can_nguoi=True, ly_do=ly_do, huong_dan=huong_dan)


def xoa_can_nguoi(module_id: str, khoa: str) -> None:
    """Người đã xử lý tay xong → xoá trạng thái can_nguoi và bộ đếm."""
    _cap_nhat(module_id, khoa, can_nguoi=False, huong_dan="", ly_do="",
              hong_b_lien_tiep=0, ngung_tu_dang_nhap=False)


# ── Mở noVNC ─────────────────────────────────────────────────────────────────

def mo_novnc_mac_dinh(module_id: str, ho_so: str,
                      nguoi_dung_id: str | None = None) -> str | None:
    """Cấp vé noVNC cho workspace và trả đường mở. Vé không bao giờ vào log.

    Trả None nếu không cấp được vé (workspace chưa mở) — người gọi vẫn trả
    hướng dẫn chữ cho người dùng.
    """
    try:
        from app import novnc
        ve = novnc.cap_ve(module_id, ho_so, nguoi_dung_id)
    except Exception as e:
        log.warning("khoi-phuc: không cấp được vé noVNC cho %s/%s: %s",
                    module_id, ho_so, type(e).__name__)
        return None
    return f"/novnc/dang-nhap?ve={ve}"


# ── Bộ máy chính ─────────────────────────────────────────────────────────────

def _id_cua_module(module: Any) -> str:
    manifest = getattr(module, "manifest", None)
    if isinstance(manifest, dict):
        for truong in ("id", "tien_to"):
            gia_tri = manifest.get(truong)
            if isinstance(gia_tri, str) and gia_tri:
                return gia_tri
    return type(module).__name__


def _che_email(email: str) -> str:
    email = str(email or "")
    if "@" not in email:
        return "…"
    dau, mien = email.split("@", 1)
    return f"{dau[:1]}…@{mien}" if dau else f"…@{mien}"


async def _vao_can_nguoi(module_id: str, khoa: str, ly_do: str,
                         mo_novnc=None, nguoi_dung_id: str | None = None,
                         ho_so: str | None = None,
                         huong_dan_module: str = "") -> KetQuaKhoiPhuc:
    """Tầng (d): ghi trạng thái can_nguoi, mở noVNC, trả hướng dẫn tiếng Việt."""
    ho_so = ho_so or f"{module_id}-{khoa}"
    # NV94-d: workspace noVNC ("<module>-<khoa>") dùng khuôn RỘNG tương thích
    # ngược — khuôn hex thuần chỉ áp cho đường điều khiển trình duyệt thật.
    if not isinstance(ho_so, str) or not _MAU_ID_WORKSPACE_NOVNC.fullmatch(ho_so):
        ho_so = f"{module_id}-mac-dinh"
    mo = mo_novnc or mo_novnc_mac_dinh
    try:
        url = mo(module_id, ho_so, nguoi_dung_id)
    except Exception as e:
        log.warning("khoi-phuc: mở noVNC lỗi: %s", type(e).__name__)
        url = None
    huong_dan = (
        f"Tài khoản cần bạn thao tác trực tiếp ({ly_do}). "
        f"Hãy mở noVNC cho workspace «{ho_so}»"
        + (" theo đường đã cấp" if url else "")
        + " rồi đăng nhập tay; xong quay lại bấm «Đã xử lý xong»."
    )
    if huong_dan_module:
        huong_dan = huong_dan_module + " " + huong_dan
    dat_can_nguoi(module_id, khoa, ly_do, huong_dan)
    log.warning("khoi-phuc: %s/%s can_nguoi (%s)", module_id, khoa, ly_do)
    return KetQuaKhoiPhuc(False, tang=TANG_D, ma_loi=MA_CAN_NGUOI,
                          can_nguoi=True, huong_dan=huong_dan, url_novnc=url)


async def khoi_phuc(module: Any, tai_khoan: TaiKhoanCanKhoiPhuc, *,
                    trinh_duyet: BoDieuKhienTrinhDuyet | None = None,
                    dung_mat_khau_luu: bool = True,
                    mo_novnc=None,
                    nguoi_dung_id: str | None = None,
                    dang_chay: dict | None = None) -> KetQuaKhoiPhuc:
    """Chạy các tầng khôi phục rẻ → đắt cho một tài khoản.

    - `tai_khoan.mat_khau`: mật khẩu tạm của lần gọi này (BiMatTam) — bị engine
      xoá khỏi bộ nhớ ngay sau khi tầng (b) dùng xong. None +
      `dung_mat_khau_luu=True` → hỏi module `mat_khau_da_luu(khoa)` (bản sao
      module đưa ra cũng bị engine xoá); «chỉ dùng cookie» thì đặt cả hai về
      tắt/bỏ qua.
    - `trinh_duyet`: bộ điều khiển trình duyệt cho tầng (b); None thì module tự
      quyết (thường → can_nguoi kèm hướng dẫn mở noVNC).
    - `dang_chay`: ánh xạ id → thể hiện module đang chạy (NV92 P1). Khi
      `trinh_duyet` là None mà có `dang_chay`, lõi TỰ lấy bộ điều khiển thật
      qua cầu nối `lay_trinh_duyet_that` (hồ sơ hex lấy từ bản ghi danh tính
      qua `danh_tinh_id_cua(khoa)` — NV94-d); không lấy được → giữ None
      (đóng cửa, không đoán).
    - Không ném ngoại lệ của module ra ngoài: tầng hỏng thì sang tầng kế.
    """
    module_id = _id_cua_module(module)
    khoa = kiem_khoa(tai_khoan.khoa)
    td_tu_cau_noi = False  # NV92 P1: bộ điều khiển do lõi tự lấy → lõi tự giành/nhả
    if trinh_duyet is None and dang_chay is not None:
        # NV92 P1 + NV94-d: người gọi chưa có bộ điều khiển → thử cầu nối lõi.
        # Hồ sơ THẬT (hex) đi qua danh tính, không đoán từ "<module>-<hex>".
        ho_so = _ho_so_that_cua(module, khoa)
        td = lay_trinh_duyet_that(ho_so or "", dang_chay)
        if td is not None:
            trinh_duyet = td
            td_tu_cau_noi = True
            log.info("khoi-phuc: %s/%s dùng cầu nối trình duyệt thật (hồ sơ …%s)",
                     module_id, khoa, (ho_so or "")[-8:])
    email_che = _che_email(tai_khoan.email)
    log.info("khoi-phuc: bắt đầu module=%s khoa=%s (%s)", module_id, khoa, email_che)
    ma_loi_cuoi = ""

    # ── Tầng (a): làm mới phiên/token bằng cookie đang có ──
    lam_moi = getattr(module, "lam_moi_phien", None)
    if callable(lam_moi):
        kq = await _goi_an_toan(lam_moi, TANG_A, khoa)
        if kq.can_nguoi:
            return await _vao_can_nguoi(module_id, khoa, "gặp màn hình cần người xử lý",
                                        mo_novnc, nguoi_dung_id,
                                        _ho_so_cua(module, module_id, khoa),
                                        kq.huong_dan)
        if kq.thanh_cong:
            _dat_lai_hong_b(module_id, khoa)
            log.info("khoi-phuc: %s/%s xong ở tầng (a)", module_id, khoa)
            return kq
        ma_loi_cuoi = kq.ma_loi
        log.info("khoi-phuc: %s/%s tầng (a) hỏng (%s)", module_id, khoa, kq.ma_loi)

    # ── Tầng (b): đăng nhập lại tự động bằng mật khẩu đã lưu ──
    tt = lay_trang_thai(module_id, khoa)
    mat_khau_truc_tiep = tai_khoan.mat_khau
    mat_khau_kho: BiMatTam | None = None
    try:
        if not tt.get("ngung_tu_dang_nhap"):
            mat_khau_dung = mat_khau_truc_tiep
            if mat_khau_dung is None and dung_mat_khau_luu:
                lay_mk = getattr(module, "mat_khau_da_luu", None)
                if callable(lay_mk):
                    try:
                        mat_khau_kho = lay_mk(khoa)
                    except Exception as e:
                        log.warning("khoi-phuc: đọc mật khẩu lưu lỗi: %s", type(e).__name__)
                        mat_khau_kho = None
                    if mat_khau_kho is not None and not isinstance(mat_khau_kho, BiMatTam):
                        mat_khau_kho = None
                    mat_khau_dung = mat_khau_kho
            dang_nhap_td = getattr(module, "dang_nhap_tu_dong", None)
            if mat_khau_dung is not None and callable(dang_nhap_td):
                # NV94-b: quy tắc tín hiệu cấu trúc do module khai (mã mở).
                quy_tac_b = _quy_tac_cua(module)

                async def _thu_thach_con() -> bool:
                    """Còn tín hiệu thử thách truy cập không (đọc an toàn)."""
                    if trinh_duyet is None:
                        return False
                    return "thu_thach_truy_cap" in await _tin_hieu_an_toan(
                        trinh_duyet, quy_tac_b)

                async def _can_nguoi_thu_thach():
                    return await _vao_can_nguoi(
                        module_id, khoa, "gặp thử thách truy cập, không vượt được",
                        mo_novnc, nguoi_dung_id,
                        _ho_so_cua(module, module_id, khoa), "")

                tk_b = TaiKhoanCanKhoiPhuc(khoa=khoa, email=tai_khoan.email,
                                           mat_khau=mat_khau_dung)
                if td_tu_cau_noi:
                    # Bộ điều khiển do lõi tự lấy → lõi giành/nhả quyền cho
                    # đúng lượt này; người gọi tự đưa thì tự quản vòng đời.
                    # NV94-P3: dùng xong DONG luôn (đóng httpx.Client, chống
                    # rò rỉ socket — mỗi lượt một thể hiện mới).
                    try:
                        async with phien_dieu_khien_that(trinh_duyet):
                            # NV94-P3: kiểm tín hiệu SAU khi đã giành quyền.
                            # (Trước đây kiểm trước khi giành → với trình duyệt
                            # thật luôn 409 và bị nuốt thành «không thử thách».)
                            if await _thu_thach_con():
                                if not await vuot_thu_thach_toi_da(
                                        trinh_duyet, quy_tac=quy_tac_b):
                                    return await _can_nguoi_thu_thach()
                            kq = await _goi_an_toan(dang_nhap_td, TANG_B, tk_b,
                                                   trinh_duyet)
                    except Exception as e:
                        # Giành quyền lỗi (hồ sơ đang bận/không mở): hỏng tầng này,
                        # sang tầng kế — khoi_phuc không bao giờ ném ra ngoài.
                        log.warning("khoi-phuc: không giành được trình duyệt: %s",
                                    type(e).__name__)
                        kq = KetQuaKhoiPhuc(False, tang=TANG_B, ma_loi="trinh_duyet_ban")
                    finally:
                        dong = getattr(trinh_duyet, "dong", None)
                        if callable(dong):
                            try:
                                await dong()
                            except Exception:
                                pass
                else:
                    # Người gọi tự quản vòng đời trình duyệt: kiểm trước khi
                    # gọi module như cũ (không có giành/409 ở đây).
                    if await _thu_thach_con():
                        if not await vuot_thu_thach_toi_da(
                                trinh_duyet, quy_tac=quy_tac_b):
                            return await _can_nguoi_thu_thach()
                    kq = await _goi_an_toan(dang_nhap_td, TANG_B, tk_b, trinh_duyet)
                if kq.can_nguoi:
                    return await _vao_can_nguoi(module_id, khoa, "gặp màn hình cần người xử lý",
                                                mo_novnc, nguoi_dung_id,
                                                _ho_so_cua(module, module_id, khoa),
                                                kq.huong_dan)
                if kq.thanh_cong:
                    _dat_lai_hong_b(module_id, khoa)
                    log.info("khoi-phuc: %s/%s xong ở tầng (b)", module_id, khoa)
                    return kq
                ma_loi_cuoi = kq.ma_loi
                so_lan = _tang_hong_b(module_id, khoa)
                log.info("khoi-phuc: %s/%s tầng (b) hỏng lần %d (%s)",
                         module_id, khoa, so_lan, kq.ma_loi)
                if so_lan >= HONG_B_TOI_DA:
                    _cap_nhat(module_id, khoa, ngung_tu_dang_nhap=True)
                    log.warning("khoi-phuc: %s/%s hỏng tầng (b) %d lần liên tiếp — "
                                "ngừng tự đăng nhập, cần người bấm tiếp",
                                module_id, khoa, so_lan)
        else:
            log.info("khoi-phuc: %s/%s đã ngừng tự đăng nhập — bỏ qua tầng (b)", module_id, khoa)
    finally:
        # Xoá mọi bản mật khẩu engine đã chạm tới trong tầng (b): bản lấy từ kho
        # module lẫn bản người gọi đưa trực tiếp. Gọi xoa() nhiều lần vẫn an toàn.
        if mat_khau_kho is not None:
            mat_khau_kho.xoa()
        if mat_khau_truc_tiep is not None:
            mat_khau_truc_tiep.xoa()

    # ── Tầng (c): chuyển tài khoản/khoá kế ──
    ke_fn = getattr(module, "tai_khoan_ke", None)
    khoa_moi: str | None = None
    if callable(ke_fn):
        try:
            khoa_moi = ke_fn(khoa)
        except Exception as e:
            log.warning("khoi-phuc: tai_khoan_ke lỗi: %s", type(e).__name__)
            khoa_moi = None
    if khoa_moi:
        try:
            kiem_khoa(khoa_moi)
        except ValueError:
            khoa_moi = None
    if khoa_moi:
        danh_dau = getattr(module, "danh_dau_hong", None)
        if callable(danh_dau):
            try:
                danh_dau(khoa, ma_loi_cuoi or "khong_ro")
            except Exception as e:
                log.warning("khoi-phuc: danh_dau_hong lỗi: %s", type(e).__name__)
        log.info("khoi-phuc: %s/%s chuyển sang khoá %s ở tầng (c)", module_id, khoa, khoa_moi)
        return KetQuaKhoiPhuc(True, tang=TANG_C, ma_loi=ma_loi_cuoi,
                              khoa_moi=khoa_moi,
                              huong_dan=f"Đã chuyển sang tài khoản kế ({khoa_moi}).")

    # ── Tầng (d): hết cách → cần người ──
    return await _vao_can_nguoi(
        module_id, khoa,
        "đã thử làm mới phiên, đăng nhập lại và chuyển tài khoản nhưng chưa được",
        mo_novnc, nguoi_dung_id, _ho_so_cua(module, module_id, khoa))


def _ho_so_cua(module: Any, module_id: str, khoa: str) -> str:
    """ID workspace cho noVNC: module khai `ho_so_trinh_duyet(khoa)` thì dùng,
    không thì `<module_id>-<khoa>` (đã qua kiểm khuôn ở _vao_can_nguoi)."""
    fn = getattr(module, "ho_so_trinh_duyet", None)
    if callable(fn):
        try:
            hs = fn(khoa)
            if isinstance(hs, str) and _MAU_ID_WORKSPACE_NOVNC.fullmatch(hs):
                return hs
        except Exception as e:
            log.warning("khoi-phuc: ho_so_trinh_duyet lỗi: %s", type(e).__name__)
    return f"{module_id}-{khoa}"
