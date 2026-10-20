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
