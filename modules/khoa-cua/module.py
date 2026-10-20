"""Module khoa-cua — khoá cửa thông minh (chuẩn C1 `thiet_bi`).

TẬN DỤNG từ c2a `services/khoa_cua_nha.py` (commit d879b03, CHỈ ĐỌC — bản sao
~/workspace/c2a-nv146). CHÉP (không viết lại):
- Bảng cách mở `_CACH_MO` («vân tay số 11», «khuôn mặt số 17»…).
- `_ma()` / `_mo_ta_ma()` — khoá định danh một người «fingerprint#11».
- Học tên người từ mã vân tay: sổ tên cục bộ CRUD (tên Tuya ưu tiên khi có);
  hỏi tối đa _HOI_TOI_DA lần — ở đây phơi qua hành động, không tự gửi tin.
- Đường nhật ký mở/đóng Tuya door-lock/open-logs + gộp trùng trong 5 giây
  (lớp KhoaDamMay trong dam_may.py).
KHÔNG port: nếp ngủ nhà (gio_di_ngu — cần lịch sử Home Assistant, web2api
không có dịch vụ này), vòng nhịp 15 giây + heartbeat, gửi thông báo qua
thong_bao, sổ tên dùng chung so_ten_nha của c2a (web2api không có).

HÀNG RÀO AN TOÀN (bắt buộc của NV169):
- Hành động mở khoá LUÔN «nguy_hiem» theo CẤU TRÚC (manifest + thuc_the khai,
  không theo danh sách tên). Cổng rủi ro của lõi (SEC-004) bắt quản trị +
  «xac_nhan: true», không thì 409; khách/mô hình vào hàng chờ phê duyệt, KHÔNG
  bao giờ chạy trực tiếp.
- Ngoài cổng lõi, module bắt thêm cửa sổ xác nhận lại (step-up) của RIÊNG
  mình: quản trị gọi «chuan_bi_mo» → nhận mã xác nhận dùng MỘT LẦN, gắn đúng
  quản trị đó, hết hạn 120 giây. «mo_khoa» thiếu mã/hết hạn/khác người/chủ
  cũ gửi lại → từ chối. Hai lệnh mở song song cùng một mã: đúng một lệnh
  chạy (khoá asyncio).
- Điều khiển (mở/đóng) mặc định TẮT (cho_phep_dieu_khien=«tat»); khoá API đám
  mây kiểu bi_mat, không vào log/phản hồi/suc_khoe; lỗi thiết bị chỉ nêu tên
  kiểu, không chép thân phản hồi (có thể chứa khoá).
- Mọi lần mở/đóng + cấp mã xác nhận đều ghi vào nhật ký BẤT BIẾN
  (nhat-ky-bat-bien.jsonl, 0600, chỉ ghi thêm — module không có hành động nào
  xoá/sửa nó): ai, từ đâu (nguồn ngữ cảnh), lúc nào.
- Tên học được là dữ liệu cá nhân: lưu 0600 (so-ten.json), không log, xoá được
  (xoa_ten_ma), KHÔNG dùng làm bằng chứng để mở khoá.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import kho
from app.kham_pha import doc_manifest  # noqa: F401  (giữ import theo khuôn module)

log = logging.getLogger("web2api.module.khoa_cua")

#: Cửa sổ xác nhận lại của module (giây) — «có hạn hiệu lực ngắn cho mỗi lần xác nhận».
HET_HAN_MA_XAC_NHAN = 120.0
#: Nhịp kiểm sự kiện mặc định (giây).
NHIP_SU_KIEN_MAC_DINH = 15.0
#: Hỏi tên tối đa cho MỘT mã — CHÉP c2a (nài thêm chỉ làm phiền).
_HOI_TOI_DA = 3

#: Mã lệnh mở khoá của Tuya. `unlock_` + cách mở. CHÉP c2a.
_CACH_MO = {
    "fingerprint": "vân tay", "face": "khuôn mặt", "password": "mật khẩu",
    "card": "thẻ từ", "key": "chìa cơ", "temporary": "mã tạm",
    "dynamic": "mã động", "hand": "vân tay", "finger_vein": "tĩnh mạch ngón",
    "phone": "điện thoại", "app": "điện thoại",
}


def _ma(code: str, value: Any) -> str:
    """Khoá định danh một người: 'fingerprint#11'. CHÉP c2a."""
    return f"{str(code or '').replace('unlock_', '')}#{value}"


def _mo_ta_ma(ma: str) -> str:
    """'fingerprint#11' → 'vân tay số 11'. CHÉP c2a."""
    cach, _, so = ma.partition("#")
    return f"{_CACH_MO.get(cach, cach)} số {so}"


def _nguoi_goi_la_quan_tri() -> tuple[bool, str, str]:
    """(là_quan_tri, id_quan_tri, nguon). Thiếu ngữ cảnh/lỗi → (False,…) đóng khi lỗi."""
    try:
        from app.ngu_canh import lay  # chỉ đọc, không gọi sâu vào lõi

        nc = lay()
    except Exception:
        return False, "", ""
    if nc is None:
        return False, "", ""
    dt = nc.danh_tinh or {}
    la_qt = str(dt.get("loai") or "") in ("quan_tri", "noi_bo")
    return la_qt, str(dt.get("id") or dt.get("loai") or ""), str(nc.nguon or "")


class Module:
    """Module khoá cửa chuẩn C1."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to", "khoa_cua")
        self._cau_hinh: dict[str, Any] = {}
        self._be = None  # backend: KhoaDamMay hoặc backend giả trong test
        self._step_up: dict[str, dict] = {}  # admin_id -> {"ma", "het"}
        self._khoa = asyncio.Lock()
        self._nhip_su_kien = NHIP_SU_KIEN_MAC_DINH

    # ── đường dẫn dữ liệu ──

    def _thu_muc(self) -> Path:
        return kho.thu_muc_du_lieu() / "du_lieu" / "khoa-cua"

    def _p_so_ten(self) -> Path:
        return self._thu_muc() / "so-ten.json"

    def _p_nhat_ky(self) -> Path:
        return self._thu_muc() / "nhat-ky-bat-bien.jsonl"

    # ── sổ tên (học tên người từ mã vân tay — dữ liệu cá nhân) ──

    def _doc_so_ten(self) -> dict:
        try:
            d = json.loads(self._p_so_ten().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _ghi_so_ten(self, d: dict) -> None:
        p = self._p_so_ten()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(p)
        os.chmod(p, 0o600)

    def _ten_cua(self, ma: str, ten_tuya: str = "") -> str:
        """Tên người. Rỗng = CHƯA BIẾT. Tên Tuya ưu tiên khi có (CHÉP ý c2a)."""
        if ten_tuya:
            return ten_tuya
        muc = self._doc_so_ten().get(ma)
        if isinstance(muc, dict):
            return str(muc.get("ten") or "")
        return ""

    # ── nhật ký bất biến ──

    def _ghi_nhat_ky(self, su_kien: str, ai: str, nguon: str,
                     chi_tiet: str = "") -> None:
        p = self._p_nhat_ky()
        p.parent.mkdir(parents=True, exist_ok=True)
        dong = json.dumps({
            "luc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "su_kien": su_kien,
            "ai": ai,
            "nguon": nguon,
            "chi_tiet": chi_tiet,
        }, ensure_ascii=False)
        with open(p, "a", encoding="utf-8") as f:
            f.write(dong + "\n")
        os.chmod(p, 0o600)
        # Tuyệt đối không log chi_tiet có thể chứa mã: chỉ log tên sự kiện.

    # ── cấu hình ──

    def _cau_hinh_day_du(self) -> bool:
        return bool(self._cau_hinh.get("access_id")
                    and self._cau_hinh.get("access_secret")
                    and self._cau_hinh.get("ma_thiet_bi"))

    def _dieu_khien_bat(self) -> bool:
        return str(self._cau_hinh.get("cho_phep_dieu_khien") or "tat").strip().lower() == "bat"

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng."""
        self._cau_hinh = dict(cau_hinh or {})
        self._be = None
        if self._cau_hinh_day_du():
            from .dam_may import KhoaDamMay, MAY_CHU

            vung = str(self._cau_hinh.get("vung") or "america").strip().lower()
            if vung not in MAY_CHU:
                return  # cai() đã chặn; phòng thủ
            def _lenh(khoa):
                raw = str(self._cau_hinh.get(khoa) or "").strip()
                if not raw:
                    return None
                try:
                    d = json.loads(raw)
                except ValueError:
                    return None
                return d if isinstance(d, list) else None

            try:
                self._be = KhoaDamMay(
                    self._cau_hinh["access_id"], self._cau_hinh["access_secret"],
                    vung, self._cau_hinh["ma_thiet_bi"],
                    str(self._cau_hinh.get("ma_dp_trang_thai") or "lock_motor_state"),
                    _lenh("lenh_mo"), _lenh("lenh_dong"))
            except Exception as e:
                log.warning("khoa-cua: không dựng được backend đám mây (%s)",
                            type(e).__name__)
                self._be = None

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình (không gọi mạng). Bí mật chỉ vào kho, không trả lại."""
        cau_hinh = dict(cau_hinh or {})

        def _loi(msg):
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": msg,
                    "cau_hinh_luu": {}}

        access_id = str(cau_hinh.get("access_id") or "").strip()
        access_secret = str(cau_hinh.get("access_secret") or "").strip()
        if bool(access_id) != bool(access_secret):
            return _loi("«access_id» và «access_secret» phải đi cùng nhau.")
        if not access_id:
            return _loi("Chưa khai Access ID/Secret của dự án Tuya IoT.")
        from .dam_may import MAY_CHU
        vung = str(cau_hinh.get("vung") or "america").strip().lower()
        if vung not in MAY_CHU:
            return _loi("«vung» không hợp lệ: " + ", ".join(sorted(MAY_CHU)) + ".")
        ma_tb = str(cau_hinh.get("ma_thiet_bi") or "").strip()
        if not ma_tb or len(ma_tb) > 128:
            return _loi("«ma_thiet_bi» bắt buộc (mã thiết bị khoá trên Tuya).")
        dk = str(cau_hinh.get("cho_phep_dieu_khien") or "tat").strip().lower()
        if dk not in ("tat", "bat"):
            return _loi("«cho_phep_dieu_khien» chỉ nhận «tat» hoặc «bat».")
        for k in ("lenh_mo", "lenh_dong"):
            raw = str(cau_hinh.get(k) or "").strip()
            if raw:
                try:
                    d = json.loads(raw)
                except ValueError:
                    return _loi(f"«{k}» phải là JSON (mảng commands của Tuya).")
                if not isinstance(d, list):
                    return _loi(f"«{k}» phải là mảng JSON.")
        luu = {
            "access_id": access_id,
            "access_secret": access_secret,
            "vung": vung,
            "ma_thiet_bi": ma_tb,
            "ma_dp_trang_thai": str(cau_hinh.get("ma_dp_trang_thai")
                                    or "lock_motor_state").strip(),
            "cho_phep_dieu_khien": dk,
            "lenh_mo": str(cau_hinh.get("lenh_mo") or "").strip(),
            "lenh_dong": str(cau_hinh.get("lenh_dong") or "").strip(),
        }
        self.nap(luu)
        che = "…" + access_id[-4:] if len(access_id) >= 4 else "…"
        return {"ok": True,
                "ket_qua_tung_khoa": [{"khoa": che, "ok": True}],
                "loi": None, "cau_hinh_luu": luu}

    # ── chuẩn thiet_bi ──

    async def thuc_the(self, loc=None) -> list[dict]:
        if not self._cau_hinh_day_du():
            return []
        return [
            {
                "id": f"{self.tien_to}.khoa_chinh",
                "ten": "Khoá cửa chính",
                "loai": "khoa_cua",
                "khu": "cong_vao",
                "thuoc_tinh": {"ma_thiet_bi": str(self._cau_hinh.get("ma_thiet_bi") or "")},
                "tinh_nang": [
                    {"hanh_dong": "chuan_bi_mo",
                     "mo_ta": "Cấp mã xác nhận dùng một lần (120 giây) cho lần mở khoá kế tiếp. Chỉ quản trị."},
                    {"hanh_dong": "mo_khoa",
                     "mo_ta": "Mở khoá cửa. LUÔN nguy hiểm: chỉ quản trị đã qua cửa sổ xác nhận lại (chuan_bi_mo) mới mở được; mọi lần mở ghi nhật ký bất biến.",
                     "tham_so": {
                         "type": "object",
                         "required": ["ma_xac_nhan"],
                         "additionalProperties": False,
                         "properties": {
                             "ma_xac_nhan": {
                                 "type": "string", "minLength": 16, "maxLength": 128,
                                 "bi_mat": True,
                                 "description": "Mã xác nhận do «chuan_bi_mo» cấp, dùng một lần.",
                             },
                         },
                     }},
                    {"hanh_dong": "dong_khoa",
                     "mo_ta": "Đóng/khoá cửa. Qua cổng rủi ro như mở, nhưng không cần mã xác nhận."},
                ],
                # BẮT BUỘC của NV169: mở khoá LUÔN nguy_hiem bằng CẤU TRÚC.
                "rui_ro_mac_dinh": "nguy_hiem",
            },
            {
                "id": f"{self.tien_to}.nhat_ky",
                "ten": "Nhật ký khoá cửa",
                "loai": "cam_bien",
                "khu": "cong_vao",
                "thuoc_tinh": {},
                "tinh_nang": [
                    {"hanh_dong": "xem_nhat_ky",
                     "mo_ta": "Đọc nhật ký mở/đóng gần đây (mã mở, tên đã học, thời điểm).",
                     "tham_so": {
                         "type": "object",
                         "additionalProperties": False,
                         "properties": {
                             "so_ngay": {"type": "integer", "minimum": 1,
                                         "maximum": 30,
                                         "description": "Số ngày nhìn lại (mặc định 2)."},
                         },
                     }},
                    {"hanh_dong": "dat_ten_ma",
                     "mo_ta": "Dạy tên cho một mã mở (vd «fingerprint#11» là «Con trai»). Chỉ quản trị.",
                     "tham_so": {
                         "type": "object",
                         "required": ["ma", "ten"],
                         "additionalProperties": False,
                         "properties": {
                             "ma": {"type": "string", "maxLength": 64,
                                    "description": "Mã dạng «cach#so», vd «fingerprint#11»."},
                             "ten": {"type": "string", "minLength": 1, "maxLength": 40,
                                     "description": "Tên người dùng mã đó."},
                         },
                     }},
                    {"hanh_dong": "xoa_ten_ma",
                     "mo_ta": "Xoá tên đã dạy cho một mã mở. Chỉ quản trị.",
                     "tham_so": {
                         "type": "object",
                         "required": ["ma"],
                         "additionalProperties": False,
                         "properties": {
                             "ma": {"type": "string", "maxLength": 64},
                         },
                     }},
                ],
                "rui_ro_mac_dinh": "doc",
            },
        ]

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        ra: dict[str, dict] = {}
        be = self._be
        for i in (ids or []):
            if i == f"{self.tien_to}.khoa_chinh":
                gia_tri = "chua_ro"
                hop_le = False
                if be is not None:
                    try:
                        gia_tri = await be.trang_thai_khoa()
                        hop_le = gia_tri in ("khoa", "mo")
                    except Exception as e:
                        log.warning("khoa-cua: đọc trạng thái lỗi (%s)",
                                    type(e).__name__)
                ra[i] = {"gia_tri": gia_tri, "hop_le": hop_le,
                         "luc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            elif i == f"{self.tien_to}.nhat_ky":
                muc = await self._nhat_ky_gan(1)
                ra[i] = {"gia_tri": (muc[0]["ts"] if muc else None),
                         "hop_le": True,
                         "luc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            else:
                ra[i] = {"gia_tri": None, "hop_le": False}
        return ra

    async def _nhat_ky_gan(self, so_ngay: int) -> list[dict]:
        """Mục nhật ký đã giải tên, mới nhất trước; gộp trùng 5 giây (CHÉP ý c2a)."""
        be = self._be
        if be is None:
            return []
        try:
            tho = await be.nhat_ky_mo(max(1, min(30, int(so_ngay))))
        except Exception as e:
            log.warning("khoa-cua: đọc nhật ký lỗi (%s)", type(e).__name__)
            return []
        so_ten = self._doc_so_ten()
        gop: list[dict] = []
        for m in tho:
            ma = str(m.get("ma") or "")
            if not ma:
                continue
            if any(x["ma"] == ma and abs(x["ts"] - float(m.get("ts") or 0)) < 5
                   for x in gop):
                continue  # trùng hai nguồn trong 5 giây — giữ một (CHÉP c2a)
            muc = so_ten.get(ma)
            ten = (str(m.get("ten_tuya") or "")
                   or (str(muc.get("ten")) if isinstance(muc, dict) else "") or "")
            gop.append({"ma": ma, "ten": ten, "hien_thi": _mo_ta_ma(ma),
                        "ts": float(m.get("ts") or 0),
                        "thiet_bi": str(m.get("thiet_bi") or "khoá cửa")})
        gop.sort(key=lambda x: -x["ts"])
        return gop

    async def thuc_hien(self, id_thuc_the: str, hanh_dong: str,
                        tham_so: dict) -> dict:
        tham_so = tham_so or {}
        tien = f"{self.tien_to}."
        if id_thuc_the == tien + "khoa_chinh":
            return await self._thuc_hien_khoa(hanh_dong, tham_so)
        if id_thuc_the == tien + "nhat_ky":
            return await self._thuc_hien_nhat_ky(hanh_dong, tham_so)
        return {"ok": False, "loi": "Không thấy thực thể."}

    # ── thực thể khoá (nguy_hiem) ──

    def _chan_dieu_khien(self) -> dict | None:
        if not self._dieu_khien_bat():
            return {"ok": False,
                    "loi": "Điều khiển khoá đang TẮT (cho_phep_dieu_khien=«tat»). "
                           "Bật trong cấu hình module nếu chủ máy cho phép."}
        if self._be is None:
            return {"ok": False, "loi": "Chưa nối được khoá (thiếu cấu hình đám mây)."}
        return None

    async def _thuc_hien_khoa(self, hanh_dong: str, tham_so: dict) -> dict:
        la_qt, ai, nguon = _nguoi_goi_la_quan_tri()
        if not la_qt:
            return {"ok": False,
                    "loi": "Chỉ quản trị mới được điều khiển khoá cửa."}
        chan = self._chan_dieu_khien()
        if hanh_dong == "chuan_bi_mo":
            if chan:
                return chan
            async with self._khoa:
                ma = secrets.token_urlsafe(32)
                self._step_up[ai] = {"ma": ma,
                                     "het": time.time() + HET_HAN_MA_XAC_NHAN}
                self._ghi_nhat_ky("chuan_bi_mo", ai, nguon)
                return {"ok": True, "ma_xac_nhan": ma,
                        "het_han_giay": int(HET_HAN_MA_XAC_NHAN)}
        if hanh_dong == "mo_khoa":
            if chan:
                return chan
            return await self._mo_khoa(ai, nguon, tham_so)
        if hanh_dong == "dong_khoa":
            if chan:
                return chan
            try:
                await self._be.dong_khoa()
            except Exception as e:
                self._ghi_nhat_ky("dong_khoa_loi", ai, nguon, type(e).__name__)
                return {"ok": False, "loi": "Không đóng được khoá (lỗi thiết bị)."}
            self._ghi_nhat_ky("dong_khoa_thanh_cong", ai, nguon)
            return {"ok": True, "trang_thai_moi": {"trang_thai": "khoa"}}
        return {"ok": False, "loi": "Hành động không hỗ trợ."}

    async def _mo_khoa(self, ai: str, nguon: str, tham_so: dict) -> dict:
        ma = str(tham_so.get("ma_xac_nhan") or "")
        async with self._khoa:
            ban_ghi = self._step_up.pop(ai, None)  # đơn dùng: lấy là mất
            if not ban_ghi or ban_ghi.get("ma") != ma:
                self._ghi_nhat_ky("mo_khoa_tu_choi", ai, nguon, "sai_ma")
                return {"ok": False,
                        "loi": "Thiếu mã xác nhận hợp lệ — gọi «chuan_bi_mo» trước, "
                               "mỗi mã chỉ dùng một lần."}
            if time.time() > float(ban_ghi.get("het") or 0):
                self._ghi_nhat_ky("mo_khoa_tu_choi", ai, nguon, "het_han")
                return {"ok": False,
                        "loi": "Mã xác nhận đã hết hạn (120 giây) — gọi lại «chuan_bi_mo»."}
            # Mã hợp lệ: ghi ý định TRƯỚC khi chạm thiết bị (kể cả khi lỗi,
            # ý định vẫn nằm trong nhật ký bất biến).
            self._ghi_nhat_ky("mo_khoa_yeu_cau", ai, nguon)
            try:
                await self._be.mo_khoa()
            except Exception as e:
                self._ghi_nhat_ky("mo_khoa_loi", ai, nguon, type(e).__name__)
                return {"ok": False, "loi": "Không mở được khoá (lỗi thiết bị)."}
            self._ghi_nhat_ky("mo_khoa_thanh_cong", ai, nguon)
            return {"ok": True, "trang_thai_moi": {"trang_thai": "mo"}}

    # ── thực thể nhật ký (doc) ──

    async def _thuc_hien_nhat_ky(self, hanh_dong: str, tham_so: dict) -> dict:
        if hanh_dong == "xem_nhat_ky":
            so_ngay = tham_so.get("so_ngay", 2)
            try:
                so_ngay = max(1, min(30, int(so_ngay)))
            except (TypeError, ValueError):
                so_ngay = 2
            muc = await self._nhat_ky_gan(so_ngay)
            return {"ok": True, "muc": muc, "so_ngay": so_ngay,
                    "nguon_ngoai": True}
        la_qt, ai, nguon = _nguoi_goi_la_quan_tri()
        if hanh_dong == "dat_ten_ma":
            if not la_qt:
                return {"ok": False, "loi": "Chỉ quản trị mới được dạy tên."}
            ma = str(tham_so.get("ma") or "").strip()
            ten = " ".join(str(tham_so.get("ten") or "").split())
            if "#" not in ma or len(ma) > 64 or not ten or len(ten) > 40:
                return {"ok": False, "loi": "«ma» dạng «cach#so», «ten» 1–40 ký tự."}
            so_ten = self._doc_so_ten()
            muc = so_ten.get(ma)
            dem = int(muc.get("da_hoi", 0)) if isinstance(muc, dict) else 0
            so_ten[ma] = {"ten": ten, "da_hoi": dem}
            self._ghi_so_ten(so_ten)
            self._ghi_nhat_ky("dat_ten_ma", ai, nguon, ma)
            return {"ok": True}
        if hanh_dong == "xoa_ten_ma":
            if not la_qt:
                return {"ok": False, "loi": "Chỉ quản trị mới được xoá tên."}
            ma = str(tham_so.get("ma") or "").strip()
            so_ten = self._doc_so_ten()
            if ma in so_ten:
                del so_ten[ma]
                self._ghi_so_ten(so_ten)
            self._ghi_nhat_ky("xoa_ten_ma", ai, nguon, ma)
            return {"ok": True}
        return {"ok": False, "loi": "Hành động không hỗ trợ."}

    # ── sự kiện thời gian thực ──

    async def su_kien(self):
        """Đẩy sự kiện mở cửa mới (CHÉP nhịp kiểm của c2a, không dùng thread)."""
        moc = 0.0
        khoi_dong = True
        try:
            while True:
                try:
                    muc = await self._nhat_ky_gan(1)
                except Exception as e:
                    log.warning("khoa-cua: vòng sự kiện lỗi (%s)", type(e).__name__)
                    muc = []
                if khoi_dong:
                    # Lần đầu chỉ đặt mốc — lịch sử cũ không phát lại.
                    moc = max([m["ts"] for m in muc] + [time.time() - 1])
                    khoi_dong = False
                else:
                    moi = [m for m in muc if m["ts"] > moc]
                    for m in sorted(moi, key=lambda x: x["ts"]):
                        moc = m["ts"]
                        yield {"id": f"{self.tien_to}.khoa_chinh",
                               "loai": "mo_khoa",
                               "gia_tri": m["ma"],
                               "luc": datetime.fromtimestamp(
                                   m["ts"], timezone.utc).isoformat(timespec="seconds")}
                await asyncio.sleep(self._nhip_su_kien)
        except (asyncio.CancelledError, GeneratorExit):
            return

    # ── sức khoẻ / dọn dẹp ──

    async def suc_khoe(self) -> dict:
        """Không gọi mạng, không trả bí mật/tên đã học."""
        if not self._cau_hinh_day_du():
            return {"ok": False, "loi": "chua_cau_hinh"}
        return {"ok": self._be is not None,
                "dieu_khien": "bat" if self._dieu_khien_bat() else "tat",
                "loi": None if self._be is not None else "chua_noi_duoc_khoa"}

    async def dung(self) -> None:
        be = self._be
        self._be = None
        if be is not None:
            try:
                await be.dong()
            except Exception as e:
                log.warning("khoa-cua: đóng backend lúc dừng lỗi (%s)",
                            type(e).__name__)
