"""Module claude-web — trò chuyện với Claude qua API web nội bộ của claude.ai.

Hai đường xác thực: (1) cookie/sessionKey do NGƯỜI DÙNG dán (đường cũ, khoá
«cl-…»); (2) tái dùng hồ sơ Google đã đăng nhập qua ``tai_dung_ho_so``
(NV93 — «Continue with Google» trong hồ sơ, khoá «tk-<băm>», đi tắt nếu hồ sơ
đã có phiên). Hỗ trợ nhiều tài khoản: xoay vòng khi gặp 429/529 (hết hạn
mức), 401/403 (cookie hỏng), hoặc lỗi tạm thời 5xx/mạng — phân loại bằng mã
trạng thái HTTP, không dò chữ trong thân phản hồi.

Bí mật: cookie chỉ đi trong header Cookie tới đúng base_url đã kiểm; log và mọi
phản hồi API chỉ nêu dạng che «…abcd», không bao giờ mang giá trị thô.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

import httpx

from app import model_bo_sung
from app import tu_lam_moi
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap
from app.tu_xa import kiem_url_lan

from . import converter as cv
from .client import BASE_MAC_DINH, ClaudeWebClient, LoiClaudeWeb, MayKhachClaudeWeb

LOG = logging.getLogger("web2api.module.claude-web")

#: Số tài khoản tối đa thử trong một lượt chat (mọi tài khoản đã nạp đều được thử).
GIOI_HAN_LUOT = 8


def _che(cookie: str) -> str:
    """Che cookie theo quy ước kho: chỉ hiện «…abcd» (4 ký tự cuối của GIÁ TRỊ
    cookie đầu). Không hiện tên cookie; giá trị ≤ 8 ký tự → «…»."""
    dau = str(cookie or "").split(";")[0].strip()
    if "=" in dau:
        _, v = dau.split("=", 1)
        v = v.strip()
        return f"…{v[-4:]}" if len(v) > 8 else "…"
    s = str(cookie or "").strip()
    return f"…{s[-4:]}" if len(s) > 8 else "…"


def chuan_hoa_cookie(gia_tri: str) -> str:
    """Chuỗi cookie đầy đủ «k1=v1; k2=v2» giữ nguyên; giá trị trần (không có «=»)
    được coi là sessionKey."""
    s = str(gia_tri or "").strip()
    if s and "=" not in s:
        s = "sessionKey=" + s
    return s


def chuan_hoa_base_url(gia_tri) -> str:
    s = str(gia_tri or "").strip().rstrip("/")
    if not s:
        return BASE_MAC_DINH
    u = urlsplit(s)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("base_url phải bắt đầu bằng http:// hoặc https:// và có tên máy")
    return s


def ds_cookie(gia_tri) -> list[str]:
    if isinstance(gia_tri, str):
        gia_tri = [gia_tri]
    if not isinstance(gia_tri, list):
        return []
    ra: list[str] = []
    for c in gia_tri:
        c = chuan_hoa_cookie(c)
        if c and c not in ra:
            ra.append(c)
    return ra


class _TaiKhoan:
    """Một bộ cookie claude.ai + máy khách riêng.

    """

    def __init__(self, cookie: str, base_url: str):
        self.cookie = cookie
        self.ten_che = _che(cookie)
        self.may_khach = MayKhachClaudeWeb(cookie, base_url)
        self.khoe = True

    def ten_hien_thi(self) -> str:
        return self.ten_che


class _TaiKhoanNV93:
    """Tài khoản claude-web từ đường tái dùng hồ sơ Google (NV93).

    ``session_key`` là BÍ MẬT — chỉ đi trong cookie tới claude.ai, không bao
    giờ vào log, phản hồi API, nhật ký hay thông điệp lỗi. Bản ghi lưu kho
    riêng KHÔNG mang giá trị thô (chỉ siêu dữ liệu làm giàu).
    """

    def __init__(self, session_key: str):
        self.session_key = str(session_key)
        self.org: str = ""
        self.email: str = ""
        self.ho_so: str | None = None
        #: CHƯA KIỂM: chưa có cấu trúc suy gói Free/Pro từ claude.ai thật —
        #: /api/organizations (máy chủ giả) không mang thông tin gói.
        self.goi: str = "khong_ro"
        self.trang_thai: str = "hoat_dong"
        self.lan_dung_cuoi: str | None = None
        #: Mã model tài khoản này dùng được (CHƯA KIỂM theo tài khoản thật —
        #: mặc định bảng tĩnh đầy đủ của converter).
        self.model: list[str] = sorted(set(cv.MO_HINH_BI_DANH) | {"auto"})

    def ban_ghi(self, khoa: str) -> dict:
        """Bản ghi làm giàu lưu kho riêng (KHÔNG bí mật thô)."""
        return {
            "khoa": khoa,
            "email": self.email,
            "ho_so": self.ho_so,
            "org": self.org,
            "goi": self.goi,
            "trang_thai": self.trang_thai,
            "lan_dung_cuoi": self.lan_dung_cuoi,
            "model": list(self.model),
        }

    def ap_dung_ban_ghi(self, bg: dict | None) -> None:
        if not isinstance(bg, dict):
            return
        for truong in ("email", "org", "goi", "trang_thai", "lan_dung_cuoi"):
            v = bg.get(truong)
            if isinstance(v, str) and v:
                setattr(self, truong, v)
        hs = bg.get("ho_so")
        if isinstance(hs, str) and hs:
            self.ho_so = hs
        md = bg.get("model")
        if isinstance(md, list):
            self.model = [str(m) for m in md if isinstance(m, str) and m]


def _session_key_tu_cookie(cookie: str) -> str | None:
    """Trích sessionKey khỏi chuỗi cookie đã chuẩn hoá «sessionKey=…».

    Không phải dạng đó → None (tài khoản dán cookie cũ không thuộc NV93).
    """
    s = str(cookie or "").strip()
    if s.startswith("sessionKey="):
        v = s[len("sessionKey="):].split(";")[0].strip()
        return v or None
    return None


def phan_loai_ma(ma: int) -> str:
    """Mã HTTP → nhóm lỗi: chỉ dùng mã trạng thái, không dò chữ."""
    if ma in (401, 403):
        return "xac_thuc"
    if ma in (429, 529):
        return "han_muc"
    return "tam_thoi"


def _hash_cookie(chuoi: str) -> str:
    """Dấu vân tay ổn định của một bộ cookie — khoá để nhớ liên kết danh tính
    qua các lần nạp mà không phải lưu cookie hai nơi."""
    return hashlib.sha256(str(chuoi or "").strip().encode("utf-8")).hexdigest()


_QUY_TAC_TIN_HIEU = [{'loai': 'cookie', 'mien': 'claude.ai', 'ten': 'sessionKey', 'tin_hieu': 'da_dang_nhap'}, {'loai': 'url_chua', 'gia_tri': 'claude.ai/login', 'tin_hieu': 'chua_dang_nhap'}]

class Module:
    """Module claude-web: nap / cai / models / chat / suc_khoe / dung."""

    def __init__(self, manifest: dict):
        self.manifest = manifest or {}
        self._tien_to = str(self.manifest.get("tien_to") or "clw")
        self._accs: list[_TaiKhoan] = []
        self._base_url = BASE_MAC_DINH
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        # Khoá tài khoản đã đánh dấu hỏng (tầng c bỏ qua khi chọn kế).
        self._hong: set[str] = set()
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV89):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None
        # Tài khoản từ đường tái dùng hồ sơ Google (NV93): khoá «tk-<băm>»,
        # độc lập khoá «cl-…» của tài khoản dán cookie cũ (giữ để test NV89 xanh).
        self._tai_khoan_nv93: list[_TaiKhoanNV93] = []

    # ── đăng nhập + khôi phục (NV89, hợp đồng NV67/NV88) ────────────────

    @staticmethod
    def _khoa_tu_cookie(cookie: str) -> str:
        """Khoá mờ của một bộ cookie: «cl-» + 12 ký tự hex của SHA-256."""
        return "cl-" + hashlib.sha256(str(cookie).strip().encode("utf-8")).hexdigest()[:12]

    def _tim_acc(self, khoa: str) -> _TaiKhoan | None:
        for a in self._accs:
            if self._khoa_tu_cookie(a.cookie) == khoa:
                return a
        return None

    def _them_cookie(self, cookie: str) -> _TaiKhoan:
        """Chuẩn hoá rồi thêm vào _accs nếu chưa có (lũy đẳng); trả _Acc."""
        cookie = chuan_hoa_cookie(cookie)
        acc = self._tim_acc(self._khoa_tu_cookie(cookie))
        if acc is None:
            acc = _TaiKhoan(cookie, self._base_url)
            self._accs.append(acc)
        return acc

    def mat_khau_da_luu(self, khoa: str) -> BiMatTam | None:
        """Mật khẩu đã lưu cho một khoá/email (kho riêng mã hoá của module).

        Đọc hỏng → None (coi như chưa lưu): tầng (b)/dang_nhap_lai trả thất bại
        chứ không cho phép — fail-closed.
        """
        if self.kho is None:
            return None
        try:
            ds = self.kho.doc("mat_khau") or {}
        except Exception:
            return None
        mk = ds.get(str(khoa or "").strip().lower()) if isinstance(ds, dict) else None
        return BiMatTam(mk) if isinstance(mk, str) and mk else None

    def _luu_mat_khau(self, khoa: str, email: str, mat_khau: BiMatTam) -> None:
        """Lưu mật khẩu theo cả khoá-cookie và email (dang_nhap_lai chỉ có email).

        Lưu hỏng → chỉ ghi nhật ký, KHÔNG vỡ lượt đăng nhập đang thành công
        (mật khẩu lần này đơn giản không được nhớ cho tầng (b) sau).
        """
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
            LOG.warning("claude-web: không lưu được mật khẩu: %s", type(e).__name__)

    def co_tai_khoan(self, khoa: str) -> bool:
        """Module có biết khoá/email này không?"""
        khoa = str(khoa or "").strip().lower()
        if self._tim_acc(khoa) is not None:
            return True
        return self.mat_khau_da_luu(khoa) is not None

    def quy_tac_tin_hieu(self) -> list:
        """Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): module KHAI, mã tín hiệu MỞ.

        Dịch vụ thực thi: cookie đặc trưng có/không, URL đích — không dò chữ.
        Chỉ NHẬN RA thử thách → can_nguoi (không tự vượt).
        """
        return [dict(q) for q in _QUY_TAC_TIN_HIEU]
    def ho_so_trinh_duyet(self, khoa: str) -> str:
        """ID workspace cho noVNC: đúng khuôn chữ/số/gạch ngang/gạch dưới."""
        bam = hashlib.sha256(str(khoa).encode("utf-8")).hexdigest()[:16]
        return f"claude-web-{bam}"



    _URL_DANG_NHAP = "https://claude.ai/login"

    async def _loi_kiem_cookie(self, cookie: str) -> KetQuaDangNhap | None:
        """Kiểm một bộ cookie bằng GET /api/organizations (phải có capability
        «chat»): sống → None; hỏng → kết quả THẤT BẠI đã phân loại.

        Fail-closed: lỗi lạ → «loi_mang», không bao giờ trả thành công; phân
        loại chỉ bằng mã HTTP, không dò chữ.
        """
        khach = MayKhachClaudeWeb(cookie, self._base_url)
        try:
            await khach.lay_org_id()
        except LoiClaudeWeb as e:
            if e.ma in (401, 403):
                return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                      ma_trang_thai=401)
            return KetQuaDangNhap(False, ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_mang")
        finally:
            await khach.dong()
        return None

    async def _dang_nhap_bang_trinh_duyet(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet) -> KetQuaDangNhap:
        """BƯỚC ĐĂNG NHẬP DUY NHẤT NẰM TRONG MODULE.

        Luồng khung: mở https://claude.ai/login → đọc tín hiệu → điền email +
        mật khẩu → bấm đăng nhập → đọc cookie «sessionKey» → kiểm phiên bằng
        GET /api/organizations.

        LƯU Ý THỰC TẾ: claude.ai đăng nhập bằng mã gửi qua email hoặc «Continue
        with Google» (SSO) — KHÔNG có ô mật khẩu của Anthropic. Đường điền
        email + mật khẩu ở đây là khuôn dành cho tài khoản Google ĐÃ LIÊN KẾT
        (đi qua SSO); chi tiết SSO Google thật CHƯA KIỂM — cần trình duyệt thật
        + tài khoản thật mới xác nhận được. Kiểm phiên chỉ bằng mã trạng thái
        HTTP (không dò chữ trong thân phản hồi).
        """
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
            tin_hieu = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if "captcha" in tin_hieu or "2fa" in tin_hieu or "xac_minh" in tin_hieu:
            # CAPTCHA/2FA/xác minh thiết bị: DỪNG ngay, không cố vượt, không né bot.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.dien("email", email)
            await trinh_duyet.dien("mat_khau", mat_khau.lay())
            await trinh_duyet.bam("dang_nhap")
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        try:
            cookie = await trinh_duyet.doc_cookie("claude.ai")
        except Exception:
            cookie = {}
        phien = cookie.get("sessionKey") if isinstance(cookie, dict) else None
        if not phien:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        chuoi_cookie = "sessionKey=" + str(phien)
        khach = MayKhachClaudeWeb(chuoi_cookie, self._base_url)
        try:
            await khach.lay_org_id()
        except LoiClaudeWeb as e:
            if e.ma in (401, 403):
                return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                      ma_trang_thai=401)
            return KetQuaDangNhap(False, ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_mang")
        finally:
            await khach.dong()
        acc = self._them_cookie(chuoi_cookie)
        khoa = self._khoa_tu_cookie(acc.cookie)
        self._luu_mat_khau(khoa, email, mat_khau)
        return KetQuaDangNhap(True, cookie=acc.cookie)

    async def dang_nhap(self, tai_khoan: DongDaTach) -> KetQuaDangNhap:
        """Hợp đồng NV67: cookie → kiểm; mat_khau → trình duyệt; dang_nhap_lai →
        mật khẩu đã lưu. BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        try:
            if loai == "cookie":
                cookie_tho = tai_khoan.cookie.lay() if tai_khoan.cookie else ""
                if not cookie_tho:
                    return KetQuaDangNhap(False, ma_loi="thieu_cookie")
                cookie = chuan_hoa_cookie(cookie_tho)
                loi = await self._loi_kiem_cookie(cookie)
                if loi is not None:
                    return loi
                acc = self._them_cookie(cookie)
                return KetQuaDangNhap(True, cookie=acc.cookie)
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

    # ── tái dùng hồ sơ Google (NV93 — đường chính) ────────────────────────

    _URL_GOC = "https://claude.ai/"
    # NV94-c: chỉ CSS chuẩn (Marionette không hiểu :has-text của Playwright).
    # Đo trên trang đăng nhập THẬT (18/10/2026): nút Google là <button data-testid="login-with-google"> (không phải liên kết); bấm → accounts.google.com.
    _BO_CHON_GOOGLE = 'button[data-testid="login-with-google"], a[href*="accounts.google.com"]'
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare", "thu_thach_truy_cap"}

    def _tao_client(self, session_key: str) -> ClaudeWebClient:
        """Mối nối để test chích máy chủ giả (NV93).

        Mặc định: ClaudeWebClient thật tới base_url của module.
        """
        return ClaudeWebClient(session_key, base_url=self._base_url)

    @staticmethod
    def _khoa_tu_session_key(session_key: str) -> str:
        """Khoá mờ ổn định của một tài khoản NV93: «tk-» + 12 hex SHA-256.

        Hợp đồng NV93: khoá mọi module web đều dạng ``tk-<băm>``. Tài khoản cũ
        dán cookie vẫn giữ khoá «cl-…» của _khoa_tu_cookie (không đổi để test
        NV89 xanh).
        """
        return "tk-" + hashlib.sha256(
            str(session_key or "").encode("utf-8")).hexdigest()[:12]

    def _khoa_nv93_tu_cookie(self, cookie: str) -> str | None:
        """Khoá NV93 của một chuỗi cookie đã chuẩn hoá; không phải dạng
        sessionKey → None."""
        sk = _session_key_tu_cookie(cookie)
        return self._khoa_tu_session_key(sk) if sk else None

    async def tai_dung_ho_so(self, ho_so, trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập bằng Google TRONG hồ sơ đã đăng nhập Google (NV93).

        Hợp đồng NV92-P3: module tự làm bước «Continue with Google» của nó
        trong hồ sơ, lấy cookie ``sessionKey``, kiểm phiên + đọc org qua
        GET /api/organizations (org có capability «chat»), thêm tài khoản vào
        kho của CHÍNH mình, trả ``KetQuaDangNhap`` với ``cookie`` là chuỗi
        cookie đã chuẩn hoá. Không nhập lại email/mật khẩu/2FA — KHÔNG BAO
        GIỜ ``dien()``. Gặp CAPTCHA/Cloudflare/2FA/xác minh → can_nguoi NGAY
        (không cố vượt, không thử lại).

        HỌC TỪ c2a (chỉ đọc lấy hiểu, tự viết lại): TriTue2011/chatgpt2api,
        ``captcha-solver/src/claude_web_login.py`` — các bước: mở claude.ai
        (đi tắt nếu hồ sơ đã có phiên) → /login → «Continue with Google»
        (popup) → chọn tài khoản Google trong hồ sơ → về claude.ai → đọc
        sessionKey → kiểm bằng /api/organizations.

        CHƯA KIỂM trên claude.ai thật: luồng SSO Google tay có thể cần thêm
        bước mà chỉ kiểm thật mới thấy; suy gói Free/Pro từ cấu trúc hiện
        chưa có dữ liệu nên để «khong_ro».
        """
        hs_id = ho_so.get("id") if isinstance(ho_so, dict) else str(ho_so or "")
        hs_id = str(hs_id or "").strip() or None
        email = str(ho_so.get("email") or "") if isinstance(ho_so, dict) else ""
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            # Bước 1 — đi tắt: hồ sơ đã có phiên claude.ai còn hiệu lực.
            await trinh_duyet.mo_trang(self._URL_GOC)
            tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
            if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                # CAPTCHA/Cloudflare/2FA/xác minh: DỪNG ngay, không cố vượt.
                return KetQuaDangNhap(False, ma_loi="can_nguoi")
            if "da_dang_nhap" not in tin_hieu:
                # Bước 2 — bấm «Continue with Google» trong hồ sơ (SSO).
                await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
                tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
                if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                    return KetQuaDangNhap(False, ma_loi="can_nguoi")
                if "da_dang_nhap" not in tin_hieu:
                    await trinh_duyet.bam(self._BO_CHON_GOOGLE)
                    # Bước 3 — chờ phiên hình thành sau SSO (hết lượt chờ rõ).
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
                        return KetQuaDangNhap(False,
                                             ma_loi="khong_lay_duoc_phien")
            try:
                cookie = await trinh_duyet.doc_cookie("claude.ai")
            except Exception:
                cookie = {}
            session_key = (cookie.get("sessionKey")
                           if isinstance(cookie, dict) else None)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if not session_key:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        session_key = str(session_key)
        # Bước 4 — kiểm phiên + đọc org (bằng cấu trúc; không đoán cookie khác).
        khach = self._tao_client(session_key)
        try:
            org = await khach.lay_org()
        except LoiClaudeWeb as e:
            if e.ma in (401, 403):
                return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                      ma_trang_thai=401)
            return KetQuaDangNhap(False, ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_mang")
        finally:
            try:
                await khach.dong()
            except Exception as e:
                # Dọn dẹp best-effort: chỉ ghi nhật ký, không vỡ lượt tái dùng.
                LOG.warning("claude-web: đóng client kiểm phiên: %s",
                            type(e).__name__)
        khoa = self._khoa_tu_session_key(session_key)
        self._them_tai_khoan_nv93(session_key, org, hs_id, email)
        # Log chỉ khoá mờ — KHÔNG BAO GIỜ log session_key/org thô.
        LOG.info("claude-web: tái dùng hồ sơ %s → tài khoản %s", hs_id, khoa)
        return KetQuaDangNhap(True, cookie="sessionKey=" + session_key)

    def _them_tai_khoan_nv93(self, session_key: str, org: str,
                             hs_id: str | None, email: str) -> _TaiKhoanNV93:
        """Thêm/cập nhật tài khoản NV93 (chống trùng session_key).

        Đồng thời thêm vào _accs để chat() xoay được; lưu bản ghi làm giàu
        (không bí mật thô) vào kho riêng.
        """
        session_key = str(session_key)
        tk = next((t for t in self._tai_khoan_nv93
                   if t.session_key == session_key), None)
        if tk is None:
            tk = _TaiKhoanNV93(session_key)
            self._tai_khoan_nv93.append(tk)
        if org:
            tk.org = str(org)
        if email:
            tk.email = str(email)
        if hs_id:
            tk.ho_so = hs_id
        tk.trang_thai = "hoat_dong"
        tk.lan_dung_cuoi = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        khoa = self._khoa_tu_session_key(session_key)
        self._xoa_khoi_go_bo_nv93(khoa)
        self._them_cookie("sessionKey=" + session_key)
        self._luu_ban_ghi_nv93()
        return tk

    # -- liệt kê / gỡ tài khoản (NV93) ------------------------------------------

    def liet_ke_tai_khoan(self) -> list[dict]:
        """Danh sách tài khoản NV93 KHÔNG bí mật: khoa/email/ho_so/goi/trạng thái.

        Tài khoản dán cookie cũ (khoá «cl-…») vẫn quản lý qua Hệ thống → AI
        (đường cai()/nap() có sẵn); hàm này theo hợp đồng NV93 mới.
        """
        return [{
            "khoa": self._khoa_tu_session_key(tk.session_key),
            "email": tk.email,
            "ho_so": tk.ho_so,
            "goi": tk.goi,
            "trang_thai": tk.trang_thai,
            "lan_dung_cuoi": tk.lan_dung_cuoi,
        } for tk in list(self._tai_khoan_nv93)]

    def go_tai_khoan(self, khoa: str) -> bool:
        """Gỡ đúng MỘT tài khoản NV93 của module này; ghi sổ gỡ bỏ vào kho riêng
        để khởi động lại không dựng lại. Không động module khác, không động hồ sơ."""
        khoa = str(khoa or "")
        bi_go = [tk for tk in self._tai_khoan_nv93
                 if self._khoa_tu_session_key(tk.session_key) == khoa]
        if not bi_go:
            return False
        self._tai_khoan_nv93 = [tk for tk in self._tai_khoan_nv93
                                if self._khoa_tu_session_key(tk.session_key)
                                != khoa]
        # Gỡ khỏi _accs (chat) để không dùng phiên đã gỡ.
        for tk in bi_go:
            chuoi = "sessionKey=" + tk.session_key
            self._accs = [a for a in self._accs if a.cookie != chuoi]
        tap = self._doc_go_bo_nv93()
        tap.add(khoa)
        self._luu_go_bo_nv93(tap)
        self._luu_ban_ghi_nv93()
        LOG.info("claude-web: đã gỡ tài khoản %s", khoa)
        return True

    # -- kho riêng: bản ghi làm giàu + sổ gỡ bỏ (NV93) -------------------------

    def _doc_ban_ghi_nv93(self) -> dict:
        """{khoa: bản ghi} từ kho riêng; kho chưa gán/hỏng → {} (không vỡ)."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("tai_khoan_nv93") or {}
        except Exception as e:
            LOG.warning("claude-web: không đọc được bản ghi NV93: %s",
                        type(e).__name__)
            return {}
        bg = du_lieu.get("ban_ghi") if isinstance(du_lieu, dict) else None
        return bg if isinstance(bg, dict) else {}

    def _luu_ban_ghi_nv93(self) -> None:
        """Lưu bản ghi làm giàu NV93 (không bí mật thô). Không vỡ."""
        if self.kho is None:
            return
        try:
            ban_ghi = {}
            for tk in self._tai_khoan_nv93:
                khoa = self._khoa_tu_session_key(tk.session_key)
                ban_ghi[khoa] = tk.ban_ghi(khoa)
            self.kho.luu("tai_khoan_nv93", {"ban_ghi": ban_ghi})
        except Exception as e:
            LOG.warning("claude-web: không lưu được bản ghi NV93: %s",
                        type(e).__name__)

    def _doc_go_bo_nv93(self) -> set:
        """Tập khoá NV93 đã bị go_tai_khoan gỡ (để nap() không dựng lại)."""
        if self.kho is None:
            return set()
        try:
            du_lieu = self.kho.doc("go_bo_nv93") or {}
        except Exception:
            return set()
        ds = du_lieu.get("khoa") if isinstance(du_lieu, dict) else None
        return {str(k) for k in ds} if isinstance(ds, list) else set()

    def _luu_go_bo_nv93(self, tap: set) -> None:
        if self.kho is None:
            return
        try:
            self.kho.luu("go_bo_nv93", {"khoa": sorted(tap)})
        except Exception as e:
            LOG.warning("claude-web: không lưu được sổ gỡ bỏ NV93: %s",
                        type(e).__name__)

    def _xoa_khoi_go_bo_nv93(self, khoa: str) -> None:
        tap = self._doc_go_bo_nv93()
        if khoa in tap:
            tap.discard(khoa)
            self._luu_go_bo_nv93(tap)

    # -- các tầng khôi phục NV89 ----------------------------------------------

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a): kiểm phiên còn sống bằng GET /api/organizations.

        claude.ai không có endpoint làm mới session công khai — tầng này chỉ
        kiểm tra; hỏng thì để tầng (b)/(c) xử lý.
        """
        acc = self._tim_acc(khoa)
        if acc is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        try:
            await acc.may_khach.lay_org_id()
        except LoiClaudeWeb as e:
            if e.ma in (401, 403):
                return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han",
                                      ma_trang_thai=401)
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
        ds = [self._khoa_tu_cookie(a.cookie) for a in self._accs]
        khoa = str(khoa)
        con_khoe = [k for k in ds if k != khoa and k not in self._hong]
        return con_khoe[0] if con_khoe else None

    def danh_dau_hong(self, khoa: str, ma_loi: str) -> None:
        """Đánh dấu tài khoản hỏng để tầng (c) bỏ qua."""
        self._hong.add(str(khoa))

    def khoa_tai_khoan(self) -> list[str]:
        """NV91-P2: khoá mờ mọi tài khoản (vòng làm mới định kỳ của lõi liệt kê)."""
        return [self._khoa_tu_cookie(a.cookie) for a in self._accs]

    def danh_dau_khoe(self, khoa: str) -> None:
        """NV91-P2: tài khoản khoẻ lại sau tự khôi phục → bỏ đánh dấu hỏng."""
        self._hong.discard(str(khoa))

    # ── vòng đời ─────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM.

        """
        cau_hinh = cau_hinh or {}
        try:
            self._base_url = chuan_hoa_base_url(cau_hinh.get("base_url"))
        except ValueError:
            self._base_url = BASE_MAC_DINH
        ds = ds_cookie(cau_hinh.get("cookies"))
        # NV93: cookie đã bị go_tai_khoan gỡ thì không dựng lại; bản ghi làm
        # giàu (email/hồ sơ/gói) được khôi phục từ kho riêng, không cần mạng.
        go_bo_nv93 = self._doc_go_bo_nv93()
        ban_ghi_nv93 = self._doc_ban_ghi_nv93()
        accs = []
        tai_khoan_nv93 = []
        for i, c in enumerate(ds):
            khoa93 = self._khoa_nv93_tu_cookie(c)
            if khoa93 is not None and khoa93 in go_bo_nv93:
                continue  # đã bị gỡ tường minh — không dựng lại
            acc = _TaiKhoan(c, self._base_url)
            accs.append(acc)
            if khoa93 is not None:
                bg = ban_ghi_nv93.get(khoa93)
                if isinstance(bg, dict):
                    sk = _session_key_tu_cookie(c)
                    if sk:
                        tk93 = _TaiKhoanNV93(sk)
                        tk93.ap_dung_ban_ghi(bg)
                        tai_khoan_nv93.append(tk93)
        self._accs = accs
        self._tai_khoan_nv93 = tai_khoan_nv93
        LOG.info("claude-web: đã nạp %d tài khoản", len(accs))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng một lượt GET /api/organizations (không tốn tiền).

        Cookie bị từ chối (401/403) bị loại; cookie lỗi tạm thời (5xx/mạng) được
        GIỮ lại (đánh dấu ok=false) để thử lại sau — tránh bắt người dùng dán lại
        chỉ vì mạng chập chờn lúc cài.
        """
        cau_hinh = cau_hinh or {}
        try:
            base_url = chuan_hoa_base_url(cau_hinh.get("base_url"))
        except ValueError as e:
            return {"ok": False, "loi": str(e), "ket_qua_tung_khoa": [],
                    "cau_hinh_luu": {}}
        if str(cau_hinh.get("base_url") or "").strip():
            # base_url do người dùng ghi đè (kiểm thử/trỏ proxy): bắt buộc qua
            # app/tu_xa.py như mọi URL cấu hình được — chặn SSRF (loopback,
            # link-local, IPv4 nhúng trong 6to4/Teredo). Giá trị mặc định
            # claude.ai không cần kiểm (hằng số trong mã).
            try:
                base_url = await kiem_url_lan(base_url)
            except ValueError as e:
                return {"ok": False, "loi": f"base_url không hợp lệ: {e}",
                        "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}
        cookies = ds_cookie(cau_hinh.get("cookies"))
        if not cookies:
            return {"ok": False, "loi": "Chưa có cookie nào — dán cookie từ tab claude.ai đã đăng nhập.",
                    "ket_qua_tung_khoa": [], "cau_hinh_luu": {}}
        ket_qua = []
        giu = []
        for c in cookies:
            khach = MayKhachClaudeWeb(c, base_url)
            try:
                dung_duoc, ly_do, la_xac_thuc = await khach.kiem_tra()
            finally:
                await khach.dong()
            muc = {"cookie": _che(c), "ok": dung_duoc}
            if not dung_duoc:
                muc["loi"] = ly_do
                if not la_xac_thuc:
                    giu.append(c)  # lỗi tạm: giữ lại để thử sau
            else:
                giu.append(c)
            ket_qua.append(muc)
        if not giu:
            return {"ok": False, "loi": "Mọi cookie đều bị claude.ai từ chối (401/403).",
                    "ket_qua_tung_khoa": ket_qua, "cau_hinh_luu": {}}
        return {"ok": True, "ket_qua_tung_khoa": ket_qua,
                "cau_hinh_luu": {"cookies": giu, "base_url": base_url}}

    async def models(self) -> list[dict]:
        """Hợp các model theo TÀI KHOẢN NV93 (NV91-P3 cho Claude).

        id ổn định + nhãn «nhan» tách riêng; mỗi model kèm «goi» và
        «available_cho» (khoá tài khoản dùng được). Chưa có tài khoản NV93 →
        bảng tĩnh đầy đủ (CHƯA KIỂM thật: chưa đo model theo gói Free/Pro
        trên claude.ai thật; định tuyến theo model chờ khám phá thật).
        """
        co_ban = cv.danh_sach_model(self._tien_to)
        ten = sorted(set(cv.MO_HINH_BI_DANH) | {"auto"})
        nhan = {m["id"].split("/", 1)[1]: m["id"].split("/", 1)[1]
                for m in co_ban if "/" in m["id"]}
        ds93 = list(self._tai_khoan_nv93)
        if not ds93:
            ra = [{**m, "nhan": nhan.get(m["id"].split("/", 1)[1], m["id"]),
                   "goi": "khong_ro", "available_cho": []} for m in co_ban]
        else:
            hop: dict[str, dict] = {}
            for tk in ds93:
                khoa = self._khoa_tu_session_key(tk.session_key)
                for m in tk.model or ten:
                    muc = hop.setdefault(m, {"khoa": [], "goi": []})
                    if khoa not in muc["khoa"]:
                        muc["khoa"].append(khoa)
                        muc["goi"].append(tk.goi)
            ra = []
            for m in ten:
                v = hop.get(m, {"khoa": [], "goi": []})
                goi_can = ("pro" if "pro" in v["goi"]
                           else "free" if "free" in v["goi"] else "khong_ro")
                ra.append({"id": f"{self._tien_to}/{m}", "object": "model",
                           "owned_by": "claude-web", "nhan": nhan.get(m, m),
                           "goi": goi_can, "available_cho": list(v["khoa"])})

        ra_dict: dict[str, dict] = {}
        for item in ra:
            m = item["id"].split("/", 1)[1] if "/" in item["id"] else item["id"]
            ra_dict[m] = item

        tat_ca_khoa = [self._khoa_tu_session_key(tk.session_key) for tk in ds93]
        for b in model_bo_sung.doc("claude-web"):
            if not isinstance(b, dict):
                continue
            mid = b.get("id")
            if not mid:
                continue
            ten_m = mid.split("/", 1)[-1].strip()
            nhan_m = b.get("nhan")
            if ten_m in ra_dict:
                if nhan_m:
                    ra_dict[ten_m]["nhan"] = nhan_m
            else:
                ra_dict[ten_m] = {
                    "id": f"{self._tien_to}/{ten_m}",
                    "object": "model",
                    "owned_by": "claude-web",
                    "nhan": nhan_m or ten_m,
                    "goi": "khong_ro",
                    "available_cho": list(tat_ca_khoa),
                }
        return list(ra_dict.values())

    async def lam_moi_model(self) -> None:
        """NV273: làm mới danh sách model (hợp đồng lõi, xem app/lam_moi_model.py).

        NGUỒN — TRUNG THỰC: claude.ai KHÔNG có endpoint liệt kê model cho
        cookie web trong repo này: GET /api/organizations chỉ trả tổ chức và
        capability «chat», không trả model; endpoint /v1/models của Anthropic
        cần API key, không phải cookie của module này, nên KHÔNG dùng làm
        nguồn và KHÔNG bịa endpoint. Nguồn là danh sách tĩnh đã khai trong
        manifest («model_tinh», khớp bảng của converter).
        Bước «lấy lại» là kiểm phiên thật của từng tài khoản qua
        GET /api/organizations đã có của module: ít nhất một phiên còn dùng
        được thì giữ danh sách tĩnh; mọi phiên đều hỏng thì ném RuntimeError
        mang mã lỗi rõ (xac_thuc / mang / sai_khuon) và KHÔNG xoá hay thay
        danh sách đang có. Chưa có tài khoản nào thì trả về im lặng (như
        chatgpt-web): models() vẫn trả bảng tĩnh.
        """
        hien = [m["id"] for m in await self.models()]
        khai = (self.manifest or {}).get("model_tinh")
        if khai is not None:
            if (not isinstance(khai, list) or not khai
                    or not all(isinstance(x, str) and x for x in khai)):
                raise RuntimeError(
                    "claude-web: sai_khuon — «model_tinh» trong manifest "
                    "không phải danh sách chuỗi khác rỗng; giữ danh sách cũ")
            if not set(khai) <= set(hien):
                raise RuntimeError(
                    "claude-web: sai_khuon — danh sách hiện tại thiếu model "
                    "đã khai trong manifest; giữ danh sách cũ")
        accs = list(self._accs)
        if not accs:
            return
        loi_xac_thuc = None
        loi_sai_khuon = None
        loi_mang = None
        for acc in accs:
            try:
                await acc.may_khach.lay_org_id()
                return  # ít nhất một phiên còn dùng được → giữ danh sách tĩnh
            except LoiClaudeWeb as e:
                if e.ma in (401, 403):
                    loi_xac_thuc = e
                elif "JSON hỏng" in (e.thong_bao or ""):
                    loi_sai_khuon = e
                else:
                    loi_mang = e
            except Exception as e:  # noqa: BLE001 — mạng lạ: coi là tạm thời
                loi_mang = e
        if loi_sai_khuon is not None:
            raise RuntimeError(
                "claude-web: sai_khuon — claude.ai trả JSON hỏng ở bước lấy "
                "tổ chức; giữ danh sách cũ") from loi_sai_khuon
        if loi_xac_thuc is not None:
            raise RuntimeError(
                "claude-web: xac_thuc — cookie bị claude.ai từ chối; "
                "giữ danh sách cũ") from loi_xac_thuc
        raise RuntimeError(
            "claude-web: mang — không nối được claude.ai lúc làm mới; "
            "giữ danh sách cũ") from loi_mang

    async def suc_khoe(self) -> dict:
        return {
            "ok": any(a.khoe for a in self._accs),
            "base_url": self._base_url,
            "tai_khoan": [{"cookie": a.ten_che, "khoe": a.khoe} for a in self._accs],
        }

    async def dung(self) -> None:
        """Đóng máy khách của mọi tài khoản."""
        for a in self._accs:
            await a.may_khach.dong()
        self._accs = []

    # ── chat ────────────────────────────────────────────────────────

    def _chon(self, da_thu: set[int]) -> _TaiKhoan | None:
        con_lai = [a for a in self._accs if id(a) not in da_thu]
        for a in con_lai:
            if a.khoe:
                return a
        # Không còn tài khoản khoẻ: cho tài khoản đã đánh dấu hỏng một cơ hội
        # hồi phục (cookie có thể chỉ bị từ chối thoáng qua).
        return con_lai[0] if con_lai else None

    async def chat(self, body: dict, stream: bool):
        body = body or {}
        try:
            prompt = cv.xay_dung_prompt(body.get("messages"))
        except cv.LoiYeuCau as e:
            return e.ma, {}, cv.goi_loi(e.thong_bao, "yeu_cau_sai")
        model = str(body.get("model") or "")
        if not model:
            return 400, {}, cv.goi_loi("Thiếu «model» (dạng clw/<tên>, ví dụ clw/sonnet).",
                                       "thieu_model")
        if not self._accs:
            return 401, {}, cv.goi_loi(
                "Module claude-web chưa có tài khoản nào — thêm cookie ở Hệ thống → AI.",
                "chua_co_tai_khoan")
        model_noi_bo = cv.giai_model(model, self._tien_to)

        da_thu: set[int] = set()
        loi_cuoi: LoiClaudeWeb | None = None
        for _ in range(min(GIOI_HAN_LUOT, len(self._accs))):
            acc = self._chon(da_thu)
            if acc is None:
                break
            da_thu.add(id(acc))
            try:
                return await self._chat_voi(acc, model, model_noi_bo, prompt, stream)
            except LoiClaudeWeb as e:
                loi_cuoi = e
                nhom = phan_loai_ma(e.ma)
                if nhom in ("xac_thuc", "han_muc"):
                    acc.khoe = False
                    if nhom == "xac_thuc":
                        # NV91-P2: lỗi phiên nhận ra bằng CẤU TRÚC (mã HTTP, không
                        # dò chữ) → tự khôi phục MỘT lần cho đúng tài khoản
                        # (single-flight); vòng lặp vẫn chuyển tài khoản kế ngay.
                        tu_lam_moi.bao_loi_phien(self, self._khoa_tu_cookie(acc.cookie))
                LOG.warning("claude-web: tài khoản %s lỗi HTTP %s (%s), chuyển tài khoản",
                            acc.ten_che, e.ma, nhom)
        return self._loi_tong(loi_cuoi)

    async def _chat_voi(self, acc: _TaiKhoan, model: str, model_noi_bo: str,
                        prompt: str, stream: bool):
        # Mọi lỗi trước khi có response 200 đều ném LoiClaudeWeb → chat() xoay
        # tài khoản. Sau khi đã trả 200 + generator, lỗi giữa luồng thành sự
        # kiện «error» (không [DONE]), không thử lại để khỏi hỏi Claude hai lần.
        org = await acc.may_khach.lay_org_id()
        conv = await acc.may_khach.tao_hoi_thoai(org)
        r = await acc.may_khach.mo_luong(org, conv, prompt, model_noi_bo)
        if stream:
            return 200, {"Content-Type": "text/event-stream",
                         "Cache-Control": "no-cache"}, self._luong(acc, r, model)
        van_ban: list[str] = []
        mo_hinh_that: str | None = None
        async for sk in acc.may_khach.doc_su_kien(r):
            m = cv.mo_hinh_that(sk)
            if m:
                mo_hinh_that = m
            van_ban.append(cv.trich_van_ban_sse(sk))
        acc.khoe = True
        return 200, {"Content-Type": "application/json"}, cv.phan_hoi_khong_stream(
            model, "".join(van_ban), mo_hinh_that)

    async def _luong(self, acc: _TaiKhoan, r: httpx.Response, model: str):
        ma_hoi = f"chatcmpl-{uuid.uuid4().hex}"
        luc = int(time.time())
        mo_hinh = model
        da_gui_role = False
        try:
            async for sk in acc.may_khach.doc_su_kien(r):
                m = cv.mo_hinh_that(sk)
                if m:
                    mo_hinh = m
                van_ban = cv.trich_van_ban_sse(sk)
                if van_ban:
                    delta = {"content": van_ban}
                    if not da_gui_role:
                        da_gui_role = True
                        delta = {"role": "assistant", **delta}
                    yield cv.chunk_sse(mo_hinh, ma_hoi, luc, delta)
            acc.khoe = True
            yield cv.chunk_sse(mo_hinh, ma_hoi, luc, {}, ket_thuc="stop")
            yield b"data: [DONE]\n\n"
        except (LoiClaudeWeb, httpx.HTTPError):
            # Lỗi giữa luồng: kết thúc bằng sự kiện «error», KHÔNG có [DONE],
            # và không ném tiếp — ném tiếp sẽ làm lõi cắt ngang kết nối của
            # khách. (CancelledError/GeneratorExit không bị bắt: vẫn lan ra
            # để việc dọn dẹp khi khách ngắt diễn ra đúng.)
            yield (b"data: " + json.dumps(
                {"error": {"message": "Luồng claude.ai bị ngắt giữa chừng.",
                           "type": "loi_luong"}},
                ensure_ascii=False).encode("utf-8") + b"\n\n")

    def _loi_tong(self, e: LoiClaudeWeb | None):
        ma = e.ma if e else 502
        nhom = phan_loai_ma(ma)
        if nhom == "xac_thuc":
            return 401, {}, cv.goi_loi(
                "Cookie claude.ai không hợp lệ hoặc hết hạn — đăng nhập lại claude.ai "
                "trên trình duyệt rồi dán lại cookie ở Hệ thống → AI.", "khoa_hong", 401)
        if nhom == "han_muc":
            return 429, {}, cv.goi_loi(
                "claude.ai báo hết hạn mức cho mọi tài khoản — đợi hạn mức hồi rồi thử lại.",
                "het_han_muc", 429)
        return 502, {}, cv.goi_loi(
            f"Dịch vụ claude.ai lỗi (HTTP {ma}) — thử lại sau.", "may_chu_loi", 502)
