"""Cảm biến ghép — tính một cảm biến nhị phân từ cảm biến gốc bằng biểu thức VÀ / HOẶC / KHÔNG.

Port từ ``services/cam_bien_ghep.py`` (c2a, commit d879b03) + ``services/cam_bien_ket.py``
(cảm biến kẹt theo từng ngày). Chỉ đổi lớp lưu trữ/kết nối:

- ``DATA_DIR/agent/cam_bien_ghep.json`` → ``_duong_dan.tep("cam_bien_ghep.json")``
  (dưới ``WEB2API_DATA/modules/nha-tien-ich``, thư mục 0700/tệp 0600).
- ``ha_client.get_states()`` → ``co_che.doc_trang_thai()`` (tiêm trong test; qua lõi khi chạy thật).
- Tiền tố mã: ``binary_sensor.c2a_`` → ``nhatienich.ghep_`` (thực thể do module này
  khai qua chuẩn C1, ``tien_to`` = ``nhatienich``).
- ``chuoi()`` nhận danh sách sự kiện ``[(ts, thiet_bi, gia_tri)]`` thay vì
  connection SQLite của c2a (kho lịch sử của module nằm trong ``tinh_huong.py``).
- Nút ``khoang_cach`` (vùng radar học từ ``vung_khoang_cach`` của c2a — KHÔNG port,
  vì tầng học vùng thuộc NV146/hoc-nha): khi không có số đo thì coi là «không đo
  được» = không khớp (an toàn, không khẳng định sai).

Vì sao ghép trong module chứ không tạo cảm biến mẫu trong HA (lý do gốc của c2a,
giữ nguyên): bot học từ lịch sử, cảm biến mới trong HA không có lịch sử nào —
cảm biến ghép dựng lại được trọn vẹn quá khứ từ lịch sử cảm biến gốc (``chuoi``).
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import _duong_dan, co_che

_TEP = "cam_bien_ghep.json"
TIEN_TO = "nhatienich.ghep_"
_TIEN_TO_RE = re.compile(re.escape(TIEN_TO) + r"[a-z0-9_]+")
_MA_RE = re.compile(r"[a-z_]+\.[a-z0-9_]+")

_khoa = threading.RLock()
_dl: dict[str, Any] | None = None
#: Giá trị cuối đã báo cho bộ kích hoạt (chỉ báo khi ĐỔI) — port từ c2a.
_cuoi: dict[str, str] = {}

_TZ = timezone(timedelta(hours=7))
#: Ngày theo GIỜ NHÀ (port cam_bien_ket: CI chạy UTC thì «ngày» lệch 7 tiếng).
_KET = 0.98
_BAT_TAT = frozenset({"on", "off"})


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
        _dl.setdefault("cam_bien", {})
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
    """Dùng trong test: quên bộ nhớ đệm (đổi WEB2API_DATA)."""
    global _dl
    with _khoa:
        _dl = None
        _cuoi.clear()


def ds() -> dict[str, dict[str, Any]]:
    with _khoa:
        return {k: dict(v) for k, v in _nap()["cam_bien"].items()}


def la_ghep(ma: str) -> bool:
    return str(ma or "") in _nap()["cam_bien"]


def _chuan(bt: Any) -> dict[str, Any]:
    """Chuẩn hoá biểu thức: mã thực thể trần (str) = viết tắt của ``{"ma": mã}``."""
    if isinstance(bt, str):
        return {"ma": bt}
    return bt


def _kiem(bt: Any, sau: int = 0) -> None:
    """Kiểm biểu thức — port nguyên từ c2a (độ sâu tối đa 8).

    Mở rộng tương thích: nút thực thể có thể viết tắt bằng mã trần (str),
    tương đương ``{"ma": mã}``.
    """
    bt = _chuan(bt)
    if sau > 8 or not isinstance(bt, dict) or len(bt) not in (1, 2):
        raise ValueError("Biểu thức: {ma[, la]} | {va: [...]} | {hoac: [...]} | {khong: …} | {khoang_cach: …}.")
    if set(bt) == {"khoang_cach"}:
        if not _MA_RE.fullmatch(str(bt["khoang_cach"])):
            raise ValueError(f"«khoang_cach» phải là mã cảm biến khoảng cách, không phải «{bt['khoang_cach']}».")
    elif "ma" in bt:
        if not _MA_RE.fullmatch(str(bt["ma"])) or str(bt["ma"]).startswith(TIEN_TO):
            raise ValueError(f"Thực thể «{bt['ma']}» không hợp lệ (không ghép cảm biến ghép vào nhau).")
        la = bt.get("la", ["on"])
        if not isinstance(la, list) or not la or not all(isinstance(x, str) for x in la):
            raise ValueError("«la» phải là danh sách trạng thái, vd [\"on\"].")
        if set(bt) - {"ma", "la"}:
            raise ValueError("Nút thực thể chỉ có «ma» và «la».")
    elif set(bt) == {"khong"}:
        _kiem(_chuan(bt["khong"]), sau + 1)
    elif len(bt) == 1 and next(iter(bt)) in ("va", "hoac") and isinstance(next(iter(bt.values())), list) \
            and next(iter(bt.values())):
        for x in next(iter(bt.values())):
            _kiem(x, sau + 1)
    else:
        raise ValueError("Biểu thức: {ma[, la]} | {va: [...]} | {hoac: [...]} | {khong: …} | {khoang_cach: …}.")


def dat(ma: str, ten: str, bieu_thuc: dict[str, Any], loai: str = "occupancy") -> dict[str, Any]:
    """Thêm / sửa một cảm biến ghép. ``ma`` phải dạng ``nhatienich.ghep_<tên>``."""
    ma = str(ma or "").strip()
    if not _TIEN_TO_RE.fullmatch(ma):
        raise ValueError(f"Mã cảm biến ghép phải dạng {TIEN_TO}<chữ_thường_số>.")
    if not str(ten or "").strip() or len(str(ten)) > 60:
        raise ValueError("Tên cảm biến ghép 1–60 chữ.")
    if loai not in ("occupancy", "presence", "motion", "power", "running", ""):
        raise ValueError("Loại: occupancy / presence / motion / power / running.")
    _kiem(bieu_thuc)
    with _khoa:
        _nap()["cam_bien"][ma] = {"ten": str(ten).strip(), "loai": loai, "bieu_thuc": bieu_thuc}
        _cuoi[ma] = "on" if tinh(bieu_thuc, _tt()) else "off"
        _luu()
    return ds()[ma]


def xoa(ma: str) -> bool:
    with _khoa:
        co = _nap()["cam_bien"].pop(str(ma), None) is not None
        _cuoi.pop(str(ma), None)
        if co:
            _luu()
    return co


def thanh_phan(bt: Any) -> set[str]:
    """Mọi mã cảm biến gốc trong biểu thức — port nguyên từ c2a."""
    bt = _chuan(bt)
    if "ma" in bt:
        return {str(bt["ma"])}
    if "khoang_cach" in bt:
        return {str(bt["khoang_cach"])}
    if "khong" in bt:
        return thanh_phan(_chuan(bt["khong"]))
    return set().union(*(thanh_phan(x) for x in next(iter(bt.values()))))


def tinh(bt: Any, tt: dict[str, str]) -> bool:
    """Giá trị biểu thức trên trạng thái ``tt`` (mã → trạng thái chữ thường).
    Thiếu = không khớp. Port từ c2a; nút ``khoang_cach`` không có số đo thì
    «không đo được» = không khớp (không khẳng định sai)."""
    bt = _chuan(bt)
    if "ma" in bt:
        return tt.get(str(bt["ma"]), "") in {str(x).lower() for x in bt.get("la", ["on"])}
    if "khoang_cach" in bt:
        return False  # giản lược NV195: vùng radar học thuộc hoc-nha (NV146)
    if "khong" in bt:
        return not tinh(_chuan(bt["khong"]), tt)
    if "va" in bt:
        return all(tinh(x, tt) for x in bt["va"])
    return any(tinh(x, tt) for x in bt["hoac"])


def _tt() -> dict[str, str]:
    try:
        ds_tt = co_che.doc_trang_thai() or []
        return {str(s.get("entity_id")): str(s.get("state") or "").lower() for s in ds_tt}
    except Exception:
        return {}


def hien_tai(tt: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Trạng thái hiện tại của mọi cảm biến ghép, cùng dạng một state HA."""
    tt = _tt() if tt is None else tt
    return [{"entity_id": ma, "state": "on" if tinh(c["bieu_thuc"], tt) else "off",
             "attributes": {"friendly_name": c["ten"], "device_class": c.get("loai") or ""}}
            for ma, c in ds().items()]


def chuoi(ma: str, su_kien: list[tuple[float, str, str]], tu: float, den: float) -> list[tuple[float, str]]:
    """Chuỗi (ts, "on"/"off") của cảm biến ghép ``ma`` trong [tu, den), dựng từ
    lịch sử cảm biến gốc ``su_kien`` = [(ts, thiet_bi, gia_tri)]: mốc đầu ở ``tu``
    là giá trị lúc ấy, sau đó chỉ các lần ĐỔI. Port từ c2a (đổi ro SQLite → danh
    sách sự kiện). KHÔNG ghi vào kho lịch sử: kho chỉ giữ điều quan sát được."""
    c = ds().get(ma)
    if not c:
        return []
    bt = c["bieu_thuc"]
    goc = sorted(thanh_phan(bt))
    tt: dict[str, str] = {}
    for ts, tb, gt in sorted(su_kien, key=lambda x: x[0]):
        if tb in goc and ts < tu:
            tt[tb] = str(gt).lower()
    cu = "on" if tinh(bt, tt) else "off"
    ra = [(float(tu), cu)]
    if not goc:
        return ra
    for ts, tb, gt in sorted(su_kien, key=lambda x: x[0]):
        if tb not in goc or ts < tu or ts >= den:
            continue
        tt[tb] = str(gt).lower()
        moi = "on" if tinh(bt, tt) else "off"
        if moi != cu:
            ra.append((float(ts), moi))
            cu = moi
    return ra


def khi_doi(entity_id: str, tt: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Tính lại các cảm biến ghép có dùng ``entity_id``; cái nào ĐỔI thì trả về
    [(ma, gia_tri_moi)]. Port ý tưởng ``khi_doi`` của c2a (bỏ phụ thuộc ha_live)."""
    ghep = [(ma, c) for ma, c in ds().items() if entity_id in thanh_phan(c["bieu_thuc"])]
    if not ghep:
        return []
    tt = _tt() if tt is None else tt
    doi = []
    with _khoa:
        for ma, c in ghep:
            moi = "on" if tinh(c["bieu_thuc"], tt) else "off"
            if _cuoi.get(ma, moi) != moi:
                doi.append((ma, moi))
            _cuoi[ma] = moi
    return doi


# ── Cảm biến kẹt theo từng ngày — port nguyên services/cam_bien_ket.py ────────
def _dau_ngay(t: float) -> float:
    d = datetime.fromtimestamp(t, _TZ)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _ngay_sau(t: float) -> float:
    return (datetime.fromtimestamp(t, _TZ) + timedelta(days=1, hours=2)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()


def ngay_ket(ts: list[float], gt: list[str | None], tu: float, den: float) -> list[tuple[float, float]]:
    """[(đầu ngày, cuối ngày)] những ngày TRỌN trong [tu, den] mà chuỗi báo «on»
    ≥ 98% thời gian — port nguyên từ c2a."""
    if not ts or not any(str(g).lower() in _BAT_TAT for g in gt):
        return []
    ra: list[tuple[float, float]] = []
    a = _ngay_sau(tu - 1) if _dau_ngay(tu) < tu else _dau_ngay(tu)
    i = 0
    cur: str | None = None
    while i < len(ts) and ts[i] <= a:
        cur = str(gt[i]).lower() if gt[i] is not None else None
        i += 1
    while True:
        b = _ngay_sau(a)
        if b > den:
            return ra
        on, last = 0.0, a
        while i < len(ts) and ts[i] < b:
            if cur == "on":
                on += ts[i] - last
            last, cur = ts[i], (str(gt[i]).lower() if gt[i] is not None else None)
            i += 1
        if cur == "on":
            on += b - last
        if on >= _KET * (b - a):
            ra.append((a, b))
        a = b


def bo_ket(ts: list[float], gt: list[str | None], tu: float, den: float) -> tuple[list[float], list[str | None]]:
    """Bỏ các ngày kẹt khỏi chuỗi (một mốc None ở đầu quãng + giá trị thật ở cuối
    quãng) — port nguyên từ c2a."""
    quang: list[list[float]] = []
    for a, b in ngay_ket(ts, gt, tu, den):
        if quang and quang[-1][1] == a:
            quang[-1][1] = b
        else:
            quang.append([a, b])
    if not quang:
        return ts, gt
    ra: list[tuple[float, str | None]] = []
    j, cuoi = 0, None
    for t, g in zip(ts, gt):
        while j < len(quang) and t >= quang[j][1]:
            ra += [(quang[j][0], None), (quang[j][1], cuoi)]
            j += 1
        cuoi = g
        if j < len(quang) and quang[j][0] <= t < quang[j][1]:
            continue
        ra.append((t, g))
    for a, b in quang[j:]:
        ra += [(a, None), (b, cuoi)]
    ra.sort(key=lambda x: x[0])
    return [t for t, _ in ra], [g for _, g in ra]
