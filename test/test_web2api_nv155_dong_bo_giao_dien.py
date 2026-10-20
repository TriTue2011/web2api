"""NV155 — đồng bộ giữa module công cụ (chuẩn C2) và trang Thiết bị.

Hai lỗi cùng một lớp «giao diện đọc hợp đồng module theo giả định riêng»:

1. Thực thể công cụ khai ``tinh_nang`` là danh sách ĐỐI TƯỢNG ``{hanh_dong, mo_ta, tham_so}``,
   trang đọc như danh sách CHUỖI → nút nhãn rỗng, bấm gửi cả đối tượng.
2. Thực thể mới (hoặc đổi tính năng) bị SEC-004f ép «nguy_hiem» cho tới khi quản trị xác
   nhận, nhưng trang hiện y hệt một công cụ THẬT SỰ nguy hiểm — người dùng không biết đó là
   «chờ xác nhận» chứ không phải kết luận của ai. Lõi cần nói rõ (``cho_xac_nhan``).

Không nới cổng SEC-004f: chưa xác nhận vẫn là ``nguy_hiem`` về hiệu lực.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gia import ghi_thu_muc
from test_web2api_giao_dien_thiet_bi import QT as QT_UI, loi_that_ui
from test_web2api_thiet_bi import QT, moi_truong  # noqa: F401  (fixture)
from ui_chung import CO_CHROME, dang_nhap_quan_tri, trang, trinh_duyet  # noqa: F401

TIEN_TO = "ccui"


def mau_module_cong_cu() -> dict[str, bytes]:
    """Module công cụ đúng khuôn C2: tinh_nang là danh sách đối tượng có lược đồ tham số."""
    manifest = {
        "id": "cong-cu-ui", "ten": "Công cụ UI mẫu", "mo_ta": "Module công cụ cho test",
        "loai": "tien_ich", "hang": "A", "giao_thuc": "cuc_bo", "tien_to": TIEN_TO,
        "cung_cap": ["thiet_bi"], "truong_cau_hinh": [],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma = f'''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
    def nap(self, c): pass
    async def cai(self, c): return {{"ok": True}}
    async def chat(self, b, s=False): return 501, {{}}, b"{{}}"
    async def models(self): return []
    async def suc_khoe(self): return {{"ok": True}}
    async def dung(self): pass
    async def thuc_the(self, loc=None):
        return [
            {{
                "id": f"{{self.tien_to}}.chuan_hoa", "ten": "Chuẩn hoá chữ", "loai": "cong_cu",
                "rui_ro_mac_dinh": "doc",
                "tinh_nang": [
                    {{"hanh_dong": "chuan_hoa_doc", "mo_ta": "Cần văn bản",
                      "tham_so": {{"type": "object", "required": ["van_ban"],
                                  "properties": {{"van_ban": {{"type": "string"}}}}}}}},
                    {{"hanh_dong": "dem_ky_tu", "mo_ta": "Không cần tham số",
                      "tham_so": {{"type": "object", "properties": {{}}}}}},
                ],
            }},
            {{
                "id": f"{{self.tien_to}}.xoa_het", "ten": "Xoá sạch", "loai": "cong_cu",
                "rui_ro_mac_dinh": "nguy_hiem", "tinh_nang": ["xoa"],
            }},
        ]
    async def trang_thai(self, ids):
        return {{f"{{self.tien_to}}.chuan_hoa": {{"gia_tri": "san_sang", "hop_le": True}},
                f"{{self.tien_to}}.xoa_het": {{"gia_tri": "san_sang", "hop_le": True}}}}
    async def thuc_hien(self, id, hanh_dong, tham_so):
        return {{"ok": True}}
'''
    return {"manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            "module.py": ma.encode("utf-8")}


def _cai(client, data_dir):
    ghi_thu_muc(data_dir / "modules" / "cong-cu-ui", mau_module_cong_cu())
    client.post("/api/modules/nap-lai", headers=QT)
    r = client.post("/api/modules/cong-cu-ui/cai", headers=QT, json={"cau_hinh": {}})
    assert r.status_code == 200, r.text


def _muc(client, id_):
    ds = client.get("/api/thiet-bi", headers=QT).json()["thuc_the"]
    return next(e for e in ds if e["id"] == id_)


# ───────────── Lõi: nói rõ «chờ xác nhận» ─────────────

def test_thuc_the_moi_la_cho_xac_nhan_khong_phai_nguy_hiem_that(moi_truong):
    client, data_dir, _ = moi_truong
    _cai(client, data_dir)
    moi = _muc(client, f"{TIEN_TO}.chuan_hoa")
    # Cổng SEC-004f giữ nguyên: chưa xác nhận thì hiệu lực vẫn là nguy_hiem…
    assert moi["rui_ro"] == "nguy_hiem"
    # …nhưng lõi phải nói đó là CHỜ XÁC NHẬN (module khai «doc»), không phải kết luận nguy hiểm.
    assert moi["rui_ro_mac_dinh"] == "doc"
    assert moi["cho_xac_nhan"] is True
    that = _muc(client, f"{TIEN_TO}.xoa_het")
    assert that["rui_ro"] == "nguy_hiem" and that["cho_xac_nhan"] is False


def test_bam_doc_xac_nhan_va_giu_lai(moi_truong):
    client, data_dir, _ = moi_truong
    _cai(client, data_dir)
    r = client.put(f"/api/thiet-bi/{TIEN_TO}.chuan_hoa/rui-ro", headers=QT, json={"rui_ro": "doc"})
    assert r.status_code == 200
    sau = _muc(client, f"{TIEN_TO}.chuan_hoa")
    assert sau["rui_ro"] == "doc" and sau["cho_xac_nhan"] is False
    # Đọc lại một thực thể riêng lẻ cũng phải khớp (cùng một nguyên tắc tính, không hai bản).
    r1 = client.get(f"/api/thiet-bi/{TIEN_TO}.chuan_hoa", headers=QT)
    assert r1.status_code == 200
    mot = r1.json().get("thuc_the", r1.json())
    assert mot["rui_ro"] == "doc" and mot["cho_xac_nhan"] is False


def test_xac_nhan_nguy_hiem_that_khong_nhan_cho_xac_nhan(moi_truong):
    client, data_dir, _ = moi_truong
    _cai(client, data_dir)
    client.put(f"/api/thiet-bi/{TIEN_TO}.xoa_het/rui-ro", headers=QT, json={"rui_ro": "nguy_hiem"})
    assert _muc(client, f"{TIEN_TO}.xoa_het")["cho_xac_nhan"] is False


# ───────────── Giao diện (Chrome thật) ─────────────

chrome = pytest.mark.skipif(not CO_CHROME, reason="cần Chrome")


def _mo_trang(trang, w, tmp_cho):
    httpx.post(f"{w.url}/api/modules/cong-cu-ui/cai", json={"cau_hinh": {}}, headers=QT_UI)
    dang_nhap_quan_tri(trang, w.url)
    trang.di(f"{w.url}/He-thong-Thiet-bi.dc.html")
    trang.cho("document.querySelector('.tb-dau')", mo_ta=tmp_cho)


JS_THE = """(ten) => { const d = [...document.querySelectorAll('.tb-dau')]
  .find((e) => e.querySelector('.tb-ten').textContent.trim() === ten); return d ? d.parentElement : null; }"""


@chrome
def test_ui_nut_hanh_dong_co_nhan_dung_va_khoa_khi_can_tham_so(trang, tmp_path, monkeypatch):
    mods = {"cong-cu-ui": mau_module_cong_cu()}
    with loi_that_ui(tmp_path, monkeypatch, modules=mods) as w:
        _mo_trang(trang, w, "thẻ thiết bị")
        nut = trang.js(f"""(() => {{ const the = ({JS_THE})('Chuẩn hoá chữ');
          return [...the.querySelectorAll('.tb-hanh-dong button')].map((b) => [b.textContent.trim(), b.disabled]); }})()""")
        assert nut == [["chuan_hoa_doc", True], ["dem_ky_tu", False]], nut
        cu = trang.js(f"""(() => {{ const the = ({JS_THE})('Xoá sạch');
          return [...the.querySelectorAll('.tb-hanh-dong button')].map((b) => [b.textContent.trim(), b.disabled]); }})()""")
        assert cu == [["xoa", False]], cu  # khai kiểu chuỗi cũ vẫn chạy


@chrome
def test_ui_cho_xac_nhan_khac_nguy_hiem_that_va_bam_doc_giu_lai(trang, tmp_path, monkeypatch):
    mods = {"cong-cu-ui": mau_module_cong_cu()}
    with loi_that_ui(tmp_path, monkeypatch, modules=mods) as w:
        _mo_trang(trang, w, "thẻ thiết bị")
        nhan = lambda ten: trang.js(f"""(() => {{ const the = ({JS_THE})('{ten}');
          return the.querySelector('.tb-dau > span').textContent.trim(); }})()""")
        assert "xác nhận" in nhan("Chuẩn hoá chữ").lower(), nhan("Chuẩn hoá chữ")
        assert nhan("Xoá sạch") == "Nguy hiểm"
        # Giải thích cho người dùng có mặt trên thẻ chờ xác nhận.
        goi_y = trang.js(f"""(() => {{ const the = ({JS_THE})('Chuẩn hoá chữ');
          return the.textContent; }})()""")
        assert "Đọc" in goi_y and "module khai" in goi_y.lower()
        trang.js(f"""(() => {{ const the = ({JS_THE})('Chuẩn hoá chữ');
          [...the.querySelectorAll('.tb-rui-ro button')].find((b) => b.textContent.trim() === 'Đọc').click(); }})()""")
        trang.cho(f"({JS_THE})('Chuẩn hoá chữ').querySelector('.tb-dau > span').textContent.trim() === 'Đọc'",
                  mo_ta="nhãn Đọc sau khi xác nhận")
        trang.di(f"{w.url}/He-thong-Thiet-bi.dc.html")  # tải lại: phải còn nguyên
        trang.cho("document.querySelector('.tb-dau')", mo_ta="tải lại")
        assert nhan("Chuẩn hoá chữ") == "Đọc"


# ───────────── Hướng dẫn khoá dịch vụ phụ khớp với dịch vụ thật ─────────────

import re
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent
# module → thư mục dịch vụ phụ của nó (đúng ba module có khoá do NGƯỜI DÙNG tự đặt cho dịch vụ phụ)
DICH_VU_PHU = {"van-phong": "van-phong-office", "vnc-xa": "trinh-duyet", "antigravity": "antigravity-cau-noi"}


@pytest.mark.parametrize("mod,dv", sorted(DICH_VU_PHU.items()))
def test_huong_dan_khoa_noi_ro_tu_dat_va_dung_ten_bien_cua_dich_vu(mod, dv):
    m = json.loads((GOC / "modules" / mod / "manifest.json").read_text(encoding="utf-8"))
    truong = next(t for t in m["truong_cau_hinh"] if t.get("kieu") == "bi_mat" and t.get("bat_buoc"))
    mo_ta = truong["mo_ta"]
    assert "TỰ ĐẶT" in mo_ta and "không ai cấp" in mo_ta, f"{mod}: chưa nói khoá do người dùng tự đặt"
    assert "openssl rand" in mo_ta, f"{mod}: chưa chỉ cách sinh khoá"
    ten_bien = re.findall(r"\b[A-Z][A-Z0-9_]*KHOA\b", mo_ta)
    assert ten_bien, f"{mod}: chưa nêu tên biến môi trường của dịch vụ"
    ma_dich_vu = "\n".join(p.read_text(encoding="utf-8") for p in (GOC / "dich-vu" / dv).glob("*") if p.suffix in {".py", ".md"})
    for b in ten_bien:
        assert b in ma_dich_vu, f"{mod}: biến {b} không có trong dich-vu/{dv} — manifest lệch dịch vụ"


# ───────────── «Sau khi cài tốn thêm bao nhiêu»: số ĐO THẬT trên đĩa ─────────────

def test_catalog_tra_dung_luong_tren_dia_dung_tung_byte(moi_truong):
    client, data_dir, _ = moi_truong
    _cai(client, data_dir)
    # Tự cộng độc lập từ các tệp đã ghi; __pycache__ (nếu Python đã sinh) không được tính.
    mong = sum(len(b) for b in mau_module_cong_cu().values())
    ds = client.get("/api/modules/catalog", headers=QT).json()["modules"]
    km = next(m for m in ds if m["id"] == "cong-cu-ui")
    assert km["dung_luong_dia"] == mong


@chrome
def test_ui_bang_sau_khi_cai_hien_dung_luong_do_that_va_goi_co_san(trang, tmp_path, monkeypatch):
    mod = mau_module_cong_cu()
    man = json.loads(mod["manifest.json"])
    man["yeu_cau_goi"] = ["httpx"]  # gói có sẵn trong ảnh
    mod["manifest.json"] = json.dumps(man, ensure_ascii=False).encode("utf-8")
    with loi_that_ui(tmp_path, monkeypatch, modules={"cong-cu-ui": mod}) as w:
        httpx.post(f"{w.url}/api/modules/cong-cu-ui/cai", json={"cau_hinh": {}}, headers=QT_UI)
        dang_nhap_quan_tri(trang, w.url)
        trang.di(w.url + "/He-thong.dc.html")
        sel = "document.querySelector('[data-o=\"cong-cu-ui\"]')"
        trang.cho(sel, han=15, mo_ta="ô module")
        trang.bam(sel)
        trang.cho("document.getElementById('can-gi')", mo_ta="bảng chi tiết")
        bang = trang.chu("#can-gi")
        assert "sau khi cài" in trang.chu("#h-can-gi").lower()
        assert "(đo trên đĩa)" in bang and "Dung lượng mã (đã cài)" in bang
        assert "Đĩa tăng sau khi cài" in bang and "chưa đo" in bang  # đĩa khi chạy chưa khai → nói thật
        assert "có sẵn trong lõi" in bang and "httpx" in bang
