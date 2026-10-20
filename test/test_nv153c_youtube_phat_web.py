"""Test NV153 đợt C — giao diện web cho module youtube-phat.

Viết TRƯỚC mã (test-first): lần chạy đầu ĐỎ vì module chưa có hành động nào
và chưa có web/.

Phạm vi đợt C (đọc từ viec-nv153-bo-sung-c.txt):
- Module khai `tab_web` → tab «YouTube phát» chỉ hiện khi module đã cài.
- Module mở 7 hành động qua chuẩn C1 `thiet_bi`/`hanh-dong` (lõi đã có cơ chế,
  không sửa lõi): tim, phat_len_loa, dung_phien, trang_thai,
  danh_sach_playlist, nhap_ma_chia_se, lay_khoa. Mọi lời gọi dịch vụ đi bằng
  khoá phía server, không lộ khoá ra trình duyệt (trừ `lay_khoa` khi quản trị
  bấm «Chép token» có chủ đích).
- Giao diện tĩnh theo khung (khung.css), không innerHTML với dữ liệu ngoài,
  escape mọi dữ liệu hiển thị.
- ẨN (không nút chết) và ghi báo cáo: «Nghe trên máy này» (CSP media-src của
  trang module chặn luồng khác origin), «Video nhúng» (CSP frame-src chặn
  youtube.com), «Bài kế» (dịch vụ đợt A không có điểm cuối next).

Hợp đồng lõi liên quan (đọc từ mã, không đoán):
- app/kham_pha.py: `tab_web` cần {"nhan": str, "trang": "web/<tệp>"} (không «..»);
  `ModuleKhamPha.duong_tab()` → "/m/<id>/<tệp>".
- app/tuyen.py `tep_cua_module`: GET /m/<id>/<tệp> 404 khi module chưa cài hoặc
  không khai tab_web.
- app/thiet_bi.py `thuc_hien_hanh_dong_loi`: kiểm tham số theo lược đồ
  (SEC-004a), thực thể đổi `tinh_nang` → rủi ro hiệu lực `nguy_hiem` tới khi
  quản trị xác nhận (SEC-004f) — giao diện phải báo 409 đúng cách.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

from app.kham_pha import ModuleKhamPha, kiem_manifest

GOC = pathlib.Path(__file__).resolve().parent.parent
THU_MUC_MODULE = GOC / "modules" / "youtube-phat"
MANIFEST_PATH = THU_MUC_MODULE / "manifest.json"
MODULE_PY = THU_MUC_MODULE / "module.py"
WEB_DIR = THU_MUC_MODULE / "web"

KHOA_GIA = "khoa-gia-test-youtube-phat-0123456789abcdef"
URL_DICH_VU = "http://127.0.0.1:8713"

HANH_DONG_MONG_DOI = {
    "tim", "phat_len_loa", "dung_phien", "trang_thai",
    "danh_sach_playlist", "nhap_ma_chia_se", "lay_khoa",
}


def tai_module():
    ten = "mod_youtube_phat_nv153c_test"
    spec = importlib.util.spec_from_file_location(ten, MODULE_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ten] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(ten, None)
        raise
    return mod


@pytest.fixture()
def mod():
    assert MODULE_PY.is_file(), "chưa có modules/youtube-phat/module.py"
    return tai_module()


@pytest.fixture()
def manifest():
    assert MANIFEST_PATH.is_file(), "chưa có modules/youtube-phat/manifest.json"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def the(mod, manifest):
    return mod.Module(manifest)


def nap_gia(the):
    the.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": KHOA_GIA})


# ── máy chủ dịch vụ giả ───────────────────────────────────────────────────

class _PhanHoiGia:
    def __init__(self, ma=200, than=None):
        self.status_code = ma
        self._than = than if than is not None else {"success": True}

    def json(self):
        if isinstance(self._than, Exception):
            raise self._than
        return self._than


def _client_gia(monkeypatch, mod, phan_hoi=None, loi=None, goi=None):
    goi = goi if goi is not None else {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, params=None, json=None, headers=None):
            goi["method"] = method
            goi["url"] = url
            goi["params"] = params
            goi["json"] = json
            goi["headers"] = headers
            if loi is not None:
                raise loi
            return phan_hoi

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)
    return goi


# ── manifest + tab ────────────────────────────────────────────────────────

def test_manifest_hop_le_va_co_tab_web(manifest):
    assert kiem_manifest(manifest) is None, kiem_manifest(manifest)
    tab = manifest.get("tab_web")
    assert isinstance(tab, dict), "đợt C: manifest phải khai tab_web"
    assert isinstance(tab.get("nhan"), str) and tab["nhan"].strip()
    assert tab.get("trang") == "web/index.html"


def test_duong_tab_tro_ve_trang_module(manifest):
    kp = ModuleKhamPha(id="youtube-phat", nguon="du_lieu", thu_muc=THU_MUC_MODULE,
                       manifest=manifest, lop=None, van_tay="test", ten_ma="test")
    tab = kp.duong_tab()
    assert tab["duong"] == "/m/youtube-phat/index.html"
    assert tab["nhan"]


def test_web_files_ton_tai_va_dung_khung():
    for ten in ("index.html", "trang.css", "trang.js"):
        assert (WEB_DIR / ten).is_file(), f"thiếu modules/youtube-phat/web/{ten}"
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert "../../khung.css" in html
    assert "<html" in html and 'lang="vi"' in html


# ── thực thể + lược đồ hành động ──────────────────────────────────────────

def test_thuc_the_khai_du_7_hanh_dong_va_rui_ro_ghi(the):
    nap_gia(the)
    ents = asyncio.run(the.thuc_the(None))
    assert len(ents) == 1
    ent = ents[0]
    assert ent["id"] == "youtube_phat.dieu_khien"
    assert ent["rui_ro_mac_dinh"] == "ghi"
    tn = ent["tinh_nang"]
    ten_hd = {t["hanh_dong"] for t in tn}
    assert ten_hd == HANH_DONG_MONG_DOI, ten_hd
    for t in tn:
        assert isinstance(t.get("mo_ta"), str) and t["mo_ta"].strip()
        ls = t.get("tham_so")
        assert isinstance(ls, dict) and ls.get("type") == "object"
        assert isinstance(ls.get("properties"), dict)
    # khoá che, không lộ
    assert KHOA_GIA not in json.dumps(ent, ensure_ascii=False)
    assert ent["thuoc_tinh"]["khoa"].endswith("cdef")


def test_thuc_hien_hanh_dong_la_bi_tu_choi(the):
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien("youtube_phat.dieu_khien", "xoa_sach", {}))
    assert kq["ok"] is False


# ── proxy dịch vụ ─────────────────────────────────────────────────────────

TIM_TRA = {"success": True, "source": "youtube", "total": 2, "items": [
    {"id": "dQw4w9WgXcQ", "title": "Bài <b>độc</b>", "channel": "Kênh & Co",
     "duration": 212, "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
     "source": "youtube"},
    {"id": "abc123", "title": "Bài 2", "channel": "", "duration": None,
     "url": "https://www.youtube.com/watch?v=abc123", "source": "youtube"},
]}


def test_tim_goi_dich_vu_va_tra_ket_qua_sach(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, TIM_TRA))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "tim",
        {"tu_khoa": "nhạc trẻ", "nguon": "youtube", "gioi_han": 10}))
    assert kq["ok"] is True
    assert len(kq["ket_qua"]) == 2
    assert kq["ket_qua"][0]["ma_bai"] == "dQw4w9WgXcQ"
    assert goi["method"] == "GET"
    assert goi["url"] == URL_DICH_VU + "/api/integration/search"
    assert goi["params"]["q"] == "nhạc trẻ"
    assert goi["headers"]["Authorization"] == f"Bearer {KHOA_GIA}"
    assert KHOA_GIA not in json.dumps(kq, ensure_ascii=False)


def test_tim_mac_dinh_nguon_youtube(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, TIM_TRA))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "tim", {"tu_khoa": "x"}))
    assert kq["ok"] is True
    assert goi["params"]["source"] == "youtube"


def test_loi_dich_vu_tra_dict_sach_khong_nem_khong_lo_khoa(monkeypatch, mod, the):
    import httpx
    _client_gia(monkeypatch, mod, loi=httpx.ConnectError("khong noi duoc"))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "tim", {"tu_khoa": "x"}))
    assert kq["ok"] is False
    assert "chưa kết nối được" in kq["loi"] or "không nối được" in kq["loi"]
    assert KHOA_GIA not in kq["loi"]


def test_dich_vu_401_bao_khoa_sai_sach(monkeypatch, mod, the):
    _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(401, {"error": "sai"}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "trang_thai", {}))
    assert kq["ok"] is False
    assert "401" in kq["loi"]
    assert KHOA_GIA not in kq["loi"]


def test_phat_len_loa_goi_session_dung_tham_so(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {
        "success": True, "session": {"session_id": "s1", "state": "playing"}}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "phat_len_loa",
        {"nguon": "youtube", "muc_tieu": "dQw4w9WgXcQ",
         "thiet_bi": ["media_player.phong_khach"]}))
    assert kq["ok"] is True
    assert goi["url"] == URL_DICH_VU + "/api/integration/session"
    assert goi["json"]["source"] == "youtube"
    assert goi["json"]["target"] == "dQw4w9WgXcQ"
    assert goi["json"]["output_entity_ids"] == ["media_player.phong_khach"]


def test_phat_len_loa_khong_thiet_bi_bao_loi_sach(the):
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "phat_len_loa",
        {"nguon": "youtube", "muc_tieu": "dQw4w9WgXcQ", "thiet_bi": []}))
    assert kq["ok"] is False
    assert "thiết bị" in kq["loi"] or "loa" in kq["loi"]


def test_dung_phien_tu_lay_trang_thai_khi_khong_dua_phien(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {
        "success": True, "session": {"session_id": "s9", "state": "playing"}}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "dung_phien", {}))
    assert kq["ok"] is True
    # lần gọi đầu là status để lấy session_id
    assert goi["url"] in (URL_DICH_VU + "/api/integration/status",
                         URL_DICH_VU + "/api/integration/stop")


def test_dung_phien_co_phien_goi_thang_stop(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {
        "success": True, "stopped": True,
        "session": {"session_id": "s1", "state": "idle"},
        "state": "idle"}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "dung_phien", {"phien": "s1"}))
    assert kq["ok"] is True
    assert goi["url"] == URL_DICH_VU + "/api/integration/stop"
    assert goi["json"]["session_id"] == "s1"


def test_danh_sach_playlist(monkeypatch, mod, the):
    _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {
        "success": True, "playlists": [{"id": "abcdef012345", "name": "Nhạc Việt"}]}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "danh_sach_playlist", {}))
    assert kq["ok"] is True
    assert kq["danh_sach"][0]["id"] == "abcdef012345"


def test_nhap_ma_chia_se_sai_khuon_bao_loi_truoc_khi_goi(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {"success": True}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "nhap_ma_chia_se", {"ma": "xyz"}))
    assert kq["ok"] is False
    assert "TTPL1." in kq["loi"]
    assert "url" not in goi  # chưa hề gọi dịch vụ


def test_nhap_ma_chia_se_dung_goi_import(monkeypatch, mod, the):
    goi = _client_gia(monkeypatch, mod, phan_hoi=_PhanHoiGia(200, {
        "success": True, "playlist": {"id": "abcdef012345"}}))
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "nhap_ma_chia_se", {"ma": "TTPL1.abcd"}))
    assert kq["ok"] is True
    assert goi["url"] == URL_DICH_VU + "/api/integration/playlists"
    assert goi["json"]["action"] == "import"


def test_lay_khoa_tra_day_du_khi_chu_dich_bam(monkeypatch, mod, the):
    _client_gia(monkeypatch, mod)  # không cần gọi mạng
    nap_gia(the)
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "lay_khoa", {}))
    assert kq["ok"] is True
    assert kq["khoa"] == KHOA_GIA


def test_lay_khoa_khi_chua_cau_hinh_bao_loi(the):
    the.nap({"dich_vu_url": URL_DICH_VU, "khoa_dich_vu": ""})
    kq = asyncio.run(the.thuc_hien(
        "youtube_phat.dieu_khien", "lay_khoa", {}))
    assert kq["ok"] is False


# ── JavaScript: không innerHTML, escape đúng ───────────────────────────────

def _js():
    p = WEB_DIR / "trang.js"
    assert p.is_file(), "chưa có modules/youtube-phat/web/trang.js"
    return p.read_text(encoding="utf-8")


def test_js_khong_dung_innerHTML():
    assert "innerHTML" not in _js(), "dữ liệu ngoài không được qua innerHTML"


def test_js_khong_dung_document_write_va_eval():
    js = _js()
    assert "document.write" not in js
    assert re.search(r"(?<![\w$])eval\s*\(", js) is None


def test_js_co_ham_escape_va_dung(tmp_path):
    js = _js()
    m = re.search(r"function esc\([\s\S]*?\n\}", js)
    assert m, "trang.js phải có hàm esc() thuần để escape HTML"
    harness = tmp_path / "esc_test.js"
    harness.write_text(
        m.group(0) + "\n"
        "const ca = ["
        "  ['<img src=x onerror=alert(1)>', '&lt;img src=x onerror=alert(1)&gt;'],"
        "  ['Kênh & Co', 'Kênh &amp; Co'],"
        "  ['\"nháy\"', '&quot;nháy&quot;'],"
        "  [\"it's\", 'it&#39;s'],"
        "  ['a/b', 'a/b'],"
        "  ['', ''],"
        "  [null, ''],"
        "];\n"
        "let loi = 0;\n"
        "for (const [vao, ra] of ca) {"
        "  const kq = esc(vao);"
        "  if (kq !== ra) { console.error('SAI:', JSON.stringify(vao), '→', JSON.stringify(kq), 'muốn', JSON.stringify(ra)); loi++; }"
        "}\n"
        "if (loi) process.exit(1);\n"
        "console.log('esc OK');\n",
        encoding="utf-8")
    node = shutil.which("node")
    assert node, "cần node để kiểm hàm escape"
    r = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr + r.stdout


def test_js_khong_luu_token_vao_localStorage():
    js = _js()
    assert "localStorage" not in js, "giao diện không được lưu token vào localStorage"


def test_html_muc_thu_gon_mac_dinh_dong():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # mục thu gọn theo quy ước: nút có data-mo-muc, aria-expanded=false
    assert "data-mo-muc" in html
    assert 'aria-expanded="false"' in html


# ── tài liệu đồng bộ ──────────────────────────────────────────────────────

def test_readme_module_ghi_tab_va_hanh_dong():
    readme = (THU_MUC_MODULE / "README.md").read_text(encoding="utf-8")
    assert "tab_web" in readme or "YouTube phát" in readme
    assert "phat_len_loa" in readme


def test_tham_chieu_co_tieu_muc_dot_c():
    tham_chieu = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")
    assert "youtube-phat" in tham_chieu
    assert "đợt C" in tham_chieu or "Đợt C" in tham_chieu
