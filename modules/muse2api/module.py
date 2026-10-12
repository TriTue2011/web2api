"""Module muse2api — Muse Spark qua Meta Model API chính thức.

Gọi https://api.meta.ai/v1 (tương thích OpenAI) bằng API key của bạn.
Không mật khẩu, không cookie phiên web, không scrape.

Khả năng: chat (stream / không stream), anh (tạo ảnh).
Video: official API không có endpoint nên không khai.
"""

from __future__ import annotations

import logging
from typing import Any

from app.chuyen_tiep import ChuyenTiepOpenAI, ds_khoa

log = logging.getLogger("web2api.module.muse2api")

BASE_URL_MAC_DINH = "https://api.meta.ai/v1"


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi Meta Model API về mã trung lập của lõi."""
    if ma in (401, 403):
        return "khoa_hong"  # key sai / hết hạn / bị thu hồi
    if ma == 429:
        return "het_han_muc"  # rate limit tính theo team
    if ma == 400:
        return "yeu_cau_sai"
    if ma is not None and ma >= 500:
        return "may_chu_loi"
    return None


def chuan_hoa_base_url(gia_tri: Any) -> str:
    base = (gia_tri or "").strip() or BASE_URL_MAC_DINH
    return base.rstrip("/")


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest["tien_to"]
        self._ct: ChuyenTiepOpenAI | None = None

    def phan_loai_loi(self, ma: int, loi: dict, boi_canh: dict) -> str | None:
        return phan_loai_loi(ma, loi, boi_canh)

    def _tao(self, khoa: list[str], base: str) -> ChuyenTiepOpenAI:
        # Khả năng tốn tiền suc_khoe báo theo từng khoá = mọi mã đã khai ở `cung_cap` trừ chat.
        kn = tuple(x for x in self.manifest.get("cung_cap", []) if x != "chat")
        return ChuyenTiepOpenAI(ten="Meta", tien_to=self.tien_to, base_url=base,
                                khoa=khoa, phan_loai_loi=self.phan_loai_loi,
                                kha_nang=kn)

    # ── Vòng đời ────────────────────────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM (lúc khởi động/cài xong) — không gọi mạng."""
        self._ct = self._tao(ds_khoa(cau_hinh.get("api_keys")),
                             chuan_hoa_base_url(cau_hinh.get("base_url")))

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm từng khoá bằng GET /models (không tốn token).

        Chỉ giữ khoá hợp lệ; không khoá nào → ok=false.
        """
        khoa = ds_khoa(cau_hinh.get("api_keys"))
        if not khoa:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": "Chưa nhập khoá API nào (trường api_keys)."}
        base = chuan_hoa_base_url(cau_hinh.get("base_url"))
        ct = self._tao(khoa, base)
        try:
            ds = (await ct.suc_khoe())["khoa"]
        finally:
            await ct.dung()
        tot = [k for k, muc in zip(khoa, ds) if muc["ok"]]
        if not tot:
            return {"ok": False, "ket_qua_tung_khoa": ds,
                    "loi": f"Không khoá nào gọi được {base}/models."}
        return {"ok": True, "ket_qua_tung_khoa": ds, "loi": None,
                "cau_hinh_luu": {"api_keys": tot, "base_url": base}}

    # ── Khả năng ─────────────────────────────────────────────────────────

    async def chat(self, body: dict, stream: bool):
        return await self._ct.chat(body, stream)

    async def anh(self, body: dict):
        _, kq = await self._ct.goi_kha_nang(
            "anh", "tạo ảnh", "POST", f"{self._ct.base_url}/images/generations",
            json_than=self._ct.doi_model(body), tieu_de=self._ct._tieu_de)
        return kq

    async def models(self) -> list[dict]:
        return await self._ct.models()

    async def suc_khoe(self) -> dict:
        return await self._ct.suc_khoe()

    async def dung(self) -> None:
        if self._ct is not None:
            await self._ct.dung()
