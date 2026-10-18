"""Module doc-viettat — Chuẩn hoá chữ để đọc tiếng Việt chuẩn OpenAI / TTS.

Thuần Python, không mạng, không I/O ngoài.
Cung cấp khả năng «thiet_bi» (chuẩn C2) với thực thể `doc_viettat.chuan_hoa`,
hành động `chuan_hoa_doc`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .chuan_hoa import chuan_hoa_doc

log = logging.getLogger("web2api.doc_viettat")


class Module:
    """Module chuẩn hoá chữ đọc tiếng Việt cho web2api."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "doc_viettat")
        self._cau_hinh: dict[str, Any] = {}

    def nap(self, cau_hinh: dict[str, Any]) -> None:
        """Nạp cấu hình đã lưu (không gọi mạng)."""
        self._cau_hinh = cau_hinh or {}

    async def cai(self, cau_hinh: dict[str, Any]) -> dict[str, Any]:
        """Kiểm tra cấu hình khi cài đặt module."""
        return {
            "ok": True,
            "ket_qua_tung_khoa": [],
            "cau_hinh_luu": {},
        }

    async def suc_khoe(self) -> dict[str, Any]:
        """Báo cáo sức khoẻ module (không gọi mạng)."""
        return {
            "ok": True,
            "base_url": None,
            "khoa": [],
        }

    async def dung(self) -> None:
        """Dọn dẹp khi dừng module."""
        return None

    # ── Chuẩn thiet_bi (C1/C2): Cung cấp công cụ cho AI qua OpenAI function calling ──

    async def thuc_the(self, loc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Khai báo thực thể công cụ chuan_hoa_doc."""
        return [
            {
                "id": f"{self.tien_to}.chuan_hoa",
                "ten": "Chuẩn hoá chữ đọc tiếng Việt",
                "loai": "cong_cu",
                "rui_ro_mac_dinh": "doc",
                "tinh_nang": [
                    {
                        "hanh_dong": "chuan_hoa_doc",
                        "mo_ta": "Chuẩn hoá chữ viết tắt, số, đơn vị, công thức hoá học để đọc tiếng Việt chuẩn OpenAI / TTS",
                        "tham_so": {
                            "type": "object",
                            "required": ["van_ban"],
                            "additionalProperties": False,
                            "properties": {
                                "van_ban": {
                                    "type": "string",
                                    "description": "Văn bản tiếng Việt cần chuẩn hoá (tối đa 20.000 ký tự)",
                                },
                                "tuy_chon": {
                                    "type": "object",
                                    "description": "Các tuỳ chọn xử lý bổ sung",
                                },
                            },
                        },
                    }
                ],
            }
        ]

    async def trang_thai(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Báo trạng thái thực thể."""
        target_id = f"{self.tien_to}.chuan_hoa"
        if not ids or target_id in ids:
            return {
                target_id: {
                    "gia_tri": "san_sang",
                    "don_vi": None,
                    "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "hop_le": True,
                }
            }
        return {}

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict[str, Any] | None) -> dict[str, Any]:
        """Thực thi công cụ chuan_hoa_doc."""
        target_id = f"{self.tien_to}.chuan_hoa"
        if id != target_id:
            return {"ok": False, "loi": f"Không tìm thấy thực thể {id!r}"}

        if hanh_dong != "chuan_hoa_doc":
            return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ"}

        tham_so = tham_so or {}
        van_ban = tham_so.get("van_ban")
        if not isinstance(van_ban, str):
            return {"ok": False, "loi": "Thiếu tham số bắt buộc «van_ban»"}

        tuy_chon = tham_so.get("tuy_chon")
        try:
            kq = chuan_hoa_doc(van_ban, tuy_chon)
            return {"ok": True, "ket_qua": kq}
        except ValueError as e:
            return {"ok": False, "loi": str(e)}
        except Exception as e:
            log.warning("Lỗi chuẩn hoá văn bản: %s", type(e).__name__)
            return {"ok": False, "loi": "Lỗi nội bộ khi chuẩn hoá văn bản"}

    # ── Phương thức gọi trực tiếp tiện ích ──

    def chuan_hoa_doc(self, van_ban: str, tuy_chon: dict[str, Any] | None = None) -> dict[str, Any]:
        """Gọi trực tiếp hàm chuẩn hoá văn bản."""
        return chuan_hoa_doc(van_ban, tuy_chon)
