"""Test NV158-SUA cho module facebook: sửa lỗi thử lại POST + đua giới hạn tần suất.

Theo soát độc lập (soat-doc-lap-A.md, C1–C3):
- (a) POST đăng bài KHÔNG được tự thử lại sau khi yêu cầu đã gửi:
  lỗi mạng sau gửi hay phản hồi lỗi mơ hồ = «không rõ kết quả», báo người
  dùng mở Trang kiểm tra, không đăng lại. Chỉ thử lại lỗi chắc chắn chưa
  gửi (ConnectError / ConnectTimeout / PoolTimeout).
- (b) Giới hạn «30 s giữa hai bài/Trang» bị vượt khi 5 lệnh chạy đồng thời:
  thêm khoá chống đua theo Trang — 5 lệnh đồng thời chỉ cho 1 qua.
- (c) Mọi lời gọi Graph khác đã rà: chỉ `_goi_graph` gọi Graph; GET (đọc)
  giữ thử lại lỗi thoáng qua.

Máy chủ Graph GIẢ (http.server trên 127.0.0.1 +
WEB2API_TEST_CHO_PHEP_LOOPBACK=1). Không Internet, không token thật.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

GOC = Path(__file__).resolve().parents[1]
THU_MUC = GOC / "modules" / "facebook"
TEN_GOI = "facebook_nv158sua_mod_test"


mod_module = None  # nạp TRONG fixture _nap_module_cach_ly, không ở cấp tệp
Module = None     # (xem fixture — nạp module lần hai ở cấp tệp phá bộ lọc log toàn cục)


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


# Logger của thư viện HTTP mà module facebook cài bộ lọc (PHẢI khớp
# modules/facebook/module.py::_LOGGER_HTTP — fixture kiểm tra bằng assert,
# đổi module mà quên đổi đây thì test báo ngay).
_LOGGER_HTTP = ("httpx", "httpcore", "httpcore.connection",
                "httpcore.http11", "httpcore.http2", "httpcore.proxy")


def _anh_chup_bo_loc():
    """Lưu danh sách bộ lọc hiện tại của các logger HTTP."""
    return {ten: logging.getLogger(ten).filters[:] for ten in _LOGGER_HTTP}


def _khoi_phuc_bo_loc(anh_chup):
    """Trả lại danh sách bộ lọc như lúc chụp — test không được để lại
    trạng thái toàn cục khác lúc nó bắt đầu."""
    for ten, ds in anh_chup.items():
        logging.getLogger(ten).filters[:] = ds


@pytest.fixture(scope="module", autouse=True)
def _nap_module_cach_ly():
    # NV158-SUA2: nạp module TRONG fixture, KHÔNG ở cấp tệp. Module facebook
    # cài bộ lọc log TOÀN CỤC lúc nạp (_cai_bo_loc_log): nạp lần hai ở cấp
    # tệp (ngay lúc thu thập) gỡ bộ lọc của bản nạp trước và cài bộ lọc mới
    # với sổ token RỖNG → test cũ (dùng bản nạp đầu) mất bảo vệ khi chạy
    # chung hai tệp. Fixture chụp bộ lọc trước khi nạp và khôi phục khi xong.
    truoc = _anh_chup_bo_loc()
    mod = _tai_goi()
    assert tuple(mod._LOGGER_HTTP) == _LOGGER_HTTP, \
        "module đổi danh sách logger — đồng bộ _LOGGER_HTTP ở tệp test này"
    global mod_module, Module
    mod_module, Module = mod, mod.Module
    try:
        yield
    finally:
        _khoi_phuc_bo_loc(truoc)
MANIFEST = json.loads((THU_MUC / "manifest.json").read_text(encoding="utf-8"))

# Hằng giả — khớp test/test_web2api_nv158_facebook.py (không token thật).
APP_ID = "100000000000001"
APP_SECRET_GIA = "bi-mat-app-secret-gia-0123456789"
USER_TOKEN_GIA = "TOKEN-NGUOI-DUNG-NGAN-HAN-GIA"
TOKEN_DAI_GIA = "TOKEN-Dai-HAN-GIA-60-ngay-abcdef"
PAGE_TOKEN_1 = "TOKEN-TRANG-1-GIA-zzzz"
PAGE_TOKEN_2 = "TOKEN-TRANG-2-GIA-yyyy"
CAU_HINH_DAY_DU = {
    "app_id": APP_ID,
    "app_secret": APP_SECRET_GIA,
    "user_token": USER_TOKEN_GIA,
}


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


# ───────────── máy chủ Graph GIẢ cho các ca NV158-SUA ─────────────

class _XuLySua(BaseHTTPRequestHandler):
    ds_yeu_cau: list = []
    che_do: str = "ok"      # ok | loi_502_rong | ngat_giua_chung | cham | get_thoang_qua
    token_da_dang_ky: bool | None = None
    _get_da_that_bai: bool = False

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
        _XuLySua.ds_yeu_cau.append({
            "phuong_thuc": phuong_thuc, "duong": phan.path,
            "query": parse_qs(phan.query), "than": than, "kieu": kieu})

    def do_GET(self):
        phan = urlsplit(self.path)
        duong = phan.path
        self._ghi("GET")
        if duong.endswith("/me/accounts"):
            # Ghi lại token dài hạn đã nằm trong bộ lọc log của module chưa
            # (lúc lời gọi này đi qua).
            _XuLySua.token_da_dang_ky = TOKEN_DAI_GIA in mod_module._TOKEN_DANG_DUNG
            self._tra(200, {"data": [
                {"id": "111", "name": "Trang Một", "access_token": PAGE_TOKEN_1}]})
            return
        if duong.endswith("/oauth/access_token"):
            self._tra(200, {"access_token": TOKEN_DAI_GIA, "expires_in": 5184000})
            return
        if "/v23.0/" in duong:
            if (_XuLySua.che_do == "get_thoang_qua"
                    and not _XuLySua._get_da_that_bai):
                _XuLySua._get_da_that_bai = True
                self.send_response(500)              # 500 trống — lỗi mơ hồ
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            pid = duong.rsplit("/", 1)[-1]
            self._tra(200, {"id": pid, "name": "Trang"})
            return
        self._tra(404, {})

    def do_POST(self):
        dai = int(self.headers.get("Content-Length", 0) or 0)
        than = self.rfile.read(dai) if dai else b""
        duong = urlsplit(self.path).path
        cd = _XuLySua.che_do
        if cd == "ngat_giua_chung" and duong.endswith("/feed"):
            self._ghi("POST", than)   # Facebook đã NHẬN bài…
            try:                       # …rồi ngắt kết nối, không trả gì
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            self.close_connection = True
            return
        if cd == "cham" and duong.endswith("/feed"):
            self._ghi("POST", than)
            time.sleep(0.25)           # giả Facebook phản hồi chậm
            self._tra(200, {"id": "111_bai1", "permalink_url": "/111/posts/bai1"})
            return
        self._ghi("POST", than)
        if cd == "loi_502_rong" and duong.endswith("/feed"):
            self.send_response(502)   # Facebook đã NHẬN bài nhưng trả lỗi trống
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if duong.endswith("/feed"):
            pid = duong.split("/")[-2]
            self._tra(200, {"id": f"{pid}_bai1",
                            "permalink_url": f"/{pid}/posts/bai1"})
        else:
            self._tra(404, {})


@pytest.fixture(scope="module")
def may_chu():
    _XuLySua.ds_yeu_cau = []
    sv = ThreadingHTTPServer(("127.0.0.1", 0), _XuLySua)
    threading.Thread(target=sv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{sv.server_address[1]}"
    sv.shutdown()


@pytest.fixture(autouse=True)
def _moi_test(monkeypatch):
    _XuLySua.ds_yeu_cau = []
    _XuLySua.che_do = "ok"
    _XuLySua.token_da_dang_ky = None
    _XuLySua._get_da_that_bai = False
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(mod_module, "_THOI_GIAN_NGHI", 0.01)
    yield


def _module(may_chu, cau_hinh=None):
    m = Module(MANIFEST)
    m._goc_test = may_chu + "/v23.0"
    if cau_hinh is not None:
        m.nap(cau_hinh)
    return m


def _yeu_cau_feed():
    return [y for y in _XuLySua.ds_yeu_cau
            if y["phuong_thuc"] == "POST" and y["duong"].endswith("/feed")]


# ───────────── (a) POST không thử lại sau khi đã gửi ─────────────

def test_sua_post_502_rong_khong_thu_lai_khong_dang_trung(may_chu):
    # Facebook đã NHẬN bài (ghi nhận) nhưng trả 502 trống → bản cũ thử lại
    # tạo BÀI THỨ HAI; bản mới: không thử lại, báo «không rõ kết quả».
    _XuLySua.che_do = "loi_502_rong"
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai",
                                 {"noi_dung": "Bài duy nhất"}))
    assert kq["ok"] is False
    assert "không rõ" in kq["loi"].lower() and "Trang" in kq["loi"]
    assert len(_yeu_cau_feed()) == 1, "PHẢI chỉ gửi đúng 1 lần — không đăng lại"


def test_sua_post_ngat_ket_noi_sau_gui_khong_dang_lai(may_chu):
    # Mất kết nối sau khi yêu cầu đã gửi (ReadTimeout/RemoteProtocolError) →
    # không thử lại: yêu cầu có thể đã tới Facebook.
    _XuLySua.che_do = "ngat_giua_chung"
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.trang_111", "dang_bai",
                                 {"noi_dung": "Bài duy nhất"}))
    assert kq["ok"] is False
    assert "không rõ" in kq["loi"].lower() and "Trang" in kq["loi"]
    assert len(_yeu_cau_feed()) == 1, "PHẢI chỉ gửi đúng 1 lần — không đăng lại"


def test_sua_post_loi_ket_noi_truoc_gui_duoc_thu_lai(may_chu, monkeypatch):
    # Lỗi chắc chắn CHƯA gửi (không nối được máy chủ) → vẫn được thử lại.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    cong = s.getsockname()[1]
    s.close()
    m = _module(f"http://127.0.0.1:{cong}", _cau_hinh_da_ket_noi())
    monkeypatch.setattr(mod_module, "_THOI_GIAN_NGHI", 0.3)

    async def chay():
        tac_vu = asyncio.ensure_future(
            m.thuc_hien("facebook.trang_111", "dang_bai", {"noi_dung": "Thử lại"}))
        await asyncio.sleep(0.45)     # máy chủ chỉ lên sau 2 lần thử hỏng
        sv = ThreadingHTTPServer(("127.0.0.1", cong), _XuLySua)
        threading.Thread(target=sv.serve_forever, daemon=True).start()
        try:
            return await tac_vu
        finally:
            sv.shutdown()

    kq = asyncio.run(chay())
    assert kq["ok"] is True, kq
    assert len(_yeu_cau_feed()) == 1


def test_sua_get_thoang_qua_van_duoc_thu_lai(may_chu):
    # GET là đọc (idempotent) → giữ nguyên chính sách thử lại lỗi thoáng qua.
    _XuLySua.che_do = "get_thoang_qua"
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    kq = asyncio.run(m.thuc_hien("facebook.ket_noi", "kiem_tra", {}))
    assert kq["ok"] is True, kq
    assert kq["trang_thai_moi"]["so_trang_song"] == 2   # cả 2 Trang: 111 thử lại thành công


# ───────────── (b) khoá chống đua giới hạn tần suất theo Trang ─────────────

def test_sua_5_lenh_dong_thoi_chi_1_qua(may_chu):
    # 5 lệnh đồng thời cho cùng một Trang (Facebook phản hồi chậm) →
    # chỉ 1 bài được đăng, 4 còn lại bị chặn bởi giới hạn tần suất.
    _XuLySua.che_do = "cham"
    m = _module(may_chu, _cau_hinh_da_ket_noi())
    m._khoang_cach_bai = 60.0

    async def chay():
        return await asyncio.gather(*[
            m.thuc_hien("facebook.trang_111", "dang_bai",
                        {"noi_dung": f"Bài đồng thời {i}"})
            for i in range(5)])

    kqs = asyncio.run(chay())
    thanh_cong = [k for k in kqs if k.get("ok")]
    assert len(thanh_cong) == 1, f"chỉ 1 bài được đăng, được {len(thanh_cong)}"
    for k in kqs:
        if not k.get("ok"):
            assert "quá nhanh" in k["loi"]
    assert len(_yeu_cau_feed()) == 1, "Facebook PHẢI chỉ nhận đúng 1 bài"


# ───────────── (c/C3) token dài hạn ghi nhận trước me/accounts ─────────────

def test_sua_token_dai_dang_ky_truoc_khi_goi_me_accounts(may_chu):
    # Trong cai(), lời gọi GET me/accounts kèm access_token trong URL — token
    # dài hạn phải có trong _TOKEN_DANG_DUNG của bộ lọc log TRƯỚC lời gọi này.
    mod_module._TOKEN_DANG_DUNG.discard(TOKEN_DAI_GIA)
    m = _module(may_chu)
    kq = asyncio.run(m.cai(dict(CAU_HINH_DAY_DU)))
    assert kq["ok"] is True, kq
    assert _XuLySua.token_da_dang_ky is True, \
        "token dài hạn phải được ghi nhận trước khi gọi me/accounts"


# ───────────── khoá lớp lỗi NV158-SUA2 ─────────────

def test_sua2_chay_chung_hai_thu_tu_deu_xanh():
    # Khoá lớp lỗi «test nạp module lần hai phá bộ lọc log toàn cục»: hai tệp
    # facebook phải xanh khi chạy chung, theo CẢ HAI thứ tự. Chạy tiến trình
    # con để mỗi thứ tự là một tiến trình pytest độc lập. Bỏ chọn chính test
    # này trong tiến trình con (không đệ quy).
    cu = "test/test_web2api_nv158_facebook.py::test_token_khong_vao_log_qua_get"
    moi = "test/test_web2api_nv158_sua_facebook.py"
    bo_chon = ("--deselect",
               "test/test_web2api_nv158_sua_facebook.py"
               "::test_sua2_chay_chung_hai_thu_tu_deu_xanh")
    for args in ([cu, moi], [moi, cu]):
        kq = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             *bo_chon, *args],
            cwd=GOC, capture_output=True, text=True, timeout=300)
        assert kq.returncode == 0, \
            f"chạy chung thứ tự {args} đỏ:\n{kq.stdout[-2000:]}\n{kq.stderr[-2000:]}"
