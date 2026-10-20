"""NV199b — PRESET nhà cung cấp AI + ô «Thêm nhà cung cấp» (5 nhà chỉ cần dữ liệu).

ĐỎ→XANH. Nhà cung cấp GIẢ (không Internet thật); các ca kiểm qua lõi thật
(TestClient). Test Chrome thật: môi trường này KHÔNG có Chrome → mọi ca UI
đều là kiểm TĨNH cấu trúc .dc.html + hợp đồng JS, đánh dấu rõ «chưa chạy Chrome».

Lược đồ tệp preset (app/tu_xa_preset.json), phiên bản 1:
  {phien_ban_luoc_do: 1,
   preset: [{id, ten, mo_ta_ngan, base_url_mac_dinh,
             kieu_xac_thuc: "bearer" | "bearer_khong_bat_buoc",
             truong: [{khoa, nhan, bat_buoc, bi_mat: true,
                       mo_ta (LẤY Ở ĐÂU), lien_ket (https://)}],
             id_goi_y, ten_goi_y, tien_to_goi_y,
             mo_hinh_goi_y: [...], ghi_chu_gioi_han}]}
Quy tắc an toàn: tệp preset là DỮ LIỆU, không chứa khoá/URL riêng nhà chủ máy;
khoá người dùng nhập phải bi_mat=true kèm mo_ta và lien_ket chính thức.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import so_module
from app.main import app
from gia import MayChuThat

KHOA_QT = "khoa-quan-tri-nv199b-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_XA = "khoa-xa-gia-nv199b-abcdef0123456789"
DUONG_JSON = Path(__file__).resolve().parent.parent / "app" / "tu_xa_preset.json"
ID_5_NHA = {"custom_openai", "agnes", "nvidia_nim", "tokenrouter", "ninerouter"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    monkeypatch.setattr(so_module, "_PRESET_TU_XA_DEM", None)
    return tmp_path


def doc_json():
    return json.loads(DUONG_JSON.read_text(encoding="utf-8"))


# ── 1. lược đồ tệp preset ─────────────────────────────────────────────────

def test_luoc_do_phien_ban_va_5_nha():
    dl = doc_json()
    assert dl["phien_ban_luoc_do"] == 1
    assert {p["id"] for p in dl["preset"]} == ID_5_NHA


def test_luoc_do_moi_preset_du_truong():
    for p in doc_json()["preset"]:
        for k in ("id", "ten", "mo_ta_ngan", "base_url_mac_dinh", "kieu_xac_thuc",
                  "truong", "id_goi_y", "ten_goi_y", "tien_to_goi_y",
                  "mo_hinh_goi_y", "ghi_chu_gioi_han"):
            assert k in p, f"{p.get('id')}: thiếu trường {k}"
        assert p["kieu_xac_thuc"] in ("bearer", "bearer_khong_bat_buoc")
        assert isinstance(p["truong"], list) and p["truong"]
        assert isinstance(p["mo_hinh_goi_y"], list)
        bu = p["base_url_mac_dinh"]
        assert bu == "" or re.match(r"^https?://[^/]+", bu), f"{p['id']}: base_url lạ {bu!r}"
        assert re.match(r"^[a-z0-9][a-z0-9_-]{1,40}$", p["id_goi_y"])
        assert re.match(r"^[a-z0-9][a-z0-9._-]{0,40}$", p["tien_to_goi_y"])


def test_luoc_do_truong_khoa_bi_mat_va_mo_ta():
    """Khoá = bi_mat + mo_ta LẤY Ở ĐÂU + liên kết chính thức (quy tắc NV199b)."""
    for p in doc_json()["preset"]:
        for t in p["truong"]:
            assert t["bi_mat"] is True, f"{p['id']}: trường {t['khoa']} phải bi_mat"
            assert isinstance(t["mo_ta"], str) and len(t["mo_ta"]) >= 20, \
                f"{p['id']}: mo_ta phải nói rõ LẤY Ở ĐÂU"
            assert isinstance(t["lien_ket"], str) and t["lien_ket"].startswith("https://"), \
                f"{p['id']}: lien_ket phải là URL chính thức https://"


def test_luoc_do_khong_chua_khoa_that():
    """Tệp preset là dữ liệu công khai — không được chứa khoá thật hay giá trị khoá.
    («192.168.1.20» trong ghi chú ninerouter là VÍ DỤ tài liệu, giống placeholder
    sẵn có «http://192.168.1.20:8000/v1» của giao diện — không phải dữ liệu nhà chủ máy.)"""
    raw = DUONG_JSON.read_text(encoding="utf-8")
    for mau in ("sk-", "sk-ant-", "nvapi-", "xai-", "gsk_"):
        assert mau not in raw, f"tệp preset chứa dấu hiệu khoá thật: {mau}"
    for p in doc_json()["preset"]:
        for t in p["truong"]:
            assert t.get("gia_tri_mac_dinh") in (None, ""), \
                f"{p['id']}: không được đặt giá trị khoá mặc định"


def test_luoc_do_ninerouter_canh_bao_loopback():
    p = next(x for x in doc_json()["preset"] if x["id"] == "ninerouter")
    gh = p["ghi_chu_gioi_han"]
    assert "localhost" in gh and "SSRF" in gh
    assert "192.168" in gh or "LAN" in gh


def test_luoc_do_agnes_chi_chat():
    p = next(x for x in doc_json()["preset"] if x["id"] == "agnes")
    assert "CHỈ" in p["ghi_chu_gioi_han"] and "chat" in p["ghi_chu_gioi_han"]


def test_doc_preset_loc_muc_hong(monkeypatch, tmp_path):
    """doc_preset_tu_xa phải lọc mục hỏng (id không phải chuỗi) thay vì làm rơi trang."""
    import app.so_module as sm
    dl = doc_json()
    dl["preset"].append({"id": 123, "ten": "hong"})
    f = tmp_path / "preset_hong.json"
    f.write_text(json.dumps(dl), encoding="utf-8")
    monkeypatch.setattr(sm, "_DUONG_DAN_PRESET_TU_XA", f)
    monkeypatch.setattr(sm, "_PRESET_TU_XA_DEM", None)
    assert {p["id"] for p in sm.doc_preset_tu_xa()} == ID_5_NHA


# ── 2. đường GET preset ───────────────────────────────────────────────────

def test_get_preset_qua_loi_that(du_lieu):
    with TestClient(app) as c:
        r = c.get("/api/modules/tu-xa/preset", headers=QT)
        assert r.status_code == 200, r.text
        ds = r.json()["preset"]
        assert {p["id"] for p in ds} == ID_5_NHA
        for p in ds:
            assert p["base_url_mac_dinh"] is not None
            assert all(t["bi_mat"] is True for t in p["truong"])
        assert "sk-" not in r.text  # không bí mật nào lọt ra


def test_get_preset_khong_khoa_bi_chan(du_lieu):
    with TestClient(app) as c:
        assert c.get("/api/modules/tu-xa/preset").status_code == 401


# ── 3. đường POST kiem-tra ────────────────────────────────────────────────

class GiaTheoMa:
    """Máy chủ giả trả mã HTTP cố định cho GET /v1/models và /models."""

    def __init__(self, ma, than=None):
        self.ma = ma
        self.than = than
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/models", self.models),
        ])

    async def models(self, req):
        return JSONResponse(self.than or {"error": {"message": "gia"}}, status_code=self.ma)


def test_kiem_tra_khoa_sai_401(du_lieu):
    with MayChuThat(GiaTheoMa(401).app):
        pass
    g = GiaTheoMa(401)
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": s.url + "/v1", "api_key": KHOA_XA}, headers=QT)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is False and j["loai"] == "khoa"
        assert KHOA_XA not in r.text  # khoá không lọt vào phản hồi


def test_kiem_tra_het_han_muc_429(du_lieu):
    g = GiaTheoMa(429)
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": s.url + "/v1", "api_key": KHOA_XA}, headers=QT)
        assert r.json()["loai"] == "han_muc"
        assert KHOA_XA not in r.text


def test_kiem_tra_ok_va_mo_hinh_co(du_lieu):
    g = GiaTheoMa(200, {"object": "list", "data": [{"id": "mo-a"}, {"id": "mo-b"}]})
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": s.url + "/v1", "api_key": KHOA_XA, "model": "mo-a"},
                   headers=QT)
        j = r.json()
        assert j["ok"] is True and j["mo_hinh_co"] is True and j["so_model"] == 2
        assert j["do_tre_ms"] is not None


def test_kiem_tra_mo_hinh_khong_co(du_lieu):
    g = GiaTheoMa(200, {"object": "list", "data": [{"id": "mo-a"}]})
    with MayChuThat(g.app) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": s.url + "/v1", "api_key": KHOA_XA, "model": "mo-khong-co"},
                   headers=QT)
        j = r.json()
        assert j["ok"] is False and j["loai"] == "mo_hinh"
        assert "mo-khong-co" in j["thong_diep"]


def test_kiem_tra_dia_chi_khong_ket_noi_duoc(du_lieu):
    with TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": "http://127.0.0.1:1/v1", "api_key": KHOA_XA}, headers=QT)
        j = r.json()
        assert j["ok"] is False and j["loai"] == "dia_chi"


def test_kiem_tra_ssrf_loopback_bi_chan(monkeypatch, tmp_path):
    """Không có WEB2API_TEST_CHO_PHEP_LOOPBACK → localhost bị chặn ngay ở kiểm URL."""
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", raising=False)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with TestClient(app) as c:
        r = c.post("/api/modules/tu-xa/kiem-tra",
                   json={"base_url": "http://localhost:20128/v1"}, headers=QT)
        assert r.status_code == 400, r.text
        assert "loopback" in r.text


def test_kiem_tra_khong_khoa_401(du_lieu):
    g = GiaTheoMa(200, {"object": "list", "data": []})
    with MayChuThat(g.app) as s, TestClient(app) as c:
        assert c.post("/api/modules/tu-xa/kiem-tra",
                      json={"base_url": s.url + "/v1"}).status_code == 401


def test_kiem_tra_than_sai_400(du_lieu):
    with TestClient(app) as c:
        assert c.post("/api/modules/tu-xa/kiem-tra", json=["khong-phai-doi-tuong"],
                      headers=QT).status_code == 400
        assert c.post("/api/modules/tu-xa/kiem-tra", json={"api_key": 123},
                      headers=QT).status_code == 400


# ── 4. luồng preset → đăng ký → dùng được trong chat và combo ─────────────

class GiaNhaChuan:
    def __init__(self):
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.chat, methods=["POST"]),
        ])

    async def models(self, req):
        return JSONResponse({"object": "list", "data": [{"id": "mo-chuan", "object": "model"}]})

    async def chat(self, req):
        than = await req.json()
        return JSONResponse({"object": "chat.completion", "model": than["model"],
                             "choices": [{"index": 0,
                                          "message": {"role": "assistant", "content": "ok"},
                                          "finish_reason": "stop"}]})


def test_luong_preset_dang_ky_dung_duoc(du_lieu):
    """Lấy preset nvidia_nim → đăng ký qua lõi thật → hiện trong danh sách,
    trong /v1/models (combo) và chat được."""
    g = GiaNhaChuan()
    with MayChuThat(g.app) as s, TestClient(app) as c:
        pr = c.get("/api/modules/tu-xa/preset", headers=QT).json()["preset"]
        nv = next(p for p in pr if p["id"] == "nvidia_nim")
        r = c.post("/api/modules/tu-xa", json={
            "id": nv["id_goi_y"], "ten": nv["ten_goi_y"], "tien_to": nv["tien_to_goi_y"],
            "base_url": s.url + "/v1", "api_key": KHOA_XA}, headers=QT)
        assert r.status_code == 200, r.text
        ds = c.get("/api/modules", headers=QT).json()["modules"]
        assert any(m.get("id") == "nvidia" and m.get("loai") == "tu_xa" for m in ds)
        models = c.get("/v1/models", headers=QT).json()["data"]
        assert "nv/mo-chuan" in [m["id"] for m in models]
        r2 = c.post("/v1/chat/completions", json={
            "model": "nv/mo-chuan",
            "messages": [{"role": "user", "content": "xin chao"}]}, headers=QT)
        assert r2.status_code == 200, r2.text
        assert r2.json()["choices"][0]["message"]["content"] == "ok"


# ── 5. UI tĩnh (chưa chạy Chrome thật) ─────────────────────────────────────

def test_ui_co_o_them_nha_cung_cap_va_nut_kiem_tra():
    """CHƯA chạy Chrome thật — kiểm tĩnh cấu trúc .dc.html. Điều phối giao agy chạy Chrome."""
    html = (Path(__file__).resolve().parent.parent / "web" / "He-thong.dc.html").read_text(
        encoding="utf-8")
    assert 'id="tx-preset"' in html
    assert "Thêm nhà cung cấp" in html
    assert "Tuỳ chỉnh tương thích OpenAI" in html
    assert "{{txKiemTra}}" in html and "{{nhanKtTx}}" in html
    assert "api/modules/tu-xa/preset" in html
    assert "api/modules/tu-xa/kiem-tra" in html
    assert "{{txGhiChu}}" in html and "{{txMoTaKhoa}}" in html
    # Ẩn khi lõi chưa có đường preset:
    assert "lõi cũ chưa có đường này" in html


def test_ui_khong_de_khoa_lot_vao_thong_bao():
    """Sau «Kiểm tra»/đăng ký, ô khoá bị xoá khỏi trang (mẫu đã có ở themTuXa)."""
    html = (Path(__file__).resolve().parent.parent / "web" / "He-thong.dc.html").read_text(
        encoding="utf-8")
    assert html.count("txKhoa: ''") >= 2
