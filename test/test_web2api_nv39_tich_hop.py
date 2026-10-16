"""Giao diện Tích hợp & công cụ (web/He-thong-Tich-hop.dc.html) + kiểm quyền các đường tích hợp.

Tuân thủ khuôn test_web2api_combo_giao_dien.py (NV38):
- Test tĩnh (không cần Chrome): 4 khối thu gọn ĐÓNG mặc định, nút thật <button aria-expanded
  aria-controls>, không innerHTML, trang mở được khi gõ địa chỉ trực tiếp, chữ tiếng Việt.
- Test API: leo thang quyền (không khoá 401, khoá khách 403) cho /api/cong-cu, /api/tim-kiem/nguon,
  POST /api/tim-kiem, POST /api/tim-kiem/muc-luc, POST /api/modules/nap-lai, POST /api/thiet-bi/su-kien;
  sự kiện thử: thực thể không có → 404, loai sai → 400; không bí mật nào lộ qua API/trang.
- Test Chrome (tự bỏ qua khi thiếu): kết quả tìm kiếm chứa <script>/<img onerror> không thực thi.

LƯU Ý: Hộp cát không có Chrome nên các test Chrome sẽ tự động bỏ qua (skipped) nhờ CO_CHROME.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.so_module import SoModule
from ui_chung import CO_CHROME  # noqa: F401 — dùng cho test Chrome

KHOA_QT = "khoa-quan-tri-test-tich-hop-0123456789"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

GOC = Path(__file__).resolve().parents[1]
TRANG = GOC / "web" / "He-thong-Tich-hop.dc.html"
HTML = TRANG.read_text(encoding="utf-8")


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    with TestClient(app) as client:
        so = SoModule()
        client.app.state.so = so
        asyncio.run(so.khoi_dong())
        yield client, data_dir


def khoa_khach_moi(client, pham_vi) -> dict:
    r = client.post("/api/khoa-khach", headers=QT,
                    json={"nhan": "Khách thử tích hợp", "pham_vi": pham_vi})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['khoa']['khoa']}"}


# ───────────────────────────── tĩnh: cấu trúc trang ─────────────────────────────

def test_trang_ton_tai_va_co_tieu_de():
    assert TRANG.exists()
    assert "<title>web2api — Hệ thống · Tích hợp &amp; công cụ</title>" in HTML
    assert 'src="./khung.js"' in HTML and 'src="./support.js"' in HTML


def test_du_bon_khoi_thu_gon_dong_mac_dinh():
    # Mỗi khối: nút thật <button> với aria-expanded + aria-controls, nội dung bọc trong sc-if.
    for nut, khoi in [("nut-cong-cu", "khoi-cong-cu"), ("nut-tim-kiem", "khoi-tim-kiem"),
                      ("nut-nap-lai", "khoi-nap-lai"), ("nut-wh", "khoi-wh")]:
        m = re.search(r'<button type="button"[^>]*id="%s"[^>]*>' % nut, HTML)
        assert m, f"thiếu nút {nut}"
        assert 'aria-expanded="{{' in m.group(0), f"{nut} thiếu aria-expanded"
        assert f'aria-controls="{khoi}"' in m.group(0), f"{nut} thiếu aria-controls"
    for khoi, bien in [("khoi-cong-cu", "ccMo"), ("khoi-tim-kiem", "tkMo"),
                       ("khoi-nap-lai", "nlMo"), ("khoi-wh", "whMo")]:
        assert f'<sc-if value="{{{{{bien}}}}}"><div id="{khoi}"' in HTML, f"khối {khoi} phải bọc sc-if {bien}"
    # Trạng thái khởi tạo: cả bốn khối đóng.
    script = HTML.split('data-dc-script')[1]
    for bien in ["ccMo: false", "tkMo: false", "nlMo: false", "whMo: false"]:
        assert bien in script, f"{bien} phải false mặc định"


def test_khong_innerHTML_va_khong_su_kien_chua_du_lieu():
    script = HTML.split('data-dc-script')[1]
    assert "innerHTML" not in script
    assert "document.write" not in script
    # Thuộc tính sự kiện on* chỉ mang tên hàm/biến, không nội suy dữ liệu API.
    for m in re.finditer(r'on[A-Z][a-zA-Z]*="([^"]*)"', HTML):
        assert "{{" not in m.group(1) or re.fullmatch(r"\{\{[a-zA-Z0-9_.]+\}\}", m.group(1)), \
            f"sự kiện chứa nội suy phức tạp: {m.group(0)[:80]}"


def test_chu_tieng_viet_va_dieu_huong():
    for chu in ["Công cụ cho AI", "Tìm kiếm", "Nạp lại module", "Webhook &amp; gửi ra",
                "Gửi sự kiện thử", "dữ liệu không tin được"]:
        assert chu in HTML, f"thiếu chữ tiếng Việt: {chu}"
    # Thanh điều hướng Hệ thống có mặt để đi tới các trang khác bằng địa chỉ.
    assert 'aria-label="Điều hướng Hệ thống"' in HTML
    assert "{{navHT}}" in HTML


def test_cac_duong_api_trang_goi_la_dung():
    script = HTML.split('data-dc-script')[1]
    for duong in ["api/cong-cu", "api/tim-kiem/nguon", "api/tim-kiem",
                  "api/tim-kiem/muc-luc", "api/modules/nap-lai",
                  "api/thiet-bi/su-kien", "api/modules/catalog"]:
        assert f"'{duong}'" in script or f'"{duong}"' in script, f"trang chưa gọi {duong}"


# ───────────────────────────── trang tĩnh qua HTTP ─────────────────────────────

def test_trang_mo_truc_tiep_quyen(moi_truong):
    client, _ = moi_truong
    r = client.get("/He-thong-Tich-hop.dc.html", headers=QT)
    assert r.status_code == 200, r.text
    assert "Tích hợp &amp; công cụ" in r.text
    # Trang tĩnh phục vụ 200 cho mọi người; trang tự kiểm phiên bằng JS
    # (K.napToi -> K.chuyenDangNhap khi chưa đăng nhập), không phải bằng HTTP 401.
    script = HTML.split('data-dc-script')[1]
    assert "K.napToi()" in script and "K.chuyenDangNhap()" in script
    r_khong_khoa = client.get("/He-thong-Tich-hop.dc.html")
    assert r_khong_khoa.status_code == 200
    # Khoá khách bị middleware chặn ở mọi .dc.html -> 403.
    khach = khoa_khach_moi(client, ["chat"])
    assert client.get("/He-thong-Tich-hop.dc.html", headers=khach).status_code == 403


def test_chong_bam_doi():
    # Nút Nạp lại, Tìm thử, Lấy mục lục, Gửi sự kiện thử đều có chốt chống bấm đôi.
    script = HTML.split('data-dc-script')[1]
    assert "if (this.state.nlDangGoi) return;" in script
    assert "if (this.state.tkDangTim) return;" in script
    assert "if (this.state.tkMlDangTai) return;" in script
    assert "if (this.state.skDangGui) return;" in script


def test_ket_qua_tim_kiem_bi_cat_ngan():
    # Kết quả tìm kiếm là dữ liệu không tin được: mỗi trường cắt ở 500 ký tự (TRAN_TRUONG).
    script = HTML.split('data-dc-script')[1]
    assert "TRAN_TRUONG = 500" in script
    assert "s.slice(0, TRAN_TRUONG)" in script


def test_so_tran_tan_suat_dung():
    # Số mặc định phải khớp mã nguồn (app/webhook.py, app/gui_ra.py), không đoán.
    assert "20 tin/phút" in HTML
    assert "60 tin/phút" in HTML
    assert "30 tin/phút" not in HTML and "30/phút" not in HTML
    assert "(mặc định 20)" not in HTML and "(mặc định 10)" not in HTML
    assert "WEB2API_GUI_TIN_PHUT" in HTML and "WEB2API_GUI_DONG_THOI" in HTML
    assert "WEB2API_WEBHOOK_TIN_PHUT" in HTML and "WEB2API_WEBHOOK_DONG_THOI" in HTML


def test_wh_dung_catalog_va_an_webhook():
    # Khối (d) dùng catalog (có cung_cap) để biết module nào khai kenh_ra;
    # khai báo webhook không có đường đọc nào trả về -> trang không liệt kê, chỉ ghi chú.
    script = HTML.split('data-dc-script')[1]
    assert "api/modules/catalog" in script
    assert "cc.indexOf('kenh_ra')" in script, "phải suy kenh_ra từ cung_cap của catalog"
    assert "không liệt kê ở đây" in HTML or "chưa cho biết module nào đã khai webhook" in HTML
    # Không bao giờ hiện bí mật: không có chỗ nào đọc trường bi_mat/chữ ký từ API.
    assert "bi_mat" not in HTML


def test_muc_luc_xac_nhan_hai_buoc():
    script = HTML.split('data-dc-script')[1]
    assert "tkMlXacNhan" in script
    assert "Bấm lần nữa để xác nhận" in script
    # Lần bấm đầu chỉ lên cò, không gọi API.
    m = re.search(r"async tkMucLucSubmit\(e\) \{(.*?)\n  \}", script, re.S)
    assert m, "thiếu tkMucLucSubmit"
    than = m.group(1)
    assert "if (!this.state.tkMlXacNhan)" in than
    assert than.index("tkMlXacNhan: true") < than.index("api/tim-kiem/muc-luc")


def test_trang_khong_lo_bi_mat(moi_truong):
    client, _ = moi_truong
    r = client.get("/He-thong-Tich-hop.dc.html", headers=QT)
    assert KHOA_QT not in r.text
    assert "khoa-quan-tri-test" not in r.text


# ───────────────────────────── leo thang quyền ─────────────────────────────

def test_khong_khoa_401(moi_truong):
    client, _ = moi_truong
    assert client.get("/api/tim-kiem/nguon").status_code == 401
    assert client.get("/api/cong-cu").status_code == 401
    assert client.post("/api/tim-kiem", json={"chuoi": "x"}).status_code == 401
    assert client.post("/api/tim-kiem/muc-luc", json={"nguon": "x", "tai_lieu": "y"}).status_code == 401
    assert client.post("/api/modules/nap-lai").status_code == 401
    assert client.post("/api/thiet-bi/su-kien", json={"id": "x"}).status_code == 401


def test_khoa_khach_403_duong_quan_tri(moi_truong):
    client, _ = moi_truong
    khach = khoa_khach_moi(client, ["chat"])
    assert client.get("/api/tim-kiem/nguon", headers=khach).status_code == 403
    assert client.post("/api/modules/nap-lai", headers=khach).status_code == 403


def test_khoa_khach_thieu_pham_vi_403(moi_truong):
    client, _ = moi_truong
    khach = khoa_khach_moi(client, ["chat"])
    # /api/cong-cu, /api/tim-kiem, /api/thiet-bi/su-kien cho khoá khách qua nhưng đòi phạm vi.
    assert client.get("/api/cong-cu", headers=khach).status_code == 403
    assert client.post("/api/tim-kiem", headers=khach, json={"chuoi": "x"}).status_code == 403
    assert client.post("/api/thiet-bi/su-kien", headers=khach, json={"id": "x"}).status_code == 403


def test_quan_tri_200_va_hinh_dang(moi_truong):
    client, _ = moi_truong
    r = client.get("/api/tim-kiem/nguon", headers=QT)
    assert r.status_code == 200 and isinstance(r.json()["nguon"], list)
    r = client.get("/api/cong-cu", headers=QT)
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "list" and isinstance(j["data"], list)
    # Tool không mang bí mật: chỉ có type/function/x_web2api.
    for t in j["data"]:
        assert set(t) <= {"type", "function", "x_web2api"}, set(t)
    r = client.post("/api/modules/nap-lai", headers=QT)
    assert r.status_code == 200


def test_catalog_cho_biet_kenh_ra(moi_truong):
    # GET /api/modules/catalog (QT) trả manifest gồm cung_cap -> trang suy ra module nào khai kenh_ra.
    client, _ = moi_truong
    r = client.get("/api/modules/catalog", headers=QT)
    assert r.status_code == 200, r.text
    mods = r.json()["modules"]
    assert isinstance(mods, list)
    for m in mods:
        assert "cung_cap" in m, f"catalog thiếu cung_cap cho {m.get('id')}"
    # Khoá khách -> 403 (đường quản trị).
    khach = khoa_khach_moi(client, ["chat"])
    assert client.get("/api/modules/catalog", headers=khach).status_code == 403
    assert client.get("/api/modules/catalog").status_code == 401


def test_su_kien_thu_kiem_dau_vao(moi_truong):
    client, _ = moi_truong
    # Thực thể không tồn tại → 404, không phát sự kiện.
    r = client.post("/api/thiet-bi/su-kien", headers=QT,
                    json={"id": "thuc-the-khong-co-abc", "loai": "trang_thai"})
    assert r.status_code == 404
    # loai sai mẫu → 400.
    r = client.post("/api/thiet-bi/su-kien", headers=QT,
                    json={"id": "x", "loai": "<script>"})
    assert r.status_code == 400
    # Thiếu id → 400.
    r = client.post("/api/thiet-bi/su-kien", headers=QT, json={"loai": "trang_thai"})
    assert r.status_code == 400


def test_muc_luc_kiem_dau_vao(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "", "tai_lieu": "x"})
    assert r.status_code == 400
    r = client.post("/api/tim-kiem/muc-luc", headers=QT, json={"nguon": "nguon-khong-co", "tai_lieu": "x"})
    assert r.status_code == 404


def test_tim_kiem_khong_module_khong_sap(moi_truong):
    client, _ = moi_truong
    r = client.post("/api/tim-kiem", headers=QT, json={"chuoi": "xin chào", "so_luong": 5})
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert isinstance(r.json().get("muc"), list)


# ───────────────────────────── Chrome (tự bỏ qua khi thiếu) ─────────────────────────────

import httpx
import json as _json

from gia import MayChuThat, ghi_thu_muc
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401


def mau_module_tich_hop() -> dict[str, bytes]:
    """Module giả: khai tim_kiem + kenh_ra, trả kết quả tìm kiếm chứa chuỗi độc XSS."""
    manifest = {
        "id": "mod-tich-hop",
        "ten": "Module Tích hợp giả",
        "mo_ta": "Module gia lap cho test NV39",
        "loai": "tri_thuc",
        "giao_thuc": "cuc_bo",
        "tien_to": "mth",
        "cung_cap": ["tim_kiem", "kenh_ra"],
        "so_do_tim_kiem": [],
        "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False,
                       "mang_ra": [], "ghi_chu": ""},
    }
    ma = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {"ok": True}

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass

    async def tim_kiem(self, yc: dict):
        return {"muc": [
            {"tieu_de": "<script>window.__xss_chay=true</script>",
             "noi_dung": "<img src=x onerror=window.__xss_chay=true>",
             "nguon": "mod-tich-hop"},
        ], "tong": 1}

    async def muc_luc(self, yc: dict):
        return {"muc": [{"tieu_de": "Chương 1", "con": [{"tieu_de": "Mục 1.1"}]}]}

    async def gui_tin(self, dich: dict, noi_dung: dict):
        return {"ok": True, "kenh": dich.get("kenh_id")}

    def kenh_ra(self):
        return [{"id": "kenh-a", "ten": "Kênh A"}]
'''
    return {
        "manifest.json": _json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        "module.py": ma.encode("utf-8"),
    }


@pytest.fixture
def loi_that_ui_chrome(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEB2API_DATA", str(data_dir))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    ghi_thu_muc(data_dir / "modules" / "mod-tich-hop", mau_module_tich_hop())
    with MayChuThat(app, lifespan="on") as w:
        r = httpx.post(f"{w.url}/api/modules/mod-tich-hop/cai",
                       json={"cau_hinh": {}}, headers=QT)
        assert r.status_code == 200, r.text
        yield w


def _mo_trang(trang, w):
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-Tich-hop.dc.html")


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_chrome_bon_khoi_dong_mac_dinh(trang, loi_that_ui_chrome):
    """Cả 4 khối đóng mặc định; bấm tiêu đề thì mở, aria-expanded đổi."""
    _mo_trang(trang, loi_that_ui_chrome)
    for khoi in ["#khoi-cong-cu", "#khoi-tim-kiem", "#khoi-nap-lai", "#khoi-wh"]:
        assert trang.js(f"document.querySelector('{khoi}') === null"), f"{khoi} phải ẩn lúc đầu"
    trang.bam("document.getElementById('nut-tim-kiem')", mo_ta="mở khối Tìm kiếm")
    trang.cho("document.querySelector('#khoi-tim-kiem')", mo_ta="khối Tìm kiếm hiện")
    assert trang.js("document.getElementById('nut-tim-kiem').getAttribute('aria-expanded')") == "true"


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_chrome_tim_kiem_xss_khong_thuc_thi(trang, loi_that_ui_chrome):
    """Kết quả tìm kiếm chứa <script>/<img onerror> hiện dạng chữ thuần, không thực thi."""
    _mo_trang(trang, loi_that_ui_chrome)
    trang.bam("document.getElementById('nut-tim-kiem')", mo_ta="mở khối Tìm kiếm")
    trang.cho("document.querySelector('#o-chuoi-tim')", mo_ta="ô tìm kiếm hiện")
    trang.go("#o-chuoi-tim", "thu nghiem xss")
    trang.bam("document.getElementById('nut-tim-thu')", mo_ta="bấm Tìm thử")
    trang.cho("document.querySelector('[data-ket-qua]')", mo_ta="kết quả hiện")
    assert trang.js("window.__xss_chay === undefined"), "XSS đã thực thi!"
    # Không có phần tử script/img nào được tạo trong vùng kết quả.
    assert trang.js("document.querySelector('[data-ket-qua]').querySelectorAll('script,img').length") == 0
    # Chuỗi độc vẫn đọc được dạng chữ.
    chu = trang.js("document.querySelector('[data-ket-qua]').innerText")
    assert "window.__xss_chay" in chu


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_chrome_muc_luc_xac_nhan_hai_buoc(trang, loi_that_ui_chrome):
    """Nút Lấy mục lục: bấm lần đầu chỉ lên cò, bấm lần hai mới gọi API."""
    _mo_trang(trang, loi_that_ui_chrome)
    trang.bam("document.getElementById('nut-tim-kiem')", mo_ta="mở khối Tìm kiếm")
    trang.cho("document.querySelector('#o-ml-nguon option')", mo_ta="có nguồn tri thức")
    trang.go("#o-ml-tai-lieu", "tai-lieu-thu")
    trang.bam("document.getElementById('nut-ml-lay')", mo_ta="bấm Lấy mục lục lần 1")
    trang.cho("document.getElementById('nut-ml-lay').innerText.includes('xác nhận')",
              mo_ta="nút chuyển sang chế độ xác nhận")
    assert trang.js("document.querySelectorAll('ul.cay li').length") == 0, "chưa bấm lần 2 mà đã có cây"
    trang.bam("document.getElementById('nut-ml-lay')", mo_ta="bấm Lấy mục lục lần 2")
    trang.cho("document.querySelector('ul.cay li')", mo_ta="cây mục lục hiện")
    chu = trang.js("document.querySelector('ul.cay').innerText")
    assert "Chương 1" in chu and "Mục 1.1" in chu


@pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")
def test_chrome_wh_hien_kenh_ra_va_khong_lo_bi_mat(trang, loi_that_ui_chrome):
    """Khối (d): module khai kenh_ra hiện /api/gui/{id}; khoá quản trị không lọt vào DOM."""
    _mo_trang(trang, loi_that_ui_chrome)
    for nut in ["nut-cong-cu", "nut-tim-kiem", "nut-nap-lai", "nut-wh"]:
        trang.bam(f"document.getElementById('{nut}')", mo_ta=f"mở {nut}")
    trang.cho("document.querySelector('#khoi-wh table.bang')", mo_ta="bảng cổng hiện")
    html = trang.js("document.documentElement.outerHTML")
    assert "/api/gui/mod-tich-hop" in html
    assert KHOA_QT not in html
    assert "khoa-quan-tri-test" not in html
