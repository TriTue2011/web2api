# W2A NV305: mật khẩu xem trình duyệt (VNC) do chủ máy tự đặt qua giao diện.
#
# Chỉ dùng thư viện chuẩn để test nhẹ được mà không import app FastAPI nặng.
# Mật khẩu KHÔNG BAO GIỜ được ghi vào log hay trả về trong phản hồi: các hàm ở đây
# chỉ trả {"ok", "da_nap_lai"} / bool, và log chỉ ghi trạng thái chung.
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("may-dang-nhap.mat-khau-vnc")

#: Tên tệp mật khẩu VNC trong thư mục khoá (x11vnc đọc bằng -passwdfile).
TEN_TEP_MAT_KHAU = "mat-khau-vnc"
#: Tệp đánh dấu «đã từng đặt qua giao diện» (rỗng, 0600): phân biệt với mật khẩu
#: ngẫu nhiên do entrypoint sinh lúc đầu — entrypoint không ghi đè tệp đã có.
TEN_TEP_DANH_DAU = "mat-khau-vnc-chu-dat"
#: Cấu hình supervisord chạy noVNC (có chương trình x11vnc) — dùng để nạp lại.
DUONG_SUPERVISOR_NOVNC = "/etc/supervisor/supervisord-novnc.conf"
#: RFB chỉ dùng 8 ký tự đầu; mật khẩu 1–8 ký tự ASCII in được, không khoảng trắng
#: (không dấu: ký tự ngoài ASCII sẽ bị cắt/méo khi qua DES của RFB).
MAU_MAT_KHAU = re.compile(r"[!-~]{1,8}")


def kiem_mat_khau_vnc(mat_khau) -> str | None:
    """Trả None nếu hợp lệ, ngược lại trả thông điệp lỗi KHÔNG chứa mật khẩu."""
    if not isinstance(mat_khau, str) or not MAU_MAT_KHAU.fullmatch(mat_khau):
        return ("Mật khẩu xem trình duyệt chỉ dùng 1–8 ký tự "
                "(chữ, số, ký tự thường; không khoảng trắng, không dấu)")
    return None


def _ghi_tep_0600(duong: Path, noi_dung: str) -> None:
    """Ghi nguyên tử (tệp tạm cùng thư mục + os.replace), quyền 0600."""
    duong.parent.mkdir(parents=True, exist_ok=True)
    fd, tam = tempfile.mkstemp(dir=str(duong.parent), prefix=".mat-khau-vnc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(noi_dung)
        os.chmod(tam, 0o600)
        os.replace(tam, duong)
    except BaseException:
        try:
            os.unlink(tam)
        except OSError:
            pass
        raise


def _nap_lai_x11vnc(chay_lenh=None) -> bool:
    """Nạp lại x11vnc để mật khẩu mới có hiệu lực ngay, không dựng lại container.

    Trả False (không ném) khi supervisorctl vắng mặt, x11vnc chưa chạy (noVNC tắt)
    hay lệnh hỏng — mật khẩu đã ghi vẫn có hiệu lực khi x11vnc khởi động lại.
    """
    chay = chay_lenh or (lambda lenh, **kw: subprocess.run(
        lenh, capture_output=True, timeout=10, **kw).returncode)
    try:
        ma = chay(["supervisorctl", "-c", DUONG_SUPERVISOR_NOVNC, "restart", "x11vnc"])
        ma = ma.returncode if hasattr(ma, "returncode") else ma
        if ma == 0:
            return True
        log.warning("nap lai x11vnc hong (ma=%s)", ma)
        return False
    except Exception as exc:  # noqa: BLE001 — mọi lỗi nạp lại đều chỉ là «chưa nạp lại»
        log.warning("nap lai x11vnc hong: %s", type(exc).__name__)
        return False


def ghi_mat_khau_vnc(mat_khau: str, thu_muc_khoa=None, chay_lenh=None) -> dict:
    """Ghi mật khẩu + tệp đánh dấu, rồi nạp lại x11vnc. Sai định dạng → ValueError."""
    loi = kiem_mat_khau_vnc(mat_khau)
    if loi:
        raise ValueError(loi)
    thu_muc = Path(thu_muc_khoa) if thu_muc_khoa is not None else Path("/khoa")
    _ghi_tep_0600(thu_muc / TEN_TEP_MAT_KHAU, mat_khau)
    _ghi_tep_0600(thu_muc / TEN_TEP_DANH_DAU, "")
    da_nap_lai = _nap_lai_x11vnc(chay_lenh)
    log.info("NV305: da ghi mat khau VNC, da_nap_lai=%s", da_nap_lai)
    return {"ok": True, "da_nap_lai": da_nap_lai}


def trang_thai_da_dat(thu_muc_khoa=None) -> bool:
    """True chỉ khi đã từng đặt qua giao diện (có tệp đánh dấu)."""
    thu_muc = Path(thu_muc_khoa) if thu_muc_khoa is not None else Path("/khoa")
    return (thu_muc / TEN_TEP_DANH_DAU).exists()
