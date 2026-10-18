"""Module home-assistant — nguồn THIẾT BỊ NHÀ cho chuẩn C1 qua REST API của Home Assistant.

Đọc trạng thái từ ``GET /api/states``, điều khiển qua ``POST /api/services/<miền>/<dịch vụ>``,
lấy khu vực qua ``POST /api/template`` (area_name theo lô). Bảo mật:

- Địa chỉ do quản trị khai đi qua ĐÚNG bộ kiểm ``app/tu_xa.py`` (``kiem_url_lan``):
  cho LAN riêng tư, chặn loopback/link-local/không định tuyến; mọi kết nối ghim IP
  đã kiểm chống DNS rebinding qua ``TransportGhimIp`` của lõi.
- Miền/dịch vụ gửi lên HA lấy từ BẢNG ÁNH XẠ NỘI BỘ theo hành động — không bao giờ
  ghép từ chuỗi người dùng; entity_id phải thuộc danh sách module vừa liệt kê.
- Token Long-Lived chỉ đi trong header ``Authorization``, không vào log/URL/phản hồi/ngoại lệ.
- Miền chạy mã tuỳ ý (script, automation, shell_command, rest_command, python_script,
  hassio) KHÔNG BAO GIỜ được điều khiển, kể cả khi người dùng cấu hình cho phép.
- /api/states có trần đọc cứng; tối đa 5000 thực thể; tên/thuộc tính từ HA là KHÔNG TIN
  (làm sạch ký tự điều khiển, cắt độ dài); trạng thái có bộ đệm 20 giây.
"""

from __future__ import annotations

import json
import logging
import time
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.chuyen_tiep import PhanHoiQuaLon, TransportGhimIp, doc_phan_hoi_co_tran
from app.tu_xa import _dia_chi_bi_chan, kiem_url_lan

log = logging.getLogger("web2api.home_assistant")

#: Trần đọc thân /api/states (có thể >1 MB): đọc luồng, vượt trần → bỏ an toàn.
TRAN_PHAN_HOI = 8 * 1024 * 1024
#: Số thực thể tối đa trả về một lượt liệt kê (vượt cắt + ghi log).
SO_THUC_THE_TOI_DA = 5000
#: Bộ đệm trạng thái (giây) — không dội HA mỗi lần hỏi.
DEM_TRANG_THAI_GIAY = 20
#: Bộ đệm khu vực (giây).
DEM_KHU_GIAY = 600
#: Số entity_id tối đa trong một lời gọi /api/template.
TEMPLATE_LOT = 100

#: Nhóm miền mặc định được phép ĐIỀU KHIỂN (an toàn nhỏ).
NHOM_MAC_DINH = ["light", "switch", "fan", "scene", "media_player", "input_boolean", "climate"]

#: Miền chạy mã tuỳ ý — KHÔNG BAO GIỜ điều khiển qua module này (kể cả khi cấu hình).
MIEN_CAM_DIEU_KHIEN = frozenset(
    {"script", "automation", "shell_command", "rest_command", "python_script", "hassio"}
)

#: Thuộc tính HA được giữ lại (chống phình và rò thông tin — KHÔNG chép nguyên attributes).
THUOC_TINH_AN_TOAN = frozenset({"device_class", "unit_of_measurement"})

#: Trạng thái HA coi là không hợp lệ / không khả dụng.
TRANG_THAI_HONG = frozenset({"unavailable", "unknown", "none", ""})


def _luoc_do(thuoc_tinh: dict, bat_buoc: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "required": bat_buoc or [],
        "additionalProperties": False,
        "properties": thuoc_tinh,
    }


#: BẢNG ÁNH XẠ hành động theo miền: {miền: [{hanh_dong, mo_ta, dich_vu, tham_so, anh_xa}]}.
#: - dich_vu: tên dịch vụ HA (POST /api/services/<miền>/<dịch_vu>).
#: - tham_so: lược đồ JSON Schema (None = không nhận tham số).
#: - anh_xa: tên tham số của web2api → tên trường HA trong thân dịch vụ.
_KHA_NANG_MIEN: dict[str, list[dict]] = {
    "light": [
        {"hanh_dong": "bat", "mo_ta": "Bật đèn", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat", "mo_ta": "Tắt đèn", "dich_vu": "turn_off", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "chinh_sang", "mo_ta": "Chỉnh độ sáng 0–100",
         "dich_vu": "turn_on",
         "tham_so": _luoc_do({"muc_sang": {"type": "integer", "minimum": 0, "maximum": 100}}, ["muc_sang"]),
         "anh_xa": {"muc_sang": "brightness_pct"}},
    ],
    "switch": [
        {"hanh_dong": "bat", "mo_ta": "Bật công tắc", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat", "mo_ta": "Tắt công tắc", "dich_vu": "turn_off", "tham_so": None, "anh_xa": {}},
    ],
    "fan": [
        {"hanh_dong": "bat", "mo_ta": "Bật quạt", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat", "mo_ta": "Tắt quạt", "dich_vu": "turn_off", "tham_so": None, "anh_xa": {}},
    ],
    "input_boolean": [
        {"hanh_dong": "bat", "mo_ta": "Bật", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat", "mo_ta": "Tắt", "dich_vu": "turn_off", "tham_so": None, "anh_xa": {}},
    ],
    "scene": [
        {"hanh_dong": "kich_hoat", "mo_ta": "Kích hoạt cảnh", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
    ],
    "media_player": [
        {"hanh_dong": "phat", "mo_ta": "Phát", "dich_vu": "media_play", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tam_dung", "mo_ta": "Tạm dừng", "dich_vu": "media_pause", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "dung", "mo_ta": "Dừng", "dich_vu": "media_stop", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "chinh_am_luong", "mo_ta": "Chỉnh âm lượng 0–100",
         "dich_vu": "volume_set",
         "tham_so": _luoc_do({"muc": {"type": "integer", "minimum": 0, "maximum": 100}}, ["muc"]),
         "anh_xa": {"muc": "volume_level"}},
    ],
    "climate": [
        {"hanh_dong": "dat_nhiet_do", "mo_ta": "Đặt nhiệt độ (trong biên của thiết bị)",
         "dich_vu": "set_temperature",
         "tham_so": None,  # lược đồ theo biên từng thực thể, dựng ở thuc_the()
         "anh_xa": {"muc": "temperature"}},
    ],
    "cover": [
        {"hanh_dong": "mo", "mo_ta": "Mở", "dich_vu": "open_cover", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "dong", "mo_ta": "Đóng", "dich_vu": "close_cover", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "dung", "mo_ta": "Dừng", "dich_vu": "stop_cover", "tham_so": None, "anh_xa": {}},
    ],
    "lock": [
        {"hanh_dong": "khoa", "mo_ta": "Khoá", "dich_vu": "lock", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "mo_khoa", "mo_ta": "Mở khoá", "dich_vu": "unlock", "tham_so": None, "anh_xa": {}},
    ],
    "alarm_control_panel": [
        {"hanh_dong": "kich_hoat", "mo_ta": "Kích hoạt báo động", "dich_vu": "alarm_arm_away",
         "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat_che_do", "mo_ta": "Tắt báo động", "dich_vu": "alarm_disarm",
         "tham_so": None, "anh_xa": {}},
    ],
    "water_heater": [
        {"hanh_dong": "bat", "mo_ta": "Bật bình nóng lạnh", "dich_vu": "turn_on", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "tat", "mo_ta": "Tắt bình nóng lạnh", "dich_vu": "turn_off", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "dat_nhiet_do", "mo_ta": "Đặt nhiệt độ (trong biên của thiết bị)",
         "dich_vu": "set_temperature", "tham_so": None, "anh_xa": {"muc": "temperature"}},
    ],
    "valve": [
        {"hanh_dong": "mo", "mo_ta": "Mở van", "dich_vu": "open_valve", "tham_so": None, "anh_xa": {}},
        {"hanh_dong": "dong", "mo_ta": "Đóng van", "dich_vu": "close_valve", "tham_so": None, "anh_xa": {}},
    ],
}

#: Miền nguy hiểm theo cấu trúc (bếp/bình nóng lạnh/khoá: không bao giờ tự làm).
_MIEN_NGUY_HIEM = frozenset({"lock", "alarm_control_panel", "water_heater", "valve"})

#: device_class của cover coi là nguy hiểm (cửa ra vào/cổng — theo cấu trúc, không theo tên).
_COVER_NGUY_HIEM = frozenset({"garage", "gate", "door"})


def _sach_chu(s, toi_da: int = 100) -> str:
    """Làm sạch chuỗi từ HA (không tin): bỏ ký tự điều khiển, cắt độ dài."""
    ra = "".join(ch for ch in str(s or "") if not unicodedata.category(ch).startswith("C"))
    return ra.strip()[:toi_da]


def _rui_ro_mien(mien: str, device_class: str | None) -> str:
    """Mức rủi ro theo CẤU TRÚC miền (+ device_class cho cover); thiếu thông tin → nguy_hiem."""
    if mien in _MIEN_NGUY_HIEM:
        return "nguy_hiem"
    if mien == "cover" and (device_class or "") in _COVER_NGUY_HIEM:
        return "nguy_hiem"
    if mien in _KHA_NANG_MIEN:
        return "ghi"
    # Miền chỉ đọc hoặc chưa biết mà không có hành động nào → chỉ đọc, không đổi được gì.
    return "doc"


def _kiem_tham_so(luoc_do: dict | None, tham_so: dict) -> str | None:
    """Kiểm tham số theo lược đồ trước khi gửi; trả None nếu đạt, câu lỗi nếu không."""
    if luoc_do is None:
        if tham_so:
            return "hành động này không nhận tham số"
        return None
    if not isinstance(tham_so, dict):
        return "tham_so phải là đối tượng"
    props = luoc_do.get("properties", {})
    for k in luoc_do.get("required", []):
        if k not in tham_so:
            return f"thiếu tham số bắt buộc «{k}»"
    if luoc_do.get("additionalProperties") is False:
        la = [k for k in tham_so if k not in props]
        if la:
            return f"tham số lạ: {', '.join(sorted(la))}"
    for k, v in tham_so.items():
        spec = props.get(k)
        if not spec:
            continue
        kieu = spec.get("type")
        if kieu == "integer" and not (isinstance(v, int) and not isinstance(v, bool)):
            return f"«{k}» phải là số nguyên"
        if kieu == "number" and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            return f"«{k}» phải là số"
        if kieu == "string" and not isinstance(v, str):
            return f"«{k}» phải là chuỗi"
        if kieu == "boolean" and not isinstance(v, bool):
            return f"«{k}» phải là đúng/sai"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in spec and v < spec["minimum"]:
                return f"«{k}»={v} nhỏ hơn biên {spec['minimum']}"
            if "maximum" in spec and v > spec["maximum"]:
                return f"«{k}»={v} lớn hơn biên {spec['maximum']}"
        if "enum" in spec and v not in spec["enum"]:
            return f"«{k}» không nằm trong tập cho phép"
    return None


# ── Nối thuật toán khớp nhanh từ khop_nhanh.py (NV131) ────────
from .khop_nhanh import (
    ChiMucUngVien,
    KetQuaKhop,
    khop_thiet_bi,
    giai_thich_khop,
    kiem_kha_thi_ung_vien,
    normalize,
    _char_ngrams,
    _jaccard,
    _seq_ratio,
    _word_sim,
    _bm25_scores,
    _intent_affinity,
    _tokens_aligned,
    W_WORD,
    W_CHAR,
    W_BM25,
    W_INTENT,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_MARGIN,
    PREFILTER_TOP,
)


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to") or "home_assistant"
        self._dia_chi = ""
        self._token = ""
        self._kiem_tls = True
        self._nhom: list[str] = list(NHOM_MAC_DINH)
        self._tu_khoa_goi: list[str] = []
        self._chi_muc_ung_vien = ChiMucUngVien()
        self._khach: httpx.AsyncClient | None = None
        self._ban_do_thuc_the: dict[str, dict] = {}  # entity_id HA → bản ghi nội bộ
        self._dem_states: tuple[float, list] | None = None
        self._dem_khu: dict[str, tuple[float, str]] = {}

    # ── cấu hình ──────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._khach is None:
            timeout = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
            self._khach = httpx.AsyncClient(
                transport=TransportGhimIp(_dia_chi_bi_chan),  # ghim IP đã kiểm, chống DNS rebinding
                timeout=timeout,
                verify=self._kiem_tls,
            )
        return self._khach

    def _tieu_de(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _tach_nhom(gia_tri) -> list[str]:
        if isinstance(gia_tri, str):
            return [m.strip().lower() for m in gia_tri.split(",") if m.strip()]
        if isinstance(gia_tri, list):
            return [str(m).strip().lower() for m in gia_tri if str(m).strip()]
        return list(NHOM_MAC_DINH)

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng, không log token."""
        cau_hinh = cau_hinh or {}
        self._dia_chi = str(cau_hinh.get("dia_chi") or "").strip().rstrip("/")
        self._token = str(cau_hinh.get("token") or "")
        tls = str(cau_hinh.get("kiem_chung_chi_tls", "true")).strip().lower()
        self._kiem_tls = tls not in ("false", "0", "khong", "không", "no", "off")
        self._nhom = self._tach_nhom(cau_hinh.get("nhom_duoc_dieu_khien")) or list(NHOM_MAC_DINH)
        tu_khoa = cau_hinh.get("tu_khoa_goi")
        if isinstance(tu_khoa, str):
            self._tu_khoa_goi = [k.strip() for k in tu_khoa.split(",") if k.strip()]
        elif isinstance(tu_khoa, list):
            self._tu_khoa_goi = [str(k).strip() for k in tu_khoa if str(k).strip()]
        else:
            self._tu_khoa_goi = []
        self._khach = None  # cấu hình đổi → bỏ client cũ (TLS/địa chỉ mới)
        self._dem_states = None
        self._dem_khu = {}

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình: địa chỉ qua bộ kiểm lõi + gọi thử /api/ với token."""
        cau_hinh = cau_hinh or {}
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip().rstrip("/")
        token = str(cau_hinh.get("token") or "")
        if not dia_chi:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập địa chỉ Home Assistant (dia_chi)."}
        if not token:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Chưa nhập token truy cập (token)."}
        try:
            dia_chi = await kiem_url_lan(dia_chi)  # ĐÚNG bộ kiểm app/tu_xa.py, không viết bộ thứ hai
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": f"Địa chỉ không hợp lệ: {e}"}
        tls = str(cau_hinh.get("kiem_chung_chi_tls", "true")).strip().lower()
        kiem_tls = tls not in ("false", "0", "khong", "không", "no", "off")
        try:
            async with httpx.AsyncClient(
                transport=TransportGhimIp(_dia_chi_bi_chan),
                timeout=httpx.Timeout(10.0, connect=5.0),
                verify=kiem_tls,
            ) as khach:
                r = await khach.get(f"{dia_chi}/api/", headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": f"Không kết nối được Home Assistant: {type(e).__name__}"}
        if r.status_code == 401:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": "Token không đúng hoặc hết hạn (máy chủ trả 401)."}
        if r.status_code != 200:
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": f"Máy chủ trả HTTP {r.status_code} tại /api/."}
        cau_hinh_luu = {
            "dia_chi": dia_chi,
            "token": token,
            "kiem_chung_chi_tls": "true" if kiem_tls else "false",
            "nhom_duoc_dieu_khien": ",".join(self._tach_nhom(cau_hinh.get("nhom_duoc_dieu_khien")) or list(NHOM_MAC_DINH)),
            "tu_khoa_goi": ",".join(self._tu_khoa_goi),
        }
        self.nap(cau_hinh_luu)  # nạp ngay để thể hiện dùng được sau cai()
        return {
            "ok": True,
            "ket_qua_tung_khoa": [],
            "loi": None,
            "cau_hinh_luu": cau_hinh_luu,
        }

    async def suc_khoe(self) -> dict:
        if not self._dia_chi or not self._token:
            return {"ok": False, "loi": "Module Home Assistant chưa được cấu hình"}
        try:
            r = await self._client().get(f"{self._dia_chi}/api/", headers=self._tieu_de())
        except httpx.HTTPError as e:
            return {"ok": False, "loi": f"Không kết nối được: {type(e).__name__}"}
        if r.status_code != 200:
            return {"ok": False, "loi": f"Máy chủ trả HTTP {r.status_code}"}
        return {"ok": True, "dia_chi": self._dia_chi}

    async def dung(self) -> None:
        if self._khach is not None:
            await self._khach.aclose()
            self._khach = None

    # ── lấy dữ liệu HA ────────────────────────────────────────

    async def _lay_states(self) -> list | None:
        """GET /api/states có trần đọc luồng; None = lỗi/vượt trần (bỏ an toàn)."""
        if not self._dia_chi or not self._token:
            return []
        now = time.monotonic()
        if self._dem_states and now - self._dem_states[0] < DEM_TRANG_THAI_GIAY:
            return self._dem_states[1]
        try:
            req = self._client().build_request("GET", f"{self._dia_chi}/api/states", headers=self._tieu_de())
            r = await self._client().send(req, stream=True)
            if r.status_code != 200:
                log.warning("HA trả HTTP %s tại /api/states", r.status_code)
                return None
            noi_dung = await doc_phan_hoi_co_tran(r, tran_bytes=TRAN_PHAN_HOI)
        except PhanHoiQuaLon:
            log.warning("Phản hồi /api/states vượt trần %s bytes — bỏ", TRAN_PHAN_HOI)
            return None
        except (httpx.HTTPError, TimeoutError, ValueError) as e:
            log.warning("Lỗi khi lấy /api/states: %s", type(e).__name__)
            return None
        try:
            ds = json.loads(noi_dung)
        except ValueError:
            log.warning("/api/states không phải JSON")
            return None
        if not isinstance(ds, list):
            log.warning("/api/states không trả danh sách")
            return None
        self._dem_states = (now, ds)
        return ds

    async def _lay_khu_vuc(self, entity_ids: list[str]) -> dict[str, str]:
        """area_name theo lô qua /api/template, có trần + bộ đệm; lỗi → chuỗi rỗng."""
        now = time.monotonic()
        kq = {}
        can = []
        for eid in entity_ids:
            dem = self._dem_khu.get(eid)
            if dem and now - dem[0] < DEM_KHU_GIAY:
                kq[eid] = dem[1]
            else:
                can.append(eid)
        for i in range(0, len(can), TEMPLATE_LOT):
            lot = can[i:i + TEMPLATE_LOT]
            mau = "\n".join(f"{{{{ area_name('{eid}') }}}}" for eid in lot)
            try:
                r = await self._client().post(
                    f"{self._dia_chi}/api/template",
                    headers=self._tieu_de(),
                    json={"template": mau},
                )
                if r.status_code != 200:
                    raise httpx.HTTPError(f"HTTP {r.status_code}")
                j = r.json()
                dong = str(j.get("result", "")).split("\n")
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("Lỗi lấy khu vực: %s", type(e).__name__)
                dong = []
            for eid, gia_tri in zip(lot, dong):
                khu = _sach_chu(gia_tri, 60)
                # HA trả "None"/"" khi không có khu vực → để trống
                if khu.lower() in ("none", "null", ""):
                    khu = ""
                self._dem_khu[eid] = (now, khu)
                kq[eid] = khu
        for eid in can:
            kq.setdefault(eid, "")
        return kq

    # ── chuẩn thiet_bi ────────────────────────────────────────

    def _tinh_nang_cua(self, mien: str, attrs: dict) -> list[dict]:
        """Dựng tinh_nang từ BẢNG DỮ LIỆU theo miền (lược đồ climate theo biên thiết bị)."""
        ra = []
        for muc in _KHA_NANG_MIEN.get(mien, []):
            muc = dict(muc)
            if mien == "climate" and muc["hanh_dong"] == "dat_nhiet_do":
                try:
                    t_min = float(attrs.get("min_temp", 7))
                except (TypeError, ValueError):
                    t_min = 7.0
                try:
                    t_max = float(attrs.get("max_temp", 35))
                except (TypeError, ValueError):
                    t_max = 35.0
                muc["tham_so"] = _luoc_do(
                    {"muc": {"type": "number", "minimum": t_min, "maximum": t_max,
                             "description": f"Nhiệt độ {t_min}–{t_max}"}},
                    ["muc"],
                )
            khai = {"hanh_dong": muc["hanh_dong"], "mo_ta": muc["mo_ta"]}
            if muc.get("tham_so"):
                khai["tham_so"] = muc["tham_so"]
            ra.append(khai)
        return ra

    async def thuc_the(self, loc=None) -> list[dict]:
        states = await self._lay_states()
        if states is None:
            return []
        if len(states) > SO_THUC_THE_TOI_DA:
            log.warning("HA có %s thực thể, vượt trần %s — cắt", len(states), SO_THUC_THE_TOI_DA)
            states = states[:SO_THUC_THE_TOI_DA]
        hop_le = []
        for s in states:
            if not isinstance(s, dict):
                continue
            eid = str(s.get("entity_id") or "")
            if "." in eid:
                hop_le.append((eid, s if isinstance(s.get("attributes"), dict) else {**s, "attributes": {}}))
        khu_vuc = await self._lay_khu_vuc([eid for eid, _ in hop_le])
        ra = []
        self._ban_do_thuc_the = {}
        for eid, s in hop_le:
            mien = eid.split(".", 1)[0]
            attrs = s["attributes"]
            device_class = attrs.get("device_class")
            device_class = _sach_chu(device_class, 40) if device_class else None
            don_vi = attrs.get("unit_of_measurement")
            don_vi = _sach_chu(don_vi, 20) if don_vi else None
            thuoc_tinh = {}
            if device_class:
                thuoc_tinh["device_class"] = device_class
            if don_vi:
                thuoc_tinh["unit_of_measurement"] = don_vi
            tinh_nang = self._tinh_nang_cua(mien, attrs)
            muc = {
                "id": f"{self.tien_to}.{eid}",  # giữ nguyên dấu chấm HA → đảo ngược được
                "ten": _sach_chu(attrs.get("friendly_name") or eid, 100),
                "loai": mien,  # mã mở
                "khu": khu_vuc.get(eid, ""),
                "thuoc_tinh": thuoc_tinh,
                "tinh_nang": tinh_nang,
                "rui_ro_mac_dinh": _rui_ro_mien(mien, device_class),
            }
            ra.append(muc)
            self._ban_do_thuc_the[eid] = {
                "mien": mien,
                "tinh_nang": tinh_nang,
                "device_class": device_class,
                "trang_thai": s.get("state"),
            }
        return ra

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        states = await self._lay_states()
        if not states:
            return {}
        bang = {}
        for s in states:
            if not isinstance(s, dict):
                continue
            eid = str(s.get("entity_id") or "")
            attrs = s.get("attributes") if isinstance(s.get("attributes"), dict) else {}
            gia_tri = s.get("state")
            hop_le = str(gia_tri).strip().lower() not in TRANG_THAI_HONG
            bang[eid] = {
                "gia_tri": gia_tri,
                "don_vi": _sach_chu(attrs.get("unit_of_measurement"), 20) or None,
                "luc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "hop_le": bool(hop_le),
            }
        kq = {}
        for mid in ids or []:
            if not isinstance(mid, str) or not mid.startswith(f"{self.tien_to}."):
                continue
            eid = mid.split(".", 1)[1]
            if eid in bang:
                kq[mid] = bang[eid]
        return kq

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        """Chỉ gọi POST /api/services/<miền>/<dịch vụ> với miền/dịch vụ từ BẢNG NỘI BỘ."""
        tham_so = tham_so or {}
        if not isinstance(id, str) or not id.startswith(f"{self.tien_to}."):
            return {"ok": False, "loi": "id thực thể không hợp lệ"}
        eid = id.split(".", 1)[1]
        if not self._ban_do_thuc_the:
            await self.thuc_the(None)  # nạp danh sách để kiểm entity thuộc module
        ban_ghi = self._ban_do_thuc_the.get(eid)
        if ban_ghi is None:
            return {"ok": False, "loi": "không thấy thực thể trong danh sách của module"}
        mien = ban_ghi["mien"]
        if mien in MIEN_CAM_DIEU_KHIEN:
            return {"ok": False, "loi": f"miền «{mien}» không bao giờ được điều khiển qua module này"}
        if mien not in self._nhom:
            return {"ok": False, "loi": f"miền «{mien}» nằm ngoài nhóm được điều khiển"}
        muc = next((x for x in _KHA_NANG_MIEN.get(mien, [])
                    if x["hanh_dong"] == hanh_dong), None)
        if muc is None:
            return {"ok": False, "loi": f"hành động «{hanh_dong}» không hỗ trợ cho miền «{mien}»"}
        # climate: lược đồ theo biên thiết bị (dựng lại như ở thuc_the)
        luoc_do = muc.get("tham_so")
        if mien == "climate" and hanh_dong == "dat_nhiet_do":
            luoc_do = self._luoc_do_dat_nhiet_do(eid)
        loi_ts = _kiem_tham_so(luoc_do, tham_so)
        if loi_ts:
            return {"ok": False, "loi": f"tham số không hợp lệ: {loi_ts}"}
        than = {"entity_id": eid}
        for k, v in tham_so.items():
            ten_ha = muc["anh_xa"].get(k, k)
            if k == "muc" and hanh_dong == "chinh_am_luong":
                v = v / 100.0
            than[ten_ha] = v
        try:
            r = await self._client().post(
                f"{self._dia_chi}/api/services/{mien}/{muc['dich_vu']}",  # từ BẢNG, không từ chuỗi người dùng
                headers=self._tieu_de(),
                json=than,
            )
        except (httpx.HTTPError, TimeoutError) as e:
            log.warning("Lỗi gọi dịch vụ HA: %s", type(e).__name__)
            return {"ok": False, "loi": "không gọi được dịch vụ của Home Assistant"}
        if r.status_code not in (200, 201):
            return {"ok": False, "loi": "Home Assistant từ chối thực hiện"}
        return {"ok": True}

    def _luoc_do_dat_nhiet_do(self, eid: str) -> dict:
        states = self._dem_states[1] if self._dem_states else []
        attrs = {}
        for s in states:
            if isinstance(s, dict) and s.get("entity_id") == eid and isinstance(s.get("attributes"), dict):
                attrs = s["attributes"]
                break
        try:
            t_min = float(attrs.get("min_temp", 7))
        except (TypeError, ValueError):
            t_min = 7.0
        try:
            t_max = float(attrs.get("max_temp", 35))
        except (TypeError, ValueError):
            t_max = 35.0
        return _luoc_do(
            {"muc": {"type": "number", "minimum": t_min, "maximum": t_max,
                     "description": f"Nhiệt độ {t_min}–{t_max}"}},
            ["muc"],
        )

    # ── khớp thiết bị nhanh & chẩn đoán (NV131) ────────────────

    def kiem_kha_thi(self, id: str, hanh_dong: str) -> tuple[bool, str | None]:
        """Kiểm khả thi trước khi chạy theo nguyên tắc cấu trúc:
        thực thể tồn tại, không cấm, trạng thái khả dụng, hành động hỗ trợ.
        """
        if not isinstance(id, str):
            return False, "id thực thể không hợp lệ"
        eid = id.split(".", 1)[1] if id.startswith(f"{self.tien_to}.") else id
        if self._ban_do_thuc_the:
            ban_ghi = self._ban_do_thuc_the.get(eid)
            if ban_ghi is None:
                return False, "thực thể không tồn tại trong danh sách của module"

            trang_thai = ban_ghi.get("trang_thai")
            if trang_thai is None and self._dem_states and isinstance(self._dem_states[1], list):
                for s in self._dem_states[1]:
                    if isinstance(s, dict) and s.get("entity_id") == eid:
                        trang_thai = s.get("state")
                        break

            ent_check = {
                "id": id,
                "loai": ban_ghi.get("mien") or (eid.split(".", 1)[0] if "." in eid else ""),
                "tinh_nang": ban_ghi.get("tinh_nang") or [],
                "trang_thai": trang_thai,
            }
            return kiem_kha_thi_ung_vien(
                ent_check,
                hanh_dong,
                mien_cam=MIEN_CAM_DIEU_KHIEN,
                kha_nang_mien=_KHA_NANG_MIEN,
                trang_thai_hong=TRANG_THAI_HONG,
                nhom_duoc_dieu_khien=self._nhom,
            )

        # Nếu _ban_do_thuc_the chưa có (chưa gọi thuc_the), kiểm tra theo cấu trúc miền
        loai = eid.split(".", 1)[0] if "." in eid else ""
        ent_check = {"id": id, "loai": loai}
        return kiem_kha_thi_ung_vien(
            ent_check,
            hanh_dong,
            mien_cam=MIEN_CAM_DIEU_KHIEN,
            kha_nang_mien=_KHA_NANG_MIEN,
            trang_thai_hong=TRANG_THAI_HONG,
            nhom_duoc_dieu_khien=self._nhom,
        )

    async def khop_thiet_bi(
        self,
        cau: str,
        ung_vien: list[dict] | None = None,
        *,
        min_confidence: float = 0.55,
        min_margin: float = 0.08,
        tu_khoa_goi: list[str] | None = None,
        ngon_ngu: str = "vi",
    ) -> KetQuaKhop:
        """Khớp câu nói với thiết bị Home Assistant mà không cần lượt LLM."""
        if ung_vien is None:
            ung_vien = await self.thuc_the(None)
        kw = self._tu_khoa_goi if tu_khoa_goi is None else tu_khoa_goi
        return khop_thiet_bi(
            cau,
            ung_vien,
            module=self,
            tu_khoa_goi=kw,
            min_confidence=min_confidence,
            min_margin=min_margin,
            ngon_ngu=ngon_ngu,
            chi_muc=self._chi_muc_ung_vien,
            mien_cam=MIEN_CAM_DIEU_KHIEN,
            kha_nang_mien=_KHA_NANG_MIEN,
            trang_thai_hong=TRANG_THAI_HONG,
            tinh_rui_ro=_rui_ro_mien,
            nhom_duoc_dieu_khien=self._nhom,
        )

    async def giai_thich_khop(
        self,
        cau: str,
        ung_vien: list[dict] | None = None,
        *,
        ngon_ngu: str = "vi",
    ) -> dict:
        """Chẩn đoán giải thích chi tiết điểm từng tín hiệu."""
        if ung_vien is None:
            ung_vien = await self.thuc_the(None)
        return giai_thich_khop(
            cau,
            ung_vien,
            module=self,
            ngon_ngu=ngon_ngu,
            chi_muc=self._chi_muc_ung_vien,
            mien_cam=MIEN_CAM_DIEU_KHIEN,
            kha_nang_mien=_KHA_NANG_MIEN,
            trang_thai_hong=TRANG_THAI_HONG,
            tinh_rui_ro=_rui_ro_mien,
            nhom_duoc_dieu_khien=self._nhom,
        )
