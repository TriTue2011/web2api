"""Kiểm thử NV176 — module tro-ly-giao-vien (trợ lý giáo viên, đợt 1: kiểm kê + phần thuần).

Luồng test-first: chạy trước khi có mã → đỏ (FileNotFoundError ở collection).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

GOC = Path(__file__).resolve().parents[1]
MOD = GOC / "modules" / "tro-ly-giao-vien"

# Gói tổng hợp để `from . import ...` trong các tệp port chạy được ngoài loader app.
_PKG = types.ModuleType("nv176_pkg")
_PKG.__path__ = [str(MOD)]
sys.modules["nv176_pkg"] = _PKG


def _tai(ten: str, *phu: str):
    name = f"nv176_pkg.{ten}"
    if name in sys.modules:
        return sys.modules[name]
    duong = MOD.joinpath(*phu) if phu else MOD / (ten + ".py")
    spec = importlib.util.spec_from_file_location(name, duong)
    assert spec is not None and spec.loader is not None, duong
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# ── Cấu trúc tệp module ──────────────────────────────────────────────────

def test_module_co_du_tep():
    for ten in ("manifest.json", "module.py", "cham.py", "tieng_anh.py",
                "giao_an.py", "mon_hoc.py", "lop.py", "muc_luc.py",
                "kho_de.py", "README.md"):
        assert (MOD / ten).is_file(), ten


def test_manifest_co_cau_truc():
    m = json.loads((MOD / "manifest.json").read_text(encoding="utf-8"))
    assert m["id"] == "tro-ly-giao-vien"
    assert m["hang"] in ("A", "B")
    assert "thiet_bi" in m["cung_cap"]
    for truong in ("id", "ten", "mo_ta", "loai", "giao_thuc", "tien_to", "hang"):
        assert m[truong], truong
    assert m["tai_nguyen"]["ram_them_mb"] is None or isinstance(m["tai_nguyen"]["ram_them_mb"], int)


def test_readme_va_tam_chieu_co_dong():
    rd = (MOD / "README.md").read_text(encoding="utf-8")
    assert "Trợ lý giáo viên" in rd
    tc = (GOC / "docs" / "THAM-CHIEU.md").read_text(encoding="utf-8")
    assert "tro-ly-giao-vien" in tc
    goc = (GOC / "README.md").read_text(encoding="utf-8")
    assert "Trợ lý giáo viên" in goc


# ── Đơn vị: mon_hoc (môn theo lớp) ────────────────────────────────────────

def test_mon_hoc_theo_lop():
    mon = _tai("mon_hoc", "mon_hoc.py")
    assert mon.subjects_for(4) == ("toan", "tviet", "anh", "sudia")
    assert mon.subjects_for(12) == ("toan", "van", "anh", "su", "dia", "ly", "hoa", "sinh")
    assert mon.level_of(4) == "tieu_hoc"
    assert mon.level_of(7) == "thcs"
    assert mon.level_of(11) == "thpt"
    assert "Tiểu học" in mon.level_label(4)
    assert mon.chuan_mon("TV") == "tviet"        # chữ viết tắt cũng nhận
    assert mon.chuan_mon("không biết môn này") is None
    assert "Toán" in (mon.nhan(mon.subjects_for(1)[0]) or "")


# ── Đơn vị: lop (xác định lớp, mạch kiến thức) ────────────────────────────

def test_lop_xac_dinh_lop():
    lop = _tai("lop", "lop.py")
    kq = lop.xac_dinh_lop({"nam_sinh": 2017}, hom_nay_gia="2026-10-01")
    assert kq["lop"] == 4 and kq["nguon"] == "năm sinh"
    kq2 = lop.xac_dinh_lop({"nam_sinh": 2017, "lop": 5}, hom_nay_gia="2026-10-01")
    assert kq2["lop"] == 5 and kq2["nguon"] == "khai tay"  # ghi đè
    kq3 = lop.xac_dinh_lop({"nam_sinh": 1990}, hom_nay_gia="2026-10-01")
    assert kq3["lop"] == 0  # ngoài phạm vi → 0, không bịa
    mach = lop.mach_kien_thuc("toan", 2)
    assert any(m["id"] == "tru" for m in mach)
    mach_tv = lop.mach_kien_thuc("van", 5)
    assert mach_tv[0]["id"] == "chinhta"


# ── Đơn vị: cham (ra đề + chấm) ───────────────────────────────────────────

def test_cham_ra_de_toan():
    cham = _tai("cham", "cham.py")
    de = cham.ra_de(lop=4, mon="toan", chu_de="cộng", so_cau=5, do_kho="medium")
    assert len(de["cau_hoi"]) == 5
    for cau in de["cau_hoi"]:
        assert cau["de_bai"] and cau["goi_y_dap_an"] and cau["id"]
    assert de["mon"] == "toan"


def test_cham_ra_de_anh():
    cham = _tai("cham", "cham.py")
    ta = _tai("tieng_anh", "tieng_anh.py")
    de = cham.ra_de(lop=5, mon="anh", chu_de="present", so_cau=3)
    assert len(de["cau_hoi"]) == 3
    assert de["cau_hoi"][0]["skill"] == "present"
    ban = ta.english_skill_map(5)
    assert any(s["id"] == "present" for s in ban["skills"])
    assert ban["cefr"] == "A1"


def test_cham_diem_toan_dung():
    cham = _tai("cham", "cham.py")
    kq = cham.cham_mot_cau("Tính: 7 + 5 = ?", "12", "12", mon="toan")
    assert kq["diem"] == 10 and kq["dung"] is True
    kq2 = cham.cham_mot_cau("Tính: 7 + 5 = ?", "", "12", mon="toan")
    assert kq2["diem"] == 0  # chưa làm → 0, không đoán


def test_cham_khong_tiet_lo_bai():
    cham = _tai("cham", "cham.py")
    kq = cham.cham_mot_cau("Tính: 7 + 5 = ?", "18", "12", mon="toan")
    assert kq["diem"] < 10
    assert "12" not in (kq.get("phan_hoi") or "")  # không đập đáp án vào feedback


def test_band_diem_rubric():
    cham = _tai("cham", "cham.py")
    band = cham.band_diem(8, mon="toan")
    assert band["band"] == "Khá"
    tb = cham.bang_rubric("van")
    assert "Rubric chấm van" in tb


def test_goi_y_bac_thang():
    cham = _tai("cham", "cham.py")
    g = cham.goi_y_bac_thang("Tính 12 + 9", "21", cap=1)
    assert g["cap"] == 1 and g["goi_y"] and "21" not in g["text_goi_y"]


# ── Đơn vị: giao_an / muc_luc ─────────────────────────────────────────────

def test_giao_an_mau():
    ga = _tai("giao_an", "giao_an.py")
    md = ga.mau_giao_an(lop=6, mon="van", chu_de="danh từ", muc_tieu="Nhận biết danh từ")
    assert "Lớp 6" in md and "danh từ" in md and "### 1)" in md
    assert "khen" in ga.loi_khen(9, lop=3).lower() or "giỏi" in ga.loi_khen(9, lop=3).lower()


def test_muc_luc_tu_markdown():
    muc = _tai("muc_luc", "muc_luc.py")
    md = "Bài 1. Cộng trong phạm vi 10 … 5\nBài 2. Trừ … 12\nxem Bài 12 trang 3"
    muc_luc = muc.muc_luc_tu_markdown(md)
    assert len(muc_luc) == 2 and muc_luc[0]["trang"] == 5
    assert muc.doan_tap("SGK_Toán_4_KNTT_tập_2.pdf") == "tập hai"


def test_verbalize_tts():
    ga = _tai("giao_an", "giao_an.py")
    assert "nhân" in ga.verbalize_cho_tts("2 × 3 = 6")


# ── Tích hợp HTTP: 401/403/404 + hợp đồng C2 ───────────────────────────────

KHOA_QUAN_TRI = "khoa-quan-tri-nv176-test-0123456789abcdef"


def _tai_module_class():
    spec = importlib.util.spec_from_file_location(
        "nv176_pkg.module", MOD / "module.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["nv176_pkg.module"] = m
    spec.loader.exec_module(m)
    return m.Module


@pytest.fixture
def moi_truong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QUAN_TRI)
    return tmp_path


def test_http_401_va_404_thuc_the_la(moi_truong):
    from fastapi.testclient import TestClient
    from app import so_module
    from app.main import app
    old = so_module.THU_MUC_ANH if hasattr(so_module, "THU_MUC_ANH") else None
    try:
        if hasattr(so_module, "THU_MUC_ANH"):
            so_module.THU_MUC_ANH = GOC / "modules"
        QT = {"Authorization": f"Bearer {KHOA_QUAN_TRI}"}
        with TestClient(app) as client:
            assert client.post("/api/modules/nap-lai", headers=QT).status_code == 200
            assert client.post("/api/modules/tro-ly-giao-vien/cai", headers=QT,
                               json={"cau_hinh": {}}).status_code == 200
            # 401: không khoá
            r = client.post("/api/thiet-bi/tro_ly_giao_vien.kho_de/hanh-dong",
                            json={"hanh_dong": "luu_de", "tham_so": {"quiz_id": "x", "de": {"cau_hoi": []}}})
            assert r.status_code == 401
            # xac-nhan thực thể để gọi
            for eid in ("tro_ly_giao_vien.thu_vien", "tro_ly_giao_vien.kho_de"):
                assert client.post(f"/api/thiet-bi/{eid}/xac-nhan", headers=QT).status_code == 200
            # 404: thực thể lạ (IDOR entity)
            r404 = client.post("/api/thiet-bi/tro_ly_giao_vien.thuc_the_la/hanh-dong",
                               headers=QT, json={"hanh_dong": "doc_de", "tham_so": {"quiz_id": "x"}})
            assert r404.status_code == 404
            # quản trị chấm một câu (đọc) → 200
            r_ok = client.post("/api/thiet-bi/tro_ly_giao_vien.thu_vien/hanh-dong",
                               headers=QT, json={
                                   "hanh_dong": "cham_mot_cau",
                                   "tham_so": {"cau_hoi": "Tính: 7 + 5 = ?", "bai_hs": "12", "goi_y_dap_an": "12"},
                               })
            assert r_ok.status_code == 200
            assert r_ok.json()["ket_qua"]["diem"] == 10
    finally:
        if hasattr(so_module, "THU_MUC_ANH"):
            so_module.THU_MUC_ANH = old


# ── IDOR giữa các danh tính ở mức module ─────────────────────────────────

async def _chay(mod, eid, hd, ts):
    return await mod.thuc_hien(eid, hd, ts)


def test_idor_danh_tinh_kho_de(tmp_path):
    import anyio
    from app import kho, ngu_canh
    Module = _tai_module_class()
    manifest = json.loads((MOD / "manifest.json").read_text(encoding="utf-8"))
    mod = Module(manifest)

    async def kich():
        # GV A lưu đề
        tk = ngu_canh.dat({"id": "gv-A", "pham_vi": ["thiet_bi"]})
        try:
            r = await mod.thuc_hien("tro_ly_giao_vien.kho_de", "luu_de",
                                    {"quiz_id": "de001", "de": {"cau_hoi": [], "questions": []}})
            assert r["ok"] is True, r
            r2 = await mod.thuc_hien("tro_ly_giao_vien.kho_de", "doc_de", {"quiz_id": "de001"})
            assert r2["ok"] is True
        finally:
            ngu_canh.khoi_phuc(tk)
        # GV B KHÔNG thấy đề của A
        tk = ngu_canh.dat({"id": "gv-B", "pham_vi": ["thiet_bi"]})
        try:
            r3 = await mod.thuc_hien("tro_ly_giao_vien.kho_de", "doc_de", {"quiz_id": "de001"})
            assert r3["ok"] is False
            r4 = await mod.thuc_hien("tro_ly_giao_vien.thu_vien", "cham_de",
                                     {"quiz_id": "de001", "answers": {}})
            assert r4["ok"] is False
            r5 = await mod.thuc_hien("tro_ly_giao_vien.kho_de", "liet_ke_de", {})
            assert r5["danh_sach"] == []
        finally:
            ngu_canh.khoi_phuc(tk)
        # không ngữ cảnh → từ chối
        r6 = await mod.thuc_hien("tro_ly_giao_vien.kho_de", "doc_de", {"quiz_id": "de001"})
        assert r6["ok"] is False and r6["ma_loi"] == "missing_context"

    with monkeypatch_ctx(tmp_path) as _:
        anyio.run(kich)


class monkeypatch_ctx:
    def __init__(self, tmp_path):
        import os
        self._old = os.environ.get("WEB2API_DATA")
        self.tmp = tmp_path

    def __enter__(self):
        import os
        os.environ["WEB2API_DATA"] = str(self.tmp)

    def __exit__(self, *a):
        import os
        if self._old is None:
            os.environ.pop("WEB2API_DATA", None)
        else:
            os.environ["WEB2API_DATA"] = self._old

def test_kho_de_cach_ly_danh_tinh(tmp_path):
    import stat as _stat
    kho = _tai("kho_de", "kho_de.py")
    luu = kho.luu_de(tmp_path, "aabbcc11", "de001", {"cau_hoi": []})
    assert luu["ok"] is True
    p = luu["duong_dan"]
    assert (_stat.S_IMODE(os.stat(p).st_mode) == 0o600)
    doc = kho.doc_de(tmp_path, "aabbcc11", "de001")
    assert doc["ok"] is True
    doc_khac = kho.doc_de(tmp_path, "ddeeff22", "de001")
    assert doc_khac["ok"] is False  # danh tính khác không thấy
    xoa = kho.xoa_de(tmp_path, "aabbcc11", "de001")
    assert xoa["ok"] is True and not p.exists()


def test_kho_de_tran_100_de(tmp_path):
    kho = _tai("kho_de", "kho_de.py")
    for i in range(100):
        r = kho.luu_de(tmp_path, "aabbcc11", f"de{i:03d}", {"cau_hoi": []})
        assert r["ok"] is True, r
    r101 = kho.luu_de(tmp_path, "aabbcc11", "de101", {"cau_hoi": []})
    assert r101["ok"] is False  # trần 100 đề/danh tính
    # ghi đè cùng quiz_id của cùng danh tính vẫn được
    r_lai = kho.luu_de(tmp_path, "aabbcc11", "de000", {"cau_hoi": [1]})
    assert r_lai["ok"] is True


def test_kho_de_chong_duong_dan(tmp_path):
    kho = _tai("kho_de", "kho_de.py")
    r = kho.luu_de(tmp_path, "aabbcc11", "../xau", {"cau_hoi": []})
    assert r["ok"] is False
