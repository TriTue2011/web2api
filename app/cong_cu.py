"""Chuẩn CÔNG CỤ cho AI (cong_cu): chuyển thực thể và hành động thành tools chuẩn OpenAI function-calling.

Nguyên tắc:
- Lõi không chứa tên nhà cung cấp hay module cụ thể nào; mọi thứ là mã mở.
- Hàm thuần, không I/O, không gọi mạng hay đọc ghi đĩa.
- Một tool cho mỗi cặp (loại thực thể × hành động), không phải mỗi thực thể.
- Đầu vào không tin được: tên hay mô tả tự do từ nguồn ngoài không đưa nguyên văn vào description.
- Lược đồ tham số: luôn có thuc_the, không bao giờ có xac_nhan (model không được tự xác nhận).
- Giới hạn trần: tối đa 128 tools, vượt thì cắt và đánh dấu bi_cat=True.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any

TRAN_CONG_CU = 128
TRAN_DUNG_LUONG_SCHEMA = 4096
TRAN_DO_SAU_SCHEMA = 4
_MAU_TEN_OPENAI = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAU_DINH_DANH = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_BAC_RUI_RO = ("doc", "ghi", "nguy_hiem")


def _chuan_rui_ro(v: Any) -> str:
    """Mã rủi ro hợp lệ giữ nguyên; thiếu hoặc lạ ⇒ «nguy_hiem» (nguyên tắc đóng khi lỗi, cùng quy tắc với thiet_bi.py)."""
    return v if v in _BAC_RUI_RO else "nguy_hiem"


class DanhSachCongCu(list):
    """Danh sách công cụ hỗ trợ thuộc tính tong và bi_cat cho phản hồi API."""

    def __init__(self, items: list[dict[str, Any]], tong: int, bi_cat: bool):
        super().__init__(items)
        self.tong = tong
        self.bi_cat = bi_cat


def bo_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt để chuẩn hoá tên."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFD", s)
    khong_dau = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return khong_dau.replace("đ", "d").replace("Đ", "d")


def lam_sach_chuoi_an_toan(s: str, toi_da: int = 120) -> str:
    """Loại bỏ ký tự điều khiển, ký tự xuống dòng và cắt độ dài an toàn."""
    if not isinstance(s, str):
        return ""
    # Giữ lại ký tự in được (ASCII >= 32 và != 127, hoặc ký tự unicode hợp lệ)
    sach = "".join(c for c in s if (c >= " " and c != "\x7f") or c == " ")
    # Rút gọn khoảng trắng
    sach = re.sub(r"\s+", " ", sach).strip()
    return sach[:toi_da]


def chuan_hoa_ten_openai(loai: str, hanh_dong: str, da_dung: set[str]) -> str:
    """Sinh định danh tool chuẩn OpenAI ^[A-Za-z0-9_-]{1,64}$ từ loai và hanh_dong.

    Xử lý trùng lặp bằng cách thêm hậu tố băm ngắn ổn định.
    """
    l_sach = re.sub(r"[^A-Za-z0-9_-]+", "_", bo_dau(loai)).strip("_")
    h_sach = re.sub(r"[^A-Za-z0-9_-]+", "_", bo_dau(hanh_dong)).strip("_")

    if not l_sach:
        l_sach = "thiet_bi"
    if not h_sach:
        h_sach = "hanh_dong"

    co_ban = f"{l_sach}_{h_sach}"[:64]
    if co_ban not in da_dung:
        da_dung.add(co_ban)
        return co_ban

    # Trùng tên: thêm hậu tố băm ổn định
    bam = hashlib.sha256(f"{loai}\0{hanh_dong}".encode("utf-8")).hexdigest()[:6]
    ten_moi = f"{co_ban[:57]}_{bam}"
    lan_lap = 1
    while ten_moi in da_dung:
        lan_lap += 1
        bam_i = hashlib.sha256(f"{loai}\0{hanh_dong}\0{lan_lap}".encode("utf-8")).hexdigest()[:6]
        ten_moi = f"{co_ban[:57]}_{bam_i}"

    da_dung.add(ten_moi)
    return ten_moi


class _LoiLuocDo(Exception):
    """Ngoại lệ nội bộ khi lược đồ tham số vi phạm trần độ sâu hoặc dung lượng."""


def _lam_sach_de_quy(luoc_do: Any, do_sau: int) -> dict[str, Any]:
    if not isinstance(luoc_do, dict):
        return {"type": "object"}
    if do_sau > TRAN_DO_SAU_SCHEMA:
        raise _LoiLuocDo("Độ sâu vượt quá trần 4")

    kq: dict[str, Any] = {}

    # 1. type
    if "type" in luoc_do:
        t = luoc_do["type"]
        if isinstance(t, str) and _MAU_DINH_DANH.fullmatch(t.strip()):
            kq["type"] = t.strip()
        elif isinstance(t, list):
            cac_t = [str(x).strip() for x in t if isinstance(x, str) and _MAU_DINH_DANH.fullmatch(str(x).strip())]
            if cac_t:
                kq["type"] = cac_t

    # 2. enum
    if "enum" in luoc_do and isinstance(luoc_do["enum"], list):
        cac_enum = []
        for v in luoc_do["enum"]:
            if isinstance(v, bool):
                cac_enum.append(v)
            elif isinstance(v, (int, float)):
                if math.isfinite(v):
                    cac_enum.append(v)
            elif isinstance(v, str):
                v_sach = v.strip()
                if _MAU_DINH_DANH.fullmatch(v_sach):
                    cac_enum.append(v_sach)
        if cac_enum:
            kq["enum"] = cac_enum

    # 3. properties
    if "properties" in luoc_do and isinstance(luoc_do["properties"], dict):
        props: dict[str, Any] = {}
        for k, v in luoc_do["properties"].items():
            if not isinstance(k, str):
                continue
            k_sach = k.strip()
            # Bỏ xac_nhan và các tên không khớp định danh an toàn
            if k_sach == "xac_nhan" or not _MAU_DINH_DANH.fullmatch(k_sach):
                continue
            props[k_sach] = _lam_sach_de_quy(v, do_sau=do_sau + 1)
        if props:
            kq["properties"] = props

    # 4. required
    if "required" in luoc_do and isinstance(luoc_do["required"], list):
        reqs = []
        for r in luoc_do["required"]:
            if isinstance(r, str):
                r_sach = r.strip()
                if r_sach != "xac_nhan" and _MAU_DINH_DANH.fullmatch(r_sach):
                    reqs.append(r_sach)
        if reqs:
            kq["required"] = reqs

    # 5. items
    if "items" in luoc_do:
        items = luoc_do["items"]
        if isinstance(items, dict):
            kq["items"] = _lam_sach_de_quy(items, do_sau=do_sau + 1)
        elif isinstance(items, list):
            kq["items"] = [_lam_sach_de_quy(it, do_sau=do_sau + 1) for it in items if isinstance(it, dict)]

    # 6. Giới hạn số/độ dài: mọi số lấy từ lược đồ ngoài phải hữu hạn
    for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if k in luoc_do and isinstance(luoc_do[k], (int, float)) and not isinstance(luoc_do[k], bool):
            if math.isfinite(luoc_do[k]):
                kq[k] = luoc_do[k]

    for k in ("minLength", "maxLength"):
        if k in luoc_do and isinstance(luoc_do[k], int) and not isinstance(luoc_do[k], bool) and luoc_do[k] >= 0:
            kq[k] = luoc_do[k]

    return kq if kq else {"type": "object"}


def lam_sach_luoc_do(luoc_do: Any, do_sau: int = 1) -> dict[str, Any]:
    """Lọc sạch lược đồ tham số, chỉ giữ các khoá cấu trúc, trần độ sâu 4 và dung lượng 4KB.

    - Chỉ giữ: type, properties, required, enum, items, minimum, maximum, minLength, maxLength.
    - Bỏ triệt để các trường chữ tự do (description, title, examples, default...).
    - Trần độ sâu 4 cấp; quá trần -> {"type": "object"}.
    - Giá trị enum chỉ nhận định danh an toàn (^[A-Za-z0-9_.:-]{1,64}$) hoặc số/bool.
    - Không bao giờ chứa trường xac_nhan.
    - Tổng dung lượng JSON sau làm sạch <= 4096 bytes; quá trần -> {"type": "object"}.
    """
    try:
        kq = _lam_sach_de_quy(luoc_do, do_sau)
        if do_sau == 1:
            chuoi_json = json.dumps(kq, ensure_ascii=False)
            if len(chuoi_json.encode("utf-8")) > TRAN_DUNG_LUONG_SCHEMA:
                return {"type": "object"}
        return kq
    except _LoiLuocDo:
        return {"type": "object"}


def _tao_luoc_do_parameters(luoc_do_tham_so: dict[str, Any] | None) -> dict[str, Any]:
    """Tạo JSON Schema cho trường parameters của function tool.

    KHÔNG bao giờ chứa xac_nhan (model không được phép tự xác nhận).
    Lược đồ tham số được làm sạch cấu trúc, trần độ sâu 4 và dung lượng 4KB.
    """
    if isinstance(luoc_do_tham_so, dict):
        tham_so_schema = lam_sach_luoc_do(luoc_do_tham_so, do_sau=1)
    else:
        tham_so_schema = {"type": "object"}

    return {
        "type": "object",
        "required": ["thuc_the"],
        "properties": {
            "thuc_the": {
                "type": "string",
                "description": "id thực thể, lấy từ GET /api/thiet-bi",
            },
            "tham_so": tham_so_schema,
            "ly_do": {
                "type": "string",
            },
        },
        "additionalProperties": False,
    }


def sinh_cong_cu(thuc_the: list[dict[str, Any]], duoc_ghi: bool) -> DanhSachCongCu:
    """Biến danh sách thực thể/hành động thành danh sách tools chuẩn OpenAI function-calling.

    Tham số:
        thuc_the: Danh sách thực thể từ các module (chuẩn thiet_bi).
        duoc_ghi: True nếu người gọi có quyền ghi/quản trị; False nếu chỉ đọc (thiet_bi:doc).

    Trả về:
        DanhSachCongCu (kế thừa list) chứa tối đa 128 tools kèm thuộc tính tong và bi_cat.
    """
    if not isinstance(thuc_the, list):
        return DanhSachCongCu([], tong=0, bi_cat=False)

    cac_cap: dict[tuple[str, str], dict[str, Any]] = {}
    so_bi_loai = 0

    for item in thuc_the:
        if not isinstance(item, dict):
            continue

        raw_loai = str(item.get("loai") or "thiet_bi").strip()
        # Rủi ro hiệu lực của thực thể do lõi (thiet_bi.py) tính: ghi đè của quản trị > mặc định > «nguy_hiem».
        # Không đọc gì khác từ khai báo của module: module không tự hạ được mức rủi ro.
        rui_ro_thuc_the = _chuan_rui_ro(item.get("rui_ro"))
        tinh_nang = item.get("tinh_nang") or []

        if isinstance(tinh_nang, str):
            tinh_nang = [tinh_nang]
        elif not isinstance(tinh_nang, (list, tuple)):
            continue

        for tn in tinh_nang:
            if isinstance(tn, str):
                raw_hd = tn.strip()
                luoc_do = None
                rr_hd = rui_ro_thuc_the
            elif isinstance(tn, dict):
                raw_hd = str(tn.get("hanh_dong") or tn.get("ten") or tn.get("name") or "").strip()
                luoc_do = tn.get("tham_so") or tn.get("parameters") or tn.get("luoc_do") or tn.get("schema")
                rr_hd = max(rui_ro_thuc_the, _chuan_rui_ro(tn.get("rui_ro_mac_dinh")), key=_BAC_RUI_RO.index)
            else:
                continue

            if not raw_hd:
                continue

            # Kiểm tra định danh an toàn ^[A-Za-z0-9_.:-]{1,64}$ cho cả loai và hanh_dong
            if not _MAU_DINH_DANH.fullmatch(raw_loai) or not _MAU_DINH_DANH.fullmatch(raw_hd):
                so_bi_loai += 1
                continue

            khoa = (raw_loai, raw_hd)
            if khoa not in cac_cap:
                cac_cap[khoa] = {"rui_ro": set(), "luoc_do": None}

            cac_cap[khoa]["rui_ro"].add(rr_hd)
            if luoc_do and isinstance(luoc_do, dict) and not cac_cap[khoa]["luoc_do"]:
                cac_cap[khoa]["luoc_do"] = luoc_do

    # Xác định mức rủi ro hiệu lực cho từng cặp và lọc theo quyền duoc_ghi
    cap_hop_le: list[dict[str, Any]] = []

    # Sắp xếp khoá có thứ tự ổn định để tên và thứ tự luôn nhất quán
    cac_khoa_sap_xep = sorted(cac_cap.keys(), key=lambda k: (k[0], k[1]))

    for loai, hanh_dong in cac_khoa_sap_xep:
        thong_tin = cac_cap[(loai, hanh_dong)]
        tap_rui_ro = thong_tin["rui_ro"]

        # Mức rủi ro cao nhất trong các nguồn khai cho cùng cặp (loai, hanh_dong)
        rui_ro = max(tap_rui_ro, key=_BAC_RUI_RO.index)

        # Kiểm soát quyền:
        # duoc_ghi = False (chỉ thiet_bi:doc) -> chỉ lấy hành động rui_ro == "doc"
        # duoc_ghi = True (thiet_bi:ghi hoặc quản trị) -> lấy mọi rủi ro
        if not duoc_ghi and rui_ro != "doc":
            continue

        cap_hop_le.append({
            "loai": loai,
            "hanh_dong": hanh_dong,
            "rui_ro": rui_ro,
            "luoc_do": thong_tin["luoc_do"],
        })

    tong = len(cap_hop_le) + so_bi_loai
    bi_cat = (len(cap_hop_le) > TRAN_CONG_CU) or (so_bi_loai > 0)

    # Sinh danh sách tool (chuẩn OpenAI function-calling)
    da_dung_ten: set[str] = set()
    danh_sach_tools: list[dict[str, Any]] = []

    for muc in cap_hop_le:
        loai = muc["loai"]
        hanh_dong = muc["hanh_dong"]
        rui_ro = muc["rui_ro"]
        luoc_do = muc["luoc_do"]

        ten_tool = chuan_hoa_ten_openai(loai, hanh_dong, da_dung_ten)

        if rui_ro == "nguy_hiem":
            mo_ta = (
                f"Thực hiện hành động «{hanh_dong}» cho thực thể loại «{loai}» "
                f"(mức rủi ro: {rui_ro}) — cần quản trị xác nhận hoặc vào hàng chờ phê duyệt."
            )
        else:
            mo_ta = (
                f"Thực hiện hành động «{hanh_dong}» cho thực thể loại «{loai}» "
                f"(mức rủi ro: {rui_ro})."
            )

        parameters = _tao_luoc_do_parameters(luoc_do)

        tool = {
            "type": "function",
            "function": {
                "name": ten_tool,
                "description": mo_ta,
                "parameters": parameters,
            },
            "x_web2api": {
                "loai": loai,
                "hanh_dong": hanh_dong,
                "rui_ro": rui_ro,
            },
        }
        danh_sach_tools.append(tool)

    # Giới hạn trần tối đa 128 tools
    items = danh_sach_tools[:TRAN_CONG_CU]
    return DanhSachCongCu(items, tong=tong, bi_cat=bi_cat)
