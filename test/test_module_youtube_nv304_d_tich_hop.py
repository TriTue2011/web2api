"""Bổ sung NV304d: vé tua, rơ-le byte và biên tin cậy của nguồn.

Tái sử dụng fixture/fake của bộ gốc; không mở mạng hoặc gọi yt-dlp thật.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import pytest

from test_module_youtube_nv304_d import (
    VIDEO_1,
    BanGia,
    MoNguonGia,
    _thu_thap,
    _ve_thuong_nguon,
    _yt_dlp_gia,
    goi,
    mm,  # noqa: F401 — pytest cần import fixture tường minh.
    moi,  # noqa: F401 — pytest cần import fixture tường minh.
    tai_trich_xuat,
    tao_phien,
)


def _tao_seek_http(moi):
    headers = moi.khoa()
    phan_hoi = moi.c.post(
        "/m/youtube/api/integration/session", headers=headers,
        json={"source": "youtube", "target": VIDEO_1},
    )
    assert phan_hoi.status_code == 200, phan_hoi.text
    sid = phan_hoi.json()["session"]["session_id"]
    phan_hoi = moi.c.post(
        "/m/youtube/api/integration/seek", headers=headers,
        json={"session_id": sid, "giay": 30},
    )
    assert phan_hoi.status_code == 200, phan_hoi.text
    url = urlsplit(phan_hoi.json()["seek_url"])
    assert dict(parse_qsl(url.query))["t"] == "30"
    return url


@pytest.mark.parametrize("gia_tri_t", ["30", "60", None],
                         ids=["goc", "doi-60", "bo-t"])
def test_seek_http_query_da_ky_tu_choi_doi_hoac_bo_t(moi, gia_tri_t):
    url = _tao_seek_http(moi)
    query = [(k, v) for k, v in parse_qsl(url.query) if k != "t"]
    if gia_tri_t is not None:
        query.append(("t", gia_tri_t))

    phan_hoi = moi.c.get(url.path + "?" + urlencode(query))

    if gia_tri_t == "30":
        assert phan_hoi.status_code == 206
        assert phan_hoi.headers["content-range"] == "bytes 144-1023/1024"
        assert phan_hoi.content == moi.module.mo_nguon.du_lieu[144:]
    else:
        assert phan_hoi.status_code == 404
        assert phan_hoi.content == b""
        assert moi.module.mo_nguon.da_goi == []


def test_luong_dung_offset_ve_ram_khong_can_truyen_query(mm):
    _, m = mm
    du_lieu = bytes(range(250)) * 4
    ve = m._dang_ky_luong({
        "source": "youtube", "bytes": du_lieu,
        "duration": 100, "tua_giay": 30,
    })
    assert dict(parse_qsl(urlsplit(ve["stream_url"]).query))["t"] == "30"

    # Lõi chỉ truyền token vào module; offset phải lấy từ vé RAM.
    ma, headers, noi_dung = asyncio.run(m.luong(ve["token"], None, "GET"))

    assert ma == 206
    assert headers["Content-Range"] == "bytes 300-999/1000"
    assert noi_dung == du_lieu[300:]


@pytest.mark.parametrize("sua", ["chu_ky", "duong_dan"])
def test_seek_http_sua_chu_ky_hoac_duong_tra_404_trong(moi, sua):
    url = _tao_seek_http(moi)
    query = parse_qsl(url.query)
    duong = url.path
    if sua == "chu_ky":
        assert "_ky" in dict(query)
        query = [(k, v + "x" if k == "_ky" else v) for k, v in query]
    else:
        # Đường mới cũng có vé RAM hợp lệ: 404 phải do sai chữ ký đường,
        # không phải chỉ vì đổi sang một vé không tồn tại.
        duong = _tao_seek_http(moi).path
        assert duong != url.path

    phan_hoi = moi.c.get(duong + "?" + urlencode(query))

    assert phan_hoi.status_code == 404
    assert phan_hoi.content == b""
    assert moi.module.mo_nguon.da_goi == []


def test_range_thang_vi_tri_luu_trong_ve_ram(mm):
    _, m = mm
    du_lieu = bytes(range(250)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100, total=len(du_lieu))
    m._luong["ve1"]["tua_giay"] = 50

    ma, headers, luong = asyncio.run(m.luong("ve1", "bytes=7-19", "GET"))
    assert ma == 206
    assert headers["Content-Range"] == "bytes 7-19/1000"
    noi_dung, loi = _thu_thap(luong)

    assert loi is None
    assert noi_dung == du_lieu[7:20]
    assert m.mo_nguon.da_goi[0][1]["Range"] == "bytes=7-19"


@pytest.mark.parametrize("giay_cuoi", [213, 9999], ids=["dung-cuoi", "qua-cuoi"])
def test_seek_url_co_trong_bai_va_none_o_cuoi(mm, giay_cuoi):
    _, m = mm
    sid = tao_phien(m)["session_id"]

    ma, _, giua = goi(m, "integration/seek", {"session_id": sid, "giay": 30})
    assert ma == 200
    assert giua["position"] == 30
    url = urlsplit(giua["seek_url"])
    assert url.path.startswith("/m/youtube/api/stream/")
    assert dict(parse_qsl(url.query))["t"] == "30"

    ma, _, cuoi = goi(
        m, "integration/seek", {"session_id": sid, "giay": giay_cuoi},
    )
    assert ma == 200
    assert cuoi["position"] == 213
    assert cuoi["seek_url"] is None


def test_probe_tong_byte_dong_truoc_khi_phat_va_dung_byte(mm):
    _, m = mm
    du_lieu = bytes(range(250)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100)
    m._luong["ve1"]["tua_giay"] = 30
    nguon = m.mo_nguon
    cac_ban = []

    async def mo(url, headers):
        if cac_ban:
            assert cac_ban[0].da_dong
        ban = await nguon(url, headers)
        cac_ban.append(ban)
        return ban

    m.mo_nguon = mo
    ma, headers, luong = asyncio.run(m.luong("ve1", None, "GET"))
    assert ma == 206
    assert headers["Content-Range"] == "bytes 300-999/1000"
    assert len(cac_ban) == 1
    assert cac_ban[0].da_dong
    assert nguon.da_goi[0][1]["Range"] == "bytes=0-0"

    noi_dung, loi = _thu_thap(luong)
    assert loi is None
    assert noi_dung == du_lieu[300:]
    assert len(cac_ban) == 2
    assert all(ban.da_dong for ban in cac_ban)


@pytest.mark.parametrize(
    "ma_probe,content_range",
    [
        pytest.param(200, "bytes 0-0/1000", id="khong-phai-206"),
        pytest.param(206, None, id="thieu-content-range"),
        pytest.param(206, "khong-phai-content-range", id="sai-cu-phap"),
        pytest.param(206, "bytes 1-1/1000", id="lech-offset"),
        pytest.param(206, "bytes 0-9/1000", id="sai-do-dai-probe"),
        pytest.param(206, "bytes 0-0/*", id="khong-biet-tong"),
        pytest.param(206, "bytes 0-0/0", id="tong-bang-khong"),
    ],
)
def test_probe_content_range_khong_hop_le_van_dong(mm, ma_probe, content_range):
    _, m = mm
    _ve_thuong_nguon(m, b"du-lieu-gia", duration=100)
    m._luong["ve1"]["tua_giay"] = 30
    headers = {} if content_range is None else {"Content-Range": content_range}
    ban = BanGia(ma_probe, headers, [b"khong-duoc-phat"])
    cac_lan_mo = []

    async def mo(url, headers):
        cac_lan_mo.append((url, dict(headers)))
        return ban

    m.mo_nguon = mo
    ma, _, noi_dung = asyncio.run(m.luong("ve1", None, "GET"))

    assert ma == 400
    assert noi_dung == b""
    assert ban.da_dong
    assert len(cac_lan_mo) == 1
    assert cac_lan_mo[0][1]["Range"] == "bytes=0-0"


@pytest.mark.parametrize(
    "tham_so",
    [
        pytest.param({"tua_giay": "NaN", "duration": 100}, id="tua-nan"),
        pytest.param({"tua_giay": "Inf", "duration": 100}, id="tua-inf"),
        pytest.param({"tua_giay": "-Inf", "duration": 100}, id="tua-am-inf"),
        pytest.param({"tua_giay": 30, "duration": "NaN"}, id="duration-nan"),
        pytest.param({"tua_giay": 30, "duration": "Inf"}, id="duration-inf"),
        pytest.param({"tua_giay": 30, "duration": None}, id="duration-none"),
        pytest.param({"tua_giay": 30}, id="thieu-duration"),
    ],
)
def test_luong_tua_khong_hop_le_400_khong_mo_nguon(mm, tham_so):
    _, m = mm
    _ve_thuong_nguon(m, b"du-lieu-gia", total=11)
    m._luong["ve1"].pop("duration")
    m._luong["ve1"].update(tham_so)

    ma, _, noi_dung = asyncio.run(m.luong("ve1", None, "GET"))

    assert ma == 400
    assert noi_dung == b""
    assert m.mo_nguon.da_goi == []


@pytest.mark.parametrize("giay", ["NaN", "Inf", "-Inf"])
def test_seek_nan_inf_400_khong_mo_nguon(mm, giay):
    _, m = mm
    _ve_thuong_nguon(m, b"du-lieu-gia")
    sid = tao_phien(m)["session_id"]
    da_giai = list(m.trich_xuat.da_giai)

    ma, _, noi_dung = goi(
        m, "integration/seek", {"session_id": sid, "giay": giay},
    )

    assert ma == 400
    assert noi_dung["error"] == "invalid_seek_position"
    assert m.mo_nguon.da_goi == []
    assert m.trich_xuat.da_giai == da_giai


@pytest.mark.parametrize("ten_hosts", ["YOUTUBE_STREAM_HOSTS", "FACEBOOK_STREAM_HOSTS"])
@pytest.mark.parametrize(
    "mau,cho_phep",
    [
        pytest.param("https://{host}/audio", True, id="exact"),
        pytest.param("https://media.{host}/audio", True, id="subdomain"),
        pytest.param("HTTPS://MEDIA.{host}/audio", True, id="hoa-thuong"),
        pytest.param("http://{host}/audio", False, id="http"),
        pytest.param("file://{host}/audio", False, id="file"),
        pytest.param("https://user@{host}/audio", False, id="userinfo"),
        pytest.param("https://user:pass@{host}/audio", False, id="userinfo-password"),
        pytest.param("https://@{host}/audio", False, id="userinfo-rong"),
        pytest.param("https://{host}.evil.test/audio", False, id="suffix-evil"),
        pytest.param("https://evil{host}/audio", False, id="khong-co-ranh-gioi-host"),
        pytest.param("https://{host}@evil.test/audio", False, id="host-trong-userinfo"),
    ],
)
def test_host_allowlist_kiem_scheme_userinfo_va_ranh_gioi(ten_hosts, mau, cho_phep):
    tx = tai_trich_xuat()
    hosts = getattr(tx, ten_hosts)
    for host in hosts:
        url = mau.format(host=host)
        assert tx._host_la_cho_phep(url, hosts) is cho_phep, url


def test_facebook_id_fullmatch_tu_choi_newline_cuoi(monkeypatch):
    tx = tai_trich_xuat()
    cac_lan_trich = []
    url = "https://video.fbcdn.net/am-thanh-gia.m4a"

    def yt_gia():
        cac_lan_trich.append(True)
        return _yt_dlp_gia({
            "formats": [{"url": url, "acodec": "mp4a", "vcodec": "none"}],
            "title": "Video giả", "duration": 100,
        })

    monkeypatch.setattr(tx, "_yt_dlp", yt_gia)
    assert tx.resolve_facebook_audio("123456789012345")["url"] == url
    cac_lan_trich.clear()

    with pytest.raises(tx.TrichXuatUnavailable, match="^invalid_facebook_id$"):
        tx.resolve_facebook_audio("123456789012345\n")

    assert cac_lan_trich == []


@pytest.mark.parametrize("source,target", [
    ("youtube", VIDEO_1), ("facebook", "123456789012345"),
])
@pytest.mark.parametrize("ma_cu", [403, 404, 410, 200],
                         ids=["403", "404", "410", "bo-qua-range"])
def test_refresh_mo_url_moi_giu_headers_va_range_dung(mm, monkeypatch, source, target, ma_cu):
    mod, m = mm
    du_lieu = bytes(range(250)) * 4
    _ve_thuong_nguon(m, du_lieu, total=len(du_lieu))
    nguon = m.mo_nguon
    url_cu = "https://media.example.test/cu"
    url_moi = "https://media.example.test/moi"
    m._luong["ve1"].update(
        source=source, target=target, url=url_cu,
        headers={"Referer": "https://cu.example.test/", "X-Nguon": "cu",
                 "Range": "bytes=0-1", "rAnGe": "bytes=2-3"},
    )
    monkeypatch.setattr(mod, "CHO_NOI_LAI", (0,) * len(mod.CHO_NOI_LAI))
    cac_lan_giai = []
    cac_lan_mo = []
    cac_ban = []

    async def giai(source, target):
        cac_lan_giai.append((source, target))
        return {
            "url": url_moi,
            "headers": {"Referer": "https://moi.example.test/", "X-Nguon": "moi",
                        "Range": "bytes=0-0", "rAnGe": "bytes=1-1"},
        }

    async def mo(url, headers):
        cac_lan_mo.append((url, dict(headers)))
        if url == url_moi:
            ban = await nguon(url, headers)
        else:
            ban = BanGia(ma_cu, {}, [b"byte-cu-khong-duoc-lo"])
        cac_ban.append(ban)
        return ban

    monkeypatch.setattr(m.trich_xuat, "giai_am_thanh", giai)
    m.mo_nguon = mo
    ma, headers, luong = asyncio.run(m.luong("ve1", "bytes=111-198", "GET"))
    assert ma == 206
    assert headers["Content-Range"] == "bytes 111-198/1000"
    noi_dung, loi = _thu_thap(luong)

    assert loi is None
    assert noi_dung == du_lieu[111:199]
    assert cac_lan_giai == [(source, target)]
    assert [url for url, _ in cac_lan_mo] == [url_cu, url_moi]
    for (_, headers), nhan in zip(cac_lan_mo, ("cu", "moi")):
        assert headers["Referer"] == f"https://{nhan}.example.test/"
        assert headers["X-Nguon"] == nhan
        assert [(k.lower(), v) for k, v in headers.items() if k.lower() == "range"] == [
            ("range", "bytes=111-198"),
        ]
    assert all(ban.da_dong for ban in cac_ban)


def test_http_lazy_kiem_lan_truoc_mo_va_dung_url_da_kiem(mm, monkeypatch):
    mod, m = mm
    du_lieu = bytes(range(250)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100)
    nguon = m.mo_nguon
    target = "http://media.lan.test/chua-kiem.mp3"
    url_da_kiem = "http://media.lan.test/da-kiem.mp3"
    m._luong["ve1"].pop("url")
    m._luong["ve1"].update(source="http", target=target, tua_giay=30)
    cac_buoc = []

    async def kiem(url):
        cac_buoc.append(("kiem", url))
        return url_da_kiem

    async def mo(url, headers):
        cac_buoc.append(("mo", url))
        return await nguon(url, headers)

    monkeypatch.setattr(mod, "kiem_url_lan", kiem)
    m.mo_nguon = mo
    assert cac_buoc == []
    ma, headers, luong = asyncio.run(m.luong("ve1", None, "GET"))
    assert ma == 206
    assert headers["Content-Range"] == "bytes 300-999/1000"
    noi_dung, loi = _thu_thap(luong)

    assert loi is None
    assert noi_dung == du_lieu[300:]
    assert cac_buoc == [
        ("kiem", target), ("mo", url_da_kiem), ("mo", url_da_kiem),
    ]
    assert m.trich_xuat.da_giai == []


def test_http_lazy_bi_chan_lan_400_khong_mo_nguon(mm, monkeypatch):
    mod, m = mm
    _ve_thuong_nguon(m, b"du-lieu-gia", duration=100)
    target = "http://ngoai-lan.example.test/am-thanh.mp3"
    m._luong["ve1"].pop("url")
    m._luong["ve1"].update(source="http", target=target, tua_giay=30)
    da_kiem = []

    async def kiem(url):
        da_kiem.append(url)
        raise ValueError("url_khong_thuoc_lan_gia")

    monkeypatch.setattr(mod, "kiem_url_lan", kiem)
    ma, _, noi_dung = asyncio.run(m.luong("ve1", None, "GET"))

    assert ma == 400
    assert noi_dung == b""
    assert da_kiem == [target]
    assert m.mo_nguon.da_goi == []
    assert m.trich_xuat.da_giai == []


def test_httpx_redirect_302_khong_request_loopback_va_dong_nguon(mm, monkeypatch):
    mod, m = mm
    du_lieu_noi_bo = b"du-lieu-noi-bo-gia"
    _ve_thuong_nguon(m, du_lieu_noi_bo, total=len(du_lieu_noi_bo))
    url_nguon = "https://media.googlevideo.com/am-thanh-gia"
    m._luong["ve1"]["url"] = url_nguon
    m.mo_nguon = None
    monkeypatch.setattr(mod, "CHO_NOI_LAI", (0,) * len(mod.CHO_NOI_LAI))
    client_goc = httpx.AsyncClient
    cac_client = []
    cac_request = []
    cac_ban = []

    def xu_ly(request):
        cac_request.append(request)
        if str(request.url) == url_nguon:
            ban = httpx.Response(
                302, headers={"Location": "http://127.0.0.1/noi-bo-gia"},
                stream=httpx.ByteStream(b"redirect-khong-duoc-phat"),
                request=request,
            )
        else:
            # Nếu tự đi theo redirect, vẫn chỉ trả dữ liệu giả qua transport.
            ban = httpx.Response(
                206,
                headers={"Content-Range":
                         f"bytes 0-{len(du_lieu_noi_bo) - 1}/{len(du_lieu_noi_bo)}"},
                stream=httpx.ByteStream(du_lieu_noi_bo), request=request,
            )
        assert not ban.is_closed
        cac_ban.append(ban)
        return ban

    def tao_client(*args, **kwargs):
        kwargs.update(transport=httpx.MockTransport(xu_ly), trust_env=False)
        client = client_goc(*args, **kwargs)
        cac_client.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", tao_client)
    ma, _, luong = asyncio.run(m.luong("ve1", None, "GET"))
    assert ma == 200
    noi_dung, loi = _thu_thap(luong)

    assert cac_request
    assert not any(request.url.host == "127.0.0.1" for request in cac_request)
    assert all(str(request.url) == url_nguon for request in cac_request)
    assert noi_dung == b""
    assert isinstance(loi, mod.LoiNguonKhongCuuDuoc)
    assert "thượng nguồn trả 302" in str(loi)
    assert cac_ban and all(ban.is_closed for ban in cac_ban)
    assert cac_client and all(client.is_closed for client in cac_client)


@pytest.mark.parametrize("huy", [False, True], ids=["send-loi", "send-bi-huy"])
def test_httpx_send_loi_hoac_cancel_van_dong_client(mm, monkeypatch, huy):
    mod, m = mm
    _ve_thuong_nguon(m, b"am-thanh-gia", total=12)
    m._luong["ve1"]["url"] = "https://media.googlevideo.com/am-thanh-gia"
    m.mo_nguon = None
    monkeypatch.setattr(mod, "CHO_NOI_LAI", (0,) * len(mod.CHO_NOI_LAI))
    client_goc = httpx.AsyncClient
    cac_client = []
    cac_request = []
    cac_byte = []

    def xu_ly(request):
        cac_request.append(request)
        if huy:
            raise asyncio.CancelledError("send-bi-huy-gia")
        raise httpx.ReadError("send-loi-gia", request=request)

    def tao_client(*args, **kwargs):
        kwargs.update(transport=httpx.MockTransport(xu_ly), trust_env=False)
        client = client_goc(*args, **kwargs)
        cac_client.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", tao_client)

    async def chay():
        ma, _, luong = await m.luong("ve1", None, "GET")
        assert ma == 200
        loi_mong_doi = asyncio.CancelledError if huy else mod.LoiNguonKhongCuuDuoc
        try:
            with pytest.raises(loi_mong_doi):
                async for khuc in luong:
                    cac_byte.append(khuc)
        finally:
            await luong.aclose()

    asyncio.run(chay())

    assert cac_request
    assert cac_byte == []
    assert cac_client and all(client.is_closed for client in cac_client)


def test_stop_trong_lazy_resolve_tra_404_khong_mo_nguon(mm, monkeypatch):
    _, m = mm
    _ve_thuong_nguon(m, b"am-thanh-gia", total=12)
    m._luong["ve1"].pop("url")
    m._luong["ve1"]["target"] = VIDEO_1
    cac_lan_giai = []
    cac_stop = []

    async def giai(source, target):
        cac_lan_giai.append((source, target))
        ma, _, body = await m.tich_hop("integration/stop", "POST", {}, {})
        cac_stop.append((ma, body["stopped"]))
        return {"url": "https://media.googlevideo.com/am-thanh-gia"}

    monkeypatch.setattr(m.trich_xuat, "giai_am_thanh", giai)
    ma, _, noi_dung = asyncio.run(m.luong("ve1", None, "GET"))

    assert cac_lan_giai == [("youtube", VIDEO_1)]
    assert cac_stop == [(200, True)]
    assert "ve1" not in m._luong
    assert ma == 404
    assert noi_dung == b""
    assert m.mo_nguon.da_goi == []


def test_stop_khi_tao_stream_khong_duoc_cap_ve_moi(mm, monkeypatch):
    _, m = mm
    ma, _, phien = goi(m, "integration/session", {
        "source": "youtube", "target": VIDEO_1,
    })
    assert ma == 200
    generation = m._luong_generation

    async def giai(source, target):
        await m.tich_hop("integration/stop", "POST", {}, {})
        return {"url": "https://rr1.googlevideo.com/audio", "duration": 90}

    monkeypatch.setattr(m.trich_xuat, "giai_am_thanh", giai)
    ma, _, body = asyncio.run(m.tich_hop(
        "integration/stream", "POST", {},
        {"source": "youtube", "target": VIDEO_1}))

    assert ma == 409 and body["error"] == "stream_stopped"
    assert m._luong_generation == generation + 1
    assert m._luong == {}


def test_stop_phien_cu_da_dung_khong_xoa_ve_phien_moi(mm):
    _, m = mm
    cu = tao_phien(m)
    ma, _, _ = goi(m, "integration/stop", {"session_id": cu["session_id"]})
    assert ma == 200
    moi = tao_phien(m)
    ve = m._dang_ky_luong({"source": "youtube", "url": "https://a.googlevideo.com/b"})
    m._luong[ve["token"]]["session_id"] = moi["session_id"]

    ma, _, body = goi(m, "integration/stop", {"session_id": cu["session_id"]})

    assert ma == 200 and body["stopped"] is False
    assert ve["token"] in m._luong
    assert m._phien_hien_tai == moi["session_id"]


def test_seek_khong_biet_thoi_luong_cap_ve_de_giai_luc_mo(mm, monkeypatch):
    _, m = mm
    m.mo_nguon = MoNguonGia(bytes(range(250)) * 4)
    phien = tao_phien(m)
    m._phien[phien["session_id"]]["duration"] = None
    m._phien[phien["session_id"]]["item"]["duration"] = None
    ma, _, body = goi(m, "integration/seek", {
        "session_id": phien["session_id"], "giay": 30,
    })

    assert ma == 200 and "?t=30" in body["seek_url"]
    ve = body["seek_url"].split("/api/stream/")[1].split("?")[0]
    assert m._luong[ve]["duration"] is None

    async def giai(source, target):
        return {"url": "https://a.googlevideo.com/b", "duration": 100,
                "content_type": "audio/mp4"}

    monkeypatch.setattr(m.trich_xuat, "giai_am_thanh", giai)
    ma, headers, luong = asyncio.run(m.luong(ve, None, "GET"))

    assert ma == 206
    assert headers["Content-Range"] == "bytes 300-999/1000"
    assert m._luong[ve]["duration"] == 100


def test_stop_trong_probe_tra_404_dong_phan_hoi_khong_request_tiep(mm):
    _, m = mm
    du_lieu = bytes(range(250)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100)
    m._luong["ve1"]["tua_giay"] = 30
    nguon = m.mo_nguon
    cac_ban = []
    cac_stop = []

    async def mo(url, headers):
        ban = await nguon(url, headers)
        cac_ban.append(ban)
        ma, _, body = await m.tich_hop("integration/stop", "POST", {}, {})
        cac_stop.append((ma, body["stopped"]))
        return ban

    m.mo_nguon = mo
    ma, _, noi_dung = asyncio.run(m.luong("ve1", None, "GET"))

    assert cac_stop == [(200, True)]
    assert "ve1" not in m._luong
    assert ma == 404
    assert noi_dung == b""
    assert len(nguon.da_goi) == 1
    assert nguon.da_goi[0][1]["Range"] == "bytes=0-0"
    assert len(cac_ban) == 1
    assert cac_ban[0].status_code == 206
    assert cac_ban[0].da_dong


@pytest.mark.parametrize("kich_thuoc_khuc", [65536, 131072],
                         ids=["truoc-request-ke", "trong-phan-hoi"])
def test_stop_sau_khuc_dau_khong_them_byte_hay_request(mm, monkeypatch, kich_thuoc_khuc):
    mod, m = mm
    du_lieu = bytes(range(256)) * 1024
    _ve_thuong_nguon(m, du_lieu, total=len(du_lieu))
    monkeypatch.setattr(mod, "KHUC", kich_thuoc_khuc)
    monkeypatch.setattr(mod, "CHO_NOI_LAI", (0,) * len(mod.CHO_NOI_LAI))
    nguon = m.mo_nguon
    cac_ban = []

    async def mo(url, headers):
        ban = await nguon(url, headers)
        cac_ban.append(ban)
        return ban

    m.mo_nguon = mo

    async def chay():
        ma, _, luong = await m.luong("ve1", None, "GET")
        assert ma == 200
        try:
            khuc_dau = await anext(luong)
            assert khuc_dau == du_lieu[:65536]
            ma, _, body = await m.tich_hop("integration/stop", "POST", {}, {})
            assert ma == 200 and body["stopped"] is True
            assert "ve1" not in m._luong
            return b"".join([khuc async for khuc in luong])
        finally:
            await luong.aclose()

    noi_dung_sau_stop = asyncio.run(chay())

    assert noi_dung_sau_stop == b""
    assert len(nguon.da_goi) == 1
    assert len(cac_ban) == 1
    assert cac_ban[0].da_dong


@pytest.mark.parametrize("ten_hosts", ["YOUTUBE_STREAM_HOSTS", "FACEBOOK_STREAM_HOSTS"])
@pytest.mark.parametrize("port,cho_phep", [
    pytest.param("443", True, id="https-443"),
    pytest.param("444", False, id="port-khac-443"),
    pytest.param("sai", False, id="port-khong-phai-so"),
    pytest.param("99999", False, id="port-vuot-mien"),
])
def test_host_allowlist_chi_nhan_port_443_hop_le(ten_hosts, port, cho_phep):
    tx = tai_trich_xuat()
    hosts = getattr(tx, ten_hosts)
    for host in hosts:
        url = f"https://{host}:{port}/am-thanh-gia"
        assert tx._host_la_cho_phep(url, hosts) is cho_phep, url
