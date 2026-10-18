"""Cổng TIN VÀO chung của lõi (module kênh nhận tin) + PHÂN QUYỀN người gửi + lệnh của chủ máy.

Quy tắc:
- Lõi không chứa tên nhà cung cấp hay module cụ thể nào.
- Tin chuẩn hoá từ module: id_tin, nguoi_gui, noi_gui, la_nhom, ten_tu_khai, van_ban, luc.
- Chống trùng (module, id_tin) 10 phút, tối đa 10.000 id (như webhook.py).
- Hạn dòng theo (module, nguoi_gui) mặc định 20 lệnh/phút, theo module 200/phút; vượt -> bỏ tin, không trả lời.
- Phân quyền theo NGƯỜI GỬI, KHÔNG theo nơi gửi (chat/nhóm): người chưa cấp quyền trong nhóm vẫn là người lạ.
- Người lạ (la): KHÔNG trả lời, KHÔNG chạy lệnh; ghi sổ người lạ gần đây (trần 200), báo 1 lần tới đích báo sổ thiết bị.
- Bảng lệnh: vai_toi_thieu bắt buộc; phân tích câu theo cấu trúc (bỏ dấu tiếng Việt kể cả đ->d, chữ thường, bỏ «/» đầu,
  bỏ hậu tố «@bot» ở token đầu). Token đầu = tên lệnh, phần còn lại = đối số thô.
- Trả lời qua hàm chung gui_den_dich() của app/gui_ra.py (nguoi_goi="kenh_vao").
- Kéo tin định kỳ (hỏi vòng) do lõi giữ vòng lặp: chu kỳ, lùi dần khi lỗi (tối đa 5 phút), dừng khi gỡ module,
  không chạy trùng, mỗi module hết giờ riêng.
- Sổ phân quyền <WEB2API_DATA>/kenh_vao.json (0600, ghi nguyên tử, KhoHong -> dừng xử lý, API 503, không ghi đè).
- Mọi API yêu cầu quyền quản trị (can_quan_tri).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import gui_ra, kho, nhat_ky, so_thiet_bi
from app.ham_khoa import KhoHong
from app.loi_openai import LoiOpenAI
from app.thiet_bi import bo_dau
from app.xac_thuc import can_quan_tri

log = logging.getLogger("web2api.kenh_vao")

router = APIRouter(prefix="/api/kenh-vao", dependencies=[Depends(can_quan_tri)])

MAU_NGUOI_GUI = re.compile(r"^[0-9A-Za-z._:@-]{1,64}\Z")
MAU_TEN_LENH = re.compile(r"^[a-z0-9-]{1,32}\Z")
MAU_VAI = re.compile(r"^[a-z0-9_]{1,32}\Z")
MAU_ID_HOP_LE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}\Z")

THU_BAC_VAI_MAC_DINH: dict[str, int] = {
    "quan_tri": 3,
    "nguoi_dung": 2,
    "chi_xem": 1,
}

TTL_PHAT_LAI_GIAY = 600.0  # 10 phút
TRAN_PHAT_LAI = 10000
TRAN_NGUOI_LA = 200
TRAN_VAN_BAN = 4000
TRAN_TRA_LOI = 1000
#: Báo «người lạ nhắn bot» tới kênh của chủ máy: tối đa bấy nhiêu tin trong cửa sổ — bot công khai thì ai cũng nhắn được, 10.000 tài
#: khoản lạ không được thành 10.000 tin vào điện thoại chủ máy. Vượt trần: chỉ ghi nhận (xem /api/kenh-vao/la), không báo thêm.
TRAN_BAO_NGUOI_LA = 5
CUA_SO_BAO_NGUOI_LA_GIAY = 600.0

# ───────────── Trạng thái trong bộ nhớ ─────────────

_KHOA = threading.RLock()
_DA_THAY: dict[tuple[str, str], float] = {}  # (module, id_tin) -> monotonic
_HAN_DONG_NGUOI: dict[tuple[str, str], deque] = {}  # (module, nguoi_gui) -> timestamps
_HAN_DONG_MODULE: dict[str, deque] = {}  # module -> timestamps
_NGUOI_LA: dict[tuple[str, str], dict] = {}  # (module, nguoi_id) -> record
_DA_BAO_NGUOI_LA: set[tuple[str, str]] = set()  # (module, nguoi_id) đã gửi tin báo 1 lần
_LAN_BAO_NGUOI_LA: deque = deque()  # monotonic của các tin báo người lạ trong cửa sổ
_HAN_DONG_LA_MODULE: dict[str, deque] = {}  # module -> timestamps xử lý NGƯỜI LẠ (ngân sách riêng, không ăn vào hạn dòng của chủ máy)
_BANG_LENH: dict[str, dict] = {}  # ten_lenh -> {ham, mo_ta, vai_toi_thieu}
_DEM = {
    "nhan": 0,
    "trung": 0,
    "vuot_han": 0,
    "tu_choi": 0,
    "lenh_chay": 0,
}


def _dat_lai_trang_thai() -> None:
    """Xoá trạng thái trong bộ nhớ (dùng cho kiểm thử)."""
    with _KHOA:
        _DA_THAY.clear()
        _HAN_DONG_NGUOI.clear()
        _HAN_DONG_MODULE.clear()
        _NGUOI_LA.clear()
        _DA_BAO_NGUOI_LA.clear()
        _LAN_BAO_NGUOI_LA.clear()
        _HAN_DONG_LA_MODULE.clear()
        _BANG_LENH.clear()
        _DEM["nhan"] = 0
        _DEM["trung"] = 0
        _DEM["vuot_han"] = 0
        _DEM["tu_choi"] = 0
        _DEM["lenh_chay"] = 0


def cap_bac_vai(vai: str) -> int:
    """Thứ bậc vai trò: quan_tri (3) > nguoi_dung (2) > chi_xem (1). Mã lạ = 0."""
    return THU_BAC_VAI_MAC_DINH.get(vai, 0)


def _han_dong_nguoi_phut() -> int:
    val = os.environ.get("WEB2API_KENH_VAO_NGUOI_PHUT", "20").strip()
    try:
        return max(1, int(val))
    except ValueError:
        return 20


def _han_dong_module_phut() -> int:
    val = os.environ.get("WEB2API_KENH_VAO_MODULE_PHUT", "200").strip()
    try:
        return max(1, int(val))
    except ValueError:
        return 200


def _han_dong_la_phut() -> int:
    val = os.environ.get("WEB2API_KENH_VAO_LA_PHUT", "60").strip()
    try:
        return max(1, min(10000, int(val)))
    except ValueError:
        return 60


def _cho_phep_xu_ly_nguoi_la(module: str) -> bool:
    """Ngân sách RIÊNG cho tin từ người lạ (theo module, mặc định 60/phút). Hết ngân sách → bỏ ngay, không đụng bộ nhớ theo dõi
    nào — kẻ lạ nhắn dồn không làm cạn hạn dòng của chủ máy và không làm phình bộ nhớ."""
    now = time.monotonic()
    with _KHOA:
        q = _HAN_DONG_LA_MODULE.setdefault(module, deque())
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= _han_dong_la_phut():
            return False
        q.append(now)
        return True


def _kiem_va_cap_nhat_han_dong(module: str, nguoi_gui: str) -> bool:
    """Kiểm tra hạn dòng theo (module, nguoi_gui) và theo module. Trả về True nếu được phép, False nếu vượt."""
    now = time.monotonic()
    nguoi_limit = _han_dong_nguoi_phut()
    mod_limit = _han_dong_module_phut()

    with _KHOA:
        # 1. Theo người
        k_nguoi = (module, nguoi_gui)
        q_nguoi = _HAN_DONG_NGUOI.setdefault(k_nguoi, deque())
        while q_nguoi and (now - q_nguoi[0] > 60.0):
            q_nguoi.popleft()
        if len(q_nguoi) >= nguoi_limit:
            return False

        # 2. Theo module
        q_mod = _HAN_DONG_MODULE.setdefault(module, deque())
        while q_mod and (now - q_mod[0] > 60.0):
            q_mod.popleft()
        if len(q_mod) >= mod_limit:
            return False

        q_nguoi.append(now)
        q_mod.append(now)
        return True


def _kiem_chong_trung(module: str, id_tin: str) -> bool:
    """Trả về True nếu id_tin BỊ TRÙNG (đã thấy trong 10 phút), False nếu là tin mới."""
    now = time.monotonic()
    with _KHOA:
        # Dọn dẹp id cũ
        het_han = [k for k, t in _DA_THAY.items() if (now - t) > TTL_PHAT_LAI_GIAY]
        for k in het_han:
            _DA_THAY.pop(k, None)

        k = (module, id_tin)
        if k in _DA_THAY:
            return True

        if len(_DA_THAY) >= TRAN_PHAT_LAI:
            # Xoá bớt mục cũ nhất
            sorted_keys = sorted(_DA_THAY.keys(), key=lambda x: _DA_THAY[x])
            for old_k in sorted_keys[:100]:
                _DA_THAY.pop(old_k, None)

        _DA_THAY[k] = now
        return False


def lam_sach_van_ban(text: Any) -> str:
    """Làm sạch văn bản: loại bỏ ký tự điều khiển (trừ newline, tab)."""
    if not isinstance(text, str):
        return ""
    return "".join(
        c for c in text if not (unicodedata.category(c).startswith("C") and c not in ("\n", "\t", "\r"))
    )


def lam_sach_ten(ten: Any) -> str | None:
    """Làm sạch tên tự khai: bỏ ký tự điều khiển, cắt ≤ 60 ký tự."""
    if not isinstance(ten, str):
        return None
    s = "".join(c for c in ten if not unicodedata.category(c).startswith("C")).strip()
    return s[:60] if s else None


# ───────────── Sổ phân quyền trên đĩa (<DATA>/kenh_vao.json) ─────────────

def duong_dan_so() -> Path:
    return kho.thu_muc_du_lieu() / "kenh_vao.json"


def doc_so() -> dict:
    """Đọc sổ phân quyền từ đĩa. Tệp chưa có -> {"nguoi": []}. Hỏng -> KhoHong."""
    p = duong_dan_so()
    with _KHOA:
        du_lieu = kho.doc_json(p)
        if du_lieu is None:
            return {"nguoi": []}
        if not isinstance(du_lieu, dict) or not isinstance(du_lieu.get("nguoi"), list):
            raise KhoHong(f"{p} cấu trúc sai: cần đối tượng có 'nguoi'")
        return du_lieu


def ghi_so(so: dict) -> None:
    """Ghi nguyên tử sổ phân quyền quyền 0600."""
    p = duong_dan_so()
    with _KHOA:
        kho.ghi_nguyen_tu(p, json.dumps(so, ensure_ascii=False, indent=2), quyen=0o600)


async def cap_quyen(module: str, nguoi_id: str, vai: str, ten: str = "") -> dict:
    """Cấp hoặc cập nhật vai cho một người trong sổ."""
    with _KHOA:
        so = doc_so()
        ds = so.setdefault("nguoi", [])
        muc = next((x for x in ds if x.get("module") == module and x.get("nguoi_id") == nguoi_id), None)
        ten_sach = lam_sach_ten(ten) or ""
        if muc is not None:
            muc["vai"] = vai
            muc["ten"] = ten_sach
        else:
            muc = {
                "module": module,
                "nguoi_id": nguoi_id,
                "vai": vai,
                "ten": ten_sach,
            }
            ds.append(muc)
        ghi_so(so)
        return muc


def tra_vai(module: str, nguoi_id: str) -> tuple[str, str | None]:
    """Tra vai của người gửi trong sổ. Trả về (vai, ten). Không có -> ('la', None). Hỏng kho -> KhoHong."""
    so = doc_so()
    for x in so.get("nguoi", []):
        if x.get("module") == module and x.get("nguoi_id") == nguoi_id:
            return x.get("vai", "la"), x.get("ten")
    return "la", None


# ───────────── Đăng ký và quản lý lệnh ─────────────

def dang_ky_lenh(ten: str, ham: Callable, mo_ta: str, vai_toi_thieu: str) -> None:
    """Đăng ký lệnh mới vào lõi.
    - vai_toi_thieu BẮT BUỘC (không có mặc định ngầm).
    - ten: khuôn [a-z0-9-]{1,32}.
    - Trùng tên -> ném ValueError.
    """
    if not isinstance(ten, str) or not MAU_TEN_LENH.match(ten):
        raise ValueError(f"Tên lệnh '{ten}' không hợp lệ (khuôn [a-z0-9-]{{1,32}}).")
    if not isinstance(vai_toi_thieu, str) or not vai_toi_thieu.strip():
        raise ValueError("vai_toi_thieu là bắt buộc (không có mặc định ngầm).")
    if not callable(ham):
        raise ValueError("ham xử lý lệnh phải là callable.")

    with _KHOA:
        if ten in _BANG_LENH:
            raise ValueError(f"Lệnh '{ten}' đã được đăng ký trước đó.")
        _BANG_LENH[ten] = {
            "ham": ham,
            "mo_ta": mo_ta,
            "vai_toi_thieu": vai_toi_thieu.strip(),
        }


def danh_sach_lenh() -> list[dict]:
    """Danh sách các lệnh đã đăng ký: [{ten, mo_ta, vai_toi_thieu}]."""
    with _KHOA:
        return [
            {
                "ten": k,
                "mo_ta": v["mo_ta"],
                "vai_toi_thieu": v["vai_toi_thieu"],
            }
            for k, v in sorted(_BANG_LENH.items())
        ]


# ───────────── Ghi nhật ký chuẩn hoá ─────────────

def _ghi_nhat_ky_kenh_vao(module: str, nguoi_gui: str, ten_lenh: str, vai: str, ket_qua: str) -> None:
    """Nhật ký kênh kenh_vao:
    - nguoi_gui băm SHA256 rút gọn (12 ký tự) như gui_ra băm kenh_id.
    - KHÔNG nội dung tin, KHÔNG đối số, KHÔNG tên tự khai, KHÔNG id thô.
    """
    nguoi_bam = hashlib.sha256(nguoi_gui.encode("utf-8")).hexdigest()[:12]
    nhat_ky.ghi(
        "kenh_vao",
        "thong_tin" if ket_qua == "ok" else "canh_bao",
        f"Kênh vào {module}: lệnh {ten_lenh} từ {nguoi_bam} ({vai}) -> {ket_qua}",
        module=module,
        nguoi_gui=nguoi_bam,
        lenh=ten_lenh,
        vai=vai,
        ket_qua=ket_qua,
    )


# ───────────── Trợ giúp gửi trả lời ─────────────

async def _gui_tra_loi(app: Any, module_id: str, noi_gui: str, van_ban: str) -> bool:
    """Gửi trả lời về đúng noi_gui qua cổng chung gui_den_dich().
    Nếu noi_gui không có trong kenh_ra() của module -> bỏ phần trả lời, ghi log ngắn, không làm vỡ xử lý.
    """
    try:
        res = await gui_ra.gui_den_dich(
            app,
            module_id,
            noi_gui,
            {"van_ban": van_ban},
            nguoi_goi="kenh_vao",
        )
        return bool(isinstance(res, dict) and res.get("ok"))
    except LoiOpenAI as e:
        log.warning("Kênh vào không thể trả lời tới %s/%s: %s (%s)", module_id, noi_gui, e, e.code)
        return False
    except Exception as e:
        log.warning("Kênh vào gửi trả lời tới %s/%s gặp lỗi: %s", module_id, noi_gui, type(e).__name__)
        return False


# ───────────── Xử lý người lạ và thông báo ─────────────

async def _ghi_nhan_va_bao_nguoi_la(
    app: Any, module_id: str, nguoi_gui: str, noi_gui: str, ten_tu_khai: str | None
) -> None:
    """Ghi nhận người lạ vào danh sách gần đây và báo tới đích của sổ thiết bị CHỈ MỘT LẦN."""
    now_iso = kho.bay_gio_iso()
    k = (module_id, nguoi_gui)
    can_bao = False

    with _KHOA:
        if k in _NGUOI_LA:
            rec = _NGUOI_LA[k]
            rec["so_lan"] += 1
            rec["lan_cuoi"] = now_iso
            if ten_tu_khai:
                rec["ten_tu_khai"] = ten_tu_khai
        else:
            if len(_NGUOI_LA) >= TRAN_NGUOI_LA:
                # Bỏ bớt mục cũ nhất
                sorted_k = sorted(_NGUOI_LA.keys(), key=lambda x: _NGUOI_LA[x]["lan_cuoi"])
                for old_k in sorted_k[:10]:
                    _NGUOI_LA.pop(old_k, None)
                    _DA_BAO_NGUOI_LA.discard(old_k)   # tập «đã báo» không được lớn hơn sổ người lạ

            _NGUOI_LA[k] = {
                "module": module_id,
                "nguoi_id": nguoi_gui,
                "ten_tu_khai": ten_tu_khai,
                "so_lan": 1,
                "lan_cuoi": now_iso,
            }

        if k not in _DA_BAO_NGUOI_LA:
            _DA_BAO_NGUOI_LA.add(k)
            now = time.monotonic()
            while _LAN_BAO_NGUOI_LA and now - _LAN_BAO_NGUOI_LA[0] > CUA_SO_BAO_NGUOI_LA_GIAY:
                _LAN_BAO_NGUOI_LA.popleft()
            if len(_LAN_BAO_NGUOI_LA) < TRAN_BAO_NGUOI_LA:   # vượt trần tổng: chỉ ghi nhận, không dội tin báo
                _LAN_BAO_NGUOI_LA.append(now)
                can_bao = True

    # Tuỳ chọn báo: mặc định bật (WEB2API_KENH_VAO_BAO_NGUOI_LA!=0)
    bat_bao = os.environ.get("WEB2API_KENH_VAO_BAO_NGUOI_LA", "1").strip().lower() not in ("0", "false", "off")
    if can_bao and bat_bao:
        try:
            so_tb = so_thiet_bi.doc_so()
            ch = so_tb.get("cau_hinh_bao", {})
            if ch.get("bat") and ch.get("dich"):
                ten_hien = ten_tu_khai if ten_tu_khai else "không tên"
                tin_bao = f"Người lạ ({ten_hien}) id {nguoi_gui} vừa nhắn tin qua {module_id}."
                for d in ch["dich"]:
                    mid = d.get("module")
                    kid = d.get("kenh_id")
                    if mid and kid:
                        try:
                            await gui_ra.gui_den_dich(
                                app,
                                mid,
                                kid,
                                {"van_ban": tin_bao},
                                nguoi_goi="kenh_vao",
                            )
                        except Exception as e:
                            log.warning("Gửi tin báo người lạ tới %s/%s thất bại: %s", mid, kid, type(e).__name__)
        except Exception as e:
            log.warning("Không thể đọc cấu hình báo để cảnh báo người lạ: %s", e)


# ───────────── Cổng nhận tin chính của lõi ─────────────

async def nhan_tin(app: Any, module_id: str, tin: dict) -> dict:
    """Cổng nhận tin chuẩn hoá từ module đẩy vào."""
    if not isinstance(tin, dict):
        return {"ok": False, "loi": "khuon_tin_sai"}

    id_tin = tin.get("id_tin")
    nguoi_gui = tin.get("nguoi_gui")
    noi_gui = tin.get("noi_gui")
    van_ban_tho = tin.get("van_ban")
    ten_tu_khai_tho = tin.get("ten_tu_khai")

    # 1. Kiểm tra khuôn bắt buộc
    if not isinstance(id_tin, str) or not id_tin.strip():
        return {"ok": False, "loi": "id_tin_sai"}
    if not isinstance(nguoi_gui, str) or not MAU_NGUOI_GUI.match(nguoi_gui):
        return {"ok": False, "loi": "nguoi_gui_sai"}
    if not isinstance(noi_gui, str) or not noi_gui.strip():
        return {"ok": False, "loi": "noi_gui_sai"}
    if not isinstance(van_ban_tho, str) or len(van_ban_tho) > TRAN_VAN_BAN:
        return {"ok": False, "loi": "van_ban_sai"}

    with _KHOA:
        _DEM["nhan"] += 1

    # 2. Chống trùng theo (module, id_tin) trong 10 phút
    if _kiem_chong_trung(module_id, id_tin):
        with _KHOA:
            _DEM["trung"] += 1
        return {"ok": True, "trung": True}

    # Làm sạch văn bản và tên
    van_ban = lam_sach_van_ban(van_ban_tho).strip()
    ten_tu_khai = lam_sach_ten(ten_tu_khai_tho)

    # 3. Phân quyền người gửi TRƯỚC hạn dòng (tra sổ rẻ): hạn dòng theo module chỉ tính tin của người ĐÃ được cấp quyền, nếu
    #    không kẻ lạ nhắn dồn làm cạn hạn dòng và lệnh của chính chủ máy bị từ chối.
    try:
        vai, _ = tra_vai(module_id, nguoi_gui)
    except KhoHong:
        log.error("Kho dữ liệu kenh_vao.json bị hỏng -> DỪNG xử lý tin")
        return {"ok": False, "loi": "kho_hong"}

    cap_bac = cap_bac_vai(vai)

    # 4. Người lạ: không chạy lệnh, không trả lời, ghi nhận (ngân sách riêng + bộ nhớ có trần)
    if cap_bac == 0:
        with _KHOA:
            _DEM["tu_choi"] += 1
        if not _cho_phep_xu_ly_nguoi_la(module_id):
            return {"ok": True, "vai": "la", "bo_qua": True, "han_dong": True}
        _ghi_nhat_ky_kenh_vao(module_id, nguoi_gui, "(khong)", "la", "tu_choi_nguoi_la")
        await _ghi_nhan_va_bao_nguoi_la(app, module_id, nguoi_gui, noi_gui, ten_tu_khai)
        return {"ok": True, "vai": "la", "bo_qua": True}

    # 5. Hạn dòng theo (module, nguoi_gui) và theo module — chỉ cho người đã được cấp quyền
    if not _kiem_va_cap_nhat_han_dong(module_id, nguoi_gui):
        with _KHOA:
            _DEM["vuot_han"] += 1
        log.warning("Kênh vào %s: vượt hạn dòng từ %s", module_id, nguoi_gui)
        return {"ok": False, "loi": "vuot_han_dong"}

    if not van_ban:
        return {"ok": True, "van_ban_rong": True}

    # 6. Phân tích lệnh theo CẤU TRÚC
    parts = van_ban.split(None, 1)
    tok0 = parts[0]
    doi_so = parts[1].strip() if len(parts) > 1 else ""

    # Chuẩn hoá token đầu: bỏ '/', bỏ '@bot', bỏ dấu tiếng Việt, chữ thường
    if tok0.startswith("/"):
        tok0 = tok0[1:]
    if "@" in tok0:
        tok0 = tok0.split("@", 1)[0]
    ten_lenh = bo_dau(tok0).strip()

    # 7. Tra bảng lệnh
    with _KHOA:
        lenh_info = _BANG_LENH.get(ten_lenh)

    # Lệnh lạ
    if lenh_info is None:
        # Trả lời trợ giúp ngắn cho vai >= chi_xem
        ds_kha_dung = [
            l for l in danh_sach_lenh()
            if cap_bac >= cap_bac_vai(l["vai_toi_thieu"])
        ]
        if ds_kha_dung:
            dong = ["Lệnh không hợp lệ. Các lệnh bạn có thể dùng:"]
            for l in ds_kha_dung:
                dong.append(f"• {l['ten']}: {l['mo_ta']}")
            tra_loi = "\n".join(dong)
        else:
            tra_loi = "Lệnh không hợp lệ. Bạn chưa có lệnh khả dụng nào."

        await _gui_tra_loi(app, module_id, noi_gui, tra_loi)
        _ghi_nhat_ky_kenh_vao(module_id, nguoi_gui, ten_lenh or "(la)", vai, "lenh_la")
        return {"ok": True, "lenh": ten_lenh, "tra_loi": tra_loi}

    # Kiểm quyền gọi lệnh
    vai_toi_thieu = lenh_info["vai_toi_thieu"]
    cap_bac_can = cap_bac_vai(vai_toi_thieu)
    if cap_bac < cap_bac_can:
        with _KHOA:
            _DEM["tu_choi"] += 1
        tra_loi = "Bạn không có quyền dùng lệnh này."
        await _gui_tra_loi(app, module_id, noi_gui, tra_loi)
        _ghi_nhat_ky_kenh_vao(module_id, nguoi_gui, ten_lenh, vai, "khong_du_quyen")
        return {"ok": False, "loi": "khong_du_quyen"}

    # Chạy hàm xử lý
    ngu_canh = {
        "module": module_id,
        "nguoi_gui": nguoi_gui,
        "vai": vai,
        "noi_gui": noi_gui,
        "lenh": ten_lenh,
        "app": app,
    }

    try:
        kq_str = await lenh_info["ham"](ngu_canh, doi_so)
        if not isinstance(kq_str, str):
            kq_str = str(kq_str or "")
        tra_loi = kq_str[:TRAN_TRA_LOI]
        ket_qua_log = "ok"
        with _KHOA:
            _DEM["lenh_chay"] += 1
    except Exception as e:
        log.exception("Hàm lệnh %s ném ngoại lệ: %s", ten_lenh, type(e).__name__)
        tra_loi = "Lệnh lỗi."
        ket_qua_log = "loi_lenh"

    # Trả lời về đúng noi_gui
    await _gui_tra_loi(app, module_id, noi_gui, tra_loi)
    _ghi_nhat_ky_kenh_vao(module_id, nguoi_gui, ten_lenh, vai, ket_qua_log)
    return {"ok": (ket_qua_log == "ok"), "lenh": ten_lenh, "tra_loi": tra_loi}


# ───────────── Quản lý người lạ & trạng thái ─────────────

async def lay_danh_sach_nguoi_la() -> list[dict]:
    """Lấy danh sách người lạ gần đây (quản trị mới thấy id đầy đủ)."""
    with _KHOA:
        return sorted(_NGUOI_LA.values(), key=lambda x: x["lan_cuoi"], reverse=True)


async def lay_trang_thai() -> dict:
    """Lấy số liệu thống kê: nhận, trùng, vượt hạn, bị từ chối, lệnh chạy, lỗi kho."""
    loi_kho = False
    try:
        doc_so()
    except KhoHong:
        loi_kho = True

    with _KHOA:
        return {
            "nhan": _DEM["nhan"],
            "trung": _DEM["trung"],
            "vuot_han": _DEM["vuot_han"],
            "tu_choi": _DEM["tu_choi"],
            "lenh_chay": _DEM["lenh_chay"],
            "loi_kho": loi_kho,
        }


# ───────────── Vòng hỏi vòng (Polling loop) ─────────────

class VongHoiVong:
    """Vòng lặp hỏi vòng (kéo tin theo chu kỳ) do lõi giữ.
    - Nghỉ theo chu kỳ khai trong manifest (chu_ky_giay kẹp 2..300).
    - Lùi dần khi lỗi (tối đa 5 phút = 300s). Reset khi thành công.
    - Dừng khi gỡ module.
    - Không chạy trùng (single-flight per module).
    - Mỗi module hết giờ riêng.
    - Một module hỏng không làm hỏng module khác.
    - Đẩy từng tin nhận được qua nhan_tin.
    - Tiêm được hàm ngủ để test không cần ngủ thật.
    """

    def __init__(self, lay_app: Callable[[], Any], *, ngu: Callable[[float], Any] | None = None):
        self._lay_app = lay_app
        self._ngu = ngu or asyncio.sleep
        self._dung_co = False
        self._task_dieu_phoi: asyncio.Task | None = None
        self._tasks_module: dict[str, asyncio.Task] = {}
        self._dang_chay: set[str] = set()
        self._lui_loi: dict[str, float] = {}

    def bat_dau(self) -> None:
        """Khởi động tác vụ điều phối trong vòng sự kiện đang chạy (idempotent)."""
        if self._task_dieu_phoi is not None and not self._task_dieu_phoi.done():
            return
        self._dung_co = False
        self._task_dieu_phoi = asyncio.get_running_loop().create_task(self._vong_dieu_phoi())

    async def dung(self) -> None:
        """Dừng mọi vòng lặp hỏi vòng (idempotent)."""
        self._dung_co = True
        for mid, t in list(self._tasks_module.items()):
            t.cancel()
        if self._tasks_module:
            await asyncio.gather(*self._tasks_module.values(), return_exceptions=True)
        self._tasks_module.clear()

        if self._task_dieu_phoi is not None:
            self._task_dieu_phoi.cancel()
            try:
                await self._task_dieu_phoi
            except asyncio.CancelledError:
                pass
            self._task_dieu_phoi = None

    async def chay_mot_luot_module(self, module_id: str, mod: Any, chu_ky_giay: float) -> float:
        """Thực hiện một lượt hỏi vòng cho một module. Trả về thời gian delay cho lượt tiếp theo."""
        if module_id in self._dang_chay:
            return chu_ky_giay

        self._dang_chay.add(module_id)
        app_obj = self._lay_app()
        try:
            # Hết giờ riêng cho mỗi module
            timeout = min(max(chu_ky_giay * 2, 30.0), 60.0)
            tin_ds = await asyncio.wait_for(mod.hoi_vong(), timeout=timeout)
            if isinstance(tin_ds, list):
                for tin in tin_ds:
                    if isinstance(tin, dict):
                        try:
                            await nhan_tin(app_obj, module_id, tin)
                        except Exception as e:
                            log.warning("Xử lý tin hỏi vòng từ %s ném: %s", module_id, type(e).__name__)
            # Thành công: khôi phục chu kỳ gốc
            self._lui_loi[module_id] = chu_ky_giay
            return chu_ky_giay
        except Exception as e:
            current = self._lui_loi.get(module_id, chu_ky_giay)
            new_delay = min(max(current * 2, chu_ky_giay * 2), 300.0)  # Tối đa 5 phút
            self._lui_loi[module_id] = new_delay
            log.warning("Hỏi vòng module %s lỗi: %s (lùi %ss)", module_id, type(e).__name__, new_delay)
            return new_delay
        finally:
            self._dang_chay.discard(module_id)

    async def _vong_module(self, module_id: str, chu_ky_giay: float) -> None:
        """Vòng lặp hỏi vòng cho riêng một module."""
        try:
            while not self._dung_co:
                app_obj = self._lay_app()
                so = getattr(getattr(app_obj, "state", None), "so", None) or getattr(app_obj, "so", None)
                if so is None:
                    break
                mod = so.dang_chay.get(module_id)
                if mod is None or not callable(getattr(mod, "hoi_vong", None)):
                    # Dừng khi gỡ module
                    break

                delay = await self.chay_mot_luot_module(module_id, mod, chu_ky_giay)
                await self._ngu(delay)
        except asyncio.CancelledError:
            pass
        finally:
            self._tasks_module.pop(module_id, None)

    async def _vong_dieu_phoi(self) -> None:
        """Tác vụ quét các module đang chạy để khởi tạo/dọn dẹp vòng lặp hỏi vòng từng module."""
        try:
            while not self._dung_co:
                app_obj = self._lay_app()
                so = getattr(getattr(app_obj, "state", None), "so", None) or getattr(app_obj, "so", None)
                if so is not None:
                    dang_chay = dict(so.dang_chay)
                    for mid, mod in dang_chay.items():
                        if not callable(getattr(mod, "hoi_vong", None)):
                            continue
                        # Lấy chu kỳ từ manifest
                        manifest = None
                        if mid in getattr(so, "catalog", {}):
                            manifest = getattr(so.catalog[mid], "manifest", None)
                        if manifest is None and hasattr(mod, "manifest"):
                            manifest = mod.manifest

                        hv = manifest.get("hoi_vong") if isinstance(manifest, dict) else None
                        if not isinstance(hv, dict) or "chu_ky_giay" not in hv:
                            continue

                        ck = hv["chu_ky_giay"]
                        if not (isinstance(ck, (int, float)) and not isinstance(ck, bool) and 2 <= ck <= 300):
                            continue

                        if mid not in self._tasks_module or self._tasks_module[mid].done():
                            t = asyncio.get_running_loop().create_task(self._vong_module(mid, float(ck)))
                            self._tasks_module[mid] = t

                await self._ngu(2.0)
        except asyncio.CancelledError:
            pass


# ───────────── Tuyến API quản trị (/api/kenh-vao/*) ─────────────

@router.get("/nguoi")
async def api_lay_danh_sach_nguoi():
    """Xem danh sách người trong sổ phân quyền."""
    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu phân quyền bị hỏng.", code="kho_loi") from e
    return so.get("nguoi", [])


@router.put("/nguoi")
async def api_cap_nhat_nguoi(request: Request):
    """Thêm hoặc sửa người trong sổ phân quyền."""
    try:
        body = await request.json()
    except Exception:
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON object.", code="invalid_request")

    if not isinstance(body, dict):
        raise LoiOpenAI(400, "Thân yêu cầu phải là JSON object.", code="invalid_request")

    mid = body.get("module")
    nid = body.get("nguoi_id")
    vai = body.get("vai")
    ten = body.get("ten") or ""

    if not isinstance(mid, str) or not MAU_ID_HOP_LE.match(mid):
        raise LoiOpenAI(400, "Trường «module» không hợp lệ.", code="invalid_request")
    if not isinstance(nid, str) or not MAU_NGUOI_GUI.match(nid):
        raise LoiOpenAI(400, "Trường «nguoi_id» không hợp lệ.", code="invalid_request")
    if not isinstance(vai, str) or not MAU_VAI.match(vai):
        raise LoiOpenAI(400, "Trường «vai» không hợp lệ (khuôn [a-z0-9_]{1,32}).", code="invalid_request")

    # Kiểm module tồn tại trong catalog hoặc dang_chay và có khả năng kenh_ra
    so = getattr(getattr(request.app, "state", None), "so", None) or getattr(request.app, "so", None)
    if so is not None:
        co_mod = (mid in so.dang_chay) or (mid in getattr(so, "catalog", {}))
        if not co_mod:
            raise LoiOpenAI(404, f"Không tìm thấy module «{mid}».", code="module_khong_co")
        # Kiểm tra kenh_ra
        mod_obj = so.dang_chay.get(mid)
        manifest = getattr(so.catalog.get(mid), "manifest", {}) if hasattr(so, "catalog") else {}
        cung_cap = manifest.get("cung_cap", []) if isinstance(manifest, dict) else []
        has_kenh_ra = "kenh_ra" in cung_cap or (mod_obj is not None and callable(getattr(mod_obj, "kenh_ra", None)))
        if not has_kenh_ra:
            raise LoiOpenAI(400, f"Module «{mid}» không hỗ trợ gửi ra (kenh_ra).", code="khong_co_kenh_ra")

    try:
        muc = await cap_quyen(mid, nid, vai, ten)
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu phân quyền bị hỏng.", code="kho_loi") from e

    return muc


@router.delete("/nguoi/{module}/{nguoi_id:path}")
async def api_xoa_nguoi(module: str, nguoi_id: str):
    """Xoá một người khỏi sổ phân quyền."""
    # Kiểm khuôn chặt chống path traversal
    if not MAU_ID_HOP_LE.match(module) or not MAU_NGUOI_GUI.match(nguoi_id):
        raise LoiOpenAI(400, "Định danh không hợp lệ.", code="invalid_id")
    if ".." in module or ".." in nguoi_id or "/" in nguoi_id or "\\" in nguoi_id:
        raise LoiOpenAI(400, "Định danh không hợp lệ.", code="invalid_id")

    try:
        so = doc_so()
    except KhoHong as e:
        raise LoiOpenAI(503, "Kho dữ liệu phân quyền bị hỏng.", code="kho_loi") from e

    ds = so.get("nguoi", [])
    truoc = len(ds)
    so["nguoi"] = [x for x in ds if not (x.get("module") == module and x.get("nguoi_id") == nguoi_id)]

    if len(so["nguoi"]) == truoc:
        raise LoiOpenAI(404, "Không tìm thấy người này trong sổ.", code="nguoi_khong_co")

    ghi_so(so)
    return {"ok": True}


@router.get("/la")
async def api_lay_nguoi_la():
    """Xem danh sách người lạ gần đây (quản trị mới thấy id đầy đủ)."""
    return await lay_danh_sach_nguoi_la()


@router.get("/lenh")
async def api_lay_danh_sach_lenh():
    """Xem danh sách lệnh đã đăng ký kèm vai tối thiểu."""
    return danh_sach_lenh()


@router.get("/trang-thai")
async def api_lay_trang_thai():
    """Xem thống kê: nhận, trùng, vượt hạn, bị từ chối, lệnh chạy, lỗi kho."""
    return await lay_trang_thai()
