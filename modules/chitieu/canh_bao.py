"""Cảnh báo chủ động — kiểm tra các mốc ngân sách của một sổ.

Port logic từ ``~/workspace/c2a-nv146/services/chi_tieu/canh_bao.py`` @ d879b03 (bản gốc
``/opt/claude-c2a/chatgpt2api/services/chi_tieu/canh_bao.py`` — chuyển từ ``chi-tieu-mcp/app/alerts.py``
(Quiz99, MIT)), chỉ lấy PHẦN TÍNH TOÁN: mỗi mốc gửi tối đa một lần mỗi kỳ (ghi vào
``canh_bao_da_gui``), nhiều mốc cùng đạt thì chỉ lấy mốc cao nhất.

KHÔNG port: luồng gửi tin qua kênh chat của c2a (``services.agent.reminders._send``,
``channel_of``) và thread vòng lặp 30 phút — web2api module không tự mở thread gửi tin;
cảnh báo được tính theo yêu cầu (công cụ ``xem_canh_bao``) và phần gửi thực tế thuộc
cơ chế thông báo của lõi/kênh đã liên kết, không nằm trong phạm vi NV160.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from . import kho, ngan_sach as ns

MA_TONG, MA_TONG_VUOT = "__tong__", "__tong_vuot__"


def _moc_cao_nhat(so_id: int, thang: str, khoa: str, ty_le: float, nguong: list[float],
                  soan: Callable[[float], str], gui: Callable[[int, str], int]) -> dict[str, Any] | None:
    for m in sorted(nguong, reverse=True):
        if ty_le < m:
            continue
        if kho.da_gui(so_id, thang, khoa, m):
            return None
        if gui(so_id, soan(m)) > 0:
            kho.danh_dau_da_gui(so_id, thang, khoa, m)
            return {"khoa": khoa, "nguong": m}
        return None
    return None


def kiem_so(so_id: int, gui: Callable[[int, str], int]) -> list[dict[str, Any]]:
    """Kiểm tra mọi mốc của sổ ``so_id``; ``gui(so_id, tin)`` trả số nơi đã nhận tin (>0 mới ghi mốc)."""
    s = kho.so(so_id)
    if s is None:
        return []
    n = ns.tinh(so_id)
    thang, nguong = n["thang"], list(s["nguong"] or [0.65, 0.8, 1.0])
    ra = []
    for h in n["hu"]:
        hm, chi = h["han_muc_truoc_bu"], h["da_chi"]
        if hm <= 0 and chi == 0:
            continue
        x = _moc_cao_nhat(so_id, thang, f"hu:{h['id']}", chi / hm if hm > 0 else 1.0, nguong,
                          lambda m, h=h: (f"Vượt hạn mức hũ «{h['ten']}»: đã chi {h['da_chi']:,} / "
                                          f"{h['han_muc_truoc_bu']:,} đ kỳ này."
                                          + (f" Đã tự bù {h['duoc_bu']:,} đ từ hũ khác." if h["duoc_bu"] else "")
                                          if m >= 1.0 else
                                          f"Hũ «{h['ten']}» đã dùng {m * 100:.0f}% hạn mức "
                                          f"({h['da_chi']:,} / {h['han_muc_truoc_bu']:,} đ)."), gui)
        if x:
            ra.append(x)
    hom_nay = date.today()
    ngay_bd = ns.ngay_bat_dau(s)
    so_ngay = ns.so_ngay_con_lai(hom_nay, ngay_bd)
    ky_sau = ns.ngay_bat_dau_ky_sau(hom_nay, ngay_bd).strftime("%d/%m")
    con = n["tong_con_lai"]

    def soan_tong(m: float) -> str:
        if con < 0:
            return f"ĐÃ CHI VƯỢT TỔNG NGÂN SÁCH KỲ {-con:,} đ! Đừng chi thêm tới kỳ lương {ky_sau}."
        if m >= 1.0:
            return f"Đã dùng hết tổng ngân sách kỳ. Đừng chi thêm tới kỳ lương {ky_sau}."
        return (f"Đã dùng {n['ty_le_tong_da_dung'] * 100:.0f}% tổng ngân sách kỳ ({n['tong_da_chi']:,} / "
                f"{n['tong_ngan_sach']:,} đ). Còn {con:,} đ cho {so_ngay} ngày tới {ky_sau} "
                f"(~{con // so_ngay:,} đ/ngày).")

    x = _moc_cao_nhat(so_id, thang, MA_TONG_VUOT if con < 0 else MA_TONG, n["ty_le_tong_da_dung"], nguong,
                      soan_tong, gui)
    if x:
        ra.append(x)
    return ra


def canh_bao_hien_tai(so_id: int) -> list[str]:
    """Cảnh báo ĐANG ÁP DỤNG của sổ (tính tức thời, không ghi mốc) — dùng cho công cụ chỉ-đọc."""
    s = kho.so(so_id)
    if s is None:
        return []
    n = ns.tinh(so_id)
    nguong = list(s["nguong"] or [0.65, 0.8, 1.0])
    ra: list[str] = []
    for h in n["hu"]:
        hm, chi = h["han_muc_truoc_bu"], h["da_chi"]
        if hm <= 0 and chi == 0:
            continue
        ty_le = chi / hm if hm > 0 else 1.0
        dat = [m for m in nguong if ty_le >= m]
        if dat:
            ra.append(f"Hũ «{h['ten']}» đã dùng {ty_le * 100:.0f}% hạn mức ({chi:,} / {hm:,} đ kỳ này)."
                      + (f" Đã tự bù {h['duoc_bu']:,} đ từ hũ khác." if h["duoc_bu"] else ""))
    if n["ty_le_tong_da_dung"] >= max(nguong) if nguong else False:
        ra.append(f"Đã dùng {n['ty_le_tong_da_dung'] * 100:.0f}% tổng ngân sách kỳ "
                  f"({n['tong_da_chi']:,} / {n['tong_ngan_sach']:,} đ).")
    elif n["tong_con_lai"] < 0:
        ra.append(f"ĐÃ CHI VƯỢT TỔNG NGÂN SÁCH KỲ {-n['tong_con_lai']:,} đ.")
    return ra
