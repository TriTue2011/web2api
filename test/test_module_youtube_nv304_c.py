"""NV304 mảnh (c) — giải luồng Zing/Facebook, nhập playlist Zing, seek_url.

Không Internet thật: trich_xuat chạy qua client giả (httpx) và yt-dlp
giả; module chạy qua TrichXuatGia như test NV301. Đỏ-trước-xanh.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import pathlib
import sys
import tempfile

import pytest

GOC = pathlib.Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "youtube"
sys.path.insert(0, str(GOC))

ZING_ID = "a1b2c3d4"
VIDEO_1 = "dQw4w9WgXcQ"


def tai_trich_xuat():
    ten = "mod_youtube_nv304_c_trich_xuat"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(ten, THU_MUC_MODULE / "trich_xuat.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


def tai_module():
    ten = "mod_youtube_nv304_c"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, THU_MUC_MODULE / "module.py",
        submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


class RespGia:
    def __init__(self, content=b"", url="", headers=None):
        self.content = content if isinstance(content, bytes) else content.encode()
        self.url = url
        self.headers = headers or {}


class ClientGia:
    """Client giả kiểu httpx: bản đồ URL → RespGia, cookie dict."""

    def __init__(self, ban_do, cookies=None):
        self.ban_do = ban_do
        self.cookies = dict(cookies or {})
        self.da_goi = []

    def get(self, url, headers=None, **kw):
        self.da_goi.append(url)
        if url not in self.ban_do:
            raise AssertionError(f"client giả không có URL {url}")
        return self.ban_do[url]


def test_trich_khoa_web_zing():
    tx = tai_trich_xuat()
    script = 'x="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",y="BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"'
    assert tx.extract_zing_web_keys(script) == {
        "api_key": "A" * 32, "api_secret": "B" * 32}
    assert tx.extract_zing_web_keys("không có khoá") is None


def test_ky_url_api_zing_dung_thuat_toan_cu():
    tx = tai_trich_xuat()
    url = tx._build_zing_api_url(ZING_ID, "1.20.4", 1700000000, "K" * 32, "S" * 32)
    params = {"id": ZING_ID, "ctime": "1700000000", "version": "1.20.4"}
    canonical = "".join(f"{k}={params[k]}" for k in sorted(params))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    sig = hmac.new(("S" * 32).encode(), f"/api/v2/song/get/streaming{digest}".encode(),
                   hashlib.sha512).hexdigest()
    assert f"sig={sig}" in url and "apiKey=" + "K" * 32 in url


def test_zing_playlist_id():
    tx = tai_trich_xuat()
    assert tx.zing_playlist_id(f"https://zingmp3.vn/album/Ten/{ZING_ID}.html") == ZING_ID
    assert tx.zing_playlist_id(f"https://zingmp3.vn/playlist/Ten/{ZING_ID}.html") == ZING_ID
    assert tx.zing_playlist_id("https://example.com/album/Ten/a1b2c3d4.html") is None
    assert tx.zing_playlist_id(f"https://zingmp3.vn/bai-hat/Ten/{ZING_ID}.html") is None
    for pid in ("a1b2c3d4e5f6", "a1b2c3d4e5f6g7h8"):
        assert tx.zing_playlist_id(f"https://zingmp3.vn/album/Ten/{pid}.html") == pid
        assert tx.ZING_ID.fullmatch(pid)


def _client_zing():
    trang_chu = '<script src="https://static.zmdcdn.me/abc/main.min.js"></script>'
    bundle = 'x="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",y="BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"'
    return ClientGia({
        "https://zingmp3.vn/": RespGia(trang_chu, "https://zingmp3.vn/"),
        "https://static.zmdcdn.me/abc/main.min.js": RespGia(
            bundle, "https://static.zmdcdn.me/abc/main.min.js"),
    }, cookies={"zmp3_app_version.1": "1.20.4"})


def test_lay_khoa_web_zing_qua_client_gia():
    tx = tai_trich_xuat()
    assert tx.fetch_zing_web_keys(client=_client_zing()) == {
        "api_key": "A" * 32, "api_secret": "B" * 32}


def test_trang_chu_zing_khong_gui_yeu_cau_toi_dich_chuyen_huong_ngoai():
    import httpx

    tx = tai_trich_xuat()
    da_goi = []

    def tra_loi(request):
        da_goi.append(str(request.url))
        if len(da_goi) == 1:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(tra_loi), follow_redirects=True) as client:
        with pytest.raises(tx.TrichXuatUnavailable, match="zing_target_rejected"):
            tx.fetch_zing_web_keys(client=client)
    assert da_goi == [tx.ZING_HOME_URL]


def test_giai_luong_zing_chon_320_va_loc_cdn():
    tx = tai_trich_xuat()
    client = _client_zing()
    api_payload = {"err": 0, "data": {
        "320": "https://zmdcdn.me/audio/a.mp3", "128": "https://zmdcdn.me/audio/b.mp3",
        "duration": 200}}
    # URL API ký phụ thuộc thời gian — client giả trả payload cho mọi URL API.
    class ClientApi(ClientGia):
        def get(self, url, headers=None, **kw):
            if url.startswith("https://zingmp3.vn/api/v2/song/get/streaming"):
                return RespGia(json.dumps(api_payload), url)
            return super().get(url, headers=headers)
    client = ClientApi(client.ban_do, cookies=client.cookies)
    client.ban_do["https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html"] = RespGia(
        "", "https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html")
    kq = tx.resolve_zing_stream("https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html",
                                api_key="A" * 32, api_secret="B" * 32, client=client)
    assert kq["url"] == "https://zmdcdn.me/audio/a.mp3"
    assert kq["duration"] == 200
    assert kq["headers"]["Referer"] == "https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html"


def test_giai_luong_zing_loi_api_thanh_502():
    tx = tai_trich_xuat()

    class ClientLoi(ClientGia):
        def get(self, url, headers=None, **kw):
            if url.startswith("https://zingmp3.vn/api/"):
                return RespGia(json.dumps({"err": 1001}), url)
            return super().get(url, headers=headers)
    client = ClientLoi(_client_zing().ban_do, cookies={"zmp3_app_version.1": "1.20.4"})
    client.ban_do["https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html"] = RespGia(
        "", "https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html")
    with pytest.raises(tx.TrichXuatUnavailable):
        tx.resolve_zing_stream("https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html",
                               api_key="A" * 32, api_secret="B" * 32, client=client)


def test_giai_luong_zing_dung_id_sau_redirect():
    tx = tai_trich_xuat()
    source = "https://zingmp3.vn/bai-hat/Cu/a1b2c3d4.html"
    final = "https://zingmp3.vn/bai-hat/Moi/a1b2c3d4e5f6.html"
    client = _client_zing()
    client.ban_do[source] = RespGia("", source, {"location": final})
    client.ban_do[source].status_code = 302
    client.ban_do[final] = RespGia("", final)
    api = tx._build_zing_api_url("a1b2c3d4e5f6", "1.20.4", 1700000000, "K" * 32, "S" * 32)
    client.ban_do[api] = RespGia(json.dumps({"err": 0, "data": {
        "128": "https://static.zmdcdn.me/song.mp3"}}), api)
    result = tx.resolve_zing_stream(source, api_key="K" * 32, api_secret="S" * 32,
                                    client=client, now=1700000000)
    assert result["headers"]["Referer"] == final
    assert client.da_goi[-1] == api


def test_nhap_playlist_zing_loc_vip():
    tx = tai_trich_xuat()

    class ClientPl(ClientGia):
        def get(self, url, headers=None, **kw):
            if url.startswith("https://zingmp3.vn/api/v2/page/get/playlist"):
                payload = {"err": 0, "data": {"title": "Album test", "song": {"items": [
                    {"streamingStatus": 1, "link": "/bai-hat/Bai-mot/a1b2c3d4.html",
                     "encodeId": "a1b2c3d4", "title": "Bài một",
                     "artistsNames": "Ca sĩ A", "duration": 180, "thumbnailM": "http://x/a.jpg"},
                    {"streamingStatus": 2, "link": "/bai-hat/Bai-vip/e5f6a7b8.html",
                     "encodeId": "e5f6a7b8", "title": "Bài VIP",
                     "artistsNames": "Ca sĩ B", "duration": 200, "thumbnailM": ""}]}}}
                return RespGia(json.dumps(payload), url)
            return super().get(url, headers=headers)
    client = ClientPl(_client_zing().ban_do, cookies={"zmp3_app_version.1": "1.20.4"})
    ten, muc = tx.fetch_zing_playlist(
        "https://zingmp3.vn/album/Album-test/a1b2c3d4.html",
        api_key="A" * 32, api_secret="B" * 32, client=client)
    assert ten == "Album test" and len(muc) == 1
    assert muc[0]["id"] == "a1b2c3d4" and muc[0]["title"] == "Bài một"


class YtDlpGia:
    def __init__(self, info):
        self.info = info

    def YoutubeDL(self, opts):
        info = self.info

        class _YDL:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return info
        return _YDL()


def test_giai_luong_facebook_chon_tieng_rieng():
    tx = tai_trich_xuat()
    info = {"title": "Video FB", "duration": 120, "formats": [
        {"format_id": "hd", "ext": "mp4", "url": "https://video.fbcdn.net/hd.mp4"},
        {"format_id": "sd", "ext": "mp4", "url": "https://video.fbcdn.net/sd.mp4"},
        {"format_id": "dash-audio", "ext": "m4a", "acodec": "mp4a.40.5", "vcodec": "none",
         "url": "https://video.fbcdn.net/audio.m4a",
         "http_headers": {"User-Agent": "extractor-test"}}]}
    tx._yt_dlp = lambda: YtDlpGia(info)
    kq = tx.resolve_facebook_audio("1234567890123456")
    assert kq["url"] == "https://video.fbcdn.net/audio.m4a"
    assert kq["duration"] == 120
    assert kq["content_type"] == "audio/mp4"
    assert kq["headers"]["User-Agent"] == "extractor-test"


def test_giai_luong_facebook_khong_tieng_rieng_dung_sd():
    tx = tai_trich_xuat()
    info = {"title": "Video FB", "duration": 60, "formats": [
        {"format_id": "hd", "ext": "mp4", "url": "https://video.fbcdn.net/hd.mp4"},
        {"format_id": "sd", "ext": "mp4", "url": "https://video.fbcdn.net/sd.mp4"}]}
    tx._yt_dlp = lambda: YtDlpGia(info)
    kq = tx.resolve_facebook_audio("1234567890123456")
    assert kq["url"] == "https://video.fbcdn.net/sd.mp4"
    assert kq["content_type"] == "video/mp4"


# ── module: vé, seek_url, nhập playlist Zing ──────────────────────────

class TrichXuatGia:
    async def tim_kiem(self, source, query, limit):
        return []

    async def giai_am_thanh(self, source, target):
        if source == "zing":
            return {"url": "https://zmdcdn.me/audio/a.mp3", "title": "Bài Zing",
                    "duration": 200, "source": "zing",
                    "headers": {"Referer": "https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html",
                                "User-Agent": "UA-gia"}}
        if source == "facebook":
            return {"url": "https://video.fbcdn.net/audio.m4a", "title": "Video FB",
                    "duration": 120, "source": "facebook"}
        return {"url": "fake://am-thanh-1", "title": "Bài giả", "duration": 213,
                "source": source, "channel": "Kênh giả",
                "thumbnail": f"https://i.ytimg.com/vi/{target}/hqdefault.jpg"}

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        assert source == "zing"
        return ("Album Zing", [{"source": "zing", "kind": "song", "id": "a1b2c3d4",
                                "url": "https://zingmp3.vn/bai-hat/Bai/a1b2c3d4.html",
                                "title": "Bài Zing", "channel": "Ca sĩ",
                                "duration": 200, "thumbnail": ""}])


def _mod():
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=tempfile.mkdtemp(prefix="yt304c-"))
    m.nap({})
    m.trich_xuat = TrichXuatGia()
    return mod, m


class BanGia:
    """Phản hồi thượng nguồn giả cho rơ-le: headers + aiter_bytes."""

    def __init__(self, headers, khoi=b"x" * 100):
        self.status_code = 206
        self.headers = headers
        self.khoi = khoi

    async def aiter_bytes(self):
        yield self.khoi

    async def aclose(self):
        return None


def test_seek_tra_seek_url_va_re_le_tua_theo_giay(tmp_path, monkeypatch):
    # Khoá ký URL ghi vào thư mục tạm của test, không chạm dữ liệu thật.
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    _, m = _mod()
    ma, _, nd = asyncio.run(m.tich_hop("integration/session", "POST", {}, {
        "source": "youtube", "target": VIDEO_1}))
    assert ma == 200
    sid = nd["session"]["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop("integration/seek", "POST", {}, {
        "session_id": sid, "giay": 30}))
    assert ma == 200, nd
    seek_url = nd["seek_url"]
    # t được ký cùng URL (chữ ký phủ cả query) nên dạng ?t=30&_het_han&_ky.
    assert seek_url and "/api/stream/" in seek_url and "?t=30" in seek_url
    # Vé lười: chưa có URL thật cho tới khi loa mở — rơ-le tự giải lại.
    ve = seek_url.split("/api/stream/")[1].split("?")[0]
    assert ve in m._luong
    # Mở vé với ?t=30: rơ-le phải tua tới byte tỉ lệ 30/213 của tổng 1000.
    da_nhan = {}

    async def mo_nguon(url, headers):
        da_nhan.update(headers)
        # NV304d yêu cầu probe 0-0 được trả đúng một byte.
        khoang = headers.get("Range", "bytes=0-")
        dau, cuoi = map(int, khoang.removeprefix("bytes=").split("-"))
        return BanGia({"content-range": f"bytes {dau}-{cuoi}/1000"},
                      khoi=b"y" * (cuoi - dau + 1))
    m.mo_nguon = mo_nguon
    ma, headers, noi_dung = asyncio.run(m.luong(ve, None, "GET", "30"))
    assert ma == 206, (ma, headers)
    assert headers["Content-Range"] == "bytes 141-999/1000", headers
    # Iterator rơ-le là lazy: phải đọc hết mới mở khúc thật.
    async def _doc():
        return b"".join([k async for k in noi_dung])
    than = asyncio.run(_doc())
    assert len(than) == 859
    assert da_nhan.get("Range") == "bytes=141-999"


def test_seek_khong_tinh_duoc_offset_tra_400():
    _, m = _mod()
    ve = m._dang_ky_luong({"source": "youtube", "target": VIDEO_1,
                           "url": "fake://khong-co-thoi-luong",
                           "duration": 0})["token"]
    ma, _, _ = asyncio.run(m.luong(ve, None, "GET", "30"))
    assert ma == 400


def test_seek_khong_cap_ve_cho_url_http_bi_chan_trong_queue(tmp_path):
    m = tai_module().Module({}, _thu_muc_du_lieu=str(tmp_path))
    ma, _, nd = asyncio.run(m.tich_hop("integration/session", "POST", {}, {
        "queue": [{"source": "http", "url": "http://127.0.0.1/audio.mp3",
                   "duration": 60}]}))
    assert ma == 200, nd
    ma, _, nd = asyncio.run(m.tich_hop("integration/seek", "POST", {}, {
        "session_id": nd["session"]["session_id"], "giay": 10}))
    assert (ma, nd["error"]) == (400, "invalid_target")
    assert not m._luong


def test_re_le_zing_khong_gui_yeu_cau_toi_dich_chuyen_huong_ngoai(tmp_path, monkeypatch):
    import httpx

    m = tai_module().Module({}, _thu_muc_du_lieu=str(tmp_path))
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    url = "https://static.zmdcdn.me/audio.mp3"
    ve = m._dang_ky_luong({"source": "zing", "url": url, "duration": 60})["token"]
    da_goi = []

    def tra_loi(request):
        da_goi.append(str(request.url))
        if len(da_goi) == 1:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})
        return httpx.Response(200)

    async_client = httpx.AsyncClient

    def tao_client(**tuy_chon):
        tuy_chon["transport"] = httpx.MockTransport(tra_loi)
        return async_client(**tuy_chon)

    monkeypatch.setattr(httpx, "AsyncClient", tao_client)
    ma, _, _ = asyncio.run(m.luong(ve, None, "GET", "10"))
    assert ma == 400
    assert da_goi == [url]


def test_nhap_playlist_zing_qua_trich_xuat(tmp_path):
    _, m = _mod()
    ten, muc, _ = asyncio.run(m._nhap_nguon_playlist(
        "https://zingmp3.vn/album/Album-test/a1b2c3d4.html"))
    assert ten == "Album Zing" and len(muc) == 1
    assert muc[0]["id"] == "a1b2c3d4"


def test_sieu_du_lieu_zing_dien_thoi_luong():
    _, m = _mod()
    muc = asyncio.run(m._sieu_du_lieu(
        "zing", "https://zingmp3.vn/bai-hat/Ten/a1b2c3d4.html",
        {"title": None, "duration": None}))
    assert muc["duration"] == 200


# ── route: ?t= ký cùng URL phải qua xác minh và tới được luong ────

def test_route_truyen_t_cho_luong_4_tham_so(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import duong_module, url_ky
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    class Mod4:
        def __init__(self):
            self.t_nhan = "chua-goi"

        async def luong(self, ve, khoang, phuong_thuc, tua_giay=None):
            self.t_nhan = tua_giay
            return 200, {"Content-Length": "3"}, b"abc"

    class Mod3:
        async def luong(self, ve, khoang, phuong_thuc):
            return 200, {"Content-Length": "3"}, b"xyz"

    mod4, mod3 = Mod4(), Mod3()
    so = SimpleNamespace(
        catalog={
            "youtube": SimpleNamespace(manifest={"duong_module": {"lan_chi": True}}),
            "other": SimpleNamespace(manifest={"duong_module": {"lan_chi": True}}),
        },
        dang_chay={"youtube": mod4, "other": mod3})
    app = FastAPI()
    app.include_router(duong_module.router)
    app.state.so = so
    c = TestClient(app, raise_server_exceptions=False,
                   client=("127.0.0.1", 50000))
    url = url_ky.ky_url("/m/youtube/api/stream/ve-test?t=30",
                        het_han_giay=3600, phuong_thuc="GET")
    r = c.get(url)
    assert r.status_code == 200 and r.content == b"abc"
    assert mod4.t_nhan == "30"
    # Module cũ chỉ cài luong 3 tham số vẫn phục vụ được vé có ?t=.
    url_cu = url_ky.ky_url("/m/other/api/stream/ve-cu?t=30",
                           het_han_giay=3600, phuong_thuc="GET")
    r = c.get(url_cu)
    assert r.status_code == 200 and r.content == b"xyz"
    # t không có trong chữ ký → xác minh hỏng → 404.
    r = c.get("/m/youtube/api/stream/ve-test?t=31&_het_han=9999999999"
              "&_ky=" + "0" * 64)
    assert r.status_code == 404
