"""Test NV158 cho module facebook MỚI: đăng bài Trang qua Graph API chính thức.

- manifest hợp chuẩn lõi (kiem_manifest), token Trang ở trường bi_mat,
  mang_ra chỉ graph.facebook.com, địa chỉ gọi CỐ ĐỊNH trong mã.
- cai(): đổi user token → token dài hạn + nạp danh sách Page (phân trang),
  user_token ngắn hạn bị XOÁ khỏi cấu hình lưu, pages lưu dạng bí mật.
- thuc_the: mỗi Page một thực thể `facebook.trang_<id>`, rui_ro_mac_dinh
  «nguy_hiem» (lõi bắt quản trị xác nhận trước khi đăng ra công chúng);
  thực thể `facebook.ket_noi` rủi ro «doc» cho liệt kê/kiểm tra.
- thuc_hien: dang_bai (chữ/link), dang_anh (tải qua app.tai_url_khong_tin +
  app.tep_an_toan.kiem_anh rồi upload multipart), dang_video (file_url đã
  kiểm host công khai); giới hạn tần suất theo Page; lỗi Graph dịch tiếng
  Việt, thử lại lỗi thoáng qua; token KHÔNG vào log/phản hồi/kết quả.
- IDOR: page_id không thuộc cấu hình → từ chối, không gọi Graph.

Máy chủ Graph/media là GIẢ (http.server trên 127.0.0.1 +
WEB2API_TEST_CHO_PHEP_LOOPBACK=1). Không Internet, không token thật.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import importlib.util
import json
import logging
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.kham_pha import kiem_manifest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "facebook"
TEN_GOI = "facebook_nv158_mod_test"

APP_ID = "100000000000001"
APP_SECRET_GIA = "bi-mat-app-secret-gia-0123456789"
USER_TOKEN_GIA = "TOKEN-NGUOI-DUNG-NGAN-HAN-GIA"
TOKEN_DAI_GIA = "TOKEN-Dai-HAN-GIA-60-ngay-abcdef"
PAGE_TOKEN_1 = "TOKEN-TRANG-1-GIA-zzzz"
PAGE_TOKEN_2 = "TOKEN-TRANG-2-GIA-yyyy"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

CAU_HINH_DAY_DU = {
    "app_id": APP_ID,
    "app_secret": APP_SECRET_GIA,
    "user_token": USER_TOKEN_GIA,
}


def _tai_goi():
    for ten in [t for t in list(sys.modules) if t == TEN_GOI or t.startswith(TEN_GOI + ".")]:
        del sys.modules[ten]
    goi = types.ModuleType(TEN_GOI)
    goi.__path__ = [str(THU_MUC)]
    sys.modules[TEN_GOI] = goi
    spec = importlib.util.spec_from_file_location(f"{TEN_GOI}.module", THU_MUC / "module.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{TEN_GOI}.module"] = mod
    spec.loader.exec_module(mod)
    return mod


mod_module = _tai_goi()
Module = mod_module.Module
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))


# ───────────── máy chủ Graph + media GIẢ ─────────────

class _XuLyGia(BaseHTTPRequestHandler):
    ds_yeu_cau: list = []
    che_do: str = "ok"      # ok | token_190 | subcode_anh | code_200 | thoang_qua | phan_trang
    _da_that_bai_1_lan: bool = False

    def log_message(self, *a):
        pass

    def _tra(self, ma, obj):
        than = json.dumps(obj).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(than)))
        self.end_headers()
        self.wfile.write(than)

    def _ghi(self, phuong_thuc, than=b"", kieu=""):
        phan = urlsplit(self.path)
        _XuLyGia.ds_yeu_cau.append({
            "phuong_thuc": phuong_thuc, "duong": phan.path,
            "query": parse_qs(phan.query), "than": than, "kieu": kieu})

    def do_GET(self):
        phan = urlsplit(self.path)
        duong = phan.path
        if duong.startswith("/media/"):
            self._ghi("GET")
            if duong == "/media/anh.png":
                self._tra_raw(PNG_1X1, "image/png")
            elif duong == "/media/chu.txt":
                self._tra_raw(b"day khong phai anh", "text/plain")
            elif duong == "/media/tep-lon.bin":
                self._tra_raw(b"x" * (5 * 1024 * 1024), "application/octet-stream")
            else:
                self._tra(404, {})
            return
        self._ghi("GET")
        cd = _XuLyGia.che_do
        if cd == "token_190":
            self._tra(400, {"error": {"message": "Session has expired",
                                      "code": 190, "error_subcode": 463}})
            return
        if duong.endswith("/oauth/access_token"):
            self._tra(200, {"access_token": TOKEN_DAI_GIA, "expires_in": 5184000})
        elif duong.endswith("/me/accounts"):
            q = parse_qs(phan.query)
            if cd == "phan_trang" and "after" not in q:
                self._tra(200, {
                    "data": [{"id": "111", "name": "Trang Một",
                              "access_token": PAGE_TOKEN_1}],
                    "paging": {"cursors": {"after": "TRANG2"},
                               "next": "https://graph.facebook.com/v23.0/me/accounts?after=TRANG2"}})
            elif cd == "phan_trang":
                self._tra(200, {"data": [{"id": "222", "name": "Trang Hai\x01\x02",
                                          "access_token": PAGE_TOKEN_2}]})
            else:
                self._tra(200, {"data": [
                    {"id": "111", "name": "Trang Một", "access_token": PAGE_TOKEN_1},
                    {"id": "222", "name": "Trang Hai\x01\x02", "access_token": PAGE_TOKEN_2}]})
        elif "/v23.0/" in duong:
            pid = duong.rsplit("/", 1)[-1]
            self._tra(200, {"id": pid, "name": "Trang"})
        else:
            self._tra(404, {})

    def _tra_raw(self, du_lieu, kieu):
        self.send_response(200)
        self.send_header("Content-Type", kieu)
        self.send_header("Content-Length", str(len(du_lieu)))
        self.end_headers()
        self.wfile.write(du_lieu)

    def do_POST(self):
        dai = int(self.headers.get("Content-Length", 0) or 0)
        than = self.rfile.read(dai) if dai else b""
        kieu = self.headers.get("Content-Type", "")
        self._ghi("POST", than, kieu)
        duong = urlsplit(self.path).path
        cd = _XuLyGia.che_do
        if cd == "token_190":
            self._tra(400, {"error": {"message": "Session has expired", "code": 190}})
            return
        if cd == "subcode_anh":
            self._tra(400, {"error": {"message": "(#1366046) loi",
                                      "code": 100, "error_subcode": 1366046}})
            return
        if cd == "code_200":
            self._tra(403, {"error": {"message": "(#200) Requires pages_manage_posts",
                                      "code": 200}})
            return
        if cd == "thoang_qua" and not _XuLyGia._da_that_bai_1_lan:
            _XuLyGia._da_that_bai_1_lan = True
            self._tra(429, {"error": {"message": "rate", "code": 4}})
            return
        if duong.endswith("/feed"):
            pid = duong.split("/")[-2]
            self._tra(200, {"id": f"{pid}_bai1",
                            "permalink_url": f"/{pid}/posts/bai1"})
        elif duong.endswith("/photos"):
            self._tra(200, {"id": "anh1"})
        elif duong.endswith("/videos"):
            self._tra(200, {"id": "video1"})
        else:
            self._tra(404, {})


@pytest.fixture(scope="module")
def may_chu():
    _XuLyGia.ds_yeu_cau = []
    sv = ThreadingHTTPServer(("127.0.0.1", 0), _XuLyGia)
    threading.Thread(target=sv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{sv.server_address[1]}"
    sv.shutdown()


@pytest.fixture(autouse=True)
def _moi_test(monkeypatch):
    _XuLyGia.ds_yeu_cau = []
    _XuLyGia.che_do = "ok"
    _XuLyGia._da_that_bai_1_lan = False
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(mod_module, "_THOI_GIAN_NGHI", 0.01)
    yield


def _module(may_chu, cau_hinh=None):
    m = Module(MANIFEST)
    m._goc_test = may_chu + "/v23.0"
    if cau_hinh is not None:
        m.nap(cau_hinh)
    return m


def _cau_hinh_da_ket_noi():
    return {
        "app_id": APP_ID,
        "app_secret": APP_SECRET_GIA,
        "user_token": "",
        "user_token_long": TOKEN_DAI_GIA,
        "pages": json.dumps([
            {"id": "111", "name": "Trang Một", "access_token": PAGE_TOKEN_1},
            {"id": "222", "name": "Trang Hai\x01\x02", "access_token": PAGE_TOKEN_2},
        ]),
    }


def _yeu_cau(duong_cuoi=None, phuong_thuc=None):
    ra = []
    for y in _XuLyGia.ds_yeu_cau:
        if duong_cuoi and not y["duong"].endswith(duong_cuoi):
            continue
        if phuong_thuc and y["phuong_thuc"] != phuong_thuc:
            continue
        ra.append(y)
    return ra


# ───────────── manifest ─────────────

def test_manifest_hop_chuan_loi():
    assert kiem_manifest(json.loads(json.dumps(MANIFEST))) is None
    assert MANIFEST["cung_cap"] == ["thiet_bi"]
    assert MANIFEST["tai_nguyen"]["mang_ra"] == ["graph.facebook.com"]
    assert MANIFEST["yeu_cau_goi"] == ["httpx"]
    truong = {t["khoa"]: t for t in MANIFEST["truong_cau_hinh"]}
    for khoa in ("app_secret", "user_token", "user_token_long", "pages"):
        assert truong[khoa]["kieu"] == "bi_mat", khoa
    assert truong["app_id"]["bat_buoc"] is True
    assert truong["app_secret"]["bat_buoc"] is True
    # mo_ta truong bi_mat phai noi cach lay khoa (chu may phan nan thieu huong dan)
    assert "developers.facebook.com" in truong["user_token"]["mo_ta"]
    assert "Graph API Explorer" in truong["user_token"]["mo_ta"]


# ───────────── cai(): đổi token + nạp Page ─────────────

def test_cai_thieu_truong(may_chu):
    m = _module(may_chu)
    for thieu in ({}, {"app_id": APP_ID}, {"app_id": APP_ID, "app_secret": APP_SECRET_GIA}):
        kq = asyncio.run(m.cai(dict(thieu)))
        assert kq["ok"] is False, thieu
        assert kq["cau_hinh_luu"] == {}


def test_cai_doi_token_luu_trang_xoa_token_ngan(may_chu):
    m = _module(may_chu)
    kq = asyncio.run(m.cai(dict(CAU_HINH_DAY_DU)))
    assert kq["ok"] is True, kq
    luu = kq["cau_hinh_luu"]
    assert luu["user_token"] == ""                      # token ngắn hạn đã dùng → xoá
    assert luu["user_token_long"] == TOKEN_DAI_GIA
    pages = json.loads(luu["pages"])
    assert [p["id"] for p in pages] == ["111", "222"]
    assert pages[0]["access_token"] == PAGE_TOKEN_1
    # ket_qua_tung_khoa chi dang che — khong token that
    chuoi = json.dumps(kq["ket_qua_tung_khoa"])
    for tok in (USER_TOKEN_GIA, TOKEN_DAI_GIA, PAGE_TOKEN_1, PAGE_TOKEN_2, APP_SECRET_GIA):
        assert tok not in chuoi
    # loi goi doi token dung user token vua dan
    yc = _yeu_cau("/oauth/access_token")
    assert len(yc) == 1 and yc[0]["query"]["fb_exchange_token"] == [USER_TOKEN_GIA]


def test_cai_phan_trang_me_accounts(may_chu):
    _XuLyGia.che_do = "phan_trang"
    m = _module(may_chu)
    kq = asyncio.run(m.cai(dict(CAU_HINH_DAY_DU)))
    assert kq["ok"] is True
    pages = json.loads(kq["cau_hinh_luu"]["pages"])
    assert [p["id"] for p in pages] == ["111", "222"]
    assert len(_yeu_cau("/me/accounts")) == 2            # co goi trang 2 bang after


def test_cai_token_hong_190_loi_tieng_viet_khong_lo_token(may_chu):
    _XuLyGia.che_do = "token_190"
    m = _module(may_chu)
    kq = asyncio.run(m.cai(dict(CAU_HINH_DAY_DU)))
    assert kq["ok"] is False
    assert "hết hạn" in kq["loi"]
    assert USER_TOKEN_GIA not in kq["loi"]
    assert APP_SECRET_GIA not in kq["loi"]


def test_cai_lam_tuoi_bang_token_dai(may_chu):
    m = _module(may_chu)
    ch = dict(CAU_HINH_DAY_DU)
    ch["user_token"] = ""
    ch["user_token_long"] = TOKEN_DAI_GIA
    kq = asyncio.run(m.cai(ch))
    assert kq["ok"] is True, kq
    yc = _yeu_cau("/oauth/access_token")
    assert yc[0]["query"]["fb_exchange_token"] == [TOKEN_DAI_GIA]


# ───────────── thuc_the / trang_thai / suc_khoe ─────────────

def test_thuc_the_rui_ro_nguy_hiem_va_ten_sach(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    ds = asyncio.run(m.thuc_the())
    ids = {e["id"] for e in ds}
    assert ids == {"facebook.ket_noi", "facebook.trang_111", "facebook.trang_222"}
    for e in ds:
        if e["id"].startswith("facebook.trang_"):
            assert e["rui_ro_mac_dinh"] == "nguy_hiem"   # dang ra cong chung → quan tri xac nhan
            hd = {t["hanh_dong"] for t in e["tinh_nang"]}
            assert hd == {"dang_bai", "dang_anh", "dang_video"}
        else:
            assert e["rui_ro_mac_dinh"] == "doc"
    hai = next(e for e in ds if e["id"] == "facebook.trang_222")
    assert "\x01" not in hai["ten"] and "\x02" not in hai["ten"]  # ky tu dieu khien bi bo


def test_trang_thai_va_suc_khoe(may_chu):
    m = _module(may_chu)
    assert asyncio.run(m.suc_khoe())["ok"] is False
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    sk = asyncio.run(m.suc_khoe())
    assert sk["ok"] is True and sk["so_trang"] == 2
    assert TOKEN_DAI_GIA not in json.dumps(sk)
    tt = asyncio.run(m.trang_thai(["facebook.trang_111", "facebook.ket_noi"]))
    assert tt["facebook.trang_111"]["gia_tri"] == "da_ket_noi"


def test_nap_pages_json_hong_khong_sap(may_chu):
    ch = _cau_hinh_da_ket_noi()
    ch["pages"] = "{hong"
    m = _module(may_chu, ch)
    assert asyncio.run(m.thuc_the()) != []
    assert asyncio.run(m.suc_khoe())["ok"] is False


# ───────────── dang bai ─────────────

def test_dang_bai_chu_va_link(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai",
                                 {"noi_dung": "Xin chào", "link": "https://example.invalid/bai"}))
    assert kq["ok"] is True, kq
    assert kq["trang_thai_moi"]["url"] == "https://www.facebook.com/111/posts/bai1"
    yc = _yeu_cau("/feed", "POST")
    assert len(yc) == 1
    than = json.loads(yc[0]["than"])
    assert than["message"] == "Xin chào"
    assert than["link"] == "https://example.invalid/bai"
    # token Trang di trong than (khong phai URL) + appsecret_proof dung HMAC
    assert than["access_token"] == PAGE_TOKEN_1
    proof = hmac_mod.new(APP_SECRET_GIA.encode(), PAGE_TOKEN_1.encode(),
                         hashlib.sha256).hexdigest()
    assert than["appsecret_proof"] == proof
    assert PAGE_TOKEN_1 not in yc[0]["duong"]


def test_dang_bai_trang_khong_co_idor(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_999", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is False
    assert _yeu_cau("/feed") == []                       # khong goi Graph


def test_hanh_dong_khong_ho_tro(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "xoa_bai", {}))
    assert kq["ok"] is False
    kq = asyncio.run(m.thuc_hien("facebook.ket_noi", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is False


def test_dang_bai_noi_dung_rong(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "  "}))
    assert kq["ok"] is False
    assert _yeu_cau("/feed") == []


def test_gioi_han_tan_suat_theo_trang(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    m._khoang_cach_bai = 0.3
    kq1 = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "mot"}))
    assert kq1["ok"] is True
    kq2 = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "hai"}))
    assert kq2["ok"] is False and "quá nhanh" in kq2["loi"]
    # trang khac khong bi anh huong
    kq3 = asyncio.run(m.thuc_hien("facebook.trang_222", "dang_bai", {"noi_dung": "ba"}))
    assert kq3["ok"] is True
    time.sleep(0.35)
    kq4 = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "bon"}))
    assert kq4["ok"] is True


def test_loi_graph_duoc_dich(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    _XuLyGia.che_do = "subcode_anh"
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is False
    assert kq["loi"] == "Ảnh phải nhỏ hơn 4 MB và là JPG/PNG"
    _XuLyGia.che_do = "code_200"
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is False and "pages_manage_posts" in kq["loi"]
    assert PAGE_TOKEN_1 not in kq["loi"]


def test_thu_lai_loi_thoang_qua_429(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    _XuLyGia.che_do = "thoang_qua"
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is True, kq
    assert len(_yeu_cau("/feed", "POST")) == 2           # that bai 1 lan roi thu lai


def test_kiem_tra_ket_noi_va_liet_ke(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.ket_noi", "liet_ke_trang", {}))
    assert kq["ok"] is True
    chuoi = json.dumps(kq)
    assert PAGE_TOKEN_1 not in chuoi and PAGE_TOKEN_2 not in chuoi
    kq = asyncio.run(m.thuc_hien("facebook.ket_noi", "kiem_tra", {}))
    assert kq["ok"] is True
    assert kq["trang_thai_moi"]["so_trang_song"] == 2


def test_token_khong_vao_log_khi_loi_mang(caplog):
    m = Module(MANIFEST)
    m._goc_test = "http://127.0.0.1:9/v23.0"             # cong dong — loi mang chac chan
    m.nap(_cau_hinh_da_ket_noi())
    with caplog.at_level(logging.DEBUG):
        kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "x"}))
    assert kq["ok"] is False
    tat_ca_log = "\n".join(r.getMessage() for r in caplog.records)
    for tok in (PAGE_TOKEN_1, PAGE_TOKEN_2, TOKEN_DAI_GIA, APP_SECRET_GIA):
        assert tok not in tat_ca_log
        assert tok not in json.dumps(kq)


def test_token_khong_vao_log_qua_get(may_chu, caplog):
    # GET cua Graph de token o QUERY — httpx ghi URL day du o INFO. Bo loc log
    # cua module phai bo nhung dong do (do _cai_bo_loc_log cai tren httpx).
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    with caplog.at_level(logging.DEBUG):
        kq = asyncio.run(m.thuc_hien("facebook.ket_noi", "kiem_tra", {}))
    assert kq["ok"] is True
    tat_ca_log = "\n".join(r.getMessage() for r in caplog.records)
    # httpcore ghi nhan van duoc (chung minh loi goi that su di qua);
    # rieng dong "HTTP Request" cua httpx chua URL+token phai bi loc bo.
    assert any(r.name.startswith("httpcore") for r in caplog.records)
    assert not any("HTTP Request" in r.getMessage() for r in caplog.records)
    for tok in (PAGE_TOKEN_1, PAGE_TOKEN_2, TOKEN_DAI_GIA, APP_SECRET_GIA):
        assert tok not in tat_ca_log


# ───────────── dang anh / video ─────────────

def test_dang_anh_tai_kiem_upload(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    url_anh = f"{may_chu}/media/anh.png"
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_anh",
                                 {"media_urls": [url_anh], "caption": "Ảnh đẹp"}))
    assert kq["ok"] is True, kq
    yc_anh = _yeu_cau("/photos", "POST")
    assert len(yc_anh) == 1
    assert "multipart/form-data" in yc_anh[0]["kieu"]
    assert PNG_1X1 in yc_anh[0]["than"]                  # gui dung byte anh da tai
    assert b"false" in yc_anh[0]["than"]                 # published=false
    yc_feed = _yeu_cau("/feed", "POST")
    than = json.loads(yc_feed[0]["than"])
    assert than["attached_media"] == [{"media_fbid": "anh1"}]
    assert than["message"] == "Ảnh đẹp"


def test_dang_anh_khong_phai_anh_bi_chan(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_anh",
                                 {"media_urls": [f"{may_chu}/media/chu.txt"]}))
    assert kq["ok"] is False
    assert _yeu_cau("/photos") == []                     # chan truoc khi upload


def test_dang_anh_qua_4mb_bi_chan(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_anh",
                                 {"media_urls": [f"{may_chu}/media/tep-lon.bin"]}))
    assert kq["ok"] is False
    assert _yeu_cau("/photos") == []


def test_dang_anh_url_metadata_bi_chan(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_anh",
                                 {"media_urls": ["http://169.254.169.254/latest/meta-data/"]}))
    assert kq["ok"] is False
    assert _yeu_cau("/photos") == []


def test_dang_video_url_rieng_tu_bi_chan(may_chu):
    # link-local/metadata va IP rieng bi chan bat ke che do test (chi loopback
    # duoc mien khi test) — bo _kiem_url_video thi /videos se duoc goi.
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    for url in ("http://169.254.169.254/clip.mp4", "http://192.0.2.55/clip.mp4"):
        kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_video",
                                     {"url": url}))
        assert kq["ok"] is False
    assert _yeu_cau("/videos") == []


def test_dang_video_ok(may_chu):
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    url_video = f"{may_chu}/media/clip.mp4"   # loopback duoc phep trong test
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_video",
                                 {"url": url_video, "mo_ta": "Clip hay"}))
    assert kq["ok"] is True, kq
    assert kq["trang_thai_moi"]["url"] == "https://www.facebook.com/reel/video1"
    yc = _yeu_cau("/videos", "POST")
    than = json.loads(yc[0]["than"])
    assert than["file_url"] == url_video
    assert than["access_token"] == PAGE_TOKEN_1
