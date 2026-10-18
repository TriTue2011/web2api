"""Kiểm chứng tính độc lập của lõi web2api và module:
1. Lõi chạy đúng khi không có module nào.
2. Thêm/gỡ module giữa chừng không sửa mã lõi (nạp lại nóng).
3. Không có tên nhà cung cấp trong mã lõi và giao diện (chú thích ≤ trần 17 dòng).
4. Mã mở: loại lạ và khả năng lạ không gây lỗi, được đếm nguyên văn, giao diện không bị chặn.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import PHIEN_BAN, app

GOC = Path(__file__).resolve().parents[1]
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
TRAN_HIEN_TAI = 0

#: Mảnh tên module là từ thông dụng của giao thức, KHÔNG phải tên nhà cung cấp —
#: không tính khi quét app//web/. «assistant» là vai tin nhắn của chuẩn OpenAI, đã có
#: trong app/tuyen.py và web/Studio.dc.html từ trước NV118 (module home-assistant mới
#: sinh ra mảnh này khi test tách id theo dấu «-»). Tên đầy đủ «home-assistant» vẫn bị quét.
#: «phong» là từ tiếng Việt thông dụng («phong cách»), đã có trong web/Studio.dc.html và
#: web/khung.css từ trước NV138 (module van-phong mới sinh ra mảnh này khi test tách id
#: theo dấu «-»). Tên đầy đủ «van-phong» vẫn bị quét.
MANH_TU_THONG_DUNG = {"assistant", "phong"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def _danh_sach_nha_cung_cap() -> list[str]:
    """Tên nhà cung cấp/module cụ thể: lấy từ modules/ cộng thêm các tên phổ biến."""
    danh_sach = set()
    thu_muc_modules = GOC / "modules"
    if thu_muc_modules.is_dir():
        for d in os.listdir(thu_muc_modules):
            if (thu_muc_modules / d).is_dir():
                danh_sach.add(d.lower())
                for p in d.lower().split("-"):
                    if len(p) >= 4 and p not in {"api", "apis"} and p not in MANH_TU_THONG_DUNG:
                        danh_sach.add(p)
    danh_sach |= {"gemini", "google", "openai-chat", "muse", "meta"}
    return sorted(danh_sach)


def _kiem_tra_dong_khop(dong: str, ten_ncc: list[str]) -> list[str]:
    """Kiểm tra dòng có chứa tên nhà cung cấp, bỏ qua liên kết phông và thẻ HTML <meta>."""
    if "fonts.googleapis" in dong or "gstatic" in dong:
        return []
    dong_low = dong.lower()
    khop = []
    for t in ten_ncc:
        if t == "meta":
            # Thẻ HTML <meta>, thuộc tính phím metaKey, biến .meta, meta:, và metadata mạng đám mây không phải nhà cung cấp AI
            if "<meta" in dong_low or "metakey" in dong_low or ".meta" in dong_low or "metadata" in dong_low or "meta:" in dong_low:
                continue
            if re.search(r"\bmeta\b", dong_low):
                khop.append(t)
        elif t in dong_low:
            khop.append(t)
    return khop


def _chu_trong_ma_python(p: Path) -> list[str]:
    """Trích xuất chuỗi hằng (trừ docstring) và các định danh trong mã thực thi."""
    cay = ast.parse(p.read_text(encoding="utf-8"))
    docstring = set()
    for nut in ast.walk(cay):
        if isinstance(nut, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nut.body:
            dau = nut.body[0]
            if isinstance(dau, ast.Expr) and isinstance(dau.value, ast.Constant) and isinstance(dau.value.value, str):
                docstring.add(id(dau.value))
    ra = []
    for nut in ast.walk(cay):
        if isinstance(nut, ast.Constant) and isinstance(nut.value, str) and id(nut) not in docstring:
            ra.append(nut.value)
        elif isinstance(nut, ast.Name):
            ra.append(nut.id)
        elif isinstance(nut, ast.Attribute):
            ra.append(nut.attr)
        elif isinstance(nut, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ra.append(nut.name)
        elif isinstance(nut, ast.arg):
            ra.append(nut.arg)
        elif isinstance(nut, ast.keyword) and nut.arg:
            ra.append(nut.arg)
    return ra


# ───────────── Điều kiện 1: Lõi chạy đúng khi không có module nào ─────────────

def test_loi_chay_dung_khi_khong_co_module(du_lieu):
    """Lõi phục vụ bình thường khi không cài module nào: health, tính năng, modules, models và web tĩnh."""
    with TestClient(app) as c:
        # GET /health
        r_health = c.get("/health")
        assert r_health.status_code == 200
        assert r_health.json() == {"ok": True, "phien_ban": PHIEN_BAN}

        # GET /api/tinh-nang
        r_tn = c.get("/api/tinh-nang", headers=QT)
        assert r_tn.status_code == 200
        j_tn = r_tn.json()
        assert j_tn["modules"] == []
        assert j_tn["loai"] == []
        assert j_tn["kha_nang_da_cai"] == {}
        assert j_tn["kha_nang_lien_ket"] == {}
        assert j_tn["tab"] == []
        assert j_tn["muc"] == []

        # GET /api/modules
        r_mod = c.get("/api/modules", headers=QT)
        assert r_mod.status_code == 200
        assert r_mod.json()["modules"] == []

        # GET /v1/models với khoá quản trị
        r_models = c.get("/v1/models", headers=QT)
        assert r_models.status_code == 200
        assert r_models.json() == {"object": "list", "data": []}

        # Các trang web tĩnh
        for trang in ("/", "/index.html", "/khung.js", "/khung.css", "/He-thong.dc.html", "/Studio.dc.html"):
            r_tinh = c.get(trang)
            assert r_tinh.status_code == 200, f"Lỗi tải trang tĩnh {trang}"


# ───────────── Điều kiện 2: Thêm/gỡ module giữa chừng không sửa mã lõi ─────────────

def test_them_go_module_giua_chung_khong_sua_ma_loi(du_lieu):
    """Thêm và gỡ module qua nạp lại nóng, phản ánh đầy đủ trên API mà không sửa mã lõi."""
    with TestClient(app) as c:
        assert c.get("/api/tinh-nang", headers=QT).json()["modules"] == []

        # Tạo module giả với giá trị lạ
        mod_id = "module-gia-doc-lap-123"
        mod_dir = du_lieu / "modules" / mod_id
        mod_dir.mkdir(parents=True)
        manifest = {
            "id": mod_id,
            "ten": "Module Giả Độc Lập",
            "mo_ta": "Mô tả module độc lập chưa từng có trong repo",
            "phien_ban": "1.0.0",
            "hang": "A",
            "loai": "loai_la_doc_lap",
            "loai_ten": "Loại Lạ Độc Lập",
            "loai_thu_tu": 88,
            "giao_thuc": "openai",
            "tien_to": "tien_to_la_doc_lap",
            "cung_cap": ["cung_cap_la_xyz"],
            "truong_cau_hinh": [
                {
                    "khoa": "truong_la_bi_an",
                    "nhan": "Trường lạ bí ẩn",
                    "kieu": "chuoi",
                    "bat_buoc": False,
                    "mac_dinh": "gia_tri_mac_dinh_la",
                    "mo_ta": "Giá trị cấu hình lạ",
                }
            ],
            "tai_nguyen": {"mang_ra": []},
        }
        (mod_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        ma_module = """class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return [{"id": self.tien_to + "/m-la", "object": "model"}]
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
        (mod_dir / "module.py").write_text(ma_module, encoding="utf-8")

        # Nạp lại nóng
        r_nap = c.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        assert r_nap.json()["them"] == [mod_id]
        assert r_nap.json()["loi"] == []

        # /api/tinh-nang và /api/modules phản ánh module mới
        tn = c.get("/api/tinh-nang", headers=QT).json()
        assert any(m["id"] == mod_id for m in tn["modules"])
        assert any(l["ma"] == "loai_la_doc_lap" and l["ten"] == "Loại Lạ Độc Lập" for l in tn["loai"])
        assert tn["kha_nang_da_cai"].get("loai_la_doc_lap") == 1
        assert tn["kha_nang_da_cai"].get("cung_cap_la_xyz") == 1

        ds_mod = c.get("/api/modules", headers=QT).json()["modules"]
        assert any(m["id"] == mod_id and m["loai"] == "loai_la_doc_lap" for m in ds_mod)

        # Gỡ thư mục rồi nạp lại nóng
        shutil.rmtree(mod_dir)
        r_nap_xoa = c.post("/api/modules/nap-lai", headers=QT)
        assert r_nap_xoa.status_code == 200
        assert r_nap_xoa.json()["go"] == [mod_id]

        # Kiểm tra biến mất hoàn toàn
        tn_sau = c.get("/api/tinh-nang", headers=QT).json()
        assert tn_sau["modules"] == []
        assert tn_sau["loai"] == []
        assert tn_sau["kha_nang_da_cai"] == {}

        ds_sau = c.get("/api/modules", headers=QT).json()["modules"]
        assert ds_sau == []


# ───────────── Điều kiện 3: Không có tên nhà cung cấp trong lõi và giao diện ─────────────

def test_dem_dong_nhac_ten_nha_cung_cap_khong_vuot_tran():
    """Đếm dòng nhắc tên nhà cung cấp trong app/ và web/, không vượt quá trần quy định."""
    ten_ncc = _danh_sach_nha_cung_cap()
    cac_tep = sorted(
        list(GOC.glob("app/*.py"))
        + list(GOC.glob("web/*.html"))
        + [GOC / "web/khung.js", GOC / "web/khung.css"]
    )

    dong_khop = []
    for p in cac_tep:
        lines = p.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, 1):
            khop = _kiem_tra_dong_khop(line, ten_ncc)
            if khop:
                dong_khop.append((str(p.relative_to(GOC)), idx, khop, line.strip()))

    so_dong = len(dong_khop)
    thong_bao_loi = (
        f"giảm dần về 0, đừng thêm tên mới: hiện có {so_dong} dòng (trần {TRAN_HIEN_TAI}):\n"
        + "\n".join(f"  {d[0]}:{d[1]} {d[2]} -> {d[3]}" for d in dong_khop)
    )
    assert so_dong <= TRAN_HIEN_TAI, thong_bao_loi


def test_khong_dong_ma_thuc_thi_nao_chua_ten_nha_cung_cap():
    """Không có bất kỳ định danh hay chuỗi trong mã thực thi nào chứa tên nhà cung cấp."""
    ten_ncc = _danh_sach_nha_cung_cap()
    pham = []
    for p in sorted(GOC.glob("app/*.py")):
        for chu in _chu_trong_ma_python(p):
            chu_low = chu.lower()
            for t in ten_ncc:
                if t == "meta":
                    if "metadata" in chu_low:
                        continue
                    if re.search(r"\bmeta\b", chu_low):
                        pham.append(f"{p.relative_to(GOC)}: «{t}» trong {chu[:80]!r}")
                elif t in chu_low:
                    pham.append(f"{p.relative_to(GOC)}: «{t}» trong {chu[:80]!r}")

    assert not pham, "Mã thực thi chứa đặc thù nhà cung cấp:\n" + "\n".join(pham)


# ───────────── Điều kiện 4: Mã mở (loại lạ và khả năng lạ) ─────────────

def test_ma_mo_loai_la_va_kha_nang_la(du_lieu):
    """Mã mở: loại lạ và khả năng lạ không gây lỗi, được đếm nguyên văn và không chặn giao diện."""
    with TestClient(app) as c:
        mod_id = "module-ma-mo-la"
        mod_dir = du_lieu / "modules" / mod_id
        mod_dir.mkdir(parents=True)
        manifest = {
            "id": mod_id,
            "ten": "Module Mã Mở Lạ",
            "mo_ta": "Mô tả tính năng mã mở",
            "hang": "A",
            "loai": "loai_la_xyz",
            "loai_ten": "Loại Lạ XYZ",
            "loai_thu_tu": 50,
            "giao_thuc": "openai",
            "tien_to": "la_xyz",
            "cung_cap": ["kha_nang_la_abc", "kha_nang_la_def"],
            "muc": [{"id": "muc-la", "nhan": "Mục Lạ", "can": ["kha_nang_la_abc"]}],
            "truong_cau_hinh": [],
            "tai_nguyen": {"mang_ra": []},
        }
        (mod_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        ma_code = """class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return [{"id": self.tien_to + "/m-1", "object": "model"}]
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
        (mod_dir / "module.py").write_text(ma_code, encoding="utf-8")

        r_nap = c.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200

        # Lõi không lỗi, /api/tinh-nang đếm mã lạ nguyên văn
        r_tn = c.get("/api/tinh-nang", headers=QT)
        assert r_tn.status_code == 200
        tn = r_tn.json()
        assert tn["kha_nang_da_cai"]["loai_la_xyz"] == 1
        assert tn["kha_nang_da_cai"]["kha_nang_la_abc"] == 1
        assert tn["kha_nang_da_cai"]["kha_nang_la_def"] == 1
        assert tn["muc"] == [{"module": mod_id, "id": "muc-la", "nhan": "Mục Lạ", "can": ["kha_nang_la_abc"]}]

        # Liên kết module (không cần khoá bắt buộc)
        r_cai = c.post(f"/api/modules/{mod_id}/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200 and r_cai.json()["ok"] is True

        tn_lk = c.get("/api/tinh-nang", headers=QT).json()
        assert tn_lk["kha_nang_lien_ket"]["loai_la_xyz"] == 1
        assert tn_lk["kha_nang_lien_ket"]["kha_nang_la_abc"] == 1
        assert tn_lk["kha_nang_lien_ket"]["kha_nang_la_def"] == 1

        # Giao diện tĩnh trả bình thường, không bị chặn bởi mã lạ
        for trang in ("/", "/index.html", "/He-thong.dc.html", "/Studio.dc.html"):
            assert c.get(trang).status_code == 200

        # Nếu có node, chạy trực tiếp hàm của khung giao diện web/khung.js trên dữ liệu tinh_nang
        if shutil.which("node"):
            ma_js = (
                "const fs = require('fs'), vm = require('vm');"
                "const sb = {window: {}, location: {href: 'http://may/', replace() {}}, URL, console};"
                "vm.createContext(sb);"
                f"vm.runInContext(fs.readFileSync({json.dumps(str(GOC / 'web' / 'khung.js'))}, 'utf8'), sb);"
                f"const K = sb.window.Khung, tn = {json.dumps(tn_lk)};"
                "const kq = {"
                "  trangDau: K.trangDau(tn),"
                "  nav: K.navChinh(tn, ''),"
                "  navHT: K.navHeThong(tn, '', 0).map((x) => x.id),"
                "  thieu: K.thieu(tn.muc[0], tn),"
                "  noiThemKhoa: K.noiThemKhoa(tn.modules[0], tn)"
                "};"
                "process.stdout.write(JSON.stringify(kq));"
            )
            r_node = subprocess.run(["node", "-e", ma_js], capture_output=True, text=True, timeout=15)
            assert r_node.returncode == 0, r_node.stderr
            kq_khung = json.loads(r_node.stdout)
            assert kq_khung["trangDau"] == "He-thong.dc.html"
            assert not any(x["id"].startswith("m-") for x in kq_khung["nav"])
            assert "module" in kq_khung["navHT"] and "bao_mat" in kq_khung["navHT"]
            assert kq_khung["thieu"] == []  # Không thiếu vì đã liên kết và có đủ khả năng
            assert kq_khung["noiThemKhoa"]["tab"] == "module"


def test_module_mang_tab_web_la_doc_lap(du_lieu):
    """Module mang tab_web LẠ: lõi tự nhận, khi liên kết tab hiện trong thanh Hệ thống
    (sau Module, trước Bảo mật), không nằm trên thanh chính/thanh dưới; gỡ thì biến mất."""
    mod_id = "mod-tab-la-doc-lap"
    mod_dir = du_lieu / "modules" / mod_id
    mod_dir.mkdir(parents=True)
    manifest = {
        "id": mod_id,
        "hang": "A",
        "ten": "Module Tab Lạ Độc Lập",
        "mo_ta": "Mô tả tính năng tab web lạ",
        "loai": "loai_la_uvw",
        "loai_ten": "Loại Lạ UVW",
        "loai_thu_tu": 80,
        "giao_thuc": "openai",
        "tien_to": "la_uvw",
        "cung_cap": ["kha_nang_la_123"],
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
        "tab_web": {"nhan": "Tab Riêng Lạ", "trang": "web/index.html"},
    }
    (mod_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    ma_code = """class Module:
    def __init__(self, m): self.manifest = m; self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""
    (mod_dir / "module.py").write_text(ma_code, encoding="utf-8")

    with TestClient(app) as c:
        assert c.post("/api/modules/nap-lai", headers=QT).status_code == 200
        # Cài/liên kết
        r_cai = c.post(f"/api/modules/{mod_id}/cai", json={"cau_hinh": {}}, headers=QT)
        assert r_cai.status_code == 200 and r_cai.json()["ok"] is True

        tn = c.get("/api/tinh-nang", headers=QT).json()
        assert any(t["module"] == mod_id for t in tn.get("tab", []))

        if shutil.which("node"):
            ma_js = (
                "const fs = require('fs'), vm = require('vm');"
                "const sb = {window: {}, location: {href: 'http://may/', replace() {}}, URL, console};"
                "vm.createContext(sb);"
                f"vm.runInContext(fs.readFileSync({json.dumps(str(GOC / 'web' / 'khung.js'))}, 'utf8'), sb);"
                f"const K = sb.window.Khung, tn = {json.dumps(tn)};"
                "const kq = {"
                "  navChinh: K.navChinh(tn, '').map((x) => x.id),"
                "  navDuoi: K.navDuoi(tn, '').map((x) => x.id),"
                "  navHT: K.navHeThong(tn, '', 0).map((x) => x.id)"
                "};"
                "process.stdout.write(JSON.stringify(kq));"
            )
            r_node = subprocess.run(["node", "-e", ma_js], capture_output=True, text=True, timeout=15)
            assert r_node.returncode == 0, r_node.stderr
            res = json.loads(r_node.stdout)
            # Không có trên thanh trên và thanh dưới
            assert f"m-{mod_id}" not in res["navChinh"]
            assert f"m-{mod_id}" not in res["navDuoi"]
            # Nằm trong thanh Hệ thống, sau module và trước bao_mat
            assert f"m-{mod_id}" in res["navHT"]
            assert res["navHT"].index("module") < res["navHT"].index(f"m-{mod_id}") < res["navHT"].index("bao_mat")

        # Gỡ module
        assert c.delete(f"/api/modules/{mod_id}", headers=QT).status_code == 200
        tn_sau = c.get("/api/tinh-nang", headers=QT).json()
        assert not any(t["module"] == mod_id for t in tn_sau.get("tab", []))

