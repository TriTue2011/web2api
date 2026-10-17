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
import threading
import time

from app import danh_tinh
from app import tu_lam_moi
from app.kho import che
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap
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
    nhan_model,
)
from .converter import (
    TIEN_TO,
    chunk_delta,
    chunk_ket_thuc,
    chunk_loi,
    chunk_vai_tro,
    chuyen_thanh_prompt,
    dong_done,
    giai_quyet_model_day_du,
    id_day_du,
    phan_hoi_json,
    tach_tien_to,
    tao_hoan_thanh_id,
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


def _che_email(email: str) -> str:
    """Che email khi hiển thị theo danh tính: giữ ký tự đầu + miền."""
    email = str(email or "")
    cuc_bo, _, mien = email.partition("@")
    dau = (cuc_bo[:1] + "***") if cuc_bo else "***"
    return f"{dau}@{mien}" if mien else dau


class _TaiKhoan:
    """Một bộ cookie: client tạo lười, trạng thái sức khoẻ do module giữ.

    Có thể gắn một danh tính chung của lõi (``danh_tinh_id``): gắn rồi thì hiển
    thị tên theo danh tính thay vì dạng che của cookie. Tài khoản không gắn
    chạy y như trước (tương thích ngược).

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
        self.danh_tinh_id: str | None = None
        self.ten_danh_tinh: str | None = None
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
        return self.ten_danh_tinh or che(self.psid)

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

    def danh_tinh_id_cua(self, khoa: str) -> str | None:
        """id danh tính của tài khoản (NV94-d: cầu nối trình duyệt thật đi qua
        danh tính → ho_so hex, không đoán từ ho_so_trinh_duyet)."""
        try:
            tk = self._tim_tai_khoan(str(khoa or "").strip().lower())
        except Exception:
            return None
        return tk.danh_tinh_id if tk is not None else None

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
                                          trinh_duyet) -> KetQuaDangNhap:
        """Bước đăng nhập DUY NHẤT của module: điền form Google, đọc
        __Secure-1PSID/__Secure-1PSIDTS từ cookie, kiểm phiên bằng init().

        CAPTCHA/2FA/xác minh thiết bị → dừng với can_nguoi, KHÔNG cố vượt,
        không né phát hiện bot. Kiểm phiên bằng mã trạng thái/cấu trúc, không
        dò chữ trong thân phản hồi. CHƯA KIỂM với trang đăng nhập Google thật.
        """
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
            tin_hieu = trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if "captcha" in tin_hieu or "2fa" in tin_hieu or "xac_minh" in tin_hieu:
            # CAPTCHA/2FA/xác minh thiết bị: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        mat_khau_tho = mat_khau.lay()
        try:
            await trinh_duyet.dien("email", email)
            await trinh_duyet.dien("mat_khau", mat_khau_tho)
            await trinh_duyet.bam("dang_nhap")
        finally:
            mat_khau_tho = None  # noqa: F841
        try:
            cookie = trinh_duyet.doc_cookie("google.com")
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
                tao = self._tao_trinh_duyet
                td = tao() if callable(tao) else None
                self._trinh_duyet_da_dung = td
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, tai_khoan.mat_khau, td)
            if loai == "dang_nhap_lai":
                bm = self.mat_khau_da_luu(tai_khoan.email)
                if bm is None:
                    return KetQuaDangNhap(False, ma_loi="khong_co_mat_khau_luu")
                tao = self._tao_trinh_duyet
                td = tao() if callable(tao) else None
                self._trinh_duyet_da_dung = td
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, bm, td)
            return KetQuaDangNhap(False, ma_loi="loai_khong_ho_tro")
        finally:
            tai_khoan.xoa_bi_mat()

    # -- tái dùng hồ sơ Google (NV93 — đường chính) -----------------------------

    _URL_TAI_DUNG = "https://gemini.google.com/"
    _BO_CHON_DANG_NHAP = ('a[href*="accounts.google.com"], '
                          'button:has-text("Đăng nhập"), button:has-text("Sign in")')
    _BO_CHON_GOOGLE = '[data-identifier], button:has-text("@gmail.com")'
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare"}

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
            tin_hieu = set(trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
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
                    tin_hieu = set(trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
                    if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                        return KetQuaDangNhap(False, ma_loi="can_nguoi")
                    if "da_dang_nhap" in tin_hieu:
                        da_vao = True
                        break
                    await asyncio.sleep(1)
                if not da_vao:
                    return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
            try:
                cookie = trinh_duyet.doc_cookie("gemini.google.com")
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
                                trinh_duyet) -> KetQuaKhoiPhuc:
        """Tầng (b): đăng nhập lại bằng mật khẩu đã lưu qua trình duyệt."""
        try:
            bm = tai_khoan.mat_khau
            if bm is None:
                return KetQuaKhoiPhuc(False, tang="b", ma_loi="thieu_mat_khau")
            kq = await self._dang_nhap_bang_trinh_duyet(
                tai_khoan.email, bm, trinh_duyet)
            if kq.thanh_cong:
                return KetQuaKhoiPhuc(True, tang="b")
            return KetQuaKhoiPhuc(False, tang="b", ma_loi=kq.ma_loi or "loi_module",
                                  ma_trang_thai=kq.ma_trang_thai,
                                  can_nguoi=(kq.ma_loi == "can_nguoi"))
        finally:
            if tai_khoan.mat_khau is not None:
                tai_khoan.mat_khau.xoa()

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

    # -- gắn danh tính chung (NV77) -------------------------------------------

    def _doc_lien_ket(self) -> dict:
        """Ánh xạ hash cookie → danh_tinh_id trong kho riêng của module. Đọc hỏng
        thì coi như chưa gắn (tài khoản cũ vẫn chạy), không vỡ module."""
        try:
            if self.kho is None:
                return {}
            du_lieu = self.kho.doc("danh_tinh") or {}
            anh_xa = du_lieu.get("anh_xa") or {}
            return {str(k): v for k, v in anh_xa.items() if isinstance(v, str)}
        except Exception as e:
            log.warning("gemini-web: không đọc được liên kết danh tính: %s",
                        type(e).__name__)
            return {}

    def _luu_lien_ket(self, anh_xa: dict) -> None:
        if self.kho is None:
            return
        try:
            cu = self.kho.doc("danh_tinh") or {}
        except Exception:
            cu = {}
        try:
            self.kho.luu("danh_tinh", {"anh_xa": anh_xa,
                                       "gan": cu.get("gan") or [],
                                       "chu_so_huu": cu.get("chu_so_huu")})
        except Exception as e:
            log.warning("gemini-web: không lưu được liên kết danh tính: %s",
                        type(e).__name__)

    def _ten_theo_danh_tinh(self, dt_id: str) -> str | None:
        """Tên hiển thị theo danh tính: nhãn do người dùng đặt, không thì email che."""
        try:
            dt = danh_tinh.lay(dt_id)
        except Exception as e:
            log.warning("gemini-web: không đọc được danh tính %s: %s",
                        dt_id, type(e).__name__)
            return None
        if not dt:
            return None
        nhan = str(dt.get("nhan") or "").strip()
        if nhan:
            return nhan
        return _che_email(dt.get("email") or "")

    def _dong_bo_tham_chieu(self, tap_moi: set[str]) -> None:
        """Đăng ký/gỡ tham chiếu với kho danh tính của lõi theo chênh lệch tuyệt đối.

        Lõi chặn xoá danh tính còn tài khoản trỏ tới (409 con_tai_khoan) dựa trên
        chính các tham chiếu này. Trạng thái đã gắn + chủ sở hữu được nhớ trong
        kho riêng để dung() của thể hiện cũ không gỡ nhầm của thể hiện mới.
        """
        cu: set[str] = set()
        if self.kho is not None:
            try:
                cu = set((self.kho.doc("danh_tinh") or {}).get("gan") or [])
            except Exception as e:
                log.warning("gemini-web: không đọc được trạng thái gắn danh tính: %s",
                            type(e).__name__)
        for dt_id in sorted(cu - tap_moi):
            try:
                danh_tinh.go(dt_id, "gemini-web")
            except Exception as e:
                log.warning("gemini-web: gỡ tham chiếu danh tính %s: %s",
                            dt_id, type(e).__name__)
        for dt_id in sorted(tap_moi - cu):
            # Đã kiểm tồn tại ở nap(); kho danh tính hỏng lúc này thì để 503 lan ra
            # (đóng khi lỗi) thay vì âm thầm chạy không tham chiếu.
            danh_tinh.gan(dt_id, "gemini-web")
        if self.kho is not None:
            try:
                du_lieu = self.kho.doc("danh_tinh") or {}
                self.kho.luu("danh_tinh", {"anh_xa": du_lieu.get("anh_xa") or {},
                                           "gan": sorted(tap_moi),
                                           "chu_so_huu": id(self)})
            except Exception as e:
                log.warning("gemini-web: không lưu được trạng thái gắn danh tính: %s",
                            type(e).__name__)

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách cookie.

        Khoá phụ «danh_tinh» (không khai trong manifest, lõi cho qua): danh sách
        cùng thứ tự với «cookies», mỗi phần tử là id danh tính hoặc null. id lạ
        → ValueError rõ ràng, chưa gắn gì. Không có khoá này thì tra liên kết đã
        nhớ trong kho riêng theo hash cookie (bền qua khởi động); tài khoản không
        gắn chạy y như trước.
        """
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("cookies") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        if isinstance(danh_sach, dict):
            raise ValueError("«cookies» phải là danh sách chuỗi cookie.")
        gan_moi = cau_hinh.get("danh_tinh")
        if gan_moi is not None:
            if not isinstance(gan_moi, list) or len(gan_moi) != len(danh_sach):
                raise ValueError(
                    "«danh_tinh» phải là danh sách cùng độ dài với «cookies» "
                    f"(nhận {len(gan_moi) if isinstance(gan_moi, list) else type(gan_moi).__name__}, "
                    f"cần {len(danh_sach)}); mỗi phần tử là id danh tính hoặc null.")
            for dt_id in gan_moi:
                if dt_id is None:
                    continue
                if not isinstance(dt_id, str) or danh_tinh.lay(dt_id) is None:
                    raise ValueError(
                        f"danh_tinh_id {dt_id!r} không tồn tại trong sổ danh tính "
                        "(tạo trước ở «Hệ thống → AI → Danh tính»).")
        lien_ket = self._doc_lien_ket()
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
            if gan_moi is not None:
                tk.danh_tinh_id = gan_moi[i]
            else:
                tk.danh_tinh_id = lien_ket.get(_hash_cookie(tk.chuoi_goc))
            if tk.danh_tinh_id:
                tk.ten_danh_tinh = self._ten_theo_danh_tinh(tk.danh_tinh_id)
            tai_khoan.append(tk)
        if gan_moi is not None:
            for tk in tai_khoan:
                h = _hash_cookie(tk.chuoi_goc)
                if tk.danh_tinh_id:
                    lien_ket[h] = tk.danh_tinh_id
                else:
                    lien_ket.pop(h, None)
            self._luu_lien_ket(lien_ket)
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._co_so_url = co_so
        self._dong_bo_tham_chieu({tk.danh_tinh_id for tk in tai_khoan
                                  if tk.danh_tinh_id})
        log.info("gemini-web: đã nạp %d tài khoản (%d gắn danh tính)",
                 len(tai_khoan), sum(1 for tk in tai_khoan if tk.danh_tinh_id))

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
        """
        ds = []
        for tk in self._thu_tu_tai_khoan():
            model_tk = tk.model or list(MO_HINH_HO_TRO)
            if ten_model in model_tk:
                ds.append(tk)
        return ds

    async def chat(self, body: dict, stream: bool):
        body = dict(body or {})
        model_goc = str(body.get("model") or "")
        try:
            ten_model = giai_quyet_model_day_du(model_goc)
        except ModelKhongCo:
            thong_diep = (f"model {nhac_lai(tach_tien_to(model_goc))} không có; "
                          f"chọn trong: {', '.join(id_day_du(m) for m in MO_HINH_HO_TRO)}")
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, thong_diep), ensure_ascii=False).encode("utf-8")
        model_id = id_day_du(ten_model)
        prompt = chuyen_thanh_prompt(body)
        if not prompt:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        ds = self._tai_khoan_cho_model(ten_model)
        if not ds:
            if not self._thu_tu_tai_khoan():
                return 401, {"Content-Type": "application/json"}, \
                    json.dumps(than_loi(401, "gemini-web chưa có tài khoản nào: "
                                             "thêm cookie Google ở trường cookies rồi cài lại."),
                               ensure_ascii=False).encode("utf-8")
            goi_can = "pro" if ten_model == "gemini-3-pro" else "free"
            thong_diep = (f"model {model_id} không có cho tài khoản nào "
                          f"(cần tài khoản gói {goi_can} trở lên)")
            return 404, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(404, thong_diep), ensure_ascii=False).encode("utf-8")
        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(ds, prompt, model_id, ten_model)

        loi_cuoi: LoiGeminiWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)
                van_ban = await tk.client.generate_content(prompt, ten_model)
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
            tk.khoe = True
            tk.loai_loi = None
            return 200, {"Content-Type": "application/json"}, phan_hoi_json(model_id, van_ban)

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
                          model_id: str, ten_model: str):
        """SSE OpenAI; xoay tài khoản ngay trong luồng khi tài khoản đầu hỏng."""
        hoan_thanh_id = tao_hoan_thanh_id()
        tao_luc = int(time.time())
        yield chunk_vai_tro(model_id, hoan_thanh_id, tao_luc)
        loi_cuoi: LoiGeminiWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.psid, tk.psidts)
                da_ra_du_lieu = False
                async for delta in tk.client.generate_content_stream(prompt, ten_model):
                    da_ra_du_lieu = True
                    yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)
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
            tk.khoe = True
            tk.loai_loi = None
            yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc)
            yield dong_done()
            return
        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản gemini-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / suc_khoe / dung --------------------------------------------

    def _muc_model(self, ma: str, ds_khoa: list[str], ds_goi: list[str]) -> dict:
        """Một mục /v1/models: id ổn định + nhãn tách riêng + gói/tài khoản."""
        goi_can = "pro" if "pro" in ds_goi else ("free" if ds_goi else "khong_ro")
        return {"id": id_day_du(ma), "object": "model",
                "created": 1760000000, "owned_by": "gemini-web",
                "nhan": nhan_model(ma), "goi": goi_can,
                "available_cho": list(ds_khoa)}

    async def models(self):
        """Hợp các model theo TÀI KHOẢN (NV91-P3): mỗi model kèm nhãn hiển thị,
        gói tối thiểu và danh sách khoá tài khoản dùng được. Chưa có tài khoản
        → bảng tĩnh đầy đủ (CHƯA KIỂM thật)."""
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            return [self._muc_model(m, [], []) for m in MO_HINH_HO_TRO]
        hop: dict[str, dict] = {}
        for tk in ds:
            khoa = self._khoa_tu_token(tk.psid)
            for m in tk.model or list(MO_HINH_HO_TRO):
                muc = hop.setdefault(m, {"khoa": [], "goi": []})
                if khoa not in muc["khoa"]:
                    muc["khoa"].append(khoa)
                    muc["goi"].append(tk.goi)
        return [self._muc_model(m, v["khoa"], v["goi"]) for m, v in hop.items()]

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
                "ghi_chu": None if ds else "chưa cấu hình tài khoản nào"}

    async def dung(self):
        # Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký. Thể hiện thay
        # thế (nap sau) đã ghi đè chu_so_huu nên không bị gỡ nhầm; thể hiện tạm
        # dùng để kiểm ở bước cài cũng tự dọn sau finally.
        try:
            if self.kho is not None:
                du_lieu = self.kho.doc("danh_tinh") or {}
                if du_lieu.get("chu_so_huu") == id(self):
                    for dt_id in du_lieu.get("gan") or []:
                        try:
                            danh_tinh.go(dt_id, "gemini-web")
                        except Exception as e:
                            log.warning("gemini-web: gỡ tham chiếu danh tính %s khi dừng: %s",
                                        dt_id, type(e).__name__)
                    self.kho.luu("danh_tinh",
                                 {"anh_xa": du_lieu.get("anh_xa") or {},
                                  "gan": [], "chu_so_huu": id(self)})
        except Exception as e:
            log.warning("gemini-web: dọn tham chiếu danh tính khi dừng: %s",
                        type(e).__name__)
        with self._khoa:
            ds = list(self._tai_khoan)
            self._tai_khoan = []
        for tk in ds:
            if tk.client is not None:
                try:
                    await tk.client.close()
                except Exception:
                    pass
