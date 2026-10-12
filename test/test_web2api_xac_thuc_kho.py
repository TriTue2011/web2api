"""Xác thực quản trị và kho cấu hình/bí mật."""

import json
import logging
import stat

import pytest
from fastapi.testclient import TestClient

from app import kho, xac_thuc
from app.main import app

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    with TestClient(app) as c:
        yield c


CAN_QUAN_TRI = [
    ("GET", "/v1/models"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/api/modules/gemini-api/cai"),
    ("DELETE", "/api/modules/gemini-api"),
    ("GET", "/api/modules/gemini-api/suc-khoe"),
    ("GET", "/api/modules/gemini-api/cau-hinh"),
    ("POST", "/api/modules/nap-lai"),
    ("POST", "/api/modules/tu-xa"),
    ("GET", "/api/modules/thong-bao"),
    ("GET", "/api/nguon-git"),
    ("PUT", "/api/nguon-git"),
    ("POST", "/api/nguon-git/cai/echo"),
    ("GET", "/api/dia-chi"),
    ("PUT", "/api/dia-chi"),
    ("GET", "/api/ket-noi"),
]


@pytest.mark.parametrize("phuong_thuc,duong", CAN_QUAN_TRI)
@pytest.mark.parametrize("tieu_de", [None, "Bearer sai-khoa", f"Basic {KHOA_QT}", "Bearer "])
def test_thieu_hoac_sai_khoa_bi_401(client, phuong_thuc, duong, tieu_de):
    hd = {"Authorization": tieu_de} if tieu_de is not None else {}
    r = client.request(phuong_thuc, duong, headers=hd, json={})
    assert r.status_code == 401
    loi = r.json()["error"]
    assert loi["type"] == "invalid_request_error" and loi["code"] == "invalid_api_key" and loi["message"]


@pytest.mark.parametrize("duong", ["/health", "/api/he-thong", "/api/modules", "/api/modules/catalog", "/", "/support.js"])
def test_duong_mo_khong_can_khoa(client, duong):
    assert client.get(duong).status_code == 200


def test_dung_khoa_thi_qua(client):
    assert client.get("/v1/models", headers=QT).status_code == 200


def test_khoa_tu_sinh_lan_dau_0600_khong_in_ra_log(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as c:
            p = tmp_path / "admin_key"
            khoa = p.read_text().strip()
            assert len(khoa) >= 40
            assert stat.S_IMODE(p.stat().st_mode) == 0o600
            assert c.get("/v1/models", headers={"Authorization": f"Bearer {khoa}"}).status_code == 200
    assert khoa not in caplog.text
    assert str(p) in caplog.text  # chỉ báo ĐƯỜNG DẪN để người vận hành tìm được
    assert xac_thuc.khoa_quan_tri() == khoa  # lần sau đọc lại đúng khoá cũ, không sinh mới


def test_tep_khoa_rong_thi_khong_chap_nhan_khoa_rong(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    (tmp_path / "admin_key").write_text("")
    with pytest.raises(RuntimeError):
        xac_thuc.khoa_quan_tri()


def test_ghi_nguyen_tu_va_quyen(tmp_path):
    p = tmp_path / "a" / "b.json"
    kho.ghi_nguyen_tu(p, "{}", 0o600)
    assert p.read_text() == "{}" and stat.S_IMODE(p.stat().st_mode) == 0o600
    kho.ghi_nguyen_tu(p, '{"x": 1}', 0o600)
    assert json.loads(p.read_text()) == {"x": 1}
    assert [x.name for x in p.parent.iterdir()] == ["b.json"]  # không sót tệp tạm


@pytest.mark.parametrize("vao,ra", [("AIzaSyABCDEFGHIJKLmnop", "…mnop"), ("ngan", "…"), ("", "…")])
def test_che(vao, ra):
    assert kho.che(vao) == ra


def test_bi_mat_ten_khong_hop_le_bi_tu_choi(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    for ten in ("../x", "a/b", "", "A"):
        with pytest.raises(ValueError):
            kho.ghi_bi_mat(ten, {"k": "v"})


def test_modules_json_hong_thi_cai_tu_choi_va_khong_ghi_de(client, tmp_path):
    (tmp_path / "modules.json").write_text("{hỏng")
    loi = kho.KhoHong
    with pytest.raises(loi):
        kho.luu_cai_dat("echo", [], {})
    assert (tmp_path / "modules.json").read_text() == "{hỏng"
