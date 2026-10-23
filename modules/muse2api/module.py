"""Module muse2api — Muse (muse.ai) qua giao thức trực tiếp.

Xác thực bằng cookie của tab muse.ai đã đăng nhập — miễn phí, không cần
API key. Luồng: cookie → access_token → hatch token →
WebSocket Noise XX tới máy ảo → POST /chat/stream + poll /chat/history.

Hai đường lấy cookie: (1) NGƯỜI DÙNG dán (đường cũ); (2) tái dùng hồ sơ
Google đã đăng nhập qua ``tai_dung_ho_so`` (NV93 — thử «Đăng nhập bằng
Google» trong hồ sơ; NV90-C: muse.ai dùng SSO Meta nên có thể không có
Google SSO → ghi rõ «khong_ho_tro», giữ đường cookie/làm mới hiện có).

Thay thế bản Model API trả phí trước đây (giữ lại ở branch `model-api`
của repo TriTue2011/muse2api).

Khả năng: chat (stream / không stream). Giao thức nội bộ của web app đã
được reverse-engineer, không chính thức — Meta đổi web có thể làm module
ngừng hoạt động.

Tự đăng nhập + khôi phục (NV90, theo mẫu chatgpt-web/NV88): hợp đồng 8 hàm
`dang_nhap`, `lam_moi_phien`, `dang_nhap_tu_dong`, `tai_khoan_ke`,
`danh_dau_hong`, `mat_khau_da_luu`, `co_tai_khoan`, `ho_so_trinh_duyet`.
muse.ai đăng nhập qua SSO của Meta — KHÔNG có form email/mật khẩu để tự
điền, nên đường làm mới/đăng nhập lại khả thi duy nhất là kiểm lại cookie
(tầng a); tầng (b) trả `can_nguoi` kèm hướng dẫn mở noVNC (trung thực, không
đoán luồng không có). Cookie phiên giữ nguyên cơ chế hiện có (chuỗi cookie
trong `_TaiKhoan`, kiểm bằng bootstrap).

NV230: tầng (a) khi bootstrap TỪ CHỐI cookie còn thử tự lấy cookie MỚI từ
workspace Firefox ĐÃ GẮN với khoá (bản ghi NV93) qua dịch vụ trình duyệt —
xem `lam_moi_cookie.py`. Đây là đường đọc cookie từ hồ sơ trình duyệt bền
đã có sẵn của hệ thống (client vnc-xa), không phải lấy cookie lén từ máy
người dùng.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap

from .chat import ChatSession, build_prompt
from .lam_moi_cookie import BoLamMoiCookie
from .protocol import AuthError, Gateway, GatewayError, fetch_access_token, fetch_session_info

log = logging.getLogger("web2api.module.muse2api")

MODEL_ID = "muse"

#: ID module trong sổ cấu hình của lõi (khớp manifest ``id``).
MODULE_ID = "muse2api"
_THU_MUC_MODULE = Path(__file__).resolve().parent
_TRUONG_CAU_HINH_CACHE: list | None = None


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi module về mã trung lập của lõi."""
    if ma in (401, 403):
        return "khoa_hong"  # cookie sai / hết hạn
    if ma == 429:
        return "het_han_muc"
    if ma == 400:
        return "yeu_cau_sai"
    if ma is not None and ma >= 500:
        return "may_chu_loi"
    return None


def _ds_cookie(gia_tri) -> list[str]:
    # Cookie header chứa dấu cách sau ';' nên KHÔNG dùng ds_khoa
    # (nó tách theo khoảng trắng và làm vỡ cookie).
    if isinstance(gia_tri, str):
        gia_tri = [gia_tri]
    if not isinstance(gia_tri, list):
        return []
    return [c.strip() for c in gia_tri if isinstance(c, str) and c.strip()]


def _che(cookie: str) -> str:
    """Chỉ hiện dạng …abcd (4 ký tự cuối của GIÁ TRỊ cookie đầu).

    Không hiện tên cookie lẫn 4 ký tự đầu; giá trị ≤ 8 ký tự → …
    """
    dau = cookie.split(";")[0].strip()
    if "=" in dau:
        _, v = dau.split("=", 1)
        v = v.strip()
        return f"…{v[-4:]}" if len(v) > 8 else "…"
    return f"…{cookie[-4:]}" if len(cookie) > 8 else "…"


def _sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


class _LoiModule(Exception):
    """Lỗi đã map sẵn thành tuple kết quả chuẩn (status, headers, body)."""

    def __init__(self, status: int, message: str, type_: str):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"message": message, "type": type_}}

    def ket_qua(self):
        return self.status, {"content-type": "application/json"}, \
            json.dumps(self.payload, ensure_ascii=False).encode()


class _TaiKhoan:
    """Một bộ cookie + kết nối Gateway duy trì. Khóa theo tài khoản vì
    lịch sử chat dùng chung và Noise decrypt không chịu được đọc xen kẽ."""

    def __init__(self, ten: str, cookies: str):
        self.ten = ten
        self.cookies = cookies
        self.khoa = threading.Lock()
        self.node_id = secrets.token_hex(8)
        self._gw: Gateway | None = None
        self.khoe = True

    def dam_bao(self) -> Gateway:
        if self._gw is None:
            self._gw = Gateway(self.cookies)
        return self._gw

    def bo(self):
        gw, self._gw = self._gw, None
        if gw is not None:
            try:
                gw.close()
            except Exception:  # noqa: BLE001 - best effort
                pass


def _hatch_sess_tu_cookie(cookies: str) -> str | None:
    """Trích giá trị cookie ``hatch_sess`` khỏi chuỗi cookie header.

    Không có → None (cookie dán cũ không thuộc đường NV93).
    """
    for phan in str(cookies or "").split(";"):
        ten, _, gia_tri = phan.partition("=")
        if ten.strip() == "hatch_sess" and gia_tri.strip():
            return gia_tri.strip()
    return None


class _TaiKhoanNV93:
    """Tài khoản muse2api từ đường tái dùng hồ sơ Google (NV93).

    ``hatch_sess`` là BÍ MẬT — chỉ đi trong cookie tới muse.ai, không bao giờ
    vào log, phản hồi API, nhật ký hay thông điệp lỗi. Bản ghi lưu kho riêng
    KHÔNG mang giá trị thô (chỉ siêu dữ liệu làm giàu).
    """

    def __init__(self, hatch_sess: str):
        self.hatch_sess = str(hatch_sess)
        self.email: str = ""
        self.ho_so: str | None = None
        #: muse.ai miễn phí — chưa có khái niệm gói trong giao thức hiện tại.
        self.goi: str = "khong_ro"
        self.trang_thai: str = "hoat_dong"
        self.lan_dung_cuoi: str | None = None

    def ban_ghi(self, khoa: str) -> dict:
        """Bản ghi làm giàu lưu kho riêng (KHÔNG bí mật thô)."""
        return {
            "khoa": khoa,
            "email": self.email,
            "ho_so": self.ho_so,
            "goi": self.goi,
            "trang_thai": self.trang_thai,
            "lan_dung_cuoi": self.lan_dung_cuoi,
        }

    def ap_dung_ban_ghi(self, bg: dict | None) -> None:
        if not isinstance(bg, dict):
            return
        for truong in ("email", "goi", "trang_thai", "lan_dung_cuoi"):
            v = bg.get(truong)
            if isinstance(v, str) and v:
                setattr(self, truong, v)
        hs = bg.get("ho_so")
        if isinstance(hs, str) and hs:
            self.ho_so = hs


_QUY_TAC_TIN_HIEU = [{'loai': 'cookie', 'mien': 'muse.ai', 'ten': 'hatch_sess', 'tin_hieu': 'da_dang_nhap'}, {'loai': 'url_chua', 'gia_tri': 'muse.ai/login', 'tin_hieu': 'chua_dang_nhap'}]

class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._accs: list[_TaiKhoan] = []
        self._vi_tri = 0
        self._khoa_chon = threading.Lock()
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._hong: set[str] = set()  # khoá tài khoản tầng (c) đã đánh dấu hỏng
        # Tài khoản từ đường tái dùng hồ sơ Google (NV93): khoá «tk-<băm>».
        self._tai_khoan_nv93: list[_TaiKhoanNV93] = []
        # NV230: bộ tự lấy cookie MỚI từ workspace Firefox đã gắn với khoá.
        self._bo_lam_moi_cookie = BoLamMoiCookie(self)
        self._khoa_thay_cookie = threading.Lock()
        self._dich_vu_trinh_duyet_url = ""
        self._khoa_dich_vu_trinh_duyet = ""
        self._cookie_tran_thu_ngay = 5

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    # ── Vòng đời ────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng.

        NV93: bỏ qua cookie đã bị go_tai_khoan gỡ (sổ go_bo trong kho riêng);
        khôi phục email/hồ sơ đã làm giàu từ bản ghi kho riêng.

        NV230: cấu hình dịch vụ trình duyệt cho bộ tự lấy cookie mới
        (để trống = tắt, giữ hành vi hết hạn như cũ).
        """
        self._dich_vu_trinh_duyet_url = str(
            cau_hinh.get("dich_vu_trinh_duyet_url") or "").strip()
        self._khoa_dich_vu_trinh_duyet = str(
            cau_hinh.get("khoa_dich_vu_trinh_duyet") or "")
        try:
            self._cookie_tran_thu_ngay = int(float(
                cau_hinh.get("cookie_tran_thu_ngay") or 5))
        except (TypeError, ValueError):
            self._cookie_tran_thu_ngay = 5
        if self._cookie_tran_thu_ngay <= 0:
            self._cookie_tran_thu_ngay = 5
        cookies = _ds_cookie(cau_hinh.get("cookies"))
        go_bo = self._doc_go_bo_nv93()
        ban_ghi = self._doc_ban_ghi_nv93()
        accs = []
        tai_khoan_nv93 = []
        for i, c in enumerate(cookies):
            khoa93 = self._khoa_nv93_tu_cookie(c)
            if khoa93 is not None and khoa93 in go_bo:
                continue  # đã bị gỡ tường minh — không dựng lại
            accs.append(_TaiKhoan(f"acc{i + 1}", c))
            if khoa93 is not None:
                bg = ban_ghi.get(khoa93)
                if isinstance(bg, dict):
                    hs = _hatch_sess_tu_cookie(c)
                    if hs:
                        tk93 = _TaiKhoanNV93(hs)
                        tk93.ap_dung_ban_ghi(bg)
                        tai_khoan_nv93.append(tk93)
        self._accs = accs
        self._tai_khoan_nv93 = tai_khoan_nv93
        self._vi_tri = 0

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng bộ cookie bằng HTTPS bootstrap (auth/check + session):
        không tốn tiền, không cần chat thật."""
        cookies = _ds_cookie(cau_hinh.get("cookies"))
        if not cookies:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập cookie nào (trường cookies). Lấy từ tab muse.ai "
                           "đã đăng nhập: DevTools (F12) → Application → Cookies."}
        kqs = await asyncio.gather(
            *[asyncio.to_thread(self._kiem_cookie, c) for c in cookies])
        ds, giu = [], []
        co_dat = False
        for c, (ok, loi, bi_tu_choi) in zip(cookies, kqs):
            muc = {"khoa": _che(c), "ok": ok, "loi": loi}
            ds.append(muc)
            if ok:
                co_dat = True
                giu.append(c)
            elif not bi_tu_choi:
                # Lỗi tạm: vẫn giữ trong cấu hình lưu
                giu.append(c)
        if not co_dat:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": "Không cookie nào đăng nhập được muse.ai — đăng nhập lại "
                           "trên trình duyệt rồi export lại cookie."}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"cookies": giu}}

    @staticmethod
    def _kiem_cookie(cookies: str) -> tuple[bool, str | None, bool]:
        """Kiểm cookie. Trả về (ok, loi, bi_tu_choi)."""
        try:
            fetch_access_token(cookies)
            fetch_session_info(cookies)
            return True, None, False
        except AuthError as e:
            return False, f"cookie không hợp lệ/hết hạn: {e}", True
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}", False

    async def models(self) -> list[dict]:
        """Model duy nhất của muse.ai, kèm nhãn/goi/available_cho theo tài
        khoản NV93 (mã id ổn định, nhãn tách riêng — NV91-P3)."""
        khoa_ds = [self._khoa_tu_hatch_sess(tk.hatch_sess)
                   for tk in self._tai_khoan_nv93]
        return [{"id": f"{self.tien_to}/{MODEL_ID}", "object": "model",
                 "owned_by": "muse2api", "kha_nang": ["chat"],
                 "nhan": "Muse", "goi": "khong_ro",
                 "available_cho": khoa_ds}]

    async def suc_khoe(self) -> dict:
        async def _kiem(acc: _TaiKhoan):
            ok, loi, _ = await asyncio.to_thread(self._kiem_cookie, acc.cookies)
            return {"khoa": _che(acc.cookies), "ok": ok, "loi": loi,
                    "dang_dung": acc._gw is not None}
        ds = await asyncio.gather(*[_kiem(a) for a in self._accs]) if self._accs else []
        return {"ok": any(d["ok"] for d in ds), "khoa": ds}

    async def dung(self) -> None:
        for acc in self._accs:
            acc.bo()

    # ── Tự đăng nhập + khôi phục theo hợp đồng NV67/NV88 (NV90) ──────────

    @staticmethod
    def _khoa_tu_cookies(cookies: str) -> str:
        """Khoá mờ của một bộ cookie: «ms-» + 12 ký tự hex của SHA-256."""
        return "ms-" + hashlib.sha256(str(cookies or "").encode("utf-8")).hexdigest()[:12]

    def _tim_acc(self, khoa: str) -> _TaiKhoan | None:
        """Tài khoản khớp khoá mờ; None nếu không có."""
        khoa = str(khoa or "")
        for acc in self._accs:
            if self._khoa_tu_cookies(acc.cookies) == khoa:
                return acc
        return None

    def mat_khau_da_luu(self, khoa: str) -> BiMatTam | None:
        """muse.ai đăng nhập qua SSO của Meta — không có form mật khẩu nên
        module không lưu mật khẩu: luôn None (fail-closed, lõi báo cần người)."""
        return None

    def co_tai_khoan(self, khoa: str) -> bool:
        """Module có biết khoá này không?"""
        return self._tim_acc(khoa) is not None

    def quy_tac_tin_hieu(self) -> list:
        """Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): module KHAI, mã tín hiệu MỞ.

        Dịch vụ thực thi: cookie đặc trưng có/không, URL đích — không dò chữ.
        Chỉ NHẬN RA thử thách → can_nguoi (không tự vượt).
        """
        return [dict(q) for q in _QUY_TAC_TIN_HIEU]
    def ho_so_trinh_duyet(self, khoa: str) -> str:
        """ID workspace cho noVNC: đúng khuôn chữ/số/gạch ngang/gạch dưới."""
        bam = hashlib.sha256(str(khoa).encode("utf-8")).hexdigest()[:16]
        return f"muse2api-{bam}"



    async def dang_nhap(self, tai_khoan: DongDaTach) -> KetQuaDangNhap:
        """Hợp đồng NV67: cookie → kiểm bằng bootstrap; mat_khau/dang_nhap_lai
        → can_nguoi (muse.ai không có form mật khẩu để tự điền — cần người đăng
        nhập tay qua noVNC). BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        try:
            if loai == "cookie":
                cookies = tai_khoan.cookie.lay() if tai_khoan.cookie else ""
                if not cookies:
                    return KetQuaDangNhap(False, ma_loi="thieu_cookie")
                ok, _loi, bi_tu_choi = await asyncio.to_thread(
                    self._kiem_cookie, cookies)
                if ok:
                    if self._tim_acc(self._khoa_tu_cookies(cookies)) is None:
                        self._accs.append(
                            _TaiKhoan(f"acc{len(self._accs) + 1}", cookies))
                    return KetQuaDangNhap(True, cookie=cookies)
                if bi_tu_choi:
                    return KetQuaDangNhap(False, ma_trang_thai=401)
                return KetQuaDangNhap(False, ma_loi="loi_may_chu")
            if loai in ("mat_khau", "dang_nhap_lai"):
                return KetQuaDangNhap(False, ma_loi="can_nguoi")
            return KetQuaDangNhap(False, ma_loi="loai_khong_ho_tro")
        finally:
            tai_khoan.xoa_bi_mat()

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a): kiểm lại cookie bằng bootstrap (rẻ, không tốn tiền).

        NV230: bootstrap TỪ CHỐI cookie (401/AuthError) → thử tự lấy cookie
        MỚI từ workspace Firefox ĐÃ GẮN với khoá (xem lam_moi_cookie.py)
        trước khi tuyên bố hết hạn. Cần người (hồ sơ đang mở/không có
        hatch_sess) → can_nguoi ngay ở tầng (a); lỗi tạm → giữ het_han như
        cũ (bộ làm mới đã ghi nghỉ luỹ thừa, không thử lại mãi).
        """
        acc = self._tim_acc(khoa)
        if acc is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        try:
            ok, _loi, bi_tu_choi = await asyncio.to_thread(
                self._kiem_cookie, acc.cookies)
        except Exception as e:
            # Lỗi lạ khi kiểm → thất bại đóng (không coi như còn sống).
            log.warning("muse2api: kiểm lại cookie lỗi không ngờ: %s",
                        type(e).__name__)
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_mang")
        if ok:
            acc.khoe = True
            return KetQuaKhoiPhuc(True, tang="a")
        acc.khoe = False
        if bi_tu_choi:
            kq_lm = await self._bo_lam_moi_cookie.lam_moi(khoa)
            if kq_lm.get("ok"):
                acc.khoe = True
                return KetQuaKhoiPhuc(True, tang="a",
                                      khoa_moi=kq_lm.get("khoa_moi"))
            if kq_lm.get("can_nguoi"):
                return KetQuaKhoiPhuc(
                    False, tang="a", ma_loi="can_nguoi", can_nguoi=True,
                    huong_dan=str(kq_lm.get("huong_dan") or ""))
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han",
                                  ma_trang_thai=401)
        return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_may_chu")

    async def dang_nhap_tu_dong(self, tai_khoan: TaiKhoanCanKhoiPhuc,
                                trinh_duyet) -> KetQuaKhoiPhuc:
        """Tầng (b): muse.ai đăng nhập qua SSO của Meta — không có form mật
        khẩu để tự điền → can_nguoi kèm hướng dẫn mở noVNC."""
        try:
            return KetQuaKhoiPhuc(
                False, tang="b", ma_loi="can_nguoi", can_nguoi=True,
                huong_dan=("muse.ai đăng nhập qua tài khoản Meta (SSO), không có "
                            "form mật khẩu để tự điền. Hãy mở noVNC của workspace "
                            "rồi đăng nhập tay; xong bấm «Đã xử lý xong»."))
        finally:
            if tai_khoan.mat_khau is not None:
                tai_khoan.mat_khau.xoa()

    def tai_khoan_ke(self, khoa: str) -> str | None:
        """Tầng (c): khoá kế còn khoẻ (chưa bị đánh dấu hỏng); hết → None."""
        ds = [self._khoa_tu_cookies(a.cookies) for a in self._accs]
        khoa = str(khoa)
        con_khoe = [k for k in ds if k != khoa and k not in self._hong]
        return con_khoe[0] if con_khoe else None

    def danh_dau_hong(self, khoa: str, ma_loi: str) -> None:
        """Đánh dấu tài khoản hỏng để tầng (c) bỏ qua."""
        self._hong.add(str(khoa))

    # ── NV230: tự lấy cookie mới từ workspace Firefox đã gắn ─────────────

    def khoa_tai_khoan(self) -> list[str]:
        """Mọi khoá tài khoản hiện có — hợp đồng vòng tự làm mới của lõi
        (VongTuLamMoi quét các khoá này rồi gọi lam_moi_phien từng khoá)."""
        return [self._khoa_tu_cookies(a.cookies) for a in self._accs]

    def danh_dau_khoe(self, khoa: str) -> None:
        """Đánh dấu một khoá khoẻ trở lại (vòng nền gọi sau khi thấy khoá
        dùng được, vd chat 200)."""
        acc = self._tim_acc(khoa)
        if acc is not None:
            acc.khoe = True

    def _ho_so_gan_voi_khoa(self, khoa: str) -> str | None:
        """Hồ sơ Firefox GẮN CHẮC CHẮN với khoá, hoặc None.

        Ghép qua chính hatch_sess của cookie hiện tại → bản ghi kho riêng
        NV93 (trường ``ho_so`` do tai_dung_ho_so ghi). KHÔNG đoán từ tên
        hồ sơ/email. Không gắn → None (bộ làm mới không làm mới bừa).
        """
        acc = self._tim_acc(khoa)
        if acc is None:
            return None
        hs = _hatch_sess_tu_cookie(acc.cookies)
        if not hs:
            return None
        khoa93 = self._khoa_tu_hatch_sess(hs)
        for tk in self._tai_khoan_nv93:
            if self._khoa_tu_hatch_sess(tk.hatch_sess) == khoa93 and tk.ho_so:
                return tk.ho_so
        bg = self._doc_ban_ghi_nv93().get(khoa93)
        if isinstance(bg, dict):
            ho_so = bg.get("ho_so")
            if isinstance(ho_so, str) and ho_so:
                return ho_so
        return None

    def _truong_cau_hinh_cookie(self) -> list:
        """Định nghĩa trường cấu hình từ manifest (đệm) — cho _thay_cookie."""
        global _TRUONG_CAU_HINH_CACHE
        if _TRUONG_CAU_HINH_CACHE is not None:
            return _TRUONG_CAU_HINH_CACHE
        try:
            with open(_THU_MUC_MODULE / "manifest.json",
                      encoding="utf-8") as f:
                man = json.load(f)
            _TRUONG_CAU_HINH_CACHE = man.get("truong_cau_hinh") or []
        except Exception:
            _TRUONG_CAU_HINH_CACHE = []
        return _TRUONG_CAU_HINH_CACHE

    def _thay_cookie(self, khoa_cu: str, cookie_moi: str) -> dict:
        """Thay cookie của khoá BẰNG ĐƯỜNG cai {giu, them} (như NV207).

        Đọc-ghép-lưu trong khoá module (không để hai lượt làm mới đè nhau):
        đọc cấu hình đã lưu → `kho.ghep_cau_hinh` với {giu: mọi chỉ số trừ
        mục cũ, them: [cookie mới]} → `kho.luu_cai_dat`. Rồi cập nhật bộ nhớ
        (đóng Gateway cũ dựng bằng cookie cũ) + bản ghi NV93 đi theo
        hatch_sess mới (giữ email/hồ sơ đã gắn). Không log cookie.
        """
        from app import kho as kho_loi
        khoa_cu = str(khoa_cu or "")
        cookie_moi = str(cookie_moi or "").strip()
        if not cookie_moi:
            return {"ok": False, "ma_loi": "cookie_moi_rong"}
        with self._khoa_thay_cookie:
            acc = self._tim_acc(khoa_cu)
            if acc is None or acc.cookies == cookie_moi:
                # Khoá đã đổi chủ trong lúc làm mới (lượt khác nhanh hơn).
                return {"ok": False, "ma_loi": "cookie_da_doi"}
            cookie_cu = acc.cookies
            # Đọc-ghép-lưu cấu hình đã lưu (đường {giu, them} của kho).
            try:
                truong = self._truong_cau_hinh_cookie()
                muc = None
                ds_mod, _canh_bao = kho_loi.doc_modules()
                for m in ds_mod or []:
                    if isinstance(m, dict) and m.get("id") == MODULE_ID:
                        muc = m
                        break
                da_luu = kho_loi.doc_cau_hinh(MODULE_ID, truong, muc) \
                    if muc else {}
                cu = da_luu.get("cookies")
                cu = list(cu) if isinstance(cu, list) else []
                try:
                    idx_luu = cu.index(cookie_cu)
                except ValueError:
                    idx_luu = None
                if idx_luu is None:
                    # Cấu hình lưu lệch khỏi bộ nhớ: giữ nguyên + thêm mới.
                    giu = list(range(len(cu)))
                else:
                    giu = [i for i in range(len(cu)) if i != idx_luu]
                ghep = kho_loi.ghep_cau_hinh(
                    truong, da_luu,
                    {"cookies": {"giu": giu, "them": [cookie_moi]}})
                kho_loi.luu_cai_dat(MODULE_ID, truong, ghep)
            except Exception as e:
                log.warning("muse2api: lưu cấu hình sau thay cookie lỗi: %s",
                            type(e).__name__)
                return {"ok": False, "ma_loi": "loi_luu_cau_hinh"}
            # Cập nhật bộ nhớ (tìm lại: ai đó có thể đã thay trong lúc lưu).
            acc = self._tim_acc(khoa_cu)
            if acc is None:
                return {"ok": False, "ma_loi": "cookie_da_doi"}
            sess_cu = _hatch_sess_tu_cookie(cookie_cu)
            sess_moi = _hatch_sess_tu_cookie(cookie_moi)
            acc.cookies = cookie_moi
            acc.bo()  # Gateway cũ giữ cookie cũ — bỏ để dựng lại
            acc.khoe = True
            if sess_cu and sess_moi and sess_cu != sess_moi:
                for tk in self._tai_khoan_nv93:
                    if tk.hatch_sess == sess_cu:
                        tk.hatch_sess = sess_moi
                        tk.trang_thai = "hoat_dong"
                self._luu_ban_ghi_nv93()
        return {"ok": True, "khoa_moi": self._khoa_tu_cookies(cookie_moi)}

    # -- sổ trạng thái làm mới cookie (kho riêng, KHÔNG bí mật) -----------

    def _doc_trang_thai_cookie(self) -> dict:
        """{khoa: trạng thái làm mới} từ kho riêng; chưa gán/hỏng → {}."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("lam_moi_cookie") or {}
        except Exception:
            return {}
        tt = du_lieu.get("trang_thai") if isinstance(du_lieu, dict) else None
        return tt if isinstance(tt, dict) else {}

    def _luu_trang_thai_cookie(self, tt: dict) -> None:
        """Lưu sổ trạng thái làm mới cookie (chỉ số đếm/thời điểm/mã lỗi)."""
        if self.kho is None:
            return
        try:
            self.kho.luu("lam_moi_cookie", {"trang_thai": tt})
        except Exception as e:
            log.warning("muse2api: không lưu được trạng thái làm mới "
                        "cookie: %s", type(e).__name__)

    # ── tái dùng hồ sơ Google (NV93) ─────────────────────────────────────

    _URL_TAI_DUNG = "https://muse.ai/"
    _BO_CHON_GOOGLE = ('button[aria-label*="Google" i], '
                       'a[href*="google" i]')
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare"}

    @staticmethod
    def _khoa_tu_hatch_sess(hatch_sess: str) -> str:
        """Khoá mờ ổn định của một tài khoản NV93: «tk-» + 12 hex SHA-256.

        Hợp đồng NV93: khoá mọi module web đều dạng ``tk-<băm>``.
        """
        return "tk-" + hashlib.sha256(
            str(hatch_sess or "").encode("utf-8")).hexdigest()[:12]

    def _khoa_nv93_tu_cookie(self, cookies: str) -> str | None:
        """Khoá NV93 của một chuỗi cookie; không có hatch_sess → None."""
        hs = _hatch_sess_tu_cookie(cookies)
        return self._khoa_tu_hatch_sess(hs) if hs else None

    async def tai_dung_ho_so(self, ho_so, trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập bằng Google TRONG hồ sơ đã đăng nhập Google (NV93).

        Hợp đồng NV92-P3: module thử bước «Đăng nhập bằng Google» của nó trong
        hồ sơ, lấy cookie ``hatch_sess``, thêm tài khoản vào kho của CHÍNH
        mình, trả ``KetQuaDangNhap`` với ``cookie`` là chuỗi cookie đã chuẩn
        hoá. Không nhập lại email/mật khẩu/2FA — KHÔNG BAO GIỜ ``dien()``.
        Gặp CAPTCHA/Cloudflare/2FA/xác minh → can_nguoi NGAY (không cố vượt).

        NV90-C: muse.ai dùng SSO Meta — có thể KHÔNG có Google SSO. Hết lượt
        chờ mà không vào được → «khong_ho_tro» rõ ràng (không đoán, không thử
        lại); giữ nguyên đường cookie dán + làm mới/đăng nhập lại hiện có.

        CHƯA KIỂM trên muse.ai thật: chưa rõ nút Google SSO có tồn tại không;
        phiên hatch_sess đọc được kiểm thực ở lượt chat đầu (đường _kiem_cookie
        có sẵn), chưa kiểm trong lượt tái dùng này.
        """
        hs_id = ho_so.get("id") if isinstance(ho_so, dict) else str(ho_so or "")
        hs_id = str(hs_id or "").strip() or None
        email = str(ho_so.get("email") or "") if isinstance(ho_so, dict) else ""
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            # Đi tắt: hồ sơ đã có phiên muse.ai còn hiệu lực.
            await trinh_duyet.mo_trang(self._URL_TAI_DUNG)
            tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
            if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                # CAPTCHA/Cloudflare/2FA/xác minh: DỪNG ngay, không cố vượt.
                return KetQuaDangNhap(False, ma_loi="can_nguoi")
            if "da_dang_nhap" not in tin_hieu:
                # Thử «Đăng nhập bằng Google» trong hồ sơ (có thể không có).
                await trinh_duyet.bam(self._BO_CHON_GOOGLE)
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
                    # NV90-C: không qua được Google SSO (SSO Meta?) → ghi rõ.
                    return KetQuaDangNhap(False, ma_loi="khong_ho_tro")
            try:
                cookie = await trinh_duyet.doc_cookie("muse.ai")
            except Exception:
                cookie = {}
            hatch_sess = (cookie.get("hatch_sess")
                          if isinstance(cookie, dict) else None)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if not hatch_sess:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        hatch_sess = str(hatch_sess)
        khoa = self._khoa_tu_hatch_sess(hatch_sess)
        self._them_tai_khoan_nv93(hatch_sess, hs_id, email)
        # Log chỉ khoá mờ — KHÔNG BAO GIỜ log hatch_sess thô.
        log.info("muse2api: tái dùng hồ sơ %s → tài khoản %s", hs_id, khoa)
        return KetQuaDangNhap(True, cookie="hatch_sess=" + hatch_sess)

    def _them_tai_khoan_nv93(self, hatch_sess: str, hs_id: str | None,
                             email: str) -> _TaiKhoanNV93:
        """Thêm/cập nhật tài khoản NV93 (chống trùng hatch_sess).

        Đồng thời thêm vào _accs để chat() xoay được; lưu bản ghi làm giàu
        (không bí mật thô) vào kho riêng.
        """
        hatch_sess = str(hatch_sess)
        tk = next((t for t in self._tai_khoan_nv93
                   if t.hatch_sess == hatch_sess), None)
        if tk is None:
            tk = _TaiKhoanNV93(hatch_sess)
            self._tai_khoan_nv93.append(tk)
        if email:
            tk.email = str(email)
        if hs_id:
            tk.ho_so = hs_id
        tk.trang_thai = "hoat_dong"
        tk.lan_dung_cuoi = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        khoa = self._khoa_tu_hatch_sess(hatch_sess)
        self._xoa_khoi_go_bo_nv93(khoa)
        chuoi = "hatch_sess=" + hatch_sess
        if not any(a.cookies == chuoi for a in self._accs):
            self._accs.append(_TaiKhoan(f"nv93-{khoa}", chuoi))
        self._luu_ban_ghi_nv93()
        return tk

    # -- liệt kê / gỡ tài khoản (NV93) ------------------------------------------

    def liet_ke_tai_khoan(self) -> list[dict]:
        """Danh sách tài khoản NV93 KHÔNG bí mật: khoa/email/ho_so/goi/trạng thái.

        Tài khoản dán cookie cũ vẫn quản lý qua Hệ thống → AI (đường cai()/
        nap() có sẵn); hàm này theo hợp đồng NV93 mới.
        """
        return [{
            "khoa": self._khoa_tu_hatch_sess(tk.hatch_sess),
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
                 if self._khoa_tu_hatch_sess(tk.hatch_sess) == khoa]
        if not bi_go:
            return False
        self._tai_khoan_nv93 = [tk for tk in self._tai_khoan_nv93
                                if self._khoa_tu_hatch_sess(tk.hatch_sess)
                                != khoa]
        # Gỡ khỏi _accs (chat) để không dùng phiên đã gỡ.
        for tk in bi_go:
            chuoi = "hatch_sess=" + tk.hatch_sess
            self._accs = [a for a in self._accs if a.cookies != chuoi]
        tap = self._doc_go_bo_nv93()
        tap.add(khoa)
        self._luu_go_bo_nv93(tap)
        self._luu_ban_ghi_nv93()
        log.info("muse2api: đã gỡ tài khoản %s", khoa)
        return True

    # -- kho riêng: bản ghi làm giàu + sổ gỡ bỏ (NV93) -------------------------

    def _doc_ban_ghi_nv93(self) -> dict:
        """{khoa: bản ghi} từ kho riêng; kho chưa gán/hỏng → {} (không vỡ)."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("tai_khoan_nv93") or {}
        except Exception as e:
            log.warning("muse2api: không đọc được bản ghi NV93: %s",
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
                khoa = self._khoa_tu_hatch_sess(tk.hatch_sess)
                ban_ghi[khoa] = tk.ban_ghi(khoa)
            self.kho.luu("tai_khoan_nv93", {"ban_ghi": ban_ghi})
        except Exception as e:
            log.warning("muse2api: không lưu được bản ghi NV93: %s",
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
            log.warning("muse2api: không lưu được sổ gỡ bỏ NV93: %s",
                        type(e).__name__)

    def _xoa_khoi_go_bo_nv93(self, khoa: str) -> None:
        tap = self._doc_go_bo_nv93()
        if khoa in tap:
            tap.discard(khoa)
            self._luu_go_bo_nv93(tap)

    # ── Khả năng chat ────────────────────────────────────────────────────

    async def chat(self, body: dict, stream: bool):
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return _LoiModule(400, "'messages' phải là mảng không rỗng",
                              "yeu_cau_sai").ket_qua()
        # Bước 6: phần tử messages không phải dict → 400
        for phan_tu in msgs:
            if not isinstance(phan_tu, dict):
                return _LoiModule(400, "mỗi phần tử của 'messages' phải là đối tượng",
                                  "yeu_cau_sai").ket_qua()
        prompt = build_prompt(msgs)
        if not prompt.strip():
            return _LoiModule(400, "prompt rỗng", "yeu_cau_sai").ket_qua()
        if not self._accs:
            return _LoiModule(401, "Module chưa được cấu hình cookie",
                              "khoa_hong").ket_qua()
        model = f"{self.tien_to}/{MODEL_ID}"

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())
        co_huy = threading.Event()

        def _day(kind: str, payload=None):
            loop.call_soon_threadsafe(q.put_nowait, (kind, payload))

        def _luot(acc: _TaiKhoan, accs_da_thu: set) -> bool:
            """Một tài khoản, tối đa 2 lần (lần 2 = nối lại khi kết nối cũ
            chết trước khi gửi). True = đã xong (đẩy kết quả hoặc khách ngắt),
            False = chuyển sang tài khoản kế."""
            for lan in range(2):
                sess = None
                try:
                    sess = ChatSession(acc.dam_bao(), acc.node_id)
                    for delta in sess.run(prompt, co_huy=co_huy):
                        if co_huy.is_set():
                            return True
                        acc.khoe = True  # có trả lời thật = cookie còn sống
                        _day("delta", delta)
                    if not co_huy.is_set():
                        _day("done")
                    return True
                except Exception as e:  # noqa: BLE001 - phân loại theo kiểu bên dưới
                    da_gui = sess is not None and sess.da_gui
                    if isinstance(e, AuthError) and not da_gui and any(
                            a not in accs_da_thu for a in self._accs):
                        acc.khoe = False
                        acc.bo()
                        log.warning("Tài khoản %s bị từ chối, chuyển sang tài khoản kế", acc.ten)
                        return False
                    if lan == 0 and sess is not None and not da_gui and self._la_loi_ket_noi(e):
                        log.warning("Kết nối của %s đã chết trước khi gửi, nối lại", acc.ten)
                        acc.bo()
                        continue
                    _day("error", self._map_loi(e, acc))
                    return True

        def _worker():
            try:
                accs_da_thu: set[_TaiKhoan] = set()
                while not co_huy.is_set():
                    acc = self._chon_chua_thu(accs_da_thu)  # hết tài khoản → _LoiModule
                    accs_da_thu.add(acc)
                    with acc.khoa:
                        # Khách đã ngắt khi còn xếp hàng chờ khoá → không gửi gì.
                        if co_huy.is_set() or _luot(acc, accs_da_thu):
                            return
            except _LoiModule as e:
                _day("error", e)
            except BaseException as e:  # noqa: BLE001 - worker không bao giờ chết im lặng
                log.exception("muse2api: worker hỏng ngoài dự kiến")
                _day("error", _LoiModule(502, f"Lỗi nội bộ module: {type(e).__name__}",
                                         "may_chu_loi"))

        threading.Thread(target=_worker, daemon=True).start()

        try:
            # Chờ sự kiện đầu tiên để quyết định mã trạng thái
            kind, payload = await q.get()

            if kind == "error":
                co_huy.set()
                return payload.ket_qua()

            if not stream:
                chunks = []
                if kind == "delta":
                    chunks.append(payload)
                while kind != "done":
                    kind, payload = await q.get()
                    if kind == "delta":
                        chunks.append(payload)
                    elif kind == "error":
                        co_huy.set()
                        return payload.ket_qua()
        except asyncio.CancelledError:
            co_huy.set()  # khách ngắt khi đang chờ → worker dừng, không gửi/không poll tiếp
            raise

        if not stream:
            text = "".join(chunks)
            return 200, {"content-type": "application/json"}, json.dumps({
                "id": chat_id,
                "object": "chat.completion", "created": created,
                "model": model,
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0}}, ensure_ascii=False).encode()

        async def _sinh_sse() -> AsyncIterator[bytes]:
            try:
                curr_kind = kind
                curr_payload = payload
                first_chunk = True
                while True:
                    if curr_kind == "delta":
                        delta = {"role": "assistant", "content": curr_payload} if first_chunk \
                            else {"content": curr_payload}
                        first_chunk = False
                        yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                                    "created": created, "model": model,
                                    "choices": [{"index": 0, "delta": delta,
                                                 "finish_reason": None}]})
                    elif curr_kind == "done":
                        yield _sse({"id": chat_id, "object": "chat.completion.chunk",
                                    "created": created, "model": model,
                                    "choices": [{"index": 0, "delta": {},
                                                 "finish_reason": "stop"}]})
                        yield b"data: [DONE]\n\n"
                        return
                    elif curr_kind == "error":
                        yield _sse({"error": {"message": str(curr_payload),
                                              "type": curr_payload.payload["error"]["type"]}})
                        return
                    curr_kind, curr_payload = await q.get()
            finally:
                co_huy.set()

        return 200, {"content-type": "text/event-stream",
                     "cache-control": "no-cache",
                     "x-accel-buffering": "no"}, _sinh_sse()

    # ── nội bộ ──────────────────────────────────────────────────────────

    def _chon_chua_thu(self, da_thu: set[_TaiKhoan]) -> _TaiKhoan:
        """Xoay vòng trong số tài khoản CHƯA thử ở yêu cầu này, ưu tiên tài
        khoản khoẻ; không còn ai khoẻ thì vẫn xoay vòng qua tất cả (khoe chỉ
        là gợi ý thứ tự, không loại hẳn — 401/403 có thể chỉ thoáng qua)."""
        with self._khoa_chon:
            chua_thu = [a for a in self._accs if a not in da_thu]
            if not chua_thu:
                raise _LoiModule(401, "Tất cả tài khoản cookie đều hỏng hoặc chưa có", "khoa_hong")
            ung_vien = [a for a in chua_thu if a.khoe] or chua_thu
            acc = ung_vien[self._vi_tri % len(ung_vien)]
            self._vi_tri += 1
            return acc

    def _map_loi(self, e: Exception, acc: _TaiKhoan) -> _LoiModule:
        """Ánh xạ ngoại lệ → _LoiModule, đặt acc.khoe = False CHỈ khi AuthError.

        Thông báo cho khách chỉ dựng từ CẤU TRÚC (kiểu ngoại lệ, mã trạng
        thái); nội dung phản hồi thượng nguồn trong str(e) chỉ vào log.
        """
        log.warning("muse2api %s: %s: %s", acc.ten, type(e).__name__, e)
        ma = e.status if isinstance(e, GatewayError) else None
        chi_tiet = type(e).__name__ + (f" status={ma}" if isinstance(ma, int) and ma > 0 else "")
        if isinstance(e, AuthError):
            acc.khoe = False
            acc.bo()
            return _LoiModule(401, f"Cookie không hợp lệ/hết hạn ({chi_tiet})", "khoa_hong")
        if isinstance(e, TimeoutError):
            acc.bo()
            return _LoiModule(504, f"Hết hạn chờ muse.ai ({chi_tiet})", "timeout_error")
        if ma == 429:
            return _LoiModule(429, f"Hết hạn mức ({chi_tiet})", "het_han_muc")
        acc.bo()
        return _LoiModule(502, f"Lỗi máy chủ hoặc kết nối: {chi_tiet}", "may_chu_loi")

    def _la_loi_ket_noi(self, e: Exception) -> bool:
        """True nếu lỗi kiểu kết nối (không phải auth, không phải timeout, không phải 429)."""
        if isinstance(e, (AuthError, TimeoutError)):
            return False
        if isinstance(e, GatewayError) and getattr(e, "status", None) == 429:
            return False
        return True
