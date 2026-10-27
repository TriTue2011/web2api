"""Module gemini-web: trò chuyện với Gemini bản web (gemini.google.com).

Xác thực bằng cookie Google: người dùng DÁN cookie (trường ``cookies``), đăng
nhập bằng MẬT KHẨU qua trình duyệt (đường phụ NV89), hoặc TÁI DÙNG hồ sơ
Google đã đăng nhập (``tai_dung_ho_so``, NV93 — đường chính). Hỗ trợ nhiều tài
khoản, model theo từng TÀI KHOẢN (NV91-P3): /v1/models là hợp các tài khoản,
định tuyến chỉ chọn tài khoản có model, thiếu → lỗi rõ 400/404.

Luồng giao tiếp (học từ kho tham khảo chatgpt2api/api/gemini_web.py, qua gói
gemini_webapi): init lấy mã SNlM0e → POST batchexecute → bóc khung đáp án.
Xem client.py để biết chi tiết giao thức. CHƯA KIỂM với dịch vụ thật.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time

from app import model_bo_sung
from app import tu_lam_moi
from app.kho import che
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach, KetQuaDangNhap
from app.tu_xa import kiem_url_lan

from .client import (
    GeminiWebClient,
    LoiGeminiWeb,
    LoiHanMuc,
    LoiHetGio,
    LoiMayChu,
    LoiXacThuc,
    MAY_CHU_MAC_DINH,
    MO_HINH_HO_TRO,
    MO_HINH_ON_DINH,
    ModelKhongCo,
    TOI_DA_KY_TU_PROMPT,
    nhan_model,
)
from .converter import (
    TIEN_TO,
    LocToolCallStream,
    chunk_delta,
    chunk_delta_tool_calls,
    chunk_ket_thuc,
    chunk_loi,
    chunk_vai_tro,
    chuyen_thanh_prompt,
    dong_done,
    giai_quyet_model_day_du,
    id_day_du,
    kiem_tra_json_schema,
    phan_hoi_json,
    tach_tien_to,
    tao_hoan_thanh_id,
    trich_xuat_json,
    trich_xuat_tool_calls,
)

log = logging.getLogger("web2api.module.gemini-web")


def _tach_psid(chuoi: str) -> tuple[str | None, str | None]:
    """'k1=v1; k2=v2' → (__Secure-1PSID, __Secure-1PSIDTS|None)."""
    psid = psidts = None
    for phan in str(chuoi or "").split(";"):
        phan = phan.strip()
        if not phan or "=" not in phan:
            continue
        khoa, _, gia_tri = phan.partition("=")
        khoa = khoa.strip()
        gia_tri = gia_tri.strip().strip('"')
        if khoa == "__Secure-1PSID" and gia_tri:
            psid = gia_tri
        elif khoa == "__Secure-1PSIDTS" and gia_tri:
            psidts = gia_tri
    return psid, psidts


def _hash_cookie(chuoi: str) -> str:
    """Dấu vân tay ổn định của một bộ cookie — khoá để nhớ liên kết danh tính
    qua các lần nạp mà không phải lưu cookie hai nơi."""
    return hashlib.sha256(str(chuoi or "").strip().encode("utf-8")).hexdigest()


class _TaiKhoan:
    """Một bộ cookie: client tạo lười, trạng thái sức khoẻ do module giữ.

    NV93: thêm email/ho_so/goi/trang_thai/lan_dung_cuoi/model (model theo
    TÀI KHOẢN — khám phá từ trang chủ, NV91-P3).
    """

    def __init__(self, psid: str, psidts: str | None):
        self.psid = psid
        self.psidts = psidts
        self.client: GeminiWebClient | None = None
        self.khoe = True
        self.loai_loi: str | None = None  # khoa_hong | het_han_muc | may_chu_loi | None
        self.lan_429_cuoi: str | None = None
        self.chuoi_goc: str = ""
        # -- NV93 ------------------------------------------------------------
        self.email: str = ""
        self.ho_so: str | None = None
        self.goi: str = "khong_ro"
        self.trang_thai: str = "hoat_dong"
        self.lan_dung_cuoi: str | None = None
        # Mã ổn định tài khoản này dùng được; chưa khám phá → giả định bảng đầy
        # đủ (tinh chỉnh khi kiểm qua cai()/tai_dung_ho_so).
        self.model: list[str] = list(MO_HINH_HO_TRO)

    def ten_che(self) -> str:
        return che(self.psid)

    def ban_ghi(self, khoa: str) -> dict:
        """Bản ghi làm giàu lưu kho riêng (KHÔNG bí mật thô — psid chỉ có ở
        kho mã hoá của cấu hình; đây chỉ giữ siêu dữ liệu)."""
        return {
            "khoa": khoa,
            "email": self.email,
            "ho_so": self.ho_so,
            "goi": self.goi,
            "trang_thai": self.trang_thai,
            "lan_dung_cuoi": self.lan_dung_cuoi,
            "model": list(self.model),
        }

    def ap_dung_ban_ghi(self, ban_ghi: dict | None) -> None:
        if not isinstance(ban_ghi, dict):
            return
        for truong in ("email", "goi", "trang_thai", "lan_dung_cuoi"):
            gia_tri = ban_ghi.get(truong)
            if isinstance(gia_tri, str) and gia_tri:
                setattr(self, truong, gia_tri)
        hs = ban_ghi.get("ho_so")
        if isinstance(hs, str) and hs:
            self.ho_so = hs
        md = ban_ghi.get("model")
        if isinstance(md, list):
            self.model = [str(m) for m in md if isinstance(m, str) and m]


def _gio_utc() -> str:
    """Giờ hiện tại UTC dạng ISO-8601 gọn (cho lan_dung_cuoi)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_QUY_TAC_TIN_HIEU = [{'loai': 'cookie', 'mien': 'google.com', 'ten': '__Secure-1PSID', 'tin_hieu': 'da_dang_nhap'}, {'loai': 'url_chua', 'gia_tri': 'accounts.google.com', 'tin_hieu': 'chua_dang_nhap'}]

class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None
        # Khoá tài khoản đã đánh dấu hỏng (tầng c bỏ qua khi chọn kế).
        self._hong: set[str] = set()
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV89):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None
        # Trạng thái đăng nhập tự động RIÊNG của module này (không chia sẻ với module khác).
        self._ham_tao_trinh_duyet_rieng = None
        self._khoa_tu_dong = None
        self._captcha_den_het = 0.0
        self._captcha_den_khoa = None
        self._captcha_hoan_giay = 1800.0
        self._lan_t2 = {}
        self._t2_gian_cach_giay = 0.0

    # -- tạo client: mối nối để test chích máy chủ giả ---------------------

    def _tao_client(self, psid: str, psidts: str | None) -> GeminiWebClient:
        return GeminiWebClient(psid, psidts,
                               base_url=self._co_so_url or MAY_CHU_MAC_DINH)

    # -- đăng nhập tự động + khôi phục nhiều tầng (NV89) -----------------------
    # Sao chép hợp đồng của chatgpt-web (NV88): dang_nhap(DongDaTach),
    # lam_moi_phien (tầng a), dang_nhap_tu_dong (tầng b), tai_khoan_ke/
    # danh_dau_hong (tầng c), mat_khau_da_luu, co_tai_khoan, ho_so_trinh_duyet.
    # CHƯA KIỂM với dịch vụ thật: luồng đăng nhập Google tay trên web thật có
    # thể cần thêm bước (chọn tài khoản, nhập số điện thoại, ...) mà chỉ kiểm
    # thật mới thấy.

    @staticmethod
    def _khoa_tu_token(psid: str) -> str:
        """Khoá mờ ổn định của một tài khoản: «tk-» + 12 hex SHA-256(psid).

        Hợp đồng NV93: khoá mọi module web đều dạng ``tk-<băm>``.
        """
        return "tk-" + hashlib.sha256(str(psid or "").encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _khoa_tu_chuoi(chuoi: str) -> str:
        """Khoá mờ của một chuỗi cookie: tách ``__Secure-1PSID`` rồi băm.

        Ổn định khi ``__Secure-1PSIDTS`` xoay vòng; không tách được psid thì
        băm nguyên chuỗi (vẫn «tk-» + 12 hex).
        """
        psid, _ = _tach_psid(chuoi or "")
        return Module._khoa_tu_token(psid or str(chuoi or "").strip())

    def _tim_tai_khoan(self, khoa: str) -> _TaiKhoan | None:
        """Tìm tài khoản theo khoá mờ (có lock); không thấy → None."""
        with self._khoa:
            for tk in self._tai_khoan:
                if self._khoa_tu_token(tk.psid) == khoa:
                    return tk
        return None

    def _them_chuoi_cookie(self, chuoi: str) -> _TaiKhoan:
        """Tách psid/psidts khỏi chuỗi, thêm vào danh sách nếu chưa có.

        Trả _TaiKhoan tương ứng. Thiếu __Secure-1PSID → ValueError (người gọi
        chỉ gọi sau khi đã đọc được PSID từ trình duyệt hoặc cấu hình đã kiểm).
        """
        psid, psidts = _tach_psid(chuoi)
        if not psid:
            raise ValueError("chuỗi cookie thiếu __Secure-1PSID")
        with self._khoa:
            for tk in self._tai_khoan:
                if tk.chuoi_goc == chuoi:
                    return tk
            tk = _TaiKhoan(psid, psidts)
            tk.chuoi_goc = chuoi
            self._tai_khoan.append(tk)
            return tk

    # -- mật khẩu đã lưu (NV89) ------------------------------------------------

    def mat_khau_da_luu(self, khoa: str) -> BiMatTam | None:
        """Mật khẩu đã lưu cho một khoá/email (kho riêng mã hoá của module)."""
        if self.kho is None:
            return None
        try:
            ds = self.kho.doc("mat_khau") or {}
        except Exception:
            return None
        mk = ds.get(str(khoa or "").strip().lower()) if isinstance(ds, dict) else None
        return BiMatTam(mk) if isinstance(mk, str) and mk else None

    def _luu_mat_khau(self, khoa: str, email: str, mat_khau: BiMatTam) -> None:
        """Lưu mật khẩu theo cả khoá-chuỗi-cookie và email (dang_nhap_lai chỉ có email)."""
        if self.kho is None:
            return
        try:
            ds = self.kho.doc("mat_khau") or {}
            if not isinstance(ds, dict):
                ds = {}
            gia_tri = mat_khau.lay()
            ds[str(khoa).strip().lower()] = gia_tri
            if email:
                ds[str(email).strip().lower()] = gia_tri
            self.kho.luu("mat_khau", ds)
        except Exception as e:
            log.warning("gemini-web: không lưu được mật khẩu: %s", type(e).__name__)

    def co_tai_khoan(self, khoa: str) -> bool:
        """Module có biết khoá/email này không?"""
        khoa = str(khoa or "").strip().lower()
        if self._tim_tai_khoan(khoa) is not None:
            return True
        return self.mat_khau_da_luu(khoa) is not None


    # -- đăng nhập tự động ĐỘC LẬP trong module này (sửa 2026-10-27, bản module) --
    # Kiến trúc, ghi rõ để không bị hiểu nhầm thành logic lõi:
    # - web2api (app/) chỉ là phiên dịch/chuyển tiếp: nó gọi
    #   module.khoi_phuc_tu_dong() nếu module có, rồi dịch kết quả sang
    #   phiên MCP, không tự quyết tầng, khóa hay hoãn captcha.
    # - Mọi quyết định đăng nhập của module này sống Ở ĐÂY, trong chính
    #   file module này, không import logic của module kia, không dùng
    #   logic khôi phục chung của lõi. Bản này có thể trông giống bản
    #   của module kia, nhưng đó là hai bản riêng, độc lập: hỏng module
    #   này không làm hỏng module kia, sửa module này không động module kia.
    # - Dịch vụ máy đăng nhập (dich-vu/may-dang-nhap, BrowserPool/solver)
    #   chỉ cung cấp bộ điều khiển trình duyệt qua factory đã đăng ký;
    #   nó không quyết tầng T0-T3 của module này.
    # - Tầng (a) của module này chỉ KIỂM phiên, không bịa endpoint làm mới:
    #   chatgpt-web gọi /backend-api/me, gemini-web kiểm cookie PSID.
    #   Nhà cung cấp nào có đường refresh-token thật thì mới dùng đường đó,
    #   còn hai module web này thì đường cứu chính vẫn là T2 trình duyệt.

    def dang_ky_trinh_duyet(self, ham_tao) -> None:
        """Đăng ký factory bộ điều khiển trình duyệt RIÊNG cho module này.

        Chủ máy gọi hàm này trên chính thể hiện module (không qua lõi):
        ``module.dang_ky_trinh_duyet(lambda: controller)``. Factory có thể
        trả controller trực tiếp hoặc coroutine. Chưa đăng ký -> None.
        """
        self._ham_tao_trinh_duyet_rieng = ham_tao

    async def _lay_trinh_duyet_module(self):
        """Lấy controller: _tao_trinh_duyet (test) trước, rồi factory đã đăng ký.

        Lỗi ở factory -> None (fail-closed), module trả trinh_duyet_none.
        """
        tao = getattr(self, "_tao_trinh_duyet", None)
        td = tao() if callable(tao) else None
        if td is not None:
            return td
        ham = getattr(self, "_ham_tao_trinh_duyet_rieng", None)
        if not callable(ham):
            return None
        try:
            td = ham()
            if asyncio.iscoroutine(td):
                td = await td
            return td
        except Exception as _loi:
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "_lay_trinh_duyet_module", type(_loi).__name__)
            return None

    def totp_da_luu(self, khoa: str):
        """TOTP đã lưu cho một khoá/email, từ kho riêng mã hoá của module này.

        Trả BiMatTotp (mã được tính lúc gọi, không lưu mã), hoặc None nếu
        chưa lưu. Không bao giờ trả secret thô, không log secret.
        """
        if self.kho is None:
            return None
        try:
            ds = self.kho.doc("totp") or {}
        except Exception as _loi:
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "totp_da_luu", type(_loi).__name__)
            return None
        mk = ds.get(str(khoa or "").strip().lower()) if isinstance(ds, dict) else None
        return BiMatTotp(mk) if isinstance(mk, str) and mk else None

    def luu_totp(self, khoa: str, email: str, secret: str) -> bool:
        """Lưu secret TOTP (Base32) vào kho riêng của module này. Sai khuôn -> False.

        Secret chỉ được lưu khi chủ máy gọi tường minh hàm này; module KHÔNG
        tự trích secret từ đối tượng mã TOTP (mã chỉ là mã 6 số, không phải
        secret), và không bao giờ log secret.
        """
        if self.kho is None:
            return False
        s = str(secret or "").strip().upper().replace(" ", "").replace("-", "")
        if not (16 <= len(s) <= 128) or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in s):
            return False
        try:
            ds = self.kho.doc("totp") or {}
            if not isinstance(ds, dict):
                ds = {}
            ds[str(khoa).strip().lower()] = s
            if email:
                ds[str(email).strip().lower()] = s
            self.kho.luu("totp", ds)
            return True
        except Exception as e:
            log.warning("%s: không lưu được TOTP: %s", self.tien_to, type(e).__name__)
            return False

    async def _xu_ly_2fa(self, trinh_duyet, totp) -> str:
        """Kênh TOTP trong module: gặp 2FA mà có TOTP đã lưu thì tự điền mã.

        Trả "" nếu đã điền mã, "can_nguoi" nếu không có TOTP/mã rỗng,
        "loi_module" nếu điền/bấm lỗi. Mã được tính đúng lúc gọi, rồi
        ghi đè biến mã và xoá totp ngay sau khi dùng. Không có TOTP ->
        dừng ở can_nguoi, KHÔNG cố vượt bằng cách khác.
        CAPTCHA/selfie/điện thoại/khóa/tap/code không có TOTP không đi
        qua hàm này, chúng dừng ở can_nguoi ở tầng gọi.
        """
        if totp is None:
            return "can_nguoi"
        try:
            ma = totp.lay() if callable(getattr(totp, "lay", None)) else str(totp)
        except Exception:
            ma = ""
        if not ma:
            try:
                totp.xoa()
            except Exception as _loi:
                log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "_xu_ly_2fa", type(_loi).__name__)
                pass
            return "can_nguoi"
        try:
            await trinh_duyet.dien("ma_xac_minh", ma)
            await trinh_duyet.bam("xac_nhan_2fa")
            return ""
        except Exception as _loi:
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "_xu_ly_2fa", type(_loi).__name__)
            return "loi_module"
        finally:
            ma = None  # noqa: F841
            try:
                totp.xoa()
            except Exception as _loi:
                log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "_xu_ly_2fa", type(_loi).__name__)
                pass

    def _lay_khoa_tu_dong(self):
        """Khóa serial RIÊNG của module này, tạo lười (không gắn vòng lặp lúc import)."""
        if getattr(self, "_khoa_tu_dong", None) is None:
            self._khoa_tu_dong = asyncio.Lock()
        return self._khoa_tu_dong

    def dat_lai_trang_thai_tu_dong(self) -> None:
        """Dùng cho test: xoá khóa serial, cửa sổ hoãn captcha, mốc T2 của module này."""
        self._khoa_tu_dong = None
        self._captcha_den_het = 0.0
        self._captcha_den_khoa = None
        self._lan_t2 = {}

    async def khoi_phuc_tu_dong(self, khoa: str, email: str = "",
                                mat_khau=None, totp=None, trinh_duyet=None,
                                nghi_t1_giay: float = 0.0) -> KetQuaKhoiPhuc:
        """Thang T0-T3 ĐẦY ĐỦ, tự chứa trong module này. Gọi trực tiếp cũng chạy.

        - T0: lam_moi_phien(khoa) — chỉ kiểm, không bịa refresh endpoint.
        - T1: nghỉ nghi_t1_giay rồi kiểm lại (mặc định 0 để test không chờ).
        - T2: khóa serial riêng + hoãn captcha riêng + đăng nhập lại bằng
          mật khẩu và TOTP đã lưu qua trình duyệt (xem _dang_nhap_bang_trinh_duyet).
        - T3: kiểm lại bằng lam_moi_phien; nếu khoa là id ngoài module
          (vd: id kho MCP, không phải khoá nội bộ) mà lam_moi không nhận,
          thì lấy luôn kết quả xác minh phiên của T2 (module đã kiểm token/
          cookie trong lúc đăng nhập), không báo hỏng giả.
        - Tầng (c) vẫn là tai_khoan_ke() riêng, người gọi tự chọn khoá kế;
          khoi_phuc_tu_dong không tự đổi tài khoản âm thầm.
        Trả KetQuaKhoiPhuc với tang T0/T1/T2/T3 và mã riêng, không bí mật.
        """
        khoa = str(khoa or "")
        email = str(email or "")
        mat_khau_dung = mat_khau
        totp_dung = totp
        try:
            # T0
            if khoa:
                try:
                    kq0 = await self.lam_moi_phien(khoa)
                except Exception:
                    kq0 = None
                if kq0 is not None and getattr(kq0, "thanh_cong", False):
                    return KetQuaKhoiPhuc(True, tang="T0")
            # T1
            if nghi_t1_giay and nghi_t1_giay > 0:
                await asyncio.sleep(float(nghi_t1_giay))
                if khoa:
                    try:
                        kq1 = await self.lam_moi_phien(khoa)
                    except Exception:
                        kq1 = None
                    if kq1 is not None and getattr(kq1, "thanh_cong", False):
                        return KetQuaKhoiPhuc(True, tang="T1")
            # T2, trong khóa serial riêng của module này
            async with self._lay_khoa_tu_dong():
                bay_gio = time.time()
                if bay_gio < getattr(self, "_captcha_den_het", 0.0) and khoa != getattr(self, "_captcha_den_khoa", None):
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi="captcha_hoan")
                if mat_khau_dung is None:
                    mat_khau_dung = self.mat_khau_da_luu(khoa) or self.mat_khau_da_luu(email)
                if mat_khau_dung is None:
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi="thieu_mat_khau")
                if totp_dung is None:
                    totp_dung = self.totp_da_luu(khoa) or self.totp_da_luu(email)
                td = trinh_duyet if trinh_duyet is not None else await self._lay_trinh_duyet_module()
                self._trinh_duyet_da_dung = td
                kq2 = await self._dang_nhap_bang_trinh_duyet(email, mat_khau_dung, td, totp=totp_dung)
                self._lan_t2[khoa] = time.time()
                if not kq2.thanh_cong:
                    ma = kq2.ma_loi or "loi_module"
                    if ma in ("captcha", "thu_thach_truoc_khi_dien"):
                        self._captcha_den_het = time.time() + float(getattr(self, "_captcha_hoan_giay", 1800.0))
                        self._captcha_den_khoa = khoa
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi=ma,
                                          ma_trang_thai=kq2.ma_trang_thai,
                                          can_nguoi=(ma in ("can_nguoi", "thu_thach_truoc_khi_dien")))
                # T3
                if khoa:
                    try:
                        kq3 = await self.lam_moi_phien(khoa)
                    except Exception:
                        kq3 = None
                    if kq3 is not None and getattr(kq3, "thanh_cong", False):
                        return KetQuaKhoiPhuc(True, tang="T3")
                # T2 đã tự xác minh phiên trong lúc đăng nhập, nên vẫn tính thành công.
                return KetQuaKhoiPhuc(True, tang="T2")
        finally:
            if mat_khau_dung is not None:
                try:
                    mat_khau_dung.xoa()
                except Exception as _loi:
                    log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "khoi_phuc_tu_dong", type(_loi).__name__)
                    pass
            if totp_dung is not None:
                try:
                    totp_dung.xoa()
                except Exception as _loi:
                    log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "khoi_phuc_tu_dong", type(_loi).__name__)
                    pass

    def quy_tac_tin_hieu(self) -> list:
        """Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): module KHAI, mã tín hiệu MỞ.

        Dịch vụ thực thi: cookie đặc trưng có/không, URL đích — không dò chữ.
        Chỉ NHẬN RA thử thách → can_nguoi (không tự vượt).
        """
        return [dict(q) for q in _QUY_TAC_TIN_HIEU]
    def ho_so_trinh_duyet(self, khoa: str) -> str:
        """ID workspace cho noVNC: đúng khuôn chữ/số/gạch ngang/gạch dưới."""
        bam = hashlib.sha256(str(khoa).encode("utf-8")).hexdigest()[:16]
        return f"gemini-web-{bam}"



    # -- đăng nhập bằng trình duyệt -------------------------------------------

    _URL_DANG_NHAP = ("https://accounts.google.com/signin/v2/identifier"
                      "?flowName=GlifWebSignIn&flowEntry=ServiceLogin")

    async def _kiem_chuoi_cookie(self, chuoi: str) -> tuple[bool, int | None]:
        """Kiểm chuỗi cookie qua client.init(); hỏng → (False, mã trạng thái)."""
        psid, psidts = _tach_psid(chuoi)
        if not psid:
            return False, None
        try:
            await self._tao_client(psid, psidts).init()
        except LoiXacThuc:
            return False, 401
        except LoiGeminiWeb as e:
            return False, e.ma
        except Exception:
            return False, None
        return True, None

    async def _dang_nhap_bang_trinh_duyet(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet, totp=None) -> KetQuaDangNhap:
        """Bước đăng nhập DUY NHẤT của module: điền form Google, đọc
        __Secure-1PSID/__Secure-1PSIDTS từ cookie, kiểm phiên bằng init().

        CAPTCHA/2FA/xác minh thiết bị → dừng với can_nguoi, KHÔNG cố vượt,
        không né phát hiện bot. Kiểm phiên bằng mã trạng thái/cấu trúc, không
        dò chữ trong thân phản hồi. CHƯA KIỂM với trang đăng nhập Google thật.
        """
        # Mã lỗi RIÊNG của module này (không chìm thành can_nguoi):
        # trinh_duyet_none (chưa nối controller), het_gio (hết thời gian),
        # thu_thach_truoc_khi_dien (trang đã có thử thách trước khi điền).
        # 2FA chỉ là thu_thach khi KHÔNG có TOTP; có TOTP thì đi kênh TOTP
        # của module này (xem _xu_ly_2fa), không dừng ở can_nguoi.
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="trinh_duyet_none")
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
            tin_hieu = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except (TimeoutError, asyncio.TimeoutError):
            return KetQuaDangNhap(False, ma_loi="het_gio")
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if "captcha" in tin_hieu or "xac_minh" in tin_hieu or "cloudflare" in tin_hieu:
            # CAPTCHA/xác minh thiết bị: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="thu_thach_truoc_khi_dien")
        totp_dung = totp
        if "2fa" in tin_hieu:
            if totp_dung is None:
                totp_dung = self.totp_da_luu(email)
            if totp_dung is None:
                return KetQuaDangNhap(False, ma_loi="thu_thach_truoc_khi_dien")
        mat_khau_tho = mat_khau.lay()
        try:
            await trinh_duyet.dien("email", email)
            await trinh_duyet.dien("mat_khau", mat_khau_tho)
            await trinh_duyet.bam("dang_nhap")
        finally:
            mat_khau_tho = None  # noqa: F841
        # Sau mật khẩu, nếu trang hiện 2FA: kênh TOTP của module này.
        try:
            tin_hieu_sau = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except Exception:
            tin_hieu_sau = set()
        if "2fa" in tin_hieu_sau:
            if totp_dung is None:
                totp_dung = self.totp_da_luu(email)
            ma_2fa = await self._xu_ly_2fa(trinh_duyet, totp_dung)
            totp_dung = None
            if ma_2fa:
                return KetQuaDangNhap(False, ma_loi=ma_2fa)
        if "captcha" in tin_hieu_sau or "xac_minh" in tin_hieu_sau or "cloudflare" in tin_hieu_sau:
            return KetQuaDangNhap(False, ma_loi="thu_thach_truoc_khi_dien")
        try:
            cookie = await trinh_duyet.doc_cookie("google.com")
        except Exception:
            # Không đọc được cookie thì coi như chưa lấy được phiên (fail-closed).
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        psid = cookie.get("__Secure-1PSID") if isinstance(cookie, dict) else None
        psidts = cookie.get("__Secure-1PSIDTS") if isinstance(cookie, dict) else None
        if not psid:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        chuoi = f"__Secure-1PSID={psid}"
        if psidts:
            chuoi += f"; __Secure-1PSIDTS={psidts}"
        try:
            await self._tao_client(psid, psidts).init()
        except LoiXacThuc:
            return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                  ma_trang_thai=401)
        except LoiGeminiWeb as e:
            return KetQuaDangNhap(False, ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_mang")
        self._them_chuoi_cookie(chuoi)
        khoa_moi = self._khoa_tu_token(psid)
        self._luu_mat_khau(khoa_moi, email, mat_khau)
        return KetQuaDangNhap(True, cookie=chuoi)

    async def dang_nhap(self, tai_khoan: DongDaTach) -> KetQuaDangNhap:
        """Hợp đồng NV67: cookie → kiểm; mat_khau → trình duyệt; dang_nhap_lai →
        mật khẩu đã lưu. BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        try:
            if loai == "cookie":
                chuoi = tai_khoan.cookie.lay() if tai_khoan.cookie else ""
                if not chuoi:
                    return KetQuaDangNhap(False, ma_loi="thieu_cookie")
                ok, ma = await self._kiem_chuoi_cookie(chuoi)
                if not ok:
                    return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                          ma_trang_thai=ma or 401)
                self._them_chuoi_cookie(chuoi)
                return KetQuaDangNhap(True, cookie=chuoi)
            if loai == "mat_khau":
                if tai_khoan.mat_khau is None:
                    return KetQuaDangNhap(False, ma_loi="thieu_mat_khau")
                td = await self._lay_trinh_duyet_module()
                self._trinh_duyet_da_dung = td
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, tai_khoan.mat_khau, td, totp=tai_khoan.totp)
            if loai == "dang_nhap_lai":
                bm = self.mat_khau_da_luu(tai_khoan.email)
                if bm is None:
                    return KetQuaDangNhap(False, ma_loi="khong_co_mat_khau_luu")
                td = await self._lay_trinh_duyet_module()
                self._trinh_duyet_da_dung = td
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, bm, td)
            return KetQuaDangNhap(False, ma_loi="loai_khong_ho_tro")
        finally:
            tai_khoan.xoa_bi_mat()

    # -- tái dùng hồ sơ Google (NV93 — đường chính) -----------------------------

    _URL_TAI_DUNG = "https://gemini.google.com/"
    # NV94-c: chỉ CSS chuẩn (Marionette không hiểu :has-text của Playwright).
    # Đo trên trang khách THẬT (18/10/2026): «Sign in» là <a href="#" data-test-id="sidenav-error-action-link"> (không có href tới accounts.google.com);
    # bấm vào thì chuyển tới accounts.google.com/v3/signin/identifier.
    _BO_CHON_DANG_NHAP = 'a[data-test-id="sidenav-error-action-link"], a[href*="accounts.google.com"]'
    _BO_CHON_GOOGLE = '[data-identifier]'
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare", "thu_thach_truy_cap"}

    @staticmethod
    def _suy_goi(models: list[str]) -> str:
        """Suy gói từ CẤU TRÚC danh sách model (NV91-P3).

        CHƯA KIỂM trên dịch vụ thật: chỉ là phỏng đoán (tài khoản thấy model
        pro → coi là pro); Google đổi danh sách thì suy sai — ghi rõ để không
        ai coi đây là sự thật đã đo.
        """
        return "pro" if "gemini-3-pro" in (models or []) else "free"

    async def _kiem_va_kham_pha(self, psid: str, psidts: str | None):
        """Kiểm phiên + khám phá model theo tài khoản.

        Trả (ok, models, ma_loi): ok=False → ma_loi rõ (phien_khong_dung_duoc
        / loi_may_chu / loi_mang). Không bóc được danh sách mà phiên còn sống
        → dùng bảng mặc định (không đoán gói).
        """
        try:
            models = await self._tao_client(psid, psidts).lay_model_kha_dung()
        except LoiXacThuc:
            return False, [], "phien_khong_dung_duoc"
        except LoiGeminiWeb:
            return False, [], "loi_may_chu"
        except Exception:
            return False, [], "loi_mang"
        if not models:
            models = list(MO_HINH_HO_TRO)
        return True, models, ""

    async def tai_dung_ho_so(self, ho_so, trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập bằng Google TRONG hồ sơ đã đăng nhập Google (NV93).

        Hợp đồng NV92-P3: module tự làm bước «Đăng nhập bằng Google» của nó
        trong hồ sơ, lấy cookie __Secure-1PSID/__Secure-1PSIDTS, kiểm phiên +
        khám phá model theo tài khoản, làm giàu email/gói/hồ sơ, thêm tài khoản
        vào kho của CHÍNH mình, trả ``KetQuaDangNhap`` với ``cookie`` là chuỗi
        cookie thô (route tái dùng lưu nó qua đường cai() có sẵn). Không nhập
        lại email/mật khẩu/2FA — KHÔNG BAO GIỜ ``dien()``. Gặp
        CAPTCHA/Cloudflare/2FA/xác minh → can_nguoi NGAY (không cố vượt).

        HỌC TỪ c2a (chỉ đọc lấy hiểu, tự viết lại): TriTue2011/chatgpt2api,
        ``captcha-solver/src/gemini_web_login.py`` — các bước: mở gemini.google.com
        → bấm Đăng nhập → chọn tài khoản Google trong hồ sơ (SSO) → chờ trang
        đã đăng nhập → đọc cookie → kiểm bằng trang chủ.
        """
        hs_id = ho_so.get("id") if isinstance(ho_so, dict) else str(ho_so or "")
        hs_id = str(hs_id or "").strip() or None
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.mo_trang(self._URL_TAI_DUNG)
            tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
            # CAPTCHA/Cloudflare/2FA/xác minh thiết bị: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            if "da_dang_nhap" not in tin_hieu:
                # Bước «bấm Google»: chọn tài khoản Google đã đăng nhập trong hồ sơ.
                await trinh_duyet.bam(self._BO_CHON_DANG_NHAP)
                await trinh_duyet.bam(self._BO_CHON_GOOGLE)
                # Bước «chờ phiên»: SSO chuyển về trang đã đăng nhập.
                da_vao = False
                for _ in range(20):
                    tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
                    if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                        return KetQuaDangNhap(False, ma_loi="can_nguoi")
                    if "da_dang_nhap" in tin_hieu:
                        da_vao = True
                        break
                    await asyncio.sleep(1)
                if not da_vao:
                    return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
            try:
                cookie = await trinh_duyet.doc_cookie("gemini.google.com")
            except Exception:
                cookie = {}
            psid = cookie.get("__Secure-1PSID") if isinstance(cookie, dict) else None
            psidts = cookie.get("__Secure-1PSIDTS") if isinstance(cookie, dict) else None
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if not psid:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        ok, models, ma_loi = await self._kiem_va_kham_pha(psid, psidts)
        if not ok:
            return KetQuaDangNhap(False, ma_loi=ma_loi,
                                  ma_trang_thai=401
                                  if ma_loi == "phien_khong_dung_duoc" else None)
        chuoi = "__Secure-1PSID=" + psid
        if psidts:
            chuoi += "; __Secure-1PSIDTS=" + psidts
        goi = self._suy_goi(models)
        email = str(ho_so.get("email") or "") if isinstance(ho_so, dict) else ""
        await self._them_tai_khoan_tu_ho_so(chuoi, psid, psidts, hs_id, email,
                                            goi, models)
        # Log chỉ khoá mờ — KHÔNG BAO GIỜ log psid/psidts.
        log.info("gemini-web: tái dùng hồ sơ %s → tài khoản %s (gói %s)",
                 hs_id, self._khoa_tu_token(psid), goi)
        return KetQuaDangNhap(True, cookie=chuoi)

    async def _them_tai_khoan_tu_ho_so(self, chuoi: str, psid: str,
                                       psidts: str | None, hs_id: str | None,
                                       email: str, goi: str,
                                       models: list[str]) -> _TaiKhoan:
        """Thêm/cập nhật bản ghi từ phiên tái dùng hồ sơ (chống trùng psid)."""
        with self._khoa:
            tk = next((t for t in self._tai_khoan if t.psid == psid), None)
            if tk is None:
                tk = _TaiKhoan(psid, psidts)
                tk.chuoi_goc = chuoi
                self._tai_khoan.append(tk)
            else:
                tk.psidts = psidts or tk.psidts
                tk.chuoi_goc = chuoi
            if email:
                tk.email = email
            if hs_id:
                tk.ho_so = hs_id
            tk.goi = goi or "khong_ro"
            tk.trang_thai = "hoat_dong"
            tk.khoe = True
            tk.loai_loi = None
            tk.model = list(models or [])
            tk.lan_dung_cuoi = _gio_utc()
        self._xoa_khoi_go_bo(self._khoa_tu_token(psid))
        self._luu_ban_ghi_tai_khoan()
        return tk

    # -- liệt kê / gỡ tài khoản (NV93) ------------------------------------------

    def liet_ke_tai_khoan(self) -> list[dict]:
        """Danh sách tài khoản KHÔNG bí mật: khoa/email/ho_so/goi/trạng thái."""
        with self._khoa:
            ds = list(self._tai_khoan)
        return [{
            "khoa": self._khoa_tu_token(tk.psid),
            "email": tk.email,
            "ho_so": tk.ho_so,
            "goi": tk.goi,
            "trang_thai": tk.trang_thai,
            "lan_dung_cuoi": tk.lan_dung_cuoi,
        } for tk in ds]

    def go_tai_khoan(self, khoa: str) -> bool:
        """Gỡ đúng MỘT tài khoản của module này; ghi sổ gỡ bỏ vào kho riêng để
        khởi động lại không dựng lại. Không động module khác, không động hồ sơ."""
        khoa = str(khoa or "")
        with self._khoa:
            con_lai = [tk for tk in self._tai_khoan
                       if self._khoa_tu_token(tk.psid) != khoa]
            da_go = len(con_lai) != len(self._tai_khoan)
            self._tai_khoan = con_lai
        if da_go:
            tap = self._doc_go_bo()
            tap.add(khoa)
            self._luu_go_bo(tap)
            self._luu_ban_ghi_tai_khoan()
            log.info("gemini-web: đã gỡ tài khoản %s", khoa)
        return da_go

    # -- kho riêng: bản ghi làm giàu + sổ gỡ bỏ (NV93) -------------------------

    def _doc_ban_ghi(self) -> dict:
        """{khoa: bản ghi} từ kho riêng; kho chưa gán/hỏng → {} (không vỡ)."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("tai_khoan") or {}
        except Exception as e:
            log.warning("gemini-web: không đọc được bản ghi tài khoản: %s",
                        type(e).__name__)
            return {}
        ban_ghi = du_lieu.get("ban_ghi") if isinstance(du_lieu, dict) else None
        return ban_ghi if isinstance(ban_ghi, dict) else {}

    def _luu_ban_ghi_tai_khoan(self) -> None:
        """Lưu bản ghi làm giàu (không bí mật thô). Không vỡ."""
        if self.kho is None:
            return
        try:
            with self._khoa:
                ban_ghi = {self._khoa_tu_token(tk.psid): tk.ban_ghi(self._khoa_tu_token(tk.psid))
                           for tk in self._tai_khoan}
            self.kho.luu("tai_khoan", {"ban_ghi": ban_ghi})
        except Exception as e:
            log.warning("gemini-web: không lưu được bản ghi tài khoản: %s",
                        type(e).__name__)

    def _doc_go_bo(self) -> set:
        """Tập khoá đã bị go_tai_khoan gỡ (để nap() không dựng lại)."""
        if self.kho is None:
            return set()
        try:
            du_lieu = self.kho.doc("go_bo") or {}
        except Exception:
            return set()
        ds = du_lieu.get("khoa") if isinstance(du_lieu, dict) else None
        return {str(k) for k in ds} if isinstance(ds, list) else set()

    def _luu_go_bo(self, tap: set) -> None:
        if self.kho is None:
            return
        try:
            self.kho.luu("go_bo", {"khoa": sorted(tap)})
        except Exception as e:
            log.warning("gemini-web: không lưu được sổ gỡ bỏ: %s",
                        type(e).__name__)

    def _xoa_khoi_go_bo(self, khoa: str) -> None:
        tap = self._doc_go_bo()
        if khoa in tap:
            tap.discard(khoa)
            self._luu_go_bo(tap)

    # -- các tầng khôi phục ---------------------------------------------------

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a): gemini.google.com không có endpoint làm mới cookie công
        khai — tầng này chỉ kiểm cookie còn sống bằng init()."""
        tk = self._tim_tai_khoan(khoa)
        if tk is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        try:
            await self._tao_client(tk.psid, tk.psidts).init()
        except LoiXacThuc:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han",
                                  ma_trang_thai=401)
        except LoiGeminiWeb as e:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_mang")
        return KetQuaKhoiPhuc(True, tang="a")

    async def dang_nhap_tu_dong(self, tai_khoan: TaiKhoanCanKhoiPhuc,
                                trinh_duyet, totp=None) -> KetQuaKhoiPhuc:
        """Tầng (b): đăng nhập lại bằng mật khẩu đã lưu qua trình duyệt.

        Tầng này nay cũng đi qua kênh TOTP của module (totp truyền vào, hoặc
        totp đã lưu nếu không truyền), và dùng controller đã đăng ký riêng
        cho module này nếu trinh_duyet không được truyền vào. Mã lỗi riêng
        của module được giữ nguyên, không gộp thành can_nguoi.
        """
        try:
            bm = tai_khoan.mat_khau
            if bm is None:
                return KetQuaKhoiPhuc(False, tang="b", ma_loi="thieu_mat_khau")
            td = trinh_duyet if trinh_duyet is not None else await self._lay_trinh_duyet_module()
            kq = await self._dang_nhap_bang_trinh_duyet(
                tai_khoan.email, bm, td, totp=totp)
            if kq.thanh_cong:
                return KetQuaKhoiPhuc(True, tang="b")
            return KetQuaKhoiPhuc(False, tang="b", ma_loi=kq.ma_loi or "loi_module",
                                  ma_trang_thai=kq.ma_trang_thai,
                                  can_nguoi=(kq.ma_loi in ("can_nguoi", "thu_thach_truoc_khi_dien")))
        finally:
            if tai_khoan.mat_khau is not None:
                tai_khoan.mat_khau.xoa()
            if totp is not None:
                try:
                    totp.xoa()
                except Exception as _loi:
                    log.warning("%s: %s bỏ qua lỗi kiểu %s", self.tien_to, "dang_nhap_tu_dong", type(_loi).__name__)
                    pass

    def tai_khoan_ke(self, khoa: str) -> str | None:
        """Tầng (c): khoá kế còn khoẻ (chưa bị đánh dấu hỏng); hết → None."""
        with self._khoa:
            ds = [self._khoa_tu_token(tk.psid) for tk in self._tai_khoan]
        khoa = str(khoa)
        con_khoe = [k for k in ds if k != khoa and k not in self._hong]
        if con_khoe:
            return con_khoe[0]
        # Chỉ còn chính nó và nó khoẻ → không có «kế».
        return None

    def danh_dau_hong(self, khoa: str, ma_loi: str) -> None:
        """Đánh dấu tài khoản hỏng để tầng (c) bỏ qua."""
        self._hong.add(str(khoa))

    def khoa_tai_khoan(self) -> list[str]:
        """NV91-P2: khoá mờ mọi tài khoản (vòng làm mới định kỳ của lõi liệt kê)."""
        return [self._khoa_tu_chuoi(tk.chuoi_goc) for tk in self._tai_khoan]

    def danh_dau_khoe(self, khoa: str) -> None:
        """NV91-P2: tài khoản khoẻ lại sau tự khôi phục → bỏ đánh dấu hỏng."""
        self._hong.discard(str(khoa))

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách cookie.

        """
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("cookies") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        if isinstance(danh_sach, dict):
            raise ValueError("«cookies» phải là danh sách chuỗi cookie.")
        go_bo = self._doc_go_bo()
        ban_ghi = self._doc_ban_ghi()
        tai_khoan = []
        for i, chuoi in enumerate(danh_sach):
            psid, psidts = _tach_psid(chuoi)
            if not psid:
                log.warning("gemini-web: bỏ qua một bộ cookie thiếu __Secure-1PSID")
                continue
            khoa = self._khoa_tu_token(psid)
            if khoa in go_bo:
                continue  # đã bị go_tai_khoan gỡ tường minh — không dựng lại
            tk = _TaiKhoan(psid, psidts)
            tk.chuoi_goc = chuoi if isinstance(chuoi, str) else ""
            tk.ap_dung_ban_ghi(ban_ghi.get(khoa))
            tai_khoan.append(tk)
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._co_so_url = co_so
        log.info("gemini-web: đã nạp %d tài khoản", len(tai_khoan))

    async def _kiem_co_so_url(self) -> None:
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                raise ValueError(f"co_so_url không hợp lệ: {e}") from None

    async def _init_tai_khoan(self, tk: _TaiKhoan) -> None:
        """Kiểm phiên + khám phá model theo tài khoản (NV91-P3) + suy gói.

        Không mạng/không đọc được → tài khoản vẫn dùng được với model đã biết
        (hoặc bảng mặc định), gói "khong_ro". Không bao giờ lộ cookie trong log.
        """
        if tk.client is None:
            tk.client = self._tao_client(tk.psid, tk.psidts)
        await tk.client.init()
        try:
            models = await tk.client.lay_model_kha_dung()
        except LoiGeminiWeb:
            models = []
        except Exception:
            models = []
        if models:
            tk.model = list(models)
        elif not tk.model:
            tk.model = list(MO_HINH_HO_TRO)
        if not tk.goi or tk.goi == "khong_ro":
            tk.goi = self._suy_goi(tk.model) if models else tk.goi
        tk.trang_thai = "hoat_dong"
        self._luu_ban_ghi_tai_khoan()

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng init() (rẻ, không tốn lượt chat)."""
        self.nap(cau_hinh)
        try:
            await self._kiem_co_so_url()
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập cookie nào (trường cookies)."}
        ket_qua = []
        tot = []
        for i, tk in enumerate(ds):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc:
                tk.khoe = False
                tk.loai_loi = "khoa_hong"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": "Cookie bị từ chối (401/403) — export lại cookie mới."})
                # Không log cookie: chỉ ghi dạng che.
                log.warning("gemini-web: tài khoản %s bị từ chối ở bước cài", tk.ten_che())
            except LoiGeminiWeb as e:
                tk.khoe = False
                tk.loai_loi = "may_chu_loi"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": f"Tạm thời không kiểm được: {e}"})
            except Exception as e:  # không để ngoại lệ lạ vỡ bước cài
                tk.khoe = False
                tk.loai_loi = "may_chu_loi"
                ket_qua.append({"khoa": tk.ten_che(), "ok": False,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": f"Lỗi không ngờ: {type(e).__name__}"})
                log.warning("gemini-web: tài khoản %s lỗi không ngờ ở bước cài: %s",
                            tk.ten_che(), type(e).__name__)
            else:
                tk.khoe = True
                tk.loai_loi = None
                tot.append(i)
                ket_qua.append({"khoa": tk.ten_che(), "ok": True,
                                "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                                "loi": None})
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ket_qua,
                    "loi": "Không tài khoản nào dùng được (xem chi tiết từng khoá)."}
        # Lưu đúng chuỗi gốc của từng tài khoản còn tốt (theo thể hiện, không theo
        # chỉ số — chỉ số lệch khi có bộ cookie bị bỏ qua lúc tách).
        luu = {"cookies": [ds[i].chuoi_goc for i in tot]}
        if self._co_so_url:
            luu["co_so_url"] = self._co_so_url
        return {"ok": True, "ket_qua_tung_khoa": ket_qua, "loi": None,
                "cau_hinh_luu": luu}

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        """Phân loại bằng mã HTTP — không dò chữ trong thông điệp."""
        if ma in (401, 403):
            return "khoa_hong"
        if ma == 429:
            return "het_han_muc"
        if ma == 400:
            return "yeu_cau_sai"
        if 500 <= ma <= 599:
            return "may_chu_loi"
        return None  # để lõi dùng phân loại mặc định

    # -- chat ---------------------------------------------------------------

    def _thu_tu_tai_khoan(self) -> list[_TaiKhoan]:
        """Tài khoản khoẻ trước; tài khoản từng lỗi đứng sau nhưng KHÔNG BAO GIỜ
        bị chặn hẳn — hết lựa chọn khoẻ thì vẫn thử chúng (lựa chọn cuối)."""
        with self._khoa:
            ds = list(self._tai_khoan)
        return sorted(ds, key=lambda t: 0 if t.khoe else 1)

    def _danh_dau_loi(self, tk: _TaiKhoan, loai: str, chi_so: int) -> None:
        tk.khoe = False
        tk.loai_loi = loai
        log.warning("gemini-web: tài khoản #%d (%s) lỗi %s, chuyển tài khoản",
                    chi_so, tk.ten_che(), loai)
        if loai == "khoa_hong":
            # NV91-P2: lỗi phiên nhận ra bằng CẤU TRÚC → tự khôi phục MỘT lần cho
            # đúng tài khoản (single-flight); module vẫn chuyển tài khoản kế ngay.
            tu_lam_moi.bao_loi_phien(self, self._khoa_tu_chuoi(tk.chuoi_goc))

    def _tai_khoan_cho_model(self, ten_model: str) -> list[_TaiKhoan]:
        """Tài khoản khoẻ trước, CHỈ những tài khoản có model này (NV91-P3).

        KHÔNG fallback âm thầm sang model khác — không tài khoản nào có thì
        chat() trả lỗi rõ 404. Tài khoản chưa khám phá model (tk.model rỗng)
        được coi như có bảng mặc định (sẽ tinh chỉnh ở cai()).
        Nếu ten_model nằm trong model_bo_sung thì mọi tài khoản đều được thử.
        """
        ds_bo_sung = {tach_tien_to(b.get("id", "")) for b in model_bo_sung.doc("gemini-web")}
        la_bo_sung = ten_model in ds_bo_sung
        ds = []
        for tk in self._thu_tu_tai_khoan():
            if la_bo_sung:
                ds.append(tk)
                continue
            model_tk = tk.model or list(MO_HINH_HO_TRO)
            if ten_model in model_tk:
                ds.append(tk)
        return ds

    async def chat(self, body: dict, stream: bool):
        body = dict(body or {})
        model_goc = str(body.get("model") or "")
        biet_them = set()
        with self._khoa:
            for tk in self._tai_khoan:
                if tk.model:
                    biet_them.update(tk.model)
        for bo_sung in model_bo_sung.doc("gemini-web"):
            mid = bo_sung.get("id")
            if mid:
                biet_them.add(tach_tien_to(mid))

        try:
            ten_model = giai_quyet_model_day_du(model_goc, biet_them=biet_them)
        except ModelKhongCo:
            cac_model_biet = sorted(set(MO_HINH_HO_TRO) | biet_them)
            thong_diep = (f"model {nhac_lai(tach_tien_to(model_goc))} không có; "
                          f"chọn trong: {', '.join(id_day_du(m) for m in cac_model_biet)}")
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, thong_diep), ensure_ascii=False).encode("utf-8")
        model_id = id_day_du(ten_model)
        prompt = chuyen_thanh_prompt(body)
        if not prompt:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        # NV134: Giới hạn độ dài nội dung prompt (quá trần trả 413 rõ ràng)
        if len(prompt) > TOI_DA_KY_TU_PROMPT:
            thong_diep = (f"nội dung yêu cầu quá dài ({len(prompt)} ký tự, "
                          f"tối đa {TOI_DA_KY_TU_PROMPT} ký tự)")
            return 413, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(413, thong_diep), ensure_ascii=False).encode("utf-8")

        ds = self._tai_khoan_cho_model(ten_model)
        if not ds:
            if not self._thu_tu_tai_khoan():
                # NV272: module đã chạy ở trạng thái chưa có phiên (lõi không
                # còn chặn vì thiếu cookie). Mã rõ can_dang_nhap để đăng nhập
                # tự động có chỗ chạy và tự lấy cookie về.
                return 401, {"Content-Type": "application/json"}, \
                    json.dumps(than_loi(401, "gemini-web chưa có phiên: đăng nhập "
                                        "tự động để lấy cookie Google về rồi gọi lại.",
                                        code="can_dang_nhap"),
                               ensure_ascii=False).encode("utf-8")
            goi_can = "pro" if ten_model == "gemini-3-pro" else "free"
            thong_diep = (f"model {model_id} không có cho tài khoản nào "
                          f"(cần tài khoản gói {goi_can} trở lên)")
            return 404, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(404, thong_diep), ensure_ascii=False).encode("utf-8")

        # Xác định thông tin tools và response_format
        tools = body.get("tools")
        tool_choice = body.get("tool_choice")
        co_tools = bool(tools and tool_choice != "none")
        response_format = body.get("response_format")
        messages = body.get("messages") or []

        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(
                ds, prompt, model_id, ten_model,
                tools=tools if co_tools else None,
            )

        loi_cuoi: LoiGeminiWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)

                van_ban = await tk.client.generate_content(
                    prompt, ten_model_da_giai=ten_model
                )
                # Bóc tách tool_calls nếu có
                if co_tools:
                    van_ban_sach, tool_calls = trich_xuat_tool_calls(van_ban, tools)
                else:
                    van_ban_sach, tool_calls = van_ban, []

                # Xử lý response_format nếu có
                if isinstance(response_format, dict):
                    rf_type = response_format.get("type")
                    if rf_type == "json_object":
                        ok, parsed_json, err_msg = trich_xuat_json(van_ban)
                        if not (ok and isinstance(parsed_json, dict)):
                            # Thử lại tối đa 1 lần (nằm trong try để xoay tài khoản nếu lỗi kết nối/hạn mức)
                            prompt_sua = (
                                f"{prompt}\n\nSystem: Your previous response was not valid JSON. Error: {err_msg}. "
                                "You MUST return ONLY a valid JSON object. Do not include markdown fences or text outside JSON."
                            )
                            van_ban_2 = await tk.client.generate_content(
                                prompt_sua, ten_model_da_giai=ten_model
                            )
                            ok, parsed_json, err_msg = trich_xuat_json(van_ban_2)
                            if not (ok and isinstance(parsed_json, dict)):
                                err_body = {
                                    "error": {
                                        "message": f"Không thể tạo đầu ra JSON hợp lệ sau khi thử lại: {err_msg}",
                                        "type": "server_error",
                                        "code": "structured_output_failed",
                                    }
                                }
                                return 502, {"Content-Type": "application/json"}, \
                                    json.dumps(err_body, ensure_ascii=False).encode("utf-8")
                        van_ban_sach = json.dumps(parsed_json, ensure_ascii=False)

                    elif rf_type == "json_schema":
                        schema_info = response_format.get("json_schema") or {}
                        schema = schema_info.get("schema") or {}
                        ok, parsed_json, err_msg = trich_xuat_json(van_ban)
                        schema_ok = False
                        if ok:
                            schema_ok, err_msg = kiem_tra_json_schema(parsed_json, schema)
                        if not schema_ok:
                            # Thử lại tối đa 1 lần (nằm trong try để xoay tài khoản nếu lỗi kết nối/hạn mức)
                            prompt_sua = (
                                f"{prompt}\n\nSystem: Your previous response did not match the JSON Schema. Error: {err_msg}. "
                                f"You MUST return ONLY a valid JSON object strictly conforming to schema:\n"
                                f"{json.dumps(schema, ensure_ascii=False)}\n"
                                "Do not include markdown fences or text outside JSON."
                            )
                            van_ban_2 = await tk.client.generate_content(
                                prompt_sua, ten_model_da_giai=ten_model
                            )
                            ok, parsed_json, err_msg = trich_xuat_json(van_ban_2)
                            schema_ok = False
                            if ok:
                                schema_ok, err_msg = kiem_tra_json_schema(parsed_json, schema)
                            if not schema_ok:
                                err_body = {
                                    "error": {
                                        "message": f"Không thể tạo đầu ra JSON Schema hợp lệ sau khi thử lại: {err_msg}",
                                        "type": "server_error",
                                        "code": "structured_output_failed",
                                    }
                                }
                                return 502, {"Content-Type": "application/json"}, \
                                    json.dumps(err_body, ensure_ascii=False).encode("utf-8")
                        van_ban_sach = json.dumps(parsed_json, ensure_ascii=False)

                tk.khoe = True
                tk.loai_loi = None
                return 200, {"Content-Type": "application/json"}, \
                    phan_hoi_json(model_id, van_ban_sach, tool_calls=tool_calls if tool_calls else None)

            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                continue
            except LoiHanMuc as e:
                self._danh_dau_loi(tk, "het_han_muc", i)
                loi_cuoi = e
                continue
            except LoiHetGio as e:
                self._danh_dau_loi(tk, "may_chu_loi", i)
                loi_cuoi = e
                continue
            except LoiMayChu as e:
                self._danh_dau_loi(tk, "may_chu_loi", i)
                loi_cuoi = e
                continue
            except Exception as e:  # phòng thủ: không để ngoại lệ lạ thành 500 vỡ lõi
                log.warning("gemini-web: lỗi không ngờ ở tài khoản #%d: %s", i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                continue

        # Hết tài khoản: mã theo lỗi cuối (không bao giờ lộ cookie hay đáp án thô).
        ma = loi_cuoi.ma if loi_cuoi else 502
        thong_diep = {
            401: "Mọi tài khoản gemini-web đều bị từ chối — cookie hết hạn hoặc không hợp lệ, export lại.",
            429: "Mọi tài khoản gemini-web đều bị giới hạn lượt — thử lại sau.",
            504: "gemini.google.com không phản hồi kịp.",
        }.get(ma, "gemini.google.com lỗi phía máy chủ.")
        return ma, {"Content-Type": "application/json"}, \
            json.dumps(than_loi(ma, thong_diep), ensure_ascii=False).encode("utf-8")

    async def _luong_chat(self, ds: list[_TaiKhoan], prompt: str,
                          model_id: str, ten_model: str,
                          tools: list | None = None):
        """SSE OpenAI; xoay tài khoản ngay trong luồng khi tài khoản đầu hỏng."""
        hoan_thanh_id = tao_hoan_thanh_id()
        tao_luc = int(time.time())
        yield chunk_vai_tro(model_id, hoan_thanh_id, tao_luc)
        loi_cuoi: LoiGeminiWeb | None = None
        co_tools = bool(tools)

        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)

                da_ra_du_lieu = False
                loc = LocToolCallStream() if co_tools else None

                async for delta in tk.client.generate_content_stream(
                    prompt, ten_model_da_giai=ten_model
                ):
                    da_ra_du_lieu = True
                    if loc is not None:
                        van_ban_phat = loc.feed(delta)
                        if van_ban_phat:
                            yield chunk_delta(model_id, hoan_thanh_id, tao_luc, van_ban_phat)
                    else:
                        yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)

                tool_calls = []
                van_ban_cuoi = ""
                if loc is not None:
                    duoi = loc.flush()
                    if duoi:
                        yield chunk_delta(model_id, hoan_thanh_id, tao_luc, duoi)
                    van_ban_toan_bo = loc.van_ban_day_du()
                    van_ban_cuoi, tool_calls = trich_xuat_tool_calls(van_ban_toan_bo, tools)
                    da_phat_loc = loc.da_phat()

                    # Trích xuất và phát phần văn bản chưa phát (sau khối ToolCalls hoặc khi cú pháp hỏng)
                    m_block = re.search(r"\[ToolCalls\]([\s\S]*?)\[/ToolCalls\]", van_ban_toan_bo, re.IGNORECASE)
                    if m_block:
                        truoc = van_ban_toan_bo[: m_block.start()]
                        sau = van_ban_toan_bo[m_block.end() :]
                        phan_chua_phat = truoc[len(da_phat_loc) :] + sau
                    else:
                        phan_chua_phat = van_ban_toan_bo[len(da_phat_loc) :]

                    if phan_chua_phat:
                        yield chunk_delta(model_id, hoan_thanh_id, tao_luc, phan_chua_phat)
                else:
                    van_ban_cuoi = ""

                tk.khoe = True
                tk.loai_loi = None

                if tool_calls:
                    yield chunk_delta_tool_calls(model_id, hoan_thanh_id, tao_luc, tool_calls)
                    yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc, finish_reason="tool_calls")
                else:
                    yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc, finish_reason="stop")
                yield dong_done()
                return

            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break  # đã ra dữ liệu thì không trộn hai tài khoản vào một luồng
                continue
            except LoiGeminiWeb as e:
                self._danh_dau_loi(tk, "het_han_muc" if isinstance(e, LoiHanMuc) else "may_chu_loi", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break
                continue
            except Exception as e:
                log.warning("gemini-web: lỗi không ngờ khi stream ở tài khoản #%d: %s",
                            i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                break

        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản gemini-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / lam_moi_model / suc_khoe / dung ----------------------------

    async def lam_moi_model(self) -> None:
        """Lấy lại model từ nguồn thật (trang chủ gemini.google.com) cho từng tài khoản."""
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            raise RuntimeError("gemini-web chưa có tài khoản nào")
        co_thanh_cong = False
        for tk in ds:
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)
                kq = await tk.client.lay_model_kha_dung()
                if kq:
                    tk.model = list(kq)
                    if not tk.goi or tk.goi == "khong_ro":
                        tk.goi = self._suy_goi(tk.model)
                    co_thanh_cong = True
            except Exception as e:
                log.warning("gemini-web: không lấy được model cho %s: %s",
                            tk.ten_che(), type(e).__name__)
        if co_thanh_cong:
            self._luu_ban_ghi_tai_khoan()
        else:
            raise RuntimeError("không lấy được model từ nguồn")

    def _muc_model(self, ma: str, ds_khoa: list[str], ds_goi: list[str], nhan_ghi_de: str | None = None) -> dict:
        """Một mục /v1/models: id ổn định + nhãn tách riêng + gói/tài khoản."""
        goi_can = "pro" if "pro" in ds_goi else ("free" if ds_goi else "khong_ro")
        nhan = nhan_ghi_de or nhan_model(ma)
        return {"id": id_day_du(ma), "object": "model",
                "created": 1760000000, "owned_by": "gemini-web",
                "nhan": nhan, "goi": goi_can,
                "available_cho": list(ds_khoa)}

    async def models(self):
        """Hợp các model theo TÀI KHOẢN (NV91-P3) + model_bo_sung: mỗi model kèm nhãn
        hiển thị, gói tối thiểu và danh sách khoá tài khoản dùng được."""
        with self._khoa:
            ds = list(self._tai_khoan)
        hop: dict[str, dict] = {}
        if not ds:
            for m in MO_HINH_HO_TRO:
                hop[m] = {"khoa": [], "goi": []}
        else:
            for tk in ds:
                khoa = self._khoa_tu_token(tk.psid)
                for m in tk.model or list(MO_HINH_HO_TRO):
                    muc = hop.setdefault(m, {"khoa": [], "goi": []})
                    if khoa not in muc["khoa"]:
                        muc["khoa"].append(khoa)
                        muc["goi"].append(tk.goi)

        # Bổ sung từ model_bo_sung
        nhan_bo_sung: dict[str, str] = {}
        for b in model_bo_sung.doc("gemini-web"):
            mid = b.get("id")
            if not mid:
                continue
            ten = tach_tien_to(mid)
            if b.get("nhan"):
                nhan_bo_sung[ten] = b["nhan"]
            if ten not in hop:
                tat_ca_khoa = [self._khoa_tu_token(tk.psid) for tk in ds]
                tat_ca_goi = [tk.goi for tk in ds]
                hop[ten] = {"khoa": tat_ca_khoa, "goi": tat_ca_goi}

        return [self._muc_model(m, v["khoa"], v["goi"], nhan_ghi_de=nhan_bo_sung.get(m))
                for m, v in hop.items()]

    async def suc_khoe(self):
        with self._khoa:
            ds = list(self._tai_khoan)
        muc = []
        for i, tk in enumerate(ds, start=1):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "dang_nghi_den": None})
            except LoiGeminiWeb as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "dang_nghi_den": None})
            except Exception as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": type(e).__name__, "dang_nghi_den": None})
            else:
                muc.append({"khoa": tk.ten_che(), "ok": True, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": len(MO_HINH_HO_TRO), "loi": None,
                            "dang_nghi_den": None})
        ok = any(m["ok"] for m in muc)
        return {"ok": ok, "khoa": muc,
                "ghi_chu": None if ds else "chưa cấu hình tài khoản nào",
                "tham_so_bo_qua": ["temperature", "top_p", "max_tokens", "parallel_tool_calls", "safety"],
                "chu_thich_tham_so": "các tham số temperature, top_p, max_tokens, parallel_tool_calls, safety không có tác dụng trên giao diện web"}

    async def dung(self):
        with self._khoa:
            ds = list(self._tai_khoan)
            self._tai_khoan = []
        for tk in ds:
            if tk.client is not None:
                try:
                    await tk.client.close()
                except Exception:
                    pass
