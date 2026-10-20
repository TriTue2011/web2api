"""Máy chủ dịch vụ phụ camera (NV167) — chụp ảnh + phát tiếng qua loa camera.

Chạy trong container riêng cạnh web2api (xem Dockerfile cùng thư mục), nghe NỘI BỘ
mặc định ``127.0.0.1:8714``, xác thực bằng Bearer <redacted>á riêng (``CAM_KHOA``,
≥ 24 ký tự, từ chối khởi động nếu yếu). Chỉ thư viện chuẩn.

Hợp đồng (module web2api gọi):
- ``POST /dang-ky`` ``{danh_sach: [{id, name, kind, ...}]}`` → ``{ok,
  so_camera}``: module đăng ký bảng camera MỘT lần; dịch vụ kiểm từng bản
  ghi bằng ``chup.kiem_ban_ghi`` (kind/scheme đóng, địa chỉ riêng, GHIM IP)
  và giữ bảng trong bộ nhớ. Một bản ghi xấu → cả lượt đăng ký bị từ chối 400.
- ``POST /chup`` ``{cam_id, cho_ai?}`` → ``{anh_b64}`` hoặc ``{loi}``.
- ``POST /phat`` ``{cam_id, am_thanh_b64, toi_da_giay?}`` → ``{ten, giay}``.
- ``POST /bo_dam/mo`` ``{cam_id}`` → ``{phien}``; ``/bo_dam/them``
  ``{phien, alaw_b64}``; ``/bo_dam/dong`` ``{phien}`` → ``{giay}``.
- ``GET /suc-khoe`` — đường DUY NHẤT không cần khoá (chỉ ``{"ok": true}``),
  ngoại lệ có chủ đích như khuôn van-phong-office.

BẢO MẬT (đặc tả NV167 + soát độc lập C2):
- Lời gọi chỉ mang ``cam_id`` — dịch vụ TRA BẢNG CỦA CHÍNH NÓ, không nhận
  URL/bản ghi theo từng lượt nữa (bản cũ biến dịch vụ thành proxy SSRF:
  URL tuỳ ý tới loopback/metadata/LAN và ``ffmpeg -i`` nhận scheme tuỳ ý).
  Bản ghi (chứa URL có mật khẩu) không bao giờ vào log/phản hồi; lỗi luôn
  dùng câu đã che của ``kenh``/``chup`` (``che_bi_mat``).
- Khoá Bearer kiểm TRƯỚC khi đọc thân (đường đúng khoá vẫn đọc đủ thân
  trước khi trả lời để giữ keep-alive — xem do_POST); so sánh bằng
  ``hmac.compare_digest``.
- ``phat``/``bo_dam`` là hành động NGUY HIỂM ở phía module; ở đây giữ trần
  thời lượng phát (``CAM_PHAT_TOI_DA_GIAY``, mặc định 30 s), giãn cách phát
  (``CAM_PHAT_GIAN_CACH_GIAY``, mặc định 2 s) và TRẦN PHIÊN BỘ ĐÀM: số phiên
  đồng thời (``CAM_BO_DAM_TOI_DA_PHIEN``, 4), hết hạn khi im
  (``CAM_BO_DAM_HET_HAN_GIAY``, 60 s) và tổng thời lượng nói mỗi phiên
  (``CAM_BO_DAM_TONG_GIAY``, 300 s).
- Dịch vụ không lưu ảnh/âm thanh ra đĩa; bảng camera chỉ ở bộ nhớ.
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
BO_DAM_HET_HAN_MAC_DINH = 60.0
BO_DAM_TONG_MAC_DINH = 300.0
BO_DAM_PHIEN_MAC_DINH = 4


def _so_moi(ten: str, mac_dinh: float) -> float:
    try:
        v = float(os.environ.get(ten) or mac_dinh)
        return v if v > 0 else mac_dinh
    except ValueError:
        return mac_dinh


def _so_moi_nguyen(ten: str, mac_dinh: int) -> int:
    try:
        v = int(os.environ.get(ten) or mac_dinh)
        return v if v > 0 else mac_dinh
    except ValueError:
        return mac_dinh


# ── Bảng camera đã đăng ký (hợp đồng C2) ────────────────────────────────────
# Bảng chỉ thay NGUYÊN TỬ sau khi MỌI bản ghi qua kiem_ban_ghi; một bản ghi
# xấu không phá bảng đang chạy tốt. Bảng sống trong bộ nhớ — module đăng ký
# lại sau mỗi lần dịch vụ khởi động (lời gọi cam_id lạ sẽ bị từ chối rõ).

_SO_CAM_KHOA = threading.Lock()
_SO_CAM: dict[str, dict] = {}


def _dang_ky_bang(danh_sach) -> dict:
    if not isinstance(danh_sach, list) or not danh_sach:
        raise ValueError("danh_sach phải là mảng khác rỗng")
    bang: dict[str, dict] = {}
    ten_da_thay: set[str] = set()
    for i, muc in enumerate(danh_sach):
        if not isinstance(muc, dict):
            raise ValueError(f"danh_sach[{i}] phải là một đối tượng")
        id_ = str(muc.get("id") or "").strip()
        if not id_:
            raise ValueError(f"danh_sach[{i}] thiếu id")
        if id_ in bang:
            raise ValueError(f"trùng id camera «{id_}» trong bảng đăng ký")
        ban = dict(muc)
        loi = _chup.kiem_ban_ghi(ban)
        if loi:
            # Thông điệp chỉ gồm tên camera + lý do — không chứa URL/mật khẩu.
            raise ValueError(f"camera «{ban.get('name') or id_}»: {loi}")
        ten = str(ban.get("name") or "")
        if ten in ten_da_thay:
            raise ValueError(f"trùng tên camera «{ten}» trong bảng đăng ký")
        ten_da_thay.add(ten)
        bang[id_] = ban
    with _SO_CAM_KHOA:
        _SO_CAM.clear()
        _SO_CAM.update(bang)
    return {"ok": True, "so_camera": len(bang)}


def _lay_cam(cam_id) -> dict:
    """Tra bản ghi theo cam_id trong bảng ĐÃ KIỂM của dịch vụ."""
    with _SO_CAM_KHOA:
        ban = _SO_CAM.get(str(cam_id or ""))
    if ban is None:
        raise ValueError("camera chưa đăng ký với dịch vụ (module phải gọi /dang-ky trước)")
    return ban


class _BoDam:
    """Phiên bộ đàm đang giữ — ánh xạ phien_id → {bd, ten, tao, cuoi}.

    Trần phiên (C3): số phiên đồng thời, hết hạn khi im, tổng thời lượng
    nói — đọc từ môi trường MỖI LẦN GỌI để vận hành chỉnh không cần sửa mã.
    Bản cũ không có trần nào: phiên mở vô hạn / im vô hạn / nói vô hạn.
    """

    def __init__(self) -> None:
        self._khoa = threading.Lock()
        self._phien: dict[str, dict] = {}

    def _don_phien_het_han(self, bay_gio: float) -> list[_kenh.BoDam]:
        """Gỡ các phiên im quá hạn (gọi khi đang giữ _khoa); trả các BoDam
        phải đóng (đóng ngoài khoá vì dong() có thể chờ mạng)."""
        het = _so_moi("CAM_BO_DAM_HET_HAN_GIAY", BO_DAM_HET_HAN_MAC_DINH)
        phai_dong = []
        for ma in [m for m, p in self._phien.items() if bay_gio - p["cuoi"] > het]:
            phai_dong.append(self._phien.pop(ma)["bd"])
        return phai_dong

    @staticmethod
    def _dong_nhe(ds) -> None:
        for bd in ds:
            try:
                bd.dong()
            except Exception:  # noqa: BLE001 — dọn dẹp không được làm hỏng lượt gọi
                pass

    def mo(self, ten: str, cam: dict) -> str:
        ma = secrets.token_hex(12)
        # _KHOA_PHAT bảo vệ _SO toàn cục (xem server._KHOA_PHAT): dat_so và mở
        # kênh phải nguyên tử, không thì một /phat đồng thời ghi đè sổ.
        with _KHOA_PHAT:
            _kenh.dat_so({ten: cam})
            bd = _kenh.BoDam(ten)
        bay_gio = time.monotonic()
        with self._khoa:
            phai_dong = self._don_phien_het_han(bay_gio)
            toi_da = _so_moi_nguyen("CAM_BO_DAM_TOI_DA_PHIEN", BO_DAM_PHIEN_MAC_DINH)
            if len(self._phien) >= toi_da:
                self._dong_nhe(phai_dong)
                try:
                    bd.dong()
                except Exception:  # noqa: BLE001
                    pass
                raise _kenh.LoiLoa(
                    f"đã đủ {toi_da} phiên bộ đàm đang mở — đóng bớt trước khi mở thêm.")
            self._phien[ma] = {"bd": bd, "ten": ten, "tao": bay_gio, "cuoi": bay_gio}
        self._dong_nhe(phai_dong)
        return ma

    def them(self, ma: str, alaw: bytes) -> None:
        bay_gio = time.monotonic()
        het_han = vuot_tran = False
        with self._khoa:
            muc = self._phien.get(ma)
            if muc is None:
                raise _kenh.LoiLoa("phiên bộ đàm không còn (đã đóng hoặc hết hạn).")
            if bay_gio - muc["cuoi"] > _so_moi("CAM_BO_DAM_HET_HAN_GIAY", BO_DAM_HET_HAN_MAC_DINH):
                het_han = True
                self._phien.pop(ma, None)
            elif muc["bd"].nhan_giay + len(alaw) / 8000.0 > _so_moi(
                    "CAM_BO_DAM_TONG_GIAY", BO_DAM_TONG_MAC_DINH):
                vuot_tran = True
                self._phien.pop(ma, None)
            else:
                muc["cuoi"] = bay_gio
        if het_han or vuot_tran:
            self._dong_nhe([muc["bd"]])
            if het_han:
                raise _kenh.LoiLoa("phiên bộ đàm đã hết hạn vì im quá lâu — mở phiên mới.")
            raise _kenh.LoiLoa(
                f"phiên bộ đàm vượt trần tổng "
                f"{_so_moi('CAM_BO_DAM_TONG_GIAY', BO_DAM_TONG_MAC_DINH):.0f} giây nói — mở phiên mới.")
        muc["bd"].them(alaw)

    def dong(self, ma: str) -> float:
        with self._khoa:
            muc = self._phien.pop(ma, None)
        if muc is None:
            raise _kenh.LoiLoa("phiên bộ đàm không còn (đã đóng hoặc hết hạn).")
        muc["bd"].dong()
        return round(muc["bd"].giay, 1)


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

    def _json(self, ma: int, than: dict, dong: bool = False) -> None:
        b = json.dumps(than, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        if dong:
            self.send_header("Connection", "close")
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
        try:
            dai = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            dai = 0
        if dai < 0 or dai > TRAN_THAN:
            self.close_connection = True
            self._json(413, {"loi": "than_qua_lon"})
            return
        # Kiểm khoá TRƯỚC khi đọc thân (C4): bản cũ đọc hết 4 MB rồi mới 401
        # — kẻ không có khoá vẫn bắt dịch vụ nuốt thân (khuếch đại theo số
        # kết nối). Khoá sai: chỉ rút TỐI ĐA 1 KB trong tối đa 1 giây rồi
        # đóng kết nối. Việc đọc-trước nguyên bản tồn tại vì keep-alive
        # (thân chưa đọc làm hỏng yêu cầu sau trên cùng kết nối — đo thật
        # 2026-10-20 httpx nhận 501): đường ĐÚNG khoá vẫn đọc đủ thân
        # trước khi trả lời ở ngay dưới, giữ nguyên hành vi đó.
        if not self._kiem_khoa():
            if dai:
                try:
                    self.connection.settimeout(1.0)
                    self.rfile.read(min(dai, 1024))
                except (TimeoutError, OSError):
                    pass
                finally:
                    try:
                        self.connection.settimeout(None)
                    except OSError:
                        pass
            self.close_connection = True
            self._json(401, {"loi": "sai_khoa"}, dong=True)
            return
        self._than_bytes = self.rfile.read(dai) if dai else b""
        try:
            if duong == "/dang-ky":
                self._dang_ky()
            elif duong == "/chup":
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
    def _dang_ky(self):
        than = self._doc_than(TRAN_THAN)
        self._json(200, _dang_ky_bang(than.get("danh_sach")))

    def _chup(self):
        than = self._doc_than(TRAN_THAN_CHUP)
        cam = _lay_cam(than.get("cam_id"))
        cho_ai = bool(than.get("cho_ai"))
        jpeg = _chup.chup(str(cam.get("name") or "camera"), cam, cho_ai=cho_ai)
        self._json(200, {"ok": True, "anh_b64": base64.b64encode(jpeg).decode("ascii"),
                         "dinh_dang": "image/jpeg"})

    def _phat(self):
        than = self._doc_than(TRAN_THAN)
        cam = _lay_cam(than.get("cam_id"))
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
        cam = _lay_cam(than.get("cam_id"))
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
