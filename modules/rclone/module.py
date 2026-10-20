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
- Địa chỉ dịch vụ chỉ được đặt qua cấu hình; kiểm bằng bộ kiểm của lõi
  (`app/tu_xa.py`) nới đúng một đầu — loopback ĐƯỢC phép vì dịch vụ phụ nghe
  loopback — và siết đầu còn lại: địa chỉ CÔNG KHAI bị chặn (dịch vụ chỉ ở
  loopback/mạng nội bộ). Kiểm ở cả `cai()` (bất đồng bộ) lẫn `nap()` (đồng
  bộ): URL xấu thì module từ chối gọi mạng. Không có địa chỉ cố định nào
  khác trong mã.
- BỐN thực thể, mỗi thực thể MỘT mức rủi ro (lõi tính rủi ro theo THỰC THỂ):
  `rclone.doc` (doc), `rclone.ghi` (ghi), `rclone.xoa` và `rclone.cau_hinh`
  (nguy_hiem — luôn đòi xác nhận của lõi, kể cả sau khi thực thể đã được
  xác nhận một lần). Hành động ngoài tập của thực thể bị từ chối TRƯỚC khi
  gọi mạng.
- Hành động GHI/XOÁ mặc định TẮT (`ghi_bat`): tắt thì báo «chua_bat_ghi»,
  không gọi mạng — chống ghi/xoá nhầm khi chưa bật.
- `gui_len` KHÔNG nhận nội dung tệp qua tham số hành động (lõi trần tham số
  8 KB): module đọc tệp tại `duong_dan_tep` nằm TRONG `thu_muc_len` đã khai
  trên máy chạy web2api, đẩy byte qua đường tệp riêng của dịch vụ.
- Gọi dịch vụ bằng `httpx.AsyncClient` + semaphore riêng của module: lệnh
  rclone chậm/treo không chiếm luồng executor mặc định của lõi.
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
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.tu_xa import _dia_chi_bi_chan as _chan_loi

log = logging.getLogger(__name__)

#: Trần byte nhận khi tải tệp qua API (khớp dịch vụ: 25 MB).
TRAN_TAI_VE = 25 * 1024 * 1024
#: Hết giờ gọi dịch vụ: lệnh truyền dài nhất của dịch vụ là 600 s + biên.
HET_GIO_GOI = 650

#: Công cụ đọc (thực thể `rclone.doc`, rủi ro «doc»): chỉ đọc dữ liệu.
_CONG_CU_DOC = ("liet_ke", "doc_chu", "tai_ve",
                "remotes", "kiem_tra", "config_xem", "drive_start")
#: Công cụ ghi (thực thể `rclone.ghi`, rủi ro «ghi»): đổi trạng thái đám mây.
_CONG_CU_GHI = ("gui_len", "chuyen_tep")
#: Công cụ xoá (thực thể `rclone.xoa`, rủi ro «nguy_hiem»): huỷ dữ liệu/kho.
_CONG_CU_XOA = ("xoa", "xoa_remote")
#: Công cụ cấu hình (thực thể `rclone.cau_hinh`, rủi ro «nguy_hiem»): đổi
#: cấu hình + khoá của dịch vụ — bề mặt nhạy cảm nhất.
_CONG_CU_CAU_HINH = ("them_remote", "config_ghi",
                     "drive_exchange", "luu_khoa_json")

#: Trần cuộc gọi đồng thời tới dịch vụ của module này (chống nghẽn lõi).
_SEMA_DICH_VU = asyncio.Semaphore(4)

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


def _ip_dich_vu_bi_chan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Luật địa chỉ cho DỊCH VỤ PHỤ — dùng chung bộ kiểm của lõi, chỉnh 2 đầu.

    Khác luật lõi (`app/tu_xa.py`) ở đúng hai chỗ: loopback ĐƯỢC phép (dịch
    vụ phụ nghe loopback/mạng Docker nội bộ) và địa chỉ CÔNG KHAI bị CHẶN
    (bản cũ cho qua mọi IP công khai kể cả 6to4 mang metadata — phát hiện
    A5). Mọi lớp còn lại (link-local, không định tuyến, IPv4 nhúng trong
    6to4/Teredo) dùng ĐÚNG `_dia_chi_bi_chan` của lõi, không bộ kiểm thứ hai.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return None
    if ip.is_global:
        return "địa chỉ công khai — dịch vụ phụ chỉ ở loopback/mạng nội bộ"
    return _chan_loi(ip)


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
        ly_do = _ip_dich_vu_bi_chan(ip)
        if ly_do:
            raise ValueError(f"dich_vu_url trỏ vào {ip} — bị chặn: {ly_do}")
    return s


def _kiem_url_dong_bo(url: str) -> str | None:
    """Bản đồng bộ cho `nap()`: trả lý do bị chặn, hoặc None nếu hợp lệ.

    `nap()` là đường cấu hình đi vào lúc nạp module — trước đây đường này
    KHÔNG kiểm gì, URL xấu lọt thẳng vào các lời gọi mạng (phát hiện A5).
    """
    s = str(url or "").strip().rstrip("/")
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        return "dich_vu_url phải dạng http(s)://<máy>[:cổng]"
    if phan.username or phan.password:
        return "dich_vu_url không được chứa tài khoản/mật khẩu"
    try:
        ds = [ipaddress.ip_address(phan.hostname)]
    except ValueError:
        try:
            thong_tin = socket.getaddrinfo(
                phan.hostname, phan.port or 0, type=socket.SOCK_STREAM)
        except OSError:
            return f"không phân giải được tên máy {phan.hostname!r}"
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        ly_do = _ip_dich_vu_bi_chan(ip)
        if ly_do:
            return f"dich_vu_url trỏ vào {ip} — bị chặn: {ly_do}"
    return None


def _duong_trong_thu_muc(duong: str, goc_chu: str) -> Path | None:
    """Ép đường dẫn tệp tải lên nằm TRONG thư mục gốc đã khai (resolve hết
    symlink, cùng ràng buộc 1 của dịch vụ). Ngoài thư mục → None."""
    try:
        goc = Path(goc_chu).resolve()
        dd = Path(duong)
        if not dd.is_absolute():
            dd = goc / dd
        that = dd.resolve()
    except (OSError, RuntimeError):
        return None
    if that != goc and goc not in that.parents:
        return None
    return that


class Module:
    """Cầu nối rclone: liệt kê/đọc/tải về (mặc định) + tải lên/xoá (cần ghi_bat)."""

    def __init__(self, manifest, _goc_kiem_thu: str | None = None):
        self.manifest = manifest
        self.tien_to = str(manifest.get("tien_to") or "rclone")
        self._url = "http://127.0.0.1:8702"
        self._khoa = ""
        self._ghi_bat = False
        self._thu_muc_len = ""
        self._url_loi: str | None = None

    # ── vòng đời ──────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        cau_hinh = cau_hinh or {}
        self._ghi_bat = _bat(cau_hinh.get("ghi_bat", False))
        url = str(cau_hinh.get("dich_vu_url") or "http://127.0.0.1:8702").strip().rstrip("/")
        self._url = url
        # Kiểm URL ngay ở nap(): URL bị chặn → mọi lời gọi mạng bị từ chối
        # với mã lỗi rõ (không đợi tới cai(), không gọi thử ra ngoài).
        self._url_loi = _kiem_url_dong_bo(url)
        if self._url_loi:
            log.warning("rclone: %s", self._url_loi)
        self._thu_muc_len = str(cau_hinh.get("thu_muc_len") or "").strip()
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
                "thu_muc_len": str(cau_hinh.get("thu_muc_len") or "").strip(),
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

    async def thuc_the(self, loc=None) -> list[dict]:
        """Bốn thực thể, mỗi thực thể ĐÚNG một mức rủi ro.

        Lõi tính rủi ro theo THỰC THỂ, không theo hành động: bản cũ trộn xoá
        và ghi cấu hình (nguy_hiem) vào thực thể «ghi» nên chỉ cần quản trị
        xác nhận thực thể một lần là các hành động nguy hiểm chạy không cần
        xác nhận nữa (phát hiện A2). Tách ra thì `rclone.xoa`/`rclone.cau_hinh`
        giữ mức nguy_hiem vĩnh viễn — lõi luôn đòi xác nhận từng lần.
        """
        goc = {"loai": "luu_tru", "thuoc_tinh": {"dich_vu": "dich-vu/rclone"}}
        return [
            {"id": f"{self.tien_to}.doc",
             "ten": "Kho lưu trữ đám mây (rclone) — đọc/tải về",
             **goc,
             "tinh_nang": self._cong_cu(_CONG_CU_DOC, "doc"),
             "rui_ro_mac_dinh": "doc"},
            {"id": f"{self.tien_to}.ghi",
             "ten": "Kho lưu trữ đám mây (rclone) — tải lên/chuyển (cần ghi_bat)",
             **goc,
             "tinh_nang": self._cong_cu(_CONG_CU_GHI, "ghi"),
             "rui_ro_mac_dinh": "ghi"},
            {"id": f"{self.tien_to}.xoa",
             "ten": "Kho lưu trữ đám mây (rclone) — xoá tệp/remote (cần ghi_bat)",
             **goc,
             "tinh_nang": self._cong_cu(_CONG_CU_XOA, "nguy_hiem"),
             "rui_ro_mac_dinh": "nguy_hiem"},
            {"id": f"{self.tien_to}.cau_hinh",
             "ten": "Kho lưu trữ đám mây (rclone) — cấu hình remote/khoá (cần ghi_bat)",
             **goc,
             "tinh_nang": self._cong_cu(_CONG_CU_CAU_HINH, "nguy_hiem"),
             "rui_ro_mac_dinh": "nguy_hiem"},
        ]

    async def trang_thai(self, ids=None) -> dict:
        """Trạng thái theo thực thể từ op `trang_thai` của dịch vụ.

        Dịch vụ không nối được → mọi thực thể báo ok=False (không ném lỗi —
        lõi gọi hàm này khi liệt kê, ném lỗi làm cả danh sách thiết bị đỏ).
        """
        kq = await self._goi_dich_vu("trang_thai", {}, het_gio=15)
        remotes = kq.get("remotes")
        tt = {"ok": bool(kq.get("ok")),
              "phien_ban_rclone": str(kq.get("version") or ""),
              "so_remote": len(remotes) if isinstance(remotes, list) else 0}
        muon = {e["id"] for e in await self.thuc_the()}
        if ids:
            muon &= {str(i) for i in ids}
        return {i: dict(tt) for i in sorted(muon)}

    def _tra_loi_dong(self) -> dict:
        return {"ok": False, "loi": "dich_vu_khong_noi_duoc"}

    def _tieu_de(self) -> dict:
        return {"Authorization": f"Bearer {self._khoa}"}

    async def _goi_dich_vu(self, lenh: str, tham_so: dict, *,
                           het_gio: float = HET_GIO_GOI) -> dict:
        """POST /lenh — bằng AsyncClient (phát hiện A7).

        Bản cũ dùng httpx.Client đồng bộ qua `asyncio.to_thread`: lệnh rclone
        treo chiếm một luồng của executor mặc định mà CẢ LÕI dùng chung.
        Nay: AsyncClient + semaphore riêng của module, không chạm luồng lõi.
        """
        if self._url_loi:
            return {"ok": False, "loi": "dia_chi_dich_vu_khong_hop_le"}
        try:
            async with _SEMA_DICH_VU:
                async with httpx.AsyncClient(
                        timeout=httpx.Timeout(het_gio, connect=10.0)) as client:
                    tra_loi = await client.post(
                        self._url + "/lenh",
                        json={"lenh": lenh, "tham_so": tham_so},
                        headers=self._tieu_de(),
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
        """GET /tep-xuong/<id> — lấy byte đã tải về, ĐỌC THEO DÒNG có trần.

        Không nạp cả thân rồi mới so trần như bản cũ: vượt trần là bỏ ngay.
        """
        if self._url_loi:
            return None
        try:
            async with _SEMA_DICH_VU:
                async with httpx.AsyncClient(
                        timeout=httpx.Timeout(HET_GIO_GOI, connect=10.0)) as client:
                    async with client.stream(
                            "GET", self._url + "/tep-xuong/" + id_tep,
                            headers=self._tieu_de()) as tra_loi:
                        if tra_loi.status_code != 200:
                            return None
                        dem = bytearray()
                        async for muc in tra_loi.aiter_bytes(65536):
                            dem += muc
                            if len(dem) > TRAN_TAI_VE:
                                return None
                        return bytes(dem)
        except Exception as e:
            log.warning("khong_tai_duoc_tep_rclone: %s", type(e).__name__)
            return None

    async def _day_byte_len_dich_vu(self, du_lieu: bytes) -> str | None:
        """POST /tep-len — đẩy byte lên dịch vụ trước khi gui_len."""
        if self._url_loi:
            return None
        try:
            async with _SEMA_DICH_VU:
                async with httpx.AsyncClient(
                        timeout=httpx.Timeout(HET_GIO_GOI, connect=10.0)) as client:
                    tra_loi = await client.post(
                        self._url + "/tep-len",
                        content=du_lieu,
                        headers=self._tieu_de(),
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
        thuc_the = {e["id"]: e for e in await self.thuc_the()}
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
            # Dịch vụ đã chặn ở trần; kiểm lại cỡ TRƯỚC khi tải byte về.
            co = kq.get("co")
            if isinstance(co, int) and co > TRAN_TAI_VE:
                return {"ok": False, "loi": "tep_qua_lon"}
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
            # Nội dung tệp KHÔNG đi qua tham số hành động (lõi trần 8 KB —
            # bản cũ hứa 25 MB qua `du_lieu_b64` là vô dụng trên đường thật,
            # phát hiện A1). Module đọc tệp tại `duong_dan_tep` TRONG
            # `thu_muc_len` đã khai trên máy chạy web2api, đẩy byte qua
            # đường /tep-len riêng của dịch vụ.
            duong = _chu("duong_dan_tep").strip()
            if not duong:
                return {"ok": False, "loi": "thieu_duong_dan_tep"}
            if not self._thu_muc_len:
                return {"ok": False, "loi": "chua_khai_thu_muc_len"}
            tep = _duong_trong_thu_muc(duong, self._thu_muc_len)
            if tep is None:
                return {"ok": False, "loi": "duong_dan_ngoai_thu_muc_len"}
            if not tep.is_file():
                return {"ok": False, "loi": "khong_thay_tep"}
            if tep.stat().st_size > TRAN_TAI_VE:
                return {"ok": False, "loi": "tep_qua_lon"}
            byte = await asyncio.to_thread(tep.read_bytes)
            id_tep = await self._day_byte_len_dich_vu(byte)
            if not id_tep:
                return {"ok": False, "loi": "dich_vu_khong_noi_duoc"}
            return await self._goi_dich_vu("gui_len", {"id_tep": id_tep,
                                                       "thu_muc": _chu("thu_muc"),
                                                       "ten": _chu("ten") or tep.name})
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
