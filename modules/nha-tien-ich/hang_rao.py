"""Sáu hàng rào an toàn bắt buộc của module nha-tien-ich.

Port ý tưởng từ c2a (services/kich_hoat_nha.py, canh_bao_nha.py, kich_ban_nha.py,
commit d879b03) sang khuôn module web2api; mọi ngưỡng đều học từ dữ liệu hoặc
là hằng an toàn chung — không ngưỡng cứng theo tên cảm biến/thiết bị.
"""
from __future__ import annotations

import time
from typing import Any

#: Trạng thái «không rõ» — KHÔNG BAO GIỜ được coi là tắt/vắng (hàng rào 6).
#: Port từ c2a ``kich_hoat_nha._KHONG_RO``: nhận diện bằng trạng thái, không bằng chuỗi.
TRANG_THAI_KHONG_RO = frozenset({"unavailable", "unknown", "none", ""})

#: Miền (domain) thực thể NGUY HIỂM — không bao giờ tự chạy trong kịch bản.
#: Port từ ``modules/home-assistant`` (lock/valve/water_heater/alarm_control_panel,
#: cover gara/cổng/cửa → nguy_hiem) + yêu cầu NV195 (khoá, bếp, bình nóng lạnh).
MIEN_NGUY_HIEM = frozenset({
    "lock", "water_heater", "alarm_control_panel", "valve", "cover", "siren",
    "vacuum",  # robot có thể gây hại khi tự chạy
})

#: Miền AN TOÀN đã biết cho kịch bản tự động. Miền LẠ (loại mới chưa biết)
#: mặc định coi là NGUY HIỂM (fail-closed) — yêu cầu NV195 hàng rào 2.
MIEN_AN_TOAN = frozenset({
    "light", "switch", "fan", "input_boolean", "scene", "media_player",
})

#: device_class báo bếp/lò — nguy hiểm dù domain có vẻ lành.
#: Port: c2a xác định khoá/bếp/bình «không bao giờ tự làm»; ở đây xác định theo
#: LOẠI do home-assistant báo (device_class), không theo tên.
DEVICE_CLASS_BEP_LO = frozenset({
    "hob", "oven", "stove", "cooktop", "cooker", "grill", "microwave",
})


def la_thuc_the_nguy_hiem(mien: str, device_class: str = "") -> bool:
    """Thực thể có nguy hiểm với kịch bản tự động không?

    Xác định theo LOẠI (domain/device_class) do home-assistant báo, KHÔNG theo
    tên. Loại mới chưa biết (không trong MIEN_AN_TOAN) ⇒ nguy hiểm (fail-closed).
    """
    mien = str(mien or "").strip().lower()
    dc = str(device_class or "").strip().lower()
    if mien in MIEN_NGUY_HIEM:
        return True
    if dc in DEVICE_CLASS_BEP_LO:
        return True
    return mien not in MIEN_AN_TOAN


def la_trang_thai_khong_ro(trang_thai: Any) -> bool:
    """Trạng thái có phải «không rõ» (mất kết nối) không — so bằng tập trạng
    thái, không dò chuỗi con (hàng rào 6)."""
    return str(trang_thai or "").strip().lower() in TRANG_THAI_KHONG_RO


def coi_la_tat(trang_thai: Any) -> bool:
    """«Tắt» thật để kích hoạt hành động: unavailable/unknown KHÔNG được coi là
    tắt (hàng rào 6 — chống tự gây hại khi mất kết nối)."""
    if la_trang_thai_khong_ro(trang_thai):
        return False
    return str(trang_thai or "").strip().lower() in ("off", "closed", "locked", "idle", "standby")


# ── Hàng rào 3: chống lặp kịch bản ────────────────────────────────────────────
#: Tối đa ngần này lượt chạy lồng nhau trong một chuỗi kích hoạt (A→B→A…).
SAU_TOI_DA = 3
#: Một kịch bản được chạy tối đa ngần này lần trong cửa sổ dưới (chống dội vòng).
LAP_TOI_DA = 5
LAP_CUA_SO_GIAY = 60.0


class ChongLap:
    """Chống lặp kịch bản: dấu vết chuỗi gốc + giới hạn số lần/cửa sổ.

    Port ý tưởng từ c2a ``kich_hoat_nha._vua_lam`` (chốt NGHI_LAP chống nhấp nháy
    và vòng kích hoạt) sang dạng dùng được cho kịch bản lồng nhau.
    """

    def __init__(self) -> None:
        self._chuoi: list[str] = []   # ngăn xếp id kịch bản đang chạy
        self._lan_chay: list[tuple[str, float]] = []  # (id, mốc) các lượt đã chạy

    def vao(self, kich_ban_id: str, now: float | None = None) -> dict[str, Any]:
        """Xin vào chạy kịch bản. Trả {"ok": True} hoặc {"ok": False, "loi": ...}."""
        now = float(now if now is not None else time.time())
        kid = str(kich_ban_id)
        if kid in self._chuoi:
            return {"ok": False, "loi": "vong_lap",
                    "chi_tiet": f"Kịch bản «{kid}» đã có trong chuỗi kích hoạt "
                                f"({' → '.join(self._chuoi)} → {kid}): dừng để chống lặp."}
        if len(self._chuoi) >= SAU_TOI_DA:
            return {"ok": False, "loi": "sau_qua",
                    "chi_tiet": f"Chuỗi kích hoạt đã sâu {len(self._chuoi)} (tối đa {SAU_TOI_DA})."}
        moc = now - LAP_CUA_SO_GIAY
        self._lan_chay = [(k, t) for k, t in self._lan_chay if t >= moc]
        dem = sum(1 for k, _ in self._lan_chay if k == kid)
        if dem >= LAP_TOI_DA:
            return {"ok": False, "loi": "chay_day",
                    "chi_tiet": f"Kịch bản «{kid}» đã chạy {dem} lần trong "
                                f"{LAP_CUA_SO_GIAY:.0f} giây qua (tối đa {LAP_TOI_DA})."}
        self._chuoi.append(kid)
        self._lan_chay.append((kid, now))
        return {"ok": True}

    def ra(self, kich_ban_id: str) -> None:
        try:
            self._chuoi.remove(str(kich_ban_id))
        except ValueError:
            pass

    def dau_vet(self) -> list[str]:
        return list(self._chuoi)


# ── Hàng rào 4: chống dội cảnh báo ───────────────────────────────────────────
#: Gom cảnh báo cùng thiết bị + loại trong ngần này giây (chống dội).
DOI_GOM_GIAY = 300.0
#: Nhịp báo lại thưa dần — port từ c2a ``canh_bao_nha._NHIP``: 5p → 30p → 60p → 6h → 24h.
NHIP_BAO_LAI = (5 * 60, 30 * 60, 60 * 60, 6 * 3600, 24 * 3600)

MUC_DO = ("thap", "trung_binh", "cao", "khan_cap")


def loc_nhay_cam(noi_dung: str) -> str:
    """Lọc nội dung nhạy cảm khỏi cảnh báo kênh công cộng: che tên người và vị
    trí chính xác. Port nguyên tắc c2a (không gửi tên/vị trí ra kênh công cộng)."""
    import re
    s = str(noi_dung or "")
    # Che toạ độ / địa chỉ số nhà chi tiết.
    s = re.sub(r"\d{1,3}[,.]\d{3,}", "〈toạ độ đã che〉", s)
    s = re.sub(r"số\s+\d+[a-zA-Z]?\s+đường\s+\S+", "〈địa chỉ đã che〉", s, flags=re.IGNORECASE)
    # Che tên người dạng «anh X»/«chị X»/«ông X»/«bà X» giữ lại vai.
    s = re.sub(r"\b(anh|chị|chi|ông|ong|bà|ba|em|cô|co|chú|chu)\s+[A-ZÀ-Ỹ][a-zà-ỹ]+",
               r"\1 〈đã che tên〉", s)
    return s
