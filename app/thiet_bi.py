"""Chuẩn THIẾT BỊ / ỨNG DỤNG của lõi (thiet_bi): thực thể, hành động, sự kiện, hàng chờ phê duyệt.

Nguyên tắc:
- Lõi KHÔNG chứa tên dịch vụ hay thiết bị cụ thể nào; mọi thứ là mã mở.
- Định tuyến theo tiền tố: id thực thể bắt đầu bằng "<tien_to>." → module có tiền tố đó.
- Ghi đè mức độ rủi ro lưu tại <DATA>/thiet_bi.json (0600).
- Hàng chờ phê duyệt cho hành động nguy hiểm lưu tại <DATA>/phe_duyet.json (0600), tự huỷ khi quá hạn.
- Sự kiện hỗ trợ SSE một chiều cho quản trị và khoá khách có phạm vi thiet_bi:su_kien.
- Phạm vi sự kiện đã TÁCH (NV29/SEC-003): `thiet_bi:su_kien` = quyền GỬI sự kiện (POST /api/thiet-bi/su-kien),
  `thiet_bi:su_kien_doc` = quyền NGHE (GET /api/thiet-bi/su-kien). Tương thích: khoá có `*` hoặc
  `thiet_bi:su_kien` vẫn nghe được.
- Lõi đóng dấu trường `nguon` (danh tính người gửi) vào mọi sự kiện; người gửi không đặt được.
  Sự kiện loại phê duyệt (`phe_duyet*`) chỉ phát tới quản trị.
- Sổ nhật ký kênh `thiet_bi`: ghi nhận hành động, phê duyệt, từ chối; KHÔNG ghi khoá hay tham số bí mật.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import kho, nhat_ky
from app.loi_openai import LoiOpenAI
from app.so_module import SoModule

log = logging.getLogger("web2api.thiet_bi")

_KHOA_LUONG = threading.RLock()
RUI_RO_HOP_LE = {"doc", "ghi", "nguy_hiem"}


def _luu_json(p: Path, du_lieu: Any, quyen: int = 0o600) -> None:
    kho.ghi_nguyen_tu(p, json.dumps(du_lieu, ensure_ascii=False, indent=2), quyen=quyen)



def bo_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt và chuyển chữ thường để tìm kiếm linh hoạt."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFD", s)
    khong_dau = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return khong_dau.replace("đ", "d").replace("Đ", "d").lower()


def lay_luoc_do_tham_so(tinh_nang: Any, hanh_dong: str) -> dict | None:
    """Trích xuất lược đồ tham số của hành động từ danh sách tinh_nang của thực thể."""
    if not isinstance(tinh_nang, (list, tuple)):
        return None
    for tn in tinh_nang:
        if isinstance(tn, dict):
            ten_hd = str(tn.get("hanh_dong") or tn.get("ten") or tn.get("name") or "").strip()
            if ten_hd == hanh_dong:
                ld = tn.get("tham_so") or tn.get("parameters") or tn.get("luoc_do") or tn.get("schema")
                if isinstance(ld, dict):
                    return ld
    return None


def lam_sach_tham_so(ts: dict, luoc_do_tham_so: dict | None = None) -> dict:
    """Làm sạch tham số theo NGUYÊN TẮC (SEC-004b):
    - Tham số khai «bi_mat: true» trong lược đồ -> che thành «…che».
    - Tham số KHÔNG khai trong lược đồ (hoặc khi không có lược đồ) -> chỉ ghi tên + độ dài, không ghi giá trị.
    - Tham số khai bình thường (không bí mật) -> giữ nguyên giá trị.
    """
    if not isinstance(ts, dict):
        return {}
    props = luoc_do_tham_so.get("properties", {}) if isinstance(luoc_do_tham_so, dict) else {}
    sach = {}
    for k, v in ts.items():
        if props and k in props:
            p_spec = props[k]
            if isinstance(p_spec, dict) and p_spec.get("bi_mat") is True:
                sach[k] = "…che"
            elif isinstance(v, dict):
                sub_luoc_do = p_spec.get("properties") if isinstance(p_spec, dict) else None
                sach[k] = lam_sach_tham_so(v, sub_luoc_do)
            else:
                sach[k] = v
        else:
            # Tham số KHÔNG khai trong lược đồ: chỉ ghi tên + độ dài, không ghi giá trị
            do_dai = len(str(v))
            sach[k] = f"…che(độ dài {do_dai})"
    return sach


def _kiem_tra_non_finite(val: Any, ten: str = "") -> None:
    if isinstance(val, float) and not math.isfinite(val):
        raise LoiOpenAI(400, f"Tham số «{ten or 'số'}» không được là NaN hoặc Infinity", code="invalid_request")
    if isinstance(val, dict):
        for k, v in val.items():
            _kiem_tra_non_finite(v, f"{ten}.{k}" if ten else k)
    elif isinstance(val, (list, tuple)):
        for i, item in enumerate(val):
            _kiem_tra_non_finite(item, f"{ten}[{i}]")


def _kiem_tra_gia_tri_theo_luoc_do(val: Any, spec: dict, ten: str, do_sau: int = 0) -> None:
    if do_sau > 6:
        raise LoiOpenAI(400, f"Độ sâu tham số «{ten}» vượt quá mức cho phép (tối đa 6 tầng)", code="invalid_request")
    if not isinstance(spec, dict):
        return

    # 1. Kiểm tra type (hỗ trợ cả str lẫn list[str])
    p_type = spec.get("type")
    if p_type:
        danh_sach_type = p_type if isinstance(p_type, (list, tuple)) else [p_type]
        hop_le = False
        for t in danh_sach_type:
            if t == "string" and isinstance(val, str):
                hop_le = True
                break
            elif t == "integer" and isinstance(val, int) and not isinstance(val, bool):
                hop_le = True
                break
            elif t == "number" and isinstance(val, (int, float)) and not isinstance(val, bool):
                if math.isfinite(val):
                    hop_le = True
                    break
            elif t == "boolean" and isinstance(val, bool):
                hop_le = True
                break
            elif t == "null" and val is None:
                hop_le = True
                break
            elif t == "array" and isinstance(val, (list, tuple)):
                hop_le = True
                break
            elif t == "object" and isinstance(val, dict):
                hop_le = True
                break
        if not hop_le:
            raise LoiOpenAI(400, f"Tham số «{ten}» không đúng kiểu (mong muốn {p_type!r}, nhận {type(val).__name__})", code="invalid_request")

    # 2. Số: bắt buộc math.isfinite, minimum, maximum, exclusiveMinimum, exclusiveMaximum
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if not math.isfinite(val):
            raise LoiOpenAI(400, f"Tham số «{ten}» không được là NaN hoặc Infinity", code="invalid_request")
        if "minimum" in spec and val < spec["minimum"]:
            raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) nhỏ hơn mức tối thiểu ({spec['minimum']})", code="invalid_request")
        if "maximum" in spec and val > spec["maximum"]:
            raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) vượt quá mức tối đa ({spec['maximum']})", code="invalid_request")
        if "exclusiveMinimum" in spec:
            ex_min = spec["exclusiveMinimum"]
            if isinstance(ex_min, bool):
                if ex_min and "minimum" in spec and val <= spec["minimum"]:
                    raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) phải lớn hơn ({spec['minimum']})", code="invalid_request")
            elif isinstance(ex_min, (int, float)) and val <= ex_min:
                raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) phải lớn hơn ({ex_min})", code="invalid_request")
        if "exclusiveMaximum" in spec:
            ex_max = spec["exclusiveMaximum"]
            if isinstance(ex_max, bool):
                if ex_max and "maximum" in spec and val >= spec["maximum"]:
                    raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) phải nhỏ hơn ({spec['maximum']})", code="invalid_request")
            elif isinstance(ex_max, (int, float)) and val >= ex_max:
                raise LoiOpenAI(400, f"Tham số «{ten}» ({val}) phải nhỏ hơn ({ex_max})", code="invalid_request")

    # 3. Chuỗi: minLength, maxLength
    if isinstance(val, str):
        if "minLength" in spec and len(val) < spec["minLength"]:
            raise LoiOpenAI(400, f"Độ dài tham số «{ten}» nhỏ hơn mức tối thiểu ({spec['minLength']})", code="invalid_request")
        if "maxLength" in spec and len(val) > spec["maxLength"]:
            raise LoiOpenAI(400, f"Độ dài tham số «{ten}» vượt quá mức tối đa ({spec['maxLength']})", code="invalid_request")

    # 4. Enum
    if "enum" in spec and isinstance(spec["enum"], (list, tuple)):
        if val not in spec["enum"]:
            raise LoiOpenAI(400, f"Giá trị tham số «{ten}» không nằm trong enum cho phép: {spec['enum']}", code="invalid_request")

    # 5. Đệ quy array
    if isinstance(val, (list, tuple)):
        if "minItems" in spec and len(val) < spec["minItems"]:
            raise LoiOpenAI(400, f"Số phần tử của «{ten}» nhỏ hơn tối thiểu ({spec['minItems']})", code="invalid_request")
        if "maxItems" in spec and len(val) > spec["maxItems"]:
            raise LoiOpenAI(400, f"Số phần tử của «{ten}» vượt quá tối đa ({spec['maxItems']})", code="invalid_request")
        items_spec = spec.get("items")
        if isinstance(items_spec, dict):
            for i, item_val in enumerate(val):
                _kiem_tra_gia_tri_theo_luoc_do(item_val, items_spec, f"{ten}[{i}]", do_sau + 1)

    # 6. Đệ quy object
    if isinstance(val, dict):
        required = spec.get("required")
        if isinstance(required, (list, tuple)):
            for req in required:
                if req not in val:
                    ten_hien = f"{ten}.{req}" if ten else req
                    raise LoiOpenAI(400, f"Thiếu tham số bắt buộc: «{ten_hien}»", code="invalid_request")
        props = spec.get("properties")
        if isinstance(props, dict):
            additional = spec.get("additionalProperties", True)
            if additional is False:
                for k in val:
                    if k not in props:
                        ten_k = f"{ten}.{k}" if ten else k
                        raise LoiOpenAI(400, f"Tham số «{ten_k}» không được phép theo lược đồ", code="invalid_request")
            for k, sub_v in val.items():
                if k in props and isinstance(props[k], dict):
                    _kiem_tra_gia_tri_theo_luoc_do(sub_v, props[k], f"{ten}.{k}" if ten else k, do_sau + 1)


def kiem_tra_tham_so(tinh_nang: Any, hanh_dong: str, tham_so: dict) -> None:
    """Kiểm tra tham số đầu vào so với lược đồ được khai báo trong tinh_nang (SEC-004a).

    Ném LoiOpenAI(400) nếu:
    - Tham số không phải dict hoặc vượt trần kích thước 8 KB.
    - Chứa số non-finite (NaN hoặc Infinity).
    - Thiếu trường bắt buộc (required).
    - Có trường lạ khi additionalProperties là False.
    - Sai kiểu dữ liệu (kể cả type danh sách), vi phạm mảng/đối tượng lồng nhau.
    - Vượt khoảng giá trị (minimum, maximum, exclusiveMinimum, exclusiveMaximum, minLength, maxLength).
    - Không nằm trong enum cho phép.
    """
    if not isinstance(tham_so, dict):
        raise LoiOpenAI(400, "«tham_so» phải là đối tượng từ điển", code="invalid_request")

    # Trần kích thước 8 KB sau khi tuần tự hoá
    raw = json.dumps(tham_so, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 8192:
        raise LoiOpenAI(400, "Kích thước tham số vượt quá giới hạn 8 KB", code="invalid_request")

    # Từ chối non-finite ở mọi trường số
    _kiem_tra_non_finite(tham_so)

    luoc_do = lay_luoc_do_tham_so(tinh_nang, hanh_dong)
    if not luoc_do:
        return

    _kiem_tra_gia_tri_theo_luoc_do(tham_so, luoc_do, "", do_sau=0)


# ───────────── Trần tần suất lệnh thiết bị (SEC-004e) ─────────────

_KHOA_TAN_SUAT = threading.Lock()
_LICH_SU_KHOA_THUC_THE: dict[tuple[str, str], list[float]] = {}
_LICH_SU_LUOT: dict[tuple[str, str], int] = {}
_LICH_SU_BURST: dict[str, list[float]] = {}


def tran_luot_thiet_bi() -> int:
    val = os.environ.get("WEB2API_THIET_BI_TRAN_LUOT", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 8


def tran_phut_thiet_bi() -> int:
    val = os.environ.get("WEB2API_THIET_BI_TRAN_PHUT", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 30


def kiem_tra_tan_suat(id_khoa: str, id_thuc_the: str, id_luot: str | None = None) -> None:
    """Kiểm tra trần tần suất theo SEC-004(e):
    - Mỗi lượt: WEB2API_THIET_BI_TRAN_LUOT (mặc định 8 lệnh/lượt).
    - Theo (khoá, thực thể): WEB2API_THIET_BI_TRAN_PHUT (mặc định 30 lệnh/phút).
    Vượt -> 429 kèm Retry-After.
    """
    now = time.time()
    tran_luot = tran_luot_thiet_bi()
    tran_phut = tran_phut_thiet_bi()

    with _KHOA_TAN_SUAT:
        # 1. Kiểm tra trần mỗi lượt
        if id_luot:
            key_luot = (id_khoa, id_luot)
            so_luot = _LICH_SU_LUOT.get(key_luot, 0)
            if so_luot >= tran_luot:
                raise LoiOpenAI(
                    429,
                    f"Vượt quá giới hạn tần suất lệnh mỗi lượt ({tran_luot} lệnh/lượt).",
                    code="rate_limit_exceeded",
                    headers={"Retry-After": "5"},
                )
            _LICH_SU_LUOT[key_luot] = so_luot + 1
        else:
            burst = _LICH_SU_BURST.setdefault(id_khoa, [])
            burst[:] = [t for t in burst if now - t < 5.0]
            if len(burst) >= tran_luot:
                raise LoiOpenAI(
                    429,
                    f"Vượt quá giới hạn tần suất lệnh mỗi lượt ({tran_luot} lệnh/lượt).",
                    code="rate_limit_exceeded",
                    headers={"Retry-After": "5"},
                )
            burst.append(now)

        # 2. Kiểm tra trần theo (khoá, thực thể) trong 60s
        key_kt = (id_khoa, id_thuc_the)
        lich_su = _LICH_SU_KHOA_THUC_THE.setdefault(key_kt, [])
        lich_su[:] = [t for t in lich_su if now - t < 60.0]

        if len(lich_su) >= tran_phut:
            oldest = lich_su[0]
            cho = int(max(1.0, 60.0 - (now - oldest)))
            raise LoiOpenAI(
                429,
                f"Vượt quá giới hạn tần suất ({tran_phut} lệnh/phút) cho thực thể «{id_thuc_the}».",
                code="rate_limit_exceeded",
                headers={"Retry-After": str(cho)},
            )
        lich_su.append(now)


def _p_thiet_bi() -> Path:
    return kho.thu_muc_du_lieu() / "thiet_bi.json"


def _p_phe_duyet() -> Path:
    return kho.thu_muc_du_lieu() / "phe_duyet.json"


# ───────────── Quản trị xác nhận & Ghi đè rủi ro (SEC-004f) ─────────────

def bam_tinh_nang(tinh_nang: Any) -> str:
    """Tính băm SHA-256 ổn định của tinh_nang để phát hiện thực thể mới hoặc đổi tính năng."""
    chuoi = json.dumps(tinh_nang or [], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(chuoi.encode("utf-8")).hexdigest()[:16]


def doc_da_xac_nhan() -> dict[str, str]:
    """Đọc từ điển {id_thuc_the: bam_tinh_nang} các thực thể đã được quản trị xác nhận mức rủi ro."""
    with _KHOA_LUONG:
        p = _p_thiet_bi()
        try:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("da_xac_nhan"), dict):
                return {str(k): str(v) for k, v in du_lieu["da_xac_nhan"].items()}
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
        return {}


def xac_nhan_thuc_the(id_thuc_the: str, tinh_nang: Any, rui_ro: str | None = None) -> None:
    """Quản trị xác nhận rủi ro cho thực thể và lưu băm tính năng vào kho thiết bị (0600, nguyên tử)."""
    with _KHOA_LUONG:
        p = _p_thiet_bi()
        du_lieu: dict[str, Any] = {}
        try:
            doc = kho.doc_json(p)
            if isinstance(doc, dict):
                du_lieu = doc
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Lỗi khi đọc %s để xác nhận: %s", p, e)

        ghi_de = du_lieu.get("ghi_de_rui_ro")
        if not isinstance(ghi_de, dict):
            ghi_de = {}
        da_xn = du_lieu.get("da_xac_nhan")
        if not isinstance(da_xn, dict):
            da_xn = {}

        if rui_ro is not None:
            if rui_ro not in RUI_RO_HOP_LE:
                raise ValueError(f"Mức rủi ro không hợp lệ: {rui_ro!r}")
            ghi_de[id_thuc_the] = rui_ro

        da_xn[id_thuc_the] = bam_tinh_nang(tinh_nang)
        du_lieu["ghi_de_rui_ro"] = ghi_de
        du_lieu["da_xac_nhan"] = da_xn
        _luu_json(p, du_lieu)


def doc_ghi_de_rui_ro() -> dict[str, str]:
    """Đọc từ điển ghi đè rủi ro {id_thuc_the: "doc" | "ghi" | "nguy_hiem"}.

    Tệp hỏng → 503 (không trả rỗng): trả rỗng rồi ghi lại sẽ XOÁ toàn bộ ghi đè cũ.
    """
    with _KHOA_LUONG:
        p = _p_thiet_bi()
        try:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("ghi_de_rui_ro"), dict):
                return {str(k): str(v) for k, v in du_lieu["ghi_de_rui_ro"].items() if v in RUI_RO_HOP_LE}
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
            raise LoiOpenAI(503, f"Không đọc được kho thiết bị ({p.name}), không ghi đè.",
                            code="store_unreadable") from None
        return {}


def luu_ghi_de_rui_ro(id_thuc_the: str, rui_ro: str, tinh_nang: Any = None) -> None:
    """Lưu ghi đè rủi ro và xác nhận thực thể (0600, nguyên tử)."""
    if rui_ro not in RUI_RO_HOP_LE:
        raise ValueError(f"Mức rủi ro không hợp lệ: {rui_ro!r} (chỉ nhận: {sorted(RUI_RO_HOP_LE)})")
    with _KHOA_LUONG:
        p = _p_thiet_bi()
        du_lieu: dict[str, Any] = {}
        try:
            doc = kho.doc_json(p)
            if isinstance(doc, dict):
                du_lieu = doc
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
            raise LoiOpenAI(503, f"Không đọc được kho thiết bị ({p.name}), không ghi đè.",
                            code="store_unreadable") from None

        ghi_de = du_lieu.get("ghi_de_rui_ro")
        if not isinstance(ghi_de, dict):
            ghi_de = {}
        ghi_de[id_thuc_the] = rui_ro
        du_lieu["ghi_de_rui_ro"] = ghi_de

        if tinh_nang is not None:
            da_xn = du_lieu.get("da_xac_nhan")
            if not isinstance(da_xn, dict):
                da_xn = {}
            da_xn[id_thuc_the] = bam_tinh_nang(tinh_nang)
            du_lieu["da_xac_nhan"] = da_xn

        _luu_json(p, du_lieu)


# ───────────── Hàng chờ phê duyệt ─────────────

def thoi_han_phe_duyet_phut() -> int:
    """Thời hạn mục phê duyệt (phút), mặc định 10 phút từ biến WEB2API_PHE_DUYET_PHUT."""
    val = os.environ.get("WEB2API_PHE_DUYET_PHUT", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 10


def doc_hang_cho_phe_duyet() -> list[dict[str, Any]]:
    """Đọc danh sách mục đang chờ phê duyệt. Mục quá hạn tự động bị loại bỏ.

    Tệp hỏng → 503 (không trả rỗng): trả rỗng rồi ghi lại sẽ XOÁ toàn bộ hàng chờ.
    """
    with _KHOA_LUONG:
        p = _p_phe_duyet()
        hang_cho: list[dict[str, Any]] = []
        try:
            du_lieu = kho.doc_json(p)
            if isinstance(du_lieu, dict) and isinstance(du_lieu.get("hang_cho"), list):
                hang_cho = [dict(m) for m in du_lieu["hang_cho"] if isinstance(m, dict)]
        except FileNotFoundError:
            return []
        except Exception as e:
            log.warning("Lỗi khi đọc %s: %s", p, e)
            raise LoiOpenAI(503, f"Không đọc được hàng chờ phê duyệt ({p.name}), không ghi đè.",
                            code="store_unreadable") from None

        bay_gio = datetime.now(timezone.utc)
        con_han: list[dict[str, Any]] = []
        co_thay_doi = False

        for item in hang_cho:
            het_han_str = item.get("het_han")
            if het_han_str:
                try:
                    hh = datetime.fromisoformat(het_han_str)
                    if hh.tzinfo is None:
                        hh = hh.replace(tzinfo=timezone.utc)
                    if hh <= bay_gio:
                        co_thay_doi = True
                        continue
                except Exception:
                    co_thay_doi = True
                    continue
            con_han.append(item)

        if co_thay_doi:
            try:
                _luu_json(p, {"hang_cho": con_han})
            except Exception as e:
                log.warning("Lỗi khi dọn mục phê duyệt quá hạn: %s", e)

        return con_han


def tran_phe_duyet_moi_khoa() -> int:
    """Trần số mục chờ phê duyệt của một khoá, mặc định 10 từ WEB2API_PHE_DUYET_TOI_DA_MOI_KHOA."""
    val = os.environ.get("WEB2API_PHE_DUYET_TOI_DA_MOI_KHOA", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 10


def tran_so_muc_phe_duyet() -> int:
    """Trần số mục trong hàng chờ phê duyệt, mặc định 100 từ WEB2API_PHE_DUYET_TRAN_SO_MUC."""
    val = os.environ.get("WEB2API_PHE_DUYET_TRAN_SO_MUC", "").strip()
    if val:
        try:
            n = int(val)
            if n > 0:
                return n
        except ValueError:
            pass
    return 100


def _bam_trang_thai_phe_duyet(thuc_the: str, tinh_nang: Any, hanh_dong: str, tham_so: dict) -> str:
    raw = json.dumps({
        "thuc_the": thuc_the,
        "tinh_nang": tinh_nang if tinh_nang is not None else [],
        "hanh_dong": hanh_dong,
        "tham_so": tham_so or {},
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def them_hang_cho_phe_duyet(
    ai_de_xuat: str,
    thuc_the: str,
    hanh_dong: str,
    tham_so: dict,
    ly_do: str = "",
    tinh_nang: Any = None,
) -> dict[str, Any]:
    """Thêm một yêu cầu vào hàng chờ phê duyệt, loại trùng và áp dụng trần số mục (SEC-004c)."""
    # Trần kích thước tham số 8 KB
    raw_ts = json.dumps(tham_so or {}, ensure_ascii=False)
    if len(raw_ts.encode("utf-8")) > 8192:
        raise LoiOpenAI(400, "Kích thước tham số vượt quá giới hạn 8 KB", code="invalid_request")

    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        # Loại trùng theo (khoá, thực thể, hành động) - KHÔNG trả pd_id của khoá A cho khoá B
        for muc_cu in ds:
            if (muc_cu.get("ai_de_xuat") == ai_de_xuat
                and muc_cu.get("thuc_the") == thuc_the
                and muc_cu.get("hanh_dong") == hanh_dong):
                ts_moi = dict(tham_so or {})
                ly_do_moi = str(ly_do or "")
                bam_moi = _bam_trang_thai_phe_duyet(thuc_the, tinh_nang, hanh_dong, ts_moi)
                if (muc_cu.get("tham_so") == ts_moi
                    and muc_cu.get("ly_do") == ly_do_moi
                    and muc_cu.get("bam") == bam_moi):
                    return muc_cu
                muc_cu["tham_so"] = ts_moi
                muc_cu["ly_do"] = ly_do_moi
                muc_cu["bam"] = bam_moi
                _luu_json(_p_phe_duyet(), {"hang_cho": ds})
                return muc_cu

        # Kiểm tra trần theo khoá
        tran_khoa = tran_phe_duyet_moi_khoa()
        so_muc_khoa = sum(1 for m in ds if m.get("ai_de_xuat") == ai_de_xuat)
        if so_muc_khoa >= tran_khoa:
            raise LoiOpenAI(429, f"Khoá «{ai_de_xuat}» đã đạt trần số mục phê duyệt chờ xử lý ({tran_khoa}).", code="queue_full_per_key")

        # Kiểm tra trần toàn cục
        tran_muc = tran_so_muc_phe_duyet()
        if len(ds) >= tran_muc:
            raise LoiOpenAI(429, f"Hàng chờ phê duyệt đã đạt trần số mục ({tran_muc}).", code="queue_full")

        phut = thoi_han_phe_duyet_phut()
        bay_gio = datetime.now(timezone.utc)
        het_han = bay_gio.timestamp() + phut * 60
        het_han_iso = datetime.fromtimestamp(het_han, timezone.utc).isoformat()
        pd_id = f"pd_{secrets.token_hex(6)}"

        bam_luc_tao = _bam_trang_thai_phe_duyet(thuc_the, tinh_nang, hanh_dong, tham_so)

        muc = {
            "id": pd_id,
            "ai_de_xuat": ai_de_xuat,
            "thuc_the": thuc_the,
            "hanh_dong": hanh_dong,
            "tham_so": dict(tham_so or {}),
            "ly_do": str(ly_do or ""),
            "luc": kho.bay_gio_iso(),
            "het_han": het_han_iso,
            "bam": bam_luc_tao,
        }

        ds.append(muc)
        _luu_json(_p_phe_duyet(), {"hang_cho": ds})

    # Phát sự kiện SSE duy nhất ở đây, làm sạch tham số trước khi phát
    luoc_do = lay_luoc_do_tham_so(tinh_nang, hanh_dong) if tinh_nang else None
    muc_phat = dict(muc)
    muc_phat["tham_so"] = lam_sach_tham_so(muc.get("tham_so", {}), luoc_do)
    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_moi", "muc": muc_phat})
    return muc


async def duyet_phe_duyet(pd_id: str, so: SoModule, nguoi_duyet: str = "quan_tri") -> dict:
    """Quản trị duyệt mục phê duyệt: kiểm tra LẠI trước khi thực hiện (SEC-004d), xoá mục và thực thi."""
    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        tim_thay = next((m for m in ds if m.get("id") == pd_id), None)
        if tim_thay is None:
            raise LoiOpenAI(404, f"Không tìm thấy mục phê duyệt {pd_id!r} hoặc mục đã quá hạn.", code="not_found")
        # Xoá ngay trong khoá trước mọi await để chống đua lệnh (race condition / TOCTOU)
        con_lai = [m for m in ds if m.get("id") != pd_id]
        _luu_json(_p_phe_duyet(), {"hang_cho": con_lai})

    thuc_the_id = tim_thay["thuc_the"]
    hanh_dong = tim_thay["hanh_dong"]
    tham_so = tim_thay.get("tham_so", {})
    ai_de_xuat = tim_thay.get("ai_de_xuat", "khach")
    bam_cu = tim_thay.get("bam")

    # SEC-004(d): Kiểm tra lại trước khi thực hiện:
    # 1. Thực thể còn tồn tại
    item = await lay_mot_thuc_the(thuc_the_id, so)
    if not item:
        raise LoiOpenAI(409, f"Thực thể «{thuc_the_id}» không còn tồn tại.", code="conflict")

    # 2. Hành động còn trong tinh_nang
    tinh_nang = item.get("tinh_nang", [])
    cac_hd = []
    for tn in tinh_nang:
        if isinstance(tn, str):
            cac_hd.append(tn.strip())
        elif isinstance(tn, dict):
            cac_hd.append(str(tn.get("hanh_dong") or tn.get("ten") or tn.get("name") or "").strip())
    if hanh_dong not in cac_hd:
        raise LoiOpenAI(409, f"Hành động «{hanh_dong}» không còn trong tính năng hỗ trợ của thực thể.", code="conflict")

    # 3. Mức rủi ro vẫn nguy_hiem
    rr_hien_tai = item.get("rui_ro")
    if rr_hien_tai != "nguy_hiem":
        raise LoiOpenAI(409, f"Mức rủi ro của hành động «{hanh_dong}» đã thay đổi (không còn «nguy_hiem»).", code="conflict")

    # 4. Kiểm tra băm tính năng so với lúc tạo (Mục 5)
    if bam_cu:
        bam_moi = _bam_trang_thai_phe_duyet(thuc_the_id, tinh_nang, hanh_dong, tham_so)
        if bam_moi != bam_cu:
            raise LoiOpenAI(409, "Tính năng hoặc tham số của thực thể đã thay đổi so với thời điểm yêu cầu phê duyệt.", code="conflict")

    # 5. Kiểm tra lại tham số theo lược đồ hiện tại (Mục 3)
    try:
        kiem_tra_tham_so(tinh_nang, hanh_dong, tham_so)
    except LoiOpenAI as e:
        raise LoiOpenAI(409, f"Tham số không còn phù hợp với lược đồ tính năng hiện tại: {e.message}", code="conflict") from e

    # Thực hiện hành động
    tien_to, _, _ = thuc_the_id.partition(".")
    mod_id = so.tim_theo_tien_to(tien_to)
    if not mod_id or mod_id not in so.dang_chay:
        raise LoiOpenAI(502, f"Module cho tiền tố {tien_to!r} hiện không sẵn sàng.", code="upstream_error")

    km = so.catalog.get(mod_id)
    if not km or not callable(getattr(so.dang_chay[mod_id], "thuc_hien", None)):
        raise LoiOpenAI(502, f"Module {mod_id!r} không hỗ trợ thực hiện hành động.", code="upstream_error")

    m = so.dang_chay[mod_id]
    try:
        kq = await m.thuc_hien(thuc_the_id, hanh_dong, tham_so)
    except Exception as e:
        log.exception("Lỗi khi thực hiện hành động đã duyệt trên module %s", mod_id)
        nhat_ky.ghi(
            "thiet_bi",
            "loi",
            f"Duyệt nhưng lỗi: «{nguoi_duyet}» duyệt «{hanh_dong}» trên «{thuc_the_id}» nhưng module lỗi: {e}",
            nguoi_duyet=nguoi_duyet,
            ai_de_xuat=ai_de_xuat,
            thuc_the=thuc_the_id,
            hanh_dong=hanh_dong,
            loi=str(e),
        )
        raise LoiOpenAI(502, f"Module lỗi khi thực hiện: {type(e).__name__}", code="upstream_error") from e

    luoc_do = lay_luoc_do_tham_so(item.get("tinh_nang", []), hanh_dong)
    nhat_ky.ghi(
        "thiet_bi",
        "thong_tin",
        f"Đã duyệt: «{nguoi_duyet}» duyệt hành động «{hanh_dong}» cho thực thể «{thuc_the_id}» (đề xuất bởi {ai_de_xuat})",
        nguoi_duyet=nguoi_duyet,
        ai_de_xuat=ai_de_xuat,
        thuc_the=thuc_the_id,
        hanh_dong=hanh_dong,
        tham_so=lam_sach_tham_so(tham_so, luoc_do),
    )
    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_xong", "id": pd_id, "thuc_the": thuc_the_id, "hanh_dong": hanh_dong})
    return {"ok": True, "id": pd_id, "ket_qua": kq}


async def lay_hang_cho_hien_thi(so: SoModule) -> list[dict[str, Any]]:
    """Đọc hàng chờ phê duyệt và che các tham số bí mật / chưa khai theo SEC-004b trước khi trả ra API."""
    raw_list = doc_hang_cho_phe_duyet()
    if not raw_list:
        return []
    res = []
    for item in raw_list:
        m_copy = dict(item)
        thuc_the_id = m_copy.get("thuc_the", "")
        hanh_dong = m_copy.get("hanh_dong", "")
        ts = m_copy.get("tham_so", {})
        ent = await lay_mot_thuc_the(thuc_the_id, so)
        luoc_do = lay_luoc_do_tham_so(ent.get("tinh_nang", []), hanh_dong) if ent else None
        m_copy["tham_so"] = lam_sach_tham_so(ts, luoc_do)
        res.append(m_copy)
    return res


def tu_choi_phe_duyet(pd_id: str, nguoi_tu_choi: str = "quan_tri") -> dict:
    """Quản trị từ chối mục phê duyệt: xoá khỏi hàng chờ và ghi sổ kênh thiet_bi."""
    with _KHOA_LUONG:
        ds = doc_hang_cho_phe_duyet()
        tim_thay = next((m for m in ds if m.get("id") == pd_id), None)
        if tim_thay is None:
            raise LoiOpenAI(404, f"Không tìm thấy mục phê duyệt {pd_id!r} hoặc mục đã quá hạn.", code="not_found")
        con_lai = [m for m in ds if m.get("id") != pd_id]
        _luu_json(_p_phe_duyet(), {"hang_cho": con_lai})

    thuc_the_id = tim_thay["thuc_the"]
    hanh_dong = tim_thay["hanh_dong"]
    ai_de_xuat = tim_thay.get("ai_de_xuat", "khach")

    nhat_ky.ghi(
        "thiet_bi",
        "thong_tin",
        f"Từ chối: «{nguoi_tu_choi}» từ chối hành động «{hanh_dong}» cho thực thể «{thuc_the_id}» (đề xuất bởi {ai_de_xuat})",
        nguoi_tu_choi=nguoi_tu_choi,
        ai_de_xuat=ai_de_xuat,
        thuc_the=thuc_the_id,
        hanh_dong=hanh_dong,
    )
    BO_PHAT.phat_dong_bo({"loai": "phe_duyet_tu_choi", "id": pd_id, "thuc_the": thuc_the_id, "hanh_dong": hanh_dong})
    return {"ok": True, "id": pd_id, "da_tu_choi": True}


# ───────────── Tra cứu thực thể ─────────────

def _module_thiet_bi_hop_le(so: SoModule) -> list[tuple[str, Any, Any]]:
    """Liệt kê các module đang chạy có hỗ trợ chuẩn thiet_bi: (id, km, thể_hiện)."""
    kq = []
    for id_, m in list(so.dang_chay.items()):
        km = so.catalog.get(id_)
        if not km:
            continue
        cung_cap = km.manifest.get("cung_cap", [])
        if "thiet_bi" not in cung_cap:
            continue
        if "thiet_bi" in getattr(km, "chuan_loi", {}):
            continue
        if not (callable(getattr(m, "thuc_the", None)) and
                callable(getattr(m, "trang_thai", None)) and
                callable(getattr(m, "thuc_hien", None))):
            continue
        kq.append((id_, km, m))
    return kq


async def lay_danh_sach_thuc_the(
    so: SoModule,
    loc_q: str | None = None,
    loc_loai: str | None = None,
    loc_khu: str | None = None,
    loc_module: str | None = None,
    loc_rui_ro: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Gộp thực thể mọi module đang chạy có chuẩn thiet_bi, kèm rủi ro hiệu lực và trạng thái."""
    modules = _module_thiet_bi_hop_le(so)
    ghi_de = doc_ghi_de_rui_ro()
    da_xn = doc_da_xac_nhan()
    danh_sach_thuc_the: list[dict[str, Any]] = []
    loi_module: list[dict[str, str]] = []
    id_da_co: set[str] = set()

    for mod_id, km, m in modules:
        tien_to = km.tien_to
        try:
            raw_entities = await asyncio.wait_for(m.thuc_the(None), timeout=5.0)
        except Exception as e:
            log.warning("Module %s lỗi/quá hạn khi gọi thuc_the: %s", mod_id, e)
            loi_module.append({"module": mod_id, "loi": str(e)})
            continue

        if not isinstance(raw_entities, list):
            loi_module.append({"module": mod_id, "loi": "thuc_the không trả về danh sách"})
            continue

        thuc_the_hop_le = []
        ids_can_trang_thai = []

        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            e_id = item.get("id")
            if not isinstance(e_id, str):
                continue
            # Kiểm hình dạng: id phải bắt đầu bằng f"{tien_to}."
            if not e_id.startswith(f"{tien_to}."):
                log.warning("Module %s trả thực thể có id %r sai tiền tố (cần bắt đầu bằng %s.), bỏ qua",
                            mod_id, e_id, f"{tien_to}.")
                continue
            # Kiểm trùng lặp
            if e_id in id_da_co:
                log.warning("Module %s trả thực thể trùng id %r, bỏ qua", mod_id, e_id)
                continue
            id_da_co.add(e_id)

            rr_mac_dinh = item.get("rui_ro_mac_dinh")
            if rr_mac_dinh not in RUI_RO_HOP_LE:
                rr_mac_dinh = "nguy_hiem"

            # Rủi ro hiệu lực (SEC-004f): Thực thể mới hoặc đổi tinh_nang coi là nguy_hiem cho tới khi quản trị xác nhận
            b_tn = bam_tinh_nang(item.get("tinh_nang"))
            if e_id not in da_xn or da_xn.get(e_id) != b_tn:
                rr_hieu_luc = "nguy_hiem"
            else:
                rr_hieu_luc = ghi_de.get(e_id, rr_mac_dinh)

            muc = {
                "id": e_id,
                "ten": str(item.get("ten") or e_id),
                "loai": str(item.get("loai") or "thiet_bi"),
                "khu": item.get("khu"),
                "thuoc_tinh": dict(item.get("thuoc_tinh") or {}),
                "tinh_nang": list(item.get("tinh_nang") or []),
                "rui_ro_mac_dinh": rr_mac_dinh,
                "rui_ro": rr_hieu_luc,
                "module": mod_id,
                "trang_thai": None,
            }
            thuc_the_hop_le.append(muc)
            ids_can_trang_thai.append(e_id)

        # Lấy trạng thái cho các thực thể hợp lệ
        if ids_can_trang_thai:
            try:
                tt_dict = await asyncio.wait_for(m.trang_thai(ids_can_trang_thai), timeout=5.0)
                if isinstance(tt_dict, dict):
                    for muc in thuc_the_hop_le:
                        muc["trang_thai"] = tt_dict.get(muc["id"])
            except Exception as e:
                log.warning("Module %s lỗi/quá hạn khi lấy trang_thai: %s", mod_id, e)

        danh_sach_thuc_the.extend(thuc_the_hop_le)

    # Áp dụng bộ lọc
    kq = []
    q_chuan = bo_dau(loc_q.strip()) if loc_q and loc_q.strip() else None

    for item in danh_sach_thuc_the:
        if q_chuan:
            ten_chuan = bo_dau(item["ten"])
            id_chuan = bo_dau(item["id"])
            if q_chuan not in ten_chuan and q_chuan not in id_chuan:
                continue
        if loc_loai and item["loai"] != loc_loai:
            continue
        if loc_khu and item["khu"] != loc_khu:
            continue
        if loc_module and item["module"] != loc_module:
            continue
        if loc_rui_ro and item["rui_ro"] != loc_rui_ro:
            continue
        kq.append(item)

    return kq, loi_module


async def lay_mot_thuc_the(id_thuc_the: str, so: SoModule) -> dict[str, Any] | None:
    """Lấy thông tin và trạng thái của một thực thể cụ thể theo id (404 nếu không có)."""
    tien_to, co_cham, _ = id_thuc_the.partition(".")
    if not co_cham:
        return None

    mod_id = so.tim_theo_tien_to(tien_to)
    if not mod_id or mod_id not in so.dang_chay:
        return None

    km = so.catalog.get(mod_id)
    if not km or "thiet_bi" in getattr(km, "chuan_loi", {}):
        return None

    m = so.dang_chay[mod_id]
    if not (callable(getattr(m, "thuc_the", None)) and callable(getattr(m, "trang_thai", None))):
        return None

    try:
        raw_entities = await asyncio.wait_for(m.thuc_the(None), timeout=5.0)
    except Exception:
        return None

    if not isinstance(raw_entities, list):
        return None

    tim_thay = next((item for item in raw_entities if isinstance(item, dict) and item.get("id") == id_thuc_the), None)
    if not tim_thay:
        return None

    ghi_de = doc_ghi_de_rui_ro()
    da_xn = doc_da_xac_nhan()
    rr_mac_dinh = tim_thay.get("rui_ro_mac_dinh")
    if rr_mac_dinh not in RUI_RO_HOP_LE:
        rr_mac_dinh = "nguy_hiem"
    b_tn = bam_tinh_nang(tim_thay.get("tinh_nang"))
    if id_thuc_the not in da_xn or da_xn.get(id_thuc_the) != b_tn:
        rr_hieu_luc = "nguy_hiem"
    else:
        rr_hieu_luc = ghi_de.get(id_thuc_the, rr_mac_dinh)

    trang_thai = None
    try:
        tt_dict = await asyncio.wait_for(m.trang_thai([id_thuc_the]), timeout=5.0)
        if isinstance(tt_dict, dict):
            trang_thai = tt_dict.get(id_thuc_the)
    except Exception:
        pass

    return {
        "id": id_thuc_the,
        "ten": str(tim_thay.get("ten") or id_thuc_the),
        "loai": str(tim_thay.get("loai") or "thiet_bi"),
        "khu": tim_thay.get("khu"),
        "thuoc_tinh": dict(tim_thay.get("thuoc_tinh") or {}),
        "tinh_nang": list(tim_thay.get("tinh_nang") or []),
        "rui_ro_mac_dinh": rr_mac_dinh,
        "rui_ro": rr_hieu_luc,
        "module": mod_id,
        "trang_thai": trang_thai,
    }


# ───────────── Cổng thực hiện hành động thiết bị lõi (SEC-002, SEC-004) ─────────────

async def thuc_hien_hanh_dong_loi(
    id_thuc_the: str,
    hanh_dong: str,
    tham_so: dict[str, Any] | None = None,
    xac_nhan: bool = False,
    ly_do: str = "",
    ngu_canh: Any = None,
    so: SoModule | None = None,
    module: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Cổng thực hiện hành động thiết bị dùng chung cho cả HTTP và gọi nội bộ (SEC-002, SEC-004).

    Quy trình:
    1. Kiểm tra ngữ cảnh gọi và phạm vi thiet_bi:ghi (đóng khi thiếu ngữ cảnh).
    2. Kiểm tra tính hợp lệ của tham số đầu vào.
    3. Tra cứu thực thể (qua SoModule hoặc module trực tiếp).
    4. Kiểm tra hành động thuộc tính năng hỗ trợ của thực thể.
    5. Kiểm tra tham số theo lược đồ (min/max, kiểu, required, additionalProperties... SEC-004a).
    6. Kiểm tra giới hạn tần suất lệnh cho khoá khách (8/lượt, 30/phút/khoá/thực thể SEC-004e).
    7. Xử lý mức độ rủi ro:
       - nguy_hiem + quan_tri: cần xac_nhan: true (thiếu -> 409).
       - nguy_hiem + khach: vào hàng chờ phê duyệt và trả 202 (SEC-004c).
    8. Thực hiện hành động qua module.
    9. Ghi sổ kiểm toán kênh thiet_bi với danh tính và nguon do LÕI đóng dấu, tham số làm sạch (SEC-004b).
    """
    from app import ngu_canh as ngu_canh_mod

    if ngu_canh is None:
        ngu_canh = ngu_canh_mod.lay()
    if ngu_canh is None:
        raise LoiOpenAI(
            403,
            "Thiếu ngữ cảnh gọi hợp lệ (missing context).",
            loai="permission_error",
            code="missing_context",
        )

    # 1. Kiểm tra phạm vi
    danh_tinh = ngu_canh.danh_tinh
    pv = ngu_canh.pham_vi_hieu_luc
    la_quan_tri = danh_tinh.get("loai") in ("quan_tri", "noi_bo")
    if not la_quan_tri and "*" not in pv and "thiet_bi:ghi" not in pv:
        raise LoiOpenAI(
            403,
            "Khoá không có quyền thực hiện thao tác này (cần phạm vi «thiet_bi:ghi»).",
            loai="insufficient_scope",
            code="insufficient_scope",
        )

    # 2. Định dạng đầu vào
    if not isinstance(id_thuc_the, str) or not id_thuc_the.strip():
        raise LoiOpenAI(400, "Thiếu hoặc sai trường «id_thuc_the»", code="invalid_request")
    id_thuc_the = id_thuc_the.strip()

    if not isinstance(hanh_dong, str) or not hanh_dong.strip():
        raise LoiOpenAI(400, "Thiếu trường «hanh_dong»", code="invalid_request")
    hanh_dong = hanh_dong.strip()

    if tham_so is None:
        tham_so = {}
    if not isinstance(tham_so, dict):
        raise LoiOpenAI(400, "«tham_so» phải là đối tượng từ điển", code="invalid_request")

    # 3. Tra cứu thực thể
    item: dict[str, Any] | None = None
    target_module: Any = None
    mod_id_log: str = ""

    if so is not None:
        item = await lay_mot_thuc_the(id_thuc_the, so)
        if item:
            tien_to, _, _ = id_thuc_the.partition(".")
            mod_id = so.tim_theo_tien_to(tien_to)
            if mod_id and mod_id in so.dang_chay:
                target_module = so.dang_chay[mod_id]
                mod_id_log = mod_id
    elif module is not None:
        target_module = module
        manifest = getattr(module, "manifest", {})
        mod_id_log = manifest.get("id", getattr(module, "id", "module"))
        if callable(getattr(module, "thuc_the", None)):
            try:
                raw_ents = await asyncio.wait_for(module.thuc_the(None), timeout=5.0)
                if isinstance(raw_ents, list):
                    for e in raw_ents:
                        if isinstance(e, dict) and e.get("id") == id_thuc_the:
                            ghi_de = doc_ghi_de_rui_ro()
                            da_xn = doc_da_xac_nhan()
                            rr_mac_dinh = e.get("rui_ro_mac_dinh")
                            if rr_mac_dinh not in RUI_RO_HOP_LE:
                                rr_mac_dinh = "nguy_hiem"
                            b_tn = bam_tinh_nang(e.get("tinh_nang"))
                            if id_thuc_the not in da_xn or da_xn.get(id_thuc_the) != b_tn:
                                rr_hieu_luc = "nguy_hiem"
                            else:
                                rr_hieu_luc = ghi_de.get(id_thuc_the, rr_mac_dinh)
                            item = {
                                "id": id_thuc_the,
                                "ten": str(e.get("ten") or id_thuc_the),
                                "loai": str(e.get("loai") or "thiet_bi"),
                                "khu": e.get("khu"),
                                "thuoc_tinh": dict(e.get("thuoc_tinh") or {}),
                                "tinh_nang": list(e.get("tinh_nang") or []),
                                "rui_ro_mac_dinh": rr_mac_dinh,
                                "rui_ro": rr_hieu_luc,
                                "module": mod_id_log,
                                "trang_thai": None,
                            }
                            break
            except Exception as e:
                log.warning("Lỗi tra cứu thực thể từ module %s: %s", mod_id_log, e)

    if not item:
        raise LoiOpenAI(404, f"Không tìm thấy thực thể {id_thuc_the!r}", code="not_found")

    # 4. Kiểm tra hành động có trong tính năng
    tinh_nang = item.get("tinh_nang", [])
    cac_hd = []
    for tn in tinh_nang:
        if isinstance(tn, str):
            cac_hd.append(tn.strip())
        elif isinstance(tn, dict):
            ten = tn.get("hanh_dong") or tn.get("ten") or tn.get("name") or ""
            cac_hd.append(str(ten).strip())

    if hanh_dong not in cac_hd:
        raise LoiOpenAI(
            400,
            f"Hành động «{hanh_dong}» không nằm trong tính năng hỗ trợ của thực thể {id_thuc_the!r} (hỗ trợ: {cac_hd})",
            code="invalid_action",
        )

    # 5. SEC-004(a): Kiểm tra tham số theo lược đồ
    kiem_tra_tham_so(tinh_nang, hanh_dong, tham_so)

    # 6. SEC-004(e): Giới hạn tần suất cho khoá khách
    nguoi_goi = str(danh_tinh.get("id") or danh_tinh.get("loai") or "khach")
    if danh_tinh.get("loai") == "khach":
        kiem_tra_tan_suat(id_khoa=nguoi_goi, id_thuc_the=id_thuc_the)

    # 7. Xử lý mức độ rủi ro (nguy_hiem)
    if item.get("rui_ro") == "nguy_hiem":
        if la_quan_tri:
            if not xac_nhan:
                nhat_ky.ghi(
                    "thiet_bi",
                    "canh_bao",
                    f"Từ chối 409: hành động nguy hiểm «{hanh_dong}» trên «{id_thuc_the}» cần quản trị xác nhận",
                    thuc_the=id_thuc_the,
                    hanh_dong=hanh_dong,
                    nguoi_goi=nguoi_goi,
                    nguon=ngu_canh.nguon,
                )
                raise LoiOpenAI(
                    409,
                    f"Hành động «{hanh_dong}» trên thực thể «{id_thuc_the}» có mức rủi ro nguy hiểm, cần gửi kèm «xac_nhan: true».",
                    code="can_xac_nhan",
                )
        else:
            pd = them_hang_cho_phe_duyet(
                ai_de_xuat=nguoi_goi,
                thuc_the=id_thuc_the,
                hanh_dong=hanh_dong,
                tham_so=tham_so,
                ly_do=ly_do,
                tinh_nang=tinh_nang,
            )
            nhat_ky.ghi(
                "thiet_bi",
                "thong_tin",
                f"Tạo yêu cầu phê duyệt {pd['id']}: «{nguoi_goi}» yêu cầu «{hanh_dong}» trên «{id_thuc_the}» (nguy hiểm)",
                phe_duyet_id=pd["id"],
                thuc_the=id_thuc_the,
                hanh_dong=hanh_dong,
                nguoi_goi=nguoi_goi,
                nguon=ngu_canh.nguon,
                ly_do=ly_do,
            )
            res_202 = {
                "ok": True,
                "phe_duyet_id": pd["id"],
                "trang_thai": "cho_phe_duyet",
                "cho_phe_duyet": True,
            }
            return 202, res_202

    # 8. Thực hiện qua module
    if not target_module or not callable(getattr(target_module, "thuc_hien", None)):
        raise LoiOpenAI(
            502,
            f"Module cho thực thể {id_thuc_the!r} không sẵn sàng hoặc không hỗ trợ thuc_hien.",
            code="upstream_error",
        )

    try:
        kq = await target_module.thuc_hien(id_thuc_the, hanh_dong, tham_so)
    except Exception as e:
        log.exception("Module %s lỗi khi thực hiện hành động %s trên %s", mod_id_log, hanh_dong, id_thuc_the)
        nhat_ky.ghi(
            "thiet_bi",
            "loi",
            f"Module lỗi khi thực hiện «{hanh_dong}» trên «{id_thuc_the}»: {e}",
            thuc_the=id_thuc_the,
            hanh_dong=hanh_dong,
            nguoi_goi=nguoi_goi,
            nguon=ngu_canh.nguon,
            loi=str(e),
        )
        raise LoiOpenAI(502, f"Module lỗi khi thực hiện hành động: {type(e).__name__}", code="upstream_error") from e

    # 9. Ghi sổ kiểm toán thành công (làm sạch tham số theo nguyên tắc)
    luoc_do = lay_luoc_do_tham_so(tinh_nang, hanh_dong)
    ts_sach = lam_sach_tham_so(tham_so, luoc_do)
    nhat_ky.ghi(
        "thiet_bi",
        "thong_tin",
        f"Thực hiện thành công hành động «{hanh_dong}» trên «{id_thuc_the}»",
        thuc_the=id_thuc_the,
        hanh_dong=hanh_dong,
        nguoi_goi=nguoi_goi,
        nguon=ngu_canh.nguon,
        tham_so=ts_sach,
    )
    if isinstance(kq, dict):
        kq_tra = dict(kq)
    else:
        kq_tra = kq
    return 200, kq_tra


# ───────────── Bảo vệ bus sự kiện SSE (NV29/SEC-003) ─────────────

#: GỬI sự kiện: POST /api/thiet-bi/su-kien. NGHE sự kiện: GET /api/thiet-bi/su-kien.
PHAM_VI_SU_KIEN_GUI = "thiet_bi:su_kien"
PHAM_VI_SU_KIEN_NGHE = "thiet_bi:su_kien_doc"
#: Mọi phạm vi được coi là «có quyền đọc» khi lọc phát (tương thích khoá cũ).
CAC_PHAM_VI_NGHE = frozenset({PHAM_VI_SU_KIEN_NGHE, PHAM_VI_SU_KIEN_GUI, "*"})

#: Mẫu định danh cho trường `loai` của sự kiện.
MAU_LOAI_SU_KIEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: Trần kích thước `gia_tri` sau khi serialize JSON (byte).
GIA_TRI_SU_KIEN_TOI_DA_BYTE = 4096
#: Trần độ sâu lồng nhau của `gia_tri`.
DO_SAU_GIA_TRI_TOI_DA = 4
#: Các loại sự kiện phê duyệt: chỉ phát tới người nghe là quản trị.
CAC_LOAI_PHE_DUYET = ("phe_duyet_moi", "phe_duyet_xong", "phe_duyet_tu_choi")
#: Giá trị Retry-After (giây) khi vượt trần người nghe.
RETRY_AFTER_NGHE_QUA_TRAN = 30


def _so_nguyen_moi_truong(ten: str, mac_dinh: int) -> int:
    try:
        val = int(os.environ.get(ten, mac_dinh))
        return val if val > 0 else mac_dinh
    except (TypeError, ValueError):
        return mac_dinh


def nghe_toi_da_moi_danh_tinh() -> int:
    """Trần người nghe SSE cho MỘT danh tính (mặc định 8, env WEB2API_SSE_NGHE_TOI_DA_MOI_DANH_TINH)."""
    return _so_nguyen_moi_truong("WEB2API_SSE_NGHE_TOI_DA_MOI_DANH_TINH", 8)


def nghe_toi_da_toan_cuc() -> int:
    """Trần người nghe SSE TOÀN CỤC (mặc định 256, env WEB2API_SSE_NGHE_TOI_DA_TOAN_CUC)."""
    return _so_nguyen_moi_truong("WEB2API_SSE_NGHE_TOI_DA_TOAN_CUC", 256)


def chu_ky_kiem_lai_khoa_giay() -> float:
    """Chu kỳ kiểm lại khoá trong vòng phát SSE (giây; mặc định 15, env WEB2API_SSE_KIEM_LAI_KHOA_GIAY)."""
    try:
        val = float(os.environ.get("WEB2API_SSE_KIEM_LAI_KHOA_GIAY", "15"))
        if val != val:  # NaN
            return 15.0
        return min(max(val, 1.0), 300.0)
    except (TypeError, ValueError):
        return 15.0


def nguon_cua_danh_tinh(danh_tinh: dict | None) -> str:
    """Danh tính người gửi để LÕI đóng dấu trường `nguon` của sự kiện.

    Quản trị → "quan_tri"; khoá khách → "khach:<id>"; còn lại → "khong_ro".
    """
    if not isinstance(danh_tinh, dict):
        return "khong_ro"
    if danh_tinh.get("loai") == "quan_tri":
        return "quan_tri"
    id_ = danh_tinh.get("id")
    if danh_tinh.get("loai") == "khach" and id_:
        return f"khach:{id_}"
    return "khong_ro"


def _do_sau_gia_tri(obj: Any, hien: int = 0) -> int:
    if isinstance(obj, dict):
        if not obj:
            return hien + 1
        return max(_do_sau_gia_tri(v, hien + 1) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        if not obj:
            return hien + 1
        return max(_do_sau_gia_tri(v, hien + 1) for v in obj)
    return hien


def chuan_hoa_su_kien_thiet_bi(su_kien_tho: dict, nguon: str) -> dict:
    """Kiểm tra sự kiện thiết bị do khách gửi và ĐÓNG DẤU trường `nguon` của lõi.

    - Mọi trường `nguon` trong thân đều bị ghi đè bằng `nguon` do lõi truyền vào — người gửi
      không đặt được.
    - `id`/`thuc_the` bắt buộc; LƯU Ý: quy tắc «id phải nhắm thực thể đang tồn tại trong danh
      mục lõi» do ĐƯỜNG GỌI kiểm (cần tra cứu bất đồng bộ), hàm này không kiểm tồn tại.
    - `loai` khớp ^[A-Za-z0-9_.:-]{1,64}$ (mặc định "trang_thai").
    - `gia_tri` phải serialize được thành JSON, ≤ 4096 byte sau serialize, độ sâu lồng ≤ 4.

    Ném LoiOpenAI 400/413 khi không hợp lệ. Trả dict sự kiện mới (không sửa dict gốc).
    """
    if not isinstance(su_kien_tho, dict):
        raise LoiOpenAI(400, "Thân sự kiện phải là đối tượng JSON", code="invalid_request")
    e_id = su_kien_tho.get("id") or su_kien_tho.get("thuc_the")
    if not isinstance(e_id, str) or not e_id.strip():
        raise LoiOpenAI(400, "Thiếu hoặc sai trường «id»", code="invalid_request")
    loai = su_kien_tho.get("loai") or "trang_thai"
    if not isinstance(loai, str) or not MAU_LOAI_SU_KIEN.fullmatch(loai):
        raise LoiOpenAI(
            400,
            "Trường «loai» không hợp lệ (mẫu: ^[A-Za-z0-9_.:-]{1,64}$)",
            code="invalid_request",
        )
    gia_tri = su_kien_tho.get("gia_tri")
    try:
        van_ban = json.dumps(gia_tri, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raise LoiOpenAI(400, "Trường «gia_tri» không serialize được thành JSON",
                        code="invalid_request") from None
    if len(van_ban.encode("utf-8")) > GIA_TRI_SU_KIEN_TOI_DA_BYTE:
        raise LoiOpenAI(
            413,
            f"Trường «gia_tri» vượt trần {GIA_TRI_SU_KIEN_TOI_DA_BYTE} byte",
            code="payload_too_large",
        )
    if _do_sau_gia_tri(gia_tri) > DO_SAU_GIA_TRI_TOI_DA:
        raise LoiOpenAI(
            400,
            f"Trường «gia_tri» lồng quá sâu (tối đa {DO_SAU_GIA_TRI_TOI_DA})",
            code="invalid_request",
        )
    return {
        "id": e_id.strip(),
        "thuc_the": e_id.strip(),
        "loai": loai,
        "gia_tri": gia_tri,
        "nguon": nguon,  # lõi đóng dấu — ghi đè mọi giá trị người gửi đặt
    }


# ───────────── Phát sự kiện SSE ─────────────

class BoPhatSuKienThietBi:
    """Bộ phân phối sự kiện SSE cho các client đang kết nối — có lọc theo danh tính (SEC-003)."""

    def __init__(self):
        self._nghe: list[dict] = []  # mỗi mục: {"q", "loai", "id", "pham_vi"}
        self._khoa = asyncio.Lock()

    def _so_nghe_cua_danh_tinh(self, loai: str, id_: str) -> int:
        return sum(1 for n in self._nghe if n["loai"] == loai and n["id"] == id_)

    async def dang_ky(
        self,
        loai: str = "quan_tri",
        id_: str = "quan_tri",
        pham_vi: Any = (),
    ) -> asyncio.Queue:
        """Đăng ký một người nghe kèm danh tính. Vượt trần (theo danh tính hoặc toàn cục)
        → LoiOpenAI 429 kèm Retry-After."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._khoa:
            if len(self._nghe) >= nghe_toi_da_toan_cuc():
                raise LoiOpenAI(
                    429,
                    "Quá nhiều người nghe sự kiện, thử lại sau.",
                    code="too_many_listeners",
                    headers={"Retry-After": str(RETRY_AFTER_NGHE_QUA_TRAN)},
                )
            if self._so_nghe_cua_danh_tinh(loai, id_) >= nghe_toi_da_moi_danh_tinh():
                raise LoiOpenAI(
                    429,
                    "Danh tính này đã mở quá nhiều luồng nghe sự kiện, thử lại sau.",
                    code="too_many_listeners",
                    headers={"Retry-After": str(RETRY_AFTER_NGHE_QUA_TRAN)},
                )
            try:
                cac_pham_vi = frozenset(pham_vi or ())
            except TypeError:
                cac_pham_vi = frozenset()
            self._nghe.append({"q": q, "loai": loai, "id": id_, "pham_vi": cac_pham_vi})
        return q

    async def huy(self, q: asyncio.Queue) -> None:
        async with self._khoa:
            self._nghe = [n for n in self._nghe if n["q"] is not q]

    @staticmethod
    def _duoc_nhan(nghe: dict, su_kien: dict) -> bool:
        """Lọc phát theo danh tính (SEC-003):
        - sự kiện phê duyệt (loai phe_duyet*) → CHỈ quản trị;
        - sự kiện thường → người nghe có phạm vi đọc (su_kien_doc; tương thích: su_kien hoặc *)."""
        loai = su_kien.get("loai") if isinstance(su_kien, dict) else None
        if isinstance(loai, str) and (loai in CAC_LOAI_PHE_DUYET or loai.startswith("phe_duyet")):
            return nghe.get("loai") == "quan_tri"
        return bool(nghe.get("pham_vi", frozenset()) & CAC_PHAM_VI_NGHE)

    async def phat(self, su_kien: dict) -> None:
        async with self._khoa:
            cac_nghe = [n for n in self._nghe if self._duoc_nhan(n, su_kien)]
        for nghe in cac_nghe:
            try:
                nghe["q"].put_nowait(su_kien)
            except asyncio.QueueFull:
                pass

    def phat_dong_bo(self, su_kien: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.phat(su_kien))
        except RuntimeError:
            pass


BO_PHAT = BoPhatSuKienThietBi()
