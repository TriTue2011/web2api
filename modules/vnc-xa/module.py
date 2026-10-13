"""Module điều khiển trình duyệt từ xa (Chuẩn C6).

Module này đóng vai trò cầu nối HTTP tới một dịch vụ trình duyệt riêng
(chạy độc lập ở một container hoặc máy chủ riêng):
- Dịch vụ trình duyệt cung cấp API:
  POST /ho-so/mo        {ho_so, loai} -> {ho_so, loai, dich}
  POST /ho-so/{id}/dong {giu_phien}   -> {ok}
  GET  /ho-so                         -> danh sách hồ sơ
- Mọi lời gọi kèm header Authorization: Bearer <khoa_dich_vu>.
- Lõi web2api gọi các phương thức trinh_duyet_* của module này để quản lý hồ sơ và lấy
  địa chỉ nội bộ noVNC làm đích proxy.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.tu_xa import kiem_url_lan

log = logging.getLogger("web2api.module.vnc_xa")


def _che_khoa(k: str) -> str:
    if not k:
        return "…"
    return f"…{k[-4:]}" if len(k) >= 4 else "…"


def phan_loai_loi(ma: int, loi: dict, boi_canh: dict) -> str | None:
    """Phân loại lỗi theo cấu trúc mã HTTP chuẩn (không dò chữ)."""
    if ma in (401, 403):
        return "khoa_hong"
    if ma == 429:
        return "het_han_muc"
    if 500 <= ma <= 599:
        return "may_chu_loi"
    if 400 <= ma < 500:
        return "yeu_cau_sai"
    return "khong_ro"


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self._dich_vu_url = ""
        self._khoa = ""

    def nap(self, cau_hinh: dict) -> None:
        self._dich_vu_url = str(cau_hinh.get("dich_vu_url") or "").strip().rstrip("/")
        self._khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()

    async def cai(self, cau_hinh: dict) -> dict[str, Any]:
        dich_vu_url = str(cau_hinh.get("dich_vu_url") or "").strip().rstrip("/")
        khoa = str(cau_hinh.get("khoa_dich_vu") or "").strip()
        khoa_che = _che_khoa(khoa)

        if not dich_vu_url:
            return {
                "ok": False,
                "loi": "Thiếu địa chỉ dịch vụ trình duyệt (dich_vu_url)",
                "ket_qua_tung_khoa": [],
            }
        if not khoa:
            return {
                "ok": False,
                "loi": "Thiếu khoá dịch vụ (khoa_dich_vu)",
                "ket_qua_tung_khoa": [],
            }

        try:
            url_sach = await kiem_url_lan(dich_vu_url)
        except ValueError as e:
            return {
                "ok": False,
                "loi": f"Địa chỉ dịch vụ không hợp lệ hoặc bị chặn: {e}",
                "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": False, "loi": str(e)}],
            }

        # Kiểm tra kết nối tới dịch vụ trình duyệt qua GET /ho-so (KHÔNG mở trình duyệt khi cài)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{url_sach}/ho-so",
                    headers={"Authorization": f"Bearer {khoa}"},
                )
                if r.status_code == 200:
                    return {
                        "ok": True,
                        "ket_qua_tung_khoa": [{"khoa": khoa_che, "ok": True}],
                        "loi": None,
                        "cau_hinh_luu": {
                            "dich_vu_url": url_sach,
                            "khoa_dich_vu": khoa,
                        },
                    }
                elif r.status_code in (401, 403):
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [
                            {"khoa": khoa_che, "ok": False, "loi": "Khoá dịch vụ không đúng"}
                        ],
                        "loi": f"Khoá dịch vụ không đúng (mã HTTP {r.status_code})",
                    }
                else:
                    return {
                        "ok": False,
                        "ket_qua_tung_khoa": [
                            {"khoa": khoa_che, "ok": False, "loi": f"Mã HTTP {r.status_code}"}
                        ],
                        "loi": f"Dịch vụ trình duyệt trả mã {r.status_code}",
                    }
        except Exception as e:
            return {
                "ok": False,
                "ket_qua_tung_khoa": [
                    {"khoa": khoa_che, "ok": False, "loi": "Không kết nối được"}
                ],
                "loi": f"Không thể kết nối tới dịch vụ trình duyệt ({type(e).__name__})",
            }

    async def trinh_duyet_mo(self, ho_so: str, loai: str) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình địa chỉ dịch vụ hoặc khoá")

        url = f"{self._dich_vu_url}/ho-so/mo"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                json={"ho_so": ho_so, "loai": loai},
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            if r.status_code == 200:
                kq = r.json()
                if not isinstance(kq, dict):
                    raise RuntimeError("Phản hồi mở hồ sơ từ dịch vụ không phải đối tượng JSON")
                return kq
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_dong(self, ho_so: str, giu_phien: bool) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")

        url = f"{self._dich_vu_url}/ho-so/{ho_so}/dong"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json={"giu_phien": giu_phien},
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            if r.status_code == 200:
                kq = r.json()
                return kq if isinstance(kq, dict) else {"ok": True}
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def trinh_duyet_ds(self) -> list[dict[str, Any]]:
        if not self._dich_vu_url or not self._khoa:
            raise RuntimeError("Module chưa được cấu hình")

        url = f"{self._dich_vu_url}/ho-so"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._khoa}"},
            )
            if r.status_code == 200:
                ds = r.json()
                if not isinstance(ds, list):
                    raise RuntimeError("Danh sách hồ sơ không phải mảng JSON")
                return ds
            elif r.status_code in (401, 403):
                raise RuntimeError(f"Khoá xác thực dịch vụ bị từ chối (HTTP {r.status_code})")
            else:
                raise RuntimeError(f"Dịch vụ trình duyệt trả mã lỗi {r.status_code}")

    async def suc_khoe(self) -> dict[str, Any]:
        if not self._dich_vu_url or not self._khoa:
            return {
                "ok": False,
                "loi": "Chưa cấu hình dịch vụ",
                "khoa": [],
            }
        khoa_che = _che_khoa(self._khoa)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    f"{self._dich_vu_url}/ho-so",
                    headers={"Authorization": f"Bearer {self._khoa}"},
                )
                if r.status_code == 200:
                    return {
                        "ok": True,
                        "khoa": [{"khoa": khoa_che, "ok": True}],
                    }
                return {
                    "ok": False,
                    "loi": f"Dịch vụ trả mã {r.status_code}",
                    "khoa": [{"khoa": khoa_che, "ok": False, "loi": f"HTTP {r.status_code}"}],
                }
        except Exception as e:
            return {
                "ok": False,
                "loi": f"Lỗi kết nối ({type(e).__name__})",
                "khoa": [{"khoa": khoa_che, "ok": False, "loi": "Lỗi kết nối"}],
            }

    async def models(self) -> list[dict]:
        return []

    async def dung(self) -> None:
        pass
