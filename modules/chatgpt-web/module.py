"""Module chatgpt-web: trò chuyện với ChatGPT bản web (chatgpt.com).

Xác thực bằng Bearer access token do NGƯỜI DÙNG dán vào (trường ``tai_khoan``) —
không tự đăng nhập, không tự động hoá trình duyệt. Hỗ trợ nhiều token và tự
chuyển tài khoản khi lỗi, phân loại lỗi bằng mã HTTP (không dò chữ).

KHÁC các module web trước: mỗi cấu hình có thể gắn ``danh_tinh_id`` của kho
danh tính lõi (``app/danh_tinh.py``) — một Gmail nhập một lần ở lõi, module chỉ
giữ token của MÌNH trỏ về danh tính đó. Khi gắn, module tự ghi mình vào sổ
``gan`` của danh tính (lũy đẳng) để lõi chặn xoá danh tính còn tài khoản trỏ tới.

Luồng giao tiếp học từ kho tham khảo chatgpt2api
(``services/openai_backend_api.py``, ``services/account_service.py``,
``services/codex_token_refresh.py``): GET /backend-api/me kiểm token →
POST /backend-api/conversation → bóc SSE. Xem client.py để biết chi tiết.
CHƯA KIỂM với dịch vụ thật.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time

from app.kho import che
from app.khoi_phuc_phien import KetQuaKhoiPhuc, TaiKhoanCanKhoiPhuc
from app.loi_openai import loai_theo_ma, nhac_lai, than_loi
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap
from app.tu_xa import kiem_url_lan

from .client import (
    MAY_CHU_MAC_DINH,
    MO_HINH_HO_TRO,
    ChatGPTWebClient,
    LoiChatGPTWeb,
    LoiHanMuc,
    LoiHetGio,
    LoiMayChu,
    LoiXacThuc,
    ModelKhongCo,
    chuyen_thanh_tin_nhan,
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

log = logging.getLogger("web2api.module.chatgpt-web")


def _lay_danh_tinh(id_: str):
    """Bản ghi công khai của danh tính; None nếu không có. Bọc để test chích."""
    from app import danh_tinh
    return danh_tinh.lay(id_)


def _gan_danh_tinh(id_: str, module_id: str = TIEN_TO):
    """Ghi module vào sổ gắn của danh tính (lũy đẳng). Bọc để test chích."""
    from app import danh_tinh
    return danh_tinh.gan(id_, module_id)


def _che_email(email: str) -> str:
    """Che email để hiển thị theo danh tính: giữ ký tự đầu + …@ + miền."""
    email = str(email or "")
    if "@" not in email:
        return "…"
    dau, mien = email.split("@", 1)
    return f"{dau[:1]}…@{mien}" if dau else f"…@{mien}"


class _TaiKhoan:
    """Một Bearer token: client tạo lười, trạng thái sức khoẻ do module giữ."""

    def __init__(self, token: str):
        self.token = token
        self.client: ChatGPTWebClient | None = None
        self.khoe = True
        self.loai_loi: str | None = None  # khoa_hong | het_han_muc | may_chu_loi | None

    def ten_che(self) -> str:
        return che(self.token)


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", TIEN_TO)
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._khoa = threading.Lock()
        self._tai_khoan: list[_TaiKhoan] = []
        self._co_so_url: str | None = None
        self._danh_tinh_id: str | None = None
        # Khoá tài khoản đã đánh dấu hỏng (tầng c bỏ qua khi chọn kế).
        self._hong: set[str] = set()
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV88):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None

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
            tin_hieu = trinh_duyet.tin_hieu()
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
            cookie = trinh_duyet.doc_cookie("chatgpt.com")
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

    # -- tạo client: mối nối để test chích máy chủ giả ---------------------

    def _tao_client(self, token: str) -> ChatGPTWebClient:
        return ChatGPTWebClient(token, base_url=self._co_so_url or MAY_CHU_MAC_DINH)

    # -- danh tính chung ----------------------------------------------------

    def _kiem_danh_tinh(self, danh_tinh_id: str | None) -> dict | None:
        """Kiểm danh tính tồn tại; không tồn tại → ValueError với thông điệp rõ."""
        if not danh_tinh_id:
            return None
        ban_ghi = _lay_danh_tinh(danh_tinh_id)
        if ban_ghi is None:
            raise ValueError(
                f"danh_tinh_id {danh_tinh_id!r} không tồn tại trong kho danh tính "
                "— tạo trước ở /api/danh-tinh rồi gắn lại.")
        return ban_ghi

    def _thong_tin_danh_tinh(self) -> dict | None:
        """Thông tin hiển thị theo danh tính (email đã che)."""
        if not self._danh_tinh_id:
            return None
        ban_ghi = _lay_danh_tinh(self._danh_tinh_id) or {}
        email = str(ban_ghi.get("email") or "")
        return {"id": self._danh_tinh_id,
                "nhan": str(ban_ghi.get("nhan") or ""),
                "email_che": _che_email(email)}

    # -- hợp đồng lõi -------------------------------------------------------

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, chỉ tách token."""
        cau_hinh = cau_hinh or {}
        danh_sach = cau_hinh.get("tai_khoan") or []
        if isinstance(danh_sach, str):
            danh_sach = [danh_sach]
        tai_khoan = [_TaiKhoan(str(t).strip()) for t in danh_sach
                     if isinstance(t, str) and str(t).strip()]
        danh_tinh_id = str(cau_hinh.get("danh_tinh_id") or "").strip() or None
        co_so = str(cau_hinh.get("co_so_url") or "").strip().rstrip("/") or None
        with self._khoa:
            self._tai_khoan = tai_khoan
            self._danh_tinh_id = danh_tinh_id
            self._co_so_url = co_so
        log.info("chatgpt-web: đã nạp %d tài khoản%s", len(tai_khoan),
                 f" (danh tính {danh_tinh_id})" if danh_tinh_id else "")

    async def _kiem_co_so_url(self) -> None:
        if self._co_so_url:
            try:
                self._co_so_url = await kiem_url_lan(self._co_so_url)
            except ValueError as e:
                raise ValueError(f"co_so_url không hợp lệ: {e}") from None

    async def _init_tai_khoan(self, tk: _TaiKhoan) -> None:
        if tk.client is None:
            tk.client = self._tao_client(tk.token)
        await tk.client.init()

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng token bằng /backend-api/me (rẻ, không tốn lượt chat)."""
        self.nap(cau_hinh)
        try:
            await self._kiem_co_so_url()
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        try:
            self._kiem_danh_tinh(self._danh_tinh_id)
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        with self._khoa:
            ds = list(self._tai_khoan)
            danh_tinh_id = self._danh_tinh_id
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
        if danh_tinh_id and tot:
            # Gắn vào sổ của danh tính để lõi biết còn tài khoản trỏ tới
            # (chặn xoá danh tính khi còn tài khoản — NV72).
            try:
                _gan_danh_tinh(danh_tinh_id)
            except Exception as e:
                log.warning("chatgpt-web: không gắn được danh tính %s: %s",
                            danh_tinh_id, type(e).__name__)
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ket_qua,
                    "loi": "Không tài khoản nào dùng được (xem chi tiết từng khoá)."}
        ds_token = cau_hinh.get("tai_khoan") or []
        luu = {"tai_khoan": [ds_token[i] for i in tot if i < len(ds_token)]}
        if danh_tinh_id:
            luu["danh_tinh_id"] = danh_tinh_id
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
        tin_nhan = chuyen_thanh_tin_nhan(body)
        if not tin_nhan:
            return 400, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(400, "thiếu messages (danh sách hội thoại rỗng)"),
                           ensure_ascii=False).encode("utf-8")

        ds = self._thu_tu_tai_khoan()
        if not ds:
            return 401, {"Content-Type": "application/json"}, \
                json.dumps(than_loi(401, "chatgpt-web chưa có tài khoản nào: "
                                         "thêm token ở trường tai_khoan rồi cài lại."),
                           ensure_ascii=False).encode("utf-8")
        if stream:
            tieu_de = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                       "X-Accel-Buffering": "no"}
            return 200, tieu_de, self._luong_chat(ds, tin_nhan, model_id, ten_model)

        loi_cuoi: LoiChatGPTWeb | None = None
        for i, tk in enumerate(ds):
            try:
                if tk.client is None:
                    tk.client = self._tao_client(tk.token)
                van_ban = await tk.client.generate_content(tin_nhan, ten_model)
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
            return 200, {"Content-Type": "application/json"}, phan_hoi_json(model_id, van_ban)

        # Hết tài khoản: mã theo lỗi cuối (không bao giờ lộ token hay đáp án thô).
        ma = loi_cuoi.ma if loi_cuoi else 502
        thong_diep = {
            401: "Mọi tài khoản chatgpt-web đều bị từ chối — token hết hạn hoặc không hợp lệ, dán lại.",
            429: "Mọi tài khoản chatgpt-web đều bị giới hạn lượt — thử lại sau.",
            504: "chatgpt.com không phản hồi kịp.",
        }.get(ma, "chatgpt.com lỗi phía máy chủ.")
        return ma, {"Content-Type": "application/json"}, \
            json.dumps(than_loi(ma, thong_diep), ensure_ascii=False).encode("utf-8")

    async def _luong_chat(self, ds: list[_TaiKhoan], tin_nhan: list[dict],
                          model_id: str, ten_model: str):
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
                async for delta in tk.client.generate_content_stream(tin_nhan, ten_model):
                    da_ra_du_lieu = True
                    yield chunk_delta(model_id, hoan_thanh_id, tao_luc, delta)
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
            yield chunk_ket_thuc(model_id, hoan_thanh_id, tao_luc)
            yield dong_done()
            return
        ma = loi_cuoi.ma if loi_cuoi else 502
        yield chunk_loi(f"{loai_theo_ma(ma)}: không còn tài khoản chatgpt-web dùng được "
                        f"(mã {ma}) — xem nhật ký máy chủ.")
        yield dong_done()

    # -- models / suc_khoe / dung --------------------------------------------

    async def models(self):
        return [{"id": id_day_du(m), "object": "model",
                 "created": 1760000000, "owned_by": "chatgpt-web"}
                for m in MO_HINH_HO_TRO]

    async def suc_khoe(self):
        with self._khoa:
            ds = list(self._tai_khoan)
        tt_danh_tinh = self._thong_tin_danh_tinh()
        muc = []
        for i, tk in enumerate(ds, start=1):
            bat_dau = time.monotonic()
            try:
                await self._init_tai_khoan(tk)
            except LoiXacThuc as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "danh_tinh": tt_danh_tinh})
            except LoiChatGPTWeb as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": str(e), "danh_tinh": tt_danh_tinh})
            except Exception as e:
                muc.append({"khoa": tk.ten_che(), "ok": False, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": 0, "loi": type(e).__name__, "danh_tinh": tt_danh_tinh})
            else:
                muc.append({"khoa": tk.ten_che(), "ok": True, "thu_tu": i,
                            "do_tre_ms": int((time.monotonic() - bat_dau) * 1000),
                            "so_model": len(MO_HINH_HO_TRO), "loi": None,
                            "danh_tinh": tt_danh_tinh})
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
