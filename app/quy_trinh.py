"""Cơ chế quy trình nối tầng (phễu tìm kiếm và xử lý dữ liệu dùng chung).

Nguyên tắc:
- Lõi không chứa tên công nghệ cụ thể; chỉ biết các toán tử dữ liệu thuần và khả năng của module.
- Quy trình gồm danh sách tầng có thứ tự: [{ten, kha_nang, nguon?, tham_so?}].
- Tầng sau nhận kết quả tầng trước; đo từng tầng (số mục vào/ra, ms, lỗi).
- Tầng hỏng dừng ngay và nêu đúng tầng hỏng, báo cáo luôn trả về được.
- Trần số tầng: 8; trần thời gian tổng: WEB2API_QUY_TRINH_GIAY (mặc định 60s) -> 504.
- Không phải ngôn ngữ lập trình: không biểu thức, không eval, không vòng lặp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import unicodedata
from typing import Any

from app import ngu_canh
from app.goi_kha_nang import goi
from app.kham_pha import HOP_DONG_KHA_NANG
from app.kieu_chat import so_nguyen_chat
from app.loi_openai import LoiOpenAI
from app.so_module import SoModule

log = logging.getLogger("web2api.quy_trinh")

TRAN_SO_TANG = 8
MAC_DINH_THOI_GIAN_GIAY = 60.0

#: Các toán tử dữ liệu thuần của lõi (không gọi module)
TOAN_TU_THUAN = frozenset({"gop", "loc", "cat", "gop_ngu_canh", "cay", "muc_luc", "chi_muc"})

#: Tập khả năng của module được phép gọi từ phễu theo chuẩn tri thức (docs/CHUAN-MODULE.md §4, app/kham_pha.py)
KHA_NANG_PHEU_MODULE = frozenset({"tim_kiem", "xep_hang", "nhung"})

#: Mọi toán tử hợp lệ của phễu: toán tử thuần + khả năng module có trong HOP_DONG_KHA_NANG
TOAN_TU_PHEU_HOP_LE = TOAN_TU_THUAN | (KHA_NANG_PHEU_MODULE & HOP_DONG_KHA_NANG.keys())

#: Danh tính nội bộ của lõi cho các lời gọi nội bộ có chủ đích
DANH_TINH_NOI_BO = {"loai": "noi_bo", "id": "noi_bo", "pham_vi": ["*"]}


def kiem_tra_pham_vi_tang(danh_tinh: dict | None, kha_nang: str) -> None:
    """Kiểm tra phạm vi quyền hạn của người gọi cho từng tầng của quy trình.

    - Thiếu danh tính (None) -> 403 insufficient_scope (đóng khi lỗi, fail-closed).
    - Quản trị hoặc lời gọi nội bộ -> cho phép.
    - Khách: kiểm theo khop_pham_vi (khoá '*' không cho phép khả năng nguy hiểm).
    - Toán tử thuần: cần phạm vi 'tim_kiem'.
    - Tầng module: cần đúng phạm vi của chính khả năng đó ('tim_kiem', 'xep_hang', 'nhung'...).
    - Khả năng lạ ngoài phễu: kiểm phạm vi của chính khả năng đó (thiếu -> 403; có -> sẽ bị từ chối 400 ở tầng hợp đồng).
    """
    if danh_tinh is None or not isinstance(danh_tinh, dict):
        raise LoiOpenAI(
            403,
            "Thiếu danh tính phạm vi khi thực thi quy trình.",
            loai="insufficient_scope",
            code="insufficient_scope",
        )
    if danh_tinh.get("loai") in ("quan_tri", "noi_bo"):
        return
    pham_vi = danh_tinh.get("pham_vi", [])

    # Xác định phạm vi cần thiết cho khả năng này
    if kha_nang in TOAN_TU_THUAN:
        can = "tim_kiem"
    else:
        can = kha_nang

    from app.xac_thuc import khop_pham_vi
    for pv in pham_vi:
        if khop_pham_vi(pv, can):
            return

    raise LoiOpenAI(
        403,
        f"Khoá không có quyền thực hiện thao tác này (cần phạm vi «{can}»).",
        loai="insufficient_scope",
        code="insufficient_scope",
    )


class LoiQuyTrinh(LoiOpenAI):
    """Lỗi xảy ra trong quá trình chạy một tầng của quy trình."""

    def __init__(
        self,
        ma: int,
        thong_diep: str,
        bao_cao: list[dict],
        tang_loi: str,
        loai: str | None = None,
        code=None,
    ):
        super().__init__(ma, thong_diep, loai=loai, code=code or "step_failed")
        self.bao_cao = bao_cao
        self.tang_loi = tang_loi
        self.than["error"]["tang_loi"] = tang_loi
        self.than["error"]["bao_cao"] = bao_cao
        self.than["bao_cao"] = bao_cao


# ───────────── Các toán tử dữ liệu thuần của lõi ─────────────


def toan_tu_gop(du_lieu_vao: dict, k: int = 60) -> dict:
    """Hợp nhất danh sách kết quả theo giải thuật Reciprocal Rank Fusion (RRF).

    Công thức: điểm = Σ 1 / (k + hạng), với hạng tính từ 1.
    """
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}

    danh_sach_nguon: list[list[dict]] = []

    # 1. Đầu vào từ danh_sach (danh sách các danh sách)
    if isinstance(du_lieu_vao.get("danh_sach"), list):
        for sub in du_lieu_vao["danh_sach"]:
            if isinstance(sub, list):
                danh_sach_nguon.append([x for x in sub if isinstance(x, dict)])

    # 2. Hoặc trường muc là danh sách các danh sách
    elif isinstance(du_lieu_vao.get("muc"), list) and du_lieu_vao["muc"] and isinstance(du_lieu_vao["muc"][0], list):
        for sub in du_lieu_vao["muc"]:
            if isinstance(sub, list):
                danh_sach_nguon.append([x for x in sub if isinstance(x, dict)])

    # 3. Hoặc trường muc là danh sách phẳng chứa các mục đã có trường nguon
    elif isinstance(du_lieu_vao.get("muc"), list):
        nhom_theo_nguon: dict[str, list[dict]] = {}
        for item in du_lieu_vao["muc"]:
            if not isinstance(item, dict):
                continue
            nguon_id = str(item.get("nguon") or "mac_dinh")
            nhom_theo_nguon.setdefault(nguon_id, []).append(item)
        danh_sach_nguon = list(nhom_theo_nguon.values())

    if not danh_sach_nguon:
        muc_cu = du_lieu_vao.get("muc", [])
        return {**du_lieu_vao, "muc": muc_cu if isinstance(muc_cu, list) else []}

    # Tính điểm RRF
    bang_diem: dict[str, float] = {}
    ban_ghi: dict[str, dict] = {}

    for ds in danh_sach_nguon:
        for hang, item in enumerate(ds, start=1):
            m_id = str(item.get("id") or f"id_{id(item)}")
            diem_them = 1.0 / (float(k) + float(hang))
            bang_diem[m_id] = bang_diem.get(m_id, 0.0) + diem_them
            if m_id not in ban_ghi:
                ban_ghi[m_id] = dict(item)

    ket_qua_muc: list[dict] = []
    for m_id, tong_diem in sorted(bang_diem.items(), key=lambda x: x[1], reverse=True):
        muc = dict(ban_ghi[m_id])
        muc["diem"] = round(tong_diem, 8)
        muc["nguon_ngoai"] = True
        ket_qua_muc.append(muc)

    return {**du_lieu_vao, "muc": ket_qua_muc}


_KHONG_CO = object()


def _tra_cuu_duong_dan(goc: Any, cac_khoa: list[str]) -> Any:
    hien_tai = goc
    for k in cac_khoa:
        if isinstance(hien_tai, dict) and k in hien_tai:
            hien_tai = hien_tai[k]
        else:
            return _KHONG_CO
    return hien_tai


def _lay_gia_tri_truong(item: dict, duong_dan: str) -> Any:
    """Lấy giá trị trường, hỗ trợ trường lồng bằng đường chấm ('truong.kenh' hoặc 'kenh.ten')."""
    if not isinstance(item, dict) or not duong_dan:
        return _KHONG_CO

    parts = [p for p in str(duong_dan).strip().split(".") if p]
    if not parts:
        return _KHONG_CO

    # 1. Nếu đường dẫn bắt đầu bằng "truong." và item có "truong"
    if parts[0] == "truong" and len(parts) > 1 and isinstance(item.get("truong"), dict):
        val = _tra_cuu_duong_dan(item["truong"], parts[1:])
        if val is not _KHONG_CO:
            return val

    # 2. Tìm trong item["truong"] trước
    if isinstance(item.get("truong"), dict):
        val = _tra_cuu_duong_dan(item["truong"], parts)
        if val is not _KHONG_CO:
            return val
        if duong_dan in item["truong"]:
            return item["truong"][duong_dan]

    # 3. Tìm từ cấp gốc của item
    val = _tra_cuu_duong_dan(item, parts)
    if val is not _KHONG_CO:
        return val
    if duong_dan in item:
        return item[duong_dan]

    return _KHONG_CO


def _khop_dieu_kien(gia_tri_that: Any, v: Any) -> bool:
    """Kiểm tra giá trị thật có khớp điều kiện lọc hay không.

    Hỗ trợ: giá trị đơn (bằng), danh sách (thuộc một trong), khoảng {tu, den}, loại trừ {khac: ...}.
    Kiểu không khớp -> trả về False (không lỗi).
    """
    if gia_tri_that is _KHONG_CO:
        return False

    if isinstance(v, dict):
        # 1. Loại trừ: {khac: ...}
        if "khac" in v:
            v_khac = v["khac"]
            if isinstance(v_khac, list):
                try:
                    if gia_tri_that in v_khac:
                        return False
                except (TypeError, ValueError):
                    pass
            else:
                if gia_tri_that == v_khac:
                    return False

        # 2. Khoảng: {tu, den} cho số hoặc chuỗi ISO ngày giờ
        if "tu" in v or "den" in v:
            if gia_tri_that is None:
                return False
            tu = v.get("tu")
            den = v.get("den")
            if tu is not None:
                try:
                    if not (gia_tri_that >= tu):
                        return False
                except (TypeError, ValueError):
                    return False
            if den is not None:
                try:
                    if not (gia_tri_that <= den):
                        return False
                except (TypeError, ValueError):
                    return False

        # 3. Từ điển so sánh bằng nếu không phải khac và khoảng
        if "khac" not in v and "tu" not in v and "den" not in v:
            if gia_tri_that != v:
                return False

        return True

    # Danh sách: thuộc một trong các phần tử
    if isinstance(v, list):
        try:
            return gia_tri_that in v
        except (TypeError, ValueError):
            return False

    # Giá trị đơn
    return gia_tri_that == v


def toan_tu_loc(du_lieu_vao: dict, dieu_kien: dict) -> dict:
    """Lọc các mục theo từ điển trường=giá trị trên trường 'truong' hoặc cấp ngoài."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(dieu_kien, dict) or not dieu_kien:
        return du_lieu_vao

    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        khop = True
        for k, v in dieu_kien.items():
            gia_tri_that = _lay_gia_tri_truong(item, k)
            if not _khop_dieu_kien(gia_tri_that, v):
                khop = False
                break
        if khop:
            ket_qua.append(item)
    return {**du_lieu_vao, "muc": ket_qua}


def toan_tu_cat(du_lieu_vao: dict, so_luong: int) -> dict:
    """Lấy top N mục đầu tiên từ danh sách kết quả, lưu muc_truoc_cat để đếm mặt."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    n = max(0, int(so_luong))
    return {**du_lieu_vao, "muc_truoc_cat": muc_in, "muc": muc_in[:n]}


def toan_tu_gop_ngu_canh(du_lieu_vao: dict, tran_ky_tu: int) -> dict:
    """Cắt danh sách sao cho tổng độ dài các đoạn text (trường 'doan') <= trần ký tự."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}

    tran = max(0, int(tran_ky_tu))
    tong_do_dai = 0
    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        doan = str(item.get("doan") or "")
        do_dai = len(doan)
        if tong_do_dai + do_dai <= tran:
            ket_qua.append(item)
            tong_do_dai += do_dai
        else:
            break
    return {**du_lieu_vao, "muc": ket_qua}


def dem_cay_nut(muc_ds: list[dict], cay_cfg: dict) -> list[dict]:
    """Đếm số mục các nút con trực tiếp dưới gốc phục vụ duyệt cây trên giao diện."""
    if not isinstance(cay_cfg, dict):
        return []
    truong_ten = str(cay_cfg.get("truong") or "duong_dan")
    goc_tho = str(cay_cfg.get("goc") or "").strip()
    goc = goc_tho.strip("/")

    cac_nut: dict[str, int] = {}
    for item in muc_ds:
        if not isinstance(item, dict):
            continue
        val = _lay_gia_tri_truong(item, truong_ten)
        if val is _KHONG_CO or val is None:
            continue
        path = str(val).strip().strip("/")
        if not path:
            continue

        if goc:
            if path.startswith(f"{goc}/"):
                con = path[len(goc) + 1:].strip("/")
                if con:
                    nhanh_truc_tiep = con.split("/")[0]
                    duong_nut = f"{goc}/{nhanh_truc_tiep}"
                    cac_nut[duong_nut] = cac_nut.get(duong_nut, 0) + 1
        else:
            nhanh_truc_tiep = path.split("/")[0]
            cac_nut[nhanh_truc_tiep] = cac_nut.get(nhanh_truc_tiep, 0) + 1

    return [{"duong": k, "so_muc": v} for k, v in sorted(cac_nut.items())]


def dem_mat(muc_ds: list[dict], danh_sach_mat: list[str]) -> dict[str, dict[str, int]]:
    """Đếm số mục theo từng giá trị của mỗi mặt (facet) trước khi cắt số lượng."""
    if not isinstance(danh_sach_mat, list):
        return {}
    ket_qua: dict[str, dict[str, int]] = {}
    for mat_ten in danh_sach_mat:
        if not isinstance(mat_ten, str) or not mat_ten.strip():
            continue
        ten_mat = mat_ten.strip()
        dem_gia_tri: dict[str, int] = {}
        for item in muc_ds:
            if not isinstance(item, dict):
                continue
            val = _lay_gia_tri_truong(item, ten_mat)
            if val is _KHONG_CO or val is None:
                continue
            if isinstance(val, list):
                for x in val:
                    if x is not None:
                        k_str = str(x)
                        dem_gia_tri[k_str] = dem_gia_tri.get(k_str, 0) + 1
            elif isinstance(val, (str, int, float, bool)):
                k_str = str(val)
                dem_gia_tri[k_str] = dem_gia_tri.get(k_str, 0) + 1
        ket_qua[ten_mat] = dem_gia_tri
    return ket_qua


def toan_tu_cay(du_lieu_vao: dict, cay_cfg: dict) -> dict:
    """Lọc theo đường dẫn phân cấp trên trường 'truong' của mục (tiền tố gốc, độ sâu tối đa)."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(cay_cfg, dict) or not cay_cfg:
        return du_lieu_vao

    truong_ten = str(cay_cfg.get("truong") or "duong_dan")
    goc_tho = str(cay_cfg.get("goc") or "").strip()
    goc = goc_tho.strip("/")
    do_sau = cay_cfg.get("do_sau")
    do_sau_int = so_nguyen_chat(do_sau, "do_sau", toi_thieu=1, toi_da=100) if do_sau is not None else None

    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        # Nếu module đã tự áp dụng lọc cây, bỏ qua bước lọc lại của lõi
        if item.get("cay_da_ap") is True:
            ket_qua.append(item)
            continue

        val = _lay_gia_tri_truong(item, truong_ten)
        if val is _KHONG_CO or val is None:
            continue
        path = str(val).strip().strip("/")

        # Kiểm tra tiền tố gốc
        if goc:
            if path != goc and not path.startswith(f"{goc}/"):
                continue
            if do_sau_int is not None:
                if path == goc:
                    phan_doan = []
                else:
                    con = path[len(goc) + 1:].strip("/")
                    phan_doan = [p for p in con.split("/") if p]
                if len(phan_doan) > do_sau_int:
                    continue
        else:
            if do_sau_int is not None:
                phan_doan = [p for p in path.split("/") if p]
                if len(phan_doan) > do_sau_int:
                    continue

        ket_qua.append(item)

    tra_ve = {**du_lieu_vao, "muc": ket_qua}
    if cay_cfg.get("dem") is True:
        tra_ve["cay_nut"] = dem_cay_nut(ket_qua, cay_cfg)
    return tra_ve


def chuan_hoa_chu_cai_dau(s: Any) -> str:
    """Lấy chữ cái đầu và chuẩn hoá về A-Z (bỏ dấu tiếng Việt, Đ xếp cùng D), còn lại '#'."""
    if s is None:
        return "#"
    chuoi = str(s).strip()
    if not chuoi:
        return "#"
    first_char = chuoi[0]
    if first_char in ("Đ", "đ"):
        return "D"
    nfd = unicodedata.normalize("NFD", first_char)
    khong_dau = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    if not khong_dau:
        return "#"
    c = khong_dau[0].upper()
    if "A" <= c <= "Z":
        return c
    return "#"


def dem_chi_muc(muc_ds: list[dict], chi_muc_cfg: dict) -> dict[str, int]:
    """Đếm số mục theo chữ cái đầu (bỏ dấu tiếng Việt, Đ xếp cùng D) trước khi cắt."""
    if not isinstance(chi_muc_cfg, dict):
        return {}
    truong_ten = str(chi_muc_cfg.get("truong") or "tieu_de").strip()
    dem: dict[str, int] = {}
    for item in muc_ds:
        if not isinstance(item, dict):
            continue
        val = _lay_gia_tri_truong(item, truong_ten)
        if val is _KHONG_CO or val is None:
            val = item.get("tieu_de") or item.get("doan") or item.get("ten") or ""
        chu = chuan_hoa_chu_cai_dau(val)
        dem[chu] = dem.get(chu, 0) + 1

    keys_az = sorted([k for k in dem.keys() if k != "#"])
    if "#" in dem:
        keys_az.append("#")
    return {k: dem[k] for k in keys_az}


def toan_tu_chi_muc(du_lieu_vao: dict, chi_muc_cfg: dict) -> dict:
    """Toán tử chỉ mục A-Z: tính đếm chi_muc trước khi cắt; nếu có chi_muc.chon thì lọc giữ mục bắt đầu bằng chữ đó."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(chi_muc_cfg, dict) or not chi_muc_cfg:
        return du_lieu_vao

    dem = dem_chi_muc(muc_in, chi_muc_cfg)
    truong_ten = str(chi_muc_cfg.get("truong") or "tieu_de").strip()
    chon = chi_muc_cfg.get("chon")
    if chon is not None and str(chon).strip():
        chon_chuan = chuan_hoa_chu_cai_dau(str(chon).strip())
        ket_qua = []
        for item in muc_in:
            if not isinstance(item, dict):
                continue
            val = _lay_gia_tri_truong(item, truong_ten)
            if val is _KHONG_CO or val is None:
                val = item.get("tieu_de") or item.get("doan") or item.get("ten") or ""
            chu = chuan_hoa_chu_cai_dau(val)
            if chu == chon_chuan:
                ket_qua.append(item)
        return {**du_lieu_vao, "muc": ket_qua, "chi_muc": dem}

    return {**du_lieu_vao, "chi_muc": dem}


def _item_khop_muc_luc(item: dict, tai_lieu: str | None, muc_id: str) -> bool:
    """Kiểm tra một mục có khớp tai_lieu và muc_id của đề mục hay không."""
    if not isinstance(item, dict):
        return False
    if tai_lieu:
        tl_item = item.get("tai_lieu")
        if tl_item is None and isinstance(item.get("truong"), dict):
            tl_item = item["truong"].get("tai_lieu")
        if tl_item is not None and str(tl_item) != str(tai_lieu):
            return False

    m_id_item = item.get("muc_id")
    if m_id_item is None and isinstance(item.get("truong"), dict):
        m_id_item = item["truong"].get("muc_id")
    if m_id_item is not None and str(m_id_item) == str(muc_id):
        return True

    vi_tri = item.get("vi_tri")
    if vi_tri is not None:
        if str(vi_tri) == str(muc_id):
            return True
        if isinstance(vi_tri, dict) and str(vi_tri.get("muc_id") or "") == str(muc_id):
            return True

    return False


def _item_co_thong_tin_muc(item: dict) -> bool:
    """Kiểm tra mục có trường vi_tri hoặc truong.muc_id hay không."""
    if not isinstance(item, dict):
        return False
    if item.get("muc_id") is not None:
        return True
    if isinstance(item.get("truong"), dict) and item["truong"].get("muc_id") is not None:
        return True
    if item.get("vi_tri") is not None:
        return True
    return False


def toan_tu_muc_luc(du_lieu_vao: dict, muc_luc_cfg: dict) -> dict:
    """Toán tử nhảy tới đề mục: lọc theo vi_tri/truong.muc_id nếu module chưa tự áp dụng, không có thì ghi so_do_bo_qua."""
    if not isinstance(du_lieu_vao, dict):
        return {"muc": []}
    muc_in = du_lieu_vao.get("muc")
    if not isinstance(muc_in, list):
        return {**du_lieu_vao, "muc": []}
    if not isinstance(muc_luc_cfg, dict) or not muc_luc_cfg:
        return du_lieu_vao

    tai_lieu = muc_luc_cfg.get("tai_lieu")
    muc_id = muc_luc_cfg.get("muc_id")
    if not muc_id:
        return du_lieu_vao

    da_ap_dung = any(isinstance(x, dict) and x.get("muc_luc_da_ap") is True for x in muc_in)
    if da_ap_dung:
        return du_lieu_vao

    co_it_nhat_mot_muc_thong_tin = any(_item_co_thong_tin_muc(x) for x in muc_in)
    if not co_it_nhat_mot_muc_thong_tin:
        so_do_bo_qua = dict(du_lieu_vao.get("so_do_bo_qua") or {})
        nguon_ds = {x.get("nguon") for x in muc_in if isinstance(x, dict) and x.get("nguon")}
        if not nguon_ds and du_lieu_vao.get("nguon"):
            ng = du_lieu_vao["nguon"]
            nguon_ds = {ng} if isinstance(ng, str) else set(ng)
        for ng in nguon_ds:
            if ng:
                cur = list(so_do_bo_qua.get(ng) or [])
                if "muc_luc" not in cur:
                    cur.append("muc_luc")
                so_do_bo_qua[ng] = cur
        return {**du_lieu_vao, "so_do_bo_qua": so_do_bo_qua}

    ket_qua = []
    for item in muc_in:
        if not isinstance(item, dict):
            continue
        if _item_khop_muc_luc(item, tai_lieu, str(muc_id)):
            ket_qua.append(item)
    return {**du_lieu_vao, "muc": ket_qua}


# ───────────── Thực thi từng tầng ─────────────


async def _thuc_hien_mot_tang(
    kha_nang: str,
    nguon: str | None,
    tham_so: dict,
    du_lieu_vao: dict,
    so: SoModule,
) -> dict:
    """Thực thi một tầng đơn lẻ của quy trình."""
    # 1. Toán tử gộp RRF
    if kha_nang == "gop":
        k = so_nguyen_chat(tham_so.get("k", 60), "k", toi_thieu=1, toi_da=100_000)
        return toan_tu_gop(du_lieu_vao, k=k)

    # 2. Toán tử lọc
    if kha_nang == "loc":
        dieu_kien = tham_so.get("dieu_kien") if "dieu_kien" in tham_so else tham_so
        return toan_tu_loc(du_lieu_vao, dieu_kien)

    # 3. Toán tử cắt (top N)
    if kha_nang == "cat":
        n = tham_so.get("n") or tham_so.get("so_luong") or tham_so.get("top_n") or du_lieu_vao.get("so_luong", 10)
        return toan_tu_cat(du_lieu_vao, so_nguyen_chat(n, "so_luong", toi_thieu=0, toi_da=100_000))

    # 4. Toán tử gộp ngữ cảnh
    if kha_nang == "gop_ngu_canh":
        tran = tham_so.get("tran_ky_tu") or tham_so.get("tran") or tham_so.get("max_chars")
        if tran is None and isinstance(du_lieu_vao.get("ngu_canh"), dict):
            tran = du_lieu_vao["ngu_canh"].get("tran_ky_tu")
        return toan_tu_gop_ngu_canh(du_lieu_vao, so_nguyen_chat(
            tran if tran is not None else 2000, "tran_ky_tu", toi_thieu=0, toi_da=10_000_000))

    # 5. Toán tử cây
    if kha_nang == "cay":
        cay_cfg = tham_so if ("truong" in tham_so or "goc" in tham_so) else du_lieu_vao.get("cay", {})
        return toan_tu_cay(du_lieu_vao, cay_cfg)

    # 6. Toán tử đề mục (muc_luc)
    if kha_nang == "muc_luc":
        muc_luc_cfg = tham_so if ("muc_id" in tham_so or "tai_lieu" in tham_so) else du_lieu_vao.get("muc_luc", {})
        return toan_tu_muc_luc(du_lieu_vao, muc_luc_cfg)

    # 7. Toán tử chỉ mục A-Z (chi_muc)
    if kha_nang == "chi_muc":
        chi_muc_cfg = tham_so if ("truong" in tham_so or "kieu" in tham_so or "chon" in tham_so) else du_lieu_vao.get("chi_muc", {})
        return toan_tu_chi_muc(du_lieu_vao, chi_muc_cfg)

    # 8. Khả năng tìm kiếm (tim_kiem)
    if kha_nang == "tim_kiem":
        yc = {**du_lieu_vao, **tham_so}
        bat_tat = yc.get("bat_tat") if isinstance(yc.get("bat_tat"), dict) else {}
        bat_tat_nguon = bat_tat.get("nguon") if isinstance(bat_tat.get("nguon"), dict) else {}
        bat_tat_so_do = bat_tat.get("so_do") if isinstance(bat_tat.get("so_do"), dict) else {}

        if nguon:
            m = so.dang_chay.get(nguon)
            km = so.catalog.get(nguon)
            if not m or not km or not callable(getattr(m, "tim_kiem", None)):
                raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không hỗ trợ tim_kiem", code="module_not_ready")
            if "tim_kiem" not in km.manifest.get("cung_cap", []):
                raise LoiOpenAI(400, f"Khả năng «tim_kiem» không được module {nguon!r} cung cấp", code="unknown_capability")
            if "tim_kiem" in getattr(km, "chuan_loi", {}):
                raise LoiOpenAI(501, f"Khả năng «tim_kiem» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")
            if ngu_canh.lay() is None:
                raise LoiOpenAI(403, "Thiếu ngữ cảnh thực thi (missing context)", code="missing_context")
            kq = await goi(m, "tim_kiem", yc)
            if not isinstance(kq, dict):
                kq = {"muc": []}
            muc = kq.get("muc", [])
            for x in muc:
                if isinstance(x, dict):
                    x["nguon"] = nguon
                    x["nguon_ngoai"] = True

            so_do_bo_qua_m: list[str] = []
            if isinstance(kq.get("so_do_bo_qua"), list):
                so_do_bo_qua_m.extend(kq["so_do_bo_qua"])
            elif isinstance(kq.get("so_do_bo_qua"), dict) and nguon in kq["so_do_bo_qua"]:
                so_do_bo_qua_m.extend(kq["so_do_bo_qua"][nguon])

            so_do_ho_tro = km.manifest.get("so_do_tim_kiem") if km else None
            if isinstance(so_do_ho_tro, list):
                for sd in bat_tat_so_do.keys():
                    if sd not in so_do_ho_tro and sd not in so_do_bo_qua_m:
                        so_do_bo_qua_m.append(sd)

            # Xử lý nhảy tới đề mục (muc_luc)
            if yc.get("muc_luc") and isinstance(yc["muc_luc"], dict):
                ml_cfg = yc["muc_luc"]
                mod_da_ho_tro = bool(
                    kq.get("muc_luc_da_ap") is True
                    or (so_do_ho_tro and "muc_luc" in so_do_ho_tro and "muc_luc" not in so_do_bo_qua_m)
                )
                if not mod_da_ho_tro:
                    co_thong_tin = any(_item_co_thong_tin_muc(x) for x in muc if isinstance(x, dict))
                    if co_thong_tin:
                        muc_loc = [
                            x for x in muc
                            if isinstance(x, dict) and _item_khop_muc_luc(x, ml_cfg.get("tai_lieu"), str(ml_cfg.get("muc_id") or ""))
                        ]
                        for x in muc_loc:
                            x["muc_luc_da_ap"] = True
                        muc = muc_loc
                    else:
                        if "muc_luc" not in so_do_bo_qua_m:
                            so_do_bo_qua_m.append("muc_luc")

            so_do_bo_qua_dict = {nguon: so_do_bo_qua_m} if so_do_bo_qua_m else {}
            return {**du_lieu_vao, **kq, "muc": muc, "so_do_bo_qua": so_do_bo_qua_dict}

        # Tập hợp các module khai tim_kiem sẵn sàng
        ds_san_sang: dict[str, tuple[Any, Any]] = {}
        for mid, m in list(so.dang_chay.items()):
            km = so.catalog.get(mid)
            if km and "tim_kiem" in km.manifest.get("cung_cap", []) and "tim_kiem" not in getattr(km, "chuan_loi", {}):
                if callable(getattr(m, "tim_kiem", None)):
                    ds_san_sang[mid] = (m, km)

        loc_nguon = du_lieu_vao.get("nguon")
        if isinstance(loc_nguon, str) and loc_nguon:
            loc_nguon = [loc_nguon]

        nguon_bo_qua: list[str] = []

        # 1. Nếu có loc_nguon (danh sách chọn)
        if isinstance(loc_nguon, list):
            ds_chon: list[str] = []
            for nid in loc_nguon:
                if nid in ds_san_sang:
                    if nid not in ds_chon:
                        ds_chon.append(nid)
                else:
                    if nid not in nguon_bo_qua:
                        nguon_bo_qua.append(nid)
        else:
            ds_chon = list(ds_san_sang.keys())

        # 2. Xử lý bat_tat.nguon (công tắc tắt/bật nguồn)
        for nid, bat in bat_tat_nguon.items():
            if nid not in ds_san_sang and nid not in nguon_bo_qua:
                nguon_bo_qua.append(nid)

        ds_cuoi: list[str] = []
        for nid in ds_chon:
            if bat_tat_nguon.get(nid) is False:
                if nid not in nguon_bo_qua:
                    nguon_bo_qua.append(nid)
            else:
                ds_cuoi.append(nid)

        ds_m = [(nid, ds_san_sang[nid][0], ds_san_sang[nid][1]) for nid in ds_cuoi if nid in ds_san_sang]

        if not ds_m:
            return {
                **du_lieu_vao,
                "danh_sach": [],
                "muc": [],
                "tong": 0,
                "nguon_bo_qua": nguon_bo_qua,
                "so_do_bo_qua": {},
            }

        cac_task = [m.tim_kiem(yc) for _, m, _ in ds_m]
        cac_kq = await asyncio.gather(*cac_task, return_exceptions=True)

        danh_sach_cac_nguon = []
        tat_ca_muc = []
        so_do_bo_qua_tong: dict[str, list[str]] = {}

        for (mid, _, km), res in zip(ds_m, cac_kq):
            if isinstance(res, Exception):
                log.warning("Module %s lỗi khi gọi tim_kiem: %s", mid, res)
                continue
            if isinstance(res, dict):
                # Xử lý so_do_bo_qua
                bo_qua_cua_m = []
                if isinstance(res.get("so_do_bo_qua"), list):
                    bo_qua_cua_m.extend(res["so_do_bo_qua"])
                elif isinstance(res.get("so_do_bo_qua"), dict) and mid in res["so_do_bo_qua"]:
                    bo_qua_cua_m.extend(res["so_do_bo_qua"][mid])

                so_do_ho_tro = km.manifest.get("so_do_tim_kiem")
                if isinstance(so_do_ho_tro, list):
                    for sd in bat_tat_so_do.keys():
                        if sd not in so_do_ho_tro and sd not in bo_qua_cua_m:
                            bo_qua_cua_m.append(sd)

                # Xử lý nhảy tới đề mục (muc_luc)
                if yc.get("muc_luc") and isinstance(yc["muc_luc"], dict):
                    ml_cfg = yc["muc_luc"]
                    mod_da_ho_tro = bool(
                        res.get("muc_luc_da_ap") is True
                        or (so_do_ho_tro and "muc_luc" in so_do_ho_tro and "muc_luc" not in bo_qua_cua_m)
                    )
                    if not mod_da_ho_tro:
                        items_raw = res.get("muc") if isinstance(res.get("muc"), list) else []
                        co_thong_tin = any(_item_co_thong_tin_muc(x) for x in items_raw if isinstance(x, dict))
                        if co_thong_tin:
                            muc_loc = [
                                x for x in items_raw
                                if isinstance(x, dict) and _item_khop_muc_luc(x, ml_cfg.get("tai_lieu"), str(ml_cfg.get("muc_id") or ""))
                            ]
                            for x in muc_loc:
                                x["muc_luc_da_ap"] = True
                            res["muc"] = muc_loc
                        else:
                            if "muc_luc" not in bo_qua_cua_m:
                                bo_qua_cua_m.append("muc_luc")

                if bo_qua_cua_m:
                    so_do_bo_qua_tong[mid] = bo_qua_cua_m

                if isinstance(res.get("muc"), list):
                    sub_list = []
                    for item in res["muc"]:
                        if isinstance(item, dict):
                            item_moi = dict(item)
                            item_moi["nguon"] = mid
                            item_moi["nguon_ngoai"] = True
                            sub_list.append(item_moi)
                            tat_ca_muc.append(item_moi)
                    danh_sach_cac_nguon.append(sub_list)

        return {
            **du_lieu_vao,
            "danh_sach": danh_sach_cac_nguon,
            "muc": tat_ca_muc,
            "nguon_bo_qua": nguon_bo_qua,
            "so_do_bo_qua": so_do_bo_qua_tong,
        }

    # 7. Khả năng xếp hạng lại (xep_hang)
    if kha_nang == "xep_hang":
        muc_in = du_lieu_vao.get("muc", [])
        if not isinstance(muc_in, list) or not muc_in:
            return du_lieu_vao

        m = None
        km = None
        if nguon:
            m = so.dang_chay.get(nguon)
            km = so.catalog.get(nguon)
            if not m or not km or not callable(getattr(m, "xep_hang", None)):
                raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không hỗ trợ xep_hang", code="module_not_ready")
            if "xep_hang" not in km.manifest.get("cung_cap", []):
                raise LoiOpenAI(400, f"Khả năng «xep_hang» không được module {nguon!r} cung cấp", code="unknown_capability")
            if "xep_hang" in getattr(km, "chuan_loi", {}):
                raise LoiOpenAI(501, f"Khả năng «xep_hang» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")
        else:
            for mid, mod in list(so.dang_chay.items()):
                c_km = so.catalog.get(mid)
                if c_km and "xep_hang" in c_km.manifest.get("cung_cap", []) and "xep_hang" not in getattr(c_km, "chuan_loi", {}):
                    if callable(getattr(mod, "xep_hang", None)):
                        m = mod
                        km = c_km
                        break

        if not m:
            raise LoiOpenAI(400, "Không có module nào hỗ trợ khả năng «xep_hang»", code="unknown_capability")

        query = str(tham_so.get("query") or tham_so.get("chuoi") or du_lieu_vao.get("chuoi") or "")
        docs = [str(item.get("doan") or item.get("text") or "") for item in muc_in if isinstance(item, dict)]
        if not docs:
            return du_lieu_vao
        model = str(tham_so.get("model") or (f"{km.tien_to}/rerank" if km else "rerank"))
        top_n_raw = tham_so.get("top_n") if "top_n" in tham_so else tham_so.get("n")
        if top_n_raw is not None:
            top_n = min(so_nguyen_chat(top_n_raw, "top_n", toi_thieu=1, toi_da=100_000), len(docs))
        else:
            top_n = len(docs)
        if ngu_canh.lay() is None:
            raise LoiOpenAI(403, "Thiếu ngữ cảnh thực thi (missing context)", code="missing_context")
        kq_raw = await goi(m, "xep_hang", {"model": model, "query": query, "documents": docs, "top_n": top_n})
        if isinstance(kq_raw, tuple) and len(kq_raw) == 3:
            ma, _, noi_dung = kq_raw
            if ma >= 400:
                raise LoiOpenAI(ma, f"Module xếp hạng trả mã lỗi {ma}")
            if isinstance(noi_dung, (bytes, bytearray)):
                noi_dung = json.loads(noi_dung)
            kq_json = noi_dung
        elif isinstance(kq_raw, dict):
            kq_json = kq_raw
        else:
            raise LoiOpenAI(502, "Kết quả từ module xep_hang không đúng định dạng", code="upstream_error")

        results = kq_json.get("results", [])
        if not isinstance(results, list):
            results = []

        muc_sap_xep = []
        for r in results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            diem = r.get("relevance_score", 0.0)
            if isinstance(idx, int) and 0 <= idx < len(muc_in):
                muc_item = dict(muc_in[idx])
                muc_item["diem"] = diem
                muc_item["nguon_ngoai"] = True
                muc_sap_xep.append(muc_item)

        if top_n and top_n > 0:
            muc_sap_xep = muc_sap_xep[:top_n]

        return {**du_lieu_vao, "muc": muc_sap_xep}

    # 8. Khả năng khác do module khai: CHỈ cho phép các khả năng thuộc chuẩn phễu tri thức
    # theo HOP_DONG_KHA_NANG (tim_kiem, xep_hang, nhung). Tuyệt đối không gọi phương thức
    # vòng đời (nap, cai, dung, suc_khoe...) hay khả năng ngoài phễu (anh, noi, video_tao...).
    if kha_nang not in KHA_NANG_PHEU_MODULE or kha_nang not in HOP_DONG_KHA_NANG:
        raise LoiOpenAI(400, f"Khả năng «{kha_nang}» không hợp lệ hoặc không có module nào hỗ trợ", code="unknown_capability")

    cac_pt_hop_dong = HOP_DONG_KHA_NANG.get(kha_nang, ())

    if nguon:
        m = so.dang_chay.get(nguon)
        km = so.catalog.get(nguon)
        if not m or not km:
            raise LoiOpenAI(404, f"Module {nguon!r} không sẵn sàng hoặc không hỗ trợ {kha_nang}", code="module_not_ready")
        if kha_nang not in km.manifest.get("cung_cap", []):
            raise LoiOpenAI(400, f"Khả năng «{kha_nang}» không được module {nguon!r} cung cấp", code="unknown_capability")
        if kha_nang in getattr(km, "chuan_loi", {}):
            raise LoiOpenAI(501, f"Khả năng «{kha_nang}» của module {nguon!r} không đúng chuẩn", code="capability_not_supported")
        pt = getattr(m, kha_nang, None)
        if kha_nang not in cac_pt_hop_dong or not callable(pt):
            raise LoiOpenAI(501, f"Module {nguon!r} thiếu phương thức «{kha_nang}»", code="capability_not_supported")

        yc = {**du_lieu_vao, **tham_so}
        kq = await pt(yc)
        if isinstance(kq, tuple) and len(kq) == 3:
            ma, _, noi_dung = kq
            if ma >= 400:
                raise LoiOpenAI(ma, f"Module {nguon!r} trả mã lỗi {ma}")
            if isinstance(noi_dung, (bytes, bytearray)):
                noi_dung = json.loads(noi_dung)
            kq_dict = noi_dung if isinstance(noi_dung, dict) else {}
            return {**du_lieu_vao, **kq_dict}
        return {**du_lieu_vao, **kq} if isinstance(kq, dict) else du_lieu_vao

    for mid, m in list(so.dang_chay.items()):
        km = so.catalog.get(mid)
        if (
            km
            and kha_nang in km.manifest.get("cung_cap", [])
            and kha_nang not in getattr(km, "chuan_loi", {})
            and kha_nang in cac_pt_hop_dong
            and callable(getattr(m, kha_nang, None))
        ):
            pt = getattr(m, kha_nang)
            yc = {**du_lieu_vao, **tham_so}
            kq = await pt(yc)
            if isinstance(kq, tuple) and len(kq) == 3:
                ma, _, noi_dung = kq
                if ma >= 400:
                    raise LoiOpenAI(ma, f"Module {mid!r} trả mã lỗi {ma}")
                if isinstance(noi_dung, (bytes, bytearray)):
                    noi_dung = json.loads(noi_dung)
                kq_dict = noi_dung if isinstance(noi_dung, dict) else {}
                return {**du_lieu_vao, **kq_dict}
            return {**du_lieu_vao, **kq} if isinstance(kq, dict) else du_lieu_vao

    # Khả năng lạ không thuộc toán tử và không có module khai
    raise LoiOpenAI(400, f"Khả năng «{kha_nang}» không hợp lệ hoặc không có module nào hỗ trợ", code="unknown_capability")


# ───────────── Hàm điều phối quy trình chính ─────────────


async def chay(
    tang: list,
    du_lieu_vao: dict,
    so: SoModule,
    danh_tinh: dict | None = None,
) -> dict:
    """Chạy quy trình nối tầng với danh sách tầng có thứ tự.

    Trả về: {"ket_qua": ..., "bao_cao": [{ten, vao, ra, ms, loi?}]}
    """
    if not isinstance(tang, list):
        raise LoiOpenAI(400, "Danh sách tầng «pheu» phải là mảng", code="invalid_pipeline")

    if len(tang) > TRAN_SO_TANG:
        raise LoiOpenAI(400, f"Vượt quá trần số tầng quy trình (tối đa {TRAN_SO_TANG}, nhận {len(tang)})", code="too_many_steps")

    try:
        gioi_han_giay = float(os.environ.get("WEB2API_QUY_TRINH_GIAY", MAC_DINH_THOI_GIAN_GIAY))
    except (ValueError, TypeError):
        gioi_han_giay = MAC_DINH_THOI_GIAN_GIAY

    thoi_gian_bat_dau = time.monotonic()
    du_lieu_hien_tai = dict(du_lieu_vao) if isinstance(du_lieu_vao, dict) else {}
    bao_cao: list[dict] = []

    for i, t in enumerate(tang):
        if not isinstance(t, dict):
            raise LoiOpenAI(400, f"Tầng thứ {i+1} phải là đối tượng JSON", code="invalid_step")

        kha_nang = str(t.get("kha_nang") or "").strip()
        if not kha_nang:
            raise LoiOpenAI(400, f"Tầng thứ {i+1} thiếu «kha_nang»", code="missing_capability")

        ten = str(t.get("ten") or f"tang_{i+1}_{kha_nang}")
        nguon = str(t.get("nguon")).strip() if t.get("nguon") else None
        tham_so = t.get("tham_so") or {}
        if not isinstance(tham_so, dict):
            tham_so = {}

        # Đo số lượng mục vào
        if isinstance(du_lieu_hien_tai.get("muc"), list):
            vao = len(du_lieu_hien_tai["muc"])
        elif isinstance(du_lieu_hien_tai.get("danh_sach"), list):
            vao = sum(len(sub) for sub in du_lieu_hien_tai["danh_sach"] if isinstance(sub, list))
        else:
            vao = 0

        # Kiểm tra thời gian còn lại
        da_chay = time.monotonic() - thoi_gian_bat_dau
        con_lai = gioi_han_giay - da_chay
        if con_lai <= 0:
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": 0.0, "loi": f"Quá hạn thời gian quy trình ({gioi_han_giay}s)"})
            raise LoiQuyTrinh(504, f"Quá hạn thời gian quy trình ({gioi_han_giay}s) tại tầng {ten!r}", bao_cao=bao_cao, tang_loi=ten, code="gateway_timeout")

        t0 = time.monotonic()
        try:
            kiem_tra_pham_vi_tang(danh_tinh, kha_nang)
            du_lieu_moi = await asyncio.wait_for(
                _thuc_hien_mot_tang(kha_nang, nguon, tham_so, du_lieu_hien_tai, so),
                timeout=con_lai,
            )
        except asyncio.TimeoutError:
            ms = round((time.monotonic() - t0) * 1000, 2)
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": ms, "loi": f"Quá hạn thời gian quy trình ({gioi_han_giay}s)"})
            raise LoiQuyTrinh(504, f"Quá hạn thời gian quy trình ({gioi_han_giay}s) tại tầng {ten!r}", bao_cao=bao_cao, tang_loi=ten, code="gateway_timeout")
        except Exception as e:
            ms = round((time.monotonic() - t0) * 1000, 2)
            # Lỗi chuẩn của lõi (LoiOpenAI) đã che khoá → giữ nguyên văn; lỗi lạ từ mã module có thể chứa dữ liệu nội bộ
            # → chỉ ghi log, khách nhận thông báo chung.
            if isinstance(e, LoiOpenAI):
                mo_ta = str(e)
            else:
                log.warning("Quy trình: tầng %r lỗi %s: %s", ten, type(e).__name__, e)
                mo_ta = f"tầng gặp lỗi nội bộ ({type(e).__name__})"
            bao_cao.append({"ten": ten, "vao": vao, "ra": 0, "ms": ms, "loi": mo_ta})
            ma_loi = getattr(e, "ma", 500) if isinstance(e, LoiOpenAI) else 500
            code_loi = getattr(e, "than", {}).get("error", {}).get("code", "step_failed") if isinstance(e, LoiOpenAI) else "step_failed"
            raise LoiQuyTrinh(ma_loi, f"Lỗi tại tầng {ten!r}: {mo_ta}", bao_cao=bao_cao, tang_loi=ten, code=code_loi) from e

        ms = round((time.monotonic() - t0) * 1000, 2)
        if isinstance(du_lieu_moi, dict) and isinstance(du_lieu_moi.get("muc"), list):
            ra = len(du_lieu_moi["muc"])
        elif isinstance(du_lieu_moi, dict) and isinstance(du_lieu_moi.get("danh_sach"), list):
            ra = sum(len(sub) for sub in du_lieu_moi["danh_sach"] if isinstance(sub, list))
        else:
            ra = vao

        muc_bc: dict[str, Any] = {"ten": ten, "vao": vao, "ra": ra, "ms": ms}
        if isinstance(du_lieu_moi, dict) and "nguon_bo_qua" in du_lieu_moi and du_lieu_moi["nguon_bo_qua"]:
            muc_bc["bo_qua"] = list(du_lieu_moi["nguon_bo_qua"])
            muc_bc["nguon_bo_qua"] = list(du_lieu_moi["nguon_bo_qua"])
        bao_cao.append(muc_bc)
        du_lieu_hien_tai = du_lieu_moi

    return {"ket_qua": du_lieu_hien_tai, "bao_cao": bao_cao}
