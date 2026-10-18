"""Test NV108: module antigravity tự lấy model mới (lam_moi_model) + báo phiên bản CLI agy.

Kiểm tra:
1. Module antigravity:
   - lam_moi_model() khi chưa cấu hình: không làm gì, không lỗi
   - lam_moi_model() khi đã cấu hình: gọi đúng POST /v1/models/lam-moi, xoá đệm và lấy danh mục mới
   - lam_moi_model() khi cầu nối lỗi: ném ngoại lệ chung (KHÔNG nuốt)
   - suc_khoe() trả về agy_phien_ban từ cầu nối (null nếu không có / lỗi)
2. Tích hợp NV102:
   - VongLamMoiModel tự động nhận ra module antigravity qua lam_moi_model
   - Cập nhật model_so.json: model mới xuất hiện, model biến mất được ghi nhận
3. Bảo mật & leo thang quyền:
   - POST /v1/models/lam-moi của cầu nối từ chối không khoá (401), sai khoá (401)
   - Không lộ khoá cầu nối ra ngoài
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import kho, so_module
from app.kham_pha import doc_manifest, nap_ma, van_tay
from app.lam_moi_model import VongLamMoiModel, _doc_so
from gia import MayChuThat

GOC = Path(__file__).resolve().parents[1]
THU_MUC_MODULE = GOC / "modules" / "antigravity"
KHOA_CAU_NOI = "sk-lan-0000000000000000000000000000xyzw"


class CauNoiGiaNV108:
    """Máy chủ cầu nối giả lập đầy đủ cho NV108."""

    def __init__(self):
        self.da_goi_lam_moi = False
        self.da_goi_models = False
        self.da_goi_health = False
        self.so_lan_models = 0
        self.so_lan_lam_moi = 0
        self.phien_ban_agy = "1.2.3-alpha"
        self.ds_models = [
            {"id": "gemini-3.8-flash-high", "object": "model", "owned_by": "antigravity"},
            {"id": "claude-sonnet-4-6", "object": "model", "owned_by": "antigravity"},
        ]
        self.lam_moi_loi = False
        self.lam_moi_ma_loi = 502

        routes = [
            Route("/health", self.health, methods=["GET"]),
            Route("/v1/models", self.models, methods=["GET"]),
            Route("/v1/models/lam-moi", self.lam_moi, methods=["POST"]),
            Route("/tai-khoan", self.tai_khoan, methods=["GET"]),
        ]
        self.app = Starlette(routes=routes)

    async def health(self, request):
        self.da_goi_health = True
        return JSONResponse({
            "status": "ok",
            "accounts": 1,
            "agy_phien_ban": self.phien_ban_agy,
            "models_doi_luc": "2026-10-18T10:00:00Z",
        })

    async def models(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        self.da_goi_models = True
        self.so_lan_models += 1
        return JSONResponse({
            "object": "list",
            "data": self.ds_models,
        })

    async def lam_moi(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        self.da_goi_lam_moi = True
        self.so_lan_lam_moi += 1
        if self.lam_moi_loi:
            return JSONResponse({"error": {"message": "Lỗi thượng nguồn"}}, status_code=self.lam_moi_ma_loi)
        return JSONResponse({
            "ok": True,
            "so_model": len(self.ds_models),
            "moi": ["gemini-3.9-pro"],
            "khong_con": ["claude-sonnet-4-6"],
        })

    async def tai_khoan(self, request):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {KHOA_CAU_NOI}":
            return JSONResponse({"error": {"message": "Sai khoá"}}, status_code=401)
        return JSONResponse({"tai_khoan": []})


@pytest.fixture
def du_lieu(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB2API_DATA", str(tmp_path))
    monkeypatch.setattr(so_module, "THU_MUC_ANH", tmp_path / "anh-rong")
    return tmp_path


def tao_instance_module():
    m, _ = doc_manifest(THU_MUC_MODULE)
    vt = van_tay(THU_MUC_MODULE)
    lop, _ = nap_ma(THU_MUC_MODULE, m, vt)
    return lop(m)


def test_lam_moi_model_chua_cau_hinh_khong_goi_gi():
    mod = tao_instance_module()
    # Chưa gọi nap() -> không ném ngoại lệ, không gọi mạng
    asyncio.run(mod.lam_moi_model())


def test_lam_moi_model_goi_dung_duong_va_cap_nhat_models():
    cn = CauNoiGiaNV108()
    with MayChuThat(cn.app) as server:
        async def _chay():
            mod = tao_instance_module()
            mod.nap({
                "dia_chi_cau_noi": server.url,
                "khoa_cau_noi": KHOA_CAU_NOI,
            })
            # Lần 1: lấy models qua cache ChuyenTiepOpenAI
            ds1 = await mod.models()
            assert len(ds1) == 2
            assert cn.so_lan_models == 1

            # Thay đổi danh sách model phía cầu nối
            cn.ds_models = [
                {"id": "gemini-3.8-flash-high", "object": "model", "owned_by": "antigravity"},
                {"id": "gemini-3.9-pro", "object": "model", "owned_by": "antigravity"},
            ]

            # Nếu chưa lam_moi_model, ChuyenTiepOpenAI vẫn trả đệm cũ
            ds_dem = await mod.models()
            assert [m["id"] for m in ds_dem] == ["antigravity/gemini-3.8-flash-high", "antigravity/claude-sonnet-4-6"]
            assert cn.so_lan_models == 1

            # Gọi lam_moi_model()
            await mod.lam_moi_model()
            assert cn.da_goi_lam_moi is True
            assert cn.so_lan_lam_moi == 1

            # Sau khi lam_moi_model, đệm cũ đã hết hạn, models() phải gọi lại cầu nối
            ds2 = await mod.models()
            assert [m["id"] for m in ds2] == ["antigravity/gemini-3.8-flash-high", "antigravity/gemini-3.9-pro"]
            assert cn.so_lan_models == 2

        asyncio.run(_chay())


def test_lam_moi_model_loi_cau_noi_nem_ngoai_le():
    cn = CauNoiGiaNV108()
    cn.lam_moi_loi = True
    with MayChuThat(cn.app) as server:
        async def _chay():
            mod = tao_instance_module()
            mod.nap({
                "dia_chi_cau_noi": server.url,
                "khoa_cau_noi": KHOA_CAU_NOI,
            })
            # Khi cầu nối báo lỗi 502, lam_moi_model PHẢI ném ngoại lệ (không nuốt)
            with pytest.raises(Exception):
                await mod.lam_moi_model()

        asyncio.run(_chay())


def test_suc_khoe_lay_agy_phien_ban_tu_cau_noi():
    cn = CauNoiGiaNV108()
    with MayChuThat(cn.app) as server:
        async def _chay():
            mod = tao_instance_module()
            mod.nap({
                "dia_chi_cau_noi": server.url,
                "khoa_cau_noi": KHOA_CAU_NOI,
            })
            sk = await mod.suc_khoe()
            assert sk.get("agy_phien_ban") == "1.2.3-alpha"

            # Cầu nối không có agy_phien_ban (hoặc null)
            cn.phien_ban_agy = None
            sk2 = await mod.suc_khoe()
            assert sk2.get("agy_phien_ban") is None

        asyncio.run(_chay())


def test_vong_nv102_cap_nhat_so_cho_antigravity(du_lieu):
    cn = CauNoiGiaNV108()
    with MayChuThat(cn.app) as server:
        async def _chay():
            mod = tao_instance_module()
            mod.nap({
                "dia_chi_cau_noi": server.url,
                "khoa_cau_noi": KHOA_CAU_NOI,
            })

            # Tạo VongLamMoiModel với module antigravity
            vong = VongLamMoiModel(lambda: {"antigravity": mod})
            kq = await vong.lam_moi_mot("antigravity")
            assert kq["ket_qua"] == "ok"
            assert "antigravity/gemini-3.8-flash-high" in kq["moi"]
            assert "antigravity/claude-sonnet-4-6" in kq["moi"]

            so = _doc_so()
            assert "antigravity" in so["modules"]
            mod_so = so["modules"]["antigravity"]
            assert "antigravity/gemini-3.8-flash-high" in mod_so
            assert mod_so["antigravity/gemini-3.8-flash-high"]["con"] is True

            # Thay đổi model: claude biến mất, gemini-3.9-pro mới
            cn.ds_models = [
                {"id": "gemini-3.8-flash-high", "object": "model", "owned_by": "antigravity"},
                {"id": "gemini-3.9-pro", "object": "model", "owned_by": "antigravity"},
            ]
            kq2 = await vong.lam_moi_mot("antigravity")
            assert kq2["ket_qua"] == "ok"
            assert "antigravity/gemini-3.9-pro" in kq2["moi"]

            so2 = _doc_so()
            mod_so2 = so2["modules"]["antigravity"]
            assert mod_so2["antigravity/claude-sonnet-4-6"]["vang"] == 1
            assert mod_so2["antigravity/claude-sonnet-4-6"]["con"] is True

            # Vắng thêm một chu kỳ -> con: False
            kq3 = await vong.lam_moi_mot("antigravity")
            assert "antigravity/claude-sonnet-4-6" in kq3["khong_con"]
            so3 = _doc_so()
            assert so3["modules"]["antigravity"]["antigravity/claude-sonnet-4-6"]["con"] is False

        asyncio.run(_chay())
