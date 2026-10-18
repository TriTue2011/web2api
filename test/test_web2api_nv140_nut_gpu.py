"""Kiểm tra chức năng Máy GPU từ xa (NV140).

Bao gồm:
- Hợp đồng nút GPU: khám phá khả năng, telemetry GPU, làm sạch dữ liệu không tin.
- Giới hạn đồng thời + hàng đợi (429 + Retry-After, huỷ không rò slot).
- Mạch ngắt: 3 lỗi mạng/5xx liên tiếp -> ngắt 503 nhanh -> nửa mở -> hồi phục; 4xx không ngắt.
- Bật / Tắt module GPU (503 khi tắt).
- Bảo mật: 401 không khoá, 403 khoá khách, 404 IDOR, SSRF, XSS.
- Tương thích ngược: dịch vụ không có w2a-node/health vẫn dùng bình thường.
"""

import asyncio
import json
import stat
import time

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app import so_module
from app.main import app
from app.nut_gpu import (
    HangDoiDongThoi,
    MachNgat,
    do_nut_gpu,
    lam_sach_manifest_nut,
    lam_sach_telemetry_gpu,
)
from gia import MayChuThat

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
KHOA_KH = "sk-lan-0000000000000000000000000000xyzw"
KHOA_NUT = "sk-khoa-tu-xa-RIENG-7777"


class NutGpuGia:
    """Máy chủ nút GPU giả lập chuẩn hợp đồng w2a-node + /health."""

    def __init__(self, khoa=KHOA_NUT):
        self.khoa = khoa
        self.health_data = {
            "ok": True,
            "phien_ban": "0.1.0",
            "gpu": [{
                "ten": "NVIDIA GeForce RTX 3060",
                "vram_tong_mb": 12288.0,
                "vram_dung_mb": 2048.0,
                "nhiet_c": 52.0,
                "tai_pct": 15.0,
            }],
            "mo_hinh_da_nap": ["whisper-large-v3"],
            "hang_doi": {"dang_chay": 0, "cho": 0},
        }
        self.node_data = {
            "id": "may-gpu",
            "ten": "Máy GPU trong nhà",
            "kha_nang": ["chat", "nghe", "noi"],
            "mo_hinh": ["whisper-large-v3", "qwen-chat"],
            "gioi_han_dong_thoi": 2,
        }
        self.so_luot_chat = 0
        self.chat_loi_ma = None
        self.chat_tri_hoan = 0.0
        self.nhat_ky = []

        routes = [
            Route("/health", self._health, methods=["GET"]),
            Route("/.well-known/w2a-node", self._node, methods=["GET"]),
            Route("/v1/models", self._models, methods=["GET"]),
            Route("/v1/chat/completions", self._chat, methods=["POST"]),
            Route("/v1/audio/speech", self._speech, methods=["POST"]),
        ]
        self.app = Starlette(routes=routes)

    def _kiem_khoa(self, r: Request) -> bool:
        auth = r.headers.get("authorization", "")
        return auth == f"Bearer {self.khoa}"

    async def _health(self, r: Request):
        return JSONResponse(self.health_data)

    async def _node(self, r: Request):
        if self.khoa and not self._kiem_khoa(r):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(self.node_data)

    async def _models(self, r: Request):
        if self.khoa and not self._kiem_khoa(r):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        data = [{"id": m, "object": "model"} for m in self.node_data.get("mo_hinh", [])]
        return JSONResponse({"object": "list", "data": data})

    async def _chat(self, r: Request):
        if self.khoa and not self._kiem_khoa(r):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        self.so_luot_chat += 1
        self.nhat_ky.append("chat")
        if self.chat_tri_hoan > 0:
            await asyncio.sleep(self.chat_tri_hoan)
        if self.chat_loi_ma:
            return JSONResponse({"error": {"message": f"Lỗi {self.chat_loi_ma}"}}, status_code=self.chat_loi_ma)
        body = await r.json()
        return JSONResponse({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": body.get("model", "qwen-chat"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Chào từ máy GPU!"},
                "finish_reason": "stop"
            }]
        })

    async def _speech(self, r: Request):
        if self.khoa and not self._kiem_khoa(r):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return Response(b"AUDIO_DATA_GIA", media_type="audio/wav")


@pytest.fixture(scope="module")
def _nut_gpu():
    g = NutGpuGia(khoa=KHOA_NUT)
    with MayChuThat(g.app) as s:
        g.url = s.url
        yield g


@pytest.fixture
def nut_gpu(_nut_gpu):
    _nut_gpu.chat_loi_ma = None
    _nut_gpu.chat_tri_hoan = 0.0
    _nut_gpu.so_luot_chat = 0
    _nut_gpu.nhat_ky.clear()
    return _nut_gpu


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


# ── 1. Kiểm tra làm sạch dữ liệu không tin (Dò nút) ──

def test_lam_sach_telemetry_gpu():
    """Dữ liệu rác/lớn/kiểu sai được làm sạch đúng hợp đồng."""
    rac = {
        "ok": True,
        "phien_ban": "0.1.0-beta",
        "gpu": [
            {
                "ten": "Card \x00\x1fSiêu\x7f GPU " + "A" * 120,  # chứa ký tự điều khiển và quá dài
                "vram_tong_mb": -100,                           # số âm
                "vram_dung_mb": "2048",                         # chuỗi số
                "nhiet_c": 300,                                 # quá trần 150
                "tai_pct": -5,                                  # âm
            },
            {
                "ten": "Card 2",
                "vram_tong_mb": 999999999999,                   # quá trần 10M
                "vram_dung_mb": 1000,
                "nhiet_do_c": 60,                               # nhận cả nhiet_do_c
                "tai_pct": 120,                                 # quá 100%
            }
        ] + [{"ten": f"Card {i}"} for i in range(15)],          # quá 8 card
        "mo_hinh_da_nap": ["m1" * 50] + [f"model_{i}" for i in range(100)],
        "hang_doi": {"dang_chay": -1, "cho": "3"},
    }
    sach = lam_sach_telemetry_gpu(rac)
    assert len(sach["gpu"]) == 8  # tối đa 8 GPU
    g0 = sach["gpu"][0]
    assert len(g0["ten"]) <= 80
    assert "\x00" not in g0["ten"] and "\x1f" not in g0["ten"]
    assert g0["vram_tong_mb"] == 0.0  # âm bị kẹp về 0
    assert g0["vram_dung_mb"] == 2048.0
    assert g0["nhiet_c"] == 150.0     # 300 kẹp về 150
    assert g0["tai_pct"] == 0.0       # âm kẹp về 0

    g1 = sach["gpu"][1]
    assert g1["vram_tong_mb"] <= 10_000_000.0
    assert g1["nhiet_c"] == 60.0      # đọc từ nhiet_do_c
    assert g1["tai_pct"] == 100.0     # 120% kẹp về 100

    assert len(sach["mo_hinh_da_nap"]) <= 64
    assert sach["hang_doi"]["dang_chay"] == 0
    assert sach["hang_doi"]["cho"] == 3


def test_lam_sach_manifest_nut():
    """Manifest nút GPU được kiểm kiểu và giới hạn hợp lý."""
    rac = {
        "id": "may_gpu_01",
        "ten": "Tên máy \x08\x09GPU " + "B" * 150,
        "kha_nang": ["chat", "nghe", "noi", "kha_nang_la_99", "MÃ_HOA_KHÔNG_HỢP_LỆ"] + [f"kn_{i}" for i in range(20)],
        "mo_hinh": [f"m_{i}" for i in range(100)],
        "gioi_han_dong_thoi": 99,  # vượt trần 8
    }
    sach = lam_sach_manifest_nut(rac)
    assert sach["id"] == "may_gpu_01"
    assert len(sach["ten"]) <= 80
    assert "\x08" not in sach["ten"]
    assert sach["gioi_han_dong_thoi"] == 8  # trần cứng 8
    assert len(sach["kha_nang"]) <= 16
    assert "kha_nang_la_99" in sach["kha_nang"]  # mã mở giữ nguyên
    assert len(sach["mo_hinh"]) <= 64


def test_do_nut_gpu_thanh_cong(monkeypatch, nut_gpu):
    """Hàm do_nut_gpu kết nối và lấy đầy đủ thông tin nút giả lập."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    kq = asyncio.run(do_nut_gpu(nut_gpu.url, api_key=KHOA_NUT))
    assert kq["ok"] is True
    assert kq["co_w2a"] is True
    assert len(kq["gpu"]) == 1
    assert kq["gpu"][0]["ten"] == "NVIDIA GeForce RTX 3060"
    assert "chat" in kq["kha_nang"]
    assert kq["gioi_han_dong_thoi"] == 2
    assert kq["do_tre_ms"] >= 0



# ── 2. Đăng ký & Khai khả năng ──

def test_dang_ky_may_gpu_va_khai_kha_nang(du_lieu, nut_gpu):
    """Đăng ký máy GPU với khả năng đã chọn, kiểm tra /api/tinh-nang."""
    with TestClient(app) as c:
        # Đăng ký với kha_nang_bat = ["chat", "noi"] (không bật "nghe")
        r = c.post("/api/modules/tu-xa", json={
            "id": "gpu-nha",
            "ten": "Máy GPU phòng khách",
            "tien_to": "gpu",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "gioi_han_dong_thoi": 2,
            "kha_nang_bat": ["chat", "noi"],
        }, headers=QT)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j["module"]["la_nut_gpu"] is True
        assert j["module"]["gioi_han_dong_thoi"] == 2

        # Kiểm tra /api/tinh-nang
        r_tn = c.get("/api/tinh-nang", headers=QT)
        assert r_tn.status_code == 200, r_tn.text
        tn = r_tn.json()
        mod = next((m for m in tn["modules"] if m["id"] == "gpu-nha"), None)
        assert mod is not None
        assert "chat" in mod["cung_cap"]
        assert "noi" in mod["cung_cap"]
        assert "nghe" not in mod["cung_cap"]  # không bật thì không hiện

        # Gọi chat thành công
        r_chat = c.post("/v1/chat/completions", json={
            "model": "gpu/qwen-chat",
            "messages": [{"role": "user", "content": "Xin chào"}]
        }, headers=QT)
        assert r_chat.status_code == 200, r_chat.text
        assert "Chào từ máy GPU!" in r_chat.json()["choices"][0]["message"]["content"]


# ── 3. Giới hạn đồng thời & Hàng đợi ──

def test_gioi_han_dong_thoi_va_hang_doi(du_lieu, nut_gpu):
    """Nhiều request đồng thời: request vượt quá giới hạn phải xếp hàng hoặc trả 429 Retry-After."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-dong-thoi",
            "ten": "Máy GPU test đồng thời",
            "tien_to": "gpudt",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "gioi_han_dong_thoi": 1,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        # Lấy thể hiện module từ sổ
        so = app.state.so
        m = so.dang_chay["gpu-dong-thoi"]
        assert m.gioi_han_dong_thoi == 1

        # Test trực tiếp lớp HangDoiDongThoi
        hd = HangDoiDongThoi(gioi_han=1)
        async def chay_tac_vu():
            async with hd.vao():
                assert hd.dang_chay == 1
                await asyncio.sleep(0.05)

        async def chay_song_song():
            t1 = asyncio.create_task(chay_tac_vu())
            await asyncio.sleep(0.01)
            assert hd.dang_chay == 1
            t2 = asyncio.create_task(chay_tac_vu())
            await asyncio.sleep(0.01)
            # t2 đang phải chờ trong hàng đợi
            assert hd.dang_cho == 1
            await asyncio.gather(t1, t2)
            assert hd.dang_chay == 0
            assert hd.dang_cho == 0

        asyncio.run(chay_song_song())


def test_huy_yeu_cau_khong_ro_slot():
    """Client ngắt kết nối (CancelledError) không làm rò rỉ slot đồng thời."""
    hd = HangDoiDongThoi(gioi_han=1)

    async def tac_vu_bi_huy():
        async with hd.vao():
            assert hd.dang_chay == 1
            await asyncio.sleep(10.0)

    async def chay():
        task = asyncio.create_task(tac_vu_bi_huy())
        await asyncio.sleep(0.02)
        assert hd.dang_chay == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Sau khi huỷ, slot phải được giải phóng
        assert hd.dang_chay == 0
        assert hd.dang_cho == 0

    asyncio.run(chay())


# ── 4. Mạch ngắt (Circuit Breaker) ──

def test_mach_ngat_3_loi_lien_tiep_ngat_503():
    """3 lỗi liên tiếp -> ngắt -> trả 503 circuit_broken ngay không chờ."""
    cb = MachNgat(nguong_loi=3, thoi_gian_ngat_ban_dau=1.0)
    assert cb.trang_thai == "dong"

    cb.ghi_that_bai()
    cb.ghi_that_bai()
    assert cb.trang_thai == "dong"

    # Lần thứ 3 thất bại -> chuyển sang ngat
    cb.ghi_that_bai()
    assert cb.trang_thai == "ngat"

    co_the, ly_do = cb.co_the_goi()
    assert co_the is False
    assert ly_do == "ngat"


def test_mach_ngat_hoi_phuc_nua_mo():
    """Sau thời gian ngắt -> nửa mở -> 1 lượt thử thành công -> đóng mạch."""
    cb = MachNgat(nguong_loi=3, thoi_gian_ngat_ban_dau=0.05)
    cb.ghi_that_bai()
    cb.ghi_that_bai()
    cb.ghi_that_bai()
    assert cb.trang_thai == "ngat"

    # Chờ hết thời gian ngắt
    time.sleep(0.06)
    co_the, ly_do = cb.co_the_goi()
    assert co_the is True
    assert ly_do == "nua_mo"

    # Lượt thử thành công -> đóng mạch
    cb.ghi_thanh_cong()
    assert cb.trang_thai == "dong"
    assert cb.so_loi_lien_tiep == 0


def test_mach_ngat_4xx_khong_lam_ngat(du_lieu, nut_gpu):
    """Lỗi 4xx của khách không làm ngắt mạch."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-4xx",
            "ten": "Máy GPU 4xx",
            "tien_to": "gpu4xx",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        so = app.state.so
        m = so.dang_chay["gpu-4xx"]

        # Đặt nút trả 400 (lỗi của khách / yêu cầu)
        nut_gpu.chat_loi_ma = 400
        for _ in range(5):
            r = c.post("/v1/chat/completions", json={
                "model": "gpu4xx/qwen-chat",
                "messages": [{"role": "user", "content": "test"}]
            }, headers=QT)
            assert r.status_code == 400

        # Mạch vẫn đóng vì 4xx là lỗi của khách
        assert m.mach_ngat.trang_thai == "dong"
        assert m.mach_ngat.so_loi_lien_tiep == 0



# ── 5. Telemetry & Sức khoẻ ──

def test_telemetry_suc_khoe(du_lieu, nut_gpu):
    """GET /api/modules/{id}/suc-khoe trả thông tin gpu, hang_doi, mach_ngat."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-sk",
            "ten": "Máy GPU telemetry",
            "tien_to": "gputel",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        r = c.get("/api/modules/gpu-sk/suc-khoe", headers=QT)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert "gpu" in j
        assert len(j["gpu"]) >= 1
        assert j["gpu"][0]["ten"] == "NVIDIA GeForce RTX 3060"
        assert "hang_doi" in j
        assert j["mach_ngat"] == "dong"
        assert j["bat"] is True


# ── 6. Bật / Tắt module ──

def test_bat_tat_module_gpu(du_lieu, nut_gpu):
    """POST /api/modules/{id}/bat-tat cho phép tắt và bật lại module."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-bt",
            "ten": "Máy GPU bật tắt",
            "tien_to": "gpubt",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        # Tắt module
        r_tat = c.post("/api/modules/gpu-bt/bat-tat", json={"bat": False}, headers=QT)
        assert r_tat.status_code == 200
        assert r_tat.json()["bat"] is False

        # Gọi chat khi module đang tắt -> 503 module_disabled
        r_chat = c.post("/v1/chat/completions", json={
            "model": "gpubt/qwen-chat",
            "messages": [{"role": "user", "content": "test"}]
        }, headers=QT)
        assert r_chat.status_code == 503
        assert r_chat.json()["error"]["code"] == "module_disabled"

        # Bật lại
        r_bat = c.post("/api/modules/gpu-bt/bat-tat", json={"bat": True}, headers=QT)
        assert r_bat.status_code == 200
        assert r_bat.json()["bat"] is True

        # Gọi lại -> 200
        r_chat2 = c.post("/v1/chat/completions", json={
            "model": "gpubt/qwen-chat",
            "messages": [{"role": "user", "content": "test"}]
        }, headers=QT)
        assert r_chat2.status_code == 200


# ── 7. Bảo mật (401, 403, 404 IDOR, SSRF, XSS) ──

def test_bao_mat_leo_thang_quyen_va_idor(du_lieu, nut_gpu):
    """Kiểm tra không khoá -> 401; khoá khách -> 403; sai ID (IDOR) -> 404."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-sec",
            "ten": "Máy GPU bảo mật",
            "tien_to": "gpusec",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        # Tạo khoá khách hợp lệ có phạm vi hẹp ("chat")
        r_kk = c.post("/api/khoa-khach", json={"nhan": "khach-test", "pham_vi": ["chat"]}, headers=QT)
        assert r_kk.status_code == 200, r_kk.text
        khoa_khach = r_kk.json()["khoa"]["khoa"]
        KH = {"Authorization": f"Bearer {khoa_khach}"}

        # 1. Tuyến POST /api/modules/do-nut-gpu
        # Không khoá -> 401
        assert c.post("/api/modules/do-nut-gpu", json={"base_url": nut_gpu.url}).status_code == 401
        # Khoá khách -> 403
        assert c.post("/api/modules/do-nut-gpu", json={"base_url": nut_gpu.url}, headers=KH).status_code == 403

        # 2. Tuyến POST /api/modules/tu-xa
        # Không khoá -> 401
        assert c.post("/api/modules/tu-xa", json={"id": "gpu-hack", "ten": "Hack", "tien_to": "h", "base_url": nut_gpu.url}).status_code == 401
        # Khoá khách -> 403
        assert c.post("/api/modules/tu-xa", json={"id": "gpu-hack", "ten": "Hack", "tien_to": "h", "base_url": nut_gpu.url}, headers=KH).status_code == 403

        # 3. Tuyến POST /api/modules/{id}/bat-tat
        # Không khoá -> 401
        assert c.post("/api/modules/gpu-sec/bat-tat", json={"bat": False}).status_code == 401
        # Khoá khách -> 403
        assert c.post("/api/modules/gpu-sec/bat-tat", json={"bat": False}, headers=KH).status_code == 403
        # IDOR (đổi id không tồn tại) -> 404
        assert c.post("/api/modules/gpu-khong-ton-tai/bat-tat", json={"bat": False}, headers=QT).status_code == 404


def test_bao_mat_ssrf_va_chuyen_huong(du_lieu):
    """POST /api/modules/do-nut-gpu chặn loopback/metadata và không theo redirect."""
    with TestClient(app) as c:
        # Loopback khi không cho phép test (monkeypatch tắt biến cờ)
        # SSRF metadata đám mây
        r_meta = c.post("/api/modules/do-nut-gpu", json={"base_url": "http://169.254.169.254/v1"}, headers=QT)
        assert r_meta.status_code == 400
        assert "link-local" in r_meta.text or "bị chặn" in r_meta.text


def test_khoa_nut_khong_lo_ra_phan_hoi_hay_log(du_lieu, nut_gpu):
    """Khoá nút lưu ở bi_mat/ 0600, không lộ ra phản hồi hay công khai."""
    with TestClient(app) as c:
        r = c.post("/api/modules/tu-xa", json={
            "id": "gpu-secret",
            "ten": "Máy GPU bí mật",
            "tien_to": "gpusec2",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)
        assert r.status_code == 200
        # Khoá thô KHÔNG được nằm trong phản hồi
        assert KHOA_NUT not in r.text

        # GET /api/modules
        r_mods = c.get("/api/modules", headers=QT)
        assert KHOA_NUT not in r_mods.text

        # GET /api/modules/gpu-secret/suc-khoe
        r_sk = c.get("/api/modules/gpu-secret/suc-khoe", headers=QT)
        assert KHOA_NUT not in r_sk.text

        # Kiểm tra tệp bi_mat/gpu-secret.json có quyền 0600
        p_bm = du_lieu / "bi_mat" / "gpu-secret.json"
        assert p_bm.exists()
        quyen = stat.S_IMODE(p_bm.stat().st_mode)
        assert quyen == 0o600


# ── 8. Test tái hiện các lỗi SOÁT ĐỘC LẬP (Lượt 2) ──

def test_loi1_huy_giua_loi_goi_khong_stream_khong_ro_slot():
    """Lỗi 1: Khách huỷ giữa lúc chờ lời gọi không stream không được rò slot dang_chay."""
    from app.tu_xa import ModuleTuXa

    ban_ghi = {
        "id": "gpu-huy-slot",
        "ten": "Máy GPU huỷ slot",
        "tien_to": "gpuhuyslot",
        "base_url": "http://192.0.2.1/v1",
        "la_nut_gpu": True,
        "gioi_han_dong_thoi": 1,
    }
    m = ModuleTuXa(ban_ghi, api_key=None)

    async def chay():
        async def fn_lau(*args, **kwargs):
            await asyncio.sleep(5.0)
            return (200, {"content-type": "application/json"}, b'{"ok":true}')

        task = asyncio.create_task(m._dieu_phoi(fn_lau))
        await asyncio.sleep(0.02)
        assert m.hang_doi.dang_chay == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Kiểm tra bất biến: sau khi huỷ, slot phải về 0
        assert m.hang_doi.dang_chay == 0, f"RÒ SLOT: dang_chay vẫn là {m.hang_doi.dang_chay} sau khi task bị huỷ!"
        assert m.hang_doi.dang_cho == 0

    asyncio.run(chay())


def test_loi1_huy_khi_dang_o_nua_mo_khong_ket_503_vinh_vien():
    """Lỗi 1: Huỷ request khi mạch đang ở nửa mở không được kẹt dang_thu_nua_mo dẫn tới 503 vĩnh viễn."""
    from app.tu_xa import ModuleTuXa

    ban_ghi = {
        "id": "gpu-nua-mo",
        "ten": "Máy GPU nửa mở",
        "tien_to": "gpunuamo",
        "base_url": "http://192.0.2.1/v1",
        "la_nut_gpu": True,
        "gioi_han_dong_thoi": 1,
    }
    m = ModuleTuXa(ban_ghi, api_key=None)

    # Đưa mạch ngắt vào trạng thái ngắt, sau đó cho hết hạn ngắt để sang nua_mo
    m.mach_ngat.ghi_that_bai()
    m.mach_ngat.ghi_that_bai()
    m.mach_ngat.ghi_that_bai()
    assert m.mach_ngat.trang_thai == "ngat"
    m.mach_ngat.ngat_den = 0.0
    assert m.mach_ngat.trang_thai == "nua_mo"

    async def chay():
        async def fn_lau(*args, **kwargs):
            await asyncio.sleep(5.0)
            return (200, {"content-type": "application/json"}, b'{"ok":true}')

        # Cuộc gọi thử nghiệm nửa mở bị huỷ giữa chừng
        task = asyncio.create_task(m._dieu_phoi(fn_lau))
        await asyncio.sleep(0.02)
        assert m.mach_ngat.dang_thu_nua_mo is True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # dang_thu_nua_mo phải được reset để cho phép thử lại
        assert m.mach_ngat.dang_thu_nua_mo is False, "dang_thu_nua_mo bị kẹt True sau khi task bị huỷ!"
        assert m.hang_doi.dang_chay == 0, "dang_chay bị kẹt sau khi task bị huỷ!"

        # Lượt gọi sau phải được phép thử (không bị 503 circuit_broken ngay lập tức)
        async def fn_nhanh(*args, **kwargs):
            return (200, {"content-type": "application/json"}, b'{"ok":true}')

        res = await m._dieu_phoi(fn_nhanh)
        assert res[0] == 200, f"Bị 503 vĩnh viễn! Kết quả: {res}"
        assert m.mach_ngat.trang_thai == "dong"

    asyncio.run(chay())


def test_loi2_hang_doi_huy_khi_vua_duoc_danh_thuc():
    """Lỗi 2: Task trong hàng đợi vừa được đánh thức (đã pop, đã tăng dang_chay hộ) mà bị huỷ thì không được rò dang_chay."""
    from app.tu_xa import ModuleTuXa

    ban_ghi = {
        "id": "gpu-wake-cancel",
        "ten": "Máy GPU vừa thức bị huỷ",
        "tien_to": "gpuwake",
        "base_url": "http://192.0.2.1/v1",
        "la_nut_gpu": True,
        "gioi_han_dong_thoi": 1,
    }
    m = ModuleTuXa(ban_ghi, api_key=None)

    async def chay():
        fn1_bat_dau = asyncio.Event()
        fn1_tiep_tuc = asyncio.Event()

        async def fn1(*args, **kwargs):
            fn1_bat_dau.set()
            await fn1_tiep_tuc.wait()
            return (200, {"content-type": "application/json"}, b'{"ok":1}')

        task1 = asyncio.create_task(m._dieu_phoi(fn1))
        await fn1_bat_dau.wait()
        assert m.hang_doi.dang_chay == 1
        assert m.hang_doi.dang_cho == 0

        # Task 2 vào hàng đợi
        fn2_bat_dau = asyncio.Event()
        async def fn2(*args, **kwargs):
            fn2_bat_dau.set()
            return (200, {"content-type": "application/json"}, b'{"ok":2}')

        task2 = asyncio.create_task(m._dieu_phoi(fn2))
        await asyncio.sleep(0.02)
        assert m.hang_doi.dang_cho == 1

        # Huỷ task 2 ngay khi task 1 kết thúc (ev đã pop, dang_chay đã được cộng hộ cho task 2)
        # Chúng ta huỷ task2 TRƯỚC KHI task2 bắt đầu chạy fn2
        task2.cancel()
        # Cho phép task 1 hoàn thành để đánh thức task 2
        fn1_tiep_tuc.set()
        await task1

        with pytest.raises(asyncio.CancelledError):
            await task2

        # Bất biến: dang_chay và dang_cho PHẢI về 0
        assert m.hang_doi.dang_chay == 0, f"RÒ SLOT HÀNG ĐỢI: dang_chay là {m.hang_doi.dang_chay} sau khi task 2 bị huỷ!"
        assert m.hang_doi.dang_cho == 0, f"LỖI ĐẾM CHỜ: dang_cho là {m.hang_doi.dang_cho}!"

    asyncio.run(chay())


def test_loi2_hang_doi_het_gio_cho_va_huy_khi_dang_cho():
    """Lỗi 2: Hết giờ chờ trả 429 và huỷ khi đang chờ không làm lệch dang_cho, dang_chay."""
    from app.tu_xa import ModuleTuXa

    ban_ghi = {
        "id": "gpu-timeout",
        "ten": "Máy GPU timeout",
        "tien_to": "gputo",
        "base_url": "http://192.0.2.1/v1",
        "la_nut_gpu": True,
        "gioi_han_dong_thoi": 1,
    }
    m = ModuleTuXa(ban_ghi, api_key=None)
    m.hang_doi.thoi_gian_cho = 0.05  # timeout cực nhanh cho test

    async def chay():
        fn1_tiep_tuc = asyncio.Event()
        async def fn1(*args, **kwargs):
            await fn1_tiep_tuc.wait()
            return (200, {"content-type": "application/json"}, b'{"ok":1}')

        task1 = asyncio.create_task(m._dieu_phoi(fn1))
        await asyncio.sleep(0.01)

        # Task 2 chờ quá thời gian -> nhận 429
        async def fn2(*args, **kwargs):
            return (200, {"content-type": "application/json"}, b'{"ok":2}')

        res2 = await m._dieu_phoi(fn2)
        assert res2[0] == 429
        assert m.hang_doi.dang_cho == 0

        # Task 3 vào hàng đợi rồi bị huỷ ngay khi còn đang chờ
        task3 = asyncio.create_task(m._dieu_phoi(fn2))
        await asyncio.sleep(0.01)
        assert m.hang_doi.dang_cho == 1
        task3.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task3
        assert m.hang_doi.dang_cho == 0

        # Kết thúc task 1
        fn1_tiep_tuc.set()
        await task1
        assert m.hang_doi.dang_chay == 0
        assert m.hang_doi.dang_cho == 0

    asyncio.run(chay())


def test_loi2_lap_ngau_nhien_dong_thoi_huy_bat_ky_bat_bien():
    """Lỗi 2: Bất biến dang_chay == 0 và dang_cho == 0 sau khi nhiều task đồng thời bị huỷ ngẫu nhiên."""
    import random
    from app.tu_xa import ModuleTuXa

    ban_ghi = {
        "id": "gpu-stress",
        "ten": "Máy GPU stress",
        "tien_to": "gpustress",
        "base_url": "http://192.0.2.1/v1",
        "la_nut_gpu": True,
        "gioi_han_dong_thoi": 2,
    }
    m = ModuleTuXa(ban_ghi, api_key=None)
    m.hang_doi.thoi_gian_cho = 0.5

    async def chay():
        async def fn_ngau_nhien(*args, **kwargs):
            await asyncio.sleep(random.uniform(0.01, 0.05))
            return (200, {"content-type": "application/json"}, b'{"ok":true}')

        tasks = []
        for _ in range(20):
            t = asyncio.create_task(m._dieu_phoi(fn_ngau_nhien))
            tasks.append(t)

        # Huỷ ngẫu nhiên một nửa số task sau thời gian ngắn
        await asyncio.sleep(0.02)
        for t in tasks[:10]:
            t.cancel()

        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        # Bất biến sau cùng
        assert m.hang_doi.dang_chay == 0, f"Bất biến vi phạm: dang_chay={m.hang_doi.dang_chay}"
        assert m.hang_doi.dang_cho == 0, f"Bất biến vi phạm: dang_cho={m.hang_doi.dang_cho}"

    asyncio.run(chay())


def test_loi2_hang_doi_vao_da_pop_bi_cancel_khong_ro_dang_chay():
    """Lỗi 2: HangDoiDongThoi.vao() khi ev đã pop mà bị cancel không được rò dang_chay."""
    hd = HangDoiDongThoi(gioi_han=1)

    async def chay():
        t1_xong = asyncio.Event()
        async def t1():
            async with hd.vao():
                await t1_xong.wait()

        task1 = asyncio.create_task(t1())
        await asyncio.sleep(0.01)
        assert hd.dang_chay == 1

        async def t2():
            async with hd.vao():
                pass

        task2 = asyncio.create_task(t2())
        await asyncio.sleep(0.01)
        assert hd.dang_cho == 1

        # Mô phỏng: _danh_thuc_nguoi_ke đã pop ev của task 2, tăng dang_chay hộ
        async with hd._khoa:
            hd.dang_chay -= 1
            hd._danh_thuc_nguoi_ke()
            assert hd.dang_chay == 1
            assert hd.dang_cho == 0

        # Huỷ task 2 ngay sau khi đã được cấp slot hộ
        task2.cancel()
        t1_xong.set()
        await task1
        with pytest.raises(asyncio.CancelledError):
            await task2

        # Bất biến
        assert hd.dang_chay == 0, f"RÒ SLOT TRONG VAO: dang_chay={hd.dang_chay} sau khi task 2 bị huỷ!"
        assert hd.dang_cho == 0

    asyncio.run(chay())


def test_loi3_do_nut_gpu_va_telemetry_than_khong_lo_khong_doc_qua_tran(du_lieu, monkeypatch):
    """Lỗi 3: Thân /health khổng lồ từ nút không tin phải bị ngắt có trần (<= 64KB), không đọc hết vào RAM."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    so_byte_da_gui = 0

    async def stream_khong_lo(request: Request):
        nonlocal so_byte_da_gui
        async def generator():
            nonlocal so_byte_da_gui
            chunk = b"A" * 16384  # 16 KB mỗi chunk
            for _ in range(500):  # tổng ~8 MB
                try:
                    yield chunk
                    so_byte_da_gui += len(chunk)
                    await asyncio.sleep(0.001)
                except (asyncio.CancelledError, GeneratorExit):
                    break
        return StreamingResponse(generator(), media_type="application/json")

    app_doc = Starlette(routes=[
        Route("/health", stream_khong_lo, methods=["GET"]),
        Route("/.well-known/w2a-node", stream_khong_lo, methods=["GET"]),
    ])

    with MayChuThat(app_doc) as s:
        so_byte_da_gui = 0
        kq = asyncio.run(do_nut_gpu(s.url))
        assert kq["gpu"] == []
        # Client phải ngắt stream khi vượt trần 64 KB, không tải trọn 8 MB!
        assert so_byte_da_gui < 500 * 1024, f"ĐÃ TẢI QUÁ NHIỀU: {so_byte_da_gui} bytes (vượt trần 64KB)!"


def test_loi4_module_tu_xa_thuong_khong_goi_health_va_khong_co_truong_gpu(du_lieu, monkeypatch):
    """Lỗi 4: Module từ xa thường (la_nut_gpu=False) KHÔNG được gọi /health và suc_khoe() không chứa trường gpu/mach_ngat."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")
    so_luot_health = 0

    async def mock_models(r: Request):
        return JSONResponse({"object": "list", "data": [{"id": "model-1", "object": "model"}]})

    async def mock_health(r: Request):
        nonlocal so_luot_health
        so_luot_health += 1
        return JSONResponse({"ok": True})

    app_mock = Starlette(routes=[
        Route("/v1/models", mock_models, methods=["GET"]),
        Route("/health", mock_health, methods=["GET"]),
    ])

    with MayChuThat(app_mock) as s:
        with TestClient(app) as c:
            # Đăng ký module từ xa THƯỜNG (la_nut_gpu=False hoặc không khai)
            r = c.post("/api/modules/tu-xa", json={
                "id": "tu-xa-thuong",
                "ten": "Từ xa thông thường",
                "tien_to": "txthuong",
                "base_url": s.url + "/v1",
                "api_key": KHOA_NUT,
            }, headers=QT)

            assert r.status_code == 200

            so_luot_health = 0
            # Gọi GET /api/modules/tu-xa-thuong/suc-khoe
            r_sk = c.get("/api/modules/tu-xa-thuong/suc-khoe", headers=QT)
            assert r_sk.status_code == 200
            j = r_sk.json()

            # Kiểm tra: KHÔNG được gọi GET /health tới server
            assert so_luot_health == 0, f"ĐÃ GỌI /health {so_luot_health} LẦN tới module từ xa thường!"
            # Kiểm tra: Không có các trường riêng của GPU làm phá vỡ hợp đồng cũ
            assert "gpu" not in j, "Trường 'gpu' xuất hiện trong suc_khoe của module từ xa thường!"
            assert "mach_ngat" not in j, "Trường 'mach_ngat' xuất hiện trong suc_khoe của module từ xa thường!"


def test_loi5_bat_false_an_models(du_lieu, nut_gpu):
    """Lỗi 5: Khi bat=False, model của module GPU phải bị ẩn khỏi GET /v1/models."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-an-model",
            "ten": "Máy GPU ẩn model",
            "tien_to": "gpuan",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        # Ban đầu bat=True -> model xuất hiện
        r_models1 = c.get("/v1/models", headers=QT)
        assert r_models1.status_code == 200
        ds_ids = [m["id"] for m in r_models1.json()["data"]]
        assert any(mid.startswith("gpuan/") for mid in ds_ids)

        # Tắt module bat=False
        r_tat = c.post("/api/modules/gpu-an-model/bat-tat", json={"bat": False}, headers=QT)
        assert r_tat.status_code == 200

        # Model PHẢI bị ẩn khỏi /v1/models
        r_models2 = c.get("/v1/models", headers=QT)
        assert r_models2.status_code == 200
        ds_ids2 = [m["id"] for m in r_models2.json()["data"]]
        assert not any(mid.startswith("gpuan/") for mid in ds_ids2), f"Model vẫn hiện trong /v1/models khi bat=False: {ds_ids2}"


def test_loi5_do_nut_gpu_tra_ok_false_khi_khong_do_duoc_gi(monkeypatch):
    """Lỗi 5: do_nut_gpu phải trả ok=False khi cả /health và manifest đều không dùng được."""
    monkeypatch.setenv("WEB2API_TEST_CHO_PHEP_LOOPBACK", "1")

    # Máy chủ giả trả 404 cho cả hai endpoint
    app_rong = Starlette(routes=[])
    with MayChuThat(app_rong) as s:
        kq = asyncio.run(do_nut_gpu(s.url))
        assert kq["ok"] is False, f"do_nut_gpu trả ok={kq['ok']} khi không có cả /health và w2a-node!"


def test_loi5_5xx_kem_than_loi_4xx_kieu_khong_kich_mach_ngat(du_lieu, nut_gpu):
    """Lỗi 5: Lỗi 5xx từ nút nhưng có thân cấu trúc invalid_request_error không kích mạch ngắt."""
    with TestClient(app) as c:
        c.post("/api/modules/tu-xa", json={
            "id": "gpu-5xx-client",
            "ten": "Máy GPU 5xx client",
            "tien_to": "gpu5xxc",
            "base_url": nut_gpu.url + "/v1",
            "api_key": KHOA_NUT,
            "la_nut_gpu": True,
            "kha_nang_bat": ["chat"],
        }, headers=QT)

        so = app.state.so
        m = so.dang_chay["gpu-5xx-client"]

        async def fn_500_nhung_loi_khach(*args, **kwargs):
            than_loi = json.dumps({
                "error": {
                    "message": "Maximum context length exceeded",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                }
            }).encode("utf-8")
            return (500, {"content-type": "application/json"}, than_loi)

        for _ in range(5):
            res = asyncio.run(m._dieu_phoi(fn_500_nhung_loi_khach))
            assert res[0] == 500

        # Mạch ngắt không được kích vì cấu trúc lỗi là invalid_request_error
        assert m.mach_ngat.trang_thai == "dong", f"Mạch ngắt bị kích {m.mach_ngat.trang_thai} do lỗi của khách!"
        assert m.mach_ngat.so_loi_lien_tiep == 0






