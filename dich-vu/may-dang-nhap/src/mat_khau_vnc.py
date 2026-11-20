# NGUỒN: web2api (mã nội bộ, không thuộc nguồn c2a)
"""Set and report the owner-configured VNC password for noVNC."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import novnc_bat_tat

log = logging.getLogger("may-dang-nhap.mat-khau-vnc")

#: Tên tệp mật khẩu VNC trong thư mục khoá (x11vnc đọc bằng -passwdfile).
TEN_TEP_MAT_KHAU = "mat-khau-vnc"
#: Tệp đánh dấu «đã từng đặt qua giao diện» (0600), giúp phân biệt với tệp mật khẩu cũ
#: có thể do entrypoint của bản trước sinh ra.
TEN_TEP_DANH_DAU = "mat-khau-vnc-chu-dat"
DUONG_SUPERVISOR_NOVNC = "/etc/supervisor/supervisord-novnc.conf"
MAU_MAT_KHAU = re.compile(r"[!-~]{1,8}")
LOI_MAT_KHAU = "Mật khẩu xem trình duyệt chỉ dùng 1–8 ký tự ASCII in được, không khoảng trắng."


def kiem_mat_khau_vnc(mat_khau: object) -> str:
    """Trả chuỗi rỗng nếu hợp lệ, nếu không trả lỗi không chứa mật khẩu."""
    if not isinstance(mat_khau, str) or not MAU_MAT_KHAU.fullmatch(mat_khau):
        return LOI_MAT_KHAU
    return ""


def _nap_lai_x11vnc(chay_lenh=None, thu_muc_khoa: Path | None = None) -> bool:
    """Nạp lại x11vnc để mật khẩu mới có hiệu lực ngay, không dựng lại container.

    Dùng bộ quản lý tiến trình runtime trong môi trường thật. Tham số chích lệnh
    giữ được test cũ thuần stdlib mà không chạy supervisorctl thật.
    """
    khoa = Path(thu_muc_khoa) if thu_muc_khoa is not None else Path("/khoa")
    if chay_lenh is None:
        return novnc_bat_tat.nap_lai_x11(khoa)
    try:
        ma = chay_lenh(["supervisorctl", "-c", DUONG_SUPERVISOR_NOVNC, "restart", "x11vnc"],
                       capture_output=True, timeout=10)
        ma = ma.returncode if hasattr(ma, "returncode") else ma
        return ma == 0
    except Exception as exc:  # noqa: BLE001 — mọi lỗi nạp lại đều chỉ là «chưa nạp lại»
        log.warning("nap lai x11vnc hong: %s", type(exc).__name__)
        return False


def ghi_mat_khau_vnc(mat_khau: str, thu_muc_khoa=None, chay_lenh=None) -> dict:
    """Ghi mật khẩu + tệp đánh dấu, rồi nạp lại x11vnc. Sai định dạng → ValueError."""
    loi = kiem_mat_khau_vnc(mat_khau)
    if loi:
        raise ValueError(loi)
    with novnc_bat_tat.KHOA_NOVNC:
        thu_muc = Path(thu_muc_khoa) if thu_muc_khoa is not None else Path("/khoa")
        thu_muc.mkdir(mode=0o700, parents=True, exist_ok=True)
        novnc_bat_tat._ghi_tep_0600(thu_muc / TEN_TEP_MAT_KHAU, mat_khau)
        novnc_bat_tat._ghi_tep_0600(thu_muc / TEN_TEP_DANH_DAU, "da-dat")
        try:
            da_nap_lai = _nap_lai_x11vnc(chay_lenh, thu_muc)
        except Exception as exc:  # mật khẩu đã lưu; reload lỗi không được làm lộ dữ liệu
            log.warning("nap lai x11vnc hong: %s", type(exc).__name__)
            da_nap_lai = False
        log.info("NV305: da ghi mat khau VNC, da_nap_lai=%s", da_nap_lai)
        return {"ok": True, "da_nap_lai": da_nap_lai}


def trang_thai_da_dat(thu_muc_khoa=None) -> bool:
    """True khi có cả mật khẩu hợp lệ lẫn dấu hiệu đặt qua giao diện."""
    thu_muc = Path(thu_muc_khoa) if thu_muc_khoa is not None else Path("/khoa")
    return novnc_bat_tat.da_dat_mat_khau(thu_muc)
