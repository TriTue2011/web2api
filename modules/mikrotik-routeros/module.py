"""Module mikrotik-routeros — nguồn máy mạng CHỈ ĐỌC từ router MikroTik qua RouterOS API.

Đọc bảng DHCP lease (``/ip/dhcp-server/lease/print``) và bảng host
(``/interface/bridge/host/print``), gộp theo địa chỉ MAC thành thực thể chuẩn
thiết bị C1 (``loai="may_mang"``, ``thuoc_tinh.mac`` — lõi dùng để nhận diện máy mạng).

CHỈ ĐỌC: lệnh gửi đi bị lọc theo cấu trúc ở lớp client (``routeros.goi_doc``);
``thuc_hien`` luôn từ chối. Khuyến nghị tạo tài khoản RouterOS chỉ có quyền ``read``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import unicodedata

from app.tu_xa import _dia_chi_bi_chan
from . import routeros

log = logging.getLogger("web2api.mikrotik_routeros")

LENH_LEASE = "/ip/dhcp-server/lease/print"
LENH_HOST = "/interface/bridge/host/print"
DO_DAI_TEN_TOI_DA = 40
TRAN_DO_DAI_CAU_HINH = 512

_MAC = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
_MAC_BO = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}


def _la_dung(gia_tri) -> bool:
    """Chuỗi cấu hình «true»/«false» → bool (giao diện gửi chuỗi)."""
    if isinstance(gia_tri, bool):
        return gia_tri
    return str(gia_tri or "").strip().lower() in ("true", "1", "yes", "co", "on")


def _lam_sach_ten(gia_tri) -> str:
    """Dữ liệu từ router là KHÔNG TIN: bỏ ký tự điều khiển, cắt 40 ký tự."""
    s = "".join(c for c in str(gia_tri or "") if unicodedata.category(c)[0] != "C")
    return s.strip()[:DO_DAI_TEN_TOI_DA]


def _mac_chuan(giatri) -> str | None:
    """MAC hợp lệ dạng chữ thường có hai chấm; bỏ MAC sai khuôn/broadcast/zero."""
    mac = str(giatri or "").strip()
    if not _MAC.match(mac):
        return None
    mac = mac.lower()
    if mac in _MAC_BO:
        return None
    return mac


def _mo_phien_da_dang_nhap(host, cong, tai_khoan, mat_khau, *,
                           tls=True, kiem_chung_chi=True, van_tay=""):
    """Mở phiên và đăng nhập (đồng bộ; gọi qua ``asyncio.to_thread``)."""
    p = routeros.ket_noi(host, cong, tai_khoan, mat_khau, tls=tls,
                         kiem_chung_chi=kiem_chung_chi, van_tay=van_tay)
    try:
        p.dang_nhap(tai_khoan, mat_khau)
    except Exception:
        p.dong()
        raise
    return p


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._cfg: dict | None = None

    # ── kiểm địa chỉ (đúng khuôn app/tu_xa.py: chặn loopback/link-local, ghim IP) ──

    def kiem_dia_chi(self, xau) -> tuple[str, int]:
        """Chuẩn hoá «host[:cổng]» → (host/IP đã ghim, cổng). ValueError nêu lý do.

        Dùng nguyên tắc của ``app.tu_xa`` (``_dia_chi_bi_chan``): chặn loopback
        (trừ khi test bật cờ), link-local/metadata, địa chỉ không định tuyến;
        tên miền được phân giải và ghim IP đã kiểm (chống DNS rebinding).
        """
        s = str(xau or "").strip()
        if not s or len(s) > TRAN_DO_DAI_CAU_HINH:
            raise ValueError("dia_chi rỗng hoặc quá dài")
        host, cong_s = s, ""
        if s.startswith("["):  # IPv6 dạng [::1]:8729
            ket = s.find("]")
            if ket < 0:
                raise ValueError(f"dia_chi {s!r} sai khuôn IPv6")
            host = s[1:ket]
            phan_con = s[ket + 1:]
            if phan_con.startswith(":"):
                cong_s = phan_con[1:]
            elif phan_con:
                raise ValueError(f"dia_chi {s!r} sai khuôn")
        elif s.count(":") == 1:
            host, _, cong_s = s.partition(":")
        elif s.count(":") > 1:
            raise ValueError(f"dia_chi {s!r} sai khuôn (IPv6 cần ngoặc vuông)")
        host = host.strip()
        if not host:
            raise ValueError("dia_chi thiếu tên máy/IP")
        try:
            cong = int(cong_s) if cong_s else routeros.CONG_MAC_DINH
        except ValueError:
            raise ValueError(f"cổng {cong_s!r} không phải số") from None
        if not 1 <= cong <= 65535:
            raise ValueError(f"cổng {cong} ngoài khoảng 1–65535")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            ly_do = _dia_chi_bi_chan(ip)
            if ly_do:
                raise ValueError(f"dia_chi trỏ vào {ip} — bị chặn: {ly_do}")
            return str(ip), cong
        try:
            thong_tin = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {host!r}") from e
        for thong in thong_tin:
            ip = ipaddress.ip_address(thong[4][0])
            if _dia_chi_bi_chan(ip) is None:
                return str(ip), cong  # ghim IP đã kiểm, chống DNS rebinding
        raise ValueError(f"tên máy {host!r} chỉ phân giải ra địa chỉ bị chặn")

    # ── vòng đời ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng."""
        host, cong = self.kiem_dia_chi(cau_hinh.get("dia_chi"))
        self._cfg = {
            "host": host,
            "cong": cong,
            "tai_khoan": str(cau_hinh.get("tai_khoan") or "").strip(),
            "mat_khau": str(cau_hinh.get("mat_khau") or ""),
            "kiem_chung_chi_tls": _la_dung(cau_hinh.get("kiem_chung_chi_tls", True)),
            "van_tay_tls": str(cau_hinh.get("van_tay_tls") or "").strip(),
            "khong_ma_hoa": _la_dung(cau_hinh.get("cho_phep_khong_ma_hoa", False)),
        }

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình bằng cách nối + đăng nhập thật vào router."""
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip()
        tai_khoan = str(cau_hinh.get("tai_khoan") or "").strip()
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        if not dia_chi:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập địa chỉ router (dia_chi)."}
        if not tai_khoan:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập tài khoản (tai_khoan)."}
        if not mat_khau:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập mật khẩu (mat_khau)."}
        try:
            host, cong = self.kiem_dia_chi(dia_chi)
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        khong_ma_hoa = _la_dung(cau_hinh.get("cho_phep_khong_ma_hoa", False))
        try:
            p = await asyncio.to_thread(
                _mo_phien_da_dang_nhap, host, cong, tai_khoan, mat_khau,
                tls=not khong_ma_hoa,
                kiem_chung_chi=_la_dung(cau_hinh.get("kiem_chung_chi_tls", True)),
                van_tay=str(cau_hinh.get("van_tay_tls") or "").strip(),
            )
        except routeros.RouterOSError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        await asyncio.to_thread(p.dong)
        return {
            "ok": True,
            "ket_qua_tung_khoa": [],
            "loi": None,
            # lõi lưu vào kho bí mật, không trả ra API (hợp đồng giống antigravity).
            "cau_hinh_luu": {"dia_chi": dia_chi, "tai_khoan": tai_khoan, "mat_khau": mat_khau,
                             "kiem_chung_chi_tls": str(cau_hinh.get("kiem_chung_chi_tls", "true")),
                             "van_tay_tls": str(cau_hinh.get("van_tay_tls") or ""),
                             "cho_phep_khong_ma_hoa": str(cau_hinh.get("cho_phep_khong_ma_hoa", "false"))},
        }

    async def suc_khoe(self) -> dict:
        if not self._cfg:
            return {"ok": False, "khoa": []}
        try:
            p = await asyncio.to_thread(
                _mo_phien_da_dang_nhap, self._cfg["host"], self._cfg["cong"],
                self._cfg["tai_khoan"], self._cfg["mat_khau"],
                tls=not self._cfg["khong_ma_hoa"],
                kiem_chung_chi=self._cfg["kiem_chung_chi_tls"],
                van_tay=self._cfg["van_tay_tls"],
            )
        except routeros.RouterOSError as e:
            return {"ok": False, "khoa": [], "loi": str(e)}
        await asyncio.to_thread(p.dong)
        return {"ok": True, "khoa": []}

    async def dung(self) -> None:
        self._cfg = None

    # ── chuẩn thiết bị C1 ──

    def _thuc_the_dong_bo(self) -> list[dict]:
        cfg = self._cfg
        if not cfg:
            return []
        try:
            p = _mo_phien_da_dang_nhap(cfg["host"], cfg["cong"], cfg["tai_khoan"], cfg["mat_khau"],
                                       tls=not cfg["khong_ma_hoa"],
                                       kiem_chung_chi=cfg["kiem_chung_chi_tls"],
                                       van_tay=cfg["van_tay_tls"])
        except routeros.RouterOSError as e:
            log.warning("mikrotik-routeros: không đọc được router: %s", type(e).__name__)
            return []
        try:
            try:
                leases = p.goi_doc(LENH_LEASE, "mac-address,address,host-name,status")
                hosts = p.goi_doc(LENH_HOST, "mac-address,on-interface")
            except routeros.RouterOSError as e:
                log.warning("mikrotik-routeros: lỗi đọc bảng: %s", type(e).__name__)
                return []
        finally:
            p.dong()
        theo_mac: dict[str, dict] = {}
        for ls in leases:
            mac = _mac_chuan(ls.get("mac-address"))
            if not mac:
                continue
            ten = _lam_sach_ten(ls.get("host-name"))
            theo_mac[mac] = {
                "id": f"{self.tien_to}.{mac.replace(':', '')}",
                "ten": ten,
                "loai": "may_mang",
                "thuoc_tinh": {
                    "mac": mac,
                    "ip": str(ls.get("address") or "").strip() or None,
                    "ten_may_tu_khai": ten,
                    "cong": None,
                    "nguon": "dhcp",
                },
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            }
        for h in hosts:
            mac = _mac_chuan(h.get("mac-address"))
            if not mac:
                continue
            cong = _lam_sach_ten(h.get("on-interface"))
            if mac in theo_mac:
                if cong:
                    theo_mac[mac]["thuoc_tinh"]["cong"] = cong
            else:
                theo_mac[mac] = {
                    "id": f"{self.tien_to}.{mac.replace(':', '')}",
                    "ten": "",
                    "loai": "may_mang",
                    "thuoc_tinh": {
                        "mac": mac,
                        "ip": None,
                        "ten_may_tu_khai": "",
                        "cong": cong or None,
                        "nguon": "host",
                    },
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                }
        return list(theo_mac.values())

    async def thuc_the(self, loc) -> list[dict]:
        if not self._cfg:
            return []
        try:
            return await asyncio.to_thread(self._thuc_the_dong_bo)
        except Exception as e:  # lỗi lạ → bỏ, không sập lõi
            log.warning("mikrotik-routeros: lỗi không ngờ khi đọc thực thể: %s", type(e).__name__)
            return []

    async def thuc_hien(self, id_: str, hanh_dong: str, tham_so: dict) -> dict:
        # Module CHỈ ĐỌC: không có hành động nào được thực hiện.
        return {"ok": False, "loi": "Module mikrotik-routeros chỉ đọc: không thực hiện hành động nào."}

    async def trang_thai(self, ids: list[str]) -> dict:
        return {i: {"gia_tri": None, "don_vi": None, "luc": None, "hop_le": False}
                for i in (ids or [])}

    async def su_kien(self):
        return
        yield  # pragma: no cover — module chỉ đọc, không đẩy sự kiện
