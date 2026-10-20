"""Module chay-ma — chạy đoạn mã Python qua dịch vụ phụ cách ly (NV171).

Module chỉ là LỚP CẦU NỐI HTTP mỏng: thực thi thật nằm ở dịch vụ riêng
(`dich-vu/chay-ma`), mỗi lượt chạy là một container docker dùng-một-lần
cách ly (--network none, rootfs chỉ đọc, user không-root, trần CPU/RAM/
thời gian/đầu ra, không mount dữ liệu lõi, không docker.sock).

Bảo mật:
- Mặc định TẮT (`chay_bat`, cùng khuôn van-phong/office của lõi): khi tắt
  hoặc không nối được dịch vụ → báo «chưa bật» (đóng khi lỗi, không lộ chi
  tiết) và KHÔNG gọi mạng. Hành động trên thực thể `chay_ma.chay` mang rủi
  ro `nguy_hiem` nên lõi bắt cổng phê duyệt SEC-004f — mô hình không tự gọi.
- URL dịch vụ qua ĐÚNG bộ kiểm của lõi `app/tu_xa.py` (NV171-SUA C1):
  phân giải host rồi kiểm TỪNG IP bằng `_dia_chi_bi_chan` (bản cũ tự viết
  bộ lọc theo hình dạng host nên lọt host dạng số nguyên/hex trỏ vào
  169.254.169.254); lúc gọi ghim IP bằng `TransportGhimIp` chống DNS
  rebinding. Ngoại lệ có chủ đích duy nhất: loopback ĐƯỢC phép vì dịch vụ
  là tiến trình cục bộ do quản trị cấu hình (mặc định 127.0.0.1:8703) —
  chi tiết ở `_kiem_ip_dich_vu`.
- Mã và đầu ra là DỮ LIỆU KHÔNG TIN (nguon_ngoai=True): module cắt ở trần
  TRAN_DAU_RA rồi mới trả cho lõi, không diễn giải thành chỉ thị nào.
- Khoá dịch vụ riêng (khác khoá quản trị/khoá khách); module import cài
  logging.Filter theo khuôn van-phong để khoá không bao giờ vào log.

Khung theo chuẩn CHUAN-MODULE.md và khuôn modules/van-phong của lõi.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

from app.chuyen_tiep import TransportGhimIp
from app.tu_xa import _dia_chi_bi_chan

log = logging.getLogger("module.chay_ma")

# Trần đầu ra của một lượt chạy (ký tự) — lõi còn kiểm tổng ở tầng công cụ.
TRAN_DAU_RA = 20_000
TRAN_CHAN_DOAN = 2_000
# Trần thời gian gọi sang dịch vụ: han_giay tối đa 60s + khởi động container.
HET_GIO_GOI = 90.0
_MA_LOI_CODE = re.compile(r"^[a-z0-9_]{1,80}$")

# Sổ khoá đang dùng là CHUNG cho cả tiến trình (neo trên module logging):
# lõi có thể nạp module nhiều lần (kiểm tra, nạp lại) — mọi bản sao phải tra
# cùng một sổ, nếu không bộ lọc của bản sao cũ sẽ chặn «hộ» sai tập khoá.
# Chú ý: phải dùng `is None`, không dùng `or`, vì set RỖNG là falsy.
_so_chung = getattr(logging, "_chay_ma_khoa_chung", None)
if _so_chung is None:
    _so_chung = set()
    logging._chay_ma_khoa_chung = _so_chung  # type: ignore[attr-defined]
_KHOA_DANG_DUNG: set[str] = _so_chung


class _BoLocAnKhoa(logging.Filter):
    """Chặn mọi bản ghi log chứa khoá dịch vụ đang dùng (khuôn van-phong)."""

    _chay_ma_loc = True

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            noi_dung = record.getMessage()
        except Exception:
            return True
        for khoa in _KHOA_DANG_DUNG:
            if khoa and khoa in noi_dung:
                return False
        return True


def _cai_loc_log() -> None:
    for ten_logger in (None, "httpx"):
        lg = logging.getLogger(ten_logger)
        if not any(getattr(f, "_chay_ma_loc", False) for f in lg.filters):
            lg.addFilter(_BoLocAnKhoa())


_cai_loc_log()


def _bat(gia_tri) -> bool:
    return str(gia_tri).strip().lower() in ("1", "true", "yes", "on", "bat", "bật")


def _kiem_ip_dich_vu(ip) -> str | None:
    """Chính sách IP cho địa chỉ DỊCH VỤ chay-ma — dùng ĐÚNG bộ kiểm của
    lõi `app.tu_xa._dia_chi_bi_chan` (link-local/metadata, địa chỉ dành
    riêng, multicast, IPv4 nhúng trong 6to4/Teredo đều bị chặn như lõi),
    cộng hai điều chỉnh theo ngữ cảnh dịch vụ cục bộ:

    - Loopback ĐƯỢC phép (ngoại lệ CÓ CHỦ ĐÍCH): dịch vụ chay-ma là tiến
      trình CỤC BỘ do QUẢN TRỊ cấu hình, mặc định chính là
      http://127.0.0.1:8703 — `_dia_chi_bi_chan` chặn loopback vì nó viết
      cho module từ xa (trỏ vào chính container lõi). Ngoại lệ này do cấu
      hình quản trị đặt (trường dich_vu_url), không phải người gọi quyết.
    - Ngược lại CHẶT hơn lõi ở đầu kia: chỉ loopback hoặc mạng riêng
      (RFC1918/ULA) — dịch vụ thực thi mã không bao giờ nằm trên Internet
      công cộng, chặn luôn để khoá Bearer không đi ra ngoài nhà.
    Trả None nếu được phép, chuỗi lý do nếu bị chặn."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return None
    ly_do = _dia_chi_bi_chan(ip)
    if ly_do:
        return ly_do
    if not ip.is_private:
        return "chỉ chấp nhận địa chỉ loopback hoặc mạng riêng (RFC1918/ULA)"
    return None


def _kiem_url_dich_vu(url: str) -> tuple[str, str | None]:
    """Kiểm URL dịch vụ (NV171-SUA C1): kiểm cấu trúc, PHÂN GIẢI host rồi
    kiểm TỪNG IP phân giải bằng `_kiem_ip_dich_vu`.

    Bản cũ kiểm theo HÌNH DẠNG host: host không chứa «.»/«:» chỉ cần
    getaddrinfo thành công là qua — mà getaddrinfo/inet_aton hiểu host
    dạng số nguyên/hex/bát phân (`http://2852039166`, `http://0xa9fea9fe`)
    thành 169.254.169.254, nên khoá Bearer bị gửi tới metadata đám mây.
    Nay phân giải bằng chính getaddrinfo của hệ thống (hiểu mọi dạng như
    tầng kết nối) và soi IP thật; lúc GỌI, `TransportGhimIp` phân giải +
    kiểm + ghim lại nên DNS đổi giữa hai thời điểm cũng không lách được.
    Trả (url_sạch, lỗi|None)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return "", "thiếu dich_vu_url"
    if any(ord(c) < 32 for c in u) or "\\" in u:
        return "", "dich_vu_url chứa ký tự điều khiển hoặc dấu gạch ngược"
    parts = urlparse(u)
    if parts.scheme not in ("http", "https"):
        return "", "dich_vu_url phải là http hoặc https"
    if parts.username or parts.password:
        return "", "dich_vu_url không được chứa thông tin đăng nhập"
    host = parts.hostname
    if not host:
        return "", "dich_vu_url thiếu tên máy"
    try:
        cong = parts.port or 0
    except ValueError:
        return "", "dich_vu_url có cổng không hợp lệ"
    try:
        thong_tin = socket.getaddrinfo(host, cong, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        return "", f"Không phân giải được tên máy dịch vụ: {exc}"
    ds_ip = {x[4][0] for x in thong_tin}
    if not ds_ip:
        return "", "Không phân giải được tên máy dịch vụ"
    for chuoi_ip in ds_ip:
        try:
            ip = ipaddress.ip_address(chuoi_ip)
        except ValueError:
            return "", "dich_vu_url phải là địa chỉ cùng máy hoặc mạng nội bộ"
        ly_do = _kiem_ip_dich_vu(ip)
        if ly_do:
            return "", f"dich_vu_url trỏ vào {chuoi_ip} — bị chặn: {ly_do}"
    return u, None


def _cat(chuoi: str, tran: int) -> str:
    s = str(chuoi or "")
    if len(s) > tran:
        return s[:tran] + "…[cat]"
    return s


class Module:
    """Module lõi hạng B: cầu nối tới dịch vụ chay-ma (container cách ly)."""

    def __init__(self, manifest: dict):
        self._manifest = manifest
        self._bat = False
        self._url = ""
        self._loi_url: str | None = None
        self._khoa = ""

    # ── Nạp cấu hình (lõi gọi sau cai()/khoi_dong) ──────────────────────
    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = dict(cau_hinh or {})
        self._bat = _bat(cau_hinh.get("chay_bat"))
        # C1: kiểm lại URL NGAY Ở nap() — không chỉ lúc cai(): cấu hình nạp
        # từ đĩa (bản phục vụ) cũng phải qua cùng một bộ kiểm, URL bị chặn
        # thì hành động từ chối thẳng, không gọi mạng.
        tho_url = str(cau_hinh.get("dich_vu_url") or "")
        if tho_url:
            self._url, self._loi_url = _kiem_url_dich_vu(tho_url)
        else:
            self._url, self._loi_url = "", None
        self._khoa = str(cau_hinh.get("khoa_dich_vu") or "")
        if self._khoa:
            _KHOA_DANG_DUNG.add(self._khoa)
        log.info("chay-ma: nạp cấu hình, chay_bat=%s", self._bat)

    # ── Khai báo thực thể/hành động ──────────────────────────────────────
    def thuc_the(self) -> list[dict]:
        return [
            {
                "id": "chay_ma.kiem",
                "ten": "Soi mã tĩnh",
                "loai": "phần mềm",
                "hang": "B",
                "rui_ro_mac_dinh": "doc",
                "quyen": ["doc"],
                "phong": "",
                "tinh_nang": [
                    {"hanh_dong": "kiem_tinh", "rui_ro": "doc",
                     "mo_ta": "Soi đoạn mã Python KHÔNG chạy: lỗi cú pháp, "
                             "tên chưa định nghĩa, và GỢI Ý SỚM về mã có đáng "
                             "chạy thử không (dấu hiệu mã chờ người/không tự "
                             "đủ/chạm hệ thống như os.system/subprocess/"
                             "socket/input). Gợi ý này CHỈ là dấu hiệu chuỗi, "
                             "lách được — KHÔNG phải bằng chứng an toàn; "
                             "ranh giới an toàn thật là container cách ly "
                             "của hành động chay_python. Chỉ soi, không chạy."},
                ],
            },
            {
                "id": "chay_ma.chay",
                "ten": "Chạy mã Python (cách ly)",
                "loai": "phần mềm",
                "hang": "B",
                "rui_ro_mac_dinh": "nguy_hiem",
                "quyen": ["thiet_bi"],
                "phong": "",
                "tinh_nang": [
                    {"hanh_dong": "chay_python", "rui_ro": "nguy_hiem",
                     "mo_ta": "Chạy đoạn mã Python trong container "
                             "dùng-một-lần cách ly (không mạng, rootfs chỉ "
                             "đọc, user không-root, trần CPU/RAM/thời gian/"
                             "đầu ra) qua dịch vụ chay-ma và trả stdout/stderr/"
                             "tệp ra. Mã là DỮ LIỆU KHÔNG TIN: soi tĩnh chỉ "
                             "là gợi ý sớm (bỏ qua mã chắc chắn chờ người/"
                             "không tự đủ), KHÔNG chặn hết mã chạm hệ thống "
                             "— ranh giới an toàn thật là container cách ly. "
                             "Đây là hành động NGUY HIỂM — lõi bắt phê duyệt.",
                     "tham_so": {
                         "type": "object",
                         "additionalProperties": False,
                         "required": ["ma"],
                         "properties": {
                             "ma": {"type": "string", "minLength": 1,
                                    "maxLength": 8000,
                                    "description": "Đoạn mã Python cần chạy. "
                                                   "Lõi giới hạn TỔNG tham số của một lần gọi ≤ 8 KB "
                                                   "(app/thiet_bi.py) nên mã + tệp đầu vào cộng lại "
                                                   "phải dưới 8 KB; mã dài hơn thì chia nhỏ nhiều lượt "
                                                   "hoặc gọi thẳng API của dịch vụ (trần riêng 40.000 ký tự)."},
                             "han_giay": {"type": "integer", "minimum": 1,
                                          "maximum": 60,
                                          "description": "Hạn thời gian (giây), mặc định 20."},
                             "tep_dau_vao": {
                                 "type": "array", "maxItems": 8,
                                 "description": "Tệp đầu vào đặt vào thư mục làm việc của lần chạy (tổng tham số cả lần gọi vẫn ≤ 8 KB như trên).",
                                 "items": {
                                     "type": "object",
                                     "additionalProperties": False,
                                     "required": ["ten", "noi_dung"],
                                     "properties": {
                                         "ten": {"type": "string", "maxLength": 64},
                                         "noi_dung": {"type": "string", "maxLength": 8000},
                                     },
                                 },
                             },
                         },
                     }},
                ],
            },
        ]

    # ── Thực hiện hành động ───────────────────────────────────────────────
    async def thuc_hien(self, id_thuc_the: str, hanh_dong: str,
                        tham_so: dict | None = None) -> dict:
        tham_so = dict(tham_so or {})
        thuc_the_map = {e["id"]: e for e in self.thuc_the()}
        muc = thuc_the_map.get(id_thuc_the)
        if muc is None:
            return {"ok": False, "loi": "khong_thay_thuc_the"}
        ten_hop_le = {t.get("hanh_dong") for t in muc.get("tinh_nang", [])}
        if hanh_dong not in ten_hop_le:
            return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
        if not self._bat:
            return {"ok": False,
                    "loi": "module chay-ma chưa bật (chay_bat) — cần quản trị "
                           "bật trong Cài đặt và đặt khoá dịch vụ"}
        if self._loi_url:
            return {"ok": False,
                    "loi": "dich_vu_url không hợp lệ hoặc bị chặn (SSRF) — "
                           "quản trị kiểm lại cấu hình module"}

        ma = str(tham_so.get("ma") or "")
        if hanh_dong == "kiem_tinh":
            duong = "/kiem-tinh"
            than_gui = {"ma": ma}
        else:
            duong = "/chay"
            than_gui = {"ma": ma}
            if tham_so.get("han_giay") is not None:
                than_gui["han_giay"] = tham_so.get("han_giay")
            if tham_so.get("tep_dau_vao") is not None:
                than_gui["tep_dau_vao"] = tham_so.get("tep_dau_vao")
        try:
            ket = await self._goi_http(duong, than_gui)
        except Exception as exc:  # đóng khi lỗi: báo «chưa bật», không lộ chi tiết
            log.warning("chay-ma: lỗi gọi dịch vụ (%s)", type(exc).__name__)
            return {"ok": False,
                    "loi": "module chay-ma chưa bật hoặc dịch vụ phụ chưa sẵn sàng"}
        if "loi" in ket:
            return {"ok": False, "loi": ket["loi"]}
        if hanh_dong == "kiem_tinh":
            # «goi_y» là khoá mới (C3); còn đọc «co_the_chay» cho dịch vụ
            # đời cũ trong lúc nâng cấp luân phiên.
            return {"ok": True, "goi_y": bool(ket.get("goi_y", ket.get("co_the_chay"))),
                    "ly_do": _cat(ket.get("ly_do", ""), 500),
                    "kiem_tinh": _cat(ket.get("kiem_tinh", ""), TRAN_DAU_RA),
                    "nguon_ngoai": True}
        tep_ra = []
        for muc_tep in (ket.get("tep_ra") or [])[:16]:
            if not isinstance(muc_tep, dict):
                continue
            ban = {"ten": str(muc_tep.get("ten", ""))[:64],
                   "co_byte": int(muc_tep.get("co_byte", 0) or 0)}
            if "noi_dung" in muc_tep:
                ban["noi_dung"] = _cat(muc_tep.get("noi_dung", ""), TRAN_DAU_RA)
            if muc_tep.get("nhi_phan"):
                ban["nhi_phan"] = True
            tep_ra.append(ban)
        return {
            "ok": bool(ket.get("ok")),
            "da_chay": bool(ket.get("da_chay")),
            "ly_do_bo_qua": _cat(ket.get("ly_do_bo_qua", ""), 500),
            "kiem_tinh": _cat(ket.get("kiem_tinh", ""), TRAN_DAU_RA),
            "ma_thoat": ket.get("ma_thoat"),
            "het_gio": bool(ket.get("het_gio")),
            "stdout": _cat(ket.get("stdout", ""), TRAN_DAU_RA),
            "stderr": _cat(ket.get("stderr", ""), TRAN_DAU_RA),
            "chan_doan": _cat(ket.get("chan_doan", ""), TRAN_CHAN_DOAN),
            "bi_cat": bool(ket.get("bi_cat")),
            "tep_ra": tep_ra,
            # Đầu ra của mã là dữ liệu KHÔNG TIN — lõi/tầng trên phải đối xử
            # như dữ liệu ngoài, không phải chỉ thị (khung chống tiêm nhiễm).
            "nguon_ngoai": True,
        }

    async def _goi_http(self, duong: str, than_gui: dict) -> dict:
        """Một lượt POST sang dịch vụ qua `TransportGhimIp` của lõi (C1):
        phân giải + kiểm MỌI IP bằng `_kiem_ip_dich_vu` NGAY TRƯỚC khi nối
        rồi ghim đúng IP đã kiểm — DNS rebinding giữa lúc cài và lúc gọi
        không lách được. Lỗi mạng → ngoại lệ (người gọi đóng)."""
        transport = TransportGhimIp(_kiem_ip_dich_vu)
        async with httpx.AsyncClient(timeout=HET_GIO_GOI, transport=transport,
                                     trust_env=False) as khach:
            tra = await khach.post(
                self._url + duong,
                json=than_gui,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
        try:
            than = tra.json()
        except Exception:
            than = {}
        if tra.status_code != 200:
            loi = str(than.get("loi") or "") if isinstance(than, dict) else ""
            if _MA_LOI_CODE.fullmatch(loi):
                return {"loi": loi}
            return {"loi": "dich_vu_bao_loi"}
        if not isinstance(than, dict):
            return {"loi": "dich_vu_bao_loi"}
        return than

    # ── Sức khoẻ ──────────────────────────────────────────────────────────
    async def suc_khoe(self) -> dict:
        return {"ok": True, "chay_bat": self._bat,
                "dich_vu_url": self._url if self._bat else None}

    async def dung(self) -> None:
        return None

    # ── Cài đặt từ quản trị viên ──────────────────────────────────────────
    async def cai(self, cau_hinh: dict) -> dict:
        cau_hinh = dict(cau_hinh or {})
        bat = _bat(cau_hinh.get("chay_bat"))
        url, loi_url = _kiem_url_dich_vu(
            str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8703"))
        if loi_url:
            return {"ok": False, "loi": loi_url}
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        if khoa and (len(khoa) < 24 or len(set(khoa)) < 10):
            return {"ok": False,
                    "loi": "khoa_dich_vu phải dài ít nhất 24 ký tự và có ít "
                           "nhất 10 ký tự khác nhau"}
        if bat and not khoa:
            return {"ok": False,
                    "loi": "bật chạy mã thì phải có khoa_dich_vu — sinh bằng "
                           "openssl rand -hex 32 và đặt giống CHAYMA_KHOA "
                           "của dịch vụ"}
        ket_qua_tung_khoa = []
        if khoa:
            ket_qua_tung_khoa.append({"khoa": f"…{khoa[-4:]}", "ok": True})
        return {
            "ok": True,
            "ket_qua_tung_khoa": ket_qua_tung_khoa,
            "cau_hinh_luu": {
                "chay_bat": "1" if bat else "0",
                "dich_vu_url": url,
                "khoa_dich_vu": khoa,
            },
        }
