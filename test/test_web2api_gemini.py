"""Module Gemini API + bộ chuyển tiếp OpenAI dùng chung, đo trên máy chủ Google GIẢ cục bộ.

Module được chép vào <WEB2API_DATA>/modules/gemini-api/ (volume) trước khi lõi khởi động —
đúng chỗ nó nằm khi được cài từ git, không phải trong ảnh.
"""

import importlib.util
import json
import logging
import shutil
import stat
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from gia import GoogleGia, MayChuThat

GOC_GEMINI = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"
KSAI = "AIzaSySAI-bon-000000000000000000000004"
MOI_KHOA = (K1, K2, K3, KSAI)


@pytest.fixture(scope="module")
def _google():
    g = GoogleGia()
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def google(_google):
    _google.hanh_vi.clear()
    _google.nhat_ky.clear()
    _google.models_loi = False
    _google.cho_khuc_2.clear()
    _google.qua_han_cho = False
    return _google


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    shutil.copytree(GOC_GEMINI, tmp_path / "modules" / "gemini-api")
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        yield c


def cai(c, google, khoa, base=None):
    cau_hinh = {"api_keys": list(khoa), "base_url": base if base is not None else google.url}
    return c.post("/api/modules/gemini-api/cai", json={"cau_hinh": cau_hinh}, headers=QT)


def mod(c):
    return c.app.state.so.dang_chay["gemini-api"]


def so_lan_chat(google, khoa=None):
    return sum(1 for x in google.nhat_ky if x["duong"] == "chat" and (khoa is None or x["khoa"] == khoa))


def khong_ro_khoa(*van_ban):
    for vb in van_ban:
        for k in MOI_KHOA:
            assert k not in vb, f"khoá {k[-6:]} bị lộ"


def tin(model="gemini/gemini-2.5-flash", **them):
    return {"model": model, "messages": [{"role": "user", "content": "xin chào"}], **them}


# ───────────── chuẩn hoá base_url ─────────────

def _nap_gemini():
    # Biên dịch thẳng từ nguồn (như lõi làm): exec_module sẽ ghi __pycache__ vào thư mục module trong repo.
    tep = GOC_GEMINI / "module.py"
    m = importlib.util.module_from_spec(importlib.util.spec_from_file_location("gemini_api_test_rieng", tep))
    exec(compile(tep.read_bytes(), str(tep), "exec"), m.__dict__)
    return m


@pytest.mark.parametrize("vao,ra", [
    ("", "https://generativelanguage.googleapis.com/v1beta/openai"),
    (None, "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("https://proxy.vd.workers.dev", "https://proxy.vd.workers.dev/v1beta/openai"),
    ("https://proxy.vd.workers.dev/", "https://proxy.vd.workers.dev/v1beta/openai"),
    ("https://proxy.vd.workers.dev/v1beta", "https://proxy.vd.workers.dev/v1beta/openai"),
    ("https://proxy.vd.workers.dev/v1beta/openai/", "https://proxy.vd.workers.dev/v1beta/openai"),
])
def test_chuan_hoa_base_url(vao, ra):
    assert _nap_gemini().chuan_hoa_base_url(vao) == ra


def test_base_url_sai_luoc_do_bi_tu_choi():
    with pytest.raises(ValueError):
        _nap_gemini().chuan_hoa_base_url("ftp://x")


# ───────────── cài ─────────────

def test_cai_khoa_tot_xau_lan_lon(client, google, du_lieu, caplog):
    google.hanh_vi.update({K1: {"kieu": "tot"}, KSAI: {"kieu": "401"}})
    with caplog.at_level(logging.DEBUG):
        r = cai(client, google, [K1, KSAI])
        ds = client.get("/api/modules").text
        catalog = client.get("/api/modules/catalog").text
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["loi"] is None
    kq = {x["khoa"]: x for x in j["ket_qua_tung_khoa"]}
    assert kq["…" + K1[-4:]]["ok"] is True and kq["…" + K1[-4:]]["so_model"] == 2
    assert kq["…" + KSAI[-4:]]["ok"] is False and "401" in kq["…" + KSAI[-4:]]["loi"]
    assert "cau_hinh_luu" not in j
    # Bí mật: chỉ khoá TỐT được lưu, tệp 0600; modules.json công khai chỉ có dạng che.
    p = du_lieu / "bi_mat" / "gemini-api.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert json.loads(p.read_text())["api_keys"] == [K1]
    cong_khai = (du_lieu / "modules.json").read_text()
    assert "…" + K1[-4:] in cong_khai
    khong_ro_khoa(r.text, cong_khai, ds, catalog, caplog.text)
    # Máy chủ giả NHẮC LẠI khoá trong lỗi 401 — phản hồi cài vẫn không được chứa nó (đã kiểm ở trên).
    assert any(x["khoa"] == KSAI for x in google.nhat_ky)


def test_cai_tat_ca_xau_khong_luu(client, google, du_lieu):
    r = cai(client, google, [KSAI])
    assert r.status_code == 400
    assert r.json()["ok"] is False and r.json()["loi"]
    assert not (du_lieu / "modules.json").exists()
    assert not (du_lieu / "bi_mat" / "gemini-api.json").exists()
    assert "gemini-api" not in client.app.state.so.dang_chay
    khong_ro_khoa(r.text)


@pytest.mark.parametrize("kieu", ["403_vung", "400_vung"])
def test_cai_bi_chan_vung_goi_y_proxy(client, google, kieu):
    google.hanh_vi[K1] = {"kieu": kieu}
    j = cai(client, google, [K1]).json()
    assert j["ok"] is False
    assert "chặn vùng" in j["loi"] and "base_url" in j["loi"]


def test_cai_khong_nhap_khoa(client, google):
    j = cai(client, google, []).json()
    assert j["ok"] is False and "api_keys" in j["loi"]


# ───────────── chat ─────────────

def test_chat_khong_stream_doi_ten_va_giu_truong_la(client, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    assert cai(client, google, [K1]).status_code == 200
    than = {
        "model": "gemini/gemini-3-flash-preview",
        "messages": [
            {"role": "user", "content": "mấy giờ rồi"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "gio", "arguments": "{}"},
                "extra_content": {"google": {"thought_signature": "CsUBAb4+9vu…chữ-ký-thật"}},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "10:00"},
        ],
        "tools": [{"type": "function", "function": {"name": "gio", "parameters": {"type": "object"}}}],
        "x_truong_moi_chua_ai_biet": {"a": [1, 2, {"b": None}]},
    }
    r = client.post("/v1/chat/completions", json=than, headers=QT)
    assert r.status_code == 200, r.text
    assert r.json()["x_truong_la_cua_google"] == {"giu": True}
    assert "content-encoding" not in r.headers  # đã giải nén thì không được báo gzip nữa
    nhan = [x for x in google.nhat_ky if x["duong"] == "chat"][-1]["than"]
    mong = dict(than, model="gemini-3-flash-preview")
    assert nhan == mong  # nguyên văn, kể cả extra_content.google.thought_signature


def test_stream_tung_dong_dung_thu_tu_khong_gom(du_lieu, google):
    google.hanh_vi[K1] = {"kieu": "tot", "cho_khuc_2": True}
    with MayChuThat(app, lifespan="on") as w:
        with httpx.Client(base_url=w.url, timeout=10) as h:
            r = h.post("/api/modules/gemini-api/cai", headers=QT,
                       json={"cau_hinh": {"api_keys": [K1], "base_url": google.url}})
            assert r.status_code == 200, r.text
            dong = []
            with h.stream("POST", "/v1/chat/completions", headers=QT, json=tin(stream=True)) as s:
                assert s.status_code == 200
                assert s.headers["content-type"].startswith("text/event-stream")
                for d in s.iter_lines():
                    dong.append(d)
                    if "Một" in d:
                        # Khúc 1 đã tới tay khách TRƯỚC khi Google giả nhả khúc 2 → không ai gom bộ đệm.
                        google.cho_khuc_2.set()
    assert google.qua_han_cho is False
    co_noi_dung = [d for d in dong if d]
    assert co_noi_dung == [
        'data: {"choices":[{"index":0,"delta":{"content":"Một"}}]}',
        'data: {"choices":[{"index":0,"delta":{"content":"Hai"}}]}',
        "data: [DONE]",
    ]


def test_429_xoay_khoa_nghi_dung_thoi_gian(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    assert cai(client, google, [K1, K2]).status_code == 200
    m = mod(client)
    gio = [1000.0]
    m._ct._dong_ho = lambda: gio[0]
    google.hanh_vi[K1] = {"kieu": "429", "retry_after": "30"}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 200
    assert [x["khoa"] for x in google.nhat_ky if x["duong"] == "chat"] == [K1, K2]
    assert m._ct._nghi[K1] == 1030.0
    # Lượt sau: K1 đang nghỉ → không bị gọi.
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 200 and so_lan_chat(google, K1) == 1
    # Hết khoá: K2 cũng 429 → trả 429 chuẩn OpenAI, không phải 500.
    google.hanh_vi[K2] = {"kieu": "429"}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 429
    loi = r.json()["error"]
    assert loi["type"] == "rate_limit_error" and loi["message"] and "code" in loi
    assert m._ct._nghi[K2] == 1060.0  # không có Retry-After → 60 s
    # Mọi khoá đang nghỉ → 429 ngay, không gọi Google, có Retry-After.
    n = so_lan_chat(google)
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 429 and so_lan_chat(google) == n
    assert int(r.headers["retry-after"]) == 30
    # Hết giờ nghỉ → K1 dùng lại được.
    gio[0] = 1031.0
    google.hanh_vi[K1] = {"kieu": "tot"}
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200


def test_retry_after_co_tran_10_phut(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    m = mod(client)
    m._ct._dong_ho = lambda: 0.0
    google.hanh_vi[K1] = {"kieu": "429", "retry_after": "99999"}
    client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert m._ct._nghi[K1] == 600.0


def thu_tu_chat(google):
    return [x["khoa"] for x in google.nhat_ky if x["duong"] == "chat"]


# 400: Google trả 400 cả cho khoá sai/bị thu hồi → phân xử bằng so sánh giữa các khoá (xem đầu app/chuyen_tiep.py).

def test_400_khoa_sai_doi_khoa_ke_va_danh_dau_khong_lo_khoa(client, google, caplog):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    assert cai(client, google, [K1, K2]).status_code == 200  # /models không tính là «đã phục vụ chat»
    ct = mod(client)._ct
    ct._dong_ho = lambda: 0.0
    google.hanh_vi[K1] = {"kieu": "400_khoa"}
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 200, r.text
    assert thu_tu_chat(google) == [K1, K2]
    assert ct._nghi == {K1: 3600.0}
    assert "…" + K1[-4:] in caplog.text
    khong_ro_khoa(r.text, caplog.text)
    # Nhiều lượt sau (xoay vòng lẽ ra rơi vào K1 ở lượt thứ hai): khoá hỏng không bị gọi lại.
    for _ in range(3):
        assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert so_lan_chat(google, K1) == 1 and so_lan_chat(google, K2) == 4


def test_400_mot_khoa_hong_tra_nguyen_van_da_che(client, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])
    google.hanh_vi[K1] = {"kieu": "400_khoa"}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 400
    loi = r.json()["error"]
    assert loi["message"] == f"API key not valid. Please pass a valid API key. (…{K1[-4:]})"
    assert loi["code"] == "INVALID_ARGUMENT" and loi["type"] == "invalid_request_error"
    khong_ro_khoa(r.text)
    assert so_lan_chat(google) == 1 and mod(client)._ct._nghi == {}  # một khoá: không có gì để so → không đánh dấu


def test_400_moi_khoa_nhu_nhau_la_loi_yeu_cau_khong_danh_dau(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    r = client.post("/v1/chat/completions", json=tin("gemini/loi-400-mang"), headers=QT)
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "Request contains an invalid argument."  # nguyên văn, không thêm chữ
    assert sorted(thu_tu_chat(google)) == sorted([K1, K2])  # mỗi khoá đúng một lần
    assert mod(client)._ct._nghi == {}
    # Yêu cầu đúng thì cả hai khoá vẫn phục vụ.
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert thu_tu_chat(google)[-2:] in ([K1, K2], [K2, K1])


def test_400_khoa_da_chay_tot_tra_ngay_roi_ve_chua_ro(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    ct = mod(client)._ct
    ct._dong_ho = lambda: 0.0
    for _ in range(2):  # K1 rồi K2 phục vụ thành công
        assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert ct._chat_duoc == {K1, K2}
    ct._vi_tri = 0
    n = so_lan_chat(google)
    r = client.post("/v1/chat/completions", json=tin("gemini/loi-400"), headers=QT)
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_request_error"
    assert thu_tu_chat(google)[n:] == [K1]  # trả ngay, không đổi khoá
    assert ct._nghi == {}
    # K1 về «chưa rõ»: nếu nó bị thu hồi SAU khi đã chạy tốt thì lần 400 kế được phân xử, không kẹt mãi.
    google.hanh_vi[K1] = {"kieu": "400_khoa"}
    ct._vi_tri = 0
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert thu_tu_chat(google)[n + 1:] == [K1, K2]
    assert ct._nghi == {K1: 3600.0}


def test_stream_400_khoa_sai_doi_khoa_truoc_byte_dau(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    mod(client)._ct._dong_ho = lambda: 0.0
    google.hanh_vi[K1] = {"kieu": "400_khoa"}
    r = client.post("/v1/chat/completions", json=tin(stream=True), headers=QT)
    assert r.status_code == 200
    assert "data: [DONE]" in r.text
    khong_ro_khoa(r.text)
    assert thu_tu_chat(google) == [K1, K2]
    assert mod(client)._ct._nghi == {K1: 3600.0}


def test_400_nghi_ngo_khong_tieu_luot_thu_them_cua_5xx(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}, K3: {"kieu": "tot"}})
    cai(client, google, [K1, K2, K3])
    mod(client)._ct._dong_ho = lambda: 0.0
    google.hanh_vi.update({K1: {"kieu": "400_khoa"}, K2: {"kieu": "500"}})
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 200
    assert thu_tu_chat(google) == [K1, K2, K3]
    assert mod(client)._ct._nghi == {K1: 3600.0}  # 5xx không phải lỗi của khoá


def test_chan_vung_400_trong_chat_tra_ngay_khong_danh_dau(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    google.hanh_vi.update({K1: {"kieu": "400_vung"}, K2: {"kieu": "400_vung"}})
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 400 and "chặn vùng" in r.json()["error"]["message"]
    assert so_lan_chat(google) == 1 and mod(client)._ct._nghi == {}


def test_loi_dang_mang_duoc_chuan_hoa(client, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])
    r = client.post("/v1/chat/completions", json=tin("gemini/loi-400-mang"), headers=QT)
    assert r.status_code == 400
    j = r.json()
    assert isinstance(j, dict) and j["error"]["message"] == "Request contains an invalid argument."
    assert j["error"]["code"] == "INVALID_ARGUMENT"


def test_401_danh_dau_hong_thu_khoa_ke_va_che_khoa(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    m = mod(client)
    m._ct._dong_ho = lambda: 0.0
    google.hanh_vi[K1] = {"kieu": "401"}
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    assert m._ct._nghi[K1] == 3600.0
    m._ct._nghi.clear()
    google.hanh_vi[K2] = {"kieu": "401"}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 401
    khong_ro_khoa(r.text)
    assert "…" in r.json()["error"]["message"]


def test_5xx_chi_thu_them_mot_khoa(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}, K3: {"kieu": "tot"}})
    cai(client, google, [K1, K2, K3])
    for k in (K1, K2, K3):
        google.hanh_vi[k] = {"kieu": "500"}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 500
    assert so_lan_chat(google) == 2
    khong_ro_khoa(r.text)
    assert mod(client)._ct._nghi == {}  # 5xx không phải lỗi của khoá


def test_phan_hoi_cham_thanh_504(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    mod(client)._ct.TIMEOUT_DOC = 0.3
    google.hanh_vi.update({K1: {"kieu": "cham", "giay": 1.5}, K2: {"kieu": "cham", "giay": 1.5}})
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 504
    assert r.json()["error"]["message"]
    assert so_lan_chat(google) == 2


def test_chan_vung_trong_chat_khong_danh_dau_khoa(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    google.hanh_vi.update({K1: {"kieu": "403_vung"}, K2: {"kieu": "403_vung"}})
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 403
    assert "chặn vùng" in r.json()["error"]["message"]
    assert so_lan_chat(google) == 1 and mod(client)._ct._nghi == {}


def test_stream_429_doi_khoa_truoc_byte_dau(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    google.hanh_vi[K1] = {"kieu": "429", "retry_after": "5"}
    r = client.post("/v1/chat/completions", json=tin(stream=True), headers=QT)
    assert r.status_code == 200
    assert "Một" in r.text and "[DONE]" in r.text
    assert [x["khoa"] for x in google.nhat_ky if x["duong"] == "chat"] == [K1, K2]


# ───────────── models / sức khoẻ ─────────────

def test_models_bo_tien_to_dem_va_dung_ban_cu_khi_loi(client, google, caplog):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])
    m = mod(client)
    gio = [0.0]
    m._ct._dong_ho = lambda: gio[0]
    ids = [x["id"] for x in client.get("/v1/models", headers=QT).json()["data"]]
    assert ids == ["gemini/gemini-2.5-flash", "gemini/gemini-3-flash-preview"]
    n = sum(1 for x in google.nhat_ky if x["duong"] == "models")
    gio[0] = 300.0
    client.get("/v1/models", headers=QT)
    assert sum(1 for x in google.nhat_ky if x["duong"] == "models") == n  # còn trong 10 phút → dùng đệm
    gio[0] = 700.0
    google.models_loi = True
    with caplog.at_level(logging.WARNING):
        ids2 = [x["id"] for x in client.get("/v1/models", headers=QT).json()["data"]]
    assert ids2 == ids
    assert sum(1 for x in google.nhat_ky if x["duong"] == "models") == n + 1
    assert "bản đệm" in caplog.text


def test_suc_khoe_khong_goi_chat_va_che_khoa(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])
    mod(client)._ct._nghi[K2] = 4102444800.0  # năm 2100
    google.nhat_ky.clear()
    r = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    ds = {x["khoa"]: x for x in j["khoa"]}
    assert ds["…" + K1[-4:]]["ok"] and ds["…" + K1[-4:]]["dang_nghi_den"] is None
    assert ds["…" + K2[-4:]]["dang_nghi_den"].startswith("2100-01-01")
    assert all(set(x) >= {"ok", "do_tre_ms", "so_model", "loi", "dang_nghi_den"} for x in j["khoa"])
    assert so_lan_chat(google) == 0
    khong_ro_khoa(r.text)


def test_bo_dem_dung_va_dang_dung_va_thu_tu_tung_khoa(client, google, caplog):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])

    # Ban đầu: cả 2 khoá chưa dùng, dang_dung là False, thứ tự #1 và #2
    r0 = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r0.status_code == 200
    j0 = r0.json()
    assert [x["thu_tu"] for x in j0["khoa"]] == [1, 2]
    assert all(x["dang_dung"] is False for x in j0["khoa"])
    assert all(x["dung_cuoi"] is None and x["so_429"] == 0 for x in j0["khoa"])
    hm1_0 = next(h for h in j0["khoa"][0]["han_muc"] if h["ten"] == "chat")
    assert hm1_0["da_dung"] == 0 and hm1_0["trang_thai"] == "chua_ro"

    # Lượt 1: chat thành công qua K1
    with caplog.at_level(logging.INFO):
        r_chat1 = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r_chat1.status_code == 200

    r1 = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r1.status_code == 200
    j1 = r1.json()
    k1_info = j1["khoa"][0]
    k2_info = j1["khoa"][1]
    assert k1_info["thu_tu"] == 1 and k2_info["thu_tu"] == 2
    # K1 là khoá vừa phục vụ gần nhất ("đang dùng")
    assert k1_info["dang_dung"] is True and k2_info["dang_dung"] is False
    assert k1_info["dung_cuoi"] is not None and k2_info["dung_cuoi"] is None
    hm_chat1 = next(h for h in k1_info["han_muc"] if h["ten"] == "chat")
    assert hm_chat1["da_dung"] == 1 and hm_chat1["trang_thai"] == "dung_duoc"

    # Kiểm khoá (GET /models) và làm mới sức khoẻ KHÔNG đổi "đang dùng"
    client.get("/v1/models", headers=QT)
    r1b = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r1b.json()["khoa"][0]["dang_dung"] is True

    # Lượt 2: chat tiếp xoay sang K2
    r_chat2 = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r_chat2.status_code == 200

    r2 = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    j2 = r2.json()
    # K2 giờ là "đang dùng", K1 không còn
    assert j2["khoa"][0]["dang_dung"] is False and j2["khoa"][1]["dang_dung"] is True
    hm_k1_2 = next(h for h in j2["khoa"][0]["han_muc"] if h["ten"] == "chat")
    hm_k2_2 = next(h for h in j2["khoa"][1]["han_muc"] if h["ten"] == "chat")
    assert hm_k1_2["da_dung"] == 1 and hm_k2_2["da_dung"] == 1
    assert j2["khoa"][1]["dung_cuoi"] is not None

    # Khoá không lộ trong phản hồi hay nhật ký
    khong_ro_khoa(r2.text)
    for k in MOI_KHOA:
        assert k not in caplog.text


def test_bo_dem_ghi_nhan_429_va_trang_thai_het_han_muc(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1, K2])

    # K1 sau đó gặp 429 trong lúc chat
    google.hanh_vi[K1] = {"kieu": "429", "retry_after": 60}
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 200  # K1 gặp 429, xoay sang K2 thành công

    r_sk = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r_sk.status_code == 200
    j = r_sk.json()
    k1_info = j["khoa"][0]
    k2_info = j["khoa"][1]

    # K1 ghi nhận số lần 429 và lần 429 cuối
    assert k1_info["so_429"] == 1
    assert k1_info["lan_429_cuoi"] is not None
    assert k1_info["dang_nghi_den"] is not None
    hm_k1_chat = next(h for h in k1_info["han_muc"] if h["ten"] == "chat")
    assert hm_k1_chat["trang_thai"] == "het_han_muc"
    assert hm_k1_chat["ma"] == 429
    assert hm_k1_chat["hoi_luc"] == k1_info["dang_nghi_den"]

    # K2 phục vụ thành công -> đang dùng
    assert k2_info["dang_dung"] is True
    assert k2_info["so_429"] == 0
    hm_k2_chat = next(h for h in k2_info["han_muc"] if h["ten"] == "chat")
    assert hm_k2_chat["da_dung"] == 1 and hm_k2_chat["trang_thai"] == "dung_duoc"


def test_han_muc_co_truong_toi_da_nhung_khong_bia_tran(client, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])

    # Mặc định Google không có trần: toi_da là None
    r = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r.status_code == 200
    j = r.json()
    hm = j["khoa"][0]["han_muc"]
    assert all("toi_da" in h for h in hm)
    assert all(h["toi_da"] is None for h in hm)

    # Dùng thật rồi vẫn KHÔNG có trần: bộ đếm từ lúc khởi động không phải hạn mức của nhà cung cấp — không bịa con số.
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200
    hm2 = {h["ten"]: h for h in client.get("/api/modules/gemini-api/suc-khoe", headers=QT).json()["khoa"][0]["han_muc"]}
    assert list(hm2) == ["chat", "anh", "video"]
    assert hm2["chat"]["da_dung"] == 1 and all(h["toi_da"] is None for h in hm2.values())


def test_suc_khoe_module_tu_xa_va_chua_lien_ket(du_lieu, monkeypatch):
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    from gia import OpenAIGia
    from test_web2api_tu_xa import KHOA_XA

    # 1. Module chưa liên kết: trả 404 có lý do rõ, không lỗi 500
    with TestClient(app) as c:
        r_cl = c.get("/api/modules/gemini-api/suc-khoe", headers=QT)
        assert r_cl.status_code == 404
        assert "chưa có khoá/cấu hình" in r_cl.json()["error"]["message"]

    # 2. Module từ xa: có sức khoẻ với đầy đủ bộ đếm và thứ tự #1
    g_xa = OpenAIGia(khoa=KHOA_XA)
    with MayChuThat(g_xa.app) as s_xa, TestClient(app) as c:
        r_dk = c.post("/api/modules/tu-xa", json={
            "id": "gpu-lan", "ten": "Model GPU trong LAN", "tien_to": "lan",
            "base_url": s_xa.url + "/v1", "api_key": KHOA_XA,
        }, headers=QT)
        assert r_dk.status_code == 200

        r_sk = c.get("/api/modules/gpu-lan/suc-khoe", headers=QT)
        assert r_sk.status_code == 200
        j_sk = r_sk.json()
        assert j_sk["ok"] is True
        khoa_entry = j_sk["khoa"][0]
        assert khoa_entry["thu_tu"] == 1
        assert khoa_entry["dang_dung"] is False
        assert khoa_entry["so_429"] == 0
        assert "han_muc" in khoa_entry
        assert khoa_entry["han_muc"][0]["ten"] == "chat"
        assert khoa_entry["han_muc"][0]["toi_da"] is None

        # Chat thử qua module từ xa -> lượt dùng và đang dùng cập nhật
        r_chat = c.post("/v1/chat/completions", json={
            "model": "lan/qwen-3b", "messages": [{"role": "user", "content": "hi"}]
        }, headers=QT)
        assert r_chat.status_code == 200

        r_sk2 = c.get("/api/modules/gpu-lan/suc-khoe", headers=QT)
        assert r_sk2.json()["khoa"][0]["dang_dung"] is True
        assert r_sk2.json()["khoa"][0]["han_muc"][0]["da_dung"] == 1


# ───────────── định tuyến, gỡ, khởi động lại ─────────────

def test_model_khong_thuoc_module_nao_404(client, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])
    for model in ("khong-co/abc", "abc"):
        r = client.post("/v1/chat/completions", json=tin(model), headers=QT)
        assert r.status_code == 404
        loi = r.json()["error"]
        assert model in loi["message"] and "gemini/" in loi["message"]


def test_module_chua_lien_ket_404_ro(client):
    # Mã đã nằm trong thư mục dữ liệu = ĐÃ CÀI; chưa có khoá = chưa liên kết (KE-HOACH-CAI-MODULE.md 3.1).
    r = client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert r.status_code == 404
    assert "chưa có khoá" in r.json()["error"]["message"] and "gemini-api" in r.json()["error"]["message"]


def test_v1_models_la_json_khong_roi_vao_tep_tinh(client):
    r = client.get("/v1/models", headers=QT)
    assert r.status_code == 200 and r.json() == {"object": "list", "data": []}


def test_go_module(client, google, du_lieu):
    google.hanh_vi[K1] = {"kieu": "tot"}
    cai(client, google, [K1])
    client.get("/v1/models", headers=QT)
    cu = mod(client)
    assert cu._ct._khach is not None
    r = client.delete("/api/modules/gemini-api", headers=QT)
    assert r.status_code == 200, r.text
    assert cu._ct._khach is None  # dung() đã đóng kết nối
    assert json.loads((du_lieu / "modules.json").read_text())["modules"] == []
    assert not (du_lieu / "bi_mat" / "gemini-api.json").exists()
    assert client.get("/v1/models", headers=QT).json()["data"] == []
    assert client.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 404


def test_khoi_dong_lai_nap_tu_kho_khong_goi_mang(du_lieu, google):
    google.hanh_vi[K1] = {"kieu": "tot"}
    with TestClient(app) as c:
        assert cai(c, google, [K1]).status_code == 200
    n = len(google.nhat_ky)
    with TestClient(app) as c:
        assert len(google.nhat_ky) == n  # nạp lúc khởi động không gọi Google
        assert c.post("/v1/chat/completions", json=tin(), headers=QT).status_code == 200


def test_cai_lai_thay_ban_dang_chay(client, google):
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    cai(client, google, [K1])
    cu = mod(client)
    client.get("/v1/models", headers=QT)
    cai(client, google, [K2])
    assert mod(client) is not cu and cu._ct._khach is None
    client.post("/v1/chat/completions", json=tin(), headers=QT)
    assert [x for x in google.nhat_ky if x["duong"] == "chat"][-1]["khoa"] == K2


def test_chay_song_song_khong_lam_hong(du_lieu, google):
    # Hai yêu cầu đồng thời qua uvicorn thật: vòng xoay khoá không được trộn trạng thái.
    google.hanh_vi.update({K1: {"kieu": "tot"}, K2: {"kieu": "tot"}})
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=10) as h:
        h.post("/api/modules/gemini-api/cai", headers=QT, json={"cau_hinh": {"api_keys": [K1, K2], "base_url": google.url}})
        kq = []
        luong = [threading.Thread(target=lambda: kq.append(h.post("/v1/chat/completions", headers=QT, json=tin()).status_code))
                 for _ in range(6)]
        [t.start() for t in luong]
        [t.join() for t in luong]
    assert kq == [200] * 6
    assert {so_lan_chat(google, K1), so_lan_chat(google, K2)} == {3}
