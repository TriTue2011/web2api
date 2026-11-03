"""Test NV301 mảnh giao diện — trình phát tab + dedup (2)+(3) của file.

Chạy async qua asyncio.run. Không Internet thật: trich_xuat chích giả.

Kiểm:
- Lõi (2)+(3): `giao_dien: false` giữ lại qua kiem_manifest và cho
  giao_dien None (module chỉ-công-cụ không tạo tab/mục); `thay_the`
  chuẩn hoá (tối đa 8, id hợp lệ, bỏ chính mình, sai thì bỏ trường);
  tinh_nang: module thay thế ĐÃ CÀI thì module bị thay mất mục VÀ tab
  (kể cả khi thay thế chưa chạy — quy tắc «đã cài thì thắng»); gỡ thay
  thế thì mục/tab cũ hiện lại. Manifest thật: doc-viettat khai
  giao_dien false, youtube khai thay_the ["youtube-phat"].
- Thực thể youtube: 2 thực thể (xem doc + dieu_khien ghi), lược đồ
  tham số hợp lệ (required ⊆ properties, additionalProperties False),
  không lộ khoá (dump không chứa Bearer/tich_hop@).
- Hành động thực thể qua thuc_hien: tim, phat, dung, tam_dung + tiếp
  tục, bai_ke/bai_truoc, tua, danh_sach_playlist, nhap_playlist (mã
  TTPL1.), tao_playlist, xoa_playlist, phat_playlist, them_vao_playlist,
  them_vao_hang_doi, xoa_khoi_hang_doi, phat_tu_hang_doi (giữ nguyên
  hàng đợi, chuyển đúng vị trí kể cả khi có bài trùng), doi_loa,
  cac_phien. Lỗi → {ok: False, loi} tiếng thường, không ném.
- Endpoint integration/queue: GET/POST add/remove.
- Trang tab tĩnh: index.html có đủ id/chữ theo demo (Đang phát, Nhóm
  đang phát, Tìm kiếm, Playlist, Hàng đợi, Loa & tivi, Cài đặt, Nghe
  khi tắt màn hình, chip nguồn, thanh chip dưới); trang.js không dùng
  innerHTML với dữ liệu, không chứa khoá, và chỉ gọi những hành động
  thực thể mà module khai.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import zlib
from types import SimpleNamespace

import pytest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube"
MODULE_PY = THU_MUC_MODULE / "module.py"
TRANG_HTML = THU_MUC_MODULE / "web" / "index.html"
TRANG_JS = THU_MUC_MODULE / "web" / "trang.js"

VIDEO_1 = "dQw4w9WgXcQ"
VIDEO_2 = "9bZkp7q19f0"
VIDEO_3 = "kXYiU_JCYtU"


def tai_module():
    ten = "mod_youtube_nv301_gd"
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
            "artists": [], "duration": 200,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "source": "youtube"}


class TrichXuatGia:
    async def tim_kiem(self, source, query, limit):
        return [_muc(VIDEO_1, "Bài một"), _muc(VIDEO_2, "Bài hai")][:limit]

    async def giai_am_thanh(self, source, target):
        return {"url": "fake://am-thanh-1", "title": "Bài giả",
                "duration": 200, "source": source, "channel": "Kênh giả",
                "thumbnail": f"https://i.ytimg.com/vi/{target}/hqdefault.jpg"}

    async def lay_danh_sach_phat(self, source, playlist_id, limit=500):
        return ("Playlist giả", [_muc(VIDEO_1, "Bài một"),
                                  _muc(VIDEO_2, "Bài hai")])


def _mod(tmp_path=None):
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=tmp_path or tempfile.mkdtemp(prefix="yt301gd-"))
    m.nap({})
    m.trich_xuat = TrichXuatGia()
    return mod, m


def _th(m, entity, hanh_dong, tham_so=None):
    return asyncio.run(m.thuc_hien(entity, hanh_dong, tham_so or {}))


# ── lõi: giao_dien false + thay_the ──────────────────────────────────

def test_giao_dien_false_va_thay_the_chuan_hoa():
    from app.kham_pha import giao_dien_cua, kiem_manifest
    # Dùng manifest thật trên đĩa (đủ trường bắt buộc), sửa trên bản sao.
    dv = json.loads((GOC / "modules" / "doc-viettat" / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(dict(dv)) is None
    assert dv["giao_dien"] is False  # giữ lại, không bị coi là sai rồi bỏ
    assert giao_dien_cua(dv) is None
    yt = json.loads((GOC / "modules" / "youtube" / "manifest.json").read_text(encoding="utf-8"))
    assert kiem_manifest(dict(yt)) is None
    assert yt["thay_the"] == ["youtube-phat"]
    m3 = dict(yt, thay_the=["youtube", "sai id!", "youtube-phat"])
    kiem_manifest(m3)
    assert "thay_the" not in m3  # có mục sai → bỏ cả trường, không chặn nạp
    m4 = dict(yt, thay_the=[f"m{i}" for i in range(9)])
    kiem_manifest(m4)
    assert "thay_the" not in m4  # vượt 8 → bỏ trường


def _man(id_, **them):
    man = {"id": id_, "ten": id_, "mo_ta": "Mô tả", "loai": "tien_ich",
           "tien_to": id_.replace("-", "_"),
           "cung_cap": [], "nhom_con": {"id": "doi-song-va-giai-tri"}}
    man.update(them)
    return man


def _so(catalog, dang_chay):
    from app.so_module import SoModule
    so = SoModule()
    so.catalog = {k: SimpleNamespace(manifest=v, tien_to=v["tien_to"])
                  for k, v in catalog.items()}
    so.dang_chay = {k: object() for k in dang_chay}
    return so


def test_thay_the_an_muc_va_tab_khi_da_cai():
    yt = _man("youtube",
              giao_dien={"khu": "doi_song", "muc": "youtube", "nhan": "YouTube", "chip": []},
              tab_web={"nhan": "YouTube", "trang": "web/index.html"},
              thay_the=["youtube-phat"])
    phat = _man("youtube-phat",
                tab_web={"nhan": "YouTube phát", "trang": "web/index.html"})
    so = _so({"youtube": yt, "youtube-phat": phat}, ["youtube", "youtube-phat"])
    tn = so.tinh_nang()
    muc = {e["id"]: e["giao_dien"] for e in tn["modules"]}
    assert muc["youtube"] is not None and muc["youtube-phat"] is None
    assert [t["module"] for t in tn["tab"]] == ["youtube"]
    # Thay thế đã cài nhưng CHƯA chạy: tab cũ vẫn ẩn (quy tắc «đã cài»).
    so2 = _so({"youtube": yt, "youtube-phat": phat}, ["youtube-phat"])
    tn2 = so2.tinh_nang()
    assert tn2["tab"] == []
    assert {e["id"]: e["giao_dien"] for e in tn2["modules"]}["youtube-phat"] is None
    # Gỡ thay thế: mục/tab cũ hiện lại.
    so3 = _so({"youtube-phat": phat}, ["youtube-phat"])
    tn3 = so3.tinh_nang()
    assert {e["id"]: e["giao_dien"] for e in tn3["modules"]}["youtube-phat"] is not None
    assert [t["module"] for t in tn3["tab"]] == ["youtube-phat"]


def test_doc_viettat_khong_tao_muc_va_manifest_that():
    from app.kham_pha import giao_dien_cua, kiem_manifest
    dv = _man("doc-viettat", giao_dien=False)
    assert giao_dien_cua(dv) is None
    so = _so({"doc-viettat": dv}, [])
    tn = so.tinh_nang()
    assert tn["modules"][0]["giao_dien"] is None
    that_dv = json.loads((GOC / "modules" / "doc-viettat" / "manifest.json").read_text(encoding="utf-8"))
    assert that_dv["giao_dien"] is False and kiem_manifest(dict(that_dv)) is None
    that_yt = json.loads((GOC / "modules" / "youtube" / "manifest.json").read_text(encoding="utf-8"))
    assert that_yt["thay_the"] == ["youtube-phat"] and kiem_manifest(dict(that_yt)) is None


# ── thực thể + hành động ────────────────────────────────────────────

def test_thuc_the_hai_thuc_the_khong_lo_bi_mat(tmp_path):
    _, m = _mod(tmp_path)
    ds = asyncio.run(m.thuc_the())
    assert [e["id"] for e in ds] == ["youtube.xem", "youtube.dieu_khien"]
    assert [e["rui_ro_mac_dinh"] for e in ds] == ["doc", "ghi"]
    for e in ds:
        assert e["ten"].strip() and e["mo_ta"].strip()
        for t in e["tinh_nang"]:
            assert t["hanh_dong"] and t["mo_ta"]
            ls = t["tham_so"]
            assert ls["type"] == "object" and ls["additionalProperties"] is False
            assert set(ls.get("required", [])) <= set(ls["properties"])
    dump = json.dumps(ds, ensure_ascii=False)
    assert "Bearer" not in dump and "tich_hop@" not in dump


def test_hanh_dong_dieu_khien_tron_vong(tmp_path):
    _, m = _mod(tmp_path)
    xem, dk = "youtube.xem", "youtube.dieu_khien"
    du = _th(m, xem, "tim", {"tu_khoa": "test", "nguon": "youtube", "gioi_han": 2})
    assert du["ok"] is True and len(du["ket_qua"]) == 2
    du = _th(m, dk, "phat", {"muc_tieu": VIDEO_1, "nguon": "youtube"})
    assert du["ok"] is True and du["phien"]["item"]["id"] == VIDEO_1
    sid = du["phien"]["session_id"]
    du = _th(m, dk, "tam_dung", {"phien": sid})
    assert du["ok"] is True and du["phien"]["state"] == "paused"
    du = _th(m, dk, "phat", {"phien": sid})
    assert du["ok"] is True and du["phien"]["state"] == "playing"
    du = _th(m, dk, "tua", {"phien": sid, "giay": 30})
    assert du["ok"] is True and du["vi_tri"] == 30
    du = _th(m, dk, "doi_loa", {"phien": sid, "thiet_bi": ["media_player.bep"]})
    assert du["ok"] is True and du["phien"]["outputs"] == ["media_player.bep"]
    du = _th(m, xem, "cac_phien", {})
    assert du["ok"] is True and len(du["cac_phien"]) == 1
    du = _th(m, dk, "dung", {"phien": sid})
    assert du["ok"] is True and du["da_dung"] is True
    du = _th(m, dk, "bai_ke", {"phien": sid})
    assert du["ok"] is False and du["ma_loi"] == "session_not_found"
    du = _th(m, "youtube.khong-co", "trang_thai", {})
    assert du["ok"] is False
    du = _th(m, xem, "phat", {})
    assert du["ok"] is False  # xem không có hành động ghi


def test_hanh_dong_playlist_va_hang_doi(tmp_path):
    _, m = _mod(tmp_path)
    xem, dk = "youtube.xem", "youtube.dieu_khien"
    du = _th(m, dk, "tao_playlist", {"ten": "Nghe sau"})
    assert du["ok"] is True
    pid = du["playlist"]["id"]
    du = _th(m, dk, "them_vao_playlist",
             {"playlist_id": pid, "muc_tieu": VIDEO_1, "nguon": "youtube"})
    assert du["ok"] is True and du["added"] == 1
    du = _th(m, dk, "them_vao_playlist",
             {"playlist_id": pid, "muc_tieu": VIDEO_1, "nguon": "youtube"})
    assert du["added"] == 0  # trùng không thêm nữa
    du = _th(m, xem, "danh_sach_playlist", {})
    assert du["ok"] is True and len(du["danh_sach"]) == 1
    du = _th(m, dk, "phat_playlist", {"playlist_id": pid})
    assert du["ok"] is True and du["phien"]["item"]["id"] == VIDEO_1
    sid = du["phien"]["session_id"]
    du = _th(m, dk, "them_vao_hang_doi",
             {"phien": sid, "muc_tieu": VIDEO_2, "nguon": "youtube"})
    assert du["ok"] is True and du["so_bai"] == 2
    # Hàng đợi có bài trùng: phát từ vị trí 1 phải đúng bài thứ 2, không
    # rơi về lần xuất hiện đầu tiên.
    du = _th(m, dk, "them_vao_hang_doi",
             {"phien": sid, "muc_tieu": VIDEO_1, "nguon": "youtube"})
    assert du["so_bai"] == 3
    du = _th(m, dk, "phat_tu_hang_doi", {"phien": sid, "chi_so": 2})
    assert du["ok"] is True
    assert du["phien"]["queue_index"] == 2 and len(du["phien"]["queue"]["items"]) == 3
    du = _th(m, dk, "xoa_khoi_hang_doi", {"phien": sid, "chi_so": 0})
    assert du["ok"] is True and len(du["hang_doi"]["items"]) == 2
    du = _th(m, dk, "xoa_playlist", {"playlist_id": pid})
    assert du["ok"] is True
    du = _th(m, xem, "danh_sach_playlist", {})
    assert du["danh_sach"] == []


def test_nhap_playlist_qua_thuc_the(tmp_path):
    _, m = _mod(tmp_path)
    dk = "youtube.dieu_khien"
    compact = {"n": "Chia sẻ", "i": [[
        "youtube", VIDEO_1, f"https://www.youtube.com/watch?v={VIDEO_1}",
        "Bài một", "Kênh Bài một", 200,
        f"https://i.ytimg.com/vi/{VIDEO_1}/hqdefault.jpg"]]}
    raw = zlib.compress(json.dumps(compact, ensure_ascii=False,
                                   separators=(",", ":")).encode(), 9)
    code = "TTPL1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    du = _th(m, dk, "nhap_playlist", {"text": code})
    assert du["ok"] is True and du["playlist"]["name"] == "Chia sẻ"
    assert len(du["playlist"]["items"]) == 1
    du = _th(m, dk, "nhap_playlist", {"text": "TTPL1.hong"})
    assert du["ok"] is False and du["ma_loi"] == "invalid_share_code"


def test_endpoint_queue(tmp_path):
    _, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_1}))
    assert ma == 200
    sid = nd["session"]["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/queue", "GET", {"session_id": sid}, None))
    assert ma == 200 and len(nd["queue"]["items"]) == 1
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/queue", "POST", {},
        {"session_id": sid, "action": "add",
         "item": {"source": "youtube", "id": VIDEO_2, "title": "Bài hai"}}))
    assert ma == 200 and len(nd["queue"]["items"]) == 2
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/queue", "POST", {},
        {"session_id": sid, "action": "remove", "index": 0}))
    assert ma == 200 and nd["queue"]["items"][0]["id"] == VIDEO_2
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/queue", "POST", {},
        {"session_id": sid, "action": "remove", "index": 9}))
    assert ma == 400 and nd["error"] == "invalid_queue_index"


# ── trang tab tĩnh ───────────────────────────────────────────────────

def test_trang_tab_du_id_chu_va_khong_dung_inner_html():
    html = TRANG_HTML.read_text(encoding="utf-8")
    js = TRANG_JS.read_text(encoding="utf-8")
    for id_can in ["anh-bia", "chip-nguon", "ten-bai", "kenh-bai", "tren-loa",
                   "thanh-tien-do", "tien-do", "thoi-gian", "nut-truoc",
                   "nut-phat-tam-dung", "nut-sau", "nut-dung", "nut-man-hinh",
                   "ds-nhom", "chip-yt", "chip-zing", "chip-fb", "o-tu-khoa",
                   "nut-tim", "ds-ket-qua", "o-playlist-nhan", "o-nhap-text",
                   "nut-nhap", "ds-playlist", "ds-queue", "ds-thiet-bi",
                   "nd-cai-dat", "dem-playlist", "dem-queue"]:
        assert f'id="{id_can}"' in html, f"thiếu id {id_can} trong trang tab"
    for chu in ["Đang phát", "Nhóm đang phát", "Tìm kiếm", "Playlist",
                "Hàng đợi", "Loa &amp; tivi", "Cài đặt",
                "Nghe khi tắt màn hình", "YouTube", "Zing MP3", "Facebook",
                "Bảo mật"]:
        assert chu in html, f"thiếu chữ {chu!r} trong trang tab"
    for chu in ["Thêm vào Queue", "Thêm vào Playlist", "Phát từ đây"]:
        assert chu in js, f"thiếu nút {chu!r} trong trang.js"
    assert ".innerHTML" not in js  # dữ liệu ngoài chỉ qua textContent
    assert "Bearer" not in html and "Bearer" not in js
    assert "tich_hop@" not in html and "tich_hop@" not in js
    # JS chỉ gọi hành động mà module khai (xem + điều khiển).
    _, m = _mod()
    khai = set()
    for e in asyncio.run(m.thuc_the()):
        khai.update(t["hanh_dong"] for t in e["tinh_nang"])
    goi = set(re.findall(r"hanhDong(?:Doc)?\('([^']+)'", js))
    assert goi <= khai, f"trang.js gọi hành động module không khai: {goi - khai}"
    assert goi, "không tìm thấy lời gọi hành động nào trong trang.js"
