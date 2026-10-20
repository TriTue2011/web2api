"""Test dich-vu/nhin-nha/server.py — detector GIẢ (không cần numpy/cv2/ort).

Server nhận vào MayChuML với detector giả được nạp thẳng thuộc tính `_yolo`/
`_mat` + `doc_anh` giả: đủ để kiểm hợp đồng HTTP, Bearer, trần ảnh, và việc
dữ liệu sinh trắc (vector) chỉ đi đúng một đường /quet.
"""

from __future__ import annotations

import base64
import json
import struct
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as svc  # noqa: E402
import yolo as yolo_mod  # noqa: E402

KHOA = "k" * 24 + "dich-vu-gia"


class _Anh:
    shape = (480, 640, 3)


class _VatThe:
    def as_dict(self, rong, cao):
        return {"nhan": "person", "ten": "người", "diem": 0.9,
                "hop": [1, 2, 3, 4], "hop_ti_le": [0.0, 0.0, 0.0, 0.0],
                "vi_tri": "giữa khung"}


class _YoloGia:
    def phat_hien(self, anh, nguong=0.35):
        return [_VatThe()]


class _MatGia:
    hop = (10.0, 10.0, 130.0, 190.0)
    diem = 0.9
    moc = [[40.0, 40.0], [80.0, 40.0], [60.0, 70.0], [45.0, 100.0], [75.0, 100.0]]
    vector = (lambda: None)  # noqa: E731

    net = 20.0
    chuan = 20.0


def _mat_gia():
    class _V:
        def tobytes(self):
            return struct.pack("<512f", *([1.0] + [0.0] * 511))

    m = _MatGia()
    m.vector = _V()
    return m


class _BoMatGia:
    def phan_tich(self, anh):
        return [_mat_gia()]


@pytest.fixture()
def srv(monkeypatch):
    monkeypatch.setattr(yolo_mod, "doc_anh", lambda b: _Anh())
    ml = svc.MayChuML(thu_muc_model="/khong-co")
    ml._yolo = _YoloGia()
    ml._mat = _BoMatGia()
    lop = type("Lop", (svc.LopXuLy,), {"may_chu_ml": ml, "khoa": KHOA})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lop)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", ml
    httpd.shutdown()
    t.join(timeout=5)


def _goi(url, duong, phuong="GET", than=None, khoa=KHOA, thieu_khoa=False):
    req = urllib.request.Request(url + duong, method=phuong,
                                 data=json.dumps(than).encode() if than is not None else None,
                                 headers={} if thieu_khoa else
                                 {"Authorization": f"Bearer {khoa}",
                                  "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_suc_khoe_can_bearer(srv):
    url, _ = srv
    ma, _ = _goi(url, "/suc-khoe", thieu_khoa=True)
    assert ma == 401


def test_suc_khoe_khong_lo_khoa(srv):
    url, _ = srv
    ma, kq = _goi(url, "/suc-khoe")
    assert ma == 200 and kq["ok"] is True
    assert KHOA not in json.dumps(kq)


def test_quet_tra_vat_the_va_mat(srv):
    url, _ = srv
    ma, kq = _goi(url, "/quet", "POST",
                  {"anh_b64": base64.b64encode(b"anh-gia").decode(), "nhan_mat": True})
    assert ma == 200
    assert kq["vat_the"][0]["nhan"] == "person"
    assert len(kq["mat"]) == 1 and "vector_b64" in kq["mat"][0]


def test_quet_khong_nhan_mat_khong_co_mat(srv):
    url, _ = srv
    ma, kq = _goi(url, "/quet", "POST",
                  {"anh_b64": base64.b64encode(b"anh-gia").decode(), "nhan_mat": False})
    assert ma == 200 and kq["mat"] == []


def test_quet_sai_khoa_401(srv):
    url, _ = srv
    ma, _ = _goi(url, "/quet", "POST",
                 {"anh_b64": base64.b64encode(b"x").decode()}, khoa="x" * 24)
    assert ma == 401


def test_duong_la_404(srv):
    url, _ = srv
    ma, _ = _goi(url, "/du-lieu-mat", "GET")
    assert ma == 404


def test_anh_qua_lon_413(srv):
    url, _ = srv
    ma, _ = _goi(url, "/quet", "POST", {"anh_b64": "A" * 14_000_010})
    assert ma == 413


def test_mo_ta_tieng_viet():
    kq = {"vat_the": [{"ten": "người"}, {"ten": "người"}, {"ten": "chó"}], "mat": [{}]}
    assert svc.MayChuML.mo_ta(kq) == "1 mặt, 2 người, 1 chó"


# ── NV168-SUA N5: đầu vào lạ không được làm sập/treo dịch vụ ─────────────────

import socket as _socket  # noqa: E402
import struct as _struct  # noqa: E402


def _goi_tho(url, than_bytes: bytes, han=5.0) -> bytes:
    """Gửi HTTP thô (header ngoài ASCII / Content-Length lạ) và đọc trả lời."""
    cong = int(url.rsplit(":", 1)[1])
    with _socket.create_connection(("127.0.0.1", cong), timeout=han) as s:
        s.sendall(than_bytes)
        ra = b""
        try:
            while b"\r\n\r\n" not in ra:
                khoi = s.recv(4096)
                if not khoi:
                    break
                ra += khoi
        except _socket.timeout:
            raise AssertionError("dịch vụ treo, không trả lời")
    return ra


def test_n5_auth_khong_ascii_tra_401_sach(srv):
    url, _ = srv
    tho = (b"GET /suc-khoe HTTP/1.1\r\nHost: x\r\n"
           b"Authorization: Bearer kho\xe1-latin1-" + b"k" * 24 + b"\r\n\r\n")
    tra = _goi_tho(url, tho)
    assert tra.startswith(b"HTTP/1.0 401") or tra.startswith(b"HTTP/1.1 401"), tra[:80]


def test_n5_content_length_am_tra_400_khong_treo(srv):
    url, _ = srv
    tho = (b"POST /quet HTTP/1.1\r\nHost: x\r\n"
           b"Authorization: Bearer " + KHOA.encode() + b"\r\n"
           b"Content-Length: -1\r\n\r\n")
    tra = _goi_tho(url, tho)
    assert b" 400 " in tra.split(b"\r\n", 1)[0] + b" ", tra[:80]


def test_n5_content_length_la_tra_400(srv):
    url, _ = srv
    tho = (b"POST /quet HTTP/1.1\r\nHost: x\r\n"
           b"Authorization: Bearer " + KHOA.encode() + b"\r\n"
           b"Content-Length: abc\r\n\r\n")
    tra = _goi_tho(url, tho)
    assert b" 400 " in tra.split(b"\r\n", 1)[0] + b" ", tra[:80]


def _png(rong: int, cao: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + _struct.pack(">I", 13) + b"IHDR"
            + _struct.pack(">II", rong, cao) + b"\x08\x02\x00\x00\x00")


def _jpeg(rong: int, cao: int) -> bytes:
    app0 = b"\xff\xe0" + _struct.pack(">H", 16) + b"\x00" * 14
    sof0 = (b"\xff\xc0" + _struct.pack(">H", 17) + b"\x08"
            + _struct.pack(">HH", cao, rong) + b"\x03" + b"\x00" * 9)
    return b"\xff\xd8" + app0 + sof0


def test_n5_kich_thuoc_anh_tu_tieu_de():
    assert yolo_mod._kich_thuoc_anh(_png(100, 50)) == (100, 50)
    assert yolo_mod._kich_thuoc_anh(_jpeg(640, 480)) == (640, 480)
    assert yolo_mod._kich_thuoc_anh(b"khong phai anh") is None


def test_n5_bom_diem_anh_bi_chan_truoc_giai_ma():
    # 20000x20000 = 400 triệu điểm ảnh trong tệp vài chục byte: phải bị chặn
    # ở bước đọc tiêu đề, TRƯỚC khi cv2 giải mã (máy test không có cv2).
    for du_lieu in (_png(20000, 20000), _jpeg(20000, 20000)):
        with pytest.raises(ValueError) as e:
            yolo_mod.doc_anh(du_lieu)
        assert "điểm ảnh" in str(e.value)


def test_n5_kiem_lai_sha256_model_khi_nap(tmp_path):
    import khuon_mat as km
    # Tệp model bị thay (nội dung rác) → nạp phải từ chối bằng sha256,
    # TRƯỚC khi onnxruntime được import (máy test không có onnxruntime).
    (tmp_path / "yolo26n.onnx").write_bytes(b"model-gia-bi-thay")
    with pytest.raises(ValueError) as e:
        yolo_mod.BoPhatHien(tmp_path / "yolo26n.onnx")
    assert "sha256" in str(e.value)
    (tmp_path / "det_500m.onnx").write_bytes(b"rac")
    (tmp_path / "w600k_mbf.onnx").write_bytes(b"rac")
    with pytest.raises(ValueError) as e2:
        km.BoNhanMat(tmp_path, km.get("buffalo_s"))
    assert "sha256" in str(e2.value)
