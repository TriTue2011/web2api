"""Đo tài nguyên THEO YÊU CẦU (NV261) — chỉ đo khi người dùng bấm, không đo nền.

Nguyên văn chủ máy: «không cần liên tục, chỉ khi người dùng kích vào cái nào mới gọi lệnh đo,
chậm một tí không sao». Một lần đo chụp MỘT ảnh dùng chung cho mọi module:

- RAM module chạy trong lõi: quét theo THAM CHIẾU từ biến toàn cục của gói module + thể hiện
  đang chạy (đoạn 14b MỚI của NV255 — bản cũ duyệt cây tệp nên bỏ sót 100% dict đệm toàn cục).
  Bỏ qua module/hàm/phương thức/code/frame/lớp, tập đã-thấy, `sys.getsizeof`, cắt ở hạn 5 giây.
  Đây là ƯỚC LƯỢNG (nhãn «uoc_luong»): đối tượng dùng chung giữa lõi và module có thể bị tính
  cho module; đích kiểm chứng là sai số ≤ 20% so với tracemalloc trên 3 module giả của chủ máy.
- CPU module trong lõi: lấy mẫu ngăn xếp mọi luồng (mẫu 10 ms, cửa sổ ≥ 2 giây,
  `sys._current_frames`), gán cho module đầu tiên có `/modules/<id>/` trong ngăn xếp.
  Module async đang chờ mạng không chiếm luồng → 0% (đúng).
- Module có dịch vụ phụ (khai `dich_vu_phu` trong manifest): đọc cgroup của chính container
  (memory.current / cpu.stat). Không thấy → «chua_do», KHÔNG ước lượng. KHÔNG mở docker.sock.
- «Chưa quy cho module» = VmRSS của lõi − tổng RAM đã gán cho module trong lõi (tiến trình
  phụ nằm ngoài VmRSS nên không trừ).
- Chi phí nạp: hiệu VmRSS trước/sau một lần `cai()` thành công, lưu vào trạng thái module
  (tệp CÔNG KHAI tai-nguyen.json trong thư mục dữ liệu — không phải bí mật).

Chế độ chẩn đoán chính xác (tuỳ chọn, TẮT mặc định): WEB2API_DO_CHUAN=1 → tracemalloc bật
từ lúc khởi động (app/main.py gọi `bat_tracemalloc_neu_can()`), có chuẩn tuyệt đối nhưng
web2api chậm đi rõ (chủ máy đã biết ~2,8 lần); giao diện hiện cảnh báo khi đang bật.

Mọi hàm đo KHÔNG raise ra ngoài: một chỉ số hỏng trả None/«chua_do» kèm log, để phép đo
không làm sập trang Module.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
import sys
import threading
import time
import tracemalloc
import types
from datetime import datetime, timezone
from pathlib import Path

from app import he_thong, kho

log = logging.getLogger("web2api.do_tai_nguyen")

MB = 1024 * 1024
CUA_SO_CPU_MAC_DINH = 2.0
BUOC_LAY_MAU_GIAY = 0.01
HAN_TONG_GIAY = 5.0
GOC_CGROUP = Path("/sys/fs/cgroup")

#: Bật tracemalloc từ khởi động để có chuẩn tuyệt đối (chẩn đoán; chậm — tắt mặc định).
DO_CHUAN_BAT = os.environ.get("WEB2API_DO_CHUAN", "").strip() == "1"

_KHOA_TRANG_THAI = threading.Lock()


def bat_tracemalloc_neu_can() -> None:
    """Gọi từ app/main.py lúc khởi động. Chỉ bật khi WEB2API_DO_CHUAN=1."""
    if DO_CHUAN_BAT and not tracemalloc.is_tracing():
        tracemalloc.start()
        log.warning("WEB2API_DO_CHUAN=1: tracemalloc ĐANG BẬT từ khởi động — có chuẩn tuyệt đối "
                    "nhưng web2api chậm đi đáng kể; chỉ dùng khi chẩn đoán phép đo.")


def do_chuan_dang_bat() -> bool:
    return tracemalloc.is_tracing()


# ───────────────────────── số của chính tiến trình lõi ─────────────────────────

def doc_vmrss_mb(tep: Path = Path("/proc/self/status")) -> float | None:
    """VmRSS (RAM thật đang chiếm) của tiến trình lõi, MB."""
    try:
        for dong in tep.read_text().splitlines():
            if dong.startswith("VmRSS:"):
                return int(dong.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def _doc_cpu_tien_trinh_giay(tep: Path = Path("/proc/self/stat")) -> float | None:
    """(utime + stime) của tiến trình theo giây đồng hồ CPU."""
    try:
        noi_dung = tep.read_text()
        # comm nằm trong ngoặc đơn, có thể chứa khoảng trắng — cắt sau «)» cuối.
        phan = noi_dung[noi_dung.rfind(")") + 2:].split()
        tong_tick = int(phan[11]) + int(phan[12])  # field 14, 15 (utime, stime)
    except (OSError, ValueError, IndexError):
        return None
    return tong_tick / os.sysconf("SC_CLK_TCK")


# ───────────────────────── quét RAM theo tham chiếu ─────────────────────────

_BO_QUA = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
           types.MethodType, types.CodeType, types.FrameType, type)
_CO_SO = (str, bytes, bytearray, int, float, bool, complex)


def quet_ram(goc, han_monotonic: float) -> tuple[int, bool]:
    """Quét đồ thị tham chiếu từ `goc`, cộng `sys.getsizeof` mỗi đối tượng gặp lần đầu.

    Bỏ qua (không đếm, không duyệt vào): module, hàm, phương thức, code, frame, lớp.
    Trả (tổng byte, True nếu quét đủ / False nếu bị cắt ở hạn thời gian).
    """
    da_thay: set[int] = set()
    chong = list(goc)
    tong = 0
    dem = 0
    while chong:
        o = chong.pop()
        i = id(o)
        if i in da_thay:
            continue
        da_thay.add(i)
        if o is None or isinstance(o, _BO_QUA):
            continue
        dem += 1
        if dem % 4096 == 0 and time.monotonic() > han_monotonic:
            return tong, False
        try:
            tong += sys.getsizeof(o)
        except Exception as e:  # đối tượng lạ báo lỗi getsizeof — bỏ qua nó, không sập phép đo (có log)
            log.debug("quet_ram: getsizeof lỗi trên %r: %s", type(o), e)
        if isinstance(o, dict):
            chong.extend(o.keys())
            chong.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            chong.extend(o)
        elif isinstance(o, _CO_SO):
            continue
        else:
            try:
                chong.extend(gc.get_referents(o))
            except Exception as e:  # như trên: bỏ qua đối tượng không duyệt được, có log
                log.debug("quet_ram: get_referents lỗi trên %r: %s", type(o), e)
    return tong, True


# ───────────────────────── lấy mẫu CPU ─────────────────────────

_MAU_DUONG_MODULE = re.compile(r"/modules/([^/]+)/")


def lay_mau_cpu(cua_so_giay: float, buoc_giay: float, id_module: set[str]) -> tuple[dict[str, int], int]:
    """Lấy mẫu ngăn xếp MỌI luồng trong cửa sổ; trả ({id: số mẫu được gán}, tổng số mẫu luồng).

    Mỗi mẫu luồng gán cho module ĐẦU TIÊN (từ đỉnh ngăn xếp xuống) có tệp thuộc
    `/modules/<id>/` với <id> nằm trong `id_module`; không khớp → mẫu «lõi/khác».
    """
    dem: dict[str, int] = {}
    tong = 0
    bat_dau = time.monotonic()
    han = bat_dau + cua_so_giay
    while True:
        for frame in sys._current_frames().values():
            tong += 1
            f = frame
            while f is not None:
                m = _MAU_DUONG_MODULE.search(f.f_code.co_filename)
                if m and m.group(1) in id_module:
                    dem[m.group(1)] = dem.get(m.group(1), 0) + 1
                    break
                f = f.f_back
        if time.monotonic() >= han:
            return dem, tong
        time.sleep(buoc_giay)


# ───────────────────────── cgroup của dịch vụ phụ ─────────────────────────

def tim_thu_muc_cgroup(ten: str, goc: Path = GOC_CGROUP, sau_toi_da: int = 4) -> Path | None:
    """Tìm thư mục cgroup của container dịch vụ phụ theo TÊN khai trong manifest.

    cgroup của container con thường nằm ở <gốc>/…/docker-<tên>.scope hoặc thư mục chứa tên.
    Chỉ ĐỌC cây /sys/fs/cgroup đã gắn sẵn — không mở docker.sock, không gọi docker.
    """
    if not ten or not goc.is_dir():
        return None
    hang = [(goc, 0)]
    while hang:
        thu_muc, sau = hang.pop(0)
        if sau > 0 and ten in thu_muc.name and (thu_muc / "memory.current").is_file():
            return thu_muc
        if sau < sau_toi_da:
            try:
                con = sorted(thu_muc.iterdir())
            except OSError:
                continue
            hang.extend((c, sau + 1) for c in con if c.is_dir())
    return None


def doc_cgroup_dich_vu(thu_muc: Path) -> tuple[int | None, int | None]:
    """(memory.current byte, cpu usage_usec) của một thư mục cgroup; thiếu → None từng phần."""
    ram = None
    try:
        ram = int((thu_muc / "memory.current").read_text().strip())
    except (OSError, ValueError):
        pass
    cpu_us = None
    try:
        for dong in (thu_muc / "cpu.stat").read_text().splitlines():
            phan = dong.split()
            if len(phan) == 2 and phan[0] == "usage_usec":
                cpu_us = int(phan[1])
                break
    except (OSError, ValueError):
        pass
    return ram, cpu_us


# ───────────────────────── chi phí nạp (trạng thái module) ─────────────────────────

def _p_trang_thai() -> Path:
    return kho.thu_muc_du_lieu() / "tai-nguyen.json"


def _doc_trang_thai() -> dict:
    try:
        d = json.loads(_p_trang_thai().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def doc_chi_phi_nap() -> dict[str, float]:
    """{id module: MB tăng thêm lúc nạp gần nhất} — {} nếu chưa ghi lần nào."""
    d = _doc_trang_thai().get("chi_phi_nap")
    if not isinstance(d, dict):
        return {}
    return {k: float(v) for k, v in d.items() if isinstance(v, (int, float))}


def ghi_chi_phi_nap(id_: str, mb: float) -> None:
    """Lưu chi phí nạp của một module (trạng thái công khai, KHÔNG bí mật). Lỗi ghi chỉ log."""
    with _KHOA_TRANG_THAI:
        try:
            d = _doc_trang_thai()
            muc = d.get("chi_phi_nap") if isinstance(d.get("chi_phi_nap"), dict) else {}
            muc[id_] = round(max(0.0, mb), 1)
            d["chi_phi_nap"] = muc
            p = _p_trang_thai()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False))
        except OSError as e:
            log.warning("Không ghi được chi phí nạp của %s: %s", id_, e)


# ───────────────────────── một lần đo đầy đủ ─────────────────────────

def _goc_quet(km, the) -> list:
    goc: list = []
    ten_ma = getattr(km, "ten_ma", None)
    if ten_ma:
        for ten, mod in list(sys.modules.items()):
            if ten == ten_ma or ten.startswith(ten_ma + "."):
                goc.extend(mod.__dict__.values())
    if the is not None:
        goc.append(the)
    return goc


def do_tat_ca(so, cua_so_cpu: float = CUA_SO_CPU_MAC_DINH, han_giay: float = HAN_TONG_GIAY,
              goc_cgroup: Path = GOC_CGROUP) -> dict:
    """Chụp một ảnh tài nguyên. `so` là SoModule (chỉ đọc catalog/dang_chay/tu_xa).

    Chạy đồng bộ (bên gọi cho vào to_thread): lấy mẫu CPU ở luồng riêng trong khi quét RAM,
    tổng thời gian ≈ max(cửa sổ CPU, quét RAM) và bị cắt ở `han_giay`.
    """
    bat_dau = time.monotonic()
    han = bat_dau + han_giay
    luc = datetime.now(timezone.utc).isoformat()
    catalog = dict(getattr(so, "catalog", {}) or {})
    dang_chay = dict(getattr(so, "dang_chay", {}) or {})
    tu_xa = dict(getattr(so, "tu_xa", {}) or {})
    chi_phi = doc_chi_phi_nap()

    # Phân loại: có dịch vụ phụ → đo cgroup; còn lại → quét trong lõi.
    thu_muc_dv: dict[str, list[Path | None]] = {}
    can_quet: list[str] = []
    for mid, km in catalog.items():
        dv = (getattr(km, "manifest", None) or {}).get("dich_vu_phu") or []
        if isinstance(dv, list) and dv:
            thu_muc_dv[mid] = [tim_thu_muc_cgroup(str(t), goc_cgroup) for t in dv]
        else:
            can_quet.append(mid)

    cg_dau = {mid: [doc_cgroup_dich_vu(p) if p else (None, None) for p in ps]
              for mid, ps in thu_muc_dv.items()}
    cpu_tt_dau = _doc_cpu_tien_trinh_giay()

    mau: dict[str, int] = {}
    tong_mau = [0]
    cua_so_thuc = [cua_so_cpu]

    def _lay_mau():
        t0 = time.monotonic()
        d, t = lay_mau_cpu(cua_so_cpu, BUOC_LAY_MAU_GIAY, set(catalog))
        mau.update(d)
        tong_mau[0] = t
        cua_so_thuc[0] = max(0.01, time.monotonic() - t0)

    luong_mau = threading.Thread(target=_lay_mau, daemon=True)
    luong_mau.start()

    ram_gan: dict[str, tuple[float, bool]] = {}
    for mid in can_quet:
        byte_, day_du = quet_ram(_goc_quet(catalog[mid], dang_chay.get(mid)), han)
        ram_gan[mid] = (byte_ / MB, day_du)

    luong_mau.join(timeout=max(0.1, han - time.monotonic()) + 1.0)
    cua_so = cua_so_thuc[0]
    cpu_tt_cuoi = _doc_cpu_tien_trinh_giay()
    cpu_loi = None
    if cpu_tt_dau is not None and cpu_tt_cuoi is not None:
        cpu_loi = round(100.0 * max(0.0, cpu_tt_cuoi - cpu_tt_dau) / cua_so, 1)

    gpu = he_thong.doc_gpu()
    ket_qua: dict[str, dict] = {}
    for mid, km in catalog.items():
        manifest = getattr(km, "manifest", None) or {}
        if mid in thu_muc_dv:
            ram_tong = 0.0
            cpu_tong = 0.0
            doc_duoc = False
            for p, (ram_d, cpu_d) in zip(thu_muc_dv[mid], cg_dau[mid]):
                if p is None:
                    continue
                ram_c, cpu_c = doc_cgroup_dich_vu(p)
                if ram_c is None:
                    continue
                doc_duoc = True
                ram_tong += ram_c / MB
                if cpu_d is not None and cpu_c is not None:
                    cpu_tong += 100.0 * max(0.0, cpu_c - cpu_d) / 1e6 / cua_so
            if doc_duoc:
                muc = {"ram_mb": round(ram_tong, 1), "cpu_pct": round(cpu_tong, 1),
                       "nguon": "do", "rieng": True, "goc": "cgroup"}
            else:
                muc = {"ram_mb": None, "cpu_pct": None,
                       "nguon": "chua_do", "rieng": True, "goc": "cgroup"}
        else:
            ram_mb, day_du = ram_gan.get(mid, (0.0, True))
            cpu_pct = None
            if cpu_loi is not None:
                cpu_pct = round(cpu_loi * (mau.get(mid, 0) / tong_mau[0]), 1) if tong_mau[0] else 0.0
            muc = {"ram_mb": round(ram_mb, 1), "cpu_pct": cpu_pct,
                   "nguon": "uoc_luong", "rieng": False, "goc": "quet_tham_chieu"}
            if not day_du:
                muc["day_du"] = False
        if manifest.get("tai_nguyen", {}).get("gpu") is True and gpu is None:
            muc["gpu"] = "cần GPU, máy này không có"
        if mid in chi_phi:
            muc["nap_mb"] = chi_phi[mid]
        ket_qua[mid] = muc
    for mid in tu_xa:
        if mid not in ket_qua:
            ket_qua[mid] = {"ram_mb": None, "cpu_pct": None,
                            "nguon": "chua_do", "rieng": True, "goc": "cgroup"}

    vmrss = doc_vmrss_mb()
    tong_gan_loi = sum(v[0] for v in ram_gan.values())
    ram_may_byte = he_thong.ram_may().get("tong")
    tong = {
        "ram_may": round(ram_may_byte / MB, 1) if ram_may_byte else None,
        "ram_loi_mb": round(vmrss, 1) if vmrss is not None else None,
        "cpu_loi_pct": cpu_loi,
        "gpu": gpu,
        "chua_quy_cho_module_mb": (round(max(0.0, vmrss - tong_gan_loi), 1)
                                   if vmrss is not None else None),
    }
    return {
        "luc": luc,
        "cua_so_cpu_giay": round(cua_so, 2),
        "module": ket_qua,
        "tong": tong,
        "do_chuan": {"tracemalloc": do_chuan_dang_bat()},
    }
