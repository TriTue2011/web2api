"""Combo: model ảo chạy lần lượt nhiều bước (model của các module), lùi theo mã HTTP — mã lõi, không tên nhà cung cấp.

Dữ liệu: `<WEB2API_DATA>/combo.json` (0600) — {<tên>: {"ten", "mo_ta", "kha_nang": [mã…], "buoc": [{"model", "bat"}]}}.
Bước chỉ lưu `model` dạng `<tiền tố>/<tên>`; module phục vụ được suy LÚC CHẠY bằng ĐÚNG hàm phân giải của đường /v1
thường (truyền vào qua `phan_giai`, không tự viết lại) — module cài lại/đổi id vẫn đúng.

Gọi: `model = combo/<tên>` hoặc tên trần `<tên>` (không «/»). Module mang tiền tố `combo` thì THẮNG `combo/<…>` (gọi
combo bằng tên trần) — xem `tuyen._module_theo_model`.

Luật chạy (một vòng chung cho chat và ảnh, một bộ phân loại `phan_loai`):
- Lọc HỢP LỆ trước: bước tắt, model tắt, module chưa liên kết, thiếu phương thức của việc → không bao giờ gọi; không
  còn bước hợp lệ → 503 `no_available_routes`. «Không chặn lựa chọn cuối» chỉ áp trên tập đã hợp lệ (cầu chì mở,
  bước bị hạ bậc vẫn được thử, xếp cuối, theo thứ tự gốc).
- 2xx chốt; 400 PHÂN XỬ bằng so sánh (bước hợp lệ kế trả 200 → 400 là của (module, model) ấy: hạ bậc có hạn trong
  nhóm việc đó; bước kế cũng 400 → lỗi của yêu cầu, trả 400 đầu nguyên văn, không thử thêm); 413/404/501 sang bước kế,
  không tính hỏng; 429 hạ bậc tới hết Retry-After; 401/403 cầu chì (module, việc) mở NGAY; 5xx/lỗi gọi tính một lỗi
  cầu chì; 4xx khác trả ngay.
- Cầu chì theo (module, nhóm việc): dong → mo (ngưỡng lỗi liên tiếp) → nửa mở; «hỏi» (`trang_thai`) không chiếm
  lượt; «chiếm» (`chiem`) giữ MỘT lượt thăm dò, có hạn (bẫy B3), thả trong `finally`.
- Stream: chỉ đổi bước TRƯỚC byte đầu; sau byte đầu lỗi → sự kiện SSE `stream_interrupted` rồi kết thúc (không nuốt);
  iterator thượng nguồn luôn được `aclose` trong `finally`.
- Trần thời gian TỔNG `WEB2API_COMBO_TOI_DA_GIAY` (mặc định 300) cho các bước tới byte đầu → 504 `combo_timeout`;
  bước đầu luôn được thử; luồng đã chảy thì không bị cắt.
- Dính tài khoản theo băm sha256 (của `user`/tiêu đề `x-user` cắt 256 ký tự, hoặc tin user đầu cắt 2000 ký tự) —
  khoá cố định, không giữ chuỗi thô; trần số khoá.
- Tiêu đề `x-web2api-route: <tiền tố>/<tên>` (dán lại vào `model` là gọi thẳng đúng bước) và `x-web2api-combo`.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from app import kho, nhat_ky
from app.bien_loi import che_chuoi
from app.kham_pha import ID_HOP_LE, MA_KHA_NANG, phan_loai_loi_mac_dinh
from app.loi_openai import LoiOpenAI

log = logging.getLogger("web2api.combo")

TRAN_SO_BUOC = 20
TRAN_KHA_NANG = 20
TRAN_MO_TA = 500
TRAN_PHIEN_DINH = 2000
TRAN_USER_BAM = 256
TRAN_TIN_BAM = 2000
TRAN_THAN_LOI = 65536
TIMEOUT_NUA_MO = 30.0
TIMEOUT_MODELS = 5.0
NGHI_429_MAC_DINH = 60.0
NGHI_429_TRAN = 600.0
#: Tên trùng đường con của /api/combo (GET /api/combo/trang-thai) thì không đọc lại được bằng GET.
TEN_DANH_RIENG = {"combo", "trang-thai"}
#: Model của bước: `<tiền tố>/<tên>` — tiền tố theo mẫu TIEN_TO_HOP_LE, tên là ASCII in được không khoảng trắng
#: (giá trị đi vào tiêu đề x-web2api-route nên không được có ký tự điều khiển).
MAU_MODEL_BUOC = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}/[\x21-\x7e]{1,200}")


def _so_env(ten: str, mac_dinh: float, toi_thieu: float) -> float:
    try:
        return max(toi_thieu, float(os.environ.get(ten, mac_dinh)))
    except (TypeError, ValueError):
        return mac_dinh


def nguong_cau_chi() -> int:
    return int(_so_env("WEB2API_COMBO_NGUONG", 3, 1))


def thoi_gian_nghi_cau_chi() -> float:
    return _so_env("WEB2API_COMBO_NGHI_GIAY", 60.0, 1.0)


def thoi_gian_dinh_phien() -> float:
    return _so_env("WEB2API_COMBO_DINH_GIAY", 900.0, 0.0)


def toi_da_giay() -> float:
    return _so_env("WEB2API_COMBO_TOI_DA_GIAY", 300.0, 0.1)


def doc_retry_after(gia_tri: str | None, bay_gio: float) -> float:
    """Số giây nghỉ từ Retry-After (giây hoặc ngày HTTP), kẹp trong [1, NGHI_429_TRAN]."""
    giay = None
    if gia_tri:
        gia_tri = str(gia_tri).strip()
        try:
            giay = float(gia_tri)
        except ValueError:
            try:
                giay = email.utils.parsedate_to_datetime(gia_tri).timestamp() - bay_gio
            except (TypeError, ValueError):
                giay = None
    if giay is None or giay != giay:
        return NGHI_429_MAC_DINH
    return min(max(giay, 1.0), NGHI_429_TRAN)


# ───────────── trạng thái trong bộ nhớ (khởi động lại là sạch) ─────────────

class BoCauChi:
    """Cầu chì theo (module, nhóm việc). `trang_thai` chỉ HỎI; `chiem` mới giữ lượt thăm dò nửa mở."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], dict[str, Any]] = {}

    def _get(self, k: tuple[str, str]) -> dict[str, Any]:
        st = self._states.get(k)
        if st is None:
            st = self._states[k] = {
                "trang_thai": "dong", "consecutive_failures": 0, "opened_at": 0.0,
                "probe_in_progress": False, "probe_started_at": 0.0, "loi_cuoi": "", "ma_loi_cuoi": None,
                "so_thanh_cong": 0, "so_that_bai": 0, "so_lan_mo": 0,
            }
        return st

    @staticmethod
    def _tinh(st: dict[str, Any], bay_gio: float) -> str:
        if st["trang_thai"] == "dong":
            return "dong"
        if st["trang_thai"] == "mo" and bay_gio - st["opened_at"] < thoi_gian_nghi_cau_chi():
            return "mo"
        if st["probe_in_progress"] and bay_gio - st["probe_started_at"] < TIMEOUT_NUA_MO:
            return "dang_tham_do"
        return "cho_tham_do"

    def trang_thai(self, module: str, viec: str) -> str:
        """dong | mo | cho_tham_do (được phép một thăm dò) | dang_tham_do (lượt thăm dò đã có người giữ). Không đổi gì."""
        with self._lock:
            st = self._states.get((module, viec))
            return "dong" if st is None else self._tinh(st, time.time())

    def chiem(self, module: str, viec: str) -> str | None:
        """"dong" (đi thẳng), "tham_do" (đã giữ lượt thăm dò — PHẢI kết thúc bằng record_*/tha), None (không được gọi)."""
        bay_gio = time.time()
        with self._lock:
            st = self._states.get((module, viec))
            tt = "dong" if st is None else self._tinh(st, bay_gio)
            if tt == "dong":
                return "dong"
            if tt != "cho_tham_do":
                return None
            if st["trang_thai"] == "mo":
                nhat_ky.ghi("combo", "thong_tin", f"Cầu chì NỬA MỞ cho ({module}, {viec})", module=module, viec=viec)
            st["trang_thai"] = "nua_mo"
            st["probe_in_progress"] = True
            st["probe_started_at"] = bay_gio
            return "tham_do"

    def tha(self, module: str, viec: str) -> None:
        """Trả lượt thăm dò khi bước kết thúc mà không có kết luận về sức khoẻ (413, 404, 400…)."""
        with self._lock:
            st = self._states.get((module, viec))
            if st is not None:
                st["probe_in_progress"] = False

    def record_success(self, module: str, viec: str) -> None:
        with self._lock:
            st = self._get((module, viec))
            st["so_thanh_cong"] += 1
            st["probe_in_progress"] = False
            if st["trang_thai"] != "dong":
                nhat_ky.ghi("combo", "thong_tin", f"Cầu chì ĐÓNG cho ({module}, {viec})", module=module, viec=viec)
            st.update(trang_thai="dong", consecutive_failures=0, opened_at=0.0, loi_cuoi="", ma_loi_cuoi=None)

    def _ghi_loi(self, module: str, viec: str, ma: int, loi: str, mo_ngay: bool) -> None:
        with self._lock:
            st = self._get((module, viec))
            st["consecutive_failures"] += 1
            st["so_that_bai"] += 1
            st["loi_cuoi"] = che_chuoi(str(loi or ""))[:200]
            st["ma_loi_cuoi"] = ma
            st["probe_in_progress"] = False
            if mo_ngay or st["trang_thai"] == "nua_mo" or st["consecutive_failures"] >= nguong_cau_chi():
                if st["trang_thai"] != "mo":
                    st["so_lan_mo"] += 1
                    nhat_ky.ghi("combo", "canh_bao", f"Cầu chì MỞ cho ({module}, {viec}) sau "
                                f"{st['consecutive_failures']} lỗi liên tiếp (mã {ma})", module=module, viec=viec,
                                ma=ma)
                st["trang_thai"] = "mo"
                st["opened_at"] = time.time()

    def record_failure(self, module: str, viec: str, ma: int, loi: str = "") -> None:
        self._ghi_loi(module, viec, ma, loi, False)

    def mo_ngay(self, module: str, viec: str, ma: int, loi: str = "") -> None:
        """Khoá hỏng (401/403): module đã tự xoay hết khoá rồi mới trả — nghỉ ngay, không đợi đủ ngưỡng."""
        self._ghi_loi(module, viec, ma, loi, True)

    def get_stats(self, module: str, viec: str) -> dict[str, Any]:
        bay_gio = time.time()
        with self._lock:
            st = dict(self._get((module, viec)))
        st["trang_thai"] = {"dong": "dong", "mo": "mo"}.get(self._tinh(st, bay_gio), "nua_mo")
        st["mo_con_lai_giay"] = (max(0.0, thoi_gian_nghi_cau_chi() - (bay_gio - st["opened_at"]))
                                 if st["trang_thai"] == "mo" else 0.0)
        return st

    def cua_module(self, module: str) -> dict[str, dict[str, Any]]:
        """{việc: thống kê} của mọi nhóm việc đã từng ghi cho module — mã mở, không danh sách việc."""
        with self._lock:
            viec = [v for (m, v) in self._states if m == module]
        return {v: self.get_stats(module, v) for v in sorted(viec)}


class BoHanMuc:
    """Bước bị HẠ BẬC có hạn theo (module, model, việc): 429 (tới hết Retry-After) hoặc 400 đã phân xử."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nghi: dict[tuple[str, str, str], tuple[float, int]] = {}

    def con_lai_giay(self, module: str, model: str, viec: str) -> float:
        k = (module, model, viec)
        bay_gio = time.time()
        with self._lock:
            muc = self._nghi.get(k)
            if muc is None:
                return 0.0
            if bay_gio >= muc[0]:
                del self._nghi[k]
                return 0.0
            return muc[0] - bay_gio

    def dang_ha(self, module: str, model: str, viec: str) -> bool:
        return self.con_lai_giay(module, model, viec) > 0

    def ha(self, module: str, model: str, viec: str, giay: float, ma: int) -> None:
        with self._lock:
            self._nghi[(module, model, viec)] = (time.time() + giay, ma)
        nhat_ky.ghi("combo", "canh_bao", f"Bước {model} ({module}, {viec}) hạ xuống cuối thứ tự {round(giay, 1)} s "
                    f"(mã {ma})", module=module, model=model, viec=viec, ma=ma, nghi_giay=round(giay, 1))

    def cua_buoc(self, module: str, model: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            viec = [v for (m, mdl, v) in self._nghi if m == module and mdl == model]
        ra = {}
        for v in sorted(viec):
            con = self.con_lai_giay(module, model, v)
            if con > 0:
                ra[v] = {"con_lai_giay": round(con, 1), "ma": self._nghi.get((module, model, v), (0, None))[1]}
        return ra


class BoDinhPhien:
    """Dính tài khoản: (combo, việc, băm phiên) → model của bước đã trả lời. Chỉ giữ băm, có trần số khoá."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: OrderedDict[str, tuple[str, float]] = OrderedDict()

    @staticmethod
    def lay_khoa_phien(than: dict, x_user: str | None = None) -> str | None:
        """Khoá phiên ĐỘ DÀI CỐ ĐỊNH: "u:"/"h:" + sha256 hex — của `user` (hoặc tiêu đề x-user) cắt TRAN_USER_BAM ký
        tự, nếu không có thì của tin user đầu tiên cắt TRAN_TIN_BAM ký tự. Chuỗi thô không được giữ lại."""
        user = than.get("user")
        if not isinstance(user, str) or not user.strip():
            user = x_user if isinstance(x_user, str) else ""
        user = user.strip()[:TRAN_USER_BAM] if user else ""
        if user:
            return "u:" + hashlib.sha256(user.encode("utf-8", errors="replace")).hexdigest()
        messages = than.get("messages")
        if not isinstance(messages, list):
            return None
        for m in messages:
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, list):
                phan, dai = [], 0
                for p in c:
                    if dai >= TRAN_TIN_BAM:
                        break
                    if isinstance(p, dict) and isinstance(p.get("text"), str):
                        phan.append(p["text"][:TRAN_TIN_BAM])
                        dai += len(phan[-1]) + 1
                c = " ".join(phan)
            tin = c[:TRAN_TIN_BAM] if isinstance(c, str) else ""
            if tin:
                return "h:" + hashlib.sha256(tin.encode("utf-8", errors="replace")).hexdigest()
            return None
        return None

    def get(self, combo: str, viec: str, khoa: str | None) -> str | None:
        if thoi_gian_dinh_phien() <= 0 or not khoa:
            return None
        mk = f"{combo}|{viec}|{khoa}"
        with self._lock:
            muc = self._map.get(mk)
            if muc is None:
                return None
            if time.time() > muc[1]:
                del self._map[mk]
                return None
            self._map.move_to_end(mk)
            return muc[0]

    def bind(self, combo: str, viec: str, khoa: str | None, route: str) -> None:
        ttl = thoi_gian_dinh_phien()
        if ttl <= 0 or not khoa:
            return
        mk = f"{combo}|{viec}|{khoa}"
        with self._lock:
            self._map.pop(mk, None)
            self._map[mk] = (route, time.time() + ttl)
            while len(self._map) > TRAN_PHIEN_DINH:
                self._map.popitem(last=False)

    def evict(self, combo: str, viec: str, khoa: str | None) -> None:
        if khoa:
            with self._lock:
                self._map.pop(f"{combo}|{viec}|{khoa}", None)

    def count_for_combo(self, combo: str) -> int:
        dau, bay_gio = f"{combo}|", time.time()
        with self._lock:
            return sum(1 for k, (_, het) in self._map.items() if k.startswith(dau) and het > bay_gio)


bo_cau_chi = BoCauChi()
bo_han_muc = BoHanMuc()
bo_dinh_phien = BoDinhPhien()


# ───────────── hình dạng combo: MỘT bộ kiểm cho PUT và cho dữ liệu đã lưu ─────────────

def _loi_400(thong_diep: str, code: str = "invalid_request") -> LoiOpenAI:
    return LoiOpenAI(400, thong_diep, code=code)


def kiem_ten(ten: Any) -> str:
    if not isinstance(ten, str) or not ID_HOP_LE.fullmatch(ten) or ten in TEN_DANH_RIENG:
        raise _loi_400("Tên combo không hợp lệ: chữ thường, số, «_», «-», dài 2–41 ký tự; không được là "
                       "«combo» hay «trang-thai»", "invalid_name")
    return ten


def kiem_hinh_dang(ten: Any, du_lieu: Any) -> dict[str, Any]:
    """Combo chuẩn {"ten", "mo_ta", "kha_nang", "buoc": [{"model", "bat"}]} — sai KIỂU/hình dạng → LoiOpenAI 400.
    Lời lỗi chỉ nêu vị trí và quy tắc, không chép lại giá trị đầu vào."""
    ten = kiem_ten(ten)
    if not isinstance(du_lieu, dict):
        raise _loi_400("Combo phải là đối tượng JSON")
    buoc = du_lieu.get("buoc")
    if not isinstance(buoc, list) or not 1 <= len(buoc) <= TRAN_SO_BUOC:
        raise _loi_400(f"«buoc» phải là danh sách 1–{TRAN_SO_BUOC} bước")
    buoc_chuan = []
    for i, b in enumerate(buoc, 1):
        if not isinstance(b, dict):
            raise _loi_400(f"Bước {i}: phải là đối tượng {{\"model\", \"bat\"}}")
        model = b.get("model")
        if not isinstance(model, str) or not MAU_MODEL_BUOC.fullmatch(model):
            raise _loi_400(f"Bước {i}: «model» phải có dạng <tiền tố>/<tên> (ASCII in được, không khoảng trắng)")
        if model.startswith("combo/"):
            raise _loi_400(f"Bước {i}: không lồng combo trong combo")
        bat = b.get("bat", True)
        if not isinstance(bat, bool):
            raise _loi_400(f"Bước {i}: «bat» phải là true/false")
        buoc_chuan.append({"model": model, "bat": bat})
    kha_nang = du_lieu.get("kha_nang", ["chat"])
    if (not isinstance(kha_nang, list) or len(kha_nang) > TRAN_KHA_NANG
            or not all(isinstance(x, str) and MA_KHA_NANG.fullmatch(x) for x in kha_nang)):
        raise _loi_400(f"«kha_nang» phải là danh sách (≤ {TRAN_KHA_NANG}) mã khả năng dạng chữ thường/số/«_»")
    mo_ta = du_lieu.get("mo_ta", "")
    if not isinstance(mo_ta, str) or len(mo_ta) > TRAN_MO_TA:
        raise _loi_400(f"«mo_ta» phải là chuỗi ≤ {TRAN_MO_TA} ký tự")
    return {"ten": ten, "mo_ta": mo_ta.strip(), "kha_nang": list(dict.fromkeys(kha_nang)), "buoc": buoc_chuan}


_da_canh_bao: set[str] = set()


def _canh_bao_mot_lan(thong_diep: str) -> None:
    if thong_diep not in _da_canh_bao and len(_da_canh_bao) < 1000:
        _da_canh_bao.add(thong_diep)
        log.warning("%s", thong_diep)


def doc_tat_ca() -> tuple[dict[str, dict[str, Any]], int]:
    """(combo hợp lệ theo tên, số mục hỏng bị bỏ). combo.json hỏng/sửa tay sai KHÔNG bao giờ làm sập đường gọi:
    mục hỏng bị bỏ và ghi cảnh báo (mỗi lý do một lần)."""
    try:
        tat_ca = kho.doc_cai_dat_combo()
    except kho.KhoHong as e:
        _canh_bao_mot_lan(f"combo.json không đọc được, coi như chưa có combo: {e}")
        return {}, 1
    ra, hong = {}, 0
    for ten, muc in tat_ca.items():
        try:
            ra[ten] = kiem_hinh_dang(ten, muc)
        except LoiOpenAI as e:
            hong += 1
            _canh_bao_mot_lan(f"combo.json: bỏ một mục hỏng ({e})")
    return ra, hong


def lay(ten: str) -> dict[str, Any] | None:
    return doc_tat_ca()[0].get(ten)


def co_combo() -> bool:
    return bool(doc_tat_ca()[0])


async def kiem_tra_combo(ten: Any, du_lieu: Any, so: Any, phan_giai: Callable[[str], tuple[str, Any]]
                         ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Kiểm cho PUT: hình dạng (400) + tên không trùng tiền tố module đang có (400); bước trỏ module/model chưa dùng
    được vẫn LƯU nhưng nêu trong `bo_qua` = [{"buoc": số thứ tự, "ly_do": mã}] (mã của bộ phân giải /v1, hoặc
    "model_khong_co" khi module đang chạy mà danh sách model của nó không có)."""
    combo = kiem_hinh_dang(ten, du_lieu)
    if so.tim_theo_tien_to(combo["ten"]) is not None:
        raise _loi_400("Tên combo trùng tiền tố của một module đang có", "invalid_name")
    bo_qua: list[dict[str, Any]] = []
    can_hoi: dict[str, tuple[Any, list[tuple[int, str]]]] = {}
    for i, b in enumerate(combo["buoc"], 1):
        try:
            id_, m = phan_giai(b["model"])
        except LoiOpenAI as e:
            bo_qua.append({"buoc": i, "ly_do": e.than["error"]["code"]})
            continue
        can_hoi.setdefault(id_, (m, []))[1].append((i, b["model"]))

    async def hoi(m: Any) -> set[str] | None:
        try:
            ds = await asyncio.wait_for(m.models(), TIMEOUT_MODELS)
        except Exception as e:  # module hỏng/chậm không chặn việc lưu — chỉ không kết luận được
            log.warning("combo: không hỏi được danh sách model (%s)", type(e).__name__)
            return None
        return {x.get("id") for x in ds if isinstance(x, dict)} if isinstance(ds, list) else None

    cac = list(can_hoi.values())
    for (_, buoc), co in zip(cac, await asyncio.gather(*(hoi(m) for m, _ in cac))):
        if co is not None:
            bo_qua += [{"buoc": i, "ly_do": "model_khong_co"} for i, model in buoc if model not in co]
    bo_qua.sort(key=lambda x: x["buoc"])
    return combo, bo_qua


def muc_model(data: list[dict]) -> list[dict]:
    """Mục /v1/models của các combo: `kha_nang` = giao khả năng của các bước bật có trong danh mục, không có thì
    lời khai của combo."""
    model_map = {m.get("id"): m for m in data if isinstance(m, dict)}
    ra = []
    for ten, cb in sorted(doc_tat_ca()[0].items()):
        cac_kn = []
        for b in cb["buoc"]:
            kn = model_map.get(b["model"], {}).get("kha_nang") if b["bat"] else None
            if isinstance(kn, list):
                cac_kn.append({x for x in kn if isinstance(x, str)})
        chung = set.intersection(*cac_kn) if cac_kn else set(cb["kha_nang"])
        ra.append({"id": f"combo/{ten}", "object": "model", "created": 0, "owned_by": "combo",
                   "kha_nang": sorted(chung or {"chat"})})
    return ra


def trang_thai_tat_ca(so: Any, phan_giai: Callable[[str], tuple[str, Any]]) -> list[dict[str, Any]]:
    """Trạng thái từng (combo, bước) cho GET /api/combo/trang-thai."""
    ra = []
    for ten, cb in sorted(doc_tat_ca()[0].items()):
        buoc = []
        for b in cb["buoc"]:
            muc: dict[str, Any] = {"model": b["model"], "bat": b["bat"]}
            try:
                id_, _ = phan_giai(b["model"])
            except LoiOpenAI as e:
                muc.update(module=None, dung_duoc=False, ly_do=e.than["error"]["code"])
            else:
                muc.update(module=id_, dung_duoc=b["bat"], ly_do=None if b["bat"] else "buoc_tat",
                           cau_chi=bo_cau_chi.cua_module(id_), ha_bac=bo_han_muc.cua_buoc(id_, b["model"]))
            buoc.append(muc)
        ra.append({"ten": ten, "mo_ta": cb["mo_ta"], "kha_nang": cb["kha_nang"], "buoc": buoc,
                   "so_phien_dinh": bo_dinh_phien.count_for_combo(ten)})
    return ra


# ───────────── chạy combo ─────────────

def phan_loai(ma: int) -> str:
    """Cách combo đối xử với mã của một bước — MỘT hàm cho chat và ảnh, theo mã trạng thái (không dò chữ)."""
    if 200 <= ma < 300:
        return "thanh_cong"
    if ma == 400:
        return "phan_xu"
    if ma in (404, 413, 501):
        return "bo_qua"  # bước không dùng được cho yêu cầu này (model/khả năng không có, thân quá lớn): không tính hỏng
    loai = phan_loai_loi_mac_dinh(ma)
    return loai if loai in ("het_han_muc", "khoa_hong", "may_chu_loi") else "yeu_cau_sai"


async def _dong(noi_dung: Any) -> None:
    dong = getattr(noi_dung, "aclose", None)
    if callable(dong):
        try:
            await dong()
        except Exception as e:  # đóng luồng thượng nguồn hỏng không được che lỗi chính
            log.warning("combo: đóng luồng thượng nguồn lỗi (%s)", type(e).__name__)


async def _doc_than_loi(noi_dung: Any) -> bytes:
    """Thân lỗi của một bước thành bytes (đã che khoá); luồng thì đọc có trần rồi đóng."""
    if isinstance(noi_dung, str):
        noi_dung = noi_dung.encode("utf-8")
    elif not isinstance(noi_dung, (bytes, bytearray)):
        phan, dai = [], 0
        try:
            if hasattr(noi_dung, "__aiter__"):
                async for k in noi_dung:
                    k = k.encode("utf-8") if isinstance(k, str) else bytes(k)
                    phan.append(k)
                    dai += len(k)
                    if dai >= TRAN_THAN_LOI:
                        break
        except Exception as e:
            log.warning("combo: đọc thân lỗi của bước hỏng (%s)", type(e).__name__)
        finally:
            await _dong(noi_dung)
        noi_dung = b"".join(phan)[:TRAN_THAN_LOI]
    noi_dung = bytes(noi_dung)
    # che_chuoi che MỌI chuỗi dạng token ≥ 20 ký tự (kể cả "invalid_request_error"): với thân lỗi JSON chuẩn chỉ che
    # `error.message` — nơi nhà cung cấp hay nhắc lại khoá — để `type`/`code` tới khách nguyên văn.
    try:
        j = json.loads(noi_dung)
    except ValueError:
        j = None
    loi = j.get("error") if isinstance(j, dict) else None
    if isinstance(loi, dict):
        tin = loi.get("message")
        if isinstance(tin, str) and che_chuoi(tin) != tin:
            loi["message"] = che_chuoi(tin)
            return json.dumps(j, ensure_ascii=False).encode("utf-8")
        return noi_dung
    return che_chuoi(noi_dung.decode("utf-8", errors="replace")).encode("utf-8")


def _su_kien_loi(thong_diep: str) -> bytes:
    loi = {"error": {"message": thong_diep, "type": "api_error", "code": "stream_interrupted"}}
    return f"data: {json.dumps(loi, ensure_ascii=False)}\n\n".encode("utf-8")


class _Het(Exception):
    """Hết trần thời gian tổng."""


class BoDieuPhoiCombo:
    """Thể hiện «module ảo» của một combo cho một yêu cầu: có `chat` và `anh` như module thật."""

    def __init__(self, ten: str, combo: dict, so: Any, request: Any = None, *,
                 phan_giai: Callable[[str], tuple[str, Any]]) -> None:
        self.ten = ten
        self.combo = combo
        self.so = so
        self.request = request
        self.phan_giai = phan_giai
        self.manifest = {"id": f"combo/{ten}", "cung_cap": ["chat", "anh"]}

    # ── chọn bước ──

    def _hop_le(self, viec: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Bước HỢP LỆ theo thứ tự gốc + lý do của bước bị loại (bước tắt, phân giải hỏng, thiếu phương thức)."""
        hop_le, ly_do = [], []
        for i, b in enumerate(self.combo["buoc"], 1):
            if b.get("bat") is not True:
                ly_do.append(f"bước {i}: buoc_tat")
                continue
            try:
                id_, m = self.phan_giai(b["model"])
            except LoiOpenAI as e:
                ly_do.append(f"bước {i}: {e.than['error']['code']}")
                continue
            if not callable(getattr(m, viec, None)):
                ly_do.append(f"bước {i}: capability_not_supported")
                continue
            hop_le.append({"module": id_, "model": b["model"], "the_hien": m, "cuoi": False})
        return hop_le, ly_do

    def _khong_con_buoc(self, viec: str, ly_do: list[str]) -> LoiOpenAI:
        return LoiOpenAI(503, f"Combo «{self.ten}» không còn bước nào dùng được cho «{viec}» ({'; '.join(ly_do)}). "
                              "Xem GET /api/combo/trang-thai.", code="no_available_routes",
                         headers={"x-web2api-combo": self.ten})

    def _xep(self, hop_le: list[dict[str, Any]], viec: str, khoa: str | None) -> list[dict[str, Any]]:
        """Thứ tự thử: [bước dính] + bước tốt + bước bị hạ bậc + bước cầu chì đang chặn (vẫn thử — không chặn lựa
        chọn cuối). Mỗi nhóm giữ THỨ TỰ GỐC, nên hết hạn hạ bậc/nghỉ là bước tự về chỗ cũ."""
        tot, ha, chan = [], [], []
        for b in hop_le:
            tt = bo_cau_chi.trang_thai(b["module"], viec)
            if tt in ("mo", "dang_tham_do"):
                b["cuoi"] = True
                chan.append(b)
            elif bo_han_muc.dang_ha(b["module"], b["model"], viec):
                ha.append(b)
            else:
                tot.append(b)
        dinh = bo_dinh_phien.get(self.ten, viec, khoa)
        if dinh is not None:
            vi_tri = next((i for i, b in enumerate(tot) if b["model"] == dinh), None)
            if vi_tri is None:
                bo_dinh_phien.evict(self.ten, viec, khoa)  # bước dính hỏng/cạn/không còn: bỏ dính
            else:
                tot.insert(0, tot.pop(vi_tri))
        return tot + ha + chan

    # ── ghi nhận ──

    def _tieu_de(self, headers: dict, model: str) -> dict:
        ra = {k: v for k, v in dict(headers or {}).items() if k.lower() != "content-length"}
        ra["x-web2api-route"] = model
        ra["x-web2api-combo"] = self.ten
        return ra

    def _ghi_nhan(self, id_: str) -> None:
        """Bước đã trả lời: nhật ký lượt gọi và tiêu đề X-Web2api-Module mang module THẬT."""
        if self.request is not None:
            self.request.state.module = id_
            self.request.state.tien_to_module = self.so.tien_to_cua(id_)

    # ── vòng chung ──

    async def _chay(self, viec: str, than: dict, goi: Callable[[Any, dict], Awaitable[tuple[int, dict, Any]]],
                    stream: bool = False) -> tuple[int, dict, Any]:
        hop_le, ly_do = self._hop_le(viec)
        if not hop_le:
            raise self._khong_con_buoc(viec, ly_do)
        x_user = self.request.headers.get("x-user") if self.request is not None else None
        khoa = bo_dinh_phien.lay_khoa_phien(than, x_user)
        hang = self._xep(hop_le, viec, khoa)
        loop = asyncio.get_running_loop()
        han = loop.time() + toi_da_giay()
        loi_400: tuple[int, dict, bytes, dict] | None = None  # (mã, tiêu đề, thân, bước) của 400 ĐẦU TIÊN
        loi_cuoi: tuple[int, dict, bytes, dict] | None = None
        da_thu = 0
        i = 0
        while i < len(hang):
            b = hang[i]
            i += 1
            id_, model = b["module"], b["model"]
            luot = None
            if not b["cuoi"]:
                luot = bo_cau_chi.chiem(id_, viec)
                if luot is None:  # cầu chì vừa mở trong chính lượt này / người khác đang thăm dò: dời xuống cuối
                    b["cuoi"] = True
                    hang.append(b)
                    continue
            if da_thu and loop.time() >= han:
                if luot == "tham_do":
                    bo_cau_chi.tha(id_, viec)
                raise self._het_gio()
            da_thu += 1
            ket_qua = None
            try:
                ket_qua = await self._thu(b, viec, than, goi, stream, han)
            except _Het:
                bo_cau_chi.record_failure(id_, viec, 504, "quá trần thời gian tổng của combo")
                raise self._het_gio() from None
            finally:
                if luot == "tham_do" and ket_qua is None:
                    bo_cau_chi.tha(id_, viec)
            ma, headers, noi_dung, dau = ket_qua
            loai = phan_loai(ma)

            if loai == "thanh_cong":
                bo_cau_chi.record_success(id_, viec)
                bo_dinh_phien.bind(self.ten, viec, khoa, model)
                if loi_400 is not None:
                    b400 = loi_400[3]
                    bo_han_muc.ha(b400["module"], b400["model"], viec, thoi_gian_nghi_cau_chi(), 400)
                    nhat_ky.ghi("combo", "canh_bao", f"Combo {self.ten}: bước {b400['model']} trả 400 trong khi "
                                f"bước {model} trả {ma} cho cùng yêu cầu — 400 thuộc bước ấy, hạ bậc trong «{viec}»",
                                combo=self.ten, route=b400["model"], viec=viec)
                self._ghi_nhan(id_)
                if dau is not None:
                    return ma, self._tieu_de(headers, model), self._phat(dau, noi_dung, id_, model, viec)
                return ma, self._tieu_de(headers, model), noi_dung

            if luot == "tham_do" and loai in ("phan_xu", "bo_qua", "het_han_muc", "yeu_cau_sai"):
                bo_cau_chi.tha(id_, viec)  # không kết luận được về sức khoẻ module
            than_loi = await _doc_than_loi(noi_dung)
            hien_tai = (ma, headers, than_loi, b)

            if loai == "phan_xu":
                if loi_400 is not None:  # hai bước khác nhau cùng 400 → lỗi của yêu cầu
                    return self._tra_loi(loi_400)
                loi_400 = hien_tai
                continue
            if loai == "yeu_cau_sai":
                return self._tra_loi(hien_tai)
            loi_cuoi = hien_tai
            if loai == "het_han_muc":
                giay = doc_retry_after(dict(headers or {}).get("retry-after"), time.time())
                bo_han_muc.ha(id_, model, viec, giay, ma)
                bo_dinh_phien.evict(self.ten, viec, khoa)
            elif loai == "khoa_hong":
                bo_cau_chi.mo_ngay(id_, viec, ma, than_loi.decode("utf-8", errors="replace"))
                bo_dinh_phien.evict(self.ten, viec, khoa)
            elif loai == "may_chu_loi":
                bo_cau_chi.record_failure(id_, viec, ma, than_loi.decode("utf-8", errors="replace"))
                bo_dinh_phien.evict(self.ten, viec, khoa)
            nhat_ky.ghi("combo", "canh_bao", f"Combo {self.ten}: bước {model} trả {ma} ({loai}), thử bước kế",
                        combo=self.ten, route=model, viec=viec, ma=ma)

        ket = loi_400 or loi_cuoi
        if ket is None:
            raise self._khong_con_buoc(viec, ly_do)
        return self._tra_loi(ket)

    async def _thu(self, b: dict, viec: str, than: dict, goi, stream: bool, han: float
                   ) -> tuple[int, dict, Any, bytes | None]:
        """Gọi MỘT bước trong trần thời gian còn lại (tới byte đầu nếu stream). Lỗi gọi/lỗi trước byte đầu → 502
        JSON của lõi; hết trần → _Het. Luồng bỏ dở luôn được đóng."""
        than_buoc = dict(than)  # bản sao nông: mỗi bước nhận thân gốc, không thấy sửa đổi của bước trước
        than_buoc["model"] = b["model"]
        noi_dung: Any = None
        cm = asyncio.timeout_at(han)
        try:
            async with cm:
                ma, headers, noi_dung = await goi(b["the_hien"], than_buoc)
                if stream and 200 <= ma < 300 and hasattr(noi_dung, "__aiter__"):
                    it = noi_dung.__aiter__()
                    noi_dung = it
                    try:
                        dau = await it.__anext__()
                    except StopAsyncIteration:
                        await _dong(it)
                        return ma, headers, b"", None
                    return ma, headers, it, dau
                return ma, headers, noi_dung, None
        except asyncio.CancelledError:  # khách ngắt khi đang chờ byte đầu: đóng thượng nguồn ngay, không chờ GC
            await _dong(noi_dung)
            raise
        except TimeoutError:
            await _dong(noi_dung)
            if cm.expired():
                raise _Het() from None
            return self._loi_goi(b, "TimeoutError")
        except Exception as e:
            await _dong(noi_dung)
            return self._loi_goi(b, type(e).__name__)

    def _loi_goi(self, b: dict, kieu: str) -> tuple[int, dict, bytes, None]:
        nhat_ky.ghi("combo", "canh_bao", f"Combo {self.ten}: bước {b['model']} lỗi khi gọi ({kieu})",
                    combo=self.ten, route=b["model"])
        loi = {"error": {"message": f"Bước {b['model']} của combo «{self.ten}» lỗi trước khi trả lời ({kieu})",
                         "type": "api_error", "code": "upstream_error"}}
        return 502, {"content-type": "application/json"}, json.dumps(loi, ensure_ascii=False).encode(), None

    def _het_gio(self) -> LoiOpenAI:
        return LoiOpenAI(504, f"Combo «{self.ten}» vượt trần thời gian tổng {toi_da_giay():g} giây "
                              "(WEB2API_COMBO_TOI_DA_GIAY)", code="combo_timeout",
                         headers={"x-web2api-combo": self.ten})

    def _tra_loi(self, ket: tuple[int, dict, bytes, dict]) -> tuple[int, dict, bytes]:
        ma, headers, than_loi, b = ket
        self._ghi_nhan(b["module"])
        return ma, self._tieu_de(headers, b["model"]), than_loi

    async def _phat(self, dau: bytes, it: Any, id_: str, model: str, viec: str):
        """Luồng đã chốt bước: lỗi sau byte đầu → sự kiện SSE lỗi chuẩn rồi kết thúc (như chuyen_tiep), không đổi
        bước; thượng nguồn luôn được aclose trong finally (khách ngắt, hết luồng hay lỗi)."""
        try:
            yield dau
            async for khuc in it:
                yield khuc
        except Exception as e:
            bo_cau_chi.record_failure(id_, viec, 502, f"luồng đứt sau byte đầu ({type(e).__name__})")
            nhat_ky.ghi("combo", "canh_bao", f"Combo {self.ten}: bước {model} đứt luồng sau byte đầu "
                        f"({type(e).__name__})", combo=self.ten, route=model, viec=viec)
            yield _su_kien_loi(f"Combo «{self.ten}», bước {model}: luồng đứt giữa chừng ({type(e).__name__})")
        finally:
            await _dong(it)

    # ── «phương thức module» ──

    async def chat(self, than: dict, stream: bool = False) -> tuple[int, dict, Any]:
        return await self._chay("chat", than, lambda m, t: m.chat(t, stream), stream)

    async def anh(self, than: dict) -> tuple[int, dict, Any]:
        return await self._chay("anh", than, lambda m, t: m.anh(t))
