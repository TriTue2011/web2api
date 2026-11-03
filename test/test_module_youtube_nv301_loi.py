"""Test NV301 mảnh lõi — phiên ngang dịch vụ cũ (dich-vu/youtube-phat).

Chạy async qua asyncio.run (kho chưa có pytest-asyncio). Không Internet
thật: trich_xuat chích giả; conftest chung đã chặn đường ra ngoài.

Kiểm (đối chiếu hợp đồng dịch vụ cũ trong dich-vu/youtube-phat):
- status đủ hình dạng cũ: success/api_version/app_version/state/item/
  session/sessions/history_count; phiên có item (title/channel/
  thumbnail/duration/id/source) + queue {index, items} + revision.
- session POST trả 200 (như dịch vụ cũ, không phải 201) và ghi lịch sử.
- Hàng đợi thật: theo playlist_id, theo kết quả tìm gần nhất; next/prev
  chuyển bài, đặt lại position = 0; ở biên → 409 queue_end/queue_start
  (không ở yên như NV277); không phiên → 404.
- pause giữ phiên (paused), play tiếp tục; stop rồi thì next/play
  theo id → 404 (dừng là dừng hẳn, không hồi sinh); sessions trong
  status loại phiên đã dừng (nhưng _phien vẫn giữ bản ghi cho tab).
- seek nhận cả `giay` lẫn `position`, kẹp theo thời lượng, seek_url None
  (không bịa URL — rơ-le vé không có sidx); sai → 400
  invalid_seek_position.
- search trả cả `results` (NV277) lẫn `items` (dịch vụ cũ) với
  channel/thumbnail; Facebook chỉ nhận link (chữ → 400).
- session/outputs nhận cả `outputs` lẫn `output_entity_ids`; rỗng → dừng.
- stop theo expected_revision sai → 200 {stopped: False}.
- Giải siêu dữ liệu hỏng thì title rơi về id, ảnh rơi về i.ytimg.com
  (quy ước dịch vụ cũ), không bịa tên bài.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import tempfile

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube"
MODULE_PY = THU_MUC_MODULE / "module.py"

VIDEO_1 = "dQw4w9WgXcQ"
VIDEO_2 = "9bZkp7q19f0"
VIDEO_3 = "kXYiU_JCYtU"


def tai_module():
    ten = "mod_youtube_nv301_loi"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, MODULE_PY, submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


def _muc(vid, tieu_de):
    return {"id": vid, "title": tieu_de, "channel": f"Kênh {tieu_de}",
            "artists": [], "duration": 213,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "source": "youtube"}


class TrichXuatGia:
    def __init__(self, hong_giai=False):
        self.hong_giai = hong_giai
        self.goi_giai = []

    async def tim_kiem(self, source, query, limit):
        return [_muc(VIDEO_1, "Bài một"), _muc(VIDEO_2, "Bài hai"),
                _muc(VIDEO_3, "Bài ba")][:limit]

    async def giai_am_thanh(self, source, target):
        self.goi_giai.append((source, target))
        if self.hong_giai:
            raise Exception("nguồn giả hỏng")
        return {"url": "fake://am-thanh-1", "title": "Bài giả",
                "duration": 213, "source": source,
                "channel": "Kênh giả",
                "thumbnail": f"https://i.ytimg.com/vi/{target}/hqdefault.jpg"}

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        return ("Playlist giả", [_muc(VIDEO_1, "Bài một"),
                                  _muc(VIDEO_2, "Bài hai")])


def _mod(tmp_path=None, hong_giai=False):
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=tmp_path or tempfile.mkdtemp(prefix="yt301-"))
    m.nap({})
    m.trich_xuat = TrichXuatGia(hong_giai=hong_giai)
    return mod, m


def _phien_moi(m, **body):
    than = {"source": "youtube", "target": VIDEO_1}
    than.update(body)
    ma, _, nd = asyncio.run(m.tich_hop("integration/session", "POST", {}, than))
    assert ma == 200, nd
    return nd["session"]


def test_status_du_truong_dich_vu_cu_va_sieu_du_lieu(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert ma == 200 and nd["success"] is True
    assert nd["api_version"] == "1" and nd["app_version"]
    assert nd["state"] == "idle" and nd["item"] is None
    assert nd["session"]["state"] == "idle" and nd["sessions"] == []
    assert nd["history_count"] == 0
    ss = _phien_moi(m)
    assert ss["state"] == "playing" and ss["status"] == "playing"
    assert ss["item"]["id"] == VIDEO_1 and ss["item"]["title"] == "Bài giả"
    assert ss["item"]["channel"] == "Kênh giả"
    assert ss["item"]["thumbnail"].startswith("https://i.ytimg.com/vi/")
    assert ss["item"]["source"] == "youtube" and ss["duration"] == 213
    assert ss["queue"]["index"] == 0 and len(ss["queue"]["items"]) == 1
    assert ss["queue_index"] == 0 and ss["revision"] >= 1
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert nd["state"] == "playing" and nd["item"]["id"] == VIDEO_1
    assert nd["session"]["item"]["duration"] == 213
    assert nd["session"]["position"] == 0
    assert len(nd["sessions"]) == 1


def test_hang_doi_theo_playlist_id_va_next_prev(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {}, {"action": "create", "name": "PL"}))
    assert ma == 200
    pid = nd["playlist"]["id"]
    for vid, ten in ((VIDEO_1, "Bài một"), (VIDEO_2, "Bài hai"), (VIDEO_3, "Bài ba")):
        ma, _, nd = asyncio.run(m.tich_hop(
            "integration/playlists", "POST", {},
            {"action": "add", "playlist_id": pid, "item": _muc(vid, ten)}))
        assert ma == 200
    ss = _phien_moi(m, target=VIDEO_2, playlist_id=pid)
    assert len(ss["queue"]["items"]) == 3 and ss["queue_index"] == 1
    assert ss["item"]["id"] == VIDEO_2
    sid = ss["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop("integration/next", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["session"]["item"]["id"] == VIDEO_3
    assert nd["item"]["id"] == VIDEO_3 and nd["session"]["position"] == 0
    ma, _, nd = asyncio.run(m.tich_hop("integration/next", "POST", {},
                                       {"session_id": sid}))
    assert ma == 409 and nd["error"] == "queue_end"
    ma, _, nd = asyncio.run(m.tich_hop("integration/prev", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["session"]["item"]["id"] == VIDEO_2
    asyncio.run(m.tich_hop("integration/prev", "POST", {}, {"session_id": sid}))
    ma, _, nd = asyncio.run(m.tich_hop("integration/prev", "POST", {},
                                       {"session_id": sid}))
    assert ma == 409 and nd["error"] == "queue_start"
    ma, _, _ = asyncio.run(m.tich_hop("integration/next", "POST", {},
                                      {"session_id": "khong-co"}))
    assert ma == 404


def test_hang_doi_tu_ket_qua_tim_gan_nhat(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "test", "source": "youtube",
                                        "limit": 3}, None))
    assert ma == 200 and len(nd["results"]) == 3
    ss = _phien_moi(m, target=VIDEO_2)
    assert len(ss["queue"]["items"]) == 3 and ss["queue_index"] == 1
    ma, _, nd = asyncio.run(m.tich_hop("integration/next", "POST", {}, {}))
    assert ma == 200 and nd["session"]["item"]["id"] == VIDEO_3


def test_next_prev_khong_hang_doi_thi_409(tmp_path):
    _, m = _mod(tmp_path)
    _phien_moi(m)
    ma, _, nd = asyncio.run(m.tich_hop("integration/next", "POST", {}, {}))
    assert ma == 409 and nd["error"] == "queue_end"
    ma, _, nd = asyncio.run(m.tich_hop("integration/prev", "POST", {}, {}))
    assert ma == 409 and nd["error"] == "queue_start"


def test_pause_resume_stop_khong_hoi_sinh(tmp_path):
    _, m = _mod(tmp_path)
    ss = _phien_moi(m)
    sid = ss["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop("integration/pause", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["session"]["state"] == "paused"
    ma, _, nd = asyncio.run(m.tich_hop("integration/play", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["session"]["state"] == "playing"
    ma, _, nd = asyncio.run(m.tich_hop("integration/stop", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["stopped"] is True and nd["state"] == "idle"
    ma, _, _ = asyncio.run(m.tich_hop("integration/next", "POST", {},
                                      {"session_id": sid}))
    assert ma == 404
    ma, _, _ = asyncio.run(m.tich_hop("integration/play", "POST", {},
                                      {"session_id": sid}))
    assert ma == 404
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert nd["sessions"] == [] and nd["state"] == "idle"
    # Bản ghi dừng vẫn ở lại _phien để tab hiện «dung» (test mảnh tab).
    assert m._phien[sid]["status"] == "stopped"


def test_seek_giay_position_kep_va_loi(tmp_path):
    _, m = _mod(tmp_path)
    _phien_moi(m)
    ma, _, nd = asyncio.run(m.tich_hop("integration/seek", "POST", {},
                                       {"giay": 300}))
    assert ma == 200 and nd["position"] == 213  # kẹp theo thời lượng
    assert nd["seek_url"] is None
    ma, _, nd = asyncio.run(m.tich_hop("integration/seek", "POST", {},
                                       {"position": 10}))
    assert ma == 200 and nd["position"] == 10
    ma, _, nd = asyncio.run(m.tich_hop("integration/seek", "POST", {},
                                       {"giay": -1}))
    assert ma == 400 and nd["error"] == "invalid_seek_position"
    ma, _, _ = asyncio.run(m.tich_hop("integration/seek", "POST", {},
                                      {"giay": 5, "session_id": "khong-co"}))
    assert ma == 404


def test_search_ca_results_items_va_facebook_chi_link(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "test", "source": "youtube",
                                        "limit": 3}, None))
    assert ma == 200 and nd["items"] == nd["results"] and nd["total"] == 3
    assert nd["results"][0]["channel"] and nd["results"][0]["thumbnail"]
    assert nd["results"][0]["kind"] == "video"
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "123456789012", "source": "facebook",
                                        "limit": 5}, None))
    assert ma == 200 and nd["results"][0]["source"] == "facebook"
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "tu khoa thuong", "source": "facebook",
                                        "limit": 5}, None))
    assert ma == 400 and nd["error"] == "invalid_search_query"


def test_search_link_youtube_video_va_playlist(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/search", "GET",
        {"q": f"https://www.youtube.com/watch?v={VIDEO_1}",
         "source": "youtube", "limit": 5}, None))
    assert ma == 200 and len(nd["results"]) == 1
    assert nd["results"][0]["id"] == VIDEO_1
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/search", "GET",
        {"q": "https://www.youtube.com/playlist?list=PL1234567890abcdef",
         "source": "youtube", "limit": 5}, None))
    assert ma == 200 and len(nd["results"]) == 2


def test_outputs_bi_danh_va_rong_thi_dung(tmp_path):
    _, m = _mod(tmp_path)
    ss = _phien_moi(m, output_entity_ids=["media_player.phong_khach"])
    assert ss["output_entity_ids"] == ["media_player.phong_khach"]
    sid = ss["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session/outputs", "POST", {},
        {"session_id": sid, "output_entity_ids": ["media_player.bep"]}))
    assert ma == 200 and nd["session"]["outputs"] == ["media_player.bep"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session/outputs", "POST", {},
        {"session_id": sid, "outputs": []}))
    assert ma == 200 and nd["stopped"] is True


def test_stop_sai_revision_khong_dung(tmp_path):
    _, m = _mod(tmp_path)
    ss = _phien_moi(m)
    sid = ss["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/stop", "POST", {},
        {"session_id": sid, "expected_revision": 999999}))
    assert ma == 200 and nd["stopped"] is False
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/stop", "POST", {}, {"session_id": sid}))
    assert ma == 200 and nd["stopped"] is True


def test_playlist_khong_co_va_sieu_du_lieu_hong(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_1, "playlist_id": "khong-co"}))
    assert ma == 404 and nd["error"] == "playlist_not_found"
    _, m2 = _mod(tmp_path, hong_giai=True)
    # Fake hỏng ném Exception thường (không phải TrichXuatUnavailable):
    # _sieu_du_lieu bắt mọi lỗi, giữ gợi ý — title rơi về id.
    ma, _, nd = asyncio.run(m2.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_1}))
    assert ma == 200
    assert nd["session"]["item"]["title"] == VIDEO_1
    assert nd["session"]["item"]["thumbnail"] == \
        f"https://i.ytimg.com/vi/{VIDEO_1}/hqdefault.jpg"


def test_session_post_200_va_history_co_items(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_1}))
    assert ma == 200 and nd["item"]["id"] == VIDEO_1
    ma, _, nd = asyncio.run(m.tich_hop("integration/history", "GET", {}, None))
    assert ma == 200 and nd["items"] == nd["history"] and nd["total"] == 1
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {}, {"action": "create", "name": "X"}))
    assert ma == 200
