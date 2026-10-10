"""Khám phá module từ cấu trúc thư mục (hai nguồn) + nạp lại nóng, không khởi động lại lõi.

Mọi module ở đây là module GIẢ dựng trong thư mục tạm — chứng minh thả thư mục mới là lõi
tự nhận, không sửa dòng nào của lõi.
"""

import json
import logging
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from gia import ghi_thu_muc, tep_echo

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    anh = tmp_path / "anh"
    anh.mkdir()
    monkeypatch.setattr(so_module, "THU_MUC_ANH", anh)
    (tmp_path / "data" / "modules").mkdir(parents=True)
    return tmp_path


def vol(du_lieu, ten):
    return du_lieu / "data" / "modules" / ten


def tin(model="echo/lap-lai", noi_dung="chào"):
    return {"model": model, "messages": [{"role": "user", "content": noi_dung}]}


def tra_loi(c, model="echo/lap-lai"):
    r = c.post("/v1/chat/completions", json=tin(model), headers=QT)
    assert r.status_code == 200, r.text
    return r.json()["choices"][0]["message"]["content"]


def test_kham_pha_hai_nguon_module_hong_chi_hong_rieng(du_lieu, caplog):
    ghi_thu_muc(du_lieu / "anh" / "mau", tep_echo("mẫu đi kèm", id_="mau", tien_to="mau"))
    ghi_thu_muc(vol(du_lieu, "echo"), tep_echo())
    hong = {
        "hong-json": {"manifest.json": b"{khong phai json", "module.py": b""},
        "thieu-truong": {**tep_echo(id_="thieu-truong", tien_to="tt")},
        "kieu-la": {**tep_echo(id_="kieu-la", tien_to="kl")},
        "zz-trung-id": tep_echo(id_="echo", tien_to="khac"),
        "trung-tien-to": tep_echo(id_="trung-tien-to", tien_to="echo"),
        "cu-phap": tep_echo(id_="cu-phap", tien_to="cp", hong=True),
        "thieu-goi": tep_echo(id_="thieu-goi", tien_to="tg", yeu_cau_goi=["goi_khong_ton_tai_xyz"]),
        "de-anh": tep_echo(id_="mau", tien_to="de"),
    }
    m = json.loads(hong["thieu-truong"]["manifest.json"])
    del m["tien_to"]
    hong["thieu-truong"]["manifest.json"] = json.dumps(m).encode()
    m = json.loads(hong["kieu-la"]["manifest.json"])
    m["truong_cau_hinh"] = [{"khoa": "k", "nhan": "K", "kieu": "bi_mat "}]
    hong["kieu-la"]["manifest.json"] = json.dumps(m).encode()
    hong["thieu-lop"] = {**tep_echo(id_="thieu-lop", tien_to="tl"), "module.py": b"X = 1\n"}
    hong["loi-init"] = {**tep_echo(id_="loi-init", tien_to="li"),
                        "module.py": tep_echo()["module.py"].replace(b"self.da_dung = False",
                                                                       b"raise RuntimeError('hong init')")}
    hong["thoat"] = {**tep_echo(id_="thoat", tien_to="th"), "module.py": b"raise SystemExit(3)\n"}
    for ten, tep in hong.items():
        ghi_thu_muc(vol(du_lieu, ten), tep)
    vol(du_lieu, "khong-manifest").mkdir()
    (vol(du_lieu, "__pycache__")).mkdir()
    (vol(du_lieu, ".tam-echo-1234")).mkdir()
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as c:
            j = c.get("/api/modules/catalog").json()
            assert c.get("/health").status_code == 200  # lõi vẫn sống
    ids = {(x["id"], x["nguon"]) for x in j["modules"]}
    assert ids == {("mau", "anh"), ("echo", "du_lieu")}
    loi = {x["thu_muc"].rsplit("/", 1)[-1]: x["ly_do"] for x in j["loi"]}
    assert set(loi) == set(hong) | {"khong-manifest"}
    assert "manifest.json" in loi["hong-json"]
    assert "tien_to" in loi["thieu-truong"]
    assert "kiểu" in loi["kieu-la"]
    assert "trùng id" in loi["zz-trung-id"]
    assert "trùng tiền tố" in loi["trung-tien-to"]
    assert "SyntaxError" in loi["cu-phap"]
    assert "Module" in loi["thieu-lop"]
    assert "hong init" in loi["loi-init"]
    assert "SystemExit" in loi["thoat"]
    assert "goi_khong_ton_tai_xyz" in loi["thieu-goi"] and "từ xa" in loi["thieu-goi"]
    assert "trùng id" in loi["de-anh"] and "đi kèm ảnh" in loi["de-anh"]
    assert "thiếu manifest.json" in loi["khong-manifest"]
    # Không im lặng: mỗi thư mục hỏng có một WARNING nêu tên thư mục.
    for ten in loi:
        assert any(ten in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING), ten
    assert "__pycache__" not in caplog.text and ".tam-echo" not in caplog.text


def test_tha_thu_muc_moi_nap_lai_khong_khoi_dong(du_lieu):
    with TestClient(app) as c:
        assert c.get("/v1/models", headers=QT).json()["data"] == []
        ghi_thu_muc(vol(du_lieu, "echo"), tep_echo())
        kq = c.post("/api/modules/nap-lai", headers=QT).json()
        assert kq == {"them": ["echo"], "doi": [], "go": [], "loi": []}
        # Khám phá xong nhưng chưa cài cấu hình → chưa phục vụ, và /m/ chưa mở.
        assert c.get("/v1/models", headers=QT).json()["data"] == []
        assert c.get("/m/echo/echo.dc.html").status_code == 404
        r = c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT)
        assert r.status_code == 200 and r.json()["ok"]
        assert [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]] == ["echo/lap-lai"]
        assert tra_loi(c) == "echo v1: chào"
        ds = c.get("/api/modules").json()["modules"]
        assert ds[0]["tab_web"] == {"nhan": "Echo", "duong": "/m/echo/echo.dc.html"}
        r = c.get("/m/echo/echo.dc.html")
        assert r.status_code == 200 and "echo v1" in r.text
        for duong in ("/m/echo/%2e%2e/manifest.json", "/m/echo/..%2fmodule.py", "/m/echo/khong-co.html",
                      "/m/khong-co/echo.dc.html"):
            assert c.get(duong).status_code == 404, duong


def test_nap_lai_doi_noi_dung_dung_ban_moi_va_don_sys_modules(du_lieu):
    ghi_thu_muc(vol(du_lieu, "echo"), tep_echo("echo v1"))
    with TestClient(app) as c:
        c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT)
        so = c.app.state.so
        ten_cu = so.catalog["echo"].ten_ma
        the_cu = so.dang_chay["echo"]
        assert ten_cu in sys.modules
        # Cùng độ dài, ghi ngay trong cùng giây — kiểu thay đổi mà .pyc theo mtime sẽ bỏ sót.
        ghi_thu_muc(vol(du_lieu, "echo"), tep_echo("echo v2"))
        kq = c.post("/api/modules/nap-lai", headers=QT).json()
        assert kq["doi"] == ["echo"] and kq["loi"] == []
        assert tra_loi(c) == "echo v2: chào"
        assert ten_cu not in sys.modules  # mã bản cũ không còn nằm trong bộ nhớ
        assert so.catalog["echo"].ten_ma in sys.modules
        assert the_cu.da_dung is True
        # Không đổi gì → nạp lại không chạy lại mã.
        assert c.post("/api/modules/nap-lai", headers=QT).json() == {"them": [], "doi": [], "go": [], "loi": []}


def test_ban_moi_hong_giu_ban_cu_va_module_khac_van_chay(du_lieu):
    ghi_thu_muc(vol(du_lieu, "echo"), tep_echo("echo v1"))
    ghi_thu_muc(vol(du_lieu, "khac"), tep_echo("khác", id_="khac", tien_to="khac"))
    with TestClient(app) as c:
        for i in ("echo", "khac"):
            c.post(f"/api/modules/{i}/cai", json={"cau_hinh": {}}, headers=QT)
        ghi_thu_muc(vol(du_lieu, "echo"), tep_echo("echo v2", hong=True))
        kq = c.post("/api/modules/nap-lai", headers=QT).json()
        assert kq["doi"] == [] and len(kq["loi"]) == 1 and "SyntaxError" in kq["loi"][0]["ly_do"]
        assert tra_loi(c) == "echo v1: chào"
        assert tra_loi(c, "khac/lap-lai") == "khác: chào"


def test_thu_muc_bien_mat_thi_go_va_dung(du_lieu):
    ghi_thu_muc(vol(du_lieu, "echo"), tep_echo())
    with TestClient(app) as c:
        c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT)
        the = c.app.state.so.dang_chay["echo"]
        ten = c.app.state.so.catalog["echo"].ten_ma
        shutil.rmtree(vol(du_lieu, "echo"))
        assert c.post("/api/modules/nap-lai", headers=QT).json()["go"] == ["echo"]
        assert the.da_dung is True and ten not in sys.modules
        assert c.get("/v1/models", headers=QT).json()["data"] == []
        assert c.get("/m/echo/echo.dc.html").status_code == 404


def test_delete_module_volume_xoa_ca_thu_muc(du_lieu):
    ghi_thu_muc(vol(du_lieu, "echo"), tep_echo())
    with TestClient(app) as c:
        c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.delete("/api/modules/echo", headers=QT)
        assert r.status_code == 200 and r.json()["nap_lai"]["go"] == ["echo"]
        assert not vol(du_lieu, "echo").exists()
        assert [p.name for p in (du_lieu / "data" / "modules").iterdir()] == []
        assert c.get("/api/modules/catalog").json()["modules"] == []
        assert c.delete("/api/modules/echo", headers=QT).status_code == 404


def test_delete_module_di_kem_anh(du_lieu):
    ghi_thu_muc(du_lieu / "anh" / "mau", tep_echo(id_="mau", tien_to="mau"))
    with TestClient(app) as c:
        r = c.delete("/api/modules/mau", headers=QT)
        assert r.status_code == 400 and "đi kèm ảnh" in r.json()["error"]["message"]
        c.post("/api/modules/mau/cai", json={"cau_hinh": {}}, headers=QT)
        r = c.delete("/api/modules/mau", headers=QT)
        assert r.status_code == 200 and "đi kèm ảnh" in r.json()["ghi_chu"]
        assert (du_lieu / "anh" / "mau" / "module.py").exists()
        assert c.get("/v1/models", headers=QT).json()["data"] == []


def test_id_trung_ten_tuyen_bi_tu_choi(du_lieu):
    ghi_thu_muc(vol(du_lieu, "tu-xa"), tep_echo(id_="tu-xa", tien_to="tx"))
    with TestClient(app) as c:
        j = c.get("/api/modules/catalog").json()
    assert j["modules"] == [] and "không hợp lệ" in j["loi"][0]["ly_do"]
