"""Chẩn đoán sự cố thiết bị — vì sao thiết bị không theo lệnh / mất kết nối.

Port từ ``services/su_co_thiet_bi.py`` (c2a, commit d879b03), giữ nguyên cây
chẩn đoán của ``chan_doan``:

- HA không trả lời → «mất kết nối tới Home Assistant».
- Thực thể không còn → «thực thể không còn».
- ``unavailable`` → phân biệt: nhiều thực thể cùng mất lúc ≈ nhau (≥20 thực thể /
  ≥3 tích hợp) → «mất mạng / HA khởi động lại»; cả một tích hợp cùng mất (≥80%
  thực thể của tích hợp) → «cả tích hợp mất kết nối»; còn lại → «thiết bị mất
  kết nối» (kèm gợi ý kiểm tra).
- Đã gửi lệnh mà trạng thái không đổi (thiết bị còn kết nối): ``assumed_state``
  → «điều khiển một chiều» (IR/RF); không gửi gì >6h → «thiết bị đơ»; còn lại →
  «thiết bị không theo lệnh».

Hàng rào 6 được giữ: ``unavailable`` LUÔN chẩn đoán là mất kết nối, không bao
giờ suy ra «tắt/vắng» để kích hoạt hành động.

Chỉ đổi: ``ha_client.get_states()`` → ``co_che.doc_trang_thai()``; chỉ mục khu
vực/tích hợp của HA (``get_ha_area_index``) không có trong module → dùng
``attributes.integration`` nếu nguồn đọc cung cấp, thiếu thì bỏ qua bước đó.
"""
from __future__ import annotations

import time
from typing import Any

from . import co_che, hang_rao

#: Cùng mất kết nối trong ngần này giây thì coi là cùng một đợt.
CUNG_LUC = 300.0
#: Không gửi gì quá ngần này giây mà còn kết nối → «đơ».
DO_GIAY = 6 * 3600.0


def _goc(tb: str) -> str:
    return str(tb or "").split(".", 1)[0]


def chan_doan(tb: str, *, sau_lenh: str = "") -> dict[str, str]:
    """{"nguyen_nhan": nhãn ngắn, "chi_tiet": câu đầy đủ}.

    ``sau_lenh`` = "on"/"off": đã gửi lệnh đó mà trạng thái không đổi (thiết bị
    còn kết nối) → xét đơ/không theo lệnh.
    """
    try:
        tat_ca = co_che.doc_trang_thai() or []
    except Exception:
        tat_ca = []
    if not tat_ca:
        return {"nguyen_nhan": "mất kết nối tới Home Assistant",
                "chi_tiet": "Không đọc được trạng thái — nguồn HA chưa cấu hình hoặc mất kết nối"}
    ds = {str(s.get("entity_id")): s for s in tat_ca}
    s = ds.get(tb)
    if s is None:
        return {"nguyen_nhan": "thực thể không còn",
                "chi_tiet": f"Không còn mã {tb} — thiết bị đã bị xoá hoặc đổi mã"}
    tt = str(s.get("state") or "").lower()
    at = s.get("attributes") or {}
    if hang_rao.la_trang_thai_khong_ro(tt):
        # Nhiều thực thể cùng mất lúc ≈ nhau → mất mạng / HA khởi động lại.
        cung = [m for m, x in ds.items()
                if hang_rao.la_trang_thai_khong_ro(str(x.get("state") or "").lower())]
        tich_hop = {str((x.get("attributes") or {}).get("integration") or "")
                    for x in (ds[m] for m in cung)} - {""}
        if len(cung) >= 20 and len(tich_hop) >= 3:
            return {"nguyen_nhan": "mất mạng / HA khởi động lại",
                    "chi_tiet": f"{len(cung)} thực thể của {len(tich_hop)} tích hợp cùng mất kết nối — "
                                "mạng nội bộ chập hoặc Home Assistant vừa khởi động lại"}
        # Cả một tích hợp cùng mất (≥80%) → cầu nối/hub của tích hợp hỏng.
        p = str(at.get("integration") or "")
        if p:
            tong_th = [m for m, x in ds.items()
                       if str((x.get("attributes") or {}).get("integration") or "") == p]
            cung_th = [m for m in cung
                       if str((ds[m].get("attributes") or {}).get("integration") or "") == p]
            if tong_th and len(cung_th) >= 0.8 * len(tong_th):
                return {"nguyen_nhan": f"cả tích hợp {p} mất kết nối",
                        "chi_tiet": f"{len(cung_th)}/{len(tong_th)} thực thể của tích hợp «{p}» cùng mất — "
                                    "cầu nối/hub của tích hợp hỏng (bộ điều phối Zigbee, MQTT, dịch vụ đám mây…), "
                                    "không phải riêng thiết bị"}
        return {"nguyen_nhan": "thiết bị mất kết nối",
                "chi_tiet": f"«{tb}» báo unavailable/unknown — kiểm tra nguồn điện, mạng của thiết bị "
                            "và tích hợp cung cấp nó trong HA (Cài đặt › Thiết bị & Dịch vụ)"}
    if sau_lenh:
        if at.get("assumed_state"):
            return {"nguyen_nhan": "điều khiển một chiều",
                    "chi_tiet": "Thiết bị hồng ngoại/RF không báo trạng thái thật — lệnh có thể không tới "
                                "(bị chắn, bộ phát IR hỏng)"}
        cap_nhat = s.get("last_updated")
        try:
            tuoi = time.time() - float(cap_nhat) if cap_nhat else 0.0
        except (TypeError, ValueError):
            tuoi = 0.0
        if tuoi > DO_GIAY:
            return {"nguyen_nhan": "thiết bị đơ",
                    "chi_tiet": "Còn kết nối nhưng không gửi gì quá 6 giờ và không theo lệnh — treo; "
                                "ngắt nguồn rồi cấp lại"}
        return {"nguyen_nhan": "thiết bị không theo lệnh",
                "chi_tiet": f"Nhận lệnh {'bật' if sau_lenh == 'on' else 'tắt'} mà vẫn «{tt}» — treo (đơ), "
                            "kẹt rơ-le hoặc bị khoá trẻ em/công tắc cứng"}
    return {"nguyen_nhan": "", "chi_tiet": ""}
