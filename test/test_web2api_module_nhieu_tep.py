"""Module NHIỀU TỆP nạp được ở chỗ cài thật (<WEB2API_DATA>/modules/<id>/), không chỉ khi chạy trong kho.

Lỗi thật 12/10: module muse viết `from modules.muse.client import …` — chạy xanh trong kho (có gói `modules`) nhưng lúc cài thật
lõi báo «No module named 'modules'», vì lõi nạp module.py như tệp đơn. Chuẩn: lõi nạp như GÓI, tệp anh em import tương đối."""
from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}

MA_MODULE = """from .ho_tro import HE_SO, ten_anh_em
from .goi_con.sau import SAU


class Module:
    def __init__(self, m):
        self.manifest = m
        self.tien_to = m["tien_to"]
    def nap(self, cfg): pass
    async def cai(self, cfg): return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": {}}
    async def chat(self, b, s): return 200, {}, b"{}"
    async def models(self): return [{"id": self.tien_to + "/" + ten_anh_em() + str(HE_SO) + SAU, "object": "model"}]
    async def suc_khoe(self): return {"ok": True}
    async def dung(self): pass
"""


@pytest.fixture
def khach(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app) as c:
        yield c, tmp_path


def _tha_module(goc, id_, he_so, ten="client"):
    thu_muc = goc / "modules" / id_
    (thu_muc / "goi_con").mkdir(parents=True, exist_ok=True)
    (thu_muc / "manifest.json").write_text(json.dumps({
        "id": id_, "ten": id_, "mo_ta": "thử", "phien_ban": "0.0.1", "loai": "ai", "loai_ten": "AI", "loai_thu_tu": 10,
        "giao_thuc": "openai", "tien_to": id_.replace("-", ""), "cung_cap": ["chat"], "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []}}), encoding="utf-8")
    (thu_muc / "module.py").write_text(MA_MODULE, encoding="utf-8")
    (thu_muc / "ho_tro.py").write_text(f"HE_SO = {he_so}\ndef ten_anh_em(): return {ten!r}\n", encoding="utf-8")
    (thu_muc / "goi_con" / "__init__.py").write_text("", encoding="utf-8")
    (thu_muc / "goi_con" / "sau.py").write_text("SAU = '-sau'\n", encoding="utf-8")


def test_module_nhieu_tep_nap_duoc_va_hai_module_cung_ten_tep_khong_dung_nhau(khach):
    c, goc = khach
    _tha_module(goc, "mot-tep", 1)
    _tha_module(goc, "hai-tep", 2)          # CÙNG tên tệp anh em `ho_tro.py` nhưng nội dung khác
    r = c.post("/api/modules/nap-lai", headers=QT).json()
    assert r["loi"] == [] and sorted(r["them"]) == ["hai-tep", "mot-tep"]
    ids = {m["id"] for m in c.get("/api/modules", headers=QT).json()["modules"]}
    assert {"mot-tep", "hai-tep"} <= ids


def test_sua_tep_anh_em_nap_lai_chay_ma_moi_va_go_het_mo_dun_cu(khach):
    c, goc = khach
    _tha_module(goc, "doi-tep", 1)
    assert c.post("/api/modules/nap-lai", headers=QT).json()["loi"] == []
    truoc = {k for k in sys.modules if k.startswith("web2api_module_doi_tep_")}
    assert any(k.endswith(".ho_tro") for k in truoc), "tệp anh em phải được import dưới tên gói của module"
    _tha_module(goc, "doi-tep", 7)          # chỉ đổi tệp anh em, module.py giữ nguyên
    r = c.post("/api/modules/nap-lai", headers=QT).json()
    assert r["loi"] == [] and r["doi"] == ["doi-tep"]
    sau = {k for k in sys.modules if k.startswith("web2api_module_doi_tep_")}
    assert not (truoc & sau), "mô-đun của bản cũ (kể cả tệp anh em) phải bị gỡ khỏi bộ nhớ"
