"""Sổ thiết bị mạng và báo MÁY LẠ qua chuẩn kenh_ra (NV107-LÕI).

Nguyên tắc:
- Lõi không chứa tên nhà cung cấp hay module cụ thể nào.
- Nhận diện máy qua chuẩn thực thể thiet_bi (thuộc tính thuoc_tinh.mac).
- Lưu bền tại <WEB2API_DATA>/so_thiet_bi.json (0600, ghi nguyên tử).
- Kho hỏng (KhoHong) -> DỪNG báo, DỪNG ghi, trả 503, hiện loi_kho ở trạng thái, không ghi đè.
- Chuẩn hoá MAC (bỏ multicast, broadcast, toàn 0, nhận diện MAC ngẫu nhiên).
- Dữ liệu tên tự khai là KHÔNG TIN: làm sạch, bỏ ký tự điều khiển/ngắt dòng, cắt ≤ 40 ký tự.
- Chống bão: máy mới tăng đột biến -> 1 tin tóm tắt, tạm dừng, chờ quản trị tiếp tục.
- Báo qua cổng chuẩn gui_den_dich() của app/gui_ra.py.
- Theo dõi gửi từng (máy, đích): thử lại tối đa 5 lần, đích thành công không gửi lại.
- Nhật ký kênh so_thiet_bi: che MAC dạng aa:bb:cc:**:**:ff, không ghi tên, không ghi IP.
- Mọi API yêu cầu quyền quản trị (can_quan_tri).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import gui_ra, kho, nhat_ky
from app.ham_khoa import KhoHong
from app.loi_openai import LoiOpenAI
from app.thiet_bi import bo_dau
from app.xac_thuc import can_quan_tri

log = logging.getLogger("web2api.so_thiet_bi")

router = APIRouter(prefix="/api/so-thiet-bi", dependencies=[Depends(can_quan_tri)])

MAU_HEX_12 = re.compile(r"^[0-9a-fA-F]{12}\Z")
MAU_MAC_URL = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}\Z")
TRANG_THAI_HOP_LE = {"quen", "la", "bo_qua"}
CHE_DO_MAC_NGAU_NHIEN_HOP_LE = {"gop", "co", "khong"}

CHU_KY_MAC_DINH_GIAY = 60
CHU_KY_TOI_THIEU_GIAY = 15
CHU_KY_TOI_DA_GIAY = 3600
TRI_HOAN_DAU_GIAY = 20.0
TOI_DA_DONG_DS = 500
TOI_DA_THU_LAI_BAO = 5

_KHOA_SO = threading.RLock()


def chu_ky_giay() -> int:
    """Đọc chu kỳ quét từ biến môi trường WEB2API_CHU_KY_SO_THIET_BI_GIAY (15..3600s, mặc định 60)."""
    val = os.environ.get("WEB2API_CHU_KY_SO_THIET_BI_GIAY", "").strip()
    if not val:
        return CHU_KY_MAC_DINH_GIAY
    try:
        so = int(val)
        if not (CHU_KY_TOI_THIEU_GIAY <= so <= CHU_KY_TOI_DA_GIAY):
            raise ValueError(f"Ngoài khoảng {CHU_KY_TOI_THIEU_GIAY}..{CHU_KY_TOI_DA_GIAY}")
        return so
    except Exception:
        log.warning(
            "WEB2API_CHU_KY_SO_THIET_BI_GIAY=%r không hợp lệ (cần %d..%d), dùng mặc định %d",
            val[:40],
            CHU_KY_TOI_THIEU_GIAY,
            CHU_KY_TOI_DA_GIAY,
            CHU_KY_MAC_DINH_GIAY,
        )
        return CHU_KY_MAC_DINH_GIAY


def chuan_hoa_mac(mac: Any) -> tuple[str | None, bool]:
    """Chuẩn hoá MAC từ nhiều dạng (AA:BB.., aa-bb.., aabb.ccdd.., aabbccddeeff) về aa:bb:cc:dd:ee:ff.

    Bỏ qua: không đúng 12 hex, toàn 0, broadcast ff:ff:ff:ff:ff:ff, multicast (bit 0 của octet đầu = 1).
    Trả về (mac_chuan, ngau_nhien): ngau_nhien = True khi bit 0x02 của octet đầu bật (địa chỉ quản trị nội bộ).
    """
    if not isinstance(mac, str):
        return None, False
    s = mac.strip().replace(":", "").replace("-", "").replace(".", "")
    if not MAU_HEX_12.match(s):
        return None, False
    raw = s.lower()
    mac_chuan = f"{raw[0:2]}:{raw[2:4]}:{raw[4:6]}:{raw[6:8]}:{raw[8:10]}:{raw[10:12]}"
    if mac_chuan in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
        return None, False
    octet0 = int(raw[0:2], 16)
    if octet0 & 0x01 != 0:
        # Multicast -> bỏ qua
        return None, False
    ngau_nhien = bool(octet0 & 0x02)
    return mac_chuan, ngau_nhien


def lam_sach_ten_tu_khai(val: Any) -> str | None:
    """Làm sạch tên máy tự khai qua DHCP: bỏ ký tự điều khiển, ngắt dòng, cắt ≤ 40 ký tự."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    loc = []
    for c in s:
        cat = unicodedata.category(c)
        if cat.startswith("C") or cat in ("Zl", "Zp"):
            continue
        loc.append(c)
    ket_qua = "".join(loc).strip()[:40]
    return ket_qua if ket_qua else None


def lam_sach_ten_chu_may(val: Any) -> str | None:
    """Làm sạch tên do chủ máy đặt: bỏ ký tự điều khiển, ngắt dòng, cắt ≤ 60 ký tự."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    loc = []
    for c in s:
        cat = unicodedata.category(c)
        if cat.startswith("C") or cat in ("Zl", "Zp"):
            continue
        loc.append(c)
    ket_qua = "".join(loc).strip()[:60]
    return ket_qua if ket_qua else None


def che_mac(mac: str) -> str:
    """Che địa chỉ MAC khi ghi nhật ký: aa:bb:cc:**:**:ff."""
    parts = mac.split(":")
    if len(parts) == 6:
        return f"{parts[0]}:{parts[1]}:{parts[2]}:**:**:{parts[5]}"
    return "…che_mac"


def duong_dan_so() -> Path:
    return kho.thu_muc_du_lieu() / "so_thiet_bi.json"


def doc_so() -> dict:
    """Đọc sổ thiết bị từ đĩa; tệp chưa có -> cấu trúc mặc định; hỏng -> KhoHong."""
    p = duong_dan_so()
    with _KHOA_SO:
        du_lieu = kho.doc_json(p)
        if du_lieu is None:
            return {
                "da_chot": False,
                "tam_dung": False,
                "cau_hinh_bao": {
                    "bat": False,
                    "dich": [],
                    "bao_mac_ngau_nhien": "gop",
                },
                "trang_thai_nguon": {},
                "thiet_bi": {},
            }
        if not isinstance(du_lieu, dict):
            raise KhoHong(f"{p} hỏng: nội dung không phải dict")
        if not isinstance(du_lieu.get("thiet_bi"), dict):
            raise KhoHong(f"{p} hỏng: trường thiet_bi không phải dict")
        # Đảm bảo các khoá cơ bản luôn có mặt
        du_lieu.setdefault("da_chot", False)
        du_lieu.setdefault("tam_dung", False)
        du_lieu.setdefault("cau_hinh_bao", {"bat": False, "dich": [], "bao_mac_ngau_nhien": "gop"})
        du_lieu.setdefault("trang_thai_nguon", {})
        return du_lieu


def ghi_so(so: dict) -> None:
    """Ghi sổ thiết bị nguyên tử với quyền 0600."""
    p = duong_dan_so()
    with _KHOA_SO:
        kho.ghi_nguyen_tu(p, json.dumps(so, ensure_ascii=False, indent=2), quyen=0o600)


def _kiem_mac_url(mac_param: str) -> str:
    """Kiểm tra và chuẩn hoá MAC từ đường dẫn API. Sai khuôn -> 404."""
    mac_chuan, _ = chuan_hoa_mac(mac_param)
    if not mac_chuan or not MAU_MAC_URL.match(mac_chuan):
        raise LoiOpenAI(404, "Địa chỉ MAC không hợp lệ hoặc sai định dạng.", code="mac_khong_hop_le")
    return mac_chuan


def _kiem_tra_dich_hop_le(so_mod: Any, module_id: str, kenh_id: str) -> bool:
    """Kiểm tra một đích có đang khả dụng trong danh sách module đang chạy không."""
    if so_mod is None:
        return False
    m = so_mod.dang_chay.get(module_id)
    if m is None or not callable(getattr(m, "kenh_ra", None)):
        return False
    km = so_mod.catalog.get(module_id)
    man = getattr(km, "manifest", None) if km else None
    if not isinstance(man, dict) or "kenh_ra" not in man.get("cung_cap", []):
        return False
    try:
        ds = m.kenh_ra()
        if not isinstance(ds, list):
            return False
        return any(isinstance(k, dict) and k.get("id") == kenh_id for k in ds)
    except Exception as e:
        log.warning("Module %s.kenh_ra() ném %s khi kiểm tra đích", module_id, type(e).__name__)
        return False


def _trich_dich_hop_le_cau_hinh(so_mod: Any, cau_hinh_dich: list[dict]) -> tuple[list[dict], bool]:
    """Trả về (danh sách đích đã gán cờ hop_le, co_it_nhat_mot_dich_hop_le)."""
    ket_qua = []
    co_hop_le = False
    for d in cau_hinh_dich:
        if not isinstance(d, dict):
            continue
        mid = d.get("module")
        kid = d.get("kenh_id")
        if not mid or not kid:
            continue
        hl = _kiem_tra_dich_hop_le(so_mod, mid, kid)
        if hl:
            co_hop_le = True
        ket_qua.append({"module": mid, "kenh_id": kid, "hop_le": hl})
    return ket_qua, co_hop_le


# ───────────── Vòng quét định kỳ ─────────────

class VongQuetSoThietBi:
    """Vòng quét thiết bị mạng nền: thu thập thực thể từ các module thiet_bi và báo máy lạ."""

    def __init__(self, lay_app: Callable[[], Any], *, ngu: Callable[[float], Any] | None = None):
        self._lay_app = lay_app
        self._ngu = ngu or asyncio.sleep
        self._task: asyncio.Task | None = None
        self._dung_co = False
        self._dang_chay_chu_ky = False

    def bat_dau(self) -> None:
        """Khởi động tác vụ vòng quét nền nếu chưa chạy."""
        if self._task is not None and not self._task.done():
            return
        self._dung_co = False
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._vong_lap())
        except RuntimeError:
            pass

    async def dung(self) -> None:
        """Dừng vòng quét và chờ kết thúc tác vụ."""
        self._dung_co = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _vong_lap(self) -> None:
        try:
            await self._ngu(TRI_HOAN_DAU_GIAY)
            while not self._dung_co:
                try:
                    await self.chay_mot_chu_ky()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("Lỗi trong chu kỳ quét sổ thiết bị: %s", type(e).__name__)
                chu_ky = chu_ky_giay()
                await self._ngu(float(chu_ky))
        except asyncio.CancelledError:
            pass

    async def chay_mot_chu_ky(self) -> dict:
        """Chạy một chu kỳ quét: lấy thực thể từ các module thiet_bi, gom máy, phát hiện lạ và báo."""
        if self._dang_chay_chu_ky:
            return {"bo_qua": "dang_chay"}
        self._dang_chay_chu_ky = True
        try:
            return await self._thuc_hien_chu_ky()
        finally:
            self._dang_chay_chu_ky = False

    async def _thuc_hien_chu_ky(self) -> dict:
        app = self._lay_app()
        so_mod = getattr(getattr(app, "state", None), "so", None)

        try:
            so = doc_so()
        except KhoHong as e:
            log.warning("Không thể đọc sổ thiết bị (kho hỏng): %s. Tạm dừng chu kỳ.", e)
            return {"loi": "kho_hong"}

        bay_gio = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # 1. Thu thập thực thể từ các module có khả năng thiet_bi
        cac_nguon: list[tuple[str, str, Any]] = []  # (module_id, tien_to, the_hien)
        if so_mod is not None:
            for mid, m in list(so_mod.dang_chay.items()):
                km = so_mod.catalog.get(mid)
                if not km:
                    continue
                cung_cap = km.manifest.get("cung_cap", [])
                if "thiet_bi" not in cung_cap:
                    continue
                if not callable(getattr(m, "thuc_the", None)):
                    continue
                cac_nguon.append((mid, km.tien_to, m))

        may_thay_chu_ky: dict[str, dict] = {}  # mac_chuan -> thong_tin_gom
        trang_thai_nguon = so.setdefault("trang_thai_nguon", {})

        for mid, tien_to, m in cac_nguon:
            try:
                raw_entities = await asyncio.wait_for(m.thuc_the(None), timeout=5.0)
            except Exception as e:
                log.warning("Module nguồn %s (%s) lỗi/quá hạn thuc_the: %s", mid, tien_to, type(e).__name__)
                trang_thai_nguon[tien_to] = {"trang_thai": "loi", "luc": bay_gio}
                continue

            if not isinstance(raw_entities, list):
                log.warning("Module nguồn %s trả thuc_the không phải list", mid)
                trang_thai_nguon[tien_to] = {"trang_thai": "loi", "luc": bay_gio}
                continue

            thuc_the_hop_le_cua_nguon = 0
            for item in raw_entities:
                if not isinstance(item, dict):
                    continue
                tt = item.get("thuoc_tinh") or {}
                mac_raw = tt.get("mac")
                mac_chuan, ngau_nhien = chuan_hoa_mac(mac_raw)
                if not mac_chuan:
                    continue

                thuc_the_hop_le_cua_nguon += 1
                ip = tt.get("ip") if isinstance(tt.get("ip"), str) else None
                ten_tk_raw = tt.get("ten_may_tu_khai") or tt.get("hostname")
                ten_tk = lam_sach_ten_tu_khai(ten_tk_raw)

                # Gộp máy nếu thấy ở nhiều nguồn
                if mac_chuan not in may_thay_chu_ky:
                    may_thay_chu_ky[mac_chuan] = {
                        "mac": mac_chuan,
                        "ten_tu_khai": ten_tk,
                        "nguon": tien_to,
                        "ip": ip,
                        "ngau_nhien": ngau_nhien,
                    }
                else:
                    hien_co = may_thay_chu_ky[mac_chuan]
                    if ip and not hien_co["ip"]:
                        hien_co["ip"] = ip
                    if ten_tk and not hien_co["ten_tu_khai"]:
                        hien_co["ten_tu_khai"] = ten_tk

            if thuc_the_hop_le_cua_nguon > 0:
                trang_thai_nguon[tien_to] = {"trang_thai": "ok", "luc": bay_gio}
            else:
                trang_thai_nguon[tien_to] = {"trang_thai": "trong", "luc": bay_gio}

        # 2. Cập nhật vào sổ
        thiet_bi = so.setdefault("thiet_bi", {})
        cac_may_moi: list[dict] = []

        for mac_chuan, info in may_thay_chu_ky.items():
            if mac_chuan not in thiet_bi:
                # Máy mới xuất hiện
                tb = {
                    "mac": mac_chuan,
                    "ten": None,
                    "ten_tu_khai": info["ten_tu_khai"],
                    "trang_thai": "la",
                    "lan_dau": bay_gio,
                    "lan_cuoi": bay_gio,
                    "nguon": info["nguon"],
                    "ip": info["ip"],
                    "ngau_nhien": info["ngau_nhien"],
                    "da_bao": {},
                    "lan_loi_bao": {},
                }
                thiet_bi[mac_chuan] = tb
                cac_may_moi.append(tb)
                # Nhật ký che MAC, không tên, không IP
                nhat_ky.ghi(
                    "so_thiet_bi",
                    "thong_tin",
                    f"Phát hiện máy mới trong mạng: {che_mac(mac_chuan)}",
                    mac=che_mac(mac_chuan),
                    nguon=info["nguon"],
                    ngau_nhien=info["ngau_nhien"],
                )
            else:
                # Máy đã có: cập nhật thời gian thấy, ip, tên tự khai
                tb = thiet_bi[mac_chuan]
                tb["lan_cuoi"] = bay_gio
                if info["ip"]:
                    tb["ip"] = info["ip"]
                if info["ten_tu_khai"] and not tb.get("ten_tu_khai"):
                    tb["ten_tu_khai"] = info["ten_tu_khai"]

        # 3. Kiểm tra chống bão
        # Trong một chu kỳ nếu số máy MỚI > max(5, nửa số máy thấy được)
        tong_thay = len(may_thay_chu_ky)
        nguong_bao = max(5, math.floor(tong_thay / 2.0))
        # Chỉ SAU khi chủ máy chốt sổ: lần quét đầu (sổ rỗng) mọi máy đều «mới» — đó là việc nạp sổ, không phải bão.
        if so.get("da_chot") and len(cac_may_moi) > nguong_bao:
            so["tam_dung"] = True
            nhat_ky.ghi(
                "so_thiet_bi",
                "canh_bao",
                f"Phát hiện bão thiết bị ({len(cac_may_moi)} máy mới). Tạm dừng báo tự động.",
                so_may_moi=len(cac_may_moi),
            )
            # Nếu đã chốt và đang bật báo -> gửi 1 tin tóm tắt cảnh báo bão (không quá 10 MAC)
            cau_hinh_bao = so.get("cau_hinh_bao", {})
            if so.get("da_chot") and cau_hinh_bao.get("bat"):
                danh_sach_dich = cau_hinh_bao.get("dich", [])
                mac_liet_ke = [che_mac(m["mac"]) for m in cac_may_moi[:10]]
                con_lai = len(cac_may_moi) - len(mac_liet_ke)
                chuoi_mac = ", ".join(mac_liet_ke)
                if con_lai > 0:
                    chuoi_mac += f" (và {con_lai} máy khác)"
                tin_bao = (
                    f"Cảnh báo bão thiết bị: phát hiện {len(cac_may_moi)} máy mới trong mạng. "
                    f"Hệ thống đã tạm dừng báo tự động để phòng ngừa. Các MAC: {chuoi_mac}."
                )
                await self._gui_tin_toi_cac_dich(app, danh_sach_dich, tin_bao)
            ghi_so(so)
            return {"chong_bao": True, "so_may_moi": len(cac_may_moi)}

        # 4. Kiểm tra điều kiện báo máy lạ
        # Nếu chưa chốt -> không báo gì
        if not so.get("da_chot"):
            ghi_so(so)
            return {"da_chot": False, "so_may_moi": len(cac_may_moi)}

        # Nếu đang tạm dừng hoặc tắt báo -> không báo gì
        cau_hinh_bao = so.get("cau_hinh_bao", {})
        if so.get("tam_dung") or not cau_hinh_bao.get("bat"):
            ghi_so(so)
            return {"tam_dung": so.get("tam_dung"), "bat": cau_hinh_bao.get("bat")}

        # 5. Xử lý báo máy lạ
        danh_sach_dich = cau_hinh_bao.get("dich", [])
        che_do_nn = cau_hinh_bao.get("bao_mac_ngau_nhien", "gop")
        if che_do_nn not in CHE_DO_MAC_NGAU_NHIEN_HOP_LE:
            che_do_nn = "gop"

        for d in danh_sach_dich:
            if not isinstance(d, dict):
                continue
            mid = d.get("module")
            kid = d.get("kenh_id")
            if not mid or not kid:
                continue

            d_key = f"{mid}|{kid}"
            if not _kiem_tra_dich_hop_le(so_mod, mid, kid):
                continue

            # Tìm máy lạ cần báo cho đích này
            can_bao_thuong: list[dict] = []
            can_bao_ngau_nhien: list[dict] = []

            for tb in thiet_bi.values():
                if tb.get("trang_thai") != "la":
                    continue
                if d_key in tb.get("da_bao", {}):
                    continue
                if tb.get("lan_loi_bao", {}).get(d_key, 0) >= TOI_DA_THU_LAI_BAO:
                    continue

                if tb.get("ngau_nhien"):
                    if che_do_nn == "khong":
                        continue
                    elif che_do_nn == "gop":
                        can_bao_ngau_nhien.append(tb)
                    else:
                        can_bao_thuong.append(tb)
                else:
                    can_bao_thuong.append(tb)

            # Gửi tin máy thường
            if can_bao_thuong:
                await self._gui_nhom_may_la(app, mid, kid, d_key, can_bao_thuong, bay_gio)

            # Gửi tin máy ngẫu nhiên gộp
            if can_bao_ngau_nhien:
                await self._gui_nhom_mac_ngau_nhien(app, mid, kid, d_key, can_bao_ngau_nhien, bay_gio)

        ghi_so(so)
        return {"ok": True, "may_moi": len(cac_may_moi)}

    async def _gui_nhom_may_la(
        self, app: Any, module_id: str, kenh_id: str, d_key: str, ds_may: list[dict], bay_gio: str
    ) -> None:
        """Gửi tin báo cho nhóm máy thường (gộp vào một tin nếu nhiều máy)."""
        dong_tin = []
        for tb in ds_may[:10]:
            ten_tk = tb.get("ten_tu_khai")
            ten_hien = f"{ten_tk} (tự khai)" if ten_tk else "(không tên)"
            mac = tb.get("mac")
            ip = tb.get("ip") or "chưa rõ"
            nguon = tb.get("nguon") or "mạng"
            luc = tb.get("lan_dau", bay_gio)
            dong_tin.append(f"• Máy lạ vào mạng: {ten_hien} · MAC {mac} · IP {ip} · nguồn {nguon} · lúc {luc}")

        if len(ds_may) > 10:
            dong_tin.append(f"• ... và {len(ds_may) - 10} máy khác.")

        van_ban = "\n".join(dong_tin)
        try:
            res = await gui_ra.gui_den_dich(app, module_id, kenh_id, {"van_ban": van_ban}, nguoi_goi="so_thiet_bi")
            if isinstance(res, dict) and res.get("ok"):
                for tb in ds_may:
                    tb.setdefault("da_bao", {})[d_key] = bay_gio
                    nhat_ky.ghi(
                        "so_thiet_bi",
                        "thong_tin",
                        f"Đã báo máy lạ {che_mac(tb['mac'])} qua {module_id}",
                        mac=che_mac(tb["mac"]),
                        dich=d_key,
                    )
            else:
                self._tang_loi_bao(ds_may, d_key)
        except Exception as e:
            log.warning("Gửi tin báo máy lạ tới %s thất bại: %s", d_key, type(e).__name__)
            self._tang_loi_bao(ds_may, d_key)

    async def _gui_nhom_mac_ngau_nhien(
        self, app: Any, module_id: str, kenh_id: str, d_key: str, ds_may: list[dict], bay_gio: str
    ) -> None:
        """Gửi một tin tóm tắt cho nhóm máy dùng MAC ngẫu nhiên."""
        dong_tin = [f"Phát hiện {len(ds_may)} máy lạ dùng MAC ngẫu nhiên trong mạng:"]
        for tb in ds_may[:10]:
            mac = tb.get("mac")
            ip = tb.get("ip") or "chưa rõ"
            dong_tin.append(f"• MAC {mac} · IP {ip}")
        if len(ds_may) > 10:
            dong_tin.append(f"• ... và {len(ds_may) - 10} máy khác.")

        van_ban = "\n".join(dong_tin)
        try:
            res = await gui_ra.gui_den_dich(app, module_id, kenh_id, {"van_ban": van_ban}, nguoi_goi="so_thiet_bi")
            if isinstance(res, dict) and res.get("ok"):
                for tb in ds_may:
                    tb.setdefault("da_bao", {})[d_key] = bay_gio
                    nhat_ky.ghi(
                        "so_thiet_bi",
                        "thong_tin",
                        f"Đã báo máy MAC ngẫu nhiên {che_mac(tb['mac'])} qua {module_id}",
                        mac=che_mac(tb["mac"]),
                        dich=d_key,
                    )
            else:
                self._tang_loi_bao(ds_may, d_key)
        except Exception as e:
            log.warning("Gửi tin báo MAC ngẫu nhiên tới %s thất bại: %s", d_key, type(e).__name__)
            self._tang_loi_bao(ds_may, d_key)

    def _tang_loi_bao(self, ds_may: list[dict], d_key: str) -> None:
        for tb in ds_may:
            loi_dict = tb.setdefault("lan_loi_bao", {})
            loi_dict[d_key] = loi_dict.get(d_key, 0) + 1
            nhat_ky.ghi(
                "so_thiet_bi",
                "canh_bao",
                f"Lỗi gửi tin báo máy lạ {che_mac(tb['mac'])} tới {d_key} (lần {loi_dict[d_key]})",
                mac=che_mac(tb["mac"]),
                dich=d_key,
                lan=loi_dict[d_key],
            )

    async def _gui_tin_toi_cac_dich(self, app: Any, danh_sach_dich: list[dict], van_ban: str) -> None:
        so_mod = getattr(getattr(app, "state", None), "so", None)
        for d in danh_sach_dich:
            if not isinstance(d, dict):
                continue
            mid = d.get("module")
            kid = d.get("kenh_id")
            if not mid or not kid or not _kiem_tra_dich_hop_le(so_mod, mid, kid):
                continue
            try:
                await gui_ra.gui_den_dich(app, mid, kid, {"van_ban": van_ban}, nguoi_goi="so_thiet_bi")
            except Exception as e:
                log.warning("Gửi tin cảnh báo tới %s|%s thất bại: %s", mid, kid, type(e).__name__)


# ───────────── Tuyến API quản trị ─────────────

@router.get("")
async def lay_danh_sach(trang_thai: str | None = None, q: str | None = None):
    """Liệt kê danh sách máy trong sổ, hỗ trợ lọc theo trang_thai và tìm kiếm q."""
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    ds = list(so.get("thiet_bi", {}).values())

    if trang_thai:
        ds = [tb for tb in ds if tb.get("trang_thai") == trang_thai]

    if q:
        tu_khoa = bo_dau(q.strip().lower())
        ket_qua = []
        for tb in ds:
            mac = tb.get("mac", "").lower()
            ten = bo_dau((tb.get("ten") or "").lower())
            ten_tk = bo_dau((tb.get("ten_tu_khai") or "").lower())
            ip = (tb.get("ip") or "").lower()
            if tu_khoa in mac or tu_khoa in ten or tu_khoa in ten_tk or tu_khoa in ip:
                ket_qua.append(tb)
        ds = ket_qua

    return ds[:TOI_DA_DONG_DS]


@router.get("/trang-thai")
async def lay_trang_thai(request: Request):
    """Lấy số liệu tổng hợp: tổng, lạ, quen, bỏ qua, chưa báo, đã chốt, tạm dừng, đích hợp lệ, lỗi kho."""
    so_mod = getattr(getattr(request.app, "state", None), "so", None)
    try:
        so = doc_so()
    except KhoHong:
        return {
            "tong": 0,
            "la": 0,
            "quen": 0,
            "bo_qua": 0,
            "chua_bao": 0,
            "da_chot": False,
            "tam_dung": False,
            "dich_hop_le": False,
            "loi_kho": True,
            "trang_thai_nguon": {},
            "bao_loi": False,
        }

    thiet_bi = so.get("thiet_bi", {})
    tong = len(thiet_bi)
    la = sum(1 for tb in thiet_bi.values() if tb.get("trang_thai") == "la")
    quen = sum(1 for tb in thiet_bi.values() if tb.get("trang_thai") == "quen")
    bo_qua = sum(1 for tb in thiet_bi.values() if tb.get("trang_thai") == "bo_qua")

    cau_hinh_bao = so.get("cau_hinh_bao", {})
    cac_dich = cau_hinh_bao.get("dich", [])
    _, co_dich_hop_le = _trich_dich_hop_le_cau_hinh(so_mod, cac_dich)

    # Đếm số máy lạ chưa báo hết các đích hợp lệ
    chua_bao = 0
    bao_loi = False
    for tb in thiet_bi.values():
        if tb.get("trang_thai") == "la":
            # Nếu chưa chốt hoặc chưa có đích nào báo thành công -> tính là chưa báo
            if not tb.get("da_bao"):
                chua_bao += 1
        # Kiểm tra cờ bao_loi nếu có máy lỗi thử lại >= TOI_DA_THU_LAI_BAO
        for lan in tb.get("lan_loi_bao", {}).values():
            if lan >= TOI_DA_THU_LAI_BAO:
                bao_loi = True

    return {
        "tong": tong,
        "la": la,
        "quen": quen,
        "bo_qua": bo_qua,
        "chua_bao": chua_bao,
        "da_chot": bool(so.get("da_chot", False)),
        "tam_dung": bool(so.get("tam_dung", False)),
        "dich_hop_le": co_dich_hop_le,
        "loi_kho": False,
        "trang_thai_nguon": so.get("trang_thai_nguon", {}),
        "bao_loi": bao_loi,
    }


@router.post("/chot")
async def chot_so():
    """Chốt sổ lần đầu: đánh dấu mọi máy hiện có 'la' thành 'quen' và đặt da_chot = true."""
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    thiet_bi = so.setdefault("thiet_bi", {})
    dem = 0
    for tb in thiet_bi.values():
        if tb.get("trang_thai") == "la":
            tb["trang_thai"] = "quen"
            dem += 1

    so["da_chot"] = True
    so["tam_dung"] = False   # chốt = bắt đầu sạch; không để cờ tạm dừng cũ làm im lặng báo máy lạ
    ghi_so(so)
    nhat_ky.ghi("so_thiet_bi", "thong_tin", f"Quản trị viên đã chốt sổ ({dem} máy thành quen)", so_luong=dem)
    return {"ok": True, "da_chot": True, "so_luong": dem}


@router.post("/tiep-tuc")
async def tiep_tuc():
    """Tiếp tục báo tự động sau khi bị tạm dừng chống bão."""
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    so["tam_dung"] = False
    ghi_so(so)
    nhat_ky.ghi("so_thiet_bi", "thong_tin", "Quản trị viên đã tiếp tục báo thiết bị")
    return {"ok": True, "tam_dung": False}


@router.put("/bao")
async def luu_cau_hinh_bao(request: Request):
    """Lưu cấu hình báo: {bat, dich: [{module, kenh_id}], bao_mac_ngau_nhien}."""
    so_mod = getattr(getattr(request.app, "state", None), "so", None)
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    try:
        body = await request.json()
    except Exception:
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON.", code="invalid_request")

    if not isinstance(body, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON object.", code="invalid_request")

    bat = bool(body.get("bat", False))
    bao_nn = body.get("bao_mac_ngau_nhien", "gop")
    if bao_nn not in CHE_DO_MAC_NGAU_NHIEN_HOP_LE:
        raise LoiOpenAI(
            400,
            f"Trường «bao_mac_ngau_nhien» phải là một trong {list(CHE_DO_MAC_NGAU_NHIEN_HOP_LE)}.",
            code="invalid_request",
        )

    raw_dich = body.get("dich", [])
    if not isinstance(raw_dich, list):
        raise LoiOpenAI(400, "Trường «dich» phải là danh sách.", code="invalid_request")

    dich_luu = []
    for d in raw_dich:
        if not isinstance(d, dict):
            raise LoiOpenAI(400, "Mục trong «dich» phải là object.", code="invalid_request")
        mid = d.get("module")
        if not isinstance(mid, str) or not mid.strip():
            raise LoiOpenAI(400, "Mục trong «dich» thiếu «module».", code="invalid_request")
        mid = mid.strip()

        # Kiểm module đang chạy và có khả năng kenh_ra
        if so_mod is None or mid not in so_mod.dang_chay:
            raise LoiOpenAI(400, f"Module «{mid}» không tồn tại hoặc chưa chạy.", code="module_khong_co")
        m = so_mod.dang_chay[mid]
        km = so_mod.catalog.get(mid)
        man = getattr(km, "manifest", None) if km else None
        if not isinstance(man, dict) or "kenh_ra" not in man.get("cung_cap", []):
            raise LoiOpenAI(400, f"Module «{mid}» không hỗ trợ chuẩn gửi ra «kenh_ra».", code="module_khong_co")

        if not callable(getattr(m, "kenh_ra", None)):
            raise LoiOpenAI(502, f"Module «{mid}» thiếu phương thức kenh_ra().", code="module_loi")

        ds_kenh = m.kenh_ra()
        if not isinstance(ds_kenh, list) or not ds_kenh:
            raise LoiOpenAI(400, f"Module «{mid}» chưa có đích gửi nào khả dụng.", code="chua_co_kenh")

        kid = d.get("kenh_id")
        if kid is not None and not isinstance(kid, str):
            raise LoiOpenAI(400, "Trường «kenh_id» phải là chuỗi.", code="invalid_request")
        kid = kid.strip() if isinstance(kid, str) else None

        if not kid:
            if len(ds_kenh) == 1:
                kid = ds_kenh[0].get("id")
            else:
                raise LoiOpenAI(400, f"Phải chỉ định «kenh_id» vì module «{mid}» có nhiều đích.", code="kenh_required")

        # Kiểm kid có trong ds_kenh không
        hop_le = any(isinstance(k, dict) and k.get("id") == kid for k in ds_kenh)
        if not hop_le:
            raise LoiOpenAI(400, f"Đích «{kid}» không tồn tại trong module «{mid}».", code="kenh_khong_co")

        dich_luu.append({"module": mid, "kenh_id": kid})

    so["cau_hinh_bao"] = {
        "bat": bat,
        "dich": dich_luu,
        "bao_mac_ngau_nhien": bao_nn,
    }
    ghi_so(so)
    return so["cau_hinh_bao"]


@router.get("/bao")
async def lay_cau_hinh_bao(request: Request):
    """Xem cấu hình báo hiện tại kèm tính hợp lệ của từng đích."""
    so_mod = getattr(getattr(request.app, "state", None), "so", None)
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    ch = dict(so.get("cau_hinh_bao", {"bat": False, "dich": [], "bao_mac_ngau_nhien": "gop"}))
    ds_dich, _ = _trich_dich_hop_le_cau_hinh(so_mod, ch.get("dich", []))
    ch["dich"] = ds_dich
    return ch


@router.post("/bao/thu")
async def gui_thu_bao(request: Request):
    """Gửi một tin thử nghiệm tới các đích đã lưu để kiểm tra kênh."""
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    cau_hinh_bao = so.get("cau_hinh_bao", {})
    danh_sach_dich = cau_hinh_bao.get("dich", [])
    if not danh_sach_dich:
        raise LoiOpenAI(400, "Chưa cấu hình đích gửi nào.", code="chua_co_kenh")

    ket_qua = []
    tat_ca_ok = True
    noi_dung_thu = {"van_ban": "Thử nghiệm kênh báo sổ thiết bị web2api"}

    for d in danh_sach_dich:
        mid = d.get("module")
        kid = d.get("kenh_id")
        try:
            res = await gui_ra.gui_den_dich(request.app, mid, kid, noi_dung_thu, nguoi_goi="so_thiet_bi")
            ok = bool(isinstance(res, dict) and res.get("ok"))
            if not ok:
                tat_ca_ok = False
            ket_qua.append({"module": mid, "kenh_id": kid, "ok": ok, "loi": None if ok else "gui_that_bai"})
        except Exception as e:
            tat_ca_ok = False
            # KHÔNG lộ lỗi thô ra ngoài — chỉ nêu mã kết quả chung
            log.warning("Thử gửi tin tới %s|%s thất bại: %s", mid, kid, type(e).__name__)
            ket_qua.append({"module": mid, "kenh_id": kid, "ok": False, "loi": "gui_that_bai"})

    return {"ok": tat_ca_ok, "ket_qua": ket_qua}


@router.patch("/{mac:path}")
async def sua_thiet_bi(mac: str, request: Request):
    """Cập nhật tên hoặc trạng thái của một thiết bị trong sổ."""
    mac_chuan = _kiem_mac_url(mac)
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    thiet_bi = so.get("thiet_bi", {})
    if mac_chuan not in thiet_bi:
        raise LoiOpenAI(404, "Không tìm thấy thiết bị trong sổ.", code="thiet_bi_khong_co")

    try:
        body = await request.json()
    except Exception:
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON.", code="invalid_request")

    if not isinstance(body, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON object.", code="invalid_request")

    tb = thiet_bi[mac_chuan]

    if "trang_thai" in body:
        tt = body["trang_thai"]
        if tt not in TRANG_THAI_HOP_LE:
            raise LoiOpenAI(
                400,
                f"Trường «trang_thai» phải là một trong {list(TRANG_THAI_HOP_LE)}.",
                code="invalid_request",
            )
        tb["trang_thai"] = tt

    if "ten" in body:
        tb["ten"] = lam_sach_ten_chu_may(body["ten"])

    ghi_so(so)
    return tb


@router.delete("/{mac:path}")
async def xoa_thiet_bi(mac: str):
    """Xoá thiết bị khỏi sổ."""
    mac_chuan = _kiem_mac_url(mac)
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu sổ thiết bị bị hỏng.", code="kho_loi") from e

    thiet_bi = so.get("thiet_bi", {})
    if mac_chuan not in thiet_bi:
        raise LoiOpenAI(404, "Không tìm thấy thiết bị trong sổ.", code="thiet_bi_khong_co")

    del thiet_bi[mac_chuan]
    ghi_so(so)
    nhat_ky.ghi("so_thiet_bi", "thong_tin", f"Đã xoá thiết bị {che_mac(mac_chuan)} khỏi sổ", mac=che_mac(mac_chuan))
    return {"ok": True, "mac": mac_chuan}
