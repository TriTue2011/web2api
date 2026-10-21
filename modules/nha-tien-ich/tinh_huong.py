"""Tình huống nhà — nhận diện nếp sinh hoạt lặp lại từ DỮ LIỆU cảm biến.

Port từ ``services/tinh_huong_nha.py`` (c2a, commit d879b03). Giữ nguyên thuật toán
học (``gom_cua_so``):

- Gom sự kiện theo ô 30 phút, tìm ô lặp lại nhiều ngày (≥3 ngày).
- NGƯỠNG SO VỚI MỨC NỀN (phân vị 25% số nguồn cùng hoạt động), không phải ngưỡng
  tuyệt đối — vì cảm biến hiện diện báo suốt ngày đêm, đòi «lặp ≥3 ngày» suông
  thì cả 48 ô đều qua. Sàn 2: một nguồn đơn độc không là tình huống.
- Tách nguồn LÕI (ổn định: presence/occupancy/person/khuon_mat/yolo/motion)
  khỏi nguồn KÈM THEO (đổi mỗi ngày: state/switch/light/fan).
- Bẫy tự học số 3: ``ghi(..., do_ai=1)`` đánh dấu mẫu do AI tự gây; ``gom_cua_so``
  BỎ các mẫu này khi học (không thì bot tự khẳng định vòng quanh).

Chỉ đổi: kho SQLite của c2a → JSON dưới ``_duong_dan`` (đủ cho quy mô nhà);
``lich_su_nha.doc_cua_so`` → nhật ký nội bộ của module.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import _duong_dan

_TEP = "tinh_huong.json"
_khoa = threading.RLock()
_dl: dict[str, Any] | None = None

_TZ = timezone(timedelta(hours=7))
_O_PHUT = 30
_TOI_THIEU_NGAY = 3
_HON_NEN = 1.5
#: Nguồn LÕI (ổn định) / KÈM THEO — port từ c2a.
_NGUON_LOI = ("presence", "occupancy", "person", "khuon_mat", "yolo", "motion")
_NGUON_KEM = ("state", "switch", "light", "fan")
_SU_KIEN_TOI_DA = 50000


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
        _dl.setdefault("su_kien", [])
        _dl.setdefault("tinh_huong", [])
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


def ghi(thiet_bi: str, gia_tri: str, ts: float | None = None, do_ai: int = 0) -> None:
    """Ghi một sự kiện cảm biến. ``do_ai=1`` = do AI tự gây → học sẽ bỏ qua."""
    ts = float(ts if ts is not None else time.time())
    with _khoa:
        d = _nap()
        d["su_kien"].append({"ts": ts, "thiet_bi": str(thiet_bi),
                             "gia_tri": str(gia_tri), "do_ai": 1 if do_ai else 0})
        if len(d["su_kien"]) > _SU_KIEN_TOI_DA:
            d["su_kien"] = d["su_kien"][-_SU_KIEN_TOI_DA:]
        _luu()


def su_kien(tu: float, den: float, bo_do_ai: bool = True) -> list[dict[str, Any]]:
    sk = [s for s in _nap()["su_kien"] if tu <= float(s["ts"]) < den]
    if bo_do_ai:
        sk = [s for s in sk if not s.get("do_ai")]
    return sk


def _phan_loai(truong: str, thiet_bi: str) -> str:
    """«loi» / «kem» / «» — port từ c2a."""
    t = f"{thiet_bi} {truong}".lower()
    if any(k in t for k in _NGUON_LOI):
        return "loi"
    if any(k in t for k in _NGUON_KEM):
        return "kem"
    return ""


def _mad(xs: list[float]) -> float:
    if not xs:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def gom_cua_so(so_ngay: int = 14, now: float | None = None) -> list[dict[str, Any]]:
    """Học tình huống từ dữ liệu — port thuật toán ``gom_cua_so`` của c2a."""
    now = float(now if now is not None else time.time())
    den = now
    tu = den - max(1, int(so_ngay)) * 86400
    sk = su_kien(tu, den, bo_do_ai=True)  # bẫy 3: bỏ mẫu do AI tự gây
    o: dict[tuple, dict[str, Any]] = {}
    for r in sk:
        try:
            t = datetime.fromtimestamp(float(r["ts"]), _TZ)
        except (TypeError, ValueError, KeyError):
            continue
        tb = str(r.get("thiet_bi") or "")
        loai = _phan_loai("state", tb)
        if not loai:
            continue
        # Chỉ tính lúc BẬT/CÓ, không tính lúc tắt — tình huống là «đang diễn ra».
        gt = str(r.get("gia_tri") or "").lower()
        if gt in ("off", "0", "false", "", "none", "unavailable", "unknown"):
            continue
        khoa = (t.date(), t.hour * 2 + (t.minute // _O_PHUT))
        m = o.setdefault(khoa, {"loi": set(), "kem": set(),
                                "gio": t.hour + t.minute / 60, "thu": t.weekday()})
        m[loai].add(tb)
    theo_o: dict[int, list[dict]] = {}
    for (_ngay, slot), m in o.items():
        if not m["loi"]:
            continue  # không có người → không phải tình huống
        theo_o.setdefault(slot, []).append(m)
    do_o = {slot: statistics.median([len(m["loi"]) + len(m["kem"]) for m in ds])
            for slot, ds in theo_o.items() if ds}
    muc = sorted(do_o.values())
    nen = muc[max(0, len(muc) // 4 - 1)] if muc else 0
    nguong = max(nen * _HON_NEN, 2)  # sàn 2: một nguồn đơn độc không là tình huống
    ra = []
    for slot, ds in theo_o.items():
        if len(ds) < _TOI_THIEU_NGAY:
            continue
        if do_o.get(slot, 0) < nguong:
            continue  # chỉ là mức nền, không phải tình huống
        gio = [m["gio"] for m in ds]
        dem_loi: dict[str, int] = {}
        dem_kem: dict[str, int] = {}
        for m in ds:
            for x in m["loi"]:
                dem_loi[x] = dem_loi.get(x, 0) + 1
            for x in m["kem"]:
                dem_kem[x] = dem_kem.get(x, 0) + 1
        nua = max(1, len(ds) // 2)
        ra.append({
            "slot": slot,
            "gio_tb": round(statistics.median(gio), 3),
            "do_lech": round(max(_mad(gio), 10 / 60), 3),
            "so_ngay": len(ds),
            "loi_cot": sorted(k for k, v in dem_loi.items() if v >= nua),
            "kem_theo": sorted(k for k, v in dem_kem.items() if v >= nua),
        })
    return sorted(ra, key=lambda x: x["gio_tb"])


def hoc(so_ngay: int = 14) -> list[dict[str, Any]]:
    """Học lại tình huống và lưu vào sổ."""
    ra = gom_cua_so(so_ngay)
    with _khoa:
        _nap()["tinh_huong"] = ra
        _luu()
    return ra


def tinh_huong_hien_tai(now: float | None = None) -> list[dict[str, Any]]:
    """Các tình huống đã học mà ô 30 phút HIỆN TẠI rơi vào."""
    now = float(now if now is not None else time.time())
    t = datetime.fromtimestamp(now, _TZ)
    slot = t.hour * 2 + (t.minute // _O_PHUT)
    return [th for th in _nap()["tinh_huong"] if th.get("slot") == slot]
