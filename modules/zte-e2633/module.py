"""Module zte-e2633 — nguồn máy mạng (CHỈ ĐỌC) từ bộ phát WiFi ZTE E2633.

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

Quy tắc an toàn (theo spec NV111):
- Mật khẩu KHÔNG vào log/URL/phản hồi lỗi; mọi thông báo lỗi đều chung chung.
- Mỗi chu kỳ đọc đăng nhập tối đa 1 lần; phiên hết hạn thì đăng nhập lại đúng 1 lần.
- Gặp tín hiệu khoá của thiết bị hoặc lỗi đăng nhập lặp (≥3 lần liên tiếp) thì DỪNG
  hẳn — thiết bị khoá sau vài lần sai, không được thử dồn dập.
- Địa chỉ qua đúng bộ kiểm SSRF của lõi (app.tu_xa.kiem_url_lan), không tự viết lại.
- TLS: mặc định kiểm chứng chỉ (an toàn). Bộ phát dùng chứng chỉ tự ký nên cho phép
  đặt kiem_chung_chi_tls=false HOẶC ghim vân tay SHA-256 (van_tay_tls).
- Hết giờ / vượt trần phản hồi / JSON sai khuôn → bỏ qua mục sai, không sập.
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
from datetime import datetime, timezone
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
LOI_DANG_NHAP_LIEN_TIEP_TOI_DA = 3  # quá ngưỡng → dừng (thiết bị khoá sau vài lần sai)
TEN_GOI_COOKIE_PHIEN = "zte_sess"


class ZteLoi(Exception):
    """Lỗi chung của module; thông báo KHÔNG chứa mật khẩu/token/cookie."""


class ZteLoiDangNhap(ZteLoi):
    """Đăng nhập bị từ chối hoặc không đọc được dữ liệu sau đăng nhập."""


class ZteBiKhoa(ZteLoiDangNhap):
    """Thiết bị báo khoá hoặc lỗi lặp quá ngưỡng — đã dừng, không thử lại."""


class ZteLoiKetNoi(ZteLoi):
    """Không tới được thiết bị (mạng, TLS, hết giờ, phản hồi quá lớn)."""


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


class MayKhachZte:
    """Phiên làm việc với một bộ phát ZTE E2633. Chỉ đọc."""

    def __init__(self, dia_chi: str, tai_khoan: str, mat_khau: str, *,
                 kiem_chung_chi_tls: bool = True, van_tay_tls: str = ""):
        self._dia_chi = dia_chi
        self._tai_khoan = tai_khoan
        self._mat_khau = mat_khau
        self._kiem_chung_chi = kiem_chung_chi_tls
        self._van_tay = van_tay_tls
        self._client: httpx.AsyncClient | None = None
        self._co_phien = False
        self._bi_khoa = False
        self._loi_lien_tiep = 0

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
            raise ZteLoiKetNoi("vân tay TLS của thiết bị không khớp cấu hình")

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
            raise ZteLoiKetNoi(f"không tới được {self._dia_chi}: {type(e).__name__}") from e
        return than

    def _ghi_nhan_loi_dang_nhap(self) -> None:
        self._loi_lien_tiep += 1
        if self._loi_lien_tiep >= LOI_DANG_NHAP_LIEN_TIEP_TOI_DA:
            self._bi_khoa = True
            log.warning("zte-e2633: dừng sau %d lần đăng nhập lỗi liên tiếp",
                        self._loi_lien_tiep)

    # ── đăng nhập ────────────────────────────────────────────────────────────
    async def dang_nhap(self) -> None:
        """Một lần đăng nhập. Không tự thử lại."""
        if self._bi_khoa:
            raise ZteBiKhoa("đã dừng sau khoá/lỗi lặp — kiểm tra lại mật khẩu rồi cấu hình lại")
        if self._van_tay:
            await asyncio.to_thread(self._doi_chieu_van_tay)
        self._co_phien = False
        client = self._client_hien_tai()
        client.cookies.clear()  # bắt đầu từ phiên sạch
        trang = await self._goi("GET", "/", tieu_de={"Referer": self._dia_chi + "/"})
        m = MAU_SESSION_TOKEN.search(trang.decode("utf-8", "replace"))
        if not m:
            self._ghi_nhan_loi_dang_nhap()
            raise ZteLoiDangNhap("trang đăng nhập không có _sessionTOKEN")
        token = MAU_THE_HTML.sub("", (await self._goi(
            "GET", DUONG_TOKEN, tieu_de={"Referer": self._dia_chi + "/"})
        ).decode("utf-8", "replace")).strip()
        if not token:
            self._ghi_nhan_loi_dang_nhap()
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
            self._bi_khoa = True
            raise ZteBiKhoa("thiết bị báo khoá tài khoản — dừng, không thử lại")
        if TEN_GOI_COOKIE_PHIEN not in client.cookies:
            self._ghi_nhan_loi_dang_nhap()
            ly_do = " (trang «đã quá hạn»)" if _la_trang_qua_han(tra_loi) else ""
            raise ZteLoiDangNhap(f"đăng nhập bị từ chối{ly_do}")
        self._loi_lien_tiep = 0
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
    def _trich_danh_sach(tra_loi: bytes) -> list | None:
        """JSON đối tượng có khoá clientsData là danh sách; khác đi → None (hết phiên/trang lỗi)."""
        try:
            du = json.loads(tra_loi.decode("utf-8", "replace"))
        except ValueError:
            return None
        if isinstance(du, dict) and isinstance(du.get("clientsData"), list):
            return du["clientsData"]
        return None

    async def doc_may(self) -> list:
        """Danh sách máy thô. Đăng nhập tối đa 1 lần; hết phiên thì đăng nhập lại đúng 1 lần."""
        vua_dang_nhap = not self._co_phien
        if vua_dang_nhap:
            await self.dang_nhap()
        du = self._trich_danh_sach(await self._goi("GET", DUONG_DU_LIEU))
        if du is None and not vua_dang_nhap:
            await self.dang_nhap()
            du = self._trich_danh_sach(await self._goi("GET", DUONG_DU_LIEU))
        if du is None:
            self._co_phien = False
            raise ZteLoiDangNhap("không đọc được danh sách máy sau khi đăng nhập")
        return du

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


class Module:
    """Lớp Module theo hợp đồng app/kham_pha.py (khả năng thiet_bi, chỉ đọc)."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "zte-e2633")
        self._khach: MayKhachZte | None = None

    # ── cấu hình ─────────────────────────────────────────────────────────────
    @staticmethod
    def _dung_khach(cau_hinh: dict) -> MayKhachZte | None:
        cau_hinh = cau_hinh or {}
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip().rstrip("/")
        tai_khoan = str(cau_hinh.get("tai_khoan") or "").strip()
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        phan = urlsplit(dia_chi)
        if not dia_chi or phan.scheme not in ("http", "https") or not phan.hostname:
            return None
        if not tai_khoan or not mat_khau:
            return None
        van_tay = str(cau_hinh.get("van_tay_tls") or "").strip().lower()
        if van_tay and not VAN_TAY_HEX.match(van_tay):
            van_tay = ""
        return MayKhachZte(
            dia_chi, tai_khoan, mat_khau,
            kiem_chung_chi_tls=not _la_false(cau_hinh.get("kiem_chung_chi_tls", "true")),
            van_tay_tls=van_tay,
        )

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động hoặc sau khi cài) — không gọi mạng."""
        self._khach = self._dung_khach(cau_hinh)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm địa chỉ qua bộ chống SSRF của lõi, kiểm trường, rồi đăng nhập THẬT một lần."""
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

        khach = MayKhachZte(dia_chi, tai_khoan, mat_khau,
                            kiem_chung_chi_tls=kiem_chung_chi, van_tay_tls=van_tay)
        try:
            try:
                ds = await khach.doc_may()
            except ZteBiKhoa:
                return that_bai("Thiết bị đã khoá tài khoản (hoặc lỗi lặp) — "
                                "đợi thiết bị mở khoá rồi kiểm lại mật khẩu.")
            except ZteLoi as e:
                return that_bai(f"Không đăng nhập/đọc được thiết bị: {e}")
            so_may = sum(1 for m in ds if _thuc_the_tu_may(self.tien_to, m))
        finally:
            await khach.dong()
        return {
            "ok": True,
            "ket_qua_tung_khoa": [{"khoa": tai_khoan, "ok": True, "loi": None,
                                   "so_may": so_may}],
            "loi": None,
            "cau_hinh_luu": {
                "dia_chi": dia_chi,
                "tai_khoan": tai_khoan,
                "mat_khau": mat_khau,
                "kiem_chung_chi_tls": "false" if not kiem_chung_chi else "true",
                **({"van_tay_tls": van_tay} if van_tay else {}),
            },
        }

    async def _lay_thuc_the(self, loc: dict | None) -> list[dict]:
        """Đọc và đổi sang thực thể; NÉM ZteLoi khi không đọc được (để suc_khoe phân biệt)."""
        if self._khach is None:
            return []
        ds_may = await self._khach.doc_may()
        ra = []
        for may in ds_may:
            try:
                thuc_the = _thuc_the_tu_may(self.tien_to, may)
            except Exception:
                log.warning("zte-e2633: bỏ qua mục máy sai khuôn", exc_info=False)
                continue
            if thuc_the is not None:
                ra.append(thuc_the)
        if isinstance(loc, dict) and loc.get("loai"):
            ra = [e for e in ra if e.get("loai") == loc["loai"]]
        return ra

    # ── khả năng thiet_bi ────────────────────────────────────────────────────
    async def thuc_the(self, loc: dict | None) -> list[dict]:
        try:
            return await self._lay_thuc_the(loc)
        except ZteLoi as e:
            log.warning("zte-e2633: không đọc được danh sách máy: %s", e)
            return []

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
        if self._khach is None:
            return {"ok": False, "loi": "chưa cấu hình"}
        try:
            ds = await self._lay_thuc_the(None)
        except ZteLoi as e:
            return {"ok": False, "loi": str(e)}
        return {"ok": True, "so_may": len(ds)}

    async def dung(self) -> None:
        if self._khach is not None:
            await self._khach.dong()
