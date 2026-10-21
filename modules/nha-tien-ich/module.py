"""Module nha-tien-ich — khuôn module web2api cho 7 thực thể công cụ nhà thông minh.

Port c2a (commit d879b03): ``services/kich_ban_nha.py``, ``services/kich_hoat_nha.py``,
``services/tinh_huong_nha.py``, ``services/canh_bao_nha.py``, ``services/co_nguoi_nha.py``,
``services/dien_nha.py``, ``services/su_co_thiet_bi.py``, ``services/cam_bien_ghep.py``,
``services/cam_bien_ket.py``, ``services/do_tin_cam_bien.py``.

Khuôn C1 chuẩn (giống ``modules/hoc-nha/module.py``): module KHÔNG import trực
tiếp ``home-assistant`` hay ``hoc-nha`` — mọi đọc/ghi thiết bị qua ``co_che``
(tiêm hàm trong test; đường lõi ``app.thiet_bi`` khi chạy thật).
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("web2api.nha_tien_ich")

from . import (_duong_dan, cam_bien, canh_bao, co_che, co_nguoi, dien,
               do_tin, hang_rao, kich_ban, su_co, tinh_huong)

TIEN_TO = "nhatienich"

TT_KICH_BAN = f"{TIEN_TO}.kich_ban"
TT_CANH_BAO = f"{TIEN_TO}.canh_bao"
TT_TINH_HUONG = f"{TIEN_TO}.tinh_huong"
TT_DIEN = f"{TIEN_TO}.dien"
TT_CO_NGUOI = f"{TIEN_TO}.co_nguoi"
TT_CAM_BIEN = f"{TIEN_TO}.cam_bien"
TT_SU_CO = f"{TIEN_TO}.su_co"


def _sach_chu(gia_tri: Any, toi_da: int = 200) -> str:
    """Làm sạch chuỗi nhập: bỏ ký tự điều khiển và cặp < > (chống XSS khi
    tên/mô tả hiển thị trên giao diện web)."""
    if gia_tri is None:
        return ""
    s = str(gia_tri)
    s = "".join(c for c in s if (c.isprintable() or c in (" ", "\n", "\t"))
                and c not in ("<", ">"))
    return s[:toi_da]


def _sach_id(gia_tri: Any, toi_da: int = 60) -> str:
    s = _sach_chu(gia_tri, toi_da).strip()
    return "".join(c for c in s if c.isalnum() or c in ("-", "_", "."))


def _bool_tu_cau_hinh(gia_tri: Any, mac_dinh: bool = True) -> bool:
    if isinstance(gia_tri, bool):
        return gia_tri
    if isinstance(gia_tri, (int, float)):
        return gia_tri != 0
    return str(gia_tri).strip().lower() in ("true", "1", "co", "có", "yes", "on")


def _luoc_do(tinh_nang: list[dict], hanh_dong: str) -> dict | None:
    for tn in tinh_nang:
        if tn.get("hanh_dong") == hanh_dong:
            return tn.get("tham_so")
    return None


class Module:
    """Khuôn module web2api cho nhà tiện ích."""

    def __init__(self, manifest: dict):
        self.manifest = manifest or {}
        self.tien_to = self.manifest.get("tien_to") or TIEN_TO
        self._hang_rao_bat = True
        self._nut = ""

    # ── cấu hình / vòng đời ─────────────────────────────────────

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng."""
        cau_hinh = cau_hinh or {}
        self._hang_rao_bat = _bool_tu_cau_hinh(cau_hinh.get("hang_rao_bat", "1"), True)
        self._nut = _sach_chu(cau_hinh.get("nut", ""), 120)
        try:
            dien.dat_dia_chi(self._nut)
        except Exception:
            dien.dat_dia_chi("")

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình: ``nut`` qua bộ kiểm LAN (chống SSRF); không gọi mạng.

        Trả ``cau_hinh_luu`` — lõi lưu vào kho (bài học NV184: thiếu trường này
        là module chạy như chưa cấu hình).
        """
        cau_hinh = cau_hinh or {}
        ket_qua_tung_khoa: list[dict] = []
        nut = _sach_chu(cau_hinh.get("nut", ""), 120).strip()
        nut_luu = ""
        if nut:
            try:
                nut_luu = await dien.kiem_dia_chi(nut)  # chặn loopback/link-local (SSRF)
            except (ValueError, OSError) as e:
                return {"ok": False, "ket_qua_tung_khoa": ket_qua_tung_khoa,
                        "loi": f"Địa chỉ NUT không hợp lệ: {e}"}
        ket_qua_tung_khoa.append({"khoa": "nut", "ok": True})
        hang_rao_bat = _bool_tu_cau_hinh(cau_hinh.get("hang_rao_bat", "1"), True)
        ket_qua_tung_khoa.append({"khoa": "hang_rao_bat", "ok": True})
        cau_hinh_luu = {"nut": nut_luu,
                        "hang_rao_bat": "1" if hang_rao_bat else "0"}
        self.nap(cau_hinh_luu)
        return {"ok": True, "ket_qua_tung_khoa": ket_qua_tung_khoa,
                "loi": None, "cau_hinh_luu": cau_hinh_luu}

    async def suc_khoe(self) -> dict:
        return {"ok": True, "hang_rao_bat": self._hang_rao_bat,
                "nut": "da_cau_hinh" if self._nut else "chua_cau_hinh",
                "kho_du_lieu": str(_duong_dan.goc())}

    async def dung(self) -> None:
        return None

    # ── chuẩn C1 ────────────────────────────────────────────────

    async def thuc_the(self, loc=None) -> list[dict]:
        return [
            {
                "id": TT_KICH_BAN, "ten": "Kịch bản nhà (thử khô mặc định)",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "dinh_nghia", "mo_ta": "Định nghĩa kịch bản {id, ten, buoc[], mo_ta}",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["id", "buoc"],
                                 "additionalProperties": True,
                                 "properties": {
                                     "id": {"type": "string", "minLength": 1, "maxLength": 60},
                                     "ten": {"type": "string", "maxLength": 80},
                                     "buoc": {"type": "array", "minItems": 1},
                                     "mo_ta": {"type": "string", "maxLength": 500}}}},
                    {"hanh_dong": "xoa", "mo_ta": "Xoá một kịch bản theo id",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["id"],
                                 "additionalProperties": False,
                                 "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 60}}}},
                    {"hanh_dong": "danh_sach", "mo_ta": "Liệt kê các kịch bản đã định nghĩa",
                     "rui_ro": "doc"},
                    {"hanh_dong": "xem_truoc", "mo_ta": "THỬ KHÔ: trả «sẽ làm gì» cho từng bước, không chạy gì",
                     "rui_ro": "doc",
                     "tham_so": {"type": "object", "required": ["id"],
                                 "additionalProperties": False,
                                 "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 60}}}},
                    {"hanh_dong": "chay", "mo_ta": "Chạy kịch bản (cần xác nhận của quản trị; bước chạm thiết bị nguy hiểm không bao giờ chạy)",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["id"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "id": {"type": "string", "minLength": 1, "maxLength": 60},
                                     "xac_nhan": {"type": "boolean"},
                                     "hang_rao_bat": {"type": "boolean"}}}},
                ],
                "rui_ro_mac_dinh": "ghi",
            },
            {
                "id": TT_CANH_BAO, "ten": "Cảnh báo nhà (chống dội)",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "gui", "mo_ta": "Gửi cảnh báo {thiet_bi, loai, muc_do, noi_dung} — chống dội tự động",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["thiet_bi", "loai", "muc_do", "noi_dung"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "thiet_bi": {"type": "string", "minLength": 1, "maxLength": 120},
                                     "loai": {"type": "string", "minLength": 1, "maxLength": 60},
                                     "muc_do": {"type": "string", "enum": ["thap", "trung_binh", "cao", "khan_cap"]},
                                     "noi_dung": {"type": "string", "minLength": 1, "maxLength": 1000}}}},
                    {"hanh_dong": "het", "mo_ta": "Đánh dấu vấn đề đã hết (xoá khỏi đang báo)",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["thiet_bi", "loai"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "thiet_bi": {"type": "string", "minLength": 1, "maxLength": 120},
                                     "loai": {"type": "string", "minLength": 1, "maxLength": 60}}}},
                    {"hanh_dong": "dang_bao", "mo_ta": "Các cảnh báo đang phát", "rui_ro": "doc"},
                    {"hanh_dong": "lich_su", "mo_ta": "Lịch sử cảnh báo", "rui_ro": "doc"},
                ],
                "rui_ro_mac_dinh": "ghi",
            },
            {
                "id": TT_TINH_HUONG, "ten": "Tình huống nhà (học từ dữ liệu)",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "ghi", "mo_ta": "Ghi một sự kiện cảm biến {thiet_bi, gia_tri} (do_ai=1: do AI tự gây, học bỏ qua)",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["thiet_bi", "gia_tri"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "thiet_bi": {"type": "string", "minLength": 1, "maxLength": 120},
                                     "gia_tri": {"type": "string", "maxLength": 120},
                                     "do_ai": {"type": "integer", "minimum": 0, "maximum": 1}}}},
                    {"hanh_dong": "hoc", "mo_ta": "Học lại tình huống từ dữ liệu {so_ngay}",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object",
                                 "additionalProperties": False,
                                 "properties": {"so_ngay": {"type": "integer", "minimum": 1, "maximum": 90}}}},
                    {"hanh_dong": "tinh_huong_hien_tai", "mo_ta": "Tình huống đã học mà ô 30 phút hiện tại rơi vào",
                     "rui_ro": "doc"},
                ],
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": TT_DIEN, "ten": "Điện nhà (đọc UPS qua NUT)",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "chay_mot_lan", "mo_ta": "Đọc UPS một lần, cập nhật sổ, xếp tin vào hàng đợi",
                     "rui_ro": "ghi"},
                    {"hanh_dong": "trang_thai", "mo_ta": "Trạng thái điện/sổ UPS (không gọi mạng)",
                     "rui_ro": "doc"},
                    {"hanh_dong": "hang_doi", "mo_ta": "Tin cảnh báo điện đang chờ gửi", "rui_ro": "doc"},
                ],
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": TT_CO_NGUOI, "ten": "Có người trong nhà",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "hien_tai", "mo_ta": "Có người trong nhà lúc này không («co/khong/khong_ro»)",
                     "rui_ro": "doc"},
                ],
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": TT_CAM_BIEN, "ten": "Cảm biến ghép + độ tin cảm biến",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "dat", "mo_ta": "Định nghĩa cảm biến ghép {ma, ten, bieu_thuc{va|hoac|khong}, loai}",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["ma", "bieu_thuc"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "ma": {"type": "string", "minLength": 1, "maxLength": 60},
                                     "ten": {"type": "string", "maxLength": 80},
                                     "bieu_thuc": {"type": "object"},
                                     "loai": {"type": "string", "maxLength": 30}}}},
                    {"hanh_dong": "xoa", "mo_ta": "Xoá cảm biến ghép theo mã",
                     "rui_ro": "ghi",
                     "tham_so": {"type": "object", "required": ["ma"],
                                 "additionalProperties": False,
                                 "properties": {"ma": {"type": "string", "minLength": 1, "maxLength": 60}}}},
                    {"hanh_dong": "hien_tai", "mo_ta": "Giá trị hiện tại các cảm biến ghép", "rui_ro": "doc"},
                    {"hanh_dong": "danh_sach", "mo_ta": "Danh sách cảm biến ghép đã định nghĩa", "rui_ro": "doc"},
                ],
                "rui_ro_mac_dinh": "doc",
            },
            {
                "id": TT_SU_CO, "ten": "Chẩn đoán sự cố thiết bị",
                "loai": "cong_cu",
                "tinh_nang": [
                    {"hanh_dong": "chan_doan", "mo_ta": "Chẩn đoán vì sao thiết bị không theo lệnh/mất kết nối",
                     "rui_ro": "doc",
                     "tham_so": {"type": "object", "required": ["thiet_bi"],
                                 "additionalProperties": False,
                                 "properties": {
                                     "thiet_bi": {"type": "string", "minLength": 1, "maxLength": 120},
                                     "sau_lenh": {"type": "string", "maxLength": 120}}}},
                ],
                "rui_ro_mac_dinh": "doc",
            },
        ]

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        kq: dict[str, dict] = {}
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for mid in ids or []:
            if mid == TT_KICH_BAN:
                kq[mid] = {"gia_tri": f"{len(kich_ban.danh_sach())} kịch bản",
                           "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_CANH_BAO:
                kq[mid] = {"gia_tri": f"{len(canh_bao.dang_bao())} đang báo",
                           "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_TINH_HUONG:
                kq[mid] = {"gia_tri": f"{len(tinh_huong.tinh_huong_hien_tai())} tình huống hiện tại",
                           "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_DIEN:
                kq[mid] = {"gia_tri": "da_cau_hinh" if self._nut else "chua_cau_hinh",
                           "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_CO_NGUOI:
                try:
                    kq[mid] = {"gia_tri": co_nguoi.hien_tai().get("trang_thai", "khong_ro"),
                               "don_vi": None, "luc": luc, "hop_le": True}
                except Exception:
                    kq[mid] = {"gia_tri": "khong_ro", "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_CAM_BIEN:
                kq[mid] = {"gia_tri": f"{len(cam_bien.ds())} cảm biến ghép",
                           "don_vi": None, "luc": luc, "hop_le": True}
            elif mid == TT_SU_CO:
                kq[mid] = {"gia_tri": "san_sang", "don_vi": None, "luc": luc, "hop_le": True}
        return kq

    async def thuc_hien(self, id: str, hanh_dong: str,
                        tham_so: dict | None) -> dict:
        tham_so = dict(tham_so or {})
        try:
            if id == TT_KICH_BAN:
                return self._kich_ban(hanh_dong, tham_so)
            if id == TT_CANH_BAO:
                return self._canh_bao(hanh_dong, tham_so)
            if id == TT_TINH_HUONG:
                return self._tinh_huong(hanh_dong, tham_so)
            if id == TT_DIEN:
                return self._dien(hanh_dong, tham_so)
            if id == TT_CO_NGUOI:
                return self._co_nguoi(hanh_dong, tham_so)
            if id == TT_CAM_BIEN:
                return self._cam_bien(hanh_dong, tham_so)
            if id == TT_SU_CO:
                return self._su_co(hanh_dong, tham_so)
        except ValueError as e:
            return {"ok": False, "loi": "tham_so_sai", "chi_tiet": str(e)[:200]}
        except (RuntimeError, OSError) as e:
            log.warning("thuc_hien %s.%s lỗi: %s", id, hanh_dong, type(e).__name__)
            return {"ok": False, "loi": "loi_thuc_hien"}
        return {"ok": False, "loi": "khong_thay_thuc_the"}

    # ── hành động từng thực thể ──────────────────────────────────

    def _kich_ban(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "dinh_nghia":
            kb_id = _sach_id(ts.get("id"))
            buoc = ts.get("buoc")
            if not isinstance(buoc, list):
                raise ValueError("buoc phải là mảng")
            buoc_sach = []
            for b in buoc[:50]:
                if not isinstance(b, dict):
                    continue
                buoc_sach.append({"thuc_the": _sach_chu(b.get("thuc_the"), 120),
                                  "hanh_dong": _sach_chu(b.get("hanh_dong"), 60),
                                  "tham_so": {str(k)[:40]: _sach_chu(v, 200)
                                              for k, v in (b.get("tham_so") or {}).items()
                                              if isinstance(k, str)} if isinstance(b.get("tham_so"), dict) else {}})
            kb = kich_ban.dinh_nghia(kb_id, _sach_chu(ts.get("ten"), 80),
                                     buoc_sach, _sach_chu(ts.get("mo_ta"), 500))
            return {"ok": True, "kich_ban": kb}
        if hanh_dong == "xoa":
            return {"ok": kich_ban.xoa(_sach_id(ts.get("id")))}
        if hanh_dong == "danh_sach":
            return {"ok": True, "danh_sach": kich_ban.danh_sach()}
        if hanh_dong == "xem_truoc":
            return kich_ban.xem_truoc(_sach_id(ts.get("id")))
        if hanh_dong == "chay":
            # Hàng rào 1: xác nhận của quản trị bắt buộc (chính kich_ban.chay
            # kiểm lại vai qua co_che.la_quan_tri — defense in depth).
            xac_nhan = ts.get("xac_nhan") is True
            hr = self._hang_rao_bat and ts.get("hang_rao_bat", True) is not False
            return kich_ban.chay(_sach_id(ts.get("id")), xac_nhan=xac_nhan,
                                 hang_rao_bat=hr)
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _canh_bao(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "gui":
            muc_do = _sach_chu(ts.get("muc_do"), 20).lower()
            if muc_do not in hang_rao.MUC_DO:
                raise ValueError(f"muc_do phải một trong {list(hang_rao.MUC_DO)}")
            return canh_bao.gui(_sach_chu(ts.get("thiet_bi"), 120),
                                _sach_chu(ts.get("loai"), 60), muc_do,
                                _sach_chu(ts.get("noi_dung"), 1000))
        if hanh_dong == "het":
            return {"ok": canh_bao.het(_sach_chu(ts.get("thiet_bi"), 120),
                                       _sach_chu(ts.get("loai"), 60))}
        if hanh_dong == "dang_bao":
            return {"ok": True, "dang_bao": canh_bao.dang_bao()}
        if hanh_dong == "lich_su":
            return {"ok": True, "lich_su": canh_bao.lich_su()[-50:]}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _tinh_huong(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "ghi":
            tinh_huong.ghi(_sach_chu(ts.get("thiet_bi"), 120),
                           _sach_chu(ts.get("gia_tri"), 120),
                           do_ai=1 if ts.get("do_ai") else 0)
            return {"ok": True}
        if hanh_dong == "hoc":
            so_ngay = ts.get("so_ngay", 14)
            so_ngay = 14 if not isinstance(so_ngay, int) else max(1, min(90, so_ngay))
            return {"ok": True, "tinh_huong": tinh_huong.hoc(so_ngay)}
        if hanh_dong == "tinh_huong_hien_tai":
            return {"ok": True, "tinh_huong": tinh_huong.tinh_huong_hien_tai()}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _dien(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "chay_mot_lan":
            return {"ok": True, "tin_moi": dien.chay_mot_lan()}
        if hanh_dong == "trang_thai":
            so = dien.trang_thai()
            so["nut"] = "da_cau_hinh" if so.get("nut") else "chua_cau_hinh"
            return {"ok": True, "trang_thai": so}
        if hanh_dong == "hang_doi":
            return {"ok": True, "hang_doi": dien.lay_hang_doi()}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _co_nguoi(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "hien_tai":
            return {"ok": True, **co_nguoi.hien_tai()}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _cam_bien(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "dat":
            bt = ts.get("bieu_thuc")
            if not isinstance(bt, dict):
                raise ValueError("bieu_thuc phải là đối tượng {va|hoac|khong}")
            try:
                ra = cam_bien.dat(_sach_id(ts.get("ma")), _sach_chu(ts.get("ten"), 80),
                                  bt, _sach_chu(ts.get("loai") or "occupancy", 30))
            except ValueError as e:
                raise ValueError(str(e))
            return {"ok": True, "cam_bien": ra}
        if hanh_dong == "xoa":
            return {"ok": cam_bien.xoa(_sach_id(ts.get("ma")))}
        if hanh_dong == "hien_tai":
            return {"ok": True, "cam_bien": cam_bien.hien_tai()}
        if hanh_dong == "danh_sach":
            return {"ok": True, "danh_sach": [{"ma": k, **v} for k, v in cam_bien.ds().items()]}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}

    def _su_co(self, hanh_dong: str, ts: dict) -> dict:
        if hanh_dong == "chan_doan":
            return {"ok": True,
                    "chan_doan": su_co.chan_doan(_sach_chu(ts.get("thiet_bi"), 120),
                                                 sau_lenh=_sach_chu(ts.get("sau_lenh"), 120))}
        return {"ok": False, "loi": "hanh_dong_khong_ho_tro"}
