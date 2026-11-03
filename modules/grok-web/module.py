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

from app import model_bo_sung
from app import tu_lam_moi
from app.chuyen_tiep import ds_khoa
from app.khoi_phuc_phien import (
    SO_LUOT_VUOT_THU_THACH_TOI_DA,
    KetQuaKhoiPhuc,
    TaiKhoanCanKhoiPhuc,
    la_captcha_chu,
    vuot_thu_thach_toi_da,
)
from app.tai_khoan_hang_loat import BiMatTam, BiMatTotp, DongDaTach, KetQuaDangNhap
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
    CheDoKhongCo,
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


_QUY_TAC_TIN_HIEU = [{'loai': 'cookie', 'mien': 'grok.com', 'ten': 'sso', 'tin_hieu': 'da_dang_nhap'}, {'loai': 'url_chua', 'gia_tri': 'accounts.x.ai', 'tin_hieu': 'chua_dang_nhap'}]

class Module:
    """Điểm vào của module theo hợp đồng CHUAN-MODULE."""

    #: NV300: cách đăng nhập module này hỗ trợ theo từng tài khoản.
    CACH_DANG_NHAP_HO_TRO = ("google", "email")

    def __init__(self, manifest: dict | None = None) -> None:
        manifest = manifest or {}
        self._manifest = manifest
        self._tien_to = str(manifest.get("tien_to") or "grok")
        self._tai_khoan: list[dict[str, str]] = []  # cookie từng tài khoản
        self._ws_co_so = ""
        self._goc_https = GOC_HTTPS_MAC_DINH
        self._nghi_den: dict[int, float] = {}  # chỉ số tài khoản → mốc hết nghỉ
        self.kho = None  # lõi gán sau khi tạo (kho riêng của module)
        self._hong: set[str] = set()  # khoá tài khoản tầng (c) đã đánh dấu hỏng
        # Bộ điều khiển trình duyệt cho đăng nhập bằng mật khẩu (NV89):
        # test gán; triển khai thật nối workspace vnc-xa (chưa có trong lượt này).
        self._tao_trinh_duyet = None
        # Trạng thái đăng nhập tự động RIÊNG của module này (thêm 2026-10-30,
        # theo mẫu module chatgpt-web/gemini-web ở fb8aa25, tự viết lại cho
        # grok-web, không import logic của module kia, không qua lõi):
        self._ham_tao_trinh_duyet_rieng = None
        self._khoa_tu_dong = None
        self._captcha_den_het = 0.0
        self._captcha_den_khoa = None
        self._captcha_hoan_giay = 1800.0
        self._lan_t2 = {}
        # Trình duyệt vừa dùng (test kiểm tra thứ tự bước).
        self._trinh_duyet_da_dung = None
        # Chế độ đăng nhập: "truc_tiep" (email+mật khẩu trên form đăng nhập của grok.com) hoặc
        # "google" (SSO Google, lưu phiên). Mặc định "truc_tiep".
        # NV300: chỉ còn làm MẶC ĐỊNH ĐỌC NGƯỢC cho tài khoản chưa có cách
        # riêng; quyết định thật theo từng tài khoản (xem cach_dang_nhap_cua).
        self._che_do_dang_nhap = "truc_tiep"
        # NV300: cách đăng nhập theo từng tài khoản (khoá/email → cách +
        # hồ sơ Google), lưu kho riêng; thể hiện mới nạp lại từ kho.
        self._cach_theo_khoa: dict[str, dict] = {}
        # Làm giàu NV93: khoa -> {email, ho_so, goi, trang_thai, lan_dung_cuoi}
        self._lam_giau: dict[str, dict] = {}

    # ── Nạp / cài đặt ──────────────────────────────────────────────────

    def _nap_tu(self, cau_hinh: dict) -> str | None:
        """Nạp cấu hình vào bộ nhớ; trả về thông điệp lỗi hoặc None nếu ổn.

        """
        ds = ds_khoa(cau_hinh.get("tai_khoan"))
        tai_khoan = []
        for chuoi in ds:
            jar = _tach_cookie(chuoi)
            if not jar.get("sso"):
                return "mỗi tài khoản phải có cookie «sso»"
            tai_khoan.append(jar)
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
                    f"grok.com) hoặc «google» (SSO Google), nhận {che_do!r}.")
        self._che_do_dang_nhap = che_do
        # NV93: bỏ tài khoản đã bị go_tai_khoan gỡ tường minh; khôi phục bản
        # ghi làm giàu từ kho riêng (khởi động lại không mất).
        self._tai_khoan = []
        go_bo = self._doc_go_bo()
        ban_ghi = self._doc_ban_ghi()
        self._lam_giau = {}
        for jar in tai_khoan:
            khoa = self._khoa_tu_jar(jar)
            if khoa in go_bo:
                continue  # đã bị go_tai_khoan gỡ tường minh — không dựng lại
            self._tai_khoan.append(jar)
            g = ban_ghi.get(khoa)
            if isinstance(g, dict):
                self._lam_giau[khoa] = g
        self._ws_co_so = ws_co_so
        self._goc_https = goc_https
        self._nghi_den = {}
        log.info("grok-web: đã nạp %d tài khoản", len(self._tai_khoan))
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
            # Lõi chỉ lưu/nạp lại những gì cai() trả trong «cau_hinh_luu»
            # (app/so_module.py::_cai); thiếu là lõi lưu {} và bản phục vụ
            # nạp cấu hình trống → suc_khoe tự nhận «chưa cấu hình» (NV189).
            "cau_hinh_luu": {
                "tai_khoan": ds_khoa((cau_hinh or {}).get("tai_khoan")),
                "ws_url": str((cau_hinh or {}).get("ws_url") or "").strip(),
                "che_do_dang_nhap": str(
                    (cau_hinh or {}).get("che_do_dang_nhap") or "truc_tiep"
                ).strip().lower(),
            },
        }

    # ── Cách đăng nhập theo từng tài khoản (NV300) ──────────────────────

    def _doc_cach(self) -> dict:
        """Đọc bảng cách theo tài khoản từ kho riêng (hỏng → {})."""
        if self.kho is None:
            return {}
        try:
            ds = self.kho.doc("cach_dang_nhap") or {}
        except Exception:
            return {}
        return ds if isinstance(ds, dict) else {}

    def _luu_cach(self, ds: dict) -> None:
        if self.kho is None:
            return
        try:
            self.kho.luu("cach_dang_nhap", ds)
        except Exception as _loi:
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "_luu_cach", type(_loi).__name__)

    def dat_cach_dang_nhap(self, khoa, cach, ho_so_google=None,
                           email="") -> bool:
        """Đặt cách đăng nhập cho một tài khoản (NV300).

        Ghi theo cả khoá và email (khoá nào có thì ghi khoá đó); hồ sơ
        Google chỉ ghi đè khi truyền tường minh (không xoá lựa chọn cũ
        khi lần gọi chỉ đổi cách). Sai giá trị cách → False.
        """
        cach = str(cach or "").strip().lower()
        if cach not in ("google", "email"):
            return False
        khoa_chia = {str(khoa or "").strip(),
                     str(email or "").strip().lower()} - {""}
        if not khoa_chia:
            return False
        ds = self._doc_cach()
        for khoa_cu in khoa_chia:
            muc = dict(self._cach_theo_khoa.get(khoa_cu) or {})
            muc["cach"] = cach
            if ho_so_google is not None:
                muc["ho_so_google"] = str(ho_so_google or "").strip()
            self._cach_theo_khoa[khoa_cu] = muc
            ds[khoa_cu] = dict(muc)
        self._luu_cach(ds)
        return True

    def cach_dang_nhap_cua(self, khoa="", email="", goi_y=None) -> str:
        """Cách đăng nhập HIỆU LỰC của một tài khoản (NV300).

        Ưu tiên: gợi ý tường minh của lần gọi → đã đặt theo tài khoản →
        cài đặt chung cũ đọc ngược (truc_tiep → email, google → google).
        Không bao giờ đoán từ tên tài khoản.
        """
        if isinstance(goi_y, str) and goi_y.strip().lower() in ("google",
                                                               "email"):
            return goi_y.strip().lower()
        for khoa_cu, muc in self._doc_cach().items():
            if khoa_cu not in self._cach_theo_khoa and isinstance(muc, dict):
                self._cach_theo_khoa[khoa_cu] = muc
        for khoa_cu in (str(khoa or "").strip(),
                        str(email or "").strip().lower()):
            if not khoa_cu:
                continue
            muc = self._cach_theo_khoa.get(khoa_cu)
            if isinstance(muc, dict):
                cach = str(muc.get("cach") or "").strip().lower()
                if cach in ("google", "email"):
                    return cach
        return "google" if self._che_do_dang_nhap == "google" else "email"

    def ho_so_google_cua(self, khoa="", email="") -> str:
        """Id hồ sơ Google đã chọn của một tài khoản (NV300); chưa có → ""."""
        for khoa_cu in (str(khoa or "").strip(),
                        str(email or "").strip().lower()):
            if not khoa_cu:
                continue
            muc = self._cach_theo_khoa.get(khoa_cu)
            if isinstance(muc, dict):
                return str(muc.get("ho_so_google") or "")
        return ""

    # ── Tái dùng hồ sơ Google (NV93 — đường chính) ─────────────────────────

    _URL_TAI_DUNG = "https://grok.com/"
    _BO_CHON_GOOGLE = ('button[aria-label*="Sign in with Google" i], '
                       'button[aria-label*="Google" i]')
    _TIN_HIEU_CAN_NGUOI = {"captcha", "2fa", "xac_minh", "xac_minh_thiet_bi",
                           "cloudflare", "thu_thach_truy_cap"}

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
            tin_hieu = set(await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu()) or set())
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
                cookie = await trinh_duyet.doc_cookie("grok.com")
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

    # NV300: đường TRỰC TIẾP = form email + mật khẩu của CHÍNH grok.com (màn
    # «Đăng nhập»: bốn nút nhà cung cấp, dòng «hoặc», ô email, nút «Tiếp tục», rồi
    # bước mật khẩu). Trước đây mở x.com/login (tài khoản X) — sai với tài khoản
    # hotmail/outlook. CHƯA KIỂM trên grok.com thật (cần NV293, dừng ở CAPTCHA/
    # Cloudflare, không vượt). Bộ điều khiển chọn phần tử theo tên logic
    # («email», «tiep_tuc», «mat_khau», «dang_nhap») theo CẤU TRÚC — module
    # không bao giờ gọi tên nút nhà cung cấp (Google/X/Apple/Github).
    _URL_DANG_NHAP = "https://grok.com/sign-in"

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
        """Thêm jar vào danh sách (lũy đẳng)."""
        if jar not in self._tai_khoan:
            self._tai_khoan.append(dict(jar))

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

    def quy_tac_tin_hieu(self) -> list:
        """Quy tắc tín hiệu bằng CẤU TRÚC (NV94-b): module KHAI, mã tín hiệu MỞ.

        Dịch vụ thực thi: cookie đặc trưng có/không, URL đích — không dò chữ.
        Chỉ NHẬN RA thử thách → can_nguoi (không tự vượt).
        """
        return [dict(q) for q in _QUY_TAC_TIN_HIEU]
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

    #: Tín hiệu «cần người» ngoài captcha/2fa/xac_minh mà một bộ điều khiển
    #: thật có thể báo: selfie, điện thoại, khóa tài khoản, tap. Gặp bất kỳ
    #: tín hiệu nào trong này cũng dừng ở can_nguoi, không cố vượt/giải.
    _TIN_HIEU_CAN_NGUOI_BO_SUNG = {
        "selfie", "dien_thoai", "khoa_tai_khoan", "tap",
        "need_tap", "need_code", "xac_minh_thiet_bi",
    }

    async def _dang_nhap_bang_trinh_duyet(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet, totp=None,
                                          ma_loi_rieng: bool = False,
                                          cach_dang_nhap=None
                                          ) -> KetQuaDangNhap:
        """BƯỚC ĐĂNG NHẬP DUY NHẤT NẰM TRONG MODULE: lõi chỉ đưa bộ điều khiển.

        Hai đường theo CÁCH CỦA TỪNG TÀI KHOẢN (NV300): ``email`` — email +
        mật khẩu trên form grok.com/sign-in; ``google`` — SSO Google, lưu phiên Google
        để lần sau chỉ bấm «tiếp tục với Google». Không truyền cách thì đọc
        ngược cài đặt chung cũ ``che_do_dang_nhap``. Cả hai đều cố vượt thử thách
        truy cập (tối đa 3 lượt, bằng helper lõi dùng chung
        ``vuot_thu_thach_toi_da``) trước khi bó tay → ``can_nguoi``.

        CAPTCHA chữ / 2FA / xác minh thiết bị: nhận diện bằng CẤU TRÚC qua
        tin_hieu() sau mỗi lần chuyển trang, trả ``can_nguoi`` NGAY trước khi
        cố vượt — captcha chữ «phát hiện được, không giải được» (cấm thêm gói
        nên không OCR; cấm dịch vụ giải hộ).

        ``totp`` / ``ma_loi_rieng`` chỉ khoi_phuc_tu_dong dùng: chế độ
        truc_tiep được điền mã 2FA từ TOTP đã lưu, thử thách trước khi điền
        trả mã riêng ``thu_thach_truoc_khi_dien``, hết thời gian trả
        ``het_gio``; đường dang_nhap() cũ không đổi (can_nguoi/loi_module).
        Chế độ google KHÔNG điền TOTP: nó chỉ tái dùng hồ sơ Google đã đăng
        nhập (tai_dung_ho_so không bao giờ dien()), gặp 2FA vẫn can_nguoi.
        """
        if trinh_duyet is None:
            return KetQuaDangNhap(
                False,
                ma_loi=("trinh_duyet_none" if ma_loi_rieng else "can_nguoi"))
        cach = cach_dang_nhap
        if cach not in ("google", "email"):
            cach = "google" if self._che_do_dang_nhap == "google" else "email"
        if cach == "google":
            return await self._dang_nhap_che_do_google(email, mat_khau,
                                                       trinh_duyet)
        return await self._dang_nhap_che_do_truc_tiep(
            email, mat_khau, trinh_duyet, totp=totp,
            ma_loi_rieng=ma_loi_rieng)

    def _co_tin_hieu_can_nguoi_khac_2fa(self, tin_hieu) -> bool:
        """Captcha / xác minh / selfie / điện thoại / khóa / tap → cần người.

        2FA KHÔNG nằm ở đây: 2FA có TOTP đã lưu thì được điền mã, không có
        TOTP mới dừng ở can_nguoi (xử lý riêng ở nơi gọi).
        """
        try:
            tap = set(tin_hieu or [])
        except Exception as _loi:
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "_co_tin_hieu_can_nguoi_khac_2fa", type(_loi).__name__)
            return False
        if la_captcha_chu(tap):
            return True
        if tap & self._TIN_HIEU_CAN_NGUOI_BO_SUNG:
            return True
        return any(str(s).startswith("xac_minh") for s in tap)

    async def _kiem_captcha_som(
            self, trinh_duyet, cho_phep_2fa: bool = False,
            ma_loi_rieng: bool = False
            ) -> KetQuaDangNhap | None:
        """Đọc tín hiệu trang HIỆN TẠI; captcha chữ / 2FA / xác minh thiết bị →
        ``can_nguoi`` NGAY (không cố vượt/giải). Trả None nếu được phép tiếp tục.

        Gọi sau mỗi ``mo_trang`` và TRƯỚC mỗi lượt cố vượt thử thách: captcha
        chữ «phát hiện được, không giải được» (cấm thêm gói nên không OCR;
        cấm dịch vụ giải hộ). ``cho_phep_2fa=True`` chỉ khi đã có TOTP để
        điền sau bước mật khẩu: lúc đó tín hiệu 2fa một mình không chặn,
        còn captcha/xác minh/selfie/điện thoại/khóa/tap vẫn chặn ngay.
        ``ma_loi_rieng=True`` (chỉ khoi_phuc_tu_dong dùng): thử thách khác
        2fa trước khi điền trả ``thu_thach_truoc_khi_dien``; 2fa không TOTP
        vẫn trả ``can_nguoi``.
        """
        try:
            tin_hieu = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if self._co_tin_hieu_can_nguoi_khac_2fa(tin_hieu):
            return KetQuaDangNhap(
                False,
                ma_loi=("thu_thach_truoc_khi_dien" if ma_loi_rieng
                        else "can_nguoi"))
        if "2fa" in tin_hieu and not cho_phep_2fa:
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        return None

    async def _dang_nhap_che_do_truc_tiep(self, email: str, mat_khau: BiMatTam,
                                          trinh_duyet, totp=None,
                                          ma_loi_rieng: bool = False
                                          ) -> KetQuaDangNhap:
        """Đăng nhập trực tiếp bằng email/mật khẩu trên form của grok.com (như OpenAI).

        Có TOTP đã lưu thì 2FA sau bước mật khẩu được điền mã qua kênh TOTP
        của module (xem _xu_ly_2fa); không có TOTP thì 2FA vẫn can_nguoi.
        ``ma_loi_rieng=True`` (chỉ khoi_phuc_tu_dong dùng): thử thách trước
        khi điền → thu_thach_truoc_khi_dien, hết thời gian → het_gio.
        """
        totp_dung = totp
        if totp_dung is None:
            try:
                totp_dung = self.totp_da_luu(email)
            except Exception:
                totp_dung = None
        try:
            await trinh_duyet.mo_trang(self._URL_DANG_NHAP)
        except (TimeoutError, asyncio.TimeoutError):
            return KetQuaDangNhap(
                False, ma_loi=("het_gio" if ma_loi_rieng else "loi_module"))
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        kiem_som = await self._kiem_captcha_som(
            trinh_duyet, cho_phep_2fa=totp_dung is not None,
            ma_loi_rieng=ma_loi_rieng)
        if kiem_som is not None:
            return kiem_som
        if not await vuot_thu_thach_toi_da(
                trinh_duyet, self._SO_LUOT_VUOT_THU_THACH_TOI_DA):
            # Vượt không được sau 3 lượt → «cần người» như NV90 (người tích
            # ô tay qua noVNC ở tầng d, rồi xoa-can-nguoi để chạy tiếp).
            # Đây là hành vi NV90 có sẵn của module, không phải giải captcha:
            # thử thách truy cập (Cloudflare) được cố vượt tối đa 3 lượt
            # bằng helper chung, không được thì dừng, không OCR/giải hộ.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        try:
            tin_hieu = await trinh_duyet.tin_hieu(self.quy_tac_tin_hieu())
        except Exception:
            return KetQuaDangNhap(False, ma_loi="loi_module")
        if self._co_tin_hieu_can_nguoi_khac_2fa(tin_hieu):
            # Vẫn trước khi điền: mã riêng khi khoi_phuc gọi.
            if ma_loi_rieng:
                return KetQuaDangNhap(
                    False, ma_loi="thu_thach_truoc_khi_dien")
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        if "2fa" in tin_hieu and totp_dung is None:
            # CAPTCHA/2FA/xác minh thiết bị: dừng, KHÔNG cố vượt.
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        mat_khau_tho = mat_khau.lay()
        try:
            # Hai bước như màn thật: email → «Tiếp tục» → mật khẩu → đăng nhập.
            await trinh_duyet.dien("email", email)
            await trinh_duyet.bam("tiep_tuc")
            await trinh_duyet.dien("mat_khau", mat_khau_tho)
            await trinh_duyet.bam("dang_nhap")
        finally:
            mat_khau_tho = None  # noqa: F841
        # Sau mật khẩu, nếu trang hiện 2FA: kênh TOTP của module.
        try:
            tin_hieu_sau = await trinh_duyet.tin_hieu(
                self.quy_tac_tin_hieu())
        except Exception:
            tin_hieu_sau = set()
        if "2fa" in tin_hieu_sau:
            if totp_dung is None:
                try:
                    totp_dung = self.totp_da_luu(email)
                except Exception:
                    totp_dung = None
            ma_2fa = await self._xu_ly_2fa(trinh_duyet, totp_dung)
            totp_dung = None
            if ma_2fa:
                return KetQuaDangNhap(False, ma_loi=ma_2fa)
        if self._co_tin_hieu_can_nguoi_khac_2fa(tin_hieu_sau):
            return KetQuaDangNhap(False, ma_loi="can_nguoi")
        return await self._doc_va_kiem_phien(trinh_duyet, email, mat_khau)

    async def _dang_nhap_che_do_google(self, email: str, mat_khau: BiMatTam,
                                       trinh_duyet) -> KetQuaDangNhap:
        """Chế độ "google": dùng `tai_dung_ho_so` (hồ sơ Google đã đăng nhập).

        NV94-P3: KHÔNG tự đăng nhập Google riêng, KHÔNG lưu cookie
        accounts.google.com vào kho module (bí mật giá trị cao mà không bao giờ
        nạp lại). Mật khẩu (nếu có) bị xoá sau khi dùng.
        """
        try:
            return await self.tai_dung_ho_so("", trinh_duyet)
        finally:
            if mat_khau is not None:
                mat_khau.xoa()


    async def _doc_va_kiem_phien(self, trinh_duyet, email: str,
                                 mat_khau: BiMatTam) -> KetQuaDangNhap:
        """Đọc cookie grok.com sau bước trình duyệt, kiểm phiên bằng cấu trúc.

        «Đã đăng nhập» được kết luận bằng cấu trúc (phiên dùng được khi gọi
        kiểm phiên), KHÔNG bằng tên cookie đoán mò — chỉ yêu cầu cookie «sso»
        vì đó là điều kiện module đã khai ở _nap_tu."""
        try:
            jar = await trinh_duyet.doc_cookie("grok.com")
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
        mật khẩu đã lưu; google → hồ sơ Google đã đăng nhập (NV300).
        BiMatTam luôn bị xoá sau khi dùng (kể cả lỗi)."""
        loai = tai_khoan.loai
        # NV300: lần gọi mang cách tường minh (đường lõi gắn từ bản ghi) thì
        # ghi nhớ theo tài khoản cho lần gọi sau không kèm cách.
        cach_goi = getattr(tai_khoan, "cach_dang_nhap", None)
        if isinstance(cach_goi, str) \
                and cach_goi.strip().lower() in ("google", "email"):
            self.dat_cach_dang_nhap(
                "", cach_goi,
                getattr(tai_khoan, "ho_so_google", None),
                email=tai_khoan.email)
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
            if loai == "google":
                # NV300: đăng nhập qua hồ sơ Google đã đăng nhập — không cần
                # mật khẩu, không điền gì (tai_dung_ho_so không bao giờ dien()).
                tao = self._tao_trinh_duyet
                td = tao() if callable(tao) else None
                self._trinh_duyet_da_dung = td
                ho_so = getattr(tai_khoan, "ho_so_google", "") or ""
                return await self.tai_dung_ho_so(ho_so, td)
            if loai == "mat_khau":
                if tai_khoan.mat_khau is None:
                    return KetQuaDangNhap(False, ma_loi="thieu_mat_khau")
                tao = self._tao_trinh_duyet
                td = tao() if callable(tao) else None
                self._trinh_duyet_da_dung = td
                cach = self.cach_dang_nhap_cua(
                    "", tai_khoan.email,
                    goi_y=getattr(tai_khoan, "cach_dang_nhap", None))
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, tai_khoan.mat_khau, td,
                    cach_dang_nhap=cach)
            if loai == "dang_nhap_lai":
                bm = self.mat_khau_da_luu(tai_khoan.email)
                cach = self.cach_dang_nhap_cua(
                    "", tai_khoan.email,
                    goi_y=getattr(tai_khoan, "cach_dang_nhap", None))
                if cach == "google":
                    tao = self._tao_trinh_duyet
                    td = tao() if callable(tao) else None
                    self._trinh_duyet_da_dung = td
                    ho_so = getattr(tai_khoan, "ho_so_google", "") or ""
                    return await self.tai_dung_ho_so(ho_so, td)
                if bm is None:
                    return KetQuaDangNhap(False, ma_loi="khong_co_mat_khau_luu")
                tao = self._tao_trinh_duyet
                td = tao() if callable(tao) else None
                self._trinh_duyet_da_dung = td
                return await self._dang_nhap_bang_trinh_duyet(
                    tai_khoan.email, bm, td, cach_dang_nhap=cach)
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
        """Tầng (b): đăng nhập lại qua trình duyệt, theo cách của tài khoản
        (NV300: google → tái dùng hồ sơ, không cần mật khẩu)."""
        try:
            cach = self.cach_dang_nhap_cua("", tai_khoan.email)
            if cach == "google":
                kq = await self.tai_dung_ho_so("", trinh_duyet)
                if kq.thanh_cong:
                    return KetQuaKhoiPhuc(True, tang="b")
                return KetQuaKhoiPhuc(False, tang="b",
                                      ma_loi=kq.ma_loi or "loi_module",
                                      ma_trang_thai=kq.ma_trang_thai,
                                      can_nguoi=(kq.ma_loi == "can_nguoi"))
            bm = tai_khoan.mat_khau
            if bm is None:
                return KetQuaKhoiPhuc(False, tang="b", ma_loi="thieu_mat_khau")
            kq = await self._dang_nhap_bang_trinh_duyet(
                tai_khoan.email, bm, trinh_duyet, cach_dang_nhap=cach)
            if kq.thanh_cong:
                return KetQuaKhoiPhuc(True, tang="b")
            return KetQuaKhoiPhuc(False, tang="b", ma_loi=kq.ma_loi or "loi_module",
                                  ma_trang_thai=kq.ma_trang_thai,
                                  can_nguoi=(kq.ma_loi == "can_nguoi"))
        finally:
            if tai_khoan.mat_khau is not None:
                tai_khoan.mat_khau.xoa()

    # -- đăng nhập tự động ĐỘC LẬP trong module này (thêm 2026-10-30) --
    # Kiến trúc, ghi rõ để không bị hiểu nhầm thành logic lõi hay logic
    # của module kia:
    # - web2api (app/) chỉ là phiên dịch/chuyển tiếp: nó gọi
    #   module.khoi_phuc_tu_dong() nếu module có (delegate mỏng đã có từ
    #   fb8aa25), rồi dịch kết quả sang phiên MCP, không tự quyết tầng,
    #   khóa hay hoãn captcha.
    # - Mọi quyết định đăng nhập của module này sống Ở ĐÂY, trong chính
    #   file module này, không import logic của module kia, không dùng
    #   logic khôi phục chung của lõi làm logic chính.
    # - Dịch vụ máy đăng nhập (dich-vu/may-dang-nhap, BrowserPool/solver)
    #   chỉ cung cấp bộ điều khiển trình duyệt qua factory đã đăng ký;
    #   nó không quyết tầng T0-T3 của module này.
    # - Tầng (a) của module này chỉ KIỂM phiên, không bịa endpoint làm mới:
    #   grok-web gọi /api/auth/session qua lay_uid_tu_phien (hoặc dùng
    #   cookie x-userid có sẵn). grok.com không có endpoint làm mới cookie
    #   công khai — đường cứu chính vẫn là T2 trình duyệt.
    # - Học từ TriTue2011/chatgpt2api (chỉ đọc, tự viết lại, không sao chép
    #   mã): thang T0 kiểm phiên → T1 nghỉ-kiểm lại → T2 đăng nhập lại
    #   bằng mật khẩu+TOTP → T3 kiểm lại; khóa serial toàn cục cho lượt
    #   đăng nhập Google; captcha là thử thách theo IP/trình duyệt nên
    #   hoãn; need_captcha/need_code/need_tap không có TOTP → cần người.
    #   CHỖ KHÔNG ÁP ĐƯỢC, nói rõ: chatgpt2api cho Grok (grok_web_login.py)
    #   là «bản phụ thuộc một cú tích tay» — đo 20/09/2026 Cloudflare của
    #   grok.com không tự qua được sau 60s, nên luồng đó mở grok.com →
    #   bấm «Sign in with Google» → đợi người tích tay, rồi mới đọc cookie
    #   từ hồ sơ (trừ nhóm cf_*), và chưa từng đăng nhập được nên KHÔNG
    #   đoán tên cookie phiên. Vì vậy khoi_phuc_tu_dong này KHÔNG hứa
    #   tự qua được Cloudflare grok.com: thử thách truy cập vẫn cố vượt
    #   tối đa 3 lượt theo NV90 có sẵn rồi dừng ở can_nguoi, và chế độ
    #   google của grok-web chỉ tái dùng hồ sơ Google đã đăng nhập, không
    #   tự điền mật khẩu/TOTP Google (tai_dung_ho_so không bao giờ dien()).
    #   TOTP ở đây chỉ áp cho chế độ truc_tiep (form grok.com/sign-in).

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
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "_lay_trinh_duyet_module", type(_loi).__name__)
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
            log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "totp_da_luu", type(_loi).__name__)
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
            log.warning("%s: không lưu được TOTP: %s", self._tien_to, type(e).__name__)
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
                log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "_xu_ly_2fa", type(_loi).__name__)
                pass
            return "can_nguoi"
        try:
            await trinh_duyet.dien("ma_xac_minh", ma)
            await trinh_duyet.bam("xac_nhan_2fa")
            return ""
        except Exception:
            return "loi_module"
        finally:
            ma = None  # noqa: F841
            try:
                totp.xoa()
            except Exception as _loi:
                log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "_xu_ly_2fa", type(_loi).__name__)
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
                                nghi_t1_giay: float = 0.0,
                                cach_dang_nhap=None,
                                ho_so_google=None) -> KetQuaKhoiPhuc:
        """Thang T0-T3 ĐẦY ĐỦ, tự chứa trong module này. Gọi trực tiếp cũng chạy.

        - T0: lam_moi_phien(khoa) — chỉ kiểm, không bịa refresh endpoint.
        - T1: nghỉ nghi_t1_giay rồi kiểm lại (mặc định 0 để test không chờ).
        - T2: khóa serial riêng + hoãn captcha riêng + đăng nhập lại bằng
          mật khẩu và TOTP đã lưu qua trình duyệt (xem
          _dang_nhap_bang_trinh_duyet, ma_loi_rieng=True). Cách của TỪNG
          tài khoản (NV300): tài khoản google không cần mật khẩu và không
          điền TOTP — nó tái dùng hồ sơ Google đã đăng nhập; hồ sơ chưa
          đăng nhập → can_nguoi / khong_lay_duoc_phien, không bịa thành
          công. Tài khoản chưa có cách riêng đọc ngược cài đặt chung cũ.
        - T3: kiểm lại bằng lam_moi_phien; nếu khoa là id ngoài module mà
          lam_moi không nhận, thì lấy luôn kết quả xác minh phiên của T2
          (module đã kiểm phiên trong lúc đăng nhập qua _doc_va_kiem_phien
          ở chế độ truc_tiep, hoặc qua tai_dung_ho_so ở chế độ google),
          không báo hỏng giả.
        - Tầng (c) vẫn là tai_khoan_ke() riêng, người gọi tự chọn khoá kế;
          khoi_phuc_tu_dong không tự đổi tài khoản âm thầm.
        - Hoãn captcha theo mẫu fb8aa25: sau một lượt captcha, MỌI khoá
          (kể cả khoá vừa gặp) bị hoãn 1800s (captcha_hoan) vì captcha là thử thách theo
          IP/trình duyệt, không phải theo từng tài khoản.
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
                if bay_gio < getattr(self, "_captcha_den_het", 0.0):  # chặn cả khoá vừa gặp captcha: thử lại ngay chỉ thêm một lần gặp captcha
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi="captcha_hoan")
                # NV300: cách theo TỪNG tài khoản; lần gọi mang cách tường
                # minh thì ghi nhớ cho lần sau không kèm.
                cach = self.cach_dang_nhap_cua(khoa, email,
                                              goi_y=cach_dang_nhap)
                if cach_dang_nhap is not None or ho_so_google is not None:
                    self.dat_cach_dang_nhap(khoa, cach, ho_so_google,
                                            email=email)
                if mat_khau_dung is None:
                    mat_khau_dung = self.mat_khau_da_luu(khoa) or self.mat_khau_da_luu(email)
                if mat_khau_dung is None and cach != "google":
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi="thieu_mat_khau")
                if totp_dung is None:
                    totp_dung = self.totp_da_luu(khoa) or self.totp_da_luu(email)
                td = trinh_duyet if trinh_duyet is not None else await self._lay_trinh_duyet_module()
                self._trinh_duyet_da_dung = td
                if td is None:
                    return KetQuaKhoiPhuc(False, tang="T2", ma_loi="trinh_duyet_none")
                kq2 = await self._dang_nhap_bang_trinh_duyet(
                    email, mat_khau_dung, td, totp=totp_dung,
                    ma_loi_rieng=True, cach_dang_nhap=cach)
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
                    log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "khoi_phuc_tu_dong", type(_loi).__name__)
                    pass
            if totp_dung is not None:
                try:
                    totp_dung.xoa()
                except Exception as _loi:
                    log.warning("%s: %s bỏ qua lỗi kiểu %s", self._tien_to, "khoi_phuc_tu_dong", type(_loi).__name__)
                    pass

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
                # NV272: module đã chạy ở trạng thái chưa có phiên (lõi không
                # còn chặn vì thiếu cookie). Mã rõ can_dang_nhap để đăng nhập
                # tự động có chỗ chạy và tự lấy cookie về.
                "Module grok-web chưa có phiên: đăng nhập tự động để lấy cookie "
                "Grok về rồi gọi lại.",
                "invalid_request_error", "can_dang_nhap",
            )
        model = str(body.get("model") or "")
        biet_them = {b.get("id", "").split("/", 1)[-1].strip().lower() for b in model_bo_sung.doc("grok-web") if isinstance(b, dict) and b.get("id")}
        try:
            che_do = tach_che_do(model, biet_them=biet_them)
        except (ValueError, CheDoKhongCo):
            from app.loi_openai import nhac_lai
            cac_lua_chon = sorted(set(CAC_CHE_DO) | biet_them)
            return 400, {"content-type": "application/json"}, _loi_openai(
                f"Model {nhac_lai(model)} không có; chọn trong: {', '.join(f'{self._tien_to}/{c}' for c in cac_lua_chon)}",
                "invalid_request_error",
                "model_not_found",
            )
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
        """Các chế độ của web app Grok dưới dạng model ``grok/<chế độ>`` + model_bo_sung.

        NV93: id ổn định + nhãn tách riêng; mỗi model kèm gói/tài khoản dùng
        được (grok chưa phân biệt gói theo tài khoản — CHƯA KIỂM thật).
        """
        ds_khoa = [self._khoa_tu_jar(j) for j in self._tai_khoan]
        cac_model: dict[str, dict] = {}
        for m in CAC_CHE_DO:
            cac_model[m] = {
                "id": f"{self._tien_to}/{m}",
                "object": "model",
                "owned_by": "grok-web",
                "nhan": f"Grok {m}",
                "goi": "khong_ro",
                "available_cho": list(ds_khoa),
            }
        for b in model_bo_sung.doc("grok-web"):
            if not isinstance(b, dict):
                continue
            mid = b.get("id")
            if not mid:
                continue
            ten = mid.split("/", 1)[-1].strip().lower()
            nhan = b.get("nhan") or f"Grok {ten}"
            if ten in cac_model:
                if b.get("nhan"):
                    cac_model[ten]["nhan"] = b["nhan"]
            else:
                cac_model[ten] = {
                    "id": f"{self._tien_to}/{ten}",
                    "object": "model",
                    "owned_by": "grok-web",
                    "nhan": nhan,
                    "goi": "khong_ro",
                    "available_cho": list(ds_khoa),
                }
        return list(cac_model.values())

    # ── Sức khoẻ ──────────────────────────────────────────────────────

    async def lam_moi_model(self) -> None:
        """NV273: làm mới danh sách model (hợp đồng lõi, xem app/lam_moi_model.py).

        NGUỒN — TRUNG THỰC: grok.com KHÔNG có endpoint HTTP liệt kê model
        cho cookie web trong repo này: các «model» của module này thực chất
        là chế độ của web app (fast/auto/expert/heavy), khai tĩnh trong
        converter; endpoint /v1/models của xAI cần API key, không phải cookie
        của module này, nên KHÔNG dùng làm nguồn và KHÔNG bịa endpoint.
        Nguồn là danh sách tĩnh đã khai trong manifest («model_tinh»). Kết luận của chủ máy ngày 2026-10-28: không có endpoint liệt kê thật → giữ danh sách tĩnh kèm kiểm phiên; lam_moi_model này KHÔNG TỰ LẤY ĐƯỢC model mới.
        Bước «lấy lại» là kiểm phiên thật của từng tài khoản qua
        /api/auth/session đã có của module (lay_uid_tu_phien): ít nhất một
        phiên còn dùng được thì giữ danh sách tĩnh; mọi phiên đều hỏng thì
        ném RuntimeError mang mã lỗi rõ (xac_thuc / mang / sai_khuon) và
        KHÔNG xoá hay thay danh sách đang có. Thử thách truy cập (Cloudflare)
        coi là cần người, không vượt: báo mang mã «mang» kèm lý do thử thách
        và dừng. Chưa có tài khoản nào thì trả về im lặng: models() vẫn trả
        4 chế độ.
        """
        hien = [m["id"] for m in await self.models()]
        khai = (self._manifest or {}).get("model_tinh")
        if khai is not None:
            if (not isinstance(khai, list) or not khai
                    or not all(isinstance(x, str) and x for x in khai)):
                raise RuntimeError(
                    "grok-web: sai_khuon — «model_tinh» trong manifest "
                    "không phải danh sách chuỗi khác rỗng; giữ danh sách cũ")
            if not set(khai) <= set(hien):
                raise RuntimeError(
                    "grok-web: sai_khuon — danh sách hiện tại thiếu model "
                    "đã khai trong manifest; giữ danh sách cũ")
        jars = list(self._tai_khoan)
        if not jars:
            return
        loi_xac_thuc = None
        loi_sai_khuon = None
        loi_mang = None
        for jar in jars:
            try:
                await lay_uid_tu_phien(jar, self._goc_https)
                return  # ít nhất một phiên còn dùng được → giữ danh sách tĩnh
            except LoiPhienGrokWeb as e:
                # Phân loại theo thông điệp của chính client này (client.py):
                # «không lấy được uid» = lỗi kết nối (mạng), «không chứa
                # userId» = phản hồi sai khuôn, còn lại = cookie bị từ chối.
                if "không chứa userId" in str(e):
                    loi_sai_khuon = e
                elif "không lấy được uid" in str(e):
                    loi_mang = e
                else:
                    loi_xac_thuc = e
            except LoiThuThachTruyCap as e:
                # Thử thách truy cập: không vượt, báo rõ và dừng.
                loi_mang = e
            except Exception as e:  # noqa: BLE001 — mạng lạ: coi là tạm thời
                loi_mang = e
        if loi_sai_khuon is not None:
            raise RuntimeError(
                "grok-web: sai_khuon — grok.com trả phiên không chứa userId; "
                "giữ danh sách cũ") from loi_sai_khuon
        if loi_xac_thuc is not None:
            raise RuntimeError(
                "grok-web: xac_thuc — cookie bị grok.com từ chối; "
                "giữ danh sách cũ") from loi_xac_thuc
        raise RuntimeError(
            "grok-web: mang — không nối được grok.com lúc làm mới (hoặc gặp "
            "thử thách truy cập, không vượt); giữ danh sách cũ") from loi_mang

    async def suc_khoe(self) -> dict:
        """Tình trạng module — không để lộ cookie."""
        if not self._tai_khoan:
            return {
                "ok": False,
                "trang_thai": "chưa cấu hình",
                "chi_tiet": "Thiếu danh sách cookie tài khoản («tai_khoan»)",
            }
        return {
            "ok": True,
            "trang_thai": "sẵn sàng",
            "ghi_chu": f"{len(self._tai_khoan)} tài khoản đã cấu hình",
        }

    # ── Dừng ───────────────────────────────────────────────────────────

    async def dung(self) -> None:
        """Gỡ tham chiếu danh tính mà CHÍNH thể hiện này đã đăng ký (khuôn NV77).
        Thể hiện thay thế (nap sau) đã ghi đè chu_so_huu nên không bị gỡ nhầm."""
        self._nghi_den = {}
