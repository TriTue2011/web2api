"""Kịch bản nhà — thử khô mặc định, xác nhận quản trị, chống lặp, chặn thiết bị nguy hiểm.

Port ý tưởng từ ``services/kich_ban_nha.py`` (sổ kịch bản: mỗi thiết bị một bản
«việc đang được cài làm», bot dựng tình huống rồi hỏi) và ``services/kich_hoat_nha.py``
(c2a, commit d879b03) — đặc biệt:

- ``_duoc_tu_lam`` → ở đây THẮT CHẶT thành hàng rào 1: mọi hành động GHI đều
  MẶC ĐỊNH THỬ KHÔ (``xem_truoc`` trả «sẽ làm gì»), chỉ chạy khi có xác nhận từ
  vai quản trị (``chay(..., xac_nhan=True)`` + ``co_che.la_quan_tri()``).
- ``NGHI_LAP`` (chốt chống nhấp nháy/vòng kích hoạt) → ``hang_rao.ChongLap``
  (dấu vết chuỗi gốc + giới hạn số lần/cửa sổ), test bằng kịch bản vòng thật.
- «Khoá cửa / bếp / bình nóng lạnh không bao giờ tự làm» (CLAUDE.md c2a) →
  hàng rào 2: ``hang_rao.la_thuc_the_nguy_hiem`` theo LOẠI do home-assistant báo
  (domain/device_class), không theo tên; loại lạ chưa biết ⇒ nguy hiểm.

Chỉ đổi lớp lưu trữ (``_duong_dan``) và lớp gọi thiết bị (``co_che`` — qua lõi
chuẩn C1, không import trực tiếp home-assistant).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from . import _duong_dan, co_che, hang_rao

_TEP = "kich_ban.json"
_khoa = threading.RLock()
_chong_lap = hang_rao.ChongLap()
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
        _dl.setdefault("kich_ban", {})
        _dl.setdefault("nhat_ky", [])
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
    global _dl, _chong_lap
    with _khoa:
        _dl = None
        _chong_lap = hang_rao.ChongLap()


def dinh_nghia(kb_id: str, ten: str, buoc: list[dict[str, Any]], mo_ta: str = "") -> dict[str, Any]:
    """Định nghĩa một kịch bản. Mỗi bước: {thuc_the, hanh_dong, tham_so?}."""
    kb_id = str(kb_id or "").strip()
    if not kb_id or len(kb_id) > 60:
        raise ValueError("Mã kịch bản 1–60 ký tự.")
    if not isinstance(buoc, list) or not buoc:
        raise ValueError("Kịch bản cần ít nhất một bước.")
    for b in buoc:
        if not isinstance(b, dict) or not b.get("thuc_the") or not b.get("hanh_dong"):
            raise ValueError("Mỗi bước cần {thuc_the, hanh_dong}.")
    with _khoa:
        _nap()["kich_ban"][kb_id] = {
            "ten": str(ten or kb_id)[:80], "mo_ta": str(mo_ta or "")[:500],
            "buoc": [{"thuc_the": str(b["thuc_the"]), "hanh_dong": str(b["hanh_dong"]),
                      "tham_so": dict(b.get("tham_so") or {})} for b in buoc],
            "tao_luc": time.time(),
        }
        _luu()
    return lay(kb_id) or {}


def xoa(kb_id: str) -> bool:
    with _khoa:
        co = _nap()["kich_ban"].pop(str(kb_id), None) is not None
        if co:
            _luu()
    return co


def lay(kb_id: str) -> dict[str, Any] | None:
    kb = _nap()["kich_ban"].get(str(kb_id))
    return dict(kb) if kb else None


def danh_sach() -> list[dict[str, Any]]:
    return [{"id": k, **v} for k, v in _nap()["kich_ban"].items()]


def _thuc_the_map() -> dict[str, dict[str, Any]]:
    """entity_id → {domain, device_class} từ nguồn đọc đã tiêm (HA giả trong test)."""
    ra: dict[str, dict[str, Any]] = {}
    try:
        for s in co_che.doc_trang_thai() or []:
            eid = str(s.get("entity_id") or "")
            at = s.get("attributes") or {}
            ra[eid] = {"domain": eid.split(".", 1)[0] if "." in eid else "",
                       "device_class": str(at.get("device_class") or "")}
    except Exception:
        pass
    return ra


def xem_truoc(kb_id: str) -> dict[str, Any]:
    """THỬ KHÔ (hàng rào 1): trả «sẽ làm gì» cho từng bước, KHÔNG chạy gì cả.

    Bước nào chạm thực thể nguy hiểm (hàng rào 2) thì đánh dấu ``bi_chan`` —
    kể cả khi chạy thật có xác nhận, bước đó cũng KHÔNG BAO GIỜ chạy.
    """
    kb = lay(kb_id)
    if kb is None:
        return {"ok": False, "loi": "khong_thay_kich_ban"}
    tmap = _thuc_the_map()
    se_lam: list[dict[str, Any]] = []
    for b in kb["buoc"]:
        eid = b["thuc_the"]
        info = tmap.get(eid, {})
        nguy = hang_rao.la_thuc_the_nguy_hiem(info.get("domain", ""), info.get("device_class", ""))
        se_lam.append({**b, "bi_chan": nguy,
                       "ly_do_chan": "Thực thể loại nguy hiểm (khoá/bếp/bình nóng lạnh/…) — "
                                     "không bao giờ tự chạy trong kịch bản." if nguy else ""})
    return {"ok": True, "id": kb_id, "ten": kb["ten"], "se_lam": se_lam,
            "che_do": "thu_kho"}


def chay(kb_id: str, xac_nhan: bool = False, hang_rao_bat: bool = True) -> dict[str, Any]:
    """Chạy kịch bản.

    Hàng rào 1: ``hang_rao_bat=True`` (mặc định) thì BẮT BUỘC ``xac_nhan=True``
    TỪ VAI QUẢN TRỊ — thiếu thì trả ``can_xac_nhan`` kèm bản thử khô, không chạy
    bước nào. ``hang_rao_bat=False`` (nút tắt toàn bộ, chỉ dùng khi có người
    giám sát trực tiếp) thì chạy ngay — nhưng hàng rào 2 (chặn thiết bị nguy
    hiểm), 3 (chống lặp), 6 (unavailable) VẪN BẬT.
    """
    kb = lay(kb_id)
    if kb is None:
        return {"ok": False, "loi": "khong_thay_kich_ban"}
    if hang_rao_bat and not (xac_nhan and co_che.la_quan_tri()):
        xt = xem_truoc(kb_id)
        return {"ok": False, "loi": "can_xac_nhan",
                "chi_tiet": "Kịch bản chỉ chạy khi có xác nhận từ vai quản trị "
                            "(hàng rào 1: thử khô mặc định).",
                "xem_truoc": xt.get("se_lam", [])}
    with _khoa:
        vao = _chong_lap.vao(kb_id)
        if not vao["ok"]:
            return {"ok": False, **vao}
        try:
            return _chay_buoc(kb_id, kb)
        finally:
            _chong_lap.ra(kb_id)


def _chay_buoc(kb_id: str, kb: dict[str, Any]) -> dict[str, Any]:
    tmap = _thuc_the_map()
    ket_qua: list[dict[str, Any]] = []
    for b in kb["buoc"]:
        eid = b["thuc_the"]
        info = tmap.get(eid, {})
        # Hàng rào 2 — TUYỆT ĐỐI: không bao giờ tự chạy thiết bị nguy hiểm,
        # kể cả kịch bản khai «tự động» hay đã có xác nhận.
        if hang_rao.la_thuc_the_nguy_hiem(info.get("domain", ""), info.get("device_class", "")):
            ket_qua.append({**b, "ok": False, "loi": "bi_chan_nguy_hiem",
                            "chi_tiet": f"«{eid}» là thực thể loại nguy hiểm "
                                        "(khoá/bếp/bình nóng lạnh/… theo loại do HA báo) — "
                                        "kịch bản không bao giờ được tự chạy nó."})
            continue
        # Hàng rào 6: thực thể mất kết nối thì BỎ QUA, không coi là tắt để hành động.
        tt = {str(s.get("entity_id")): s.get("state") for s in (co_che.doc_trang_thai() or [])}
        if hang_rao.la_trang_thai_khong_ro(tt.get(eid)):
            ket_qua.append({**b, "ok": False, "loi": "bo_qua_khong_ro",
                            "chi_tiet": f"«{eid}» đang mất kết nối (unavailable/unknown) — "
                                        "không coi là tắt để kích hoạt hành động (hàng rào 6)."})
            continue
        kq = co_che.goi_hanh_dong(eid, b["hanh_dong"], b.get("tham_so") or {})
        ket_qua.append({**b, **kq})
    with _khoa:
        _nap()["nhat_ky"].append({"kich_ban": kb_id, "luc": time.time(), "ket_qua": ket_qua})
        _nap()["nhat_ky"] = _nap()["nhat_ky"][-200:]
        _luu()
    return {"ok": True, "id": kb_id, "ket_qua": ket_qua,
            "that_bai": sum(1 for k in ket_qua if not k.get("ok"))}
