"""Client HTTP cho AdGuard Home (REST, xác thực Basic) — chỉ thư viện chuẩn.

API dùng (đã đối chiếu tài liệu AdGuard Home / openapi, bản v0.107.x):
- ``GET /control/status``: ``protection_enabled`` (bool).
- ``GET /control/stats``: ``num_dns_queries``, ``num_blocked_filtering``,
  ``avg_processing_time`` (giây, float), ``top_blocked_domains`` và
  ``top_clients`` dạng mảng object một khoá, vd ``[{"example.com": 42}]``.
- ``GET /control/filtering/status``: ``filters`` — mỗi bộ lọc có ``enabled``.
- ``GET /control/clients``: ``clients.auto`` / ``clients.persistent`` — mỗi mục
  có ``ip`` và ``name`` (dùng để hiển thị tên thay cho IP ở top máy khách;
  gọi này hỏng thì bỏ qua, không làm hỏng thống kê).
- ``POST /control/protection``: thân ``{"enabled": bool, "duration": ms}``.
  ``duration`` là mili-giây; tắt kèm ``duration = 0`` nghĩa là vô thời hạn
  (không tự bật lại), tắt kèm số mili-giây dương là tạm tắt rồi tự bật lại.
  Bật thì chỉ gửi ``{"enabled": true}``. Phản hồi 200 không có thân.

KHÔNG dùng ``GET /control/querylog``: top miền bị chặn và top máy khách đã
có sẵn dạng tổng hợp trong ``/control/stats``; đọc nhật ký thô là không cần
thiết và trái với yêu cầu «không trả nguyên nhật ký truy vấn» của NV278.

An toàn: không bao giờ ghi mật khẩu hay header ``Authorization`` vào log;
mọi lỗi chỉ ghi mã lỗi + mã HTTP. Opener tắt proxy hoàn toàn (địa chỉ đã
được ghim IP LAN ở lớp module, không được đi qua proxy ra ngoài).
"""

from __future__ import annotations

import base64
import json
import logging
import unicodedata
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("web2api.adguard")

THOI_GIAN_CHO_GIAY = 10
DO_DAI_CHUOI_TOI_DA = 64


class AdGuardError(Exception):
    """Lỗi AdGuard có mã rõ cho giao diện; không mang mật khẩu/header."""

    def __init__(self, ma_loi: str, thong_bao: str):
        super().__init__(thong_bao)
        self.ma_loi = ma_loi
        self.thong_bao = thong_bao


def _lam_sach_chuoi(gia_tri: Any, toi_da: int = DO_DAI_CHUOI_TOI_DA) -> str:
    """Dữ liệu từ máy chủ là KHÔNG TIN: bỏ ký tự điều khiển, cắt độ dài."""
    s = "".join(c for c in str(gia_tri or "") if unicodedata.category(c)[0] != "C")
    return s.strip()[:toi_da]


def _so_nguyen(gia_tri: Any, ten: str) -> int:
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        raise AdGuardError("du_lieu_sai", f"Trường AdGuard «{ten}» không phải số.")
    return int(gia_tri)


def _so_thuc(gia_tri: Any, ten: str) -> float:
    if isinstance(gia_tri, bool) or not isinstance(gia_tri, (int, float)):
        raise AdGuardError("du_lieu_sai", f"Trường AdGuard «{ten}» không phải số.")
    return float(gia_tri)


def _lam_phang_top(gia_tri: Any, ten: str, gioi_han: int) -> list[dict]:
    """Mảng object một khoá -> [{ten, so_lan}] đã sắp giảm dần, cắt giới hạn."""
    if not isinstance(gia_tri, list):
        raise AdGuardError("du_lieu_sai", f"Trường AdGuard «{ten}» không phải mảng.")
    muc: list[tuple[str, int]] = []
    for phan_tu in gia_tri:
        if not isinstance(phan_tu, dict) or len(phan_tu) != 1:
            continue
        (khoa, so_lan), = phan_tu.items()
        ten_sach = _lam_sach_chuoi(khoa)
        if not ten_sach:
            continue
        try:
            muc.append((ten_sach, _so_nguyen(so_lan, ten)))
        except AdGuardError:
            continue
    muc.sort(key=lambda x: x[1], reverse=True)
    return [{"ten": t, "so_lan": n} for t, n in muc[:gioi_han]]


class MayChu:
    """Một máy AdGuard Home đã ghim IP (host là IP đã kiểm, không phân giải lại)."""

    def __init__(self, host: str, cong: int, tai_khoan: str, mat_khau: str,
                 timeout: float = THOI_GIAN_CHO_GIAY):
        self.host = host
        self.cong = cong
        self.tai_khoan = tai_khoan
        self._mat_khau = mat_khau
        self.timeout = timeout
        token = base64.b64encode(f"{tai_khoan}:{mat_khau}".encode("utf-8")).decode("ascii")
        self._tieu_de = {"Authorization": f"Basic {token}"}

    def _yeu_cau(self, phuong_thuc: str, duong: str, than: dict | None = None) -> Any:
        url = f"http://{self.host}:{self.cong}{duong}"
        du_lieu = None
        tieu_de = dict(self._tieu_de)
        if than is not None:
            du_lieu = json.dumps(than).encode("utf-8")
            tieu_de["Content-Type"] = "application/json"
        yeu_cau = urllib.request.Request(url, data=du_lieu, headers=tieu_de, method=phuong_thuc)
        mo = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with mo.open(yeu_cau, timeout=self.timeout) as tra_loi:
                noi_dung = tra_loi.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AdGuardError(
                    "sai_dang_nhap",
                    "AdGuard từ chối đăng nhập (HTTP %d) — kiểm tra tài khoản/mật khẩu." % e.code,
                ) from None
            raise AdGuardError(
                "loi_may_chu", "AdGuard trả HTTP %d cho %s %s." % (e.code, phuong_thuc, duong)
            ) from None
        except TimeoutError:
            raise AdGuardError("het_gio", "Hết giờ chờ AdGuard (%s %s)." % (phuong_thuc, duong)) from None
        except OSError as e:
            raise AdGuardError(
                "mang_loi", "Không kết nối được tới AdGuard: %s." % type(e).__name__
            ) from None
        if not noi_dung:
            return None
        try:
            return json.loads(noi_dung.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise AdGuardError("du_lieu_sai", "AdGuard trả dữ liệu không phải JSON.") from None

    def kiem_tra_dang_nhap(self) -> None:
        """GET /control/status — chỉ để cai() kiểm tài khoản; bỏ thân trả về."""
        self._yeu_cau("GET", "/control/status")

    def doc_thong_ke(self, gioi_han_top: int) -> dict:
        """Đọc trạng thái + thống kê + bộ lọc + tên máy khách; không đọc querylog."""
        trang_thai = self._yeu_cau("GET", "/control/status")
        thong_ke = self._yeu_cau("GET", "/control/stats")
        loc = self._yeu_cau("GET", "/control/filtering/status")
        if not isinstance(trang_thai, dict) or not isinstance(thong_ke, dict) or not isinstance(loc, dict):
            raise AdGuardError("du_lieu_sai", "AdGuard trả dữ liệu không đúng hình dạng.")
        bao_ve = trang_thai.get("protection_enabled")
        if not isinstance(bao_ve, bool):
            raise AdGuardError("du_lieu_sai", "Trường AdGuard «protection_enabled» không phải bool.")
        tong = _so_nguyen(thong_ke.get("num_dns_queries"), "num_dns_queries")
        bi_chan = _so_nguyen(thong_ke.get("num_blocked_filtering"), "num_blocked_filtering")
        xu_ly = _so_thuc(thong_ke.get("avg_processing_time"), "avg_processing_time")
        bo_loc = loc.get("filters")
        if not isinstance(bo_loc, list):
            raise AdGuardError("du_lieu_sai", "Trường AdGuard «filters» không phải mảng.")
        so_bo_loc_bat = sum(1 for f in bo_loc if isinstance(f, dict) and f.get("enabled") is True)
        top_mien = _lam_phang_top(thong_ke.get("top_blocked_domains"), "top_blocked_domains", gioi_han_top)
        top_may = _lam_phang_top(thong_ke.get("top_clients"), "top_clients", gioi_han_top)
        # Tên máy khách (không bắt buộc): hỏng thì giữ IP làm tên hiển thị.
        ten_may: dict[str, str] = {}
        try:
            may_khach = self._yeu_cau("GET", "/control/clients")
            if isinstance(may_khach, dict) and isinstance(may_khach.get("clients"), dict):
                for nhom in ("auto", "persistent"):
                    danh_sach = may_khach["clients"].get(nhom)
                    if isinstance(danh_sach, list):
                        for muc in danh_sach:
                            if isinstance(muc, dict):
                                ip = _lam_sach_chuoi(muc.get("ip"))
                                ten = _lam_sach_chuoi(muc.get("name"))
                                if ip and ten:
                                    ten_may[ip] = ten
        except AdGuardError as e:
            log.warning("adguard: không đọc được tên máy khách (%s), giữ IP.", e.ma_loi)
        top_may_khach = [
            {"may_khach": ten_may.get(m["ten"], m["ten"]), "so_lan": m["so_lan"]} for m in top_may
        ]
        return {
            "bao_ve_bat": bao_ve,
            "tong_truy_van": tong,
            "so_bi_chan": bi_chan,
            "ti_le_chan": round((bi_chan / tong * 100.0) if tong > 0 else 0.0, 2),
            "thoi_gian_xu_ly_trung_binh_giay": xu_ly,
            "so_bo_loc_dang_bat": so_bo_loc_bat,
            "top_mien_bi_chan": [{"mien": m["ten"], "so_lan": m["so_lan"]} for m in top_mien],
            "top_may_khach": top_may_khach,
        }

    def dat_bao_ve(self, bat: bool, thoi_gian_giay: int | None) -> dict:
        """Bật/tắt bảo vệ. Tắt: thoi_gian_giay None -> mặc định do module truyền
        vào; 0 -> vô thời hạn; dương -> mili-giây gửi đi."""
        if bat:
            than = {"enabled": True}
            hieu = None
        else:
            if thoi_gian_giay is None:
                raise AdGuardError("tham_so_sai", "Tắt bảo vệ cần thời gian tự bật lại.")
            if thoi_gian_giay == 0:
                than = {"enabled": False, "duration": 0}
                hieu = 0
            else:
                than = {"enabled": False, "duration": thoi_gian_giay * 1000}
                hieu = thoi_gian_giay
        self._yeu_cau("POST", "/control/protection", than)
        return {"bat": bat, "thoi_gian_giay": hieu}
