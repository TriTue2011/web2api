"""Module van-phong — công cụ office_* thao tác tài liệu văn phòng qua container phụ.

Đọc/sửa tài liệu (docx/xlsx/pptx) bằng cách gọi dịch vụ `dich-vu/van-phong-office`
(HTTP nội bộ, OfficeCLI trong hộp cát: tập lệnh đóng, hết giờ, không mạng ra
ngoài). Công cụ `office_*` mặc định TẮT (`office_bat`); chưa bật hoặc không nối
được dịch vụ → báo «chưa bật» (đóng khi lỗi, không lộ chi tiết) và không gọi
mạng. Đầu ra dịch vụ là DỮ LIỆU KHÔNG TIN: cắt ở trần, gắn `nguon_ngoai=True`.

Thiết kế bảo mật:
- Khoá dịch vụ (Bearer) chỉ thêm vào header ở đúng lời gọi mạng; một
  `logging.Filter` (khuôn zalo-bot) chặn mọi dòng log của thư viện HTTP chứa
  khoá — KHÔNG đổi mức log toàn cục.
- Địa chỉ dịch vụ chỉ được đặt qua cấu hình; lúc `cai` kiểm bằng đúng
  `app.tu_xa.kiem_url_lan` của lõi (chặn loopback/link-local/không định tuyến —
  chống SSRF). Không có địa chỉ cố định nào khác trong mã.
- Hành động ngoài tập `office_*` bị từ chối TRƯỚC khi gọi mạng.

Chỉ dùng httpx (đã khai `yeu_cau_goi` trong manifest) + thư viện chuẩn.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

#: Trần cắt đầu ra phía module — khớp TRAN_DAU_RA của dịch vụ (80.000 ký tự).
TRAN_DAU_RA = 80_000
#: Hết giờ gọi dịch vụ: lệnh dài nhất của dịch vụ là 180 s + biên.
HET_GIO_GOI = 200

#: Công cụ đọc (rủi ro «doc») và công cụ ghi (rủi ro «ghi»), suy từ TẬP ĐÓNG
#: của dịch vụ (không liệt kê tay tên lệnh dịch vụ ở đây — ánh xạ office_X → X).
_CONG_CU_DOC = ("office_view", "office_get", "office_query",
                "office_validate", "office_dump", "office_help")
_CONG_CU_GHI = ("office_create", "office_set", "office_add", "office_remove",
                "office_move", "office_swap", "office_merge", "office_save",
                "office_import", "office_batch")

#: Khoá đang dùng — bộ lọc log chặn mọi dòng của thư viện HTTP chứa một trong các chuỗi này.
_KHOA_DANG_DUNG: set[str] = set()
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                "httpcore.http2", "httpcore.proxy")


class _ChanKhoaDichVu(logging.Filter):
    """Bỏ dòng log chứa khoá dịch vụ. Cài MỘT lần lên logger của thư viện HTTP —
    KHÔNG đổi mức log toàn cục (khuôn modules/zalo-bot).

    Chặn theo CẤU TRÚC, không theo giá trị khoá cụ thể: mọi dòng log mang mẫu
    «Authorization … Bearer» đều là dòng mang bí mật xác thực — chặn ngay cả khi
    khoá trong dòng chưa từng được nạp qua `nap` (vd test chỉ import module).
    Ngoài ra chặn mọi dòng chứa một khoá đã nạp (phòng khi khoá lọt vào URL hay
    thân thông báo khác)."""

    _van_phong_loc = True

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
        for f in [f for f in lg.filters if getattr(f, "_van_phong_loc", False)]:
            lg.removeFilter(f)  # nạp lại module: bỏ bộ lọc của bản cũ
        lg.addFilter(_ChanKhoaDichVu())


_cai_bo_loc_log()


def _bat(gia_tri) -> bool:
    """Chuẩn hoá cờ office_bat (bool hoặc chuỗi «1»/«bat»/«true»)."""
    if isinstance(gia_tri, bool):
        return gia_tri
    if isinstance(gia_tri, (int, float)):
        return gia_tri == 1
    return str(gia_tri or "").strip().lower() in ("1", "bat", "true", "yes", "on")


def _cat_dau_ra(dau_ra) -> str:
    s = dau_ra if isinstance(dau_ra, str) else json.dumps(dau_ra, ensure_ascii=False)
    if len(s) > TRAN_DAU_RA:
        s = s[:TRAN_DAU_RA] + "\n…[cat]"
    return s


def _dia_chi_bi_chan_dich_vu(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if isinstance(ip, ipaddress.IPv6Address):
        nhung = [ip.sixtofour] if ip.sixtofour else []
        if ip.teredo:
            nhung += list(ip.teredo)
        for v4 in nhung:
            ly_do = _dia_chi_bi_chan_dich_vu(v4)
            if ly_do:
                return f"IPv4 nhúng {v4}: {ly_do}"
    if ip.is_loopback:
        return None  # Dịch vụ phụ nội bộ nghe loopback (127.0.0.1/::1)
    if ip.is_private or ip.is_global:
        return None
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
        ly_do = _dia_chi_bi_chan_dich_vu(ip)
        if ly_do:
            raise ValueError(f"dich_vu_url trỏ vào {ip} — bị chặn: {ly_do}")
    return s


class Module:
    """Công cụ office_* qua dịch vụ van-phong-office (mặc định tắt)."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "van_phong")
        self._bat = False
        self._url = "http://127.0.0.1:8701"
        self._khoa = ""
        self._ban_do = {}  # (id_thuc_the, hanh_dong) → (lenh_dich_vu, rui_ro)

    # ── vòng đời ──────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        self._bat = _bat(cau_hinh.get("office_bat", False))
        url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8701").strip().rstrip("/")
        self._url = url
        khoa = str(cau_hinh.get("khoa_dich_vu") or "")
        self._khoa = khoa
        if khoa:
            _KHOA_DANG_DUNG.add(khoa)

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình trước khi lưu: địa chỉ dịch vụ qua bộ kiểm SSRF dịch vụ phụ (cho phép loopback/mạng nội bộ)."""
        cau_hinh = cau_hinh or {}
        url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8701")
        try:
            url_sach = await _kiem_url_dich_vu(url)
        except ValueError as e:
            return {"ok": False, "loi": f"dia_chi_dich_vu_khong_hop_le: {e}"}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        bat = _bat(cau_hinh.get("office_bat", False))
        khoa_che = f"…{khoa[-4:]}" if len(khoa) >= 4 else "…"
        ket_qua_tung_khoa = [{"khoa": khoa_che, "ok": True}] if khoa else []
        return {
            "ok": True,
            "loi": None,
            "ket_qua_tung_khoa": ket_qua_tung_khoa,
            "cau_hinh_luu": {
                "office_bat": "1" if bat else "0",
                "dich_vu_url": url_sach,
                "khoa_dich_vu": khoa,
            },
        }

    async def suc_khoe(self) -> dict:
        return {"ok": True, "office_bat": self._bat,
                "dich_vu_url": self._url if self._bat else None}

    async def dung(self) -> None:
        return None

    # ── thực thể / hành động ──────────────────────────────────────────────

    def _cong_cu(self, nhom: tuple[str, ...], rui_ro: str) -> list[dict]:
        return [{"hanh_dong": t, "rui_ro": rui_ro,
                 "mo_ta": f"Công cụ văn phòng «{t}» qua container phụ (dịch vụ van-phong-office)."}
                for t in nhom]

    def thuc_the(self, loc=None) -> list[dict]:
        doc = {
            "id": f"{self.tien_to}.office_doc",
            "ten": "Công cụ đọc tài liệu văn phòng (office_*)",
            "loai": "cong_cu",
            "thuoc_tinh": {"dich_vu": "van-phong-office"},
            "tinh_nang": self._cong_cu(_CONG_CU_DOC, "doc"),
            "rui_ro_mac_dinh": "doc",
        }
        ghi = {
            "id": f"{self.tien_to}.office_ghi",
            "ten": "Công cụ sửa tài liệu văn phòng (office_*)",
            "loai": "cong_cu",
            "thuoc_tinh": {"dich_vu": "van-phong-office"},
            "tinh_nang": self._cong_cu(_CONG_CU_GHI, "ghi"),
            "rui_ro_mac_dinh": "ghi",
        }
        return [doc, ghi]

    def _tra_loi_dong(self) -> dict:
        """Đóng khi lỗi: chưa bật / không nối được → cùng một thông báo chung."""
        return {"ok": False, "loi": "chưa bật"}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        tham_so = tham_so or {}
        # Hành động ngoài tập office_* → từ chối TRƯỚC khi gọi mạng.
        if not isinstance(hanh_dong, str) or not hanh_dong.startswith("office_"):
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        thuc_the = {e["id"]: e for e in self.thuc_the()}
        muc = thuc_the.get(id)
        if muc is None:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        cho_phep = {t["hanh_dong"] for t in muc["tinh_nang"]}
        if hanh_dong not in cho_phep:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        if not self._bat:
            return self._tra_loi_dong()
        lenh = hanh_dong[len("office_"):]
        doi_so = self._doi_so(tham_so)
        try:
            with httpx.Client(timeout=HET_GIO_GOI) as client:
                tra_loi = await asyncio.to_thread(
                    client.post,
                    self._url + "/lenh",
                    json={"lenh": lenh, "doi_so": doi_so},
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
        except Exception as e:
            log.warning("khong_noi_duoc_dich_vu_van_phong: %s", type(e).__name__)
            return self._tra_loi_dong()
        try:
            than = tra_loi.json()
        except Exception:
            return {"ok": False, "loi": "phan_hoi_dich_vu_sai"}
        if not isinstance(than, dict):
            return {"ok": False, "loi": "phan_hoi_dich_vu_sai"}
        dau_ra = _cat_dau_ra(than.get("dau_ra", ""))
        kq = {"ok": bool(than.get("ok", False)), "dau_ra": dau_ra,
              "nguon_ngoai": True}
        if than.get("het_gio"):
            kq["het_gio"] = True
        if not kq["ok"] and "loi" in than:
            kq["loi"] = "dich_vu_bao_loi"
        return kq

    @staticmethod
    def _doi_so(tham_so: dict) -> list:
        """Tham số lõi → doi_so[] của dịch vụ: «tep» làm phần tử đầu, «doi_so»
        (nếu có) nối tiếp. Không thêm gì khác."""
        doi_so = []
        tep = tham_so.get("tep")
        if isinstance(tep, str) and tep:
            doi_so.append(tep)
        them = tham_so.get("doi_so")
        if isinstance(them, list):
            doi_so.extend(x for x in them if isinstance(x, (str, dict)))
        return doi_so
