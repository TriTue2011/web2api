# NGUỒN: web2api (mã nội bộ, không thuộc nguồn c2a)
"""Công tắc noVNC runtime cho dịch vụ may-dang-nhap (NV305-bao-mat).

Chỉ dùng stdlib để test được mà không cần FastAPI/patchright (máy làm việc không
import được src.main — cùng giới hạn như NV305).

Thiết kế:
- Cờ bền ``novnc-bat`` trong volume /khoa («bat»/«tat», ghi nguyên tử 0600):
  cờ có thì cờ thắng biến môi trường lúc khởi động (entrypoint đọc cờ trước).
  Cờ này KHÔNG phải bí mật — nó chỉ nói «muốn xem hay không», mật khẩu vẫn
  nằm riêng ở tệp mat-khau-vnc và không bao giờ bị công tắc này thay đổi/xoá.
- Bật khi chưa đặt mật khẩu qua giao diện → TỪ CHỐI (không bao giờ chạy xem
  không mật khẩu, nhất quán bo-sung: không -nopw). Tắt thì không động vào mật khẩu.
- Chương trình do supervisor quản (conf novnc) thì dùng supervisorctl start/stop;
  nếu không thì spawn/kill trực tiếp và ghi PID file vào /khoa (để tắt được cả
  sau khi dịch vụ khởi động lại). Khi tắt, chỉ giết PID khớp lệnh (đọc
  /proc/<pid>/cmdline) để không giết nhầm PID đã tái sử dụng sau khởi động lại.
- Lệnh spawn đúng như supervisord-novnc.conf: x11vnc -passwdfile (không -nopw),
  websockify 0.0.0.0:6080 → 127.0.0.1:5900.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# Một khóa cho các worker thread trong process dịch vụ; ghi mật khẩu gọi reload
# lồng nhau nên phải reentrant. Không phải khóa liên process/multiworker.
KHOA_NOVNC = threading.RLock()

TEN_TEP_CO = "novnc-bat"
TEN_TEP_MAT_KHAU = "mat-khau-vnc"
TEN_TEP_DANH_DAU = "mat-khau-vnc-chu-dat"
PID_X11 = "novnc-x11vnc.pid"
PID_WS = "novnc-websockify.pid"
CONF_SUPERVISOR_NOVNC = "/etc/supervisor/supervisord-novnc.conf"
CONG_X11 = 5900
CONG_WS = 6080
LENH_X11 = ["/usr/bin/x11vnc", "-display", ":99", "-passwdfile", "/khoa/mat-khau-vnc",
            "-forever", "-shared", "-rfbport", "5900", "-listen", "127.0.0.1", "-quiet"]
LENH_WS = ["/usr/bin/websockify", "--web=/usr/share/novnc/", "0.0.0.0:6080", "127.0.0.1:5900"]
LOI_CHUA_DAT = ("Chưa đặt mật khẩu xem trình duyệt qua giao diện — "
                "hãy đặt mật khẩu trước khi bật màn hình xem.")


def _ghi_tep_0600(duong_dan: Path, noi_dung: str) -> None:
    """Ghi nguyên tử (tệp tạm cùng thư mục + os.replace), quyền 0600."""
    import tempfile
    fd, tam = tempfile.mkstemp(dir=str(duong_dan.parent), prefix=duong_dan.name + ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(noi_dung)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tam, 0o600)
        os.replace(tam, duong_dan)
    except BaseException:
        try:
            os.unlink(tam)
        except OSError:
            pass
        raise


# ── Cờ bền ──────────────────────────────────────────────────────────────────


def doc_co(khoa_dir: Path) -> bool | None:
    """True = bật, False = tắt/cờ hỏng, chỉ thiếu cờ mới trả None để dùng env."""
    try:
        nd = (Path(khoa_dir) / TEN_TEP_CO).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        log.warning("Không đọc được cờ noVNC; giữ tắt.")
        return False
    if nd == "bat":
        return True
    if nd == "tat":
        return False
    log.warning("Cờ noVNC có nội dung lạ — giữ tắt.")
    return False


def ghi_co(khoa_dir: Path, bat: bool) -> None:
    _ghi_tep_0600(Path(khoa_dir) / TEN_TEP_CO, "bat" if bat else "tat")


def xoa_co(khoa_dir: Path) -> None:
    try:
        os.unlink(Path(khoa_dir) / TEN_TEP_CO)
    except OSError:
        pass


# ── Mật khẩu ────────────────────────────────────────────────────────────────


def da_dat_mat_khau(khoa_dir: Path) -> bool:
    """Đã đặt qua giao diện iff có tệp đánh dấu (tệp cũ sót lại không tính)."""
    return ((Path(khoa_dir) / TEN_TEP_DANH_DAU).is_file()
            and (Path(khoa_dir) / TEN_TEP_MAT_KHAU).is_file())


def doc_mat_khau(khoa_dir: Path) -> str:
    """Đọc mật khẩu VNC — chỉ khi có tệp đánh dấu; không có thì ValueError.

    Chỉ lõi gọi qua endpoint đã xác thực lại mới được nhận giá trị này; hàm này
    không bao giờ ghi giá trị vào log.
    """
    if not da_dat_mat_khau(khoa_dir):
        raise ValueError(LOI_CHUA_DAT)
    from .mat_khau_vnc import kiem_mat_khau_vnc

    try:
        mat_khau = (Path(khoa_dir) / TEN_TEP_MAT_KHAU).read_text(encoding="utf-8")
    except UnicodeError:
        raise ValueError(LOI_CHUA_DAT) from None
    if kiem_mat_khau_vnc(mat_khau):
        raise ValueError(LOI_CHUA_DAT)
    return mat_khau


# ── Cổng / tiến trình / supervisor (mặc định thật, test chích giả) ──────────


def cong_nghe(cong: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", cong), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn_thuc(lenh: list[str]) -> int:
    p = subprocess.Popen(lenh, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return p.pid


def _kill_thuc(pid: int, lenh: list[str]) -> None:
    # Giữ tham chiếu kernel trước khi kiểm cmdline: PID tái sử dụng không nhận signal.
    fd = os.pidfd_open(pid)
    try:
        if not _pid_khop(pid, lenh):
            return
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.1)
            try:
                signal.pidfd_send_signal(fd, 0)
            except ProcessLookupError:
                return
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
    finally:
        os.close(fd)


def _pid_khop(pid: int, lenh: list[str]) -> bool:
    """Khớp toàn bộ argv, không nhận tên chương trình nằm trong đối số tuỳ ý."""
    if pid <= 1:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8").rstrip("\0").split("\0")
    except (OSError, UnicodeError):
        return False
    # websockify là script Python: kernel thêm interpreter đứng trước đường script.
    if lenh[0] == LENH_WS[0] and cmd and re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(cmd[0]).name):
        cmd = cmd[1:]
    return cmd == lenh


def lenh_x11(khoa_dir: Path) -> list[str]:
    lenh = list(LENH_X11)
    lenh[lenh.index("-passwdfile") + 1] = str(Path(khoa_dir) / TEN_TEP_MAT_KHAU)
    return lenh


def nap_lai_x11(khoa_dir: Path) -> bool:
    """Nạp mật khẩu mới bằng lệnh cố định, không shell/không bí mật trong argv."""
    with KHOA_NOVNC:
        if doc_co(khoa_dir) is False:
            return False
        tt = _supervisor_thuc("status", "x11vnc")
        if tt is not None:
            # Không tự bật lại màn hình đã tắt. FATAL có thể do chưa có mật khẩu lúc boot.
            if tt == "STOPPED":
                return False
            return bool(_supervisor_thuc("restart", "x11vnc"))
        pid_file = Path(khoa_dir) / PID_X11
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        lenh = lenh_x11(khoa_dir)
        if not _pid_khop(pid, lenh):
            return False
        try:
            _kill_thuc(pid, lenh)
            pid_moi = _spawn_thuc(lenh)
            _ghi_tep_0600(pid_file, str(pid_moi))
        except OSError:
            return False
        return True


def _supervisor_thuc(*doi_so: str) -> Any:
    """Giao thức: status → chuỗi trạng thái hoặc None (không do supervisor quản);
    start/stop → True/False theo mã thoát."""
    try:
        kq = subprocess.run(
            ["supervisorctl", "-c", CONF_SUPERVISOR_NOVNC, *doi_so],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None if doi_so and doi_so[0] == "status" else False
    if doi_so and doi_so[0] == "status":
        dong = (kq.stdout or "").strip().splitlines()
        if not dong:
            return None
        phan = dong[0].split()
        if len(phan) < 2 or "ERROR" in dong[0] or "no such process" in dong[0].lower():
            return None
        return phan[1]
    return kq.returncode == 0


def _khoi_dong(ten: str, lenh: list[str], pid_file: Path,
               spawn: Callable[[list[str]], int],
               supervisor: Callable[..., Any]) -> None:
    tt = supervisor("status", ten)
    if tt is not None:  # Do supervisor quản
        if tt == "RUNNING":
            return
        if not supervisor("start", ten):
            raise RuntimeError(f"supervisorctl start {ten} thất bại.")
        return
    # Lượt bật trước có thể đã timeout trong khi tiến trình vẫn đang mở cổng.
    # Giữ PID còn khớp để retry tiếp tục chờ và tat() vẫn dừng được tiến trình gốc.
    try:
        pid_cu = int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        pid_cu = None
    if pid_cu is not None and _pid_khop(pid_cu, lenh):
        return
    pid = spawn(lenh)
    _ghi_tep_0600(pid_file, str(pid))


def _dung(ten: str, pid_file: Path, lenh: list[str],
          kill: Callable[[int, list[str]], None],
          supervisor: Callable[..., Any],
          kiem_pid: Callable[[int, list[str]], bool]) -> bool:
    """Dừng một chương trình; trả True nếu đã tác động (giết hoặc stop)."""
    tac_dong = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        if pid is not None and pid > 1 and kiem_pid(pid, lenh):
            kill(pid, lenh)
            tac_dong = True
        elif pid is not None:
            log.warning("PID %s cho %s không khớp lệnh — bỏ tệp PID cũ, không giết.", pid, ten)
        pid_file.unlink(missing_ok=True)
    tt = supervisor("status", ten)
    if tt is not None and tt != "STOPPED":
        return bool(supervisor("stop", ten)) or tac_dong
    return tac_dong


def _cho_cong(kiem_cong: Callable[[int], bool], ngu: Callable[[float], None]) -> tuple[bool, bool]:
    for _ in range(20):
        x11 = kiem_cong(CONG_X11)
        ws = kiem_cong(CONG_WS)
        if x11 and ws:
            return True, True
        ngu(0.1)
    return kiem_cong(CONG_X11), kiem_cong(CONG_WS)


# ── API chính ───────────────────────────────────────────────────────────────


def trang_thai(khoa_dir: Path, kiem_cong: Callable[[int], bool] = cong_nghe,
               bat_mac_dinh: bool = False) -> dict:
    co = doc_co(khoa_dir)
    bat = co if co is not None else bat_mac_dinh
    x11 = kiem_cong(CONG_X11)
    ws = kiem_cong(CONG_WS)
    return {"bat": bat, "x11vnc": x11, "websockify": ws,
            "dang_chay": bool(x11 and ws), "da_dat": da_dat_mat_khau(khoa_dir)}


def bat(khoa_dir: Path, kiem_cong: Callable[[int], bool] = cong_nghe,
        spawn: Callable[[list[str]], int] = _spawn_thuc,
        supervisor: Callable[..., Any] = _supervisor_thuc,
        ngu: Callable[[float], None] = time.sleep) -> dict:
    """Bật màn hình xem. Từ chối khi chưa đặt mật khẩu. Chỉ ghi cờ khi chạy đủ cả hai."""
    with KHOA_NOVNC:
        khoa_dir = Path(khoa_dir)
        doc_mat_khau(khoa_dir)  # kiểm cả mật khẩu, không chỉ sự tồn tại của marker
        if kiem_cong(CONG_X11) and kiem_cong(CONG_WS):
            ghi_co(khoa_dir, True)
            return {"bat": True, "da_ap_dung": False, "dang_chay": True,
                    "x11vnc": True, "websockify": True}
        if not kiem_cong(CONG_X11):
            _khoi_dong("x11vnc", lenh_x11(khoa_dir), khoa_dir / PID_X11, spawn, supervisor)
        if not kiem_cong(CONG_WS):
            _khoi_dong("novnc", LENH_WS, khoa_dir / PID_WS, spawn, supervisor)
        x11, ws = _cho_cong(kiem_cong, ngu)
        if x11 and ws:
            ghi_co(khoa_dir, True)
            return {"bat": True, "da_ap_dung": True, "dang_chay": True,
                    "x11vnc": True, "websockify": True}
        log.warning("Bật noVNC chưa đủ: x11vnc=%s websockify=%s (cờ chưa ghi).", x11, ws)
        raise RuntimeError("Bật màn hình xem chưa đủ: một trong hai chương trình chưa nghe cổng.")


def tat(khoa_dir: Path, kiem_cong: Callable[[int], bool] = cong_nghe,
        kill: Callable[[int, list[str]], None] = _kill_thuc,
        supervisor: Callable[..., Any] = _supervisor_thuc,
        kiem_pid: Callable[[int, list[str]], bool] = _pid_khop) -> dict:
    """Lưu cờ tắt bền rồi dừng chương trình; KHÔNG động vào mật khẩu."""
    with KHOA_NOVNC:
        khoa_dir = Path(khoa_dir)
        co_truoc = doc_co(khoa_dir)
        ghi_co(khoa_dir, False)
        tac_dong = co_truoc is not False
        tac_dong = _dung("novnc", khoa_dir / PID_WS, LENH_WS, kill, supervisor, kiem_pid) or tac_dong
        tac_dong = _dung("x11vnc", khoa_dir / PID_X11, lenh_x11(khoa_dir), kill, supervisor, kiem_pid) or tac_dong
        x11 = kiem_cong(CONG_X11)
        ws = kiem_cong(CONG_WS)
        if x11 or ws:
            raise RuntimeError("Chưa dừng đủ các chương trình màn hình xem.")
        return {"bat": False, "da_ap_dung": bool(tac_dong), "dang_chay": bool(x11 and ws),
                "x11vnc": x11, "websockify": ws}
