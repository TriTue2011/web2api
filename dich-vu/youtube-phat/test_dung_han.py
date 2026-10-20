"""Test «DỪNG LÀ DỪNG HẲN» (5 điều) — NV153 đợt A.

1. Restart dịch vụ -> phiên không sống lại, không tự phát.
2. dong_may_chu giữa luồng -> generator dừng, không thêm request thượng nguồn.
3. /status luôn phản ánh trạng thái thật, không trả trạng thái cũ.
4. Client abort giữa luồng -> không nối lại, không thêm request, không rò task.
5. Hết 5 lần nối lại -> luồng ngắt (ngắn), KHÔNG tự phát bài kế.
"""
from __future__ import annotations

import asyncio
import os
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

import dich_vu
import server
from conftest import tieu_de_xac_thuc
from ho_tro_chung import VIDEO_ID_GIA, dat_giai_lai_stream, duong_dan_luong

TONG = 10 * 1024 * 1024  # 3 khúc 4 MiB


@pytest.fixture(scope="module")
def du_lieu():
    return os.urandom(TONG)


def _ten_thread_nen():
    return sorted(
        t.name
        for t in threading.enumerate()
        if t is not threading.main_thread() and not t.daemon
    )


# ------------------------------------------------- (1) restart: phiên không sống lại

def test_restart_phien_khong_song_lai(ung_dung):
    app, token, thu_muc = ung_dung
    h = tieu_de_xac_thuc(token)
    with TestClient(app) as c:
        tao = c.post("/api/integration/session",
                     json={"source": "youtube", "target": VIDEO_ID_GIA}, headers=h)
        assert tao.json()["session"]["state"] == "playing"
        # restart: app mới trên CÙNG thư mục dữ liệu (phiên chỉ nằm RAM)
        dich_vu._dat_lai_cho_test()
        app2 = server.tao_ung_dung(thu_muc)
        token2 = app2.state.core.integration_token
    assert token2 == token  # cùng tệp token trên đĩa
    with TestClient(app2) as c2:
        st = c2.get("/api/integration/status",
                    headers=tieu_de_xac_thuc(token2)).json()
        assert st["state"] == "idle", "phiên sống lại sau restart!"
        assert st["sessions"] == []
        assert st["session"]["state"] == "idle"


# --------------------------------- (2) dong_may_chu: ngắt luồng đang chạy ngay

def test_dong_may_chu_ngat_luong(ung_dung, monkeypatch, may_chu_gia, du_lieu):
    app, token, _ = ung_dung
    # nguồn chậm: mỗi miếng 64 KiB sleep 10ms -> khúc 4 MiB mất ~0.6 s,
    # shutdown ở 0.5 s rơi vào GIỮA khúc 1 một cách ổn định.
    may = may_chu_gia("binh_thuong", du_lieu, truyen_cham_moi_mieng=0.01)
    dat_giai_lai_stream(monkeypatch, may)
    duong = duong_dan_luong(app.state.core)

    async def _main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                    base_url="http://test") as client:
            tac_vu = asyncio.create_task(client.get(duong))
            await asyncio.sleep(0.5)
            assert may.dem >= 1
            await server.dong_may_chu(app)
            dem_sau_dung = may.dem
            resp = await asyncio.wait_for(tac_vu, timeout=15)
            await asyncio.sleep(1.0)
            return resp, dem_sau_dung

    resp, dem_sau_dung = asyncio.run(_main())
    assert resp.status_code == 200
    assert 0 < len(resp.content) < TONG, "luồng không bị ngắt sau shutdown"
    assert may.dem == dem_sau_dung, "vẫn mở thêm khúc thượng nguồn sau khi dừng"
    assert len(app.state.luong_dang_chay) == 0


# -------------------------------------------- (3) /status phản ánh trạng thái thật

def test_status_luon_phai_anh_trang_thai_that(ung_dung):
    app, token, _ = ung_dung
    h = tieu_de_xac_thuc(token)
    with TestClient(app) as c:
        assert c.get("/api/integration/status", headers=h).json()["state"] == "idle"
        tao = c.post("/api/integration/session",
                     json={"source": "youtube", "target": VIDEO_ID_GIA}, headers=h)
        assert tao.json()["session"]["state"] == "playing"
        assert c.get("/api/integration/status", headers=h).json()["state"] == "playing"
        sid = tao.json()["session"]["session_id"]
        c.post("/api/integration/stop", json={"session_id": sid}, headers=h)
        # dừng xong /status phải idle ngay — không được trả trạng thái cũ
        assert c.get("/api/integration/status", headers=h).json()["state"] == "idle"


# -------------------------------------- (4) client abort: dừng ngay, không nối lại

def test_client_abort_khong_noi_lai_khong_ro(ung_dung, monkeypatch, may_chu_gia, du_lieu):
    app, token, _ = ung_dung
    may = may_chu_gia("cat_giua_khuc", du_lieu)
    dat_giai_lai_stream(monkeypatch, may)

    async def _mo_luong():
        """Dựng đúng generator mà route /api/stream trao cho StreamingResponse
        (giống hệt adapter trong proxy_stream)."""
        resolved = {"url": may.url, "headers": {"User-Agent": "test"},
                    "content_type": "audio/mp4"}
        up = await asyncio.to_thread(server._mo_khuc_don, resolved,
                                     0, server.KHUC_LUONG - 1)
        hop = {"resolved": resolved}

        async def _mo_khuc(tu, den):
            ban = await asyncio.to_thread(
                server._mo_khuc_don, hop["resolved"], tu, den)
            ban.ma_trang_thai = ban.status
            doc_goc = ban.read

            async def _doc(n):
                return await asyncio.to_thread(doc_goc, n)

            ban.read = _doc
            return ban

        def _giai_lai_link():
            return dict(hop["resolved"])

        return server._luong_cho_loa(app, up, _mo_khuc, _giai_lai_link, 0, TONG - 1)

    async def _main():
        # làm nóng executor/thread để baseline ổn định
        gen0 = await _mo_luong()
        async for _ in gen0:
            pass
        nen_truoc = _ten_thread_nen()

        gen = await _mo_luong()
        doc = 0
        async for mieng in gen:
            doc += len(mieng)
            if doc >= 200_000:
                break
        assert doc >= 200_000
        dem_luc_abort = may.dem
        await gen.aclose()  # client ngắt giữa chừng
        await asyncio.sleep(1.0)
        # chụp số thread khi loop còn sống (sau asyncio.run executor bị dọn)
        return dem_luc_abort, nen_truoc, _ten_thread_nen()

    dem_luc_abort, nen_truoc, nen_sau = asyncio.run(_main())
    assert may.dem == dem_luc_abort, "abort rồi mà vẫn mở thêm request thượng nguồn"
    assert len(app.state.luong_dang_chay) == 0, "rò generator trong sổ luồng đang chạy"
    assert nen_sau == nen_truoc, f"rò thread nền sau abort: {nen_sau} != {nen_truoc}"


# ------------------------------- (5) hết 5 lần: ngắt luồng, không phát bài kế

def test_het_5_lan_ngat_luong_khong_phat_bai_ke(
    ung_dung, monkeypatch, may_chu_gia, du_lieu, capsys
):
    app, token, _ = ung_dung
    may = may_chu_gia("qua_5_lan", du_lieu)
    dat_giai_lai_stream(monkeypatch, may)
    dem = {"giai": 0}
    giai_goc = dich_vu.resolve_youtube_audio

    def _dem_giai(*args, **kwargs):
        dem["giai"] += 1
        return giai_goc(*args, **kwargs)

    monkeypatch.setattr(dich_vu, "resolve_youtube_audio", _dem_giai)
    duong = duong_dan_luong(app.state.core)

    async def _main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                    base_url="http://test") as client:
            return await client.get(duong)

    resp = asyncio.run(_main())
    assert resp.status_code == 200
    assert len(resp.content) < TONG, "hết cách cứu mà luồng vẫn đủ — phải NGẮN"
    assert dem["giai"] == 1, f"tự giải thêm luồng {dem['giai'] - 1} lần sau khi hỏng"
    ra = capsys.readouterr().out
    assert "luong_that_bai" in ra
