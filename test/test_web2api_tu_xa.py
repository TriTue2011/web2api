"""Module TỪ XA: đăng ký một dịch vụ OpenAI bằng URL, không nạp mã; chặn SSRF nhưng cho LAN."""

import asyncio
import json
import stat

import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from app.tu_xa import kiem_url_lan
from gia import MayChuThat, OpenAIGia

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_XA = "sk-lan-0000000000000000000000000000xyzw"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1", "http://localhost:8000/v1", "http://[::1]/v1",
    "http://169.254.169.254/latest", "http://0.0.0.0/v1", "http://224.0.0.1/v1",
    "ftp://192.168.1.2/v1", "http://u:p@192.168.1.2/v1", "khong-phai-url",
])
def test_chan_ssrf(url):
    with pytest.raises(ValueError):
        asyncio.run(kiem_url_lan(url))


@pytest.mark.parametrize("url", [
    "http://192.168.1.220:8000/v1", "http://10.0.0.5/v1/", "http://172.16.3.4:9000/v1", "https://8.8.8.8/v1",
])
def test_cho_phep_lan_va_internet(url):
    assert asyncio.run(kiem_url_lan(url)) == url.rstrip("/")


def test_loopback_chi_mo_khi_bat_co_test(monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    assert asyncio.run(kiem_url_lan("http://127.0.0.1:1/v1")) == "http://127.0.0.1:1/v1"
    with pytest.raises(ValueError):
        asyncio.run(kiem_url_lan("http://169.254.169.254/"))  # metadata vẫn chặn


@pytest.fixture(scope="module")
def _xa():
    g = OpenAIGia(khoa=KHOA_XA)
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def xa(_xa):
    _xa.nhat_ky.clear()
    _xa.khoa = KHOA_XA
    return _xa


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def dang_ky(c, xa, **them):
    than = {"id": "gpu-lan", "ten": "Model GPU trong LAN", "tien_to": "lan", "base_url": xa.url + "/v1",
            "api_key": KHOA_XA, **them}
    return c.post("/api/modules/tu-xa", json=than, headers=QT)


def test_dang_ky_dung_va_khong_ro_khoa(du_lieu, xa):
    with TestClient(app) as c:
        r = dang_ky(c, xa)
        assert r.status_code == 200, r.text
        assert r.json()["suc_khoe"]["ok"] is True
        assert [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]] == ["lan/qwen-3b"]
        than = {"model": "lan/qwen-3b", "messages": [{"role": "user", "content": "hi"}],
                "x_truong_la": {"giu": [1, None]}}
        r2 = c.post("/v1/chat/completions", json=than, headers=QT)
        assert r2.status_code == 200 and r2.json()["choices"][0]["message"]["content"] == "từ xa"
        nhan = xa.nhat_ky[-1]
        assert nhan["than"] == dict(than, model="qwen-3b") and nhan["auth"] == f"Bearer {KHOA_XA}"
        s = c.post("/v1/chat/completions", json=dict(than, stream=True), headers=QT)
        assert s.status_code == 200 and "data: [DONE]" in s.text
        ds = c.get("/api/modules").text
        sk = c.get("/api/modules/gpu-lan/suc-khoe", headers=QT).text
        for vb in (r.text, ds, sk, (du_lieu / "tu_xa.json").read_text()):
            assert KHOA_XA not in vb
        assert "…xyzw" in ds
        p = du_lieu / "bi_mat" / "gpu-lan.json"
        assert json.loads(p.read_text()) == {"api_key": KHOA_XA} and stat.S_IMODE(p.stat().st_mode) == 0o600
    # Khởi động lại: nạp lại từ tu_xa.json.
    with TestClient(app) as c:
        assert [x["id"] for x in c.get("/v1/models", headers=QT).json()["data"]] == ["lan/qwen-3b"]
        assert c.delete("/api/modules/gpu-lan", headers=QT).status_code == 200
        assert c.get("/v1/models", headers=QT).json()["data"] == []
        assert not (du_lieu / "bi_mat" / "gpu-lan.json").exists()


def test_khong_khoa_thi_khong_gui_authorization(du_lieu, xa):
    xa.khoa = None
    with TestClient(app) as c:
        assert dang_ky(c, xa, api_key=None).status_code == 200
        c.post("/v1/chat/completions", json={"model": "lan/x", "messages": []}, headers=QT)
    assert xa.nhat_ky[-1]["auth"] is None


def test_trung_id_tien_to_va_url_bi_chan(du_lieu, xa, monkeypatch):
    with TestClient(app) as c:
        assert dang_ky(c, xa).status_code == 200
        assert dang_ky(c, xa).status_code == 409
        assert dang_ky(c, xa, id="khac").status_code == 409  # trùng tiền tố
        assert dang_ky(c, xa, id="catalog", tien_to="ct").status_code == 400
        assert dang_ky(c, xa, id="b", tien_to="a/b").status_code == 400
        r = dang_ky(c, xa, id="meta", tien_to="mt", base_url="http://169.254.169.254/v1")
        assert r.status_code == 400 and "169.254" in r.json()["error"]["message"]
        monkeypatch.delenv("WEB2API_TEST_CHO_PHEP_LOOPBACK")
        r = dang_ky(c, xa, id="vong", tien_to="vg")
        assert r.status_code == 400 and "loopback" in r.json()["error"]["message"]
