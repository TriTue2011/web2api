"""Kiểm tra chuẩn phân loại lỗi cho module (VIỆC P1): LÕI ĐẶT CHUẨN, MODULE ĐỔI THEO.

Chứng minh:
1. app/kham_pha.py định nghĩa chuẩn loại lỗi trung lập LOAI_LOI_CHUAN và phan_loai_loi_mac_dinh.
2. Hook tuỳ chọn phan_loai_loi(ma, loi, boi_canh) của module:
   - Ánh xạ các mã lỗi lạ:
     - 402 -> "het_han_muc": xoay khoá, nghỉ Retry-After.
     - 410 -> "khong_co_o_khoa": chat ghi sổ 6 giờ (khoá, model); khả năng nghỉ 24 giờ.
     - 451 -> "chan_vung": trả ngay 451, không đổi khoá, không phạt khoá.
     - 498 -> "khoa_hong": cho khoá nghỉ 1 giờ (3600s), xoay khoá.
   - Hook ném ngoại lệ hoặc trả về mã lạ -> coi là "khong_ro", không vỡ tiến trình.
3. Module lạ cắm vào hệ thống qua nạp nóng, chạy đúng mọi hành vi chỉ bằng hook, lõi không sửa.
"""

import asyncio
import json
import logging
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import pytest
from fastapi.testclient import TestClient

from app.chuyen_tiep import ChuyenTiepOpenAI, NGHI_KHOA_HONG, NGHI_MODEL_KHONG_CO
from app.kham_pha import LOAI_LOI_CHUAN, phan_loai_loi_mac_dinh
from app.main import app
from gia import MayChuThat, ghi_thu_muc

KHOA_QT = "khoa-quan-tri-test-0123456789abcdef"
QT = {"Authorization": f"Bearer {KHOA_QT}"}
K1 = "AIzaSyTOT-mot-000000000000000000000001"
K2 = "AIzaSyTOT-hai-000000000000000000000002"
K3 = "AIzaSyTOT-ba--000000000000000000000003"


# ───────────── 1. Kiểm tra chuẩn loại lỗi và phân loại mặc định ─────────────

def test_chuan_loi_kham_pha():
    assert "khoa_hong" in LOAI_LOI_CHUAN
    assert "het_han_muc" in LOAI_LOI_CHUAN
    assert "khong_co_o_khoa" in LOAI_LOI_CHUAN
    assert "yeu_cau_sai" in LOAI_LOI_CHUAN
    assert "may_chu_loi" in LOAI_LOI_CHUAN
    assert "chan_vung" in LOAI_LOI_CHUAN
    assert "khong_ro" in LOAI_LOI_CHUAN

    # Phân loại mặc định theo mã trạng thái HTTP
    assert phan_loai_loi_mac_dinh(429) == "het_han_muc"
    assert phan_loai_loi_mac_dinh(401) == "khoa_hong"
    assert phan_loai_loi_mac_dinh(403) == "khoa_hong"
    assert phan_loai_loi_mac_dinh(500) == "may_chu_loi"
    assert phan_loai_loi_mac_dinh(502) == "may_chu_loi"
    assert phan_loai_loi_mac_dinh(503) == "may_chu_loi"
    assert phan_loai_loi_mac_dinh(504) == "may_chu_loi"
    assert phan_loai_loi_mac_dinh(400) == "khong_ro"
    assert phan_loai_loi_mac_dinh(404) == "khong_ro"
    assert phan_loai_loi_mac_dinh(418) == "khong_ro"


def test_chuyen_tiep_an_toan_khi_hook_nem_loi_hoac_tra_ma_la(caplog):
    # Hook ném lỗi -> coi là khong_ro
    def hook_loi(ma, loi, boi_canh):
        raise RuntimeError("hook bi loi bat ngo")

    ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url="http://127.0.0.1", khoa=[K1],
                         phan_loai_loi=hook_loi)
    with caplog.at_level(logging.WARNING):
        res = ct.phan_loai_loi(500, {"error": {"message": "loi"}}, {})
    assert res == "khong_ro"
    assert "hook phan_loai_loi ném lỗi" in caplog.text

    # Hook trả về None -> fallback phan_loai_loi_mac_dinh
    def hook_none(ma, loi, boi_canh):
        return None

    ct_none = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url="http://127.0.0.1", khoa=[K1],
                              phan_loai_loi=hook_none)
    assert ct_none.phan_loai_loi(500, {"error": {"message": "loi"}}, {}) == "may_chu_loi"
    assert ct_none.phan_loai_loi(429, {"error": {"message": "loi"}}, {}) == "het_han_muc"
    assert ct_none.phan_loai_loi(401, {"error": {"message": "loi"}}, {}) == "khoa_hong"

    # Hook trả về mã lạ không thuộc LOAI_LOI_CHUAN -> coi là khong_ro
    def hook_ma_la(ma, loi, boi_canh):
        return "ma_hoan_toan_khong_hop_le_xyz"

    ct2 = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url="http://127.0.0.1", khoa=[K1],
                          phan_loai_loi=hook_ma_la)
    assert ct2.phan_loai_loi(500, {"error": {"message": "loi"}}, {}) == "khong_ro"


# ───────────── 2. Máy chủ giả với các mã lỗi lạ ─────────────

class MayChuLa:
    """Máy chủ giả trả các mã lỗi lạ như 402, 410, 451, 498 theo từng khoá."""
    def __init__(self):
        self.hanh_vi = {}
        self.nhat_ky = []

    async def chat(self, request: Request):
        khoa = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json()
        self.nhat_ky.append({"khoa": khoa, "body": body})
        hv = self.hanh_vi.get(khoa, {"ma": 200})
        ma = hv.get("ma", 200)
        if ma == 200:
            return JSONResponse({"choices": [{"message": {"role": "assistant", "content": f"ok tu {khoa[-4:]}"}}]})
        hd = {}
        if "retry-after" in hv:
            hd["retry-after"] = str(hv["retry-after"])
        return JSONResponse({"error": {"message": hv.get("tin", f"loi {ma}"), "type": "error", "code": f"code_{ma}"}},
                            status_code=ma, headers=hd)

    async def anh(self, request: Request):
        khoa = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json()
        self.nhat_ky.append({"khoa": khoa, "body": body})
        hv = self.hanh_vi.get(khoa, {"ma": 200})
        ma = hv.get("ma", 200)
        if ma == 200:
            return JSONResponse({"data": [{"b64_json": "YWJj"}]})
        hd = {}
        if "retry-after" in hv:
            hd["retry-after"] = str(hv["retry-after"])
        return JSONResponse({"error": {"message": hv.get("tin", f"loi {ma}"), "type": "error", "code": f"code_{ma}"}},
                            status_code=ma, headers=hd)

    def tao_app(self):
        routes = [
            Route("/chat/completions", self.chat, methods=["POST"]),
            Route("/images/generations", self.anh, methods=["POST"]),
        ]
        return Starlette(routes=routes)


@pytest.fixture
def may_la():
    mc = MayChuLa()
    app_la = mc.tao_app()
    with MayChuThat(app_la) as s:
        mc.url = s.url
        yield mc


# Hook module lạ ánh xạ các mã lỗi khác biệt
def hook_module_la(ma: int, loi: dict, boi_canh: dict) -> str | None:
    if ma == 402:
        return "het_han_muc"
    if ma == 410:
        return "khong_co_o_khoa"
    if ma == 451:
        return "chan_vung"
    if ma == 498:
        return "khoa_hong"
    return None


# ───────────── 3. Test hành vi chat với các loại lỗi lạ ─────────────

def test_module_la_chat_402_het_han_muc(may_la):
    async def _chay():
        # K1 trả 402 kèm Retry-After: 45 -> coi như het_han_muc, xoay sang K2
        may_la.hanh_vi[K1] = {"ma": 402, "retry-after": 45}
        may_la.hanh_vi[K2] = {"ma": 200}

        ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url=may_la.url, khoa=[K1, K2],
                             phan_loai_loi=hook_module_la)
        ct._dong_ho = lambda: 1000.0

        ma, hd, body = await ct.chat({"model": "la/model-1", "messages": []}, stream=False)
        assert ma == 200
        assert ct._nghi[K1] == 1045.0  # nghỉ 45s
        assert ct._nghi_ma[K1] == 429
        assert K2[-4:] in str(body)

        # Lượt tiếp theo: K1 đang nghỉ, chỉ gọi K2
        may_la.nhat_ky.clear()
        ma, hd, body = await ct.chat({"model": "la/model-1", "messages": []}, stream=False)
        assert ma == 200
        assert len(may_la.nhat_ky) == 1 and may_la.nhat_ky[0]["khoa"] == K2
        await ct.dung()

    asyncio.run(_chay())


def test_module_la_chat_410_khong_co_o_khoa(may_la):
    async def _chay():
        # K1 trả 410 -> coi như khong_co_o_khoa, ghi sổ 6h cho (khoa, model), xoay sang K2
        may_la.hanh_vi[K1] = {"ma": 410}
        may_la.hanh_vi[K2] = {"ma": 200}

        ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url=may_la.url, khoa=[K1, K2],
                             phan_loai_loi=hook_module_la)
        ct._dong_ho = lambda: 1000.0

        ma, hd, body = await ct.chat({"model": "la/model-x", "messages": []}, stream=False)
        assert ma == 200
        # K1 không bị nghỉ toàn bộ khoá
        assert K1 not in ct._nghi
        # Được ghi sổ 6h cho model-x
        assert ct._khong_co[(K1, "model-x")] == 1000.0 + NGHI_MODEL_KHONG_CO

        # Lượt sau với model-x: bỏ qua K1, gọi thẳng K2
        may_la.nhat_ky.clear()
        ma, hd, body = await ct.chat({"model": "la/model-x", "messages": []}, stream=False)
        assert ma == 200
        assert len(may_la.nhat_ky) == 1 and may_la.nhat_ky[0]["khoa"] == K2

        # Nhưng với model-y: K1 vẫn được gọi bình thường
        may_la.nhat_ky.clear()
        ct._vi_tri = 0
        may_la.hanh_vi[K1] = {"ma": 200}
        ma, hd, body = await ct.chat({"model": "la/model-y", "messages": []}, stream=False)
        assert ma == 200
        assert len(may_la.nhat_ky) == 1 and may_la.nhat_ky[0]["khoa"] == K1
        await ct.dung()

    asyncio.run(_chay())


def test_module_la_chat_451_chan_vung(may_la):
    async def _chay():
        # K1 trả 451 -> coi như chan_vung: lỗi hạ tầng, trả ngay, không đổi khoá, không phạt khoá
        may_la.hanh_vi[K1] = {"ma": 451, "tin": "Unavailable For Legal Reasons"}
        may_la.hanh_vi[K2] = {"ma": 200}

        ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url=may_la.url, khoa=[K1, K2],
                             phan_loai_loi=hook_module_la)

        ma, hd, body = await ct.chat({"model": "la/model-1", "messages": []}, stream=False)
        assert ma == 451
        # Chỉ gọi đúng 1 lần (K1), không gọi K2
        assert len(may_la.nhat_ky) == 1 and may_la.nhat_ky[0]["khoa"] == K1
        # Không phạt khoá
        assert ct._nghi == {}
        await ct.dung()

    asyncio.run(_chay())


def test_module_la_chat_498_khoa_hong(may_la):
    async def _chay():
        # K1 trả 498 -> coi như khoa_hong: nghỉ 1 giờ, xoay sang K2
        may_la.hanh_vi[K1] = {"ma": 498, "tin": "Token Expired"}
        may_la.hanh_vi[K2] = {"ma": 200}

        ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url=may_la.url, khoa=[K1, K2],
                             phan_loai_loi=hook_module_la)
        ct._dong_ho = lambda: 1000.0

        ma, hd, body = await ct.chat({"model": "la/model-1", "messages": []}, stream=False)
        assert ma == 200
        assert ct._nghi[K1] == 1000.0 + NGHI_KHOA_HONG
        assert ct._nghi_ma[K1] == 498
        await ct.dung()

    asyncio.run(_chay())


# ───────────── 4. Test gọi khả năng (goi_kha_nang) với các loại lỗi lạ ─────────────

def test_module_la_kha_nang_402_410_451_498(may_la):
    async def _chay():
        ct = ChuyenTiepOpenAI(ten="TestLa", tien_to="la", base_url=may_la.url, khoa=[K1, K2],
                             kha_nang=("anh",), phan_loai_loi=hook_module_la)
        ct._dong_ho = lambda: 1000.0

        # 1. 451 (chặn vùng): trả ngay không thử tiếp, không phạt
        may_la.hanh_vi[K1] = {"ma": 451}
        may_la.hanh_vi[K2] = {"ma": 200}
        khoa_duoc, (ma, _, _) = await ct.goi_kha_nang("anh", "tạo ảnh", "POST",
                                                     f"{may_la.url}/images/generations",
                                                     json_than={"prompt": "anh"},
                                                     tieu_de=ct._tieu_de)
        assert khoa_duoc is None and ma == 451
        assert ct._kn == {}

        # 2. 410 (khong_co_o_khoa): K1 bị từ chối khả năng này 24h, xoay sang K2 thành công
        ct._vi_tri = 0
        may_la.hanh_vi[K1] = {"ma": 410}
        may_la.hanh_vi[K2] = {"ma": 200}
        khoa_duoc, (ma, _, _) = await ct.goi_kha_nang("anh", "tạo ảnh", "POST",
                                                     f"{may_la.url}/images/generations",
                                                     json_than={"prompt": "anh"},
                                                     tieu_de=ct._tieu_de)
        assert khoa_duoc == K2 and ma == 200
        assert ct._kn[(K1, "anh")]["dung_duoc"] is False
        assert ct._kn[(K1, "anh")]["ma"] == 410

        # 3. 498 (khoa_hong): K1 bị từ chối 24h, xoay sang K2
        ct._vi_tri = 0
        ct._kn.clear()
        may_la.hanh_vi[K1] = {"ma": 498}
        khoa_duoc, (ma, _, _) = await ct.goi_kha_nang("anh", "tạo ảnh", "POST",
                                                     f"{may_la.url}/images/generations",
                                                     json_than={"prompt": "anh"},
                                                     tieu_de=ct._tieu_de)
        assert khoa_duoc == K2 and ma == 200
        assert ct._kn[(K1, "anh")]["ma"] == 498

        await ct.dung()

    asyncio.run(_chay())


# ───────────── 5. Test tích hợp module lạ nạp nóng qua FastAPI ─────────────

def test_module_la_nap_nong_tich_hop(tmp_path, monkeypatch, may_la):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setenv("WEB2API_ADMIN_KEY", KHOA_QT)
    mod_dir = tmp_path / "modules" / "la-api"

    # Module lạ với manifest và module.py tự định nghĩa hook phan_loai_loi
    manifest = {
        "id": "la-api", "ten": "Dịch vụ Lạ", "tien_to": "la", "phien_ban": "1.0.0",
        "mo_ta": "Module test chuẩn lỗi với các mã khác thường",
        "giao_thuc": "openai",
        "tac_gia": "test", "giay_phep": "MIT", "loai": "ai", "cung_cap": ["chat"],
        "truong_cau_hinh": [{"khoa": "api_keys", "nhan": "Khoá", "kieu": "danh_sach_bi_mat"}],
        "tai_nguyen": {"ram_them_mb": None, "dia_mb": None, "gpu": False, "mang_ra": [], "ghi_chu": ""},
    }
    ma_module = f'''
from app.chuyen_tiep import ChuyenTiepOpenAI, ds_khoa

def phan_loai(ma, loi, boi_canh):
    if ma == 402:
        return "het_han_muc"
    if ma == 410:
        return "khong_co_o_khoa"
    if ma == 451:
        return "chan_vung"
    if ma == 498:
        return "khoa_hong"
    return None

class Module:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct = None

    def nap(self, ch):
        self._ct = ChuyenTiepOpenAI(ten="DichVuLa", tien_to=self.tien_to,
                                   base_url=ch.get("base_url", "{may_la.url}"),
                                   khoa=ds_khoa(ch.get("api_keys")),
                                   phan_loai_loi=phan_loai)

    async def cai(self, ch):
        self.nap(ch)
        return {{"ok": True, "ket_qua_tung_khoa": [], "loi": None, "cau_hinh_luu": ch}}

    async def chat(self, body, stream):
        return await self._ct.chat(body, stream)

    async def models(self):
        return [{{"id": "la/la-model", "object": "model"}}]

    async def suc_khoe(self):
        return await self._ct.suc_khoe()

    async def dung(self):
        if self._ct:
            await self._ct.dung()
'''
    with TestClient(app) as c:
        ghi_thu_muc(mod_dir, {"manifest.json": json.dumps(manifest).encode(), "module.py": ma_module.encode()})
        # Nạp nóng module mới
        r_nap = c.post("/api/modules/nap-lai", headers=QT)
        assert r_nap.status_code == 200
        assert "la-api" in r_nap.json()["them"]

        # Cài cấu hình cho module với K1 và K2
        cai_res = c.post("/api/modules/la-api/cai", json={"cau_hinh": {"api_keys": [K1, K2], "base_url": may_la.url}}, headers=QT)
        assert cai_res.status_code == 200

        # 1. Gọi chat khi K1 gặp 402 -> lõi nhận biết het_han_muc, xoay K2 thành công
        may_la.hanh_vi[K1] = {"ma": 402, "retry-after": 60}
        may_la.hanh_vi[K2] = {"ma": 200}
        r_chat = c.post("/v1/chat/completions", json={"model": "la/la-model", "messages": [{"role": "user", "content": "hi"}]}, headers=QT)
        assert r_chat.status_code == 200
        assert f"ok tu {K2[-4:]}" in r_chat.json()["choices"][0]["message"]["content"]

        # 2. Gọi chat khi gặp 451 -> trả ngay 451
        may_la.hanh_vi[K2] = {"ma": 451, "tin": "Chan vung boi phap ly"}
        r_chat2 = c.post("/v1/chat/completions", json={"model": "la/la-model", "messages": [{"role": "user", "content": "hi"}]}, headers=QT)
        assert r_chat2.status_code == 451
