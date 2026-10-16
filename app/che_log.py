"""Che bí mật trong LOG HỆ THỐNG (stderr) ở gốc — NV13 / NV26.

Lọc áp dụng ở nơi tạo bản ghi log (nhà máy toàn cục) hoặc qua filter gắn vào handler.
Phân biệt định danh vận hành (tên model, đường dẫn, tên tệp) với khoá/token ngẫu nhiên.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app import kho

_TEN_BIEN_MOI_TRUONG = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_DOAN_LIEN_MACH = re.compile(r"[A-Za-z0-9_\-]{20,}")
_HEX_32 = re.compile(r"^[0-9a-fA-F]{32,}\Z")

# Mẫu (d): bot URL / bot token, Bearer, Authorization, api_key=, token=, key=
_MAU_BOT = re.compile(r"((?:/|\b)bot\d+:)([A-Za-z0-9_\-]+)")
_MAU_AUTH = re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer\s+)?)([A-Za-z0-9_\-./+=]+)")
_MAU_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9_\-./+=]+)")
_MAU_KEY = re.compile(r"(?i)\b((?:api_key|token|key)\s*[:=]\s*['\"]?)([A-Za-z0-9_\-./+=]+)(['\"]?)")


def _la_khoa_ngau_nhien(s: str) -> bool:
    """Kiểm tra hình khoá ngẫu nhiên hoặc băm hex:

    (a) độ dài ≥ 20 VÀ có ≥ 3 trong 4 lớp {thường, HOA, số, -/_} VÀ có ≥ 1 số VÀ ≥ 1 HOA;
    (b) toàn hex dài ≥ 32.
    Định danh toàn HOA/số/gạch (tên biến môi trường) hoặc thuần chữ thường + gạch/chấm không bị coi là khoá.
    """
    if _TEN_BIEN_MOI_TRUONG.fullmatch(s):
        return False
    # (b) toàn hex dài ≥ 32 (băm, khoá hex)
    if len(s) >= 32 and _HEX_32.fullmatch(s):
        return True
    # (a) độ dài ≥ 20 và có cả HOA lẫn số, cùng ít nhất một lớp nữa (thường hoặc gạch)
    if len(s) >= 20:
        co_thuong = any(c.islower() for c in s)
        co_hoa = any(c.isupper() for c in s)
        co_so = any(c.isdigit() for c in s)
        co_gach = any(c in "-_" for c in s)
        if co_hoa and co_so and (co_thuong or co_gach):
            return True
    return False


def che_log(van_ban: str) -> str:
    """Che bí mật trong log hệ thống theo hình khoá ngẫu nhiên, giữ nguyên định danh vận hành."""
    if not van_ban:
        return van_ban

    # (c) trùng đúng giá trị khoá quản trị hiện hành
    try:
        from app import xac_thuc
        khoa = xac_thuc.khoa_quan_tri()
    except Exception:
        khoa = ""
    if khoa:
        van_ban = van_ban.replace(khoa, kho.che(khoa))

    # (d) cấu trúc bot URL, Bearer, Authorization, api_key=, token=, key=
    van_ban = _MAU_BOT.sub(lambda m: m.group(1) + kho.che(m.group(2)), van_ban)
    van_ban = _MAU_AUTH.sub(lambda m: m.group(1) + kho.che(m.group(2)), van_ban)
    van_ban = _MAU_BEARER.sub(lambda m: m.group(1) + kho.che(m.group(2)), van_ban)
    van_ban = _MAU_KEY.sub(lambda m: m.group(1) + kho.che(m.group(2)) + m.group(3), van_ban)

    # (a) & (b) các đoạn liền mạch dạng token ngẫu nhiên hoặc hex
    return _DOAN_LIEN_MACH.sub(
        lambda m: kho.che(m.group()) if _la_khoa_ngau_nhien(m.group()) else m.group(), van_ban)


def _che_doi_so(a: Any) -> Any:
    """Chỉ che đối số str và đối tượng ngoại lệ; giữ nguyên mọi kiểu khác (Decimal, Path, số...)."""
    if isinstance(a, str):
        try:
            return che_log(a)
        except Exception:
            return "<không che được>"
    if isinstance(a, BaseException):
        try:
            return che_log(str(a))
        except Exception:
            return "<không che được>"
    return a


class CheBiMat(logging.Filter):
    """Filter che bí mật cho mọi log record đi qua logger/handler được gắn."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            has_args = bool(record.args)
            if not has_args:
                # args rỗng (f-string hoặc log trực tiếp ngoại lệ): che msg
                try:
                    if isinstance(record.msg, str):
                        record.msg = che_log(record.msg)
                    else:
                        record.msg = che_log(str(record.msg))
                except Exception:
                    record.msg = "<không che được>"
            else:
                # args khác rỗng: KHÔNG che chuỗi mẫu record.msg
                if isinstance(record.args, tuple):
                    record.args = tuple(_che_doi_so(a) for a in record.args)
                elif isinstance(record.args, dict):
                    record.args = {k: _che_doi_so(v) for k, v in record.args.items()}
        except Exception:
            pass

        # T1: không dựng lại ngoại lệ; che ở dạng văn bản exc_text, giữ nguyên record.exc_info
        try:
            if record.exc_info:
                if not getattr(record, "exc_text", None):
                    try:
                        record.exc_text = che_log(logging.Formatter().formatException(record.exc_info))
                    except Exception:
                        record.exc_text = None
                else:
                    record.exc_text = che_log(record.exc_text)
            if getattr(record, "stack_info", None):
                record.stack_info = che_log(record.stack_info)
        except Exception:
            pass

        return True


def cai_dat() -> None:
    """Che bí mật ở NƠI TẠO bản ghi (không phụ thuộc handler nào). Gọi nhiều lần vẫn chỉ bọc một lần."""
    cu = logging.getLogRecordFactory()
    if getattr(cu, "_che_bi_mat", False):
        return
    loc = CheBiMat()

    def nha_may(*args, **kwargs):
        record = cu(*args, **kwargs)
        loc.filter(record)
        return record

    nha_may._che_bi_mat = True
    logging.setLogRecordFactory(nha_may)
