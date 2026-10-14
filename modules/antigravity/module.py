"""Module Antigravity — chuyển tiếp yêu cầu AI tới cầu nối Antigravity CLI (agy) chạy trên máy chủ.

Module mỏng kết nối tới ``dich-vu/antigravity-cau-noi/`` qua giao thức tương thích OpenAI.
Không gọi API Google trực tiếp, không giả danh ứng dụng, không đọc/in token.
Mọi giao tiếp đi qua bộ chuyển tiếp dùng chung ``app.chuyen_tiep.ChuyenTiepOpenAI``.
"""

from __future__ import annotations

import json

import httpx

from app.chuyen_tiep import (
    ChuyenTiepOpenAI,
    PhanHoiQuaLon,
    doc_phan_hoi_co_tran,
    tra_loi_json,
)
from app.kho import che


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct: ChuyenTiepOpenAI | None = None
        self._dia_chi_cau_noi: str = ""
        self._khoa: str = ""

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động hoặc sau khi cài) — không gọi mạng."""
        dia_chi = str(cau_hinh.get("dia_chi_cau_noi") or "http://127.0.0.1:8300").strip().rstrip("/")
        khoa = str(cau_hinh.get("khoa_cau_noi") or "").strip()
        self._dia_chi_cau_noi = dia_chi
        self._khoa = khoa
        base_url = f"{dia_chi}/v1"
        self._ct = ChuyenTiepOpenAI(
            ten="Antigravity",
            tien_to=self.tien_to,
            base_url=base_url,
            khoa=[khoa] if khoa else [],
        )

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm tra cấu hình bằng cách gọi GET /health và GET /v1/models của cầu nối."""
        dia_chi = str(cau_hinh.get("dia_chi_cau_noi") or "http://127.0.0.1:8300").strip().rstrip("/")
        khoa = str(cau_hinh.get("khoa_cau_noi") or "").strip()
        if not dia_chi:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập địa chỉ cầu nối (dia_chi_cau_noi)."}
        if not khoa:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập khoá cầu nối (khoa_cau_noi)."}

        headers = {"Authorization": f"Bearer {khoa}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Kiểm /health
            try:
                r_health = await client.get(f"{dia_chi}/health")
                if r_health.status_code != 200:
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [],
                        "loi": f"Cầu nối trả HTTP {r_health.status_code} tại /health",
                    }
            except httpx.HTTPError as e:
                return {
                    "ok": False,
                    "ket_qua_tung_khoa": [],
                    "loi": f"Không kết nối được cầu nối tại {dia_chi}/health: {type(e).__name__}",
                }

            # 2. Kiểm /v1/models
            try:
                req_models = client.build_request("GET", f"{dia_chi}/v1/models", headers=headers)
                r_models = await client.send(req_models, stream=True)
                noi_dung = await doc_phan_hoi_co_tran(r_models)
                if r_models.status_code != 200:
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [],
                        "loi": f"Cầu nối từ chối xác thực hoặc lỗi tại /v1/models (HTTP {r_models.status_code})",
                    }
                j = json.loads(noi_dung)
                data = j.get("data") if isinstance(j, dict) else None
                if not isinstance(data, list):
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [],
                        "loi": "Cầu nối trả /v1/models không đúng định dạng OpenAI",
                    }
            except (httpx.HTTPError, ValueError, PhanHoiQuaLon, TimeoutError) as e:
                return {
                    "ok": False,
                    "ket_qua_tung_khoa": [],
                    "loi": f"Không lấy được danh sách model từ {dia_chi}/v1/models: {type(e).__name__}",
                }

        ket_qua = [{
            "khoa": che(khoa),
            "ok": True,
            "loi": None,
            "so_model": len(data),
        }]
        return {
            "ok": True,
            "ket_qua_tung_khoa": ket_qua,
            "loi": None,
            "cau_hinh_luu": {
                "dia_chi_cau_noi": dia_chi,
                "khoa_cau_noi": khoa,
            },
        }

    async def chat(self, body: dict, stream: bool):
        if self._ct is None:
            return tra_loi_json(
                503,
                {"error": {"message": "Module Antigravity chưa được cấu hình", "type": "api_error", "code": "module_not_configured"}},
            )
        return await self._ct.chat(body, stream)

    async def models(self) -> list[dict]:
        if self._ct is None:
            return []
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        if self._ct is None:
            return {"ok": False, "loi": "Module Antigravity chưa được nạp cấu hình"}
        kq = await self._ct.suc_khoe()
        if self._dia_chi_cau_noi and self._khoa:
            try:
                khach = self._ct._client()
                req = khach.build_request(
                    "GET",
                    f"{self._dia_chi_cau_noi}/tai-khoan",
                    headers={"Authorization": f"Bearer {self._khoa}"},
                    timeout=httpx.Timeout(self._ct.TIMEOUT_KIEM),
                )
                r = await khach.send(req, stream=True)
                noi_dung = await doc_phan_hoi_co_tran(r)
                if r.status_code == 200:
                    j = json.loads(noi_dung)
                    if isinstance(j, dict) and "tai_khoan" in j:
                        kq["tai_khoan"] = j["tai_khoan"]
            except (httpx.HTTPError, ValueError, PhanHoiQuaLon, TimeoutError):
                pass
        return kq

    async def dung(self) -> None:
        if self._ct is not None:
            await self._ct.dung()
