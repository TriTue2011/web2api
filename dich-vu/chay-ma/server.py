"""Máy chủ dịch vụ chay-ma (NV171): chạy mã Python do người dùng/mô hình đưa vào
CHỈ trong container phụ dùng-một-lần cách ly.

Kiến trúc an toàn (theo spec NV171, hạng B, rủi ro nguy_hiem):
- KHÔNG BAO GIỜ chạy mã trong tiến trình lõi hay container lõi: mỗi lượt chạy
  là một `docker run` dùng-một-lần (--rm) với cờ cách ly ĐẦY ĐỦ:
  `--network none`, `--read-only` + tmpfs /tmp có trần, user không-root
  (65534:65534), `--cap-drop ALL`, `no-new-privileges`, `--cpus/--memory/
  --memory-swap/--pids-limit`, `--pull never`, bind-mount duy nhất là thư mục
  tạm của lần chạy (do hệ thống tạo, id ngẫu nhiên) → /lam-viec, không mount
  dữ liệu lõi, không docker.sock, không thiết bị.
- Môi trường trong container là WHITELIST tối thiểu (PATH, HOME, LANG…);
  tiến trình docker client cũng chạy với env tối thiểu — bí mật của lõi
  (biến môi trường, khoá) không được lộ vào môi trường chạy mã.
- Lớp thứ hai trong container: bootstrap đặt RLIMIT_CPU/RLIMIT_AS/RLIMIT_FSIZE
  SAU exec (tránh deadlock preexec_fn của server đa luồng).
- Hết giờ/tràn/lỗi/xong: giết cả process group của docker client và
  `finally` LUÔN `docker kill` + `docker rm -f` container theo tên
  (NV171-SUA C2 — không container mồ côi); `--log-driver none` để daemon
  không ghi đĩa máy chủ; giờ tường trong container bằng `timeout`.
  Đầu ra stdout/stderr bị cắt ở trần và container bị diệt sớm khi phun
  vượt trần cứng (chống đầy bộ nhớ dịch vụ).

Nguồn port (LUẬT PORT, chủ máy 19/10): kho CỦA CHÍNH CHỦ MÁY,
đường đọc /opt/claude-c2a/chatgpt2api đã KHÔNG CÒN trên VM này nên dùng bản
sao ~/workspace/c2a-nv146 (cùng commit d879b03 của kho gốc). Chép từ
services/code_runner.py (417 dòng): boc_code_python, co_the_chay (NV171-SUA
C3: đổi tên `goi_y_som` + hạ vai trò xuống GỢI Ý SỚM — không phải ranh giới
an toàn; ranh giới thật là cách ly container) (+ các danh
sách _KHONG_TU_DU/_CHO_NGUOI/_NGUY_HIEM), _moi_truong_sach (ĐÃ ĐỔI: bỏ biến
proxy trỏ địa chỉ chết — vì --network none là cách ly mạng THẬT, không cần
lừa thư viện HTTP), chan_doan_loi, kiem_tinh (+ _ten_da_dinh_nghia/
_ten_chua_dinh_nghia/_chu_khong_phai_ascii), _RUNNER_BOOTSTRAP (giữ làm lớp
thứ hai trong container), _diet_nhom_tien_trinh. KHÔNG port chay() chạy
subprocess python trực tiếp (thay bằng docker run cách ly), KHÔNG port
services/code_pipeline.py (chia việc/gộp code cho pipeline agent của c2a —
không thuộc phạm vi «chạy đoạn mã, trả đầu ra»).

Đường KHÔNG cần khoá (ngoại lệ có chủ đích, duy nhất): GET /suc-khoe — chỉ
trả {"ok": true}, không đọc hay lộ trạng thái nào. Mọi đường khác đi qua
kiểm Bearer TRƯỚC khi đọc thân yêu cầu.

Chỉ dùng thư viện chuẩn (không thêm gói).
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dich_vu.chay_ma")

# ── Trần ───────────────────────────────────────────────────────────────────
HET_GIO_MAC_DINH = 20          # giây chạy mã mặc định (c2a: HAN_GIAY)
HAN_GIAY_TOI_DA = 60           # trần han_giay do khách yêu cầu
TRAN_MA = 40_000               # ký tự mã nguồn (c2a: TRAN_KY_TU_CODE)
TRAN_THAN = 1024 * 1024        # byte thân JSON yêu cầu
TRAN_SO_TEP = 8                # số tệp đầu vào tối đa
TRAN_TEP_BYTE = 64 * 1024      # byte mỗi tệp đầu vào
TRAN_TONG_TEP = 256 * 1024     # tổng byte tệp đầu vào
TRAN_DAU_RA_BYTE = 65_536      # byte mỗi luồng stdout/stderr giữ lại
TRAN_CUNG_BYTE = 2 * 1024 * 1024  # byte: vượt thì diệt container ngay
TRAN_TEP_RA = 16               # số tệp ra tối đa trả về
TRAN_TEP_RA_BYTE = 64 * 1024   # byte mỗi tệp ra trả về
MAU_TEN_TEP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TEN_TEP_MA = "ma.py"
DIEM_MOUNT = "/lam-viec"
ANH_MAC_DINH = ("python:3.13-slim@sha256:"
                "8fb4cfa1a2616d7b8e0c2175cc6ad68f5729c34ea8488c0b360d2934b7be9024")

# Môi trường whitelist tối thiểu TRONG container chạy mã (c2a: _moi_truong_sach,
# đã bỏ http_proxy/https_proxy trỏ địa chỉ chết vì --network none cách ly thật).
MOI_TRUONG_CONTAINER = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}

_RAM_TRAN_BYTE = 1024 * 1024 * 1024   # mặc định bootstrap; bị ghi đè bởi cấu hình
_FILE_TRAN_BYTE = 64 * 1024 * 1024   # 64 MB mỗi file (chống ghi đầy đĩa)


class LoiYeuCau(Exception):
    """Yêu cầu sai khuôn — mang mã HTTP, không lộ chi tiết nội bộ."""

    def __init__(self, ma: int, loi: str):
        super().__init__(loi)
        self.ma = ma
        self.loi = loi


# ── Port c2a: boc_code_python / goi_y_som (c2a: co_the_chay) / kiem_tinh ──

_KHOI_CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)

_KHONG_TU_DU = (
    "from services", "import services", "from api", "import api",
    "from utils", "import utils", "from captcha", "import chromadb",
    "import fastapi", "from fastapi", "import curl_cffi", "import httpx",
    "import requests", "import torch", "import numpy", "import pandas",
    "import playwright", "from playwright",
)
_CHO_NGUOI = ("input(", "sys.stdin", "while true:", "while 1:")
_NGUY_HIEM = (
    "os.system", "subprocess", "shutil.rmtree", "os.remove", "os.unlink",
    "socket.", "os.fork", "eval(", "exec(", "__import__",
    "open(\"/", "open('/",
)


def boc_code_python(text: str) -> str:
    """Lấy code Python từ câu trả lời của model (port c2a, giữ nguyên)."""
    khoi = _KHOI_CODE_RE.findall(text or "")
    if khoi:
        return "\n\n".join(k.strip() for k in khoi if k.strip())
    t = (text or "").strip()
    if re.search(r"(?m)^\s*(def |class |import |from \w+ import )", t):
        return t
    return ""


def goi_y_som(code: str) -> tuple[bool, str]:
    """GỢI Ý SỚM (port c2a `co_the_chay`, ĐỔI VAI TRÒ ở NV171-SUA C3):
    mã này có ĐÁNG TỐN một lượt container để chạy thử không — mã rỗng/quá
    dài, mã chắc chắn chờ người gõ hoặc không tự đủ (thiếu dịch vụ của c2a)
    thì bỏ qua sớm cho khỏi tốn lượt chạy.

    ĐÂY KHÔNG PHẢI RANH GIỚI AN TOÀN và không được tài liệu nào mô tả như
    vậy: nó là danh sách dấu hiệu chuỗi (kế thừa c2a), lách được dễ dàng
    (nối chuỗi, getattr, importlib…). Mã lọt qua gợi ý này VẪN chỉ chạy
    trong container cách ly — cách ly container (--network none, rootfs
    chỉ đọc, user 65534, cap-drop ALL, trần CPU/RAM/pids/thời gian) mới là
    ranh giới an toàn thật. Trả (đáng chạy thử không, lý do nếu không)."""
    c = (code or "").strip()
    if not c:
        return False, "không bóc được khối code Python nào"
    if len(c) > TRAN_MA:
        return False, f"code dài {len(c)} ký tự, vượt trần {TRAN_MA}"
    low = c.lower()
    for dau in _NGUY_HIEM:
        if dau in low:
            return False, f"code có {dau!r} — gợi ý sớm: nhiều khả năng chạm hệ thống, không tốn lượt chạy thử"
    for dau in _CHO_NGUOI:
        if dau in low:
            return False, f"code có {dau!r} — chờ người gõ hoặc chạy vô hạn"
    for dau in _KHONG_TU_DU:
        if dau in low:
            return False, f"code cần {dau!r} — không tự đủ để chạy riêng"
    return True, ""


#: Tên cũ thời NV171 — giữ làm alias để mã/test cũ còn import được; vai trò
#: đã đổi thành «gợi ý sớm» như docstring của goi_y_som (NV171-SUA C3).
co_the_chay = goi_y_som


def chan_doan_loi(stderr: str, code: str) -> str:
    """Biến traceback thô thành chẩn đoán ngắn (port c2a, giữ nguyên)."""
    err = (stderr or "").strip()
    if not err:
        return ""
    dong = err.splitlines()
    loai = ""
    for d in reversed(dong):
        if re.match(r"^\w+(Error|Exception|Warning)\b", d.strip()):
            loai = d.strip()
            break
    if not loai:
        loai = dong[-1].strip()
    so_dong = 0
    for d in dong:
        m = re.search(r'File "[^"]*", line (\d+)', d)
        if m:
            so_dong = int(m.group(1))
    ra = [f"Lỗi khi chạy: {loai}"]
    if so_dong:
        cac_dong = (code or "").splitlines()
        if 1 <= so_dong <= len(cac_dong):
            ra.append(f"Tại dòng {so_dong}: {cac_dong[so_dong - 1].strip()}")
        else:
            ra.append(f"Tại dòng {so_dong}")
    if "AssertionError" in loai and len(loai) < 20:
        for d in reversed(dong):
            if "assert" in d:
                ra.append(f"Câu assert thất bại: {d.strip()}")
                break
    return "\n".join(ra)


def _ten_da_dinh_nghia(cay) -> set[str]:
    import ast
    ten: set[str] = set()
    for n in ast.walk(cay):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            ten.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ten.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                ten.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.arg):
            ten.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            ten.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            ten.update(n.names)
        elif isinstance(n, ast.alias):
            ten.add((n.asname or n.name).split(".")[0])
    return ten


def _ten_chua_dinh_nghia(cay) -> list[str]:
    import ast
    import builtins
    co = _ten_da_dinh_nghia(cay) | set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    thieu: list[str] = []
    for n in ast.walk(cay):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in co:
            dong = getattr(n, "lineno", 0)
            thieu.append(f"{n.id} (dòng {dong})")
    da: set[str] = set()
    ra = []
    for t in thieu:
        k = t.split(" ")[0]
        if k in da:
            continue
        da.add(k)
        ra.append(t)
    return ra[:8]


def _chu_khong_phai_ascii(code: str) -> list[str]:
    import io
    import tokenize
    ra: list[str] = []
    _CHU_TRONG_FSTRING = getattr(tokenize, "FSTRING_MIDDLE", -1)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT, _CHU_TRONG_FSTRING):
                continue
            xau = [c for c in tok.string if ord(c) > 127]
            if xau:
                ra.append(f"dòng {tok.start[0]}: {tok.string.strip()[:40]!r}")
    except Exception:
        return []
    return ra[:5]


def kiem_tinh(code: str) -> str:
    """Soi code KHÔNG cần chạy (port c2a, giữ nguyên logic)."""
    import ast
    c = (code or "").strip()
    if not c:
        return ""
    try:
        cay = ast.parse(c)
    except SyntaxError as exc:
        dong = exc.lineno or 0
        cac = c.splitlines()
        mo_ta = f"Lỗi CÚ PHÁP: {exc.msg}"
        if 1 <= dong <= len(cac):
            mo_ta += f"\nTại dòng {dong}: {cac[dong - 1].strip()}"
        return mo_ta
    except Exception:
        return ""
    van_de: list[str] = []
    la = _chu_khong_phai_ascii(c)
    if la:
        van_de.append("Có ký tự KHÔNG phải ASCII lọt vào phần cú pháp (tên biến/toán "
                      "tử). Cú pháp code phải viết bằng ký hiệu ASCII; tiếng Việt chỉ "
                      "được ở chuỗi và chú thích. Chỗ sai: " + "; ".join(la))
    thieu = _ten_chua_dinh_nghia(cay)
    if thieu:
        van_de.append("Dùng tên CHƯA được định nghĩa hay import: " + ", ".join(thieu))
    if not van_de:
        return ""
    return ("Soi code (không chạy) thấy các lỗi sau — đây là lỗi xác định, không "
            "phải nhận xét chủ quan:\n- " + "\n- ".join(van_de))


# Bootstrap rlimit đặt SAU exec trong container (port c2a _RUNNER_BOOTSTRAP,
# giữ nguyên — tránh deadlock preexec_fn của server đa luồng).
BOOTSTRAP = r"""
import runpy
import sys

script, cpu, ram, fsize = sys.argv[1:5]
try:
    import resource
    for kind, limit in (
        (resource.RLIMIT_CPU, (int(cpu), int(cpu))),
        (resource.RLIMIT_FSIZE, (int(fsize), int(fsize))),
    ):
        try:
            resource.setrlimit(kind, limit)
        except (OSError, ValueError):
            pass
    if sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (int(ram), int(ram)))
        except (OSError, ValueError):
            pass
except ImportError:
    pass

sys.argv = [script]
runpy.run_path(script, run_name="__main__")
"""


# ── Cấu hình / khởi động ──────────────────────────────────────────────────

def _so(ten: str, mac_dinh: int) -> int:
    try:
        return int(os.environ.get(ten, str(mac_dinh)))
    except (ValueError, TypeError):
        return mac_dinh


def kiem_tra_khoa(khoa: str | None = None) -> str:
    """Khoá dịch vụ bắt buộc, ≥ 24 ký tự và ≥ 10 ký tự khác nhau.

    KHÔNG in giá trị khoá ra log hay thông báo lỗi.
    """
    k = (os.environ.get("CHAYMA_KHOA", "") if khoa is None else khoa).strip()
    if len(k) < 24:
        raise RuntimeError("CHAYMA_KHOA bắt buộc và phải dài ít nhất 24 ký tự.")
    if len(set(k)) < 10:
        raise RuntimeError("CHAYMA_KHOA quá đơn điệu: cần ít nhất 10 ký tự khác nhau.")
    return k


def lay_cau_hinh() -> dict:
    return {
        "host": os.environ.get("CHAYMA_HOST", "127.0.0.1"),
        "port": _so("CHAYMA_PORT", 8703),
        "thu_muc_goc": Path(os.environ.get("CHAYMA_THU_MUC_GOC", "/data")),
        "docker_bin": os.environ.get("CHAYMA_DOCKER_BIN", "docker"),
        "anh": os.environ.get("CHAYMA_ANH", ANH_MAC_DINH),
        "het_gio": max(1, _so("CHAYMA_HET_GIO_MAC_DINH", HET_GIO_MAC_DINH)),
        "cpu": os.environ.get("CHAYMA_CPU", "1"),
        "ram_mb": max(64, _so("CHAYMA_RAM_MB", 512)),
        "pids": max(16, _so("CHAYMA_PIDS", 64)),
        "dong_thoi": max(1, _so("CHAYMA_DONG_THOI", 2)),
        # C5: trần kết nối HTTP đồng thời + hết giờ socket của handler.
        "tran_ket_noi": max(1, _so("CHAYMA_KET_NOI_TOI_DA", 32)),
        "het_gio_socket": max(1, _so("CHAYMA_HET_GIO_SOCKET", 20)),
    }


def kiem_tra_docker(cau_hinh: dict) -> str:
    """Nhị phân docker phải tồn tại và thực thi được; chạy --version nhanh."""
    bin_path = cau_hinh["docker_bin"]
    duong = shutil.which(bin_path) or bin_path
    if not (Path(duong).is_file() and os.access(duong, os.X_OK)):
        raise RuntimeError(f"Không thấy nhị phân docker thực thi được ở {bin_path!r}.")
    try:
        proc = subprocess.run([duong, "--version"], capture_output=True,
                              timeout=15, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                               "HOME": "/tmp"})
        log.info("docker --version: %s", (proc.stdout or b"").decode("utf-8", "replace")[:80])
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"Không khởi động được docker: {type(e).__name__}")
    return duong


# ── Dựng lệnh docker (HÀM THUẦN — test đọc lại cờ từ đây) ──────────────────

def xay_dung_lenh(docker_bin: str, anh: str, id_chay: str, thu_muc_chay: Path,
                  han_giay: int, ram_mb: int, cpu: str, pids: int) -> list[str]:
    """Lệnh `docker run` cách ly đầy đủ cho MỘT lần chạy mã.

    Mọi ràng buộc của spec đều thể hiện ở đây (test đọc lại từng cờ):
    - dùng một lần (--rm, --name), KHÔNG mạng (--network none),
      rootfs chỉ đọc (--read-only) + tmpfs /tmp có trần,
      user không-root 65534:65534, --cap-drop ALL, no-new-privileges,
      trần CPU/RAM/pids, --pull never (không kéo ảnh lén), mount DUY NHẤT
      thư mục tạm của lần chạy → /lam-viec, env là whitelist tối thiểu.
    - Bootstrap rlimit (CPU/RAM/FSIZE) đặt SAU exec trong container.
    - NV171-SUA C2: `--log-driver none` — daemon KHÔNG ghi nhật ký container
      xuống đĩa máy chủ (client gắn trực tiếp vẫn đọc được stdout/stderr);
      và giờ TƯỜNG trong container bằng `timeout -s KILL <han_giay>` bọc
      ngoài python3 — RLIMIT_CPU của bootstrap chỉ tính giờ CPU, mã ngủ
      (sleep) không tốn CPU nên phải có đồng hồ tường riêng trong hộp.
    """
    ram_byte = ram_mb * 1024 * 1024
    han = max(1, int(han_giay))
    lenh_trong = ["timeout", "-s", "KILL", str(han),
                  "python3", "-I", "-c", BOOTSTRAP,
                  f"{DIEM_MOUNT}/{TEN_TEP_MA}",
                  str(han), str(ram_byte), str(_FILE_TRAN_BYTE)]
    lenh = [
        docker_bin, "run",
        "--rm",
        "--name", f"chayma-{id_chay}",
        "--log-driver", "none",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16777216",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--cpus", str(cpu),
        "--memory", f"{ram_mb}m",
        "--memory-swap", f"{ram_mb}m",
        "--pids-limit", str(pids),
        "--ulimit", "nofile=256:256",
        "--pull", "never",
        "-v", f"{thu_muc_chay}:{DIEM_MOUNT}:rw",
        "-w", DIEM_MOUNT,
    ]
    for khoa, gia_tri in sorted(MOI_TRUONG_CONTAINER.items()):
        lenh += ["--env", f"{khoa}={gia_tri}"]
    lenh += [anh, *lenh_trong]
    return lenh


def _diet_nhom_tien_trinh(proc: subprocess.Popen) -> None:
    """Dừng cả process group (port c2a, giữ nguyên)."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _env_client_docker() -> dict[str, str]:
    """Env tối thiểu cho tiến trình docker client — KHÔNG kế thừa env của dịch vụ.

    Bí mật của lõi (biến môi trường, khoá) không được lộ vào môi trường này.
    Chỉ cho test: CHAYMA_GIA_* được truyền qua khi có (docker_gia đọc chúng);
    nhị phân docker thật bỏ qua biến lạ, không ảnh hưởng.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for khoa in ("CHAYMA_GIA_CHE_DO", "CHAYMA_GIA_THU_MUC"):
        if khoa in os.environ:
            env[khoa] = os.environ[khoa]
    return env


def _diet_container(docker_bin: str, ten_container: str) -> None:
    """NV171-SUA C2: diệt container THEO TÊN — `docker kill` rồi `docker rm -f`.

    `--rm` chỉ tự dọn khi container TỰ thoát; khi dịch vụ giết docker client
    (hết giờ, phun đầu ra vượt trần) container vẫn chạy tiếp thành MỒ CÔI,
    giữ 512 MB RAM + 64 tiến trình tới khi ai đó dọn tay. Hàm này chạy trong
    `finally` của MỌI lượt chạy. Container đã tự thoát (đã bị --rm dọn) thì
    kill/rm báo lỗi «không có container» — bình thường, nuốt lặng."""
    for lenh_con in (["kill", ten_container], ["rm", "-f", ten_container]):
        try:
            subprocess.run([docker_bin, *lenh_con], capture_output=True,
                           timeout=10, env=_env_client_docker())
        except (OSError, subprocess.TimeoutExpired):
            pass


def thuc_thi_lenh(cmd: list[str], docker_bin: str, ten_container: str,
                  han_giay: int) -> dict:
    """Chạy lệnh docker với trần đầu ra + hết giờ.

    - Đọc stdout/stderr bằng luồng riêng: giữ lại TRAN_DAU_RA_BYTE mỗi luồng;
      tổng vượt TRAN_CUNG_BYTE thì diệt ngay (chống phun đầy bộ nhớ dịch vụ).
    - Quá han_giay: killpg tiến trình client.
    - NV171-SUA C2: `finally` LUÔN `docker kill` + `docker rm -f` container
      theo tên — mọi đường thoát (xong, lỗi, hết giờ, tràn, huỷ) đều dọn,
      không để container mồ côi.
    - Trả dict {ok, ma_thoat, het_gio, stdout, stderr, bi_cat}.
    """
    ket = {"ok": False, "ma_thoat": None, "het_gio": False,
           "stdout": "", "stderr": "", "bi_cat": False}
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=_env_client_docker(),
        )
    except OSError as e:
        ket["stderr"] = f"không khởi động được docker: {type(e).__name__}"
        return ket

    khoa = threading.Lock()
    trang_thai = {"tong": 0, "cat": False, "ra": b"", "loi": b""}

    def _doc(luong, khoa_luu: str):
        try:
            while True:
                mieng = luong.read(65536)
                if not mieng:
                    break
                with khoa:
                    trang_thai["tong"] += len(mieng)
                    hien = trang_thai[khoa_luu]
                    if len(hien) < TRAN_DAU_RA_BYTE:
                        trang_thai[khoa_luu] = hien + mieng[:TRAN_DAU_RA_BYTE - len(hien)]
                    if trang_thai["tong"] > TRAN_CUNG_BYTE and not trang_thai["cat"]:
                        trang_thai["cat"] = True
                if trang_thai["tong"] > TRAN_CUNG_BYTE:
                    _diet_nhom_tien_trinh(proc)
        except (OSError, ValueError):
            pass

    t1 = threading.Thread(target=_doc, args=(proc.stdout, "ra"), daemon=True)
    t2 = threading.Thread(target=_doc, args=(proc.stderr, "loi"), daemon=True)
    t1.start()
    t2.start()
    try:
        try:
            proc.wait(timeout=han_giay)
        except subprocess.TimeoutExpired:
            ket["het_gio"] = True
            _diet_nhom_tien_trinh(proc)
        t1.join(timeout=10)
        t2.join(timeout=10)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    finally:
        # C2: dù thoát bằng đường nào cũng diệt container theo tên.
        if proc.poll() is None:
            _diet_nhom_tien_trinh(proc)
        _diet_container(docker_bin, ten_container)
    ket["ma_thoat"] = proc.returncode
    ket["bi_cat"] = trang_thai["cat"]
    ket["stdout"] = trang_thai["ra"].decode("utf-8", "replace")
    ket["stderr"] = trang_thai["loi"].decode("utf-8", "replace")
    if ket["bi_cat"]:
        ket["stdout"] += "\n…[cat]"
        ket["stderr"] += "\n…[cat]"
    ket["ok"] = proc.returncode == 0 and not ket["het_gio"]
    if ket["het_gio"]:
        ket["stderr"] = (f"hết hạn {han_giay}s — đã diệt container dùng-một-lần\n"
                         + ket["stderr"])
    return ket


# ── Tệp đầu vào / tệp ra ──────────────────────────────────────────────────

def kiem_ten_tep(ten) -> str:
    """Tên tệp đầu vào: tên đơn thuần, đúng khuôn, không «..», không tuyệt đối."""
    if not isinstance(ten, str) or not ten:
        raise LoiYeuCau(400, "ten_tep_khong_hop_le")
    if _co_ky_tu_dieu_khien(ten) or "/" in ten or "\\" in ten or ".." in ten:
        raise LoiYeuCau(400, "ten_tep_khong_hop_le")
    if not MAU_TEN_TEP.fullmatch(ten):
        raise LoiYeuCau(400, "ten_tep_khong_hop_le")
    return ten


def _co_ky_tu_dieu_khien(s: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


def viet_tep_dau_vao(danh_sach, thu_muc_chay: Path) -> None:
    """Ghi tệp đầu vào (đã kiểm) vào thư mục lần chạy. Tên do khách khai NHƯNG
    đúng khuôn tên đơn; nội dung là dữ liệu thô (không thực thi)."""
    if not isinstance(danh_sach, list):
        raise LoiYeuCau(400, "tep_dau_vao_phai_la_danh_sach")
    if len(danh_sach) > TRAN_SO_TEP:
        raise LoiYeuCau(400, "qua_nhieu_tep_dau_vao")
    tong = 0
    da_thay = set()
    for muc in danh_sach:
        if not isinstance(muc, dict):
            raise LoiYeuCau(400, "ten_tep_khong_hop_le")
        ten = kiem_ten_tep(muc.get("ten"))
        if ten in da_thay or ten == TEN_TEP_MA:
            raise LoiYeuCau(400, "ten_tep_khong_hop_le")
        da_thay.add(ten)
        noi_dung = muc.get("noi_dung", "")
        if not isinstance(noi_dung, str):
            raise LoiYeuCau(400, "ten_tep_khong_hop_le")
        _kiem_chuoi_utf8(noi_dung, "tep_dau_vao_khong_hop_le")  # C6
        du_lieu = noi_dung.encode("utf-8")
        if len(du_lieu) > TRAN_TEP_BYTE:
            raise LoiYeuCau(400, "tep_dau_vao_qua_lon")
        tong += len(du_lieu)
        if tong > TRAN_TONG_TEP:
            raise LoiYeuCau(400, "tep_dau_vao_qua_lon")
        dich = thu_muc_chay / ten
        # resolve() phòng thủ kép: tên đã đúng khuôn nhưng vẫn kiểm lại.
        try:
            dich.resolve().relative_to(thu_muc_chay.resolve())
        except ValueError:
            raise LoiYeuCau(403, "duong_dan_ngoai_thu_muc") from None
        dich.write_bytes(du_lieu)
        try:
            os.chmod(dich, 0o644)
        except OSError:
            pass


def thu_tep_ra(thu_muc_chay: Path) -> list[dict]:
    """Thu tệp ra sau khi chạy: KHÔNG đi theo symlink; chỉ tệp thường; trần
    số lượng/kích thước. Nội dung nhị phân không giải mã được → chỉ trả
    tên + kích thước."""
    ra: list[dict] = []
    tong = 0
    try:
        cac_ten = sorted(p.name for p in thu_muc_chay.iterdir())
    except OSError:
        return ra
    for ten in cac_ten:
        if ten == TEN_TEP_MA or len(ra) >= TRAN_TEP_RA:
            continue
        dich = thu_muc_chay / ten
        try:
            if dich.is_symlink():
                log.warning("tep_ra_bo_qua_symlink: %s", ten)
                continue
            if not dich.is_file():
                continue
            try:
                dich.resolve().relative_to(thu_muc_chay.resolve())
            except ValueError:
                continue
            du_lieu = dich.read_bytes()
        except OSError:
            continue
        if len(du_lieu) > TRAN_TEP_RA_BYTE:
            log.warning("tep_ra_qua_lon_bi_bo: %s (%d byte)", ten, len(du_lieu))
            continue
        tong += len(du_lieu)
        if tong > TRAN_TONG_TEP:
            break
        muc = {"ten": ten, "co_byte": len(du_lieu)}
        try:
            muc["noi_dung"] = du_lieu.decode("utf-8")
        except UnicodeDecodeError:
            muc["nhi_phan"] = True
        ra.append(muc)
    return ra


# ── Luồng chạy một lần ────────────────────────────────────────────────────

def _kiem_chuoi_utf8(s, ma_loi: str) -> None:
    """NV171-SUA C6: chuỗi chứa surrogate đơn độc (vd "\\ud800" từ JSON) làm
    `encode("utf-8")` ném UnicodeEncodeError ở tận lúc ghi tệp — phải chặn
    NGAY ở cửa vào thành 400 sạch."""
    if isinstance(s, str):
        try:
            s.encode("utf-8")
        except UnicodeEncodeError:
            raise LoiYeuCau(400, ma_loi) from None


def _chuan_bi_thu_muc_chay(thu_muc_chay: Path) -> None:
    """NV171-SUA C4: thư mục lần chạy phải THUỘC uid 65534 — container chạy
    bằng uid đó, thư mục 0o755 của người dùng dịch vụ thì container KHÔNG
    ghi được tệp ra nào (bản cũ «tệp ra» chỉ xanh nhờ docker giả ghi bằng
    uid của dịch vụ). TUYỆT ĐỐI không chữa bằng chmod 777: bind-mount ghi
    được mà không trần dung lượng là đường đầy đĩa máy chủ. Đổi chủ cần
    dịch vụ chạy bằng root hoặc chính uid 65534 (xem README triển khai);
    không đổi được thì giữ 0o755 (tệp VÀO vẫn đọc được, tệp RA không ghi
    được) và CẢNH BÁO to trong log thay vì im lặng."""
    thu_muc_chay.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(thu_muc_chay, 65534, 65534)
        os.chmod(thu_muc_chay, 0o700)
    except OSError:
        try:
            os.chmod(thu_muc_chay, 0o755)
        except OSError:
            pass
        log.warning("chay-ma: khong doi duoc chu thu muc lan chay sang uid "
                    "65534 — container co the khong ghi duoc tep ra; hay "
                    "chay dich vu bang root hoac uid 65534 (README trien khai)")


def xu_ly_chay(yeu_cau: dict, cau_hinh: dict) -> dict:
    """Chạy một lượt mã. Trả dict kết quả đầy đủ (không bao giờ ném ra ngoài
    trừ LoiYeuCau cho lỗi khuôn 4xx)."""
    if not isinstance(yeu_cau, dict):
        raise LoiYeuCau(400, "than_khong_phai_json")
    ngon_ngu = yeu_cau.get("ngon_ngu", "python")
    if ngon_ngu != "python":
        raise LoiYeuCau(400, "ngon_ngu_khong_ho_tro")
    tho = yeu_cau.get("ma", "")
    ma = boc_code_python(tho) if "```" in str(tho or "") else str(tho or "")
    _kiem_chuoi_utf8(ma, "ma_khong_hop_le")
    # C6: int(1e999) ném OverflowError, int(NaN) ném ValueError — bắt đủ và
    # chặn số float không hữu hạn TRƯỚC khi ép kiểu, tất cả thành 400 sạch.
    han_tho = yeu_cau.get("han_giay", cau_hinh["het_gio"])
    if isinstance(han_tho, float) and not math.isfinite(han_tho):
        raise LoiYeuCau(400, "han_giay_khong_hop_le")
    try:
        han_giay = int(han_tho)
    except (TypeError, ValueError, OverflowError):
        raise LoiYeuCau(400, "han_giay_khong_hop_le") from None
    han_giay = max(1, min(HAN_GIAY_TOI_DA, han_giay))

    kiem = kiem_tinh(ma)
    duoc, ly_do = goi_y_som(ma)
    if not duoc:
        log.info("chay_bo_qua ly_do=%s", ly_do[:80])
        return {"ok": False, "da_chay": False, "ly_do_bo_qua": ly_do,
                "kiem_tinh": kiem, "ma_thoat": None, "het_gio": False,
                "stdout": "", "stderr": "", "chan_doan": "", "bi_cat": False,
                "tep_ra": []}

    id_chay = secrets.token_hex(8)
    thu_muc_chay = cau_hinh["thu_muc_goc"] / id_chay
    _chuan_bi_thu_muc_chay(thu_muc_chay)
    try:
        (thu_muc_chay / TEN_TEP_MA).write_text(ma, encoding="utf-8")
        try:
            os.chmod(thu_muc_chay / TEN_TEP_MA, 0o644)
        except OSError:
            pass
        viet_tep_dau_vao(yeu_cau.get("tep_dau_vao", []), thu_muc_chay)

        cmd = xay_dung_lenh(cau_hinh["docker_bin"], cau_hinh["anh"], id_chay,
                            thu_muc_chay, han_giay, cau_hinh["ram_mb"],
                            cau_hinh["cpu"], cau_hinh["pids"])
        ket = thuc_thi_lenh(cmd, cau_hinh["docker_bin"], f"chayma-{id_chay}", han_giay)
        log.info("chay_xong id=%s ok=%s ma_thoat=%s het_gio=%s", id_chay,
                 ket["ok"], ket["ma_thoat"], ket["het_gio"])
        tep_ra = thu_tep_ra(thu_muc_chay)
        return {"ok": ket["ok"], "da_chay": True, "ly_do_bo_qua": "",
                "kiem_tinh": kiem, "ma_thoat": ket["ma_thoat"],
                "het_gio": ket["het_gio"], "stdout": ket["stdout"],
                "stderr": ket["stderr"],
                "chan_doan": "" if ket["ok"] else chan_doan_loi(ket["stderr"], ma),
                "bi_cat": ket["bi_cat"], "tep_ra": tep_ra}
    finally:
        shutil.rmtree(thu_muc_chay, ignore_errors=True)


# ── Máy chủ HTTP ──────────────────────────────────────────────────────────

class XuLy(BaseHTTPRequestHandler):
    """Xử lý HTTP: kiểm Bearer TRƯỚC khi đọc thân (trừ /suc-khoe).

    NV171-SUA C5: `timeout` đặt hết giờ cho MỌI socket của handler —
    kết nối treo (slowloris) bị cắt sau ngần này giây thay vì giữ luồng
    vô hạn; tao_may_chu ghi đè bằng CHAYMA_HET_GIO_SOCKET."""

    timeout = 20
    khoa_dich_vu: str = ""
    cau_hinh: dict = {}
    semaphore: threading.Semaphore | None = None
    server_version = "ChayMa/1.0"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _tra_json(self, ma: int, obj: dict):
        du_lieu = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(du_lieu)))
        self.end_headers()
        self.wfile.write(du_lieu)

    def _xac_thuc(self) -> bool:
        """So sánh thời gian hằng; thiếu/sai → 401. Header không ASCII cũng
        chỉ 401 (so bằng bytes, không ném 500)."""
        nhan = self.headers.get("Authorization", "")
        if not nhan.startswith("Bearer "):
            return False
        token = nhan[len("Bearer "):]
        token_bytes = token.encode("utf-8", "surrogateescape")
        khoa_bytes = self.khoa_dich_vu.encode("utf-8")
        return hmac.compare_digest(token_bytes, khoa_bytes)

    def _doc_than(self, tran: int) -> bytes:
        try:
            dai = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise LoiYeuCau(400, "thieu_content_length")
        if dai < 0 or dai > tran:
            raise LoiYeuCau(413, "than_qua_lon")
        return self.rfile.read(dai) if dai else b""

    def do_GET(self):
        duong = urlsplit(self.path).path
        if duong == "/suc-khoe":
            self._tra_json(200, {"ok": True})
            return
        self._tra_json(404, {"loi": "khong_co_duong"})

    def do_POST(self):
        duong = urlsplit(self.path).path
        try:
            if not self._xac_thuc():
                # C5: 401 NGAY, KHÔNG đọc thân — bản cũ đọc ≤1 MB rồi mới
                # 401 (trái docstring): kẻ treo thân lớn giữ được luồng +
                # băng thông. Đóng kết nối để đầu kia không bơm tiếp.
                self.close_connection = True
                du_lieu = json.dumps({"loi": "khong_xac_thuc"}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(du_lieu)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(du_lieu)
                return
            if duong == "/chay":
                than = self._doc_than(TRAN_THAN)
                try:
                    yeu_cau = json.loads(than.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise LoiYeuCau(400, "than_khong_phai_json")
                if self.semaphore is not None and not self.semaphore.acquire(blocking=True, timeout=5):
                    self._tra_json(429, {"loi": "dich_vu_dang_ban"})
                    return
                try:
                    ket_qua = xu_ly_chay(yeu_cau, self.cau_hinh)
                finally:
                    if self.semaphore is not None:
                        self.semaphore.release()
                log.info("chay: ok=%s da_chay=%s", ket_qua["ok"], ket_qua["da_chay"])
                self._tra_json(200, ket_qua)
            elif duong == "/kiem-tinh":
                than = self._doc_than(TRAN_THAN)
                try:
                    yeu_cau = json.loads(than.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise LoiYeuCau(400, "than_khong_phai_json")
                if not isinstance(yeu_cau, dict):
                    raise LoiYeuCau(400, "than_khong_phai_json")
                tho = yeu_cau.get("ma", "")
                ma = boc_code_python(tho) if "```" in str(tho or "") else str(tho or "")
                _kiem_chuoi_utf8(ma, "ma_khong_hop_le")  # C6
                duoc, ly_do = goi_y_som(ma)
                # NV171-SUA C3: khoá đổi tên «goi_y» — gợi ý sớm, KHÔNG
                # phải bằng chứng an toàn (xem docstring goi_y_som).
                self._tra_json(200, {"ok": True, "goi_y": duoc,
                                     "ly_do": ly_do, "kiem_tinh": kiem_tinh(ma)})
            else:
                self._tra_json(404, {"loi": "khong_co_duong"})
        except LoiYeuCau as e:
            self._tra_json(e.ma, {"loi": e.loi})
        except (ConnectionError, BrokenPipeError):
            pass
        except Exception as exc:  # C6: lưới cuối — KHÔNG bao giờ cắt kết nối
            # không một lời (bản cũ: han_giay=1e999 và chuỗi surrogate làm
            # văng ngoại lệ không bắt → RemoteDisconnected). Không lộ chi
            # tiết nội bộ, chỉ ghi loại lỗi vào log của dịch vụ.
            log.warning("chay-ma: loi khong du kien o do_POST: %s",
                        type(exc).__name__)
            try:
                self._tra_json(500, {"loi": "loi_noi_bo"})
            except Exception:
                pass


class MayChuChayMa(ThreadingHTTPServer):
    """ThreadingHTTPServer CÓ TRẦN KẾT NỐI (NV171-SUA C5).

    Bản cũ: một luồng cho mỗi kết nối, không trần — 150 kết nối treo là
    150 luồng giữ vô hạn, trong khi tiến trình này cầm docker.sock. Vượt
    trần thì ĐÓNG kết nối ngay ở cửa (không sinh luồng)."""

    daemon_threads = True
    _TRAN_KET_NOI = 32

    def __init__(self, *args, **kwargs):
        self._sem_ket_noi = threading.Semaphore(self._TRAN_KET_NOI)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._sem_ket_noi.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._sem_ket_noi.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem_ket_noi.release()


def tao_may_chu() -> tuple[ThreadingHTTPServer, dict]:
    """Dựng máy chủ (chưa chạy): kiểm khoá, nhị phân docker, thư mục."""
    khoa = kiem_tra_khoa()
    cau_hinh = lay_cau_hinh()
    cau_hinh["docker_bin"] = kiem_tra_docker(cau_hinh)
    thu_muc = cau_hinh["thu_muc_goc"]
    thu_muc.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(thu_muc, 0o700)
    except OSError:
        pass
    XuLy.khoa_dich_vu = khoa
    XuLy.cau_hinh = cau_hinh
    XuLy.semaphore = threading.Semaphore(cau_hinh["dong_thoi"])
    XuLy.timeout = cau_hinh["het_gio_socket"]
    MayChuChayMa._TRAN_KET_NOI = cau_hinh["tran_ket_noi"]
    srv = MayChuChayMa((cau_hinh["host"], cau_hinh["port"]), XuLy)
    return srv, cau_hinh


def main() -> None:
    srv, cau_hinh = tao_may_chu()
    log.info("chay-ma nghe %s:%d, thư mục %s",
             cau_hinh["host"], srv.server_address[1], cau_hinh["thu_muc_goc"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
