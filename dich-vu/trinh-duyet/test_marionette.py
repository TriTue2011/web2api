"""Kiểm thử máy khách Marionette tối thiểu (marionette.py) — NV92 P1.

Máy chủ Marionette GIẢ nói đúng giao thức khung THẬT của Firefox (``<độ dài thập phân>:<JSON>``,
đã đo bằng Firefox thật: lời chào ``50:{"applicationType":"gecko","marionetteProtocol":3}``),
không cần Firefox thật, không Internet.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

GOC_DICH_VU = Path(__file__).resolve().parent
if str(GOC_DICH_VU) not in sys.path:
    sys.path.insert(0, str(GOC_DICH_VU))

from marionette import KHOA_PHAN_TU, LoiMarionette, MarionetteClient


def _gui_khung(conn: socket.socket, obj) -> None:
    du_lieu = json.dumps(obj).encode("utf-8")
    conn.sendall(str(len(du_lieu)).encode("ascii") + b":" + du_lieu)


def _doc_khung(conn: socket.socket):
    chu_so = b""
    while True:
        b = conn.recv(1)
        if not b:
            raise ConnectionError("đóng giữa chừng")
        if b == b":":
            break
        chu_so += b
    dai = int(chu_so)
    du_lieu = b""
    while len(du_lieu) < dai:
        khuc = conn.recv(min(65536, dai - len(du_lieu)))
        if not khuc:
            raise ConnectionError("đóng giữa chừng")
        du_lieu += khuc
    return json.loads(du_lieu.decode("utf-8"))


class MayChuMarionetteGia:
    """Máy chủ giả: lời chào + kịch bản phản hồi theo tên lệnh."""

    def __init__(self, kich_ban: dict | None = None):
        self.kich_ban = kich_ban or {}
        self.da_nhan: list = []
        self._sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(1)
        self.port = self._sk.getsockname()[1]
        self._luong = threading.Thread(target=self._phuc_vu, daemon=True)

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *a):
        try:
            self._sk.close()
        except OSError:
            pass
        self._luong.join(timeout=5)

    def _phuc_vu(self):
        try:
            conn, _ = self._sk.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            _gui_khung(conn, [0, 1, None, {"applicationType": "gecko",
                                           "marionetteProtocol": 3}])
            while True:
                try:
                    yeu_cau = _doc_khung(conn)
                except (ConnectionError, OSError):
                    break
                self.da_nhan.append(yeu_cau)
                _loai, msg_id, lenh, tham_so = yeu_cau
                tra_loi = self.kich_ban.get(lenh)
                if lenh not in self.kich_ban:
                    # Firefox thật báo «unknown command» cho tên lệnh không có (bản đầu dùng tên kiểu Selenium
                    # ClickElement/SendKeysToElement mà máy chủ giả im lặng chấp nhận → test xanh, ngoài đời hỏng).
                    tra_loi = ({"error": "unknown command", "message": "", "stacktrace": ""}, None)
                if callable(tra_loi):
                    loi, ket_qua = tra_loi(tham_so)
                elif tra_loi is None:
                    loi, ket_qua = None, None
                else:
                    loi, ket_qua = tra_loi
                try:
                    _gui_khung(conn, [1, msg_id, loi, ket_qua])
                except OSError:
                    break


def _may_chu_co_ban() -> MayChuMarionetteGia:
    phan_tu = {KHOA_PHAN_TU: "uuid-phan-tu-1"}
    return MayChuMarionetteGia({
        "WebDriver:NewSession": (None, {"sessionId": "phien-1",
                                        "capabilities": {"browserName": "firefox",
                                                         "moz:profile": "/tmp/ho-so-gia"}}),
        "WebDriver:Navigate": (None, None),
        "WebDriver:GetCurrentURL": (None, {"value": "https://accounts.google.com/"}),
        "WebDriver:GetTitle": (None, {"value": "Đăng nhập"}),
        "WebDriver:GetPageSource": (None, {"value": "<html></html>"}),
        "WebDriver:FindElement": (None, {"value": phan_tu}),
        "WebDriver:ElementClick": (None, None),
        "WebDriver:ElementSendKeys": (None, None),
        "WebDriver:ElementClear": (None, None),
        # Firefox thật trả DANH SÁCH trực tiếp cho GetCookies (không bọc {"value": …}); đo 18/10/2026.
        "WebDriver:GetCookies": (None, [
            {"name": "SID", "value": "bi-mat-gia", "domain": ".google.com"}]),
        "WebDriver:DeleteSession": (None, None),
    })


def test_ket_noi_va_phien_moi():
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            phien = kh.phien_moi()
            assert phien == "phien-1"
            # lệnh gửi đi đúng khung [0, id, tên, tham số]
            lenh_moi = [y for y in may_chu.da_nhan if y[2] == "WebDriver:NewSession"]
            assert len(lenh_moi) == 1 and lenh_moi[0][0] == 0
        finally:
            kh.dong()
    assert not kh.da_noi


def test_dieu_huong_va_doc_trang():
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.dieu_huong("https://accounts.google.com/")
            assert kh.url_hien_tai() == "https://accounts.google.com/"
            assert kh.tieu_de() == "Đăng nhập"
            assert kh.nguon_trang() == "<html></html>"
            dieu_huong = [y for y in may_chu.da_nhan if y[2] == "WebDriver:Navigate"]
            assert dieu_huong[0][3] == {"url": "https://accounts.google.com/"}
        finally:
            kh.dong()


def test_dien_bam_gui_dung_tham_chieu_phan_tu():
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            kh.dien("input[type=email]", "a@example.com")
            kh.bam("#tiep-tuc")
            tim = [y for y in may_chu.da_nhan if y[2] == "WebDriver:FindElement"]
            assert tim[0][3] == {"using": "css selector", "value": "input[type=email]"}
            gui_phim = [y for y in may_chu.da_nhan if y[2] == "WebDriver:ElementSendKeys"]
            assert gui_phim[0][3]["text"] == "a@example.com"
            assert gui_phim[0][3]["id"] == "uuid-phan-tu-1"          # CHUỖI định danh, không phải đối tượng tham chiếu
            bam = [y for y in may_chu.da_nhan if y[2] == "WebDriver:ElementClick"]
            assert bam[0][3]["id"] == "uuid-phan-tu-1"
        finally:
            kh.dong()


def test_doc_cookie():
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            ck = kh.cookie()
            assert ck == [{"name": "SID", "value": "bi-mat-gia",
                           "domain": ".google.com"}]
        finally:
            kh.dong()


def test_loi_no_such_element_thanh_loi_co_ma():
    def _khong_thay(tham_so):
        return ({"error": "no such element", "message": "không thấy",
                 "stacktrace": ""}, None)

    with MayChuMarionetteGia({
        "WebDriver:NewSession": (None, {"sessionId": "s", "capabilities": {}}),
        "WebDriver:FindElement": _khong_thay,
        "WebDriver:DeleteSession": (None, None),
    }) as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            with pytest.raises(LoiMarionette) as exc:
                kh.tim_phan_tu("input#khong-co", timeout=0.2)
            assert exc.value.ma_loi == "no such element"
            # câu lỗi thô của trang không lọt vào ngoại lệ
            assert "không thấy" not in str(exc.value)
        finally:
            kh.dong()


def test_cho_phan_tu_het_gio_tra_false():
    def _khong_thay(tham_so):
        return ({"error": "no such element", "message": "", "stacktrace": ""}, None)

    with MayChuMarionetteGia({
        "WebDriver:NewSession": (None, {"sessionId": "s", "capabilities": {}}),
        "WebDriver:FindElement": _khong_thay,
        "WebDriver:DeleteSession": (None, None),
    }) as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            assert kh.cho_phan_tu("#mai-khong-co", giay=0.3) is False
        finally:
            kh.dong()


def test_ket_noi_that_bai_nem_loi():
    kh = MarionetteClient(port=1)  # cổng đóng
    with pytest.raises(LoiMarionette) as exc:
        kh.ket_noi()
    assert exc.value.ma_loi == "ket_noi_that_bai"


def test_xoa_phien_gui_delete_session():
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        kh.phien_moi()
        kh.xoa_phien()
        kh.dong()
        xoa = [y for y in may_chu.da_nhan if y[2] == "WebDriver:DeleteSession"]
        assert len(xoa) == 1


# ── NV94-P3: khớp id phản hồi / bỏ phản hồi cũ / đóng khi hết giờ ──────────

class MayChuTraLoiCu:
    """Máy chủ gửi TRƯỚC một phản hồi muộn (id cũ) rồi mới phản hồi đúng."""

    def __init__(self):
        self._sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(1)
        self.port = self._sk.getsockname()[1]
        self._luong = threading.Thread(target=self._phuc_vu, daemon=True)

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *a):
        try:
            self._sk.close()
        except OSError:
            pass
        self._luong.join(timeout=5)

    def _phuc_vu(self):
        try:
            conn, _ = self._sk.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            _gui_khung(conn, [0, 1, None, {"applicationType": "gecko",
                                           "marionetteProtocol": 3}])
            try:
                yeu_cau = _doc_khung(conn)
            except (ConnectionError, OSError):
                return
            _loai, msg_id, _lenh, _ts = yeu_cau
            # Phản hồi muộn của lệnh cũ (id không khớp) rồi mới phản hồi đúng.
            _gui_khung(conn, [1, msg_id + 1000, None, {"value": "CU"}])
            _gui_khung(conn, [1, msg_id, None, {"value": "MOI"}])


def test_lenh_bo_qua_phan_hoi_cu_va_khop_id():
    with MayChuTraLoiCu() as may_chu:
        khach = MarionetteClient(port=may_chu.port)
        khach.ket_noi()
        try:
            kq = khach.lenh("WebDriver:GetTitle")
            assert (kq or {}).get("value") == "MOI", \
                "đọc nhầm phản hồi muộn của lệnh cũ"
        finally:
            khach.dong()


class MayChuImLang:
    """Máy chủ chào rồi im lặng — lệnh phải hết giờ và ĐÓNG socket."""

    def __init__(self):
        self._sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind(("127.0.0.1", 0))
        self._sk.listen(1)
        self.port = self._sk.getsockname()[1]
        self._luong = threading.Thread(target=self._phuc_vu, daemon=True)

    def __enter__(self):
        self._luong.start()
        return self

    def __exit__(self, *a):
        try:
            self._sk.close()
        except OSError:
            pass
        self._luong.join(timeout=5)

    def _phuc_vu(self):
        try:
            conn, _ = self._sk.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            _gui_khung(conn, [0, 1, None, {"applicationType": "gecko",
                                           "marionetteProtocol": 3}])
            try:
                _doc_khung(conn)  # đọc lệnh rồi im lặng
                threading.Event().wait(10)
            except (ConnectionError, OSError):
                pass


def test_lenh_het_gio_thi_dong_socket():
    with MayChuImLang() as may_chu:
        khach = MarionetteClient(port=may_chu.port)
        khach.ket_noi()
        with pytest.raises(LoiMarionette):
            khach.lenh("WebDriver:GetTitle", het_gio=0.5)
        assert not khach.da_noi, "hết giờ mà socket vẫn mở"


# ── NV94-P3: xác minh moz:profile trong NewSession ─────────────────────────

def test_phien_moi_doc_moz_profile():
    """NewSession trả capabilities có moz:profile — client đọc ra để dịch vụ
    xác minh phiên đúng là hồ sơ đã yêu cầu."""
    with _may_chu_co_ban() as may_chu:
        khach = MarionetteClient(port=may_chu.port)
        khach.ket_noi()
        try:
            khach.phien_moi()
            assert khach.moz_profile == "/tmp/ho-so-gia", \
                f"không đọc được moz:profile: {khach.moz_profile!r}"
        finally:
            khach.dong()


# ── Giao thức khung THẬT của Firefox (đo bằng Firefox thật 18/10/2026) ──

def _may_chu_tho(byte_gui: bytes):
    """Mở cổng, chấp nhận một kết nối, gửi nguyên `byte_gui` rồi đóng. Trả (cổng, luồng)."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sk.bind(("127.0.0.1", 0))
    sk.listen(1)

    def chay():
        try:
            conn, _ = sk.accept()
            conn.sendall(byte_gui)
            conn.close()
        finally:
            sk.close()

    t = threading.Thread(target=chay, daemon=True)
    t.start()
    return sk.getsockname()[1], t


def test_loi_chao_that_cua_firefox_doc_duoc():
    """Đúng chuỗi byte Firefox thật gửi khi nối: «50:{...}». Bản đầu đọc «50:{» như số nguyên 892353147."""
    port, t = _may_chu_tho(b'50:{"applicationType":"gecko","marionetteProtocol":3}')
    c = MarionetteClient(port=port)
    c.ket_noi()                                  # không ném lỗi
    assert c.da_noi
    c.dong()
    t.join(2)


@pytest.mark.parametrize("byte_gui,ma_loi", [
    (b"\x00\x00\x00\x32" + b"x" * 50, "khung_sai_dinh_dang"),      # tiền tố nhị phân kiểu cũ
    (b"abc:{}", "khung_sai_dinh_dang"),                              # độ dài không phải số
    (b":{}", "khung_sai_dinh_dang"),                                 # thiếu độ dài
    (b"12345678901:{}", "khung_sai_dinh_dang"),                      # quá nhiều chữ số (rác vô hạn)
    (b"99999999:{}", "khung_qua_lon"),                               # vượt trần 64 MiB
])
def test_khung_sai_bi_tu_choi_bang_ma_loi(byte_gui, ma_loi):
    port, t = _may_chu_tho(byte_gui)
    c = MarionetteClient(port=port)
    with pytest.raises(LoiMarionette) as e:
        c.ket_noi()
    assert e.value.ma_loi == ma_loi
    t.join(2)


def test_do_dai_tinh_theo_byte_utf8_khong_theo_ky_tu():
    """Độ dài là số BYTE UTF-8: «Tiếng Việt» dài hơn số ký tự."""
    nd = json.dumps({"ten": "Tiếng Việt"}, ensure_ascii=False).encode("utf-8")
    port, t = _may_chu_tho(str(len(nd)).encode() + b":" + nd)
    c = MarionetteClient(port=port)
    c.ket_noi()
    c.dong()
    t.join(2)


def test_lenh_la_bi_tu_choi_nhu_firefox_that():
    """Tên lệnh kiểu Selenium (ClickElement…) KHÔNG tồn tại trong Marionette: máy chủ giả phải trả «unknown command»."""
    with _may_chu_co_ban() as may_chu:
        kh = MarionetteClient(port=may_chu.port)
        kh.ket_noi()
        try:
            kh.phien_moi()
            with pytest.raises(LoiMarionette) as e:
                kh.lenh("WebDriver:ClickElement", {"id": "x"})
            assert e.value.ma_loi == "unknown command"
        finally:
            kh.dong()
