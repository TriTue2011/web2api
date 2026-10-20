"""Nghiệp vụ chi tiêu — dùng chung cho công cụ C2 (`thuc_hien`) và cảnh báo.

Port từ ``~/workspace/c2a-nv146/services/chi_tieu/nghiep_vu.py`` @ d879b03 (bản gốc
``/opt/claude-c2a/chatgpt2api/services/chi_tieu/nghiep_vu.py`` — chuyển từ ``chi-tieu-mcp/app/tools.py``
(Quiz99, MIT)), chỉ đổi import theo khuôn module web2api và giờ nhà ở ``thoi_gian``.

KHÔNG port: ``so_cua_chat`` / ``tao_ma_lien_ket`` / ``lien_ket_bang_ma`` (nối kênh chat
Zalo/Telegram của c2a — web2api cách ly sổ theo danh tính đăng nhập đọc từ ``app.ngu_canh``,
không có cơ chế mã liên kết 6 số; xem README.md mục «Khác biệt với c2a»).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date
from typing import Any

from . import kho, ngan_sach as ns, thoi_gian as _tg

TOI_DA_TIEN = 10**12
NGUONG_SAP_HET_TONG = 0.8


class LoiChiTieu(ValueError):
    """Lỗi người dùng (đầu vào sai, không có hũ…) — công cụ trả mã lỗi, không sập tiến trình."""


def _tien(x: Any, ten: str = "so_tien") -> int:
    """Số tiền VNĐ: số nguyên (đơn vị nhỏ nhất), số thực tròn (model hay gửi 50000.0) hoặc chuỗi toàn chữ số;
    dương, ≤ 1.000 tỷ. KHÔNG dùng float cho tiền."""
    if isinstance(x, bool):
        raise LoiChiTieu(f"{ten} phải là số tiền")
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    elif isinstance(x, str) and x.strip().isdigit():
        x = int(x.strip())
    if not isinstance(x, int):
        raise LoiChiTieu(f"{ten} phải là số tiền (số nguyên VNĐ)")
    if x <= 0 or x > TOI_DA_TIEN:
        raise LoiChiTieu(f"{ten} phải dương, tối đa 1.000 tỷ")
    return x


def _chu(x: Any, ten: str, toi_da: int = 500, bat_buoc: bool = True) -> str:
    if not isinstance(x, str):
        if x is None and not bat_buoc:
            return ""
        raise LoiChiTieu(f"{ten} phải là chữ")
    x = x.strip()
    if bat_buoc and not x:
        raise LoiChiTieu(f"{ten} không được để trống")
    if len(x) > toi_da:
        raise LoiChiTieu(f"{ten} quá dài (tối đa {toi_da} ký tự)")
    return x


def _khong_dau(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower()).replace("đ", "d")
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()


def tim_hu(so_id: int, hu: Any) -> dict[str, Any]:
    """Hũ theo id hoặc TÊN (không dấu, không phân biệt hoa thường, khớp trọn hoặc khớp đầu tên duy nhất)."""
    hus = kho.ds_hu(so_id)
    if isinstance(hu, int) or (isinstance(hu, str) and hu.strip().isdigit()):
        for h in hus:
            if h["id"] == int(hu):
                return h
    ten = _khong_dau(str(hu or ""))
    if ten:
        trung = [h for h in hus if _khong_dau(h["ten"]) == ten]
        if not trung:
            trung = [h for h in hus if _khong_dau(h["ten"]).startswith(ten) or ten in _khong_dau(h["ten"])]
        if len(trung) == 1:
            return trung[0]
    raise LoiChiTieu(f"Không có hũ «{hu}». Các hũ: " + "; ".join(f"{h['id']} = {h['ten']}" for h in hus))


# ── Sổ theo người ───────────────────────────────────────────────────────────
def so_cua_tai_khoan(identity: dict[str, Any], tao: bool = True) -> dict[str, Any] | None:
    """Sổ của tài khoản đang đăng nhập — tạo khi mở lần đầu. Quy tắc của chủ máy:
    "mỗi account chỉ xem được chi tiêu của họ, kể cả admin cũng thế" — không có đường nào lấy sổ
    của người khác theo tham số: mọi hàm nghiệp vụ đều nhận ``so_id`` đã ràng buộc theo danh tính."""
    chu = str(identity.get("id") or "")
    if not chu:
        raise LoiChiTieu("Không rõ tài khoản đăng nhập.")
    s = kho.so_cua_chu(chu)
    if s is None and tao:
        try:
            s = kho.tao_so(f"Sổ của {identity.get('name') or chu}", chu)
        except sqlite3.IntegrityError:
            s = kho.so_cua_chu(chu)       # hai yêu cầu đầu tiên cùng tạo — chỉ mục duy nhất `so_chu` giữ một sổ
    return s


# ── Ghi / xem ───────────────────────────────────────────────────────────────
def _thong_tin_ky(s: dict[str, Any], tong_con_lai: int) -> dict[str, Any]:
    hom_nay = _tg.bay_gio_dt().date()
    ngay_bd = ns.ngay_bat_dau(s)
    so_ngay = ns.so_ngay_con_lai(hom_nay, ngay_bd)
    return {"so_ngay_con_lai": so_ngay, "ngay_bat_dau_ky_sau": ns.ngay_bat_dau_ky_sau(hom_nay, ngay_bd).isoformat(),
            "trung_binh_moi_ngay_con_lai": tong_con_lai // so_ngay if tong_con_lai > 0 else 0}


def _canh_bao_tong(n: dict[str, Any], ky: dict[str, Any]) -> str | None:
    ky_sau = date.fromisoformat(ky["ngay_bat_dau_ky_sau"]).strftime("%d/%m")
    con = n["tong_con_lai"]
    if con < 0:
        return (f"ĐÃ CHI VƯỢT TỔNG NGÂN SÁCH KỲ {-con:,} đ! Các hũ đã bù hết. "
                f"Đừng chi thêm cho tới kỳ lương {ky_sau}.")
    if n["ty_le_tong_da_dung"] >= NGUONG_SAP_HET_TONG:
        return (f"Đã dùng {n['ty_le_tong_da_dung'] * 100:.0f}% tổng ngân sách kỳ. Còn {con:,} đ cho "
                f"{ky['so_ngay_con_lai']} ngày tới kỳ lương {ky_sau} (~{ky['trung_binh_moi_ngay_con_lai']:,} đ/ngày).")
    return None


def _ty_le_hieu_luc(h: dict[str, Any]) -> float:
    if h["han_muc_hieu_luc"]:
        return round(h["da_chi"] / h["han_muc_hieu_luc"] * 100, 1)
    return 100.0 if (h["da_chi"] > 0 or h["da_nhuong"] > 0) else 0.0


def _can_xac_nhan(s: dict[str, Any], n: dict[str, Any], them: int) -> dict[str, Any] | None:
    """Cổng «vượt tổng»: khoản THÊM vào làm vượt tổng ngân sách kỳ thì phải hỏi lại người dùng."""
    if them <= n["tong_con_lai"]:
        return None
    vuot = them - n["tong_con_lai"]
    return {"da_ghi": False, "can_xac_nhan": True, "tong_con_lai": n["tong_con_lai"], "vuot_tong_neu_ghi": vuot,
            **_thong_tin_ky(s, n["tong_con_lai"]),
            "canh_bao": (f"Khoản này sẽ làm VƯỢT tổng ngân sách kỳ {vuot:,} đ "
                         f"(cả kỳ chỉ còn {max(n['tong_con_lai'], 0):,} đ)."),
            "huong_dan": ("CHƯA GHI. Đọc canh_bao cho người dùng, hỏi có chắc đã chi không. Chỉ gọi lại với "
                          "xac_nhan_vuot_tong=true khi người dùng đồng ý RÕ RÀNG ở tin nhắn sau.")}


def ghi_chi(so_id: int, hu: Any, so_tien: Any, ghi_chu: str = "", xac_nhan_vuot_tong: bool = False,
            nguon: str = "cong_cu") -> dict[str, Any]:
    s = kho.so(so_id)
    h = tim_hu(so_id, hu)
    tien = _tien(so_tien)
    ghi_chu = _chu(ghi_chu, "ghi_chu", bat_buoc=False)
    thang = ns.ky_hien_tai(s)  # type: ignore[arg-type]
    truoc = ns.tinh(so_id, thang)
    if not xac_nhan_vuot_tong:
        hoi = _can_xac_nhan(s, truoc, tien)  # type: ignore[arg-type]
        if hoi:
            return {**hoi, "hu": h["ten"], "so_tien_de_nghi_ghi": tien}
    id_ = kho.ghi_chi(so_id, h["id"], tien, ghi_chu, nguon, thang)
    sau = ns.tinh(so_id, thang)
    x = next(v for v in sau["hu"] if v["id"] == h["id"])
    ky = _thong_tin_ky(s, sau["tong_con_lai"])  # type: ignore[arg-type]
    dong = []
    if x["duoc_bu"] > 0:
        dong.append(f"Hũ {x['ten']} vượt hạn mức {x['han_muc_truoc_bu']:,} đ; đang được bù {x['duoc_bu']:,} đ từ: "
                    + ", ".join(f"{b['ten']} {b['so_tien']:,}" for b in x["bu_tu"]) + ".")
    cb = _canh_bao_tong(sau, ky)
    if cb:
        dong.append(cb)
    rieng = x["han_muc_truoc_bu"]
    return {"da_ghi": True, "id": id_, "hu": x["ten"], "so_tien_vua_ghi": tien, "da_chi_ky_nay": x["da_chi"],
            "han_muc_ky": x["han_muc_hieu_luc"], "con_lai": x["con_lai"], "ty_le_da_dung_phan_tram": _ty_le_hieu_luc(x),
            "trang_thai": ("vuot_han_muc" if x["da_chi"] >= rieng else
                           "canh_bao_80" if x["da_chi"] >= rieng * 0.8 else "binh_thuong"),
            "duoc_bu": x["duoc_bu"], "bu_tu": x["bu_tu"], "tong_con_lai": sau["tong_con_lai"], **ky,
            "canh_bao": " ".join(dong) or None}


def _canh_bao_dac_biet(n: dict[str, Any]) -> str | None:
    if n["con_thieu_dac_biet"] <= 0:
        return None
    hung = " + ".join(n["hu_hung_dac_biet"]) or "hũ hứng chi phí đặc biệt"
    dau = (f"Chi phí đặc biệt kỳ này ({n['tong_chi_phi_dac_biet']:,} đ) vượt cả {hung}; "
           f"phần thiếu {n['con_thieu_dac_biet']:,} đ")
    if n["thieu_dac_biet_chua_bu"] > 0:
        return f"{dau} tự bù từ các hũ khác nhưng không đủ — còn {n['thieu_dac_biet_chua_bu']:,} đ chưa có chỗ bù."
    return f"{dau} đã tự bù từ các hũ khác theo thứ tự."


def xem_ngan_sach(so_id: int, thang: str = "") -> dict[str, Any]:
    s = kho.so(so_id)
    n = ns.tinh(so_id, thang or None)
    ky = _thong_tin_ky(s, n["tong_con_lai"])  # type: ignore[arg-type]
    return {**n, "hu": [{**h, "ty_le_da_dung_phan_tram": _ty_le_hieu_luc(h)} for h in n["hu"]],
            "chi_phi_dac_biet": kho.ds_ky("chi_phi_dac_biet", so_id, n["thang"]),
            "thu_nhap_them": kho.ds_ky("thu_nhap_them", so_id, n["thang"]),
            "canh_bao_chi_phi_dac_biet": _canh_bao_dac_biet(n), "ngay_bat_dau": ns.ngay_bat_dau(s),  # type: ignore[arg-type]
            "ten_so": s["ten"], "ty_le_tong_da_dung_phan_tram": round(n["ty_le_tong_da_dung"] * 100, 1),  # type: ignore[index]
            **ky, "canh_bao_tong": _canh_bao_tong(n, ky)}


def _kiem_thang(thang: str, s: dict[str, Any]) -> str:
    t = (thang or "").strip() or ns.ky_hien_tai(s)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", t):
        raise LoiChiTieu("Kỳ phải dạng YYYY-MM")
    return t


def xem_lich_su(so_id: int, thang: str = "") -> dict[str, Any]:
    s = kho.so(so_id)
    t = _kiem_thang(thang, s)  # type: ignore[arg-type]
    ten = {h["id"]: h["ten"] for h in kho.ds_hu(so_id, ca_an=True)}
    gd = [{**r, "hu_ten": ten.get(r["hu_id"], "?")} for r in kho.ds_chi(so_id, t)]
    return {"thang": t, "giao_dich": gd, "so_luong": len(gd)}


def thong_ke(so_id: int, so_ky: int = 6) -> dict[str, Any]:
    """Tổng chi theo kỳ và theo ngày (port từ api/chi_tieu.py::thong_ke @ d879b03)."""
    s = kho.so(so_id)
    so_ky = max(1, min(int(so_ky or 6), 12))
    hien = ns.ky_hien_tai(s)  # type: ignore[arg-type]
    ky = []
    for lui in range(so_ky - 1, -1, -1):
        t = ns.lui_ky(hien, lui)
        theo = kho.tong_chi_theo_hu(so_id, t)
        ky.append({"thang": t, "tong_chi": sum(theo.values()), "theo_hu": {str(k): v for k, v in theo.items()}})
    ngay: dict[str, int] = {}
    for x in kho.ds_chi(so_id, hien):
        ngay[x["thoi_gian"][:10]] = ngay.get(x["thoi_gian"][:10], 0) + int(x["so_tien"])
    return {"ky": ky, "theo_ngay": [{"ngay": k, "tong": v} for k, v in sorted(ngay.items())]}


def sua_chi(so_id: int, id_: int, *, hu: Any = None, so_tien: Any = None, ghi_chu: Any = None,
            xac_nhan_vuot_tong: bool = False) -> dict[str, Any]:
    goc = kho.chi_theo_id(so_id, int(id_))
    if goc is None:
        raise LoiChiTieu("Không có khoản chi này.")
    truong: dict[str, Any] = {}
    if hu is not None:
        truong["hu_id"] = tim_hu(so_id, hu)["id"]
    if so_tien is not None:
        truong["so_tien"] = _tien(so_tien)
    if ghi_chu is not None:
        truong["ghi_chu"] = _chu(ghi_chu, "ghi_chu", bat_buoc=False)
    if not truong:
        raise LoiChiTieu("Không có gì để sửa.")
    tang = truong.get("so_tien", goc["so_tien"]) - goc["so_tien"]
    if tang > 0 and not xac_nhan_vuot_tong:
        # Sửa lỗi bản gốc: nút sửa trên web đổi số tiền mà không qua cổng «vượt tổng» như nút thêm.
        hoi = _can_xac_nhan(kho.so(so_id), ns.tinh(so_id, goc["thang"]), tang)  # type: ignore[arg-type]
        if hoi:
            return hoi
    kho.sua_chi(so_id, int(id_), **truong)
    return {"da_sua": True, "id": int(id_)}


def xoa_chi(so_id: int, id_: int) -> dict[str, Any]:
    if not kho.xoa_chi(so_id, int(id_)):
        raise LoiChiTieu("Không có khoản chi này.")
    return {"da_xoa": True, "id": int(id_)}


def tach_chi(so_id: int, id_: int, danh_sach: list[dict[str, Any]]) -> dict[str, Any]:
    goc = kho.chi_theo_id(so_id, int(id_))
    if goc is None:
        raise LoiChiTieu(f"Không có khoản chi id {id_}")
    if not isinstance(danh_sach, list) or len(danh_sach) < 2:
        raise LoiChiTieu("Cần ít nhất 2 mục để tách")
    muc = []
    for m in danh_sach:
        if not isinstance(m, dict):
            raise LoiChiTieu("Mỗi mục là {hu, so_tien, ghi_chu}")
        muc.append({"hu_id": tim_hu(so_id, m.get("hu", m.get("hu_id")))["id"], "so_tien": _tien(m.get("so_tien")),
                    "ghi_chu": _chu(m.get("ghi_chu", ""), "ghi_chu", bat_buoc=False)})
    tong = sum(m["so_tien"] for m in muc)
    if tong != goc["so_tien"]:
        raise LoiChiTieu(f"Tổng các mục ({tong:,} đ) phải bằng số tiền gốc ({goc['so_tien']:,} đ)")
    return {"da_tach": True, "id_goc": int(id_), "id_moi": kho.tach_chi(so_id, int(id_), muc)}


def de_xuat(so_id: int) -> dict[str, Any]:
    n = xem_ngan_sach(so_id)
    tl = ns.ty_le_thuc_te_3_ky(so_id, n["thang"])
    loi: list[str] = []
    vuot = [h for h in n["hu"] if h["han_muc_truoc_bu"] and h["da_chi"] >= h["han_muc_truoc_bu"] * 0.8]
    if vuot:
        loi.append("Hũ đang sát/vượt hạn mức kỳ này: " + ", ".join(h["ten"] for h in vuot) + ".")
    bu = [h for h in n["hu"] if h["duoc_bu"] > 0]
    if bu:
        loi.append("Đã tự bù phần vượt của " + ", ".join(h["ten"] for h in bu)
                   + f" từ các hũ còn dư. Tổng còn chi được: {n['tong_con_lai']:,} đ.")
    xu = []
    for h in n["hu"]:
        thuc = tl.get(h["id"])
        if thuc is not None and abs(thuc - h["ty_le"]) >= 5:
            xu.append(f"Hũ «{h['ten']}»: 3 kỳ gần đây chi trung bình {thuc:.1f}% thu nhập, đang đặt {h['ty_le']:.1f}% — "
                      f"cân nhắc {'giảm' if thuc < h['ty_le'] else 'tăng'} khoảng {abs(thuc - h['ty_le']):.0f} điểm %.")
    if xu:
        loi += ["Theo chi thực tế 3 kỳ gần đây:", *xu]
    return {"thang": n["thang"], "de_xuat": loi or ["Chi tiêu kỳ này trong tầm kiểm soát."],
            "phan_bo_de_xuat": ns.de_xuat_phan_bo(so_id, n["thang"])}


# ── Thu nhập thêm, chi phí đặc biệt ─────────────────────────────────────────
def ghi_ky(bang: str, so_id: int, mo_ta: Any, so_tien: Any) -> dict[str, Any]:
    s = kho.so(so_id)
    thang = ns.ky_hien_tai(s)  # type: ignore[arg-type]
    id_ = kho.ghi_ky(bang, so_id, _chu(mo_ta, "mo_ta"), _tien(so_tien), thang)
    n = xem_ngan_sach(so_id, thang)
    return {"da_ghi": True, "id": id_, "thang": thang, "tong_con_lai": n["tong_con_lai"],
            "canh_bao": n["canh_bao_chi_phi_dac_biet"] if bang == "chi_phi_dac_biet" else None,
            "han_muc_moi": [{"ten": h["ten"], "han_muc_ky": h["han_muc_hieu_luc"]} for h in n["hu"]]}


def sua_ky(bang: str, so_id: int, id_: int, mo_ta: Any = None, so_tien: Any = None) -> dict[str, Any]:
    truong: dict[str, Any] = {}
    if mo_ta is not None:
        truong["mo_ta"] = _chu(mo_ta, "mo_ta")
    if so_tien is not None:
        truong["so_tien"] = _tien(so_tien)
    if not truong or not kho.sua_ky(bang, so_id, int(id_), **truong):
        raise LoiChiTieu("Không có mục này hoặc không có gì để sửa.")
    return {"da_sua": True}


def xoa_ky(bang: str, so_id: int, id_: int) -> dict[str, Any]:
    if not kho.xoa_ky(bang, so_id, int(id_)):
        raise LoiChiTieu("Không có mục này.")
    return {"da_xoa": True}


# ── Hũ & cấu hình ───────────────────────────────────────────────────────────
def luu_cau_hinh(so_id: int, *, ten: Any = None, luong: Any = None, ngay_bat_dau: Any = None,
                 nguong: Any = None) -> dict[str, Any]:
    truong: dict[str, Any] = {}
    if ten is not None:
        truong["ten"] = _chu(ten, "tên sổ", 80)
    if luong is not None:
        truong["luong"] = _tien(luong, "lương")
    if ngay_bat_dau is not None:
        if isinstance(ngay_bat_dau, bool) or not isinstance(ngay_bat_dau, int) or not 1 <= ngay_bat_dau <= 28:
            raise LoiChiTieu("Ngày bắt đầu kỳ là số 1–28")
        truong["ngay_bat_dau"] = ngay_bat_dau
    if nguong is not None:
        if not isinstance(nguong, list) or not nguong or not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x <= 2 for x in nguong):
            raise LoiChiTieu("Ngưỡng cảnh báo là danh sách tỷ lệ 0–2, vd [0.65, 0.8, 1]")
        truong["nguong"] = sorted({round(float(x), 3) for x in nguong})
    kho.sua_so(so_id, **truong)
    return {"da_luu": True}


def _kiem_hu(truong: dict[str, Any]) -> dict[str, Any]:
    ra: dict[str, Any] = {}
    if "ten" in truong:
        ra["ten"] = _chu(truong["ten"], "tên hũ", 60)
    if "ty_le" in truong:
        t = truong["ty_le"]
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 100:
            raise LoiChiTieu("Tỷ lệ hũ là số 0–100")
        ra["ty_le"] = round(float(t), 2)
    for k in ("thu_tu", "thu_tu_bu", "thu_tu_dac_biet"):
        if k in truong:
            v = truong[k]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 999:
                raise LoiChiTieu(f"{k} là số nguyên 0–999")
            ra[k] = v
    return ra


def them_hu(so_id: int, ten: Any, ty_le: Any = 0, thu_tu_bu: Any = 50, thu_tu_dac_biet: Any = 0) -> dict[str, Any]:
    t = _kiem_hu({"ten": ten, "ty_le": ty_le, "thu_tu_bu": thu_tu_bu, "thu_tu_dac_biet": thu_tu_dac_biet})
    if any(_khong_dau(h["ten"]) == _khong_dau(t["ten"]) for h in kho.ds_hu(so_id)):
        raise LoiChiTieu("Đã có hũ trùng tên.")
    return {"id": kho.them_hu(so_id, t["ten"], t["ty_le"], t["thu_tu_bu"], t["thu_tu_dac_biet"])}


def sua_hu(so_id: int, hu_id: int, **truong: Any) -> dict[str, Any]:
    if not any(h["id"] == int(hu_id) for h in kho.ds_hu(so_id)):
        raise LoiChiTieu("Không có hũ này.")
    t = _kiem_hu(truong)
    if "ten" in t and any(_khong_dau(h["ten"]) == _khong_dau(t["ten"]) and h["id"] != int(hu_id)
                          for h in kho.ds_hu(so_id)):
        raise LoiChiTieu("Đã có hũ trùng tên.")
    kho.sua_hu(so_id, int(hu_id), **t)
    return {"da_sua": True}


def xoa_hu(so_id: int, hu_id: int) -> dict[str, Any]:
    hus = kho.ds_hu(so_id)
    if not any(h["id"] == int(hu_id) for h in hus):
        raise LoiChiTieu("Không có hũ này.")
    if len(hus) <= 1:
        raise LoiChiTieu("Sổ phải còn ít nhất một hũ.")
    kho.xoa_hu(so_id, int(hu_id))
    return {"da_xoa": True}


def tong_ty_le(so_id: int) -> float:
    return round(sum(float(h["ty_le"]) for h in kho.ds_hu(so_id)), 2)


# ── Tạm ứng công ty ─────────────────────────────────────────────────────────
def ghi_cong_ty(so_id: int, loai: str, so_tien: Any, mo_ta: Any) -> dict[str, Any]:
    if loai not in ("tam_ung", "chi"):
        raise LoiChiTieu("loai là tam_ung hoặc chi")
    kho.ghi_cong_ty(so_id, loai, _tien(so_tien), _chu(mo_ta, "mo_ta"))
    return {"da_ghi": True, **xem_cong_ty(so_id)}


def xem_cong_ty(so_id: int) -> dict[str, Any]:
    gd = kho.ds_cong_ty(so_id)
    so_du = sum(r["so_tien"] if r["loai"] == "tam_ung" else -r["so_tien"] for r in gd)
    dien = ("Chưa có giao dịch tạm ứng/chi công ty nào trong kỳ." if not gd else
            f"Đang giữ {so_du:,} đ tiền công ty chưa chi hết." if so_du > 0 else
            f"Đã chi hộ công ty {-so_du:,} đ, công ty còn nợ lại." if so_du < 0 else "Số dư tạm ứng bằng 0.")
    return {"so_du": so_du, "dien_giai": dien, "giao_dich": gd, "giai_chi": kho.ds_giai_chi(so_id)}


def giai_chi(so_id: int) -> dict[str, Any]:
    kq = kho.giai_chi_ky(so_id)
    if kq is None:
        raise LoiChiTieu("Chưa có giao dịch nào để giải chi.")
    return {"da_giai_chi": True, **kq}
