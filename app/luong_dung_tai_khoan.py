"""Sổ lượng dùng + hạn mức THỤ ĐỘNG theo tài khoản AI (NV197d).

- Ghi ở MỘT điểm chung (app/chuyen_tiep.py::_ghi_so_lieu), không chép vào từng module.
- Sổ bền: tệp JSON trong thư mục dữ liệu (không phải kho bí mật — số liệu dùng
  không phải bí mật), ghi nguyên tử, có trần kích thước + dọn dữ liệu cũ.
- Lượng dùng theo ngày: số lượt, token vào/ra (nếu phản hồi có), số lỗi theo LOẠI
  phân loại theo MÃ HTTP (429→han_muc, 401/403→het_phien, còn lại→khac) — KHÔNG
  dò chuỗi lỗi của từng nhà cung cấp. Không ghi nội dung hội thoại, không ghi bí mật.
- Hạn mức CHỈ THỤ ĐỘNG: đọc tiêu đề phản hồi nhà cung cấp tự gửi
  (x-ratelimit-*, retry-after, x-codex-* như c2a ghi_nhan_tu_header); KHÔNG tự
  gọi API hạn mức theo lịch. Không bịa số: không có tiêu đề → «khong_biet».
- Hết hạn mức → đánh dấu «het_han_muc_den» để lõi bỏ qua đúng đến lúc đặt lại
  (app/kho_tai_khoan.thu_dang_nhap_lai kiểm qua con_het_han_muc).

Không bao giờ ném ra ngoài từ ghi_luot — đường chat thật không được hỏng vì sổ.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app import kho as kho_mod

LOG = logging.getLogger("luong_dung_tai_khoan")

_KHOA = threading.RLock()
_TEN_TEP = "luong_dung_tai_khoan.json"

_TRAN_NGAY = 90            # giữ tối đa 90 ngày/tài khoản
_TRAN_TAI_KHOAN = 500     # tối đa 500 tài khoản trong sổ
_RETRY_FALLBACK_GIAY = 3600  # 429 không có retry-after → đánh dấu 1 giờ

LOAI_LOI_HAN_MUC = "han_muc"
LOAI_LOI_HET_PHIEN = "het_phien"
LOAI_LOI_KHAC = "khac"

# Thuộc tính thể hiện để test thay đồng hồ mà không phải ngủ thật.
_dong_ho = time.time


def _duong() -> object:
    return kho_mod.thu_muc_du_lieu() / _TEN_TEP


def phan_loai_loi(ma: int) -> str:
    """Phân loại lỗi theo MÃ HTTP — cấm dò chữ (luật chung)."""
    if ma == 429:
        return LOAI_LOI_HAN_MUC
    if ma in (401, 403):
        return LOAI_LOI_HET_PHIEN
    return LOAI_LOI_KHAC


def _so(gia_tri) -> float | None:
    try:
        so = float(gia_tri)
    except (TypeError, ValueError):
        return None
    return so if so == so and abs(so) != float("inf") else None  # loại NaN/inf


def _nguyen(gia_tri) -> int | None:
    so = _so(gia_tri)
    return None if so is None else int(so)


def doc_han_muc_tu_header(headers) -> dict | None:
    """Đọc thụ động tiêu đề hạn mức nhà cung cấp tự gửi.

    Trả None khi không có tiêu đề nào có cấu trúc dùng được — KHÔNG bịa số.
    Chỉ chấp nhận giá trị SỐ (không dò chữ, không suy luận theo tên).
    """
    if not headers:
        return None
    try:
        h = {str(k).lower(): v for k, v in dict(headers).items()}
    except (TypeError, ValueError):
        return None
    ra: dict = {}
    # Họ x-ratelimit-* chuẩn.
    for nhan, khoa in (("x-ratelimit-limit", "gioi_han"),
                       ("x-ratelimit-limit-tokens", "gioi_han_token"),
                       ("x-ratelimit-remaining", "con_lai"),
                       ("x-ratelimit-remaining-tokens", "con_lai_token"),
                       ("x-ratelimit-reset", "dat_lai_luc"),
                       ("x-ratelimit-reset-tokens", "dat_lai_luc_token")):
        so = _nguyen(h.get(nhan))
        if so is not None:
            ra[khoa] = so
    # retry-after: số giây từ bây giờ.
    retry = _nguyen(h.get("retry-after"))
    if retry is not None and retry >= 0:
        ra["dat_lai_sau_giay"] = retry
    # Họ x-codex-* (cấu trúc như c2a doc_header): cửa sổ primary/secondary.
    for vai in ("primary", "secondary"):
        pct = _so(h.get(f"x-codex-{vai}-used-percent"))
        if pct is not None and 0 <= pct <= 100:
            cua_so = {"da_dung_pct": pct}
            phut = _nguyen(h.get(f"x-codex-{vai}-window-minutes"))
            if phut is not None:
                cua_so["cua_so_phut"] = phut
            reset_at = _nguyen(h.get(f"x-codex-{vai}-reset-at"))
            if reset_at is not None:
                cua_so["dat_lai_luc"] = reset_at
            sau_giay = _nguyen(h.get(f"x-codex-{vai}-reset-after-seconds"))
            if sau_giay is not None:
                cua_so["dat_lai_sau_giay"] = sau_giay
            ra[f"codex_{vai}"] = cua_so
    if not ra:
        return None
    ra["nguon"] = "header"
    ra["luc_doc"] = int(_dong_ho())
    return ra


def _kiem_id(tai_khoan_id) -> str | None:
    if not isinstance(tai_khoan_id, str):
        return None
    id_ = tai_khoan_id.strip()
    return id_ if id_ and len(id_) <= 128 else None


def _doc() -> dict:
    try:
        du_lieu = kho_mod.doc_json(_duong())
    except Exception as e:  # kho hỏng → coi như sổ trống, không làm hỏng đường gọi
        LOG.warning("sổ lượng dùng đọc hỏng: %s", e)
        return {}
    return du_lieu if isinstance(du_lieu, dict) else {}


def _ghi(du_lieu: dict) -> None:
    # Dữ liệu dùng không bí mật → quyền đọc chung; ghi nguyên tử.
    import json
    kho_mod.ghi_nguyen_tu(_duong(), json.dumps(du_lieu, ensure_ascii=False), 0o644)


def _don(du_lieu: dict) -> None:
    """Trần kích thước: ≤90 ngày/tài khoản, ≤500 tài khoản (dọn cái cũ nhất)."""
    ds = du_lieu.get("tai_khoan")
    if not isinstance(ds, dict):
        return
    for id_, ban in list(ds.items()):
        if not isinstance(ban, dict):
            del ds[id_]
            continue
        ngay = ban.get("ngay")
        if isinstance(ngay, dict) and len(ngay) > _TRAN_NGAY:
            for k in sorted(ngay)[:-_TRAN_NGAY]:
                del ngay[k]
    if len(ds) > _TRAN_TAI_KHOAN:
        xep = sorted(ds.items(), key=lambda kv: kv[1].get("thay_lan_cuoi", 0) if isinstance(kv[1], dict) else 0)
        for id_, _ban in xep[:len(ds) - _TRAN_TAI_KHOAN]:
            del ds[id_]


def ghi_luot(tai_khoan_id: str, *, viec: str = "chat", ma: int = 200,
             token_vao: int = 0, token_ra: int = 0, headers=None) -> bool:
    """Ghi một lượt gọi vào sổ. KHÔNG BAO GIỜ ném — trả False khi bỏ qua."""
    id_ = _kiem_id(tai_khoan_id)
    if id_ is None:
        return False
    try:
        token_vao = max(0, int(token_vao or 0))
        token_ra = max(0, int(token_ra or 0))
    except (TypeError, ValueError):
        token_vao = token_ra = 0
    try:
        with _KHOA:
            bay_gio = int(_dong_ho())
            du_lieu = _doc()
            ds = du_lieu.setdefault("tai_khoan", {})
            ban = ds.setdefault(id_, {"ngay": {}, "thay_lan_cuoi": 0})
            ngay_str = datetime.fromtimestamp(bay_gio, tz=timezone.utc).strftime("%Y-%m-%d")
            o_ngay = ban["ngay"].setdefault(
                ngay_str, {"luot": 0, "token_vao": 0, "token_ra": 0,
                           "loi_han_muc": 0, "loi_het_phien": 0, "loi_khac": 0})
            if 200 <= ma < 300:
                o_ngay["luot"] += 1
                o_ngay["token_vao"] += token_vao
                o_ngay["token_ra"] += token_ra
                han_muc = doc_han_muc_tu_header(headers)
                if han_muc:
                    ban["han_muc"] = han_muc
            else:
                loai = phan_loai_loi(ma)
                o_ngay["loi_han_muc"] += (loai == LOAI_LOI_HAN_MUC)
                o_ngay["loi_het_phien"] += (loai == LOAI_LOI_HET_PHIEN)
                o_ngay["loi_khac"] += (loai == LOAI_LOI_KHAC)
                if loai == LOAI_LOI_HAN_MUC:
                    # Hết hạn mức → đánh dấu tài khoản bỏ qua tới lúc đặt lại (item 4 spec).
                    han_muc = doc_han_muc_tu_header(headers)
                    sau_giay = None
                    if han_muc:
                        sau_giay = han_muc.get("dat_lai_sau_giay")
                    if sau_giay is None:
                        sau_giay = _RETRY_FALLBACK_GIAY
                    ban["het_han_muc_den"] = bay_gio + max(0, int(sau_giay))
                    if han_muc:
                        ban["han_muc"] = han_muc
            ban["thay_lan_cuoi"] = bay_gio
            _don(du_lieu)
            _ghi(du_lieu)
        return True
    except Exception as e:
        LOG.warning("ghi lượt dùng hỏng (bỏ qua, không chặn đường gọi): %s", e)
        return False


def _tong_ngay(ds_ngay: dict, tu: str | None, den: str | None) -> tuple[list[dict], dict]:
    hang = []
    tong = {"luot": 0, "token_vao": 0, "token_ra": 0,
            "loi_han_muc": 0, "loi_het_phien": 0, "loi_khac": 0}
    for ngay in sorted(ds_ngay):
        if tu and ngay < tu:
            continue
        if den and ngay > den:
            continue
        o = ds_ngay[ngay]
        if not isinstance(o, dict):
            continue
        dong = {"ngay": ngay}
        for k in tong:
            v = o.get(k, 0)
            dong[k] = v if isinstance(v, int) else 0
            tong[k] += dong[k]
        hang.append(dong)
    return hang, tong


def tom_tat_moi_tai_khoan() -> dict:
    """Tóm tắt dung/hạn mức của MỌI tài khoản có số liệu (cho đường tổng quan)."""
    with _KHOA:
        ds = _doc().get("tai_khoan") or {}
        ids = [id_ for id_ in ds if isinstance(id_, str)]
    return {id_: tom_tat(id_) for id_ in ids}


def thong_ke(tai_khoan_id: str, *, tu_ngay: str | None = None,
             den_ngay: str | None = None) -> dict | None:
    """Thống kê lượng dùng theo ngày của một tài khoản. None nếu id lạ/không có số liệu."""
    id_ = _kiem_id(tai_khoan_id)
    if id_ is None:
        return None
    with _KHOA:
        ban = (_doc().get("tai_khoan") or {}).get(id_)
    if not isinstance(ban, dict):
        return None
    ngay = ban.get("ngay")
    if not isinstance(ngay, dict) or not ngay:
        return None
    hang, tong = _tong_ngay(ngay, tu_ngay, den_ngay)
    return {"tai_khoan_id": id_, "ngay": hang, "tong": tong}


def lay_han_muc(tai_khoan_id: str) -> dict | None:
    """Hạn mức thụ động đã đọc được; None = chưa biết (không bịa)."""
    id_ = _kiem_id(tai_khoan_id)
    if id_ is None:
        return None
    with _KHOA:
        ban = (_doc().get("tai_khoan") or {}).get(id_)
    if not isinstance(ban, dict):
        return None
    han_muc = ban.get("han_muc")
    return dict(han_muc) if isinstance(han_muc, dict) else None


def danh_dau_het_han_muc(tai_khoan_id: str, den_luc: int) -> bool:
    """Đánh dấu tài khoản hết hạn mức tới `den_luc` (epoch). Trả False khi id lạ."""
    id_ = _kiem_id(tai_khoan_id)
    if id_ is None:
        return False
    try:
        with _KHOA:
            du_lieu = _doc()
            ban = (du_lieu.setdefault("tai_khoan", {})).setdefault(
                id_, {"ngay": {}, "thay_lan_cuoi": 0})
            ban["het_han_muc_den"] = int(den_luc)
            ban["thay_lan_cuoi"] = int(_dong_ho())
            _don(du_lieu)
            _ghi(du_lieu)
        return True
    except Exception as e:
        LOG.warning("đánh dấu hết hạn mức hỏng: %s", e)
        return False


def con_het_han_muc(tai_khoan_id: str) -> int:
    """Epoch «hết hạn mức tới» nếu còn hiệu lực, 0 nếu hết/không có."""
    id_ = _kiem_id(tai_khoan_id)
    if id_ is None:
        return 0
    with _KHOA:
        ban = (_doc().get("tai_khoan") or {}).get(id_)
    if not isinstance(ban, dict):
        return 0
    try:
        den = int(ban.get("het_han_muc_den", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return den if den > _dong_ho() else 0


def tom_tat(tai_khoan_id: str) -> dict:
    """Tóm tắt cho ban_cong_khai: dung (hôm nay/7 ngày) + han_muc (hoặc «khong_biet»)."""
    id_ = _kiem_id(tai_khoan_id)
    hom_nay = bay_ngay = 0
    if id_ is not None:
        try:
            bay_gio = datetime.fromtimestamp(int(_dong_ho()), tz=timezone.utc)
            hom_nay_str = bay_gio.strftime("%Y-%m-%d")
            muc_7 = (bay_gio - timedelta(days=6)).strftime("%Y-%m-%d")
            tk = thong_ke(id_, tu_ngay=muc_7, den_ngay=hom_nay_str)
            if tk:
                bay_ngay = tk["tong"]["luot"]
                for dong in tk["ngay"]:
                    if dong["ngay"] == hom_nay_str:
                        hom_nay = dong["luot"]
        except Exception as e:
            LOG.warning("tóm tắt lượng dùng hỏng: %s", e)
    han_muc = lay_han_muc(id_) if id_ else None
    ra: dict = {"dung": {"hom_nay": hom_nay, "7_ngay": bay_ngay},
                "han_muc": han_muc if han_muc else "khong_biet"}
    den = con_het_han_muc(id_) if id_ else 0
    if den:
        ra["het_han_muc_den"] = den
    return ra
