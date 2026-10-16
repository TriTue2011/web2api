"""Đa phương thức của lõi: /v1/audio/speech, /v1/audio/transcriptions, /v1/images/edits và /v1/videos nhận multipart.

Chứng minh bằng MODULE GIẢ «giong» (không nhà cung cấp thật): lõi đọc multipart thành YeuCauDaPhan, đặt trần, dọn
tệp tạm, định tuyến theo tiền tố model, trả nhị phân/stream. Khoá trong cấu hình module là chuỗi GIẢ có trong
allowlist của .gitleaks.toml. Phần dùng SDK openai thật chỉ chạy khi gói có trong môi trường (không có trong
requirements-dev.txt → CI bỏ qua).
"""

import base64
import hashlib
import json
import logging
import tracemalloc

import httpx
import pytest
import starlette.formparsers as fp
from fastapi.testclient import TestClient

from app import so_module
from app.main import app
from gia import MayChuThat, ghi_thu_muc, tep_echo

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"

MANIFEST_GIONG = {
    "id": "giong", "ten": "Giọng giả", "mo_ta": "Module giả cho test đa phương thức", "loai": "ai",
    "hang": "A",
    "giao_thuc": "openai", "tien_to": "giong", "phien_ban": "0.1.0",
    "cung_cap": ["noi", "nghe", "anh_sua", "video"],
    "truong_cau_hinh": [{"khoa": "api_key", "nhan": "Khoá", "kieu": "bi_mat", "bat_buoc": True}],
    "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
}

MA_GIONG = '''import asyncio
import base64
import json
import threading
import time


class Module:
    def __init__(self, manifest):
        self.tien_to = manifest["tien_to"]
        self.nhan = []                       # (phương thức, đầu vào) theo thứ tự lõi gọi
        self.cho_khuc = threading.Event()    # test đặt khi đã nhận khúc đầu
        self.qua_han_cho = None
        self.loi = None                      # đặt một Exception → nghe/anh_sua ném nó

    def nap(self, cau_hinh):
        self.cau_hinh = cau_hinh

    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": dict(cau_hinh)}

    async def chat(self, body, stream):
        return 200, {"content-type": "application/json"}, b"{}"

    async def models(self):
        return [{"id": self.tien_to + "/doc", "object": "model", "owned_by": "giong"}]

    async def suc_khoe(self):
        return {"ok": True}

    async def dung(self):
        pass

    async def noi(self, body):
        self.nhan.append(("noi", body))
        if not body.get("stream"):
            # content-length cố tình SAI: lõi phải tự tính lại cho thân bytes.
            return 200, {"content-type": "audio/mpeg", "content-length": "1",
                         "content-disposition": 'attachment; filename="noi.mp3"'}, b"ID3" + body["input"].encode()
        return 200, {"content-type": "audio/pcm"}, self._phat()

    async def _phat(self):
        yield b"KHUC-1;"
        han = time.monotonic() + 5
        while not self.cho_khuc.is_set():
            if time.monotonic() > han:
                self.qua_han_cho = True
                break
            await asyncio.sleep(0.01)
        else:
            self.qua_han_cho = False
        yield b"KHUC-2;"
        yield b"KHUC-3"

    async def nghe(self, yc):
        self.nhan.append(("nghe", yc))
        if self.loi:
            raise self.loi
        t = yc.tep_ten("file")[0]
        return 200, {"content-type": "application/json"}, json.dumps({"text": f"{t.ten_tep}:{len(t.noi_dung)}"}).encode()

    async def anh_sua(self, yc):
        self.nhan.append(("anh_sua", yc))
        if self.loi:
            raise self.loi
        anh = b"|".join(t.noi_dung for t in yc.tep_ten("image"))
        return 200, {"content-type": "application/json"}, json.dumps(
            {"created": 0, "data": [{"b64_json": base64.b64encode(anh).decode()}]}).encode()

    async def video_tao(self, body):
        self.nhan.append(("video_tao", body))
        return 200, {"content-type": "application/json"}, json.dumps(
            {"id": "v1", "object": "video", "status": "queued", "model": body["model"], "progress": 0,
             "created_at": 0, "seconds": body.get("seconds", "4"), "size": body.get("size", "720x1280")}).encode()

    async def video_trang_thai(self, ma):
        return 404, {"content-type": "application/json"}, b"{}"

    async def video_noi_dung(self, ma):
        return 404, {"content-type": "application/json"}, b"{}"
'''


def tep_giong() -> dict[str, bytes]:
    return {"manifest.json": json.dumps(MANIFEST_GIONG, ensure_ascii=False).encode(), "module.py": MA_GIONG.encode()}


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.delenv("WEB2API_TEP_TOI_DA_MB", raising=False)
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    ghi_thu_muc(tmp_path / "modules" / "giong", tep_giong())
    ghi_thu_muc(tmp_path / "modules" / "echo", tep_echo())
    return tmp_path


def lien_ket(c):
    for id_, ch in (("giong", {"api_key": K1}), ("echo", {})):
        r = c.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": ch})
        assert r.status_code == 200, r.text


@pytest.fixture
def client(du_lieu):
    with TestClient(app) as c:
        lien_ket(c)
        yield c


@pytest.fixture
def spool(monkeypatch):
    """Ghi lại mọi tệp tạm Starlette mở cho phần tệp; trần RAM 1 KB để tệp nào cũng tràn ra đĩa thật."""
    ds = []

    class Ghi(fp.SpooledTemporaryFile):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            ds.append(self)

    monkeypatch.setattr(fp, "SpooledTemporaryFile", Ghi)
    monkeypatch.setattr(fp.MultiPartParser, "spool_max_size", 1024)
    return ds


def giong(c):
    return c.app.state.so.dang_chay["giong"]


def da_goi(c, phuong_thuc):
    return [x for p, x in giong(c).nhan if p == phuong_thuc]


def khong_ro(*van_ban):
    for vb in van_ban:
        assert K1 not in vb and KHOA_QT not in vb, "khoá bị lộ"


def multipart(truong: list[tuple[str, str]], tep: list[tuple[str, tuple]]) -> tuple[bytes, str]:
    """Thân multipart dựng tay đúng khuôn SDK openai gửi (đo bằng event hook của SDK 3.28): trường chữ lặp được, có
    hay không có tệp đều là multipart (httpx tự đổi sang urlencoded khi không có tệp — SDK thì không)."""
    ranh = "ranh-gioi-web2api-0123"
    phan = [f'--{ranh}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + v.encode() + b"\r\n"
            for k, v in truong]
    for k, (ten, noi_dung, kieu) in tep:
        dau = f'--{ranh}\r\nContent-Disposition: form-data; name="{k}"; filename="{ten}"\r\n'
        if kieu:
            dau += f"Content-Type: {kieu}\r\n"
        phan.append((dau + "\r\n").encode() + noi_dung + b"\r\n")
    return b"".join(phan) + f"--{ranh}--\r\n".encode(), f"multipart/form-data; boundary={ranh}"


def nghe(**them):
    return {"model": "giong/nghe", "language": "vi", **them}


AM_THANH = b"RIFF" + bytes(range(256)) * 40


# ───────────── /v1/audio/speech ─────────────

def test_speech_bytes_di_nguyen_content_type_cua_module(client):
    r = client.post("/v1/audio/speech", headers=QT,
                    json={"model": "giong/doc", "input": "xin chào", "voice": "mai", "response_format": "mp3",
                          "speed": 1.2, "truong_la": {"x": 1}})
    assert r.status_code == 200, r.text
    assert r.content == b"ID3" + "xin chào".encode()
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.headers["content-disposition"] == 'attachment; filename="noi.mp3"'
    assert r.headers["content-length"] == str(len(r.content))  # số sai của module bị thay bằng số thật
    # Thân đi nguyên (kể cả trường lạ, model còn tiền tố — module tự bỏ như với chat).
    assert da_goi(client, "noi")[-1] == {"model": "giong/doc", "input": "xin chào", "voice": "mai",
                                         "response_format": "mp3", "speed": 1.2, "truong_la": {"x": 1}}


def test_speech_stream_tung_khuc_khong_gom(du_lieu):
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        for id_, ch in (("giong", {"api_key": K1}), ("echo", {})):
            assert h.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": ch}).status_code == 200
        m = app.state.so.dang_chay["giong"]
        nhan = []
        with h.stream("POST", "/v1/audio/speech", headers=QT,
                      json={"model": "giong/doc", "input": "x", "voice": "mai", "stream": True}) as s:
            assert s.status_code == 200 and s.headers["content-type"] == "audio/pcm"
            for khuc in s.iter_bytes():
                nhan.append(khuc)
                # Khúc đầu đã tới tay khách TRƯỚC khi module nhả phần còn lại → lõi không gom iterator.
                m.cho_khuc.set()
    assert m.qua_han_cho is False
    assert b"".join(nhan) == b"KHUC-1;KHUC-2;KHUC-3"


def test_speech_module_khong_co_noi_501_tien_to_la_404(client):
    r = client.post("/v1/audio/speech", headers=QT, json={"model": "echo/x", "input": "a", "voice": "b"})
    assert r.status_code == 501 and r.json()["error"]["code"] == "capability_not_supported"
    assert "echo" in r.json()["error"]["message"] and "noi" in r.json()["error"]["message"]
    r = client.post("/v1/audio/speech", headers=QT, json={"model": "khong-co/x", "input": "a"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    r = client.post("/v1/audio/speech", headers=QT, json={"input": "a"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_model"
    assert da_goi(client, "noi") == []


def test_module_chua_lien_ket_404(du_lieu):
    with TestClient(app) as c:
        body, ct = multipart(list(nghe().items()), [("file", ("a.wav", AM_THANH, "audio/wav"))])
        for r in (c.post("/v1/audio/speech", headers=QT, json={"model": "giong/doc", "input": "a"}),
                  c.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)):
            assert r.status_code == 404 and r.json()["error"]["code"] == "module_not_configured", r.text


@pytest.mark.parametrize("duong", ["/v1/audio/speech", "/v1/audio/transcriptions", "/v1/images/edits"])
def test_khong_khoa_quan_tri_401_khong_doc_than(client, duong):
    body, ct = multipart([("model", "giong/x"), ("prompt", "p")], [("file", ("a.wav", AM_THANH, "audio/wav"))])
    r = client.post(duong, headers={"content-type": ct}, content=body)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    assert giong(client).nhan == []


# ───────────── /v1/audio/transcriptions ─────────────

def test_transcriptions_module_nhan_dung_truong_va_tep(client, spool, caplog):
    body, ct = multipart([("model", "giong/nghe"), ("language", "vi"), ("prompt", "Hà Nội"),
                          ("temperature", "0.2"), ("timestamp_granularities[]", "word"),
                          ("timestamp_granularities[]", "segment")],
                         [("file", ("ghi âm.wav", AM_THANH, "audio/wav"))])
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"text": f"ghi âm.wav:{len(AM_THANH)}"}
    yc = da_goi(client, "nghe")[-1]
    assert yc.truong == {"model": "giong/nghe", "language": "vi", "prompt": "Hà Nội", "temperature": "0.2",
                         "timestamp_granularities": ["word", "segment"]}
    [t] = yc.tep
    assert (t.ten_truong, t.ten_tep, t.content_type, t.noi_dung) == ("file", "ghi âm.wav", "audio/wav", AM_THANH)
    # Tệp đã tràn ra đĩa (trần RAM 1 KB) và đã đóng trước khi trả lời.
    assert spool and all(f._rolled and f.closed for f in spool)
    khong_ro(r.text, caplog.text)


def test_transcriptions_truong_mot_lan_la_chuoi_lap_la_danh_sach(client):
    body, ct = multipart([("model", "giong/nghe"), ("include", "a"), ("include", "b")],
                         [("file", ("a.wav", b"x", None))])
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 200, r.text
    yc = da_goi(client, "nghe")[-1]
    assert yc.truong["include"] == ["a", "b"] and yc.truong["model"] == "giong/nghe"
    assert yc.tep[0].content_type == "application/octet-stream"  # khách không khai kiểu tệp


def test_transcriptions_thieu_file_400(client):
    for tep in ([], [("am_thanh", ("a.wav", AM_THANH, "audio/wav"))]):
        body, ct = multipart(list(nghe().items()), tep)
        r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "missing_file", r.text
    # «file» gửi dạng trường chữ, không phải tệp → vẫn là thiếu tệp.
    body, ct = multipart(list(nghe(file="RIFF...").items()), [("x", ("a", b"1", "text/plain"))])
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 400 and "file" in r.json()["error"]["message"]
    # Hai tệp «file» → 400 (chuẩn chỉ một).
    body, ct = multipart(list(nghe().items()), [("file", ("a.wav", b"1", "audio/wav")),
                                               ("file", ("b.wav", b"2", "audio/wav"))])
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "too_many_files"
    assert da_goi(client, "nghe") == []


def test_transcriptions_json_thay_multipart_415(client):
    r = client.post("/v1/audio/transcriptions", headers=QT, json={"model": "giong/nghe", "file": "x"})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"
    assert "multipart/form-data" in r.json()["error"]["message"]
    r = client.post("/v1/audio/transcriptions", headers=QT, data={"model": "giong/nghe"})  # urlencoded
    assert r.status_code == 415
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": "multipart/form-data"}, content=b"x")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_multipart"  # thiếu boundary
    assert da_goi(client, "nghe") == []


def test_transcriptions_module_khong_co_nghe_501(client):
    body, ct = multipart([("model", "echo/x")], [("file", ("a.wav", AM_THANH, "audio/wav"))])
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 501 and r.json()["error"]["code"] == "capability_not_supported"


# ───────────── trần kích thước, tệp tạm ─────────────

def test_vuot_tran_theo_content_length_413_truoc_khi_doc(client, spool, monkeypatch):
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "1")
    body, ct = multipart(list(nghe().items()), [("file", ("a.wav", b"\0" * (1024 * 1024 + 1), "audio/wav"))])
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large", r.text
    assert "WEB2API_TEP_TOI_DA_MB" in r.json()["error"]["message"]
    assert spool == []  # chưa đọc byte nào của thân → chưa mở tệp tạm nào
    assert da_goi(client, "nghe") == []


def test_content_length_noi_doi_van_413_khi_dem_that(client, spool, monkeypatch):
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "1")
    body, ct = multipart(list(nghe().items()), [("file", ("a.wav", b"\1" * (3 * 1024 * 1024), "audio/wav"))])
    # Header nói thân chỉ 100 byte; thân thật 3 MB → lõi đếm byte thật, không tin header.
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct, "content-length": "100"},
                    content=body)
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large", r.text
    assert spool == []  # TestClient đưa cả thân trong MỘT khúc: chặn trước khi bộ đọc mở tệp tạm nào
    assert da_goi(client, "nghe") == []
    r = client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct, "content-length": "abc"},
                    content=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_content_length"


def test_chunked_khong_content_length_vuot_tran_413_tren_uvicorn_that(du_lieu, spool, monkeypatch):
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "1")
    body, ct = multipart(list(nghe().items()), [("file", ("a.wav", b"\2" * (3 * 1024 * 1024), "audio/wav"))])

    def tung_khuc():  # generator → httpx gửi Transfer-Encoding: chunked, không có Content-Length
        for i in range(0, len(body), 64 * 1024):
            yield body[i:i + 64 * 1024]

    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        assert h.post("/api/modules/giong/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        try:
            r = h.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=tung_khuc())
        except httpx.TransportError:
            r = None  # máy chủ trả 413 rồi đóng kết nối khi khách còn đang gửi — cũng là đã chặn
        if r is not None:
            assert r.status_code == 413, r.text
        assert app.state.so.dang_chay["giong"].nhan == []
        # Thân tới từng khúc 64 KB: bộ đọc đã mở tệp tạm và ghi dở ~1 MB khi bị chặn — tệp đó phải đã đóng.
        assert spool and all(f._rolled and f.closed for f in spool)
        # Thân hợp lệ, chunked, dưới trần vẫn đi được.
        nho, ct2 = multipart(list(nghe().items()), [("file", ("a.wav", AM_THANH, "audio/wav"))])
        r = h.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct2}, content=iter([nho]))
        assert r.status_code == 200, r.text


def test_tep_tam_dong_khi_module_loi(du_lieu, spool):
    with TestClient(app, raise_server_exceptions=False) as c:
        lien_ket(c)
        giong(c).loi = RuntimeError("module hỏng")
        body, ct = multipart(list(nghe().items()), [("file", ("a.wav", AM_THANH, "audio/wav"))])
        r = c.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body)
        assert r.status_code == 500
        assert len(da_goi(c, "nghe")) == 1
    assert spool and all(f._rolled and f.closed for f in spool)


def test_tep_tam_dong_khi_tu_choi_sau_khi_doc(client, spool):
    # Đã đọc trọn thân rồi mới biết sai (thiếu prompt, 501, 415 video) → tệp tạm vẫn đóng.
    body, ct = multipart([("model", "giong/sua")], [("image", ("a.png", AM_THANH, "image/png"))])
    assert client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body).status_code == 400
    body, ct = multipart([("model", "echo/x")], [("file", ("a.wav", AM_THANH, "audio/wav"))])
    assert client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body).status_code == 501
    body, ct = multipart([("model", "giong/v"), ("prompt", "p")], [("input_reference", ("a.png", AM_THANH, "image/png"))])
    assert client.post("/v1/videos", headers={**QT, "content-type": ct}, content=body).status_code == 415
    assert len(spool) == 3 and all(f._rolled and f.closed for f in spool)


def test_qua_nhieu_tep_400(client):
    body, ct = multipart([("model", "giong/sua"), ("prompt", "p")],
                         [("image[]", (f"{i}.png", b"x", "image/png")) for i in range(21)])
    r = client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_multipart"
    assert da_goi(client, "anh_sua") == []


def test_bo_nho_dinh_khoang_mot_lan_tep(du_lieu):
    """Tệp 20 MB (gần trần 25 MB) qua uvicorn thật: bộ nhớ đỉnh của cả tiến trình (lõi + khách, đo bằng
    tracemalloc) ≈ một bản tệp (bytes đưa module), không phải hai-ba bản."""
    co = 20 * 1024 * 1024
    body, ct = multipart(list(nghe().items()), [("file", ("lon.wav", b"\3" * co, "audio/wav"))])

    def tung_khuc():
        for i in range(0, len(body), 256 * 1024):
            yield body[i:i + 256 * 1024]

    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=60) as h:
        assert h.post("/api/modules/giong/cai", headers=QT, json={"cau_hinh": {"api_key": K1}}).status_code == 200
        tracemalloc.start()
        try:
            r = h.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=tung_khuc())
            _, dinh = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert r.status_code == 200, r.text
        assert len(app.state.so.dang_chay["giong"].nhan[-1][1].tep[0].noi_dung) == co
    print(f"\nbộ nhớ đỉnh {dinh / co:.2f}× cỡ tệp ({dinh / 1024 / 1024:.1f} MB cho tệp 20 MB)")
    assert dinh < 1.5 * co


# ───────────── /v1/images/edits ─────────────

def test_edits_mot_anh(client):
    body, ct = multipart([("model", "giong/sua"), ("prompt", "thêm mũ"), ("n", "1"), ("size", "1024x1024")],
                         [("image", ("a.png", b"PNG-A", "image/png"))])
    r = client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["data"][0]["b64_json"]) == b"PNG-A"
    yc = da_goi(client, "anh_sua")[-1]
    assert yc.truong == {"model": "giong/sua", "prompt": "thêm mũ", "n": "1", "size": "1024x1024"}
    assert [(t.ten_truong, t.ten_tep) for t in yc.tep] == [("image", "a.png")]


def test_edits_nhieu_anh_kieu_mang_va_mask(client):
    body, ct = multipart([("model", "giong/sua"), ("prompt", "ghép")],
                         [("image[]", ("a.png", b"PNG-A", "image/png")), ("image[]", ("b.webp", b"WEBP-B", "image/webp")),
                          ("mask", ("m.png", b"MASK", "image/png"))])
    r = client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["data"][0]["b64_json"]) == b"PNG-A|WEBP-B"
    yc = da_goi(client, "anh_sua")[-1]
    assert [(t.ten_truong, t.ten_tep, t.content_type) for t in yc.tep] == [
        ("image", "a.png", "image/png"), ("image", "b.webp", "image/webp"), ("mask", "m.png", "image/png")]
    assert yc.tep_ten("mask")[0].noi_dung == b"MASK"


def test_edits_thieu_prompt_hay_image_hay_hai_mask_400(client):
    anh = [("image", ("a.png", b"PNG", "image/png"))]
    for truong, tep, ma in (([("model", "giong/sua")], anh, "missing_prompt"),
                            ([("model", "giong/sua"), ("prompt", "  ")], anh, "missing_prompt"),
                            ([("model", "giong/sua"), ("prompt", "p")], [("mask", ("m.png", b"M", "image/png"))],
                             "missing_file"),
                            ([("model", "giong/sua"), ("prompt", "p")],
                             anh + [("mask", ("m.png", b"M", "image/png"))] * 2, "too_many_files"),
                            ([("prompt", "p")], anh, "missing_model")):
        body, ct = multipart(truong, tep)
        r = client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == ma, (truong, r.text)
    body, ct = multipart([("model", "khong-co/x"), ("prompt", "p")], anh)
    r = client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert da_goi(client, "anh_sua") == []


# ───────────── /v1/videos nhận multipart ─────────────

def test_video_multipart_thanh_than_json(client):
    # Đúng khuôn SDK gửi videos.create khi không có tệp: multipart chỉ có trường chữ.
    body, ct = multipart([("model", "giong/v"), ("prompt", "thác nước"), ("seconds", "8"), ("size", "720x1280")], [])
    r = client.post("/v1/videos", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "giong:v1"
    assert da_goi(client, "video_tao")[-1] == {"model": "giong/v", "prompt": "thác nước", "seconds": "8",
                                               "size": "720x1280"}
    # JSON cũ vẫn chạy, kiểu giữ nguyên.
    r = client.post("/v1/videos", headers=QT, json={"model": "giong/v", "prompt": "x", "seconds": 8})
    assert r.status_code == 200 and da_goi(client, "video_tao")[-1]["seconds"] == 8


def test_video_multipart_co_tep_415_ro(client):
    body, ct = multipart([("model", "giong/v"), ("prompt", "p")],
                         [("input_reference", ("a.png", b"PNG", "image/png"))])
    r = client.post("/v1/videos", headers={**QT, "content-type": ct}, content=body)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"
    assert "input_reference" in r.json()["error"]["message"] and "tệp tham chiếu" in r.json()["error"]["message"]
    assert da_goi(client, "video_tao") == []
    # Module không có video: 501 đứng trước 415 của phần tệp.
    body, ct = multipart([("model", "echo/x"), ("prompt", "p")], [("input_reference", ("a.png", b"P", "image/png"))])
    assert client.post("/v1/videos", headers={**QT, "content-type": ct}, content=body).status_code == 501


# ───────────── khoá không lộ ─────────────

def test_loi_cac_duong_moi_khong_lo_khoa(client, caplog, monkeypatch):
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "0.001")
    van_ban = []
    with caplog.at_level(logging.DEBUG):
        body, ct = multipart(list(nghe().items()), [("file", ("a.wav", AM_THANH, "audio/wav"))])
        van_ban.append(client.post("/v1/audio/transcriptions", headers={**QT, "content-type": ct}, content=body).text)
        monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "khong-phai-so")  # → mặc định, có cảnh báo trong log
        van_ban.append(client.post("/v1/audio/transcriptions", headers=QT, json={"model": "giong/nghe"}).text)
        van_ban.append(client.post("/v1/audio/speech", headers=QT, json={"model": "echo/x"}).text)
        body, ct = multipart([("model", "giong/sua")], [("image", ("a.png", b"P", "image/png"))])
        van_ban.append(client.post("/v1/images/edits", headers={**QT, "content-type": ct}, content=body).text)
        van_ban.append(client.post("/v1/audio/speech", headers={"Authorization": "Bearer " + K1},
                                   json={"model": "giong/doc"}).text)
    assert "WEB2API_TEP_TOI_DA_MB" in caplog.text
    khong_ro(*van_ban, caplog.text)


# ───────────── SDK openai thật ─────────────

@pytest.fixture
def sdk(du_lieu):
    openai = pytest.importorskip("openai")
    with MayChuThat(app, lifespan="on") as w, httpx.Client(base_url=w.url, timeout=15) as h:
        for id_, ch in (("giong", {"api_key": K1}), ("echo", {})):
            assert h.post(f"/api/modules/{id_}/cai", headers=QT, json={"cau_hinh": ch}).status_code == 200
        yield openai, openai.OpenAI(base_url=w.url + "/v1", api_key=KHOA_QT, max_retries=0, timeout=15)


def test_sdk_openai_speech(sdk):
    _, c = sdk
    r = c.audio.speech.create(model="giong/doc", voice="alloy", input="xin chào")
    assert r.content == b"ID3" + "xin chào".encode()
    m = app.state.so.dang_chay["giong"]
    m.cho_khuc.set()
    with c.audio.speech.with_streaming_response.create(model="giong/doc", voice="alloy", input="x",
                                                       extra_body={"stream": True}) as s:
        assert b"".join(s.iter_bytes()) == b"KHUC-1;KHUC-2;KHUC-3"


def test_sdk_openai_transcriptions(sdk):
    _, c = sdk
    t = c.audio.transcriptions.create(model="giong/nghe", file=("a.wav", AM_THANH, "audio/wav"), language="vi",
                                      timestamp_granularities=["word", "segment"], response_format="json")
    assert t.text == f"a.wav:{len(AM_THANH)}"
    yc = app.state.so.dang_chay["giong"].nhan[-1][1]
    assert yc.truong["timestamp_granularities"] == ["word", "segment"] and yc.truong["language"] == "vi"
    assert hashlib.sha256(yc.tep[0].noi_dung).digest() == hashlib.sha256(AM_THANH).digest()


def test_sdk_openai_images_edit(sdk):
    _, c = sdk
    r = c.images.edit(model="giong/sua", prompt="ghép",
                      image=[("a.png", b"PNG-A", "image/png"), ("b.png", b"PNG-B", "image/png")],
                      mask=("m.png", b"M", "image/png"))
    assert base64.b64decode(r.data[0].b64_json) == b"PNG-A|PNG-B"


def test_sdk_openai_videos_create(sdk):
    openai, c = sdk
    # SDK gửi videos.create bằng multipart kể cả khi không có tệp (đo ở SDK 3.28) — trước đợt này lõi trả 415.
    v = c.videos.create(model="giong/v", prompt="thác nước", seconds="8", size="720x1280")
    assert v.id == "giong:v1"
    assert app.state.so.dang_chay["giong"].nhan[-1] == (
        "video_tao", {"prompt": "thác nước", "model": "giong/v", "seconds": "8", "size": "720x1280"})
    with pytest.raises(openai.APIStatusError) as e:
        c.videos.create(model="giong/v", prompt="p", input_reference=("a.png", b"PNG", "image/png"))
    assert e.value.status_code == 415 and "tệp tham chiếu" in e.value.message
