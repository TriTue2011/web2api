"""Test bộ chuyển tiếp dùng được cho âm thanh/ảnh nhị phân và multipart (module từ xa).

Kiểm tra:
- TTS trả byte nhị phân đủ 256 giá trị, chứa \n, \r, \x00 -> giữ nguyên từng byte (so băm), content-type giữ,
  không có content-length / content-encoding sai.
- Dịch vụ xa trả gzip -> khách nhận bản đã giải nén đúng, không còn header content-encoding gzip.
- STT multipart: tên trường/tệp/content_type tới dịch vụ xa đúng; tên tệp chứa ../ và ký tự điều khiển
  được làm sạch; content_type rác -> application/octet-stream.
- Sửa ảnh multipart tương tự.
- Trần kích thước multipart: vượt trần -> 413, dịch vụ xa không bị gọi.
- Hợp đồng aclose: khách đọc hết hoặc ngắt THẬT (uvicorn, đóng socket) -> lõi gọi aclose() trên iterator module
  đúng một lần và kết nối ngược dòng bị đóng; iterator chưa chạy mà bị aclose() cũng đóng kết nối ngược dòng.
- Chống SSRF: chuyển hướng 3xx của dịch vụ xa không bị theo (dịch vụ thứ hai không nhận yêu cầu); đích bị chặn lúc
  kết nối (ghim IP) -> 502 `ssrf_blocked`, không thử khoá khác, không đánh dấu khoá.
- Header của dịch vụ xa: mặc định từ chối, chỉ `content-type` tới khách (Set-Cookie, CORS, Server… bị bỏ).
- Sổ nghỉ theo (khoá, khả năng): TTS 403 không làm chat nghỉ, chat 429 không chặn TTS; 429 cuối kèm Retry-After.
- Luồng nhị phân đứt giữa chừng -> khách thấy kết nối bị cắt (không phải 200 sạch), nhật ký ghi `dut`.
- Đường gọi = `{base_url}/<endpoint>` như chat, với mọi dạng base_url.
- Client không ghim IP vẫn theo proxy môi trường (HTTP(S)_PROXY/NO_PROXY).
- Lỗi thượng nguồn chứa khoá giả -> không lọt vào thân lỗi (đã che).
- Module không khai chat nạp được và gọi chat trả 501 chuẩn OpenAI.
- Kiểm leo thang quyền: không khoá -> 401; khoá khách thiếu phạm vi -> 403.
"""

import asyncio
import gzip
import hashlib
import json
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app import khoa_khach, so_module
from app.kham_pha import doc_manifest, go_ma, nap_ma, van_tay
from app.main import app
from gia import MayChuThat

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_XA = "sk-lan-0000000000000000000000000000xyzw"


class DichVuNhiPhanGia:
    """Máy chủ ASGI cục bộ mô phỏng dịch vụ OpenAI từ xa hỗ trợ TTS, STT và sửa ảnh."""

    def __init__(self, khoa: str | None = KHOA_XA):
        self.khoa = khoa
        self.nhat_ky: list[dict] = []
        self.du_lieu_tts: bytes = bytes(range(256)) * 10
        self.tts_gzip = False
        self.chuyen_huong_url: str | None = None
        self.loi_nhac_khoa = False
        self.ma_loi: int | None = None

        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/audio/speech", self.speech, methods=["POST"]),
            Route("/v1/audio/transcriptions", self.transcriptions, methods=["POST"]),
            Route("/v1/images/edits", self.image_edits, methods=["POST"]),
        ])

    def _kiem_khoa(self, req: Request):
        if self.khoa and req.headers.get("authorization") != f"Bearer {self.khoa}":
            return JSONResponse({"error": {"message": f"sai khoá {req.headers.get('authorization')}",
                                           "type": "invalid_request_error"}}, status_code=401)
        return None

    async def models(self, req: Request):
        self.nhat_ky.append({"duong": "models", "auth": req.headers.get("authorization")})
        return self._kiem_khoa(req) or JSONResponse({
            "object": "list",
            "data": [
                {"id": "giong-noi-1", "object": "model", "owned_by": "lan"},
                {"id": "whisper-lan", "object": "model", "owned_by": "lan"},
                {"id": "sua-anh-lan", "object": "model", "owned_by": "lan"},
            ]
        })

    async def speech(self, req: Request):
        than = await req.json()
        self.nhat_ky.append({"duong": "speech", "auth": req.headers.get("authorization"), "than": than})
        sai = self._kiem_khoa(req)
        if sai:
            return sai
        if self.chuyen_huong_url:
            return Response(status_code=302, headers={"location": self.chuyen_huong_url})
        if self.ma_loi:
            msg = f"Dịch vụ lỗi {self.ma_loi}: {self.khoa}" if self.loi_nhac_khoa else "Dịch vụ từ chối"
            return JSONResponse({"error": {"message": msg, "type": "api_error", "code": self.ma_loi}},
                                status_code=self.ma_loi)
        if self.tts_gzip:
            nen = gzip.compress(self.du_lieu_tts)
            return Response(nen, media_type="audio/ogg", headers={"content-encoding": "gzip"})
        return Response(self.du_lieu_tts, media_type="audio/mpeg")

    async def transcriptions(self, req: Request):
        sai = self._kiem_khoa(req)
        if sai:
            return sai
        if self.chuyen_huong_url:
            return Response(status_code=302, headers={"location": self.chuyen_huong_url})
        if self.ma_loi:
            return JSONResponse({"error": {"message": "Dịch vụ lỗi", "type": "api_error", "code": self.ma_loi}},
                                status_code=self.ma_loi)
        form = await req.form()
        truong = {}
        tep = []
        for k, v in form.multi_items():
            if isinstance(v, str):
                truong[k] = v
            else:
                tep.append({
                    "truong": k,
                    "filename": v.filename,
                    "content_type": v.content_type,
                    "noi_dung": await v.read(),
                })
        self.nhat_ky.append({
            "duong": "transcriptions",
            "auth": req.headers.get("authorization"),
            "truong": truong,
            "tep": tep,
        })
        return JSONResponse({"text": "văn bản đã nhận dạng"})

    async def image_edits(self, req: Request):
        sai = self._kiem_khoa(req)
        if sai:
            return sai
        form = await req.form()
        truong = {}
        tep = []
        for k, v in form.multi_items():
            if isinstance(v, str):
                truong[k] = v
            else:
                tep.append({
                    "truong": k,
                    "filename": v.filename,
                    "content_type": v.content_type,
                    "noi_dung": await v.read(),
                })
        self.nhat_ky.append({
            "duong": "image_edits",
            "auth": req.headers.get("authorization"),
            "truong": truong,
            "tep": tep,
        })
        return JSONResponse({"data": [{"b64_json": "anh-da-sua"}]})


class DichVuDich2:
    """Máy chủ đích thứ 2 dùng để kiểm tra việc không theo chuyển hướng 3xx."""

    def __init__(self):
        self.so_lan_goi = 0
        self.app = Starlette(routes=[
            Route("/{path:path}", self.handler, methods=["GET", "POST"]),
        ])

    async def handler(self, req: Request):
        self.so_lan_goi += 1
        return JSONResponse({"ok": True})


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


@pytest.fixture
def dich_vu_xa():
    dv = DichVuNhiPhanGia()
    with MayChuThat(dv.app) as s:
        dv.url = s.url
        yield dv


def dang_ky_tu_xa(c: TestClient, dv: DichVuNhiPhanGia, tien_to: str = "lan"):
    than = {
        "id": "dv-lan",
        "ten": "Dịch vụ nhị phân trong LAN",
        "tien_to": tien_to,
        "base_url": dv.url + "/v1",
        "api_key": KHOA_XA,
    }
    return c.post("/api/modules/tu-xa", json=than, headers=QT)


# ───────────────────────────── 1. Test TTS Nhị phân ─────────────────────────────


def test_tts_tra_byte_nhi_phan_chua_du_256_byte(du_lieu, dich_vu_xa):
    """TTS trả byte nhị phân đủ 256 giá trị, chứa \n, \r, \x00 -> tới tay khách giống hệt từng byte."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        body = {"model": "lan/giong-noi-1", "input": "Xin chào thế giới"}
        r = c.post("/v1/audio/speech", json=body, headers=QT)
        assert r.status_code == 200
        assert r.headers.get("content-type") == "audio/mpeg"
        # Băm giống hệt
        bam_goc = hashlib.sha256(dich_vu_xa.du_lieu_tts).hexdigest()
        bam_nhan = hashlib.sha256(r.content).hexdigest()
        assert bam_nhan == bam_goc
        # Không có header content-encoding sai
        assert "content-encoding" not in r.headers
        # Request gửi sang dịch vụ xa đã bỏ tiền tố model
        assert dich_vu_xa.nhat_ky[-1]["than"]["model"] == "giong-noi-1"


def test_tts_dich_vu_xa_tra_gzip_khach_giai_nen_dung(du_lieu, dich_vu_xa):
    """Dịch vụ xa trả gzip -> khách nhận bản đã giải nén đúng, không còn header content-encoding gzip."""
    dich_vu_xa.tts_gzip = True
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        body = {"model": "lan/giong-noi-1", "input": "Kiểm tra gzip"}
        r = c.post("/v1/audio/speech", json=body, headers=QT)
        assert r.status_code == 200
        assert r.headers.get("content-type") == "audio/ogg"
        # Đã giải nén đúng
        bam_goc = hashlib.sha256(dich_vu_xa.du_lieu_tts).hexdigest()
        bam_nhan = hashlib.sha256(r.content).hexdigest()
        assert bam_nhan == bam_goc
        # Header content-encoding không còn là gzip (tránh trình duyệt giải nén 2 lần)
        assert r.headers.get("content-encoding") != "gzip"


# ───────────────────────────── 2. Test STT & Sửa ảnh Multipart ─────────────────────────────


def test_stt_multipart_ten_truong_tep_content_type(du_lieu, dich_vu_xa):
    """STT multipart: tên trường/tệp/content_type tới dịch vụ xa đúng, tên tệp được làm sạch."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        tep_am_thanh = b"AUDIO-BINARY-DATA-\x00\x01\x02"
        # Tên tệp bẩn chứa ../ và ký tự điều khiển
        ten_tep_ban = "../../../duong/dan/\x00xau\x1f_ghi_am.wav"
        data = {
            "model": "lan/whisper-lan",
            "language": "vi",
            "truong_la_rieng": "gia_tri_dac_biet",
        }
        files = {
            "file": (ten_tep_ban, tep_am_thanh, "audio/wav"),
        }
        r = c.post("/v1/audio/transcriptions", data=data, files=files, headers=QT)
        assert r.status_code == 200
        assert r.json() == {"text": "văn bản đã nhận dạng"}

        # Dịch vụ xa nhận được
        nk = dich_vu_xa.nhat_ky[-1]
        assert nk["duong"] == "transcriptions"
        assert nk["truong"]["model"] == "whisper-lan"  # đã bỏ tiền tố
        assert nk["truong"]["language"] == "vi"
        assert nk["truong"]["truong_la_rieng"] == "gia_tri_dac_biet"

        tep_nhan = nk["tep"][0]
        assert tep_nhan["truong"] == "file"
        assert tep_nhan["noi_dung"] == tep_am_thanh
        assert tep_nhan["content_type"] == "audio/wav"
        # Tên tệp đã được làm sạch: không chứa .. hay ký tự điều khiển
        assert ".." not in tep_nhan["filename"]
        assert "/" not in tep_nhan["filename"]
        assert "\x00" not in tep_nhan["filename"]
        assert "\x1f" not in tep_nhan["filename"]
        assert tep_nhan["filename"] == "xau_ghi_am.wav"


def test_stt_content_type_rac_thanh_octet_stream(du_lieu, dich_vu_xa):
    """MIME type rác từ khách khai -> đổi thành application/octet-stream."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        data = {"model": "lan/whisper-lan"}
        files = {"file": ("ghi_am.wav", b"AUDIO", "kieu-rac-khong-hop-le")}
        r = c.post("/v1/audio/transcriptions", data=data, files=files, headers=QT)
        assert r.status_code == 200

        tep_nhan = dich_vu_xa.nhat_ky[-1]["tep"][0]
        assert tep_nhan["content_type"] == "application/octet-stream"


def test_anh_sua_multipart(du_lieu, dich_vu_xa):
    """POST /v1/images/edits multipart chuyển nguyên tới dịch vụ xa."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        data = {
            "model": "lan/sua-anh-lan",
            "prompt": "Thêm một chú mèo",
            "size": "512x512",
        }
        files = [
            ("image", ("anh.png", b"PNG-IMAGE-BYTES", "image/png")),
            ("mask", ("mat_na.png", b"PNG-MASK-BYTES", "image/png")),
        ]
        r = c.post("/v1/images/edits", data=data, files=files, headers=QT)
        assert r.status_code == 200
        assert r.json() == {"data": [{"b64_json": "anh-da-sua"}]}

        nk = dich_vu_xa.nhat_ky[-1]
        assert nk["duong"] == "image_edits"
        assert nk["truong"]["model"] == "sua-anh-lan"
        assert nk["truong"]["prompt"] == "Thêm một chú mèo"
        assert nk["truong"]["size"] == "512x512"
        ten_cac_tep = {t["truong"]: t for t in nk["tep"]}
        assert "image" in ten_cac_tep and ten_cac_tep["image"]["noi_dung"] == b"PNG-IMAGE-BYTES"
        assert "mask" in ten_cac_tep and ten_cac_tep["mask"]["noi_dung"] == b"PNG-MASK-BYTES"


def test_tran_kich_thuoc_multipart(du_lieu, dich_vu_xa, monkeypatch):
    """Trần kích thước tệp multipart: vượt trần -> 413, dịch vụ xa không bị gọi."""
    # Đặt trần nhỏ: 1 MB
    monkeypatch.setenv("WEB2API_TEP_TOI_DA_MB", "1")
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        so_luot_truoc = len(dich_vu_xa.nhat_ky)
        # Gửi file 2 MB vượt trần
        tep_lon = b"0" * (2 * 1024 * 1024)
        data = {"model": "lan/whisper-lan"}
        files = {"file": ("lon.wav", tep_lon, "audio/wav")}
        r = c.post("/v1/audio/transcriptions", data=data, files=files, headers=QT)
        assert r.status_code == 413
        assert "error" in r.json()
        # Dịch vụ xa không nhận được yêu cầu
        assert len(dich_vu_xa.nhat_ky) == so_luot_truoc


# ───────────────────────────── 3. Test Đếm aclose khi Khách Ngắt Sớm & Đọc Hết ─────────────────────────────


class DemDong:
    """Bọc iterator nhị phân của module: đếm số lần lõi gọi aclose(), chuyển nguyên từng khúc và lỗi."""

    def __init__(self, it):
        self._goc = it
        self._it = it.__aiter__()
        self.so_lan_dong = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._it.__anext__()

    async def aclose(self):
        self.so_lan_dong += 1
        await self._goc.aclose()


def _boc_noi_dem_dong(monkeypatch) -> list[DemDong]:
    """ModuleTuXa.noi trả iterator đã bọc DemDong; trả danh sách bộ đếm theo thứ tự lượt gọi."""
    from app.tu_xa import ModuleTuXa
    goc = ModuleTuXa.noi
    ds: list[DemDong] = []

    async def noi(self, body):
        ma, hd, nd = await goc(self, body)
        if hasattr(nd, "__aiter__"):
            nd = DemDong(nd)
            ds.append(nd)
        return ma, hd, nd
    monkeypatch.setattr(ModuleTuXa, "noi", noi)
    return ds


def _cho(dieu_kien, giay: float = 5.0) -> bool:
    han = time.monotonic() + giay
    while time.monotonic() < han:
        if dieu_kien():
            return True
        time.sleep(0.02)
    return dieu_kien()


def _goi_tho(url_lo: str, duong: str, than: bytes) -> socket.socket:
    """Gửi một POST /v1 thô qua socket tới lõi chạy uvicorn thật; trả socket để test tự đọc/đóng."""
    may, cong = url_lo.removeprefix("http://").split(":")
    sk = socket.create_connection((may, int(cong)), timeout=10)
    sk.sendall(f"POST {duong} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {KHOA_QT}\r\n"
               f"Content-Type: application/json\r\nContent-Length: {len(than)}\r\n\r\n".encode() + than)
    return sk


def _dang_ky_tho(url_lo: str, url_xa: str) -> None:
    with httpx.Client(base_url=url_lo, timeout=10) as h:
        r = h.post("/api/modules/tu-xa", headers=QT, json={
            "id": "dv-lan", "ten": "DV", "tien_to": "lan", "base_url": url_xa + "/v1", "api_key": KHOA_XA})
        assert r.status_code == 200, r.text


def _dich_vu_phat_cham(trang_thai: dict) -> Starlette:
    """Dịch vụ xa phát TTS chậm và lâu (2000 khúc); `trang_thai["ket_thuc"]` = "het" nếu phát xong, "bi_huy" nếu
    kết nối của nó bị đóng giữa chừng."""
    async def speech(req):
        async def phat():
            try:
                for _ in range(2000):
                    trang_thai["da_phat"] += 1
                    yield b"\x00\n\r" * 300
                    await asyncio.sleep(0.01)
                trang_thai["ket_thuc"] = "het"
            finally:
                if trang_thai["ket_thuc"] is None:
                    trang_thai["ket_thuc"] = "bi_huy"
        return StreamingResponse(phat(), media_type="audio/mpeg")
    return Starlette(routes=[Route("/v1/audio/speech", speech, methods=["POST"])])


def test_khach_doc_het_aclose_mot_lan(du_lieu, dich_vu_xa, monkeypatch):
    """Khách đọc hết luồng TTS -> lõi gọi aclose() trên iterator của module đúng một lần."""
    dem = _boc_noi_dem_dong(monkeypatch)
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200
        with c.stream("POST", "/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "x"}, headers=QT) as r:
            assert r.status_code == 200
            assert b"".join(r.iter_bytes()) == dich_vu_xa.du_lieu_tts
    assert [d.so_lan_dong for d in dem] == [1]


def test_khach_ngat_that_giua_luong_aclose_mot_lan_va_dong_nguoc_dong(du_lieu, monkeypatch):
    """Ngắt THẬT qua uvicorn: khách đọc vài khúc rồi đóng socket -> iterator của module được aclose() đúng một lần
    và kết nối ngược dòng bị đóng (dịch vụ xa thấy luồng của nó bị huỷ, không phát tiếp tới hết)."""
    trang_thai = {"da_phat": 0, "ket_thuc": None}
    dem = _boc_noi_dem_dong(monkeypatch)
    with MayChuThat(_dich_vu_phat_cham(trang_thai)) as s, MayChuThat(app, lifespan="on") as w:
        _dang_ky_tho(w.url, s.url)
        sk = _goi_tho(w.url, "/v1/audio/speech", json.dumps({"model": "lan/giong-noi-1", "input": "x"}).encode())
        nhan = b""
        while len(nhan) < 5000:
            khuc = sk.recv(4096)
            assert khuc, "lõi đóng trước khi gửi đủ vài khúc"
            nhan += khuc
        assert nhan.startswith(b"HTTP/1.1 200")
        sk.close()
        assert _cho(lambda: len(dem) == 1 and dem[0].so_lan_dong == 1), [d.so_lan_dong for d in dem]
        assert _cho(lambda: trang_thai["ket_thuc"] is not None)
        time.sleep(0.3)
        assert dem[0].so_lan_dong == 1
    assert trang_thai["ket_thuc"] == "bi_huy" and trang_thai["da_phat"] < 2000


def test_iterator_chua_chay_aclose_van_dong_ket_noi_nguoc_dong(du_lieu):
    """aclose() iterator nhị phân khi CHƯA đọc khúc nào (khách ngắt trước byte đầu) -> phản hồi ngược dòng vẫn
    đóng. Async generator chưa chạy thì aclose() không chạy `finally` của nó: kết nối treo trong pool."""
    from app.chuyen_tiep import ChuyenTiepOpenAI
    trang_thai = {"da_phat": 0, "ket_thuc": None}

    async def chay(url):
        ct = ChuyenTiepOpenAI(ten="X", tien_to="x", base_url=url + "/v1", khoa=["k"])
        try:
            ma, _, it = await ct.noi({"model": "x/m", "input": "a"})
            assert ma == 200
            await it.aclose()
            for _ in range(150):
                if trang_thai["ket_thuc"] is not None:
                    break
                await asyncio.sleep(0.02)
            # Đọc TRƯỚC khi đóng client: dung() đóng cả pool, khi đó kết nối treo nào cũng bị đóng theo.
            return trang_thai["ket_thuc"]
        finally:
            await ct.dung()

    with MayChuThat(_dich_vu_phat_cham(trang_thai)) as s:
        assert asyncio.run(chay(s.url)) == "bi_huy"


# ───────────────────────────── 4. Test Chống SSRF: Chuyển hướng 3xx ─────────────────────────────


def test_chuyen_huong_3xx_khong_bi_theo(du_lieu, dich_vu_xa):
    """Chuyển hướng 3xx của dịch vụ xa KHÔNG bị theo (dịch vụ thứ hai không nhận yêu cầu)."""
    dich_2 = DichVuDich2()
    with MayChuThat(dich_2.app) as s2:
        dich_vu_xa.chuyen_huong_url = f"{s2.url}/v1/dich-doc-hai"
        with TestClient(app) as c:
            assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

            r = c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "test"}, headers=QT)
            # Không theo chuyển hướng: nhận HTTP 302 lỗi chuẩn OpenAI
            assert r.status_code == 302
            assert "error" in r.json()
            # Dịch vụ thứ hai không nhận được bất kỳ yêu cầu nào
            assert dich_2.so_lan_goi == 0


# ───────────────────────────── 5. Test Chuẩn hoá lỗi và Che khoá giả ─────────────────────────────


def test_loi_thuong_nguon_chua_chuoi_gia_khong_lot(du_lieu, dich_vu_xa):
    """Lỗi thượng nguồn chứa chuỗi khoá giả -> không lọt vào thân lỗi (đã được che)."""
    dich_vu_xa.ma_loi = 400
    dich_vu_xa.loi_nhac_khoa = True
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        r = c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "test"}, headers=QT)
        assert r.status_code == 400
        # Khoá giả KHOA_XA tuyệt đối không xuất hiện nguyên văn
        assert KHOA_XA not in r.text
        # Nhưng dạng che …xyzw có thể xuất hiện
        assert "…xyzw" in r.text


def test_dich_vu_xa_404_501_tra_ve_loi_chuan_khong_treo(du_lieu, dich_vu_xa):
    """Dịch vụ xa trả 404 hoặc 501 cho đường đó thì trả về như lỗi chuẩn, không treo."""
    dich_vu_xa.ma_loi = 404
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        r = c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "test"}, headers=QT)
        assert r.status_code == 404
        assert "error" in r.json()

        dich_vu_xa.ma_loi = 501
        r2 = c.post("/v1/audio/transcriptions", data={"model": "lan/whisper-lan"},
                    files={"file": ("test.wav", b"data", "audio/wav")}, headers=QT)
        assert r2.status_code == 501
        assert "error" in r2.json()


# ───────────────────────────── 6. Test Module Không Khai chat Nạp Được ─────────────────────────────


def test_module_khong_khai_chat_nap_duoc_va_chat_tra_501(du_lieu, tmp_path):
    """Module chỉ làm giọng nói (không khai chat trong cung_cap, không hàm chat) nạp được; gọi chat -> 501."""
    thu_muc = tmp_path / "module-chi-noi"
    thu_muc.mkdir()
    manifest = {
        "id": "chi-noi",
        "ten": "Module chỉ nói",
        "mo_ta": "Chỉ làm TTS",
        "loai": "ai",
        "giao_thuc": "openai",
        "tien_to": "noi",
        "truong_cau_hinh": [],
        "tai_nguyen": {"mang_ra": []},
        "cung_cap": ["noi"],
    }
    (thu_muc / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    ma_module = '''
class Module:
    def __init__(self, manifest):
        self.manifest = manifest
    def nap(self, cau_hinh):
        pass
    async def cai(self, cau_hinh):
        return {"ok": True, "ket_qua_tung_khoa": [], "loi": None}
    async def models(self):
        return [{"id": "noi/tts-1", "object": "model"}]
    async def suc_khoe(self):
        return {"ok": True, "khoa": []}
    async def noi(self, body):
        return 200, {"content-type": "audio/mpeg"}, b"AUDIO"
    async def dung(self):
        pass
'''
    (thu_muc / "module.py").write_text(ma_module.strip(), encoding="utf-8")

    m, ly_do = doc_manifest(thu_muc)
    assert ly_do is None, ly_do

    vt = van_tay(thu_muc)
    lop, ten = nap_ma(thu_muc, m, vt)
    try:
        the_hien = lop(m)
        # Nạp thành công và có hàm noi
        assert callable(getattr(the_hien, "noi", None))
        # Không có hàm chat
        assert getattr(the_hien, "chat", None) is None

        # Thử nghiệm gọi qua API
        with TestClient(app) as c:
            so = c.app.state.so
            so.tu_xa["chi-noi"] = {"id": "chi-noi", "ten": "Chỉ nói", "tien_to": "noi"}
            so.dang_chay["chi-noi"] = the_hien

            # Gọi noi -> 200
            r_noi = c.post("/v1/audio/speech", json={"model": "noi/tts-1", "input": "test"}, headers=QT)
            assert r_noi.status_code == 200

            # Gọi chat -> 501 chuẩn OpenAI
            r_chat = c.post("/v1/chat/completions", json={"model": "noi/tts-1", "messages": []}, headers=QT)
            assert r_chat.status_code == 501
            assert r_chat.json()["error"]["code"] == "capability_not_supported"
    finally:
        go_ma(ten)


# ───────────────────────────── 7. Test Bảo Mật — Leo Thang Quyền ─────────────────────────────


def test_bao_mat_khong_khoa_tra_401(du_lieu, dich_vu_xa):
    """Leo thang quyền: không có khoá xác thực -> 401."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        # Không khoá -> 401
        assert c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "x"}).status_code == 401
        assert c.post("/v1/audio/transcriptions", data={"model": "lan/whisper-lan"},
                      files={"file": ("a.wav", b"data", "audio/wav")}).status_code == 401
        assert c.post("/v1/images/edits", data={"model": "lan/sua-anh-lan", "prompt": "p"},
                      files={"image": ("a.png", b"data", "image/png")}).status_code == 401


def test_bao_mat_khoa_khach_thieu_pham_vi_tra_403(du_lieu, dich_vu_xa):
    """Leo thang quyền: khoá khách thiếu phạm vi -> 403."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        # Tạo khoá khách CHỈ có phạm vi 'chat'
        tao = c.post("/api/khoa-khach", headers=QT, json={"nhan": "Khách chỉ chat", "pham_vi": ["chat"]})
        assert tao.status_code == 200
        khoa_kh = tao.json()["khoa"]["khoa"]
        hd_kh = {"Authorization": f"Bearer {khoa_kh}"}

        # Gọi các đường khác -> 403
        r_noi = c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "x"}, headers=hd_kh)
        assert r_noi.status_code == 403
        assert r_noi.json()["error"]["code"] == "insufficient_scope"

        r_nghe = c.post("/v1/audio/transcriptions", data={"model": "lan/whisper-lan"},
                        files={"file": ("a.wav", b"data", "audio/wav")}, headers=hd_kh)
        assert r_nghe.status_code == 403

        r_anh = c.post("/v1/images/edits", data={"model": "lan/sua-anh-lan", "prompt": "p"},
                       files={"image": ("a.png", b"data", "image/png")}, headers=hd_kh)
        assert r_anh.status_code == 403


def test_bao_mat_khoa_khach_dung_pham_vi_thanh_cong(du_lieu, dich_vu_xa):
    """Leo thang quyền: khoá khách đúng phạm vi -> thành công 200."""
    with TestClient(app) as c:
        assert dang_ky_tu_xa(c, dich_vu_xa).status_code == 200

        # Tạo khoá khách có đủ phạm vi
        tao = c.post("/api/khoa-khach", headers=QT,
                     json={"nhan": "Khách đa năng", "pham_vi": ["noi", "nghe", "anh_sua"]})
        assert tao.status_code == 200
        khoa_kh = tao.json()["khoa"]["khoa"]
        hd_kh = {"Authorization": f"Bearer {khoa_kh}"}

        # Gọi thành công
        r_noi = c.post("/v1/audio/speech", json={"model": "lan/giong-noi-1", "input": "x"}, headers=hd_kh)
        assert r_noi.status_code == 200

        r_nghe = c.post("/v1/audio/transcriptions", data={"model": "lan/whisper-lan"},
                        files={"file": ("a.wav", b"data", "audio/wav")}, headers=hd_kh)
        assert r_nghe.status_code == 200


# ───────────────────────────── 8. Ranh giới với dịch vụ xa (header, sổ nghỉ, luồng đứt, đường, SSRF, proxy) ─────


#: Header dịch vụ xa gửi kèm mà KHÔNG được tới khách: cookie đặt lên origin của lõi, mở CORS, lộ phần mềm nội bộ,
#: hộp đăng nhập của trình duyệt, khung kết nối hop-by-hop, ép tải về/đổi tên tệp, CSP đè CSP của lõi.
HEADER_XA_DOC = {
    "Set-Cookie": "sid=KE-XAU-DAT-COOKIE; Path=/",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Credentials": "true",
    "Server": "internal-gpu/9.9",
    "X-Powered-By": "secret-stack",
    "WWW-Authenticate": 'Basic realm="noi-bo"',
    "Connection": "close",
    "Keep-Alive": "timeout=5",
    "Content-Disposition": "attachment; filename=trang.html",
    "Content-Security-Policy": "default-src * 'unsafe-inline'",
}


def _dich_vu_tra_header_doc() -> Starlette:
    async def speech(req):
        return Response(b"AUDIO", media_type="audio/mpeg", headers=HEADER_XA_DOC)

    async def json_ok(req):
        await req.body()  # đọc hết thân multipart trước khi trả (Connection: close mà chưa đọc là khách gửi dở)
        return JSONResponse({"text": "ok", "choices": [], "data": []}, headers=HEADER_XA_DOC)
    return Starlette(routes=[
        Route("/v1/audio/speech", speech, methods=["POST"]),
        Route("/v1/audio/transcriptions", json_ok, methods=["POST"]),
        Route("/v1/images/edits", json_ok, methods=["POST"]),
        Route("/v1/chat/completions", json_ok, methods=["POST"]),
    ])


def _goi_duong(c: TestClient, duong: str, hd: dict):
    if duong == "noi":
        return c.post("/v1/audio/speech", json={"model": "lan/g", "input": "x"}, headers=hd)
    if duong == "nghe":
        return c.post("/v1/audio/transcriptions", data={"model": "lan/w"},
                      files={"file": ("a.wav", b"AUDIO", "audio/wav")}, headers=hd)
    if duong == "anh_sua":
        return c.post("/v1/images/edits", data={"model": "lan/s", "prompt": "p"},
                      files={"image": ("a.png", b"PNG", "image/png")}, headers=hd)
    return c.post("/v1/chat/completions", json={"model": "lan/m", "messages": [{"role": "user", "content": "x"}]},
                  headers=hd)


@pytest.mark.parametrize("duong", ["noi", "nghe", "anh_sua", "chat"])
def test_header_dich_vu_xa_mac_dinh_tu_choi_chi_content_type(du_lieu, duong):
    """Dịch vụ xa trả Set-Cookie, CORS, Server, X-Powered-By, WWW-Authenticate, Connection, Keep-Alive,
    Content-Disposition, CSP -> khách chỉ nhận content-type của nó; không giá trị nào của các header kia lọt ra."""
    with MayChuThat(_dich_vu_tra_header_doc()) as s, TestClient(app) as c:
        r = c.post("/api/modules/tu-xa", headers=QT, json={
            "id": "dv-lan", "ten": "DV", "tien_to": "lan", "base_url": s.url + "/v1", "api_key": KHOA_XA})
        assert r.status_code == 200, r.text
        r = _goi_duong(c, duong, QT)
        assert r.status_code == 200, r.text
        assert "set-cookie" not in r.headers
        assert not r.cookies
        for ten in ("access-control-allow-origin", "access-control-allow-credentials", "x-powered-by",
                    "www-authenticate", "keep-alive", "content-disposition", "server", "connection"):
            assert ten not in r.headers, (ten, r.headers.get(ten))
        tat_ca = "\n".join(f"{k}: {v}" for k, v in r.headers.multi_items())
        for gia_tri in HEADER_XA_DOC.values():
            assert gia_tri not in tat_ca, gia_tri
        assert r.headers["content-type"].startswith("audio/mpeg" if duong == "noi" else "application/json")


class DichVuHaiViec:
    """Dịch vụ xa có chat và TTS/STT, mã trả về chỉnh được theo việc (để kiểm sổ nghỉ tách theo khả năng)."""

    def __init__(self):
        self.ma = {"chat": 200, "noi": 200, "nghe": 200}
        self.retry_after = "30"
        self.luot = {"chat": 0, "noi": 0, "nghe": 0}
        self.app = Starlette(routes=[
            Route("/v1/models", self.models),
            Route("/v1/chat/completions", self.viec("chat"), methods=["POST"]),
            Route("/v1/audio/speech", self.viec("noi"), methods=["POST"]),
            Route("/v1/audio/transcriptions", self.viec("nghe"), methods=["POST"]),
        ])

    async def models(self, req):
        return JSONResponse({"data": [{"id": "m"}]})

    def viec(self, ten):
        async def xu_ly(req):
            await req.body()
            self.luot[ten] += 1
            ma = self.ma[ten]
            if ma == 200:
                if ten == "noi":
                    return Response(b"AUDIO", media_type="audio/mpeg")
                return JSONResponse({"text": "ok", "choices": [{"index": 0, "message": {"content": "hi"}}]})
            hd = {"retry-after": self.retry_after} if ma == 429 else {}
            return JSONResponse({"error": {"message": f"lỗi {ma}"}}, status_code=ma, headers=hd)
        return xu_ly


@pytest.fixture
def hai_viec():
    dv = DichVuHaiViec()
    with MayChuThat(dv.app) as s:
        dv.url = s.url
        yield dv


def _dang_ky(c: TestClient, url: str):
    r = c.post("/api/modules/tu-xa", headers=QT, json={
        "id": "dv-lan", "ten": "DV", "tien_to": "lan", "base_url": url + "/v1", "api_key": KHOA_XA})
    assert r.status_code == 200, r.text


def _han_muc(c: TestClient) -> dict:
    r = c.get("/api/modules/dv-lan/suc-khoe", headers=QT)
    assert r.status_code == 200, r.text
    return {m["ten"]: m for m in r.json()["khoa"][0]["han_muc"]}


def test_tts_403_khong_lam_chat_nghi(du_lieu, hai_viec):
    """Một khoá không có quyền giọng nói (TTS 403) -> chat vẫn chạy; suc_khoe báo chat dùng được, noi bị từ chối."""
    hai_viec.ma["noi"] = 403
    with TestClient(app) as c:
        _dang_ky(c, hai_viec.url)
        assert _goi_duong(c, "chat", QT).status_code == 200
        assert _goi_duong(c, "noi", QT).status_code == 403
        r = _goi_duong(c, "chat", QT)
        assert r.status_code == 200, r.text
        hm = _han_muc(c)
        assert hm["chat"]["trang_thai"] == "dung_duoc"
        assert hm["noi"]["trang_thai"] == "bi_tu_choi" and hm["noi"]["ma"] == 403
    assert hai_viec.luot["chat"] == 2


@pytest.mark.parametrize("viec", ["noi", "nghe"])
def test_chat_429_khong_chan_tts_stt(du_lieu, hai_viec, viec):
    """Chat hết hạn mức (429) -> TTS/STT của cùng khoá vẫn gọi được."""
    hai_viec.ma["chat"] = 429
    hai_viec.retry_after = "300"
    with TestClient(app) as c:
        _dang_ky(c, hai_viec.url)
        assert _goi_duong(c, "chat", QT).status_code == 429
        r = _goi_duong(c, viec, QT)
        assert r.status_code == 200, r.text
        hm = _han_muc(c)
        assert hm["chat"]["trang_thai"] == "het_han_muc"
        assert hm[viec]["trang_thai"] == "dung_duoc"
    assert hai_viec.luot[viec] == 1


@pytest.mark.parametrize("viec", ["noi", "nghe"])
def test_429_cuoi_cua_nhi_phan_multipart_kem_retry_after(du_lieu, hai_viec, viec):
    """Khoá đã dùng được việc này rồi gặp 429 -> khách nhận 429 kèm Retry-After như chat."""
    with TestClient(app) as c:
        _dang_ky(c, hai_viec.url)
        assert _goi_duong(c, viec, QT).status_code == 200
        hai_viec.ma[viec] = 429
        r = _goi_duong(c, viec, QT)
        assert r.status_code == 429, r.text
        assert 1 <= int(r.headers["retry-after"]) <= 30
        assert _goi_duong(c, "chat", QT).status_code == 200


def _dich_vu_tho(phan_hoi: bytes) -> tuple[socket.socket, int]:
    """Dịch vụ xa bằng socket thô: đọc đầu yêu cầu, gửi `phan_hoi` nguyên văn rồi đóng (giả luồng đứt giữa chừng)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def chay():
        while True:
            try:
                cn, _ = srv.accept()
            except OSError:
                return
            cn.settimeout(2)
            du_lieu_vao = b""
            try:
                while b"\r\n\r\n" not in du_lieu_vao:
                    khuc = cn.recv(65536)
                    if not khuc:
                        break
                    du_lieu_vao += khuc
                cn.sendall(phan_hoi)
            except OSError:
                pass
            cn.close()
    threading.Thread(target=chay, daemon=True).start()
    return srv, srv.getsockname()[1]


@pytest.mark.parametrize("kieu", ["dut_giua_chung", "gzip_hong"])
def test_luong_nhi_phan_dut_giua_chung_khach_thay_ket_noi_cat(du_lieu, monkeypatch, kieu):
    """Dịch vụ xa khai Content-Length 100000 nhưng gửi 1000 byte rồi đóng (hoặc gzip hỏng) -> khách KHÔNG nhận 200
    sạch mà thấy kết nối bị cắt; nhật ký cuộc gọi ghi `dut`; iterator của module vẫn được aclose() đúng một lần."""
    from app import nhat_ky
    if kieu == "dut_giua_chung":
        phan_hoi = b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nContent-Length: 100000\r\n\r\n" + b"A" * 1000
    else:
        phan_hoi = (b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nContent-Encoding: gzip\r\n"
                    b"Content-Length: 1000\r\n\r\n" + b"B" * 1000)
    srv, cong = _dich_vu_tho(phan_hoi)
    dem = _boc_noi_dem_dong(monkeypatch)
    try:
        with MayChuThat(app, lifespan="on") as w:
            _dang_ky_tho(w.url, f"http://127.0.0.1:{cong}")
            with httpx.Client(base_url=w.url, timeout=10) as h:
                with pytest.raises(httpx.TransportError):
                    with h.stream("POST", "/v1/audio/speech", headers=QT,
                                  json={"model": "lan/giong-noi-1", "input": "x"}) as r:
                        assert r.status_code == 200
                        for _ in r.iter_bytes():
                            pass
            assert _cho(lambda: any(d["them"].get("duong") == "/v1/audio/speech"
                                    for d in nhat_ky.doc(kenh="cuoc_goi")))
    finally:
        srv.close()
    dong = [d for d in nhat_ky.doc(kenh="cuoc_goi") if d["them"].get("duong") == "/v1/audio/speech"]
    assert len(dong) == 1 and dong[0]["them"].get("dut") is True, dong
    assert [d.so_lan_dong for d in dem] == [1]


@pytest.mark.parametrize("tien_to_duong", ["/v1", "/openai", "/v1beta/openai", ""])
def test_duong_goi_bang_base_url_cong_endpoint_nhu_chat(tien_to_duong):
    """base_url do quản trị khai đã gồm tiền tố phiên bản: mọi việc gọi `{base_url}/<endpoint>` như chat — không tự
    chèn /v1 (base_url `/openai` từng làm chat chạy còn audio/ảnh 404)."""
    from app.chuyen_tiep import ChuyenTiepOpenAI
    from app.yeu_cau_da_phan import TepTai, YeuCauDaPhan
    duong_nhan: list[str] = []

    async def bat_ca(req):
        await req.body()
        duong_nhan.append(req.url.path)
        if req.url.path.endswith("/audio/speech"):
            return Response(b"AUDIO", media_type="audio/mpeg")
        return JSONResponse({"text": "ok", "choices": [], "data": []})
    up = Starlette(routes=[Route("/{p:path}", bat_ca, methods=["POST"])])

    async def chay(url):
        ct = ChuyenTiepOpenAI(ten="X", tien_to="x", base_url=url + tien_to_duong, khoa=["k"])
        try:
            assert (await ct.chat({"model": "x/m", "messages": []}, False))[0] == 200
            ma, _, it = await ct.noi({"model": "x/m", "input": "a"})
            assert ma == 200
            assert b"".join([k async for k in it]) == b"AUDIO"
            yc = YeuCauDaPhan(truong={"model": "x/m", "prompt": "p"},
                              tep=[TepTai("file", "a.wav", "audio/wav", b"A"), TepTai("image", "a.png", "image/png", b"P")])
            assert (await ct.nghe(yc))[0] == 200
            assert (await ct.anh_sua(yc))[0] == 200
        finally:
            await ct.dung()

    with MayChuThat(up) as s:
        asyncio.run(chay(s.url))
    assert duong_nhan == [f"{tien_to_duong}/{e}" for e in
                          ("chat/completions", "audio/speech", "audio/transcriptions", "images/edits")]


@pytest.mark.parametrize("viec", ["noi", "nghe", "anh_sua"])
def test_dich_bi_chan_luc_ket_noi_tra_ssrf_blocked_khong_thu_khoa_khac(viec):
    """Ghim IP chặn đích lúc kết nối (DNS rebinding) -> 502 `ssrf_blocked` ngay như chat: không thử khoá kế, không
    đánh dấu khoá nào, không lộ tên lớp nội bộ."""
    from app.chuyen_tiep import ChuyenTiepOpenAI
    from app.yeu_cau_da_phan import TepTai, YeuCauDaPhan
    so_lan_kiem = []

    def kiem(ip):
        so_lan_kiem.append(str(ip))
        return "dải bị chặn (test)"

    async def chay():
        ct = ChuyenTiepOpenAI(ten="X", tien_to="x", base_url="http://127.0.0.1:9/v1", khoa=["k1", "k2"],
                              kiem_ip=kiem)
        yc = YeuCauDaPhan(truong={"model": "x/m", "prompt": "p"},
                          tep=[TepTai("file", "a.wav", "audio/wav", b"A"), TepTai("image", "a.png", "image/png", b"P")])
        try:
            if viec == "noi":
                kq = await ct.noi({"model": "x/m", "input": "a"})
            else:
                kq = await getattr(ct, viec)(yc)
            return kq, dict(ct._nghi), dict(ct._kn)
        finally:
            await ct.dung()

    (ma, _, than), nghi, kn = asyncio.run(chay())
    assert ma == 502
    loi = json.loads(than)["error"]
    assert loi["code"] == "ssrf_blocked"
    assert "LoiDichBiChan" not in than.decode()
    assert len(so_lan_kiem) == 1
    assert nghi == {} and kn == {}


def test_client_khong_ghim_ip_van_theo_proxy_moi_truong(monkeypatch):
    """Module tự cố định đích (kiem_ip=None, vd gemini-api) vẫn đi qua HTTP(S)_PROXY của môi trường — lưới chặn
    Internet của conftest dựa vào điều này. Client ghim IP thì httpx tự bỏ proxy môi trường (có transport riêng)."""
    from app.chuyen_tiep import ChuyenTiepOpenAI
    dong_dau: list[bytes] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def proxy():
        while True:
            try:
                cn, _ = srv.accept()
            except OSError:
                return
            cn.settimeout(2)
            vao = b""
            try:
                while b"\r\n\r\n" not in vao:
                    k = cn.recv(65536)
                    if not k:
                        break
                    vao += k
                dong_dau.append(vao.split(b"\r\n", 1)[0])
                than = b'{"data": [{"id": "m"}]}'
                cn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                           + str(len(than)).encode() + b"\r\n\r\n" + than)
            except OSError:
                pass
            cn.close()
    threading.Thread(target=proxy, daemon=True).start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{srv.getsockname()[1]}")
    monkeypatch.setenv("NO_PROXY", "")

    async def models(**kw):
        ct = ChuyenTiepOpenAI(ten="X", tien_to="x", khoa=["k"], **kw)
        try:
            return await ct.models()
        finally:
            await ct.dung()

    try:
        assert asyncio.run(models(base_url="http://may-gia.invalid/v1")) == [{"id": "x/m"}]
        assert dong_dau == [b"GET http://may-gia.invalid/v1/models HTTP/1.1"]
        # Client ghim IP: nối thẳng tới IP đã kiểm, không qua proxy (proxy tự phân giải lại tên là hỏng ghim IP).
        that = Starlette(routes=[Route("/v1/models", lambda req: JSONResponse({"data": [{"id": "that"}]}))])
        with MayChuThat(that) as s:
            assert asyncio.run(models(base_url=s.url + "/v1", kiem_ip=lambda ip: None)) == [{"id": "x/that"}]
        assert len(dong_dau) == 1
    finally:
        srv.close()
