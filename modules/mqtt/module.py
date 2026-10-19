"""Module mqtt — nguồn THIẾT BỊ NHÀ qua MQTT cho chuẩn C1, KHÔNG cần Home Assistant.

Port từ c2a (kho của chủ máy): api/mqtt.py @ commit d879b03 — chỉ lấy Ý của các
đường test / quet / thiet-bi / dieu-khien (lich-su / tuya / mang-nha / dien-nha /
tinh-huong / du-doan / canh-bao / soi-hong / nap-ha KHÔNG thuộc phạm vi, là việc
của module khác sau này). Lớp kết nối + khám phá nằm ở ``mqtt_lop.py`` (port từ
c2a services/mqtt_nha.py @ d879b03); file này chỉ là khuôn module web2api.

Bảo mật:
- Mật khẩu MQTT: trường ``bi_mat`` trong manifest → kho bí mật 0600 của lõi;
  không vào log, phản hồi, suc_khoe, modules.json (có test khẳng định).
- Địa chỉ do quản trị khai đi qua ĐÚNG bộ kiểm ``app/tu_xa.py``
  (``_dia_chi_bi_chan``): cho LAN riêng tư (MQTT chạy ở LAN là đúng mục đích),
  chặn loopback/link-local/metadata/không định tuyến; KHÔNG nới bộ kiểm.
- GHI (publish lệnh) mặc định TẮT: chỉ đọc cho tới khi bật ô
  «cho phép điều khiển». Loại nguy_hiem (khoá cửa, bình nóng lạnh, van, báo
  động — phân loại bằng CẤU TRÚC component trong bản tự khai báo, không bằng
  chữ trong tên) KHÔNG BAO GIỜ điều khiển được.
- Chủ đề đích của lệnh chỉ lấy từ bản tự khai báo đã nạp — không ghép từ chuỗi
  người gọi (chống chủ đề tuỳ ý).
- Dữ liệu từ máy chủ MQTT là KHÔNG TIN: payload > 64 KiB bị bỏ, giá trị cắt ở
  4096 ký tự, tối đa 5000 chủ đề, không ngoại lệ thô.
- Mất kết nối ≠ 0 thiết bị: suc_khoe báo «chưa kết nối được: <lý do>», giữ dữ
  liệu cũ có dấu thời gian.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import unicodedata
from typing import Any

from app.tu_xa import _dia_chi_bi_chan

from .mqtt_lop import LoiMqtt, LopMqtt, che_mat_khau

log = logging.getLogger("web2api.mqtt")

#: Component trong bản tự khai báo → nguy_hiem: KHÔNG BAO GIỜ điều khiển được
#: qua module này. Phân loại bằng CẤU TRÚC (component), không bằng chữ trong
#: tên — theo cách home-assistant gắn rủi ro theo miền.
LOAI_NGUY_HIEM = frozenset({
    "lock",               # khoá cửa
    "alarm_control_panel",  # báo động
    "water_heater",       # bình nóng lạnh
    "valve",              # van (gas/nước)
    "siren",              # còi báo động
})

#: Hành động điều khiển hỗ trợ cho thực thể có command_topic và không nguy hiểm.
HANH_DONG_DIEU_KHIEN = ("bat", "tat")


def _la_dung(gia_tri: Any) -> bool:
    return str(gia_tri or "").strip().lower() in ("true", "1", "co", "có", "yes", "on")


def _sach_id(s: str) -> str:
    """Tên thiết bị/thực thể → mảnh id ASCII an toàn.

    Bỏ dấu tiếng Việt trước rồi mới lọc ký tự: id ``mqtt.khoa_thu_1``
    tra được bằng chữ không dấu, dùng được trong URL/API. (Tên hiển thị
    ``ten`` vẫn giữ nguyên dấu.)
    """
    s = unicodedata.normalize("NFD", s or "").strip()
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^0-9a-zA-Z_-]", "_", s)[:80].strip("_") or "tb"


class Module:
    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.tien_to = manifest.get("tien_to") or "mqtt"
        self._lop: LopMqtt | None = None
        self._cho_phep_dieu_khien = False
        self._ban_do: dict[str, tuple[str, str]] = {}  # id -> (ten_thiet_bi, ten_thuc_the)

    # ── cấu hình ─────────────────────────────────────────────────────

    @staticmethod
    async def kiem_dia_chi(dia_chi: str) -> str:
        """Kiểm «may[:cong]» bằng ĐÚNG bộ kiểm app/tu_xa.py. Trả dia_chi đã chuẩn hoá.

        Cho LAN riêng tư (MQTT chạy ở LAN là đúng mục đích); chặn loopback
        (trừ test), link-local/metadata, multicast, không định tuyến.
        """
        dc = str(dia_chi or "").strip()
        if not dc:
            raise ValueError("Chưa nhập địa chỉ máy chủ MQTT (dia_chi).")
        tam = LopMqtt({"dia_chi": dc})
        try:
            host, cong = tam._tach_may_chu()
        except LoiMqtt as e:
            raise ValueError(str(e))
        try:
            ds = [ipaddress.ip_address(host)]
        except ValueError:
            # RFC 2606: .invalid không bao giờ phân giải được — chặn ngay,
            # không phụ thuộc DNS (một số môi trường trả lời bừa mọi tên).
            if host.lower() == "invalid" or host.lower().endswith(".invalid"):
                raise ValueError(f"không phân giải được tên máy {host!r}: "
                                 "tên miền .invalid không tồn tại (RFC 2606)")
            try:
                thong_tin = await asyncio.get_running_loop().getaddrinfo(
                    host, cong, type=socket.SOCK_STREAM)
            except OSError as e:
                raise ValueError(f"không phân giải được tên máy {host!r}: {e}")
            ds = list({ipaddress.ip_address(x[4][0]) for x in thong_tin})
        for ip in ds:
            ly_do = _dia_chi_bi_chan(ip)
            if ly_do:
                raise ValueError(f"địa chỉ MQTT trỏ vào {ip} — bị chặn: {ly_do}")
        return f"{host}:{cong}"

    def nap(self, cau_hinh: dict) -> None:
        """Nạp cấu hình ĐÃ KIỂM — không gọi mạng đồng bộ, chỉ khởi động luồng nền."""
        cau_hinh = cau_hinh or {}
        dia_chi = str(cau_hinh.get("dia_chi") or "").strip()
        self._cho_phep_dieu_khien = _la_dung(cau_hinh.get("cho_phep_dieu_khien"))
        if self._lop is not None:
            self._lop.stop()
        self._lop = LopMqtt({
            "dia_chi": dia_chi,
            "nguoi_dung": cau_hinh.get("nguoi_dung"),
            "mat_khau": cau_hinh.get("mat_khau"),
        })
        self._ban_do = {}
        if dia_chi:
            self._lop.start()  # luồng nền tự nối lại; không có dia_chi thì không chạy

    async def cai(self, cau_hinh: dict) -> dict:
        """Kiểm cấu hình bằng cách nối THẬT tới máy chủ (nút «Kiểm tra»)."""
        cau_hinh = cau_hinh or {}
        try:
            dia_chi = await self.kiem_dia_chi(cau_hinh.get("dia_chi"))
        except ValueError as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        nguoi_dung = str(cau_hinh.get("nguoi_dung") or "").strip()
        mat_khau = str(cau_hinh.get("mat_khau") or "")
        cho_phep = _la_dung(cau_hinh.get("cho_phep_dieu_khien"))
        lop = LopMqtt({"dia_chi": dia_chi, "nguoi_dung": nguoi_dung,
                       "mat_khau": mat_khau})
        try:
            kq = await asyncio.to_thread(lop.thu_ket_noi, 5.0)
        except LoiMqtt as e:
            return {"ok": False, "ket_qua_tung_khoa": [], "loi": str(e)}
        if not kq.get("ok"):
            return {"ok": False, "ket_qua_tung_khoa": [],
                    "loi": kq.get("error") or "Không nối được tới máy chủ MQTT."}
        if not kq.get("so_nhanh"):
            log.warning("mqtt: nối được %s nhưng không tin nào về — có thể quyền bị giới hạn", dia_chi)
        cau_hinh_luu = {
            "dia_chi": dia_chi,
            "nguoi_dung": nguoi_dung,
            "mat_khau": mat_khau,  # lõi tách vào kho bí mật 0600, không trả ra API
            "cho_phep_dieu_khien": "true" if cho_phep else "false",
        }
        self.nap(cau_hinh_luu)  # nạp ngay để thể hiện dùng được sau cai()
        return {
            "ok": True,
            "ket_qua_tung_khoa": [
                {"khoa": "dia_chi", "ok": True, "loi": None,
                 "chi_tiet": f"nối được, thấy {kq.get('so_nhanh', 0)} nhánh"}],
            "loi": None,
            "cau_hinh_luu": cau_hinh_luu,
        }

    async def suc_khoe(self) -> dict:
        # Mật khẩu KHÔNG BAO GIỜ xuất hiện ở đây (test khẳng định).
        if self._lop is None or not self._lop.dia_chi:
            return {"ok": False, "loi": "Module MQTT chưa được cấu hình (dia_chi)."}
        s = self._lop.suc_khoe_dict()
        if not s.get("connected"):
            ly_do = s.get("last_error") or "chưa nối được"
            return {
                "ok": False,
                "loi": f"chưa kết nối được: {ly_do}",
                # Giữ dữ liệu cũ: mất kết nối ≠ 0 thiết bị.
                "so_thiet_bi": s.get("thiet_bi", 0),
                "tin_da_nhan": s.get("tin", 0),
                "du_lieu_luc": s.get("last_tin_ts") or None,
            }
        return {
            "ok": True,
            "dia_chi": self._lop.dia_chi,
            "so_thiet_bi": s.get("thiet_bi", 0),
            "so_nhanh": s.get("nhanh", 0),
            "tin_da_nhan": s.get("tin", 0),
            "du_lieu_cu": bool(s.get("cu")),
        }

    async def dung(self) -> None:
        if self._lop is not None:
            await asyncio.to_thread(self._lop.stop)
            self._lop = None

    # ── chuẩn thiet_bi ─────────────────────────────────────────────

    def _rui_ro(self, loai: str, dieu_khien_duoc: bool) -> str:
        if (loai or "") in LOAI_NGUY_HIEM:
            return "nguy_hiem"
        if dieu_khien_duoc:
            return "ghi"
        return "doc"

    def _thuc_the_tu_danh_sach(self) -> list[dict]:
        """Dựng thực thể C1 từ sổ đã khám phá; đồng thời nạp bản đồ id."""
        if self._lop is None:
            return []
        ra: list[dict] = []
        self._ban_do = {}
        dem: dict[str, int] = {}
        for tb in self._lop.danh_sach():
            nguon = tb.get("nguon")
            # (mục, điều khiển được?): chỉ mục trong danh_sach «dieu_khien»
            # (có command_topic) mới là ứng viên điều khiển; frigate/tho luôn
            # chỉ đọc.
            ung_vien = [(m, True) for m in (tb.get("dieu_khien") or [])]
            ung_vien += [(m, False) for m in (tb.get("doc") or [])]
            for muc, co_lenh in ung_vien:
                ten_tb = tb["ten"]
                ten_tt = muc.get("ten") or ""
                loai = muc.get("loai") or ("camera" if nguon == "frigate" else "cam_bien")
                dieu_khien_duoc = (co_lenh and nguon == "tu_khai_bao"
                                   and loai not in LOAI_NGUY_HIEM)
                rui_ro = self._rui_ro(loai, dieu_khien_duoc)
                id_goc = f"{_sach_id(ten_tb)}_{_sach_id(ten_tt)}"
                dem[id_goc] = dem.get(id_goc, 0) + 1
                ma = id_goc if dem[id_goc] == 1 else f"{id_goc}_{dem[id_goc]}"
                eid = f"{self.tien_to}.{ma}"
                tinh_nang = (
                    [{"hanh_dong": "bat", "mo_ta": "Bật"},
                     {"hanh_dong": "tat", "mo_ta": "Tắt"}]
                    if dieu_khien_duoc else []
                )
                thuoc_tinh: dict[str, Any] = {}
                if muc.get("don_vi"):
                    thuoc_tinh["don_vi"] = str(muc["don_vi"])[:20]
                if loai:
                    thuoc_tinh["component"] = str(loai)[:40]
                ra.append({
                    "id": eid,
                    "ten": f"{ten_tb} — {ten_tt}" if ten_tt else ten_tb,
                    "loai": loai,  # mã mở
                    "thuoc_tinh": thuoc_tinh,
                    "tinh_nang": tinh_nang,
                    "rui_ro_mac_dinh": rui_ro,
                })
                self._ban_do[eid] = (ten_tb, ten_tt)
        return ra

    async def thuc_the(self, loc: dict | None = None) -> list[dict]:
        ds = self._thuc_the_tu_danh_sach()
        if isinstance(loc, dict) and loc.get("loai"):
            ds = [e for e in ds if e.get("loai") == loc["loai"]]
        return ds

    async def trang_thai(self, ids: list[str]) -> dict[str, dict]:
        if self._lop is None:
            return {}
        # đảm bảo bản đồ id đã nạp
        self._thuc_the_tu_danh_sach()
        kq: dict[str, dict] = {}
        with self._lop._khoa:
            gt = dict(self._lop._gia_tri)
        for mid in ids or []:
            if not isinstance(mid, str) or not mid.startswith(f"{self.tien_to}."):
                continue
            anh_xa = self._ban_do.get(mid)
            if not anh_xa:
                continue
            ten_tb, ten_tt = anh_xa
            with self._lop._khoa:
                ents = dict(self._lop._so_thiet_bi.get(ten_tb) or {})
            m = ents.get(ten_tt, {})
            ct = m.get("doc") or ""
            v = gt.get(ct)
            kq[mid] = {
                "gia_tri": v[0][:200] if v else None,
                "don_vi": (m.get("don_vi") or "")[:20] or None,
                "luc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   __import__("time").gmtime(v[1])) if v else None,
                "hop_le": bool(v),
            }
        return kq

    async def thuc_hien(self, id: str, hanh_dong: str, tham_so: dict | None) -> dict:
        """Publish lệnh — qua NHIỀU lớp chốt (defense in depth)."""
        tham_so = tham_so or {}
        if self._lop is None:
            return {"ok": False, "loi": "Module MQTT chưa được cấu hình."}
        if not self._cho_phep_dieu_khien:
            return {"ok": False,
                    "loi": "Điều khiển đang TẮT: bật ô «cho phép điều khiển» trong cấu hình module trước."}
        self._thuc_the_tu_danh_sach()  # nạp bản đồ id mới nhất
        anh_xa = self._ban_do.get(id) if isinstance(id, str) else None
        if not anh_xa:
            return {"ok": False, "loi": "không thấy thực thể trong danh sách của module"}
        ten_tb, ten_tt = anh_xa
        with self._lop._khoa:
            ents = dict(self._lop._so_thiet_bi.get(ten_tb) or {})
        m = ents.get(ten_tt)
        if m is None:
            return {"ok": False, "loi": "không thấy thực thể trong sổ đã khám phá"}
        loai = m.get("loai") or ""
        if loai in LOAI_NGUY_HIEM:
            return {"ok": False,
                    "loi": f"loại «{loai}» nguy hiểm — KHÔNG BAO GIỜ điều khiển được qua module này"}
        ct = m.get("dieu_khien")
        if not ct:
            return {"ok": False, "loi": "thực thể này chỉ đọc được, không điều khiển được"}
        if hanh_dong not in HANH_DONG_DIEU_KHIEN:
            return {"ok": False, "loi": f"hành động «{hanh_dong}» không hỗ trợ (chỉ bat/tat)"}
        # tham_so bị BỎ QUA có chủ ý: chủ đề đích và nội dung lệnh chỉ lấy từ
        # bản tự khai báo đã nạp — kẻ gọi không nhét được chủ đề tuỳ ý.
        try:
            await asyncio.to_thread(self._lop.dieu_khien, ten_tb, ten_tt,
                                    hanh_dong == "bat")
        except LoiMqtt as e:
            return {"ok": False, "loi": str(e)}
        return {"ok": True}
