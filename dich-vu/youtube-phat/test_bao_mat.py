"""Test bảo mật đợt A NV153: token luồng, không proxy mở, log sạch, tệp 0600."""
from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

import cau_hinh
import dich_vu
import server
import streaming
from conftest import tieu_de_xac_thuc
from ho_tro_chung import VIDEO_ID_GIA, duong_dan_luong


@pytest.fixture()
def client(ung_dung):
    app, token, thu_muc = ung_dung
    with TestClient(app) as c:
        yield c, token, thu_muc, app


def _duong_luong_voi_token(app, token: str) -> str:
    return f"/api/stream/{token}"


def _token_het_han(core) -> str:
    return streaming.create_stream_token(
        VIDEO_ID_GIA, core.integration_token, source="youtube",
        now=int(time.time()) - 7200, ttl=3600,
    )


def test_token_sai_chu_ky_403(client):
    c, token, _, app = client
    duong = duong_dan_luong(app.state.core)
    token_that = duong.rsplit("/", 1)[-1]
    gia = token_that[:-1] + ("A" if token_that[-1] != "A" else "B")
    r = c.get(_duong_luong_voi_token(app, gia))
    assert r.status_code == 403
    assert r.json()["error"] == "invalid_stream_token"


def test_token_het_han_403(client):
    c, _, _, app = client
    r = c.get(_duong_luong_voi_token(app, _token_het_han(app.state.core)))
    assert r.status_code == 403
    assert r.json()["error"] == "invalid_stream_token"


def test_token_ky_bang_khoa_sai_403(client):
    c, _, _, app = client
    gia = streaming.create_stream_token(
        VIDEO_ID_GIA, "khoa-sai-hoan-toan", source="youtube")
    r = c.get(_duong_luong_voi_token(app, gia))
    assert r.status_code == 403
    assert r.json()["error"] == "invalid_stream_token"


def test_token_nguon_la_403(client):
    c, _, _, app = client
    duong = duong_dan_luong(app.state.core)
    payload, chu_ky = duong.rsplit("/", 1)[-1].split(".", 1)
    import base64
    pad = "=" * (-len(payload) % 4)
    du_lieu = json.loads(base64.urlsafe_b64decode(payload + pad))
    du_lieu["source"] = "nguon-la"  # đổi nguồn nhưng giữ chữ ký cũ
    payload_gia = base64.urlsafe_b64encode(
        json.dumps(du_lieu, separators=(",", ":")).encode()).decode().rstrip("=")
    r = c.get(_duong_luong_voi_token(app, f"{payload_gia}.{chu_ky}"))
    assert r.status_code == 403


def test_token_dot_dot_403(client):
    c, _, _, app = client
    for xau in ("..", "..%2F..%2Fetc%2Fpasswd", "x" * 10 + "/../y"):
        r = c.get(f"/api/stream/{xau}")
        # ".." bị chuẩn hoá path thành 404 trước khi tới route; các dạng còn
        # lại tới route và bị 403 — cả hai đều nghĩa là KHÔNG đọc file.
        assert r.status_code in (403, 404), xau
        assert "root:" not in r.text


def test_token_dai_qua_4096_403(client):
    c, _, _, app = client
    r = c.get(_duong_luong_voi_token(app, "a" * 4097))
    assert r.status_code == 403
    assert r.json()["error"] == "invalid_stream_token"


def test_token_m3u8_sai_403(client):
    c, _, _, app = client
    r = c.get(_duong_luong_voi_token(app, "sai") + ".m3u8")
    assert r.status_code == 403


# ------------------------------------------------------- không phải proxy mở

def _giai_qua_ytdlp_gia(video_id, *, timeout=20, extractor=None):
    """resolve_youtube_audio thật, extractor gọi yt-dlp GIẢ qua tiến trình con."""
    import json as _json
    import subprocess as _sp

    def _extractor(watch_url, timeout):
        hoan = _sp.run(
            [sys.executable, "-m", "yt_dlp", "--dump-single-json",
             "--skip-download", "--no-warnings", watch_url],
            capture_output=True, text=True, timeout=timeout,
        )
        if hoan.returncode != 0:
            raise RuntimeError("yt-dlp gia that bai")
        return _json.loads(hoan.stdout)

    return streaming.resolve_youtube_audio(
        video_id, timeout=timeout, extractor=_extractor)


@pytest.mark.parametrize("kich_ban", ["url_doc_hai", "url_la"])
def test_url_yt_dlp_tra_ve_khong_hop_le_502_va_khong_fetch(
    client, monkeypatch, may_chu_gia, kich_ban
):
    """yt-dlp trả URL http thường / host lạ -> 502, KHÔNG fetch URL đó."""
    c, token, _, app = client
    may = may_chu_gia("binh_thuong", b"x" * 1000)
    monkeypatch.setenv("YTDLP_GIA_KICH_BAN", kich_ban)
    # KHÔNG nới YOUTUBE_STREAM_HOSTS: kiểm host thật phải chặn.
    monkeypatch.setattr(dich_vu, "resolve_youtube_audio", _giai_qua_ytdlp_gia)
    r = c.post("/api/integration/stream",
               json={"source": "youtube", "target": VIDEO_ID_GIA},
               headers=tieu_de_xac_thuc(token))
    assert r.status_code == 502
    assert r.json()["error"] == "stream_unavailable"
    assert may.dem == 0, "đã fetch thượng nguồn dù URL không hợp lệ"


def test_resolve_chan_truc_tiep_host_la():
    """Mức hàm: host không thuộc googlevideo.com -> lỗi, không chạm mạng."""
    import json as _json

    def _extractor(_url, _timeout):
        return {"id": VIDEO_ID_GIA,
                "formats": [{"url": "http://googlevideo.com.evil.example/x",
                             "acodec": "mp4a.40.2", "vcodec": "none", "ext": "m4a"}]}

    with pytest.raises(streaming.StreamUnavailableError):
        streaming.resolve_youtube_audio(VIDEO_ID_GIA, extractor=_extractor)


# ------------------------------------------------------------------ log sạch

def test_ghi_log_loc_khoa_nhay_cam(capsys):
    server._ghi_log({"su_kien": "kiem_tra", "token": "BI_MAT_1",
                     "stream_url": "http://x/?sig=BI_MAT_2",
                     "authorization": "Bearer BI_MAT_3",
                     "integration_token": "BI_MAT_4", "offset": 123})
    ra = capsys.readouterr().out
    assert "BI_MAT_1" not in ra and "BI_MAT_2" not in ra
    assert "BI_MAT_3" not in ra and "BI_MAT_4" not in ra
    assert '"offset": 123' in ra  # trường thường vẫn còn


def test_log_luong_khong_chua_token(client, monkeypatch, may_chu_gia, capsys):
    """Chạy relay có nối lại qua server._ghi_log: stdout không lộ token."""
    import asyncio

    import ro_le
    from ho_tro_chung import dat_giai_lai_stream

    c, token, _, app = client
    core = app.state.core
    may = may_chu_gia("200_thay_vi_206", b"y" * (10 * 1024 * 1024))
    dat_giai_lai_stream(monkeypatch, may)
    duong = duong_dan_luong(core)
    token_luong = duong.rsplit("/", 1)[-1]

    async def _go():
        from urllib.request import Request, urlopen

        def _mo(tu, den):
            req = Request(may.url, headers={"Range": f"bytes={tu}-{den}"})
            return urlopen(req, timeout=10)

        class _Ban:
            def __init__(self, r):
                self._r = r
                self.ma_trang_thai = getattr(r, "status", 200) or 200
                self.loai_loi_403_410 = False
                self.headers = dict(r.headers.items())

            def read(self, n):
                return self._r.read(n)

            def close(self):
                self._r.close()

        async def _mo_khuc(tu, den):
            return _Ban(await asyncio.to_thread(_mo, tu, den))

        async def _giai():
            return None

        def _doc_tong(h):
            cr = str(h.get("Content-Range") or "")
            return int(cr.rsplit("/", 1)[-1]) if "/" in cr else None

        def _ghi_log_ro_le(su_kien, **truong):
            # đúng wrapper mà server._luong_cho_loa dùng cho ro_le
            server._ghi_log({"su_kien": su_kien, **truong})

        gen = ro_le.luong_chiu_loi(
            _mo_khuc, 0, None, giai_lai_link=_giai,
            doc_tong=_doc_tong, ghi_log=_ghi_log_ro_le,
            ngu=lambda g: asyncio.sleep(0),
        )
        async for _ in gen:
            pass

    asyncio.run(_go())
    ra = capsys.readouterr().out
    assert token_luong not in ra, "token luồng lọt vào log!"
    assert token not in ra, "integration_token lọt vào log!"
    assert "luong_dut_noi_lai" in ra  # sự kiện vẫn được log


# ------------------------------------------------------------- tệp 0600

def test_tep_bi_mat_quyen_0600(client):
    _, _, thu_muc, _ = client
    goc = Path(thu_muc) / "youtube_phat"
    for ten in ("integration_token", "hmac_key"):
        duong = goc / ten
        assert duong.exists(), ten
        assert stat.S_IMODE(duong.stat().st_mode) == 0o600, ten


def test_token_sinh_ngau_nhien_bang_secrets(tmp_path):
    t1 = cau_hinh.giai_quyet_token_tich_hop(str(tmp_path / "a"))
    t2 = cau_hinh.giai_quyet_token_tich_hop(str(tmp_path / "b"))
    assert t1 != t2
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", t1), "token_urlsafe(32) -> 43 ký tự urlsafe"
    # đọc lại từ tệp phải ra đúng token đã sinh (không sinh lại mỗi lần)
    assert cau_hinh.giai_quyet_token_tich_hop(str(tmp_path / "a")) == t1


def test_khoa_hmac_32_byte_0600(tmp_path):
    ch = cau_hinh.doc_cau_hinh(str(tmp_path / "d"))
    assert len(ch.khoa_hmac) == 32
    duong = Path(str(tmp_path / "d")) / "youtube_phat" / "hmac_key"
    assert stat.S_IMODE(duong.stat().st_mode) == 0o600
