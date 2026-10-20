"""Test NV153 đợt D4 — bài kế/bài trước có khoá tuần tự + tua + thời lượng.

Bao phủ:
- `PlaybackSession.advance`/`dat_vi_tri` và `PlayerCore.bai_ke/bai_truoc/tua`
  (khoá tuần tự `_khoa_chuyen_bai`): bấm liên tiếp không sinh hai phiên,
  không bỏ sót bài; «dừng là dừng hẳn» — dừng rồi bài kế không sống lại.
- Thời lượng thật: `prepare_stream` điền `duration` (thay vì 0).
- `GET /api/stream/{token}?t=<giây>`: phục vụ từ giây cần tới (sidx MP4,
  không có sidx thì tỉ lệ theo thời lượng) — đồng bộ với rơ-le `ro_le`
  (rơ-le mở lại từ `dau` đã tính).
- Route `/api/integration/next|prev|seek`: 401/404/409 đúng mã.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

import dich_vu
import ro_le
import server
from conftest import tieu_de_xac_thuc
from session import PlaybackSession, normalize_session_item


def _muc(i, thoi_luong=100):
    return normalize_session_item("youtube", {
        "kind": "video", "id": f"bai-{i}", "title": f"Bai {i}",
        "duration": thoi_luong})


@pytest.fixture()
def core_co_hang_doi(tmp_path):
    """PlayerCore + phiên web có 4 bài (bai-1..bai-4), đang ở bai-1."""
    core = dich_vu.tao_core(str(tmp_path))
    muc = [_muc(i) for i in range(1, 5)]
    core.playback_session.start(
        "youtube", "bai-1", fallback_item=muc[0],
        output_entity_ids=[], queue_items=muc)
    return core


# ── bài kế / bài trước ở mức core ──────────────────────────────────────────

def test_bai_ke_tien_dung_mot_bai(core_co_hang_doi):
    core = core_co_hang_doi
    # vị trí cũ khác 0: bài kế phải đặt lại về 0 (không giữ lại chỗ cũ)
    core.tua(None, 42)
    ss = core.bai_ke()
    assert ss["queue"]["index"] == 1
    assert ss["item"]["id"] == "bai-2"
    assert ss["state"] == "playing"
    assert ss["position"] == 0


def test_bai_truoc_lui_dung_mot_bai(core_co_hang_doi):
    core_co_hang_doi.bai_ke()
    ss = core_co_hang_doi.bai_truoc()
    assert ss["queue"]["index"] == 0
    assert ss["item"]["id"] == "bai-1"


def test_bai_ke_cuoi_hang_doi_bao_loi(core_co_hang_doi):
    for _ in range(3):
        core_co_hang_doi.bai_ke()
    with pytest.raises(ValueError, match="het_hang_doi"):
        core_co_hang_doi.bai_ke()


def test_bai_truoc_dau_hang_doi_bao_loi(core_co_hang_doi):
    with pytest.raises(ValueError, match="dau_hang_doi"):
        core_co_hang_doi.bai_truoc()


def test_dung_roi_bai_ke_khong_song_lai(core_co_hang_doi):
    core_co_hang_doi.stop(session_id="web")
    with pytest.raises(ValueError, match="session_not_found"):
        core_co_hang_doi.bai_ke()
    ss = core_co_hang_doi.get_session()
    assert ss["state"] == "idle"
    assert ss["item"] is None


def test_bam_lien_tiep_5_lan_chuyen_tuan_tu(tmp_path):
    """5 luồng bấm bài kế cùng lúc: mỗi bài kế đúng một lần, khoá tuần tự giữ
    cho không có hai lần chuyển nào chạy chồng nhau."""
    core = dich_vu.tao_core(str(tmp_path))
    muc = [_muc(i) for i in range(1, 8)]
    core.playback_session.start(
        "youtube", "bai-1", fallback_item=muc[0],
        output_entity_ids=[], queue_items=muc)
    goc = core.playback_session.advance
    trang_thai = {"dang_chay": 0, "toi_da": 0}

    def boc(huong, session_id=None):
        trang_thai["dang_chay"] += 1
        trang_thai["toi_da"] = max(trang_thai["toi_da"],
                                   trang_thai["dang_chay"])
        time.sleep(0.05)  # ép chồng nhau nếu không có khoá
        try:
            return goc(huong, session_id)
        finally:
            trang_thai["dang_chay"] -= 1

    core.playback_session.advance = boc
    loi = []
    def bam():
        try:
            core.bai_ke()
        except Exception as e:  # noqa: BLE001 — gom để assert sau
            loi.append(e)

    cac_luong = [threading.Thread(target=bam) for _ in range(5)]
    for t in cac_luong:
        t.start()
    for t in cac_luong:
        t.join()
    assert loi == []
    assert trang_thai["toi_da"] == 1, trang_thai
    ss = core.get_session()
    assert ss["queue"]["index"] == 5
    assert ss["item"]["id"] == "bai-6"


# ── tua: vị trí phát ở mức core ─────────────────────────────────────────────

def test_tua_dat_vi_tri(core_co_hang_doi):
    ss = core_co_hang_doi.tua("web", 42)
    assert ss["position"] == 42


def test_tua_vuot_thoi_luong_bi_kep(core_co_hang_doi):
    ss = core_co_hang_doi.tua("web", 10 ** 9)
    assert ss["position"] == 100  # thời lượng bài


def test_tua_am_bao_loi(core_co_hang_doi):
    with pytest.raises(ValueError, match="invalid_seek_position"):
        core_co_hang_doi.tua("web", -5)


def test_tua_khong_phai_so_bao_loi(core_co_hang_doi):
    with pytest.raises(ValueError, match="invalid_seek_position"):
        core_co_hang_doi.tua("web", "khong-phai-so")


def test_tua_phien_khong_co_bao_loi(core_co_hang_doi):
    core_co_hang_doi.stop(session_id="web")
    with pytest.raises(ValueError, match="session_not_found"):
        core_co_hang_doi.tua("web", 10)


# ── route next/prev/seek ────────────────────────────────────────────────────

def _nap_phien(app, so_bai=3):
    """Nạp phiên web có hàng đợi; mã bài đúng khuôn video YouTube 11 ký tự
    để đường tạo stream URL (seek) chạy được như ngoài thật."""
    core = app.state.core
    muc = [normalize_session_item("youtube", {
        "kind": "video", "id": f"vid{i:08d}", "title": f"Bai {i}",
        "duration": 100}) for i in range(1, so_bai + 1)]
    core.playback_session.start(
        "youtube", "vid00000001", fallback_item=muc[0],
        output_entity_ids=[], queue_items=muc)
    return core


def test_next_prev_endpoint_200(ung_dung):
    app, token, _thu_muc = ung_dung
    _nap_phien(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/integration/next", headers=tieu_de_xac_thuc(token))
        assert r.status_code == 200
        than = r.json()
        assert than["success"] is True
        assert than["session"]["queue"]["index"] == 1
        r = c.post("/api/integration/prev", headers=tieu_de_xac_thuc(token))
        assert r.status_code == 200
        assert r.json()["session"]["queue"]["index"] == 0


def test_next_sai_khoa_401(ung_dung):
    app, _token, _thu_muc = ung_dung
    _nap_phien(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/integration/next",
                   headers={"Authorization": "Bearer sai"})
        assert r.status_code == 401


def test_next_khong_phien_404(ung_dung):
    app, token, _thu_muc = ung_dung
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/integration/next", headers=tieu_de_xac_thuc(token))
        assert r.status_code == 404
        assert r.json()["error"] == "session_not_found"


def test_next_cuoi_hang_doi_409(ung_dung):
    app, token, _thu_muc = ung_dung
    _nap_phien(app, so_bai=2)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.post("/api/integration/next",
                      headers=tieu_de_xac_thuc(token)).status_code == 200
        r = c.post("/api/integration/next", headers=tieu_de_xac_thuc(token))
        assert r.status_code == 409
        assert r.json()["error"] == "queue_end"


def test_seek_endpoint_tra_seek_url(ung_dung):
    app, token, _thu_muc = ung_dung
    core = _nap_phien(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/integration/seek",
                   headers=tieu_de_xac_thuc(token),
                   json={"session_id": "web", "giay": 30})
        assert r.status_code == 200
        than = r.json()
        assert than["success"] is True
        assert than["session"]["position"] == 30
        assert "?t=30" in than["seek_url"]
        # seek_url là URL luồng đã ký của đúng bài đang phát
        assert "/api/stream/" in than["seek_url"]


def test_seek_khong_hop_le_400(ung_dung):
    app, token, _thu_muc = ung_dung
    _nap_phien(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        for than_gui in ({"giay": -1}, {"giay": "abc"}, {}):
            r = c.post("/api/integration/seek",
                       headers=tieu_de_xac_thuc(token), json=than_gui)
            assert r.status_code == 400, than_gui


# ── thời lượng thật ở prepare_stream ─────────────────────────────────────────

def test_prepare_stream_dien_thoi_luong_that(monkeypatch, tmp_path):
    core = dich_vu.tao_core(str(tmp_path))

    def giai_gia(video_id):
        return {"url": "http://127.0.0.1/x.mp4", "headers": {},
                "content_type": "audio/mp4", "duration": 185}

    monkeypatch.setattr(dich_vu, "resolve_youtube_audio", giai_gia)
    _target, giai = core.prepare_stream("youtube", "dTeH03NpB1A")
    assert giai["duration"] == 185


def test_resolve_youtube_audio_lay_duration_tu_info():
    from streaming import resolve_youtube_audio
    giai = resolve_youtube_audio(
        "dTeH03NpB1A",
        extractor=lambda url, timeout: {
            "url": "https://rr1---sn.googlevideo.com/videoplayback?x=1",
            "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none",
            "duration": 242,
        })
    assert giai["duration"] == 242


# ── GET /api/stream/{token}?t= — tua theo giây ở mức byte ────────────────────

def _sidx_gia():
    """MP4 giả có sidx: 3 mảnh, mỗi mảnh 10 giây, offset tuyệt đối đã biết."""
    hop = bytearray()
    hop += b"\x00\x00\x00\x14ftyp" + b"\x00" * 12          # 20 byte đầu
    # sidx v0: size=68, timescale=1000, 3 ref (200B/10s, 300B/10s, 400B/10s)
    sidx = bytearray()
    sidx += b"\x00\x00\x00\x44sidx"
    sidx += b"\x00\x00\x00\x00"      # version/flags
    sidx += b"\x00\x00\x00\x01"      # reference_ID
    sidx += (1000).to_bytes(4, "big")   # timescale
    sidx += (0).to_bytes(4, "big")      # earliest_presentation_time
    sidx += (0).to_bytes(4, "big")      # first_offset
    sidx += b"\x00\x00"             # reserved
    sidx += (3).to_bytes(2, "big")  # count
    for dai, giay in ((200, 10000), (300, 10000), (400, 10000)):
        sidx += dai.to_bytes(4, "big") + giay.to_bytes(4, "big") + b"\x00\x00\x00\x00"
    assert len(sidx) == 68
    hop += bytes(sidx)              # offset tuyệt đối của mảnh 1 = 88
    hop += b"\x00" * 900            # thân mảnh giả
    return bytes(hop)


def test_offset_tua_theo_sidx(may_chu_gia):
    buf = _sidx_gia()
    may = may_chu_gia("binh_thuong", buf)
    giai = {"url": may.url, "headers": {}, "content_type": "audio/mp4",
            "duration": 30}
    assert asyncio.run(server._offset_theo_giay(giai, 0)) == 88
    assert asyncio.run(server._offset_theo_giay(giai, 15)) == 288
    assert asyncio.run(server._offset_theo_giay(giai, 25)) == 588
    assert asyncio.run(server._offset_theo_giay(giai, 10**6)) == 588


def test_offset_tua_ti_le_khi_khong_co_sidx(may_chu_gia):
    du_lieu = bytes(i % 251 for i in range(1000))
    may = may_chu_gia("binh_thuong", du_lieu)
    giai = {"url": may.url, "headers": {}, "content_type": "audio/mpeg",
            "duration": 100}
    assert asyncio.run(server._offset_theo_giay(giai, 50)) == 500
    assert asyncio.run(server._offset_theo_giay(giai, 0)) == 0


def test_offset_tua_khong_biet_thoi_luong_thi_none(may_chu_gia):
    du_lieu = bytes(i % 251 for i in range(1000))
    may = may_chu_gia("binh_thuong", du_lieu)
    giai = {"url": may.url, "headers": {}, "content_type": "audio/mpeg"}
    assert asyncio.run(server._offset_theo_giay(giai, 50)) is None


def test_stream_tu_giay_tra_206_va_byte_dung(monkeypatch, ung_dung,
                                             may_chu_gia):
    du_lieu = bytes(i % 251 for i in range(1000))
    may = may_chu_gia("binh_thuong", du_lieu)
    app, _token, _thu_muc = ung_dung
    core = app.state.core

    def giai_gia(source, target):
        return {"url": may.url, "headers": {}, "content_type": "audio/mpeg",
                "duration": 100}

    monkeypatch.setattr(core, "resolve_stream", giai_gia)
    url = core.create_stream_url("youtube", "dTeH03NpB1A",
                                 "http://127.0.0.1:8713")
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(url + "?t=50")
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 500-999/1000"
    assert r.content == du_lieu[500:]
    assert ro_le.lay_thong_ke()["luong"] >= 1  # đi qua rơ-le như luồng thường


def test_stream_tu_giay_khong_hop_le_400(monkeypatch, ung_dung, may_chu_gia):
    du_lieu = bytes(i % 251 for i in range(1000))
    may = may_chu_gia("binh_thuong", du_lieu)
    app, _token, _thu_muc = ung_dung
    core = app.state.core

    def giai_gia(source, target):
        return {"url": may.url, "headers": {}, "content_type": "audio/mpeg",
                "duration": 100}

    monkeypatch.setattr(core, "resolve_stream", giai_gia)
    url = core.create_stream_url("youtube", "dTeH03NpB1A",
                                 "http://127.0.0.1:8713")
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get(url + "?t=abc").status_code == 400
        assert c.get(url + "?t=-5").status_code == 400
