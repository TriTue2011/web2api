"""Quản lý nút GPU từ xa: khám phá khả năng, telemetry GPU, hàng đợi đồng thời và mạch ngắt (NV140).

Đặc tả:
1. Hợp đồng nút GPU: GET /health và GET /.well-known/w2a-node; dữ liệu không tin được làm sạch (kiểm kiểu/khoảng/độ dài).
2. Khai khả năng: giao giữa khả năng nút khai và khả năng lõi biết (chat, nghe, noi, anh_sua).
3. Telemetry: suc_khoe() trả thêm gpu, hang_doi, do_tre_ms, lan_cuoi_thay, mach_ngat, bat.
4. Giới hạn đồng thời + hàng đợi (1..8 luồng đồng thời, tối đa 64 chỗ trong hàng chờ, chờ tối đa 30s -> 429).
5. Mạch ngắt (Circuit Breaker): 3 lỗi mạng/5xx liên tiếp -> ngắt trong T giây (lùi dần tối đa 300s), trả 503 ngay.
6. Huỷ request không rò slot; stream giữ slot cho đến khi stream đóng hoàn toàn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx

from app.chuyen_tiep import TransportGhimIp, doc_phan_hoi_co_tran
from app.kham_pha import MA_KHA_NANG
from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

log = logging.getLogger("web2api.nut_gpu")

TRAN_DONG_THOI_GPU = 8
TRAN_HANG_CHO_GPU = 64
THOI_GIAN_CHO_HANG_DOI_GPU = 30.0
NGUONG_LOI_MACH_NGAT = 3
THOI_GIAN_NGAT_BAN_DAU = 30.0
THOI_GIAN_NGAT_TOI_DA = 300.0

KHA_NANG_LOI_BIET = ("chat", "nghe", "noi", "anh_sua")
TRAN_GPU = 8
TRAN_MO_HINH = 64
TRAN_KHA_NANG = 16
DO_DAI_TEN_CARD = 80
DO_DAI_MO_HINH = 80

MAU_ID_NUT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def lam_sach_chuoi(s: Any, toi_da: int = 80) -> str:
    """Loại bỏ ký tự điều khiển, cắt độ dài tối đa."""
    if s is None:
        return ""
    chuoi = str(s)
    chuoi = re.sub(r"[\x00-\x1f\x7f]", "", chuoi)
    return chuoi[:toi_da].strip()


def lam_sach_so(val: Any, mac_dinh: float, nho_nhat: float, lon_nhat: float) -> float:
    """Ép kiểu số thực và kẹp trong khoảng [nho_nhat, lon_nhat]."""
    try:
        so = float(val)
        if so != so:  # NaN
            return mac_dinh
        return max(nho_nhat, min(lon_nhat, so))
    except (ValueError, TypeError):
        return mac_dinh


def lam_sach_telemetry_gpu(du_lieu: Any) -> dict:
    """Làm sạch dữ liệu telemetry từ GET /health của nút GPU."""
    if not isinstance(du_lieu, dict):
        return {
            "ok": False,
            "phien_ban": "",
            "gpu": [],
            "mo_hinh_da_nap": [],
            "hang_doi": {"dang_chay": 0, "cho": 0},
        }

    ds_gpu_tho = du_lieu.get("gpu")
    if ds_gpu_tho is None:
        ds_gpu_tho = du_lieu.get("gpus")
    if isinstance(ds_gpu_tho, dict):
        ds_gpu_tho = [ds_gpu_tho]
    ds_gpu = []
    if isinstance(ds_gpu_tho, list):
        for item in ds_gpu_tho[:TRAN_GPU]:
            if isinstance(item, dict):
                ten = lam_sach_chuoi(item.get("ten") or item.get("name") or "GPU", DO_DAI_TEN_CARD)
                vram_tong_raw = item.get("vram_tong_mb") if "vram_tong_mb" in item else item.get("vram_total_mb")
                vram_tong = lam_sach_so(vram_tong_raw, 0.0, 0.0, 10_000_000.0)
                vram_dung_raw = item.get("vram_dung_mb") if "vram_dung_mb" in item else item.get("vram_used_mb")
                vram_dung = lam_sach_so(vram_dung_raw, 0.0, 0.0, 10_000_000.0)
                # Chấp nhận cả nhiet_c, nhiet_do_c và temperature_c
                nhiet_raw = item.get("nhiet_c") if "nhiet_c" in item else item.get("nhiet_do_c") if "nhiet_do_c" in item else item.get("temperature_c")
                nhiet = lam_sach_so(nhiet_raw, 0.0, 0.0, 150.0)
                tai_raw = item.get("tai_pct") if "tai_pct" in item else item.get("utilization_gpu_pct")
                tai = lam_sach_so(tai_raw, 0.0, 0.0, 100.0)
                ds_gpu.append({
                    "ten": ten,
                    "vram_tong_mb": vram_tong,
                    "vram_dung_mb": vram_dung,
                    "nhiet_c": nhiet,
                    "tai_pct": tai,
                })

    # Hàng đợi
    hd_tho = du_lieu.get("hang_doi")
    hang_doi = {"dang_chay": 0, "cho": 0}
    if isinstance(hd_tho, dict):
        hang_doi["dang_chay"] = int(lam_sach_so(hd_tho.get("dang_chay"), 0.0, 0.0, 1000.0))
        hang_doi["cho"] = int(lam_sach_so(hd_tho.get("cho"), 0.0, 0.0, 10000.0))

    # Mô hình đã nạp
    mh_tho = du_lieu.get("mo_hinh_da_nap")
    if mh_tho is None:
        mh_tho = du_lieu.get("models")
    mo_hinh_da_nap = []
    if isinstance(mh_tho, list):
        for m in mh_tho[:TRAN_MO_HINH]:
            ten_m = lam_sach_chuoi(m, DO_DAI_MO_HINH)
            if ten_m:
                mo_hinh_da_nap.append(ten_m)

    return {
        "ok": bool(du_lieu.get("ok", True)),
        "phien_ban": lam_sach_chuoi(du_lieu.get("phien_ban", ""), 32),
        "gpu": ds_gpu,
        "mo_hinh_da_nap": mo_hinh_da_nap,
        "hang_doi": hang_doi,
    }


def lam_sach_manifest_nut(du_lieu: Any) -> dict:
    """Làm sạch manifest từ GET /.well-known/w2a-node của nút GPU."""
    if not isinstance(du_lieu, dict):
        return {
            "id": "",
            "ten": "",
            "kha_nang": ["chat"],
            "mo_hinh": [],
            "gioi_han_dong_thoi": 1,
        }

    id_raw = lam_sach_chuoi(du_lieu.get("id", ""), 40).lower()
    id_nut = id_raw if MAU_ID_NUT.match(id_raw) else ""
    ten = lam_sach_chuoi(du_lieu.get("ten", "Máy GPU"), DO_DAI_TEN_CARD)

    # Khả năng
    kn_tho = du_lieu.get("kha_nang")
    kha_nang = []
    if isinstance(kn_tho, list):
        for k in kn_tho[:TRAN_KHA_NANG]:
            k_str = str(k or "").strip().lower()
            if MA_KHA_NANG.match(k_str) and k_str not in kha_nang:
                kha_nang.append(k_str)
    if not kha_nang:
        kha_nang = ["chat"]

    # Mô hình
    mh_tho = du_lieu.get("mo_hinh")
    mo_hinh = []
    if isinstance(mh_tho, list):
        for m in mh_tho[:TRAN_MO_HINH]:
            m_str = lam_sach_chuoi(m, DO_DAI_MO_HINH)
            if m_str and m_str not in mo_hinh:
                mo_hinh.append(m_str)

    # Giới hạn đồng thời (trần cứng 8, tối thiểu 1)
    dong_thoi = int(lam_sach_so(du_lieu.get("gioi_han_dong_thoi"), 1.0, 1.0, float(TRAN_DONG_THOI_GPU)))

    return {
        "id": id_nut,
        "ten": ten,
        "kha_nang": kha_nang,
        "mo_hinh": mo_hinh,
        "gioi_han_dong_thoi": dong_thoi,
    }


class MachNgat:
    """Mạch ngắt (Circuit Breaker) bảo vệ hệ thống khỏi máy GPU treo/lỗi liên tiếp."""

    def __init__(
        self,
        nguong_loi: int = NGUONG_LOI_MACH_NGAT,
        thoi_gian_ngat_ban_dau: float = THOI_GIAN_NGAT_BAN_DAU,
        thoi_gian_ngat_toi_da: float = THOI_GIAN_NGAT_TOI_DA,
    ):
        self.nguong_loi = nguong_loi
        self.thoi_gian_ngat_ban_dau = thoi_gian_ngat_ban_dau
        self.thoi_gian_ngat_toi_da = thoi_gian_ngat_toi_da

        self.so_loi_lien_tiep = 0
        self.thoi_gian_ngat = thoi_gian_ngat_ban_dau
        self.ngat_den = 0.0
        self.dang_thu_nua_mo = False
        self._trang_thai = "dong"

    @property
    def trang_thai(self) -> str:
        """Trạng thái hiện tại: 'dong', 'ngat', hoặc 'nua_mo'."""
        if self._trang_thai == "ngat":
            if time.monotonic() >= self.ngat_den:
                return "nua_mo"
        return self._trang_thai

    def co_the_goi(self) -> tuple[bool, str]:
        """Kiểm tra xem có được phép gửi yêu cầu tới nút không.

        Trả về (duoc_goi, ly_do):
          (True, "dong")
          (True, "nua_mo") - lượt thử nghiệm
          (False, "ngat") - mạch đang mở
          (False, "nua_mo_dang_thu") - đã có lượt thử đang chạy
        """
        tt = self.trang_thai
        if tt == "dong":
            return True, "dong"
        if tt == "nua_mo":
            if not self.dang_thu_nua_mo:
                self.dang_thu_nua_mo = True
                self._trang_thai = "nua_mo"
                return True, "nua_mo"
            return False, "nua_mo_dang_thu"
        return False, "ngat"

    def ghi_thanh_cong(self) -> None:
        """Ghi nhận lượt gọi thành công (HTTP < 500) -> đóng mạch."""
        self._trang_thai = "dong"
        self.so_loi_lien_tiep = 0
        self.thoi_gian_ngat = self.thoi_gian_ngat_ban_dau
        self.dang_thu_nua_mo = False

    def ghi_that_bai(self) -> None:
        """Ghi nhận lỗi mạng hoặc 5xx từ nút -> tăng đếm lỗi, ngắt nếu quá ngưỡng."""
        self.so_loi_lien_tiep += 1
        tt = self.trang_thai
        if self.so_loi_lien_tiep >= self.nguong_loi or tt == "nua_mo":
            if tt == "nua_mo":
                self.thoi_gian_ngat = min(self.thoi_gian_ngat * 2, self.thoi_gian_ngat_toi_da)
            else:
                self.thoi_gian_ngat = self.thoi_gian_ngat_ban_dau
            self.ngat_den = time.monotonic() + self.thoi_gian_ngat
            self._trang_thai = "ngat"
            self.dang_thu_nua_mo = False
            log.warning(
                "Mạch ngắt máy GPU kích hoạt: nghỉ %.0fs do %d lỗi liên tiếp",
                self.thoi_gian_ngat,
                self.so_loi_lien_tiep,
            )

    def huy_thu_nua_mo(self) -> None:
        """Huỷ lượt thử nửa mở (do client huỷ kết nối hoặc hết giờ) -> cho phép lượt sau thử lại."""
        if self.dang_thu_nua_mo:
            self.dang_thu_nua_mo = False


def la_loi_cau_truc_khach(ma: int, than: bytes | str | None) -> bool:
    """Phân loại lỗi bằng CẤU TRÚC: phản hồi lỗi kiểu OpenAI có error.type == 'invalid_request_error'
    (vd prompt vượt context) là do đầu vào của khách, không tính là lỗi sập nút GPU."""
    if not than:
        return False
    try:
        d = json.loads(than) if isinstance(than, (bytes, bytearray, str)) else than
        if isinstance(d, dict) and isinstance(d.get("error"), dict):
            err_type = str(d["error"].get("type", "")).strip().lower()
            if err_type == "invalid_request_error":
                return True
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, AttributeError):
        return False
    return False




class QuaTaiHangDoi(Exception):
    """Hàng đợi máy GPU quá tải hoặc hết thời gian chờ."""

    def __init__(self, thong_diep: str, retry_after: int = 30):
        super().__init__(thong_diep)
        self.retry_after = retry_after


class TheSlot:
    """Thẻ giữ slot thực thi đồng thời, cho phép chuyển giao cho stream hoặc tự động giải phóng."""

    def __init__(self, hang_doi: HangDoiDongThoi):
        self._hang_doi = hang_doi
        self._da_chuyen_giao = False
        self._da_giai_phong = False

    def chuyen_giao(self):
        """Chuyển giao quyền giải phóng slot cho stream."""
        self._da_chuyen_giao = True
        return self.giai_phong

    def giai_phong(self) -> None:
        """Hàm giải phóng đồng bộ (callback) khi stream kết thúc hoặc đóng."""
        if not self._da_giai_phong:
            self._da_giai_phong = True
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._nha())
            except RuntimeError:
                pass

    async def _nha(self) -> None:
        async with self._hang_doi._khoa:
            self._hang_doi.dang_chay = max(0, self._hang_doi.dang_chay - 1)
            self._hang_doi._danh_thuc_nguoi_ke()


class HangDoiDongThoi:
    """Giới hạn số tác vụ đồng thời và hàng đợi FIFO cho máy GPU."""

    def __init__(
        self,
        gioi_han: int = 1,
        tran_hang_cho: int = TRAN_HANG_CHO_GPU,
        thoi_gian_cho: float = THOI_GIAN_CHO_HANG_DOI_GPU,
    ):
        self.gioi_han = max(1, min(TRAN_DONG_THOI_GPU, int(gioi_han)))
        self.tran_hang_cho = tran_hang_cho
        self.thoi_gian_cho = thoi_gian_cho

        self.dang_chay = 0
        self.dang_cho = 0
        self._hang_cho: list[asyncio.Event] = []
        self._khoa = asyncio.Lock()

    @asynccontextmanager
    async def vao(self) -> AsyncIterator[TheSlot]:
        """Vào hàng đợi và lấy slot thực thi; ném QuaTaiHangDoi nếu đầy/quá giờ."""
        ev: asyncio.Event | None = None
        async with self._khoa:
            if self.dang_chay < self.gioi_han:
                self.dang_chay += 1
                duoc_chay_ngay = True
            else:
                if self.dang_cho >= self.tran_hang_cho:
                    raise QuaTaiHangDoi("Hàng đợi máy GPU đã đầy", int(self.thoi_gian_cho))
                ev = asyncio.Event()
                self._hang_cho.append(ev)
                self.dang_cho += 1
                duoc_chay_ngay = False

        if not duoc_chay_ngay and ev is not None:
            try:
                await asyncio.wait_for(ev.wait(), timeout=self.thoi_gian_cho)
            except (asyncio.TimeoutError, TimeoutError):
                async with self._khoa:
                    if ev in self._hang_cho:
                        self._hang_cho.remove(ev)
                        self.dang_cho = max(0, self.dang_cho - 1)
                    else:
                        # Đã được pop hộ: dang_cho đã trừ, dang_chay đã tăng hộ
                        self.dang_chay = max(0, self.dang_chay - 1)
                        self._danh_thuc_nguoi_ke()
                raise QuaTaiHangDoi("Hết thời gian chờ trong hàng đợi máy GPU", int(self.thoi_gian_cho)) from None
            except asyncio.CancelledError:
                async with self._khoa:
                    if ev in self._hang_cho:
                        self._hang_cho.remove(ev)
                        self.dang_cho = max(0, self.dang_cho - 1)
                    else:
                        # Đã được pop hộ: dang_cho đã trừ, dang_chay đã tăng hộ
                        self.dang_chay = max(0, self.dang_chay - 1)
                        self._danh_thuc_nguoi_ke()
                raise

        # Đã giữ slot thành công
        slot = TheSlot(self)
        try:
            yield slot
        finally:
            if not slot._da_chuyen_giao:
                async with self._khoa:
                    self.dang_chay = max(0, self.dang_chay - 1)
                    self._danh_thuc_nguoi_ke()


    def _danh_thuc_nguoi_ke(self) -> None:
        """Đánh thức yêu cầu tiếp theo trong hàng đợi nếu còn slot."""
        if self._hang_cho and self.dang_chay < self.gioi_han:
            ev = self._hang_cho.pop(0)
            self.dang_cho = max(0, self.dang_cho - 1)
            self.dang_chay += 1
            ev.set()


class StreamDongThoi:
    """Bọc một async iterator để giữ slot hàng đợi đồng thời cho tới khi kết thúc stream."""

    def __init__(self, it: AsyncIterator[bytes], giai_phong_fn):
        self._it = it
        self._giai_phong_fn = giai_phong_fn
        self._da_giai_phong = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._it.__anext__()
        except StopAsyncIteration:
            self._giai_phong()
            raise
        except Exception:
            self._giai_phong()
            raise

    def _giai_phong(self) -> None:
        if not self._da_giai_phong:
            self._da_giai_phong = True
            if self._giai_phong_fn:
                self._giai_phong_fn()

    async def aclose(self) -> None:
        try:
            if hasattr(self._it, "aclose"):
                await self._it.aclose()
        finally:
            self._giai_phong()


TRAN_JSON_TELEMETRY = 64 * 1024


async def do_nut_gpu(base_url: str, api_key: str | None = None) -> dict:
    """Dò nút GPU bằng GET /health và GET /.well-known/w2a-node qua TransportGhimIp.

    Trả về thông tin đã làm sạch:
      {ok, co_w2a, ten, gpu, mo_hinh, kha_nang, gioi_han_dong_thoi, do_tre_ms, hang_doi}
    """
    url_chuan = await kiem_url_lan(base_url)
    phan = urlsplit(url_chuan)
    goc = f"{phan.scheme}://{phan.netloc}"

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    transport = TransportGhimIp(_dia_chi_bi_chan)
    bat_dau = time.monotonic()
    do_tre_ms = 0
    co_health = False
    co_w2a = False
    manifest_nut = {"id": "", "ten": "Máy GPU", "kha_nang": ["chat"], "mo_hinh": [], "gioi_han_dong_thoi": 1}
    telemetry = {"ok": True, "phien_ban": "", "gpu": [], "mo_hinh_da_nap": [], "hang_doi": {"dang_chay": 0, "cho": 0}}

    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        timeout=httpx.Timeout(5.0, connect=3.0),
    ) as client:
        # 1. Gọi GET /health có trần 64KB
        try:
            req_health = client.build_request("GET", f"{goc}/health", headers=headers)
            r_health = await client.send(req_health, stream=True)
            do_tre_ms = max(0, int((time.monotonic() - bat_dau) * 1000))
            if r_health.status_code == 200:
                try:
                    raw_health = await doc_phan_hoi_co_tran(r_health, tran_bytes=TRAN_JSON_TELEMETRY)
                    telemetry = lam_sach_telemetry_gpu(json.loads(raw_health))
                    co_health = True
                except Exception as e:
                    log.debug("Lỗi parse JSON /health từ %s: %s", goc, e)
            else:
                await r_health.aclose()
        except Exception as e:
            log.warning("Không đọc được /health từ %s: %s", goc, type(e).__name__)

        # 2. Gọi GET /.well-known/w2a-node có trần 64KB
        try:
            req_node = client.build_request("GET", f"{goc}/.well-known/w2a-node", headers=headers)
            r_node = await client.send(req_node, stream=True)
            if r_node.status_code == 200:
                try:
                    raw_node = await doc_phan_hoi_co_tran(r_node, tran_bytes=TRAN_JSON_TELEMETRY)
                    manifest_nut = lam_sach_manifest_nut(json.loads(raw_node))
                    co_w2a = True
                except Exception as e:
                    log.debug("Lỗi parse JSON /.well-known/w2a-node từ %s: %s", goc, e)
            else:
                await r_node.aclose()
        except Exception as e:
            log.warning("Không đọc được /.well-known/w2a-node từ %s: %s", goc, type(e).__name__)

        # Nếu không có w2a-node, thử /models để kiểm tra dịch vụ OpenAI thông thường
        if not co_w2a:
            try:
                req_models = client.build_request("GET", f"{url_chuan}/models", headers=headers)
                r_models = await client.send(req_models, stream=True)
                if r_models.status_code == 200:
                    try:
                        raw_models = await doc_phan_hoi_co_tran(r_models, tran_bytes=TRAN_JSON_TELEMETRY)
                        d = json.loads(raw_models).get("data", [])
                        if isinstance(d, list):
                            manifest_nut["mo_hinh"] = [lam_sach_chuoi(m.get("id"), DO_DAI_MO_HINH) for m in d if isinstance(m, dict)][:TRAN_MO_HINH]
                    except Exception as e:
                        log.debug("Lỗi parse JSON /models: %s", e)
                else:
                    await r_models.aclose()
            except Exception as e:
                log.debug("Không gọi được /models từ %s: %s", url_chuan, e)

    return {
        "ok": bool(co_health or co_w2a),
        "co_w2a": co_w2a,
        "base_url": url_chuan,
        "do_tre_ms": do_tre_ms,
        "id": manifest_nut["id"],
        "ten": manifest_nut["ten"],
        "gpu": telemetry["gpu"],
        "mo_hinh": manifest_nut["mo_hinh"],
        "mo_hinh_da_nap": telemetry["mo_hinh_da_nap"],
        "kha_nang": manifest_nut["kha_nang"],
        "gioi_han_dong_thoi": manifest_nut["gioi_han_dong_thoi"],
        "hang_doi": telemetry["hang_doi"],
        "manifest": manifest_nut,
        "telemetry": telemetry,
    }

