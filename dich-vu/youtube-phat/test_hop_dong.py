"""Test hợp đồng 10 route API — NV153 đợt A.

10 route: health, search, status, history, stop, session, playlists GET+POST,
session/outputs, stream, play. Mã lỗi khớp c2a: 401 invalid_auth khi sai Bearer,
400 invalid_request khi thân rỗng/sai, thân playlists > 400_000 B bị chặn.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import playlists
from conftest import tieu_de_xac_thuc
from ho_tro_chung import VIDEO_ID_GIA, dat_giai_lai_stream, duong_dan_luong


@pytest.fixture()
def client(ung_dung):
    app, token, thu_muc = ung_dung
    with TestClient(app) as c:
        yield c, token, thu_muc, app


# ---------------------------------------------------------------- health

def test_health_ok(client):
    c, token, _, _ = client
    r = c.get("/api/integration/health", headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["status"] == "ok"
    assert body["api_version"] and body["app_version"]
    assert "search" in body["capabilities"] and "youtube_stream" in body["capabilities"]
    assert "youtube" in body["sources"]


def test_health_sai_bearer_401(client):
    c, token, _, _ = client
    assert c.get("/api/integration/health").status_code == 401
    r = c.get("/api/integration/health",
              headers={"Authorization": "Bearer sai-hoan-toan"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_auth"}


_CAC_ROUTE_CAN_BEARER = [
    ("GET", "/api/integration/health", None),
    ("GET", "/api/integration/search?q=x", None),
    ("GET", "/api/integration/status", None),
    ("GET", "/api/integration/history", None),
    ("POST", "/api/integration/stop", None),
    ("POST", "/api/integration/session", {"source": "youtube", "target": VIDEO_ID_GIA}),
    ("GET", "/api/integration/playlists", None),
    ("POST", "/api/integration/playlists", {"action": "list"}),
    ("POST", "/api/integration/session/outputs", {"session_id": "x", "output_entity_ids": []}),
    ("POST", "/api/integration/stream", {"source": "youtube", "target": VIDEO_ID_GIA}),
    ("POST", "/api/integration/play", {"target": VIDEO_ID_GIA}),
]


@pytest.mark.parametrize("method,duong,than", _CAC_ROUTE_CAN_BEARER)
def test_tat_ca_route_deu_401_khi_thieu_bearer(client, method, duong, than):
    c, _, _, _ = client
    r = c.request(method, duong, json=than)
    assert r.status_code == 401, duong
    assert r.json()["error"] == "invalid_auth"


# ---------------------------------------------------------------- search

def test_search_ok(client, monkeypatch):
    c, token, _, _ = client
    monkeypatch.setenv("YTDLP_GIA_KICH_BAN", "tim_kiem_ok")
    r = c.get("/api/integration/search", params={"q": "nhac gia", "limit": 2},
              headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["source"] == "youtube"
    assert body["total"] == 2
    assert body["items"][0]["id"] == VIDEO_ID_GIA
    assert body["items"][0]["title"] == "Bai tim kiem gia 1"


def test_search_tham_so_sai_400(client):
    c, token, _, _ = client
    h = tieu_de_xac_thuc(token)
    assert c.get("/api/integration/search", params={"q": ""}, headers=h).json()["error"] \
        == "invalid_search_query"
    assert c.get("/api/integration/search", params={"q": "x", "limit": 99}, headers=h).json()["error"] \
        == "invalid_search_query"
    r = c.get("/api/integration/search", params={"q": "x", "source": "nguon-la"}, headers=h)
    assert r.status_code == 400 and r.json()["error"] == "invalid_search_source"


def test_search_that_bai_502(client, monkeypatch):
    c, token, _, _ = client
    monkeypatch.setenv("YTDLP_GIA_KICH_BAN", "tim_kiem_loi")
    r = c.get("/api/integration/search", params={"q": "x"}, headers=tieu_de_xac_thuc(token))
    assert r.status_code == 502
    assert r.json()["error"] == "search_unavailable"


# ---------------------------------------------------------------- status / history

def test_status_idle_khi_moi(client):
    c, token, _, _ = client
    r = c.get("/api/integration/status", headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["state"] == "idle"
    assert body["sessions"] == []
    assert body["history_count"] == 0


def test_history_rong_khi_moi(client):
    c, token, _, _ = client
    r = c.get("/api/integration/history", headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    assert r.json() == {"success": True, "items": [], "total": 0}


# ---------------------------------------------------------------- session / stop / outputs

def test_session_post_ok(client):
    c, token, _, _ = client
    r = c.post("/api/integration/session",
               json={"source": "youtube", "target": VIDEO_ID_GIA,
                     "output_entity_ids": ["media_player.loa_gia"]},
               headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    phien = body["session"]
    assert phien["item"]["id"] == VIDEO_ID_GIA
    assert phien["output_entity_ids"] == ["media_player.loa_gia"]
    # /status phản ánh đúng phiên vừa tạo (đọc trạng thái thật, không cache)
    trang_thai = c.get("/api/integration/status", headers=tieu_de_xac_thuc(token)).json()
    assert trang_thai["state"] == phien["state"]


@pytest.mark.parametrize("than,ma_loi", [
    ({}, "invalid_request"),                       # thân rỗng
    ({"source": "youtube"}, "invalid_youtube_target"),  # thiếu target -> target rỗng
    ({"source": "youtube", "target": "khong-phai-id"}, "invalid_youtube_target"),
    ({"source": "nguon-la", "target": "x"}, "unsupported_session_source"),
])
def test_session_post_than_sai_400(client, than, ma_loi):
    c, token, _, _ = client
    r = c.post("/api/integration/session", json=than, headers=tieu_de_xac_thuc(token))
    assert r.status_code == 400
    assert r.json()["error"] == ma_loi


def test_stop_dung_phien(client):
    c, token, _, _ = client
    h = tieu_de_xac_thuc(token)
    tao = c.post("/api/integration/session",
                 json={"source": "youtube", "target": VIDEO_ID_GIA}, headers=h).json()
    sid = tao["session"]["session_id"]
    r = c.post("/api/integration/stop", json={"session_id": sid}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["stopped"] is True
    assert body["session"]["state"] == "idle"
    assert c.get("/api/integration/status", headers=h).json()["state"] == "idle"


def test_stop_than_sai_400(client):
    c, token, _, _ = client
    r = c.post("/api/integration/stop", json={}, headers=tieu_de_xac_thuc(token))
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_session_revision"


def test_session_outputs_ok_va_sai(client):
    c, token, _, _ = client
    h = tieu_de_xac_thuc(token)
    tao = c.post("/api/integration/session",
                 json={"source": "youtube", "target": VIDEO_ID_GIA}, headers=h).json()
    sid = tao["session"]["session_id"]
    r = c.post("/api/integration/session/outputs",
               json={"session_id": sid,
                     "output_entity_ids": ["media_player.loa_mot", "media_player.loa_hai"]},
               headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["stopped"] is False
    assert body["session"]["output_entity_ids"] == ["media_player.loa_mot", "media_player.loa_hai"]
    r = c.post("/api/integration/session/outputs",
               json={"session_id": sid, "output_entity_ids": ["den-sai"]}, headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------- playlists

def _bai_gia():
    return {"source": "youtube", "id": VIDEO_ID_GIA,
            "url": f"https://www.youtube.com/watch?v={VIDEO_ID_GIA}",
            "title": "Bai gia", "channel": "Kenh gia", "duration": 185, "thumbnail": ""}


def test_playlists_get_rong(client):
    c, token, _, _ = client
    r = c.get("/api/integration/playlists", headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    assert r.json() == {"success": True, "playlists": []}


def test_playlists_tao_them_xoa(client):
    c, token, _, _ = client
    h = tieu_de_xac_thuc(token)
    tao = c.post("/api/integration/playlists",
                 json={"action": "create", "name": "Nhac gia", "items": [_bai_gia()]},
                 headers=h)
    assert tao.status_code == 200
    pl = tao.json()["playlist"]
    assert pl["name"] == "Nhac gia" and len(pl["items"]) == 1
    them = c.post("/api/integration/playlists",
                  json={"action": "add", "id": pl["id"], "items": [_bai_gia()]},
                  headers=h).json()
    assert them["added"] == 0  # trùng bài -> không thêm
    danh_sach = c.get("/api/integration/playlists", headers=h).json()
    assert len(danh_sach["playlists"]) == 1
    xoa = c.post("/api/integration/playlists",
                 json={"action": "delete", "id": pl["id"]}, headers=h)
    assert xoa.status_code == 200
    assert c.get("/api/integration/playlists", headers=h).json()["playlists"] == []


def test_playlists_than_qua_400000_bi_chan(client):
    c, token, _, _ = client
    than = {"action": "list", "dem": "x" * 400_001}
    raw = json.dumps(than)
    assert len(raw.encode()) > 400_000
    r = c.post("/api/integration/playlists", content=raw,
               headers={**tieu_de_xac_thuc(token), "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_playlists_import_ma_chia_se_ttpl1(client):
    c, token, _, _ = client
    ma = playlists.share_code({
        "name": "Nhac chia se",
        "items": [{
            "source": "youtube", "id": VIDEO_ID_GIA,
            "url": f"https://www.youtube.com/watch?v={VIDEO_ID_GIA}",
            "title": "Bai chia se", "channel": "Kenh chia se",
            "duration": 200, "thumbnail": "",
        }],
    })
    assert ma.startswith("TTPL1.")
    r = c.post("/api/integration/playlists",
               json={"action": "import", "text": ma},
               headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    pl = r.json()["playlist"]
    assert pl["name"] == "Nhac chia se"
    assert len(pl["items"]) == 1
    assert pl["items"][0]["id"] == VIDEO_ID_GIA


# ---------------------------------------------------------------- stream / play

def test_stream_post_ok(client, monkeypatch, may_chu_gia):
    c, token, _, app = client
    may = may_chu_gia("binh_thuong", b"z" * 1000)
    dat_giai_lai_stream(monkeypatch, may)
    r = c.post("/api/integration/stream",
               json={"source": "youtube", "target": VIDEO_ID_GIA},
               headers=tieu_de_xac_thuc(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "/api/stream/" in body["stream_url"]
    assert body["expires_in"] == 3600
    assert body["media_content_type"] == "audio/mp4"


@pytest.mark.parametrize("than,ma_loi", [
    ({}, "invalid_request"),
    ({"source": "nguon-la", "target": "x"}, "unsupported_stream_source"),
    ({"source": "youtube", "target": "khong-phai-id"}, "invalid_youtube_target"),
])
def test_stream_post_sai_400(client, than, ma_loi):
    c, token, _, _ = client
    r = c.post("/api/integration/stream", json=than,
               headers=tieu_de_xac_thuc(token))
    assert r.status_code == 400
    assert r.json()["error"] == ma_loi


def test_play_ok_va_sai(client):
    c, token, _, _ = client
    h = tieu_de_xac_thuc(token)
    r = c.post("/api/integration/play", json={"target": VIDEO_ID_GIA}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["item"]["id"] == VIDEO_ID_GIA
    assert isinstance(body["session_revision"], int)
    r = c.post("/api/integration/play", json={"target": "khong-phai-id"}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_youtube_target"
    r = c.post("/api/integration/play", json={}, headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------- Range 200/206

def test_range_200_206_va_suffix_200(client, monkeypatch, may_chu_gia):
    c, token, _, app = client
    tong = 300_000
    may = may_chu_gia("binh_thuong", (bytes(range(256)) * (tong // 256 + 1))[:tong])
    dat_giai_lai_stream(monkeypatch, may)
    duong = duong_dan_luong(app.state.core)

    r = c.get(duong)
    assert r.status_code == 200, "không Range phải 200"
    assert len(r.content) == tong

    r = c.get(duong, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206, "có Range phải 206"
    assert r.headers["Content-Range"] == f"bytes 0-99/{tong}"
    assert len(r.content) == 100

    # bytes=-N (xin N byte CUỐI): giữ đúng c2a — 200 từ đầu
    r = c.get(duong, headers={"Range": "bytes=-500"})
    assert r.status_code == 200, "bytes=-N phải 200 từ đầu như c2a"
    assert len(r.content) == tong

    r = c.get(duong, headers={"Range": "bytes=zzz"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_range"