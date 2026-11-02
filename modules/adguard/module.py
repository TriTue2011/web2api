"""Module adguard — thống kê DNS chặn quảng cáo + bật/tắt bảo vệ AdGuard Home.

Thực thể (chuẩn thiet_bi của lõi, không có route riêng):
- ``adguard.tong_truy_van`` / ``adguard.so_bi_chan`` / ``adguard.ti_le_chan``
  / ``adguard.thoi_gian_xu_ly_trung_binh`` / ``adguard.so_bo_loc_dang_bat``:
  cảm biến chỉ đọc (``rui_ro_mac_dinh: "doc"``).
- ``adguard.bao_ve``: công tắc bảo vệ, một hành động duy nhất ``bao_ve`` với
  ``rui_ro_mac_dinh: "nguy_hiem"`` — tức qua đúng cơ chế quyền/duyệt của lõi
  (khách thiếu quyền -> 403; khách gọi nguy hiểm -> 202 hàng chờ phê duyệt;
  quản trị thiếu xác nhận -> 409 ``can_xac_nhan``; nhật ký kiểm toán của lõi
  ghi ai làm, lúc nào). Module không tự đặt cơ chế duyệt riêng và không có
  hành động ghi nào khác vào AdGuard (không sửa bộ lọc, không xoá nhật ký).

Địa chỉ: CHỈ LAN. ``kiem_dia_chi`` phân giải tên máy và ghim IP đã kiểm
(chống DNS rebinding) như module mikrotik-routeros, rồi siết thêm: IP công
cộng bị từ chối (chỉ chấp nhận IP riêng RFC1918/ULA); loopback chỉ cho
phép khi test bật ``WEB2API_TEST_CHO_PHEP_LOOPBACK=1`` (máy chủ giả).
Mật khẩu chỉ nằm trong cấu hình đã lưu của lõi (kho bí mật mã hoá), không
vào manifest, log, ngoại lệ hay phản hồi; mọi lỗi AdGuard ra mã rõ
(``sai_dang_nhap`` / ``mang_loi`` / ``het_gio`` / ``loi_may_chu`` /
``du_lieu_sai``), không nuốt (test nv45).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any

from app.tu_xa import _dia_chi_bi_chan, cho_phep_loopback_cho_test
from . import adguard as ag

log = logging.getLogger("web2api.adguard")

CONG_MAC_DINH = 3000
TRAN_DO_DAI_CAU_HINH = 512
TOP_MAC_DINH = 10
TOP_TOI_THIEU = 1
TOP_TOI_DA = 50
THOI_GIAN_TAT_MAC_DINH_GIAY = 300
THOI_GIAN_TAT_TOI_THIEU_GIAY = 30
THOI_GIAN_TAT_TOI_DA_GIAY = 86400
CHU_KY_MAC_DINH_GIAY = 30


def _la_dung(gia_tri: Any) -> bool:
    if isinstance(gia_tri, bool):
        return gia_tri
    return str(gia_tri or "").strip().lower() in ("true", "1", "yes", "co", "on")


def _kep_so(val: Any, mac_dinh: int, duoi: int, tren: int) -> int:
    if val is None or str(val).strip() == "":
        return mac_dinh
    try:
        n = int(str(val).strip())
    except ValueError:
        return mac_dinh
    return max(duoi, min(tren, n))


class Module:
    def __init__(self, manifest: dict, _thu_muc_du_lieu: Any = None, _dong_ho: Any = None):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._cfg: dict | None = None
        self._dong_ho = _dong_ho or time.time
        self._dong_ho_mono = time.monotonic
        self._khoa_lam_moi = asyncio.Lock()
        self._lan_doc = 0.0
        self._thong_ke: dict | None = None
        self._loi: str | None = None

    # ── Kiểm tra địa chỉ (chỉ LAN, ghim IP) ──

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
            cong = int(cong_s) if cong_s else CONG_MAC_DINH
        except ValueError:
            raise ValueError(f"cổng {cong_s!r} không phải số") from None
        if not 1 <= cong <= 65535:
            raise ValueError(f"cổng {cong} ngoài khoảng 1–65535")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            self._kiem_ip_lan(ip)
            return str(ip), cong
        try:
            thong_tin = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {host!r}") from e
        loi_cuoi: str | None = None
        for thong in thong_tin:
            try:
                ip = ipaddress.ip_address(thong[4][0])
            except ValueError:
                continue
            try:
                self._kiem_ip_lan(ip)
            except ValueError as e:
                loi_cuoi = str(e)
                continue
            return str(ip), cong
        raise ValueError(loi_cuoi or f"tên máy {host!r} chỉ phân giải ra địa chỉ không phải LAN")

    @staticmethod
    def _kiem_ip_lan(ip: Any) -> None:
        ly_do = _dia_chi_bi_chan(ip)
        if ly_do and not (ip.is_loopback and cho_phep_loopback_cho_test()):
            raise ValueError(f"dia_chi trỏ vào {ip} — bị chặn: {ly_do}")
        if ip.is_loopback:
            if not cho_phep_loopback_cho_test():
                raise ValueError(f"dia_chi trỏ vào {ip} — loopback chỉ cho phép ở test")
            return
        if not ip.is_private:
            raise ValueError(f"dia_chi trỏ vào {ip} — không phải địa chỉ LAN")

    # ── Vòng đời ──

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng."""
        host, cong = self.kiem_dia_chi(cau_hinh.get("dia_chi"))
        self._cfg = {
            "host": host,
            "cong": cong,
            "tai_khoan": str(cau_hinh.get("tai_khoan") or "").strip(),
            "mat_khau": str(cau_hinh.get("mat_khau") or ""),
            "gioi_han_top": _kep_so(cau_hinh.get("gioi_han_top"), TOP_MAC_DINH, TOP_TOI_THIEU, TOP_TOI_DA),
            "thoi_gian_tat_mac_dinh_giay": _kep_so(
                cau_hinh.get("thoi_gian_tat_mac_dinh_giay"),
                THOI_GIAN_TAT_MAC_DINH_GIAY, THOI_GIAN_TAT_TOI_THIEU_GIAY, THOI_GIAN_TAT_TOI_DA_GIAY,
            ),
            "chu_ky_giay": _kep_so(cau_hinh.get("chu_ky_giay"), CHU_KY_MAC_DINH_GIAY, 10, 3600),
        }

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình bằng cách nối + đăng nhập thật vào AdGuard."""
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip()
        tai_khoan = str(cau_hinh.get("tai_khoan") or "").strip()
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        if not dia_chi:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập địa chỉ AdGuard (dia_chi)."}
        if not tai_khoan:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập tài khoản (tai_khoan)."}
        if not mat_khau:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập mật khẩu (mat_khau)."}
        for truong, ten_truong in [
            ("gioi_han_top", "Giới hạn top"),
            ("thoi_gian_tat_mac_dinh_giay", "Thời gian tắt mặc định"),
            ("chu_ky_giay", "Chu kỳ đọc"),
        ]:
            val = cau_hinh.get(truong)
            if val is not None and str(val).strip() != "":
                try:
                    int(str(val).strip())
                except ValueError:
                    return {"ok": False, "ket_qua_tung_khoa": [],
                            "loi": f"Trường «{ten_truong}» phải là số nguyên."}
        try:
            host, cong = self.kiem_dia_chi(dia_chi)
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        try:
            await asyncio.to_thread(ag.MayChu(host, cong, tai_khoan, mat_khau).kiem_tra_dang_nhap)
        except ag.AdGuardError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": e.thong_bao}
        return {
            "ok": True,
            "ket_qua_tung_khoa": [],
            "loi": None,
            "cau_hinh_luu": {
                "dia_chi": dia_chi,
                "tai_khoan": tai_khoan,
                "mat_khau": mat_khau,
                "gioi_han_top": str(_kep_so(cau_hinh.get("gioi_han_top"), TOP_MAC_DINH, TOP_TOI_THIEU, TOP_TOI_DA)),
                "thoi_gian_tat_mac_dinh_giay": str(_kep_so(
                    cau_hinh.get("thoi_gian_tat_mac_dinh_giay"),
                    THOI_GIAN_TAT_MAC_DINH_GIAY, THOI_GIAN_TAT_TOI_THIEU_GIAY, THOI_GIAN_TAT_TOI_DA_GIAY)),
                "chu_ky_giay": str(_kep_so(cau_hinh.get("chu_ky_giay"), CHU_KY_MAC_DINH_GIAY, 10, 3600)),
            },
        }

    async def suc_khoe(self) -> dict:
        if not self._cfg:
            return {"ok": False, "khoa": []}
        try:
            await asyncio.to_thread(
                ag.MayChu(self._cfg["host"], self._cfg["cong"],
                          self._cfg["tai_khoan"], self._cfg["mat_khau"]).kiem_tra_dang_nhap)
        except ag.AdGuardError as e:
            return {"ok": False, "khoa": [], "loi": e.thong_bao}
        return {"ok": True, "khoa": []}

    async def dung(self) -> None:
        return None

    # ── Đọc thống kê (cache theo chu kỳ) ──

    async def _lam_moi_neu_can(self, bat_buoc: bool = False) -> None:
        if not self._cfg:
            return
        if not bat_buoc and self._thong_ke is not None:
            if self._dong_ho_mono() - self._lan_doc < self._cfg["chu_ky_giay"]:
                return
        # NV187: không gọi log.* khi đang giữ khoá không tái nhập (bộ lọc log
        # toàn cục có thể chạm khoá khác); ghi lỗi SAU khi thả khoá.
        loi = None
        async with self._khoa_lam_moi:
            if not bat_buoc and self._thong_ke is not None:
                if self._dong_ho_mono() - self._lan_doc < self._cfg["chu_ky_giay"]:
                    return
            try:
                tk = await asyncio.to_thread(
                    ag.MayChu(self._cfg["host"], self._cfg["cong"],
                              self._cfg["tai_khoan"], self._cfg["mat_khau"]).doc_thong_ke,
                    self._cfg["gioi_han_top"])
            except ag.AdGuardError as e:
                self._loi = e.ma_loi
                loi = e.ma_loi
            else:
                self._thong_ke = tk
                self._loi = None
                self._lan_doc = self._dong_ho_mono()
        if loi is not None:
            log.warning("adguard: lỗi đọc thống kê (%s), giữ số cũ.", loi)

    def _luc_iso(self) -> str | None:
        if self._thong_ke is None:
            return None
        return datetime.fromtimestamp(self._dong_ho(), tz=timezone.utc).isoformat(timespec="seconds")

    def _tao_thuc_the_va_trang_thai(self) -> tuple[list[dict], dict]:
        tk = self._thong_ke
        hop_le = tk is not None
        luc = self._luc_iso()
        bao_ve = tk["bao_ve_bat"] if hop_le else None

        def cam_bien(id_, ten, gia_tri, don_vi):
            tt = {"gia_tri": gia_tri, "don_vi": don_vi, "luc": luc, "hop_le": hop_le}
            if not hop_le and self._loi:
                tt["loi"] = self._loi
            return (
                {"id": f"{self.tien_to}.{id_}", "ten": ten, "loai": "cam_bien",
                 "thuoc_tinh": {}, "tinh_nang": [], "rui_ro_mac_dinh": "doc"},
                tt,
            )

        danh_sach: list[dict] = []
        tt: dict[str, dict] = {}
        for id_, ten, gia_tri, don_vi in [
            ("tong_truy_van", "Tổng truy vấn DNS", tk["tong_truy_van"] if hop_le else None, None),
            ("so_bi_chan", "Số truy vấn bị chặn", tk["so_bi_chan"] if hop_le else None, None),
            ("ti_le_chan", "Tỉ lệ chặn", tk["ti_le_chan"] if hop_le else None, "%"),
            ("thoi_gian_xu_ly_trung_binh", "Thời gian xử lý trung bình",
             tk["thoi_gian_xu_ly_trung_binh_giay"] if hop_le else None, "giây"),
            ("so_bo_loc_dang_bat", "Số bộ lọc đang bật",
             tk["so_bo_loc_dang_bat"] if hop_le else None, None),
        ]:
            e, s = cam_bien(id_, ten, gia_tri, don_vi)
            danh_sach.append(e)
            tt[e["id"]] = s

        tt_bao_ve = {"gia_tri": ("bat" if bao_ve else "tat") if hop_le else None,
                     "don_vi": None, "luc": luc, "hop_le": hop_le}
        if not hop_le and self._loi:
            tt_bao_ve["loi"] = self._loi
        danh_sach.append({
            "id": f"{self.tien_to}.bao_ve",
            "ten": "Bảo vệ AdGuard Home",
            "loai": "cong_tac",
            "thuoc_tinh": {
                "top_mien_bi_chan": tk["top_mien_bi_chan"] if hop_le else [],
                "top_may_khach": tk["top_may_khach"] if hop_le else [],
            },
            "tinh_nang": [{
                "hanh_dong": "bao_ve",
                "mo_ta": "Bật/tắt bảo vệ AdGuard Home. Tắt có thể kèm thời gian tự bật lại "
                          "(giây, mặc định 300, trần 86400; 0 = vô thời hạn). Hành động nguy "
                          "hiểm: qua phê duyệt của lõi, có nhật ký kiểm toán.",
                "tham_so": {
                    "type": "object",
                    "required": ["bat"],
                    "additionalProperties": False,
                    "properties": {
                        "bat": {"type": "boolean",
                                "description": "true = bật bảo vệ, false = tắt bảo vệ."},
                        "thoi_gian_giay": {"type": "integer", "minimum": 0, "maximum": 86400,
                                           "description": "Chỉ dùng khi bat=false: giây tới lúc tự bật lại; "
                                                          "bỏ trống = mặc định đã cấu hình; 0 = vô thời hạn."},
                    },
                },
            }],
            "rui_ro_mac_dinh": "nguy_hiem",
        })
        tt[f"{self.tien_to}.bao_ve"] = tt_bao_ve
        return danh_sach, tt

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        if not self._cfg:
            return []
        try:
            await self._lam_moi_neu_can()
        except Exception as e:
            log.warning("adguard: lỗi khi làm mới thực thể: %s", type(e).__name__)
        danh_sach, _ = self._tao_thuc_the_va_trang_thai()
        return danh_sach

    async def trang_thai(self, ids: list[str]) -> dict:
        if not self._cfg:
            return {i: {"gia_tri": None, "don_vi": None, "luc": None, "hop_le": False} for i in (ids or [])}
        try:
            await self._lam_moi_neu_can()
        except Exception as e:
            log.warning("adguard: lỗi khi làm mới trạng thái: %s", type(e).__name__)
        _, tt = self._tao_thuc_the_va_trang_thai()
        ra: dict[str, dict] = {}
        for i in (ids or []):
            ra[i] = tt.get(i, {"gia_tri": None, "don_vi": None, "luc": None, "hop_le": False})
        return ra

    async def thuc_hien(self, id_: str, hanh_dong: str, tham_so: dict) -> dict:
        if not self._cfg:
            return {"ok": False, "loi": "Module adguard chưa cấu hình."}
        if id_ != f"{self.tien_to}.bao_ve" or hanh_dong != "bao_ve":
            return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ cho «{id_}»."}
        tham_so = tham_so or {}
        bat = tham_so.get("bat")
        if not isinstance(bat, bool):
            return {"ok": False, "loi": "Tham số «bat» phải là true/false."}
        thoi_gian = tham_so.get("thoi_gian_giay")
        if bat:
            thoi_gian_hieu = None
        elif thoi_gian is None:
            thoi_gian_hieu = self._cfg["thoi_gian_tat_mac_dinh_giay"]
        else:
            if isinstance(thoi_gian, bool) or not isinstance(thoi_gian, int):
                return {"ok": False, "loi": "Tham số «thoi_gian_giay» phải là số nguyên."}
            if not 0 <= thoi_gian <= THOI_GIAN_TAT_TOI_DA_GIAY:
                return {"ok": False, "loi": "Tham số «thoi_gian_giay» ngoài khoảng 0–86400."}
            thoi_gian_hieu = thoi_gian
        try:
            await asyncio.to_thread(
                ag.MayChu(self._cfg["host"], self._cfg["cong"],
                          self._cfg["tai_khoan"], self._cfg["mat_khau"]).dat_bao_ve,
                bat, thoi_gian_hieu)
        except ag.AdGuardError as e:
            return {"ok": False, "loi": e.thong_bao}
        # Ghi nhật ký hành động thay đổi (không mật khẩu, không header, không địa chỉ):
        # nhật ký «ai» do lõi ghi ở tầng thiet_bi; module chỉ ghi trạng thái + thời hạn.
        log.info("adguard: bao_ve bat=%s thoi_gian_giay=%s", bat, thoi_gian_hieu)
        await self._lam_moi_neu_can(bat_buoc=True)
        return {"ok": True,
                "trang_thai_moi": {"gia_tri": "bat" if bat else "tat",
                                   "don_vi": None, "luc": self._luc_iso(), "hop_le": True}}
