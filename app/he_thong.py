"""Đọc tài nguyên THẬT của máy để web so với nhu cầu của module trước khi cài.

Vì sao báo cả RAM máy lẫn giới hạn container: trong container, /proc/meminfo vẫn là
số của MÁY CHỦ (Docker không che nó), còn trần thật mà tiến trình chạm tới là
memory.max của cgroup (đặt bằng `docker run --memory`). Chỉ báo một con số thì sẽ
khuyến cáo sai: máy còn 8 GB trống nhưng container chỉ được 256 MB.

RAM/CPU của MÁY không đọc được đáng tin từ trong container bị giới hạn (đo 10/10/2026 trên máy chủ
LXC/Proxmox: lxcfs tính /proc/meminfo theo cgroup của tiến trình ĐỌC, nên container `--memory 256m`
thấy "máy" chỉ ~0,2 GB; gắn -v /proc/meminfo:/proc/meminfo:ro cũng không cứu được). Nên người vận
hành được KHAI số của máy: WEB2API_MAY_RAM_MB, WEB2API_MAY_CPU — có thì dùng (`nguon: "khai_bao"`,
RAM trống = null vì không biết), không có thì đọc /proc như cũ (`nguon: "doc_tu_proc"`). Số của
CONTAINER (cgroup memory.max/cpu.max) luôn đúng nên không cần khai.

Mọi hàm ở đây KHÔNG raise: thiếu file/thiếu công cụ trả None (kèm log nếu là lỗi
thật), để một chỉ số hỏng không làm sập cả trang «Máy này».
Chỉ đọc cgroup v2 (máy chủ Debian 12 dùng v2); cgroup v1 coi như không có giới hạn.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("web2api.he_thong")

CGROUP = Path("/sys/fs/cgroup")
MEMINFO = Path("/proc/meminfo")


def _doc(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def doc_ram_may(meminfo: Path = MEMINFO) -> dict:
    """RAM của máy chủ, byte. «trong» = MemAvailable (cái thật sự cấp được, gồm cả cache nhả ra được)."""
    noi_dung = _doc(meminfo)
    if noi_dung is None:
        return {"tong": None, "trong": None}
    so = {}
    for dong in noi_dung.splitlines():
        ten, _, gia_tri = dong.partition(":")
        phan = gia_tri.split()
        if phan and phan[0].isdigit():
            so[ten] = int(phan[0]) * 1024  # meminfo ghi theo kB
    return {"tong": so.get("MemTotal"), "trong": so.get("MemAvailable")}


def doc_ram_container(cgroup: Path = CGROUP) -> dict:
    """Giới hạn RAM của container (memory.max, "max" = không đặt) và lượng đang dùng, byte."""
    tran = _doc(cgroup / "memory.max")
    dung = _doc(cgroup / "memory.current")
    return {
        "gioi_han": int(tran) if tran and tran.isdigit() else None,
        "dang_dung": int(dung) if dung and dung.isdigit() else None,
    }


def doc_cpu_container(cgroup: Path = CGROUP) -> float | None:
    """Số nhân container được dùng theo cpu.max ("quota period"); "max" = không giới hạn."""
    noi_dung = _doc(cgroup / "cpu.max")
    if not noi_dung:
        return None
    phan = noi_dung.split()
    if len(phan) != 2 or not phan[0].isdigit() or not phan[1].isdigit() or int(phan[1]) == 0:
        return None
    return round(int(phan[0]) / int(phan[1]), 2)


def doc_dia(thu_muc: str) -> dict:
    """Dung lượng của ổ chứa thư mục dữ liệu — nơi model/module sẽ được tải về."""
    try:
        d = shutil.disk_usage(thu_muc)
    except OSError as e:
        return {"thu_muc": thu_muc, "tong": None, "trong": None, "loi": f"Không đọc được {thu_muc}: {e.strerror or e}"}
    return {"thu_muc": thu_muc, "tong": d.total, "trong": d.free, "loi": None}


def doc_gpu() -> list[dict] | None:
    """GPU NVIDIA qua nvidia-smi; không có công cụ hoặc lỗi → None (máy không GPU là bình thường)."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,  # nvidia-smi có thể treo khi driver hỏng; không để nó giữ request
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("nvidia-smi không chạy được, coi như không có GPU: %s", e)
        return None
    if r.returncode != 0:
        log.warning("nvidia-smi trả mã %s, coi như không có GPU: %s", r.returncode, r.stderr.strip())
        return None
    ds = []
    for dong in r.stdout.splitlines():
        phan = [x.strip() for x in dong.split(",")]
        if len(phan) != 3:
            continue
        try:
            ds.append({"ten": phan[0], "vram_tong_mb": int(phan[1]), "vram_dung_mb": int(phan[2])})
        except ValueError:
            log.warning("Dòng nvidia-smi không đọc được: %r", dong)
    return ds or None


def _so_khai_bao(ten: str) -> int | None:
    """Số nguyên dương từ biến môi trường do người vận hành đặt; sai dạng → None + cảnh báo (không im lặng)."""
    gia_tri = os.environ.get(ten, "").strip()
    if not gia_tri:
        return None
    if not gia_tri.isdigit() or int(gia_tri) <= 0:
        log.warning("%s=%r không phải số nguyên dương — bỏ qua, đọc từ /proc", ten, gia_tri)
        return None
    return int(gia_tri)


def mui_gio() -> str | None:
    """Múi giờ của nhà do người vận hành khai bằng biến chuẩn `TZ` (tên IANA, vd Asia/Ho_Chi_Minh) — giao diện dùng
    nó để báo «giờ hiện tại» cho model, không phụ thuộc trình duyệt đang mở ở đâu. Hợp lệ là khi zoneinfo nạp được
    (glibc cho phép tiền tố «:»); rỗng → None; sai (dạng POSIX «ICT-7», thư mục, tên lạ) → None + một cảnh báo không
    chép lại giá trị. Chỉ gọi lúc dựng sổ module (một lần mỗi lần khởi động), không gọi theo từng yêu cầu."""
    gia_tri = os.environ.get("TZ", "").strip().removeprefix(":")
    if not gia_tri:
        return None
    try:
        return ZoneInfo(gia_tri).key
    except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError là KeyError; thư mục/không đọc được là OSError
        log.warning("Biến TZ không phải tên múi giờ IANA (vd Asia/Ho_Chi_Minh) — bỏ qua, Studio dùng múi giờ trình duyệt")
        return None


def ram_may() -> dict:
    mb = _so_khai_bao("WEB2API_MAY_RAM_MB")
    if mb is not None:
        return {"tong": mb * 1024 * 1024, "trong": None, "nguon": "khai_bao"}
    return {**doc_ram_may(), "nguon": "doc_tu_proc"}


def cpu_may() -> dict:
    so = _so_khai_bao("WEB2API_MAY_CPU")
    if so is not None:
        return {"so_nhan_may": so, "nguon": "khai_bao"}
    return {"so_nhan_may": os.cpu_count(), "nguon": "doc_tu_proc"}


def tong_hop(thu_muc_du_lieu: str, phien_ban: str) -> dict:
    return {
        "phien_ban": phien_ban,
        "ram": {"may": ram_may(), "container": doc_ram_container()},
        "cpu": {**cpu_may(), "gioi_han_container": doc_cpu_container()},
        "dia": doc_dia(thu_muc_du_lieu),
        "gpu": doc_gpu(),
    }
