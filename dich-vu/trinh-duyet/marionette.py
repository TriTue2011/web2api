"""Máy khách Marionette tối thiểu cho dich-vu/trinh-duyet — NV92 P1 (Cách B).

Giao thức điều khiển TÍCH HỢP của Firefox (không thêm gói bên thứ ba):
TCP tới 127.0.0.1:<cổng>, mỗi thông điệp là ``<độ dài>:<JSON>`` — độ dài là
SỐ THẬP PHÂN viết bằng chữ số ASCII (đếm BYTE UTF-8 của JSON), rồi dấu hai chấm
(ví dụ lời chào thật của Firefox: ``50:{"applicationType":"gecko","marionetteProtocol":3}``).
KHÔNG phải tiền tố nhị phân: bản đầu (NV92) giả định 4 byte big-endian, test xanh vì
máy chủ giả cũng giả định thế, nhưng Firefox thật làm «50:{» thành số 892353147 → lỗi
``khung_qua_lon`` (đo bằng Firefox thật 18/10/2026)::

    lệnh     [0, id, "WebDriver:TênLệnh", {tham số}]
    phản hồi [1, id, loi, ket_qua]      (loi = null khi thành công)

Máy chủ gửi một lời chào ngay khi nối — đọc và bỏ qua. Mọi lệnh sau đó
dùng ``WebDriver:NewSession`` để lấy session rồi điều khiển.

AN TOÀN:
- Chỉ nối 127.0.0.1 (loopback trong container); cổng do dịch vụ cấp phát.
- Ngoại lệ chỉ mang MÃ lỗi của giao thức (ví dụ "no such element"),
  KHÔNG mang câu lỗi thô/nội dung trang (có thể chứa bí mật).
- Marionette bật ``navigator.webdriver``: GIỮ NGUYÊN, không che dấu
  (đúng luật cấm né phát hiện — trang nào vì thế đưa thử thách thì
  tầng trên trả «cần người»).

Chạy đồng bộ (blocking); dịch vụ gọi qua ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any

log = logging.getLogger("dich_vu.trinh_duyet.marionette")

#: Khoá tham chiếu phần tử của WebDriver.
KHOA_PHAN_TU = "element-6066-11e4-a52e-4f735466cecf"

#: Hết giờ đọc khung mặc định cho một lệnh (điều hướng có thể lâu).
HET_GIO_LENH_MAC_DINH = 30.0
#: Hết giờ nối TCP.
HET_GIO_KET_NOI = 10.0
#: Trần độ dài một khung (chụp nguồn trang rất lớn vẫn trong mức này).
TRAN_KHUNG_BYTE = 64 * 1024 * 1024
#: Số chữ số tối đa của tiền tố độ dài (64 MiB cần 8 chữ số; chừa dư, chặn rác vô hạn).
TOI_DA_CHU_SO_DO_DAI = 10

#: Số phản hồi cũ tối đa bỏ qua khi chờ đúng id (chống phản hồi muộn của
#: lệnh trước bị đọc nhầm làm kết quả lệnh kế — NV94-P3).
TRAN_BO_QUA_PHAN_HOI_CU = 8



class LoiMarionette(RuntimeError):
    """Lỗi giao thức Marionette. Chỉ mang ``ma_loi`` (mã của giao thức),
    không mang nội dung trang/câu lỗi thô."""

    def __init__(self, ma_loi: str):
        super().__init__(f"Marionette: {ma_loi}")
        self.ma_loi = ma_loi


def _doc_du(sk: socket.socket, so_byte: int, het_gio: float) -> bytes:
    """Đọc đúng ``so_byte`` byte; hết giờ/gãy nối → LoiMarionette."""
    han = time.monotonic() + het_gio
    du_lieu = bytearray()
    while len(du_lieu) < so_byte:
        con_lai = han - time.monotonic()
        if con_lai <= 0:
            raise LoiMarionette("het_gio_doc")
        sk.settimeout(con_lai)
        try:
            khuc = sk.recv(so_byte - len(du_lieu))
        except (socket.timeout, OSError):
            raise LoiMarionette("doc_that_bai") from None
        if not khuc:
            raise LoiMarionette("ket_noi_dong")
        du_lieu += khuc
    return bytes(du_lieu)


class MarionetteClient:
    """Máy khách Marionette đồng bộ, một kết nối TCP cho một phiên."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2828,
                 het_gio_lenh: float = HET_GIO_LENH_MAC_DINH):
        if host != "127.0.0.1":
            raise ValueError("Marionette chỉ được nối qua loopback 127.0.0.1")
        self._host = host
        self._port = int(port)
        self._het_gio_lenh = float(het_gio_lenh)
        self._sk: socket.socket | None = None
        self._id_tiep = 1
        self._phien: str | None = None
        #: Đường dẫn profile Firefox mà phiên đang dùng (từ moz:profile).
        self._moz_profile: str | None = None

    @property
    def moz_profile(self) -> str | None:
        """Đường dẫn profile của phiên (None nếu chưa mở phiên)."""
        return self._moz_profile

    @property
    def da_noi(self) -> bool:
        return self._sk is not None

    def ket_noi(self) -> None:
        """Nối TCP và đọc lời chào của máy chủ (bỏ qua nội dung)."""
        if self._sk is not None:
            return
        try:
            sk = socket.create_connection((self._host, self._port),
                                          timeout=HET_GIO_KET_NOI)
        except OSError:
            raise LoiMarionette("ket_noi_that_bai") from None
        self._sk = sk
        try:
            self._doc_khung(HET_GIO_KET_NOI)  # lời chào — bỏ qua
        except LoiMarionette:
            self.dong()
            raise

    def dong(self) -> None:
        sk, self._sk = self._sk, None
        self._phien = None
        self._moz_profile = None
        if sk is not None:
            try:
                sk.close()
            except OSError:
                pass

    # ── khung ──

    def _doc_khung(self, het_gio: float) -> Any:
        assert self._sk is not None
        # Tiền tố độ dài: chữ số thập phân ASCII tới dấu «:» (tối đa 10 chữ số, đọc từng byte).
        chu_so = bytearray()
        han = time.monotonic() + het_gio
        while True:
            b = _doc_du(self._sk, 1, max(0.0, han - time.monotonic()))
            if b == b":":
                break
            if not b.isdigit() or len(chu_so) >= TOI_DA_CHU_SO_DO_DAI:
                raise LoiMarionette("khung_sai_dinh_dang")
            chu_so += b
        if not chu_so:
            raise LoiMarionette("khung_sai_dinh_dang")
        dai = int(chu_so)
        if dai > TRAN_KHUNG_BYTE:
            raise LoiMarionette("khung_qua_lon")
        try:
            return json.loads(_doc_du(self._sk, dai, het_gio).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise LoiMarionette("khung_sai_dinh_dang") from None

    def _gui_khung(self, obj: Any) -> None:
        assert self._sk is not None
        du_lieu = json.dumps(obj).encode("utf-8")
        try:
            self._sk.sendall(str(len(du_lieu)).encode("ascii") + b":" + du_lieu)
        except OSError:
            raise LoiMarionette("gui_that_bai") from None

    def lenh(self, ten: str, tham_so: dict | None = None,
             het_gio: float | None = None) -> Any:
        """Gửi một lệnh WebDriver; lỗi giao thức → LoiMarionette (chỉ mã lỗi).

        NV94-P3: (1) KHỚP id phản hồi — bỏ qua tối đa TRAN_BO_QUA_PHAN_HOI_CU
        khung có id không khớp (phản hồi muộn của lệnh cũ), không đọc nhầm;
        (2) hết giờ/gãy nối → ĐÓNG socket trước khi ném (không giữ socket nửa
        vời để phản hồi muộn chui vào lệnh kế).
        """
        if self._sk is None:
            raise LoiMarionette("chua_ket_noi")
        msg_id = self._id_tiep
        self._id_tiep += 1
        self._gui_khung([0, msg_id, ten, tham_so or {}])
        han = HET_GIO_LENH_MAC_DINH if het_gio is None else het_gio
        try:
            ket_qua = None
            for _ in range(TRAN_BO_QUA_PHAN_HOI_CU):
                tra_loi = self._doc_khung(han)
                try:
                    _loai, id_ve, loi, ket_qua = tra_loi
                except (TypeError, ValueError):
                    raise LoiMarionette("tra_loi_sai_dinh_dang") from None
                if id_ve == msg_id:
                    break
                # id không khớp: phản hồi muộn của lệnh cũ → bỏ qua, đọc tiếp.
                log.warning("marionette: bỏ phản hồi id lạ (chờ %d, gặp %r)",
                            msg_id, id_ve)
            else:
                raise LoiMarionette("khong_thay_phan_hoi_dung_id")
        except LoiMarionette as e:
            if e.ma_loi in ("het_gio_doc", "doc_that_bai", "ket_noi_dong"):
                self.dong()
            raise
        if loi:
            ma = loi.get("error") if isinstance(loi, dict) else None
            raise LoiMarionette(str(ma) if ma else "loi_khong_ro")
        return ket_qua

    # ── phiên ──

    def phien_moi(self) -> str:
        ket_qua = self.lenh("WebDriver:NewSession", {"capabilities": {}})
        if not isinstance(ket_qua, dict):
            raise LoiMarionette("khong_lay_duoc_phien")
        phien = ket_qua.get("sessionId")
        if not phien:
            raise LoiMarionette("khong_lay_duoc_phien")
        self._phien = str(phien)
        # NV94-P3: đọc moz:profile để dịch vụ xác minh phiên đúng hồ sơ yêu cầu.
        nang_luc = ket_qua.get("capabilities")
        if isinstance(nang_luc, dict):
            mp = nang_luc.get("moz:profile")
            self._moz_profile = str(mp) if mp else None
        return self._phien

    def xoa_phien(self) -> None:
        if self._phien is None:
            return
        try:
            self.lenh("WebDriver:DeleteSession")
        except LoiMarionette:
            pass
        finally:
            self._phien = None

    # ── điều hướng / đọc ──

    def dieu_huong(self, url: str) -> None:
        self.lenh("WebDriver:Navigate", {"url": url}, het_gio=60.0)

    def url_hien_tai(self) -> str:
        ket_qua = self.lenh("WebDriver:GetCurrentURL")
        return str((ket_qua or {}).get("value") or "")

    def tieu_de(self) -> str:
        ket_qua = self.lenh("WebDriver:GetTitle")
        return str((ket_qua or {}).get("value") or "")

    def nguon_trang(self) -> str:
        ket_qua = self.lenh("WebDriver:GetPageSource", het_gio=60.0)
        return str((ket_qua or {}).get("value") or "")

    def cookie(self) -> list[dict]:
        """Toàn bộ cookie của ngữ cảnh hiện tại (dạng danh sách dict)."""
        ket_qua = self.lenh("WebDriver:GetCookies")
        # Firefox thật trả DANH SÁCH trực tiếp (khác mọi lệnh khác bọc trong {"value": …}); đo 18/10/2026. Bản đầu giả định bọc
        # `value` nên AttributeError ('list' has no attribute 'get') khi trang có cookie → dịch vụ trả 500 loi_dieu_khien.
        ds = ket_qua if isinstance(ket_qua, list) else ((ket_qua or {}).get("value") or [])
        return [c for c in ds if isinstance(c, dict)]

    def thuc_thi(self, script: str, doi_so: list | None = None) -> Any:
        ket_qua = self.lenh("WebDriver:ExecuteScript",
                            {"script": script, "args": doi_so or []})
        return (ket_qua or {}).get("value")

    # ── phần tử ──

    def tim_phan_tu(self, bo_chon: str, timeout: float = 10.0) -> dict:
        """Tìm phần tử CSS; không thấy trong ``timeout`` → LoiMarionette."""
        han = time.monotonic() + timeout
        while True:
            try:
                ket_qua = self.lenh("WebDriver:FindElement",
                                    {"using": "css selector", "value": bo_chon})
                phan_tu = (ket_qua or {}).get("value")
                if isinstance(phan_tu, dict) and KHOA_PHAN_TU in phan_tu:
                    return phan_tu
                raise LoiMarionette("phan_tu_sai_dinh_dang")
            except LoiMarionette as e:
                if e.ma_loi != "no such element" or time.monotonic() >= han:
                    raise
                time.sleep(0.3)

    def cho_phan_tu(self, bo_chon: str, giay: float = 10.0) -> bool:
        """Chờ phần tử xuất hiện; True/False (không ném khi hết giờ)."""
        try:
            self.tim_phan_tu(bo_chon, timeout=max(0.0, giay))
            return True
        except LoiMarionette:
            return False

    def bam(self, bo_chon: str, timeout: float = 10.0) -> None:
        phan_tu = self.tim_phan_tu(bo_chon, timeout=timeout)
        self.lenh("WebDriver:ClickElement", {"id": phan_tu})

    def dien(self, bo_chon: str, van_ban: str, timeout: float = 10.0,
             xoa_truoc: bool = True) -> None:
        """Điền văn bản vào ô; ``van_ban`` không bao giờ vào log."""
        phan_tu = self.tim_phan_tu(bo_chon, timeout=timeout)
        if xoa_truoc:
            try:
                self.lenh("WebDriver:ClearElement", {"id": phan_tu})
            except LoiMarionette:
                pass  # ô không xoá được (vd chỉ đọc) thì cứ gõ tiếp
        self.lenh("WebDriver:SendKeysToElement",
                  {"id": phan_tu, "text": van_ban})
