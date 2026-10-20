"""Máy chủ dịch vụ phụ camera (NV167) — chụp ảnh + phát tiếng qua loa camera.

Chạy trong container riêng cạnh web2api (xem Dockerfile cùng thư mục), nghe NỘI BỘ
mặc định ``127.0.0.1:8714``, xác thực bằng Bearer <redacted>á riêng (``CAM_KHOA``,
≥ 24 ký tự, từ chối khởi động nếu yếu). Chỉ thư viện chuẩn.

Hợp đồng (module web2api gọi):
- ``POST /chup`` ``{cam, cho_ai?}`` → JPEG thô (``image/jpeg``) hoặc ``{loi}``.
- ``POST /phat`` ``{cam, am_thanh_b64, toi_da_giay?}`` → ``{ten, giay}``.
- ``POST /bo_dam/mo`` ``{cam}`` → ``{phien}``; ``/bo_dam/them``
  ``{phien, alaw_b64}``; ``/bo_dam/dong`` ``{phien}`` → ``{giay}``.
- ``GET /suc-khoe`` — đường DUY NHẤT không cần khoá (chỉ ``{"ok": true}``),
  ngoại lệ có chủ đích như khuôn van-phong-office.

BẢO MẬT (đặc tả NV167):
- Bản ghi camera trong thân yêu cầu chứa URL có mật khẩu — không bao giờ vào
  log/phản hồi; lỗi luôn dùng câu đã che của ``kenh``/``chup`` (``che_bi_mat``).
- Dịch vụ không giữ cấu hình camera, không lưu ảnh/âm thanh ra đĩa.
- ``phat``/``bo_dam`` là hành động GHI: khoá Bearer bắt buộc + trần thời lượng
  (``CAM_PHAT_TOI_DA_GIAY``, mặc định 30 s) và giãn cách tối thiểu giữa hai lần
  phát (``CAM_PHAT_GIAN_CACH_GIAY``, mặc định 2 s) — module còn một lớp cổng
  nữa (chỉ khi bật ở cấu hình module mới có thực thể ghi).
- Đăng nhập/Bearer so bằng ``hmac.compare_digest``.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chup as _chup
import kenh as _kenh

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dich_vu.camera")

TRAN_THAN = 4 * 1024 * 1024          # âm thanh 30 s WAV ~ 1 MB là dư; 4 MB chặn lạm dụng
TRAN_THAN_CHUP = 64 * 1024
TOI_DA_PHAT_MAC_DINH = 30.0
GIAN_CACH_PHAT_MAC_DINH = 2.0


def _so_moi(ten: str, mac_dinh: float) -> float:
    try:
        v = float(os.environ.get(ten) or mac_dinh)
        return v if v > 0 else mac_dinh
    except ValueError:
        return mac_dinh


class _BoDam:
    """Phiên bộ đàm đang giữ — ánh xạ phien_id → (BoDam, tên camera)."""

    def __init__(self) -> None:
        self._khoa = threading.Lock()
        self._phien: dict[str, tuple[_kenh.BoDam, str]] = {}

    def mo(self, ten: str, cam: dict) -> str:
        ma = secrets.token_hex(12)
        # _KHOA_PHAT bảo vệ _SO toàn cục (xem server._KHOA_PHAT): dat_so và mở
        # kênh phải nguyên tử, không thì một /phat đồng thời ghi đè sổ.
        with _KHOA_PHAT:
            _kenh.dat_so({ten: cam})
            bd = _kenh.BoDam(ten)
        with self._khoa:
            self._phien[ma] = (bd, ten)
        return ma

    def them(self, ma: str, alaw: bytes) -> None:
        with self._khoa:
            muc = self._phien.get(ma)
        if muc is None:
            raise _kenh.LoiLoa("phiên bộ đàm không còn (đã đóng hoặc hết hạn).")
        muc[0].them(alaw)

    def dong(self, ma: str) -> float:
        with self._khoa:
            muc = self._phien.pop(ma, None)
        if muc is None:
            raise _kenh.LoiLoa("phiên bộ đàm không còn (đã đóng hoặc hết hạn).")
        muc[0].dong()
        return round(muc[0].giay, 1)


_BO_DAM = _BoDam()
_PHAT_LAN_CUOI: dict[str, float] = {}
_KHOA_PHAT = threading.Lock()


def _kiem_gian_cach(ten: str) -> None:
    gian = _so_moi("CAM_PHAT_GIAN_CACH_GIAY", GIAN_CACH_PHAT_MAC_DINH)
    with _KHOA_PHAT:
        con = _PHAT_LAN_CUOI.get(ten, 0.0) + gian - time.monotonic()
        _PHAT_LAN_CUOI[ten] = time.monotonic()
    if con > 0:
        raise _kenh.LoiLoa(f"phát quá dày — chờ thêm {con:.1f} giây (giới hạn tần suất).")


class _XuLy(BaseHTTPRequestHandler):
    server_version = "camera-dv/1.0"
    protocol_version = "HTTP/1.1"

    # ── tiện ích ──
    def log_message(self, fmt, *args):  # noqa: A003 — chặn log mặc định (không ghi đường có tham số)
        log.info("%s %s", self.command, self.path.split("?", 1)[0])

    def _json(self, ma: int, than: dict) -> None:
        b = json.dumps(than, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _kiem_khoa(self) -> bool:
        khoa = self.server.khoa  # type: ignore[attr-defined]
        gui = self.headers.get("Authorization") or ""
        if gui.startswith("Bearer "):
            gui = gui[7:]
        return hmac.compare_digest(gui.encode(), khoa.encode())

    def _doc_than(self, tran: int) -> dict:
        than_bytes = getattr(self, "_than_bytes", b"")
        if len(than_bytes) > tran:
            raise ValueError("thân yêu cầu quá lớn cho đường này")
        if not than_bytes:
            raise ValueError("thân yêu cầu rỗng")
        than = json.loads(than_bytes.decode("utf-8"))
        if not isinstance(than, dict):
            raise ValueError("thân phải là đối tượng JSON")
        return than

    # ── điều phối ──
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/suc-khoe":
            self._json(200, {"ok": True})
            return
        self._json(404, {"loi": "khong_co_duong_nay"})

    def do_POST(self):
        duong = self.path.split("?", 1)[0]
        # Đọc thân TRƯỚC khi trả lời: HTTP/1.1 giữ kết nối (keep-alive), thân
        # chưa đọc sẽ còn trong bộ đệm và làm hỏng yêu cầu tiếp theo trên cùng
        # kết nối (đo thật 2026-10-20: httpx dùng chung kết nối nhận 501).
        try:
            dai = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            dai = 0
        if dai < 0 or dai > TRAN_THAN:
            self.close_connection = True
            self._json(413, {"loi": "than_qua_lon"})
            return
        self._than_bytes = self.rfile.read(dai) if dai else b""
        if not self._kiem_khoa():
            self._json(401, {"loi": "sai_khoa"})
            return
        try:
            if duong == "/chup":
                self._chup()
            elif duong == "/phat":
                self._phat()
            elif duong == "/bo_dam/mo":
                self._bo_dam_mo()
            elif duong == "/bo_dam/them":
                self._bo_dam_them()
            elif duong == "/bo_dam/dong":
                self._bo_dam_dong()
            else:
                self._json(404, {"loi": "khong_co_duong_nay"})
        except (_kenh.LoiLoa, _chup.LoiCamera) as exc:
            self._json(422, {"loi": str(exc)[:200]})
        except ValueError as exc:
            self._json(400, {"loi": str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001 — không lộ chi tiết nội bộ
            log.warning("loi_khong_xac_dinh: %s", type(exc).__name__)
            self._json(500, {"loi": "loi_dich_vu"})

    # ── từng đường ──
    def _chup(self):
        than = self._doc_than(TRAN_THAN_CHUP)
        cam = than.get("cam")
        if not isinstance(cam, dict):
            raise ValueError("thiếu bản ghi camera (cam)")
        cho_ai = bool(than.get("cho_ai"))
        jpeg = _chup.chup(str(cam.get("name") or "camera"), cam, cho_ai=cho_ai)
        self._json(200, {"ok": True, "anh_b64": base64.b64encode(jpeg).decode("ascii"),
                         "dinh_dang": "image/jpeg"})

    def _phat(self):
        than = self._doc_than(TRAN_THAN)
        cam = than.get("cam")
        if not isinstance(cam, dict):
            raise ValueError("thiếu bản ghi camera (cam)")
        ten = str(cam.get("name") or "camera")
        am_b64 = than.get("am_thanh_b64")
        if not isinstance(am_b64, str) or not am_b64:
            raise ValueError("thiếu âm thanh (am_thanh_b64)")
        try:
            am = base64.b64decode(am_b64, validate=True)
        except ValueError:
            raise ValueError("am_thanh_b64 không phải base64 hợp lệ") from None
        toi_da = _so_moi("CAM_PHAT_TOI_DA_GIAY", TOI_DA_PHAT_MAC_DINH)
        if than.get("toi_da_giay") is not None:
            try:
                toi_da = min(toi_da, float(than["toi_da_giay"]))
            except (TypeError, ValueError):
                raise ValueError("toi_da_giay phải là số") from None
        if len(am) > 2 * 1024 * 1024:
            raise _kenh.LoiLoa("âm thanh quá lớn (tối đa 2 MB).")
        _kiem_gian_cach(ten)
        with _KHOA_PHAT:
            _kenh.dat_so({ten: cam})
            ket = _kenh.phat_gioi_han(ten, am, toi_da)
        ket["ok"] = True
        self._json(200, ket)

    def _bo_dam_mo(self):
        than = self._doc_than(TRAN_THAN_CHUP)
        cam = than.get("cam")
        if not isinstance(cam, dict):
            raise ValueError("thiếu bản ghi camera (cam)")
        ten = str(cam.get("name") or "camera")
        _kiem_gian_cach(ten)
        ma = _BO_DAM.mo(ten, cam)
        self._json(200, {"ok": True, "phien": ma})

    def _bo_dam_them(self):
        than = self._doc_than(TRAN_THAN)
        phien = str(than.get("phien") or "")
        alaw_b64 = than.get("alaw_b64")
        if not phien or not isinstance(alaw_b64, str):
            raise ValueError("thiếu phien hoặc alaw_b64")
        try:
            alaw = base64.b64decode(alaw_b64, validate=True)
        except ValueError:
            raise ValueError("alaw_b64 không phải base64 hợp lệ") from None
        _BO_DAM.them(phien, alaw)
        self._json(200, {"ok": True})

    def _bo_dam_dong(self):
        than = self._doc_than(TRAN_THAN_CHUP)
        phien = str(than.get("phien") or "")
        giay = _BO_DAM.dong(phien)
        self._json(200, {"ok": True, "giay": giay})


def main() -> None:
    khoa = os.environ.get("CAM_KHOA") or ""
    if len(khoa) < 24 or len(set(khoa)) < 10:
        raise SystemExit("CAM_KHOA phải có ít nhất 24 ký tự và đủ đa dạng — từ chối khởi động.")
    host = os.environ.get("CAM_HOST") or "127.0.0.1"
    cong = int(os.environ.get("CAM_CONG") or 8714)
    srv = ThreadingHTTPServer((host, cong), _XuLy)
    srv.khoa = khoa  # type: ignore[attr-defined]
    log.info("dich vu camera nghe %s:%d", host, cong)
    srv.serve_forever()


if __name__ == "__main__":
    main()
