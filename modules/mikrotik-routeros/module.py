"""Module mikrotik-routeros — nguồn kiểm soát mạng LAN CHỈ ĐỌC từ router MikroTik qua RouterOS API.

Đọc:
- DHCP lease (``/ip/dhcp-server/lease/print``) và host (``/interface/bridge/host/print``)
  thành thực thể thiết bị mạng C1 (``loai="may_mang"``, ``thuoc_tinh.mac``).
  Tự động dọn thiết bị đã vắng mặt khỏi mạng quá N ngày (cấu hình, mặc định 14).
- Cổng mạng (``/interface/print``): trạng thái lên/xuống, tốc độ Mbit/s tính từ bộ đếm.
- Tài nguyên (``/system/resource/print``): CPU %, RAM %, thời gian chạy router, nhiệt độ nếu có.
- WireGuard peers (``/interface/wireguard/peers/print``): trạng thái kết nối và thời gian bắt tay.
- Kid Control (``/ip/kid-control/device/print``): tốc độ từng máy nếu bật.

CHỈ ĐỌC: mọi lệnh đều qua danh sách cho phép cố định tại client; ``thuc_hien`` luôn từ chối.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any
import unicodedata

from app.tu_xa import _dia_chi_bi_chan
from . import routeros
from .toc_do import tinh_toc_do_mbit, doc_thoi_luong_giay

log = logging.getLogger("web2api.mikrotik_routeros")

LENH_LEASE = "/ip/dhcp-server/lease/print"
LENH_HOST = "/interface/bridge/host/print"
LENH_INTERFACE = "/interface/print"
LENH_RESOURCE = "/system/resource/print"
LENH_WIREGUARD = "/interface/wireguard/peers/print"
LENH_KID_CONTROL = "/ip/kid-control/device/print"

DO_DAI_TEN_TOI_DA = 40
TRAN_DO_DAI_CAU_HINH = 512
TRAN_CONG = 64
TRAN_WG_PEER = 64
NGUONG_WG_KET_NOI_GIAY = 180  # ~1.5 lần chu kỳ rekey 120s của WireGuard

_MAC = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
_MAC_BO = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}
_KY_TU_ID_KHONG_HOP_LE = re.compile(r"[^a-z0-9_]")


def _la_dung(gia_tri: Any) -> bool:
    """Chuỗi cấu hình «true»/«false» → bool (giao diện gửi chuỗi)."""
    if isinstance(gia_tri, bool):
        return gia_tri
    return str(gia_tri or "").strip().lower() in ("true", "1", "yes", "co", "on")


def _lam_sach_ten(gia_tri: Any) -> str:
    """Dữ liệu từ router là KHÔNG TIN: bỏ ký tự điều khiển, cắt 40 ký tự."""
    s = "".join(c for c in str(gia_tri or "") if unicodedata.category(c)[0] != "C")
    return s.strip()[:DO_DAI_TEN_TOI_DA]


def _mac_chuan(giatri: Any) -> str | None:
    """MAC hợp lệ dạng chữ thường có hai chấm; bỏ MAC sai khuôn/broadcast/zero."""
    mac = str(giatri or "").strip()
    if not _MAC.match(mac):
        return None
    mac = mac.lower()
    if mac in _MAC_BO:
        return None
    return mac


def _chuan_hoa_id_ten(ten_goc: str) -> str:
    """Chuẩn hoá tên thành phần ID: chỉ chữ thường, số, gạch dưới."""
    ten = str(ten_goc or "").strip().lower()
    ten = _KY_TU_ID_KHONG_HOP_LE.sub("_", ten).strip("_")
    return ten or "khong_ten"


def _mo_phien_da_dang_nhap(host: str, cong: int, tai_khoan: str, mat_khau: str, *,
                           tls: bool = True, kiem_chung_chi: bool = True, van_tay: str = "") -> routeros.Phien:
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
    def __init__(self, manifest: dict, _thu_muc_du_lieu: str | Path | None = None,
                 _dong_ho: Any = None):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._cfg: dict | None = None
        self._dong_ho = _dong_ho or time.time
        self._dong_ho_mono = time.monotonic

        # Thư mục dữ liệu và sổ bền
        goc = Path(_thu_muc_du_lieu) if _thu_muc_du_lieu else None
        if goc is None and os.environ.get("WEB2API_DATA"):
            from app import kho
            goc = kho.thu_muc_du_lieu() / "du_lieu" / "mikrotik-routeros"
        self._goc = goc
        self._da_canh_bao_so = False
        self._so_may_vang: dict[str, float] = self._doc_so_may_vang()

        # Nhịp và bộ nhớ đệm
        self._chu_ky_nhanh_giay = 15
        self._chu_ky_cham_giay = 120
        self._ngay_giu_may_vang = 14
        self._bat_kid_control = True

        self._lan_doc_nhanh = 0.0
        self._lan_doc_cham = 0.0
        self._khoa_lam_moi = asyncio.Lock()
        # Luồng đọc router còn chạy sau khi thuc_the đã bỏ chờ (2,5 s): không mở luồng thứ hai chồng lên.
        self._luong_dang_chay = threading.Event()

        # Bộ đếm tốc độ cổng: ten_cong -> {"rx": (val, t, uptime), "tx": (val, t, uptime)}
        self._mau_cong: dict[str, dict[str, tuple[int, float, int | None]]] = {}
        self._toc_do_cong: dict[str, dict[str, float | None]] = {}
        self._uptime_router: int | None = None

        # Trạng thái từng nguồn
        self._nguon: dict[str, dict[str, Any]] = {
            "interface": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
            "kid_control": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
            "resource": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
            "wireguard": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
            "lease": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
            "host": {"trang_thai": "chua_doc", "loi": None, "luc": None, "du_lieu": []},
        }
        self._trang_thai_nguon_cu: dict[str, str] = {}
        self._trang_thai_thuc_the: dict[str, dict[str, Any]] = {}
        self._thuc_the_cache: list[dict] = []

    # ── Đọc / ghi sổ bền (thay_lan_cuoi.json) ──

    def _doc_so_may_vang(self) -> dict[str, float]:
        if self._goc is None:
            return {}
        p = self._goc / "thay_lan_cuoi.json"
        if not p.is_file():
            return {}
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                ra: dict[str, float] = {}
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        ra[k.lower()] = float(v)
                return ra
        except Exception as e:
            if not self._da_canh_bao_so:
                log.warning("mikrotik-routeros: lỗi đọc sổ máy vắng (bắt đầu rỗng): %s", type(e).__name__)
                self._da_canh_bao_so = True
        return {}

    def _luu_so_may_vang(self) -> None:
        if self._goc is None:
            return
        try:
            self._goc.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._goc, 0o700)
            except OSError:
                pass
            p = self._goc / "thay_lan_cuoi.json"
            now = self._dong_ho()
            # Giữ mốc để khởi động lại module vẫn nhớ máy vắng; chỉ cắt mục quá 365 ngày
            # hoặc khi vượt quá 5000 mục thì giữ 5000 mục mới nhất.
            gioi_han_luu_tru = 365 * 86400
            so_moi = {
                m: t for m, t in self._so_may_vang.items()
                if (now - t) <= gioi_han_luu_tru
            }
            if len(so_moi) > 5000:
                cac_muc = sorted(so_moi.items(), key=lambda x: x[1], reverse=True)[:5000]
                so_moi = dict(cac_muc)
            self._so_may_vang = so_moi

            noi_dung = json.dumps(self._so_may_vang, indent=2, ensure_ascii=False)
            tam = self._goc / "thay_lan_cuoi.json.tmp"
            tam.write_text(noi_dung, encoding="utf-8")
            try:
                os.chmod(tam, 0o600)
            except OSError:
                pass
            tam.replace(p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except Exception as e:
            if not self._da_canh_bao_so:
                log.warning("mikrotik-routeros: không ghi được sổ máy vắng: %s", type(e).__name__)
                self._da_canh_bao_so = True

    # ── Kiểm tra địa chỉ ──

    def kiem_dia_chi(self, xau: Any) -> tuple[str, int]:
        s = str(xau or "").strip()
        if not s or len(s) > TRAN_DO_DAI_CAU_HINH:
            raise ValueError("dia_chi rỗng hoặc quá dài")
        host, cong_s = s, ""
        if s.startswith("["):
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
                return str(ip), cong
        raise ValueError(f"tên máy {host!r} chỉ phân giải ra địa chỉ bị chặn")

    @staticmethod
    def _kep_so(val: Any, mac_dinh: int, duoi: int, tren: int) -> int:
        if val is None or str(val).strip() == "":
            return mac_dinh
        try:
            n = int(str(val).strip())
        except ValueError:
            return mac_dinh
        return max(duoi, min(tren, n))

    # ── Vòng đời ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng."""
        host, cong = self.kiem_dia_chi(cau_hinh.get("dia_chi"))
        self._chu_ky_nhanh_giay = self._kep_so(cau_hinh.get("chu_ky_nhanh_giay"), 15, 10, 3600)
        self._chu_ky_cham_giay = self._kep_so(cau_hinh.get("chu_ky_cham_giay"), 120, 30, 3600)
        if self._chu_ky_cham_giay < self._chu_ky_nhanh_giay:
            self._chu_ky_cham_giay = self._chu_ky_nhanh_giay
        self._ngay_giu_may_vang = self._kep_so(cau_hinh.get("ngay_giu_may_vang"), 14, 1, 365)
        self._bat_kid_control = _la_dung(cau_hinh.get("bat_kid_control", True))

        self._cfg = {
            "host": host,
            "cong": cong,
            "tai_khoan": str(cau_hinh.get("tai_khoan") or "").strip(),
            "mat_khau": str(cau_hinh.get("mat_khau") or ""),
            "kiem_chung_chi_tls": _la_dung(cau_hinh.get("kiem_chung_chi_tls", True)),
            "van_tay_tls": str(cau_hinh.get("van_tay_tls") or "").strip(),
            "khong_ma_hoa": _la_dung(cau_hinh.get("cho_phep_khong_ma_hoa", False)),
            "chu_ky_nhanh_giay": self._chu_ky_nhanh_giay,
            "chu_ky_cham_giay": self._chu_ky_cham_giay,
            "ngay_giu_may_vang": self._ngay_giu_may_vang,
            "bat_kid_control": self._bat_kid_control,
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

        # Kiểm tra kiểu các trường số mới: chữ rác -> trả ok: False
        for truong, ten_truong in [
            ("chu_ky_nhanh_giay", "Chu kỳ đọc nhanh"),
            ("chu_ky_cham_giay", "Chu kỳ đọc chậm"),
            ("ngay_giu_may_vang", "Số ngày giữ máy vắng"),
        ]:
            val = cau_hinh.get(truong)
            if val is not None and str(val).strip() != "":
                try:
                    int(str(val).strip())
                except ValueError:
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [],
                        "loi": f"Trường «{ten_truong}» phải là số nguyên.",
                    }

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

        ck_nhanh = self._kep_so(cau_hinh.get("chu_ky_nhanh_giay"), 15, 10, 3600)
        ck_cham = self._kep_so(cau_hinh.get("chu_ky_cham_giay"), 120, 30, 3600)
        if ck_cham < ck_nhanh:
            ck_cham = ck_nhanh
        ngay_giu = self._kep_so(cau_hinh.get("ngay_giu_may_vang"), 14, 1, 365)
        bat_kc = "true" if _la_dung(cau_hinh.get("bat_kid_control", True)) else "false"

        return {
            "ok": True,
            "ket_qua_tung_khoa": [],
            "loi": None,
            "cau_hinh_luu": {
                "dia_chi": dia_chi,
                "tai_khoan": tai_khoan,
                "mat_khau": mat_khau,
                "kiem_chung_chi_tls": str(cau_hinh.get("kiem_chung_chi_tls", "true")),
                "van_tay_tls": str(cau_hinh.get("van_tay_tls") or ""),
                "cho_phep_khong_ma_hoa": str(cau_hinh.get("cho_phep_khong_ma_hoa", "false")),
                "chu_ky_nhanh_giay": str(ck_nhanh),
                "chu_ky_cham_giay": str(ck_cham),
                "ngay_giu_may_vang": str(ngay_giu),
                "bat_kid_control": bat_kc,
            },
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
            return {"ok": False, "khoa": [], "loi": str(e), "nguon": self._lay_thong_tin_nguon()}
        await asyncio.to_thread(p.dong)
        return {"ok": True, "khoa": [], "nguon": self._lay_thong_tin_nguon()}

    def _lay_thong_tin_nguon(self) -> dict[str, dict[str, Any]]:
        ra: dict[str, dict[str, Any]] = {}
        for ten, info in self._nguon.items():
            luc_iso = None
            if info["luc"] is not None:
                luc_iso = datetime.fromtimestamp(info["luc"], tz=timezone.utc).isoformat(timespec="seconds")
            ra[ten] = {
                "trang_thai": info["trang_thai"],
                "loi": info["loi"],
                "luc": luc_iso,
            }
        return ra

    async def dung(self) -> None:
        self._cfg = None

    # ── Làm mới dữ liệu theo nhịp ──

    def _ghi_log_chuyen_trang_thai(self, ten_nguon: str, trang_thai_moi: str, ma_loi: str | None) -> None:
        trang_thai_cu = self._trang_thai_nguon_cu.get(ten_nguon, "chua_doc")
        if trang_thai_cu != trang_thai_moi:
            if trang_thai_moi == "loi":
                log.warning("mikrotik-routeros: nguồn %s chuyển sang lỗi: %s", ten_nguon, ma_loi)
            elif trang_thai_moi == "ok" and trang_thai_cu == "loi":
                log.info("mikrotik-routeros: nguồn %s đã phục hồi bình thường", ten_nguon)
            self._trang_thai_nguon_cu[ten_nguon] = trang_thai_moi

    def _ghi_loi_nguon(self, ten_nguon: str, ma_loi: str) -> None:
        info = self._nguon[ten_nguon]
        info["trang_thai"] = "loi"
        info["loi"] = ma_loi
        self._ghi_log_chuyen_trang_thai(ten_nguon, "loi", ma_loi)

    def _ghi_thanh_cong_nguon(self, ten_nguon: str, du_lieu: list[dict], now_t: float) -> None:
        info = self._nguon[ten_nguon]
        info["trang_thai"] = "ok"
        info["loi"] = None
        info["luc"] = now_t
        info["du_lieu"] = du_lieu
        self._ghi_log_chuyen_trang_thai(ten_nguon, "ok", None)

    def _ghi_khong_co_nguon(self, ten_nguon: str) -> None:
        info = self._nguon[ten_nguon]
        info["trang_thai"] = "khong_co"
        info["loi"] = None
        info["du_lieu"] = []
        self._ghi_log_chuyen_trang_thai(ten_nguon, "khong_co", None)

    def _lam_moi_dong_bo(self, can_nhanh: bool, can_cham: bool) -> None:
        cfg = self._cfg
        if not cfg:
            return

        now_t = self._dong_ho()
        now_mono = self._dong_ho_mono()

        try:
            p = _mo_phien_da_dang_nhap(
                cfg["host"], cfg["cong"], cfg["tai_khoan"], cfg["mat_khau"],
                tls=not cfg["khong_ma_hoa"],
                kiem_chung_chi=cfg["kiem_chung_chi_tls"],
                van_tay=cfg["van_tay_tls"],
            )
        except Exception:
            # Lỗi kết nối / login: đánh dấu lỗi ket_noi cho mọi nguồn cần đọc lượt này
            if can_nhanh:
                self._ghi_loi_nguon("interface", "ket_noi")
                if cfg.get("bat_kid_control"):
                    self._ghi_loi_nguon("kid_control", "ket_noi")
            if can_cham:
                self._ghi_loi_nguon("resource", "ket_noi")
                self._ghi_loi_nguon("wireguard", "ket_noi")
                self._ghi_loi_nguon("lease", "ket_noi")
                self._ghi_loi_nguon("host", "ket_noi")
            return

        try:
            # 1. Đọc nhóm nhanh
            if can_nhanh:
                # interface
                try:
                    res = p.goi_doc(LENH_INTERFACE, tran_so_cau=200)
                    self._ghi_thanh_cong_nguon("interface", res, now_t)
                    self._cap_nhat_toc_do_cong(res, now_t)
                except routeros.LoiRouterTuChoi:
                    self._ghi_loi_nguon("interface", "router_tu_choi")
                except (routeros.LoiKetNoi, OSError):
                    self._ghi_loi_nguon("interface", "ket_noi")
                    return
                except routeros.RouterOSError:
                    self._ghi_loi_nguon("interface", "doc")
                    return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này

                # kid_control
                if cfg.get("bat_kid_control"):
                    try:
                        res_kc = p.goi_doc(LENH_KID_CONTROL, tran_so_cau=200)
                        self._ghi_thanh_cong_nguon("kid_control", res_kc, now_t)
                    except routeros.LoiRouterTuChoi:
                        self._ghi_khong_co_nguon("kid_control")
                    except (routeros.LoiKetNoi, OSError):
                        self._ghi_loi_nguon("kid_control", "ket_noi")
                        return
                    except routeros.RouterOSError:
                        self._ghi_loi_nguon("kid_control", "doc")
                        return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này
                else:
                    self._ghi_khong_co_nguon("kid_control")

            # 2. Đọc nhóm chậm
            if can_cham:
                # resource
                try:
                    res_r = p.goi_doc(LENH_RESOURCE, tran_so_cau=10)
                    self._ghi_thanh_cong_nguon("resource", res_r, now_t)
                    if res_r:
                        up_s = doc_thoi_luong_giay(res_r[0].get("uptime"))
                        if up_s is not None:
                            self._uptime_router = up_s
                except routeros.LoiRouterTuChoi:
                    self._ghi_loi_nguon("resource", "router_tu_choi")
                except (routeros.LoiKetNoi, OSError):
                    self._ghi_loi_nguon("resource", "ket_noi")
                    return
                except routeros.RouterOSError:
                    self._ghi_loi_nguon("resource", "doc")
                    return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này

                # wireguard
                try:
                    res_wg = p.goi_doc(LENH_WIREGUARD, tran_so_cau=100)
                    self._ghi_thanh_cong_nguon("wireguard", res_wg, now_t)
                except routeros.LoiRouterTuChoi:
                    self._ghi_khong_co_nguon("wireguard")
                except (routeros.LoiKetNoi, OSError):
                    self._ghi_loi_nguon("wireguard", "ket_noi")
                    return
                except routeros.RouterOSError:
                    self._ghi_loi_nguon("wireguard", "doc")
                    return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này

                # lease
                try:
                    res_l = p.goi_doc(LENH_LEASE, "mac-address,address,host-name,status,last-seen", tran_so_cau=routeros.TRAN_SO_CAU_TOI_DA)
                    self._ghi_thanh_cong_nguon("lease", res_l, now_t)
                except routeros.LoiRouterTuChoi:
                    self._ghi_loi_nguon("lease", "router_tu_choi")
                except (routeros.LoiKetNoi, OSError):
                    self._ghi_loi_nguon("lease", "ket_noi")
                    return
                except routeros.RouterOSError:
                    self._ghi_loi_nguon("lease", "doc")
                    return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này

                # host
                try:
                    res_h = p.goi_doc(LENH_HOST, "mac-address,on-interface", tran_so_cau=routeros.TRAN_SO_CAU_TOI_DA)
                    self._ghi_thanh_cong_nguon("host", res_h, now_t)
                except routeros.LoiRouterTuChoi:
                    self._ghi_loi_nguon("host", "router_tu_choi")
                except (routeros.LoiKetNoi, OSError):
                    self._ghi_loi_nguon("host", "ket_noi")
                    return
                except routeros.RouterOSError:
                    self._ghi_loi_nguon("host", "doc")
                    return  # lỗi giao thức giữa chừng (hết giờ, quá số câu, sai khuôn): phản hồi dở còn trong luồng, KHÔNG đọc tiếp trên phiên này
        finally:
            p.dong()

        if can_nhanh:
            self._lan_doc_nhanh = now_mono
        if can_cham:
            self._lan_doc_cham = now_mono

    def _cap_nhat_toc_do_cong(self, interfaces: list[dict], now_t: float) -> None:
        uptime = self._uptime_router
        ten_cac_cong_hien_tai = set()
        for iface in interfaces[:TRAN_CONG]:
            ten = _lam_sach_ten(iface.get("name"))
            if not ten:
                continue
            ten_cac_cong_hien_tai.add(ten)

            rx_raw = iface.get("rx-byte")
            tx_raw = iface.get("tx-byte")
            try:
                rx_val = int(rx_raw) if rx_raw is not None else None
            except ValueError:
                rx_val = None
            try:
                tx_val = int(tx_raw) if tx_raw is not None else None
            except ValueError:
                tx_val = None

            nay_rx = (rx_val, now_t, uptime) if rx_val is not None else None
            nay_tx = (tx_val, now_t, uptime) if tx_val is not None else None

            mau = self._mau_cong.get(ten, {})
            truoc_rx = mau.get("rx")
            truoc_tx = mau.get("tx")

            mbit_rx = tinh_toc_do_mbit(truoc_rx, nay_rx)
            mbit_tx = tinh_toc_do_mbit(truoc_tx, nay_tx)

            self._toc_do_cong[ten] = {"rx": mbit_rx, "tx": mbit_tx}

            # Cập nhật mẫu gốc nếu hợp lệ và dt >= 1s (hoặc mẫu đầu)
            moi_rx = truoc_rx
            if nay_rx is not None:
                if truoc_rx is None or (nay_rx[1] - truoc_rx[1] >= 1.0):
                    moi_rx = nay_rx

            moi_tx = truoc_tx
            if nay_tx is not None:
                if truoc_tx is None or (nay_tx[1] - truoc_tx[1] >= 1.0):
                    moi_tx = nay_tx

            self._mau_cong[ten] = {"rx": moi_rx, "tx": moi_tx}

        # Dọn các cổng đã biến mất khỏi router (không giữ mẫu ma)
        cong_da_mat = set(self._mau_cong.keys()) - ten_cac_cong_hien_tai
        for c in cong_da_mat:
            self._mau_cong.pop(c, None)
            self._toc_do_cong.pop(c, None)

    async def _lam_moi_neu_can(self) -> None:
        if not self._cfg:
            return
        now_mono = self._dong_ho_mono()
        can_nhanh = (self._lan_doc_nhanh == 0.0) or (now_mono - self._lan_doc_nhanh >= self._chu_ky_nhanh_giay)
        can_cham = (self._lan_doc_cham == 0.0) or (now_mono - self._lan_doc_cham >= self._chu_ky_cham_giay)
        if not can_nhanh and not can_cham:
            return

        if self._khoa_lam_moi.locked() or self._luong_dang_chay.is_set():
            return

        async with self._khoa_lam_moi:
            now_mono = self._dong_ho_mono()
            can_nhanh = (self._lan_doc_nhanh == 0.0) or (now_mono - self._lan_doc_nhanh >= self._chu_ky_nhanh_giay)
            can_cham = (self._lan_doc_cham == 0.0) or (now_mono - self._lan_doc_cham >= self._chu_ky_cham_giay)
            if not can_nhanh and not can_cham:
                return

            loop = asyncio.get_running_loop()
            fut: asyncio.Future[None] = loop.create_future()

            def _chay() -> None:
                try:
                    self._lam_moi_dong_bo(can_nhanh, can_cham)
                    if not loop.is_closed() and not fut.done():
                        try:
                            loop.call_soon_threadsafe(fut.set_result, None)
                        except RuntimeError:
                            pass
                except Exception as e:
                    if not loop.is_closed() and not fut.done():
                        try:
                            loop.call_soon_threadsafe(fut.set_exception, e)
                        except RuntimeError:
                            pass

            def _chay_roi_nha() -> None:
                try:
                    _chay()
                finally:
                    self._luong_dang_chay.clear()

            self._luong_dang_chay.set()
            t = threading.Thread(target=_chay_roi_nha, daemon=True)
            t.start()
            try:
                await asyncio.wait_for(asyncio.shield(fut), timeout=2.5)
            except asyncio.TimeoutError:
                log.warning("mikrotik-routeros: làm mới quá 2.5s, dùng dữ liệu đệm")

    # ── Tạo thực thể và trạng thái ──

    def _la_nguon_hop_le(self, ten_nguon: str, chu_ky_giay: float) -> bool:
        info = self._nguon.get(ten_nguon)
        if not info or info["trang_thai"] != "ok":
            return False
        if info["luc"] is None:
            return False
        # Dữ liệu cũ hơn 3 nhịp -> không hợp lệ
        if (self._dong_ho() - info["luc"]) > (3 * chu_ky_giay):
            return False
        return True

    def _tao_thuc_the_va_trang_thai(self) -> list[dict]:
        danh_sach: list[dict] = []
        tt: dict[str, dict[str, Any]] = {}
        now_t = self._dong_ho()
        now_iso = datetime.fromtimestamp(now_t, tz=timezone.utc).isoformat(timespec="seconds")

        # 1. Cổng mạng (loai="cong_mang")
        info_if = self._nguon["interface"]
        hop_le_if = self._la_nguon_hop_le("interface", self._chu_ky_nhanh_giay)
        luc_if = (
            datetime.fromtimestamp(info_if["luc"], tz=timezone.utc).isoformat(timespec="seconds")
            if info_if["luc"] else None
        )
        ma_loi_if = info_if["loi"]

        da_dung_id_cong: set[str] = set()
        for iface in info_if["du_lieu"][:TRAN_CONG]:
            ten_goc = _lam_sach_ten(iface.get("name"))
            if not ten_goc:
                continue
            ten_chuan = _chuan_hoa_id_ten(ten_goc)
            if ten_chuan in da_dung_id_cong:
                ten_chuan = f"{ten_chuan}_{hashlib.sha256(ten_goc.encode()).hexdigest()[:6]}"
            da_dung_id_cong.add(ten_chuan)

            running = _la_dung(iface.get("running", False))
            disabled = _la_dung(iface.get("disabled", False))
            if disabled:
                trang_thai_cong = "tat"
            elif running:
                trang_thai_cong = "len"
            else:
                trang_thai_cong = "xuong"

            # 1a. Trạng thái cổng
            id_tt = f"{self.tien_to}.cong_{ten_chuan}_trang_thai"
            danh_sach.append({
                "id": id_tt,
                "ten": f"Cổng {ten_goc} (trạng thái)",
                "loai": "cong_mang",
                "thuoc_tinh": {"ten_cong": ten_goc},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_tt] = {
                "gia_tri": trang_thai_cong if hop_le_if else None,
                "don_vi": None,
                "luc": luc_if,
                "hop_le": hop_le_if,
            }
            if ma_loi_if:
                tt[id_tt]["loi"] = ma_loi_if

            # 1b. Tốc độ nhận Mbit/s
            id_rx = f"{self.tien_to}.cong_{ten_chuan}_nhan_mbit"
            toc_do = self._toc_do_cong.get(ten_goc, {})
            mbit_rx = toc_do.get("rx")
            hop_le_rx = hop_le_if and (mbit_rx is not None)
            danh_sach.append({
                "id": id_rx,
                "ten": f"Cổng {ten_goc} (tốc độ nhận)",
                "loai": "cong_mang",
                "thuoc_tinh": {"ten_cong": ten_goc},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_rx] = {
                "gia_tri": mbit_rx if hop_le_rx else None,
                "don_vi": "Mbit/s",
                "luc": luc_if,
                "hop_le": hop_le_rx,
            }
            if ma_loi_if:
                tt[id_rx]["loi"] = ma_loi_if

            # 1c. Tốc độ gửi Mbit/s
            id_tx = f"{self.tien_to}.cong_{ten_chuan}_gui_mbit"
            mbit_tx = toc_do.get("tx")
            hop_le_tx = hop_le_if and (mbit_tx is not None)
            danh_sach.append({
                "id": id_tx,
                "ten": f"Cổng {ten_goc} (tốc độ gửi)",
                "loai": "cong_mang",
                "thuoc_tinh": {"ten_cong": ten_goc},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_tx] = {
                "gia_tri": mbit_tx if hop_le_tx else None,
                "don_vi": "Mbit/s",
                "luc": luc_if,
                "hop_le": hop_le_tx,
            }
            if ma_loi_if:
                tt[id_tx]["loi"] = ma_loi_if

        # 2. Tài nguyên (loai="cam_bien")
        info_res = self._nguon["resource"]
        hop_le_res = self._la_nguon_hop_le("resource", self._chu_ky_cham_giay)
        luc_res = (
            datetime.fromtimestamp(info_res["luc"], tz=timezone.utc).isoformat(timespec="seconds")
            if info_res["luc"] else None
        )
        ma_loi_res = info_res["loi"]

        if info_res["du_lieu"]:
            r = info_res["du_lieu"][0]

            # 2a. CPU %
            cpu_raw = r.get("cpu-load")
            cpu_val = None
            try:
                cpu_val = int(cpu_raw) if cpu_raw is not None else None
            except ValueError:
                pass
            id_cpu = f"{self.tien_to}.router_cpu_phan_tram"
            danh_sach.append({
                "id": id_cpu,
                "ten": "Router CPU",
                "loai": "cam_bien",
                "thuoc_tinh": {},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_cpu] = {
                "gia_tri": cpu_val if hop_le_res else None,
                "don_vi": "%",
                "luc": luc_res,
                "hop_le": hop_le_res and (cpu_val is not None),
            }
            if ma_loi_res:
                tt[id_cpu]["loi"] = ma_loi_res

            # 2b. RAM %
            free_raw = r.get("free-memory")
            tot_raw = r.get("total-memory")
            ram_pct = None
            hop_le_ram = False
            try:
                if free_raw is not None and tot_raw is not None:
                    free_mem = int(free_raw)
                    tot_mem = int(tot_raw)
                    if tot_mem > 0:
                        ram_pct = round(100.0 * (tot_mem - free_mem) / tot_mem, 1)
                        hop_le_ram = hop_le_res
            except ValueError:
                pass
            id_ram = f"{self.tien_to}.router_ram_phan_tram"
            danh_sach.append({
                "id": id_ram,
                "ten": "Router RAM",
                "loai": "cam_bien",
                "thuoc_tinh": {},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_ram] = {
                "gia_tri": ram_pct if hop_le_ram else None,
                "don_vi": "%",
                "luc": luc_res,
                "hop_le": hop_le_ram,
            }
            if ma_loi_res:
                tt[id_ram]["loi"] = ma_loi_res

            # 2c. Uptime
            uptime_raw = r.get("uptime")
            uptime_s = doc_thoi_luong_giay(uptime_raw)
            id_uptime = f"{self.tien_to}.router_thoi_gian_chay_giay"
            danh_sach.append({
                "id": id_uptime,
                "ten": "Router thời gian chạy",
                "loai": "cam_bien",
                "thuoc_tinh": {},
                "tinh_nang": [],
                "rui_ro_mac_dinh": "doc",
            })
            tt[id_uptime] = {
                "gia_tri": uptime_s if hop_le_res else None,
                "don_vi": "s",
                "luc": luc_res,
                "hop_le": hop_le_res and (uptime_s is not None),
            }
            if ma_loi_res:
                tt[id_uptime]["loi"] = ma_loi_res

            # 2d. Nhiệt độ nếu có
            temp_raw = r.get("temperature") or r.get("cpu-temperature")
            temp_val = None
            if temp_raw is not None:
                try:
                    temp_val = float(str(temp_raw).strip())
                except ValueError:
                    pass
            if temp_val is not None:
                id_temp = f"{self.tien_to}.router_nhiet_do_c"
                danh_sach.append({
                    "id": id_temp,
                    "ten": "Router nhiệt độ",
                    "loai": "cam_bien",
                    "thuoc_tinh": {},
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
                tt[id_temp] = {
                    "gia_tri": temp_val if hop_le_res else None,
                    "don_vi": "°C",
                    "luc": luc_res,
                    "hop_le": hop_le_res,
                }
                if ma_loi_res:
                    tt[id_temp]["loi"] = ma_loi_res

        # 3. WireGuard peers (loai="cam_bien")
        info_wg = self._nguon["wireguard"]
        hop_le_wg = self._la_nguon_hop_le("wireguard", self._chu_ky_cham_giay)
        luc_wg = (
            datetime.fromtimestamp(info_wg["luc"], tz=timezone.utc).isoformat(timespec="seconds")
            if info_wg["luc"] else None
        )
        ma_loi_wg = info_wg["loi"]

        if info_wg["trang_thai"] != "khong_co":
            for idx, peer in enumerate(info_wg["du_lieu"][:TRAN_WG_PEER], start=1):
                pub_key = str(peer.get("public-key") or "")
                if not pub_key:
                    continue
                bam = hashlib.sha256(pub_key.encode("utf-8")).hexdigest()[:12]
                ten_peer = (
                    _lam_sach_ten(peer.get("name") or peer.get("comment"))
                    or f"peer {idx}"
                )

                hs_raw = peer.get("last-handshake")
                hs_s = doc_thoi_luong_giay(hs_raw)
                disabled = _la_dung(peer.get("disabled", False))

                co_ket_noi = "co" if (not disabled and hs_s is not None and hs_s <= NGUONG_WG_KET_NOI_GIAY) else "khong"

                # 3a. Kết nối
                id_kn = f"{self.tien_to}.wg_{bam}_ket_noi"
                danh_sach.append({
                    "id": id_kn,
                    "ten": f"WireGuard {ten_peer} (kết nối)",
                    "loai": "cam_bien",
                    "thuoc_tinh": {"ten_peer": ten_peer},
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
                tt[id_kn] = {
                    "gia_tri": co_ket_noi if hop_le_wg else None,
                    "don_vi": None,
                    "luc": luc_wg,
                    "hop_le": hop_le_wg,
                }
                if ma_loi_wg:
                    tt[id_kn]["loi"] = ma_loi_wg

                # 3b. Bắt tay
                id_bt = f"{self.tien_to}.wg_{bam}_bat_tay_giay"
                danh_sach.append({
                    "id": id_bt,
                    "ten": f"WireGuard {ten_peer} (bắt tay gần nhất)",
                    "loai": "cam_bien",
                    "thuoc_tinh": {"ten_peer": ten_peer},
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
                # Nếu chưa từng bắt tay: gia_tri là None nhưng hop_le True
                tt[id_bt] = {
                    "gia_tri": hs_s if hop_le_wg else None,
                    "don_vi": "s",
                    "luc": luc_wg,
                    "hop_le": hop_le_wg,
                }
                if ma_loi_wg:
                    tt[id_bt]["loi"] = ma_loi_wg

        # 4. Kid Control (loai="cam_bien")
        info_kc = self._nguon["kid_control"]
        hop_le_kc = self._la_nguon_hop_le("kid_control", self._chu_ky_nhanh_giay)
        luc_kc = (
            datetime.fromtimestamp(info_kc["luc"], tz=timezone.utc).isoformat(timespec="seconds")
            if info_kc["luc"] else None
        )
        ma_loi_kc = info_kc["loi"]

        if info_kc["trang_thai"] != "khong_co" and self._bat_kid_control:
            for dev in info_kc["du_lieu"]:
                mac = _mac_chuan(dev.get("mac-address"))
                if not mac:
                    continue
                mac12 = mac.replace(":", "")
                ten_may = _lam_sach_ten(dev.get("name")) or mac

                try:
                    down_bps = int(dev.get("rate-down") or 0)
                except ValueError:
                    down_bps = 0
                try:
                    up_bps = int(dev.get("rate-up") or 0)
                except ValueError:
                    up_bps = 0

                down_mbit = round(down_bps / 1_000_000.0, 3)
                up_mbit = round(up_bps / 1_000_000.0, 3)

                id_down = f"{self.tien_to}.kc_{mac12}_nhan_mbit"
                danh_sach.append({
                    "id": id_down,
                    "ten": f"Kid Control {ten_may} (nhận)",
                    "loai": "cam_bien",
                    "thuoc_tinh": {"mac_may": mac, "ten_may": ten_may},
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
                tt[id_down] = {
                    "gia_tri": down_mbit if hop_le_kc else None,
                    "don_vi": "Mbit/s",
                    "luc": luc_kc,
                    "hop_le": hop_le_kc,
                }
                if ma_loi_kc:
                    tt[id_down]["loi"] = ma_loi_kc

                id_up = f"{self.tien_to}.kc_{mac12}_gui_mbit"
                danh_sach.append({
                    "id": id_up,
                    "ten": f"Kid Control {ten_may} (gửi)",
                    "loai": "cam_bien",
                    "thuoc_tinh": {"mac_may": mac, "ten_may": ten_may},
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
                tt[id_up] = {
                    "gia_tri": up_mbit if hop_le_kc else None,
                    "don_vi": "Mbit/s",
                    "luc": luc_kc,
                    "hop_le": hop_le_kc,
                }
                if ma_loi_kc:
                    tt[id_up]["loi"] = ma_loi_kc

        # 5. Máy mạng LAN (loai="may_mang") — kèm dọn thiết bị đã mất
        leases = self._nguon["lease"]["du_lieu"]
        hosts = self._nguon["host"]["du_lieu"]

        # Tập MAC đang thấy: có mặt trong bảng host hoặc lease bound
        mac_dang_thay: set[str] = set()
        host_cong: dict[str, str] = {}
        for h in hosts:
            mac_h = _mac_chuan(h.get("mac-address"))
            if mac_h:
                mac_dang_thay.add(mac_h)
                cong = _lam_sach_ten(h.get("on-interface"))
                if cong:
                    host_cong[mac_h] = cong

        lease_theo_mac: dict[str, dict] = {}
        for ls in leases:
            mac_l = _mac_chuan(ls.get("mac-address"))
            if not mac_l:
                continue
            lease_theo_mac[mac_l] = ls
            if ls.get("status") == "bound":
                mac_dang_thay.add(mac_l)

        tat_ca_mac = set(lease_theo_mac.keys()) | set(host_cong.keys())
        nguong_vang_giay = self._ngay_giu_may_vang * 86400

        for mac in tat_ca_mac:
            if mac in mac_dang_thay:
                lan_cuoi_thay = now_t
                if mac in self._so_may_vang:
                    del self._so_may_vang[mac]
            else:
                # Không đang thấy
                ls = lease_theo_mac.get(mac)
                ls_sec = doc_thoi_luong_giay(ls.get("last-seen")) if ls else None
                if ls_sec is not None:
                    lan_cuoi_thay = now_t - ls_sec
                elif mac in self._so_may_vang:
                    lan_cuoi_thay = self._so_may_vang[mac]
                else:
                    # Lần đầu quan sát thấy vắng mặt
                    self._so_may_vang[mac] = now_t
                    lan_cuoi_thay = now_t

            # Nếu vắng mặt quá hạn: ngừng báo trong sổ thiết bị
            if (now_t - lan_cuoi_thay) > nguong_vang_giay:
                continue

            ls = lease_theo_mac.get(mac)
            if ls:
                ten = _lam_sach_ten(ls.get("host-name"))
                cong = host_cong.get(mac)
                danh_sach.append({
                    "id": f"{self.tien_to}.{mac.replace(':', '')}",
                    "ten": ten,
                    "loai": "may_mang",
                    "thuoc_tinh": {
                        "mac": mac,
                        "ip": str(ls.get("address") or "").strip() or None,
                        "ten_may_tu_khai": ten,
                        "cong": cong or None,
                        "nguon": "dhcp",
                    },
                    "tinh_nang": [],
                    "rui_ro_mac_dinh": "doc",
                })
            else:
                cong = host_cong.get(mac)
                danh_sach.append({
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
                })

        self._luu_so_may_vang()
        self._trang_thai_thuc_the = tt
        self._thuc_the_cache = danh_sach
        return danh_sach

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        if not self._cfg:
            return []
        try:
            await self._lam_moi_neu_can()
        except Exception as e:
            log.warning("mikrotik-routeros: lỗi khi làm mới thực thể: %s", type(e).__name__)
        return self._tao_thuc_the_va_trang_thai()

    async def thuc_hien(self, id_: str, hanh_dong: str, tham_so: dict) -> dict:
        return {"ok": False, "loi": "Module mikrotik-routeros chỉ đọc: không thực hiện hành động nào."}

    async def trang_thai(self, ids: list[str]) -> dict:
        if not self._cfg:
            return {i: {"gia_tri": None, "don_vi": None, "luc": None, "hop_le": False} for i in (ids or [])}
        try:
            await self._lam_moi_neu_can()
        except Exception as e:
            log.warning("mikrotik-routeros: lỗi khi làm mới trạng thái: %s", type(e).__name__)

        self._tao_thuc_the_va_trang_thai()
        ra: dict[str, dict] = {}
        for i in (ids or []):
            if i in self._trang_thai_thuc_the:
                ra[i] = self._trang_thai_thuc_the[i]
            else:
                ra[i] = {"gia_tri": None, "don_vi": None, "luc": None, "hop_le": False}
        return ra

    async def su_kien(self):
        return
        yield  # pragma: no cover — module chỉ đọc, không đẩy sự kiện
