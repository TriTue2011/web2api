"""Test module youtube (NV277) — máy phát chạy thẳng trong web2api.

Chạy async qua asyncio.run (kho chưa có pytest-asyncio, theo khuôn
test_module_youtube_phat.py). Không Internet thật: trich_xuat và nguồn
thượng nguồn đều chích giả; conftest chung đã chặn đường ra ngoài.

Kiểm:
- manifest hợp lệ qua app.kham_pha.kiem_manifest (None = hợp lệ);
  id youtube, tien_to youtube, khai duong_module, không có trường cấu hình (NV289).
- nap đồng bộ, cai (thiếu khoá → ok False; có khoá → ok True + che),
  suc_khoe (chưa cấu hình → ok False).
- tich_hop: health/search (giả)/status/history/stop/session/playlists
  (create/add/list)/outputs/stream/play/seek; search sai source → 400;
  target youtube sai → 400; session không tồn tại → 404.
- luong: vé giả → 404; Range lặp lại cho cùng vé → cùng byte, 206;
  rơ-le nối lại từ đúng byte khi nguồn đứt giữa chừng (1 lần) và ngắt
  sau 5 lần (LoiNguonKhongCuuDuoc, không lấp rác); dừng là dừng hẳn
  (stop/dung xoá vé, iterator đang chạy dừng, không request thêm).
- Độc lập: module chỉ import app.duong_module + app.tu_xa trong app.*;
  "youtube" không xuất hiện trong app/ hay web/ (lõi không mang tên
  nhà cung cấp/module cụ thể — khuôn youtube-phat).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys

import pytest

from app.kham_pha import kiem_manifest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube"
MANIFEST_PATH = THU_MUC_MODULE / "manifest.json"
MODULE_PY = THU_MUC_MODULE / "module.py"

KHOA_GIA = "khoa-gia-test-youtube-nv277-0123456789abcdef"
VIDEO_ID = "dQw4w9WgXcQ"


def tai_module():
    ten = "mod_youtube_test"
    if ten in sys.modules:
        del sys.modules[ten]
    spec = importlib.util.spec_from_file_location(
        ten, MODULE_PY, submodule_search_locations=[str(THU_MUC_MODULE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    spec.loader.exec_module(mod)
    return mod


class TrichXuatGia:
    def __init__(self):
        self.goi_giai = []

    async def tim_kiem(self, source, query, limit):
        return [{"id": VIDEO_ID, "title": "Bài giả", "artists": [],
                 "duration": 213, "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                 "source": source}]

    async def giai_am_thanh(self, source, target):
        self.goi_giai.append((source, target))
        return {"url": "fake://am-thanh-1", "title": "Bài giả",
                "duration": 213, "source": source}


class BanGia:
    """Bản thượng nguồn giả: headers + aiter_bytes, có thể đứt giữa chừng."""

    def __init__(self, du_lieu: bytes, *, status_code=206, dut_o=None):
        self.status_code = status_code
        self.headers = {"content-range": f"bytes 0-{len(du_lieu) - 1}/{len(du_lieu)}",
                        "content-length": str(len(du_lieu))}
        self._du_lieu = du_lieu
        self._dut_o = dut_o  # số byte đã gửi thì ném (None = không đứt)
        self.da_dong = False

    async def aiter_bytes(self):
        da_gui = 0
        for i in range(0, len(self._du_lieu), 5):
            khuc = self._du_lieu[i:i + 5]
            if self._dut_o is not None and da_gui + len(khuc) > self._dut_o:
                # Gửi tới đúng điểm đứt rồi ném.
                con = self._dut_o - da_gui
                if con > 0:
                    yield self._du_lieu[i:i + con]
                raise ConnectionError("nguồn giả đứt giữa chừng")
            yield khuc
            da_gui += len(khuc)

    async def aclose(self):
        self.da_dong = True


class NguonGia:
    """mo_nguon giả có trạng thái: mỗi lần mở theo Range, lần đầu có thể đứt."""

    def __init__(self, du_lieu: bytes, *, dut_lan_dau=False, luon_dut=False):
        self.du_lieu = du_lieu
        self.dut_lan_dau = dut_lan_dau
        self.luon_dut = luon_dut
        self.lan_mo = []  # các Range đã mở

    async def __call__(self, url, headers):
        import re
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", headers.get("Range", ""))
        dau = int(m.group(1)) if m else 0
        self.lan_mo.append(dau)
        phan = self.du_lieu[dau:]
        if self.luon_dut:
            return BanGia(phan, dut_o=0)
        if self.dut_lan_dau and len(self.lan_mo) == 1:
            return BanGia(phan, dut_o=7)
        ban = BanGia(phan)
        ban.headers = {"content-range": f"bytes {dau}-{len(self.du_lieu) - 1}/{len(self.du_lieu)}"}
        return ban


def _mod(tmp_path=None):
    import tempfile
    mod = tai_module()
    m = mod.Module({}, _thu_muc_du_lieu=tmp_path or tempfile.mkdtemp(prefix="yt-test-"))
    m.nap({})
    m.trich_xuat = TrichXuatGia()
    return mod, m


def test_manifest_hop_le():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert kiem_manifest(dict(manifest)) is None, kiem_manifest(dict(manifest))
    assert manifest["id"] == "youtube"
    assert manifest["tien_to"] == "youtube"
    # NV289: không có bí mật nhập tay; khoá do lõi tự tạo (phạm vi tich_hop@youtube).
    assert manifest["duong_module"] == {"lan_chi": True}
    assert manifest["truong_cau_hinh"] == []


def test_nap_cai_suc_khoe_khong_can_khoa():
    """NV289: module chạy ngay khi cài, không đọc bí mật nào (khoá nằm ở lõi)."""
    mod, m = _mod()
    ket = asyncio.run(m.cai({}))
    assert ket["ok"] is True and ket["cau_hinh_luu"] == {}
    assert asyncio.run(m.suc_khoe())["ok"] is True
    assert not hasattr(m, "khoa_tich_hop")
    assert "khoa_tich_hop" not in MODULE_PY.read_text(encoding="utf-8")


def test_tich_hop_health_search_status():
    mod, m = _mod()
    ma, _, nd = asyncio.run(m.tich_hop("integration/health", "GET", {}, None))
    assert ma == 200 and nd["status"] == "ok"
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "test", "source": "youtube", "limit": 5}, None))
    assert ma == 200 and nd["results"][0]["id"] == VIDEO_ID
    ma, _, nd = asyncio.run(m.tich_hop("integration/search", "GET",
                                       {"q": "test", "source": "la"}, None))
    assert ma == 400
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert ma == 200 and nd["session"] is None


def test_tich_hop_session_play_stop_history():
    mod, m = _mod()
    ma, _, nd = asyncio.run(m.tich_hop(
        "integration/session", "POST", {},
        {"source": "youtube", "target": VIDEO_ID}))
    assert ma == 201
    sid = nd["session"]["session_id"]
    ma, _, nd = asyncio.run(m.tich_hop("integration/play", "POST", {},
                                       {"session_id": sid}))
    assert ma == 200 and nd["session"]["status"] == "playing"
    ma, _, nd = asyncio.run(m.tich_hop("integration/history", "GET", {}, None))
    assert ma == 200 and len(nd["history"]) == 1
    ma, _, nd = asyncio.run(m.tich_hop("integration/stop", "POST", {}, {}))
    assert ma == 200 and nd["stopped"] is True
    ma, _, nd = asyncio.run(m.tich_hop("integration/status", "GET", {}, None))
    assert ma == 200 and nd["session"] is None
    ma, _, _ = asyncio.run(m.tich_hop("integration/play", "POST", {},
                                      {"session_id": "khong-co"}))
    assert ma == 404
    ma, _, _ = asyncio.run(m.tich_hop("integration/session", "POST", {},
                                      {"source": "youtube", "target": "khong-phai-id"}))
    assert ma == 400


def test_tich_hop_playlists_va_stream(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    mod, m = _mod(tmp_path)
    ma, _, nd = asyncio.run(m.tich_hop("integration/playlists", "POST", {},
                                       {"action": "create", "name": "Nghe sau"}))
    assert ma == 201
    pid = nd["playlist"]["id"]
    ma, _, nd = asyncio.run(m.tich_hop("integration/playlists", "POST", {},
                                       {"action": "add", "playlist_id": pid,
                                        "item": {"id": VIDEO_ID, "title": "Bài giả"}}))
    assert ma == 200 and len(nd["playlist"]["items"]) == 1
    ma, _, nd = asyncio.run(m.tich_hop("integration/stream", "POST", {},
                                       {"source": "youtube", "target": VIDEO_ID},
                                       "http://192.0.2.10:3031"))
    assert ma == 200 and nd["expires_in"] == 3600
    # Tích hợp HA bắt buộc URL tuyệt đối (scheme + host): loa tải thẳng URL này.
    assert nd["stream_url"].startswith("http://192.0.2.10:3031/m/youtube/api/stream/")
    assert "_ky=" in nd["stream_url"] and "_het_han=" in nd["stream_url"]
    # Danh sách phát bền: thể hiện mới đọc lại được từ đĩa.
    _, m2 = _mod(tmp_path)
    assert [p["name"] for p in m2._playlists] == ["Nghe sau"]
    assert len(m2._playlists[0]["items"]) == 1


def test_luong_ve_gia_va_range_lap_lai():
    mod, m = _mod()
    du_lieu = b"0123456789abcdef" * 4
    ve = "ve-gia-test"
    m._luong[ve] = {"bytes": du_lieu, "het_han": 9999999999}
    ma, _, nd = asyncio.run(m.luong("ve-khong-co", None, "GET"))
    assert ma == 404
    ma, headers, nd = asyncio.run(m.luong(ve, "bytes=10-19", "GET"))
    assert ma == 206 and nd == du_lieu[10:20]
    assert headers["Content-Range"] == f"bytes 10-19/{len(du_lieu)}"
    ma, _, nd2 = asyncio.run(m.luong(ve, "bytes=10-19", "GET"))
    assert ma == 206 and nd2 == nd  # Range lặp lại trong hạn vé vẫn chạy
    ma, _, _ = asyncio.run(m.luong(ve, "bytes=zzz", "GET"))
    assert ma == 416


def test_luong_noi_lai_dung_byte_va_ngat_sau_5_lan():
    mod, m = _mod()
    du_lieu = bytes(range(256)) * 2  # 512 byte
    nguon = NguonGia(du_lieu, dut_lan_dau=True)
    m.mo_nguon = nguon
    m._luong["ve-1"] = {"url": "fake://am-thanh-1", "source": "youtube",
                        "het_han": 9999999999, "video_id": VIDEO_ID}
    ma, _, it = asyncio.run(m.luong("ve-1", None, "GET"))
    assert ma == 200
    got = b""

    async def _doc():
        nonlocal got
        async for khuc in it:
            got += khuc
    asyncio.run(_doc())
    assert got == du_lieu  # nối lại từ đúng byte, không thiếu/không lặp
    assert nguon.lan_mo[0] == 0 and nguon.lan_mo[1] == 7  # mở lại đúng offset đứt

    # Luôn đứt → sau 5 lần thì ngắt, không lấp rác.
    nguon2 = NguonGia(du_lieu, luon_dut=True)
    m.mo_nguon = nguon2
    m._luong["ve-2"] = {"url": "fake://am-thanh-1", "source": "youtube",
                        "het_han": 9999999999, "video_id": VIDEO_ID}
    ma, _, it2 = asyncio.run(m.luong("ve-2", None, "GET"))
    with pytest.raises(mod.LoiNguonKhongCuuDuoc):
        async def _doc2():
            async for _ in it2:
                pass
        asyncio.run(_doc2())
    assert len(nguon2.lan_mo) == 5


def test_dung_la_dung_han():
    mod, m = _mod()
    m._luong["ve-1"] = {"bytes": b"0123456789", "het_han": 9999999999}
    asyncio.run(m.tich_hop("integration/session", "POST", {},
                           {"source": "youtube", "target": VIDEO_ID}))
    asyncio.run(m.dung())
    assert m._luong == {} and m._phien == {}
    ma, _, _ = asyncio.run(m.luong("ve-1", None, "GET"))
    assert ma == 404  # vé cũ vô hiệu sau khi dừng


def test_doc_lap_loi_module():
    import ast
    tree = ast.parse(MODULE_PY.read_text(encoding="utf-8"))
    app_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            app_imports.add(node.module)
    assert app_imports <= {"app.duong_module", "app.tu_xa", "app.kho"}, app_imports  # kho: chỉ để lấy thư mục dữ liệu
    for thu_muc in (GOC / "app", GOC / "web"):
        for p in thu_muc.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".html", ".js", ".css"):
                text = p.read_text(encoding="utf-8", errors="ignore")
                assert "youtube" not in text, f"{p} chứa tên module youtube"


def test_tuy_chon_yt_dlp_khong_phu_thuoc_thu_muc_nha(monkeypatch, tmp_path):
    """Container chạy uid 1000 không có $HOME ghi được: bộ nhớ đệm yt-dlp/deno nằm trong thư mục tạm,
    chỉ một video (không danh sách phát), có hạn thời gian; chỉ đường thẳng tới deno nếu có."""
    import sys
    mod = tai_module()
    tx = sys.modules[mod.TrichXuat.__module__]
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("DENO_DIR", raising=False)
    monkeypatch.setattr(tx.tempfile, "tempdir", None)
    monkeypatch.setattr(tx, "_deno_path", lambda: "/opt/deno")
    opts = tx._tuy_chon_chung()
    assert opts["noplaylist"] is True and opts["socket_timeout"] == 20
    assert opts["cachedir"].startswith(str(tmp_path))
    assert opts["js_runtimes"] == {"deno": {"path": "/opt/deno"}}
    assert tx.os.environ["DENO_DIR"].startswith(str(tmp_path))
    monkeypatch.setattr(tx, "_deno_path", lambda: "")
    assert "js_runtimes" not in tx._tuy_chon_chung()
