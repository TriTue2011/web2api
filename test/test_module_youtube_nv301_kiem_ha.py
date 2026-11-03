"""NV301 — kiểm của điều phối: module `youtube` trả cho tích hợp HA ĐỦ trường
mà dịch vụ cũ trả (``youtube_player/app/session.py`` + ``server.py``) và HA đọc
(``custom_components/tritue_youtube_player/media_player.py``, ``sessions.py``).

Đối chiếu bằng danh sách trường chép từ mã dịch vụ cũ (không phụ thuộc đường
dẫn ngoài). Nguồn tìm/giải luồng GIẢ, không Internet.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from test_module_youtube_nv301_loi import (VIDEO_1, _mod, _phien_moi)

# session.py của dịch vụ cũ: _idle_session / start (cùng bộ khoá).
KHOA_PHIEN_CU = {
    "session_id", "revision", "state", "position", "duration", "updated_at",
    "volume_level", "item", "queue", "output_entity_ids", "supported_actions",
    "controller", "auto_advance"}
# server.py: GET /api/integration/status.
KHOA_STATUS_CU = {"success", "api_version", "app_version", "state", "item",
                  "session", "sessions", "history_count"}
# media_player.py + sessions.compact_sessions đọc từ item.
KHOA_ITEM_HA_DOC = {"id", "source", "url", "title", "artist", "album",
                    "duration", "thumbnail", "kind"}


def _status(m):
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert ma == 200
    return nd


def test_status_va_phien_chua_du_khoa_dich_vu_cu_luc_idle_va_luc_phat(tmp_path):
    _, m = _mod(tmp_path)
    nd = _status(m)
    assert KHOA_STATUS_CU <= set(nd)
    assert KHOA_PHIEN_CU <= set(nd["session"])        # idle cũng đủ khoá
    assert nd["session"]["queue"] == {"index": -1, "items": []}
    _phien_moi(m, controller="ha-gia")
    nd = _status(m)
    assert KHOA_STATUS_CU <= set(nd)
    ss = nd["session"]
    assert KHOA_PHIEN_CU <= set(ss)
    assert nd["sessions"] and KHOA_PHIEN_CU <= set(nd["sessions"][0])
    assert ss["controller"] == "ha-gia" and ss["supported_actions"] == ["stop"]
    # HA nội suy vị trí từ updated_at: phải là ISO có múi giờ.
    assert datetime.fromisoformat(ss["updated_at"]).tzinfo is not None


def test_item_co_du_truong_ma_ha_doc_va_artist_la_kenh(tmp_path):
    _, m = _mod(tmp_path)
    ss = _phien_moi(m)
    item = _status(m)["item"]
    assert KHOA_ITEM_HA_DOC <= set(item)
    assert item["id"] == VIDEO_1 and item["title"] == "Bài giả"
    assert item["artist"] == "Kênh giả"               # HA: media_artist
    assert item["thumbnail"].startswith("https://") and item["duration"] == 213
    assert item["kind"] == "video"
    assert ss["item"] == item                          # state/item cấp trên = session.item


def test_updated_at_doi_khi_phien_doi_trang_thai(tmp_path, monkeypatch):
    mod, m = _mod(tmp_path)
    moc = iter(["2026-01-01T00:00:01+00:00", "2026-01-01T00:00:05+00:00",
                "2026-01-01T00:00:09+00:00"])
    cuoi = {"v": "2026-01-01T00:00:00+00:00"}

    def gia():
        cuoi["v"] = next(moc, cuoi["v"])
        return cuoi["v"]
    monkeypatch.setattr(mod, "_iso_utc", gia)
    ss = _phien_moi(m)
    sid = ss["session_id"]
    t0 = ss["updated_at"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/pause", "POST", {}, {"session_id": sid}))
    assert ma == 200
    assert nd["session"]["updated_at"] > t0
