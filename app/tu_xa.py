"""Module TỪ XA: một dịch vụ nói chuẩn OpenAI đăng ký bằng URL — lõi không nạp mã nào của nó.

Dành cho module nặng/GPU/cần gói lõi không có (vd `fw-tach-am`, `vn-translate-gpu` trên .220):
chạy ở container/máy riêng, web2api chỉ chuyển tiếp /v1 bằng CÙNG bộ chuyển tiếp của
lõi (đổi tên model bỏ tiền tố, giữ trường lạ, stream từng dòng, chuẩn hoá lỗi).

Chặn SSRF khi đăng ký: lõi sẽ gửi yêu cầu (kèm khoá) tới URL này theo lệnh của quản trị, nên
không được trỏ vào chính container (loopback — cổng quản trị nội bộ, admin_key…), link-local
169.254.0.0/16 (metadata đám mây: 169.254.169.254 trả khoá IAM) hay địa chỉ không định tuyến.
IPv4 nhúng trong 6to4/Teredo chịu cùng luật (vd 2002:7f00:1:: mang loopback).
NHƯNG cho phép LAN riêng tư 10/8, 172.16/12, 192.168/16: module GPU nằm đúng trong mạng nhà
(.220), chặn chúng là chặn đúng mục đích của tính năng. Cho phép cả http:// vì dịch vụ trong
LAN gần như không có chứng chỉ TLS.
Chống DNS rebinding: tên miền được phân giải và kiểm lại NGAY TRƯỚC mỗi lần nối (TransportGhimIp trong
app/chuyen_tiep.py ghim IP đã kiểm, giữ Host/SNI đúng tên gốc — khuôn app/novnc.py); IP lúc nối khác lúc đăng ký mà bị
chặn thì lượt gọi đó trả 502 `ssrf_blocked`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from urllib.parse import urlsplit



from app.chuyen_tiep import ChuyenTiepOpenAI
from app.yeu_cau_da_phan import YeuCauDaPhan

log = logging.getLogger("web2api.tu_xa")


def cho_phep_loopback_cho_test() -> bool:
    """Chỉ test bật (máy chủ giả chạy trên 127.0.0.1). Production không đặt biến này."""
    return os.environ.get("WEB2API_TEST_CHO_PHEP_LOOPBACK") == "1"


def _dia_chi_bi_chan(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return None if cho_phep_loopback_cho_test() else "loopback (chính container)"
    if ip.is_link_local:
        return "link-local / metadata đám mây (169.254.0.0/16)"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "địa chỉ không định tuyến"
    if not (ip.is_private or ip.is_global):
        return "dải địa chỉ đặc biệt"
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4 NHÚNG trong 6to4 (2002::/16) và Teredo (2001::/32): áp CÙNG luật trên
        # theo CẤU TRÚC địa chỉ (sixtofour/teredo), không khớp chuỗi — vd 2002:7f00:1::
        # mang 127.0.0.1, 2002:a9fe:a9fe:: mang 169.254.169.254. Bản chung cho mọi nơi kiểm SSRF.
        nhung = [ip.sixtofour] if ip.sixtofour else []
        if ip.teredo:
            nhung += list(ip.teredo)
        for v4 in nhung:
            ly_do = _dia_chi_bi_chan(v4)
            if ly_do:
                return f"IPv4 nhúng {v4}: {ly_do}"
    return None


async def kiem_url_lan(url: str) -> str:
    """Chuẩn hoá base_url của module từ xa; ValueError nêu lý do nếu bị chặn."""
    s = str(url or "").strip().rstrip("/")
    phan = urlsplit(s)
    if phan.scheme not in ("http", "https") or not phan.hostname:
        raise ValueError("base_url phải dạng http(s)://<máy>[:cổng]/<đường> — thường kết thúc bằng /v1")
    if phan.username or phan.password:
        raise ValueError("base_url không được chứa tài khoản/mật khẩu; dùng trường api_key")
    try:
        ds = [ipaddress.ip_address(phan.hostname)]
    except ValueError:
        try:
            thong_tin = await asyncio.get_running_loop().getaddrinfo(phan.hostname, phan.port or 0,
                                                                     type=socket.SOCK_STREAM)
        except OSError as e:
            raise ValueError(f"không phân giải được tên máy {phan.hostname!r}: {e}") from None
        ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
    for ip in ds:
        ly_do = _dia_chi_bi_chan(ip)
        if ly_do:
            raise ValueError(f"base_url trỏ vào {ip} — bị chặn: {ly_do}")
    return s


class ModuleTuXa:
    """Thể hiện phục vụ /v1 cho một bản ghi tu_xa.json (cùng giao diện với lớp Module có mã)."""

    def __init__(self, ban_ghi: dict, api_key: str | None):
        from app.nut_gpu import (
            HangDoiDongThoi,
            MachNgat,
            KHA_NANG_LOI_BIET,
            TRAN_DONG_THOI_GPU,
        )

        self.id_ = ban_ghi["id"]
        self.ten = ban_ghi["ten"]
        self.tien_to = ban_ghi["tien_to"]
        self.base_url = ban_ghi["base_url"]
        self.api_key = api_key
        self.la_nut_gpu = bool(ban_ghi.get("la_nut_gpu", False))
        self.bat = bool(ban_ghi.get("bat", True))

        dong_thoi = ban_ghi.get("gioi_han_dong_thoi")
        self.gioi_han_dong_thoi = max(1, min(TRAN_DONG_THOI_GPU, int(dong_thoi or 1)))
        self.hang_doi = HangDoiDongThoi(self.gioi_han_dong_thoi)
        self.mach_ngat = MachNgat()

        # Khai khả năng:
        # Module từ xa thường (không phải nút GPU): luôn chỉ cung cấp "chat" (tương thích ngược hoàn toàn).
        # Nút GPU: áp phép giao giữa khả năng nút khai ∩ khả năng lõi biết ∩ khả năng người dùng bật.
        if self.la_nut_gpu:
            kn_khai = ban_ghi.get("kha_nang")
            kn_bat = ban_ghi.get("kha_nang_bat")
            if kn_khai is None and kn_bat is None:
                cung_cap = ["chat"]
            else:
                cung_cap = []
                for k in KHA_NANG_LOI_BIET:
                    if kn_bat is not None and k not in kn_bat:
                        continue
                    if kn_khai is not None and k not in kn_khai:
                        continue
                    cung_cap.append(k)
                if not cung_cap:
                    cung_cap = ["chat"]
        else:
            cung_cap = ["chat"]

        self.manifest = {
            "id": ban_ghi["id"],
            "ten": ban_ghi["ten"],
            "tien_to": ban_ghi["tien_to"],
            "loai": "ai",
            "giao_thuc": "openai",
            "cung_cap": list(cung_cap),
        }

        # Với nút GPU: khả năng người dùng chưa chọn bật thì không mở phương thức
        if self.la_nut_gpu:
            if "noi" not in cung_cap:
                self.noi = None
            if "nghe" not in cung_cap:
                self.nghe = None
            if "anh_sua" not in cung_cap:
                self.anh_sua = None

        self._ct = ChuyenTiepOpenAI(
            ten=ban_ghi["ten"],
            tien_to=ban_ghi["tien_to"],
            base_url=ban_ghi["base_url"],
            khoa=[api_key] if api_key else [],
            kha_nang=("noi", "nghe", "anh_sua"),
            kiem_ip=_dia_chi_bi_chan,
        )

        self._gpu_telemetry: list[dict] = []
        self._hang_doi_nut: dict = {"dang_chay": 0, "cho": 0}
        self._do_tre_ms: int | None = None
        self._lan_cuoi_thay: str | None = None
        self._vong_nen_task: asyncio.Task | None = None

        if self.la_nut_gpu:
            try:
                loop = asyncio.get_running_loop()
                self._vong_nen_task = loop.create_task(self._vong_telemetry_nen())
            except RuntimeError:
                pass

    async def _dieu_phoi(self, fn, *args, **kwargs):
        """Điều phối cuộc gọi qua mạch ngắt và hàng đợi đồng thời (áp dụng cho nút GPU)."""
        if not self.la_nut_gpu:
            return await fn(*args, **kwargs)

        import json
        from typing import AsyncIterator
        from app.nut_gpu import (
            QuaTaiHangDoi,
            StreamDongThoi,
            la_loi_cau_truc_khach,
        )

        if not self.bat:
            return (
                503,
                {"content-type": "application/json"},
                b'{"error":{"message":"Module dang tat","type":"server_error","code":"module_disabled"}}',
            )

        co_the, ly_do = self.mach_ngat.co_the_goi()
        if not co_the:
            return (
                503,
                {"content-type": "application/json"},
                b'{"error":{"message":"Mach ngat may GPU dang mo do loi lien tiep","type":"server_error","code":"circuit_broken"}}',
            )

        try:
            async with self.hang_doi.vao() as slot:
                try:
                    res = await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    if ly_do == "nua_mo":
                        self.mach_ngat.huy_thu_nua_mo()
                    raise
                except BaseException:
                    self.mach_ngat.ghi_that_bai()
                    raise

                ma = res[0]
                than_loi = res[2] if len(res) > 2 else None
                if ma >= 500:
                    if not la_loi_cau_truc_khach(ma, than_loi):
                        self.mach_ngat.ghi_that_bai()
                else:
                    self.mach_ngat.ghi_thanh_cong()

                if len(res) > 2 and isinstance(res[2], AsyncIterator):
                    fn_giai_phong = slot.chuyen_giao()
                    return (res[0], res[1], StreamDongThoi(res[2], fn_giai_phong))
                else:
                    return res
        except QuaTaiHangDoi as e:
            if ly_do == "nua_mo":
                self.mach_ngat.huy_thu_nua_mo()
            return (
                429,
                {"content-type": "application/json", "retry-after": str(e.retry_after)},
                json.dumps({
                    "error": {
                        "message": str(e),
                        "type": "requests",
                        "code": "rate_limit_exceeded",
                    }
                }).encode("utf-8"),
            )

    async def chat(self, body: dict, stream: bool):
        return await self._dieu_phoi(self._ct.chat, body, stream)

    async def noi(self, body: dict):
        return await self._dieu_phoi(self._ct.noi, body)

    async def nghe(self, yc: YeuCauDaPhan):
        return await self._dieu_phoi(self._ct.nghe, yc)

    async def anh_sua(self, yc: YeuCauDaPhan):
        return await self._dieu_phoi(self._ct.anh_sua, yc)

    async def models(self) -> list[dict]:
        if not self.bat:
            return []
        return await self._ct.models()

    async def lam_moi_model(self) -> None:
        self._ct.lam_het_han_dem_models()

    async def cap_nhat_telemetry(self) -> None:
        """Đo telemetry từ GET /health của nút."""
        import httpx
        from app.chuyen_tiep import TransportGhimIp, doc_phan_hoi_co_tran
        from app.kho import bay_gio_iso
        from app.nut_gpu import lam_sach_telemetry_gpu, TRAN_JSON_TELEMETRY

        phan = urlsplit(self.base_url)
        goc = f"{phan.scheme}://{phan.netloc}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        bat_dau = time.monotonic()
        try:
            transport = TransportGhimIp(_dia_chi_bi_chan)
            async with httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                timeout=httpx.Timeout(5.0, connect=3.0),
            ) as client:
                req = client.build_request("GET", f"{goc}/health", headers=headers)
                r = await client.send(req, stream=True)
                self._do_tre_ms = max(0, int((time.monotonic() - bat_dau) * 1000))
                if r.status_code == 200:
                    try:
                        raw = await doc_phan_hoi_co_tran(r, tran_bytes=TRAN_JSON_TELEMETRY)
                        data = lam_sach_telemetry_gpu(json.loads(raw))
                        self._gpu_telemetry = data["gpu"]
                        self._hang_doi_nut = data["hang_doi"]
                        self._lan_cuoi_thay = bay_gio_iso()
                    except Exception as e:
                        log.debug("%s: lỗi đọc telemetry /health: %s", self.ten, e)
                else:
                    await r.aclose()
        except Exception as e:
            log.debug("%s: lỗi đo telemetry /health: %s", self.ten, type(e).__name__)



    async def _vong_telemetry_nen(self) -> None:
        """Vòng lặp nền làm mới telemetry định kỳ 60s (đo ngay lần đầu)."""
        while True:
            try:
                await self.cap_nhat_telemetry()
                await asyncio.sleep(60.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("%s: lỗi vòng lặp telemetry nền: %s", self.ten, e)


    async def suc_khoe(self) -> dict:
        kq = await self._ct.suc_khoe()
        if not self.la_nut_gpu:
            return kq

        await self.cap_nhat_telemetry()
        kq["gpu"] = self._gpu_telemetry
        kq["hang_doi"] = {
            "dang_chay": self.hang_doi.dang_chay,
            "cho": self.hang_doi.dang_cho,
        }
        kq["do_tre_ms"] = self._do_tre_ms if self._do_tre_ms is not None else (kq.get("khoa") and kq["khoa"][0].get("do_tre_ms"))
        kq["lan_cuoi_thay"] = self._lan_cuoi_thay
        kq["mach_ngat"] = self.mach_ngat.trang_thai
        kq["gioi_han_dong_thoi"] = self.gioi_han_dong_thoi
        kq["bat"] = self.bat
        kq["la_nut_gpu"] = self.la_nut_gpu
        return kq


    async def dung(self) -> None:
        if self._vong_nen_task:
            self._vong_nen_task.cancel()
            self._vong_nen_task = None
        await self._ct.dung()

