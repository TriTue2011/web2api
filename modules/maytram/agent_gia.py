"""Agent GIẢ phía máy trạm — dùng cho test và mô tả giao thức (NV170).

Đối xứng với phien_agent.py: thực hiện ĐÚNG phía agent của bắt tay HMAC hai chiều
(xác thực `xac_thuc_loi` của lõi bằng khoá mình giữ — lõi giả mạo thì từ chối nối),
rồi vòng nhận lệnh: kiểm MAC từng khung, kiểm quyền và allowlist CỦA RIÊNG MÌNH
(hai lớp trùng nhau như c2a: «agent tự giữ allowlist, KHÔNG tin lệnh từ gateway»),
trả kết quả đã ký MAC từ bảng `ket_qua_gia`.

Tham số test:
- thoi_gian_lech: giây lệch đồng hồ để kiểm cửa sổ thời gian của lõi.
- pha_mac: ký sai MAC kết quả (kiểm lõi bỏ khung giả).
- treo_lenh: nhận lệnh nhưng không trả (kiểm hết giờ của lõi).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from . import phien_agent as pa


class AgentGia:
    """Agent giả: giữ khoá, allowlist và quyền của RIÊNG MÌNH."""

    def __init__(self, khoa_hex: str, duong_dan: list, quyen: dict,
                 ket_qua_gia: dict | None = None, *,
                 ten: str = "may-gia-1",
                 thoi_gian_lech: float = 0.0, pha_mac: bool = False,
                 treo_lenh: bool = False, dap_sai: bool = False):
        self._khoa = bytes.fromhex(khoa_hex)
        self._duong_dan = [str(x) for x in duong_dan]
        self._quyen = dict(quyen or {})
        self._ket_qua_gia = dict(ket_qua_gia or {})
        self._ten = ten
        self._lech = thoi_gian_lech
        self._pha_mac = pha_mac
        self._treo = treo_lenh
        self._dap_sai = dap_sai

    @property
    def ten(self) -> str:
        return self._ten

    @ten.setter
    def ten(self, v: str) -> None:
        self._ten = v

    async def chay(self, kenh) -> None:
        import time
        nonce_agent = secrets.token_hex(16)
        await kenh.gui({"loai": "chao", "ten": self._ten,
                        "nonce_agent": nonce_agent,
                        "thoi_gian": time.time() + self._lech})
        # Kiểm thách thức của LÕI (xác thực hai chiều, phía agent):
        thach = await kenh.nhan()
        if not isinstance(thach, dict) or thach.get("loai") != "thach_thuc":
            raise RuntimeError("lõi không gửi thách thức đúng khuôn")
        nonce_loi = str(thach.get("nonce_loi") or "")
        mong = pa._hmac_hex(self._khoa, b"loi:", nonce_agent.encode(), b":",
                            nonce_loi.encode())
        if not hmac.compare_digest(str(thach.get("xac_thuc_loi") or ""), mong):
            raise RuntimeError("lõi GIẢ MẠO — xac_thuc_loi không khớp khoá")
        dap = pa._hmac_hex(self._khoa, b"agent:", nonce_agent.encode(), b":",
                           nonce_loi.encode())
        if self._dap_sai:
            dap = "00" * 32  # agent bị sửa: chứng minh sai (kiểm lõi phải từ chối)
        await kenh.gui({"loai": "dap", "xac_thuc_agent": dap,
                        "thong_tin": {"nen_tang": "gia", "ten_may": "may-gia",
                                      "phien_ban": "0-test"}})
        khoa_phien = bytes.fromhex(pa._hmac_hex(
            self._khoa, b"phien:", nonce_agent.encode(), b":",
            nonce_loi.encode()))
        san_sang = await kenh.nhan()
        if not isinstance(san_sang, dict) or san_sang.get("loai") != "san_sang":
            raise RuntimeError("không nhận được san_sang")
        # Vòng lệnh
        while True:
            khung = await kenh.nhan()
            await self._xu_ly_lenh(kenh, khoa_phien, khung)

    async def _xu_ly_lenh(self, kenh, khoa_phien: bytes, khung: dict) -> None:
        if not isinstance(khung, dict):
            return
        rid = str(khung.get("id") or "")
        lenh = str(khung.get("lenh") or "")
        doi_so = khung.get("doi_so") if isinstance(khung.get("doi_so"), dict) else {}
        mong = pa._hmac_hex(khoa_phien, rid.encode(), b":", lenh.encode(), b":",
                            pa._json_chuan(doi_so))
        if not hmac.compare_digest(str(khung.get("mac") or ""), mong):
            return  # khung giả — agent thật cũng bỏ
        if self._treo:
            return  # không trả gì — kiểm hết giờ phía lõi
        ket_qua = self._dap_lenh(lenh, doi_so)
        if "loi" in ket_qua:
            tai = {"loi": str(ket_qua["loi"])[:500]}
        else:
            tai = ket_qua
        dia_chi = "ket_qua" if "loi" not in ket_qua else None
        khung_ve = {"id": rid}
        if dia_chi:
            khung_ve["ket_qua"] = tai
            khung_ve["mac"] = pa._hmac_hex(khoa_phien, rid.encode(), b":",
                                           pa._json_chuan(tai))
        else:
            khung_ve["loi"] = tai["loi"]
            khung_ve["mac"] = pa._hmac_hex(khoa_phien, rid.encode(), b":",
                                           pa._json_chuan(tai))
        if self._pha_mac:
            khung_ve["mac"] = "ff" * 32
        await kenh.gui(khung_ve)

    def _dap_lenh(self, lenh: str, doi_so: dict) -> dict:
        # Lớp 2 của agent: kiểm quyền + allowlist của RIÊNG MÌNH.
        if lenh in pa.LENH_GHI and not self._quyen.get("ghi"):
            return {"loi": "agent: chưa bật quyền ghi trên máy này"}
        if lenh in pa.LENH_NGUON and not self._quyen.get("nguon"):
            return {"loi": "agent: chưa bật quyền nguồn trên máy này"}
        if lenh in pa.LENH_GHI_MAN_HINH and not self._quyen.get("ghi_man_hinh"):
            return {"loi": "agent: chưa bật quyền ghi màn hình trên máy này"}
        if lenh not in pa.LENH_DONG and lenh not in pa.LENH_NOI_BO:
            return {"loi": f"agent: lệnh «{lenh}» không trong tập cho phép"}
        for truong in pa.TRUONG_DUONG_DAN:
            p = doi_so.get(truong)
            if p and not pa.duong_dan_duoc_phep(self._duong_dan, str(p)):
                return {"loi": f"agent: «{p}» ngoài phạm vi của máy này"}
        if lenh in self._ket_qua_gia:
            return dict(self._ket_qua_gia[lenh])
        return {"ok": True, "lenh": lenh, "da_nhan_doi_so": sorted(doi_so)}
