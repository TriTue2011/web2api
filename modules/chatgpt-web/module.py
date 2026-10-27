"""Module chatgpt-web: trò chuyện với ChatGPT bản web (chatgpt.com).

Xác thực bằng Bearer access token: NGƯỜI DÙNG dán vào (trường ``tai_khoan``)
hoặc tái dùng hồ sơ Google đã đăng nhập qua ``tai_dung_ho_so`` (NV93: mở trang
đăng nhập trong hồ sơ → bấm «Đăng nhập bằng Google» → đọc access_token từ
cookie; không nhập lại email/mật khẩu/2FA; CAPTCHA/Cloudflare → can_nguoi).
Hỗ trợ nhiều token và tự chuyển tài khoản khi lỗi, phân loại lỗi bằng mã HTTP
(không dò chữ). Model định tuyến theo GÓI tài khoản (bảng MO_HINH_THEO_GOI —
CHƯA KIỂM trên dịch vụ thật); model không tài khoản nào có → lỗi rõ, không
fallback âm thầm.

Luồng giao tiếp học từ kho tham khảo chatgpt2api
(``services/openai_backend_api.py``, ``services/account_service.py``,
``services/codex_token_refresh.py``): GET /backend-api/me kiểm token →
POST /backend-api/conversation → bóc SSE. Xem client.py để biết chi tiết.
CHƯA KIỂM với dịch vụ thật.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.kho import che
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap
from app import tu_lam_moi
from app.tu_xa import kiem_url_lan

from app.lam_moi_model import TOI_DA_MODEL, chuan_hoa_ma_model, chuan_hoa_nhan

from .client import (
    MAY_CHU_MAC_DINH,
    MODEL_MAC_DINH,
    MO_HINH_HO_TRO,
    ChatGPTWebClient,
    LoiChatGPTWeb,
    LoiChanBot,
    LoiHanMuc,
    LoiHetGio,
    LoiMayChu,
    LoiXacThuc,
    ModelKhongCo,
    chuyen_thanh_tin_nhan,
    slug_cua_model,
    ten_tu_slug,
)
from .converter import (
    TIEN_TO,
    chunk_delta,
    chunk_ket_thuc,
    chunk_loi,
    chunk_vai_tro,
    dong_done,
    giai_quyet_model_day_du,
    id_day_du,
    phan_hoi_json,
    tach_tien_to,
    tao_hoan_thanh_id,
)
from .lam_moi_jwt import (
    NGUONG_NGAY_MAC_DINH as _JWT_NGUONG_MAC_DINH,
    TRAN_THU_NGAY_MAC_DINH as _JWT_TRAN_MAC_DINH,
    BoLamMoiJWT,
    con_lai_ngay as _jwt_con_lai_ngay,
    exp_cua as _jwt_exp_cua,
    het_han_iso as _jwt_het_han_iso,
)

log = logging.getLogger("web2api.module.chatgpt-web")

#: NV207: id module + thư mục module (đọc manifest khi cần trường cấu hình).
MODULE_ID = "chatgpt-web"
_THU_MUC_MODULE = Path(__file__).resolve().parent


class _TaiKhoan:
    """Một Bearer token: client tạo lười, trạng thái sức khoẻ do module giữ.

    NV93: bản ghi làm giàu thêm email/gói/hồ sơ (từ phiên), lưu vào kho riêng
    của module để khởi động lại không mất (xem Module._luu_ban_ghi_tai_khoan).
    """

    def __init__(self, token: str):
        self.token = token
        self.client: ChatGPTWebClient | None = None
        self.khoe = True
        self.loai_loi: str | None = None  # khoa_hong | het_han_muc | may_chu_loi | None
        # -- bản ghi NV93 (làm giàu từ phiên; không mạng thì trống) -----------
        self.email: str = ""
        self.ho_so: str | None = None
        self.goi: str = "khong_ro"  # free | plus | pro | khong_ro | ...
        self.trang_thai: str = "hoat_dong"
        self.lan_dung_cuoi: str | None = None
        self.model_that: list[dict] | None = None

    def ten_che(self) -> str:
        return che(self.token)

    def model_duoc(self) -> list[str]:
        """Tên thân thiện các model tài khoản này dùng được (ưu tiên nguồn thật khi có, ngược lại theo gói)."""
        if self.model_that:
            return [ten_tu_slug(m["slug"]) for m in self.model_that if isinstance(m, dict) and "slug" in m]
        return _model_theo_goi(self.goi)

    def ban_ghi(self, khoa: str) -> dict:
        """Bản ghi đầy đủ để lưu kho riêng (gồm phien — kho đã mã hoá, 0600)."""
        return {
            "khoa": khoa,
            "email": self.email,
            "ho_so": self.ho_so,
            "phien": self.token,
            "goi": self.goi,
            "trang_thai": self.trang_thai,
            "lan_dung_cuoi": self.lan_dung_cuoi,
            "model_that": self.model_that,
        }

    def ap_dung_ban_ghi(self, ban_ghi: dict) -> None:
        """Khôi phục trường làm giàu từ kho (không đụng token/client)."""
        if not isinstance(ban_ghi, dict):
            return
        email = ban_ghi.get("email")
        if isinstance(email, str) and email:
            self.email = email
        ho_so = ban_ghi.get("ho_so")
        if isinstance(ho_so, str) and ho_so:
            self.ho_so = ho_so
        goi = ban_ghi.get("goi")
        if isinstance(goi, str) and goi:
            self.goi = goi
        trang_thai = ban_ghi.get("trang_thai")
        if isinstance(trang_thai, str) and trang_thai:
            self.trang_thai = trang_thai
        lan = ban_ghi.get("lan_dung_cuoi")
        if isinstance(lan, str) and lan:
            self.lan_dung_cuoi = lan
        mt = ban_ghi.get("model_that")
        if isinstance(mt, list):
            ds_mt = []
            da_thay: set[str] = set()
            for it in mt:
                if not isinstance(it, dict):
                    continue
                s = chuan_hoa_ma_model(it.get("slug"))
                if s is None or s in da_thay:
                    continue
                da_thay.add(s)
                nhan = chuan_hoa_nhan(it.get("nhan"))
                ds_mt.append({"slug": s, "nhan": nhan})
                if len(ds_mt) >= TOI_DA_MODEL:
                    break
            if ds_mt:
                self.model_that = ds_mt


def _gio_utc() -> str:
    """Giờ hiện tại dạng ISO UTC (cho lan_dung_cuoi)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _so_thuc_duong(gia_tri: object, mac_dinh: float) -> float:
    """Số thực dương từ cấu hình; sai khuôn/không dương → mặc định + cảnh báo."""
    try:
        v = float(str(gia_tri).strip())
    except (TypeError, ValueError, AttributeError):
        v = mac_dinh
    if not (v > 0):
        if str(gia_tri or "").strip():
            log.warning("chatgpt-web: jwt_nguong_ngay %r sai khuôn — dùng mặc định %s",
                        gia_tri, mac_dinh)
        v = mac_dinh
    return v


def _so_nguyen_duong(gia_tri: object, mac_dinh: int) -> int:
    """Số nguyên dương từ cấu hình; sai khuôn/không dương → mặc định + cảnh báo."""
    try:
        v = int(float(str(gia_tri).strip()))
    except (TypeError, ValueError, AttributeError):
        v = mac_dinh
    if not (v > 0):
        if str(gia_tri or "").strip():
            log.warning("chatgpt-web: jwt_tran_thu_ngay %r sai khuôn — dùng mặc định %s",
                        gia_tri, mac_dinh)
        v = mac_dinh
    return v


# ---------------------------------------------------------------------------
# Bảng model theo gói (dự phòng khi nguồn thật thất bại) — CHƯA KIỂM trên dịch vụ thật.
#
# Suy từ web app công khai của ChatGPT (không dò chữ trong đáp án, chỉ là bảng
# tĩnh trong mã, dùng làm dự phòng khi gọi nguồn thật thất bại). OpenAI đổi gói/model bất cứ lúc nào — cập nhật bảng khi có
# đo thật. Gói lạ hoặc không rõ → cho phép toàn bộ (tài khoản vẫn dùng được
# khi cai() không có mạng; định tuyến lúc đó dựa vào lỗi 4xx thực tế).
# ---------------------------------------------------------------------------

MO_HINH_THEO_GOI: dict[str, tuple[tuple[str, str], ...]] = {
    "free": (
        ("4o-mini", "GPT-4o mini"),
        ("4.1-mini", "GPT-4.1 mini"),
    ),
    "plus": (
        ("4o-mini", "GPT-4o mini"),
        ("4.1-mini", "GPT-4.1 mini"),
        ("4o", "GPT-4o"),
        ("4.1", "GPT-4.1"),
        ("o3", "o3"),
        ("o4-mini", "o4-mini"),
    ),
    "pro": (
        ("4o-mini", "GPT-4o mini"),
        ("4.1-mini", "GPT-4.1 mini"),
        ("4o", "GPT-4o"),
        ("4.1", "GPT-4.1"),
        ("o3", "o3"),
        ("o4-mini", "o4-mini"),
    ),
}
_THU_TU_GOI = ("free", "plus", "pro")


def _model_theo_goi(goi: str | None) -> list[str]:
    """Tên thân thiện các model theo gói; gói lạ/không rõ → toàn bộ."""
    goi = str(goi or "").strip().lower()
    if goi in MO_HINH_THEO_GOI:
        return [ten for ten, _nhan in MO_HINH_THEO_GOI[goi]]
    thay = []
    for g in _THU_TU_GOI:
        for ten, _nhan in MO_HINH_THEO_GOI[g]:
            if ten not in thay:
                thay.append(ten)
    return thay


def _nhan_model(ten: str) -> str:
    """Nhãn hiển thị của model (tách khỏi id ổn định)."""
    for g in _THU_TU_GOI:
        for t, nhan in MO_HINH_THEO_GOI[g]:
            if t == ten:
                return nhan
    return ten


def _goi_toi_thieu(ten: str) -> str:
    """Gói rẻ nhất có model này (theo bảng tĩnh)."""
    for g in _THU_TU_GOI:
        if any(t == ten for t, _nhan in MO_HINH_THEO_GOI[g]):
            return g
    return "plus"


_QUY_TAC_TIN_HIEU = [{'loai': 'cookie', 'mien': 'chatgpt.com', 'ten': 'access_token', 'tin_hieu': 'da_dang_nhap'}, {'loai': 'url_chua', 'gia_tri': 'auth/login', 'tin_hieu': 'chua_dang_nhap'}]

class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None
        # NV207: cấu hình dịch vụ may-dang-nhap cho vòng làm mới JWT.
        self._dich_vu_dang_nhap_url: str | None = None
        self._khoa_dich_vu_dang_nhap: str = ""
        self._jwt_nguong_ngay: float = _JWT_NGUONG_MAC_DINH
        self._jwt_tran_thu_ngay: int = _JWT_TRAN_MAC_DINH
        # NV207: khoá single-flight cho từng khoá đang làm mới JWT.
        self._khoa_lam_moi_jwt: dict[str, object] = {}
        self._bo_lam_moi_jwt = BoLamMoiJWT(self)
        # Khoá tài khoản đã đánh dấu hỏng (tầng c bỏ qua khi chọn kế).
        self._hong: set[str] = set()
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV88):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None

    async def _lay_goi(self, token: str) -> str:
        """Gói tài khoản qua /backend-api/accounts/check (accounts.default.plan_type).

        Suy từ CẤU TRÚC JSON, không dò chữ. Không đọc được → "khong_ro"
        (tài khoản vẫn dùng được, định tuyến lúc đó cho phép toàn bộ).
        """
        try:
            goi = await self._tao_client(token).thong_tin_goi()
        except Exception:
            return "khong_ro"
        goi = str(goi or "").strip().lower()
        return goi or "khong_ro"

    # -- tái dùng hồ sơ Google (NV92 P3 → NV93) ---------------------------------

    _URL_TAI_DUNG = "https://chatgpt.com/auth/login"
    _BO_CHON_DANG_NHAP = 'button[data-testid="login-button"], a[href*="auth/login"]'
    # Đo trên trang đăng nhập THẬT (18/10/2026): nút Google là <button type="button"> KHÔNG có data-provider/aria-label, nằm đầu tiên trong
    # form của [data-testid="login-form"] (trước Apple, điện thoại). CSS không chọn theo chữ nên dùng vị trí cấu trúc làm dự phòng.
    _BO_CHON_GOOGLE = ('button[data-provider="google"], button[aria-label*="Google" i], '
                       '[data-testid="login-form"] form > div > button:first-of-type')
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare", "thu_thach_truy_cap"}

    async def tai_dung_ho_so(self, ho_so, trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập bằng Google TRONG hồ sơ đã đăng nhập Google (NV93).

        Hợp đồng NV92-P3: ``tai_dung_ho_so(ho_so, trinh_duyet)`` — module tự làm
        bước «Đăng nhập bằng Google» của nó trong hồ sơ, lấy phiên, làm giàu
        email/gói/hồ sơ, thêm tài khoản vào kho của CHÍNH mình, trả
        ``KetQuaDangNhap`` với ``cookie`` là chuỗi phiên thô (route tái dùng
        lưu nó qua đường cai() có sẵn). Không nhập lại email/mật khẩu/2FA —
        KHÔNG BAO GIỜ ``dien()``. Gặp CAPTCHA/Cloudflare/2FA/xác minh →
        can_nguoi NGAY (không cố vượt, không thử lại).

        HỌC TỪ c2a (chỉ đọc lấy hiểu, tự viết lại): TriTue2011/chatgpt2api,
        ``captcha-solver/src/chatgpt_login.py`` — các bước: mở trang đăng nhập
        → bấm «Log in» → bấm «Continue with Google» → chờ phiên hình thành →
        đọc access_token từ cookie chatgpt.com → kiểm qua /backend-api/me.
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
                # Bước «bấm Google»: đăng nhập bằng Google trong hồ sơ.
                await trinh_duyet.bam(self._BO_CHON_DANG_NHAP)
                await trinh_duyet.bam(self._BO_CHON_GOOGLE)
                # Bước «chờ phiên»: Google chuyển về với phiên đã đăng nhập.
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
                cookie = await trinh_duyet.doc_cookie("chatgpt.com")
            except Exception:
                cookie = {}
            token = cookie.get("access_token") if isinstance(cookie, dict) else None
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if not token:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        me = await self._kiem_token(token)
        if me is None:
            return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                  ma_trang_thai=401)
        email = str(me.get("email") or "") if isinstance(me, dict) else ""
        goi = await self._lay_goi(token)
        await self._them_tai_khoan_tu_ho_so(token, hs_id, email, goi)
        log.info("chatgpt-web: tái dùng hồ sơ %s → tài khoản %s (gói %s)",
                 hs_id, self._khoa_tu_token(token), goi)
        return KetQuaDangNhap(True, cookie=token)

    # -- liệt kê / gỡ tài khoản (NV93) ------------------------------------------

    def liet_ke_tai_khoan(self) -> list[dict]:
        """Danh sách tài khoản KHÔNG bí mật: khoa/email/ho_so/goi/trạng thái."""
        with self._khoa:
            ds = list(self._tai_khoan)
        return [{
            "khoa": self._khoa_tu_token(tk.token),
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
                       if self._khoa_tu_token(tk.token) != khoa]
            da_go = len(con_lai) != len(self._tai_khoan)
            self._tai_khoan = con_lai
        if da_go:
            tap = self._doc_go_bo()
            tap.add(khoa)
            self._luu_go_bo(tap)
            self._luu_ban_ghi_tai_khoan()
            log.info("chatgpt-web: đã gỡ tài khoản %s", khoa)
        return da_go

    # -- khoá tài khoản mờ (NV88) -------------------------------------------

    @staticmethod
    def _khoa_tu_token(token: str) -> str:
        """Khoá mờ của một token: «tk-» + 12 ký tự hex của SHA-256."""
        return "tk-" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]

    def _tim_token(self, khoa: str) -> str | None:
        with self._khoa:
            for tk in self._tai_khoan:
                if self._khoa_tu_token(tk.token) == khoa:
                    return tk.token
        return None

    def _them_token(self, token: str) -> None:
        with self._khoa:
            if not any(t.token == token for t in self._tai_khoan):
                self._tai_khoan.append(_TaiKhoan(token))
        # Thêm tường minh = người dùng muốn dùng lại → xoá khỏi sổ gỡ bỏ.
        self._xoa_khoi_go_bo(self._khoa_tu_token(token))

    # -- kho riêng: bản ghi làm giàu + sổ gỡ bỏ (NV93) -------------------------

    def _doc_ban_ghi(self) -> dict:
        """{khoa: bản ghi} từ kho riêng; kho chưa gán/hỏng → {} (không vỡ)."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("tai_khoan") or {}
        except Exception as e:
            log.warning("chatgpt-web: không đọc được bản ghi tài khoản: %s",
                        type(e).__name__)
            return {}
        ban_ghi = du_lieu.get("ban_ghi") if isinstance(du_lieu, dict) else None
        return ban_ghi if isinstance(ban_ghi, dict) else {}

    def _luu_ban_ghi_tai_khoan(self) -> None:
        """Lưu bản ghi (kèm phien — kho riêng đã mã hoá, 0600). Không vỡ."""
        if self.kho is None:
            return
        try:
            with self._khoa:
                ban_ghi = {self._khoa_tu_token(tk.token): tk.ban_ghi(self._khoa_tu_token(tk.token))
                           for tk in self._tai_khoan}
            self.kho.luu("tai_khoan", {"ban_ghi": ban_ghi})
        except Exception as e:
            log.warning("chatgpt-web: không lưu được bản ghi tài khoản: %s",
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
            log.warning("chatgpt-web: không lưu được sổ gỡ bỏ: %s",
                        type(e).__name__)

    def _xoa_khoi_go_bo(self, khoa: str) -> None:
        tap = self._doc_go_bo()
        if khoa in tap:
            tap.discard(khoa)
            self._luu_go_bo(tap)

    async def _them_tai_khoan_tu_ho_so(self, token: str, hs_id: str | None,
                                       email: str, goi: str) -> _TaiKhoan:
        """Thêm/cập nhật bản ghi từ phiên tái dùng hồ sơ (chống trùng token)."""
        with self._khoa:
            tk = next((t for t in self._tai_khoan if t.token == token), None)
            if tk is None:
                tk = _TaiKhoan(token)
                self._tai_khoan.append(tk)
            if email:
                tk.email = email
            if hs_id:
                tk.ho_so = hs_id
            tk.goi = goi or "khong_ro"
            tk.trang_thai = "hoat_dong"
            tk.khoe = True
            tk.loai_loi = None
            tk.lan_dung_cuoi = _gio_utc()
        self._xoa_khoi_go_bo(self._khoa_tu_token(token))
        self._luu_ban_ghi_tai_khoan()
        return tk

    # -- mật khẩu đã lưu (NV88) ----------------------------------------------

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
        """Lưu mật khẩu theo cả khoá-token và email (dang_nhap_lai chỉ có email)."""
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
            log.warning("chatgpt-web: không lưu được mật khẩu: %s", type(e).__name__)

    def co_tai_khoan(self, khoa: str) -> bool:
        """Module có biết khoá/email này không?"""
        khoa = str(khoa or "").strip().lower()
        if self._tim_token(khoa) is not None:
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
        return f"chatgpt-web-{bam}"



    # -- đăng nhập theo hợp đồng NV67 (NV88 mở rộng) ---------------------------

    _URL_DANG_NHAP = "https://chatgpt.com/auth/login"

    async def _kiem_token(self, token: str) -> dict | None:
        """Kiểm token qua /backend-api/me; hỏng → None (không ném)."""
        try:
            me = await self._tao_client(token).init()
        except LoiXacThuc:
            return None
        except LoiChatGPTWeb:
            return None
        except Exception:
            return None
        return me if isinstance(me, dict) else {}

    async def _dang_nhap_bang_trinh_duyet(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet) -> KetQuaDangNhap:
        """Điền form đăng nhập trên trình duyệt; đọc access_token từ cookie."""
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
            tin_hieu = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
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
            cookie = await trinh_duyet.doc_cookie("chatgpt.com")
        except Exception:
            cookie = {}
        token = cookie.get("access_token") if isinstance(cookie, dict) else None
        if not token:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        me = await self._kiem_token(token)
        if me is None:
            return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                  ma_trang_thai=401)
        self._them_token(token)
        khoa = self._khoa_tu_token(token)
        self._luu_mat_khau(khoa, email, mat_khau)
        return KetQuaDangNhap(True, cookie=token)

    async def dang_nhap(self, tai_khoan: DongDaTach) -> KetQuaDangNhap:
        """Hợp đồng NV67: cookie → kiểm; mat_khau → trình duyệt; dang_nhap_lai →
        mật khẩu đã lưu. BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        try:
            if loai == "cookie":
                token = tai_khoan.cookie.lay() if tai_khoan.cookie else ""
                if not token:
                    return KetQuaDangNhap(False, ma_loi="thieu_cookie")
                me = await self._kiem_token(token)
                if me is None:
                    return KetQuaDangNhap(False, ma_trang_thai=401)
                self._them_token(token)
                return KetQuaDangNhap(True, cookie=token)
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

    # -- các tầng khôi phục NV88 ------------------------------------------------

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a): kiểm token còn sống bằng /backend-api/me.

        chatgpt.com không có endpoint làm mới token công khai (kho tham khảo
        cũng phải đăng nhập lại khi token chết) — tầng này chỉ kiểm tra.
        """
        token = self._tim_token(khoa)
        if token is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        try:
            me = await self._tao_client(token).init()
        except LoiXacThuc:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han",
                                  ma_trang_thai=401)
        except LoiChatGPTWeb as e:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_may_chu",
                                  ma_trang_thai=e.ma)
        except Exception:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_mang")
        if not isinstance(me, dict):
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_module")
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
            ds = [self._khoa_tu_token(tk.token) for tk in self._tai_khoan]
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
        with self._khoa:
            return [self._khoa_tu_token(tk.token) for tk in self._tai_khoan]

    def danh_dau_khoe(self, khoa: str) -> None:
        """NV91-P2: tài khoản khoẻ lại sau tự khôi phục → bỏ đánh dấu hỏng."""
        self._hong.discard(str(khoa))

    # -- tự làm mới JWT (NV207) ------------------------------------------------
    # Hợp đồng với vòng nền app/lam_moi_jwt.py: `lam_moi_jwt(khoa) -> dict`
    # (không ném; dict có `ma` nêu kết quả). Các móc nối gạch dưới do
    # modules/chatgpt-web/lam_moi_jwt.py gọi qua getattr.

    async def lam_moi_jwt(self, khoa: str) -> dict:
        """NV207: một lượt làm mới JWT cho một khoá (vòng nền gọi)."""
        try:
            return await self._bo_lam_moi_jwt.lam_moi(khoa)
        except Exception as e:  # không để vòng nền vỡ vì một khoá
            log.warning("chatgpt-web: lam_moi_jwt(%s) lỗi không ngờ: %s",
                        khoa, type(e).__name__)
            return {"ok": False, "khoa": str(khoa), "ma": "loi_noi_bo"}

    def _tim__tai_khoan_obj(self, khoa: str):
        """_TaiKhoan có khoá mờ == khoa, hoặc None."""
        khoa = str(khoa or "")
        with self._khoa:
            for tk in self._tai_khoan:
                if self._khoa_tu_token(tk.token) == khoa:
                    return tk
        return None

    def _truong_cau_hinh_jwt(self) -> list:
        """Định nghĩa trường cấu hình từ manifest (dùng cho ghep/luu_cai_dat)."""
        global _TRUONG_CAU_HINH_CACHE
        try:
            return _TRUONG_CAU_HINH_CACHE
        except NameError:
            pass
        try:
            with open(_THU_MUC_MODULE / "manifest.json", encoding="utf-8") as f:
                man = json.load(f)
            _TRUONG_CAU_HINH_CACHE = man.get("truong_cau_hinh") or []
        except Exception:
            _TRUONG_CAU_HINH_CACHE = []
        return _TRUONG_CAU_HINH_CACHE

    def _thay_token_jwt(self, khoa_cu: str, token_moi: str) -> dict:
        """Thay token cũ bằng token mới BẰNG ĐƯỜNG cai {giu, them}.

        Đọc-ghép-lưu trong khoá module (không để hai lượt làm mới đè nhau):
        đọc cấu hình đã lưu → `kho.ghep_cau_hinh` với {giu: mọi chỉ số trừ
        mục cũ, them: [token mới]} → `kho.luu_cai_dat`. Rồi cập nhật bộ nhớ
        + bản ghi kho riêng. Không log token.
        """
        from app import kho as kho_loi
        khoa_cu = str(khoa_cu or "")
        token_moi = str(token_moi or "")
        if not token_moi:
            return {"ok": False, "ma_loi": "token_moi_rong"}
        with self._khoa:
            ds_khoa = [self._khoa_tu_token(tk.token) for tk in self._tai_khoan]
            if khoa_cu not in ds_khoa:
                return {"ok": False, "ma_loi": "token_da_doi"}
            token_cu = self._tai_khoan[ds_khoa.index(khoa_cu)].token
            if token_cu == token_moi:
                return {"ok": False, "ma_loi": "token_da_doi"}
            # Đọc-ghép-lưu cấu hình đã lưu (đường {giu, them} của kho).
            try:
                truong = self._truong_cau_hinh_jwt()
                muc = None
                ds_mod, _canh_bao = kho_loi.doc_modules()
                for m in ds_mod or []:
                    if isinstance(m, dict) and m.get("id") == MODULE_ID:
                        muc = m
                        break
                da_luu = kho_loi.doc_cau_hinh(MODULE_ID, truong, muc) \
                    if muc else {}
                cu = da_luu.get("tai_khoan")
                cu = list(cu) if isinstance(cu, list) else []
                try:
                    idx_luu = cu.index(token_cu)
                except ValueError:
                    idx_luu = None
                if idx_luu is None:
                    # Cấu hình lưu lệch khỏi bộ nhớ (ai đó vừa sửa tay):
                    # giữ nguyên đã lưu + thêm mới (ghep tự khử trùng).
                    giu = list(range(len(cu)))
                else:
                    giu = [i for i in range(len(cu)) if i != idx_luu]
                ghep = kho_loi.ghep_cau_hinh(
                    truong, da_luu,
                    {"tai_khoan": {"giu": giu, "them": [token_moi]}})
                kho_loi.luu_cai_dat(MODULE_ID, truong, ghep)
            except Exception as e:
                log.warning("lam-moi-jwt: lưu cấu hình sau thay token lỗi: %s",
                            type(e).__name__)
                return {"ok": False, "ma_loi": "loi_luu_cau_hinh"}
            # Cập nhật bộ nhớ (giữ email/gói/hồ sơ đã làm giàu).
            ds_khoa = [self._khoa_tu_token(tk.token) for tk in self._tai_khoan]
            if khoa_cu not in ds_khoa:  # ai đó đã thay trong lúc mình lưu
                return {"ok": False, "ma_loi": "token_da_doi"}
            tk = self._tai_khoan[ds_khoa.index(khoa_cu)]
            tk.token = token_moi
            tk.client = None
            tk.khoe = True
            tk.loai_loi = None
        self._luu_ban_ghi_tai_khoan()
        return {"ok": True, "khoa_moi": self._khoa_tu_token(token_moi)}

    def gan_jwt_tu_dang_nhap(self, email: str, token: str, ho_so: str | None = None) -> dict:
        """NV210 (điều phối nối): đưa JWT vừa đăng nhập thành công vào khoá sổ và GẮN ``ho_so``.

        Nhận diện tài khoản bằng EMAIL (claim trong JWT / email của chính tài khoản vừa đăng nhập —
        dữ liệu chắc chắn, không đoán chuỗi). Đúng MỘT khoá cùng email → thay token cũ bằng token mới
        (``_thay_token_jwt``: đọc-ghép-lưu cấu hình bền trong khoá module); chưa có khoá nào → thêm mới;
        NHIỀU khoá cùng email → từ chối (mơ hồ, không đoán). Rồi gắn email/hồ sơ vào bản ghi để NV207
        làm mới được về sau. Không log token. Trả {ok, khoa?, thay_the?, ma_loi?}."""
        token = str(token or "").strip()
        email_chuan = str(email or "").strip().lower()
        if not token or not email_chuan:
            return {"ok": False, "ma_loi": "thieu_du_lieu"}
        with self._khoa:
            da_co = any(tk.token == token for tk in self._tai_khoan)
            khop = [tk for tk in self._tai_khoan
                    if (tk.email or "").strip().lower() == email_chuan and tk.token != token]
            khoa_cu = self._khoa_tu_token(khop[0].token) if len(khop) == 1 else None
        if len(khop) > 1:
            return {"ok": False, "ma_loi": "mo_ho_nhieu_khoa_cung_email"}
        thay_the = False
        if not da_co:
            if khoa_cu is not None:
                kq = self._thay_token_jwt(khoa_cu, token)
                if not kq.get("ok"):
                    return {"ok": False, "ma_loi": kq.get("ma_loi") or "loi_thay_token"}
                thay_the = True
            else:
                kq = self._them_token_ben(token)
                if not kq.get("ok"):
                    return {"ok": False, "ma_loi": kq.get("ma_loi") or "loi_them_token"}
        with self._khoa:
            tk = next((t for t in self._tai_khoan if t.token == token), None)
            if tk is None:
                return {"ok": False, "ma_loi": "khong_thay_khoa_sau_khi_gan"}
            if not tk.email:
                tk.email = email_chuan
            if ho_so:
                tk.ho_so = str(ho_so)
        self._luu_ban_ghi_tai_khoan()
        return {"ok": True, "khoa": self._khoa_tu_token(token), "thay_the": thay_the}

    def _them_token_ben(self, token: str) -> dict:
        """Thêm MỘT token mới vào cấu hình bền (giữ nguyên các token đang có) rồi vào bộ nhớ.
        Cùng đường đọc-ghép-lưu với ``_thay_token_jwt``; không log token."""
        from app import kho as kho_loi
        with self._khoa:
            try:
                truong = self._truong_cau_hinh_jwt()
                muc = None
                ds_mod, _canh_bao = kho_loi.doc_modules()
                for m in ds_mod or []:
                    if isinstance(m, dict) and m.get("id") == MODULE_ID:
                        muc = m
                        break
                da_luu = kho_loi.doc_cau_hinh(MODULE_ID, truong, muc) if muc else {}
                cu = da_luu.get("tai_khoan")
                cu = list(cu) if isinstance(cu, list) else []
                ghep = kho_loi.ghep_cau_hinh(
                    truong, da_luu,
                    {"tai_khoan": {"giu": list(range(len(cu))), "them": [token]}})
                kho_loi.luu_cai_dat(MODULE_ID, truong, ghep)
            except Exception as e:
                log.warning("chatgpt-web: lưu cấu hình khi thêm khoá lỗi: %s", type(e).__name__)
                return {"ok": False, "ma_loi": "loi_luu_cau_hinh"}
            if not any(t.token == token for t in self._tai_khoan):
                self._tai_khoan.append(_TaiKhoan(token))
        self._xoa_khoi_go_bo(self._khoa_tu_token(token))
        return {"ok": True}

    def _doc_trang_thai_jwt(self) -> dict:
        """Sổ trạng thái làm mới JWT (kho riêng, không bí mật)."""
        if self.kho is None:
            return {}
        try:
            tt = self.kho.doc("jwt_lam_moi") or {}
        except Exception:
            return {}
        return tt if isinstance(tt, dict) else {}

    def _luu_trang_thai_jwt(self, tt: dict) -> None:
        if self.kho is None:
            return
        try:
            self.kho.luu("jwt_lam_moi", dict(tt))
        except Exception as e:
            log.warning("lam-moi-jwt: không lưu được trạng thái: %s",
                        type(e).__name__)

    def _sau_lam_moi_jwt(self, khoa_moi: str, email_moi: str,
                         ho_so: str | None) -> None:
        """Làm giàu bản ghi sau làm mới (email đọc từ claim token mới)."""
        tk = self._tim__tai_khoan_obj(khoa_moi)
        if tk is None:
            return
        with self._khoa:
            if email_moi and not tk.email:
                tk.email = email_moi
            if ho_so and not tk.ho_so:
                tk.ho_so = ho_so
        self._luu_ban_ghi_tai_khoan()

    def jwt_trang_thai(self) -> dict:
        """NV207: trạng thái JWT từng khoá cho giao diện (không bí mật).

        Mỗi mục: khoa (mờ), email, het_han (ISO từ exp), con_lai_ngay,
        lam_moi {trang_thai, lan_lam_moi, ma_loi}, canh_bao (còn < ngưỡng
        mà chưa làm mới được).
        """
        from .lam_moi_jwt import con_lai_ngay as _con_lai, exp_cua as _exp, \
            het_han_iso as _iso
        nguong = float(getattr(self, "_jwt_nguong_ngay", 2.0) or 2.0)
        tt = self._doc_trang_thai_jwt()
        ra = []
        with self._khoa:
            ds = list(self._tai_khoan)
        for tk in ds:
            khoa = self._khoa_tu_token(tk.token)
            exp = _exp(tk.token)
            con_lai = _con_lai(exp)
            muc = tt.get(khoa) if isinstance(tt.get(khoa), dict) else {}
            trang_thai = str(muc.get("trang_thai") or "")
            da_lam_moi = trang_thai == "da_lam_moi"
            canh_bao = bool(exp is not None and con_lai is not None
                            and con_lai <= nguong and not da_lam_moi)
            ra.append({
                "khoa": khoa,
                "email": tk.email,
                "het_han": _iso(exp),
                "con_lai_ngay": round(con_lai, 2) if con_lai is not None else None,
                "lam_moi": {
                    "trang_thai": trang_thai or None,
                    "lan_lam_moi": muc.get("lan_lam_moi"),
                    "ma_loi": muc.get("ma_loi"),
                },
                "canh_bao": canh_bao,
            })
        return {"khoa": ra, "nguong_ngay": nguong}

    # -- tạo client: mối nối để test chích máy chủ giả ---------------------

    def _tao_client(self, token: str) -> ChatGPTWebClient:
        return ChatGPTWebClient(token, base_url=self._co_so_url or MAY_CHU_MAC_DINH)

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách token.

        NV93: bỏ qua token đã bị go_tai_khoan gỡ (sổ go_bo trong kho riêng);
        khôi phục email/gói/hồ sơ đã làm giàu từ bản ghi kho riêng (không cần
        mạng). Làm giàu lại khi có mạng ở cai().
        """
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("tai_khoan") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        go_bo = self._doc_go_bo()
        ban_ghi = self._doc_ban_ghi()
        tai_khoan = []
        for t in danh_sach:
            if not isinstance(t, str) or not str(t).strip():
                continue
            token = str(t).strip()
            khoa = self._khoa_tu_token(token)
            if khoa in go_bo:
                continue  # đã bị gỡ tường minh — không dựng lại
            tk = _TaiKhoan(token)
            tk.ap_dung_ban_ghi(ban_ghi.get(khoa))
            tai_khoan.append(tk)
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        # NV207: cấu hình vòng làm mới JWT (số sai khuôn → mặc định + cảnh báo).
        url_dn = str(cau_hinh.get("dich_vu_dang_nhap_url") or "").strip().rstrip("/") or None
        khoa_dn = str(cau_hinh.get("khoa_dich_vu_dang_nhap") or "")
        nguong = _so_thuc_duong(cau_hinh.get("jwt_nguong_ngay"), _JWT_NGUONG_MAC_DINH)
        tran = _so_nguyen_duong(cau_hinh.get("jwt_tran_thu_ngay"), _JWT_TRAN_MAC_DINH)
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._co_so_url = co_so
            self._dich_vu_dang_nhap_url = url_dn
            self._khoa_dich_vu_dang_nhap = khoa_dn
            self._jwt_nguong_ngay = nguong
            self._jwt_tran_thu_ngay = tran
        log.info("chatgpt-web: đã nạp %d tài khoản", len(tai_khoan))

    async def _kiem_co_so_url(self) -> None:
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                raise ValueError(f"co_so_url không hợp lệ: {e}") from None

    async def _init_tai_khoan(self, tk: _TaiKhoan) -> None:
        """Kiểm token + làm giàu email/gói khi mạng cho phép.

        Không mạng/không đọc được gói → tài khoản vẫn dùng được, email/gói
        giữ nguyên (hoặc "khong_ro"). Không bao giờ lộ token trong log.
        """
        if tk.client is None:
            tk.client = self._tao_client(tk.token)
        me = await tk.client.init()
        if isinstance(me, dict):
            email = str(me.get("email") or "").strip()
            if email and not tk.email:
                tk.email = email
        if not tk.goi or tk.goi == "khong_ro":
            tk.goi = await self._lay_goi(tk.token)
        try:
            kq = await tk.client.lay_model()
            if kq:
                tk.model_that = kq
        except Exception as e:
            log.warning("chatgpt-web: không lấy được model ở _init_tai_khoan cho %s: %s",
                        tk.ten_che(), type(e).__name__)
        tk.trang_thai = "hoat_dong"
        self._luu_ban_ghi_tai_khoan()

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng token bằng /backend-api/me (rẻ, không tốn lượt chat)."""
        self.nap(cau_hinh)
        try:
            await self._kiem_co_so_url()
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        # NV207: kiểm URL dịch vụ may-dang-nhap bằng cùng bộ kiểm SSRF của lõi.
        if self._dich_vu_dang_nhap_url:
            try:
                self._dich_vu_dang_nhap_url = await kiem_url_lan(
                    self._dich_vu_dang_nhap_url)
            except ValueError as e:
                return {"ok": False, "ket_qua_tung_khoa": [],
                        "loi": f"dich_vu_dang_nhap_url không hợp lệ: {e}"}
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập token nào (trường tai_khoan)."}
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
                                "loi": "Token bị từ chối (401/403) — dán lại token mới."})
                log.warning("chatgpt-web: tài khoản %s bị từ chối ở bước cài", tk.ten_che())
            except LoiChatGPTWeb as e:
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
                log.warning("chatgpt-web: tài khoản %s lỗi không ngờ ở bước cài: %s",
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
        ds_token = cau_hinh.get("tai_khoan") or []
        luu = {"tai_khoan": [ds_token[i] for i in tot if i < len(ds_token)]}
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
        log.warning("chatgpt-web: tài khoản #%d (%s) lỗi %s, chuyển tài khoản",
                    chi_so, tk.ten_che(), loai)
        if loai == "khoa_hong":
            # NV91-P2: lỗi phiên nhận ra bằng CẤU TRÚC (LoiXacThuc = 401/403, không
            # dò chữ) → kích hoạt tự khôi phục MỘT lần cho ĐÚNG tài khoản này
            # (single-flight trong app/tu_lam_moi; module vẫn chuyển tài khoản kế ngay).
            tu_lam_moi.bao_loi_phien(self, self._khoa_tu_token(tk.token))

    def _tai_khoan_cho_model(self, ten_than_thien: str) -> list[_TaiKhoan]:
        """Tài khoản khoẻ trước, CHỈ những tài khoản có model này (theo gói).

        Không có → danh sách rỗng; người gọi trả lỗi rõ, KHÔNG fallback âm thầm
        sang model khác.
        """
        with self._khoa:
            ds = list(self._tai_khoan)
        co = [tk for tk in ds if ten_than_thien in tk.model_duoc()]
        return sorted(co, key=lambda t: 0 if t.khoe else 1)

    def _model_mac_dinh_kha_dung(self) -> str | None:
        """Model mặc định cho yêu cầu rỗng/auto: 4o nếu tài khoản nào có,
        ngược lại model đầu của tài khoản khoẻ nhất; không có tài khoản → None."""
        ds = self._thu_tu_tai_khoan()
        if not ds:
            return None
        for tk in ds:
            if MODEL_MAC_DINH in tk.model_duoc():
                return MODEL_MAC_DINH
        for tk in ds:
            ds_model = tk.model_duoc()
            if ds_model:
                return ds_model[0]
        return None

    def co_tai_khoan_email(self, email: str) -> bool:
        """Module có token của tài khoản mang email đó không (so sánh casefold)."""
        em = str(email or "").casefold()
        if not em:
            return False
        with self._khoa:
            return any(str(tk.email or "").casefold() == em for tk in self._tai_khoan)

    async def chat(self, body: dict, stream: bool, *,
                   tai_khoan_email: str | None = None):
        body = dict(body or {})
        model_goc = str(body.get("model") or "")
        yeu_cau_trong = not model_goc.strip() or model_goc.strip().lower() == "auto"
        with self._khoa:
            ds_tk = list(self._tai_khoan)
        biet_them = set()
        for tk in ds_tk:
            if tk.model_that:
                for m in tk.model_that:
                    if isinstance(m, dict) and "slug" in m:
                        biet_them.add(ten_tu_slug(m["slug"]))
                        biet_them.add(m["slug"])
        try:
            ten_model = giai_quyet_model_day_du(model_goc, biet_them=biet_them)
        except ModelKhongCo:
            dang_co = [m["id"] for m in await self.models()]
            thong_diep = (f"model {nhac_lai(tach_tien_to(model_goc))} không có; "
                          f"chọn trong: {', '.join(dang_co) if dang_co else ', '.join(id_day_du(m) for m in MO_HINH_HO_TRO)}")
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, thong_diep), ensure_ascii=False).encode("utf-8")
        tat_ca = self._thu_tu_tai_khoan()
        if not tat_ca:
            return 401, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(401, "chatgpt-web chưa có tài khoản nào: "
                                         "thêm token ở trường tai_khoan rồi cài lại."),
                           ensure_ascii=False).encode("utf-8")
        if yeu_cau_trong:
            mac_dinh = self._model_mac_dinh_kha_dung()
            if mac_dinh is not None:
                ten_model = mac_dinh
        ds = self._tai_khoan_cho_model(ten_model)
        if not ds:
            # Định tuyến theo gói: không tài khoản nào có model này → lỗi rõ,
            # KHÔNG chạy model khác thay thế.
            model_id = id_day_du(ten_model)
            dang_co = [m["id"] for m in await self.models()]
            thong_diep = (
                f"model {model_id} không khả dụng: không tài khoản chatgpt-web "
                f"nào đang có model này (cần gói {_goi_toi_thieu(ten_model)} trở lên). "
                f"Các model đang có: {', '.join(dang_co) or 'không có'}.")
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, thong_diep), ensure_ascii=False).encode("utf-8")

        if tai_khoan_email is not None:
            em_ghim = str(tai_khoan_email).casefold()
            ds = [tk for tk in ds if str(tk.email or "").casefold() == em_ghim]
            if not ds:
                return 503, {"Content-Type": "application/json"}, \
                    json.dumps(than_loi(503, "không còn tài khoản chatgpt-web cho email này dùng được"),
                               ensure_ascii=False).encode("utf-8")

        model_id = id_day_du(ten_model)
        tin_nhan = chuyen_thanh_tin_nhan(body)
        if not tin_nhan:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        slug_da_giai = slug_cua_model(ten_model)
        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(ds, tin_nhan, model_id, ten_model, slug_da_giai=slug_da_giai)

        loi_cuoi: LoiChatGPTWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                van_ban = await tk.client.generate_content(tin_nhan, ten_model, slug_da_giai=slug_da_giai)
            except LoiChanBot as e:
                # Chặn lượt tự động: không phải lỗi khoá → không đánh dấu hỏng,
                # không thử tiếp các khoá khác (cùng bị chặn, chỉ tốn lượt).
                loi_cuoi = e
                break
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
                log.warning("chatgpt-web: lỗi không ngờ ở tài khoản #%d: %s", i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                continue
            tk.khoe = True
            tk.loai_loi = None
            tk.lan_dung_cuoi = _gio_utc()
            self._luu_ban_ghi_tai_khoan()
            return 200, {"Content-Type": "application/json"}, phan_hoi_json(model_id, van_ban)

        # Hết tài khoản: mã theo lỗi cuối (không bao giờ lộ token hay đáp án thô).
        ma = loi_cuoi.ma if loi_cuoi else 502
        hd_tra = {"Content-Type": "application/json"}
        if isinstance(loi_cuoi, LoiChanBot):
            hd_tra["X-Web2api-Loi-Loai"] = "chan_bot"
        thong_diep = {
            401: "Mọi tài khoản chatgpt-web đều bị từ chối — token hết hạn hoặc không hợp lệ, dán lại.",
            403: "chatgpt.com chặn lượt chat tự động — không phải lỗi token. Dùng module openaicodex hoặc khoá API để chat ChatGPT.",
            429: "Mọi tài khoản chatgpt-web đều bị giới hạn lượt — thử lại sau.",
            504: "chatgpt.com không phản hồi kịp.",
        }.get(ma, "chatgpt.com lỗi phía máy chủ.")
        return ma, hd_tra, \
            json.dumps(than_loi(ma, thong_diep), ensure_ascii=False).encode("utf-8")

    async def _luong_chat(self, ds: list[_TaiKhoan], tin_nhan: list[dict],
                          model_id: str, ten_model: str, slug_da_giai: str | None = None):
        """SSE OpenAI; xoay tài khoản ngay trong luồng khi tài khoản đầu hỏng."""
        hoan_thanh_id = tao_hoan_thanh_id()
        tao_luc = int(time.time())
        yield chunk_vai_tro(model_id, hoan_thanh_id, tao_luc)
        loi_cuoi: LoiChatGPTWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                da_ra_du_lieu = False
                async for delta in tk.client.generate_content_stream(tin_nhan, ten_model, slug_da_giai=slug_da_giai):
                    da_ra_du_lieu = True
                    yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)
            except LoiChanBot as e:
                loi_cuoi = e
                break
            except LoiXacThuc as e:
                self._danh_dau_loi(tk, "khoa_hong", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break  # đã ra dữ liệu thì không trộn hai tài khoản vào một luồng
                continue
            except LoiChatGPTWeb as e:
                self._danh_dau_loi(tk, "het_han_muc" if isinstance(e, LoiHanMuc) else "may_chu_loi", i)
                loi_cuoi = e
                if da_ra_du_lieu:
                    break
                continue
            except Exception as e:
                log.warning("chatgpt-web: lỗi không ngờ khi stream ở tài khoản #%d: %s",
                            i, type(e).__name__)
                loi_cuoi = LoiMayChu(f"lỗi không ngờ: {type(e).__name__}")
                break
            tk.khoe = True
            tk.loai_loi = None
            tk.lan_dung_cuoi = _gio_utc()
            self._luu_ban_ghi_tai_khoan()
            yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc)
            yield dong_done()
            return
        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản chatgpt-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / lam_moi_model / suc_khoe / dung ----------------------------

    async def lam_moi_model(self) -> None:
        """Lấy lại model từ nguồn thật GET /backend-api/models cho từng tài khoản."""
        with self._khoa:
            ds = list(self._tai_khoan)
        if not ds:
            return
        co_thanh_cong = False
        for tk in ds:
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                kq = await tk.client.lay_model()
                if kq:
                    tk.model_that = kq
                    co_thanh_cong = True
            except Exception as e:
                log.warning("chatgpt-web: không lấy được model cho %s: %s",
                            tk.ten_che(), type(e).__name__)
        if co_thanh_cong:
            self._luu_ban_ghi_tai_khoan()
        else:
            raise RuntimeError("không lấy được model từ nguồn")

    async def models(self):
        """HỢP model theo tài khoản: mỗi model có id ổn định «chatgpt-web/<tên>»,
        nhãn hiển thị «nhan» tách riêng, «goi» và «available_cho»."""
        with self._khoa:
            ds = list(self._tai_khoan)
        hop: dict[str, list[str]] = {}
        nhan_that: dict[str, str] = {}
        co_that: dict[str, list[str]] = {}

        for tk in ds:
            khoa = self._khoa_tu_token(tk.token)
            if tk.model_that:
                for m in tk.model_that:
                    if not isinstance(m, dict) or "slug" not in m:
                        continue
                    slug = m["slug"]
                    ten = ten_tu_slug(slug)
                    hop.setdefault(ten, [])
                    if khoa not in hop[ten]:
                        hop[ten].append(khoa)
                    if m.get("nhan") and ten not in nhan_that:
                        nhan_that[ten] = m["nhan"]
                    co_that.setdefault(ten, []).append(tk.goi)
            else:
                for ten in tk.model_duoc():
                    hop.setdefault(ten, [])
                    if khoa not in hop[ten]:
                        hop[ten].append(khoa)

        thu_tu_chuan = _model_theo_goi(None)  # thứ tự chuẩn của bảng
        cac_ten = []
        for ten in thu_tu_chuan:
            if ten in hop and ten not in cac_ten:
                cac_ten.append(ten)
        for ten in hop:
            if ten not in cac_ten:
                cac_ten.append(ten)

        GOI_BAC = {"free": 1, "plus": 2, "pro": 3}
        ra = []
        for ten in cac_ten:
            nhan = nhan_that.get(ten) or _nhan_model(ten) or ten
            if ten in co_that:
                ds_goi = [g for g in co_that[ten] if g in GOI_BAC]
                if ds_goi:
                    goi_nho_nhat = min(ds_goi, key=lambda g: GOI_BAC[g])
                else:
                    goi_nho_nhat = "khong_ro"
            else:
                goi_nho_nhat = _goi_toi_thieu(ten)

            ra.append({
                "id": id_day_du(ten),
                "object": "model",
                "created": 1760000000,
                "owned_by": "chatgpt-web",
                "nhan": nhan,
                "goi": goi_nho_nhat,
                "available_cho": hop[ten],
            })
        return ra

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
                            "so_model": 0, "loi": str(e)})
            except LoiChatGPTWeb as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e)})
            except Exception as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": type(e).__name__})
            else:
                muc.append({"khoa": tk.ten_che(), "ok": True, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": len(tk.model_duoc()), "loi": None,
})
        ok = any(m["ok"] for m in muc)
        return {"ok": ok, "khoa": muc,
                "ghi_chu": None if ds else "chưa cấu hình tài khoản nào"}

    async def dung(self):
        with self._khoa:
            ds = list(self._tai_khoan)
            self._tai_khoan = []
        for tk in ds:
            if tk.client is not None:
                try:
                    await tk.client.close()
                except Exception as e:
                    # Ghi nhật ký thay vì nuốt lặng (test NV45a chốt danh sách
                    # nuốt lỗi: điểm mới chưa kiểm toán sẽ làm đỏ).
                    log.warning("chatgpt-web: lỗi khi đóng client ở bước dung: %s",
                                type(e).__name__)
