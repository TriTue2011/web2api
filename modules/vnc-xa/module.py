"""Module điều khiển trình duyệt từ xa (Chuẩn C6).

Module này đóng vai trò cầu nối HTTP tới một dịch vụ trình duyệt riêng
(chạy độc lập ở một container hoặc máy chủ riêng):
- Dịch vụ trình duyệt cung cấp API:
  POST /ho-so           {ten, loai}   -> {ho_so, ten}  (workspace: tên hiển thị, ID ngẫu nhiên)
  POST /ho-so/mo        {ho_so, loai, url?} -> {ho_so, loai, dich}
  POST /ho-so/{id}/dong {giu_phien}   -> {ok, session_preserved}
  GET  /ho-so                         -> danh sách hồ sơ (ho_so, ten, loai, trang_thai, dang_mo, thu_cong, luc_mo)
  GET  /ho-so/{id}/cookie?mien=<miền> -> {cookie, so_cookie} (chỉ hồ sơ Firefox; Chromium 501)
- Mọi lời gọi kèm header Authorization: Bearer <khoa_dich_vu>.
- Lõi web2api gọi các phương thức trinh_duyet_* của module này để quản lý hồ sơ và lấy
  địa chỉ nội bộ noVNC làm đích proxy.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from typing import Any

import httpx

from app.chuyen_tiep import (
    LoiDichBiChan,
    PhanHoiQuaLon,
    TransportGhimIp,
    doc_phan_hoi_co_tran,
)
from app.khoi_phuc_phien import BoDieuKhienTrinhDuyet
from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

log = logging.getLogger("web2api.module.vnc_xa")

#: Mã hồ sơ đi vào ĐƯỜNG DẪN gọi dịch vụ: kiểm lại ở đây dù lõi đã kiểm.
MAU_HO_SO = re.compile(r"[a-f0-9]{16,64}")
#: Mã trạng thái của dịch vụ mà lõi chuyển tiếp cho quản trị (hợp đồng `trinh_duyet_cookie`: thuộc tính `ma`).
MA_CHUYEN_TIEP = (400, 404, 409, 501)


class LoiDichVu(RuntimeError):
    """Lỗi dựng từ MÃ TRẠNG THÁI của dịch vụ (không chép nội dung phản hồi — có thể chứa cookie)."""

    def __init__(self, thong_diep: str, ma: int | None = None):
        super().__init__(thong_diep)
        self.ma = ma


def _che_khoa(k: str) -> str:
    if not k:
        return "…"
    return f"…{k[-4:]}" if len(k) >= 4 else "…"


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi theo cấu trúc mã HTTP chuẩn (không dò chữ)."""
    if ma in (401, 403):
        return "khoa_hong"
    if ma == 429:
        return "het_han_muc"
    if 500 <= ma <= 599:
        return "may_chu_loi"
    if 400 <= ma < 500:
        return "yeu_cau_sai"
    return "khong_ro"


class _VanChuyenGhimIpDongBo(httpx.BaseTransport):
    """Bản đồng bộ của TransportGhimIp (app/chuyen_tiep.py).

    Hai phương thức đồng bộ của hợp đồng BoDieuKhienTrinhDuyet (doc_cookie,
    tin_hieu) phải gọi HTTP mà không có vòng sự kiện chạy ở chỗ gọi — nên cần
    vận chuyển đồng bộ vẫn ghim IP lúc KẾT NỐI (chống DNS rebinding) y như bản
    bất đồng bộ: phân giải ngay trước khi gửi, kiểm MỌI IP, nối thẳng IP đã
    kiểm, giữ Host/SNI gốc.
    """

    def __init__(self, kiem_ip, goc: httpx.BaseTransport | None = None):
        self._kiem_ip = kiem_ip
        self._goc = goc or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        may = request.url.host
        cong = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            ds = [ipaddress.ip_address(may)]
            la_ip_chu = True
        except ValueError:
            la_ip_chu = False
            try:
                tt = socket.getaddrinfo(may, cong, type=socket.SOCK_STREAM)
            except OSError:
                raise httpx.ConnectError("không phân giải được tên máy",
                                         request=request) from None
            ds = []
            for x in tt:
                ip = ipaddress.ip_address(x[4][0])
                if ip not in ds:
                    ds.append(ip)
        chon = None
        ly_do = None
        for ip in ds:
            ly_do = self._kiem_ip(ip)
            if ly_do is None:
                chon = ip
                break
        if chon is None:
            log.warning("Chặn kết nối tới %s: phân giải ra %s — %s",
                        may, ds[0] if ds else "?", ly_do)
            raise LoiDichBiChan("đích trỏ vào dải bị chặn lúc kết nối", request=request)
        if not la_ip_chu:
            request.url = request.url.copy_with(host=str(chon))
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": may}
        return self._goc.handle_request(request)

    def close(self) -> None:
        self._goc.close()


class TrinhDuyetThat(BoDieuKhienTrinhDuyet):
    """Bộ điều khiển trình duyệt THẬT qua dịch vụ trình duyệt (NV92 P1).

    Thực hiện hợp đồng BoDieuKhienTrinhDuyet (app/khoi_phuc_phien.py) bằng các
    đường điều khiển của dịch vụ (giành → nguyên thuỷ → nhả). Mỗi thể hiện gắn
    MỘT hồ sơ:

    - ``gianh()`` trước khi dùng; ``dong()`` (hoặc ``nha()``) để nhả.
    - Lỗi dịch vụ → LoiDichVu chỉ mang mã trạng thái (không chép nội dung phản
      hồi — có thể chứa cookie).
    - Giá trị điền không vào log.
    - Mọi lời gọi mang Bearer <redacted> vụ và qua kiểm IP lúc kết nối
      (chống DNS rebinding) như các phương thức khác của module này.
    """

    def __init__(self, dich_vu_url: str, khoa: str, ho_so: str,
                 kiem_ip=None, timeout: float = 30.0):
        if not isinstance(ho_so, str) or not MAU_HO_SO.fullmatch(ho_so):
            raise ValueError("Mã hồ sơ không hợp lệ (hex 16-64 ký tự).")
        if not dich_vu_url or not khoa:
            raise ValueError("Thiếu dich_vu_url hoặc khoa dịch vụ.")
        self._goc = dich_vu_url.rstrip("/")
        self._ho_so = ho_so
        self._khoa = khoa
        self._kiem_ip = kiem_ip or _dia_chi_bi_chan
        self._client = httpx.Client(
            timeout=float(timeout),
            transport=_VanChuyenGhimIpDongBo(self._kiem_ip),
        )
        self._da_gianh = False

    # ── gọi dịch vụ ──

    def _duong(self, duoi: str) -> str:
        return f"{self._goc}/ho-so/{self._ho_so}{duoi}"

    def _post(self, duoi: str, than: dict) -> dict:
        try:
            r = self._client.post(
                self._duong(duoi), json=than,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
        except httpx.HTTPError as e:
            raise LoiDichVu(
                f"Dịch vụ trình duyệt không trả lời ({type(e).__name__}).") from None
        if r.status_code >= 400:
            raise LoiDichVu(
                f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}.",
                ma=r.status_code)
        try:
            kq = r.json()
        except ValueError:
            raise LoiDichVu("Dịch vụ trình duyệt trả JSON hỏng.",
                            ma=r.status_code) from None
        if not isinstance(kq, dict):
            raise LoiDichVu("Dịch vụ trình duyệt trả cấu trúc lạ.",
                            ma=r.status_code)
        return kq

    def _get(self, duoi: str, tham_so: dict) -> dict:
        """GET tới dịch vụ — cùng chính sách lỗi/log như `_post` (NV155c-SUA)."""
        try:
            r = self._client.get(
                self._duong(duoi), params=tham_so,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
        except httpx.HTTPError as e:
            raise LoiDichVu(
                f"Dịch vụ trình duyệt không trả lời ({type(e).__name__}).") from None
        if r.status_code >= 400:
            raise LoiDichVu(
                f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}.",
                ma=r.status_code)
        try:
            kq = r.json()
        except ValueError:
            raise LoiDichVu("Dịch vụ trình duyệt trả JSON hỏng.",
                            ma=r.status_code) from None
        if not isinstance(kq, dict):
            raise LoiDichVu("Dịch vụ trình duyệt trả cấu trúc lạ.",
                            ma=r.status_code)
        return kq

    async def gianh(self) -> None:
        """Giành quyền điều khiển máy cho hồ sơ (409 nếu đang bận/có người)."""
        await asyncio.to_thread(self._post, "/dieu-khien/gianh", {})
        self._da_gianh = True

    async def nha(self) -> None:
        """Nhả quyền điều khiển (luỹ đẳng, không ném khi dịch vụ lỗi)."""
        try:
            await asyncio.to_thread(self._post, "/dieu-khien/nha", {})
        except LoiDichVu as e:
            log.warning("trinh-duyet-that: nhả quyền lỗi (%s)", e.ma)
        finally:
            self._da_gianh = False

    # ── hợp đồng BoDieuKhienTrinhDuyet ──

    async def mo_trang(self, url: str) -> None:
        await asyncio.to_thread(self._post, "/dieu-huong", {"url": url})

    async def dien(self, bo_chon: str, gia_tri: str) -> None:
        await asyncio.to_thread(
            self._post, "/dien", {"bo_chon": bo_chon, "van_ban": gia_tri})
        # Giá trị KHÔNG vào log — chỉ ghi độ dài bộ chọn.
        log.info("trinh-duyet-that: dien (bo_chon %d ky tu)", len(str(bo_chon)))

    async def bam(self, bo_chon: str) -> None:
        await asyncio.to_thread(self._post, "/bam", {"bo_chon": bo_chon})

    async def cho(self, bo_chon: str, giay: float = 10.0) -> bool:
        kq = await asyncio.to_thread(
            self._post, "/cho", {"bo_chon": bo_chon, "giay": giay})
        return bool(kq.get("thay"))

    async def doc_cookie(self, mien: str) -> dict:
        kq = await asyncio.to_thread(
            self._post, "/cookie-truc-tiep", {"mien": mien})
        chuoi = kq.get("cookie") or ""
        ra: dict[str, str] = {}
        if isinstance(chuoi, str):
            for muc in chuoi.split(";"):
                ten, _, gt = muc.strip().partition("=")
                if ten:
                    ra[ten] = gt
        return ra

    async def tin_hieu(self, quy_tac: list | None = None) -> set:
        """Tín hiệu qua dịch vụ: gửi quy tắc cấu trúc của module (NV94-b).

        Dịch vụ thực thi quy tắc bằng cấu trúc (cookie có/không, URL/tiêu đề
        chứa, bộ chọn) — mã mở, không dò chữ (xem server.doc_tin_hieu).
        """
        kq = await asyncio.to_thread(
            self._post, "/tin-hieu", {"quy_tac": list(quy_tac or [])})
        ds = kq.get("tin_hieu") or []
        return {s for s in ds if isinstance(s, str)}

    async def doc_cookie_ho_so_da_dong(self, mien: str):
        """Cookie của `mien` trong KHO của hồ sơ ĐÃ ĐÓNG (NV155c-SUA).

        Qua API hồ sơ của dịch vụ: GET /ho-so/{id}/cookie — dịch vụ đọc
        cookies.sqlite trên đĩa, KHÔNG mở trình duyệt. Trả {tên: giá trị}
        (rỗng = đã kiểm được, miền đó không có cookie). Dịch vụ lỗi
        (404 chưa có kho, 409 hồ sơ đang mở, 501 Chromium, mất kết nối…)
        → None «không kiểm được». Giá trị cookie không vào log.
        """
        try:
            kq = await asyncio.to_thread(self._get, "/cookie", {"mien": mien})
        except LoiDichVu:
            return None
        chuoi = kq.get("cookie") or ""
        ra: dict[str, str] = {}
        if isinstance(chuoi, str):
            for muc in chuoi.split(";"):
                ten, _, gt = muc.strip().partition("=")
                if ten:
                    ra[ten] = gt
        return ra

    async def dong(self) -> None:
        """Nhả quyền và đóng client HTTP."""
        try:
            await self.nha()
        finally:
            await asyncio.to_thread(self._client.close)


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self._dich_vu_url = ""
        self._khoa = ""
        # Hàm kiểm IP lúc KẾT NỐI (chống DNS rebinding: kiểm lúc cài, dùng lúc gọi).
        # Mặc định là chính sách SSRF của lõi; test cần nối máy chủ giả trên loopback
        # thì gán thuộc tính này của thể hiện (không có công tắc môi trường).
        self._kiem_ip = _dia_chi_bi_chan

    def _client(self, timeout: float) -> httpx.AsyncClient:
        """MỘT chỗ dựng client cho mọi lời gọi tới dịch vụ trình duyệt.

        Transport ghim IP: mỗi yêu cầu phân giải tên máy NGAY trước khi gửi, kiểm mọi IP bằng `self._kiem_ip`,
        nối thẳng IP đã kiểm (giữ Host/SNI gốc)."""
        return httpx.AsyncClient(timeout=timeout, transport=TransportGhimIp(self._kiem_ip))

    def nap(self, cau_hinh: dict) -> None:
        self._dich_vu_url = str(cau_hinh.get("dich_vu_url") or "").strip().rstrip("/")
        self._khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()

    async def cai(self, cau_hinh: dict) -> dict[str, Any]:
        dich_vu_url = str(cau_hinh.get("dich_vu_url") or "").strip().rstrip("/")
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        khoa_che = _che_khoa(khoa)

        if not dich_vu_url:
            return {
                "ok": False,
                "loi": "Thiếu địa chỉ dịch vụ trình duyệt (dich_vu_url)",
                "ket_qua_tung_khoa": [],
            }
        if not khoa:
            return {
                "ok": False,
                "loi": "Thiếu khoá dịch vụ (khoa_dich_vu)",
                "ket_qua_tung_khoa": [],
            }

        try:
            url_sach = await kiem_url_lan(dich_vu_url)
        except ValueError as e:
            return {
                "ok": False,
                "loi": f"Địa chỉ dịch vụ không hợp lệ hoặc bị chặn: {e}",
                "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": False, "loi": str(e)}],
            }

        # Kiểm tra kết nối tới dịch vụ trình duyệt qua GET /ho-so (KHÔNG mở trình duyệt khi cài)
        try:
            async with self._client(10.0) as client:
                r = await client.get(
                    f"{url_sach}/ho-so",
                    headers={"Authorization": f"Bearer {khoa}"},
                )
                if r.status_code == 200:
                    return {
                        "ok": True,
                        "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": True}],
                        "loi": None,
                        "cau_hinh_luu": {
                            "dich_vu_url": url_sach,
                            "khoa_dich_vu": khoa,
                        },
                    }
                elif r.status_code in (401, 403):
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [
                            {"khoa": khoa_che, "ok": False, "loi": "Khoá dịch vụ không đúng"}
                        ],
                        "loi": f"Khoá dịch vụ không đúng (mã HTTP {r.status_code})",
                    }
                else:
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [
                            {"khoa": khoa_che, "ok": False, "loi": f"Mã HTTP {r.status_code}"}
                        ],
                        "loi": f"Dịch vụ trình duyệt trả mã {r.status_code}",
                    }
        except Exception as e:
            return {
                "ok": False,
                "ket_qua_tung_khoa": [
                    {"khoa": khoa_che, "ok": False, "loi": "Không kết nối được"}
                ],
                "loi": f"Không thể kết nối tới dịch vụ trình duyệt ({type(e).__name__})",
            }

    async def trinh_duyet_tao(self, ten: str) -> dict[str, Any]:
        """Tạo workspace Firefox (Firefox: cookie đọc được để đưa sang module khác — xem trinh_duyet_cookie)."""
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")
        async with self._client(15.0) as client:
            req = client.build_request(
                "POST",
                f"{self._dich_vu_url}/ho-so",
                json={"ten": ten, "loai": "firefox"},
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            r = await client.send(req, stream=True)
            try:
                noi_dung = await doc_phan_hoi_co_tran(r)
            except PhanHoiQuaLon:
                raise LoiDichVu("Phản hồi tạo workspace từ dịch vụ quá lớn", ma=502)
            except TimeoutError:
                raise LoiDichVu("Dịch vụ không trả lời kịp: quá thời gian đọc", ma=504)
        if r.status_code == 200:
            try:
                kq = json.loads(noi_dung)
            except ValueError:
                kq = None
            if not isinstance(kq, dict):
                raise LoiDichVu("Phản hồi tạo workspace từ dịch vụ không phải đối tượng JSON")
            return {"ho_so": kq.get("ho_so"), "ten": kq.get("ten")}
        if r.status_code in (401, 403):
            raise LoiDichVu(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
        raise LoiDichVu(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_mo(self, ho_so: str, loai: str, url: str | None = None) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình địa chỉ dịch vụ hoặc khoá")

        than: dict[str, Any] = {"ho_so": ho_so, "loai": loai}
        if url:
            than["url"] = url  # lõi đã kiểm (http/https, không userinfo); dịch vụ kiểm lại
        async with self._client(30.0) as client:
            req = client.build_request(
                "POST",
                f"{self._dich_vu_url}/ho-so/mo",
                json=than,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            r = await client.send(req, stream=True)
            try:
                noi_dung = await doc_phan_hoi_co_tran(r)
            except PhanHoiQuaLon:
                raise RuntimeError("Phản hồi mở hồ sơ từ dịch vụ quá lớn")
            except TimeoutError:
                raise RuntimeError("Dịch vụ trình duyệt không trả lời kịp: quá thời gian đọc")
            if r.status_code == 200:
                try:
                    kq = json.loads(noi_dung)
                except ValueError:
                    kq = None
                if not isinstance(kq, dict):
                    raise RuntimeError("Phản hồi mở hồ sơ từ dịch vụ không phải đối tượng JSON")
                return kq
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")

        url = f"{self._dich_vu_url}/ho-so/{ho_so}/dong"
        async with self._client(15.0) as client:
            req = client.build_request(
                "POST",
                url,
                json={"giu_phien": giu_phien},
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            r = await client.send(req, stream=True)
            try:
                noi_dung = await doc_phan_hoi_co_tran(r)
            except PhanHoiQuaLon:
                raise RuntimeError("Phản hồi đóng hồ sơ từ dịch vụ quá lớn")
            except TimeoutError:
                raise RuntimeError("Dịch vụ trình duyệt không trả lời kịp: quá thời gian đọc")
            if r.status_code == 200:
                try:
                    kq = json.loads(noi_dung)
                except ValueError:
                    kq = None
                return kq if isinstance(kq, dict) else {"ok": True}
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_ds(self) -> list[dict[str, Any]]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")

        url = f"{self._dich_vu_url}/ho-so"
        async with self._client(15.0) as client:
            req = client.build_request(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            r = await client.send(req, stream=True)
            try:
                noi_dung = await doc_phan_hoi_co_tran(r)
            except PhanHoiQuaLon:
                raise RuntimeError("Danh sách hồ sơ từ dịch vụ quá lớn")
            except TimeoutError:
                raise RuntimeError("Dịch vụ trình duyệt không trả lời kịp: quá thời gian đọc")
            if r.status_code == 200:
                try:
                    ds = json.loads(noi_dung)
                except ValueError:
                    ds = None
                if not isinstance(ds, list):
                    raise RuntimeError("Danh sách hồ sơ không phải mảng JSON")
                return ds
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_cookie(self, ho_so: str, mien: str) -> dict[str, Any]:
        """Cookie của `mien` trong hồ sơ, đọc qua GET /ho-so/{id}/cookie của dịch vụ. KHÔNG log giá trị."""
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")
        if not isinstance(ho_so, str) or not MAU_HO_SO.fullmatch(ho_so):
            raise LoiDichVu("Mã hồ sơ không hợp lệ", ma=400)

        async with self._client(15.0) as client:  # httpx mặc định KHÔNG theo chuyển hướng
            req = client.build_request(
                "GET",
                f"{self._dich_vu_url}/ho-so/{ho_so}/cookie",
                params={"mien": mien},
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            r = await client.send(req, stream=True)
            try:
                noi_dung = await doc_phan_hoi_co_tran(r)
            except PhanHoiQuaLon:
                raise LoiDichVu("Phản hồi cookie từ dịch vụ quá lớn", ma=502)
            except TimeoutError:
                raise LoiDichVu("Dịch vụ trình duyệt không trả lời kịp: quá thời gian đọc", ma=504)
        if r.status_code == 200:
            try:
                kq = json.loads(noi_dung)
            except ValueError:
                kq = None
            if not isinstance(kq, dict) or not isinstance(kq.get("cookie"), str):
                raise LoiDichVu("Phản hồi cookie từ dịch vụ không đúng cấu trúc")
            so = kq.get("so_cookie")
            return {"cookie": kq["cookie"], "so_cookie": so if type(so) is int else None}
        if r.status_code in (401, 403):
            raise LoiDichVu(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
        ma = r.status_code if r.status_code in MA_CHUYEN_TIEP else None
        raise LoiDichVu(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}", ma=ma)

    def trinh_duyet_that(self, ho_so: str) -> TrinhDuyetThat:
        """Dựng bộ điều khiển thật cho một hồ sơ (NV92 P1).

        Lõi (app/khoi_phuc_phien.lay_trinh_duyet_that) tìm module có khả năng
        `trinh_duyet` rồi gọi phương thức này — KHÔNG gọi tên module.
        """
        if not self._dich_vu_url or not self._khoa:
            raise LoiDichVu("Module chưa được cấu hình (dich_vu_url/khoa_dich_vu).")
        return TrinhDuyetThat(self._dich_vu_url, self._khoa, ho_so,
                              kiem_ip=self._kiem_ip)

    async def suc_khoe(self) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            return {
                "ok": False,
                "loi": "Chưa cấu hình dịch vụ",
                "khoa": [],
            }
        khoa_che = _che_khoa(self._khoa)
        try:
            async with self._client(5.0) as client:
                r = await client.get(
                    f"{self._dich_vu_url}/ho-so",
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
                if r.status_code == 200:
                    return {
                        "ok": True,
                        "khoa": [{"khoa": khoa_che, "ok": True}],
                    }
                return {
                    "ok": False,
                    "loi": f"Dịch vụ trả mã {r.status_code}",
                    "khoa": [{"khoa": khoa_che, "ok": False, "loi": f"HTTP {r.status_code}"}],
                }
        except Exception as e:
            return {
                "ok": False,
                "loi": f"Lỗi kết nối ({type(e).__name__})",
                "khoa": [{"khoa": khoa_che, "ok": False, "loi": "Lỗi kết nối"}],
            }

    async def chat(self, body: dict, stream: bool):
        """Phương thức BẮT BUỘC của hợp đồng lớp Module (lõi từ chối nạp nếu thiếu). Module này không có model nào
        (models() rỗng) nên lõi không định tuyến chat tới đây; gọi thẳng thì trả 501 chuẩn OpenAI."""
        than = {"error": {"message": "Module trình duyệt từ xa không phục vụ chat", "type": "invalid_request_error",
                          "code": "not_implemented"}}
        return 501, {"content-type": "application/json"}, json.dumps(than, ensure_ascii=False).encode("utf-8")

    async def models(self) -> list[dict]:
        return []

    async def dung(self) -> None:
        pass
