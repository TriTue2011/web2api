"""Máy chủ HTTP giả lập googlevideo cho test rơ-le chịu lỗi — NV153 đợt A.

Chạy trên 127.0.0.1 (conftest cho phép loopback, chặn mọi host khác). Phục vụ
``du_lieu`` (bytes) theo kịch bản ``kich_ban``; đếm mọi request vào ``.dem``
để test assert "không thêm request" khi client abort / dịch vụ dừng.

Kịch bản:
  binh_thuong            206 chuẩn (200 khi không có Range)
  cat_giua_khuc          lần phục vụ đầu cắt giữa khúc (chỉ gửi một phần rồi
                         ngắt kết nối đột ngột), các lần sau bình thường
  cat_truoc_ranh_gioi_4MiB  cắt ở đúng (4MiB - 64) — ngay trước ranh giới khúc
  cat_sau_ranh_gioi      khúc đầu đủ; cắt ở (4MiB + 64) khi mở khúc kế
  3_lan_lien_tiep        3 lần phục vụ đầu đều cắt, lần 4 trở đi bình thường
  403_khi_mo_lai         lần đầu cắt; mở lại bằng URL cũ -> 403; khi URL đổi
                         (query ``moi=1``, tức đã "giải lại link") -> 206 đủ
  200_thay_vi_206        lần mở lại đầu (tu > 0) trả 200 cả tệp thay vì 206;
                         các lần sau 206 chuẩn
  content_range_lech     lần mở lại đầu trả 206 nhưng Content-Range bắt đầu
                         lệch (tu + 64); các lần sau chuẩn
  doi_tong_co            lần mở lại đầu trả 206 nhưng tổng trong Content-Range
                         đổi (tong + 4096); các lần sau chuẩn
  qua_5_lan              MỌI lần phục vụ đều cắt -> hết 5 lần nối lại
"""

from __future__ import annotations

import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

KHUC = 4 * 1024 * 1024
_RANGE = re.compile(r"bytes=(\d+)-(\d*)$")
_DIEM_CAT = 131072  # điểm cắt mặc định: 2 miếng 64 KiB sau đầu khúc
_MIENG_GUI = 64 * 1024


class _TrinhXuLy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GoogleVideoGia/1"

    def log_message(self, *args):  # giữ output test sạch
        pass

    def _tang_dem(self) -> int:
        gia = self.server.gia
        with gia.khoa:
            gia.dem += 1
            return gia.dem

    def do_GET(self):
        try:
            self._phuc_vu()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True

    def _phuc_vu(self):
        gia = self.server.gia
        lan = self._tang_dem()
        du_lieu = gia.du_lieu
        tong = len(du_lieu)
        m = _RANGE.fullmatch((self.headers.get("Range") or "").strip())
        if m:
            tu = int(m.group(1))
            den = int(m.group(2)) if m.group(2) else tong - 1
        else:
            tu, den = 0, tong - 1
        den = min(den, tong - 1)
        if tu >= tong:
            self._loi(416)
            return

        kich_ban = gia.kich_ban
        moi = parse_qs(urlsplit(self.path).query).get("moi", [""])[0] == "1"

        if kich_ban == "403_khi_mo_lai":
            if moi:
                self._gui_206(tu, den, tong)
            elif lan == 1:
                self._cat(tu, den, tong, tu + _DIEM_CAT)
            else:
                self._loi(403)
            return
        if kich_ban == "qua_5_lan":
            self._cat(tu, den, tong, tu + _DIEM_CAT)
            return
        if kich_ban == "3_lan_lien_tiep":
            if lan <= 3:
                self._cat(tu, den, tong, tu + _DIEM_CAT)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "cat_giua_khuc":
            if lan == 1:
                self._cat(tu, den, tong, tu + _DIEM_CAT)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "cat_truoc_ranh_gioi_4MiB":
            if lan == 1 and tu == 0:
                self._cat(tu, den, tong, KHUC - 64)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "cat_sau_ranh_gioi":
            if lan == 1:
                self._gui_206(tu, den, tong)
            elif tu == KHUC:
                self._cat(tu, den, tong, KHUC + 64)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "200_thay_vi_206":
            # khúc đầu đủ bình thường; khi mở khúc KẾ, thượng nguồn trả 200 cả
            # tệp thay vì 206 -> bản sai, tính 1 thất bại, KHÔNG gửi byte nào
            # của nó; mở lại -> 206 chuẩn -> vẫn đủ byte.
            if lan == 1:
                self._gui_206(tu, den, tong)
            elif lan == 2:
                self._gui_200_ca_tep(tong)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "content_range_lech":
            if lan == 1:
                self._gui_206(tu, den, tong)
            elif lan == 2:
                self._gui_206(tu, den, tong, dau_sai=tu + 64)
            else:
                self._gui_206(tu, den, tong)
            return
        if kich_ban == "doi_tong_co":
            if lan == 1:
                self._gui_206(tu, den, tong)
            elif lan == 2:
                self._gui_206(tu, den, tong, tong_sai=tong + 4096)
            else:
                self._gui_206(tu, den, tong)
            return
        # binh_thuong (và mọi tên lạ): 206 khi có Range, 200 cả tệp khi không.
        if m:
            self._gui_206(tu, den, tong)
        else:
            self._gui_200_ca_tep(tong)

    def _gui_tung_mieng(self, khoi: memoryview):
        """Ghi theo miếng 64 KiB (memoryview: không copy); sleep nếu truyền chậm."""
        cham = self.server.gia.truyen_cham_moi_mieng
        if not cham:
            self.wfile.write(khoi)
            return
        for i in range(0, len(khoi), _MIENG_GUI):
            self.wfile.write(khoi[i : i + _MIENG_GUI])
            self.wfile.flush()
            time.sleep(cham)

    def _gui_206(self, tu, den, tong, dau_sai=None, tong_sai=None):
        gia = self.server.gia
        body = memoryview(gia.du_lieu)[tu : den + 1]
        dau = tu if dau_sai is None else dau_sai
        t = tong if tong_sai is None else tong_sai
        self.send_response(206)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Range", f"bytes {dau}-{den}/{t}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._gui_tung_mieng(body)
        body.release()

    def _gui_200_ca_tep(self, tong):
        gia = self.server.gia
        self.send_response(200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(tong))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        khoi = memoryview(gia.du_lieu)
        try:
            self._gui_tung_mieng(khoi)
        finally:
            khoi.release()

    def _cat(self, tu, den, tong, diem_cat):
        """Hứa đủ cả khúc nhưng chỉ gửi tới diem_cat rồi ngắt kết nối đột ngột."""
        gia = self.server.gia
        diem_cat = max(tu + 1, min(diem_cat, den))
        self.send_response(206)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Range", f"bytes {tu}-{den}/{tong}")
        self.send_header("Content-Length", str(den - tu + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        khoi = memoryview(gia.du_lieu)[tu:diem_cat]
        try:
            self.wfile.write(khoi)
            self.wfile.flush()
        finally:
            khoi.release()
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _loi(self, ma: int):
        self.send_response(ma)
        self.send_header("Content-Length", "0")
        self.end_headers()


class MayChuGoogleVideoGia:
    """Máy chủ googlevideo giả: ``bat_dau()`` -> url, ``dung()`` dọn dẹp."""

    def __init__(self, kich_ban: str, du_lieu: bytes, truyen_cham_moi_mieng: float = 0.0):
        self.kich_ban = kich_ban
        self.du_lieu = bytes(du_lieu)
        #: số giây sleep sau mỗi miếng 64 KiB — giả lập nguồn chậm để test
        #: dừng-hẳn quan sát được luồng bị ngắt giữa chừng một cách ổn định.
        self.truyen_cham_moi_mieng = truyen_cham_moi_mieng
        self.dem = 0
        self.co: dict = {}
        self.khoa = threading.Lock()
        self.url = ""
        self._may_chu: ThreadingHTTPServer | None = None
        self._luong: threading.Thread | None = None

    def bat_dau(self) -> str:
        may_chu = ThreadingHTTPServer(("127.0.0.1", 0), _TrinhXuLy)
        may_chu.daemon_threads = True
        may_chu.gia = self  # type: ignore[attr-defined]
        self._may_chu = may_chu
        cong = may_chu.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{cong}/videoplayback?x=1"
        self._luong = threading.Thread(
            target=may_chu.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
            name="googlevideo-gia",
        )
        self._luong.start()
        return self.url

    def dung(self) -> None:
        if self._may_chu is not None:
            self._may_chu.shutdown()
            self._may_chu.server_close()
            self._may_chu = None
