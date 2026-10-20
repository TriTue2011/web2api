"""Module tien-ich-doi-song — tiện ích đời sống (chuẩn C2 thiet_bi).

Port 4/5 nguồn của c2a (LUẬT PORT 19/10, chỉ chép không viết lại):
  - services/digest.py      → digest.py    (bản tin tóm tắt: máy gom mục → gửi theo giờ)
  - services/chi_duong.py   → chi_duong.py (chỉ đường/tra địa điểm, Nominatim + OSRM)
  - services/vung_khoang_cach.py → vung.py (học vùng khoảng cách radar)
  - services/cham_cau.py    → cham_cau.py  (phục hồi dấu câu cho lời thoại)
  - services/trong_xe.py    → KHÔNG port (phụ thuộc camera/nhin-nha, ⚠ chờ chủ máy)
  nguồn c2a: /opt/claude-c2a/chatgpt2api (CHỈ ĐỌC), commit d879b03.

Thực thể (tiền tố `tienich`), cung cấp qua công cụ OpenAI function-calling:
  tienich.tin_tuc — bản tin: thêm mục, đếm chờ, tổng hợp, kiểm mốc giờ, tóm tắt.
  tienich.chi_duong — tìm đường A→B, xác nhận địa chỉ, tra địa điểm, tên từ toạ độ.
  tienich.vung — ngưỡng vùng radar: đặt ngưỡng, hỏi trong/ngoài vùng, học từ dữ liệu.
  tienich.dau_cau — kiểm tra chữ trần không dấu, chia lô cho chấm câu.

Lớp GỬI (kênh Zalo/Telegram của c2a) không port: `tong_hop`/`kiem_tra_moc_gio`
trả VĂN BẢN + danh sách đích, tác nhân (tro-ly) hoặc người gọi tự gửi.
`phuc_hoi` (chấm câu bằng model) giữ nguyên ở dạng hàm thư viện nhận
`goi_model` do caller tiêm — tool chỉ phơi phần thuần (kiểm tra, chia lô).

Riêng tư: địa chỉ/vị trí của người dùng là dữ liệu NHẠY CẢM — không log nội dung
(chỉ độ dài/số kết quả), gửi dịch vụ ngoài (Nominatim/OSRM) chỉ ở mức cần cho
truy vấn, cấu hình `bat_tim_duong` tắt toàn bộ gọi mạng ngoài.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from . import cham_cau, chi_duong, digest, vung

log = logging.getLogger("web2api.tien_ich_doi_song")

_TRAN_CHUOI = 4000        # trần ký tự cho một chuỗi đầu vào của tool
_TRAN_MANH = 200          # trần số mảnh của cham_cau / danh sách
_TRAN_DONG = 50           # trần số dòng tin cho một hành động
_TRAN_DIEM = 20000        # trần điểm chuỗi dữ liệu cho học vùng


def _cat(s: Any, tran: int = _TRAN_CHUOI) -> str:
    s = s if isinstance(s, str) else ""
    s = s.strip()
    return s[:tran]


class Module:
    """Module tiện ích đời sống cho web2api (chuẩn C2)."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "tienich")
        self._cau_hinh: dict[str, Any] = {}

    # ── Nạp / cài / sức khoẻ ──────────────────────────────────────────────

    def _du_lieu_dir(self) -> Path:
        goc = os.environ.get("WEB2API_DATA") or os.path.join(os.path.expanduser("~"), ".web2api")
        d = Path(goc) / "du_lieu" / "tien-ich-doi-song"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def nap(self, cau_hinh: dict[str, Any]) -> None:
        """Nạp cấu hình đã lưu (không gọi mạng)."""
        self._cau_hinh = cau_hinh or {}
        try:
            d = self._du_lieu_dir()
            digest.dat_duong_du_lieu(d)
            vung.dat_duong_du_lieu(d)
        except Exception as exc:
            log.warning("nap: không đặt được thư mục dữ liệu: %s", type(exc).__name__)

    def _bat_tim_duong(self) -> bool:
        return str(self._cau_hinh.get("bat_tim_duong", "1")).strip().lower() in ("1", "bat", "true", "co", "yes")

    async def cai(self, cau_hinh: dict[str, Any]) -> dict[str, Any]:
        """Kiểm tra cấu hình khi cài đặt module (không gọi mạng)."""
        btd = str((cau_hinh or {}).get("bat_tim_duong", "1")).strip().lower()
        ket = []
        if btd not in ("0", "1", "bat", "tat", "true", "false"):
            ket.append({"khoa": "bat_tim_duong", "ok": False,
                        "loi": "Chỉ nhận «1»/«0» (hoặc bat/tat)."})
        else:
            ket.append({"khoa": "bat_tim_duong", "ok": True})
        return {"ok": all(k["ok"] for k in ket), "ket_qua_tung_khoa": ket,
                "cau_hinh_luu": {"bat_tim_duong": btd if ket[0]["ok"] else "1"}}

    async def suc_khoe(self) -> dict[str, Any]:
        """Báo cáo sức khoẻ module (không gọi mạng)."""
        try:
            d = self._du_lieu_dir()
            ok = d.is_dir()
        except Exception:
            ok = False
        return {"ok": ok, "tim_duong_bat": self._bat_tim_duong(),
                "khoa": [], "base_url": None}

    async def dung(self) -> None:
        return None

    # ── Chuẩn thiet_bi (C2): công cụ cho AI ───────────────────────────────

    def _thuc_the_tin_tuc(self) -> dict[str, Any]:
        return {
            "id": f"{self.tien_to}.tin_tuc",
            "ten": "Bản tin tóm tắt",
            "loai": "cong_cu",
            "rui_ro_mac_dinh": "ghi",
            "tinh_nang": [
                {"hanh_dong": "them_muc", "mo_ta": "Thêm một mục tin vào bộ đệm của nguồn (chờ tới giờ gửi). ma_muc tuỳ chọn để chống trùng.",
                 "tham_so": {"type": "object", "required": ["nguon", "noi_dung"], "additionalProperties": False,
                             "properties": {"nguon": {"type": "string", "description": "Mã nguồn tin, vd «lich»", "maxLength": 100},
                                            "noi_dung": {"type": "string", "description": "Nội dung mục (tối đa 4000 ký tự)", "maxLength": 4000},
                                            "ma_muc": {"type": "string", "description": "Mã mục để chống gửi trùng (tuỳ chọn)", "maxLength": 200}}}},
                {"hanh_dong": "so_muc_dang_cho", "mo_ta": "Số mục đang chờ gửi của nguồn.",
                 "tham_so": {"type": "object", "required": ["nguon"], "additionalProperties": False,
                             "properties": {"nguon": {"type": "string", "maxLength": 100}}}},
                {"hanh_dong": "tong_hop", "mo_ta": "Gộp toàn bộ mục đang chờ của nguồn thành MỘT bản tin, trả văn bản để người gọi tự gửi (module không giữ kênh gửi).",
                 "tham_so": {"type": "object", "required": ["nguon"], "additionalProperties": False,
                             "properties": {"nguon": {"type": "string", "maxLength": 100},
                                            "tieu_de": {"type": "string", "description": "Tiêu đề bản tin (tuỳ chọn)", "maxLength": 200}}}},
                {"hanh_dong": "kiem_tra_moc_gio", "mo_ta": "Kiểm tra các mốc giờ định kỳ (HH:MM) của các nguồn, trả các bản tin đã tới giờ (mỗi mốc 1 lần/ngày).",
                 "tham_so": {"type": "object", "required": ["danh_sach"], "additionalProperties": False,
                             "properties": {"danh_sach": {"type": "array", "description": "Danh sách {nguon, gio_gui: [HH:MM], tieu_de?, dich?}", "maxItems": 20}}}},
                {"hanh_dong": "tom_tat", "mo_ta": "Tóm tắt văn bản bằng bản cắt gọn xác định (không gọi model, không bịa). Dùng cho tin dài trước khi xếp vào bản tin.",
                 "tham_so": {"type": "object", "required": ["noi_dung"], "additionalProperties": False,
                             "properties": {"noi_dung": {"type": "string", "description": "Văn bản cần tóm tắt", "maxLength": 12000},
                                            "loai": {"type": "string", "description": "Loại nội dung, vd «email»", "maxLength": 50},
                                            "toi_da": {"type": "integer", "description": "Số ký tự tối đa (mặc định 900)", "minimum": 50, "maximum": 4000}}}},
                {"hanh_dong": "quen_nguon", "mo_ta": "Xoá toàn bộ trạng thái (bộ đệm, đã xem, mốc đã bắn) của một nguồn.",
                 "tham_so": {"type": "object", "required": ["nguon"], "additionalProperties": False,
                             "properties": {"nguon": {"type": "string", "maxLength": 100}}}},
            ],
        }

    def _thuc_the_chi_duong(self) -> dict[str, Any]:
        return {
            "id": f"{self.tien_to}.chi_duong",
            "ten": "Chỉ đường & tra địa điểm",
            "loai": "cong_cu",
            "rui_ro_mac_dinh": "doc",
            "tinh_nang": [
                {"hanh_dong": "tim_duong", "mo_ta": "Chỉ đường A→B: km, phút, các bước tiếng Việt, link Google Maps. Miễn phí, không API key (Nominatim + OSRM).",
                 "tham_so": {"type": "object", "required": ["diem_di", "diem_den"], "additionalProperties": False,
                             "properties": {"diem_di": {"type": "string", "description": "Điểm đi (địa chỉ hoặc link ghim Maps)", "maxLength": 300},
                                            "diem_den": {"type": "string", "description": "Điểm đến", "maxLength": 300},
                                            "phuong_tien": {"type": "string", "description": "«xe máy» (mặc định) / «ô tô» / «đi bộ» / «xe buýt»", "maxLength": 20}}}},
                {"hanh_dong": "dinh_vi", "mo_ta": "Chỉ tra toạ độ hai đầu (bước xác nhận trước khi chỉ đường).",
                 "tham_so": {"type": "object", "required": ["diem_di", "diem_den"], "additionalProperties": False,
                             "properties": {"diem_di": {"type": "string", "maxLength": 300},
                                            "diem_den": {"type": "string", "maxLength": 300},
                                            "phuong_tien": {"type": "string", "maxLength": 20}}}},
                {"hanh_dong": "tim_dia_diem", "mo_ta": "Tìm địa điểm theo chuỗi tự do, trả danh sách lựa chọn (không tự chọn thay người dùng).",
                 "tham_so": {"type": "object", "required": ["dia_chi"], "additionalProperties": False,
                             "properties": {"dia_chi": {"type": "string", "maxLength": 300},
                                            "so_luong": {"type": "integer", "description": "Số kết quả (mặc định 8)", "minimum": 1, "maximum": 8}}}},
                {"hanh_dong": "ten_tu_toa_do", "mo_ta": "Toạ độ → địa chỉ chữ (để xác nhận ghim).",
                 "tham_so": {"type": "object", "required": ["vi_do", "kinh_do"], "additionalProperties": False,
                             "properties": {"vi_do": {"type": "number", "minimum": -90, "maximum": 90},
                                            "kinh_do": {"type": "number", "minimum": -180, "maximum": 180}}}},
            ],
        }

    def _thuc_the_vung(self) -> dict[str, Any]:
        return {
            "id": f"{self.tien_to}.vung",
            "ten": "Vùng khoảng cách radar",
            "loai": "cong_cu",
            "rui_ro_mac_dinh": "ghi",
            "tinh_nang": [
                {"hanh_dong": "dat_nguong", "mo_ta": "Chủ máy đặt ngưỡng khoảng cách cho radar (0–50, mét); bỏ trống để trả về số bot học.",
                 "tham_so": {"type": "object", "required": ["ma"], "additionalProperties": False,
                             "properties": {"ma": {"type": "string", "maxLength": 100},
                                            "nguong": {"type": ["number", "null"], "description": "Ngưỡng mét (0–50) hoặc null", "minimum": 0, "maximum": 50}}}},
                {"hanh_dong": "trong_vung", "mo_ta": "Số đo khoảng cách có thể là người trong khu không (chưa học đạt → luôn đúng, không tắt nhầm).",
                 "tham_so": {"type": "object", "required": ["ma", "gia_tri"], "additionalProperties": False,
                             "properties": {"ma": {"type": "string", "maxLength": 100},
                                            "gia_tri": {"type": ["number", "string"]}}}},
                {"hanh_dong": "vi_tri", "mo_ta": "Ba trạng thái: true = người trong vùng, false = ngoài vùng, null = không biết.",
                 "tham_so": {"type": "object", "required": ["ma", "gia_tri"], "additionalProperties": False,
                             "properties": {"ma": {"type": "string", "maxLength": 100},
                                            "gia_tri": {"type": ["number", "string"]}}}},
                {"hanh_dong": "danh_sach_vung", "mo_ta": "Liệt kê vùng đã học của mọi radar.",
                 "tham_so": {"type": "object", "additionalProperties": False, "properties": {}}},
                {"hanh_dong": "hoc_tu_du_lieu", "mo_ta": "Học vùng cho một radar từ dữ liệu lịch sử đã thu thập (chuỗi (thoi_diem, gia_tri)). Điểm nối thay cap_radar/hoc của c2a (cần lịch sử HA).",
                 "tham_so": {"type": "object", "required": ["ma", "khu", "radar", "du_lieu_khoang_cach", "du_lieu_radar"], "additionalProperties": False,
                             "properties": {
                                 "ma": {"type": "string", "maxLength": 100},
                                 "khu": {"type": "string", "maxLength": 100},
                                 "radar": {"type": "string", "description": "Mã cảm biến hiện diện cùng thiết bị", "maxLength": 100},
                                 "don_vi": {"type": "string", "description": "Đơn vị số đo (mặc định m)", "maxLength": 10},
                                 "du_lieu_khoang_cach": {"type": "array", "description": "Chuỗi [thoi_diem, gia_tri] của cảm biến khoảng cách", "maxItems": 20000},
                                 "du_lieu_radar": {"type": "array", "description": "Chuỗi [thoi_diem, on/off] của radar hiện diện", "maxItems": 20000},
                                 "du_lieu_cam": {"type": "array", "description": "Danh sách chuỗi (t, on/off) của camera trong khu", "maxItems": 10},
                                 "du_lieu_ke": {"type": "object", "description": "{mã cảm biến khu bên cạnh: chuỗi (t, on/off)}"}}}},
            ],
        }

    def _thuc_the_dau_cau(self) -> dict[str, Any]:
        return {
            "id": f"{self.tien_to}.dau_cau",
            "ten": "Phục hồi dấu câu",
            "loai": "cong_cu",
            "rui_ro_mac_dinh": "doc",
            "tinh_nang": [
                {"hanh_dong": "kiem_tra_dau_cau", "mo_ta": "Bản chép lời thoại có phải chữ trần không dấu câu không (để quyết định có cần phục hồi).",
                 "tham_so": {"type": "object", "required": ["cac_manh"], "additionalProperties": False,
                             "properties": {"cac_manh": {"type": "array", "description": "Danh sách mảnh văn bản", "maxItems": 200,
                                                        "items": {"type": "string", "maxLength": 5000}}}}},
                {"hanh_dong": "chia_lo", "mo_ta": "Chia mảnh văn bản thành các lô theo ký tự để gọi model chấm câu từng lô.",
                 "tham_so": {"type": "object", "required": ["cac_manh"], "additionalProperties": False,
                             "properties": {"cac_manh": {"type": "array", "maxItems": 200,
                                                        "items": {"type": "string", "maxLength": 5000}}}}},
            ],
        }

    async def thuc_the(self, loc: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Khai báo 4 thực thể công cụ."""
        return [self._thuc_the_tin_tuc(), self._thuc_the_chi_duong(),
                self._thuc_the_vung(), self._thuc_the_dau_cau()]

    async def trang_thai(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Báo trạng thái các thực thể (chỉ thực thể module thực có)."""
        ra: dict[str, dict[str, Any]] = {}
        luc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        da_biet = {f"{self.tien_to}.tin_tuc", f"{self.tien_to}.chi_duong",
                   f"{self.tien_to}.vung", f"{self.tien_to}.dau_cau"}
        for eid in ids or []:
            if eid in da_biet:
                ra[eid] = {"gia_tri": "san_sang", "don_vi": None, "luc": luc, "hop_le": True}
        return ra

    # ── Xử lý hành động ──────────────────────────────────────────────────

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict[str, Any] | None) -> dict[str, Any]:
        """Thực thi công cụ. Trả {ok, ket_qua?} hoặc {ok: False, loi}."""
        tham_so = tham_so or {}
        tien_to = self.tien_to + "."
        if not id.startswith(tien_to):
            return {"ok": False, "loi": f"Không tìm thấy thực thể {id!r}"}
        phan = id[len(tien_to):]
        try:
            if phan == "tin_tuc":
                return await self._tin_tuc(hanh_dong, tham_so)
            if phan == "chi_duong":
                return await self._chi_duong(hanh_dong, tham_so)
            if phan == "vung":
                return await self._vung(hanh_dong, tham_so)
            if phan == "dau_cau":
                return await self._dau_cau(hanh_dong, tham_so)
            return {"ok": False, "loi": f"Không tìm thấy thực thể {id!r}"}
        except ValueError as e:
            return {"ok": False, "loi": str(e)}
        except Exception:
            log.warning("thuc_hien lỗi (%s/%s)", id, hanh_dong)
            return {"ok": False, "loi": "Lỗi nội bộ khi thực thi công cụ"}

    # ── tin_tuc ──────────────────────────────────────────────────────────

    async def _tin_tuc(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        if hanh_dong == "them_muc":
            nguon = _cat(ts.get("nguon"), 100)
            noi_dung = _cat(ts.get("noi_dung"))
            if not nguon or not noi_dung:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «nguon»/«noi_dung»"}
            ma_muc = _cat(ts.get("ma_muc"), 200) if ts.get("ma_muc") else ""
            if ma_muc and digest.seen(nguon, ma_muc):
                return {"ok": True, "ket_qua": {"trung": True, "so_muc_cho": digest.pending_count(nguon)}}
            if ma_muc:
                digest.mark_seen(nguon, ma_muc)
            digest.queue(nguon, noi_dung)
            return {"ok": True, "ket_qua": {"trung": False, "so_muc_cho": digest.pending_count(nguon)}}
        if hanh_dong == "so_muc_dang_cho":
            nguon = _cat(ts.get("nguon"), 100)
            if not nguon:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «nguon»"}
            return {"ok": True, "ket_qua": {"nguon": nguon, "so_muc_cho": digest.pending_count(nguon)}}
        if hanh_dong == "tong_hop":
            nguon = _cat(ts.get("nguon"), 100)
            if not nguon:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «nguon»"}
            tieu_de = _cat(ts.get("tieu_de"), 200)
            ban = digest.flush(nguon, title=tieu_de)
            return {"ok": True, "ket_qua": ban}
        if hanh_dong == "kiem_tra_moc_gio":
            ds = ts.get("danh_sach")
            if not isinstance(ds, list) or not ds:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «danh_sach» (danh sách)"}
            nguon_list = []
            for muc in ds[:_TRAN_DONG]:
                if not isinstance(muc, dict):
                    continue
                nguon = _cat(muc.get("nguon"), 100)
                if not nguon:
                    continue
                gio = [str(g) for g in (muc.get("gio_gui") or [])][:10]
                tieu_de = _cat(muc.get("tieu_de"), 200)
                dich = [str(d) for d in (muc.get("dich") or [])][:10]
                nguon_list.append((nguon, {"notify_times": gio, "notify_targets": dich}, tieu_de))
            return {"ok": True, "ket_qua": {"ban_tin": digest.tick(nguon_list)}}
        if hanh_dong == "tom_tat":
            noi_dung = _cat(ts.get("noi_dung"), 12000)
            if not noi_dung:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «noi_dung»"}
            loai = _cat(ts.get("loai"), 50) or "email"
            try:
                toi_da = int(ts.get("toi_da", 900))
            except (TypeError, ValueError):
                toi_da = 900
            toi_da = max(50, min(toi_da, 4000))
            return {"ok": True, "ket_qua": {"tom_tat": digest.summarize(noi_dung, what=loai, max_chars=toi_da)}}
        if hanh_dong == "quen_nguon":
            nguon = _cat(ts.get("nguon"), 100)
            if not nguon:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «nguon»"}
            digest.forget(nguon)
            return {"ok": True, "ket_qua": {"nguon": nguon, "da_quen": True}}
        return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ ở tin_tuc"}

    # ── chi_duong ────────────────────────────────────────────────────────

    def _can_chi_duong(self) -> dict[str, Any] | None:
        if not self._bat_tim_duong():
            return {"ok": False, "loi": "Tính năng tìm đường đã tắt (cấu hình «bat_tim_duong» = 0)."}
        return None

    async def _chi_duong(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        tat = self._can_chi_duong()
        if tat:
            return tat
        if hanh_dong == "tim_duong":
            diem_di = _cat(ts.get("diem_di"), 300)
            diem_den = _cat(ts.get("diem_den"), 300)
            if not diem_di or not diem_den:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «diem_di»/«diem_den»"}
            phuong_tien = _cat(ts.get("phuong_tien"), 20) or "xe máy"
            return {"ok": True, "ket_qua": chi_duong.chi_duong(diem_di, diem_den, phuong_tien)}
        if hanh_dong == "dinh_vi":
            diem_di = _cat(ts.get("diem_di"), 300)
            diem_den = _cat(ts.get("diem_den"), 300)
            if not diem_di or not diem_den:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «diem_di»/«diem_den»"}
            phuong_tien = _cat(ts.get("phuong_tien"), 20) or "xe máy"
            return {"ok": True, "ket_qua": chi_duong.dinh_vi(diem_di, diem_den, phuong_tien)}
        if hanh_dong == "tim_dia_diem":
            dia_chi = _cat(ts.get("dia_chi"), 300)
            if not dia_chi:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «dia_chi»"}
            try:
                so = int(ts.get("so_luong", 8))
            except (TypeError, ValueError):
                so = 8
            so = max(1, min(so, 8))
            return {"ok": True, "ket_qua": {"dia_chi": dia_chi,
                                            "ket_qua": chi_duong.tim_dia_diem(dia_chi, so=so)}}
        if hanh_dong == "ten_tu_toa_do":
            try:
                vi_do = float(ts["vi_do"])
                kinh_do = float(ts["kinh_do"])
            except (TypeError, ValueError, KeyError):
                return {"ok": False, "loi": "Tham số «vi_do»/«kinh_do» phải là số"}
            if not (-90 <= vi_do <= 90 and -180 <= kinh_do <= 180):
                return {"ok": False, "loi": "Toạ độ ngoài khoảng hợp lệ"}
            return {"ok": True, "ket_qua": {"vi_do": vi_do, "kinh_do": kinh_do,
                                            "ten": chi_duong._ten_tu_toa_do(vi_do, kinh_do)}}
        return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ ở chi_duong"}

    # ── vung ─────────────────────────────────────────────────────────────

    @staticmethod
    def _chuoi_diem(ds: Any) -> list[tuple[float, str]] | None:
        if not isinstance(ds, list):
            return None
        ra = []
        for muc in ds[:_TRAN_DIEM]:
            if isinstance(muc, (list, tuple)) and len(muc) == 2:
                try:
                    ra.append((float(muc[0]), str(muc[1])))
                except (TypeError, ValueError):
                    return None
            else:
                return None
        return ra

    async def _vung(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        if hanh_dong == "dat_nguong":
            ma = _cat(ts.get("ma"), 100)
            if not ma:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «ma»"}
            nguong = ts.get("nguong")
            if nguong is not None:
                try:
                    nguong = float(nguong)
                except (TypeError, ValueError):
                    return {"ok": False, "loi": "«nguong» phải là số (0–50) hoặc null"}
            return {"ok": True, "ket_qua": vung.dat_nguong(ma, nguong)}
        if hanh_dong in ("trong_vung", "vi_tri"):
            ma = _cat(ts.get("ma"), 100)
            if not ma:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «ma»"}
            gia_tri = ts.get("gia_tri")
            kq = vung.trong_vung(ma, gia_tri) if hanh_dong == "trong_vung" else vung.vi_tri(ma, gia_tri)
            return {"ok": True, "ket_qua": {"ma": ma, "gia_tri": kq}}
        if hanh_dong == "danh_sach_vung":
            return {"ok": True, "ket_qua": {"vung": vung.ds()}}
        if hanh_dong == "hoc_tu_du_lieu":
            ma = _cat(ts.get("ma"), 100)
            khu = _cat(ts.get("khu"), 100)
            radar = _cat(ts.get("radar"), 100)
            if not ma or not khu or not radar:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «ma»/«khu»/«radar»"}
            don_vi = _cat(ts.get("don_vi"), 10) or "m"
            kc = self._chuoi_diem(ts.get("du_lieu_khoang_cach"))
            r_ct = self._chuoi_diem(ts.get("du_lieu_radar"))
            if kc is None or r_ct is None or not kc or not r_ct:
                return {"ok": False, "loi": "«du_lieu_khoang_cach»/«du_lieu_radar» phải là danh sách [thoi_diem, gia_tri]"}
            cam_ct = []
            for c in (ts.get("du_lieu_cam") or [])[:10]:
                s = self._chuoi_diem(c)
                if s is None:
                    return {"ok": False, "loi": "«du_lieu_cam» sai hình dạng"}
                cam_ct.append(s)
            ke = ts.get("du_lieu_ke") or {}
            if not isinstance(ke, dict):
                return {"ok": False, "loi": "«du_lieu_ke» phải là đối tượng {mã: chuỗi}"}
            ke_ct = {}
            for k, c in list(ke.items())[:10]:
                s = self._chuoi_diem(c)
                if s is None:
                    return {"ok": False, "loi": f"«du_lieu_ke[{k}]» sai hình dạng"}
                ke_ct[str(k)] = s
            return {"ok": True, "ket_qua": vung.hoc_tu_du_lieu(ma, khu, radar, don_vi, kc, r_ct, cam_ct, ke_ct)}
        return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ ở vung"}

    # ── dau_cau ──────────────────────────────────────────────────────────

    async def _dau_cau(self, hanh_dong: str, ts: dict[str, Any]) -> dict[str, Any]:
        if hanh_dong in ("kiem_tra_dau_cau", "chia_lo"):
            manh = ts.get("cac_manh")
            if not isinstance(manh, list) or not manh:
                return {"ok": False, "loi": "Thiếu tham số bắt buộc «cac_manh» (danh sách)"}
            manh = [str(m)[:5000] for m in manh[:_TRAN_MANH]]
            if hanh_dong == "kiem_tra_dau_cau":
                return {"ok": True, "ket_qua": {"thieu_dau_cau": cham_cau.thieu_dau_cau(manh)}}
            return {"ok": True, "ket_qua": {"lo": cham_cau._chia_lo(manh)}}
        return {"ok": False, "loi": f"Hành động «{hanh_dong}» không hỗ trợ ở dau_cau"}

    # ── Gọi trực tiếp tiện ích (cho test) ─────────────────────────────────

    def digest_tom_tat(self, noi_dung: str, **kw) -> str:
        return digest.summarize(noi_dung, **kw)
