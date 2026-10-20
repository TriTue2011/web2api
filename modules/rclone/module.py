"""Module rclone — cầu nối lưu trữ đám mây qua dịch vụ phụ.

Nguồn port (LUẬT PORT, chủ máy 19/10): /opt/claude-c2a/chatgpt2api @ d879b03
  services/rclone_service.py + api/rclone.py — giữ nguyên ý ba ràng buộc an
  toàn của c2a (đường cục bộ khoá bằng resolve(); không bao giờ qua shell;
  tệp cấu hình 0600 tách khỏi log/phản hồi); khác c2a: nhị phân rclone chạy
  trong container phụ riêng (dich-vu/rclone), không nhúng vào ảnh lõi.

Phạm vi: liệt kê, đọc, tải về, tải lên, xoá, chuyển tệp trên 150+ backend của
rclone (Google Drive, S3/R2, OneDrive, WebDAV, SFTP…) qua HTTP nội bộ tới dịch
vụ. Cấu hình rclone (token R2/S3/Drive) nằm ở dịch vụ, KHÔNG qua module.

Thiết kế bảo mật:
- Khoá dịch vụ (Bearer) chỉ thêm vào header ở đúng lời gọi mạng; một
  `logging.Filter` (khuôn zalo-bot) chặn mọi dòng log của thư viện HTTP chứa
  khoá — KHÔNG đổi mức log toàn cục.
- Địa chỉ dịch vụ chỉ được đặt qua cấu hình; lúc `cai` kiểm bằng bộ kiểm SSRF
  (cho phép loopback/mạng nội bộ — dịch vụ phụ nội bộ; chặn link-local, dải
  không định tuyến). Không có địa chỉ cố định nào khác trong mã.
- Hành động ngoài tập của hai thực thể bị từ chối TRƯỚC khi gọi mạng.
- Hành động GHI/XOÁ (gui_len, chuyen_tep, xoa) mặc định TẮT (`ghi_bat`): tắt thì
  báo «chua_bat_ghi», không gọi mạng — chống ghi/xoá nhầm khi chưa bật.
- Dữ liệu từ đám mây là KHÔNG TIN: đường dẫn qua dịch vụ kiểm (resolve dưới
  thư mục làm việc); nội dung file trả về cắt ở trần.

Chỉ dùng httpx (đã khai `yeu_cau_goi` trong manifest) + thư viện chuẩn.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

#: Trần byte nhận khi tải tệp qua API (khớp dịch vụ: 25 MB).
TRAN_TAI_VE = 25 * 1024 * 1024
#: Hết giờ gọi dịch vụ: lệnh truyền dài nhất của dịch vụ là 600 s + biên.
HET_GIO_GOI = 650

#: Công cụ đọc (rủi ro «doc»): chỉ đọc dữ liệu.
_CONG_CU_DOC = ("liet_ke", "doc_chu", "tai_ve",
                "remotes", "kiem_tra", "config_xem", "drive_start")
#: Công cụ ghi (rủi ro «ghi»): đổi trạng thái đám mây, cần ghi_bat.
_CONG_CU_GHI = ("gui_len", "chuyen_tep")
#: Công cụ nguy hiểm (rủi ro «nguy_hiem»): cần ghi_bat + xác nhận quản trị.
_CONG_CU_NGUY_HIEM = ("xoa", "them_remote", "xoa_remote",
                      "config_ghi", "drive_exchange", "luu_khoa_json")

#: Khoá đang dùng — bộ lọc log chặn mọi dòng của thư viện HTTP chứa một trong các chuỗi này.
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy")


class _ChanKhoaDichVu(logging.Filter):
    """Bỏ dòng log chứa khoá dịch vụ. Cài MỘT lần lên logger của thư viện HTTP —
    KHÔNG đổi mức log toàn cục (khuôn modules/zalo-bot).

    Chặn theo CẤU TRÚC, không theo giá trị khoá cụ thể: mọi dòng log mang mẫu
    «Authorization … Bearer» đều là dòng mang bí mật xác thực."""

    _rclone_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return False
        thap = noi_dung.lower()
        if "authorization" in thap and "bearer" in thap:
            return False
        return not any(k in noi_dung for k in _KHOA_DANG_DUNG)


def _cai_bo_loc_log() -> None:
    for ten in _LOGGER_HTTP:
        lg = logging.getLogger(ten)
        for f in [f for f in lg.filters if getattr(f, "_rclone_loc", False)]:
            lg.removeFilter(f)  # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanKhoaDichVu())


_cai_bo_loc_log()


def _bat(gia_tri) -> bool:
    if isinstance(gia_tri, bool):
        return gia_tri
    if isinstance(gia_tri, (int, float)):
        return gia_tri == 1
    return str(gia_tri or "").strip().lower() in ("1", "bat", "true", "yes", "on")


def _dia_chi_bi_chan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if ip.is_loopback:
        return None  # Dịch vụ phụ nội bộ nghe loopback
    if ip.is_private or ip.is_global:
        return None  # mạng nội bộ Docker / máy chủ
    return "dải địa chỉ đặc biệt"


async def _kiem_url_dich_vu(url: str) -> str:
    s = str(url or "").strip().rstrip("/")
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError("dich_vu_url phải dạng http(s)://<máy>[:cổng]")
    if phan.username or phan.password:
        raise ValueError("dich_vu_url không được chứa tài khoản/mật khẩu")
    try:
        ds = [ipaddress.ip_address(phan.hostname)]
    except ValueError:
        try:
            thong_tin = await asyncio.get_running_loop().getaddrinfo(
                phan.hostname, phan.port or 0, type=socket.SOCK_STREAM
            )
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {phan.hostname!r}: {e}") from None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        ly_do = _dia_chi_bi_chan(ip)
        if ly_do:
            raise ValueError(f"dich_vu_url trỏ vào {ip} — bị chặn: {ly_do}")
    return s


class Module:
    """Cầu nối rclone: liệt kê/đọc/tải về (mặc định) + tải lên/xoá (cần ghi_bat)."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "rclone")
        self._url = "http://127.0.0.1:8702"
        self._khoa = ""
        self._ghi_bat = False

    # ── vòng đời ──────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        self._ghi_bat = _bat(cau_hinh.get("ghi_bat", False))
        url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8702").strip().rstrip("/")
        self._url = url
        khoa = str(cau_hinh.get("khoa_dich_vu") or "")
        self._khoa = khoa
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình trước khi lưu: địa chỉ dịch vụ + khoá đủ dài."""
        cau_hinh = cau_hinh or {}
        url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8702")
        try:
            url_sach = await _kiem_url_dich_vu(url)
        except ValueError as e:
            return {"ok": False, "loi": f"dia_chi_dich_vu_khong_hop_le: {e}"}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        if len(khoa) < 24:
            return {"ok": False, "loi": "khoa_dich_vu_qua_ngan (phải ≥ 24 ký tự)"}
        ghi_bat = _bat(cau_hinh.get("ghi_bat", False))
        khoa_che = f"…{khoa[-4:]}" if len(khoa) >= 4 else "…"
        return {
            "ok": True,
            "loi": None,
            "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": True}],
            "cau_hinh_luu": {
                "dich_vu_url": url_sach,
                "khoa_dich_vu": khoa,
                "ghi_bat": "1" if ghi_bat else "0",
            },
        }

    async def suc_khoe(self) -> dict:
        return {"ok": True, "ghi_bat": self._ghi_bat,
                "dich_vu_url": self._url}

    async def dung(self) -> None:
        return None

    # ── thực thể / hành động ──────────────────────────────────────────────

    def _cong_cu(self, nhom: tuple[str, ...], rui_ro: str) -> list[dict]:
        return [{"hanh_dong": t, "rui_ro": rui_ro,
                 "mo_ta": f"Cầu nối rclone «{t}» qua container phụ (dịch vụ dich-vu/rclone)."}
                for t in nhom]

    def thuc_the(self, loc=None) -> list[dict]:
        doc = {
            "id": f"{self.tien_to}.kho",
            "ten": "Kho lưu trữ đám mây (rclone) — đọc/tải về",
            "loai": "luu_tru",
            "thuoc_tinh": {"dich_vu": "dich-vu/rclone"},
            "tinh_nang": self._cong_cu(_CONG_CU_DOC, "doc"),
            "rui_ro_mac_dinh": "doc",
        }
        ghi = {
            "id": f"{self.tien_to}.ghi",
            "ten": "Kho lưu trữ đám mây (rclone) — tải lên/xoá (cần ghi_bat)",
            "loai": "luu_tru",
            "thuoc_tinh": {"dich_vu": "dich-vu/rclone"},
            "tinh_nang": self._cong_cu(_CONG_CU_GHI, "ghi")
            + self._cong_cu(_CONG_CU_NGUY_HIEM, "nguy_hiem"),
            "rui_ro_mac_dinh": "ghi",
        }
        return [doc, ghi]

    def _tra_loi_dong(self) -> dict:
        return {"ok": False, "loi": "dich_vu_khong_noi_duoc"}

    async def _goi_dich_vu(self, lenh: str, tham_so: dict) -> dict:
        """POST /lenh."""
        try:
            with httpx.Client(timeout=HET_GIO_GOI) as client:
                tra_loi = await asyncio.to_thread(
                    client.post,
                    self._url + "/lenh",
                    json={"lenh": lenh, "tham_so": tham_so},
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
        except Exception as e:
            log.warning("khong_noi_duoc_dich_vu_rclone: %s", type(e).__name__)
            return self._tra_loi_dong()
        try:
            than = tra_loi.json()
        except Exception:
            return {"ok": False, "loi": "phan_hoi_dich_vu_sai"}
        if not isinstance(than, dict):
            return {"ok": False, "loi": "phan_hoi_dich_vu_sai"}
        return than

    async def _tai_byte_dich_vu(self, id_tep: str) -> bytes | None:
        """GET /tep-xuong/<id> — lấy byte đã tải về từ đám mây."""
        try:
            with httpx.Client(timeout=HET_GIO_GOI) as client:
                tra_loi = await asyncio.to_thread(
                    client.get,
                    self._url + "/tep-xuong/" + id_tep,
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
            if tra_loi.status_code != 200:
                return None
            return tra_loi.content
        except Exception as e:
            log.warning("khong_tai_duoc_tep_rclone: %s", type(e).__name__)
            return None

    async def _day_byte_len_dich_vu(self, du_lieu: bytes) -> str | None:
        """POST /tep-len — đẩy byte lên dịch vụ trước khi gui_len."""
        try:
            with httpx.Client(timeout=HET_GIO_GOI) as client:
                tra_loi = await asyncio.to_thread(
                    client.post,
                    self._url + "/tep-len",
                    content=du_lieu,
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
            than = tra_loi.json()
            if than.get("ok"):
                return than.get("id_tep")
            return None
        except Exception as e:
            log.warning("khong_day_duoc_tep_rclone: %s", type(e).__name__)
            return None

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        if not isinstance(hanh_dong, str):
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        thuc_the = {e["id"]: e for e in self.thuc_the()}
        muc = thuc_the.get(id)
        if muc is None:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        # Hành động ngoài tập của thực thể → từ chối TRƯỚC khi gọi mạng.
        bang_rui_ro = {}
        for t in muc["tinh_nang"]:
            bang_rui_ro[t["hanh_dong"]] = t["rui_ro"]
        if hanh_dong not in bang_rui_ro:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        rui_ro = bang_rui_ro[hanh_dong]
        # Hành động GHI/NGUY HIỂM cần ghi_bat bật (mặc định tắt).
        if rui_ro in ("ghi", "nguy_hiem") and not self._ghi_bat:
            return {"ok": False, "loi": "chua_bat_ghi"}

        def _chu(k):
            v = tham_so.get(k)
            return str(v) if v is not None else ""

        # ── hành động đọc ──
        if hanh_dong == "liet_ke":
            return await self._goi_dich_vu("liet_ke", {"duong_dan": _chu("duong_dan")})
        if hanh_dong == "doc_chu":
            return await self._goi_dich_vu("doc_chu", {"duong_dan": _chu("duong_dan")})
        if hanh_dong == "tai_ve":
            kq = await self._goi_dich_vu("tai_ve", {"duong_dan": _chu("duong_dan")})
            if not kq.get("ok"):
                return kq
            id_tep = kq.get("id_tep")
            byte = await self._tai_byte_dich_vu(id_tep) if id_tep else None
            if byte is None:
                return {"ok": False, "loi": "dich_vu_khong_noi_duoc"}
            if len(byte) > TRAN_TAI_VE:
                return {"ok": False, "loi": "tep_qua_lon"}
            return {"ok": True, "ten": kq.get("ten"), "co": len(byte),
                    "du_lieu_b64": base64.b64encode(byte).decode("ascii")}
        if hanh_dong == "remotes":
            return await self._goi_dich_vu("remotes", {})
        if hanh_dong == "kiem_tra":
            return await self._goi_dich_vu("kiem_tra", {"ten": _chu("ten")})
        if hanh_dong == "config_xem":
            return await self._goi_dich_vu("config_xem", {})
        if hanh_dong == "drive_start":
            return await self._goi_dich_vu("drive_start", {"client_id": _chu("client_id"),
                                                     "scope": _chu("scope") or "drive"})

        # ── hành động ghi / nguy hiểm ──
        if hanh_dong == "gui_len":
            du_lieu = tham_so.get("du_lieu_b64")
            if not isinstance(du_lieu, str) or not du_lieu:
                return {"ok": False, "loi": "thieu_du_lieu"}
            try:
                byte = base64.b64decode(du_lieu, validate=True)
            except Exception:
                return {"ok": False, "loi": "du_lieu_sai"}
            if len(byte) > TRAN_TAI_VE:
                return {"ok": False, "loi": "tep_qua_lon"}
            id_tep = await self._day_byte_len_dich_vu(byte)
            if not id_tep:
                return {"ok": False, "loi": "dich_vu_khong_noi_duoc"}
            return await self._goi_dich_vu("gui_len", {"id_tep": id_tep,
                                                       "thu_muc": _chu("thu_muc"),
                                                       "ten": _chu("ten")})
        if hanh_dong == "chuyen_tep":
            return await self._goi_dich_vu("chuyen_tep", {"nguon": _chu("nguon"),
                                                   "dich": _chu("dich")})
        if hanh_dong == "xoa":
            return await self._goi_dich_vu("xoa", {"duong_dan": _chu("duong_dan")})
        if hanh_dong == "them_remote":
            return await self._goi_dich_vu("them_remote",
                                     {"ten": _chu("ten"), "loai": _chu("loai"),
                                      "tham_so": tham_so.get("tham_so") or {}})
        if hanh_dong == "xoa_remote":
            return await self._goi_dich_vu("xoa_remote", {"ten": _chu("ten")})
        if hanh_dong == "config_ghi":
            return await self._goi_dich_vu("config_ghi", {"noi_dung": _chu("noi_dung")})
        if hanh_dong == "drive_exchange":
            return await self._goi_dich_vu("drive_exchange",
                                     {"ten": _chu("ten"), "client_id": _chu("client_id"),
                                      "client_secret": _chu("client_secret"),
                                      "redirect_url": _chu("redirect_url"),
                                      "scope": _chu("scope") or "drive",
                                      "root_folder_id": _chu("root_folder_id")})
        if hanh_dong == "luu_khoa_json":
            return await self._goi_dich_vu("luu_khoa_json",
                                     {"ten": _chu("ten"), "noi_dung": _chu("noi_dung")})
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
