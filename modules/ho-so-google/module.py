"""Module hồ sơ Google — thủ tục ĐĂNG NHẬP GOOGLE trong hồ sơ trình duyệt (NV92 P2).

Mô hình «Cách A» (c2a): MỘT HỒ SƠ TRÌNH DUYỆT = MỘT TÀI KHOẢN GOOGLE đã đăng nhập
sống. Thủ tục đăng nhập Google là việc RIÊNG của Google nên nằm ở module này —
không ở lõi, không ở module provider.

Ngữ nghĩa 4 bước (ghi ở NV92-P3-XONG cho hợp đồng ``tai_dung_ho_so``):
  1. «đi tắt»: hồ sơ đã đăng nhập sẵn → xong ngay, không chạm mật khẩu;
  2. «bấm Google»: điền email/mật khẩu trên accounts.google.com;
  3. «chờ phiên»: Google đòi mã 2FA → trả ``cho_2fa=True``, giữ tiến độ để quản
     trị gửi mã sau qua ``nhap_ma_2fa()``;
  4. «can_nguoi»: gặp CAPTCHA/xác minh thiết bị (hoặc 2FA mà không có mã) →
     DỪNG, trạng thái «cần người» + noVNC. KHÔNG tự giải thử thách, KHÔNG che
     dấu tự động.

An toàn bí mật: email/mật khẩu/mã 2FA ở dạng ``BiMatTam``, bị xoá khỏi bộ nhớ
ngay sau khi dùng (kể cả khi lỗi); không log, không trả trong kết quả, không
vào nhật ký. Bộ chọn CSS dưới đây là cấu trúc DOM công khai của trang đăng
nhập Google (mã mở).

HỌC TỪ c2a (chỉ đọc lấy hiểu, tự viết lại): TriTue2011/chatgpt2api,
``captcha-solver/src/auto_login.py`` + ``workspaces.py`` — các bước: mở trang
đăng nhập → điền email → tiếp tục → điền mật khẩu → tiếp tục → xử lý 2FA →
kiểm tra phiên. KHÔNG chép mã, KHÔNG chép bí mật/dữ liệu nhà/IP nội bộ
(đã kiểm: không dùng vault.py/accounts_db.py/settings.py của c2a).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.tai_khoan_hang_loat import BiMatTam

log = logging.getLogger("web2api.module.ho_so_google")

#: Trang đăng nhập và trang kiểm tra phiên của Google.
URL_DANG_NHAP = "https://accounts.google.com/"
URL_KIEM_TRA = "https://myaccount.google.com/"

#: Bộ chọn CSS trên trang đăng nhập Google (cấu trúc DOM công khai).
BO_CHON_EMAIL = 'input[type="email"]'
BO_CHON_TIEP_EMAIL = "#identifierNext"
BO_CHON_MAT_KHAU = 'input[type="password"]'
BO_CHON_TIEP_MAT_KHAU = "#passwordNext"
BO_CHON_MA_2FA = 'input[type="tel"]'
BO_CHON_TIEP_MA_2FA = "#idvPreregisteredPhoneNext"

#: Tín hiệu «cần người» từ tin_hieu() của bộ điều khiển (mã mở theo hợp đồng).
TIN_HIEU_CAN_NGUOI = frozenset({"captcha", "xac_minh_thiet_bi", "xac_minh"})

#: Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): cookie đặc trưng có/không, URL đích.
#: Mã mở — không dò chữ trên trang, không tên nhà cung cấp trong mã lõi.
#: Dịch vụ thực thi các quy tắc này (server.doc_tin_hieu).
QUY_TAC_TIN_HIEU = [
    {"loai": "cookie", "mien": "google.com", "ten": "SID",
     "tin_hieu": "da_dang_nhap"},
    {"loai": "cookie", "mien": "google.com", "ten": "__Secure-1PSID",
     "tin_hieu": "da_dang_nhap"},
    {"loai": "url_chua", "gia_tri": "accounts.google.com/challenge",
     "tin_hieu": "thu_thach_truy_cap"},
    {"loai": "url_chua", "gia_tri": "accounts.google.com/signin",
     "tin_hieu": "chua_dang_nhap"},
]

#: Các bước của thủ tục (ngữ nghĩa 4 bước).
BUOC_DI_TAT = "di_tat"
BUOC_BAM_GOOGLE = "bam_google"
BUOC_CHO_PHIEN = "cho_phien"
BUOC_CAN_NGUOI = "can_nguoi"


@dataclass
class KetQuaDangNhapGoogle:
    """Kết quả một lượt đăng nhập Google. KHÔNG bao giờ chứa bí mật thô."""

    thanh_cong: bool
    buoc: str = ""
    cho_2fa: bool = False
    can_nguoi: bool = False
    ma_loi: str = ""
    ma_trang_thai: int | None = None
    huong_dan: str = ""
    url_novnc: str | None = None

    def sang_dict(self) -> dict:
        """dict JSON được — chỉ các trường an toàn, không bí mật."""
        return {
            "thanh_cong": bool(self.thanh_cong),
            "buoc": self.buoc,
            "cho_2fa": bool(self.cho_2fa),
            "can_nguoi": bool(self.can_nguoi),
            "ma_loi": self.ma_loi,
            "ma_trang_thai": self.ma_trang_thai,
            "huong_dan": self.huong_dan,
            "url_novnc": self.url_novnc,
        }


def _can_nguoi(ma_loi: str, huong_dan: str = "",
               url_novnc: str | None = None) -> KetQuaDangNhapGoogle:
    return KetQuaDangNhapGoogle(
        False, buoc=BUOC_CAN_NGUOI, can_nguoi=True, ma_loi=ma_loi,
        huong_dan=huong_dan, url_novnc=url_novnc)


class Module:
    """Thủ tục đăng nhập Google theo hợp đồng 8 hàm NV88 + 2FA + tiến độ."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        #: Tiến độ đăng nhập theo hồ sơ (bộ nhớ tiến trình; mất khi khởi động lại
        #: — trạng thái bền nằm ở kho danh tính của lõi).
        self._tien_do: dict[str, dict] = {}

    # -- vòng đời chuẩn --------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Module không có trường cấu hình nào — chỉ nạp nhẹ."""

    async def cai(self, cau_hinh: dict) -> dict:
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None,
                "cau_hinh_luu": {}}

    async def suc_khoe(self) -> dict:
        return {"ok": True,
                "dang_cho_2fa": sorted(h for h, t in self._tien_do.items()
                                       if t.get("cho_2fa"))}

    async def dung(self) -> None:
        self._tien_do.clear()

    # -- tiến độ ----------------------------------------------------------------

    def _dat_tien_do(self, ho_so: str, **truong: Any) -> None:
        ban_ghi = self._tien_do.setdefault(str(ho_so), {})
        ban_ghi.update(truong)
        ban_ghi["cap_nhat_luc"] = time.time()

    def tien_do(self, ho_so: str) -> dict:
        """Trạng thái tiến độ của một hồ sơ (bản sao; chưa có → mặc định)."""
        ban_ghi = self._tien_do.get(str(ho_so))
        ra = {"giai_doan": "chua_bat_dau", "buoc": "", "cho_2fa": False,
              "can_nguoi": False, "ma_loi": "", "cap_nhat_luc": None}
        if isinstance(ban_ghi, dict):
            for k in ra:
                if k in ban_ghi:
                    ra[k] = ban_ghi[k]
        return ra

    def xoa_tien_do(self, ho_so: str) -> None:
        self._tien_do.pop(str(ho_so), None)

    # -- thủ tục đăng nhập ------------------------------------------------------

    def quy_tac_tin_hieu(self) -> list:
        """Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): module KHAI, mã tín hiệu MỞ.

        Dịch vụ thực thi: cookie đặc trưng có/không, URL đích — không dò chữ.
        Chỉ NHẬN RA thử thách → can_nguoi (không tự vượt).
        """
        return [dict(q) for q in QUY_TAC_TIN_HIEU]

    async def dang_nhap(self, ho_so: str, email: str,
                               mat_khau: BiMatTam | None,
                               trinh_duyet,
                               ma_2fa: BiMatTam | None = None,
                               url_novnc: str | None = None
                               ) -> KetQuaDangNhapGoogle:
        """Đăng nhập Google trong hồ sơ bằng bộ điều khiển trình duyệt.

        - ``mat_khau=None``: chỉ kiểm «đi tắt»; chưa đăng nhập → can_nguoi
          (người đăng nhập tay qua noVNC).
        - Mật khẩu/mã 2FA luôn bị xoá sau khi dùng, kể cả khi lỗi.
        """
        ho_so = str(ho_so or "")
        self._dat_tien_do(ho_so, giai_doan="bat_dau", buoc="", cho_2fa=False,
                          can_nguoi=False, ma_loi="")
        if trinh_duyet is None:
            return _can_nguoi("chua_co_trinh_duyet",
                              "Chưa có bộ điều khiển trình duyệt cho hồ sơ này.",
                              url_novnc)
        try:
            # Bước 1 — «đi tắt»: hồ sơ đã đăng nhập sẵn thì xong ngay.
            self._dat_tien_do(ho_so, giai_doan="kiem_tra_phien")
            await trinh_duyet.mo_trang(URL_KIEM_TRA)
            if "da_dang_nhap" in (trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set()):
                self._dat_tien_do(ho_so, giai_doan="xong", buoc=BUOC_DI_TAT)
                return KetQuaDangNhapGoogle(True, buoc=BUOC_DI_TAT)
            if mat_khau is None:
                self._dat_tien_do(ho_so, giai_doan="can_nguoi",
                                  buoc=BUOC_CAN_NGUOI, can_nguoi=True,
                                  ma_loi="can_nguoi")
                return _can_nguoi(
                    "can_nguoi",
                    "Hồ sơ chưa đăng nhập Google. Hãy mở noVNC đăng nhập tay "
                    "rồi thử lại.", url_novnc)

            # Bước 2 — «bấm Google»: điền email/mật khẩu.
            self._dat_tien_do(ho_so, giai_doan="dien_email", buoc=BUOC_BAM_GOOGLE)
            await trinh_duyet.mo_trang(URL_DANG_NHAP)
            if TIN_HIEU_CAN_NGUOI & set(trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set()):
                return self._dung_can_nguoi(ho_so, url_novnc)
            mat_khau_tho = mat_khau.lay()
            try:
                await trinh_duyet.dien(BO_CHON_EMAIL, str(email or ""))
                await trinh_duyet.bam(BO_CHON_TIEP_EMAIL)
                self._dat_tien_do(ho_so, giai_doan="dien_mat_khau")
                await trinh_duyet.dien(BO_CHON_MAT_KHAU, mat_khau_tho)
                await trinh_duyet.bam(BO_CHON_TIEP_MAT_KHAU)
            finally:
                mat_khau_tho = None  # noqa: F841 — không giữ chuỗi thô

            tin_hieu = set(trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
            if TIN_HIEU_CAN_NGUOI & tin_hieu:
                return self._dung_can_nguoi(ho_so, url_novnc)
            # Bước 3 — «chờ phiên»: Google đòi mã 2FA.
            if "2fa" in tin_hieu:
                self._dat_tien_do(ho_so, giai_doan="cho_2fa",
                                  buoc=BUOC_CHO_PHIEN, cho_2fa=True,
                                  ma_loi="cho_2fa")
                if ma_2fa is None:
                    return KetQuaDangNhapGoogle(
                        False, buoc=BUOC_CHO_PHIEN, cho_2fa=True,
                        ma_loi="cho_2fa",
                        huong_dan="Google yêu cầu mã xác minh 2 bước. Gửi mã "
                                  "qua đường «gửi mã 2FA» rồi thử lại.")
                return await self._nhap_ma_2fa_noi_bo(
                    ho_so, ma_2fa, trinh_duyet, url_novnc)
            if "da_dang_nhap" in tin_hieu:
                self._dat_tien_do(ho_so, giai_doan="xong",
                                  buoc=BUOC_BAM_GOOGLE)
                return KetQuaDangNhapGoogle(True, buoc=BUOC_BAM_GOOGLE)
            self._dat_tien_do(ho_so, giai_doan="hong", buoc=BUOC_BAM_GOOGLE,
                              ma_loi="khong_lay_duoc_phien")
            return KetQuaDangNhapGoogle(False, buoc=BUOC_BAM_GOOGLE,
                                        ma_loi="khong_lay_duoc_phien")
        except Exception as e:
            # Lỗi lạ → ĐÓNG CỬA: báo hỏng, không bao giờ báo thành công.
            log.warning("ho-so-google: lỗi không ngờ ở hồ sơ %s: %s",
                        ho_so, type(e).__name__)
            self._dat_tien_do(ho_so, giai_doan="hong", ma_loi="loi_module")
            return KetQuaDangNhapGoogle(False, ma_loi="loi_module")
        finally:
            # Xoá mọi bí mật đã chạm tới trong lượt này, kể cả khi lỗi.
            if mat_khau is not None:
                mat_khau.xoa()
            if ma_2fa is not None:
                ma_2fa.xoa()

    def _dung_can_nguoi(self, ho_so: str,
                        url_novnc: str | None) -> KetQuaDangNhapGoogle:
        """Bước 4 — «can_nguoi»: gặp CAPTCHA/xác minh → dừng, không cố vượt."""
        self._dat_tien_do(ho_so, giai_doan="can_nguoi", buoc=BUOC_CAN_NGUOI,
                          can_nguoi=True, ma_loi="can_nguoi")
        log.warning("ho-so-google: hồ sơ %s gặp màn hình cần người xử lý",
                    ho_so)
        return _can_nguoi(
            "can_nguoi",
            "Google đưa ra thử thách (CAPTCHA/xác minh) — máy DỪNG, không tự "
            "giải. Hãy mở noVNC xử lý tay rồi bấm «Đã xử lý xong».",
            url_novnc)

    async def _nhap_ma_2fa_noi_bo(self, ho_so: str, ma: BiMatTam, trinh_duyet,
                                 url_novnc: str | None) -> KetQuaDangNhapGoogle:
        ma_tho = ma.lay()
        try:
            await trinh_duyet.dien(BO_CHON_MA_2FA, ma_tho)
            await trinh_duyet.bam(BO_CHON_TIEP_MA_2FA)
        finally:
            ma_tho = None  # noqa: F841
            ma.xoa()
        tin_hieu = set(trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
        if TIN_HIEU_CAN_NGUOI & tin_hieu:
            return self._dung_can_nguoi(ho_so, url_novnc)
        if "da_dang_nhap" in tin_hieu:
            self._dat_tien_do(ho_so, giai_doan="xong", buoc=BUOC_BAM_GOOGLE,
                              cho_2fa=False, ma_loi="")
            return KetQuaDangNhapGoogle(True, buoc=BUOC_BAM_GOOGLE)
        self._dat_tien_do(ho_so, giai_doan="hong", cho_2fa=False,
                          ma_loi="ma_2fa_sai")
        return KetQuaDangNhapGoogle(False, buoc=BUOC_CHO_PHIEN,
                                    ma_loi="ma_2fa_sai")

    async def nhap_ma_2fa(self, ho_so: str, ma: BiMatTam, trinh_duyet,
                          url_novnc: str | None = None
                          ) -> KetQuaDangNhapGoogle:
        """Gửi mã 2FA khi tiến độ đang ở trạng thái chờ (bước 3)."""
        ho_so = str(ho_so or "")
        tien_do = self.tien_do(ho_so)
        if not tien_do.get("cho_2fa"):
            ma.xoa()
            return KetQuaDangNhapGoogle(False, ma_loi="khong_cho_2fa",
                                        huong_dan="Hồ sơ này không ở trạng thái "
                                                  "chờ mã 2FA.")
        if trinh_duyet is None:
            ma.xoa()
            return _can_nguoi("chua_co_trinh_duyet",
                              "Chưa có bộ điều khiển trình duyệt cho hồ sơ này.",
                              url_novnc)
        try:
            return await self._nhap_ma_2fa_noi_bo(ho_so, ma, trinh_duyet,
                                                 url_novnc)
        except Exception as e:
            log.warning("ho-so-google: nhập mã 2FA lỗi ở hồ sơ %s: %s",
                        ho_so, type(e).__name__)
            return KetQuaDangNhapGoogle(False, ma_loi="loi_module")
