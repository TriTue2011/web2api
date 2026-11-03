"""Test NV301 mảnh playlist — nhập playlist từ link/mã chia sẻ.

Chạy async qua asyncio.run. Không Internet thật: trich_xuat chích giả.

Kiểm (đối chiếu playlists.py + dich_vu.playlist_action của dịch vụ cũ):
- Import mã chia sẻ TTPL1. (định dạng base64url(zlib(JSON)) — test tự
  dựng mã bằng zlib/base64, KHÔNG qua hàm của module, để kiểm tương
  thích định dạng thật): tạo playlist đúng tên + bài đã chuẩn hoá.
- Export ra mã TTPL1. mà test tự giải (zlib) đọc lại được đúng tên/bài.
- Import link playlist YouTube (/playlist và link xem có list=) qua
  lay_danh_sach_phat giả; playlist nhập về phát được qua playlist_id.
- Link lạ → 400 invalid_playlist_link; mã hỏng → 400
  invalid_share_code; playlist rỗng → 400 playlist_empty; lấy playlist
  hỏng → 502 playlist_unavailable; link album Zing → 502
  playlist_unavailable (không khoá API Zing).
- Create kèm items chuẩn hoá + khử trùng; add trùng → added 0, add mới
  → added 1, add sai → 400 invalid_item; remove/move theo chỉ số kiểu
  cũ, sai chỉ số → 400 invalid_playlist_index; import dạng cũ
  (`playlists`) vẫn thay toàn bộ kho.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import pathlib
import sys
import tempfile
import zlib

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube"
MODULE_PY = THU_MUC_MODULE / "module.py"

VIDEO_1 = "dQw4w9WgXcQ"
VIDEO_2 = "9bZkp7q19f0"


def tai_module():
    ten = "mod_youtube_nv301_playlist"
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
            "duration": 200, "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "source": "youtube"}


def _dung_ma_chia_se(ten, hang):
    """Dựng mã TTPL1. độc lập với mã module (theo đúng đặc tả định dạng)."""
    compact = {"n": ten,
               "i": [[m["source"], m["id"], m["url"], m["title"],
                      m["channel"], m["duration"], m["thumbnail"]]
                     for m in hang]}
    raw = zlib.compress(json.dumps(compact, ensure_ascii=False,
                                   separators=(",", ":")).encode(), 9)
    return "TTPL1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _giai_ma_chia_se(code):
    body = code[len("TTPL1."):]
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    return json.loads(zlib.decompress(raw))


class TrichXuatGia:
    def __init__(self, playlist=None, hong=False, loi=None):
        self.playlist = playlist
        self.hong = hong
        self.loi = loi or Exception

    async def tim_kiem(self, source, query, limit):
        return []

    async def giai_am_thanh(self, source, target):
        return {"url": "fake://am-thanh-1", "title": "Bài giả",
                "duration": 200, "source": source}

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        if self.hong:
            raise self.loi("playlist_unavailable")
        if self.playlist is None:
            return ("Playlist YouTube", [])
        return self.playlist


def _mod(tmp_path=None, **gia_kw):
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=tmp_path or tempfile.mkdtemp(prefix="yt301pl-"))
    m.nap({})
    # Fake phải ném đúng TrichXuatUnavailable như TrichXuat thật.
    m.trich_xuat = TrichXuatGia(loi=mod.TrichXuatUnavailable, **gia_kw)
    return mod, m


def _nhap(m, text, **them):
    than = {"action": "import", "text": text}
    than.update(them)
    return asyncio.run(m.tich_hop("integration/playlists", "POST", {}, than))


def test_import_ma_chia_se_dung_dinh_dang_cu(tmp_path):
    _, m = _mod(tmp_path)
    hang = [_muc(VIDEO_1, "Bài một"), _muc(VIDEO_2, "Bài hai")]
    code = _dung_ma_chia_se("Chia sẻ nhà", hang)
    ma, _, nd = _nhap(m, code)
    assert ma == 200, nd
    pl = nd["playlist"]
    assert pl["name"] == "Chia sẻ nhà" and len(pl["items"]) == 2
    assert pl["items"][0]["id"] == VIDEO_1
    assert pl["items"][0]["channel"] == "Kênh Bài một"
    assert pl["items"][1]["title"] == "Bài hai"
    # Import xong phát được qua playlist_id (nối mảnh lõi).
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_2, "playlist_id": pl["id"]}))
    assert ma == 200 and len(nd["session"]["queue"]["items"]) == 2
    assert nd["session"]["queue_index"] == 1


def test_import_ma_chia_se_co_ten_ghi_de(tmp_path):
    _, m = _mod(tmp_path)
    code = _dung_ma_chia_se("Tên gốc", [_muc(VIDEO_1, "Bài một")])
    ma, _, nd = _nhap(m, code, name="Tên mới")
    assert ma == 200 and nd["playlist"]["name"] == "Tên mới"


def test_export_ra_ma_chia_se_doc_lai_duoc(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "create", "name": "Xuất đi",
         "items": [_muc(VIDEO_1, "Bài một")]}))
    assert ma == 200
    pid = nd["playlist"]["id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "export", "playlist_id": pid}))
    assert ma == 200 and nd["code"].startswith("TTPL1.")
    du_lieu = _giai_ma_chia_se(nd["code"])
    assert du_lieu["n"] == "Xuất đi"
    assert du_lieu["i"][0][1] == VIDEO_1 and du_lieu["i"][0][3] == "Bài một"
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "export", "playlist_id": "khong-co"}))
    assert ma == 404


def test_import_link_playlist_youtube(tmp_path):
    pl_gia = ("Nhạc của tôi", [_muc(VIDEO_1, "Bài một"),
                                _muc(VIDEO_2, "Bài hai")])
    _, m = _mod(tmp_path, playlist=pl_gia)
    ma, _, nd = _nhap(
        m, "https://www.youtube.com/playlist?list=PL1234567890abcdef")
    assert ma == 200, nd
    assert nd["playlist"]["name"] == "Nhạc của tôi"
    assert len(nd["playlist"]["items"]) == 2
    # Link xem có list= cũng hiểu là playlist.
    ma, _, nd = _nhap(
        m, f"https://www.youtube.com/watch?v={VIDEO_1}&list=PL1234567890abcdef")
    assert ma == 200 and len(nd["playlist"]["items"]) == 2


def test_import_loi_ro_rang(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = _nhap(m, "https://example.com/khong-phai-playlist")
    assert ma == 400 and nd["error"] == "invalid_playlist_link"
    ma, _, nd = _nhap(m, "TTPL1.!!!khong-phai-base64")
    assert ma == 400 and nd["error"] == "invalid_share_code"
    ma, _, nd = _nhap(m, "")
    assert ma == 400 and nd["error"] == "invalid_playlist_link"
    ma, _, nd = _nhap(
        m, "https://zingmp3.vn/album/Ten-Album-Nghe-Si/abc12345.html")
    assert ma == 502 and nd["error"] == "playlist_unavailable"
    _, m_rong = _mod(tmp_path, playlist=("Rỗng", []))
    ma, _, nd = _nhap(
        m_rong, "https://www.youtube.com/playlist?list=PL1234567890abcdef")
    assert ma == 400 and nd["error"] == "playlist_empty"
    _, m_hong = _mod(tmp_path, hong=True)
    ma, _, nd = _nhap(
        m_hong, "https://www.youtube.com/playlist?list=PL1234567890abcdef")
    assert ma == 502 and nd["error"] == "playlist_unavailable"


def test_create_add_chuan_hoa_khu_trung(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "create", "name": "  Trùng   lặp  ",
         "items": [_muc(VIDEO_1, "Bài một"), _muc(VIDEO_1, "Bài một"),
                   {"source": "youtube", "id": "x"}]}))
    assert ma == 200
    pl = nd["playlist"]
    assert pl["name"] == "Trùng lặp" and len(pl["items"]) == 1  # trùng + sai bị bỏ
    pid = pl["id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "add", "playlist_id": pid, "item": _muc(VIDEO_1, "Bài một")}))
    assert ma == 200 and nd["added"] == 0 and len(nd["playlist"]["items"]) == 1
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "add", "playlist_id": pid, "item": _muc(VIDEO_2, "Bài hai")}))
    assert ma == 200 and nd["added"] == 1 and len(nd["playlist"]["items"]) == 2
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "add", "playlist_id": pid,
         "item": {"source": "youtube", "id": "sai"}}))
    assert ma == 400 and nd["error"] == "invalid_item"


def test_remove_move_theo_chi_so(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "create", "name": "Đổi chỗ",
         "items": [_muc(VIDEO_1, "Bài một"), _muc(VIDEO_2, "Bài hai")]}))
    pid = nd["playlist"]["id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "move", "playlist_id": pid, "from": 0, "to": 1}))
    assert ma == 200 and nd["playlist"]["items"][0]["id"] == VIDEO_2
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "move", "playlist_id": pid, "from": 0, "to": 9}))
    assert ma == 400 and nd["error"] == "invalid_playlist_index"
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "remove", "playlist_id": pid, "index": 0}))
    assert ma == 200 and len(nd["playlist"]["items"]) == 1
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "remove", "playlist_id": pid, "index": 5}))
    assert ma == 400 and nd["error"] == "invalid_playlist_index"


def test_import_dang_cu_van_thay_toan_bo_kho(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/playlists", "POST", {},
        {"action": "import",
         "playlists": [{"id": "cu", "name": "Cũ", "items": []}]}))
    assert ma == 200 and nd["playlists"][0]["id"] == "cu"
