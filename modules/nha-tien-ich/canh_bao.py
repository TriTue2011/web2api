"""Cảnh báo nhà — chống dội, nhịp báo lại thưa dần, lọc nội dung nhạy cảm.

Port từ ``services/canh_bao_nha.py`` (c2a, commit d879b03). Giữ nguyên thuật toán:

- Chống dội: gom cảnh báo cùng thiết bị + loại trong ``DOI_GOM_GIAY`` (300s) —
  tin trùng trong cửa sổ gom thì không gửi lại, chỉ đếm.
- Nhịp báo lại thưa dần ``_NHIP = (5p, 30p, 60p, 6h, 24h)``: cùng một vấn đề còn
  tồn tại thì báo lại mỗi lúc một thưa, không spam.
- Mức độ rõ: thap / trung_binh / cao / khan_cap.
- Không gửi nội dung nhạy cảm (tên người, vị trí chính xác) ra kênh công cộng —
  ``hang_rao.loc_nhay_cam``.

Chỉ đổi: lớp lưu trữ (``_duong_dan``), kênh gửi (``co_che`` — module không import
trực tiếp kênh nào; tin đi qua hàng đợi nội bộ, tầng module.py quyết định gửi).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import _duong_dan, hang_rao

_TEP = "canh_bao.json"
_khoa = threading.RLock()
_dl: dict[str, Any] | None = None


def _duong() -> Any:
    return _duong_dan.tep(_TEP)


def _nap() -> dict[str, Any]:
    global _dl
    if _dl is None:
        try:
            _dl = json.loads(_duong().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _dl = {}
        if not isinstance(_dl, dict):
            _dl = {}
        _dl.setdefault("dang_bao", {})   # khoa -> ban ghi dang bao
        _dl.setdefault("lich_su", [])
    return _dl


def _luu() -> None:
    p = _duong()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_nap(), ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import os
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def dat_lai() -> None:
    global _dl
    with _khoa:
        _dl = None


def _khoa_loi(khoa_tb: str, loai: str) -> str:
    """Khoá chống dội: thiết bị + loại — port ``_khoa_loi`` của c2a."""
    return f"{khoa_tb}::{loai}"


def gui(khoa_tb: str, loai: str, muc_do: str, noi_dung: str,
        now: float | None = None) -> dict[str, Any]:
    """Gửi (hoặc gom) một cảnh báo.

    Trả ``{"ok": True, "trang_thai": "da_gui" | "da_gom" | "bao_lai", ...}``.
    ``da_gom`` = trùng trong cửa sổ chống dội → không gửi lại, chỉ đếm.
    ``bao_lai`` = vấn đề còn tồn tại, tới nhịp báo lại → gửi nhắc thưa dần.
    Nội dung luôn qua ``loc_nhay_cam`` trước khi ra ngoài.
    """
    now = float(now if now is not None else time.time())
    muc_do = str(muc_do or "").strip().lower()
    if muc_do not in hang_rao.MUC_DO:
        return {"ok": False, "loi": "muc_do_sai",
                "chi_tiet": f"Mức độ phải một trong {list(hang_rao.MUC_DO)}."}
    nd = hang_rao.loc_nhay_cam(noi_dung)
    khoa = _khoa_loi(str(khoa_tb), str(loai))
    with _khoa:
        d = _nap()
        bg = d["dang_bao"].get(khoa)
        if bg is not None:
            # Chống dội: cùng thiết bị + loại trong cửa sổ gom → chỉ đếm.
            if now - float(bg.get("luc_gui", 0)) < hang_rao.DOI_GOM_GIAY:
                bg["so_lan"] = int(bg.get("so_lan", 1)) + 1
                _luu()
                return {"ok": True, "trang_thai": "da_gom", "khoa": khoa,
                        "so_lan": bg["so_lan"], "noi_dung": nd}
            # Nhịp báo lại thưa dần — port _NHIP của c2a.
            lan = int(bg.get("lan_bao_lai", 0))
            nhip = hang_rao.NHIP_BAO_LAI[min(lan, len(hang_rao.NHIP_BAO_LAI) - 1)]
            if now - float(bg.get("luc_gui", 0)) < nhip:
                return {"ok": True, "trang_thai": "cho_nhip", "khoa": khoa,
                        "bao_lai_sau_giay": round(nhip - (now - float(bg["luc_gui"])), 1),
                        "noi_dung": nd}
            bg["luc_gui"] = now
            bg["lan_bao_lai"] = lan + 1
            bg["so_lan"] = int(bg.get("so_lan", 1)) + 1
            _luu()
            return {"ok": True, "trang_thai": "bao_lai", "khoa": khoa,
                    "lan_bao_lai": bg["lan_bao_lai"], "noi_dung": nd, "muc_do": muc_do}
        d["dang_bao"][khoa] = {"khoa_tb": str(khoa_tb), "loai": str(loai),
                               "muc_do": muc_do, "noi_dung": nd,
                               "luc_gui": now, "lan_bao_lai": 0, "so_lan": 1}
        d["lich_su"].append({"khoa": khoa, "muc_do": muc_do, "luc": now})
        d["lich_su"] = d["lich_su"][-500:]
        _luu()
        return {"ok": True, "trang_thai": "da_gui", "khoa": khoa,
                "noi_dung": nd, "muc_do": muc_do}


def het(khoa_tb: str, loai: str) -> bool:
    """Vấn đề đã hết → xoá khỏi danh sách đang báo (để lần sau báo lại từ đầu)."""
    khoa = _khoa_loi(str(khoa_tb), str(loai))
    with _khoa:
        co = _nap()["dang_bao"].pop(khoa, None) is not None
        if co:
            _luu()
    return co


def dang_bao() -> list[dict[str, Any]]:
    return list(_nap()["dang_bao"].values())


def lich_su() -> list[dict[str, Any]]:
    return list(_nap()["lich_su"])
