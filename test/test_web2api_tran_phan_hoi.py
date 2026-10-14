"""Kiểm tra trần cho phản hồi ngược dòng đọc vào RAM và luồng nhả kết nối đúng.

1. Trần kích thước (WEB2API_PHAN_HOI_TOI_DA_MB, mặc định 32 MB):
   - Trả thân 200 MB (với trần test 1 MB): ngắt ngay lập tức, RSS không vọt, trả 502 upstream_too_large
     cho chat không stream, goi_kha_nang, models, kiem_khoa, gemini-api, vnc-xa.
2. Trần thời gian tổng (WEB2API_PHAN_HOI_TOI_DA_GIAY, mặc định 300 giây):
   - Nguồn nhỏ giọt (1 byte mỗi 0.2s, trần 1s): ngắt sau 1s, trả 504 upstream_timeout.
3. Hợp đồng aclose của luồng:
   - Iterator chưa chạy dòng nào mà aclose() -> r.aclose() luôn được gọi (r.is_closed == True)
     cho _chuyen_dong (SSE chat) và luồng video (gemini-api).
4. Biến môi trường xấu (abc, 0, -1, nan, inf, rỗng) về mặc định an toàn.
"""

import asyncio
import json
import os
import resource
import time
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from app.chuyen_tiep import (
    ChuyenTiepOpenAI,
    PhanHoiQuaLon,
    doc_phan_hoi_co_tran,
    phan_hoi_toi_da_bytes,
    phan_hoi_toi_da_giay,
    phan_hoi_toi_da_mb,
)
from app.kham_pha import doc_manifest, nap_ma, van_tay
from gia import MayChuThat

GOC = Path(__file__).resolve().parents[1]


def lay_module(id_mod: str):
    p = GOC / "modules" / id_mod
    m, _ = doc_manifest(p)
    lop, _ = nap_ma(p, m, van_tay(p))
    return lop(m)


def lay_rss_kb() -> int:
    try:
        with open("/proc/self/status") as f:
            for dong in f:
                if dong.startswith("VmRSS:"):
                    return int(dong.split()[1])
    except Exception:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


async def sinh_than_lon():
    """Sinh thân 200 MB bằng các khúc 64 KB mà không tốn 200 MB RAM của máy chủ."""
    khuc = b"X" * (64 * 1024)
    for _ in range((200 * 1024 * 1024) // len(khuc)):
        yield khuc


async def sinh_nho_giot():
    """Nhỏ giọt 1 byte mỗi 0.2 giây."""
    for _ in range(50):
        yield b"a"
        await asyncio.sleep(0.2)


HO_SO = "0123456789abcdef"


def tao_app_dich_vu_lon():
    app = Starlette(routes=[
        Route("/v1/chat/completions", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["POST"]),
        Route("/v1/images/generations", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["POST"]),
        Route("/v1/models", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["GET"]),
        Route("/v1beta/veo-test-op", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["GET"]),
        Route("/v1beta/models", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["GET"]),
        Route("/tai-video", lambda req: StreamingResponse(sinh_than_lon(), status_code=500, media_type="application/json"), methods=["GET"]),
        Route("/ho-so", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["POST", "GET"]),
        Route("/ho-so/mo", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["POST"]),
        Route("/health", lambda req: PlainTextResponse("ok"), methods=["GET"]),
        Route("/tai-khoan", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["GET"]),
        Route(f"/ho-so/{HO_SO}/dong", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["POST"]),
        Route(f"/ho-so/{HO_SO}/cookie", lambda req: StreamingResponse(sinh_than_lon(), media_type="application/json"), methods=["GET"]),
    ])
    return app


def tao_app_dich_vu_nho_giot():
    app = Starlette(routes=[
        Route("/v1/chat/completions", lambda req: StreamingResponse(sinh_nho_giot(), media_type="application/json"), methods=["POST"]),
        Route("/v1/images/generations", lambda req: StreamingResponse(sinh_nho_giot(), media_type="application/json"), methods=["POST"]),
    ])
    return app


# ── 1. Biến môi trường ──

def test_bien_moi_truong_phan_hoi_mac_dinh_va_xau(monkeypatch):
    monkeypatch.delenv("WEB2API_PHAN_HOI_TOI_DA_MB", raising=False)
    assert phan_hoi_toi_da_mb() == 32.0
    assert phan_hoi_toi_da_bytes() == 32 * 1024 * 1024

    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "64")
    assert phan_hoi_toi_da_mb() == 64.0
    assert phan_hoi_toi_da_bytes() == 64 * 1024 * 1024

    for xau in ["abc", "0", "-1", "nan", "inf", "-inf", ""]:
        monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", xau)
        assert phan_hoi_toi_da_mb() == 32.0
        assert phan_hoi_toi_da_bytes() == 32 * 1024 * 1024

    monkeypatch.delenv("WEB2API_PHAN_HOI_TOI_DA_GIAY", raising=False)
    assert phan_hoi_toi_da_giay() == 300.0

    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_GIAY", "60")
    assert phan_hoi_toi_da_giay() == 60.0

    for xau in ["abc", "0", "-1", "nan", "inf", "-inf", ""]:
        monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_GIAY", xau)
        assert phan_hoi_toi_da_giay() == 300.0


# ── 2. Đọc có trần trực tiếp ──

def test_doc_phan_hoi_co_tran_ngat_khi_vuot_tran():
    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            async with httpx.AsyncClient() as client:
                req = client.build_request("GET", f"{sv.url}/v1/models")
                r = await client.send(req, stream=True)
                with pytest.raises(PhanHoiQuaLon) as exc_info:
                    await doc_phan_hoi_co_tran(r, tran_bytes=1024 * 1024, tran_giay=10.0)
                assert "vượt trần" in str(exc_info.value).lower()
                assert r.is_closed

    asyncio.run(chay())


def test_doc_phan_hoi_co_tran_timeout():
    async def chay():
        with MayChuThat(tao_app_dich_vu_nho_giot()) as sv:
            async with httpx.AsyncClient() as client:
                req = client.build_request("POST", f"{sv.url}/v1/chat/completions", json={})
                r = await client.send(req, stream=True)
                with pytest.raises(TimeoutError):
                    await doc_phan_hoi_co_tran(r, tran_bytes=10 * 1024 * 1024, tran_giay=0.5)
                assert r.is_closed

    asyncio.run(chay())


# ── 3. Thử từng điểm đọc với thân 200 MB (trần 1 MB) ──

def test_chat_khong_stream_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")
    rss_truoc = lay_rss_kb()

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
            try:
                ma, hd, than = await ct.chat({"model": "test/m", "messages": []}, stream=False)
                assert ma == 502
                loi = json.loads(than)["error"]
                assert loi["code"] == "upstream_too_large"
            finally:
                await ct.dung()

    asyncio.run(chay())
    rss_sau = lay_rss_kb()
    assert (rss_sau - rss_truoc) < 50 * 1024


def test_goi_kha_nang_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")
    rss_truoc = lay_rss_kb()

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
            try:
                khoa, kq = await ct.goi_kha_nang("anh", "tạo ảnh", "POST", f"{sv.url}/v1/images/generations",
                                                json_than={}, tieu_de=lambda k: {}, stream=False)
                assert khoa is None
                ma, hd, than = kq
                assert ma == 502
                loi = json.loads(than)["error"]
                assert loi["code"] == "upstream_too_large"
            finally:
                await ct.dung()

    asyncio.run(chay())
    rss_sau = lay_rss_kb()
    assert (rss_sau - rss_truoc) < 50 * 1024


def test_kiem_khoa_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")
    rss_truoc = lay_rss_kb()

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
            try:
                muc = await ct.kiem_khoa("k1")
                assert muc["ok"] is False
                assert "upstream_too_large" in str(muc["loi"])
            finally:
                await ct.dung()

    asyncio.run(chay())
    rss_sau = lay_rss_kb()
    assert (rss_sau - rss_truoc) < 50 * 1024


def test_models_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")
    rss_truoc = lay_rss_kb()

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
            try:
                ds = await ct.models()
                assert ds == []  # hạ cấp êm, không sập
            finally:
                await ct.dung()

    asyncio.run(chay())
    rss_sau = lay_rss_kb()
    assert (rss_sau - rss_truoc) < 50 * 1024


def test_gemini_api_hoi_va_tai_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            mod = lay_module("gemini-api")
            mod.nap({"api_keys": ["AIzaSyFakeKeyForTestOnly1234567890abcdef"], "base_url": sv.url})
            try:
                j, loi = await mod._hoi("veo-test-op", "AIzaSyFakeKeyForTestOnly1234567890abcdef")
                assert j is None
                assert loi[0] == 502
                assert json.loads(loi[2])["error"]["code"] == "upstream_too_large"

                kq_tai = await mod._tai(f"{sv.url}/tai-video", "AIzaSyFakeKeyForTestOnly1234567890abcdef")
                assert kq_tai[0] == 502
                assert json.loads(kq_tai[2])["error"]["code"] == "upstream_too_large"
            finally:
                await mod.dung()

    asyncio.run(chay())


def test_vnc_xa_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            mod = lay_module("vnc-xa")
            mod._kiem_ip = lambda ip: None
            mod.nap({"dich_vu_url": sv.url, "khoa_dich_vu": "test-key-0123456789abcdef"})
            try:
                with pytest.raises(Exception) as exc:
                    await mod.trinh_duyet_tao("test")
                assert getattr(exc.value, "ma", None) == 502 or "quá lớn" in str(exc.value).lower()

                with pytest.raises(RuntimeError) as exc:
                    await mod.trinh_duyet_mo(HO_SO, "firefox")
                assert "quá lớn" in str(exc.value).lower()

                with pytest.raises(RuntimeError) as exc:
                    await mod.trinh_duyet_ds()
                assert "quá lớn" in str(exc.value).lower()

                with pytest.raises(Exception) as exc:
                    await mod.trinh_duyet_cookie(HO_SO, "example.com")
                assert getattr(exc.value, "ma", None) == 502 or "quá lớn" in str(exc.value).lower()
            finally:
                await mod.dung()

    asyncio.run(chay())


def test_antigravity_than_lon(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_MB", "1")

    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            mod = lay_module("antigravity")
            # 1. cai() khi /v1/models trả thân > trần
            kq_cai = await mod.cai({"dia_chi_cau_noi": sv.url, "khoa_cau_noi": "fake-bridge-key-0123456789abcdef"})
            assert kq_cai["ok"] is False
            assert "PhanHoiQuaLon" in kq_cai["loi"]

            # 2. suc_khoe() khi /tai-khoan trả thân > trần -> hạ cấp êm, không sập
            mod.nap({"dia_chi_cau_noi": sv.url, "khoa_cau_noi": "fake-bridge-key-0123456789abcdef"})
            try:
                kq_sk = await mod.suc_khoe()
                assert isinstance(kq_sk, dict)
            finally:
                await mod.dung()

    asyncio.run(chay())


# ── 4. Dịch vụ nhỏ giọt (timeout tổng) ──

def test_chat_nho_giot_timeout(monkeypatch):
    monkeypatch.setenv("WEB2API_PHAN_HOI_TOI_DA_GIAY", "1")

    async def chay():
        with MayChuThat(tao_app_dich_vu_nho_giot()) as sv:
            ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
            try:
                bat_dau = time.monotonic()
                ma, hd, than = await ct.chat({"model": "test/m", "messages": []}, stream=False)
                da_chay = time.monotonic() - bat_dau
                assert da_chay < 5.0
                assert ma == 504
                loi = json.loads(than)["error"]
                assert loi["code"] == "upstream_timeout"
            finally:
                await ct.dung()

    asyncio.run(chay())


# ── 5. Hợp đồng aclose khi iterator chưa chạy ──

def test_aclose_chua_chay_dong_nao_dong_ket_noi():
    async def chay():
        with MayChuThat(tao_app_dich_vu_lon()) as sv:
            async with httpx.AsyncClient() as client:
                # 1. Luồng SSE chat
                req = client.build_request("POST", f"{sv.url}/v1/chat/completions", json={})
                r = await client.send(req, stream=True)
                ct = ChuyenTiepOpenAI(ten="Test", tien_to="test", base_url=f"{sv.url}/v1", khoa=["k1"])
                luong_sse = ct._chuyen_dong(r)
                # Chưa gọi __anext__ lần nào
                await luong_sse.aclose()
                assert r.is_closed, "r.is_closed phải True khi aclose() gọi trước byte đầu"

                # 2. Luồng video gemini-api
                mod = lay_module("gemini-api")
                mod.nap({"api_keys": ["AIzaSyFakeKeyForTestOnly1234567890abcdef"], "base_url": sv.url})
                req2 = client.build_request("GET", f"{sv.url}/tai-video")
                r2 = await client.send(req2, stream=True)
                luong_video = mod._luong(r2)
                await luong_video.aclose()
                assert r2.is_closed, "r2.is_closed phải True khi aclose() luồng video"
                await mod.dung()
                await ct.dung()

    asyncio.run(chay())


def test_khach_ngat_giua_luc_doc_van_dong_phan_hoi():
    """Tác vụ đang đọc phản hồi ngược dòng bị HUỶ (khách ngắt → CancelledError, không phải Exception) vẫn phải đóng phản
    hồi — nếu không kết nối ngược dòng treo trong pool."""
    import asyncio

    from app.chuyen_tiep import doc_phan_hoi_co_tran

    class PhanHoiGia:
        def __init__(self):
            self.da_dong = 0

        async def aiter_bytes(self):
            yield b"mot-khuc"
            await asyncio.sleep(30)  # nguồn nhỏ giọt: đang chờ khúc kế thì bị huỷ
            yield b"khong-bao-gio-toi"

        async def aclose(self):
            self.da_dong += 1

    async def chay():
        r = PhanHoiGia()
        tac_vu = asyncio.create_task(doc_phan_hoi_co_tran(r, tran_bytes=1024, tran_giay=60))
        await asyncio.sleep(0.05)
        tac_vu.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tac_vu
        return r.da_dong

    assert asyncio.run(chay()) >= 1, "phản hồi không được đóng khi tác vụ đọc bị huỷ"
