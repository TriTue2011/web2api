"""Tạo ẢNH và VIDEO qua đường chuẩn OpenAI của lõi (/v1/images/generations, /v1/videos…) với module gemini-api.

Google, máy chủ tệp và GitHub đều là máy chủ GIẢ cục bộ (gia.py); khoá là chuỗi giả đã có trong allowlist.

GIẢ ĐỊNH CHƯA ĐO: Google từ chối khoá hạng miễn phí cho ảnh/video bằng mã 400/403/429 (trang giá ghi
«Not available» cho hạng miễn phí, nhưng dạng lỗi thật chưa đo được — không có khoá thật). Vì vậy mọi quyết
định ở đây theo MÃ TRẠNG THÁI và so sánh giữa các khoá, không theo chữ trong thông điệp.
"""

import base64
import hashlib
import json
import logging
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from gia import GoogleGia, MayChuThat, MayTepGia, du_lieu_video, ghi_thu_muc, tep_echo

GOC_GEMINI = Path(__file__).resolve().parents[1] / "modules" / "gemini-api"
KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"
MOI_KHOA = (K1, K2, K3)
NGAY = 24 * 3600


@pytest.fixture(scope="module")
def _may():
    g = GoogleGia()
    t = MayTepGia(g)
    with MayChuThat(g.app) as s, MayChuThat(t.app) as s2:
        g.url, t.url = s.url, s2.url
        yield g, t


@pytest.fixture
def google(_may):
    g, t = _may
    g.hanh_vi.clear()
    g.nhat_ky.clear()
    g.dat_lai_anh_video()
    g.hanh_vi.update({k: {"kieu": "tot"} for k in MOI_KHOA})
    t.nhat_ky.clear()
    return g


@pytest.fixture
def may_tep(_may):
    return _may[1]


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    shutil.copytree(GOC_GEMINI, tmp_path / "modules" / "gemini-api")
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        yield c


def cai(c, google, khoa):
    r = c.post("/api/modules/gemini-api/cai", headers=QT,
               json={"cau_hinh": {"api_keys": list(khoa), "base_url": google.url}})
    assert r.status_code == 200, r.text
    return r


def ct(c):
    return c.app.state.so.dang_chay["gemini-api"]._ct


def lan(google, duong, khoa=None):
    return [x for x in google.nhat_ky if x["duong"] == duong and (khoa is None or x["khoa"] == khoa)]


def khong_ro_khoa(*van_ban):
    for vb in van_ban:
        for k in MOI_KHOA:
            assert k not in vb, f"khoá …{k[-6:]} bị lộ"


def anh(model="gemini/gemini-3.1-flash-image", **them):
    return {"model": model, "prompt": "con mèo đội mũ", "response_format": "b64_json", "n": 1, **them}


def vid(model="gemini/veo-3.1-fast-generate-preview", **them):
    return {"model": model, "prompt": "thác nước quay bằng flycam", **them}


def suc_khoe_kn(c, khoa, kn):
    j = c.get("/api/modules/gemini-api/suc-khoe", headers=QT).json()
    return next(x for x in j["khoa"] if x["khoa"] == "…" + khoa[-4:])["kha_nang"][kn]


# ───────────── manifest: trường mở `cung_cap` ─────────────

def test_manifest_gemini_khai_cung_cap_va_catalog_tra_ra(client):
    m = json.loads((GOC_GEMINI / "manifest.json").read_text(encoding="utf-8"))
    assert m["cung_cap"] == ["chat", "anh", "video"]
    assert m["phien_ban"] == "0.2.0"
    # Không đổi phần quyền: đổi là bản cập nhật bị coi «xin thêm quyền» và không tự cập nhật được.
    assert m["yeu_cau_goi"] == ["httpx"] and m["tai_nguyen"]["mang_ra"] == ["generativelanguage.googleapis.com"]
    cat = next(x for x in client.get("/api/modules/catalog", headers=QT).json()["modules"] if x["id"] == "gemini-api")
    assert cat["cung_cap"] == ["chat", "anh", "video"]


@pytest.mark.parametrize("sai", ["anh", ["Anh"], ["anh", "anh"], [1], ["co dau cach"], {"anh": True}])
def test_cung_cap_sai_mau_bi_tu_choi(du_lieu, sai):
    tep = tep_echo()
    m = json.loads(tep["manifest.json"])
    m["cung_cap"] = sai
    tep["manifest.json"] = json.dumps(m).encode()
    ghi_thu_muc(du_lieu / "modules" / "echo", tep)
    with TestClient(app) as c:
        j = c.get("/api/modules/catalog", headers=QT).json()
    loi = {x["thu_muc"].rsplit("/", 1)[-1]: x["ly_do"] for x in j["loi"]}
    assert "cung_cap" in loi["echo"]


def test_cung_cap_ma_moi_chua_ai_biet_van_hop_le(du_lieu):
    # Trường MỞ: lõi kiểm mẫu, không liệt kê mã hợp lệ — mã khả năng mới không cần sửa lõi.
    tep = tep_echo()
    m = json.loads(tep["manifest.json"])
    m["cung_cap"] = ["chat", "nhung_van_ban", "am_thanh_2"]
    tep["manifest.json"] = json.dumps(m).encode()
    ghi_thu_muc(du_lieu / "modules" / "echo", tep)
    with TestClient(app) as c:
        assert "echo" in c.app.state.so.catalog


# ───────────── quyền, định tuyến, 404/501 ─────────────

@pytest.mark.parametrize("phuong_thuc,duong", [
    ("POST", "/v1/images/generations"), ("POST", "/v1/videos"),
    ("GET", "/v1/videos/gemini:abc.12345678"), ("GET", "/v1/videos/gemini:abc.12345678/content"),
])
def test_khong_khoa_quan_tri_401(client, phuong_thuc, duong):
    r = client.request(phuong_thuc, duong, json=anh())
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"


def test_module_chua_lien_ket_404(client):
    for r in (client.post("/v1/images/generations", json=anh(), headers=QT),
              client.post("/v1/videos", json=vid(), headers=QT),
              client.get("/v1/videos/gemini:abc.12345678", headers=QT),
              client.get("/v1/videos/gemini:abc.12345678/content", headers=QT)):
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "module_not_configured" and "chưa có khoá" in r.json()["error"]["message"]


def test_tien_to_la_va_ma_video_hong_404(client, google):
    cai(client, google, [K1])
    r = client.post("/v1/images/generations", json=anh("khong-co/x"), headers=QT)
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    for ma in ("khongcohaicham", "khong-co:abc.12345678", "gemini:", "gemini:abc", "gemini:!!!.12345678"):
        r = client.get(f"/v1/videos/{ma}", headers=QT)
        assert r.status_code == 404, (ma, r.text)
    assert lan(google, "video_hoi") == []


def test_module_khong_co_kha_nang_501(du_lieu):
    ghi_thu_muc(du_lieu / "modules" / "echo", tep_echo())
    with TestClient(app) as c:
        assert c.post("/api/modules/echo/cai", json={"cau_hinh": {}}, headers=QT).status_code == 200
        r = c.post("/v1/images/generations", json=anh("echo/x"), headers=QT)
        assert r.status_code == 501 and "echo" in r.json()["error"]["message"]
        assert r.json()["error"]["code"] == "capability_not_supported"
        assert c.post("/v1/videos", json=vid("echo/x"), headers=QT).status_code == 501
        assert c.get("/v1/videos/echo:abc.12345678", headers=QT).status_code == 501
        assert c.get("/v1/videos/echo:abc.12345678/content", headers=QT).status_code == 501


def test_multipart_video_co_tep_415_khong_tep_nhu_json(client, google):
    # SDK openai gửi videos.create bằng multipart: trường chữ đi như thân JSON; phần tệp chưa có hợp đồng → 415.
    cai(client, google, [K1])
    r = client.post("/v1/videos", headers=QT, data={"model": "gemini/veo-3.1-generate-preview", "prompt": "x"},
                    files={"input_reference": ("a.png", b"\x89PNG", "image/png")})
    assert r.status_code == 415 and "tệp tham chiếu" in r.json()["error"]["message"]
    assert lan(google, "video_tao") == []
    ranh = "ranh-gioi-0123"
    body = b"".join(f'--{ranh}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in vid(seconds="8", size="720x1280").items()) + f"--{ranh}--\r\n".encode()
    r = client.post("/v1/videos", headers={**QT, "content-type": f"multipart/form-data; boundary={ranh}"}, content=body)
    assert r.status_code == 200 and r.json()["id"].startswith("gemini:"), r.text
    assert lan(google, "video_tao")[-1]["than"] == {
        "instances": [{"prompt": "thác nước quay bằng flycam"}],
        "parameters": {"durationSeconds": 8, "aspectRatio": "9:16"}}


# ───────────── ảnh ─────────────

def test_anh_thanh_cong_doi_ten_giu_truong_khoa_chi_trong_header(client, google, caplog):
    cai(client, google, [K1])
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/images/generations", headers=QT,
                        json=anh(aspect_ratio="16:9", generation_config={"x": 1}))
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["data"][0]["b64_json"]) == b"PNG-GIA-gemini-3.1-flash-image-0"
    goi = lan(google, "anh")[-1]
    assert goi["than"] == {**anh(aspect_ratio="16:9", generation_config={"x": 1}), "model": "gemini-3.1-flash-image"}
    assert goi["kieu_khoa"] == "bearer" and "key=" not in goi["url"]
    assert suc_khoe_kn(client, K1, "anh") == {"trang_thai": "dung_duoc"}
    assert suc_khoe_kn(client, K1, "video") == {"trang_thai": "chua_ro"}
    khong_ro_khoa(r.text, caplog.text)


def test_khoa_mien_phi_bi_tu_choi_thu_khoa_ke_nho_24h_khong_anh_huong_chat(client, google, caplog):
    cai(client, google, [K1, K2])
    gio = [1000.0]
    ct(client)._dong_ho = lambda: gio[0]
    google.anh[K1] = {"kieu": "tu_choi", "ma": 403}
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/images/generations", json=anh(), headers=QT)
        assert r.status_code == 200, r.text
        assert [x["khoa"] for x in lan(google, "anh")] == [K1, K2]
        kn = suc_khoe_kn(client, K1, "anh")
        assert kn["trang_thai"] == "bi_tu_choi" and kn["ma"] == 403
        assert kn["thu_lai_luc"].startswith("1970-01-02")  # 1000 s + 24 giờ
        assert suc_khoe_kn(client, K2, "anh") == {"trang_thai": "dung_duoc"}
        # Lượt sau không gọi K1 cho ảnh nữa…
        for _ in range(3):
            assert client.post("/v1/images/generations", json=anh(), headers=QT).status_code == 200
        assert len(lan(google, "anh", K1)) == 1
        # …nhưng CHAT của cùng khoá K1 vẫn chạy: nghỉ theo (khoá, khả năng), không theo khoá.
        for _ in range(2):
            assert client.post("/v1/chat/completions", headers=QT, json={
                "model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "chào"}]}).status_code == 200
        assert len(lan(google, "chat", K1)) == 1 and ct(client)._nghi == {}
        assert suc_khoe_kn(client, K1, "video") == {"trang_thai": "chua_ro"}  # video chưa thử: không suy từ ảnh
        # Hết 24 giờ: thử lại K1 (khoá được nâng lên trả phí thì tự dùng được).
        gio[0] = 1000.0 + NGAY + 1
        google.anh[K1] = {"kieu": "tot"}
        client.post("/v1/images/generations", json=anh(), headers=QT)
        client.post("/v1/images/generations", json=anh(), headers=QT)
        assert len(lan(google, "anh", K1)) == 2
        assert suc_khoe_kn(client, K1, "anh") == {"trang_thai": "dung_duoc"}
    khong_ro_khoa(r.text, caplog.text)


def test_moi_khoa_bi_tu_choi_bao_ro_han_mien_phi(client, google, caplog):
    cai(client, google, [K1, K2])
    google.anh.update({K1: {"kieu": "tu_choi", "ma": 403}, K2: {"kieu": "tu_choi", "ma": 401}})
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/images/generations", json=anh(), headers=QT)
        assert r.status_code == 403, r.text
        loi = r.json()["error"]
        assert loi["type"] == "permission_error" and loi["code"] == "no_key_permitted"
        assert "miễn phí" in loi["message"] and "trả phí" in loi["message"] and "tạo ảnh" in loi["message"]
        assert "403" in loi["message"] and "401" in loi["message"]
        assert "…" + K1[-4:] in loi["message"]
        # Lượt sau: mọi khoá đang bị từ chối cho ảnh → trả ngay, không gọi Google (không tốn lượt).
        n = len(lan(google, "anh"))
        r2 = client.post("/v1/images/generations", json=anh(), headers=QT)
        assert r2.status_code == 403 and len(lan(google, "anh")) == n
        assert "miễn phí" in r2.json()["error"]["message"]
    khong_ro_khoa(r.text, r2.text, caplog.text)


def test_429_khoa_da_dung_duoc_nghi_theo_retry_after(client, google):
    cai(client, google, [K1, K2])
    ct(client)._dong_ho = lambda: 0.0
    for _ in range(2):  # cả hai khoá từng tạo ảnh được → 429 về sau là hết hạn mức thật, không phải bị từ chối
        assert client.post("/v1/images/generations", json=anh(), headers=QT).status_code == 200
    google.anh.update({K1: {"kieu": "tu_choi", "ma": 429, "retry_after": "30"},
                       K2: {"kieu": "tu_choi", "ma": 429}})
    r = client.post("/v1/images/generations", json=anh(), headers=QT)
    # Mọi khoá chỉ hết hạn mức → 429 chuẩn OpenAI có Retry-After (khoá trả phí cũng gặp 429 khi dồn tải).
    assert r.status_code == 429 and int(r.headers["retry-after"]) == 30
    assert r.json()["error"]["code"] == "rate_limit_exceeded"
    assert ct(client)._kn[(K1, "anh")]["den"] == 30.0 and ct(client)._kn[(K2, "anh")]["den"] == 60.0
    assert suc_khoe_kn(client, K1, "anh")["trang_thai"] == "bi_tu_choi"


def test_429_khoa_chua_tung_duoc_nghi_24h_bao_403_chat_van_chay(client, google, caplog):
    # «Hạn mức = 0» của hạng miễn phí rất có thể là 429 (GIẢ ĐỊNH, chưa đo): khoá chưa từng làm được việc đó mà
    # gặp 429 thì coi là bị từ chối 24 giờ — không nã lại mỗi phút, không trả 429 làm khách tưởng chỉ quá tải.
    cai(client, google, [K1, K2])
    gio = [0.0]
    ct(client)._dong_ho = lambda: gio[0]
    google.anh.update({K1: {"kieu": "tu_choi", "ma": 429, "retry_after": "30"}, K2: {"kieu": "tu_choi", "ma": 429}})
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/images/generations", json=anh(), headers=QT)
        assert r.status_code == 403 and r.json()["error"]["code"] == "no_key_permitted", r.text
        assert "retry-after" not in r.headers and "429" in r.json()["error"]["message"]
        assert "miễn phí" in r.json()["error"]["message"]
        assert ct(client)._kn[(K1, "anh")]["den"] == NGAY and ct(client)._kn[(K2, "anh")]["den"] == NGAY
        gio[0] = 3600.0  # một giờ sau vẫn không gọi Google cho ảnh
        n = len(lan(google, "anh"))
        assert client.post("/v1/images/generations", json=anh(), headers=QT).status_code == 403
        assert len(lan(google, "anh")) == n
        assert client.post("/v1/chat/completions", headers=QT, json={
            "model": "gemini/gemini-2.5-flash", "messages": [{"role": "user", "content": "chào"}]}).status_code == 200
    khong_ro_khoa(r.text, caplog.text)


def test_400_cua_mot_khoa_ma_khoa_sau_duoc_thi_danh_dau_khoa_truoc(client, google):
    cai(client, google, [K1, K2])
    google.anh[K1] = {"kieu": "tu_choi", "ma": 400}
    assert client.post("/v1/images/generations", json=anh(), headers=QT).status_code == 200
    kn = suc_khoe_kn(client, K1, "anh")
    assert kn["trang_thai"] == "bi_tu_choi" and kn["ma"] == 400


def test_400_cho_moi_khoa_la_loi_yeu_cau_khong_danh_dau(client, google):
    # Yêu cầu sai (vd tỉ lệ khung không hợp lệ) trả 400 với MỌI khoá: không được khoá cả nhà khỏi ảnh 24 giờ.
    cai(client, google, [K1, K2])
    r = client.post("/v1/images/generations", json=anh("gemini/loi-400"), headers=QT)
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "Invalid aspect ratio" in msg and "miễn phí" in msg
    assert ct(client)._kn == {}
    assert suc_khoe_kn(client, K1, "anh") == {"trang_thai": "chua_ro"}


def test_400_cua_khoa_da_dung_duoc_tra_ngay(client, google):
    cai(client, google, [K1, K2])
    ct(client)._vi_tri = 0
    assert client.post("/v1/images/generations", json=anh(), headers=QT).status_code == 200   # K1 dùng được
    ct(client)._vi_tri = 0
    n = len(lan(google, "anh"))
    r = client.post("/v1/images/generations", json=anh("gemini/loi-400"), headers=QT)
    assert r.status_code == 400 and len(lan(google, "anh")) == n + 1  # không thử K2
    assert suc_khoe_kn(client, K1, "anh") == {"trang_thai": "dung_duoc"}


def test_chan_vung_khong_danh_dau_khoa_anh(client, google):
    cai(client, google, [K1, K2])
    google.hanh_vi.update({K1: {"kieu": "403_vung"}, K2: {"kieu": "403_vung"}})
    r = client.post("/v1/images/generations", json=anh(), headers=QT)
    assert r.status_code == 403 and "chặn vùng" in r.json()["error"]["message"]
    assert len(lan(google, "anh")) == 1 and ct(client)._kn == {}


# ───────────── video ─────────────

def _tao_va_cho_xong(c, google, **them):
    r = c.post("/v1/videos", json=vid(**them), headers=QT)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["object"] == "video" and v["status"] == "queued" and v["id"].startswith("gemini:")
    for _ in range(google.so_lan_hoi):
        s = c.get(f"/v1/videos/{v['id']}", headers=QT)
        assert s.status_code == 200 and s.json()["status"] == "in_progress", s.text
    s = c.get(f"/v1/videos/{v['id']}", headers=QT)
    assert s.status_code == 200, s.text
    return v, s.json()


def test_video_tao_hoi_xong_tai_stream(client, google, caplog):
    cai(client, google, [K1])
    with caplog.at_level(logging.DEBUG):
        v, xong = _tao_va_cho_xong(client, google, seconds="8", size="720x1280", resolution="720p",
                                   negative_prompt="mờ")
        assert xong["status"] == "completed" and xong["id"] == v["id"]
        assert xong["model"] == "gemini/veo-3.1-fast-generate-preview" and xong["progress"] == 100
        goi = lan(google, "video_tao")[-1]
        assert goi["model"] == "veo-3.1-fast-generate-preview"
        assert goi["than"] == {"instances": [{"prompt": "thác nước quay bằng flycam"}],
                               "parameters": {"durationSeconds": 8, "aspectRatio": "9:16", "resolution": "720p",
                                              "negativePrompt": "mờ"}}
        r = client.get(f"/v1/videos/{v['id']}/content", headers=QT)
    assert r.status_code == 200 and r.headers["content-type"].startswith("video/mp4")
    assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(du_lieu_video("op1", google.co_video)).hexdigest()
    # URI Google trả trỏ máy Google THẬT: module tải qua máy của base_url (proxy), khoá chỉ đi bằng header.
    tai = lan(google, "tai_tep")
    assert tai and all(x["khoa"] == K1 and x["kieu_khoa"] == "x-goog-api-key" for x in tai)
    for x in google.nhat_ky:
        assert "key=" not in x["url"] and K1 not in x["url"]
    khong_ro_khoa(r.text[:1000], caplog.text, v["id"], json.dumps(xong))


def test_video_anh_dau_vao_data_url(client, google):
    cai(client, google, [K1])
    b64 = base64.b64encode(b"\xff\xd8JPEG-GIA").decode()
    r = client.post("/v1/videos", json=vid(image=f"data:image/jpeg;base64,{b64}"), headers=QT)
    assert r.status_code == 200, r.text
    assert lan(google, "video_tao")[-1]["than"]["instances"][0]["image"] == {"bytesBase64Encoded": b64,
                                                                          "mimeType": "image/jpeg"}


def test_video_thieu_prompt_hay_model_la_400(client, google):
    cai(client, google, [K1])
    assert client.post("/v1/videos", json={"model": "gemini/veo-3.1-generate-preview"}, headers=QT).status_code == 400
    for model in ("gemini/../../files/x", "gemini/..", "gemini/a:b", "gemini/"):
        r = client.post("/v1/videos", json=vid(model=model), headers=QT)
        assert r.status_code == 400, (model, r.text)
    assert client.post("/v1/videos", json=vid(seconds="tám"), headers=QT).status_code == 400
    assert lan(google, "video_tao") == []


def test_video_chuyen_huong_khac_may_khong_mang_khoa(client, google, may_tep):
    cai(client, google, [K1])
    google.chuyen_huong_toi = may_tep.url.replace("127.0.0.1", "localhost")  # khác máy (origin) với base_url
    v, xong = _tao_va_cho_xong(client, google)
    r = client.get(f"/v1/videos/{v['id']}/content", headers=QT)
    assert r.status_code == 200 and len(r.content) == google.co_video
    assert may_tep.nhat_ky and all(x["x_goog_api_key"] is None and x["authorization"] is None
                                   for x in may_tep.nhat_ky)


def test_video_uri_o_may_la_khong_mang_khoa(client, google, may_tep):
    # URI trỏ một máy không phải Google cũng không phải base_url: tải KHÔNG kèm khoá (máy lạ không bao giờ nhận khoá).
    cai(client, google, [K1])
    google.goc_uri = may_tep.url
    v, _ = _tao_va_cho_xong(client, google)
    r = client.get(f"/v1/videos/{v['id']}/content", headers=QT)
    assert r.status_code == 404  # máy lạ không có tệp; điều cần chứng minh là nó KHÔNG nhận được khoá
    assert may_tep.nhat_ky and all(x["x_goog_api_key"] is None and x["authorization"] is None
                                   for x in may_tep.nhat_ky)
    assert not lan(google, "tai_tep")
    khong_ro_khoa(r.text)


def test_video_chua_xong_thi_noi_dung_409(client, google):
    cai(client, google, [K1])
    v = client.post("/v1/videos", json=vid(), headers=QT).json()
    r = client.get(f"/v1/videos/{v['id']}/content", headers=QT)
    assert r.status_code == 409 and r.json()["error"]["code"] == "video_not_ready"


@pytest.mark.parametrize("kq", ["loi", "loc"])
def test_video_that_bai_bao_failed(client, google, kq):
    cai(client, google, [K1])
    google.ket_qua_video = kq
    v, xong = _tao_va_cho_xong(client, google)
    assert xong["status"] == "failed" and xong["error"]["message"]
    khong_ro_khoa(json.dumps(xong))
    if kq == "loc":
        assert "bộ lọc" in xong["error"]["message"]


def test_video_khoa_1_tu_choi_khoa_2_tao_va_hoi_dung_khoa_2(client, google):
    cai(client, google, [K1, K2])
    ct(client)._vi_tri = 0
    google.video[K1] = {"kieu": "tu_choi", "ma": 403}
    v, xong = _tao_va_cho_xong(client, google)
    assert xong["status"] == "completed"
    assert {x["khoa"] for x in lan(google, "video_hoi")} == {K2}  # thao tác thuộc dự án của khoá đã tạo
    assert suc_khoe_kn(client, K1, "video")["trang_thai"] == "bi_tu_choi"
    assert suc_khoe_kn(client, K1, "anh") == {"trang_thai": "chua_ro"}  # ảnh không bị kéo theo


def test_video_hoi_duoc_sau_khi_cai_lai_cau_hinh(client, google):
    # Thêm khoá = dựng thể hiện mới (mất bộ nhớ trong) — mã video mang vân tay khoá nên vẫn hỏi đúng khoá.
    cai(client, google, [K1])
    v = client.post("/v1/videos", json=vid(), headers=QT).json()
    cai(client, google, [K2, K1])
    google.thao_tac["op1"]["con_lai"] = 0
    s = client.get(f"/v1/videos/{v['id']}", headers=QT)
    assert s.status_code == 200 and s.json()["status"] == "completed"
    assert {x["khoa"] for x in lan(google, "video_hoi")} == {K1}
    # Bớt khoá đã tạo video → báo rõ, không thử bừa khoá khác.
    cai(client, google, [K2])
    r = client.get(f"/v1/videos/{v['id']}", headers=QT)
    assert r.status_code == 404 and "khoá" in r.json()["error"]["message"]


def test_ma_video_tu_che_khong_doc_duong_tuy_y(client, google):
    # Khách tự chế mã trỏ tới đường khác trên Google: lõi/module phải từ chối TRƯỚC khi gọi Google kèm khoá.
    cai(client, google, [K1])
    v = client.post("/v1/videos", json=vid(), headers=QT).json()
    vt = v["id"].rsplit(".", 1)[1]
    for duong in ("files/abc", "models/x/operations/../../files/abc", "tunedModels/x", "models/../operations/x",
                  "models/x/operations/op1/../../../files/op1:download"):
        ma = base64.urlsafe_b64encode(duong.encode()).decode().rstrip("=")
        r = client.get(f"/v1/videos/gemini:{ma}.{vt}", headers=QT)
        assert r.status_code == 404, (duong, r.text)
    assert lan(google, "video_hoi") == []


def test_moi_khoa_bi_tu_choi_video(client, google):
    cai(client, google, [K1, K2])
    google.video.update({K1: {"kieu": "tu_choi", "ma": 403}, K2: {"kieu": "tu_choi", "ma": 403}})
    r = client.post("/v1/videos", json=vid(), headers=QT)
    assert r.status_code == 403 and "tạo video" in r.json()["error"]["message"]
    assert "miễn phí" in r.json()["error"]["message"]
    khong_ro_khoa(r.text)


def test_video_lon_stream_khong_gom_bo_dem(du_lieu, google):
    google.co_video = 4 * 1024 * 1024
    google.so_lan_hoi = 0
    google.video_cho = True
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        r = h.post("/api/modules/gemini-api/cai", headers=QT,
                   json={"cau_hinh": {"api_keys": [K1], "base_url": google.url}})
        assert r.status_code == 200, r.text
        v = h.post("/v1/videos", headers=QT, json=vid()).json()
        assert h.get(f"/v1/videos/{v['id']}", headers=QT).json()["status"] == "completed"
        nhan = bytearray()
        with h.stream("GET", f"/v1/videos/{v['id']}/content", headers=QT) as s:
            assert s.status_code == 200
            for khuc in s.iter_bytes():
                nhan += khuc
                # Khúc đầu đã tới tay khách TRƯỚC khi Google giả nhả phần còn lại → không ai gom cả tệp vào RAM.
                google.cho_khuc_video.set()
    assert google.video_qua_han_cho is False
    assert bytes(nhan) == du_lieu_video("op1", google.co_video)


# ───────────── models ─────────────

def test_models_them_model_video_theo_phuong_thuc_google(client, google):
    google.models_goc_ds = [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/veo-3.1-generate-preview", "supportedGenerationMethods": ["predictLongRunning"]},
        {"name": "models/ten-nghe-nhu-video-nhung-khong", "supportedGenerationMethods": ["generateContent"]},
    ]
    cai(client, google, [K1])
    ct(client).DEM_MODELS_GIAY = 0  # không dùng bản đệm giữa các lượt trong test
    mod = client.app.state.so.dang_chay["gemini-api"]
    mod.DEM_MODELS_GIAY = 0
    ds = {x["id"]: x for x in client.get("/v1/models", headers=QT).json()["data"]}
    # Danh sách chat của đường tương thích giữ nguyên; model video thêm theo predictLongRunning, không theo tên.
    assert "gemini/gemini-2.5-flash" in ds and "gemini/gemini-3-flash-preview" in ds
    assert ds["gemini/veo-3.1-generate-preview"]["kha_nang"] == ["video"]
    assert "kha_nang" not in ds["gemini/gemini-2.5-flash"]
    assert "gemini/ten-nghe-nhu-video-nhung-khong" not in ds
    # Đi hết các trang của danh sách gốc.
    assert any(x["trang"] is None for x in lan(google, "models_goc"))
    for x in lan(google, "models_goc"):
        assert x["kieu_khoa"] == "x-goog-api-key" and "key=" not in x["url"]
    # Danh sách gốc hỏng không làm hỏng danh sách chat.
    google.models_goc_loi = True
    ids = [x["id"] for x in client.get("/v1/models", headers=QT).json()["data"]]
    assert "gemini/gemini-2.5-flash" in ids


def test_models_gemini_di_het_cac_trang(client, google):
    google.models_goc_ds = [{"name": f"models/veo-{i}", "supportedGenerationMethods": ["predictLongRunning"]}
                            for i in range(3)]
    cai(client, google, [K1])
    mod = client.app.state.so.dang_chay["gemini-api"]
    mod.CO_TRANG_MODELS_GOC = 1
    ids = [x["id"] for x in client.get("/v1/models", headers=QT).json()["data"]]
    assert {"gemini/veo-0", "gemini/veo-1", "gemini/veo-2"} <= set(ids)
    assert len(lan(google, "models_goc")) == 3


CHAT = ["generateContent", "countTokens"]
NANO_BANANA = ["generateContent", "countTokens", "batchGenerateContent"]  # model ảnh Gemini: GIỐNG hệt model chat


def _models_tuoi(client):
    """/v1/models không dùng bản đệm (đường tương thích lẫn danh sách gốc) → {id: mục}."""
    ct(client).DEM_MODELS_GIAY = 0
    client.app.state.so.dang_chay["gemini-api"].DEM_MODELS_GIAY = 0
    r = client.get("/v1/models", headers=QT)
    assert r.status_code == 200, r.text
    ids = [x["id"] for x in r.json()["data"]]
    assert len(ids) == len(set(ids)), "model có ở cả hai danh sách bị liệt kê hai lần"
    return {x["id"]: x for x in r.json()["data"]}


def test_models_gan_anh_theo_predict_hoac_doan_ten_image(client, google):
    """Nhóm «Tạo ảnh»: `predict` (Imagen) theo CẤU TRÚC; model chỉ generateContent thì theo tên Google đặt — id có
    một ĐOẠN đúng bằng «image», hoặc hai đoạn liền nhau «nano» «banana». displayName/description không được dùng
    (model chat nhắc «image» là đầu vào)."""
    google.models_them = [{"id": "models/gemini-3.1-flash-image", "object": "model", "owned_by": "google"},
                          {"id": "models/nano-banana-pro-preview", "object": "model", "owned_by": "google"}]
    google.models_goc_ds = [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": CHAT,
         "description": "Multimodal model: understands text, image, audio and video input."},
        {"name": "models/gemini-3-flash-preview", "supportedGenerationMethods": CHAT},
        # Có trong danh sách tương thích → chỉ gắn nhãn, không thêm lần hai.
        {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": NANO_BANANA},
        # Chỉ có ở danh sách gốc → thêm vào (như model video).
        {"name": "models/gemini-3-pro-image-preview", "supportedGenerationMethods": NANO_BANANA},
        {"name": "models/thu_nghiem.image", "supportedGenerationMethods": NANO_BANANA},     # tách cả «_» «.»
        {"name": "models/imagen-4.0-generate-001", "supportedGenerationMethods": ["predict"]},
        {"name": "models/veo-3.1-generate-preview", "supportedGenerationMethods": ["predictLongRunning"]},
        # Không phải ảnh: «imagery» là chuỗi con, không phải đoạn; «image» chỉ ở description/displayName.
        {"name": "models/gemini-imagery-1", "supportedGenerationMethods": CHAT},
        {"name": "models/gemini-hieu-anh", "supportedGenerationMethods": CHAT,
         "displayName": "Image understanding", "description": "Reads an image and answers."},
        # Nano Banana: không có đoạn «image» → nhận theo hai đoạn «nano» «banana» của id (không theo description).
        {"name": "models/nano-banana-pro-preview", "supportedGenerationMethods": NANO_BANANA,
         "displayName": "Nano Banana Pro", "description": "Gemini 3 Pro Image Preview"},
        {"name": "models/gemini-nano-banana-2.1", "supportedGenerationMethods": NANO_BANANA,
         "displayName": "Nano Banana 2.1", "description": "Gemini Nano Banana 2.1."},
        # Không phải ảnh: chỉ «nano», đảo thứ tự, dính liền — khớp ĐOẠN liền nhau, không chuỗi con.
        {"name": "models/gemini-nano-2", "supportedGenerationMethods": CHAT},
        {"name": "models/banana-nano-1", "supportedGenerationMethods": CHAT},
        {"name": "models/nanobanana-x", "supportedGenerationMethods": CHAT},
        # Có đoạn «image» nhưng không sinh nội dung (nhúng) → không phải model tạo ảnh.
        {"name": "models/multimodal-image-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        # Mục dị dạng không làm hỏng danh sách.
        {"name": 5, "supportedGenerationMethods": ["predict"]},
        {"name": "models/x-image"},
        {"name": "models/y-image", "supportedGenerationMethods": "generateContent"},
        "khong-phai-dict",
    ]
    cai(client, google, [K1])
    kn = {i: m.get("kha_nang") for i, m in _models_tuoi(client).items()}
    assert kn == {
        "gemini/gemini-2.5-flash": None, "gemini/gemini-3-flash-preview": None,
        "gemini/gemini-3.1-flash-image": ["anh"], "gemini/nano-banana-pro-preview": ["anh"],
        "gemini/gemini-nano-banana-2.1": ["anh"],
        "gemini/gemini-3-pro-image-preview": ["anh"], "gemini/thu_nghiem.image": ["anh"],
        "gemini/imagen-4.0-generate-001": ["anh"], "gemini/veo-3.1-generate-preview": ["video"],
    }
    # Danh sách gốc hỏng → vẫn trả danh sách chat của đường tương thích (không nhãn), không lỗi.
    google.models_goc_loi = True
    ds = _models_tuoi(client)
    assert set(ds) == {"gemini/gemini-2.5-flash", "gemini/gemini-3-flash-preview", "gemini/gemini-3.1-flash-image",
                       "gemini/nano-banana-pro-preview"}
    assert not any("kha_nang" in m for m in ds.values())


#: 46 id model THẬT của Google (12/10/2026). Danh sách này không mang
#: `supportedGenerationMethods`: test gán generateContent cho mọi model trừ Imagen (`predict`) — trường hợp KHÓ nhất
#: cho quy tắc tên, vì khi đó chỉ còn id để phân biệt.
ID_THAT_12_10 = """antigravity-preview-05-2026 antigravity-preview-09-2026 antigravity-preview-latest
deep-research-max-preview-04-2026 deep-research-preview-04-2026 deep-research-pro-preview-12-2025
gemini-2.5-computer-use-preview-10-2025 gemini-2.5-flash gemini-2.5-flash-image gemini-2.5-flash-lite
gemini-2.5-flash-preview-tts gemini-2.5-pro gemini-2.5-pro-preview-tts gemini-3-flash-preview gemini-3-pro-image
gemini-3-pro-image-preview gemini-3.1-flash-image gemini-3.1-flash-image-preview gemini-3.1-flash-lite
gemini-3.1-flash-lite-image gemini-3.1-flash-lite-preview gemini-3.1-flash-tts-preview gemini-3.1-pro-preview
gemini-3.1-pro-preview-customtools gemini-3.5-flash gemini-3.5-flash-lite gemini-3.5-transcribe gemini-3.6-flash
gemini-3.7-flash gemini-3.8-flash gemini-3.8-flash-lite-tts gemini-3.8-flash-tts gemini-flash-latest
gemini-flash-lite-latest gemini-nano-banana-2.1 gemini-omni-1.1-flash gemini-omni-flash-preview gemini-pro-latest
gemini-robotics-er-2-preview gemma-4-26b-a4b-it gemma-4-31b-it imagen-3.0-generate-001 lyria-3-clip-preview
lyria-3-pro-preview lyria-3.5 nano-banana-pro-preview""".split()


def test_models_quy_tac_anh_tren_id_that(client, google):
    assert len(ID_THAT_12_10) == 46
    google.models_goc_ds = [{"name": f"models/{i}", "supportedGenerationMethods":
                             ["predict"] if i.startswith("imagen-") else NANO_BANANA} for i in ID_THAT_12_10]
    cai(client, google, [K1])
    anh = sorted(i for i, m in _models_tuoi(client).items() if "anh" in (m.get("kha_nang") or []))
    assert anh == ["gemini/gemini-2.5-flash-image", "gemini/gemini-3-pro-image", "gemini/gemini-3-pro-image-preview",
                   "gemini/gemini-3.1-flash-image", "gemini/gemini-3.1-flash-image-preview",
                   "gemini/gemini-3.1-flash-lite-image", "gemini/gemini-nano-banana-2.1", "gemini/imagen-3.0-generate-001",
                   "gemini/nano-banana-pro-preview"]


def test_suc_khoe_khong_goi_anh_video(client, google):
    # Nguyên tắc: không dò khoá bằng lời gọi tốn tiền — «chua_ro» cho tới khi dùng thật.
    cai(client, google, [K1])
    google.nhat_ky.clear()
    r = client.get("/api/modules/gemini-api/suc-khoe", headers=QT)
    assert r.status_code == 200
    assert {x["duong"] for x in google.nhat_ky} == {"models"}
    assert r.json()["khoa"][0]["kha_nang"] == {"anh": {"trang_thai": "chua_ro"}, "video": {"trang_thai": "chua_ro"}}
    khong_ro_khoa(r.text)
