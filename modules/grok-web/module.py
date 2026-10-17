"""Module Grok Web — trò chuyện với Grok qua giao thức WebSocket của grok.com.

Xác thực bằng cookie của người dùng đã đăng nhập: DÁN cookie (trường cấu hình
``tai_khoan``, danh sách — mỗi phần tử là một tài khoản), đăng nhập bằng MẬT
KHẨU qua trình duyệt (đường phụ NV89), hoặc TÁI DÙNG hồ sơ Google đã đăng
nhập (``tai_dung_ho_so``, NV93 — đường chính: «Sign in with Google» trong hồ
sơ). Đăng nhập tự động qua trình duyệt theo hợp đồng NV67/NV88 (NV89): trước
khi bó tay với thử thách truy cập (Cloudflare), bước trình duyệt CỐ VƯỢT tối
đa 3 lượt (patch bổ sung NV90 — CHƯA KIỂM trên grok.com thật; vượt không
được → ``can_nguoi`` như cũ). Hỗ trợ nhiều tài khoản và tự chuyển tài khoản
khi:

- lỗi phiên (bắt tay 401/403, sự kiện ``error`` có mã thuộc nhóm phiên),
- hết lượt (``stream_error`` có kind thuộc nhóm hạn mức),
- phiên xong nhưng không trả chữ nào.

Cloudflare của grok.com → NHẬN BẰNG CẤU TRÚC (403/503 + ``cf-mitigated:
challenge``) → can_nguoi NGAY, KHÔNG tự tích/giải (NV90-A). KHÔNG đoán tên
cookie phiên: «đã đăng nhập» = phiên dùng được; chỗ chưa chắc ghi «CHƯA KIỂM».

Phân loại lỗi bằng mã trạng thái/cấu trúc sự kiện, không dò chữ trong thông điệp.
Chỉ chuyển tài khoản khi CHƯA có chữ nào được gửi — đã stream dở thì dừng tại
đó để khỏi hỏi Grok hai lần. Tài khoản vừa hết lượt được nghỉ một lúc; nhưng
không bao giờ chặn lựa chọn cuối: hết đường thì vẫn dùng tài khoản ít lỗi nhất.

Cookie không bao giờ vào log hay phản hồi — log chỉ ghi khoá mờ ``tk-…``.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import time
from typing import AsyncIterator
from urllib.parse import urlsplit

from app import danh_tinh
from app import tu_lam_moi
from app.chuyen_tiep import ds_khoa
from app.khoi_phuc_phien import (
    SO_LUOT_VUOT_THU_THACH_TOI_DA,
    KetQuaKhoiPhuc,
    TaiKhoanCanKhoiPhuc,
    la_captcha_chu,
    vuot_thu_thach_toi_da,
)
from app.tai_khoan_hang_loat import BiMatTam, DongDaTach, KetQuaDangNhap
from app.tu_xa import kiem_url_lan

from .client import (
    GOC_HTTPS_MAC_DINH,
    WS_DUONG_MAC_DINH,
    GrokWebClient,
    KhachGrokWeb,
    LoiGrokWeb,
    LoiGrokWebRong,
    LoiHanMucGrokWeb,
    LoiMayChuGrokWeb,
    LoiPhienGrokWeb,
    LoiThuThachCloudflare,
    LoiThuThachTruyCap,
    lay_uid_tu_phien,
)
from .converter import (
    CAC_CHE_DO,
    LoiGiuaLuu,
    gom_json_openai,
    hop_van_ban,
    luong_sse_openai,
    tach_che_do,
)

log = logging.getLogger("web2api.module.grok_web")

#: Tài khoản vừa hết lượt thì nghỉ chừng này trước khi được thử lại.
NGHI_SAU_HET_LUOT = 120.0


def _gio_utc() -> str:
    """Giờ hiện tại UTC dạng ISO-8601 gọn (cho lan_dung_cuoi)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tach_cookie(chuoi: str) -> dict[str, str]:
    """Tách chuỗi ``k1=v1; k2=v2`` thành dict; mục hỏng bị bỏ qua."""
    ra: dict[str, str] = {}
    for muc in chuoi.split(";"):
        muc = muc.strip()
        if not muc or "=" not in muc:
            continue
        k, v = muc.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v and k not in ra:
            ra[k] = v
    return ra


def _kiem_ws_url(ws_url: str) -> str | None:
    """Kiểm địa chỉ WebSocket ghi đè: scheme ws/wss, không userinfo,
    máy là grok.com hoặc loopback (cho test). Trả về lý do lỗi hoặc None."""
    try:
        phan = urlsplit(ws_url)
    except Exception:
        return "địa chỉ không phân tích được"
    if phan.scheme not in ("ws", "wss"):
        return "scheme phải là ws hoặc wss"
    if phan.username or phan.password:
        return "không được kèm thông tin đăng nhập trong địa chỉ"
    may = (phan.hostname or "").lower()
    if not may:
        return "thiếu tên máy"
    if may in ("grok.com", "www.grok.com"):
        return None
    try:
        ip = ipaddress.ip_address(may)
    except ValueError:
        return "chỉ chấp nhận grok.com hoặc địa chỉ loopback"
    if ip.is_loopback:
        return None
    return "chỉ chấp nhận grok.com hoặc địa chỉ loopback"


def _loi_openai(thong_diep: str, loai: str, ma: str) -> bytes:
    return json.dumps(
        {"error": {"message": thong_diep, "type": loai, "code": ma}},
        ensure_ascii=False,
    ).encode("utf-8")


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


class Module:
    """Điểm vào của module theo hợp đồng CHUAN-MODULE."""

    def __init__(self, manifest: dict | None = None) -> None:
        manifest = manifest or {}
        self._manifest = manifest
        self._tien_to = str(manifest.get("tien_to") or "grok")
        self._tai_khoan: list[dict[str, str]] = []  # cookie từng tài khoản
        self._ws_co_so = ""
        self._goc_https = GOC_HTTPS_MAC_DINH
        self._nghi_den: dict[int, float] = {}  # chỉ số tài khoản → mốc hết nghỉ
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._dt_id: list[str | None] = []   # danh_tinh_id từng tài khoản
        self._dt_ten: list[str | None] = []  # tên hiển thị theo danh tính
        self._hong: set[str] = set()  # khoá tài khoản tầng (c) đã đánh dấu hỏng
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV89):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None
        # Chế độ đăng nhập: "truc_tiep" (email+mật khẩu trên x.com) hoặc
        # "google" (SSO Google, lưu phiên). Mặc định "truc_tiep".
        self._che_do_dang_nhap = "truc_tiep"
        # Làm giàu NV93: khoa -> {email, ho_so, goi, trang_thai, lan_dung_cuoi}
        self._lam_giau: dict[str, dict] = {}

    # ── Gắn danh tính chung (khuôn NV77) ──────────────────────────────────

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
            log.warning("grok-web: không đọc được liên kết danh tính: %s",
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
            log.warning("grok-web: không lưu được liên kết danh tính: %s",
                        type(e).__name__)

    def _ten_theo_danh_tinh(self, dt_id: str) -> str | None:
        """Tên hiển thị theo danh tính: nhãn do người dùng đặt, không thì email che."""
        try:
            dt = danh_tinh.lay(dt_id)
        except Exception as e:
            log.warning("grok-web: không đọc được danh tính %s: %s",
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
                log.warning("grok-web: không đọc được trạng thái gắn danh tính: %s",
                            type(e).__name__)
        for dt_id in sorted(cu - tap_moi):
            try:
                danh_tinh.go(dt_id, "grok-web")
            except Exception as e:
                log.warning("grok-web: gỡ tham chiếu danh tính %s: %s",
                            dt_id, type(e).__name__)
        for dt_id in sorted(tap_moi - cu):
            # Đã kiểm tồn tại ở nap(); kho danh tính hỏng lúc này thì để 503 lan ra
            # (đóng khi lỗi) thay vì âm thầm chạy không tham chiếu.
            danh_tinh.gan(dt_id, "grok-web")
        if self.kho is not None:
            try:
                du_lieu = self.kho.doc("danh_tinh") or {}
                self.kho.luu("danh_tinh", {"anh_xa": du_lieu.get("anh_xa") or {},
                                           "gan": sorted(tap_moi),
                                           "chu_so_huu": id(self)})
            except Exception as e:
                log.warning("grok-web: không lưu được trạng thái gắn danh tính: %s",
                            type(e).__name__)

    # ── Nạp / cài đặt ──────────────────────────────────────────────────

    def _nap_tu(self, cau_hinh: dict) -> str | None:
        """Nạp cấu hình vào bộ nhớ; trả về thông điệp lỗi hoặc None nếu ổn.

        Khoá phụ «danh_tinh» (không khai trong manifest, lõi cho qua): danh sách
        cùng thứ tự với «tai_khoan», mỗi phần tử là id danh tính hoặc null. id lạ
        → thông điệp lỗi rõ ràng, chưa gắn gì. Không có khoá này thì tra liên kết
        đã nhớ trong kho riêng theo hash cookie (bền qua khởi động); tài khoản
        không gắn chạy y như trước.
        """
        ds = ds_khoa(cau_hinh.get("tai_khoan"))
        tai_khoan = []
        for chuoi in ds:
            jar = _tach_cookie(chuoi)
            if not jar.get("sso"):
                return "mỗi tài khoản phải có cookie «sso»"
            tai_khoan.append(jar)
        gan_moi = cau_hinh.get("danh_tinh")
        if gan_moi is not None:
            if not isinstance(gan_moi, list) or len(gan_moi) != len(ds):
                return (
                    "«danh_tinh» phải là danh sách cùng độ dài với «tai_khoan» "
                    f"(nhận {len(gan_moi) if isinstance(gan_moi, list) else type(gan_moi).__name__}, "
                    f"cần {len(ds)}); mỗi phần tử là id danh tính hoặc null.")
            for dt_id in gan_moi:
                if dt_id is None:
                    continue
                if not isinstance(dt_id, str) or danh_tinh.lay(dt_id) is None:
                    return (
                        f"danh_tinh_id {dt_id!r} không tồn tại trong sổ danh tính "
                        "(tạo trước ở «Hệ thống → AI → Danh tính»).")
        lien_ket = self._doc_lien_ket()
        dt_id_ds: list[str | None] = []
        dt_ten_ds: list[str | None] = []
        for i, chuoi in enumerate(ds):
            if gan_moi is not None:
                dt_id = gan_moi[i]
            else:
                dt_id = lien_ket.get(_hash_cookie(chuoi))
            dt_id_ds.append(dt_id)
            dt_ten_ds.append(self._ten_theo_danh_tinh(dt_id) if dt_id else None)
        if gan_moi is not None:
            for i, chuoi in enumerate(ds):
                h = _hash_cookie(chuoi)
                if dt_id_ds[i]:
                    lien_ket[h] = dt_id_ds[i]
                else:
                    lien_ket.pop(h, None)
            self._luu_lien_ket(lien_ket)
        ws_url = str(cau_hinh.get("ws_url") or "").strip()
        ws_co_so = WS_DUONG_MAC_DINH
        goc_https = GOC_HTTPS_MAC_DINH
        if ws_url:
            loi = _kiem_ws_url(ws_url)
            if loi:
                return f"ws_url không hợp lệ: {loi}"
            phan = urlsplit(ws_url)
            ws_co_so = ws_url
            goc_https = f"{'https' if phan.scheme == 'wss' else 'http'}://{phan.hostname}"
            if phan.port:
                goc_https += f":{phan.port}"
        che_do = str(cau_hinh.get("che_do_dang_nhap") or "truc_tiep").strip().lower()
        if che_do not in ("truc_tiep", "google"):
            return ("che_do_dang_nhap phải là «truc_tiep» (email+mật khẩu trên "
                    f"x.com) hoặc «google» (SSO Google), nhận {che_do!r}.")
        self._che_do_dang_nhap = che_do
        # NV93: bỏ tài khoản đã bị go_tai_khoan gỡ tường minh; khôi phục bản
        # ghi làm giàu từ kho riêng (khởi động lại không mất).
        self._tai_khoan = []
        self._dt_id = []
        self._dt_ten = []
        go_bo = self._doc_go_bo()
        ban_ghi = self._doc_ban_ghi()
        self._lam_giau = {}
        for jar, dt_id, dt_ten in zip(tai_khoan, dt_id_ds, dt_ten_ds):
            khoa = self._khoa_tu_jar(jar)
            if khoa in go_bo:
                continue  # đã bị go_tai_khoan gỡ tường minh — không dựng lại
            self._tai_khoan.append(jar)
            self._dt_id.append(dt_id)
            self._dt_ten.append(dt_ten)
            g = ban_ghi.get(khoa)
            if isinstance(g, dict):
                self._lam_giau[khoa] = g
        self._ws_co_so = ws_co_so
        self._goc_https = goc_https
        self._nghi_den = {}
        self._dong_bo_tham_chieu({d for d in self._dt_id if d})
        n_gan = sum(1 for d in self._dt_id if d)
        log.info("grok-web: đã nạp %d tài khoản (%d gắn danh tính)",
                 len(self._tai_khoan), n_gan)
        return None

    async def nap(self, cau_hinh: dict) -> None:
        loi = self._nap_tu(cau_hinh or {})
        if loi:
            raise ValueError(loi)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm tra cấu hình cài đặt.

        Chỉ kiểm định dạng cookie (bắt buộc có ``sso``). Khi có ``ws_url`` ghi đè
        (kiểm thử/trỏ proxy) thì thử mở WebSocket rồi đóng ngay để chắc địa chỉ
        nghe được — không tạo phiên, không tốn lượt.
        """
        loi = self._nap_tu(cau_hinh or {})
        if loi:
            return {"ok": False, "loi": loi}
        if not self._tai_khoan:
            return {
                "ok": False,
                "loi": "Chưa có tài khoản nào: nhập cookie của ít nhất một tài khoản grok.com đã đăng nhập.",
            }
        if str((cau_hinh or {}).get("ws_url") or "").strip():
            # URL do người dùng ghi đè (kiểm thử/trỏ proxy): bắt buộc qua
            # app/tu_xa.py như mọi URL cấu hình được — chặn SSRF (loopback,
            # link-local, IPv4 nhúng trong 6to4/Teredo). goc_https cùng máy/cổng
            # với ws_url nên kiểm nó là đủ (kiem_url_lan chỉ nhận http/https).
            try:
                self._goc_https = await kiem_url_lan(self._goc_https)
            except ValueError as e:
                return {"ok": False, "loi": f"ws_url không hợp lệ: {e}"}
            try:
                khach = KhachGrokWeb(self._ws_co_so, self._tai_khoan[0], uid="kiem_tra")
                ws = await khach._mo_ws()
                await ws.close()
            except LoiPhienGrokWeb:
                # Bắt tay tới nơi nhưng máy chủ từ chối — địa chỉ vẫn nghe được.
                pass
            except Exception as e:
                return {
                    "ok": False,
                    "loi": f"Không mở được WebSocket tới {self._ws_co_so}: {e}",
                }
        return {
            "ok": True,
            "ghi_chu": f"Đã nhận {len(self._tai_khoan)} tài khoản Grok.",
        }

    # ── Tái dùng hồ sơ Google (NV93 — đường chính) ─────────────────────────

    _URL_TAI_DUNG = "https://grok.com/"
    _BO_CHON_GOOGLE = ('button[aria-label*="Sign in with Google" i], '
                       'button[aria-label*="Google" i]')
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare"}

    def _tao_client(self, jar) -> GrokWebClient:
        """Mối nối để test chích máy chủ giả (probe thử thách Cloudflare)."""
        return GrokWebClient(jar, base_url=self._goc_https or GOC_HTTPS_MAC_DINH)

    @staticmethod
    def _loc_cookie(cookie: dict) -> dict[str, str]:
        """Lọc cookie đọc từ grok.com: BỎ tên bắt đầu «cf_» TRỪ «cf_clearance».

        Giữ ``cf_clearance`` (cần cho phiên sau khi người qua thử thách),
        ``__cf_bm`` (không bắt đầu bằng «cf_» nên được giữ), ``sso`` và mọi
        cookie khác. KHÔNG đoán tên cookie phiên — giữ tất cả còn lại.
        """
        jar: dict[str, str] = {}
        for k, v in (cookie or {}).items():
            k, v = str(k), str(v)
            if not k or not v:
                continue
            if k.startswith("cf_") and k != "cf_clearance":
                continue  # cookie thử thách (vd cf_chl_*) — không lưu
            jar[k] = v
        return jar

    async def _co_thu_thach(self, jar=None) -> bool:
        """Probe HTTP có gặp thử thách Cloudflare không (nhận bằng cấu trúc).

        Lỗi probe (mạng…) → False: probe không quyết định thay tín hiệu trình
        duyệt, chỉ bổ sung phát hiện sớm.
        """
        try:
            await self._tao_client(jar or {}).kiem_tra_thu_thach()
        except LoiThuThachCloudflare:
            return True
        except Exception as e:
            # Probe chỉ phát hiện sớm: lỗi probe → coi như không thấy thử thách,
            # tín hiệu trình duyệt ở dưới vẫn bắt và trả can_nguoi (an toàn).
            log.warning("grok-web: probe thử thách lỗi: %s", type(e).__name__)
            return False
        return False

    async def tai_dung_ho_so(self, ho_so, trinh_duyet) -> KetQuaDangNhap:
        """«Sign in with Google» TRONG hồ sơ đã đăng nhập Google (NV93).

        Hợp đồng NV92-P3: module tự làm bước Google SSO của nó trong hồ sơ,
        đọc TẤT CẢ cookie grok.com (lọc bỏ cookie thử thách ``cf_*``), thêm tài
        khoản vào kho của CHÍNH mình, trả ``KetQuaDangNhap`` với ``cookie`` là
        chuỗi cookie đã lọc (route tái dùng lưu qua đường cai() có sẵn). Không
        nhập lại email/mật khẩu/2FA — KHÔNG BAO GIỜ ``dien()``.

        Cloudflare → NHẬN BẰNG CẤU TRÚC (403/503 + ``cf-mitigated: challenge``,
        qua probe HTTP sớm, NV90-A) → can_nguoi NGAY, KHÔNG tự tích/giải.
        KHÔNG đoán tên cookie phiên: «đã đăng nhập» = có cookie + không bị
        chặn; kiểm phiên thật đầy đủ là CHƯA KIỂM (endpoint thật chưa xác định).

        HỌC TỪ c2a (chỉ đọc lấy hiểu, tự viết lại): TriTue2011/chatgpt2api,
        ``captcha-solver/src/grok_web_login.py`` — các bước: mở grok.com → bấm
        «Sign in with Google» → SSO trong hồ sơ → về grok.com đã đăng nhập.
        """
        hs_id = ho_so.get("id") if isinstance(ho_so, dict) else str(ho_so or "")
        hs_id = str(hs_id or "").strip() or None
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.mo_trang(self._URL_TAI_DUNG)
            tin_hieu = set(trinh_duyet.tin_hieu() or set())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
            # CAPTCHA/Cloudflare/2FA/xác minh: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        # Probe HTTP sớm: phát hiện thử thách bằng cấu trúc NGAY, không chờ
        # hết vòng (c2a đo 20/09: grok.com không tự qua được).
        if await self._co_thu_thach():
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            if "da_dang_nhap" not in tin_hieu:
                # Bước «bấm Google»: SSO trong hồ sơ, không nhập lại gì.
                await trinh_duyet.bam(self._BO_CHON_GOOGLE)
                da_vao = False
                for _ in range(20):
                    tin_hieu = set(trinh_duyet.tin_hieu() or set())
                    if tin_hieu & self._TIN_HIEU_CAN_NGUOI:
                        return KetQuaDangNhap(False, ma_loi="can_nguoi")
                    if "da_dang_nhap" in tin_hieu:
                        da_vao = True
                        break
                    await asyncio.sleep(1)
                if not da_vao:
                    return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
            try:
                cookie = trinh_duyet.doc_cookie("grok.com")
            except Exception:
                cookie = {}
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        jar = self._loc_cookie(cookie if isinstance(cookie, dict) else {})
        if not jar:
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        if await self._co_thu_thach(jar):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        chuoi = self._chuoi_tu_jar(jar)
        email = str(ho_so.get("email") or "") if isinstance(ho_so, dict) else ""
        khoa = self._them_tai_khoan_tu_ho_so(jar, hs_id, email)
        # Log chỉ khoá mờ — KHÔNG BAO GIỜ log cookie.
        log.info("grok-web: tái dùng hồ sơ %s → tài khoản %s", hs_id, khoa)
        return KetQuaDangNhap(True, cookie=chuoi)

    def _them_tai_khoan_tu_ho_so(self, jar: dict[str, str],
                                 hs_id: str | None, email: str) -> str:
        """Thêm/cập nhật tài khoản từ phiên tái dùng (chống trùng theo khoá)."""
        khoa = self._khoa_tu_jar(jar)
        idx = next((i for i, j in enumerate(self._tai_khoan)
                    if self._khoa_tu_jar(j) == khoa), None)
        if idx is None:
            self._tai_khoan.append(dict(jar))
            self._dt_id.append(None)
            self._dt_ten.append(None)
        else:
            self._tai_khoan[idx] = dict(jar)
        self._lam_giau[khoa] = {
            "email": email,
            "ho_so": hs_id,
            "goi": "khong_ro",  # CHƯA KIỂM: chưa xác định cách suy gói grok
            "trang_thai": "hoat_dong",
            "lan_dung_cuoi": _gio_utc(),
        }
        self._xoa_khoi_go_bo(khoa)
        self._luu_ban_ghi_tai_khoan()
        return khoa

    # ── Liệt kê / gỡ tài khoản (NV93) ──────────────────────────────────────

    def liet_ke_tai_khoan(self) -> list[dict]:
        """Danh sách tài khoản KHÔNG bí mật: khoa/email/ho_so/goi/trạng thái."""
        ds = []
        for jar in self._tai_khoan:
            khoa = self._khoa_tu_jar(jar)
            g = self._lam_giau.get(khoa) or {}
            ds.append({
                "khoa": khoa,
                "email": g.get("email", ""),
                "ho_so": g.get("ho_so"),
                "goi": g.get("goi", "khong_ro"),
                "trang_thai": g.get("trang_thai", "hoat_dong"),
                "lan_dung_cuoi": g.get("lan_dung_cuoi"),
            })
        return ds

    def go_tai_khoan(self, khoa: str) -> bool:
        """Gỡ đúng MỘT tài khoản của module này; ghi sổ gỡ bỏ vào kho riêng để
        khởi động lại không dựng lại. Không động module khác, không động hồ sơ."""
        khoa = str(khoa or "")
        idx = next((i for i, j in enumerate(self._tai_khoan)
                    if self._khoa_tu_jar(j) == khoa), None)
        if idx is None:
            return False
        del self._tai_khoan[idx]
        for lst in (self._dt_id, self._dt_ten):
            if idx < len(lst):
                del lst[idx]
        self._lam_giau.pop(khoa, None)
        tap = self._doc_go_bo()
        tap.add(khoa)
        self._luu_go_bo(tap)
        self._luu_ban_ghi_tai_khoan()
        log.info("grok-web: đã gỡ tài khoản %s", khoa)
        return True

    # ── Kho riêng: bản ghi làm giàu + sổ gỡ bỏ (NV93) ──────────────────────

    def _doc_ban_ghi(self) -> dict:
        """{khoa: bản ghi} từ kho riêng; kho chưa gán/hỏng → {} (không vỡ)."""
        if self.kho is None:
            return {}
        try:
            du_lieu = self.kho.doc("tai_khoan") or {}
        except Exception as e:
            log.warning("grok-web: không đọc được bản ghi tài khoản: %s",
                        type(e).__name__)
            return {}
        ban_ghi = du_lieu.get("ban_ghi") if isinstance(du_lieu, dict) else None
        return ban_ghi if isinstance(ban_ghi, dict) else {}

    def _luu_ban_ghi_tai_khoan(self) -> None:
        """Lưu bản ghi làm giàu (không bí mật thô). Không vỡ."""
        if self.kho is None:
            return
        try:
            ban_ghi = {}
            for jar in self._tai_khoan:
                khoa = self._khoa_tu_jar(jar)
                g = self._lam_giau.get(khoa) or {}
                ban_ghi[khoa] = {
                    "khoa": khoa,
                    "email": g.get("email", ""),
                    "ho_so": g.get("ho_so"),
                    "goi": g.get("goi", "khong_ro"),
                    "trang_thai": g.get("trang_thai", "hoat_dong"),
                    "lan_dung_cuoi": g.get("lan_dung_cuoi"),
                }
            self.kho.luu("tai_khoan", {"ban_ghi": ban_ghi})
        except Exception as e:
            log.warning("grok-web: không lưu được bản ghi tài khoản: %s",
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
            log.warning("grok-web: không lưu được sổ gỡ bỏ: %s",
                        type(e).__name__)

    def _xoa_khoi_go_bo(self, khoa: str) -> None:
        tap = self._doc_go_bo()
        if khoa in tap:
            tap.discard(khoa)
            self._luu_go_bo(tap)

    # ── Đăng nhập + khôi phục theo hợp đồng NV67/NV88 (NV89) ──────────

    # Đăng nhập grok.com qua SSO của X (không có form đăng nhập riêng trên
    # grok.com). CHƯA KIỂM với trình duyệt thật / X thật.
    _URL_DANG_NHAP = "https://x.com/login"

    # -- khoá tài khoản mờ -------------------------------------------------

    @staticmethod
    def _chuoi_tu_jar(jar: dict[str, str]) -> str:
        """Dạng chuẩn của jar để băm khoá (sắp xếp theo tên cho ổn định)."""
        return "; ".join(f"{k}={v}" for k, v in sorted(jar.items()))

    @staticmethod
    def _khoa_tu_jar(jar: dict[str, str]) -> str:
        """Khoá mờ ổn định của một cookie-jar: «tk-» + 12 hex SHA-256.

        Hợp đồng NV93: khoá mọi module web đều dạng ``tk-<băm>``.
        """
        return "tk-" + hashlib.sha256(
            Module._chuoi_tu_jar(jar).encode("utf-8")).hexdigest()[:12]

    def _tim_jar(self, khoa: str) -> tuple[int, dict[str, str]] | None:
        """(chỉ số, jar) của tài khoản khớp khoá; None nếu không có."""
        khoa = str(khoa or "")
        for i, jar in enumerate(self._tai_khoan):
            if self._khoa_tu_jar(jar) == khoa:
                return i, jar
        return None

    def _them_jar(self, jar: dict[str, str]) -> None:
        """Thêm jar vào danh sách (lũy đẳng), giữ _dt_id/_dt_ten song song."""
        if jar not in self._tai_khoan:
            self._tai_khoan.append(dict(jar))
            self._dt_id.append(None)
            self._dt_ten.append(None)

    # -- mật khẩu đã lưu ----------------------------------------------------

    def mat_khau_da_luu(self, khoa: str) -> BiMatTam | None:
        """Mật khẩu đã lưu cho một khoá/email (kho riêng mã hoá của module).

        Kho đọc hỏng → None (fail-closed: coi như chưa lưu, lõi báo cần người)."""
        if self.kho is None:
            return None
        try:
            ds = self.kho.doc("mat_khau") or {}
        except Exception:
            return None
        mk = ds.get(str(khoa or "").strip().lower()) if isinstance(ds, dict) else None
        return BiMatTam(mk) if isinstance(mk, str) and mk else None

    def _luu_mat_khau(self, khoa: str, email: str, mat_khau: BiMatTam) -> None:
        """Lưu mật khẩu theo cả khoá-jar và email (dang_nhap_lai chỉ có email).

        Lưu hỏng → chỉ ghi nhật ký: lần dang_nhap_lai sau sẽ fail-closed với
        «khong_co_mat_khau_luu», không bao giờ thành công âm thầm."""
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
            log.warning("grok-web: không lưu được mật khẩu: %s", type(e).__name__)

    def co_tai_khoan(self, khoa: str) -> bool:
        """Module có biết khoá/email này không?"""
        khoa = str(khoa or "").strip().lower()
        if self._tim_jar(khoa) is not None:
            return True
        return self.mat_khau_da_luu(khoa) is not None

    def ho_so_trinh_duyet(self, khoa: str) -> str:
        """ID workspace cho noVNC: đúng khuôn chữ/số/gạch ngang/gạch dưới."""
        bam = hashlib.sha256(str(khoa).encode("utf-8")).hexdigest()[:16]
        return f"grok-web-{bam}"

    # -- đăng nhập theo hợp đồng NV67 --------------------------------------

    # -- đăng nhập theo hợp đồng NV67 --------------------------------------

    #: Số lượt tối đa cố vượt thử thách truy cập trước khi bó tay → can_nguoi.
    #: Dùng chung hằng của lõi (NV91 tầng b và NV92 dùng luôn).
    _SO_LUOT_VUOT_THU_THACH_TOI_DA = SO_LUOT_VUOT_THU_THACH_TOI_DA

    #: Trang SSO của Google (chế độ "google"). CHƯA KIỂM với X/Google thật —
    #: luồng thật bấm nút «đăng nhập bằng Google» trên x.com rồi chuyển tới đây.
    _URL_SSO_GOOGLE = "https://accounts.google.com/"

    async def _dang_nhap_bang_trinh_duyet(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet) -> KetQuaDangNhap:
        """BƯỚC ĐĂNG NHẬP DUY NHẤT NẰM TRONG MODULE: lõi chỉ đưa bộ điều khiển.

        Hai chế độ (cấu hình ``che_do_dang_nhap``): ``truc_tiep`` — email +
        mật khẩu trên x.com/login; ``google`` — SSO Google, lưu phiên Google
        để lần sau chỉ bấm «tiếp tục với Google». Cả hai đều cố vượt thử thách
        truy cập (tối đa 3 lượt, bằng helper lõi dùng chung
        ``vuot_thu_thach_toi_da``) trước khi bó tay → ``can_nguoi``.

        CAPTCHA chữ / 2FA / xác minh thiết bị: nhận diện bằng CẤU TRÚC qua
        tin_hieu() sau mỗi lần chuyển trang, trả ``can_nguoi`` NGAY trước khi
        cố vượt — captcha chữ «phát hiện được, không giải được» (cấm thêm gói
        nên không OCR; cấm dịch vụ giải hộ).
        """
        if trinh_duyet is None:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        if self._che_do_dang_nhap == "google":
            return await self._dang_nhap_che_do_google(email, mat_khau,
                                                       trinh_duyet)
        return await self._dang_nhap_che_do_truc_tiep(email, mat_khau,
                                                     trinh_duyet)

    async def _kiem_captcha_som(
            self, trinh_duyet) -> KetQuaDangNhap | None:
        """Đọc tín hiệu trang HIỆN TẠI; captcha chữ / 2FA / xác minh thiết bị →
        ``can_nguoi`` NGAY (không cố vượt/giải). Trả None nếu được phép tiếp tục.

        Gọi sau mỗi ``mo_trang`` và TRƯỚC mỗi lượt cố vượt thử thách: captcha
        chữ «phát hiện được, không giải được» (cấm thêm gói nên không OCR;
        cấm dịch vụ giải hộ).
        """
        try:
            tin_hieu = trinh_duyet.tin_hieu()
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if (la_captcha_chu(tin_hieu) or "2fa" in tin_hieu
                or "xac_minh" in tin_hieu):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        return None

    def _doc_phien_google(self) -> dict:
        """Phiên Google đã lưu (kho riêng của module); hỏng/không có → {}."""
        if self.kho is None:
            return {}
        try:
            ds = self.kho.doc("phien_google") or {}
        except Exception:
            return {}
        return dict(ds) if isinstance(ds, dict) else {}

    def _luu_phien_google(self, jar: dict) -> None:
        """Lưu cookie phiên Google vào kho riêng (0600) để lần sau chỉ cần bấm
        «tiếp tục với Google». Lưu hỏng → chỉ ghi nhật ký (lần sau nhập lại)."""
        if self.kho is None or not isinstance(jar, dict) or not jar:
            return
        try:
            self.kho.luu("phien_google",
                         {str(k): str(v) for k, v in jar.items()})
        except Exception as e:
            log.warning("grok-web: không lưu được phiên Google: %s",
                        type(e).__name__)

    async def _dang_nhap_che_do_truc_tiep(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập trực tiếp bằng email/mật khẩu trên x.com (như OpenAI)."""
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        kiem_som = await self._kiem_captcha_som(trinh_duyet)
        if kiem_som is not None:
            return kiem_som
        if not await vuot_thu_thach_toi_da(
                trinh_duyet, self._SO_LUOT_VUOT_THU_THACH_TOI_DA):
            # Vượt không được sau 3 lượt → «cần người» như NV90 (người tích
            # ô tay qua noVNC ở tầng d, rồi xoa-can-nguoi để chạy tiếp).
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            tin_hieu = trinh_duyet.tin_hieu()
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if (la_captcha_chu(tin_hieu) or "2fa" in tin_hieu
                or "xac_minh" in tin_hieu):
            # CAPTCHA/2FA/xác minh thiết bị: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        mat_khau_tho = mat_khau.lay()
        try:
            await trinh_duyet.dien("email", email)
            await trinh_duyet.dien("mat_khau", mat_khau_tho)
            await trinh_duyet.bam("dang_nhap")
        finally:
            mat_khau_tho = None  # noqa: F841
        return await self._doc_va_kiem_phien(trinh_duyet, email, mat_khau)

    async def _dang_nhap_che_do_google(self, email: str, mat_khau: BiMatTam,
                                       trinh_duyet) -> KetQuaDangNhap:
        """Đăng nhập qua Google SSO; lưu phiên Google để lần sau không nhập lại.

        CHƯA KIỂM với X/Google thật — luồng thật có thể cần thêm bước."""
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        kiem_som = await self._kiem_captcha_som(trinh_duyet)
        if kiem_som is not None:
            return kiem_som
        if not await vuot_thu_thach_toi_da(
                trinh_duyet, self._SO_LUOT_VUOT_THU_THACH_TOI_DA):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            await trinh_duyet.bam("dang_nhap_google")
            await trinh_duyet.mo_trang(self._URL_SSO_GOOGLE)
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        kiem_som = await self._kiem_captcha_som(trinh_duyet)
        if kiem_som is not None:
            return kiem_som
        if not await vuot_thu_thach_toi_da(
                trinh_duyet, self._SO_LUOT_VUOT_THU_THACH_TOI_DA):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            tin_hieu = trinh_duyet.tin_hieu()
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if (la_captcha_chu(tin_hieu) or "2fa" in tin_hieu
                or "xac_minh" in tin_hieu):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        if self._doc_phien_google():
            # Đã có phiên Google lưu → chỉ bấm «tiếp tục với Google»,
            # không nhập lại email/mật khẩu.
            try:
                await trinh_duyet.bam("tiep_tuc_google")
            except Exception:
                return KetQuaDangNhap(False, ma_loi="loi_module")
        else:
            mat_khau_tho = mat_khau.lay()
            try:
                await trinh_duyet.dien("email", email)
                await trinh_duyet.dien("mat_khau", mat_khau_tho)
                await trinh_duyet.bam("dang_nhap")
            finally:
                mat_khau_tho = None  # noqa: F841
            try:
                jar_google = trinh_duyet.doc_cookie("accounts.google.com")
            except Exception:
                jar_google = {}
            # Lưu hỏng → _luu_phien_google chỉ ghi nhật ký, lần sau nhập lại.
            self._luu_phien_google(jar_google)
        return await self._doc_va_kiem_phien(trinh_duyet, email, mat_khau)

    async def _doc_va_kiem_phien(self, trinh_duyet, email: str,
                                 mat_khau: BiMatTam) -> KetQuaDangNhap:
        """Đọc cookie grok.com sau bước trình duyệt, kiểm phiên bằng cấu trúc.

        «Đã đăng nhập» được kết luận bằng cấu trúc (phiên dùng được khi gọi
        kiểm phiên), KHÔNG bằng tên cookie đoán mò — chỉ yêu cầu cookie «sso»
        vì đó là điều kiện module đã khai ở _nap_tu."""
        try:
            jar = trinh_duyet.doc_cookie("grok.com")
        except Exception:
            jar = {}
        if not isinstance(jar, dict) or not jar.get("sso"):
            return KetQuaDangNhap(False, ma_loi="khong_lay_duoc_phien")
        try:
            await lay_uid_tu_phien(jar, self._goc_https)
        except LoiPhienGrokWeb:
            return KetQuaDangNhap(False, ma_loi="phien_khong_dung_duoc",
                                  ma_trang_thai=401)
        except LoiGrokWeb:
            return KetQuaDangNhap(False, ma_loi="loi_may_chu")
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_mang")
        chuoi_cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
        self._them_jar(jar)
        khoa = self._khoa_tu_jar(jar)
        self._luu_mat_khau(khoa, email, mat_khau)
        return KetQuaDangNhap(True, cookie=chuoi_cookie)

    async def dang_nhap(self, tai_khoan: DongDaTach) -> KetQuaDangNhap:
        """Hợp đồng NV67: cookie → kiểm; mat_khau → trình duyệt; dang_nhap_lai →
        mật khẩu đã lưu. BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        try:
            if loai == "cookie":
                chuoi = tai_khoan.cookie.lay() if tai_khoan.cookie else ""
                jar = _tach_cookie(chuoi)
                if not jar.get("sso"):
                    return KetQuaDangNhap(False, ma_loi="thieu_cookie")
                try:
                    await lay_uid_tu_phien(jar, self._goc_https)
                except LoiThuThachTruyCap:
                    # 403/503 + cf-mitigated: challenge → «cần người»,
                    # KHÔNG phải «cookie sai».
                    return KetQuaDangNhap(False, ma_loi="can_nguoi")
                except LoiPhienGrokWeb:
                    return KetQuaDangNhap(False, ma_trang_thai=401)
                except LoiGrokWeb:
                    return KetQuaDangNhap(False, ma_loi="loi_may_chu")
                except Exception:
                    return KetQuaDangNhap(False, ma_loi="loi_mang")
                self._them_jar(jar)
                chuoi_cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
                return KetQuaDangNhap(True, cookie=chuoi_cookie)
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

    # -- các tầng khôi phục -------------------------------------------------

    async def lam_moi_phien(self, khoa: str) -> KetQuaKhoiPhuc:
        """Tầng (a): kiểm phiên còn sống bằng /api/auth/session (lay_uid_tu_phien).

        grok.com không có endpoint làm mới cookie công khai — tầng này chỉ kiểm
        tra; hỏng thì lõi sang tầng (b)/(c)."""
        tim = self._tim_jar(khoa)
        if tim is None:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="khoa_khong_co")
        _, jar = tim
        try:
            await lay_uid_tu_phien(jar, self._goc_https)
        except LoiThuThachTruyCap:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="can_nguoi",
                                  can_nguoi=True)
        except LoiPhienGrokWeb:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="het_han",
                                  ma_trang_thai=401)
        except LoiGrokWeb as e:
            return KetQuaKhoiPhuc(False, tang="a", ma_loi="loi_may_chu",
                                  ma_trang_thai=getattr(e, "ma", None))
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
        ds = [self._khoa_tu_jar(jar) for jar in self._tai_khoan]
        khoa = str(khoa)
        con_khoe = [k for k in ds if k != khoa and k not in self._hong]
        return con_khoe[0] if con_khoe else None

    def danh_dau_hong(self, khoa: str, ma_loi: str) -> None:
        """Đánh dấu tài khoản hỏng để tầng (c) bỏ qua."""
        self._hong.add(str(khoa))

    def khoa_tai_khoan(self) -> list[str]:
        """NV91-P2: khoá mờ mọi tài khoản (vòng làm mới định kỳ của lõi liệt kê)."""
        return [self._khoa_tu_jar(jar) for jar in self._tai_khoan]

    def danh_dau_khoe(self, khoa: str) -> None:
        """NV91-P2: tài khoản khoẻ lại sau tự khôi phục → bỏ đánh dấu hỏng."""
        self._hong.discard(str(khoa))

    # ── Xoay tài khoản ──────────────────────────────────────────────────

    def _thu_tu_tai_khoan(self) -> list[int]:
        bay_gio = time.monotonic()
        san_sang = [i for i in range(len(self._tai_khoan))
                    if self._nghi_den.get(i, 0.0) <= bay_gio]
        if san_sang:
            return san_sang
        # Không bao giờ chặn lựa chọn cuối: tất cả đang nghỉ thì vẫn thử,
        # tài khoản hết nghỉ sớm nhất trước.
        return sorted(range(len(self._tai_khoan)),
                      key=lambda i: self._nghi_den.get(i, 0.0))

    def _danh_dau_loi(self, idx: int, loi: Exception) -> None:
        if isinstance(loi, LoiHanMucGrokWeb):
            self._nghi_den[idx] = time.monotonic() + NGHI_SAU_HET_LUOT
            log.info("grok-web: tài khoản #%d hết lượt, nghỉ %ds",
                     idx, int(NGHI_SAU_HET_LUOT))
        else:
            log.info("grok-web: tài khoản #%d lỗi %s",
                     idx, type(loi).__name__)
            if isinstance(loi, LoiPhienGrokWeb):
                # NV91-P2: lỗi phiên nhận ra bằng CẤU TRÚC (không dò chữ) → tự
                # khôi phục MỘT lần cho đúng tài khoản (single-flight).
                try:
                    khoa = self._khoa_tu_jar(self._tai_khoan[idx])
                except (IndexError, TypeError, AttributeError):
                    khoa = None
                if khoa:
                    tu_lam_moi.bao_loi_phien(self, khoa)

    def _danh_dau_khoe(self, idx: int) -> None:
        self._nghi_den.pop(idx, None)

    async def _khach(self, idx: int) -> KhachGrokWeb:
        jar = self._tai_khoan[idx]
        uid = await lay_uid_tu_phien(jar, self._goc_https)
        return KhachGrokWeb(self._ws_co_so, jar, uid)

    async def _luong_van_ban(
        self, che_do: str, loi_nhan: str
    ) -> AsyncIterator[str]:
        """Yield chữ của lượt chat, tự xoay tài khoản khi lỗi TRƯỚC khi có chữ.

        Mọi lỗi xảy ra trước khi có chữ nào (lỗi phiên, hết lượt, lỗi máy chủ,
        mất kết nối…) đều chuyển sang tài khoản kế. Đã có chữ rồi mà lỗi thì
        dừng (không xoay để khỏi hỏi Grok hai lần). Hết tài khoản mà thử được
        thì ném lỗi cuối cùng.
        """
        loi_cuoi: Exception | None = None
        for idx in self._thu_tu_tai_khoan():
            da_co_chu = False
            try:
                khach = await self._khach(idx)
            except LoiGrokWeb as e:
                self._danh_dau_loi(idx, e)
                loi_cuoi = e
                continue
            try:
                async for manh in khach.chay(che_do, loi_nhan):
                    da_co_chu = True
                    yield manh
                self._danh_dau_khoe(idx)
                return
            except LoiGrokWeb as e:
                self._danh_dau_loi(idx, e)
                loi_cuoi = e
                if da_co_chu:
                    break  # đã stream dở: dừng, không xoay
                continue
        raise loi_cuoi or LoiGrokWeb("không có tài khoản nào dùng được")

    # ── Chat ───────────────────────────────────────────────────────────

    def _loi_chat(self, loi: Exception) -> tuple[int, dict, bytes]:
        tieu_de = {"content-type": "application/json"}
        if isinstance(loi, LoiPhienGrokWeb):
            return 401, tieu_de, _loi_openai(
                "Phiên Grok hết hạn hoặc cookie bị từ chối — đăng nhập lại grok.com rồi cập nhật cookie.",
                "authentication_error", "loi_phien",
            )
        if isinstance(loi, LoiHanMucGrokWeb):
            tieu_de["retry-after"] = "60"
            return 429, tieu_de, _loi_openai(
                "Tài khoản Grok đã hết lượt dùng cho chế độ này — thử lại sau hoặc thêm tài khoản khác.",
                "rate_limit_error", "het_luot",
            )
        if isinstance(loi, LoiGrokWebRong):
            return 502, tieu_de, _loi_openai(
                "Grok xong nhưng không trả chữ nào.",
                "upstream_error", "tra_loi_rong",
            )
        if isinstance(loi, LoiMayChuGrokWeb):
            return 502, tieu_de, _loi_openai(
                "Máy chủ Grok kết thúc phiên với trạng thái lỗi.",
                "upstream_error", "loi_may_chu",
            )
        return 502, tieu_de, _loi_openai(
            f"Lỗi khi gọi grok.com ({type(loi).__name__}).",
            "upstream_error", "loi_grok",
        )

    async def chat(self, body: dict, stream: bool):
        """Chuyển tiếp yêu cầu chat OpenAI tới Grok web."""
        if not self._tai_khoan:
            return 404, {"content-type": "application/json"}, _loi_openai(
                "Module grok-web chưa được cấu hình tài khoản («tai_khoan»).",
                "module_not_configured", "missing_credentials",
            )
        model = str(body.get("model") or "")
        che_do = tach_che_do(model)
        ten_model = f"{self._tien_to}/{che_do}"
        loi_nhan = hop_van_ban(body.get("messages") or [])
        if not loi_nhan.strip():
            return 400, {"content-type": "application/json"}, _loi_openai(
                "Thiếu nội dung tin nhắn.", "invalid_request_error", "missing_messages",
            )

        if stream:
            # Lấy mảnh đầu NGAY để lỗi trước stream ánh xạ đúng mã HTTP;
            # lỗi giữa stream thì kết thúc không [DONE] (ghi log).
            gen = self._luong_van_ban(che_do, loi_nhan)
            try:
                manh_dau = await gen.__anext__()
            except StopAsyncIteration:
                return self._loi_chat(LoiGrokWebRong("luồng rỗng"))
            except LoiGrokWeb as e:
                return self._loi_chat(e)

            async def tiep() -> AsyncIterator[str]:
                yield manh_dau
                try:
                    async for manh in gen:
                        yield manh
                except LoiGrokWeb as e:
                    log.warning("grok-web: lỗi giữa stream (%s) — kết thúc không [DONE]",
                                type(e).__name__)
                    raise LoiGiuaLuu() from e

            return (
                200,
                {"content-type": "text/event-stream"},
                luong_sse_openai(tiep(), ten_model),
            )

        try:
            noi_dung = await gom_json_openai(
                self._luong_van_ban(che_do, loi_nhan), ten_model
            )
        except LoiGrokWeb as e:
            return self._loi_chat(e)
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(noi_dung, ensure_ascii=False).encode("utf-8"),
        )

    # ── Danh sách model ────────────────────────────────────────────────

    async def models(self) -> list[dict]:
        """Các chế độ của web app Grok dưới dạng model ``grok/<chế độ>``.

        NV93: id ổn định + nhãn tách riêng; mỗi model kèm gói/tài khoản dùng
        được (grok chưa phân biệt gói theo tài khoản — CHƯA KIỂM thật).
        """
        ds_khoa = [self._khoa_tu_jar(j) for j in self._tai_khoan]
        return [
            {"id": f"{self._tien_to}/{m}", "object": "model",
             "owned_by": "grok-web", "nhan": f"Grok {m}",
             "goi": "khong_ro", "available_cho": list(ds_khoa)}
            for m in CAC_CHE_DO
        ]

    # ── Sức khoẻ ──────────────────────────────────────────────────────

    async def suc_khoe(self) -> dict:
        """Tình trạng module — không để lộ cookie."""
        if not self._tai_khoan:
            return {
                "trang_thai": "chưa cấu hình",
                "chi_tiet": "Thiếu danh sách cookie tài khoản («tai_khoan»)",
            }
        return {
            "trang_thai": "sẵn sàng",
            "ghi_chu": f"{len(self._tai_khoan)} tài khoản đã cấu hình",
        }

    # ── Dừng ───────────────────────────────────────────────────────────

    async def dung(self) -> None:
        """Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký (khuôn NV77).
        Thể hiện thay thế (nap sau) đã ghi đè chu_so_huu nên không bị gỡ nhầm."""
        try:
            if self.kho is not None:
                du_lieu = self.kho.doc("danh_tinh") or {}
                if du_lieu.get("chu_so_huu") == id(self):
                    for dt_id in du_lieu.get("gan") or []:
                        try:
                            danh_tinh.go(dt_id, "grok-web")
                        except Exception as e:
                            log.warning("grok-web: gỡ tham chiếu danh tính %s khi dừng: %s",
                                        dt_id, type(e).__name__)
                    self.kho.luu("danh_tinh",
                                 {"anh_xa": du_lieu.get("anh_xa") or {},
                                  "gan": [], "chu_so_huu": id(self)})
        except Exception as e:
            log.warning("grok-web: dọn tham chiếu danh tính khi dừng: %s",
                        type(e).__name__)
        self._nghi_den = {}
