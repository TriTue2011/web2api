"""Module tuya — thiết bị Tuya chuẩn C1, hai đường đám mây + LAN.

Nguồn: port từ c2a (services/tuya_nha.py + services/tuya_local.py, commit
d879b03, CHỈ ĐỌC). Lớp đám mây ở dam_may.py (ký HMAC chép nguyên công thức
c2a), lớp LAN ở lan.py (giao thức 6668 tự viết — c2a dùng tinytuya, thư viện
Tuya bên thứ ba, KHÔNG port theo luật kho).

Luật nhà áp ở TẦNG CẤU TRÚC (không dựa vào tên thiết bị):
- Điều khiển (ghi) mặc định TẮT — cấu hình «cho_phep_dieu_khien» = «bat»
  mới mở, và chỉ cho ngành an toàn nhỏ (đèn/công tắc/ổ cắm/quạt/rèm…).
- Khoá cửa / ổ khoá / hộp an toàn / bếp / bình nóng lạnh (theo MÃ NGÀNH
  Tuya) KHÔNG BAO GIỜ có hành động ghi — thực thể của chúng không mang
  tinh_nang nào, và thuc_hien() kiểm lại lần nữa trước khi gửi.
- Thực thể lạ / ngành lạ: không hành động, rủi ro «nguy_hiem» (fail-closed).

Đường ưu tiên trạng thái: thiết bị có khai LAN thì đọc LAN trước (nhanh,
không phụ thuộc Internet), hỏng thì quay về đám mây nếu thiết bị cũng có
trên đám mây. local_key lấy tường minh từ cấu hình; thiếu thì tự lấy qua
đám mây khi đã khai Access ID/Secret.
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import dam_may as _dm
from . import lan as _lan_mod
from .dam_may import DamMay, LoiDamMay
from .lan import LoiLan

_TIEN_TO = "tuya."


def _nhom_nganh(nganh: str) -> str:
    """Nhóm của ngành theo bảng c2a: «khoá» / «ghi» / «doc»."""
    return _dm._NGANH.get(nganh or "", ("Thiết bị Tuya", "doc"))[1]


def _ten_loai(nganh: str) -> str:
    return _dm._NGANH.get(nganh or "", ("Thiết bị Tuya", "doc"))[0]


def _rui_ro(nganh: str) -> str:
    if nganh in _dm._NGANH_KHONG_DIEU_KHIEN:
        return "nguy_hiem"
    if nganh not in _dm._NGANH:
        return "nguy_hiem"  # ngành lạ — fail-closed
    return "ghi" if _nhom_nganh(nganh) == "ghi" else "doc"


def _gia_tri_chinh(gia_tri: Any) -> Any:
    """Chuẩn hoá giá trị điểm chính cho hợp đồng C1: bool → «on»/«off»."""
    if isinstance(gia_tri, bool):
        return "on" if gia_tri else "off"
    return gia_tri


class _Lan:
    """Cầu nối bản ghi cấu hình LAN ↔ hàm giao thức trong lan.py."""

    def __init__(self, module: "Module") -> None:
        self._m = module

    async def hoi(self, ban_ghi: dict) -> dict:
        return await _lan_mod.hoi_dps(
            ban_ghi["ip"], ban_ghi["cong"], ban_ghi.get("khoa") or "",
            ban_ghi.get("phien_ban") or "3.3", ban_ghi["id"])

    async def dat(self, ban_ghi: dict, dps: dict) -> dict:
        return await _lan_mod.dat_dps(
            ban_ghi["ip"], ban_ghi["cong"], ban_ghi.get("khoa") or "",
            ban_ghi.get("phien_ban") or "3.3", ban_ghi["id"], dps)


class Module:
    """Module C1: __init__(manifest) + nap() + vòng đời + hợp đồng thiết bị."""

    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        self.id = manifest["id"]
        self._cau_hinh: dict = {}
        self._dam_may: DamMay | None = None
        self._loi_dam_may: str | None = None
        self._ban_do_lan: dict[str, dict] = {}
        self._lan = _Lan(self)
        self._cache_spec: dict[str, dict] = {}
        self._hanh_dong: dict[str, dict[str, dict]] = {}
        self._nganh_thuc_the: dict[str, str] = {}

    # ── cấu hình ────────────────────────────────────────────────────────

    @property
    def _dieu_khien(self) -> bool:
        return str(self._cau_hinh.get("cho_phep_dieu_khien") or "tat").strip().lower() == "bat"

    def nap(self, cau_hinh: dict) -> None:
        self._cau_hinh = dict(cau_hinh or {})
        self._dam_may = None
        self._loi_dam_may = None
        aid = str(self._cau_hinh.get("access_id") or "").strip()
        secret = str(self._cau_hinh.get("access_secret") or "").strip()
        if aid and secret:
            try:
                self._dam_may = DamMay(aid, secret,
                                       str(self._cau_hinh.get("vung") or "america"))
            except LoiDamMay as e:
                self._loi_dam_may = str(e)
        self._ban_do_lan = self._doc_ban_do_lan(self._cau_hinh)

    @staticmethod
    def _doc_ban_do_lan(cau_hinh: dict) -> dict[str, dict]:
        tho = cau_hinh.get("thiet_bi_lan")
        danh_sach: list = []
        if isinstance(tho, str) and tho.strip():
            try:
                doc = json.loads(tho)
                danh_sach = doc if isinstance(doc, list) else []
            except ValueError:
                danh_sach = []
        elif isinstance(tho, list):
            danh_sach = tho
        cac_khoa = cau_hinh.get("local_key_lan")
        if isinstance(cac_khoa, str):
            cac_khoa = [d.strip() for d in cac_khoa.splitlines() if d.strip()]
        if not isinstance(cac_khoa, list):
            cac_khoa = []
        ban_do: dict[str, dict] = {}
        for i, muc in enumerate(danh_sach):
            if not isinstance(muc, dict) or not muc.get("id"):
                continue
            ma = str(muc["id"])
            ban_do[ma] = {
                "id": ma,
                "ip": str(muc.get("ip") or ""),
                "cong": int(muc.get("cong") or _lan_mod.CONG_MAC_DINH),
                "ten": str(muc.get("ten") or ma),
                "nganh": str(muc.get("nganh") or ""),
                "phien_ban": str(muc.get("version") or muc.get("phien_ban") or "3.3"),
                "khoa": str(cac_khoa[i]) if i < len(cac_khoa) else "",
            }
        return ban_do

    async def cai(self, dau_vao: dict) -> dict:
        """Kiểm cấu hình như lúc lưu ở màn hình cài đặt; gọi THẬT để kiểm
        khoá đám mây (liệt kê thiết bị) và kiểm cấu trúc LAN (không nối)."""
        dau_vao = dict(dau_vao or {})
        tung: dict[str, dict] = {}
        loi_tong: list[str] = []

        def danh(khoa: str, ok: bool, loi: str | None = None) -> None:
            muc = {"ok": ok}
            if loi:
                muc["loi"] = loi
            tung[khoa] = muc
            if not ok and loi:
                loi_tong.append(loi)

        aid = str(dau_vao.get("access_id") or "").strip()
        secret = str(dau_vao.get("access_secret") or "").strip()
        muon_may = bool(aid or secret)
        dm: DamMay | None = None
        if muon_may:
            if not aid:
                danh("access_id", False, "Đã điền Access Secret thì phải có Access ID.")
            if not secret:
                danh("access_secret", False,
                     "Đã điền Access ID thì phải có Access Secret (cùng dự án).")
            if aid and secret:
                try:
                    dm = DamMay(aid, secret, str(dau_vao.get("vung") or "america"))
                except LoiDamMay as e:
                    danh("vung", False, str(e))
                if dm is not None:
                    gan = getattr(self, "_lay_client_gia", None)
                    if gan is not None:
                        dm._lay_client = gan  # chỉ test dùng
                    try:
                        await dm.danh_sach_thiet_bi()
                        danh("access_id", True)
                        danh("access_secret", True)
                    except LoiDamMay as e:
                        danh("access_secret", False,
                             f"Đám mây từ chối: {e}")
        danh_sach_lan = self._doc_ban_do_lan(dau_vao)
        tho_lan = dau_vao.get("thiet_bi_lan")
        muon_lan = bool((isinstance(tho_lan, str) and tho_lan.strip())
                        or (isinstance(tho_lan, list) and tho_lan))
        if muon_lan:
            if isinstance(tho_lan, str):
                try:
                    doc = json.loads(tho_lan)
                    if not isinstance(doc, list):
                        raise ValueError("không phải danh sách")
                except ValueError:
                    danh("thiet_bi_lan", False,
                         "«Thiết bị LAN» phải là JSON danh sách "
                         "[{\"id\": ..., \"ip\": ...}].")
                    doc = None
            if tung.get("thiet_bi_lan", {}).get("ok") is not False:
                for ma, bg in danh_sach_lan.items():
                    try:
                        _lan_mod.kiem_ip_lan(bg["ip"])
                    except ValueError as e:
                        danh("thiet_bi_lan", False, f"Thiết bị {ma}: {e}")
                        break
                else:
                    if danh_sach_lan:
                        danh("thiet_bi_lan", True)
            cac_khoa = dau_vao.get("local_key_lan")
            if isinstance(cac_khoa, str):
                cac_khoa = [d.strip() for d in cac_khoa.splitlines() if d.strip()]
            if isinstance(cac_khoa, list) and cac_khoa \
                    and len(cac_khoa) != len(danh_sach_lan):
                danh("local_key_lan", False,
                     f"Số local key ({len(cac_khoa)}) khác số thiết bị LAN "
                     f"({len(danh_sach_lan)}) — local_key_lan phải liệt kê "
                     "cùng thứ tự với thiet_bi_lan, một dòng một khoá.")
        if not muon_may and not muon_lan:
            loi_tong.append("Chưa có cấu hình nào dùng được: khai Access "
                            "ID/Secret (đám mây) hoặc thiết bị LAN.")
        ok = not loi_tong
        cau_hinh_luu: dict = {}
        if ok:
            cau_hinh_luu = {
                "access_id": aid or None,
                "access_secret": secret or None,
                "vung": str(dau_vao.get("vung") or "america"),
                "thiet_bi_lan": dau_vao.get("thiet_bi_lan") or "[]",
                "local_key_lan": dau_vao.get("local_key_lan"),
                "cho_phep_dieu_khien":
                    str(dau_vao.get("cho_phep_dieu_khien") or "tat"),
            }
            self.nap(cau_hinh_luu)
            if dm is not None:
                self._dam_may = dm
        return {"ok": ok, "ket_qua_tung_khoa": tung,
                "loi": "; ".join(loi_tong) or None,
                "cau_hinh_luu": cau_hinh_luu}

    async def suc_khoe(self) -> dict:
        kq: dict[str, Any] = {"ok": False}
        if self._dam_may is not None:
            try:
                ds = await self._dam_may.danh_sach_thiet_bi()
                kq["dam_may"] = {"ok": True, "vung": self._dam_may.vung,
                                 "so_thiet_bi": len(ds)}
                kq["ok"] = True
            except LoiDamMay as e:
                kq["dam_may"] = {"ok": False, "loi": str(e)}
        elif self._loi_dam_may:
            kq["dam_may"] = {"ok": False, "loi": self._loi_dam_may}
        if self._ban_do_lan:
            noi_duoc = 0
            for bg in self._ban_do_lan.values():
                try:
                    if not bg.get("khoa"):
                        continue
                    await self._lan.hoi(bg)
                    noi_duoc += 1
                except (LoiLan, ValueError, OSError):
                    continue
            kq["lan"] = {"ok": noi_duoc > 0, "so_ket_noi_duoc": noi_duoc,
                         "tong": len(self._ban_do_lan)}
            if noi_duoc > 0:
                kq["ok"] = True
        if "dam_may" not in kq and "lan" not in kq:
            kq["loi"] = ("Chưa cấu hình: cần Access ID/Secret (đám mây) "
                         "hoặc thiết bị LAN.")
        return kq

    async def dung(self) -> dict:
        if self._dam_may is not None:
            await self._dam_may.dong()
        return {"ok": True}

    # ── hợp đồng thiết bị C1 ────────────────────────────────────────────

    async def _dac_ta(self, ma: str) -> dict:
        if ma not in self._cache_spec and self._dam_may is not None:
            try:
                self._cache_spec[ma] = await self._dam_may.dac_ta(ma)
            except LoiDamMay:
                self._cache_spec[ma] = {}
        return self._cache_spec.get(ma) or {}

    def _tinh_nang_va_hanh_dong(self, ma_day_du: str, ma: str, nganh: str,
                                dac_ta: dict, nguon: str) -> list[dict]:
        """Dựng tinh_nang theo đặc tả của CHÍNH thiết bị; ngành cấm ghi →
        không hành động. Hành động lưu vào bản đồ để thuc_hien() tra đúng."""
        tinh_nang: list[dict] = []
        ban_do = self._hanh_dong.setdefault(ma_day_du, {})
        if nganh in _dm._NGANH_KHONG_DIEU_KHIEN or _rui_ro(nganh) != "ghi":
            self._nganh_thuc_the[ma_day_du] = nganh
            return tinh_nang
        self._nganh_thuc_the[ma_day_du] = nganh
        by_dp: dict[str, dict] = {}
        dp = ""
        for m in dac_ta.get("status") or []:
            if isinstance(m, dict) and m.get("code") and "dp_id" in m:
                by_dp[str(m["code"])] = m
        for f in dac_ta.get("functions") or []:
            if not isinstance(f, dict) or not f.get("code"):
                continue
            code = str(f["code"])
            loai = str(f.get("type") or "")
            tr = by_dp.get(code, {})
            dp = str(tr.get("dp_id", ""))
            gia_tri: dict | None = None
            try:
                gia_tri = json.loads(f.get("values") or "{}") \
                    if isinstance(f.get("values"), str) else None
            except ValueError:
                gia_tri = None
            if loai == "Boolean":
                for tien, gt in (("bat", True), ("tat", False)):
                    hd = f"{tien}_{code}"
                    tinh_nang.append({
                        "hanh_dong": hd,
                        "mo_ta": f"{'Bật' if gt else 'Tắt'} {code}",
                        "tham_so": []})
                    ban_do[hd] = {"nguon": nguon, "ma": ma, "code": code,
                                  "kieu": "Boolean", "gia_tri": gt, "dp": dp}
            elif loai in ("Integer", "Float", "Enum"):
                hd = f"dat_{code}"
                mo = f"Đặt {code}"
                luoc = {"gia_tri": {"kieu": "so_nguyen" if loai == "Integer"
                                    else "chuoi", "bat_buoc": True}}
                if isinstance(gia_tri, dict):
                    if "min" in gia_tri:
                        luoc["gia_tri"]["toi_thieu"] = gia_tri["min"]
                    if "max" in gia_tri:
                        luoc["gia_tri"]["toi_da"] = gia_tri["max"]
                    if gia_tri.get("unit"):
                        mo += f" (đơn vị {gia_tri['unit']})"
                tinh_nang.append({"hanh_dong": hd, "mo_ta": mo,
                                  "tham_so": luoc})
                ban_do[hd] = {"nguon": nguon, "ma": ma, "code": code,
                              "kieu": loai, "dp": dp}
        if not tinh_nang and nguon == "lan":
            # LAN không có đặc tả đám mây: công tắc bật/tắt điểm 1
            for tien, gt in (("bat", True), ("tat", False)):
                hd = f"{tien}_1"
                tinh_nang.append({
                    "hanh_dong": hd, "mo_ta": f"{'Bật' if gt else 'Tắt'} (điểm 1)",
                    "tham_so": []})
                ban_do[hd] = {"nguon": "lan", "ma": ma, "code": "1",
                              "kieu": "Boolean", "gia_tri": gt, "dp": "1"}
        return tinh_nang

    async def thuc_the(self, loc: dict | None) -> list[dict]:
        """Liệt kê thực thể. LAN dùng cấu hình tường minh; đám mây dùng
        danh sách từ tài khoản. Không bao giờ chạy dò quảng bá ở đây."""
        cac: list[dict] = []
        self._hanh_dong.clear()
        self._nganh_thuc_the.clear()
        if self._dam_may is not None:
            try:
                cac_tb = await self._dam_may.danh_sach_thiet_bi()
            except LoiDamMay:
                cac_tb = []
            for tb in cac_tb:
                ma = tb["id"]
                nganh = tb.get("nganh") or ""
                dac_ta = await self._dac_ta(ma)
                id_day = _TIEN_TO + ma
                cac.append({
                    "id": id_day,
                    "ten": tb.get("ten") or ma,
                    "loai": tb.get("loai") or _ten_loai(nganh),
                    "phong": None,
                    "tinh_nang": self._tinh_nang_va_hanh_dong(
                        id_day, ma, nganh, dac_ta, "dam_may"),
                    "rui_ro_mac_dinh": _rui_ro(nganh),
                    "nguon": "dam_may",
                })
        for ma, bg in self._ban_do_lan.items():
            nganh = bg.get("nganh") or ""
            dac_ta: dict = {}
            if self._dam_may is not None:  # cùng mã → có đặc tả đám mây
                dac_ta = await self._dac_ta(ma)
            id_day = _TIEN_TO + ma
            loai = _ten_loai(nganh)
            cac.append({
                "id": id_day,
                "ten": bg.get("ten") or ma,
                "loai": loai,
                "phong": None,
                "tinh_nang": self._tinh_nang_va_hanh_dong(
                    id_day, ma, nganh, dac_ta, "lan"),
                "rui_ro_mac_dinh": _rui_ro(nganh),
                "nguon": "lan",
            })
        return cac

    async def _thiet_bi_dam_may(self, ma: str) -> dict | None:
        if self._dam_may is None:
            return None
        try:
            for tb in await self._dam_may.danh_sach_thiet_bi():
                if tb["id"] == ma:
                    return tb
        except LoiDamMay:
            return None
        return None

    def _trang_thai_dam_may(self, tb: dict) -> dict:
        """Hợp đồng C1: đúng 4 khoá + «loi» khi hỏng."""
        luc = time.time()
        if not tb.get("truc_tuyen"):
            return {"gia_tri": None, "don_vi": None, "luc": luc, "hop_le": False,
                    "loi": "Thiết bị ngoại tuyến (Tuya báo is_online=false)."}
        return None  # cần shadow — xử lý ở trang_thai

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        kq: dict[str, dict] = {}
        for id_day in ids or []:
            if not isinstance(id_day, str) or not id_day.startswith(_TIEN_TO):
                continue  # chống IDOR: id lạ tiền tố khác bị bỏ qua
            ma = id_day[len(_TIEN_TO):]
            if not ma:
                continue
            luc = time.time()
            if ma in self._ban_do_lan:
                bg = self._ban_do_lan[ma]
                try:
                    dps = await self._lan.hoi(bg)
                    khoa_chinh = "1" if "1" in dps else next(iter(dps))
                    kq[id_day] = {"gia_tri": _gia_tri_chinh(dps[khoa_chinh]),
                                  "don_vi": None, "luc": luc, "hop_le": True}
                except (LoiLan, ValueError, OSError) as e:
                    kq[id_day] = {"gia_tri": None, "don_vi": None,
                                  "luc": luc, "hop_le": False,
                                  "loi": str(e)}
                except Exception as e:  # không sập vì một thiết bị
                    kq[id_day] = {"gia_tri": None, "don_vi": None,
                                  "luc": luc, "hop_le": False,
                                  "loi": f"Lỗi không mong đợi: {type(e).__name__}."}
                continue
            tb = await self._thiet_bi_dam_may(ma)
            if tb is None:
                continue  # id lạ không thuộc module — vắng mặt
            loi_off = self._trang_thai_dam_may(tb)
            if loi_off is not None:
                kq[id_day] = loi_off
                continue
            try:
                thuoc = await self._dam_may.thuoc_tinh(ma)
            except LoiDamMay as e:
                kq[id_day] = {"gia_tri": None, "don_vi": None,
                              "luc": luc, "hop_le": False, "loi": str(e)}
                continue
            except Exception as e:
                kq[id_day] = {"gia_tri": None, "don_vi": None,
                              "luc": luc, "hop_le": False,
                              "loi": f"Lỗi không mong đợi: {type(e).__name__}."}
                continue
            dac_ta = await self._dac_ta(ma)
            don_vi = None
            chon = None
            for m in dac_ta.get("status") or []:
                if isinstance(m, dict) and m.get("code"):
                    chon = str(m["code"])
                    try:
                        v = json.loads(m.get("values") or "{}")
                        don_vi = v.get("unit")
                    except ValueError:
                        don_vi = None
                    break
            if chon is None:
                chon = next(iter(thuoc), None)
            kq[id_day] = {"gia_tri": _gia_tri_chinh(thuoc.get(chon)),
                          "don_vi": don_vi, "luc": luc, "hop_le": True}
        return kq

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict) -> dict:
        """Ghi. Chống IDOR: chỉ hành động của ĐÚNG thực thể mới được chạy;
        ngành cấm ghi không bao giờ tới đây."""
        tham_so = tham_so or {}
        ban_do = self._hanh_dong.get(id or "")
        if ban_do is None or not ban_do:
            try:
                await self.thuc_the(None)
            except Exception:
                pass
            ban_do = self._hanh_dong.get(id or "") or {}
        hd = ban_do.get(hanh_dong or "")
        if hd is None or not hd:
            return {"ok": False,
                    "loi": "Hành động không tồn tại hoặc không thuộc "
                           "thiết bị này."}
        ma = hd["ma"]
        nganh = self._nganh_thuc_the.get(id, "")
        if nganh in _dm._NGANH_KHONG_DIEU_KHIEN or _rui_ro(nganh) != "ghi":
            return {"ok": False,
                    "loi": "Loại thiết bị này không bao giờ điều khiển được "
                           "(luật nhà: khoá/bếp/bình nóng lạnh chỉ đọc)."}
        if not self._dieu_khien:
            return {"ok": False,
                    "loi": "Điều khiển đang TẮT (mặc định chỉ đọc). Bật "
                           "«cho_phep_dieu_khien» trong cấu hình để điều "
                           "khiển thiết bị an toàn nhỏ."}
        kieu = hd["kieu"]
        if kieu == "Boolean":
            gia_tri: Any = bool(hd["gia_tri"])
        elif kieu == "Integer":
            try:
                gia_tri = int(tham_so.get("gia_tri"))
            except (TypeError, ValueError):
                return {"ok": False,
                        "loi": "tham_so.gia_tri phải là số nguyên."}
        elif kieu == "Float":
            try:
                gia_tri = float(tham_so.get("gia_tri"))
            except (TypeError, ValueError):
                return {"ok": False,
                        "loi": "tham_so.gia_tri phải là số."}
        else:  # Enum
            gia_tri = tham_so.get("gia_tri")
        try:
            if hd["nguon"] == "lan":
                bg = self._ban_do_lan.get(ma)
                if bg is None:
                    raise LoiLan("Thiết bị LAN không còn trong cấu hình.")
                if not bg.get("khoa"):
                    return {"ok": False,
                            "loi": "Chưa có local_key cho thiết bị LAN này."}
                await self._lan.dat(bg, {hd.get("dp") or hd["code"]: gia_tri})
                return {"ok": True}
            if self._dam_may is None:
                return {"ok": False,
                        "loi": "Chưa cấu hình đường đám mây."}
            ok = await self._dam_may.gui_lenh(
                ma, [{"code": hd["code"], "value": gia_tri}])
            if not ok:
                return {"ok": False,
                        "loi": "Tuya không xác nhận lệnh."}
            return {"ok": True}
        except (LoiDamMay, LoiLan, ValueError, OSError) as e:
            return {"ok": False, "loi": str(e)}
        except Exception as e:
            return {"ok": False,
                    "loi": f"Lỗi không mong đợi: {type(e).__name__}."}
