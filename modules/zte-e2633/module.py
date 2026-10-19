"""Module zte-e2633 — nguồn máy mạng (CHỈ ĐỌC) từ bộ phát WiFi ZTE E2633.

VAI TRÒ DỰ PHÒNG: theo quyết định đã chốt, chỉ MỘT bên được đăng nhập bộ phát
(tích hợp Home Assistant `mikrotik_snmp` của chủ máy đang giữ đăng nhập liên tục).
Module này chỉ dùng khi KHÔNG có Home Assistant. Bộ phát có thể chỉ cho MỘT phiên
quản trị: đừng chạy module này đồng thời với tích hợp khác đang đọc ZTE.

Đăng nhập trang quản trị của bộ phát, đọc danh sách máy WiFi, trả thành thực thể
chuẩn C1 cho sổ thiết bị của lõi (app/so_thiet_bi.py). Không đổi bất kỳ cấu hình nào.

Giao thức đo từ chính trang đăng nhập của thiết bị (không đoán):
  GET / → HTML chứa addParameter("_sessionTOKEN", "<số>")
  GET /function_module/login_module/login_page/logintoken_lua.lua → token đăng nhập
  POST / với ĐÚNG thứ tự trường (Username, Password, action, Frm_Logintoken,
      captchaCode, _sessionTOKEN), Password = sha256(mật khẩu + token),
      kèm header `check` = SHA-256 của đúng chuỗi thân → cookie phiên.
  Thiếu `check` hoặc sai thứ tự trường → thiết bị trả trang «页面过期».
  GET /getpage.lua?pid=1005&nextpage=topo_lua.lua&Action=GetALLClients → JSON máy WiFi.

Quy tắc an toàn (NV150):
- `cai()`: kiểm biên rồi LƯU cấu hình ngay, không phụ thuộc đăng nhập thử.
  Đăng nhập thử chỉ sinh cảnh báo có cấu trúc (`canh_bao` + `ma_loi`).
- Mật khẩu KHÔNG vào log/URL/phản hồi lỗi/suc_khoe; mọi thông báo lỗi đều chung chung.
- MỘT PHIÊN: giữ phiên giữa các lần đọc; hết phiên đăng nhập lại đúng 1 lần;
  `dung()` quên phiên phía client; KHÔNG gửi yêu cầu đăng xuất (chưa có điểm cuối
  kiểm chứng trên E2633).
- Bị từ chối (sai mật khẩu/khoá) → tạm ngừng dài 30 phút rồi tự hồi phục; trang lạ/
  token rỗng KHÔNG tạm ngừng dài (phân loại bằng cấu trúc, không dò chữ).
- Đọc hỏng (JSON sai khuôn) là LỖI: giữ dữ liệu cũ, không trả «0 máy».
- Địa chỉ qua đúng bộ kiểm SSRF của lõi (app.tu_xa.kiem_url_lan), không tự viết lại.
- TLS: mặc định kiểm chứng chỉ (an toàn). Bộ phát dùng chứng chỉ tự ký nên cho phép
  đặt kiem_chung_chi_tls=false HOẶC ghim vân tay SHA-256 (van_tay_tls).
- Hết giờ / vượt trần phản hồi → lỗi kết nối, không sập.
- Không dùng proxy môi trường (trust_env=False): thiết bị nằm trong LAN.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx

from app.chuyen_tiep import PhanHoiQuaLon, doc_phan_hoi_co_tran
from app.so_thiet_bi import chuan_hoa_mac
from app.tu_xa import kiem_url_lan

log = logging.getLogger("web2api.zte_e2633")

# ── Hằng số giao thức (đo từ thiết bị, không đoán) ────────────────────────────
DUONG_TOKEN = "/function_module/login_module/login_page/logintoken_lua.lua"
DUONG_DU_LIEU = "/getpage.lua?pid=1005&nextpage=topo_lua.lua&Action=GetALLClients"
TRUONG_DANG_NHAP = ("Username", "Password", "action", "Frm_Logintoken",
                    "captchaCode", "_sessionTOKEN")
MAU_SESSION_TOKEN = re.compile(r'addParameter\("_sessionTOKEN",\s*"(\d+)"\)')
MAU_THE_HTML = re.compile(r"<[^>]+>")
KY_TU_DIEU_KHIEN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
VAN_TAY_HEX = re.compile(r"\A[0-9a-f]{64}\Z")

TRAN_PHAN_HOI = 1_000_000  # 1 MB — danh sách máy của bộ phát gia đình nhỏ hơn nhiều
HET_GIO_GIAY = 10.0
TEN_GOI_COOKIE_PHIEN = "zte_sess"

# ── Chính sách phiên (NV150) ─────────────────────────────────────────────────
TAM_NGUNG_SAU_TU_CHOI_PHUT = 30  # bị từ chối → nghỉ dài rồi tự hồi phục
HAN_DOC_CUOI_PHUT = 5            # suc_khoe dùng lại kết quả đọc gần nhất trong hạn này
KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT = 10  # đăng nhập tối đa 1 lần mỗi N phút
KHOANG_CACH_DANG_NHAP_TOI_DA_PHUT = 1440


class ZteLoi(Exception):
    """Lỗi chung của module; thông báo KHÔNG chứa mật khẩu/token/cookie."""

    def __init__(self, thong_diep: str, *, bi_tu_choi: bool = False,
                 tls_tu_ky: bool = False):
        super().__init__(thong_diep)
        # bi_tu_choi: thiết bị TỪ CHỐI rõ ràng (sai mật khẩu/khoá) — khác với
        # trang lạ/token rỗng/lỗi mạng. Chỉ khi bị từ chối mới tạm ngừng dài.
        self.bi_tu_choi = bi_tu_choi
        # tls_tu_ky: chuỗi lỗi chứa SSLCertVerificationError (phân loại bằng
        # KIỂU ngoại lệ, không dò chữ trong thông báo).
        self.tls_tu_ky = tls_tu_ky


class ZteLoiDangNhap(ZteLoi):
    """Đăng nhập bị từ chối hoặc không đọc được dữ liệu sau đăng nhập."""


class ZteBiKhoa(ZteLoiDangNhap):
    """Thiết bị báo khoá tài khoản — tạm ngừng dài, tự hồi phục."""


class ZteLoiKetNoi(ZteLoi):
    """Không tới được thiết bị (mạng, TLS, hết giờ, phản hồi quá lớn)."""


class ZteLoiVanTay(ZteLoiKetNoi):
    """Vân tay TLS của thiết bị không khớp cấu hình đã ghim (phân loại theo KIỂU)."""


class ZteLoiDocHong(ZteLoi):
    """Thiết bị trả dữ liệu sai khuôn sau khi đăng nhập — là LỖI, không phải
    «không có máy»; bên gọi giữ dữ liệu lần đọc trước."""


def _lam_sach_ten(ten) -> str:
    """Tên tự khai là dữ liệu KHÔNG TIN: bỏ ký tự điều khiển, cắt ≤ 40 ký tự."""
    s = KY_TU_DIEU_KHIEN.sub("", str(ten or ""))
    return " ".join(s.split())[:40]


def _so_nguyen_hoac_none(gia_tri):
    try:
        return int(str(gia_tri).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _ip_hoac_none(gia_tri):
    s = str(gia_tri or "").strip()
    if not s:
        return None
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return None


def _la_trang_qua_han(than: bytes) -> bool:
    """Trang «页面过期» thiết bị trả khi thiếu header check hoặc sai thứ tự trường."""
    return "页面过期".encode("utf-8") in than or "页面过期".encode("gbk") in than


def _la_loi_chung_chi_tu_ky(loi: BaseException) -> bool:
    """Chuỗi nguyên nhân có SSLCertVerificationError không (theo KIỂU, không dò chữ)."""
    thay, dem = loi, 0
    while thay is not None and dem < 10:
        if isinstance(thay, ssl.SSLCertVerificationError):
            return True
        thay = thay.__cause__ or thay.__context__
        dem += 1
    return False


class MayKhachZte:
    """Phiên làm việc với một bộ phát ZTE E2633. Chỉ đọc. Giữ MỘT phiên."""

    def __init__(self, dia_chi: str, tai_khoan: str, mat_khau: str, *,
                 kiem_chung_chi_tls: bool = True, van_tay_tls: str = ""):
        self._dia_chi = dia_chi
        self._tai_khoan = tai_khoan
        self._mat_khau = mat_khau
        self._kiem_chung_chi = kiem_chung_chi_tls
        self._van_tay = van_tay_tls
        self._client: httpx.AsyncClient | None = None
        self._co_phien = False

    @property
    def co_phien(self) -> bool:
        return self._co_phien

    # ── hạ tầng ──────────────────────────────────────────────────────────────
    def _ngu_canh_tls(self) -> ssl.SSLContext:
        ngu = ssl.create_default_context()
        if self._van_tay or not self._kiem_chung_chi:
            # Chứng chỉ tự ký: hoặc bỏ kiểm (do quản trị chọn), hoặc ghim vân tay
            # (đối chiếu riêng ở _doi_chieu_van_tay trước mỗi lần đăng nhập).
            ngu.check_hostname = False
            ngu.verify_mode = ssl.CERT_NONE
        return ngu

    def _doi_chieu_van_tay(self) -> None:
        """Ghim vân tay SHA-256 của chứng chỉ máy chủ (chạy trong thread)."""
        if not VAN_TAY_HEX.match(self._van_tay):
            raise ZteLoi("van_tay_tls phải là 64 ký tự hex (SHA-256)")
        phan = urlsplit(self._dia_chi)
        ngu = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ngu.check_hostname = False
        ngu.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection((phan.hostname, phan.port or 443),
                                          timeout=HET_GIO_GIAY) as sock:
                with ngu.wrap_socket(sock, server_hostname=phan.hostname) as tls:
                    der = tls.getpeercert(binary_form=True)
        except OSError as e:
            raise ZteLoiKetNoi(f"không tới được {self._dia_chi} để đối chiếu vân tay") from e
        that = hashlib.sha256(der).hexdigest()
        if not hmac.compare_digest(that, self._van_tay):
            raise ZteLoiVanTay("vân tay TLS của thiết bị không khớp cấu hình") from None

    def _client_hien_tai(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self._ngu_canh_tls(),
                timeout=HET_GIO_GIAY,
                trust_env=False,  # thiết bị trong LAN: không đi qua proxy môi trường
            )
        return self._client

    async def _goi(self, phuong_thuc: str, duong: str, *,
                   du_lieu: bytes | None = None,
                   tieu_de: dict | None = None) -> bytes:
        client = self._client_hien_tai()
        try:
            yc = client.build_request(phuong_thuc, self._dia_chi + duong,
                                      content=du_lieu, headers=tieu_de)
            phan_hoi = await client.send(yc, stream=True)
            than = await doc_phan_hoi_co_tran(phan_hoi, tran_bytes=TRAN_PHAN_HOI,
                                              tran_giay=HET_GIO_GIAY)
        except (httpx.HTTPError, PhanHoiQuaLon, TimeoutError) as e:
            loi = ZteLoiKetNoi(f"không tới được {self._dia_chi}: {type(e).__name__}",
                               tls_tu_ky=_la_loi_chung_chi_tu_ky(e))
            raise loi from e
        return than

    # ── đăng nhập ────────────────────────────────────────────────────────────
    async def dang_nhap(self) -> None:
        """Một lần đăng nhập. Không tự thử lại. Phân loại TỪ CHỐI theo cấu trúc."""
        if self._van_tay:
            await asyncio.to_thread(self._doi_chieu_van_tay)
        self._co_phien = False
        client = self._client_hien_tai()
        client.cookies.clear()  # bắt đầu từ phiên sạch
        trang = await self._goi("GET", "/", tieu_de={"Referer": self._dia_chi + "/"})
        m = MAU_SESSION_TOKEN.search(trang.decode("utf-8", "replace"))
        if not m:
            # Trang lạ (không có _sessionTOKEN): KHÔNG phải «bộ phát từ chối».
            raise ZteLoiDangNhap("trang đăng nhập không có _sessionTOKEN")
        token = MAU_THE_HTML.sub("", (await self._goi(
            "GET", DUONG_TOKEN, tieu_de={"Referer": self._dia_chi + "/"})
        ).decode("utf-8", "replace")).strip()
        if not token:
            # Token rỗng: KHÔNG phải «bộ phát từ chối».
            raise ZteLoiDangNhap("không lấy được token đăng nhập")
        bam = hashlib.sha256((self._mat_khau + token).encode()).hexdigest()
        # ĐÚNG thứ tự trường mà trình duyệt gửi; header `check` = SHA-256 của đúng chuỗi thân.
        than = urlencode([
            ("Username", self._tai_khoan),
            ("Password", bam),
            ("action", "login"),
            ("Frm_Logintoken", ""),
            ("captchaCode", ""),
            ("_sessionTOKEN", m.group(1)),
        ]).encode()
        tra_loi = await self._goi(
            "POST", "/",
            du_lieu=than,
            tieu_de={"Referer": self._dia_chi + "/",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "check": hashlib.sha256(than).hexdigest()},
        )
        if self._la_bi_khoa(tra_loi):
            raise ZteBiKhoa("thiết bị báo khoá tài khoản", bi_tu_choi=True)
        if TEN_GOI_COOKIE_PHIEN not in client.cookies:
            ly_do = " (trang «đã quá hạn»)" if _la_trang_qua_han(tra_loi) else ""
            # Không có cookie phiên sau POST đúng khuôn = thiết bị TỪ CHỐI.
            raise ZteLoiDangNhap(f"đăng nhập bị từ chối{ly_do}", bi_tu_choi=True)
        self._co_phien = True

    @staticmethod
    def _la_bi_khoa(tra_loi: bytes) -> bool:
        """Tín hiệu khoá của thiết bị: JSON có cờ khoá (theo cấu trúc, không dò chữ)."""
        try:
            du = json.loads(tra_loi.decode("utf-8", "replace"))
        except ValueError:
            return False
        return isinstance(du, dict) and bool(du.get("bi_khoa"))

    # ── đọc dữ liệu ──────────────────────────────────────────────────────────
    @staticmethod
    def _phan_loai_doc(tra_loi: bytes) -> tuple[str, list | None]:
        """Phân loại PHẢN HỒI ĐỌC bằng cấu trúc (không dò chữ trong nội dung):
        - («duoc», [...]) khi JSON là đối tượng có khoá clientsData là danh sách;
        - («hong», None) khi JSON parse được nhưng sai khuôn (thiếu khoá/không phải
          danh sách) — là LỖI đọc hỏng, không phải «không có máy»;
        - («het_phien», None) khi không parse được JSON (trang đăng nhập/HTML)."""
        try:
            du = json.loads(tra_loi.decode("utf-8", "replace"))
        except ValueError:
            return ("het_phien", None)
        if isinstance(du, dict) and isinstance(du.get("clientsData"), list):
            return ("duoc", du["clientsData"])
        return ("hong", None)

    async def doc_may(self) -> list:
        """Danh sách máy thô. Giữ MỘT phiên: hết phiên thì đăng nhập lại ĐÚNG 1 lần."""
        vua_dang_nhap = not self._co_phien
        if vua_dang_nhap:
            await self.dang_nhap()
        loai, du = self._phan_loai_doc(await self._goi("GET", DUONG_DU_LIEU))
        if loai == "het_phien" and not vua_dang_nhap:
            await self.dang_nhap()
            loai, du = self._phan_loai_doc(await self._goi("GET", DUONG_DU_LIEU))
        if loai == "het_phien":
            self._co_phien = False
            raise ZteLoiDangNhap("không đọc được danh sách máy sau khi đăng nhập")
        if loai == "hong":
            raise ZteLoiDocHong(
                "thiết bị trả dữ liệu sai khuôn (thiếu clientsData hoặc không phải "
                "danh sách) sau khi đăng nhập")
        return du if du is not None else []

    async def dong(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._co_phien = False


def _thuc_the_tu_may(tien_to: str, may: object) -> dict | None:
    """Một mục máy WiFi → thực thể C1. Sai khuôn → None (bỏ qua mục đó)."""
    if not isinstance(may, dict):
        return None
    mac_chuan, _ = chuan_hoa_mac(may.get("MACAddress"))
    if not mac_chuan:
        return None
    ten = _lam_sach_ten(may.get("HostName")) or mac_chuan
    # AccessType của thiết bị: 1 = 2.4G, 2 = 5G; giá trị lạ → null (mã mở, không sập).
    bang_tan = {"1": "2.4G", "2": "5G"}.get(str(may.get("AccessType") or "").strip())
    return {
        "id": f"{tien_to}.{mac_chuan.replace(':', '')}",
        "ten": ten,
        "loai": "may_mang",
        "thuoc_tinh": {
            "mac": mac_chuan,
            "ip": _ip_hoac_none(may.get("IPAddress")),
            "ten_may_tu_khai": ten,
            "bang_tan": bang_tan,
            "rssi": _so_nguyen_hoac_none(may.get("RSSI")),
            "nguon": "wifi",
        },
        "rui_ro_mac_dinh": "doc",
        "tinh_nang": [],
    }


def _la_false(gia_tri) -> bool:
    """Chuỗi cấu hình → False chỉ khi ghi rõ «false»; còn lại True (mặc định an toàn)."""
    return str(gia_tri).strip().lower() == "false"


# ── Mã lỗi có cấu trúc cho cai() (tiếng Việt, kèm hướng dẫn) ──────────────────
def _canh_bao_tu_loi(loi: ZteLoi, kiem_chung_chi: bool) -> tuple[str | None, str | None]:
    """(ma_loi, canh_bao) từ lỗi đăng nhập thử. Thông báo KHÔNG chứa mật khẩu."""
    if isinstance(loi, ZteBiKhoa):
        return ("TAI_KHOAN_BI_KHOA",
                "Tài khoản bị thiết bị khoá tạm — không thử dồn dập. Đã lưu cấu hình; "
                "đợi thiết bị mở khoá rồi kiểm tra lại mật khẩu "
                f"(module tự thử lại sau {TAM_NGUNG_SAU_TU_CHOI_PHUT} phút).")
    if isinstance(loi, ZteLoiDangNhap):
        if loi.bi_tu_choi:
            return ("MAT_KHAU_BI_TU_CHOI",
                    "Mật khẩu bị từ chối (hoặc bộ phát chỉ cho một phiên: có thể tích "
                    "hợp khác đang đăng nhập — đừng chạy đồng thời). Đã lưu cấu hình; "
                    "kiểm tra lại mật khẩu.")
        return ("TRANG_LA",
                "Thiết bị trả trang lạ hoặc token rỗng (chưa rõ là từ chối). Đã lưu "
                "cấu hình; kiểm tra địa chỉ có đúng trang quản trị của bộ phát không.")
    if isinstance(loi, ZteLoiDocHong):
        return ("DOC_HONG",
                "Thiết bị trả dữ liệu sai khuôn sau khi đăng nhập (thiếu danh sách "
                "clientsData). Đã lưu cấu hình; giữ nguyên dữ liệu lần đọc trước.")
    if isinstance(loi, ZteLoiKetNoi):
        if loi.tls_tu_ky and kiem_chung_chi:
            return ("TLS_TU_KY",
                    "Chứng chỉ TLS của bộ phát là tự ký nên không kiểm chứng được. "
                    "Đã lưu cấu hình. Để nối được: đặt kiem_chung_chi_tls=false "
                    "hoặc ghim van_tay_tls (vân tay SHA-256 của chứng chỉ bộ phát).")
        if isinstance(loi, ZteLoiVanTay):
            return ("VAN_TAY_KHONG_KHOP",
                    "Vân tay TLS của thiết bị không khớp van_tay_tls đã ghim. Đã lưu "
                    "cấu hình; lấy lại vân tay SHA-256 đúng của chứng chỉ bộ phát.")
        return ("KHONG_TOI_DUOC",
                f"Không tới được bộ phát ({loi}). Đã lưu cấu hình; "
                "kiểm tra địa chỉ/mạng rồi thử lại.")
    return (None, f"Lỗi chưa phân loại ({type(loi).__name__}): {loi}. Đã lưu cấu hình.")


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng thiet_bi, chỉ đọc).

    VAI TRÒ DỰ PHÒNG (xem docstring đầu tệp): chỉ bật khi không có Home Assistant
    đọc ZTE; không chạy đồng thời với tích hợp khác đang đăng nhập bộ phát.
    """

    def __init__(self, manifest: dict, dong_ho=None):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "zte-e2633")
        self._khach: MayKhachZte | None = None
        self._dong_ho = dong_ho or (lambda: datetime.now(timezone.utc))
        self._khoang_cach_phut = KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT
        # Trạng thái một phiên + tạm ngừng + đọc gần nhất:
        self._tam_ngung_den: datetime | None = None
        self._ly_do_tam_ngung = ""
        self._lan_thu_dang_nhap: datetime | None = None
        self._doc_cuoi: tuple[datetime, list[dict]] | None = None
        self._loi_cuoi = ""

    # ── cấu hình ─────────────────────────────────────────────────────────────
    @staticmethod
    def _dung_khach(cau_hinh: dict) -> tuple[MayKhachZte | None, int]:
        cau_hinh = cau_hinh or {}
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip().rstrip("/")
        tai_khoan = str(cau_hinh.get("tai_khoan") or "").strip()
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        phan = urlsplit(dia_chi)
        if not dia_chi or phan.scheme not in ("http", "https") or not phan.hostname:
            return None, KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT
        if not tai_khoan or not mat_khau:
            return None, KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT
        van_tay = str(cau_hinh.get("van_tay_tls") or "").strip().lower()
        if van_tay and not VAN_TAY_HEX.match(van_tay):
            van_tay = ""
        try:
            khoang_cach = int(str(cau_hinh.get("khoang_cach_dang_nhap_phut")
                                  or KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT))
        except (ValueError, TypeError):
            khoang_cach = KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT
        khoang_cach = min(max(khoang_cach, 1), KHOANG_CACH_DANG_NHAP_TOI_DA_PHUT)
        return MayKhachZte(
            dia_chi, tai_khoan, mat_khau,
            kiem_chung_chi_tls=not _la_false(cau_hinh.get("kiem_chung_chi_tls", "true")),
            van_tay_tls=van_tay,
        ), khoang_cach

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động hoặc sau khi cài) — không gọi mạng."""
        self._khach, self._khoang_cach_phut = self._dung_khach(cau_hinh)

    @staticmethod
    def _kiem_khoang_cach(cau_hinh: dict) -> tuple[int | None, str | None]:
        """khoang_cach_dang_nhap_phut: số phút 1–1440; sai → (None, thông báo)."""
        tho = cau_hinh.get("khoang_cach_dang_nhap_phut")
        if tho is None or str(tho).strip() == "":
            return KHOANG_CACH_DANG_NHAP_MAC_DINH_PHUT, None
        try:
            so = int(str(tho).strip())
        except (ValueError, TypeError):
            return None, "khoang_cach_dang_nhap_phut phải là số phút 1–1440."
        if not 1 <= so <= KHOANG_CACH_DANG_NHAP_TOI_DA_PHUT:
            return None, "khoang_cach_dang_nhap_phut phải là số phút 1–1440."
        return so, None

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm BIÊN rồi LƯU cấu hình ngay, không phụ thuộc đăng nhập thử.

        Đăng nhập thử vẫn chạy nhưng kết quả chỉ là CẢNH BÁO có cấu trúc
        (`canh_bao` + `ma_loi` trong từng mục `ket_qua_tung_khoa`), không phải
        lý do bỏ lưu. Mật khẩu không vào log/phản hồi.
        """
        cau_hinh = cau_hinh or {}

        def that_bai(loi: str) -> dict:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": loi}

        dia_chi = str(cau_hinh.get("dia_chi") or "").strip().rstrip("/")
        if not dia_chi:
            return that_bai("Chưa nhập địa chỉ bộ phát (dia_chi).")
        try:
            dia_chi = await kiem_url_lan(dia_chi)
        except ValueError as e:
            return that_bai(str(e))
        tai_khoan = str(cau_hinh.get("tai_khoan") or "").strip()
        if not tai_khoan:
            return that_bai("Chưa nhập tài khoản (tai_khoan).")
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        if not mat_khau:
            return that_bai("Chưa nhập mật khẩu (mat_khau).")
        van_tay = str(cau_hinh.get("van_tay_tls") or "").strip().lower()
        if van_tay and not VAN_TAY_HEX.match(van_tay):
            return that_bai("van_tay_tls phải là 64 ký tự hex (SHA-256 của chứng chỉ).")
        kiem_chung_chi = not _la_false(cau_hinh.get("kiem_chung_chi_tls", "true"))
        khoang_cach, loi_khoang_cach = self._kiem_khoang_cach(cau_hinh)
        if khoang_cach is None:
            return that_bai(loi_khoang_cach or "khoang_cach_dang_nhap_phut không hợp lệ.")

        cau_hinh_luu = {
            "dia_chi": dia_chi,
            "tai_khoan": tai_khoan,
            "mat_khau": mat_khau,
            "kiem_chung_chi_tls": "false" if not kiem_chung_chi else "true",
            "khoang_cach_dang_nhap_phut": str(khoang_cach),
            **({"van_tay_tls": van_tay} if van_tay else {}),
        }

        # Đăng nhập THỬ: kết quả chỉ là cảnh báo có cấu trúc, không bỏ lưu.
        khach = MayKhachZte(dia_chi, tai_khoan, mat_khau,
                            kiem_chung_chi_tls=kiem_chung_chi, van_tay_tls=van_tay)
        ma_loi: str | None = None
        canh_bao: str | None = None
        so_may: int | None = None
        try:
            try:
                ds = await khach.doc_may()
            except ZteLoi as e:
                ma_loi, canh_bao = _canh_bao_tu_loi(e, kiem_chung_chi)
                log.warning("zte-e2633: đăng nhập thử cảnh báo [%s]", ma_loi)
            else:
                so_may = sum(1 for m in ds if _thuc_the_tu_may(self.tien_to, m))
        finally:
            await khach.dong()
        return {
            "ok": True,
            "ket_qua_tung_khoa": [{"khoa": tai_khoan, "ok": True, "loi": None,
                                   "so_may": so_may, "canh_bao": canh_bao,
                                   "ma_loi": ma_loi}],
            "loi": None,
            "cau_hinh_luu": cau_hinh_luu,
        }

    # ── đọc một vòng (một phiên) ─────────────────────────────────────────────
    def _con_tam_ngung(self) -> bool:
        return (self._tam_ngung_den is not None
                and self._dong_ho() < self._tam_ngung_den)

    async def _doc_mot_vong(self) -> list[dict]:
        """Một vòng đọc: dùng phiên hiện có; hết phiên đăng nhập lại đúng 1 lần.

        Bị từ chối → đặt tạm ngừng dài rồi ném; đọc hỏng → ném ZteLoiDocHong.
        Ghi nhận lần thử đăng nhập để suc_khoe điều tiết.
        """
        if self._khach is None:
            raise ZteLoi("chưa cấu hình")
        if self._con_tam_ngung():
            raise ZteLoiDangNhap(
                f"đang tạm ngừng đến {self._tam_ngung_den.isoformat()} sau khi "
                "thiết bị từ chối — kiểm tra mật khẩu, đừng chạy đồng thời với "
                "tích hợp khác", bi_tu_choi=True)
        bay_gio = self._dong_ho()
        if not self._khach.co_phien:
            self._lan_thu_dang_nhap = bay_gio
        try:
            ds_may = await self._khach.doc_may()
        except ZteLoi as e:
            if e.bi_tu_choi:
                self._tam_ngung_den = bay_gio + timedelta(
                    minutes=TAM_NGUNG_SAU_TU_CHOI_PHUT)
                self._ly_do_tam_ngung = str(e)
                log.warning("zte-e2633: bị từ chối — tạm ngừng %d phút",
                            TAM_NGUNG_SAU_TU_CHOI_PHUT)
            raise
        ra = []
        for may in ds_may:
            try:
                thuc_the = _thuc_the_tu_may(self.tien_to, may)
            except Exception:
                log.warning("zte-e2633: bỏ qua mục máy sai khuôn", exc_info=False)
                continue
            if thuc_the is not None:
                ra.append(thuc_the)
        self._doc_cuoi = (bay_gio, ra)
        self._loi_cuoi = ""
        return ra

    async def _lay_thuc_the(self, loc: dict | None) -> list[dict]:
        """Đọc và đổi sang thực thể.

        Đọc hỏng → GIỮ DỮ LIỆU CŨ (không trả «0 máy»); lỗi khác → rỗng + log.
        """
        if self._khach is None:
            return []
        try:
            ra = await self._doc_mot_vong()
        except ZteLoiDocHong as e:
            log.warning("zte-e2633: đọc hỏng, giữ dữ liệu cũ: %s", e)
            self._loi_cuoi = str(e)
            if self._doc_cuoi is not None:
                ra = self._doc_cuoi[1]
            else:
                return []
        except ZteLoi as e:
            log.warning("zte-e2633: không đọc được danh sách máy: %s", e)
            self._loi_cuoi = str(e)
            return []
        if isinstance(loc, dict) and loc.get("loai"):
            ra = [e for e in ra if e.get("loai") == loc["loai"]]
        return ra

    # ── khả năng thiet_bi ────────────────────────────────────────────────────
    async def thuc_the(self, loc: dict | None) -> list[dict]:
        return await self._lay_thuc_the(loc)

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        luc = datetime.now(timezone.utc).isoformat()
        try:
            co_mat = {e["id"] for e in await self._lay_thuc_the(None)}
        except ZteLoi:
            return {str(i): {"gia_tri": "khong", "hop_le": False, "luc": luc} for i in ids}
        return {str(i): {"gia_tri": "co" if str(i) in co_mat else "khong",
                         "hop_le": True, "luc": luc} for i in ids}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict) -> dict:
        # Module chỉ đọc: không có hành động nào.
        return {"ok": False,
                "loi": "Module zte-e2633 chỉ đọc danh sách máy WiFi, không hỗ trợ hành động."}

    async def suc_khoe(self) -> dict:
        """Không đăng nhập lại mỗi lần gọi: dùng kết quả đọc gần nhất (có hạn);
        nếu không thì đăng nhập tối đa 1 lần mỗi N phút. Lỗi → trạng thái
        «chưa kết nối được: <lý do>», không ném."""
        if self._khach is None:
            return {"ok": False, "loi": "chưa kết nối được: chưa cấu hình"}
        bay_gio = self._dong_ho()
        if self._doc_cuoi is not None:
            tuoi = bay_gio - self._doc_cuoi[0]
            if tuoi < timedelta(minutes=HAN_DOC_CUOI_PHUT):
                return {"ok": True, "so_may": len(self._doc_cuoi[1])}
        if self._con_tam_ngung():
            return {"ok": False,
                    "loi": "chưa kết nối được: " + self._ly_do_tam_ngung,
                    "tam_ngung_den": self._tam_ngung_den.isoformat()}
        if (self._lan_thu_dang_nhap is not None
                and bay_gio - self._lan_thu_dang_nhap
                < timedelta(minutes=self._khoang_cach_phut)):
            return {"ok": False,
                    "loi": "chưa kết nối được: " + (self._loi_cuoi or
                          f"đang chờ thử lại (mỗi {self._khoang_cach_phut} phút)")}
        self._lan_thu_dang_nhap = bay_gio
        try:
            ra = await self._doc_mot_vong()
        except ZteLoiDocHong as e:
            self._loi_cuoi = str(e)
            if self._doc_cuoi is not None:
                # Đọc hỏng là lỗi — giữ số máy lần đọc trước, không trả 0.
                return {"ok": False, "loi": f"chưa kết nối được: đọc hỏng ({e})",
                        "so_may": len(self._doc_cuoi[1]), "du_lieu_cu": True}
            return {"ok": False, "loi": f"chưa kết nối được: đọc hỏng ({e})"}
        except ZteLoi as e:
            self._loi_cuoi = str(e)
            return {"ok": False, "loi": f"chưa kết nối được: {e}"}
        self._doc_cuoi = (bay_gio, ra)
        self._loi_cuoi = ""
        return {"ok": True, "so_may": len(ra)}

    async def dung(self) -> None:
        """Quên phiên phía client; KHÔNG gửi yêu cầu đăng xuất lên bộ phát."""
        if self._khach is not None:
            await self._khach.dong()
