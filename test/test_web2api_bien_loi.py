"""Biên an toàn của lõi (Đợt A): lỗi dưới /v1 luôn đúng khuôn OpenAI, đường /v1 lạ là 404 `unknown_endpoint`,
sai phương thức là 405 kèm `Allow` suy từ router, thân JSON có trần (một bộ đếm chung với multipart), lỗi chưa bắt
không lộ chi tiết nội bộ hay khoá — kể cả trong nhật ký của uvicorn.

Module GIẢ «bien» (không nhà cung cấp thật). Khoá là chuỗi GIẢ có trong allowlist của .gitleaks.toml. Phần SDK openai
chỉ chạy khi gói có trong môi trường.
"""

import json
import logging
import tracemalloc

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import so_module, tuyen
from app.main import app
from gia import MayChuThat, ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
BI_MAT_NOI_BO = "/opt/noi-bo/duong-dan-bi-mat.py dòng 42"

MANIFEST_BIEN = {
    "id": "bien", "ten": "Biên giả", "mo_ta": "Module giả cho test biên lỗi", "loai": "ai",
    "giao_thuc": "openai", "tien_to": "bien", "phien_ban": "0.1.0", "cung_cap": ["chat", "anh", "noi", "video"],
    "truong_cau_hinh": [{"khoa": "api_key", "nhan": "Khoá", "kieu": "bi_mat", "bat_buoc": True}],
    "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
}

MA_BIEN = '''import json


class Module:
    def __init__(self, manifest):
        self.tien_to = manifest["tien_to"]
        self.nhan = []        # (phương thức, độ dài trường "input"/nội dung) theo thứ tự lõi gọi
        self.loi = None       # đặt một Exception → mọi phương thức ném nó
        self.hong_giua_luong = None  # đặt một Exception → chat stream ném nó SAU khúc đầu

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": dict(cau_hinh)}

    def _ghi(self, ten, body):
        self.nhan.append((ten, len(body.get("input") or "")))
        if self.loi:
            raise self.loi

    async def chat(self, body, stream):
        self._ghi("chat", body)
        if stream and self.hong_giua_luong:
            return 200, {"content-type": "text/event-stream"}, self._luong_hong()
        return 200, {"content-type": "application/json"}, json.dumps(
            {"object": "chat.completion", "model": body["model"],
             "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        ).encode()

    async def _luong_hong(self):
        yield b'data: {"choices": []}\\n\\n'
        raise self.hong_giua_luong

    async def anh(self, body):
        self._ghi("anh", body)
        return 200, {"content-type": "application/json"}, b'{"created": 0, "data": []}'

    async def noi(self, body):
        self._ghi("noi", body)
        return 200, {"content-type": "audio/mpeg"}, b"ID3"

    async def video_tao(self, body):
        self._ghi("video_tao", body)
        return 200, {"content-type": "application/json"}, json.dumps({"id": "v1", "object": "video"}).encode()

    async def video_trang_thai(self, ma):
        return 404, {"content-type": "application/json"}, b"{}"

    async def video_noi_dung(self, ma):
        return 404, {"content-type": "application/json"}, b"{}"

    async def models(self):
        return [{"id": self.tien_to + "/x", "object": "model", "owned_by": "bien"}]

    async def suc_khoe(self):
        if self.loi:
            raise self.loi
        return {"ok": True}

    async def dung(self):
        pass
'''


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_THAN_TOI_DA_MB", raising=False)
    monkeypatch.delenv("WEB2API_TEP_TOI_DA_MB", raising=False)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    ghi_thu_muc(tmp_path / "modules" / "bien", {"manifest.json": json.dumps(MANIFEST_BIEN).encode(),
                                                 "module.py": MA_BIEN.encode()})
    return tmp_path


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:  # raise_server_exceptions mặc định True: lỗi chưa bắt mà lọt ra là test hỏng
        assert c.post("/api/modules/bien/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        yield c


def bien(c=None):
    return app.state.so.dang_chay["bien"]


def khuon_openai(r, ma: int, code=None) -> dict:
    assert r.status_code == ma, r.text
    j = r.json()
    assert set(j) == {"error"}, j  # không có "detail" hay khoá lạ nào khác
    e = j["error"]
    assert isinstance(e["message"], str) and e["message"]
    assert isinstance(e["type"], str) and e["type"]
    assert "code" in e
    if code is not None:
        assert e["code"] == code, e
    return e


def khong_ro(*van_ban):
    for vb in van_ban:
        assert K1 not in vb and KHOA_QT not in vb, "khoá bị lộ"


def chat(**them):
    return {"model": "bien/x", "messages": [{"role": "user", "content": "chào"}], **them}


# ───────────── (a) đường /v1 lạ, sai phương thức ─────────────

@pytest.mark.parametrize("phuong_thuc,duong", [
    ("POST", "/v1/threads"), ("POST", "/v1/messages"), ("POST", "/v1/batches"), ("GET", "/v1/khong-co"),
    ("GET", "/v1/a/b/c"), ("DELETE", "/v1/"), ("GET", "/v1"), ("POST", "/v1"), ("PATCH", "/v1/models/x"),
])
def test_duong_v1_khong_co_la_404_unknown_endpoint(client, phuong_thuc, duong):
    r = client.request(phuong_thuc, duong, headers=QT)
    e = khuon_openai(r, 404, "unknown_endpoint")
    assert e["type"] == "invalid_request_error"
    assert duong in e["message"] and phuong_thuc in e["message"]


def test_duong_v1_khong_co_van_doi_khoa_truoc(client):
    # Chưa xác thực thì không cho dò đường nào có: 401 trước, đúng khuôn.
    for duong in ("/v1/responses", "/v1/chat/completions"):
        khuon_openai(client.post(duong, json={}), 401, "invalid_api_key")
        khuon_openai(client.post(duong, json={}, headers={"x-api-key": KHOA_QT}), 401, "invalid_api_key")


@pytest.mark.parametrize("phuong_thuc,duong", [
    ("GET", "/v1/chat/completions"), ("OPTIONS", "/v1/models"), ("DELETE", "/v1/videos/abc"),
    ("PUT", "/v1/images/edits"), ("POST", "/v1/models"), ("POST", "/v1/videos/a:b/content"),
    ("GET", "/v1/responses"),
])
def test_sai_phuong_thuc_405_allow_suy_tu_router(client, phuong_thuc, duong):
    r = client.request(phuong_thuc, duong, headers=QT)
    khuon_openai(r, 405, "method_not_allowed")
    # Đáp án lấy từ CHÍNH router /v1 (không phải bảng gõ tay trong test): mọi route khớp đường này.
    scope = {"type": "http", "path": duong, "method": phuong_thuc, "root_path": "", "headers": [], "query_string": b""}
    mong = set()
    for route in tuyen.v1.routes:
        if route.matches(dict(scope))[0].name != "NONE":
            mong |= route.methods
    assert mong and phuong_thuc not in mong
    assert {m.strip() for m in r.headers["allow"].split(",")} == mong


@pytest.mark.parametrize("phuong_thuc,duong,query", [
    ("GET", "/v1/models/", ""), ("GET", "/v1/models//", "a=1&b=%2F"), ("POST", "/v1/chat/completions/", ""),
    ("GET", "/v1/videos/bien:x/content/", ""),
])
def test_dau_gach_cuoi_307_toi_duong_co_that(client, phuong_thuc, duong, query):
    """Bỏ «/» cuối mà KHỚP trọn một route của router `v1` (cả phương thức) → 307 sang đường không «/» cuối, giữ query,
    không đổi phương thức (như redirect_slashes của Starlette — mount StaticFiles ở gốc khiến cơ chế đó không bao giờ
    chạy). Đáp án suy từ router; còn lại vẫn 404/405 như cũ."""
    r = client.request(phuong_thuc, duong + ("?" + query if query else ""), headers=QT, follow_redirects=False)
    assert r.status_code == 307, r.text
    assert r.headers["location"] == duong.rstrip("/") + ("?" + query if query else "")


def test_dau_gach_cuoi_khong_khop_van_404_405_va_van_doi_khoa(client):
    # «%2F» cuối giải mã thành «/» nhưng không phải dấu «/» của đường: không chuyển hướng (bỏ đi là vòng lặp 307).
    for phuong_thuc, duong in (("GET", "/v1/"), ("POST", "/v1/"), ("GET", "/v1/khong-co/"), ("POST", "/v1/models/"),
                               ("GET", "/v1/models%2F")):
        khuon_openai(client.request(phuong_thuc, duong, headers=QT, follow_redirects=False), 404, "unknown_endpoint")
    # Chưa xác thực: 401 trước, không cho dò đường nào có bằng cách thêm «/».
    khuon_openai(client.get("/v1/models/", follow_redirects=False), 401, "invalid_api_key")


def test_phuong_thuc_la_ngoai_chuan_van_dung_khuon(client):
    khuon_openai(client.request("FOO", "/v1/models", headers=QT), 405)


def test_khong_phan_chieu_chuoi_dai_query_hay_ky_tu_dieu_khien(client):
    r = client.post("/v1/" + "a" * 5000 + f"?key={K1}", headers=QT)
    e = khuon_openai(r, 404, "unknown_endpoint")
    assert len(e["message"]) < 400
    khong_ro(r.text)
    # Ký tự điều khiển trong đường (%0A, %1B): vẫn khuôn OpenAI, không phản chiếu ký tự đó. («.*» của Starlette không
    # khớp xuống dòng nên đường này rơi xuống StaticFiles → 404 not_found — vẫn qua bộ xử lý /v1.)
    for duong in ("/v1/a%1Bb", "/v1/a%0Ab"):
        e = khuon_openai(client.post(duong, headers=QT), 404)
        assert "\n" not in e["message"] and "\x1b" not in e["message"]


# ───────────── (a) thân sai, 413, 500, 401 ─────────────

def test_than_json_hong_400_dung_khuon(client):
    khuon_openai(client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"},
                             content=b"{h\xc3\xb4ng"), 400)
    khuon_openai(client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"},
                             content=b"\xff\xfe\x00"), 400)
    khuon_openai(client.post("/v1/chat/completions", headers=QT, json=[1, 2]), 400)
    assert bien().nhan == []


@pytest.mark.parametrize("duong", ["/v1/chat/completions", "/api/modules/tu-xa"])
def test_than_json_long_qua_sau_400_khong_phai_500(client, duong):
    # json.loads ném RecursionError (không phải ValueError) với mảng lồng sâu — vẫn là thân hỏng của khách, không phải
    # lỗi nội bộ. Thân ~400 KB, dưới trần mặc định.
    than = b"[" * 200_000 + b"]" * 200_000
    e = khuon_openai(client.post(duong, headers={**QT, "content-type": "application/json"}, content=than), 400)
    assert e["message"] == "Thân yêu cầu không phải JSON hợp lệ"
    assert bien().nhan == []


def test_500_module_nem_loi_khong_lo_chi_tiet_va_khoa(client, caplog):
    bien().loi = RuntimeError(f"hỏng với khoá {K1} và {KHOA_QT} tại {BI_MAT_NOI_BO}")
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/chat/completions", headers=QT, json=chat())
    e = khuon_openai(r, 500, "internal_error")
    assert "RuntimeError" not in e["message"] and BI_MAT_NOI_BO not in e["message"]
    khong_ro(r.text, caplog.text)
    # Nhật ký có đủ để người vận hành tra: mã tra cứu trong phản hồi cũng có trong nhật ký, kèm loại lỗi.
    ma_tra = e["message"].split("mã tra nhật ký ", 1)[1].split(")", 1)[0]
    assert ma_tra in caplog.text and "RuntimeError" in caplog.text


def test_nhat_ky_loi_chua_bat_khong_cho_khach_chen_dong_gia(caplog):
    """Đường có %0A được giải mã thành xuống dòng; thông điệp lỗi có thể mang chuỗi khách gửi. Đi nguyên vào nhật ký
    là khách chèn được dòng giả (vd giả một dòng «Lỗi chưa bắt…» với mã tra khác). Ứng dụng tạm, cài như app thật:
    «.*» của route /v1 thật không khớp xuống dòng, còn tham số đường «[^/]+» thì khớp."""
    from app.bien_loi import cai_bien_loi

    tam = FastAPI()
    cai_bien_loi(tam)

    @tam.get("/v1/hong/{x}")
    async def hong(x: str):
        raise RuntimeError(f"không xử lý được {x}\nGIA-MAO-2 dòng giả từ thông điệp")

    with TestClient(tam) as c, caplog.at_level(logging.DEBUG):
        khuon_openai(c.get("/v1/hong/a%0AGIA-MAO-1%0D%1B[31m dòng giả"), 500, "internal_error")
    dong = caplog.text.splitlines()
    assert dong and not [d for d in dong if d.startswith("GIA-MAO")], caplog.text
    assert "\r" not in caplog.text and "\x1b" not in caplog.text
    # Vẫn đủ thông tin để tra: ký tự điều khiển hiện ở dạng thoát, không bị nuốt mất.
    assert "/v1/hong/a\\nGIA-MAO-1\\r\\x1b[31m dòng giả" in caplog.text
    assert "\\nGIA-MAO-2 dòng giả từ thông điệp" in caplog.text


def test_413_va_401_dung_khuon(client, monkeypatch):
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.001")
    khuon_openai(client.post("/v1/chat/completions", headers=QT, json=chat(input="x" * 5000)), 413,
                 "request_too_large")
    khuon_openai(client.post("/v1/chat/completions", json=chat()), 401, "invalid_api_key")


# ───────────── (b) /api/* và trang web giữ khuôn cũ ─────────────

def test_api_va_trang_web_giu_khuon_cu(client):
    assert client.get("/api/khong-co").json() == {"detail": "Not Found"}
    r = client.post("/api/he-thong")
    assert r.status_code == 405 and r.json() == {"detail": "Method Not Allowed"}
    assert client.get("/m/khong-co/x.html").json() == {"detail": "Not Found"}
    assert client.get("/khong-co.html").json() == {"detail": "Not Found"}
    assert client.get("/v1x").json() == {"detail": "Not Found"}  # không phải /v1, cũng không phải /v1/…
    # /api vốn dùng LoiOpenAI cho lỗi của chính nó — giữ nguyên.
    khuon_openai(client.post("/api/modules/nap-lai"), 401, "invalid_api_key")
    assert client.get("/").status_code == 200 and client.get("/health").json()["ok"] is True


def test_api_500_van_la_chu_tron_va_khong_lo_khoa(client, caplog):
    bien().loi = RuntimeError(f"hỏng với khoá {K1}")
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/modules/bien/suc-khoe", headers=QT)
    assert r.status_code == 500 and r.text == "Internal Server Error"
    khong_ro(caplog.text)
    assert "RuntimeError" in caplog.text


def test_loi_tham_so_v1_400_khuon_openai_api_giu_422_detail():
    # Chưa đường /v1 nào có tham số Pydantic → thử bộ xử lý trên một ứng dụng tạm, cài như app thật.
    from app.bien_loi import cai_bien_loi

    tam = FastAPI()
    cai_bien_loi(tam)

    @tam.get("/v1/thu")
    async def thu_v1(n: int):
        return {"n": n}

    @tam.get("/api/thu")
    async def thu_api(n: int):
        return {"n": n}

    with TestClient(tam) as c:
        e = khuon_openai(c.get("/v1/thu", params={"n": f"khong-phai-so-{K1}"}), 400, "invalid_request_error")
        assert "n" in e["message"]
        khong_ro(e["message"])  # không phản chiếu giá trị khách gửi
        r = c.get("/api/thu", params={"n": "x"})
        assert r.status_code == 422 and isinstance(r.json()["detail"], list)


# ───────────── (c) trần thân JSON ─────────────

@pytest.mark.parametrize("duong", ["/v1/chat/completions", "/v1/images/generations", "/v1/audio/speech", "/v1/videos"])
def test_tran_than_json_moi_duong_json(client, monkeypatch, duong):
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.01")  # 10 485 byte
    e = khuon_openai(client.post(duong, headers=QT, json={"model": "bien/x", "input": "x" * 20000}), 413,
                     "request_too_large")
    assert "WEB2API_THAN_TOI_DA_MB" in e["message"] and "0.01 MB" in e["message"]
    assert bien().nhan == []
    # Dưới trần vẫn đi như cũ.
    assert client.post(duong, headers=QT, json={"model": "bien/x", "prompt": "p", "input": "x" * 5000,
                                                "messages": [{"role": "user", "content": "c"}]}).status_code == 200
    assert bien().nhan[-1][1] == 5000


def test_tran_doc_moi_luot_va_gia_tri_hong_dung_mac_dinh(client, monkeypatch, caplog):
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.001")
    assert client.post("/v1/chat/completions", headers=QT, json=chat(input="x" * 2000)).status_code == 413
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "khong-phai-so")
    with caplog.at_level(logging.WARNING):
        assert client.post("/v1/chat/completions", headers=QT, json=chat(input="x" * 2000)).status_code == 200
    assert "WEB2API_THAN_TOI_DA_MB" in caplog.text
    monkeypatch.delenv("WEB2API_THAN_TOI_DA_MB")
    # Mặc định 25 MB: thân ~1 MB đi được.
    assert client.post("/v1/chat/completions", headers=QT, json=chat(input="x" * 1_000_000)).status_code == 200


def test_content_length_lon_hon_tran_413_som_khong_phai_so_400(client, monkeypatch):
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.01")
    than = json.dumps(chat(input="x" * 20000)).encode()
    r = client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"}, content=than)
    khuon_openai(r, 413, "request_too_large")
    r = client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json",
                                                     "content-length": "abc"}, content=b"{}")
    khuon_openai(r, 400, "invalid_content_length")
    assert bien().nhan == []


@pytest.mark.parametrize("duong,kieu", [("/v1/chat/completions", "application/json"),
                                        ("/v1/audio/transcriptions", "multipart/form-data; boundary=x")])
def test_content_length_qua_nhieu_chu_so_413_moi_duong_doc_than(client, duong, kieu):
    # Hơn 4300 chữ số: int() của Python ném ValueError (giới hạn chuyển chuỗi số) — trước đây thành 500 ở multipart
    # và «không phải JSON hợp lệ» ở đường JSON. Số lớn hơn mọi trần → 413 như mọi Content-Length vượt trần.
    r = client.post(duong, headers={**QT, "content-type": kieu, "content-length": "9" * 5000}, content=b"{}")
    khuon_openai(r, 413, "request_too_large")
    # Số 0 đứng đầu không làm số lớn lên: «000…02» là 2 byte, không bị coi là vượt trần.
    r = client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json",
                                                     "content-length": "0" * 5000 + "2"}, content=b"{}")
    khuon_openai(r, 400, "missing_model")
    assert bien().nhan == []


def test_content_length_noi_doi_nho_hon_thuc_te_van_413(client, monkeypatch):
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.01")
    than = json.dumps(chat(input="x" * 200_000)).encode()
    # Header nói 100 byte; thân thật ~200 KB → lõi đếm byte thật, không tin header.
    r = client.post("/v1/chat/completions", headers={**QT, "content-type": "application/json",
                                                     "content-length": "100"}, content=than)
    khuon_openai(r, 413, "request_too_large")
    assert bien().nhan == []


def _tung_khuc(than: bytes, co: int):
    def gen():  # generator → httpx gửi Transfer-Encoding: chunked, không có Content-Length
        for i in range(0, len(than), co):
            yield than[i:i + co]
    return gen()


def test_chunked_vuot_tran_413_tren_uvicorn_that_dinh_ram_quanh_tran(du_lieu, monkeypatch):
    """Thân chunked gấp 10 lần trần qua uvicorn THẬT: 413, module không được gọi, và bộ nhớ đỉnh của cả tiến trình
    quanh cỡ TRẦN chứ không phải cỡ thân — bỏ bộ đếm (đọc hết rồi mới xét) là test này đỏ."""
    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "1")
    co_tran = 1024 * 1024
    than = json.dumps(chat(input="x" * (10 * co_tran))).encode()
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=30) as h:
        assert h.post("/api/modules/bien/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        tracemalloc.start()
        try:
            try:
                r = h.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"},
                           content=_tung_khuc(than, 64 * 1024))
            except httpx.TransportError:
                r = None  # máy chủ trả 413 rồi đóng kết nối khi khách còn đang gửi — cũng là đã chặn
            _, dinh = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if r is not None:
            khuon_openai(r, 413, "request_too_large")
        assert app.state.so.dang_chay["bien"].nhan == []
        # Thân hợp lệ, chunked, dưới trần vẫn đi được.
        r = h.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"},
                   content=iter([json.dumps(chat(input="y" * 1000)).encode()]))
        assert r.status_code == 200, r.text
    print(f"\nbộ nhớ đỉnh {dinh / co_tran:.2f}× trần (thân gửi {len(than) / co_tran:.1f}× trần)")
    assert dinh < 3 * co_tran


def test_bo_nho_dinh_than_json_vua_tran(du_lieu):
    """Thân JSON 20 MB (gần trần mặc định 25 MB) qua uvicorn thật: bộ nhớ đỉnh (lõi + khách, tracemalloc) là một
    bội số nhỏ CỐ ĐỊNH của cỡ thân. Sàn của json chuẩn là 2×: văn bản đã giải mã + chuỗi đã phân tích cùng sống lúc
    phân tích (bản thân module nhận đã là 1×)."""
    co = 20 * 1024 * 1024
    than = json.dumps(chat(input="z" * co)).encode()
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=60) as h:
        assert h.post("/api/modules/bien/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        tracemalloc.start()
        try:
            r = h.post("/v1/chat/completions", headers={**QT, "content-type": "application/json"},
                       content=_tung_khuc(than, 256 * 1024))
            _, dinh = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert r.status_code == 200, r.text
        assert app.state.so.dang_chay["bien"].nhan[-1] == ("chat", co)
    print(f"\nbộ nhớ đỉnh {dinh / co:.2f}× cỡ thân ({dinh / 1024 / 1024:.1f} MB cho thân 20 MB)")
    assert dinh < 2.6 * co


# ───────────── (d) khoá không lọt vào nhật ký uvicorn ─────────────

class _GomLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.dong = []

    def emit(self, record):
        self.dong.append(self.format(record))
        if record.exc_info:
            self.dong.append(logging.Formatter().formatException(record.exc_info))


def test_500_qua_uvicorn_that_khong_lo_khoa_trong_nhat_ky(du_lieu, capfd):
    """uvicorn cấu hình logger «uvicorn» propagate=False → caplog không thấy; gắn bộ gom thẳng vào logger đó và
    logger gốc, đọc cả stderr. Lỗi chưa bắt mà để lọt tới uvicorn thì nó in traceback có nguyên chuỗi lỗi."""
    gom = _GomLog()
    loggers = [logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")]
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        for lg in loggers:
            lg.addHandler(gom)
        try:
            assert h.post("/api/modules/bien/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
            m = app.state.so.dang_chay["bien"]
            m.loi = RuntimeError(f"hỏng với khoá {K1} và {KHOA_QT}")
            r = h.post("/v1/chat/completions", headers=QT, json=chat())
            e = khuon_openai(r, 500, "internal_error")
            r2 = h.get("/api/modules/bien/suc-khoe", headers=QT)
            assert r2.status_code == 500
            # Lỗi GIỮA luồng (đã gửi 200 và khúc đầu): kết nối bị cắt, khách thấy luồng hỏng; không traceback thô.
            m.loi = None
            m.hong_giua_luong = RuntimeError(f"đứt luồng, khoá {K1}")
            nhan = b""
            with pytest.raises(httpx.HTTPError):
                with h.stream("POST", "/v1/chat/completions", headers=QT, json=chat(stream=True)) as s:
                    assert s.status_code == 200
                    for khuc in s.iter_raw():
                        nhan += khuc
            assert nhan.startswith(b"data: ")
        finally:
            for lg in loggers:
                lg.removeHandler(gom)
    ra, loi = capfd.readouterr()
    nhat_ky = "\n".join(gom.dong) + ra + loi
    khong_ro(r.text, r2.text, nhan.decode(errors="replace"), nhat_ky)
    assert "Exception in ASGI application" not in nhat_ky
    ma_tra = e["message"].split("mã tra nhật ký ", 1)[1].split(")", 1)[0]
    assert ma_tra in nhat_ky


# ───────────── (e) SDK openai làm trọng tài ─────────────

@pytest.fixture
def sdk(du_lieu):
    openai = pytest.importorskip("openai")
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        assert h.post("/api/modules/bien/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        yield openai, openai.OpenAI(base_url=w.url + "/v1", api_key=KHOA_QT, max_retries=0, timeout=15)


def test_sdk_openai_doc_duoc_loi(sdk, monkeypatch):
    openai, c = sdk
    with pytest.raises(openai.NotFoundError) as e:
        c.responses.create(model="bien/x", input="chào")
    assert e.value.code == "unknown_endpoint" and e.value.type == "invalid_request_error"
    assert "/v1/responses" in e.value.body["message"]
    with pytest.raises(openai.APIStatusError) as e:
        c.embeddings.create(model="bien/x", input="chào")
    assert e.value.status_code == 404 and e.value.code == "unknown_endpoint"

    monkeypatch.setenv("WEB2API_THAN_TOI_DA_MB", "0.01")
    with pytest.raises(openai.APIStatusError) as e:
        c.chat.completions.create(model="bien/x", messages=[{"role": "user", "content": "x" * 20000}])
    assert e.value.status_code == 413 and e.value.code == "request_too_large"
    assert "WEB2API_THAN_TOI_DA_MB" in e.value.body["message"]
    monkeypatch.delenv("WEB2API_THAN_TOI_DA_MB")

    app.state.so.dang_chay["bien"].loi = RuntimeError(f"khoá {K1}")
    with pytest.raises(openai.InternalServerError) as e:
        c.chat.completions.create(model="bien/x", messages=[{"role": "user", "content": "x"}])
    assert e.value.code == "internal_error"
    khong_ro(str(e.value.body))
