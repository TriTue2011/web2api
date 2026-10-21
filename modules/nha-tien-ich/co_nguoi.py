"""Có người trong nhà — hợp các khoảng hiện diện từ cảm biến gốc + cảm biến ghép.

Port ý tưởng từ ``services/co_nguoi_nha.py`` (c2a, commit d879b03), giản lược cho
khuôn module (bỏ tầng học chọn cảm biến theo thiết bị — đó là việc của hoc-nha):

- Khoảng «có người» = HỢP các khoảng cảm biến hiện diện báo «on»
  (``binary_sensor`` có ``device_class`` trong ``_LOP_HIEN_DIEN``) + cảm biến
  ghép loại occupancy/presence của module.
- Cảm biến KẸT (báo «on» ≥98% thời gian trong ngày — ``cam_bien.ngay_ket``) thì
  ngày đó coi là «không biết», không phải «có người» (port cam_bien_ket).
- Hàng rào 6: cảm biến ``unavailable``/``unknown`` KHÔNG được coi là «vắng» —
  quãng không rõ thì trả «không rõ», không trả «vắng».
- Bẫy tự học số 1: ``co_nguoi_tai(moc)`` dựng lại giá trị TẠI mốc quá khứ từ
  chuỗi sự kiện (``do_tin.chuoi_tai_moc``), không lấy trạng thái hiện tại.
"""
from __future__ import annotations

import time
from typing import Any

from . import cam_bien, co_che, do_tin, hang_rao

#: Cảm biến không đổi trạng thái ngần này giây sau lần «on» cuối thì coi là đã vắng.
MAT_DAU_GIAY = 600.0
#: Người vừa rời đi trong ngần này giây vẫn coi là «mới vắng» (chờ quay lại).
ROI_GIAY = 60.0
#: Lớp hiện diện của binary_sensor — port ``kich_hoat_nha._LOP_HIEN_DIEN`` (ý tưởng).
_LOP_HIEN_DIEN = frozenset({"presence", "occupancy", "motion"})


def _cam_bien_hien_dien() -> list[dict[str, Any]]:
    """Các cảm biến hiện diện đang biết (gốc HA + ghép của module)."""
    ra: list[dict[str, Any]] = []
    try:
        for s in co_che.doc_trang_thai() or []:
            eid = str(s.get("entity_id") or "")
            at = s.get("attributes") or {}
            if eid.split(".", 1)[0] == "binary_sensor" and str(at.get("device_class") or "") in _LOP_HIEN_DIEN:
                ra.append({"ma": eid, "trang_thai": str(s.get("state") or "").lower()})
    except Exception:
        pass
    for g in cam_bien.hien_tai():
        if (g.get("attributes") or {}).get("device_class") in ("occupancy", "presence"):
            ra.append({"ma": g["entity_id"], "trang_thai": str(g.get("state") or "").lower()})
    return ra


def hien_tai() -> dict[str, Any]:
    """Có người trong nhà lúc này không? Trả {co_nguoi: True/False/None}.

    None = «không rõ» (mọi cảm biến đều mất kết nối) — KHÔNG được coi là vắng
    (hàng rào 6: unavailable không phải là tắt/vắng để kích hoạt hành động).
    """
    cb = _cam_bien_hien_dien()
    if not cb:
        return {"co_nguoi": None, "ly_do": "không có cảm biến hiện diện nào"}
    ro = [c for c in cb if hang_rao.la_trang_thai_khong_ro(c["trang_thai"])]
    if len(ro) == len(cb):
        return {"co_nguoi": None, "ly_do": "mọi cảm biến đều mất kết nối (unavailable/unknown)"}
    on = [c["ma"] for c in cb if c["trang_thai"] == "on"]
    return {"co_nguoi": bool(on), "cam_bien_on": on,
            "cam_bien_khong_ro": [c["ma"] for c in ro]}


def co_nguoi_tai(su_kien: list[tuple[float, str, str]], moc: float) -> dict[str, Any]:
    """Có người TẠI mốc quá khứ ``moc`` không — dựng từ chuỗi sự kiện.

    Bẫy tự học số 1 của c2a: không bao giờ lấy trạng thái HIỆN TẠI để trả lời
    câu hỏi về quá khứ (rò rỉ tương lai). ``su_kien`` = [(ts, ma, gia_tri)].
    """
    cb_ma = {c["ma"] for c in _cam_bien_hien_dien()}
    on: list[str] = []
    ro = False
    for ma in cb_ma:
        sk = [(ts, gt) for ts, m, gt in su_kien if m == ma]
        gt = do_tin.chuoi_tai_moc(sk, moc)
        if gt is None:
            continue
        if hang_rao.la_trang_thai_khong_ro(gt):
            ro = True
        elif gt == "on":
            on.append(ma)
    if on:
        return {"co_nguoi": True, "cam_bien_on": on}
    if ro:
        return {"co_nguoi": None, "ly_do": "cảm biến mất kết nối tại mốc hỏi"}
    return {"co_nguoi": False}


def khoang_vang(su_kien: list[tuple[float, str, str]], tu: float, den: float) -> list[dict[str, Any]]:
    """Các quãng VẮNG (không có người) trong [tu, den), từ chuỗi sự kiện.

    Ngày cảm biến kẹt (``cam_bien.ngay_ket``) bị bỏ khỏi tính toán — coi là
    «không biết», không phải «có người» (port cam_bien_ket).
    """
    cb_ma = sorted({c["ma"] for c in _cam_bien_hien_dien()})
    if not cb_ma:
        return []
    # Dựng chuỗi on/off cho từng cảm biến, bỏ ngày kẹt.
    chuoi_cb: dict[str, list[tuple[float, str]]] = {}
    for ma in cb_ma:
        sk = sorted([(ts, gt) for ts, m, gt in su_kien if m == ma], key=lambda x: x[0])
        if not sk:
            continue
        ts_l = [t for t, _ in sk]
        gt_l = [g for _, g in sk]
        ts_l, gt_l = cam_bien.bo_ket(ts_l, gt_l, tu, den)
        chuoi_cb[ma] = [(t, str(g or "").lower()) for t, g in zip(ts_l, gt_l) if g is not None]
    # Quét mốc: vắng = không cảm biến nào đang on và không có cảm biến không rõ.
    moc_set = sorted({tu, den} | {t for ch in chuoi_cb.values() for t, _ in ch
                                 if tu <= t <= den})
    ra: list[dict[str, Any]] = []
    bat_dau: float | None = None
    for i, moc in enumerate(moc_set):
        het = moc_set[i + 1] if i + 1 < len(moc_set) else den
        trang = {ma: do_tin.chuoi_tai_moc([(t, g) for t, g in ch], moc) for ma, ch in chuoi_cb.items()}
        co_on = any(g == "on" for g in trang.values())
        co_ro = any(g is not None and hang_rao.la_trang_thai_khong_ro(g) for g in trang.values())
        vang = not co_on and not co_ro
        if vang and bat_dau is None:
            bat_dau = moc
        elif not vang and bat_dau is not None:
            ra.append({"tu": bat_dau, "den": moc})
            bat_dau = None
    if bat_dau is not None:
        ra.append({"tu": bat_dau, "den": den})
    return ra
