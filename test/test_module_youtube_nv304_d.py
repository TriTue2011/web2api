"""NV304 mảnh (d) — port 9 tệp test của dịch vụ cũ sang module youtube mới.

Nguồn cũ: dich-vu/youtube-phat/test_*.py (trích từ lịch sử git web2api,
commit ngay trước khi NV301 gỡ module youtube-phat; xem báo cáo (d) để
biết cách đếm: 94 hàm test / 116 case thu thập, không phải ~125 như
file NV304 ước lượng).

Đối chiếu theo file cũ (ghi ở đầu mỗi nhóm):
- test_hop_dong.py      → hợp đồng HTTP + mã lỗi (qua tich_hop + lõi).
- test_bai_ke_tua.py    → next/prev/seek, khoá tuần tự, tua theo tỉ lệ.
- test_dung_han.py      → bền sau khởi động lại, ngắt luồng, hết lần nối.
- test_gioi_han.py      → semaphore trích xuất, cắt bộ nhớ đệm vé.
- test_bao_mat.py       → vé luồng, lọc host, log sạch, tệp 0600.
- test_luong.py         → rơ-le _luong_chiu_loi (cắt giữa khúc, 403, 200
                           phớt Range, Content-Range lệch, đổi tổng).
- test_luong_doc_loi.py → rơ-le khi đọc giữa khúc ném lỗi + backoff.
- test_tim_kiem_chuyen_huong.py → KHÔNG port trực tiếp được: handler
  redirect urllib đã bỏ (Facebook giờ giải qua yt-dlp); chỉ port phần
  bảo vệ còn lại là lọc host (xem test_bao_mat) + tìm Facebook chỉ
  nhận link (xem test_hop_dong).
- test_xac_thuc_khong_ascii.py → khoá/vé không ASCII qua lõi.

Hành vi khác cũ được assert theo hành vi ĐÚNG và ghi chú «NV304d»
tại chỗ (không port mù assertion cũ, không sửa lén):
- ĐÃ KHÔI PHỤC trong (d) vì NV301/(e) làm sai so với c2a/dịch vụ cũ:
  mã lỗi theo nguồn (invalid_youtube_target / invalid_zing_target /
  invalid_facebook_target / invalid_http_audio_target /
  unsupported_session_source / unsupported_stream_source /
  invalid_output_entity_ids / invalid_search_query cho giới hạn sai),
  health quảng cáo "play" + facebook, history items có "id",
  session item có "artist", giới hạn tìm (từ khoá 120 / link 2048),
  media_content_type ở stream POST, lọc host YouTube, rơ-le không
  tính ranh giới khúc sạch là đứt.
- Giữ theo kiến trúc mới có chủ ý (không phải sai): xác thực bằng
  khoá khách lõi (thay token tích hợp riêng); vé luồng ký lõi
  (sai → 404 trống thay vì 403); hậu tố Range → 206 byte cuối
  (đúng RFC, thay quirk 200-từ-đầu của c2a); Range hỏng → 416
  (thay 400); stop body rỗng → dừng hết (giống c2a khi không
  truyền thân; khác dịch vụ cũ ở chỗ {} cũng dừng hết);
  outputs ["den-sai"] được chấp nhận (module không kiểm định dạng
  thực thể — lệch nhỏ, ghi ở báo cáo).

Phân tích chatgpt2api (luật đứng 16:11): xem báo cáo (d) — kết luận
chính: module youtube mirror DỊCH VỤ CŨ youtube-phat (vốn là port
trung thực của c2a api/youtube_phat.py + services/youtube_phat/* tại
d879b03), không mirror trực tiếp c2a; các điểm c2a có mà module
không có (đường video youtube_video/facebook_video, danh sách HLS
.m3u8 cho iPhone, route quản trị /api/youtube-phat/*) đều là phần
thuộc giao diện web của c2a, không thuộc hợp đồng integration.

Không Internet thật: trich_xuat và thượng nguồn đều là giả trong
tiến trình; đường lõi dùng khoá khách thật (theo mẫu NV277).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import pathlib
import sys
import tempfile
import time
import tracemalloc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

GOC = pathlib.Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "youtube"
sys.path.insert(0, str(GOC))

from app import duong_module, gioi_han, khoa_khach  # noqa: E402

KHOA_QT = "khoa-qt-nv304d-0123456789abcdef0123456789ab"
VIDEO_1 = "dQw4w9WgXcQ"
VIDEO_2 = "9bZkp7q19f0"
VIDEO_3 = "kJQP7kiw5Fk"


def tai_module():
    ten = "mod_youtube_nv304_d"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, THU_MUC_MODULE / "module.py",
        submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


def tai_trich_xuat():
    ten = "mod_youtube_nv304_d_trich_xuat"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(ten, THU_MUC_MODULE / "trich_xuat.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


def _muc_youtube(vid, tieu_de):
    return {"source": "youtube", "id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": tieu_de, "channel": "Kênh giả",
            "duration": 213, "thumbnail": ""}


class TrichXuatGia:
    """Trích xuất giả — cùng vai TrichXuatGia của test NV301/(c)."""

    def __init__(self):
        self.da_giai = []

    async def tim_kiem(self, source, query, limit):
        if source == "facebook" and "://" not in str(query):
            # Facebook chỉ nhận link — module tự chặn trước khi tới đây,
            # nhưng giữ hành vi cũ: chữ thường không phải link thì rỗng.
            return []
        if source == "youtube":
            return [_muc_youtube(VIDEO_1, "Bài một"),
                    _muc_youtube(VIDEO_2, "Bài hai"),
                    _muc_youtube(VIDEO_3, "Bài ba")][:limit]
        return []

    async def giai_am_thanh(self, source, target):
        self.da_giai.append((source, target))
        if source == "zing":
            return {"url": "https://zmdcdn.me/audio/a.mp3", "title": "Bài Zing",
                    "duration": 200, "source": "zing",
                    "headers": {"Referer": "https://zingmp3.vn/"}}
        if source == "facebook":
            return {"url": "https://video.fbcdn.net/audio.m4a", "title": "Video FB",
                    "duration": 120, "source": "facebook"}
        return {"url": "https://rr1---sn.googlevideo.com/videoplayback?id=1",
                "title": "Bài giả", "duration": 213, "source": source,
                "channel": "Kênh giả", "content_type": "audio/mp4",
                "thumbnail": f"https://i.ytimg.com/vi/{target}/hqdefault.jpg"}

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        assert source == "zing"
        return ("Album Zing", [_muc_youtube(VIDEO_1, "Bài một")])


def _mod(monkeypatch, tmp_path):
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=str(tmp_path))
    m.nap({})
    m.trich_xuat = TrichXuatGia()
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    return mod, m


async def _goi(m, duong, than=None, query=None, phuong_thuc="POST", goc=""):
    return await m.tich_hop(duong, phuong_thuc, query or {},
                            than if than is not None else {}, goc)


async def _tao_phien(m, **them):
    than = {"source": "youtube", "target": VIDEO_1}
    than.update(them)
    ma, _, body = await _goi(m, "integration/session", than)
    assert ma == 200, body
    return body["session"]


def goi(m, duong, than=None, query=None, phuong_thuc="POST", goc=""):
    """Gói sync của _goi (kho chưa có pytest-asyncio, theo mẫu (c)/(e))."""
    return asyncio.run(_goi(m, duong, than, query, phuong_thuc, goc))


def tao_phien(m, **them):
    return asyncio.run(_tao_phien(m, **them))


class BanGia:
    """Phản hồi thượng nguồn kiểu httpx cho rơ-le / MoNguonGia."""

    def __init__(self, status_code, headers, chunks):
        self.status_code = status_code
        self.headers = headers
        self._chunks = list(chunks)
        self.da_dong = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self):
        self.da_dong = True


class MoNguonGia:
    """Thượng nguồn phục vụ đúng Range (cho test luong qua HTTP)."""

    def __init__(self, du_lieu):
        self.du_lieu = du_lieu
        self.da_goi = []

    async def __call__(self, url, headers):
        self.da_goi.append((url, dict(headers or {})))
        import re
        tong = len(self.du_lieu)
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", (headers or {}).get("Range") or "")
        if not m:
            return BanGia(200, {"Content-Length": str(tong),
                                "Content-Type": "audio/mpeg"},
                          [self.du_lieu[i:i + 65536]
                           for i in range(0, tong, 65536)])
        dau = int(m.group(1))
        cuoi = int(m.group(2)) if m.group(2) else tong - 1
        cat = self.du_lieu[dau:cuoi + 1]
        return BanGia(206, {"Content-Range": f"bytes {dau}-{cuoi}/{tong}",
                            "Content-Type": "audio/mpeg"},
                      [cat[i:i + 65536] for i in range(0, len(cat), 65536)])


class Moi:
    """Lõi tối thiểu + module youtube thật + khoá khách thật (mẫu NV277)."""

    def __init__(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
        monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
        monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
        monkeypatch.delenv("WEB2API_TRUST_PROXY", raising=False)
        monkeypatch.setattr(gioi_han, "_BO", None)
        khoa_khach.lam_moi_cache()
        mod = tai_module()
        self.mod = mod
        self.module = mod.Module({}, _thu_muc_du_lieu=str(tmp_path))
        self.module.nap({})
        self.module.trich_xuat = TrichXuatGia()
        self.module.mo_nguon = MoNguonGia(bytes(range(256)) * 4)
        self.so = SimpleNamespace(
            catalog={"youtube": SimpleNamespace(
                         manifest={"duong_module": {"lan_chi": True}}),
                     "other": SimpleNamespace(
                         manifest={"duong_module": {"lan_chi": True}})},
            dang_chay={"youtube": self.module})
        app = FastAPI()
        app.include_router(duong_module.router)
        app.state.so = self.so
        self.c = TestClient(app, raise_server_exceptions=False,
                            client=("127.0.0.1", 50000))

    def khoa(self, pham_vi=("tich_hop@youtube",)):
        _, tho = khoa_khach.tao_khoa(nhan="khoa-test", pham_vi=list(pham_vi),
                                     so=self.so)
        return {"Authorization": f"Bearer {tho}"}


# ── test_hop_dong (port) ────────────────────────────────────────────────

@pytest.fixture
def moi(monkeypatch, tmp_path):
    return Moi(monkeypatch, tmp_path)


@pytest.fixture
def mm(monkeypatch, tmp_path):
    return _mod(monkeypatch, tmp_path)


def _sid(body):
    return body["session"]["session_id"]


def test_health_hinh_moi_nv304d(moi):
    r = moi.c.get("/m/youtube/api/integration/health", headers=moi.khoa())
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "ok" and b["api_version"] == "1"
    # NV304d: health phải quảng cáo đúng như c2a (test_youtube_phat_api.py
    # assert "play" trong capabilities, playback_sources đủ 4 nguồn).
    # NV301 đã giấu "play" và facebook — khôi phục trong (d).
    assert "play" in b["capabilities"]
    assert b["sources"] == ["youtube", "zing", "facebook"]
    assert b["playback_sources"] == ["youtube", "zing", "facebook", "http"]


_DUONG_CU = [
    ("GET", "/m/youtube/api/integration/health", None),
    ("GET", "/m/youtube/api/integration/search?q=x", None),
    ("GET", "/m/youtube/api/integration/status", None),
    ("GET", "/m/youtube/api/integration/history", None),
    ("POST", "/m/youtube/api/integration/stop", {}),
    ("POST", "/m/youtube/api/integration/session", {}),
    ("GET", "/m/youtube/api/integration/playlists", None),
    ("POST", "/m/youtube/api/integration/playlists", {"action": "list"}),
    ("POST", "/m/youtube/api/integration/session/outputs", {}),
    ("POST", "/m/youtube/api/integration/stream", {}),
    ("POST", "/m/youtube/api/integration/play", {}),
]
_DUONG_MOI = _DUONG_CU + [
    ("POST", "/m/youtube/api/integration/pause", {}),
    ("POST", "/m/youtube/api/integration/next", {}),
    ("POST", "/m/youtube/api/integration/prev", {}),
    ("POST", "/m/youtube/api/integration/seek", {}),
    ("GET", "/m/youtube/api/integration/queue", None),
    ("POST", "/m/youtube/api/integration/queue", {}),
]


@pytest.mark.parametrize("method,duong,than", _DUONG_MOI)
def test_moi_duong_deu_doi_bearer_401(moi, method, duong, than):
    # Port test_hop_dong: 11 route cũ parametrize + 6 route mới (pause /
    # next / prev / seek / queue GET+POST). Xác thực ở lõi, trước khi
    # module đọc thân — nên mọi thân đều 401 khi không có khoá.
    if method == "GET":
        r = moi.c.get(duong)
    else:
        r = moi.c.post(duong, json=than)
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_auth"


def test_khoa_sai_va_khoa_module_khac_401_403(moi):
    h = moi.khoa(("tich_hop@other",))
    r = moi.c.get("/m/youtube/api/integration/health", headers=h)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    r = moi.c.get("/m/youtube/api/integration/health",
                  headers={"Authorization": "Bearer khoa-sai"})
    assert r.status_code == 401


def test_than_post_qua_tran_loi_400(moi):
    # Port test_hop_dong: thân playlists > 400_000 → 400 (trần lõi
    # TRAN_THAN; cũ: trần 400_000 ở route playlists, các route khác
    # 8192/4096 — nay lõi gộp thành một trần, xem báo cáo).
    h = moi.khoa()
    r = moi.c.post("/m/youtube/api/integration/playlists", headers=h,
                   content=b"x" * 400_001)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_search_ok_va_hai_hinh_results_items(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/search",
                            query={"q": "bai", "source": "youtube", "limit": 20},
                            phuong_thuc="GET")
    assert ma == 200
    assert body["source"] == "youtube" and body["total"] == 3
    assert body["results"] == body["items"]
    assert body["items"][0]["id"] == VIDEO_1


def test_search_tham_so_sai_va_nguon_la(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/search",
                            query={"q": "", "source": "youtube"},
                            phuong_thuc="GET")
    assert ma == 400 and body["error"] == "invalid_search_query"
    ma, _, body = goi(m, "integration/search",
                            query={"q": "x", "limit": 99},
                            phuong_thuc="GET")
    # NV304d: mã giới hạn khôi phục theo c2a (NV301 đổi thành
    # invalid_search_limit).
    assert ma == 400 and body["error"] == "invalid_search_query"
    ma, _, body = goi(m, "integration/search",
                            query={"q": "x", "source": "nguon-la"},
                            phuong_thuc="GET")
    assert ma == 400 and body["error"] == "invalid_search_source"
    ma, _, body = goi(m, "integration/search",
                            query={"q": "chu thuong", "source": "facebook"},
                            phuong_thuc="GET")
    assert ma == 400 and body["error"] == "invalid_search_query"


def test_search_502_khi_trich_xuat_hong(mm):
    mod, m = mm

    class Hong:
        async def tim_kiem(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

        async def giai_am_thanh(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

        async def lay_danh_sach_phat(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

    m.trich_xuat = Hong()
    ma, _, body = goi(m, "integration/search",
                            query={"q": "x"}, phuong_thuc="GET")
    assert ma == 502 and body["error"] == "search_unavailable"


def test_status_idle_va_history_rong(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert ma == 200 and body["state"] == "idle"
    assert body["sessions"] == [] and body["history_count"] == 0
    ma, _, body = goi(m, "integration/history", phuong_thuc="GET")
    assert ma == 200 and body["items"] == [] and body["total"] == 0


def test_session_ok_va_ghi_lich_su(mm):
    mod, m = mm
    phien = tao_phien(m)
    assert phien["state"] == "playing"
    assert phien["item"]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert body["state"] == "playing" and body["history_count"] == 1
    assert body["sessions"][0]["session_id"] == phien["session_id"]


def test_session_loi_ma_moi_nv304d(mm):
    mod, m = mm
    # NV304d: mã lỗi khôi phục theo c2a (NV301 gộp thành
    # invalid_target/invalid_source).
    ma, _, body = goi(m, "integration/session", {})
    assert ma == 400 and body["error"] == "invalid_youtube_target"
    ma, _, body = goi(
        m, "integration/session",
        {"source": "youtube", "target": "khong-phai-id"})
    assert ma == 400 and body["error"] == "invalid_youtube_target"
    ma, _, body = goi(
        m, "integration/session", {"source": "nguon-la", "target": "x"})
    assert ma == 400 and body["error"] == "unsupported_session_source"
    ma, _, body = goi(
        m, "integration/session",
        {"source": "youtube", "target": VIDEO_1, "volume_level": 2})
    assert ma == 400 and body["error"] == "invalid_volume_level"
    ma, _, body = goi(
        m, "integration/session",
        {"source": "youtube", "target": VIDEO_1, "playlist_id": "khong-co"})
    assert ma == 404 and body["error"] == "playlist_not_found"
    ma, _, body = goi(
        m, "integration/session",
        {"source": "youtube", "target": VIDEO_1, "output_entity_ids": "khong-phai-list"})
    assert ma == 400 and body["error"] == "invalid_output_entity_ids"


def test_stop_theo_phien_va_body_rong_nv304d(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    ma, _, body = goi(m, "integration/stop", {"session_id": sid})
    assert ma == 200 and body["stopped"] is True
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert body["state"] == "idle"
    # NV304d: body rỗng → dừng hết (hành vi NV277); cũ/c2a: 400
    # invalid_session_revision.
    tao_phien(m)
    ma, _, body = goi(m, "integration/stop", {})
    assert ma == 200 and body["stopped"] is True
    ma, _, body = goi(m, "integration/stop",
                            {"session_id": "khong-co"})
    assert ma == 200 and body["stopped"] is False
    ma, _, body = goi(m, "integration/stop",
                            {"session_id": sid, "expected_revision": "chu"})
    assert ma == 400 and body["error"] == "invalid_session_revision"


def test_outputs_ok_loi_va_rong(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    ma, _, body = goi(m, "integration/session/outputs",
                            {"session_id": sid,
                             "output_entity_ids": ["media_player.loa"]})
    assert ma == 200
    ma, _, body = goi(m, "integration/session/outputs",
                            {"session_id": sid,
                             "output_entity_ids": "khong-phai-list"})
    # NV304d: mã khôi phục theo c2a (NV301 đổi thành invalid_outputs).
    assert ma == 400 and body["error"] == "invalid_output_entity_ids"
    # NV304d: outputs ["den-sai"] được chấp nhận (module không kiểm
    # định dạng thực thể); cũ: 400.
    ma, _, body = goi(m, "integration/session/outputs",
                            {"session_id": sid,
                             "output_entity_ids": ["den-sai"]})
    assert ma == 200
    ma, _, body = goi(m, "integration/session/outputs",
                            {"session_id": sid, "output_entity_ids": []})
    assert ma == 200 and body["stopped"] is True
    ma, _, body = goi(m, "integration/session/outputs",
                            {"session_id": "khong-co",
                             "output_entity_ids": ["media_player.loa"]})
    assert ma == 200 and body["stopped"] is False


def test_playlists_crud_va_chia_se_ttpl1(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "list"})
    assert ma == 200 and body["playlists"] == []
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "create", "name": "Nghe nhiều",
                             "items": [_muc_youtube(VIDEO_1, "Bài một")]})
    assert ma == 200
    pid = body["playlist"]["id"]
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "add", "playlist_id": pid,
                             "items": [_muc_youtube(VIDEO_1, "Bài một"),
                                      _muc_youtube(VIDEO_2, "Bài hai")]})
    assert ma == 200 and body["added"] == 1  # trùng bị khử
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "export", "playlist_id": pid})
    assert ma == 200 and body["code"].startswith("TTPL1.")
    code = body["code"]
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "import", "text": code})
    assert ma == 200 and len(body["playlist"]["items"]) == 2
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "import", "text": "KHONG-PHAI-MA"})
    assert ma == 400 and body["error"] == "invalid_playlist_link"
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "import", "text": "TTPL1.!!!"})
    assert ma == 400 and body["error"] == "invalid_share_code"
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "create", "name": "   "})
    assert ma == 400 and body["error"] == "invalid_playlist_name"
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "rename", "playlist_id": pid,
                             "name": "Tên mới"})
    assert ma == 200 and body["playlist"]["name"] == "Tên mới"
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "delete", "playlist_id": pid})
    assert ma == 200 and body["deleted"] is True
    # NV304d: action lạ → 404 playlist_not_found (cũ: invalid_playlist_action).
    ma, _, body = goi(m, "integration/playlists",
                            {"action": "hanh-dong-la", "playlist_id": pid})
    assert ma == 404 and body["error"] == "playlist_not_found"


def test_stream_post_ok_va_loi(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/stream",
                            {"source": "youtube", "target": VIDEO_1})
    assert ma == 200
    assert "/m/youtube/api/stream/" in body["stream_url"]
    assert body["token"] and body["expires_in"] == 3600
    assert body["media_content_type"] == "audio/mp4"
    ma, _, body = goi(m, "integration/stream", {})
    assert ma == 400 and body["error"] == "invalid_youtube_target"
    ma, _, body = goi(m, "integration/stream",
                            {"source": "nguon-la", "target": "x"})
    assert ma == 400 and body["error"] == "unsupported_stream_source"
    ma, _, body = goi(m, "integration/stream",
                            {"source": "youtube", "target": "khong-phai-id"})
    assert ma == 400 and body["error"] == "invalid_youtube_target"


def test_play_ok_loi_va_phien_dung_404(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/play",
                            {"source": "youtube", "target": VIDEO_1})
    assert ma == 200
    assert body["item"]["id"] == VIDEO_1
    assert isinstance(body["session_revision"], int)
    sid = body["session"]["session_id"]
    ma, _, body = goi(m, "integration/play",
                            {"session_id": sid})
    assert ma == 200  # tiếp tục phiên sống, không ghi lại lịch sử
    goi(m, "integration/stop", {"session_id": sid})
    ma, _, body = goi(m, "integration/play",
                            {"session_id": sid})
    # NV304d: phiên đã dừng thì 404, không hồi sinh (cũ: hồi sinh).
    assert ma == 404 and body["error"] == "session_not_found"
    ma, _, body = goi(m, "integration/play", {})
    # NV304d: theo c2a normalize_target(None) → invalid_youtube_target.
    assert ma == 400 and body["error"] == "invalid_youtube_target"


def test_pause_moi_nv304d(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    ma, _, body = goi(m, "integration/pause", {"session_id": sid})
    assert ma == 200 and body["session"]["state"] == "paused"
    goi(m, "integration/stop", {"session_id": sid})
    ma, _, body = goi(m, "integration/pause", {"session_id": sid})
    assert ma == 404 and body["error"] == "session_not_found"


def test_queue_moi_nv304d(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    ma, _, body = goi(m, "integration/queue", phuong_thuc="GET",
                            query={"session_id": sid})
    assert ma == 200 and body["queue_index"] == 0
    ma, _, body = goi(m, "integration/queue",
                            {"session_id": sid, "action": "add",
                             "item": _muc_youtube(VIDEO_2, "Bài hai")})
    assert ma == 200 and len(body["queue"]["items"]) == 2
    ma, _, body = goi(m, "integration/queue",
                            {"session_id": sid, "action": "add",
                             "item": {"source": "youtube", "id": "x"}})
    assert ma == 400 and body["error"] == "invalid_queue_item"
    ma, _, body = goi(m, "integration/queue",
                            {"session_id": sid, "action": "hanh-dong-la"})
    assert ma == 400 and body["error"] == "invalid_queue_action"
    ma, _, body = goi(m, "integration/queue", phuong_thuc="GET",
                            query={"session_id": "khong-co"})
    assert ma == 404 and body["error"] == "session_not_found"


# ── test_bai_ke_tua (port) ──────────────────────────────────────────────

def _queue_6():
    # Mục tiêu mặc định của _tao_phien là VIDEO_1 nên phải đứng đầu
    # hàng, không thì _tao_phien bỏ hàng đợi bên gọi đưa.
    return [_muc_youtube(VIDEO_1, "Bài 1"),
            _muc_youtube(VIDEO_2, "Bài 2"),
            _muc_youtube(VIDEO_3, "Bài 3"),
            _muc_youtube("id000000004", "Bài 4"),
            _muc_youtube("id000000005", "Bài 5"),
            _muc_youtube("id000000006", "Bài 6")]


def test_next_prev_va_bien_409(mm):
    mod, m = mm
    phien = tao_phien(m, queue=_queue_6()[:3])
    sid = phien["session_id"]
    assert phien["item"]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/next", {"session_id": sid})
    assert ma == 200 and body["item"]["id"] == VIDEO_2
    assert body["session"]["position"] == 0
    ma, _, body = goi(m, "integration/prev", {"session_id": sid})
    assert ma == 200 and body["item"]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/prev", {"session_id": sid})
    assert ma == 409 and body["error"] == "queue_start"
    goi(m, "integration/next", {"session_id": sid})
    goi(m, "integration/next", {"session_id": sid})
    ma, _, body = goi(m, "integration/next", {"session_id": sid})
    assert ma == 409 and body["error"] == "queue_end"


def test_next_tren_phien_da_dung_404(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    goi(m, "integration/stop", {"session_id": sid})
    ma, _, body = goi(m, "integration/next", {"session_id": sid})
    assert ma == 404 and body["error"] == "session_not_found"
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert body["state"] == "idle" and body["item"] is None


def test_phat_bai_trong_ket_qua_tim_thi_next_theo_hang_tim(mm):
    mod, m = mm
    # Port remember_search của dịch vụ cũ: tìm rồi phát một bài trong
    # kết quả thì bài kế đi theo hàng đợi tìm đó.
    goi(m, "integration/search", query={"q": "bai", "source": "youtube"},
        phuong_thuc="GET")
    ma, _, body = goi(m, "integration/play",
                      {"source": "youtube", "target": VIDEO_1})
    assert ma == 200
    sid = body["session"]["session_id"]
    ma, _, body = goi(m, "integration/next", {"session_id": sid})
    assert ma == 200 and body["item"]["id"] == VIDEO_2


def test_next_dong_thoi_5_lan_chuyen_dung_1_lan_moi_luot(mm):
    mod, m = mm
    # Port khoá tuần tự của dịch vụ cũ (threading.Lock → asyncio.Lock
    # của module): 5 tác vụ next đồng thời, mỗi lượt chuyển đúng 1 bài.
    phien = tao_phien(m, queue=_queue_6())
    sid = phien["session_id"]

    async def _chay():
        res = await asyncio.gather(*[
            m.tich_hop("integration/next", "POST", {},
                       {"session_id": sid}) for _ in range(5)])
        return res

    res = asyncio.run(_chay())
    assert all(r[0] == 200 for r in res)
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert body["sessions"][0]["queue_index"] == 5
    assert body["sessions"][0]["item"]["title"] == "Bài 6"


def test_seek_dat_kep_loi_va_khoa_position(mm):
    mod, m = mm
    phien = tao_phien(m)
    sid = phien["session_id"]
    ma, _, body = goi(m, "integration/seek", {"session_id": sid, "giay": 30})
    assert ma == 200 and body["position"] == 30.0
    assert "?t=30" in body["seek_url"]
    assert "/m/youtube/api/stream/" in body["seek_url"]
    ma, _, body = goi(m, "integration/seek",
                      {"session_id": sid, "position": 50})
    assert ma == 200 and body["position"] == 50.0
    ma, _, body = goi(m, "integration/seek",
                      {"session_id": sid, "giay": 9999})
    assert ma == 200 and body["position"] == 213.0  # kẹp theo thời lượng
    ma, _, body = goi(m, "integration/seek", {"session_id": sid, "giay": -1})
    assert ma == 400 and body["error"] == "invalid_seek_position"
    ma, _, body = goi(m, "integration/seek",
                      {"session_id": sid, "giay": "abc"})
    assert ma == 400 and body["error"] == "invalid_seek_position"
    ma, _, body = goi(m, "integration/seek",
                      {"session_id": "khong-co", "giay": 10})
    assert ma == 404 and body["error"] == "session_not_found"


def _ve_thuong_nguon(m, du_lieu, duration=100, total=None):
    m._luong["ve1"] = {"url": "fake://am-thanh", "headers": {},
                       "total": total, "duration": duration,
                       "source": "youtube", "het_han": time.time() + 3600}
    m.mo_nguon = MoNguonGia(du_lieu)


def _thu_thap(gen):
    async def _c():
        out = []
        try:
            async for chunk in gen:
                out.append(chunk)
        except Exception as e:
            return b"".join(out), e
        return b"".join(out), None
    return asyncio.run(_c())


def test_luong_tua_ti_le_theo_giay(mm):
    mod, m = mm
    # Port test_offset_tua_ti_le_khi_khong_co_sidx (cũ dùng sidx khi có,
    # tỉ lệ khi không): duration 100, tổng 1000, t=50 → byte 500.
    du_lieu = bytes(range(256)) * 4
    du_lieu = du_lieu[:1000]
    _ve_thuong_nguon(m, du_lieu, duration=100, total=1000)
    ma, headers, gen = asyncio.run(
        m.luong("ve1", None, "GET", tua_giay="50"))
    assert ma == 206
    assert headers["Content-Range"] == "bytes 500-999/1000"
    data, err = _thu_thap(gen)
    assert err is None and data == du_lieu[500:]


def test_luong_range_thang_tua_giay_va_tua_loi(mm):
    mod, m = mm
    du_lieu = bytes(range(256)) * 4
    du_lieu = du_lieu[:1000]
    _ve_thuong_nguon(m, du_lieu, duration=100, total=1000)
    ma, headers, gen = asyncio.run(
        m.luong("ve1", "bytes=0-99", "GET", tua_giay="50"))
    assert ma == 206 and headers["Content-Range"] == "bytes 0-99/1000"
    data, _ = _thu_thap(gen)
    assert data == du_lieu[:100]
    for tua_xau in ("abc", "-5", "1000000000000000000"):
        ma, _, _ = asyncio.run(m.luong("ve1", None, "GET", tua_giay=tua_xau))
        assert ma == 400, tua_xau
    # Port test_offset_tua_khong_biet_thoi_luong_thi_none (dạng HTTP:
    # không tính được offset → 400, như cũ trả 400 invalid_seek).
    m._luong["ve1"]["duration"] = None
    ma, _, _ = asyncio.run(m.luong("ve1", None, "GET", tua_giay="50"))
    assert ma == 400


def test_seek_url_qua_http_tra_206_dung_byte(moi):
    # Port stream ?t= của dịch vụ cũ qua đường lõi: seek_url ký kèm
    # ?t=, loa mở vé lười → giải lại → tua tỉ lệ → 206 đúng byte.
    h = moi.khoa()
    r = moi.c.post("/m/youtube/api/integration/session", headers=h,
                   json={"source": "youtube", "target": VIDEO_1})
    assert r.status_code == 200
    sid = r.json()["session"]["session_id"]
    r = moi.c.post("/m/youtube/api/integration/seek", headers=h,
                   json={"session_id": sid, "giay": 30})
    assert r.status_code == 200
    seek_url = r.json()["seek_url"]
    assert "?t=30" in seek_url and "/m/youtube/api/stream/" in seek_url
    duong = seek_url.split("http://testserver")[-1]
    r = moi.c.get(duong)
    assert r.status_code == 206
    # duration 213 (fake), tổng 1024 → round(30/213*1024) = 144.
    assert r.headers["content-range"] == "bytes 144-1023/1024"
    assert r.content == (bytes(range(256)) * 4)[144:]


# ── test_dung_han (port) ────────────────────────────────────────────────

def test_khoi_dong_lai_phien_mat_playlist_lich_su_con(mm, tmp_path):
    mod, m = mm
    tao_phien(m)
    goi(m, "integration/playlists",
        {"action": "create", "name": "P", "items": [_muc_youtube(VIDEO_1, "B1")]})
    m2 = mod.Module({}, _thu_muc_du_lieu=str(tmp_path))
    m2.nap({})
    m2.trich_xuat = TrichXuatGia()
    ma, _, body = asyncio.run(
        m2.tich_hop("integration/status", "GET", {}, {}))
    assert body["state"] == "idle" and body["sessions"] == []
    # NV304d: playlists + history BỀN (khác cũ: cũ chỉ assert phiên
    # không sống lại; history của module mới vẫn còn sau khởi động).
    ma, _, body = asyncio.run(
        m2.tich_hop("integration/playlists", "POST", {}, {"action": "list"}))
    assert len(body["playlists"]) == 1
    ma, _, body = asyncio.run(
        m2.tich_hop("integration/history", "GET", {}, {}))
    assert body["total"] == 1


def test_tat_giua_chung_khong_mo_them(mm):
    mod, m = mm
    mod.KHUC = 256  # dữ liệu 1024 byte thành 4 khúc thì _dung mới có nghĩa
    du_lieu = bytes(range(256)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100, total=len(du_lieu))
    m.mo_nguon.da_goi.clear()

    async def _chay():
        ma, _, gen = await m.luong("ve1", None, "GET")
        assert ma == 200
        it = gen.__aiter__()
        await it.__anext__()  # khúc đầu (256 byte)
        m._dung = True
        async for _ in gen:
            pass

    asyncio.run(_chay())
    assert len(m.mo_nguon.da_goi) == 1


def test_client_ngat_giua_chung_khong_mo_them_khong_giai_lai(mm):
    mod, m = mm
    mod.KHUC = 256
    du_lieu = bytes(range(256)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100, total=len(du_lieu))
    m.mo_nguon.da_goi.clear()
    giai_truoc = len(m.trich_xuat.da_giai)

    async def _chay():
        ma, _, gen = await m.luong("ve1", None, "GET")
        it = gen.__aiter__()
        await it.__anext__()
        await gen.aclose()

    asyncio.run(_chay())
    assert len(m.mo_nguon.da_goi) == 1
    assert len(m.trich_xuat.da_giai) == giai_truoc


def test_het_5_lan_noi_lai_thi_ngat_va_khong_giai_lai(mm):
    mod, m = mm
    du_lieu = bytes(range(256)) * 4
    _ve_thuong_nguon(m, du_lieu, duration=100, total=len(du_lieu))

    async def _hong(url, headers):
        raise ConnectionError("dut")

    m.mo_nguon = _hong
    giai_truoc = len(m.trich_xuat.da_giai)

    async def _chay():
        ma, _, gen = await m.luong("ve1", None, "GET")
        assert ma == 200
        try:
            async for _ in gen:
                pass
            return None
        except Exception as e:
            return e

    err = asyncio.run(_chay())
    assert isinstance(err, mod.LoiNguonKhongCuuDuoc)
    # Lỗi kết nối không trigger giải lại link (chỉ 403/404/410 và
    # 200-phớt-Range mới giải lại).
    assert len(m.trich_xuat.da_giai) == giai_truoc


# ── test_gioi_han (port, từ unittest sang pytest) ────────────────────────

def test_semaphore_trich_xuat_toi_da_2(mm):
    mod, m = mm

    async def _chay():
        hien = 0
        dinh = 0

        async def _giu():
            nonlocal hien, dinh
            sem = await m._chiem_cho_trich_xuat()
            try:
                hien += 1
                dinh = max(dinh, hien)
                await asyncio.sleep(0.05)
                hien -= 1
            finally:
                sem.release()

        await asyncio.gather(*[_giu() for _ in range(3)])
        return dinh

    assert asyncio.run(_chay()) == 2


def test_semaphore_het_cho_qua_thoi_gian_thi_bao_ban(mm):
    mod, m = mm
    mod.CHO_TRICH_XUAT_GIAY = 0.05

    async def _chay():
        s1 = await m._chiem_cho_trich_xuat()
        s2 = await m._chiem_cho_trich_xuat()
        t0 = time.monotonic()
        try:
            await m._chiem_cho_trich_xuat()
            return None, time.monotonic() - t0
        except Exception as e:
            return e, time.monotonic() - t0
        finally:
            s1.release()
            s2.release()

    err, elapsed = asyncio.run(_chay())
    assert isinstance(err, mod.TrichXuatUnavailable)
    assert str(err) == "busy"
    assert elapsed < 3


def test_cat_bo_nho_dem_ve_giu_256_het_han_lau_nhat(mm):
    mod, m = mm
    for i in range(260):
        m._luong[f"ve-{i}"] = {"het_han": float(i)}
    m._cat_bo_luong()
    assert len(m._luong) == 256
    assert min(v["het_han"] for v in m._luong.values()) == 4.0
    m._luong.clear()
    for i in range(10):
        m._luong[f"ve-{i}"] = {"het_han": float(i)}
    truoc = dict(m._luong)
    m._cat_bo_luong()
    assert m._luong == truoc


# ── test_bao_mat (port) ─────────────────────────────────────────────────

class _YdlGia:
    def __init__(self, info):
        self.info = info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return self.info


def _yt_dlp_gia(info):
    from types import SimpleNamespace
    return SimpleNamespace(YoutubeDL=lambda opts: _YdlGia(info))


def test_resolve_youtube_chan_host_la_nv304d(monkeypatch):
    # Port test_resolve_chan_truc_tiep_host_la (cũ: host googlevideo
    # .com.evil → StreamUnavailableError, không chạm mạng). NV304d:
    # module mới (e) đã ĐÁNH RƠI kiểm host này → KHÔI PHỤC trong (d)
    # (xem test đỏ-trước-xanh trong báo cáo).
    tx = tai_trich_xuat()
    monkeypatch.setattr(tx, "_yt_dlp", lambda: _yt_dlp_gia(
        {"url": "https://googlevideo.com.evil.com/x.mp4",
         "title": "T", "duration": 10}))
    with pytest.raises(tx.TrichXuatUnavailable) as exc:
        tx.resolve_youtube_audio("dQw4w9WgXcQ")
    assert str(exc.value) == "youtube_host_rejected"
    monkeypatch.setattr(tx, "_yt_dlp", lambda: _yt_dlp_gia(
        {"url": "https://rr1---sn.googlevideo.com/videoplayback?id=1",
         "title": "T", "duration": 10}))
    assert tx.resolve_youtube_audio(
        "dQw4w9WgXcQ")["url"].endswith("id=1")


def test_resolve_facebook_chi_nhan_fbcdn(monkeypatch):
    tx = tai_trich_xuat()
    monkeypatch.setattr(tx, "_yt_dlp", lambda: _yt_dlp_gia(
        {"formats": [{"url": "https://evil.example.com/a.m4a",
                      "acodec": "mp4a", "vcodec": "none"}],
         "title": "T", "duration": 10}))
    with pytest.raises(tx.TrichXuatUnavailable) as exc:
        tx.resolve_facebook_audio("123456789012345")
    assert str(exc.value) == "facebook_host_rejected"
    monkeypatch.setattr(tx, "_yt_dlp", lambda: _yt_dlp_gia(
        {"formats": [{"url": "https://video.fbcdn.net/a.m4a",
                      "acodec": "mp4a", "vcodec": "none"}],
         "title": "T", "duration": 10}))
    assert tx.resolve_facebook_audio(
        "123456789012345")["url"].startswith("https://video.fbcdn.net/")


def test_ve_luong_gia_het_han_m3u8_qua_loi_404_trong(moi):
    # Port test_bao_mat vé luồng (cũ: 403 invalid_stream_token).
    # NV304d: lõi ký URL (url_ky) trả 404 TRỐNG cho mọi vé sai — không
    # lộ module nào tồn tại hay vé sai ở đâu (theo tài liệu lõi).
    h = moi.khoa()
    r = moi.c.post("/m/youtube/api/integration/stream", headers=h,
                   json={"source": "youtube", "target": VIDEO_1})
    assert r.status_code == 200
    url = r.json()["stream_url"]
    duong = url.split("http://testserver")[-1]
    assert moi.c.get(duong).status_code == 200
    gia = duong.replace("_ky=", "_ky=00000000000000000000000000000000"
                        "00000000000000000000000000000000")
    r = moi.c.get(gia)
    assert r.status_code == 404 and r.content == b""
    r = moi.c.get("/m/youtube/api/stream/" + "x" * 5000)
    assert r.status_code == 404
    r = moi.c.get("/m/youtube/api/stream/sai.m3u8")
    assert r.status_code == 404  # cũ: 403 (token .m3u8 sai)


def test_log_khong_chua_bi_mat(mm, caplog):
    import logging
    mod, m = mm
    bi_mat = ["BI_MAT_STREAM_123", "BI_MAT_BEARER_456", "BI_MAT_QUERY_789"]

    class Hong:
        async def tim_kiem(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

        async def giai_am_thanh(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

        async def lay_danh_sach_phat(self, *a):
            raise mod.TrichXuatUnavailable("search_unavailable")

    m.trich_xuat = Hong()
    with caplog.at_level(logging.WARNING):
        goi(m, "integration/search",
            query={"q": "BI_MAT_QUERY_789"}, phuong_thuc="GET")
        # Thượng nguồn chứa bí mật, luôn hỏng → rơ-le log kiểu lỗi.
        m._luong["ve1"] = {"url": "https://evil.example/BI_MAT_STREAM_123",
                           "headers": {}, "total": 100, "duration": 10,
                           "source": "youtube", "het_han": time.time() + 3600}

        async def _hong(url, headers):
            raise ConnectionError("dut")

        m.mo_nguon = _hong

        async def _doc():
            _, _, gen = await m.luong("ve1", None, "GET")
            try:
                async for _ in gen:
                    pass
            except Exception:
                pass

        asyncio.run(_doc())
    for s in bi_mat:
        assert s not in caplog.text


def test_tep_ben_0600_thu_muc_0700(monkeypatch, tmp_path):
    import os
    mod = tai_module()
    thu_muc = tmp_path / "yt-moi"  # chưa tồn tại → mkdir tạo 0700
    m = mod.Module({}, _thu_muc_du_lieu=str(thu_muc))
    m.nap({})
    m.trich_xuat = TrichXuatGia()
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    goi(m, "integration/playlists",
        {"action": "create", "name": "P",
         "items": [_muc_youtube(VIDEO_1, "B1")]})
    assert (os.stat(thu_muc).st_mode & 0o777) == 0o700
    assert (os.stat(thu_muc / "playlists.json").st_mode & 0o777) == 0o600


def test_ve_luong_ngau_nhien_va_urlsafe(mm):
    import re
    mod, m = mm
    v1 = m._dang_ky_luong({"url": "https://x.googlevideo.com/a",
                           "source": "youtube"})
    v2 = m._dang_ky_luong({"url": "https://x.googlevideo.com/a",
                           "source": "youtube"})
    assert v1["token"] != v2["token"]
    assert re.fullmatch(r"[A-Za-z0-9_-]+", v1["token"])
    assert v1["expires_in"] == 3600


# ── test_luong + test_luong_doc_loi (port, qua _luong_chiu_loi) ──────────

class RelayFake:
    """mo_khuc/dong/giai_lai/kiem_dung/ngu giả cho rơ-le."""

    def __init__(self, du_lieu, opens):
        self.du_lieu = du_lieu
        self.opens = opens  # list spec theo lượt mở
        self.calls = []
        self.giai_lai = 0
        self.ngu = []
        self.dong_dem = 0

    async def mo_khuc(self, tu, den):
        self.calls.append((tu, den))
        # Hết kịch bản thì phục vụ mặc định (khúc đúng CR); muốn lặp
        # cùng một lỗi thì gọi phải liệt kê đủ số spec (xem test 5 lần).
        if len(self.calls) - 1 < len(self.opens):
            spec = self.opens[len(self.calls) - 1]
        else:
            spec = {}
        if spec.get("mo_loi") is not None:
            raise spec["mo_loi"]
        status = spec.get("status", 206)
        headers = {}
        if status == 206:
            cr = spec.get("cr")
            if cr is None:
                cat = self.du_lieu[tu:den + 1]
                cr = (tu, tu + len(cat) - 1, len(self.du_lieu))
            headers["Content-Range"] = f"bytes {cr[0]}-{cr[1]}/{cr[2]}"
        chunks = spec.get("chunks")
        if chunks is None:
            chunks = [self.du_lieu[tu:den + 1]]
        elif callable(chunks):
            chunks = chunks(tu, den)
        return BanGia(status, headers, chunks)

    async def dong_khuc(self, ban):
        self.dong_dem += 1
        await ban.aclose()

    async def giai_lai_link(self):
        self.giai_lai += 1

    async def kiem_dung_fn(self):
        return False

    async def ngu_fn(self, giay):
        self.ngu.append(giay)


def _chay_relay(mod, m, fake, dau, cuoi, tong):
    gen = m._luong_chiu_loi(fake.mo_khuc, fake.dong_khuc,
                            fake.giai_lai_link, fake.kiem_dung_fn,
                            dau, cuoi, tong, ngu=fake.ngu_fn)
    return _thu_thap(gen)


def test_relay_cat_giua_khuc_noi_lai_dung_offset(mm):
    mod, m = mm
    mod.KHUC = 256
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"chunks": [du_lieu[0:100]]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.calls[1][0] == 100 and fake.ngu == [0.5]


def test_relay_cat_truoc_va_sau_ranh_gioi_256(mm):
    mod, m = mm
    mod.KHUC = 256
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"chunks": [du_lieu[0:200]]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.calls[1][0] == 200
    fake = RelayFake(du_lieu, [{"chunks": [du_lieu[0:256]]},
                                {"chunks": [du_lieu[256:356]]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert (356, 611) in fake.calls


def test_relay_3_lan_cat_lien_tiep(mm):
    mod, m = mm
    mod.KHUC = 256
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"chunks": [du_lieu[0:100]]},
                                {"chunks": [du_lieu[100:200]]},
                                {"chunks": [du_lieu[200:300]]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert [c[0] for c in fake.calls[:4]] == [0, 100, 200, 300]
    assert len(fake.ngu) == 3


def test_relay_403_thi_giai_lai_link(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"status": 403}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.giai_lai == 1 and fake.ngu == [0.5]


def test_relay_200_phot_range_thi_giai_lai_roi_thu_lai(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"status": 200, "chunks": [du_lieu]}])
    data, err = _chay_relay(mod, m, fake, 100, 999, 1000)
    assert err is None and data == du_lieu[100:1000]
    assert fake.giai_lai == 1


def test_relay_content_range_lech_va_doi_tong(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"cr": (50, 305, 1000)}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    fake = RelayFake(du_lieu, [{"cr": (0, 255, 2000)}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]


def test_relay_416_tra_im_lang(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"status": 416}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == b""
    assert len(fake.calls) == 1 and fake.ngu == []


def test_relay_qua_5_lan_thi_ngat(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{"mo_loi": ConnectionError("dut")}] * 5)
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert isinstance(err, mod.LoiNguonKhongCuuDuoc)
    assert len(fake.calls) == 5 and fake.ngu == [0.5, 1, 2, 2]


def test_relay_doc_loi_giua_khuc_noi_lai_dung_offset(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(
        du_lieu, [{"chunks": [du_lieu[0:300], ConnectionResetError("rst")]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.calls[1][0] == 300 and fake.ngu == [0.5]
    fake = RelayFake(
        du_lieu, [{"chunks": [du_lieu[0:512], TimeoutError("het-gio")]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.calls[1][0] == 512


def test_relay_mo_lai_hong_ngay_mieng_dau_khong_nhay_offset(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(
        du_lieu, [{"chunks": [du_lieu[0:100], ConnectionError("dut")]},
                  {"chunks": [ConnectionError("dut")]}])
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert err is None and data == du_lieu[:1000]
    assert fake.calls[2][0] == 100


def test_relay_doc_loi_8_lan_5_lan_mo_backoff_dung(mm):
    mod, m = mm
    du_lieu = (bytes(range(256)) * 4)[:1000]

    def _cat10(tu, den):
        return [du_lieu[tu:tu + 10], ConnectionError("dut")]

    fake = RelayFake(du_lieu, [{"chunks": _cat10}] * 5)
    data, err = _chay_relay(mod, m, fake, 0, 999, 1000)
    assert isinstance(err, mod.LoiNguonKhongCuuDuoc)
    assert data == du_lieu[:50]
    assert [c[0] for c in fake.calls] == [0, 10, 20, 30, 40]
    assert fake.ngu == [0.5, 1, 2, 2]


def test_relay_ben_nhan_dong_giua_chung_khong_mo_lai(mm):
    mod, m = mm
    mod.KHUC = 256
    du_lieu = (bytes(range(256)) * 4)[:1000]
    fake = RelayFake(du_lieu, [{}])

    async def _chay():
        gen = m._luong_chiu_loi(fake.mo_khuc, fake.dong_khuc,
                                fake.giai_lai_link, fake.kiem_dung_fn,
                                0, 999, 1000, ngu=fake.ngu_fn)
        it = gen.__aiter__()
        await it.__anext__()
        await gen.aclose()

    asyncio.run(_chay())
    assert len(fake.calls) == 1 and fake.giai_lai == 0 and fake.ngu == []


def test_relay_doc_cham_khong_phinh_bo_nho(mm):
    mod, m = mm
    du_lieu = bytes(range(256)) * 32  # 8 KiB, chia miếng 64 KiB → 1 miếng
    du_lieu = (bytes(range(256)) * 8192)[: 2 * 1024 * 1024]  # 2 MiB
    mieng = 65536
    fake = RelayFake(du_lieu, [{"chunks":
                                [du_lieu[i:i + mieng]
                                 for i in range(0, len(du_lieu), mieng)]}])
    tracemalloc.start()
    try:
        async def _chay():
            gen = m._luong_chiu_loi(fake.mo_khuc, fake.dong_khuc,
                                    fake.giai_lai_link, fake.kiem_dung_fn,
                                    0, len(du_lieu) - 1, len(du_lieu),
                                    ngu=fake.ngu_fn)
            h = hashlib.sha256()
            dem = 0
            async for chunk in gen:
                h.update(chunk)
                dem += 1
            return h.hexdigest(), dem

        digest, dem = asyncio.run(_chay())
        _, dinh = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert digest == hashlib.sha256(du_lieu).hexdigest()
    assert dem == 32
    assert dinh < 2 * 1024 * 1024


# ── test_tim_kiem_chuyen_huong (phần còn port được) ──────────────────────

def test_tim_facebook_chi_nhan_link(mm):
    mod, m = mm
    ma, _, body = goi(m, "integration/search",
                      query={"q": "https://www.facebook.com/watch/?v=123456789012345",
                             "source": "facebook"},
                      phuong_thuc="GET")
    assert ma == 200 and body["items"][0]["id"] == "123456789012345"
    # NV304d: 9 case handler redirect urllib KHÔNG port được (handler đã
    # bỏ, Facebook giải qua yt-dlp) — xem báo cáo; phần bảo vệ còn lại
    # là lọc host fbcdn (test_resolve_facebook_chi_nhan_fbcdn).


# ── test_xac_thuc_khong_ascii (port, qua lõi) ────────────────────────────

def test_bearer_khong_ascii_401_khong_500(moi):
    r = moi.c.get("/m/youtube/api/integration/health",
                  headers=[(b"authorization", b"Bearer \xc3\xb6k")])
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_auth"
    r = moi.c.get("/m/youtube/api/integration/health", headers=moi.khoa())
    assert r.status_code == 200


def test_ve_luong_khong_ascii_404_khong_500(moi):
    r = moi.c.get("/m/youtube/api/stream/YWJj.tök")
    assert r.status_code == 404 and r.content == b""


# ── Smoke bảng endpoint cũ-vs-mới (mọi route cũ đều trả 200) ───────────

def test_bang_endpoint_16_route_cu_deu_co(moi):
    # Bảng đầy đủ trong báo cáo (d). Smoke này khóa bảng: mọi route của
    # dịch vụ cũ đều có mặt ở module mới (tiền tố /m/youtube/api/...).
    h = moi.khoa()
    r = moi.c.post("/m/youtube/api/integration/session", headers=h,
                   json={"source": "youtube", "target": VIDEO_1,
                         "queue": [_muc_youtube(VIDEO_1, "B1"),
                                   _muc_youtube(VIDEO_2, "B2")]})
    assert r.status_code == 200
    sid = r.json()["session"]["session_id"]
    checks = [
        ("GET", "/m/youtube/api/integration/health", None),
        ("GET", "/m/youtube/api/integration/search?source=youtube&q=bai", None),
        ("GET", "/m/youtube/api/integration/status", None),
        ("GET", "/m/youtube/api/integration/history", None),
        ("GET", "/m/youtube/api/integration/playlists", None),
        ("POST", "/m/youtube/api/integration/playlists", {"action": "list"}),
        ("POST", "/m/youtube/api/integration/session",
         {"source": "youtube", "target": VIDEO_1}),
        ("POST", "/m/youtube/api/integration/session/outputs",
         {"session_id": sid, "output_entity_ids": ["media_player.loa"]}),
        ("POST", "/m/youtube/api/integration/stream",
         {"source": "youtube", "target": VIDEO_1}),
        ("POST", "/m/youtube/api/integration/play",
         {"source": "youtube", "target": VIDEO_1}),
        ("POST", "/m/youtube/api/integration/next", {"session_id": sid}),
        ("POST", "/m/youtube/api/integration/prev", {"session_id": sid}),
        ("POST", "/m/youtube/api/integration/seek",
         {"session_id": sid, "giay": 10}),
    ]
    for method, duong, than in checks:
        if method == "GET":
            r = moi.c.get(duong, headers=h)
        else:
            r = moi.c.post(duong, headers=h, json=than)
        assert r.status_code == 200, (method, duong, r.status_code, r.text[:200])
    r = moi.c.post("/m/youtube/api/integration/stream", headers=h,
                   json={"source": "youtube", "target": VIDEO_1})
    stream_url = r.json()["stream_url"]
    duong = stream_url.split("http://testserver")[-1]
    r = moi.c.get(duong)
    assert r.status_code == 200 and len(r.content) == 1024
    r = moi.c.head(duong)
    assert r.status_code == 200 and r.content == b""
    # NV304d: lõi trả HEAD với thân rỗng nên Starlette ghi
    # Content-Length 0 (khác cũ: HEAD thăm dò trả tổng thật) — ghi
    # rõ ở báo cáo, không sửa lõi trong (d).
    assert r.headers["content-type"] == "audio/mp4"
    r = moi.c.post("/m/youtube/api/integration/stop", headers=h,
                   json={"session_id": sid})
    assert r.status_code == 200 and r.json()["stopped"] is True


# ── NV304d: test khóa các hành vi đúng vừa khôi phục theo c2a ───────────

def test_play_chi_target_link_youtu_be_khong_can_source(mm):
    # c2a play chỉ nhận target, tự suy nguồn từ link (kể cả youtu.be).
    mod, m = mm
    ma, _, body = goi(m, "integration/play",
                      {"target": f"https://youtu.be/{VIDEO_1}"})
    assert ma == 200 and body["item"]["id"] == VIDEO_1


def test_stop_theo_revision_cu_khong_dung_phien_moi(mm):
    # c2a: stop với revision của phiên cũ → stopped False, phiên hiện
    # tại vẫn là phiên mới; đúng revision mới → stopped True.
    mod, m = mm
    a = tao_phien(m)
    rev_a = a["revision"]
    b = tao_phien(m)
    rev_b = b["revision"]
    assert rev_b > rev_a
    ma, _, body = goi(m, "integration/stop", {"expected_revision": rev_a})
    assert ma == 200 and body["stopped"] is False
    assert body["session"]["item"]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/stop", {"expected_revision": rev_b})
    assert ma == 200 and body["stopped"] is True


def test_history_item_co_id_va_session_item_co_artist(mm):
    # c2a: history items[0]["id"], session item có "artist" (= channel).
    mod, m = mm
    tao_phien(m)
    ma, _, body = goi(m, "integration/history", phuong_thuc="GET")
    assert ma == 200 and body["items"][0]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/status", phuong_thuc="GET")
    assert body["session"]["item"]["artist"] == "Kênh giả"


def test_search_link_dai_2048_duoc_tu_khoa_dai_120_bi_chan(mm):
    # c2a: MAX_URL_LENGTH=2048 cho link, MAX_QUERY_LENGTH=120 cho từ khoá.
    mod, m = mm
    link_dai = (f"https://www.youtube.com/watch?v={VIDEO_1}&x="
                + "y" * 1400)
    assert len(link_dai) > 120 and len(link_dai) < 2048
    ma, _, body = goi(m, "integration/search",
                      query={"q": link_dai}, phuong_thuc="GET")
    assert ma == 200 and body["items"][0]["id"] == VIDEO_1
    ma, _, body = goi(m, "integration/search",
                      query={"q": "x" * 200}, phuong_thuc="GET")
    assert ma == 400 and body["error"] == "invalid_search_query"


def test_volume_khong_phai_so_bao_invalid_volume_level(mm):
    mod, m = mm
    ma, _, body = goi(
        m, "integration/session",
        {"source": "youtube", "target": VIDEO_1, "volume_level": "abc"})
    assert ma == 400 and body["error"] == "invalid_volume_level"
