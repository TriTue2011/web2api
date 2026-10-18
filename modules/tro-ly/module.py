"""Module tro-ly — NV136 G1.5: bộ dựng prompt theo ngân sách, bộ chọn công cụ
theo tầng, X-quang prompt và sổ lỗi công cụ.

Module này CHƯA có vòng lặp gọi mô hình (đó là NV135, đang làm song song):
nó cung cấp các khối xây dựng thuần túy để NV135 (hoặc lõi sau này) gọi:
- `dung_prompt(...)`: dựng prompt từ các mảnh theo ngân sách (A, B).
- `tim_cong_cu(...)`: chọn công cụ theo tầng qua DANH MỤC (C, D).
- `do_chong_cheo(...)` + cảnh báo trong `suc_khoe()` (E).
- `ghi_x_quang(...)` / `doc_x_quang(...)` có phân quyền theo hồ sơ (F).
- `ghi_loi(...)` / `manh_so_loi(...)` — sổ lỗi theo hồ sơ (G).

Không gọi mạng, không thêm gói, thuần thư viện chuẩn.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

from . import chon_cong_cu as _cc
from . import manh_prompt as _mp
from . import so_loi as _sl
from . import x_quang as _xq


class Module:
    """Khối xây dựng prompt/công cụ cho vòng lặp agent (NV135 dùng)."""

    def __init__(self, manifest, _thu_muc_du_lieu: str | None = None):
        self.manifest = manifest
        self._du_lieu = Path(_thu_muc_du_lieu) if _thu_muc_du_lieu else None
        self._bo_dung = _mp.BoDungPrompt()
        self._nk: _xq.NhatKyXQuang | None = None
        self._so_loi: _sl.SoLoi | None = None
        self._cap_chong_cheo_da_bao: set[tuple[str, str]] = set()

    # ── nền tảng ──────────────────────────────────────────────────────
    def nap(self, cau_hinh: dict) -> None:
        goc = self._du_lieu
        if goc is None:
            goc = Path("/tmp/tro-ly-nv136")
        goc.mkdir(parents=True, exist_ok=True)
        self._nk = _xq.NhatKyXQuang(goc / "x_quang.jsonl")
        self._so_loi = _sl.SoLoi(goc / "so_loi.json")

    async def cai(self, cau_hinh: dict) -> dict:
        if not isinstance(cau_hinh, dict):
            return {"ok": False, "ket_qua_tung_khoa": {}, "loi": "cau_hinh_sai",
                    "cau_hinh_luu": {}}
        return {"ok": True, "ket_qua_tung_khoa": {}, "loi": None, "cau_hinh_luu": {}}

    def suc_khoe(self) -> dict:
        return {"ok": True, "canh_bao_chong_cheo": len(self._cap_chong_cheo_da_bao),
                "prompt_nen_token": _mp.uoc_tinh_token(_mp.PROMPT_NEN_TOI_GIAN)}

    def dung(self) -> None:
        return None

    # ── A/B: dựng prompt ──────────────────────────────────────────────
    def manh_nen(self) -> _mp.ManhPrompt:
        """Mảnh prompt nền tối giản (B): danh tính + quy tắc cốt lõi."""
        return _mp.ManhPrompt(id="nen", nguon="loi", tang="on_dinh", uu_tien=100,
                              noi_dung=_mp.PROMPT_NEN_TOI_GIAN,
                              tran_token=_mp.TRAN_TOKEN_PROMPT_NEN,
                              dieu_kien_nap=None)

    def dung_prompt(self, manhs: list[_mp.ManhPrompt], *, ngan_sach_tong: int,
                    ngan_sach_tang: dict, boi_canh: dict,
                    cong_cu_da_chon: list[str] | None = None,
                    danh_muc: list[dict] | None = None) -> tuple[str, dict]:
        return self._bo_dung.dung(manhs, ngan_sach_tong=ngan_sach_tong,
                                  ngan_sach_tang=ngan_sach_tang, boi_canh=boi_canh,
                                  cong_cu_da_chon=cong_cu_da_chon, danh_muc=danh_muc)

    def tien_to_on_dinh(self, van_ban: str, x_quang: dict) -> str:
        return self._bo_dung.tien_to_on_dinh(van_ban, x_quang)

    # ── C/D: chọn công cụ ─────────────────────────────────────────────
    def tim_cong_cu(self, y_dinh: str, danh_muc: list[dict], **kw) -> dict:
        return _cc.tim_cong_cu(y_dinh, danh_muc, **kw)

    def chon_cong_cu(self, y_dinh: str, ung_vien: list[dict], **kw) -> dict:
        return _cc.chon_cong_cu(y_dinh, ung_vien, **kw)

    def cong_cu_nen(self) -> list[dict]:
        return [dict(c) for c in _cc.CONG_CU_NEN]

    # ── E: chồng chéo ─────────────────────────────────────────────────
    def do_chong_cheo(self, ds_cong_cu: list[dict]) -> list[tuple[str, str, float]]:
        cap = _cc.do_chong_cheo(ds_cong_cu)
        for a, b, _d in cap:
            khoa = (a, b) if a < b else (b, a)
            if khoa not in self._cap_chong_cheo_da_bao:
                self._cap_chong_cheo_da_bao.add(khoa)
                log.warning("tro-ly: công cụ %s và %s mô tả chồng chéo", a, b)
        return cap

    # ── F: X-quang ────────────────────────────────────────────────────
    def ghi_x_quang(self, *, luot_id: str, ho_so_id: str, x_quang: dict,
                    cong_cu_schema: list[str], cong_cu_danh_muc: list[str],
                    **them) -> None:
        if self._nk is None:
            self.nap({})
        assert self._nk is not None
        self._nk.ghi(luot_id=luot_id, ho_so_id=ho_so_id, x_quang=x_quang,
                     cong_cu_schema=cong_cu_schema,
                     cong_cu_danh_muc=cong_cu_danh_muc, **them)

    def doc_x_quang(self, *, ho_so_id: str, nguoi_hoi_ho_so_id: str,
                    luot_id: str | None = None) -> list[dict]:
        """Đọc X-quang có phân quyền theo hồ sơ (IDOR: sai hồ sơ → PermissionError)."""
        if self._nk is None:
            self.nap({})
        assert self._nk is not None
        return self._nk.doc(ho_so_id=ho_so_id,
                            nguoi_hoi_ho_so_id=nguoi_hoi_ho_so_id, luot_id=luot_id)

    # ── G: sổ lỗi ─────────────────────────────────────────────────────
    def ghi_loi(self, ho_so_id: str, cong_cu: str, loai_loi: str, tom_tat: str) -> None:
        if self._so_loi is None:
            self.nap({})
        assert self._so_loi is not None
        self._so_loi.ghi(ho_so_id, cong_cu, loai_loi, tom_tat)

    def manh_so_loi(self, ho_so_id: str, cong_cu: str) -> _mp.ManhPrompt | None:
        if self._so_loi is None:
            self.nap({})
        assert self._so_loi is not None
        noi_dung = self._so_loi.manh_hay_doi(ho_so_id, cong_cu)
        if not noi_dung:
            return None
        return _mp.ManhPrompt(id=f"so_loi:{cong_cu}", nguon="bo_nho",
                              tang="hay_doi", uu_tien=20, noi_dung=noi_dung,
                              tran_token=300, dieu_kien_nap=None)
