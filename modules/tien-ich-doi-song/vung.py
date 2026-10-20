"""Vùng khoảng cách — bot tự học vùng của radar đo khoảng cách.

Port từ kho CỦA CHÍNH CHỦ MÁY (LUẬT PORT 19/10, chỉ chép không viết lại):
  nguồn: /opt/claude-c2a/chatgpt2api/services/vung_khoang_cach.py
  commit: d879b03 (cây CHỈ ĐỌC, không sửa c2a)

Ý tưởng (giữ nguyên, đã đo thật trên nhà chủ máy 01/10/2026): radar báo có người
mà người đứng ở khu bên cạnh (ví dụ radar phòng khách nhìn thẳng ra bếp). Bot tự
rút ngưỡng khoảng cách tách hai nhãn TRONG KHU / KHU BÊN CẠNH theo độ đúng CÂN
BẰNG; tách không đạt thì không dùng (``dat`` = False) và mọi phán đoán lúc đó là
đúng — chưa học được không bao giờ gây quyết định sai vì thiếu hiểu biết.

Chỉnh so với c2a (chỉ đổi lớp kết nối/khuôn module):
  - `from services.config import DATA_DIR` → `dat_duong_du_lieu()` như digest.
  - KHÔNG port: `cap_radar()` (đọc sổ thiết bị HA của c2a), `hoc()` (đọc lịch sử
    HA qua services.canh_bao_nha / ha_client của c2a). Điểm nối: `hoc_tu_du_lieu`
    nhận dữ liệu lịch sử ĐÃ LẤY (chuỗi (t, giá_trị)) do người gọi — ví dụ module
    home-assistant — thu thập, rồi chạy đúng thuật toán của c2a.
  - Số 0 / không phải số = radar KHÔNG đo được khoảng cách (đo: số 0 giữ trung vị
    13 giây, có lúc 5 phút, trong lúc radar vẫn báo có người), không phải "không
    có người" — cũng tính là đúng.
"""

from __future__ import annotations

import bisect
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("web2api.tien_ich_doi_song.vung")

_DUONG_DU_LIEU: Path | None = None
_PATH: Path | None = None
_khoa = threading.RLock()
_dl: dict[str, Any] | None = None

#: Mỗi nhãn (trong khu / khu bên cạnh) cần ít nhất ngần này số đo.
MAU_TOI_THIEU = 100
#: Độ đúng cân bằng (trung bình đúng của hai nhãn) tối thiểu mới dùng — đoán mò là 0,5.
CONG = 0.8
#: Radar khu bên cạnh báo có người quá ngần này phần thời gian là KẸT — bỏ khỏi nhãn.
KET = 0.98
#: Khu bên cạnh chỉ là NGUỒN BÁO LÂY khi radar của nó báo có người ít nhất ngần này
#: phần số đo lúc radar khu này báo mà camera khu này không thấy ai.
LAY = 0.4
#: …VÀ cao hơn lúc camera khu này THẤY người ít nhất ngần này.
LAY_CHENH = 0.2


def dat_duong_du_lieu(duong: str | Path) -> Path:
    """Module gọi một lần lúc nạp: thư mục chứa tệp trạng thái (sẽ mkdir 0700)."""
    global _DUONG_DU_LIEU, _PATH, _dl
    _DUONG_DU_LIEU = Path(duong)
    _DUONG_DU_LIEU.mkdir(parents=True, exist_ok=True)
    try:
        _DUONG_DU_LIEU.chmod(0o700)
    except OSError:
        pass
    _PATH = _DUONG_DU_LIEU / "vung_khoang_cach.json"
    with _khoa:
        _dl = None
    return _DUONG_DU_LIEU


def _nap() -> dict[str, Any]:
    global _dl
    if _dl is None:
        try:
            _dl = json.loads(_duong_tep().read_text(encoding="utf-8"))
            if not isinstance(_dl, dict):
                _dl = {}
        except (OSError, ValueError):
            _dl = {}
    return _dl


def _duong_tep() -> Path:
    if _PATH is None:
        raise RuntimeError("vung: chưa đặt thư mục dữ liệu (dat_duong_du_lieu)")
    return _PATH


def _luu() -> None:
    try:
        p = _duong_tep()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_nap(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    except Exception as exc:
        log.warning("vung: save state lỗi: %s", str(exc)[:160])


def ds() -> dict[str, dict[str, Any]]:
    with _khoa:
        return {k: dict(v) for k, v in _nap().items()}


def vung_dang_dung(m: dict[str, Any] | None) -> tuple[str, float] | None:
    """(hướng, ngưỡng) đang dùng: số chủ máy đặt (`dat_nguong`) thắng số bot học;
    chưa học đạt mà chủ máy chưa đặt thì None."""
    if not m:
        return None
    if m.get("nguong_chu") is not None:
        return str(m.get("huong") or "duoi"), float(m["nguong_chu"])
    return (str(m["huong"]), float(m["nguong"])) if m.get("dat") else None


def dat_nguong(ma: str, nguong: float | None) -> dict[str, Any]:
    """Chủ máy đặt ngưỡng vùng (None = trả về số bot học). Giữ qua mọi lần học lại."""
    with _khoa:
        m = _nap().get(str(ma))
        if m is None:
            raise ValueError(f"{ma} không phải khoảng cách của radar nào bot biết.")
        if nguong is None:
            m.pop("nguong_chu", None)
        else:
            if not 0 < float(nguong) <= 50:
                raise ValueError("Ngưỡng khoảng cách: 0–50.")
            m["nguong_chu"] = round(float(nguong), 2)
        _luu()
        return dict(m)


def trong_vung(ma: str, gia_tri: Any) -> bool:
    """Khoảng cách ``gia_tri`` của cảm biến ``ma`` có thể là người trong khu không.
    Chưa học đạt, số 0, không phải số → True (không bao giờ vì thiếu hiểu biết
    mà nói «khu trống»)."""
    vg = vung_dang_dung(_nap().get(str(ma)))
    if vg is None:
        return True
    try:
        v = float(gia_tri)
    except (TypeError, ValueError):
        return True
    if v <= 0:
        return True
    return v < vg[1] if vg[0] == "duoi" else v >= vg[1]


def vi_tri(ma: str, gia_tri: Any) -> bool | None:
    """Ba trạng thái: True = radar đang đo một người trong vùng khu, False = đo
    được nhưng ở ngoài vùng (khu bên cạnh), None = không biết (chưa học đạt,
    số 0, không phải số)."""
    vg = vung_dang_dung(_nap().get(str(ma)))
    if vg is None:
        return None
    try:
        v = float(gia_tri)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v < vg[1] if vg[0] == "duoi" else v >= vg[1]


def nguong(trong: list[float], ngoai: list[float]) -> dict[str, Any]:
    """Ngưỡng tách số đo lúc người TRONG khu với lúc người ở KHU BÊN CẠNH, theo
    độ đúng CÂN BẰNG (hai nhãn nặng như nhau — nhãn đông không được lấn)."""
    if not trong or not ngoai:
        return {"dung": 0.0}
    st, sn = sorted(trong), sorted(ngoai)
    tot = {"dung": 0.0}
    for t in sorted(set(st) | set(sn))[1:]:
        a = bisect.bisect_left(st, t) / len(st)
        b = (len(sn) - bisect.bisect_left(sn, t)) / len(sn)
        for huong, d in (("duoi", (a + b) / 2), ("tren", (2 - a - b) / 2)):
            if d > tot["dung"]:
                tot = {"dung": round(d, 3), "huong": huong, "nguong": round(t, 2)}
    return tot


def _luc(ts: list[float], chuoi: list[tuple[float, str]], t: float) -> str:
    i = bisect.bisect_right(ts, t) - 1
    return chuoi[i][1] if i >= 0 else ""


def ti_le_bat(chuoi: list[tuple[float, str]], tu: float, den: float) -> float:
    """Phần thời gian trong [tu, den) chuỗi trạng thái ở «on»."""
    if den <= tu:
        return 0.0
    bat, cu, t0 = 0.0, "", tu
    for t, g in chuoi:
        t = min(max(t, tu), den)
        if cu == "on":
            bat += t - t0
        cu, t0 = g, t
    if cu == "on":
        bat += den - t0
    return bat / (den - tu)


def nguon_lay(kc: list[tuple[float, str]], radar: list[tuple[float, str]],
              cam: list[list[tuple[float, str]]],
              ke: dict[str, list[tuple[float, str]]]) -> list[str]:
    """Khu bên cạnh nào radar khu này hay báo LÂY: lúc radar khu này báo (có số đo)
    mà camera khu không thấy ai, radar khu kia báo có người ít nhất LAY phần số
    lần, và hơn lúc camera thấy người ít nhất LAY_CHENH."""
    moc = {id(c): [x[0] for x in c] for c in [radar, *cam, *ke.values()]}
    n = {False: 0, True: 0}
    dem = {k: {False: 0, True: 0} for k in ke}
    for t, g in kc:
        try:
            if float(g) <= 0:
                continue
        except ValueError:
            continue
        if _luc(moc[id(radar)], radar, t) != "on":
            continue
        thay = any(_luc(moc[id(c)], c, t) == "on" for c in cam)
        n[thay] += 1
        for k, c in ke.items():
            dem[k][thay] += _luc(moc[id(c)], c, t) == "on"
    if not n[False]:
        return []
    ra = []
    for k, v in dem.items():
        khong = v[False] / n[False]
        co = v[True] / n[True] if n[True] else 0.0
        if khong >= LAY and khong - co >= LAY_CHENH:
            ra.append(k)
    return ra


def gan_nhan(kc: list[tuple[float, str]], radar: list[tuple[float, str]],
             cam: list[list[tuple[float, str]]],
             ke: list[list[tuple[float, str]]]) -> tuple[list[float], list[float]]:
    """(số đo lúc người TRONG khu, số đo lúc người ở KHU BÊN CẠNH). Chỉ xét lúc
    radar khu báo có người và số đo khác 0. Trong khu: camera khu thấy người VÀ
    không radar khu bên cạnh nào báo. Khu bên cạnh: camera khu KHÔNG thấy ai VÀ
    có radar khu bên cạnh báo."""
    trong, ngoai = [], []
    moc = {id(c): [x[0] for x in c] for c in [radar, *cam, *ke]}

    def luc(c: list[tuple[float, str]], t: float) -> str:
        return _luc(moc[id(c)], c, t)
    for t, g in kc:
        try:
            v = float(g)
        except ValueError:
            continue
        if v <= 0 or luc(radar, t) != "on":
            continue
        thay = any(luc(c, t) == "on" for c in cam)
        ben = any(luc(k, t) == "on" for k in ke)
        if thay and not ben:
            trong.append(v)
        elif not thay and ben:
            ngoai.append(v)
    return trong, ngoai


def hoc_tu_du_lieu(ma: str, khu: str, radar: str, don_vi: str,
                   kc: list[tuple[float, str]], radar_ct: list[tuple[float, str]],
                   cam_ct: list[list[tuple[float, str]]],
                   ke_ct: dict[str, list[tuple[float, str]]]) -> dict[str, Any]:
    """Học vùng cho một radar từ dữ liệu lịch sử ĐÃ THU THẬP.

    Người gọi (ví dụ tác nhân dùng module home-assistant) lấy lịch sử các cảm
    biến rồi gọi hàm này — không tự đọc HA như c2a (cap_radar/hoc không port,
    ghi lý do ở báo cáo NV165). `kc`: chuỗi (t, giá_trị_khoảng_cách) của cảm
    biến khoảng cách; `radar_ct`: chuỗi (t, on/off) của radar hiện diện cùng
    thiết bị; `cam_ct`: danh sách chuỗi (t, on/off) của camera Frigate trong khu;
    `ke_ct`: {mã cảm biến hiện diện khu bên cạnh: chuỗi (t, on/off)}.

    Trả kết quả học (và lưu vào kho): {ma, khu, radar, dat, huong, nguong,
    dung, n_trong, n_ngoai, ly_do?, luc}. `dat`=False khi chưa đủ mẫu hoặc độ
    đúng < CONG — lúc đó `trong_vung` luôn đúng (không tắt nhầm)."""
    gio = time.time()
    ke_hop_le = [k for k, c in ke_ct.items()
                 if ti_le_bat(c, gio - 10 * 86400, gio) <= KET]
    ke = nguon_lay(kc, radar_ct, cam_ct, {k: ke_ct[k] for k in ke_hop_le})
    trong, ngoai = gan_nhan(kc, radar_ct, cam_ct, [ke_ct[k] for k in ke])
    t = nguong(trong, ngoai)
    dat = len(trong) >= MAU_TOI_THIEU and len(ngoai) >= MAU_TOI_THIEU and t["dung"] >= CONG
    if not dat:
        ly_do = "không đủ mẫu" if (len(trong) < MAU_TOI_THIEU or len(ngoai) < MAU_TOI_THIEU) \
            else f"độ đúng {t.get('dung', 0)} < {CONG}"
    else:
        ly_do = ""
    ket = {"ma": ma, "khu": khu, "radar": radar, "don_vi": don_vi,
           "nhan": ["frigate"], "ke": ke,
           "n_trong": len(trong), "n_ngoai": len(ngoai), "dat": dat,
           "ly_do": ly_do, "luc": gio}
    ket.update({k: t[k] for k in ("dung", "huong", "nguong") if k in t})
    with _khoa:
        cu = _nap().get(str(ma)) or {}
        if cu.get("nguong_chu") is not None:
            ket["nguong_chu"] = cu["nguong_chu"]  # số chủ máy đặt sống qua lần học
        _nap()[str(ma)] = ket
        _luu()
    return dict(ket)


def cho_de(khu: str) -> list[dict[str, Any]]:
    """Vùng đang dùng của radar trong ``khu`` (học đạt, hoặc chủ máy đặt)."""
    ra = []
    for m, v in ds().items():
        vg = vung_dang_dung(v)
        if v.get("khu") == khu and vg:
            ra.append({"ma": m, **v, "huong": vg[0], "nguong": vg[1]})
    return ra


def _reset_for_tests(duong: Path | None = None) -> None:
    global _dl, _PATH
    with _khoa:
        if duong is not None:
            _PATH = duong
        _dl = None
