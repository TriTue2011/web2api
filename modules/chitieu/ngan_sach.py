"""Tính ngân sách một kỳ lương.

Port từ ``~/workspace/c2a-nv146/services/chi_tieu/ngan_sach.py`` @ d879b03 (bản gốc
``/opt/claude-c2a/chatgpt2api/services/chi_tieu/ngan_sach.py`` — chuyển từ ``chi-tieu-mcp/app/jars.py``
(Quiz99, MIT)), chỉ đổi import theo khuôn module web2api. Thuật toán giữ nguyên:
tự bù hũ vượt theo ``thu_tu_bu`` tăng dần; chi phí đặc biệt trừ vào các hũ có
``thu_tu_dac_biet > 0`` theo thứ tự đó. Chỉ đọc, không ghi.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from . import kho, thoi_gian as _tg


def nhan_ky(dt: datetime | date, ngay_bat_dau: int) -> str:
    """Nhãn kỳ lương "YYYY-MM" chứa ``dt``: ngày ≥ ngày bắt đầu kỳ thì là tháng của ``dt``, không thì tháng trước."""
    if dt.day >= ngay_bat_dau:
        return f"{dt.year:04d}-{dt.month:02d}"
    nam, thang = dt.year, dt.month - 1
    if thang == 0:
        nam, thang = nam - 1, 12
    return f"{nam:04d}-{thang:02d}"


def ngay_bat_dau(s: dict[str, Any]) -> int:
    return min(max(int(s.get("ngay_bat_dau") or 1), 1), 28)


def ky_hien_tai(s: dict[str, Any]) -> str:
    return nhan_ky(_tg.bay_gio_dt(), ngay_bat_dau(s))


def lui_ky(nhan: str, so_ky: int) -> str:
    nam, thang = (int(x) for x in nhan.split("-"))
    tong = nam * 12 + (thang - 1) - so_ky
    return f"{tong // 12:04d}-{tong % 12 + 1:02d}"


def ngay_bat_dau_ky_sau(hom_nay: date, ngay_bd: int) -> date:
    if hom_nay.day < ngay_bd:
        return hom_nay.replace(day=ngay_bd)
    if hom_nay.month == 12:
        return date(hom_nay.year + 1, 1, ngay_bd)
    return date(hom_nay.year, hom_nay.month + 1, ngay_bd)


def so_ngay_con_lai(hom_nay: date, ngay_bd: int) -> int:
    return (ngay_bat_dau_ky_sau(hom_nay, ngay_bd) - hom_nay).days


def thu_nhap_ky(s: dict[str, Any], thang: str) -> int:
    """Lương + thu nhập thêm đã ghi trong ĐÚNG kỳ đó — chia theo tỷ lệ hũ như lương."""
    return int(s["luong"]) + kho.tong_ky("thu_nhap_them", s["id"], thang)


def tru_chi_phi_dac_biet(han_muc: dict[int, int], hus: list[dict[str, Any]], tong: int) -> tuple[dict[int, int], int]:
    """Trừ ``tong`` chi phí đặc biệt vào các hũ hứng (``thu_tu_dac_biet`` > 0, nhỏ trước), không cho âm. Trả
    (hạn mức mới, phần còn thiếu khi đã trừ cạn các hũ hứng)."""
    moi = dict(han_muc)
    con = tong
    for h in sorted((h for h in hus if int(h.get("thu_tu_dac_biet") or 0) > 0), key=lambda h: h["thu_tu_dac_biet"]):
        if con <= 0:
            break
        tru = min(moi.get(h["id"], 0), con)
        moi[h["id"]] -= tru
        con -= tru
    return moi, con


def tu_bu(han_muc: dict[int, int], da_chi: dict[int, int], thu_tu: list[int], thieu_dac_biet: int = 0) -> dict[str, Any]:
    """Hàm THUẦN. ``thu_tu``: id hũ theo thứ tự bị rút bù. Phần chi phí đặc biệt còn thiếu được bù TRƯỚC,
    rồi tới các hũ âm theo thứ tự trong ``han_muc``. Không đủ bù thì hũ âm vẫn âm."""
    thu_tu = [i for i in thu_tu if i in han_muc] + [i for i in han_muc if i not in thu_tu]
    con_nhuong = {i: max(0, han_muc[i] - da_chi.get(i, 0)) for i in han_muc}
    da_nhuong = {i: 0 for i in han_muc}
    duoc_bu = {i: 0 for i in han_muc}
    bu_tu: dict[int, list[tuple[int, int]]] = {i: [] for i in han_muc}

    def rut(can: int) -> tuple[list[tuple[int, int]], int]:
        lay_duoc: list[tuple[int, int]] = []
        for i in thu_tu:
            if can <= 0:
                break
            lay = min(con_nhuong[i], can)
            if lay <= 0:
                continue
            con_nhuong[i] -= lay
            da_nhuong[i] += lay
            can -= lay
            lay_duoc.append((i, lay))
        return lay_duoc, can

    _, thieu_chua_bu = rut(max(0, thieu_dac_biet))
    for i in han_muc:
        am = da_chi.get(i, 0) - han_muc[i]
        if am > 0:
            lay, chua = rut(am)
            duoc_bu[i] = am - chua
            bu_tu[i] = lay
    tong_ns = sum(han_muc.values()) - max(0, thieu_dac_biet)
    tong_chi = sum(da_chi.get(i, 0) for i in han_muc)
    return {"han_muc_hieu_luc": {i: han_muc[i] - da_nhuong[i] + duoc_bu[i] for i in han_muc},
            "duoc_bu": duoc_bu, "da_nhuong": da_nhuong, "bu_tu": bu_tu, "thieu_dac_biet_chua_bu": thieu_chua_bu,
            "tong_ngan_sach": tong_ns, "tong_da_chi": tong_chi, "tong_con_lai": tong_ns - tong_chi}


def tinh(so_id: int, thang: str | None = None) -> dict[str, Any]:
    """Trạng thái ngân sách đủ của một kỳ — NGUỒN DUY NHẤT cho công cụ, cảnh báo."""
    s = kho.so(so_id)
    if s is None:
        raise ValueError("Không có sổ này.")
    thang = thang or ky_hien_tai(s)
    hus = kho.ds_hu(so_id)
    thu_nhap = thu_nhap_ky(s, thang)
    goc = {h["id"]: round(thu_nhap * float(h["ty_le"]) / 100) for h in hus}
    tong_db = kho.tong_ky("chi_phi_dac_biet", so_id, thang)
    truoc_bu, thieu = tru_chi_phi_dac_biet(goc, hus, tong_db)
    da_chi_tat_ca = kho.tong_chi_theo_hu(so_id, thang)
    da_chi = {h["id"]: da_chi_tat_ca.get(h["id"], 0) for h in hus}
    # Khoản chi của hũ đã xoá (mềm) vẫn là tiền đã tiêu trong kỳ — tính vào tổng, không gán cho hũ nào.
    chi_hu_da_xoa = sum(v for k, v in da_chi_tat_ca.items() if k not in da_chi)
    bu = tu_bu(truoc_bu, da_chi, [h["id"] for h in sorted(hus, key=lambda h: (h["thu_tu_bu"], h["thu_tu"]))], thieu)
    ten = {h["id"]: h["ten"] for h in hus}
    hu_kq = []
    for h in hus:
        i = h["id"]
        hl = bu["han_muc_hieu_luc"][i]
        hu_kq.append({"id": i, "ten": h["ten"], "ty_le": float(h["ty_le"]), "thu_tu_bu": h["thu_tu_bu"],
                      "thu_tu_dac_biet": h["thu_tu_dac_biet"], "han_muc_truoc_bu": truoc_bu[i], "da_chi": da_chi[i],
                      "duoc_bu": bu["duoc_bu"][i], "da_nhuong": bu["da_nhuong"][i],
                      "bu_tu": [{"id": j, "ten": ten[j], "so_tien": st} for j, st in bu["bu_tu"][i]],
                      "han_muc_hieu_luc": hl, "con_lai": hl - da_chi[i]})
    tong_ns = bu["tong_ngan_sach"]
    tong_chi = bu["tong_da_chi"] + chi_hu_da_xoa
    con_lai = tong_ns - tong_chi
    ty_le = tong_chi / tong_ns if tong_ns > 0 else (1.0 if (tong_chi > 0 or tong_ns < 0) else 0.0)
    return {"so_id": so_id, "thang": thang, "luong": int(s["luong"]), "thu_nhap_hieu_qua": thu_nhap, "hu": hu_kq,
            "tong_chi_phi_dac_biet": tong_db, "con_thieu_dac_biet": thieu,
            "thieu_dac_biet_chua_bu": bu["thieu_dac_biet_chua_bu"],
            "hu_hung_dac_biet": [h["ten"] for h in sorted((h for h in hus if int(h["thu_tu_dac_biet"] or 0) > 0),
                                                         key=lambda h: h["thu_tu_dac_biet"])],
            "tong_ngan_sach": tong_ns, "tong_da_chi": tong_chi, "tong_con_lai": con_lai, "ty_le_tong_da_dung": ty_le}


def ty_le_thuc_te_3_ky(so_id: int, thang: str) -> dict[int, float]:
    """% thu nhập đã chi cho từng hũ, trung bình 3 kỳ TRƯỚC ``thang`` — mỗi kỳ chia cho thu nhập của CHÍNH kỳ đó."""
    s = kho.so(so_id)
    if s is None:
        return {}
    ra: dict[int, float] = {}
    for lui in (1, 2, 3):
        k = lui_ky(thang, lui)
        tn = thu_nhap_ky(s, k)
        if tn <= 0:
            continue
        for hu_id, chi in kho.tong_chi_theo_hu(so_id, k).items():
            ra[hu_id] = ra.get(hu_id, 0.0) + chi / tn * 100 / 3
    return ra


def de_xuat_phan_bo(so_id: int, thang: str) -> dict[str, Any]:
    """% đề xuất từng hũ theo chi thực tế 3 kỳ trước, chuẩn hoá tổng = 100. Không có dữ liệu → giữ % đang đặt."""
    hus = kho.ds_hu(so_id)
    tl = ty_le_thuc_te_3_ky(so_id, thang)
    tho = {h["id"]: tl.get(h["id"], 0.0) for h in hus}
    tong = sum(tho.values())
    if tong <= 0:
        return {"thang": thang, "du_lieu_du": False,
                "de_xuat": [{"id": h["id"], "ten": h["ten"], "ty_le": float(h["ty_le"])} for h in hus]}
    de = [{"id": h["id"], "ten": h["ten"], "ty_le": round(tho[h["id"]] * 100 / tong, 1)} for h in hus]
    du = round(100.0 - sum(d["ty_le"] for d in de), 1)
    lon = max(de, key=lambda d: d["ty_le"])
    lon["ty_le"] = max(0.0, round(lon["ty_le"] + du, 1))
    return {"thang": thang, "du_lieu_du": True, "de_xuat": de}
